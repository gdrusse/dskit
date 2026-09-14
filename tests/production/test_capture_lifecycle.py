"""F5a private-plan-before-capture contract (ADR-0125).

CAPTURED is refused until ScopeIntent, CES, PEA, BVP, CAS, and a consumed
admission are bound. The historical-study doorway is
``HistoricalStudyVerifier``; F4's development broker still writes the WORM
chain and stays ``deployment_eligible=false``.
"""

import copy
import inspect

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


def _bound_two_stream_setup():
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
    return (
        broker,
        published_a,
        frozen_a,
        port_a,
        published_b,
        frozen_b,
        port_b,
        verifier,
    )


def _opaque_copy_value(obj):
    for value in obj.__dict__.values():
        try:
            copy.copy(value)
        except TypeError as exc:
            if "opaque" in str(exc):
                return value
    raise AssertionError("no opaque copy value")


def _discovered_zero_arg_mint(spend, verifier):
    namespaces = [vars(inspect.getmodule(type(spend))), verifier.capture.__globals__]
    seen = []
    for namespace in namespaces:
        for obj in namespace.values():
            if obj in seen or not callable(obj):
                continue
            if getattr(obj, "__module__", None) != type(spend).__module__:
                continue
            seen.append(obj)
            try:
                got = obj()
            except Exception:
                continue
            if type(got) is type(spend):
                return got
    return None


def _discovered_mappings(spend, verifier):
    seen = []
    namespaces = [vars(inspect.getmodule(type(spend))), verifier.capture.__globals__]
    for namespace in namespaces:
        for obj in namespace.values():
            if obj in seen:
                continue
            try:
                holds_spend = spend in obj
            except TypeError:
                holds_spend = False
            if holds_spend or type(obj).__name__ == "WeakKeyDictionary":
                seen.append(obj)
                yield obj


def _readd_into(holders, cell):
    for holder in holders:
        adder = getattr(holder, "add", None)
        if callable(adder):
            try:
                adder(cell)
            except Exception:
                continue



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


def test_copy_cannot_capture_a_second_stream():
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
    with pytest.raises(TypeError, match="opaque"):
        copy.copy(verifier)
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


def test_pickle_identity_cannot_capture_a_second_stream():
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
    with pytest.raises(TypeError, match="opaque"):
        verifier.__reduce__()
    with pytest.raises(TypeError, match="opaque"):
        verifier.__getstate__()
    with pytest.raises(TypeError, match="opaque"):
        verifier.__reduce_ex__(4)
    twin = object.__new__(verifier_module.HistoricalStudyVerifier)
    twin.__dict__.update(verifier.__dict__)
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
        twin.capture(
            published_b,
            frozen_b,
            port_b,
            consumer_run_identity="consumer-b",
            process_measurement_sha256=f4._SHA["consumer_process"],
            runtime_sha256=f4._SHA["consumer_runtime"],
            transition_nonce="nonce-captured-b",
        )
    assert broker._receipt_audit(published_b)[-1]["event"] == "PUBLISHED"


def test_dict_value_copy_cannot_capture_a_second_stream():
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
    twin = object.__new__(verifier_module.HistoricalStudyVerifier)
    for key, value in verifier.__dict__.items():
        try:
            twin.__dict__[key] = copy.copy(value)
        except TypeError:
            twin.__dict__[key] = value
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
        twin.capture(
            published_b,
            frozen_b,
            port_b,
            consumer_run_identity="consumer-b",
            process_measurement_sha256=f4._SHA["consumer_process"],
            runtime_sha256=f4._SHA["consumer_runtime"],
            transition_nonce="nonce-captured-b",
        )
    assert broker._receipt_audit(published_b)[-1]["event"] == "PUBLISHED"


def test_type_value_mint_cannot_capture_a_second_stream():
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
    twin = object.__new__(verifier_module.HistoricalStudyVerifier)
    for key, value in verifier.__dict__.items():
        try:
            twin.__dict__[key] = copy.copy(value)
        except TypeError:
            try:
                twin.__dict__[key] = type(value)()
            except TypeError:
                twin.__dict__[key] = value
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
        twin.capture(
            published_b,
            frozen_b,
            port_b,
            consumer_run_identity="consumer-b",
            process_measurement_sha256=f4._SHA["consumer_process"],
            runtime_sha256=f4._SHA["consumer_runtime"],
            transition_nonce="nonce-captured-b",
        )
    assert broker._receipt_audit(published_b)[-1]["event"] == "PUBLISHED"


