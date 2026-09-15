"""The last gate between a minted permit and real money (§5.14, D14).

Everything before this point is a *record* of an intention; ``verify_and_call``
is the moment it becomes an order, so it is the one place in the package
where being slightly wrong costs money rather than a test failure. D14 fixes
what it does, in this order and with no caller-visible gap:

* it **rehashes the already frozen** ``EntryBatch`` **in memory** — the
  outputs to ``inputs_digest``, the watermarks to ``coverage_digest`` — and
  checks its source identity, never rereading a row: a re-read would be a
  second, later observation, and the whole point of the bound digest is that
  the order the venue receives was decided from the bytes the plan hashed;
* it re-earns the release from bytes and the runtime (D24), then requires
  **exact** equality with every digest and version the plan, intent and
  permit bound, rechecks every deadline the document declares (inclusive at
  the bound, like every freshness ladder here), **refreshes** the executor's
  authenticated scope, the lease and its fence, the accounting source tokens
  and the authority the permit names, re-runs the hard guards and the
  authority scope without adopting an amendment, and asks ``ActionPolicy``
  — the sole owner of the permission matrix, whose rule name is the reason
  when it refuses;
* it rebuilds §5.4's ``SafetyEpoch`` from those live values LAST and requires
  the digest the permit bound — the catch-all over everything the checks
  above proved one at a time, and the only check that covers a term none of
  them compares;
* it then invokes ``native_call(intent, permit, timeout_ms)`` **once**,
  synchronously, with ``timeout_ms`` the lesser of the document's
  ``execution.submit_timeout_ms`` and the permit's remaining lifetime.

Any mismatch is ``Ack(not_sent, reason=<the member that moved>)`` — the
closed ``VERIFY_REASONS`` — and nothing leaves the process. A raise or
timeout out of the native call is ``unknown``, because the request may
already have left, and an ``unknown`` **disables** every later send until
``reset_after_reconcile()`` — reconciliation is what resolves the ambiguous
reference (D13), never a resend. ``refuse_until_reconciled(reason)`` sets that
same disable from the outside, which is how §5.9's
``document.reconcile.on_mismatch: refuse`` stops submissions against a
mismatching venue without halting. A wiring defect (a non-``Intent``, a
non-``ActPermit``, a state without its batch, an uncallable callback) is a
``ProductionError`` and propagates: a defect must not be answered with a
polite ``Ack`` that reads like a routine refusal. The gate never replans and
never reauthorises in place; ``_NotArmed`` stays inside it and never crosses
the ``SubmittingExecutor`` contract (§5.7).
"""

import dataclasses
import hashlib
import json
import math
import re
from abc import ABC, abstractmethod
from threading import Lock
from types import MappingProxyType

from dskit.pipeline.trust import LifecycleAuthority, ReleaseKeyring, TrustedClock
from dskit.production.base import ProductionError, canonical_hash, pin_members
from dskit.production.coordination import scope_equal
from dskit.production.decider import DEFAULT_MAX_ARTIFACT_AGE
from dskit.production.executor import empty_ack
from dskit.production.guards import max_verdict
from dskit.production.records import ActPermit, EntryBatch, Intent, PolicyRequest, SafetyEpoch
from dskit.production.redact import get_logger
from dskit.production.release import parse_iso_duration, verify_release
from dskit.production.state import TickState
from dskit.production.vocab import (
    AUTHORITY_ROLES,
    LEG_ORIGINS,
    OPERATIONS,
    STATUSES,
    VERDICT_ORDER,
)

__all__ = [
    "VERIFY_REASONS",
    "HistoricalStudyCaptureDriver",
    "HistoricalStudyEnvelopePreflight",
    "HistoricalStudyRevocations",
    "HistoricalStudyVerifier",
    "NonAuthorizingAdr0125StructuralSignaturePreflight",
    "SubmissionVerifier",
]

_LOG = get_logger("verifier")

