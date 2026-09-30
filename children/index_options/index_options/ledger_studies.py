"""Read-only studies over the ledgers of finished walk-forward runs (ADR-0196).

A study reads what a walk-forward already recorded and writes nothing, like
``python -m dskit.pipeline skill``. Each walk summary directory lists its fold run
directories (:func:`~dskit.pipeline.runs.walk_fold_dirs`); a fold's backtest node
record (``<run dir>/nodes/NN-backtest.json``) names its ``report`` as a JSON artifact
(:func:`~dskit.pipeline.driver.resolve_json_artifact` checks its size and digest), and
the report's ledger holds one entry per entry date. One command ships::

    python -m index_options.ledger_studies allocate <walk dir> <walk dir> [...] [--book model]

**allocate** asks where to sell, SPY or QQQ, on the dates both could trade. Give it one
walk summary directory per underlying (two or more, each a different instrument).

1. Per walk it gathers the chosen book's ENTERED cells (``--book``: ``model``, the
   default, or ``always``; the ``implied`` book trades other strikes than the model's
   expectation was computed for), with their date, ``pnl_usd``, ``credit_usd``,
   ``strikes``, the structure's legs and the entry's ``model_expected_pnl_usd``.
2. The structure's legs are the report's own: the condor's or the put spread's for a
   fixed-structure report, and for the payoff selector the cell's ``selected.structure``
   looked up in :data:`~index_options.contracts.STRUCTURES`. Nothing here restates a
   structure or a width: the widths are :func:`~index_options.contracts.structure_credit`'s.
3. The ex-ante score of a cell is ``model_expected_pnl_usd / max_loss`` with
   ``max_loss = multiplier x the widest vertical - credit_usd``: the expectation the model
   had BEFORE the outcome, per unit of the money the structure can lose.
4. On each date in the UNION of the walks' entry dates, the ``allocate`` series takes, among
   the underlyings that entered that date, the cell with the highest score (an exact tie
   goes to the earlier command-line argument) and its ``pnl_usd``. The ``equal`` series
   takes the mean ``pnl_usd`` of those that entered. Each underlying ``alone`` is its own
   entered cells. All series run in date order.

One table is printed, one row per series: ``n``, the mean P&L, its Newey-West ``t`` (lags
0: the backtests' own, so a float-noise-constant series reads 0.0), the lower-tail mean at
the backtests' default tail (``cvar5_usd``) and the maximum drawdown, each from the same
owner the backtests' metrics use. The study refuses, with one ``error: ...`` line and exit
code 1, unless every fold of every walk declares the same ``dte_min`` / ``dte_max`` (one
bucket: other buckets have other cadences), every report is an archived-quote backtest, and
every ledger cell can be scored; exit code 0 otherwise. A malformed command line exits 2.
Never decision-eligible: it reads backtests whose fills are the end-of-day touch.
"""

import argparse
import json
import os
import sys
from abc import ABC, abstractmethod
from typing import NamedTuple

from dskit.pipeline.driver import resolve_json_artifact
from dskit.pipeline.records import number_ok
from dskit.pipeline.runs import NODES_DIR, walk_fold_dirs
from dskit.pipeline.stats import lower_tail_mean, max_drawdown

from .contracts import STRUCTURES, structure_credit
from .nodes import CondorQuoteBacktest, PayoffSelectQuoteBacktest, PutSpreadQuoteBacktest

__all__ = ["AllocationStudy", "EnteredCell", "LedgerStudy", "STUDIES", "WalkLedger", "main"]

#: The key the shipped grid documents give their backtest node.
BACKTEST_NODE = "backtest"
#: The tail level of the printed lower-tail mean: the backtests' own default.
CVAR_ALPHA = CondorQuoteBacktest.DEFAULTS["cvar_alpha"]
#: The printed table's columns; the tail column is named for the share of the tail.
COLUMNS = ("series", "n", "mean_pnl_usd", "t", f"cvar{round(100 * (1 - CVAR_ALPHA))}_usd",
           "max_drawdown_usd")
