"""`outcomes.py` — D21's bitemporal join, and the only producer of `outcome` (§5.13.2).

The module answers one question — *what actually happened to this leg, as
far as anyone knew at time T* — and answers it the same way twice. Four
properties carry the whole section, and each has its own group below:

* **The strict `>` is the anti-leak rule.** `forward_asof` matches a leg
  to the FIRST event strictly LATER than its `decided_at_ms`; an event at
  the same instant, or earlier, is something the decision could itself
  have seen. A hypothesis case over `label_asof > decided_at` proves it
  as a property rather than on three hand-picked instants.
* **A leg matches at most one event, and events are consumed in `at`
  order.** A leg with no later event is DROPPED, never matched to an
  earlier one — the second half of the same rule.
* **The join stamps `known_at_ms`, the source never does.** One instant
  per `collect`, read from the clock once by the caller, so a
  crash-replayed collect produces byte-identical payloads and
  `Ledger.append` dedups them instead of refusing a changed payload
  under a reused id (§6's rule for `cash_flow.known_at_ms`).
* **Vintage reproducibility.** `current_outcome` re-asked at an earlier
  `at_ms` reproduces exactly what was knowable then, which is what makes
  an attribution number auditable rather than merely current.

The ledger here is `test_reconcile`'s `FoldingLedger`: it chains, refuses a
float under a money name and folds into a real `SeriesState`, so an
assertion about what stands "in the fold" is an assertion about the fold.

Readings this file pins where §5.13.2 is silent or self-contradictory are
marked `READING:` in the test that carries them.
"""

import json
import os
from decimal import Decimal

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from dskit.onboarding import OnboardingRoot, run_acquisition
from dskit.production import outcomes as outcomes_module
from dskit.production.base import ProductionError, Registry, canonical_hash
from dskit.production.clock import TestClock
from dskit.production.document import ServeDocument
from dskit.production.outcomes import (
    DEFAULT_OUTCOME_LOOKBACK_MS,
    OUTCOME_ID_TAG,
    OUTCOME_SOURCE_KINDS,
    LabelOutcomes,
    OutcomeJoin,
    OutcomeSource,
    SettlementOutcomes,
    forward_asof,
    outcome_record_id,
)
from dskit.production.records import DecidedLeg, Outcome, Settlement
from dskit.production.state import SeriesState
from dskit.production.vocab import OUTCOME_KINDS, OUTCOME_SOURCES
from tests.production.test_document import minimal_document, set_path
from tests.production.test_reconcile import FoldingLedger, RELEASE_HASH, SERIES_ID

BASE_MS = 1_767_268_800_000
MINUTE_MS = 60_000

INSTRUMENT = "INS1"
OTHER_INSTRUMENT = "INS2"


# ---------------------------------------------------------------------------
# Local fakes and builders
# ---------------------------------------------------------------------------


class FakeRelease:
    """The one member the join reads off a release: its hash."""

    release_hash = RELEASE_HASH


class FakeExecutor:
    """`settlements(since_ms)` and nothing else — what `SettlementOutcomes` reads."""

    def __init__(self, settlements=()):
        self._settlements = tuple(settlements)
        self.asked = []

    def settlements(self, since_ms):
        """Record the bound it was asked for and answer every settlement."""
        self.asked.append(since_ms)
        return self._settlements


def settlement(instrument=INSTRUMENT, qty="10", payout="12", settled_ms=BASE_MS + MINUTE_MS):
    """One venue settlement of `instrument`."""
    return Settlement(
        instrument=instrument,
        outcome="yes",
        qty=Decimal(qty),
        payout=Decimal(payout),
        fee=Decimal("0"),
        settled_ms=settled_ms,
        native=None,
    )


def decided_leg(
    leg_id="leg-1",
    tick_id="tick-1",
    instrument=INSTRUMENT,
    decided_at_ms=BASE_MS,
    qty="10",
    prediction=0.6,
):
    """One `DecidedLeg` — §6's `decision.legs[]` entry joined to its tick."""
    return DecidedLeg(
        leg_id=leg_id,
        tick_id=tick_id,
        instrument=instrument,
        decided_at_ms=decided_at_ms,
        final="buy",
        client_ref=f"ref-{leg_id}",
        qty=None if qty is None else Decimal(qty),
        prediction=prediction,
        baseline=0.5,
        expected_value=0.1,
        reference_price=Decimal("1.00"),
    )


def an_outcome(
    leg_id="leg-1",
    outcome_kind="settled",
    effective_at_ms=BASE_MS + MINUTE_MS,
    known_at_ms=BASE_MS + 2 * MINUTE_MS,
    value="1.2",
    weight="10",
    terminal=True,
    source="settlement",
    supersedes=None,
):
    """One `Outcome` value object — §6's `outcome` body itself."""
    return Outcome(
        leg_id=leg_id,
        outcome_kind=outcome_kind,
        effective_at_ms=effective_at_ms,
        known_at_ms=known_at_ms,
        value=Decimal(value),
        weight=Decimal(weight),
        terminal=terminal,
        source=source,
        supersedes=supersedes,
    )


