"""`records.py` — the value objects every other module passes around (§5.4).

Every record here is frozen, JSON-round-trippable, default-deny on
`from_obj`, and holds money as `Decimal` — which is why a guard, a permit
and a ledger row can all name the same quantity and mean it. The digest
tests are the load-bearing half: `intent_digest`, `decision_plan_digest`,
`requirement_digest`, `reduction_intent_digest` and
`AccountState.risk_digest()` are what a permit binds, so a recipe that
silently includes or drops a field is a permit that authorises something
other than what was planned.

Expected digests are computed here with `hashlib` and `json.dumps` rather
than by calling `base.canonical_hash` — an assertion sourced from its
subject asserts nothing (CLAUDE.md).

Scope: only what §8 places in `records.py`. `LegBindings`, `LegEvaluation`
and `LegResult` belong to `leg.py`, `StateView`/`TickState` to `state.py`,
`ReadinessResult`/`readiness_digest` to `readiness.py`; their field lists
are pinned by those modules' own tests.
"""

import dataclasses
import hashlib
import json
from decimal import Decimal

import pytest

from dskit.production import records
from dskit.production.base import ProductionError
from dskit.production.vocab import (
    FILL_STATUSES,
    LEG_LATENCY_BUCKETS,
    MONEY_FIELDS,
    SIDES,
    TICK_PHASES,
)

MS = 1_757_030_400_000
DIGEST_A = "a" * 64
DIGEST_B = "b" * 64
DIGEST_C = "c" * 64
RELEASE = "d" * 64


def _canonical_hash(obj):
    """The §5.0 recipe, restated: Decimal as str, tuple as list, sorted keys."""

    def plain(value):
        if isinstance(value, Decimal):
            return str(value)
        if isinstance(value, (tuple, list)):
            return [plain(v) for v in value]
        if isinstance(value, dict):
            return {k: plain(v) for k, v in value.items()}
        return value

    text = json.dumps(
        plain(obj),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )
    return hashlib.sha256(text.encode("ascii")).hexdigest()


# ---------------------------------------------------------------------------
# One sample per §5.4 value object — the table the generic tests walk
# ---------------------------------------------------------------------------

