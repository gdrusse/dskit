"""``pmquant-kelly-mio`` under the toolkit's conformance bar, plus its behaviour.

The fixture is two events of two rungs each, built through the child's own
record path (``DecisionEpochRecord`` -> ``market_record_from_epoch``), a
stub signal with a clear per-contract belief, and a scalar fee book. The
solver is the real ``appsi_highs`` — never mocked — except where the test
proves it was NOT woken.
"""

import json
import os
import sys

import pytest

from dskit.pipeline.conformance import NodeProbe, conformance_suite
from dskit.pipeline.libs.pyomo import DEFAULT_SOLVER, BudgetedSelect
from dskit.pipeline.node import NodeContext

from pmquant import mio
from pmquant.books import DecisionEpochRecord, market_record_from_epoch, mid_from_ladders
from pmquant.fees import FeeRateUnresolved
from pmquant.nodes_capital import (
    DEFAULT_SPLIT,
    DISPOSITION_DECLINED,
    DISPOSITION_FEE_GATE,
    DISPOSITION_ROUTED_OUT,
    DISPOSITION_SIZED,
    DISPOSITION_ZERO_LOTS,
    DISPOSITIONS,
    NODE_KINDS,
    KellyMIO,
)

KIND = "pmquant-kelly-mio"
SERIES = "KXTEST"
FEES = {SERIES: 0.07}
BANKROLL, DEPLOY_FRAC = 1000.0, 0.5

PARAMS = {
    "bankroll": BANKROLL,
    "deploy_frac": DEPLOY_FRAC,
    "kelly_fraction": 0.5,
    "fee_rate_by_series": dict(FEES),
    "split": "test",
}

#: contract -> (event, older-lead book, newest-lead book) as (yes_bids, no_bids).
#: E1: T1 carries a YES edge at belief 0.40 (ask 0.30); T2 has no edge at 0.60.
#: E2: T2 YES (ask 0.61) strictly dominates T1 NO (ask 0.62) for the SAME bet,
#: so T1 is entered with zero lots — a natural zero-lot disposition.
BOOKS = {
    "KXTEST-E1-T1": ("KXTEST-E1", ([[0.25, 500]], [[0.73, 500]]), ([[0.28, 500]], [[0.70, 500]])),
    "KXTEST-E1-T2": ("KXTEST-E1", ([[0.55, 500]], [[0.41, 500]]), ([[0.58, 500]], [[0.38, 500]])),
    "KXTEST-E2-T1": ("KXTEST-E2", ([[0.35, 500]], [[0.63, 500]]), ([[0.38, 500]], [[0.60, 500]])),
    "KXTEST-E2-T2": ("KXTEST-E2", ([[0.57, 500]], [[0.41, 500]]), ([[0.59, 500]], [[0.39, 500]])),
}
BELIEFS = {
    "KXTEST-E1-T1": 0.40,
    "KXTEST-E1-T2": 0.60,
    "KXTEST-E2-T1": 0.30,
    "KXTEST-E2-T2": 0.70,
}
OLD_TS, NEW_TS, SETTLE_TS = 1_000, 2_000, 9_000


class StubSignal:
    """``predict(record)`` answers the belief table, ``None`` off it."""

    def __init__(self, beliefs):
        self.beliefs = dict(beliefs)
        self.asked = []

    def predict(self, record):
        self.asked.append(record.contract)
        return self.beliefs.get(record.contract)


def _levels(pairs):
    return tuple((float(p), int(d)) for p, d in pairs)


def _lead(series, event, contract, ts, yes, no, lead_frac=0.5):
    yes_levels, no_levels = _levels(yes), _levels(no)
    rec = DecisionEpochRecord(
        series=series, event_ticker=event, contract_ticker=contract, epoch_kind="lead",
        lead_frac=lead_frac, epoch_ts_ms=ts, source="fixture", yes_levels=yes_levels,
        no_levels=no_levels, p_mid=mid_from_ladders(yes_levels, no_levels), staleness_ms=0,
        admissible=True, quality_ok=True, usable=True, reason="ok",
    )
    return market_record_from_epoch("kalshi", rec)


