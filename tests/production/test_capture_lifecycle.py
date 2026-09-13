"""F5a private-plan-before-capture contract (ADR-0125).

CAPTURED is refused until ScopeIntent, CES, PEA, BVP, CAS, and a consumed
admission are bound. The historical-study doorway is
``HistoricalStudyVerifier``; F4's development broker still writes the WORM
chain and stays ``deployment_eligible=false``.
"""

import pytest

from dskit.production import verifier as verifier_module
from tests.pipeline import test_trust as f4

_PLACEHOLDERS = {
    "scope_intent": {"schema": "scope-intent"},
    "ces": {"schema": "ces"},
    "pea": {"schema": "pea"},
    "bvp": {"schema": "bvp"},
    "cas": {"schema": "cas"},
    "admission": {"schema": "admission", "consumed": True},
}


def _study_capture_setup():
    trust = f4._trust()
    broker = trust._development_broker(start_ms=1_700_000_000_000)
    producer_session, published, _values = f4._publish(broker)
    broker.end_session(producer_session)
    _document, frozen = f4._freeze(broker, published)
    port = broker.derive_consumer_port(frozen)
    verifier = verifier_module.HistoricalStudyVerifier(broker)
    return broker, published, frozen, port, verifier


def test_private_plan_precedes_capture():
    broker, published, frozen, port, verifier = _study_capture_setup()
    with pytest.raises(
        ValueError,
        match="ScopeIntent|CES|PEA|BVP|CAS|admission",
    ):
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


def test_bound_private_plan_allows_capture():
    broker, published, frozen, port, verifier = _study_capture_setup()
    verifier.bind(**_PLACEHOLDERS)
    captured, session = verifier.capture(
        published,
        frozen,
        port,
        consumer_run_identity="consumer-run",
        process_measurement_sha256=f4._SHA["consumer_process"],
        runtime_sha256=f4._SHA["consumer_runtime"],
        transition_nonce="nonce-captured",
    )
    assert broker._receipt_audit(published)[-1]["event"] == "CAPTURED"
    assert captured is not None
    assert session is not None


def test_falsy_plan_artifact_cannot_bind_capture():
    for name in _PLACEHOLDERS:
        for value in ("", False, 0):
            broker, published, frozen, port, verifier = _study_capture_setup()
            payload = dict(_PLACEHOLDERS)
            payload[name] = value
            with pytest.raises(ValueError, match="plan artifact is required"):
                verifier.bind(**payload)
            with pytest.raises(
                ValueError,
                match="ScopeIntent|CES|PEA|BVP|CAS|admission",
            ):
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
