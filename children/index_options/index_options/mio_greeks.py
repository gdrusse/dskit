"""Greeks diagnostics over a SAVED, closed MIO run (ADR-0310 stage 2).

Diagnostics, never accounting: the books were held to expiry, so nothing here
prices, marks, sizes or reserves anything. :class:`MIOGreeksDiagnostics` is a thin
child of :class:`dskit.pipeline.option_exposure.BookGreeksReport`: it maps the
saved files to the generic inputs (the three hooks), derives the A0006 cell,
enforces the data window, and writes four new files elsewhere. All pricing, beta,
split and aggregation arithmetic is the core's.

It opens only ``result.json``, ``trades.csv``, ``daily-account.csv`` and the pinned
chain and bar parquet the result names, read-only, and writes only under
``out_dir``. It never opens ``run.log`` (RE-ENTRY "Do not"), and imports no solver,
no study and no replay. Every date it may touch lies below ``sealed_from`` (and
below Path A0003's protected start): the seal is checked on the books' dates and
again by the chain reader, and bars are read only below it.

``result.json`` holds every method's P&L and cannot be read in part with the stdlib,
so the whole file is parsed; the seal and date check on the requested methods' trades
therefore runs first, before any P&L field is used (the recompute gate included).

Refusals carry stable codes (:class:`GreeksRefusal`): ``window_outside_data_rights``,
``development_seal_exceeded`` (a config's ``sealed_from`` later than
:data:`DEVELOPMENT_SEAL`), ``bad_date``, ``trades_schema``, ``out_dir_inside_run_dir``,
``input_not_closed``, ``pnl_recompute_mismatch``, ``beta_lookback_mismatch``,
``beta_min_pairs_mismatch`` (the run's ``correlation_min_pairs``), ``symbols_missing`` and
``multiplier_mismatch`` (the config's ``multiplier`` must equal the run's own solver
multiplier), and (ADR-0310 A2) ``quarter_mismatch``, ``dividend_column_absent`` and
``cell_by_incomplete``; every output row carries the run's ``quarter`` and each method's summary
carries ``direction_read`` (:meth:`MIOGreeksDiagnostics._direction_read`); ``seal_crossing`` ``exclude`` drops, by dates alone, books that settle past
the seal. The per-book and per-day codes are the core's, hedge flags included. The static delta-hedge column's dividends come from the pinned bar
files' ``dividend_amount`` column when it exists (:meth:`MIOGreeksDiagnostics._hedge_dividend`
through :func:`~index_options.contracts.dividends_paid`), else every row is flagged
``dividends_absent``. Import cost: stdlib, plus pyarrow when a chain or bar file is read.
"""

import csv
import hashlib
import io
import calendar
import json
import math
import os
import re
from collections import Counter, OrderedDict
from datetime import date, timedelta
from pathlib import Path

from dskit.onboarding.artifacts import PinnedPayloadSource
from dskit.pipeline.node import atomic_write, check_int_param
from dskit.pipeline.option_exposure import STOCK_HEDGE_NOTE, BookGreeksReport, StaticDeltaHedge
from dskit.pipeline.option_structures import structure_payoff

from .contracts import dividends_paid
from .mio_run_events import MioBookOutcomes

__all__ = ["ChainQuotes", "CellRank", "GreeksRefusal", "MIOGreeksDiagnostics"]

#: Path A0003: the holdout opens on this session; no seal may lie beyond it.
PROTECTED_FROM = "2024-01-02"
#: The first date the owner has NOT opened for development economics (RE-ENTRY: no 2022-2023 MIO
#: economics before the owner's 3 picks). One owner; it moves only when the owner lifts it.
DEVELOPMENT_SEAL = "2021-07-01"
#: The ADR's exact-match gate between a saved P&L and its recomputation, in dollars.
PNL_GATE_USD = 0.005
#: A0006 / ADR-0310 A1: a cell rank is the book expiry's position among the listed slots; weekly
#: names rank 1..8, monthly-only names 1..3. The classification horizon is
#: ``WEEKLY_MAX_RANK x WEEKLY_WINDOW_DAYS`` days.
WEEKLY_WINDOW_DAYS, WEEKLY_MAX_RANK, MONTHLY_MAX_POSITION = 7, 8, 3
#: Owner option (a): a name is weekly with at least this many non-monthly slots in the horizon.
WEEKLY_MIN_NONSTANDARD = 2
#: The ``rank_reason`` codes of a book row.
RANK_OK, RANK_NON_SLOT, RANK_OUT_OF_RANGE, RANK_NOT_LISTED = (
    "ok", "non_slot_expiry", "rank_out_of_range", "expiry_not_listed")
#: The saved files read (``run.log`` is deliberately absent).
INPUT_FILES = ("result.json", "trades.csv", "daily-account.csv")
#: The files written, under ``out_dir``.
OUTPUT_FILES = ("books.csv", "cells.csv", "exposure.csv", "greeks-summary.json")
#: Default size of the chain day cache (a node sets it by ``chain_cache_days``).
DEFAULT_CACHE_DAYS = 8
#: Chain columns read when present (``iv`` may be absent: every leg is then ``leg_no_iv``).
CHAIN_COLUMNS = ("date", "expiration", "type", "strike_raw", "bid", "ask", "iv")
#: ``seal_crossing`` values: refuse a book that settles past the seal (default), or exclude it.
SEAL_CROSSING_MODES = ("refuse", "exclude")
#: ``dividends`` values: ``required`` refuses a bar file with no ``dividend_amount``.
DIVIDEND_MODES = ("required", "optional")
_QUARTER = re.compile(r"^(\d{4})Q([1-4])$")
_DATE_FIELDS = ("decision_date", "fill_date", "expiry")
_PLACEMENT = ("run_dir", "out_dir")
#: Params that do not change the arithmetic, so stay out of the identity hash.
_NON_IDENTITY = (*_PLACEMENT, "chain_cache_days")


