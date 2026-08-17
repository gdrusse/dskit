"""The flow kinds: filter / event-bank / eligibility / banking-report.

Unit tests construct nodes directly (dict records AND MarketRecord
objects — the one accessor must serve both); the integration run proves
the ★BANKING spine (events -> event-bank -> eligibility ->
banking-report) wires end to end under the driver, and that a too-high
bar halts the report as the gate's descendant.
"""

import json
import os

import pytest

from dskit.pipeline.base import ConfigError, OutputsConfig, TimeSplitConfig
from dskit.pipeline.document import NodeSpec, PipelineDocument
from dskit.pipeline.driver import run_document
from dskit.pipeline.kinds_flow import (
    BankingReport,
    Eligibility,
    EventBank,
    Filter,
    register,
)
from dskit.pipeline.node import NodeContext, NodeKindRegistry
from dskit.pipeline.records import MarketRecord
from dskit.pipeline.synthetic_nodes import SynthClip, SynthEvents, SynthLabels

DAY = 24 * 60 * 60 * 1000
ASOF = "2026-01-01"


@pytest.fixture
def ctx(tmp_path):
    return NodeContext(name="kinds", asof=ASOF, run_dir=str(tmp_path))


def drec(instrument, contract, asof_ms, **extra):
    """A plain-dict record."""
    return {"instrument": instrument, "contract": contract, "asof_ms": asof_ms, **extra}


def mrec(instrument, contract, asof_ms, *, usable=True, mid=None, group=None):
    """A MarketRecord — the attribute-access case."""
    return MarketRecord(
        venue="synth",
        instrument=instrument,
        contract=contract,
        asof_ms=asof_ms,
        usable=usable,
        reason="ok",
        mid=mid,
        group=group,
    )


def ladder(instrument, event, n_strikes, n_leads, t0=0):
    """The shape I-224 is about: ONE event observed at ``n_leads`` lead
    times across ``n_strikes`` strike contracts — ``n_strikes * n_leads``
    records that are ONE settled event."""
    return [
        mrec(instrument, f"{event}-S{s}", t0 + lead, group=event)
        for s in range(n_strikes)
        for lead in range(n_leads)
    ]


