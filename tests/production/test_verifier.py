"""`verifier.py` — the last gate between a minted permit and real money (§5.14, D14).

Everything before this point is a *record* of an intention. `verify_and_call`
is the moment the intention becomes an order, so it is the one place in the
package where being slightly wrong costs money rather than a test failure.
D14 fixes what it does, in this order and with no caller-visible gap:

* it **rehashes the already-frozen `EntryBatch` in memory** and checks its
  source identity and input deadline *without rereading mutable rows* — a
  re-read would be a second, later observation, and the whole point of the
  bound `inputs_digest` is that the order the venue receives was decided
  from the bytes the plan hashed;
* it **refreshes** quote, accounting, authority, executor identity and lease
  state, requires **exact** equality with every version and digest the plan,
  intent and permit bound, and rechecks every deadline, the hard guards, the
  authority scope and the action policy;
* it then invokes `native_call(intent, permit, timeout_ms)` **synchronously**,
  with a timeout bounded by the permit's remaining lifetime;
* **any** mismatch is `Ack(not_sent, …)` naming the member that moved; a raise
  or timeout *after possible I/O* is `unknown`, because the request may have
  left; and an `unknown` disables further sends until reconciliation resolves
  the ambiguous reference. It never replans and never reauthorizes in place.

The tests below are one per bound member, because that is the only shape of
test that can fail for the right reason: a gate that checks eight of nine
bindings passes every "happy path" test ever written. Each mutation moves
exactly one member and asserts the refusal NAMES it.

Fakes, not mocks, for every collaborator — the point is to move one input at a
time — with the real `ActionPolicy`, the real `records` value objects, the real
`ReleaseManifest` over the conftest synthetic run, and a `TestClock`. No
network, no wall clock, no sleeping.

Plan gaps this module pins (see the report):

* §5.14's eleven collaborators cannot build a `PolicyRequest`: the section
  itself says "a `PolicyRequest` needs breaker, health and readiness", and
  `health` is in no constructor. `Authority` (§5.13.1) takes `health` among
  its ten. `health` is pinned here as a twelfth, keyword-only, so §5.14's
  positional order is untouched.
* The gate must rehash the frozen `EntryBatch`, and `(intent, permit, state)`
  is "the only route it has" (§5.14) — yet `TickState` (§5.8.1) has five
  members and none of them is the batch. Pinned here as a sixth `TickState`
  member, `entry_batch`, assembled by `Tick.run` exactly like `feed_status`
  and `feed_ages`.
"""

import dataclasses
import inspect
from decimal import Decimal
from types import MappingProxyType

import pytest

from dskit.production import feed as feed_module
from dskit.production import verifier as verifier_module
from dskit.production.base import ProductionError, canonical_hash
from dskit.production.clock import TestClock
from dskit.production.coordination import LeasePermit
from dskit.production.document import ServeDocument
from dskit.production.policy import ActionPolicy
from dskit.production.records import (
    AccountState,
    ActPermit,
    Ack,
    Candidate,
    EntryBatch,
    ExecutionScope,
    Finding,
    InputWatermark,
    Intent,
    Proposal,
    Quote,
    QuoteSet,
    RiskVersion,
    SafetyEpoch,
    ScopeVerdict,
    SimulatedPermit,
)
from dskit.production.state import (
    ArmingProjection,
    ReadinessProjection,
    StateView,
    TickState,
)
from dskit.production.verifier import VERIFY_REASONS, SubmissionVerifier
from tests.production.conftest import NOW_MS, SERIES_ID
from tests.production.test_document import live_capable_document

# ---------------------------------------------------------------------------
# Fixed material
# ---------------------------------------------------------------------------

INSTRUMENT = "INS1"
CLIENT_REF = "c" * 64
PLAN_ID = "p" * 64
AUTHORITY_ID = "auth-1"
HOLDER = "release-1/process-1"
FENCE = 7

SCOPE = ExecutionScope(venue="paper", account="strategy-a")
OTHER_SCOPE = ExecutionScope(venue="paper", account="strategy-b")

#: The health the scenario is minted under; `_health_not_ready` refuses every
#: other member, so a health MOVE is only isolable in this direction.
HEALTH = "ready"

#: When the session containing `checked_at_ms` closes, per the fake calendar.
CLOSE_MS = NOW_MS + 3_600_000

#: §4.1's three freshness budgets, as `example_document` declares them. They
#: are read from the document in the tests too — these copies exist only so a
#: test can say "one millisecond past max_quote_age_ms" in its own words.
MAX_STALENESS_MS = 120_000
MAX_QUOTE_AGE_MS = 30_000
MAX_VALUATION_AGE_MS = 60_000
SUBMIT_TIMEOUT_MS = 5_000

#: Generous, so only the deadline a test moves can be the one that fires.
PERMIT_LIFETIME_MS = 20_000

#: The refusal vocabulary, restated INDEPENDENTLY of `verifier.py` (a list
#: read from its subject asserts nothing). One name per bound member, so a
#: refusal always says which binding moved.
EXPECTED_REASONS = (
    "authority_scope",
    "calendar_closed",
    "client_ref",
    "coverage_digest",
    "decision_plan_digest",
    "disabled",
    "evidence_age",
    "evidence_digest",
    "fencing_token",
    "guard",
    "input_deadline",
    "inputs_digest",
    "intent_digest",
    "lease",
    "not_armed",
    "permit_expired",
    "quote_age",
    "quote_digest",
    "readiness_digest",
    "readiness_expired",
    "release",
    "release_hash",
    "risk_state_digest",
    "risk_version",
    "safety_epoch",
    "scope",
    "source_config",
)


# ---------------------------------------------------------------------------
# Collaborator fakes — one knob each, so a test moves exactly one thing
# ---------------------------------------------------------------------------


class FakeExecutor:
    """The read/query surface the gate touches: its authenticated scope."""

    def __init__(self, scope=SCOPE):
        self.scope = scope
        self.calls = []

    def execution_scope(self):
        self.calls.append("execution_scope")
        return self.scope

    def submit(self, *args, **kwargs):
        raise AssertionError("the gate calls native_call, never executor.submit")


class FakeAccounting:
    """`source_tokens` is the accounting refresh the gate can actually make."""

    def __init__(self, tokens=("etok-1", ("atok-1",))):
        self.tokens = tokens
        self.calls = []

    def source_tokens(self, executor, at_ms):
        self.calls.append((executor, at_ms))
        return self.tokens

    def snapshot(self, *args, **kwargs):
        raise AssertionError("the gate never re-snapshots — it never replans")


class FakeLease:
    """Holds one permit per scope; `None` means the grip was lost."""

    def __init__(self, permit=None):
        self._permit = permit
        self.calls = []

    def current(self, scope):
        self.calls.append(scope)
        return self._permit

    def permit_current(self, permit):
        return self._permit is not None and self._permit == permit


class FakeArming:
    """`current(view, at_ms)` — the ordinary arm the fold holds, or None."""

    def __init__(self, arm=None):
        self.arm = arm
        self.calls = []

    def current(self, view, at_ms):
        self.calls.append((view, at_ms))
        return self.arm

    def check_conjunction(self, *args, **kwargs):
        raise AssertionError(
            "check_conjunction needs an Invocation, an origin and a reduction "
            "binding, none of which the gate holds — see the report"
        )


class FakeGuards:
    """A `GuardChain` stand-in: the hard-guard recheck and the scope gate."""

    def __init__(self, findings=(), final=None, verdict=None):
        self._findings = tuple(findings)
        self._final = final
        self._verdict = verdict
        self.calls = []
        self.guards = MappingProxyType({})

    def check_all(self, proposal, state):
        self.calls.append(("check_all", proposal, state))
        return (self._final if self._final is not None else proposal, self._findings)

    def check_authority_scope(self, proposal, state, scope):
        self.calls.append(("check_authority_scope", proposal, state, scope))
        if self._verdict is not None:
            return self._verdict
        return ScopeVerdict(allowed=True, scope_key=proposal.instrument, reason="")


