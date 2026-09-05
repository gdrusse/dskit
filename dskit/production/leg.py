"""The eight steps between a proposal and real money (plan §5.13, §5.13.1, D13, D14).

This is the money path. Every barrier the package has, every reservation, the
cumulative-risk fold and the one call that reaches a venue live here, so the
order of the steps is enforced by the base class rather than by convention
inside eight methods: :meth:`LegPipeline.run` is FINAL, walks
``vocab.LEG_STEPS``, and ``__init_subclass__`` refuses a subclass that
overrides it. The steps themselves ARE the seam — a subclass may replace any
one of them — because the invariant is the walk, not the abstractness of what
it walks.

Three of the steps take a **fresh** ``recording.state.snapshot()``. Step (2)
does because prior legs of this same tick have appended and folded their own
reservations and acks, and only a fresh fold carries them — which is what makes
cumulative exposure, working orders and per-scope limits include every earlier
leg. Step (3) does because the gates judge the world as it is now. Step (6)
does because ``breaker``, ``guard_holds``, ``working`` and ``pending`` are
exactly the members an earlier leg can change, and a later leg reading a stale
``active`` would mint a live permit the action matrix forbids. Consistency is
what the *bound* members need and freshness is what the *decision* members
need; refusing on drift gives both.

A refusal is a VALUE and a crash is an exception. A refusal before the
``decision_plan`` barrier writes only that plan, whose ``result`` is the
terminal fact; a refusal after it terminalises through step (8) by appending an
``order_event`` whose ``event`` and ``status`` are ``not_sent`` with a
synthesized ``Ack``, so a leg that reached step (5) always has an outcome
record and recovery never has to guess whether an intent was answered (D13).
Nothing here catches a ledger failure: what survived on disk is what the
barriers promised would survive.

:class:`Authority` is where D2 becomes structural. Minting has exactly one home
(D14), and it is this module: ``SimulatedAuthority`` returns a
``SimulatedPermit`` and writes nothing, ``LiveAuthority`` mints an
``ActPermit`` and appends the ``authorization``, ``ReductionAuthority`` appends
its single-use ``authority_use`` FIRST and then the ``authorization``. The leg
picks one by ``safety.authorities.for_origin(origin, breaker)`` — a table
lookup on two declared values, never a question about the rung, which
``compose.py`` alone may ask. There is deliberately no ``AUTHORITY_KINDS``: an
authority mints the object that authorises real money, so it must not be
reachable through the document's ``pkg.module:Class`` doorway.

The eight step signatures are fixed by §5.13.1 and drop four values the records
still need — the leg's ``client_ref`` (allocated before step (1), because a
guard refusal never reaches step (5) yet §6's ``decision.legs[]`` needs one),
the step-(2) ``TickState``, the ``DecisionPlan`` and the latency buckets ``run``
measures. They live for the duration of one ``run`` in a single private
carrier rather than as scattered attributes, so "what one leg threads" has one
name and no step can leave state behind for the next leg.
"""

import dataclasses
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from decimal import Decimal

from dskit.production.arming import SCOPE_REASONS, ReductionRights
from dskit.production.base import ProductionError, canonical_hash, check_digest, pin_members
from dskit.production.coordination import scope_equal
from dskit.production.guards import max_verdict
from dskit.production.records import (
    Ack,
    ActPermit,
    DecisionPlan,
    GateResult,
    Intent,
    PolicyRequest,
    ReductionIntent,
    SimulatedPermit,
)
from dskit.production.redact import get_logger, redact
from dskit.production.vocab import (
    AUTHORITY_ROLES,
    CALENDAR_WINDOWS,
    LEG_LATENCY_BUCKETS,
    LEG_ORIGINS,
    LEG_STEPS,
    ORDER_EVENTS,
    PLAN_RESULTS,
    RISK_EFFECTS,
    STATUSES,
    VERDICT_ORDER,
)

__all__ = [
    "DEFAULT_ATTEMPT",
    "LEG_GATES",
    "Authority",
    "LegBindings",
    "LegEvaluation",
    "LegPipeline",
    "LegResult",
    "LiveAuthority",
    "ReductionAuthority",
    "ReductionBinding",
    "SimulatedAuthority",
]

_LOG = get_logger("leg")