class TestFilter:
    def test_where_clauses_all_hold_and_order_is_preserved(self, ctx):
        records = [
            drec("A", "A-0", 10, mid=0.2),
            drec("A", "A-1", 11, mid=0.5),
            drec("B", "B-0", 12, mid=0.6),
            drec("B", "B-1", 13, mid=0.9),
        ]
        node = Filter(
            "f",
            {
                "where": [
                    {"field": "mid", "op": ">", "value": 0.3},
                    {"field": "mid", "op": "<", "value": 0.8},
                ]
            },
        )
        out = node.run(ctx, {"records": records})
        assert out["records"] == [records[1], records[2]]
        assert node.validate_outputs(out) == []

    def test_every_known_op_behaves(self, ctx):
        records = [drec("A", f"A-{i}", i) for i in range(5)]
        run = lambda op, value: [
            r["asof_ms"]
            for r in Filter(
                "f", {"where": [{"field": "asof_ms", "op": op, "value": value}]}
            ).run(ctx, {"records": records})["records"]
        ]
        assert run("==", 2) == [2]
        assert run("!=", 2) == [0, 1, 3, 4]
        assert run(">", 2) == [3, 4]
        assert run("<", 2) == [0, 1]
        assert run(">=", 3) == [3, 4]
        assert run("<=", 1) == [0, 1]
        assert run("in", (1, 4)) == [1, 4]

    def test_instruments_allow_list_and_missing_instrument_drops(self, ctx):
        records = [
            drec("A", "A-0", 1),
            drec("B", "B-0", 2),
            {"contract": "X-0", "asof_ms": 3},  # no instrument: unprovable
        ]
        out = Filter("f").run(ctx, {"records": records, "instruments": ["A"]})
        assert out["records"] == [records[0]]
        # unwired allow-list: everything passes
        assert len(Filter("f").run(ctx, {"records": records})["records"]) == 3

    def test_require_usable_drops_falsy_and_missing(self, ctx):
        records = [
            drec("A", "A-0", 1, usable=True),
            drec("A", "A-1", 2, usable=False),
            drec("A", "A-2", 3, usable=0),
            drec("A", "A-3", 4),  # no usable field: cannot claim usability
        ]
        out = Filter("f", {"require_usable": True}).run(ctx, {"records": records})
        assert out["records"] == [records[0]]
        # default False: usability not consulted
        assert len(Filter("f").run(ctx, {"records": records})["records"]) == 4

    def test_missing_field_drops_the_record_even_under_not_equals(self, ctx):
        records = [drec("A", "A-0", 1, color="red"), drec("A", "A-1", 2)]
        node = Filter("f", {"where": [{"field": "color", "op": "!=", "value": "blue"}]})
        assert node.run(ctx, {"records": records})["records"] == [records[0]]

    def test_incomparable_values_drop_not_crash(self, ctx):
        records = [
            drec("A", "A-0", 1, mid="not-a-number"),
            drec("A", "A-1", 2, mid=0.5),
        ]
        out = Filter("f", {"where": [{"field": "mid", "op": ">", "value": 0.1}]}).run(
            ctx, {"records": records}
        )
        assert out["records"] == [records[1]]
        # 'in' against a non-container is a failed clause, not a crash
        out = Filter("f", {"where": [{"field": "mid", "op": "in", "value": 5}]}).run(
            ctx, {"records": records}
        )
        assert out["records"] == []

    def test_market_records_and_dicts_mix_in_one_stream(self, ctx):
        records = [
            mrec("A", "A-0", 1, mid=0.5),
            mrec("A", "A-1", 2, usable=False, mid=0.6),
            drec("A", "A-2", 3, usable=True, mid=0.7),
        ]
        node = Filter(
            "f",
            {
                "require_usable": True,
                "where": [{"field": "mid", "op": ">", "value": 0.4}],
            },
        )
        out = node.run(ctx, {"records": records})
        assert out["records"] == [records[0], records[2]]

    def test_field_absent_from_the_object_drops_it(self, ctx):
        records = [
            mrec("A", "A-0", 1, mid=0.5),  # MarketRecord has no p_true
            drec("A", "A-1", 2, mid=0.6, p_true=0.9),
        ]
        out = Filter(
            "f", {"where": [{"field": "p_true", "op": ">", "value": 0.5}]}
        ).run(ctx, {"records": records})
        assert out["records"] == [records[1]]

    def test_validate_params_shapes(self):
        assert Filter.validate_params({}) == []
        assert Filter.validate_params({"where": [], "require_usable": False}) == []
        # deferred $-references tolerated — construction re-validates the
        # materialized values
        assert (
            Filter.validate_params(
                {"where": "$other.clauses", "require_usable": "$splits.flag"}
            )
            == []
        )
        assert Filter.validate_params({"require_usable": "yes"}) != []
        assert Filter.validate_params({"where": {"field": "x"}}) != []

    def test_unknown_op_names_the_known_set(self):
        problems = Filter.validate_params(
            {"where": [{"field": "mid", "op": "~", "value": 1}]}
        )
        assert len(problems) == 1
        assert "'in'" in problems[0] and "'>='" in problems[0]

    def test_clause_shape_problems_accumulate(self):
        problems = Filter.validate_params(
            {"where": ["nope", {"field": "", "op": "==", "value": 1, "extra": 2}]}
        )
        # clause 0: not a dict; clause 1: extra key + empty field
        assert len(problems) == 3
        assert any("where[0]" in p for p in problems)
        assert any("exactly the keys" in p for p in problems)

    def test_bad_params_refused_at_construction(self):
        with pytest.raises(ConfigError, match="op must be one of"):
            Filter("f", {"where": [{"field": "mid", "op": "between", "value": 1}]})

    def test_validate_inputs(self):
        node = Filter("f")
        assert node.validate_inputs({"records": []}) == []
        assert node.validate_inputs({"records": {}}) != []
        problems = node.validate_inputs({"records": [], "instruments": "AB"})
        assert problems and "allow-list" in problems[0]

    def test_empty_records(self, ctx):
        assert Filter("f").run(ctx, {"records": []}) == {"records": []}


