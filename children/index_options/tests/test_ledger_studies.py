"""ADR-0196: the read-only allocation study over finished walk-forward ledgers.

Every walk here is a REAL ``run_walk_forward`` over a synthetic document whose ``backtest`` node
hands a pre-built backtest report through as its JSON artifact, so the on-disk layout the study
reads (``walkforward.json``, ``nodes/NN-backtest.json``, ``artifacts/json/<sha256>.json``) is the
driver's own. Every expected number is worked by hand from the ledgers below.
"""

import hashlib
import json
import math
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from dskit.pipeline.base import OutputsConfig
from dskit.pipeline.document import NodeSpec, PipelineDocument, WalkForwardSpec
from dskit.pipeline.driver import run_walk_forward
from dskit.pipeline.node import JsonArtifact, Node

from index_options import ledger_studies


class Reporter(Node):
    """Stands in for a finished backtest: its report for this fold is handed through as the JSON artifact."""

    role = "transform"
    outputs = ("score", "report")

    def run(self, ctx, inputs):
        return {"score": 0.0, "report": JsonArtifact(self.params["reports"][ctx.fold_index])}


CONDOR = "archived_quote_condor_backtest"
PUT_SPREAD = "archived_quote_put_spread_backtest"
SELECT = "archived_quote_payoff_select_backtest"
PROXY = "vix_proxy_condor_backtest"
A, B, C, D = "2024-03-01", "2024-03-11", "2024-03-19", "2024-03-27"
SYMMETRIC_CONDOR = [90.0, 95.0, 105.0, 110.0]         # wings 5 and 5
LOPSIDED_CONDOR = [90.0, 95.0, 105.0, 115.0]          # wings 5 and 10: the widest vertical is 10
PUT_SPREAD_STRIKES = [92.0, 95.0]                     # width 3
CALL_SPREAD_STRIKES = [106.0, 109.0]                  # width 3


def _cell(strikes, credit, pnl, structure=None, reason=None):
    """One entered book cell; ``structure`` adds the selector's ``selected`` record."""
    cell = {"strikes": strikes, "credit_usd": credit, "pnl_usd": pnl, "entered": True,
            "reason": reason}
    if structure is not None:
        cell["selected"] = {"structure": structure, "short_q": 0.1, "wing_z": 0.5, "score": 0.0,
                            "n_candidates_scored": 3}
    return cell


def _skipped(reason="below_min_edge"):
    return {"strikes": None, "credit_usd": None, "pnl_usd": None, "entered": False,
            "reason": reason}


def _entry(day, expected, model, always=None, instrument="SPY"):
    """One ledger entry; the always book trades what the model does unless told otherwise."""
    return {"date": day, "asof_ms": 0, "instrument": instrument,
            "model_expected_pnl_usd": expected,
            "books": {"model": model, "always": model if always is None else always,
                      "implied": _cell(SYMMETRIC_CONDOR, 1.0, 1.0)}}


def _report(kind, entries, dte=(7, 10), multiplier=100, pricing="archived_eod_quotes"):
    return {"kind": kind, "pricing": pricing, "decision_eligible": False,
            "units": "USD per structure",
            "params": {"multiplier": multiplier, "dte_min": dte[0], "dte_max": dte[1],
                       "cvar_alpha": 0.95},
            "chain": {"first_date": A, "last_date": D, "n_rows": 1},
            "metrics": {}, "ledger": entries}


def _walk(root, name, reports):
    """Run a real walk-forward with one fold per report; return its summary directory."""
    document = PipelineDocument(
        name=name,
        pipeline={"events": NodeSpec(uses="dskit.pipeline.synthetic_nodes:SynthEvents",
                                     params={"n_events": 4}),
                  "backtest": NodeSpec(uses="test_ledger_studies:Reporter",
                                       inputs={"events": "$events.events"},
                                       params={"reports": reports})},
        outputs=OutputsConfig(run_root=str(root)),
        walkforward=WalkForwardSpec(
            objective="$backtest.score", val_days=7,
            folds=[f"2025-0{k + 1}-01" for k in range(len(reports))]))
    result = run_walk_forward(document, asof="2026-01-01")
    assert result.state == "ran"
    return result.summary_dir


# SPY: a condor book, two folds.                              score = E_P / (100 x widest - credit)
#   A  credit 100  E_P  80  pnl +100   wings 5, 5            80 / 400  = 0.2
#   B  credit 150  E_P 120  pnl -350   wings 5, 10 (!)       120 / 850 = 0.14118  (not 120 / 350)
#   C  the model skips (below_min_edge); the always book trades it: credit 100, E_P -10, pnl -80
#   D  credit 100  E_P  40  pnl +100   wings 5, 5            40 / 400  = 0.1
SPY_FOLDS = [
    [_entry(A, 80.0, _cell(SYMMETRIC_CONDOR, 100.0, 100.0)),
     _entry(B, 120.0, _cell(LOPSIDED_CONDOR, 150.0, -350.0))],
    [_entry(C, -10.0, _skipped(), always=_cell(SYMMETRIC_CONDOR, 100.0, -80.0)),
     _entry(D, 40.0, _cell(SYMMETRIC_CONDOR, 100.0, 100.0))],
]
# QQQ: the payoff selector, whatever structure it picked.
#   A  put spread  [92, 95]   credit  60  E_P  60  pnl  +60   60 / (300 - 60)  = 0.25
#   B  call spread [106, 109] credit  50  E_P  50  pnl -250   50 / (300 - 50)  = 0.2
#   C  condor      sym        credit 200  E_P 100  pnl +200  100 / (500 - 200) = 0.3333
#   D  put spread  [92, 95]   credit  60  E_P  24  pnl  -20   24 / (300 - 60)  = 0.1 (== SPY D)
QQQ_FOLDS = [
    [_entry(A, 60.0, _cell(PUT_SPREAD_STRIKES, 60.0, 60.0, "put_spread"), instrument="QQQ"),
     _entry(B, 50.0, _cell(CALL_SPREAD_STRIKES, 50.0, -250.0, "call_spread"), instrument="QQQ")],
    [_entry(C, 100.0, _cell(SYMMETRIC_CONDOR, 200.0, 200.0, "condor"), instrument="QQQ"),
     _entry(D, 24.0, _cell(PUT_SPREAD_STRIKES, 60.0, -20.0, "put_spread"), instrument="QQQ")],
]


@pytest.fixture(scope="module")
def walks(tmp_path_factory):
    """The SPY condor walk and the QQQ selector walk, run once and only ever read."""
    root = tmp_path_factory.mktemp("walks")
    return {
        "spy": _walk(root, "spy", [_report(CONDOR, fold) for fold in SPY_FOLDS]),
        "qqq": _walk(root, "qqq", [_report(SELECT, fold) for fold in QQQ_FOLDS]),
    }


def _run(capsys, *argv):
    """Run the CLI; return ``(exit code, stdout lines, stderr)``."""
    code = ledger_studies.main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out.splitlines(), captured.err


def _blocks(lines):
    """The printed lines as blank-line separated blocks."""
    blocks, current = [], []
    for line in lines:
        if line:
            current.append(line)
        elif current:
            blocks.append(current)
            current = []
    return blocks + ([current] if current else [])


def _table(lines):
    """Parse a printed table: ``(header tokens, {series: [n, mean, t, cvar, drawdown]})``."""
    header = lines[0].split()
    return header, {row.split()[0]: [int(row.split()[1]), *map(float, row.split()[2:])]
                    for row in lines[1:]}


def _allocation(lines):
    """Parse ``allocate``'s three blocks: the shared-dates table, the walks, the union table."""
    blocks = _blocks(lines)
    assert len(blocks) == 3, blocks
    shared, walks, union = blocks
    assert shared[0].startswith("n_shared: "), shared[0]
    assert walks[0] == "walks", walks[0]
    assert union[0].startswith("union of dates"), union[0]
    return SimpleNamespace(n_shared=int(shared[0].split()[1]), header=_table(shared[1:])[0],
                           shared=_table(shared[1:])[1], walks=[w.split() for w in walks[2:]],
                           walks_header=walks[1].split(), union=_table(union[1:])[1],
                           union_caption=union[0])