SCOPE = records.ExecutionScope(venue="paper-venue", account="acct-1")
QUOTE = records.Quote(
    instrument="INS1",
    bid=Decimal("0.40"),
    ask=Decimal("0.42"),
    mid=Decimal("0.41"),
    asof_ms=MS,
)
QUOTE_SET = records.QuoteSet(quotes=(QUOTE,), quote_digest=DIGEST_A, min_asof_ms=MS)
CANDIDATE = records.Candidate(id="cand-1", instrument="INS1", scope_keys=("INS1",))
PROPOSAL = records.Proposal(
    id="cand-1",
    instrument="INS1",
    side="buy",
    qty=Decimal("10"),
    notional=Decimal("4.10"),
    limit=Decimal("0.41"),
    tif="ioc",
    expires_ms=MS + 5_000,
    reference_price=Decimal("0.41"),
    exposure=Decimal("4.10"),
    direction="long",
    confidence=0.61,
    prediction=0.58,
    baseline=0.50,
    expected_value=0.03,
    inputs_asof_ms=MS,
    inputs_digest=DIGEST_A,
    coverage_digest=DIGEST_B,
    quote_asof_ms=MS,
    quote_digest=DIGEST_C,
    extra={},
)
FINDING = records.Finding(
    guard="notional_limit",
    measure="notional",
    value=Decimal("4.10"),
    bound=Decimal("25.00"),
    window="session",
    scope_key="INS1",
    verdict="allow",
    reason="notional 4.10 within bound 25.00",
)
GATE_RESULT = records.GateResult(
    gate="watermark_age", passed=True, reason="", at_ms=MS
)
SCOPE_VERDICT = records.ScopeVerdict(allowed=True, scope_key="INS1", reason="")
RISK_VERSION = records.RiskVersion(
    economic_seq=41, executor_token="etok-7", accounting_tokens=("atok-3",)
)
REQUIREMENT = records.EvidenceRequirement(
    measure="pnl",
    window_kind="duration",
    window_arg=86_400_000,
    scope_key="INS1",
    window_start_ms=MS - 86_400_000,
    window_end_ms=MS,
    baseline_at_ms=MS - 86_400_000,
    include_working=True,
)
EVIDENCE = records.MeasureEvidence(
    requirement_digest=REQUIREMENT.requirement_digest,
    value=Decimal("-12.50"),
    sample_count=37,
    window_start_ms=MS - 86_400_000,
    window_end_ms=MS,
    scope_key="INS1",
    effective_at_ms=MS,
    known_at_ms=MS,
    source_digests={"fills": DIGEST_A},
)
BALANCE = records.Balance(
    currency="USD", total=Decimal("1000.00"), available=Decimal("900.00"), native={}
)
POSITION = records.Position(
    instrument="INS1",
    qty=Decimal("5"),
    avg_cost=Decimal("0.39"),
    source="derived",
    native={},
)
ACK = records.Ack(
    client_ref="cref-1",
    venue_ref="vref-1",
    status="filled",
    ts_ms=MS,
    filled_qty=Decimal("10"),
    avg_price=Decimal("0.41"),
    fee=Decimal("0.02"),
    reason="",
    native={},
)
ORDER_STATE = records.OrderState(
    client_ref="cref-1",
    venue_ref="vref-1",
    status="partial",
    ts_ms=MS,
    filled_qty=Decimal("4"),
    avg_price=Decimal("0.41"),
    fee=Decimal("0.01"),
    reason="",
    native={},
    instrument="INS1",
    side="buy",
    qty=Decimal("10"),
    remaining_qty=Decimal("6"),
    limit=Decimal("0.41"),
    tif="gtc",
    created_ms=MS,
    updated_ms=MS + 10,
)
ACCOUNT_STATE = records.AccountState(
    risk_version=RISK_VERSION,
    asof_ms=MS,
    evidence_digest=DIGEST_A,
    balances=(BALANCE,),
    positions=(POSITION,),
    working=(ORDER_STATE,),
    measure_evidence={REQUIREMENT.requirement_digest: {"INS1": EVIDENCE}},
    source_digests={"fills": DIGEST_A},
)
WATERMARK_ONE = records.InputWatermark(
    key="INS1", latest_asof_ms=MS - 1_000, source_digest=DIGEST_A
)
WATERMARK_TWO = records.InputWatermark(
    key="INS2", latest_asof_ms=MS, source_digest=DIGEST_B
)
ENTRY_BATCH = records.EntryBatch(
    outputs={"rows": []},
    watermarks_by_key={"INS1": WATERMARK_ONE, "INS2": WATERMARK_TWO},
    required_keys_digest=DIGEST_A,
    coverage_digest=DIGEST_B,
    data_asof_ms=MS - 1_000,
    inputs_digest=DIGEST_C,
    source_config_hash=DIGEST_A,
)
PROVENANCE = records.Provenance(
    inputs_asof_ms=MS,
    inputs_digest=DIGEST_A,
    coverage_digest=DIGEST_B,
    quote_asof_ms=MS,
    quote_digest=DIGEST_C,
)
DECISION_PLAN = records.DecisionPlan(
    plan_id="plan-1",
    inputs_asof_ms=MS,
    inputs_digest=DIGEST_A,
    coverage_digest=DIGEST_B,
    quote_asof_ms=MS,
    quote_digest=DIGEST_C,
    evidence_asof_ms=MS,
    evidence_digest=DIGEST_A,
    provenance_digests={"entry": DIGEST_A, "head": DIGEST_B, "candidate": "cand-1"},
    original=PROPOSAL,
    final=PROPOSAL,
    findings=(FINDING,),
    gate_results=(GATE_RESULT,),
    scope_verdict=SCOPE_VERDICT,
    risk_effect="increase",
    risk_version=RISK_VERSION,
    risk_state_digest=DIGEST_B,
    result="submit",
)
INTENT = records.Intent(
    client_ref="cref-1",
    decision_plan_id="plan-1",
    decision_plan_digest=DECISION_PLAN.decision_plan_digest(),
    proposal=PROPOSAL,
    created_ms=MS,
    authority_id="auth-1",
    release_hash=RELEASE,
    inputs_asof_ms=MS,
    inputs_digest=DIGEST_A,
    coverage_digest=DIGEST_B,
    quote_asof_ms=MS,
    quote_digest=DIGEST_C,
    evidence_asof_ms=MS,
    evidence_digest=DIGEST_A,
    risk_version=RISK_VERSION,
    risk_state_digest=DIGEST_B,
)
REDUCTION_INTENT = records.ReductionIntent(
    release_hash=RELEASE,
    request_id="req-1",
    index=0,
    candidate=CANDIDATE,
    proposal=PROPOSAL,
    risk_state_digest=DIGEST_B,
    expires_ms=MS + 600_000,
)
REDUCTION_PLAN = records.ReductionPlan(
    release_hash=RELEASE,
    risk_state_digest=DIGEST_B,
    intents=(REDUCTION_INTENT,),
    reduction_intent_digests=(REDUCTION_INTENT.reduction_intent_digest(),),
    expires_ms=MS + 600_000,
)
REDUCTION_AUTHORIZATION = records.ReductionAuthorization(
    authority_id="auth-2",
    release_hash=RELEASE,
    request_id="req-1",
    reduction_intent_digests=(REDUCTION_INTENT.reduction_intent_digest(),),
    expires_ms=MS + 600_000,
)
PERMIT = records.Permit(
    plan_id="plan-1",
    decision_plan_digest=DECISION_PLAN.decision_plan_digest(),
    client_ref="cref-1",
    valid_until_ms=MS + 30_000,
)
SIMULATED_PERMIT = records.SimulatedPermit(
    plan_id="plan-1",
    decision_plan_digest=DECISION_PLAN.decision_plan_digest(),
    client_ref="cref-1",
    valid_until_ms=MS + 30_000,
)
ACT_PERMIT = records.ActPermit(
    plan_id="plan-1",
    decision_plan_digest=DECISION_PLAN.decision_plan_digest(),
    client_ref="cref-1",
    valid_until_ms=MS + 30_000,
    authority_id="auth-1",
    release_hash=RELEASE,
    intent_digest=INTENT.intent_digest(),
    instrument="INS1",
    risk_effect="increase",
    inputs_asof_ms=MS,
    inputs_digest=DIGEST_A,
    coverage_digest=DIGEST_B,
    quote_asof_ms=MS,
    quote_digest=DIGEST_C,
    evidence_asof_ms=MS,
    evidence_digest=DIGEST_A,
    authority_scope_digest=DIGEST_C,
    reduction_right_digest=None,
    risk_version=RISK_VERSION,
    risk_state_digest=DIGEST_B,
    readiness_digest=DIGEST_A,
    readiness_until_ms=MS + 3_600_000,
    lease_scope=SCOPE,
    fencing_token=17,
    safety_epoch_digest=DIGEST_C,
    checked_at_ms=MS,
)
FEED_BLOCK = {
    "status": "live",
    "acq_id": "acq-9",
    "records_added": 12,
    "source_config_hash": DIGEST_A,
    "required_keys_digest": DIGEST_A,
    "watermarks_by_key": {"INS1": MS - 1_000, "INS2": MS},
    "coverage_digest": DIGEST_B,
}
TICK_RESULT = records.TickResult(
    tick_id="tick-1",
    status="decided",
    data_asof_ms=MS - 1_000,
    coverage_digest=DIGEST_B,
    inputs_digest=DIGEST_C,
    decision_plan_ids=("plan-1",),
    legs=({"leg_id": "leg-1", "result": "submitted"},),
    findings=(FINDING,),
    observed_at_ms=MS + 40,
    nav=Decimal("1004.10"),
    latency_ms={phase: 1 for phase in TICK_PHASES},
    leg_latency_ms={bucket: 2 for bucket in LEG_LATENCY_BUCKETS},
    refusal_reason="",
    error=None,
    feed=FEED_BLOCK,
)