def _settle(series, event, contract, ts=SETTLE_TS):
    rec = DecisionEpochRecord(
        series=series, event_ticker=event, contract_ticker=contract, epoch_kind="settle",
        lead_frac=None, epoch_ts_ms=ts, source="fixture", yes_levels=(), no_levels=(),
        p_mid=None, staleness_ms=None, admissible=False, quality_ok=False, usable=False,
        reason="settle",
    )
    return market_record_from_epoch("kalshi", rec)


def _records(events=("KXTEST-E1", "KXTEST-E2"), old_ts=OLD_TS, new_ts=NEW_TS):
    out = []
    for contract, (event, old, new) in BOOKS.items():
        if event not in events:
            continue
        out.append(_lead(SERIES, event, contract, old_ts, *old, lead_frac=0.7))
        out.append(_lead(SERIES, event, contract, new_ts, *new, lead_frac=0.3))
        out.append(_settle(SERIES, event, contract))
    return out


def _inputs(records=None, survivors=(SERIES,), beliefs=None, markets=None):
    inputs = {
        "records": _records() if records is None else records,
        "survivors": list(survivors),
        "signal": StubSignal(BELIEFS if beliefs is None else beliefs),
    }
    if markets is not None:
        inputs["markets"] = markets
    return inputs


def _ctx(tmp_path, splits=None):
    return NodeContext(name="t", asof="2026-01-01", run_dir=str(tmp_path), splits=splits)


def _node(**over):
    return KellyMIO("size", {**PARAMS, **over})


# ---------------------------------------------------------------------------
# Conformance — the toolkit bar, capital probe fully populated
# ---------------------------------------------------------------------------


def probes(tmp_path):
    return {
        KIND: NodeProbe(
            params=dict(PARAMS),
            required=("bankroll", "deploy_frac", "kelly_fraction", "fee_rate_by_series"),
            inputs=_inputs(),
            stream_ports=("records",),
            runnable=True,
            budget=DEPLOY_FRAC * BANKROLL,
            outlay=lambda out: out["outlay"],
            gate_port="survivors",
            ctx=NodeContext(name="probe", asof="2026-01-01", run_dir=str(tmp_path), splits=None),
        ),
    }


TestKellyConformance = conformance_suite(
    registry=NODE_KINDS,
    module="pmquant.nodes_capital",
    probes=probes,
    expected_roles={KIND: "capital"},
    name="TestKellyConformance",
)


# ---------------------------------------------------------------------------
# Params — plan-time refusals, refs tolerated, the determinism pins
# ---------------------------------------------------------------------------


