"""ADR-0173: environment-bound synthetic raw-event/v2 publication."""

import gc
import inspect
import json
import sys
from weakref import ref as weakref_ref

import pytest

import dskit.pipeline as pipeline
from dskit.pipeline import trust
from dskit.production import bundles
from dskit.production import verifier as production_verifier
from tests.pipeline import test_captured_authorization as cases
from tests.pipeline.test_event_wire_v2 import _v2_case


def _case(tmp_path, **changes):
    roster_publisher, preflight, source, signed, roster = _v2_case(
        tmp_path, **changes
    )
    fixture = preflight.verify(*signed, *roster)
    raw_publisher = trust._SyntheticRawPublisher(preflight)
    environment = trust._synthetic_environment_identity()
    return (
        roster_publisher,
        raw_publisher,
        fixture,
        environment,
        signed,
        roster,
        source,
    )


def _binding_authority():
    pending = [trust._SyntheticRawPublisher.publish_v2]
    seen = set()
    while pending:
        function = pending.pop()
        if id(function) in seen:
            continue
        seen.add(id(function))
        nonlocals = inspect.getclosurevars(function).nonlocals
        if {"records", "anchors", "record_identities"} <= set(nonlocals):
            return nonlocals
        pending.extend(
            value for value in nonlocals.values()
            if inspect.isfunction(value)
        )
    raise AssertionError("v2 binding authority closure not found")


def test_publish_v2_is_private_positional_only_and_not_exported():
    method = trust._SyntheticRawPublisher.publish_v2
    signature = inspect.signature(method)
    assert tuple(signature.parameters) == (
        "self", "proof", "environment_identity",
        "authorization_bytes", "g1", "g2", "attestation",
        "bootstrap", "bg1", "bg2", "roster_basis", "roster_receipt",
    )
    assert all(
        parameter.kind is inspect.Parameter.POSITIONAL_ONLY
        for parameter in tuple(signature.parameters.values())[1:]
    )
    assert "publish_v2" not in trust.__all__
    assert not hasattr(trust, "_SYNTHETIC_V2_RAW_ENVIRONMENTS")
    assert "_publish_common" not in trust._SyntheticRawPublisher.__dict__
    direct_closure = inspect.getclosurevars(method).nonlocals
    assert set(direct_closure) == {"authorized_publish_v2"}
    legacy_closure = inspect.getclosurevars(
        trust._SyntheticRawPublisher.publish
    ).nonlocals
    assert set(legacy_closure) == {"authorized_publish"}
    legacy_impl = inspect.getclosurevars(
        legacy_closure["authorized_publish"]
    ).nonlocals
    assert "raw_writer" in legacy_impl
    assert "v2_writer" not in legacy_impl


def test_publish_v2_refuses_wrong_environment_before_any_effect(tmp_path):
    roster_publisher, raw_publisher, fixture, _environment, signed, roster, _source = (
        _case(tmp_path)
    )
    before = (
        fixture._used,
        raw_publisher._closed,
        raw_publisher._retained,
        roster_publisher._reserve._connection.total_changes,
        dict(roster_publisher._broker._storage),
        tuple(roster_publisher._broker._receipt_store._data),
    )
    with pytest.raises(ValueError, match="environment"):
        raw_publisher.publish_v2(fixture, object(), *signed, *roster)
    closure = _binding_authority()
    assert raw_publisher not in closure["records"]
    assert raw_publisher not in closure["anchors"]
    assert before == (
        fixture._used,
        raw_publisher._closed,
        raw_publisher._retained,
        roster_publisher._reserve._connection.total_changes,
        dict(roster_publisher._broker._storage),
        tuple(roster_publisher._broker._receipt_store._data),
    )


def test_publish_v2_refuses_replaced_weakref_dispatch_before_effect(
    tmp_path, monkeypatch,
):
    (
        roster_publisher,
        raw_publisher,
        fixture,
        environment,
        signed,
        roster,
        _source,
    ) = _case(tmp_path)
    calls = []
    monkeypatch.setattr(
        trust, "weakref_ref", lambda *_args: calls.append("called")
    )
    before = roster_publisher._reserve._connection.total_changes
    with pytest.raises(ValueError, match="dispatch changed"):
        raw_publisher.publish_v2(
            fixture, environment, *signed, *roster
        )
    assert calls == []
    assert fixture._used is False
    assert raw_publisher._retained is None
    assert roster_publisher._reserve._connection.total_changes == before