def make_ledger(clock=None):
    """A folding fake ledger over a real `SeriesState`."""
    clock = clock or TestClock(start_ms=BASE_MS)
    state = SeriesState(SERIES_ID)
    return FoldingLedger(state, clock), state, clock


def fold_decision(ledger, legs, tick_id="tick-1", observed_at_ms=BASE_MS):
    """Append the `decision` and its terminal `tick`, the way a real tick does."""
    ledger.append(
        {
            "kind": "decision",
            "id": f"decision:{tick_id}",
            "body": {
                "tick_id": tick_id,
                "decision_plan_ids": [],
                "decision_plan_digests": [],
                "legs": [leg_entry(leg) for leg in legs],
            },
        }
    )
    ledger.append(
        {
            "kind": "tick",
            "id": f"tick:{tick_id}",
            "body": {
                "tick_id": tick_id,
                "tick_at": observed_at_ms,
                "data_asof_ms": observed_at_ms - MINUTE_MS,
                "observed_at_ms": observed_at_ms,
                "status": "decided",
            },
        }
    )


def leg_entry(leg):
    """§6's `decision.legs[]` entry for one `DecidedLeg`, as `loop.py` writes it."""
    return {
        "leg_id": leg.leg_id,
        "instrument": leg.instrument,
        "prediction": leg.prediction,
        "confidence": 0.7,
        "baseline": leg.baseline,
        "expected_value": leg.expected_value,
        "reference_price": str(leg.reference_price),
        "proposal": {"qty": None if leg.qty is None else str(leg.qty), "side": leg.final},
        "findings": [],
        "final": leg.final,
        "client_ref": leg.client_ref,
    }


def fold_outcome(ledger, outcome, release_hash=RELEASE_HASH):
    """Append one `Outcome` under the id `record` would mint for it."""
    return ledger.append(
        {
            "kind": "outcome",
            "id": outcome_record_id(release_hash, outcome),
            "body": outcome.to_obj(),
        }
    )


class StubSource(OutcomeSource):
    """A source that answers a fixed tuple and records how it was called."""

    _PARAMS = ("tag",)

    def _configure(self, params):
        """Keep the declared tag; the answers are set by the test."""
        self.tag = params.get("tag")
        self.answers = ()
        self.calls = []

    def poll(self, legs, at_ms, standing):
        """Record the call and answer whatever the test staged."""
        self.calls.append((tuple(legs), at_ms, dict(standing)))
        return self.answers


def make_join(sources=None, clock=None, document=None, ledger=None, state=None):
    """An `OutcomeJoin` over a folding ledger and a real fold.

    The document declares exactly the source names it is handed, because
    that is what `compose.py` builds and what the join refuses to differ
    from.
    """
    if ledger is None:
        ledger, state, clock = make_ledger(clock)
    sources = {} if sources is None else sources
    if document is None:
        document = a_document({name: {"uses": "settlement"} for name in sources} or None)
    return (
        OutcomeJoin(
            document,
            FakeRelease(),
            ledger=ledger,
            state=state,
            clock=clock or TestClock(start_ms=BASE_MS),
            sources=sources,
        ),
        ledger,
        state,
    )


def a_document(sources=None):
    """A shadow serve document, with an `outcomes.sources` block when asked."""
    obj = minimal_document()
    if sources is not None:
        obj = set_path(obj, ("outcomes",), {"sources": sources})
    return ServeDocument.from_obj(obj)


# ---------------------------------------------------------------------------
# The module surface (§8, CLAUDE.md's `__all__` contract)
# ---------------------------------------------------------------------------


def test_the_module_exports_exactly_what_section_5_13_2_places_here():
    for name in (
        "LabelOutcomes",
        "OUTCOME_SOURCE_KINDS",
        "OutcomeJoin",
        "OutcomeSource",
        "SettlementOutcomes",
        "forward_asof",
    ):
        assert name in outcomes_module.__all__, name


def test_all_leaks_no_private_name_and_names_nothing_missing():
    assert outcomes_module.__all__
    assert not [name for name in outcomes_module.__all__ if name.startswith("_")]
    assert not [name for name in outcomes_module.__all__ if not hasattr(outcomes_module, name)]


def test_the_registry_is_the_open_doorway_for_the_outcome_source_family():
    assert isinstance(OUTCOME_SOURCE_KINDS, Registry)
    assert OUTCOME_SOURCE_KINDS.family == "outcome_source"
    assert OUTCOME_SOURCE_KINDS.abc is OutcomeSource
    assert OUTCOME_SOURCE_KINDS.resolve("settlement") is SettlementOutcomes
    assert OUTCOME_SOURCE_KINDS.resolve("label") is LabelOutcomes


def test_every_registered_source_kind_is_a_member_of_the_outcome_source_vocabulary():
    """`vocab.OUTCOME_SOURCES` closes what an `Outcome.source` may say; the
    registry is the open doorway of the classes that produce them. A kind
    whose name is not a source spelling would stamp a value the record
    refuses."""
    assert set(OUTCOME_SOURCE_KINDS.kinds()) <= set(OUTCOME_SOURCES)


