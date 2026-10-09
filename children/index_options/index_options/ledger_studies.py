"""Read-only studies over the ledgers of finished walk-forward runs (ADR-0196, ADR-0197).

A study reads what a walk-forward already recorded and writes nothing, like
``python -m dskit.pipeline skill``. Each walk summary directory lists its fold run
directories (:func:`~dskit.pipeline.runs.walk_fold_dirs`); a fold's backtest node
record (``<run dir>/nodes/NN-backtest.json``) names its ``report`` as a JSON artifact
(:func:`~dskit.pipeline.driver.resolve_json_artifact` checks its size and digest), and
the report's ledger holds one entry per entry date. Two commands ship over walks::

    python -m index_options.ledger_studies allocate <walk dir> <walk dir> [...] [--book model]
    python -m index_options.ledger_studies hedge <core walk dir> <sleeve walk dir> [--book model]

and a third, ``account`` (ADR-0256), over ONE completed robust-replay run directory (see
:class:`AccountStudy`; it needs ``--max-concurrent``, ``--max-per-symbol``, ``--max-per-date`` and
``--max-reserved-fraction``).

Both take the chosen book's ENTERED cells of each walk (``--book``: ``model``, the default,
or ``always``; the ``implied`` book trades other strikes than the model's expectation was
computed for), with their date, ``pnl_usd``, ``credit_usd`` (negative for a debit structure),
``strikes``, the structure's legs and the entry's ``model_expected_pnl_usd``. The structure's
legs are the report's own: the fixed structure's (:attr:`CondorQuoteBacktest.LEGS` and its
subclasses') or, for the payoff selector, the cell's ``selected.structure`` looked up in
:data:`~index_options.contracts.STRUCTURES`. Nothing here restates a structure or a loss rule:
the most a structure can lose is :func:`~index_options.contracts.structure_max_loss`'s.

Every study refuses, with one ``error: ...`` line and exit code 1, a walk that is not fully
finished (its summary or any fold whose ``state`` is not ``ran``: a short walk would pass as a
long one), a report that is not an archived-quote backtest, a bucket that differs
(``dte_min`` / ``dte_max``: other buckets have other cadences) and any ledger cell it cannot
use. A malformed command line exits 2. Everything printed is never decision-eligible: the
backtests' fills are the end-of-day touch.

**allocate** asks where to sell, SPY or QQQ, on the dates both could trade. Give it one walk
summary directory per underlying (two or more, each a different instrument). The ex-ante score
of a cell is ``model_expected_pnl_usd / max_loss``: the expectation the model had BEFORE the
outcome, per unit of the money the structure can lose. Every series is compared over the
SHARED dates, those on which EVERY walk entered the book: the ``allocate`` series takes, on each,
the cell with the highest score (an exact tie goes to the earlier command-line argument) and its
``pnl_usd``, the ``equal`` series the mean ``pnl_usd`` of the walks, and each underlying
``alone`` is its own cells on those dates. The output is three blocks: ``n_shared`` and that
table; ``walks``, each one's instrument, entries, first and last entry date and fold count; and,
labelled as NOT comparable with the first, the same rows over the UNION of the walks' entry dates
(ADR-0196's original table, in which a series may cover dates another lacks).

**hedge** asks whether a bought-convexity SLEEVE earns its place beside a CORE book, at the same
tail risk. Give it the core walk (a condor, say) and the sleeve walk (a long straddle or a long
spread), the same instrument and bucket. The two ledgers are joined on entry date; a date either
lacks is counted and printed, never joined. On the joined dates ``core+sleeve`` is the sum of the
two P&Ls, and ``k = CVaR5(core+sleeve) / CVaR5(core)`` is the scale at which a SMALLER core has
the combined book's tail: ``k*core`` has the same CVaR5 by construction. The sleeve earns its
place only if the mean of ``core+sleeve`` exceeds that of ``k*core``, both at the printed cent
(two means that print alike are a tie: a sleeve proportional to the core adds nothing, and its
float noise must not read as an edge): the verdict line says which. A ``k`` above 1 means the
sleeve made the tail worse, so the matched core is LARGER than one contract: a ``note:`` line under
``k`` says so. The study refuses, for want of a tail to match, a core whose CVaR5 is not negative and
a combined book whose CVaR5 is not (k would not be a positive scale).

The table has one row per series: ``n``, the mean P&L, its Newey-West ``t`` (lags 0: the
backtests' own, so a float-noise-constant series reads 0.0), the lower-tail mean at the
backtests' default tail (``cvar5_usd``) and the maximum drawdown, each from the same owner the
backtests' metrics use.
"""

import argparse
import json
import math
import os
import sys
from abc import ABC, abstractmethod
from collections import Counter
from datetime import date
from fractions import Fraction
from typing import NamedTuple

from dskit.pipeline.driver import resolve_json_artifact
from dskit.pipeline.records import number_ok
from dskit.pipeline.runs import NODES_DIR, RESULT_FILE, WALKFORWARD_FILE, walk_fold_dirs
from dskit.pipeline.stats import lower_tail_mean, max_drawdown

from .contracts import CONDOR_LEGS, STRUCTURES, structure_max_loss
from .nodes import (
    CondorQuoteBacktest,
    LongCallSpreadQuoteBacktest,
    LongPutSpreadQuoteBacktest,
    LongStraddleQuoteBacktest,
    PayoffSelectQuoteBacktest,
    PutSpreadQuoteBacktest,
    mean_or_zero,
    t_or_zero,
)

__all__ = ["ACCOUNT_COLUMNS", "BACKTEST_NODE", "CAPITAL_LEVELS", "COLUMNS", "CVAR_ALPHA",
           "REJECTION_REASONS", "STUDIES", "AccountRow", "AccountStudy", "AdmissionCaps",
           "AllocationStudy", "EnteredCell", "HedgeStudy", "LedgerStudy", "Order", "ReplayRun",
           "WalkLedger", "main"]

#: The key the shipped grid documents give their backtest node.
BACKTEST_NODE = "backtest"
#: The tail level of the printed lower-tail mean: the backtests' own default.
CVAR_ALPHA = CondorQuoteBacktest.DEFAULTS["cvar_alpha"]
#: The tail as a percentage of the series (5 for alpha 0.95), named in the column and the messages.
_TAIL_PERCENT = round(100 * (1 - CVAR_ALPHA))
#: The printed table's columns; the tail column is named for the share of the tail.
COLUMNS = ("series", "n", "mean_pnl_usd", "t", f"cvar{_TAIL_PERCENT}_usd", "max_drawdown_usd")
#: The decimals every printed money figure carries (the cent): the verdict compares at them.
_MONEY_DECIMALS = 2
#: The note printed under ``k`` when the matched core is larger than one contract.
_K_ABOVE_ONE = ("note: k > 1: the matched core is LARGER than one contract (the sleeve worsened "
                "the tail), so core+sleeve is compared with MORE than the core itself")
