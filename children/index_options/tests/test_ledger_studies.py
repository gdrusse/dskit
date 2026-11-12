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
from index_options.ledger_studies import MIOPacketStudy


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

def _indexed_packet_inputs(tmp_path):
    from dskit.pipeline.base import value_hash

    expiry = "2024-03-08"
    pin = {"source": "chain", "sha256": "a" * 64}
    bounds = {"date": {"start": "2024-03-01", "end_before": "2024-03-29"},
              "expiration": {"end_before": "2024-03-29"}}
    index_path = tmp_path / "artifacts" / "mio-quotes" / "X.sqlite"
    index = {"path": str(index_path), "rows": 3}
    entry = {"index": index, "symbol": "X", "relative": "X.parquet", "source_pin": pin,
             "bounds": bounds, "expiry_inventory_sha256": value_hash([expiry]),
             "forecast_expiries": [expiry], "selected_rows": 3, "basis_factors": {}}
    study = {"domain": {"sources": {"chain": {"files": {"X": "X.parquet"}, "pin": pin}},
                        "params": {"symbols": ["X"]}}}
    plan = {"read_bounds": {"chain": bounds["date"]}, "source_pins": {"chain": pin},
            "sha256": "b" * 64}
    bounded = {"inputs": {"X": {"forecasts": [{"expiry": expiry}], "chain_index": entry}},
               "clocks": {"sessions": ["2024-03-01"]}}
    return study, plan, bounded, index_path

def test_mio_indexed_input_validator_accepts_pinned_descriptor(tmp_path):
    study, plan, bounded, index_path = _indexed_packet_inputs(tmp_path)
    assert MIOPacketStudy.validate_indexed_inputs(study, bounded, plan, tmp_path / "artifacts" / "mio-quotes") is None
    assert not index_path.exists()  # validation is metadata-only; the later quote owner opens it


@pytest.mark.parametrize("mutation", [
    "metadata_keys", "symbol", "relative", "source_pin", "bounds",
    "expiry_inventory", "forecast_expiries", "selected_rows", "index_rows",
    "root_escape", "inline_mixed", "missing_symbol", "invalid_index_path",
])
def test_mio_indexed_input_validator_rejects_descriptor_drift_and_mixed_mode(tmp_path, mutation):
    import copy

    study, plan, bounded, _ = _indexed_packet_inputs(tmp_path)
    bounded = copy.deepcopy(bounded)
    entry = bounded["inputs"]["X"]["chain_index"]
    if mutation == "metadata_keys":
        entry["unexpected"] = True
    elif mutation == "symbol":
        entry["symbol"] = "Y"
    elif mutation == "relative":
        entry["relative"] = "other.parquet"
    elif mutation == "source_pin":
        entry["source_pin"] = {"source": "chain", "sha256": "c" * 64}
    elif mutation == "bounds":
        entry["bounds"]["date"]["start"] = "2024-02-01"
    elif mutation == "expiry_inventory":
        entry["expiry_inventory_sha256"] = "0" * 64
    elif mutation == "forecast_expiries":
        entry["forecast_expiries"] = ["2024-03-15"]
    elif mutation == "selected_rows":
        entry["selected_rows"] = 2
    elif mutation == "index_rows":
        entry["index"]["rows"] = 4
    elif mutation == "root_escape":
        entry["index"]["path"] = str(tmp_path / "outside.sqlite")
    elif mutation == "inline_mixed":
        bounded["inputs"]["X"]["chain"] = []
    elif mutation == "missing_symbol":
        del bounded["inputs"]["X"]
    else:
        entry["index"]["path"] = None
    with pytest.raises(ValueError):
        MIOPacketStudy.validate_indexed_inputs(
            study, bounded, plan, tmp_path / "artifacts" / "mio-quotes")


def test_mio_packet_intake_helper_authenticates_and_builds_study_params(tmp_path, monkeypatch):
    from dskit.pipeline.base import value_hash

    study, plan, bounded, _ = _indexed_packet_inputs(tmp_path)
    study["registry"] = {"path": str(tmp_path / "registry.sqlite"),
                         "id": "00000000-0000-4000-8000-000000000001",
                         "domain": "synthetic:test"}
    study["domain"]["params"] = {"symbols": ["X"], "methods": []}
    study["domain"]["pairs"] = [{"id": "pair"}]
    provenance = {"inputs_sha256": value_hash(bounded), "plan_sha256": plan["sha256"],
                  "source_pins_sha256": value_hash(plan["source_pins"]),
                  "read_bounds": plan["read_bounds"]}
    permit = {"id": "permit"}
    class Registry:
        def verify(self, observed_plan, observed_permit):
            assert observed_plan == plan and observed_permit == permit
            return {"inputs": value_hash(provenance)}
    registry = Registry()
    from dskit.pipeline.study_protocol import StudyContract
    import dskit.pipeline.study_protocol as protocol
    monkeypatch.setattr(MIOPacketStudy, "plan", lambda self: plan)
    monkeypatch.setattr(StudyContract, "verify_plan", staticmethod(lambda study, supplied: plan))
    monkeypatch.setattr(protocol, "ExposureRegistry", lambda config: registry)
    node = MIOPacketStudy("study", {"study": study})
    validated = node.validate_intake(
        SimpleNamespace(run_dir=str(tmp_path)), {
            "permit": permit, "bounded_inputs": bounded, "provenance": provenance})
    assert validated == (
        plan, registry, permit, bounded, provenance,
        {"symbols": ["X"], "methods": [], "selected_pairs": [{"id": "pair"}],
         "clocks": bounded["clocks"]})




def _authenticated_intake_fixture(tmp_path, monkeypatch):
    from dskit.pipeline.base import value_hash
    from dskit.pipeline.study_protocol import StudyContract
    import dskit.pipeline.study_protocol as protocol

    study, plan, bounded, _ = _indexed_packet_inputs(tmp_path)
    study["registry"] = {"path": str(tmp_path / "registry.sqlite"),
                         "id": "00000000-0000-4000-8000-000000000001",
                         "domain": "synthetic:test"}
    study["domain"]["params"].update(methods=[{"id": "m1"}])
    study["domain"]["pairs"] = [{"id": "pair"}]
    provenance = {"inputs_sha256": value_hash(bounded), "plan_sha256": plan["sha256"],
                  "source_pins_sha256": value_hash(plan["source_pins"]),
                  "read_bounds": plan["read_bounds"]}
    permit = {"id": "active-permit"}

    class Registry:
        def verify(self, observed_plan, observed_permit):
            assert observed_plan == plan
            assert observed_permit == permit
            return {"inputs": value_hash(provenance)}

    registry = Registry()
    monkeypatch.setattr(StudyContract, "verify_plan", staticmethod(lambda observed, supplied: plan))
    monkeypatch.setattr(protocol, "ExposureRegistry", lambda config: registry)
    return study, plan, permit, provenance, bounded, registry


def test_authenticated_intake_rebuilds_plan_verifies_active_claim_and_returns_canonical_values(
    tmp_path, monkeypatch
):
    study, plan, permit, provenance, bounded, registry = _authenticated_intake_fixture(
        tmp_path, monkeypatch)

    verified_registry, params = MIOPacketStudy.validate_authenticated_intake(
        study, plan, permit, provenance, bounded,
        tmp_path / "artifacts" / "mio-quotes")

    assert verified_registry is registry
    assert params == {"symbols": ["X"], "methods": [{"id": "m1"}],
                      "selected_pairs": [{"id": "pair"}],
                      "clocks": bounded["clocks"]}


@pytest.mark.parametrize("mutation", ["bounded_hash", "provenance_claim", "stale_permit", "plan_drift"])
def test_authenticated_intake_rejects_forged_or_stale_evidence(tmp_path, monkeypatch, mutation):
    from dskit.pipeline.base import value_hash
    from dskit.pipeline.study_protocol import StudyContract

    study, plan, permit, provenance, bounded, _registry = _authenticated_intake_fixture(
        tmp_path, monkeypatch)
    if mutation == "bounded_hash":
        bounded["clocks"]["sessions"].append("2024-03-04")
    elif mutation in {"provenance_claim", "stale_permit"}:
        from dskit.pipeline import study_protocol as protocol
        class WrongClaimRegistry:
            def verify(self, observed_plan, observed_permit):
                if mutation == "stale_permit":
                    raise ValueError("inactive permit")
                return {"inputs": value_hash({"different": True})}
        wrong = WrongClaimRegistry()
        monkeypatch.setattr(protocol, "ExposureRegistry", lambda config: wrong)
        if mutation == "provenance_claim":
            provenance["inputs_sha256"] = "f" * 64
    else:
        monkeypatch.setattr(
            StudyContract, "verify_plan",
            staticmethod(lambda observed, supplied: (_ for _ in ()).throw(ValueError("stale plan"))),
        )

    with pytest.raises(ValueError):
        MIOPacketStudy.validate_authenticated_intake(
            study, plan, permit, provenance, bounded,
            tmp_path / "artifacts" / "mio-quotes")




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


# ---------------------------------------------------------------------------
# risk_charge method key (ADR-0278 amendment, estimation risk)
# ---------------------------------------------------------------------------


def _without_seconds(decision):
    """One decision with every certificate's wall-clock seconds removed."""
    return {**decision, "certificates": [{k: v for k, v in c.items() if k != "seconds"}
                                         for c in decision.get("certificates", [])]}


def test_weekly_portfolio_risk_charge_absent_or_zero_is_identity(monkeypatch):
    """kappa 0 leaves every program, decision, certificate, book and trade exactly as no key."""
    import copy

    params, inputs = _weekly_portfolio_fixture()
    programs = _capture_programs(monkeypatch)
    base = ledger_studies.WeeklyOptionPortfolioStudy(params, inputs).run()["methods"][0]
    free = copy.deepcopy(params)
    free["methods"][0]["risk_charge"] = {"measure": "mad", "price": 0.}
    count = len(programs)
    same = ledger_studies.WeeklyOptionPortfolioStudy(free, inputs).run()["methods"][0]
    assert count and programs[count:] == programs[:count]
    assert not any("risk_charge" in g for p in programs for g in p["groups"])
    assert [_without_seconds(d) for d in same["decisions"]] == \
        [_without_seconds(d) for d in base["decisions"]]
    assert same["trades"] == base["trades"] and same["rejected"] == base["rejected"]
    assert same["pnl"] == base["pnl"] and same["curve"] == base["curve"]
    assert not any("risk_charges" in d.get("selection", {}) for d in same["decisions"])
    assert not any("risk_charge" in b for b in same["trades"])


def _capture_programs(monkeypatch):
    """Record a deep copy of every program the study hands the solver."""
    import copy
    from dskit.pipeline.libs.pyomo import TransportPortfolioSolve

    programs, run = [], TransportPortfolioSolve.run

    def spy(self, ctx, inputs):
        programs.append(copy.deepcopy(inputs))
        return run(self, ctx, inputs)

    monkeypatch.setattr(TransportPortfolioSolve, "run", spy)
    return programs


@pytest.mark.parametrize("kappa", [.05, .5])
def test_weekly_portfolio_risk_charge_passes_to_groups_and_records_each_book(monkeypatch, kappa):
    """Every group carries the declaration; each book records its exact charge beside expected."""
    import copy
    import numpy as np
    from dskit.pipeline.libs.pyomo import MeanAbsoluteDeviation

    params, inputs = _weekly_portfolio_fixture()
    spec = {"measure": "mad", "price": kappa}
    params["methods"][0]["risk_charge"] = copy.deepcopy(spec)
    programs = _capture_programs(monkeypatch)
    study = ledger_studies.WeeklyOptionPortfolioStudy(params, inputs)
    result = study._method(params["methods"][0], None)
    solved = _solved(result)
    assert solved and len(programs) == len(solved)
    books = 0
    for program, decision in zip(programs, solved):
        assert all(g["risk_charge"] == spec for g in program["groups"])
        selection = decision["selection"]
        assert selection["value"] == pytest.approx(
            sum(selection["group_values"])-sum(selection["risk_charges"]), abs=1e-9)
        index = {g["id"]: i for i, g in enumerate(program["groups"])}
        for book in decision["books"]:
            i = index[book["id"]]
            group = program["groups"][i]
            loss = np.asarray(group["losses"]) @ selection["quantities"][i]
            exact = MeanAbsoluteDeviation(group["masses"], kappa).charge(loss)
            assert book["risk_charge"] == selection["risk_charges"][i] == pytest.approx(exact, abs=1e-9)
            assert book["risk_charge"] > 0.
            assert book["expected"] == selection["group_values"][i]
            books += 1
    assert books, "the charged method still selects at least one book"
    assert all("risk_charge" in t for t in result["trades"])
    monkeypatch.undo()
    assert study._method(params["methods"][0], None, recorded=copy.deepcopy(result)) == result
    k = next(n for n, d in enumerate(solved) if d["books"])
    i = [g["id"] for g in programs[k]["groups"]].index(solved[k]["books"][0]["id"])
    bad = copy.deepcopy(result)
    bad["decisions"][result["decisions"].index(solved[k])]["selection"]["risk_charges"][i] += 1.
    with pytest.raises(ValueError, match="recorded"):
        study._method(params["methods"][0], None, recorded=bad)
    bad = copy.deepcopy(result)
    next(d for d in bad["decisions"] if d.get("books"))["books"][0]["risk_charge"] += 1.
    with pytest.raises(ValueError, match="recorded"):
        study._method(params["methods"][0], None, recorded=bad)
    other = copy.deepcopy(params["methods"][0])
    other["risk_charge"]["price"] = 2*kappa
    with pytest.raises(ValueError, match="recorded method or decision calendar differs"):
        study._method(other, None, recorded=copy.deepcopy(result))


def test_weekly_portfolio_risk_charge_prohibitive_price_is_cash():
    """The charge reaches the trading decision: a prohibitive price leaves the account in cash."""
    import copy

    params, inputs = _weekly_portfolio_fixture()
    contracts = []
    for kappa in (None, .05, .5, 1e4):
        method = copy.deepcopy(params)
        if kappa is not None:
            method["methods"][0]["risk_charge"] = {"measure": "mad", "price": kappa}
        result = ledger_studies.WeeklyOptionPortfolioStudy(method, inputs).run()["methods"][0]
        first = _solved(result)[0]
        contracts.append(sum(b["contracts"] for b in first["books"]))
        if kappa == 1e4:
            assert first["status"] == "cash" and not result["trades"]
    assert contracts[0] > 0 and contracts[-1] == 0


@pytest.mark.parametrize("spec", [
    {"measure": "mad", "price": -.5},
    {"measure": "mad", "price": float("nan")},
    {"measure": "mad", "price": "1"},
    {"measure": "cvar", "price": .5},
    {"measure": "mad"},
    {"measure": "mad", "price": .5, "alpha": .9},
    None, "mad", .5,
])
def test_weekly_portfolio_risk_charge_refuses_invalid(spec):
    import copy

    params, inputs = _weekly_portfolio_fixture()
    bad = copy.deepcopy(params)
    bad["methods"][0]["risk_charge"] = spec
    with pytest.raises(ValueError, match="invalid fixed method"):
        ledger_studies.WeeklyOptionPortfolioStudy(bad, inputs)


# --- radius_scale: the volatility-scaled W1 radius (ADR-0297) ----------------------------------------------------

#: `_method` digests (decision `seconds` removed) taken from the code before `radius_scale` existed.
RADIUS_BASELINES = {
    ("packet", 0.): "f272caf41f3e12db4af505e9f43425e5474575ec375b4b8f549565271951c748",
    ("packet", .1): "fd0181f0b4bc42d4803592a4abe2fc79f385e86b7d55d30a3d4ddd0f98ba25e3",
    ("weekly", 0.): "c2ebdcb1d6f54f739c9c7748e2c989c4dc0c35670da1d142ee5523b6162c1bcd",
    ("weekly", .1): "28397c12b80cd20029cdad05e1ab19c16ecbdafde85159371c8ffcc190c6b8ee"}


def _timeless_digest(result):
    import copy
    from dskit.pipeline.base import value_hash

    result = copy.deepcopy(result)
    for decision in result["decisions"]:
        for certificate in decision.get("certificates", []):
            certificate.pop("seconds", None)
    return value_hash(result)


@pytest.mark.parametrize("kind", ["packet", "weekly"])
@pytest.mark.parametrize("rho", [0., .1])
def test_a_method_without_radius_scale_is_byte_identical_to_the_code_before(kind, rho):
    params, inputs = (_packet_clock_fixture if kind == "packet" else _weekly_portfolio_fixture)()
    params["methods"][0]["rho"] = rho
    result = ledger_studies.WeeklyOptionPortfolioStudy(params, inputs)._method(params["methods"][0], None)
    assert _timeless_digest(result) == RADIUS_BASELINES[kind, rho]


def _program_scales(monkeypatch):
    """Record every group's radius and scale the solver is handed."""
    from dskit.pipeline.libs.pyomo import TransportPortfolioSolve

    seen, run = [], TransportPortfolioSolve.run

    def spy(self, ctx, inputs):
        seen.extend((g["radius"], g["scale"]) for g in inputs["groups"])
        return run(self, ctx, inputs)
    monkeypatch.setattr(TransportPortfolioSolve, "run", spy)
    return seen


def _weekly_method(radius_scale, rho):
    params, inputs = _weekly_portfolio_fixture()
    params["methods"][0]["rho"] = rho
    if radius_scale:
        params["methods"][0]["radius_scale"] = "reference_scale"
    return params, inputs


def test_radius_scale_makes_the_group_scale_spot_times_the_rows_reference_scale(monkeypatch):
    seen = _program_scales(monkeypatch)
    params, inputs = _weekly_method(False, .01)
    ledger_studies.WeeklyOptionPortfolioStudy(params, inputs)._method(params["methods"][0], None)
    assert seen and {scale for _, scale in seen} == {100.}, "absent key: the scale stays the spot"
    seen.clear()
    params, inputs = _weekly_method(True, .5)
    for row in inputs["X"]["contexts"]:
        row["reference_scale"] = {"2019-01-02": .02, "2019-01-07": .04}[row["quote_date"]]
    ledger_studies.WeeklyOptionPortfolioStudy(params, inputs)._method(params["methods"][0], None)
    assert {radius for radius, _ in seen} == {.5}, "rho is still the radius, in units of the scale"
    assert sorted({scale for _, scale in seen}) == [2., 4.], "each row has its own spot x reference_scale"


def test_radius_scale_is_the_same_ball_as_a_smaller_rho_on_the_spot_scale():
    """W1 radius rho x scale: (rho .5, scale 2) and (rho .01, scale 100) are one ball, so one decision."""
    scaled_params, inputs = _weekly_method(True, .5)
    by_hand_params, _ = _weekly_method(False, .01)
    spot_params, _ = _weekly_method(False, .5)
    study = ledger_studies.WeeklyOptionPortfolioStudy
    scaled = study(scaled_params, inputs)._method(scaled_params["methods"][0], None)
    by_hand = study(by_hand_params, inputs)._method(by_hand_params["methods"][0], None)
    spot = study(spot_params, inputs)._method(spot_params["methods"][0], None)
    assert scaled["trades"] and not spot["trades"], "the volatility radius is far smaller than rho x spot"
    held = [(a, b) for a, b in zip(scaled["decisions"], by_hand["decisions"]) if "selection" in a]
    assert held and all("selection" in b for _, b in held)
    for a, b in held:
        assert a["selection"]["primary_quantities"] == b["selection"]["primary_quantities"]
        assert a["selection"]["value"] == pytest.approx(b["selection"]["value"], rel=1e-9)
        assert a["selection"]["bound"] == pytest.approx(b["selection"]["bound"], rel=1e-9)


def _equal_but_last_bits(a, b):
    """Return whether two results are equal but for float noise: the derived host re-prices the duals."""
    if isinstance(a, dict):
        return a.keys() == b.keys() and all(_equal_but_last_bits(a[k], b[k]) for k in a)
    if isinstance(a, list):
        return len(a) == len(b) and all(_equal_but_last_bits(x, y) for x, y in zip(a, b))
    if isinstance(a, float):
        return a == pytest.approx(b, rel=1e-12, abs=1e-12)
    return a == b


def test_the_derived_and_recorded_paths_value_the_actions_on_the_same_scale():
    """The same integer actions are accepted on the scale that solved them and refused on any other."""
    import copy
    from index_options.mio_worker import MIOMethodWorker

    params, inputs = _weekly_method(True, .5)
    method = params["methods"][0]
    solved = ledger_studies.WeeklyOptionPortfolioStudy(params, inputs)._method(method, None)
    actions = MIOMethodWorker.actions(solved, ledger_studies.WeeklyOptionPortfolioStudy(params, inputs).weeks)
    assert any(e["actions"] for e in actions["decisions"].values())
    derived = ledger_studies.WeeklyOptionPortfolioStudy(params, inputs)._method(method, None, derived=actions)
    assert _equal_but_last_bits(derived, solved)
    assert ledger_studies.WeeklyOptionPortfolioStudy(params, inputs)._method(method, None, recorded=derived) == derived
    spot_scale = copy.deepcopy(params)
    del spot_scale["methods"][0]["radius_scale"]
    with pytest.raises(ValueError):
        ledger_studies.WeeklyOptionPortfolioStudy(spot_scale, inputs)._method(
            spot_scale["methods"][0], None, derived=actions)


def test_derived_actions_for_a_week_with_no_eligible_candidate_are_refused():
    """The derived path consumes exactly the eligible weeks: an entry for a week that had none is refused."""
    import copy
    from index_options.mio_worker import MIOMethodWorker

    params, inputs = _weekly_method(True, .5)
    method = params["methods"][0]
    study = ledger_studies.WeeklyOptionPortfolioStudy(params, inputs)
    solved = study._method(method, None)
    ineligible = [d["date"] for d in solved["decisions"] if d["status"] == "no_eligible_candidates"]
    assert ineligible, "the fixture has weeks with no eligible candidate"
    actions = MIOMethodWorker.actions(solved, study.weeks)
    assert ledger_studies.WeeklyOptionPortfolioStudy(params, inputs)._method(method, None, derived=actions)
    extra = copy.deepcopy(actions)
    extra["decisions"][ineligible[0]] = {"actions": [], "certificates": []}
    with pytest.raises(ValueError, match="ineligible week"):
        ledger_studies.WeeklyOptionPortfolioStudy(params, inputs)._method(method, None, derived=extra)


@pytest.mark.parametrize("value", ["spot", "", None, True, 1, ["reference_scale"], "Reference_Scale"])
def test_radius_scale_refuses_any_other_value(value):
    params, inputs = _weekly_portfolio_fixture()
    params["methods"][0]["radius_scale"] = value
    with pytest.raises(ValueError, match="invalid fixed method"):
        ledger_studies.WeeklyOptionPortfolioStudy(params, inputs)


# --- a 25-point forecast grid: nothing in the study is sized by 13 (ADR-0297) --------------------------------------

def _grid_fixture(points, strikes=(96., 98., 100., 102., 104.)):
    """The weekly fixture on an evenly spaced -3..3 grid of ``points`` points and a denser two-sided chain."""
    import copy
    import math

    params, inputs = _weekly_portfolio_fixture()
    z = [-3.+6.*i/(points-1) for i in range(points)]
    labels = [str(i) for i in range(points)]
    params["bridge"].update(z_grid=z, grid_labels=labels)
    for row in inputs["X"]["forecasts"]:
        for key in [k for k in row if k.startswith("method_cdf_")]:
            del row[key]
        row.update({"method_cdf_"+label: .5*(1+math.erf(v/2**.5)) for label, v in zip(labels, z)})
    quotes = []
    for row in inputs["X"]["chain"]:
        if row["strike_raw"] != 100. or row["type"] != "put":
            continue
        for kind in ("put", "call"):
            for strike in strikes:
                width = abs(strike-100.)
                bid = max(.05, 4.-.9*width if (kind == "put") == (strike <= 100.) else .3)
                quotes.append({**row, "type": kind, "strike_raw": strike, "strike": strike,
                               "bid": bid, "ask": bid+.2})
    inputs["X"]["chain"] = quotes
    return params, copy.deepcopy(inputs)


def test_a_25_point_grid_sizes_the_program_by_strikes_and_feeds_the_quadrature_every_knot():
    study = {}
    for points in (13, 25):
        params, inputs = _grid_fixture(points)
        study[points] = ledger_studies.WeeklyOptionPortfolioStudy(params, inputs)
    prepared = {n: s._prepare("2019-01-02") for n, s in study.items()}
    for n in (13, 25):
        groups, metadata, refusals = prepared[n]
        assert len(groups) == 1 and not refusals, refusals
        group, meta = groups[0], metadata[0]
        support, items = len(group["grid"]), len(group["capacity"])
        assert (len(group["losses"]), len(group["losses"][0])) == (support, items), "one loss row per support point"
        assert len(group["masses"]) == support and abs(sum(group["masses"])-1.) < 1e-9
        knots = meta["projection"]["breakpoints"]
        assert len(knots) == n-2 or len(knots) == n, f"every grid point strictly inside the support is a knot ({n}, got {len(knots)})"
        assert [round(z, 6) for z in knots] == sorted(round(z, 6) for z in knots)
    coarse, fine = prepared[13][0][0], prepared[25][0][0]
    assert (len(coarse["grid"]), len(coarse["capacity"])) == (len(fine["grid"]), len(fine["capacity"])), \
        "the program is sized by the listed strikes, so a finer CDF adds no variable or row"
    assert len(prepared[25][1][0]["projection"]["breakpoints"]) > len(prepared[13][1][0]["projection"]["breakpoints"])
    # the normal CDF is the same distribution on either grid: the projected masses agree to interpolation error
    assert fine["masses"] == pytest.approx(coarse["masses"], abs=2e-2)


def test_a_25_point_grid_solves_and_replays_like_the_13_point_one():
    results = {}
    for points in (13, 25):
        params, inputs = _grid_fixture(points)
        params["methods"][0]["rho"] = .01
        study = ledger_studies.WeeklyOptionPortfolioStudy(params, inputs)
        result = study._method(params["methods"][0], None)
        assert study._method(params["methods"][0], None, recorded=result) == result
        results[points] = result
    assert results[13]["trades"] and results[25]["trades"]
    sized = {n: [(c["variables"], c["constraints"]) for d in r["decisions"] for c in d.get("certificates", [])]
             for n, r in results.items()}
    assert sized[13] == sized[25], "solver size does not depend on the number of CDF points"


# --- CDF rounding noise: raised just before MIO uses a forecast, a real decrease still refused (ADR-0297) ------------

def _with_cdf(edit):
    """The weekly fixture whose first forecast row's 13 CDF values ``edit`` may rewrite (label -> value dict)."""
    params, inputs = _weekly_portfolio_fixture()
    row = inputs["X"]["forecasts"][0]
    values = {k: row[k] for k in row if k.startswith("method_cdf_")}
    row.update(edit(values))
    return params, inputs


def _decisions(params, inputs):
    study = ledger_studies.WeeklyOptionPortfolioStudy(params, inputs)
    return study._method(params["methods"][0], None), study


@pytest.mark.parametrize("drop", [1e-15, 5e-10, 9e-10])
def test_a_cdf_point_that_falls_by_rounding_noise_is_raised_to_the_prior_value(drop):
    noisy = _with_cdf(lambda v: {"method_cdf_6": v["method_cdf_5"]-drop})
    raised = _with_cdf(lambda v: {"method_cdf_6": v["method_cdf_5"]})
    result, study = _decisions(*noisy)
    expected, clean = _decisions(*raised)
    trace = study._prepare("2019-01-02")[1][0]["projection"]
    assert trace["cdf_noise_raised"] == pytest.approx(drop, rel=1e-6), "a repaired row says how far it moved"
    # ... and the saved result says which rows were repaired, not only the in-memory projection
    saved = json.loads(json.dumps(result))
    repaired = [d["cdf_noise_repaired"] for d in saved["decisions"] if "cdf_noise_repaired" in d]
    assert len(repaired) == 1 and repaired[0]["rows"] == 1
    assert repaired[0]["largest"] == pytest.approx(drop, rel=1e-6)
    assert list(repaired[0]["raised"]) == ["2019-01-02:X:2019-02-01"]
    assert all("cdf_noise_repaired" not in d for d in expected["decisions"]), "a clean forecast carries no key"
    assert all("cdf_noise_raised" not in m["projection"] for m in clean._prepare("2019-01-02")[1])
    assert result["trades"], "the repaired forecast still drives a book"
    assert result["trades"] == expected["trades"] and result["pnl"] == expected["pnl"]
    assert [d.get("selection") for d in result["decisions"]] == [d.get("selection") for d in expected["decisions"]]


def test_a_run_of_sub_tolerance_falls_is_raised_by_the_builders_own_rule():
    """The builder (MONOTONE_TOLERANCE) judges consecutive differences, so MIO does too: each step is within
    the tolerance, the cumulative fall is not, and a forecast the builder published must not refuse a method."""
    drifting = _with_cdf(lambda v: {"method_cdf_5": v["method_cdf_4"]-8e-10, "method_cdf_6": v["method_cdf_4"]-1.6e-9})
    flat = _with_cdf(lambda v: {"method_cdf_5": v["method_cdf_4"], "method_cdf_6": v["method_cdf_4"]})
    result, study = _decisions(*drifting)
    expected, _ = _decisions(*flat)
    assert study._prepare("2019-01-02")[1][0]["projection"]["cdf_noise_raised"] == pytest.approx(1.6e-9, rel=1e-3)
    assert result["trades"] and result["pnl"] == expected["pnl"]


@pytest.mark.parametrize("edit", [
    lambda v: {"method_cdf_6": v["method_cdf_5"]-2e-9},             # past the tolerance
    lambda v: {"method_cdf_6": v["method_cdf_5"]-1e-6},
    lambda v: {"method_cdf_6": v["method_cdf_5"]-.1},               # a real inversion
    lambda v: {"method_cdf_6": float("nan")},
    lambda v: {"method_cdf_6": -1e-3, "method_cdf_0": 0.},
    lambda v: {"method_cdf_12": 1.0000001},
], ids=["past-tol", "1e-6", "inversion", "nan", "negative", "above-one"])
def test_a_real_cdf_decrease_or_defect_is_still_refused(edit):
    params, inputs = _with_cdf(edit)
    with pytest.raises(ValueError, match="incoherent method CDF"):
        _decisions(params, inputs)


def test_a_monotone_cdf_carries_no_trace_of_the_cleanup():
    """Existing frozen forecasts are monotone: the cleanup changes neither their masses nor their evidence."""
    params, inputs = _weekly_portfolio_fixture()
    study = ledger_studies.WeeklyOptionPortfolioStudy(params, inputs)
    assert all("cdf_noise_raised" not in m["projection"] for m in study._prepare("2019-01-02")[1])
    assert _timeless_digest(study._method(params["methods"][0], None)) == RADIUS_BASELINES["weekly", 0.]


# --- the seven MIO formulations and the component cap (ADR-0297; owner rulings 2026-11-05 ~15:30 to ~16:00) ------------

