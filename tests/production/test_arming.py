"""`arming.py` — authenticated proofs, the arming fold, scope (§5.6, D11).

Arming is an authenticated maker-checker act, not a config key. The maker
signs a canonical `ArmRequest` bound to the release hash, the exact
document rung, a bounded expiry, an allowlist that may only narrow and a
limits overlay that must be provably stricter; the checker signs an
`ArmApproval` over that request's digest. Both principal ids are
*derived from the proofs* — there is no `--by` — and for
`live_limited`/`live` the two must differ. The result is the frozen
`ArmingState` the ledger folds and `arming.json` merely caches.

`Arming` owns three things and no more (§5.13.1): the proofs, the read of
the fold, and **scope application**. It never mints a permit — that is
`leg.py`'s `Authority` — and it never branches on the rung, which is why
`check_conjunction` takes `rung` and `origin` as arguments and decides
for itself rather than letting a caller test them and skip the check.

The origin split is the safety-critical half of D11. A model leg needs a
current unexpired ordinary arm. A reduction leg needs a current unexpired
**unconsumed right for its own digest** and must NOT need an ordinary
arm, because D10/D12 revoke ordinary arming on leaving `active` and
forbid reissuing it while `reducing` — demanding one there would refuse
every live flatten leg, removing the emergency de-risking path at exactly
the rungs it exists for.

The verifier is a seam: `deny-all` is the core default and refuses
everything, so the tests carry their own `FakeVerifier` that accepts
`b"<principal>:<purpose>"` and derives the principal id and proof digest
from those bytes — the shape a child HMAC or signature verifier has.
"""

import dataclasses
import hashlib
import inspect
import json
import os
from decimal import Decimal

import pytest

from dskit.production import arming as arming_module
from dskit.production import vocab
from dskit.production.arming import (
    APPROVAL_KINDS,
    CONJUNCTION_REASONS,
    ApprovalVerifier,
    ArmApproval,
    ArmingState,
    ArmRequest,
    Arming,
    ConjunctionResult,
    DenyAll,
    ReductionRights,
    VerifiedPrincipal,
    approval_verifier,
    verifier_fingerprint,
)
from dskit.production.base import ProductionError, canonical_hash
from dskit.production.document import ServeDocument
from dskit.production.ledger import ServeRoot
from dskit.production.records import (
    Candidate,
    Proposal,
    ReductionAuthorization,
    ReductionIntent,
    ReductionPlan,
)
from dskit.production.release import fingerprint_class
from dskit.production.state import SeriesState
from tests.production.test_document import (
    example_document,
    live_capable_document,
    minimal_document,
)
from tests.production.test_release import artifact_root, manifest

# ---------------------------------------------------------------------------
# Fixed material
# ---------------------------------------------------------------------------

SERIES_ID = "018f0f4e-7b21-7d3a-9c31-6d8f36d806a1"
GENESIS_HASH = "0" * 64
BASE_MS = 1_767_268_800_000
PROCESS_ID = "proc-1"

DIGEST_A = "1" * 64
DIGEST_B = "2" * 64
DIGEST_RISK = "5" * 64

#: `document.arming.max_duration_s` in §4.1's illustration.
MAX_DURATION_S = 14_400
MAX_DURATION_MS = MAX_DURATION_S * 1000

#: The release's universe — `feed_spec.required_keys` in test_release.
UNIVERSE = ("AAPL", "MSFT")

MAKER = "maker-1"
CHECKER = "checker-1"

#: `ArmingState`'s eleven members, in §5.6's order.
ARMING_STATE_MEMBERS = (
    "authority_id",
    "release_hash",
    "rung",
    "maker",
    "checker",
    "armed_at_ms",
    "armed_until_ms",
    "allowlist",
    "limits_overlay",
    "request_proof_digest",
    "approval_proof_digest",
)

#: `ArmRequest`'s six members, in §5.6's order.
ARM_REQUEST_MEMBERS = (
    "release_hash",
    "rung",
    "allowlist",
    "limits_overlay",
    "requested_until_ms",
    "request_proof",
)

#: §6's `control_request` body, as `test_state.py` folds it.
CONTROL_REQUEST_KEYS = {
    "request_id",
    "purpose",
    "payload",
    "principal_digest",
    "proof_digest",
    "expires_ms",
}

#: §6's `authority` body for an ordinary issue, as `test_state.py` folds it.
AUTHORITY_ISSUE_KEYS = {
    "authority_id",
    "event",
    "role",
    "request_id",
    "approval_id",
    "reason",
    "arming",
}

REQUEST_ID = "req-arm-1"
APPROVAL_ID = "apr-arm-1"


# ---------------------------------------------------------------------------
# Local fakes
# ---------------------------------------------------------------------------


class FakeClock:
    """The two `Clock` methods arming needs; no wall time."""

    def __init__(self, ms=BASE_MS):
        self._ms = int(ms)

    def now_ms(self):
        return self._ms

    def monotonic(self):
        return self._ms / 1000.0

    def advance(self, ms):
        self._ms += int(ms)
        return self._ms


class FakeVerifier(ApprovalVerifier):
    """Accepts `b"<principal>:<purpose>"`; derives the id from the proof.

    The shape a child HMAC/signature verifier has: the caller never says
    who it is, the proof does. `seen` records what it was handed so a
    test can pin that the bytes signed are the request's canonical bytes.
    """

    LIVE_CAPABLE = True
    _PARAMS = ()

    def __init__(self, params=None):
        self.params = dict(params or {})
        self.seen = []

    def verify(self, canonical_bytes, proof, purpose):
        self.check_purpose(purpose)
        self.seen.append((canonical_bytes, proof, purpose))
        if not isinstance(proof, bytes) or proof.count(b":") != 1:
            raise ProductionError(["unreadable proof"])
        principal, claimed = proof.split(b":")
        if claimed.decode() != purpose:
            raise ProductionError([f"proof is not for purpose {purpose}"])
        return VerifiedPrincipal(
            id=principal.decode(), proof_digest=hashlib.sha256(proof).hexdigest()
        )


class RefusingVerifier(ApprovalVerifier):
    """A verifier that refuses every proof, like `deny-all`."""

    _PARAMS = ()

    def __init__(self, params=None):
        self.params = dict(params or {})

    def verify(self, canonical_bytes, proof, purpose):
        raise ProductionError(["proof refused"])


@dataclasses.dataclass(frozen=True)
class FakeInvocation:
    """`bundles.Invocation`'s four knobs, without importing it."""

    armed: bool
    env_release_hash: object
    once: bool = False
    max_ticks: object = None


@dataclasses.dataclass(frozen=True)
class FakeReduction:
    """`LegBindings.reduction`: the signed intent, its digest, its right."""

    signed: object
    digest: str
    right: object = None


class Chain:
    """Hands out §6 envelopes with dense `seq` and a chained `hash`."""

    def __init__(self, series_id=SERIES_ID, release_hash="b" * 64):
        self.series_id = series_id
        self.release_hash = release_hash
        self.seq = 0
        self.head_hash = GENESIS_HASH

    def env(self, kind, body, rid=None):
        self.seq += 1
        prev = self.head_hash
        caller = {"kind": kind, "id": rid or f"{kind}-{self.seq}", "body": dict(body)}
        digest = canonical_hash(caller)
        env = {
            **caller,
            "payload_digest": digest,
            "seq": self.seq,
            "series_id": self.series_id,
            "process_id": PROCESS_ID,
            "release_hash": self.release_hash,
            "recorded_at_ms": BASE_MS + self.seq,
            "schema_version": 1,
            "prev_hash": prev,
            "hash": hashlib.sha256((prev + digest).encode()).hexdigest(),
        }
        self.head_hash = env["hash"]
        return env


class RecordLedger:
    """Envelopes, the fold they produce, and the `head`/`scan` a cache needs."""

    def __init__(self, series_id=SERIES_ID):
        self.state = SeriesState(series_id)
        self.chain = Chain(series_id)
        self.records = []

    def add(self, kind, body):
        env = self.chain.env(kind, body)
        self.records.append(env)
        self.state.apply(env)
        return env

    def head(self):
        return self.state.head()

    def scan(self, kind=None, since_seq=0):
        return tuple(
            env
            for env in self.records
            if env["seq"] > since_seq and (kind is None or env["kind"] == kind)
        )

    def view(self):
        return self.state.snapshot()


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------


@pytest.fixture
def release(tmp_path):
    """A complete `ReleaseManifest` whose universe is `UNIVERSE`."""
    return manifest(artifact_root(tmp_path))


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def verifier():
    return FakeVerifier()


def document_for(rung):
    """The smallest document that validates at `rung`."""
    if rung in ("live_limited", "live"):
        return live_capable_document(rung)
    if rung == "paper":
        return example_document(rung="paper")
    return minimal_document()


def make_arming(tmp_path, release, clock, verifier, document=None, rung="live_limited"):
    """An `Arming` over a live-capable document unless told otherwise."""
    obj = document if document is not None else document_for(rung)
    obj["series_id"] = SERIES_ID
    return Arming(
        ServeDocument.from_obj(obj),
        release,
        serve_root=ServeRoot(str(tmp_path / "serve"), SERIES_ID),
        verifier=verifier,
        clock=clock,
    )


def proof_for(principal, purpose):
    return f"{principal}:{purpose}".encode()


def arm_request(release, **overrides):
    fields = {
        "release_hash": release.release_hash,
        "rung": "live_limited",
        "allowlist": ("AAPL",),
        "limits_overlay": {},
        "requested_until_ms": BASE_MS + 3_600_000,
        "request_proof": proof_for(MAKER, "arm_request"),
    }
    fields.update(overrides)
    return ArmRequest(**fields)