#: The state a finished walk, and each of its folds, records in ``walkforward.json``.
_RAN = "ran"
#: Report kind -> the legs of its one structure; the selector's kind has none (its cells say).
_FIXED_LEGS = {cls.REPORT_KIND: cls.LEGS for cls in (
    CondorQuoteBacktest, PutSpreadQuoteBacktest, LongStraddleQuoteBacktest,
    LongCallSpreadQuoteBacktest, LongPutSpreadQuoteBacktest)}
_QUOTE_KINDS = (*_FIXED_LEGS, PayoffSelectQuoteBacktest.REPORT_KIND)


class EnteredCell(NamedTuple):
    """One traded cell of one book, with what its ex-ante score needs.

    Parameters
    ----------
    date : str
        The entry date, ISO.
    pnl_usd : float
        The cell's realized P&L.
    credit_usd : float
        The cash received at entry, net of fees; negative for a debit structure.
    strikes : tuple of float
        One strike per leg, in leg order.
    legs : tuple
        ``(right, signed quantity)`` per leg.
    multiplier : int
        The report's contract multiplier.
    expected_pnl_usd : float
        The model's expected P&L for this entry.

    Examples
    --------
    A put credit spread, 92 / 95, credit 60, expected 60::

        cell = EnteredCell("2024-03-01", 60.0, 60.0, (92.0, 95.0), PUT_SPREAD_LEGS, 100, 60.0)
        cell.score
        # -> 0.25
    """

    date: str
    pnl_usd: float
    credit_usd: float
    strikes: tuple
    legs: tuple
    multiplier: int
    expected_pnl_usd: float

    @property
    def max_loss_usd(self):
        """The most the structure can lose, :func:`~index_options.contracts.structure_max_loss`'s."""
        return structure_max_loss(self.legs, self.strikes, self.credit_usd, self.multiplier)

    @property
    def score(self):
        """The ex-ante score: the expected P&L per unit of the money at risk."""
        return self.expected_pnl_usd / self.max_loss_usd


class WalkLedger:
    """The backtest ledgers of one walk-forward summary directory, read back and checked.

    Parameters
    ----------
    summary_dir : str
        A walk-forward summary directory (the one holding ``walkforward.json``).

    Raises
    ------
    ValueError
        With one line, when the directory is not a walk, its summary or a fold did not finish
        ``ran`` (or a fold lists no run directory), it lists no fold that ran, a fold has no
        readable backtest record or report artifact, a report is not an archived-quote
        backtest or lacks its params or ledger, a ledger entry is not an object, its folds
        disagree on the bucket, or it holds more than one instrument.

    Examples
    --------
    The model book's traded cells of one walk::

        ledger = WalkLedger(summary_dir)
        ledger.bucket, ledger.label, ledger.n_folds
        # -> ((7, 10), "SPY", 2)
        cells = ledger.cells("model")
    """

    def __init__(self, summary_dir):
        self.summary_dir = os.fspath(summary_dir)
        run_dirs = walk_fold_dirs(self.summary_dir)
        self._require_ran()
        if not run_dirs:
            raise ValueError(f"{self.summary_dir} lists no fold that ran")
        self._reports = [(run_dir, self._checked(run_dir, self._load(run_dir)))
                         for run_dir in run_dirs]
        #: How many folds the walk ran: all of them, for a walk that was not refused.
        self.n_folds = len(run_dirs)
        self.bucket = self._one_bucket()
        self.instrument = self._one_instrument()
        #: What the walk is called in a table: its instrument, else its directory.
        self.label = self.instrument or os.path.basename(self.summary_dir.rstrip(os.sep))

    def _require_ran(self):
        """Refuse a walk whose summary, or any fold row, is not a finished ``ran`` one with a run directory."""
        with open(os.path.join(self.summary_dir, WALKFORWARD_FILE), encoding="utf-8") as handle:
            record = json.load(handle)
        if record.get("state") != _RAN:
            raise ValueError(f"{self.summary_dir}: the walk's state is {record.get('state')!r}, "
                             f"not {_RAN!r}")
        for fold in record["folds"]:
            if not isinstance(fold, dict):
                raise ValueError(f"{self.summary_dir}: a fold row is not an object: {fold!r}")
            where = f"{self.summary_dir}: the fold for cutoff {fold.get('cutoff')!r}"
            if fold.get("state") != _RAN:
                raise ValueError(f"{where} is {fold.get('state')!r}, not {_RAN!r}: a short "
                                 "walk would pass as a long one")
            if not isinstance(fold.get("run_dir"), str):
                raise ValueError(f"{where} lists no run directory")

    @staticmethod
    def _load(run_dir):
        """Return one fold's backtest report, verified against its manifest."""
        nodes_dir = os.path.join(run_dir, NODES_DIR)
        suffix = f"-{BACKTEST_NODE}.json"
        names = sorted(n for n in (os.listdir(nodes_dir) if os.path.isdir(nodes_dir) else ())
                       if n.endswith(suffix))
        if len(names) != 1:
            raise ValueError(f"{run_dir}: expected one {NODES_DIR}/ record of the "
                             f"{BACKTEST_NODE!r} node, found {len(names)}")
        try:
            with open(os.path.join(nodes_dir, names[0]), encoding="utf-8") as handle:
                record = json.load(handle)
        except (OSError, ValueError):
            raise ValueError(f"{run_dir}: the {BACKTEST_NODE!r} node record is unreadable") from None
        outputs = record.get("outputs") if isinstance(record, dict) else None
        if (not isinstance(outputs, dict) or record.get("node") != BACKTEST_NODE
                or record.get("status") != "ok" or "report" not in outputs):
            raise ValueError(f"{run_dir}: the {BACKTEST_NODE!r} node record is not a finished "
                             "backtest with a 'report' output")
        try:
            return resolve_json_artifact(run_dir, outputs["report"])
        except ValueError as exc:
            raise ValueError(f"{run_dir}: the backtest report: {exc}") from None

    @staticmethod
    def _checked(run_dir, report):
        """Return ``report`` if it is an archived-quote backtest report with what a score needs."""
        kind = report.get("kind") if isinstance(report, dict) else None
        pricing = report.get("pricing") if isinstance(report, dict) else None
        if kind not in _QUOTE_KINDS or pricing != CondorQuoteBacktest.REPORT_PRICING:
            raise ValueError(f"{run_dir}: the backtest report is {kind!r} priced {pricing!r}, "
                             f"not an archived-quote backtest ({list(_QUOTE_KINDS)})")
        params = report.get("params")
        if (not isinstance(params, dict) or not isinstance(report.get("ledger"), list)
                or not all(number_ok(params.get(k)) for k in ("dte_min", "dte_max", "multiplier"))):
            raise ValueError(f"{run_dir}: the backtest report lacks dte_min, dte_max, "
                             "multiplier or its ledger")
        for n, entry in enumerate(report["ledger"], start=1):
            if not isinstance(entry, dict):
                raise ValueError(f"{run_dir}: ledger entry {n} is not an object: {entry!r}")
        return report

    def _one_bucket(self):
        """Return the ``(dte_min, dte_max)`` every fold declares, or refuse naming two that differ."""
        first_dir, first = self._reports[0]
        bucket = (first["params"]["dte_min"], first["params"]["dte_max"])
        for run_dir, report in self._reports[1:]:
            other = (report["params"]["dte_min"], report["params"]["dte_max"])
            if other != bucket:
                raise ValueError(f"{self.summary_dir}: its folds disagree on the bucket: "
                                 f"{_bucket_text(bucket)} in {first_dir} but "
                                 f"{_bucket_text(other)} in {run_dir}")
        return bucket

    def _entries(self):
        """Yield ``(report, entry)`` over every fold's ledger, in walk order."""
        for _run_dir, report in self._reports:
            for entry in report["ledger"]:
                yield report, entry

    def _one_instrument(self):
        """Return the one instrument the ledgers hold, ``None`` without an entry, or refuse several."""
        names = {str(entry.get("instrument")) for _report, entry in self._entries()}
        if len(names) > 1:
            raise ValueError(f"{self.summary_dir} holds more than one instrument "
                             f"({sorted(names)}): a study takes one walk per underlying")
        return next(iter(names), None)

    @staticmethod
    def _legs(report, cell, where):
        """Return the legs of the structure a cell traded."""
        if report["kind"] in _FIXED_LEGS:
            return _FIXED_LEGS[report["kind"]]
        selected = cell.get("selected")
        name = selected.get("structure") if isinstance(selected, dict) else None
        if name not in STRUCTURES:
            raise ValueError(f"{where}: the selector cell's selected.structure {name!r} is not "
                             f"one of {list(STRUCTURES)}")
        return STRUCTURES[name]

    def _cell(self, report, entry, cell):
        """Build one entered cell, refusing whatever its score cannot be formed from."""
        where = f"{self.summary_dir} entry {entry.get('date')!r}"
        legs = self._legs(report, cell, where)
        strikes = cell.get("strikes")
        if not isinstance(strikes, list) or len(strikes) != len(legs) or not all(
                number_ok(k) for k in strikes):
            raise ValueError(f"{where}: needs {len(legs)} numeric strikes, got {strikes!r}")
        for name, value in (("pnl_usd", cell.get("pnl_usd")),
                            ("credit_usd", cell.get("credit_usd")),
                            ("model_expected_pnl_usd", entry.get("model_expected_pnl_usd"))):
            if not number_ok(value):
                raise ValueError(f"{where}: {name} must be a finite number, got {value!r}")
        built = EnteredCell(entry["date"], cell["pnl_usd"], cell["credit_usd"], tuple(strikes),
                            legs, report["params"]["multiplier"], entry["model_expected_pnl_usd"])
        if not built.max_loss_usd > 0:
            raise ValueError(f"{where}: the max loss {built.max_loss_usd!r} is not positive, so "
                             "no score can be formed")
        return built

    def cells(self, book):
        """Return the ``book``'s entered cells, in date order.

        Parameters
        ----------
        book : str
            A book of the ledgers' cells: ``"model"`` or ``"always"``.

        Returns
        -------
        list of EnteredCell
            One per entry the book traded, oldest first.

        Raises
        ------
        ValueError
            When an entry has no date or book, a cell cannot be scored, or two traded cells
            share a date.
        """
        found = {}
        for report, entry in self._entries():
            books = entry.get("books")
            cell = books.get(book) if isinstance(books, dict) else None
            if not isinstance(entry.get("date"), str) or not isinstance(cell, dict):
                raise ValueError(f"{self.summary_dir}: a ledger entry has no date or no "
                                 f"{book!r} book cell")
            if cell.get("entered") is not True:
                continue
            if entry["date"] in found:
                raise ValueError(f"{self.summary_dir} repeats entry date {entry['date']}: "
                                 "one traded cell per date is what a study compares")
            found[entry["date"]] = self._cell(report, entry, cell)
        return [found[day] for day in sorted(found)]