def _clone_doorway(orig, spend):
    twin = object.__new__(type(orig))
    for key, value in orig.__dict__.items():
        try:
            copied = copy.copy(value)
        except TypeError as exc:
            if "opaque" in str(exc):
                twin.__dict__[key] = spend
            else:
                try:
                    twin.__dict__[key] = type(value)()
                except TypeError:
                    twin.__dict__[key] = value
            continue
        twin.__dict__[key] = copied
    return twin


def test_module_mint_cannot_capture_a_second_stream():
    (
        broker,
        published_a,
        frozen_a,
        port_a,
        published_b,
        frozen_b,
        port_b,
        verifier,
    ) = _bound_two_stream_setup()
    spend = _opaque_copy_value(verifier)
    minted = _discovered_zero_arg_mint(spend, verifier)
    twin = _clone_doorway(verifier, minted if minted is not None else spend)
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
        twin.capture(
            published_b,
            frozen_b,
            port_b,
            consumer_run_identity="consumer-b",
            process_measurement_sha256=f4._SHA["consumer_process"],
            runtime_sha256=f4._SHA["consumer_runtime"],
            transition_nonce="nonce-captured-b",
        )
    assert broker._receipt_audit(published_b)[-1]["event"] == "PUBLISHED"


def test_mapping_reregister_cannot_capture_a_second_stream():
    (
        broker,
        published_a,
        frozen_a,
        port_a,
        published_b,
        frozen_b,
        port_b,
        verifier,
    ) = _bound_two_stream_setup()
    spend = _opaque_copy_value(verifier)
    cell = object.__new__(type(spend))
    for mapping in _discovered_mappings(spend, verifier):
        try:
            mapping[cell] = False
        except Exception:
            continue
    twin = _clone_doorway(verifier, cell)
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
        twin.capture(
            published_b,
            frozen_b,
            port_b,
            consumer_run_identity="consumer-b",
            process_measurement_sha256=f4._SHA["consumer_process"],
            runtime_sha256=f4._SHA["consumer_runtime"],
            transition_nonce="nonce-captured-b",
        )
    assert broker._receipt_audit(published_b)[-1]["event"] == "PUBLISHED"


def test_slots_setattr_cannot_capture_a_second_stream():
    (
        broker,
        published_a,
        frozen_a,
        port_a,
        published_b,
        frozen_b,
        port_b,
        verifier,
    ) = _bound_two_stream_setup()
    spend = _opaque_copy_value(verifier)
    cell = object.__new__(type(spend))
    for name in type(spend).__slots__:
        try:
            object.__setattr__(cell, name, False)
        except Exception:
            continue
    twin = _clone_doorway(verifier, cell)
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
        twin.capture(
            published_b,
            frozen_b,
            port_b,
            consumer_run_identity="consumer-b",
            process_measurement_sha256=f4._SHA["consumer_process"],
            runtime_sha256=f4._SHA["consumer_runtime"],
            transition_nonce="nonce-captured-b",
        )
    assert broker._receipt_audit(published_b)[-1]["event"] == "PUBLISHED"


def test_live_set_readd_cannot_capture_a_second_stream():
    (
        broker,
        published_a,
        frozen_a,
        port_a,
        published_b,
        frozen_b,
        port_b,
        verifier,
    ) = _bound_two_stream_setup()
    spend = _opaque_copy_value(verifier)
    holders = list(_discovered_mappings(spend, verifier))
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
    _readd_into(holders, spend)
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


