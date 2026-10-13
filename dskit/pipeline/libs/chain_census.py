"""The tier-2 option-chain census: per-root coverage and quote quality of a daily chain stream (ADR-0265).

A daily option-chain vendor stream (one row per contract and date, with a closing bid/ask, an
open interest, a vendor implied-vol error code and the underlying price it was reported with)
is only usable after someone has counted what is in it: when each root first has a real bid,
how much of it is null, zero, crossed or locked, how many contracts a day carry a usable quote,
whether the strikes still sit on the listed grid (an adjusted strike is a corporate-action
marker) and whether the underlying price the vendor reports agrees with an independent close.
:class:`ChainCensus` is that count, one pass over the rows, and it freezes the universe a later
test may use. It tests nothing and fits nothing.

**Process caveat.** ``first_bid_date`` and every share here are WHOLE-SAMPLE statistics. A universe gate built
from them admits a root using information from after a test window's start (look-ahead inside a validation
window). A gate for a test must be recomputed from dates up to that test's start only, or be a property that
cannot change (a listing date proved independently).

**Memory.** O(roots x dates): one small tally per (root, date), plus one strike set per (root, month) when a
strike grid is declared. Pair it with a unit-deduplicating reader
(:class:`~dskit.pipeline.libs.observations.ObservationUnitRows`) for a stream with billions of rows; the reader's
own counters (``summary.reader``) are copied in when the input answers ``report()``. The defaults and conventions
here (the usable-quote definition, three terciles, the strike tolerance) are chosen, not derived; the ones that
are knobs are params.

**One pass, nothing held but tallies.** The input is any iterable of row mappings, read exactly
once (a lazy :class:`~dskit.pipeline.libs.observations.ObservationStreamRows` output is the
intended source). The node keeps one small tally per (root, date), never the rows.

**Quote states** (:func:`quote_state`, the ONE classification, also used by the option-chain
features): ``null`` (a missing bid or ask), ``zero`` (a bid or ask at or below zero), ``crossed``
(ask below bid), ``locked`` (ask equal to bid) and ``quoted`` (ask above bid, both positive).
``locked`` and ``quoted`` together are the *usable* quotes (:data:`USABLE_STATES`): a bid above
zero and an ask not below it.

**Loud refusals.** An empty input, a row at or after ``end_before`` (the sealed period), a row
missing a declared field, a bad date, a non-numeric price, a duplicate close row and a wired
``closes`` input without its field params all raise by name. A check whose params or input are
absent is skipped and listed, with its reason, in the ``summary`` output - never silently
passed. Wiring is by import path; :data:`NODE_KINDS` is empty.

Import cost: stdlib and the pipeline core.
"""

import math
from statistics import median

from dskit.pipeline.libs.chain_reuse import ReuseDetector
from dskit.pipeline.libs.observation_tables import iso_day
from dskit.pipeline.node import Node, check_int_param, reject_unknown_params
from dskit.pipeline.option_structures import quote_defects
from dskit.pipeline.records import number_ok

__all__ = [
    "CROSSED",
    "LOCKED",
    "NODE_KINDS",
    "NULL",
    "QUOTED",
    "STATES",
    "USABLE_STATES",
    "ZERO",
    "ChainCensus",
    "StrikeGrid",
    "quote_state",
]

#: The five exclusive quote states, in the order the tallies hold them.
NULL, ZERO, CROSSED, LOCKED, QUOTED = "null", "zero", "crossed", "locked", "quoted"
STATES = (NULL, ZERO, CROSSED, LOCKED, QUOTED)
#: The states that carry a price: a bid above zero and an ask not below it.
USABLE_STATES = (LOCKED, QUOTED)

#: Absolute slack, in strike units, when asking whether a strike is a multiple of its grid step.
DEFAULT_STRIKE_TOLERANCE = 1e-6
#: The reuse detector's chosen defaults (conventions, not derived): see :class:`~dskit.pipeline.libs.chain_reuse.ReuseDetector`.
DEFAULT_REUSE_WINDOW, DEFAULT_REUSE_BAND, DEFAULT_REUSE_OVERLAP, DEFAULT_REUSE_SPLIT = 5, 0.25, 0.1, 0.05