def arm_approval(request, principal=CHECKER):
    return ArmApproval(
        request_digest=request.request_digest(),
        approval_proof=proof_for(principal, "arm_approval"),
    )


def approve_arm(arming, approval, request, request_id=REQUEST_ID,
                approval_id=APPROVAL_ID, *, view=None, at_ms=BASE_MS,
                readiness_verdict="go", sentinel_present=False):
    """`Arming.approve` with R23's world keywords — the one owner of their defaults.

    Every call site goes through here, so adding a conjunct to `approve`
    is one edit, and a test that wants a refusal names only the conjunct
    it is breaking.  The defaults are the world an arm may be issued in:
    an active breaker, no `HALT` sentinel and a readiness GO.
    """
    return arming.approve(
        approval,
        request,
        request_id,
        approval_id,
        view=view_with() if view is None else view,
        at_ms=at_ms,
        readiness_verdict=readiness_verdict,
        sentinel_present=sentinel_present,
    )


def issue(arming, release, **overrides):
    """Run one full maker-checker cycle; return `(body, ArmingState)`."""
    request = arm_request(release, **overrides)
    arming.request(request, REQUEST_ID)
    return approve_arm(arming, arm_approval(request), request)


def ledger_with(*records):
    """A `RecordLedger` holding the hand-built §6 envelopes given."""
    led = RecordLedger()
    for kind, body in records:
        led.add(kind, body)
    return led


def view_with(*records):
    """A `StateView` folded from hand-built §6 envelopes."""
    return ledger_with(*records).view()


def ordinary_issue_body(state, request_id=REQUEST_ID, approval_id=APPROVAL_ID):
    return {
        "authority_id": state.authority_id,
        "event": "issue",
        "role": "ordinary",
        "request_id": request_id,
        "approval_id": approval_id,
        "reason": None,
        "arming": state.to_obj(),
    }


def reduction_issue_body(authorization, request_id="req-flatten-1"):
    return {
        "authority_id": authorization.authority_id,
        "event": "issue",
        "role": "reduction",
        "request_id": request_id,
        "approval_id": "apr-flatten-1",
        "reason": None,
        "authorization": authorization.to_obj(),
    }


def trip_body(to_state, from_state="active"):
    """§6's `trip` body — the only record that moves the fold's breaker."""
    return {
        "from": from_state,
        "to": to_state,
        "reason": "operator",
        "actor": "operator",
        "control_request_id": "req-trip-1",
        "principal_digest": DIGEST_A,
        "proof_digest": DIGEST_B,
        "acknowledged_trip_id": None,
    }


def authority_use_body(authorization, digest, client_ref="flat-ref-1"):
    return {
        "authority_id": authorization.authority_id,
        "reduction_intent_digest": digest,
        "client_ref": client_ref,
        "reserved_at_ms": BASE_MS + 40,
    }


def proposal(instrument="AAPL", qty="10", notional="1000"):
    return Proposal(
        id="cand-1",
        instrument=instrument,
        side="buy",
        qty=Decimal(qty),
        notional=Decimal(notional),
        limit=Decimal("100"),
        tif="ioc",
        expires_ms=BASE_MS + 60_000,
        reference_price=Decimal("100"),
        exposure=Decimal(notional),
        direction="long",
        confidence=0.5,
        prediction=0.01,
        baseline=0.0,
        expected_value=5.0,
        inputs_asof_ms=BASE_MS,
        inputs_digest=DIGEST_A,
        coverage_digest=DIGEST_B,
        quote_asof_ms=BASE_MS,
        quote_digest=DIGEST_A,
        extra={},
    )


def reduction_intent(release, index=0, instrument="AAPL", request_id="req-flatten-1",
                     release_hash=None, qty="10"):
    return ReductionIntent(
        release_hash=release_hash or release.release_hash,
        request_id=request_id,
        index=index,
        candidate=Candidate(id=f"cand-{index}", instrument=instrument,
                            scope_keys=(instrument,)),
        proposal=proposal(instrument=instrument, qty=qty),
        risk_state_digest=DIGEST_RISK,
        expires_ms=BASE_MS + 600_000,
    )


def reduction_plan(release, intents=None):
    intents = tuple(intents if intents is not None else (
        reduction_intent(release, 0, "AAPL"),
        reduction_intent(release, 1, "MSFT"),
    ))
    return ReductionPlan(
        release_hash=release.release_hash,
        risk_state_digest=DIGEST_RISK,
        intents=intents,
        reduction_intent_digests=tuple(i.reduction_intent_digest() for i in intents),
        expires_ms=BASE_MS + 600_000,
    )


def checker_principal():
    proof = proof_for(CHECKER, "flatten_approval")
    return VerifiedPrincipal(id=CHECKER, proof_digest=hashlib.sha256(proof).hexdigest())


# ---------------------------------------------------------------------------
# `ArmingState` — the frozen folded value (§5.6)
# ---------------------------------------------------------------------------


def sample_state(**overrides):
    fields = {
        "authority_id": "auth-1",
        "release_hash": "b" * 64,
        "rung": "live_limited",
        "maker": MAKER,
        "checker": CHECKER,
        "armed_at_ms": BASE_MS,
        "armed_until_ms": BASE_MS + 3_600_000,
        "allowlist": ("AAPL",),
        "limits_overlay": {},
        "request_proof_digest": DIGEST_A,
        "approval_proof_digest": DIGEST_B,
    }
    fields.update(overrides)
    return ArmingState(**fields)


def test_arming_state_carries_the_eleven_members_of_5_6_in_order():
    assert tuple(f.name for f in dataclasses.fields(ArmingState)) == ARMING_STATE_MEMBERS
    assert ArmingState.__dataclass_params__.frozen is True


def test_arming_state_round_trips_through_the_object_the_authority_record_embeds():
    state = sample_state()
    assert set(state.to_obj()) == set(ARMING_STATE_MEMBERS)
    assert state.to_obj()["allowlist"] == ["AAPL"]
    assert ArmingState.from_obj(state.to_obj()) == state


def test_arming_state_from_obj_default_denies_an_unknown_key():
    obj = sample_state().to_obj()
    obj["nonsuch"] = 1
    with pytest.raises(ProductionError) as excinfo:
        ArmingState.from_obj(obj)
    assert "nonsuch" in str(excinfo.value)


def test_an_arm_is_expired_at_and_after_its_deadline_never_before():
    state = sample_state()
    assert state.expired(state.armed_until_ms - 1) is False
    assert state.expired(state.armed_until_ms) is True
    assert state.expired(state.armed_until_ms + 1) is True


@pytest.mark.parametrize(
    "overrides",
    [
        {"rung": "sandbox"},
        {"rung": None},
        {"armed_until_ms": BASE_MS},
        {"armed_until_ms": BASE_MS - 1},
        {"release_hash": "short"},
        {"maker": ""},
        {"checker": ""},
    ],
)
def test_an_arming_state_refuses_a_member_no_permit_could_rest_on(overrides):
    with pytest.raises(ProductionError):
        sample_state(**overrides)


# ---------------------------------------------------------------------------
# `ArmRequest` / `ArmApproval` — what is signed, and what the digest covers
# ---------------------------------------------------------------------------


def test_arm_request_carries_the_six_members_of_5_6_in_order():
    assert tuple(f.name for f in dataclasses.fields(ArmRequest)) == ARM_REQUEST_MEMBERS
    assert ArmRequest.__dataclass_params__.frozen is True


def test_arm_approval_carries_the_two_members_of_5_6():
    assert tuple(f.name for f in dataclasses.fields(ArmApproval)) == (
        "request_digest",
        "approval_proof",
    )
    assert ArmApproval.__dataclass_params__.frozen is True


def test_the_signed_bytes_exclude_the_signature_over_them(release):
    # A proof cannot be part of what it signs.
    request = arm_request(release)
    signed = json.loads(request.canonical_bytes().decode("ascii"))
    assert "request_proof" not in signed
    assert set(signed) == set(ARM_REQUEST_MEMBERS) - {"request_proof"}


def test_the_request_digest_is_the_hash_of_exactly_those_bytes(release):
    request = arm_request(release)
    assert request.request_digest() == hashlib.sha256(
        request.canonical_bytes()
    ).hexdigest()
    assert len(request.request_digest()) == 64


@pytest.mark.parametrize(
    "overrides",
    [
        {"rung": "live"},
        {"allowlist": ("MSFT",)},
        {"limits_overlay": {"size": {"max": "5"}}},
        {"requested_until_ms": BASE_MS + 60_000},
    ],
)
def test_changing_any_signed_field_moves_the_request_digest(release, overrides):
    assert arm_request(release).request_digest() != arm_request(
        release, **overrides
    ).request_digest()


def test_re_signing_the_same_request_does_not_move_its_digest(release):
    first = arm_request(release)
    second = arm_request(release, request_proof=b"other:arm_request")
    assert first.request_digest() == second.request_digest()


def test_arm_request_to_obj_never_carries_the_proof_bytes(release):
    # redact.py: proofs are credentials and no secret reaches a record.
    assert "request_proof" not in arm_request(release).to_obj()


# ---------------------------------------------------------------------------
# The `ApprovalVerifier` seam (D11)
# ---------------------------------------------------------------------------


def test_approval_verifier_is_abstract_and_verify_is_its_hook():
    assert inspect.isabstract(ApprovalVerifier)
    assert "verify" in ApprovalVerifier.__abstractmethods__
    with pytest.raises(TypeError):
        ApprovalVerifier({})


def test_deny_all_is_the_only_verifier_core_registers():
    assert APPROVAL_KINDS.kinds() == ("deny-all",)
    assert APPROVAL_KINDS.family == "approval"
    assert APPROVAL_KINDS.resolve("deny-all") is DenyAll