@pytest.mark.parametrize(
    "helper_name",
    (
        "_manifest",
        "_identity",
        "_check_row",
        "_advance",
        "_published_facts",
        "_sign",
        "_check_signed",
        "_issue_receipt",
        "_quarantine",
    ),
)
def test_publish_v2_refuses_replaced_writer_helper_before_any_effect(
    tmp_path, monkeypatch, helper_name,
):
    (
        roster_publisher,
        raw_publisher,
        fixture,
        environment,
        signed,
        roster,
        _source,
    ) = _case(tmp_path)
    calls = []

    def replacement(*_args, **_kwargs):
        calls.append(helper_name)
        return None

    monkeypatch.setattr(
        trust._SyntheticRawPublisher, helper_name, replacement,
    )
    before = (
        fixture._used,
        raw_publisher._closed,
        raw_publisher._retained,
        roster_publisher._reserve._connection.total_changes,
        dict(roster_publisher._broker._storage),
        tuple(roster_publisher._broker._member_events),
        tuple(roster_publisher._broker._receipt_store._data),
    )
    with pytest.raises(ValueError, match="dispatch changed"):
        raw_publisher.publish_v2(
            fixture, environment, *signed, *roster
        )
    assert calls == []
    assert before == (
        fixture._used,
        raw_publisher._closed,
        raw_publisher._retained,
        roster_publisher._reserve._connection.total_changes,
        dict(roster_publisher._broker._storage),
        tuple(roster_publisher._broker._member_events),
        tuple(roster_publisher._broker._receipt_store._data),
    )


@pytest.mark.parametrize(
    ("owner_name", "descriptor_name"),
    (
        *(
            ("publisher", name)
            for name in (
                "_preflight", "_roster_publisher", "_reserve", "_broker",
                "_closed", "_retained",
            )
        ),
        *(
            ("fixture", name)
            for name in (
                "_owner", "_intent", "_members", "_event_schema", "_used",
            )
        ),
        ("preflight", "_publisher"),
    ),
)
def test_publish_v2_refuses_replaced_authority_descriptor_before_any_effect(
    tmp_path, monkeypatch, owner_name, descriptor_name,
):
    (
        roster_publisher,
        raw_publisher,
        fixture,
        environment,
        signed,
        roster,
        _source,
    ) = _case(tmp_path)
    owners = {
        "publisher": trust._SyntheticRawPublisher,
        "fixture": trust.VerifiedSyntheticDatasetFixture,
        "preflight": type(raw_publisher._preflight),
    }
    owner = owners[owner_name]
    original = owner.__dict__[descriptor_name]
    calls = []

    class ForwardingDescriptor:
        def __get__(self, instance, instance_type=None):
            calls.append("get")
            if instance is None:
                return self
            return original.__get__(instance, instance_type)

        def __set__(self, instance, value):
            calls.append("set")
            return original.__set__(instance, value)

    before = (
        fixture._used,
        raw_publisher._closed,
        raw_publisher._retained,
        roster_publisher._reserve._connection.total_changes,
        dict(roster_publisher._broker._storage),
        tuple(roster_publisher._broker._member_events),
        tuple(roster_publisher._broker._receipt_store._data),
    )
    monkeypatch.setattr(owner, descriptor_name, ForwardingDescriptor())
    with pytest.raises(ValueError, match="dispatch changed"):
        raw_publisher.publish_v2(
            fixture, environment, *signed, *roster
        )
    monkeypatch.undo()
    assert calls == []
    assert before == (
        fixture._used,
        raw_publisher._closed,
        raw_publisher._retained,
        roster_publisher._reserve._connection.total_changes,
        dict(roster_publisher._broker._storage),
        tuple(roster_publisher._broker._member_events),
        tuple(roster_publisher._broker._receipt_store._data),
    )