#: Report kind -> the legs of its one structure; the selector's kind has none (its cells say).
_FIXED_LEGS = {cls.REPORT_KIND: cls.LEGS for cls in (CondorQuoteBacktest, PutSpreadQuoteBacktest)}
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
        The credit received, net of fees.
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
        """The most the structure can lose: ``multiplier x`` its widest vertical less the credit."""
        _credit, widths = structure_credit(self.legs, self.strikes, [(0.0, 0.0)] * len(self.legs))
        return self.multiplier * max(widths) - self.credit_usd

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
        With one line, when the directory is not a walk, lists no ran fold, a fold has no
        readable backtest record or report artifact, a report is not an archived-quote
        backtest, its folds disagree on the bucket, or it holds more than one instrument.

    Examples
    --------
    The model book's traded cells of one walk::

        ledger = WalkLedger(summary_dir)
        ledger.bucket, ledger.label
        # -> ((7, 10), "SPY")
        cells = ledger.cells("model")
    """

    def __init__(self, summary_dir):
        self.summary_dir = os.fspath(summary_dir)
        run_dirs = walk_fold_dirs(self.summary_dir)
        if not run_dirs:
            raise ValueError(f"{self.summary_dir} lists no fold that ran")
        self._reports = [(run_dir, self._checked(run_dir, self._load(run_dir)))
                         for run_dir in run_dirs]
        self.bucket = self._one_bucket()
        self.instrument = self._one_instrument()
        #: What the walk is called in the table: its instrument, else its directory.
        self.label = self.instrument or os.path.basename(self.summary_dir.rstrip(os.sep))

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
                             f"({sorted(names)}): an allocation takes one walk per underlying")
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
            books = entry.get("books") if isinstance(entry, dict) else None
            cell = books.get(book) if isinstance(books, dict) else None
            if not isinstance(entry.get("date"), str) or not isinstance(cell, dict):
                raise ValueError(f"{self.summary_dir}: a ledger entry has no date or no "
                                 f"{book!r} book cell")
            if cell.get("entered") is not True:
                continue
            if entry["date"] in found:
                raise ValueError(f"{self.summary_dir} repeats entry date {entry['date']}: "
                                 "one traded cell per date is what an allocation compares")
            found[entry["date"]] = self._cell(report, entry, cell)
        return [found[day] for day in sorted(found)]


def _bucket_text(bucket):
    """Render a ``(dte_min, dte_max)`` pair as ``7-10``."""
    return f"{bucket[0]}-{bucket[1]}"


class LedgerStudy(ABC):
    """A read-only study over finished walks: its arguments, its series and one printed table.

    A member names its command (:attr:`name`), declares its arguments
    (:meth:`add_arguments`) and supplies the series to tabulate (:meth:`series`); the base
    prints every series through the same owners of the mean, ``t``, lower-tail mean and
    drawdown the backtests report, so studies cannot disagree about a statistic.

    Examples
    --------
    A member that tabulates each walk's model book alone::

        class Alone(LedgerStudy):
            name = "alone"

            @classmethod
            def add_arguments(cls, parser):
                parser.add_argument("walks", nargs="+")

            def series(self, args):
                return [(w, [c.pnl_usd for c in WalkLedger(w).cells("model")]) for w in args.walks]

        print(Alone().table(args))
    """

    #: The command that selects the study.
    name = ""
    #: One line for the command's ``--help``.
    summary = ""

    @classmethod
    @abstractmethod
    def add_arguments(cls, parser):
        """Declare the study's command-line arguments on ``parser``."""

    @abstractmethod
    def series(self, args):
        """Return ``[(label, pnls)]``: each named P&L series, in date order."""

    @staticmethod
    def _row(label, pnls):
        """Return one series' printed cells: its count and the four statistics of its P&L."""
        # the backtests' own mean and t (0.0 below two values or with no variance), tail and path
        owner = CondorQuoteBacktest
        return [label, str(len(pnls)), f"{owner._mean(pnls):.2f}", f"{owner._t(pnls):.3f}",
                f"{lower_tail_mean(pnls, CVAR_ALPHA) if pnls else 0.0:.2f}",
                f"{max_drawdown(pnls):.2f}"]

    def table(self, args):
        """Return the study's one table: :data:`COLUMNS`, then a row per series.

        Parameters
        ----------
        args : argparse.Namespace
            The parsed arguments :meth:`add_arguments` declared.

        Returns
        -------
        str
            The table, first column left-aligned and the rest right-aligned.

        Raises
        ------
        ValueError
            When the study refuses its inputs, with one line.
        """
        rows = [list(COLUMNS)] + [self._row(label, pnls) for label, pnls in self.series(args)]
        widths = [max(len(row[k]) for row in rows) for k in range(len(COLUMNS))]
        return "\n".join("  ".join(cell.ljust(width) if k == 0 else cell.rjust(width)
                                   for k, (cell, width) in enumerate(zip(row, widths)))
                         for row in rows)