def test_the_seam_refuses_instantiation_for_being_abstract():
    with pytest.raises(TypeError, match="abstract"):
        OutcomeSource()


def test_a_source_refuses_a_param_it_does_not_declare():
    with pytest.raises(ProductionError) as excinfo:
        StubSource({"tag": "a", "lookbck_ms": 1})
    assert "lookbck_ms" in str(excinfo.value)


# ---------------------------------------------------------------------------
# `forward_asof` — the ONE forward as-of rule
# ---------------------------------------------------------------------------


def key_of(item):
    """The join key of a leg or an event: both carry `instrument`."""
    return item.instrument if hasattr(item, "instrument") else item["instrument"]


def at_of(event):
    """An event's own instant."""
    return event["at"]


def event(instrument=INSTRUMENT, at=BASE_MS + MINUTE_MS, tag="e"):
    """One opaque event with a key and an instant."""
    return {"instrument": instrument, "at": at, "tag": tag}


def test_forward_asof_matches_the_first_event_strictly_after_the_decision():
    leg = decided_leg(decided_at_ms=BASE_MS)
    events = (event(at=BASE_MS + 1, tag="first"), event(at=BASE_MS + 2, tag="second"))
    assert [tag for _leg, ev in forward_asof((leg,), events, key_of, at_of)
            for tag in (ev["tag"],)] == ["first"]


def test_forward_asof_refuses_an_event_at_the_very_instant_of_the_decision():
    """The strict `>` IS the anti-leak property: an event stamped at the
    decision's own instant is one the decision could itself have seen."""
    leg = decided_leg(decided_at_ms=BASE_MS)
    assert forward_asof((leg,), (event(at=BASE_MS),), key_of, at_of) == ()


def test_a_leg_with_no_later_event_is_dropped_never_matched_to_an_earlier_one():
    leg = decided_leg(decided_at_ms=BASE_MS)
    earlier = (event(at=BASE_MS - 10), event(at=BASE_MS - 1))
    assert forward_asof((leg,), earlier, key_of, at_of) == ()


def test_a_leg_matches_at_most_one_event():
    leg = decided_leg(decided_at_ms=BASE_MS)
    events = tuple(event(at=BASE_MS + n) for n in (1, 2, 3))
    assert len(forward_asof((leg,), events, key_of, at_of)) == 1


def test_events_are_consumed_in_at_order_so_two_legs_take_two_events():
    """"events are consumed in `at` order": the earlier decision claims the
    earlier event, and the second leg gets the next one rather than the
    same one twice."""
    first = decided_leg(leg_id="leg-1", decided_at_ms=BASE_MS)
    second = decided_leg(leg_id="leg-2", decided_at_ms=BASE_MS + 5)
    events = (event(at=BASE_MS + 10, tag="a"), event(at=BASE_MS + 20, tag="b"))
    matched = forward_asof((second, first), events, key_of, at_of)
    assert {leg.leg_id: ev["tag"] for leg, ev in matched} == {"leg-1": "a", "leg-2": "b"}


def test_an_event_of_another_key_never_matches():
    leg = decided_leg(instrument=INSTRUMENT, decided_at_ms=BASE_MS)
    other = (event(instrument=OTHER_INSTRUMENT, at=BASE_MS + 10),)
    assert forward_asof((leg,), other, key_of, at_of) == ()


def test_forward_asof_is_the_one_owner_of_the_rule_both_sources_use():
    """CLAUDE.md: a function is never repeated across modules. Both sources
    reach the same module function rather than each spelling the join."""
    assert callable(outcomes_module.forward_asof)
    assert forward_asof.__module__ == "dskit.production.outcomes"


@settings(max_examples=300, deadline=None)
@given(
    decided=st.integers(min_value=0, max_value=10**12),
    offsets=st.lists(st.integers(min_value=-10**6, max_value=10**6), min_size=0, max_size=8),
)
def test_no_matched_label_is_ever_at_or_before_the_decision(decided, offsets):
    """§5.13.2 asks for this case by name: over any `label_asof` and any
    `decided_at`, a matched label is strictly later — otherwise the label
    leaked into the decision that is being scored against it."""
    leg = decided_leg(decided_at_ms=decided)
    events = tuple(event(at=decided + offset, tag=str(n)) for n, offset in enumerate(offsets))
    matched = forward_asof((leg,), events, key_of, at_of)
    assert all(at_of(ev) > leg.decided_at_ms for _leg, ev in matched)
    later = [ev for ev in events if at_of(ev) > decided]
    assert len(matched) == (1 if later else 0)
    if later:
        assert at_of(matched[0][1]) == min(at_of(ev) for ev in later)


# ---------------------------------------------------------------------------
# `SettlementOutcomes` — settled / partial / voided / corrected
# ---------------------------------------------------------------------------