def _bucket_text(bucket):
    """Render a ``(dte_min, dte_max)`` pair as ``7-10``."""
    return f"{bucket[0]}-{bucket[1]}"


class LedgerStudy(ABC):
    """A read-only study over finished walks: its arguments and its printed report.

    A member names its command (:attr:`name`), declares its arguments
    (:meth:`add_arguments`; :meth:`add_book_argument` is the shared ``--book``) and supplies
    its whole report (:meth:`report`). It prints each series through :meth:`series_table`,
    which uses the same owners of the mean, ``t``, lower-tail mean and drawdown the backtests
    report, so studies cannot disagree about a statistic.

    Examples
    --------
    A member that tabulates each walk's model book alone::

        class Alone(LedgerStudy):
            name = "alone"

            @classmethod
            def add_arguments(cls, parser):
                parser.add_argument("walks", nargs="+")

            def report(self, args):
                return self.series_table(
                    [(w, [c.pnl_usd for c in WalkLedger(w).cells("model")]) for w in args.walks])

        print(Alone().report(args))
    """

    #: The command that selects the study.
    name = ""
    #: One line for the command's ``--help``.
    summary = ""
    #: The books whose cells the model's expectation was computed for.
    BOOKS = CondorQuoteBacktest.FORECAST_BOOKS
    DEFAULT_BOOK = "model"

    @classmethod
    @abstractmethod
    def add_arguments(cls, parser):
        """Declare the study's command-line arguments on ``parser``."""

    @classmethod
    def add_book_argument(cls, parser):
        """Declare ``--book``: :attr:`BOOKS`, :attr:`DEFAULT_BOOK` by default.

        Parameters
        ----------
        parser : argparse.ArgumentParser
            The study's subparser.
        """
        parser.add_argument("--book", choices=cls.BOOKS, default=cls.DEFAULT_BOOK,
                            help=f"the book whose traded cells are read (default: "
                                 f"{cls.DEFAULT_BOOK})")

    @abstractmethod
    def report(self, args):
        """Return the study's whole printed output as text; refuse an input with ``ValueError``."""

    @staticmethod
    def _require_one_bucket(ledgers):
        """Refuse walks whose buckets differ, naming two of them: other buckets have other cadences."""
        first = ledgers[0]
        for ledger in ledgers[1:]:
            if ledger.bucket != first.bucket:
                raise ValueError(
                    f"mixed buckets: {_bucket_text(first.bucket)} in {first.summary_dir} but "
                    f"{_bucket_text(ledger.bucket)} in {ledger.summary_dir}")

    @staticmethod
    def _row(label, pnls):
        """Return one series' printed cells: its count and the four statistics of its P&L."""
        # the backtests' own mean and t (0.0 below two values or with no variance), tail and path
        cents = _MONEY_DECIMALS
        return [label, str(len(pnls)), f"{mean_or_zero(pnls):.{cents}f}", f"{t_or_zero(pnls):.3f}",
                f"{lower_tail_mean(pnls, CVAR_ALPHA) if pnls else 0.0:.{cents}f}",
                f"{max_drawdown(pnls):.{cents}f}"]

    @staticmethod
    def _grid(rows):
        """Return ``rows`` of text cells as aligned lines: the first column left-aligned, the rest right-aligned."""
        widths = [max(len(row[k]) for row in rows) for k in range(len(rows[0]))]
        return "\n".join("  ".join(cell.ljust(width) if k == 0 else cell.rjust(width)
                                   for k, (cell, width) in enumerate(zip(row, widths)))
                         for row in rows)

    @classmethod
    def series_table(cls, series):
        """Return one table: :data:`COLUMNS`, then a row per series.

        Parameters
        ----------
        series : list of tuple
            ``(label, pnls)``: each named P&L series, in date order.

        Returns
        -------
        str
            The table, first column left-aligned and the rest right-aligned.
        """
        return cls._grid([list(COLUMNS)] + [cls._row(label, pnls) for label, pnls in series])