def _t(values):
    """The lags=0 t of a mean, written out: mean / (sd / sqrt(n)), sd with divisor n; 0.0 if undefined."""
    n = len(values)
    if n < 2:
        return 0.0
    mean = sum(values) / n
    sd = math.sqrt(sum((v - mean) ** 2 for v in values) / n)
    return mean / (sd / math.sqrt(n)) if sd > 0 else 0.0


HEADER = ["series", "n", "mean_pnl_usd", "t", "cvar5_usd", "max_drawdown_usd"]
#: series -> (P&L in date order, mean, CVaR at 5 %, max drawdown), worked by hand (model book;
#: SPY first). The 5 % tail of four or fewer values is the single worst one. THE SHARED DATES are
#: the ones both walks entered the model book: A, B, D (SPY's model skips C).
MODEL_SHARED = {
    "allocate": ([60.0, -250.0, 100.0], -30.0, -250.0, 250.0),
    "equal": ([80.0, -300.0, 40.0], -60.0, -300.0, 300.0),
    "alone:SPY": ([100.0, -350.0, 100.0], -50.0, -350.0, 350.0),
    "alone:QQQ": ([60.0, -250.0, -20.0], -70.0, -250.0, 270.0),
}
#: The UNION of the walks' entry dates, A, B, C, D: what ADR-0196 shipped, now a labelled extra.
MODEL_UNION = {
    "allocate": ([60.0, -250.0, 200.0, 100.0], 27.5, -250.0, 250.0),
    "equal": ([80.0, -300.0, 200.0, 40.0], 5.0, -300.0, 300.0),
    "alone:SPY": ([100.0, -350.0, 100.0], -50.0, -350.0, 350.0),
    "alone:QQQ": ([60.0, -250.0, 200.0, -20.0], -2.5, -250.0, 250.0),
}


def _assert_table(table, want):
    assert list(table) == list(want)                         # series and their order
    for series, (pnls, mean, cvar, drawdown) in want.items():
        n, got_mean, got_t, got_cvar, got_dd = table[series]
        assert n == len(pnls), series
        assert got_mean == pytest.approx(mean, abs=0.005), series
        assert got_t == pytest.approx(_t(pnls), abs=0.0005), series
        assert got_cvar == pytest.approx(cvar, abs=0.005), series
        assert got_dd == pytest.approx(drawdown, abs=0.005), series


def test_the_allocation_table_compares_every_series_over_the_shared_dates_by_hand(walks, capsys):
    # the model book: SPY entered A, B, D (it skipped C), QQQ entered A, B, C, D, so the SHARED
    # dates are A, B, D and every series below covers exactly those three
    # A: QQQ 0.25 beats SPY 0.2 (+60)   B: QQQ 0.2 beats SPY 0.1412 (-250) -- only because the
    # condor's widest vertical is 10 points, not its narrowest 5   D: 0.1 == 0.1, SPY is the
    # earlier argument (+100). QQQ's C (+200) is nobody's to compare with, so it is not in here
    code, lines, err = _run(capsys, "allocate", walks["spy"], walks["qqq"])
    assert code == 0 and err == ""
    got = _allocation(lines)
    assert got.header == HEADER and got.n_shared == 3
    _assert_table(got.shared, MODEL_SHARED)
    # worked out by hand, not from the oracle: the equal split's mean is (80 - 300 + 40) / 3
    assert got.shared["equal"][1:] == pytest.approx([-60.0, _t([80.0, -300.0, 40.0]), -300.0,
                                                     300.0], abs=0.005)
    # one n for all four series: a row that covered other dates would not be comparable
    assert {row[0] for row in got.shared.values()} == {3}


def test_the_union_of_dates_is_a_separate_labelled_block_with_the_old_table(walks, capsys):
    _, lines, _ = _run(capsys, "allocate", walks["spy"], walks["qqq"])
    got = _allocation(lines)
    # the caption says what the block is and that it is NOT comparable with the table above
    assert "NOT comparable" in got.union_caption and "union" in got.union_caption
    _assert_table(got.union, MODEL_UNION)
    assert got.union["allocate"][0] == got.union["equal"][0] == 4       # every date in the union
    assert got.union["alone:SPY"][0] == 3 and got.union["alone:QQQ"][0] == 4
    # the two blocks differ exactly where the dates do: QQQ's C (+200) is in the union only
    assert got.shared["alone:QQQ"][0] == 3 and got.shared["alone:QQQ"][1] != got.union["alone:QQQ"][1]
    assert list(got.shared) == list(got.union)                        # the same series, same order


def test_the_walk_block_names_each_walks_instrument_span_entries_and_folds(walks, capsys):
    _, lines, _ = _run(capsys, "allocate", walks["spy"], walks["qqq"])
    got = _allocation(lines)
    assert got.walks_header == ["instrument", "folds", "entries", "first_entry", "last_entry",
                                "walk"]
    # one row per walk in argument order: SPY entered A, B, D over its two folds, QQQ all four
    assert [row[:5] for row in got.walks] == [["SPY", "2", "3", A, D], ["QQQ", "2", "4", A, D]]
    assert [row[5] for row in got.walks] == [walks["spy"], walks["qqq"]]


def test_a_tie_goes_to_the_earlier_argument(walks, capsys):
    # on D both ex-ante scores are 40 / 400 and 24 / 240, the same rational 0.1 exactly
    _, lines, _ = _run(capsys, "allocate", walks["spy"], walks["qqq"])
    assert _allocation(lines).shared["allocate"][1] == pytest.approx(-30.0, abs=0.005)   # SPY's +100
    _, lines, _ = _run(capsys, "allocate", walks["qqq"], walks["spy"])
    got = _allocation(lines)
    assert list(got.shared) == ["allocate", "equal", "alone:QQQ", "alone:SPY"]   # argument order
    _assert_table(got.shared, {
        "allocate": ([60.0, -250.0, -20.0], -70.0, -250.0, 270.0),               # QQQ's -20 on D
        "equal": MODEL_SHARED["equal"], "alone:QQQ": MODEL_SHARED["alone:QQQ"],
        "alone:SPY": MODEL_SHARED["alone:SPY"]})
    assert [row[0] for row in got.walks] == ["QQQ", "SPY"]


def test_the_always_book_is_read_with_the_models_expectation(walks, capsys):
    # SPY's always book also trades C (-80, scored at the model's E_P -10 over 400): the two walks
    # now share all four dates, so the shared table is the whole union; the equal split and SPY
    # alone see C, the allocation still takes QQQ there (0.3333 > -0.025)
    code, lines, _ = _run(capsys, "allocate", walks["spy"], walks["qqq"], "--book", "always")
    assert code == 0
    got = _allocation(lines)
    assert got.n_shared == 4
    want = {
        "allocate": ([60.0, -250.0, 200.0, 100.0], 27.5, -250.0, 250.0),
        "equal": ([80.0, -300.0, 60.0, 40.0], -30.0, -300.0, 300.0),
        "alone:SPY": ([100.0, -350.0, -80.0, 100.0], -57.5, -350.0, 430.0),   # 100 -> -330: 430
        "alone:QQQ": MODEL_UNION["alone:QQQ"]}
    _assert_table(got.shared, want)
    _assert_table(got.union, want)                                # no date is left out of either
    _, model_lines, _ = _run(capsys, "allocate", walks["spy"], walks["qqq"], "--book", "model")
    assert _allocation(model_lines).shared == _allocation(_run(
        capsys, "allocate", walks["spy"], walks["qqq"])[1]).shared    # model is the default


def test_dates_only_one_underlying_entered_leave_the_shared_table_and_stay_in_the_union(
    tmp_path, capsys
):
    # SPY alone on A, QQQ alone on B: nothing both traded, so nothing is comparable (n_shared 0 and
    # every shared row empty); the union block still shows what each entered, dropped by no one
    spy = _walk(tmp_path, "spy", [_report(CONDOR, [
        _entry(A, 80.0, _cell(SYMMETRIC_CONDOR, 100.0, 100.0))])])
    qqq = _walk(tmp_path, "qqq", [_report(PUT_SPREAD, [
        _entry(B, 60.0, _cell(PUT_SPREAD_STRIKES, 60.0, -40.0), instrument="QQQ")])])
    code, lines, _ = _run(capsys, "allocate", spy, qqq)
    assert code == 0
    got = _allocation(lines)
    assert got.n_shared == 0
    assert all(row == [0, 0.0, 0.0, 0.0, 0.0] for row in got.shared.values())
    _assert_table(got.union, {
        "allocate": ([100.0, -40.0], 30.0, -40.0, 40.0),
        "equal": ([100.0, -40.0], 30.0, -40.0, 40.0),
        "alone:SPY": ([100.0], 100.0, 100.0, 0.0),
        "alone:QQQ": ([-40.0], -40.0, -40.0, 40.0)})