@pytest.mark.parametrize("purpose", vocab.APPROVAL_PURPOSES)
def test_deny_all_refuses_every_proof_for_every_purpose(purpose):
    with pytest.raises(ProductionError):
        DenyAll({}).verify(b"payload", b"maker-1:" + purpose.encode(), purpose)


def test_deny_all_default_denies_an_unknown_param():
    with pytest.raises(ProductionError) as excinfo:
        DenyAll({"trust_root_env": "X"})
    assert "trust_root_env" in str(excinfo.value)


def test_a_verifier_is_not_live_capable_unless_it_says_so():
    assert ApprovalVerifier.LIVE_CAPABLE is False
    assert DenyAll.LIVE_CAPABLE is False


def test_constructing_a_verifier_performs_no_network_io(monkeypatch):
    """§5.6: "construction resolves secrets once, performs no network
    I/O" — the same monkeypatched-socket pin `ShadowExecutor` carries."""
    import socket

    def forbidden(*args, **kwargs):
        raise AssertionError("a verifier opened a socket at construction")

    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    assert isinstance(DenyAll({}), ApprovalVerifier)


@pytest.mark.parametrize("purpose", vocab.APPROVAL_PURPOSES)
def test_check_purpose_accepts_every_closed_purpose(purpose):
    assert DenyAll({}).check_purpose(purpose) == purpose


@pytest.mark.parametrize("purpose", ["", None, "arm", "sign", "halt"])
def test_check_purpose_refuses_a_purpose_outside_the_closed_set(purpose):
    with pytest.raises(ProductionError):
        DenyAll({}).check_purpose(purpose)


def test_check_purpose_is_owned_by_the_seam_so_no_child_respells_it():
    assert "check_purpose" not in ApprovalVerifier.__abstractmethods__
    assert DenyAll.check_purpose is ApprovalVerifier.check_purpose


def test_the_verifier_fingerprint_binds_the_class_source_and_its_params():
    assert verifier_fingerprint(DenyAll, {}) == canonical_hash(
        {"class": fingerprint_class(DenyAll), "params": {}}
    )
    assert len(verifier_fingerprint(DenyAll, {})) == 64


def test_the_verifier_fingerprint_moves_with_the_params_and_the_class():
    assert verifier_fingerprint(DenyAll, {}) != verifier_fingerprint(
        DenyAll, {"trust_root": "a"}
    )
    assert verifier_fingerprint(DenyAll, {}) != verifier_fingerprint(FakeVerifier, {})


def test_the_verifier_fingerprint_is_stable_across_calls():
    assert verifier_fingerprint(DenyAll, {"k": 1}) == verifier_fingerprint(
        DenyAll, {"k": 1}
    )


def test_the_documents_approval_block_is_what_selects_the_verifier():
    doc = ServeDocument.from_obj(example_document())
    built = approval_verifier(doc)
    assert isinstance(built, DenyAll)
    assert verifier_fingerprint(type(built), {}) == verifier_fingerprint(DenyAll, {})


def test_verified_principal_carries_the_id_and_the_proof_digest():
    assert tuple(f.name for f in dataclasses.fields(VerifiedPrincipal)) == (
        "id",
        "proof_digest",
    )
    assert VerifiedPrincipal.__dataclass_params__.frozen is True


# ---------------------------------------------------------------------------
# `request` — the maker half (D11)
# ---------------------------------------------------------------------------


def test_a_request_becomes_the_section_6_control_request_body(tmp_path, release, clock,
                                                              verifier):
    arming = make_arming(tmp_path, release, clock, verifier)
    request = arm_request(release)
    body = arming.request(request, REQUEST_ID)
    assert set(body) == CONTROL_REQUEST_KEYS
    assert body["request_id"] == REQUEST_ID
    assert body["purpose"] == "arm_request"
    assert body["payload"] == request.to_obj()
    assert body["expires_ms"] == request.requested_until_ms
    assert len(body["principal_digest"]) == 64
    assert len(body["proof_digest"]) == 64


def test_the_recorded_request_carries_the_release_it_binds(tmp_path, release, clock,
                                                           verifier):
    arming = make_arming(tmp_path, release, clock, verifier)
    body = arming.request(arm_request(release), REQUEST_ID)
    assert body["payload"]["release_hash"] == release.release_hash


def test_the_maker_is_derived_from_the_proof_over_the_signed_bytes(tmp_path, release,
                                                                   clock, verifier):
    arming = make_arming(tmp_path, release, clock, verifier)
    request = arm_request(release)
    arming.request(request, REQUEST_ID)
    signed_bytes, proof, purpose = verifier.seen[-1]
    assert signed_bytes == request.canonical_bytes()
    assert proof == request.request_proof
    assert purpose == "arm_request"


def test_a_request_the_verifier_refuses_produces_nothing(tmp_path, release, clock):
    arming = make_arming(tmp_path, release, clock, RefusingVerifier())
    with pytest.raises(ProductionError):
        arming.request(arm_request(release), REQUEST_ID)


def test_a_proof_signed_for_another_purpose_refuses(tmp_path, release, clock, verifier):
    arming = make_arming(tmp_path, release, clock, verifier)
    with pytest.raises(ProductionError):
        arming.request(
            arm_request(release, request_proof=proof_for(MAKER, "resume")), REQUEST_ID
        )


@pytest.mark.parametrize("rung", ["live", "paper", "shadow"])
def test_a_request_cannot_name_a_rung_other_than_the_documents(tmp_path, release, clock,
                                                               verifier, rung):
    """D11: "it cannot promote the rung"."""
    arming = make_arming(tmp_path, release, clock, verifier)
    with pytest.raises(ProductionError) as excinfo:
        arming.request(arm_request(release, rung=rung), REQUEST_ID)
    assert "rung" in str(excinfo.value)


def test_a_request_bound_to_another_release_refuses(tmp_path, release, clock, verifier):
    arming = make_arming(tmp_path, release, clock, verifier)
    with pytest.raises(ProductionError):
        arming.request(arm_request(release, release_hash="c" * 64), REQUEST_ID)


def test_an_expiry_beyond_max_duration_refuses(tmp_path, release, clock, verifier):
    arming = make_arming(tmp_path, release, clock, verifier)
    with pytest.raises(ProductionError) as excinfo:
        arming.request(
            arm_request(release, requested_until_ms=clock.now_ms() + MAX_DURATION_MS + 1),
            REQUEST_ID,
        )
    assert "max_duration_s" in str(excinfo.value)


def test_an_expiry_of_exactly_max_duration_is_accepted(tmp_path, release, clock,
                                                       verifier):
    arming = make_arming(tmp_path, release, clock, verifier)
    body = arming.request(
        arm_request(release, requested_until_ms=clock.now_ms() + MAX_DURATION_MS),
        REQUEST_ID,
    )
    assert body["expires_ms"] == clock.now_ms() + MAX_DURATION_MS


@pytest.mark.parametrize("delta", [0, -1, -60_000])
def test_an_expiry_that_is_not_in_the_future_refuses(tmp_path, release, clock, verifier,
                                                     delta):
    # D11: "expiry is mandatory"; an arm that is already dead is not one.
    arming = make_arming(tmp_path, release, clock, verifier)
    with pytest.raises(ProductionError):
        arming.request(
            arm_request(release, requested_until_ms=clock.now_ms() + delta), REQUEST_ID
        )


def test_an_allowlist_may_only_narrow_the_releases_universe(tmp_path, release, clock,
                                                            verifier):
    arming = make_arming(tmp_path, release, clock, verifier)
    arming.request(arm_request(release, allowlist=UNIVERSE), REQUEST_ID)
    arming.request(arm_request(release, allowlist=("AAPL",)), REQUEST_ID)
    with pytest.raises(ProductionError) as excinfo:
        arming.request(arm_request(release, allowlist=("AAPL", "TSLA")), REQUEST_ID)
    assert "TSLA" in str(excinfo.value)


def test_an_allowlist_that_names_nothing_refuses(tmp_path, release, clock, verifier):
    """D11: the allowlist is what a permit's instrument is checked
    against, so an empty one is a request that arms nothing — a config
    slip that must refuse rather than issue an arm no leg can ever use."""
    arming = make_arming(tmp_path, release, clock, verifier)
    with pytest.raises(ProductionError) as excinfo:
        arming.request(arm_request(release, allowlist=()), REQUEST_ID)
    assert "allowlist" in str(excinfo.value)


def test_an_overlay_may_lower_a_max_but_never_raise_one(tmp_path, release, clock,
                                                        verifier):
    arming = make_arming(tmp_path, release, clock, verifier)
    arming.request(arm_request(release, limits_overlay={"size": {"max": "50"}}),
                   REQUEST_ID)
    with pytest.raises(ProductionError) as excinfo:
        arming.request(arm_request(release, limits_overlay={"size": {"max": "200"}}),
                       REQUEST_ID)
    assert "size" in str(excinfo.value)


def test_an_overlay_may_raise_a_min_but_never_lower_one(tmp_path, release, clock,
                                                        verifier):
    arming = make_arming(tmp_path, release, clock, verifier)
    arming.request(arm_request(release, limits_overlay={"day_loss": {"min": "-100"}}),
                   REQUEST_ID)
    with pytest.raises(ProductionError) as excinfo:
        arming.request(arm_request(release, limits_overlay={"day_loss": {"min": "-900"}}),
                       REQUEST_ID)
    assert "day_loss" in str(excinfo.value)


def test_an_overlay_may_tighten_an_integer_bound(tmp_path, release, clock, verifier):
    arming = make_arming(tmp_path, release, clock, verifier)
    arming.request(arm_request(release, limits_overlay={"stale": {"max": 20000}}),
                   REQUEST_ID)