class AllocationStudy(LedgerStudy):
    """Sell where the model expects most per unit at risk: SPY or QQQ, date by date.

    See the module docstring for the rule. One walk summary directory per underlying, all in
    one bucket; ``--book`` picks the model's (default) or the always book's traded cells. Every
    series is over the dates on which EVERY walk entered; the union of dates is a separate,
    labelled block.

    Examples
    --------
    Build the study and print its report, SPY first so it wins ties::

        study = AllocationStudy()
        args = argparse.Namespace(walks=["runs/spy-walk", "runs/qqq-walk"], book="model")
        print(study.report(args))
    """

    name = "allocate"
    summary = "allocate each shared entry date to the underlying with the higher ex-ante score"

    @classmethod
    def add_arguments(cls, parser):
        """Take two or more walk directories and the book to read.

        Parameters
        ----------
        parser : argparse.ArgumentParser
            The ``allocate`` subparser.
        """
        parser.add_argument("walks", nargs="+", metavar="WALK_DIR",
                            help="a walk-forward summary directory per underlying, in tie-break "
                                 "order (two or more, one bucket)")
        cls.add_book_argument(parser)

    @classmethod
    def _ledgers(cls, directories):
        """Load every walk and refuse a lone walk, mixed buckets or one instrument twice."""
        if len(directories) < 2:
            raise ValueError("allocate needs at least two walk directories, one per underlying")
        ledgers = [WalkLedger(directory) for directory in directories]
        cls._require_one_bucket(ledgers)
        seen = set()
        for ledger in (ledger for ledger in ledgers if ledger.instrument is not None):
            if ledger.instrument in seen:
                raise ValueError(f"two of these walks hold the same instrument "
                                 f"({ledger.instrument}): an allocation is across underlyings")
            seen.add(ledger.instrument)
        return ledgers

    @staticmethod
    def _series(ledgers, traded, dates):
        """Return the allocation, the equal split and each walk alone, over ``dates`` (those a walk entered)."""
        entered = [[walk[day] for walk in traded if day in walk] for day in dates]
        allocate = [max(cells, key=lambda cell: cell.score).pnl_usd for cells in entered]
        equal = [sum(cell.pnl_usd for cell in cells) / len(cells) for cells in entered]
        alone = [(f"alone:{ledger.label}", [walk[day].pnl_usd for day in dates if day in walk])
                 for ledger, walk in zip(ledgers, traded)]
        return [("allocate", allocate), ("equal", equal), *alone]

    @classmethod
    def _walk_block(cls, ledgers, traded):
        """Return the ``walks`` block: each walk's instrument, folds, entries and first and last entry date."""
        rows = [["instrument", "folds", "entries", "first_entry", "last_entry", "walk"]]
        for ledger, walk in zip(ledgers, traded):
            days = sorted(walk)
            rows.append([ledger.instrument or "-", str(ledger.n_folds), str(len(days)),
                         days[0] if days else "-", days[-1] if days else "-",
                         ledger.summary_dir])
        return "walks\n" + cls._grid(rows)

    def report(self, args):
        """Return the three blocks: the shared-dates table, the walks and the union table.

        Parameters
        ----------
        args : argparse.Namespace
            ``walks`` (directories) and ``book``.

        Returns
        -------
        str
            ``n_shared`` and ``allocate``, ``equal`` and one ``alone:<label>`` per walk over the
            shared dates; the ``walks`` table; the same series over the union of dates, labelled
            as not comparable with the first.

        Raises
        ------
        ValueError
            When a walk is refused (see :class:`WalkLedger`), the walks differ in bucket, hold
            one instrument twice, or fewer than two are given.
        """
        ledgers = self._ledgers(args.walks)
        traded = [{cell.date: cell for cell in ledger.cells(args.book)} for ledger in ledgers]
        shared = sorted(set.intersection(*(set(walk) for walk in traded)))
        union = sorted(set().union(*traded))
        return "\n\n".join([
            f"n_shared: {len(shared)} entry dates on which every walk entered the {args.book} "
            "book\n" + self.series_table(self._series(ledgers, traded, shared)),
            self._walk_block(ledgers, traded),
            "union of dates, NOT comparable with the table above (each series covers every date "
            "its walks entered)\n" + self.series_table(self._series(ledgers, traded, union))])