def test_series_run_in_date_order_and_a_walk_without_entries_adds_nothing(tmp_path, capsys):
    # the later date listed first in the ledger still comes second in the series; a walk with no
    # entry at all has no instrument to name it by, so its directory does (and, having no entry,
    # it shares no date with anyone: the shared table is empty, the union is SPY's own)
    spy = _walk(tmp_path, "spy", [_report(CONDOR, [
        _entry(B, 80.0, _cell(SYMMETRIC_CONDOR, 100.0, -300.0)),
        _entry(A, 80.0, _cell(SYMMETRIC_CONDOR, 100.0, 100.0))])])
    qqq = _walk(tmp_path, "qqq", [_report(CONDOR, [], dte=(7, 10))])
    _, lines, _ = _run(capsys, "allocate", spy, qqq)
    got = _allocation(lines)
    assert got.union["alone:SPY"][3:] == [-300.0, 300.0]    # 100 then -300: CVaR -300, drawdown 300
    assert got.union["alone:" + qqq.rsplit("/", 1)[1]][0] == 0      # no entries and no instrument
    assert got.union["equal"][0] == 2 and got.union["allocate"][0] == 2
    assert got.n_shared == 0 and got.shared["allocate"][0] == 0
    empty = got.walks[1]                                     # its row: no instrument, no span
    assert empty[:5] == ["-", "1", "0", "-", "-"] and empty[5] == qqq


def test_the_drawdown_follows_date_order_not_the_order_of_the_ledger(tmp_path, capsys):
    # 100, -50, -50, 100, -80 by date: the path peaks at 100 and falls to 0 (100), later to 20
    # (80). The ledger lists the dates 3, 1, 5, 2, 4; in that order the path would fall 130
    pnls = {"2024-03-01": 100.0, "2024-03-11": -50.0, "2024-03-19": -50.0, "2024-03-27": 100.0,
            "2024-04-04": -80.0}
    listed = ["2024-03-19", "2024-03-01", "2024-04-04", "2024-03-11", "2024-03-27"]
    spy = _walk(tmp_path, "spy", [_report(CONDOR, [
        _entry(d, 80.0, _cell(SYMMETRIC_CONDOR, 100.0, pnls[d])) for d in listed])])
    qqq = _walk(tmp_path, "qqq", [_report(SELECT, [
        _entry(d, 60.0, _cell(PUT_SPREAD_STRIKES, 60.0, 1.0, "put_spread"), instrument="QQQ")
        for d in reversed(listed)])])
    _, lines, _ = _run(capsys, "allocate", spy, qqq)
    got = _allocation(lines)
    assert got.n_shared == 5 and got.shared["alone:SPY"][0] == 5     # the same five dates
    for table in (got.shared, got.union):
        assert table["alone:SPY"][3:] == [-80.0, 100.0]     # worst single trade -80, worst fall 100
        assert table["alone:SPY"][1] == pytest.approx(4.0)


# -- three walks: the rule is the highest score among N, the shared dates those of ALL N -----------
#
#            scores E / max loss (and the P&L)
#   date   SPY condor (x400)      QQQ put spread (x240)    IWM put spread (x240)
#   A      80 / 400 = 0.2  +100   36 / 240 = 0.15   +60     30 / 240 = 0.125  -100
#   B      60 / 400 = 0.15 -300   48 / 240 = 0.2    -240    24 / 240 = 0.1    +60
#   C      40 / 400 = 0.1  +100   24 / 240 = 0.1    +60     24 / 240 = 0.1    +50    (three-way tie)
#   D      (no entry)             48 / 240 = 0.2    +60     12 / 240 = 0.05   -20
THREE_FOLDS = {
    "SPY": [_entry(A, 80.0, _cell(SYMMETRIC_CONDOR, 100.0, 100.0)),
            _entry(B, 60.0, _cell(SYMMETRIC_CONDOR, 100.0, -300.0)),
            _entry(C, 40.0, _cell(SYMMETRIC_CONDOR, 100.0, 100.0)),
            _entry(D, -5.0, _skipped())],
    "QQQ": [_entry(A, 36.0, _cell(PUT_SPREAD_STRIKES, 60.0, 60.0), instrument="QQQ"),
            _entry(B, 48.0, _cell(PUT_SPREAD_STRIKES, 60.0, -240.0), instrument="QQQ"),
            _entry(C, 24.0, _cell(PUT_SPREAD_STRIKES, 60.0, 60.0), instrument="QQQ"),
            _entry(D, 48.0, _cell(PUT_SPREAD_STRIKES, 60.0, 60.0), instrument="QQQ")],
    "IWM": [_entry(A, 30.0, _cell(PUT_SPREAD_STRIKES, 60.0, -100.0), instrument="IWM"),
            _entry(B, 24.0, _cell(PUT_SPREAD_STRIKES, 60.0, 60.0), instrument="IWM"),
            _entry(C, 24.0, _cell(PUT_SPREAD_STRIKES, 60.0, 50.0), instrument="IWM"),
            _entry(D, 12.0, _cell(PUT_SPREAD_STRIKES, 60.0, -20.0), instrument="IWM")],
}


@pytest.fixture(scope="module")
def three(tmp_path_factory):
    root = tmp_path_factory.mktemp("three")
    return {"spy": _walk(root, "spy", [_report(CONDOR, THREE_FOLDS["SPY"])]),
            "qqq": _walk(root, "qqq", [_report(PUT_SPREAD, THREE_FOLDS["QQQ"])]),
            "iwm": _walk(root, "iwm", [_report(PUT_SPREAD, THREE_FOLDS["IWM"])])}


def test_three_walks_take_the_best_score_of_three_on_the_dates_all_three_entered(three, capsys):
    # shared: A, B, C (SPY did not enter D). allocate: A SPY +100, B QQQ -240, C the three-way tie
    # goes to the first argument, SPY +100. equal: (100 + 60 - 100) / 3 = 20, (-300 - 240 + 60) / 3
    # = -160, (100 + 60 + 50) / 3 = 70
    code, lines, _ = _run(capsys, "allocate", three["spy"], three["qqq"], three["iwm"])
    assert code == 0
    got = _allocation(lines)
    assert got.n_shared == 3
    _assert_table(got.shared, {
        "allocate": ([100.0, -240.0, 100.0], -40 / 3, -240.0, 240.0),
        "equal": ([20.0, -160.0, 70.0], -70 / 3, -160.0, 160.0),
        "alone:SPY": ([100.0, -300.0, 100.0], -100 / 3, -300.0, 300.0),
        "alone:QQQ": ([60.0, -240.0, 60.0], -40.0, -240.0, 240.0),
        "alone:IWM": ([-100.0, 60.0, 50.0], 10 / 3, -100.0, 100.0)})
    # the union adds D, which QQQ and IWM entered: allocate takes QQQ (0.2 > 0.05) for +60, equal
    # (60 - 20) / 2 = 20; SPY alone has no D
    _assert_table(got.union, {
        "allocate": ([100.0, -240.0, 100.0, 60.0], 5.0, -240.0, 240.0),
        "equal": ([20.0, -160.0, 70.0, 20.0], -50.0 / 4, -160.0, 160.0),
        "alone:SPY": ([100.0, -300.0, 100.0], -100 / 3, -300.0, 300.0),
        "alone:QQQ": ([60.0, -240.0, 60.0, 60.0], -15.0, -240.0, 240.0),
        "alone:IWM": ([-100.0, 60.0, 50.0, -20.0], -2.5, -100.0, 100.0)})
    assert [row[:5] for row in got.walks] == [["SPY", "1", "3", A, C], ["QQQ", "1", "4", A, D],
                                              ["IWM", "1", "4", A, D]]


