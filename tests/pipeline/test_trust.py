"""F4 immutable capture and WORM lifecycle contract tests."""

import copy
import hashlib
import importlib
import importlib.util
import json
import os
import pickle
from types import MappingProxyType

import pytest


_SHA = {
    "producer_document": "1" * 64,
    "root": "2" * 64,
    "producer_process": "3" * 64,
    "producer_runtime": "4" * 64,
    "producer_plan": "5" * 64,
    "consumer_process": "6" * 64,
    "consumer_runtime": "7" * 64,
}
_PRODUCER = {
    "document_sha256": _SHA["producer_document"],
    "run_identity": "producer-run",
    "node": "publisher",
    "output": "bundle",
}
_ROOT = {
    "root_ref": "capture://synthetic/root",
    "root_id": _SHA["root"],
    "snapshot_version": "1",
}
_PUBLIC_TYPES = {
    "CapturedAuthorizationAuthority",
    "CapturedBindings",
    "CapturedJsonArtifact",
    "CapturedLifecyclePort",
    "CapturedMemberHandle",
    "CapturedRelease",
    "ImmutableSnapshotProvider",
    "LaunchSession",
    "LifecycleAuthority",
    "ReleaseKeyring",
    "TrustedClock",
    "TrustedRuntimeVerifier",
    "VerifiedCapture",
}


def _trust():
    spec = importlib.util.find_spec("dskit.pipeline.trust")
    assert spec is not None, "F4 trust module is not implemented"
    return importlib.import_module("dskit.pipeline.trust")


def _json_bytes(value):
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")


def _members(bundle=None):
    if bundle is None:
        bundle = {"rows": [{"id": "one", "value": 7}]}
    return [
        {
            "relative_path": "config.json",
            "media_type": "application/json",
            "bytes": bytearray(_json_bytes({"name": "producer"})),
            "file_type": "regular",
            "link_count": 1,
        },
        {
            "relative_path": "artifacts/bundle.json",
            "media_type": "application/json",
            "bytes": bytearray(_json_bytes(bundle)),
            "file_type": "regular",
            "link_count": 1,
        },
    ]


def _producer_session(broker):
    return broker.start_producer_session(
        run_identity="producer-run",
        process_measurement_sha256=_SHA["producer_process"],
        runtime_sha256=_SHA["producer_runtime"],
        plan_sha256=_SHA["producer_plan"],
    )


def _produce(broker, *, members=None, expected_members=None, nonce="nonce-produced"):
    session = _producer_session(broker)
    values = _members() if members is None else members
    expected = (
        ("config.json", "artifacts/bundle.json")
        if expected_members is None
        else expected_members
    )
    prepared = broker.produce(
        session,
        producer=dict(_PRODUCER),
        root=dict(_ROOT),
        purpose="synthetic",
        expected_members=expected,
        members=values,
        output_member="artifacts/bundle.json",
        completed=True,
        planned=True,
        transition_nonce=nonce,
    )
    return session, prepared, values


def _publish(
    broker,
    *,
    members=None,
    expected_members=None,
    produced_nonce="nonce-produced",
    sealed_nonce="nonce-sealed",
    published_nonce="nonce-published",
):
    session, prepared, values = _produce(
        broker,
        members=members,
        expected_members=expected_members,
        nonce=produced_nonce,
    )
    sealed = broker.seal(session, prepared, transition_nonce=sealed_nonce)
    published = broker.publish(session, sealed, transition_nonce=published_nonce)
    return session, published, values


def _foreign_publish(broker):
    session = broker.start_producer_session(
        run_identity="producer-b",
        process_measurement_sha256=_SHA["producer_process"],
        runtime_sha256=_SHA["producer_runtime"],
        plan_sha256=_SHA["producer_plan"],
    )
    producer = dict(_PRODUCER)
    producer["run_identity"] = "producer-b"
    prepared = broker.produce(
        session,
        producer=producer,
        root={
            "root_ref": "capture://synthetic/root-b",
            "root_id": "8" * 64,
            "snapshot_version": "1",
        },
        purpose="synthetic",
        expected_members=("config.json", "artifacts/bundle.json"),
        members=_members({"rows": [{"id": "BBB-SUBSTITUTED"}]}),
        output_member="artifacts/bundle.json",
        completed=True,
        planned=True,
        transition_nonce="nonce-produced-b",
    )
    sealed = broker.seal(session, prepared, transition_nonce="nonce-sealed-b")
    published = broker.publish(session, sealed, transition_nonce="nonce-published-b")
    broker.end_session(session)
    return published, sealed


def _two_captured_consumers(broker):
    session_prod_a, published_a, _values = _publish(
        broker,
        members=_members({"rows": [{"id": "AAA"}]}),
    )
    broker.end_session(session_prod_a)
    published_b, _sealed_b = _foreign_publish(broker)
    _document_a, frozen_a = _freeze(broker, published_a)
    _document_b, frozen_b = _freeze(
        broker,
        published_b,
        document=_consumer_document(
            broker.descriptor(published_b, purpose="synthetic"),
            name="consumer-b-doc",
        ),
    )
    captured_a, session_a = _capture(
        broker,
        published_a,
        frozen_a,
        run_identity="consumer-a",
        nonce="nonce-captured-a",
    )
    captured_b, session_b = _capture(
        broker,
        published_b,
        frozen_b,
        run_identity="consumer-b",
        nonce="nonce-captured-b",
    )
    verified_a = broker.open_capture(session_a, captured_a)
    verified_b = broker.open_capture(session_b, captured_b)
    bindings_a = broker.captured_bindings(
        session_a,
        frozen_a,
        verified_a,
        consumer_node="consume",
        transition_nonce="nonce-consumed-a",
    )
    bindings_b = broker.captured_bindings(
        session_b,
        frozen_b,
        verified_b,
        consumer_node="consume",
        transition_nonce="nonce-consumed-b",
    )
    return (
        published_a,
        published_b,
        frozen_a,
        frozen_b,
        session_a,
        session_b,
        bindings_a,
        bindings_b,
    )


def _consumer_document(descriptor, *, name="consumer"):
    return {
        "name": name,
        "pipeline": {
            "consume": {
                "inputs": {
                    "bundle": {"$captured_artifact": descriptor},
                }
            }
        },
    }


def _freeze(broker, published, *, document=None):
    descriptor = broker.descriptor(published, purpose="synthetic")
    source = _consumer_document(descriptor) if document is None else document
    return (
        source,
        broker.freeze_consumer_document(
            source,
            consumer_node="consume",
            consumer_input="bundle",
            purpose="synthetic",
        ),
    )


def _capture(
    broker,
    published,
    frozen,
    *,
    port=None,
    run_identity="consumer-run",
    nonce="nonce-captured",
):
    if port is None:
        port = broker.derive_consumer_port(frozen)
    return broker.capture(
        published,
        frozen,
        port,
        consumer_run_identity=run_identity,
        process_measurement_sha256=_SHA["consumer_process"],
        runtime_sha256=_SHA["consumer_runtime"],
        transition_nonce=nonce,
    )


def _captured_flow(
    *,
    snapshot_storage=None,
    receipt_store=None,
    session_events=None,
    member_events=None,
):
    trust = _trust()
    broker = trust._development_broker(
        snapshot_storage=snapshot_storage,
        receipt_store=receipt_store,
        session_events=session_events,
        member_events=member_events,
        start_ms=1_700_000_000_000,
    )
    producer_session, published, _values = _publish(broker)
    broker.end_session(producer_session)
    document, frozen = _freeze(broker, published)
    captured, consumer_session = _capture(broker, published, frozen)
    verified = broker.open_capture(consumer_session, captured)
    return broker, published, document, frozen, captured, consumer_session, verified


def test_public_surface_is_abstract_or_opaque_and_prepared_capture_is_private():
    trust = _trust()

    assert set(trust.__all__) == _PUBLIC_TYPES
    assert not hasattr(trust, "PreparedCapture")
    assert "_development_broker" not in trust.__all__
    assert trust.ImmutableSnapshotProvider.__abstractmethods__ == {
        "describe",
        "open_member",
    }
    assert trust.ReleaseKeyring.__abstractmethods__ == {"verify"}
    assert trust.TrustedClock.__abstractmethods__ == {"now_ms"}
    assert trust.TrustedRuntimeVerifier.__abstractmethods__ == {"verify"}
    assert trust.LifecycleAuthority.__abstractmethods__ == {
        "capture",
        "open_capture",
        "produce",
        "publish",
        "seal",
    }
    for name in _PUBLIC_TYPES:
        with pytest.raises(TypeError):
            getattr(trust, name)()


def test_exact_worm_chain_binds_receipts_and_uses_a_new_consumer_session():
    trust = _trust()
    broker, published, _document, frozen, captured, session, verified = _captured_flow()

    bindings = broker.captured_bindings(
        session,
        frozen,
        verified,
        consumer_node="consume",
        transition_nonce="nonce-consumed",
    )
    lifecycle_port = bindings.require("bundle")
    release = broker.release(session)
    receipts = broker._receipt_audit(published)
    digests = broker._receipt_digests(published)

    assert isinstance(verified, trust.VerifiedCapture)
    assert isinstance(lifecycle_port, trust.CapturedLifecyclePort)
    assert isinstance(lifecycle_port.artifact, trust.CapturedJsonArtifact)
    assert isinstance(release, trust.CapturedRelease)
    assert [receipt["event"] for receipt in receipts] == [
        "PRODUCED",
        "SEALED",
        "PUBLISHED",
        "CAPTURED",
        "CONSUMED",
    ]
    assert [receipt["sequence"] for receipt in receipts] == [1, 2, 3, 4, 5]
    assert receipts[0]["previous_receipt_sha256"] is None
    assert [
        receipt["previous_receipt_sha256"] for receipt in receipts[1:]
    ] == digests[:-1]
    assert all(receipt["producer_document_sha256"] == _SHA["producer_document"] for receipt in receipts)
    assert all(receipt["producer_run_identity"] == "producer-run" for receipt in receipts)
    assert all(receipt["producer_node"] == "publisher" for receipt in receipts)
    assert all(receipt["producer_output"] == "bundle" for receipt in receipts)
    assert all(receipt["root_ref"] == _ROOT["root_ref"] for receipt in receipts)
    assert all(receipt["root_id"] == _ROOT["root_id"] for receipt in receipts)
    assert all(receipt["snapshot_version"] == "1" for receipt in receipts)
    assert all(len(receipt["member_manifest_sha256"]) == 64 for receipt in receipts)
    assert receipts[2]["publication_receipt_sha256"]
    assert receipts[3]["consumer_captured_port"] == receipts[4]["consumer_captured_port"]
    assert receipts[3]["consumer_captured_port"] == lifecycle_port.audit["consumer_port"]
    assert receipts[0]["actor_runtime"]["run_identity"] == "producer-run"
    assert receipts[3]["actor_runtime"]["run_identity"] == "consumer-run"
    assert [receipt["transition_nonce"] for receipt in receipts] == [
        "nonce-produced",
        "nonce-sealed",
        "nonce-published",
        "nonce-captured",
        "nonce-consumed",
    ]
    assert all(receipt["key"]["usage"] == "lifecycle" for receipt in receipts)
    assert all(receipt["signature"] for receipt in receipts)
    assert all(receipt["deployment_eligible"] is False for receipt in receipts)
    assert len({receipt["issued_at_ms"] for receipt in receipts}) == 5
    assert captured is not session


def test_published_can_stop_before_any_consumer_document_or_capture():
    trust = _trust()
    broker = trust._development_broker(start_ms=1_700_000_000_000)
    producer_session, published, _values = _publish(broker)
    broker.end_session(producer_session)

    assert [row["event"] for row in broker._receipt_audit(published)] == [
        "PRODUCED",
        "SEALED",
        "PUBLISHED",
    ]
    assert broker.descriptor(published, purpose="synthetic")["root_ref"] == _ROOT["root_ref"]