SHOCK10 = {"moves": [-.1, -.05, .05, .1], "loss_fraction": .1, "beta_lookback": 60}
#: Shared base of every formulation: a 20% short-notional floor, no per-name or portfolio cap beyond the account.
_BASE = {"portfolio_fraction": 1., "name_fraction": 1., "component_fraction": None, "short_notional_fraction": .2}
#: 0.10 over the median reference_scale of 791,231 rows, 166 names, 2017-2019 (input-only; PLAN.md ~15:50).
MEDIAN_REFERENCE_SCALE = 0.05963088521417719
FORMULATIONS = {
    "w1_010_shock10_lp": {**_BASE, "rho": .10, "shock": SHOCK10},
    "w1_003_shock10_lp": {**_BASE, "rho": .03, "shock": SHOCK10},
    "w1_003_shock10_mad050_lp": {**_BASE, "rho": .03, "shock": SHOCK10, "risk_charge": {"measure": "mad", "price": .5}},
    "nominal_lp": {**_BASE, "rho": 0.},
    "w1_010z_shock10_lp": {**_BASE, "rho": 1.676983322330843, "shock": SHOCK10, "radius_scale": "reference_scale"},
    "w1_010_shock10_fine_lp": {**_BASE, "rho": .10, "shock": SHOCK10},  # its own document: the 25-point bridge grid
    "w1_010_component25_shock10_lp": {**_BASE, "rho": .10, "shock": SHOCK10, "component_fraction": .25},
    # The two added at ~17:30: a per-name cap of a quarter of the account, and a 5% shock budget on the FILLED account.
    "w1_010_name25_shock10_lp": {**_BASE, "rho": .10, "shock": SHOCK10, "name_fraction": .25},
    "w1_010_shock05_book_lp": {**_BASE, "rho": .10, "shock": {**SHOCK10, "loss_fraction": .05, "scope": "filled_account"}},
}


def test_all_nine_formulations_validate_and_differ_only_where_they_say():
    base = FORMULATIONS["w1_010_shock10_lp"]
    assert len(FORMULATIONS) == 9
    for name, method in FORMULATIONS.items():
        params, inputs = _weekly_portfolio_fixture()
        params["methods"] = [{"id": name, **method}]
        ledger_studies.WeeklyOptionPortfolioStudy.validate_configuration(params)
    assert {k: v for k, v in FORMULATIONS["w1_010z_shock10_lp"].items() if k not in ("rho", "radius_scale")} == \
        {k: v for k, v in base.items() if k != "rho"}
    assert FORMULATIONS["w1_010_component25_shock10_lp"] == {**base, "component_fraction": .25}
    assert FORMULATIONS["w1_010_shock10_fine_lp"] == base
    assert FORMULATIONS["w1_010_name25_shock10_lp"] == {**base, "name_fraction": .25}
    assert FORMULATIONS["w1_010_shock05_book_lp"] == {
        **base, "shock": {"moves": [-.1, -.05, .05, .1], "loss_fraction": .05, "beta_lookback": 60,
                          "scope": "filled_account"}}
    assert FORMULATIONS["w1_010z_shock10_lp"]["rho"] == pytest.approx(.10/MEDIAN_REFERENCE_SCALE, rel=1e-15)


def _spy_programs(monkeypatch):
    """Record every program the solver is handed, with the selection it returns."""
    from dskit.pipeline.libs.pyomo import TransportPortfolioSolve

    seen, run = [], TransportPortfolioSolve.run

    def spy(self, ctx, inputs):
        out = run(self, ctx, inputs)
        seen.append({"caps": [(c["upper"], len(c["groups"])) for c in inputs["caps"]],
                     "joint": [r["upper"] for r in inputs.get("joint_rows", [])],
                     "reserve": sum(out["selection"]["reserves"])})
        return out
    monkeypatch.setattr(TransportPortfolioSolve, "run", spy)
    return seen


def test_the_component_cap_binds_beside_the_shock_row(monkeypatch):
    """25% of funding per correlated group: with open reserve from week one, week two's room is what is left."""
    seen = _spy_programs(monkeypatch)
    outcome = {}
    for fraction in (None, .25):
        seen.clear()
        params, inputs = _weekly_portfolio_fixture()
        params.update(initial=4000., deposit=0.)
        params["methods"][0].update(rho=.01, component_fraction=fraction, short_notional_fraction=0.,
                                    shock={**SHOCK10, "loss_fraction": .5})   # the shock row is present, not the limit
        result = ledger_studies.WeeklyOptionPortfolioStudy(params, inputs)._method(params["methods"][0], None)
        outcome[fraction] = (result, list(seen))
    free, capped = outcome[None], outcome[.25]
    assert all(s["joint"] == [2000.]*4 for s in free[1] + capped[1]), "the shock rows ride beside the cap"
    assert len(capped[1][0]["caps"]) == len(free[1][0]["caps"])+1, "one group cap more"
    week_two = capped[1][1]["caps"]
    room = 4000.*.25-free[1][0]["reserve"]
    assert any(upper == pytest.approx(room) for upper, _ in week_two), "the cap is 25% of funding less the open reserve"
    assert room < free[1][1]["reserve"], "the book week two would take does not fit that room"
    assert [d["status"] for d in capped[0]["decisions"]][:2] == ["selected", "cash"]
    assert [d["status"] for d in free[0]["decisions"]][:2] == ["selected", "selected"]
    assert len(capped[0]["trades"]) == 1 and len(free[0]["trades"]) == 2
    assert all(s["reserve"] <= 4000.*.25+1e-6 for s in capped[1]), "no solve of the capped method exceeds the group cap"


def test_the_name25_cap_is_a_quarter_of_funding_per_name_and_binds(monkeypatch):
    """name_fraction .25 adds a per-name room of 25% of funding less its open reserve; no solve exceeds it."""
    seen = _spy_programs(monkeypatch)
    params, inputs = _weekly_portfolio_fixture()
    params.update(initial=4000., deposit=0.)
    params["methods"][0].update(FORMULATIONS["w1_010_name25_shock10_lp"], id="nominal", rho=.01,
                                shock={**SHOCK10, "loss_fraction": .5}, short_notional_fraction=0.)
    result = ledger_studies.WeeklyOptionPortfolioStudy(params, inputs)._method(params["methods"][0], None)
    assert any(upper == pytest.approx(1000.) for upper, _ in seen[0]["caps"]), "25% of the 4000 funding, week one"
    assert all(s["reserve"] <= 1000.+1e-6 for s in seen), "no solve of the capped method exceeds the name cap"
    assert result["decisions"][0]["status"] == "selected"


def test_the_book_shock_budget_is_five_percent_of_funding_on_the_filled_account(monkeypatch):
    """w1_010_shock05_book_lp: scope filled_account with loss_fraction .05; the budget is 5% of funding."""
    params, inputs = _weekly_portfolio_fixture()
    params.update(initial=4000., deposit=0.)
    params["methods"][0].update(FORMULATIONS["w1_010_shock05_book_lp"], id="nominal", rho=.01,
                                shock={**FORMULATIONS["w1_010_shock05_book_lp"]["shock"], "beta_lookback": 20})
    study = ledger_studies.WeeklyOptionPortfolioStudy(params, inputs)
    result = study._method(params["methods"][0], None)
    shocks = [d["shock"] for d in result["decisions"] if "shock" in d]
    assert shocks and all(x.get("scope", "filled_account") == "filled_account" for x in shocks)
    assert all(x["limit"] == pytest.approx(.05*4000.) for x in shocks if x.get("limit") is not None)


def _chain_world(chains, threshold=.3, history=250, seed=5):
    """Chains of names where neighbours share one factor (correlation about .5) and all other pairs share none."""
    import copy
    import math
    from datetime import date, timedelta
    import numpy as np

    params, inputs = _packet_clock_fixture()
    base = inputs["X"]
    days, day = [], date(2018, 12, 31)
    while len(days) < history:
        day -= timedelta(days=1)
        if day.weekday() < 5:
            days.append(day.isoformat())
    days.sort()
    rng = np.random.default_rng(seed)
    names, series = [], {}
    for c, length in enumerate(chains):
        factors = rng.standard_normal((length+1, len(days)-1))
        for i in range(length):
            name = f"C{c}N{i}"
            names.append(name)
            path = np.exp(np.cumsum(np.r_[0., .01*(factors[i]+factors[i+1])/math.sqrt(2)]))
            series[name] = {d: float(100*p/path[-1]) for d, p in zip(days, path)}   # ends at the live fixture's 100
    params["symbols"] = names
    params.update(correlation_threshold=threshold, correlation_lookback=history-1, correlation_min_pairs=20)
    live = [r for r in base["bars"] if r["date"] >= "2019-01-02"]
    data = {}
    for name in names:
        one = copy.deepcopy(base)
        for kind in ("forecasts", "contexts", "chain"):
            for row in one[kind]:
                row["symbol"] = name
        one["bars"] = [{"symbol": name, "date": d, "close": c} for d, c in series[name].items()] \
            + [dict(r, symbol=name) for r in live]
        data[name] = one
    params["clocks"].update(sessions=days+[r["date"] for r in live], history_start=days[0])
    params["selected_pairs"] = [{"id": n+"30", "symbol": n, "dte_min": 28, "dte_max": 32} for n in names]
    return params, data, series, days


def test_chained_neighbours_make_one_component_so_component_fraction_becomes_a_portfolio_cap():
    """Confirms the prior agent's warning on synthetic data: `_components` is a transitive union, rule unchanged."""
    import numpy as np
    from dskit.pipeline.libs.option_ledger import FundedOptionAccount

    result = {}
    for chains in ([12], [6, 6]):
        params, data, series, days = _chain_world(chains)
        names = params["symbols"]
        corr = np.corrcoef([np.diff(np.log([series[n][d] for d in days])) for n in names])
        pairs = [(i, j) for i in range(len(names)) for j in range(i+1, len(names))]
        linked = sum(abs(corr[i, j]) >= .3 for i, j in pairs)
        study = ledger_studies.WeeklyOptionPortfolioStudy(params, data)
        groups = study._components("2019-01-07")["groups"]
        account = FundedOptionAccount(params["initial"], params["deposit"], params["deposit_every_days"], "2019-01-02")
        method = {"component_fraction": .25, "portfolio_fraction": 1., "name_fraction": 1.}
        limits = study._limits(account, method, {"groups": groups})
        result[tuple(chains)] = (linked, len(pairs), sorted(len(g) for g in groups), limits, names)
    linked, pairs, sizes, limits, names = result[(12,)]
    assert linked == 11 and pairs == 66, "only the 11 neighbour pairs are correlated; 55 of 66 pairs are not"
    assert sizes == [12], "yet one chain of neighbours is ONE component"
    whole = [lim for lim in limits if lim["members"] == names]
    assert sorted(round(lim["upper"], 6) for lim in whole) == [2500., 2500., 10000.], \
        "the portfolio limit, the weekly limit and the component cap all span the whole universe: the cap is 25% of funding"
    two_sizes, two_limits = result[(6, 6)][2], result[(6, 6)][3]
    assert two_sizes == [6, 6], "two unlinked chains stay two components, each with its own 25%"
    assert sorted(len(lim["members"]) for lim in two_limits if lim["upper"] == 2500. and len(lim["members"]) > 1) \
        == [6, 6, 12], "(the weekly limit spans all 12; each chain has its own cap)"


# --- the fractional-Kelly formulations (ADR-0299; owner ordered 2026-11-06) ------------------------------------------

#: The shock's four moves inside a finer grid of sixteen, its lookback, the one cut that keeps log wealth finite, the
#: envelope's resolution, the fill's slack on the cash row, and the gap the objective needs (ADR-0299).
KELLY_MOVES = [-.15, -.125, -.1, -.075, -.05, -.035, -.02, -.01, .01, .02, .035, .05, .075, .1, .125, .15]
#: The certification gap of the kelly formulations, in ONE place: a relative 10% (the owner's decision of 2026-11-06 after
#: the measured real week, ADR-0299), so a change is this line; the tests below read it.
KELLY_GAP = {"absolute": 1., "relative": .10}
KELLY = {"n_tangents": 32, "core": 2., "wealth_floor": .01, "moves": KELLY_MOVES, "beta_lookback": 60,
         "cash_buffer": .05, "gap": KELLY_GAP, "time_limit": 2400}
#: No sizing cap: the fractions are 1 (the cash rule), no component cap, no robustness radius; the 20% short-notional floor stays.
#: The short-notional floor is the other formulations' (``_BASE``): margin a broker would hold against zero-loss parity books.
_KELLY_BASE = {"rho": 0., "portfolio_fraction": 1., "name_fraction": 1., "component_fraction": None,
               "short_notional_fraction": _BASE["short_notional_fraction"]}
KELLY_FORMULATIONS = {
    "kelly025_lp": {**_KELLY_BASE, "kelly": {**KELLY, "fraction": .25}},
    "kelly050_lp": {**_KELLY_BASE, "kelly": {**KELLY, "fraction": .5}},
}


def _for_kelly(params):
    """Declare what a kelly document must: the time limit the method asks for, the timeout policy that skips a week,
    HiGHS's MIP feasibility tolerance (its 1e-6 default leaves the charge variable up to ~0.006 under its lines at
    300,000, above the parity tolerance), a parity tolerance of at least 1e-3 and the ``lp`` group screen (a real week's
    program does not fit without it)."""
    params["solver"]["timeout_policy"] = "return"
    params["solver"]["solver_options"]["time_limit"] = KELLY["time_limit"]
    params["solver"]["solver_options"]["mip_feasibility_tolerance"] = 1e-9
    params["solver"]["parity_tolerance"] = 1e-3
    params["solver"]["group_screen"] = "lp"
    return params


def _kelly_fixture(fraction=.5, **kelly):
    """The weekly fixture under a kelly method whose history needs are met by the fixture's few sessions."""
    params, inputs = _weekly_portfolio_fixture()
    params.update(correlation_lookback=5, correlation_min_pairs=2)
    params["solver"]["parity_tolerance"] = 1e-3   # the study's: the charge lines leave HiGHS up to 1e-4 of slack
    _for_kelly(params)
    params["methods"] = [{"id": "kelly", **_KELLY_BASE,
                          "kelly": {**KELLY, "fraction": fraction, "beta_lookback": 5, **kelly}}]
    return params, inputs


def test_the_two_kelly_formulations_validate_and_differ_only_in_the_fraction():
    for name, method in KELLY_FORMULATIONS.items():
        params, inputs = _weekly_portfolio_fixture()
        params["methods"] = [{"id": name, **method}]
        _for_kelly(params)
        ledger_studies.WeeklyOptionPortfolioStudy.validate_configuration(params)
    quarter, half = KELLY_FORMULATIONS["kelly025_lp"], KELLY_FORMULATIONS["kelly050_lp"]
    assert {k: v for k, v in quarter["kelly"].items() if k != "fraction"} == \
        {k: v for k, v in half["kelly"].items() if k != "fraction"}
    assert (quarter["kelly"]["fraction"], half["kelly"]["fraction"]) == (.25, .5)
    assert set(SHOCK10["moves"]) <= set(KELLY["moves"]) and KELLY["beta_lookback"] == SHOCK10["beta_lookback"], \
        "the joint market risk keeps the shock's moves inside its grid, and its lookback"
    assert not any(k in quarter for k in ("shock", "risk_charge", "radius_scale"))
    assert quarter["short_notional_fraction"] == half["short_notional_fraction"] == .2 == _BASE["short_notional_fraction"]


@pytest.mark.parametrize("change", [
    lambda m: m.update(rho=.1),
    lambda m: m.update(portfolio_fraction=.5),
    lambda m: m.update(name_fraction=.25),
    lambda m: m.update(component_fraction=.25),
    lambda m: m.update(shock=SHOCK10),
    lambda m: m.update(risk_charge={"measure": "mad", "price": .5}),
    lambda m: m.update(radius_scale="reference_scale"),
    lambda m: m.update(short_notional_fraction=0.),
    lambda m: m["kelly"].update(fraction=0.),
    lambda m: m["kelly"].update(fraction=1.5),
    lambda m: m["kelly"].update(n_tangents=1),
    lambda m: m["kelly"].update(n_tangents=64.),
    lambda m: m["kelly"].update(core=1.),
    lambda m: m["kelly"].update(wealth_floor=0.),
    lambda m: m["kelly"].update(wealth_floor=1.),
    lambda m: m["kelly"].update(moves=[]),
    lambda m: m["kelly"].update(moves=[-.1, 0.]),
    lambda m: m["kelly"].update(moves=[-.1, -.1]),
    lambda m: m["kelly"].update(beta_lookback=1),
    lambda m: m["kelly"].update(cash_buffer=-.01),
    lambda m: m["kelly"].update(cash_buffer=1.),
    lambda m: m["kelly"].update(cash_buffer=None),
    lambda m: m["kelly"].pop("cash_buffer"),
    lambda m: m["kelly"].update(dispersion=.1),
    lambda m: m["kelly"].update(gap={"absolute": 0., "relative": .01}),
    lambda m: m["kelly"].update(gap={"absolute": 1., "relative": 1.}),
    lambda m: m["kelly"].update(gap={"absolute": 1.}),
    lambda m: m["kelly"].update(gap=1.),
    lambda m: m["kelly"].pop("gap"),
    lambda m: m["kelly"].update(loss_fraction=.1),
    lambda m: m["kelly"].pop("moves"),
    lambda m: m.update(kelly=None),
])
def test_a_kelly_method_refuses_every_sizing_cap_and_every_bad_key(change):
    params, inputs = _weekly_portfolio_fixture()
    params["methods"] = [{"id": "kelly025_lp", **KELLY_FORMULATIONS["kelly025_lp"]}]
    _for_kelly(params)
    params["methods"][0]["kelly"] = dict(params["methods"][0]["kelly"])
    change(params["methods"][0])
    with pytest.raises(ValueError, match="invalid fixed method"):
        ledger_studies.WeeklyOptionPortfolioStudy.validate_configuration(params)
    assert KELLY_FORMULATIONS["kelly025_lp"]["kelly"] == {**KELLY, "fraction": .25}, "the formulation itself is untouched"


def _spy_utility_programs(monkeypatch):
    """Record every program the solver is handed, whole."""
    import copy
    from dskit.pipeline.libs.pyomo import TransportPortfolioSolve

    seen, run = [], TransportPortfolioSolve.run

    def spy(self, ctx, inputs):
        seen.append(copy.deepcopy(inputs))
        return run(self, ctx, inputs)
    monkeypatch.setattr(TransportPortfolioSolve, "run", spy)
    return seen


def test_a_kelly_method_hands_the_solver_a_utility_program_under_the_cash_rule_alone(monkeypatch):
    """No weekly budget, no name or component cap, no joint loss row: the cash rule and the utility block."""
    seen = _spy_utility_programs(monkeypatch)
    params, inputs = _kelly_fixture()
    study = ledger_studies.WeeklyOptionPortfolioStudy(params, inputs)
    result = study._method(params["methods"][0], None)
    statuses = [(d["date"], d["status"]) for d in result["decisions"]]
    assert statuses[0] == ("2019-01-02", "kelly_risk_input"), "no strictly prior session: the first week does not trade"
    assert result["decisions"][0]["kelly"]["status"] == "insufficient_history"
    assert "selection" not in result["decisions"][0]
    assert statuses[1][1] in ("selected", "cash") and result["decisions"][1]["kelly"]["status"] == "estimated"
    assert seen, "the second week solves"
    program = seen[0]
    assert [c["upper"] for c in program["caps"]] == [pytest.approx(.95*result["decisions"][1]["funding"])], "cash less the 5% buffer"
    assert len(program["caps"]) == 1 and "joint_rows" not in program
    utility = program["utility"]
    assert utility["gamma"] == 2. and utility["w0"] == result["decisions"][1]["funding"]
    assert utility["wealth_lo"] == pytest.approx(.01*utility["w0"])
    assert utility["joint"] == [{"id": "market:0", "weight": 1., "terms": {}}], "flat bars: the moves get no weight"
    assert utility["aggregate"] == {g["id"]: g["id"].split(":")[1] for g in program["groups"]} and "dispersion" not in utility
    kelly = result["decisions"][1]["kelly"]
    assert kelly["sigma_daily"] == 0. and kelly["quiet"] == 1. and set(kelly["weights"]) == {str(m) for m in KELLY_MOVES}
    assert kelly["dropped"] == [] and kelly["gap"] == KELLY_GAP
    assert kelly["betas"] == {"X": 1.} and kelly["horizon_sessions"] >= 1
    assert set(kelly["losses"]) == set(kelly["weights"]), "the selection's loss under each move is recorded"
    if statuses[1][1] == "selected":
        assert result["decisions"][1]["selection"]["utility"]["wealth_min"] >= utility["wealth_lo"]
        assert result["decisions"][1]["selection"]["utility"]["exact"] > 0.


def test_a_kelly_result_replays_through_the_recorded_and_derived_routes():
    """The same integer actions, re-valued by this host's envelope, give the same result; the recorded route agrees."""
    from index_options.mio_worker import MIOMethodWorker

    params, inputs = _kelly_fixture()
    method = params["methods"][0]
    solved = ledger_studies.WeeklyOptionPortfolioStudy(params, inputs)._method(method, None)
    assert any(d.get("status") == "selected" for d in solved["decisions"]), "the fixture trades under Kelly"
    actions = MIOMethodWorker.actions(solved, ledger_studies.WeeklyOptionPortfolioStudy(params, inputs).weeks)
    derived = ledger_studies.WeeklyOptionPortfolioStudy(params, inputs)._method(method, None, derived=actions)
    assert _equal_but_last_bits(derived, solved)
    assert ledger_studies.WeeklyOptionPortfolioStudy(params, inputs)._method(method, None, recorded=solved) == solved
    other = ledger_studies.WeeklyOptionPortfolioStudy(*_kelly_fixture(fraction=.25))
    with pytest.raises(ValueError):
        other._method(other.params["methods"][0], None, derived={**actions, "method": "kelly"})


def test_a_smaller_kelly_fraction_never_takes_more_contracts():
    totals = {}
    for fraction in (.25, .5, 1.):
        params, inputs = _kelly_fixture(fraction=fraction)
        result = ledger_studies.WeeklyOptionPortfolioStudy(params, inputs)._method(params["methods"][0], None)
        totals[fraction] = sum(sum(sum(q) for q in d["selection"]["quantities"])
                               for d in result["decisions"] if "selection" in d)
    assert totals[.25] <= totals[.5] <= totals[1.]


def test_a_method_without_the_kelly_key_hands_the_solver_no_utility_block(monkeypatch):
    seen = _spy_utility_programs(monkeypatch)
    params, inputs = _weekly_portfolio_fixture()
    params["methods"][0].update(FORMULATIONS["w1_010_shock10_lp"], id="nominal", shock={**SHOCK10, "beta_lookback": 2})
    ledger_studies.WeeklyOptionPortfolioStudy(params, inputs)._method(params["methods"][0], None)
    assert seen and all("utility" not in program and "joint_rows" in program for program in seen)


def test_move_weights_read_only_sessions_strictly_before_the_decision():
    """Bars on or after the decision day cannot change the move weights; an earlier bar does."""
    import copy

    params, data, series, days = _chain_world([2, 1])
    kelly = {**KELLY, "fraction": .5}
    study = ledger_studies.WeeklyOptionPortfolioStudy(params, data)
    day = "2019-01-07"
    before = study._move_weights(day, kelly, 30)
    assert before["status"] == "estimated" and before["sigma_daily"] > 0
    assert sum(before["weights"].values())+before["quiet"] == pytest.approx(1.)
    assert all(0 < w < 1 for w in before["weights"].values()) and 0 < before["quiet"] < 1
    assert before["weights"]["-0.1"] < before["weights"]["-0.05"], "a larger move is rarer under the fitted scale"
    assert before["horizon_sessions"] == sum(1 for d in study.history_calendar if "2018-12-08" < d <= day)
    later = copy.deepcopy(data)
    for name in params["symbols"]:
        for row in later[name]["bars"]:
            if row["date"] >= day:
                row["close"] *= 3.
    assert ledger_studies.WeeklyOptionPortfolioStudy(params, later)._move_weights(day, kelly, 30) == before
    earlier = copy.deepcopy(data)
    for row in earlier[params["symbols"][0]]["bars"]:
        if row["date"] == "2019-01-04":
            row["close"] *= 1.2
    changed = ledger_studies.WeeklyOptionPortfolioStudy(params, earlier)._move_weights(day, kelly, 30)
    assert changed["weights"] != before["weights"]


def test_a_market_move_reaches_correlated_names_more_than_an_independent_one(monkeypatch):
    """Two names sharing a factor carry larger betas than the lone name: the joint scenarios hit their books harder."""
    seen = _spy_utility_programs(monkeypatch)
    params, data, series, days = _chain_world([2, 1])
    params["methods"] = [{"id": "kelly050_lp", **KELLY_FORMULATIONS["kelly050_lp"]}]
    _for_kelly(params)
    params["methods"][0]["kelly"] = {**KELLY_FORMULATIONS["kelly050_lp"]["kelly"], "beta_lookback": 240}   # 60 sessions is too noisy to rank
    study = ledger_studies.WeeklyOptionPortfolioStudy(params, data)
    result = study._method(params["methods"][0], None)
    week = next(d for d in result["decisions"] if "selection" in d)
    betas = week["kelly"]["betas"]
    assert betas["C0N0"] > betas["C1N0"] and betas["C0N1"] > betas["C1N0"], "the shared factor raises beta"
    program = seen[0]
    joint = {s["id"]: s for s in program["utility"]["joint"]}
    assert set(joint) == {"market:0", *(f"market:{m}" for m in KELLY_MOVES)}
    import numpy as np
    assert sum(s["weight"] for s in joint.values()) == pytest.approx(1.)
    for key in program["utility"]["aggregate"]:
        deviation = sum(s["weight"]*np.asarray(s["terms"].get(key, 0.)) for s in joint.values())
        assert deviation == pytest.approx(0., abs=1e-9), "each book's probability-weighted deviation is zero"
    crash = joint["market:-0.1"]["terms"]
    by_name = {key.split(":")[1]: row for key, row in crash.items()}
    assert set(by_name) == {"C0N0", "C0N1", "C1N0"}
    assert max(min(by_name["C0N0"]), min(by_name["C0N1"])) < min(by_name["C1N0"]) < 0, \
        "the same short put loses more against its no-move profit where beta is larger"
    losses = week["kelly"]["losses"]
    assert set(losses) == {str(m) for m in KELLY_MOVES}
    wealth = week["selection"]["utility"]["wealth"]
    assert wealth["market:-0.1"] < wealth["market:0"]
    assert sum(s["weight"]*wealth[s["id"]] for s in joint.values()) == pytest.approx(
        week["kelly"]["w0"]+sum(week["selection"]["group_values"])), "the expected wealth is the forecast mean, whatever the moves"


def test_centred_scenarios_give_every_book_a_zero_probability_weighted_deviation():
    """A short put's profit against its no-move payoff is negative on average over the moves (the no-move payoff is not
    the forecast mean); centring makes the weighted deviation zero for each book, the quiet cell included."""
    import numpy as np

    centred = ledger_studies.WeeklyOptionPortfolioStudy._centred_scenarios
    # Loss net of credit at the shocked spot: terms[move][book]; terms[0.] the no-move loss. A short put (loss grows on a
    # fall) and a long call (loss shrinks on a rise), two items each.
    terms = {0.: {"short": [0., 10.], "long": [30., 5.]},
             -.1: {"short": [20., 40.], "long": [30., 5.]},
             .1: {"short": [0., 10.], "long": [20., 0.]},
             .2: {"short": [0., 10.], "long": [0., 0.]}}
    weights = {"-0.1": .1, "0.1": .1, "0.2": 0.}
    raw = {key: sum(weights[str(m)]*(np.asarray(terms[0.][key])-np.asarray(terms[m][key])) for m in (-.1, .1))
           for key in terms[0.]}
    assert raw["short"].min() < -1., "uncentred, the short book carries a negative mean deviation"
    scenarios = centred(terms, [-.1, .1, .2], weights, 0.8)
    assert [s["id"] for s in scenarios] == ["market:0", "market:-0.1", "market:0.1"], "a move with no weight is not modelled"
    assert sum(s["weight"] for s in scenarios) == pytest.approx(1.)
    for key in terms[0.]:
        weighted = sum(s["weight"]*np.asarray(s["terms"].get(key, [0., 0.])) for s in scenarios)
        assert weighted == pytest.approx([0., 0.], abs=1e-12)
    assert centred(terms, [-.1, .1], {"-0.1": 0., "0.1": 0.}, 1.) == [{"id": "market:0", "weight": 1., "terms": {}}]


@pytest.mark.parametrize("change", [
    lambda s: s["solver_options"].pop("mip_feasibility_tolerance"),
    lambda s: s["solver_options"].update(mip_feasibility_tolerance=1e-6),
    lambda s: s["solver_options"].update(mip_feasibility_tolerance=float("nan")),
    lambda s: s.update(parity_tolerance=1e-4),
    lambda s: s.update(group_screen="none"),
    lambda s: s.pop("group_screen"),
])
def test_a_kelly_document_declares_the_mip_feasibility_tolerance_and_a_parity_floor(change):
    """Without HiGHS's MIP feasibility tolerance at 1e-9 and a parity tolerance of 1e-3 or more, a 300,000 account's
    week is refused as model_over_audit (the charge variable sits under its lines by the solver's slack); without the
    ``lp`` group screen a real-size week does not fit in memory (ADR-0299)."""
    kelly = {"id": "kelly025_lp", **KELLY_FORMULATIONS["kelly025_lp"]}
    good, _ = _weekly_portfolio_fixture()
    good["methods"] = [kelly]
    _for_kelly(good)
    ledger_studies.WeeklyOptionPortfolioStudy.validate_configuration(good)
    bad, _ = _weekly_portfolio_fixture()
    bad["methods"] = [kelly]
    _for_kelly(bad)
    change(bad["solver"])
    with pytest.raises(ValueError, match="mip_feasibility_tolerance"):
        ledger_studies.WeeklyOptionPortfolioStudy.validate_configuration(bad)
    plain, _ = _weekly_portfolio_fixture()
    ledger_studies.WeeklyOptionPortfolioStudy.validate_configuration(plain)   # a dollar document is unchanged


def test_a_kelly_document_declares_every_gap_and_holds_no_other_method():
    """An absent mip gap is HiGHS's default (looser than the method's): refused. A kelly method runs alone."""
    params, _ = _weekly_portfolio_fixture()
    kelly = {"id": "kelly025_lp", **KELLY_FORMULATIONS["kelly025_lp"]}
    params["methods"] = [kelly]
    _for_kelly(params)
    ledger_studies.WeeklyOptionPortfolioStudy.validate_configuration(params)
    for missing in ("mip_abs_gap", "mip_rel_gap"):
        bad, _ = _weekly_portfolio_fixture()
        _for_kelly(bad)
        bad["methods"] = [kelly]
        del bad["solver"]["solver_options"][missing]
        with pytest.raises(ValueError, match="gaps"):
            ledger_studies.WeeklyOptionPortfolioStudy.validate_configuration(bad)
        plain, _ = _weekly_portfolio_fixture()
        del plain["solver"]["solver_options"][missing]
        ledger_studies.WeeklyOptionPortfolioStudy.validate_configuration(plain)   # a dollar document is unchanged
    bare, _ = _weekly_portfolio_fixture()
    bare["methods"] = [kelly]
    _for_kelly(bare)
    del bare["solver"]["solver_options"]
    with pytest.raises(ValueError, match="gaps"):
        ledger_studies.WeeklyOptionPortfolioStudy.validate_configuration(bare)
    mixed, _ = _weekly_portfolio_fixture()
    mixed["methods"] = [{"id": "locked", **FORMULATIONS["w1_010_shock10_lp"]}, kelly]
    with pytest.raises(ValueError, match="runs alone"):
        ledger_studies.WeeklyOptionPortfolioStudy.validate_configuration(mixed)
    _for_kelly(mixed)
    mixed["methods"] = [kelly, {"id": "kelly050_lp", **KELLY_FORMULATIONS["kelly050_lp"]}]
    ledger_studies.WeeklyOptionPortfolioStudy.validate_configuration(mixed)   # kelly methods may share a document
    for change in (lambda s: s["solver_options"].pop("time_limit"), lambda s: s["solver_options"].update(time_limit=900),
                   lambda s: s.pop("timeout_policy"), lambda s: s.update(timeout_policy="raise")):
        short, _ = _weekly_portfolio_fixture()
        _for_kelly(short)
        short["methods"] = [kelly]
        change(short["solver"])
        with pytest.raises(ValueError, match="time_limit"):
            ledger_studies.WeeklyOptionPortfolioStudy.validate_configuration(short)
    longer, _ = _weekly_portfolio_fixture()
    _for_kelly(longer)
    longer["solver"]["solver_options"]["time_limit"] = 3000
    longer["methods"] = [kelly]
    ledger_studies.WeeklyOptionPortfolioStudy.validate_configuration(longer)   # a longer limit is no looser


