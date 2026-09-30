"""ADR-0196: the read-only allocation study over finished walk-forward ledgers.

Every walk here is a REAL ``run_walk_forward`` over a synthetic document whose ``backtest`` node
hands a pre-built backtest report through as its JSON artifact, so the on-disk layout the study
reads (``walkforward.json``, ``nodes/NN-backtest.json``, ``artifacts/json/<sha256>.json``) is the
driver's own. Every expected number is worked by hand from the ledgers below.
"""

import hashlib
import math
import subprocess
import sys
from pathlib import Path

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


def _table(lines):
    """Parse the printed table: ``(header tokens, {series: [n, mean, t, cvar, drawdown]})``."""
    header = lines[0].split()
    return header, {row.split()[0]: [int(row.split()[1]), *map(float, row.split()[2:])]
                    for row in lines[1:]}


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
#: SPY first). The 5 % tail of four or fewer values is the single worst one.
MODEL_SPY_FIRST = {
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


def test_the_allocation_table_by_hand(walks, capsys):
    # A: QQQ 0.25 beats SPY 0.2 (+60)   B: QQQ 0.2 beats SPY 0.1412 (-250) -- only because the
    # condor's widest vertical is 10 points, not its narrowest 5   C: QQQ alone (+200)
    # D: 0.1 == 0.1, SPY is the earlier argument (+100)
    code, lines, err = _run(capsys, "allocate", walks["spy"], walks["qqq"])
    assert code == 0 and err == ""
    header, table = _table(lines)
    assert header == HEADER and len(lines) == 5        # one table: the header and four series
    _assert_table(table, MODEL_SPY_FIRST)
    # worked out by hand, not from the oracle: the equal split's mean is (80 - 300 + 200 + 40) / 4
    assert table["equal"][1:] == pytest.approx([5.0, _t([80.0, -300.0, 200.0, 40.0]), -300.0,
                                                300.0], abs=0.005)
    assert table["allocate"][0] == table["equal"][0] == 4       # every date in the union
    assert table["alone:SPY"][0] == 3 and table["alone:QQQ"][0] == 4


def test_a_tie_goes_to_the_earlier_argument(walks, capsys):
    # on D both ex-ante scores are 40 / 400 and 24 / 240, the same rational 0.1 exactly
    _, lines, _ = _run(capsys, "allocate", walks["spy"], walks["qqq"])
    assert _table(lines)[1]["allocate"][1] == pytest.approx(27.5, abs=0.005)      # SPY's +100
    _, lines, _ = _run(capsys, "allocate", walks["qqq"], walks["spy"])
    header, table = _table(lines)
    assert table["allocate"][1] == pytest.approx(-2.5, abs=0.005)                 # QQQ's -20
    assert list(table) == ["allocate", "equal", "alone:QQQ", "alone:SPY"]   # argument order
    _assert_table(table, {
        "allocate": ([60.0, -250.0, 200.0, -20.0], -2.5, -250.0, 250.0),
        "equal": MODEL_SPY_FIRST["equal"], "alone:QQQ": MODEL_SPY_FIRST["alone:QQQ"],
        "alone:SPY": MODEL_SPY_FIRST["alone:SPY"]})


def test_the_always_book_is_read_with_the_models_expectation(walks, capsys):
    # SPY's always book also trades C (-80, scored at the model's E_P -10 over 400): the equal
    # split and SPY alone see it, the allocation still takes QQQ there (0.3333 > -0.025)
    code, lines, _ = _run(capsys, "allocate", walks["spy"], walks["qqq"], "--book", "always")
    assert code == 0
    _assert_table(_table(lines)[1], {
        "allocate": ([60.0, -250.0, 200.0, 100.0], 27.5, -250.0, 250.0),
        "equal": ([80.0, -300.0, 60.0, 40.0], -30.0, -300.0, 300.0),
        "alone:SPY": ([100.0, -350.0, -80.0, 100.0], -57.5, -350.0, 430.0),   # 100 -> -330: 430
        "alone:QQQ": MODEL_SPY_FIRST["alone:QQQ"]})
    _, model_lines, _ = _run(capsys, "allocate", walks["spy"], walks["qqq"], "--book", "model")
    assert _table(model_lines)[1] == _table(_run(capsys, "allocate", walks["spy"],
                                                 walks["qqq"])[1])[1]    # model is the default


def test_dates_only_one_underlying_entered_are_taken_as_they_are(tmp_path, capsys):
    # the union of entry dates: SPY alone on A, QQQ alone on B; nothing to compare, nothing dropped
    spy = _walk(tmp_path, "spy", [_report(CONDOR, [
        _entry(A, 80.0, _cell(SYMMETRIC_CONDOR, 100.0, 100.0))])])
    qqq = _walk(tmp_path, "qqq", [_report(PUT_SPREAD, [
        _entry(B, 60.0, _cell(PUT_SPREAD_STRIKES, 60.0, -40.0), instrument="QQQ")])])
    code, lines, _ = _run(capsys, "allocate", spy, qqq)
    assert code == 0
    _assert_table(_table(lines)[1], {
        "allocate": ([100.0, -40.0], 30.0, -40.0, 40.0),
        "equal": ([100.0, -40.0], 30.0, -40.0, 40.0),
        "alone:SPY": ([100.0], 100.0, 100.0, 0.0),
        "alone:QQQ": ([-40.0], -40.0, -40.0, 40.0)})


def test_series_run_in_date_order_and_a_walk_without_entries_adds_nothing(tmp_path, capsys):
    # the later date listed first in the ledger still comes second in the series; a walk with no
    # entry at all has no instrument to name it by, so its directory does
    spy = _walk(tmp_path, "spy", [_report(CONDOR, [
        _entry(B, 80.0, _cell(SYMMETRIC_CONDOR, 100.0, -300.0)),
        _entry(A, 80.0, _cell(SYMMETRIC_CONDOR, 100.0, 100.0))])])
    qqq = _walk(tmp_path, "qqq", [_report(CONDOR, [], dte=(7, 10))])
    _, lines, _ = _run(capsys, "allocate", spy, qqq)
    table = _table(lines)[1]
    assert table["alone:SPY"][3:] == [-300.0, 300.0]       # 100 then -300: CVaR -300, drawdown 300
    assert table["alone:" + qqq.rsplit("/", 1)[1]][0] == 0      # no entries and no instrument
    assert table["equal"][0] == 2 and table["allocate"][0] == 2


def test_the_drawdown_follows_date_order_not_the_order_of_the_ledger(tmp_path, capsys):
    # 100, -50, -50, 100, -80 by date: the path peaks at 100 and falls to 0 (100), later to 20
    # (80). The ledger lists the dates 3, 1, 5, 2, 4; in that order the path would fall 130
    pnls = {"2024-03-01": 100.0, "2024-03-11": -50.0, "2024-03-19": -50.0, "2024-03-27": 100.0,
            "2024-04-04": -80.0}
    listed = ["2024-03-19", "2024-03-01", "2024-04-04", "2024-03-11", "2024-03-27"]
    spy = _walk(tmp_path, "spy", [_report(CONDOR, [
        _entry(d, 80.0, _cell(SYMMETRIC_CONDOR, 100.0, pnls[d])) for d in listed])])
    qqq = _walk(tmp_path, "qqq", [_report(SELECT, [
        _entry(A, 60.0, _cell(PUT_SPREAD_STRIKES, 60.0, 1.0, "put_spread"), instrument="QQQ")])])
    _, lines, _ = _run(capsys, "allocate", spy, qqq)
    table = _table(lines)[1]
    assert table["alone:SPY"][3:] == [-80.0, 100.0]       # worst single trade -80, worst fall 100
    assert table["alone:SPY"][1] == pytest.approx(4.0)


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


def test_the_study_never_restates_a_width_rule_or_a_structure_table():
    # the width rule is contracts.structure_credit's and the structures are contracts.STRUCTURES'
    source = Path(ledger_studies.__file__).read_text(encoding="utf-8")
    assert "structure_credit" in source and "STRUCTURES" in source
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
    assert _table(ran.stdout.splitlines())[0] == HEADER
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
    assert set(ledger_studies.STUDIES) == {"allocate"}


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
    table = _table(lines)[1]
    assert table["alone:SPY"][2] == 0.0          # spread 4e-16 of the magnitude: no variance
    assert table["alone:QQQ"][2] == 0.0           # three exactly equal trades
