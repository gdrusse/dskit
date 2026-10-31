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


#: Twelve condor P&Ls (the reviewer's reproducer of ADR-0197 A-M1): negative on average, a tail of two.
PROPORTIONAL_CORE = [-117.43, 123.86, -82.21, -147.48, -251.9, -98.6, 126.41, -396.86, 31.01, 51.27,
                     87.4, 7.28]


@pytest.mark.parametrize("factor", [0.3, 2.5, 0.5, -0.1])
def test_a_sleeve_proportional_to_the_core_ties_the_smaller_core_whatever_the_float_noise(
    tmp_path, capsys, factor
):
    # sleeve = factor x core on every date adds nothing: core+sleeve = (1 + factor) x core, its CVaR5
    # is (1 + factor) times the core's, so k = 1 + factor and k x core IS core+sleeve. The two means
    # are equal to the cent and differ only by float noise (1.4e-14 USD at 0.3 and 2.8e-14 at 2.5,
    # where the raw comparison read "earns its place"): at the printed cent it is a tie
    days = [f"2024-05-{d:02d}" for d in range(1, 13)]
    core = _walk(tmp_path, "core", [_report(CONDOR, [
        _entry(d, 80.0, _cell(SYMMETRIC_CONDOR, 100.0, p)) for d, p in zip(days, PROPORTIONAL_CORE)])])
    sleeve = _walk(tmp_path, "sleeve", [_report(LONG_STRADDLE, [
        _straddle(d, factor * p) for d, p in zip(days, PROPORTIONAL_CORE)])])
    code, lines, _ = _run(capsys, "hedge", core, sleeve)
    assert code == 0
    got = _hedge(lines)
    assert got.joined == 12 and got.k == pytest.approx(1 + factor, abs=5e-5)
    assert got.table["core+sleeve"][1] == got.table["k*core"][1]        # the printed means agree
    assert "does not earn its place" in got.verdict and "does not exceed" in got.verdict


def test_the_verdict_compares_the_means_at_the_printed_cent(tmp_path, capsys):
    # core [-100, +100] has CVaR5 -100 (one value in the tail). A sleeve [0, +s] leaves the tail alone,
    # so k = 1 and k x core = core, mean 0.00: core+sleeve's mean is s / 2. A 0.2-cent edge (s =
    # 0.004) prints 0.00 against 0.00 and is no edge; a 2-cent one (s = 0.04) prints 0.02 against 0.00
    core = _walk(tmp_path, "core", [_report(CONDOR, [
        _entry(A, 80.0, _cell(SYMMETRIC_CONDOR, 100.0, -100.0)),
        _entry(B, 80.0, _cell(SYMMETRIC_CONDOR, 100.0, 100.0))])])
    for label, top, earns, printed in (("sub-cent", 0.004, False, "0.00"),
                                       ("cent", 0.04, True, "0.02")):
        sleeve = _walk(tmp_path, f"sleeve-{label}", [_report(LONG_STRADDLE, [
            _straddle(A, 0.0), _straddle(B, top)])])
        code, lines, _ = _run(capsys, "hedge", core, sleeve)
        got = _hedge(lines)
        assert code == 0 and got.k == 1.0, label
        assert got.table["core+sleeve"][1] == float(printed), label
        assert ("earns its place" in got.verdict and "does not" not in got.verdict) is earns, label
        assert f"core+sleeve mean {printed} " in got.verdict, label
        assert "k*core mean 0.00" in got.verdict, label


K_ABOVE_ONE = "note: k > 1: the matched core is LARGER than one contract"


def _notes(got):
    """The note lines among the facts."""
    return [line for line in got.facts if line.startswith("note: ")]


@pytest.mark.parametrize("factor", [0.1, 0.3])
def test_a_k_above_one_says_the_matched_core_is_larger_than_one_contract(tmp_path, capsys, factor):
    # a sleeve that makes the tail WORSE (here it is the core's own P&L, scaled up) needs a core of
    # k > 1 contracts to match: the comparison is against a LARGER core, not the "smaller" one the
    # study is named for, and the line right after k says so
    days = [f"2024-05-{d:02d}" for d in range(1, 13)]
    core = _walk(tmp_path, "core", [_report(CONDOR, [
        _entry(d, 80.0, _cell(SYMMETRIC_CONDOR, 100.0, p)) for d, p in zip(days, PROPORTIONAL_CORE)])])
    sleeve = _walk(tmp_path, "sleeve", [_report(LONG_STRADDLE, [
        _straddle(d, factor * p) for d, p in zip(days, PROPORTIONAL_CORE)])])
    _, lines, _ = _run(capsys, "hedge", core, sleeve)
    got = _hedge(lines)
    assert got.k == pytest.approx(1 + factor, abs=5e-5)
    [note] = _notes(got)
    assert note.startswith(K_ABOVE_ONE)
    assert got.facts.index(note) == next(i for i, ln in enumerate(got.facts)
                                         if ln.startswith("k: ")) + 1


def test_a_k_of_one_or_less_has_no_note_a_sleeve_that_hedges_needs_a_smaller_core(
    hedge_walks, tmp_path, capsys
):
    for sleeve in ("sleeve", "cheap"):                                # k = 3/7 and 1/7
        _, lines, _ = _run(capsys, "hedge", hedge_walks["core"], hedge_walks[sleeve])
        got = _hedge(lines)
        assert got.k < 1 and _notes(got) == []
    core = _walk(tmp_path, "core", [_report(CONDOR, CORE_ENTRIES[:2])])      # k = 1.0 exactly
    sleeve = _walk(tmp_path, "sleeve", [_report(LONG_STRADDLE, [_straddle(A, 0.0),
                                                                _straddle(B, 0.0)])])
    _, lines, _ = _run(capsys, "hedge", core, sleeve)
    got = _hedge(lines)
    assert got.k == 1.0 and _notes(got) == []


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


def test_a_sleeve_that_leaves_a_combined_tail_of_exactly_zero_is_refused_not_scaled_to_zero(
    tmp_path, capsys
):
    # core [100, -350], sleeve [0, +350]: core+sleeve [100, 0], CVaR5 exactly 0.0. "Not negative" is
    # the refusal, so zero refuses like a positive tail: k would be 0 and k x core nothing at all
    core = _walk(tmp_path, "core", [_report(CONDOR, [
        _entry(A, 80.0, _cell(SYMMETRIC_CONDOR, 100.0, 100.0)),
        _entry(B, 120.0, _cell(LOPSIDED_CONDOR, 150.0, -350.0))])])
    sleeve = _walk(tmp_path, "sleeve", [_report(LONG_STRADDLE, [_straddle(A, 0.0),
                                                                _straddle(B, 350.0)])])
    message = _refused(capsys, "hedge", core, sleeve)
    assert message.startswith("error: core+sleeve has no tail loss"), message
    assert "CVaR5 is 0.00" in message


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
    assert set(ledger_studies.STUDIES) == {"allocate", "hedge", "account"}


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


# -- ADR-0258 S3: the read-only account study over a completed robust replay run ------------------
RIGHTS_SIDES = (("LP", "put", "buy"), ("SP", "put", "sell"), ("SC", "call", "sell"),
                ("LC", "call", "buy"))


def _sel(decision, symbol="AAA", day="2025-02-04", expiry="2025-03-07", arm="nominal", value=30.,
         status="trade", prices=(1., 2., 2., 1.), strikes=(90., 95., 105., 110.), fee=0.,
         fill=None, reason="missing_fill_bar"):
    """One saved selection: reservation 300 (width 5 x 100 less credit 200) by default."""
    legs = []
    for (role, right, side), strike, price in zip(RIGHTS_SIDES, strikes, prices):
        leg = {"role": role, "contract": f"{symbol}-{role}-{decision}", "right": right,
               "side": side, "strike": strike, "price_usd_per_share": price, "fee_usd": fee}
        if fill is not None:
            leg["decision_price_usd_per_share"] = price
            leg["price_usd_per_share"] = fill[len(legs)]
        legs.append(leg)
    row = {"decision_id": decision, "arm_id": arm, "symbol": symbol, "quote_date": day,
           "expiry": expiry, "multiplier": 100, "legs": legs, "robust_value_usd": value,
           "status": status, "forecast_settlement_date": expiry}
    if status == "unfilled":
        row["unfilled_reason"] = reason
    return row


def _out(decision, arm="nominal", settle="2025-03-07", pnl=100.):
    return {"decision_id": decision, "arm_id": arm, "settlement_date": settle, "pnl_usd": pnl}


def _replay(root, selections, outcomes, state="ran", name="run"):
    """A completed run directory in the layout the driver and run-report write."""
    run = Path(root) / name
    (run / "artifacts" / "diagnostics").mkdir(parents=True, exist_ok=True)
    (run / "result.json").write_text(json.dumps({
        "name": "fixture", "asof": "2026-01-01", "run_hash": "r" * 8, "state": state,
        "document_hash": "d" * 8}))
    evidence = {"title": "t", "stages": {
        "sizing": {"decisions": selections + [
            {"decision_id": "n", "arm_id": "nominal", "symbol": "ZZZ", "quote_date": "2025-02-04",
             "expiry": "2025-03-07", "status": "no_trade"}]},
        "replay": {"outcomes": outcomes, "skips": []}}}
    (run / "artifacts" / "diagnostics" / "evidence.json").write_text(json.dumps(evidence))
    return str(run)


def _caps(**over):
    return ledger_studies.AdmissionCaps(**{"max_concurrent": 99, "max_per_symbol": 99,
                                           "max_per_date": 99, "max_reserved_fraction": 1.0,
                                           **over})


def _row(rows, arm="nominal", level="1"):
    return next(r for r in rows if (r.arm_id, r.level) == (arm, level))


def _account(tmp_path, selections, outcomes, **caps):
    run = ledger_studies.SavedReplay(_replay(tmp_path, selections, outcomes))
    return ledger_studies.AccountStudy.rows(run, _caps(**caps))


def test_account_priority_is_ex_ante_value_per_reservation_then_symbol(tmp_path):
    wide = dict(strikes=(90., 95., 105., 115.), prices=(1., 2., 2., 1.))     # reservation 800
    selections = [_sel("a", "AAA", value=60.),                               # 60/300 = .2
                  _sel("b", "BBB", value=100., **wide),                      # 100/800 = .125
                  _sel("c", "CCC", value=60.), _sel("d", "DDD", value=30.)]  # tie .2 / .1
    outcomes = [_out(d, pnl=p) for d, p in (("a", 1.), ("b", 2.), ("c", 4.), ("d", 8.))]
    got = _row(_account(tmp_path, selections, outcomes, max_per_date=2))
    # a and c tie on the ratio, symbol ascending: a first, then c; b and d lose on the ratio
    assert (got.admitted, got.pnl_usd, got.rejected) == (2, 5., {"max_per_date": 2})