class TestEventBank:
    def test_settled_counts_only_contracts_present_in_outcomes(self, ctx):
        events = [
            drec("A", "A-0", 10),
            drec("A", "A-1", 11),
            drec("A", "A-2", 12),  # not settled: absent from outcomes
            drec("B", "B-0", 13),
        ]
        outcomes = {"A-0": True, "A-1": False, "B-0": True}  # settled NO counts
        node = EventBank("bank")
        out = node.run(ctx, {"events": events, "outcomes": outcomes})
        assert out["counts"] == {"A": 2, "B": 1}
        assert out["extents"] == {
            "A": {"first_ms": 10, "last_ms": 11},
            "B": {"first_ms": 13, "last_ms": 13},
        }
        assert node.validate_outputs(out) == []

    def test_count_all_needs_no_outcomes(self, ctx):
        events = [drec("A", "A-0", 10), drec("A", "A-1", 11)]
        out = EventBank("bank", {"count": "all"}).run(ctx, {"events": events})
        assert out["counts"] == {"A": 2}

    def test_strictly_before_excludes_exactly_at_cut(self, ctx):
        events = [drec("A", "A-0", 99), drec("A", "A-1", 100), drec("A", "A-2", 101)]
        out = EventBank("bank", {"count": "all", "strictly_before": 100}).run(
            ctx, {"events": events}
        )
        # only 99 is strictly below the cut; 100 (at) and 101 (past) never bank
        assert out["counts"] == {"A": 1}
        assert out["extents"] == {"A": {"first_ms": 99, "last_ms": 99}}

    def test_market_record_events(self, ctx):
        events = [mrec("A", "A-0", 10), mrec("A", "A-1", 20), mrec("B", "B-0", 30)]
        out = EventBank("bank", {"count": "all"}).run(ctx, {"events": events})
        assert out["counts"] == {"A": 2, "B": 1}
        assert out["extents"]["A"] == {"first_ms": 10, "last_ms": 20}

    def test_malformed_events_are_skipped_not_crashed_on(self, ctx):
        events = [
            drec("A", "A-0", 10),
            {"contract": "A-1", "asof_ms": 11},  # no instrument
            {"instrument": "A", "asof_ms": 12},  # no contract
            drec("A", "A-3", "2026-01-01"),  # non-int asof_ms
            drec("A", "A-4", True),  # bool is not a timestamp
        ]
        out = EventBank("bank", {"count": "all"}).run(ctx, {"events": events})
        assert out["counts"] == {"A": 1}

    # -- I-224: the counter counts EVENTS, and does so by default --------

    def test_default_counts_one_event_per_group_not_per_record(self, ctx):
        """The I-224 regression. A 10-strike ladder observed at 21 leads is
        210 records and SIX events — and the default must say six, because
        this count is what the >=50 admission bar reads."""
        events = []
        for n in range(6):
            events += ladder("A", f"A-EVT{n}", n_strikes=10, n_leads=21, t0=n * 1000)
        assert len(events) == 6 * 10 * 21
        out = EventBank("bank", {"count": "all"}).run(ctx, {"events": events})
        assert out["counts"] == {"A": 6}
        # ...and that count does NOT clear a >=50 bar it never earned.
        assert (
            Eligibility("gate", {"min_events": 50}).run(ctx, {"banked": out["counts"]})[
                "verdict"
            ]
            == "NO-GO"
        )

    def test_default_extent_spans_every_observation_not_first_sightings(self, ctx):
        """Counts collapse to events; the extent still describes the DATA.
        Six events, last observed at 5020 — an extent ending at the last
        event's FIRST sighting (5000) would understate the banked span."""
        events = []
        for n in range(6):
            events += ladder("A", f"A-EVT{n}", n_strikes=2, n_leads=21, t0=n * 1000)
        out = EventBank("bank", {"count": "all"}).run(ctx, {"events": events})
        assert out["extents"] == {"A": {"first_ms": 0, "last_ms": 5020}}

    def test_group_none_means_own_cluster_not_one_shared_bucket(self, ctx):
        """MarketRecord: ``group=None`` = "the contract is its own cluster".
        Keying on the literal None would bank ONE event for the lot — the
        opposite error, silently starving the gate."""
        events = [mrec("A", f"A-{n}", 10 + n) for n in range(4)]
        out = EventBank("bank", {"count": "all"}).run(ctx, {"events": events})
        assert out["counts"] == {"A": 4}
        # explicit "group" is just the default spelled out — same answer
        out = EventBank("bank", {"count": "all", "distinct_by": "group"}).run(
            ctx, {"events": events}
        )
        assert out["counts"] == {"A": 4}

    def test_dict_records_without_a_group_field_fall_back_to_contract(self, ctx):
        """A record with no ``group`` key at all is the same statement as
        ``group=None``: no cluster, so the contract is the event."""
        events = [drec("A", "A-0", 10), drec("A", "A-0", 11), drec("A", "A-1", 12)]
        out = EventBank("bank", {"count": "all"}).run(ctx, {"events": events})
        assert out["counts"] == {"A": 2}

    def test_distinct_by_contract_counts_tradeable_units(self, ctx):
        events = ladder("A", "A-EVT", n_strikes=3, n_leads=21)
        out = EventBank("bank", {"count": "all", "distinct_by": "contract"}).run(
            ctx, {"events": events}
        )
        assert out["counts"] == {"A": 3}

    def test_count_every_record_must_be_declared(self, ctx):
        """The old behaviour is still reachable — but only by SAYING it."""
        events = ladder("A", "A-EVT", n_strikes=3, n_leads=21)
        out = EventBank("bank", {"count": "all", "distinct_by": "record"}).run(
            ctx, {"events": events}
        )
        assert out["counts"] == {"A": 63}

    def test_dedupe_is_per_instrument(self, ctx):
        """Two venues can name the same event; they bank separately."""
        events = ladder("A", "EVT", 2, 3) + ladder("B", "EVT", 2, 3)
        out = EventBank("bank", {"count": "all"}).run(ctx, {"events": events})
        assert out["counts"] == {"A": 1, "B": 1}

    def test_validate_params(self):
        assert EventBank.validate_params({}) == []
        assert EventBank.validate_params({"count": "all", "strictly_before": 5}) == []
        for value in ("group", "contract", "record"):
            assert EventBank.validate_params({"distinct_by": value}) == []
        problems = EventBank.validate_params({"distinct_by": "event"})
        assert problems and "distinct_by" in problems[0]
        # the designed wiring: a pre-materialization splits reference
        assert (
            EventBank.validate_params({"strictly_before": "$splits.train_end_ms"}) == []
        )
        assert EventBank.validate_params({"count": "both"}) != []
        assert EventBank.validate_params({"strictly_before": "yesterday"}) != []
        assert EventBank.validate_params({"strictly_before": True}) != []
        assert EventBank.validate_params({"strictly_before": 1.5}) != []

    def test_validate_inputs_enforces_outcomes_for_settled(self):
        node = EventBank("bank")
        problems = node.validate_inputs({"events": []})
        assert problems and "outcomes" in problems[0]
        assert node.validate_inputs({"events": [], "outcomes": {}}) == []
        allmode = EventBank("bank", {"count": "all"})
        assert allmode.validate_inputs({"events": []}) == []
        assert allmode.validate_inputs({"events": "no"}) != []

    def test_empty_events(self, ctx):
        out = EventBank("bank").run(ctx, {"events": [], "outcomes": {}})
        assert out == {"counts": {}, "extents": {}}