#: No node kind is registered: a document names ``dskit.pipeline.libs.chain_census:ChainCensus``.
NODE_KINDS = ()

_INDEX = {state: i for i, state in enumerate(STATES)}
_USABLE_INDEXES = tuple(_INDEX[s] for s in USABLE_STATES)


def _side(name, value):
    """Return ``value`` as a float, ``None`` for a missing / NaN cell; refuse anything else."""
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    if not number_ok(value):
        raise ValueError(f"{name} must be a finite number or null, got {value!r}")
    return float(value)


def quote_state(bid, ask):
    """Classify one bid/ask pair into exactly one of :data:`STATES`.

    The crossed / locked / missing tests are :func:`~dskit.pipeline.option_structures.quote_defects`'
    (the one owner of the quote rules); the ``zero`` class is this census's own. Precedence: a missing side is ``null``; otherwise a side at or below zero is ``zero``;
    otherwise ask below bid is ``crossed``, ask equal to bid ``locked``, else ``quoted``.

    Parameters
    ----------
    bid, ask : float or None
        The quote; ``None`` and NaN are missing, any other non-number refuses.

    Returns
    -------
    str
        One of :data:`STATES`.

    Raises
    ------
    ValueError
        When a side is a string, a bool or an infinity.

    Examples
    --------
    A wing with no bid is ``zero``, an unusable quote::

        quote_state(0.0, 0.05)   # "zero"
        quote_state(1.0, 1.2)    # "quoted"
    """
    low, high = _side("bid", bid), _side("ask", ask)
    defects = quote_defects(low, high)
    if "not_finite" in defects:
        return NULL
    if low <= 0 or high <= 0:
        return ZERO
    if "crossed" in defects:
        return CROSSED
    return LOCKED if "locked" in defects else QUOTED


class StrikeGrid:
    """The listed strike grid as ascending price tiers, each with its step.

    A strike is *standard* when it is a whole multiple of its tier's step, within an absolute
    ``tolerance``. A corporate action leaves adjusted strikes (a third of a dollar, say) that fail
    that test, which is why their count by month is a marker.

    Parameters
    ----------
    tiers : list of dict
        ``{"below": level, "step": step}`` for every tier but the last, with strictly increasing
        ``below``; the last tier is ``{"step": step}`` and covers every strike at or above the
        previous ``below``. A strike belongs to the first tier whose ``below`` exceeds it.
    tolerance : float
        Absolute slack in strike units (default :data:`DEFAULT_STRIKE_TOLERANCE`).

    Examples
    --------
    Half-dollar strikes below 200, whole dollars above::

        grid = StrikeGrid([{"below": 200.0, "step": 0.5}, {"step": 1.0}])
        grid.is_standard(150.5)   # True
        grid.is_standard(200.5)   # False
    """

    def __init__(self, tiers, tolerance=DEFAULT_STRIKE_TOLERANCE):
        problems = self.problems(tiers)
        if problems:
            raise ValueError("; ".join(problems))
        self._tiers = tuple((t.get("below", math.inf), float(t["step"])) for t in tiers)
        self.tolerance = float(tolerance)

    @staticmethod
    def problems(tiers):
        """List what is wrong with a tier declaration, empty when nothing is."""
        if not isinstance(tiers, (list, tuple)) or not tiers:
            return [f"strike_grid must be a non-empty list of tiers, got {tiers!r}"]
        found, last = [], -math.inf
        for i, tier in enumerate(tiers):
            if not isinstance(tier, dict) or set(tier) - {"below", "step"} or "step" not in tier:
                found.append(f"strike_grid tier {i} must be an object with step (and below), got {tier!r}")
                continue
            step = tier["step"]
            if not number_ok(step) or step <= 0:
                found.append(f"strike_grid tier {i}: step must be a number > 0, got {step!r}")
            closed = i < len(tiers) - 1
            if closed != ("below" in tier):
                found.append("strike_grid: every tier but the last needs below, and the last must not have it")
            elif closed:
                below = tier["below"]
                if not number_ok(below) or below <= last:
                    found.append(f"strike_grid tier {i}: below must be a number above the previous tier's, got {below!r}")
                else:
                    last = below
        return found

    def is_standard(self, strike):
        """Say whether ``strike`` is a whole multiple of its tier's step."""
        for below, step in self._tiers:
            if strike < below:
                return abs(strike - step * round(strike / step)) <= self.tolerance
        raise ValueError(f"no strike_grid tier covers strike {strike!r}")  # unreachable: the last tier is open