def test_publish_v2_refuses_replaced_preflight_derivation_before_any_effect(
    tmp_path, monkeypatch,
):
    (
        roster_publisher,
        raw_publisher,
        fixture,
        environment,
        signed,
        roster,
        _source,
    ) = _case(tmp_path)
    preflight_type = type(raw_publisher._preflight)
    calls = []

    def replacement(*_args, **_kwargs):
        calls.append("called")
        return None

    monkeypatch.setattr(preflight_type, "_derive_intent", replacement)
    before = roster_publisher._reserve._connection.total_changes
    with pytest.raises(ValueError, match="dispatch changed"):
        raw_publisher.publish_v2(
            fixture, environment, *signed, *roster
        )
    assert calls == []
    assert fixture._used is False
    assert raw_publisher._retained is None
    assert roster_publisher._reserve._connection.total_changes == before


def test_captured_common_writer_refuses_v2_without_provisional_binding(
    tmp_path,
):
    (
        roster_publisher,
        raw_publisher,
        fixture,
        _environment,
        signed,
        roster,
        _source,
    ) = _case(tmp_path)
    closure = _binding_authority()
    before = (
        roster_publisher._reserve._connection.total_changes,
        fixture._used,
        tuple(raw_publisher._broker._member_events),
    )
    with pytest.raises(TypeError):
        closure["v2_writer"](
            raw_publisher,
            fixture,
            signed,
            roster,
            "dskit.raw-event/v1",
        )
    with pytest.raises(ValueError, match="provisional.*binding"):
        closure["v2_writer"](
            raw_publisher,
            fixture,
            signed,
            roster,
        )
    assert before == (
        roster_publisher._reserve._connection.total_changes,
        fixture._used,
        tuple(raw_publisher._broker._member_events),
    )


def test_publish_v2_writes_raw_root_and_root_proof_reverifies(tmp_path):
    (
        _roster_publisher,
        raw_publisher,
        fixture,
        environment,
        signed,
        roster,
        _source,
    ) = _case(tmp_path)
    manifest, basis, receipt = raw_publisher.publish_v2(
        fixture, environment, *signed, *roster
    )
    parsed = json.loads(manifest)
    assert parsed["event_schema"] == "dskit.raw-event/v2"
    assert raw_publisher._closed is True
    assert fixture._used is True

    facts = raw_publisher.proof().verify(
        *signed, *roster, manifest, basis, receipt
    )
    authority = _binding_authority()
    committed_record = authority["records"][raw_publisher]
    assert committed_record[2] is authority["committed"]
    assert authority["anchors"][raw_publisher] is committed_record
    assert facts["event_count"] == 1
    assert facts["authorizing"] is False
    assert facts["deployment_eligible"] is False


def test_v2_root_proof_refuses_replaced_environment_gate_before_member_read(
    tmp_path, monkeypatch,
):
    (
        _roster_publisher,
        raw_publisher,
        fixture,
        environment,
        signed,
        roster,
        _source,
    ) = _case(tmp_path)
    manifest, basis, receipt = raw_publisher.publish_v2(
        fixture, environment, *signed, *roster
    )
    before = tuple(raw_publisher._broker._member_events)
    monkeypatch.setattr(trust, "_require_synthetic_tzdata", lambda *_args: None)
    with pytest.raises(ValueError, match="environment dispatch"):
        raw_publisher.proof().verify(
            *signed, *roster, manifest, basis, receipt
        )
    assert tuple(raw_publisher._broker._member_events) == before


def test_v2_root_proof_uses_pinned_slot_reads_before_dispatch_refusal(
    tmp_path, monkeypatch,
):
    (
        _roster_publisher,
        raw_publisher,
        fixture,
        environment,
        signed,
        roster,
        _source,
    ) = _case(tmp_path)
    output = raw_publisher.publish_v2(
        fixture, environment, *signed, *roster
    )
    calls = []

    def replaced_schema(_self):
        calls.append("called")
        return "dskit.raw-event/v2"

    monkeypatch.setattr(
        trust.VerifiedSyntheticDatasetFixture,
        "_event_schema",
        property(replaced_schema),
    )
    before = tuple(raw_publisher._broker._member_events)
    with pytest.raises(ValueError, match="dispatch changed"):
        raw_publisher.proof().verify(*signed, *roster, *output)
    assert calls == []
    assert tuple(raw_publisher._broker._member_events) == before