def test_an_overlay_naming_a_guard_the_document_never_declared_refuses(
    tmp_path, release, clock, verifier
):
    arming = make_arming(tmp_path, release, clock, verifier)
    with pytest.raises(ProductionError) as excinfo:
        arming.request(arm_request(release, limits_overlay={"nonsuch": {"max": "1"}}),
                       REQUEST_ID)
    assert "nonsuch" in str(excinfo.value)


def test_an_overlay_on_a_guard_with_no_bound_refuses(tmp_path, release, clock, verifier):
    # `sane` is a `range` guard: there is no bound to prove stricter.
    arming = make_arming(tmp_path, release, clock, verifier)
    with pytest.raises(ProductionError):
        arming.request(arm_request(release, limits_overlay={"sane": {"max": "1"}}),
                       REQUEST_ID)


def test_an_overlay_may_not_introduce_a_bound_the_document_does_not_declare(
    tmp_path, release, clock, verifier
):
    """`size` declares only a `max`; a `min` the release never graded is
    a new guard, not a tightening of an existing one."""
    arming = make_arming(tmp_path, release, clock, verifier)
    with pytest.raises(ProductionError):
        arming.request(arm_request(release, limits_overlay={"size": {"min": "1"}}),
                       REQUEST_ID)


def test_an_overlay_key_that_is_not_a_bound_refuses(tmp_path, release, clock, verifier):
    arming = make_arming(tmp_path, release, clock, verifier)
    with pytest.raises(ProductionError):
        arming.request(arm_request(release, limits_overlay={"size": {"warn_at": 0.5}}),
                       REQUEST_ID)


def test_every_problem_with_a_request_is_reported_in_one_raise(tmp_path, release, clock,
                                                               verifier):
    arming = make_arming(tmp_path, release, clock, verifier)
    with pytest.raises(ProductionError) as excinfo:
        arming.request(
            arm_request(
                release,
                rung="live",
                allowlist=("TSLA",),
                requested_until_ms=clock.now_ms() + MAX_DURATION_MS + 1,
            ),
            REQUEST_ID,
        )
    assert len(excinfo.value.problems) >= 3


# ---------------------------------------------------------------------------
# `approve` — the checker half, and the `ArmingState` it issues (D11)
# ---------------------------------------------------------------------------


def test_an_approval_issues_the_section_6_authority_record(tmp_path, release, clock,
                                                           verifier):
    arming = make_arming(tmp_path, release, clock, verifier)
    body, state = issue(arming, release)
    assert set(body) == AUTHORITY_ISSUE_KEYS
    assert body["event"] == "issue"
    assert body["role"] == "ordinary"
    assert body["request_id"] == REQUEST_ID
    assert body["approval_id"] == APPROVAL_ID
    assert body["authority_id"] == state.authority_id
    assert body["arming"] == state.to_obj()
    assert body["event"] in vocab.AUTHORITY_EVENTS
    assert body["role"] in vocab.AUTHORITY_ROLES


def test_the_issued_state_binds_the_release_rung_expiry_scope_and_both_proofs(
    tmp_path, release, clock, verifier
):
    arming = make_arming(tmp_path, release, clock, verifier)
    request = arm_request(release, allowlist=("AAPL",),
                          limits_overlay={"size": {"max": "50"}})
    arming.request(request, REQUEST_ID)
    _, state = approve_arm(arming, arm_approval(request), request)
    assert state.release_hash == release.release_hash
    assert state.rung == "live_limited"
    assert state.maker == MAKER
    assert state.checker == CHECKER
    assert state.armed_at_ms == clock.now_ms()
    assert state.armed_until_ms == request.requested_until_ms
    assert tuple(state.allowlist) == ("AAPL",)
    assert state.limits_overlay == {"size": {"max": "50"}}
    assert state.request_proof_digest == hashlib.sha256(
        request.request_proof
    ).hexdigest()
    assert state.approval_proof_digest == hashlib.sha256(
        proof_for(CHECKER, "arm_approval")
    ).hexdigest()


def test_the_checker_proof_is_verified_for_the_approval_purpose(tmp_path, release,
                                                                clock, verifier):
    arming = make_arming(tmp_path, release, clock, verifier)
    issue(arming, release)
    assert verifier.seen[-1][2] == "arm_approval"


def test_an_approval_over_a_different_request_refuses(tmp_path, release, clock,
                                                      verifier):
    arming = make_arming(tmp_path, release, clock, verifier)
    request = arm_request(release)
    other = arm_request(release, allowlist=("MSFT",))
    arming.request(request, REQUEST_ID)
    with pytest.raises(ProductionError) as excinfo:
        approve_arm(arming, arm_approval(other), request)
    assert "request_digest" in str(excinfo.value)


def test_an_approval_of_a_request_that_has_already_expired_refuses(tmp_path, release,
                                                                   clock, verifier):
    arming = make_arming(tmp_path, release, clock, verifier)
    request = arm_request(release, requested_until_ms=clock.now_ms() + 60_000)
    arming.request(request, REQUEST_ID)
    clock.advance(60_000)
    with pytest.raises(ProductionError):
        approve_arm(arming, arm_approval(request), request, at_ms=clock.now_ms())


@pytest.mark.parametrize("rung", ["live_limited", "live"])
def test_maker_and_checker_must_differ_at_the_live_rungs(tmp_path, release, clock,
                                                         verifier, rung):
    obj = document_for(rung)
    arming = make_arming(tmp_path, release, clock, verifier, document=obj)
    request = arm_request(release, rung=rung)
    arming.request(request, REQUEST_ID)
    with pytest.raises(ProductionError) as excinfo:
        approve_arm(arming, arm_approval(request, principal=MAKER), request)
    assert MAKER in str(excinfo.value)


@pytest.mark.parametrize("rung", ["shadow", "paper"])
def test_one_principal_may_arm_a_rung_that_moves_no_real_money(tmp_path, release, clock,
                                                               verifier, rung):
    """D11 requires distinct principals "for >= live_limited"; below it
    there is no live permit to gate, and demanding a second human would
    make the shadow rehearsal harder than the thing it rehearses."""
    obj = document_for(rung)
    arming = make_arming(tmp_path, release, clock, verifier, document=obj)
    request = arm_request(release, rung=rung)
    arming.request(request, REQUEST_ID)
    _, state = approve_arm(arming, arm_approval(request, principal=MAKER), request)
    assert state.maker == state.checker == MAKER


def test_an_approval_the_verifier_refuses_issues_nothing(tmp_path, release, clock):
    verifier = FakeVerifier()
    arming = make_arming(tmp_path, release, clock, verifier)
    request = arm_request(release)
    arming.request(request, REQUEST_ID)
    bad = ArmApproval(request_digest=request.request_digest(),
                      approval_proof=b"not-a-proof")
    with pytest.raises(ProductionError):
        approve_arm(arming, bad, request)


def test_the_authority_id_is_derived_and_never_depends_on_wall_time(tmp_path, release,
                                                                    clock, verifier):
    arming = make_arming(tmp_path, release, clock, verifier)
    request = arm_request(release)
    arming.request(request, REQUEST_ID)
    _, first = approve_arm(arming, arm_approval(request), request)
    clock.advance(1234)
    _, again = approve_arm(arming, arm_approval(request), request)
    _, other = approve_arm(arming, arm_approval(request), request,
                           approval_id="apr-arm-2")
    assert first.authority_id == again.authority_id
    assert first.authority_id != other.authority_id
    assert len(first.authority_id) == 64


# ---------------------------------------------------------------------------
# The world `approve` arms into (R23, D10): active breaker, no HALT, a GO
# ---------------------------------------------------------------------------

#: The four world arguments R23 adds, in the order the signature declares them.
APPROVE_WORLD = ("view", "at_ms", "readiness_verdict", "sentinel_present")


def test_approve_takes_the_world_it_judges_as_required_keyword_only_arguments():
    """Keyword-only and defaultless, so an older four-argument call fails
    loudly instead of arming under an assumed GO into a tripped breaker."""
    signature = inspect.signature(Arming.approve)
    assert tuple(signature.parameters) == (
        "self", "arm_approval", "request", "request_id", "approval_id",
    ) + APPROVE_WORLD
    for name in APPROVE_WORLD:
        parameter = signature.parameters[name]
        assert parameter.kind is inspect.Parameter.KEYWORD_ONLY, name
        assert parameter.default is inspect.Parameter.empty, name


@pytest.mark.parametrize("breaker", ["reducing", "halted"])
def test_no_ordinary_arm_is_issued_unless_the_breaker_is_active(tmp_path, release,
                                                                clock, verifier,
                                                                breaker):
    """D10: ordinary arming is issued only while `active`.  Every breaker
    transition revokes it and D12 forbids reissuing it while `reducing`,
    so an arm approved into a tripped breaker would hand back exactly the
    authority the trip removed — without a second trip to notice."""
    arming = make_arming(tmp_path, release, clock, verifier)
    request = arm_request(release)
    arming.request(request, REQUEST_ID)
    with pytest.raises(ProductionError) as excinfo:
        approve_arm(arming, arm_approval(request), request,
                    view=view_with(("trip", trip_body(breaker))))
    assert "breaker" in str(excinfo.value)
    assert breaker in str(excinfo.value)


def test_no_ordinary_arm_is_issued_while_the_halt_sentinel_is_present(tmp_path, release,
                                                                      clock, verifier):
    """The `HALT` file is the out-of-band kill switch (§5.6/D12) and it is
    retired only by a verified resume.  While it is there the operator has
    said stop, and arming under it is authority the switch was pulled to
    remove."""
    arming = make_arming(tmp_path, release, clock, verifier)
    request = arm_request(release)
    arming.request(request, REQUEST_ID)
    with pytest.raises(ProductionError) as excinfo:
        approve_arm(arming, arm_approval(request), request, sentinel_present=True)
    assert "HALT" in str(excinfo.value)