#: Every §5.4 value object, with a valid instance. The generic tests below
#: walk this table, so a record added without a sample is a record whose
#: freezing, round trip and default-deny nobody checked.
SAMPLES = {
    "ExecutionScope": SCOPE,
    "Quote": QUOTE,
    "QuoteSet": QUOTE_SET,
    "Candidate": CANDIDATE,
    "Proposal": PROPOSAL,
    "Finding": FINDING,
    "GateResult": GATE_RESULT,
    "ScopeVerdict": SCOPE_VERDICT,
    "RiskVersion": RISK_VERSION,
    "EvidenceRequirement": REQUIREMENT,
    "MeasureEvidence": EVIDENCE,
    "Balance": BALANCE,
    "Position": POSITION,
    "Ack": ACK,
    "OrderState": ORDER_STATE,
    "AccountState": ACCOUNT_STATE,
    "InputWatermark": WATERMARK_ONE,
    "EntryBatch": ENTRY_BATCH,
    "Provenance": PROVENANCE,
    "DecisionPlan": DECISION_PLAN,
    "Intent": INTENT,
    "ReductionIntent": REDUCTION_INTENT,
    "ReductionPlan": REDUCTION_PLAN,
    "ReductionAuthorization": REDUCTION_AUTHORIZATION,
    "Permit": PERMIT,
    "SimulatedPermit": SIMULATED_PERMIT,
    "ActPermit": ACT_PERMIT,
    "TickResult": TICK_RESULT,
    "Fill": records.Fill(
        fill_id="fill-1",
        venue_ref="vref-1",
        client_ref="cref-1",
        instrument="INS1",
        side="buy",
        qty=Decimal("4"),
        price=Decimal("0.41"),
        fee=Decimal("0.01"),
        fee_currency="USD",
        liquidity="taker",
        status="final",
        ts_ms=MS,
        native={},
    ),
    "Settlement": records.Settlement(
        instrument="INS1",
        outcome="yes",
        qty=Decimal("10"),
        payout=Decimal("10.00"),
        fee=Decimal("0.05"),
        settled_ms=MS,
        native={},
    ),
    "Alert": records.Alert(
        fingerprint=DIGEST_A,
        severity="warning",
        status="firing",
        summary="feed degraded",
        source="feed",
        tick_id="tick-1",
        at_ms=MS,
        labels={"scope": "INS1"},
    ),
    "Verdict": records.Verdict(
        status="ok",
        statistic=0.02,
        threshold=0.25,
        n_ref=500,
        n_cur=120,
        window="count:120",
        slice="all",
        provisional=False,
    ),
    "FeedResult": records.FeedResult(
        status="live",
        acq_id="acq-9",
        records_added=12,
        source_config_hash=DIGEST_A,
        at_ms=MS,
    ),
    "FeedAge": records.FeedAge(key="INS1", age_ms=1_000, watermark_ms=MS - 1_000),
    "PolicyRequest": records.PolicyRequest(
        operation="submit",
        risk_effect="increase",
        rung="paper",
        breaker="active",
        health="ready",
        readiness="go",
        authority="ordinary",
        origin="model",
        pending_control=False,
    ),
    "TickStart": records.TickStart(
        tick_id="tick-1", tick_at_ms=MS, release_hash=RELEASE
    ),
}

RECORD_NAMES = tuple(sorted(SAMPLES))


def _decimal_field(sample):
    """The name of one `Decimal` field of ``sample``, or None."""
    for field in dataclasses.fields(sample):
        if isinstance(getattr(sample, field.name), Decimal):
            return field.name
    return None