class HedgeStudy(LedgerStudy):
    """Test whether a bought-convexity sleeve beats a smaller core at the same tail risk.

    See the module docstring for the rule. The core walk and the sleeve walk are one
    instrument and one bucket; ``--book`` picks the model's (default) or the always book's
    traded cells of both. The verdict compares the two means at the printed cent, and a ``k``
    above 1 is flagged: the matched core is then larger than one contract.

    Examples
    --------
    Build the study and print its report for a condor core and a long-straddle sleeve::

        study = HedgeStudy()
        args = argparse.Namespace(core="runs/spy-condor", sleeve="runs/spy-straddle",
                                  book="model")
        print(study.report(args))
    """

    name = "hedge"
    summary = "does the sleeve beat a smaller core at the same CVaR5? core+sleeve against k*core"

    @classmethod
    def add_arguments(cls, parser):
        """Take the core walk, the sleeve walk and the book to read.

        Parameters
        ----------
        parser : argparse.ArgumentParser
            The ``hedge`` subparser.
        """
        parser.add_argument("core", metavar="CORE_WALK_DIR",
                            help="the walk-forward summary directory of the core book")
        parser.add_argument("sleeve", metavar="SLEEVE_WALK_DIR",
                            help="the walk-forward summary directory of the sleeve (same "
                                 "instrument and bucket)")
        cls.add_book_argument(parser)

    @classmethod
    def _pair(cls, core_dir, sleeve_dir):
        """Load the two walks and refuse a pair of different instruments or buckets."""
        core, sleeve = WalkLedger(core_dir), WalkLedger(sleeve_dir)
        if None not in (core.instrument, sleeve.instrument) and core.instrument != sleeve.instrument:
            raise ValueError(f"a hedge is one instrument: the core {core.summary_dir} is "
                             f"{core.instrument} but the sleeve {sleeve.summary_dir} is "
                             f"{sleeve.instrument}")
        cls._require_one_bucket([core, sleeve])
        return core, sleeve

    @staticmethod
    def _scale(core, combined):
        """Return ``k``, the tail of ``combined`` over the tail of ``core``; refuse where either has no tail loss to match."""
        core_tail = lower_tail_mean(core, CVAR_ALPHA)
        if not core_tail < 0:
            raise ValueError(f"the core has no tail loss to match on the joined dates: its "
                             f"CVaR{_TAIL_PERCENT} is {core_tail:.2f}, not negative")
        combined_tail = lower_tail_mean(combined, CVAR_ALPHA)
        if not combined_tail < 0:
            raise ValueError(f"core+sleeve has no tail loss on the joined dates: its "
                             f"CVaR{_TAIL_PERCENT} is {combined_tail:.2f}, not negative, so no "
                             "smaller core matches it (k would not be a positive scale)")
        return combined_tail / core_tail

    @staticmethod
    def _only(label, days):
        """Return the line naming the dates only ``label``'s walk entered."""
        return f"only in {label} ({len(days)}): {', '.join(days) if days else 'none'}"

    @staticmethod
    def _k_note(k):
        """Return the lines that follow ``k``: the note when the matched core is larger than one contract, else none."""
        return [_K_ABOVE_ONE] if k > 1 else []

    @staticmethod
    def _verdict(combined, scaled):
        """Return the verdict line: the sleeve earns its place only if core+sleeve's mean exceeds k*core's, both at the printed cent (a tie is float noise, never an edge)."""
        together = round(mean_or_zero(combined), _MONEY_DECIMALS)
        smaller = round(mean_or_zero(scaled), _MONEY_DECIMALS)
        shown = f"core+sleeve mean {together:.{_MONEY_DECIMALS}f}"
        against = f"k*core mean {smaller:.{_MONEY_DECIMALS}f}"
        if together > smaller:
            return f"verdict: {shown} exceeds {against}: the sleeve earns its place"
        return f"verdict: {shown} does not exceed {against}: the sleeve does not earn its place"

    def report(self, args):
        """Return the joined-date facts, the four-row table and the verdict line.

        Parameters
        ----------
        args : argparse.Namespace
            ``core`` and ``sleeve`` (directories) and ``book``.

        Returns
        -------
        str
            The facts (joined dates, the dates in only one walk, ``k``), the table of
            ``core+sleeve``, ``k*core``, ``core`` and ``sleeve`` over the joined dates, and
            the verdict, in three blocks.

        Raises
        ------
        ValueError
            When a walk is refused (see :class:`WalkLedger`), the walks differ in instrument
            or bucket, share no entry date, or the core or the combined book has no tail loss.
        """
        core, sleeve = self._pair(args.core, args.sleeve)
        core_cells = {cell.date: cell for cell in core.cells(args.book)}
        sleeve_cells = {cell.date: cell for cell in sleeve.cells(args.book)}
        joined = sorted(core_cells.keys() & sleeve_cells.keys())
        if not joined:
            raise ValueError(f"the two walks share no entry date on which both entered the "
                             f"{args.book} book")
        core_pnl = [core_cells[day].pnl_usd for day in joined]
        sleeve_pnl = [sleeve_cells[day].pnl_usd for day in joined]
        combined = [c + s for c, s in zip(core_pnl, sleeve_pnl)]
        k = self._scale(core_pnl, combined)
        scaled = [k * pnl for pnl in core_pnl]
        facts = "\n".join([
            f"joined: {len(joined)} entry dates on which both walks entered the {args.book} book",
            self._only("core", sorted(core_cells.keys() - sleeve_cells.keys())),
            self._only("sleeve", sorted(sleeve_cells.keys() - core_cells.keys())),
            f"k: {k:.4f} = CVaR{_TAIL_PERCENT}(core+sleeve) / CVaR{_TAIL_PERCENT}(core)",
            *self._k_note(k)])
        table = self.series_table([("core+sleeve", combined), ("k*core", scaled),
                                   ("core", core_pnl), ("sleeve", sleeve_pnl)])
        return "\n\n".join([facts, table, self._verdict(combined, scaled)])


#: The run-report artifact (under a run directory) whose ``stages`` hold each stage's evidence.
EVIDENCE_ARTIFACT = os.path.join("artifacts", "diagnostics", "evidence.json")
#: The report stage whose evidence lists the selector's decisions, and the one whose evidence lists
#: the settlement node's outcomes: the stage names the shipped replay config gives them.
SELECT_STAGE, SETTLE_STAGE = "sizing", "replay"
#: The arm whose unconstrained peak reservation sizes the capital levels.
NOMINAL_ARM = "nominal"
#: Why an order is refused, in the order the caps are checked: an order failing several is
#: counted once, under the first.
REJECTION_REASONS = ("max_concurrent", "max_per_symbol", "max_per_date", "max_reserved_fraction")
#: The capital levels, as ``(label, fraction)`` of the nominal arm's unconstrained peak reservation.
CAPITAL_LEVELS = (("1", Fraction(1)), ("1/2", Fraction(1, 2)), ("1/4", Fraction(1, 4)))
#: The level label of the row that admits every order: the single-trade view.
UNCAPPED = "uncapped"
#: The decision statuses that are orders (the others never placed one).
_ORDER_STATUSES = ("trade", "unfilled")
#: The printed account table's columns: the facts, then one column per rejection reason.
ACCOUNT_COLUMNS = ("arm", "level", "capital_usd", "admitted", "unfilled", "rejected", "pnl_usd",
                   "peak_reserved_usd", *REJECTION_REASONS)
#: The one line every account report ends with.
_ACCOUNT_NOTE = ("note: this is not a margin, mark-to-market or delivery model: admission uses "
                 "decision-time facts only, reservation is the decision max loss, and P&L is "
                 "cash-basis at settlement")


