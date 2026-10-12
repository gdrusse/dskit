"""The tier-2 option-chain day features: implied-vol, skew, term, liquidity and positioning per (symbol, date, target DTE) (ADR-0265).

Vendor greeks and ``implied_vol`` are recorded in a daily chain stream but not trusted (a few percent
are placeholders). :class:`ChainDayFeatures` rebuilds what a test needs from the NBBO alone: the
forward from put-call parity at the target expiry (``OptionPriceCDF.parity_forwards``, the one
forward step the option-price CDF already owns), implied vols from the quote MIDS by
:func:`~dskit.pipeline.option_pricing.black76_implied_vol` on that forward, and the features below.
The rate is zero throughout, so the forward is the undiscounted parity forward and the Black-76
inversion is consistent with it.

**Quotes.** A quote is usable when :func:`~dskit.pipeline.libs.chain_census.quote_state` says
``locked`` or ``quoted`` (a bid above zero, an ask not below it). Null, zero and crossed quotes are
excluded from every quote-based feature and counted in the ``summary`` output. Rows dated before a
symbol's first day with a bid above zero are excluded whole and counted. Open interest and volume are
not quotes: they are summed over every contract of the scope whatever its quote.

**Features** (all optional, chosen and named by the ``fields`` param; ``None`` where undefined, never
filled):

* ``expiration``, ``dte`` (calendar days), ``years``, ``spot`` (the median positive underlying price of
  the day), ``forward``.
* ``atm_strike`` / ``atm_iv`` / ``straddle_ratio`` / ``half_spread_over_premium``: the strike nearest the
  forward in log terms among those with both sides usable inside ``atm_band``, ties to the lower strike.
  ``atm_iv`` is the mean of the call and put implied vols there; ``straddle_ratio`` is the call mid plus
  the put mid over ``spot``; ``half_spread_over_premium`` is the two half-spreads over the two mids.
* ``put_wing_iv`` / ``call_wing_iv``: the implied vol at forward delta of magnitude ``wing_delta`` (puts
  below the forward, calls above), linear in delta between the two bracketing strikes; ``None`` without
  a bracket (no extrapolation). ``risk_reversal`` is put wing minus call wing: positive when downside
  protection costs more.
* ``put_call_iv_spread``: the mean over strikes inside ``atm_band`` with both sides solved of the put IV
  minus the call IV at the SAME strike (a parity-implied measure). ADR-0265 lists "25-delta put-call IV
  spread" and "risk-reversal skew" separately; this reading gives each its own number.
* ``term_slope``: ``atm_iv`` at the long ``term_pair`` target minus the short one, written on every row
  of that symbol-date.
* ``oi_call``, ``oi_put``, ``volume_call``, ``volume_put``, ``oi_put_call_ratio`` over the target's
  expiry (``oi_scope`` ``"expiry"``) or the whole chain (``"chain"``); ``oi_change`` and
  ``oi_put_call_ratio_change`` against the same scope ``oi_lag_sessions`` sessions of that symbol
  earlier (``None`` past ``oi_lag_max_days`` calendar days, or when that expiry did not exist then).

**Memory.** The input is read once. Unsorted, the node holds a compact tuple per row until it has them
all; ``presorted`` true (rows grouped by symbol, dates ascending within a symbol, refused otherwise)
holds one symbol-date at a time. Wiring is by import path; :data:`NODE_KINDS` is empty.

Import cost: stdlib and the pipeline core; pandas is imported inside the forward step.
"""

import math
from collections import deque
from datetime import date
from statistics import median

from dskit.pipeline.libs.chain_census import STATES, USABLE_STATES, quote_state
from dskit.pipeline.libs.observation_tables import iso_day
from dskit.pipeline.node import check_int_param, reject_unknown_params
from dskit.pipeline.node import Node
from dskit.pipeline.option_pricing import black76_delta, black76_implied_vol
from dskit.pipeline.records import number_ok