@pytest.mark.parametrize("rung", ["live_limited", "live"])
def test_no_live_arm_is_issued_under_a_no_go_readiness(tmp_path, release, clock,
                                                       verifier, rung):
    """D10 wants "a release-bound GO".  The verdict is
    `Readiness.verdict_for(view, at_ms)`'s — the one owner of "expired
    means `no_go`" — and `approve` refuses without it rather than letting
    the handler decide what a stale checklist means."""
    obj = document_for(rung)
    arming = make_arming(tmp_path, release, clock, verifier, document=obj)
    request = arm_request(release, rung=rung)
    arming.request(request, REQUEST_ID)
    with pytest.raises(ProductionError) as excinfo:
        approve_arm(arming, arm_approval(request), request, readiness_verdict="no_go")
    assert "readiness" in str(excinfo.value)
    assert "no_go" in str(excinfo.value)


@pytest.mark.parametrize("rung", ["shadow", "paper"])
def test_a_rung_with_no_live_permit_to_gate_arms_without_a_go(tmp_path, release, clock,
                                                              verifier, rung):
    """The GO conjunct rides the same rung-keyed table the conjunction does,
    never a `rung ==` test (D2).  Below `live_limited` no live permit exists
    to gate, and demanding a GO would make the paper rehearsal harder than
    the thing it rehearses."""
    obj = document_for(rung)
    arming = make_arming(tmp_path, release, clock, verifier, document=obj)
    request = arm_request(release, rung=rung)
    arming.request(request, REQUEST_ID)
    _, state = approve_arm(arming, arm_approval(request), request,
                           readiness_verdict="no_go")
    assert state.rung == rung
    assert state.release_hash == release.release_hash


@pytest.mark.parametrize("rung", ["shadow", "paper"])
def test_the_breaker_and_the_sentinel_gate_every_rung(tmp_path, release, clock,
                                                      verifier, rung):
    """Only the GO is rung-dependent.  A halted breaker and a live `HALT`
    file stop a paper arm too — the rehearsal would otherwise practise
    ignoring the kill switch."""
    obj = document_for(rung)
    arming = make_arming(tmp_path, release, clock, verifier, document=obj)
    request = arm_request(release, rung=rung)
    arming.request(request, REQUEST_ID)
    with pytest.raises(ProductionError):
        approve_arm(arming, arm_approval(request), request,
                    view=view_with(("trip", trip_body("halted"))))
    with pytest.raises(ProductionError):
        approve_arm(arming, arm_approval(request), request, sentinel_present=True)


def test_every_failed_world_conjunct_is_named_at_once(tmp_path, release, clock,
                                                      verifier):
    """Accumulate, never stop at the first: an operator clearing one refusal
    at a time would learn about the sentinel only after fixing the breaker."""
    arming = make_arming(tmp_path, release, clock, verifier)
    request = arm_request(release)
    arming.request(request, REQUEST_ID)
    with pytest.raises(ProductionError) as excinfo:
        approve_arm(arming, arm_approval(request), request,
                    view=view_with(("trip", trip_body("halted"))),
                    readiness_verdict="no_go", sentinel_present=True)
    assert len(excinfo.value.problems) >= 3


def test_approve_refuses_a_readiness_verdict_outside_the_vocabulary(tmp_path, release,
                                                                    clock, verifier):
    """The verdict is a `READINESS_VERDICTS` member the caller took from
    `Readiness.verdict_for`; anything else — `True`, `"ok"`, a forgotten
    `None` — is a caller bug, not a GO."""
    arming = make_arming(tmp_path, release, clock, verifier)
    request = arm_request(release)
    arming.request(request, REQUEST_ID)
    for verdict in ("ok", None, True):
        with pytest.raises(ProductionError) as excinfo:
            approve_arm(arming, arm_approval(request), request,
                        readiness_verdict=verdict)
        assert "readiness_verdict" in str(excinfo.value)
    assert set(vocab.READINESS_VERDICTS) == {"go", "no_go"}


def test_approve_refuses_a_sentinel_flag_that_is_not_a_bool(tmp_path, release, clock,
                                                            verifier):
    """A truthy path string would read as "present" and an empty one as
    "absent"; the caller answers the question, it does not hand over its
    working."""
    arming = make_arming(tmp_path, release, clock, verifier)
    request = arm_request(release)
    arming.request(request, REQUEST_ID)
    with pytest.raises(ProductionError) as excinfo:
        approve_arm(arming, arm_approval(request), request, sentinel_present="HALT")
    assert "sentinel_present" in str(excinfo.value)


# ---------------------------------------------------------------------------
# `current` — the fold is the arming (§5.8.1)
# ---------------------------------------------------------------------------


def test_current_reads_the_arm_the_ledger_folded(tmp_path, release, clock, verifier):
    arming = make_arming(tmp_path, release, clock, verifier)
    _, state = issue(arming, release)
    view = view_with(("authority", ordinary_issue_body(state)))
    assert arming.current(view, clock.now_ms()) == state


def test_current_is_none_when_nothing_has_been_armed(tmp_path, release, clock,
                                                     verifier):
    arming = make_arming(tmp_path, release, clock, verifier)
    assert arming.current(view_with(), clock.now_ms()) is None


def test_current_is_none_once_the_arm_has_expired(tmp_path, release, clock, verifier):
    arming = make_arming(tmp_path, release, clock, verifier)
    _, state = issue(arming, release)
    view = view_with(("authority", ordinary_issue_body(state)))
    assert arming.current(view, state.armed_until_ms - 1) is not None
    assert arming.current(view, state.armed_until_ms) is None
    assert arming.current(view, state.armed_until_ms + 1) is None


# ---------------------------------------------------------------------------
# `check_conjunction` — D11's live conjunction, origin-aware (§5.6)
# ---------------------------------------------------------------------------


def armed_invocation(release, armed=True, env=True):
    return FakeInvocation(
        armed=armed,
        env_release_hash=release.release_hash if env else None,
    )


def conjunction(arming, release, view, origin="model", reduction=None,
                rung="live_limited", at_ms=BASE_MS, invocation=None):
    invocation = invocation if invocation is not None else armed_invocation(release)
    return arming.check_conjunction(invocation, view, origin, reduction, rung, at_ms)


def armed_view(arming, release):
    _, state = issue(arming, release)
    return view_with(("authority", ordinary_issue_body(state))), state


def test_the_conjunction_result_is_a_frozen_satisfied_reason_pair():
    assert tuple(f.name for f in dataclasses.fields(ConjunctionResult)) == (
        "satisfied",
        "reason",
    )
    assert ConjunctionResult.__dataclass_params__.frozen is True


@pytest.mark.parametrize("rung", ["shadow", "paper"])
def test_a_rung_with_no_live_permit_to_gate_is_always_satisfied(tmp_path, release,
                                                                clock, verifier, rung):
    obj = document_for(rung)
    arming = make_arming(tmp_path, release, clock, verifier, document=obj)
    result = conjunction(
        arming, release, view_with(), rung=rung,
        invocation=FakeInvocation(armed=False, env_release_hash=None),
    )
    assert result.satisfied is True
    assert result.reason == ""


def test_a_rung_that_disagrees_with_the_document_refuses(tmp_path, release, clock,
                                                         verifier):
    arming = make_arming(tmp_path, release, clock, verifier)
    view, _ = armed_view(arming, release)
    result = conjunction(arming, release, view, rung="live")
    assert result.satisfied is False
    assert result.reason == "rung_mismatch"


def test_serving_live_without_the_armed_flag_refuses(tmp_path, release, clock,
                                                     verifier):
    arming = make_arming(tmp_path, release, clock, verifier)
    view, _ = armed_view(arming, release)
    result = conjunction(arming, release, view,
                         invocation=armed_invocation(release, armed=False))
    assert result.satisfied is False
    assert result.reason == "not_armed_flag"


def test_serving_live_without_the_arm_environment_variable_refuses(tmp_path, release,
                                                                   clock, verifier):
    arming = make_arming(tmp_path, release, clock, verifier)
    view, _ = armed_view(arming, release)
    result = conjunction(arming, release, view,
                         invocation=armed_invocation(release, env=False))
    assert result.satisfied is False
    assert result.reason == "arm_env_missing"


def test_an_arm_environment_variable_naming_another_release_refuses(tmp_path, release,
                                                                    clock, verifier):
    arming = make_arming(tmp_path, release, clock, verifier)
    view, _ = armed_view(arming, release)
    result = conjunction(
        arming, release, view,
        invocation=FakeInvocation(armed=True, env_release_hash="c" * 64),
    )
    assert result.satisfied is False
    assert result.reason == "release_mismatch"


def test_a_model_leg_with_all_five_conjuncts_is_satisfied(tmp_path, release, clock,
                                                          verifier):
    arming = make_arming(tmp_path, release, clock, verifier)
    view, _ = armed_view(arming, release)
    result = conjunction(arming, release, view)
    assert result.satisfied is True
    assert result.reason == ""


def test_a_model_leg_without_an_ordinary_arm_is_not_armed(tmp_path, release, clock,
                                                          verifier):
    arming = make_arming(tmp_path, release, clock, verifier)
    result = conjunction(arming, release, view_with())
    assert result.satisfied is False
    assert result.reason == "not_armed"


def test_a_model_leg_whose_arm_has_expired_is_not_armed(tmp_path, release, clock,
                                                        verifier):
    arming = make_arming(tmp_path, release, clock, verifier)
    view, state = armed_view(arming, release)
    result = conjunction(arming, release, view, at_ms=state.armed_until_ms)
    assert result.satisfied is False
    assert result.reason == "not_armed"