def test_account_reservation_is_the_decision_max_loss_with_fees_counted_once(tmp_path):
    selections = [_sel("a", fee=.65, value=30.)]
    run = ledger_studies.SavedReplay(_replay(tmp_path, selections, [_out("a")]))
    # credit 100*(2+2-1-1) - 4*.65 = 197.4; most the condor can lose: 500 - 197.4
    assert run.orders[0].reservation == pytest.approx(302.6)
    assert run.orders[0].score == pytest.approx(30. / 302.6)


def test_account_admission_uses_decision_prices_not_the_fill_prices(tmp_path):
    filled = _sel("a", fill=(1.5, 1.5, 1.5, 1.5))      # fill credit 0: a fill-based reservation is 500
    run = ledger_studies.SavedReplay(_replay(tmp_path, [filled], [_out("a")]))
    assert run.orders[0].reservation == pytest.approx(300.)


def test_account_unfilled_order_still_takes_the_date_slot_from_a_lower_ranked_one(tmp_path):
    # A outranks B; A is unfilled at t'.  B must still be rejected: admission is a decision fact.
    selections = [_sel("A", "AAA", value=90., status="unfilled"), _sel("B", "BBB", value=30.)]
    got = _row(_account(tmp_path, selections, [_out("B", pnl=7.)], max_per_date=1))
    assert (got.admitted, got.unfilled, got.rejected, got.pnl_usd) == (
        1, 1, {"max_per_date": 1}, 0.)


@pytest.mark.parametrize("first", ["unfilled", "skipped_downstream"])
def test_account_admitted_unfilled_or_skipped_order_keeps_its_reservation_until_settlement(
        tmp_path, first):
    # A is admitted at its decision; it never settles (no t' fill, or no settlement bar), yet
    # its slot is held to its settlement date: nothing learned after the decision frees it.
    selections = [_sel("A", "AAA", day="2025-02-04",
                       status="unfilled" if first == "unfilled" else "trade"),
                  _sel("B", "BBB", day="2025-02-06"),
                  _sel("C", "CCC", day="2025-03-07", expiry="2025-04-07")]
    outcomes = [_out("B"), _out("C", settle="2025-04-07", pnl=11.)]
    got = _row(_account(tmp_path, selections, outcomes, max_concurrent=1))
    # B is refused while A holds the slot; C, decided after the close of 03-07, is admitted
    assert got.admitted == 2 and got.rejected == {"max_concurrent": 1}
    assert got.unfilled == (first == "unfilled") and got.pnl_usd == 11.


def test_account_releases_at_the_saved_settlement_date_before_that_days_decisions(tmp_path):
    early = _sel("A", "AAA", day="2025-02-04", expiry="2025-03-07")
    same_day = _sel("B", "BBB", day="2025-03-07", expiry="2025-04-07")
    nxt = _sel("C", "CCC", day="2025-03-06", expiry="2025-04-07")
    got = _row(_account(tmp_path, [early, same_day], [_out("A"), _out("B", settle="2025-04-07")],
                        max_concurrent=1))
    assert got.admitted == 2 and got.rejected == {}
    got = _row(_account(tmp_path, [early, nxt], [_out("A"), _out("C")], max_concurrent=1))
    assert got.admitted == 1 and got.rejected == {"max_concurrent": 1}


def test_account_uses_the_outcomes_settlement_date_not_the_nominal_expiry(tmp_path):
    holiday = _sel("A", "AAA", day="2025-03-19", expiry="2025-04-18")      # Good Friday
    later = _sel("B", "BBB", day="2025-04-17", expiry="2025-05-19")
    got = _row(_account(tmp_path, [holiday, later],
                        [_out("A", settle="2025-04-17"), _out("B", settle="2025-05-19")],
                        max_concurrent=1))
    assert got.admitted == 2
    got = _row(_account(tmp_path, [holiday, later],
                        [_out("A", settle="2025-04-18"), _out("B", settle="2025-05-19")],
                        max_concurrent=1))
    assert got.admitted == 1


def test_account_symbol_cap_and_reason_order(tmp_path):
    selections = [_sel("a", "AAA", day="2025-02-04"), _sel("b", "AAA", day="2025-02-05"),
                  _sel("c", "BBB", day="2025-02-05")]
    got = _row(_account(tmp_path, selections, [_out(d) for d in "abc"], max_per_symbol=1))
    assert (got.admitted, got.rejected) == (2, {"max_per_symbol": 1})
    # an order failing several caps is counted once, under the first in the documented order
    got = _row(_account(tmp_path, selections, [_out(d) for d in "abc"], max_concurrent=1,
                        max_per_symbol=1, max_per_date=1))
    assert got.rejected == {"max_concurrent": 2}


def test_account_capital_levels_are_fractions_of_the_nominal_unconstrained_peak(tmp_path):
    selections = [_sel(d, s) for d, s in (("a", "AAA"), ("b", "BBB"), ("c", "CCC"))]
    nominal = [_out(d, pnl=p) for d, p in (("a", 1.), ("b", 2.), ("c", 4.))]
    rows = _account(tmp_path, selections, nominal)
    assert [(r.arm_id, r.level) for r in rows] == [("nominal", "uncapped"), ("nominal", "1"),
                                                   ("nominal", "1/2"), ("nominal", "1/4")]
    assert [r.capital_usd for r in rows] == [None, 900., 450., 225.]
    assert [r.admitted for r in rows] == [3, 3, 1, 0]
    assert [r.pnl_usd for r in rows] == [7., 7., 1., 0.]
    assert rows[2].rejected == {"max_reserved_fraction": 2}
    assert rows[3].rejected == {"max_reserved_fraction": 3}
    assert rows[0].peak_reserved_usd == rows[1].peak_reserved_usd == 900.
    # the fraction of capital allowed to be reserved scales the cap
    rows = _account(tmp_path, selections, nominal, max_reserved_fraction=.5)
    assert [r.admitted for r in rows] == [3, 1, 0, 0]


def test_account_every_arm_is_accounted_on_the_nominal_arms_capital(tmp_path):
    selections = [_sel("a", "AAA"), _sel("b", "BBB"), _sel("a", "AAA", arm="haircut_0"),
                  _sel("b", "BBB", arm="haircut_0"), _sel("c", "CCC", arm="haircut_0")]
    outcomes = [_out("a"), _out("b"), _out("a", "haircut_0"), _out("b", "haircut_0"),
                _out("c", "haircut_0")]
    rows = _account(tmp_path, selections, outcomes)
    assert [(r.arm_id, r.level) for r in rows][:5] == [
        ("nominal", "uncapped"), ("nominal", "1"), ("nominal", "1/2"), ("nominal", "1/4"),
        ("haircut_0", "uncapped")]
    assert _row(rows, "haircut_0", "1").capital_usd == 600.
    assert _row(rows, "haircut_0", "1").admitted == 2
    assert _row(rows, "haircut_0", "1").rejected == {"max_reserved_fraction": 1}


def test_account_the_nominal_arm_is_required_and_named_by_the_caller(tmp_path):
    run = ledger_studies.SavedReplay(_replay(tmp_path, [_sel("a", arm="rho_base")],
                                           [_out("a", "rho_base")]))
    with pytest.raises(ValueError, match="nominal"):
        ledger_studies.AccountStudy.rows(run, _caps())
    assert ledger_studies.AccountStudy.rows(run, _caps(), nominal_arm="rho_base")


def test_account_refuses_a_run_that_did_not_finish_ran(tmp_path):
    for state in ("halted", "error"):
        with pytest.raises(ValueError, match="state"):
            ledger_studies.SavedReplay(_replay(tmp_path, [_sel("a")], [_out("a")], state=state,
                                             name=state))


@pytest.mark.parametrize("damage", ["no_evidence", "no_stage", "dup", "orphan_outcome",
                                    "free_lunch", "outcome_for_unfilled", "bad_json"])
def test_account_refuses_what_it_cannot_account(tmp_path, damage):
    selections, outcomes = [_sel("a")], [_out("a")]
    if damage == "dup":
        selections.append(_sel("a"))
    elif damage == "orphan_outcome":
        outcomes.append(_out("zz"))
    elif damage == "free_lunch":     # a credit above the width: the structure cannot lose
        selections = [_sel("a", prices=(1., 9., 9., 1.))]
    elif damage == "outcome_for_unfilled":
        selections = [_sel("a", status="unfilled")]
    path = Path(_replay(tmp_path, selections, outcomes))
    evidence = path / "artifacts" / "diagnostics" / "evidence.json"
    if damage == "no_evidence":
        evidence.unlink()
    elif damage == "no_stage":
        evidence.write_text(json.dumps({"stages": {}}))
    elif damage == "bad_json":
        evidence.write_text("{")
    with pytest.raises(ValueError):
        ledger_studies.AccountStudy.rows(ledger_studies.SavedReplay(str(path)), _caps())


def test_account_cli_prints_the_table_and_the_not_a_margin_model_line(tmp_path, capsys):
    run = _replay(tmp_path, [_sel("a"), _sel("b", "BBB")], [_out("a"), _out("b")])
    code, lines, _ = _run(capsys, "account", run, "--max-concurrent", "10", "--max-per-symbol", "1",
                          "--max-per-date", "3", "--max-reserved-fraction", "1.0")
    assert code == 0
    text = "\n".join(lines)
    assert "not a margin, mark-to-market or delivery model" in text
    header = next(line for line in lines if line.split()[:2] == ["arm", "level"])
    assert "admitted" in header and "pnl_usd" in header and "max_per_date" in header
    assert sum(1 for line in lines if line.startswith("nominal")) == 4
    assert code == 0 and capsys.readouterr().err == ""


def test_account_cli_refuses_in_one_error_line_and_requires_every_cap(tmp_path, capsys):
    run = _replay(tmp_path, [_sel("a")], [_out("a")], state="halted")
    code, lines, _ = _run(capsys, "account", run, "--max-concurrent", "10", "--max-per-symbol", "1",
                          "--max-per-date", "3", "--max-reserved-fraction", "1.0")
    assert code == 1 and len(lines) == 1 and lines[0].startswith("error: ")
    for argv in (["account", run], ["account", run, "--max-concurrent", "10"]):
        with pytest.raises(SystemExit) as exit_:
            ledger_studies.main(argv)
        assert exit_.value.code == 2
    for bad in ("0", "-1", "x"):
        with pytest.raises(SystemExit):
            ledger_studies.main(["account", run, "--max-concurrent", bad, "--max-per-symbol", "1",
                                 "--max-per-date", "1", "--max-reserved-fraction", "1"])
    for bad in ("0", "-1", "nan", "x"):
        with pytest.raises(SystemExit):
            ledger_studies.main(["account", run, "--max-concurrent", "1", "--max-per-symbol", "1",
                                 "--max-per-date", "1", "--max-reserved-fraction", bad])