def test_the_three_way_tie_goes_to_whichever_walk_is_named_first(three, capsys):
    _, lines, _ = _run(capsys, "allocate", three["iwm"], three["qqq"], three["spy"])
    got = _allocation(lines)
    # C is now IWM's +50; A (SPY 0.2) and B (QQQ 0.2) are unchanged by the order
    _assert_table(got.shared, {
        "allocate": ([100.0, -240.0, 50.0], -30.0, -240.0, 240.0),
        "equal": ([20.0, -160.0, 70.0], -70 / 3, -160.0, 160.0),
        "alone:IWM": ([-100.0, 60.0, 50.0], 10 / 3, -100.0, 100.0),
        "alone:QQQ": ([60.0, -240.0, 60.0], -40.0, -240.0, 240.0),
        "alone:SPY": ([100.0, -300.0, 100.0], -100 / 3, -300.0, 300.0)})


def test_a_date_one_of_three_walks_lacks_is_not_shared_however_many_of_the_others_entered(
    three, capsys
):
    # D: QQQ and IWM entered, SPY did not: two of three is not shared, so n_shared stays 3
    _, lines, _ = _run(capsys, "allocate", three["qqq"], three["iwm"])       # only those two
    assert _allocation(lines).n_shared == 4                   # ... who DO share D
    _, lines, _ = _run(capsys, "allocate", three["spy"], three["qqq"], three["iwm"])
    assert _allocation(lines).n_shared == 3


# -- the score divides by the widest vertical, not by the credit and not by the call wing --------

WIDE_PUT_CONDOR = [85.0, 95.0, 105.0, 110.0]            # wings 10 (put) and 5 (call)


def test_the_choice_follows_expected_pnl_over_max_loss_not_over_the_credit_or_the_call_wing(
    tmp_path, capsys
):
    # one date. SPY: a symmetric condor, credit 100, E_P 25: 25 / (500 - 100) = 0.0625.
    # QQQ: a condor whose PUT wing is the wide one, credit 50, E_P 40: its max loss is
    # 100 x 10 - 50 = 950, so 40 / 950 = 0.0421. Other rules pick QQQ: E_P / credit says 0.25 vs
    # 0.8; dividing by the call wing (5) gives 40 / 450 = 0.0889 > 0.0625. Only the true rule
    # takes SPY's +100 (QQQ's settlement cost it the full 950)
    spy = _walk(tmp_path, "spy", [_report(CONDOR, [
        _entry(A, 25.0, _cell(SYMMETRIC_CONDOR, 100.0, 100.0))])])
    qqq = _walk(tmp_path, "qqq", [_report(CONDOR, [
        _entry(A, 40.0, _cell(WIDE_PUT_CONDOR, 50.0, -950.0), instrument="QQQ")])])
    (cell,) = ledger_studies.WalkLedger(qqq).cells("model")
    assert cell.max_loss_usd == 950.0 and cell.score == pytest.approx(40 / 950, rel=1e-12)
    assert 40 / 50 > 25 / 100 and 40 / 450 > 25 / 400                 # the wrong rules' winner: QQQ
    _, lines, _ = _run(capsys, "allocate", spy, qqq)
    got = _allocation(lines)
    assert got.n_shared == 1
    assert got.shared["allocate"][1] == 100.0 and got.shared["equal"][1] == pytest.approx(-425.0)
    # and the wide wing is the PUT's whichever way round the condor lists its widths
    (mirror,) = _scores(tmp_path, [_entry(A, 40.0, _cell([90.0, 95.0, 105.0, 115.0], 50.0, 0.0))])
    assert mirror == pytest.approx(40 / 950, rel=1e-12)


# -- the debit structures: the same reader, scored by the debit --------------------------------------

LONG_STRADDLE = "archived_quote_long_straddle_backtest"
LONG_CALL_SPREAD = "archived_quote_long_call_spread_backtest"
LONG_PUT_SPREAD = "archived_quote_long_put_spread_backtest"
STRADDLE_STRIKES = [100.0, 100.0]                      # one strike, listed once per leg
LONG_CALLS = [100.0, 105.0]                            # the bought call under the sold one
LONG_PUTS = [95.0, 100.0]                              # the SOLD put under the bought one


@pytest.mark.parametrize("kind, strikes, credit, multiplier, want", [
    (LONG_STRADDLE, STRADDLE_STRIKES, -440.0, 100, 440.0),      # a debit is a negative credit
    (LONG_CALL_SPREAD, LONG_CALLS, -130.0, 100, 130.0),
    (LONG_PUT_SPREAD, LONG_PUTS, -120.0, 100, 120.0),
    (LONG_CALL_SPREAD, LONG_CALLS, -13.0, 10, 13.0),
])
def test_a_long_structures_max_loss_is_its_debit_and_its_score_follows(
    tmp_path, kind, strikes, credit, multiplier, want
):
    (score,) = _scores(tmp_path, [_entry(A, 20.0, _cell(strikes, credit, 0.0))], kind, multiplier)
    assert score == pytest.approx(20.0 / want, rel=1e-12)


# -- hedge: does a bought-convexity sleeve beat a SMALLER core at the same CVaR5? ------------------
#
# The core is a SPY condor walk, the sleeve a SPY long-straddle walk; both entered the model book
# on A, B and D. C is the core's alone (the sleeve's model skipped it), E the sleeve's alone.
#
#   date   core pnl   sleeve pnl        joined on A, B, D:
#   A      +100       -60               core          [100, -350, 100]   mean -50      CVaR5 -350
#   B      -350       +200              sleeve        [-60, 200, -60]    mean 26.667   CVaR5  -60
#   C      +100       (not entered)     core+sleeve   [40, -150, 40]     mean -23.333  CVaR5 -150
#   D      +100       -60               k = -150 / -350 = 3/7
#   E      (no entry) +300              k x core      [300/7, -150, 300/7]  mean -21.4286  CVaR5 -150
#
# core+sleeve's mean (-23.33) is BELOW k x core's (-21.43): the sleeve costs more than the smaller
# core gives up, so it does not earn its place. A cheaper sleeve (-20 / +300 / -20) gives
# core+sleeve [80, -50, 80] (mean 36.667, CVaR5 -50, k = 1/7) against k x core's mean -7.143: it does.
E = "2024-04-04"
STRADDLE_CELL = STRADDLE_STRIKES, -440.0


def _straddle(day, pnl, entered=True):
    cell = _cell(*STRADDLE_CELL, pnl) if entered else _skipped()
    return _entry(day, 10.0, cell)


CORE_ENTRIES = [_entry(A, 80.0, _cell(SYMMETRIC_CONDOR, 100.0, 100.0)),
                _entry(B, 120.0, _cell(LOPSIDED_CONDOR, 150.0, -350.0)),
                _entry(C, 80.0, _cell(SYMMETRIC_CONDOR, 100.0, 100.0)),
                _entry(D, 80.0, _cell(SYMMETRIC_CONDOR, 100.0, 100.0))]


def _sleeve_entries(a, b, d, e=300.0):
    return [_straddle(A, a), _straddle(B, b), _straddle(C, 0.0, entered=False),
            _straddle(D, d), _straddle(E, e)]


@pytest.fixture(scope="module")
def hedge_walks(tmp_path_factory):
    root = tmp_path_factory.mktemp("hedge")
    return {"core": _walk(root, "core", [_report(CONDOR, CORE_ENTRIES[:2]),
                                         _report(CONDOR, CORE_ENTRIES[2:])]),
            "sleeve": _walk(root, "sleeve", [_report(LONG_STRADDLE, _sleeve_entries(-60, 200, -60))]),
            "cheap": _walk(root, "cheap", [_report(LONG_STRADDLE, _sleeve_entries(-20, 300, -20))])}


def _hedge(lines):
    """Parse ``hedge``'s output: the facts, the table and the one verdict line."""
    blocks = _blocks(lines)
    assert len(blocks) == 3, blocks
    facts, table, verdict = blocks
    assert len(verdict) == 1 and verdict[0].startswith("verdict: "), verdict

    def fact(prefix):
        return next(line for line in facts if line.startswith(prefix))

    header, rows = _table(table)
    return SimpleNamespace(facts=facts, header=header, table=rows, verdict=verdict[0],
                           joined=int(fact("joined: ").split()[1]),
                           k=float(fact("k: ").split()[1]))