def test_capture_before_frozen_document_refuses_without_member_or_session_effects():
    trust = _trust()
    session_events = []
    member_events = []
    broker = trust._development_broker(
        session_events=session_events,
        member_events=member_events,
        start_ms=1_700_000_000_000,
    )
    producer_session, published, _values = _publish(broker)
    broker.end_session(producer_session)
    prior_sessions = tuple(session_events)

    with pytest.raises((TypeError, ValueError), match="frozen|document"):
        broker.derive_consumer_port(None)
    with pytest.raises((TypeError, ValueError), match="frozen|document"):
        _capture(broker, published, None, port=None)

    assert tuple(session_events) == prior_sessions
    assert member_events == []
    assert [row["event"] for row in broker._receipt_audit(published)] == [
        "PRODUCED",
        "SEALED",
        "PUBLISHED",
    ]


def test_publish_is_required_before_capture_or_member_access():
    trust = _trust()
    member_events = []
    session_events = []
    broker = trust._development_broker(
        member_events=member_events,
        session_events=session_events,
        start_ms=1_700_000_000_000,
    )
    producer_session, prepared, _values = _produce(broker)
    sealed = broker.seal(producer_session, prepared, transition_nonce="nonce-sealed")

    with pytest.raises((TypeError, ValueError), match="PUBLISHED|published"):
        broker.descriptor(sealed, purpose="synthetic")
    with pytest.raises((TypeError, ValueError), match="PUBLISHED|published"):
        broker.capture(
            sealed,
            None,
            None,
            consumer_run_identity="consumer-run",
            process_measurement_sha256=_SHA["consumer_process"],
            runtime_sha256=_SHA["consumer_runtime"],
            transition_nonce="nonce-captured",
        )

    assert member_events == []
    assert len(session_events) == 1


@pytest.mark.parametrize(
    ("operation", "message"),
    [
        ("publish_unsealed", "SEALED|sealed"),
        ("seal_twice", "replay|SEALED|sealed"),
        ("publish_twice", "replay|PUBLISHED|published"),
        ("capture_twice", "replay|CAPTURED|captured"),
        ("duplicate_nonce", "nonce"),
    ],
)
def test_skipped_reordered_replayed_and_duplicate_nonce_transitions_refuse(
    operation,
    message,
):
    trust = _trust()
    broker = trust._development_broker(start_ms=1_700_000_000_000)
    producer_session, prepared, _values = _produce(broker)

    if operation == "publish_unsealed":
        action = lambda: broker.publish(
            producer_session,
            prepared,
            transition_nonce="nonce-published",
        )
    else:
        sealed = broker.seal(
            producer_session,
            prepared,
            transition_nonce="nonce-sealed",
        )
        if operation == "seal_twice":
            action = lambda: broker.seal(
                producer_session,
                sealed,
                transition_nonce="another-seal",
            )
        else:
            publish_nonce = (
                "nonce-sealed" if operation == "duplicate_nonce" else "nonce-published"
            )
            published = broker.publish(
                producer_session,
                sealed,
                transition_nonce=publish_nonce,
            ) if operation != "duplicate_nonce" else None
            if operation == "duplicate_nonce":
                action = lambda: broker.publish(
                    producer_session,
                    sealed,
                    transition_nonce=publish_nonce,
                )
            elif operation == "publish_twice":
                action = lambda: broker.publish(
                    producer_session,
                    published,
                    transition_nonce="another-publish",
                )
            else:
                broker.end_session(producer_session)
                _document, frozen = _freeze(broker, published)
                port = broker.derive_consumer_port(frozen)
                _captured, _consumer = _capture(
                    broker,
                    published,
                    frozen,
                    port=port,
                )
                action = lambda: _capture(
                    broker,
                    published,
                    frozen,
                    port=port,
                    nonce="another-capture",
                )

    with pytest.raises((TypeError, ValueError), match=message):
        action()


@pytest.mark.parametrize(
    ("relative_path", "message"),
    [
        (b"bytes.json", "path"),
        ("", "path"),
        (".", "path"),
        ("/absolute.json", "path"),
        ("../escape.json", "path"),
        ("a/../escape.json", "path"),
        ("a/./member.json", "path"),
        ("a//member.json", "path"),
        ("a\\member.json", "path"),
        ("a\x00member.json", "path"),
    ],
)
def test_invalid_member_paths_refuse_before_produced(relative_path, message):
    trust = _trust()
    broker = trust._development_broker(start_ms=1_700_000_000_000)
    members = _members()
    members[1]["relative_path"] = relative_path

    with pytest.raises(ValueError, match=message):
        _produce(
            broker,
            members=members,
            expected_members=("config.json", relative_path),
        )


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"file_type": "symlink"}, "regular|symlink"),
        ({"file_type": "directory"}, "regular|directory"),
        ({"link_count": 2}, "link|hard"),
        ({"unexpected": True}, "unknown|keys"),
    ],
)
def test_symlink_hardlink_nonregular_and_unknown_member_fields_refuse(change, message):
    trust = _trust()
    broker = trust._development_broker(start_ms=1_700_000_000_000)
    members = _members()
    members[1].update(change)

    with pytest.raises(ValueError, match=message):
        _produce(broker, members=members)


def test_partial_duplicate_and_reordered_member_declarations_refuse():
    trust = _trust()

    partial = trust._development_broker(start_ms=1_700_000_000_000)
    with pytest.raises(ValueError, match="complete|expected|member"):
        _produce(partial, members=_members()[:1])

    duplicate_members = _members()
    duplicate_members[1]["relative_path"] = "config.json"
    duplicate = trust._development_broker(start_ms=1_700_000_000_000)
    with pytest.raises(ValueError, match="duplicate"):
        _produce(
            duplicate,
            members=duplicate_members,
            expected_members=("config.json", "config.json"),
        )

    reordered = trust._development_broker(start_ms=1_700_000_000_000)
    with pytest.raises(ValueError, match="order|expected|member"):
        _produce(
            reordered,
            members=list(reversed(_members())),
            expected_members=("config.json", "artifacts/bundle.json"),
        )


def test_post_seal_source_mutation_refuses_publication():
    trust = _trust()
    broker = trust._development_broker(start_ms=1_700_000_000_000)
    producer_session, prepared, members = _produce(broker)
    sealed = broker.seal(
        producer_session,
        prepared,
        transition_nonce="nonce-sealed",
    )
    members[1]["bytes"][:] = _json_bytes({"rows": [{"id": "changed"}]})

    with pytest.raises(ValueError, match="mutation|changed|digest"):
        broker.publish(
            producer_session,
            sealed,
            transition_nonce="nonce-published",
        )


def test_non_worm_root_and_unsealed_capture_refuse():
    trust = _trust()
    broker = trust._development_broker(
        provider_worm=False,
        start_ms=1_700_000_000_000,
    )
    producer_session, prepared, _values = _produce(broker)
    sealed = broker.seal(
        producer_session,
        prepared,
        transition_nonce="nonce-sealed",
    )

    with pytest.raises(ValueError, match="WORM|immutable"):
        broker.publish(
            producer_session,
            sealed,
            transition_nonce="nonce-published",
        )
    with pytest.raises((TypeError, ValueError), match="PUBLISHED|published"):
        broker.descriptor(prepared, purpose="synthetic")


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("document_sha256", "self", "document_sha256|SHA-256|placeholder"),
        ("document_sha256", "0" * 64, "placeholder|document_sha256"),
        ("document_sha256", "A" * 64, "document_sha256|SHA-256"),
        ("root_ref", "capture://other/root", "published|root"),
        ("snapshot_version", "2", "published|snapshot"),
        ("purpose", "paper", "purpose"),
        ("unknown", "value", "unknown|descriptor"),
    ],
)
def test_placeholder_self_wrong_root_and_unknown_descriptor_fields_refuse(
    field,
    value,
    message,
):
    trust = _trust()
    broker = trust._development_broker(start_ms=1_700_000_000_000)
    producer_session, published, _values = _publish(broker)
    broker.end_session(producer_session)
    descriptor = broker.descriptor(published, purpose="synthetic")
    descriptor[field] = value
    document = _consumer_document(descriptor)

    with pytest.raises(ValueError, match=message):
        broker.freeze_consumer_document(
            document,
            consumer_node="consume",
            consumer_input="bundle",
            purpose="synthetic",
        )


def test_descriptor_mutation_after_freeze_refuses_before_port_derivation():
    trust = _trust()
    broker = trust._development_broker(start_ms=1_700_000_000_000)
    producer_session, published, _values = _publish(broker)
    broker.end_session(producer_session)
    document, frozen = _freeze(broker, published)
    document["pipeline"]["consume"]["inputs"]["bundle"]["$captured_artifact"][
        "output"
    ] = "substituted"

    with pytest.raises(ValueError, match="changed after freeze|mutation"):
        broker.derive_consumer_port(frozen)


def test_producer_session_must_end_and_consumer_run_and_session_must_be_distinct():
    trust = _trust()
    broker = trust._development_broker(start_ms=1_700_000_000_000)
    producer_session, published, _values = _publish(broker)
    _document, frozen = _freeze(broker, published)

    with pytest.raises(ValueError, match="producer session|end"):
        _capture(broker, published, frozen)
    broker.end_session(producer_session)
    with pytest.raises(ValueError, match="distinct|same run"):
        _capture(
            broker,
            published,
            frozen,
            run_identity="producer-run",
        )

    captured, consumer_session = _capture(broker, published, frozen)
    assert consumer_session is not producer_session
    with pytest.raises(ValueError, match="session|consumer"):
        broker.open_capture(producer_session, captured)


def test_wrong_document_port_cross_plan_and_consumer_substitution_refuse():
    trust = _trust()
    broker = trust._development_broker(start_ms=1_700_000_000_000)
    producer_session, published, _values = _publish(broker)
    broker.end_session(producer_session)
    _document, frozen = _freeze(broker, published)
    _other_document, other_frozen = _freeze(
        broker,
        published,
        document=_consumer_document(
            broker.descriptor(published, purpose="synthetic"),
            name="other-consumer",
        ),
    )
    wrong_port = broker.derive_consumer_port(other_frozen)

    with pytest.raises(ValueError, match="document|port"):
        _capture(broker, published, frozen, port=wrong_port)

    captured, consumer_session = _capture(broker, published, frozen)
    verified = broker.open_capture(consumer_session, captured)
    with pytest.raises(ValueError, match="plan|document"):
        broker.captured_bindings(
            consumer_session,
            other_frozen,
            verified,
            consumer_node="consume",
            transition_nonce="nonce-consumed",
        )
    with pytest.raises(ValueError, match="node|consumer"):
        broker.captured_bindings(
            consumer_session,
            frozen,
            verified,
            consumer_node="substituted",
            transition_nonce="nonce-consumed",
        )


def test_uncaptured_unverified_and_wrong_input_cannot_be_consumed():
    trust = _trust()
    broker = trust._development_broker(start_ms=1_700_000_000_000)
    producer_session, published, _values = _publish(broker)
    broker.end_session(producer_session)
    _document, frozen = _freeze(broker, published)

    with pytest.raises((TypeError, ValueError), match="CAPTURED|captured"):
        broker.open_capture(producer_session, published)

    captured, consumer_session = _capture(broker, published, frozen)
    with pytest.raises((TypeError, ValueError), match="verified"):
        broker.captured_bindings(
            consumer_session,
            frozen,
            captured,
            consumer_node="consume",
            transition_nonce="nonce-consumed",
        )

    verified = broker.open_capture(consumer_session, captured)
    bindings = broker.captured_bindings(
        consumer_session,
        frozen,
        verified,
        consumer_node="consume",
        transition_nonce="nonce-consumed",
    )
    with pytest.raises((KeyError, ValueError), match="input|missing"):
        bindings.require("substituted")


def test_provider_mutation_before_verification_refuses_without_a_handle():
    trust = _trust()
    storage = {}
    member_events = []
    broker = trust._development_broker(
        snapshot_storage=storage,
        member_events=member_events,
        start_ms=1_700_000_000_000,
    )
    producer_session, published, _values = _publish(broker)
    broker.end_session(producer_session)
    _document, frozen = _freeze(broker, published)
    captured, consumer_session = _capture(broker, published, frozen)
    storage[(_ROOT["root_ref"], "1", "artifacts/bundle.json")] = b'{"changed":true}'

    with pytest.raises(ValueError, match="digest|bytes|mutation"):
        broker.open_capture(consumer_session, captured)

    assert member_events