def test_v2_root_proof_refuses_copied_private_binding_record_before_member_read(
    tmp_path,
):
    (
        _roster_publisher,
        raw_publisher,
        fixture,
        environment,
        signed,
        roster,
        _source,
    ) = _case(tmp_path)
    output = raw_publisher.publish_v2(
        fixture, environment, *signed, *roster
    )
    closure = _binding_authority()
    records = closure["records"]
    anchors = closure["anchors"]
    copied = list(records[raw_publisher])
    records[raw_publisher] = copied
    anchors[raw_publisher] = copied
    before = tuple(raw_publisher._broker._member_events)
    with pytest.raises(ValueError, match="committed.*binding"):
        raw_publisher.proof().verify(*signed, *roster, *output)
    assert tuple(raw_publisher._broker._member_events) == before


def test_v2_root_proof_refuses_environment_identity_substitution(
    tmp_path,
):
    (
        _roster_publisher,
        raw_publisher,
        fixture,
        environment,
        signed,
        roster,
        _source,
    ) = _case(tmp_path)
    output = raw_publisher.publish_v2(
        fixture, environment, *signed, *roster
    )
    closure = _binding_authority()
    closure["records"][raw_publisher][1] = (
        trust._synthetic_environment_identity()
    )
    before = tuple(raw_publisher._broker._member_events)
    with pytest.raises(ValueError, match="committed.*binding"):
        raw_publisher.proof().verify(*signed, *roster, *output)
    assert tuple(raw_publisher._broker._member_events) == before


def test_v2_root_proof_seal_uses_environment_identity_not_equality(
    tmp_path, monkeypatch,
):
    (
        _roster_publisher,
        raw_publisher,
        fixture,
        environment,
        signed,
        roster,
        _source,
    ) = _case(tmp_path)
    output = raw_publisher.publish_v2(
        fixture, environment, *signed, *roster
    )
    authority = _binding_authority()
    alternate = trust._synthetic_environment_identity()
    equality_calls = []

    def equal(_self, _other):
        equality_calls.append("called")
        return True

    monkeypatch.setattr(
        trust._SyntheticEnvironmentIdentity, "__eq__", equal, raising=False
    )
    authority["records"][raw_publisher][1] = alternate
    before = tuple(raw_publisher._broker._member_events)
    with pytest.raises(ValueError, match="committed.*binding"):
        raw_publisher.proof().verify(*signed, *roster, *output)
    assert equality_calls == []
    assert tuple(raw_publisher._broker._member_events) == before


def test_v2_root_proof_refuses_cross_publisher_record_rewire(tmp_path):
    first = _case(tmp_path / "first")
    second = _case(
        tmp_path / "second",
        dataset_changes={"authorization_id": "raw-authorization-v2-2"},
    )
    (
        _first_roster,
        first_publisher,
        first_fixture,
        first_environment,
        first_signed,
        first_roster,
        _first_source,
    ) = first
    (
        _second_roster,
        second_publisher,
        second_fixture,
        second_environment,
        second_signed,
        second_roster,
        _second_source,
    ) = second
    first_output = first_publisher.publish_v2(
        first_fixture, first_environment, *first_signed, *first_roster
    )
    second_publisher.publish_v2(
        second_fixture, second_environment, *second_signed, *second_roster
    )
    closure = _binding_authority()
    other_record = closure["records"][second_publisher]
    closure["records"][first_publisher] = other_record
    closure["anchors"][first_publisher] = other_record
    before = tuple(first_publisher._broker._member_events)
    with pytest.raises(ValueError, match="committed.*binding"):
        first_publisher.proof().verify(
            *first_signed, *first_roster, *first_output
        )
    assert tuple(first_publisher._broker._member_events) == before


def test_v2_root_proof_refuses_deleted_private_binding_before_member_read(
    tmp_path,
):
    (
        _roster_publisher,
        raw_publisher,
        fixture,
        environment,
        signed,
        roster,
        _source,
    ) = _case(tmp_path)
    output = raw_publisher.publish_v2(
        fixture, environment, *signed, *roster
    )
    closure = _binding_authority()
    del closure["records"][raw_publisher]
    before = tuple(raw_publisher._broker._member_events)
    with pytest.raises(ValueError, match="committed.*binding"):
        raw_publisher.proof().verify(*signed, *roster, *output)
    assert tuple(raw_publisher._broker._member_events) == before