#: The gates §5.13 step (3) re-evaluates, in the order it lists them. A closed
#: vocabulary in the shape ``arming.CONJUNCTION_REASONS`` and
#: ``verifier.VERIFY_REASONS`` already use: every gate is evaluated and every
#: result is recorded, because a chain that stopped at the first failure would
#: write an audit trail with a hole in it.
LEG_GATES = (
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

#: The attempt a leg's model client reference is allocated for. A retry is a
#: new proposal through the whole path (D10 has no ``replace`` verb), so phase
#: one never allocates a second attempt.
DEFAULT_ATTEMPT = 0

#: The calendar window whose close bounds a permit: the session under way.
_CALENDAR_WINDOW = "session"

#: The operation every leg asks the action policy about.
_SUBMIT_OPERATION = "submit"

#: The health state D10 requires before any submit.
_READY_HEALTH = "ready"

#: The breaker state that refuses every submit (D12).
_HALTED_BREAKER = "halted"

_SUBMIT, _NOT_SENT = PLAN_RESULTS
_ZERO = Decimal(0)
_NOT_ARMED = "not_armed"
_SATISFIED = ""
_AMEND_RANK = VERDICT_ORDER["amend"]

pin_members("leg.py's calendar window", (_CALENDAR_WINDOW,), CALENDAR_WINDOWS)
pin_members("leg.py's plan result for a terminalised leg", (_NOT_SENT,), STATUSES)
pin_members("leg.py's unarmed scope reason", (_NOT_ARMED,), SCOPE_REASONS)

#: Which §6 ``order_event`` event one ``Ack.status`` reports. A table, because
#: "what happened" and "where the order stands" are two vocabularies and a
#: reader joins them on this row rather than on an implementer's guess.
_EVENT_BY_STATUS = pin_members(
    "leg.py's order_event table",
    {
        "pending": "ack",
        "open": "ack",
        "partial": "partial_fill",
        "pending_cancel": "status",
        "filled": "fill",
        "cancelled": "cancel",
        "expired": "expire",
        "rejected": "reject",
        "replaced": "replaced_by_venue",
        "unknown": "unknown",
        "not_sent": "not_sent",
    },
    STATUSES,
    exact=True,
)
pin_members("leg.py's order_event events", _EVENT_BY_STATUS.values(), ORDER_EVENTS)

#: Which §6 latency bucket each step's elapsed time is charged to. Step (8)
#: records the outcome and is charged to the TICK, so it names no bucket —
#: three step names and three bucket names collide while meaning different
#: spans, which is why the two vocabularies are separate.
_STEP_BUCKETS = pin_members(
    "leg.py's latency buckets",
    {
        "guard": "guard",
        "refresh": "guard",
        "rebind": "guard",
        "plan": "authorize",
        "intent": "authorize",
        "authorize": "authorize",
        "act": "act",
        "fold": None,
    },
    LEG_STEPS,
    exact=True,
)
pin_members(
    "leg.py's charged buckets",
    [bucket for bucket in _STEP_BUCKETS.values() if bucket is not None],
    LEG_LATENCY_BUCKETS,
)

#: The one step that runs however the leg ended: an outcome is a recorded fact,
#: not an absence.
_ALWAYS_RUN = ("fold",)
pin_members("leg.py's always-run step", _ALWAYS_RUN, LEG_STEPS)


# ---------------------------------------------------------------------------
# The declared values a leg is built from, threads and returns (§5.13.1)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ReductionBinding:
    """The signed reduction one leg carries: the intent, its digest and the right.

    It is what makes "the digest the right names is the digest that reaches the
    venue" checkable rather than aspirational (D12): ``signed`` carries the
    maker-approved candidate and proposal, ``digest`` is that intent's own
    ``reduction_intent_digest``, and ``right`` is the single-use grant being
    consumed. Step (5) rebuilds the digest from the constructed ``Intent`` and
    refuses if it does not match ``right``.

    Parameters
    ----------
    signed : ReductionIntent
        What the maker signed at ``flatten-request`` time.
    digest : str
        ``signed.reduction_intent_digest()``.
    right : str
        The ``reduction_intent_digest`` the authorization granted.

    Examples
    --------
    ::

        binding = ReductionBinding(
            signed=signed, digest=signed.reduction_intent_digest(),
            right=signed.reduction_intent_digest(),
        )
        binding.digest == binding.right  # True
    """

    signed: ReductionIntent
    digest: str
    right: str


@dataclass(frozen=True)
class LegBindings:
    """Everything one leg is bound to, assembled once by ``Tick.run`` (§5.13.1).

    Thirteen members, in the order §5.13.1 states them. ``state`` is the
    ``TickState`` the tick assembled after its ``account`` phase — the SAME
    object in every leg of the tick, which is why step (2) re-snapshots rather
    than reusing it. ``requirements`` is the deduplicated union
    ``GuardChain.requirements`` produced for the whole tick: a leg cannot
    recompute it, because that call needs every candidate and a leg holds one
    proposal.

    Parameters
    ----------
    proposal : Proposal
        The original, from ``propose`` or from a stored reduction plan.
    origin : str
        A ``vocab.LEG_ORIGINS`` member — where the proposal came from.
    entry_batch : EntryBatch
        The frozen batch this tick decided from.
    head_digest : str
        The head-output provenance §6 requires; the leg cannot see the outputs.
    quotes : QuoteSet
    state : TickState
    requirements : tuple of EvidenceRequirement
    reduction : ReductionBinding or None
        ``None`` for a model leg.
    release : ReleaseManifest
    rung : str
    tick_id, leg_id : str
    leg_index : int

    Examples
    --------
    ::

        bindings = LegBindings(
            proposal=proposal, origin="model", entry_batch=batch,
            head_digest="b" * 64, quotes=quotes, state=tick_state,
            requirements=(), reduction=None, release=release, rung="shadow",
            tick_id="t" * 64, leg_id="l" * 64, leg_index=0,
        )
        bindings.reduction is None  # True
    """

    proposal: object
    origin: str
    entry_batch: object
    head_digest: str
    quotes: object
    state: object
    requirements: tuple
    reduction: ReductionBinding | None
    release: object
    rung: str
    tick_id: str
    leg_id: str
    leg_index: int


@dataclass(frozen=True)
class LegEvaluation:
    """The frozen accumulator steps (1)-(3) thread and step (4) records (§5.4).

    Every ``DecisionPlan`` field is one of these members, a ``LegBindings``
    member, an id from the ``IdSource``, or step (4)'s own ``result`` — which
    is what makes §5.16's closure check mechanical.

    Parameters
    ----------
    original, final : Proposal
        The proposal as proposed and as amended.
    findings : tuple of Finding
        Every finding of steps (1) and (2), in order.
    gate_results : tuple of GateResult
        One per :data:`LEG_GATES` member.
    scope_verdict : ScopeVerdict
    account : AccountState
        The REFRESHED snapshot of step (2) — never ``bindings.state.account``.
    risk_effect : str
        ``execution.accounting.classify(final, state)``; D10 makes accounting
        the exclusive classifier.
    risk_version : RiskVersion
    risk_state_digest : str

    Examples
    --------
    ::

        evaluation = LegEvaluation(
            original=proposal, final=proposal, findings=(), gate_results=(),
            scope_verdict=verdict, account=account, risk_effect="reduce",
            risk_version=account.risk_version,
            risk_state_digest=account.risk_digest(),
        )
        evaluation.risk_effect  # 'reduce'
    """

    original: object
    final: object
    findings: tuple
    gate_results: tuple
    scope_verdict: object
    account: object
    risk_effect: str
    risk_version: object
    risk_state_digest: str


@dataclass(frozen=True)
class LegResult:
    """What one leg came to — §6's ``decision.legs[]`` is written from it.

    ``plan_id``, ``plan_digest`` and ``final`` are members precisely because a
    guard refusal terminalises at step (4) without an ``Intent``, and the
    decision record still has to be written for that leg.

    Parameters
    ----------
    result : str
        A ``vocab.STATUSES`` member: the ack's status, or ``not_sent``.
    leg_id, plan_id, plan_digest, client_ref : str
    final : Proposal
    intent : Intent or None
        ``None`` when the leg never reached step (5).
    ack : Ack or None
        ``None`` when the leg refused before the plan barrier.
    findings : tuple of Finding
    leg_latency_ms : dict
        Keyed by ``vocab.LEG_LATENCY_BUCKETS``.

    Examples
    --------
    ::

        result = LegResult(
            result="not_sent", leg_id="l" * 64, plan_id="p" * 64,
            plan_digest="d" * 64, final=proposal, client_ref="c" * 64,
            intent=None, ack=None, findings=(),
            leg_latency_ms={"guard": 1, "authorize": 0, "act": 0},
        )
        result.intent is None  # True
    """

    result: str
    leg_id: str
    plan_id: str
    plan_digest: str
    final: object
    client_ref: str
    intent: object
    ack: object
    findings: tuple
    leg_latency_ms: dict


# ---------------------------------------------------------------------------
# Private carriers — what the eight fixed signatures drop
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _AuthorityScope:
    """One authority's answer for one proposal: D11's conjunction, its scope, and whether it exists.

    Two questions with one answer, because they are one decision: an authority
    admits this proposal only if it is in force at all (``conjunction`` — the
    document rung, ``--armed``, ``DSKIT_PRODUCTION_ARM`` and, for a reduction,
    an unconsumed right for its own digest) AND its scope covers the exact
    final proposal (``verdict``). Where NO authority is in force the leg
    records the verdict and refuses nothing: ``ActionPolicy`` owns that case,
    its authority axis is inert below live, and a leg that terminalised on it
    could never submit at ``shadow`` — the rung the package is meant to run at
    first (R27).
    """

    verdict: object
    conjunction: object
    in_force: bool
    role: str

    @property
    def admitted(self):
        """Say whether the authority both holds and admits this exact proposal."""
        return bool(self.verdict.allowed and self.conjunction.satisfied)

    @property
    def gate_passed(self):
        """Say whether the authority-scope gate passes; no authority in force is not a failure."""
        return bool(self.admitted or not self.in_force)

    @property
    def reason(self):
        """Name what the authority refused on, for the gate's recorded reason."""
        return self.verdict.reason or self.conjunction.reason

    @property
    def authority(self):
        """Return the ``AUTHORITY_ROLES`` member in force for this proposal, or None."""
        return self.role if (self.in_force and self.admitted) else None


@dataclass(frozen=True)
class _Refusal:
    """A ``PolicyDecision``-shaped answer for a leg that refused before it asked the matrix."""

    reason: str
    allowed: bool = False


@dataclass(frozen=True)
class _ReductionScope:
    """A signed reduction's scope: its own candidate's instrument, under the document's limits."""

    allowlist: tuple
    limits_overlay: dict


@dataclass
class _Stage:
    """The values one ``run`` threads that the eight fixed step signatures drop."""

    client_ref: str
    final: object = None
    findings: tuple = ()
    account: object = None
    scope: object = None
    risk_effect: str = ""
    state: object = None
    gate_view: object = None
    gates: tuple = ()
    plan: object = None
    intent: object = None
    permit: object = None
    ack: object = None
    result: object = None
    refusal: str = ""
    stopped: bool = False
    latency: dict = field(default_factory=lambda: dict.fromkeys(LEG_LATENCY_BUCKETS, 0))

    def refuse(self, reason):
        """Stop the leg at this step, recording why."""
        self.refusal = redact(reason)
        self.stopped = True

    @property
    def permitted(self):
        """Say whether the recorded plan promised a submit — what step (8) owes an outcome for."""
        return self.plan is not None and self.plan.result == _SUBMIT


# ---------------------------------------------------------------------------
# The origin table — a declared value, not a mode (D2)
# ---------------------------------------------------------------------------


class _Origin(ABC):
    """What a leg's ORIGIN decides: its client reference, its authority and its scope."""

    #: The ``vocab.AUTHORITY_ROLES`` member this origin acts under.
    role = None

    @abstractmethod
    def client_ref(self, leg):
        """Return the deterministic client reference of this leg's one attempt."""

    @abstractmethod
    def authority_id(self, leg, view, at_ms):
        """Return the id of the authority in force in ``view``, or None."""

    @abstractmethod
    def scope(self, leg, final, state, at_ms):
        """Return the :class:`_AuthorityScope` this origin's authority answers for ``final``."""


class _ModelOrigin(_Origin):
    """A model leg: the ordinary arm's allowlist and overlay decide (D11)."""

    role = "ordinary"

    def client_ref(self, leg):
        """Return ``client_ref(tick, leg, attempt)`` — the release-derived model reference (D20)."""
        bindings = leg.bindings
        return leg.recording.id_source.client_ref(
            bindings.tick_id, bindings.leg_index, DEFAULT_ATTEMPT
        )

    def authority_id(self, leg, view, at_ms):
        """Return the current unexpired ordinary arm's id, or None where none is armed."""
        arm = leg.safety.arming.current(view, at_ms)
        return None if arm is None else arm.authority_id

    def scope(self, leg, final, state, at_ms):
        """Re-apply the arm's allowlist and then its overlay through the guard chain."""
        arm = leg.safety.arming.current(state.view, at_ms)
        verdict = leg.safety.arming.apply_scope(final, arm)
        if verdict.allowed:
            verdict = leg.decision.guards.check_authority_scope(final, state, arm)
        return _AuthorityScope(
            verdict=verdict,
            conjunction=_conjunction(leg, state.view, at_ms),
            in_force=arm is not None,
            role=self.role,
        )


class _ReductionOrigin(_Origin):
    """A reduction leg: the signed candidate IS the scope, so there is nothing to diverge from (§5.4)."""

    role = "reduction"

    def client_ref(self, leg):
        """Return D12's flatten reference, independent of process time and ledger sequence."""
        signed = _binding(leg.bindings).signed
        return leg.recording.id_source.flatten_client_ref(
            signed.release_hash,
            signed.request_id,
            signed.index,
            signed.reduction_intent_digest(),
        )

    def authority_id(self, leg, view, at_ms):
        """Return the reduction authority's id — during ``reducing`` no ordinary arm exists."""
        grant = view.reduction
        return None if grant is None else grant.authority_id

    def scope(self, leg, final, state, at_ms):
        """Judge the final proposal against the instrument the maker signed."""
        signed = _binding(leg.bindings).signed
        scope = _ReductionScope(allowlist=(signed.candidate.instrument,), limits_overlay={})
        return _AuthorityScope(
            verdict=leg.decision.guards.check_authority_scope(final, state, scope),
            conjunction=_conjunction(leg, state.view, at_ms),
            in_force=state.view.reduction is not None,
            role=self.role,
        )


#: One strategy per ``vocab.LEG_ORIGINS`` member. A table lookup keyed by a
#: declared value is not the branch D2 forbids — what D2 forbids is asking what
#: rung it is.
_ORIGINS = pin_members(
    "leg.py's origin table",
    {"model": _ModelOrigin(), "reduction": _ReductionOrigin()},
    LEG_ORIGINS,
    exact=True,
)
pin_members(
    "leg.py's origin roles",
    [origin.role for origin in _ORIGINS.values()],
    AUTHORITY_ROLES,
)


def _conjunction(leg, view, at_ms):
    """Evaluate D11's live conjunction for this leg — arming.py is its one owner.

    The rung passed is the DOCUMENT's, because §4.1 makes the document the
    authority on the rung and the ``rung`` gate is the sole owner of "the rung
    the leg carries agrees with it": asking twice would report one disagreement
    as two refusals.
    """
    bindings = leg.bindings
    return leg.safety.arming.check_conjunction(
        leg.safety.invocation, view, bindings.origin, bindings.reduction, leg.document.rung, at_ms
    )


def _binding(bindings):
    """Return the leg's ``ReductionBinding``, refusing a reduction leg that carries none."""
    binding = bindings.reduction
    if not isinstance(binding, ReductionBinding):
        raise ProductionError(
            [f"a reduction leg carries a ReductionBinding, got {binding!r}"]
        )
    return binding


# ---------------------------------------------------------------------------
# Authority — minting has exactly one home (D14, §5.13.1)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _AuthorityBinding:
    """What one authority contributes to a permit: its id, its scope digest and its expiry."""

    authority_id: str
    scope_digest: str
    expires_ms: int
    right_digest: str | None


class Authority(ABC):
    """The seam that mints what authorises real money — one signature, three kinds (§5.13.1).

    Constructed only by ``compose.py``, and it takes ten collaborators because
    ``ActPermit`` binds them: ``valid_until_ms`` is the minimum over all nine
    terms §5.13 step (6) lists, ``lease_scope`` / ``fencing_token`` come from
    the ``Lease``, and ``safety_epoch_digest`` covers calendar, health,
    executor link and scope, rung and pending-control state. A permit cannot be
    minted from ``(intent, plan, state_view)`` alone. ``inbox`` is the tenth
    because the epoch covers pending-control state and a queued-but-unfolded
    command must not be missed at the moment a permit is minted.

    Parameters
    ----------
    clock : Clock
        The injected time source; ``checked_at_ms`` and the submit-timeout term.
    calendar : Calendar
        Supplies the session close, the sixth term.
    arming : Arming
        The ordinary-arm fold and its effective bounds.
    lease : Lease
        The venue/account grip: scope, fence and the eighth term.
    health : Health
        The state machine the safety epoch covers.
    executor : Executor
        Its authenticated ``execution_scope()``.
    document : ServeDocument
        The freshness and timeout knobs; §4.1 rules that code holds none.
    release : ReleaseManifest
        The release the permit binds.
    ledger : Ledger
        Where ``authority_use`` and ``authorization`` are appended and barriered.
    inbox : ControlInbox
        The durable control spool.

    Examples
    --------
    A live authority over the composed collaborators::

        authority = LiveAuthority(
            clock, calendar, arming, lease, health, executor, document,
            release, ledger, inbox,
        )
        permit = authority.mint(intent, plan, view, None)
        permit.client_ref == intent.client_ref  # True
    """

    def __init__(self, clock, calendar, arming, lease, health, executor, document, release,
                 ledger, inbox):
        self._clock = clock
        self._calendar = calendar
        self._arming = arming
        self._lease = lease
        self._health = health
        self._executor = executor
        self._document = document
        self._release = release
        self._ledger = ledger
        self._inbox = inbox

    @abstractmethod
    def mint(self, intent, plan, state_view, reduction):
        """Return the ``Permit`` this intent may be submitted under.

        Parameters
        ----------
        intent : Intent
            The intent step (5) built and barriered.
        plan : DecisionPlan
            The plan step (4) recorded.
        state_view : StateView
            Step (6)'s FRESH fold.
        reduction : ReductionBinding or None
            The leg's signed reduction; None for a model leg. It is a
            parameter because a ``ReductionAuthority`` cannot find the right it
            consumes in ``(intent, plan, view)``, and giving one subclass a
            wider signature than its siblings would break the polymorphism the
            seam exists for.

        Returns
        -------
        Permit
            A ``SimulatedPermit`` where no live authority exists, else an
            ``ActPermit``.
        """

    # -- the terms every permit binds ---------------------------------------

    def _lease_permit(self):
        """Return the permit the lease holds on the document's scope, or None."""
        return self._lease.current(self._document.coordination.scope)

    def _valid_until_ms(self, intent, state_view, checked_at_ms, authority_expires_ms):
        """Return the minimum of §5.13 step (6)'s nine terms — stated there, never restated.

        Parameters
        ----------
        intent : Intent
        state_view : StateView
        checked_at_ms : int
        authority_expires_ms : int or None
            The authority's own expiry, or None where none is in force.

        Returns
        -------
        int
            The earliest instant any bound fact stops holding. An authority
            that dropped proposal expiry, input staleness, quote age, evidence
            age or readiness validity would mint a permit that outlives the
            data it binds.
        """
        document, readiness = self._document, state_view.readiness
        lease_permit = self._lease_permit()
        terms = {
            "proposal_expiry": intent.proposal.expires_ms,
            "input_staleness": intent.inputs_asof_ms + document.schedule.max_staleness_ms,
            "quote_age": intent.quote_asof_ms + document.schedule.max_quote_age_ms,
            "evidence_age": intent.evidence_asof_ms + document.accounting.max_valuation_age_ms,
            "readiness": None if readiness is None else readiness.valid_until_ms,
            "calendar_close": self._calendar.window(_CALENDAR_WINDOW, checked_at_ms)[1],
            "authority_expiry": authority_expires_ms,
            "lease_expiry": None if lease_permit is None else lease_permit.expires_ms,
            "submit_timeout": checked_at_ms + document.execution.submit_timeout_ms,
        }
        bound = [value for value in terms.values() if value is not None]
        if not bound:
            raise ProductionError(["a permit cannot be minted with no deadline to bind"])
        return min(bound)

    def _safety_epoch_digest(self, intent, plan, state_view, binding, checked_at_ms):
        """Return the digest over everything §5.4's safety epoch covers.

        Parameters
        ----------
        intent : Intent
        plan : DecisionPlan
        state_view : StateView
        binding : _AuthorityBinding
        checked_at_ms : int

        Returns
        -------
        str
            Any change to release, readiness, calendar, coverage and
            watermarks, input/quote/evidence/risk versions, executor link and
            scope, health, breaker, rung, risk effect, authority,
            pending-control state or lease invalidates it.
        """
        readiness, lease_permit = state_view.readiness, self._lease_permit()
        return canonical_hash(
            [
                "safety-epoch-v1",
                {
                    "release_hash": intent.release_hash,
                    "readiness_digest": None if readiness is None else readiness.readiness_digest,
                    "readiness_until_ms": None if readiness is None else readiness.valid_until_ms,
                    "calendar_close_ms": self._calendar.window(_CALENDAR_WINDOW, checked_at_ms)[1],
                    "coverage_digest": intent.coverage_digest,
                    "inputs_digest": intent.inputs_digest,
                    "inputs_asof_ms": intent.inputs_asof_ms,
                    "quote_digest": intent.quote_digest,
                    "quote_asof_ms": intent.quote_asof_ms,
                    "evidence_digest": intent.evidence_digest,
                    "evidence_asof_ms": intent.evidence_asof_ms,
                    "risk_version": intent.risk_version.to_obj(),
                    "risk_state_digest": intent.risk_state_digest,
                    "executor_scope": self._executor.execution_scope().to_obj(),
                    "health": self._health.state,
                    "breaker": state_view.breaker,
                    "rung": self._document.rung,
                    "risk_effect": plan.risk_effect,
                    "authority_id": binding.authority_id,
                    "authority_scope_digest": binding.scope_digest,
                    "pending_control": sorted(state_view.pending_control),
                    "queued_control": len(self._inbox.pending()),
                    "lease_scope": None if lease_permit is None else lease_permit.scope.to_obj(),
                    "fencing_token": None if lease_permit is None else lease_permit.fencing_token,
                },
            ]
        )

    def _act_permit(self, intent, plan, state_view, binding):
        """Build the live binding: every version, digest, scope and fence the verifier rechecks.

        Parameters
        ----------
        intent : Intent
        plan : DecisionPlan
        state_view : StateView
        binding : _AuthorityBinding

        Returns
        -------
        ActPermit

        Raises
        ------
        ProductionError
            When the lease no longer holds the document's scope — a permit
            without a fence is one the gateway could never accept.
        """
        checked_at_ms = self._clock.now_ms()
        lease_permit = self._lease_permit()
        if lease_permit is None:
            raise ProductionError(
                ["no lease permit holds the document's execution scope; nothing to fence with"]
            )
        readiness = state_view.readiness
        if readiness is None:
            raise ProductionError(["a live permit binds a readiness evaluation; the fold has none"])
        return ActPermit(
            plan_id=plan.plan_id,
            decision_plan_digest=plan.decision_plan_digest(),
            client_ref=intent.client_ref,
            valid_until_ms=self._valid_until_ms(
                intent, state_view, checked_at_ms, binding.expires_ms
            ),
            authority_id=binding.authority_id,
            release_hash=intent.release_hash,
            intent_digest=intent.intent_digest(),
            instrument=intent.proposal.instrument,
            risk_effect=plan.risk_effect,
            inputs_asof_ms=intent.inputs_asof_ms,
            inputs_digest=intent.inputs_digest,
            coverage_digest=intent.coverage_digest,
            quote_asof_ms=intent.quote_asof_ms,
            quote_digest=intent.quote_digest,
            evidence_asof_ms=intent.evidence_asof_ms,
            evidence_digest=intent.evidence_digest,
            authority_scope_digest=binding.scope_digest,
            reduction_right_digest=binding.right_digest,
            risk_version=intent.risk_version,
            risk_state_digest=intent.risk_state_digest,
            readiness_digest=readiness.readiness_digest,
            readiness_until_ms=readiness.valid_until_ms,
            lease_scope=lease_permit.scope,
            fencing_token=lease_permit.fencing_token,
            safety_epoch_digest=self._safety_epoch_digest(
                intent, plan, state_view, binding, checked_at_ms
            ),
            checked_at_ms=checked_at_ms,
        )

    def _record_authorization(self, permit, authority_use_id):
        """Append and barrier §6's ``authorization`` — no raw authority is executable."""
        self._ledger.append(
            {
                "kind": "authorization",
                "id": f"authorization:{permit.client_ref}",
                "body": {"permit": permit.to_obj(), "authority_use_id": authority_use_id},
            }
        )
        self._ledger.barrier()
        _LOG.debug("authorization recorded for %s", permit.client_ref)


class SimulatedAuthority(Authority):
    """Shadow and paper: returns a ``SimulatedPermit`` and writes nothing (D10).

    There is no live permit to authorise below ``live_limited``, so an
    ``authorization`` row would be a record of an authority that does not
    exist. The deadline is still the nine-term minimum, minus the authority
    term there is no authority to supply.

    Examples
    --------
    ::

        authority = SimulatedAuthority(
            clock, calendar, arming, lease, health, executor, document,
            release, ledger, inbox,
        )
        permit = authority.mint(intent, plan, view, None)
        type(permit).__name__  # 'SimulatedPermit'
    """

    def mint(self, intent, plan, state_view, reduction):
        """Return a ``SimulatedPermit`` bound to the same plan and deadline; write nothing."""
        return SimulatedPermit(
            plan_id=plan.plan_id,
            decision_plan_digest=plan.decision_plan_digest(),
            client_ref=intent.client_ref,
            valid_until_ms=self._valid_until_ms(
                intent, state_view, self._clock.now_ms(), None
            ),
        )


class LiveAuthority(Authority):
    """``live_limited`` and ``live`` in ``active``: mint an ``ActPermit`` from the ordinary arm.

    D11 requires each live submit to carry a fresh exact-intent permit derived
    from the arm; the arm's allowlist and effective bounds are what the scope
    digest covers, so a permit can never be replayed under an arm that never
    admitted the instrument.

    Examples
    --------
    ::

        authority = LiveAuthority(
            clock, calendar, arming, lease, health, executor, document,
            release, ledger, inbox,
        )
        permit = authority.mint(intent, plan, view, None)
        permit.reduction_right_digest is None  # True
    """

    def mint(self, intent, plan, state_view, reduction):
        """Mint the permit, then append and barrier the ``authorization`` before any submit."""
        permit = self._act_permit(intent, plan, state_view, self._binding(state_view))
        self._record_authorization(permit, None)
        return permit

    def _binding(self, state_view):
        """Return the ordinary arm's id, scope digest and expiry."""
        arm = self._arming.current(state_view, self._clock.now_ms())
        if arm is None:
            raise ProductionError(
                ["a live ordinary permit needs a current arm; the fold holds none"]
            )
        return _AuthorityBinding(
            authority_id=arm.authority_id,
            scope_digest=canonical_hash(
                [
                    "ordinary-scope-v1",
                    list(arm.allowlist),
                    self._arming.effective_bounds(arm),
                ]
            ),
            expires_ms=arm.armed_until_ms,
            right_digest=None,
        )


class ReductionAuthority(Authority):
    """``live_limited`` and ``live`` in ``reducing``: reserve the right, then mint (D12).

    The single-use ``authority_use`` is appended and barriered FIRST: the
    reservation must be durable before anything says the right was converted
    into a permit, so a crash between the two leaves the right consumed rather
    than usable twice.

    Examples
    --------
    ::

        authority = ReductionAuthority(
            clock, calendar, arming, lease, health, executor, document,
            release, ledger, inbox,
        )
        permit = authority.mint(intent, plan, view, binding)
        permit.reduction_right_digest == binding.digest  # True
    """

    def mint(self, intent, plan, state_view, reduction):
        """Reserve the right, barrier it, then mint the permit and record the authorization."""
        binding = self._binding(state_view, reduction)
        authority_use_id = self._reserve(state_view, reduction, intent)
        permit = self._act_permit(intent, plan, state_view, binding)
        self._record_authorization(permit, authority_use_id)
        return permit

    def _reserve(self, state_view, reduction, intent):
        """Append and barrier the ``authority_use`` that consumes one right; return its id."""
        body = ReductionRights(clock=self._clock).reserve(
            state_view, _right_digest(reduction), intent.client_ref
        )
        record_id = f"authority_use:{body['authority_id']}:{body['reduction_intent_digest']}"
        self._ledger.append({"kind": "authority_use", "id": record_id, "body": body})
        self._ledger.barrier()
        return record_id

    def _binding(self, state_view, reduction):
        """Return the reduction authority's id, scope digest and expiry."""
        grant = state_view.reduction
        if grant is None:
            raise ProductionError(
                ["a reduction permit needs a reduction authority; the fold holds none"]
            )
        return _AuthorityBinding(
            authority_id=grant.authority_id,
            scope_digest=canonical_hash(
                [
                    "reduction-scope-v1",
                    list(grant.rights),
                    self._arming.effective_bounds(None),
                ]
            ),
            expires_ms=grant.expires_ms,
            right_digest=_right_digest(reduction),
        )


def _right_digest(reduction):
    """Return the ``reduction_intent_digest`` the leg's right names."""
    if not isinstance(reduction, ReductionBinding):
        raise ProductionError(
            [f"a reduction permit is minted from a ReductionBinding, got {reduction!r}"]
        )
    return reduction.digest


# ---------------------------------------------------------------------------
# LegPipeline — the eight steps, and the walk no subclass may replace
# ---------------------------------------------------------------------------


class LegPipeline:
    """One proposal's journey from a guard chain to a venue — eight steps, four barriers.

    A concrete class, not an ABC: the invariant is that :meth:`run` is final,
    not that the steps are abstract, and the tick must be able to construct it.
    ``document`` is a constructor argument because step (3) enforces four
    document thresholds and §4.1 rules that code holds none; ``schedule`` is,
    because every instant the leg stamps or judges comes from the injected
    clock — reaching for the wall clock would break the D20 replay parity that
    rests on it.

    Parameters
    ----------
    document : ServeDocument
        The graded serve document.
    release : ReleaseManifest
        The release this process serves.
    bindings : LegBindings
        What the tick bound for this leg.
    schedule : Schedule
        ``clock`` and ``calendar``.
    decision : Decision
        ``guards`` — the chain of steps (1), (2) and the authority gate.
    safety : Safety
        ``arming``, ``authorities``, ``readiness``, ``invocation``,
        ``action_policy``.
    execution : Execution
        ``executor``, ``accounting``, ``lease``.
    recording : Recording
        ``ledger``, ``state``, ``inbox``, ``id_source``.
    observability : Observability
        ``health``.

    Raises
    ------
    ProductionError
        From ``__init_subclass__``, when a subclass overrides ``run``: "record
        before act" is enforced by the base, so a subclass that could reorder
        or skip a barrier must be impossible rather than discouraged.

    Examples
    --------
    Run one leg and read what it came to::

        leg = LegPipeline(
            document, release, bindings, schedule, decision, safety,
            execution, recording, observability,
        )
        result = leg.run()
        result.result  # 'open'
    """

    def __init__(self, document, release, bindings, schedule, decision, safety, execution,
                 recording, observability):
        self.document = document
        self.release = release
        self.bindings = bindings
        self.schedule = schedule
        self.decision = decision
        self.safety = safety
        self.execution = execution
        self.recording = recording
        self.observability = observability
        self._stage = None

    def __init_subclass__(cls, **kwargs):
        """Refuse a subclass that replaces the walk; every STEP stays overridable."""
        super().__init_subclass__(**kwargs)
        if "run" in vars(cls):
            raise ProductionError(
                [
                    f"{cls.__name__} overrides run, which is final (§5.13.1): a leg's "
                    "steps are the seam, its order is not — override a step instead"
                ]
            )

    # -- the walk -----------------------------------------------------------

    def run(self):
        """Walk ``vocab.LEG_STEPS`` in order, timing each into the §6 latency buckets.

        Returns
        -------
        LegResult
            Always — a refusal is a value. Only a process failure (a ledger
            that cannot write, an executor that dies after the request left)
            propagates, and what survived on disk is what the barriers
            promised.
        """
        stage = self._stage = _Stage(client_ref=_ORIGINS[self.bindings.origin].client_ref(self))
        clock = self.schedule.clock
        for step in LEG_STEPS:
            if stage.stopped and step not in _ALWAYS_RUN:
                continue
            started = clock.now_ms()
            _STEP_CALLS[step](self, stage)
            self._charge(stage, step, clock.now_ms() - started)
        return stage.result

    @staticmethod
    def _charge(stage, step, elapsed_ms):
        """Add one step's elapsed time to the span it belongs to; step (8) is the tick's."""
        bucket = _STEP_BUCKETS[step]
        if bucket is not None:
            stage.latency[bucket] += elapsed_ms

    # -- (1) the ordinary guards --------------------------------------------

    def guard(self):
        """Run the ordinary guards and compose any monotone amendment (§5.13 step (1), D9).

        Returns
        -------
        tuple
            ``(final, findings)`` — the possibly-amended proposal every later
            step binds, and every finding the chain recorded.
        """
        return self.decision.guards.check_all(self.bindings.proposal, self.bindings.state)

    # -- (2) the fresh fold and the refreshed account -----------------------

    def refresh(self, final, findings):
        """Re-snapshot the fold, refresh the account, re-run the hard guards (§5.13 step (2)).

        Prior legs of this tick have appended and folded their own reservations
        and acks, and only a fresh fold carries them — so cumulative exposure,
        working orders and group scopes include every earlier leg.

        Parameters
        ----------
        final : Proposal
            Step (1)'s candidate.
        findings : tuple of Finding
            Step (1)'s findings.

        Returns
        -------
        tuple
            ``(account, scope_verdict, risk_effect, findings)`` — the REFRESHED
            snapshot the plan, intent and permit all bind, the active
            authority's verdict on the exact final proposal, accounting's
            exclusive risk classification, and every finding so far.
        """
        stage = self._stage
        view = self.recording.state.snapshot()
        at_ms = self.schedule.clock.now_ms()
        account = self.execution.accounting.snapshot(
            view,
            self.execution.executor,
            self.bindings.quotes,
            at_ms,
            self.bindings.requirements,
            self.schedule.calendar,
        )
        state = dataclasses.replace(self.bindings.state, view=view, account=account)
        findings = tuple(findings) + tuple(
            guard.check(final, state)
            for guard in self.decision.guards.guards.values()
            if guard.hard
        )
        scope = _ORIGINS[self.bindings.origin].scope(self, final, state, at_ms)
        stage.state, stage.scope = state, scope
        return account, scope.verdict, self.execution.accounting.classify(final, state), findings

    # -- (3) the gates ------------------------------------------------------

    def rebind(self, account):
        """Re-evaluate every declared gate against a fresh fold (§5.13 step (3)).

        Parameters
        ----------
        account : AccountState
            Step (2)'s refreshed snapshot.

        Returns
        -------
        tuple of GateResult
            One per :data:`LEG_GATES` member, in that order — every gate is
            evaluated even after one fails, because a recorded audit trail with
            a hole in it is worse than none.
        """
        stage = self._stage
        stage.gate_view = view = self.recording.state.snapshot()
        at_ms = self.schedule.clock.now_ms()
        results = []
        for gate in LEG_GATES:
            passed, reason = getattr(self, f"_gate_{gate}")(at_ms, view, account)
            results.append(
                GateResult(gate=gate, passed=bool(passed), reason=redact(reason), at_ms=at_ms)
            )
        return tuple(results)

    def _gate_release(self, at_ms, view, account):
        """Re-check that the leg is bound to the release this process serves."""
        served, carried = self.release.release_hash, self.bindings.release.release_hash
        problems = []
        check_digest(problems, "release_hash", carried)
        if problems:
            return False, "; ".join(problems)
        if carried != served:
            return False, f"leg bound release {carried} but the process serves {served}"
        return True, _SATISFIED

    def _gate_readiness(self, at_ms, view, account):
        """Record the durable readiness verdict and pass; the matrix owns the live GO (R29)."""
        verdict = self.safety.readiness.verdict_for(view, at_ms)
        return True, f"readiness is {verdict}"

    def _gate_calendar(self, at_ms, view, account):
        """Re-check the session: one that closed since the fetch stops this leg, not the next tick."""
        if not self.schedule.calendar.is_open(at_ms):
            return False, f"the calendar is closed at {at_ms}"
        return True, _SATISFIED

    def _gate_coverage(self, at_ms, view, account):
        """Re-check exact required-key coverage and the provenance the proposal claims."""
        batch, proposal = self.bindings.entry_batch, self.bindings.proposal
        required = tuple(self.release.feed_spec["required_keys"])
        missing = [key for key in required if key not in batch.watermarks_by_key]
        if missing:
            return False, f"required key(s) absent from the batch: {sorted(missing)}"
        for name in ("inputs_digest", "coverage_digest"):
            claimed, held = getattr(proposal, name), getattr(batch, name)
            if claimed != held:
                return False, f"proposal {name} {claimed} is not the batch's {held}"
        return True, _SATISFIED

    def _gate_watermark_age(self, at_ms, view, account):
        """Re-check the OLDEST watermark: one fresh key may never hide a stale input (D6)."""
        allowed = self.document.schedule.max_staleness_ms
        age = at_ms - self.bindings.entry_batch.data_asof_ms
        if age > allowed:
            return False, f"oldest input is {age} ms old, past max_staleness_ms {allowed}"
        return True, _SATISFIED

    def _gate_quote_age(self, at_ms, view, account):
        """Re-check the oldest quote against the document's budget."""
        allowed = self.document.schedule.max_quote_age_ms
        age = at_ms - self.bindings.quotes.min_asof_ms
        if age > allowed:
            return False, f"oldest quote is {age} ms old, past max_quote_age_ms {allowed}"
        return True, _SATISFIED

    def _gate_quote_digest(self, at_ms, view, account):
        """Re-check that the quote set the leg binds is the one the proposal was made from."""
        claimed, held = self.bindings.proposal.quote_digest, self.bindings.quotes.quote_digest
        if claimed != held:
            return False, f"proposal quote_digest {claimed} is not the quote set's {held}"
        return True, _SATISFIED

    def _gate_evidence_age(self, at_ms, view, account):
        """Re-check the account's as-of: stale evidence must refuse, never size an order."""
        allowed = self.document.accounting.max_valuation_age_ms
        age = at_ms - account.asof_ms
        if age > allowed:
            return False, f"accounting evidence is {age} ms old, past max_valuation_age_ms {allowed}"
        return True, _SATISFIED

    def _gate_evidence_digest(self, at_ms, view, account):
        """Re-check that the account carries a well-formed digest taken from THIS fold."""
        problems = []
        check_digest(problems, "account.evidence_digest", account.evidence_digest)
        if problems:
            return False, "; ".join(problems)
        bound, folded = account.risk_version.economic_seq, view.risk_version.economic_seq
        if bound != folded:
            return False, f"evidence was snapshotted at economic_seq {bound}, the fold is at {folded}"
        return True, _SATISFIED

    def _gate_venue_skew(self, at_ms, view, account):
        """Re-check the venue's clock against ``document.schedule.max_venue_skew_ms``."""
        allowed = self.document.schedule.max_venue_skew_ms
        venue_ms = self.execution.executor.venue_time_ms()
        if allowed is None or venue_ms is None:
            return True, _SATISFIED
        skew = abs(venue_ms - at_ms)
        if skew > allowed:
            return False, f"venue clock is {skew} ms away, past max_venue_skew_ms {allowed}"
        return True, _SATISFIED

    def _gate_executor_scope(self, at_ms, view, account):
        """Re-check the executor's authenticated scope against the graded document's (§5.7)."""
        actual = self.execution.executor.execution_scope()
        declared = self.document.coordination.scope
        if not scope_equal(actual, declared):
            return False, f"executor acts in {actual} but the document grades {declared}"
        return True, _SATISFIED

    def _gate_health(self, at_ms, view, account):
        """Re-check health: degraded and unhealthy refuse a submit in every rung (D10)."""
        state = self.observability.health.state
        if state != _READY_HEALTH:
            return False, f"health is {state}, not {_READY_HEALTH}"
        return True, _SATISFIED

    def _gate_breaker(self, at_ms, view, account):
        """Re-check the breaker from the FRESH fold — an earlier leg's halt is visible here."""
        if view.breaker == _HALTED_BREAKER:
            return False, f"the breaker is {_HALTED_BREAKER}"
        return True, _SATISFIED

    def _gate_rung(self, at_ms, view, account):
        """Re-check the document rung: a leg carrying a promoted one is what D11 forbids."""
        carried, declared = self.bindings.rung, self.document.rung
        if carried != declared:
            return False, f"leg carries rung {carried!r} but the document grades {declared!r}"
        return True, _SATISFIED

    def _gate_risk_effect(self, at_ms, view, account):
        """Re-check that accounting classified exactly one mutually exclusive effect (D10)."""
        effect = self._stage.risk_effect
        if effect not in RISK_EFFECTS:
            return False, f"risk effect {effect!r} is outside {list(RISK_EFFECTS)}"
        return True, _SATISFIED

    def _gate_authority_scope(self, at_ms, view, account):
        """Re-check the active authority's scope; where none is in force the matrix decides (R27)."""
        scope = self._stage.scope
        if not scope.gate_passed:
            return False, f"authority scope: {scope.reason}"
        return True, _SATISFIED

    def _gate_lease(self, at_ms, view, account):
        """Re-check the venue grip: without a current permit there is no fence to submit under."""
        scope = self.document.coordination.scope
        if self.execution.lease.current(scope) is None:
            return False, f"the lease holds no current permit on {scope}"
        return True, _SATISFIED

    # -- (4) the decision plan ----------------------------------------------

    def plan(self, evaluation):
        """Record the whole pre-submit evaluation and barrier it (§5.13 step (4), D9).

        Parameters
        ----------
        evaluation : LegEvaluation
            Steps (1)-(3), threaded.

        Returns
        -------
        DecisionPlan
            Appended and barriered before any submit I/O. Its ``result`` is the
            action-matrix verdict: a ``not_sent`` plan terminalises the leg
            without an intent, and that plan record IS the terminal fact.
        """
        stage = self._stage
        bindings = self.bindings
        decision = self._permits(evaluation)
        plan = DecisionPlan(
            plan_id=self.recording.id_source.plan_id(bindings.tick_id, bindings.leg_index),
            inputs_asof_ms=bindings.entry_batch.data_asof_ms,
            inputs_digest=bindings.entry_batch.inputs_digest,
            coverage_digest=bindings.entry_batch.coverage_digest,
            quote_asof_ms=bindings.quotes.min_asof_ms,
            quote_digest=bindings.quotes.quote_digest,
            evidence_asof_ms=evaluation.account.asof_ms,
            evidence_digest=evaluation.account.evidence_digest,
            provenance_digests={
                "entry": bindings.entry_batch.inputs_digest,
                "head": bindings.head_digest,
                "candidate": bindings.proposal.id,
            },
            original=evaluation.original,
            final=evaluation.final,
            findings=evaluation.findings,
            gate_results=evaluation.gate_results,
            scope_verdict=evaluation.scope_verdict,
            risk_effect=evaluation.risk_effect,
            risk_version=evaluation.risk_version,
            risk_state_digest=evaluation.risk_state_digest,
            result=_SUBMIT if decision.allowed else _NOT_SENT,
        )
        self.recording.ledger.append(
            {"kind": "decision_plan", "id": f"decision_plan:{plan.plan_id}", "body": plan.to_obj()}
        )
        self.recording.ledger.barrier()
        if not decision.allowed:
            stage.refuse(decision.reason)
        return plan

    def _permits(self, evaluation):
        """Ask the action policy whether this evaluation may submit — the one matrix owner."""
        findings_verdict = max_verdict(evaluation.findings)
        if VERDICT_ORDER[findings_verdict] > _AMEND_RANK:
            return _Refusal(f"a guard returned {findings_verdict}")
        failed = [gate.gate for gate in evaluation.gate_results if not gate.passed]
        if failed:
            return _Refusal(f"gate(s) refused: {failed}")
        return self.safety.action_policy.permits(
            self._policy_request(self._stage.gate_view, evaluation.risk_effect)
        )

    def _policy_request(self, view, risk_effect):
        """Assemble §5.14's nine facts from the FRESH view — never the tick-assembly one."""
        return PolicyRequest(
            operation=_SUBMIT_OPERATION,
            risk_effect=risk_effect,
            rung=self.bindings.rung,
            breaker=view.breaker,
            health=self.observability.health.state,
            readiness=self.safety.readiness.verdict_for(view, self.schedule.clock.now_ms()),
            authority=self._stage.scope.authority,
            origin=self.bindings.origin,
            pending_control=bool(view.pending_control) or bool(self.recording.inbox.pending()),
        )

    # -- (5) the intent -----------------------------------------------------

    def intent(self, plan):
        """Build, record and barrier the canonical intent (§5.13 step (5), §5.4).

        Parameters
        ----------
        plan : DecisionPlan
            The plan step (4) recorded.

        Returns
        -------
        Intent or None
            None when a reduction leg's rebuilt ``reduction_intent_digest``
            does not match the right being consumed — the only thing standing
            between "the maker signed this order" and "this order reached the
            venue", so nothing is appended and nothing is submitted.
        """
        stage = self._stage
        bindings = self.bindings
        view = stage.state.view
        at_ms = self.schedule.clock.now_ms()
        intent = Intent(
            client_ref=stage.client_ref,
            decision_plan_id=plan.plan_id,
            decision_plan_digest=plan.decision_plan_digest(),
            proposal=stage.final,
            created_ms=at_ms,
            authority_id=_ORIGINS[bindings.origin].authority_id(self, view, at_ms),
            release_hash=bindings.release.release_hash,
            inputs_asof_ms=bindings.entry_batch.data_asof_ms,
            inputs_digest=bindings.entry_batch.inputs_digest,
            coverage_digest=bindings.entry_batch.coverage_digest,
            quote_asof_ms=bindings.quotes.min_asof_ms,
            quote_digest=bindings.quotes.quote_digest,
            evidence_asof_ms=stage.account.asof_ms,
            evidence_digest=stage.account.evidence_digest,
            risk_version=stage.account.risk_version,
            risk_state_digest=stage.account.risk_digest(),
        )
        rebuilt = self._rebuild_refusal(intent)
        if rebuilt:
            stage.refuse(rebuilt)
            return None
        self.recording.ledger.append(
            {"kind": "intent", "id": f"intent:{intent.client_ref}", "body": intent.to_obj()}
        )
        self.recording.ledger.barrier()
        return intent

    def _rebuild_refusal(self, intent):
        """Return why the signed reduction does not survive the rebuild, or "" (§5.4).

        ``release_hash`` and ``proposal`` come from the CONSTRUCTED intent;
        ``candidate``, ``request_id``, ``index``, ``expires_ms`` and
        ``risk_state_digest`` from the signed intent, because the last four are
        not ``Intent`` fields and cannot be recovered from one. That the digest
        is unchanged is what pins economic content — the order that reaches the
        venue is the order signed.
        """
        binding = self.bindings.reduction
        if binding is None:
            return _SATISFIED
        signed = _binding(self.bindings).signed
        rebuilt = ReductionIntent(
            release_hash=intent.release_hash,
            request_id=signed.request_id,
            index=signed.index,
            candidate=signed.candidate,
            proposal=intent.proposal,
            risk_state_digest=signed.risk_state_digest,
            expires_ms=signed.expires_ms,
        ).reduction_intent_digest()
        if rebuilt != binding.right:
            return (
                f"the rebuilt reduction_intent_digest {rebuilt} is not the right "
                f"{binding.right} being consumed"
            )
        return _SATISFIED

    # -- (6) the permit -----------------------------------------------------

    def authorize(self, intent, plan):
        """Take a fresh fold, refuse on drift, and mint under the origin's authority (step (6)).

        Parameters
        ----------
        intent : Intent
        plan : DecisionPlan

        Returns
        -------
        Permit or None
            None when a member the plan and intent already bound has moved
            since — an earlier leg's halt verdict trips the breaker, a reduce
            consumed between legs moves it to ``reducing``, and a later leg
            reading a stale ``active`` would mint a live permit the action
            matrix forbids.
        """
        stage = self._stage
        view = self.recording.state.snapshot()
        at_ms = self.schedule.clock.now_ms()
        drift = self._drift(intent, view, at_ms)
        if drift:
            stage.refuse(drift)
            return None
        decision = self.safety.action_policy.permits(
            self._policy_request(view, plan.risk_effect)
        )
        if not decision.allowed:
            stage.refuse(f"the action matrix refused at the permit gate: {decision.reason}")
            return None
        authority = self.safety.authorities.for_origin(self.bindings.origin, view.breaker)
        return authority.mint(intent, plan, view, self.bindings.reduction)

    def _drift(self, intent, view, at_ms):
        """Return why a bound member has moved since the plan and intent, or ""."""
        bound = _ORIGINS[self.bindings.origin].authority_id(self, view, at_ms)
        if bound != intent.authority_id:
            return (
                f"the authority moved: the intent bound {intent.authority_id!r} and the fold "
                f"now holds {bound!r}"
            )
        folded, promised = view.risk_version.economic_seq, intent.risk_version.economic_seq
        if folded != promised:
            return f"economic_seq moved from {promised} to {folded} since the plan"
        return _SATISFIED

    # -- (7) the one call that reaches a venue ------------------------------

    def act(self, intent, permit):
        """Submit through the executor — D14's only route (§5.13 step (7)).

        Parameters
        ----------
        intent : Intent
        permit : Permit

        Returns
        -------
        Ack
            Always: the live wrapper's ``SubmissionVerifier`` turns a mismatch
            into ``not_sent`` and a post-I/O timeout into ``unknown``, which
            only ``executor.order(ref)`` may resolve — never a blind resend.
        """
        return self.execution.executor.submit(intent, permit, self._stage.state)

    # -- (8) the outcome ----------------------------------------------------

    def fold(self, intent, permit, ack, findings):
        """Record the outcome and assemble the leg's result (§5.13 step (8), R28).

        Parameters
        ----------
        intent : Intent or None
        permit : Permit or None
        ack : Ack or None
            The venue's answer, or None when the leg refused.
        findings : tuple of Finding

        Returns
        -------
        LegResult
            A leg that reached step (5) always has an ``order_event``: a
            refusal after the plan barrier terminalises with a synthesized
            ``not_sent`` ack, so recovery never has to guess whether an intent
            was answered.
        """
        stage = self._stage
        if ack is None and stage.permitted:
            ack = self._not_sent_ack(stage.refusal)
        if ack is not None:
            self._record_order_event(ack)
        plan = stage.plan
        return LegResult(
            result=_NOT_SENT if ack is None else ack.status,
            leg_id=self.bindings.leg_id,
            plan_id=plan.plan_id,
            plan_digest=plan.decision_plan_digest(),
            final=plan.final,
            client_ref=stage.client_ref,
            intent=intent,
            ack=ack,
            findings=tuple(findings),
            leg_latency_ms=dict(stage.latency),
        )

    def _not_sent_ack(self, reason):
        """Synthesize the ack a refusal after the plan barrier terminalises through."""
        return Ack(
            client_ref=self._stage.client_ref,
            venue_ref=None,
            status=_NOT_SENT,
            ts_ms=self.schedule.clock.now_ms(),
            filled_qty=_ZERO,
            avg_price=None,
            fee=_ZERO,
            reason=redact(reason),
            native={},
        )

    def _record_order_event(self, ack):
        """Append and barrier §6's ``order_event`` — the outcome is a recorded fact."""
        at_ms = self.schedule.clock.now_ms()
        self.recording.ledger.append(
            {
                "kind": "order_event",
                "id": f"order_event:{ack.client_ref}",
                "body": {
                    "client_ref": ack.client_ref,
                    "venue_ref": ack.venue_ref,
                    "event": _EVENT_BY_STATUS[ack.status],
                    "status": ack.status,
                    "venue_ts_ms": ack.ts_ms,
                    "recv_at_ms": at_ms,
                    "reason": redact(ack.reason or _SATISFIED),
                },
            }
        )
        self.recording.ledger.barrier()


def _call_guard(leg, stage):
    """Step (1): the ordinary guards and the one monotone amendment they may compose."""
    stage.final, stage.findings = leg.guard()


def _call_refresh(leg, stage):
    """Step (2): the fresh fold, the refreshed account, the hard guards and the scope.

    The verdict ``refresh`` RETURNS is folded back into the carrier, so the
    gate, the policy's authority axis and the recorded plan all read one value
    even when a subclass supplied its own.
    """
    account, verdict, risk_effect, findings = leg.refresh(stage.final, stage.findings)
    stage.account, stage.risk_effect, stage.findings = account, risk_effect, tuple(findings)
    stage.scope = dataclasses.replace(stage.scope, verdict=verdict)


def _call_rebind(leg, stage):
    """Step (3): every declared gate, against a fresh fold."""
    stage.gates = leg.rebind(stage.account)


def _call_plan(leg, stage):
    """Step (4): the barriered ``decision_plan`` that every terminal decision references."""
    stage.plan = leg.plan(
        LegEvaluation(
            original=leg.bindings.proposal,
            final=stage.final,
            findings=stage.findings,
            gate_results=stage.gates,
            scope_verdict=stage.scope.verdict,
            account=stage.account,
            risk_effect=stage.risk_effect,
            risk_version=stage.account.risk_version,
            risk_state_digest=stage.account.risk_digest(),
        )
    )


def _call_intent(leg, stage):
    """Step (5): the canonical intent, serialized from that plan and barriered."""
    stage.intent = leg.intent(stage.plan)


def _call_authorize(leg, stage):
    """Step (6): the fresh fold, the drift refusal, and the origin's authority."""
    stage.permit = leg.authorize(stage.intent, stage.plan)


def _call_act(leg, stage):
    """Step (7): the one call that reaches a venue."""
    stage.ack = leg.act(stage.intent, stage.permit)


def _call_fold(leg, stage):
    """Step (8): record the outcome before the next proposal is considered."""
    stage.result = leg.fold(stage.intent, stage.permit, stage.ack, stage.findings)


#: How ``run`` calls each step: the arguments §5.13.1 fixes, and where each
#: answer is threaded. Keyed by ``vocab.LEG_STEPS`` so a step that lost its
#: caller refuses at import rather than at the first live tick.
_STEP_CALLS = pin_members(
    "leg.py's step table",
    {
        "guard": _call_guard,
        "refresh": _call_refresh,
        "rebind": _call_rebind,
        "plan": _call_plan,
        "intent": _call_intent,
        "authorize": _call_authorize,
        "act": _call_act,
        "fold": _call_fold,
    },
    LEG_STEPS,
    exact=True,
)
