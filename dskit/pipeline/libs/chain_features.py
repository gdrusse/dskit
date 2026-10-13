"""The tier-2 option-chain day features: implied-vol, skew, term, liquidity and positioning per (symbol, date, target DTE) (ADR-0265).

Vendor greeks and ``implied_vol`` are recorded in a daily chain stream but not trusted (a few percent
are placeholders). :class:`ChainDayFeatures` rebuilds what a test needs from the NBBO alone: the
forward from put-call parity at the target expiry (``OptionPriceCDF.parity_forwards``, the one
forward step the option-price CDF already owns), implied vols from the quote MIDS by
:func:`~dskit.pipeline.option_pricing.black76_implied_vol` on that forward, and the features below.
The rate comes from ``rate`` (a constant, default 0.0, the undiscounted parity), ``rate_field`` (a per-row
annual rate) or a ``rates`` input table joined as of the last date strictly BEFORE the day; the forward is
``K + e^(rT) * (call - put)`` and the Black-76 inversion discounts at the same rate. A zero rate against a
real 4.5% rate biases a 30/90 term slope by about a quarter of a vol point, so a test should supply one.

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
* ``put_wing_iv`` / ``call_wing_iv``: the implied vol at forward delta of magnitude ``wing_delta`` (the DISCOUNTED
  forward delta, ``e^(-rT) N(d1)``, as ``black76_delta`` returns it, so at a positive rate it is smaller than the
  undiscounted ``N(d1)`` a vendor quotes; puts
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

**Memory.** The input is read once and rows are held as packed arrays (about 50 bytes a row, one block per
symbol-date). Three modes: the default holds every row until the input ends (a sample only);
``symbol_contiguous`` true (members arrive grouped by symbol, any date order within one) holds ONE symbol and
flushes it when the symbol changes, processing its dates in true date order so the open-interest change is
right, and refuses a symbol that reappears after its flush; ``presorted`` true (grouped by symbol, dates
ascending) holds one symbol-date. Pair it with a unit-deduplicating reader
(:class:`~dskit.pipeline.libs.observations.ObservationUnitRows`) for a store too large for a per-row key.
Budget: about 61 bytes a row held, so a root of 30,000 rows a day over 2,500 dates (an index weekly) needs
about 4.6 GB; ``max_symbol_rows`` makes the node refuse, naming the root and its row count, instead of
exhausting memory.

**Rows and statuses.** Every requested target of every symbol-date emits a row; ``status_field`` says what
it holds: ``ok``, ``no_expiry`` (no listed expiry inside the tolerance), ``no_forward`` (too few parity pairs),
``no_rate`` (a rates source is declared and has no value for the day). A non-``ok`` row has every
expiry-dependent and quote-dependent feature ``None``. The ``summary`` counts each status per target, the
quote exclusions, the vol solves that returned ``None`` (``iv_unsolved``), the open-interest changes left
``None`` for a lag past ``oi_lag_max_days`` (``oi_lag_too_old``), and the reader's own counters when the input
answers ``report()``.

**Defaults are chosen, not derived**: ``days_per_year`` (365), the vol bracket ``iv_min`` / ``iv_max`` and the
numbers a document supplies (band, delta, lags) are conventions; each is a param. The node refuses a row
dated at or after ``end_before`` once it has parsed it (the registered stores end 2025-12-31, so none exist);
the guard is a refusal, not a promise that no sealed row was read.

Import cost: stdlib and the pipeline core; pandas is imported inside the forward step.
"""

import math
from array import array
from bisect import bisect_left
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
    "NoFeatureRows",
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


