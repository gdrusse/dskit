"""`leg.py` — the eight steps between a proposal and real money (§5.13, §5.13.1, D13).

This is the money path. Every barrier the package has, every reservation, the
cumulative-risk fold and the one call that reaches a venue live in these eight
methods, so a test here that passes for the wrong reason is a test that would
have let an order out.

What the tests below pin, and why each one can only pass for one reason:

* **`run` is final and walks `LEG_STEPS`.** "Record before act" is enforced by
  the base, not by convention inside eight methods (§5.13.1), so a subclass
  that reorders or skips a step must be impossible rather than discouraged. A
  subclass may override a *step* — that is the seam — and the tests use one to
  spy.
* **Barrier ORDER, not barrier presence.** D13 gives each of `decision_plan`,
  `intent`, `authority_use` and `authorization` its own barrier before
  `executor.submit`. The tests assert the whole interleaved call sequence —
  appends, barriers and the submit — because a barrier in the right count but
  the wrong place is exactly the bug that loses an order on a crash.
* **A refusal is a value; a crash is an exception.** A guard or gate refusal
  terminalizes the leg at step (4) with `result == "not_sent"`, an appended
  plan and no intent. A ledger that dies mid-run raises, and what survived on
  disk is what the plan promised would survive.
* **Freshness where it matters, consistency where it matters.** Steps (2),
  (3) and (6) each take a *fresh* `recording.state.snapshot()`; a trip folded
  between (5) and (6) refuses the attempt without minting. The tests fold real
  records into a real `SeriesState` through a ledger fake, so "the second leg
  sees the first leg's reservation" is a property of the fold rather than of a
  stub that was told to say so.
* **The §5.4 rebuild check.** For a reduction leg the constructed `Intent`
  must re-derive the signed `reduction_intent_digest` — that is the only thing
  standing between "the maker signed this order" and "this order reached the
  venue". It is tested twice: once positively, and once with a guard amendment
  that changes the economic content, which must refuse.
* **`Authority` polymorphism.** `SimulatedAuthority` writes nothing,
  `LiveAuthority` appends an `authorization`, `ReductionAuthority` appends the
  `authority_use` FIRST. That is D2 made structural: no module below
  `compose.py` asks what rung it is, which the AST test at the bottom pins.

Real collaborators wherever they exist — `records`, `SeriesState` / `StateView`
/ `TickState`, `GuardChain` / `Limit` / `RangeGuard`, `ActionPolicy`, `Arming`
/ `ArmingState`, `ProcessLease`, `ReleaseIdSource`, the `bundles`, a real
`ServeDocument` — plus a ledger fake that assigns the nine §6 envelope fields
and folds into the real `SeriesState` (the `test_breaker.py` idiom). Fakes only
where the module does not exist yet (accounting, executor, readiness, health,
inbox) or where the collaborator is the thing under test's *table*
(`AuthorityTable`). No wall clock, no network, no sleeping.

Plan gaps this module pins (see the report; each is flagged in the test that
depends on it):

* `Authority.mint(intent, plan, state_view)` (§5.13.1) cannot mint a reduction
  permit: `ActPermit.reduction_right_digest` is "copied from
  `bindings.reduction.digest`" (§5.16) and neither the `Intent` nor the
  `StateView` carries it. Pinned here as a fourth positional argument,
  `reduction`, `None` for a model leg — one signature for all three subclasses,
  so no subclass strengthens a precondition of its base (§5.15).
* Step (3)'s seventeen gates are prose, not a vocabulary. Pinned here as
  `leg.LEG_GATES`, restated independently below, in the shape `arming.py`
  (`CONJUNCTION_REASONS`) and `verifier.py` (`VERIFY_REASONS`) already use.
* `TickState` (§5.8.1) has five members and the verifier needs the frozen
  `EntryBatch` (§5.14); `tests/production/test_verifier.py` pins it as a sixth.
  The builder below passes it when the field exists, so this file agrees with
  that group either way without asserting the member itself.
* Record ids: R9 rules them kind-qualified and unique across the series.
  Pinned here as `decision_plan:<plan_id>`, `intent:<client_ref>`,
  `authority_use:<authority_id>:<reduction_intent_digest>` (§6's stated
  uniqueness for that kind), `authorization:<client_ref>` and
  `order_event:<client_ref>`.
* `records.Intent.authority_id` is annotated `str` and refuses `None`, but
  §5.16 rules it `None` at shadow/paper where no ordinary arm exists — so no
  simulated leg can build one. `str | None` is the fix.
* §5.5's `check_authority_scope` has no scope object at shadow/paper for the
  same reason. Pinned: the leg records the verdict and `ActionPolicy` decides
  (its authority axis is inert below live), rather than branching on a rung.
* A refusal AFTER the plan barrier — a step-(6) drift, a step-(7) mismatch —
  terminates an intent that exists, so the outcome needs a record: pinned as
  an `order_event` with `event`/`status` `not_sent` and a synthesized `Ack`,
  which is why `not_sent` is in both §6's event set and `vocab.STATUSES`. A
  refusal BEFORE it writes only the plan, whose `result` is the terminal fact.
"""

import ast
import dataclasses
import hashlib
import inspect
from decimal import Decimal
from pathlib import Path

import pytest

from dskit.production import leg as leg_module
from dskit.production import vocab
from dskit.production.arming import Arming, ApprovalVerifier, ArmingState, VerifiedPrincipal
from dskit.production.base import GENESIS_HASH, ProductionError, canonical_hash
from dskit.production.bundles import (
    Decision,
    Execution,
    Invocation,
    Observability,
    Recording,
    Safety,
    Schedule,
)
from dskit.production.clock import TestClock
from dskit.production.coordination import ProcessLease
from dskit.production.document import ServeDocument
from dskit.production.guards import GuardChain, Limit, RangeGuard
from dskit.production.ids import ReleaseIdSource
from dskit.production.leg import (
    Authority,
    LegBindings,
    LegEvaluation,
    LegPipeline,
    LegResult,
    LiveAuthority,
    ReductionAuthority,
    ReductionBinding,
    SimulatedAuthority,
)
from dskit.production.records import (
    AccountState,
    Ack,
    ActPermit,
    Balance,
    Candidate,
    DecisionPlan,
    EntryBatch,
    ExecutionScope,
    InputWatermark,
    Intent,
    Proposal,
    Quote,
    QuoteSet,
    ReductionAuthorization,
    ReductionIntent,
    RiskVersion,
    SimulatedPermit,
)
from dskit.production.policy import ActionPolicy
from dskit.production.state import ReadinessProjection, SeriesState, TickState
from tests.production.conftest import SERIES_ID
from tests.production.test_document import live_capable_document
from tests.production.test_purity import _branch_hits

# ---------------------------------------------------------------------------
# Fixed material — one coherent live_limited leg, moved one member at a time
# ---------------------------------------------------------------------------

NOW_MS = 1_767_268_800_000
RELEASE_HASH = "a1" * 32
PROCESS_ID = "process-1"
INSTRUMENT = "INS1"
OTHER_INSTRUMENT = "INS2"
TICK_ID = "t" * 64
REQUEST_ID = "req-flatten-1"

SCOPE = ExecutionScope(venue="paper", account="strategy-a")
HOLDER = "release-a/process-1"
LEASE_TTL_MS = 30_000

#: The rungs at which a live permit exists to gate, so an ordinary arm can be
#: in force (D11). Restated here for the SCENARIO builders only — `leg.py`
#: itself may never read a rung (D2), which the AST test at the bottom pins.
LIVE_RUNGS = ("live_limited", "live")

#: How long before the leg the tick's `account` phase ran. Non-zero on
#: purpose: §5.16 rules the plan, intent and permit bind step (2)'s REFRESHED
#: account and "never `bindings.state.account`", and a scenario where the two
#: agree could not tell the difference.
TICK_ASSEMBLY_LAG_MS = 5_000

#: The three §4.1 freshness budgets and the two deadlines the leg reads from
#: the document. Restated here only so a test can say "one millisecond past
#: `max_quote_age_ms`" in its own words; every assertion reads the document.
MAX_STALENESS_MS = 120_000
MAX_QUOTE_AGE_MS = 30_000
MAX_VENUE_SKEW_MS = 1_000
MAX_VALUATION_AGE_MS = 60_000
SUBMIT_TIMEOUT_MS = 5_000
READINESS_VALID_FOR_S = 86_400

#: The three digests the entry batch and quote set carry, fixed so a test can
#: assert an exact copy rather than "some 64-hex string".
INPUTS_DIGEST = "b2" * 32
COVERAGE_DIGEST = "c3" * 32
QUOTE_DIGEST = "d4" * 32
READINESS_DIGEST = "e5" * 32
SOURCE_CONFIG_HASH = "f6" * 32
RISK_STATE_DIGEST_SIGNED = "07" * 32

#: Step (3)'s gate names, restated INDEPENDENTLY of `leg.py` — a vocabulary
#: read from its subject asserts nothing (CLAUDE.md, "deliberate independent
#: restatement"). One name per member §5.13 step (3) lists, in that order.
EXPECTED_GATES = (
    "release",
    "readiness",
    "calendar",
    "coverage",
    "watermark_age",
    "quote_age",
    "quote_digest",
    "evidence_age",
    "evidence_digest",
    "venue_skew",
    "executor_scope",
    "health",
    "breaker",
    "rung",
    "risk_effect",
    "authority_scope",
    "lease",
)

#: The caller's three keys on every ledger append (ruling R1).
CALLER_KEYS = ("kind", "id", "body")

#: `LegBindings`' thirteen fields in §5.13.1 order, restated independently.
EXPECTED_BINDINGS_FIELDS = (
    "proposal",
    "origin",
    "entry_batch",
    "head_digest",
    "quotes",
    "state",
    "requirements",
    "reduction",
    "release",
    "rung",
    "tick_id",
    "leg_id",
    "leg_index",
)

#: `LegEvaluation`'s nine fields in §5.4 order, restated independently.
EXPECTED_EVALUATION_FIELDS = (
    "original",
    "final",
    "findings",
    "gate_results",
    "scope_verdict",
    "account",
    "risk_effect",
    "risk_version",
    "risk_state_digest",
)

#: `LegResult`'s ten fields in §5.13.1 order, restated independently.
EXPECTED_RESULT_FIELDS = (
    "result",
    "leg_id",
    "plan_id",
    "plan_digest",
    "final",
    "client_ref",
    "intent",
    "ack",
    "findings",
    "leg_latency_ms",
)


class Cut(RuntimeError):
    """A process death, injected at a chosen barrier — never caught by `run`."""


# ---------------------------------------------------------------------------
# Ledger — assigns the nine §6 envelope fields and folds into a real state
# ---------------------------------------------------------------------------


class FoldingLedger:
    """The `Ledger` surface a leg uses, folding into a real `SeriesState`.

    `append` assigns the nine §6 fields, chains the hash and calls
    `SeriesState.apply` — what the real ledger does — so a test asserts
    against the fold rather than against a stub that was told what to say.
    `calls` records `append`, `barrier` and (via the executor) `submit` in
    order, so "each step barriers before its effect" is checkable as a
    sequence rather than as a count.

    `cut_after_barrier` raises :class:`Cut` once the given barrier has
    completed, modelling a process death at that instant: everything before
    it is durable, everything after it never happened.
    """

    def __init__(self, state, clock, *, series_id=SERIES_ID, release_hash=RELEASE_HASH,
                 process_id=PROCESS_ID, cut_after_barrier=None):
        self.state = state
        self.clock = clock
        self.series_id = series_id
        self.release_hash = release_hash
        self.process_id = process_id
        self.cut_after_barrier = cut_after_barrier
        self.records = []
        self.calls = []
        self.seq = 0
        self.barriers = 0
        self.marked = 0
        self.head_hash = GENESIS_HASH

    def mark(self):
        """Forget the seeded records: what follows is the leg's own writing."""
        self.marked = len(self.records)
        self.calls.clear()
        return self

    def append(self, record):
        assert isinstance(record, dict), record
        assert set(record) == set(CALLER_KEYS), sorted(record)
        assert isinstance(record["body"], dict), record["body"]
        self.seq += 1
        prev = self.head_hash
        digest = canonical_hash(record)
        envelope = {
            **record,
            "body": dict(record["body"]),
            "payload_digest": digest,
            "seq": self.seq,
            "series_id": self.series_id,
            "process_id": self.process_id,
            "release_hash": self.release_hash,
            "recorded_at_ms": self.clock.now_ms(),
            "schema_version": 1,
            "prev_hash": prev,
            "hash": hashlib.sha256((prev + digest).encode()).hexdigest(),
        }
        self.head_hash = envelope["hash"]
        self.records.append(envelope)
        self.calls.append(("append", record["kind"]))
        if self.state is not None:
            self.state.apply(envelope)
        return envelope["seq"]

    def append_many(self, records):
        return [self.append(record) for record in records]

    def barrier(self):
        self.barriers += 1
        self.calls.append(("barrier", None))
        if self.cut_after_barrier is not None and self.barriers >= self.cut_after_barrier:
            raise Cut(f"process died after barrier {self.barriers}")

    def head(self):
        return (self.seq, self.head_hash)

    @property
    def written(self):
        """Only what was appended since :meth:`mark` — the leg's own records."""
        return self.records[self.marked:]

    def kinds(self):
        return [envelope["kind"] for envelope in self.written]

    def bodies(self, kind):
        return [e["body"] for e in self.written if e["kind"] == kind]

    def ids(self, kind):
        return [e["id"] for e in self.written if e["kind"] == kind]

    def one(self, kind):
        bodies = self.bodies(kind)
        assert len(bodies) == 1, f"{kind}: {len(bodies)} records"
        return bodies[0]


