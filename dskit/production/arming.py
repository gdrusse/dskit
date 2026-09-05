"""Authenticated proofs, the arming fold and scope application (plan §5.6, D11).

Arming is an authenticated maker-checker act, not a config key. The maker
signs a canonical :class:`ArmRequest` bound to the release hash, the exact
document rung, a bounded expiry, an allowlist that may only narrow the
release's universe and a limits overlay that must be at least as strict
as the document; the checker signs an :class:`ArmApproval` over that
request's digest. Both principal ids are DERIVED from the proofs by the
graded :class:`ApprovalVerifier` — there is no free-form identity — and
at the two live rungs the two must differ. The result is the frozen
:class:`ArmingState` the ledger folds (embedded in the ``authority``
issue body, ruling R5) and ``arming.json`` merely caches.

:class:`Arming` owns three things and no more (§5.13.1): the proofs, the
read of the fold (``current``) and scope application (``apply_scope``,
``effective_bounds``). It never mints a permit — ``leg.py``'s
``Authority`` does — and it never appends: every verb returns the §6 body
the control processor records.

Two rules from D2 shape the code. Rung-dependent BEHAVIOUR is a table
keyed by rung (:data:`_RUNG_GATES`: at ``shadow``/``paper`` no live
permit exists to gate, so the conjunction is satisfied and one principal
may arm), never a branch on the rung. Rung AGREEMENT — the request's rung
against the document's, the leg's rung against the document's — is one
equality helper shared with the release-hash conjuncts, because "must all
agree" is one rule.

``check_conjunction`` is D11's live conjunction and it is origin-aware: a
``model`` leg needs a current unexpired ordinary arm; a ``reduction`` leg
needs a current unexpired unconsumed right for ITS OWN digest and must
not need an ordinary arm, because D10/D12 revoke ordinary arming on
leaving ``active`` — demanding one there would refuse every live flatten
leg. :class:`ReductionRights` folds a stored ``ReductionPlan`` plus the
checker's approval into one single-use right per intent digest and
reserves a right through the ``authority_use`` body the sole writer
barriers before an authorization.
"""

import json
from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass, fields
from decimal import Decimal, InvalidOperation

from dskit.production.base import (
    ProductionError,
    Registry,
    _check_dict,
    _check_str,
    _check_unknown,
    canonical_bytes,
    canonical_hash,
    check_digest,
    pin_members,
    reject_unknown_params,
)
from dskit.production.document import SIZE_MEASURES
from dskit.production.ledger import HeadBoundCache, validate_cache_head
from dskit.production.records import (
    Proposal,
    ReductionAuthorization,
    ReductionIntent,
    ReductionPlan,
    ScopeVerdict,
)
from dskit.production.redact import get_logger, redact
from dskit.production.release import fingerprint_class
from dskit.production.vocab import (
    APPROVAL_PURPOSES,
    AUTHORITY_EVENTS,
    AUTHORITY_ROLES,
    LEG_ORIGINS,
    RUNGS,
)

__all__ = [
    "APPROVAL_KINDS",
    "ApprovalVerifier",
    "ArmApproval",
    "ArmRequest",
    "Arming",
    "ArmingState",
    "CONJUNCTION_REASONS",
    "ConjunctionResult",
    "DenyAll",
    "ReductionRights",
    "SCOPE_REASONS",
    "VerifiedPrincipal",
    "approval_verifier",
    "verifier_fingerprint",
]

_LOG = get_logger("arming")

#: Why ``check_conjunction`` was not satisfied — closed, so a leg's refusal
#: reason is a token the metrics and the decision record can carry.
CONJUNCTION_REASONS = (
    "rung_mismatch",
    "not_armed_flag",
    "arm_env_missing",
    "release_mismatch",
    "not_armed",
    "no_reduction_authority",
    "reduction_authority_expired",
    "reduction_right_unknown",
    "reduction_right_consumed",
)

#: Why ``apply_scope`` refused, besides a breached bound (which names the guard).
SCOPE_REASONS = ("not_armed", "instrument_not_allowlisted")

_SATISFIED = ""
_NOTES = ("notes",)
_ARM_REQUEST, _ARM_APPROVAL = "arm_request", "arm_approval"
_ISSUE, _DISARM, _REVOKE, _EXPIRE = "issue", "disarm", "revoke", "expire"
_ORDINARY, _REDUCTION = "ordinary", "reduction"
_MODEL, _REDUCTION_ORIGIN = "model", "reduction"
#: The overlay keys a bound may carry, and the "within the bound" test each
#: means — used to prove an overlay stricter, to pick the stricter of two
#: bounds, and to judge a proposal against the effective bound.
_WITHIN = {
    "max": lambda value, bound: value <= bound,
    "min": lambda value, bound: value >= bound,
}
#: Which ``Proposal`` field a per-proposal size measure reads. Only these
#: measures can be judged against a proposal alone; every other guard the
#: overlay tightens is enforced by the guard chain with the account.
_PROPOSAL_FIELDS = {"quantity": "qty", "notional": "notional"}
_ORDINARY_ARM_ID = "ordinary-arm-v1"


pin_members("arming.py's purposes", (_ARM_REQUEST, _ARM_APPROVAL), APPROVAL_PURPOSES)
pin_members(
    "arming.py's authority events", (_ISSUE, _DISARM, _REVOKE, _EXPIRE), AUTHORITY_EVENTS
)
pin_members("arming.py's authority roles", (_ORDINARY, _REDUCTION), AUTHORITY_ROLES)
pin_members("arming.py's origins", (_MODEL, _REDUCTION_ORIGIN), LEG_ORIGINS)
pin_members("arming.py's _PROPOSAL_FIELDS", _PROPOSAL_FIELDS, SIZE_MEASURES, exact=True)


@dataclass(frozen=True)
class _RungGate:
    """What one rung demands (D11): whether a live permit exists to gate at all."""

    live: bool


#: One row per ``vocab.RUNGS`` member, pinned at import: the conjunction and
#: the distinct-principal rule apply where a live permit exists to gate.
_RUNG_GATES = pin_members(
    "arming.py's _RUNG_GATES",
    {
        "shadow": _RungGate(live=False),
        "paper": _RungGate(live=False),
        "live_limited": _RungGate(live=True),
        "live": _RungGate(live=True),
    },
    RUNGS,
    exact=True,
)