def test_a_model_leg_whose_arm_names_another_release_refuses(tmp_path, release, clock,
                                                             verifier):
    arming = make_arming(tmp_path, release, clock, verifier)
    _, state = issue(arming, release)
    foreign = sample_state(release_hash="c" * 64,
                           armed_until_ms=state.armed_until_ms)
    view = view_with(("authority", ordinary_issue_body(foreign)))
    result = conjunction(arming, release, view)
    assert result.satisfied is False
    assert result.reason == "release_mismatch"


def reduction_view(release, digests, reserved=(), expires_ms=BASE_MS + 600_000,
                   release_hash=None):
    authorization = ReductionAuthorization(
        authority_id="auth-red-1",
        release_hash=release_hash or release.release_hash,
        request_id="req-flatten-1",
        reduction_intent_digests=tuple(digests),
        expires_ms=expires_ms,
    )
    records = [("authority", reduction_issue_body(authorization))]
    for digest in reserved:
        records.append(("authority_use", authority_use_body(authorization, digest)))
    return view_with(*records), authorization


def test_a_reduction_leg_needs_its_own_right_and_no_ordinary_arm(tmp_path, release,
                                                                 clock, verifier):
    """D11/D12: demanding an ordinary arm while `reducing` would refuse
    every live flatten leg, since leaving `active` revoked it."""
    arming = make_arming(tmp_path, release, clock, verifier)
    signed = reduction_intent(release)
    digest = signed.reduction_intent_digest()
    view, _ = reduction_view(release, [digest])
    assert view.arming is None
    result = conjunction(arming, release, view, origin="reduction",
                         reduction=FakeReduction(signed=signed, digest=digest))
    assert result.satisfied is True
    assert result.reason == ""


def test_a_reduction_leg_whose_right_is_already_reserved_refuses(tmp_path, release,
                                                                 clock, verifier):
    arming = make_arming(tmp_path, release, clock, verifier)
    signed = reduction_intent(release)
    digest = signed.reduction_intent_digest()
    view, _ = reduction_view(release, [digest], reserved=[digest])
    result = conjunction(arming, release, view, origin="reduction",
                         reduction=FakeReduction(signed=signed, digest=digest))
    assert result.satisfied is False
    assert result.reason == "reduction_right_consumed"


def test_a_reduction_leg_naming_a_digest_no_right_covers_refuses(tmp_path, release,
                                                                 clock, verifier):
    """"the specific right, which is why the digest is an argument and
    not left as 'some right is unconsumed'"."""
    arming = make_arming(tmp_path, release, clock, verifier)
    granted = reduction_intent(release, 0, "AAPL")
    other = reduction_intent(release, 1, "MSFT")
    view, _ = reduction_view(release, [granted.reduction_intent_digest()])
    result = conjunction(
        arming, release, view, origin="reduction",
        reduction=FakeReduction(signed=other,
                                digest=other.reduction_intent_digest()),
    )
    assert result.satisfied is False
    assert result.reason == "reduction_right_unknown"


def test_a_reduction_leg_after_the_authority_expired_refuses(tmp_path, release, clock,
                                                             verifier):
    arming = make_arming(tmp_path, release, clock, verifier)
    signed = reduction_intent(release)
    digest = signed.reduction_intent_digest()
    view, authorization = reduction_view(release, [digest])
    result = conjunction(arming, release, view, origin="reduction",
                         reduction=FakeReduction(signed=signed, digest=digest),
                         at_ms=authorization.expires_ms)
    assert result.satisfied is False
    assert result.reason == "reduction_authority_expired"


def test_a_reduction_leg_with_no_reduction_authority_at_all_refuses(tmp_path, release,
                                                                    clock, verifier):
    arming = make_arming(tmp_path, release, clock, verifier)
    view, _ = armed_view(arming, release)
    signed = reduction_intent(release)
    result = conjunction(
        arming, release, view, origin="reduction",
        reduction=FakeReduction(signed=signed,
                                digest=signed.reduction_intent_digest()),
    )
    assert result.satisfied is False
    assert result.reason == "no_reduction_authority"


def test_a_reduction_right_granted_under_another_release_refuses(tmp_path, release,
                                                                 clock, verifier):
    """R24: rights never survive a re-plan.  A right is granted for a
    `reduction_intent_digest` computed from one release's artifacts, and
    honouring it under another would consume authority for a plan this
    release never made — the digests would not even be comparable."""
    arming = make_arming(tmp_path, release, clock, verifier)
    signed = reduction_intent(release)
    digest = signed.reduction_intent_digest()
    view, _ = reduction_view(release, [digest], release_hash="c" * 64)
    assert view.reduction.release_hash == "c" * 64
    result = conjunction(arming, release, view, origin="reduction",
                         reduction=FakeReduction(signed=signed, digest=digest))
    assert result.satisfied is False
    assert result.reason == "reduction_release_mismatch"
    assert result.reason in CONJUNCTION_REASONS


def test_both_origins_refuse_an_authority_bound_to_another_release(tmp_path, release,
                                                                   clock, verifier):
    """The two conjuncts are symmetric: each compares the release the
    authority was issued under with THIS release's hash, so a re-plan
    cannot leave one of the two origins open."""
    arming = make_arming(tmp_path, release, clock, verifier)
    foreign = sample_state(release_hash="c" * 64)
    model = conjunction(arming, release, view_with(("authority",
                                                    ordinary_issue_body(foreign))))
    signed = reduction_intent(release)
    digest = signed.reduction_intent_digest()
    view, _ = reduction_view(release, [digest], release_hash="c" * 64)
    reduction = conjunction(arming, release, view, origin="reduction",
                            reduction=FakeReduction(signed=signed, digest=digest))
    assert (model.satisfied, reduction.satisfied) == (False, False)
    assert {model.reason, reduction.reason} <= set(CONJUNCTION_REASONS)
    assert model.reason != reduction.reason


def test_an_ordinary_arm_never_satisfies_a_reduction_leg(tmp_path, release, clock,
                                                         verifier):
    arming = make_arming(tmp_path, release, clock, verifier)
    view, _ = armed_view(arming, release)
    signed = reduction_intent(release)
    result = conjunction(
        arming, release, view, origin="reduction",
        reduction=FakeReduction(signed=signed,
                                digest=signed.reduction_intent_digest()),
    )
    assert result.satisfied is False


def test_a_reduction_leg_whose_digest_is_not_its_signed_intents_refuses(
    tmp_path, release, clock, verifier
):
    """The right is granted for a DIGEST; the leg submits an INTENT.  If
    the digest is taken on the leg's word the two are never tied together,
    and a granted right would cover whatever intent came with it."""
    arming = make_arming(tmp_path, release, clock, verifier)
    granted = reduction_intent(release, 0, "AAPL")
    other = reduction_intent(release, 1, "MSFT")
    view, _ = reduction_view(release, [granted.reduction_intent_digest()])
    with pytest.raises(ProductionError) as excinfo:
        conjunction(
            arming, release, view, origin="reduction",
            reduction=FakeReduction(signed=other,
                                    digest=granted.reduction_intent_digest()),
        )
    assert "reduction.digest" in str(excinfo.value)


@pytest.mark.parametrize("signed", [None, "an intent", 7])
def test_a_reduction_leg_without_its_signed_intent_refuses(tmp_path, release, clock,
                                                           verifier, signed):
    """§5.16: a reduction leg carries "the signed `ReductionIntent` +
    digest + right".  Without the intent there is nothing to recompute
    the digest from, so the digest would be an assertion, not a binding."""
    arming = make_arming(tmp_path, release, clock, verifier)
    intent = reduction_intent(release)
    view, _ = reduction_view(release, [intent.reduction_intent_digest()])
    with pytest.raises(ProductionError) as excinfo:
        conjunction(
            arming, release, view, origin="reduction",
            reduction=FakeReduction(signed=signed,
                                    digest=intent.reduction_intent_digest()),
        )
    assert "reduction.signed" in str(excinfo.value)


def test_a_reduction_leg_with_no_reduction_binding_refuses(tmp_path, release, clock,
                                                           verifier):
    arming = make_arming(tmp_path, release, clock, verifier)
    view, _ = reduction_view(release, [reduction_intent(release)
                                       .reduction_intent_digest()])
    with pytest.raises(ProductionError):
        conjunction(arming, release, view, origin="reduction", reduction=None)


@pytest.mark.parametrize("origin", ["", None, "operator", "flatten"])
def test_an_origin_outside_the_vocabulary_refuses(tmp_path, release, clock, verifier,
                                                  origin):
    arming = make_arming(tmp_path, release, clock, verifier)
    view, _ = armed_view(arming, release)
    with pytest.raises(ProductionError):
        conjunction(arming, release, view, origin=origin)


# ---------------------------------------------------------------------------
# `apply_scope` — the exact final proposal against the current scope (§5.5)
# ---------------------------------------------------------------------------


def test_an_instrument_in_the_allowlist_is_in_scope(tmp_path, release, clock, verifier):
    arming = make_arming(tmp_path, release, clock, verifier)
    _, state = issue(arming, release)
    verdict = arming.apply_scope(proposal("AAPL"), state)
    assert verdict.allowed is True
    assert verdict.scope_key == "AAPL"
    assert verdict.reason == ""


def test_an_instrument_outside_the_allowlist_is_refused(tmp_path, release, clock,
                                                        verifier):
    arming = make_arming(tmp_path, release, clock, verifier)
    _, state = issue(arming, release)
    verdict = arming.apply_scope(proposal("MSFT"), state)
    assert verdict.allowed is False
    assert verdict.scope_key == "MSFT"
    assert verdict.reason == "instrument_not_allowlisted"