class FakeInbox:
    """The control spool: a command queued but not yet folded still blocks."""

    def __init__(self, pending=()):
        self._pending = tuple(pending)
        self.calls = 0

    def pending(self):
        self.calls += 1
        return self._pending


class FakeCalendar:
    """Open unless a test closes it, with the session close the epoch binds."""

    def __init__(self, open_=True, close_ms=CLOSE_MS):
        self.open = open_
        self.close_ms = close_ms
        self.calls = []
        self.windows = []

    def is_open(self, ms):
        self.calls.append(ms)
        return self.open

    def window(self, kind, at_ms):
        self.windows.append((kind, at_ms))
        return (at_ms - 3_600_000, self.close_ms)


class FakeHealth:
    """The health state machine's current member of `HEALTH_STATES`.

    `Health.state` is a PROPERTY (§5.11), so the double is one too: a fake
    that answered a callable is what let `verifier.py` call
    `health.state()` and raise `TypeError` against every real `Health`.
    """

    def __init__(self, state=HEALTH):
        self._state = state
        self.calls = 0

    @property
    def state(self):
        self.calls += 1
        return self._state


def _forbidden(*args, **kwargs):
    """Stand in for the door back to the rows: being called is the defect."""
    raise AssertionError("the gate reread the entry rows")


class NativeCall:
    """The child gateway's callback: records its arguments, answers an `Ack`."""

    def __init__(self, answer=None, raises=None):
        self.calls = []
        self.answer = answer
        self.raises = raises

    def __call__(self, intent, permit, timeout_ms):
        self.calls.append((intent, permit, timeout_ms))
        if self.raises is not None:
            raise self.raises
        if self.answer is not None:
            return self.answer
        return Ack(
            client_ref=intent.client_ref,
            venue_ref="v-1",
            status="open",
            ts_ms=permit.checked_at_ms,
            filled_qty=Decimal("0"),
            avg_price=None,
            fee=Decimal("0"),
            reason="",
            native={},
        )


# ---------------------------------------------------------------------------
# One consistent scenario, rebuilt from its economic inputs
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class LegState(TickState):
    """The `TickState` a leg hands `submit`, carrying the frozen `EntryBatch`.

    §5.14 rules `(intent, permit, state)` is the gate's only route, and the
    gate must rehash the batch — so the batch is a `TickState` member. Until
    `state.py` grows it (see `test_tick_state_carries_the_entry_batch…`), this
    subclass is what the gate is written against; a real six-member
    `TickState` satisfies every use below unchanged.
    """

    entry_batch: object = None


@dataclasses.dataclass(frozen=True)
class Scenario:
    """Everything one `verify_and_call` needs, mutually consistent."""

    document: object
    release: object
    root: str
    clock: object
    batch: EntryBatch
    quotes: QuoteSet
    account: AccountState
    view: StateView
    state: LegState
    intent: Intent
    permit: ActPermit
    arm: ArmingProjection
    lease_permit: LeasePermit
    epoch: SafetyEpoch


def entry_batch(source_config_hash, asof_ms=NOW_MS, outputs=None):
    """One frozen batch whose `inputs_digest` really is its outputs' hash."""
    outputs = {"rows": [{"instrument": INSTRUMENT, "value": 1}]} if outputs is None else outputs
    watermark = InputWatermark(
        key=INSTRUMENT, latest_asof_ms=asof_ms, source_digest="a" * 64
    )
    watermarks = {INSTRUMENT: watermark}
    return EntryBatch(
        outputs=outputs,
        watermarks_by_key=watermarks,
        required_keys_digest=canonical_hash([INSTRUMENT]),
        coverage_digest=canonical_hash({INSTRUMENT: watermark.to_obj()}),
        data_asof_ms=asof_ms,
        inputs_digest=canonical_hash(outputs),
        source_config_hash=source_config_hash,
    )


def quote_set(asof_ms=NOW_MS):
    """One instrument's market, with the digest the plan binds."""
    quote = Quote(
        instrument=INSTRUMENT,
        bid=Decimal("0.40"),
        ask=Decimal("0.42"),
        mid=Decimal("0.41"),
        asof_ms=asof_ms,
    )
    return QuoteSet(
        quotes=(quote,),
        quote_digest=canonical_hash([quote.to_obj()]),
        min_asof_ms=asof_ms,
    )


def account_state(asof_ms=NOW_MS, tokens=("etok-1", ("atok-1",)), economic_seq=41):
    """The refreshed step-(2) account the plan, intent and permit all bind."""
    return AccountState(
        risk_version=RiskVersion(
            economic_seq=economic_seq, executor_token=tokens[0], accounting_tokens=tokens[1]
        ),
        asof_ms=asof_ms,
        evidence_digest=canonical_hash({"evidence": asof_ms}),
        balances=(),
        positions=(),
        working=(),
        measure_evidence={},
        source_digests={"fills": "b" * 64},
    )


def proposal(batch, quotes):
    """A buy the guards allow, carrying the batch's and quotes' provenance."""
    return Proposal(
        id="cand-1",
        instrument=INSTRUMENT,
        side="buy",
        qty=Decimal("10"),
        notional=Decimal("4.20"),
        limit=Decimal("0.42"),
        tif="ioc",
        expires_ms=NOW_MS + PERMIT_LIFETIME_MS,
        reference_price=Decimal("0.41"),
        exposure=Decimal("4.20"),
        direction="long",
        confidence=0.61,
        prediction=0.58,
        baseline=0.50,
        expected_value=0.03,
        inputs_asof_ms=batch.data_asof_ms,
        inputs_digest=batch.inputs_digest,
        coverage_digest=batch.coverage_digest,
        quote_asof_ms=quotes.min_asof_ms,
        quote_digest=quotes.quote_digest,
        extra={},
    )


def readiness_projection(now_ms=NOW_MS, valid_until_ms=None):
    """A GO bound to this release, unexpired unless a test expires it."""
    return ReadinessProjection(
        verdict="go",
        items=({"item": "reconciled", "required": True, "evidence": "recon-1",
                "waiver": None, "passed": True},),
        readiness_digest="7" * 64,
        evaluated_at_ms=now_ms,
        valid_until_ms=now_ms + 86_400_000 if valid_until_ms is None else valid_until_ms,
    )


def arming_projection(release_hash, now_ms=NOW_MS, authority_id=AUTHORITY_ID):
    """The current ordinary arm, with an allowlist the proposal satisfies."""
    return ArmingProjection(
        authority_id=authority_id,
        release_hash=release_hash,
        rung="live_limited",
        maker="principal-maker",
        checker="principal-checker",
        armed_at_ms=now_ms - 1_000,
        armed_until_ms=now_ms + 3_600_000,
        allowlist=[INSTRUMENT],
        limits_overlay={},
        request_proof_digest="6" * 64,
        approval_proof_digest="4" * 64,
    )


def state_view(account, arm=None, readiness=None, **overrides):
    """A `StateView` with the members the gate reads; the rest are empty."""
    fields = {
        "positions": (),
        "working": MappingProxyType({}),
        "pending": (),
        "balances": MappingProxyType({}),
        "decision_history": (),
        "breaker": "active",
        "arming": arm,
        "readiness": readiness,
        "guard_holds": MappingProxyType({}),
        "reduction": None,
        "pending_control": MappingProxyType({}),
        "risk_version": account.risk_version,
        "head_seq": 12,
        "head_hash": "d" * 64,
    }
    fields.update(overrides)
    return StateView(**fields)


def serve_doc(run_dir, **overrides):
    """§4.1's illustration, made live-capable and pointed at the real run."""
    obj = live_capable_document("live_limited")
    obj["series_id"] = SERIES_ID
    obj["serving"]["run_dir"] = run_dir
    obj["serving"]["adapter"] = "tests.production.conftest"
    for section, values in overrides.items():
        obj[section].update(values)
    return ServeDocument.from_obj(obj)