__all__ = [
    "DEFAULT_DAYS_PER_YEAR",
    "DEFAULT_IV_MAX",
    "DEFAULT_IV_MIN",
    "FEATURES",
    "NODE_KINDS",
    "OI_SCOPES",
    "ChainDayFeatures",
]

#: Every feature a document may name in ``fields``.
FEATURES = (
    "expiration", "dte", "years", "spot", "forward", "atm_strike", "atm_iv", "put_wing_iv",
    "call_wing_iv", "risk_reversal", "put_call_iv_spread", "term_slope", "straddle_ratio",
    "half_spread_over_premium", "oi_call", "oi_put", "volume_call", "volume_put",
    "oi_put_call_ratio", "oi_change", "oi_put_call_ratio_change",
)

#: The open-interest / volume scopes: the target's expiry, or every expiry of the day.
OI_SCOPES = ("expiry", "chain")

#: Days in a year when a document does not say; the vol bracket the inversion searches when it does not say.
DEFAULT_DAYS_PER_YEAR = 365.0
DEFAULT_IV_MIN = 0.01
DEFAULT_IV_MAX = 5.0

#: No node kind is registered: a document names ``dskit.pipeline.libs.chain_features:ChainDayFeatures``.
NODE_KINDS = ()

_CHAIN = None          # the totals key of the whole chain, beside the per-expiration keys
_AGGREGATES = ("oi_call", "oi_put", "volume_call", "volume_put", "oi_put_call_ratio")
_CHANGES = ("oi_change", "oi_put_call_ratio_change")
_EXCLUDED = tuple(s for s in STATES if s not in USABLE_STATES)
_SIDES = ("call", "put")


class _Totals:
    """Open interest and volume summed by side over one scope; a side with no number is ``None``."""

    __slots__ = ("cells",)

    def __init__(self):
        self.cells = {(kind, side): [0.0, 0] for kind in ("oi", "volume") for side in _SIDES}

    def add(self, side, oi, volume):
        """Add one contract's open interest and volume; a missing number adds nothing."""
        for kind, value in (("oi", oi), ("volume", volume)):
            if value is not None:
                cell = self.cells[(kind, side)]
                cell[0] += value
                cell[1] += 1

    def total(self, kind, side):
        """Return the sum for ``kind`` and ``side``, or ``None`` when no contract carried a number."""
        total, count = self.cells[(kind, side)]
        return total if count else None

    def oi_sum(self):
        """Return call plus put open interest over the sides that have it, or ``None`` with neither."""
        parts = [v for v in (self.total("oi", s) for s in _SIDES) if v is not None]
        return sum(parts) if parts else None

    def ratio(self):
        """Return put open interest over call open interest, or ``None``."""
        call, put = self.total("oi", "call"), self.total("oi", "put")
        return None if not call or put is None else put / call