def test_private_binding_entries_and_identity_anchor_expire_with_publisher(
    tmp_path,
):
    (
        _roster_publisher,
        raw_publisher,
        fixture,
        environment,
        signed,
        roster,
        _source,
    ) = _case(tmp_path)
    raw_publisher.publish_v2(fixture, environment, *signed, *roster)
    closure = _binding_authority()
    record_identity = id(closure["records"][raw_publisher])
    publisher_ref = weakref_ref(raw_publisher)
    del raw_publisher
    gc.collect()
    assert publisher_ref() is None
    assert record_identity not in closure["record_identities"]
    assert record_identity not in closure["writer_invoked_identities"]
    assert record_identity not in closure["binding_seals"]


def test_legacy_publish_remains_v1_only_after_v2_entry_exists(tmp_path):
    _publisher, raw_publisher, fixture, _environment, signed, roster, _source = (
        _case(tmp_path)
    )
    with pytest.raises(ValueError, match="v1-only"):
        raw_publisher.publish(fixture, *signed, *roster)
    assert fixture._used is False
    assert raw_publisher._retained is None


def test_writer_failure_terminalizes_private_binding(tmp_path, monkeypatch):
    (
        _publisher,
        failed_publisher,
        failed_fixture,
        failed_environment,
        failed_signed,
        failed_roster,
        _source,
    ) = _case(tmp_path / "failed")
    broker_type = type(failed_publisher._broker)
    original_produce = broker_type.produce

    def fail_produce(*_args, **_kwargs):
        raise RuntimeError("injected writer failure")

    monkeypatch.setattr(broker_type, "produce", fail_produce)
    with pytest.raises(RuntimeError, match="injected writer failure"):
        failed_publisher.publish_v2(
            failed_fixture,
            failed_environment,
            *failed_signed,
            *failed_roster,
        )
    assert failed_fixture._used is True
    assert failed_publisher._retained is None
    closure = _binding_authority()
    failed_record = closure["records"][failed_publisher]
    assert closure["anchors"][failed_publisher] is failed_record
    assert failed_record[2] is closure["failed"]
    assert closure["binding_seals"][id(failed_record)] == (
        failed_record, failed_environment,
    )

    monkeypatch.setattr(broker_type, "produce", original_produce)
    (
        _other_publisher,
        good_publisher,
        good_fixture,
        good_environment,
        good_signed,
        good_roster,
        _other_source,
    ) = _case(tmp_path / "good")
    good_output = good_publisher.publish_v2(
        good_fixture, good_environment, *good_signed, *good_roster
    )
    failed_publisher._retained = good_publisher._retained
    failed_publisher._closed = True
    forged = trust.NonAuthorizingRawRootProof(trust._MAKE, failed_publisher)
    with pytest.raises(ValueError, match="committed.*binding"):
        forged.verify(*good_signed, *good_roster, *good_output)


def test_pre_writer_fault_removes_provisional_binding(tmp_path):
    (
        roster_publisher,
        raw_publisher,
        fixture,
        environment,
        signed,
        roster,
        _source,
    ) = _case(tmp_path)
    authority = _binding_authority()
    v2_writer = authority["v2_writer"]
    fired = False

    def trace(frame, event, _arg):
        nonlocal fired
        if (
            not fired
            and event == "line"
            and frame.f_code is v2_writer.__code__
        ):
            fired = True
            raise RuntimeError("injected pre-writer fault")
        return trace

    before = (
        roster_publisher._reserve._connection.total_changes,
        tuple(raw_publisher._broker._member_events),
    )
    sys.settrace(trace)
    try:
        with pytest.raises(RuntimeError, match="pre-writer"):
            raw_publisher.publish_v2(
                fixture, environment, *signed, *roster
            )
    finally:
        sys.settrace(None)
    assert fired is True
    assert raw_publisher not in authority["records"]
    assert raw_publisher not in authority["anchors"]
    assert fixture._used is False
    assert raw_publisher._closed is False
    assert raw_publisher._retained is None
    assert before == (
        roster_publisher._reserve._connection.total_changes,
        tuple(raw_publisher._broker._member_events),
    )