class AdmissionCaps(NamedTuple):
    """The frozen admission caps of the account study.

    Parameters
    ----------
    max_concurrent : int
        Most orders open at once.
    max_per_symbol : int
        Most open orders of one underlying.
    max_per_date : int
        Most orders admitted on one decision date.
    max_reserved_fraction : float
        The share of the capital level that open reservations may fill.

    Examples
    --------
    The caps the ADR-0256 account study was frozen with::

        AdmissionCaps(max_concurrent=10, max_per_symbol=1, max_per_date=3,
                      max_reserved_fraction=1.0)
    """

    max_concurrent: int
    max_per_symbol: int
    max_per_date: int
    max_reserved_fraction: float


class Order(NamedTuple):
    """One order the selector placed, with only what admission may know.

    Parameters
    ----------
    arm_id, decision_id, symbol : str
        The order's identity.
    date : str
        The decision date, ISO: admission happens after that day's close.
    release_date : str
        The date the reservation is released at the close: the outcome's saved settlement
        date, else (never settled) the selection's forecast settlement date.
    reservation : float
        The decision legs' maximum loss in USD, fees counted once.
    score : float
        The ex-ante robust value per unit of reservation: the priority.
    status : str
        ``trade`` or ``unfilled``: a fact learned AFTER the decision, never used to admit.
    pnl_usd : float or None
        The settled P&L, ``None`` for an order that never settled.

    Examples
    --------
    An order reserving 300 USD::

        Order("nominal", "d1", "AAA", "2025-02-04", "2025-03-07", 300.0, 0.1, "trade", 12.0)
    """

    arm_id: str
    decision_id: str
    symbol: str
    date: str
    release_date: str
    reservation: float
    score: float
    status: str
    pnl_usd: object


class AccountRow(NamedTuple):
    """One arm at one capital level: what was admitted, refused and earned.

    Parameters
    ----------
    arm_id, level : str
        The arm and the level label (:data:`CAPITAL_LEVELS`, or ``uncapped``).
    capital_usd : float or None
        The level's capital; ``None`` for ``uncapped``.
    admitted, unfilled : int
        Orders admitted, and how many of those never filled.
    rejected : dict
        Refusals by reason (:data:`REJECTION_REASONS`); absent reasons are zero.
    pnl_usd : float
        The cash-basis P&L of the admitted orders that settled.
    peak_reserved_usd : float
        The largest total reservation open at once.

    Examples
    --------
    A row that admitted two orders::

        AccountRow("nominal", "1", 900.0, 2, 0, {"max_per_date": 1}, 5.0, 600.0)
    """

    arm_id: str
    level: str
    capital_usd: object
    admitted: int
    unfilled: int
    rejected: dict
    pnl_usd: float
    peak_reserved_usd: float


def _iso(value):
    """Say whether ``value`` is a canonical ISO calendar date string."""
    try:
        return isinstance(value, str) and date.fromisoformat(value).isoformat() == value
    except ValueError:
        return False


class ReplayRun:
    """The orders and outcomes of one completed robust replay run directory, read back and checked.

    The selector's selections and the settlement node's outcomes are lists, which the driver
    records in a node record only as ``{"type": "list"}``; their content is in the run-report
    artifact (``artifacts/<report node>/evidence.json``, ``stages.<stage>``), which is what is read.

    Parameters
    ----------
    run_dir : str
        A run directory whose ``result.json`` says ``ran``.
    evidence : str, optional
        The run-report artifact, relative to ``run_dir`` (default :data:`EVIDENCE_ARTIFACT`).
    select_stage, settle_stage : str, optional
        The report stages holding the selector's ``decisions`` and the settlement ``outcomes``.

    Raises
    ------
    ValueError
        With one line, when the run did not finish ``ran`` or its result is unreadable, the
        evidence or a stage is missing or malformed, an order repeats, an outcome has no order
        (or belongs to an unfilled one), an order has malformed legs, dates or prices, or its
        reservation is not positive (no priority can be formed).

    Examples
    --------
    Read a finished replay and list its orders::

        run = ReplayRun("pipeline_runs/condor-replay/2026-01-01-ab12cd")
        run.arms, len(run.orders)
        # -> (("nominal", "rho_base"), 8584)
    """

    def __init__(self, run_dir, evidence=EVIDENCE_ARTIFACT, select_stage=SELECT_STAGE,
                 settle_stage=SETTLE_STAGE):
        self.run_dir = os.fspath(run_dir)
        self._require_ran()
        stages = self._stages(os.path.join(self.run_dir, evidence), evidence)
        decisions = self._listed(stages, select_stage, "decisions")
        outcomes = self._outcomes(self._listed(stages, settle_stage, "outcomes"))
        #: Every order, in the run's own order.
        self.orders = self._orders(decisions, outcomes)
        #: The arms, in the order they first appear.
        self.arms = tuple(dict.fromkeys(order.arm_id for order in self.orders))

    def _require_ran(self):
        """Refuse a run whose ``result.json`` is unreadable or whose state is not ``ran``."""
        try:
            with open(os.path.join(self.run_dir, RESULT_FILE), encoding="utf-8") as handle:
                state = json.load(handle).get("state")
        except (OSError, ValueError, AttributeError):
            raise ValueError(f"{self.run_dir}: {RESULT_FILE} is missing or unreadable") from None
        if state != "ran":
            raise ValueError(f"{self.run_dir}: the run's state is {state!r}, not 'ran'")

    def _stages(self, path, name):
        """Return the run-report evidence's ``stages`` mapping."""
        try:
            with open(path, encoding="utf-8") as handle:
                stages = json.load(handle).get("stages")
        except (OSError, ValueError, AttributeError):
            raise ValueError(f"{self.run_dir}: the report evidence {name} is missing or "
                             "unreadable") from None
        if not isinstance(stages, dict):
            raise ValueError(f"{self.run_dir}: {name} has no stages")
        return stages

    def _listed(self, stages, stage, key):
        """Return ``stages[stage][key]``, a list of objects."""
        rows = stages.get(stage, {}).get(key) if isinstance(stages.get(stage), dict) else None
        if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
            raise ValueError(f"{self.run_dir}: stage {stage!r} lacks a list of {key}")
        return rows

    def _outcomes(self, rows):
        """Return the outcomes by ``(arm, decision)``, refusing a repeated identity."""
        found = {}
        for row in rows:
            key = (row.get("arm_id"), row.get("decision_id"))
            if key in found:
                raise ValueError(f"{self.run_dir}: two outcomes for arm/decision {key}")
            found[key] = row
        return found

    def _orders(self, decisions, outcomes):
        """Build every order from the selector's trade and unfilled decisions."""
        orders, seen = [], set()
        for row in decisions:
            if row.get("status") not in _ORDER_STATUSES:
                continue
            key = (row.get("arm_id"), row.get("decision_id"))
            if key in seen:
                raise ValueError(f"{self.run_dir}: two selections for arm/decision {key}")
            seen.add(key)
            orders.append(self._order(row, outcomes.get(key)))
        orphans = sorted(set(outcomes) - seen, key=str)
        if orphans:
            raise ValueError(f"{self.run_dir}: outcome for arm/decision {orphans[0]} has no "
                             "selection")
        return orders

    def _legs(self, row, where):
        """Return the decision-time ``(strikes, credit_usd)`` of an order's four condor legs."""
        legs, multiplier = row.get("legs"), row.get("multiplier")
        if (not isinstance(legs, list) or len(legs) != len(CONDOR_LEGS)
                or isinstance(multiplier, bool) or not isinstance(multiplier, int)
                or multiplier < 1):
            raise ValueError(f"{where}: needs a positive multiplier and {len(CONDOR_LEGS)} legs")
        credit, strikes = 0.0, []
        for leg, (right, sign) in zip(legs, CONDOR_LEGS):
            price = leg.get("decision_price_usd_per_share", leg.get("price_usd_per_share"))
            if (leg.get("right") != right or leg.get("side") != ("buy" if sign > 0 else "sell")
                    or not all(number_ok(v) for v in (price, leg.get("fee_usd"), leg.get("strike")))):
                raise ValueError(f"{where}: an ordered condor leg is malformed")
            credit += -sign * price * multiplier - leg["fee_usd"]
            strikes.append(leg["strike"])
        return strikes, credit, multiplier

    def _order(self, row, outcome):
        """Build one order: its reservation, priority and release date, from decision facts only."""
        where = f"{self.run_dir} arm/decision {(row.get('arm_id'), row.get('decision_id'))}"
        if row["status"] == "unfilled" and outcome is not None:
            raise ValueError(f"{where}: an unfilled selection has a settlement outcome")
        for key in ("arm_id", "decision_id", "symbol"):
            if not isinstance(row.get(key), str) or not row[key]:
                raise ValueError(f"{where}: lacks {key}")
        strikes, credit, multiplier = self._legs(row, where)
        reservation = structure_max_loss(CONDOR_LEGS, strikes, credit, multiplier)
        if not (math.isfinite(reservation) and reservation > 0 and number_ok(
                row.get("robust_value_usd"))):
            raise ValueError(f"{where}: its reservation {reservation!r} is not positive, so no "
                             "priority can be formed (or its robust value is not a number)")
        release = (outcome.get("settlement_date") if outcome is not None
                   else row.get("forecast_settlement_date", row.get("expiry")))
        if not _iso(row.get("quote_date")) or not _iso(release) or release <= row["quote_date"]:
            raise ValueError(f"{where}: needs a decision date before its settlement date")
        pnl = None if outcome is None else outcome.get("pnl_usd")
        if outcome is not None and not number_ok(pnl):
            raise ValueError(f"{where}: its outcome has no finite pnl_usd")
        return Order(row["arm_id"], row["decision_id"], row["symbol"], row["quote_date"], release,
                     reservation, row["robust_value_usd"] / reservation, row["status"], pnl)