class _Book:
    """The usable quotes of one expiration on one day, and the vols solved from their mids."""

    def __init__(self, years, iv_min, iv_max):
        self.years, self.iv_min, self.iv_max = years, iv_min, iv_max
        self.quotes = {side: {} for side in _SIDES}       # side -> strike -> (bid, ask)
        self.forward = None
        self._iv = {}

    def add(self, side, strike, bid, ask):
        """Record one usable quote."""
        self.quotes[side][strike] = (bid, ask)

    def mid(self, side, strike):
        """Return the quote's mid."""
        bid, ask = self.quotes[side][strike]
        return (bid + ask) / 2

    def solve_forward(self, min_pairs):
        """Set the parity forward from the usable quotes' mids; return it, or ``None`` below ``min_pairs`` pairs."""
        import pandas as pd

        from dskit.pipeline.libs.predictive_cdf import OptionPriceCDF

        frame = pd.DataFrame([(k, side, self.mid(side, k)) for side in _SIDES for k in self.quotes[side]],
                             columns=["strike", "type", "mark"])
        forwards = OptionPriceCDF.parity_forwards(frame) if len(frame) else None
        if forwards is None or len(forwards) < min_pairs:
            return None
        self.forward = float(median(forwards.tolist()))
        return self.forward

    def iv(self, side, strike):
        """Return the Black-76 implied vol of the mid on the parity forward, or ``None``."""
        key = (side, strike)
        if key not in self._iv:
            self._iv[key] = black76_implied_vol(side, self.mid(side, strike), self.forward, strike,
                                                self.years, vol_min=self.iv_min, vol_max=self.iv_max)
        return self._iv[key]

    def both(self, band):
        """Return the strikes quoted on both sides within ``band`` of the forward, nearest first (ties lower)."""
        near = [k for k in self.quotes["call"] if k in self.quotes["put"]
                and abs(math.log(k / self.forward)) <= band]
        return sorted(near, key=lambda k: (abs(math.log(k / self.forward)), k))

    def wing(self, side, delta):
        """Return the IV at forward delta magnitude ``delta`` on the out-of-the-money ``side``, or ``None``."""
        out = [k for k in self.quotes[side] if (k < self.forward if side == "put" else k > self.forward)]
        previous = None
        for strike in sorted(out, reverse=side == "put"):
            vol = self.iv(side, strike)
            if vol is None:
                continue
            here = abs(black76_delta(side, self.forward, strike, vol, self.years))
            if here <= delta:
                if previous is None:
                    return None
                near, near_vol = previous
                return near_vol + (vol - near_vol) * (near - delta) / (near - here)
            previous = (here, vol)
        return None

    def spread(self, band):
        """Return the mean put IV minus call IV over strikes within ``band`` with both solved, or ``None``."""
        gaps = []
        for strike in self.both(band):
            call, put = self.iv("call", strike), self.iv("put", strike)
            if call is not None and put is not None:
                gaps.append(put - call)
        return sum(gaps) / len(gaps) if gaps else None


class _Symbol:
    """Per-symbol run state: whether a bid has appeared, and the last few sessions' totals."""

    __slots__ = ("started", "history")

    def __init__(self, lag):
        self.started = False
        self.history = deque(maxlen=lag)