class AllocationStudy(LedgerStudy):
    """Sell where the model expects most per unit at risk: SPY or QQQ, date by date.

    See the module docstring for the rule. One walk summary directory per underlying, all in
    one bucket; ``--book`` picks the model's (default) or the always book's traded cells.

    Examples
    --------
    From the command line, SPY first so it wins ties::

        python -m index_options.ledger_studies allocate runs/spy-walk runs/qqq-walk --book model
    """

    name = "allocate"
    summary = "allocate each entry date to the underlying with the higher ex-ante score"
    #: The books whose cells the model's expectation was computed for.
    BOOKS = CondorQuoteBacktest.FORECAST_BOOKS
    DEFAULT_BOOK = "model"

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
        parser.add_argument("--book", choices=cls.BOOKS, default=cls.DEFAULT_BOOK,
                            help="the book whose traded cells are compared (default: model)")

    @staticmethod
    def _ledgers(directories):
        """Load every walk and refuse a lone walk, mixed buckets or one instrument twice."""
        if len(directories) < 2:
            raise ValueError("allocate needs at least two walk directories, one per underlying")
        ledgers = [WalkLedger(directory) for directory in directories]
        for ledger in ledgers[1:]:
            if ledger.bucket != ledgers[0].bucket:
                raise ValueError(
                    "mixed buckets: "
                    f"{_bucket_text(ledgers[0].bucket)} in {ledgers[0].summary_dir} but "
                    f"{_bucket_text(ledger.bucket)} in {ledger.summary_dir}")
        seen = set()
        for ledger in (ledger for ledger in ledgers if ledger.instrument is not None):
            if ledger.instrument in seen:
                raise ValueError(f"two of these walks hold the same instrument "
                                 f"({ledger.instrument}): an allocation is across underlyings")
            seen.add(ledger.instrument)
        return ledgers

    def series(self, args):
        """Return the allocation, the equal split and each underlying alone.

        Parameters
        ----------
        args : argparse.Namespace
            ``walks`` (directories) and ``book``.

        Returns
        -------
        list of tuple
            ``(label, pnls)`` for ``allocate``, ``equal`` and one ``alone:<label>`` per walk.
        """
        ledgers = self._ledgers(args.walks)
        traded = [{cell.date: cell for cell in ledger.cells(args.book)} for ledger in ledgers]
        entered = [[walk[day] for walk in traded if day in walk]
                   for day in sorted(set().union(*traded))]
        allocate = [max(cells, key=lambda cell: cell.score).pnl_usd for cells in entered]
        equal = [sum(cell.pnl_usd for cell in cells) / len(cells) for cells in entered]
        alone = [(f"alone:{ledger.label}", [cell.pnl_usd for cell in walk.values()])
                 for ledger, walk in zip(ledgers, traded)]
        return [("allocate", allocate), ("equal", equal), *alone]


#: The studies by command; ADR-0197 adds ``hedge`` here.
STUDIES = {AllocationStudy.name: AllocationStudy}


def main(argv=None):
    """Run one study: print its table and return 0, or print one error line and return 1.

    Parameters
    ----------
    argv : list of str, optional
        The command line after the program name; ``sys.argv`` by default.

    Returns
    -------
    int
        ``0`` when the table was printed, ``1`` when an input was refused. A malformed
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
        table = STUDIES[args.study]().table(args)
    except ValueError as exc:
        print(f"error: {exc}")
        return 1
    print(table)
    return 0


if __name__ == "__main__":
    sys.exit(main())