class GreeksRefusal(ValueError):
    """A refusal with a stable ``code`` (message ``"<code>: <detail>"``).

    Examples
    --------
    Raise and read a code::

        try:
            raise GreeksRefusal("input_not_closed", "result.json is absent")
        except GreeksRefusal as refusal:
            refusal.code    # -> "input_not_closed"
    """

    def __init__(self, code, detail):
        super().__init__(f"{code}: {detail}")
        self.code = code


class CellRank:
    """The A0006 cell rank of a book, from the expiries listed on its decision day (ADR-0310 A1).

    Slots are the listed expiries with DTE >= 1 that are a Friday, or a Thursday whose ISO
    week lists no Friday (the holiday expiry). The rank is the 1-based position of the book's
    expiry among the sorted slots, ``None`` past :data:`WEEKLY_MAX_RANK` (weekly) or
    :data:`MONTHLY_MAX_POSITION` (monthly-only). A name is weekly when at least
    :data:`WEEKLY_MIN_NONSTANDARD` slots within ``WEEKLY_MAX_RANK x WEEKLY_WINDOW_DAYS`` days
    are not standard monthly expiries (Friday on day 15-21, or Thursday on day 14-20).
    Nothing but ``listed`` is read, so no later day can change a rank.

    Examples
    --------
    A weekly name and a monthly-only name::

        CellRank().of("2021-04-05", "2021-04-16", ["2021-04-09", "2021-04-16", "2021-04-23"])
        # -> ("weekly", 2, "ok")
        CellRank().of("2021-04-05", "2021-05-21", ["2021-04-16", "2021-05-21"])
        # -> ("monthly", 2, "ok")
    """

    def of(self, decision_date, expiry, listed):
        """Return ``(cadence, rank, rank_reason)``; ``rank`` is ``None`` unless the reason is ``ok``.

        Parameters
        ----------
        decision_date, expiry : str
            ISO dates.
        listed : list of str
            The expiries listed on the decision day (and only that day).

        Returns
        -------
        tuple
            ``("weekly" | "monthly", int or None, reason)``; reason is ``ok``,
            ``non_slot_expiry``, ``rank_out_of_range`` or ``expiry_not_listed``.

        Raises
        ------
        GreeksRefusal
            ``bad_date`` when ``decision_date``, ``expiry`` or a listed entry is not a canonical
            ISO date.
        """
        start = self._day(decision_date)
        slots = self._slots(start, [self._day(e) for e in listed])
        horizon = start + timedelta(days=WEEKLY_MAX_RANK * WEEKLY_WINDOW_DAYS)
        stray = sum(not self._standard(d) for d in slots if d <= horizon)
        if stray >= WEEKLY_MIN_NONSTANDARD:
            cadence, cap = "weekly", WEEKLY_MAX_RANK
        else:
            cadence, cap = "monthly", MONTHLY_MAX_POSITION
        book = self._day(expiry)
        if expiry not in listed:
            return cadence, None, RANK_NOT_LISTED
        if book not in slots:
            return cadence, None, RANK_NON_SLOT
        position = slots.index(book) + 1
        if position > cap:
            return cadence, None, RANK_OUT_OF_RANGE
        return cadence, position, RANK_OK

    @staticmethod
    def _day(text):
        """Return ``text`` as a date, or refuse ``bad_date`` (never a bare ``ValueError``)."""
        if MIOGreeksDiagnostics._iso(text) is None:
            raise GreeksRefusal(MIOGreeksDiagnostics.BAD_DATE, f"{text!r} is not an ISO date")
        return date.fromisoformat(text)

    @staticmethod
    def _slots(start, listed):
        """Return the sorted slots: Fridays, plus a Thursday when its ISO week lists no Friday."""
        ahead = [d for d in set(listed) if d > start]
        fridays = {d.isocalendar()[:2] for d in ahead if d.weekday() == 4}
        return sorted(d for d in ahead if d.weekday() == 4
                      or (d.weekday() == 3 and d.isocalendar()[:2] not in fridays))

    @staticmethod
    def _standard(day):
        """Return whether ``day`` is a standard monthly: third Friday, or its holiday Thursday."""
        return 15 <= day.day <= 21 if day.weekday() == 4 else 14 <= day.day <= 20