# ---------------------------------------------------------------------------
# Small checks shared by the value objects
# ---------------------------------------------------------------------------


def _member(problems, name, value, members):
    """Append a problem unless ``value`` is one of ``members``."""
    if value not in members:
        problems.append(f"{name} must be one of {list(members)}, got {value!r}")


def _agree(problems, what, expected, actual):
    """Append a problem unless two declarations of ``what`` are the same value."""
    if actual != expected:
        problems.append(f"{what} {actual!r} does not agree with the bound {expected!r}")


def _check_instant(problems, name, value):
    """Append a problem unless ``value`` is an epoch-ms int (never a bool)."""
    if isinstance(value, bool) or not isinstance(value, int):
        problems.append(f"{name} must be an epoch-ms int, got {value!r}")


def _check_bytes(problems, name, value):
    """Append a problem unless ``value`` is non-empty bytes."""
    if not isinstance(value, bytes) or not value:
        problems.append(f"{name} must be non-empty bytes, got {value!r}")


def _check_allowlist(problems, name, value):
    """Append a problem unless ``value`` is a sequence of non-empty strings."""
    if isinstance(value, str) or not isinstance(value, (list, tuple)):
        problems.append(f"{name} must be a sequence of instrument keys, got {value!r}")
        return
    for index, key in enumerate(value):
        _check_str(problems, f"{name}[{index}]", key)


def _decimal(problems, where, value):
    """Return ``value`` as a finite Decimal; a bool, None or garbage refuses."""
    if isinstance(value, bool) or value is None:
        problems.append(f"{where}: a bound is a number, got {value!r}")
        return None
    try:
        amount = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        problems.append(f"{where}: {value!r} is not a number")
        return None
    if not amount.is_finite():
        problems.append(f"{where}: non-finite bound {amount} refused")
        return None
    return amount


def _json_ready(obj):
    """Return ``obj`` in JSON-ready form through the one canonical renderer."""
    return json.loads(canonical_bytes(obj).decode("ascii"))


def _exact_keys(cls, obj):
    """Refuse a non-dict or any key set but exactly the dataclass fields."""
    if not isinstance(obj, dict):
        raise ProductionError([f"{cls.__name__}.from_obj expects a dict, got {obj!r}"])
    names = tuple(field.name for field in fields(cls))
    problems = []
    _check_unknown(problems, obj, names, where=cls.__name__)
    missing = [name for name in names if name not in obj]
    if missing:
        problems.append(f"{cls.__name__}: missing key(s) {missing}")
    if problems:
        raise ProductionError(problems)


def _head_check(head_seq, head_hash, ledger):
    """Place a cached head in the chain through the ledger's one owner of that rule.

    Looked up by name at call time — never bound at import — so the
    owner stays ``ledger.validate_cache_head`` as this module sees it.
    """
    return validate_cache_head(head_seq, head_hash, ledger)


# ---------------------------------------------------------------------------
# Value objects (§5.6)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ArmingState:
    """The frozen folded value of one ordinary arm — §5.6's eleven members, in order.

    Issued by :meth:`Arming.approve`, embedded as ``arming`` in the
    ``authority`` issue body (ruling R5), folded by ``SeriesState`` into
    ``StateView.arming`` and rebuilt from that projection by
    :meth:`Arming.current`.

    Parameters
    ----------
    authority_id : str
        Derived from the request digest and the two record ids, never
        from wall time.
    release_hash : str
        The 64-hex release the arm binds.
    rung : str
        A member of ``vocab.RUNGS`` — the document's, never promoted.
    maker, checker : str
        Principal ids the verifier derived from the two proofs.
    armed_at_ms, armed_until_ms : int
        Epoch ms; the arm is expired AT and after ``armed_until_ms``.
    allowlist : tuple of str
        Instruments a permit may name; a subset of the release universe.
    limits_overlay : dict
        ``{guard: {"max": ..}}`` / ``{"min": ..}`` tightenings, JSON-shaped.
    request_proof_digest, approval_proof_digest : str
        Digests of the two proofs; the proofs themselves never reach a record.

    Examples
    --------
    ::

        state = ArmingState(
            authority_id="auth-1", release_hash="b" * 64, rung="live_limited",
            maker="maker-1", checker="checker-1", armed_at_ms=1_767_268_800_000,
            armed_until_ms=1_767_272_400_000, allowlist=("INS1",), limits_overlay={},
            request_proof_digest="1" * 64, approval_proof_digest="2" * 64,
        )
        state.expired(1_767_272_400_000)  # True
        ArmingState.from_obj(state.to_obj()) == state  # True
    """

    authority_id: str
    release_hash: str
    rung: str
    maker: str
    checker: str
    armed_at_ms: int
    armed_until_ms: int
    allowlist: tuple
    limits_overlay: dict
    request_proof_digest: str
    approval_proof_digest: str

    def __post_init__(self):
        """Validate every member and freeze the two containers."""
        problems = []
        for name in ("authority_id", "maker", "checker"):
            _check_str(problems, f"ArmingState.{name}", getattr(self, name))
        for name in ("release_hash", "request_proof_digest", "approval_proof_digest"):
            check_digest(problems, f"ArmingState.{name}", getattr(self, name))
        _member(problems, "ArmingState.rung", self.rung, RUNGS)
        _check_instant(problems, "ArmingState.armed_at_ms", self.armed_at_ms)
        _check_instant(problems, "ArmingState.armed_until_ms", self.armed_until_ms)
        if not problems and self.armed_until_ms <= self.armed_at_ms:
            problems.append("ArmingState.armed_until_ms must be after armed_at_ms")
        _check_allowlist(problems, "ArmingState.allowlist", self.allowlist)
        overlay = dict(self.limits_overlay) if isinstance(self.limits_overlay, Mapping) else None
        _check_dict(problems, "ArmingState.limits_overlay", overlay)
        if problems:
            raise ProductionError(problems)
        object.__setattr__(self, "allowlist", tuple(self.allowlist))
        object.__setattr__(self, "limits_overlay", _json_ready(overlay))

    def expired(self, at_ms):
        """Say whether the arm is expired at ``at_ms`` — inclusive at the deadline.

        Parameters
        ----------
        at_ms : int

        Returns
        -------
        bool
        """
        return at_ms >= self.armed_until_ms

    def to_obj(self):
        """Return the eleven members JSON-ready (the allowlist as a list).

        Returns
        -------
        dict
        """
        return _json_ready({field.name: getattr(self, field.name) for field in fields(self)})

    @classmethod
    def from_obj(cls, obj):
        """Rebuild the state from its ``to_obj()`` form, default-deny.

        Parameters
        ----------
        obj : dict
            Exactly the eleven members.

        Returns
        -------
        ArmingState

        Raises
        ------
        ProductionError
            On an unknown or missing key, or any malformed member.
        """
        _exact_keys(cls, obj)
        return cls(**obj)