def test_the_hedge_table_by_hand_when_the_sleeve_does_not_earn_its_place(hedge_walks, capsys):
    code, lines, err = _run(capsys, "hedge", hedge_walks["core"], hedge_walks["sleeve"])
    assert code == 0 and err == ""
    got = _hedge(lines)
    assert got.header == HEADER and got.joined == 3
    assert got.k == pytest.approx(3 / 7, abs=5e-5)                   # -150 / -350
    _assert_table(got.table, {
        "core+sleeve": ([40.0, -150.0, 40.0], -70 / 3, -150.0, 150.0),
        "k*core": ([300 / 7, -150.0, 300 / 7], -150 / 7, -150.0, 150.0),
        "core": ([100.0, -350.0, 100.0], -50.0, -350.0, 350.0),
        "sleeve": ([-60.0, 200.0, -60.0], 80 / 3, -60.0, 60.0)})
    assert list(got.table) == ["core+sleeve", "k*core", "core", "sleeve"]      # the printed order
    # -23.33 < -21.43: the smaller core loses less than the hedged one earns, so: no
    assert got.verdict.startswith("verdict: ") and "does not earn its place" in got.verdict
    assert "-23.33" in got.verdict and "-21.43" in got.verdict


def test_the_hedge_verdict_turns_when_a_cheaper_sleeve_beats_the_smaller_core(hedge_walks,
                                                                              capsys):
    code, lines, _ = _run(capsys, "hedge", hedge_walks["core"], hedge_walks["cheap"])
    assert code == 0
    got = _hedge(lines)
    assert got.k == pytest.approx(1 / 7, abs=5e-5)                   # -50 / -350
    _assert_table(got.table, {
        "core+sleeve": ([80.0, -50.0, 80.0], 110 / 3, -50.0, 50.0),
        "k*core": ([100 / 7, -50.0, 100 / 7], -50 / 7, -50.0, 50.0),
        "core": ([100.0, -350.0, 100.0], -50.0, -350.0, 350.0),
        "sleeve": ([-20.0, 300.0, -20.0], 260 / 3, -20.0, 20.0)})
    assert "earns its place" in got.verdict and "does not" not in got.verdict
    assert "36.67" in got.verdict and "-7.14" in got.verdict


def test_a_sleeve_that_adds_nothing_ties_the_smaller_core_and_does_not_earn_its_place(tmp_path,
                                                                                       capsys):
    # the sleeve earns its place only if core+sleeve EXCEEDS k x core, so equal means are not enough:
    # a sleeve of exact zeros leaves core+sleeve = core, k = 1.0 and k x core = core, tie on the mean
    core = _walk(tmp_path, "core", [_report(CONDOR, CORE_ENTRIES[:2])])
    sleeve = _walk(tmp_path, "sleeve", [_report(LONG_STRADDLE, [_straddle(A, 0.0),
                                                                _straddle(B, 0.0)])])
    code, lines, _ = _run(capsys, "hedge", core, sleeve)
    assert code == 0
    got = _hedge(lines)
    assert got.k == 1.0 and got.table["core+sleeve"][1] == got.table["k*core"][1] == -125.0
    assert "does not earn its place" in got.verdict and "does not exceed" in got.verdict


def test_k_times_the_core_has_the_combined_books_tail_by_construction(hedge_walks, capsys):
    # k = CVaR5(core+sleeve) / CVaR5(core): scaling the core by it gives the core+sleeve tail, so the
    # two printed CVaR5 cells agree and the comparison is at ONE tail risk
    for sleeve in ("sleeve", "cheap"):
        _, lines, _ = _run(capsys, "hedge", hedge_walks["core"], hedge_walks[sleeve])
        table = _hedge(lines).table
        assert table["k*core"][3] == pytest.approx(table["core+sleeve"][3], abs=0.005)


def test_dates_only_one_walk_entered_are_counted_and_printed_not_joined(hedge_walks, capsys):
    _, lines, _ = _run(capsys, "hedge", hedge_walks["core"], hedge_walks["sleeve"])
    got = _hedge(lines)
    only_core = next(ln for ln in got.facts if ln.startswith("only in core"))
    only_sleeve = next(ln for ln in got.facts if ln.startswith("only in sleeve"))
    assert only_core == f"only in core (1): {C}" and only_sleeve == f"only in sleeve (1): {E}"
    # neither date enters a series: the core's +100 on C and the sleeve's +300 on E are not joined
    assert all(row[0] == 3 for row in got.table.values())
    assert got.table["core"][1] == pytest.approx(-50.0, abs=0.005)


def test_the_hedge_reads_the_always_book_when_asked(tmp_path, capsys):
    # the model skips B on both walks; the always book trades it. Model: joined A only (core +100,
    # sleeve -60), so the core has NO tail loss and the study refuses; always: joined A, B
    core = _walk(tmp_path, "core", [_report(CONDOR, [
        _entry(A, 80.0, _cell(SYMMETRIC_CONDOR, 100.0, 100.0)),
        _entry(B, -5.0, _skipped(), always=_cell(SYMMETRIC_CONDOR, 100.0, -400.0))])])
    sleeve = _walk(tmp_path, "sleeve", [_report(LONG_STRADDLE, [
        _straddle(A, -60.0),
        _entry(B, -5.0, _skipped(), always=_cell(*STRADDLE_CELL, 300.0))])])
    code, lines, _ = _run(capsys, "hedge", core, sleeve)
    assert code == 1 and lines[0].startswith("error: ") and "tail" in lines[0]
    code, lines, _ = _run(capsys, "hedge", core, sleeve, "--book", "always")
    assert code == 0
    got = _hedge(lines)
    # core [100, -400], sleeve [-60, 300]: combined [40, -100]; worst single -400 / -100: k = 0.25
    assert got.joined == 2 and got.k == pytest.approx(0.25)
    _assert_table(got.table, {
        "core+sleeve": ([40.0, -100.0], -30.0, -100.0, 100.0),
        "k*core": ([25.0, -100.0], -37.5, -100.0, 100.0),
        "core": ([100.0, -400.0], -150.0, -400.0, 400.0),
        "sleeve": ([-60.0, 300.0], 120.0, -60.0, 60.0)})
    assert "earns its place" in got.verdict                   # -30 > -37.5


def test_the_hedge_tail_is_the_backtests_default_and_holds_more_than_one_value_past_twenty(
    tmp_path, capsys
):
    # 21 joined dates: the 5 % tail holds ceil(1.05) = 2 values. core [-100, -80, +10 x 19] has
    # CVaR5 -90; the sleeve [+50, +10, -1 x 19] makes core+sleeve [-50, -70, +9 x 19], CVaR5 -60:
    # k = 60 / 90 = 2/3. (A one-value tail would say -100 and -70: k = 0.7.)
    days = [f"2024-04-{d:02d}" for d in range(1, 22)]
    core_pnl = [-100.0, -80.0] + [10.0] * 19
    sleeve_pnl = [50.0, 10.0] + [-1.0] * 19
    core = _walk(tmp_path, "core", [_report(CONDOR, [
        _entry(d, 80.0, _cell(SYMMETRIC_CONDOR, 100.0, p)) for d, p in zip(days, core_pnl)])])
    sleeve = _walk(tmp_path, "sleeve", [_report(LONG_STRADDLE, [
        _straddle(d, p) for d, p in zip(days, sleeve_pnl)])])
    _, lines, _ = _run(capsys, "hedge", core, sleeve)
    got = _hedge(lines)
    assert got.joined == 21 and got.k == pytest.approx(2 / 3, abs=5e-5)
    assert got.table["core"][3] == pytest.approx(-90.0) and got.table["core+sleeve"][3] == -60.0
    assert got.table["k*core"][3] == pytest.approx(-60.0)
    assert got.table["core+sleeve"][1] == pytest.approx(51 / 21, abs=0.005)       # 2.43
    assert got.table["k*core"][1] == pytest.approx(10 / 21 * 2 / 3, abs=0.005)    # 0.32
    assert "earns its place" in got.verdict


# -- hedge refusals: one line, exit 1 ---------------------------------------------------------------