# ---------------------------------------------------------------------------
# Collaborator fakes — one knob each, so a test moves exactly one thing
# ---------------------------------------------------------------------------


class FakeAccounting:
    """`snapshot` mirrors the fold, so leg 2 really sees leg 1's reservation.

    T7 pins `AccountState.positions/working/balances` as the fold's, and R8
    pins `asof_ms == at_ms`; `stale_by_ms` backdates only `asof_ms`, which is
    the one input step (3)'s evidence-age gate reads.
    """

    def __init__(self, *, risk_effect="reduce", stale_by_ms=0, tokens=("etok-1", ("atok-1",))):
        self.risk_effect = risk_effect
        self.stale_by_ms = stale_by_ms
        self.tokens = tokens
        self.snapshots = []
        self.classifications = []

    def snapshot(self, view, executor, quotes, at_ms, requirements, calendar):
        self.snapshots.append((view, executor, quotes, at_ms, tuple(requirements), calendar))
        executor_token, accounting_tokens = self.tokens
        return AccountState(
            risk_version=RiskVersion(
                economic_seq=view.risk_version.economic_seq,
                executor_token=executor_token,
                accounting_tokens=accounting_tokens,
            ),
            asof_ms=at_ms - self.stale_by_ms,
            # The digest moves with the snapshot instant as well as the
            # requirements, so a plan that bound the tick-assembly account
            # instead of the step-(2) refresh is visible rather than
            # coincidentally equal (§5.16).
            evidence_digest=canonical_hash(
                [at_ms] + [r.requirement_digest for r in requirements]
            ),
            balances=tuple(
                Balance(currency=ccy, total=amount, available=amount, native=None)
                for ccy, amount in sorted(view.balances.items())
            ),
            positions=tuple(view.positions),
            working=tuple(view.working.values()),
            measure_evidence={},
            source_digests={"paper": "s" * 64},
        )

    def classify(self, proposal, state):
        self.classifications.append((proposal, state))
        return self.risk_effect

    def value(self, view, quotes, at_ms):
        return Decimal("1000")

    def source_tokens(self, executor, at_ms):
        return self.tokens


class FakeExecutor:
    """`submit(intent, permit, state)` — the leg's only route to the venue (D14)."""

    def __init__(self, *, ack=None, scope=SCOPE, venue_time_ms=NOW_MS, ledger=None,
                 status="open"):
        self.scope = scope
        self._ack = ack
        self._status = status
        self._venue_time_ms = venue_time_ms
        self.ledger = ledger
        self.submits = []
        self.calls_at_entry = []

    def execution_scope(self):
        return self.scope

    def venue_time_ms(self):
        return self._venue_time_ms

    def submit(self, intent, permit, state):
        self.submits.append((intent, permit, state))
        if self.ledger is not None:
            self.calls_at_entry.append(list(self.ledger.calls))
            self.ledger.calls.append(("submit", None))
        if self._ack is not None:
            return self._ack
        return Ack(
            client_ref=intent.client_ref,
            venue_ref="v-1",
            status=self._status,
            ts_ms=permit.valid_until_ms - 1,
            filled_qty=Decimal("0"),
            avg_price=None,
            fee=Decimal("0"),
            reason="",
            native={},
        )


class BoomVerifier:
    """The leg never calls the verifier: `SubmittingExecutor.submit` is the route."""

    def verify_and_call(self, *args, **kwargs):
        raise AssertionError(
            "the leg reached SubmissionVerifier directly; §5.14 routes it through "
            "SubmittingExecutor.submit(intent, permit, state)"
        )


class FakeHealth:
    """The health state machine's current `HEALTH_STATES` member.

    `Health.state` is a PROPERTY (§5.11), so the double is one too: a fake
    that answered a callable is what let `leg.py` call `health.state()` and
    raise `TypeError` against every real `Health`.
    """

    def __init__(self, state="ready"):
        self._state = state

    @property
    def state(self):
        return self._state


class FakeInbox:
    """The control spool: a queued-but-unfolded command still reaches the epoch."""

    def __init__(self, pending=()):
        self._pending = tuple(pending)

    def pending(self):
        return self._pending


class FakeReadiness:
    """`verdict_for(view, at_ms)` — T13's one owner of "expired => no_go"."""

    def __init__(self, verdict="go"):
        self.verdict = verdict
        self.calls = []

    def verdict_for(self, view, at_ms):
        self.calls.append((view, at_ms))
        return self.verdict

    def current(self, view, at_ms):
        return view.readiness


class FakeCalendar:
    """Open, with a session that closes far enough out to lose the minimum."""

    tz_name = "UTC"

    def __init__(self, *, open_=True, close_ms=NOW_MS + 3_600_000):
        self.open = open_
        self.close_ms = close_ms

    def is_open(self, at_ms):
        return self.open

    def window(self, kind, at_ms):
        return (NOW_MS - 3_600_000, self.close_ms)


class FakeAuthorityTable:
    """`for_origin(origin, breaker)` — `compose.py`'s table, faked to one entry.

    The real table is `compose.py`'s and is tested there; here it exists so the
    leg's step (6) can be exercised without importing the composition root.
    """

    def __init__(self, by_origin, *, refuse_outside_reducing=True):
        self.by_origin = dict(by_origin)
        self.refuse_outside_reducing = refuse_outside_reducing
        self.calls = []

    def for_origin(self, origin, breaker):
        self.calls.append((origin, breaker))
        if origin == "reduction" and self.refuse_outside_reducing and breaker != "reducing":
            raise ProductionError([f"a reduction authority is illegal while {breaker!r}"])
        return self.by_origin[origin]


class Present:
    """A bundle member the leg never touches: present, and loud if used."""

    def __init__(self, label):
        self.label = label

    def __getattr__(self, name):
        raise AssertionError(f"the leg used {self.label}.{name}, which it has no business reading")


class AcceptingVerifier(ApprovalVerifier):
    """A live-capable `ApprovalVerifier` so a real `Arming` can be constructed.

    The leg never verifies a proof — `Arming.check_conjunction` and
    `apply_scope` read the fold — but `Arming` refuses a non-live-capable
    verifier at a live rung, so the scenario needs one that constructs.
    """

    LIVE_CAPABLE = True
    _PARAMS = ()

    def verify(self, canonical_bytes, proof, purpose):
        self.check_purpose(purpose)
        return VerifiedPrincipal(
            id=f"{purpose}-principal", proof_digest=canonical_hash(purpose)
        )


# ---------------------------------------------------------------------------
# Builders — the leg's inputs, each rebuildable from one changed member
# ---------------------------------------------------------------------------


def document(rung="live_limited"):
    """The §4.1 document at a live rung — the six thresholds the leg reads."""
    return ServeDocument.from_obj(live_capable_document(rung))


def proposal(*, instrument=INSTRUMENT, side="sell", qty="10", limit="0.41",
             expires_ms=NOW_MS + 600_000, pid="cand-1"):
    """One proposal, sized so the `size` guard allows it."""
    return Proposal(
        id=pid,
        instrument=instrument,
        side=side,
        qty=None if qty is None else Decimal(qty),
        notional=None,
        limit=None if limit is None else Decimal(limit),
        tif="ioc",
        expires_ms=expires_ms,
        reference_price=Decimal("0.41"),
        exposure=Decimal("4.10"),
        direction="short",
        confidence=0.61,
        prediction=0.58,
        baseline=0.50,
        expected_value=0.03,
        inputs_asof_ms=NOW_MS - 1_000,
        inputs_digest=INPUTS_DIGEST,
        coverage_digest=COVERAGE_DIGEST,
        quote_asof_ms=NOW_MS - 1_000,
        quote_digest=QUOTE_DIGEST,
        extra={},
    )


def entry_batch(*, data_asof_ms=NOW_MS - 1_000):
    """The frozen batch this tick decided from; `data_asof_ms` is the oldest key."""
    return EntryBatch(
        outputs={},
        watermarks_by_key={
            INSTRUMENT: InputWatermark(
                key=INSTRUMENT, latest_asof_ms=data_asof_ms, source_digest="1" * 64
            ),
            OTHER_INSTRUMENT: InputWatermark(
                key=OTHER_INSTRUMENT, latest_asof_ms=data_asof_ms + 500, source_digest="2" * 64
            ),
        },
        required_keys_digest=canonical_hash([INSTRUMENT, OTHER_INSTRUMENT]),
        coverage_digest=COVERAGE_DIGEST,
        data_asof_ms=data_asof_ms,
        inputs_digest=INPUTS_DIGEST,
        source_config_hash=SOURCE_CONFIG_HASH,
    )


def quote_set(*, min_asof_ms=NOW_MS - 1_000):
    """One quote per instrument; `min_asof_ms` is the oldest."""
    return QuoteSet(
        quotes=(
            Quote(
                instrument=INSTRUMENT,
                bid=Decimal("0.40"),
                ask=Decimal("0.42"),
                mid=Decimal("0.41"),
                asof_ms=min_asof_ms,
            ),
        ),
        quote_digest=QUOTE_DIGEST,
        min_asof_ms=min_asof_ms,
    )


def guard_chain(*, size_max="100", exposure_max="20000", on_size_breach="refuse"):
    """The §4.1 `size` / `exposure` / `sane` guards — no evidence required."""
    return GuardChain(
        {
            "size": Limit(
                {"measure": "quantity", "bound": {"max": size_max}, "on_breach": on_size_breach},
                name="size",
            ),
            "exposure": Limit(
                {
                    "measure": "exposure_after",
                    "scope": "aggregate",
                    "include_working": True,
                    "bound": {"max": exposure_max},
                    "on_breach": "refuse",
                },
                name="exposure",
            ),
            "sane": RangeGuard(
                {"field": "confidence", "min": 0, "max": 1, "nan": "refuse"}, name="sane"
            ),
        }
    )


def arming_state(*, until_ms=NOW_MS + 1_800_000, allowlist=(INSTRUMENT, OTHER_INSTRUMENT),
                 overlay=None, authority_id="auth-arm-1", rung="live_limited"):
    """The ordinary arm, as ruling R5 embeds it in the `authority` issue body.

    The fold projects it into `StateView.arming`; seeding the RECORD rather
    than the projection is what makes the leg's reads a property of the fold.
    """
    return ArmingState(
        authority_id=authority_id,
        release_hash=RELEASE_HASH,
        rung=rung,
        maker="maker-1",
        checker="checker-1",
        armed_at_ms=NOW_MS - 60_000,
        armed_until_ms=until_ms,
        allowlist=allowlist,
        limits_overlay=overlay or {},
        request_proof_digest="1" * 64,
        approval_proof_digest="2" * 64,
    )


def readiness_projection(*, evaluated_at_ms=NOW_MS - 60_000, verdict="go"):
    """A GO whose `valid_until_ms` agrees with `document.readiness.valid_for_s`."""
    return ReadinessProjection(
        verdict=verdict,
        items=[
            {
                "item": "startup_reconciled",
                "required": True,
                "evidence": "recon-1",
                "waiver": None,
                "passed": True,
            }
        ],
        readiness_digest=READINESS_DIGEST,
        evaluated_at_ms=evaluated_at_ms,
        valid_until_ms=evaluated_at_ms + READINESS_VALID_FOR_S * 1000,
    )


def signed_reduction(*, index=0, expires_ms=NOW_MS + 900_000, prop=None):
    """What a maker signs at `flatten-request` time: the seven §5.4 fields."""
    stored = prop if prop is not None else proposal(pid="cand-red-1")
    return ReductionIntent(
        release_hash=RELEASE_HASH,
        request_id=REQUEST_ID,
        index=index,
        candidate=Candidate(id=stored.id, instrument=stored.instrument, scope_keys=(INSTRUMENT,)),
        proposal=stored,
        risk_state_digest=RISK_STATE_DIGEST_SIGNED,
        expires_ms=expires_ms,
    )


def reduction_authorization(digests, *, expires_ms=NOW_MS + 900_000):
    """The checker's grant: one single-use right per named digest."""
    return ReductionAuthorization(
        authority_id="auth-red-1",
        release_hash=RELEASE_HASH,
        request_id=REQUEST_ID,
        reduction_intent_digests=tuple(digests),
        expires_ms=expires_ms,
    )


def tick_state(view, account, *, feed_status="live", feed_ages=(), calendar=None, batch=None):
    """A `TickState`, carrying the frozen `EntryBatch` when the field exists.

    §5.14 rules `(intent, permit, state)` is the verifier's only route and the
    gate must rehash the batch, so `tests/production/test_verifier.py` pins the
    batch as a sixth `TickState` member. This builder passes it when
    `state.py` declares it, so this file agrees with that group either way and
    no assertion here depends on which is true.
    """
    names = {f.name for f in dataclasses.fields(TickState)}
    extra = {"entry_batch": batch} if "entry_batch" in names else {}
    return TickState(
        view=view,
        account=account,
        feed_status=feed_status,
        feed_ages=tuple(feed_ages),
        calendar=calendar if calendar is not None else FakeCalendar(),
        **extra,
    )