def test_account_study_reads_and_never_writes(tmp_path, capsys):
    run = _replay(tmp_path, [_sel("a")], [_out("a")])
    before = {str(p): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in Path(run).rglob("*") if p.is_file()}
    assert _run(capsys, "account", run, "--max-concurrent", "1", "--max-per-symbol", "1",
                "--max-per-date", "1", "--max-reserved-fraction", "1")[0] == 0
    assert before == {str(p): hashlib.sha256(p.read_bytes()).hexdigest()
                      for p in Path(run).rglob("*") if p.is_file()}


def test_account_study_is_registered():
    assert ledger_studies.STUDIES["account"] is ledger_studies.AccountStudy


def test_account_peak_reservation_is_concurrent_not_cumulative(tmp_path):
    # two 300 USD orders that never overlap: a settles before b is decided
    selections = [_sel("a", "AAA", day="2025-02-04", expiry="2025-03-07"),
                  _sel("b", "BBB", day="2025-03-10", expiry="2025-04-10")]
    outcomes = [_out("a", settle="2025-03-07", pnl=1.), _out("b", settle="2025-04-10", pnl=2.)]
    rows = _account(tmp_path, selections, outcomes)
    assert rows[0].peak_reserved_usd == 300.
    assert [r.capital_usd for r in rows] == [None, 300., 150., 75.]
    assert [r.admitted for r in rows] == [2, 2, 0, 0]


def test_account_date_cap_counts_only_admitted_orders(tmp_path):
    # a outranks b on the same date but fails max_per_symbol; the one date slot stays for b
    selections = [_sel("z", "AAA", day="2025-02-03", value=30.),
                  _sel("a", "AAA", day="2025-02-04", value=60.),
                  _sel("b", "BBB", day="2025-02-04", value=30.)]
    got = _row(_account(tmp_path, selections, [_out(d) for d in "zab"],
                        max_per_symbol=1, max_per_date=1))
    assert (got.admitted, got.rejected) == (2, {"max_per_symbol": 1})


def test_account_refuses_a_non_iso_decision_date(tmp_path):
    bad = _sel("a", day="2025-2-4")
    with pytest.raises(ValueError, match="quote_date|date"):
        ledger_studies.SavedReplay(_replay(tmp_path, [bad], [_out("a")]))


def _weekly_portfolio_fixture():
    """Two independent expiries exercise overlapping weekly funded books."""
    import math
    from datetime import date, timedelta

    days = []
    day = date(2019, 1, 2)
    while day < date(2019, 2, 9):
        if day.weekday() < 5:
            days.append(day.isoformat())
        day += timedelta(days=1)
    labels = [str(i) for i in range(13)]
    z = [-3+.5*i for i in range(13)]
    bridge = {"symbol": "X", "start": days[0], "end_before": "2019-02-09",
        "protected_end_before": "2020-01-01", "fold": 1, "require_fitted": False,
        "z_grid": z, "grid_labels": labels, "target_dte": 30, "dte_tolerance": 2,
        "max_abs_z": 2.5, "max_relative_spread": .5, "min_quote_size": 1,
        "stress_spread_fraction": .25, "rho": 0.,
        "projection": {"quadrature_tolerance": 1e-8, "payoff_tolerance": 1e-6,
                       "cdf_tolerance": None, "w1_tolerance": None, "integration_limit": 100},
        "solver": {"multiplier": 100, "fee_per_contract_usd": .65,
                   "tie_tolerance_usd": 1e-6, "max_absolute_gap_usd": 1e-7,
                   "max_relative_gap": 1e-8, "objective_parity_usd": 1e-5,
                   "max_total_contracts": 10}}
    forecasts, contexts, quotes = [], [], []
    for decision, expiry in [("2019-01-02", "2019-02-01"), ("2019-01-07", "2019-02-08")]:
        identity = {"symbol": "X", "quote_date": decision, "expiry": expiry,
                    "fold": 1, "band": "val"}
        forecasts.append({**identity, "fitted": False,
            **{"method_cdf_"+label: .5*(1+math.erf(v/2**.5)) for label, v in zip(labels, z)}})
        contexts.append({**identity, "spot": 100., "reference_scale": .02, "settlement_date": expiry})
        for day in days:
            if decision <= day <= expiry:
                for strike, bid, ask in [(90., .10, .12), (100., 4., 4.2)]:
                    quotes.append({"symbol": "X", "date": day, "expiration": expiry,
                        "type": "put", "strike_raw": strike, "strike": strike,
                        "bid": bid, "ask": ask, "bid_size": 10, "ask_size": 10,
                        "basis_factor": 1., "underlying_price": 100.})
    inputs = {"X": {"forecasts": forecasts, "contexts": contexts, "chain": quotes,
                    "bars": [{"symbol": "X", "date": d, "close": 100.} for d in days]}}
    params = {"symbols": ["X"], "bridge": bridge, "initial": 10000., "deposit": 1000.,
              "deposit_every_days": 14, "weekly_fraction": .25, "correlation_lookback": 60,
              "correlation_min_pairs": 20, "correlation_threshold": .6,
              "solver": {"absolute_gap": 1e-7, "relative_gap": 0., "parity_tolerance": 1e-5,
                  "solver_options": {"mip_abs_gap": 1e-8, "mip_rel_gap": 0., "threads": 1}},
              "methods": [{"id": "nominal", "rho": 0., "portfolio_fraction": 1.,
                           "name_fraction": 1., "component_fraction": None}]}
    return params, inputs


def test_weekly_portfolio_full_pipeline_and_future_isolation():
    """Weekly selection includes far strikes and keeps futures out of first decisions."""
    import copy

    assert hasattr(ledger_studies, "WeeklyOptionPortfolioStudy"), "missing weekly portfolio study"
    params, inputs = _weekly_portfolio_fixture()
    study = ledger_studies.WeeklyOptionPortfolioStudy(params, inputs)
    result = study.run()["methods"][0]
    assert result["contributions"] == 12000.
    assert len(result["trades"]) == 2
    assert result["final_equity"] == pytest.approx(12000.+result["pnl"])
    assert result["peak_open_books"] == 2
    assert result["unbounded_books"] == 0
    assert any(leg["strike"] == 90. for b in result["trades"] for leg in b["legs"])
    changed = copy.deepcopy(inputs)
    for row in changed["X"]["chain"]:
        if row["date"] > "2019-01-02":
            row["bid_size"] = row["ask_size"] = 1
    second = ledger_studies.WeeklyOptionPortfolioStudy(params, changed).run()["methods"][0]
    assert second["decisions"][0]["selection"] == result["decisions"][0]["selection"]
    assert second["unfilled"] > 0
    assert len(second["trades"]) == 1
    assert second["rejected"][0]["decision_date"] == "2019-01-02"



@pytest.mark.parametrize("source,field,value", [
    ("forecasts", "quote_date", "2020-01-02"),
    ("forecasts", "expiry", "2020-01-02"),
    ("contexts", "settlement_date", "2020-01-02"),
    ("chain", "date", "2020-01-02"),
    ("chain", "expiration", "2020-01-02"),
    ("bars", "date", "2020-01-02"),
    ("bars", "date", "2018-12-31"),
])
def test_weekly_portfolio_refuses_protected_intake(source, field, value):
    """The study refuses protected observations before any optimization."""
    params, inputs = _weekly_portfolio_fixture()
    inputs["X"][source][0][field] = value
    with pytest.raises(ValueError):
        ledger_studies.WeeklyOptionPortfolioStudy(params, inputs)


def test_weekly_portfolio_correlations_lag_returns_and_cash_ties():
    """Later prices cannot change earlier components; negative EV permits cash."""
    import copy

    params, inputs = _weekly_portfolio_fixture()
    params["symbols"] = ["X", "Y"]
    inputs["Y"] = copy.deepcopy(inputs["X"])
    for rows in inputs["Y"].values():
        for row in rows:
            row["symbol"] = "Y"
    study = ledger_studies.WeeklyOptionPortfolioStudy(params, inputs)
    early = study._components("2019-01-07")
    assert early["groups"] == [["X", "Y"]]
    assert early["observations"] == 2
    for row in inputs["Y"]["bars"]:
        if row["date"] >= "2019-01-07":
            row["close"] *= 1.5
    later = ledger_studies.WeeklyOptionPortfolioStudy(params, inputs)
    assert later._components("2019-01-07") == early


def test_weekly_portfolio_fill_repricing_refuses_shared_cap():
    """Repricing can refuse all survivors without resizing the fixed order."""
    params, inputs = _weekly_portfolio_fixture()
    study = ledger_studies.WeeklyOptionPortfolioStudy(params, inputs)
    result = study.run()["methods"][0]
    first_count = result["decisions"][0]["books"][0]["contracts"]
    for row in inputs["X"]["chain"]:
        if row["date"] == "2019-01-03":
            if row["strike_raw"] == 100.:
                row["bid"], row["ask"] = .02, .03
    changed = ledger_studies.WeeklyOptionPortfolioStudy(params, inputs).run()["methods"][0]
    assert changed["decisions"][0]["books"][0]["contracts"] == first_count
    assert changed["rejected"][0]["reason"] == "repriced_shared_cap"
    assert all(t["decision_date"] != "2019-01-02" for t in changed["trades"])



def test_weekly_portfolio_per_book_fill_and_component_caps():
    """One failed name leaves the other frozen book intact within joint caps."""
    import copy

    params, inputs = _weekly_portfolio_fixture()
    params["symbols"] = ["X", "Y"]
    params["methods"][0].update(name_fraction=.1, component_fraction=.15)
    inputs["Y"] = copy.deepcopy(inputs["X"])
    for rows in inputs["Y"].values():
        for row in rows:
            row["symbol"] = "Y"
    inputs["X"]["chain"] = [r for r in inputs["X"]["chain"] if r["date"] != "2019-01-03"]
    result = ledger_studies.WeeklyOptionPortfolioStudy(params, inputs).run()["methods"][0]
    first = result["decisions"][0]
    assert {b["symbol"] for b in first["books"]} == {"X", "Y"}
    assert any(b["symbol"] == "Y" and b["decision_date"] == "2019-01-02" for b in result["trades"])
    assert any(b["symbol"] == "X" and b["decision_date"] == "2019-01-02" for b in result["rejected"])
    assert all(s["reserve"] <= .15*s["funding"]+.001 for s in result["curve"])