def test_walks_of_two_instruments_are_not_a_hedge_and_the_message_names_both(tmp_path, capsys,
                                                                               hedge_walks):
    qqq = _walk(tmp_path, "qqq", [_report(LONG_STRADDLE, [
        _entry(A, 10.0, _cell(*STRADDLE_CELL, -60.0), instrument="QQQ")])])
    message = _refused(capsys, "hedge", hedge_walks["core"], qqq)
    assert "SPY" in message and "QQQ" in message and "instrument" in message


def test_walks_of_two_buckets_are_not_a_hedge_and_the_message_names_both(tmp_path, capsys,
                                                                          hedge_walks):
    sleeve = _walk(tmp_path, "sleeve", [_report(LONG_CALL_SPREAD, [
        _entry(A, 10.0, _cell(LONG_CALLS, -130.0, 200.0))], dte=(30, 45))])
    message = _refused(capsys, "hedge", hedge_walks["core"], sleeve)
    assert "7-10" in message and "30-45" in message and "bucket" in message


def test_a_core_with_no_tail_loss_leaves_no_loss_to_match_and_refuses(tmp_path, capsys):
    core = _walk(tmp_path, "core", [_report(CONDOR, [
        _entry(A, 80.0, _cell(SYMMETRIC_CONDOR, 100.0, 100.0)),
        _entry(B, 80.0, _cell(SYMMETRIC_CONDOR, 100.0, 0.0))])])        # CVaR5 = 0.0: not negative
    for label, pnls in (("wins", (5.0, 5.0)),          # core+sleeve has no tail loss either
                        ("loses", (-200.0, -200.0))):   # core+sleeve [-100, -200] does: still refused
        sleeve = _walk(tmp_path, f"sleeve-{label}", [_report(LONG_STRADDLE, [
            _straddle(A, pnls[0]), _straddle(B, pnls[1])])])
        message = _refused(capsys, "hedge", core, sleeve)
        assert message.startswith("error: the core has no tail loss to match"), message
        assert "CVaR5 is 0.00" in message


def test_a_sleeve_that_leaves_no_tail_loss_at_all_cannot_be_matched_by_scaling_the_core(
    tmp_path, capsys
):
    # core [100, -350], sleeve [0, +400]: core+sleeve [100, 50] has CVaR5 +50, so k = -1/7: a
    # negative multiple of the core is no smaller core, and the comparison would be meaningless
    core = _walk(tmp_path, "core", [_report(CONDOR, [
        _entry(A, 80.0, _cell(SYMMETRIC_CONDOR, 100.0, 100.0)),
        _entry(B, 120.0, _cell(LOPSIDED_CONDOR, 150.0, -350.0))])])
    sleeve = _walk(tmp_path, "sleeve", [_report(LONG_STRADDLE, [_straddle(A, 0.0),
                                                                _straddle(B, 400.0)])])
    message = _refused(capsys, "hedge", core, sleeve)
    assert "core+sleeve" in message and "tail" in message


def test_walks_with_no_shared_entry_date_are_not_a_hedge(tmp_path, capsys):
    core = _walk(tmp_path, "core", [_report(CONDOR, [
        _entry(A, 80.0, _cell(SYMMETRIC_CONDOR, 100.0, -100.0))])])
    sleeve = _walk(tmp_path, "sleeve", [_report(LONG_STRADDLE, [_straddle(B, 5.0)])])
    message = _refused(capsys, "hedge", core, sleeve)
    assert "no entry date" in message
    empty = _walk(tmp_path, "empty", [_report(LONG_STRADDLE, [])])        # no entry, no instrument
    assert "no entry date" in _refused(capsys, "hedge", core, empty)


def test_a_hedge_needs_exactly_a_core_and_a_sleeve(hedge_walks, capsys):
    for argv in (["hedge", hedge_walks["core"]],
                 ["hedge", hedge_walks["core"], hedge_walks["sleeve"], hedge_walks["cheap"]],
                 ["hedge", hedge_walks["core"], hedge_walks["sleeve"], "--book", "implied"]):
        with pytest.raises(SystemExit) as exit_:
            ledger_studies.main(argv)
        assert exit_.value.code == 2


def test_the_hedge_reads_and_never_writes(hedge_walks, capsys):
    def snapshot():
        return {str(path): (hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_mtime_ns)
                for root in map(Path, hedge_walks.values()) for path in sorted(root.parent.rglob("*"))
                if path.is_file()}

    before = snapshot()
    assert _run(capsys, "hedge", hedge_walks["core"], hedge_walks["sleeve"])[0] == 0
    assert snapshot() == before


# -- a walk that is not fully 'ran' is refused, by allocate and hedge alike -------------------------


def _edit_walk_record(summary_dir, change):
    path = Path(summary_dir) / "walkforward.json"
    record = json.loads(path.read_text())
    change(record)
    path.write_text(json.dumps(record))


@pytest.mark.parametrize("study", ["allocate", "hedge"])
def test_a_walk_whose_summary_state_is_not_ran_refuses(tmp_path, capsys, walks, study):
    spy = _walk(tmp_path, "spy", [_report(CONDOR, SPY_FOLDS[0])])
    _edit_walk_record(spy, lambda record: record.update(state="halted"))
    message = _refused(capsys, study, spy, walks["qqq"])
    assert "halted" in message and "ran" in message


@pytest.mark.parametrize("study", ["allocate", "hedge"])
@pytest.mark.parametrize("state", ["error", "skipped", "halted"])
def test_a_walk_with_a_fold_that_did_not_run_refuses_rather_than_passing_as_a_short_walk(
    tmp_path, capsys, walks, study, state
):
    spy = _walk(tmp_path, "spy", [_report(CONDOR, SPY_FOLDS[0]), _report(CONDOR, SPY_FOLDS[1])])
    _edit_walk_record(spy, lambda record: record["folds"][1].update(state=state))
    message = _refused(capsys, study, spy, walks["qqq"])
    assert state in message and "fold" in message
    assert record_cutoff(spy, 1) in message                     # it names WHICH fold


def record_cutoff(summary_dir, index):
    return json.loads((Path(summary_dir) / "walkforward.json").read_text())["folds"][index]["cutoff"]


def test_a_fold_row_that_is_not_an_object_or_lists_no_run_dir_refuses(tmp_path, capsys, walks):
    spy = _walk(tmp_path, "spy", [_report(CONDOR, SPY_FOLDS[0])])
    _edit_walk_record(spy, lambda record: record["folds"].append("not a fold"))
    assert "fold" in _refused(capsys, "allocate", spy, walks["qqq"])
    spy = _walk(tmp_path, "spy2", [_report(CONDOR, SPY_FOLDS[0])])
    _edit_walk_record(spy, lambda record: record["folds"][0].update(run_dir=None))
    assert "run" in _refused(capsys, "allocate", spy, walks["qqq"])


def test_a_walk_that_lists_no_fold_at_all_refuses(tmp_path, capsys, walks):
    spy = _walk(tmp_path, "spy", [_report(CONDOR, SPY_FOLDS[0])])
    _edit_walk_record(spy, lambda record: record.update(folds=[]))
    assert "no fold that ran" in _refused(capsys, "allocate", spy, walks["qqq"])


# -- refusals the reader owns that no other test reached ------------------------------------------


def test_a_node_record_that_is_not_json_refuses(tmp_path, capsys, walks):
    spy = _walk(tmp_path, "spy", [_report(CONDOR, SPY_FOLDS[0])])
    next(Path(spy).parent.glob("spy-wf-*/nodes/*-backtest.json")).write_text("{not json")
    assert "unreadable" in _refused(capsys, "allocate", spy, walks["qqq"])


@pytest.mark.parametrize("damage", [
    lambda r: r["params"].pop("multiplier"), lambda r: r["params"].pop("dte_min"),
    lambda r: r["params"].pop("dte_max"), lambda r: r["params"].update(multiplier="100"),
    lambda r: r.pop("params"), lambda r: r.pop("ledger"), lambda r: r.update(ledger={"a": 1}),
])
def test_a_report_without_its_params_or_ledger_refuses(tmp_path, capsys, walks, damage):
    report = _report(CONDOR, SPY_FOLDS[0])
    damage(report)
    spy = _walk(tmp_path, "spy", [report])
    assert "lacks dte_min, dte_max, multiplier or its ledger" in _refused(
        capsys, "allocate", spy, walks["qqq"])