@dataclass(frozen=True)
class ArmRequest:
    """What the maker signs — §5.6's six members; the proof signs the other five.

    Parameters
    ----------
    release_hash : str
    rung : str
    allowlist : tuple of str
    limits_overlay : dict
    requested_until_ms : int
    request_proof : bytes
        Opaque to this module; the verifier reads it. Never rendered.

    Examples
    --------
    ::

        request = ArmRequest(
            release_hash="b" * 64, rung="live_limited", allowlist=("INS1",),
            limits_overlay={"size": {"max": "50"}}, requested_until_ms=1_767_272_400_000,
            request_proof=b"<maker signature>",
        )
        "request_proof" in request.to_obj()  # False
        len(request.request_digest())  # 64
    """

    release_hash: str
    rung: str
    allowlist: tuple
    limits_overlay: dict
    requested_until_ms: int
    request_proof: bytes

    def __post_init__(self):
        """Check the shape only; the document-bound rules are :meth:`Arming.request`'s."""
        problems = []
        _check_str(problems, "ArmRequest.release_hash", self.release_hash)
        _check_str(problems, "ArmRequest.rung", self.rung)
        _check_allowlist(problems, "ArmRequest.allowlist", self.allowlist)
        overlay = dict(self.limits_overlay) if isinstance(self.limits_overlay, Mapping) else None
        _check_dict(problems, "ArmRequest.limits_overlay", overlay)
        _check_instant(problems, "ArmRequest.requested_until_ms", self.requested_until_ms)
        _check_bytes(problems, "ArmRequest.request_proof", self.request_proof)
        if problems:
            raise ProductionError(problems)
        object.__setattr__(self, "allowlist", tuple(self.allowlist))
        object.__setattr__(self, "limits_overlay", _json_ready(overlay))

    def to_obj(self):
        """Return the five signed members JSON-ready — never the proof bytes.

        Returns
        -------
        dict
        """
        return _json_ready(
            {
                "release_hash": self.release_hash,
                "rung": self.rung,
                "allowlist": self.allowlist,
                "limits_overlay": self.limits_overlay,
                "requested_until_ms": self.requested_until_ms,
            }
        )

    def canonical_bytes(self):
        """Return the bytes the maker signs: the canonical JSON of :meth:`to_obj`.

        Returns
        -------
        bytes
        """
        return canonical_bytes(self.to_obj())

    def request_digest(self):
        """Return the sha256 hex of :meth:`canonical_bytes` — what the checker approves.

        Returns
        -------
        str
        """
        return canonical_hash(self.to_obj())


@dataclass(frozen=True)
class ArmApproval:
    """What the checker signs — the request's digest, and the proof over it.

    Parameters
    ----------
    request_digest : str
    approval_proof : bytes

    Examples
    --------
    ::

        approval = ArmApproval(request_digest=request.request_digest(),
                               approval_proof=b"<checker signature>")
    """

    request_digest: str
    approval_proof: bytes

    def __post_init__(self):
        """Check the shape."""
        problems = []
        check_digest(problems, "ArmApproval.request_digest", self.request_digest)
        _check_bytes(problems, "ArmApproval.approval_proof", self.approval_proof)
        if problems:
            raise ProductionError(problems)

    def canonical_bytes(self):
        """Return the bytes the checker signs: the canonical JSON of ``{request_digest}``.

        Returns
        -------
        bytes
        """
        return canonical_bytes({"request_digest": self.request_digest})


@dataclass(frozen=True)
class VerifiedPrincipal:
    """Who a proof was from, as the verifier derived it, and the proof's digest.

    Parameters
    ----------
    id : str
    proof_digest : str
        64-hex.

    Examples
    --------
    ::

        principal = VerifiedPrincipal(id="maker-1", proof_digest="1" * 64)
    """

    id: str
    proof_digest: str

    def __post_init__(self):
        """Check the shape."""
        problems = []
        _check_str(problems, "VerifiedPrincipal.id", self.id)
        check_digest(problems, "VerifiedPrincipal.proof_digest", self.proof_digest)
        if problems:
            raise ProductionError(problems)


@dataclass(frozen=True)
class ConjunctionResult:
    """The answer of :meth:`Arming.check_conjunction`.

    Parameters
    ----------
    satisfied : bool
    reason : str
        ``""`` when satisfied, else a :data:`CONJUNCTION_REASONS` member.

    Examples
    --------
    ::

        ConjunctionResult(satisfied=False, reason="not_armed")
    """

    satisfied: bool
    reason: str


# ---------------------------------------------------------------------------
# The approval seam (D11)
# ---------------------------------------------------------------------------