def test_verified_retained_bytes_are_not_reopened_after_toctou_change():
    storage = {}
    member_events = []
    broker, _published, _document, frozen, _captured, session, verified = (
        _captured_flow(snapshot_storage=storage, member_events=member_events)
    )
    opens_after_verify = tuple(member_events)
    storage[(_ROOT["root_ref"], "1", "artifacts/bundle.json")] = b'{"changed":true}'

    bindings = broker.captured_bindings(
        session,
        frozen,
        verified,
        consumer_node="consume",
        transition_nonce="nonce-consumed",
    )
    artifact = bindings.require("bundle").artifact

    assert artifact.value["rows"][0]["id"] == "one"
    assert tuple(member_events) == opens_after_verify


@pytest.mark.parametrize(
    "bad_bytes",
    [
        b'{"rows": NaN}',
        b'{ "rows":[]}',
        b'{"rows":[]}\n',
        b'{"rows":[],"rows":[]}',
        '{"value":"é"}'.encode(),
        b"\xff",
    ],
)
def test_noncanonical_nonfinite_or_invalid_json_member_refuses_seal(bad_bytes):
    trust = _trust()
    broker = trust._development_broker(start_ms=1_700_000_000_000)
    members = _members()
    members[1]["bytes"] = bytearray(bad_bytes)
    producer_session, prepared, _values = _produce(broker, members=members)

    with pytest.raises(ValueError, match="canonical|JSON|UTF-8|finite|duplicate"):
        broker.seal(
            producer_session,
            prepared,
            transition_nonce="nonce-sealed",
        )


def test_handles_leak_no_paths_reopen_or_serialization_interface():
    broker, _published, _document, frozen, _captured, session, verified = (
        _captured_flow()
    )
    member = verified.member("artifacts/bundle.json")
    bindings = broker.captured_bindings(
        session,
        frozen,
        verified,
        consumer_node="consume",
        transition_nonce="nonce-consumed",
    )
    port = bindings.require("bundle")
    artifact = port.artifact
    release = broker.release(session)

    for handle in (session, verified, member, bindings, port, artifact, release):
        for name in (
            "path",
            "relative_path",
            "root_ref",
            "provider",
            "reopen",
            "open",
            "to_obj",
            "from_obj",
            "__fspath__",
            "__dict__",
        ):
            assert not hasattr(handle, name), (type(handle).__name__, name)
        with pytest.raises(TypeError):
            os.fspath(handle)
        with pytest.raises((TypeError, ValueError)):
            json.dumps(handle)
        with pytest.raises(TypeError):
            dict(handle)
        with pytest.raises(TypeError):
            copy.copy(handle)
        with pytest.raises(TypeError):
            copy.deepcopy(handle)
        with pytest.raises((TypeError, pickle.PicklingError)):
            pickle.dumps(handle)


def test_member_reads_once_and_captured_json_value_and_audit_are_immutable():
    broker, _published, _document, frozen, _captured, session, verified = (
        _captured_flow()
    )
    handle = verified.member("artifacts/bundle.json")
    assert json.loads(handle.read_text())["rows"][0]["value"] == 7
    with pytest.raises(ValueError, match="consumed|read"):
        handle.read_bytes()

    bindings = broker.captured_bindings(
        session,
        frozen,
        verified,
        consumer_node="consume",
        transition_nonce="nonce-consumed",
    )
    artifact = bindings.require("bundle").artifact
    assert {
        name for name in dir(artifact) if not name.startswith("_")
    } == {"audit", "value"}
    assert isinstance(artifact.value, MappingProxyType)
    assert isinstance(artifact.audit, MappingProxyType)
    with pytest.raises(TypeError):
        artifact.value["new"] = "changed"
    with pytest.raises(TypeError):
        artifact.value["rows"][0]["id"] = "changed"
    assert "relative_path" not in artifact.audit


def test_bindings_are_non_enumerable_exact_and_one_way():
    trust = _trust()
    broker, _published, _document, frozen, _captured, session, verified = (
        _captured_flow()
    )
    bindings = broker.captured_bindings(
        session,
        frozen,
        verified,
        consumer_node="consume",
        transition_nonce="nonce-consumed",
    )

    assert not hasattr(bindings, "keys")
    assert not hasattr(bindings, "items")
    with pytest.raises(TypeError):
        iter(bindings)
    port = bindings.require("bundle")
    assert isinstance(port, trust.CapturedLifecyclePort)
    with pytest.raises(ValueError, match="consumed|one-way|replay"):
        bindings.require("bundle")


def test_release_refuses_before_consumption_and_second_release_refuses():
    trust = _trust()
    broker, _published, _document, frozen, _captured, session, verified = (
        _captured_flow()
    )
    bindings = broker.captured_bindings(
        session,
        frozen,
        verified,
        consumer_node="consume",
        transition_nonce="nonce-consumed",
    )
    with pytest.raises(ValueError, match="consume|CONSUMED"):
        broker.release(session)
    bindings.require("bundle")
    release = broker.release(session)
    assert isinstance(release, trust.CapturedRelease)
    with pytest.raises(ValueError, match="released|replay"):
        broker.release(session)


@pytest.mark.parametrize("attack", ["signature", "reorder"])
def test_recovery_refuses_tampered_or_reordered_receipts(attack):
    receipt_store = {}
    broker, published, _document, frozen, _captured, session, verified = (
        _captured_flow(receipt_store=receipt_store)
    )
    bindings = broker.captured_bindings(
        session,
        frozen,
        verified,
        consumer_node="consume",
        transition_nonce="nonce-consumed",
    )
    bindings.require("bundle")
    stream_id = broker._receipt_audit(published)[0]["stream_id"]
    records = list(receipt_store[stream_id])
    if attack == "signature":
        changed = json.loads(records[2])
        changed["signature"] = "0" * 64
        records[2] = _json_bytes(changed)
    else:
        records[1], records[2] = records[2], records[1]
    receipt_store[stream_id] = tuple(records)

    with pytest.raises(ValueError, match="signature|sequence|predecessor|order"):
        broker._recover(stream_id)


def test_identical_development_clock_keyring_and_inputs_make_identical_receipts():
    trust = _trust()

    def run():
        broker = trust._development_broker(start_ms=1_700_000_000_000)
        producer_session, published, _values = _publish(broker)
        broker.end_session(producer_session)
        _document, frozen = _freeze(broker, published)
        _captured, session = _capture(broker, published, frozen)
        verified = broker.open_capture(session, _captured)
        bindings = broker.captured_bindings(
            session,
            frozen,
            verified,
            consumer_node="consume",
            transition_nonce="nonce-consumed",
        )
        bindings.require("bundle")
        canonical_receipts = [
            _json_bytes(dict(row)) for row in broker._receipt_audit(published)
        ]
        return [hashlib.sha256(value).hexdigest() for value in canonical_receipts]

    assert run() == run()


def test_post_seal_path_and_type_mutation_refuses_publication():
    trust = _trust()
    storage = {}
    broker = trust._development_broker(
        snapshot_storage=storage,
        start_ms=1_700_000_000_000,
    )
    producer_session, prepared, members = _produce(broker)
    sealed = broker.seal(
        producer_session,
        prepared,
        transition_nonce="nonce-sealed",
    )
    members[1]["relative_path"] = "../escape.json"
    members[1]["file_type"] = "symlink"
    published = broker.publish(
        producer_session,
        sealed,
        transition_nonce="nonce-published",
    )
    paths = {key[2] for key in storage}

    assert "../escape.json" not in paths
    assert paths == {"config.json", "artifacts/bundle.json"}
    assert broker.descriptor(published, purpose="synthetic")["root_ref"] == _ROOT["root_ref"]


def test_failed_publish_does_not_poison_worm_storage_or_block_retry():
    trust = _trust()
    storage = {}
    broker = trust._development_broker(
        snapshot_storage=storage,
        start_ms=1_700_000_000_000,
    )
    producer_session, prepared, members = _produce(broker)
    sealed = broker.seal(
        producer_session,
        prepared,
        transition_nonce="nonce-sealed",
    )
    members[1]["bytes"][:] = _json_bytes({"rows": [{"id": "changed"}]})

    with pytest.raises(ValueError, match="mutation|digest"):
        broker.publish(
            producer_session,
            sealed,
            transition_nonce="nonce-published",
        )
    assert storage == {}
    members[1]["bytes"][:] = _json_bytes({"rows": [{"id": "one", "value": 7}]})
    published = broker.publish(
        producer_session,
        sealed,
        transition_nonce="nonce-published",
    )
    assert broker.descriptor(published, purpose="synthetic")["root_ref"] == _ROOT["root_ref"]


def test_consume_decodes_verified_retained_bytes_not_seal_cache():
    broker, published, _document, frozen, _captured, session, verified = (
        _captured_flow()
    )
    published.sealed.parsed["artifacts/bundle.json"]["rows"][0]["id"] = "PWNED"
    bindings = broker.captured_bindings(
        session,
        frozen,
        verified,
        consumer_node="consume",
        transition_nonce="nonce-consumed",
    )
    artifact = bindings.require("bundle").artifact
    member = verified.member("artifacts/bundle.json")

    assert artifact.value["rows"][0]["id"] == "one"
    assert json.loads(member.read_text())["rows"][0]["id"] == "one"


def test_consumer_port_is_rederived_and_freeze_token_mutation_refuses():
    trust = _trust()
    broker = trust._development_broker(start_ms=1_700_000_000_000)
    producer_session, published, _values = _publish(broker)
    broker.end_session(producer_session)
    _document, frozen = _freeze(broker, published)
    frozen.port["consumer_input"] = "substituted"
    port = broker.derive_consumer_port(frozen)
    assert port["consumer_input"] == "bundle"
    with pytest.raises((TypeError, ValueError, AttributeError)):
        frozen.consumer_input = "other"


def test_publication_receipt_digest_binds_later_receipts():
    broker, published, _document, frozen, captured, session, verified = (
        _captured_flow()
    )
    bindings = broker.captured_bindings(
        session,
        frozen,
        verified,
        consumer_node="consume",
        transition_nonce="nonce-consumed",
    )
    bindings.require("bundle")
    receipts = broker._receipt_audit(published)
    field = receipts[2]["publication_receipt_sha256"]
    unsigned = {
        key: value
        for key, value in receipts[2].items()
        if key not in {"signature", "publication_receipt_sha256"}
    }
    weak = hashlib.sha256(
        _json_bytes(
            {
                "event": "PUBLISHED",
                "root_id": _ROOT["root_id"],
                "stream_id": receipts[2]["stream_id"],
            }
        )
    ).hexdigest()
    signed = {
        key: value
        for key, value in receipts[2].items()
        if key != "signature"
    }

    assert field
    assert field != weak
    assert "publication_receipt_sha256" in signed
    assert field == hashlib.sha256(_json_bytes(unsigned)).hexdigest()
    assert receipts[3]["publication_receipt_sha256"] == field
    assert receipts[4]["publication_receipt_sha256"] == field


def test_live_cas_refuses_when_worm_store_drops_published():
    trust = _trust()
    receipt_store = {}
    broker = trust._development_broker(
        receipt_store=receipt_store,
        start_ms=1_700_000_000_000,
    )
    producer_session, published, _values = _publish(broker)
    broker.end_session(producer_session)
    _document, frozen = _freeze(broker, published)
    stream_id = broker._receipt_audit(published)[0]["stream_id"]
    receipt_store[stream_id] = receipt_store[stream_id][:2]

    with pytest.raises(ValueError, match="PUBLISHED|store|predecessor|sequence"):
        _capture(broker, published, frozen)


def test_same_run_refusal_uses_producer_launch_session_identity():
    trust = _trust()
    broker = trust._development_broker(start_ms=1_700_000_000_000)
    session = broker.start_producer_session(
        run_identity="producer-run",
        process_measurement_sha256=_SHA["producer_process"],
        runtime_sha256=_SHA["producer_runtime"],
        plan_sha256=_SHA["producer_plan"],
    )
    producer = dict(_PRODUCER)
    producer["run_identity"] = "alice-producer"
    prepared = broker.produce(
        session,
        producer=producer,
        root=dict(_ROOT),
        purpose="synthetic",
        expected_members=("config.json", "artifacts/bundle.json"),
        members=_members(),
        output_member="artifacts/bundle.json",
        completed=True,
        planned=True,
        transition_nonce="nonce-produced",
    )
    sealed = broker.seal(session, prepared, transition_nonce="nonce-sealed")
    published = broker.publish(session, sealed, transition_nonce="nonce-published")
    broker.end_session(session)
    _document, frozen = _freeze(broker, published)

    with pytest.raises(ValueError, match="distinct|same run|session"):
        _capture(broker, published, frozen, run_identity="producer-run")