class ChainQuotes:
    """Read-only per-day view of the pinned chain parquet, never at or past ``end_before``.

    Parameters
    ----------
    binding : dict
        The plan's ``source_pins.chain``: ``pin`` and ``files`` (symbol -> file).
    end_before : str
        ISO date; a day on or after it refuses ``window_outside_data_rights``.
    cache_days : int
        Most (name, session) days kept; the least recently used is evicted, so
        memory is bounded however many days are read.

    Examples
    --------
    One expiry's rows for a name on a day::

        quotes = ChainQuotes(binding, "2021-07-01")
        rows = quotes.chain("BLK", "2021-04-06", "2021-04-16")   # right, strike, bid, ask, iv
    """

    def __init__(self, binding, end_before, cache_days=DEFAULT_CACHE_DAYS):
        self.binding, self.end_before, self.cache_days = binding, end_before, cache_days
        self._source = PinnedPayloadSource(binding["pin"])
        self._paths, self._days = {}, OrderedDict()
        #: Relative file -> its pinned sha256, for every file opened.
        self.files = {}

    def _day(self, symbol, day):
        """Return ``{expiry: rows}`` for one name and session, read once."""
        if day >= self.end_before:
            raise GreeksRefusal(MIOGreeksDiagnostics.WINDOW,
                                f"chain day {day} is on or after {self.end_before}")
        if (symbol, day) in self._days:
            self._days.move_to_end((symbol, day))
        else:
            import pyarrow.parquet as pq

            if symbol not in self._paths:
                if symbol not in self.binding["files"]:
                    raise GreeksRefusal(MIOGreeksDiagnostics.SYMBOLS_MISSING,
                                        f"no chain file for {symbol}")
                relative = self.binding["files"][symbol]
                self._paths[symbol] = self._source.file(relative, verify=True)
                self.files[relative] = self.binding["pin"]["files"][relative]
            path = self._paths[symbol]
            columns = [c for c in CHAIN_COLUMNS if c in pq.read_schema(path).names]
            rows = pq.read_table(path, columns=columns, filters=[("date", "=", day)]).to_pylist()
            by_expiry = {}
            for r in rows:
                by_expiry.setdefault(r["expiration"], []).append(
                    {"right": r["type"], "strike": r["strike_raw"], "bid": r["bid"],
                     "ask": r["ask"], "iv": r.get("iv")})
            self._days[symbol, day] = by_expiry
            while len(self._days) > self.cache_days:
                self._days.popitem(last=False)
        return self._days[symbol, day]

    def chain(self, symbol, day, expiry):
        """Return the rows of one expiry on ``day`` (empty when none are saved)."""
        return self._day(symbol, day).get(expiry, [])

    def expiries(self, symbol, day):
        """Return the sorted expiries listed for ``symbol`` on ``day``.

        The listing is that session's own data: the expiries the chain quoted on ``day``, which
        for a book's decision day is day-of-decision data and never a later day's. A ``day`` on
        or after the seal refuses ``window_outside_data_rights`` like any other read.
        """
        return sorted(self._day(symbol, day))