@pytest.mark.parametrize("expiry_factor", [1., None, 2.])
def test_weekly_portfolio_expiry_basis_does_not_require_option_quote(expiry_factor):
    """Only observed unchanged daily basis permits quote-free expiry settlement."""
    import copy
    params, inputs = _weekly_portfolio_fixture()
    expected = ledger_studies.WeeklyOptionPortfolioStudy(params, inputs).run()["methods"][0]
    for row in inputs["X"]["bars"]:
        row["basis_factor"] = 1.
        if row["date"] == "2019-02-08":
            if expiry_factor is None:
                del row["basis_factor"]
            else:
                row["basis_factor"] = expiry_factor
    inputs["X"]["chain"] = [r for r in inputs["X"]["chain"] if r["date"] != "2019-02-08"]
    before = copy.deepcopy(inputs)
    study = ledger_studies.WeeklyOptionPortfolioStudy(params, inputs)
    if expiry_factor == 1.:
        actual = study.run()["methods"][0]
        assert actual["trades"] == expected["trades"]
        assert actual["pnl"] == expected["pnl"]
        assert actual["contracts"] == expected["contracts"]
        assert actual["final_equity"] == expected["final_equity"]
        assert actual["ledger_summary"]["totals"]["pnl_usd"] == expected["ledger_summary"]["totals"]["pnl_usd"]
        assert not any(key[0] == "2019-02-08" for key in study._quotes["X"])
    else:
        with pytest.raises(ValueError, match="unsettleable"):
            study.run()
    assert inputs == before


@pytest.mark.parametrize("entry", ["constructor", "run", "method"])
def test_weekly_portfolio_validates_solver_policy_before_no_solve(entry, monkeypatch):
    """No-candidate paths cannot bless an invalid declared solver policy."""
    params, inputs = _weekly_portfolio_fixture()
    inputs["X"]["forecasts"] = []
    if entry == "constructor":
        params["solver"]["typo_count_policy"] = "bounded"
        monkeypatch.setattr(ledger_studies._AllQuotedOptionContext, "_bounded",
            lambda *args: (_ for _ in ()).throw(AssertionError("intake before policy validation")))
        with pytest.raises(ValueError, match="typo_count_policy"):
            ledger_studies.WeeklyOptionPortfolioStudy(params, inputs)
    else:
        study = ledger_studies.WeeklyOptionPortfolioStudy(params, inputs)
        study.params["solver"]["typo_count_policy"] = "bounded"
        monkeypatch.setattr(study, "_prepare",
            lambda *args: (_ for _ in ()).throw(AssertionError("work before policy validation")))
        with pytest.raises(ValueError, match="typo_count_policy"):
            study.run() if entry == "run" else study._method(params["methods"][0], None)


@pytest.mark.parametrize("mutation", [
    "genuine", "selected_right", "selected_strike", "selected_sign",
    "quantities", "selected_price", "selected_reserve", "selected_strategy",
    "fill_date", "fill_quantity", "fill_price", "fill_credit", "fill_fees",
    "stress_extra", "settlement_pnl", "close_date", "deposit_date",
    "deposit_amount", "curve_cash", "curve_funding", "curve_equity",
    "curve_open_ids", "curve_reserve", "curve_nav", "curve_drawdown",
    "curve_carried", "ledger_summary", "native_gap", "cleanup_pairs",
])
def test_weekly_portfolio_record_replay_reconciles_full_outcome(mutation, monkeypatch):
    import copy
    from dskit.pipeline.libs.pyomo import TransportPortfolioSolve
    from index_options.ledger_studies import WeeklyOptionPortfolioStudy

    params, inputs = _weekly_portfolio_fixture()
    study = WeeklyOptionPortfolioStudy(params, inputs)
    original = study._method(params["methods"][0], None)
    recorded = copy.deepcopy(original)
    decision = recorded["decisions"][0]
    book = decision["books"][0]
    trade = recorded["trades"][0]
    curve = recorded["curve"][1]
    if mutation.startswith("selected_"):
        field = mutation.removeprefix("selected_")
        if field in ("right", "strike", "sign", "price"):
            value = {"right": "call", "strike": 91., "sign": -1, "price": 17.}[field]
            book["legs"][0][field] = value
            trade["legs"][0][field] = value
        elif field == "reserve":
            book[field] += 1
            decision["selection"]["reserves"][0] += 1
        else:
            book["strategy"]["name"] = "invented"
            trade["strategy"]["name"] = "invented"
    elif mutation == "quantities":
        decision["selection"]["quantities"][0] = [0, 4, 4, 0]
    elif mutation == "fill_date":
        trade["fill_date"] = "2019-01-04"
    elif mutation == "fill_quantity":
        trade["legs"][0]["quantity"] += 1
    elif mutation == "fill_price":
        trade["legs"][0]["fill_price"] += 1
    elif mutation in ("fill_credit", "fill_fees", "stress_extra", "settlement_pnl"):
        field = {"fill_credit": "credit", "fill_fees": "fees",
                 "stress_extra": "stress_extra", "settlement_pnl": "pnl"}[mutation]
        trade[field] += 100000
    elif mutation == "close_date":
        trade["close_date"] = "2019-01-31"
    elif mutation.startswith("deposit_"):
        field = mutation.removeprefix("deposit_")
        recorded["deposits"][0][field] = "2019-01-17" if field == "date" else 2.
    elif mutation.startswith("curve_"):
        field = mutation.removeprefix("curve_")
        field = "carried_books" if field == "carried" else field
        curve[field] = ["invented"] if field == "open_ids" else curve[field] + 1
        if field == "nav":
            recorded["curve"][-1]["nav"] = 2.
            recorded["twr"] = 1.
        if field == "drawdown":
            curve[field] = -.99
            recorded["max_drawdown"] = -.99
    elif mutation == "ledger_summary":
        recorded["ledger_summary"] = {}
    elif mutation == "native_gap":
        decision["certificates"][0]["gap"] = 999.
    elif mutation == "cleanup_pairs":
        decision["selection"]["canonicalization"]["primary"]["pairs"] = "garbage"
    before = copy.deepcopy(recorded)
    monkeypatch.setattr(TransportPortfolioSolve, "run",
                        lambda *a, **k: pytest.fail("record validation must not solve an MIO"))
    if mutation == "genuine":
        assert study._method(params["methods"][0], None, recorded=recorded) == original
    else:
        with pytest.raises(ValueError):
            study._method(params["methods"][0], None, recorded=recorded)
    assert recorded == before


def test_weekly_portfolio_releases_evicted_preparation_arrays(monkeypatch):
    """Older heavy matrices become collectible without changing regenerated candidates."""
    import copy
    import gc
    import weakref
    import numpy as np
    from dskit.pipeline.base import value_hash

    params, inputs = _weekly_portfolio_fixture()
    study = ledger_studies.WeeklyOptionPortfolioStudy(params, inputs)
    node = study._nodes["X"]
    prepare = node._prepare
    calls, arrays = [], []

    def tracked(*args, **kwargs):
        prepared, projection = prepare(*args, **kwargs)
        prepared["grid"] = np.asarray(prepared["grid"], dtype=float)
        calls.append(args[2])
        arrays.append(weakref.ref(prepared["grid"]))
        return prepared, projection

    def fingerprint(result):
        groups, metadata, refusals = result
        plain = [{**g, "grid": np.asarray(g["grid"]).tolist()} for g in groups]
        return value_hash((plain, metadata, refusals))

    monkeypatch.setattr(node, "_prepare", tracked)
    first = study._prepare("2019-01-02")
    expected = fingerprint(first)
    assert study._prepare("2019-01-02") is first
    assert calls == ["2019-01-02"]
    prior_array = arrays[0]
    del first
    second = study._prepare("2019-01-07")
    second_expected = fingerprint(copy.deepcopy(second))
    gc.collect()
    assert prior_array() is None, "past-week preparation matrices remain retained"
    restored = study._prepare("2019-01-02")
    assert fingerprint(restored) == expected
    assert fingerprint(second) == second_expected
    assert calls == ["2019-01-02", "2019-01-07", "2019-01-02"]
    empty = study._prepare("2019-01-14")
    assert empty[0] == [] and empty[1] == []
    assert empty[2] == [{"symbol": "X", "reason": "no_eligible_forecast_expiry"}]
    study._prepare("2019-01-02")
    assert study._prepare("2019-01-14") == empty


def test_weekly_portfolio_failed_preparation_preserves_cached_result(monkeypatch):
    """An unexpected later-date error leaves the last valid preparation reusable."""
    params, inputs = _weekly_portfolio_fixture()
    study = ledger_studies.WeeklyOptionPortfolioStudy(params, inputs)
    first = study._prepare("2019-01-02")
    node = study._nodes["X"]
    prepare = node._prepare

    def fail(*args, **kwargs):
        raise RuntimeError("projection failure")

    monkeypatch.setattr(node, "_prepare", fail)
    with pytest.raises(RuntimeError, match="projection failure"):
        study._prepare("2019-01-07")
    assert study._prepare("2019-01-02") is first
    monkeypatch.setattr(node, "_prepare", prepare)
    assert study._prepare("2019-01-07")[1][0]["expiry"] == "2019-02-08"


def test_weekly_portfolio_full_dte_includes_each_supported_expiry():
    """One date offers both the 30-day and 37-day expiry to the same optimizer."""
    import copy

    params, inputs = _weekly_portfolio_fixture()
    data = inputs["X"]
    for name in ("forecasts", "contexts"):
        row = copy.deepcopy(data[name][1])
        row["quote_date"] = "2019-01-02"
        data[name].append(row)
    data["chain"].extend(
        {**row, "date": "2019-01-02"}
        for row in list(data["chain"])
        if row["date"] == "2019-01-07" and row["expiration"] == "2019-02-08"
    )
    narrow = ledger_studies.WeeklyOptionPortfolioStudy(params, inputs)
    assert [m["dte"] for m in narrow._prepare("2019-01-02")[1]] == [30]
    wide_params = copy.deepcopy(params)
    wide_params["bridge"]["dte_tolerance"] = 30
    wide = ledger_studies.WeeklyOptionPortfolioStudy(wide_params, inputs)
    groups, metadata, refusals = wide._prepare("2019-01-02")
    assert len(groups) == 2 and refusals == []
    assert [(m["expiry"], m["dte"]) for m in metadata] == [
        ("2019-02-01", 30), ("2019-02-08", 37)
    ]