def test_bindings_and_release_are_bound_to_the_capture_session():
    trust = _trust()
    broker = trust._development_broker(start_ms=1_700_000_000_000)
    first = _publish(
        broker,
        produced_nonce="nonce-produced-a",
        sealed_nonce="nonce-sealed-a",
        published_nonce="nonce-published-a",
    )
    producer_a, published_a, _values_a = first
    broker.end_session(producer_a)
    _document_a, frozen_a = _freeze(broker, published_a)
    captured_a, session_a = _capture(
        broker,
        published_a,
        frozen_a,
        run_identity="consumer-a",
        nonce="nonce-captured-a",
    )
    verified_a = broker.open_capture(session_a, captured_a)

    producer_b = broker.start_producer_session(
        run_identity="producer-b",
        process_measurement_sha256=_SHA["producer_process"],
        runtime_sha256=_SHA["producer_runtime"],
        plan_sha256=_SHA["producer_plan"],
    )
    producer_b_ids = dict(_PRODUCER)
    producer_b_ids["run_identity"] = "producer-b"
    root_b = {
        "root_ref": "capture://synthetic/root-b",
        "root_id": "8" * 64,
        "snapshot_version": "1",
    }
    prepared_b = broker.produce(
        producer_b,
        producer=producer_b_ids,
        root=root_b,
        purpose="synthetic",
        expected_members=("config.json", "artifacts/bundle.json"),
        members=_members(),
        output_member="artifacts/bundle.json",
        completed=True,
        planned=True,
        transition_nonce="nonce-produced-b",
    )
    sealed_b = broker.seal(
        producer_b,
        prepared_b,
        transition_nonce="nonce-sealed-b",
    )
    published_b = broker.publish(
        producer_b,
        sealed_b,
        transition_nonce="nonce-published-b",
    )
    broker.end_session(producer_b)
    _document_b, frozen_b = _freeze(
        broker,
        published_b,
        document=_consumer_document(
            broker.descriptor(published_b, purpose="synthetic"),
            name="consumer-b-doc",
        ),
    )
    captured_b, session_b = _capture(
        broker,
        published_b,
        frozen_b,
        run_identity="consumer-b",
        nonce="nonce-captured-b",
    )
    verified_b = broker.open_capture(session_b, captured_b)

    with pytest.raises(ValueError, match="session|consumer"):
        broker.captured_bindings(
            session_b,
            frozen_a,
            verified_a,
            consumer_node="consume",
            transition_nonce="nonce-consumed-cross",
        )

    bindings_a = broker.captured_bindings(
        session_a,
        frozen_a,
        verified_a,
        consumer_node="consume",
        transition_nonce="nonce-consumed-a",
    )
    bindings_a.require("bundle")
    bindings_b = broker.captured_bindings(
        session_b,
        frozen_b,
        verified_b,
        consumer_node="consume",
        transition_nonce="nonce-consumed-b",
    )
    bindings_b.require("bundle")
    release_a = broker.release(session_a)
    release_b = broker.release(session_b)
    stream_a = broker._receipt_audit(published_a)[0]["stream_id"]
    stream_b = broker._receipt_audit(published_b)[0]["stream_id"]

    assert release_a._stream_id == stream_a
    assert release_b._stream_id == stream_b
    assert stream_a != stream_b


def test_sealed_digest_oracle_mutation_cannot_publish_hostile_members():
    trust = _trust()
    storage = {}
    broker = trust._development_broker(
        snapshot_storage=storage,
        start_ms=1_700_000_000_000,
    )
    producer_session, prepared, members = _produce(broker)
    sealed = broker.seal(
        producer_session,
        prepared,
        transition_nonce="nonce-sealed",
    )
    original_bundle = bytes(members[1]["bytes"])
    hostile = _json_bytes({"rows": [{"id": "PWNED"}]})
    mutated = False
    try:
        sealed.digests[1]["relative_path"] = "../escape.json"
        sealed.digests[1]["sha256"] = hashlib.sha256(hostile).hexdigest()
        sealed.digests[1]["bytes"] = len(hostile)
        mutated = True
    except (TypeError, ValueError, AttributeError):
        pass
    members[1]["bytes"][:] = hostile
    if mutated:
        with pytest.raises((TypeError, ValueError, AttributeError)):
            broker.publish(
                producer_session,
                sealed,
                transition_nonce="nonce-published",
            )
        assert "../escape.json" not in {key[2] for key in storage}
    members[1]["bytes"][:] = original_bundle
    published = broker.publish(
        producer_session,
        sealed,
        transition_nonce="nonce-published",
    )
    paths = {key[2] for key in storage}

    assert "../escape.json" not in paths
    assert paths == {"config.json", "artifacts/bundle.json"}
    assert broker.descriptor(published, purpose="synthetic")["root_ref"] == _ROOT["root_ref"]


def test_duplicate_publish_nonce_does_not_write_members_or_block_retry():
    trust = _trust()
    storage = {}
    broker = trust._development_broker(
        snapshot_storage=storage,
        start_ms=1_700_000_000_000,
    )
    producer_session, prepared, _values = _produce(broker)
    sealed = broker.seal(
        producer_session,
        prepared,
        transition_nonce="nonce-sealed",
    )

    with pytest.raises(ValueError, match="nonce"):
        broker.publish(
            producer_session,
            sealed,
            transition_nonce="nonce-sealed",
        )
    assert storage == {}
    published = broker.publish(
        producer_session,
        sealed,
        transition_nonce="nonce-published",
    )
    paths = {key[2] for key in storage}

    assert paths == {"config.json", "artifacts/bundle.json"}
    assert broker.descriptor(published, purpose="synthetic")["root_ref"] == _ROOT["root_ref"]


def test_publication_receipt_identity_is_hmac_bound_and_required_on_recover():
    trust = _trust()
    receipt_store = {}
    broker = trust._development_broker(
        receipt_store=receipt_store,
        start_ms=1_700_000_000_000,
    )
    producer_session, published, _values = _publish(broker)
    broker.end_session(producer_session)
    _document, frozen = _freeze(broker, published)
    stream_id = broker._receipt_audit(published)[0]["stream_id"]
    original = tuple(receipt_store[stream_id])
    published_receipt = json.loads(original[2].decode("ascii"))
    field = published_receipt["publication_receipt_sha256"]
    unsigned = {
        key: value
        for key, value in published_receipt.items()
        if key not in {"signature", "publication_receipt_sha256"}
    }
    signed = {
        key: value
        for key, value in published_receipt.items()
        if key != "signature"
    }

    stripped = dict(published_receipt)
    del stripped["publication_receipt_sha256"]
    receipt_store[stream_id] = original[:2] + (_json_bytes(stripped),) + original[3:]
    with pytest.raises(ValueError, match="publication|identity|signature|store"):
        _capture(broker, published, frozen)

    tampered = dict(published_receipt)
    tampered["publication_receipt_sha256"] = "0" * 64
    receipt_store[stream_id] = original[:2] + (_json_bytes(tampered),) + original[3:]
    with pytest.raises(ValueError, match="publication|identity|signature|store"):
        _capture(broker, published, frozen)

    receipt_store[stream_id] = original
    captured, session = _capture(broker, published, frozen)
    verified = broker.open_capture(session, captured)
    bindings = broker.captured_bindings(
        session,
        frozen,
        verified,
        consumer_node="consume",
        transition_nonce="nonce-consumed",
    )
    bindings.require("bundle")
    receipts = broker._receipt_audit(published)

    assert "publication_receipt_sha256" in signed
    assert field == hashlib.sha256(_json_bytes(unsigned)).hexdigest()
    assert receipts[3]["publication_receipt_sha256"] == field
    assert receipts[4]["publication_receipt_sha256"] == field
    assert receipts[3]["previous_receipt_sha256"] == hashlib.sha256(original[2]).hexdigest()


def test_deleted_receipt_store_key_refuses_capture_of_in_memory_published():
    trust = _trust()
    receipt_store = {}
    broker = trust._development_broker(
        receipt_store=receipt_store,
        start_ms=1_700_000_000_000,
    )
    producer_session, published, _values = _publish(broker)
    broker.end_session(producer_session)
    _document, frozen = _freeze(broker, published)
    stream_id = broker._receipt_audit(published)[0]["stream_id"]
    del receipt_store[stream_id]

    with pytest.raises(ValueError, match="PUBLISHED|store|predecessor|sequence"):
        _capture(broker, published, frozen)


def test_published_sealed_swap_cannot_consume_another_stream():
    trust = _trust()
    broker = trust._development_broker(start_ms=1_700_000_000_000)
    session_a, published_a, _values_a = _publish(
        broker,
        members=_members({"rows": [{"id": "AAA"}]}),
    )
    broker.end_session(session_a)
    session_b = broker.start_producer_session(
        run_identity="producer-b",
        process_measurement_sha256=_SHA["producer_process"],
        runtime_sha256=_SHA["producer_runtime"],
        plan_sha256=_SHA["producer_plan"],
    )
    producer_b = dict(_PRODUCER)
    producer_b["run_identity"] = "producer-b"
    prepared_b = broker.produce(
        session_b,
        producer=producer_b,
        root={
            "root_ref": "capture://synthetic/root-b",
            "root_id": "8" * 64,
            "snapshot_version": "1",
        },
        purpose="synthetic",
        expected_members=("config.json", "artifacts/bundle.json"),
        members=_members({"rows": [{"id": "BBB-SUBSTITUTED"}]}),
        output_member="artifacts/bundle.json",
        completed=True,
        planned=True,
        transition_nonce="nonce-produced-b",
    )
    sealed_b = broker.seal(session_b, prepared_b, transition_nonce="nonce-sealed-b")
    broker.publish(session_b, sealed_b, transition_nonce="nonce-published-b")
    broker.end_session(session_b)
    _document, frozen = _freeze(broker, published_a)
    try:
        published_a.sealed = sealed_b
    except (TypeError, ValueError, AttributeError):
        pass
    captured, session = _capture(broker, published_a, frozen)
    verified = broker.open_capture(session, captured)
    artifact = broker.captured_bindings(
        session,
        frozen,
        verified,
        consumer_node="consume",
        transition_nonce="nonce-consumed",
    ).require("bundle").artifact

    assert artifact.value["rows"][0]["id"] == "AAA"
    assert broker.descriptor(published_a, purpose="synthetic")["root_ref"] == _ROOT["root_ref"]


def test_truncated_sealed_members_cannot_publish_partial_worm_snapshot():
    trust = _trust()
    storage = {}
    broker = trust._development_broker(
        snapshot_storage=storage,
        start_ms=1_700_000_000_000,
    )
    producer_session, prepared, _values = _produce(broker)
    sealed = broker.seal(
        producer_session,
        prepared,
        transition_nonce="nonce-sealed",
    )
    truncated = False
    try:
        sealed.prepared.members.pop()
        truncated = True
    except (TypeError, ValueError, AttributeError):
        try:
            sealed.prepared.members = list(sealed.prepared.members)[:1]
            truncated = True
        except (TypeError, ValueError, AttributeError):
            pass
    if truncated:
        with pytest.raises((TypeError, ValueError, AttributeError)):
            broker.publish(
                producer_session,
                sealed,
                transition_nonce="nonce-published",
            )
        assert {key[2] for key in storage} != {"config.json"}
        assert storage == {}
        return
    published = broker.publish(
        producer_session,
        sealed,
        transition_nonce="nonce-published",
    )
    paths = {key[2] for key in storage}

    assert paths == {"config.json", "artifacts/bundle.json"}
    assert broker.descriptor(published, purpose="synthetic")["root_ref"] == _ROOT["root_ref"]