class TestEligibility:
    def test_family_is_sorted_and_at_bar_is_in(self, ctx):
        node = Eligibility("gate", {"min_events": 50})
        out = node.run(ctx, {"banked": {"B": 50, "A": 51, "C": 49}})
        assert out == {"instruments": ["A", "B"], "verdict": "GO"}
        assert node.validate_outputs(out) == []

    def test_empty_family_is_nogo(self, ctx):
        node = Eligibility("gate", {"min_events": 50})
        nogo = {"instruments": [], "verdict": "NO-GO"}
        assert node.run(ctx, {"banked": {"A": 3}}) == nogo
        assert node.run(ctx, {"banked": {}}) == nogo

    def test_min_events_is_required_no_default(self):
        problems = Eligibility.validate_params({})
        assert problems and "required" in problems[0] and "no default" in problems[0]
        with pytest.raises(ConfigError, match="required"):
            Eligibility("gate")

    def test_min_events_shape(self):
        assert Eligibility.validate_params({"min_events": 1}) == []
        for bad in (0, -3, True, 5.0, "50"):
            assert Eligibility.validate_params({"min_events": bad}) != [], bad

    def test_validate_inputs(self):
        node = Eligibility("gate", {"min_events": 1})
        assert node.validate_inputs({"banked": {"A": 1}}) == []
        assert node.validate_inputs({"banked": [("A", 1)]}) != []
        problems = node.validate_inputs({"banked": {"A": -1, "B": "2"}})
        assert len(problems) == 2