@pytest.mark.parametrize("skip_index", [0, 1])
def test_weekly_portfolio_timeout_skips_orders_and_continues(monkeypatch, skip_index):
    import copy
    import json
    from dataclasses import replace
    from dskit.pipeline.libs.pyomo import TransportPortfolioSolve, SolveRecord

    params, inputs = _weekly_portfolio_fixture()
    params["solver"].update(timeout_policy="return", absolute_gap=50.)
    params["solver"]["solver_options"]["time_limit"] = 900
    original = TransportPortfolioSolve.extract
    calls = []
    def timed(self, model, results):
        index = len(calls)
        calls.append(index)
        if index == skip_index:
            record = self.solve_record
            bound = record.objective + 51.
            self.solve_record = replace(record, termination="maxTimeLimit", status="aborted",
                bound=bound, gap=SolveRecord._gap(record.objective, bound))
        return original(self, model, results)
    monkeypatch.setattr(TransportPortfolioSolve, "extract", timed)
    study = ledger_studies.WeeklyOptionPortfolioStudy(params, inputs)
    progress = []
    result = study._method(params["methods"][0], lambda *v: progress.append(v))
    assert len(calls) == 2 and len(progress) == len(study.weeks)
    assert result["decisions"][skip_index]["status"] == "timeout_skipped"
    assert result["decisions"][skip_index]["books"] == []
    assert len(result["trades"]) == 1
    assert result["trades"][0]["decision_date"] == study.weeks[1-skip_index]
    assert result["contributions"] == 12000. and result["pnl"] > 0.
    assert result["curve"][-1]["open_ids"] == []
    assert result["final_equity"] == pytest.approx(result["contributions"]+result["pnl"])
    monkeypatch.setattr(TransportPortfolioSolve, "run",
        lambda *a: (_ for _ in ()).throw(AssertionError("reoptimization forbidden")))
    saved = json.loads(json.dumps(result))
    assert study._method(params["methods"][0], None, recorded=saved) == saved
    for mutate in (
        lambda r: r["decisions"][skip_index].update(status="cash"),
        lambda r: r["decisions"][skip_index].update(books=[{}]),
        lambda r: r["decisions"][skip_index]["selection"].update(timeout="no_incumbent"),
        lambda r: r["decisions"][skip_index]["certificates"][0].update(termination="optimal"),
    ):
        bad = copy.deepcopy(saved); mutate(bad)
        with pytest.raises(ValueError):
            study._method(params["methods"][0], None, recorded=bad)


def test_weekly_portfolio_reserve_per_contract_default_is_identity():
    """An undeclared or zero capital charge leaves every recorded field unchanged."""
    import copy

    params, inputs = _weekly_portfolio_fixture()
    base = ledger_studies.WeeklyOptionPortfolioStudy(params, inputs).run()["methods"][0]
    explicit = copy.deepcopy(params)
    explicit["methods"][0]["reserve_per_contract"] = 0.
    same = ledger_studies.WeeklyOptionPortfolioStudy(explicit, inputs).run()["methods"][0]
    assert same["decisions"][0]["selection"] == base["decisions"][0]["selection"]
    timeless = lambda d: [{k: v for k, v in c.items() if k != "seconds"} for c in d["certificates"]]
    assert timeless(same["decisions"][0]) == timeless(base["decisions"][0])
    assert same["trades"] == base["trades"] and same["pnl"] == base["pnl"]


def test_weekly_portfolio_reserve_per_contract_floors_every_reserve():
    """A positive charge floors solver, book, fill and ledger reserves and binds the caps."""
    import copy

    params, inputs = _weekly_portfolio_fixture()
    base = ledger_studies.WeeklyOptionPortfolioStudy(params, inputs).run()["methods"][0]
    charged = copy.deepcopy(params)
    charged["methods"][0]["reserve_per_contract"] = 400.
    result = ledger_studies.WeeklyOptionPortfolioStudy(charged, inputs).run()["methods"][0]
    assert result["trades"], "the charged method still trades"
    for book in result["trades"]:
        assert book["reserve"] >= 400.*book["contracts"]-1e-9
        assert book["reserve"] == pytest.approx(max(0., book["max_loss"], -book["credit"], 400.*book["contracts"]))
    for decision in result["decisions"]:
        for book in decision.get("books", []):
            assert book["reserve"] >= 400.*book["contracts"]-1e-9
    assert result["contracts"] < base["contracts"]
    floors = [400.*b["contracts"] for b in result["trades"]]
    assert max(floors)-1e-6 <= result["peak_reserve"] <= sum(floors)+1e-6


@pytest.mark.parametrize("key", ["reserve_per_contract", "short_notional_fraction"])
@pytest.mark.parametrize("charge", [-1., "1", None, float("nan")])
def test_weekly_portfolio_reserve_per_contract_refuses_invalid(key, charge):
    import copy

    params, inputs = _weekly_portfolio_fixture()
    bad = copy.deepcopy(params)
    bad["methods"][0][key] = charge
    with pytest.raises(ValueError, match="invalid fixed method"):
        ledger_studies.WeeklyOptionPortfolioStudy(bad, inputs)


def test_weekly_portfolio_short_notional_fraction_floors_sold_legs_only():
    """The notional floor charges sold strikes, leaves bought legs alone and binds the caps."""
    import copy

    params, inputs = _weekly_portfolio_fixture()
    base = ledger_studies.WeeklyOptionPortfolioStudy(params, inputs).run()["methods"][0]
    charged = copy.deepcopy(params)
    charged["methods"][0]["short_notional_fraction"] = .2
    result = ledger_studies.WeeklyOptionPortfolioStudy(charged, inputs).run()["methods"][0]
    assert result["trades"], "the charged method still trades"
    mult = params["bridge"]["solver"]["multiplier"]
    for book in result["trades"]:
        short_notional = sum(.2*leg["strike"]*mult*leg["quantity"] for leg in book["legs"] if leg["sign"] < 0)
        assert book["reserve"] == pytest.approx(max(0., book["max_loss"], -book["credit"], short_notional))
    assert any(leg["sign"] < 0 for b in result["trades"] for leg in b["legs"])
    assert result["contracts"] < base["contracts"]
    explicit = copy.deepcopy(params)
    explicit["methods"][0]["short_notional_fraction"] = 0.
    same = ledger_studies.WeeklyOptionPortfolioStudy(explicit, inputs).run()["methods"][0]
    assert same["trades"] == base["trades"]


def test_weekly_portfolio_group_screen_prunes_and_replays_with_charges(monkeypatch):
    """A screened, charged method records, replays and refuses a mutated reserve or charge."""
    import copy
    from dskit.pipeline.libs.pyomo import TransportPortfolioSolve

    params, inputs = _weekly_portfolio_fixture()
    params = copy.deepcopy(params)
    data = inputs["X"]
    for name in ("forecasts", "contexts"):
        row = copy.deepcopy(data[name][1])
        row["quote_date"] = "2019-01-02"
        data[name].append(row)
    data["chain"].extend({**row, "date": "2019-01-02"} for row in list(data["chain"])
                         if row["date"] == "2019-01-07" and row["expiration"] == "2019-02-08")
    params["solver"]["group_screen"] = "lp"
    params["bridge"]["dte_tolerance"] = 30  # both expiries compete on 2019-01-02
    params["methods"][0].update(reserve_per_contract=400., short_notional_fraction=.1)
    bound = TransportPortfolioSolve._group_lp_bound
    monkeypatch.setattr(TransportPortfolioSolve, "_group_lp_bound",
                        staticmethod(lambda g: -1. if g["id"].endswith("2019-02-08") else bound(g)))
    study = ledger_studies.WeeklyOptionPortfolioStudy(params, inputs)
    original = study._method(params["methods"][0], None)
    first = original["decisions"][0]
    assert first["candidate_books"] == 2 and first["status"] == "selected"
    assert first["certificates"][0]["variables"] < 2*len(first["selection"]["quantities"][0])
    assert all(b["expiry"] != "2019-02-08" for b in first["books"])
    assert all(t["reserve"] >= 400.*t["contracts"]-1e-9 for t in original["trades"])
    replayed = study._method(params["methods"][0], None, recorded=copy.deepcopy(original))
    assert replayed == original
    bad = copy.deepcopy(original)
    bad["trades"][0]["reserve"] /= 2
    with pytest.raises(ValueError, match="recorded"):
        study._method(params["methods"][0], None, recorded=bad)
    other = copy.deepcopy(params["methods"][0])
    other["reserve_per_contract"] = 0.
    with pytest.raises(ValueError, match="recorded method or decision calendar differs"):
        study._method(other, None, recorded=copy.deepcopy(original))
    plain = copy.deepcopy(params)
    plain["solver"]["group_screen"] = "none"
    with pytest.raises(ValueError, match="recorded model dimensions differ"):
        ledger_studies.WeeklyOptionPortfolioStudy(plain, inputs)._method(
            params["methods"][0], None, recorded=copy.deepcopy(original))


_SHOCK = {"moves": [-.1, .1], "loss_fraction": .08, "beta_lookback": 20}


def _solved(result):
    """Return the decisions that carry a solved selection."""
    return [d for d in result["decisions"] if "selection" in d]


def test_weekly_portfolio_shock_default_is_identity():
    """A method with no shock policy records no shock and replays byte-identically."""
    params, inputs = _weekly_portfolio_fixture()
    first = ledger_studies.WeeklyOptionPortfolioStudy(params, inputs).run()["methods"][0]
    again = ledger_studies.WeeklyOptionPortfolioStudy(params, inputs).run()["methods"][0]
    assert not any("shock" in d for d in first["decisions"])
    timeless = lambda r: [{k: v for k, v in d.items() if k not in ("certificates",)}
                          for d in r["decisions"]]
    assert timeless(first) == timeless(again)
    assert first["trades"] == again["trades"] and first["pnl"] == again["pnl"]


def test_weekly_portfolio_shock_rows_bound_terminal_loss():
    """The shock rows cap the selection's terminal loss at each move; a loose cap changes nothing."""
    import copy

    params, inputs = _weekly_portfolio_fixture()
    base = ledger_studies.WeeklyOptionPortfolioStudy(params, inputs).run()["methods"][0]
    shocked = copy.deepcopy(params)
    shocked["methods"][0]["shock"] = copy.deepcopy(_SHOCK)
    result = ledger_studies.WeeklyOptionPortfolioStudy(shocked, inputs).run()["methods"][0]
    solved = _solved(result)
    assert solved and all("shock" in d for d in solved)
    first = solved[0]["shock"]
    assert first["observations"] < params["correlation_min_pairs"]
    assert first["betas"] == {"X": 1.}
    assert set(first["losses"]) == {"-0.1", "0.1"}
    for decision in solved:
        shock = decision["shock"]
        assert shock["limit"] == pytest.approx(_SHOCK["loss_fraction"]*decision["funding"])
        for loss in shock["losses"].values():
            assert loss <= shock["limit"]+1e-6
    count = lambda r: sum(b["contracts"] for d in r["decisions"] for b in d.get("books", []))
    assert 0 < count(result) < count(base)
    assert max(v for d in solved for v in d["shock"]["losses"].values()) > 0
    loose = copy.deepcopy(params)
    loose["methods"][0]["shock"] = {**_SHOCK, "loss_fraction": 1.}
    free = ledger_studies.WeeklyOptionPortfolioStudy(loose, inputs).run()["methods"][0]
    assert [d["selection"]["quantities"] for d in _solved(free)] == \
        [d["selection"]["quantities"] for d in _solved(base)]