# ---------------------------------------------------------------------------
# The contract every value object owes (§5.4 preamble)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", RECORD_NAMES)
def test_every_record_is_a_frozen_dataclass(name):
    """A guard receives records and must not be able to edit what it
    judges (§5.15, encapsulation)."""
    sample = SAMPLES[name]
    assert dataclasses.is_dataclass(sample)
    field = dataclasses.fields(sample)[0].name
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(sample, field, "mutated")


@pytest.mark.parametrize("name", RECORD_NAMES)
def test_every_record_round_trips_through_to_obj_and_from_obj(name):
    sample = SAMPLES[name]
    rebuilt = type(sample).from_obj(sample.to_obj())
    assert rebuilt == sample


@pytest.mark.parametrize("name", RECORD_NAMES)
def test_every_to_obj_is_json_ready(name):
    """`to_obj()` is what a ledger row serializes: it must survive
    `json.dumps` with no custom encoder, which is why money is a string."""
    text = json.dumps(SAMPLES[name].to_obj(), sort_keys=True)
    assert isinstance(text, str)


@pytest.mark.parametrize("name", RECORD_NAMES)
def test_every_to_obj_emits_exactly_the_declared_fields(name):
    sample = SAMPLES[name]
    assert set(sample.to_obj()) == {f.name for f in dataclasses.fields(sample)}


@pytest.mark.parametrize("name", RECORD_NAMES)
def test_from_obj_is_default_deny(name):
    """A typo'd key is an error, not a silent default (CLAUDE.md)."""
    sample = SAMPLES[name]
    obj = dict(sample.to_obj(), surprise_knob=1)
    with pytest.raises(ProductionError):
        type(sample).from_obj(obj)


@pytest.mark.parametrize("name", RECORD_NAMES)
def test_from_obj_refuses_a_non_finite_number(name):
    sample = SAMPLES[name]
    field = _decimal_field(sample)
    if field is None:
        pytest.skip(f"{name} holds no Decimal field")
    for bad in ("NaN", "Infinity", "-Infinity"):
        with pytest.raises(ProductionError):
            type(sample).from_obj(dict(sample.to_obj(), **{field: bad}))


@pytest.mark.parametrize("name", RECORD_NAMES)
def test_money_fields_are_decimal_and_serialize_as_strings(name):
    """Money never touches float (§5.4). `Decimal("1.50")` must reach JSON
    as `"1.50"` — a float would round it and a bare number would invite a
    reader to parse it as one."""
    sample = SAMPLES[name]
    field = _decimal_field(sample)
    if field is None:
        pytest.skip(f"{name} holds no Decimal field")
    assert isinstance(sample.to_obj()[field], str)


@pytest.mark.parametrize("name", RECORD_NAMES)
def test_a_float_where_money_belongs_refuses(name):
    sample = SAMPLES[name]
    field = _decimal_field(sample)
    if field is None:
        pytest.skip(f"{name} holds no Decimal field")
    with pytest.raises(ProductionError):
        type(sample).from_obj(dict(sample.to_obj(), **{field: 4.10}))


@pytest.mark.parametrize(
    "payload, path_part",
    [
        ({"fee": 1.5}, "native.fee"),
        ({"charges": {"fee": 0.25}}, "native.charges.fee"),
        ({"price": [1.0, 2]}, "native.price[0]"),
        ({"legs": [{"qty": 2.0}]}, "native.legs[0].qty"),
    ],
)
def test_a_money_float_inside_an_opaque_payload_refuses(payload, path_part):
    """`native`/`extra` are the venue's own JSON and are not typed field
    by field, so the money rule has to walk them. A list under a money
    name inherits it — `{"price": [1.0]}` is a price that is a float —
    which is the same reading `ledger.py` applies to a record body,
    because both call the one walk in `base.py`."""
    with pytest.raises(ProductionError) as exc:
        records.Ack(
            client_ref="cref-1",
            venue_ref="vref-1",
            status="filled",
            ts_ms=MS,
            filled_qty=Decimal("10"),
            avg_price=Decimal("0.41"),
            fee=Decimal("0.02"),
            reason="",
            native=payload,
        )
    assert path_part in str(exc.value)


def test_a_ratio_inside_an_opaque_payload_stays_a_float():
    """Only the money NAMES are closed; a `confidence` or a weight list
    is dimensionless and legal (§5.4)."""
    ack = records.Ack(
        client_ref="cref-1",
        venue_ref="vref-1",
        status="filled",
        ts_ms=MS,
        filled_qty=Decimal("10"),
        avg_price=Decimal("0.41"),
        fee=Decimal("0.02"),
        reason="",
        native={"confidence": 0.61, "weights": [0.25, 0.75]},
    )
    assert ack.native["confidence"] == 0.61
    assert ack.native["weights"] == [0.25, 0.75]


def test_the_opaque_money_walk_is_the_shared_owner():
    """One walk, imported — not a second copy that came to disagree with
    the ledger's about a list under a money key (CLAUDE.md)."""
    from dskit.production import base

    assert records.reject_money_floats is base.reject_money_floats