class TestParams:
    def test_the_reference_params_validate_clean(self):
        assert KellyMIO.validate_params(dict(PARAMS)) == []

    @pytest.mark.parametrize(
        "missing", ["bankroll", "deploy_frac", "kelly_fraction", "fee_rate_by_series"]
    )
    def test_required_knobs_refuse_by_name(self, missing):
        params = {k: v for k, v in PARAMS.items() if k != missing}
        assert any(missing in p for p in KellyMIO.validate_params(params))

    def test_defaults_are_the_module_constants(self):
        node = _node()
        assert node.params.get("split", DEFAULT_SPLIT) == "test"
        assert DEFAULT_SPLIT == "test"
        assert KellyMIO.validate_params({**PARAMS, "n_tangents": mio.DEFAULT_N_TANGENTS}) == []
        assert KellyMIO.validate_params({**PARAMS, "min_lot": mio.DEFAULT_MIN_LOT}) == []

    @pytest.mark.parametrize(
        ("knob", "value"),
        [
            ("bankroll", 0), ("bankroll", -5.0), ("bankroll", "1,000"), ("bankroll", True),
            ("deploy_frac", 0.0), ("deploy_frac", 1.0), ("deploy_frac", 1.5),
            ("kelly_fraction", 0.0), ("kelly_fraction", 1.01), ("kelly_fraction", True),
            ("min_lot", 0), ("min_lot", 1.5), ("min_lot", True),
            ("tau", -0.01), ("tau", "0"),
            ("depth_haircut", 0.0), ("depth_haircut", 1.1),
            ("n_tangents", 1), ("n_tangents", "128"),
            ("event_cap", 0.0), ("event_cap", -1.0), ("event_cap", "50"),
            ("split", "holdout"), ("split", None), ("split", 3),
            ("fee_rate_by_series", {}), ("fee_rate_by_series", {"KXA": 1.5}),
            ("fee_rate_by_series", "0.07"), ("fee_rate_by_series", [("KXA", 0.07)]),
            ("fee_rate_by_series", {"KXA": {"cases": []}}),
        ],
    )
    def test_bad_knobs_refuse_by_name(self, knob, value):
        problems = KellyMIO.validate_params({**PARAMS, knob: value})
        assert any(knob in p for p in problems), (knob, value, problems)

    def test_unknown_knobs_refuse_by_name(self):
        problems = KellyMIO.validate_params({**PARAMS, "bankrol": 5.0})
        assert any("bankrol" in p for p in problems)

    def test_references_are_tolerated_at_plan(self):
        # a merged fee table wired from a join / table-file, and a $prev bankroll
        params = {
            **PARAMS,
            "fee_rate_by_series": "$fee_book.records",
            "bankroll": {"$prev": "replay.final_bankroll", "default": 1000.0},
            "event_cap": "$caps.per_event",
        }
        assert KellyMIO.validate_params(params) == []
        assert KellyMIO.validate_params({**PARAMS, "bankroll": "$replay.final_bankroll"}) == []

    def test_a_dated_fee_book_validates_at_plan(self):
        dated = {
            "POLYWX": [
                {"when": [{"field": "close_ts", "op": "<", "value": "2026-03-30T12:00:00Z"}],
                 "value": 0.0},
                {"when": [], "value": 0.05},
            ],
            **FEES,
        }
        assert KellyMIO.validate_params({**PARAMS, "fee_rate_by_series": dated}) == []

    def test_determinism_pins_agree_with_the_doorways_reference_subclass(self):
        # The three HiGHS keys appear in two classes; this is the pin.
        assert KellyMIO._HIGHS_DETERMINISM == BudgetedSelect._HIGHS_DETERMINISM
        assert set(KellyMIO._HIGHS_DETERMINISM) == {"mip_rel_gap", "threads", "random_seed"}

    def test_pins_are_injected_under_the_documents_options(self):
        assert _node()._solver_options() == KellyMIO._HIGHS_DETERMINISM
        merged = _node(solver_options={"threads": 4, "time_limit": 30.0})._solver_options()
        assert merged == {**KellyMIO._HIGHS_DETERMINISM, "threads": 4, "time_limit": 30.0}
        assert _node(solver="cbc")._solver_options() == {}
        assert _node(solver=DEFAULT_SOLVER)._solver_options() == KellyMIO._HIGHS_DETERMINISM
        # ...and the merged table is what the RESOLVED solver will hand to HiGHS
        resolved = _node(solver_options={"threads": 4})._resolve_solver()
        assert {k: resolved.options[k] for k in KellyMIO._HIGHS_DETERMINISM} == {
            **KellyMIO._HIGHS_DETERMINISM, "threads": 4
        }

    def test_the_disposition_vocabulary_is_closed(self):
        assert DISPOSITIONS == (
            DISPOSITION_ROUTED_OUT, DISPOSITION_FEE_GATE, DISPOSITION_ZERO_LOTS,
            DISPOSITION_DECLINED, DISPOSITION_SIZED,
        )
        assert DISPOSITION_ROUTED_OUT == "routed out before sizing"
        assert DISPOSITION_FEE_GATE == "fee gate rejected (net edge did not clear tau)"
        assert DISPOSITION_ZERO_LOTS == "entered but 0 lots (depth/constraint/cardinality)"
        assert DISPOSITION_DECLINED == "signal declined to price"
        assert DISPOSITION_SIZED == "sized"


# ---------------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------------


class TestInputs:
    def test_the_reference_inputs_validate_clean(self):
        assert _node().validate_inputs(_inputs()) == []

    def test_a_one_shot_record_stream_is_refused_not_walked(self):
        seen = []

        def one_shot():
            for rec in _records():
                seen.append(rec)
                yield rec

        inputs = _inputs()
        inputs["records"] = one_shot()
        problems = _node().validate_inputs(inputs)
        assert any("records" in p for p in problems) and not seen

    def test_survivors_signal_and_markets_are_shape_checked(self):
        inputs = _inputs()
        inputs["survivors"] = iter([SERIES])
        assert any("survivors" in p for p in _node().validate_inputs(inputs))
        inputs = _inputs()
        inputs["survivors"] = [1]
        assert any("survivors" in p for p in _node().validate_inputs(inputs))
        inputs = _inputs()
        inputs["signal"] = object()
        assert any("signal" in p for p in _node().validate_inputs(inputs))
        inputs = _inputs(markets="not-a-list")
        assert any("markets" in p for p in _node().validate_inputs(inputs))