# ---------------------------------------------------------------------------
# The scenario — one consistent leg, every collaborator reachable
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class Scenario:
    """Everything one `LegPipeline` needs, mutually consistent and mutable."""

    doc: object
    release: object
    clock: object
    state: object
    ledger: object
    accounting: object
    executor: object
    lease: object
    lease_permit: object
    calendar: object
    guards: object
    health: object
    inbox: object
    readiness: object
    arming: object
    authorities: object
    id_source: object
    bindings: object
    action_policy: object

    def parts(self, **replace):
        """The nine positional arguments `LegPipeline` takes (§5.16)."""
        parts = (
            self.doc,
            self.release,
            self.bindings,
            Schedule(
                clock=self.clock,
                calendar=self.calendar,
                cadence=Present("cadence"),
                overrun=Present("overrun"),
            ),
            Decision(guards=self.guards, monitors={}),
            Safety(
                breaker=Present("breaker"),
                arming=self.arming,
                authorities=self.authorities,
                readiness=self.readiness,
                invocation=Invocation(
                    armed=True, env_release_hash=RELEASE_HASH, once=False, max_ticks=None
                ),
                action_policy=self.action_policy,
                transition_policy=Present("transition_policy"),
                submission_verifier=BoomVerifier(),
            ),
            Execution(
                executor=self.executor,
                accounting=self.accounting,
                lease=self.lease,
                resilience=Present("resilience"),
            ),
            Recording(
                ledger=self.ledger,
                state=self.state,
                inbox=self.inbox,
                reconciler=Present("reconciler"),
                checkpoint=Present("checkpoint"),
                journal_hook=Present("journal_hook"),
                id_source=self.id_source,
            ),
            Observability(
                metrics=Present("metrics"),
                alerts=Present("alerts"),
                health=self.health,
                heartbeat=Present("heartbeat"),
            ),
        )
        names = ("document", "release", "bindings", "schedule", "decision", "safety",
                 "execution", "recording", "observability")
        listed = list(parts)
        for name, value in replace.items():
            listed[names.index(name)] = value
        return tuple(listed)

    def pipeline(self, cls=None):
        """Build the `LegPipeline` (or a spy subclass) over this scenario."""
        return (cls or LegPipeline)(*self.parts())

    def rebind(self, **changes):
        """Return the scenario with `LegBindings` members replaced."""
        self.bindings = dataclasses.replace(self.bindings, **changes)
        return self

    def view(self):
        """The fold as it stands now."""
        return self.state.snapshot()


class FakeRelease:
    """The `ReleaseManifest` members the leg binds: the hash and the universe."""

    def __init__(self, release_hash=RELEASE_HASH):
        self.release_hash = release_hash
        self.feed_spec = {"required_keys": [INSTRUMENT, OTHER_INSTRUMENT]}


def seed_state(*, breaker="active", arm=None, ready=None, reduction=None):
    """A real `SeriesState` folded to the projections a live leg needs.

    Built by appending real §6 records through the folding ledger, never by
    constructing a `StateView` by hand: the point of the cumulative-reservation
    tests is that the fold is what carries a leg's effect to the next leg.
    """
    state = SeriesState(SERIES_ID)
    clock = TestClock(start_ms=NOW_MS)
    ledger = FoldingLedger(state, clock)
    ledger.append({"kind": "tick_start", "id": f"tick_start:{TICK_ID}",
                   "body": {"tick_id": TICK_ID, "tick_at_ms": NOW_MS,
                            "release_hash": RELEASE_HASH}})
    if arm is not None:
        ledger.append(
            {
                "kind": "authority",
                "id": f"authority:{arm.authority_id}",
                "body": {
                    "authority_id": arm.authority_id,
                    "role": "ordinary",
                    "event": "issue",
                    "arming": arm.to_obj(),
                },
            }
        )
    if ready is not None:
        ledger.append(
            {
                "kind": "readiness",
                "id": f"readiness:{ready.readiness_digest}",
                "body": {"release_hash": RELEASE_HASH, **ready.to_obj()},
            }
        )
    if reduction is not None:
        ledger.append(
            {
                "kind": "authority",
                "id": f"authority:{reduction.authority_id}",
                "body": {
                    "authority_id": reduction.authority_id,
                    "role": "reduction",
                    "event": "issue",
                    "authorization": reduction.to_obj(),
                },
            }
        )
    if breaker != "active":
        ledger.append(
            {
                "kind": "trip",
                "id": f"trip:seed:{breaker}",
                "body": {"from": "active", "to": breaker, "reason": "monitor_alarm",
                         "actor": "seed", "control_request_id": None,
                         "principal_digest": None, "proof_digest": None,
                         "acknowledged_trip_id": None},
            }
        )
    return state, clock, ledger


def build(*, origin="model", rung="live_limited", breaker=None, reduction=None,
          prop=None, guards=None, accounting=None, health="ready", readiness="go",
          pending_control=(), lease_ms=None, batch=None, quotes=None,
          venue_time_ms=None, authorities=None, leg_index=0, cut_after_barrier=None,
          reduction_expires_ms=None):
    """Assemble one consistent scenario; every knob moves exactly one member."""
    doc = document(rung)
    release = FakeRelease()
    # §5.16: no ordinary arm exists at shadow/paper, and none exists while
    # `reducing` — D10/D12 revoke it on leaving `active`.
    arm = arming_state(rung=rung) if (origin == "model" and rung in LIVE_RUNGS) else None
    grant = None
    binding = None
    if origin == "reduction":
        signed = reduction if reduction is not None else signed_reduction(
            prop=prop if prop is not None else proposal(pid="cand-red-1")
        )
        digest = signed.reduction_intent_digest()
        grant = reduction_authorization(
            [digest], **({} if reduction_expires_ms is None
                         else {"expires_ms": reduction_expires_ms})
        )
        binding = ReductionBinding(signed=signed, digest=digest, right=digest)
        prop = signed.proposal
    state, clock, ledger = seed_state(
        breaker=breaker or ("reducing" if origin == "reduction" else "active"),
        arm=arm,
        ready=readiness_projection(verdict=readiness),
        reduction=grant,
    )
    ledger.cut_after_barrier = cut_after_barrier
    for request_id in pending_control:
        ledger.append(
            {
                "kind": "control_request",
                "id": f"control_request:{request_id}",
                "body": {"request_id": request_id, "purpose": "halt",
                         "payload": {}, "release_hash": RELEASE_HASH,
                         "principal_digest": None, "proof_digest": "9" * 64,
                         "expires_ms": NOW_MS + 60_000},
            }
        )
    ledger.mark()
    account_source = accounting if accounting is not None else FakeAccounting()
    executor = FakeExecutor(ledger=ledger, venue_time_ms=venue_time_ms or NOW_MS)
    lease = ProcessLease({}, clock=clock)
    lease_permit = lease.acquire(SCOPE, HOLDER, lease_ms or LEASE_TTL_MS)
    chain = guards if guards is not None else guard_chain()
    calendar = FakeCalendar()
    the_batch = batch if batch is not None else entry_batch()
    the_quotes = quotes if quotes is not None else quote_set()
    view = state.snapshot()
    # The tick's `account` phase ran a moment before this leg — so the
    # tick-assembly account and step (2)'s refresh are distinguishable.
    account = account_source.snapshot(
        view, executor, the_quotes, NOW_MS - TICK_ASSEMBLY_LAG_MS, (), calendar
    )
    id_source = ReleaseIdSource(RELEASE_HASH)
    arming = Arming(
        doc, release, serve_root=_ServeRootStub(), verifier=AcceptingVerifier({}), clock=clock
    )
    bindings = LegBindings(
        proposal=prop if prop is not None else proposal(),
        origin=origin,
        entry_batch=the_batch,
        head_digest=canonical_hash({"head": "picks"}),
        quotes=the_quotes,
        state=tick_state(view, account, calendar=calendar, batch=the_batch),
        requirements=(),
        reduction=binding,
        release=release,
        rung=rung,
        tick_id=TICK_ID,
        leg_id=id_source.leg_id(TICK_ID, leg_index),
        leg_index=leg_index,
    )
    # The tick assembly's own snapshot is not the leg's: forget it, so
    # `accounting.snapshots[0]` is always the step-(2) refresh.
    account_source.snapshots.clear()
    scenario = Scenario(
        doc=doc,
        release=release,
        clock=clock,
        state=state,
        ledger=ledger,
        accounting=account_source,
        executor=executor,
        lease=lease,
        lease_permit=lease_permit,
        calendar=calendar,
        guards=chain,
        health=FakeHealth(health),
        inbox=FakeInbox(),
        readiness=FakeReadiness(readiness),
        arming=arming,
        authorities=authorities or default_authorities(),
        id_source=id_source,
        bindings=bindings,
        action_policy=ActionPolicy(),
    )
    return scenario


class _ServeRootStub:
    """The one `ServeRoot` accessor `Arming` needs — a path it may cache to."""

    def __init__(self, tmp=None):
        self.arming_cache = str(Path(tmp or "/nonexistent-serve") / "arming.json")


def default_authorities():
    """`for_origin` answering the two origins with the classes under test."""
    return FakeAuthorityTable({"model": None, "reduction": None})


def wire_authorities(scen, *, model=None, reduction=None):
    """Build the three `Authority` objects over `scen` and install the table."""
    parts = (
        scen.clock,
        scen.calendar,
        scen.arming,
        scen.lease,
        scen.health,
        scen.executor,
        scen.doc,
        scen.release,
        scen.ledger,
        scen.inbox,
    )
    table = FakeAuthorityTable(
        {
            "model": (model or LiveAuthority)(*parts),
            "reduction": (reduction or ReductionAuthority)(*parts),
        }
    )
    scen.authorities = table
    return table


@pytest.fixture
def live_leg():
    """A live_limited model leg that submits: the shape everything else moves."""
    scen = build()
    wire_authorities(scen)
    return scen


@pytest.fixture
def reduction_leg():
    """A live_limited reduction leg in `reducing`, with its right unreserved."""
    scen = build(origin="reduction")
    wire_authorities(scen)
    return scen


@pytest.fixture
def shadow_leg():
    """A shadow model leg: `SimulatedAuthority`, nothing outward-authorising."""
    scen = build(rung="shadow")
    wire_authorities(scen, model=SimulatedAuthority, reduction=SimulatedAuthority)
    return scen


def run(scen, cls=None):
    """Run the leg and return its `LegResult`."""
    return scen.pipeline(cls).run()


# ---------------------------------------------------------------------------
# `run` is final, and walks LEG_STEPS
# ---------------------------------------------------------------------------