@pytest.mark.parametrize("entry", ["not a dict", 7, None, ["date"]])
def test_a_ledger_entry_that_is_not_an_object_is_one_error_line_not_a_traceback(
    tmp_path, capsys, walks, entry
):
    spy = _walk(tmp_path, "spy", [_report(CONDOR, [_entry(A, 80.0, _cell(
        SYMMETRIC_CONDOR, 100.0, 1.0)), entry])])
    message = _refused(capsys, "allocate", spy, walks["qqq"])
    assert "ledger entry" in message


@pytest.mark.parametrize("damage", [
    lambda e: e.pop("date"), lambda e: e.update(date=20240301), lambda e: e.pop("books"),
    lambda e: e.update(books=[]), lambda e: e["books"].pop("model"),
    lambda e: e["books"].update(model="entered"),
])
def test_an_entry_without_a_date_or_the_books_cell_refuses(tmp_path, capsys, walks, damage):
    entry = _entry(A, 80.0, _cell(SYMMETRIC_CONDOR, 100.0, 1.0))
    damage(entry)
    spy = _walk(tmp_path, "spy", [_report(CONDOR, [entry])])
    assert "no date or no 'model' book cell" in _refused(capsys, "allocate", spy, walks["qqq"])


# -- the rules the score is made of --------------------------------------------------------------


def _scores(tmp_path, entries, kind=CONDOR, multiplier=100, book="model"):
    """The study's ex-ante scores of one walk's entered cells, in date order."""
    walk = _walk(tmp_path, "one", [_report(kind, entries, multiplier=multiplier)])
    return [cell.score for cell in ledger_studies.WalkLedger(walk).cells(book)]


def test_max_loss_is_the_multiplier_times_the_widest_vertical_less_the_credit(tmp_path):
    # symmetric condor (width 5), lopsided (widths 5 and 10), put spread and call spread (3):
    # 100 x 5 - 100, 100 x 10 - 150, 100 x 3 - 60, and multiplier 10 over the lopsided wing
    cases = [(CONDOR, SYMMETRIC_CONDOR, 100.0, 80.0, 100, 80 / 400),
             (CONDOR, LOPSIDED_CONDOR, 150.0, 120.0, 100, 120 / 850),
             (PUT_SPREAD, PUT_SPREAD_STRIKES, 60.0, 60.0, 100, 60 / 240),
             (CONDOR, LOPSIDED_CONDOR, 15.0, 12.0, 10, 12 / 85)]
    for kind, strikes, credit, expected, multiplier, want in cases:
        (score,) = _scores(tmp_path, [_entry(A, expected, _cell(strikes, credit, 0.0))], kind,
                           multiplier)
        assert score == pytest.approx(want, rel=1e-12), (kind, strikes, multiplier)


def test_the_selector_structure_comes_from_its_selected_record(tmp_path):
    entries = [_entry(A, 60.0, _cell(PUT_SPREAD_STRIKES, 60.0, 0.0, "put_spread")),
               _entry(B, 50.0, _cell(CALL_SPREAD_STRIKES, 50.0, 0.0, "call_spread")),
               _entry(C, 100.0, _cell(SYMMETRIC_CONDOR, 200.0, 0.0, "condor")),
               _entry(D, 100.0, _cell(LOPSIDED_CONDOR, 200.0, 0.0, "condor"))]
    assert _scores(tmp_path, entries, SELECT) == pytest.approx(
        [60 / 240, 50 / 250, 100 / 300, 100 / 800], rel=1e-12)


def test_the_study_never_restates_a_max_loss_rule_or_a_structure_table():
    # the max-loss rule is contracts.structure_max_loss's and the structures are contracts.STRUCTURES'
    source = Path(ledger_studies.__file__).read_text(encoding="utf-8")
    assert "structure_max_loss" in source and "STRUCTURES" in source
    assert "max(widths)" not in source and "structure_credit" not in source
    for literal in ('"put_spread"', '"call_spread"', '"condor"', "'put_spread'", "'call_spread'",
                    "'condor'"):
        assert literal not in source, literal


# -- refusals: one line, exit 1, nothing printed as a table ----------------------------------------


def _refused(capsys, *argv):
    code, lines, err = _run(capsys, *argv)
    assert code == 1 and err == "" and len(lines) == 1 and lines[0].startswith("error: "), lines
    return lines[0]


def test_walks_of_two_buckets_refuse_and_name_both(tmp_path, capsys):
    spy = _walk(tmp_path, "spy", [_report(CONDOR, SPY_FOLDS[0], dte=(7, 10))])
    qqq = _walk(tmp_path, "qqq", [_report(SELECT, QQQ_FOLDS[0], dte=(30, 45))])
    message = _refused(capsys, "allocate", spy, qqq)
    assert "7-10" in message and "30-45" in message and "bucket" in message


def test_folds_of_one_walk_in_two_buckets_refuse_too(tmp_path, capsys):
    spy = _walk(tmp_path, "spy", [_report(CONDOR, SPY_FOLDS[0], dte=(7, 10)),
                                  _report(CONDOR, SPY_FOLDS[1], dte=(7, 11))])
    qqq = _walk(tmp_path, "qqq", [_report(SELECT, QQQ_FOLDS[0])])
    message = _refused(capsys, "allocate", spy, qqq)
    assert "7-10" in message and "7-11" in message


@pytest.mark.parametrize("pricing, kind", [("vix_proxy", PROXY), ("archived_eod_quotes", PROXY),
                                           ("vix_proxy", CONDOR), ("archived_eod_quotes", "other")])
def test_a_report_that_is_not_an_archived_quote_backtest_refuses(tmp_path, capsys, pricing, kind):
    spy = _walk(tmp_path, "spy", [_report(kind, SPY_FOLDS[0], pricing=pricing)])
    qqq = _walk(tmp_path, "qqq", [_report(SELECT, QQQ_FOLDS[0])])
    assert "archived-quote" in _refused(capsys, "allocate", spy, qqq)


def test_a_missing_report_artifact_refuses(tmp_path, capsys, walks):
    spy = _walk(tmp_path, "spy", [_report(CONDOR, SPY_FOLDS[0])])
    artifacts = [p for p in Path(spy).parent.glob("spy-wf-*/artifacts/json/*.json")]
    assert len(artifacts) == 1
    artifacts[0].unlink()
    assert "missing" in _refused(capsys, "allocate", spy, walks["qqq"])


def test_a_tampered_report_artifact_refuses(tmp_path, capsys, walks):
    spy = _walk(tmp_path, "spy", [_report(CONDOR, SPY_FOLDS[0])])
    artifact = next(Path(spy).parent.glob("spy-wf-*/artifacts/json/*.json"))
    artifact.write_bytes(artifact.read_bytes().replace(b"SPY", b"XXX"))
    assert "JSON artifact" in _refused(capsys, "allocate", spy, walks["qqq"])


def test_a_fold_without_a_backtest_node_record_refuses(tmp_path, capsys, walks):
    spy = _walk(tmp_path, "spy", [_report(CONDOR, SPY_FOLDS[0])])
    record = next(Path(spy).parent.glob("spy-wf-*/nodes/*-backtest.json"))
    record.unlink()
    assert "backtest" in _refused(capsys, "allocate", spy, walks["qqq"])


def test_a_backtest_record_that_did_not_finish_refuses(tmp_path, capsys, walks):
    import json

    spy = _walk(tmp_path, "spy", [_report(CONDOR, SPY_FOLDS[0])])
    record = next(Path(spy).parent.glob("spy-wf-*/nodes/*-backtest.json"))
    body = json.loads(record.read_text())
    assert body["status"] == "ok"                     # the driver's own word for a finished node
    body["status"] = "error"
    record.write_text(json.dumps(body))
    assert "not a finished backtest" in _refused(capsys, "allocate", spy, walks["qqq"])