def build_scenario(
    release_manifest,
    run_dir,
    *,
    now_ms=NOW_MS,
    inputs_asof_ms=None,
    quote_asof_ms=None,
    evidence_asof_ms=None,
    tokens=("etok-1", ("atok-1",)),
    readiness_valid_until_ms=None,
    fencing_token=FENCE,
    lease_expires_ms=None,
    valid_until_ms=None,
    outputs=None,
):
    """Build one mutually consistent leg: batch → account → intent → permit.

    Every digest below is COMPUTED from the value it describes, so a test
    that moves an economic input gets a scenario that is still internally
    consistent — which is what makes a single-member mutation isolable.
    """
    clock = TestClock(start_ms=now_ms)
    document = serve_doc(run_dir)
    release_hash = release_manifest.release_hash
    batch = entry_batch(
        release_manifest.source_config["hash"],
        asof_ms=now_ms if inputs_asof_ms is None else inputs_asof_ms,
        outputs=outputs,
    )
    quotes = quote_set(now_ms if quote_asof_ms is None else quote_asof_ms)
    account = account_state(
        asof_ms=now_ms if evidence_asof_ms is None else evidence_asof_ms, tokens=tokens
    )
    arm = arming_projection(release_hash, now_ms=now_ms)
    readiness = readiness_projection(now_ms=now_ms, valid_until_ms=readiness_valid_until_ms)
    view = state_view(account, arm=arm, readiness=readiness)
    state = LegState(
        view=view,
        account=account,
        feed_status="live",
        feed_ages=(),
        calendar=FakeCalendar(),
        entry_batch=batch,
    )
    final = proposal(batch, quotes)
    intent = Intent(
        client_ref=CLIENT_REF,
        decision_plan_id=PLAN_ID,
        decision_plan_digest="e" * 64,
        proposal=final,
        created_ms=now_ms,
        authority_id=arm.authority_id,
        release_hash=release_hash,
        inputs_asof_ms=batch.data_asof_ms,
        inputs_digest=batch.inputs_digest,
        coverage_digest=batch.coverage_digest,
        quote_asof_ms=quotes.min_asof_ms,
        quote_digest=quotes.quote_digest,
        evidence_asof_ms=account.asof_ms,
        evidence_digest=account.evidence_digest,
        risk_version=account.risk_version,
        risk_state_digest=account.risk_digest(),
    )
    lease_permit = LeasePermit(
        scope=SCOPE,
        holder=HOLDER,
        fencing_token=fencing_token,
        expires_ms=now_ms + 30_000 if lease_expires_ms is None else lease_expires_ms,
    )
    # The epoch the MINT would have computed over this scenario: every term
    # read from the object that owns it, exactly as `leg.py` reads it, and
    # the four collaborator terms from the defaults `build_verifier` wires.
    epoch = SafetyEpoch(
        release_hash=release_hash,
        readiness_digest=readiness.readiness_digest,
        readiness_until_ms=readiness.valid_until_ms,
        calendar_close_ms=state.calendar.close_ms,
        coverage_digest=batch.coverage_digest,
        inputs_digest=batch.inputs_digest,
        inputs_asof_ms=batch.data_asof_ms,
        quote_digest=quotes.quote_digest,
        quote_asof_ms=quotes.min_asof_ms,
        evidence_digest=account.evidence_digest,
        evidence_asof_ms=account.asof_ms,
        risk_version=account.risk_version,
        risk_state_digest=account.risk_digest(),
        executor_scope=SCOPE,
        health=HEALTH,
        breaker=view.breaker,
        rung=document.rung,
        risk_effect="increase",
        authority_id=arm.authority_id,
        authority_scope_digest=canonical_hash(arm.to_obj()),
        pending_control=tuple(sorted(view.pending_control)),
        queued_control=0,
        lease_scope=lease_permit.scope,
        fencing_token=lease_permit.fencing_token,
    )
    permit = ActPermit(
        plan_id=PLAN_ID,
        decision_plan_digest=intent.decision_plan_digest,
        client_ref=intent.client_ref,
        valid_until_ms=(
            now_ms + PERMIT_LIFETIME_MS if valid_until_ms is None else valid_until_ms
        ),
        authority_id=arm.authority_id,
        release_hash=release_hash,
        intent_digest=intent.intent_digest(),
        instrument=final.instrument,
        risk_effect="increase",
        inputs_asof_ms=intent.inputs_asof_ms,
        inputs_digest=intent.inputs_digest,
        coverage_digest=intent.coverage_digest,
        quote_asof_ms=intent.quote_asof_ms,
        quote_digest=intent.quote_digest,
        evidence_asof_ms=intent.evidence_asof_ms,
        evidence_digest=intent.evidence_digest,
        authority_scope_digest=canonical_hash(arm.to_obj()),
        reduction_right_digest=None,
        risk_version=intent.risk_version,
        risk_state_digest=intent.risk_state_digest,
        readiness_digest=readiness.readiness_digest,
        readiness_until_ms=readiness.valid_until_ms,
        lease_scope=SCOPE,
        fencing_token=lease_permit.fencing_token,
        safety_epoch_digest=epoch.digest(),
        checked_at_ms=now_ms,
    )
    return Scenario(
        document=document,
        release=release_manifest,
        root=run_dir,
        clock=clock,
        batch=batch,
        quotes=quotes,
        account=account,
        view=view,
        state=state,
        intent=intent,
        permit=permit,
        arm=arm,
        lease_permit=lease_permit,
        epoch=epoch,
    )


def build_verifier(scenario, **overrides):
    """A `SubmissionVerifier` over fakes, one keyword per collaborator."""
    parts = {
        "executor": FakeExecutor(),
        "accounting": FakeAccounting(),
        "lease": FakeLease(scenario.lease_permit),
        "arming": FakeArming(scenario.arm),
        "guards": FakeGuards(),
        "action_policy": ActionPolicy(),
        "release": scenario.release,
        "inbox": FakeInbox(),
        "calendar": scenario.state.calendar,
        "document": scenario.document,
        "clock": scenario.clock,
        "health": FakeHealth(),
    }
    parts.update(overrides)
    return SubmissionVerifier(**parts), parts


@pytest.fixture
def scenario(release_manifest, run_dir):
    """One consistent leg at `NOW_MS`, ready to be mutated one member at a time."""
    return build_scenario(release_manifest, run_dir)


@pytest.fixture
def gate(scenario):
    """`(verifier, parts, scenario)` — the happy path, before any mutation."""
    verifier, parts = build_verifier(scenario)
    return verifier, parts, scenario


def call(gate_tuple, native=None, **replace):
    """Run the gate, optionally replacing permit fields first."""
    verifier, _parts, scen = gate_tuple
    native = NativeCall() if native is None else native
    permit = dataclasses.replace(scen.permit, **replace) if replace else scen.permit
    return verifier.verify_and_call(scen.intent, permit, scen.state, native), native


# ---------------------------------------------------------------------------
# Construction — §5.14's eleven collaborators, plus the one it forgot
# ---------------------------------------------------------------------------


def test_the_constructor_takes_section_5_14s_eleven_collaborators_in_that_order(scenario):
    """§5.14 writes the constructor positionally, and `compose.py` builds it
    from that reading. A reordering here would silently swap `arming` for
    `guards` at every live submit."""
    _verifier, parts = build_verifier(scenario)
    positional = SubmissionVerifier(
        parts["executor"],
        parts["accounting"],
        parts["lease"],
        parts["arming"],
        parts["guards"],
        parts["action_policy"],
        parts["release"],
        parts["inbox"],
        parts["calendar"],
        parts["document"],
        parts["clock"],
        health=parts["health"],
    )
    assert isinstance(positional, SubmissionVerifier)


def test_health_is_a_collaborator_because_a_policy_request_needs_one(scenario):
    """§5.14 itself says "a `PolicyRequest` needs breaker, health and
    readiness", and names ten collaborators that cannot supply health;
    `Authority` (§5.13.1) takes it. Without it the health axis of the action
    matrix is simply unenforced at the final gate — the one place it matters
    most. Pinned as a twelfth, keyword-only, so §5.14's order is untouched."""
    names = tuple(inspect.signature(SubmissionVerifier.__init__).parameters)
    assert "health" in names, (
        "SubmissionVerifier cannot build a PolicyRequest without a health "
        "state — see the report's plan gap"
    )