class SpyLeg(LegPipeline):
    """Records each step in call order — the seam a subclass is allowed to use."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.steps = []

    def guard(self):
        self.steps.append("guard")
        return super().guard()

    def refresh(self, final, findings):
        self.steps.append("refresh")
        return super().refresh(final, findings)

    def rebind(self, account):
        self.steps.append("rebind")
        return super().rebind(account)

    def plan(self, evaluation):
        self.steps.append("plan")
        return super().plan(evaluation)

    def intent(self, plan):
        self.steps.append("intent")
        return super().intent(plan)

    def authorize(self, intent, plan):
        self.steps.append("authorize")
        return super().authorize(intent, plan)

    def act(self, intent, permit):
        self.steps.append("act")
        return super().act(intent, permit)

    def fold(self, intent, permit, ack, findings):
        self.steps.append("fold")
        return super().fold(intent, permit, ack, findings)


def test_a_subclass_that_overrides_run_refuses_at_class_creation():
    """`run` is final (§5.13.1): "record before act" is enforced by the base,
    not by convention inside eight methods. A subclass that could reorder or
    skip a barrier must be impossible, not merely discouraged — the same
    standard "abstract means `@abstractmethod`" applies in the other
    direction."""
    with pytest.raises(ProductionError) as exc:
        type("BadLeg", (LegPipeline,), {"run": lambda self: None})
    assert "run" in str(exc.value)


def test_a_subclass_may_override_a_step_and_still_inherits_the_final_run():
    """The steps ARE overridable (§5.13.1) — that is the seam. Only the walk
    is closed, so `type(self).run is LegPipeline.run` for every subclass."""
    assert SpyLeg.run is LegPipeline.run


def test_run_walks_exactly_the_eight_leg_steps_in_vocab_order(live_leg):
    """`vocab.LEG_STEPS` is the order, and `run` walks it — not a hand-written
    sequence of eight calls that a later edit could reorder."""
    spy = live_leg.pipeline(SpyLeg)
    spy.run()
    assert tuple(spy.steps) == vocab.LEG_STEPS


def test_the_eight_step_names_are_public_methods_of_the_pipeline():
    """Every `LEG_STEPS` member resolves to a method: a walk over names that
    do not exist would fail at run time rather than at import."""
    missing = [name for name in vocab.LEG_STEPS if not callable(getattr(LegPipeline, name, None))]
    assert not missing, missing


# ---------------------------------------------------------------------------
# The declared shapes — §5.13.1's field lists, restated independently
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "cls,expected",
    [
        (LegBindings, EXPECTED_BINDINGS_FIELDS),
        (LegEvaluation, EXPECTED_EVALUATION_FIELDS),
        (LegResult, EXPECTED_RESULT_FIELDS),
    ],
    ids=["LegBindings", "LegEvaluation", "LegResult"],
)
def test_the_three_leg_values_carry_exactly_their_declared_fields_in_order(cls, expected):
    """Field ORDER is the constructor contract and the digest order (§5.4); a
    member that moved would silently swap two values."""
    assert tuple(f.name for f in dataclasses.fields(cls)) == expected


@pytest.mark.parametrize("cls", [LegBindings, LegEvaluation, LegResult], ids=lambda c: c.__name__)
def test_the_three_leg_values_are_frozen(cls):
    """§5.13.1 calls them frozen accumulators: a step that mutated one would
    make the recorded plan and the submitted intent disagree."""
    assert dataclasses.fields(cls) and cls.__dataclass_params__.frozen


def test_leg_bindings_reduction_is_none_for_a_model_leg(live_leg):
    """`origin` is a declared value and `reduction` is what makes it real:
    a model leg carries no signed intent to build an `Intent` around."""
    assert live_leg.bindings.origin == "model"
    assert live_leg.bindings.reduction is None


def test_the_reduction_binding_carries_signed_digest_and_right(reduction_leg):
    """`arming._bound_digest` reads `.signed` and `.digest`; step (5) reads
    `.right`. All three are members, so the check §5.13.1 requires is
    structural rather than a convention two modules share."""
    binding = reduction_leg.bindings.reduction
    assert tuple(f.name for f in dataclasses.fields(ReductionBinding)) == (
        "signed",
        "digest",
        "right",
    )
    assert isinstance(binding.signed, ReductionIntent)
    assert binding.digest == binding.signed.reduction_intent_digest()


# ---------------------------------------------------------------------------
# Barrier order — the whole interleaved sequence, not a count
# ---------------------------------------------------------------------------


def test_a_live_model_leg_barriers_plan_then_intent_then_authorization_then_submits(live_leg):
    """D13: each of `decision_plan`, `intent` and `authorization` crosses its
    OWN barrier before `executor.submit`, and the outcome is recorded after.
    A barrier in the right count but the wrong place is the bug that loses an
    order on a crash, so the whole sequence is the assertion."""
    result = run(live_leg)
    assert result.result == "open"
    assert live_leg.ledger.calls == [
        ("append", "decision_plan"),
        ("barrier", None),
        ("append", "intent"),
        ("barrier", None),
        ("append", "authorization"),
        ("barrier", None),
        ("submit", None),
        ("append", "order_event"),
        ("barrier", None),
    ]


def test_a_live_reduction_leg_appends_authority_use_before_the_authorization(reduction_leg):
    """§6: `authority_use` is "barrier before authorization"; §5.13.1 says the
    `ReductionAuthority` appends it FIRST. The reservation must be durable
    before anything says the right was converted into a permit."""
    run(reduction_leg)
    assert reduction_leg.ledger.calls == [
        ("append", "decision_plan"),
        ("barrier", None),
        ("append", "intent"),
        ("barrier", None),
        ("append", "authority_use"),
        ("barrier", None),
        ("append", "authorization"),
        ("barrier", None),
        ("submit", None),
        ("append", "order_event"),
        ("barrier", None),
    ]


def test_a_simulated_leg_writes_no_authority_record_at_all(shadow_leg):
    """`SimulatedAuthority` "writes nothing" (§5.13.1): at shadow there is no
    live permit to authorise, so an `authorization` row would be a record of
    an authority that does not exist."""
    run(shadow_leg)
    assert shadow_leg.ledger.calls == [
        ("append", "decision_plan"),
        ("barrier", None),
        ("append", "intent"),
        ("barrier", None),
        ("submit", None),
        ("append", "order_event"),
        ("barrier", None),
    ]


def test_the_executor_is_entered_only_after_every_authority_barrier(live_leg):
    """Checked from INSIDE the executor rather than inferred from the final
    order: what matters is what was durable at the instant the venue could
    have been reached."""
    run(live_leg)
    at_entry = live_leg.executor.calls_at_entry[0]
    assert at_entry[-1] == ("barrier", None)
    assert at_entry.count(("barrier", None)) == 3
    assert [kind for verb, kind in at_entry if verb == "append"] == [
        "decision_plan",
        "intent",
        "authorization",
    ]


def test_the_leg_reaches_the_venue_only_through_submitting_executor_submit(live_leg):
    """§5.14: `SubmittingExecutor.submit(intent, permit, state)` is "the only
    route it has"; the `SubmissionVerifier` is the LiveExecutor wrapper's
    collaborator, not the leg's. A `BoomVerifier` in the bundle proves it."""
    run(live_leg)
    assert len(live_leg.executor.submits) == 1
    intent, permit, state = live_leg.executor.submits[0]
    assert isinstance(intent, Intent)
    assert isinstance(permit, ActPermit)
    assert isinstance(state, TickState)


def test_submit_receives_the_step_two_tick_state_not_the_tick_assembly_one(live_leg):
    """§5.14 names `state` "the leg's step-(2) `TickState`" — the verifier
    rechecks hard guards against the refreshed account, and the tick-assembly
    account predates every earlier leg of this tick."""
    run(live_leg)
    _intent, _permit, state = live_leg.executor.submits[0]
    assert state is not live_leg.bindings.state
    assert state.account is not live_leg.bindings.state.account
    refreshed = live_leg.accounting.snapshots[0]
    assert state.view == refreshed[0]
    assert state.account.asof_ms == refreshed[3]


# ---------------------------------------------------------------------------
# Record ids and bodies (§6, ruling R9)
# ---------------------------------------------------------------------------


def test_record_ids_are_kind_qualified_and_unique_across_the_series(live_leg):
    """R9: an `id` is unique across the SERIES and the index is keyed by id
    alone, so every producer qualifies its id with the kind."""
    result = run(live_leg)
    ids = [envelope["id"] for envelope in live_leg.ledger.records]
    assert len(ids) == len(set(ids))
    assert f"decision_plan:{result.plan_id}" in ids
    assert f"intent:{result.client_ref}" in ids
    assert f"authorization:{result.client_ref}" in ids
    assert f"order_event:{result.client_ref}" in ids


def test_the_authority_use_id_names_the_authority_and_the_right(reduction_leg):
    """§6 makes `(authority_id, reduction_intent_digest)` the uniqueness rule
    for `authority_use`, so the record id is what enforces it: a replayed
    `execute-flatten` cannot reserve the same right twice."""
    binding = reduction_leg.bindings.reduction
    grant = reduction_leg.view().reduction
    run(reduction_leg)
    assert reduction_leg.ledger.ids("authority_use") == [
        f"authority_use:{grant.authority_id}:{binding.digest}"
    ]


def test_the_authorization_body_is_the_permit_plus_an_authority_use_id(live_leg):
    """Ruling R5 pins the body as `{permit, authority_use_id}`; the ordinary
    path has no use to name, and §6 says the key is present and null rather
    than absent, so a reader never has to guess which path wrote it."""
    run(live_leg)
    body = live_leg.ledger.one("authorization")
    assert set(body) == {"permit", "authority_use_id"}
    assert body["authority_use_id"] is None
    assert set(body["permit"]) == {f.name for f in dataclasses.fields(ActPermit)}
    assert ActPermit.from_obj(body["permit"]) == live_leg.executor.submits[0][1]


def test_a_reduction_authorization_names_its_authority_use(reduction_leg):
    """The permit and the reservation that paid for it are joined in the
    record, or a reader cannot tell which right a live order consumed."""
    run(reduction_leg)
    body = reduction_leg.ledger.one("authorization")
    assert body["authority_use_id"] == reduction_leg.ledger.ids("authority_use")[0]


def test_the_authority_use_body_carries_the_right_the_client_ref_and_the_stamp(reduction_leg):
    """§6's four members. `client_ref` is what recovery queries; without it a
    crash between the reservation and the submit cannot be resolved."""
    binding = reduction_leg.bindings.reduction
    grant = reduction_leg.view().reduction
    result = run(reduction_leg)
    body = reduction_leg.ledger.one("authority_use")
    assert body == {
        "authority_id": grant.authority_id,
        "reduction_intent_digest": binding.digest,
        "client_ref": result.client_ref,
        "reserved_at_ms": reduction_leg.clock.now_ms(),
    }


def test_the_intent_record_body_is_the_canonical_intent_value(live_leg):
    """§6: "the canonical `records.Intent` value object; no second schema"."""
    result = run(live_leg)
    assert live_leg.ledger.one("intent") == result.intent.to_obj()


def test_the_order_event_records_the_ack_the_venue_returned(live_leg):
    """Step (8) records the outcome (§5.13); §6's `order_event` is where it
    lands, joined to the intent by `client_ref`."""
    result = run(live_leg)
    body = live_leg.ledger.one("order_event")
    assert body["client_ref"] == result.client_ref
    assert body["status"] == result.ack.status
    assert body["venue_ref"] == result.ack.venue_ref


# ---------------------------------------------------------------------------
# The client ref — allocated by `run` BEFORE step (1) (§5.16)
# ---------------------------------------------------------------------------


def test_a_model_leg_client_ref_is_the_release_tick_leg_attempt_id(live_leg):
    """§5.16: allocated by `run` before step (1), not at step 5 — a guard
    refusal terminalizes at step (4) and §6's `decision.legs[]` still needs a
    `client_ref` for that leg."""
    result = run(live_leg)
    assert result.client_ref == live_leg.id_source.client_ref(TICK_ID, 0, 0)


def test_a_reduction_leg_client_ref_is_the_flatten_recipe(reduction_leg):
    """D12 fixes the flatten ref as `H("flatten-v1", release_hash,
    reduction_request_id, zero_based_intent_index, reduction_intent_digest)`,
    independent of CLI/process time, ledger sequence or retries — which is
    what makes a crash-recovery query deterministic."""
    signed = reduction_leg.bindings.reduction.signed
    result = run(reduction_leg)
    assert result.client_ref == reduction_leg.id_source.flatten_client_ref(
        signed.release_hash, signed.request_id, signed.index, signed.reduction_intent_digest()
    )


def test_a_guard_refused_leg_still_carries_its_client_ref(live_leg):
    """The whole reason §5.16 moves the allocation before step (1)."""
    live_leg.guards = guard_chain(size_max="1")
    result = run(live_leg)
    assert result.result == "not_sent"
    assert result.client_ref == live_leg.id_source.client_ref(TICK_ID, 0, 0)


# ---------------------------------------------------------------------------
# Step (1)-(2) refusal: terminal at step (4), no intent
# ---------------------------------------------------------------------------


def test_a_guard_refusal_terminalizes_at_step_four_with_no_intent(live_leg):
    """§5.13 step (4): "a refusal terminalizes as `not_sent` without an
    intent". The plan record IS the terminal record — nothing else was
    written, and nothing reached the venue."""
    live_leg.guards = guard_chain(size_max="1")
    result = run(live_leg)
    assert result.result == "not_sent"
    assert result.intent is None
    assert result.ack is None
    assert live_leg.ledger.kinds() == ["decision_plan"]
    assert live_leg.ledger.one("decision_plan")["result"] == "not_sent"
    assert live_leg.executor.submits == []


def test_a_terminalized_leg_still_carries_plan_id_digest_and_final(live_leg):
    """§5.13.1: `plan_id`, `plan_digest` and `final` are `LegResult` members
    precisely because a guard refusal never reaches step (5), yet §6's
    `decision.legs[]` and `decision_plan_ids[]` must still be written."""
    live_leg.guards = guard_chain(size_max="1")
    result = run(live_leg)
    assert result.plan_id == live_leg.id_source.plan_id(TICK_ID, 0)
    assert len(result.plan_digest) == 64
    assert result.final is not None
    assert result.leg_id == live_leg.bindings.leg_id
    assert result.findings


def test_the_recorded_plan_digest_is_the_digest_of_the_recorded_plan(live_leg):
    """§5.4: `decision_plan_digest = canonical_hash(DecisionPlan)` over its
    eighteen fields in declared order. The `LegResult` carries the digest and
    the ledger carries the body; the two must be one object."""
    result = run(live_leg)
    body = live_leg.ledger.one("decision_plan")
    assert DecisionPlan.from_obj(body).decision_plan_digest() == result.plan_digest


def test_an_amendment_at_step_one_is_what_the_plan_and_the_intent_bind(live_leg):
    """D9 lets a guard reduce one declared scalable field; §5.16 sources
    `DecisionPlan.final` and `Intent.proposal` from `LegEvaluation.final`, so
    the amended size is what reaches the venue and what was recorded."""
    live_leg.guards = GuardChain(
        {
            "size": Limit(
                {
                    "measure": "quantity",
                    "bound": {"max": "4"},
                    "on_breach": "amend",
                },
                name="size",
            )
        }
    )
    result = run(live_leg)
    assert result.final.qty == Decimal("4")
    assert result.intent.proposal.qty == Decimal("4")
    assert live_leg.ledger.one("decision_plan")["final"]["qty"] == "4"
    assert live_leg.ledger.one("decision_plan")["original"]["qty"] == "10"


