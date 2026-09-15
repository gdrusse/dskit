"""Packet 4 capability and refusal boundaries from the approved Matrix v7.

These initial RED cases cover the fixed factory, unresolvable admissions, and
preservation of ordinary lifecycle behavior. They do not stand in for the
required complete signed action/replay, transaction, or concurrency matrix.
"""

import copy
import inspect
import pickle

import pytest

from dskit.pipeline import trust
from tests.pipeline import test_trust as f4


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