def test_no_arm_means_no_scope(tmp_path, release, clock, verifier):
    arming = make_arming(tmp_path, release, clock, verifier)
    verdict = arming.apply_scope(proposal("AAPL"), None)
    assert verdict.allowed is False
    assert verdict.reason == "not_armed"


def test_an_overlay_bound_is_applied_to_the_exact_proposal(tmp_path, release, clock,
                                                           verifier):
    """§5.5: the allowlist and overlay are re-applied "against the exact
    final proposal immediately before permit"."""
    arming = make_arming(tmp_path, release, clock, verifier)
    _, tight = issue(arming, release, limits_overlay={"size": {"max": "5"}})
    refused = arming.apply_scope(proposal("AAPL", qty="10"), tight)
    assert refused.allowed is False
    assert "size" in refused.reason
    assert arming.apply_scope(proposal("AAPL", qty="5"), tight).allowed is True


def test_the_same_proposal_passes_the_documents_own_bound_without_an_overlay(
    tmp_path, release, clock, verifier
):
    arming = make_arming(tmp_path, release, clock, verifier)
    _, state = issue(arming, release)
    assert arming.apply_scope(proposal("AAPL", qty="10"), state).allowed is True


def test_effective_bounds_tighten_only_the_guards_the_overlay_names(tmp_path, release,
                                                                    clock, verifier):
    arming = make_arming(tmp_path, release, clock, verifier)
    _, state = issue(arming, release, limits_overlay={"size": {"max": "50"}})
    bounds = arming.effective_bounds(state)
    assert bounds["size"]["max"] == Decimal("50")
    assert bounds["exposure"]["max"] == Decimal("20000")
    assert bounds["day_loss"]["min"] == Decimal("-500")
    assert "sane" not in bounds


def test_effective_bounds_without_an_arm_are_the_documents_own(tmp_path, release,
                                                               clock, verifier):
    arming = make_arming(tmp_path, release, clock, verifier)
    assert arming.effective_bounds(None)["size"]["max"] == Decimal("100")


@pytest.mark.parametrize(
    "overlay, guard, key, kept",
    [
        ({"size": {"max": "500"}}, "size", "max", Decimal("100")),
        ({"day_loss": {"min": "-5000"}}, "day_loss", "min", Decimal("-500")),
    ],
)
def test_a_folded_overlay_can_never_loosen_a_document_bound(
    tmp_path, release, clock, verifier, overlay, guard, key, kept
):
    """D11: "every limit overlay must prove it is at least as strict as
    the document".  `request` refuses a looser one, but `effective_bounds`
    reads the FOLD — an arm issued under a since-tightened document, or a
    forged `arming.json`, is the case where the strictness rule has to
    hold a second time.  The document's own bound is the floor."""
    arming = make_arming(tmp_path, release, clock, verifier)
    loose = sample_state(release_hash=release.release_hash, limits_overlay=overlay)
    assert arming.effective_bounds(loose)[guard][key] == kept


def test_a_folded_overlay_that_is_stricter_still_applies(tmp_path, release, clock,
                                                         verifier):
    arming = make_arming(tmp_path, release, clock, verifier)
    tight = sample_state(release_hash=release.release_hash,
                         limits_overlay={"size": {"max": "5"}})
    assert arming.effective_bounds(tight)["size"]["max"] == Decimal("5")


# ---------------------------------------------------------------------------
# Reduction rights (D12) — one right per digest, single use, never erased
# ---------------------------------------------------------------------------


def test_a_plan_grants_one_right_per_unique_intent_digest(release):
    plan = reduction_plan(release)
    authorization = ReductionRights.from_plan(
        plan, checker_principal(), "auth-red-1", BASE_MS + 300_000
    )
    assert isinstance(authorization, ReductionAuthorization)
    assert authorization.authority_id == "auth-red-1"
    assert authorization.release_hash == release.release_hash
    assert authorization.request_id == "req-flatten-1"
    assert tuple(authorization.reduction_intent_digests) == tuple(
        plan.reduction_intent_digests
    )
    assert authorization.expires_ms == BASE_MS + 300_000


@pytest.mark.parametrize("approval", [None, CHECKER, {"id": CHECKER}])
def test_a_plan_approved_by_nothing_verified_refuses(release, approval):
    with pytest.raises(ProductionError):
        ReductionRights.from_plan(reduction_plan(release), approval, "auth-red-1",
                                  BASE_MS + 300_000)


def test_a_plan_whose_entries_are_out_of_maker_approved_index_order_refuses(release):
    """D12: `execute-flatten` processes the stored intents "in
    maker-approved `index` order", and "execution stops on the first
    refusal" means that order — so a plan whose indices are not 0..n-1
    in sequence has no order to stop in."""
    intents = (reduction_intent(release, 1, "MSFT"), reduction_intent(release, 0, "AAPL"))
    with pytest.raises(ProductionError):
        ReductionRights.from_plan(reduction_plan(release, intents), checker_principal(),
                                  "auth-red-1", BASE_MS + 300_000)


def test_a_plan_carrying_an_intent_bound_to_another_release_refuses(release):
    intents = (reduction_intent(release, 0, "AAPL", release_hash="c" * 64),)
    with pytest.raises(ProductionError):
        ReductionRights.from_plan(reduction_plan(release, intents), checker_principal(),
                                  "auth-red-1", BASE_MS + 300_000)


def test_two_entries_with_the_same_order_content_refuse(release):
    """D12: "two entries whose `(instrument, side, qty, limit)` match
    refuse — the check is over proposal content, not over
    `reduction_intent_digest`, which now includes `index` and so differs
    for byte-identical proposals"."""
    intents = (reduction_intent(release, 0, "AAPL"), reduction_intent(release, 1, "AAPL"))
    assert intents[0].reduction_intent_digest() != intents[1].reduction_intent_digest()
    with pytest.raises(ProductionError):
        ReductionRights.from_plan(reduction_plan(release, intents), checker_principal(),
                                  "auth-red-1", BASE_MS + 300_000)


def test_two_entries_that_differ_only_in_quantity_are_both_authorised(release):
    intents = (reduction_intent(release, 0, "AAPL", qty="10"),
               reduction_intent(release, 1, "AAPL", qty="4"))
    authorization = ReductionRights.from_plan(
        reduction_plan(release, intents), checker_principal(), "auth-red-1",
        BASE_MS + 300_000
    )
    assert len(authorization.reduction_intent_digests) == 2


def test_a_plan_with_no_intents_authorises_nothing_and_refuses(release):
    plan = ReductionPlan(
        release_hash=release.release_hash,
        risk_state_digest=DIGEST_RISK,
        intents=(),
        reduction_intent_digests=(),
        expires_ms=BASE_MS + 600_000,
    )
    with pytest.raises(ProductionError):
        ReductionRights.from_plan(plan, checker_principal(), "auth-red-1",
                                  BASE_MS + 300_000)


def test_a_plan_whose_intents_name_different_requests_refuses(release):
    intents = (
        reduction_intent(release, 0, "AAPL", request_id="req-flatten-1"),
        reduction_intent(release, 1, "MSFT", request_id="req-flatten-2"),
    )
    with pytest.raises(ProductionError):
        ReductionRights.from_plan(reduction_plan(release, intents),
                                  checker_principal(), "auth-red-1", BASE_MS + 300_000)


def test_an_authorization_may_never_outlive_the_plan_it_rests_on(release):
    plan = reduction_plan(release)
    with pytest.raises(ProductionError):
        ReductionRights.from_plan(plan, checker_principal(), "auth-red-1",
                                  plan.expires_ms + 1)


def test_a_reservation_is_the_four_field_authority_use_body(release, clock):
    signed = reduction_intent(release)
    digest = signed.reduction_intent_digest()
    view, _ = reduction_view(release, [digest])
    body = ReductionRights(clock=clock).reserve(view, digest, "flat-ref-1")
    assert set(body) == {"authority_id", "reduction_intent_digest", "client_ref",
                         "reserved_at_ms"}
    assert body["authority_id"] == "auth-red-1"
    assert body["reduction_intent_digest"] == digest
    assert body["client_ref"] == "flat-ref-1"
    assert body["reserved_at_ms"] == clock.now_ms()


def test_a_right_already_reserved_can_never_be_reserved_again(release, clock):
    """D12: "that reservation is never erased or reused"."""
    signed = reduction_intent(release)
    digest = signed.reduction_intent_digest()
    view, _ = reduction_view(release, [digest], reserved=[digest])
    with pytest.raises(ProductionError) as excinfo:
        ReductionRights(clock=clock).reserve(view, digest, "flat-ref-1")
    assert digest in str(excinfo.value)


def test_reserving_a_digest_no_right_covers_refuses(release, clock):
    granted = reduction_intent(release, 0, "AAPL")
    other = reduction_intent(release, 1, "MSFT")
    view, _ = reduction_view(release, [granted.reduction_intent_digest()])
    with pytest.raises(ProductionError):
        ReductionRights(clock=clock).reserve(
            view, other.reduction_intent_digest(), "flat-ref-1"
        )


def test_reserving_after_the_authority_expired_refuses(release):
    clock = FakeClock()
    signed = reduction_intent(release)
    digest = signed.reduction_intent_digest()
    view, authorization = reduction_view(release, [digest])
    clock.advance(authorization.expires_ms - clock.now_ms())
    with pytest.raises(ProductionError):
        ReductionRights(clock=clock).reserve(view, digest, "flat-ref-1")


def test_reserving_without_a_reduction_authority_refuses(release, clock):
    with pytest.raises(ProductionError):
        ReductionRights(clock=clock).reserve(view_with(), "a" * 64, "flat-ref-1")


