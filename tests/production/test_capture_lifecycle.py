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


class _TruthyEmpty(dict):
    def __bool__(self):
        return True


class _GetConsumed(dict):
    def get(self, key, default=None):
        if key == "consumed":
            return True
        return super().get(key, default)



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


def test_unconsumed_admission_cannot_bind_capture():
    broker, published, frozen, port, verifier = _study_capture_setup()
    payload = dict(_PLACEHOLDERS)
    payload["admission"] = {"schema": "admission", "consumed": False}
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


def test_whitespace_plan_artifact_cannot_bind_capture():
    for name in ("scope_intent", "ces", "pea", "bvp", "cas"):
        broker, published, frozen, port, verifier = _study_capture_setup()
        payload = dict(_PLACEHOLDERS)
        payload[name] = " "
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


def test_format_blank_plan_artifact_cannot_bind_capture():
    for name in ("scope_intent", "ces", "pea", "bvp", "cas"):
        for value in ("\ufeff", "\u200b", " \ufeff", "\ufeff "):
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


def test_bytes_blank_plan_artifact_cannot_bind_capture():
    for name in ("scope_intent", "ces", "pea", "bvp", "cas"):
        for value in (b" ", b"\t", bytearray(b" ")):
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


def test_dict_subclass_plan_artifact_cannot_bind_capture():
    broker, published, frozen, port, verifier = _study_capture_setup()
    payload = dict(_PLACEHOLDERS)
    empty = _TruthyEmpty()
    for name in ("scope_intent", "ces", "pea", "bvp", "cas"):
        payload[name] = empty
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


def test_admission_get_spoof_cannot_bind_capture():
    broker, published, frozen, port, verifier = _study_capture_setup()
    payload = dict(_PLACEHOLDERS)
    payload["admission"] = _GetConsumed()
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


def test_refused_bind_does_not_store_partial_artifacts():
    broker, published, frozen, port, verifier = _study_capture_setup()
    five = {name: _PLACEHOLDERS[name] for name in _PLACEHOLDERS if name != "admission"}
    verifier.bind(**five)
    with pytest.raises(ValueError, match="plan artifact is required"):
        verifier.bind(admission=_PLACEHOLDERS["admission"], cas={})
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


def test_consumed_admission_cannot_capture_a_second_stream():
    trust = f4._trust()
    broker = trust._development_broker(start_ms=1_700_000_000_000)
    producer_a, published_a, _values = f4._publish(broker)
    broker.end_session(producer_a)
    published_b, _sealed_b = f4._foreign_publish(broker)
    _document_a, frozen_a = f4._freeze(broker, published_a)
    _document_b, frozen_b = f4._freeze(
        broker,
        published_b,
        document=f4._consumer_document(
            broker.descriptor(published_b, purpose="synthetic"),
            name="consumer-b-doc",
        ),
    )
    port_a = broker.derive_consumer_port(frozen_a)
    port_b = broker.derive_consumer_port(frozen_b)
    verifier = verifier_module.HistoricalStudyVerifier(broker)
    verifier.bind(**_PLACEHOLDERS)
    captured, session = verifier.capture(
        published_a,
        frozen_a,
        port_a,
        consumer_run_identity="consumer-a",
        process_measurement_sha256=f4._SHA["consumer_process"],
        runtime_sha256=f4._SHA["consumer_runtime"],
        transition_nonce="nonce-captured-a",
    )
    assert captured is not None
    assert session is not None
    assert broker._receipt_audit(published_a)[-1]["event"] == "CAPTURED"
    with pytest.raises(ValueError, match="consumed admission"):
        verifier.capture(
            published_b,
            frozen_b,
            port_b,
            consumer_run_identity="consumer-b",
            process_measurement_sha256=f4._SHA["consumer_process"],
            runtime_sha256=f4._SHA["consumer_runtime"],
            transition_nonce="nonce-captured-b",
        )
    assert broker._receipt_audit(published_b)[-1]["event"] == "PUBLISHED"


def test_capture_write_then_raise_still_spends_admission():
    trust = f4._trust()
    broker = trust._development_broker(start_ms=1_700_000_000_000)
    producer_a, published_a, _values = f4._publish(broker)
    broker.end_session(producer_a)
    published_b, _sealed_b = f4._foreign_publish(broker)
    _document_a, frozen_a = f4._freeze(broker, published_a)
    _document_b, frozen_b = f4._freeze(
        broker,
        published_b,
        document=f4._consumer_document(
            broker.descriptor(published_b, purpose="synthetic"),
            name="consumer-b-doc",
        ),
    )
    port_a = broker.derive_consumer_port(frozen_a)
    port_b = broker.derive_consumer_port(frozen_b)
    verifier = verifier_module.HistoricalStudyVerifier(broker)
    verifier.bind(**_PLACEHOLDERS)
    inner = broker.capture

    def write_then_raise(*args, **kwargs):
        inner(*args, **kwargs)
        raise ValueError("capture wrote then failed")

    broker.capture = write_then_raise
    with pytest.raises(ValueError, match="capture wrote then failed"):
        verifier.capture(
            published_a,
            frozen_a,
            port_a,
            consumer_run_identity="consumer-a",
            process_measurement_sha256=f4._SHA["consumer_process"],
            runtime_sha256=f4._SHA["consumer_runtime"],
            transition_nonce="nonce-captured-a",
        )
    assert broker._receipt_audit(published_a)[-1]["event"] == "CAPTURED"
    broker.capture = inner
    with pytest.raises(ValueError, match="consumed admission"):
        verifier.capture(
            published_b,
            frozen_b,
            port_b,
            consumer_run_identity="consumer-b",
            process_measurement_sha256=f4._SHA["consumer_process"],
            runtime_sha256=f4._SHA["consumer_runtime"],
            transition_nonce="nonce-captured-b",
        )
    assert broker._receipt_audit(published_b)[-1]["event"] == "PUBLISHED"
