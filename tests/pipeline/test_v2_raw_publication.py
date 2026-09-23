"""ADR-0173: environment-bound synthetic raw-event/v2 publication."""

import inspect
import json

import pytest

from dskit.pipeline import trust
from tests.pipeline.test_event_wire_v2 import _v2_case


def _case(tmp_path):
    roster_publisher, preflight, source, signed, roster = _v2_case(tmp_path)
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
    assert before == (
        fixture._used,
        raw_publisher._closed,
        raw_publisher._retained,
        roster_publisher._reserve._connection.total_changes,
        dict(roster_publisher._broker._storage),
        tuple(roster_publisher._broker._receipt_store._data),
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