class ApprovalVerifier(ABC):
    """The seam that turns a proof into a principal (§5.6, D11).

    A child (HMAC, signature, SSO assertion) subclasses it, declares
    ``LIVE_CAPABLE = True`` and its trust-root ``_PARAMS`` (env-var
    NAMES, never secret material), resolves those once at construction
    without network I/O, and implements :meth:`verify`. Purposes are
    closed and :meth:`check_purpose` is concrete here so no child
    respells the set.

    Parameters
    ----------
    params : dict or None
        The ``document.arming.approval.params`` block; default-deny over
        ``_PARAMS`` (plus ``notes``).

    Examples
    --------
    A verifier that trusts one static token — for a rehearsal only::

        class TokenVerifier(ApprovalVerifier):
            LIVE_CAPABLE = False
            _PARAMS = ("token",)

            def verify(self, canonical_bytes, proof, purpose):
                self.check_purpose(purpose)
                if proof != self._params["token"].encode():
                    raise ProductionError(["bad token"])
                return VerifiedPrincipal(id="operator", proof_digest=canonical_hash(proof.hex()))

        verifier = TokenVerifier({"token": "open-sesame"})
        verifier.verify(b"payload", b"open-sesame", "arm_request").id  # 'operator'
    """

    #: Only a child that says so may back a live document (§5.6).
    LIVE_CAPABLE = False
    _PARAMS = ()

    def __init__(self, params=None):
        params = dict(params or {})
        problems = self.validate_params(params)
        if problems:
            raise ProductionError(problems)
        self._params = params

    @classmethod
    def validate_params(cls, params):
        """Return every problem with ``params``; empty when acceptable.

        Parameters
        ----------
        params : dict

        Returns
        -------
        list of str
            Default-deny over ``_PARAMS`` and ``notes``; subclasses extend.
        """
        problems = []
        reject_unknown_params(problems, params, tuple(cls._PARAMS) + _NOTES)
        return problems

    @abstractmethod
    def verify(self, canonical_bytes, proof, purpose):
        """Return the principal behind ``proof`` over ``canonical_bytes`` for ``purpose``.

        Parameters
        ----------
        canonical_bytes : bytes
            What was signed.
        proof : bytes
        purpose : str
            A member of ``vocab.APPROVAL_PURPOSES``; call :meth:`check_purpose`.

        Returns
        -------
        VerifiedPrincipal

        Raises
        ------
        ProductionError
            When the proof does not verify for this purpose.
        """

    def check_purpose(self, purpose):
        """Refuse a purpose outside the closed set; return it otherwise.

        Parameters
        ----------
        purpose : str

        Returns
        -------
        str
            ``purpose``.

        Raises
        ------
        ProductionError
            If it is not a ``vocab.APPROVAL_PURPOSES`` member.
        """
        problems = []
        _member(problems, "purpose", purpose, APPROVAL_PURPOSES)
        if problems:
            raise ProductionError(problems)
        return purpose


class DenyAll(ApprovalVerifier):
    """The core shadow/paper default: refuses every proof, holds no trust root.

    Parameters
    ----------
    params : dict or None
        Must be empty (``notes`` aside).

    Examples
    --------
    ::

        verifier = DenyAll({})
        verifier.LIVE_CAPABLE  # False
    """

    _PARAMS = ()

    def verify(self, canonical_bytes, proof, purpose):
        """Refuse.

        Parameters
        ----------
        canonical_bytes : bytes
        proof : bytes
        purpose : str

        Raises
        ------
        ProductionError
            Always — after :meth:`check_purpose`.
        """
        self.check_purpose(purpose)
        raise ProductionError(
            [f"deny-all: no proof verifies for {purpose!r}; a live plan names a child verifier"]
        )


def verifier_fingerprint(cls, params):
    """Return the release-bound fingerprint of a verifier class and its params.

    Parameters
    ----------
    cls : type
        An ``ApprovalVerifier`` subclass.
    params : mapping
        Its ``params`` block (trust-root references, never secrets).

    Returns
    -------
    str
        ``canonical_hash({"class": fingerprint_class(cls), "params": params})``.

    Raises
    ------
    ProductionError
        If the class source cannot be read or the params are not JSON.
    """
    return canonical_hash({"class": fingerprint_class(cls), "params": dict(params)})


def approval_verifier(document):
    """Build the verifier ``document.arming.approval`` selects.

    Parameters
    ----------
    document : ServeDocument

    Returns
    -------
    ApprovalVerifier
        ``APPROVAL_KINDS.resolve(uses)(params)``.

    Raises
    ------
    ProductionError
        On an unknown kind, a non-verifier reference or refused params.
    """
    site = document.arming.approval
    params = dict(site.params) if site.params is not None else {}
    return APPROVAL_KINDS.resolve(site.uses)(params)


# ---------------------------------------------------------------------------
# The arming service (§5.6)
# ---------------------------------------------------------------------------