@pytest.mark.parametrize("shock", [
    {"moves": [-.1], "loss_fraction": .1},
    {"moves": [-.1], "loss_fraction": .1, "beta_lookback": 20, "extra": 1},
    {"moves": [], "loss_fraction": .1, "beta_lookback": 20},
    {"moves": [0.], "loss_fraction": .1, "beta_lookback": 20},
    {"moves": [-1.], "loss_fraction": .1, "beta_lookback": 20},
    {"moves": [-.1, -.1], "loss_fraction": .1, "beta_lookback": 20},
    {"moves": [float("nan")], "loss_fraction": .1, "beta_lookback": 20},
    {"moves": [float("inf")], "loss_fraction": .1, "beta_lookback": 20},
    {"moves": [-.1], "loss_fraction": -.1, "beta_lookback": 20},
    {"moves": [-.1], "loss_fraction": 1.5, "beta_lookback": 20},
    {"moves": [-.1], "loss_fraction": .1, "beta_lookback": 1},
    {"moves": [-.1], "loss_fraction": .1, "beta_lookback": True},
    {"moves": [-.1], "loss_fraction": .1, "beta_lookback": 2.5},
    None,
])
def test_weekly_portfolio_shock_refuses_invalid(shock):
    import copy

    params, inputs = _weekly_portfolio_fixture()
    bad = copy.deepcopy(params)
    bad["methods"][0]["shock"] = shock
    with pytest.raises(ValueError, match="invalid fixed method"):
        ledger_studies.WeeklyOptionPortfolioStudy(bad, inputs)


def test_weekly_portfolio_shock_record_replays_and_refuses_tamper():
    """A shocked method replays equal and refuses a mutated loss, limit or beta."""
    import copy

    params, inputs = _weekly_portfolio_fixture()
    params["methods"][0]["shock"] = copy.deepcopy(_SHOCK)
    method = params["methods"][0]
    study = ledger_studies.WeeklyOptionPortfolioStudy(params, inputs)
    original = study._method(method, None)
    assert study._method(method, None, recorded=copy.deepcopy(original)) == original
    def tampered(edit):
        bad = copy.deepcopy(original)
        edit(next(d["shock"] for d in bad["decisions"] if "shock" in d))
        return bad
    edits = [lambda s: s["losses"].update({"-0.1": s["losses"]["-0.1"]+1.}),
             lambda s: s.update(limit=s["limit"]+1.),
             lambda s: s["betas"].update(X=2.)]
    for edit in edits:
        with pytest.raises(ValueError, match="recorded"):
            study._method(method, None, recorded=tampered(edit))


def _two_symbol_fixture():
    """Fixture X plus a copy Y; X wobbles, Y trends, so their return paths differ."""
    import copy

    params, inputs = _weekly_portfolio_fixture()
    params = copy.deepcopy(params)
    params["symbols"] = ["X", "Y"]
    swapped = copy.deepcopy(inputs["X"])
    for rows in swapped.values():
        for row in rows:
            row["symbol"] = "Y"
    inputs["Y"] = swapped
    for k, row in enumerate(inputs["X"]["bars"]):
        row["close"] = 100.+(k % 3)
    for k, row in enumerate(inputs["Y"]["bars"]):
        row["close"] = 100.*(1+.01*k)
    return params, inputs


def test_weekly_portfolio_betas_use_only_earlier_sessions():
    """Betas read the trailing window strictly before the day and average to one."""
    import copy

    params, inputs = _two_symbol_fixture()
    study = ledger_studies.WeeklyOptionPortfolioStudy(params, inputs)
    late = "2019-02-04"
    prior = [d for d in study.calendar if d < late]
    out = study._betas(late, 20)
    assert out["observations"] == min(20, len(prior)-1)
    assert all(math.isfinite(b) for b in out["betas"].values())
    assert sum(out["betas"].values())/2 == pytest.approx(1., abs=1e-9)
    assert out["betas"]["X"] != out["betas"]["Y"]
    early = study._betas("2019-01-07", 20)
    assert early["observations"] < params["correlation_min_pairs"]
    assert early["betas"] == {"X": 1., "Y": 1.}
    moved = copy.deepcopy(inputs)
    for row in moved["Y"]["bars"]:
        if row["date"] >= late:
            row["close"] *= 3.
    other = ledger_studies.WeeklyOptionPortfolioStudy(params, moved)
    assert other._betas(late, 20) == out
    assert other._betas("2019-01-07", 20) == early


def test_weekly_portfolio_shock_rows_apply_betas_to_each_leg():
    """One group, beta 1.5, a sold put spread and a sold call spread, hand-priced at each move."""
    study = ledger_studies.WeeklyOptionPortfolioStudy
    mult, fee = 100, .65
    items = [{"sign": -1, "right": "put", "strike": 95., "price": 2.},
             {"sign": 1, "right": "put", "strike": 90., "price": 1.},
             {"sign": -1, "right": "call", "strike": 105., "price": 1.5},
             {"sign": 1, "right": "call", "strike": 110., "price": .5}]
    credit = [-i["sign"]*i["price"]*mult-fee for i in items]
    meta = [{"id": "g", "symbol": "A", "spot": 100., "items": items}]
    shock = {"moves": [-.1, .1, -.3, -.9], "loss_fraction": .1, "beta_lookback": 5}
    rows = study._shock_rows(shock, {"A": 1.5}, meta, [{"credit": credit}], mult, 1000.)
    assert [r["id"] for r in rows] == ["shock:-0.1", "shock:0.1", "shock:-0.3", "shock:-0.9"]
    # Shocked spots 85, 115, 55 and 0: the spreads lose their full width less
    # the net premium of 2 - 1 + 1.5 - .5 = 2 per share, plus four fees.
    for row in rows:
        assert row["upper"] == 100.
        assert sum(row["terms"]["g"]) == pytest.approx(500.-200.+4*fee)
    unshocked = study._shock_rows({**shock, "moves": [.01]}, {"A": 1.5}, meta,
                                  [{"credit": credit}], mult, 1000.)
    assert sum(unshocked[0]["terms"]["g"]) == pytest.approx(-200.+4*fee)
    assert sum(study._shock_rows(shock, {"A": 0.}, meta, [{"credit": credit}], mult, 1000.)[0]
               ["terms"]["g"]) == pytest.approx(-200.+4*fee)


def test_weekly_portfolio_shock_losses_use_estimated_betas_end_to_end():
    """With betas fitted from two names, every recorded loss equals a leg-by-leg recomputation."""
    import copy

    params, inputs = _two_symbol_fixture()
    params["correlation_min_pairs"] = 2
    for k, row in enumerate(inputs["X"]["bars"]):
        row["close"] = 100.+3.*(k % 2)
    shock = {"moves": [-.1, .1], "loss_fraction": .08, "beta_lookback": 5}
    params["methods"] = [{**params["methods"][0], "shock": shock}]
    study = ledger_studies.WeeklyOptionPortfolioStudy(params, inputs)
    result = study.run()["methods"][0]
    mult = params["bridge"]["solver"]["multiplier"]
    solved = [d for d in result["decisions"] if "shock" in d]
    assert solved and any(d["shock"]["betas"]["Y"] != 1. for d in solved)
    for decision in solved:
        betas, funding = decision["shock"]["betas"], decision["funding"]
        assert decision["shock"]["limit"] == shock["loss_fraction"]*max(0., funding)
        for move in shock["moves"]:
            loss = 0.
            for book in decision["books"]:
                spot = max(0., book["spot"]*(1.+betas[book["symbol"]]*move))
                loss -= book["credit"]
                for leg in book["legs"]:
                    payoff = max((leg["strike"]-spot) if leg["right"] == "put" else (spot-leg["strike"]), 0.)
                    loss += -leg["sign"]*mult*payoff*leg["quantity"]
            assert decision["shock"]["losses"][str(move)] == pytest.approx(loss, abs=1e-6)
            assert loss <= decision["shock"]["limit"]+1e-6
    assert study._method(params["methods"][0], None, recorded=copy.deepcopy(result)) == result

_FILLED = {**_SHOCK, "scope": "filled_account"}


def _account_loss(books, spots, betas, move, mult):
    """Recompute the books' net terminal loss leg by leg at each name's shocked spot."""
    loss = 0.
    for book in books:
        spot = max(0., spots[book["symbol"]]*(1.+betas[book["symbol"]]*move))
        loss -= book["credit"]
        for leg in book["legs"]:
            payoff = max((leg["strike"]-spot) if leg["right"] == "put" else (spot-leg["strike"]), 0.)
            loss -= leg["sign"]*mult*payoff*leg["quantity"]
    return loss


def _filled_run(params, inputs, shock, mutate=None):
    """Run one method under a shock policy; mutate(study) may alter the built study first."""
    import copy

    params = copy.deepcopy(params)
    params["methods"] = [{**params["methods"][0], "shock": copy.deepcopy(shock)}]
    study = ledger_studies.WeeklyOptionPortfolioStudy(params, inputs)
    if mutate is not None:
        mutate(study)
    return study, params["methods"][0], study._method(params["methods"][0], None)


@pytest.mark.parametrize("scope", ["selection", "", None, 1, "FILLED_ACCOUNT", ["filled_account"]])
def test_weekly_portfolio_shock_scope_refuses_invalid(scope):
    import copy

    params, inputs = _weekly_portfolio_fixture()
    bad = copy.deepcopy(params)
    bad["methods"][0]["shock"] = {**_SHOCK, "scope": scope}
    with pytest.raises(ValueError, match="invalid fixed method"):
        ledger_studies.WeeklyOptionPortfolioStudy(bad, inputs)


def test_weekly_portfolio_legacy_shock_keeps_its_exact_record():
    """Three-key shock JSON keeps its record shape, selections and pinned results."""
    params, inputs = _weekly_portfolio_fixture()
    _, _, result = _filled_run(params, inputs, _SHOCK)
    assert not any("shock_admission" in d for d in result["decisions"])
    solved = _solved(result)
    assert [set(d["shock"]) for d in solved] == [{"betas", "observations", "limit", "losses"}]*2
    assert [d["shock"]["losses"] for d in solved] == [
        {"-0.1": pytest.approx(613.3), "0.1": pytest.approx(-386.7)}]*2
    assert [len(d["books"]) for d in solved] == [1, 1]
    assert [t["credit"] for t in result["trades"]] == [pytest.approx(386.7)]*2
    assert result["pnl"] == pytest.approx(773.4)