class TestBankingReport:
    def test_artifact_content_and_summary(self, ctx, tmp_path):
        node = BankingReport("report", {"min_events": 50})
        out = node.run(
            ctx,
            {
                "banked": {"A": 51, "B": 43, "C": 7},
                "family": ["A"],
                "extents": {"A": {"first_ms": 10, "last_ms": 20}},
            },
        )
        assert out["summary"] == {"in": 1, "pending": 2}
        assert node.validate_outputs(out) == []
        assert out["path"] == os.path.join(
            str(tmp_path), "artifacts", "report", "banking.json"
        )
        with open(out["path"], encoding="utf-8") as fh:
            payload = json.load(fh)
        assert payload["min_events"] == 50
        assert payload["instruments"]["A"] == {
            "banked": 51,
            "in_family": True,
            "gap": 0,
            "first_ms": 10,
            "last_ms": 20,
        }
        assert payload["instruments"]["B"] == {
            "banked": 43,
            "in_family": False,
            "gap": 7,
        }
        assert payload["instruments"]["C"]["gap"] == 43
        assert payload["totals"] == {
            "instruments": 3,
            "in_family": 1,
            "pending": 2,
            "banked_events": 101,
        }

    def test_family_member_missing_from_banked_still_gets_a_row(self, ctx):
        out = BankingReport("report", {"min_events": 5}).run(
            ctx, {"banked": {"A": 6}, "family": ["A", "GHOST"]}
        )
        with open(out["path"], encoding="utf-8") as fh:
            payload = json.load(fh)
        assert payload["instruments"]["GHOST"] == {
            "banked": 0,
            "in_family": True,
            "gap": 5,
        }
        assert out["summary"] == {"in": 2, "pending": 0}

    def test_min_events_required(self):
        assert BankingReport.validate_params({}) != []
        assert BankingReport.validate_params({"min_events": 2}) == []
        with pytest.raises(ConfigError, match="required"):
            BankingReport("report")

    def test_validate_inputs(self):
        node = BankingReport("report", {"min_events": 1})
        assert node.validate_inputs({"banked": {}, "family": []}) == []
        assert node.validate_inputs({"banked": {}, "family": ()}) == []
        problems = node.validate_inputs({"banked": None, "family": "AB", "extents": 3})
        assert len(problems) == 3


class TestRegister:
    def test_registers_all_four_unowned(self):
        reg = register(NodeKindRegistry())
        assert {"filter", "event-bank", "eligibility", "banking-report"} <= set(
            reg.kinds()
        )
        assert reg.get("filter") == (Filter, False)
        assert reg.get("event-bank") == (EventBank, False)
        assert reg.get("eligibility") == (Eligibility, False)
        assert reg.get("banking-report") == (BankingReport, False)

    def test_idempotent_and_never_shadows(self):
        reg = NodeKindRegistry()
        reg.register("filter", SynthClip)  # someone got there first
        register(reg)
        register(reg)  # second call: no duplicate-registration raise
        assert reg.get("filter") == (SynthClip, False)  # skipped, not shadowed
        assert reg.get("event-bank") == (EventBank, False)

    def test_defaults_to_the_global_registry(self, monkeypatch):
        import dskit.pipeline.kinds_flow as kinds_flow

        private = NodeKindRegistry()
        monkeypatch.setattr(kinds_flow, "DEFAULT_NODE_KINDS", private)
        assert kinds_flow.register() is private
        assert "banking-report" in private