class Arming:
    """Proofs, the arming fold and scope application — never a permit (§5.6, §5.13.1).

    Parameters
    ----------
    document : ServeDocument
        Read for ``rung``, ``arming.max_duration_s`` and the guards' declared
        bounds (``guards.<name>.params.bound``).
    release : ReleaseManifest
        Read for ``release_hash`` and ``feed_spec["required_keys"]`` — the
        universe an allowlist may only narrow.
    serve_root : ServeRoot
        Supplies ``arming_cache``.
    verifier : ApprovalVerifier
        Turns proofs into principals.
    clock : Clock
        ``now_ms()`` stamps ``armed_at_ms`` and judges expiry.

    Examples
    --------
    One maker-checker cycle, then the conjunction a model leg asks::

        arming = Arming(document, release, serve_root=serve_root,
                        verifier=verifier, clock=clock)
        control_body = arming.request(request, "req-arm-1")
        authority_body, state = arming.approve(approval, request, "req-arm-1", "apr-arm-1")
        arming.check_conjunction(invocation, view, "model", None, "live_limited", now_ms)
        # -> ConjunctionResult(satisfied=True, reason='')
    """

    def __init__(self, document, release, *, serve_root, verifier, clock):
        problems = []
        if not isinstance(verifier, ApprovalVerifier):
            problems.append(f"verifier must be an ApprovalVerifier, got {verifier!r}")
        _member(problems, "document.rung", document.rung, RUNGS)
        if problems:
            raise ProductionError(problems)
        self._release = release
        self._verifier = verifier
        self._clock = clock
        self._rung = document.rung
        self._gate = _RUNG_GATES[self._rung]
        self._max_duration_ms = int(document.arming.max_duration_s) * 1000
        self._universe = tuple(release.feed_spec["required_keys"])
        self._guard_names = tuple(document.guards)
        self._bounds = _declared_bounds(document.guards)
        self._proposal_fields = _proposal_fields(document.guards)
        self._cache = HeadBoundCache(serve_root.arming_cache, "arming", _head_check)

    # -- the maker half ------------------------------------------------------------

    def request(self, arm_request, request_id):
        """Verify the maker's request and return its §6 ``control_request`` body.

        Parameters
        ----------
        arm_request : ArmRequest
        request_id : str
            The control request's own id.

        Returns
        -------
        dict
            ``{request_id, purpose, payload, principal_digest, proof_digest,
            expires_ms}`` — nothing is appended here.

        Raises
        ------
        ProductionError
            Every problem at once: a rung other than the document's, another
            release, an expiry not in the future or beyond
            ``arming.max_duration_s``, an allowlist outside the universe, an
            overlay that is not provably stricter, or a proof the verifier
            refuses.
        """
        problems = []
        _check_str(problems, "request_id", request_id)
        if not isinstance(arm_request, ArmRequest):
            problems.append(f"arm_request must be an ArmRequest, got {arm_request!r}")
        if problems:
            raise ProductionError(problems)
        self._check_request(problems, arm_request)
        maker = self._verify(problems, arm_request.canonical_bytes(), arm_request.request_proof,
                             _ARM_REQUEST)
        if problems:
            raise ProductionError(problems)
        return {
            "request_id": request_id,
            "purpose": _ARM_REQUEST,
            "payload": arm_request.to_obj(),
            "principal_digest": canonical_hash(maker.id),
            "proof_digest": maker.proof_digest,
            "expires_ms": arm_request.requested_until_ms,
        }

    # -- the checker half -----------------------------------------------------------

    def approve(self, arm_approval, request, request_id, approval_id):
        """Verify the checker's approval and issue the arm.

        Parameters
        ----------
        arm_approval : ArmApproval
        request : ArmRequest
            The request approved; re-validated and re-verified here, because
            this is the call that issues authority.
        request_id, approval_id : str
            The two control record ids the authority binds.

        Returns
        -------
        tuple
            ``(authority_body, ArmingState)`` — the §6 ``authority`` issue
            body (``arming`` embeds ``ArmingState.to_obj()``, ruling R5) and
            the state itself.

        Raises
        ------
        ProductionError
            If the approval is over another request's digest, the request
            no longer validates (expired, say), either proof fails, or the
            maker and checker coincide at a live rung.
        """
        problems = []
        _check_str(problems, "request_id", request_id)
        _check_str(problems, "approval_id", approval_id)
        if not isinstance(arm_approval, ArmApproval):
            problems.append(f"arm_approval must be an ArmApproval, got {arm_approval!r}")
        if not isinstance(request, ArmRequest):
            problems.append(f"request must be an ArmRequest, got {request!r}")
        if problems:
            raise ProductionError(problems)
        _agree(problems, "approval request_digest", request.request_digest(),
               arm_approval.request_digest)
        self._check_request(problems, request)
        maker = self._verify(problems, request.canonical_bytes(), request.request_proof,
                             _ARM_REQUEST)
        checker = self._verify(problems, arm_approval.canonical_bytes(),
                               arm_approval.approval_proof, _ARM_APPROVAL)
        if maker is not None and checker is not None and self._gate.live and maker.id == checker.id:
            problems.append(
                f"maker and checker must differ at rung {self._rung!r}; both are {maker.id!r}"
            )
        if problems:
            raise ProductionError(problems)
        state = ArmingState(
            authority_id=canonical_hash(
                [_ORDINARY_ARM_ID, request.request_digest(), request_id, approval_id]
            ),
            release_hash=request.release_hash,
            rung=request.rung,
            maker=maker.id,
            checker=checker.id,
            armed_at_ms=self._clock.now_ms(),
            armed_until_ms=request.requested_until_ms,
            allowlist=request.allowlist,
            limits_overlay=request.limits_overlay,
            request_proof_digest=maker.proof_digest,
            approval_proof_digest=checker.proof_digest,
        )
        body = {
            "authority_id": state.authority_id,
            "event": _ISSUE,
            "role": _ORDINARY,
            "request_id": request_id,
            "approval_id": approval_id,
            "reason": None,
            "arming": state.to_obj(),
        }
        _LOG.info("arm issued %s by %s / %s until %d", state.authority_id, redact(state.maker),
                  redact(state.checker), state.armed_until_ms)
        return body, state

    def _verify(self, problems, payload, proof, purpose):
        """Ask the verifier; fold a refusal into ``problems`` and return None."""
        try:
            principal = self._verifier.verify(payload, proof, purpose)
        except ProductionError as exc:
            problems.extend(f"{purpose}: {problem}" for problem in exc.problems)
            return None
        if not isinstance(principal, VerifiedPrincipal):
            problems.append(f"{purpose}: verifier answered {principal!r}, not a VerifiedPrincipal")
            return None
        return principal

    def _check_request(self, problems, request):
        """Accumulate every document-bound problem with a request (D11)."""
        _agree(problems, "rung", self._rung, request.rung)
        _agree(problems, "release_hash", self._release.release_hash, request.release_hash)
        self._check_expiry(problems, request.requested_until_ms)
        self._check_universe(problems, request.allowlist)
        self._check_overlay(problems, request.limits_overlay)

    def _check_expiry(self, problems, until_ms):
        """Expiry is mandatory, in the future and within ``arming.max_duration_s``."""
        now = self._clock.now_ms()
        if until_ms <= now:
            problems.append(f"requested_until_ms {until_ms} is not in the future (now {now})")
        elif until_ms - now > self._max_duration_ms:
            problems.append(
                f"requested_until_ms {until_ms} exceeds document.arming.max_duration_s "
                f"({self._max_duration_ms // 1000} s from now {now})"
            )

    def _check_universe(self, problems, allowlist):
        """Require at least one instrument, each one the release serves."""
        if not allowlist:
            problems.append("allowlist must name at least one instrument")
        outside = [key for key in allowlist if key not in self._universe]
        if outside:
            problems.append(
                f"allowlist names {outside} outside the release universe {list(self._universe)}"
            )

    def _check_overlay(self, problems, overlay):
        """Every overlay entry tightens a bound the document declares on a named guard."""
        for guard, bounds in overlay.items():
            declared = self._bounds.get(guard)
            if declared is None:
                if guard in self._guard_names:
                    problems.append(f"limits_overlay.{guard}: the guard declares no bound to tighten")
                else:
                    problems.append(f"limits_overlay.{guard}: the document declares no such guard")
                continue
            where = f"limits_overlay.{guard}"
            _check_dict(problems, where, bounds)
            if not isinstance(bounds, dict):
                continue
            _check_unknown(problems, bounds, tuple(_WITHIN), where=where)
            for key in bounds:
                if key in _WITHIN:
                    self._check_tightening(problems, where, key, bounds[key], declared)

    def _check_tightening(self, problems, where, key, value, declared):
        """Require the overlay bound to exist in the document and lie within it."""
        if key not in declared:
            problems.append(f"{where}.{key}: the document declares no {key} bound to tighten")
            return
        amount = _decimal(problems, f"{where}.{key}", value)
        if amount is not None and not _WITHIN[key](amount, declared[key]):
            problems.append(f"{where}.{key} {amount} is looser than the document's {declared[key]}")

    # -- the fold -----------------------------------------------------------

    def current(self, view, at_ms):
        """Return the current unexpired ordinary arm the fold holds, or None.

        Parameters
        ----------
        view : StateView
        at_ms : int
            Expiry is judged here, inclusive at ``armed_until_ms``.

        Returns
        -------
        ArmingState or None
        """
        state = _folded_arm(view)
        if state is None or state.expired(at_ms):
            return None
        return state

    def check_conjunction(self, invocation, view, origin, reduction, rung, at_ms):
        """Evaluate D11's live conjunction for one leg — origin-aware, rung-deciding.

        The document rung, ``--armed``, ``DSKIT_PRODUCTION_ARM`` and the
        release hash must agree for every origin; a ``model`` leg then
        needs a current ordinary arm, a ``reduction`` leg an unconsumed
        right for its own digest. Where no live permit exists to gate
        (``shadow``, ``paper``) the answer is satisfied.

        Parameters
        ----------
        invocation : Invocation
            ``armed`` (bool) and ``env_release_hash`` (str or None).
        view : StateView
        origin : str
            A member of ``vocab.LEG_ORIGINS``.
        reduction : object or None
            The leg's reduction binding (``signed``, ``digest``, ``right``);
            required when ``origin`` is ``reduction``.
        rung : str
            The leg's rung; must agree with the document's.
        at_ms : int

        Returns
        -------
        ConjunctionResult

        Raises
        ------
        ProductionError
            On an origin or rung outside its vocabulary, a malformed
            invocation, or a reduction leg whose binding is not the
            maker-signed ``ReductionIntent`` plus that intent's own digest
            — a digest the leg merely asserted would bind a right to
            nothing.
        """
        problems = []
        _member(problems, "origin", origin, LEG_ORIGINS)
        _member(problems, "rung", rung, RUNGS)
        _check_instant(problems, "at_ms", at_ms)
        armed = getattr(invocation, "armed", None)
        if not isinstance(armed, bool):
            problems.append(f"invocation.armed must be a bool, got {armed!r}")
        if problems:
            raise ProductionError(problems)
        _agree(problems, "rung", self._rung, rung)
        if problems:
            return ConjunctionResult(satisfied=False, reason="rung_mismatch")
        if not self._gate.live:
            return ConjunctionResult(satisfied=True, reason=_SATISFIED)
        common = self._common_conjuncts(invocation)
        if common:
            return ConjunctionResult(satisfied=False, reason=common)
        return self._ORIGIN_CONJUNCTS[origin](self, view, reduction, at_ms)

    def _common_conjuncts(self, invocation):
        """Check the flag and the environment variable name this release; return why not."""
        if not invocation.armed:
            return "not_armed_flag"
        env = invocation.env_release_hash
        if env is None:
            return "arm_env_missing"
        if env != self._release.release_hash:
            return "release_mismatch"
        return _SATISFIED

    def _model_conjunct(self, view, reduction, at_ms):
        """Require a current unexpired ordinary arm bound to this release and rung."""
        state = self.current(view, at_ms)
        if state is None:
            return ConjunctionResult(satisfied=False, reason="not_armed")
        problems = []
        _agree(problems, "arm release_hash", self._release.release_hash, state.release_hash)
        if problems:
            return ConjunctionResult(satisfied=False, reason="release_mismatch")
        _agree(problems, "arm rung", self._rung, state.rung)
        if problems:
            return ConjunctionResult(satisfied=False, reason="rung_mismatch")
        return ConjunctionResult(satisfied=True, reason=_SATISFIED)

    def _reduction_conjunct(self, view, reduction, at_ms):
        """Require an unconsumed right for the leg's own digest — never an ordinary arm."""
        digest = _bound_digest(reduction)
        grant = view.reduction
        if grant is None:
            return ConjunctionResult(satisfied=False, reason="no_reduction_authority")
        if at_ms >= grant.expires_ms:
            return ConjunctionResult(satisfied=False, reason="reduction_authority_expired")
        if digest not in grant.rights:
            return ConjunctionResult(satisfied=False, reason="reduction_right_unknown")
        if digest in grant.reserved:
            return ConjunctionResult(satisfied=False, reason="reduction_right_consumed")
        return ConjunctionResult(satisfied=True, reason=_SATISFIED)

    _ORIGIN_CONJUNCTS = {_MODEL: _model_conjunct, _REDUCTION_ORIGIN: _reduction_conjunct}

    # -- scope application (§5.5) ----------------------------------------------------

    def apply_scope(self, proposal, arming_state):
        """Judge the exact final proposal against the arm's allowlist and effective bounds.

        Parameters
        ----------
        proposal : Proposal
        arming_state : ArmingState or None

        Returns
        -------
        ScopeVerdict
            ``allowed`` with ``scope_key`` the instrument; ``reason`` is a
            :data:`SCOPE_REASONS` member or names the breached guard bound.

        Raises
        ------
        ProductionError
            If ``proposal`` is not a ``Proposal`` or ``arming_state`` is
            neither an ``ArmingState`` nor None.
        """
        problems = []
        if not isinstance(proposal, Proposal):
            problems.append(f"proposal must be a Proposal, got {proposal!r}")
        if arming_state is not None and not isinstance(arming_state, ArmingState):
            problems.append(f"arming_state must be an ArmingState or None, got {arming_state!r}")
        if problems:
            raise ProductionError(problems)
        key = proposal.instrument
        if arming_state is None:
            return ScopeVerdict(allowed=False, scope_key=key, reason="not_armed")
        if key not in arming_state.allowlist:
            return ScopeVerdict(allowed=False, scope_key=key, reason="instrument_not_allowlisted")
        breach = self._first_breach(proposal, self.effective_bounds(arming_state))
        return ScopeVerdict(allowed=not breach, scope_key=key, reason=breach)

    def _first_breach(self, proposal, bounds):
        """Name the first per-proposal bound the proposal breaks, or ``""``."""
        for guard, field in self._proposal_fields.items():
            value = getattr(proposal, field)
            if value is None:
                continue
            for key, bound in bounds.get(guard, {}).items():
                if not _WITHIN[key](value, bound):
                    return f"{guard}.{key} {bound} breached by {field} {value}"
        return _SATISFIED

    def effective_bounds(self, arming_state):
        """Return the document's declared bounds tightened by the arm's overlay.

        Parameters
        ----------
        arming_state : ArmingState or None
            None yields the document's own bounds.

        Returns
        -------
        dict
            ``{guard: {"max": Decimal, "min": Decimal}}`` over the guards
            that declare a bound; only the keys declared are present.
        """
        bounds = {guard: dict(declared) for guard, declared in self._bounds.items()}
        if arming_state is None:
            return bounds
        for guard, overlay in arming_state.limits_overlay.items():
            tightened = bounds.setdefault(guard, {})
            for key, value in overlay.items():
                amount = Decimal(str(value))
                current = tightened.get(key)
                if current is None or _WITHIN[key](amount, current):
                    tightened[key] = amount
        return bounds

    # -- the other authority events --------------------------------------------------

    def disarm(self, view, request_id=None):
        """Return the ``authority`` body that ends the current arm on an operator's word.

        Parameters
        ----------
        view : StateView
        request_id : str or None
            The control request, when one drove it.

        Returns
        -------
        dict

        Raises
        ------
        ProductionError
            When nothing is armed.
        """
        return self._ordinary_event(view, _DISARM, None, request_id)

    def revoke(self, view, reason, request_id=None):
        """Return the ``authority`` body that revokes the current arm for ``reason``.

        Parameters
        ----------
        view : StateView
        reason : str
        request_id : str or None

        Returns
        -------
        dict

        Raises
        ------
        ProductionError
            When nothing is armed or ``reason`` is empty.
        """
        problems = []
        _check_str(problems, "reason", reason)
        if problems:
            raise ProductionError(problems)
        return self._ordinary_event(view, _REVOKE, redact(reason), request_id)

    def expire_if_due(self, view, at_ms):
        """Return the ``authority`` body that expires the arm, once ``at_ms`` reaches its deadline.

        Parameters
        ----------
        view : StateView
        at_ms : int

        Returns
        -------
        dict or None
            None when nothing is armed or the deadline has not arrived.
        """
        state = _folded_arm(view)
        if state is None or not state.expired(at_ms):
            return None
        return self._ordinary_event(view, _EXPIRE, "expired", None)

    def _ordinary_event(self, view, event, reason, request_id):
        """Build the §6 ``authority`` body for a non-issue event on the current ordinary arm."""
        projection = view.arming
        if projection is None:
            raise ProductionError([f"cannot {event}: nothing is armed"])
        return {
            "authority_id": projection.authority_id,
            "event": event,
            "role": _ORDINARY,
            "request_id": request_id,
            "approval_id": None,
            "reason": reason,
        }

    # -- arming.json (D15) -------------------------------------------------------------

    @property
    def cache_path(self):
        """``serve_root.arming_cache`` — the head-bound cache file."""
        return self._cache.path

    def write_cache(self, view):
        """Cache the view's arm (or ``null``) at its head.

        Parameters
        ----------
        view : StateView

        Returns
        -------
        None
        """
        self._cache.write(_projection(view), view)

    def load_cache(self, ledger, view):
        """Validate the cache against the fold and return the fold's arm.

        Parameters
        ----------
        ledger : Ledger
        view : StateView

        Returns
        -------
        ArmingState or None
            The fold's arm regardless of expiry (``current`` judges that);
            an absent or stale cache is rebuilt first.

        Raises
        ------
        ProductionError
            As ``HeadBoundCache.load``.
        """
        projection = self._cache.load(ledger, view, _projection(view))
        return None if projection is None else ArmingState.from_obj(projection)