def test_captured_published_swap_cannot_consume_another_stream():
    trust = _trust()
    broker = trust._development_broker(start_ms=1_700_000_000_000)
    session_a, published_a, _values = _publish(
        broker,
        members=_members({"rows": [{"id": "AAA"}]}),
    )
    broker.end_session(session_a)
    published_b, _sealed_b = _foreign_publish(broker)
    _document, frozen = _freeze(broker, published_a)
    captured, session = _capture(broker, published_a, frozen)
    try:
        captured.published = published_b
    except (TypeError, ValueError, AttributeError):
        object.__setattr__(captured, "published", published_b)
    verified = broker.open_capture(session, captured)
    artifact = broker.captured_bindings(
        session,
        frozen,
        verified,
        consumer_node="consume",
        transition_nonce="nonce-consumed",
    ).require("bundle").artifact

    assert artifact.value["rows"][0]["id"] == "AAA"
    assert broker._receipt_audit(published_a)[-1]["event"] == "CONSUMED"
    assert broker._receipt_audit(published_b)[-1]["event"] == "PUBLISHED"


def test_sealed_slot_setattr_cannot_consume_another_stream():
    trust = _trust()
    broker = trust._development_broker(start_ms=1_700_000_000_000)
    session_a, published_a, _values = _publish(
        broker,
        members=_members({"rows": [{"id": "AAA"}]}),
    )
    broker.end_session(session_a)
    _published_b, sealed_b = _foreign_publish(broker)
    _document, frozen = _freeze(broker, published_a)
    object.__setattr__(published_a, "_sealed", sealed_b)
    captured, session = _capture(broker, published_a, frozen)
    verified = broker.open_capture(session, captured)
    artifact = broker.captured_bindings(
        session,
        frozen,
        verified,
        consumer_node="consume",
        transition_nonce="nonce-consumed",
    ).require("bundle").artifact

    assert artifact.value["rows"][0]["id"] == "AAA"


def test_frozen_published_setattr_cannot_capture_another_stream():
    trust = _trust()
    broker = trust._development_broker(start_ms=1_700_000_000_000)
    session_a, published_a, _values = _publish(
        broker,
        members=_members({"rows": [{"id": "AAA"}]}),
    )
    broker.end_session(session_a)
    published_b, _sealed_b = _foreign_publish(broker)
    _document, frozen = _freeze(broker, published_a)
    object.__setattr__(frozen, "published", published_b)

    with pytest.raises(ValueError, match="published|document|port|root"):
        _capture(broker, published_b, frozen)
    captured, session = _capture(broker, published_a, frozen)
    verified = broker.open_capture(session, captured)
    artifact = broker.captured_bindings(
        session,
        frozen,
        verified,
        consumer_node="consume",
        transition_nonce="nonce-consumed",
    ).require("bundle").artifact

    assert artifact.value["rows"][0]["id"] == "AAA"


def test_sealed_prepared_setattr_cannot_move_consumed_cas():
    trust = _trust()
    broker = trust._development_broker(start_ms=1_700_000_000_000)
    session_a, published_a, _values = _publish(
        broker,
        members=_members({"rows": [{"id": "AAA"}]}),
    )
    broker.end_session(session_a)
    published_b, sealed_b = _foreign_publish(broker)
    _document, frozen = _freeze(broker, published_a)
    captured, session = _capture(broker, published_a, frozen)
    verified = broker.open_capture(session, captured)
    object.__setattr__(published_a.sealed, "prepared", sealed_b.prepared)
    artifact = broker.captured_bindings(
        session,
        frozen,
        verified,
        consumer_node="consume",
        transition_nonce="nonce-consumed",
    ).require("bundle").artifact

    assert artifact.value["rows"][0]["id"] == "AAA"
    assert broker._receipt_audit(published_a)[-1]["event"] == "CONSUMED"
    assert broker._receipt_audit(published_b)[-1]["event"] == "PUBLISHED"


def test_verified_slots_cannot_consume_another_stream():
    trust = _trust()
    broker = trust._development_broker(start_ms=1_700_000_000_000)
    session_prod_a, published_a, _values = _publish(
        broker,
        members=_members({"rows": [{"id": "AAA"}]}),
    )
    broker.end_session(session_prod_a)
    published_b, _sealed_b = _foreign_publish(broker)
    _document_a, frozen_a = _freeze(broker, published_a)
    _document_b, frozen_b = _freeze(
        broker,
        published_b,
        document=_consumer_document(
            broker.descriptor(published_b, purpose="synthetic"),
            name="consumer-b-doc",
        ),
    )
    captured_a, session_a = _capture(
        broker,
        published_a,
        frozen_a,
        run_identity="consumer-a",
        nonce="nonce-captured-a",
    )
    captured_b, session_b = _capture(
        broker,
        published_b,
        frozen_b,
        run_identity="consumer-b",
        nonce="nonce-captured-b",
    )
    verified_a = broker.open_capture(session_a, captured_a)
    verified_b = broker.open_capture(session_b, captured_b)
    try:
        verified_a._published = published_b
        verified_a._frozen = frozen_b
        verified_a._retained = dict(verified_b._retained)
    except (TypeError, ValueError, AttributeError):
        object.__setattr__(verified_a, "_published", published_b)
        object.__setattr__(verified_a, "_frozen", frozen_b)
        object.__setattr__(verified_a, "_retained", dict(verified_b._retained))
    with pytest.raises(ValueError, match="plan|session|freeze|capture|document"):
        broker.captured_bindings(
            session_a,
            frozen_b,
            verified_a,
            consumer_node="consume",
            transition_nonce="nonce-consumed-cross",
        )
    artifact = broker.captured_bindings(
        session_a,
        frozen_a,
        verified_a,
        consumer_node="consume",
        transition_nonce="nonce-consumed-a",
    ).require("bundle").artifact

    assert artifact.value["rows"][0]["id"] == "AAA"
    assert broker._receipt_audit(published_a)[-1]["event"] == "CONSUMED"
    assert broker._receipt_audit(published_b)[-1]["event"] == "CAPTURED"


def test_require_closure_subject_cannot_move_consumed_cas():
    trust = _trust()
    broker = trust._development_broker(start_ms=1_700_000_000_000)
    session_a, published_a, _values = _publish(
        broker,
        members=_members({"rows": [{"id": "AAA"}]}),
    )
    broker.end_session(session_a)
    published_b, sealed_b = _foreign_publish(broker)
    _document_a, frozen_a = _freeze(broker, published_a)
    _document_b, frozen_b = _freeze(
        broker,
        published_b,
        document=_consumer_document(
            broker.descriptor(published_b, purpose="synthetic"),
            name="consumer-b-doc",
        ),
    )
    captured, session = _capture(broker, published_a, frozen_a)
    _captured_b, _session_b = _capture(
        broker,
        published_b,
        frozen_b,
        run_identity="consumer-b",
        nonce="nonce-captured-b",
    )
    verified = broker.open_capture(session, captured)
    bindings = broker.captured_bindings(
        session,
        frozen_a,
        verified,
        consumer_node="consume",
        transition_nonce="nonce-consumed",
    )
    on_require = getattr(bindings, "_on_require", None)
    if on_require is not None and on_require.__closure__:
        for cell, name in zip(on_require.__closure__, on_require.__code__.co_freevars):
            if name == "subject":
                cell.cell_contents = sealed_b.prepared
    artifact = bindings.require("bundle").artifact

    assert artifact.value["rows"][0]["id"] == "AAA"
    assert broker._receipt_audit(published_a)[-1]["event"] == "CONSUMED"
    assert broker._receipt_audit(published_b)[-1]["event"] == "CAPTURED"


def test_verified_port_cannot_forge_consumed_document_hash():
    trust = _trust()
    broker = trust._development_broker(start_ms=1_700_000_000_000)
    session_prod_a, published_a, _values = _publish(
        broker,
        members=_members({"rows": [{"id": "AAA"}]}),
    )
    broker.end_session(session_prod_a)
    published_b, _sealed_b = _foreign_publish(broker)
    _document_a, frozen_a = _freeze(broker, published_a)
    _document_b, frozen_b = _freeze(
        broker,
        published_b,
        document=_consumer_document(
            broker.descriptor(published_b, purpose="synthetic"),
            name="consumer-b-doc",
        ),
    )
    captured, session = _capture(
        broker,
        published_a,
        frozen_a,
        run_identity="consumer-a",
        nonce="nonce-captured-a",
    )
    verified = broker.open_capture(session, captured)
    foreign_port = dict(broker.derive_consumer_port(frozen_b))
    try:
        verified._port.clear()
        verified._port.update(foreign_port)
    except (TypeError, ValueError, AttributeError):
        object.__setattr__(verified, "_port", foreign_port)
    bindings = broker.captured_bindings(
        session,
        frozen_a,
        verified,
        consumer_node="consume",
        transition_nonce="nonce-consumed-a",
    )
    artifact = bindings.require("bundle").artifact
    consumed = broker._receipt_audit(published_a)[-1]

    assert artifact.value["rows"][0]["id"] == "AAA"
    assert consumed["event"] == "CONSUMED"
    assert consumed["consumer_captured_port"]["consumer_document_sha256"] == frozen_a.source_sha256


def test_require_defaults_cannot_move_consumed_cas():
    trust = _trust()
    broker = trust._development_broker(start_ms=1_700_000_000_000)
    (
        published_a,
        published_b,
        _frozen_a,
        _frozen_b,
        session_a,
        session_b,
        bindings_a,
        bindings_b,
    ) = _two_captured_consumers(broker)
    on_a = getattr(bindings_a, "_on_require", None)
    on_b = getattr(bindings_b, "_on_require", None)
    if callable(on_a) and callable(on_b) and on_a.__defaults__ and on_b.__defaults__:
        on_a.__defaults__ = on_b.__defaults__
    if callable(on_a):
        stream_b = broker._receipt_audit(published_b)[0]["stream_id"]
        try:
            on_a("bundle", stream_id=stream_b)
        except TypeError:
            pass
    artifact = bindings_a.require("bundle").artifact
    release_a = broker.release(session_a)

    assert artifact.value["rows"][0]["id"] == "AAA"
    assert broker._receipt_audit(published_a)[-1]["event"] == "CONSUMED"
    assert broker._receipt_audit(published_b)[-1]["event"] == "CAPTURED"
    assert release_a._stream_id == broker._receipt_audit(published_a)[0]["stream_id"]
    with pytest.raises(ValueError, match="CONSUMED|required"):
        broker.release(session_b)


def test_require_port_freevar_cannot_forge_consumed_document_hash():
    trust = _trust()
    broker = trust._development_broker(start_ms=1_700_000_000_000)
    (
        published_a,
        published_b,
        frozen_a,
        frozen_b,
        _session_a,
        _session_b,
        bindings_a,
        _bindings_b,
    ) = _two_captured_consumers(broker)
    on_require = getattr(bindings_a, "_on_require", None)
    if on_require is not None and on_require.__closure__:
        for cell, name in zip(on_require.__closure__, on_require.__code__.co_freevars):
            if name == "expected_port":
                cell.cell_contents["consumer_document_sha256"] = frozen_b.source_sha256
    artifact = bindings_a.require("bundle").artifact
    consumed = broker._receipt_audit(published_a)[-1]

    assert artifact.value["rows"][0]["id"] == "AAA"
    assert consumed["event"] == "CONSUMED"
    assert consumed["consumer_captured_port"]["consumer_document_sha256"] == frozen_a.source_sha256
    assert broker._receipt_audit(published_b)[-1]["event"] == "CAPTURED"


def test_require_session_freevar_cannot_forge_consumed_actor():
    trust = _trust()
    broker = trust._development_broker(start_ms=1_700_000_000_000)
    (
        published_a,
        published_b,
        _frozen_a,
        _frozen_b,
        session_a,
        session_b,
        bindings_a,
        _bindings_b,
    ) = _two_captured_consumers(broker)
    on_require = getattr(bindings_a, "_on_require", None)
    if on_require is not None and on_require.__closure__:
        for cell, name in zip(on_require.__closure__, on_require.__code__.co_freevars):
            if name == "session":
                cell.cell_contents = session_b
            if name == "transition_nonce":
                cell.cell_contents = "nonce-hostile"
    artifact = bindings_a.require("bundle").artifact
    consumed = broker._receipt_audit(published_a)[-1]
    release_a = broker.release(session_a)

    assert artifact.value["rows"][0]["id"] == "AAA"
    assert consumed["event"] == "CONSUMED"
    assert consumed["actor_runtime"]["run_identity"] == "consumer-a"
    assert consumed["transition_nonce"] == "nonce-consumed-a"
    assert release_a._stream_id == broker._receipt_audit(published_a)[0]["stream_id"]
    assert broker._receipt_audit(published_b)[-1]["event"] == "CAPTURED"
    with pytest.raises(ValueError, match="CONSUMED|required"):
        broker.release(session_b)


