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
import uuid
from threading import Lock
from types import MappingProxyType
from weakref import WeakKeyDictionary

from dskit.pipeline.trust import (
    CapturedAuthorizationAuthority,
    HistoricalStudyEnvelopePreflight,
    HistoricalStudyRevocations,
    LifecycleAuthority,
    NonAuthorizingAdr0125StructuralSignaturePreflight,
    NonAuthorizingSyntheticGrantVerifier,
    NonAuthorizingSyntheticFixtureVerifier,
    NonAuthorizingRosterBootstrapVerifier,
    NonAuthorizingRosterRootProof,
    NonAuthorizingRawRootProof,
    NonAuthorizingSyntheticRootPisProof,
    NonAuthorizingDynamicRootGraph,
    prepare_v2_projection_input,
)
from dskit.production import bundles as _bundles
from dskit.production.base import GENESIS_HASH, ProductionError, canonical_hash, pin_members
from dskit.production.coordination import scope_equal
from dskit.production.decider import DEFAULT_MAX_ARTIFACT_AGE
from dskit.production.executor import empty_ack
from dskit.production.guards import max_verdict
from dskit.production.ledger import LEDGER_KINDS, ServeRoot
from dskit.production.records import ActPermit, EntryBatch, Intent, PolicyRequest, SafetyEpoch
from dskit.production.redact import get_logger
from dskit.production.release import parse_iso_duration, verify_release
from dskit.production.state import (
    SeriesState,
    TickState,
    check_admission_use_body,
    replay_into_fold,
)
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
    "NonAuthorizingSyntheticGrantVerifier",
    "NonAuthorizingSyntheticFixtureVerifier",
    "NonAuthorizingRosterBootstrapVerifier",
    "NonAuthorizingRosterRootProof",
    "NonAuthorizingRawRootProof",
    "NonAuthorizingSyntheticRootPisProof",
    "NonAuthorizingDynamicRootGraph",
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


