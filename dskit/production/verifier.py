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
from types import MappingProxyType

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

__all__ = ["VERIFY_REASONS", "SubmissionVerifier"]

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