def test_the_gate_exposes_only_its_contract(scenario):
    """`__all__` plus the `_` prefix IS the API here: a caller that can reach
    an internal check can re-derive a permission by branching, which §5.15
    forbids."""
    verifier, _parts = build_verifier(scenario)
    public = {name for name in dir(verifier) if not name.startswith("_")}
    assert public == {
        "verify_and_call",
        "refuse_until_reconciled",
        "reset_after_reconcile",
        "disabled",
    }


def test_verify_reasons_is_a_sorted_closed_set_naming_every_bound_member():
    """A refusal that does not name the member that moved is an operator
    guessing at 3am. The list is restated here INDEPENDENTLY: an expectation
    read from its subject asserts nothing."""
    assert VERIFY_REASONS == EXPECTED_REASONS
    assert VERIFY_REASONS == tuple(sorted(set(VERIFY_REASONS)))


def test_tick_state_carries_the_entry_batch_the_verifier_rehashes():
    """D14: the gate "rehashes the already frozen `EntryBatch` in memory"; and
    §5.14: `submit(intent, permit, state)` is "the only route it has". Those
    two sentences require the batch to be reachable from `state`, and §5.8.1's
    five-member `TickState` does not carry it. It is a tick product exactly
    like `feed_status` and `feed_ages` — assembled once by `Tick.run` — so it
    belongs beside them."""
    members = {f.name for f in dataclasses.fields(TickState)}
    assert "entry_batch" in members, (
        "TickState has no route to the frozen EntryBatch, so the gate cannot "
        "rehash it without rereading rows — see the report's plan gap"
    )


# ---------------------------------------------------------------------------
# The clean path
# ---------------------------------------------------------------------------


def test_a_clean_gate_calls_native_exactly_once_and_returns_its_ack(gate):
    """The gate is a gate, not a retry loop: `resilience` and the child
    gateway own retries, and a second call here would be a second order."""
    ack, native = call(gate)
    assert len(native.calls) == 1
    assert ack.status == "open"
    assert ack.client_ref == CLIENT_REF


def test_native_receives_the_intent_and_the_full_permit(gate):
    """D14: "The full permit … reach `_submit_native`" — the child gateway
    enforces fencing, deadline and idempotency from it, so handing it a
    reduced view would move those checks into core where they cannot be
    atomic with the send."""
    _ack, native = call(gate)
    (intent, permit, _timeout), = native.calls
    assert intent is gate[2].intent
    assert permit is gate[2].permit


def test_the_timeout_never_outlives_the_permit(gate):
    """§5.14: "a timeout bounded by the permit's remaining lifetime". A call
    that may still be in flight when the permit expires is a call whose
    authority has lapsed — exactly the window fencing exists to close."""
    _ack, native = call(gate)
    (_intent, permit, timeout_ms), = native.calls
    remaining = permit.valid_until_ms - gate[2].clock.now_ms()
    assert 0 < timeout_ms <= remaining


def test_the_timeout_is_the_lesser_of_the_document_budget_and_the_permit_remainder(
    release_manifest, run_dir
):
    """Two bounds, and the gate takes the smaller: the document's declared
    `execution.submit_timeout_ms` is a budget, the permit's remainder is a
    deadline, and only one of them can be right at a time."""
    scen = build_scenario(release_manifest, run_dir, valid_until_ms=NOW_MS + 1_000)
    verifier, _parts = build_verifier(scen)
    native = NativeCall()
    verifier.verify_and_call(scen.intent, scen.permit, scen.state, native)
    assert native.calls[0][2] == 1_000

    wide = build_scenario(release_manifest, run_dir, valid_until_ms=NOW_MS + 500_000)
    verifier2, _parts2 = build_verifier(wide)
    native2 = NativeCall()
    verifier2.verify_and_call(wide.intent, wide.permit, wide.state, native2)
    assert native2.calls[0][2] == SUBMIT_TIMEOUT_MS
    assert wide.document.execution.submit_timeout_ms == SUBMIT_TIMEOUT_MS


def test_the_gate_refreshes_the_executor_scope_the_lease_and_the_source_tokens(gate):
    """"Refreshes quote, accounting, authority, executor identity and lease"
    is not a description of a mood: each of those is a live read, and a gate
    that reuses the values step (2) already had is checking a snapshot
    against itself."""
    verifier, parts, _scen = gate
    call(gate)
    assert parts["executor"].calls == ["execution_scope"]
    assert parts["lease"].calls == [SCOPE]
    assert parts["accounting"].calls and parts["accounting"].calls[0][1] == NOW_MS
    assert parts["arming"].calls
    assert isinstance(verifier, SubmissionVerifier)


def test_the_gate_rechecks_the_hard_guards_and_the_authority_scope(gate):
    """§5.13 step (7): "rechecks every hard gate". Between step (2) and here,
    earlier legs of this same tick have folded reservations and acks."""
    _verifier, parts, scen = gate
    call(gate)
    names = [entry[0] for entry in parts["guards"].calls]
    assert names == ["check_all", "check_authority_scope"]
    assert parts["guards"].calls[0][1] is scen.intent.proposal


# ---------------------------------------------------------------------------
# The frozen batch — rehashed in memory, never reread
# ---------------------------------------------------------------------------


def test_the_frozen_batch_is_rehashed_without_rereading_a_single_row(gate, monkeypatch):
    """D14: "without rereading mutable rows". A re-read is a *second, later*
    observation of a source that may have moved, and the order about to be
    sent was decided from the first one. `snapshot_entry` is the only door
    back to the rows, so bolting it shut proves the gate does not use it."""
    monkeypatch.setattr(feed_module, "snapshot_entry", _forbidden)
    monkeypatch.setattr(verifier_module, "snapshot_entry", _forbidden, raising=False)
    ack, native = call(gate)
    assert len(native.calls) == 1
    assert ack.status == "open"


def test_a_batch_whose_outputs_no_longer_hash_to_its_digest_refuses(
    release_manifest, run_dir
):
    """"Rehashes" means recomputing, not re-reading the field it is checking.
    A batch whose `inputs_digest` is a claim rather than a hash of the bytes
    beside it is exactly what the recomputation exists to catch."""
    scen = build_scenario(release_manifest, run_dir)
    tampered = dataclasses.replace(scen.batch, outputs={"rows": [{"instrument": "OTHER"}]})
    state = dataclasses.replace(scen.state, entry_batch=tampered)
    verifier, _parts = build_verifier(scen)
    native = NativeCall()
    ack = verifier.verify_and_call(scen.intent, scen.permit, state, native)
    assert (ack.status, ack.reason) == ("not_sent", "inputs_digest")
    assert native.calls == []


def test_a_batch_whose_source_identity_left_the_release_refuses(
    release_manifest, run_dir
):
    """§5.14 "checks its source identity": the release pins the source config
    the run was trained and planned against. A batch acquired under a
    different one is different data wearing the same name."""
    scen = build_scenario(release_manifest, run_dir)
    drifted = dataclasses.replace(scen.batch, source_config_hash="f" * 64)
    state = dataclasses.replace(scen.state, entry_batch=drifted)
    verifier, _parts = build_verifier(scen)
    native = NativeCall()
    ack = verifier.verify_and_call(scen.intent, scen.permit, state, native)
    assert (ack.status, ack.reason) == ("not_sent", "source_config")
    assert native.calls == []


# ---------------------------------------------------------------------------
# One test per bound member — permit-side drift
# ---------------------------------------------------------------------------