def test_a_settlement_yields_the_per_unit_resolution_not_the_payout():
    """§5.13.2: `value = payout / qty` — "the per-unit resolution, so legs
    of different size are comparable". A payout would make a ten-lot look
    ten times better than a one-lot at the same price."""
    source = SettlementOutcomes({}, executor=FakeExecutor((settlement(qty="10", payout="12"),)))
    big = decided_leg(leg_id="leg-big", qty="10")
    answers = source.poll((big,), BASE_MS + 10 * MINUTE_MS, {})
    assert [outcome.value for outcome in answers] == [Decimal("12") / Decimal("10")]
    assert [outcome.weight for outcome in answers] == [Decimal("10")]

    small = decided_leg(leg_id="leg-small", qty="1")
    other = SettlementOutcomes(
        {}, executor=FakeExecutor((settlement(qty="1", payout="1.2"),))
    )
    same = other.poll((small,), BASE_MS + 10 * MINUTE_MS, {})
    assert same[0].value == answers[0].value


def test_a_settled_quantity_below_the_legs_is_partial():
    source = SettlementOutcomes({}, executor=FakeExecutor((settlement(qty="4", payout="4.8"),)))
    answers = source.poll((decided_leg(qty="10"),), BASE_MS + 10 * MINUTE_MS, {})
    assert [outcome.outcome_kind for outcome in answers] == ["partial"]
    assert answers[0].weight == Decimal("4")


def test_a_settlement_of_zero_quantity_is_voided():
    source = SettlementOutcomes({}, executor=FakeExecutor((settlement(qty="0", payout="0"),)))
    answers = source.poll((decided_leg(qty="10"),), BASE_MS + 10 * MINUTE_MS, {})
    assert [outcome.outcome_kind for outcome in answers] == ["voided"]


def test_a_settlement_that_disagrees_with_a_standing_terminal_outcome_is_corrected():
    """§5.13.2: "a settlement for a leg that already carries a terminal
    outcome with a different value is `corrected`". The join hands the
    source what already stands, because a source reads no ledger."""
    source = SettlementOutcomes({}, executor=FakeExecutor((settlement(qty="10", payout="12"),)))
    standing = {"leg-1": an_outcome(value="0.5")}
    answers = source.poll((decided_leg(qty="10"),), BASE_MS + 10 * MINUTE_MS, standing)
    assert [outcome.outcome_kind for outcome in answers] == ["corrected"]


def test_a_settlement_agreeing_with_what_stands_is_not_a_correction():
    source = SettlementOutcomes({}, executor=FakeExecutor((settlement(qty="10", payout="12"),)))
    standing = {"leg-1": an_outcome(value=str(Decimal("12") / Decimal("10")))}
    answers = source.poll((decided_leg(qty="10"),), BASE_MS + 10 * MINUTE_MS, standing)
    assert [outcome.outcome_kind for outcome in answers] == ["settled"]


def test_the_lookback_bounds_what_the_executor_is_asked_for():
    executor = FakeExecutor(())
    SettlementOutcomes({"lookback_ms": 5_000}, executor=executor).poll((), BASE_MS, {})
    assert executor.asked == [BASE_MS - 5_000]


def test_the_lookback_default_has_one_name_and_the_run_reads_it():
    executor = FakeExecutor(())
    SettlementOutcomes({}, executor=executor).poll((), BASE_MS, {})
    assert executor.asked == [BASE_MS - DEFAULT_OUTCOME_LOOKBACK_MS]
    assert isinstance(DEFAULT_OUTCOME_LOOKBACK_MS, int)


def test_a_settlement_source_stamps_the_join_supplied_instant_and_never_its_own_clock():
    """"A source reads no ledger and stamps no `known_at_ms` — the join
    does both — so it cannot back-date what it found"."""
    source = SettlementOutcomes({}, executor=FakeExecutor((settlement(),)))
    at_ms = BASE_MS + 10 * MINUTE_MS
    answers = source.poll((decided_leg(),), at_ms, {})
    assert [outcome.known_at_ms for outcome in answers] == [at_ms]
    assert [outcome.source for outcome in answers] == ["settlement"]


def test_every_settlement_outcome_is_terminal():
    source = SettlementOutcomes({}, executor=FakeExecutor((settlement(),)))
    answers = source.poll((decided_leg(),), BASE_MS + 10 * MINUTE_MS, {})
    assert all(outcome.terminal for outcome in answers)


def test_between_the_two_sources_every_outcome_kind_has_a_producer(tmp_path):
    """§5.16's closure, stated in §5.13.2 for exactly this reason: "Between
    the two, every `OUTCOME_KINDS` member has a producer"."""
    settled = SettlementOutcomes({}, executor=FakeExecutor((settlement(qty="10", payout="12"),)))
    partial = SettlementOutcomes({}, executor=FakeExecutor((settlement(qty="4", payout="5"),)))
    voided = SettlementOutcomes({}, executor=FakeExecutor((settlement(qty="0", payout="0"),)))
    leg, at_ms = decided_leg(qty="10"), BASE_MS + 10 * MINUTE_MS
    produced = {
        settled.poll((leg,), at_ms, {})[0].outcome_kind,
        partial.poll((leg,), at_ms, {})[0].outcome_kind,
        voided.poll((leg,), at_ms, {})[0].outcome_kind,
        settled.poll((leg,), at_ms, {"leg-1": an_outcome(value="0.5")})[0].outcome_kind,
        marks_source(tmp_path).poll((leg,), LABEL_MS + MINUTE_MS, {})[0].outcome_kind,
    }
    assert produced == set(OUTCOME_KINDS)