class NoFeatureRows(ValueError):
    """The input held no root with a bid, or no target resolved to a status ``ok`` row.

    A ``ValueError`` (so every caller that refused before still does), but a distinct class so a driver that
    runs the node root by root can tell "this root has nothing to emit" from a malformed row. ``counts`` is the
    run's summary dictionary at the point of refusal.
    """

    def __init__(self, message, counts):
        super().__init__(message)
        self.counts = counts


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

    def __init__(self, years, iv_min, iv_max, rate=0.0):
        self.years, self.iv_min, self.iv_max, self.rate = years, iv_min, iv_max, rate
        self.quotes = {side: {} for side in _SIDES}       # side -> strike -> (bid, ask)
        self.forward = None
        self.iv_solved = self.iv_unsolved = 0
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
        growth = math.exp(self.rate * self.years)
        forwards = OptionPriceCDF.parity_forwards(frame, growth) if len(frame) else None
        if forwards is None or len(forwards) < min_pairs:
            return None
        self.forward = float(median(forwards.tolist()))
        return self.forward

    def iv(self, side, strike):
        """Return the Black-76 implied vol of the mid on the parity forward, or ``None``."""
        key = (side, strike)
        if key not in self._iv:
            self._iv[key] = black76_implied_vol(side, self.mid(side, strike), self.forward, strike,
                                                self.years, self.rate, vol_min=self.iv_min, vol_max=self.iv_max)
            if self._iv[key] is None:
                self.iv_unsolved += 1
            else:
                self.iv_solved += 1
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
            here = abs(black76_delta(side, self.forward, strike, vol, self.years, self.rate))
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


_NAN = float("nan")


class _DayCells:
    """The rows of one (symbol, date), packed in arrays (about 50 bytes a row instead of ~450 for tuples)."""

    __slots__ = ("symbol", "day", "_expirations", "_codes", "_exp", "_side", "_num")

    def __init__(self, symbol, day):
        self.symbol, self.day = symbol, day
        self._expirations, self._codes = [], {}
        self._exp, self._side = array("i"), array("b")
        self._num = tuple(array("d") for _ in range(7))   # strike, bid, ask, oi, volume, underlying, rate

    def __len__(self):
        return len(self._exp)

    def add(self, cell):
        """Append one cell tuple ``(symbol, day, expiration, strike, side, bid, ask, oi, volume, underlying, rate)``."""
        expiration = cell[2]
        code = self._codes.get(expiration)
        if code is None:
            code = self._codes[expiration] = len(self._expirations)
            self._expirations.append(expiration)
        self._exp.append(code)
        self._side.append(0 if cell[4] == "call" else 1)
        for column, value in zip(self._num, (cell[3], *cell[5:])):
            column.append(_NAN if value is None else value)

    def rates(self):
        """Return the day's non-missing per-row rates."""
        return [v for v in self._num[6] if v == v]

    def __iter__(self):
        for i in range(len(self._exp)):
            values = [None if v != v else v for v in (column[i] for column in self._num)]
            yield (self.symbol, self.day, self._expirations[self._exp[i]], values[0], _SIDES[self._side[i]],
                   *values[1:])