def _projection(view):
    """Return the JSON form of the fold's arm, or None."""
    return None if view.arming is None else view.arming.to_obj()


def _folded_arm(view):
    """Rebuild the fold's ``ArmingState`` from its projection, or None."""
    projection = _projection(view)
    return None if projection is None else ArmingState.from_obj(projection)


def _bound_digest(reduction):
    """Return the digest a reduction leg's right must name, recomputed from its signed intent."""
    if reduction is None:
        raise ProductionError(
            ["a reduction leg carries its signed ReductionIntent, digest and right; got None"]
        )
    problems = []
    digest = getattr(reduction, "digest", None)
    check_digest(problems, "reduction.digest", digest)
    signed = getattr(reduction, "signed", None)
    if not isinstance(signed, ReductionIntent):
        problems.append(
            f"reduction.signed must be the maker-signed ReductionIntent, got {signed!r}: "
            "a digest the leg merely asserts binds a right to nothing"
        )
    elif not problems:
        _agree(problems, "reduction.digest", signed.reduction_intent_digest(), digest)
    if problems:
        raise ProductionError(problems)
    return digest


def _declared_bounds(guards):
    """Collect the ``{guard: {"min"/"max": Decimal}}`` the guards declare under ``params.bound``."""
    problems, bounds = [], {}
    for name, guard in guards.items():
        params = guard.params if guard is not None and guard.params is not None else {}
        bound = params.get("bound")
        if not isinstance(bound, Mapping):
            continue
        declared = {}
        for key in _WITHIN:
            if key in bound:
                amount = _decimal(problems, f"guards.{name}.params.bound.{key}", bound[key])
                if amount is not None:
                    declared[key] = amount
        if declared:
            bounds[name] = declared
    if problems:
        raise ProductionError(problems)
    return bounds