#: `(reason, permit-field, bad value)` — each moves exactly one binding.
PERMIT_DRIFTS = (
    ("coverage_digest", "coverage_digest", "1" * 64),
    ("quote_digest", "quote_digest", "2" * 64),
    ("evidence_digest", "evidence_digest", "3" * 64),
    ("risk_state_digest", "risk_state_digest", "4" * 64),
    ("readiness_digest", "readiness_digest", "5" * 64),
    ("intent_digest", "intent_digest", "6" * 64),
    ("decision_plan_digest", "decision_plan_digest", "8" * 64),
    ("client_ref", "client_ref", "0" * 64),
    ("release_hash", "release_hash", "b" * 64),
)


@pytest.mark.parametrize(("reason", "field", "value"), PERMIT_DRIFTS)
def test_a_bound_member_that_moved_refuses_naming_it(gate, reason, field, value):
    """§5.14 "requires exact equality" — for every member, not most of them.
    A gate that checks eight of nine bindings passes every happy-path test
    ever written, so each binding gets its own mutation and its own name in
    the refusal."""
    ack, native = call(gate, **{field: value})
    assert (ack.status, ack.reason) == ("not_sent", reason)
    assert native.calls == []
    assert ack.reason in VERIFY_REASONS


def test_a_permit_bound_to_another_intent_refuses(gate):
    """`intent_digest` is what proves the order about to be sent is the order
    that was planned; a permit minted for a different intent authorises a
    different order."""
    _verifier, _parts, scen = gate
    other = dataclasses.replace(
        scen.intent, proposal=dataclasses.replace(scen.intent.proposal, qty=Decimal("999"))
    )
    verifier, _p = build_verifier(scen)
    native = NativeCall()
    ack = verifier.verify_and_call(other, scen.permit, scen.state, native)
    assert (ack.status, ack.reason) == ("not_sent", "intent_digest")
    assert native.calls == []


def test_a_risk_version_that_moved_under_the_permit_refuses(release_manifest, run_dir):
    """D14: a live adapter's monotonic source tokens are how "the account I
    priced this against" is checked; a token that changed means fills,
    corrections or balances landed since the plan bound its version."""
    scen = build_scenario(release_manifest, run_dir)
    verifier, _parts = build_verifier(
        scen, accounting=FakeAccounting(tokens=("etok-2", ("atok-1",)))
    )
    native = NativeCall()
    ack = verifier.verify_and_call(scen.intent, scen.permit, scen.state, native)
    assert (ack.status, ack.reason) == ("not_sent", "risk_version")
    assert native.calls == []


def test_an_account_whose_economic_sequence_advanced_refuses(release_manifest, run_dir):
    """`economic_seq` advances on every position reservation, order, fill,
    correction or balance change — the events that make an earlier sizing
    decision stale."""
    scen = build_scenario(release_manifest, run_dir)
    moved = account_state(asof_ms=NOW_MS, economic_seq=42)
    state = dataclasses.replace(scen.state, account=moved)
    verifier, _parts = build_verifier(scen)
    native = NativeCall()
    ack = verifier.verify_and_call(scen.intent, scen.permit, state, native)
    assert (ack.status, ack.reason) == ("not_sent", "risk_version")
    assert native.calls == []


# ---------------------------------------------------------------------------
# Deadlines — each budget is the document's, and code holds none of them
# ---------------------------------------------------------------------------


def test_an_input_past_max_staleness_refuses(release_manifest, run_dir):
    """§4.1's `schedule.max_staleness_ms` is a document knob; the gate reads
    it there. An input older than it is a decision made from data that no
    longer describes the world."""
    scen = build_scenario(
        release_manifest, run_dir, inputs_asof_ms=NOW_MS - MAX_STALENESS_MS - 1
    )
    assert scen.document.schedule.max_staleness_ms == MAX_STALENESS_MS
    verifier, _parts = build_verifier(scen)
    native = NativeCall()
    ack = verifier.verify_and_call(scen.intent, scen.permit, scen.state, native)
    assert (ack.status, ack.reason) == ("not_sent", "input_deadline")
    assert native.calls == []


def test_an_input_exactly_at_max_staleness_still_passes(release_manifest, run_dir):
    """Inclusive at the bound, like every other freshness ladder in this
    package: a budget of 120 s means 120 s is allowed."""
    scen = build_scenario(
        release_manifest, run_dir, inputs_asof_ms=NOW_MS - MAX_STALENESS_MS
    )
    verifier, _parts = build_verifier(scen)
    native = NativeCall()
    ack = verifier.verify_and_call(scen.intent, scen.permit, scen.state, native)
    assert ack.status == "open"


def test_a_quote_past_max_quote_age_refuses(release_manifest, run_dir):
    """A permit that outlived its quote prices an order at a market that has
    moved; §5.13 step (6) already makes quote age one of the nine terms of
    `valid_until_ms`, and the gate rechecks it because the permit's own
    deadline is a minimum, not a proof."""
    scen = build_scenario(
        release_manifest, run_dir, quote_asof_ms=NOW_MS - MAX_QUOTE_AGE_MS - 1
    )
    assert scen.document.schedule.max_quote_age_ms == MAX_QUOTE_AGE_MS
    verifier, _parts = build_verifier(scen)
    native = NativeCall()
    ack = verifier.verify_and_call(scen.intent, scen.permit, scen.state, native)
    assert (ack.status, ack.reason) == ("not_sent", "quote_age")
    assert native.calls == []


def test_evidence_past_max_valuation_age_refuses(release_manifest, run_dir):
    """§5.7.1: "Quotes must satisfy `max_valuation_age_ms`"; the evidence the
    guards sized against inherits that deadline."""
    scen = build_scenario(
        release_manifest, run_dir, evidence_asof_ms=NOW_MS - MAX_VALUATION_AGE_MS - 1
    )
    assert scen.document.accounting.max_valuation_age_ms == MAX_VALUATION_AGE_MS
    verifier, _parts = build_verifier(scen)
    native = NativeCall()
    ack = verifier.verify_and_call(scen.intent, scen.permit, scen.state, native)
    assert (ack.status, ack.reason) == ("not_sent", "evidence_age")
    assert native.calls == []


def test_an_expired_permit_refuses(gate):
    """`valid_until_ms` is the nine-term minimum of §5.13 step (6). At it, the
    permit is dead — expiry is inclusive everywhere in this package."""
    ack, native = call(gate, valid_until_ms=NOW_MS)
    assert (ack.status, ack.reason) == ("not_sent", "permit_expired")
    assert native.calls == []


def test_an_expired_readiness_go_refuses(release_manifest, run_dir):
    """§5.13: the GO is "durable, release-bound and expiring" rather than
    recomputed at submit; an expired one is not a NO-GO, it is no answer."""
    scen = build_scenario(release_manifest, run_dir, readiness_valid_until_ms=NOW_MS)
    verifier, _parts = build_verifier(scen)
    native = NativeCall()
    ack = verifier.verify_and_call(scen.intent, scen.permit, scen.state, native)
    assert (ack.status, ack.reason) == ("not_sent", "readiness_expired")
    assert native.calls == []


def test_a_closed_calendar_refuses(gate):
    """§5.13 step (3) and step (7) both recheck the calendar: a session that
    closed between the plan and the send makes the order untradeable, and
    `safety_epoch_digest` covers the calendar for the same reason."""
    _verifier, parts, scen = gate
    parts["calendar"].open = False
    verifier, _p = build_verifier(scen, calendar=parts["calendar"])
    native = NativeCall()
    ack = verifier.verify_and_call(scen.intent, scen.permit, scen.state, native)
    assert (ack.status, ack.reason) == ("not_sent", "calendar_closed")
    assert native.calls == []


# ---------------------------------------------------------------------------
# Authority, scope, lease and fence
# ---------------------------------------------------------------------------


def test_a_missing_ordinary_arm_is_not_armed(release_manifest, run_dir):
    """§5.7: "missing or stale authority is `Ack(not_sent, reason='not_armed')`"
    — a value, never an exception, because `submit` is total (§5.15)."""
    scen = build_scenario(release_manifest, run_dir)
    verifier, _parts = build_verifier(scen, arming=FakeArming(None))
    native = NativeCall()
    ack = verifier.verify_and_call(scen.intent, scen.permit, scen.state, native)
    assert (ack.status, ack.reason) == ("not_sent", "not_armed")
    assert native.calls == []