def test_a_kelly_week_records_how_many_groups_the_screen_dropped(monkeypatch):
    params, data, method = _overlapping_kelly_world()
    week = ledger_studies.WeeklyOptionPortfolioStudy(params, data)._method(method, None)["decisions"][0]
    screen = week["kelly"]["screen"]
    assert set(screen) == {"rule", "candidates", "kept", "screened", "fallback"}
    assert screen["candidates"] == week["kelly"]["candidates"] and screen["kept"]+screen["screened"] == screen["candidates"]


def test_a_kelly_method_must_declare_the_short_notional_floor_at_twenty_percent_or_more():
    """ADR-0299 audit: without the margin a zero-reserve hybrid is free leverage, so a kelly method that declares less
    than 0.2 (or nothing, or a non-number) is refused; 0.2 and more validate; a negative one is invalid as for any method."""
    floor = ledger_studies.WeeklyOptionPortfolioStudy.KELLY_SHORT_NOTIONAL_FLOOR
    assert floor == .2 == _BASE["short_notional_fraction"]

    def check(change):
        params, _ = _weekly_portfolio_fixture()
        params["methods"] = [{"id": "kelly025_lp", **KELLY_FORMULATIONS["kelly025_lp"]}]
        change(params["methods"][0])
        _for_kelly(params)
        ledger_studies.WeeklyOptionPortfolioStudy.validate_configuration(params)
    for accepted in (.2, .5):
        check(lambda m, v=accepted: m.update(short_notional_fraction=v))
    for refused in (0., .19, -.2, "0.2", None, float("nan")):
        with pytest.raises(ValueError, match="invalid fixed method"):
            check(lambda m, v=refused: m.update(short_notional_fraction=v))
    with pytest.raises(ValueError, match="invalid fixed method"):
        check(lambda m: m.pop("short_notional_fraction"))


def test_the_short_notional_floor_charges_the_gross_sold_strike_and_a_long_at_the_same_strike_does_not_net_it():
    """A padding pair (a long and a short at one strike) cannot create free contracts: the floor counts the sold leg's
    whole strike notional, the long leg adds nothing and takes nothing away."""
    charge = {"reserve_per_contract": 0., "short_notional_fraction": .2}
    legs = [{"strike": 100., "sign": -1, "quantity": 3}, {"strike": 100., "sign": 1, "quantity": 3}]
    floor = ledger_studies.WeeklyOptionPortfolioStudy._floor
    assert floor(0., charge, legs, 100) == pytest.approx(.2*100.*100.*3)
    assert floor(0., charge, legs[:1], 100) == floor(0., charge, legs, 100)
    assert floor(0., charge, legs[1:], 100) == 0.
    rows = ledger_studies.WeeklyOptionPortfolioStudy._charge_coefficients(
        charge, [{"strike": 100., "sign": -1}, {"strike": 100., "sign": 1}], 100)
    assert rows == [[2000., 0.]]


def _reversal_hybrid(w0=100000., capacity=1000):
    """ABBV's zero-reserve hybrid: short C32.5, short P115, long C115, long P92.5 on a spot law that never goes below 92.5.

    Its payoff is the same (-82.5 a share) at every spot, so the sold legs bring in 8,260 against a loss of 8,250: a
    reserve of nothing and a small certain gain, which a growth objective stakes without limit unless the margin counts.
    """
    items = [("call", 32.5, -1), ("put", 115., -1), ("call", 115., 1), ("put", 92.5, 1)]
    spots, mult = [95., 100., 105., 110., 115., 120.], 100.
    losses = [[-sign*mult*max((s-k) if right == "call" else (k-s), 0.) for right, k, sign in items] for s in spots]
    credit = [8260., 0., 0., 0.]
    reserve_rows = [[loss-c for loss, c in zip(row, credit)] for row in losses]+[[-c for c in credit]]
    group = {"id": "g", "grid": spots, "masses": [1./6.]*6, "radius": 0., "scale": 100., "credit": credit,
             "losses": losses, "capacity": [capacity]*4, "exclusive": [], "reserve_rows": reserve_rows, "linear_rows": []}
    meta = [{"strike": k, "sign": sign} for _right, k, sign in items]
    program = {"groups": [group], "caps": [{"groups": ["g"], "upper": .95*w0}],
               "utility": {"w0": w0, "gamma": 4., "n_tangents": 32, "core": 2., "wealth_lo": .01*w0, "aggregate": {"g": "g"},
                           "joint": [{"id": "market:0", "weight": 1., "terms": {}}]}}
    return program, meta, mult


def test_the_floor_sizes_a_zero_reserve_hybrid_by_margin_not_capacity():
    """Without the floor the hybrid is staked to its capacity of 1,000 packages (a certain gain with no reserve); with the
    20% short-notional floor its reserve is 2,950 a package and the stake falls to the cash it needs."""
    import copy
    from dskit.pipeline.libs.pyomo import TransportPortfolioSolve

    program, meta, mult = _reversal_hybrid()
    params = {"absolute_gap": 1e-3, "relative_gap": 0., "parity_tolerance": 1e-3, "timeout_policy": "return",
              "group_screen": "lp",
              "solver_options": {"threads": 1, "mip_abs_gap": 1e-9, "mip_rel_gap": 0., "mip_feasibility_tolerance": 1e-9,
                                 "time_limit": 60}}
    free = TransportPortfolioSolve("free", params).run(None, program)["selection"]
    assert min(free["quantities"][0][:3]) >= 900, "no margin: nearly the whole capacity of 1,000 (the long P92.5 is dead weight)"
    floored = copy.deepcopy(program)
    charge = {"reserve_per_contract": 0., "short_notional_fraction": .2}
    floored["groups"][0]["reserve_rows"] += ledger_studies.WeeklyOptionPortfolioStudy._charge_coefficients(charge, meta, mult)
    held = TransportPortfolioSolve("held", params).run(None, floored)["selection"]
    short_call, short_put, long_call, _long_put = held["quantities"][0]
    margin = .2*32.5*mult*short_call+.2*115.*mult*short_put   # 650 a short call, 2,300 a short put
    assert margin <= .95*100000.+1e-6 and margin >= .9*.95*100000., "the cash the margin needs sizes the stake"
    assert max(held["quantities"][0]) <= 150 < 900 <= min(free["quantities"][0][:3])
    assert held["reserves"][0] == pytest.approx(margin)


def test_a_kelly_weeks_trades_carry_the_floored_reserve_the_solver_and_the_ledger_share():
    """The books the selection becomes and the ledger's trades carry reserve = max(loss, -credit, 20% of the sold
    strikes' notional), the one `_floor` the solver rows use."""
    params, inputs = _kelly_fixture(fraction=.5)
    assert params["methods"][0]["short_notional_fraction"] == .2
    result = ledger_studies.WeeklyOptionPortfolioStudy(params, inputs)._method(params["methods"][0], None)
    mult = params["bridge"]["solver"]["multiplier"]
    assert result["trades"], "the fixture trades under Kelly"
    for trade in result["trades"]:
        notional = sum(.2*leg["strike"]*mult*leg["quantity"] for leg in trade["legs"] if leg["sign"] < 0)
        assert trade["reserve"] == pytest.approx(max(0., trade["max_loss"], -trade["credit"], notional))
    for week in result["decisions"]:
        for book in week.get("books", []):
            notional = sum(.2*leg["strike"]*mult*leg["quantity"] for leg in book["legs"] if leg["sign"] < 0)
            assert book["reserve"] >= notional-1e-9


def test_a_kelly_week_records_the_books_sd_ratio_and_effective_fraction():
    """Per week and method the decision records the chosen book's exact sd, the charge's dispersion, their ratio and the
    fraction the charge actually applies (the label is nominal); a comparison row keeps it; a week with no solve has none."""
    params, inputs = _kelly_fixture(fraction=.5)
    result = ledger_studies.WeeklyOptionPortfolioStudy(params, inputs)._method(params["methods"][0], None)
    solved = [d for d in result["decisions"] if d.get("status") == "selected"]
    assert solved, "the fixture trades under Kelly"
    for week in solved:
        book = week["kelly"]["book"]
        assert book == week["selection"]["utility"]["book"] and set(book) == {"sd", "dispersion", "ratio", "effective_fraction"}
        assert book["effective_fraction"] == pytest.approx(week["kelly"]["fraction"]*book["ratio"]**2)
        assert book["sd"] > 0 and book["dispersion"] > 0
    # `uncertified` is written only by a method that declares accept_uncertified (ADR-0301 amendment); the row reads it with .get.
    assert set(ledger_studies.MIOPacketCompare._KELLY_ROW_KEYS)-{"uncertified"} <= set(solved[0]["kelly"])
    for week in solved:
        solve = week["kelly"]["solve"]
        assert set(solve) == {"strategy_outcome", "bound_source"} and solve["strategy_outcome"] is not None
        assert solve == {k: week["certificates"][0][k] for k in solve}
    skipped = [d for d in result["decisions"] if d.get("status") == "kelly_risk_input"]
    assert all("book" not in d["kelly"] for d in skipped), "no solve, no record"


def test_a_kelly_week_whose_relaxation_fails_is_a_timeout_skipped_week_with_its_reason(monkeypatch):
    """The direct solve is not a fallback (it never finishes on a real week): the week does not trade, is
    `timeout_skipped`, and the decision records why (`kelly.solve`)."""
    from dskit.pipeline.libs.pyomo import TransportPortfolioSolve

    monkeypatch.setattr(TransportPortfolioSolve, "_relaxation", lambda self, solver, model, limit: None)
    params, inputs = _kelly_fixture(fraction=.5)
    result = ledger_studies.WeeklyOptionPortfolioStudy(params, inputs)._method(params["methods"][0], None)
    skipped = [d for d in result["decisions"] if d.get("status") == "timeout_skipped"]
    assert skipped and not [d for d in result["decisions"] if d.get("status") == "selected"]
    for week in skipped:
        assert week["kelly"]["solve"] == {"strategy_outcome": "relaxation_failed", "bound_source": "none"}
        assert week["kelly"]["book"] is None and week["selection"]["timeout"] == "no_incumbent"


def test_a_comparison_row_lists_the_names_a_kelly_method_dropped_and_their_weeks():
    decisions = [{"date": "2019-01-02", "kelly": {"dropped": []}}, {"date": "2019-01-07", "kelly": {"dropped": ["A", "B"]}},
                 {"date": "2019-01-14", "kelly": {"dropped": ["A"]}}, {"date": "2019-01-21", "status": "cash"}]
    assert ledger_studies.MIOPacketCompare._kelly_dropped(decisions) == {
        "names": {"A": ["2019-01-07", "2019-01-14"], "B": ["2019-01-07"]}, "name_count": 2, "name_weeks": 3}


#: `_method` digests of two shock methods (decision `seconds` removed), computed from 69579ad3's code, before `_move_terms`
#: was factored out of `_shock_rows` (ADR-0299): the refactor and every later kelly change leave a shock method byte-identical.
SHOCK_BASELINES = {
    "w1_010_shock10_lp": "5e6e63080d218b9f009320c578f2af5eef9a517517d0a87613314669176afeba",
    "w1_010_shock05_book_lp": "259cd4049e3a86ab7c0f3069b27f100cc29967c179a5aeb2b0bd1642f01d04bc"}


@pytest.mark.parametrize("name", sorted(SHOCK_BASELINES))
def test_a_shock_method_is_byte_identical_to_the_code_before_the_kelly_refactor(name):
    params, inputs = _weekly_portfolio_fixture()
    params["methods"][0].update(FORMULATIONS[name], id="nominal")
    params["methods"][0]["shock"] = {**params["methods"][0]["shock"], "beta_lookback": 2}
    result = ledger_studies.WeeklyOptionPortfolioStudy(params, inputs)._method(params["methods"][0], None)
    assert any("selection" in d for d in result["decisions"]), "the fixture trades under the shock"
    assert _timeless_digest(result) == SHOCK_BASELINES[name]


# --- review fixes (ADR-0299 amendment, 2026-11-06) ---------------------------------------------------------------------

def _overlapping_kelly_world(buffer=.05):
    """Two weeks whose fills are both allowed: week one's books are still open at week two's decision."""
    params, data, series, days = _chain_world([2, 1])
    params["clocks"]["entry_end_before"] = "2019-01-09"
    params["solver"]["parity_tolerance"] = 1e-3
    _for_kelly(params)
    method = {"id": "k", **KELLY_FORMULATIONS["kelly050_lp"]}
    method["kelly"] = {**method["kelly"], "beta_lookback": 240, "cash_buffer": buffer}
    params["methods"] = [method]
    return params, data, method


def test_a_kelly_week_sizes_on_capital_not_already_committed(monkeypatch):
    """The second overlapping week takes materially less than the first: its Kelly wealth is funding less open reserves."""
    seen = _spy_utility_programs(monkeypatch)
    params, data, method = _overlapping_kelly_world()
    result = ledger_studies.WeeklyOptionPortfolioStudy(params, data)._method(method, None)
    first, second = result["decisions"][:2]
    assert first["status"] == "selected" and second["status"] in ("selected", "cash")
    committed = second["open_reserve"]
    assert committed == pytest.approx(sum(b["reserve"] for b in first["books"])) and committed > 0
    one, two = seen[0], seen[1]
    assert one["utility"]["w0"] == 10000. and one["utility"]["wealth_lo"] == pytest.approx(100.)
    assert two["utility"]["w0"] == pytest.approx(10000.-committed), "uncommitted capital, not funding"
    assert two["utility"]["wealth_lo"] == pytest.approx(.01*(10000.-committed)), "the floor reads the same wealth"
    assert second["kelly"]["w0"] == two["utility"]["w0"]
    stake = lambda d: sum(b["reserve"] for b in d.get("books", []))
    assert stake(second) < stake(first), "stakes no longer stack"
    assert stake(first)+stake(second) <= .95*10000.+1e-6, "together within the cash row"


def test_a_kelly_week_with_no_uncommitted_capital_does_not_trade(monkeypatch):
    """Open reserves that leave nothing: no wealth to size on, so the week is `kelly_risk_input`, not a solve."""
    from dskit.pipeline.libs.option_ledger import FundedOptionAccount

    seen = _spy_utility_programs(monkeypatch)
    remaining = FundedOptionAccount.remaining
    monkeypatch.setattr(FundedOptionAccount, "remaining",
                        lambda self, members, fraction: 0. if self.open else remaining(self, members, fraction))
    params, data, method = _overlapping_kelly_world()
    first, second = ledger_studies.WeeklyOptionPortfolioStudy(params, data)._method(method, None)["decisions"][:2]
    assert first["status"] == "selected"
    assert second["status"] == "kelly_risk_input" and second["kelly"]["status"] == "nonpositive_capital"
    assert len(seen) == 1, "only the first week solved"


def test_the_kelly_solve_holds_back_the_cash_buffer_but_the_fill_does_not(monkeypatch):
    """The solve's cash row is (1 - buffer) of funding less open reserves; the fill-time limits are the whole cash rule."""
    seen = _spy_utility_programs(monkeypatch)
    params, data, method = _overlapping_kelly_world(buffer=.05)
    study = ledger_studies.WeeklyOptionPortfolioStudy(params, data)
    result = study._method(method, None)
    assert [c["upper"] for c in seen[0]["caps"]] == [pytest.approx(9500.)]
    committed = result["decisions"][1]["open_reserve"]
    assert [c["upper"] for c in seen[1]["caps"]] == [pytest.approx(9500.-committed)]
    from dskit.pipeline.libs.option_ledger import FundedOptionAccount
    account = FundedOptionAccount(10000., 0., 14, "2019-01-02")
    assert study._limits(account, method, {"groups": []})[0]["upper"] == 10000., "fill-time limits: no buffer"
    assert study._limits(account, method, {"groups": []}, solve=True)[0]["upper"] == pytest.approx(9500.)
    shock_method = {"id": "s", **FORMULATIONS["w1_010_shock10_lp"]}
    assert study._limits(account, shock_method, {"groups": []}, solve=True) == study._limits(
        account, shock_method, {"groups": []}), "a method without kelly has one set of limits"


def _adverse_fill_world(buffer, bid_drop):
    """The overlapping world with every fill-day put bid lowered by ``bid_drop`` (a worse reprice of the shorts)."""
    params, data, method = _overlapping_kelly_world(buffer)
    for name in data:
        for row in data[name]["chain"]:
            if row["date"] == "2019-01-03" and row["expiration"] == "2019-02-01" and row["strike"] == 100.:
                row["bid"] -= bid_drop
    return params, data, method


def test_one_adverse_reprice_does_not_void_the_kelly_week():
    """With cash the binding row, a worse fill reprice lifts the reserves past a cash row that was full: it voided the
    week as `repriced_shared_cap`. A 5% buffer leaves the room; without it (buffer 0) the same reprice still rejects."""
    params, data, method = _adverse_fill_world(.05, bid_drop=.3)
    result = ledger_studies.WeeklyOptionPortfolioStudy(params, data)._method(method, None)
    assert result["decisions"][0]["status"] == "selected" and result["rejected"] == [], "the books are all admitted"


def test_a_kelly_method_refuses_a_document_whose_gaps_are_looser_than_its_own():
    """The objective is dollars of growth: a 50-dollar document gap would certify a weak week as cash. The method's
    gap binds the document's certification and HiGHS gaps; the lp screen is required (a documented restriction: the
    screen is refused when `none`, in the tolerance test above)."""
    params, inputs = _weekly_portfolio_fixture()
    params["methods"] = [{"id": "kelly025_lp", **KELLY_FORMULATIONS["kelly025_lp"]}]
    _for_kelly(params)
    ledger_studies.WeeklyOptionPortfolioStudy.validate_configuration(params)
    for loose in ({"absolute_gap": 50.}, {"relative_gap": .11}, {"solver_options": {"mip_abs_gap": 50., "threads": 1, "time_limit": 2400}},
                  {"solver_options": {"mip_rel_gap": .11, "threads": 1, "time_limit": 2400, "mip_abs_gap": 1.}}):
        bad, _ = _weekly_portfolio_fixture()
        _for_kelly(bad)
        bad["methods"] = params["methods"]
        bad["solver"].update(loose)
        with pytest.raises(ValueError, match="solver gaps"):
            ledger_studies.WeeklyOptionPortfolioStudy.validate_configuration(bad)
        plain, _ = _weekly_portfolio_fixture()
        plain["solver"].update(loose)
        ledger_studies.WeeklyOptionPortfolioStudy.validate_configuration(plain)   # a dollar document keeps its gap


def test_the_kelly_formulations_certify_at_a_relative_ten_percent_within_2400_seconds():
    """The owner's decision (ADR-0299, 2026-11-06), after the measured real week on the Mac: the half-Kelly LP finished at
    1153 s with a certified gap of 2.29%; the quarter-Kelly LP had not in 1500 s. The gap is a relative 10% and the limit
    2400 s; a document at the limit validates, one 1 s short or one gap step looser refuses, one at the 5% gap still passes."""
    assert KELLY_GAP == {"absolute": 1., "relative": .10} and KELLY["time_limit"] == 2400
    for method in KELLY_FORMULATIONS.values():
        assert method["kelly"]["gap"] == {"absolute": 1., "relative": .10} and method["kelly"]["time_limit"] == 2400
    params, _ = _weekly_portfolio_fixture()
    params["methods"] = [{"id": "kelly050_lp", **KELLY_FORMULATIONS["kelly050_lp"]}]
    _for_kelly(params)
    assert params["solver"]["solver_options"]["time_limit"] == 2400, "the helper declares the method's own limit"
    ledger_studies.WeeklyOptionPortfolioStudy.validate_configuration(params)
    for change, match in ((lambda s: s["solver_options"].update(time_limit=2399), "time_limit"),
                          (lambda s: s["solver_options"].update(time_limit=1500), "time_limit"),
                          (lambda s: s.update(relative_gap=.11), "solver gaps"),
                          (lambda s: s["solver_options"].update(mip_rel_gap=.11), "solver gaps")):
        bad, _ = _weekly_portfolio_fixture()
        _for_kelly(bad)
        bad["methods"] = params["methods"]
        change(bad["solver"])
        with pytest.raises(ValueError, match=match):
            ledger_studies.WeeklyOptionPortfolioStudy.validate_configuration(bad)
    for tighter in (lambda s: s.update(relative_gap=.05), lambda s: s["solver_options"].update(mip_rel_gap=.05)):
        ok, _ = _weekly_portfolio_fixture()
        _for_kelly(ok)
        ok["methods"] = params["methods"]
        tighter(ok["solver"])
        ledger_studies.WeeklyOptionPortfolioStudy.validate_configuration(ok)   # a tighter gap is no looser


def _t4_cdf(x):
    """The Student-t df 4 distribution function in closed form (independent of the code under test)."""
    return .5+.375*(x/math.sqrt(1.+x*x/4.))*(1.-(x*x/12.)/(1.+x*x/4.))


def test_move_weights_use_student_t_four_tails_at_the_estimated_volatility():
    """Each move's cell mass is Student t df 4 with the basket's variance (scale = sigma / sqrt 2); the normal understates the tails."""
    params, data, series, days = _chain_world([2, 1])
    kelly = {**KELLY, "fraction": .5}
    study = ledger_studies.WeeklyOptionPortfolioStudy(params, data)
    got = study._move_weights("2019-01-07", kelly, 30)
    assert got["tail_df"] == 4
    sigma = got["sigma_daily"]*math.sqrt(got["horizon_sessions"])
    scale = sigma/math.sqrt(2.)
    points = sorted({0., *(math.log1p(m) for m in kelly["moves"])})
    edges = [-math.inf, *((a+b)/2. for a, b in zip(points, points[1:])), math.inf]
    cdf = lambda e: 0. if e == -math.inf else 1. if e == math.inf else _t4_cdf(e/scale)
    for move in kelly["moves"]:
        k = points.index(math.log1p(move))
        assert got["weights"][str(move)] == pytest.approx(cdf(edges[k+1])-cdf(edges[k]), abs=1e-9)
    normal = lambda e: 0. if e == -math.inf else 1. if e == math.inf else .5*(1.+math.erf(e/(sigma*math.sqrt(2.))))
    k = points.index(math.log1p(-.1))
    assert got["weights"]["-0.1"] > normal(edges[k+1])-normal(edges[k]), "the fat tail puts more weight on the crash"


def test_a_nonfinite_bar_drops_that_name_for_the_week_not_the_week(monkeypatch):
    """A NaN close in one name's window drops that name from the basket and the candidates, visibly; the others trade."""
    # The study refuses a NaN close at construction; a bar that went bad after it (or a zero) reaches the estimate.
    seen = _spy_utility_programs(monkeypatch)
    params, data, series, days = _chain_world([2, 1])
    params["clocks"]["entry_end_before"] = "2019-01-09"   # so 2019-01-07 has a fill session
    params["solver"]["parity_tolerance"] = 1e-3
    _for_kelly(params)
    params["methods"] = [{"id": "kelly050_lp", **KELLY_FORMULATIONS["kelly050_lp"]}]
    study = ledger_studies.WeeklyOptionPortfolioStudy(params, data)
    bad = params["symbols"][0]
    study._bars[bad]["2019-01-04"] = float("nan")
    estimate = study._kelly_estimate("2019-01-07", 60)
    assert estimate["dropped"] == [bad] and set(estimate["betas"]) == set(params["symbols"])-{bad}
    assert study._move_weights("2019-01-07", KELLY, 30, estimate["names"])["status"] == "estimated"
    whole_basket = study._move_weights("2019-01-07", KELLY, 30)
    assert whole_basket["status"] == "nonfinite_history" and whole_basket["sigma_daily"] is None
    assert study._move_weights("2019-01-07", KELLY, 30, [])["status"] == "nonfinite_history"
    result = study._method(params["methods"][0], None)
    week = next(d for d in result["decisions"] if d["date"] == "2019-01-07")
    assert week["status"] in ("selected", "cash") and week["kelly"]["status"] == "estimated"
    assert week["kelly"]["dropped"] == [bad] and week["kelly"]["candidates"] == week["candidate_books"], "counted after the drop"
    assert bad not in week["eligible_by_symbol"] and sum(week["eligible_by_symbol"].values()) == week["candidate_books"]
    assert bad not in week["kelly"]["betas"]
    program = next(p for p in seen if p["groups"][0]["id"].startswith("2019-01-07:"))
    assert len(program["groups"]) == week["kelly"]["candidates"] and not any(bad in g["id"] for g in program["groups"])
    assert all(bad not in key for s in program["utility"]["joint"] for key in s["terms"])
    for symbol in params["symbols"]:
        study._bars[symbol]["2019-01-04"] = float("nan")
    whole = study._method(params["methods"][0], None)
    week = next(d for d in whole["decisions"] if d["date"] == "2019-01-07")
    assert week["status"] == "kelly_risk_input" and week["kelly"]["status"] == "no_candidate_with_history"
    assert sorted(week["kelly"]["dropped"]) == sorted(params["symbols"])


def test_the_move_horizon_is_the_candidate_weighted_mean_dte_not_the_longest():
    meta = [{"dte": 10}, {"dte": 10}, {"dte": 10}, {"dte": 40}]
    assert ledger_studies.WeeklyOptionPortfolioStudy._mean_dte(meta) == 17.5
    assert ledger_studies.WeeklyOptionPortfolioStudy._mean_dte([]) == 0
    params, data, method = _overlapping_kelly_world()
    result = ledger_studies.WeeklyOptionPortfolioStudy(params, data)._method(method, None)
    week = result["decisions"][0]
    study = ledger_studies.WeeklyOptionPortfolioStudy(params, data)
    metadata = study._prepare(week["date"])[1]
    assert week["kelly"]["horizon_days"] == ledger_studies.WeeklyOptionPortfolioStudy._mean_dte(metadata)


# --- the select-then-size Kelly formulations (ADR-0300) and the top-K one-stage Kelly (ADR-0301) ---------------------

#: Stage 1 is the primary formulation unchanged; stage 2 sizes its books on 1,000 sampled joint settlements at a
#: quarter Kelly, with ADR-0299's envelope, floor and cash buffer, loadings over the shock's 60-session window.
SIZER = {"fraction": .25, "scenarios": 1000, "n_tangents": 32, "core": 2., "wealth_floor": .01, "cash_buffer": .05, "exposure": "cash_rule",
         "beta_lookback": 60, "mean": "nominal", "seed": 20261107, "factors": "market", "loadings": "trailing", "shrink": 0.}
#: The ADR-0300 amendment's combinable parts (owner ordered 2026-11-08): the component-level factor with stressed
#: loadings, and the declared pooling of the selected units' edges per reserve toward their common level.
CFACTOR = {"factors": "market_component", "loadings": "stressed"}
SHRINK = {"shrink": .5}
SIZED_FORMULATIONS = {
    "kelly025_sized_lp": {**FORMULATIONS["w1_010_shock10_lp"], "sizer": SIZER},
    "kelly025_sized_w1_lp": {**FORMULATIONS["w1_010_shock10_lp"], "sizer": {**SIZER, "mean": "w1_worst"}},
    "kelly050_sized_lp": {**FORMULATIONS["w1_010_shock10_lp"], "sizer": {**SIZER, "fraction": .5}},
    # The leverage-matched control: the sizer reallocates within the capital stage 1 would have committed.
    "kelly025_sized_matched_lp": {**FORMULATIONS["w1_010_shock10_lp"], "sizer": {**SIZER, "exposure": "stage1_reserve"}},
    # The amendment's arms: the one consistent drawdown reducer as stage 1; the component factor; the pooled means.
    "kelly025_sized_comp25_lp": {**FORMULATIONS["w1_010_component25_shock10_lp"], "sizer": SIZER},
    "kelly025_sized_cfactor_lp": {**FORMULATIONS["w1_010_shock10_lp"], "sizer": {**SIZER, **CFACTOR}},
    "kelly025_sized_shrunk_lp": {**FORMULATIONS["w1_010_shock10_lp"], "sizer": {**SIZER, **SHRINK}},
}
#: The 25-point-grid document (its own arm, as w1_010_shock10_fine_lp is): the plain sizer, and everything combined.
FINE_FORMULATIONS = {
    "kelly025_sized_fine_lp": {**FORMULATIONS["w1_010_shock10_lp"], "sizer": SIZER},
    "kelly025_sized_combo_fine_lp": {**FORMULATIONS["w1_010_component25_shock10_lp"], "sizer": {**SIZER, **CFACTOR, **SHRINK}},
}
#: ADR-0299's one-stage quarter Kelly restricted to the 100 best groups after the lp screen (ADR-0301).
TOP_FORMULATIONS = {
    "kelly025_top100_lp": {**_KELLY_BASE, "kelly": {**KELLY, "fraction": .25, "max_groups": 100}},
    # ADR-0301 amendment (owner, 2026-11-08, option A): the same program, trading the support incumbent of a week whose
    # certification was declined, with the gap to the relaxation's bound recorded instead of the week skipped.
    "kelly025_top100_uncert_lp": {**_KELLY_BASE, "kelly": {**KELLY, "fraction": .25, "max_groups": 100,
                                                          "accept_uncertified": True}},
    # ADR-0302 (owner option B, 2026-11-09): the uncertified arm, its declined weeks certified from the complete incumbent
    # under a node budget, and the incumbent improved first by a seeded neighbourhood search on exact sampled utility.
    "kelly025_top100_search_lp": {**_KELLY_BASE, "kelly": {
        **KELLY, "fraction": .25, "max_groups": 100, "accept_uncertified": True,
        "warm_start": {"node_limit": 1000},
        "search": {"iterations": 30, "neighbourhood": 10, "node_limit": 500, "seed": 20261109}}},
}


def _sizer_fixture(mean="nominal", fraction=.25, rho=0., **sizer):
    """The weekly fixture under a sizer method: week one has no prior session (a hold), week two sizes."""
    params, inputs = _weekly_portfolio_fixture()
    params.update(correlation_lookback=5, correlation_min_pairs=2)
    params["methods"] = [{"id": "sized", **_BASE, "rho": rho,
                          "sizer": {**SIZER, "fraction": fraction, "mean": mean, "beta_lookback": 3, **sizer}}]
    return params, inputs


def test_the_sized_and_top_formulations_validate_and_differ_only_where_they_say():
    base, comp25 = FORMULATIONS["w1_010_shock10_lp"], FORMULATIONS["w1_010_component25_shock10_lp"]
    for name, method in {**SIZED_FORMULATIONS, **FINE_FORMULATIONS}.items():
        params, inputs = _weekly_portfolio_fixture()
        params["methods"] = [{"id": name, **method}]
        ledger_studies.WeeklyOptionPortfolioStudy.validate_configuration(params)
        stage1 = comp25 if name in ("kelly025_sized_comp25_lp", "kelly025_sized_combo_fine_lp") else base
        assert {k: v for k, v in method.items() if k != "sizer"} == stage1, "stage 1 is a locked formulation, unchanged"
    assert SIZED_FORMULATIONS["kelly025_sized_w1_lp"]["sizer"] == {**SIZER, "mean": "w1_worst"}
    assert SIZED_FORMULATIONS["kelly050_sized_lp"]["sizer"] == {**SIZER, "fraction": .5}
    assert SIZED_FORMULATIONS["kelly025_sized_matched_lp"]["sizer"] == {**SIZER, "exposure": "stage1_reserve"}
    assert SIZED_FORMULATIONS["kelly025_sized_comp25_lp"]["sizer"] == SIZER
    assert SIZED_FORMULATIONS["kelly025_sized_cfactor_lp"]["sizer"] == {**SIZER, "factors": "market_component",
                                                                        "loadings": "stressed"}
    assert SIZED_FORMULATIONS["kelly025_sized_shrunk_lp"]["sizer"] == {**SIZER, "shrink": .5}
    assert FINE_FORMULATIONS["kelly025_sized_fine_lp"] == SIZED_FORMULATIONS["kelly025_sized_lp"]
    assert FINE_FORMULATIONS["kelly025_sized_combo_fine_lp"]["sizer"] == {**SIZER, "factors": "market_component",
                                                                          "loadings": "stressed", "shrink": .5}
    assert SIZER["exposure"] == "cash_rule" and SIZER["mean"] == "nominal" and SIZER["factors"] == "market"
    assert SIZER["loadings"] == "trailing" and SIZER["shrink"] == 0.
    assert len(SIZED_FORMULATIONS) == 7 and len(FINE_FORMULATIONS) == 2
    for name, method in TOP_FORMULATIONS.items():
        params, inputs = _weekly_portfolio_fixture()
        params["methods"] = [{"id": name, **method}]
        _for_kelly(params)
        ledger_studies.WeeklyOptionPortfolioStudy.validate_configuration(params)
    assert TOP_FORMULATIONS["kelly025_top100_lp"]["kelly"] == {**KELLY_FORMULATIONS["kelly025_lp"]["kelly"],
                                                               "max_groups": 100}