# ---------------------------------------------------------------------------
# `LabelOutcomes` — the derived label stream, read through `scan_stream`
# ---------------------------------------------------------------------------


LABEL_SOURCE = "labels"
LABEL_STREAM = "resolutions"


def label_rows(instrument=INSTRUMENT, asof="2026-01-02", value=1.5, weight=2):
    """One derived label row, as the connector serves it."""
    return [{"asof": asof, "instrument": instrument, "value": value, "weight": weight}]


def label_root(tmp_path, rows=None):
    """A real onboarding root whose `labels` source holds one `resolutions` stream."""
    data_dir = os.path.join(str(tmp_path), "label-data")
    os.makedirs(data_dir, exist_ok=True)
    with open(os.path.join(data_dir, f"{LABEL_STREAM}.jsonl"), "w", encoding="utf-8") as handle:
        for row in label_rows() if rows is None else rows:
            handle.write(json.dumps(row, sort_keys=True) + "\n")
    root = OnboardingRoot.create(os.path.join(str(tmp_path), "label-ob"))
    registry = root.registry()
    version = registry.register(
        "source_config",
        {
            "name": LABEL_SOURCE,
            "catalog_source": f"{LABEL_SOURCE}-src",
            "connector": "localfiles",
            "config": {"path": data_dir, "effective_field": "asof"},
        },
        origin="test_outcomes",
    )
    registry.transition(version, "active", origin="test_outcomes")
    run_acquisition(root, registry, LABEL_SOURCE, LABEL_STREAM, "backfill")
    return root, registry


def marks_source(tmp_path, outcome_kind="marked", rows=None, **params):
    """A `LabelOutcomes` over that root, declaring the label's own field names."""
    root, registry = label_root(tmp_path, rows)
    knobs = {
        "source": LABEL_SOURCE,
        "stream": LABEL_STREAM,
        "key_fields": ["instrument"],
        "time_field": "asof",
        "value_field": "value",
        "outcome_kind": outcome_kind,
    }
    knobs.update(params)
    return LabelOutcomes(knobs, root=root, registry=registry)


#: The label row's own instant, as `scan_stream` derives it from `asof`.
LABEL_MS = 1_767_312_000_000


def test_a_label_row_becomes_an_outcome_of_the_declared_kind(tmp_path):
    source = marks_source(tmp_path)
    leg = decided_leg(decided_at_ms=LABEL_MS - MINUTE_MS)
    answers = source.poll((leg,), LABEL_MS + MINUTE_MS, {})
    assert [outcome.outcome_kind for outcome in answers] == ["marked"]
    assert [outcome.terminal for outcome in answers] == [False]
    assert [outcome.source for outcome in answers] == ["label"]
    assert [outcome.value for outcome in answers] == [Decimal("1.5")]
    assert [outcome.effective_at_ms for outcome in answers] == [LABEL_MS]


def test_a_label_stream_may_declare_itself_terminal(tmp_path):
    source = marks_source(tmp_path, outcome_kind="settled")
    leg = decided_leg(decided_at_ms=LABEL_MS - MINUTE_MS)
    answers = source.poll((leg,), LABEL_MS + MINUTE_MS, {})
    assert [(o.outcome_kind, o.terminal) for o in answers] == [("settled", True)]


def test_a_label_weight_field_is_read_when_declared_and_defaults_to_one(tmp_path):
    weighted = marks_source(tmp_path, weight_field="weight")
    leg = decided_leg(decided_at_ms=LABEL_MS - MINUTE_MS)
    assert weighted.poll((leg,), LABEL_MS + MINUTE_MS, {})[0].weight == Decimal("2")
    plain = marks_source(tmp_path / "b")
    assert plain.poll((leg,), LABEL_MS + MINUTE_MS, {})[0].weight == Decimal(1)


def test_a_label_at_or_before_the_decision_never_reaches_the_leg(tmp_path):
    """The same strict `>` as everywhere else: reading the label stream is
    D4's "no second vendor fetch", not a licence to score a decision on
    something it could have seen."""
    source = marks_source(tmp_path)
    assert source.poll((decided_leg(decided_at_ms=LABEL_MS),), LABEL_MS + MINUTE_MS, {}) == ()


def test_a_label_source_refuses_a_source_the_registry_does_not_hold_active(tmp_path):
    root, registry = label_root(tmp_path)
    source = LabelOutcomes(
        {
            "source": "not-registered",
            "stream": LABEL_STREAM,
            "key_fields": ["instrument"],
            "time_field": "asof",
            "value_field": "value",
        },
        root=root,
        registry=registry,
    )
    with pytest.raises(ProductionError) as excinfo:
        source.poll((decided_leg(),), LABEL_MS + MINUTE_MS, {})
    assert "not-registered" in str(excinfo.value)