def test_an_arm_the_permit_was_not_minted_under_is_not_armed(release_manifest, run_dir):
    """A permit names its `authority_id`; a *different* current arm is a
    different maker-checker act with a different allowlist and overlay."""
    scen = build_scenario(release_manifest, run_dir)
    other = arming_projection(scen.release.release_hash, authority_id="auth-2")
    verifier, _parts = build_verifier(scen, arming=FakeArming(other))
    native = NativeCall()
    ack = verifier.verify_and_call(scen.intent, scen.permit, scen.state, native)
    assert (ack.status, ack.reason) == ("not_sent", "not_armed")
    assert native.calls == []


def test_not_armed_never_crosses_the_contract_as_an_exception(release_manifest, run_dir):
    """§5.7: "`NotArmed` stays an internal exception … and never crosses the
    `SubmittingExecutor` contract, so no subclass raises where its base
    promises a value"."""
    scen = build_scenario(release_manifest, run_dir)
    verifier, _parts = build_verifier(scen, arming=FakeArming(None))
    ack = verifier.verify_and_call(scen.intent, scen.permit, scen.state, NativeCall())
    assert isinstance(ack, Ack)
    assert not [name for name in verifier_module.__all__ if "NotArmed" in name]


def test_an_authenticated_scope_that_disagrees_refuses(release_manifest, run_dir):
    """§5.7.2 requires exact equality among the actual, document, release,
    lease and `ActPermit` scopes. The authenticated one is the executor's,
    and it is the only one an attacker or a misconfiguration can move
    without touching a config file."""
    scen = build_scenario(release_manifest, run_dir)
    verifier, _parts = build_verifier(scen, executor=FakeExecutor(scope=OTHER_SCOPE))
    native = NativeCall()
    ack = verifier.verify_and_call(scen.intent, scen.permit, scen.state, native)
    assert (ack.status, ack.reason) == ("not_sent", "scope")
    assert native.calls == []


def test_a_permit_whose_lease_scope_is_not_the_documents_refuses(gate):
    """The permit's `lease_scope` is one of the five scopes that must agree;
    a permit minted for another ownership domain must not act here."""
    ack, native = call(gate, lease_scope=OTHER_SCOPE)
    assert (ack.status, ack.reason) == ("not_sent", "scope")
    assert native.calls == []


def test_a_lost_lease_refuses(release_manifest, run_dir):
    """§5.7.2: "Loss/renewal failure … disables submit while preserving
    query/reconcile/cancel". Holding no permit is losing it."""
    scen = build_scenario(release_manifest, run_dir)
    verifier, _parts = build_verifier(scen, lease=FakeLease(None))
    native = NativeCall()
    ack = verifier.verify_and_call(scen.intent, scen.permit, scen.state, native)
    assert (ack.status, ack.reason) == ("not_sent", "lease")
    assert native.calls == []


def test_an_expired_lease_refuses(release_manifest, run_dir):
    """A permit past its expiry is not a grip; §5.7.2 makes expiry inclusive
    and requires a missed renewal deadline to invalidate locally rather than
    waiting for nominal expiry."""
    scen = build_scenario(release_manifest, run_dir, lease_expires_ms=NOW_MS)
    verifier, _parts = build_verifier(scen, lease=FakeLease(scen.lease_permit))
    native = NativeCall()
    ack = verifier.verify_and_call(scen.intent, scen.permit, scen.state, native)
    assert (ack.status, ack.reason) == ("not_sent", "lease")
    assert native.calls == []


def test_a_stale_fencing_token_refuses_locally(release_manifest, run_dir):
    """The gateway rejects a stale token atomically (§5.7.2), but a process
    that has already been fenced out must not send at all: the local check is
    what stops the request leaving, and the gateway is the backstop."""
    scen = build_scenario(release_manifest, run_dir)
    newer = dataclasses.replace(scen.lease_permit, fencing_token=FENCE + 1)
    verifier, _parts = build_verifier(scen, lease=FakeLease(newer))
    native = NativeCall()
    ack = verifier.verify_and_call(scen.intent, scen.permit, scen.state, native)
    assert (ack.status, ack.reason) == ("not_sent", "fencing_token")
    assert native.calls == []


# ---------------------------------------------------------------------------
# Guards, scope verdict and the action policy
# ---------------------------------------------------------------------------


def test_a_hard_guard_that_now_breaches_refuses(release_manifest, run_dir):
    """Between step (2) and here, earlier legs of this tick folded their own
    reservations; a limit that fit then may not fit now."""
    scen = build_scenario(release_manifest, run_dir)
    breach = Finding(
        guard="exposure",
        measure="exposure_after",
        value=Decimal("30000"),
        bound=Decimal("20000"),
        window="none",
        scope_key=INSTRUMENT,
        verdict="refuse",
        reason="exposure_after 30000 exceeds max 20000",
    )
    verifier, _parts = build_verifier(scen, guards=FakeGuards(findings=(breach,)))
    native = NativeCall()
    ack = verifier.verify_and_call(scen.intent, scen.permit, scen.state, native)
    assert (ack.status, ack.reason) == ("not_sent", "guard")
    assert native.calls == []


def test_an_amendment_at_the_gate_refuses_rather_than_being_adopted(
    release_manifest, run_dir
):
    """§5.14: the gate "never replans or reauthorizes in place". A guard that
    wants a smaller order at this point is describing an order nobody
    planned, recorded or authorised."""
    scen = build_scenario(release_manifest, run_dir)
    smaller = dataclasses.replace(scen.intent.proposal, qty=Decimal("1"))
    verifier, _parts = build_verifier(scen, guards=FakeGuards(final=smaller))
    native = NativeCall()
    ack = verifier.verify_and_call(scen.intent, scen.permit, scen.state, native)
    assert (ack.status, ack.reason) == ("not_sent", "guard")
    assert native.calls == []


def test_an_instrument_outside_the_arms_allowlist_refuses(release_manifest, run_dir):
    """The arm's allowlist and tighten-only overlay are re-applied to the
    exact final proposal; D11's authority is over *these* instruments."""
    scen = build_scenario(release_manifest, run_dir)
    denied = ScopeVerdict(
        allowed=False, scope_key=INSTRUMENT, reason="instrument outside the allowlist"
    )
    verifier, _parts = build_verifier(scen, guards=FakeGuards(verdict=denied))
    native = NativeCall()
    ack = verifier.verify_and_call(scen.intent, scen.permit, scen.state, native)
    assert (ack.status, ack.reason) == ("not_sent", "authority_scope")
    assert native.calls == []


def test_a_halted_breaker_refuses_with_the_policy_rules_own_name(release_manifest, run_dir):
    """§5.14 makes `ActionPolicy` the sole owner of the permission matrix, so
    the gate reports the *rule* that refused rather than inventing a second
    vocabulary beside it — which is how a caller could otherwise start
    re-deriving permissions by branching."""
    scen = build_scenario(release_manifest, run_dir)
    halted = state_view(scen.account, arm=scen.arm, readiness=scen.view.readiness,
                        breaker="halted")
    state = dataclasses.replace(scen.state, view=halted)
    verifier, _parts = build_verifier(scen)
    native = NativeCall()
    ack = verifier.verify_and_call(scen.intent, scen.permit, state, native)
    assert ack.status == "not_sent"
    assert ack.reason == "submit_refused_while_halted"
    assert ack.reason not in VERIFY_REASONS
    assert native.calls == []


def test_unready_health_refuses(release_manifest, run_dir):
    """D18: `degraded` observes and refuses acts. Without a health
    collaborator the gate could not ask, which is the plan gap above."""
    scen = build_scenario(release_manifest, run_dir)
    verifier, _parts = build_verifier(scen, health=FakeHealth("degraded"))
    native = NativeCall()
    ack = verifier.verify_and_call(scen.intent, scen.permit, scen.state, native)
    assert (ack.status, ack.reason) == ("not_sent", "submit_requires_ready_health")
    assert native.calls == []