def test_weekly_portfolio_filled_shock_counts_held_books_in_the_budget():
    """Held fills use their fill credit and the day's close; they shrink the next week's headroom."""
    params, inputs = _weekly_portfolio_fixture()
    mult = params["bridge"]["solver"]["multiplier"]
    _, _, legacy = _filled_run(params, inputs, _SHOCK)
    _, _, result = _filled_run(params, inputs, _FILLED)
    first, second = result["decisions"][:2]
    assert first["shock"]["scope"] == "filled_account"
    assert first["shock"]["held_ids"] == [] and first["shock"]["status"] == "within"
    assert first["shock"]["held"] == {"-0.1": 0., "0.1": 0.}
    assert first["selection"]["quantities"] == legacy["decisions"][0]["selection"]["quantities"]
    held = result["trades"][0]
    assert second["shock"]["held_ids"] == [held["id"]]
    for move in _SHOCK["moves"]:
        expected = _account_loss([held], {"X": 100.}, {"X": 1.}, move, mult)
        assert second["shock"]["held"][str(move)] == pytest.approx(expected, abs=1e-9)
        assert second["shock"]["projected"][str(move)] == pytest.approx(
            expected+second["shock"]["losses"][str(move)], abs=1e-9)
        assert second["shock"]["projected"][str(move)] <= second["shock"]["limit"]+1e-6
    # 613.30 held at -10% leaves 186.70 of the 800 limit: no new 613.30 spread fits.
    assert second["shock"]["held"]["-0.1"] == pytest.approx(613.3)
    assert second["status"] == "cash" and legacy["decisions"][1]["status"] == "selected"
    assert len(result["trades"]) == 1 and len(legacy["trades"]) == 2


def test_weekly_portfolio_filled_shock_admits_an_all_fill_batch():
    """A loose limit admits every fill and records the postfill recomputation at the fill close."""
    params, inputs = _weekly_portfolio_fixture()
    mult = params["bridge"]["solver"]["multiplier"]
    loose = {**_FILLED, "loss_fraction": 1.}
    _, _, legacy = _filled_run(params, inputs, {**_SHOCK, "loss_fraction": 1.})
    _, _, result = _filled_run(params, inputs, loose)
    assert result["trades"] == legacy["trades"] and result["rejected"] == []
    admissions = [d["shock_admission"] for d in result["decisions"] if "shock_admission" in d]
    assert [a["status"] for a in admissions] == ["admissible", "admissible"]
    for admission, trade, held in zip(admissions, result["trades"], [[], result["trades"][:1]]):
        assert admission["date"] == trade["fill_date"]
        assert admission["survivors"] == [trade["id"]] and admission["refusals"] == []
        assert admission["held_ids"] == [b["id"] for b in held]
        assert admission["limit"] == pytest.approx(admission["funding"])
        assert admission["existing_breach"] is False
        for move in _SHOCK["moves"]:
            assert admission["losses"][str(move)] == pytest.approx(
                _account_loss(held+[trade], {"X": 100.}, {"X": 1.}, move, mult), abs=1e-9)


def _hedge_fixture():
    """X sells a put spread, Y buys a cheap far put that hedges it at -20%; Y's fill lacks size."""
    import copy

    params, inputs = _weekly_portfolio_fixture()
    params = copy.deepcopy(params)
    params["symbols"] = ["X", "Y"]
    hedge = copy.deepcopy(inputs["X"])
    for rows in hedge.values():
        for row in rows:
            row["symbol"] = "Y"
    for row in hedge["chain"]:
        if row["strike"] == 90.:
            row["bid"], row["ask"] = .008, .01
            if row["date"] == "2019-01-03":
                row["ask_size"] = 1
        else:
            row["bid"] = 3.6
    for row in inputs["X"]["chain"]:
        if row["strike"] == 100.:
            row["bid"] = 4.1
    inputs["Y"] = hedge
    return params, inputs


def test_weekly_portfolio_filled_shock_declines_a_survivor_whose_hedge_failed():
    """A refused hedge leaves the sold spread over the limit: it is declined, not admitted alone."""
    params, inputs = _hedge_fixture()
    mult = params["bridge"]["solver"]["multiplier"]
    shock = {"moves": [-.2], "loss_fraction": .08, "beta_lookback": 20}
    _, _, legacy = _filled_run(params, inputs, shock)
    first = legacy["decisions"][0]
    assert sorted(b["symbol"] for b in first["books"]) == ["X", "Y"]
    assert any(t["id"].startswith("2019-01-02:X") for t in legacy["trades"])
    _, _, result = _filled_run(params, inputs, {**shock, "scope": "filled_account"})
    decision = result["decisions"][0]
    x_id, y_id = (next(b["id"] for b in decision["books"] if b["symbol"] == s) for s in ("X", "Y"))
    admission = decision["shock_admission"]
    assert admission["survivors"] == [x_id]
    assert admission["refusals"] == [{"id": y_id, "reason": "insufficient_fill_size"}]
    assert admission["status"] == "shock_risk_limit" and admission["existing_breach"] is False
    assert admission["losses"]["-0.2"] > admission["limit"]
    reasons = {r["id"]: r["reason"] for r in result["rejected"]}
    assert reasons[x_id] == "shock_risk_limit" and reasons[y_id] == "insufficient_fill_size"
    declined = next(r for r in result["rejected"] if r["id"] == x_id)
    assert admission["losses"]["-0.2"] == pytest.approx(
        _account_loss([declined], {"X": 100.}, {"X": 1.}, -.2, mult), abs=1e-9)
    assert not any(t["decision_date"] == "2019-01-02" for t in result["trades"])


def test_weekly_portfolio_filled_shock_reprices_at_the_fill_credit():
    """A spread within the limit at signal prices breaches at a worse fill bid and is declined."""
    params, inputs = _weekly_portfolio_fixture()
    for row in inputs["X"]["chain"]:
        if row["date"] == "2019-01-03" and row["expiration"] == "2019-02-01" and row["strike"] == 100.:
            row["bid"] = 3.97
    shock = {**_SHOCK, "loss_fraction": .0615}
    _, _, legacy = _filled_run(params, inputs, shock)
    assert legacy["trades"][0]["decision_date"] == "2019-01-02"
    _, _, result = _filled_run(params, inputs, {**shock, "scope": "filled_account"})
    decision = result["decisions"][0]
    assert decision["shock"]["projected"]["-0.1"] == pytest.approx(613.3)
    assert decision["shock"]["projected"]["-0.1"] <= decision["shock"]["limit"]
    admission = decision["shock_admission"]
    assert admission["losses"]["-0.1"] == pytest.approx(616.3)
    assert admission["status"] == "shock_risk_limit"
    assert result["rejected"][0]["reason"] == "shock_risk_limit"
    assert result["rejected"][0]["credit"] == pytest.approx(383.7)
    assert result["trades"][0]["decision_date"] == "2019-01-07"


def _close(inputs, day, close):
    for row in inputs["X"]["bars"]:
        if row["date"] == day:
            row["close"] = close


def test_weekly_portfolio_filled_shock_held_breach_skips_the_solve():
    """Held books alone over the limit at signal: the week is reported and no batch is solved."""
    params, inputs = _weekly_portfolio_fixture()
    mult = params["bridge"]["solver"]["multiplier"]
    _close(inputs, "2019-01-07", 95.)
    shock = {"moves": [-.05], "loss_fraction": .08, "beta_lookback": 20, "scope": "filled_account"}
    _, _, result = _filled_run(params, inputs, shock)
    held = result["trades"][0]
    second = result["decisions"][1]
    assert second["status"] == "shock_held_breach" and "selection" not in second
    assert second["shock"]["status"] == "held_breach" and second["shock"]["losses"] is None
    assert second["shock"]["held"]["-0.05"] == pytest.approx(
        _account_loss([held], {"X": 95.}, {"X": 1.}, -.05, mult), abs=1e-9)
    assert second["shock"]["held"]["-0.05"] > second["shock"]["limit"]
    assert [t["id"] for t in result["trades"]] == [held["id"]]


def test_weekly_portfolio_filled_shock_reports_an_existing_only_breach_at_fill():
    """Held books breach at the fill close: the new books are declined, the holdings kept."""
    params, inputs = _weekly_portfolio_fixture()
    _close(inputs, "2019-01-08", 95.)
    shock = {"moves": [-.05], "loss_fraction": .08, "beta_lookback": 20, "scope": "filled_account"}
    _, _, result = _filled_run(params, inputs, shock)
    second = result["decisions"][1]
    assert second["status"] == "selected"
    admission = second["shock_admission"]
    assert admission["existing_breach"] is True and admission["status"] == "shock_risk_limit"
    assert admission["held"]["-0.05"] > admission["limit"]
    assert [r["reason"] for r in result["rejected"]] == ["shock_risk_limit"]
    assert [t["decision_date"] for t in result["trades"]] == ["2019-01-02"]


def test_weekly_portfolio_filled_shock_fails_closed_on_an_unusable_close():
    """A nonfinite fill-day close cannot price the survivor: it is declined with its own reason."""
    params, inputs = _weekly_portfolio_fixture()
    def unusable(study):
        study._bars["X"]["2019-01-03"] = math.nan
    _, _, result = _filled_run(params, inputs, _FILLED, unusable)
    admission = result["decisions"][0]["shock_admission"]
    assert admission["status"] == "shock_risk_input"
    assert admission["held"] == {"-0.1": 0., "0.1": 0.} and admission["losses"] is None
    assert result["rejected"][0]["reason"] == "shock_risk_input"
    assert not any(t["decision_date"] == "2019-01-02" for t in result["trades"])


def test_weekly_portfolio_filled_shock_fails_closed_on_an_unusable_held_close():
    """A held book that cannot be priced at the signal close stops the week's solve."""
    params, inputs = _weekly_portfolio_fixture()
    def unusable(study):
        study._bars["X"]["2019-01-07"] = math.nan
    _, _, result = _filled_run(params, inputs, _FILLED, unusable)
    second = result["decisions"][1]
    assert second["status"] == "shock_risk_input" and "selection" not in second
    assert second["shock"]["status"] == "input_unavailable" and second["shock"]["held"] is None
    assert second["shock"]["held_ids"] == [result["trades"][0]["id"]]
    assert len(result["trades"]) == 1


_MISSING = object()


@pytest.mark.parametrize("observed", [_MISSING, None, math.nan, math.inf, 0., -1., 1.5, True])
def test_weekly_portfolio_filled_shock_requires_the_observed_basis(observed):
    """A held book is priced only when the day's observed basis matches its own; else no loss."""
    params, inputs = _weekly_portfolio_fixture()
    study, _, result = _filled_run(params, inputs, _FILLED)
    held = result["trades"][0]
    day = "2019-01-07"
    _, losses = study._account_shock([held], day, _FILLED)
    assert losses is not None
    if observed is _MISSING:
        del study._factors["X"][day]
    else:
        study._factors["X"][day] = observed
    estimate, losses = study._account_shock([held], day, _FILLED)
    assert losses is None and estimate["betas"] == {"X": 1.}