def _build_verified_v2_projector():
    """Close the ADR-0172 projector over its effective executable surface."""
    import gc
    import hashlib
    import json
    import math
    from types import FunctionType, MappingProxyType as ExactMappingProxyType, ModuleType

    from dskit.pipeline import trust as trust_module
    from dskit.production import base as base_module
    from dskit.production import bundles as bundles_module

    consume = trust_module.consume_v2_projection_input
    projector = bundles_module._project_v2_event_envelopes
    canonical_encoder = bundles_module._canonical_bytes
    parser = bundles_module._parse_event_envelope
    validator = bundles_module._check_event_envelope
    order_key = bundles_module._event_envelope_order_key
    digest_checker = bundles_module.check_digest
    production_error = bundles_module.ProductionError
    base_plain = base_module._plain
    base_json = base_module.json
    base_math = base_module.math
    base_decimal = base_module.Decimal
    base_digest_pattern = base_module._HEX_DIGEST
    json_dumps = json.dumps
    json_loads = json.loads
    sha256 = hashlib.sha256
    get_referents = gc.get_referents
    isfinite = math.isfinite
    envelope_schema = bundles_module.CAPTURED_REPLAY_TAPE_ENVELOPE_SCHEMA
    envelope_fields = frozenset(bundles_module._EVENT_ENVELOPE_FIELDS)
    authorization_schemas = tuple(sorted(
        bundles_module.DATASET_AUTHORIZATION_EVENT_SCHEMAS.items()
    ))
    raw_fields = tuple(sorted(
        (key, tuple(value))
        for key, value in bundles_module.RAW_EVENT_FIELDS.items()
    ))

    def capture_executable_graph(*roots):
        """Snapshot the transitive Python dispatch graph used by ``roots``.

        Function identity alone is not a pin: Python permits in-place changes
        to ``__code__``, defaults and closure cells.  The projector also
        reaches helpers and mutable schema tables through module globals.
        Capture all effective resolutions now, then compare without invoking
        equality on arbitrary runtime values.
        """
        function_records = []
        resolution_records = []
        attribute_records = []
        value_records = []
        cell_records = []
        seen_functions = set()
        seen_values = set()
        unsupported = object()

        def frozen(value):
            value_type = type(value)
            if value is None or value_type in (bool, int, float, str, bytes):
                return (value_type, value)
            if value_type in (tuple, list):
                items = tuple(frozen(item) for item in value)
                if unsupported in items:
                    return unsupported
                return (value_type, items)
            if value_type is frozenset:
                items = tuple(sorted((frozen(item) for item in value), key=repr))
                if unsupported in items:
                    return unsupported
                return (value_type, items)
            if value_type in (dict, ExactMappingProxyType):
                items = []
                for key, item in value.items():
                    key_value = frozen(key)
                    item_value = frozen(item)
                    if key_value is unsupported or item_value is unsupported:
                        return unsupported
                    items.append((key_value, item_value))
                return (value_type, tuple(sorted(items, key=repr)))
            return unsupported

        def capture_value(value):
            value_id = id(value)
            if value_id in seen_values:
                return
            if type(value) not in (ExactMappingProxyType, tuple, frozenset):
                return
            snapshot = frozen(value)
            if snapshot is not unsupported:
                seen_values.add(value_id)
                value_records.append((value, snapshot))

        def resolve_attribute(owner, name):
            if type(owner) is type:
                for cls in owner.__mro__:
                    if name in cls.__dict__:
                        return cls.__dict__[name]
                raise AttributeError(name)
            return getattr(owner, name)

        def capture_attributes(owner, names):
            for name in names:
                try:
                    value = resolve_attribute(owner, name)
                except AttributeError:
                    continue
                attribute_records.append((owner, name, value))
                capture_value(value)
                if type(value) is FunctionType:
                    visit(value)

        def capture_resolutions(code, namespace, builtins):
            names = frozenset(code.co_names)
            for name in names:
                if name in namespace:
                    value = namespace[name]
                    resolution_records.append((namespace, name, value))
                    capture_value(value)
                    if type(value) is FunctionType:
                        visit(value)
                    elif type(value) is ModuleType or type(value) is type:
                        capture_attributes(value, names)
                elif name in builtins:
                    resolution_records.append((builtins, name, builtins[name]))
            for value in code.co_consts:
                if type(value) is type(code):
                    capture_resolutions(value, namespace, builtins)

        def visit(function):
            if type(function) is not FunctionType or id(function) in seen_functions:
                return
            seen_functions.add(id(function))
            function_records.append((
                function,
                function.__code__,
                frozen(function.__defaults__),
                frozen(function.__kwdefaults__),
            ))
            namespace = function.__globals__
            names = frozenset(function.__code__.co_names)
            capture_resolutions(
                function.__code__, namespace, function.__builtins__
            )
            closure = function.__closure__ or ()
            for cell in closure:
                try:
                    value = cell.cell_contents
                except ValueError:
                    value = unsupported
                cell_records.append((cell, value))
                if value is unsupported:
                    continue
                if type(value) is FunctionType:
                    visit(value)
                elif type(value) is ModuleType or type(value) is type:
                    capture_attributes(value, names)

        for root in roots:
            visit(root)
        function_records = tuple(function_records)
        resolution_records = tuple(resolution_records)
        attribute_records = tuple(attribute_records)
        value_records = tuple(value_records)
        cell_records = tuple(cell_records)

        def intact():
            for function, code, defaults, kwdefaults in function_records:
                if (
                    function.__code__ is not code
                    or frozen(function.__defaults__) != defaults
                    or frozen(function.__kwdefaults__) != kwdefaults
                ):
                    return False
            for namespace, name, value in resolution_records:
                if namespace.get(name, unsupported) is not value:
                    return False
            for owner, name, value in attribute_records:
                try:
                    current = resolve_attribute(owner, name)
                except AttributeError:
                    return False
                if current is not value:
                    return False
            for value, snapshot in value_records:
                if frozen(value) != snapshot:
                    return False
            for cell, value in cell_records:
                try:
                    current = cell.cell_contents
                except ValueError:
                    current = unsupported
                if current is not value:
                    return False
            return True

        return intact

    executable_graph_intact = capture_executable_graph(consume, projector)

    def dispatch_ok():
        """Return whether every effective Python-level dependency is intact."""
        return (
            executable_graph_intact()
            and
            trust_module.consume_v2_projection_input is consume
            and bundles_module._project_v2_event_envelopes is projector
            and bundles_module._canonical_bytes is canonical_encoder
            and bundles_module._parse_event_envelope is parser
            and bundles_module._check_event_envelope is validator
            and bundles_module._event_envelope_order_key is order_key
            and bundles_module.check_digest is digest_checker
            and bundles_module.ProductionError is production_error
            and base_module.canonical_bytes is canonical_encoder
            and base_module._plain is base_plain
            and base_module.json is base_json is json
            and base_module.math is base_math is math
            and base_module.Decimal is base_decimal
            and base_module._HEX_DIGEST is base_digest_pattern
            and bundles_module.json is json
            and bundles_module.hashlib is hashlib
            and bundles_module.gc is gc
            and json.dumps is json_dumps
            and json.loads is json_loads
            and hashlib.sha256 is sha256
            and gc.get_referents is get_referents
            and math.isfinite is isfinite
            and bundles_module.CAPTURED_REPLAY_TAPE_ENVELOPE_SCHEMA
            == envelope_schema
            and frozenset(bundles_module._EVENT_ENVELOPE_FIELDS)
            == envelope_fields
            and tuple(sorted(
                bundles_module.DATASET_AUTHORIZATION_EVENT_SCHEMAS.items()
            )) == authorization_schemas
            and tuple(sorted(
                (key, tuple(value))
                for key, value in bundles_module.RAW_EVENT_FIELDS.items()
            )) == raw_fields
        )

    def project(value, /):
        if not dispatch_ok():
            raise ValueError("v2 projector executable dependency changed")
        payload = consume(value)
        if not dispatch_ok():
            raise ValueError("v2 projector executable dependency changed")
        result = projector(*payload)
        if not dispatch_ok():
            raise ValueError("v2 projector executable dependency changed")
        return result

    return project