def test_live_set_new_cell_cannot_capture_a_second_stream():
    (
        broker,
        published_a,
        frozen_a,
        port_a,
        published_b,
        frozen_b,
        port_b,
        verifier,
    ) = _bound_two_stream_setup()
    spend = _opaque_copy_value(verifier)
    cell = object.__new__(type(spend))
    _readd_into(list(_discovered_mappings(spend, verifier)), cell)
    twin = _clone_doorway(verifier, cell)
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
        twin.capture(
            published_b,
            frozen_b,
            port_b,
            consumer_run_identity="consumer-b",
            process_measurement_sha256=f4._SHA["consumer_process"],
            runtime_sha256=f4._SHA["consumer_runtime"],
            transition_nonce="nonce-captured-b",
        )
    assert broker._receipt_audit(published_b)[-1]["event"] == "PUBLISHED"


def _true_bool_names(cls):
    names = []
    for name, value in cls.__dict__.items():
        if value is True:
            names.append(name)
    return names


def test_type_flag_delattr_cannot_capture_a_second_stream():
    (
        broker,
        published_a,
        frozen_a,
        port_a,
        published_b,
        frozen_b,
        port_b,
        verifier,
    ) = _bound_two_stream_setup()
    spend = _opaque_copy_value(verifier)
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
    cls = type(spend)
    for name in _true_bool_names(cls):
        try:
            delattr(cls, name)
            setattr(cls, name, False)
        except Exception:
            continue
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


def test_type_setattr_cannot_capture_a_second_stream():
    (
        broker,
        published_a,
        frozen_a,
        port_a,
        published_b,
        frozen_b,
        port_b,
        verifier,
    ) = _bound_two_stream_setup()
    spend = _opaque_copy_value(verifier)
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
    cls = type(spend)
    for name in _true_bool_names(cls):
        try:
            type.__setattr__(cls, name, False)
        except Exception:
            continue
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


def test_type_call_sibling_cannot_capture_a_second_stream():
    (
        broker,
        published_a,
        frozen_a,
        port_a,
        published_b,
        frozen_b,
        port_b,
        verifier,
    ) = _bound_two_stream_setup()
    spend = _opaque_copy_value(verifier)
    door = type(spend)
    namespace = {}
    slots = getattr(door, "__slots__", ())
    if slots:
        namespace["__slots__"] = slots
    for name, value in door.__dict__.items():
        if value is False or value is True:
            namespace[name] = False
    sibling = type(door)(door.__name__, door.__bases__, namespace)
    cell = object.__new__(sibling)
    twin = _clone_doorway(verifier, cell)
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
        twin.capture(
            published_b,
            frozen_b,
            port_b,
            consumer_run_identity="consumer-b",
            process_measurement_sha256=f4._SHA["consumer_process"],
            runtime_sha256=f4._SHA["consumer_runtime"],
            transition_nonce="nonce-captured-b",
        )
    assert broker._receipt_audit(published_b)[-1]["event"] == "PUBLISHED"


def test_nonbool_sentinel_cannot_capture_a_second_stream():
    (
        broker,
        published_a,
        frozen_a,
        port_a,
        published_b,
        frozen_b,
        port_b,
        verifier,
    ) = _bound_two_stream_setup()
    spend = _opaque_copy_value(verifier)
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
    cls = type(spend)
    for name in list(cls.__dict__):
        if name.startswith("__"):
            continue
        try:
            type.__delattr__(cls, name)
        except Exception:
            pass
        try:
            type.__setattr__(cls, name, False)
        except Exception:
            try:
                setattr(cls, name, False)
            except Exception:
                continue
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


def test_sibling_type_pairing_cannot_capture_a_second_stream():
    (
        broker,
        published_a,
        frozen_a,
        port_a,
        published_b,
        frozen_b,
        port_b,
        verifier,
    ) = _bound_two_stream_setup()
    spend = _opaque_copy_value(verifier)
    door = type(spend)
    namespace = {}
    slots = getattr(door, "__slots__", ())
    if slots or slots == ():
        namespace["__slots__"] = slots
    sibling = type(door)(door.__name__, door.__bases__, namespace)
    cell = object.__new__(sibling)
    twin = object.__new__(type(verifier))
    for key, value in verifier.__dict__.items():
        if isinstance(value, type):
            twin.__dict__[key] = sibling
            continue
        try:
            copied = copy.copy(value)
        except TypeError as exc:
            if "opaque" in str(exc):
                twin.__dict__[key] = cell
            else:
                try:
                    twin.__dict__[key] = type(value)()
                except TypeError:
                    twin.__dict__[key] = value
            continue
        twin.__dict__[key] = copied
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
        twin.capture(
            published_b,
            frozen_b,
            port_b,
            consumer_run_identity="consumer-b",
            process_measurement_sha256=f4._SHA["consumer_process"],
            runtime_sha256=f4._SHA["consumer_runtime"],
            transition_nonce="nonce-captured-b",
        )
    assert broker._receipt_audit(published_b)[-1]["event"] == "PUBLISHED"