# ---------------------------------------------------------------------------
# Behaviour — the real solver
# ---------------------------------------------------------------------------


def _run(tmp_path, inputs=None, splits=None, **over):
    node = _node(**over)
    return node, node.run(_ctx(tmp_path, splits), inputs or _inputs())


class TestRun:
    def test_output_shapes(self, tmp_path):
        node, out = _run(tmp_path)
        assert set(out) == {"positions", "outlay", "lots", "metrics", "evidence"}
        assert set(out["metrics"]) == {
            "n_rows", "n_events", "n_lots", "outlay", "expected_log_growth"
        }
        ev = out["evidence"]
        assert set(ev) == {
            "stage", "split", "totals", "instruments", "events", "candidates",
            "arb_candidates", "notes",
        }
        assert ev["stage"] == "sizing" and ev["split"] == "test"
        assert set(ev["totals"]) == {
            "n_candidates", "n_priced", "n_entered", "n_entered_zero_lots", "n_routed_out",
            "n_arb_routed", "budget", "outlay", "bankroll",
        }
        assert out["metrics"]["n_rows"] == len(_records())
        assert out["metrics"]["n_events"] == 2
        assert out["metrics"]["outlay"] == out["outlay"]
        assert ev["totals"]["budget"] == pytest.approx(DEPLOY_FRAC * BANKROLL)
        assert ev["totals"]["bankroll"] == BANKROLL
        assert isinstance(ev["notes"], list) and ev["notes"]

    def test_lots_is_the_sum_of_the_positions_and_all_positive(self, tmp_path):
        _node_, out = _run(tmp_path)
        assert out["lots"] == sum(out["positions"].values()) == out["metrics"]["n_lots"]
        assert all(isinstance(n, int) and n > 0 for n in out["positions"].values())
        assert all("|" in key for key in out["positions"])

    def test_the_newest_record_per_contract_is_the_one_sized(self, tmp_path):
        _node_, out = _run(tmp_path)
        cands = out["evidence"]["candidates"]
        assert set(cands) == set(BOOKS)
        assert all(row["asof_ms"] == NEW_TS for row in cands.values())
        # the older E1-T1 book (ask 0.27) would have shown a different mid
        assert cands["KXTEST-E1-T1"]["mid"] == pytest.approx(0.29)

    def test_every_candidate_carries_a_disposition_from_the_vocabulary(self, tmp_path):
        _node_, out = _run(tmp_path)
        cands = out["evidence"]["candidates"]
        assert all(row["disposition"] in DISPOSITIONS for row in cands.values())
        assert cands["KXTEST-E1-T1"]["disposition"] == DISPOSITION_SIZED
        assert cands["KXTEST-E1-T1"]["gated_side"] == "yes"
        assert cands["KXTEST-E1-T2"]["disposition"] == DISPOSITION_FEE_GATE
        assert cands["KXTEST-E1-T2"]["gated_side"] is None
        assert cands["KXTEST-E2-T2"]["disposition"] == DISPOSITION_SIZED
        assert cands["KXTEST-E2-T1"]["disposition"] == DISPOSITION_ZERO_LOTS
        assert cands["KXTEST-E2-T1"]["gated_side"] == "no"
        for row in cands.values():
            assert set(row) == {
                "instrument", "mid", "belief", "belief_edge", "asof_ms", "lead_frac",
                "fee_rate", "event", "disposition", "gated_side", "lots", "reason",
            }
            assert row["instrument"] == SERIES and row["fee_rate"] == 0.07
            assert row["lead_frac"] == 0.3
        assert cands["KXTEST-E1-T1"]["lots"] == out["positions"]["KXTEST-E1-T1|yes"]
        totals = out["evidence"]["totals"]
        assert totals["n_candidates"] == 4 and totals["n_priced"] == 4
        assert totals["n_entered"] == 3 and totals["n_entered_zero_lots"] == 1
        assert totals["n_routed_out"] == 0 and totals["n_arb_routed"] == 0

    def test_instruments_and_events_evidence(self, tmp_path):
        _node_, out = _run(tmp_path)
        inst = out["evidence"]["instruments"][SERIES]
        assert inst["n_candidates"] == 4 and inst["n_entered"] == 3
        assert inst["lots"] == out["lots"] and inst["fee_rate"] == 0.07
        assert inst["belief_edge_min"] <= inst["belief_edge_mean"] <= inst["belief_edge_max"]
        events = out["evidence"]["events"]
        assert set(events) == {"KXTEST-E1", "KXTEST-E2"}
        for event in events.values():
            assert set(event) >= {"n_entered", "lots", "outlay", "status", "expected_log_growth"}
            assert event["status"] == "optimal"
            assert event["law"] == "partition"
        assert events["KXTEST-E1"]["n_entered"] == 1
        assert events["KXTEST-E2"]["n_entered"] == 2
        assert sum(e["outlay"] for e in events.values()) == pytest.approx(out["outlay"])
        assert sum(e["expected_log_growth"] for e in events.values()) == pytest.approx(
            out["metrics"]["expected_log_growth"]
        )

    def test_two_events_each_fill_exactly_what_they_fill_alone(self, tmp_path):
        _n1, only_e1 = _run(tmp_path / "a", _inputs(_records(events=("KXTEST-E1",))))
        _n2, only_e2 = _run(tmp_path / "b", _inputs(_records(events=("KXTEST-E2",))))
        _n3, both = _run(tmp_path / "c")
        assert only_e1["positions"] and only_e2["positions"]
        assert both["positions"] == {**only_e1["positions"], **only_e2["positions"]}
        assert both["outlay"] == pytest.approx(only_e1["outlay"] + only_e2["outlay"])

    def test_the_budget_bound_holds_when_it_binds(self, tmp_path):
        strong = {c: (0.95 if BELIEFS[c] >= 0.5 else 0.05) for c in BELIEFS}
        _node_, out = _run(tmp_path, _inputs(beliefs=strong), deploy_frac=0.05)
        budget = 0.05 * BANKROLL
        assert 0.0 < out["outlay"] <= budget * (1 + 1e-9)
        assert out["outlay"] > 0.9 * budget

    def test_an_explicit_event_cap_that_overspends_the_budget_is_refused(self, tmp_path):
        strong = {c: (0.95 if BELIEFS[c] >= 0.5 else 0.05) for c in BELIEFS}
        node = _node(deploy_frac=0.1, event_cap=100.0)
        with pytest.raises(ValueError, match="deployable"):
            node.run(_ctx(tmp_path), _inputs(beliefs=strong))

    def test_an_empty_gate_deploys_nothing_without_waking_the_solver(self, tmp_path, monkeypatch):
        monkeypatch.setitem(sys.modules, "pyomo", None)
        node, out = _run(tmp_path, _inputs(survivors=()))
        assert out["positions"] == {} and out["outlay"] == 0.0 and out["lots"] == 0
        assert out["metrics"]["n_events"] == 0 and out["metrics"]["n_lots"] == 0
        assert out["evidence"]["candidates"] == {}
        assert out["evidence"]["totals"]["n_candidates"] == 0

    def test_a_gate_with_nothing_priced_never_wakes_the_solver(self, tmp_path, monkeypatch):
        monkeypatch.setitem(sys.modules, "pyomo", None)
        # every belief inside its quotes: candidates exist, none clears the fee gate
        flat = {"KXTEST-E1-T1": 0.29, "KXTEST-E1-T2": 0.60, "KXTEST-E2-T1": 0.39,
                "KXTEST-E2-T2": 0.60}
        _node_, out = _run(tmp_path, _inputs(beliefs=flat))
        assert out["outlay"] == 0.0 and out["positions"] == {}
        cands = out["evidence"]["candidates"]
        assert all(row["disposition"] == DISPOSITION_FEE_GATE for row in cands.values())
        assert out["evidence"]["totals"]["n_entered"] == 0

    def test_a_missing_fee_rate_refuses_naming_the_series(self, tmp_path):
        records = _records() + [
            _lead("KXNOFEE", "KXNOFEE-E1", "KXNOFEE-E1-T1", NEW_TS, [[0.28, 100]], [[0.70, 100]]),
        ]
        beliefs = {**BELIEFS, "KXNOFEE-E1-T1": 0.40}
        inputs = _inputs(records, survivors=(SERIES, "KXNOFEE"), beliefs=beliefs)
        with pytest.raises(FeeRateUnresolved, match="KXNOFEE"):
            _run(tmp_path, inputs)

    def test_a_dated_fee_book_resolves_at_the_markets_close(self, tmp_path):
        # the settle record at SETTLE_TS (1970) falls in the free span; the sizer prices at 0.0
        dated = {SERIES: [
            {"when": [{"field": "close_ts", "op": "<", "value": "2000-01-01T00:00:00Z"}],
             "value": 0.0},
            {"when": [], "value": None, "unpriceable": "post-2000 schedule unknown"},
        ]}
        _node_, out = _run(tmp_path, fee_rate_by_series=dated)
        assert all(row["fee_rate"] == 0.0 for row in out["evidence"]["candidates"].values())
        # without a settle record the dated series cannot resolve -> refusal by name
        no_settle = [r for r in _records() if r.native.epoch_kind != "settle"]
        with pytest.raises(FeeRateUnresolved, match=SERIES):
            _run(tmp_path / "b", _inputs(no_settle), fee_rate_by_series=dated)

    def test_a_crossed_book_lands_in_arb_candidates_and_is_never_sized(self, tmp_path):
        crossed = _lead(SERIES, "KXTEST-E3", "KXTEST-E3-T1", NEW_TS, [[0.85, 50]], [[0.90, 50]])
        inputs = _inputs(_records() + [crossed], beliefs={**BELIEFS, "KXTEST-E3-T1": 0.5})
        _node_, out = _run(tmp_path, inputs)
        arbs = out["evidence"]["arb_candidates"]
        assert len(arbs) == 1 and arbs[0]["contract"] == "KXTEST-E3-T1"
        assert "KXTEST-E3-T1|yes" not in out["positions"]
        assert "KXTEST-E3-T1|no" not in out["positions"]
        row = out["evidence"]["candidates"]["KXTEST-E3-T1"]
        assert row["disposition"] == DISPOSITION_ROUTED_OUT and "crossed" in row["reason"]
        assert out["evidence"]["totals"]["n_arb_routed"] == 1
        assert out["evidence"]["totals"]["n_routed_out"] == 1
        assert "KXTEST-E3" not in out["evidence"]["events"]

    def test_a_declined_belief_is_recorded_not_fabricated(self, tmp_path):
        beliefs = {k: v for k, v in BELIEFS.items() if k != "KXTEST-E1-T1"}
        _node_, out = _run(tmp_path, _inputs(beliefs=beliefs))
        row = out["evidence"]["candidates"]["KXTEST-E1-T1"]
        assert row["disposition"] == DISPOSITION_DECLINED and row["belief"] is None
        assert "KXTEST-E1-T1|yes" not in out["positions"]
        assert out["evidence"]["totals"]["n_priced"] == 3

    def test_unusable_and_one_sided_records_are_skipped(self, tmp_path):
        one_sided = _lead(SERIES, "KXTEST-E1", "KXTEST-E1-T1", NEW_TS + 1, [[0.28, 500]], [])
        assert one_sided.mid is None
        _node_, out = _run(tmp_path, _inputs(_records() + [one_sided]))
        # the newer one-sided book is not a price; the two-sided newest is sized
        assert out["evidence"]["candidates"]["KXTEST-E1-T1"]["asof_ms"] == NEW_TS
        assert "KXTEST-E1-T1|yes" in out["positions"]

    def test_a_native_that_is_not_a_decision_epoch_refuses(self, tmp_path):
        from dskit.pipeline.records import MarketRecord

        alien = MarketRecord(venue="kalshi", instrument=SERIES, contract="KXTEST-E9-T1",
                             asof_ms=NEW_TS, usable=True, reason="ok", group="KXTEST-E9",
                             bid=0.3, ask=0.4, mid=0.35)
        with pytest.raises(ValueError, match="DecisionEpochRecord"):
            _run(tmp_path, _inputs(_records() + [alien]))

    def test_the_split_selects_the_records(self, tmp_path):
        class Splits:
            def split_of(self, frame):
                return "test" if frame.asof_ms >= 1_500 else "train"

        e1 = _records(events=("KXTEST-E1",))                       # leads at 1000 / 2000
        e2 = _records(events=("KXTEST-E2",), old_ts=900, new_ts=1_000)  # wholly in train
        _node_, out = _run(tmp_path, _inputs(e1 + e2), splits=Splits())
        assert set(out["evidence"]["candidates"]) == {"KXTEST-E1-T1", "KXTEST-E1-T2"}
        assert out["evidence"]["split"] == "test"
        _node2, train = _run(tmp_path / "b", _inputs(e1 + e2), splits=Splits(), split="train")
        assert set(train["evidence"]["candidates"]) == set(BOOKS)
        assert train["evidence"]["candidates"]["KXTEST-E1-T1"]["asof_ms"] == OLD_TS

    def test_markets_rows_select_the_threshold_law(self, tmp_path):
        event = "KXTEST-E4"
        records = [
            _lead(SERIES, event, "KXTEST-E4-G10", NEW_TS, [[0.48, 500]], [[0.50, 500]]),
            _lead(SERIES, event, "KXTEST-E4-G20", NEW_TS, [[0.38, 500]], [[0.60, 500]]),
        ]
        markets = [
            {"ticker": "KXTEST-E4-G20", "strike_type": "greater", "floor_strike": 20.0,
             "cap_strike": None, "event_ticker": event},
            {"ticker": "KXTEST-E4-G10", "strike_type": "greater", "floor_strike": 10.0,
             "cap_strike": None, "event_ticker": event},
        ]
        beliefs = {"KXTEST-E4-G10": 0.60, "KXTEST-E4-G20": 0.30}
        _node_, out = _run(tmp_path, _inputs(records, beliefs=beliefs, markets=markets))
        ev = out["evidence"]["events"][event]
        assert ev["law"] == "threshold" and ev["n_omega"] == 3
        assert ev["n_entered"] == 2 and out["lots"] > 0
        assert set(out["positions"]) <= {"KXTEST-E4-G10|yes", "KXTEST-E4-G20|no"}
        # a threshold rung without a markets row has no place on the line: refuse
        with pytest.raises(ValueError, match="KXTEST-E4-G20"):
            _run(tmp_path / "b", _inputs(records, beliefs=beliefs, markets=markets[1:]))

    def test_partition_rows_and_no_rows_agree(self, tmp_path):
        rows = []
        for contract, (event, _old, _new) in BOOKS.items():
            rows.append({"ticker": contract, "strike_type": "between", "floor_strike": 1.0,
                         "cap_strike": 2.0, "event_ticker": event})
        _n1, with_rows = _run(tmp_path / "a", _inputs(markets=rows))
        _n2, without = _run(tmp_path / "b", _inputs())
        assert with_rows["positions"] == without["positions"]
        assert with_rows["evidence"]["events"]["KXTEST-E1"]["law"] == "partition"

    def test_a_rung_with_no_usable_book_still_dilutes_the_partition(self, tmp_path):
        # Without markets rows the universe is every rung the records NAME: T3
        # carries no usable book, so it is never a candidate — but it can still
        # settle YES, so its siblings' beliefs must NOT renormalize over it.
        event = "KXTEST-E5"
        two_sided = ([[0.28, 500]], [[0.70, 500]])
        records = [
            _lead(SERIES, event, "KXTEST-E5-T1", NEW_TS, *two_sided),
            _lead(SERIES, event, "KXTEST-E5-T2", NEW_TS, *two_sided),
            _lead(SERIES, event, "KXTEST-E5-T3", NEW_TS, [[0.28, 500]], []),  # one-sided
            _settle(SERIES, event, "KXTEST-E5-T3"),
        ]
        beliefs = {"KXTEST-E5-T1": 0.40, "KXTEST-E5-T2": 0.30}
        _n1, out = _run(tmp_path / "a", _inputs(records, beliefs=beliefs))
        assert set(out["evidence"]["candidates"]) == {"KXTEST-E5-T1", "KXTEST-E5-T2"}
        assert out["evidence"]["events"][event]["n_omega"] == 3  # T1 / T2 / none (T3)
        # the same universe declared through markets rows: the same law, the same lots
        rows = [{"ticker": f"KXTEST-E5-T{i}", "strike_type": "between", "floor_strike": i,
                 "cap_strike": i + 1, "event_ticker": event} for i in (1, 2, 3)]
        _n2, declared = _run(tmp_path / "b", _inputs(records, beliefs=beliefs, markets=rows))
        assert declared["evidence"]["events"][event]["n_omega"] == 3
        assert declared["positions"] == out["positions"]
        # rows that omit T3 make the two priced rungs exhaustive: beliefs renormalize
        # UP (0.4 -> 4/7) and the stake grows — the direction a vanished rung must not take
        _n3, exhaustive = _run(tmp_path / "c", _inputs(records, beliefs=beliefs, markets=rows[:2]))
        assert exhaustive["evidence"]["events"][event]["n_omega"] == 2
        assert exhaustive["positions"]["KXTEST-E5-T1|yes"] > out["positions"]["KXTEST-E5-T1|yes"]

    def test_an_unpriced_rung_makes_the_partition_non_exhaustive(self, tmp_path):
        # E1-T2 declines: E1's priced rungs no longer tile the line -> a none cell
        beliefs = {k: v for k, v in BELIEFS.items() if k != "KXTEST-E1-T2"}
        _node_, out = _run(tmp_path, _inputs(beliefs=beliefs))
        assert out["evidence"]["events"]["KXTEST-E1"]["n_omega"] == 2  # T1 pays / none pays
        _n2, full = _run(tmp_path / "b")
        assert full["evidence"]["events"]["KXTEST-E1"]["n_omega"] == 2  # T1 pays / T2 pays

    def test_the_evidence_is_written_as_sizing_json(self, tmp_path):
        node, out = _run(tmp_path)
        path = os.path.join(str(tmp_path), "artifacts", node.key, "sizing.json")
        assert os.path.isfile(path)
        with open(path, encoding="utf-8") as fh:
            assert json.load(fh) == out["evidence"]

    def test_the_node_agrees_with_solve_event_on_the_same_inputs(self, tmp_path):
        # The node's per-event build/extract hooks and mio.solve_event are the same
        # three steps; this pins them to one answer.
        from pyomo.environ import SolverFactory

        from pmquant.books import contract_inputs_from_book

        _node_, out = _run(tmp_path, _inputs(_records(events=("KXTEST-E1",))))
        c1 = contract_inputs_from_book("KXTEST-E1-T1", 0.40, yes_bids=[[0.28, 500]],
                                       no_bids=[[0.70, 500]], fee_rate=0.07)
        c2 = contract_inputs_from_book("KXTEST-E1-T2", 0.60, yes_bids=[[0.58, 500]],
                                       no_bids=[[0.38, 500]], fee_rate=0.07)
        inputs = mio.EventInputs(
            event_id="KXTEST-E1", contracts=[c1, c2],
            scenarios=mio.mutually_exclusive_scenarios([c1, c2], exhaustive=True),
            bankroll=BANKROLL, deployable=DEPLOY_FRAC * BANKROLL, kelly_fraction=0.5,
            series=SERIES, event_cap=DEPLOY_FRAC * BANKROLL,
        )
        highs = SolverFactory(DEFAULT_SOLVER)
        for key, value in KellyMIO._HIGHS_DETERMINISM.items():
            highs.options[key] = value
        alloc = mio.solve_event(inputs, highs)
        assert out["positions"] == {f"{c}|{s}": n for (c, s), n in alloc.positions.items()}
        assert out["outlay"] == pytest.approx(alloc.outlay)
        assert out["metrics"]["expected_log_growth"] == pytest.approx(alloc.expected_log_growth)

    def test_build_model_outside_run_refuses(self):
        with pytest.raises(RuntimeError, match="run"):
            _node().build_model({}, dict(PARAMS))

    def test_registration(self):
        from dskit.pipeline.node import DEFAULT_NODE_KINDS

        assert NODE_KINDS == {KIND: KellyMIO}
        cls, owned = DEFAULT_NODE_KINDS.get(KIND)
        assert cls is KellyMIO and owned is False
        assert KellyMIO.role == "capital"
        assert KellyMIO.outputs == ("positions", "outlay", "lots", "metrics", "evidence")