def test_every_declared_money_field_carries_a_decimal():
    """`vocab.MONEY_FIELDS` is what the ledger refuses a float under, so
    a field named there that holds anything but a `Decimal` (or `None`)
    is a float the refusal was written to catch and would not."""
    offenders = []
    for name, sample in SAMPLES.items():
        for field in dataclasses.fields(sample):
            if field.name not in MONEY_FIELDS:
                continue
            value = getattr(sample, field.name)
            if value is not None and not isinstance(value, Decimal):
                offenders.append(f"{name}.{field.name} = {value!r}")
    assert not offenders, offenders


# ---------------------------------------------------------------------------
# Field lists — the spellings the rest of the package binds
# ---------------------------------------------------------------------------


def _names(cls):
    return tuple(f.name for f in dataclasses.fields(cls))


def test_intent_has_the_sixteen_fields_in_declared_order():
    assert _names(records.Intent) == (
        "client_ref",
        "decision_plan_id",
        "decision_plan_digest",
        "proposal",
        "created_ms",
        "authority_id",
        "release_hash",
        "inputs_asof_ms",
        "inputs_digest",
        "coverage_digest",
        "quote_asof_ms",
        "quote_digest",
        "evidence_asof_ms",
        "evidence_digest",
        "risk_version",
        "risk_state_digest",
    )


def test_decision_plan_has_the_eighteen_fields_in_declared_order():
    """Declared order IS the digest order (§5.4)."""
    assert _names(records.DecisionPlan) == (
        "plan_id",
        "inputs_asof_ms",
        "inputs_digest",
        "coverage_digest",
        "quote_asof_ms",
        "quote_digest",
        "evidence_asof_ms",
        "evidence_digest",
        "provenance_digests",
        "original",
        "final",
        "findings",
        "gate_results",
        "scope_verdict",
        "risk_effect",
        "risk_version",
        "risk_state_digest",
        "result",
    )


def test_policy_request_has_the_nine_fields_rule_veto_receives():
    assert _names(records.PolicyRequest) == (
        "operation",
        "risk_effect",
        "rung",
        "breaker",
        "health",
        "readiness",
        "authority",
        "origin",
        "pending_control",
    )


def test_provenance_has_the_five_fields_the_tick_freezes():
    assert _names(records.Provenance) == (
        "inputs_asof_ms",
        "inputs_digest",
        "coverage_digest",
        "quote_asof_ms",
        "quote_digest",
    )


def test_reduction_intent_has_exactly_the_seven_signed_fields_in_order():
    """`candidate` is signed with the rest because scope keys live on the
    candidate: without it a reduction leg contributes no requirement and
    every guard refuses for missing evidence (§5.4)."""
    assert _names(records.ReductionIntent) == (
        "release_hash",
        "request_id",
        "index",
        "candidate",
        "proposal",
        "risk_state_digest",
        "expires_ms",
    )


def test_evidence_requirement_has_exactly_the_eight_digested_fields_in_order():
    assert _names(records.EvidenceRequirement) == (
        "measure",
        "window_kind",
        "window_arg",
        "scope_key",
        "window_start_ms",
        "window_end_ms",
        "baseline_at_ms",
        "include_working",
    )


def test_tick_result_carries_the_feed_block_as_a_member():
    """The loop never sees an `EntryBatch`, so five of §6's seven feed
    members exist nowhere else and could not be added afterwards (§5.4)."""
    assert set(_names(records.TickResult)) == {
        "tick_id",
        "status",
        "data_asof_ms",
        "coverage_digest",
        "inputs_digest",
        "decision_plan_ids",
        "legs",
        "findings",
        "observed_at_ms",
        "nav",
        "latency_ms",
        "leg_latency_ms",
        "refusal_reason",
        "error",
        "feed",
    }


def test_act_permit_binds_every_field_section_five_four_lists():
    assert set(_names(records.ActPermit)) == {
        "plan_id",
        "authority_id",
        "decision_plan_digest",
        "release_hash",
        "intent_digest",
        "client_ref",
        "instrument",
        "risk_effect",
        "inputs_asof_ms",
        "inputs_digest",
        "coverage_digest",
        "quote_asof_ms",
        "quote_digest",
        "evidence_asof_ms",
        "evidence_digest",
        "authority_scope_digest",
        "reduction_right_digest",
        "risk_version",
        "risk_state_digest",
        "readiness_digest",
        "readiness_until_ms",
        "lease_scope",
        "fencing_token",
        "safety_epoch_digest",
        "valid_until_ms",
        "checked_at_ms",
    }


# ---------------------------------------------------------------------------
# The Permit split (§5.4, §5.15 Liskov)
# ---------------------------------------------------------------------------


def test_permit_is_a_frozen_dataclass_base_and_not_an_abc():
    """Deliberately NOT a seam ABC, so §5.15's "every seam ABC has an
    abstract hook" rule does not reach it — and so it can be constructed."""
    import inspect

    assert dataclasses.is_dataclass(records.Permit)
    assert not inspect.isabstract(records.Permit)
    assert _names(records.Permit) == (
        "plan_id",
        "decision_plan_digest",
        "client_ref",
        "valid_until_ms",
    )


def test_both_permits_subclass_the_shared_base():
    assert issubclass(records.SimulatedPermit, records.Permit)
    assert issubclass(records.ActPermit, records.Permit)