def test_post_writer_return_fault_marks_binding_failed(tmp_path):
    (
        _roster_publisher,
        raw_publisher,
        fixture,
        environment,
        signed,
        roster,
        _source,
    ) = _case(tmp_path)
    authority = _binding_authority()
    authorized = inspect.getclosurevars(
        trust._SyntheticRawPublisher.publish_v2
    ).nonlocals["authorized_publish_v2"]
    lines, start = inspect.getsourcelines(authorized)
    call_index = next(
        index for index, line in enumerate(lines)
        if "result = v2_writer" in line
    )
    target_line = start + next(
        index for index in range(call_index + 1, len(lines))
        if "require_dispatch()" in lines[index]
    )
    fired = False

    def trace(frame, event, _arg):
        nonlocal fired
        if (
            not fired
            and event == "line"
            and frame.f_code is authorized.__code__
            and frame.f_lineno == target_line
        ):
            fired = True
            raise RuntimeError("injected post-writer fault")
        return trace

    sys.settrace(trace)
    try:
        with pytest.raises(RuntimeError, match="post-writer"):
            raw_publisher.publish_v2(
                fixture, environment, *signed, *roster
            )
    finally:
        sys.settrace(None)
    assert fired is True
    record = authority["records"][raw_publisher]
    assert record[2] is authority["failed"]
    assert id(record) in authority["writer_invoked_identities"]
    assert fixture._used is True
    assert raw_publisher._closed is True
    assert raw_publisher._retained is not None
    with pytest.raises(ValueError, match="committed.*binding"):
        raw_publisher.proof().verify(
            *signed, *roster, *raw_publisher._retained[5:]
        )


def test_v2_root_proof_verifies_under_existing_writer_transaction(tmp_path):
    (
        roster_publisher,
        raw_publisher,
        fixture,
        environment,
        signed,
        roster,
        _source,
    ) = _case(tmp_path)
    output = raw_publisher.publish_v2(
        fixture, environment, *signed, *roster
    )
    connection = roster_publisher._reserve._connection
    connection.execute("BEGIN")
    try:
        facts = raw_publisher.proof().verify(
            *signed, *roster, *output, _under_writer_lock=True
        )
        assert connection.in_transaction is True
    finally:
        connection.execute("ROLLBACK")
    assert facts["event_count"] == 1
    assert facts["authorizing"] is False
    assert facts["deployment_eligible"] is False


def test_v2_root_proof_under_writer_lock_refuses_cross_originals_before_read(
    tmp_path, monkeypatch,
):
    first = _case(tmp_path / "first")
    second = _case(
        tmp_path / "second",
        dataset_changes={"authorization_id": "raw-authorization-v2-2"},
    )
    (
        first_roster_publisher,
        first_publisher,
        first_fixture,
        first_environment,
        first_signed,
        first_roster,
        _first_source,
    ) = first
    (
        _second_roster_publisher,
        second_publisher,
        second_fixture,
        second_environment,
        second_signed,
        second_roster,
        _second_source,
    ) = second
    first_publisher.publish_v2(
        first_fixture, first_environment, *first_signed, *first_roster
    )
    second_output = second_publisher.publish_v2(
        second_fixture, second_environment, *second_signed, *second_roster
    )
    (tmp_path / "v1").mkdir()
    _v1_publisher, v1_roster, v1_signed, v1_output = (
        cases._adr140_published_raw_case(tmp_path / "v1", monkeypatch)
    )
    malformed_signed = (b"{", *first_signed[1:])
    swapped_signed = (
        first_signed[0], first_signed[2], first_signed[1], first_signed[3],
    )
    candidates = (
        ("cross-v2", second_signed, second_roster, second_output),
        ("exact-v1", v1_signed, v1_roster, v1_output),
        ("malformed", malformed_signed, first_roster,
         first_publisher._retained[5:]),
        ("swapped", swapped_signed, first_roster,
         first_publisher._retained[5:]),
    )
    connection = first_roster_publisher._reserve._connection
    for label, candidate_signed, candidate_roster, candidate_output in candidates:
        before = tuple(first_publisher._broker._member_events)
        connection.execute("BEGIN")
        try:
            with pytest.raises(ValueError, match="originals mismatch"):
                first_publisher.proof().verify(
                    *candidate_signed,
                    *candidate_roster,
                    *candidate_output,
                    _under_writer_lock=True,
                )
        finally:
            connection.execute("ROLLBACK")
        assert tuple(first_publisher._broker._member_events) == before, label