class MIOGreeksDiagnostics(BookGreeksReport):
    """Greeks diagnostics over one saved closed MIO run; see the module docstring.

    Overrides only the three hooks and the data-window guard; :meth:`run` reads the
    saved files, calls the core once per method and writes the four files.

    Parameters
    ----------
    key : str
        Node key.
    params : dict
        Every :class:`BookGreeksReport` knob, plus ``run_dir`` (the closed run,
        read-only), ``out_dir`` (a new place outside it), ``methods`` (distinct
        result ids), ``window`` (``{start, end}`` ISO, inclusive) and ``sealed_from``
        (required ISO date: any book date on or after it refuses; ``window.end``
        must lie before it, and it may not exceed :data:`DEVELOPMENT_SEAL`),
        ``chain_cache_days`` (int >= 1: the chain day cache size; no effect on a result).
        ``beta_lookback`` must equal a method's ``shock.beta_lookback`` when it
        declares one; ``beta_min_pairs`` the run's ``correlation_min_pairs``;
        ``multiplier`` the run's solver multiplier.

    Examples
    --------
    Diagnose one saved run::

        node = MIOGreeksDiagnostics("greeks", params)    # see configs/mio-greeks-template.json
        out = node.run(None, {})                         # books, cells, exposure, summary
    """

    _CHILD_PARAMS = ("run_dir", "out_dir", "methods", "window", "sealed_from", "chain_cache_days",
                     "quarter", "dividends", "seal_crossing")
    _PARAMS = BookGreeksReport._PARAMS + _CHILD_PARAMS
    WINDOW = "window_outside_data_rights"
    DEVELOPMENT_SEAL_EXCEEDED = "development_seal_exceeded"
    BAD_DATE = "bad_date"
    TRADES_SCHEMA = "trades_schema"
    MIN_PAIRS_MISMATCH = "beta_min_pairs_mismatch"
    OUT_DIR_INSIDE = "out_dir_inside_run_dir"
    NOT_CLOSED = "input_not_closed"
    PNL_MISMATCH = "pnl_recompute_mismatch"
    BETA_MISMATCH = "beta_lookback_mismatch"
    SYMBOLS_MISSING = "symbols_missing"
    MULTIPLIER_MISMATCH = "multiplier_mismatch"
    QUARTER_MISMATCH = "quarter_mismatch"
    DIVIDEND_COLUMN_ABSENT = "dividend_column_absent"
    CELL_BY_INCOMPLETE = "cell_by_incomplete"
    FLAG_MATCHES, FLAG_CONFIG = "matches_shock", "config_value_no_shock"

    # -- config ------------------------------------------------------------

    @classmethod
    def validate_params(cls, params):
        """Return every problem ``params`` has: the core's, then the child's keys.

        Parameters
        ----------
        params : dict
            The node's declared params.

        Returns
        -------
        list of str
            Empty when valid.
        """
        problems = super().validate_params(params)
        if not isinstance(params, dict):
            return problems
        for name in ("run_dir", "out_dir"):
            if name in params and not (isinstance(params[name], str) and params[name]):
                problems.append(f"{name} must be a non-empty path string, got {params[name]!r}")
        if all(isinstance(params.get(n), str) and params[n] for n in _PLACEMENT) and (
                cls._inside(params["run_dir"], params["out_dir"])):
            problems.append(f"{cls.OUT_DIR_INSIDE}: out_dir {params['out_dir']!r} is inside "
                            f"run_dir {params['run_dir']!r}")
        methods = params.get("methods")
        if "methods" in params and (not isinstance(methods, list) or not methods or not all(
                isinstance(m, str) and m for m in methods) or len(set(methods)) != len(methods)):
            problems.append(f"methods must be a non-empty list of distinct ids, got {methods!r}")
        if "chain_cache_days" in params:
            check_int_param(problems, "chain_cache_days", params["chain_cache_days"], ge=1)
        problems += cls._window_problems(params)
        problems += cls._a2_problems(params)
        missing = [n for n in cls._CHILD_PARAMS if n not in params]
        if missing:
            problems.append(f"missing required param(s) {missing}")
        return problems

    @classmethod
    def _a2_problems(cls, params):
        """Return the problems of ``quarter``, ``dividends``, ``seal_crossing`` and ``cell_by`` (A2)."""
        problems = []
        if "quarter" in params and not (isinstance(params["quarter"], str)
                                        and _QUARTER.match(params["quarter"])):
            problems.append(f"quarter must be 'YYYYQn' with n in 1-4, got {params['quarter']!r}")
        for name, allowed in (("dividends", DIVIDEND_MODES), ("seal_crossing", SEAL_CROSSING_MODES)):
            if name in params and params[name] not in allowed:
                problems.append(f"{name} must be one of {list(allowed)}, got {params[name]!r}")
        by = params.get("cell_by")
        if isinstance(by, list) and not {"rank", "cadence"} <= set(by):
            problems.append(f"{cls.CELL_BY_INCOMPLETE}: cell_by must contain rank and cadence "
                            f"(A0006's two rank scales), got {by!r}")
        return problems

    @staticmethod
    def _quarter_bounds(quarter):
        """Return ``(first, last)`` ISO dates of a ``YYYYQn`` calendar quarter."""
        year, number = (int(g) for g in _QUARTER.match(quarter).groups())
        last_month = 3 * number
        return (f"{year:04d}-{last_month - 2:02d}-01",
                f"{year:04d}-{last_month:02d}-{calendar.monthrange(year, last_month)[1]:02d}")

    @staticmethod
    def _iso(value):
        """Return ``value`` when it is a canonical ISO date string, else ``None``."""
        try:
            return value if isinstance(value, str) and len(value) == 10 and date.fromisoformat(
                value) else None
        except ValueError:
            return None

    @classmethod
    def _window_problems(cls, params):
        """Return the problems of ``window`` and ``sealed_from`` (the data-rights pair)."""
        problems, window = [], params.get("window")
        if "window" in params:
            if not isinstance(window, dict) or set(window) != {"start", "end"} or not all(
                    cls._iso(v) for v in window.values()) or window["start"] > window["end"]:
                problems.append(f"window must be {{start, end}} ISO dates, start <= end, got {window!r}")
                window = None
        if "sealed_from" in params:
            seal = params["sealed_from"]
            if not cls._iso(seal) or seal > PROTECTED_FROM:
                problems.append(f"sealed_from must be an ISO date no later than {PROTECTED_FROM}, "
                                f"got {seal!r}")
            elif seal > DEVELOPMENT_SEAL:
                problems.append(f"{cls.DEVELOPMENT_SEAL_EXCEEDED}: sealed_from {seal} is after "
                                f"{DEVELOPMENT_SEAL}, the development seal the owner has not lifted")
            elif window and window["end"] >= seal:
                problems.append(f"window.end {window['end']} must be before sealed_from {seal} "
                                f"({cls.WINDOW})")
        return problems

    @staticmethod
    def _inside(run_dir, out_dir):
        """Say whether ``out_dir`` is ``run_dir`` or below it, resolving symlinks."""
        run, out = Path(os.path.realpath(run_dir)), Path(os.path.realpath(out_dir))
        return out == run or run in out.parents

    # -- the hooks ---------------------------------------------------------

    def _load_books(self, raw):
        """Return one method's filled books (``raw``: method, trades, bars, multiplier)."""
        books = []
        for t in raw["trades"]:
            books.append({
                "key": f"{raw['method']}|{t['id']}", "symbol": t["symbol"],
                "decision_date": t["decision_date"], "fill_date": t["fill_date"],
                "expiry": t["expiry"], "pnl": t["pnl"], "factor": float(t["factor"]),
                "end_factor": float(t.get("active_factor", t["factor"])),
                "legs": [{"right": leg["right"], "strike": float(leg["strike"]),
                          "sign": leg["sign"], "quantity": leg["quantity"]} for leg in t["legs"]],
                "tags": {"method": raw["method"]}, "credit": t["credit"]})
        for book in books:
            self._recompute(book, raw["bars"], raw["multiplier"])
        return [{k: v for k, v in b.items() if k != "credit"} for b in books]

    def _recompute(self, book, bars, multiplier):
        """Refuse a saved P&L that its credit and the bar settlement do not reproduce."""
        close = bars.get(book["symbol"], {}).get(book["expiry"])
        if close is None or book["factor"] != book["end_factor"]:
            return                          # the core lists these under their own codes
        payoff = structure_payoff([(leg["right"], leg["sign"] * leg["quantity"])
                                   for leg in book["legs"]], close / book["factor"],
                                  [leg["strike"] for leg in book["legs"]])
        expected = book["credit"] + multiplier * payoff
        pnl = book["pnl"]
        if not (isinstance(pnl, (int, float)) and not isinstance(pnl, bool)
                and abs(pnl - expected) < PNL_GATE_USD):
            raise GreeksRefusal(self.PNL_MISMATCH, f"{book['key']}: saved pnl {book['pnl']!r}, "
                                                    f"credit + payoff gives {expected!r}")

    def _load_quotes(self, raw):
        """Return the :class:`ChainQuotes` view itself."""
        return raw

    def _cell_key(self, book, quotes):
        """Return ``symbol``, the A0006 ``rank``, its ``cadence`` and the ``rank_reason``."""
        cadence, rank, reason = CellRank().of(
            book["decision_date"], book["expiry"],
            quotes.expiries(book["symbol"], book["decision_date"]))
        return {"symbol": book["symbol"], "rank": rank, "cadence": cadence, "rank_reason": reason}

    def _seal(self):
        """Return the effective first sealed date."""
        return min(self.params["sealed_from"], PROTECTED_FROM)

    def _guard_window(self, books):
        """Refuse a book dated on or after the seal or outside ``window``."""
        self._guard_dates((b["key"], b) for b in books)

    def _guard_dates(self, named):
        """Refuse ``(name, record)`` pairs with a non-ISO date, or one sealed or outside ``window``."""
        window, seal = self.params["window"], self._seal()
        for name, record in named:
            for field in ("decision_date", "fill_date", "expiry"):
                day = record.get(field)
                if self._iso(day) is None:
                    raise GreeksRefusal(self.BAD_DATE, f"{name} {field} {day!r} is not an ISO date")
                if day >= seal or not window["start"] <= day <= window["end"]:
                    raise GreeksRefusal(self.WINDOW, f"{name} {field} {day} is outside "
                                                     f"[{window['start']}, {window['end']}] or on or "
                                                     f"after {seal}")

    # -- the date census (A2.6): dates only, never an economic field ---------

    def _census(self, table):
        """Return ``{method: [crossing, ...]}`` after the dates-only census of every requested method.

        Reads only ``id``, ``decision_date``, ``fill_date`` and ``expiry`` of each requested
        trade. A date that is not ISO refuses ``bad_date``; a decision or any date outside
        ``window`` that is not a seal crossing, or any date on or after the protected start,
        refuses ``window_outside_data_rights``; a decision outside ``quarter`` refuses
        ``quarter_mismatch``. A *crossing* is a book decided inside the window whose fill or
        expiry lies on or after the seal (and before the protected start): ``seal_crossing``
        ``refuse`` raises with the census, ``exclude`` returns them for the caller to drop.
        """
        window, seal = self.params["window"], self._seal()
        crossing = {m: [] for m in self.params["methods"]}
        trades = {m: [t for r in table["results"] if r["id"] == m for t in r["result"]["trades"]]
                  for m in self.params["methods"]}
        for method, rows in trades.items():
            for t in rows:
                name = f"{method}|{t.get('id')}"
                for field in _DATE_FIELDS:
                    if self._iso(t.get(field)) is None:
                        raise GreeksRefusal(self.BAD_DATE, f"{name} {field} {t.get(field)!r} "
                                                           "is not an ISO date")
                past = [f for f in _DATE_FIELDS if t[f] >= seal]
                stray = [f for f in ("fill_date", "expiry")
                         if not (window["start"] <= t[f] <= window["end"] or f in past)]
                if (not window["start"] <= t["decision_date"] <= window["end"] or stray
                        or any(t[f] >= PROTECTED_FROM for f in _DATE_FIELDS)):
                    raise GreeksRefusal(self.WINDOW, f"{name} dates {[t[f] for f in _DATE_FIELDS]} are "
                                                     f"outside [{window['start']}, {window['end']}] "
                                                     f"and not a seal crossing (sealed_from {seal})")
                crossing[method] += [(t["id"], f, t[f]) for f in past]
        first, last = self._quarter_bounds(self.params["quarter"])
        for method, rows in trades.items():
            bad = [(method, t.get("id"), t["decision_date"]) for t in rows
                   if not first <= t["decision_date"] <= last]
            if bad:
                raise GreeksRefusal(self.QUARTER_MISMATCH, f"{len(bad)} book(s) decided outside "
                                                           f"{self.params['quarter']} [{first}, {last}]: {bad}")
        return crossing, trades

    def _apply_crossing(self, crossing, trades):
        """Return ``(kept trades, excluded rows)`` or refuse with the census (``seal_crossing``)."""
        total = {m: len({i for i, _, _ in found}) for m, found in crossing.items()}
        if self.params["seal_crossing"] == "refuse":
            if any(total.values()):
                detail = [(m, i, f, d) for m, found in crossing.items() for i, f, d in found]
                raise GreeksRefusal(self.WINDOW, f"{len(detail)} seal-crossing date(s) in "
                                                 f"n_crossing={sum(total.values())} book(s), per method "
                                                 f"{total}: {detail} (seal_crossing: refuse; sealed_from "
                                                 f"{self._seal()})")
            return trades, []
        names = {m: {i for i, _, _ in found} for m, found in crossing.items()}
        kept = {m: [t for t in rows if t["id"] not in names[m]] for m, rows in trades.items()}
        excluded = [{"method": m, "id": t["id"], **{f: t[f] for f in _DATE_FIELDS}}
                    for m, rows in trades.items() for t in rows if t["id"] in names[m]]
        return kept, excluded

    @staticmethod
    def _shared_book_count(kept):
        """Return how many books appear under more than one method, keyed on their identity."""
        seen = Counter()
        for rows in kept.values():
            keys = {(t["symbol"], t["decision_date"], t["expiry"], json.dumps(
                [[leg["right"], leg["strike"], leg["sign"], leg["quantity"]] for leg in t["legs"]]))
                    for t in rows}
            seen.update(keys)
        return sum(1 for n in seen.values() if n > 1)

    def _dividend_census(self, block, symbols):
        """Refuse ``dividend_column_absent`` listing every symbol whose bar file lacks the column."""
        import pyarrow.parquet as pq

        source = PinnedPayloadSource(block["pin"])
        lacking = []
        for symbol in sorted(set(symbols)):
            if symbol not in block["files"]:
                raise GreeksRefusal(self.SYMBOLS_MISSING, f"no bar file for {symbol}")
            path = source.file(block["files"][symbol], verify=True)
            if "dividend_amount" not in pq.read_schema(path).names:
                lacking.append(symbol)
        if lacking:
            raise GreeksRefusal(self.DIVIDEND_COLUMN_ABSENT, f"dividends: required, but the bar file of "
                                                             f"{lacking} has no dividend_amount column")

    # -- the saved run -----------------------------------------------------

    def _read_inputs(self, run_dir):
        """Return ``(table, shas)`` of a closed run; refuse ``input_not_closed`` otherwise.

        The whole ``result.json`` is parsed (the format cannot be read in part); no P&L
        field is used until :meth:`run` has checked the requested methods' dates.
        """
        shas, texts = {}, {}
        for name in INPUT_FILES:
            path = run_dir / name
            if not path.is_file():
                raise GreeksRefusal(self.NOT_CLOSED, f"{name} is absent from the run directory")
            data = path.read_bytes()
            shas[name], texts[name] = hashlib.sha256(data).hexdigest(), data.decode("utf-8")
        try:
            table = json.loads(texts["result.json"])
        except ValueError:
            raise GreeksRefusal(self.NOT_CLOSED, "result.json is not readable JSON") from None
        if not isinstance(table, dict) or table.get("complete") is not True or not isinstance(
                table.get("results"), list):
            raise GreeksRefusal(self.NOT_CLOSED, "result.json does not say the run is complete")
        saved = {(r["id"], t["id"]) for r in table["results"] for t in r["result"]["trades"]}
        reader = csv.DictReader(io.StringIO(texts["trades.csv"]))
        if not {"method", "id"} <= set(reader.fieldnames or ()):
            raise GreeksRefusal(self.TRADES_SCHEMA, f"trades.csv columns {reader.fieldnames!r} "
                                                    "lack method and id")
        listed = {(r["method"], r["id"]) for r in reader}
        if saved != listed or len(list(csv.reader(io.StringIO(texts["daily-account.csv"])))) < 2:
            raise GreeksRefusal(self.NOT_CLOSED, "trades.csv or daily-account.csv does not match "
                                                 "result.json")
        return table, shas

    def _domain(self, table):
        """Return ``(domain params, symbols, multiplier)`` or refuse."""
        try:
            domain = table["plan"]["study"]["domain"]["params"]
        except (KeyError, TypeError):
            raise GreeksRefusal(self.SYMBOLS_MISSING, "result.json carries no plan.study.domain") from None
        symbols = domain.get("symbols")
        if not isinstance(symbols, list) or not symbols:
            raise GreeksRefusal(self.SYMBOLS_MISSING, "result.json carries no symbol list")
        try:
            multiplier = domain["bridge"]["solver"]["multiplier"]
        except (KeyError, TypeError):
            raise GreeksRefusal(self.NOT_CLOSED, "result.json carries no solver multiplier") from None
        pairs = domain.get("correlation_min_pairs")
        if pairs is not None and pairs != self.params["beta_min_pairs"]:
            raise GreeksRefusal(self.MIN_PAIRS_MISMATCH, f"config {self.params['beta_min_pairs']!r}, "
                                                         f"run {pairs!r}")
        if multiplier != self.params["multiplier"]:
            raise GreeksRefusal(self.MULTIPLIER_MISMATCH, f"config {self.params['multiplier']!r}, "
                                                          f"run {multiplier!r}")
        return domain, symbols, multiplier

    def _method_flags(self, domain, table):
        """Return ``{method: flag}`` after checking each method's shock lookback."""
        declared = {m.get("id"): m for m in domain.get("methods", []) if isinstance(m, dict)}
        have = {r["id"] for r in table["results"]}
        flags = {}
        for method in self.params["methods"]:
            if method not in have:
                raise GreeksRefusal(self.NOT_CLOSED, f"result.json holds no method {method!r}")
            shock = declared.get(method, {}).get("shock")
            if shock is None:
                flags[method] = self.FLAG_CONFIG
            elif shock.get("beta_lookback") != self.params["beta_lookback"]:
                raise GreeksRefusal(self.BETA_MISMATCH, f"{method}: shock {shock.get('beta_lookback')!r}"
                                                        f", config {self.params['beta_lookback']!r}")
            else:
                flags[method] = self.FLAG_MATCHES
        return flags

    def _read_dividends(self, block, symbols):
        """Return ``{symbol: rows}`` of the bar files that carry ``dividend_amount``.

        Rows are ``date, close, dividend_amount``, read below the seal from the pinned files
        (sha256 verified).

        Sets :attr:`hedge_dividend_source` to ``absent``, ``bars`` or ``partial``.
        """
        import pyarrow.parquet as pq

        source = PinnedPayloadSource(block["pin"])
        found = {}
        for symbol in sorted(symbols):
            if symbol not in block["files"]:
                raise GreeksRefusal(self.SYMBOLS_MISSING, f"no bar file for {symbol}")
            path = source.file(block["files"][symbol], verify=True)
            if "dividend_amount" in pq.read_schema(path).names:
                found[symbol] = pq.read_table(
                    path, columns=["date", "close", "dividend_amount"],
                    filters=[("date", "<", self._seal())]).to_pylist()
        self.hedge_dividend_source = ("absent" if not found else
                                      "bars" if len(found) == len(set(symbols)) else "partial")
        return found

    def _hedge_dividend(self, book):
        """Return ``(cash per share in (fill, expiry], hedge_status)`` from the pinned bars.

        ``dividends_absent`` when the name's bar file has no ``dividend_amount`` column;
        ``dividend_missing`` when :func:`dividends_paid` refuses a session in the window.
        """
        rows = self._dividend_rows.get(book["symbol"])
        if rows is None:
            return None, self.DIVIDENDS_ABSENT
        try:
            return dividends_paid(rows, book["fill_date"], book["expiry"]), self.HEDGE_OK
        except ValueError:
            return None, self.DIVIDEND_MISSING

    def _read_bars(self, table, symbols):
        """Return ``(bars, shas)`` read below the seal through the one existing bar reader."""
        reader = MioBookOutcomes("bars", {"cohort": "greeks", "methods": ["_"], "end_before": self._seal(),
                                          "tolerance": PNL_GATE_USD})
        try:
            bars, audit = reader._bars(table["plan"]["source_pins"]["bars"], set(symbols))
        except KeyError as exc:
            raise GreeksRefusal(self.SYMBOLS_MISSING, f"no bar file for {exc}") from None
        return bars, {f"bars/{s}": a["sha256"] for s, a in audit.items()}

    # -- the run -----------------------------------------------------------

    def run(self, ctx, inputs):
        """Read the closed run, report per method and write the four files.

        Parameters
        ----------
        ctx : NodeContext
            Passed to the core.
        inputs : dict
            Unused: the inputs are the files under ``run_dir``.

        Returns
        -------
        dict
            ``books``, ``cells``, ``exposure`` (rows led by ``method``) and ``summary``.

        Raises
        ------
        GreeksRefusal
            With one of the module docstring's codes.
        """
        p = self.params
        run_dir, out_dir = Path(p["run_dir"]), Path(p["out_dir"])
        if self._inside(run_dir, out_dir):
            raise GreeksRefusal(self.OUT_DIR_INSIDE, f"{out_dir} is inside {run_dir}")
        table, shas = self._read_inputs(run_dir)
        domain, symbols, multiplier = self._domain(table)
        flags = self._method_flags(domain, table)
        crossing, trades = self._census(table)      # dates only: no P&L is used before it
        kept, excluded = self._apply_crossing(crossing, trades)
        if p["dividends"] == "required":            # before any P&L field is touched
            self._dividend_census(table["plan"]["source_pins"]["bars"],
                                  {t["symbol"] for rows in kept.values() for t in rows})
        bars, bar_shas = self._read_bars(table, symbols)
        self._dividend_rows = self._read_dividends(table["plan"]["source_pins"]["bars"], symbols)
        quotes = ChainQuotes(table["plan"]["source_pins"]["chain"], self._seal(),
                             p["chain_cache_days"])
        merged = {"books": [], "cells": [], "exposure": []}
        summaries = {}
        for method in p["methods"]:
            raw = {"method": method, "trades": kept[method], "bars": bars, "multiplier": multiplier}
            out = super().run(ctx, {"books": raw, "quotes": quotes, "bars": bars})
            out["cells"] = [c for c in out["cells"] if c["rank"] is not None]
            ranked = [r for r in out["books"] if "rank_reason" in r]
            out["summary"]["rank_reasons"] = dict(sorted(Counter(r["rank_reason"] for r in ranked).items()))
            out["summary"]["rank_null_pnl_usd"] = self._null_rank_pnl(ranked)
            for name in merged:
                merged[name] += [{"method": method, "quarter": p["quarter"], **row}
                                 if "method" not in row else {"quarter": p["quarter"], **row}
                                 for row in out[name]]
            out["summary"]["n_books_total"] = len(trades[method])
            out["summary"]["n_books_excluded"] = len(trades[method]) - len(kept[method])
            out["summary"]["direction_read"] = self._direction_read(method, out["books"], out["summary"])
            summaries[method] = out["summary"]
        summary = self._run_summary(summaries, flags, {**shas, **bar_shas, **{
            f"chain/{Path(f).stem}": d for f, d in quotes.files.items()}})
        summary.update(self._a2_summary(kept, excluded, trades))
        result = {**merged, "summary": summary}
        self._write(out_dir, result)
        return result

    def _direction_read(self, method, rows, summary):
        """Return the method x quarter block ADR-0312 reads (ADR-0310 A2.1.3).

        The core's :meth:`~dskit.pipeline.option_exposure.BookGreeksReport.direction_read` over
        the method's book rows, led by ``quarter`` and ``method``; its ``n_books_total`` is the
        method's requested books BEFORE any seal-crossing exclusion, beside ``n_books_excluded``.
        """
        read = self.direction_read(rows)
        read.update(n_books_total=summary["n_books_total"], n_books_excluded=summary["n_books_excluded"])
        return {"quarter": self.params["quarter"], "method": method, **read}

    @staticmethod
    def _unequal_by_exclusion(kept, trades):
        """Say whether the exclusion left the methods with different book sets (ADR-0310 A2.6).

        True when some book is kept by one method and absent from another's kept set although the
        other had requested it: a difference in the picks themselves is not an exclusion effect.
        """
        have = {m: {t["id"] for t in rows} for m, rows in kept.items()}
        asked = {m: {t["id"] for t in rows} for m, rows in trades.items()}
        everyone = set().union(*have.values()) if have else set()
        return any(i not in have[m] and i in asked[m] for m in have for i in everyone)

    def _a2_summary(self, kept, excluded, trades):
        """Return the run-level A2 keys: quarter, dividends mode, seal crossing and shared books."""
        return {"quarter": self.params["quarter"], "dividends": self.params["dividends"],
                "seal_crossing": self.params["seal_crossing"],
                "seal_crossing_excluded": excluded, "unequal_method_sets": bool(excluded) and self._unequal_by_exclusion(kept, trades),
                "total_excludes_seal_crossing": bool(excluded),
                "formulations_share_books": True, "methods_compared": list(self.params["methods"]),
                "shared_book_count": self._shared_book_count(kept)}

    @staticmethod
    def _null_rank_pnl(rows):
        """Return ``{rank_reason: P&L}`` of the attributed books with no rank (not in any cell)."""
        by_reason = {}
        for r in rows:
            if r["rank"] is None:
                by_reason.setdefault(r["rank_reason"], []).append(r["pnl"])
        return {k: math.fsum(v) for k, v in sorted(by_reason.items())}

    def _run_summary(self, summaries, flags, shas):
        """Return the run-level summary: inputs, params hash, per-method counts and totals."""
        identity = {k: v for k, v in self.params.items() if k not in _NON_IDENTITY}
        hedged = [s["hedged_pnl_usd"] for s in summaries.values() if s["hedged_pnl_usd"] is not None]
        return {
            "inputs": dict(sorted(shas.items())),
            "params_hash": hashlib.sha256(json.dumps(
                identity, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
            "window": self.params["window"], "sealed_from": self.params["sealed_from"],
            "rate": self.params["rate"], "iv_source": self.params.get("iv_source", "vendor"),
            "beta_lookback_flags": flags, "methods": summaries,
            "rank_reasons": dict(sorted(sum((Counter(s["rank_reasons"])
                                            for s in summaries.values()), Counter()).items())),
            "rank_null_pnl_usd": {k: math.fsum(s["rank_null_pnl_usd"].get(k, 0.0)
                                               for s in summaries.values())
                                  for k in sorted({k for s in summaries.values()
                                                   for k in s["rank_null_pnl_usd"]})},
            "refusals": dict(sorted(sum((Counter(s["refusals"]) for s in summaries.values()),
                                        Counter()).items())),
            "forward_spot": sum(s["forward_spot"] for s in summaries.values()),
            "hedged_pnl_usd": math.fsum(hedged) if hedged else None,
            "n_stock_hedged": sum(s["n_stock_hedged"] for s in summaries.values()),
            "hedge_cost_source": self.params["hedge_cost_source"],
            "stock_hedge_note": STOCK_HEDGE_NOTE,
            "hedge_status": dict(sorted(sum((Counter(s["hedge_status"]) for s in summaries.values()),
                                            Counter()).items())),
            "htb_unreadable": sum(s["htb_unreadable"] for s in summaries.values()),
            "forward_hedge_status": dict(sorted(sum(
                (Counter(s["forward_hedge_status"]) for s in summaries.values()), Counter()).items())),
            "decision_greeks_refusals": dict(sorted(sum(
                (Counter(s["decision_greeks_refusals"]) for s in summaries.values()), Counter()).items())),
            "hedge_dividend_source": self.hedge_dividend_source,
            "hedge_params": {k: self.params[k] for k in StaticDeltaHedge._PARAMS},
            "open_leg_unmarked": sum(s["open_leg_unmarked"] for s in summaries.values()),
            "iv_disagree_legs": sum(s["iv_disagree_legs"] for s in summaries.values()),
            "beta_dropped_name_days": sum(s["beta_dropped_name_days"] for s in summaries.values()),
        }

    @staticmethod
    def _csv(rows, declared):
        """Return rows as CSV bytes: columns first-seen (``declared`` when no row), ``None`` empty."""
        columns = list(dict.fromkeys(k for row in rows for k in row)) or list(declared)
        stream = io.StringIO()
        writer = csv.writer(stream, lineterminator="\n")
        writer.writerow(columns)
        for row in rows:
            writer.writerow(["" if row.get(c) is None else
                             json.dumps(row[c], sort_keys=True) if isinstance(row[c], (dict, list))
                             else repr(row[c]) if isinstance(row[c], float) else row[c]
                             for c in columns])
        return stream.getvalue().encode("utf-8")

    def _declared(self):
        """Return the header each CSV has when it has no row."""
        cells = [*self.params["cell_by"], *BookGreeksReport.CELL_COLUMNS]
        books = ["method", "quarter", *BookGreeksReport.BOOK_COLUMNS, "rank", "cadence", "rank_reason"]
        return (books, ["method", "quarter", *cells],
                ["method", "quarter", *BookGreeksReport.EXPOSURE_COLUMNS])

    def _write(self, out_dir, result):
        """Serialize all four outputs in memory, then land them under ``out_dir``, all or nothing.

        Each file lands atomically; the old summary is removed first and the new one written
        last, so a kill between files leaves no summary beside CSVs of another run.
        """
        payloads = {name: self._csv(rows, columns) for name, rows, columns in zip(
            OUTPUT_FILES, (result["books"], result["cells"], result["exposure"]),
            self._declared())}
        payloads[OUTPUT_FILES[3]] = (json.dumps(
            result["summary"], indent=2, sort_keys=True, allow_nan=False) + "\n").encode("utf-8")
        out_dir.mkdir(parents=True, exist_ok=True)
        summary = OUTPUT_FILES[3]
        (out_dir / summary).unlink(missing_ok=True)   # the summary vouches for its CSVs: none, until all land
        for name in (*OUTPUT_FILES[:3], summary):
            atomic_write(str(out_dir / name), payloads[name])