@pytest.mark.parametrize("mutate", [
    lambda m: m["sizer"].update(fraction=0.),
    lambda m: m["sizer"].update(fraction=1.5),
    lambda m: m["sizer"].update(scenarios=1),
    lambda m: m["sizer"].update(scenarios=100001),
    lambda m: m["sizer"].update(scenarios=1000.),
    lambda m: m["sizer"].update(n_tangents=1),
    lambda m: m["sizer"].update(core=1.),
    lambda m: m["sizer"].update(wealth_floor=0.),
    lambda m: m["sizer"].update(wealth_floor=1.),
    lambda m: m["sizer"].update(cash_buffer=-.01),
    lambda m: m["sizer"].update(cash_buffer=1.),
    lambda m: m["sizer"].update(beta_lookback=1),
    lambda m: m["sizer"].update(mean="robust"),
    lambda m: m["sizer"].update(exposure="matched"),
    lambda m: m["sizer"].pop("exposure"),
    lambda m: m["sizer"].update(factors="sector"),
    lambda m: m["sizer"].update(loadings="implied"),
    lambda m: m["sizer"].update(shrink=1.),
    lambda m: m["sizer"].update(shrink=-.1),
    lambda m: m["sizer"].pop("shrink"),
    lambda m: m["sizer"].update(mean="w1_worst", shrink=.5),   # pooling and the haircut do not combine
    lambda m: m["sizer"].update(loadings="stressed"),   # the fixture's beta_lookback 3 is below the 20-session short window
    lambda m: m["sizer"].update(seed=-1),
    lambda m: m["sizer"].update(seed=1.),
    lambda m: m["sizer"].pop("seed"),
    lambda m: m["sizer"].update(extra=1),
    lambda m: m.update(kelly={**KELLY, "fraction": .25}),
    lambda m: m.update(portfolio_fraction=.04),   # below the cash buffer: the stage-2 cash rule would be negative
    lambda m: m.update(sizer=None),
    lambda m: m.update(risk_charge={"measure": "mad", "price": .5}),   # the sized books would carry no charge
    lambda m: m.update(shock={**SHOCK10, "scope": "filled_account"}),  # its post-fill admission would undo the sizing
    lambda m: m.update(short_notional_fraction=.1),                     # the floor a growth objective needs (ADR-0299)
    lambda m: m.pop("short_notional_fraction"),
])
def test_a_malformed_sizer_block_is_refused(mutate):
    params, inputs = _sizer_fixture()
    mutate(params["methods"][0])
    with pytest.raises(ValueError):
        ledger_studies.WeeklyOptionPortfolioStudy.validate_configuration(params)


def test_a_sizer_needs_as_many_sessions_in_its_window_as_the_study_needs_to_estimate():
    params, inputs = _sizer_fixture()
    params["correlation_min_pairs"] = 4   # above the fixture's beta_lookback of 3
    with pytest.raises(ValueError, match="beta_lookback"):
        ledger_studies.WeeklyOptionPortfolioStudy.validate_configuration(params)
    params["methods"][0]["sizer"]["beta_lookback"] = 4
    ledger_studies.WeeklyOptionPortfolioStudy.validate_configuration(params)


def test_a_stage_two_time_limit_before_any_incumbent_is_a_skipped_week_and_replays_as_recorded(monkeypatch):
    """A halt before HiGHS finds even the cash point is a timeout certificate, not an error; the week is
    timeout_skipped with both certificates recorded, and a replay keeps the recorded timeout (the stage-2 limit is
    forced on the sizer alone, so stage 1 still selects)."""
    from index_options.kelly_sizer import BookKellySizer

    params, inputs = _sizer_fixture()
    method = params["methods"][0]
    monkeypatch.setattr(BookKellySizer, "_solver_options", lambda self: {**BookKellySizer._HIGHS_PINS, "time_limit": 1e-4})
    result = ledger_studies.WeeklyOptionPortfolioStudy(params, inputs)._method(method, None)
    weeks = [d for d in result["decisions"] if d.get("sizer", {}).get("status") == "timeout"]
    assert weeks, "the stage-2 solve cannot finish in a tenth of a millisecond"
    week = weeks[0]
    assert week["status"] == "timeout_skipped" and not week["books"] and week["sizer"]["incumbent"] is False
    assert week["sizer"]["solve"]["termination"] == "maxTimeLimit" and week["sizer"]["solve"]["objective"] is None
    assert len(week["certificates"]) == 2 and week["certificates"][1] == week["sizer"]["solve"]
    assert "stage1" in week and week["selection"] == week["stage1"]["selection"], "the stage-1 selection stays recorded"
    assert result["trades"] == [] and result["unfilled"] == 0
    assert ledger_studies.WeeklyOptionPortfolioStudy(params, inputs)._method(method, None, recorded=result) == result
    monkeypatch.undo()
    assert ledger_studies.WeeklyOptionPortfolioStudy(params, inputs)._method(method, None, recorded=result) == result, \
        "a recorded stage-2 timeout is kept as recorded, whatever this host's own limit"


@pytest.mark.parametrize("max_groups,ok", [(1, True), (100, True), (0, False), (1., False), (None, False)])
def test_a_kelly_block_may_cap_the_groups_the_screen_keeps(max_groups, ok):
    params, inputs = _kelly_fixture(max_groups=max_groups)
    if ok:
        ledger_studies.WeeklyOptionPortfolioStudy.validate_configuration(params)
    else:
        with pytest.raises(ValueError):
            ledger_studies.WeeklyOptionPortfolioStudy.validate_configuration(params)


def test_a_kelly_week_under_max_groups_records_the_cap_and_keeps_no_more_groups_than_it(monkeypatch):
    params, inputs = _kelly_fixture(max_groups=1)
    result = ledger_studies.WeeklyOptionPortfolioStudy(params, inputs)._method(params["methods"][0], None)
    weeks = [d for d in result["decisions"] if "screen" in d.get("kelly", {}) and d["kelly"]["screen"]]
    assert weeks, "a solved week records its screen"
    for week in weeks:
        assert week["kelly"]["screen"]["keep"] == 1 and week["kelly"]["screen"]["kept"] <= 1
        assert week["kelly"]["screen"]["rule"] == "lp"


def _sized_week(result):
    """The sized decisions of a result (status selected or cash after stage 2)."""
    return [d for d in result["decisions"] if d.get("sizer", {}).get("status") == "sized"]