# ---------------------------------------------------------------------------
# Step (2) — a FRESH fold, and the refreshed account is what everything binds
# ---------------------------------------------------------------------------


def test_refresh_takes_a_fresh_snapshot_rather_than_reusing_the_tick_view(live_leg):
    """§5.13 step (2): "prior legs of this tick have appended and folded their
    own reservations and acks, and only a fresh fold carries them". Something
    is folded between the tick assembly and this leg, so a `refresh` that
    reused `bindings.state.view` lands on the older head."""
    before = live_leg.bindings.state.view
    live_leg.ledger.append(
        {
            "kind": "guard_state",
            "id": "guard_state:size:OTHER",
            "body": {"guard": "size", "scope_key": OTHER_INSTRUMENT, "state_kind": "hold",
                     "reason": "an earlier leg held another scope",
                     "held_until_ms": NOW_MS + 60_000, "resume_at_ms": None, "finding": {}},
        }
    )
    run(live_leg)
    view, _executor, _quotes, _at, _reqs, _cal = live_leg.accounting.snapshots[0]
    assert view.head_seq == before.head_seq + 1
    assert ("size", OTHER_INSTRUMENT) in view.guard_holds


def test_the_account_the_plan_binds_is_the_refreshed_one(live_leg):
    """§5.16: `evidence_asof_ms`/`evidence_digest` come from the refreshed
    snapshot of step 2, "never `bindings.state.account`", so the plan, intent
    and permit all bind ONE `AccountState`."""
    stale = live_leg.bindings.state.account
    result = run(live_leg)
    refreshed = live_leg.accounting.snapshots[0]
    body = live_leg.ledger.one("decision_plan")
    assert body["evidence_asof_ms"] == refreshed[3] == live_leg.clock.now_ms()
    assert result.intent.evidence_asof_ms == body["evidence_asof_ms"]
    assert result.intent.evidence_digest == body["evidence_digest"]
    assert stale is not None


def test_refresh_asks_accounting_for_the_requirement_union_the_tick_built(live_leg):
    """§5.13.1: the leg "cannot recompute" the requirements — that call needs
    every candidate and a leg holds one proposal — so `bindings.requirements`
    is what reaches `Accounting.snapshot` (R8)."""
    requirement_bearing = build()
    wire_authorities(requirement_bearing)
    run(requirement_bearing)
    _view, _ex, _q, at_ms, requirements, calendar = requirement_bearing.accounting.snapshots[0]
    assert requirements == requirement_bearing.bindings.requirements
    assert at_ms == requirement_bearing.clock.now_ms()
    assert calendar is requirement_bearing.calendar


def test_risk_effect_is_accountings_answer_for_the_final_proposal(live_leg):
    """§5.16: `risk_effect` is set by step 2 from
    `execution.accounting.classify(final, state)` — this is the step that
    holds both the final proposal and the refreshed account. D10 rules
    accounting the exclusive classifier; no model claim may set it."""
    result = run(live_leg)
    final_proposal, state = live_leg.accounting.classifications[0]
    assert final_proposal == result.final
    assert isinstance(state, TickState)
    assert live_leg.ledger.one("decision_plan")["risk_effect"] == "reduce"


def test_the_scope_verdict_comes_from_the_guard_chains_authority_gate(live_leg):
    """D9/D11: `GuardChain.check_authority_scope` re-applies the current
    allowlist and overlay to the EXACT final proposal, immediately before a
    permit exists to be minted."""
    result = run(live_leg)
    body = live_leg.ledger.one("decision_plan")
    assert body["scope_verdict"] == {
        "allowed": True,
        "scope_key": result.final.instrument,
        "reason": "",
    }


def test_an_unarmed_shadow_leg_still_submits(shadow_leg):
    """PLAN GAP (safety-critical, §5.5 vs §5.16). §5.5 rules
    `check_authority_scope` runs "immediately before permit" against "the
    active ordinary-arm or reduction-authorization" — and §5.16 rules that at
    shadow/paper there IS no arm. `Arming.apply_scope(proposal, None)` answers
    `not_armed`, so a leg that terminalized on `allowed is False` could never
    submit at shadow, which is the rung the whole package is supposed to run
    at first. Pinned: the leg RECORDS the verdict and `ActionPolicy` decides —
    its `simulated_submit` rule allows below live and the authority axis is
    inert there — so no leg-side rung branch is needed. The plan should say so
    in §5.5."""
    result = run(shadow_leg)
    assert shadow_leg.view().arming is None
    assert result.result == "open"
    assert len(shadow_leg.executor.submits) == 1


def test_an_instrument_outside_the_arming_allowlist_refuses_before_any_intent(live_leg):
    """D11: the arm's allowlist is re-applied immediately before each ordinary
    live submit. An instrument the maker never approved must not reach step
    (5)."""
    live_leg.rebind(proposal=proposal(instrument="INS3"))
    result = run(live_leg)
    assert result.result == "not_sent"
    assert result.intent is None
    assert live_leg.ledger.one("decision_plan")["scope_verdict"]["allowed"] is False


# ---------------------------------------------------------------------------
# Step (3) — the gates, one refusal test per member
# ---------------------------------------------------------------------------


def test_the_happy_path_records_one_gate_result_per_declared_gate(live_leg):
    """§5.13 step (3) lists what is re-evaluated and §6's `decision_plan`
    carries `gate_results[]` — plural. A gate chain that stopped at the first
    failure would record an audit trail with a hole in it, so every gate is
    evaluated and every result recorded."""
    run(live_leg)
    body = live_leg.ledger.one("decision_plan")
    gates = tuple(g["gate"] for g in body["gate_results"])
    assert set(gates) == set(EXPECTED_GATES)
    assert len(gates) == len(EXPECTED_GATES)
    assert all(g["passed"] for g in body["gate_results"])
    assert all(g["at_ms"] == live_leg.clock.now_ms() for g in body["gate_results"])


def test_the_declared_gate_vocabulary_is_exactly_the_seventeen_step_three_members():
    """Restated independently above (a list read from its subject asserts
    nothing) — this is the two-way pin between the module and §5.13 step
    (3)."""
    assert tuple(leg_module.LEG_GATES) == EXPECTED_GATES


def failing_gate(scen):
    """Run the leg and return the single `GateResult` that did not pass."""
    result = run(scen)
    body = scen.ledger.one("decision_plan")
    failed = [g for g in body["gate_results"] if not g["passed"]]
    assert len(failed) == 1, failed
    assert body["result"] == "not_sent"
    assert result.intent is None
    assert scen.executor.submits == []
    return failed[0]


def test_a_watermark_older_than_max_staleness_refuses_naming_watermark_age():
    """D6: the input deadline is the OLDEST watermark plus `max_staleness_ms`
    — one fresh instrument may never hide a stale input."""
    scen = build(batch=entry_batch(data_asof_ms=NOW_MS - MAX_STALENESS_MS - 1))
    wire_authorities(scen)
    gate = failing_gate(scen)
    assert gate["gate"] == "watermark_age"
    assert str(scen.doc.schedule.max_staleness_ms) in gate["reason"]


def test_a_quote_older_than_max_quote_age_refuses_naming_quote_age():
    """§5.13 step (3) re-checks quote age against the document knob; the
    permit's `valid_until_ms` binds the same term, so a quote already past it
    could never have produced a valid permit."""
    scen = build(quotes=quote_set(min_asof_ms=NOW_MS - MAX_QUOTE_AGE_MS - 1))
    wire_authorities(scen)
    gate = failing_gate(scen)
    assert gate["gate"] == "quote_age"


def test_accounting_evidence_older_than_max_valuation_age_refuses_naming_evidence_age():
    """The account is the sole economic authority inside a guard (§5.8.1);
    stale evidence must refuse rather than size an order from it."""
    scen = build(accounting=FakeAccounting(stale_by_ms=MAX_VALUATION_AGE_MS + 1))
    wire_authorities(scen)
    gate = failing_gate(scen)
    assert gate["gate"] == "evidence_age"


def test_a_venue_clock_beyond_max_venue_skew_refuses_naming_venue_skew():
    """§5.13.1 names `document.schedule.max_venue_skew_ms` as one of the four
    document thresholds the leg exists to enforce."""
    scen = build(venue_time_ms=NOW_MS + MAX_VENUE_SKEW_MS + 1)
    wire_authorities(scen)
    gate = failing_gate(scen)
    assert gate["gate"] == "venue_skew"


def test_a_degraded_health_state_refuses_naming_health():
    """D10: degraded and unhealthy refuse submit in every rung."""
    scen = build(health="degraded")
    wire_authorities(scen)
    gate = failing_gate(scen)
    assert gate["gate"] == "health"
    assert "degraded" in gate["reason"]


def test_a_halted_breaker_refuses_naming_breaker():
    """D10: halted refuses submit. The breaker is read from the FRESH fold
    (§5.16), so an earlier leg's halt verdict is visible here."""
    scen = build(breaker="halted")
    wire_authorities(scen)
    gate = failing_gate(scen)
    assert gate["gate"] == "breaker"
    assert "halted" in gate["reason"]


def test_a_lost_lease_refuses_naming_lease():
    """The permit binds `lease_scope`/`fencing_token`; a leg without the grip
    cannot mint one, and D14's gateway would reject a stale fence anyway —
    refusing here is what stops the attempt from reaching the venue at all."""
    scen = build()
    wire_authorities(scen)
    scen.lease.release(scen.lease_permit)
    gate = failing_gate(scen)
    assert gate["gate"] == "lease"


def test_a_rung_that_disagrees_with_the_document_refuses_naming_rung():
    """§5.13.1: `rung` is a binding member because step (3) re-evaluates the
    document rung. A leg carrying a promoted rung is the one thing arming
    exists to make impossible (D11: a request "cannot promote the rung")."""
    scen = build()
    wire_authorities(scen)
    scen.rebind(rung="live")
    gate = failing_gate(scen)
    assert gate["gate"] == "rung"


def test_a_readiness_no_go_refuses_naming_readiness():
    """D11/§5.13: every live rung requires a current GO record bound to the
    exact release before arming or submit."""
    scen = build(readiness="no_go")
    wire_authorities(scen)
    gate = failing_gate(scen)
    assert gate["gate"] == "readiness"


def test_a_closed_calendar_refuses_naming_calendar():
    """§5.13 step (3) re-evaluates the calendar: a session that closed between
    the tick's fetch and this leg must stop the leg, not the next tick."""
    scen = build()
    wire_authorities(scen)
    scen.calendar.open = False
    gate = failing_gate(scen)
    assert gate["gate"] == "calendar"


def test_an_executor_scope_that_moved_refuses_naming_executor_scope():
    """§5.7: the executor's authenticated `execution_scope()` must equal the
    graded document and release scope at startup, every tick and the final
    gate; disagreement refuses."""
    scen = build()
    wire_authorities(scen)
    scen.executor.scope = ExecutionScope(venue="paper", account="strategy-b")
    gate = failing_gate(scen)
    assert gate["gate"] == "executor_scope"


def test_the_gates_read_the_document_thresholds_and_hold_none_of_their_own(live_leg):
    """§4.1 rules that code holds no threshold — which is exactly why
    §5.13.1 gives `LegPipeline` the `document`. Moving a knob must move the
    gate; a literal in `leg.py` would not."""
    loose = build(
        batch=entry_batch(data_asof_ms=NOW_MS - MAX_STALENESS_MS - 1),
    )
    obj = live_capable_document("live_limited")
    obj["schedule"]["max_staleness_ms"] = MAX_STALENESS_MS * 10
    loose.doc = ServeDocument.from_obj(obj)
    wire_authorities(loose)
    result = run(loose)
    assert result.result != "not_sent"


# ---------------------------------------------------------------------------
# Step (4) — the DecisionPlan, all eighteen fields reachable (§5.16)
# ---------------------------------------------------------------------------


def plan_sources(scen, result):
    """§5.16's producer row per `DecisionPlan` field, as values."""
    bindings = scen.bindings
    body = scen.ledger.one("decision_plan")
    intent = result.intent
    return {
        "plan_id": scen.id_source.plan_id(TICK_ID, bindings.leg_index),
        "inputs_asof_ms": bindings.entry_batch.data_asof_ms,
        "inputs_digest": bindings.entry_batch.inputs_digest,
        "coverage_digest": bindings.entry_batch.coverage_digest,
        "quote_asof_ms": bindings.quotes.min_asof_ms,
        "quote_digest": bindings.quotes.quote_digest,
        "evidence_asof_ms": intent.evidence_asof_ms,
        "evidence_digest": intent.evidence_digest,
        "provenance_digests": {
            "entry": bindings.entry_batch.inputs_digest,
            "head": bindings.head_digest,
            "candidate": bindings.proposal.id,
        },
        "original": bindings.proposal.to_obj(),
        "final": result.final.to_obj(),
        "findings": [f.to_obj() for f in result.findings],
        "gate_results": body["gate_results"],
        "scope_verdict": body["scope_verdict"],
        "risk_effect": scen.accounting.risk_effect,
        "risk_version": intent.risk_version.to_obj(),
        "risk_state_digest": intent.risk_state_digest,
        "result": "submit",
    }