def test_a_label_join_key_is_the_dedup_key_with_the_time_field_projected_out(tmp_path):
    """`key_fields` is `scan_stream`'s DEDUP key, which normally carries the
    time field too; the JOIN key is what identifies the entity, the same
    projection `ServingContract` makes."""
    source = marks_source(tmp_path, key_fields=["instrument", "asof"])
    leg = decided_leg(decided_at_ms=LABEL_MS - MINUTE_MS)
    assert [o.leg_id for o in source.poll((leg,), LABEL_MS + MINUTE_MS, {})] == ["leg-1"]


def test_a_label_key_that_leaves_more_than_one_entity_field_refuses(tmp_path):
    """A leg contributes only its instrument, so a two-field entity could
    never join — and a join that never matches reads as "nothing settled"."""
    root, registry = label_root(tmp_path)
    with pytest.raises(ProductionError) as excinfo:
        LabelOutcomes(
            {
                "source": LABEL_SOURCE,
                "stream": LABEL_STREAM,
                "key_fields": ["instrument", "venue", "asof"],
                "time_field": "asof",
                "value_field": "value",
            },
            root=root,
            registry=registry,
        )
    assert "key_fields" in str(excinfo.value)


def test_a_label_source_refuses_an_outcome_kind_outside_the_two_it_admits(tmp_path):
    root, registry = label_root(tmp_path)
    with pytest.raises(ProductionError) as excinfo:
        LabelOutcomes(
            {
                "source": LABEL_SOURCE,
                "stream": LABEL_STREAM,
                "key_fields": ["instrument"],
                "time_field": "asof",
                "value_field": "value",
                "outcome_kind": "voided",
            },
            root=root,
            registry=registry,
        )
    assert "outcome_kind" in str(excinfo.value)


# ---------------------------------------------------------------------------
# `OutcomeJoin.collect` — one instant, one answer per leg, nothing written
# ---------------------------------------------------------------------------


def test_collect_reads_and_writes_nothing():
    source = StubSource({"tag": "s"})
    join, ledger, _state = make_join({"settle": source})
    ledger.calls.clear()
    join.collect(BASE_MS)
    assert [name for name, _kind in ledger.calls if name.startswith("append")] == []


def test_collect_stamps_every_answer_with_the_runs_one_instant():
    source = StubSource({"tag": "s"})
    # The source stamps its own answer as knowable the instant it happened;
    # the join overwrites that with the run's ONE instant.
    source.answers = (an_outcome(known_at_ms=BASE_MS + MINUTE_MS),)
    join, ledger, _state = make_join({"settle": source})
    fold_decision(ledger, (decided_leg(),))
    collected = join.collect(BASE_MS + 5 * MINUTE_MS)
    assert [outcome.known_at_ms for outcome in collected] == [BASE_MS + 5 * MINUTE_MS]


def test_a_crash_replayed_collect_at_the_same_instant_is_byte_identical():
    """§5.13.2: the run's ONE instant is read from the clock once by the
    CALLER, "so a crash-replayed collect produces byte-identical payloads
    and `Ledger.append` dedups them instead of refusing a changed payload
    under a reused id"."""
    source = SettlementOutcomes({}, executor=FakeExecutor((settlement(),)))
    join, ledger, _state = make_join({"settle": source})
    fold_decision(ledger, (decided_leg(),))
    at_ms = BASE_MS + 5 * MINUTE_MS
    first = join.collect(at_ms)
    # The crash falls between the collect and the record, so the replay
    # re-collects at the same instant and must produce the same bytes.
    again = join.collect(at_ms)
    assert [outcome.to_obj() for outcome in again] == [outcome.to_obj() for outcome in first]
    ids = join.record(first)
    assert join.record(again) == ids
    assert ledger.kinds().count("outcome") == 1
    assert ("append-dedup", "outcome") in ledger.calls


def test_collect_drops_what_already_stands_unsuperseded_in_the_fold():
    source = SettlementOutcomes({}, executor=FakeExecutor((settlement(),)))
    join, ledger, _state = make_join({"settle": source})
    fold_decision(ledger, (decided_leg(),))
    join.record(join.collect(BASE_MS + 5 * MINUTE_MS))
    assert join.collect(BASE_MS + 9 * MINUTE_MS) == ()


def test_collect_asks_every_source_in_declared_order_and_orders_its_answers():
    first, second = StubSource({"tag": "a"}), StubSource({"tag": "b"})
    first.answers = (an_outcome(leg_id="leg-2", source="settlement"),)
    second.answers = (an_outcome(leg_id="leg-1", source="label", terminal=False,
                                 outcome_kind="marked"),)
    join, ledger, _state = make_join({"settle": first, "labels": second})
    fold_decision(ledger, (decided_leg(leg_id="leg-1"), decided_leg(leg_id="leg-2")))
    collected = join.collect(BASE_MS + 5 * MINUTE_MS)
    assert [outcome.leg_id for outcome in collected] == ["leg-1", "leg-2"]