def test_the_simulated_permit_adds_nothing_outward_authorising():
    """It is what shadow/paper/recorded executors receive; anything more
    would be an authority that rung has no business holding."""
    assert _names(records.SimulatedPermit) == _names(records.Permit)


def test_an_act_permit_is_not_a_simulated_permit():
    """`LiveExecutor` refuses by TYPE, so the two must not be
    interchangeable."""
    assert not isinstance(ACT_PERMIT, records.SimulatedPermit)
    assert not isinstance(SIMULATED_PERMIT, records.ActPermit)


def test_an_act_permit_binds_both_the_intent_and_the_reduction_right():
    """The verifier recomputes the first to prove the order is the one
    planned, and matches the second to prove the right authorises it."""
    assert ACT_PERMIT.intent_digest == INTENT.intent_digest()
    assert ACT_PERMIT.reduction_right_digest is None
    reduction = dataclasses.replace(
        ACT_PERMIT,
        reduction_right_digest=REDUCTION_INTENT.reduction_intent_digest(),
    )
    assert reduction.reduction_right_digest != reduction.intent_digest


# ---------------------------------------------------------------------------
# Digest recipes
# ---------------------------------------------------------------------------


def test_intent_digest_hashes_the_intent_without_its_client_ref():
    payload = {k: v for k, v in INTENT.to_obj().items() if k != "client_ref"}
    assert INTENT.intent_digest() == _canonical_hash(payload)


def test_intent_digest_ignores_the_client_ref():
    """`client_ref` identifies the intent rather than being part of it: on
    the flatten path it DERIVES from the reduction digest."""
    other = dataclasses.replace(INTENT, client_ref="cref-2")
    assert other.intent_digest() == INTENT.intent_digest()


def test_intent_digest_includes_the_authority_id():
    """Two intents authorised under different arms must not hash alike."""
    other = dataclasses.replace(INTENT, authority_id="auth-99")
    assert other.intent_digest() != INTENT.intent_digest()


@pytest.mark.parametrize(
    "field,value",
    [
        ("release_hash", "e" * 64),
        ("inputs_digest", "f" * 64),
        ("quote_asof_ms", MS + 1),
        ("risk_state_digest", "0" * 64),
        ("decision_plan_id", "plan-2"),
    ],
)
def test_intent_digest_binds_every_other_field(field, value):
    other = dataclasses.replace(INTENT, **{field: value})
    assert other.intent_digest() != INTENT.intent_digest()


def test_decision_plan_digest_hashes_all_eighteen_fields():
    assert DECISION_PLAN.decision_plan_digest() == _canonical_hash(
        DECISION_PLAN.to_obj()
    )


@pytest.mark.parametrize(
    "field,value",
    [("result", "not_sent"), ("risk_effect", "reduce"), ("plan_id", "plan-2")],
)
def test_decision_plan_digest_changes_with_any_field(field, value):
    other = dataclasses.replace(DECISION_PLAN, **{field: value})
    assert other.decision_plan_digest() != DECISION_PLAN.decision_plan_digest()


def test_requirement_digest_is_computed_at_construction_over_the_eight_fields():
    """Born complete and frozen (§5.5): nothing stamps a constructed
    requirement afterwards, or a child measure computing its own digest
    would silently fail to deduplicate."""
    assert REQUIREMENT.requirement_digest == _canonical_hash(REQUIREMENT.to_obj())
    assert "requirement_digest" not in _names(records.EvidenceRequirement)


def test_two_measures_asking_the_same_question_share_one_digest():
    """This is what lets accounting fetch the evidence once."""
    twin = dataclasses.replace(REQUIREMENT)
    assert twin.requirement_digest == REQUIREMENT.requirement_digest
    wider = dataclasses.replace(REQUIREMENT, include_working=False)
    assert wider.requirement_digest != REQUIREMENT.requirement_digest


def test_a_duration_window_arg_must_be_milliseconds():
    """`window_arg` is normalised so two spellings of one window produce
    one digest: a duration to ms, a count to an int, a calendar window to
    its resolved `[start, end)` bounds."""
    with pytest.raises(ProductionError):
        dataclasses.replace(REQUIREMENT, window_arg="one day")


def test_a_count_window_arg_is_an_integer():
    counted = dataclasses.replace(
        REQUIREMENT, window_kind="count", window_arg=50
    )
    assert counted.window_arg == 50
    with pytest.raises(ProductionError):
        dataclasses.replace(REQUIREMENT, window_kind="count", window_arg=50.5)


def test_a_calendar_window_arg_is_its_resolved_bounds():
    resolved = dataclasses.replace(
        REQUIREMENT,
        window_kind="calendar",
        window_arg=(MS - 3_600_000, MS),
    )
    assert resolved.window_arg == (MS - 3_600_000, MS)
    with pytest.raises(ProductionError):
        dataclasses.replace(REQUIREMENT, window_kind="calendar", window_arg="session")


def test_an_empty_window_has_no_argument():
    empty = dataclasses.replace(REQUIREMENT, window_kind="none", window_arg=None)
    assert empty.window_arg is None
    with pytest.raises(ProductionError):
        dataclasses.replace(REQUIREMENT, window_kind="none", window_arg=5)