class _Account:
    """Admit one arm's orders under the caps, chronologically, and tally the result.

    Parameters
    ----------
    caps : AdmissionCaps or None
        The caps; ``None`` admits every order (the single-trade view and the peak).
    capital_usd : float or None
        The level's capital, which ``caps.max_reserved_fraction`` limits.
    """

    def __init__(self, caps, capital_usd):
        self.caps, self.capital_usd = caps, capital_usd

    @staticmethod
    def _priority(order):
        """Sort key: higher ex-ante value per reservation first, then symbol ascending."""
        return (-order.score, order.symbol, order.decision_id)

    def _refusal(self, order, held, today):
        """Return the first cap ``order`` would break given ``held`` open orders, or ``None``."""
        caps = self.caps
        if caps is None:
            return None
        if len(held) >= caps.max_concurrent:
            return REJECTION_REASONS[0]
        if sum(o.symbol == order.symbol for o in held) >= caps.max_per_symbol:
            return REJECTION_REASONS[1]
        if today >= caps.max_per_date:
            return REJECTION_REASONS[2]
        if sum(o.reservation for o in held) + order.reservation > (
                caps.max_reserved_fraction * self.capital_usd):
            return REJECTION_REASONS[3]
        return None

    def admit(self, orders):
        """Return ``(admitted orders, rejected Counter, peak reserved USD)``.

        Parameters
        ----------
        orders : list of Order
            One arm's orders.

        Returns
        -------
        tuple
            The admitted orders, refusals by reason and the largest reservation held at once.
        """
        by_day = {}
        for order in orders:
            by_day.setdefault(order.date, []).append(order)
        held, admitted, rejected, peak = [], [], Counter(), 0.0
        for day in sorted(by_day):
            # settlement is at the close, decisions come after it: a release dated today is done
            held = [o for o in held if o.release_date > day]
            today = 0
            for order in sorted(by_day[day], key=self._priority):
                reason = self._refusal(order, held, today)
                if reason is not None:
                    rejected[reason] += 1
                    continue
                held.append(order)
                admitted.append(order)
                today += 1
                peak = max(peak, sum(o.reservation for o in held))
        return admitted, rejected, peak


