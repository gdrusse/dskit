"""Focused F5a driver-only capture-admission contract."""

import copy
import pickle

import pytest

from dskit.production import verifier as verifier_module
from tests.pipeline import test_trust as f4


# ADR-0147 Decision point 2: the sixth "admission" artifact is retired --
# bind() now refuses it as an unknown name; a verified admission_ref
# replaces it, checked by the ledger-backed gate in `capture()` instead.
_PLAN = {
    "scope_intent": {"schema": "scope-intent"},
    "ces": {"schema": "ces"},
    "pea": {"schema": "pea"},
    "bvp": {"schema": "bvp"},
    "cas": {"schema": "cas"},
}


def _setup():
    trust = f4._trust()
    broker = trust._development_broker(start_ms=1_700_000_000_000)
    producer, published, _values = f4._publish(broker)
    broker.end_session(producer)
    _document, frozen = f4._freeze(broker, published)
    return broker, published, frozen, broker.derive_consumer_port(frozen)


def _capture(driver, published, frozen, port, nonce="nonce-captured"):
    return driver.capture(
        published,
        frozen,
        port,
        consumer_run_identity="consumer-run",
        process_measurement_sha256=f4._SHA["consumer_process"],
        runtime_sha256=f4._SHA["consumer_runtime"],
        transition_nonce=nonce,
    )


def test_private_plan_precedes_capture():
    broker, published, frozen, port = _setup()
    verifier = verifier_module.HistoricalStudyVerifier(broker)

    with pytest.raises(ValueError, match="ScopeIntent|CES|PEA|BVP|CAS|admission"):
        verifier.capture(
            published,
            frozen,
            port,
            consumer_run_identity="consumer-run",
            process_measurement_sha256=f4._SHA["consumer_process"],
            runtime_sha256=f4._SHA["consumer_runtime"],
            transition_nonce="nonce-captured",
        )

    assert broker._receipt_audit(published)[-1]["event"] == "PUBLISHED"


@pytest.mark.parametrize("value", [None, object()])
def test_driver_refuses_non_verifier_at_construction(value):
    with pytest.raises(TypeError, match="HistoricalStudyVerifier"):
        verifier_module.HistoricalStudyCaptureDriver(value)


@pytest.mark.parametrize("keyword", ["authority", "broker", "doorway"])
def test_driver_refuses_broker_keyword_aliases(keyword):
    broker, _published, _frozen, _port = _setup()
    with pytest.raises(TypeError):
        verifier_module.HistoricalStudyCaptureDriver(**{keyword: broker})


def test_driver_refuses_subclass_and_nested_wrapper():
    class SkipBind(verifier_module.HistoricalStudyVerifier):
        pass

    broker, _published, _frozen, _port = _setup()
    subclass = object.__new__(SkipBind)
    with pytest.raises(TypeError, match="HistoricalStudyVerifier"):
        verifier_module.HistoricalStudyCaptureDriver(subclass)
    with pytest.raises(TypeError, match="HistoricalStudyVerifier"):
        verifier_module.HistoricalStudyCaptureDriver(
            verifier_module.HistoricalStudyCaptureDriver(broker)
        )


def test_driver_refuses_broker_and_exact_verifier_substitution():
    broker, published, frozen, port = _setup()
    first = verifier_module.HistoricalStudyVerifier(broker)
    second = verifier_module.HistoricalStudyVerifier(broker)
    first.bind(**_PLAN)
    second.bind(**_PLAN)
    driver = verifier_module.HistoricalStudyCaptureDriver(first)

    object.__setattr__(driver, "_verifier", broker)
    with pytest.raises(ValueError, match="bound verifier"):
        _capture(driver, published, frozen, port)
    assert broker._receipt_audit(published)[-1]["event"] == "PUBLISHED"

    object.__setattr__(driver, "_verifier", second)
    with pytest.raises(ValueError, match="bound verifier"):
        _capture(driver, published, frozen, port)
    assert broker._receipt_audit(published)[-1]["event"] == "PUBLISHED"


def test_driver_uses_class_method_not_rebound_instance_capture():
    broker, published, frozen, port = _setup()
    verifier = verifier_module.HistoricalStudyVerifier(broker)
    verifier.capture = broker.capture
    driver = verifier_module.HistoricalStudyCaptureDriver(verifier)

    with pytest.raises(ValueError, match="ScopeIntent|CES|PEA|BVP|CAS|admission"):
        _capture(driver, published, frozen, port)
    assert broker._receipt_audit(published)[-1]["event"] == "PUBLISHED"


def test_authority_only_bound_verifier_still_permanently_refuses_capture():
    """ADR-0147 Decision point 2: an authority-only verifier (built via the
    plain constructor, never `.durable(...)`) holds no ledger at all, so it
    refuses `capture()` even with every ADR-0125 plan artifact bound --
    mechanically, not merely because the retired `_spend` doorway was
    unlucky. This replaces the pre-ADR-0147 expectation that a bound
    authority-only verifier could reach CAPTURED."""
    broker, published, frozen, port = _setup()
    verifier = verifier_module.HistoricalStudyVerifier(broker)
    verifier.bind(**_PLAN)
    driver = verifier_module.HistoricalStudyCaptureDriver(verifier)

    with pytest.raises(ValueError, match="ScopeIntent|CES|PEA|BVP|CAS"):
        _capture(driver, published, frozen, port)
    assert broker._receipt_audit(published)[-1]["event"] == "PUBLISHED"


def test_unbound_driver_refuses_without_broker_fallback_then_f4_broker_still_works():
    broker, published, frozen, port = _setup()
    verifier = verifier_module.HistoricalStudyVerifier(broker)
    driver = verifier_module.HistoricalStudyCaptureDriver(verifier)

    with pytest.raises(ValueError, match="ScopeIntent|CES|PEA|BVP|CAS|admission"):
        _capture(driver, published, frozen, port)
    assert broker._receipt_audit(published)[-1]["event"] == "PUBLISHED"

    captured, session = broker.capture(
        published,
        frozen,
        port,
        consumer_run_identity="consumer-run",
        process_measurement_sha256=f4._SHA["consumer_process"],
        runtime_sha256=f4._SHA["consumer_runtime"],
        transition_nonce="nonce-direct-f4",
    )
    assert captured is not None
    assert session is not None
    assert broker._receipt_audit(published)[-1]["event"] == "CAPTURED"


def test_driver_copy_pickle_and_uninitialized_state_refuse():
    broker, _published, _frozen, _port = _setup()
    driver = verifier_module.HistoricalStudyCaptureDriver(
        verifier_module.HistoricalStudyVerifier(broker)
    )

    for operation in (lambda: copy.copy(driver), lambda: copy.deepcopy(driver), lambda: pickle.loads(pickle.dumps(driver))):
        with pytest.raises(TypeError, match="opaque"):
            operation()
    twin = object.__new__(verifier_module.HistoricalStudyCaptureDriver)
    with pytest.raises(ValueError, match="opaque"):
        _capture(twin, None, None, None)