def test_reduction_intent_digest_hashes_the_seven_signed_fields():
    assert REDUCTION_INTENT.reduction_intent_digest() == _canonical_hash(
        REDUCTION_INTENT.to_obj()
    )


def test_reduction_intent_digest_binds_the_signed_candidate():
    """Signing the candidate means the maker approves the scope the limits
    will be measured over, not just the order."""
    other = dataclasses.replace(
        REDUCTION_INTENT,
        candidate=records.Candidate(
            id="cand-1", instrument="INS1", scope_keys=("INS1", "INS2")
        ),
    )
    assert other.reduction_intent_digest() != REDUCTION_INTENT.reduction_intent_digest()


def test_reduction_intent_digest_binds_the_index():
    """Two entries whose proposals are byte-identical still differ, which
    is why `flatten-request` compares proposal CONTENT for duplicates."""
    other = dataclasses.replace(REDUCTION_INTENT, index=1)
    assert other.reduction_intent_digest() != REDUCTION_INTENT.reduction_intent_digest()


def test_a_reduction_plan_refuses_digests_that_disagree_with_its_intents():
    """The stored plan carries both the intents and their digests, and
    the checker's authorization names the digests. If the two could
    disagree, a maker would sign one order and a single-use right would
    authorise another — so the record pins the agreement at
    construction (CLAUDE.md: a value that must appear twice is pinned)."""
    good = records.ReductionPlan(
        release_hash=RELEASE,
        risk_state_digest=DIGEST_B,
        intents=(REDUCTION_INTENT,),
        reduction_intent_digests=(REDUCTION_INTENT.reduction_intent_digest(),),
        expires_ms=MS + 600_000,
    )
    assert good.reduction_intent_digests == (
        REDUCTION_INTENT.reduction_intent_digest(),
    )
    with pytest.raises(ProductionError) as exc:
        records.ReductionPlan(
            release_hash=RELEASE,
            risk_state_digest=DIGEST_B,
            intents=(REDUCTION_INTENT,),
            reduction_intent_digests=(DIGEST_C,),
            expires_ms=MS + 600_000,
        )
    assert "reduction_intent_digest" in str(exc.value)


def test_a_reduction_plan_refuses_a_digest_list_of_the_wrong_length():
    with pytest.raises(ProductionError):
        records.ReductionPlan(
            release_hash=RELEASE,
            risk_state_digest=DIGEST_B,
            intents=(REDUCTION_INTENT,),
            reduction_intent_digests=(),
            expires_ms=MS + 600_000,
        )


def test_a_reduction_intent_digest_is_never_an_intent_digest():
    """§5.4: a different hash of a different object, never spelled the
    same way — the maker signs the first, the leg builds the second."""
    assert (
        REDUCTION_INTENT.reduction_intent_digest() != INTENT.intent_digest()
    ), "the two digests must not collide for the same release and proposal"
    assert not hasattr(records.ReductionIntent, "intent_digest")


# ---------------------------------------------------------------------------
# Invariants the records enforce themselves
# ---------------------------------------------------------------------------


def test_order_state_enforces_the_quantity_identity_at_construction():
    """`filled_qty + remaining_qty == qty` — an order book that does not
    add up is a position size that does not add up."""
    assert ORDER_STATE.filled_qty + ORDER_STATE.remaining_qty == ORDER_STATE.qty
    with pytest.raises(ProductionError):
        dataclasses.replace(ORDER_STATE, remaining_qty=Decimal("5"))


def test_an_entry_batch_pins_data_asof_to_the_oldest_watermark():
    """D6: one fresh instrument cannot hide a stale input."""
    assert ENTRY_BATCH.data_asof_ms == min(
        w.latest_asof_ms for w in ENTRY_BATCH.watermarks_by_key.values()
    )
    with pytest.raises(ProductionError):
        dataclasses.replace(ENTRY_BATCH, data_asof_ms=MS)


def test_an_entry_batch_needs_at_least_one_watermark():
    with pytest.raises(ProductionError):
        dataclasses.replace(ENTRY_BATCH, watermarks_by_key={})


@pytest.mark.parametrize("side", SIDES)
def test_every_declared_side_is_accepted(side):
    assert dataclasses.replace(PROPOSAL, side=side).side == side


def test_an_undeclared_side_refuses():
    with pytest.raises(ProductionError):
        dataclasses.replace(PROPOSAL, side="short")


def test_an_undeclared_tif_refuses():
    with pytest.raises(ProductionError):
        dataclasses.replace(PROPOSAL, tif="gtx")


@pytest.mark.parametrize("status", FILL_STATUSES)
def test_a_fill_status_comes_from_the_closed_set(status):
    fill = SAMPLES["Fill"]
    assert dataclasses.replace(fill, status=status).status == status


def test_an_undeclared_fill_status_refuses():
    with pytest.raises(ProductionError):
        dataclasses.replace(SAMPLES["Fill"], status="settled")