class ChainDayFeatures(Node):
    """Reduce a daily option-chain stream to per-(symbol, date, target DTE) features (ADR-0265).

    Role ``transform``; input ``records`` (any iterable of contract rows, read once). Outputs
    ``records`` (one row per symbol, date and target the data reaches: the symbol, date and
    ``target_field`` plus the features ``fields`` names, sorted by symbol, date, target order) and
    ``summary`` (row, group and exclusion counts, per-target ``emitted`` / ``no_expiry`` /
    ``no_forward``). See the module docstring for the features.

    Parameters
    ----------
    params : dict
        Row vocabulary, REQUIRED: ``symbol_field``, ``date_field``, ``expiration_field``,
        ``strike_field``, ``right_field``, ``bid_field``, ``ask_field``; ``call_values`` and
        ``put_values`` (disjoint non-empty lists of the right field's spellings); optional
        ``open_interest_field``, ``volume_field``, ``underlying_price_field``. ``end_before`` (REQUIRED
        ISO date): a row at or after it refuses. ``targets`` (REQUIRED non-empty list of
        ``{"dte": int >= 1, "tolerance": int >= 0}``, distinct ``dte``) and ``target_field`` (the
        output name of the requested DTE). ``atm_band`` (> 0, log-moneyness), ``wing_delta`` (in
        (0, 0.5)), ``min_forward_pairs`` (int >= 1), ``fields`` (non-empty map of feature -> output
        name): REQUIRED. Needed by what ``fields`` names: ``term_pair`` (``[short_dte, long_dte]``,
        both targets), ``oi_scope`` (``"expiry"`` | ``"chain"``), ``oi_lag_sessions`` and
        ``oi_lag_max_days`` (ints >= 1). Optional: ``days_per_year`` (default
        :data:`DEFAULT_DAYS_PER_YEAR`), ``iv_min`` / ``iv_max`` (the solver's vol bracket, defaults
        :data:`DEFAULT_IV_MIN` / :data:`DEFAULT_IV_MAX`), ``presorted`` (bool, default false).

    Examples
    --------
    The 30-day ATM vol and skew of a vendor chain::

        node = ChainDayFeatures("features", {
            "symbol_field": "symbol", "date_field": "date", "expiration_field": "expiration",
            "strike_field": "strike", "right_field": "right", "bid_field": "bid", "ask_field": "ask",
            "call_values": ["call"], "put_values": ["put"], "end_before": "2026-01-01",
            "targets": [{"dte": 30, "tolerance": 5}], "target_field": "target_dte",
            "atm_band": 0.05, "wing_delta": 0.25, "min_forward_pairs": 3,
            "fields": {"atm_iv": "iv30", "risk_reversal": "rr30"}})
        rows = node.run(ctx, {"records": chain_rows})["records"]
        # -> rows[0] == {"symbol": "AAPL", "date": "2020-03-02", "target_dte": 30, "iv30": 0.31, "rr30": 0.04}
    """

    role = "transform"
    outputs = ("records", "summary")

    _ROWS = ("symbol_field", "date_field", "expiration_field", "strike_field", "right_field",
             "bid_field", "ask_field")
    _OPTIONAL_ROWS = ("open_interest_field", "volume_field", "underlying_price_field")
    _PARAMS = (*_ROWS, *_OPTIONAL_ROWS, "call_values", "put_values", "end_before", "targets", "target_field",
               "atm_band", "wing_delta", "min_forward_pairs", "fields", "term_pair", "oi_scope",
               "oi_lag_sessions", "oi_lag_max_days", "days_per_year", "iv_min", "iv_max", "presorted")

    # -- validation ------------------------------------------------------------------------

    @classmethod
    def validate_params(cls, params):
        """Problems with the declared knobs, empty when none.

        Parameters
        ----------
        params : dict
            The node's ``params`` block.

        Returns
        -------
        list of str
            One message per problem.
        """
        problems = []
        reject_unknown_params(problems, params, cls._PARAMS)
        for name in (*cls._ROWS, "target_field", *(n for n in cls._OPTIONAL_ROWS if n in params)):
            if not isinstance(params.get(name), str) or not params.get(name):
                problems.append(f"{name} must be a non-empty string, got {params.get(name)!r}")
        cls._rights_problems(problems, params)
        cls._date_problem(problems, params.get("end_before"))
        cls._targets_problems(problems, params)
        for name, ceiling in (("atm_band", None), ("wing_delta", 0.5)):
            value = params.get(name)
            if not number_ok(value) or value <= 0 or (ceiling is not None and value >= ceiling):
                bound = "a number > 0" if ceiling is None else f"a number in (0, {ceiling})"
                problems.append(f"{name} must be {bound}, got {value!r}")
        check_int_param(problems, "min_forward_pairs", params.get("min_forward_pairs"), ge=1)
        cls._solver_problems(problems, params)
        if "presorted" in params and not isinstance(params["presorted"], bool):
            problems.append(f"presorted must be a boolean, got {params['presorted']!r}")
        cls._fields_problems(problems, params)
        return problems

    @staticmethod
    def _date_problem(problems, value):
        """Append a problem unless ``end_before`` is an ISO ``YYYY-MM-DD`` date text."""
        try:
            ok = isinstance(value, str) and len(value) == 10 and iso_day(value, "end_before").isoformat() == value
        except ValueError:
            ok = False
        if not ok:
            problems.append(f"end_before must be an ISO date text YYYY-MM-DD, got {value!r}")

    @staticmethod
    def _rights_problems(problems, params):
        """Check the two right spellings are non-empty, disjoint lists of strings."""
        found = {}
        for name in ("call_values", "put_values"):
            values = params.get(name)
            if not isinstance(values, list) or not values or not all(isinstance(v, str) and v for v in values):
                problems.append(f"{name} must be a non-empty list of strings, got {values!r}")
            else:
                found[name] = set(values)
        if len(found) == 2 and found["call_values"] & found["put_values"]:
            problems.append("call_values and put_values must be disjoint, both hold "
                            f"{sorted(found['call_values'] & found['put_values'])}")

    @staticmethod
    def _targets_problems(problems, params):
        """Check ``targets`` is a non-empty list of distinct ``{dte, tolerance}`` objects."""
        targets = params.get("targets")
        if not isinstance(targets, list) or not targets:
            problems.append(f"targets must be a non-empty list of {{dte, tolerance}} objects, got {targets!r}")
            return
        seen = []
        for i, target in enumerate(targets):
            if not isinstance(target, dict) or set(target) != {"dte", "tolerance"}:
                problems.append(f"targets[{i}] must be an object with exactly dte and tolerance, got {target!r}")
                continue
            check_int_param(problems, f"targets[{i}].dte", target["dte"], ge=1)
            check_int_param(problems, f"targets[{i}].tolerance", target["tolerance"], ge=0)
            seen.append(target["dte"])
        if len(set(map(repr, seen))) < len(seen):
            problems.append(f"targets: every dte must be distinct, got {seen}")

    @staticmethod
    def _solver_problems(problems, params):
        """Check the day count and the vol bracket."""
        days = params.get("days_per_year", DEFAULT_DAYS_PER_YEAR)
        if not number_ok(days) or days <= 0:
            problems.append(f"days_per_year must be a number > 0, got {days!r}")
        low, high = params.get("iv_min", DEFAULT_IV_MIN), params.get("iv_max", DEFAULT_IV_MAX)
        if not number_ok(low) or low <= 0:
            problems.append(f"iv_min must be a number > 0, got {low!r}")
        elif not number_ok(high) or high <= low:
            problems.append(f"iv_max must be a number above iv_min, got {high!r}")

    @classmethod
    def _fields_problems(cls, problems, params):
        """Check ``fields`` and every param the named features need."""
        fields = params.get("fields")
        if not isinstance(fields, dict) or not fields:
            problems.append("fields is required: a non-empty map of feature -> output field")
            return
        bad = sorted(set(fields) - set(FEATURES))
        if bad:
            problems.append(f"fields: unknown feature(s) {bad} - allowed: {list(FEATURES)}")
        names = list(fields.values())
        if not all(isinstance(v, str) and v for v in names):
            problems.append("fields values must be non-empty strings")
            return
        taken = [params.get(n) for n in ("symbol_field", "date_field", "target_field")]
        if len(set(names)) < len(names) or set(names) & set(taken):
            problems.append("fields values must be distinct output names, none of the symbol, date or target fields")
        wanted = set(fields) & set(FEATURES)
        needs = (
            (wanted & {"spot", "straddle_ratio"}, "underlying_price_field", "underlying_price_field" in params),
            (wanted & {"term_slope"}, "term_pair", "term_pair" in params),
            (wanted & {"oi_call", "oi_put", "oi_put_call_ratio", *_CHANGES}, "open_interest_field",
             "open_interest_field" in params),
            (wanted & {"volume_call", "volume_put"}, "volume_field", "volume_field" in params),
            (wanted & {*_AGGREGATES, *_CHANGES}, "oi_scope", "oi_scope" in params),
            (wanted & set(_CHANGES), "oi_lag_sessions", "oi_lag_sessions" in params),
            (wanted & set(_CHANGES), "oi_lag_max_days", "oi_lag_max_days" in params),
        )
        for features, param, present in needs:
            if features and not present:
                problems.append(f"fields {sorted(features)} need the {param} param")
        cls._declared_problems(problems, params)

    @staticmethod
    def _declared_problems(problems, params):
        """Check the shape of the optional feature params that are present."""
        if "oi_scope" in params and params["oi_scope"] not in OI_SCOPES:
            problems.append(f"oi_scope must be one of {list(OI_SCOPES)}, got {params['oi_scope']!r}")
        for name in ("oi_lag_sessions", "oi_lag_max_days"):
            if name in params:
                check_int_param(problems, name, params[name], ge=1)
        if "term_pair" in params:
            pair = params["term_pair"]
            dtes = [t.get("dte") for t in params.get("targets", []) if isinstance(t, dict)]
            if (not isinstance(pair, list) or len(pair) != 2 or pair[0] not in dtes or pair[1] not in dtes
                    or not pair[0] < pair[1]):
                problems.append(f"term_pair must be [short_dte, long_dte], both in targets and ascending, got {pair!r}")

    # -- reading ---------------------------------------------------------------------------

    def _cell(self, record, index, memo):
        """Return ``(symbol, day, expiration, strike, side, bid, ask, oi, volume, underlying)`` of one row."""
        p, where = self.params, f"{self.key}: row {index}"

        def get(name):
            field = p[name]
            if not isinstance(record, dict) or field not in record:
                raise ValueError(f"{where}: missing field {field!r} ({name})")
            return record[field]

        def number(name, value, missing_ok=True):
            if value is None or (isinstance(value, float) and math.isnan(value)):
                if missing_ok:
                    return None
                raise ValueError(f"{where}: {name} must be a number, got {value!r}")
            if not number_ok(value):
                raise ValueError(f"{where}: {name} must be a finite number or null, got {value!r}")
            return float(value)

        symbol = get("symbol_field")
        if not isinstance(symbol, str) or not symbol:
            raise ValueError(f"{where}: symbol must be a non-empty string, got {symbol!r}")
        day, expiration = (self._iso(get(name), f"{where} {label}", memo)
                           for name, label in (("date_field", "date"), ("expiration_field", "expiration")))
        if day >= p["end_before"]:
            raise ValueError(f"{where}: date {day} is at or after end_before {p['end_before']} "
                             "- the sealed period is never read")
        strike = number("strike", get("strike_field"), missing_ok=False)
        if strike <= 0:
            raise ValueError(f"{where}: strike must be > 0, got {strike!r}")
        right = get("right_field")
        side = "call" if right in p["call_values"] else "put" if right in p["put_values"] else None
        if side is None:
            raise ValueError(f"{where}: right {right!r} is in neither call_values nor put_values")
        optional = {name: number(name, get(f"{name}_field")) if f"{name}_field" in p else None
                    for name in ("open_interest", "volume", "underlying_price")}
        return (symbol, day, expiration, strike, side, number("bid", get("bid_field")),
                number("ask", get("ask_field")), optional["open_interest"], optional["volume"],
                optional["underlying_price"])

    @staticmethod
    def _iso(value, where, memo):
        """Return the ISO text of a date cell, memoized per spelling."""
        text = memo.get(value)
        if text is None:
            text = memo[value] = iso_day(value, where).isoformat()
        return text

    def _groups(self, rows):
        """Yield ``(symbol, day, cells)`` ascending by symbol then day: lazily if presorted, else after all rows."""
        memo, held, current, group = {}, {}, None, []
        presorted = self.params.get("presorted", False)
        closed = set()
        seen = 0
        for index, record in enumerate(rows):
            seen += 1
            cell = self._cell(record, index, memo)
            key = (cell[0], cell[1])
            if not presorted:
                held.setdefault(key, []).append(cell)
                continue
            if key != current:
                self._check_order(closed, current, key)
                if group:
                    yield (*current, group)
                    if current[0] != key[0]:
                        closed.add(current[0])
                current, group = key, []
            group.append(cell)
        if not seen:
            raise ValueError(f"{self.key}: the records input is empty - features of nothing are refused, not passed")
        if presorted:
            yield (*current, group)
        else:
            for key in sorted(held):
                yield (*key, held[key])

    def _check_order(self, closed, current, key):
        """Refuse a symbol that returns after another, or a date that goes backwards within a symbol."""
        if key[0] in closed or (current is not None and key[0] == current[0] and key[1] < current[1]):
            raise ValueError(f"{self.key}: presorted input out of order at group {key!r}")

    # -- one symbol-date -------------------------------------------------------------------

    def _pick(self, expirations, day, target):
        """Return the expiration nearest ``target['dte']`` within its tolerance (ties to the earlier), or ``None``."""
        today = date.fromisoformat(day)
        best = None
        for expiration in expirations:
            dte = (date.fromisoformat(expiration) - today).days
            gap = abs(dte - target["dte"])
            if dte >= 1 and gap <= target["tolerance"] and (best is None or (gap, dte) < best[0]):
                best = ((gap, dte), expiration, dte)
        return None if best is None else best[1:]

    def _scan_day(self, cells, counts):
        """Group a day's cells by expiration; count quote states; return ``(by_expiration, totals, spot)``."""
        by_exp, seen, totals, prices = {}, set(), {_CHAIN: _Totals()}, []
        for cell in cells:
            _, _, expiration, strike, side, bid, ask, oi, volume, underlying = cell
            key = (expiration, strike, side)
            if key in seen:
                raise ValueError(f"{self.key}: duplicate contract {key!r} on {cell[1]} for {cell[0]!r}")
            seen.add(key)
            by_exp.setdefault(expiration, []).append(cell)
            state = quote_state(bid, ask)
            if state in _EXCLUDED:
                counts["excluded_quotes"][state] += 1
            else:
                counts["usable_quotes"] += 1
            totals.setdefault(expiration, _Totals()).add(side, oi, volume)
            totals[_CHAIN].add(side, oi, volume)
            if underlying is not None and underlying > 0:
                prices.append(underlying)
        return by_exp, totals, (median(prices) if prices else None)

    def _book(self, cells, years):
        """Build the usable-quote book of one expiration's cells."""
        p = self.params
        book = _Book(years, p.get("iv_min", DEFAULT_IV_MIN), p.get("iv_max", DEFAULT_IV_MAX))
        for cell in cells:
            if quote_state(cell[5], cell[6]) in USABLE_STATES:
                book.add(cell[4], cell[3], cell[5], cell[6])
        return book

    def _smile(self, book, spot, feats):
        """Write the forward-dependent features of ``book`` onto ``feats``."""
        p = self.params
        feats["forward"] = book.forward
        near = book.both(p["atm_band"])
        if near:
            strike = near[0]
            feats["atm_strike"] = strike
            ivs = [v for v in (book.iv("call", strike), book.iv("put", strike)) if v is not None]
            feats["atm_iv"] = sum(ivs) / len(ivs) if ivs else None
            premium = book.mid("call", strike) + book.mid("put", strike)
            feats["straddle_ratio"] = premium / spot if spot else None
            halves = sum((a - b) / 2 for b, a in (book.quotes["call"][strike], book.quotes["put"][strike]))
            feats["half_spread_over_premium"] = halves / premium
        feats["put_wing_iv"] = book.wing("put", p["wing_delta"])
        feats["call_wing_iv"] = book.wing("call", p["wing_delta"])
        if feats["put_wing_iv"] is not None and feats["call_wing_iv"] is not None:
            feats["risk_reversal"] = feats["put_wing_iv"] - feats["call_wing_iv"]
        feats["put_call_iv_spread"] = book.spread(p["atm_band"])

    def _positioning(self, feats, key, totals, symbol, day):
        """Write the open-interest and volume features of scope ``key`` onto ``feats``."""
        now = totals.get(key)
        if now is None:
            return
        for kind, side in (("oi", "call"), ("oi", "put"), ("volume", "call"), ("volume", "put")):
            feats[f"{kind}_{side}"] = now.total(kind, side)
        feats["oi_put_call_ratio"] = now.ratio()
        history = symbol.history
        lag = self.params.get("oi_lag_sessions")
        if not lag or len(history) < lag:
            return
        then_day, then_totals = history[0]
        if (date.fromisoformat(day) - then_day).days > self.params["oi_lag_max_days"]:
            return
        then = then_totals.get(key)
        if then is None:
            return
        before, after = then.oi_sum(), now.oi_sum()
        if before and after is not None:
            feats["oi_change"] = after / before - 1
        if then.ratio() is not None and now.ratio() is not None:
            feats["oi_put_call_ratio_change"] = now.ratio() - then.ratio()

    def _day(self, symbol_name, day, cells, state, counts):
        """Return the feature rows of one symbol-date, updating the run state and counts."""
        p = self.params
        by_exp, totals, spot = self._scan_day(cells, counts)
        built = []
        for target in p["targets"]:
            counted = counts["targets"][str(target["dte"])]
            picked = self._pick(by_exp, day, target)
            if picked is None:
                counted["no_expiry"] += 1
                continue
            expiration, dte = picked
            years = dte / p.get("days_per_year", DEFAULT_DAYS_PER_YEAR)
            feats = dict.fromkeys(FEATURES)
            feats.update(expiration=expiration, dte=dte, years=years, spot=spot)
            book = self._book(by_exp[expiration], years)
            if book.solve_forward(p["min_forward_pairs"]) is None:
                counted["no_forward"] += 1
            else:
                self._smile(book, spot, feats)
            self._positioning(feats, expiration if p.get("oi_scope") == "expiry" else _CHAIN, totals, state, day)
            counted["emitted"] += 1
            built.append((target["dte"], feats))
        self._term(built)
        state.history.append((date.fromisoformat(day), totals))
        return [{p["symbol_field"]: symbol_name, p["date_field"]: day, p["target_field"]: dte,
                 **{out: feats[name] for name, out in p["fields"].items()}} for dte, feats in built]

    def _term(self, built):
        """Write ``term_slope`` onto every built row of the day when both term-pair targets resolved an ATM vol."""
        pair = self.params.get("term_pair")
        if not pair:
            return
        ivs = {dte: feats["atm_iv"] for dte, feats in built}
        short, long_ = ivs.get(pair[0]), ivs.get(pair[1])
        slope = None if short is None or long_ is None else long_ - short
        for _, feats in built:
            feats["term_slope"] = slope

    # -- the node --------------------------------------------------------------------------

    def run(self, ctx, inputs):
        """Compute the features for every symbol-date the stream reaches.

        Parameters
        ----------
        ctx : NodeContext
            Unused; the node is a function of its inputs and params.
        inputs : dict
            ``records``: iterable of contract rows, read once.

        Returns
        -------
        dict
            ``records`` and ``summary`` (see the class).

        Raises
        ------
        ValueError
            The input is empty, a row is malformed, dated at or after ``end_before``, or repeats a
            contract; presorted input is out of order; no symbol has a bid above zero; or no target
            resolves to any row.
        """
        p = self.params
        counts = {"rows": 0, "groups": 0, "groups_before_first_bid": 0, "rows_before_first_bid": 0,
                  "usable_quotes": 0, "excluded_quotes": dict.fromkeys(_EXCLUDED, 0),
                  "targets": {str(t["dte"]): {"emitted": 0, "no_expiry": 0, "no_forward": 0}
                              for t in p["targets"]}}
        symbols, out = {}, []
        for symbol, day, cells in self._groups(inputs["records"]):
            counts["groups"] += 1
            counts["rows"] += len(cells)
            state = symbols.setdefault(symbol, _Symbol(p.get("oi_lag_sessions", 1)))
            state.started = state.started or any(c[5] is not None and c[5] > 0 for c in cells)
            if not state.started:
                counts["groups_before_first_bid"] += 1
                counts["rows_before_first_bid"] += len(cells)
                continue
            out.extend(self._day(symbol, day, cells, state, counts))
        if not any(s.started for s in symbols.values()):
            raise ValueError(f"{self.key}: no root has a bid above zero in the input - nothing to compute features on")
        if not out:
            raise ValueError(f"{self.key}: no feature row: no target resolved to an expiry on any date "
                             f"(targets {p['targets']!r})")
        counts["symbols"] = len(symbols)
        counts["end_before"] = p["end_before"]
        self.log.info("features: %d row(s) from %d group(s), %d symbol(s)", len(out), counts["groups"], len(symbols))
        return {"records": out, "summary": counts}