def test_a_folded_pending_control_refuses(release_manifest, run_dir):
    """§5.8: a pending mutating command blocks the next pre-submit gate — the
    operator asked for something and has not been answered yet."""
    scen = build_scenario(release_manifest, run_dir)
    blocked = state_view(
        scen.account,
        arm=scen.arm,
        readiness=scen.view.readiness,
        pending_control=MappingProxyType({"req-1": "halt"}),
    )
    state = dataclasses.replace(scen.state, view=blocked)
    verifier, _parts = build_verifier(scen)
    native = NativeCall()
    ack = verifier.verify_and_call(scen.intent, scen.permit, state, native)
    assert (ack.status, ack.reason) == ("not_sent", "submit_blocked_by_pending_control")
    assert native.calls == []


def test_a_command_queued_but_not_yet_folded_also_refuses(release_manifest, run_dir):
    """§5.13.1: "`inbox` is in the constructor so a queued-but-unfolded
    command cannot be missed at the moment a permit is minted" — the same
    argument applies here, one step later, where the money moves."""
    scen = build_scenario(release_manifest, run_dir)
    inbox = FakeInbox(pending=({"request_id": "req-2", "purpose": "halt"},))
    verifier, _parts = build_verifier(scen, inbox=inbox)
    native = NativeCall()
    ack = verifier.verify_and_call(scen.intent, scen.permit, scen.state, native)
    assert (ack.status, ack.reason) == ("not_sent", "submit_blocked_by_pending_control")
    assert inbox.calls == 1
    assert native.calls == []


# ---------------------------------------------------------------------------
# The safety epoch — the catch-all over everything the earlier checks proved
# ---------------------------------------------------------------------------


def test_the_gate_recomputes_the_epoch_and_a_foreign_digest_refuses(gate):
    """§5.14 justifies `inbox` and `calendar` with "a digest the permit binds
    must be recomputable by whatever rechecks it". A minted-but-never-checked
    digest is a field, not a binding: the gate rebuilds the epoch from its own
    freshest values and requires the number the permit carries."""
    ack, native = call(gate)
    assert ack.status == "open" and len(native.calls) == 1

    foreign, foreign_native = call(gate, safety_epoch_digest="9" * 64)
    assert (foreign.status, foreign.reason) == ("not_sent", "safety_epoch")
    assert foreign_native.calls == []


def test_a_calendar_that_moved_since_the_mint_refuses_on_the_safety_epoch(gate):
    """The epoch binds the close of the session containing the permit's
    `checked_at_ms` — the permit's instant, not `now`, so an unchanged
    calendar yields an unchanged term and a calendar reloaded with different
    data is a difference. `is_open` is untouched here, so no deadline check
    can be the one that fires."""
    _verifier, parts, _scen = gate
    parts["calendar"].close_ms = CLOSE_MS - 600_000
    ack, native = call(gate)
    assert (ack.status, ack.reason) == ("not_sent", "safety_epoch")
    assert parts["calendar"].windows and parts["calendar"].windows[0][0] == SafetyEpoch.WINDOW
    assert native.calls == []


def test_a_control_queue_that_moved_since_the_mint_refuses_on_the_safety_epoch(
    release_manifest, run_dir
):
    """A command queued between mint and submit refuses at the policy, one
    check earlier (`test_a_command_queued_but_not_yet_folded_also_refuses`).
    The epoch is what covers the other direction — a permit minted while the
    spool held a command and submitted after it drained — which no other
    check looks at."""
    scen = build_scenario(release_manifest, run_dir)
    minted = dataclasses.replace(scen.epoch, queued_control=1)
    permit = dataclasses.replace(scen.permit, safety_epoch_digest=minted.digest())
    verifier, parts = build_verifier(scen)
    native = NativeCall()
    ack = verifier.verify_and_call(scen.intent, permit, scen.state, native)
    assert (ack.status, ack.reason) == ("not_sent", "safety_epoch")
    assert parts["inbox"].pending() == ()
    assert native.calls == []


def test_health_that_moved_since_the_mint_refuses_on_the_safety_epoch(
    release_manifest, run_dir
):
    """`Health.state` is a PROPERTY on both sides, and the epoch covers it.
    A move TO an unready state refuses at the policy
    (`test_unready_health_refuses`); a permit minted while degraded and
    submitted once ready is the direction only the epoch sees."""
    scen = build_scenario(release_manifest, run_dir)
    minted = dataclasses.replace(scen.epoch, health="degraded")
    permit = dataclasses.replace(scen.permit, safety_epoch_digest=minted.digest())
    verifier, parts = build_verifier(scen)
    native = NativeCall()
    ack = verifier.verify_and_call(scen.intent, permit, scen.state, native)
    assert (ack.status, ack.reason) == ("not_sent", "safety_epoch")
    assert parts["health"].state == HEALTH
    assert native.calls == []


def test_a_readiness_validity_that_moved_refuses_on_the_safety_epoch(
    release_manifest, run_dir
):
    """Recomputing the epoch from the permit's own fields would be a
    tautology — every term would agree by construction and the check would
    refuse nothing — so each term is read from the source the earlier checks
    compare against. `readiness_until_ms` is the sharpest case: `_bindings`
    compares only the readiness DIGEST and `_deadlines` only takes a minimum
    over the two instants, so a readiness re-evaluated with a longer validity
    passes every earlier check. The epoch is the only one that binds it."""
    scen = build_scenario(release_manifest, run_dir)
    later = readiness_projection(now_ms=NOW_MS, valid_until_ms=NOW_MS + 90_000_000)
    view = state_view(scen.account, arm=scen.arm, readiness=later)
    state = dataclasses.replace(scen.state, view=view)
    verifier, _parts = build_verifier(scen)
    native = NativeCall()
    ack = verifier.verify_and_call(scen.intent, scen.permit, state, native)
    assert later.readiness_digest == scen.permit.readiness_digest
    assert (ack.status, ack.reason) == ("not_sent", "safety_epoch")
    assert native.calls == []


# ---------------------------------------------------------------------------
# Release re-verification (D24)
# ---------------------------------------------------------------------------


def test_the_release_is_re_earned_from_bytes_before_every_submit(
    release_manifest, run_dir, tmp_path
):
    """D24 re-verifies content hashes, artifact age and the runtime
    fingerprint "immediately before submit" — which is why `release` is a
    collaborator at all. A root with none of the artifacts is the cheapest
    proof that the check runs."""
    scen = build_scenario(release_manifest, run_dir)
    empty = serve_doc(str(tmp_path / "elsewhere"))
    verifier, _parts = build_verifier(scen, document=empty)
    native = NativeCall()
    ack = verifier.verify_and_call(scen.intent, scen.permit, scen.state, native)
    assert (ack.status, ack.reason) == ("not_sent", "release")
    assert native.calls == []


def test_an_intent_bound_to_another_release_refuses(release_manifest, run_dir):
    """`series_id → release_hash → tick → plan → intent → client_ref` is the
    ownership chain §5.14 records; an intent from another release is not part
    of it."""
    scen = build_scenario(release_manifest, run_dir)
    other = dataclasses.replace(scen.intent, release_hash="c" * 64)
    permit = dataclasses.replace(
        scen.permit, release_hash="c" * 64, intent_digest=other.intent_digest()
    )
    verifier, _parts = build_verifier(scen)
    native = NativeCall()
    ack = verifier.verify_and_call(other, permit, scen.state, native)
    assert (ack.status, ack.reason) == ("not_sent", "release_hash")
    assert native.calls == []


# ---------------------------------------------------------------------------
# unknown, and the disable that follows it
# ---------------------------------------------------------------------------


def test_a_raise_after_possible_io_is_unknown_not_a_refusal(gate):
    """D13: "An executor call that raises after the request leaves `unknown`,
    which only `executor.order(ref)` may resolve — never a blind resend."
    Calling it `not_sent` would licence exactly that resend."""
    native = NativeCall(raises=ConnectionResetError("peer went away"))
    ack, _native = call(gate, native=native)
    assert ack.status == "unknown"
    assert ack.client_ref == CLIENT_REF