def _poke_attr(target, name, value):
    try:
        object.__setattr__(target, "_locked", False)
    except (TypeError, AttributeError):
        pass
    try:
        object.__setattr__(target, name, value)
        return True
    except (TypeError, AttributeError):
        return False


def test_bindings_pin_stream_id_cannot_move_consumed_cas():
    trust = _trust()
    broker = trust._development_broker(start_ms=1_700_000_000_000)
    (
        published_a,
        published_b,
        _frozen_a,
        _frozen_b,
        session_a,
        session_b,
        bindings_a,
        _bindings_b,
    ) = _two_captured_consumers(broker)
    pin = broker._bindings_pin.get(id(bindings_a))
    sid_b = broker._receipt_audit(published_b)[0]["stream_id"]
    if pin is not None and not isinstance(pin, tuple):
        _poke_attr(pin, "stream_id", sid_b)
    artifact = bindings_a.require("bundle").artifact
    release_a = broker.release(session_a)

    assert artifact.value["rows"][0]["id"] == "AAA"
    assert broker._receipt_audit(published_a)[-1]["event"] == "CONSUMED"
    assert broker._receipt_audit(published_b)[-1]["event"] == "CAPTURED"
    assert release_a._stream_id == broker._receipt_audit(published_a)[0]["stream_id"]
    with pytest.raises(ValueError, match="CONSUMED|required"):
        broker.release(session_b)


def test_bindings_pin_and_runtime_cannot_forge_consumed_identity():
    trust = _trust()
    broker = trust._development_broker(start_ms=1_700_000_000_000)
    (
        published_a,
        published_b,
        frozen_a,
        frozen_b,
        session_a,
        session_b,
        bindings_a,
        _bindings_b,
    ) = _two_captured_consumers(broker)
    pin = broker._bindings_pin.get(id(bindings_a))
    foreign_port = dict(broker.derive_consumer_port(frozen_b))
    if pin is not None and not isinstance(pin, tuple):
        _poke_attr(pin, "port", foreign_port)
        _poke_attr(pin, "session", session_b)
        _poke_attr(pin, "nonce", "nonce-hostile")
    try:
        session_a._runtime["run_identity"] = "consumer-b"
    except (TypeError, AttributeError):
        object.__setattr__(session_a, "_runtime", {"run_identity": "consumer-b"})
    artifact = bindings_a.require("bundle").artifact
    consumed = broker._receipt_audit(published_a)[-1]
    release_a = broker.release(session_a)

    assert artifact.value["rows"][0]["id"] == "AAA"
    assert consumed["event"] == "CONSUMED"
    assert consumed["consumer_captured_port"]["consumer_document_sha256"] == frozen_a.source_sha256
    assert consumed["actor_runtime"]["run_identity"] == "consumer-a"
    assert consumed["transition_nonce"] == "nonce-consumed-a"
    assert release_a._stream_id == broker._receipt_audit(published_a)[0]["stream_id"]
    assert broker._receipt_audit(published_b)[-1]["event"] == "CAPTURED"
    with pytest.raises(ValueError, match="CONSUMED|required"):
        broker.release(session_b)


def test_release_requires_this_session_stream_consumed():
    trust = _trust()
    broker = trust._development_broker(start_ms=1_700_000_000_000)
    (
        published_a,
        published_b,
        _frozen_a,
        _frozen_b,
        session_a,
        session_b,
        bindings_a,
        _bindings_b,
    ) = _two_captured_consumers(broker)
    broker._used_inputs[id(session_a)] = {"bundle"}
    with pytest.raises(ValueError, match="CONSUMED|required"):
        broker.release(session_a)
    assert broker._receipt_audit(published_a)[-1]["event"] == "CAPTURED"
    del broker._used_inputs[id(session_a)]
    bindings_a.require("bundle")
    sid_b = broker._receipt_audit(published_b)[0]["stream_id"]
    broker._session_streams[id(session_a)] = sid_b
    with pytest.raises(ValueError, match="CONSUMED|required"):
        broker.release(session_a)
    assert broker._receipt_audit(published_b)[-1]["event"] == "CAPTURED"


def _consume_or_refuse(bindings):
    try:
        return bindings.require("bundle").artifact
    except (TypeError, ValueError, AttributeError):
        return None


def test_release_identity_tuple_cannot_return_another_stream():
    trust = _trust()
    broker = trust._development_broker(start_ms=1_700_000_000_000)
    (
        published_a,
        published_b,
        _frozen_a,
        _frozen_b,
        session_a,
        _session_b,
        _bindings_a,
        bindings_b,
    ) = _two_captured_consumers(broker)
    bindings_b.require("bundle")
    sid_b = broker._receipt_audit(published_b)[0]["stream_id"]
    broker._used_inputs[id(session_a)] = {"bundle"}
    broker._session_streams[id(session_a)] = (id(session_a), sid_b)
    with pytest.raises(ValueError, match="CONSUMED|required"):
        broker.release(session_a)
    assert broker._receipt_audit(published_a)[-1]["event"] == "CAPTURED"
    assert broker._receipt_audit(published_b)[-1]["event"] == "CONSUMED"


def test_bindings_pin_tuple_replace_cannot_move_consumed_cas():
    trust = _trust()
    broker = trust._development_broker(start_ms=1_700_000_000_000)
    (
        published_a,
        published_b,
        _frozen_a,
        _frozen_b,
        _session_a,
        _session_b,
        bindings_a,
        _bindings_b,
    ) = _two_captured_consumers(broker)
    rec = broker._bindings_pin[id(bindings_a)]
    sid_b = broker._receipt_audit(published_b)[0]["stream_id"]
    broker._bindings_pin[id(bindings_a)] = (rec[0], sid_b) + tuple(rec[2:])
    artifact = _consume_or_refuse(bindings_a)
    assert broker._receipt_audit(published_b)[-1]["event"] == "CAPTURED"
    if artifact is None:
        assert broker._receipt_audit(published_a)[-1]["event"] == "CAPTURED"
        return
    assert artifact.value["rows"][0]["id"] == "AAA"
    assert broker._receipt_audit(published_a)[-1]["event"] == "CONSUMED"


def test_bindings_pin_tuple_replace_cannot_forge_consumed_identity():
    trust = _trust()
    broker = trust._development_broker(start_ms=1_700_000_000_000)
    (
        published_a,
        published_b,
        frozen_a,
        _frozen_b,
        session_a,
        session_b,
        bindings_a,
        bindings_b,
    ) = _two_captured_consumers(broker)
    rec_a = broker._bindings_pin[id(bindings_a)]
    rec_b = broker._bindings_pin[id(bindings_b)]
    if isinstance(rec_a, tuple) and rec_a:
        broker._bindings_pin[id(bindings_a)] = rec_b
    intern = getattr(broker, "_bindings_intern", None)
    if intern is not None:
        intern[id(bindings_a)] = rec_b
    broker._session_runtime[id(session_a)] = (
        id(session_a),
        broker._session_runtime[id(session_b)][1],
    )
    artifact = _consume_or_refuse(bindings_a)
    consumed = broker._receipt_audit(published_a)[-1]
    assert broker._receipt_audit(published_b)[-1]["event"] == "CAPTURED"
    if artifact is None:
        assert consumed["event"] == "CAPTURED"
        return
    assert artifact.value["rows"][0]["id"] == "AAA"
    assert consumed["event"] == "CONSUMED"
    assert consumed["consumer_captured_port"]["consumer_document_sha256"] == frozen_a.source_sha256
    assert consumed["actor_runtime"]["run_identity"] == "consumer-a"
    assert consumed["transition_nonce"] == "nonce-consumed-a"


def test_freeze_and_publish_stream_maps_cannot_capture_another_stream():
    trust = _trust()
    broker = trust._development_broker(start_ms=1_700_000_000_000)
    session_prod_a, published_a, _values = _publish(
        broker,
        members=_members({"rows": [{"id": "AAA"}]}),
    )
    broker.end_session(session_prod_a)
    published_b, _sealed_b = _foreign_publish(broker)
    _document_a, frozen_a = _freeze(broker, published_a)
    _document_b, frozen_b = _freeze(
        broker,
        published_b,
        document=_consumer_document(
            broker.descriptor(published_b, purpose="synthetic"),
            name="consumer-b-doc",
        ),
    )
    original_freeze = broker._freeze_published[id(frozen_a)]
    broker._freeze_published[id(frozen_a)] = published_b
    with pytest.raises(ValueError, match="document|published|root|port|freeze|mismatch"):
        _capture(
            broker,
            published_b,
            frozen_a,
            run_identity="consumer-cross",
            nonce="nonce-captured-cross",
        )
    assert broker._receipt_audit(published_a)[-1]["event"] == "PUBLISHED"
    assert broker._receipt_audit(published_b)[-1]["event"] == "PUBLISHED"
    broker._freeze_published[id(frozen_a)] = original_freeze
    sid_b = broker._receipt_audit(published_b)[0]["stream_id"]
    original_stream = broker._publish_stream_intern[id(published_a)]
    broker._publish_stream[id(published_a)] = sid_b
    with pytest.raises(ValueError, match="document|published|root|port|stream|mismatch|capture|required"):
        _capture(
            broker,
            published_a,
            frozen_a,
            run_identity="consumer-a",
            nonce="nonce-captured-a",
        )
    broker._publish_stream[id(published_a)] = original_stream
    assert broker._receipt_audit(published_a)[-1]["event"] == "PUBLISHED"
    assert broker._receipt_audit(published_b)[-1]["event"] == "PUBLISHED"


def test_receipt_store_graft_cannot_consume_foreign_chain():
    trust = _trust()
    broker = trust._development_broker(start_ms=1_700_000_000_000)
    (
        published_a,
        published_b,
        _frozen_a,
        _frozen_b,
        _session_a,
        _session_b,
        bindings_a,
        _bindings_b,
    ) = _two_captured_consumers(broker)
    sid_a = broker._receipt_audit(published_a)[0]["stream_id"]
    sid_b = broker._receipt_audit(published_b)[0]["stream_id"]
    published_identity_a = broker._receipt_audit(published_a)[2]["publication_receipt_sha256"]
    broker._receipt_store[sid_a] = broker._receipt_store[sid_b]
    artifact = _consume_or_refuse(bindings_a)
    assert broker._receipt_audit(published_b)[-1]["event"] == "CAPTURED"
    if artifact is None:
        return
    consumed = broker._receipt_audit(published_a)[-1]
    assert artifact.value["rows"][0]["id"] == "AAA"
    assert consumed["event"] == "CONSUMED"
    assert consumed["stream_id"] == sid_a
    assert consumed["publication_receipt_sha256"] == published_identity_a


def test_verified_pin_inplace_cannot_move_consumed_cas():
    trust = _trust()
    broker = trust._development_broker(start_ms=1_700_000_000_000)
    session_prod_a, published_a, _values = _publish(
        broker,
        members=_members({"rows": [{"id": "AAA"}]}),
    )
    broker.end_session(session_prod_a)
    published_b, _sealed_b = _foreign_publish(broker)
    _document_a, frozen_a = _freeze(broker, published_a)
    _document_b, frozen_b = _freeze(
        broker,
        published_b,
        document=_consumer_document(
            broker.descriptor(published_b, purpose="synthetic"),
            name="consumer-b-doc",
        ),
    )
    captured, session = _capture(
        broker,
        published_a,
        frozen_a,
        run_identity="consumer-a",
        nonce="nonce-captured-a",
    )
    _capture(
        broker,
        published_b,
        frozen_b,
        run_identity="consumer-b",
        nonce="nonce-captured-b",
    )
    verified = broker.open_capture(session, captured)
    pin = broker._verified_pin[id(verified)]
    sid_b = broker._receipt_audit(published_b)[0]["stream_id"]
    if isinstance(pin, dict):
        pin["stream_id"] = sid_b
    bindings = broker.captured_bindings(
        session,
        frozen_a,
        verified,
        consumer_node="consume",
        transition_nonce="nonce-consumed-a",
    )
    artifact = _consume_or_refuse(bindings)
    assert broker._receipt_audit(published_b)[-1]["event"] == "CAPTURED"
    if artifact is None:
        assert broker._receipt_audit(published_a)[-1]["event"] == "CAPTURED"
        return
    assert artifact.value["rows"][0]["id"] == "AAA"
    assert broker._receipt_audit(published_a)[-1]["event"] == "CONSUMED"