def test_every_decision_plan_field_has_the_producer_section_five_sixteen_names(live_leg):
    """All eighteen fields, each traced to a `LegBindings` member, a
    `LegEvaluation` member, an `IdSource` id or step 4's own verdict — the
    closure check §5.16 exists to make mechanical."""
    result = run(live_leg)
    body = live_leg.ledger.one("decision_plan")
    expected = plan_sources(live_leg, result)
    assert set(expected) == {f.name for f in dataclasses.fields(DecisionPlan)}
    assert body == expected


def test_the_plan_binds_the_same_evidence_the_intent_and_the_permit_do(live_leg):
    """One `AccountState` for all three (§5.16): a plan that bound a different
    snapshot from the permit would make step (7)'s exact-version recheck a
    comparison between two different observations."""
    result = run(live_leg)
    _intent, permit, _state = live_leg.executor.submits[0]
    body = live_leg.ledger.one("decision_plan")
    assert (
        body["evidence_asof_ms"]
        == result.intent.evidence_asof_ms
        == permit.evidence_asof_ms
    )
    assert body["evidence_digest"] == result.intent.evidence_digest == permit.evidence_digest
    assert body["risk_version"] == result.intent.risk_version.to_obj() == permit.risk_version.to_obj()


# ---------------------------------------------------------------------------
# Step (5) — the Intent, sixteen fields (§5.16)
# ---------------------------------------------------------------------------


def test_every_intent_field_has_the_producer_section_five_sixteen_names(live_leg):
    """The Intent is the canonical value the ledger serializes and the permit
    hashes; a field sourced from the wrong place is a permit that binds
    something the plan never recorded."""
    result = run(live_leg)
    intent = result.intent
    bindings = live_leg.bindings
    body = live_leg.ledger.one("decision_plan")
    expected = {
        "client_ref": result.client_ref,
        "decision_plan_id": result.plan_id,
        "decision_plan_digest": result.plan_digest,
        "proposal": result.final,
        "created_ms": live_leg.clock.now_ms(),
        "authority_id": live_leg.view().arming.authority_id,
        "release_hash": bindings.release.release_hash,
        "inputs_asof_ms": bindings.entry_batch.data_asof_ms,
        "inputs_digest": bindings.entry_batch.inputs_digest,
        "coverage_digest": bindings.entry_batch.coverage_digest,
        "quote_asof_ms": bindings.quotes.min_asof_ms,
        "quote_digest": bindings.quotes.quote_digest,
        "evidence_asof_ms": body["evidence_asof_ms"],
        "evidence_digest": body["evidence_digest"],
        "risk_version": RiskVersion.from_obj(body["risk_version"]),
        "risk_state_digest": body["risk_state_digest"],
    }
    assert set(expected) == {f.name for f in dataclasses.fields(Intent)}
    assert {name: getattr(intent, name) for name in expected} == expected


def test_intent_authority_id_admits_none_because_shadow_and_paper_have_no_arm():
    """PLAN GAP (safety-critical, §5.16 vs `records.py`). §5.16 rules
    `Intent.authority_id` "None at shadow/paper, where no ordinary arm
    exists", but the built `Intent` annotates it `str` and refuses `None`, so
    no shadow or paper leg can construct one. The narrowest fix is the
    annotation: `authority_id: str | None`. Recording a placeholder id instead
    would make the ledger claim an authority that was never issued, and
    `intent_digest` covers the field — so two intents under different arms
    must not hash alike, which a placeholder would defeat."""
    hints = {f.name: f.type for f in dataclasses.fields(Intent)}
    assert "None" in str(hints["authority_id"]), hints["authority_id"]


def test_a_shadow_intent_carries_no_authority_id(shadow_leg):
    """§5.16: `authority_id` is None "at shadow/paper, where no ordinary arm
    exists". A live-looking authority on a simulated intent would make the
    ledger claim an authority that was never issued."""
    result = run(shadow_leg)
    assert result.intent.authority_id is None


def test_a_reduction_intent_carries_the_reduction_authority_id(reduction_leg):
    """§5.16: for a reduction leg the `authority_id` comes from
    `bindings.reduction` — during `reducing` no ordinary arm exists, so
    sourcing it from `arming` alone would leave it empty on the one path that
    needs it."""
    grant = reduction_leg.view().reduction
    result = run(reduction_leg)
    assert result.intent.authority_id == grant.authority_id


# ---------------------------------------------------------------------------
# The §5.4 rebuild check — the signed order is the order that reaches the venue
# ---------------------------------------------------------------------------


def test_the_constructed_intent_rederives_the_signed_reduction_intent_digest(reduction_leg):
    """§5.4's rebuild check, verbatim: `release_hash` and `proposal` come from
    the CONSTRUCTED `Intent`; `candidate`, `request_id`, `index`, `expires_ms`
    and `risk_state_digest` from `bindings.reduction.signed`, because the last
    four are not `Intent` fields and cannot be recovered from one. That the
    digest is unchanged is what pins economic content — the order that reaches
    the venue is the order signed."""
    signed = reduction_leg.bindings.reduction.signed
    result = run(reduction_leg)
    rebuilt = ReductionIntent(
        release_hash=result.intent.release_hash,
        request_id=signed.request_id,
        index=signed.index,
        candidate=signed.candidate,
        proposal=result.intent.proposal,
        risk_state_digest=signed.risk_state_digest,
        expires_ms=signed.expires_ms,
    )
    assert rebuilt.reduction_intent_digest() == signed.reduction_intent_digest()
    assert rebuilt.reduction_intent_digest() == reduction_leg.bindings.reduction.digest


def test_an_amended_reduction_proposal_refuses_because_the_rebuilt_digest_moves():
    """The rebuild check is not decoration: a guard that reduced the signed
    quantity would send the venue an order the maker never approved. §5.13.1
    makes step (5) refuse "if the rebuilt `reduction_intent_digest` does not
    match the right being consumed"."""
    scen = build(origin="reduction", guards=GuardChain(
        {
            "size": Limit(
                {"measure": "quantity", "bound": {"max": "4"}, "on_breach": "amend"}, name="size"
            )
        }
    ))
    wire_authorities(scen)
    result = run(scen)
    assert result.final.qty == Decimal("4")
    assert result.result == "not_sent"
    assert result.intent is None
    assert "intent" not in scen.ledger.kinds()
    assert "authority_use" not in scen.ledger.kinds()
    assert scen.executor.submits == []


def test_a_right_that_names_another_digest_refuses_before_any_reservation():
    """The right is single-use and names ONE digest. A binding whose right
    does not match its own signed intent must never reserve — a reservation is
    what makes the right unusable afterwards."""
    scen = build(origin="reduction")
    wire_authorities(scen)
    binding = scen.bindings.reduction
    scen.rebind(reduction=dataclasses.replace(binding, right="9" * 64))
    result = run(scen)
    assert result.result == "not_sent"
    assert result.intent is None
    assert "authority_use" not in scen.ledger.kinds()
    assert scen.executor.submits == []


def test_the_reduction_intents_signed_candidate_is_the_legs_source_of_scope():
    """§5.4: "the signed `candidate` is the leg's only source of scope keys,
    so there is nothing for it to diverge from". The plan's scope verdict must
    key on the signed candidate's instrument."""
    scen = build(origin="reduction")
    wire_authorities(scen)
    signed = scen.bindings.reduction.signed
    run(scen)
    body = scen.ledger.one("decision_plan")
    assert body["scope_verdict"]["scope_key"] == signed.candidate.instrument
    assert body["provenance_digests"]["candidate"] == signed.candidate.id


def test_the_signed_risk_state_digest_is_not_re_verified_at_execution():
    """§5.4 is explicit: positions move legitimately as earlier legs of the
    same plan fill, so requiring the signed digest to match would make every
    multi-intent plan fail after its first leg. The leg binds THIS tick's
    digest and lets accounting's `reduce` classification defend the gap."""
    scen = build(origin="reduction")
    wire_authorities(scen)
    signed = scen.bindings.reduction.signed
    result = run(scen)
    assert signed.risk_state_digest == RISK_STATE_DIGEST_SIGNED
    assert result.intent.risk_state_digest != RISK_STATE_DIGEST_SIGNED
    assert result.result == "open"


# ---------------------------------------------------------------------------
# Step (6) — the fresh view, the drift refusal, and Authority polymorphism
# ---------------------------------------------------------------------------


class TrippingAuthority(LiveAuthority):
    """Trips the breaker between steps (5) and (6) — the drift step (6) exists for.

    An earlier leg of the same tick tripping the breaker is exactly the case
    §5.13 names: "a later leg reading a stale `active` would mint a live permit
    the action matrix forbids".
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.minted = 0

    def mint(self, intent, plan, state_view, reduction):
        self.minted += 1
        return super().mint(intent, plan, state_view, reduction)


def test_a_trip_folded_between_steps_five_and_six_refuses_without_minting():
    """§5.13 step (6): the view is a FRESH fold and any member the plan and
    intent already bound that has moved refuses the attempt. The `breaker` is
    named as one of the four members an earlier leg of this same tick can
    change."""
    scen = build()
    table = wire_authorities(scen, model=TrippingAuthority)
    authority = table.by_origin["model"]

    class TrippingLeg(LegPipeline):
        def intent(self, plan):
            built = super().intent(plan)
            scen.ledger.append(
                {
                    "kind": "trip",
                    "id": "trip:mid-leg",
                    "body": {"from": "active", "to": "halted", "reason": "monitor_alarm",
                             "actor": "monitor", "control_request_id": None,
                             "principal_digest": None, "proof_digest": None,
                             "acknowledged_trip_id": None},
                }
            )
            return built

    result = TrippingLeg(*scen.parts()).run()
    assert result.result == "not_sent"
    assert authority.minted == 0
    assert "authorization" not in scen.ledger.kinds()
    assert scen.executor.submits == []


class RecordingAuthority(LiveAuthority):
    """Keeps the `state_view` it was minted from, so step (6)'s fold is checkable."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.views = []

    def mint(self, intent, plan, state_view, reduction):
        self.views.append(state_view)
        return super().mint(intent, plan, state_view, reduction)


def test_step_six_mints_from_a_view_that_already_carries_this_legs_intent(live_leg):
    """Three fresh folds — steps (2), (3) and (6) — because "freshness is what
    the DECISION members need". The view step (6) mints from must be later
    than the intent step (5) appended, or the "any bound member that moved"
    check is being made against a fold that predates the leg's own writing."""
    table = wire_authorities(live_leg, model=RecordingAuthority)
    authority = table.by_origin["model"]
    result = run(live_leg)
    assert len(authority.views) == 1
    view = authority.views[0]
    assert result.client_ref in view.pending
    assert view.head_seq == live_leg.bindings.state.view.head_seq + 2


def test_the_authority_is_chosen_by_origin_and_the_fresh_breaker(reduction_leg):
    """§5.13.1: `safety.authorities.for_origin(origin, breaker)` — "a table
    lookup on declared values, no rung test". The breaker passed is the fresh
    one, or a reduction authority could be selected in a state the transition
    policy already left."""
    run(reduction_leg)
    assert reduction_leg.authorities.calls == [("reduction", "reducing")]


def test_a_reduction_authority_is_refused_outside_reducing():
    """D10/D12: reduction authority exists only in `reducing`. The table
    refuses, and the leg terminalizes rather than minting."""
    scen = build(origin="reduction", breaker="active")
    wire_authorities(scen)
    result = run(scen)
    assert result.result == "not_sent"
    assert scen.executor.submits == []


def test_simulated_authority_returns_a_simulated_permit_and_writes_nothing(shadow_leg):
    """§5.13.1: shadow/paper "returns a `SimulatedPermit` and writes nothing"
    — there is no live authority to record, and a row claiming one would be
    false."""
    result = run(shadow_leg)
    _intent, permit, _state = shadow_leg.executor.submits[0]
    assert isinstance(permit, SimulatedPermit)
    assert not isinstance(permit, ActPermit)
    assert "authorization" not in shadow_leg.ledger.kinds()
    assert "authority_use" not in shadow_leg.ledger.kinds()
    assert permit.client_ref == result.client_ref
    assert permit.plan_id == result.plan_id
    assert permit.decision_plan_digest == result.plan_digest


def test_live_authority_mints_an_act_permit_and_appends_one_authorization(live_leg):
    """§5.13.1: `LiveAuthority` "mints an `ActPermit` and appends/barriers the
    `authorization`" — exactly one, because §6 says one per permitted live
    intent."""
    run(live_leg)
    _intent, permit, _state = live_leg.executor.submits[0]
    assert isinstance(permit, ActPermit)
    assert live_leg.ledger.kinds().count("authorization") == 1


def test_only_an_authority_constructs_an_act_permit():
    """§5.4: "only an `Authority` constructs an `ActPermit`"; D14: "Minting has
    exactly one home." An `ActPermit` built anywhere else in `leg.py` — in a
    step method, in a helper — would be a live authorisation minted outside
    the seam that exists to own it."""
    tree = ast.parse(Path(leg_module.__file__).read_text(encoding="utf-8"))
    owners = set()
    total = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        for inner in ast.walk(node):
            if isinstance(inner, ast.Call) and isinstance(inner.func, ast.Name):
                if inner.func.id == "ActPermit":
                    owners.add(node.name)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            total += node.func.id == "ActPermit"
    assert total, "leg.py is where ActPermit is constructed"
    assert owners and owners <= {"Authority", "LiveAuthority", "ReductionAuthority"}, sorted(owners)