def test_a_duplicate_client_ref_is_a_rejected_ack_not_an_exception():
    """§5.4: `DuplicateRef` is a `reason` VALUE — `submit` always returns
    an `Ack`, so a venue that refuses a re-used ref cannot become an
    exception a caller might not catch."""
    ack = dataclasses.replace(ACK, status="rejected", reason="duplicate_ref")
    assert ack.status == "rejected"
    assert ack.reason == "duplicate_ref"
    duplicate_ref = getattr(records, "DuplicateRef", None)
    assert duplicate_ref is None or not (
        isinstance(duplicate_ref, type) and issubclass(duplicate_ref, BaseException)
    )


def test_an_undeclared_ack_status_refuses():
    with pytest.raises(ProductionError):
        dataclasses.replace(ACK, status="working")


def test_an_execution_scope_is_canonical_and_compares_by_value():
    """The ownership domain two releases contend for (§5.7.2): equality is
    what makes "exact scope equality" checkable at all."""
    assert SCOPE == records.ExecutionScope(venue="paper-venue", account="acct-1")
    assert SCOPE != records.ExecutionScope(venue="paper-venue", account="acct-2")
    assert _names(records.ExecutionScope) == ("venue", "account")
    with pytest.raises(ProductionError):
        records.ExecutionScope(venue="", account="acct-1")


def test_a_tick_result_refuses_a_latency_key_that_is_not_a_phase():
    """§6 pins one key per `Tick` phase method."""
    with pytest.raises(ProductionError):
        dataclasses.replace(TICK_RESULT, latency_ms={"warmup": 1})


def test_a_tick_result_refuses_a_leg_latency_key_that_is_not_a_bucket():
    with pytest.raises(ProductionError):
        dataclasses.replace(TICK_RESULT, leg_latency_ms={"refresh": 1})


def test_a_tick_result_feed_block_carries_all_seven_members():
    partial = dict(FEED_BLOCK)
    partial.pop("required_keys_digest")
    with pytest.raises(ProductionError):
        dataclasses.replace(TICK_RESULT, feed=partial)


def test_a_tick_result_may_record_a_missing_valuation():
    """`nav` is null when a mark is missing or balances span currencies —
    a recorded fact, not a gap (§5.16)."""
    assert dataclasses.replace(TICK_RESULT, nav=None).nav is None


# ---------------------------------------------------------------------------
# risk_digest — what a permit binds about the account
# ---------------------------------------------------------------------------


def test_risk_digest_excludes_observation_only_timestamps():
    """Freshness is deadline-bound separately; re-observing the same
    account must not look like an economic change (§5.4)."""
    later = dataclasses.replace(ACCOUNT_STATE, asof_ms=MS + 60_000)
    assert later.risk_digest() == ACCOUNT_STATE.risk_digest()


def test_risk_digest_changes_when_a_balance_moves():
    moved = dataclasses.replace(
        ACCOUNT_STATE,
        balances=(dataclasses.replace(BALANCE, available=Decimal("100.00")),),
    )
    assert moved.risk_digest() != ACCOUNT_STATE.risk_digest()


def test_risk_digest_changes_when_a_position_moves():
    moved = dataclasses.replace(
        ACCOUNT_STATE,
        positions=(dataclasses.replace(POSITION, qty=Decimal("6")),),
    )
    assert moved.risk_digest() != ACCOUNT_STATE.risk_digest()


def test_risk_digest_changes_when_a_working_order_moves():
    moved = dataclasses.replace(
        ACCOUNT_STATE,
        working=(
            dataclasses.replace(
                ORDER_STATE,
                filled_qty=Decimal("5"),
                remaining_qty=Decimal("5"),
            ),
        ),
    )
    assert moved.risk_digest() != ACCOUNT_STATE.risk_digest()


def test_risk_digest_changes_when_an_evidence_value_moves():
    moved = dataclasses.replace(
        ACCOUNT_STATE,
        measure_evidence={
            REQUIREMENT.requirement_digest: {
                "INS1": dataclasses.replace(EVIDENCE, value=Decimal("-99.00"))
            }
        },
    )
    assert moved.risk_digest() != ACCOUNT_STATE.risk_digest()


def test_risk_digest_ignores_when_the_evidence_was_observed():
    moved = dataclasses.replace(
        ACCOUNT_STATE,
        measure_evidence={
            REQUIREMENT.requirement_digest: {
                "INS1": dataclasses.replace(EVIDENCE, known_at_ms=MS + 5_000)
            }
        },
    )
    assert moved.risk_digest() == ACCOUNT_STATE.risk_digest()


def test_risk_digest_is_a_hex_sha256():
    value = ACCOUNT_STATE.risk_digest()
    assert len(value) == 64 and set(value) <= set("0123456789abcdef")


# ---------------------------------------------------------------------------
# Module surface
# ---------------------------------------------------------------------------


def test_every_sampled_record_is_public_api():
    missing = [name for name in RECORD_NAMES if name not in records.__all__]
    assert not missing, f"records.__all__ omits: {missing}"


def test_every_public_record_has_a_sample():
    """The sample table is what the eight generic contract tests walk, so
    a record added to `__all__` without one is a record whose frozenness,
    round trip, default-deny and money rule nobody checked — a pinning
    test that omits a knob is worse than none (CLAUDE.md)."""
    unsampled = [name for name in records.__all__ if name not in SAMPLES]
    assert not unsampled, f"no sample for: {unsampled}"


def test_records_exports_no_private_name():
    assert not [n for n in records.__all__ if n.startswith("_")]