class _Day:
    """The tally of one (root, date): row and quote-state counts, flags, and the reported price range."""

    __slots__ = ("rows", "states", "bid_positive", "flagged", "bad_price", "null_price", "price_low", "price_high",
                 "odd", "near_low", "near_high")

    def __init__(self):
        self.rows = 0
        self.states = [0] * len(STATES)
        self.bid_positive = 0
        self.flagged = 0
        self.bad_price = 0
        self.null_price = 0
        self.price_low = None
        self.price_high = None
        self.odd = 0
        self.near_low = None
        self.near_high = None

    def see_near(self, strike):
        """Widen the interval of near-the-money strikes to hold ``strike``."""
        self.near_low = strike if self.near_low is None else min(self.near_low, strike)
        self.near_high = strike if self.near_high is None else max(self.near_high, strike)

    def usable(self):
        """Count the day's usable quotes."""
        return sum(self.states[i] for i in _USABLE_INDEXES)

    def see_price(self, price):
        """Widen the reported-price range to hold ``price``."""
        self.price_low = price if self.price_low is None else min(self.price_low, price)
        self.price_high = price if self.price_high is None else max(self.price_high, price)


class ChainCensus(Node):
    """Count, per root, what a daily option-chain stream holds (ADR-0265 H-census).

    Role ``transform``; input ``records`` (any iterable of row mappings, read once) and optional
    ``closes`` (rows of an independent daily close, for the underlying-price check). Outputs:

    ``records``
        One row per root, sorted by symbol: ``rows``, ``dates``, first/last date, first/last date
        with a bid above zero (``first_bid_date``, ``last_bid_date``) and with a usable quote,
        ``never_quoted``, the share of rows in each quote state over all rows and from the first
        bid date (``*_share``, ``*_share_from_first_bid``), the ``iv_error`` placeholder share,
        ``median_quoted_per_date`` (usable quotes a date, dates from the first bid date),
        ``median_quoted_development`` and ``liquidity_tercile`` (dates before
        ``liquidity_end_before`` only), non-standard strike rows and share, and the price check.
    ``rows_by_year``
        ``symbol, year, rows, dates, rows_per_date``.
    ``strikes_by_month``
        ``symbol, month, rows, nonstandard_rows, nonstandard_strikes`` (distinct strikes).
    ``summary``
        Run-level counts, the quote-state totals, and ``skipped``: every check whose params or
        input were absent, with the reason.

    Parameters
    ----------
    params : dict
        Row vocabulary, REQUIRED: ``symbol_field``, ``date_field``, ``strike_field``,
        ``bid_field``, ``ask_field``. ``end_before`` (REQUIRED ISO date): a row dated at or after
        it refuses (the registered stores end 2025-12-31, so none are expected; the node cannot promise a sealed row was not parsed first). Optional checks (skipped when absent):
        ``iv_error_field`` with ``iv_error_flag`` (the placeholder value, compared as a number);
        ``strike_grid`` (list of tiers, see :class:`StrikeGrid`) and ``strike_tolerance``;
        ``liquidity_end_before`` (ISO date; the development median and terciles use dates before
        it); ``underlying_price_field`` with, for the price check, ``close_symbol_field``,
        ``close_date_field``, ``close_field`` and ``price_tolerance`` (a relative tolerance, > 0).
        ``allow_unit_mismatch`` (bool, default false): a reader whose ``report()`` counts
        ``unit_row_mismatch`` > 0 (a split or partial unit) makes the run refuse unless this is true.

    Examples
    --------
    The census of a vendor chain stream::

        node = ChainCensus("census", {
            "symbol_field": "symbol", "date_field": "date", "strike_field": "strike",
            "bid_field": "bid", "ask_field": "ask", "end_before": "2026-01-01"})
        out = node.run(ctx, {"records": rows})
        # -> out["records"][0] == {"symbol": "AAPL", "first_bid_date": "2016-01-04", ...}
    """

    role = "transform"
    outputs = ("records", "rows_by_year", "strikes_by_month", "reuse_candidates", "summary")

    _REQUIRED = ("symbol_field", "date_field", "strike_field", "bid_field", "ask_field", "end_before")
    _CLOSE = ("close_symbol_field", "close_date_field", "close_field", "price_tolerance")
    _REUSE = ("reuse_price_factor", "reuse_rows_factor", "reuse_window", "reuse_band", "reuse_overlap_max",
              "reuse_split_share")
    _PARAMS = (*_REQUIRED, "iv_error_field", "iv_error_flag", "strike_grid", "strike_tolerance",
               "liquidity_end_before", "underlying_price_field", "allow_unit_mismatch", *_CLOSE, *_REUSE)

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
        for name in (*cls._REQUIRED[:-1], *(n for n in ("iv_error_field", "underlying_price_field", *cls._CLOSE[:-1])
                                            if n in params)):
            if not isinstance(params.get(name), str) or not params.get(name):
                problems.append(f"{name} must be a non-empty string, got {params.get(name)!r}")
        for name in ("end_before", "liquidity_end_before"):
            if name in params or name == "end_before":
                cls._date_problem(problems, name, params.get(name))
        cls._flag_problems(problems, params)
        if "allow_unit_mismatch" in params and not isinstance(params["allow_unit_mismatch"], bool):
            problems.append(f"allow_unit_mismatch must be a boolean, got {params['allow_unit_mismatch']!r}")
        if "strike_grid" in params:
            problems.extend(StrikeGrid.problems(params["strike_grid"]))
        tol = params.get("strike_tolerance")
        if "strike_tolerance" in params and (not number_ok(tol) or tol < 0):
            problems.append(f"strike_tolerance must be a number >= 0, got {tol!r}")
        if "strike_tolerance" in params and "strike_grid" not in params:
            problems.append("strike_tolerance needs strike_grid")
        cls._close_problems(problems, params)
        cls._reuse_problems(problems, params)
        return problems

    @classmethod
    def _reuse_problems(cls, problems, params):
        """Check the symbol-reuse detector's knobs: a price factor is required by every other, and a price field."""
        given = [n for n in cls._REUSE if n in params]
        if not given:
            return
        if "reuse_price_factor" not in params:
            problems.append(f"{given} need reuse_price_factor (the detector's one required knob)")
        if "underlying_price_field" not in params:
            problems.append("the reuse detector reads prices: declare underlying_price_field")
        for name in ("reuse_price_factor", "reuse_rows_factor"):
            if name in params and (not number_ok(params[name]) or params[name] <= 1):
                problems.append(f"{name} must be a number > 1, got {params[name]!r}")
        if "reuse_window" in params:
            check_int_param(problems, "reuse_window", params["reuse_window"], ge=1)
        if "reuse_band" in params and (not number_ok(params["reuse_band"]) or params["reuse_band"] <= 0):
            problems.append(f"reuse_band must be a number > 0, got {params['reuse_band']!r}")
        for name in ("reuse_overlap_max", "reuse_split_share"):
            if name in params and (not number_ok(params[name]) or not 0 <= params[name] <= 1):
                problems.append(f"{name} must be a number in [0, 1], got {params[name]!r}")

    @staticmethod
    def _date_problem(problems, name, value):
        """Append a problem unless ``value`` is an ISO ``YYYY-MM-DD`` date text."""
        try:
            ok = isinstance(value, str) and len(value) == 10 and iso_day(value, name).isoformat() == value
        except ValueError:
            ok = False
        if not ok:
            problems.append(f"{name} must be an ISO date text YYYY-MM-DD, got {value!r}")

    @staticmethod
    def _flag_problems(problems, params):
        """Check that ``iv_error_field`` and ``iv_error_flag`` are declared together."""
        if ("iv_error_field" in params) != ("iv_error_flag" in params):
            problems.append("declare iv_error_field and iv_error_flag together (the flag is the placeholder value)")
        flag = params.get("iv_error_flag")
        if "iv_error_flag" in params and not number_ok(flag):
            problems.append(f"iv_error_flag must be a number, got {flag!r}")

    @classmethod
    def _close_problems(cls, problems, params):
        """Check that the close fields come together, with a tolerance and the underlying price field."""
        given = [n for n in cls._CLOSE if n in params]
        if given and len(given) < len(cls._CLOSE):
            problems.append(f"declare all of the close params {list(cls._CLOSE)} or none; got {given}")
        if given and "underlying_price_field" not in params:
            problems.append("the close params need underlying_price_field (the price compared with the close)")
        tol = params.get("price_tolerance")
        if "price_tolerance" in params and (not number_ok(tol) or tol <= 0):
            problems.append(f"price_tolerance must be a number > 0, got {tol!r}")

    # -- reading ---------------------------------------------------------------------------

    def _cells(self, record, index, fields, dates):
        """Return ``(symbol, date, strike, bid, ask, flag, price)`` of one row, refusing by row."""
        where = f"{self.key}: row {index}"
        values = {}
        for name, field in fields.items():
            if not isinstance(record, dict) or field not in record:
                raise ValueError(f"{where}: missing field {field!r} ({name})")
            values[name] = record[field]
        symbol = values["symbol"]
        if not isinstance(symbol, str) or not symbol:
            raise ValueError(f"{where}: symbol must be a non-empty string, got {symbol!r}")
        text = values["date"]
        day = dates.get(text)
        if day is None:
            day = dates[text] = iso_day(text, f"{where} date").isoformat()
        if day >= self.params["end_before"]:
            raise ValueError(f"{where}: date {day} is at or after end_before {self.params['end_before']} "
                             "- the node refuses rows on or after end_before (the registered stores end "
                             "2025-12-31, so none are expected)")
        strike = values["strike"]
        if not number_ok(strike) or strike <= 0:
            raise ValueError(f"{where}: strike must be a number > 0, got {strike!r}")
        try:
            bid, ask = _side("bid", values["bid"]), _side("ask", values["ask"])
            price = _side("underlying price", values.get("price"))
        except ValueError as err:
            raise ValueError(f"{where}: {err}") from None
        return symbol, day, float(strike), bid, ask, values.get("flag"), price

    def _fields(self):
        """Map the row roles to the declared field names; roles whose field is absent are left out."""
        p = self.params
        fields = {"symbol": p["symbol_field"], "date": p["date_field"], "strike": p["strike_field"],
                  "bid": p["bid_field"], "ask": p["ask_field"]}
        if "iv_error_field" in p:
            fields["flag"] = p["iv_error_field"]
        if "underlying_price_field" in p:
            fields["price"] = p["underlying_price_field"]
        return fields

    def _is_flag(self, value):
        """Say whether a row's vendor error cell equals the declared placeholder, as a number."""
        try:
            return float(value) == float(self.params["iv_error_flag"])
        except (TypeError, ValueError):
            return False

    def _consume(self, rows):
        """Tally every row once; return ``(days, months)``, refusing an empty input."""
        fields, dates = self._fields(), {}
        grid = StrikeGrid(self.params["strike_grid"], self.params.get("strike_tolerance", DEFAULT_STRIKE_TOLERANCE)) \
            if "strike_grid" in self.params else None
        days, months, seen = {}, {}, 0
        band = self.params.get("reuse_band", DEFAULT_REUSE_BAND) if "reuse_price_factor" in self.params else None
        for index, record in enumerate(rows):
            seen += 1
            symbol, day, strike, bid, ask, flag, price = self._cells(record, index, fields, dates)
            tally = days.get((symbol, day))
            if tally is None:
                tally = days[(symbol, day)] = _Day()
            tally.rows += 1
            tally.states[_INDEX[quote_state(bid, ask)]] += 1
            tally.bid_positive += bid is not None and bid > 0
            tally.flagged += "flag" in fields and self._is_flag(flag)
            if price is not None and price > 0:
                tally.see_price(price)
                if band is not None and abs(strike / price - 1) <= band:
                    tally.see_near(strike)
            elif price is not None:
                tally.bad_price += 1
            elif "price" in fields:
                tally.null_price += 1
            if grid is not None:
                cell = months.setdefault((symbol, day[:7]), [0, 0, set()])
                cell[0] += 1
                if not grid.is_standard(strike):
                    tally.odd += 1
                    cell[1] += 1
                    cell[2].add(strike)
        if not seen:
            raise ValueError(f"{self.key}: the records input is empty - a census of nothing is refused, "
                             "not passed (wrong store, wrong filter, or a pull that landed nothing)")
        return days, months

    def _closes(self, rows):
        """Map ``(symbol, ISO date)`` -> close from the optional second input, refusing duplicates."""
        p = self.params
        if any(n not in p for n in self._CLOSE):
            raise ValueError(f"{self.key}: a closes input is wired but the close params "
                             f"{list(self._CLOSE)} and underlying_price_field are not all declared")
        out, dates = {}, {}
        for index, record in enumerate(rows):
            where = f"{self.key}: closes row {index}"
            try:
                symbol, text, close = (record[p["close_symbol_field"]], record[p["close_date_field"]],
                                       record[p["close_field"]])
            except (KeyError, TypeError) as err:
                raise ValueError(f"{where}: missing field {err}") from None
            day = dates.get(text)
            if day is None:
                day = dates[text] = iso_day(text, f"{where} date").isoformat()
            if day >= p["end_before"]:
                raise ValueError(f"{where}: date {day} is at or after end_before {p['end_before']} - filter the "
                                 "closes upstream")
            if not number_ok(close) or close <= 0:
                raise ValueError(f"{where}: close must be a number > 0, got {close!r}")
            if (symbol, day) in out:
                raise ValueError(f"{where}: duplicate close for {symbol!r} on {day}")
            out[(symbol, day)] = float(close)
        return out

    # -- reducing --------------------------------------------------------------------------

    @staticmethod
    def _by_root(days):
        """Group ``{(symbol, date): tally}`` into ``{symbol: [(date, tally), ...]}`` in date order."""
        roots = {}
        for (symbol, day), tally in sorted(days.items()):
            roots.setdefault(symbol, []).append((day, tally))
        return roots

    def _root_row(self, symbol, series, odd_strikes):
        """Build one root's row of the coverage table."""
        total = sum(t.rows for _, t in series)
        first_bid = next((d for d, t in series if t.bid_positive), None)
        last_bid = next((d for d, t in reversed(series) if t.bid_positive), None)
        usable = [(d, t) for d, t in series if t.usable()]
        row = {"symbol": symbol, "rows": total, "dates": len(series), "first_date": series[0][0],
               "last_date": series[-1][0], "first_bid_date": first_bid, "last_bid_date": last_bid,
               "first_quoted_date": usable[0][0] if usable else None,
               "last_quoted_date": usable[-1][0] if usable else None, "never_quoted": first_bid is None}
        tail = [(d, t) for d, t in series if first_bid is not None and d >= first_bid]
        tail_rows = sum(t.rows for _, t in tail)
        row["rows_from_first_bid"] = tail_rows
        for state in STATES[:-1]:
            row[f"{state}_share"] = sum(t.states[_INDEX[state]] for _, t in series) / total
            row[f"{state}_share_from_first_bid"] = self._share(
                sum(t.states[_INDEX[state]] for _, t in tail), tail_rows)
        row["usable_share"] = sum(t.usable() for _, t in series) / total
        row["usable_share_from_first_bid"] = self._share(sum(t.usable() for _, t in tail), tail_rows)
        counts = [t.usable() for _, t in tail]
        row["median_quoted_per_date"] = median(counts) if counts else None
        if "iv_error_field" in self.params:
            row["iv_error_share"] = sum(t.flagged for _, t in series) / total
            row["iv_error_share_from_first_bid"] = self._share(sum(t.flagged for _, t in tail), tail_rows)
        if "liquidity_end_before" in self.params:
            cut = self.params["liquidity_end_before"]
            development = [t.usable() for d, t in tail if d < cut]
            row["median_quoted_development"] = median(development) if development else None
            row["liquidity_tercile"] = None
        if "underlying_price_field" in self.params:
            row["underlying_price_null_rows"] = sum(t.null_price for _, t in series)
            row["underlying_price_nonpositive_rows"] = sum(t.bad_price for _, t in series)
        if "strike_grid" in self.params:
            row["nonstandard_strike_rows"] = odd_strikes.get(symbol, 0)
            row["nonstandard_strike_share"] = odd_strikes.get(symbol, 0) / total
        return row

    @staticmethod
    def _share(count, total):
        """Return ``count / total``, or ``None`` when there is nothing to divide by."""
        return count / total if total else None

    @staticmethod
    def _terciles(rows):
        """Write 1 (least liquid) .. 3 onto ``liquidity_tercile`` by rank of the development median."""
        ranked = sorted((r for r in rows if r["median_quoted_development"] is not None),
                        key=lambda r: (r["median_quoted_development"], r["symbol"]))
        for rank, row in enumerate(ranked):
            row["liquidity_tercile"] = 1 + (rank * 3) // len(ranked)

    @staticmethod
    def _by_year(roots):
        """Build the rows-per-date-by-year table."""
        out = []
        for symbol, series in roots.items():
            years = {}
            for day, tally in series:
                cell = years.setdefault(int(day[:4]), [0, 0])
                cell[0] += tally.rows
                cell[1] += 1
            out.extend({"symbol": symbol, "year": year, "rows": rows, "dates": dates,
                        "rows_per_date": rows / dates} for year, (rows, dates) in sorted(years.items()))
        return out

    def _price_check(self, row, series, closes):
        """Write the underlying-price-versus-close fields of one root onto ``row``."""
        tol = self.params["price_tolerance"]
        diffs, missing, spread = [], 0, 0
        for day, tally in series:
            if tally.price_low is None:
                continue
            spread += (tally.price_high - tally.price_low) / tally.price_low > tol
            close = closes.get((row["symbol"], day))
            if close is None:
                missing += 1
                continue
            diffs.append(abs((tally.price_low + tally.price_high) / 2 - close) / close)
        row.update(price_compared=len(diffs), price_missing_close=missing, price_spread_dates=spread,
                   price_median_abs_rel_diff=median(diffs) if diffs else None,
                   price_max_abs_rel_diff=max(diffs) if diffs else None,
                   price_share_beyond_tolerance=sum(d > tol for d in diffs) / len(diffs) if diffs else None)

    def _skipped(self, closes_wired):
        """Say which optional checks did not run, and why."""
        p, skipped = self.params, {}
        if "strike_grid" not in p:
            skipped["strike_grid"] = "no strike_grid declared: non-standard strikes not counted"
        if "iv_error_field" not in p:
            skipped["iv_error"] = "no iv_error_field declared: the placeholder share not computed"
        if "liquidity_end_before" not in p:
            skipped["liquidity_development"] = "no liquidity_end_before declared: development median and terciles not computed"
        if "reuse_price_factor" not in p:
            skipped["reuse"] = "no reuse_price_factor declared: suspected symbol reuse not looked for"
        if "underlying_price_field" not in p:
            skipped["price_check"] = "no underlying_price_field declared: the price check did not run"
        elif not closes_wired:
            skipped["price_check"] = "no closes input wired: the underlying_price check against an external close did not run"
        return skipped

    def run(self, ctx, inputs):
        """Count the stream and return the census tables.

        Parameters
        ----------
        ctx : NodeContext
            Unused; the node is a function of its inputs and params.
        inputs : dict
            ``records``: iterable of chain rows, read once. ``closes`` (optional): iterable of
            close rows for the price check.

        Returns
        -------
        dict
            ``records``, ``rows_by_year``, ``strikes_by_month``, ``summary`` (see the class).

        Raises
        ------
        ValueError
            The input is empty, a row is dated at or after ``end_before`` or is malformed, or the
            closes input repeats a (symbol, date) or is wired without its params.
        """
        closes = self._closes(inputs["closes"]) if inputs.get("closes") is not None else None
        days, months = self._consume(inputs["records"])
        roots = self._by_root(days)
        cells = sorted(months.items())
        odd = {}
        for (symbol, _), cell in cells:
            odd[symbol] = odd.get(symbol, 0) + cell[1]
        rows = [self._root_row(symbol, series, odd) for symbol, series in roots.items()]
        if "liquidity_end_before" in self.params:
            self._terciles(rows)
        if closes is not None:
            for row in rows:
                self._price_check(row, roots[row["symbol"]], closes)
        reuse, explained = self._reuse(roots)
        by_month = [{"symbol": symbol, "month": month, "rows": cell[0], "nonstandard_rows": cell[1],
                     "nonstandard_strikes": len(cell[2])} for (symbol, month), cell in cells]
        summary = self._summary(days, rows, closes is not None)
        if "reuse_price_factor" in self.params:
            summary["reuse"] = {"candidates": len(reuse), "split_explained": explained}
        report = getattr(inputs["records"], "report", None)
        if callable(report):
            summary["reader"] = report()
            if summary["reader"].get("unit_row_mismatch", 0) and not self.params.get("allow_unit_mismatch", False):
                raise ValueError(f"{self.key}: the reader found {summary['reader']['unit_row_mismatch']} unit(s) "
                                 "whose older copy has a different row count than the kept one (a split or partial "
                                 "unit): unit_row_mismatch > 0 refuses unless allow_unit_mismatch is true")
        self.log.info("census: %d root(s), %d row(s), %d never quoted", summary["roots"],
                      summary["rows"], summary["never_quoted_roots"])
        return {"records": rows, "rows_by_year": self._by_year(roots), "strikes_by_month": by_month,
                "reuse_candidates": reuse, "summary": summary}

    def _reuse(self, roots):
        """Run the symbol-reuse detector over every root; return ``(candidates, split_explained)`` (empty if off)."""
        p = self.params
        if "reuse_price_factor" not in p:
            return [], 0
        detector = ReuseDetector(p["reuse_price_factor"], p.get("reuse_rows_factor"),
                                 p.get("reuse_window", DEFAULT_REUSE_WINDOW),
                                 p.get("reuse_overlap_max", DEFAULT_REUSE_OVERLAP),
                                 p.get("reuse_split_share", DEFAULT_REUSE_SPLIT))
        gridded, found, explained = "strike_grid" in p, [], 0
        for symbol, series in roots.items():
            days = [{"date": day, "rows": t.rows, "price": (t.price_low + t.price_high) / 2,
                     "near": None if t.near_low is None else (t.near_low, t.near_high),
                     "odd": t.odd if gridded else None} for day, t in series if t.price_low is not None]
            hits, split = detector.scan(symbol, days)
            found += hits
            explained += split
        return sorted(found, key=lambda c: (c["symbol"], c["date"])), explained

    def _summary(self, days, rows, closes_wired):
        """Build the run-level counts."""
        dates = [day for _, day in days]
        return {"roots": len(rows), "rows": sum(t.rows for t in days.values()), "first_date": min(dates),
                "last_date": max(dates), "never_quoted_roots": sum(r["never_quoted"] for r in rows),
                "states": {s: sum(t.states[i] for t in days.values()) for i, s in enumerate(STATES)},
                "end_before": self.params["end_before"], "skipped": self._skipped(closes_wired)}