def test_mint_takes_the_reduction_binding_because_state_view_cannot_supply_it():
    """PLAN GAP (safety-critical, §5.13.1 vs §5.16). `mint(intent, plan,
    state_view)` cannot mint a reduction permit: `ActPermit`'s
    `reduction_right_digest` is "copied from `bindings.reduction.digest`" and
    neither the `Intent` (16 declared fields) nor the `StateView` (14) carries
    it — `ReductionIntent`'s `candidate`, `request_id`, `index` and
    `expires_ms` are not `Intent` fields, so it cannot be recomputed either.
    Pinned as a fourth positional argument on all three subclasses, so no
    subclass strengthens a precondition of its base (§5.15)."""
    names = tuple(inspect.signature(Authority.mint).parameters)
    assert names == ("self", "intent", "plan", "state_view", "reduction")
    for cls in (SimulatedAuthority, LiveAuthority, ReductionAuthority):
        assert tuple(inspect.signature(cls.mint).parameters) == names


def test_authority_mint_is_abstract_on_the_base():
    """§5.15: a seam ABC's hook is abstract, so an incomplete subclass refuses
    to CONSTRUCT rather than failing at the moment it would have minted."""
    assert getattr(Authority.mint, "__isabstractmethod__", False)
    with pytest.raises(TypeError):
        Authority(*([None] * 10))


def test_there_is_no_authority_registry():
    """§5.13.1: "There is deliberately no `AUTHORITY_KINDS`" — an authority
    mints the object that authorises real money, so it must not be reachable
    through the document's `pkg.module:Class` doorway."""
    assert not hasattr(leg_module, "AUTHORITY_KINDS")
    assert not [name for name in leg_module.__all__ if name.endswith("_KINDS")]


# ---------------------------------------------------------------------------
# `valid_until_ms` — the minimum of the nine terms §5.13 step (6) lists
# ---------------------------------------------------------------------------


def nine_terms(scen, permit):
    """The nine `valid_until_ms` terms, each read from its own owner."""
    doc = scen.doc
    view = scen.view()
    lease_permit = scen.lease.current(SCOPE)
    return {
        "proposal_expiry": scen.bindings.proposal.expires_ms,
        "input_staleness": scen.bindings.entry_batch.data_asof_ms + doc.schedule.max_staleness_ms,
        "quote_age": scen.bindings.quotes.min_asof_ms + doc.schedule.max_quote_age_ms,
        "evidence_age": permit.evidence_asof_ms + doc.accounting.max_valuation_age_ms,
        "readiness": view.readiness.valid_until_ms,
        "calendar_close": scen.calendar.close_ms,
        "authority_expiry": view.arming.armed_until_ms,
        "lease_expiry": lease_permit.expires_ms,
        "submit_timeout": permit.checked_at_ms + doc.execution.submit_timeout_ms,
    }


def test_valid_until_is_the_minimum_of_the_nine_terms(live_leg):
    """§5.13 step (6) states the nine and §5.13.1 says they are "stated once
    there and never restated, because an authority that dropped proposal
    expiry, input staleness, quote age, evidence age or readiness validity
    would mint a permit that outlives the data it binds"."""
    run(live_leg)
    _intent, permit, _state = live_leg.executor.submits[0]
    terms = nine_terms(live_leg, permit)
    assert len(terms) == 9
    assert permit.valid_until_ms == min(terms.values())


BINDING_TERM_CASES = (
    "proposal_expiry",
    "input_staleness",
    "quote_age",
    "evidence_age",
    "readiness",
    "calendar_close",
    "authority_expiry",
    "lease_expiry",
    "submit_timeout",
)


@pytest.mark.parametrize("term", BINDING_TERM_CASES)
def test_each_of_the_nine_terms_can_be_the_binding_one(term):
    """Nine tests, because a minimum over eight terms passes every "happy
    path" assertion ever written. Each case makes exactly one term the strict
    minimum and asserts the permit took it."""
    tight = NOW_MS + 2_000
    scen = build(
        prop=proposal(expires_ms=tight) if term == "proposal_expiry" else None,
        batch=entry_batch(data_asof_ms=tight - MAX_STALENESS_MS)
        if term == "input_staleness"
        else None,
        quotes=quote_set(min_asof_ms=tight - MAX_QUOTE_AGE_MS) if term == "quote_age" else None,
        accounting=FakeAccounting(stale_by_ms=NOW_MS + MAX_VALUATION_AGE_MS - tight)
        if term == "evidence_age"
        else None,
        lease_ms=tight - NOW_MS if term == "lease_expiry" else None,
    )
    if term == "readiness":
        scen.state, scen.clock, scen.ledger = seed_state(
            arm=arming_state(),
            ready=readiness_projection(
                evaluated_at_ms=tight - READINESS_VALID_FOR_S * 1000
            ),
        )
        scen.ledger.mark()
        scen.executor.ledger = scen.ledger
    if term == "calendar_close":
        scen.calendar.close_ms = tight
    if term == "authority_expiry":
        scen.state, scen.clock, scen.ledger = seed_state(
            arm=arming_state(until_ms=tight), ready=readiness_projection()
        )
        scen.ledger.mark()
        scen.executor.ledger = scen.ledger
    if term == "submit_timeout":
        obj = live_capable_document("live_limited")
        obj["execution"]["submit_timeout_ms"] = tight - NOW_MS
        scen.doc = ServeDocument.from_obj(obj)
    wire_authorities(scen)
    run(scen)
    _intent, permit, _state = scen.executor.submits[0]
    terms = nine_terms(scen, permit)
    assert terms[term] == min(terms.values()), terms
    assert permit.valid_until_ms == tight


def test_a_reduction_permit_takes_the_reduction_authoritys_expiry():
    """The seventh term is "authority expiry", and in `reducing` there is no
    ordinary arm: the authority whose expiry binds is the
    `ReductionAuthorization`'s. Sourcing it from `arming` alone would leave
    the term missing — and the permit unbounded — on the one path that needs
    it (§5.16). The grant here expires before every other term, so it must be
    the minimum the permit took."""
    tight = NOW_MS + 2_000
    scen = build(origin="reduction", reduction_expires_ms=tight)
    wire_authorities(scen)
    grant = scen.view().reduction
    assert grant.expires_ms == tight
    run(scen)
    _intent, permit, _state = scen.executor.submits[0]
    assert permit.valid_until_ms == tight


# ---------------------------------------------------------------------------
# ActPermit field sources (§5.16)
# ---------------------------------------------------------------------------


def permit_sources(scen, result, permit):
    """§5.16's source per `ActPermit` field; three are digests with recipes."""
    view = scen.view()
    lease_permit = scen.lease.current(SCOPE)
    intent = result.intent
    return {
        "plan_id": result.plan_id,
        "decision_plan_digest": result.plan_digest,
        "client_ref": intent.client_ref,
        "valid_until_ms": min(nine_terms(scen, permit).values()),
        "authority_id": view.arming.authority_id,
        "release_hash": intent.release_hash,
        "intent_digest": intent.intent_digest(),
        "instrument": intent.proposal.instrument,
        "risk_effect": scen.accounting.risk_effect,
        "inputs_asof_ms": intent.inputs_asof_ms,
        "inputs_digest": intent.inputs_digest,
        "coverage_digest": intent.coverage_digest,
        "quote_asof_ms": intent.quote_asof_ms,
        "quote_digest": intent.quote_digest,
        "evidence_asof_ms": intent.evidence_asof_ms,
        "evidence_digest": intent.evidence_digest,
        "authority_scope_digest": permit.authority_scope_digest,
        "reduction_right_digest": None,
        "risk_version": intent.risk_version,
        "risk_state_digest": intent.risk_state_digest,
        "readiness_digest": view.readiness.readiness_digest,
        "readiness_until_ms": view.readiness.valid_until_ms,
        "lease_scope": lease_permit.scope,
        "fencing_token": lease_permit.fencing_token,
        "safety_epoch_digest": permit.safety_epoch_digest,
        "checked_at_ms": scen.clock.now_ms(),
    }


def test_the_permit_source_table_covers_every_act_permit_field(live_leg):
    """The completeness half of §5.16's check: a field added to `ActPermit`
    without a producer, or a producer naming a field that no longer exists,
    fails here."""
    result = run(live_leg)
    _intent, permit, _state = live_leg.executor.submits[0]
    assert set(permit_sources(live_leg, result, permit)) == {
        f.name for f in dataclasses.fields(ActPermit)
    }


@pytest.mark.parametrize(
    "field",
    [f.name for f in dataclasses.fields(ActPermit)],
)
def test_each_act_permit_field_comes_from_the_producer_section_five_sixteen_names(
    field, live_leg
):
    """One case per field, so a permit that bound the wrong quote as-of or the
    wrong fence fails on that field's own name rather than on a whole-object
    comparison nobody can read."""
    result = run(live_leg)
    _intent, permit, _state = live_leg.executor.submits[0]
    assert getattr(permit, field) == permit_sources(live_leg, result, permit)[field]


def test_a_reduction_permit_binds_the_right_it_consumed(reduction_leg):
    """§5.4: `ActPermit` binds BOTH digests — `intent_digest` always, and
    `reduction_right_digest` "the `reduction_intent_digest` of the right being
    consumed" — because the verifier must recompute the first to prove the
    order is the one planned and match the second to prove the right
    authorises it."""
    binding = reduction_leg.bindings.reduction
    result = run(reduction_leg)
    _intent, permit, _state = reduction_leg.executor.submits[0]
    assert permit.reduction_right_digest == binding.digest
    assert permit.intent_digest != permit.reduction_right_digest
    assert permit.intent_digest == result.intent.intent_digest()
    # And the risk state it binds is THIS tick's, not the maker's: §5.4 rules
    # the signed digest deliberately not re-verified at execution.
    assert permit.risk_state_digest == result.intent.risk_state_digest
    assert permit.risk_state_digest != binding.signed.risk_state_digest


def test_the_safety_epoch_digest_moves_when_any_member_it_covers_moves():
    """§5.4: "The safety epoch covers ... risk effect, authority,
    pending-control state and lease. Any change invalidates it." A digest that
    did not move would let the verifier accept a permit minted under a state
    that no longer holds. Both mutations here change a covered member while
    still PERMITTING a submit, so there is a permit to compare.
    """
    base = build()
    wire_authorities(base)
    run(base)

    effect = build(accounting=FakeAccounting(risk_effect="increase"))
    wire_authorities(effect)
    run(effect)

    fenced = build()
    wire_authorities(fenced)
    fenced.lease_permit = fenced.lease.renew(fenced.lease_permit)
    run(fenced)

    digests = {
        scen.executor.submits[0][1].safety_epoch_digest for scen in (base, effect, fenced)
    }
    assert len(digests) == 3, digests


def test_the_safety_epoch_digest_is_deterministic_for_one_state():
    """The other half: two mints from identical inputs must agree, or the
    verifier's exact-equality recheck could never pass."""
    first, second = (build(), build())
    wire_authorities(first)
    wire_authorities(second)
    run(first)
    run(second)
    assert (
        first.executor.submits[0][1].safety_epoch_digest
        == second.executor.submits[0][1].safety_epoch_digest
    )


def test_the_authority_scope_digest_moves_with_the_arm():
    """§5.16 sources it from "the applied ordinary `Arming` scope"; two arms
    with different allowlists must not produce one digest, or a permit could
    be replayed under an arm that never admitted the instrument."""
    wide = build()
    wire_authorities(wide)
    run(wide)
    narrow = build()
    narrow.state, narrow.clock, narrow.ledger = seed_state(
        arm=arming_state(allowlist=(INSTRUMENT,)), ready=readiness_projection()
    )
    narrow.ledger.mark()
    narrow.executor.ledger = narrow.ledger
    wire_authorities(narrow)
    run(narrow)
    assert (
        wide.executor.submits[0][1].authority_scope_digest
        != narrow.executor.submits[0][1].authority_scope_digest
    )


# ---------------------------------------------------------------------------
# The action policy (§5.14) — assembled from the FRESH view
# ---------------------------------------------------------------------------


class WatchingPolicy(ActionPolicy):
    """The real rules, with the request recorded — never a stub verdict."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.requests = []

    def permits(self, request):
        self.requests.append(request)
        return super().permits(request)


def test_the_policy_request_is_built_from_the_fresh_view_not_the_tick_one():
    """§5.16: `breaker` comes from the FRESH snapshot of steps (2)/(3)/(6) —
    "never the tick-assembly view, or a trip raised inside this tick is
    invisible"."""
    scen = build()
    wire_authorities(scen)
    scen.action_policy = WatchingPolicy()
    run(scen)
    assert scen.action_policy.requests
    request = scen.action_policy.requests[-1]
    assert request.operation == "submit"
    assert request.breaker == scen.view().breaker
    assert request.rung == scen.bindings.rung
    assert request.origin == scen.bindings.origin
    assert request.risk_effect == scen.accounting.risk_effect
    assert request.health == "ready"
    assert request.readiness == "go"
    assert request.authority == "ordinary"
    assert request.pending_control is False