# ---------------------------------------------------------------------------
# integration: the ★BANKING spine under the driver
# ---------------------------------------------------------------------------


def flow_registry():
    """A PRIVATE registry: the synthetic data/labels classes registered
    individually, plus the four flow kinds."""
    registry = NodeKindRegistry()
    registry.register("synth-events", SynthEvents)
    registry.register("synth-labels", SynthLabels)
    return register(registry)


def flow_document(tmp_path, min_events):
    """events -> labels -> event-bank -> eligibility -> {filter, report}."""
    pipeline = {
        "events": NodeSpec(
            uses="synth-events",
            params={"n_events": 24, "n_instruments": 2, "seed": 7},
        ),
        "labels": NodeSpec(uses="synth-labels", inputs={"events": "$events.events"}),
        "bank": NodeSpec(
            uses="event-bank",
            inputs={"events": "$events.events", "outcomes": "$labels.outcomes"},
            params={"count": "settled", "strictly_before": "$splits.train_end_ms"},
        ),
        "family": NodeSpec(
            uses="eligibility",
            inputs={"banked": "$bank.counts"},
            params={"min_events": min_events},
        ),
        "primary": NodeSpec(
            uses="filter",
            inputs={"records": "$events.events", "instruments": "$family.instruments"},
            params={"where": [{"field": "asof_ms", "op": "<", "value": 1002 * DAY}]},
        ),
        "report": NodeSpec(
            uses="banking-report",
            inputs={
                "banked": "$bank.counts",
                "family": "$family.instruments",
                "extents": "$bank.extents",
            },
            params={"min_events": min_events},
        ),
    }
    return PipelineDocument(
        name="kinds-flow",
        pipeline=pipeline,
        splits=TimeSplitConfig(
            train_end_ms=1012 * DAY, val_end_ms=1018 * DAY, test_end_ms=1024 * DAY
        ),
        outputs=OutputsConfig(run_root=str(tmp_path)),
    )


class TestFlowIntegration:
    def test_bank_to_report_wires_end_to_end(self, tmp_path):
        result = run_document(
            flow_document(tmp_path, 10), asof=ASOF, registry=flow_registry()
        )
        assert result.state == "ran" and result.exit_code == 0
        # $splits.train_end_ms pre-materialized into strictly_before: of
        # the 24 events per instrument only the 12 strictly below the cut
        # bank (knowable-at-T1).
        assert result.outputs["bank"]["counts"] == {"SYNA": 12, "SYNB": 12}
        assert result.outputs["bank"]["extents"]["SYNA"] == {
            "first_ms": 1000 * DAY,
            "last_ms": 1011 * DAY,
        }
        assert result.outputs["family"] == {
            "instruments": ["SYNA", "SYNB"],
            "verdict": "GO",
        }
        # the filter consumed the family as its allow-list
        kept = [r["contract"] for r in result.outputs["primary"]["records"]]
        assert kept == ["SYNA-0000", "SYNA-0001", "SYNB-0000", "SYNB-0001"]
        assert result.outputs["report"]["summary"] == {"in": 2, "pending": 0}
        with open(result.outputs["report"]["path"], encoding="utf-8") as fh:
            payload = json.load(fh)
        assert payload["instruments"]["SYNA"] == {
            "banked": 12,
            "in_family": True,
            "gap": 0,
            "first_ms": 1000 * DAY,
            "last_ms": 1011 * DAY,
        }
        assert payload["totals"]["banked_events"] == 24

    def test_too_high_bar_halts_the_report(self, tmp_path):
        result = run_document(
            flow_document(tmp_path, 10_000), asof=ASOF, registry=flow_registry()
        )
        assert result.state == "halted" and result.exit_code == 3
        assert result.halted_at == "family"
        assert result.outputs["family"]["verdict"] == "NO-GO"
        assert result.node_states["family"] == "ok"  # the gate itself ran
        assert result.node_states["report"] == "halted"  # the report IS the descendant
        assert result.node_states["primary"] == "halted"
        assert result.node_states["bank"] == "ok"  # upstream untouched
        assert "report" not in result.outputs
        assert not os.path.exists(
            os.path.join(result.run_dir, "artifacts", "report", "banking.json")
        )