def test_a_node_record_without_a_report_output_refuses(tmp_path, capsys, walks):
    import json

    spy = _walk(tmp_path, "spy", [_report(CONDOR, SPY_FOLDS[0])])
    record = next(Path(spy).parent.glob("spy-wf-*/nodes/*-backtest.json"))
    body = json.loads(record.read_text())
    del body["outputs"]["report"]
    record.write_text(json.dumps(body))
    assert "report" in _refused(capsys, "allocate", spy, walks["qqq"])


def test_a_directory_that_is_not_a_walk_refuses(tmp_path, capsys, walks):
    assert "walk-forward summary" in _refused(capsys, "allocate", str(tmp_path), walks["qqq"])
    assert "walk-forward summary" in _refused(capsys, "allocate", str(tmp_path / "missing"),
                                              walks["qqq"])


def test_one_walk_is_not_an_allocation(walks, capsys):
    assert "at least two" in _refused(capsys, "allocate", walks["spy"])


def test_two_walks_of_one_instrument_are_not_an_allocation_across_underlyings(tmp_path, capsys):
    one = _walk(tmp_path, "one", [_report(CONDOR, SPY_FOLDS[0])])
    two = _walk(tmp_path, "two", [_report(CONDOR, SPY_FOLDS[1])])
    message = _refused(capsys, "allocate", one, two)
    assert "SPY" in message and "underlying" in message


def test_a_walk_with_two_instruments_or_a_repeated_entry_date_is_ambiguous(
    tmp_path, capsys, walks
):
    mixed = _walk(tmp_path, "mixed", [_report(CONDOR, [
        _entry(A, 80.0, _cell(SYMMETRIC_CONDOR, 100.0, 1.0)),
        _entry(B, 80.0, _cell(SYMMETRIC_CONDOR, 100.0, 1.0), instrument="QQQ")])])
    other = _walk(tmp_path, "iwm", [_report(CONDOR, [
        _entry(A, 80.0, _cell(SYMMETRIC_CONDOR, 100.0, 1.0), instrument="IWM")])])
    assert "more than one instrument" in _refused(capsys, "allocate", mixed, other)
    repeated = _walk(tmp_path, "repeated", [_report(CONDOR, [
        _entry(A, 80.0, _cell(SYMMETRIC_CONDOR, 100.0, 1.0)),
        _entry(A, 80.0, _cell(SYMMETRIC_CONDOR, 100.0, 2.0))])])
    assert A in _refused(capsys, "allocate", repeated, walks["qqq"])


@pytest.mark.parametrize("bad_cell, fragment", [
    (dict(expected=None), "model_expected_pnl_usd"),
    (dict(expected="80"), "model_expected_pnl_usd"),
    (dict(credit=500.0), "max loss"),                     # 100 x 5 - 500: no loss left to risk
    (dict(strikes=[90.0, 95.0, 105.0]), "strikes"),       # three strikes for four legs
    (dict(pnl=None), "pnl_usd"),
])
def test_a_ledger_cell_the_score_cannot_be_formed_from_refuses(
    tmp_path, capsys, walks, bad_cell, fragment
):
    spec = {"expected": 80.0, "credit": 100.0, "strikes": SYMMETRIC_CONDOR, "pnl": 100.0,
            **bad_cell}
    spy = _walk(tmp_path, "spy", [_report(CONDOR, [_entry(
        A, spec["expected"], _cell(spec["strikes"], spec["credit"], spec["pnl"]))])])
    assert fragment in _refused(capsys, "allocate", spy, walks["qqq"])


def test_a_selector_cell_without_its_selected_record_refuses(tmp_path, capsys, walks):
    qqq = _walk(tmp_path, "qqq", [_report(SELECT, [
        _entry(A, 60.0, _cell(PUT_SPREAD_STRIKES, 60.0, 1.0), instrument="QQQ")])])
    assert "selected" in _refused(capsys, "allocate", walks["spy"], qqq)
    unknown = _walk(tmp_path, "unknown", [_report(SELECT, [
        _entry(A, 60.0, _cell(PUT_SPREAD_STRIKES, 60.0, 1.0, "butterfly"), instrument="QQQ")])])
    assert "butterfly" in _refused(capsys, "allocate", walks["spy"], unknown)


# -- the command line -------------------------------------------------------------------------------


def test_the_study_reads_and_never_writes(walks, capsys):
    def snapshot():
        files = {}
        for root in map(Path, walks.values()):
            for path in sorted(root.parent.rglob("*")):
                if path.is_file():
                    files[str(path)] = (hashlib.sha256(path.read_bytes()).hexdigest(),
                                        path.stat().st_mtime_ns)
        return files

    before = snapshot()
    assert _run(capsys, "allocate", walks["spy"], walks["qqq"])[0] == 0
    assert snapshot() == before


def test_the_module_runs_as_a_command_and_reports_exit_codes(walks, tmp_path):
    child = Path(ledger_studies.__file__).resolve().parents[1]
    ran = subprocess.run([sys.executable, "-m", "index_options.ledger_studies", "allocate",
                          walks["spy"], walks["qqq"]], cwd=child, capture_output=True, text=True,
                         timeout=120)
    assert ran.returncode == 0 and ran.stderr == ""
    assert _allocation(ran.stdout.splitlines()).header == HEADER
    refused = subprocess.run([sys.executable, "-m", "index_options.ledger_studies", "allocate",
                              str(tmp_path), walks["qqq"]], cwd=child, capture_output=True,
                             text=True, timeout=120)
    assert refused.returncode == 1 and refused.stdout.startswith("error: ")
    assert len(refused.stdout.splitlines()) == 1


@pytest.mark.parametrize("argv", [[], ["surprise"], ["allocate"], ["allocate", "a", "b", "--book",
                                                                    "implied"],
                                  ["allocate", "a", "b", "--surprise"]])
def test_a_malformed_command_line_is_argparses_usage_error(argv, capsys):
    with pytest.raises(SystemExit) as exit_:
        ledger_studies.main(argv)
    assert exit_.value.code == 2


def test_the_book_choices_are_the_backtests_forecast_books_and_model_is_the_default():
    from index_options.nodes import CondorQuoteBacktest

    assert ledger_studies.AllocationStudy.BOOKS == CondorQuoteBacktest.FORECAST_BOOKS
    assert ledger_studies.AllocationStudy.BOOKS == ("model", "always")
    assert ledger_studies.STUDIES["allocate"] is ledger_studies.AllocationStudy
    assert ledger_studies.STUDIES["hedge"] is ledger_studies.HedgeStudy
    assert ledger_studies.HedgeStudy.BOOKS == ledger_studies.AllocationStudy.BOOKS
    assert ledger_studies.HedgeStudy.DEFAULT_BOOK == ledger_studies.AllocationStudy.DEFAULT_BOOK
    assert set(ledger_studies.STUDIES) == {"allocate", "hedge"}


def test_the_tail_level_is_the_backtests_default_and_the_column_says_five_percent():
    from index_options.nodes import CondorQuoteBacktest

    assert CondorQuoteBacktest.DEFAULTS["cvar_alpha"] == 0.95
    assert ledger_studies.CVAR_ALPHA == CondorQuoteBacktest.DEFAULTS["cvar_alpha"]
    assert ledger_studies.COLUMNS == tuple(HEADER)


def test_the_studys_t_is_the_backtests_own_so_a_noise_constant_series_reads_zero(tmp_path, capsys):
    # one owner of "0.0 for under two values or no variance": a float-noise-constant allocation
    # series must read 0.0 here exactly as it does in a backtest's metrics
    noisy = [1.7000000000000004, 1.7000000000000004, 1.7]
    days = [A, B, C]
    spy = _walk(tmp_path, "spy", [_report(CONDOR, [
        _entry(d, 80.0, _cell(SYMMETRIC_CONDOR, 100.0, p)) for d, p in zip(days, noisy)])])
    qqq = _walk(tmp_path, "qqq", [_report(SELECT, [
        _entry(d, 10.0, _cell(PUT_SPREAD_STRIKES, 60.0, 1.0, "put_spread"), instrument="QQQ")
        for d in days])])
    _, lines, _ = _run(capsys, "allocate", spy, qqq)
    table = _allocation(lines).shared
    assert table["alone:SPY"][2] == 0.0          # spread 4e-16 of the magnitude: no variance
    assert table["alone:QQQ"][2] == 0.0           # three exactly equal trades