def _replace_nested_types(value, sibling):
    if isinstance(value, type):
        return sibling
    if type(value) is tuple:
        return tuple(_replace_nested_types(item, sibling) for item in value)
    if type(value) is list:
        return [_replace_nested_types(item, sibling) for item in value]
    return value


def _twin_with_nested_type(orig, cell, sibling):
    twin = object.__new__(type(orig))
    for key, value in orig.__dict__.items():
        try:
            copy.copy(value)
        except TypeError as exc:
            if "opaque" in str(exc):
                twin.__dict__[key] = cell
                continue
            try:
                twin.__dict__[key] = type(value)()
            except TypeError:
                twin.__dict__[key] = value
            continue
        twin.__dict__[key] = _replace_nested_types(value, sibling)
    return twin


def test_unpack_nested_type_cannot_capture_a_second_stream():
    (
        broker,
        published_a,
        frozen_a,
        port_a,
        published_b,
        frozen_b,
        port_b,
        verifier,
    ) = _bound_two_stream_setup()
    spend = _opaque_copy_value(verifier)
    door = type(spend)
    sibling = type(door)(door.__name__, door.__bases__, {"__slots__": getattr(door, "__slots__", ())})
    cell = object.__new__(sibling)
    twin = _twin_with_nested_type(verifier, cell, sibling)
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
        twin.capture(
            published_b,
            frozen_b,
            port_b,
            consumer_run_identity="consumer-b",
            process_measurement_sha256=f4._SHA["consumer_process"],
            runtime_sha256=f4._SHA["consumer_runtime"],
            transition_nonce="nonce-captured-b",
        )
    assert broker._receipt_audit(published_b)[-1]["event"] == "PUBLISHED"


def test_unpack_after_a_cannot_capture_a_second_stream():
    (
        broker,
        published_a,
        frozen_a,
        port_a,
        published_b,
        frozen_b,
        port_b,
        verifier,
    ) = _bound_two_stream_setup()
    spend = _opaque_copy_value(verifier)
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
    door = type(spend)
    sibling = type(door)(door.__name__, door.__bases__, {"__slots__": getattr(door, "__slots__", ())})
    cell = object.__new__(sibling)
    for key, value in list(verifier.__dict__.items()):
        try:
            copy.copy(value)
        except TypeError as exc:
            if "opaque" in str(exc):
                verifier.__dict__[key] = cell
                continue
        verifier.__dict__[key] = _replace_nested_types(value, sibling)
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


def test_saved_cls_reset_cannot_capture_a_second_stream():
    (
        broker,
        published_a,
        frozen_a,
        port_a,
        published_b,
        frozen_b,
        port_b,
        verifier,
    ) = _bound_two_stream_setup()
    spend = _opaque_copy_value(verifier)
    cls = type(spend)
    twin = _clone_doorway(verifier, object.__new__(cls))
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
    for name in list(cls.__dict__):
        if name.startswith("__"):
            continue
        try:
            type.__delattr__(cls, name)
        except Exception:
            try:
                type.__setattr__(cls, name, False)
            except Exception:
                continue
    with pytest.raises(ValueError, match="consumed admission"):
        twin.capture(
            published_b,
            frozen_b,
            port_b,
            consumer_run_identity="consumer-b",
            process_measurement_sha256=f4._SHA["consumer_process"],
            runtime_sha256=f4._SHA["consumer_runtime"],
            transition_nonce="nonce-captured-b",
        )
    assert broker._receipt_audit(published_b)[-1]["event"] == "PUBLISHED"