_project_verified_synthetic_v2_input = _build_verified_v2_projector()
del _build_verified_v2_projector


def _compose_v2_replay_tape(raw_proof, raw_proof_bytes, capability, /):
    """Compose one verified ``CapturedReplayTape`` from an ADR-0172 v2 input (ADR-0174).

    Nonauthorizing, like ADR-0172 itself: this proves the envelope bytes and
    the capture-root/receipt fields are facts about one single verified v2
    raw root, and nothing else. It grants no P4 authority, no WORM-lifecycle
    authority, and no replay or trading authority.

    Parameters
    ----------
    raw_proof : NonAuthorizingRawRootProof
        The exact proof that minted ``capability`` (ADR-0172 shape).
    raw_proof_bytes : tuple
        The exact twelve-byte tuple ``prepare_v2_projection_input``
        already validates; its last three elements are the ``publish_v2``
        writer's own ``(manifest_bytes, basis_bytes, receipt_bytes)``.
    capability : VerifiedV2ProjectionInput
        A still-fresh capability the caller holds. Spent by this call.

    Returns
    -------
    CapturedReplayTape
        The verified, reparsed, causally-ordered tape.

    Raises
    ------
    ValueError
        ``raw_proof``/``raw_proof_bytes`` fail ADR-0172's own prepare
        checks; ``capability`` is missing, spent, or forged; or
        ``capability`` does not belong to the exact root
        ``raw_proof``/``raw_proof_bytes`` name (the envelope bytes the two
        capabilities project disagree) -- both capabilities are already
        spent by the time this is raised and neither call is retryable.
    ProductionError
        The composed tape fails ``CapturedReplayTape.parse`` or
        ``verify_causal_order`` -- unreachable for a genuine, self-consistent
        root, since both are ADR-0172's own already-verified output.
    """
    second_capability = prepare_v2_projection_input(
        raw_proof, raw_proof_bytes
    )
    first_envelope_bytes = _project_verified_synthetic_v2_input(capability)
    second_envelope_bytes = _project_verified_synthetic_v2_input(second_capability)
    if first_envelope_bytes != second_envelope_bytes:
        raise ValueError(
            "v2 projection input does not belong to the supplied raw root"
        )
    ordered_envelope_bytes = first_envelope_bytes
    parsed_envelopes = [
        _bundles._parse_event_envelope(raw) for raw in ordered_envelope_bytes
    ]
    source_rank_policy_sha256 = parsed_envelopes[0]["source_rank_policy_sha256"]
    if any(
        envelope["source_rank_policy_sha256"] != source_rank_policy_sha256
        for envelope in parsed_envelopes
    ):
        raise ValueError(
            "v2 envelope source_rank_policy_sha256 disagree across the tape"
        )
    manifest_bytes, _basis_bytes, receipt_bytes = raw_proof_bytes[9:12]
    data_capture_root = hashlib.sha256(manifest_bytes).hexdigest()
    data_captured_receipt = hashlib.sha256(receipt_bytes).hexdigest()
    ordered_envelope_digests = [
        hashlib.sha256(envelope).hexdigest() for envelope in ordered_envelope_bytes
    ]
    tape = _bundles.CapturedReplayTape._build(
        data_capture_root, data_captured_receipt,
        source_rank_policy_sha256, ordered_envelope_digests,
    )
    reparsed = _bundles.CapturedReplayTape.parse(tape.canonical_bytes())
    _bundles.verify_causal_order(reparsed, ordered_envelope_bytes)
    return reparsed


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