class _Rates:
    """The annual rate for a day: a constant, a per-row field, or a dated table read as of strictly before."""

    def __init__(self, constant, table, max_age):
        self.constant, self.max_age = constant, max_age
        self._dates = sorted(table) if table is not None else None
        self._table = table

    def at(self, day, cells):
        """Return the rate for ``day`` (ISO text) given its cells, or ``None`` when the source has no value."""
        if self._table is not None:
            index = bisect_left(self._dates, day) - 1
            if index < 0:
                return None
            then = self._dates[index]
            if (date.fromisoformat(day) - date.fromisoformat(then)).days > self.max_age:
                return None
            return self._table[then]
        return self.constant if self.constant is not None else self._row_rate(cells)

    @staticmethod
    def _row_rate(cells):
        """Median of the day's per-row rates, or ``None`` when no row has one."""
        rates = cells.rates()
        return median(rates) if rates else None


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
        output name of the requested DTE). ``status_field`` (REQUIRED, the output name of the row status).
        ``max_symbol_rows`` (int >= 1, default none) refuses, naming the symbol, when more rows than that are
        held at once (see Memory). ``allow_unit_mismatch`` (bool, default false): a reader whose ``report()``
        counts ``unit_row_mismatch`` > 0 refuses the run unless this is true. ``atm_band`` (> 0, log-moneyness), ``wing_delta`` (in
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
    _RATES = ("rate_date_field", "rate_value_field", "rate_max_age_days")
    _PARAMS = (*_ROWS, *_OPTIONAL_ROWS, "call_values", "put_values", "end_before", "targets", "target_field",
               "status_field", "atm_band", "wing_delta", "min_forward_pairs", "fields", "term_pair", "oi_scope",
               "oi_lag_sessions", "oi_lag_max_days", "days_per_year", "iv_min", "iv_max", "presorted",
               "symbol_contiguous", "rate", "rate_field", "rate_scale", "max_symbol_rows", "allow_unit_mismatch", *_RATES)

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
        for name in (*cls._ROWS, "target_field", "status_field",
                     *(n for n in (*cls._OPTIONAL_ROWS, "rate_field") if n in params)):
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
        if "max_symbol_rows" in params:
            check_int_param(problems, "max_symbol_rows", params["max_symbol_rows"], ge=1)
        for name in ("presorted", "symbol_contiguous", "allow_unit_mismatch"):
            if name in params and not isinstance(params[name], bool):
                problems.append(f"{name} must be a boolean, got {params[name]!r}")
        if params.get("presorted") is True and params.get("symbol_contiguous") is True:
            problems.append("symbol_contiguous and presorted are alternatives: presorted already implies "
                            "symbol-contiguous, date-ascending rows")
        cls._rate_problems(problems, params)
        cls._fields_problems(problems, params)
        return problems

    @classmethod
    def _rate_problems(cls, problems, params):
        """Check the rate source declarations: one constant OR a row field OR a table, a positive scale."""
        if "rate" in params and not number_ok(params["rate"]):
            problems.append(f"rate must be a finite number, got {params['rate']!r}")
        if "rate" in params and "rate_field" in params:
            problems.append("rate and rate_field are alternatives: a constant rate or a per-row rate, not both")
        scale = params.get("rate_scale", 1.0)
        if not number_ok(scale) or scale <= 0:
            problems.append(f"rate_scale must be a number > 0, got {scale!r}")
        given = [n for n in cls._RATES if n in params]
        if given and len(given) < len(cls._RATES):
            problems.append(f"declare all of the rates-table params {list(cls._RATES)} or none; got {given}")
        if "rate_max_age_days" in params:
            check_int_param(problems, "rate_max_age_days", params["rate_max_age_days"], ge=0)
        for name in ("rate_date_field", "rate_value_field"):
            if name in params and (not isinstance(params[name], str) or not params[name]):
                problems.append(f"{name} must be a non-empty string, got {params[name]!r}")

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
        taken = [params.get(n) for n in ("symbol_field", "date_field", "target_field", "status_field")]
        if len(set(names)) < len(names) or set(names) & set(taken):
            problems.append("fields values must be distinct output names, none of the symbol, date, target "
                            "or status fields")
        if len(set(taken)) < len(taken):
            problems.append("symbol_field, date_field, target_field and status_field must be distinct")
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
                             "- the node refuses rows on or after end_before (the registered stores end "
                             "2025-12-31, so none are expected)")
        strike = number("strike", get("strike_field"), missing_ok=False)
        if strike <= 0:
            raise ValueError(f"{where}: strike must be > 0, got {strike!r}")
        right = get("right_field")
        side = "call" if right in p["call_values"] else "put" if right in p["put_values"] else None
        if side is None:
            raise ValueError(f"{where}: right {right!r} is in neither call_values nor put_values")
        optional = {name: number(name, get(f"{name}_field")) if f"{name}_field" in p else None
                    for name in ("open_interest", "volume", "underlying_price", "rate")}
        if optional["rate"] is not None:
            optional["rate"] *= p.get("rate_scale", 1.0)
        return (symbol, day, expiration, strike, side, number("bid", get("bid_field")),
                number("ask", get("ask_field")), optional["open_interest"], optional["volume"],
                optional["underlying_price"], optional["rate"])

    @staticmethod
    def _iso(value, where, memo):
        """Return the ISO text of a date cell, memoized per spelling."""
        text = memo.get(value)
        if text is None:
            text = memo[value] = iso_day(value, where).isoformat()
        return text

    def _groups(self, rows):
        """Yield ``(symbol, day, cells)`` ascending by symbol then day.

        Three read modes (see the module docstring): everything held, one symbol held and flushed when the
        symbol changes (``symbol_contiguous``), or one symbol-date held (``presorted``).
        """
        p = self.params
        flush_symbols, streaming = p.get("symbol_contiguous", False), p.get("presorted", False)
        memo, held, closed, seen, held_rows = {}, {}, set(), 0, 0
        limit = p.get("max_symbol_rows")
        current_symbol = current_day = None
        for index, record in enumerate(rows):
            seen += 1
            cell = self._cell(record, index, memo)
            symbol, day = cell[0], cell[1]
            if flush_symbols or streaming:
                if symbol != current_symbol:
                    yield from self._flush(held)
                    held_rows = 0
                    if current_symbol is not None:
                        closed.add(current_symbol)
                    if symbol in closed:
                        raise ValueError(f"{self.key}: symbol {symbol!r} reappeared after it was flushed - "
                                         "symbol_contiguous / presorted need every symbol's rows together")
                    current_symbol, current_day = symbol, None
                if streaming:
                    if current_day is not None and day < current_day:
                        raise ValueError(f"{self.key}: presorted input out of order at ({symbol!r}, {day!r})")
                    if current_day is not None and day != current_day:
                        yield from self._flush(held)
                        held_rows = 0
                    current_day = day
            held.setdefault((symbol, day), _DayCells(symbol, day)).add(cell)
            held_rows += 1
            if limit is not None and held_rows > limit:
                raise ValueError(f"{self.key}: {symbol!r} holds more than max_symbol_rows={limit} rows "
                                 f"({held_rows} so far) - refusing before memory runs out; raise the limit, "
                                 "or run the roots in smaller batches")
        if not seen:
            raise ValueError(f"{self.key}: the records input is empty - features of nothing are refused, not passed")
        yield from self._flush(held)

    @staticmethod
    def _flush(held):
        """Yield the held groups in (symbol, day) order and empty ``held``."""
        for key in sorted(held):
            yield (*key, held[key])
        held.clear()

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
            _, _, expiration, strike, side, bid, ask, oi, volume, underlying, _rate = cell
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

    def _book(self, cells, years, rate):
        """Build the usable-quote book of one expiration's cells."""
        p = self.params
        book = _Book(years, p.get("iv_min", DEFAULT_IV_MIN), p.get("iv_max", DEFAULT_IV_MAX), rate)
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

    def _positioning(self, feats, key, totals, symbol, day, counts):
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
            counts["oi_lag_too_old"] += 1
            return
        then = then_totals.get(key)
        if then is None:
            return
        before, after = then.oi_sum(), now.oi_sum()
        if before and after is not None:
            feats["oi_change"] = after / before - 1
        if then.ratio() is not None and now.ratio() is not None:
            feats["oi_put_call_ratio_change"] = now.ratio() - then.ratio()

    def _day(self, symbol_name, day, cells, state, counts, rates):
        """Return the feature rows of one symbol-date, updating the run state and counts."""
        p = self.params
        by_exp, totals, spot = self._scan_day(cells, counts)
        rate = rates.at(day, cells)
        built = []
        for target in p["targets"]:
            counted = counts["targets"][str(target["dte"])]
            feats = dict.fromkeys(FEATURES)
            feats["status"] = "no_expiry"
            picked = self._pick(by_exp, day, target)
            if picked is not None:
                expiration, dte = picked
                years = dte / p.get("days_per_year", DEFAULT_DAYS_PER_YEAR)
                feats.update(expiration=expiration, dte=dte, years=years, spot=spot)
                feats["status"] = self._quote_features(by_exp[expiration], years, rate, spot, feats, counts)
                self._positioning(feats, expiration if p.get("oi_scope") == "expiry" else _CHAIN, totals, state,
                                  day, counts)
                if feats["status"] != "ok":
                    feats = dict.fromkeys(FEATURES, None) | {"status": feats["status"]}
            counted["emitted" if feats["status"] == "ok" else feats["status"]] += 1
            built.append((target["dte"], feats))
        self._term(built)
        state.history.append((date.fromisoformat(day), totals))
        return [{p["symbol_field"]: symbol_name, p["date_field"]: day, p["target_field"]: dte,
                 p["status_field"]: feats["status"],
                 **{out: feats[name] for name, out in p["fields"].items()}} for dte, feats in built]

    def _quote_features(self, cells, years, rate, spot, feats, counts):
        """Write the quote-based features of one expiry onto ``feats``; return the row's status."""
        if rate is None:
            return "no_rate"
        book = self._book(cells, years, rate)
        if book.solve_forward(self.params["min_forward_pairs"]) is None:
            return "no_forward"
        self._smile(book, spot, feats)
        counts["iv_solved"] += book.iv_solved
        counts["iv_unsolved"] += book.iv_unsolved
        return "ok"

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
            contract; presorted input is out of order.
        NoFeatureRows
            (a ``ValueError``) No symbol has a bid above zero, or no target resolves to any ``ok`` row.
        """
        p = self.params
        rates = self._rates_source(inputs.get("rates"))
        counts = {"rows": 0, "groups": 0, "groups_before_first_bid": 0, "rows_before_first_bid": 0,
                  "usable_quotes": 0, "excluded_quotes": dict.fromkeys(_EXCLUDED, 0), "iv_solved": 0,
                  "iv_unsolved": 0, "oi_lag_too_old": 0,
                  "targets": {str(t["dte"]): dict.fromkeys(("emitted", "no_expiry", "no_forward", "no_rate"), 0)
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
            out.extend(self._day(symbol, day, cells, state, counts, rates))
        counts["symbols"] = len(symbols)
        counts["end_before"] = p["end_before"]
        if not any(s.started for s in symbols.values()):
            raise NoFeatureRows(f"{self.key}: no root has a bid above zero in the input - nothing to compute "
                                "features on", counts)
        if not any(t["emitted"] for t in counts["targets"].values()):
            raise NoFeatureRows(f"{self.key}: no feature row with status ok: every target was no_expiry, "
                                f"no_forward or no_rate (targets {p['targets']!r})", counts)
        report = getattr(inputs["records"], "report", None)
        if callable(report):
            counts["reader"] = report()
            if counts["reader"].get("unit_row_mismatch", 0) and not p.get("allow_unit_mismatch", False):
                raise ValueError(f"{self.key}: the reader found {counts['reader']['unit_row_mismatch']} unit(s) whose "
                                 "older copy has a different row count than the kept one (a split or partial "
                                 "unit): unit_row_mismatch > 0 refuses unless allow_unit_mismatch is true")
        self.log.info("features: %d row(s) from %d group(s), %d symbol(s)", len(out), counts["groups"], len(symbols))
        return {"records": out, "summary": counts}

    def _rates_source(self, table):
        """Build the day-rate resolver from the declared constant / row field / ``rates`` input."""
        p = self.params
        if table is None:
            if "rate_date_field" in p:
                raise ValueError(f"{self.key}: the rates-table params are declared but no rates input is wired")
            constant = None if "rate_field" in p else p.get("rate", 0.0) * p.get("rate_scale", 1.0)
            return _Rates(constant, None, 0)
        if "rate_date_field" not in p:
            raise ValueError(f"{self.key}: a rates input is wired but the rate_date_field / rate_value_field / "
                             "rate_max_age_days params are not declared")
        if "rate_field" in p or "rate" in p:
            raise ValueError(f"{self.key}: a rates input, rate and rate_field are alternatives - declare one source")
        parsed, memo = {}, {}
        for index, record in enumerate(table):
            where = f"{self.key}: rates row {index}"
            try:
                text, value = record[p["rate_date_field"]], record[p["rate_value_field"]]
            except (KeyError, TypeError) as err:
                raise ValueError(f"{where}: missing field {err}") from None
            day = self._iso(text, f"{where} date", memo)
            if day >= p["end_before"]:
                raise ValueError(f"{where}: date {day} is at or after end_before {p['end_before']} - filter the "
                                 "rates table upstream")
            if day in parsed:
                raise ValueError(f"{where}: duplicate rate for {day}")
            number = self._rate_number(value, where)
            if number is not None:
                parsed[day] = number * p.get("rate_scale", 1.0)
        return _Rates(None, parsed, p["rate_max_age_days"])

    @staticmethod
    def _rate_number(value, where):
        """Return a rate cell as a float; ``None`` for a missing marker (null, NaN, ``"."`` or empty text)."""
        if value is None or (isinstance(value, float) and math.isnan(value)) or value in (".", ""):
            return None
        try:
            number = float(value)
        except (TypeError, ValueError):
            raise ValueError(f"{where}: rate {value!r} is not a number") from None
        if not number_ok(number):
            raise ValueError(f"{where}: rate {value!r} is not finite")
        return number