#: Every refusal the gate can give, one name per bound member or deadline,
#: sorted. A refusal from the action policy carries the RULE's name instead
#: (§5.14: the policy is the sole owner of that vocabulary). A ``vocab.py``
#: candidate, kept here until the vocabulary is ratified.
VERIFY_REASONS = (
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

# The reasons this module spells, bound to the tuple itself so a spelling
# cannot stray from the closed set.
(
    _AUTHORITY_SCOPE,
    _CALENDAR_CLOSED,
    _CLIENT_REF,
    _COVERAGE_DIGEST,
    _DECISION_PLAN_DIGEST,
    _DISABLED,
    _EVIDENCE_AGE,
    _EVIDENCE_DIGEST,
    _FENCING_TOKEN,
    _GUARD,
    _INPUT_DEADLINE,
    _INPUTS_DIGEST,
    _INTENT_DIGEST,
    _LEASE,
    _NOT_ARMED,
    _PERMIT_EXPIRED,
    _QUOTE_AGE,
    _QUOTE_DIGEST,
    _READINESS_DIGEST,
    _READINESS_EXPIRED,
    _RELEASE,
    _RELEASE_HASH,
    _RISK_STATE_DIGEST,
    _RISK_VERSION,
    _SAFETY_EPOCH,
    _SCOPE,
    _SOURCE_CONFIG,
) = VERIFY_REASONS

_NOT_SENT, _UNKNOWN = pin_members("verifier.py's statuses", ("not_sent", "unknown"), STATUSES)
_SUBMIT = pin_members("verifier.py's operation", ("submit",), OPERATIONS)[0]
#: The weakest guard verdict that refuses at the gate: an amendment here
#: describes an order nobody planned, recorded or authorised.
_AMEND_RANK = VERDICT_ORDER["amend"]


class _Refused(Exception):
    """Internal: one named refusal; a check raises it and the gate answers ``not_sent``."""

    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


class _NotArmed(_Refused):
    """Internal: no current authority for this permit — never crosses the executor contract."""

    def __init__(self):
        super().__init__(_NOT_ARMED)


# ---------------------------------------------------------------------------
# The authority axis is (origin) — a table, not a branch (§5.13.1)
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class _Refreshed:
    """Internal: what one refresh read from the live collaborators.

    The scope and the lease permit are read once and threaded, so the epoch
    recheck cannot see a second, later observation than the one the earlier
    checks passed on.
    """

    authority: object
    executor_scope: object
    lease: object


@dataclasses.dataclass(frozen=True)
class _RightScope:
    """The authority scope of a reduction right: exactly the instrument it names, no overlay."""

    allowlist: tuple
    limits_overlay: MappingProxyType


class _ModelOrigin:
    """A model leg is authorised by the current ordinary arm."""

    name, role = "model", "ordinary"

    @staticmethod
    def authority(arming, intent, permit, view, now):
        """Return the current arm the permit was minted under, or raise ``_NotArmed``."""
        arm = arming.current(view, now)
        if arm is None or permit.authority_id not in (arm.authority_id, intent.authority_id) or (
            arm.authority_id != intent.authority_id
        ):
            raise _NotArmed()
        return arm

    @staticmethod
    def scope(authority, permit):
        """Return the arm itself: its allowlist and tighten-only overlay."""
        return authority


class _ReductionOrigin:
    """A reduction leg is authorised by the single-use right its permit names."""

    name, role = "reduction", "reduction"

    @staticmethod
    def authority(arming, intent, permit, view, now):
        """Return the fold's reduction grant holding this leg's reserved right, or raise ``_NotArmed``."""
        right = view.reduction
        if (
            right is None
            or right.authority_id != permit.authority_id
            or intent.authority_id != permit.authority_id
            or permit.reduction_right_digest not in right.reserved
            or now >= right.expires_ms
        ):
            raise _NotArmed()
        return right

    @staticmethod
    def scope(authority, permit):
        """Return a scope admitting only the instrument the right names."""
        return _RightScope(allowlist=(permit.instrument,), limits_overlay=MappingProxyType({}))


_ORIGINS = pin_members(
    "verifier.py's origins",
    {origin.name: origin for origin in (_ModelOrigin, _ReductionOrigin)},
    LEG_ORIGINS,
    exact=True,
)
pin_members("verifier.py's authority roles", {o.role for o in _ORIGINS.values()}, AUTHORITY_ROLES)
_MODEL, _REDUCTION = _ORIGINS["model"], _ORIGINS["reduction"]


def _origin_of(permit):
    """Return the origin strategy a permit declares: a reduction right names one, a model leg none."""
    return _REDUCTION if permit.reduction_right_digest is not None else _MODEL


# ---------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------


class SubmissionVerifier:
    """The final verify-and-call gate a ``LiveExecutor`` delegates to (§5.14, D14).

    Built once by ``compose.py`` and held twice by design — by ``Safety``
    and by the ``LiveExecutor`` wrapper. Its twelve collaborators are the
    ones the checks need and cannot do without: a gate that refreshes
    quote, accounting, authority, executor identity and lease, and rechecks
    deadlines, hard guards and policy, cannot do any of it from
    ``(intent, permit, native_call)``.

    Parameters
    ----------
    executor : Executor
        Its authenticated ``execution_scope()`` is refreshed before every send.
    accounting : Accounting
        ``source_tokens(executor, at_ms)`` is the accounting refresh.
    lease : Lease
        ``current(scope)`` supplies the grip and its fencing token.
    arming : Arming
        ``current(view, at_ms)`` supplies the ordinary arm.
    guards : GuardChain
        ``check_all`` and ``check_authority_scope`` are re-run, without
        adopting an amendment.
    action_policy : ActionPolicy
        The sole owner of the permission matrix; its rule name is the reason.
    release : ReleaseManifest
        Re-verified from bytes and the runtime before every send (D24).
    inbox : ControlInbox
        A queued-but-unfolded command blocks the gate like a folded one.
    calendar : Calendar
        ``is_open(at_ms)`` is rechecked.
    document : ServeDocument
        Every deadline budget, the rung, the scope and the submit timeout.
    clock : Clock
        Every instant.
    health : Health
        ``state()`` is the health axis of the ``PolicyRequest``.

    Examples
    --------
    ::

        gate = SubmissionVerifier(
            executor, accounting, lease, arming, guards, ActionPolicy(), release,
            inbox, calendar, document, clock, health=health,
        )
        ack = gate.verify_and_call(intent, permit, state, venue._submit_native)
        ack.status  # 'open'
        gate.disabled  # False
    """

    def __init__(
        self,
        executor,
        accounting,
        lease,
        arming,
        guards,
        action_policy,
        release,
        inbox,
        calendar,
        document,
        clock,
        *,
        health,
    ):
        self._executor = executor
        self._accounting = accounting
        self._lease = lease
        self._arming = arming
        self._guards = guards
        self._policy = action_policy
        self._release = release
        self._inbox = inbox
        self._calendar = calendar
        self._document = document
        self._clock = clock
        self._health = health
        self._disabled = False

    @property
    def disabled(self):
        """Whether an ``unknown`` or a mismatch has stopped sends until reconciliation."""
        return self._disabled

    def refuse_until_reconciled(self, reason):
        """Stop sends until a clean reconciliation — §5.9's ``on_mismatch: refuse``.

        The same disable an ``unknown`` sets, because it is the same
        semantics: something the process cannot resolve by itself is
        outstanding, and only reconciliation resolves it. A second disable
        beside it would be a second thing to forget to clear. It is not a
        halt — that is what the other ``on_mismatch`` value does.

        Parameters
        ----------
        reason : str
            Why sends stopped, for the operator reading the log.

        Returns
        -------
        None
        """
        self._disabled = True
        _LOG.error("sends stop until reconciliation is clean: %s", reason)

    def reset_after_reconcile(self):
        """Re-enable sends: reconciliation resolved the ambiguous reference.

        Returns
        -------
        None
        """
        self._disabled = False

    def verify_and_call(self, intent, permit, state, native_call):
        """Recheck every binding, deadline, gate and rule; then send exactly once.

        Parameters
        ----------
        intent : Intent
            The canonical intent the leg appended and barriered.
        permit : ActPermit
            The authority's binding for it.
        state : TickState
            The leg's step-(2) state, carrying the frozen ``entry_batch``.
        native_call : callable
            ``native_call(intent, permit, timeout_ms) -> Ack`` — the child
            gateway's send, which enforces fencing, deadline and idempotency
            atomically.

        Returns
        -------
        Ack
            The gateway's answer; ``not_sent`` naming the member that moved
            (or the policy rule) when a check refused; ``unknown`` when the
            native call raised or timed out, after which the gate is
            ``disabled``.

        Raises
        ------
        ProductionError
            On a wiring defect: a non-``Intent``, a non-``ActPermit``, a
            state without an ``EntryBatch``, or an uncallable callback.
        """
        self._require(intent, permit, state, native_call)
        now = self._clock.now_ms()
        try:
            timeout_ms = self._checked(intent, permit, state, now)
        except _Refused as refusal:
            _LOG.info("refused %s: %s", intent.client_ref, refusal.reason)
            return empty_ack(intent.client_ref, now, _NOT_SENT, refusal.reason)
        try:
            ack = native_call(intent, permit, timeout_ms)
        except Exception as exc:
            self._disabled = True
            _LOG.error("native call for %s left an ambiguous outcome: %r", intent.client_ref, exc)
            return empty_ack(intent.client_ref, self._clock.now_ms(), _UNKNOWN, type(exc).__name__)
        if getattr(ack, "status", None) == _UNKNOWN:
            # A gateway that REPORTS its own timeout rather than raising leaves
            # the same ambiguity, and §5.14 disables on the `unknown`, not on
            # the raise: only reconciliation may resolve the reference.
            self._disabled = True
            _LOG.error("native call for %s answered %s; sends stop until reconciliation",
                       intent.client_ref, _UNKNOWN)
        return ack

    # -- argument discipline ---------------------------------------------

    @staticmethod
    def _require(intent, permit, state, native_call):
        """Refuse a wiring defect with a ``ProductionError`` naming every problem."""
        problems = []
        if not isinstance(intent, Intent):
            problems.append(f"verify_and_call takes an Intent, got {intent!r}")
        if not isinstance(permit, ActPermit):
            problems.append(f"verify_and_call takes an ActPermit, got {permit!r}")
        if not isinstance(state, TickState):
            problems.append(f"verify_and_call takes the leg's TickState, got {state!r}")
        elif not isinstance(state.entry_batch, EntryBatch):
            problems.append("verify_and_call needs state.entry_batch, the frozen EntryBatch this tick read")
        if not callable(native_call):
            problems.append(f"native_call must be callable, got {native_call!r}")
        if problems:
            raise ProductionError(problems)

    # -- the checks, in the pinned order ----------------------------------

    def _checked(self, intent, permit, state, now):
        """Run every check, in this order; return the bounded native timeout.

        The batch rehash, the release, the bound members, the deadlines, the
        refreshed scope/lease/tokens/authority, the hard guards and authority
        scope, the action policy — and LAST the safety epoch, which is the
        catch-all over everything the earlier checks proved individually and
        over the terms none of them compares.
        """
        if self._disabled:
            raise _Refused(_DISABLED)
        batch = state.entry_batch
        self._rehash(intent, permit, batch)
        self._release_bound(intent, permit, now)
        self._bindings(intent, permit, state)
        self._deadlines(intent, permit, batch, state, now)
        origin = _origin_of(permit)
        refreshed = self._refresh(origin, intent, permit, state, now)
        self._gates(origin, intent, permit, state, refreshed.authority)
        self._policy_rules(origin, permit, state)
        self._safety_epoch(intent, permit, batch, state, refreshed)
        return min(self._document.execution.submit_timeout_ms, permit.valid_until_ms - now)

    def _rehash(self, intent, permit, batch):
        """Recompute the frozen batch's digests in memory and check its source identity."""
        if not (
            canonical_hash(batch.outputs) == batch.inputs_digest
            == intent.inputs_digest == permit.inputs_digest
        ):
            raise _Refused(_INPUTS_DIGEST)
        marks = {key: mark.to_obj() for key, mark in batch.watermarks_by_key.items()}
        if not (
            canonical_hash(marks) == batch.coverage_digest
            == intent.coverage_digest == permit.coverage_digest
        ):
            raise _Refused(_COVERAGE_DIGEST)
        if batch.source_config_hash != self._release.source_config["hash"]:
            raise _Refused(_SOURCE_CONFIG)

    def _release_bound(self, intent, permit, now):
        """Re-earn the release from bytes and the runtime; require the bound release hash."""
        max_age = self._document.serving.max_artifact_age or DEFAULT_MAX_ARTIFACT_AGE
        try:
            verify_release(self._release, self._document.serving.run_dir, now, parse_iso_duration(max_age))
        except ProductionError as exc:
            _LOG.warning("release no longer verifies: %s", exc)
            raise _Refused(_RELEASE) from None
        if not (intent.release_hash == permit.release_hash == self._release.release_hash):
            raise _Refused(_RELEASE_HASH)

    def _bindings(self, intent, permit, state):
        """Require exact equality with every digest and version the plan, intent and permit bound."""
        account = state.account
        pairs = (
            (_INTENT_DIGEST, permit.intent_digest, intent.intent_digest()),
            (_DECISION_PLAN_DIGEST, permit.decision_plan_digest, intent.decision_plan_digest),
            (_CLIENT_REF, permit.client_ref, intent.client_ref),
            (_QUOTE_DIGEST, permit.quote_digest, intent.quote_digest),
            (_EVIDENCE_DIGEST, permit.evidence_digest, account.evidence_digest),
            (_RISK_STATE_DIGEST, permit.risk_state_digest, account.risk_digest()),
            (_RISK_VERSION, permit.risk_version, account.risk_version),
        )
        for reason, bound, actual in pairs:
            if bound != actual:
                raise _Refused(reason)
        readiness = state.view.readiness
        if readiness is None or permit.readiness_digest != readiness.readiness_digest:
            raise _Refused(_READINESS_DIGEST)

    def _deadlines(self, intent, permit, batch, state, now):
        """Recheck every document deadline, inclusive at the bound, against the OLDEST stamp."""
        schedule = self._document.schedule
        inputs_asof = min(batch.data_asof_ms, intent.inputs_asof_ms, permit.inputs_asof_ms)
        if now - inputs_asof > schedule.max_staleness_ms:
            raise _Refused(_INPUT_DEADLINE)
        if now - min(intent.quote_asof_ms, permit.quote_asof_ms) > schedule.max_quote_age_ms:
            raise _Refused(_QUOTE_AGE)
        evidence_asof = min(state.account.asof_ms, intent.evidence_asof_ms, permit.evidence_asof_ms)
        if now - evidence_asof > self._document.accounting.max_valuation_age_ms:
            raise _Refused(_EVIDENCE_AGE)
        if now >= min(state.view.readiness.valid_until_ms, permit.readiness_until_ms):
            raise _Refused(_READINESS_EXPIRED)
        if now >= permit.valid_until_ms:
            raise _Refused(_PERMIT_EXPIRED)
        if not self._calendar.is_open(now):
            raise _Refused(_CALENDAR_CLOSED)

    def _refresh(self, origin, intent, permit, state, now):
        """Refresh scope, lease, fence, source tokens and authority; return what was read."""
        scope = self._document.coordination.scope
        actual = self._executor.execution_scope()
        if not scope_equal(actual, scope, self._release.execution_scope, permit.lease_scope):
            raise _Refused(_SCOPE)
        held = self._lease.current(scope)
        if held is None or now >= held.expires_ms:
            raise _Refused(_LEASE)
        if held.fencing_token != permit.fencing_token:
            raise _Refused(_FENCING_TOKEN)
        executor_token, accounting_tokens = self._source_tokens(now)
        bound = permit.risk_version
        if (executor_token, accounting_tokens) != (bound.executor_token, bound.accounting_tokens):
            raise _Refused(_RISK_VERSION)
        return _Refreshed(
            authority=origin.authority(self._arming, intent, permit, state.view, now),
            executor_scope=actual,
            lease=held,
        )

    def _source_tokens(self, now):
        """Return accounting's ``(executor_token, accounting_tokens)`` in comparable form."""
        reported = self._accounting.source_tokens(self._executor, now)
        if isinstance(reported, (str, bytes)) or not isinstance(reported, (list, tuple)) or len(reported) != 2:
            raise ProductionError(
                [f"source_tokens must answer (executor_token, accounting_tokens), got {reported!r}"]
            )
        executor_token, accounting_tokens = reported
        return executor_token, None if accounting_tokens is None else tuple(accounting_tokens)

    def _gates(self, origin, intent, permit, state, authority):
        """Re-run the hard guards (no amendment adopted) and the authority scope."""
        final, findings = self._guards.check_all(intent.proposal, state)
        if final != intent.proposal or VERDICT_ORDER[max_verdict(findings)] >= _AMEND_RANK:
            raise _Refused(_GUARD)
        verdict = self._guards.check_authority_scope(intent.proposal, state, origin.scope(authority, permit))
        if not verdict.allowed:
            raise _Refused(_AUTHORITY_SCOPE)

    def _safety_epoch(self, intent, permit, batch, state, refreshed):
        """Rebuild §5.4's safety epoch from live values and require the bound digest.

        ``records.SafetyEpoch`` owns the terms, their order and the tag, so
        this is the same object the ``Authority`` minted — the point of a
        single owner. Every term is read from the source the individual
        rechecks above compare against, never from ``permit``: recomputing an
        epoch from the permit's own fields would agree by construction and
        refuse nothing. The two exceptions are ``risk_effect``, which the
        gate never holds a ``DecisionPlan`` to derive, and
        ``authority_scope_digest``, whose recipe belongs to the minting
        ``Authority``; both are the permit's, and everything around them is
        not.
        """
        account, view = state.account, state.view
        epoch = SafetyEpoch(
            release_hash=self._release.release_hash,
            readiness_digest=view.readiness.readiness_digest,
            readiness_until_ms=view.readiness.valid_until_ms,
            calendar_close_ms=self._calendar.window(
                SafetyEpoch.WINDOW, permit.checked_at_ms
            )[1],
            coverage_digest=batch.coverage_digest,
            inputs_digest=batch.inputs_digest,
            inputs_asof_ms=batch.data_asof_ms,
            quote_digest=intent.quote_digest,
            quote_asof_ms=intent.quote_asof_ms,
            evidence_digest=account.evidence_digest,
            evidence_asof_ms=account.asof_ms,
            risk_version=account.risk_version,
            risk_state_digest=account.risk_digest(),
            executor_scope=refreshed.executor_scope,
            health=self._health.state,
            breaker=view.breaker,
            rung=self._document.rung,
            risk_effect=permit.risk_effect,
            authority_id=refreshed.authority.authority_id,
            authority_scope_digest=permit.authority_scope_digest,
            pending_control=tuple(sorted(view.pending_control)),
            queued_control=len(self._inbox.pending()),
            lease_scope=refreshed.lease.scope,
            fencing_token=refreshed.lease.fencing_token,
        )
        if epoch.digest() != permit.safety_epoch_digest:
            raise _Refused(_SAFETY_EPOCH)

    def _policy_rules(self, origin, permit, state):
        """Ask the action policy; its rule name is the reason when it refuses."""
        view = state.view
        request = PolicyRequest(
            operation=_SUBMIT,
            risk_effect=permit.risk_effect,
            rung=self._document.rung,
            breaker=view.breaker,
            health=self._health.state,
            readiness=view.readiness.verdict,
            authority=origin.role,
            origin=origin.name,
            pending_control=bool(view.pending_control) or bool(self._inbox.pending()),
        )
        decision = self._policy.permits(request)
        if not decision.allowed:
            raise _Refused(decision.reason)


# ---------------------------------------------------------------------------
# ADR-0125 structural/signature preflight (F5a Packet 3)
# ---------------------------------------------------------------------------


class HistoricalStudyRevocations(ABC):
    """Verify one already-authenticated immutable revocation snapshot."""

    @abstractmethod
    def is_unrevoked(self, snapshot_sha256, key_id, key_version, now_ms):
        """Return True only when the named key is unrevoked at ``now_ms``."""


class NonAuthorizingAdr0125StructuralSignaturePreflight:
    """Opaque proof of local syntax and signatures, never lifecycle authority."""

    __slots__ = ()
    deployment_eligible = False

    def __new__(cls, *args, **kwargs):
        """Refuse direct construction; only the verifier may mint this result."""
        raise TypeError("opaque preflight result")

    def __copy__(self):
        """Refuse shallow copies of the opaque result."""
        raise TypeError("opaque preflight result")

    def __deepcopy__(self, memo):
        """Refuse deep copies of the opaque result."""
        raise TypeError("opaque preflight result")

    def __getstate__(self):
        """Refuse pickle state for the opaque result."""
        raise TypeError("opaque preflight result")

    def __reduce__(self):
        """Refuse pickle reduction for the opaque result."""
        raise TypeError("opaque preflight result")

    def __reduce_ex__(self, protocol):
        """Refuse protocol-specific pickle reduction for the opaque result."""
        raise TypeError("opaque preflight result")


_HS_HASH = re.compile(r"[0-9a-f]{64}")
_HS_ACTION_IDS = frozenset(
    (
        "tape-data-materialization",
        "tape-manifest-materialization",
        "A1",
        "A2",
        "A3",
        "A4",
    )
)
_HS_PROFILE_PHASES = frozenset(("bootstrap", "scope-action", "replay-pre-final"))

_HS_SHAPES = {
    "Key": {"key_id": "S", "key_version": "I"},
    "ActionSubject": {
        "kind": ("literal", "action"),
        "action_id": "ActionId",
        "action_intent_sha256": "H",
    },
    "ReplaySubject": {
        "kind": ("literal", "replay"),
        "replay_id": ("literal", "control", "crash-restart"),
        "replay_intent_sha256": "H",
    },
    "PublishedInputEntry": {
        "input_id": "S",
        "kind": "S",
        "root_ref": "S",
        "root_id": "S",
        "snapshot_version": "S",
        "member_manifest_sha256": "H",
        "producer_run_identity": "S",
        "producer_document_sha256": "H",
        "producer_node": "S",
        "producer_output": "S",
        "publication_receipt_schema": (
            "literal",
            "dskit.root-publication-receipt/v1",
            "dskit.lifecycle-publication-receipt/v2",
        ),
        "publication_receipt_sha256": "H",
        "contract_sha256": "H",
    },
    "InputContract": {
        "binding_id": "S",
        "consumer_node": "S",
        "consumer_input": "S",
        "source_kind": ("literal", "published-input", "predecessor-output"),
        "source_ref": "S",
        "output_schema": "S",
        "output_version": "S",
        "purpose": "S",
    },
    "OutputContract": {
        "output_name": "S",
        "output_schema": "S",
        "output_version": "S",
        "purpose": "S",
        "producer_node": "S",
        "producer_output": "S",
        "media_type": "S",
    },
    "PredecessorOutputRef": {
        "predecessor_action_id": "ActionId",
        "output_name": "S",
        "output_schema": "S",
        "output_version": "S",
        "purpose": "S",
    },
    "EdgeSetEntry": {
        "consumer_action_id": "ActionId",
        "binding_id": "S",
        "predecessor_action_id": "ActionId",
        "output_name": "S",
        "output_schema": "S",
        "output_version": "S",
        "purpose": "S",
    },
    "ActionIntent": {
        "schema": ("literal", "dskit.action-intent/v1"),
        "study_id": "S",
        "action_id": "ActionId",
        "consumer_document_contract_sha256": "H",
        "kind": "S",
        "topological_position": "I",
        "predecessor_action_ids": ("array", "ActionId"),
        "predecessor_output_refs": ("array", "PredecessorOutputRef"),
        "root_published_input_ids": ("array", "S"),
        "required_input_contracts": ("array", "InputContract"),
        "output_contract": ("shape", "OutputContract"),
        "closed_parameters_sha256": "H",
        "component_manifest_sha256": "H",
        "candidate_selection_sha256": "H",
        "policy_sha256": "H",
        "action_intent_sha256": "H",
    },
    "ReplayIntent": {
        "schema": ("literal", "dskit.replay-intent/v1"),
        "study_id": "S",
        "replay_id": ("literal", "control", "crash-restart"),
        "consumer_document_contract_sha256": "H",
        "required_input_contracts": ("array", "InputContract"),
        "environment_identity_sha256": "H",
        "execution_profile_sha256": "H",
        "component_manifest_sha256": "H",
        "crash_schedule_sha256": "H",
        "recovery_policy_sha256": "H",
        "policy_sha256": "H",
        "replay_intent_sha256": "H",
    },
    "CesEntry": {
        "binding_id": "S",
        "consumer_document_sha256": "H",
        "consumer_node": "S",
        "consumer_input": "S",
        "purpose": "S",
        "descriptor_root_ref": "S",
        "descriptor_snapshot_version": "S",
        "descriptor_document_sha256": "H",
        "descriptor_node": "S",
        "descriptor_output": "S",
        "descriptor_purpose": "S",
        "published_input": ("shape", "PublishedInputEntry"),
    },
    "PlannedCaptureEntry": {
        "schema": ("literal", "dskit.planned-capture-entry/v1"),
        "subject_ref": ("union", "ActionSubject", "ReplaySubject"),
        "binding_id": "S",
        "consumer_document_sha256": "H",
        "consumer_node": "S",
        "consumer_input": "S",
        "purpose": "S",
        "descriptor_root_ref": "S",
        "descriptor_snapshot_version": "S",
        "descriptor_document_sha256": "H",
        "descriptor_node": "S",
        "descriptor_output": "S",
        "descriptor_purpose": "S",
        "published_input": ("shape", "PublishedInputEntry"),
        "planned_entry_sha256": "H",
    },
    "ActionIntentSet": {
        "schema": ("literal", "dskit.action-intent-set/v1"),
        "entries": ("array", "ActionIntent"),
        "action_intent_set_sha256": "H",
    },
    "ReplayIntentSet": {
        "schema": ("literal", "dskit.replay-intent-set/v1"),
        "entries": ("array", "ReplayIntent"),
        "replay_intent_set_sha256": "H",
    },
}


_HS_AUTHORITY_REFS = (
    {
        "kind": ("literal", "scope-intent-gate-set"),
        "scope_intent_sha256": "H",
        "gate_set_sha256": "H",
    },
    {
        "kind": ("literal", "scope-authorization-action"),
        "scope_authorization_sha256": "H",
        "action_intent_sha256": "H",
    },
    {
        "kind": ("literal", "scope-authorization-replay"),
        "scope_authorization_sha256": "H",
        "replay_intent_sha256": "H",
    },
)

_HS_ENVELOPES = {
    "PublishedInputSet": {
        "schema": ("literal", "dskit.published-input-set/v2"),
        "study_id": "S",
        "phase": ("literal", "root-g1-g2", "stage-consumer", "replay-consumer"),
        "purpose": "S",
        "entries": ("array", "PublishedInputEntry"),
        "published_input_set_sha256": "H",
    },
    "ScopeIntent": {
        "schema": ("literal", "dskit.historical-study-scope-intent/v1"),
        "study_id": "S",
        "purpose": "S",
        "published_input_set_sha256": "H",
        "action_intents": ("array", "ActionIntent"),
        "action_intent_set_sha256": "H",
        "action_dag_sha256": "H",
        "edge_set_sha256": "H",
        "replay_intents": ("array", "ReplayIntent"),
        "replay_intent_set_sha256": "H",
        "environment_identity_sha256": "H",
        "execution_profile_sha256": "H",
        "component_manifest_sha256": "H",
        "candidate_inventory_sha256": "H",
        "policy_set_sha256": "H",
        "historical_study_scope_intent_sha256": "H",
    },
    "CES": {
        "schema": ("literal", "dskit.capture-expectation-set/v1"),
        "study_id": "S",
        "phase": "ProfilePhase",
        "subject_ref": ("union", "ActionSubject", "ReplaySubject"),
        "consumer_document_contract_sha256": "H",
        "consumer_document_sha256": "H",
        "purpose": "S",
        "component_manifest_sha256": "H",
        "published_input_set_sha256": "H",
        "entries": ("array", "CesEntry"),
        "capture_expectation_set_sha256": "H",
    },
    "PEA": {
        "schema": ("literal", "dskit.plan-evaluation-authorization/v1"),
        "study_id": "S",
        "phase": "ProfilePhase",
        "subject_ref": ("union", "ActionSubject", "ReplaySubject"),
        "consumer_document_contract_sha256": "H",
        "consumer_document_sha256": "H",
        "purpose": "S",
        "component_manifest_sha256": "H",
        "published_input_set_sha256": "H",
        "capture_expectation_set_sha256": "H",
        "authority_ref": "AuthorityRef",
        "plan_evaluation_authorization_sha256": "H",
    },
    "BVP": {
        "schema": ("literal", "dskit.broker-verified-plan/v1"),
        "plan_evaluation_authorization_sha256": "H",
        "capture_expectation_set_sha256": "H",
        "subject_ref": ("union", "ActionSubject", "ReplaySubject"),
        "study_id": "S",
        "consumer_document_contract_sha256": "H",
        "consumer_document_sha256": "H",
        "purpose": "S",
        "component_manifest_sha256": "H",
        "planning_rules_sha256": "H",
        "plan_sha256": "H",
        "planned_capture_set_sha256": "H",
        "broker_verified_plan_sha256": "H",
    },
    "CAS": {
        "schema": ("literal", "dskit.capture-admission-set/v1"),
        "study_id": "S",
        "subject_ref": ("union", "ActionSubject", "ReplaySubject"),
        "consumer_document_contract_sha256": "H",
        "consumer_document_sha256": "H",
        "purpose": "S",
        "plan_evaluation_authorization_sha256": "H",
        "capture_expectation_set_sha256": "H",
        "broker_verified_plan_sha256": "H",
        "planned_capture_set_sha256": "H",
        "entries": ("array", "PlannedCaptureEntry"),
        "capture_admission_set_sha256": "H",
    },
    "ActionAdmission": {
        "schema": ("literal", "dskit.action-execution-admission/v1"),
        "study_id": "S",
        "scope_authorization_sha256": "H",
        "action_id": "ActionId",
        "action_intent_sha256": "H",
        "historical_study_stage_admission_sha256": "H",
        "plan_evaluation_authorization_sha256": "H",
        "capture_expectation_set_sha256": "H",
        "broker_verified_plan_sha256": "H",
        "plan_sha256": "H",
        "planned_capture_set_sha256": "H",
        "capture_admission_set_sha256": "H",
        "logical_execution_id": "S",
        "run_id": "S",
        "recovery_journal_sha256": "H",
        "recovery_fence_sha256": "H",
        "recovery_attempt_rules_sha256": "H",
        "action_execution_admission_sha256": "H",
    },
    "ReplayAdmission": {
        "schema": ("literal", "dskit.final-replay-admission/v1"),
        "study_id": "S",
        "final_manifest_sha256": "H",
        "final_replay_entry_sha256": "H",
        "replay_id": ("literal", "control", "crash-restart"),
        "replay_intent_sha256": "H",
        "plan_evaluation_authorization_sha256": "H",
        "capture_expectation_set_sha256": "H",
        "broker_verified_plan_sha256": "H",
        "plan_sha256": "H",
        "planned_capture_set_sha256": "H",
        "capture_admission_set_sha256": "H",
        "logical_execution_id": "S",
        "run_id": "S",
        "recovery_journal_sha256": "H",
        "recovery_fence_sha256": "H",
        "recovery_attempt_rules_sha256": "H",
        "final_replay_admission_sha256": "H",
    },
}

_HS_SIGNED_SUFFIX = {
    "issuance_basis_sha256": "H",
    "issuer_role": "S",
    "key_usage": "S",
    "signature_alg": ("literal", "Ed25519"),
    "issued_at_ms": "I",
    "not_before_ms": "I",
    "expires_at_ms": "I",
    "revocation_snapshot_sha256": "H",
    "key": ("shape", "Key"),
    "signature": "S",
}

_HS_SELF_FIELDS = {
    "PublishedInputSet": "published_input_set_sha256",
    "ScopeIntent": "historical_study_scope_intent_sha256",
    "CES": "capture_expectation_set_sha256",
    "PEA": "plan_evaluation_authorization_sha256",
    "BVP": "broker_verified_plan_sha256",
    "CAS": "capture_admission_set_sha256",
    "ActionAdmission": "action_execution_admission_sha256",
    "ReplayAdmission": "final_replay_admission_sha256",
}

_HS_ROLE_USE = {
    "root_pis": ("data-publisher", "published-input-set-g1-g2"),
    "phase_pis": ("study-lifecycle", "published-input-set-study"),
    "scope": ("study-lifecycle", "historical-study-scope-intent"),
    "ces": ("study-lifecycle", "capture-expectation"),
    "pea": ("security-broker", "plan-evaluation"),
    "bvp": ("security-broker", "plan-verifier"),
    "cas": ("study-lifecycle", "capture-admission"),
    "action_admission": ("study-lifecycle", "action-execution-admission"),
    "replay_admission": ("study-lifecycle", "final-replay-admission"),
}


def _hs_refuse(condition, message="ADR-0125 preflight refused"):
    if not condition:
        raise ValueError(message)


def _hs_canonical_bytes(value):
    def admissible(item):
        if item is None or type(item) in (str, bool, int):
            return
        if type(item) is float:
            _hs_refuse(math.isfinite(item))
            return
        if type(item) is list:
            for child in item:
                admissible(child)
            return
        if type(item) is dict:
            for key, child in item.items():
                _hs_refuse(type(key) is str)
                admissible(child)
            return
        raise ValueError("non-JSON value")

    admissible(value)
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("ascii")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise ValueError("canonical JSON refused") from exc


def _hs_parse_canonical(raw):
    if type(raw) is not bytes:
        raise TypeError("exact bytes are required")

    def reject_constant(value):
        raise ValueError("non-finite JSON constant")

    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result

    try:
        value = json.loads(
            raw.decode("utf-8", "strict"),
            parse_constant=reject_constant,
            object_pairs_hook=unique_object,
        )
    except (UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise ValueError("canonical JSON refused") from exc
    _hs_refuse(_hs_canonical_bytes(value) == raw, "non-canonical JSON")
    return value


def _hs_validate_spec(value, spec):
    if spec == "S":
        _hs_refuse(type(value) is str)
    elif spec == "H":
        _hs_refuse(type(value) is str and _HS_HASH.fullmatch(value) is not None)
    elif spec == "I":
        _hs_refuse(type(value) is int)
    elif spec == "ActionId":
        _hs_refuse(type(value) is str and value in _HS_ACTION_IDS)
    elif spec == "ProfilePhase":
        _hs_refuse(type(value) is str and value in _HS_PROFILE_PHASES)
    elif spec == "AuthorityRef":
        matches = 0
        for fields in _HS_AUTHORITY_REFS:
            try:
                _hs_validate_object(value, fields)
            except ValueError:
                continue
            matches += 1
        _hs_refuse(matches == 1)
    elif type(spec) is tuple and spec[0] == "literal":
        _hs_refuse(type(value) is str and value in spec[1:])
    elif type(spec) is tuple and spec[0] == "shape":
        _hs_validate_object(value, _HS_SHAPES[spec[1]])
    elif type(spec) is tuple and spec[0] == "array":
        _hs_refuse(type(value) is list)
        for item in value:
            child = spec[1]
            if child in _HS_SHAPES:
                _hs_validate_object(item, _HS_SHAPES[child])
            else:
                _hs_validate_spec(item, child)
    elif type(spec) is tuple and spec[0] == "union":
        matches = 0
        for name in spec[1:]:
            try:
                _hs_validate_object(value, _HS_SHAPES[name])
            except ValueError:
                continue
            matches += 1
        _hs_refuse(matches == 1)
    else:
        raise ValueError("unknown preflight schema")


def _hs_validate_object(value, fields):
    _hs_refuse(type(value) is dict and set(value) == set(fields))
    for name, spec in fields.items():
        _hs_validate_spec(value[name], spec)


def _hs_validate_envelope(value, name):
    fields = dict(_HS_ENVELOPES[name])
    fields.update(_HS_SIGNED_SUFFIX)
    _hs_validate_object(value, fields)


def _hs_self_digest(value, self_name, *, signed=False):
    payload = dict(value)
    payload.pop(self_name)
    if signed:
        payload.pop("signature")
    preimage = _hs_canonical_bytes(payload)
    _hs_refuse(hashlib.sha256(preimage).hexdigest() == value[self_name])
    return preimage


def _hs_strict_sorted(items, key):
    keys = [key(item) for item in items]
    _hs_refuse(all(left < right for left, right in zip(keys, keys[1:])))


def _hs_scalar_key(value):
    return _hs_canonical_bytes(value)


def _hs_tuple_key(*values):
    return _hs_canonical_bytes(list(values))


class HistoricalStudyEnvelopePreflight:
    """Validate a fixed synthetic ADR-0125 byte tuple without granting authority."""

    __slots__ = ("_keyring", "_clock", "_revocations")

    def __init__(self, keyring, clock, revocations):
        if not isinstance(keyring, ReleaseKeyring):
            raise TypeError("ReleaseKeyring is required")
        if not isinstance(clock, TrustedClock):
            raise TypeError("TrustedClock is required")
        if not isinstance(revocations, HistoricalStudyRevocations):
            raise TypeError("HistoricalStudyRevocations is required")
        self._keyring = keyring
        self._clock = clock
        self._revocations = revocations

    def verify(
        self,
        root_pis_bytes,
        phase_pis_bytes,
        scope_intent_bytes,
        action_intent_set_bytes,
        edge_set_bytes,
        replay_intent_set_bytes,
        ces_bytes,
        pea_bytes,
        bvp_bytes,
        cas_bytes,
        selected_admission_bytes,
    ):
        """Return an opaque no-effect result for the exact eleven-byte tuple."""
        raw = {
            "root_pis": root_pis_bytes,
            "phase_pis": phase_pis_bytes,
            "scope": scope_intent_bytes,
            "action_set": action_intent_set_bytes,
            "edges": edge_set_bytes,
            "replay_set": replay_intent_set_bytes,
            "ces": ces_bytes,
            "pea": pea_bytes,
            "bvp": bvp_bytes,
            "cas": cas_bytes,
            "admission": selected_admission_bytes,
        }
        values = {name: _hs_parse_canonical(value) for name, value in raw.items()}
        admission_name = self._validate_shapes_and_self(values)
        profile, selected = self._validate_profiles(values, raw, admission_name)
        self._validate_ordering(values)
        self._validate_adjacency(values, raw, profile, selected, admission_name)
        self._verify_trust(values, profile, admission_name)
        return object.__new__(NonAuthorizingAdr0125StructuralSignaturePreflight)

    @staticmethod
    def _validate_shapes_and_self(values):
        envelope_names = {
            "root_pis": "PublishedInputSet",
            "phase_pis": "PublishedInputSet",
            "scope": "ScopeIntent",
            "ces": "CES",
            "pea": "PEA",
            "bvp": "BVP",
            "cas": "CAS",
        }
        for slot, name in envelope_names.items():
            _hs_validate_envelope(values[slot], name)
            _hs_self_digest(values[slot], _HS_SELF_FIELDS[name], signed=True)

        admission_schema = (
            values["admission"].get("schema")
            if type(values["admission"]) is dict
            else None
        )
        if admission_schema == "dskit.action-execution-admission/v1":
            admission_name = "ActionAdmission"
        elif admission_schema == "dskit.final-replay-admission/v1":
            admission_name = "ReplayAdmission"
        else:
            raise ValueError("selected admission schema refused")
        _hs_validate_envelope(values["admission"], admission_name)
        _hs_self_digest(
            values["admission"], _HS_SELF_FIELDS[admission_name], signed=True
        )

        _hs_validate_object(values["action_set"], _HS_SHAPES["ActionIntentSet"])
        _hs_validate_object(values["replay_set"], _HS_SHAPES["ReplayIntentSet"])
        _hs_refuse(type(values["edges"]) is list)
        for edge in values["edges"]:
            _hs_validate_object(edge, _HS_SHAPES["EdgeSetEntry"])

        action_groups = (
            values["scope"]["action_intents"],
            values["action_set"]["entries"],
        )
        for actions in action_groups:
            for action in actions:
                _hs_self_digest(action, "action_intent_sha256")
        replay_groups = (
            values["scope"]["replay_intents"],
            values["replay_set"]["entries"],
        )
        for replays in replay_groups:
            for replay in replays:
                _hs_self_digest(replay, "replay_intent_sha256")
        _hs_self_digest(values["action_set"], "action_intent_set_sha256")
        _hs_self_digest(values["replay_set"], "replay_intent_set_sha256")
        for entry in values["cas"]["entries"]:
            _hs_self_digest(entry, "planned_entry_sha256")
        return admission_name

    @staticmethod
    def _validate_profiles(values, raw, admission_name):
        root_pis = values["root_pis"]
        phase_pis = values["phase_pis"]
        ces = values["ces"]
        pea = values["pea"]
        profile = ces["phase"]
        _hs_refuse(pea["phase"] == profile)
        _hs_refuse(root_pis["phase"] == "root-g1-g2")

        subjects = (
            ces["subject_ref"],
            pea["subject_ref"],
            values["bvp"]["subject_ref"],
            values["cas"]["subject_ref"],
        )
        subject_bytes = [_hs_canonical_bytes(subject) for subject in subjects]
        _hs_refuse(len(set(subject_bytes)) == 1)
        subject = subjects[0]

        if profile == "bootstrap":
            _hs_refuse(raw["root_pis"] == raw["phase_pis"])
            _hs_refuse(phase_pis["phase"] == "root-g1-g2")
            _hs_refuse(
                subject["kind"] == "action" and admission_name == "ActionAdmission"
            )
            ref = pea["authority_ref"]
            _hs_refuse(ref["kind"] == "scope-intent-gate-set")
            _hs_refuse(
                ref["scope_intent_sha256"]
                == values["scope"]["historical_study_scope_intent_sha256"]
            )
        elif profile == "scope-action":
            _hs_refuse(phase_pis["phase"] == "stage-consumer")
            _hs_refuse(
                subject["kind"] == "action" and admission_name == "ActionAdmission"
            )
            ref = pea["authority_ref"]
            _hs_refuse(ref["kind"] == "scope-authorization-action")
            _hs_refuse(ref["action_intent_sha256"] == subject["action_intent_sha256"])
        elif profile == "replay-pre-final":
            _hs_refuse(phase_pis["phase"] == "replay-consumer")
            _hs_refuse(
                subject["kind"] == "replay" and admission_name == "ReplayAdmission"
            )
            ref = pea["authority_ref"]
            _hs_refuse(ref["kind"] == "scope-authorization-replay")
            _hs_refuse(ref["replay_intent_sha256"] == subject["replay_intent_sha256"])
        else:
            raise ValueError("profile refused")

        if subject["kind"] == "action":
            matches = [
                child
                for child in values["scope"]["action_intents"]
                if child["action_id"] == subject["action_id"]
                and child["action_intent_sha256"] == subject["action_intent_sha256"]
            ]
        else:
            matches = [
                child
                for child in values["scope"]["replay_intents"]
                if child["replay_id"] == subject["replay_id"]
                and child["replay_intent_sha256"] == subject["replay_intent_sha256"]
            ]
        _hs_refuse(len(matches) == 1)
        return profile, matches[0]

    @staticmethod
    def _validate_ordering(values):
        for slot in ("root_pis", "phase_pis"):
            _hs_strict_sorted(
                values[slot]["entries"],
                lambda item: _hs_scalar_key(item["input_id"]),
            )

        def action_key(item):
            return _hs_tuple_key(item["topological_position"], item["action_id"])

        for actions in (
            values["scope"]["action_intents"],
            values["action_set"]["entries"],
        ):
            _hs_strict_sorted(actions, action_key)
            _hs_refuse(
                len(actions) == len(_HS_ACTION_IDS)
                and {item["action_id"] for item in actions} == _HS_ACTION_IDS
            )
            for action in actions:
                _hs_strict_sorted(action["predecessor_action_ids"], _hs_scalar_key)
                _hs_strict_sorted(
                    action["predecessor_output_refs"],
                    lambda item: _hs_tuple_key(
                        item["predecessor_action_id"],
                        item["output_name"],
                        item["output_schema"],
                        item["output_version"],
                        item["purpose"],
                    ),
                )
                _hs_strict_sorted(action["root_published_input_ids"], _hs_scalar_key)
                _hs_strict_sorted(
                    action["required_input_contracts"],
                    lambda item: _hs_scalar_key(item["binding_id"]),
                )

        for replays in (
            values["scope"]["replay_intents"],
            values["replay_set"]["entries"],
        ):
            _hs_strict_sorted(replays, lambda item: _hs_scalar_key(item["replay_id"]))
            _hs_refuse(
                [item["replay_id"] for item in replays] == ["control", "crash-restart"]
            )
            for replay in replays:
                _hs_strict_sorted(
                    replay["required_input_contracts"],
                    lambda item: _hs_scalar_key(item["binding_id"]),
                )

        _hs_strict_sorted(
            values["edges"],
            lambda item: _hs_tuple_key(
                item["consumer_action_id"],
                item["binding_id"],
                item["predecessor_action_id"],
                item["output_name"],
                item["output_schema"],
                item["output_version"],
                item["purpose"],
            ),
        )
        _hs_strict_sorted(values["ces"]["entries"], _hs_canonical_bytes)
        _hs_strict_sorted(
            values["cas"]["entries"],
            lambda item: (
                _hs_scalar_key(item["planned_entry_sha256"]),
                _hs_canonical_bytes(item),
            ),
        )

    @staticmethod
    def _validate_adjacency(values, raw, profile, selected, admission_name):
        root_pis = values["root_pis"]
        phase_pis = values["phase_pis"]
        scope = values["scope"]
        action_set = values["action_set"]
        replay_set = values["replay_set"]
        ces = values["ces"]
        pea = values["pea"]
        bvp = values["bvp"]
        cas = values["cas"]
        admission = values["admission"]

        _hs_refuse(
            len(
                {
                    item["study_id"]
                    for item in (
                        root_pis,
                        phase_pis,
                        scope,
                        ces,
                        pea,
                        bvp,
                        cas,
                        admission,
                    )
                }
            )
            == 1
        )
        _hs_refuse(
            scope["published_input_set_sha256"]
            == root_pis["published_input_set_sha256"]
        )
        _hs_refuse(
            _hs_canonical_bytes(scope["action_intents"])
            == _hs_canonical_bytes(action_set["entries"])
        )
        _hs_refuse(
            scope["action_intent_set_sha256"] == action_set["action_intent_set_sha256"]
        )
        _hs_refuse(
            _hs_canonical_bytes(scope["replay_intents"])
            == _hs_canonical_bytes(replay_set["entries"])
        )
        _hs_refuse(
            scope["replay_intent_set_sha256"] == replay_set["replay_intent_set_sha256"]
        )
        _hs_refuse(hashlib.sha256(raw["edges"]).hexdigest() == scope["edge_set_sha256"])

        for child in scope["action_intents"] + scope["replay_intents"]:
            _hs_refuse(child["study_id"] == scope["study_id"])
            _hs_refuse(
                child["component_manifest_sha256"] == scope["component_manifest_sha256"]
            )
        _hs_refuse(
            all(
                item["component_manifest_sha256"] == scope["component_manifest_sha256"]
                for item in (ces, pea, bvp)
            )
        )
        if profile == "replay-pre-final":
            _hs_refuse(
                selected["environment_identity_sha256"]
                == scope["environment_identity_sha256"]
            )
            _hs_refuse(
                selected["execution_profile_sha256"]
                == scope["execution_profile_sha256"]
            )

        _hs_refuse(
            all(
                item["consumer_document_contract_sha256"]
                == selected["consumer_document_contract_sha256"]
                for item in (ces, pea, bvp, cas)
            )
        )
        _hs_refuse(len({item["purpose"] for item in (scope, ces, pea, bvp, cas)}) == 1)
        _hs_refuse(
            len({item["consumer_document_sha256"] for item in (ces, pea, bvp, cas)})
            == 1
        )
        _hs_refuse(
            ces["published_input_set_sha256"]
            == pea["published_input_set_sha256"]
            == phase_pis["published_input_set_sha256"]
        )

        if profile == "replay-pre-final":
            _hs_refuse(
                all(
                    contract["source_kind"] != "predecessor-output"
                    for contract in selected["required_input_contracts"]
                )
            )

        HistoricalStudyEnvelopePreflight._validate_graph(
            scope, values["edges"], root_pis
        )
        HistoricalStudyEnvelopePreflight._validate_capture_entries(
            ces, cas, phase_pis, selected
        )
        HistoricalStudyEnvelopePreflight._validate_digest_chain(
            ces, pea, bvp, cas, admission
        )

        subject = ces["subject_ref"]
        if admission_name == "ActionAdmission":
            _hs_refuse(admission["action_id"] == subject["action_id"])
            _hs_refuse(
                admission["action_intent_sha256"] == subject["action_intent_sha256"]
            )
        else:
            _hs_refuse(admission["replay_id"] == subject["replay_id"])
            _hs_refuse(
                admission["replay_intent_sha256"] == subject["replay_intent_sha256"]
            )

    @staticmethod
    def _validate_graph(scope, edges, root_pis):
        actions = scope["action_intents"]
        by_id = {action["action_id"]: action for action in actions}
        root_ids = [entry["input_id"] for entry in root_pis["entries"]]

        for action in actions:
            refs = action["predecessor_output_refs"]
            projected = []
            for ref in refs:
                if ref["predecessor_action_id"] not in projected:
                    projected.append(ref["predecessor_action_id"])
            _hs_refuse(projected == action["predecessor_action_ids"])
            for predecessor_id in action["predecessor_action_ids"]:
                _hs_refuse(
                    predecessor_id in by_id and predecessor_id != action["action_id"]
                )
                _hs_refuse(
                    by_id[predecessor_id]["topological_position"]
                    < action["topological_position"]
                )
            if not action["predecessor_action_ids"]:
                for input_id in action["root_published_input_ids"]:
                    _hs_refuse(root_ids.count(input_id) == 1)
            else:
                _hs_refuse(not action["root_published_input_ids"])

            for contract in action["required_input_contracts"]:
                same_binding_edges = [
                    edge
                    for edge in edges
                    if edge["consumer_action_id"] == action["action_id"]
                    and edge["binding_id"] == contract["binding_id"]
                ]
                if contract["source_kind"] == "published-input":
                    _hs_refuse(not same_binding_edges)
                    continue
                matches = []
                for edge in same_binding_edges:
                    refs_for_edge = [
                        ref
                        for ref in refs
                        if all(
                            ref[name] == edge[name]
                            for name in (
                                "predecessor_action_id",
                                "output_name",
                                "output_schema",
                                "output_version",
                                "purpose",
                            )
                        )
                    ]
                    if (
                        len(refs_for_edge) == 1
                        and contract["output_schema"] == edge["output_schema"]
                        and contract["output_version"] == edge["output_version"]
                        and contract["purpose"] == edge["purpose"]
                    ):
                        matches.append((edge, refs_for_edge[0]))
                _hs_refuse(len(matches) == 1)
                edge, ref = matches[0]
                producer = by_id[edge["predecessor_action_id"]]["output_contract"]
                for name in (
                    "output_name",
                    "output_schema",
                    "output_version",
                    "purpose",
                ):
                    _hs_refuse(ref[name] == producer[name])

            for ref in refs:
                matching_edges = [
                    edge
                    for edge in edges
                    if edge["consumer_action_id"] == action["action_id"]
                    and all(
                        edge[name] == ref[name]
                        for name in (
                            "predecessor_action_id",
                            "output_name",
                            "output_schema",
                            "output_version",
                            "purpose",
                        )
                    )
                ]
                _hs_refuse(len(matching_edges) == 1)
                edge = matching_edges[0]
                matching_contracts = [
                    contract
                    for contract in action["required_input_contracts"]
                    if contract["source_kind"] == "predecessor-output"
                    and contract["binding_id"] == edge["binding_id"]
                    and contract["output_schema"] == ref["output_schema"]
                    and contract["output_version"] == ref["output_version"]
                    and contract["purpose"] == ref["purpose"]
                ]
                _hs_refuse(len(matching_contracts) == 1)
                producer = by_id[ref["predecessor_action_id"]]["output_contract"]
                for name in (
                    "output_name",
                    "output_schema",
                    "output_version",
                    "purpose",
                ):
                    _hs_refuse(ref[name] == producer[name])

        for edge in edges:
            _hs_refuse(edge["consumer_action_id"] in by_id)
            consumer = by_id[edge["consumer_action_id"]]
            contracts = [
                contract
                for contract in consumer["required_input_contracts"]
                if contract["source_kind"] == "predecessor-output"
                and contract["binding_id"] == edge["binding_id"]
                and contract["output_schema"] == edge["output_schema"]
                and contract["output_version"] == edge["output_version"]
                and contract["purpose"] == edge["purpose"]
            ]
            refs = [
                ref
                for ref in consumer["predecessor_output_refs"]
                if all(
                    ref[name] == edge[name]
                    for name in (
                        "predecessor_action_id",
                        "output_name",
                        "output_schema",
                        "output_version",
                        "purpose",
                    )
                )
            ]
            _hs_refuse(len(contracts) == len(refs) == 1)

    @staticmethod
    def _validate_capture_entries(ces, cas, phase_pis, selected):
        published_contracts = [
            contract
            for contract in selected["required_input_contracts"]
            if contract["source_kind"] == "published-input"
        ]

        def contract_key(item):
            return (
                item["binding_id"],
                item["consumer_node"],
                item["consumer_input"],
                item["purpose"],
            )

        _hs_refuse(
            sorted(contract_key(entry) for entry in ces["entries"])
            == sorted(contract_key(contract) for contract in published_contracts)
        )

        phase_entries = phase_pis["entries"]
        converted = []
        for entry in ces["entries"]:
            _hs_refuse(
                entry["consumer_document_sha256"] == ces["consumer_document_sha256"]
            )
            _hs_refuse(entry["purpose"] == ces["purpose"])
            pis = entry["published_input"]
            comparisons = (
                ("descriptor_root_ref", "root_ref"),
                ("descriptor_snapshot_version", "snapshot_version"),
                ("descriptor_document_sha256", "producer_document_sha256"),
                ("descriptor_node", "producer_node"),
                ("descriptor_output", "producer_output"),
            )
            for descriptor, published in comparisons:
                _hs_refuse(entry[descriptor] == pis[published])
            _hs_refuse(
                entry["descriptor_purpose"] == entry["purpose"] == ces["purpose"]
            )
            matches = [
                candidate
                for candidate in phase_entries
                if candidate["input_id"] == pis["input_id"]
            ]
            _hs_refuse(
                len(matches) == 1
                and _hs_canonical_bytes(matches[0]) == _hs_canonical_bytes(pis)
            )
            planned = dict(entry)
            planned["schema"] = "dskit.planned-capture-entry/v1"
            planned["subject_ref"] = ces["subject_ref"]
            planned["planned_entry_sha256"] = hashlib.sha256(
                _hs_canonical_bytes(planned)
            ).hexdigest()
            converted.append(planned)
        converted.sort(
            key=lambda item: (
                _hs_scalar_key(item["planned_entry_sha256"]),
                _hs_canonical_bytes(item),
            )
        )
        _hs_refuse(
            _hs_canonical_bytes(converted) == _hs_canonical_bytes(cas["entries"])
        )

    @staticmethod
    def _validate_digest_chain(ces, pea, bvp, cas, admission):
        _hs_refuse(
            ces["capture_expectation_set_sha256"]
            == pea["capture_expectation_set_sha256"]
            == bvp["capture_expectation_set_sha256"]
            == cas["capture_expectation_set_sha256"]
            == admission["capture_expectation_set_sha256"]
        )
        _hs_refuse(
            pea["plan_evaluation_authorization_sha256"]
            == bvp["plan_evaluation_authorization_sha256"]
            == cas["plan_evaluation_authorization_sha256"]
            == admission["plan_evaluation_authorization_sha256"]
        )
        _hs_refuse(
            bvp["broker_verified_plan_sha256"]
            == cas["broker_verified_plan_sha256"]
            == admission["broker_verified_plan_sha256"]
        )
        _hs_refuse(
            bvp["plan_sha256"] == admission["plan_sha256"]
            and bvp["planned_capture_set_sha256"]
            == cas["planned_capture_set_sha256"]
            == admission["planned_capture_set_sha256"]
            and cas["capture_admission_set_sha256"]
            == admission["capture_admission_set_sha256"]
        )

    def _verify_trust(self, values, profile, admission_name):
        try:
            now_ms = self._clock.now_ms()
        except BaseException as exc:
            raise ValueError("trusted clock refused") from exc
        _hs_refuse(type(now_ms) is int)

        phase_slot = "root_pis" if profile == "bootstrap" else "phase_pis"
        slots = (
            ("root_pis", "PublishedInputSet", "root_pis"),
            ("phase_pis", "PublishedInputSet", phase_slot),
            ("scope", "ScopeIntent", "scope"),
            ("ces", "CES", "ces"),
            ("pea", "PEA", "pea"),
            ("bvp", "BVP", "bvp"),
            ("cas", "CAS", "cas"),
            (
                "admission",
                admission_name,
                "action_admission"
                if admission_name == "ActionAdmission"
                else "replay_admission",
            ),
        )
        for slot, envelope_name, role_slot in slots:
            envelope = values[slot]
            role, usage = _HS_ROLE_USE[role_slot]
            _hs_refuse(
                envelope["issuer_role"] == role and envelope["key_usage"] == usage
            )
            _hs_refuse(
                envelope["not_before_ms"]
                <= envelope["issued_at_ms"]
                <= envelope["expires_at_ms"]
            )
            _hs_refuse(envelope["not_before_ms"] <= now_ms <= envelope["expires_at_ms"])
            preimage = _hs_self_digest(
                envelope, _HS_SELF_FIELDS[envelope_name], signed=True
            )
            key = envelope["key"]
            try:
                verified = self._keyring.verify(
                    key["key_id"],
                    key["key_version"],
                    envelope["issued_at_ms"],
                    preimage,
                    envelope["signature"],
                )
                unrevoked = self._revocations.is_unrevoked(
                    envelope["revocation_snapshot_sha256"],
                    key["key_id"],
                    key["key_version"],
                    now_ms,
                )
            except BaseException as exc:
                raise ValueError("signature or revocation refused") from exc
            _hs_refuse(verified is True and unrevoked is True)


_REQUIRED_PLAN = ("scope_intent", "ces", "pea", "bvp", "cas", "admission")
_DOOR_LOCK = Lock()


def _make_spend_pair():
    """Shared live/spent id sets plus pins; not instance state."""
    return (set(), set(), {})


class _SpendCell:
    """Identity token for one-use admission; extra mints stay spent."""

    __slots__ = ()

    def __new__(cls, *args, **kwargs):
        """Refuse construction except through the doorway mint."""
        raise TypeError("opaque capture handle")

    def __setattr__(self, name, value):
        """Refuse attribute writes on the spend cell."""
        raise TypeError("opaque capture handle")

    def __copy__(self):
        """Refuse shallow copies of the capture facade."""
        """Refuse a shallow copy of the spend cell."""
        raise TypeError("opaque capture handle")

    def __deepcopy__(self, memo):
        """Refuse deep copies of the capture facade."""
        """Refuse a deep copy of the spend cell."""
        raise TypeError("opaque capture handle")

    def __getstate__(self):
        """Refuse serializing the capture facade."""
        """Refuse pickle state of the spend cell."""
        raise TypeError("opaque capture handle")

    def __reduce__(self):
        """Refuse pickle reduction of the capture facade."""
        """Refuse pickle reduction of the spend cell."""
        raise TypeError("opaque capture handle")

    def __reduce_ex__(self, protocol):
        """Refuse protocol pickle reduction of the facade."""
        """Refuse pickle protocol reduction of the spend cell."""
        raise TypeError("opaque capture handle")


def _plan_artifact_bound(value, name=None):
    """Return whether ``value`` is a bound plan artifact."""
    if type(value) is not dict:
        return False
    if name == "admission":
        return value.get("consumed") is True
    return bool(value)


class HistoricalStudyVerifier:
    """Refuse CAPTURED until ADR-0125 private plan and admission are bound.

    Parameters
    ----------
    authority : LifecycleAuthority
        The F4 WORM writer. Direct construction without one is refused.

    Examples
    --------
    Construction without a lifecycle authority is refused::

        try:
            HistoricalStudyVerifier()
        except TypeError:
            refused = True
        refused  # True
    """

    def __init__(self, authority, *, _spend=_make_spend_pair()):
        if not isinstance(authority, LifecycleAuthority):
            raise TypeError("lifecycle authority is required")
        self._authority = authority
        self._bound = {}
        live, _spent, pins = _spend
        cell = object.__new__(_SpendCell)
        cid = id(cell)
        live.add(cid)
        pins[cid] = cell
        self._admission_spent = cell
        self._capture_lock = Lock()
        self.deployment_eligible = False

    def __copy__(self):
        """Refuse shallow copies of the capture facade."""
        """Refuse a shallow copy of the one-use doorway."""
        raise TypeError("opaque capture handle")

    def __deepcopy__(self, memo):
        """Refuse deep copies of the capture facade."""
        """Refuse a deep copy of the one-use doorway."""
        raise TypeError("opaque capture handle")

    def __getstate__(self):
        """Refuse serializing the capture facade."""
        """Refuse pickle state of the one-use doorway."""
        raise TypeError("opaque capture handle")

    def __reduce__(self):
        """Refuse pickle reduction of the capture facade."""
        """Refuse pickle reduction of the one-use doorway."""
        raise TypeError("opaque capture handle")

    def __reduce_ex__(self, protocol):
        """Refuse protocol pickle reduction of the facade."""
        """Refuse pickle protocol reduction of the one-use doorway."""
        raise TypeError("opaque capture handle")

    def bind(self, **artifacts):
        """Bind named ADR-0125 plan artifacts. Unknown names refuse.

        Parameters
        ----------
        artifacts : dict
            Any subset of ``scope_intent``, ``ces``, ``pea``, ``bvp``,
            ``cas``, ``admission``. Each value must be a ``dict``;
            ``admission`` must have ``consumed`` equal to ``True``.

        Raises
        ------
        ValueError
            On an unknown name or an unbound artifact. A refused call
            stores none of that call's names.
        """
        unknown = tuple(name for name in artifacts if name not in _REQUIRED_PLAN)
        if unknown:
            raise ValueError("unknown plan artifact")
        pending = {}
        for name, value in artifacts.items():
            if not _plan_artifact_bound(value, name):
                raise ValueError("plan artifact is required")
            pending[name] = value
        self._bound.update(pending)

    def capture(self, published, frozen, port, **kwargs):
        """Refuse CAPTURED when any required plan artifact is unbound.

        Parameters
        ----------
        published : object
            A PUBLISHED handle from ``authority``.
        frozen : object
            The frozen consumer document.
        port : mapping
            The derived consumer captured port.
        kwargs : dict
            Forwarded to ``authority.capture`` only after every required
            plan artifact is bound.

        Returns
        -------
        tuple
            The ``authority.capture`` result.

        Raises
        ------
        ValueError
            When ScopeIntent, CES, PEA, BVP, CAS, or consumed admission
            is not bound, or when that admission is already spent.
        """
        live, spent = type(self).__init__.__kwdefaults__["_spend"][:2]
        with self._capture_lock:
            with _DOOR_LOCK:
                cell = getattr(self, "_admission_spent", None)
                cid = id(cell)
                if cell is None or cid in spent or cid not in live:
                    raise ValueError(
                        "CAPTURED refuses after consumed admission is spent"
                    )
                missing = [
                    name
                    for name in _REQUIRED_PLAN
                    if not _plan_artifact_bound(self._bound.get(name), name)
                ]
                if missing:
                    raise ValueError(
                        "CAPTURED refuses before ScopeIntent, CES, PEA, BVP, "
                        "CAS, and consumed admission are bound"
                    )
                live.discard(cid)
                spent.add(cid)
        return self._authority.capture(published, frozen, port, **kwargs)
class HistoricalStudyCaptureDriver:
    """Identity-bound facade for the private historical-study capture doorway."""

    __slots__ = ("_verifier", "_bound_verifier")

    def __init__(self, verifier):
        if type(verifier) is not HistoricalStudyVerifier:
            raise TypeError("exact HistoricalStudyVerifier is required")
        self._verifier = verifier
        self._bound_verifier = verifier

    def __copy__(self):
        """Refuse shallow copies of the capture facade."""
        raise TypeError("opaque capture handle")

    def __deepcopy__(self, memo):
        """Refuse deep copies of the capture facade."""
        raise TypeError("opaque capture handle")

    def __getstate__(self):
        """Refuse serializing the capture facade."""
        raise TypeError("opaque capture handle")

    def __reduce__(self):
        """Refuse pickle reduction of the capture facade."""
        raise TypeError("opaque capture handle")

    def __reduce_ex__(self, protocol):
        """Refuse protocol pickle reduction of the facade."""
        raise TypeError("opaque capture handle")

    def capture(self, published, frozen, port, **kwargs):
        """Delegate only to the constructor-bound verifier's class method."""
        verifier = getattr(self, "_verifier", None)
        bound = getattr(self, "_bound_verifier", None)
        if type(verifier) is not HistoricalStudyVerifier or verifier is not bound:
            raise ValueError("opaque bound verifier is required")
        return HistoricalStudyVerifier.capture(verifier, published, frozen, port, **kwargs)