#: ADR-0147 Decision point 2: narrowed from six to five — the retired
#: ``"admission"`` plan artifact is replaced by a verified ``admission_ref``
#: the durable ledger gate checks instead; ``bind()`` now refuses the name
#: ``"admission"`` as unknown, mechanically, by its absence here.
_REQUIRED_PLAN = ("scope_intent", "ces", "pea", "bvp", "cas")
_P4_VERIFIER_PINS = WeakKeyDictionary()
_P4_DRIVER_PINS = WeakKeyDictionary()

#: ADR-0147 Decision point 9: fixed across every durable verifier in a given
#: deployment/test configuration, so every durable verifier sharing one
#: ``root`` opens the identical on-disk chain and admission_use history.
_ADMISSION_SPEND_SERIES_ID = "admission-spend-v1"

#: Stamped on every envelope this series writes; carries no meaning beyond
#: "the admission-spend ledger", so a fixed placeholder (matching this
#: package's own zero-hash convention) is honest rather than invented.
_ADMISSION_SPEND_RELEASE_HASH = GENESIS_HASH

#: The §4.3 store kind the durable admission gate is specified against --
#: fsync'd, hash-chained, append-only, idempotent-by-id, exclusively locked
#: (ADR-0147 Decision point 6). Resolved through ``LEDGER_KINDS`` so this
#: module names no family member (§5.15), and PINNED rather than following
#: ``ledger.DEFAULT_LEDGER_KIND``, so moving the default store can never
#: silently change this gate's durability. ``durable()`` refuses at runtime
#: if what it resolves cannot ``reserve_once``, which pins the agreement.
_ADMISSION_SPEND_LEDGER_KIND = "jsonl"


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

    def __init__(self, authority):
        if not isinstance(authority, LifecycleAuthority):
            raise TypeError("lifecycle authority is required")
        self._authority = authority
        self._bound_authority = authority
        self._bound = {}
        self._capture_lock = Lock()
        self.deployment_eligible = False
        if type(self) is HistoricalStudyVerifier:
            _P4_VERIFIER_PINS[self] = authority

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
            ``cas``. Each value must be a truthy ``dict``. ADR-0147
            Decision point 2 retired the sixth ``"admission"`` artifact;
            a verified ``admission_ref`` passed to :meth:`capture` replaces
            it, so ``"admission"`` now refuses here as an unknown name.

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

    def capture(self, published, frozen, port, *, admission_ref=None, transition_nonces=(), **kwargs):
        """Refuse CAPTURED unless the ledger durably reserves this admission once.

        ADR-0147: replaces the retired in-process ``_spend`` kwdefault
        doorway with a durable, ChainLedger-backed consume-once gate. Only
        a verifier built by :meth:`durable` holds a ledger at all; a plain
        ``HistoricalStudyVerifier(authority)`` instance permanently refuses
        here, mechanically, because it has none — no combination of
        :meth:`bind` calls changes that (Decision point 2).

        Order, under the existing ``self._capture_lock`` (Decision point 8):
        the unchanged five-artifact :meth:`bind` check; ``authority``'s
        ``inspect_capture_admission`` (first call, validating
        ``admission_ref`` and deriving the idempotency key/body from ITS
        returned bytes, never from the raw argument); ``self._ledger
        .reserve_once`` under the ledger's own transition lock; only when
        that genuinely created the reservation THIS call, a second
        ``inspect_capture_admission`` recheck and then ``authority.capture``
        exactly once. Once ``reserve_once`` returns ``(True, seq)``, no
        code path here ever unspends, retries or re-reserves that id, in
        this call or any later one — including when ``authority.capture``
        itself raises.

        Parameters
        ----------
        published : object
            A PUBLISHED handle from ``authority``.
        frozen : object
            The frozen consumer document.
        port : mapping
            The derived consumer captured port; ``port["consumer_document_
            sha256"]`` feeds the reserved record's ``binding_sha256``.
        admission_ref : dict or None
            The verified action/replay ``IssuanceBasisRef.v1`` this capture
            spends. Required on a durable verifier; unused (and may be
            omitted) on an authority-only verifier, which refuses before
            ever reading it.
        transition_nonces : tuple
            One distinct unused nonce per capture, checked by
            ``inspect_capture_admission`` only — metadata freshness on that
            call, never a component of the durable spend-right itself.
        kwargs : dict
            Forwarded to ``authority.capture`` and to
            ``inspect_capture_admission``'s ``consumer_run_identity``/
            ``process_measurement_sha256``/``runtime_sha256`` triple.

        Returns
        -------
        tuple
            The ``authority.capture`` result.

        Raises
        ------
        ValueError
            When ScopeIntent, CES, PEA, BVP, or CAS is not bound (checked
            first, and named as such); when the verifier holds no durable
            ledger, which only ``durable()`` supplies (named separately, so
            the two causes are never confused for one another); or when
            that admission is already spent.
        """
        ledger = getattr(self, "_ledger", None)
        with self._capture_lock:
            missing = [
                name
                for name in _REQUIRED_PLAN
                if not _plan_artifact_bound(self._bound.get(name), name)
            ]
            # Two distinct causes, two distinct messages, plan gate FIRST.
            # Collapsing them let an unbound-ledger verifier answer with the
            # plan's wording, which made every test matching that wording --
            # F5a's own `test_private_plan_precedes_capture` sentinel among
            # them -- pass without the plan gate ever firing.
            if missing:
                raise ValueError(
                    "CAPTURED refuses before ScopeIntent, CES, PEA, BVP, and CAS are bound"
                )
            if ledger is None:
                raise ValueError(
                    "CAPTURED refuses on a verifier with no durable ledger -- "
                    "HistoricalStudyVerifier.durable(...) is the only "
                    "capture-capable shape"
                )
            captures = ((published, frozen, port),)
            runtime_kwargs = {
                "consumer_run_identity": kwargs["consumer_run_identity"],
                "process_measurement_sha256": kwargs["process_measurement_sha256"],
                "runtime_sha256": kwargs["runtime_sha256"],
            }
            admission_bytes = self._authority.inspect_capture_admission(
                captures, admission_ref, transition_nonces=transition_nonces, **runtime_kwargs
            )
            # Decision point 4: the key is derived from the bytes
            # inspect_capture_admission itself returned, never from the raw
            # caller argument directly.
            validated_ref = json.loads(admission_bytes)
            key = "admission_use:v1:" + canonical_hash({
                "kind": validated_ref["kind"],
                "schema": validated_ref["schema"],
                "sha256": validated_ref["sha256"],
            })
            binding_sha256 = canonical_hash({
                "consumer_document_sha256": port["consumer_document_sha256"],
                "bound_plan": {name: self._bound[name] for name in _REQUIRED_PLAN},
            })
            body = {
                "schema": "dskit.admission-use/v1",
                "admission_ref": validated_ref,
                "binding_sha256": binding_sha256,
            }
            check_admission_use_body(body)
            created, _seq = ledger.reserve_once({"kind": "admission_use", "id": key, "body": body})
            if not created:
                raise ValueError("CAPTURED refuses after consumed admission is spent")
            # Post-reservation recheck (Decision point 3e): a refusal here
            # leaves the reservation durably spent -- no unspend -- but the
            # delegate call below does not happen.
            self._authority.inspect_capture_admission(
                captures, admission_ref, transition_nonces=transition_nonces, **runtime_kwargs
            )
            return self._authority.capture(published, frozen, port, **kwargs)

    @classmethod
    def durable(cls, authority, root, *, clock):
        """Construct the only capture-capable (durable) verifier shape (ADR-0147 D9).

        Constructs its OWN :class:`~dskit.production.ledger.ServeRoot` and
        :class:`~dskit.production.ledger.JsonlLedger` internally; never
        accepts a pre-built ``Ledger``/``ChainLedger`` instance, so there is
        no public factory surface a caller could satisfy with a freshly
        constructed, empty ledger pointed at a throwaway directory and have
        it accepted as this admission's history. ``root`` is trusted
        production composition input (a test harness, or a future
        ``compose.py`` site) — never derived from ``admission_ref``,
        request data, or any other caller/request-controlled value.

        Parameters
        ----------
        authority : CapturedAuthorizationAuthority
            The broker-issued P4 capability :meth:`capture` will verify
            each admission against and, on success, delegate to.
        root : str
            The owner-configured directory the admission-spend series
            lives under. Every durable verifier sharing one ``root`` opens
            the identical on-disk chain and ``admission_use`` history.
        clock : Clock
            Injected; stamps every reserved record.

        Returns
        -------
        HistoricalStudyVerifier
            A verifier whose :meth:`capture` is ledger-backed and
            consume-once, reopening (Decision point 10's full replay) any
            ``admission_use`` history already durable under ``root``.

        Raises
        ------
        TypeError
            ``authority`` is not a ``CapturedAuthorizationAuthority``.
        ProductionError
            Another writer already holds this ``root``'s ``serve.lock``, or
            the on-disk chain fails to reopen cleanly.
        """
        if cls is not HistoricalStudyVerifier:
            raise TypeError("durable() constructs the base HistoricalStudyVerifier only")
        if not isinstance(authority, CapturedAuthorizationAuthority):
            raise TypeError("a broker-issued P4 authority capability is required")
        store = LEDGER_KINDS.resolve(_ADMISSION_SPEND_LEDGER_KIND)
        if not callable(getattr(store, "reserve_once", None)):
            raise ProductionError(
                [
                    f"the {_ADMISSION_SPEND_LEDGER_KIND!r} store resolves to "
                    f"{store.__name__}, which cannot reserve_once -- the durable "
                    "admission gate has no consume-once primitive without it"
                ]
            )
        serve_root = ServeRoot(root, _ADMISSION_SPEND_SERIES_ID)
        state = SeriesState(_ADMISSION_SPEND_SERIES_ID)
        ledger = store(
            serve_root,
            f"durable-verifier-{uuid.uuid4()}",
            _ADMISSION_SPEND_RELEASE_HASH,
            clock=clock,
            state=state,
        )
        # ADR-0147 Decision point 10: never trust a possibly-partial
        # in-memory fold (the store's own recovery walk rebuilds only the
        # head/index/snapshot cadence, not the attached state) or the latest
        # snapshot record as a resume point -- replay the WHOLE verified
        # chain, one state.apply(envelope) per record in chain order. The
        # SeriesState above is FRESH, so its head is 0 and `replay_into_fold`
        # therefore starts at genesis; the fold's own module owns that scan.
        # Refuse construction outright if the replayed fold's own derived
        # head ever disagrees with the ledger's independently verified head.
        try:
            replay_into_fold(ledger, state)
            if state.head() != ledger.head():
                raise ProductionError(
                    [
                        f"admission-spend replay head {state.head()} disagrees with "
                        f"the ledger's own head {ledger.head()}"
                    ]
                )
        except BaseException:
            ledger.close()
            raise
        self = cls(authority)
        self._ledger = ledger
        return self

    def authorize_capture_set(
        self, captures, admission_ref, *, consumer_run_identity,
        process_measurement_sha256, runtime_sha256, transition_nonces,
    ):
        """Delegate only to the same constructor-bound issued P4 capability.

        Parameters
        ----------
        captures : tuple
            Exact ordered published/frozen/derived-port tuples.
        admission_ref : dict
            Exact action or replay admission reference.
        consumer_run_identity : str
            Consumer run distinct from the producer runs.
        process_measurement_sha256 : str
            Exact consumer process measurement digest.
        runtime_sha256 : str
            Exact consumer runtime digest.
        transition_nonces : tuple
            One distinct unused nonce per capture.

        Returns
        -------
        tuple
            The held authority's opaque committed record and P4 launch session.

        Raises
        ------
        TypeError
            The held authority is not a broker-issued P4 capability.
        ValueError
            The facade or authority was substituted, the request is invalid,
            or complete closure and atomic admission preconditions fail.
        """
        if type(self) is not HistoricalStudyVerifier:
            raise ValueError("opaque bound P4 authority and verifier are required")
        authority = getattr(self, "_authority", None)
        pin = _P4_VERIFIER_PINS.get(self)
        if (
            pin is None or pin is not authority
            or getattr(self, "_bound_authority", None) is not authority
            or type(self).authorize_capture_set is not _HS_P4_VERIFIER_DISPATCH
            or "authorize_capture_set" in self.__dict__
        ):
            raise ValueError("opaque bound P4 authority and verifier are required")
        if not isinstance(authority, CapturedAuthorizationAuthority):
            raise TypeError("broker-issued P4 authority capability is required")
        if CapturedAuthorizationAuthority.authorize_capture_set is not _HS_P4_AUTHORITY_DISPATCH:
            raise ValueError("bound P4 authority dispatch was replaced")
        return _HS_P4_AUTHORITY_DISPATCH(
            authority, captures, admission_ref,
            consumer_run_identity=consumer_run_identity,
            process_measurement_sha256=process_measurement_sha256,
            runtime_sha256=runtime_sha256,
            transition_nonces=transition_nonces,
        )


