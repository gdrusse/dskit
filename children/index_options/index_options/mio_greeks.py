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

Refusals carry stable codes (:class:`GreeksRefusal`): ``window_outside_data_rights``,
``out_dir_inside_run_dir``, ``input_not_closed``, ``pnl_recompute_mismatch``,
``beta_lookback_mismatch``, ``symbols_missing`` and ``multiplier_mismatch`` (the
config's ``multiplier`` must equal the run's own solver multiplier). The per-book
and per-day codes are the core's. Import cost: stdlib, plus pyarrow when a chain or
bar file is read.
"""

import csv
import hashlib
import io
import json
import os
from collections import Counter
from datetime import date
from pathlib import Path

from dskit.onboarding.artifacts import PinnedPayloadSource
from dskit.pipeline.node import atomic_write
from dskit.pipeline.option_exposure import BookGreeksReport
from dskit.pipeline.option_structures import structure_payoff

from .mio_run_events import MioBookOutcomes

__all__ = ["ChainQuotes", "CellRank", "GreeksRefusal", "MIOGreeksDiagnostics"]

#: Path A0003: the holdout opens on this session; no seal may lie beyond it.
PROTECTED_FROM = "2024-01-02"
#: The ADR's exact-match gate between a saved P&L and its recomputation, in dollars.
PNL_GATE_USD = 0.005
#: A0006: a weekly name's rank is its 7-day window, 1..8; a monthly-only name's, its position 1..3.
WEEKLY_WINDOW_DAYS, WEEKLY_MAX_RANK, MONTHLY_MAX_POSITION = 7, 8, 3
#: A name is weekly when at least this many expiries are listed within this many days.
WEEKLY_PROBE_COUNT, WEEKLY_PROBE_DAYS = 2, 14
#: The saved files read (``run.log`` is deliberately absent).
INPUT_FILES = ("result.json", "trades.csv", "daily-account.csv")
#: The files written, under ``out_dir``.
OUTPUT_FILES = ("books.csv", "cells.csv", "exposure.csv", "greeks-summary.json")
#: Chain columns read when present (``iv`` may be absent: every leg is then ``leg_no_iv``).
CHAIN_COLUMNS = ("date", "expiration", "type", "strike_raw", "bid", "ask", "iv")
_PLACEMENT = ("run_dir", "out_dir")


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
    """The A0006 expiry rank of a book, from the expiries listed on its decision day.

    A name is weekly when at least :data:`WEEKLY_PROBE_COUNT` listed expiries lie within
    :data:`WEEKLY_PROBE_DAYS` days of the decision; its rank is the 7-day window holding
    the book's expiry (1-7 days -> 1, 8-14 -> 2, ...), ``None`` past
    :data:`WEEKLY_MAX_RANK`. Any other name is monthly-only; its rank is the expiry's
    position among the listed ones (``None`` past :data:`MONTHLY_MAX_POSITION`). A
    Thursday holiday expiry keeps the window it falls in.

    Examples
    --------
    A weekly name and a monthly-only name::

        CellRank().of("2021-04-05", "2021-04-16", ["2021-04-09", "2021-04-16"])
        # -> ("weekly", 2)
        CellRank().of("2021-04-05", "2021-05-21", ["2021-04-16", "2021-05-21"])
        # -> ("monthly", 2)
    """

    def of(self, decision_date, expiry, listed):
        """Return ``(cadence, rank)``; ``rank`` is ``None`` outside the A0006 range.

        Parameters
        ----------
        decision_date, expiry : str
            ISO dates.
        listed : list of str
            The expiries listed on the decision day.

        Returns
        -------
        tuple
            ``("weekly" | "monthly", int or None)``.
        """
        start = date.fromisoformat(decision_date)
        dte = (date.fromisoformat(expiry) - start).days
        ahead = sorted((date.fromisoformat(e) - start).days for e in listed)
        ahead = [d for d in ahead if d >= 1]
        if sum(d <= WEEKLY_PROBE_DAYS for d in ahead) >= WEEKLY_PROBE_COUNT:
            rank = (dte - 1) // WEEKLY_WINDOW_DAYS + 1 if dte >= 1 else None
            return "weekly", rank if rank is not None and rank <= WEEKLY_MAX_RANK else None
        position = ahead.index(dte) + 1 if dte in ahead else None
        return "monthly", position if position is not None and position <= MONTHLY_MAX_POSITION else None


class ChainQuotes:
    """Read-only per-day view of the pinned chain parquet, never at or past ``end_before``.

    Parameters
    ----------
    binding : dict
        The plan's ``source_pins.chain``: ``pin`` and ``files`` (symbol -> file).
    end_before : str
        ISO date; a day on or after it refuses ``window_outside_data_rights``.

    Examples
    --------
    One expiry's rows for a name on a day::

        quotes = ChainQuotes(binding, "2021-07-01")
        rows = quotes.chain("BLK", "2021-04-06", "2021-04-16")   # right, strike, bid, ask, iv
    """

    def __init__(self, binding, end_before):
        self.binding, self.end_before = binding, end_before
        self._source = PinnedPayloadSource(binding["pin"])
        self._paths, self._days = {}, {}
        #: Relative file -> its pinned sha256, for every file opened.
        self.files = {}

    def _day(self, symbol, day):
        """Return ``{expiry: rows}`` for one name and session, read once."""
        if day >= self.end_before:
            raise GreeksRefusal(MIOGreeksDiagnostics.WINDOW,
                                f"chain day {day} is on or after {self.end_before}")
        if (symbol, day) not in self._days:
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
        return self._days[symbol, day]

    def chain(self, symbol, day, expiry):
        """Return the rows of one expiry on ``day`` (empty when none are saved)."""
        return self._day(symbol, day).get(expiry, [])

    def expiries(self, symbol, day):
        """Return the sorted expiries listed for ``symbol`` on ``day``."""
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
        must lie before it, and it may not exceed Path A0003's protected start).
        ``beta_lookback`` must equal a method's ``shock.beta_lookback`` when it
        declares one; ``multiplier`` must equal the run's solver multiplier.

    Examples
    --------
    Diagnose one saved run::

        node = MIOGreeksDiagnostics("greeks", params)    # see configs/mio-greeks-template.json
        out = node.run(None, {})                         # books, cells, exposure, summary
    """

    _CHILD_PARAMS = ("run_dir", "out_dir", "methods", "window", "sealed_from")
    _PARAMS = BookGreeksReport._PARAMS + _CHILD_PARAMS
    WINDOW = "window_outside_data_rights"
    OUT_DIR_INSIDE = "out_dir_inside_run_dir"
    NOT_CLOSED = "input_not_closed"
    PNL_MISMATCH = "pnl_recompute_mismatch"
    BETA_MISMATCH = "beta_lookback_mismatch"
    SYMBOLS_MISSING = "symbols_missing"
    MULTIPLIER_MISMATCH = "multiplier_mismatch"
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
        problems += cls._window_problems(params)
        missing = [n for n in cls._CHILD_PARAMS if n not in params]
        if missing:
            problems.append(f"missing required param(s) {missing}")
        return problems

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
        if abs(book["pnl"] - expected) >= PNL_GATE_USD:
            raise GreeksRefusal(self.PNL_MISMATCH, f"{book['key']}: saved pnl {book['pnl']!r}, "
                                                    f"credit + payoff gives {expected!r}")

    def _load_quotes(self, raw):
        """Return the :class:`ChainQuotes` view itself."""
        return raw

    def _cell_key(self, book, quotes):
        """Return ``symbol``, the A0006 ``rank`` and its ``cadence``."""
        cadence, rank = CellRank().of(book["decision_date"], book["expiry"],
                                      quotes.expiries(book["symbol"], book["decision_date"]))
        return {"symbol": book["symbol"], "rank": rank, "cadence": cadence}

    def _seal(self):
        """Return the effective first sealed date."""
        return min(self.params["sealed_from"], PROTECTED_FROM)

    def _guard_window(self, books):
        """Refuse a book dated on or after the seal or outside ``window``."""
        window, seal = self.params["window"], self._seal()
        for book in books:
            for field in ("decision_date", "fill_date", "expiry"):
                day = book[field]
                if day >= seal or not window["start"] <= day <= window["end"]:
                    raise GreeksRefusal(self.WINDOW, f"{book['key']} {field} {day} is outside "
                                                     f"[{window['start']}, {window['end']}] or on or "
                                                     f"after {seal}")

    # -- the saved run -----------------------------------------------------

    def _read_inputs(self, run_dir):
        """Return ``(table, shas)`` of a closed run; refuse ``input_not_closed`` otherwise."""
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
        listed = {(r["method"], r["id"]) for r in csv.DictReader(io.StringIO(texts["trades.csv"]))}
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
        bars, bar_shas = self._read_bars(table, symbols)
        quotes = ChainQuotes(table["plan"]["source_pins"]["chain"], self._seal())
        merged = {"books": [], "cells": [], "exposure": []}
        summaries = {}
        for method in p["methods"]:
            trades = next(r for r in table["results"] if r["id"] == method)["result"]["trades"]
            raw = {"method": method, "trades": trades, "bars": bars, "multiplier": multiplier}
            out = super().run(ctx, {"books": raw, "quotes": quotes, "bars": bars})
            for name in merged:
                merged[name] += [{"method": method, **row} if "method" not in row else row
                                 for row in out[name]]
            summaries[method] = out["summary"]
        summary = self._run_summary(summaries, flags, {**shas, **bar_shas, **{
            f"chain/{Path(f).stem}": d for f, d in quotes.files.items()}})
        result = {**merged, "summary": summary}
        self._write(out_dir, result)
        return result

    def _run_summary(self, summaries, flags, shas):
        """Return the run-level summary: inputs, params hash, per-method counts and totals."""
        identity = {k: v for k, v in self.params.items() if k not in _PLACEMENT}
        return {
            "inputs": dict(sorted(shas.items())),
            "params_hash": hashlib.sha256(json.dumps(
                identity, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
            "window": self.params["window"], "sealed_from": self.params["sealed_from"],
            "rate": self.params["rate"], "iv_source": self.params.get("iv_source", "vendor"),
            "beta_lookback_flags": flags, "methods": summaries,
            "refusals": dict(sorted(sum((Counter(s["refusals"]) for s in summaries.values()),
                                        Counter()).items())),
            "forward_spot": sum(s["forward_spot"] for s in summaries.values()),
            "open_leg_unmarked": sum(s["open_leg_unmarked"] for s in summaries.values()),
        }

    @staticmethod
    def _csv(rows):
        """Return rows as CSV bytes: columns in first-seen order, ``None`` empty, nesting as JSON."""
        columns = list(dict.fromkeys(k for row in rows for k in row))
        stream = io.StringIO()
        writer = csv.writer(stream, lineterminator="\n")
        writer.writerow(columns)
        for row in rows:
            writer.writerow(["" if row.get(c) is None else
                             json.dumps(row[c], sort_keys=True) if isinstance(row[c], (dict, list))
                             else repr(row[c]) if isinstance(row[c], float) else row[c]
                             for c in columns])
        return stream.getvalue().encode("utf-8")

    def _write(self, out_dir, result):
        """Land the four output files atomically under ``out_dir``."""
        out_dir.mkdir(parents=True, exist_ok=True)
        for name, rows in zip(OUTPUT_FILES, (result["books"], result["cells"], result["exposure"])):
            atomic_write(str(out_dir / name), self._csv(rows))
        atomic_write(str(out_dir / OUTPUT_FILES[3]), (json.dumps(
            result["summary"], indent=2, sort_keys=True, allow_nan=False) + "\n").encode("utf-8"))
