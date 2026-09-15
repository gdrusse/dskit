"""Packet 4 capability and refusal boundaries from the approved Matrix v7.

These initial RED cases cover the fixed factory, unresolvable admissions, and
preservation of ordinary lifecycle behavior. They do not stand in for the
required complete signed action/replay, transaction, or concurrency matrix.
"""

import copy
import hashlib
import inspect
import json
import pickle

import pytest

from dskit.pipeline import trust
from tests.pipeline import test_trust as f4


def _terminal_type(name):
    """Fail at an executable capability assertion, never during collection."""
    value = getattr(trust, name, None)
    assert value is not None, f"Matrix v8 {name} capability is missing"
    return value


def _terminal_fact(terminal_class):
    """Return hostile external bytes, not a caller-issued proof or schema."""
    raw = json.dumps(f"nondeployment {terminal_class} fixture").encode("ascii")
    return {
        "ref": {
            "kind": "external-authorization",
            "role": terminal_class,
            "schema": "urn:dskit:synthetic-external:" + terminal_class,
            "sha256": hashlib.sha256(raw).hexdigest(),
        },
        "bytes": raw,
    }


def _local_signed(payload, self_field, role, usage):
    """Sign exact test bytes independently with the fixed nondeployment key."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    value = dict(payload)
    value.update(
        issuer_role=role, key_usage=usage, signature_alg="Ed25519",
        issued_at_ms=100, not_before_ms=0, expires_at_ms=1000,
        revocation_snapshot_sha256=hashlib.sha256(b"p4-fixed-revocations").hexdigest(),
        key={"key_id": role + "/" + usage, "key_version": 1},
    )
    preimage = f4._json_bytes(value)
    value[self_field] = hashlib.sha256(preimage).hexdigest()
    seed = hashlib.sha256(("p4-fixed-test-key/" + role + "/" + usage).encode()).digest()
    value["signature"] = Ed25519PrivateKey.from_private_bytes(seed).sign(preimage).hex()
    return value


def _terminal_parent(terminal_class):
    """Build a real signed local basis around exact external fixture refs."""
    if terminal_class == "fixed-owner-policy":
        kind, usage = "scope-intent", "historical-study-scope-intent"
        refs = [_terminal_fact(terminal_class)["ref"], {
            "kind": "root-pis", "role": "data-publisher",
            "schema": "dskit.published-input-set/v2", "sha256": "b" * 64,
        }]
        role = "study-lifecycle"
    else:
        kind, usage, role = "root-pis", "published-input-set-g1-g2", "data-publisher"
        refs = [_terminal_fact(name)["ref"] for name in (
            "G1-dataset-authorization", "G2-dataset-authorization",
        )]
        refs.append({
            "kind": "root-publication", "role": "data-publisher",
            "schema": "dskit.root-publication-receipt/v1", "sha256": "c" * 64,
        })
    refs.sort(key=lambda ref: tuple(ref[name] for name in ("kind", "role", "schema", "sha256")))
    basis = _local_signed({
        "schema": "dskit.issuance-basis/v1", "kind": kind,
        "study_id": "synthetic-study", "refs": refs,
    }, "issuance_basis_sha256", role, usage)
    return basis


def _terminal_setup(terminal_class):
    _terminal_type("TerminalArtifactVerifier")
    fact = _terminal_fact(terminal_class)
    parent = _terminal_parent(terminal_class)
    parent_fact = {
        "ref": {
            "kind": "issuance-basis", "role": parent["issuer_role"],
            "schema": parent["schema"], "sha256": parent["issuance_basis_sha256"],
        }, "bytes": f4._json_bytes(parent),
    }
    broker = _factory()(fixture_facts={"artifacts": [fact, parent_fact]})
    resolver = broker._p4_resolver
    args = (
        resolver.snapshot(), f4._json_bytes(parent), terminal_class,
        f4._json_bytes(fact["ref"]), fact["bytes"],
    )
    return broker, resolver, args


@pytest.mark.parametrize("terminal_class", [
    "G1-dataset-authorization", "G2-dataset-authorization", "fixed-owner-policy",
])
def test_exact_terminal_proof_is_opaque_registered_parent_and_snapshot_bound(terminal_class):
    broker, resolver, args = _terminal_setup(terminal_class)
    anchor = resolver._terminal.verify_terminal(*args)
    assert type(anchor) is trust.VerifiedExternalArtifactAnchor
    assert resolver._terminal.require_current(args[0], (anchor,)) is None
    for operation in (copy.copy, copy.deepcopy, pickle.dumps, dict):
        with pytest.raises(TypeError):
            operation(anchor)
    with pytest.raises(TypeError):
        type(anchor)(anchor)
    with pytest.raises(TypeError):
        type("AnchorSubclass", (type(anchor),), {})
    foreign = _factory()()._p4_resolver
    with pytest.raises((TypeError, ValueError)):
        foreign._terminal.require_current(foreign.snapshot(), (anchor,))
    assert not broker._session_events and not broker._member_events


@pytest.mark.parametrize("terminal_class", [
    "G1-dataset-authorization", "G2-dataset-authorization", "fixed-owner-policy",
])
@pytest.mark.parametrize("change", [
    "bytes", "ref-digest", "ref-kind", "ref-role", "ref-schema", "class",
    "parent-kind", "parent-ref", "parent-signature", "foreign-snapshot",
    "expiry", "issued", "key-version", "key-use", "owner", "revocations",
])
def test_terminal_proof_refuses_each_independent_authentication_mutation(terminal_class, change):
    broker, resolver, original = _terminal_setup(terminal_class)
    args = list(original)
    if change == "bytes":
        args[4] = b'"substituted external object"'
    elif change.startswith("ref-"):
        ref = json.loads(args[3])
        name = change[4:]
        ref["sha256" if name == "digest" else name] = "f" * 64
        args[3] = f4._json_bytes(ref)
    elif change == "class":
        args[2] = "arbitrary-schema-terminal"
    elif change == "foreign-snapshot":
        args[0] = _factory()()._p4_resolver.snapshot()
    else:
        parent = json.loads(args[1])
        if change == "parent-kind":
            parent["kind"] = "cas"
        elif change == "parent-ref":
            parent["refs"] = []
        elif change == "parent-signature":
            parent["signature"] = "ab" * 64
        elif change == "expiry":
            parent["expires_at_ms"] = 1
        elif change == "issued":
            parent["issued_at_ms"] = 1001
        elif change == "key-version":
            parent["key"]["key_version"] = 2
        elif change == "key-use":
            parent["key_usage"] = "captured-authorization"
        elif change == "owner":
            parent["issuer_role"] = "untrusted-owner"
        elif change == "revocations":
            parent["revocation_snapshot_sha256"] = "e" * 64
        args[1] = f4._json_bytes(parent)
    with pytest.raises((TypeError, ValueError)):
        resolver._terminal.verify_terminal(*args)
    assert not broker._session_events and not broker._member_events


@pytest.mark.parametrize("name", [
    "TerminalArtifactVerifier", "VerifiedExternalArtifactAnchor",
])
def test_terminal_capability_types_are_public_nonconstructible_and_nonserializable(name):
    value = _terminal_type(name)
    assert name in trust.__all__
    with pytest.raises(TypeError):
        value()
    with pytest.raises(TypeError):
        value({"verified": True})


def test_terminal_verifier_has_only_the_reviewed_verification_methods():
    value = _terminal_type("TerminalArtifactVerifier")
    assert value.__abstractmethods__ == {"verify_terminal", "require_current"}
    assert tuple(inspect.signature(value.verify_terminal).parameters) == (
        "self", "snapshot", "parent_basis_bytes", "terminal_class",
        "terminal_ref_bytes", "canonical_artifact_bytes",
    )
    assert tuple(inspect.signature(value.require_current).parameters) == (
        "self", "snapshot", "anchors",
    )
    assert not issubclass(value, trust.LifecycleAuthority)


@pytest.mark.parametrize("terminal_class", [
    "G1-dataset-authorization", "G2-dataset-authorization", "fixed-owner-policy",
])
def test_external_facts_remain_opaque_data_in_one_frozen_snapshot(terminal_class):
    _terminal_type("TerminalArtifactVerifier")
    fact = _terminal_fact(terminal_class)
    broker = _factory()(fixture_facts={"artifacts": [fact]})
    resolver = broker._p4_resolver
    snapshot = resolver.snapshot()
    assert resolver.snapshot() is snapshot
    assert resolver.resolve(snapshot, f4._json_bytes(fact["ref"])) == fact["bytes"]
    fact["ref"]["sha256"] = "f" * 64
    fact["bytes"] = b'"changed after construction"'
    original = _terminal_fact(terminal_class)
    assert resolver.resolve(snapshot, f4._json_bytes(original["ref"])) == original["bytes"]
    for operation in (copy.copy, copy.deepcopy, pickle.dumps, dict):
        with pytest.raises(TypeError):
            operation(snapshot)
        with pytest.raises(TypeError):
            operation(resolver._terminal)
    assert isinstance(resolver._terminal, trust.TerminalArtifactVerifier)
    assert not isinstance(resolver._terminal, trust.LifecycleAuthority)
    assert not broker._session_events and not broker._member_events


@pytest.mark.parametrize("injection", [
    "terminal_verifier", "terminal_policy", "anchor", "anchors", "snapshot",
    "generation", "verified", "trusted", "url", "path", "import_path",
])
def test_terminal_trust_cannot_be_selected_by_fixture_factory_or_request(injection):
    _terminal_type("TerminalArtifactVerifier")
    fact = _terminal_fact("G1-dataset-authorization")
    with pytest.raises((TypeError, ValueError)):
        _factory()(fixture_facts={"artifacts": [fact], injection: "caller"})
    with pytest.raises(TypeError):
        _factory()(**{injection: object()})
    broker = _factory()()
    with pytest.raises(TypeError):
        broker.authorize_capture_set((), _request(), **_runtime(), **{injection: object()})
    assert not broker._session_events and not broker._member_events


@pytest.mark.parametrize("mutation", [
    "foreign-snapshot", "rebuilt-snapshot", "substituted-records",
    "substituted-terminal", "substituted-generation", "substituted-resolve",
])
def test_terminal_snapshot_refuses_independent_identity_and_dispatch_substitution(
    mutation, monkeypatch,
):
    _terminal_type("TerminalArtifactVerifier")
    fact = _terminal_fact("G1-dataset-authorization")
    broker = _factory()(fixture_facts={"artifacts": [fact]})
    resolver = broker._p4_resolver
    snapshot = resolver.snapshot()
    if mutation == "foreign-snapshot":
        snapshot = _factory()(fixture_facts={"artifacts": [fact]})._p4_resolver.snapshot()
    elif mutation == "rebuilt-snapshot":
        snapshot = object.__new__(type(snapshot))
    elif mutation == "substituted-records":
        object.__setattr__(resolver, "_records", ())
    elif mutation == "substituted-terminal":
        object.__setattr__(resolver, "_terminal", object())
    elif mutation == "substituted-generation":
        object.__setattr__(snapshot, "_generation", 2)
    elif mutation == "substituted-resolve":
        monkeypatch.setattr(type(resolver), "resolve", lambda *args: fact["bytes"])
    with pytest.raises((TypeError, ValueError), match="snapshot|capability|integrity"):
        # The class-owned gate must detect a public-method replacement too.
        resolver._checked_resolve(snapshot, f4._json_bytes(fact["ref"]))
    assert not broker._session_events and not broker._member_events


@pytest.mark.parametrize("ref_mutation", ["kind", "role", "schema", "sha256", "extra"])
def test_external_fixture_reference_cannot_register_an_unknown_terminal(ref_mutation):
    _terminal_type("TerminalArtifactVerifier")
    fact = _terminal_fact("G1-dataset-authorization")
    fact["ref"][ref_mutation] = "f" * 64 if ref_mutation == "sha256" else "unknown"
    with pytest.raises((TypeError, ValueError)):
        _factory()(fixture_facts={"artifacts": [fact]})


def _factory():
    """Locate the approved factory with an explicit executable RED assertion."""
    factory = getattr(trust, "_development_p4_broker", None)
    assert callable(factory), "Matrix v7 fixed P4 broker factory is missing"
    return factory


def _request():
    """Return a syntactically valid reference to an unavailable admission."""
    return {
        "kind": "action-execution-admission",
        "role": "study-lifecycle",
        "schema": "dskit.action-execution-admission/v1",
        "sha256": "a" * 64,
    }


def _runtime():
    """Return distinct synthetic runtime identifiers with one unused nonce."""
    return {
        "consumer_run_identity": "consumer-run",
        "process_measurement_sha256": f4._SHA["consumer_process"],
        "runtime_sha256": f4._SHA["consumer_runtime"],
        "transition_nonces": ("p4-captured",),
    }


def test_p4_factory_has_no_dependency_selection_and_preserves_v1_abc():
    assert trust.LifecycleAuthority.__abstractmethods__ == {
        "produce", "seal", "publish", "capture", "open_capture"
    }
    factory = _factory()
    parameters = inspect.signature(factory).parameters
    assert tuple(parameters) == ("fixture_facts",)
    assert parameters["fixture_facts"].kind is inspect.Parameter.KEYWORD_ONLY
    assert parameters["fixture_facts"].default is None
    authority_type = getattr(trust, "CapturedAuthorizationAuthority", None)
    assert authority_type is not None, "P4 capability ABC is missing"
    assert issubclass(authority_type, trust.LifecycleAuthority)
    assert authority_type.__abstractmethods__ == (
        trust.LifecycleAuthority.__abstractmethods__ | {"authorize_capture_set"}
    )
    with pytest.raises(TypeError):
        authority_type()
    authority = factory()
    assert isinstance(authority, authority_type)
    assert authority.deployment_eligible is False
    assert not isinstance(trust._development_broker(), authority_type)


@pytest.mark.parametrize("name", [
    "signer", "keyring", "resolver", "clock", "revocations", "contract",
    "ledger", "store", "lock", "callback", "factory", "provider",
])
def test_p4_factory_refuses_every_dependency_keyword(name):
    factory = _factory()
    with pytest.raises(TypeError):
        factory(**{name: object()})
    with pytest.raises(TypeError):
        factory(object())


@pytest.mark.parametrize("facts", [
    object(), lambda: None, "/tmp/authority.json", "https://example.invalid/",
    {"signer": object()}, {"keyring": {}}, {"clock": 0}, {"ledger": {}},
])
def test_p4_factory_rejects_hostile_fixture_authority_selection(facts):
    factory = _factory()
    with pytest.raises((TypeError, ValueError)):
        factory(fixture_facts=facts)


def test_issued_p4_authority_is_not_copyable_or_publicly_constructible():
    authority = _factory()()
    with pytest.raises(TypeError):
        type(authority)()
    for operation in (copy.copy, copy.deepcopy, pickle.dumps):
        with pytest.raises(TypeError):
            operation(authority)
    with pytest.raises(TypeError):
        dict(authority)


@pytest.mark.parametrize("kind,schema", [
    ("action-execution-admission", "dskit.action-execution-admission/v1"),
    ("final-replay-admission", "dskit.final-replay-admission/v1"),
])
def test_missing_resolved_admission_cannot_spend_live_capture(kind, schema):
    broker = _factory()()
    producer, published, _values = f4._publish(broker)
    broker.end_session(producer)
    _document, frozen = f4._freeze(broker, published)
    port = broker.derive_consumer_port(frozen)
    before = broker._receipt_audit(published)
    session_events = tuple(broker._session_events)
    member_events = tuple(broker._member_events)
    admission = dict(_request(), kind=kind, schema=schema)

    # The same refusal is retryable and must not create a session or receipt.
    for _attempt in range(2):
        with pytest.raises(ValueError):
            broker.authorize_capture_set(
                ((published, frozen, port),), admission, **_runtime()
            )
        assert broker._receipt_audit(published) == before
        assert tuple(broker._session_events) == session_events
        assert tuple(broker._member_events) == member_events

    captured, session = broker.capture(
        published, frozen, port,
        consumer_run_identity="legacy-consumer",
        process_measurement_sha256=f4._SHA["consumer_process"],
        runtime_sha256=f4._SHA["consumer_runtime"],
        transition_nonce="legacy-captured",
    )
    assert captured is not None and session is not None
    assert broker._receipt_audit(published)[-1]["event"] == "CAPTURED"
    assert tuple(broker._member_events) == member_events


def test_loaded_fixture_is_data_and_cannot_substitute_for_verified_closure():
    admission = _request()
    facts = {"artifacts": [{
        "ref": admission,
        "bytes": f4._json_bytes({"schema": admission["schema"]}),
    }]}
    broker = _factory()(fixture_facts=facts)
    producer, published, _values = f4._publish(broker)
    broker.end_session(producer)
    _document, frozen = f4._freeze(broker, published)
    port = broker.derive_consumer_port(frozen)
    before = (broker._receipt_audit(published), tuple(broker._session_events))
    facts["artifacts"].clear()
    with pytest.raises(ValueError, match="closure|verification"):
        broker.authorize_capture_set(
            ((published, frozen, port),), admission, **_runtime()
        )
    assert (broker._receipt_audit(published), tuple(broker._session_events)) == before
    assert not broker._member_events


@pytest.mark.parametrize("change", [
    "captures-list", "entry-list", "empty", "duplicate", "port-extra",
    "port-wrong", "reference-extra", "reference-role", "reference-schema",
    "reference-null", "run-empty", "run-producer", "runtime-placeholder",
    "measurement-uppercase", "nonce-list", "nonce-empty", "nonce-used",
    "nonce-cardinality", "frozen-mutated",
])
def test_invalid_request_refuses_before_fixture_lookup_and_any_effect(change):
    broker = _factory()()
    producer, published, _values = f4._publish(broker)
    broker.end_session(producer)
    document, frozen = f4._freeze(broker, published)
    port = broker.derive_consumer_port(frozen)
    captures = ((published, frozen, port),)
    admission = _request()
    runtime = _runtime()
    if change == "captures-list":
        captures = list(captures)
    elif change == "entry-list":
        captures = (list(captures[0]),)
    elif change == "empty":
        captures = ()
    elif change == "duplicate":
        captures = captures * 2
        runtime["transition_nonces"] = ("one", "two")
    elif change == "port-extra":
        port["extra"] = "unplanned"
    elif change == "port-wrong":
        port["consumer_input"] = "other"
    elif change == "reference-extra":
        admission["resolver"] = "other"
    elif change == "reference-role":
        admission["role"] = "security-broker"
    elif change == "reference-schema":
        admission["schema"] = "dskit.final-replay-admission/v1"
    elif change == "reference-null":
        admission = None
    elif change == "run-empty":
        runtime["consumer_run_identity"] = ""
    elif change == "run-producer":
        runtime["consumer_run_identity"] = "producer-run"
    elif change == "runtime-placeholder":
        runtime["runtime_sha256"] = "0" * 64
    elif change == "measurement-uppercase":
        runtime["process_measurement_sha256"] = "A" * 64
    elif change == "nonce-list":
        runtime["transition_nonces"] = ["one"]
    elif change == "nonce-empty":
        runtime["transition_nonces"] = ("",)
    elif change == "nonce-used":
        runtime["transition_nonces"] = ("nonce-produced",)
    elif change == "nonce-cardinality":
        runtime["transition_nonces"] = ("one", "two")
    elif change == "frozen-mutated":
        document["name"] = "changed-after-freeze"
    before = (broker._receipt_audit(published), tuple(broker._session_events))
    with pytest.raises((TypeError, ValueError)) as failure:
        broker.authorize_capture_set(captures, admission, **runtime)
    assert "missing admission" not in str(failure.value)
    assert (broker._receipt_audit(published), tuple(broker._session_events)) == before
    assert not broker._member_events