class HistoricalStudyCaptureDriver:
    """Identity-bound facade for the private historical-study capture doorway.

    Parameters
    ----------
    verifier : HistoricalStudyVerifier
        Exact verifier retained by identity. Ordinary v1 capture remains valid;
        only the separate P4 method requires an issued P4 authority.

    Examples
    --------
    A non-verifier cannot construct this facade::

        try:
            HistoricalStudyCaptureDriver(None)
        except TypeError:
            refused = True
        refused  # True
    """

    __slots__ = ("_verifier", "_bound_verifier", "__weakref__")

    def __init__(self, verifier):
        if type(verifier) is not HistoricalStudyVerifier:
            raise TypeError("exact HistoricalStudyVerifier is required")
        self._verifier = verifier
        self._bound_verifier = verifier
        if type(self) is HistoricalStudyCaptureDriver:
            _P4_DRIVER_PINS[self] = verifier

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

    def authorize_capture_set(
        self, captures, admission_ref, *, consumer_run_identity,
        process_measurement_sha256, runtime_sha256, transition_nonces,
    ):
        """Invoke the checked P4 class method on the exact bound verifier.

        Parameters
        ----------
        captures : tuple
            Exact ordered published/frozen/derived-port tuples.
        admission_ref : dict
            Exact action or replay admission reference.
        consumer_run_identity : str
            Distinct consumer run identity.
        process_measurement_sha256 : str
            Consumer process measurement digest.
        runtime_sha256 : str
            Consumer runtime digest.
        transition_nonces : tuple
            One unique unused nonce per capture.

        Returns
        -------
        tuple
            The same held authority's opaque committed record and P4 session.

        Raises
        ------
        TypeError
            The verifier holds only an ordinary v1 authority.
        ValueError
            The bound verifier or dispatch changed, or P4 validation refuses.
        """
        if type(self) is not HistoricalStudyCaptureDriver:
            raise ValueError("opaque bound verifier P4 dispatch is required")
        verifier = getattr(self, "_verifier", None)
        pin = _P4_DRIVER_PINS.get(self)
        if (
            type(verifier) is not HistoricalStudyVerifier
            or verifier is not getattr(self, "_bound_verifier", None)
            or pin is None or pin is not verifier
            or type(self).authorize_capture_set is not _HS_P4_DRIVER_DISPATCH
            or type(verifier).authorize_capture_set is not _HS_P4_VERIFIER_DISPATCH
        ):
            raise ValueError("opaque bound verifier P4 dispatch is required")
        return _HS_P4_VERIFIER_DISPATCH(
            verifier, captures, admission_ref,
            consumer_run_identity=consumer_run_identity,
            process_measurement_sha256=process_measurement_sha256,
            runtime_sha256=runtime_sha256,
            transition_nonces=transition_nonces,
        )


_HS_P4_AUTHORITY_DISPATCH = CapturedAuthorizationAuthority.authorize_capture_set
_HS_P4_VERIFIER_DISPATCH = HistoricalStudyVerifier.authorize_capture_set
_HS_P4_DRIVER_DISPATCH = HistoricalStudyCaptureDriver.authorize_capture_set