def test_a_timeout_after_possible_io_is_unknown(gate):
    """A deadline that fires says nothing about whether the venue saw the
    order; the only honest answer is the ambiguous one."""
    native = NativeCall(raises=TimeoutError("deadline"))
    ack, _native = call(gate, native=native)
    assert ack.status == "unknown"


def test_an_unknown_disables_the_next_send_until_reconciliation(gate):
    """§5.7's battery: "timeout … disables later sends"; §5.13 step (8): "an
    ambiguous outcome stops all later legs until reconciliation". A second
    order sent while the first is unresolved is the double-fill this whole
    package exists to make impossible."""
    verifier, _parts, scen = gate
    verifier.verify_and_call(
        scen.intent, scen.permit, scen.state, NativeCall(raises=TimeoutError("x"))
    )
    assert verifier.disabled is True
    native = NativeCall()
    ack = verifier.verify_and_call(scen.intent, scen.permit, scen.state, native)
    assert (ack.status, ack.reason) == ("not_sent", "disabled")
    assert native.calls == []


def test_a_gateway_that_reports_unknown_rather_than_raising_also_disables(gate):
    """§5.14 disables on "an `unknown`", not on the raise: a child gateway
    that turns its own timeout into `Ack(unknown)` leaves exactly the same
    ambiguous reference, and a second send while it is unresolved is the
    double-fill this package exists to prevent."""
    verifier, _parts, scen = gate
    ambiguous = NativeCall(
        answer=Ack(
            client_ref=CLIENT_REF,
            venue_ref=None,
            status="unknown",
            ts_ms=NOW_MS,
            filled_qty=Decimal("0"),
            avg_price=None,
            fee=Decimal("0"),
            reason="gateway timed out",
            native={},
        )
    )
    ack = verifier.verify_and_call(scen.intent, scen.permit, scen.state, ambiguous)
    assert ack.status == "unknown"
    assert verifier.disabled is True
    native = NativeCall()
    later = verifier.verify_and_call(scen.intent, scen.permit, scen.state, native)
    assert (later.status, later.reason) == ("not_sent", "disabled")
    assert native.calls == []


def test_refuse_until_reconciled_disables_the_gate_without_any_io(gate):
    """§5.9's `on_mismatch: refuse` had no mechanism: `apply_policy` computed
    it and the loop dropped it, so a mismatching venue kept submitting. It is
    the same "stop until reconciliation resolves it" the `unknown` path has,
    so it sets the SAME disable rather than a second one — and it is not a
    halt, which is the only difference between the two `on_mismatch` values."""
    verifier, _parts, scen = gate
    assert verifier.disabled is False
    verifier.refuse_until_reconciled("reconciliation found a mismatch")
    assert verifier.disabled is True
    native = NativeCall()
    ack = verifier.verify_and_call(scen.intent, scen.permit, scen.state, native)
    assert (ack.status, ack.reason) == ("not_sent", "disabled")
    assert native.calls == []


def test_a_gate_refused_for_a_mismatch_is_re_enabled_by_the_same_reset(gate):
    """One disable, one reset: `reset_after_reconcile` is what a later clean
    reconciliation calls, and it must lift a `refuse` exactly as it lifts an
    `unknown`, or `refuse` would be a permanent outage."""
    verifier, _parts, scen = gate
    verifier.refuse_until_reconciled("reconciliation found a mismatch")
    verifier.reset_after_reconcile()
    assert verifier.disabled is False
    ack, native = call(gate)
    assert ack.status == "open"
    assert len(native.calls) == 1


def test_reset_after_reconcile_re_enables_the_gate(gate):
    """Reconciliation is what resolves the ambiguous reference (D13), so it
    is what clears the disable — not a timer, and not the next tick."""
    verifier, _parts, scen = gate
    verifier.verify_and_call(
        scen.intent, scen.permit, scen.state, NativeCall(raises=TimeoutError("x"))
    )
    verifier.reset_after_reconcile()
    assert verifier.disabled is False
    native = NativeCall()
    ack = verifier.verify_and_call(scen.intent, scen.permit, scen.state, native)
    assert ack.status == "open"
    assert len(native.calls) == 1


def test_a_refusal_before_any_io_leaves_the_gate_enabled(gate):
    """Nothing left the process, so nothing is ambiguous. Disabling here
    would turn every stale-quote refusal into an outage."""
    verifier, _parts, scen = gate
    permit = dataclasses.replace(scen.permit, quote_digest="2" * 64)
    verifier.verify_and_call(scen.intent, permit, scen.state, NativeCall())
    assert verifier.disabled is False
    ack = verifier.verify_and_call(scen.intent, scen.permit, scen.state, NativeCall())
    assert ack.status == "open"


def test_the_gate_never_retries_the_native_call(gate):
    """One permit, one send. Retrying inside the gate would send a second
    order under an authority that authorised one."""
    native = NativeCall(raises=ConnectionResetError("boom"))
    call(gate, native=native)
    assert len(native.calls) == 1


# ---------------------------------------------------------------------------
# Argument discipline
# ---------------------------------------------------------------------------


def test_a_permit_that_is_not_an_act_permit_is_a_programming_error(gate):
    """`LiveExecutor` refuses a non-`ActPermit` BY TYPE before it ever
    delegates (§5.15), so a `SimulatedPermit` reaching the gate is a wiring
    defect, not a permission fact — and a defect must not be answered with a
    polite `Ack` that looks like a routine refusal."""
    _verifier, _parts, scen = gate
    verifier, _p = build_verifier(scen)
    simulated = SimulatedPermit(
        plan_id=PLAN_ID,
        decision_plan_digest=scen.intent.decision_plan_digest,
        client_ref=CLIENT_REF,
        valid_until_ms=NOW_MS + PERMIT_LIFETIME_MS,
    )
    with pytest.raises(ProductionError):
        verifier.verify_and_call(scen.intent, simulated, scen.state, NativeCall())


def test_a_non_intent_is_a_programming_error(gate):
    """The one canonical `Intent` type (§5.4) is what the digests are over."""
    _verifier, _parts, scen = gate
    verifier, _p = build_verifier(scen)
    with pytest.raises(ProductionError):
        verifier.verify_and_call(
            Candidate(id="x", instrument=INSTRUMENT, scope_keys=()),
            scen.permit,
            scen.state,
            NativeCall(),
        )


def test_a_native_call_that_is_not_callable_is_a_programming_error(gate):
    """The callback is `_submit_native`; anything else means the wrapper
    handed the gate something it cannot invoke."""
    _verifier, _parts, scen = gate
    verifier, _p = build_verifier(scen)
    with pytest.raises(ProductionError):
        verifier.verify_and_call(scen.intent, scen.permit, scen.state, "not-callable")


# ---------------------------------------------------------------------------
# Shape of every refusal
# ---------------------------------------------------------------------------


def test_every_refusal_is_a_not_sent_ack_for_this_intents_client_ref(gate):
    """§6's `decision.legs[]` needs an `Ack` for every leg, refused or not,
    and the fold matches it by `client_ref`. An `Ack` under another ref would
    be folded onto another order."""
    for reason, field, value in PERMIT_DRIFTS:
        ack, _native = call(gate, **{field: value})
        assert ack.status == "not_sent", reason
        assert ack.client_ref == CLIENT_REF
        assert ack.venue_ref is None
        assert ack.filled_qty == Decimal("0")
        assert ack.avg_price is None
        assert ack.fee == Decimal("0")


def test_every_refusal_is_stamped_from_the_injected_clock(gate):
    """D20's replay parity rests on the injected clock; a `time.time()` here
    would make one recorded refusal unreplayable."""
    _verifier, _parts, scen = gate
    scen.clock.advance(1_234)
    ack, _native = call(gate, quote_digest="2" * 64)
    assert ack.ts_ms == NOW_MS + 1_234