def _without_factor(day):
    def mutate(study):
        del study._factors["X"][day]
    return mutate


def test_weekly_portfolio_filled_shock_fails_closed_on_a_missing_signal_basis():
    """No observed basis at the signal close: the held book is unpriced and nothing is solved."""
    params, inputs = _weekly_portfolio_fixture()
    _, _, result = _filled_run(params, inputs, _FILLED, _without_factor("2019-01-07"))
    second = result["decisions"][1]
    assert second["status"] == "shock_risk_input" and "selection" not in second
    assert second["shock"]["status"] == "input_unavailable" and second["shock"]["held"] is None
    assert len(result["trades"]) == 1


def _without_fill_quotes(day):
    def mutate(study):
        for key in [k for k in study._quotes["X"] if k[0] == day]:
            del study._quotes["X"][key]
    return mutate


def test_weekly_portfolio_filled_shock_reports_an_unpriced_held_book_without_survivors():
    """No survivor does not hide an unpriceable held book: input status, breach not assessed."""
    params, inputs = _weekly_portfolio_fixture()
    loose = {**_FILLED, "loss_fraction": 1.}
    _, _, result = _filled_run(params, inputs, loose, _without_factor("2019-01-08"))
    admission = result["decisions"][1]["shock_admission"]
    assert admission["survivors"] == [] and admission["refusals"][0]["reason"] == "fill_basis_or_session"
    assert admission["held"] is None and admission["losses"] is None
    assert admission["status"] == "shock_risk_input" and admission["existing_breach"] is None


def test_weekly_portfolio_filled_shock_reports_a_held_breach_without_survivors():
    """No survivor does not hide a held-only breach at the fill close."""
    params, inputs = _weekly_portfolio_fixture()
    _close(inputs, "2019-01-08", 95.)
    shock = {"moves": [-.05], "loss_fraction": .08, "beta_lookback": 20, "scope": "filled_account"}
    _, _, result = _filled_run(params, inputs, shock, _without_fill_quotes("2019-01-08"))
    admission = result["decisions"][1]["shock_admission"]
    assert admission["survivors"] == [] and admission["refusals"][0]["reason"] == "missing_fill_quote"
    assert admission["held"]["-0.05"] > admission["limit"]
    assert admission["status"] == "shock_risk_limit" and admission["existing_breach"] is True


def test_weekly_portfolio_filled_shock_reports_no_survivors_within_the_limit():
    """No survivor and a priced held book within the limit is plain no_survivors."""
    params, inputs = _weekly_portfolio_fixture()
    loose = {**_FILLED, "loss_fraction": 1.}
    _, _, result = _filled_run(params, inputs, loose, _without_fill_quotes("2019-01-08"))
    admission = result["decisions"][1]["shock_admission"]
    assert admission["survivors"] == [] and admission["held"] is not None
    assert admission["status"] == "no_survivors" and admission["existing_breach"] is False


def test_weekly_portfolio_filled_shock_record_replays_and_refuses_tamper():
    """Signal and postfill evidence replay equal; a mutated field of either is refused."""
    import copy

    params, inputs = _weekly_portfolio_fixture()
    study, method, original = _filled_run(params, inputs, _FILLED)
    assert study._method(method, None, recorded=copy.deepcopy(original)) == original
    def tampered(edit):
        bad = copy.deepcopy(original)
        edit(next(d for d in bad["decisions"] if "shock_admission" in d))
        return bad
    edits = [lambda d: d["shock"]["held"].update({"-0.1": 1.}),
             lambda d: d["shock"].update(status="held_breach"),
             lambda d: d["shock_admission"]["losses"].update({"-0.1": 0.}),
             lambda d: d["shock_admission"].update(status="shock_risk_limit"),
             lambda d: d["shock_admission"]["survivors"].clear(),
             lambda d: d["shock_admission"].update(limit=0.)]
    for edit in edits:
        with pytest.raises(ValueError, match="recorded"):
            study._method(method, None, recorded=tampered(edit))


def test_weekly_portfolio_accepts_eighteen_symbols():
    """The selected universe is configuration, not a legacy pilot-size limit."""
    import copy
    params, inputs = _weekly_portfolio_fixture()
    params["symbols"] = ["S"+str(i) for i in range(18)]
    expanded = {}
    for symbol in params["symbols"]:
        expanded[symbol] = copy.deepcopy(inputs["X"])
        for rows in expanded[symbol].values():
            for row in rows:
                row["symbol"] = symbol
        expanded[symbol]["forecasts"] = []
    study = ledger_studies.WeeklyOptionPortfolioStudy(params, expanded)
    assert len(study.params["symbols"]) == 18
    assert study.run()["methods"][0]["trades"] == []


def test_funded_account_stops_deposits_at_entry_end():
    """Due contributions on the first settlement-tail session are not credited."""
    from dskit.pipeline.libs.option_ledger import FundedOptionAccount
    account = FundedOptionAccount(10000, 1000, 14, "2019-01-02",
                                  deposit_end_before="2019-01-16")
    account.advance("2019-01-15", {})
    account.advance("2019-02-08", {})
    assert account.contributions == 10000
    assert account.deposits == []

def _packet_clock_fixture():
    params, inputs = _weekly_portfolio_fixture()
    params["correlation_lookback"] = params["correlation_min_pairs"] = 2
    history = ["2018-12-27", "2018-12-28", "2018-12-31"]
    inputs["X"]["bars"] = [{"symbol": "X", "date": d, "close": 100.} for d in history] + inputs["X"]["bars"]
    params["clocks"] = {"sessions": [r["date"] for r in inputs["X"]["bars"]],
        "history_start": history[0], "entry_start": "2019-01-02",
        "entry_end_before": "2019-01-08", "tail_end_before": "2019-02-09"}
    params["selected_pairs"] = [{"id": "X30", "symbol": "X", "dte_min": 28, "dte_max": 32}]
    return params, inputs


def test_weekly_packet_history_entry_tail_and_replay():
    params, inputs = _packet_clock_fixture()
    study = ledger_studies.WeeklyOptionPortfolioStudy(params, inputs)
    assert study.calendar[0] == "2019-01-02"
    assert study.weeks == ["2019-01-02", "2019-01-07"]
    assert study._returns("2019-01-02", 2)[1] == 2
    result = study.run()["methods"][0]
    assert len(result["trades"]) == 1
    assert result["trades"][0]["pair_id"] == "X30"
    assert result["trades"][0]["close_date"] == "2019-02-01"
    assert result["contributions"] == 10000
    assert result["decisions"][-1]["status"] == "no_entry_fill_session"
    assert study._method(params["methods"][0], None, recorded=result) == result


def test_weekly_packet_refuses_calendar_gap_and_overlapping_pairs():
    params, inputs = _packet_clock_fixture()
    inputs["X"]["bars"].pop(2)
    with pytest.raises(ValueError, match="required session"):
        ledger_studies.WeeklyOptionPortfolioStudy(params, inputs)
    params, inputs = _packet_clock_fixture()
    params["selected_pairs"].append({"id": "overlap", "symbol": "X", "dte_min": 32, "dte_max": 40})
    with pytest.raises(ValueError, match="overlap"):
        ledger_studies.WeeklyOptionPortfolioStudy(params, inputs)

def test_weekly_portfolio_standard_split_keeps_frozen_book_economics():
    """An ex-date conversion preserves the entry and routes later marks to live contracts."""
    params, inputs = _weekly_portfolio_fixture()
    baseline = ledger_studies.WeeklyOptionPortfolioStudy(params, inputs).run()["methods"][0]
    effective = "2019-01-30"
    for row in inputs["X"]["chain"]:
        if row["date"] >= effective:
            row["strike_raw"] /= 10
            row["basis_factor"] = 10.0
    for row in inputs["X"]["bars"]:
        row["basis_factor"] = 10.0 if row["date"] >= effective else 1.0
    event = {"symbol": "X", "root_before": "X", "root_after": "X",
             "deliverable_shares": 100, "published": "2018-12-31", "effective": effective,
             "ratio": 10, "strike_divisor": 10, "multiplier": 100,
             "basis_before": 1.0, "basis_after": 10.0}
    with pytest.raises(ValueError, match="unsettleable"):
        ledger_studies.WeeklyOptionPortfolioStudy(params, inputs).run()
    study = ledger_studies.WeeklyOptionPortfolioStudy(params, inputs, adjustments=[event])
    result = study.run()["methods"][0]
    assert result["pnl"] == pytest.approx(baseline["pnl"])
    assert result["contracts"] == baseline["contracts"]
    assert result["fees"] == pytest.approx(baseline["fees"])
    assert len(result["trades"]) == len(baseline["trades"])
    assert any(book.get("applied_adjustments") for book in result["trades"])
    assert result["ledger_summary"]["totals"]["pnl_usd"] == pytest.approx(result["pnl"])


def test_standard_split_conversion_is_atomic_across_open_books():
    """A missing converted quote cannot leave one book converted and the next old."""
    from types import SimpleNamespace
    import copy

    params, inputs = _weekly_portfolio_fixture()
    baseline = ledger_studies.WeeklyOptionPortfolioStudy(params, inputs).run()["methods"][0]
    assert len(baseline["trades"]) == 2
    effective = "2019-01-30"
    for row in inputs["X"]["chain"]:
        if row["date"] >= effective:
            row["strike_raw"] /= 10
            row["basis_factor"] = 10.
    for row in inputs["X"]["bars"]:
        if row["date"] >= effective:
            row["basis_factor"] = 10.
    event = {"symbol": "X", "root_before": "X", "root_after": "X",
             "deliverable_shares": 100, "published": "2018-12-31", "effective": effective,
             "ratio": 10, "strike_divisor": 10, "multiplier": 100,
             "basis_before": 1., "basis_after": 10.}
    study = ledger_studies.WeeklyOptionPortfolioStudy(params, inputs, adjustments=[event])
    books = copy.deepcopy(baseline["trades"])
    account = SimpleNamespace(open={book["id"]: book for book in books})
    expiry = books[1]["expiry"]
    for key in [key for key in study._quotes["X"] if key[0] == effective and key[1] == expiry]:
        del study._quotes["X"][key]
    with pytest.raises(ValueError, match="converted split quote"):
        study._apply_adjustments(account, effective, study._charge(params["methods"][0]))
    assert all("active_legs" not in book and "applied_adjustments" not in book
               for book in account.open.values())
