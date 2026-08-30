"""The flow kinds: filter / event-bank / eligibility / banking-report,
and the relational three: concat / join / derive.

The banking three now ship from ``dskit/pipeline/kinds_banking.py``
(TODO 3e) and their behaviour is still tested here, next to the ★BANKING
spine they belong to; ``test_kinds_banking.py`` pins the SPLIT itself.

Unit tests construct nodes directly (dict records AND MarketRecord
objects — the one accessor must serve both); the integration run proves
the ★BANKING spine (events -> event-bank -> eligibility ->
banking-report) wires end to end under the driver, and that a too-high
bar halts the report as the gate's descendant.

The relational three are tested mostly through what they REFUSE. Their
value is entirely in the refusals — an untagged union, an overlapping
namespace, an unmatched row, a defaulted branch — and a refusal that
quietly stopped firing looks exactly like a healthy run, which is the
failure mode a joint two-venue document exists inside of. So
every negative case below asserts a raise, and the second integration
run proves the raise reaches the DRIVER rather than dying in a unit
test.
"""

import json
import os

import pytest

from dskit.pipeline.base import ConfigError, OutputsConfig, TimeSplitConfig
from dskit.pipeline.document import NodeSpec, PipelineDocument
from dskit.pipeline.driver import run_document
from dskit.pipeline.kinds_banking import BankingReport, Eligibility, EventBank
from dskit.pipeline.kinds_banking import register as register_banking
from dskit.pipeline.kinds_flow import (
    Concat,
    Derive,
    EventGrid,
    Filter,
    Join,
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


def mrec(
    instrument, contract, asof_ms, *, usable=True, mid=None, group=None, venue="synth"
):
    """A MarketRecord — the attribute-access case. ``venue`` is a knob
    because it is the field ``concat`` reads as provenance on an object it
    cannot stamp."""
    return MarketRecord(
        venue=venue,
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


# ---------------------------------------------------------------------------
# concat — the union
# ---------------------------------------------------------------------------


def alphaish(contract, asof_ms):
    return mrec("ALP", contract, asof_ms, venue="alpha", group="ALP-EV")


def betaish(contract, asof_ms):
    return mrec("BET", contract, asof_ms, venue="beta", group="BET-EV")


def concat_node(**params):
    base = {"shape": "records", "provenance": "venue"}
    return Concat("merge", {**base, **params})


class TestConcat:
    def test_unions_n_ports_in_sorted_port_order(self, ctx):
        node = concat_node(provenance="source")
        out = node.run(
            ctx,
            {
                "beta": [drec("B", "B-1", 2)],
                "alpha": [drec("A", "A-1", 1)],
                "gamma": [drec("C", "C-1", 3)],
            },
        )
        # sorted by PORT NAME, never by the order JSON happened to carry
        assert [r["contract"] for r in out["merged"]] == ["A-1", "B-1", "C-1"]
        assert [r["source"] for r in out["merged"]] == ["alpha", "beta", "gamma"]

    def test_mapping_rows_are_stamped_and_never_mutated(self, ctx):
        row = drec("A", "A-1", 1)
        out = concat_node(provenance="source").run(ctx, {"alpha": [row]})
        assert out["merged"][0]["source"] == "alpha"
        assert "source" not in row  # the caller's row is untouched

    def test_object_rows_prove_their_own_provenance(self, ctx):
        out = concat_node(key="contract").run(
            ctx,
            {
                "alpha": [alphaish("ALP-1", 1), alphaish("ALP-2", 2)],
                "beta": [betaish("BET-1", 3)],
            },
        )
        assert len(out["merged"]) == 3
        # the envelopes ride through UNCOPIED — the native record every
        # venue stage reads must survive the union
        assert all(isinstance(r, MarketRecord) for r in out["merged"])
        assert out["sources"]["alpha"]["rows"] == 2

    def test_a_stream_wired_into_the_wrong_port_is_refused(self, ctx):
        """The conflation no downstream number would ever reveal."""
        with pytest.raises(ValueError, match="name the port after the source"):
            concat_node().run(ctx, {"alpha": [betaish("BET-1", 1)]})

    def test_an_untaggable_row_with_no_tag_of_its_own_is_refused(self, ctx):
        with pytest.raises(ValueError, match="cannot say where it came from"):
            concat_node().run(ctx, {"alpha": ["a-bare-ticker"]})

    def test_a_row_claiming_another_source_is_never_overwritten(self, ctx):
        with pytest.raises(ValueError, match="already claims"):
            concat_node(provenance="source").run(
                ctx, {"alpha": [drec("A", "A-1", 1, source="beta")]}
            )

    def test_an_untagged_union_refuses_at_plan_time(self):
        problems = Concat.validate_params({"shape": "records"})
        assert any("provenance" in p for p in problems)

    def test_declining_the_tag_takes_a_written_reason(self):
        assert (
            Concat.validate_params(
                {
                    "shape": "records",
                    "provenance_waiver": "bare tickers carry no source",
                }
            )
            == []
        )
        assert any(
            "WRITTEN" in p
            for p in Concat.validate_params(
                {"shape": "records", "provenance_waiver": "  "}
            )
        )

    def test_declaring_both_a_tag_and_a_waiver_is_refused(self):
        problems = Concat.validate_params(
            {"shape": "records", "provenance": "venue", "provenance_waiver": "why"}
        )
        assert any("exactly one" in p for p in problems)

    def test_schema_mismatch_refuses_rather_than_filling(self, ctx):
        with pytest.raises(ValueError, match="REFUSES a schema mismatch"):
            concat_node(provenance="source").run(
                ctx,
                {
                    "alpha": [drec("A", "A-1", 1, mid=0.5)],
                    "beta": [drec("B", "B-1", 2)],  # no mid — a fill would hide it
                },
            )

    def test_a_declared_schema_is_the_reference(self, ctx):
        params = {"shape": "records", "provenance": "source"}
        rows = {"alpha": [drec("A", "A-1", 1)]}
        assert Concat(
            "merge", {**params, "schema": ["instrument", "contract", "asof_ms"]}
        ).run(ctx, rows)["merged"]
        with pytest.raises(ValueError, match="REFUSES a schema mismatch"):
            Concat("merge", {**params, "schema": ["instrument", "contract"]}).run(
                ctx, rows
            )

    def test_overlapping_namespaces_refuse_unless_declared(self, ctx):
        streams = {
            "alpha": [mrec("SHARED", "A-1", 1, venue="alpha")],
            "beta": [mrec("SHARED", "B-1", 2, venue="beta")],
        }
        with pytest.raises(ValueError, match="overlapping key"):
            concat_node(key=["instrument", "contract"]).run(ctx, streams)
        allowed = concat_node(key="instrument", allow_overlap=True).run(ctx, streams)
        assert len(allowed["merged"]) == 2

    def test_repeats_inside_one_port_are_not_an_overlap(self, ctx):
        """A ladder carries one contract at every lead — only a value
        claimed by TWO ports breaks the independence unit."""
        out = concat_node(key="instrument").run(
            ctx, {"alpha": [alphaish("ALP-1", 1), alphaish("ALP-1", 2)]}
        )
        assert len(out["merged"]) == 2

    def test_a_row_missing_a_declared_key_is_refused(self, ctx):
        with pytest.raises(ValueError, match="cannot prove it is disjoint"):
            concat_node(provenance="source", key="venue_id").run(
                ctx, {"alpha": [drec("A", "A-1", 1)]}
            )

    def test_an_empty_port_refuses_unless_declared(self, ctx):
        streams = {"alpha": [alphaish("ALP-1", 1)], "beta": []}
        with pytest.raises(ValueError, match="contributed NOTHING"):
            concat_node().run(ctx, streams)
        assert len(concat_node(allow_empty=True).run(ctx, streams)["merged"]) == 1

    def test_table_shape_unions_lookup_tables(self, ctx):
        node = Concat("fee_book", {"shape": "table"})
        out = node.run(ctx, {"alpha": {"ALP": 0.07}, "beta": {"BET": 0.07}})
        assert out["merged"] == {"ALP": 0.07, "BET": 0.07}
        assert out["sources"]["alpha"] == {"rows": 1, "distinct": {"key": 1}}

    def test_table_shape_refuses_a_key_two_ports_claim(self, ctx):
        """The disjointness assertion a joint fee book leans on."""
        with pytest.raises(ValueError, match="overlapping key"):
            Concat("fee_book", {"shape": "table"}).run(
                ctx, {"alpha": {"SAME": 0.07}, "beta": {"SAME": 0.07}}
            )

    def test_table_values_are_compared_to_each_other(self, ctx):
        """0.07 and "0.07" is the transcription mistake that happens; 0 and
        0.0 is not a mistake at all."""
        node = Concat("fee_book", {"shape": "table"})
        assert node.run(ctx, {"a": {"X": 0}, "b": {"Y": 0.07}})["merged"] == {
            "X": 0,
            "Y": 0.07,
        }
        with pytest.raises(ValueError, match="REFUSES a schema mismatch"):
            node.run(ctx, {"a": {"X": 0.07}, "b": {"Y": "0.07"}})

    def test_declared_tables_need_no_wire(self, ctx):
        node = Concat(
            "fee_book",
            {
                "shape": "table",
                "tables": {"alpha": {"ALP": 0.07}, "beta": {"B": 0.02}},
            },
        )
        assert node.run(ctx, {})["merged"] == {"ALP": 0.07, "B": 0.02}

    def test_a_port_supplied_twice_is_refused(self):
        node = Concat("fee_book", {"shape": "table", "tables": {"alpha": {"A": 0.07}}})
        assert any(
            "BOTH by wire" in p for p in node.validate_inputs({"alpha": {"A": 0.07}})
        )

    def test_shape_is_required_and_the_knobs_do_not_cross(self):
        assert any("shape is required" in p for p in Concat.validate_params({}))
        table_only = Concat.validate_params(
            {"shape": "records", "tables": {"a": {}}, "provenance": "venue"}
        )
        assert any("table" in p and "tables" in p for p in table_only)
        record_only = Concat.validate_params(
            {"shape": "table", "provenance": "venue", "key": "contract"}
        )
        assert len(record_only) == 2

    def test_unknown_knobs_are_refused_by_name(self):
        problems = Concat.validate_params({"shape": "records", "how": "outer"})
        assert any("how" in p for p in problems)

    def test_validation_refuses_a_one_shot_stream_by_name(self):
        node = concat_node()
        problems = node.validate_inputs({"alpha": (r for r in ())})
        assert any("one-shot" in p for p in problems)


# ---------------------------------------------------------------------------
# join — the lookup
# ---------------------------------------------------------------------------


def join_node(**params):
    base = {"key": "contract", "how": "strict"}
    return Join("lookup", {**base, **params})


class TestJoin:
    def test_a_scalar_table_contributes_the_port_name_as_a_field(self, ctx):
        node = join_node(tables={"settled_yes": {"A-1": True}})
        out = node.run(ctx, {"records": [drec("A", "A-1", 1)]})
        assert out["records"] == [
            {"instrument": "A", "contract": "A-1", "asof_ms": 1, "settled_yes": True}
        ]
        assert out["matched"]["ports"]["settled_yes"] == {"matched": 1, "unmatched": 0}

    def test_a_mapping_table_contributes_its_own_fields(self, ctx):
        node = join_node(
            tables={"fees": {"A-1": {"fee_rate": 0.07, "schedule": "alpha-2026"}}}
        )
        joined = node.run(ctx, {"records": [drec("A", "A-1", 1)]})["records"][0]
        assert joined["fee_rate"] == 0.07 and joined["schedule"] == "alpha-2026"

    def test_n_side_tables_align_at_once(self, ctx):
        node = join_node(
            tables={"settled_yes": {"A-1": True}, "fee_rate": {"A-1": 0.07}}
        )
        out = node.run(ctx, {"records": [drec("A", "A-1", 1)]})
        assert out["records"][0]["settled_yes"] is True
        assert out["records"][0]["fee_rate"] == 0.07

    def test_strict_raises_on_an_unmatched_row(self, ctx):
        with pytest.raises(ValueError, match="how='strict'"):
            join_node(tables={"fee_rate": {"A-1": 0.07}}).run(
                ctx, {"records": [drec("B", "B-9", 1)]}
            )

    def test_inner_drops_and_counts(self, ctx):
        node = join_node(how="inner", tables={"fee_rate": {"A-1": 0.07}})
        out = node.run(ctx, {"records": [drec("A", "A-1", 1), drec("B", "B-9", 2)]})
        assert len(out["records"]) == 1
        assert out["matched"]["dropped"] == 1
        assert out["matched"]["ports"]["fee_rate"]["unmatched"] == 1

    def test_left_takes_a_written_fill_and_applies_it(self, ctx):
        assert any(
            "unmatched_fill is required" in p
            for p in Join.validate_params({"key": "contract", "how": "left"})
        )
        node = join_node(
            how="left",
            unmatched_fill={"fee_rate": None, "why": "no schedule for this series"},
            tables={"fee_rate": {"A-1": 0.07}},
        )
        out = node.run(ctx, {"records": [drec("B", "B-9", 1)]})
        assert out["records"][0]["why"] == "no schedule for this series"

    def test_a_fill_under_strict_or_inner_is_refused(self):
        problems = Join.validate_params(
            {"key": "contract", "how": "strict", "unmatched_fill": {}}
        )
        assert any("meaningless" in p for p in problems)

    def test_fanout_is_refused_unless_declared(self, ctx):
        rows = {"records": [drec("A", "A-1", 1)]}
        table = {"legs": {"A-1": [{"leg": 1}, {"leg": 2}]}}
        with pytest.raises(ValueError, match="must be DECLARED with allow_fanout"):
            join_node(tables=table).run(ctx, rows)
        out = join_node(tables=table, allow_fanout=True).run(ctx, rows)
        assert [r["leg"] for r in out["records"]] == [1, 2]

    def test_two_tables_fanning_at_once_is_never_authorised(self, ctx):
        node = join_node(
            allow_fanout=True,
            tables={
                "left": {"A-1": [{"x": 1}, {"x": 2}]},
                "right": {"A-1": [{"y": 1}, {"y": 2}]},
            },
        )
        with pytest.raises(ValueError, match="cartesian product"):
            node.run(ctx, {"records": [drec("A", "A-1", 1)]})

    def test_two_tables_claiming_one_field_are_refused(self, ctx):
        node = join_node(tables={"a": {"A-1": {"rate": 1}}, "b": {"A-1": {"rate": 2}}})
        with pytest.raises(ValueError, match="refusing to pick one"):
            node.run(ctx, {"records": [drec("A", "A-1", 1)]})

    def test_a_table_never_overwrites_the_row_own_field(self, ctx):
        node = join_node(tables={"instrument": {"A-1": "SOMETHING-ELSE"}})
        with pytest.raises(ValueError, match="would overwrite"):
            node.run(ctx, {"records": [drec("A", "A-1", 1)]})

    def test_a_row_with_no_key_is_refused(self, ctx):
        node = join_node(key="venue", tables={"fee_rate": {"alpha": 0.07}})
        with pytest.raises(ValueError, match="carries no 'venue'"):
            node.run(ctx, {"records": [drec("A", "A-1", 1)]})

    def test_frozen_envelopes_are_refused_by_name(self, ctx):
        node = join_node(tables={"fee_rate": {"A-1": 0.07}})
        with pytest.raises(ValueError, match="drop the native record"):
            node.run(ctx, {"records": [mrec("A", "A-1", 1)]})

    def test_key_and_how_are_required(self):
        problems = Join.validate_params({})
        assert any("key is required" in p for p in problems)
        assert any("how is required" in p for p in problems)

    def test_the_stream_port_is_reserved(self):
        problems = Join.validate_params(
            {"key": "c", "how": "strict", "tables": {"records": {}}}
        )
        assert any("reserved stream port" in p for p in problems)


# ---------------------------------------------------------------------------
# derive — the fail-closed projection
# ---------------------------------------------------------------------------

#: Two venues, two schedules that carry the SAME number for different
#: reasons — the case a default silently gets wrong.
FEE_CASES = [
    {
        "when": [{"field": "venue", "op": "==", "value": "alpha"}],
        "value": {"rate": 0.07, "schedule": "alpha-quadratic"},
    },
    {
        "when": [{"field": "venue", "op": "==", "value": "beta"}],
        "value": {"rate": 0.07, "schedule": "beta-crypto"},
    },
]


class TestDerive:
    def test_first_matching_case_wins_and_branches_are_counted(self, ctx):
        node = Derive("fees", {"field": "fee", "cases": FEE_CASES})
        out = node.run(
            ctx,
            {
                "records": [
                    {"venue": "alpha"},
                    {"venue": "beta"},
                    {"venue": "alpha"},
                ]
            },
        )
        assert [r["fee"]["schedule"] for r in out["records"]] == [
            "alpha-quadratic",
            "beta-crypto",
            "alpha-quadratic",
        ]
        assert out["branches"] == [2, 1]

    def test_each_row_gets_its_own_copy_of_a_container_value(self, ctx):
        node = Derive("fees", {"field": "fee", "cases": FEE_CASES})
        out = node.run(ctx, {"records": [{"venue": "alpha"}, {"venue": "alpha"}]})
        assert out["records"][0]["fee"] is not out["records"][1]["fee"]

    def test_an_unmatched_row_raises_with_no_default(self, ctx):
        node = Derive("fees", {"field": "fee", "cases": FEE_CASES})
        with pytest.raises(ValueError, match="FAIL-CLOSED"):
            node.run(ctx, {"records": [{"venue": "someothervenue"}]})

    def test_a_missing_field_never_falls_through_to_a_branch(self, ctx):
        node = Derive("fees", {"field": "fee", "cases": FEE_CASES})
        with pytest.raises(ValueError, match="<missing>"):
            node.run(ctx, {"records": [{"instrument": "A"}]})

    def test_an_explicit_catch_all_is_the_only_default(self, ctx):
        node = Derive(
            "fees",
            {"field": "fee", "cases": [*FEE_CASES, {"when": [], "value": "unpriced"}]},
        )
        out = node.run(ctx, {"records": [{"venue": "someothervenue"}]})
        assert out["records"][0]["fee"] == "unpriced"

    def test_a_catch_all_that_is_not_last_refuses_to_validate(self):
        problems = Derive.validate_params(
            {"field": "fee", "cases": [{"when": [], "value": 1}, *FEE_CASES]}
        )
        assert any("default in disguise" in p for p in problems)

    def test_an_existing_field_is_never_overwritten_silently(self, ctx):
        rows = {"records": [{"venue": "alpha", "fee": "already here"}]}
        with pytest.raises(ValueError, match="never overwrites"):
            Derive("fees", {"field": "fee", "cases": FEE_CASES}).run(ctx, rows)
        node = Derive("fees", {"field": "fee", "cases": FEE_CASES, "overwrite": True})
        assert (
            node.run(ctx, rows)["records"][0]["fee"]["schedule"] == "alpha-quadratic"
        )

    def test_frozen_envelopes_are_refused_by_name(self, ctx):
        node = Derive("fees", {"field": "fee", "cases": [{"when": [], "value": 1}]})
        with pytest.raises(ValueError, match="drop the native record"):
            node.run(ctx, {"records": [mrec("A", "A-1", 1)]})

    def test_field_and_cases_are_required(self):
        problems = Derive.validate_params({})
        assert any("field is required" in p for p in problems)
        assert any("cases is required" in p for p in problems)

    def test_a_malformed_case_is_named(self):
        problems = Derive.validate_params(
            {"field": "fee", "cases": [{"when": [], "value": 1, "otherwise": 2}]}
        )
        assert any("cases[0]" in p for p in problems)


class TestEventGrid:
    def test_keeps_declared_clock_instants_and_preserves_order(self, ctx):
        records = [
            drec("A", "A-0", 1000),
            mrec("A", "A-1", 1500),
            drec("B", "B-0", 2000),
            mrec("B", "B-1", 2500),
        ]
        whole = EventGrid(
            "grid", {"period_ms": 1000, "offset_ms": 0}
        ).run(ctx, {"records": records})
        half = EventGrid(
            "grid", {"period_ms": 1000, "offset_ms": 500}
        ).run(ctx, {"records": records})
        assert whole["records"] == [records[0], records[2]]
        assert half["records"] == [records[1], records[3]]

    def test_missing_or_non_integer_instants_drop(self, ctx):
        records = [
            {"asof_ms": 0},
            {"asof_ms": True},
            {"asof_ms": 1000.0},
            {"instrument": "missing"},
            {"asof_ms": 2000},
        ]
        out = EventGrid(
            "grid", {"period_ms": 1000, "offset_ms": 0}
        ).run(ctx, {"records": records})
        assert out["records"] == [records[0], records[4]]

    @pytest.mark.parametrize("params", [
        {},
        {"period_ms": 0, "offset_ms": 0},
        {"period_ms": True, "offset_ms": 0},
        {"period_ms": 1000, "offset_ms": -1},
        {"period_ms": 1000, "offset_ms": 1000},
        {"period_ms": 1000, "offset_ms": True},
        {"period_ms": 1000, "offset_ms": 0, "extra": 1},
    ])
    def test_invalid_params_are_refused(self, params):
        assert EventGrid.validate_params(params)
        with pytest.raises(ConfigError):
            EventGrid("grid", params)

    def test_node_references_defer_until_materialized(self):
        assert EventGrid.validate_params({
            "period_ms": "$clock.period",
            "offset_ms": "$clock.offset",
        }) == []

    def test_inputs_require_only_a_record_list(self):
        node = EventGrid("grid", {"period_ms": 1000, "offset_ms": 0})
        assert node.validate_inputs({"records": []}) == []
        assert node.validate_inputs({}) != []
        assert node.validate_inputs({"records": {}, "extra": []}) != []


class TestRegister:
    def test_registers_all_five_unowned(self):
        reg = register(NodeKindRegistry())
        assert {"filter", "concat", "join", "derive", "event-grid"} <= set(
            reg.kinds()
        )
        assert reg.get("filter") == (Filter, False)
        assert reg.get("concat") == (Concat, False)
        assert reg.get("join") == (Join, False)
        assert reg.get("derive") == (Derive, False)
        assert reg.get("event-grid") == (EventGrid, False)

    def test_idempotent_and_never_shadows(self):
        reg = NodeKindRegistry()
        reg.register("filter", SynthClip)  # someone got there first
        register(reg)
        register(reg)  # second call: no duplicate-registration raise
        assert reg.get("filter") == (SynthClip, False)  # skipped, not shadowed
        assert reg.get("concat") == (Concat, False)

    def test_defaults_to_the_global_registry(self, monkeypatch):
        import dskit.pipeline.kinds_flow as kinds_flow

        private = NodeKindRegistry()
        monkeypatch.setattr(kinds_flow, "DEFAULT_NODE_KINDS", private)
        assert kinds_flow.register() is private
        assert "derive" in private


# ---------------------------------------------------------------------------
# integration: the ★BANKING spine under the driver
# ---------------------------------------------------------------------------


def flow_registry():
    """A PRIVATE registry: the synthetic data/labels classes registered
    individually, plus the flow kinds and the banking chain — the spine
    spans both modules, so both register() calls are needed."""
    registry = NodeKindRegistry()
    registry.register("synth-events", SynthEvents)
    registry.register("synth-labels", SynthLabels)
    return register_banking(register(registry))


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


# ---------------------------------------------------------------------------
# integration: two sources, ONE union, under the driver
# ---------------------------------------------------------------------------


def union_document(tmp_path, *, collide=False):
    """The joint-document shape in miniature: one synthetic generator
    split into two DISJOINT streams that stand in for two venues, unioned
    by ``concat``, priced per source by ``derive``, and banked ONCE.

    ``collide=True`` points both slices at the same instrument, which is
    the namespace collision a joint document must never be able to make
    quietly — the union refuses and the DRIVER reports the error.
    """
    left = "SYNA"
    right = left if collide else "SYNB"
    pipeline = {
        "events": NodeSpec(
            uses="synth-events", params={"n_events": 24, "n_instruments": 2, "seed": 7}
        ),
        "labels": NodeSpec(uses="synth-labels", inputs={"events": "$events.events"}),
        "venue_a": NodeSpec(
            uses="filter",
            inputs={"records": "$events.events"},
            params={"where": [{"field": "instrument", "op": "==", "value": left}]},
        ),
        "venue_b": NodeSpec(
            uses="filter",
            inputs={"records": "$events.events"},
            params={"where": [{"field": "instrument", "op": "==", "value": right}]},
        ),
        "both": NodeSpec(
            uses="concat",
            inputs={"venue_a": "$venue_a.records", "venue_b": "$venue_b.records"},
            params={
                "shape": "records",
                "provenance": "source",
                "key": ["instrument", "contract"],
                "allow_overlap": False,
                "allow_empty": False,
            },
        ),
        "priced": NodeSpec(
            uses="derive",
            inputs={"records": "$both.merged"},
            params={
                "field": "fee_schedule",
                "cases": [
                    {
                        "when": [{"field": "source", "op": "==", "value": "venue_a"}],
                        "value": "schedule-a",
                    },
                    {
                        "when": [{"field": "source", "op": "==", "value": "venue_b"}],
                        "value": "schedule-b",
                    },
                ],
            },
        ),
        "bank": NodeSpec(
            uses="event-bank",
            inputs={"events": "$priced.records", "outcomes": "$labels.outcomes"},
            params={"count": "settled", "distinct_by": "contract"},
        ),
        "family": NodeSpec(
            uses="eligibility",
            inputs={"banked": "$bank.counts"},
            params={"min_events": 10},
        ),
    }
    return PipelineDocument(
        name="kinds-union",
        pipeline=pipeline,
        splits=TimeSplitConfig(
            train_end_ms=1012 * DAY, val_end_ms=1018 * DAY, test_end_ms=1024 * DAY
        ),
        outputs=OutputsConfig(run_root=str(tmp_path)),
    )


class TestUnionIntegration:
    def test_two_sources_bank_once_against_one_family(self, tmp_path):
        result = run_document(
            union_document(tmp_path), asof=ASOF, registry=flow_registry()
        )
        assert result.state == "ran" and result.exit_code == 0
        # ONE bank over the union, not two banks summed after the fact
        assert result.outputs["bank"]["counts"] == {"SYNA": 24, "SYNB": 24}
        assert result.outputs["family"]["instruments"] == ["SYNA", "SYNB"]
        sources = result.outputs["both"]["sources"]
        assert sources["venue_a"]["rows"] == sources["venue_b"]["rows"] == 24
        assert sources["venue_a"]["distinct"] == {"instrument": 1, "contract": 24}
        # every row carries which source it came from, and the per-source
        # schedule that followed from it — nothing was defaulted
        assert result.outputs["priced"]["branches"] == [24, 24]
        pairs = {
            (r["source"], r["fee_schedule"])
            for r in result.outputs["priced"]["records"]
        }
        assert pairs == {("venue_a", "schedule-a"), ("venue_b", "schedule-b")}

    def test_a_namespace_collision_stops_the_run(self, tmp_path):
        result = run_document(
            union_document(tmp_path, collide=True), asof=ASOF, registry=flow_registry()
        )
        assert result.state != "ran"
        assert result.exit_code == 1
        assert result.node_states["both"] == "error"
        assert "overlapping key" in (result.error or "")