class AccountStudy(LedgerStudy):
    """Admit a robust replay's orders under account caps, from decision-time facts only.

    Orders are admitted in decision-date order. On one date they are ranked by the ex-ante robust
    value per unit of reservation, then symbol ascending, and admitted while the caps hold: at
    most ``max_concurrent`` open, ``max_per_symbol`` open per underlying, ``max_per_date``
    admitted that date, and open reservations within ``max_reserved_fraction`` of the capital.
    The reservation is the decision legs' maximum loss
    (:func:`~index_options.contracts.structure_max_loss`, fees counted once). A reservation is
    released at the order's saved settlement date, before that day's decisions; an order that
    is later unfilled, or never settles, keeps its reservation to that date and earns nothing:
    nothing learned after the decision frees a slot. Capital levels are the fractions 1, 1/2 and
    1/4 of the nominal arm's unconstrained peak reservation; every arm is accounted at each.
    The output is, per arm and level, the admitted and refused counts (refusals by the first
    cap broken) and the cash-basis P&L of the admitted orders, beside an ``uncapped`` row that
    is the single-trade view. It is not a margin, mark-to-market or delivery model.

    Examples
    --------
    Account a finished replay under the frozen caps::

        study = AccountStudy()
        args = argparse.Namespace(
            run_dir="pipeline_runs/condor-replay/2026-01-01-ab12cd", max_concurrent=10,
            max_per_symbol=1, max_per_date=3, max_reserved_fraction=1.0,
            nominal_arm="nominal", evidence=EVIDENCE_ARTIFACT, select_stage="sizing",
            settle_stage="replay")
        print(study.report(args))
    """

    name = "account"
    summary = "decision-time admission of a robust replay's orders under account caps"

    @staticmethod
    def _positive_int(text):
        """Parse a positive integer command-line value."""
        try:
            value = int(text)
        except ValueError:
            raise argparse.ArgumentTypeError(f"{text!r} is not an integer") from None
        if value < 1:
            raise argparse.ArgumentTypeError(f"{text!r} must be at least 1")
        return value

    @staticmethod
    def _positive_number(text):
        """Parse a positive finite number command-line value."""
        try:
            value = float(text)
        except ValueError:
            raise argparse.ArgumentTypeError(f"{text!r} is not a number") from None
        if not (math.isfinite(value) and value > 0):
            raise argparse.ArgumentTypeError(f"{text!r} must be a positive finite number")
        return value

    @classmethod
    def add_arguments(cls, parser):
        """Take the run directory, the four required caps and the run-layout names.

        Parameters
        ----------
        parser : argparse.ArgumentParser
            The ``account`` subparser.
        """
        parser.add_argument("run_dir", metavar="RUN_DIR",
                            help="a completed robust replay run directory (state ran)")
        for flag, kind, text in (
                ("--max-concurrent", cls._positive_int, "most orders open at once"),
                ("--max-per-symbol", cls._positive_int, "most open orders of one underlying"),
                ("--max-per-date", cls._positive_int, "most orders admitted on one decision date"),
                ("--max-reserved-fraction", cls._positive_number,
                 "the share of the capital level open reservations may fill")):
            parser.add_argument(flag, type=kind, required=True, help=text)
        parser.add_argument("--nominal-arm", default=NOMINAL_ARM,
                            help=f"the arm whose peak reservation sizes the capital "
                                 f"(default: {NOMINAL_ARM})")
        parser.add_argument("--evidence", default=EVIDENCE_ARTIFACT,
                            help="the run-report evidence artifact, relative to RUN_DIR")
        parser.add_argument("--select-stage", default=SELECT_STAGE,
                            help="the report stage listing the selector's decisions")
        parser.add_argument("--settle-stage", default=SETTLE_STAGE,
                            help="the report stage listing the settlement outcomes")

    @staticmethod
    def _peak(orders):
        """Return the largest total reservation open at once when every order is admitted."""
        return _Account(None, None).admit(orders)[2]

    @classmethod
    def rows(cls, run, caps, nominal_arm=NOMINAL_ARM):
        """Return one :class:`AccountRow` per arm and level, ``uncapped`` first for each arm.

        Parameters
        ----------
        run : ReplayRun
            The completed run.
        caps : AdmissionCaps
            The admission caps.
        nominal_arm : str, optional
            The arm whose unconstrained peak reservation sizes the capital levels.

        Returns
        -------
        list of AccountRow
            Arms in the run's order; per arm ``uncapped`` then the levels of
            :data:`CAPITAL_LEVELS`.

        Raises
        ------
        ValueError
            When ``nominal_arm`` has no orders in the run.
        """
        by_arm = {arm: [o for o in run.orders if o.arm_id == arm] for arm in run.arms}
        if not by_arm.get(nominal_arm):
            raise ValueError(f"the nominal arm {nominal_arm!r} has no orders in {run.run_dir}: "
                             "the capital levels have no peak to take fractions of")
        peak = cls._peak(by_arm[nominal_arm])
        levels = ((UNCAPPED, None, None), *((label, fraction, float(fraction) * peak)
                                            for label, fraction in CAPITAL_LEVELS))
        out = []
        for arm, orders in by_arm.items():
            for label, _, capital in levels:
                admitted, rejected, held = _Account(None if capital is None else caps,
                                                    capital).admit(orders)
                out.append(AccountRow(
                    arm, label, capital, len(admitted),
                    sum(o.status == "unfilled" for o in admitted), dict(rejected),
                    sum(o.pnl_usd for o in admitted if o.pnl_usd is not None), held))
        return out

    @staticmethod
    def _cells(row):
        """Return one row's printed cells."""
        money = f"{{:.{_MONEY_DECIMALS}f}}".format
        return [row.arm_id, row.level, "-" if row.capital_usd is None else money(row.capital_usd),
                str(row.admitted), str(row.unfilled), str(sum(row.rejected.values())),
                money(row.pnl_usd), money(row.peak_reserved_usd),
                *(str(row.rejected.get(reason, 0)) for reason in REJECTION_REASONS)]

    def report(self, args):
        """Return the caps line, the account table and the not-a-margin-model line.

        Parameters
        ----------
        args : argparse.Namespace
            ``run_dir``, the four caps, ``nominal_arm``, ``evidence``, ``select_stage`` and
            ``settle_stage``.

        Returns
        -------
        str
            Three blocks: the caps; the table of :data:`ACCOUNT_COLUMNS`, one row per arm and
            level; the note that this is not a margin, mark-to-market or delivery model.

        Raises
        ------
        ValueError
            When the run is refused (see :class:`ReplayRun`) or the nominal arm has no orders.
        """
        run = ReplayRun(args.run_dir, args.evidence, args.select_stage, args.settle_stage)
        caps = AdmissionCaps(args.max_concurrent, args.max_per_symbol, args.max_per_date,
                             args.max_reserved_fraction)
        rows = self.rows(run, caps, args.nominal_arm)
        facts = (f"account: {len(run.orders)} orders over {len(run.arms)} arms in {run.run_dir}; "
                 + ", ".join(f"{name}={value}" for name, value in caps._asdict().items())
                 + f"; capital = 1, 1/2, 1/4 of the {args.nominal_arm} arm's unconstrained peak "
                   "reservation")
        table = self._grid([list(ACCOUNT_COLUMNS)] + [self._cells(row) for row in rows])
        return "\n\n".join([facts, table, _ACCOUNT_NOTE])


#: The studies by command.
STUDIES = {study.name: study for study in (AllocationStudy, HedgeStudy, AccountStudy)}


def main(argv=None):
    """Run one study: print its report and return 0, or print one error line and return 1.

    Parameters
    ----------
    argv : list of str, optional
        The command line after the program name; ``sys.argv`` by default.

    Returns
    -------
    int
        ``0`` when the report was printed, ``1`` when an input was refused. A malformed
        command line exits ``2`` through :mod:`argparse`.

    Examples
    --------
    The command the module runs as::

        main(["allocate", "runs/spy-walk", "runs/qqq-walk"])
    """
    parser = argparse.ArgumentParser(
        prog="python -m index_options.ledger_studies",
        description="Read-only studies over the ledgers of finished walk-forward runs.")
    commands = parser.add_subparsers(dest="study", required=True, metavar="STUDY")
    for name, study in STUDIES.items():
        study.add_arguments(commands.add_parser(name, help=study.summary,
                                                description=study.summary))
    args = parser.parse_args(argv)
    try:
        text = STUDIES[args.study]().report(args)
    except ValueError as exc:
        print(f"error: {exc}")
        return 1
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