def test_collect_supplies_only_the_legs_decided_at_or_before_the_cut():
    source = StubSource({"tag": "s"})
    join, ledger, _state = make_join({"settle": source})
    fold_decision(ledger, (decided_leg(leg_id="leg-1"),), tick_id="t1", observed_at_ms=BASE_MS)
    fold_decision(
        ledger, (decided_leg(leg_id="leg-2"),), tick_id="t2", observed_at_ms=BASE_MS + 10_000
    )
    join.collect(BASE_MS + 5_000)
    supplied, at_ms, _standing = source.calls[-1]
    assert [leg.leg_id for leg in supplied] == ["leg-1"]
    assert at_ms == BASE_MS + 5_000


def test_collect_hands_each_source_what_already_stands_so_it_can_correct():
    source = StubSource({"tag": "s"})
    join, ledger, _state = make_join({"settle": source})
    fold_decision(ledger, (decided_leg(),))
    fold_outcome(ledger, an_outcome())
    join.collect(BASE_MS + 5 * MINUTE_MS)
    _legs, _at, standing = source.calls[-1]
    assert set(standing) == {"leg-1"}
    assert standing["leg-1"].value == Decimal("1.2")


def test_collect_refuses_an_answer_that_is_not_an_outcome():
    source = StubSource({"tag": "s"})
    source.answers = ({"leg_id": "leg-1"},)
    join, ledger, _state = make_join({"settle": source})
    fold_decision(ledger, (decided_leg(),))
    with pytest.raises(ProductionError) as excinfo:
        join.collect(BASE_MS + 5 * MINUTE_MS)
    assert "Outcome" in str(excinfo.value)


def test_collect_refuses_a_cut_that_is_not_an_instant():
    join, _ledger, _state = make_join({})
    with pytest.raises(ProductionError):
        join.collect("now")


# ---------------------------------------------------------------------------
# `OutcomeJoin.record` — the id, the barrier, and the supersede refusals
# ---------------------------------------------------------------------------


def test_record_appends_under_the_section_5_13_2_id_and_barriers_once_after_the_batch():
    join, ledger, _state = make_join({})
    first = an_outcome(leg_id="leg-1")
    second = an_outcome(leg_id="leg-2")
    ledger.calls.clear()
    ids = join.record((first, second))
    assert ids == (
        outcome_record_id(RELEASE_HASH, first),
        outcome_record_id(RELEASE_HASH, second),
    )
    writes = [name for name, _kind in ledger.calls if name != "scan"]
    assert writes == ["append", "append", "barrier"]


def test_the_record_id_is_the_tagged_hash_of_the_five_naming_fields():
    outcome = an_outcome()
    assert outcome_record_id(RELEASE_HASH, outcome) == "outcome:" + canonical_hash(
        (
            OUTCOME_ID_TAG,
            RELEASE_HASH,
            outcome.leg_id,
            outcome.source,
            outcome.effective_at_ms,
            outcome.known_at_ms,
        )
    )


def test_two_releases_never_share_an_outcome_id():
    outcome = an_outcome()
    assert outcome_record_id("a" * 64, outcome) != outcome_record_id("b" * 64, outcome)


def test_record_refuses_a_supersedes_that_names_nothing_this_series_recorded():
    join, ledger, _state = make_join({})
    with pytest.raises(ProductionError) as excinfo:
        join.record((an_outcome(outcome_kind="corrected", supersedes="outcome:" + "f" * 64),))
    assert "supersedes" in str(excinfo.value)
    assert ledger.kinds().count("outcome") == 0


def test_record_refuses_a_supersedes_that_names_a_record_of_another_kind():
    join, ledger, _state = make_join({})
    fold_decision(ledger, (decided_leg(),))
    with pytest.raises(ProductionError) as excinfo:
        join.record((an_outcome(outcome_kind="corrected", supersedes="decision:tick-1"),))
    assert "supersedes" in str(excinfo.value)


def test_a_chain_link_may_be_replaced_once_and_a_second_replacement_refuses():
    """§5.13.2: "a chain link may be replaced once"."""
    join, ledger, _state = make_join({})
    first = an_outcome(value="1.0")
    join.record((first,))
    head = outcome_record_id(RELEASE_HASH, first)
    correction = an_outcome(
        outcome_kind="corrected", value="2.0", known_at_ms=BASE_MS + 3 * MINUTE_MS, supersedes=head
    )
    join.record((correction,))
    second = an_outcome(
        outcome_kind="corrected", value="3.0", known_at_ms=BASE_MS + 4 * MINUTE_MS, supersedes=head
    )
    with pytest.raises(ProductionError) as excinfo:
        join.record((second,))
    assert "already" in str(excinfo.value)


def test_record_refuses_anything_that_is_not_an_outcome():
    join, _ledger, _state = make_join({})
    with pytest.raises(ProductionError):
        join.record(({"leg_id": "leg-1"},))