def test_retained_v2_root_stops_before_root_pis_construction_effect(
    tmp_path,
):
    (
        roster_publisher,
        raw_publisher,
        fixture,
        environment,
        signed,
        roster,
        _source,
    ) = _case(tmp_path)
    raw_publisher.publish_v2(fixture, environment, *signed, *roster)
    before = (
        roster_publisher._reserve._connection.total_changes,
        dict(raw_publisher._root_pis_pairs),
        tuple(raw_publisher._broker._member_events),
    )
    with pytest.raises(ValueError, match="root-PIS issuer is v1-only"):
        trust._SyntheticRootPisIssuer(raw_publisher)
    assert before == (
        roster_publisher._reserve._connection.total_changes,
        dict(raw_publisher._root_pis_pairs),
        tuple(raw_publisher._broker._member_events),
    )


def test_retained_v2_root_has_no_downstream_capability_alias(
    tmp_path,
):
    (
        roster_publisher,
        raw_publisher,
        fixture,
        environment,
        signed,
        roster,
        _source,
    ) = _case(tmp_path)
    raw_publisher.publish_v2(fixture, environment, *signed, *roster)
    before = (
        roster_publisher._reserve._connection.total_changes,
        tuple(raw_publisher._broker._member_events),
        dict(raw_publisher._root_pis_pairs),
    )
    with pytest.raises(TypeError, match="issuer-owned root-PIS"):
        trust.NonAuthorizingSyntheticRootPisProof(
            trust._MAKE, raw_publisher
        )
    with pytest.raises(TypeError, match="issuer-owned dynamic root"):
        trust.NonAuthorizingDynamicRootGraph(
            trust._MAKE, raw_publisher
        )
    with pytest.raises(TypeError, match="lifecycle authority"):
        production_verifier.HistoricalStudyVerifier(raw_publisher)
    with pytest.raises(TypeError, match="exact HistoricalStudyVerifier"):
        production_verifier.HistoricalStudyCaptureDriver(raw_publisher)
    assert (
        production_verifier.NonAuthorizingSyntheticRootPisProof
        is trust.NonAuthorizingSyntheticRootPisProof
    )
    assert (
        production_verifier.NonAuthorizingDynamicRootGraph
        is trust.NonAuthorizingDynamicRootGraph
    )
    with pytest.raises(bundles.ProductionError, match="published token"):
        bundles.compose_replay_tape(
            raw_publisher,
            raw_publisher,
            raw_publisher,
            "0" * 64,
            "source_roster.json",
            (),
        )
    replay = trust.ReplayRun("replay", {})
    with pytest.raises(RuntimeError, match="F3 composed-tape broker"):
        replay.run(None, {"raw_root": raw_publisher})
    assert before == (
        roster_publisher._reserve._connection.total_changes,
        tuple(raw_publisher._broker._member_events),
        dict(raw_publisher._root_pis_pairs),
    )
    assert not hasattr(pipeline, "NonAuthorizingSyntheticRootPisProof")
    assert not hasattr(pipeline, "NonAuthorizingDynamicRootGraph")
    assert pipeline.ReplayRun is trust.ReplayRun
    assert {
        "NonAuthorizingSyntheticRootPisProof",
        "NonAuthorizingDynamicRootGraph",
        "ReplayRun",
    } <= set(trust.__all__)


def test_root_pis_issue_rechecks_retained_publisher_before_closing(
    tmp_path, monkeypatch,
):
    (tmp_path / "v1").mkdir()
    v1_publisher, v1_roster, v1_signed, v1_output = (
        cases._adr140_published_raw_case(tmp_path / "v1", monkeypatch)
    )
    issuer = trust._SyntheticRootPisIssuer(v1_publisher)
    (
        _roster_publisher,
        v2_publisher,
        fixture,
        environment,
        v2_signed,
        v2_roster,
        _source,
    ) = _case(tmp_path / "v2")
    v2_publisher.publish_v2(
        fixture, environment, *v2_signed, *v2_roster
    )
    issuer._publisher = v2_publisher
    before = v2_publisher._reserve._connection.total_changes
    with pytest.raises(ValueError, match="v1-only|retained"):
        issuer.issue(*v1_signed, *v1_roster, *v1_output)
    assert issuer._closed is False
    assert v2_publisher._reserve._connection.total_changes == before