def test_unused_rights_are_revoked_as_an_authority_record(release, clock):
    """D12: "the writer revokes all unused rights after any partial
    result, requiring a new plan"."""
    signed = reduction_intent(release)
    digest = signed.reduction_intent_digest()
    view, _ = reduction_view(release, [digest])
    body = ReductionRights(clock=clock).revoke_unused(view, "partial_result")
    assert body["event"] == "revoke"
    assert body["role"] == "reduction"
    assert body["authority_id"] == "auth-red-1"
    assert body["reason"] == "partial_result"
    assert body["event"] in vocab.AUTHORITY_EVENTS


def test_revoking_when_nothing_is_authorised_refuses(release, clock):
    with pytest.raises(ProductionError):
        ReductionRights(clock=clock).revoke_unused(view_with(), "partial_result")


# ---------------------------------------------------------------------------
# `disarm`, `revoke`, `expire_if_due` — the other three `AUTHORITY_EVENTS`
# ---------------------------------------------------------------------------


def test_disarm_records_the_safe_demotion(tmp_path, release, clock, verifier):
    arming = make_arming(tmp_path, release, clock, verifier)
    _, state = issue(arming, release)
    view = view_with(("authority", ordinary_issue_body(state)))
    body = arming.disarm(view)
    assert body["event"] == "disarm"
    assert body["role"] == "ordinary"
    assert body["authority_id"] == state.authority_id


def test_revoke_records_the_reason_it_was_given(tmp_path, release, clock, verifier):
    arming = make_arming(tmp_path, release, clock, verifier)
    _, state = issue(arming, release)
    view = view_with(("authority", ordinary_issue_body(state)))
    body = arming.revoke(view, "left_active")
    assert body["event"] == "revoke"
    assert body["reason"] == "left_active"


def test_expire_if_due_says_nothing_until_the_deadline(tmp_path, release, clock,
                                                       verifier):
    arming = make_arming(tmp_path, release, clock, verifier)
    _, state = issue(arming, release)
    view = view_with(("authority", ordinary_issue_body(state)))
    assert arming.expire_if_due(view, state.armed_until_ms - 1) is None
    body = arming.expire_if_due(view, state.armed_until_ms)
    assert body["event"] == "expire"
    assert body["authority_id"] == state.authority_id


@pytest.mark.parametrize("verb", ["disarm", "revoke", "expire_if_due"])
def test_the_ordinary_authority_verbs_refuse_when_nothing_is_armed(tmp_path, release,
                                                                   clock, verifier,
                                                                   verb):
    arming = make_arming(tmp_path, release, clock, verifier)
    view = view_with()
    if verb == "disarm":
        with pytest.raises(ProductionError):
            arming.disarm(view)
    elif verb == "revoke":
        with pytest.raises(ProductionError):
            arming.revoke(view, "left_active")
    else:
        assert arming.expire_if_due(view, BASE_MS) is None


def test_every_authority_event_the_arming_service_emits_is_in_the_vocabulary(
    tmp_path, release, clock, verifier
):
    arming = make_arming(tmp_path, release, clock, verifier)
    body, state = issue(arming, release)
    view = view_with(("authority", ordinary_issue_body(state)))
    emitted = [
        body["event"],
        arming.disarm(view)["event"],
        arming.revoke(view, "left_active")["event"],
        arming.expire_if_due(view, state.armed_until_ms)["event"],
    ]
    assert sorted(emitted) == sorted(vocab.AUTHORITY_EVENTS)


# ---------------------------------------------------------------------------
# `arming.json` — a head-bound cache of the fold, nothing more (D15)
# ---------------------------------------------------------------------------


def cache_of(arming):
    with open(arming.cache_path, encoding="utf-8") as handle:
        return json.load(handle)


def forge_cache(service, **overrides):
    """Rewrite the cache file with the given keys replaced.

    The first parameter is not called ``arming`` because ``arming`` is
    itself one of the cache keys a test overrides (``arming=None``).
    """
    cached = cache_of(service)
    cached.update(overrides)
    with open(service.cache_path, "w", encoding="utf-8") as handle:
        json.dump(cached, handle)


def folded(arming, release):
    """A `RecordLedger` holding one issued arm, and that `ArmingState`."""
    _, state = issue(arming, release)
    return ledger_with(("authority", ordinary_issue_body(state))), state


def test_the_cache_path_is_the_serve_roots_arming_json(tmp_path, release, clock,
                                                       verifier):
    arming = make_arming(tmp_path, release, clock, verifier)
    assert arming.cache_path == ServeRoot(
        str(tmp_path / "serve"), SERIES_ID
    ).arming_cache


def test_write_cache_records_the_arm_and_the_head_it_projects(tmp_path, release, clock,
                                                              verifier):
    arming = make_arming(tmp_path, release, clock, verifier)
    led, state = folded(arming, release)
    arming.write_cache(led.view())
    cached = cache_of(arming)
    assert set(cached) == {"arming", "head_seq", "head_hash"}
    assert cached["arming"] == state.to_obj()
    assert (cached["head_seq"], cached["head_hash"]) == led.head()


def test_an_unarmed_cache_records_no_arm_rather_than_omitting_the_key(tmp_path, release,
                                                                      clock, verifier):
    arming = make_arming(tmp_path, release, clock, verifier)
    arming.write_cache(ledger_with().view())
    assert cache_of(arming)["arming"] is None


def test_a_cache_at_the_head_is_used_as_it_stands(tmp_path, release, clock, verifier):
    arming = make_arming(tmp_path, release, clock, verifier)
    led, state = folded(arming, release)
    arming.write_cache(led.view())
    assert arming.load_cache(led, led.view()) == state


def test_a_cache_behind_the_head_is_rebuilt_and_rewritten(tmp_path, release, clock,
                                                          verifier):
    arming = make_arming(tmp_path, release, clock, verifier)
    arming.write_cache(ledger_with().view())
    led, state = folded(arming, release)
    assert arming.load_cache(led, led.view()) == state
    assert cache_of(arming)["arming"] == state.to_obj()
    assert (cache_of(arming)["head_seq"], cache_of(arming)["head_hash"]) == led.head()


def test_an_absent_cache_is_rebuilt_from_the_fold(tmp_path, release, clock, verifier):
    arming = make_arming(tmp_path, release, clock, verifier)
    led, state = folded(arming, release)
    assert not os.path.exists(arming.cache_path)
    assert arming.load_cache(led, led.view()) == state
    assert os.path.exists(arming.cache_path)


def test_the_head_check_is_ledgers_and_arming_never_respells_it(
    tmp_path, release, clock, verifier, monkeypatch
):
    """`ledger.validate_cache_head` is the one owner of the at/behind/
    off-the-chain rule; `arming.json` and `breaker.json` both call it."""
    arming = make_arming(tmp_path, release, clock, verifier)
    led, state = folded(arming, release)
    arming.write_cache(led.view())
    calls = []

    def recorded(head_seq, head_hash, ledger):
        calls.append((head_seq, head_hash, ledger))
        return "current"

    monkeypatch.setattr(arming_module, "validate_cache_head", recorded)
    assert arming.load_cache(led, led.view()) == state
    assert calls == [(led.head()[0], led.head()[1], led)]


def test_a_cache_the_head_check_calls_stale_is_rebuilt(tmp_path, release, clock,
                                                       verifier, monkeypatch):
    arming = make_arming(tmp_path, release, clock, verifier)
    arming.write_cache(ledger_with().view())
    led, state = folded(arming, release)
    monkeypatch.setattr(arming_module, "validate_cache_head",
                        lambda head_seq, head_hash, ledger: "stale")
    assert arming.load_cache(led, led.view()) == state
    assert cache_of(arming)["arming"] == state.to_obj()


def test_a_cache_ahead_of_the_ledger_refuses(tmp_path, release, clock, verifier):
    arming = make_arming(tmp_path, release, clock, verifier)
    led, _ = folded(arming, release)
    arming.write_cache(led.view())
    forge_cache(arming, head_seq=cache_of(arming)["head_seq"] + 5)
    with pytest.raises(ProductionError):
        arming.load_cache(led, led.view())


def test_a_cache_that_diverges_at_its_own_seq_refuses(tmp_path, release, clock,
                                                      verifier):
    arming = make_arming(tmp_path, release, clock, verifier)
    led, _ = folded(arming, release)
    arming.write_cache(led.view())
    forge_cache(arming, head_hash="f" * 64)
    with pytest.raises(ProductionError):
        arming.load_cache(led, led.view())


def test_a_current_cache_that_disagrees_with_the_fold_refuses(tmp_path, release, clock,
                                                              verifier):
    """A head-bound file claiming an arm the fold does not carry — or
    dropping one it does — is the forgery a head check alone waves
    through, which is why §5.6 validates the cache *against the fold*."""
    arming = make_arming(tmp_path, release, clock, verifier)
    led, _ = folded(arming, release)
    arming.write_cache(led.view())
    forge_cache(arming, arming=None)
    with pytest.raises(ProductionError):
        arming.load_cache(led, led.view())


@pytest.mark.parametrize("text", ["", "{", "[]", '{"arming": null}'])
def test_an_unreadable_cache_refuses_rather_than_reporting_unarmed(tmp_path, release,
                                                                   clock, verifier,
                                                                   text):
    arming = make_arming(tmp_path, release, clock, verifier)
    led, _ = folded(arming, release)
    with open(arming.cache_path, "w", encoding="utf-8") as handle:
        handle.write(text)
    with pytest.raises(ProductionError):
        arming.load_cache(led, led.view())


def test_a_cache_write_leaves_no_temp_file_behind(tmp_path, release, clock, verifier):
    arming = make_arming(tmp_path, release, clock, verifier)
    arming.write_cache(ledger_with().view())
    names = os.listdir(os.path.dirname(arming.cache_path))
    assert "arming.json" in names
    assert [name for name in names if name.startswith("arming.json.")] == []