# ---------------------------------------------------------------------------
# `current_outcome` / `as_of` — vintage reproducibility (D21)
# ---------------------------------------------------------------------------


def test_current_outcome_walks_the_supersede_chain_to_its_head():
    join, ledger, _state = make_join({})
    first = an_outcome(value="1.0")
    join.record((first,))
    correction = an_outcome(
        outcome_kind="corrected",
        value="2.0",
        known_at_ms=BASE_MS + 3 * MINUTE_MS,
        supersedes=outcome_record_id(RELEASE_HASH, first),
    )
    join.record((correction,))
    assert join.current_outcome("leg-1", BASE_MS + 5 * MINUTE_MS).value == Decimal("2.0")


def test_current_outcome_answers_at_the_clocks_now_when_no_cut_is_given():
    clock = TestClock(start_ms=BASE_MS)
    join, _ledger, _state = make_join({}, clock=clock)
    join.record((an_outcome(value="1.0", known_at_ms=BASE_MS + 2 * MINUTE_MS),))
    assert join.current_outcome("leg-1") is None
    clock.advance(2 * MINUTE_MS)
    assert join.current_outcome("leg-1").value == Decimal("1.0")


def test_current_outcome_reasked_at_an_earlier_cut_reproduces_what_was_knowable_then():
    """D21's vintage reproducibility, and the reason `report.py` never reads
    a settlement directly."""
    join, ledger, _state = make_join({})
    first = an_outcome(value="1.0", known_at_ms=BASE_MS + 2 * MINUTE_MS)
    join.record((first,))
    join.record((
        an_outcome(
            outcome_kind="corrected",
            value="2.0",
            known_at_ms=BASE_MS + 3 * MINUTE_MS,
            supersedes=outcome_record_id(RELEASE_HASH, first),
        ),
    ))
    assert join.current_outcome("leg-1", BASE_MS + 2 * MINUTE_MS).value == Decimal("1.0")
    assert join.current_outcome("leg-1", BASE_MS + 3 * MINUTE_MS).value == Decimal("2.0")
    assert join.current_outcome("leg-1", BASE_MS) is None


def test_current_outcome_is_none_for_a_leg_nothing_resolved():
    join, _ledger, _state = make_join({})
    assert join.current_outcome("leg-unknown") is None


def test_as_of_answers_every_leg_at_the_same_cut():
    join, ledger, _state = make_join({})
    join.record((an_outcome(leg_id="leg-1", value="1.0"), an_outcome(leg_id="leg-2", value="2.0")))
    view = join.as_of(BASE_MS + 2 * MINUTE_MS)
    assert {leg_id: outcome.value for leg_id, outcome in view.items()} == {
        "leg-1": Decimal("1.0"),
        "leg-2": Decimal("2.0"),
    }
    with pytest.raises(TypeError):
        view["leg-3"] = None


def test_the_join_refuses_a_source_map_the_document_does_not_declare():
    """§5.16's spelling rule: the join reads `document.outcomes.sources`
    through the `ServeDocument`, and a composition that handed it a
    different map would silently serve outcomes nobody declared."""
    document = a_document({"settle": {"uses": "settlement"}})
    ledger, state, clock = make_ledger()
    with pytest.raises(ProductionError) as excinfo:
        OutcomeJoin(
            document,
            FakeRelease(),
            ledger=ledger,
            state=state,
            clock=clock,
            sources={"other": StubSource({"tag": "x"})},
        )
    assert "settle" in str(excinfo.value)


# ---------------------------------------------------------------------------
# A series outlives a release, and so must its supersede chain
# ---------------------------------------------------------------------------


class LaterRelease:
    """The same series after a new fitted run was deployed."""

    release_hash = "c" * 64


def test_the_later_release_really_is_a_different_one():
    """The two tests below are worthless if the fixtures share a hash."""
    assert LaterRelease.release_hash != FakeRelease.release_hash


def a_join_at(release, ledger, state, clock):
    """A second join over the SAME series, holding a different release."""
    return OutcomeJoin(
        a_document(None),
        release,
        ledger=ledger,
        state=state,
        clock=clock,
        sources={},
    )


def test_a_correction_supersedes_a_head_recorded_under_an_earlier_release():
    """§5.13.2's `corrected`: "a settlement for a leg that already carries a
    terminal outcome with a different value ... names it in `supersedes`".
    A correction routinely arrives days later, which is exactly when a new
    release is most likely to have been deployed in between."""
    ledger, state, clock = make_ledger()
    first, _l, _s = make_join({}, ledger=ledger, state=state, clock=clock)
    (head_id,) = first.record((an_outcome(leg_id="leg-1", value="1.2"),))
    later = a_join_at(LaterRelease(), ledger, state, clock)
    correction = an_outcome(
        leg_id="leg-1",
        outcome_kind="corrected",
        value="1.5",
        known_at_ms=BASE_MS + 3 * MINUTE_MS,
        supersedes=head_id,
    )
    later.record((correction,))
    head = later.current_outcome("leg-1", BASE_MS + 5 * MINUTE_MS)
    assert head.value == Decimal("1.5")