def _proposal_fields(guards):
    """Map each guard whose measure a proposal alone can answer to its ``Proposal`` field."""
    fields_by_guard = {}
    for name, guard in guards.items():
        params = guard.params if guard is not None and guard.params is not None else {}
        field = _PROPOSAL_FIELDS.get(params.get("measure"))
        if field is not None:
            fields_by_guard[name] = field
    return fields_by_guard


# ---------------------------------------------------------------------------
# Reduction rights (D12)
# ---------------------------------------------------------------------------


class ReductionRights:
    """One single-use right per reduction intent digest — granted, reserved, revoked (D12).

    Parameters
    ----------
    clock : Clock
        ``now_ms()`` stamps a reservation and judges the authority's expiry.

    Examples
    --------
    Grant rights from a stored plan, then reserve one before its authorization::

        grant = ReductionRights.from_plan(plan, checker, "auth-red-1", plan.expires_ms)
        rights = ReductionRights(clock=clock)
        body = rights.reserve(view, plan.reduction_intent_digests[0], "flat-ref-1")
        body["authority_id"]  # 'auth-red-1'
    """

    def __init__(self, *, clock):
        self._clock = clock

    @classmethod
    def from_plan(cls, plan, approval, authority_id, expires_ms):
        """Fold a stored plan and its checker approval into a ``ReductionAuthorization``.

        Parameters
        ----------
        plan : ReductionPlan
        approval : VerifiedPrincipal
            The checker, as the verifier derived it for ``flatten_approval``.
        authority_id : str
        expires_ms : int
            The authority's own short deadline; never past the plan's.

        Returns
        -------
        ReductionAuthorization
            One right per intent digest, in maker-approved index order.

        Raises
        ------
        ProductionError
            On an unverified approval, an empty plan, indices not
            ``0..n-1`` in order, an intent bound to another release or
            another request, two entries with the same
            ``(instrument, side, qty, limit)``, or an expiry past the plan's.
        """
        problems = []
        if not isinstance(plan, ReductionPlan):
            problems.append(f"plan must be a ReductionPlan, got {plan!r}")
        if not isinstance(approval, VerifiedPrincipal):
            problems.append(
                f"a reduction authority rests on a verified checker approval, got {approval!r}"
            )
        _check_str(problems, "authority_id", authority_id)
        _check_instant(problems, "expires_ms", expires_ms)
        if problems:
            raise ProductionError(problems)
        _check_plan(problems, plan, expires_ms)
        if problems:
            raise ProductionError(problems)
        return ReductionAuthorization(
            authority_id=authority_id,
            release_hash=plan.release_hash,
            request_id=plan.intents[0].request_id,
            reduction_intent_digests=plan.reduction_intent_digests,
            expires_ms=expires_ms,
        )

    def reserve(self, view, digest, client_ref):
        """Return the ``authority_use`` body that consumes one right — never erased or reused.

        Parameters
        ----------
        view : StateView
        digest : str
            The ``reduction_intent_digest`` the leg will submit.
        client_ref : str
            The deterministic flatten client ref.

        Returns
        -------
        dict
            ``{authority_id, reduction_intent_digest, client_ref, reserved_at_ms}``.

        Raises
        ------
        ProductionError
            Without a reduction authority, after its expiry, for a digest
            it never granted, or for one already reserved.
        """
        problems = []
        check_digest(problems, "digest", digest)
        _check_str(problems, "client_ref", client_ref)
        if problems:
            raise ProductionError(problems)
        grant = _current_grant(view, "reserve a right")
        now = self._clock.now_ms()
        if now >= grant.expires_ms:
            raise ProductionError(
                [f"reduction authority {grant.authority_id!r} expired at {grant.expires_ms} (now {now})"]
            )
        grant.reserve(digest)  # the fold's own rule: granted, and not already reserved
        return {
            "authority_id": grant.authority_id,
            "reduction_intent_digest": digest,
            "client_ref": client_ref,
            "reserved_at_ms": now,
        }

    def revoke_unused(self, view, reason):
        """Return the ``authority`` body that revokes every unused right (D12, after a partial result).

        Parameters
        ----------
        view : StateView
        reason : str

        Returns
        -------
        dict

        Raises
        ------
        ProductionError
            When no reduction authority is current or ``reason`` is empty.
        """
        problems = []
        _check_str(problems, "reason", reason)
        if problems:
            raise ProductionError(problems)
        grant = _current_grant(view, "revoke unused rights")
        return {
            "authority_id": grant.authority_id,
            "event": _REVOKE,
            "role": _REDUCTION,
            "request_id": None,
            "approval_id": None,
            "reason": redact(reason),
        }