def test_a_pending_control_command_blocks_the_submit():
    """§5.8/§5.14: a pending mutating command blocks the next pre-submit gate,
    enforced by `ActionPolicy` reading `PolicyRequest.pending_control`. The
    `Authority`'s tenth collaborator is the inbox for exactly this reason."""
    scen = build(pending_control=("req-halt-1",))
    wire_authorities(scen)
    result = run(scen)
    assert result.result == "not_sent"
    assert scen.executor.submits == []


def test_a_reduction_origin_while_active_is_refused_by_the_policy():
    """D10: reduction authority exists only in `reducing`; the policy rule
    `reduction_origin_requires_reducing` owns that, and the leg does not
    duplicate it by branching."""
    scen = build(origin="reduction", breaker="active")
    wire_authorities(scen).refuse_outside_reducing = False
    result = run(scen)
    assert result.result == "not_sent"
    assert scen.executor.submits == []


# ---------------------------------------------------------------------------
# Step (7)-(8) — the outcome, and the latency buckets
# ---------------------------------------------------------------------------


def test_a_not_sent_ack_from_the_executor_is_recorded_like_any_other(shadow_leg):
    """§5.7: a `ShadowExecutor` returns `Ack(status="not_sent",
    reason="shadow")` and `submit` always RETURNS an `Ack`. The outcome is a
    recorded fact, not an absence."""
    shadow_leg.executor = FakeExecutor(
        ledger=shadow_leg.ledger,
        ack=Ack(client_ref="x", venue_ref=None, status="not_sent", ts_ms=NOW_MS,
                filled_qty=Decimal("0"), avg_price=None, fee=Decimal("0"),
                reason="shadow", native={}),
    )
    result = run(shadow_leg)
    assert result.result == "not_sent"
    assert result.ack.reason == "shadow"
    assert shadow_leg.ledger.one("order_event")["status"] == "not_sent"


def test_an_unknown_outcome_is_recorded_and_never_resent(live_leg):
    """D13: "An executor call that raises after the request leaves `unknown`,
    which only `executor.order(ref)` may resolve — never a blind resend."
    §5.13 (8) stops all later legs until reconciliation."""
    live_leg.executor = FakeExecutor(ledger=live_leg.ledger, status="unknown")
    result = run(live_leg)
    assert result.result == "unknown"
    assert live_leg.ledger.one("order_event")["status"] == "unknown"
    assert len(live_leg.executor.submits) == 1


def test_the_result_is_a_member_of_the_closed_status_vocabulary(live_leg):
    """`LegResult.result` is the ack's status or `not_sent` — always a
    `vocab.STATUSES` member, so §6's `order_event` and the metrics label are
    the same closed set."""
    assert run(live_leg).result in vocab.STATUSES


#: Each step costs a distinct number of milliseconds, so any bucket that
#: swallowed the wrong step lands on a number no other combination produces.
STEP_COSTS = {name: (index + 1) * 10 for index, name in enumerate(vocab.LEG_STEPS)}


def timed_leg(clock):
    """A `LegPipeline` subclass that advances `clock` inside each step."""

    def step(name, wrapped):
        def timed(self, *args):
            clock.advance(STEP_COSTS[name])
            return wrapped(self, *args)

        timed.__name__ = name
        return timed

    return type(
        "TimedLeg",
        (LegPipeline,),
        {name: step(name, getattr(LegPipeline, name)) for name in vocab.LEG_STEPS},
    )


def test_leg_latency_is_bucketed_guard_one_to_three_authorize_four_to_six_act_seven(live_leg):
    """§6 and §5.13.1 pin the mapping: `guard` spans steps (1)-(3),
    `authorize` (4)-(6), `act` (7), and step (8) is charged to the tick. The
    two vocabularies are separate because three step names and three bucket
    names collide while meaning different spans."""
    result = run(live_leg, timed_leg(live_leg.clock))
    assert set(result.leg_latency_ms) == set(vocab.LEG_LATENCY_BUCKETS)
    assert result.leg_latency_ms["guard"] == (
        STEP_COSTS["guard"] + STEP_COSTS["refresh"] + STEP_COSTS["rebind"]
    )
    assert result.leg_latency_ms["authorize"] == (
        STEP_COSTS["plan"] + STEP_COSTS["intent"] + STEP_COSTS["authorize"]
    )
    assert result.leg_latency_ms["act"] == STEP_COSTS["act"]


def test_step_eight_is_not_charged_to_any_leg_bucket(live_leg):
    """"step (8) records the outcome and is charged to the tick" — a bucket
    that swallowed it would double-count the fold against the act deadline."""
    result = run(live_leg, timed_leg(live_leg.clock))
    assert sum(result.leg_latency_ms.values()) == sum(
        STEP_COSTS[name] for name in vocab.LEG_STEPS if name != "fold"
    )


def test_the_leg_never_reads_the_wall_clock(live_leg):
    """D20's replay parity rests on the injected clock; §5.13.1 says reaching
    for `time.time()` would break it. An AST check, because a monkeypatch only
    covers the paths a test happens to walk."""
    tree = ast.parse(Path(leg_module.__file__).read_text(encoding="utf-8"))
    imported = {
        alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in getattr(node, "names", ())
    }
    assert not {"time", "datetime", "random", "uuid", "secrets"} & imported


# ---------------------------------------------------------------------------
# Crash cuts — what survived is what the plan promised would survive (D13)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "barrier,durable",
    [
        (1, ["decision_plan"]),
        (2, ["decision_plan", "intent"]),
        (3, ["decision_plan", "intent", "authorization"]),
    ],
    ids=["after_plan", "after_intent", "after_authorization"],
)
def test_a_crash_after_each_barrier_leaves_exactly_what_it_promised(barrier, durable):
    """§5.14's "crash cuts": tests cut after every barrier and immediately
    before/after native I/O. A barrier means "this is durable"; a cut right
    after it must leave that and nothing later, and must never have
    submitted."""
    scen = build(cut_after_barrier=barrier)
    wire_authorities(scen)
    with pytest.raises(Cut):
        run(scen)
    assert scen.ledger.kinds() == durable
    assert scen.executor.submits == []


def test_a_crash_after_the_reduction_reservation_leaves_the_right_reserved():
    """D12: "completed rights stay consumed". A reservation that survived the
    crash must be visible in the fold, so recovery "may resume only the same
    reserved intent after the executor proves it was not sent" — and no second
    intent may reference it."""
    scen = build(origin="reduction", cut_after_barrier=3)
    wire_authorities(scen)
    binding = scen.bindings.reduction
    with pytest.raises(Cut):
        run(scen)
    assert scen.ledger.kinds() == ["decision_plan", "intent", "authority_use"]
    assert binding.digest in scen.view().reduction.reserved
    assert scen.executor.submits == []


def test_a_death_between_the_native_call_and_its_record_leaves_the_ambiguity():
    """§5.14's other cut: "immediately before/after native I/O". This is the
    one genuinely ambiguous instant — the request may have left. What survives
    must be the intent and the authorization, because their deterministic
    `client_ref` is what lets recovery QUERY the venue rather than resend
    (D12, D13); an outcome record must not exist, since none was observed."""
    scen = build()
    wire_authorities(scen)

    class DyingExecutor(FakeExecutor):
        def submit(self, intent, permit, state):
            super().submit(intent, permit, state)
            raise Cut("the process died after the request left")

    scen.executor = DyingExecutor(ledger=scen.ledger)
    with pytest.raises(Cut):
        run(scen)
    assert scen.ledger.kinds() == ["decision_plan", "intent", "authorization"]
    assert len(scen.executor.submits) == 1
    assert scen.ledger.one("intent")["client_ref"] in scen.view().pending


# ---------------------------------------------------------------------------
# Cumulative reservations across legs of one tick (§5.13)
# ---------------------------------------------------------------------------


def test_leg_two_sees_leg_ones_reservation_because_it_re_snapshots():
    """§5.13: "Thus cumulative exposure, working orders, position/message
    limits and group scopes include every earlier leg." Leg 1's intent and ack
    are folded before leg 2's step (2), and only a fresh fold carries them —
    which is why step (2) re-snapshots rather than reusing the tick view."""
    scen = build()
    wire_authorities(scen)
    tick_assembly_view = scen.bindings.state.view
    first = run(scen)
    assert first.result == "open"
    assert first.client_ref not in tick_assembly_view.working

    second = build_second_leg(scen)
    assert second.bindings.state.view is tick_assembly_view
    run(second)
    view, *_ = second.accounting.snapshots[0]
    assert first.client_ref in view.working


def build_second_leg(scen, prop=None):
    """A second leg of the SAME tick, over the same fold, ledger and clock.

    `state` is deliberately the ORIGINAL `TickState`: §5.13 has `Tick.run`
    assemble it ONCE after the `account` phase and put the same one in every
    leg's `LegBindings`. So the tick view a second leg is handed predates the
    first leg entirely, and only step (2)'s own re-snapshot can carry the
    first leg's reservation — which is the whole reason step (2) re-snapshots.
    """
    scen.ledger.mark()
    scen.accounting.snapshots.clear()
    scen.bindings = dataclasses.replace(
        scen.bindings,
        proposal=prop if prop is not None else proposal(pid="cand-2"),
        leg_id=scen.id_source.leg_id(TICK_ID, 1),
        leg_index=1,
    )
    return scen


def test_leg_two_exposure_guard_is_measured_against_leg_ones_working_order():
    """The money property, not the plumbing: the second leg's `exposure_after`
    limit must include the first leg's working order. A guard chain fed the
    tick-assembly account would size the second order as if the first had
    never happened — which is the failure mode that turns a per-proposal limit
    into no limit at all."""
    scen = build(
        prop=proposal(side="buy"),
        guards=guard_chain(exposure_max="5"),
        accounting=FakeAccounting(risk_effect="increase"),
    )
    wire_authorities(scen)
    first = run(scen)
    assert first.result == "open", [f.to_obj() for f in first.findings]

    second = build_second_leg(scen, prop=proposal(side="buy", pid="cand-2"))
    result = run(second)
    exposures = [f for f in result.findings if f.guard == "exposure"]
    # Step (1) judged the tick-assembly account and allowed 4.10; step (2)
    # re-ran the hard guards against the refreshed one and saw 8.20.
    assert [f.value for f in exposures] == [Decimal("4.10"), Decimal("8.20")], [
        f.to_obj() for f in exposures
    ]
    assert exposures[0].verdict == "allow"
    assert exposures[1].verdict == "refuse"
    assert result.result == "not_sent"


def test_the_second_leg_ids_differ_from_the_first(live_leg):
    """One action, one client ref (D20/§5.14): two legs of one tick must not
    collide, or the venue's idempotency key would merge two orders."""
    first = run(live_leg)
    second_scen = build_second_leg(live_leg)
    second = run(second_scen)
    assert first.client_ref != second.client_ref
    assert first.plan_id != second.plan_id
    assert first.leg_id != second.leg_id


# ---------------------------------------------------------------------------
# D14 — `intent` and `authority_use` must not advance `economic_seq`
# ---------------------------------------------------------------------------


def test_neither_the_intent_nor_the_authority_use_advances_the_economic_sequence(reduction_leg):
    """D14: "an `authority_use` is a rights reservation, not an economic one,
    and must not advance it, or every reduction submit would fail step (7)'s
    exact-version recheck against the version its own plan bound. The same
    exemption covers the leg's own `intent`"."""
    before = reduction_leg.view().risk_version.economic_seq
    result = run(reduction_leg)
    _intent, permit, _state = reduction_leg.executor.submits[0]
    assert permit.risk_version.economic_seq == before
    assert result.intent.risk_version.economic_seq == before


def test_the_version_the_permit_binds_is_the_version_step_two_bound(live_leg):
    """The recheck at step (7) compares the permit against a version refreshed
    then; if minting had re-read the sequence, the plan and the permit could
    disagree by one and the gate would refuse every live submit."""
    run(live_leg)
    body = live_leg.ledger.one("decision_plan")
    _intent, permit, _state = live_leg.executor.submits[0]
    assert permit.risk_version.to_obj() == body["risk_version"]
    assert permit.risk_state_digest == body["risk_state_digest"]


# ---------------------------------------------------------------------------
# D2 — no branch on a rung or a mode anywhere in leg.py (AST)
# ---------------------------------------------------------------------------


def test_leg_py_never_branches_on_a_rung_or_a_mode():
    """§8: "no `rung ==` or `mode ==` branch in leg.py (AST) — the name
    appears, since `LegBindings` carries a rung; branching on it is what is
    forbidden." The detector is `test_purity`'s, imported rather than copied
    (CLAUDE.md: a function is never repeated across modules)."""
    assert _branch_hits(Path(leg_module.__file__)) == []


def test_the_rung_name_does_appear_so_the_ast_test_is_not_vacuous():
    """A test that passes because the word is absent proves nothing: the leg
    binds a rung into the plan and the intent and re-checks it at step (3)."""
    source = Path(leg_module.__file__).read_text(encoding="utf-8")
    assert "rung" in source


def test_the_authority_is_selected_by_a_table_lookup_not_a_comparison(live_leg):
    """§5.13.1: "A table lookup keyed by a declared value is not the branch D2
    forbids — what D2 forbids is the loop asking what rung it is." The leg
    calls `for_origin` exactly once, with declared values."""
    run(live_leg)
    assert live_leg.authorities.calls == [("model", "active")]