def test_dual_intern_bindings_pin_cannot_move_consumed_cas():
    trust = _trust()
    broker = trust._development_broker(start_ms=1_700_000_000_000)
    (
        published_a,
        published_b,
        _frozen_a,
        _frozen_b,
        _session_a,
        _session_b,
        bindings_a,
        _bindings_b,
    ) = _two_captured_consumers(broker)
    rec = broker._bindings_pin.get(id(bindings_a))
    sid_b = broker._receipt_audit(published_b)[0]["stream_id"]
    if rec is not None and not isinstance(rec, dict):
        hostile = (rec[0], sid_b) + tuple(rec[2:])
        broker._bindings_pin[id(bindings_a)] = hostile
        intern = getattr(broker, "_bindings_intern", None)
        if intern is not None:
            intern[id(bindings_a)] = hostile
    else:
        store = getattr(broker._bindings_pin, "_store", None)
        if store is not None:
            store[id(bindings_a)] = (sid_b,)
    artifact = _consume_or_refuse(bindings_a)
    assert broker._receipt_audit(published_b)[-1]["event"] == "CAPTURED"
    if artifact is None:
        assert broker._receipt_audit(published_a)[-1]["event"] == "CAPTURED"
        return
    assert artifact.value["rows"][0]["id"] == "AAA"
    assert broker._receipt_audit(published_a)[-1]["event"] == "CONSUMED"


def _dual_write(table, intern, key, value):
    table[key] = value
    if intern is not None:
        intern[key] = value


def test_dual_intern_session_streams_cannot_release_another_stream():
    trust = _trust()
    broker = trust._development_broker(start_ms=1_700_000_000_000)
    (
        published_a,
        published_b,
        _frozen_a,
        _frozen_b,
        session_a,
        _session_b,
        _bindings_a,
        bindings_b,
    ) = _two_captured_consumers(broker)
    bindings_b.require("bundle")
    sid_b = broker._receipt_audit(published_b)[0]["stream_id"]
    broker._used_inputs[id(session_a)] = {"bundle"}
    _dual_write(
        broker._session_streams,
        getattr(broker, "_session_stream_intern", None),
        id(session_a),
        (id(session_a), sid_b),
    )
    with pytest.raises(ValueError, match="CONSUMED|required"):
        broker.release(session_a)
    assert broker._receipt_audit(published_a)[-1]["event"] == "CAPTURED"
    assert broker._receipt_audit(published_b)[-1]["event"] == "CONSUMED"


def test_dual_intern_runtime_cannot_forge_consumed_actor():
    trust = _trust()
    broker = trust._development_broker(start_ms=1_700_000_000_000)
    (
        published_a,
        published_b,
        _frozen_a,
        _frozen_b,
        session_a,
        session_b,
        bindings_a,
        _bindings_b,
    ) = _two_captured_consumers(broker)
    rec_b = broker._session_runtime[id(session_b)]
    items_b = rec_b[1] if isinstance(rec_b, tuple) and len(rec_b) > 1 else rec_b
    _dual_write(
        broker._session_runtime,
        getattr(broker, "_session_runtime_intern", None),
        id(session_a),
        (id(session_a), items_b),
    )
    artifact = _consume_or_refuse(bindings_a)
    consumed = broker._receipt_audit(published_a)[-1]
    assert broker._receipt_audit(published_b)[-1]["event"] == "CAPTURED"
    if artifact is None:
        assert consumed["event"] == "CAPTURED"
        return
    assert artifact.value["rows"][0]["id"] == "AAA"
    assert consumed["event"] == "CONSUMED"
    assert consumed["actor_runtime"]["run_identity"] == "consumer-a"


def test_dual_intern_freeze_and_publish_stream_cannot_capture_another_stream():
    trust = _trust()
    broker = trust._development_broker(start_ms=1_700_000_000_000)
    session_prod_a, published_a, _values = _publish(
        broker,
        members=_members({"rows": [{"id": "AAA"}]}),
    )
    broker.end_session(session_prod_a)
    published_b, _sealed_b = _foreign_publish(broker)
    _document_a, frozen_a = _freeze(broker, published_a)
    _document_b, _frozen_b = _freeze(
        broker,
        published_b,
        document=_consumer_document(
            broker.descriptor(published_b, purpose="synthetic"),
            name="consumer-b-doc",
        ),
    )
    _dual_write(
        broker._freeze_published,
        getattr(broker, "_freeze_intern", None),
        id(frozen_a),
        published_b,
    )
    with pytest.raises(
        ValueError,
        match="document|published|root|port|freeze|mismatch|capture|required",
    ):
        _capture(
            broker,
            published_b,
            frozen_a,
            run_identity="consumer-cross",
            nonce="nonce-captured-cross",
        )
    assert broker._receipt_audit(published_a)[-1]["event"] == "PUBLISHED"
    assert broker._receipt_audit(published_b)[-1]["event"] == "PUBLISHED"
    freeze_intern = getattr(broker, "_freeze_intern", None)
    original_freeze = published_a
    if freeze_intern is not None:
        original_freeze = freeze_intern.get(id(frozen_a), published_a)
        if original_freeze is published_b:
            original_freeze = published_a
    _dual_write(
        broker._freeze_published,
        freeze_intern,
        id(frozen_a),
        original_freeze,
    )
    sid_b = broker._receipt_audit(published_b)[0]["stream_id"]
    stream_intern = getattr(broker, "_publish_stream_intern", None)
    original_stream = None if stream_intern is None else stream_intern.get(id(published_a))
    _dual_write(
        broker._publish_stream,
        stream_intern,
        id(published_a),
        sid_b,
    )
    with pytest.raises(
        ValueError,
        match="document|published|root|port|stream|mismatch|capture|required",
    ):
        _capture(
            broker,
            published_a,
            frozen_a,
            run_identity="consumer-a",
            nonce="nonce-captured-a",
        )
    if original_stream is not None:
        _dual_write(
            broker._publish_stream,
            stream_intern,
            id(published_a),
            original_stream,
        )
    assert broker._receipt_audit(published_a)[-1]["event"] == "PUBLISHED"
    assert broker._receipt_audit(published_b)[-1]["event"] == "PUBLISHED"


def test_dual_intern_capture_bind_cannot_consume_another_stream():
    trust = _trust()
    broker = trust._development_broker(start_ms=1_700_000_000_000)
    session_prod_a, published_a, _values = _publish(
        broker,
        members=_members({"rows": [{"id": "AAA"}]}),
    )
    broker.end_session(session_prod_a)
    published_b, _sealed_b = _foreign_publish(broker)
    _document_a, frozen_a = _freeze(broker, published_a)
    _document_b, frozen_b = _freeze(
        broker,
        published_b,
        document=_consumer_document(
            broker.descriptor(published_b, purpose="synthetic"),
            name="consumer-b-doc",
        ),
    )
    captured_a, session_a = _capture(
        broker,
        published_a,
        frozen_a,
        run_identity="consumer-a",
        nonce="nonce-captured-a",
    )
    captured_b, _session_b = _capture(
        broker,
        published_b,
        frozen_b,
        run_identity="consumer-b",
        nonce="nonce-captured-b",
    )
    bind_b = broker._capture_bind[id(captured_b)]
    _dual_write(
        broker._capture_bind,
        getattr(broker, "_capture_bind_intern", None),
        id(captured_a),
        bind_b,
    )
    with pytest.raises(ValueError, match="capture|session|token|required|bind"):
        broker.open_capture(session_a, captured_a)
    assert broker._receipt_audit(published_a)[-1]["event"] == "CAPTURED"
    assert broker._receipt_audit(published_b)[-1]["event"] == "CAPTURED"


def _hmac_mint(broker, table, intern, key, value):
    broker._store_interned(table, intern, key, value)


def _json_payload(broker, table, key):
    payload, _objs = broker._mac_unpack(table[key], key, "hmac mint")
    return payload


def test_hmac_mint_bindings_pin_cannot_move_consumed_cas():
    trust = _trust()
    broker = trust._development_broker(start_ms=1_700_000_000_000)
    (
        published_a,
        published_b,
        _frozen_a,
        _frozen_b,
        _session_a,
        _session_b,
        bindings_a,
        _bindings_b,
    ) = _two_captured_consumers(broker)
    sid_b = broker._receipt_audit(published_b)[0]["stream_id"]
    payload = list(_json_payload(broker, broker._bindings_pin, id(bindings_a)))
    payload[1] = sid_b
    _hmac_mint(
        broker,
        broker._bindings_pin,
        broker._bindings_intern,
        id(bindings_a),
        payload,
    )
    artifact = _consume_or_refuse(bindings_a)
    assert broker._receipt_audit(published_b)[-1]["event"] == "CAPTURED"
    if artifact is None:
        assert broker._receipt_audit(published_a)[-1]["event"] == "CAPTURED"
        return
    assert artifact.value["rows"][0]["id"] == "AAA"
    assert broker._receipt_audit(published_a)[-1]["event"] == "CONSUMED"


def test_hmac_mint_session_streams_cannot_release_another_stream():
    trust = _trust()
    broker = trust._development_broker(start_ms=1_700_000_000_000)
    (
        published_a,
        published_b,
        _frozen_a,
        _frozen_b,
        session_a,
        _session_b,
        _bindings_a,
        bindings_b,
    ) = _two_captured_consumers(broker)
    bindings_b.require("bundle")
    sid_b = broker._receipt_audit(published_b)[0]["stream_id"]
    broker._used_inputs[id(session_a)] = {"bundle"}
    _hmac_mint(
        broker,
        broker._session_streams,
        broker._session_stream_intern,
        id(session_a),
        [id(session_a), sid_b],
    )
    with pytest.raises(ValueError, match="CONSUMED|required"):
        broker.release(session_a)
    assert broker._receipt_audit(published_a)[-1]["event"] == "CAPTURED"
    assert broker._receipt_audit(published_b)[-1]["event"] == "CONSUMED"


def test_hmac_mint_publish_stream_cannot_capture_another_stream():
    trust = _trust()
    broker = trust._development_broker(start_ms=1_700_000_000_000)
    session_prod_a, published_a, _values = _publish(
        broker,
        members=_members({"rows": [{"id": "AAA"}]}),
    )
    broker.end_session(session_prod_a)
    published_b, _sealed_b = _foreign_publish(broker)
    _document_a, frozen_a = _freeze(broker, published_a)
    sid_b = broker._receipt_audit(published_b)[0]["stream_id"]
    _hmac_mint(
        broker,
        broker._publish_stream,
        broker._publish_stream_intern,
        id(published_a),
        sid_b,
    )
    with pytest.raises(
        ValueError,
        match="document|published|root|port|stream|mismatch|capture|required",
    ):
        _capture(
            broker,
            published_a,
            frozen_a,
            run_identity="consumer-a",
            nonce="nonce-captured-a",
        )
    assert broker._receipt_audit(published_a)[-1]["event"] == "PUBLISHED"
    assert broker._receipt_audit(published_b)[-1]["event"] == "PUBLISHED"


def test_hmac_mint_freeze_published_cannot_capture_another_stream():
    trust = _trust()
    broker = trust._development_broker(start_ms=1_700_000_000_000)
    session_prod_a, published_a, _values = _publish(
        broker,
        members=_members({"rows": [{"id": "AAA"}]}),
    )
    broker.end_session(session_prod_a)
    published_b, _sealed_b = _foreign_publish(broker)
    _document_a, frozen_a = _freeze(broker, published_a)
    _hmac_mint(
        broker,
        broker._freeze_published,
        broker._freeze_intern,
        id(frozen_a),
        published_b,
    )
    with pytest.raises(
        ValueError,
        match="document|published|root|port|freeze|mismatch|capture|required",
    ):
        _capture(
            broker,
            published_b,
            frozen_a,
            run_identity="consumer-cross",
            nonce="nonce-captured-cross",
        )
    assert broker._receipt_audit(published_a)[-1]["event"] == "PUBLISHED"
    assert broker._receipt_audit(published_b)[-1]["event"] == "PUBLISHED"