def _current_grant(view, verb):
    """Return the fold's reduction projection, or refuse naming what could not be done."""
    grant = view.reduction
    if grant is None:
        raise ProductionError([f"cannot {verb}: no reduction authority is current"])
    return grant


def _check_plan(problems, plan, expires_ms):
    """Accumulate D12's rules over a stored plan."""
    intents = plan.intents
    if not intents:
        problems.append("a plan with no intents authorises nothing")
        return
    if tuple(intent.index for intent in intents) != tuple(range(len(intents))):
        problems.append("intents must carry indices 0..n-1 in maker-approved order")
    foreign = [intent.index for intent in intents if intent.release_hash != plan.release_hash]
    if foreign:
        problems.append(f"intents {foreign} are bound to another release than the plan")
    requests = sorted({intent.request_id for intent in intents})
    if len(requests) != 1:
        problems.append(f"intents name different requests {requests}")
    seen, duplicates = set(), []
    for intent in intents:
        proposal = intent.proposal
        content = (proposal.instrument, proposal.side, proposal.qty, proposal.limit)
        if content in seen:
            duplicates.append(intent.index)
        seen.add(content)
    if duplicates:
        problems.append(f"intents {duplicates} repeat another entry's (instrument, side, qty, limit)")
    if expires_ms > plan.expires_ms:
        problems.append(
            f"authorization expiry {expires_ms} may not outlive the plan's {plan.expires_ms}"
        )


# ---------------------------------------------------------------------------
# The registry (§4.3) — import is registration
# ---------------------------------------------------------------------------

APPROVAL_KINDS = Registry("approval", ApprovalVerifier)
APPROVAL_KINDS.register("deny-all", DenyAll)