def test_a_sizer_week_keeps_the_primary_books_and_resizes_them_by_whole_units(monkeypatch):
    """Stage 1 is the dollar program under every cap it declares; stage 2 multiplies each book's primitive unit by a
    whole count within the depth, admitted under the cash rule alone; both certificates are recorded."""
    seen = _spy_programs(monkeypatch)
    params, inputs = _sizer_fixture()
    method = params["methods"][0]
    study = ledger_studies.WeeklyOptionPortfolioStudy(params, inputs)
    result = study._method(method, None)
    first, second = result["decisions"][0], _sized_week(result)[0]
    assert first["status"] == "kelly_risk_input" and first["sizer"]["status"] == "insufficient_history"
    assert not ({"stage1", "selection", "certificates"} & set(first)), "a week that cannot size solves nothing"
    assert second["sizer"]["status"] == "sized" and second["status"] == "selected"
    assert len(seen) == 1, "one stage-1 solve: the held week solved nothing"
    assert min(c[0] for c in seen[0]["caps"]) == pytest.approx(params["weekly_fraction"]*second["funding"]), \
        "the stage-1 program carries the weekly budget cap"
    stage1 = second["stage1"]["selection"]
    assert len(second["certificates"]) == 2 and len(second["stage1"]["certificates"]) == 1
    assert second["certificates"][0] == second["stage1"]["certificates"][0]
    assert second["certificates"][1] == second["sizer"]["solve"]
    units = {u["group"]: u for u in second["sizer"]["units"]}
    ids = [m["id"] for m in study._prepare(second["date"])[1]]
    for i, (before, after) in enumerate(zip(stage1["quantities"], second["selection"]["quantities"])):
        if not any(before):
            assert not any(after)
            continue
        unit = units[ids[i]]
        g = math.gcd(*before)
        assert after == [q//g*unit["k"] for q in before], "the sized book is k primitive units of the stage-1 book"
        assert 0 <= unit["k"] <= unit["k_max"] and unit["units_selected"] == g
        assert unit["stage1_contracts"] == sum(before) and unit["contracts"] == sum(after)
        assert unit["mean_target"] == unit["mean_nominal"] and not unit["target_clamped"] and unit["sample_sd"] >= 0
        assert unit["weighted_mean"] == pytest.approx(unit["mean_target"], abs=1e-6), "the tilt reaches the target"
        assert -unit["reserve"]-1e-9 <= unit["worst_profit"] <= unit["best_profit"] <= unit["credit"]+1e-9, \
            "reweighting keeps every sampled profit inside the unit's structural range"
    assert second["sizer"]["stake_ratio"] == pytest.approx(
        sum(u["contracts"] for u in second["sizer"]["units"])/sum(u["stage1_contracts"] for u in second["sizer"]["units"]))
    assert second["sizer"]["reserve_ratio"] == pytest.approx(second["sizer"]["reserve"]/second["sizer"]["stage1_reserve"])
    assert second["sizer"]["stage1_reserve"] == pytest.approx(sum(stage1["reserves"]))
    assert second["sizer"]["exposure"] == "cash_rule" and second["sizer"]["cash_limit_applied"] == second["sizer"]["cash_limit"]
    assert len(second["sizer"]["alternates"]) == 2 and all("k" in a for a in second["sizer"]["alternates"])
    assert second["sizer"]["tilt"]["effective_scenarios"] <= SIZER["scenarios"] and second["sizer"]["tilt"]["kl"] >= 0
    assert second["sizer"]["tilt"]["converged"] and second["sizer"]["tilt"]["gap"] <= 1e-9
    for book in second["books"]:
        assert book["reserve"] == pytest.approx(sum(second["selection"]["reserves"]), abs=1e-6) or len(second["books"]) > 1
        assert book["status"] == "selected"
    assert second["sizer"]["reserve"] <= second["sizer"]["cash_limit"]+1e-6
    assert second["sizer"]["wealth_min"] >= second["sizer"]["wealth_lo"]-1e-6
    assert second["sizer"]["names"] == ["X"] and second["sizer"]["correlations"] == {"X": 0.}, \
        "flat bars: no volatility, no loading"
    assert second["sizer"]["held"] == []
    assert result["trades"], "the sized book fills under the cash rule"
    assert result["unfilled"] == 0


def test_a_sizer_week_is_stable_across_seeds_within_one_unit():
    counts = {}
    for seed in (1, 2):
        params, inputs = _sizer_fixture(seed=seed)
        result = ledger_studies.WeeklyOptionPortfolioStudy(params, inputs)._method(params["methods"][0], None)
        counts[seed] = {u["id"]: u["k"] for d in _sized_week(result) for u in d["sizer"]["units"]}
    assert counts[1] and set(counts[1]) == set(counts[2])
    assert all(abs(counts[1][k]-counts[2][k]) <= 1 for k in counts[1])


def test_the_w1_worst_mean_never_sizes_above_the_nominal_mean():
    totals = {}
    for mean in ("nominal", "w1_worst"):
        # rho .03 on the fixture: the robust mean of the one selected spread is near zero, the nominal is not.
        params, inputs = _sizer_fixture(mean=mean, rho=.03)
        result = ledger_studies.WeeklyOptionPortfolioStudy(params, inputs)._method(params["methods"][0], None)
        weeks = _sized_week(result)
        assert weeks
        for week in weeks:
            for unit in week["sizer"]["units"]:
                assert unit["mean_w1_worst"] <= unit["mean_nominal"]+1e-9
                wanted = unit["mean_nominal" if mean == "nominal" else "mean_w1_worst"]
                assert unit["mean_target"] == wanted or (unit["target_clamped"] and unit["mean_target"] > wanted)
                assert unit["weighted_mean"] == pytest.approx(unit["mean_target"], abs=1e-6)
        totals[mean] = sum(u["contracts"] for d in weeks for u in d["sizer"]["units"])
    assert totals["w1_worst"] <= totals["nominal"]


def test_a_tilt_that_concentrates_the_weight_is_not_sized_on(monkeypatch):
    """Below the effective-scenario floor the week sizes on the untilted sample and records the refused tilt."""
    monkeypatch.setattr(ledger_studies.WeeklyOptionPortfolioStudy, "_SIZER_MIN_EFFECTIVE_SHARE", 1.)   # nothing passes
    params, inputs = _sizer_fixture(mean="w1_worst", rho=.03)
    result = ledger_studies.WeeklyOptionPortfolioStudy(params, inputs)._method(params["methods"][0], None)
    week = _sized_week(result)[0]
    tilt = week["sizer"]["tilt"]
    assert tilt["applied"] == "untilted_fallback" and set(tilt["refused"]) == {"converged", "gap", "effective_scenarios",
                                                                              "max_weight", "kl"}
    assert tilt["effective_scenarios"] == pytest.approx(SIZER["scenarios"]) and tilt["kl"] == pytest.approx(0., abs=1e-9)
    for unit in week["sizer"]["units"]:
        assert unit["weighted_mean"] == pytest.approx(unit["sample_mean_raw"]), "the untilted sample's own mean"
    assert ledger_studies.WeeklyOptionPortfolioStudy(params, inputs)._method(params["methods"][0], None,
                                                                            recorded=result) == result


def _two_name_sizer_fixture(**sizer):
    """Two names with different spreads on the 100 put, so the units' means differ and a component exists."""
    import copy

    params, inputs = _sizer_fixture(**sizer)
    params["symbols"] = ["X", "Y"]
    inputs["Y"] = copy.deepcopy(inputs["X"])
    for rows in inputs["Y"].values():
        for row in rows:
            row["symbol"] = "Y"
    for row in inputs["Y"]["chain"]:
        if row["strike_raw"] == 100.:
            row["bid"], row["ask"] = 3.4, 3.6   # a smaller credit than X's 4.0 / 4.2: a smaller mean per reserve
    for row in inputs["X"]["chain"]:
        if row["strike_raw"] == 100.:
            row["bid_size"] = row["ask_size"] = 2   # X's depth runs out, so the budget reaches Y too
    params["weekly_fraction"] = .8                   # room for X's two floored units (4,000) and Y's (2,000 each)
    return params, inputs


def test_the_component_factor_and_stressed_loadings_run_and_are_recorded():
    """With a component factor and the stressed window the week sizes and records the factor structure (the fixture's
    flat bars give no loading at all, so the mechanics are the module's tests; here the plumbing and the record)."""
    params, inputs = _two_name_sizer_fixture(factors="market_component", loadings="stressed", beta_lookback=20)
    params["correlation_min_pairs"] = 2
    result = ledger_studies.WeeklyOptionPortfolioStudy(params, inputs)._method(params["methods"][0], None)
    week = _sized_week(result)[0]
    s = week["sizer"]
    assert s["factors"] == "market_component" and s["loadings"] == "stressed"
    assert set(s["correlations"]) == {"X", "Y"} and all(0 <= r <= .95 for r in s["correlations"].values())
    assert isinstance(s["component_loadings"], dict) and isinstance(s["component_correlations"], dict)
    for entry in s["component_loadings"].values():
        assert set(entry) == {"component", "loading"} and 0 <= entry["loading"] <= .95
    assert "sigma_daily_short" in s and len(s["units"]) == 2
    assert ledger_studies.WeeklyOptionPortfolioStudy(params, inputs)._method(params["methods"][0], None,
                                                                            recorded=result) == result


def test_shrinkage_pools_the_units_means_per_reserve_by_the_declared_share():
    params, inputs = _two_name_sizer_fixture(shrink=.5)
    result = ledger_studies.WeeklyOptionPortfolioStudy(params, inputs)._method(params["methods"][0], None)
    week = _sized_week(result)[0]
    s = week["sizer"]
    units = s["units"]
    assert len(units) == 2 and s["shrink"] == .5
    pooled = sum(u["mean_unshrunk"] for u in units)/sum(u["reserve"] for u in units)
    assert s["pooled_mean_per_reserve"] == pytest.approx(pooled)
    assert units[0]["mean_unshrunk"] != pytest.approx(units[1]["mean_unshrunk"]), "the two names differ"
    for u in units:
        assert u["mean_shrunk"] == pytest.approx(u["reserve"]*(.5*u["mean_unshrunk"]/u["reserve"]+.5*pooled))
        assert u["mean_target"] == pytest.approx(u["mean_shrunk"]) or u["target_clamped"]
    plain = ledger_studies.WeeklyOptionPortfolioStudy(*_two_name_sizer_fixture())._method(params["methods"][0] | {
        "sizer": {**params["methods"][0]["sizer"], "shrink": 0.}}, None)
    for u in _sized_week(plain)[0]["sizer"]["units"]:
        assert u["mean_shrunk"] == u["mean_unshrunk"], "share 0 pools nothing"


def test_the_leverage_matched_arm_commits_no_more_reserve_than_stage_one():
    params, inputs = _sizer_fixture(exposure="stage1_reserve")
    result = ledger_studies.WeeklyOptionPortfolioStudy(params, inputs)._method(params["methods"][0], None)
    weeks = _sized_week(result)
    assert weeks
    for week in weeks:
        s = week["sizer"]
        assert s["exposure"] == "stage1_reserve" and s["cash_limit_applied"] == pytest.approx(min(s["cash_limit"], s["stage1_reserve"]))
        assert s["reserve"] <= s["stage1_reserve"]+1e-6 and s["reserve_ratio"] <= 1.+1e-9
    free = ledger_studies.WeeklyOptionPortfolioStudy(*_sizer_fixture())._method(params["methods"][0] | {"sizer": {**params["methods"][0]["sizer"], "exposure": "cash_rule"}}, None)
    assert _sized_week(free)[0]["sizer"]["reserve"] >= weeks[0]["sizer"]["reserve"], "the cash rule alone allows at least as much"


def test_the_recorded_route_replays_a_sizer_method_and_refuses_a_changed_count():
    import copy

    params, inputs = _sizer_fixture()
    method = params["methods"][0]
    result = ledger_studies.WeeklyOptionPortfolioStudy(params, inputs)._method(method, None)
    assert _sized_week(result)
    assert ledger_studies.WeeklyOptionPortfolioStudy(params, inputs)._method(method, None, recorded=result) == result
    changed = copy.deepcopy(result)
    week = _sized_week(changed)[0]
    week["selection"]["quantities"] = [[2*q for q in row] for row in week["selection"]["quantities"]]
    with pytest.raises(ValueError, match="recorded sizer counts"):
        ledger_studies.WeeklyOptionPortfolioStudy(params, inputs)._method(method, None, recorded=changed)
    forged = copy.deepcopy(result)
    _sized_week(forged)[0]["sizer"]["solve"]["objective"] += 1.
    with pytest.raises(ValueError, match="recorded sizer certificate"):
        ledger_studies.WeeklyOptionPortfolioStudy(params, inputs)._method(method, None, recorded=forged)
    skipped = copy.deepcopy(result)
    _sized_week(skipped)[0]["sizer"]["status"] = "timeout"   # a traded week dressed as a stage-2 timeout
    with pytest.raises(ValueError, match="recorded sizer timeout"):
        ledger_studies.WeeklyOptionPortfolioStudy(params, inputs)._method(method, None, recorded=skipped)


def test_the_derived_and_policy_routes_refuse_a_sizer_method():
    params, inputs = _sizer_fixture()
    study = ledger_studies.WeeklyOptionPortfolioStudy(params, inputs)
    method = params["methods"][0]
    with pytest.raises(ValueError, match="sizer method"):
        study._method(method, None, derived={"method": method["id"], "weeks": study.weeks, "decisions": {}})
    with pytest.raises(ValueError, match="sizer method"):
        next(study.policy_steps(method["id"]))


def test_a_sizer_week_models_an_open_book_with_a_current_forecast_and_reserves_the_rest():
    """An open book whose expiry the week still forecasts enters at its sampled outcomes; one it does not, at its
    reserve; the Kelly wealth is funding less every open reserve."""
    import copy
    from dskit.pipeline.libs.option_ledger import FundedOptionAccount

    params, inputs = _sizer_fixture()
    method = params["methods"][0]
    study = ledger_studies.WeeklyOptionPortfolioStudy(params, inputs)
    result = study._method(method, None)
    week = _sized_week(result)[0]
    day = week["date"]
    groups, metadata, _ = study._prepare(day)
    mult = params["bridge"]["solver"]["multiplier"]
    charge = study._charge(method)
    configured = [{**g, "radius": method["rho"], "scale": study._group_scale(method, g, meta),
                   "reserve_rows": g["reserve_rows"]+study._charge_coefficients(charge, meta["items"], mult)}
                  for g, meta in zip(groups, metadata)]
    selection = week["stage1"]["selection"]
    books = study._books(selection, metadata, charge)
    account = FundedOptionAccount(params["initial"], params["deposit"], params["deposit_every_days"], day)
    current = {**copy.deepcopy(books[0]), "id": "held-current"}
    stale = {**copy.deepcopy(books[0]), "id": "held-stale", "expiry": "2019-02-01"}
    assert account.admit(day, [current, stale], [{"members": ["X"], "upper": 1e9}])
    record = study._sizer_inputs(day, method, account)
    assert record["status"] == "estimated"
    out = study._size_books(day, method, configured, metadata, account, selection, books, mult, record, None)
    assert out["status"] == "sized" and out["record"] is record
    assert record["held"] == [{"id": "held-current", "modelled": True, "group": books[0]["id"]},
                              {"id": "held-stale", "modelled": False}]
    assert record["open_reserve"] == pytest.approx(current["reserve"]+stale["reserve"])
    assert record["w0"] == pytest.approx(account.funding-record["open_reserve"])
    assert record["wealth_lo"] == pytest.approx(method["sizer"]["wealth_floor"]*record["w0"])
    assert record["cash_limit"] == pytest.approx(max(0., (1.-method["sizer"]["cash_buffer"])*account.funding
                                                     - record["open_reserve"]))
    assert record["wealth_min"] >= record["wealth_lo"]-1e-6
    assert all(u["k"] <= u["k_max"] for u in record["units"])
    assert out["certificate"]["termination"] == "optimal"


def test_a_method_without_the_sizer_key_is_byte_identical_to_the_code_before():
    """The dollar digests hold: a method without ``sizer`` never reaches the sizer (the radius baselines run elsewhere
    in this module; here the fixture's own method, with and without a no-op change of the sizer code path)."""
    params, inputs = _weekly_portfolio_fixture()
    study = ledger_studies.WeeklyOptionPortfolioStudy(params, inputs)
    result = study._method(params["methods"][0], None)
    assert all("sizer" not in d and "stage1" not in d for d in result["decisions"])
    assert _timeless_digest(result) == RADIUS_BASELINES["weekly", 0.]


# --- the uncertified support incumbent (ADR-0301 amendment; owner option A, 2026-11-08) ---------------------------------

def _declined_kelly_fixture(**kelly):
    """A kelly week whose certification is always declined (no action may stay free) under a gap nothing can meet."""
    params, inputs = _kelly_fixture(fraction=.5, **kelly)
    params["solver"].update(absolute_gap=1e-9, relative_gap=0.)
    params["solver"]["solver_options"].update(mip_abs_gap=1e-9, mip_rel_gap=0.)
    params["methods"][0]["kelly"]["gap"] = {"absolute": 1e-9, "relative": 0.}
    return params, inputs


def test_a_declined_week_trades_its_support_incumbent_only_when_the_method_accepts_uncertified(monkeypatch):
    from dskit.pipeline.libs.pyomo import TransportPortfolioSolve

    monkeypatch.setattr(TransportPortfolioSolve, "UTILITY_CERTIFY_ACTIONS", 0)
    params, inputs = _declined_kelly_fixture()
    skipped = ledger_studies.WeeklyOptionPortfolioStudy(params, inputs)._method(params["methods"][0], None)
    declined = [d for d in skipped["decisions"] if d.get("kelly", {}).get("solve", {}).get("strategy_outcome") == "declined"]
    assert declined, "the fixture's certification is declined"
    assert all(d["status"] == "timeout_skipped" and not d["books"] and "uncertified" not in d["kelly"] for d in declined), \
        "a method without the key carries no field: earlier kelly records stay identical"
    assert all(d["selection"]["timeout"] == "gap" and d["selection"]["candidate"] is not None for d in declined)
    params, inputs = _declined_kelly_fixture(accept_uncertified=True)
    method = params["methods"][0]
    traded = ledger_studies.WeeklyOptionPortfolioStudy(params, inputs)._method(method, None)
    weeks = [d for d in traded["decisions"] if d.get("kelly", {}).get("uncertified")]
    assert weeks and len(weeks) == len(declined)
    for week in weeks:
        u = week["kelly"]["uncertified"]
        assert u["outcome"] == "declined" and u["gap"] is not None and u["gap"] > 1e-9 and u["bound"] >= u["value"]
        assert week["status"] in ("selected", "cash") and week["selection"]["timeout"] == "gap", \
            "the recorded selection stays the timeout envelope; the trade is the candidate"
        assert week["books"] == ledger_studies.WeeklyOptionPortfolioStudy(params, inputs)._books(
            week["selection"]["candidate"], ledger_studies.WeeklyOptionPortfolioStudy(params, inputs)._prepare(week["date"])[1],
            ledger_studies.WeeklyOptionPortfolioStudy._charge(method)) or week["status"] == "cash"
        assert week["kelly"]["book"] is not None and week["kelly"]["losses"]
    assert any(d["status"] == "selected" for d in weeks), "the support incumbent holds a book on the fixture"
    assert ledger_studies.WeeklyOptionPortfolioStudy(params, inputs)._method(method, None, recorded=traded) == traded
    failed = _declined_kelly_fixture(accept_uncertified=True)
    monkeypatch.setattr(TransportPortfolioSolve, "_relaxation", lambda self, solver, model, limit: None)
    result = ledger_studies.WeeklyOptionPortfolioStudy(*failed)._method(failed[0]["methods"][0], None)
    assert all(d["status"] == "timeout_skipped" and d["kelly"]["uncertified"] is None
               for d in result["decisions"] if "kelly" in d and "selection" in d), "a failed relaxation still skips"


@pytest.mark.parametrize("value,ok", [(True, True), (False, True), (1, False), ("yes", False)])
def test_accept_uncertified_is_a_boolean_kelly_key(value, ok):
    params, inputs = _kelly_fixture(accept_uncertified=value)
    if ok:
        ledger_studies.WeeklyOptionPortfolioStudy.validate_configuration(params)
    else:
        with pytest.raises(ValueError):
            ledger_studies.WeeklyOptionPortfolioStudy.validate_configuration(params)
    top = TOP_FORMULATIONS["kelly025_top100_uncert_lp"]["kelly"]
    assert top == {**TOP_FORMULATIONS["kelly025_top100_lp"]["kelly"], "accept_uncertified": True}


# --- ADR-0313 I2: MethodBlocks wiring with locked-formulation parity, and the decision_weeks study param -------------

#: Per formulation of A0010 on the weekly fixture, pinned from main's 7f2d03b BEFORE this stage: the `_method` digest
#: (decision `seconds` removed), the number of programs solved, the hash of their `program_identity` list and the hash of
#: the programs themselves. No block and no `decision_weeks`: all four stay byte-identical (A0010 binding).
NINE_PINS = {
    "w1_010_shock10_lp": ("c2398f4db0ad1a8abdabaac627e13a05288de4e7b89c7d27bb4a7c47f3d2aa25", 2, "b72b643c09063a09beafdbde6743052d520f15471b50c227e33f3b896e3c7eee", "866373de3fe65245dbb01f8d90f1bd6d03e8a7d50e02ca914446b51996cae37f"),
    "w1_003_shock10_lp": ("0f137de760cedf430b49cc5eda24c71c51acbd8a4c5ff6cdbe69ae0c35f20fa5", 2, "6dd9f701e6d29792b5b6d39d98dbc06b81f97e5743b965bb8c45b53dd8db16b6", "29a2117ebc2d9961ada0d00e276f839942a2f2f62324376d61775ba22bfc227a"),
    "w1_003_shock10_mad050_lp": ("eeae2dca4ca90eb51cf552ac7409f2eb15b0d86e5dea22ae5d313c06d111f77d", 2, "3d35538c065bd84d59a7177a376529cb5033279c2a1f572b711f58beae735f7e", "e4e0a742ba6bc13c87001ff8e838bd8d0848c3f79bb818f3b377cf0502de0767"),
    "nominal_lp": ("562379a02524e6ce9bde42771aa7cdd886eed8577d06dfefeeed81dec64c8145", 2, "4f6ceb270596e97433382e81593e5093318c58b45f09e10d7a9f13f81ecc72a0", "fe2ae803fa3d54be3ccd810dcc08f8cedae82a345eeb9c62a9528231ac116569"),
    "w1_010z_shock10_lp": ("88cdd3f25093d2b03766d36ed80de8ddaf1ab35185737769c6c30647f73a49be", 2, "b0ae29f057df93a35aff6a025d27e1491fc240b94a8afe3e4f8e9bb02002a843", "185e5ffe434a2426fec61285793de19390c368b4b5966331fa0b6ed981b8e2fa"),
    "w1_010_shock10_fine_lp": ("1603389287c1b48ec63de88401d40337d852d3ac25aa5cc4ba5aba73a5d94860", 2, "b72b643c09063a09beafdbde6743052d520f15471b50c227e33f3b896e3c7eee", "866373de3fe65245dbb01f8d90f1bd6d03e8a7d50e02ca914446b51996cae37f"),
    "w1_010_component25_shock10_lp": ("7489be7f79bf289e19eed9992f76cc9b70a76914d3abb9952f27c48b4c4e130c", 2, "b3be2b4f31a9cb0a8d2e1ab5be737bb630a362d4dabe6702b4388570f656a715", "08f9eb7b402e1d7485cf7785fe64359c0b98bcc81002cc29e81b38145100d5b0"),
    "w1_010_name25_shock10_lp": ("83be73c942f3dde2dc70c3612fa962eb321a625250595bb44ea65ae5e2d29fdc", 2, "ca3a74986d9e2359de6c2d49e4c3a13cd8f401ee30922ac5ddd0bd5397fff1d8", "a83b9415164406eb55c0d421e8a0eccbceb9a3bc6f62bf4e4a92d40f598bbd59"),
    "w1_010_shock05_book_lp": ("2de47444d6a369051f98d9a48ea86d0d28dd369e6be0f10cf36c895898046df3", 2, "b55be71131a4990da4fb974fbca4cffa022127da451d2119e86c50305f6cc6f8", "6604160f6dcdede2fc38a4fd7dde9d1f687594f80f042c7e8ae6726d90b920d7"),
}
_CAL = {"source": "edge_maps", "state": "expanding_fill", "edge_scale": "notional", "regressors": 1,
        "line": "bucket", "form": "slope", "variant": "point", "warmup": "no_trade", "min_warm_weeks": 8}
_LIM = {"beta_delta_fraction": 0.5, "vega_fraction_per_pt": 0.02, "dte_edges": [8, 22], "beta_lookback": 60,
        "scope": "account"}
_HEDGE = {"kind": "fixed_delta_forward", "mode": "program", "ratio": 1.0, "delta_source": "black76_chain_forward",
          "iv_source": "vendor", "rate": 0.0, "margin_fraction": 0.15, "round_units": True, "condition": None,
          "small_delta": 0.0, "hedge_cost_source": "placeholder", "htb_lookback": 20, "htb_vol_threshold": 2.5,
          "htb_jump_threshold": 0.75, "hedge_fee_per_share": 0.005, "hedge_half_spread_bps": 5.0,
          "hedge_borrow_rate": 0.005}
_BLOCKS = {"calibration": _CAL, "exposure_limits": _LIM, "hedge": _HEDGE}
_PARITY = {"parity_strikes": 3, "min_pairs": 1, "max_pair_spread_frac": 0.5}
_GREEKS_LEG = {"rate": 0.0, "iv_source": "vendor", "min_years": 0.001, "vol_floor": 0.01}


def test_the_nine_formulations_are_byte_identical_to_main_before_the_wiring(monkeypatch):
    """A0010 binding: digests, program_identity and the programs of the nine do not move."""
    import hashlib
    import json

    from dskit.pipeline.libs.pyomo import TransportActionScore, TransportPortfolioSolve

    assert set(NINE_PINS) == set(FORMULATIONS)
    for name, method in FORMULATIONS.items():
        params, inputs = _weekly_portfolio_fixture()
        params["methods"] = [{"id": name, **method}]
        study = ledger_studies.WeeklyOptionPortfolioStudy(params, inputs)
        synthetic = params["methods"][0]
        if "shock" in method:
            # Preserve the historical two-session fixture and its pins at the internal solve seam.
            synthetic = {**synthetic, "shock": {**method["shock"], "beta_lookback": 2}}
        seen, run = [], TransportPortfolioSolve.run
        monkeypatch.setattr(TransportPortfolioSolve, "run",
                            lambda self, ctx, inp, run=run, seen=seen: (seen.append(inp), run(self, ctx, inp))[1])
        result = study._method(synthetic, None)
        monkeypatch.setattr(TransportPortfolioSolve, "run", run)
        tolerance = params["solver"]["parity_tolerance"]
        identities = [TransportActionScore(p, {"parity_tolerance": tolerance}).program_identity for p in seen]
        from dskit.pipeline.base import value_hash
        assert (_timeless_digest(result), len(seen), hashlib.sha256(json.dumps(identities).encode()).hexdigest(),
                value_hash(seen)) == NINE_PINS[name], name


def test_the_nine_ids_are_the_queue_configs_selection_formulations():
    import json
    import pathlib

    config = json.loads((pathlib.Path(__file__).parent.parent / "docs/plans/mio-parallel/queue.example.json").read_text())
    assert list(FORMULATIONS) == config["selection_formulations"]


def _block_doc(block=None, *, method_id="research_arm", locked=True):
    """A clocked document (tail before the development seal) with one method carrying ``block``."""
    params, inputs = _packet_clock_fixture()
    method = {**params["methods"][0], "id": method_id}
    for key, value in (block or {}).items():
        method[key] = value
    params["methods"] = [method]
    if locked:
        params["locked_method_ids"] = list(FORMULATIONS)
    if "hedge" in (block or {}):
        params["hedge_parity"] = dict(_PARITY)
    if "exposure_limits" in (block or {}):
        params["exposure_greeks"] = {"leg": dict(_GREEKS_LEG), "parity": dict(_PARITY)}
    return params, inputs


def _refused_block(params, code):
    with pytest.raises(ValueError, match=code):
        ledger_studies.WeeklyOptionPortfolioStudy.validate_configuration(params)


def test_the_three_block_keys_come_from_the_owner():
    keys = ledger_studies.WeeklyOptionPortfolioStudy._OPTIONAL_METHOD_KEYS
    assert {"calibration", "hedge", "exposure_limits"} <= keys
    assert {"reserve_per_contract", "short_notional_fraction", "shock", "risk_charge", "radius_scale", "kelly",
            "sizer"} <= keys


@pytest.mark.parametrize("key", sorted(_BLOCKS))
def test_a_block_is_accepted_on_a_new_method_id(key):
    params, _ = _block_doc({key: dict(_BLOCKS[key])})
    ledger_studies.WeeklyOptionPortfolioStudy.validate_configuration(params)


@pytest.mark.parametrize("key", sorted(_BLOCKS))
def test_a_block_is_refused_on_every_one_of_the_nine(key):
    for locked_id in FORMULATIONS:
        params, _ = _block_doc({key: dict(_BLOCKS[key])}, method_id=locked_id)
        _refused_block(params, "block_on_locked_formulation")


@pytest.mark.parametrize("key", sorted(_BLOCKS))
def test_a_block_needs_the_locked_ids_and_the_seal(key):
    params, _ = _block_doc({key: dict(_BLOCKS[key])}, locked=False)
    _refused_block(params, "locked_ids_missing")
    params, _ = _block_doc({key: dict(_BLOCKS[key])})
    params["clocks"]["tail_end_before"] = "2021-07-02"
    params["bridge"]["protected_end_before"] = "2022-01-01"
    _refused_block(params, "integration_seal")
    params, _ = _block_doc({key: dict(_BLOCKS[key])})
    del params["clocks"], params["selected_pairs"]
    _refused_block(params, "integration_seal")


def test_unknown_keys_and_bad_block_params_are_still_refused():
    params, _ = _block_doc()
    params["methods"][0]["calibratio"] = dict(_CAL)
    _refused_block(params, "invalid fixed method")
    params, _ = _block_doc({"calibration": {**_CAL, "typo": 1}})
    _refused_block(params, "block_unknown_key")
    params, _ = _block_doc({"calibration": {**_CAL, "form": "cubic"}})
    _refused_block(params, "block_param_value")
    params, _ = _block_doc({"exposure_limits": "all"})
    _refused_block(params, "block_type")
    params, _ = _block_doc({"calibration": dict(_CAL)})
    params["methods"][0]["kelly"] = {}
    with pytest.raises(ValueError):
        ledger_studies.WeeklyOptionPortfolioStudy.validate_configuration(params)


def test_locked_method_ids_is_an_optional_list_of_ids():
    for bad in ("w1_010_shock10_lp", [1], [""], None):
        params, _ = _block_doc({"calibration": dict(_CAL)})
        params["locked_method_ids"] = bad
        with pytest.raises(ValueError):
            ledger_studies.WeeklyOptionPortfolioStudy.validate_configuration(params)
    params, _ = _weekly_portfolio_fixture()
    ledger_studies.WeeklyOptionPortfolioStudy.validate_configuration(params)  # absent: fine, the nine need none


@pytest.mark.parametrize("declaration", [[], list(reversed(FORMULATIONS)), list(FORMULATIONS)[1:],
                                         [*FORMULATIONS, "extra"]])
def test_the_weekly_study_refuses_noncanonical_locked_declarations(declaration):
    params, _ = _block_doc({"calibration": dict(_CAL)})
    params["locked_method_ids"] = declaration
    with pytest.raises(ValueError, match="locked_method_ids_changed"):
        ledger_studies.WeeklyOptionPortfolioStudy.validate_configuration(params)


@pytest.mark.parametrize("declaration", [False, True])
def test_plain_weekly_study_requires_exact_locked_specs(declaration):
    params, _ = _weekly_portfolio_fixture()
    if declaration:
        params["locked_method_ids"] = list(FORMULATIONS)
    params["methods"] = [{"id": "nominal_lp", **FORMULATIONS["nominal_lp"]}]
    ledger_studies.WeeklyOptionPortfolioStudy.validate_configuration(params)
    params["methods"][0]["rho"] = .5
    with pytest.raises(ValueError, match="locked_formulation_spec_changed"):
        ledger_studies.WeeklyOptionPortfolioStudy.validate_configuration(params)


@pytest.mark.parametrize("alias", ["NOMINAL_LP", " nominal_lp ", "nominal_lp "])
def test_weekly_study_refuses_locked_id_aliases(alias):
    params, _ = _weekly_portfolio_fixture()
    params["methods"] = [{"id": alias, **FORMULATIONS["nominal_lp"]}]
    with pytest.raises(ValueError, match="locked_method_id_collision"):
        ledger_studies.WeeklyOptionPortfolioStudy.validate_configuration(params)


def test_the_owner_is_always_given_method_keys_and_locked_method_ids(monkeypatch):
    from dskit.pipeline.method_blocks import MethodBlocks

    seen = []
    real = MethodBlocks.validate
    monkeypatch.setattr(MethodBlocks, "validate", lambda self, method, context=None: (seen.append(context), real(self, method, context))[1])
    params, _ = _block_doc({"calibration": dict(_CAL)})
    ledger_studies.WeeklyOptionPortfolioStudy.validate_configuration(params)
    params, _ = _weekly_portfolio_fixture()
    ledger_studies.WeeklyOptionPortfolioStudy.validate_configuration(params)
    assert len(seen) == 2 and all("method_keys" in c and "locked_method_ids" in c and "clocks" in c for c in seen)
    study = ledger_studies.WeeklyOptionPortfolioStudy
    assert set(seen[0]["method_keys"]) == study._METHOD_KEYS | (study._OPTIONAL_METHOD_KEYS - {"calibration", "hedge", "exposure_limits"})
    assert seen[0]["locked_method_ids"] == list(FORMULATIONS)


def test_a_block_method_is_not_derived():
    params, inputs = _block_doc({"exposure_limits": dict(_LIM)})
    study = ledger_studies.WeeklyOptionPortfolioStudy(params, inputs)
    with pytest.raises(ValueError, match="block_not_derived"):
        study._method(params["methods"][0], None, derived={"method": "research_arm", "weeks": study.weeks, "decisions": {}})


def test_a_document_with_no_block_carries_no_new_key():
    params, inputs = _weekly_portfolio_fixture()
    result = ledger_studies.WeeklyOptionPortfolioStudy(params, inputs).run()
    assert "decision_weeks" not in result and "decision_weeks" not in result["methods"][0]
    assert "locked_method_ids" not in result["params"]


def _only(weeks_index):
    params, inputs = _weekly_portfolio_fixture()
    study = ledger_studies.WeeklyOptionPortfolioStudy(params, inputs)
    params["decision_weeks"] = [study.weeks[weeks_index]]
    return params, inputs, study.weeks


def test_one_listed_week_solves_only_that_week(monkeypatch):
    base_params, base_inputs = _weekly_portfolio_fixture()
    base = ledger_studies.WeeklyOptionPortfolioStudy(base_params, base_inputs)._method(base_params["methods"][0], None)
    params, inputs, weeks = _only(1)
    study = ledger_studies.WeeklyOptionPortfolioStudy(params, inputs)
    prepared = []
    real = ledger_studies.WeeklyOptionPortfolioStudy._prepare
    monkeypatch.setattr(ledger_studies.WeeklyOptionPortfolioStudy, "_prepare",
                        lambda self, day: (prepared.append(day), real(self, day))[1])
    result = study._method(params["methods"][0], None)
    assert prepared == [weeks[1]]
    assert [d["date"] for d in result["decisions"]] == weeks == [d["date"] for d in base["decisions"]]
    assert [d["status"] for d in result["decisions"] if d["date"] != weeks[1]] == ["week_not_selected"] * (len(weeks) - 1)
    listed = next(d for d in result["decisions"] if d["date"] == weeks[1])
    assert listed["status"] == "selected" and "selection" in listed
    assert len(result["trades"]) == 1
    assert result["decision_weeks"] == [weeks[1]]


def test_skipped_weeks_are_plain_cash_and_the_account_continues():
    base_params, base_inputs = _weekly_portfolio_fixture()
    base = ledger_studies.WeeklyOptionPortfolioStudy(base_params, base_inputs)._method(base_params["methods"][0], None)
    params, inputs, weeks = _only(1)
    result = ledger_studies.WeeklyOptionPortfolioStudy(params, inputs)._method(params["methods"][0], None)
    assert result["contributions"] == base["contributions"] == 12000. and result["deposits"] == base["deposits"]
    assert [c["date"] for c in result["curve"]] == [c["date"] for c in base["curve"]]
    before = [c for c in result["curve"] if c["date"] <= weeks[1]]
    assert before and all(c["reserve"] == 0 and c["cash"] == c["funding"] == c["contributions"] and not c["open_ids"]
                          for c in before), "skipped weeks are cash, deposits still arrive"
    skipped = result["decisions"][0]
    assert skipped["funding"] == base["decisions"][0]["funding"] and skipped["open_reserve"] == 0.


def test_a_decision_weeks_result_replays_and_is_not_evidence():
    params, inputs, weeks = _only(1)
    study = ledger_studies.WeeklyOptionPortfolioStudy(params, inputs)
    document = study.run()
    method = params["methods"][0]
    assert ledger_studies.WeeklyOptionPortfolioStudy(params, inputs)._method(method, None, recorded=document["methods"][0]) \
        == document["methods"][0]
    assert document["decision_weeks"] == [weeks[1]]
    with pytest.raises(ValueError, match="check_document_not_evidence"):
        ledger_studies.WeeklyOptionPortfolioStudy.check_document_not_evidence(document)
    with pytest.raises(ValueError, match="check_document_not_evidence"):
        ledger_studies.WeeklyOptionPortfolioStudy.check_document_not_evidence(document["methods"][0])
    plain = ledger_studies.WeeklyOptionPortfolioStudy(*_weekly_portfolio_fixture()).run()
    ledger_studies.WeeklyOptionPortfolioStudy.check_document_not_evidence(plain)


@pytest.mark.parametrize("bad", [[], "2019-01-07", ["2019-01-02", "2019-01-07"], ["2019-01-02", "2019-01-02"],
                                 ["2019-01-03"], ["2019-1-2"], [20190102], None, ["2018-01-02"]])
def test_decision_weeks_must_be_one_date_that_is_a_study_week(bad):
    params, inputs = _weekly_portfolio_fixture()
    params["decision_weeks"] = bad
    with pytest.raises(ValueError, match="decision_weeks_invalid"):
        ledger_studies.WeeklyOptionPortfolioStudy(params, inputs)


def test_decision_weeks_shape_is_refused_by_validate_configuration_too():
    params, _ = _weekly_portfolio_fixture()
    params["decision_weeks"] = ["2019-01-02", "2019-01-07"]
    with pytest.raises(ValueError, match="decision_weeks_invalid"):
        ledger_studies.WeeklyOptionPortfolioStudy.validate_configuration(params)


# --- ADR-0312 Amendment 1 S3.1: parity pin before the hedge wiring (no block: nothing moves, no hedge field) ----------

#: The `_timeless_digest` of the weekly fixture's plain `nominal` method, pinned at 0189c04 BEFORE the S3 wiring.
PLAIN_NOMINAL_DIGEST = "c2ebdcb1d6f54f739c9c7748e2c989c4dc0c35670da1d142ee5523b6162c1bcd"


def test_a_method_without_a_hedge_block_is_byte_identical_and_carries_no_hedge_field():
    import json

    params, inputs = _weekly_portfolio_fixture()
    result = ledger_studies.WeeklyOptionPortfolioStudy(params, inputs)._method(params["methods"][0], None)
    assert _timeless_digest(result) == PLAIN_NOMINAL_DIGEST
    assert "hedge" not in json.dumps(result), "hedge fields are ABSENT, not null, without a block"
    assert all("hedge" not in book for book in result["trades"] + result["rejected"])



# --- ADR-0312 Amendment 1 S3.2: the replay-mode hedge on the study (ReplayHedge) ------------------------------------------

_REPLAY_BLOCK = {**_HEDGE, "mode": "replay", "htb_lookback": 6}


def _hedge_world(bump=None):
    """The weekly fixture with a full call/put chain for the 2019-02-01 expiry and a drifting synthetic spot.

    Every day's rows price at forward 1.001 x spot with vol 0.3 (so every parity pair returns that forward exactly).
    ``bump(day, right, strike, row)`` may change a row. Returns ``(params, inputs, spots)``.
    """
    from datetime import date as d_

    from dskit.pipeline.option_pricing import black76

    params, inputs = _weekly_portfolio_fixture()
    days = [r["date"] for r in inputs["X"]["bars"]]
    spots = {day: 100.0 + 0.5 * i for i, day in enumerate(days)}
    expiry, chain = "2019-02-01", []
    chain.append({"symbol": "X", "date": expiry, "expiration": expiry, "type": "put", "strike_raw": 90.,
                  "strike": 90., "bid": .05, "ask": .06, "bid_size": 10, "ask_size": 10, "basis_factor": 1.,
                  "underlying_price": spots[expiry], "iv": 0.3})        # the expiry session's basis factor
    for day in days:
        if day >= expiry:
            continue
        years = (d_.fromisoformat(expiry) - d_.fromisoformat(day)).days / 365
        for right in ("call", "put"):
            for strike in (90., 95., 100., 105., 110.):
                mid = black76(right, spots[day] * 1.001, strike, 0.3, years)
                row = {"symbol": "X", "date": day, "expiration": expiry, "type": right, "strike_raw": strike,
                       "strike": strike, "bid": mid * .99, "ask": mid * 1.01, "bid_size": 10, "ask_size": 10,
                       "basis_factor": 1., "underlying_price": spots[day], "iv": 0.3}
                if bump:
                    bump(day, right, strike, row)
                chain.append(row)
    inputs["X"]["chain"] = chain
    inputs["X"]["bars"] = [{"symbol": "X", "date": day, "close": spots[day]} for day in days]
    return params, inputs, spots


def _hedge_study(bump=None, block=None, spot_at_expiry=None):
    params, inputs, spots = _hedge_world(bump)
    if spot_at_expiry is not None:
        spots["2019-02-01"] = spot_at_expiry
        for row in inputs["X"]["bars"]:
            if row["date"] == "2019-02-01":
                row["close"] = spot_at_expiry
    study = ledger_studies.WeeklyOptionPortfolioStudy(params, inputs)
    return study, ledger_studies.ReplayHedge(study, {**_REPLAY_BLOCK, **(block or {})}, _PARITY), spots


def _filled(study, legs, decision="2019-01-15", fill="2019-01-16"):
    """Fill a hand-built book of ``(right, sign, strike, quantity)`` legs on ``fill`` through the study's own `_fill`."""
    book = {"id": "b1", "symbol": "X", "expiry": "2019-02-01", "factor": 1., "decision_date": decision,
            "legs": [{"right": r, "sign": sg, "strike": k, "quantity": q} for r, sg, k, q in legs],
            "contracts": sum(q for *_, q in legs), "credit": 0., "reserve": 0., "max_loss": 0.}
    filled, reason = study._fill(book, fill, None)
    assert reason is None
    return filled


def test_the_units_come_from_the_decision_session_forward_delta_truncated_once():
    from dskit.pipeline.option_pricing import black76_delta

    study, hedge, spots = _hedge_study()
    book = _filled(study, [("put", 1, 100., 3)])
    hedge.attach(book, "2019-01-16", None)
    decision_forward = spots["2019-01-15"] * 1.001
    delta = 3 * 100 * black76_delta("put", decision_forward, 100., 0.3, 17 / 365)
    record = book["hedge"]
    assert record["status"] == "ok" and record["units"] == math.trunc(-1.0 * delta) > 0
    assert record["raw"] == pytest.approx(-delta) and record["rounding_sh"] == pytest.approx(record["units"] + delta)
    assert isinstance(record["units"], int), "truncated once, here: the ledger is told not to round again"
    # the fill session's forward is the strike of the leg, NOT the decision's, NOT the spot
    assert record["reference"] == pytest.approx(spots["2019-01-16"] * 1.001) and record["reference_source"] == "parity"


def test_the_fill_and_mark_forwards_exclude_the_books_own_strikes():
    one = {**_PARITY, "parity_strikes": 1}        # the single nearest pair: the book's own strike would be the one used
    study, _, spots = _hedge_study(bump=lambda day, right, k, row: row.update(
        bid=row["bid"] + 20., ask=row["ask"] + 20.) if (right == "call" and k == 105. and day >= "2019-01-16") else None)
    hedge = ledger_studies.ReplayHedge(study, _REPLAY_BLOCK, one)
    book = _filled(study, [("put", 1, 105., 3)])
    hedge.attach(book, "2019-01-16", None)
    assert spots["2019-01-16"] == 105.0
    assert book["hedge"]["reference"] == pytest.approx(spots["2019-01-16"] * 1.001), "the poisoned 105 pair is excluded"
    path = hedge.path(book)
    assert path["2019-01-22"] == (pytest.approx(spots["2019-01-22"] * 1.001), False)
    assert hedge.forward("X", "2019-01-22", "2019-02-01", 1., ())[0] != pytest.approx(spots["2019-01-22"] * 1.001), \
        "without the exclusion the poisoned pair would be the nearest"


def test_a_mark_without_a_parity_pair_falls_back_to_the_carry_ratio_flagged_and_the_robustness_rule_uses_it_everywhere():
    study, hedge, spots = _hedge_study(bump=lambda day, right, k, row: row.update(bid_size=0, ask_size=0)
                                       if day == "2019-01-22" else None)
    book = _filled(study, [("put", 1, 100., 3)])
    hedge.attach(book, "2019-01-16", None)
    ref = book["hedge"]["reference"]
    path = hedge.path(book)
    assert path["2019-01-22"] == (pytest.approx(spots["2019-01-22"] * ref / spots["2019-01-16"]), True)
    assert path["2019-01-23"][1] is False and list(path)[0] == "2019-01-16" and "2019-02-01" not in path
    carry = ledger_studies.ReplayHedge(study, _REPLAY_BLOCK, _PARITY, mark_rule="carry_ratio").path(book)
    assert path["2019-01-16"] == carry["2019-01-16"] and all(v[1] for d, v in carry.items() if d > "2019-01-16")
    assert carry["2019-01-23"][0] == pytest.approx(spots["2019-01-23"] * ref / spots["2019-01-16"])


def test_the_hedged_reserve_is_the_hedged_max_loss_plus_the_closing_allowance_by_hand():
    study, hedge, spots = _hedge_study()
    book = _filled(study, [("put", 1, 100., 3)])
    before = dict(book)
    hedge.attach(book, "2019-01-16", None)
    record, units, ref = book["hedge"], book["hedge"]["units"], book["hedge"]["reference"]
    credit = book["credit"]
    loss_at_zero = -(credit + 3 * 100 * 100. - units * ref)        # put payoff 100 a share on 300 shares; the shares go to 0
    loss_at_strike = -(credit + units * (100. - ref))
    allowance = record["closing_cost_allowance"]
    rule = StaticDeltaHedge_ = None  # noqa: F841
    from dskit.pipeline.option_exposure import StaticDeltaHedge
    expect = StaticDeltaHedge({"hedge_fee_per_share": .005, "hedge_half_spread_bps": 5.0, "hedge_borrow_rate": 0.})\
        .carry_costs(units, ref, 2 * ref, record["days"])["trade_costs"]
    assert allowance == pytest.approx(expect)
    hedged_loss = max(0., loss_at_zero, loss_at_strike)
    assert book["max_loss"] == pytest.approx(max(loss_at_zero, loss_at_strike))
    assert book["reserve"] == pytest.approx(max(hedged_loss + allowance, 0.15 * units * ref))
    assert record["option_reserve"] == before["reserve"] and record["unhedged_max_loss"] == before["max_loss"]


def test_the_forward_leg_settles_with_the_book_and_the_shared_ledger_agrees_to_the_cent():
    from dskit.pipeline.libs.option_ledger import FundedOptionAccount
    from dskit.pipeline.option_exposure import StaticDeltaHedge

    study, hedge, spots = _hedge_study(spot_at_expiry=92.)
    book = _filled(study, [("put", 1, 100., 3)])
    hedge.attach(book, "2019-01-16", None)
    record = book["hedge"]
    units, ref = record["units"], record["reference"]
    account = FundedOptionAccount(10000., 1000., 14, "2019-01-02")
    account.open[book["id"]] = book
    settled = study._settlements(account, "2019-02-01")
    costs = StaticDeltaHedge({"hedge_fee_per_share": .005, "hedge_half_spread_bps": 5.0, "hedge_borrow_rate": 0.}) \
        .carry_costs(units, ref, 92., record["days"])["trade_costs"]
    options = book["credit"] + 100 * 3 * (100. - 92.)
    assert settled[book["id"]] == pytest.approx(options + units * (92. - ref) - costs)
    account.advance("2019-02-01", settled)
    assert account.realized == pytest.approx(settled[book["id"]]) and account.funding == account.contributions + account.realized
    ledger = study._ledger(account.closed, None, hedge)             # raises on any PnL or reserve mismatch
    row = ledger["trades"][0]
    assert row["pnl_usd"] == pytest.approx(settled[book["id"]]) and row["hedge_fees_usd"] == pytest.approx(costs)
    assert row["hedge_pnl_usd"] == pytest.approx(units * (92. - ref)) and row["mark_forward_carried"] == 0
    mid = next(m for m in ledger["marks"] if m["date"] == "2019-01-23")
    assert mid["hedge_pnl_usd"] == pytest.approx(units * (hedge.path(book)["2019-01-23"][0] - ref))
    assert ledger["summary"]["hedge"]["mark_forward_carried"] == 0


def test_a_book_without_a_hedge_leg_goes_through_the_plain_ledger_and_the_two_passes_merge_in_account_order():
    from dskit.pipeline.libs.option_ledger import FundedOptionAccount

    study, hedge, spots = _hedge_study(spot_at_expiry=92.)
    first, second = _filled(study, [("put", 1, 100., 3)]), _filled(study, [("put", -1, 95., 1)])
    second["id"] = "b2"
    for book in (first, second):
        hedge.attach(book, "2019-01-16", None)
    assert second["hedge"]["status"] == "hedge_unbounded_above" and second["hedge"]["units"] == 0
    account = FundedOptionAccount(10000., 1000., 14, "2019-01-02")
    account.open.update({"b2": second, "b1": first})
    account.advance("2019-02-01", study._settlements(account, "2019-02-01"))
    order = [b["id"] for b in account.closed]
    ledger = study._ledger(account.closed, None, hedge)
    assert [t["order_index"] for t in ledger["trades"]] == [0, 1]
    assert {m["order_index"] for m in ledger["marks"]} == {0, 1}
    by_index = {t["order_index"]: t for t in ledger["trades"]}
    hedged_index = order.index("b1")
    assert by_index[hedged_index]["hedge_fees_usd"] > 0 and by_index[1 - hedged_index]["hedge_fees_usd"] == 0
    assert "hedged" in ledger["summary"]
    with pytest.raises(ValueError, match="ReplayHedge"):
        study._ledger(account.closed, None)


def test_unhedgeable_books_are_left_unhedged_and_counted():
    study, hedge, _ = _hedge_study()
    short_put = _filled(study, [("put", -1, 95., 2)])
    bull_call = _filled(study, [("call", 1, 100., 1), ("call", -1, 105., 1)])
    long_put = _filled(study, [("put", 1, 100., 3)])
    for book in (short_put, bull_call, long_put):
        hedge.attach(book, "2019-01-16", None)
    assert short_put["hedge"]["status"] == bull_call["hedge"]["status"] == "hedge_unbounded_above"
    assert long_put["hedge"]["status"] == "ok"
    for book in (short_put, bull_call):
        assert book["hedge"]["units"] == 0 and book["reserve"] == book["hedge"].get("option_reserve", book["reserve"])
    assert hedge.counts["books_hedge_unbounded_above"] == 2 and hedge.record()["counts"]["books_hedge_unbounded_above"] == 2


def test_a_leg_without_iv_leaves_the_whole_book_unhedged_and_counts_the_code():
    study, hedge, _ = _hedge_study(bump=lambda day, right, k, row: row.update(iv=None)
                                   if (day == "2019-01-15" and right == "put" and k == 100.) else None)
    book = _filled(study, [("put", 1, 100., 3)])
    hedge.attach(book, "2019-01-16", None)
    assert book["hedge"] == {"status": "leg_no_iv", "units": 0}
    assert hedge.counts["leg_no_iv"] == 1 and hedge.counts["books_leg_no_iv"] == 1


def test_a_short_hedge_on_a_hard_to_borrow_name_is_left_unhedged_and_a_long_hedge_is_kept():
    params, inputs, spots = _hedge_world()
    for row in inputs["X"]["bars"]:                       # six violent closes before the decision session
        if "2019-01-04" <= row["date"] <= "2019-01-14":
            row["close"] = 100. * (3. if row["date"] in ("2019-01-04", "2019-01-08", "2019-01-10", "2019-01-14") else 1.)
    study = ledger_studies.WeeklyOptionPortfolioStudy(params, inputs)
    hedge = ledger_studies.ReplayHedge(study, _REPLAY_BLOCK, _PARITY)
    call = _filled(study, [("call", 1, 100., 3)])           # a long call sells shares: a SHORT hedge
    put = _filled(study, [("put", 1, 100., 3)])             # a long put buys shares: a LONG hedge
    hedge.attach(call, "2019-01-16", None)
    hedge.attach(put, "2019-01-16", None)
    assert call["hedge"] == {"status": "hedge_hard_to_borrow", "units": 0}
    assert put["hedge"]["status"] == "ok" and put["hedge"]["units"] > 0
    assert hedge.counts["books_hedge_hard_to_borrow"] == 1


def test_a_hedged_document_is_confined_below_the_seal_and_needs_its_parity_params():
    params, _ = _block_doc({"hedge": dict(_REPLAY_BLOCK)})
    ledger_studies.WeeklyOptionPortfolioStudy.validate_configuration(params)
    del params["hedge_parity"]
    _refused_block(params, "hedge_parity_missing")
    params["hedge_parity"] = {**_PARITY, "min_pairs": 0}
    _refused_block(params, "hedge_parity_invalid")
    params, _ = _block_doc({"hedge": dict(_REPLAY_BLOCK)})
    params["bridge"]["protected_end_before"], params["bridge"]["end_before"] = "2022-01-01", "2021-07-02"
    _refused_block(params, "hedge_seal")
    params, _ = _block_doc({"calibration": dict(_CAL)})
    params["hedge_parity"] = dict(_PARITY)
    _refused_block(params, "hedge_parity_unused")


def test_a_hedge_cannot_combine_with_a_sizer_and_other_blocks_stay_unattached():
    params, _ = _block_doc({"hedge": dict(_REPLAY_BLOCK), "sizer": dict(SIZER)})
    params["methods"][0]["short_notional_fraction"] = .2
    _refused_block(params, "hedge_with_sizer")
    params, inputs = _block_doc({"hedge": dict(_REPLAY_BLOCK), "calibration": dict(_CAL)})
    with pytest.raises(ValueError, match="edge_maps_unbound"):      # ADR-0313 I5: a calibration block needs the walk's maps
        ledger_studies.WeeklyOptionPortfolioStudy(params, inputs)


def test_a_condition_stays_refused_in_this_stage():
    for change, code in (({"condition": {"vol_regime": "high"}}, "hedge_condition_unattached"),):
        params, inputs = _block_doc({"hedge": {**_REPLAY_BLOCK, **change}})
        study = ledger_studies.WeeklyOptionPortfolioStudy(params, inputs)
        with pytest.raises(ValueError, match=code):
            study._method(params["methods"][0], None)


def test_a_replay_hedge_method_runs_like_its_unhedged_twin_when_no_book_can_be_hedged():
    plain_params, inputs = _packet_clock_fixture()
    plain = ledger_studies.WeeklyOptionPortfolioStudy(plain_params, inputs)._method(plain_params["methods"][0], None)
    params, inputs = _block_doc({"hedge": dict(_REPLAY_BLOCK)}, method_id="nominal_hedged")
    method = params["methods"][0]
    study = ledger_studies.WeeklyOptionPortfolioStudy(params, inputs)
    result = study._method(method, None)
    assert [t["pnl"] for t in result["trades"]] == [t["pnl"] for t in plain["trades"]]
    assert result["pnl"] == plain["pnl"] and result["curve"] == plain["curve"]
    assert result["hedge"]["counts"]["books_leg_no_iv"] == len(result["trades"]) >= 1    # the fixture quotes carry no iv
    assert all(t["hedge"] == {"status": "leg_no_iv", "units": 0} for t in result["trades"])
    assert result["hedge"]["mark_rule"] == "parity" and result["hedge"]["parity"] == _PARITY
    assert study._method(method, None, recorded=result) == result


def test_the_packet_route_validates_a_replay_hedge_block_with_its_settlement_tail():
    """ADR-0313 I2 review: the packet route has no `clocks` at `resolve`; it passes the tail so the seal rule reads it."""
    params, _ = _block_doc({"hedge": dict(_REPLAY_BLOCK)})
    del params["clocks"], params["selected_pairs"]
    _refused_block(params, "integration_seal")
    ledger_studies.WeeklyOptionPortfolioStudy.validate_configuration(params, tail_end_before="2019-02-09")
    with pytest.raises(ValueError, match="integration_seal"):
        ledger_studies.WeeklyOptionPortfolioStudy.validate_configuration(params, tail_end_before="2021-07-02")


def _saved_book(study, filled):
    """The decision-time form of a filled hand-built book (what a saved selection holds): no fill fields."""
    saved = {k: v for k, v in filled.items() if k not in ("hedge", "fill_date")}
    saved["legs"] = [{k: v for k, v in leg.items() if k != "fill_price"} for leg in saved["legs"]]
    return saved


def test_a_short_forward_hedge_pays_no_borrow_whatever_the_configured_rate_says():
    from dskit.pipeline.libs.option_ledger import FundedOptionAccount
    from dskit.pipeline.option_exposure import StaticDeltaHedge

    study, hedge, spots = _hedge_study(spot_at_expiry=108., block={"hedge_borrow_rate": 0.25})
    book = _filled(study, [("call", 1, 100., 3)])                  # a long call: positive delta, so a SHORT forward hedge
    hedge.attach(book, "2019-01-16", None)
    record = book["hedge"]
    units, ref = record["units"], record["reference"]
    assert record["status"] == "ok" and units < 0
    assert "hedge_borrow_rate" not in record["rates"] and hedge.block["hedge_borrow_rate"] == 0.25
    account = FundedOptionAccount(10000., 1000., 14, "2019-01-02")
    account.open[book["id"]] = book
    settled = study._settlements(account, "2019-02-01")
    no_borrow = StaticDeltaHedge({**record["rates"], "hedge_borrow_rate": 0.}).carry_costs(units, ref, 108., record["days"])
    charged = StaticDeltaHedge({**record["rates"], "hedge_borrow_rate": 0.25}).carry_costs(units, ref, 108., record["days"])
    assert no_borrow["borrow"] == 0.0 and charged["borrow"] > 0.0, "the fixture would show a charged borrow"
    legs = [{"instrument": "forward", "units": units, "reference": ref, "residual": 0.0}]
    assert hedge.overlay.costs(legs, 108., record["days"])["borrow"] == 0.0
    options = book["credit"] + 100 * 3 * (108. - 100.)
    assert settled[book["id"]] == pytest.approx(options + units * (108. - ref) - no_borrow["trade_costs"])
    account.advance("2019-02-01", settled)
    ledger = study._ledger(account.closed, None, hedge)
    assert ledger["trades"][0]["hedge_fees_usd"] == pytest.approx(no_borrow["trade_costs"])
    assert hedge.record()["forward_borrow_rate"] == 0.0


def test_the_parity_forward_reads_prices_on_the_books_basis_factor():
    study, hedge, spots = _hedge_study()
    for row in study._quotes["X"].values() if isinstance(study._quotes["X"], dict) else ():
        row.update(bid=row["bid"] * 2, ask=row["ask"] * 2, basis_factor=2.0)
    study._bars["X"] = {day: close * 2 for day, close in study._bars["X"].items()}
    hedge = ledger_studies.ReplayHedge(study, _REPLAY_BLOCK, _PARITY)
    forward, source = hedge.forward("X", "2019-01-22", "2019-02-01", 2.0)
    assert source == "parity" and forward == pytest.approx(spots["2019-01-22"] * 1.001)


def test_a_day_without_any_forward_carries_the_last_level_flagged_and_counted_never_a_gap():
    study, hedge, spots = _hedge_study()
    book = _filled(study, [("put", 1, 100., 3)])
    hedge.attach(book, "2019-01-16", None)
    study._bars["X"].pop("2019-01-22", None)
    hedge = ledger_studies.ReplayHedge(study, _REPLAY_BLOCK, _PARITY)
    path = hedge.path(book)
    days = [d for d in study.calendar if "2019-01-16" <= d < "2019-02-01"]
    assert list(path) == days, "every session has a level: no KeyError for a mark, no refused exit"
    previous = days[days.index("2019-01-22") - 1]
    assert path["2019-01-22"] == (path[previous][0], True)
    assert hedge.record()["counts"]["hedge_forward_missing"] == 1 and ("b1", "2019-01-22") in hedge.missing


# --- ADR-0312 program mode 1: parity first (no hedge block, and hedge ratio 0, move nothing) ------------------------------

_PROGRAM_BLOCK = {**_HEDGE, "mode": "program", "htb_lookback": 6}


def _solved_with_programs(params, inputs, monkeypatch):
    """Run the first method; return ``(result, programs, program_identities)`` of every solve."""
    from dskit.pipeline.libs.pyomo import TransportActionScore, TransportPortfolioSolve

    seen, run = [], TransportPortfolioSolve.run
    monkeypatch.setattr(TransportPortfolioSolve, "run",
                        lambda self, ctx, inp: (seen.append(inp), run(self, ctx, inp))[1])
    result = ledger_studies.WeeklyOptionPortfolioStudy(params, inputs)._method(params["methods"][0], None)
    monkeypatch.undo()
    tolerance = params["solver"]["parity_tolerance"]
    return result, seen, [TransportActionScore(p, {"parity_tolerance": tolerance}).program_identity for p in seen]


def _drop_hedge_fields(value):
    """Return ``value`` without any ``hedge`` key at any depth."""
    if isinstance(value, dict):
        return {k: _drop_hedge_fields(v) for k, v in value.items() if k != "hedge"}
    if isinstance(value, list):
        return [_drop_hedge_fields(v) for v in value]
    return value


def test_program_mode_at_ratio_zero_is_the_unhedged_twin_except_for_its_hedge_fields(monkeypatch):
    from dskit.pipeline.base import value_hash

    plain = _solved_with_programs(*_block_doc(), monkeypatch)
    zero = _solved_with_programs(*_block_doc({"hedge": {**_PROGRAM_BLOCK, "ratio": 0.0}}), monkeypatch)
    assert len(plain[1]) >= 1
    assert value_hash(zero[1]) == value_hash(plain[1]), "the programs the solver is handed"
    assert zero[2] == plain[2], "program_identity"
    assert _timeless_digest(_drop_hedge_fields(zero[0])) == _timeless_digest(_drop_hedge_fields(plain[0]))
    assert zero[0]["hedge"]["mode"] == "program"
    assert all(t["hedge"]["units"] == 0 for t in zero[0]["trades"])


def test_program_mode_at_ratio_zero_excludes_nothing_and_adds_no_row(monkeypatch):
    zero = _solved_with_programs(*_block_doc({"hedge": {**_PROGRAM_BLOCK, "ratio": 0.0}}), monkeypatch)
    counts = zero[0]["hedge"]["counts"]
    assert not [k for k in counts if k.endswith("_excluded")], counts
    assert all(len(g["reserve_rows"]) == len(zero[1][0]["groups"][0]["reserve_rows"]) for g in zero[1][0]["groups"])


# --- ADR-0312 program mode 2: hedged coefficients, exclusions, small_delta, the hand-worked tiny program ----------------

_TABLE_BLOCK = {**_PROGRAM_BLOCK, "margin_fraction": 0.0, "hedge_cost_source": "measured", "hedge_fee_per_share": 0.0,
                "hedge_half_spread_bps": 0.0, "hedge_borrow_rate": 0.0}


def _table_group(overlay_units):
    """The amendment's table, one group, mult 100, fee 0: ``(group, meta, entries)``.

    Items: 0 long call 105 at 2.00 (U -35), 1 long put 95 at 2.00 (+30), 2 short put 90 at 0.80 (-18; with item 1 the
    bear put spread, net +12), 3 short put 95 at 2.00 (-30: the unhedgeable short put).
    """
    items = [{"right": "call", "strike": 105., "sign": 1, "price": 2.0, "capacity": 10},
             {"right": "put", "strike": 95., "sign": 1, "price": 2.0, "capacity": 10},
             {"right": "put", "strike": 90., "sign": -1, "price": 0.8, "capacity": 10},
             {"right": "put", "strike": 95., "sign": -1, "price": 2.0, "capacity": 10}]
    grid = [80., 90., 95., 100., 105., 110., 120.]
    credit = [-i["sign"] * i["price"] * 100 for i in items]
    group = {"id": "g", "grid": grid, "masses": [1 / 7] * 7, "radius": 0., "scale": 100., "credit": credit,
             "losses": [ledger_studies.WeeklyOptionPortfolioStudy._loss_row(items, s, 100) for s in grid],
             "capacity": [10] * 4, "exclusive": [], "reserve_rows": [],
             "linear_rows": [{"coefficients": [-1. if i["right"] == "call" else 0. for i in items], "upper": 0.}]}
    meta = {"id": "g", "symbol": "X", "expiry": "2019-02-01", "decision_date": "2019-01-15", "dte": 17,
            "factor": 1., "spot": 100., "items": items}
    entries = [{"units": u, "target": u, "status": "ok", "hard_to_borrow": False} for u in overlay_units]
    return group, meta, entries


def _rows_value(group, quantities):
    """The solver's reserve for ``quantities``: the largest reserve row and 0."""
    return max([0.] + [sum(c * q for c, q in zip(row, quantities)) for row in group["reserve_rows"]])


def test_the_hedged_rows_reproduce_the_amendments_table_reserves():
    study, hedge, _ = _hedge_study(block=_TABLE_BLOCK)
    group, meta, entries = _table_group([-35., 30., -18., -30.])
    out, meta2 = hedge.rewrite(group, meta, entries, 100.0, 100)
    assert _rows_value(out, [1, 0, 0, 0]) == pytest.approx(375.0)          # long call: 375 vs 200 unhedged
    assert _rows_value(out, [0, 1, 1, 0]) == pytest.approx(820.0)          # bear put spread: 820 vs 120
    from dskit.pipeline.option_structures import structure_max_loss
    assert structure_max_loss([("call", 1)], [105.], -200., 100, hedge=(-35., 100.)) == pytest.approx(375.0)
    assert structure_max_loss([("put", 1), ("put", -1)], [95., 90.], -120., 100, hedge=(12., 100.)) == pytest.approx(820.0)
    assert meta2["program_hedge"]["units"] == [-35., 30., -18., -30.] and meta2["program_hedge"]["reference"] == 100.0


def test_the_slope_row_excludes_the_unhedgeable_classes_and_is_todays_row_at_zero_units():
    study, hedge, _ = _hedge_study(block=_TABLE_BLOCK)
    group, meta, entries = _table_group([-35., 30., -18., -30.])
    out, _ = hedge.rewrite(group, meta, entries, 100.0, 100)
    (row,) = out["linear_rows"]
    assert row["upper"] == 0.0
    assert row["coefficients"] == pytest.approx([-1. + .35, -.30, .18, .30])     # -s[call] - u / mult
    slope = lambda q: sum(c * x for c, x in zip(row["coefficients"], q))         # noqa: E731
    assert slope([0, 0, 0, 1]) > 0, "a lone short put (U -30) is unbounded above: excluded"
    assert slope([1, 0, 0, 0]) < 0 and slope([0, 1, 1, 0]) < 0, "long call and bear put spread are bounded"
    zero, _ = hedge.rewrite(*_table_group([0., 0., 0., 0.])[:2], _table_group([0., 0., 0., 0.])[2], 100.0, 100)
    assert zero["linear_rows"] == group["linear_rows"] and zero["losses"] == group["losses"]
    assert zero["reserve_rows"] == group["reserve_rows"] and zero["credit"] == group["credit"]


def test_the_grid_losses_are_the_hedged_losses_through_the_one_loss_row():
    study, hedge, _ = _hedge_study(block=_TABLE_BLOCK)
    group, meta, entries = _table_group([-35., 30., -18., -30.])
    out, _ = hedge.rewrite(group, meta, entries, 100.0, 100)
    row = ledger_studies.WeeklyOptionPortfolioStudy._loss_row
    for s, hedged in zip(group["grid"], out["losses"]):
        assert hedged == row(meta["items"], s, 100, ([-35., 30., -18., -30.], 100.0))
    assert out["losses"][0][0] == pytest.approx(0.0 - (-35.) * (80. - 100.))      # long call at S 80: loss 0 + 35 x (-20)
    plain = ledger_studies.WeeklyOptionPortfolioStudy._loss_row(meta["items"], 80., 100)
    assert plain == row(meta["items"], 80., 100, ([0.0] * 4, 100.0)), "zero units: the unhedged row, bit for bit"


def test_the_margin_floor_is_two_netted_rows_and_the_cost_is_the_shared_rule_per_item():
    block = {**_TABLE_BLOCK, "margin_fraction": 0.15, "hedge_fee_per_share": 0.01, "hedge_half_spread_bps": 10.0}
    study, hedge, _ = _hedge_study(block=block)
    group, meta, entries = _table_group([-35., 30., -18., -30.])
    out, meta2 = hedge.rewrite(group, meta, entries, 100.0, 100)
    # long call alone: |U| 35 -> floor 0.15 x 100 x 35 = 525, above the table's 375
    hedged_alone = _rows_value(out, [1, 0, 0, 0])
    assert hedged_alone >= 525.0
    # bear put spread nets +30 and -18 to +12 in the floor (180), not the gross 48 (720)
    spread_rows = [sum(c * q for c, q in zip(row, [0, 1, 1, 0])) for row in out["reserve_rows"][-2:]]
    assert sorted(spread_rows) == pytest.approx([-180.0, 180.0])
    # per-item cost: |u| x (2 fee + 2 F bps) at F_0 for both legs
    assert meta2["program_hedge"]["costs"] == pytest.approx([abs(u) * (0.02 + 200.0 * 10 / 1e4) for u in (-35, 30, -18, -30)])
    assert out["credit"] == pytest.approx([c - k for c, k in zip(group["credit"], meta2["program_hedge"]["costs"])])


def _unhedged_group(group, meta):
    """The group as `_prepare` builds it: the unhedged reserve rows (loss less credit at 0 and every strike)."""
    import numpy as np

    row = ledger_studies.WeeklyOptionPortfolioStudy._loss_row
    credit = np.asarray(group["credit"])
    rows = [(np.asarray(row(meta["items"], s, 100)) - credit).tolist() for s in [0., *sorted({i["strike"] for i in meta["items"]})]]
    return {**group, "reserve_rows": [*rows, (-credit).tolist()]}


def test_the_tiny_program_solved_with_highs_hedges_the_values_and_never_selects_the_unbounded_class():
    from dskit.pipeline.libs.pyomo import TransportPortfolioSolve

    study, hedge, _ = _hedge_study(block=_TABLE_BLOCK)
    group, meta, entries = _table_group([-35., 30., -18., -30.])
    group["masses"] = [0., 0., 0., .1, .3, .3, .3]          # a rising spot: the short put and the credit are the favourite
    solver_params = {"absolute_gap": 1e-7, "relative_gap": 0., "parity_tolerance": 1e-5,
                     "solver_options": {"mip_abs_gap": 1e-8, "mip_rel_gap": 0., "threads": 1}}

    def solve(g):
        program = {"groups": [g], "caps": [{"groups": ["g"], "upper": 30000.0}]}
        return TransportPortfolioSolve("portfolio", solver_params).run(None, program)["selection"]

    base = _unhedged_group(group, meta)
    plain = solve(base)
    assert plain["quantities"][0][3] > 0, "unhedged, the short put is chosen"
    hedged, _ = hedge.rewrite(base, meta, entries, 100.0, 100)
    chosen = solve(hedged)["quantities"][0]
    slope = sum(c * q for c, q in zip(hedged["linear_rows"][0]["coefficients"], chosen))
    assert slope <= 1e-9, "the selected book is bounded above"
    assert chosen[3] <= 2.17 * chosen[0] + 1e-9, "a short put only beside the long call that bounds it"
    rest = hedge.restrict(hedged, meta, entries, [3])[0]                        # the short put alone
    assert solve(rest)["quantities"][0] == [0], "the unbounded class alone is never selected"


def _program_world(block=None, initial=60000.):
    """The replay-hedge world as a clocked document with one method ``prog_arm`` (iv on every quote; nine history closes)."""
    params, inputs, spots = _hedge_world()
    history = ["2018-12-18", "2018-12-19", "2018-12-20", "2018-12-21", "2018-12-24", "2018-12-26", "2018-12-27",
               "2018-12-28", "2018-12-31"]
    inputs["X"]["bars"] = [{"symbol": "X", "date": d, "close": 100.} for d in history] + inputs["X"]["bars"]
    params["correlation_lookback"] = params["correlation_min_pairs"] = 2
    params["clocks"] = {"sessions": [r["date"] for r in inputs["X"]["bars"]], "history_start": history[0],
                        "entry_start": "2019-01-02", "entry_end_before": "2019-01-08", "tail_end_before": "2019-02-09"}
    params["selected_pairs"] = [{"id": "X30", "symbol": "X", "dte_min": 28, "dte_max": 32}]
    params["initial"] = initial
    method = {**params["methods"][0], "id": "prog_arm"}
    if block is not None:
        method["hedge"] = block
        params["hedge_parity"] = dict(_PARITY)
        params["locked_method_ids"] = list(FORMULATIONS)
    params["methods"] = [method]
    return params, inputs


def test_a_program_mode_run_chooses_hedged_books_whose_reserve_is_the_hedged_max_loss():
    from dskit.pipeline.option_structures import structure_max_loss

    params, inputs = _program_world(dict(_PROGRAM_BLOCK))
    study = ledger_studies.WeeklyOptionPortfolioStudy(params, inputs)
    result = study._method(params["methods"][0], None)
    book = result["decisions"][0]["books"][0]
    program = book["program_hedge"]
    legs = [(leg["right"], leg["sign"] * leg["quantity"]) for leg in book["legs"]]
    strikes = [leg["strike"] for leg in book["legs"]]
    credit = book["credit"] - program["cost_usd"]
    loss = structure_max_loss(legs, strikes, credit, 100, hedge=(program["units_raw"], program["reference"]))
    assert book["max_loss"] == pytest.approx(loss) and math.isfinite(loss)
    assert book["reserve"] == pytest.approx(max(loss, -credit, 0.15 * program["reference"] * abs(program["units_raw"])))
    trade = result["trades"][0]
    assert trade["hedge"]["status"] == "ok" and trade["hedge"]["units"] == math.trunc(program["units_raw"])
    assert result["hedge"]["mode"] == "program" and "program_cost_excess_usd" in result["hedge"]
    assert study._method(params["methods"][0], None, recorded=result) == result, "P6: re-derived from the block alone"


def test_a_program_mode_selection_differs_from_its_unhedged_twin_because_it_is_chosen_by_hedged_value():
    plain = ledger_studies.WeeklyOptionPortfolioStudy(*_program_world()).run()["methods"][0]
    hedged = ledger_studies.WeeklyOptionPortfolioStudy(*_program_world(dict(_PROGRAM_BLOCK))).run()["methods"][0]
    assert plain["decisions"][0]["books"][0]["legs"] != hedged["decisions"][0]["books"][0]["legs"]


def test_items_without_iv_are_excluded_from_the_hedged_candidate_set_and_an_emptied_group_is_refused():
    params, inputs = _program_world(dict(_PROGRAM_BLOCK))
    for row in inputs["X"]["chain"]:
        if row["date"] == "2019-01-02" and row["type"] == "call":
            row["iv"] = None
    study = ledger_studies.WeeklyOptionPortfolioStudy(params, inputs)
    result = study._method(params["methods"][0], None)
    excluded = result["decisions"][0]["hedge"]["excluded"]
    assert excluded["leg_no_iv_excluded"] > 0 and result["hedge"]["counts"]["leg_no_iv_excluded"] == excluded["leg_no_iv_excluded"]
    assert all(leg["right"] == "put" for leg in result["decisions"][0]["books"][0]["legs"]), "no call is left unhedged beside hedged puts"
    params, inputs = _program_world(dict(_PROGRAM_BLOCK))
    for row in inputs["X"]["chain"]:
        row["iv"] = None
    result = ledger_studies.WeeklyOptionPortfolioStudy(params, inputs)._method(params["methods"][0], None)
    assert result["decisions"][0]["status"] == "no_eligible_candidates" and result["trades"] == []
    assert {"symbol": "X", "expiry": "2019-02-01", "reason": "insufficient_eligible_legs"} in result["decisions"][0]["refusals"]


def test_short_hedge_items_on_a_hard_to_borrow_name_are_excluded_and_counted_per_name_and_group():
    study, hedge, _ = _hedge_study(block=_PROGRAM_BLOCK)
    group, meta, _ = _table_group([0., 0., 0., 0.])
    meta["decision_date"] = "2019-01-15"
    study._bars["X"].update({day: 100. * (3. if day in ("2019-01-04", "2019-01-08", "2019-01-10", "2019-01-14") else 1.)
                             for day in ("2019-01-04", "2019-01-08", "2019-01-09", "2019-01-10", "2019-01-11", "2019-01-14")})
    groups, metas, refusals, week = hedge.candidates([group], [meta], 100)
    kept = [(i["right"], i["strike"], i["sign"]) for i in metas[0]["items"]]
    assert kept == [("put", 95., 1)], "only the long put (a LONG hedge) survives; the other three would short the name"
    assert week["hard_to_borrow_excluded"] == 3 and week["htb_excluded_groups"] == 1 and week["htb_excluded_names"] == 1
    assert refusals == [] and len(groups[0]["capacity"]) == 1 and len(groups[0]["credit"]) == 1
    assert all(len(row) == 1 for row in groups[0]["losses"] + groups[0]["reserve_rows"])


def test_small_delta_is_not_an_exclusion_and_leaves_the_group_as_the_unhedged_twin_has_it():
    study, hedge, _ = _hedge_study(block={**_PROGRAM_BLOCK, "small_delta": 0.999})
    group, meta, _ = _table_group([0., 0., 0., 0.])
    groups, metas, refusals, week = hedge.candidates([group], [meta], 100)
    assert groups == [group] and metas == [meta] and refusals == []
    assert hedge.counts["hedge_small_delta"] == 4 and not [k for k in week if k.endswith("_excluded")]


def test_the_inputs_of_the_hedge_rewrite_are_never_mutated_the_prepared_groups_are_cached_across_methods():
    import copy

    study, hedge, _ = _hedge_study(block=_PROGRAM_BLOCK)
    group, meta, _ = _table_group([0., 0., 0., 0.])
    before = copy.deepcopy((group, meta))
    hedge.candidates([group], [meta], 100)
    assert (group, meta) == before


def test_the_shock_rows_see_the_hedged_loss_through_move_terms_and_the_gate_sees_hedged_books():
    study, hedge, _ = _hedge_study(block=_TABLE_BLOCK)
    group, meta, entries = _table_group([-35., 30., -18., -30.])
    out, hmeta = hedge.rewrite(group, meta, entries, 100.0, 100)
    terms = ledger_studies.WeeklyOptionPortfolioStudy._move_terms
    plain = terms([-.1], {"X": 1.0}, [meta], [group], 100)[-.1]["g"]
    hedged = terms([-.1], {"X": 1.0}, [hmeta], [out], 100)[-.1]["g"]
    # long call at S 90: unhedged loss 0 less credit(-200) = 200; hedged adds -u (S - F) = 35 x (90 - 100) = -350
    assert plain[0] == pytest.approx(200.0) and hedged[0] == pytest.approx(200.0 - 350.0)
    assert hmeta["program_hedge"]["reference"] == 100.0 and "program_hedge" not in meta


def test_a_program_hedge_with_kelly_or_a_sizer_is_refused_as_hedge_with_sizer():
    params, _ = _block_doc({"hedge": dict(_PROGRAM_BLOCK)})
    params["methods"][0]["kelly"] = dict(TOP_FORMULATIONS["kelly025_top100_lp"]["kelly"])
    with pytest.raises(ValueError, match="hedge_with_sizer"):
        ledger_studies.WeeklyOptionPortfolioStudy._hedge_configuration(params)
    params["methods"][0].pop("kelly")
    params["methods"][0]["sizer"] = dict(SIZER)
    params["methods"][0]["short_notional_fraction"] = .2
    _refused_block(params, "hedge_with_sizer")


def test_a_held_hedged_book_enters_the_account_shock_with_its_forward_leg_only_in_program_mode():
    study, hedge, _ = _hedge_study(block=_PROGRAM_BLOCK)
    book = _filled(study, [("put", 1, 100., 3)])
    hedge.attach(book, "2019-01-16", None)
    shock = {"moves": [-0.1], "beta_lookback": 2, "loss_fraction": 0.1, "scope": "filled_account"}
    day = "2019-01-17"
    _, plain = study._account_shock([book], day, shock)
    _, hedged = study._account_shock([book], day, shock, True)
    spot = study._bars["X"][day]
    level = max(0., spot * (1. + study._betas(day, 2)["betas"]["X"] * -0.1))
    leg = ledger_studies.ReplayHedge.net_settle(book["hedge"], level)
    assert book["hedge"]["units"] > 0
    assert hedged["-0.1"] == pytest.approx(plain["-0.1"] - leg) and leg != 0.0


# --- ADR-0312 program mode 3: fills, settlement and costs of a program-mode selection ------------------------------------

def test_a_partial_fill_scales_the_hedge_with_the_filled_quantity():
    full, hedge_full, _ = _hedge_study(block=_PROGRAM_BLOCK)
    thin, hedge_thin, _ = _hedge_study(block=_PROGRAM_BLOCK, bump=lambda day, right, k, row: row.update(ask_size=3)
                                       if (day == "2019-01-16" and right == "put" and k == 100.) else None)
    thin.params["fill_sizing"] = ledger_studies.WeeklyOptionPortfolioStudy._AVAILABLE_SIZE
    big, small = _filled(full, [("put", 1, 100., 8)]), _filled(thin, [("put", 1, 100., 8)])
    assert [leg["quantity"] for leg in big["legs"]] == [8] and [leg["quantity"] for leg in small["legs"]] == [3]
    hedge_full.attach(big, "2019-01-16", None)
    hedge_thin.attach(small, "2019-01-16", None)
    assert small["hedge"]["raw"] == pytest.approx(big["hedge"]["raw"] * 3 / 8)
    assert small["hedge"]["units"] == math.trunc(small["hedge"]["raw"]) and small["hedge"]["units"] < big["hedge"]["units"]
    assert small["reserve"] < big["reserve"]


def test_a_program_mode_settlement_carries_the_forward_leg_and_the_trade_pnl_is_option_plus_hedge():
    from dskit.pipeline.option_structures import structure_payoff

    params, inputs = _program_world(dict(_PROGRAM_BLOCK))
    study = ledger_studies.WeeklyOptionPortfolioStudy(params, inputs)
    result = study._method(params["methods"][0], None)
    trade = result["trades"][0]
    level = study._bars["X"][trade["expiry"]] / trade["factor"]
    option = trade["credit"] + 100 * structure_payoff([(leg["right"], leg["sign"] * leg["quantity"]) for leg in trade["legs"]],
                                                      level, [leg["strike"] for leg in trade["legs"]])
    leg = ledger_studies.ReplayHedge.net_settle(trade["hedge"], level)
    assert trade["hedge"]["units"] != 0 and leg != 0.0
    assert trade["pnl"] == pytest.approx(option + leg) and result["pnl"] == pytest.approx(trade["pnl"])


def test_the_program_cost_excess_is_signed_and_splits_into_a_units_effect_and_a_price_effect_by_hand():
    params, inputs = _program_world(dict(_PROGRAM_BLOCK))
    study = ledger_studies.WeeklyOptionPortfolioStudy(params, inputs)
    result = study._method(params["methods"][0], None)
    record = result["trades"][0]["hedge"]
    level = study._bars["X"]["2019-02-01"]
    fee, bps = 0.005, 5.0
    program = record["program_gross_units"] * (2 * fee + 2 * record["decision_forward"] * bps / 1e4)
    replay = abs(record["units"]) * (2 * fee + (record["reference"] + level) * bps / 1e4)
    assert record["program_cost_usd"] == pytest.approx(program)
    out = result["hedge"]
    assert out["program_cost_excess_usd"] == pytest.approx(program - replay) and program > replay > 0
    assert out["program_cost_units_effect_usd"] + out["program_cost_price_effect_usd"] == pytest.approx(program - replay)
    assert out["program_cost_units_effect_usd"] == pytest.approx(
        (record["program_gross_units"] - abs(record["units"])) * (2 * fee + 2 * record["decision_forward"] * bps / 1e4))


def test_a_replay_mode_result_carries_no_program_cost_field():
    params, inputs = _program_world(dict(_REPLAY_BLOCK))
    result = ledger_studies.WeeklyOptionPortfolioStudy(params, inputs)._method(params["methods"][0], None)
    assert not [k for k in result["hedge"] if "program" in k]
    assert all("program_cost_usd" not in t["hedge"] for t in result["trades"])


def test_a_partially_filled_program_book_recomputes_its_hedge_units_from_the_filled_legs_and_truncates_once():
    """Review of d864c39 minor 1: half the quantities filled -> half the raw units, truncated ONCE (not the full book's)."""
    import copy

    params, inputs = _program_world(dict(_PROGRAM_BLOCK))
    params["fill_sizing"] = "available_size"
    selected = ledger_studies.WeeklyOptionPortfolioStudy(params, inputs)._method(params["methods"][0], None)
    book = copy.deepcopy(selected["decisions"][0]["books"][0])
    for leg in book["legs"]:
        leg["quantity"] *= 4                    # gcd 4: every leg fills in multiples of 1/4 of the book
    book["contracts"] = sum(leg["quantity"] for leg in book["legs"])

    def attached(depth):
        for row in inputs["X"]["chain"]:
            if row["date"] == "2019-01-03":
                row["ask_size"] = row["bid_size"] = depth
        study = ledger_studies.WeeklyOptionPortfolioStudy(params, inputs)
        filled, why = study._fill(copy.deepcopy(book), "2019-01-03")
        ledger_studies.ReplayHedge(study, params["methods"][0]["hedge"], params["hedge_parity"]).attach(filled, "2019-01-03")
        return filled

    full, half = attached(999), attached(20)
    assert [leg["quantity"] * 2 for leg in half["legs"]] == [leg["quantity"] for leg in full["legs"]]
    program_raw = selected["decisions"][0]["books"][0]["program_hedge"]["units_raw"]       # the solver's units for 1x the book
    assert half["hedge"]["raw"] == pytest.approx(2 * program_raw), "recomputed from the FILLED legs (2x), not the program's"
    assert half["hedge"]["status"] == "ok" and half["hedge"]["raw"] == pytest.approx(full["hedge"]["raw"] / 2)
    assert half["hedge"]["units"] == math.trunc(half["hedge"]["raw"]) and half["hedge"]["units"] != full["hedge"]["units"]


def test_a_binding_margin_floor_is_the_physical_reserve_of_a_program_book_and_parity_holds():
    """Review of d864c39 minor 2: with margin_fraction 3.0 the floor, not the option loss, is the reserve."""
    params, inputs = _program_world({**_PROGRAM_BLOCK, "margin_fraction": 3.0})
    result = ledger_studies.WeeklyOptionPortfolioStudy(params, inputs)._method(params["methods"][0], None)
    book = result["decisions"][0]["books"][0]
    floor = 3.0 * book["program_hedge"]["reference"] * abs(book["program_hedge"]["units_raw"])
    assert book["reserve"] == pytest.approx(floor) and floor > book["max_loss"]


# --- ADR-0313 I6 (child): the exposure-limit rows in the study -------------------------------------------------------
_GREEKS = {"leg": dict(_GREEKS_LEG), "parity": dict(_PARITY)}
_WIDE = {"beta_delta_fraction": 50.0, "vega_fraction_per_pt": 50.0, "dte_edges": [20, 40], "beta_lookback": 2,
         "scope": "account"}


def _limit_world(limits=None, *, block=None, two_weeks=False, greeks=None):
    """The program world with an ``exposure_limits`` block on its one method (``limits`` overrides the wide defaults)."""
    import copy

    params, inputs = _program_world(block)
    if two_weeks:
        # The second decision (2019-01-07) trades the same 2019-02-01 expiry, 25 days out, so week 1's book is held.
        params["clocks"]["entry_end_before"] = "2019-01-10"
        params["selected_pairs"] = [{"id": "X30", "symbol": "X", "dte_min": 20, "dte_max": 32}]
        for kind in ("forecasts", "contexts"):
            for row in inputs["X"][kind]:
                if row["quote_date"] == "2019-01-07":
                    row["expiry"] = "2019-02-01"
                    if kind == "contexts":
                        row["settlement_date"] = "2019-02-01"
    if limits is not None:
        params["methods"][0]["exposure_limits"] = {**_WIDE, **limits}
        params["exposure_greeks"] = copy.deepcopy(greeks or _GREEKS)
        params["locked_method_ids"] = list(FORMULATIONS)
    return params, inputs


def _hand_greeks(inputs, day, legs, expiry="2019-02-01", mult=100, vol=0.3):
    """``(delta_f_sh, vega_usd_per_pt)`` of ``legs`` (right, strike, signed quantity) from the core Black-76 functions."""
    from datetime import date as d_

    from dskit.pipeline.option_pricing import black76_delta, black76_vega

    spot = next(r["close"] for r in inputs["X"]["bars"] if r["date"] == day)
    forward, years = spot * 1.001, (d_.fromisoformat(expiry) - d_.fromisoformat(day)).days / 365
    delta = sum(q * mult * black76_delta(r, forward, k, vol, years, 0.0) for r, k, q in legs)
    vega = sum(q * mult * black76_vega(r, forward, k, vol, years, 0.0) * 0.01 for r, k, q in legs)
    return delta, vega, forward


def _book_legs(book):
    return [(leg["right"], leg["strike"], leg["sign"] * leg["quantity"]) for leg in book["legs"]]


def _run_limited(monkeypatch, params, inputs):
    programs = _capture_programs(monkeypatch)
    study = ledger_studies.WeeklyOptionPortfolioStudy(params, inputs)
    result = study._method(params["methods"][0], None)
    monkeypatch.undo()
    return study, result, programs


def test_exposure_limits_run_and_the_row_coefficients_carry_the_m2_sign_pin(monkeypatch):
    params, inputs = _limit_world({})
    study, result, programs = _run_limited(monkeypatch, params, inputs)
    rows = {r["id"]: r for r in programs[0]["joint_rows"]}
    assert set(rows) == {"exposure:delta:+", "exposure:delta:-", "exposure:vega:20_40:+", "exposure:vega:20_40:-"}
    meta = study._prepare("2019-01-02")[1][0]
    gid = meta["id"]
    for i, item in enumerate(meta["items"]):
        delta, vega, forward = _hand_greeks(inputs, "2019-01-02", [(item["right"], item["strike"], item["sign"])])
        assert rows["exposure:delta:+"]["terms"][gid][i] == pytest.approx(delta * forward)
        assert rows["exposure:delta:-"]["terms"][gid][i] == pytest.approx(-delta * forward)
        assert rows["exposure:vega:20_40:+"]["terms"][gid][i] == pytest.approx(vega)
        if item["sign"] < 0:
            assert vega < 0, "a sold option's vega is negative"
            assert (rows["exposure:delta:+"]["terms"][gid][i] < 0) == (item["right"] == "call"), "sold call short, sold put long delta"
    assert rows["exposure:delta:+"]["upper"] == pytest.approx(50.0 * 60000.0), "no held book: the cap alone"
    assert result["decisions"][0]["exposure"]["status"] == "within"


def test_a_limit_that_never_binds_leaves_the_selection_unchanged(monkeypatch):
    free = ledger_studies.WeeklyOptionPortfolioStudy(*_limit_world())._method(_limit_world()[0]["methods"][0], None)
    params, inputs = _limit_world({})
    limited = ledger_studies.WeeklyOptionPortfolioStudy(params, inputs)._method(params["methods"][0], None)
    assert [d.get("books") for d in limited["decisions"]] == [d.get("books") for d in free["decisions"]]
    assert limited["trades"] == free["trades"]


def test_a_binding_delta_cap_holds_the_book_to_the_cap(monkeypatch):
    params, inputs = _limit_world()
    free = ledger_studies.WeeklyOptionPortfolioStudy(params, inputs)._method(params["methods"][0], None)
    free_delta, _, forward = _hand_greeks(inputs, "2019-01-02", _book_legs(free["decisions"][0]["books"][0]))
    assert abs(free_delta * forward) > 18000.0, "the free book is beyond the cap the next run sets"
    params, inputs = _limit_world({"beta_delta_fraction": 0.3})
    _, result, programs = _run_limited(monkeypatch, params, inputs)
    row = next(r for r in programs[0]["joint_rows"] if r["id"] == "exposure:delta:+")
    assert row["upper"] == pytest.approx(0.3 * 60000.0)
    book = result["decisions"][0]["books"][0]
    delta, _, forward = _hand_greeks(inputs, "2019-01-02", _book_legs(book))
    assert 0 < abs(delta * forward) <= 18000.0 + 1e-6


def test_a_binding_vega_bucket_cap_holds_the_bucket_to_the_cap(monkeypatch):
    params, inputs = _limit_world()
    free = ledger_studies.WeeklyOptionPortfolioStudy(params, inputs)._method(params["methods"][0], None)
    _, free_vega, _ = _hand_greeks(inputs, "2019-01-02", _book_legs(free["decisions"][0]["books"][0]))
    assert abs(free_vega) > 60.0
    params, inputs = _limit_world({"vega_fraction_per_pt": 0.001})
    _, result, programs = _run_limited(monkeypatch, params, inputs)
    assert next(r for r in programs[0]["joint_rows"] if r["id"] == "exposure:vega:20_40:-")["upper"] == pytest.approx(60.0)
    book = result["decisions"][0]["books"][0]
    _, vega, _ = _hand_greeks(inputs, "2019-01-02", _book_legs(book))
    assert 0 < abs(vega) <= 60.0 + 1e-6


def test_held_books_move_the_bound_over_every_held_book_outside_the_edges_too(monkeypatch):
    params, inputs = _limit_world({"beta_delta_fraction": 1.2, "dte_edges": [27, 40]}, two_weeks=True)
    study, result, programs = _run_limited(monkeypatch, params, inputs)
    held = result["decisions"][0]["books"][0]
    # Week 2 (25 days out) lies OUTSIDE the edges [27, 40): its candidates add to the delta row, the held book counts there too.
    delta, _, forward = _hand_greeks(inputs, "2019-01-07", _book_legs(held))
    rows = {r["id"]: r for r in programs[1]["joint_rows"]}
    cap = 1.2 * result["decisions"][1]["funding"]
    assert rows["exposure:delta:+"]["upper"] == pytest.approx(cap - delta * forward)
    assert rows["exposure:delta:-"]["upper"] == pytest.approx(cap + delta * forward)
    assert not any(r.startswith("exposure:vega") for r in rows), "outside the edges, no vega row"
    assert result["decisions"][1]["exposure"]["held_delta_usd"] == pytest.approx(delta * forward)


def test_a_held_book_beyond_a_limit_makes_the_week_cash_exposure_held_breach():
    params, inputs = _limit_world({"beta_delta_fraction": 0.1}, two_weeks=True)
    result = ledger_studies.WeeklyOptionPortfolioStudy(params, inputs)._method(params["methods"][0], None)
    assert result["decisions"][0]["status"] == "selected"
    assert result["decisions"][1]["status"] == "exposure_held_breach" and not result["decisions"][1].get("books")


def test_a_held_leg_without_a_usable_quote_makes_the_week_cash_exposure_risk_input():
    params, inputs = _limit_world({"beta_delta_fraction": 50.0}, two_weeks=True)
    for row in inputs["X"]["chain"]:
        if row["date"] == "2019-01-07" and row["type"] == "call" and row["strike"] == 90.:
            row["iv"] = None                                  # a held leg (the week-1 book is long the 90 call)
    result = ledger_studies.WeeklyOptionPortfolioStudy(params, inputs)._method(params["methods"][0], None)
    assert result["decisions"][1]["status"] == "exposure_risk_input" and not result["decisions"][1].get("books")
    assert len(result["trades"]) == 1, "only week 1 traded"


def test_an_item_without_iv_is_excluded_from_the_limited_candidates_and_counted(monkeypatch):
    params, inputs = _limit_world({})
    for row in inputs["X"]["chain"]:
        if row["date"] == "2019-01-02" and row["type"] == "call":
            row["iv"] = None
    result = ledger_studies.WeeklyOptionPortfolioStudy(params, inputs)._method(params["methods"][0], None)
    excluded = result["decisions"][0]["exposure"]["excluded"]
    assert excluded["leg_no_iv_excluded"] > 0
    assert all(leg["right"] == "put" for leg in result["decisions"][0]["books"][0]["legs"])


def test_without_an_exposure_block_no_key_no_row_no_param(monkeypatch):
    params, inputs = _program_world()
    _, result, programs = _run_limited(monkeypatch, params, inputs)
    assert all("joint_rows" not in p for p in programs)
    assert not any("exposure" in d for d in result["decisions"]) and "exposure" not in result


def test_exposure_greeks_is_required_exactly_when_a_method_carries_limits():
    validate = ledger_studies.WeeklyOptionPortfolioStudy.validate_configuration
    params, _ = _limit_world({})
    validate(params)
    del params["exposure_greeks"]
    _refused_block(params, "exposure_greeks_missing")
    params, _ = _limit_world({})
    params["exposure_greeks"]["leg"]["min_years"] = 0
    _refused_block(params, "exposure_greeks_invalid")
    params, _ = _limit_world({})
    params["exposure_greeks"]["parity"]["min_pairs"] = 0
    _refused_block(params, "exposure_greeks_invalid")
    params, _ = _program_world()
    params["exposure_greeks"] = {**_GREEKS}
    _refused_block(params, "exposure_greeks_unused")


def test_limits_beside_kelly_are_refused_and_greeks_must_agree_with_the_hedge():
    params, _ = _kelly_fixture()
    params["methods"][0].update(id="kelly_limits", exposure_limits={**_WIDE, "dte_edges": [20, 40]})
    params["exposure_greeks"] = dict(_GREEKS)
    params["locked_method_ids"] = list(FORMULATIONS)
    clocked = _packet_clock_fixture()[0]
    params.update(clocks=clocked["clocks"], selected_pairs=clocked["selected_pairs"])
    _refused_block(params, "exposure_limits_with_kelly")
    params, _ = _limit_world({}, block=dict(_PROGRAM_BLOCK))
    ledger_studies.WeeklyOptionPortfolioStudy.validate_configuration(params)
    for path, value in ((("leg", "rate"), 0.01), (("leg", "iv_source"), "mid"), (("leg", "min_years"), 0.002),
                        (("leg", "vol_floor"), 0.02), (("parity", "min_pairs"), 2)):
        params, _ = _limit_world({}, block=dict(_PROGRAM_BLOCK))
        params["exposure_greeks"][path[0]][path[1]] = value
        if path == ("leg", "iv_source"):
            params["exposure_greeks"]["leg"]["iv_disagree_tol"] = 0.05
        _refused_block(params, "block_params_disagree")


def test_limits_beside_a_program_hedge_count_the_hedge_units_in_the_coefficients(monkeypatch):
    params, inputs = _limit_world({}, block=dict(_PROGRAM_BLOCK))
    study, result, programs = _run_limited(monkeypatch, params, inputs)
    meta = study._prepare("2019-01-02")[1][0]
    rows = {r["id"]: r for r in programs[0]["joint_rows"]}
    gid = programs[0]["groups"][0]["id"]
    units = result["decisions"][0]["exposure"]["units"][gid]
    assert len(units) == len(programs[0]["groups"][0]["capacity"]) and any(abs(u) > 0 for u in units)
    for i, u in enumerate(units):
        item = meta["items"][i]
        delta, _, forward = _hand_greeks(inputs, "2019-01-02", [(item["right"], item["strike"], item["sign"])])
        assert rows["exposure:delta:+"]["terms"][gid][i] == pytest.approx((delta + u) * forward)


def test_held_delta_counts_the_hedge_units_of_a_program_hedged_book(monkeypatch):
    params, inputs = _limit_world({"beta_delta_fraction": 50.0, "dte_edges": [20, 40]}, block=dict(_PROGRAM_BLOCK), two_weeks=True)
    _, result, _ = _run_limited(monkeypatch, params, inputs)
    held = result["decisions"][0]["books"][0]
    units = result["trades"][0]["hedge"]["units"]
    assert units != 0, "the week-1 book is hedged"
    delta, _, forward = _hand_greeks(inputs, "2019-01-07", _book_legs(held))
    assert result["decisions"][1]["exposure"]["held_delta_usd"] == pytest.approx((delta + units) * forward)


# --- ADR-0313 I6 fix round: the held-vega side of the bounds (review of 747f3c5, Major) -------------------------------

def test_held_vega_moves_the_bucket_bound_by_the_held_books_vega_hand_computed(monkeypatch):
    params, inputs = _limit_world({"vega_fraction_per_pt": 0.01, "dte_edges": [20, 40]}, two_weeks=True)
    _, result, programs = _run_limited(monkeypatch, params, inputs)
    held = result["decisions"][0]["books"][0]
    _, vega, _ = _hand_greeks(inputs, "2019-01-07", _book_legs(held))
    assert abs(vega) > 1.0, "the held book carries vega, so a flipped sign or a dropped term shows"
    cap = 0.01 * result["decisions"][1]["funding"]
    rows = {r["id"]: r for r in programs[1]["joint_rows"]}
    assert rows["exposure:vega:20_40:+"]["upper"] == pytest.approx(cap - vega, abs=1e-6)
    assert rows["exposure:vega:20_40:-"]["upper"] == pytest.approx(cap + vega, abs=1e-6)
    assert result["decisions"][1]["exposure"]["held_vega"] == pytest.approx([vega])


def test_a_held_book_outside_the_edges_adds_no_held_vega(monkeypatch):
    params, inputs = _limit_world({"vega_fraction_per_pt": 0.01, "dte_edges": [27, 40]}, two_weeks=True)
    _, result, _ = _run_limited(monkeypatch, params, inputs)
    assert result["decisions"][1]["exposure"]["held_vega"] == [0.0]


def test_a_held_book_exactly_on_a_bucket_edge_is_in_the_bucket_it_opens_and_out_of_the_one_it_closes(monkeypatch):
    """Half-open ``[low, high)``: a book at ``dte == low`` is inside, at ``dte == high`` outside; a 1-day shift flips it."""
    from datetime import date

    params, inputs = _limit_world({"vega_fraction_per_pt": 0.01, "dte_edges": [20, 40]}, two_weeks=True)
    _, result, _ = _run_limited(monkeypatch, params, inputs)
    held = result["decisions"][0]["books"][0]
    dte = (date.fromisoformat(held["expiry"]) - date.fromisoformat("2019-01-07")).days
    _, vega, _ = _hand_greeks(inputs, "2019-01-07", _book_legs(held))
    assert 20 < dte < 40 and abs(vega) > 1.0

    def held_vega(edges):
        params, inputs = _limit_world({"vega_fraction_per_pt": 0.01, "dte_edges": edges}, two_weeks=True)
        return _run_limited(monkeypatch, params, inputs)[1]["decisions"][1]["exposure"]["held_vega"]

    assert held_vega([dte, 40]) == [pytest.approx(vega)], "dte == low edge: inside"
    assert held_vega([dte + 1, 41]) == [0.0], "one day below the low edge: outside"
    assert held_vega([20, dte + 1]) == [pytest.approx(vega)], "one day inside the high edge: inside"
    assert held_vega([20, dte]) == [0.0], "dte == high edge: outside"


def test_a_held_vega_beyond_a_bucket_cap_alone_is_exposure_held_breach():
    # Week 1 trades under a 250 cap at 30 percent volatility; week 2 quotes 20 percent, where the same book's vega is
    # larger (hand: -294 against -211 for the free book), so the held vega alone breaches the bucket cap.
    params, inputs = _limit_world({"vega_fraction_per_pt": 250.0 / 60000.0, "dte_edges": [20, 40],
                                   "beta_delta_fraction": 50.0}, two_weeks=True)
    for row in inputs["X"]["chain"]:
        if row["date"] == "2019-01-07":
            row["iv"] = 0.2
    result = ledger_studies.WeeklyOptionPortfolioStudy(params, inputs)._method(params["methods"][0], None)
    assert result["decisions"][0]["status"] == "selected"
    _, vega, _ = _hand_greeks(inputs, "2019-01-07", _book_legs(result["decisions"][0]["books"][0]), vol=0.2)
    assert abs(vega) > 250.0, "only the vega bucket is beyond its cap; delta is far inside its own"
    assert result["decisions"][1]["status"] == "exposure_held_breach"


def test_a_held_book_whose_forward_or_beta_is_unreadable_is_exposure_risk_input(monkeypatch):
    import inspect

    params, inputs = _limit_world({}, two_weeks=True)
    real = ledger_studies.ParityForwards.forward

    def forward(self, symbol, day, expiry, factor, exclude=()):
        if day == "2019-01-07" and inspect.stack()[1].function == "_held":
            return None, "no_forward"
        return real(self, symbol, day, expiry, factor, exclude)

    monkeypatch.setattr(ledger_studies.ParityForwards, "forward", forward)
    result = ledger_studies.WeeklyOptionPortfolioStudy(params, inputs)._method(params["methods"][0], None)
    assert result["decisions"][1]["status"] == "exposure_risk_input"
    monkeypatch.undo()
    params, inputs = _limit_world({}, two_weeks=True)
    real_betas = ledger_studies.WeeklyOptionPortfolioStudy._betas
    monkeypatch.setattr(ledger_studies.WeeklyOptionPortfolioStudy, "_betas",
                        lambda self, day, lookback: {**real_betas(self, day, lookback),
                                                 "betas": {} if day == "2019-01-07" else real_betas(self, day, lookback)["betas"]})
    result = ledger_studies.WeeklyOptionPortfolioStudy(params, inputs)._method(params["methods"][0], None)
    assert result["decisions"][0]["status"] == "selected" and result["decisions"][1]["status"] == "exposure_risk_input"


def test_a_reversal_book_is_unbounded_unhedged_bounded_hedged_recorded_and_settled():
    """ADR-0313 I7: a short call + long put (ratio 1) has no finite unhedged loss; its forward leg bounds it."""
    import json
    from dskit.pipeline.libs.option_ledger import FundedOptionAccount

    study, hedge, spots = _hedge_study(spot_at_expiry=92.)
    book = _filled(study, [("call", -1, 100., 3), ("put", 1, 100., 3)])
    assert math.isinf(book["max_loss"])
    hedge.attach(book, "2019-01-16", None)
    record, units, ref = book["hedge"], book["hedge"]["units"], book["hedge"]["reference"]
    assert record["status"] == "ok" and record["unhedged_unbounded"] is True
    assert record["option_reserve"] is None and record["unhedged_max_loss"] is None
    assert units > 0 and math.isfinite(book["max_loss"]) and math.isfinite(book["reserve"])
    assert units == 300 and ref == pytest.approx(105.105)       # 3 contracts each, hedge ratio 1, reference F_fill
    assert book["max_loss"] == pytest.approx(23.918, abs=5e-4)    # hand numbers of the final review
    assert book["reserve"] == pytest.approx(0.15 * units * ref, abs=1e-6) == pytest.approx(4729.725, abs=1e-6), \
        "the margin floor on the forward leg, not the tiny hedged loss, sets the reserve"
    json.dumps(record, allow_nan=False)                        # recorded: the saved record holds no infinity
    account = FundedOptionAccount(10000., 1000., 14, "2019-01-02")
    account.open[book["id"]] = book
    settled = study._settlements(account, "2019-02-01")
    leg = ledger_studies.ReplayHedge.net_settle(record, 92.)
    options = book["credit"] + 100 * 3 * (-(max(92. - 100., 0.)) + max(100. - 92., 0.))
    assert settled[book["id"]] == pytest.approx(options + leg)
    account.advance("2019-02-01", settled)
    ledger = study._ledger(account.closed, None, hedge)        # recorded then read back: the shared ledger's `unhedged_unbounded` branch
    assert ledger["trades"][0]["max_loss_unbounded"] is True
    assert ledger["trades"][0]["pnl_usd"] == pytest.approx(settled[book["id"]])

# --- warm-started certification and incumbent local search (ADR-0302; owner option B, 2026-11-09) ----------------------

SEARCH = {"iterations": 6, "neighbourhood": 1, "node_limit": 50, "seed": 7}


def _solved_weeks(result):
    return [d for d in result["decisions"] if "kelly" in d and "selection" in d]


def _outcome(decision):
    return decision.get("kelly", {}).get("solve", {}).get("strategy_outcome")


def _traded(decision):
    selection = decision["selection"]
    return selection["candidate"] if "timeout" in selection else selection


@pytest.mark.parametrize("kelly,ok", [
    ({"search": SEARCH}, True),
    ({"warm_start": {"node_limit": 10}}, True),
    ({"search": SEARCH, "warm_start": {"node_limit": 10}, "accept_uncertified": True}, True),
    ({"search": {**SEARCH, "neighbourhood": 2}, "max_groups": 3}, True),
    ({"search": None}, False),
    ({"search": {**SEARCH, "seed": -1}}, False),
    ({"search": {**SEARCH, "seed": 2**31}}, False),
    ({"search": {**SEARCH, "iterations": 0}}, False),
    ({"search": {**SEARCH, "iterations": 1001}}, False),
    ({"search": {**SEARCH, "iterations": True}}, False),
    ({"search": {**SEARCH, "neighbourhood": 0}}, False),
    ({"search": {**SEARCH, "node_limit": 1.}}, False),
    ({"search": {k: v for k, v in SEARCH.items() if k != "seed"}}, False),
    ({"search": {**SEARCH, "time_limit": 10}}, False),
    ({"search": {**SEARCH, "neighbourhood": 3}, "max_groups": 3}, False),
    ({"warm_start": True}, False),
    ({"warm_start": None}, False),
    ({"warm_start": {}}, False),
    ({"warm_start": {"node_limit": 0}}, False),
    ({"warm_start": {"node_limit": 5, "seed": 1}}, False),
])
def test_the_search_and_warm_start_keys_validate_or_refuse(kelly, ok):
    params, inputs = _kelly_fixture(**kelly)
    if ok:
        ledger_studies.WeeklyOptionPortfolioStudy.validate_configuration(params)
    else:
        with pytest.raises(ValueError):
            ledger_studies.WeeklyOptionPortfolioStudy.validate_configuration(params)


def test_the_search_and_warm_start_act_only_inside_relax_fix():
    from dskit.pipeline.libs.pyomo import TransportPortfolioSolve

    params, inputs = _kelly_fixture(search=SEARCH)
    params["solver"]["utility_solve"] = "direct"
    with pytest.raises(ValueError, match="relax_fix"):
        ledger_studies.WeeklyOptionPortfolioStudy.validate_configuration(params)
    assert TransportPortfolioSolve.validate_params({**params["solver"], "utility_solve": "relax_fix",
                                                    "utility_search": SEARCH}) == []
    assert TransportPortfolioSolve.validate_params({**params["solver"], "utility_warm_start": {"node_limit": 0}})


def test_the_search_formulation_is_the_uncertified_arm_plus_its_two_keys():
    search = TOP_FORMULATIONS["kelly025_top100_search_lp"]
    uncert = TOP_FORMULATIONS["kelly025_top100_uncert_lp"]
    assert {k: v for k, v in search.items() if k != "kelly"} == {k: v for k, v in uncert.items() if k != "kelly"}
    assert {k: v for k, v in search["kelly"].items() if k not in ("search", "warm_start")} == uncert["kelly"]
    assert search["kelly"]["search"]["neighbourhood"] < search["kelly"]["max_groups"]
    params, inputs = _weekly_portfolio_fixture()
    params["methods"] = [{"id": "kelly025_top100_search_lp", **search}]
    _for_kelly(params)
    ledger_studies.WeeklyOptionPortfolioStudy.validate_configuration(params)


def _declined_plain(monkeypatch):
    """The declined fixture's weeks without the keys (every certification declined by the action limit)."""
    from dskit.pipeline.libs.pyomo import TransportPortfolioSolve

    monkeypatch.setattr(TransportPortfolioSolve, "UTILITY_CERTIFY_ACTIONS", 0)
    params, inputs = _declined_kelly_fixture(accept_uncertified=True)
    plain = ledger_studies.WeeklyOptionPortfolioStudy(params, inputs)._method(params["methods"][0], None)
    assert all("warm_start" not in d.get("kelly", {}) and "search" not in d.get("kelly", {}) for d in plain["decisions"])
    declined = [d for d in plain["decisions"] if _outcome(d) == "declined"]
    assert declined, "the fixture's certification is declined"
    return declined


def test_a_warm_start_certifies_a_week_the_action_limit_declines_from_its_complete_incumbent(monkeypatch):
    """Above the action limit a declared warm start hands the certification the complete incumbent under the node
    budget; the fixture's program is small enough that HiGHS proves the gap, so the declined week becomes certified."""
    declined = _declined_plain(monkeypatch)
    params, inputs = _declined_kelly_fixture(accept_uncertified=True, warm_start={"node_limit": 20})
    method = params["methods"][0]
    warm = ledger_studies.WeeklyOptionPortfolioStudy(params, inputs)._method(method, None)
    by_date = {d["date"]: d for d in warm["decisions"]}
    for week in declined:
        w = by_date[week["date"]]
        record = w["kelly"]["warm_start"]
        assert set(record) == {"supplied", "accepted", "over_cap", "termination", "value_before", "value_after", "kept"}
        assert record["supplied"] and record["over_cap"] and record["accepted"] and not record["kept"], record
        assert record["value_before"] == pytest.approx(_traded(week)["value"], abs=1e-6)
        assert record["value_after"] == pytest.approx(w["selection"]["value"], abs=1e-6)
        assert record["termination"] == "optimal" and _outcome(w) == "certified"
        assert "timeout" not in w["selection"] and w["kelly"]["uncertified"] is None and w["status"] in ("selected", "cash")
        assert w["selection"]["value"] >= _traded(week)["value"]-1e-6, "the certification starts from the incumbent"
        assert "search" not in w["kelly"]
    assert ledger_studies.WeeklyOptionPortfolioStudy(params, inputs)._method(method, None, recorded=warm) == warm


def test_an_unproved_warm_started_certification_leaves_the_declined_week_as_it_was(monkeypatch):
    from dskit.pipeline.libs.pyomo import TransportPortfolioSolve

    declined = _declined_plain(monkeypatch)
    monkeypatch.setattr(TransportPortfolioSolve, "_warm_certified", lambda self, *args: False)
    params, inputs = _declined_kelly_fixture(accept_uncertified=True, warm_start={"node_limit": 20})
    method = params["methods"][0]
    warm = ledger_studies.WeeklyOptionPortfolioStudy(params, inputs)._method(method, None)
    by_date = {d["date"]: d for d in warm["decisions"]}
    for week in declined:
        w = by_date[week["date"]]
        assert w["kelly"]["warm_start"]["supplied"] and w["kelly"]["warm_start"]["over_cap"]
        assert _outcome(w) == "declined" and w["kelly"]["solve"] == week["kelly"]["solve"]
        assert w["selection"] == week["selection"] and w["books"] == week["books"], "what it found is not traded"
        assert w["kelly"]["uncertified"] == week["kelly"]["uncertified"]
    assert ledger_studies.WeeklyOptionPortfolioStudy(params, inputs)._method(method, None, recorded=warm) == warm
    forged = json.loads(json.dumps(warm))
    week = next(d for d in forged["decisions"] if _outcome(d) == "declined" and d["kelly"]["warm_start"])
    week["kelly"]["warm_start"]["over_cap"] = False
    with pytest.raises(ValueError):
        ledger_studies.WeeklyOptionPortfolioStudy(params, inputs)._method(method, None, recorded=forged)


def _search_timeless_digest(result):
    """:func:`_timeless_digest` without the search's own wall seconds (the only clock reading its record carries)."""
    import copy

    result = copy.deepcopy(result)
    for decision in result["decisions"]:
        if (decision.get("kelly") or {}).get("search"):
            decision["kelly"]["search"].pop("seconds")
    return _timeless_digest(result)


def _poor_incumbent_search(monkeypatch, **kelly):
    """The declined fixture whose support search sees only cash (every action outside the support): a poor incumbent."""
    from dskit.pipeline.libs.pyomo import TransportPortfolioSolve

    monkeypatch.setattr(TransportPortfolioSolve, "UTILITY_CERTIFY_ACTIONS", 0)
    monkeypatch.setattr(TransportPortfolioSolve, "UTILITY_SUPPORT_EPS", 1e9)
    return _declined_kelly_fixture(accept_uncertified=True, **kelly)


def test_the_search_improves_a_poor_incumbent_by_exact_utility_and_records_how(monkeypatch):
    params, inputs = _poor_incumbent_search(monkeypatch)
    plain = ledger_studies.WeeklyOptionPortfolioStudy(params, inputs)._method(params["methods"][0], None)
    params, inputs = _poor_incumbent_search(monkeypatch, search=SEARCH)
    searched = ledger_studies.WeeklyOptionPortfolioStudy(params, inputs)._method(params["methods"][0], None)
    before = {d["date"]: d for d in _solved_weeks(plain)}
    weeks = [d for d in _solved_weeks(searched) if d["kelly"]["search"] is not None]
    assert weeks and any(d["kelly"]["search"]["improved"] for d in weeks), "the search lifts the cash incumbent"
    for week in weeks:
        s = week["kelly"]["search"]
        assert set(s) == {"seed", "iterations", "improved", "value_before", "value_after", "accepted_neighbourhoods",
                          "budget_exhausted", "halted_by", "failed", "seconds", "allowance"}
        assert s["seed"] == SEARCH["seed"] and 0 <= s["iterations"] <= SEARCH["iterations"]
        assert s["halted_by"] is None and s["failed"] == 0 and s["seconds"] >= 0
        assert s["value_before"] == pytest.approx(_traded(before[week["date"]])["value"], abs=1e-6)
        if s["improved"]:
            assert s["value_after"] > s["value_before"]
            assert [len(a["groups"]) for a in s["accepted_neighbourhoods"]] == [1]*len(s["accepted_neighbourhoods"])
            assert s["accepted_neighbourhoods"][-1]["value"] == s["value_after"]
        else:
            assert s["value_after"] == s["value_before"] and not s["accepted_neighbourhoods"]
        if _outcome(week) in ("declined", "relaxation_certified") and s["value_after"] > 0:
            assert _traded(week)["value"] == pytest.approx(s["value_after"], abs=1e-6), "the best candidate is traded"
        assert s["budget_exhausted"] == (s["iterations"] == SEARCH["iterations"]) or not s["budget_exhausted"]
    assert sum(_traded(d)["value"] for d in weeks) > sum(_traded(before[d["date"]])["value"] for d in weeks)


def test_the_search_is_deterministic_and_replays_through_the_recorded_and_derived_routes(monkeypatch):
    from index_options.mio_worker import MIOMethodWorker

    params, inputs = _poor_incumbent_search(monkeypatch, search=SEARCH, warm_start={"node_limit": 20})
    method = params["methods"][0]
    first = ledger_studies.WeeklyOptionPortfolioStudy(params, inputs)._method(method, None)
    second = ledger_studies.WeeklyOptionPortfolioStudy(params, inputs)._method(method, None)
    assert _search_timeless_digest(first) == _search_timeless_digest(second), \
        "the same seed and node limits give the same weeks"
    assert any(d["kelly"]["search"] and d["kelly"]["search"]["improved"] for d in _solved_weeks(first))
    study = ledger_studies.WeeklyOptionPortfolioStudy(params, inputs)
    assert study._method(method, None, recorded=first) == first
    actions = MIOMethodWorker.actions(first, study.weeks)
    assert all(set(entry) == {"actions", "certificates", "kelly_strategy"} for entry in actions["decisions"].values())
    derived = ledger_studies.WeeklyOptionPortfolioStudy(params, inputs)._method(method, None, derived=actions)
    assert _equal_but_last_bits(derived, first)
    import copy
    forged = copy.deepcopy(first)
    week = next(d for d in forged["decisions"] if d.get("kelly", {}).get("search"))
    week["kelly"]["search"]["seed"] += 1
    with pytest.raises(ValueError):
        ledger_studies.WeeklyOptionPortfolioStudy(params, inputs)._method(method, None, recorded=forged)
    forged = copy.deepcopy(first)
    week = next(d for d in forged["decisions"] if d.get("kelly", {}).get("search", {}) and d["kelly"]["search"]["improved"])
    week["kelly"]["search"]["value_after"] += 1.
    with pytest.raises(ValueError):
        ledger_studies.WeeklyOptionPortfolioStudy(params, inputs)._method(method, None, recorded=forged)
    bare = copy.deepcopy(actions)
    for entry in bare["decisions"].values():
        del entry["kelly_strategy"]
    with pytest.raises(ValueError):
        ledger_studies.WeeklyOptionPortfolioStudy(params, inputs)._method(method, None, derived=bare)
    plain = ledger_studies.WeeklyOptionPortfolioStudy(*_kelly_fixture())
    plain_actions = MIOMethodWorker.actions(
        plain._method(plain.params["methods"][0], None), plain.weeks)
    assert all(set(entry) == {"actions", "certificates"} for entry in plain_actions["decisions"].values()), \
        "a method without the keys derives exactly as before"


def _forged_refused(params, inputs, method, result, edit):
    import copy

    forged = copy.deepcopy(result)
    edit(forged)
    with pytest.raises(ValueError):
        ledger_studies.WeeklyOptionPortfolioStudy(params, inputs)._method(method, None, recorded=forged)


def test_an_unproved_warm_start_that_a_node_budget_stopped_keeps_its_better_incumbent_uncertified(monkeypatch):
    """Fix round 1: above the action limit, a certification that did not prove the gap but stopped on its own (not on
    the clock) and beat its start by exact utility is traded uncertified; the week stays declined, relaxation-bounded."""
    from dskit.pipeline.libs.pyomo import TransportPortfolioSolve

    params, inputs = _poor_incumbent_search(monkeypatch)
    plain = {d["date"]: d for d in _solved_weeks(
        ledger_studies.WeeklyOptionPortfolioStudy(params, inputs)._method(params["methods"][0], None))}
    monkeypatch.setattr(TransportPortfolioSolve, "_warm_certified", lambda self, *args: False)
    params, inputs = _poor_incumbent_search(monkeypatch, warm_start={"node_limit": 20})
    method = params["methods"][0]
    warm = ledger_studies.WeeklyOptionPortfolioStudy(params, inputs)._method(method, None)
    kept = [d for d in _solved_weeks(warm) if d["kelly"]["warm_start"] and d["kelly"]["warm_start"]["kept"]]
    assert kept, "the cash start leaves the certification room to improve"
    for week in kept:
        w = week["kelly"]["warm_start"]
        assert _outcome(week) == "declined" and w["over_cap"] and w["termination"] in ("optimal", "maxIterations")
        assert w["value_before"] == pytest.approx(_traded(plain[week["date"]])["value"], abs=1e-6)
        assert w["value_after"] > w["value_before"] and _traded(week)["value"] == pytest.approx(w["value_after"], abs=1e-6)
        assert week["selection"]["timeout"] == "gap" and week["kelly"]["uncertified"]["outcome"] == "declined"
        assert week["kelly"]["uncertified"]["bound"] == pytest.approx(
            plain[week["date"]]["kelly"]["uncertified"]["bound"], rel=1e-9), "the bound stays the relaxation's"
    assert ledger_studies.WeeklyOptionPortfolioStudy(params, inputs)._method(method, None, recorded=warm) == warm

    def unkeep(result):
        next(d for d in result["decisions"] if d.get("kelly", {}).get("warm_start", {}) and
             d["kelly"]["warm_start"]["kept"])["kelly"]["warm_start"]["kept"] = False

    def worse(result):
        w = next(d for d in result["decisions"] if d.get("kelly", {}).get("warm_start", {}) and
                 d["kelly"]["warm_start"]["kept"])["kelly"]["warm_start"]
        w["value_after"] = w["value_before"]

    def clocked(result):
        next(d for d in result["decisions"] if d.get("kelly", {}).get("warm_start", {}) and
             d["kelly"]["warm_start"]["kept"])["kelly"]["warm_start"]["termination"] = "maxTimeLimit"

    for edit in (unkeep, worse, clocked):
        _forged_refused(params, inputs, method, warm, edit)


def test_a_clock_stopped_warm_start_is_never_traded(monkeypatch):
    """A certification whose stop may depend on the clock leaves the declined week exactly as it was."""
    from dskit.pipeline.libs.pyomo import TransportPortfolioSolve

    params, inputs = _poor_incumbent_search(monkeypatch)
    plain = {d["date"]: d for d in _solved_weeks(
        ledger_studies.WeeklyOptionPortfolioStudy(params, inputs)._method(params["methods"][0], None))}
    monkeypatch.setattr(TransportPortfolioSolve, "_warm_certified", lambda self, *args: False)
    monkeypatch.setattr(TransportPortfolioSolve, "DETERMINISTIC_TERMINATIONS", ("never",))
    params, inputs = _poor_incumbent_search(monkeypatch, warm_start={"node_limit": 20})
    method = params["methods"][0]
    warm = ledger_studies.WeeklyOptionPortfolioStudy(params, inputs)._method(method, None)
    weeks = [d for d in _solved_weeks(warm) if d["kelly"]["warm_start"]]
    assert weeks
    for week in weeks:
        assert not week["kelly"]["warm_start"]["kept"] and _outcome(week) == "declined"
        assert week["selection"] == plain[week["date"]]["selection"], "what a clock-stopped solve found is not traded"
    assert ledger_studies.WeeklyOptionPortfolioStudy(params, inputs)._method(method, None, recorded=warm) == warm


def test_the_clock_only_halts_the_search_and_never_shortens_the_certification(monkeypatch):
    """The search has its own share of the clock: spent, it halts (recorded) with the incumbent it has; its seconds
    are left out of the time the certification sees."""
    import time as clock_module
    from dskit.pipeline.libs.pyomo import TransportPortfolioSolve

    monkeypatch.setattr(TransportPortfolioSolve, "UTILITY_SEARCH_SHARE", 0.)
    params, inputs = _poor_incumbent_search(monkeypatch, search=SEARCH)
    halted = ledger_studies.WeeklyOptionPortfolioStudy(params, inputs)._method(params["methods"][0], None)
    for week in _solved_weeks(halted):
        s = week["kelly"]["search"]
        assert s["halted_by"] == "clock" and s["iterations"] == 0 and not s["improved"] and not s["budget_exhausted"]
        assert s["value_after"] == s["value_before"] and s["allowance"] == 0.
    assert ledger_studies.WeeklyOptionPortfolioStudy(params, inputs)._method(
        params["methods"][0], None, recorded=halted) == halted
    monkeypatch.undo()
    # A search that (by a fake clock) takes ten budgets still leaves the certification its whole remaining time.
    real, offset, limits = clock_module.perf_counter, [0.], []
    monkeypatch.setattr(clock_module, "perf_counter", lambda: real()+offset[0])
    search = TransportPortfolioSolve._search_incumbent

    def slow(self, *args, **kwargs):
        record = search(self, *args, **kwargs)
        offset[0] += 10*KELLY["time_limit"]
        return record
    monkeypatch.setattr(TransportPortfolioSolve, "_search_incumbent", slow)
    once = TransportPortfolioSolve._solve_once

    def spy(self, solver, model, time_limit=None):
        limits.append(time_limit)
        return once(self, solver, model, time_limit)
    monkeypatch.setattr(TransportPortfolioSolve, "_solve_once", spy)
    params, inputs = _declined_kelly_fixture(search=SEARCH)
    ledger_studies.WeeklyOptionPortfolioStudy(params, inputs)._method(params["methods"][0], None)
    # The support search is capped at its 40% share; only a certification can be handed (nearly) the whole budget,
    # and it would be handed the 1 s floor if the search's ten budgets were charged to it.
    assert any(t is not None and t > .9*KELLY["time_limit"] for t in limits), limits


def test_a_failed_neighbourhood_is_counted_and_a_missing_search_record_refused(monkeypatch):
    from dskit.pipeline.libs.pyomo import TransportPortfolioSolve

    once = TransportPortfolioSolve._solve_once

    def failing(self, solver, model, time_limit=None):
        if solver.options.get("random_seed") == SEARCH["seed"]:
            raise RuntimeError("neighbourhood fixture failure")
        return once(self, solver, model, time_limit)
    monkeypatch.setattr(TransportPortfolioSolve, "_solve_once", failing)
    params, inputs = _poor_incumbent_search(monkeypatch, search=SEARCH)
    method = params["methods"][0]
    result = ledger_studies.WeeklyOptionPortfolioStudy(params, inputs)._method(method, None)
    weeks = [d for d in _solved_weeks(result) if d["kelly"]["search"]["iterations"]]
    assert weeks and all(d["kelly"]["search"]["failed"] == d["kelly"]["search"]["iterations"]
                         and not d["kelly"]["search"]["improved"] for d in weeks)

    def missing(forged):
        next(d for d in forged["decisions"] if d.get("kelly", {}).get("search"))["kelly"]["search"] = None

    def overcounted(forged):
        s = next(d for d in forged["decisions"] if d.get("kelly", {}).get("search"))["kelly"]["search"]
        s["failed"] = s["iterations"]+1

    def halted_by_wall(forged):
        next(d for d in forged["decisions"] if d.get("kelly", {}).get("search"))["kelly"]["search"]["halted_by"] = "wall"

    def clock_halt_without_allowance(forged):
        s = next(d for d in forged["decisions"] if d.get("kelly", {}).get("search"))["kelly"]["search"]
        s["halted_by"], s["budget_exhausted"], s["allowance"] = "clock", False, None

    def negative_allowance(forged):
        next(d for d in forged["decisions"] if d.get("kelly", {}).get("search"))["kelly"]["search"]["allowance"] = -1.

    for edit in (missing, overcounted, halted_by_wall, clock_halt_without_allowance, negative_allowance):
        _forged_refused(params, inputs, method, result, edit)


def test_a_comparison_row_carries_the_search_records_only_when_the_decision_does():
    row = ledger_studies.MIOPacketCompare._kelly_row
    plain = {"status": "estimated", "solve": {"strategy_outcome": "declined", "bound_source": "relaxation"}}
    assert set(row(plain)) == set(ledger_studies.MIOPacketCompare._KELLY_ROW_KEYS)
    searched = {**plain, "search": {"iterations": 3}, "warm_start": None}
    assert row(searched) == {**row(plain), "search": {"iterations": 3}, "warm_start": None}

def test_search_replay_hedge_keeps_the_exact_declared_formulation():
    import copy

    params, _ = _kelly_fixture()
    params.update(locked_method_ids=list(FORMULATIONS), hedge_parity=dict(_PARITY))
    baseline = copy.deepcopy(TOP_FORMULATIONS["kelly025_top100_search_lp"])
    arm_id = "kelly025_top100_search_replay_hedge_lp"
    method = {"id": arm_id, **baseline, "hedge": dict(_REPLAY_BLOCK)}
    params["methods"] = [method]
    study = ledger_studies.WeeklyOptionPortfolioStudy
    study.validate_configuration(params, tail_end_before="2021-07-01")
    for field, value in (("seed", 9), ("node_limit", 9)):
        mutated = copy.deepcopy(method)
        mutated["kelly"]["search"][field] = value
        params["methods"] = [mutated]
        with pytest.raises(ValueError, match="kelly_search_hedge_formulation"):
            study.validate_configuration(params, tail_end_before="2021-07-01")
    params["methods"] = [{**method, "id": "kelly025_top100_search_lp"}]
    with pytest.raises(ValueError, match="kelly_search_hedge_formulation"):
        study.validate_configuration(params, tail_end_before="2021-07-01")

@pytest.mark.parametrize("fraction", [None, .5, .75])
def test_exact_search_hedge_reuses_the_solver_and_replays_the_settled_forward(monkeypatch, fraction):
    from datetime import date, timedelta

    from dskit.pipeline.libs.pyomo import TransportPortfolioSolve

    params, inputs = _program_world()
    closes = inputs["X"]["bars"]
    day = date.fromisoformat(closes[0]["date"])
    earlier = []
    while len(earlier) < 62:
        day -= timedelta(days=1)
        if day.weekday() < 5:
            earlier.append({**closes[0], "date": day.isoformat(),
                            "close": 100 + len(earlier) % 3 * .1})
    closes[:0] = reversed(earlier)
    params["clocks"]["history_start"] = closes[0]["date"]
    params["clocks"]["sessions"] = [row["date"] for row in closes]
    params["decision_weeks"] = ["2019-01-02"]
    _for_kelly(params)
    inputs["X"]["chain"] = [row for row in inputs["X"]["chain"]
                             if (row["strike"] == 100 and row["type"] == "call")
                             or row["date"] == "2019-02-01"]
    for row in inputs["X"]["chain"]:
        if row["date"] in ("2019-01-02", "2019-01-03"):
            row["bid"], row["ask"] = .15, .16
    method_id = "kelly025_top100_search_lp"
    baseline = {"id": method_id, **TOP_FORMULATIONS[method_id]}
    if fraction is not None:
        method_id = f"kelly{int(fraction * 100):03}_ls_lp"
        baseline = {"id": method_id, **LOCAL_FORMULATIONS["kelly050_ls_lp"],
                    "kelly": {**LOCAL_FORMULATIONS["kelly050_ls_lp"]["kelly"], "fraction": fraction}}
    arm = {**baseline, "id": method_id.removesuffix("_lp") + "_replay_hedge_lp", "hedge": dict(_REPLAY_BLOCK)}
    params["methods"] = [baseline]
    calls = []
    real = TransportPortfolioSolve.run

    def capture(solver, ctx, program):
        calls.append(program)
        return real(solver, ctx, program)

    monkeypatch.setattr(TransportPortfolioSolve, "run", capture)
    plain = ledger_studies.WeeklyOptionPortfolioStudy(params, inputs)._method(baseline, None)
    params.update(methods=[arm], hedge_parity=dict(_PARITY), locked_method_ids=list(FORMULATIONS))
    study = ledger_studies.WeeklyOptionPortfolioStudy(params, inputs)
    hedged = study._method(arm, None)
    assert len(calls) == 2 and calls[0] == calls[1]
    assert plain["decisions"][0]["books"] == hedged["decisions"][0]["books"]
    trade, = hedged["trades"]
    assert trade["hedge"]["status"] == "ok" and trade["hedge"]["units"] != 0
    assert trade["close_date"] == "2019-02-01"
    assert study._method(arm, None, recorded=hedged) == hedged
    assert len(calls) == 2
    if fraction is not None:
        from index_options.mio_worker import MIOMethodWorker

        claim = MIOMethodWorker.actions(hedged, study.weeks)
        assert "stage1" in claim["decisions"][study.weeks[0]]
        replay = study._method(arm, None, derived=claim)
        assert replay["trades"] == hedged["trades"]
        assert study._method(arm, None, recorded=replay) == replay
        assert len(calls) == 2

def test_only_exact_search_calibration_point_arm_and_identity_twin_validate():
    params, _ = _kelly_fixture()
    params["locked_method_ids"] = list(FORMULATIONS)
    baseline = TOP_FORMULATIONS["kelly025_top100_search_lp"]
    validate = ledger_studies.WeeklyOptionPortfolioStudy.validate_configuration
    for method_id, block in (
            ("kelly025_top100_search_cal_lp", dict(_CAL)),
            ("kelly025_top100_search_cal_control_lp", {**_CAL, "form": "identity", "control": True})):
        method = {"id": method_id, **baseline, "calibration": block}
        params["methods"] = [method]
        validate(params, tail_end_before="2021-07-01")
        for bad in ({**method, "id": "other_kelly"},
                    {**method, "kelly": {**method["kelly"], "fraction": .5}},
                    {**method, "calibration": {**block, "variant": "lcb"}}):
            params["methods"] = [bad]
            with pytest.raises(ValueError, match="calibration_with_kelly"):
                validate(params, tail_end_before="2021-07-01")

def test_combined_search_pair_refuses_other_strategies_and_program_blocks():
    import copy

    params, _ = _kelly_fixture()
    params.update(locked_method_ids=list(FORMULATIONS), hedge_parity=dict(_PARITY))
    baseline = TOP_FORMULATIONS["kelly025_top100_search_lp"]
    method = {"id": "kelly025_top100_search_cal_replay_hedge_lp", **baseline,
              "calibration": dict(_CAL), "hedge": dict(_REPLAY_BLOCK)}
    validate = ledger_studies.WeeklyOptionPortfolioStudy.validate_configuration
    params["methods"] = [method]
    validate(params, tail_end_before="2021-07-01")
    for bad, message in (
            ({**method, "hedge": {**method["hedge"], "mode": "program"}}, "calibration_with_kelly"),
            ({**method, "exposure_limits": dict(_WIDE)}, "exposure_limits_with_kelly"),
            ({**method, "id": "other_kelly"}, "calibration_with_kelly"),
            ({key: value for key, value in method.items() if key != "calibration"}, "kelly_search_hedge_formulation"),
            ({key: value for key, value in method.items() if key != "hedge"}, "calibration_with_kelly")):
        params["methods"] = [bad]
        with pytest.raises(ValueError, match=message):
            validate(params, tail_end_before="2021-07-01")
    altered = copy.deepcopy(method)
    altered["kelly"]["search"]["iterations"] -= 1
    params["methods"] = [altered]
    with pytest.raises(ValueError, match="calibration_with_kelly"):
        validate(params, tail_end_before="2021-07-01")


# --- the one-stage Kelly local search (ADR-0307) ---------------------------------------------------------------------

#: Budgets of the search, and the dollar program it starts from: the sized arms' stage 1 (`w1_010_shock10_lp`'s radius and shock).
LOCAL_SEARCH = {"moves": 200, "adds_evaluated": 64, "swaps_evaluated": 64, "restarts": 0, "seed": 20261109,
                "start": {"rho": FORMULATIONS["w1_010_shock10_lp"]["rho"], "shock": SHOCK10}}
LOCAL_FORMULATIONS = {
    "kelly025_ls_lp": {**_KELLY_BASE, "kelly": {**KELLY, "fraction": .25, "local_search": LOCAL_SEARCH}},
    "kelly050_ls_lp": {**_KELLY_BASE, "kelly": {**KELLY, "fraction": .5, "local_search": LOCAL_SEARCH}},
}


def _local_world(search=None, fraction=.5):
    """The overlapping two-week kelly world, under a local-search method."""
    params, data, method = _overlapping_kelly_world()
    method = {"id": "ls", **_KELLY_BASE, "kelly": {**method["kelly"], "fraction": fraction,
                                                  "local_search": {**LOCAL_SEARCH, **(search or {})}}}
    params["methods"] = [method]
    return params, data, method


def test_the_local_search_formulations_validate_and_differ_from_the_kelly_ones_only_by_the_key():
    for name, method in LOCAL_FORMULATIONS.items():
        params, inputs = _weekly_portfolio_fixture()
        params["methods"] = [{"id": name, **method}]
        _for_kelly(params)
        ledger_studies.WeeklyOptionPortfolioStudy.validate_configuration(params)
    for ls, plain in (("kelly025_ls_lp", "kelly025_lp"), ("kelly050_ls_lp", "kelly050_lp")):
        assert {k: v for k, v in LOCAL_FORMULATIONS[ls]["kelly"].items() if k != "local_search"} == \
            KELLY_FORMULATIONS[plain]["kelly"]
        assert {k: v for k, v in LOCAL_FORMULATIONS[ls].items() if k != "kelly"} == \
            {k: v for k, v in KELLY_FORMULATIONS[plain].items() if k != "kelly"}
    assert LOCAL_SEARCH["start"] == {"rho": .10, "shock": SHOCK10}


@pytest.mark.parametrize("change", [
    {"moves": 0}, {"adds_evaluated": 0}, {"swaps_evaluated": -1}, {"restarts": 1.5}, {"seed": True},
    {"start": None}, {"start": {"rho": -1., "shock": SHOCK10}}, {"start": {"rho": .1}},
    {"start": {"rho": .1, "shock": {**SHOCK10, "scope": "filled_account"}}},
    {"start": {"rho": .1, "shock": {"moves": [], "loss_fraction": .1, "beta_lookback": 60}}},
    {"extra": 1}])
def test_a_malformed_local_search_declaration_is_refused(change):
    params, _data, _method = _local_world(change)
    with pytest.raises(ValueError, match="invalid fixed method"):
        ledger_studies.WeeklyOptionPortfolioStudy.validate_configuration(params)


@pytest.mark.parametrize("key,value", [("max_groups", 100), ("accept_uncertified", True), ("search", {"x": 1}),
                                       ("warm_start", {"node_limit": 1}), ("outer_approx", {"x": 1})])
def test_the_local_search_refuses_the_keys_that_steer_the_integer_program(key, value):
    params, _data, method = _local_world()
    method["kelly"][key] = value
    with pytest.raises(ValueError, match="invalid fixed method"):
        ledger_studies.WeeklyOptionPortfolioStudy.validate_configuration(params)


def test_a_method_without_the_local_search_key_keeps_its_records_and_limits():
    """No `local` field, no `stage1`, the kelly limits as before; `stage1=True` is a local-search method's own asking."""
    params, data, method = _overlapping_kelly_world()
    result = ledger_studies.WeeklyOptionPortfolioStudy(params, data)._method(method, None)
    for week in result["decisions"]:
        assert "local" not in week.get("kelly", {}) and "stage1" not in week
    from dskit.pipeline.libs.option_ledger import FundedOptionAccount
    study = ledger_studies.WeeklyOptionPortfolioStudy(params, data)
    account = FundedOptionAccount(10000., 0., 14, "2019-01-02")
    assert len(study._limits(account, method, {"groups": []}, solve=True)) == 1
    assert len(study._limits(account, method, {"groups": []}, solve=True, stage1=True)) == 2+len(params["symbols"])


def _local_result(**kw):
    params, data, method = _local_world(**kw)
    study = ledger_studies.WeeklyOptionPortfolioStudy(params, data)
    return params, data, method, study, study._method(method, None)


def test_a_local_search_week_trades_the_search_and_records_its_evidence():
    _params, _data, _method, _study, result = _local_result()
    week = result["decisions"][0]
    assert week["status"] == "selected" and week["books"]
    local = week["kelly"]["local"]
    assert set(local) == {"start_kind", "start_value", "end_value", "start_contracts", "end_contracts", "moves",
                          "evaluations", "local_optimum", "budget_exhausted", "restarts", "restarts_skipped",
                          "move_counts"}
    assert local["start_kind"] == "stage1" and local["end_value"] >= local["start_value"]
    assert local["end_contracts"] == sum(sum(row) for row in week["selection"]["quantities"]) > 0
    assert week["selection"]["utility"]["exact"] == pytest.approx(local["end_value"], rel=1e-9, abs=1e-9)
    assert week["kelly"]["solve"] == {"strategy_outcome": "local_search", "bound_source": None}
    certificate = week["certificates"][-1]
    assert certificate["strategy_outcome"] == "local_search" and certificate["certified"] is False
    assert certificate["bound"] is None and certificate["gap"] is None and certificate["seconds"] is None
    assert week["stage1"]["certificates"] == week["certificates"][:-1] and week["stage1"]["selection"]["quantities"]
    assert "uncertified" not in week["kelly"]
    assert sum(b["contracts"] for b in week["books"]) == local["end_contracts"]


def test_the_local_search_builds_no_integer_program_and_starts_from_the_dollar_selection(monkeypatch):
    """The solver is handed the stage-one DOLLAR program (radius, shock rows, dollar caps) and never a utility program."""
    seen = _spy_utility_programs(monkeypatch)
    _params, _data, _method, _study, result = _local_result()
    assert seen and all("utility" not in program for program in seen)
    first = seen[0]
    assert all(g["radius"] == .10 for g in first["groups"])
    assert [row["id"] for row in first["joint_rows"]] == [f"shock:{m}" for m in SHOCK10["moves"]]
    assert len(first["caps"]) > 1 and max(c["upper"] for c in first["caps"]) <= 10000.
    # The W1 radius and the shock gate take this fixture's whole edge: stage one holds cash, the search still climbs.
    week = result["decisions"][0]
    assert not any(q for row in week["stage1"]["selection"]["quantities"] for q in row)
    assert week["kelly"]["local"]["start_contracts"] == 0 and week["kelly"]["local"]["end_contracts"] > 0
    # A nominal start (no radius, a shock gate wide open) trades, and the search starts from what it selected.
    loose = {"start": {"rho": 0., "shock": {**SHOCK10, "loss_fraction": 1.}}}
    _p, _d, _m, _s, nominal = _local_result(search=loose)
    start = nominal["decisions"][0]["stage1"]["selection"]["quantities"]
    local = nominal["decisions"][0]["kelly"]["local"]
    assert any(q for row in start for q in row) and local["start_contracts"] == sum(sum(r) for r in start) > 0
    assert local["start_kind"] == "stage1" and local["end_value"] >= local["start_value"]


def test_a_local_search_week_is_deterministic_and_the_recorded_route_replays_it_exactly():
    import copy

    params, data, method, _study, result = _local_result()
    again = ledger_studies.WeeklyOptionPortfolioStudy(params, data)._method(method, None)
    # Two live runs differ only in the stage-one solver's own wall-clock seconds; the decision does not read the clock.
    for first, second in zip(result["decisions"], again["decisions"]):
        for key in ("status", "selection", "books", "kelly"):
            assert first.get(key) == second.get(key), key
        assert first.get("certificates", [])[-1:] == second.get("certificates", [])[-1:]
    assert ledger_studies.WeeklyOptionPortfolioStudy(params, data)._method(method, None, recorded=result) == result
    changed = copy.deepcopy(result)
    week = changed["decisions"][0]
    week["selection"]["quantities"] = [[q+1 if q else 0 for q in row] for row in week["selection"]["quantities"]]
    with pytest.raises(ValueError, match="recorded local-search selection"):
        ledger_studies.WeeklyOptionPortfolioStudy(params, data)._method(method, None, recorded=changed)
    forged = copy.deepcopy(result)
    forged["decisions"][0]["kelly"]["local"]["end_value"] += 1.
    with pytest.raises(ValueError, match="differs from authoritative replay"):
        ledger_studies.WeeklyOptionPortfolioStudy(params, data)._method(method, None, recorded=forged)
    lacking = copy.deepcopy(result)
    del lacking["decisions"][0]["stage1"]
    with pytest.raises(ValueError, match="lacks recorded solve evidence"):
        ledger_studies.WeeklyOptionPortfolioStudy(params, data)._method(method, None, recorded=lacking)


def _derived_from(result, study, method):
    """A foreign worker's claim for each eligible week: the final actions, the search's certificate, the stage one."""
    from dskit.pipeline.libs.pyomo import TransportPortfolioSolve

    decisions = {}
    for week in result["decisions"]:
        if "stage1" in week:
            decisions[week["date"]] = {
                "actions": week["selection"]["quantities"], "certificates": week["certificates"][-1:],
                "stage1": {"actions": TransportPortfolioSolve.actions(week["stage1"]["selection"]),
                           "certificates": week["stage1"]["certificates"]}}
    return {"method": method["id"], "weeks": study.weeks, "decisions": decisions}


def test_the_derived_route_reruns_the_search_and_refuses_actions_it_does_not_reach():
    import copy
    from dskit.pipeline.libs.pyomo import RecordedCheckRefusal

    params, data, method, study, result = _local_result()
    derived = _derived_from(result, study, method)
    replay = ledger_studies.WeeklyOptionPortfolioStudy(params, data)._method(method, None, derived=derived)
    assert [w.get("selection") for w in replay["decisions"]] == [w.get("selection") for w in result["decisions"]]
    assert [w.get("books") for w in replay["decisions"]] == [w.get("books") for w in result["decisions"]]
    forged = copy.deepcopy(derived)
    day = next(iter(forged["decisions"]))
    forged["decisions"][day]["actions"] = [[0]*len(row) for row in forged["decisions"][day]["actions"]]
    with pytest.raises(RecordedCheckRefusal, match="local-search actions"):
        ledger_studies.WeeklyOptionPortfolioStudy(params, data)._method(method, None, derived=forged)
    bare = copy.deepcopy(derived)
    del bare["decisions"][day]["stage1"]
    with pytest.raises(ValueError, match="derived method or decision calendar differs"):
        ledger_studies.WeeklyOptionPortfolioStudy(params, data)._method(method, None, derived=bare)
    bad_stage = copy.deepcopy(derived)
    bad_stage["decisions"][day]["stage1"] = {"actions": 1}
    with pytest.raises(ValueError, match="stage-one evidence"):
        ledger_studies.WeeklyOptionPortfolioStudy(params, data)._method(method, None, derived=bad_stage)


def test_the_policy_route_refuses_a_local_search_method():
    params, data, method = _local_world()
    study = ledger_studies.WeeklyOptionPortfolioStudy(params, data)
    with pytest.raises(ValueError, match="local-search method"):
        next(study.policy_steps(method["id"]))


def test_a_stage_one_timeout_starts_the_search_from_cash_and_says_so(monkeypatch):
    from dskit.pipeline.libs.pyomo import TransportPortfolioSolve

    run = TransportPortfolioSolve.run

    def timed_out(self, ctx, inputs):
        selection = run(self, ctx, inputs)["selection"]
        return {"selection": {"timeout": "no_incumbent", "candidate": None,
                              "certificates": selection["certificates"]}}
    monkeypatch.setattr(TransportPortfolioSolve, "run", timed_out)
    _params, _data, _method, _study, result = _local_result()
    week = result["decisions"][0]
    assert week["kelly"]["local"]["start_kind"] == "stage1_timeout_cash" and week["kelly"]["local"]["start_contracts"] == 0
    assert week["status"] in ("selected", "cash")


def test_the_move_budget_binds_and_is_recorded():
    _params, _data, _method, _study, tight = _local_result(search={"moves": 1})
    local = tight["decisions"][0]["kelly"]["local"]
    assert local["moves"] <= 1 and (local["budget_exhausted"] or local["local_optimum"])