def test_hmac_mint_capture_bind_cannot_open_another_stream():
    trust = _trust()
    broker = trust._development_broker(start_ms=1_700_000_000_000)
    session_prod_a, published_a, _values = _publish(
        broker,
        members=_members({"rows": [{"id": "AAA"}]}),
    )
    broker.end_session(session_prod_a)
    published_b, _sealed_b = _foreign_publish(broker)
    _document_a, frozen_a = _freeze(broker, published_a)
    _document_b, frozen_b = _freeze(
        broker,
        published_b,
        document=_consumer_document(
            broker.descriptor(published_b, purpose="synthetic"),
            name="consumer-b-doc",
        ),
    )
    captured_a, session_a = _capture(
        broker,
        published_a,
        frozen_a,
        run_identity="consumer-a",
        nonce="nonce-captured-a",
    )
    captured_b, session_b = _capture(
        broker,
        published_b,
        frozen_b,
        run_identity="consumer-b",
        nonce="nonce-captured-b",
    )
    sid_b = broker._receipt_audit(published_b)[0]["stream_id"]
    _hmac_mint(
        broker,
        broker._capture_bind,
        broker._capture_bind_intern,
        id(captured_a),
        (published_b, frozen_b, session_a, sid_b),
    )
    with pytest.raises(ValueError, match="capture|session|token|required|bind|published|frozen"):
        broker.open_capture(session_a, captured_a)
    assert broker._receipt_audit(published_a)[-1]["event"] == "CAPTURED"
    assert broker._receipt_audit(published_b)[-1]["event"] == "CAPTURED"


def test_hmac_mint_verified_pin_cannot_move_consumed_cas():
    trust = _trust()
    broker = trust._development_broker(start_ms=1_700_000_000_000)
    session_prod_a, published_a, _values = _publish(
        broker,
        members=_members({"rows": [{"id": "AAA"}]}),
    )
    broker.end_session(session_prod_a)
    published_b, _sealed_b = _foreign_publish(broker)
    _document_a, frozen_a = _freeze(broker, published_a)
    _document_b, frozen_b = _freeze(
        broker,
        published_b,
        document=_consumer_document(
            broker.descriptor(published_b, purpose="synthetic"),
            name="consumer-b-doc",
        ),
    )
    captured_a, session_a = _capture(
        broker,
        published_a,
        frozen_a,
        run_identity="consumer-a",
        nonce="nonce-captured-a",
    )
    captured_b, session_b = _capture(
        broker,
        published_b,
        frozen_b,
        run_identity="consumer-b",
        nonce="nonce-captured-b",
    )
    verified_a = broker.open_capture(session_a, captured_a)
    broker.open_capture(session_b, captured_b)
    pin_a = broker._load_interned(
        broker._verified_pin,
        broker._verified_intern,
        id(verified_a),
        "verified capture is required",
    )
    sid_b = broker._receipt_audit(published_b)[0]["stream_id"]
    published, frozen_pin, bound_session, _sid_a, retained, port_items = pin_a
    _hmac_mint(
        broker,
        broker._verified_pin,
        broker._verified_intern,
        id(verified_a),
        (published, frozen_pin, bound_session, sid_b, retained, port_items),
    )
    try:
        bindings = broker.captured_bindings(
            session_a,
            frozen_a,
            verified_a,
            consumer_node="consume",
            transition_nonce="nonce-consumed-a",
        )
    except (TypeError, ValueError, AttributeError):
        bindings = None
    if bindings is None:
        assert broker._receipt_audit(published_a)[-1]["event"] == "CAPTURED"
        assert broker._receipt_audit(published_b)[-1]["event"] == "CAPTURED"
        return
    artifact = _consume_or_refuse(bindings)
    assert broker._receipt_audit(published_b)[-1]["event"] == "CAPTURED"
    if artifact is None:
        assert broker._receipt_audit(published_a)[-1]["event"] == "CAPTURED"
        return
    assert artifact.value["rows"][0]["id"] == "AAA"
    assert broker._receipt_audit(published_a)[-1]["event"] == "CONSUMED"


def test_hmac_mint_session_runtime_cannot_forge_consumed_actor():
    trust = _trust()
    broker = trust._development_broker(start_ms=1_700_000_000_000)
    (
        published_a,
        published_b,
        _frozen_a,
        _frozen_b,
        session_a,
        session_b,
        bindings_a,
        _bindings_b,
    ) = _two_captured_consumers(broker)
    items_b = tuple(sorted(dict(session_b._runtime).items()))
    _hmac_mint(
        broker,
        broker._session_runtime,
        broker._session_runtime_intern,
        id(session_a),
        [id(session_a), list(items_b)],
    )
    artifact = _consume_or_refuse(bindings_a)
    consumed = broker._receipt_audit(published_a)[-1]
    assert broker._receipt_audit(published_b)[-1]["event"] == "CAPTURED"
    if artifact is None:
        assert consumed["event"] == "CAPTURED"
        return
    assert artifact.value["rows"][0]["id"] == "AAA"
    assert consumed["event"] == "CONSUMED"
    assert consumed["actor_runtime"]["run_identity"] == "consumer-a"


def test_stream_pin_producer_root_inplace_cannot_forge_consumed_publication():
    trust = _trust()
    broker = trust._development_broker(start_ms=1_700_000_000_000)
    (
        published_a,
        published_b,
        _frozen_a,
        _frozen_b,
        _session_a,
        _session_b,
        bindings_a,
        _bindings_b,
    ) = _two_captured_consumers(broker)
    sid_a = broker._receipt_audit(published_a)[0]["stream_id"]
    published_identity_a = broker._receipt_audit(published_a)[2]["publication_receipt_sha256"]
    producer_a = broker._receipt_audit(published_a)[0]["producer_run_identity"]
    root_a = broker._receipt_audit(published_a)[0]["root_ref"]
    pin = broker._stream_pin[sid_a]
    try:
        pin.producer["run_identity"] = "producer-b"
        pin.root["root_ref"] = "capture://synthetic/root-b"
    except (TypeError, AttributeError, KeyError):
        pass
    artifact = _consume_or_refuse(bindings_a)
    consumed = broker._receipt_audit(published_a)[-1]
    assert broker._receipt_audit(published_b)[-1]["event"] == "CAPTURED"
    if artifact is None:
        assert consumed["event"] == "CAPTURED"
        return
    assert artifact.value["rows"][0]["id"] == "AAA"
    assert consumed["event"] == "CONSUMED"
    assert consumed["producer_run_identity"] == producer_a
    assert consumed["root_ref"] == root_a
    assert consumed["publication_receipt_sha256"] == published_identity_a


def test_receipt_store_truncation_cannot_rewind_consumed():
    trust = _trust()
    broker = trust._development_broker(start_ms=1_700_000_000_000)
    (
        published_a,
        published_b,
        _frozen_a,
        _frozen_b,
        _session_a,
        _session_b,
        bindings_a,
        _bindings_b,
    ) = _two_captured_consumers(broker)
    artifact = bindings_a.require("bundle").artifact
    first = broker._receipt_audit(published_a)[-1]
    assert first["event"] == "CONSUMED"
    signature = first["signature"]
    issued_at = first["issued_at_ms"]
    sid_a = first["stream_id"]
    original = tuple(broker._receipt_store[sid_a])
    truncated = original[:-1]
    try:
        broker._receipt_store[sid_a] = truncated
        truncated_applied = True
    except (TypeError, ValueError, AttributeError):
        truncated_applied = False
    if truncated_applied:
        broker._nonces.discard("nonce-consumed-a")
        bindings_a._used.clear()
        broker._used_inputs.pop(id(_session_a), None)
        _consume_or_refuse(bindings_a)
    stored = tuple(broker._receipt_store[sid_a])
    assert stored == original
    replayed = broker._receipt_audit(published_a)[-1]
    assert replayed["event"] == "CONSUMED"
    assert replayed["signature"] == signature
    assert replayed["issued_at_ms"] == issued_at
    assert artifact.value["rows"][0]["id"] == "AAA"
    assert broker._receipt_audit(published_b)[-1]["event"] == "CAPTURED"
    assert len(stored) == len(original)


def test_hmac_mint_and_stream_id_setattr_cannot_move_consumed_cas():
    trust = _trust()
    broker = trust._development_broker(start_ms=1_700_000_000_000)
    (
        published_a,
        published_b,
        _frozen_a,
        _frozen_b,
        _session_a,
        _session_b,
        bindings_a,
        _bindings_b,
    ) = _two_captured_consumers(broker)
    sid_b = broker._receipt_audit(published_b)[0]["stream_id"]
    published_identity_a = broker._receipt_audit(published_a)[2]["publication_receipt_sha256"]
    payload = list(_json_payload(broker, broker._bindings_pin, id(bindings_a)))
    payload[1] = sid_b
    _hmac_mint(
        broker,
        broker._bindings_pin,
        broker._bindings_intern,
        id(bindings_a),
        payload,
    )
    object.__setattr__(bindings_a, "_stream_id", sid_b)
    artifact = _consume_or_refuse(bindings_a)
    assert broker._receipt_audit(published_b)[-1]["event"] == "CAPTURED"
    if artifact is None:
        assert broker._receipt_audit(published_a)[-1]["event"] == "CAPTURED"
        return
    consumed = broker._receipt_audit(published_a)[-1]
    assert artifact.value["rows"][0]["id"] == "AAA"
    assert consumed["event"] == "CONSUMED"
    assert consumed["publication_receipt_sha256"] == published_identity_a


def test_hmac_mint_and_session_stream_setattr_cannot_release_another_stream():
    trust = _trust()
    broker = trust._development_broker(start_ms=1_700_000_000_000)
    (
        published_a,
        published_b,
        _frozen_a,
        _frozen_b,
        session_a,
        _session_b,
        _bindings_a,
        bindings_b,
    ) = _two_captured_consumers(broker)
    bindings_b.require("bundle")
    sid_b = broker._receipt_audit(published_b)[0]["stream_id"]
    broker._used_inputs[id(session_a)] = {"bundle"}
    _hmac_mint(
        broker,
        broker._session_streams,
        broker._session_stream_intern,
        id(session_a),
        [id(session_a), sid_b],
    )
    object.__setattr__(session_a, "_stream_id", sid_b)
    with pytest.raises(ValueError, match="CONSUMED|required"):
        broker.release(session_a)
    assert broker._receipt_audit(published_a)[-1]["event"] == "CAPTURED"
    assert broker._receipt_audit(published_b)[-1]["event"] == "CONSUMED"


def test_backing_store_and_watermark_cannot_rewind_consumed():
    trust = _trust()
    broker = trust._development_broker(start_ms=1_700_000_000_000)
    (
        published_a,
        published_b,
        _frozen_a,
        _frozen_b,
        session_a,
        _session_b,
        bindings_a,
        _bindings_b,
    ) = _two_captured_consumers(broker)
    artifact = bindings_a.require("bundle").artifact
    first = broker._receipt_audit(published_a)[-1]
    assert first["event"] == "CONSUMED"
    signature = first["signature"]
    issued_at = first["issued_at_ms"]
    sid_a = first["stream_id"]
    original = tuple(broker._receipt_store[sid_a])
    backing = getattr(broker._receipt_store, "_data", None)
    if backing is None:
        backing = getattr(broker._receipt_store, "_WormReceiptStore__data", None)
    if backing is not None:
        backing[sid_a] = original[:-1]
    broker._receipt_len[sid_a] = len(original) - 1
    broker._nonces.discard("nonce-consumed-a")
    bindings_a._used.clear()
    broker._used_inputs.pop(id(session_a), None)
    _consume_or_refuse(bindings_a)
    replayed = broker._receipt_audit(published_a)[-1]
    assert replayed["event"] == "CONSUMED"
    assert replayed["signature"] == signature
    assert replayed["issued_at_ms"] == issued_at
    assert artifact.value["rows"][0]["id"] == "AAA"
    assert broker._receipt_audit(published_b)[-1]["event"] == "CAPTURED"

