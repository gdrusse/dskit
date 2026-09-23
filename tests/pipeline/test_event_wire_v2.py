"""ADR-0169 RED: one closed v2 raw wire with hard downstream stops."""

import ast
import hashlib
import importlib
import inspect
import json
from types import MappingProxyType

import pytest

from dskit.pipeline import trust
from dskit.production import bundles
from tests.pipeline import test_captured_authorization as cases
from tests.pipeline import test_trust as f4


TZDATA_SHA256 = "7" * 64


def _wire():
    return importlib.import_module("dskit.pipeline.event_wire")


def _resign_bootstrap(raw_auth, raw_g1, raw_g2, **changes):
    auth = json.loads(raw_auth)
    auth.update(changes)
    raw_auth = f4._json_bytes(auth)
    digest = hashlib.sha256(raw_auth).hexdigest()
    grants = []
    for raw in (raw_g1, raw_g2):
        grant = json.loads(raw)
        grant["bootstrap_sha256"] = digest
        grants.append(cases._resign_roster_bootstrap_grant(grant))
    return raw_auth, *grants


def _v2_case(tmp_path, *, event_changes=None, event_remove=None,
             dataset_changes=None, bootstrap_changes=None):
    tmp_path.mkdir(parents=True, exist_ok=True)
    path = str(tmp_path / "adr169-reserve.sqlite")
    trust._SyntheticAuthorizationReserve._provision(path)
    publisher = trust._SyntheticRosterPublisher(path)
    bootstrap, bg1, bg2, _policy = cases._synthetic_roster_bootstrap_fixture()
    bootstrap_value = json.loads(bootstrap)
    scope = {**bootstrap_value["scope"], "tzdata_version_sha256": TZDATA_SHA256}
    bootstrap_updates = {
        "schema_version": "dskit.roster-bootstrap-authorization/v2",
        "event_schema": "dskit.raw-event/v2",
        "scope": scope,
    }
    bootstrap_updates.update(bootstrap_changes or {})
    bootstrap, bg1, bg2 = _resign_bootstrap(
        bootstrap, bg1, bg2, **bootstrap_updates,
    )
    _roster, basis, receipt = publisher.publish(bootstrap, bg1, bg2)
    publisher._reserve._advance_clock(600)
    receipt_value = json.loads(receipt)
    event = {
        "schema_version": "dskit.raw-event/v2",
        "source_id": "src:A",
        "event_id": "event-v2-a",
        "source_sequence": 0,
        "availability_ms": 500,
        "payload_sha256": "a" * 64,
        "exchange_ms": 490,
        "receive_ms": 495,
        "source_provenance_tag": "fixture",
        "source_timezone_tag": "America/New_York",
        "correction_position": 0,
        "corrects_event_id": None,
    }
    event.update(event_changes or {})
    if event_remove is not None:
        del event[event_remove]
    member_a = f4._json_bytes(event) + b"\n"
    members = {"fixture_A.ndjson": member_a, "fixture_B.ndjson": b""}
    empty_meta = f4._json_bytes({
        "corrections": [],
        "schema_version": "dskit.correction-bust-metadata/v1",
    })
    bootstrap_value = json.loads(bootstrap)
    authorization = {
        "schema_version": "dskit.dataset-capture-authorization/v2",
        "authorization_id": "raw-authorization-v2-1",
        "source_ids": bootstrap_value["source_ids"],
        "scope": bootstrap_value["scope"],
        "license_digests": bootstrap_value["license_digests"],
        "event_schema": "dskit.raw-event/v2",
        "media_type": "application/x-ndjson",
        "source_roster_root_sha256": hashlib.sha256(f4._json_bytes({
            key: receipt_value[key] for key in (
                "root_ref", "root_id", "snapshot_version",
                "member_manifest_sha256",
            )
        })).hexdigest(),
        "source_roster_publication_receipt_sha256":
            receipt_value["root_publication_receipt_sha256"],
        "source_roster_policy_sha256": bootstrap_value[
            "source_rank_policy_sha256"
        ],
        "correction_bust_metadata_sha256": hashlib.sha256(empty_meta).hexdigest(),
        "allow_empty_capture": False,
        "issued_at_ms": 501,
        "not_before_ms": 501,
        "expires_at_ms": 900,
    }
    authorization.update(dataset_changes or {})
    authorization_bytes = f4._json_bytes(authorization)
    snapshot = json.loads(bg1)["revocation_snapshot_sha256"]
    grants = []
    for role in ("G1", "G2"):
        grants.append(cases._resign_synthetic_grant({
            "schema_version": "dskit.dataset-capture-grant/v1",
            "role": role,
            "issuer_key_id": "synthetic-" + role.lower() + "/dataset-capture/v1",
            "authorization_sha256": hashlib.sha256(authorization_bytes).hexdigest(),
            "issued_at_ms": authorization["issued_at_ms"],
            "not_before_ms": authorization["not_before_ms"],
            "expires_at_ms": authorization["expires_at_ms"],
            "revocation_snapshot_sha256": snapshot,
        }))
    attestation = cases._resign_synthetic_fixture_attestation({
        "schema_version": "dskit.synthetic-dataset-fixture-attestation/v1",
        "issuer_key_id": "synthetic-g2/dataset-fixture-attestation/v1",
        "authorization_sha256": hashlib.sha256(authorization_bytes).hexdigest(),
        "issued_at_ms": authorization["issued_at_ms"],
        "not_before_ms": authorization["not_before_ms"],
        "expires_at_ms": authorization["expires_at_ms"],
        "revocation_snapshot_sha256": snapshot,
        "ordered_members": [
            {
                "member_name": name,
                "source_id": source_id,
                "byte_length": len(members[name]),
                "sha256": hashlib.sha256(members[name]).hexdigest(),
            }
            for name, source_id in (
                ("fixture_A.ndjson", "src:A"),
                ("fixture_B.ndjson", "src:B"),
            )
        ],
    })
    roster = (bootstrap, bg1, bg2, basis, receipt)
    signed = (authorization_bytes, *grants, attestation)
    source = trust._SyntheticFixtureSource(members)
    preflight = trust._SyntheticRawPreflight(publisher, source)
    return publisher, preflight, source, signed, roster


def test_adr169_wire_has_one_immutable_closed_owner():
    wire = _wire()
    assert wire.__all__ == (
        "AUTHORIZATION_SCOPE_FIELDS",
        "DATASET_AUTHORIZATION_EVENT_SCHEMAS",
        "RAW_EVENT_FIELDS",
        "ROSTER_AUTHORIZATION_EVENT_SCHEMAS",
    )
    assert wire.RAW_EVENT_FIELDS == MappingProxyType({
        "dskit.raw-event/v1": (
            "schema_version", "source_id", "event_id", "source_sequence",
            "availability_ms", "payload_sha256",
        ),
        "dskit.raw-event/v2": (
            "schema_version", "source_id", "event_id", "source_sequence",
            "availability_ms", "payload_sha256", "exchange_ms", "receive_ms",
            "source_provenance_tag", "source_timezone_tag",
            "correction_position", "corrects_event_id",
        ),
    })
    assert wire.DATASET_AUTHORIZATION_EVENT_SCHEMAS == MappingProxyType({
        "dskit.dataset-capture-authorization/v1": "dskit.raw-event/v1",
        "dskit.dataset-capture-authorization/v2": "dskit.raw-event/v2",
    })
    assert wire.ROSTER_AUTHORIZATION_EVENT_SCHEMAS == MappingProxyType({
        "dskit.roster-bootstrap-authorization/v1": "dskit.raw-event/v1",
        "dskit.roster-bootstrap-authorization/v2": "dskit.raw-event/v2",
    })
    assert wire.AUTHORIZATION_SCOPE_FIELDS == MappingProxyType({
        "dskit.raw-event/v1": (
            "availability_start_ms", "availability_end_ms",
            "source_provenance_sha256",
        ),
        "dskit.raw-event/v2": (
            "availability_start_ms", "availability_end_ms",
            "source_provenance_sha256", "tzdata_version_sha256",
        ),
    })
    with pytest.raises(TypeError):
        wire.RAW_EVENT_FIELDS["dskit.raw-event/v3"] = ()


def test_adr169_wire_is_stdlib_only_and_not_pipeline_reexported():
    wire = _wire()
    tree = ast.parse(inspect.getsource(wire))
    imports = {
        alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {
        (node.module or "").split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    }
    assert imports <= {"types"}
    import dskit.pipeline as pipeline
    assert "event_wire" not in getattr(pipeline, "__all__", ())
    assert "RAW_EVENT_FIELDS" not in getattr(pipeline, "__all__", ())


def test_adr169_v2_preflight_accepts_exact_signed_fixture(tmp_path):
    _publisher, preflight, source, signed, roster = _v2_case(tmp_path)
    proof = preflight.verify(*signed, *roster)
    assert proof.event_count == 1
    assert proof.member_names == ("fixture_A.ndjson", "fixture_B.ndjson")
    assert proof._events[0]["schema_version"] == "dskit.raw-event/v2"
    assert proof._events[0]["source_timezone_tag"] == "America/New_York"
    assert source.read_names == proof.member_names
    assert proof.deployment_eligible is False


@pytest.mark.parametrize(("field", "value"), [
    ("exchange_ms", True),
    ("exchange_ms", -1),
    ("receive_ms", True),
    ("receive_ms", -1),
    ("source_provenance_tag", ""),
    ("source_provenance_tag", 1),
    ("source_timezone_tag", ""),
    ("source_timezone_tag", None),
    ("correction_position", True),
    ("correction_position", -1),
    ("corrects_event_id", ""),
    ("corrects_event_id", 7),
])
def test_adr169_v2_raw_field_types_and_bounds_refuse(tmp_path, field, value):
    _publisher, preflight, source, signed, roster = _v2_case(
        tmp_path, event_changes={field: value},
    )
    with pytest.raises(ValueError, match="closed raw event"):
        preflight.verify(*signed, *roster)
    assert source.read_names == ("fixture_A.ndjson",)


@pytest.mark.parametrize("field", (
    "schema_version", "source_id", "event_id", "source_sequence",
    "availability_ms", "payload_sha256", "exchange_ms", "receive_ms",
    "source_provenance_tag", "source_timezone_tag", "correction_position",
    "corrects_event_id",
))
def test_adr169_v2_each_single_missing_key_refuses(tmp_path, field):
    publisher, preflight, source, signed, roster = _v2_case(
        tmp_path, event_remove=field,
    )
    with pytest.raises(ValueError, match="closed raw event"):
        preflight.verify(*signed, *roster)
    assert source.read_names == ("fixture_A.ndjson",)
    assert publisher._reserve._connection.execute(
        "SELECT state FROM reserve_uses WHERE kind='raw-dataset'"
    ).fetchone() == ("RAW_READ_STARTED",)


@pytest.mark.parametrize(("field", "value"), [
    ("source_id", ""),
    ("source_id", 1),
    ("event_id", ""),
    ("event_id", None),
    ("source_sequence", True),
    ("source_sequence", -1),
    ("availability_ms", True),
    ("availability_ms", 1001),
    ("payload_sha256", "A" * 64),
])
def test_adr169_v2_common_field_types_and_bounds_refuse(
    tmp_path, field, value,
):
    _publisher, preflight, source, signed, roster = _v2_case(
        tmp_path, event_changes={field: value},
    )
    with pytest.raises(ValueError):
        preflight.verify(*signed, *roster)
    assert source.read_names == ("fixture_A.ndjson",)


@pytest.mark.parametrize("mutation", ["unknown", "v1-mixed"])
def test_adr169_v2_raw_closed_shape_refuses(tmp_path, mutation):
    if mutation == "unknown":
        changes = {"unexpected": 1}
    elif mutation == "v1-mixed":
        changes = {"schema_version": "dskit.raw-event/v1"}
    publisher, preflight, source, signed, roster = _v2_case(
        tmp_path, event_changes=changes,
    )
    with pytest.raises(ValueError, match="closed raw event"):
        preflight.verify(*signed, *roster)
    assert source.read_names == ("fixture_A.ndjson",)
    assert publisher._reserve._connection.execute(
        "SELECT state FROM reserve_uses WHERE kind='raw-dataset'"
    ).fetchone() == ("RAW_READ_STARTED",)


def test_adr169_v2_tzdata_mismatch_refuses_before_member_read(tmp_path):
    _publisher, preflight, source, signed, roster = _v2_case(
        tmp_path,
        dataset_changes={"scope": {
            "availability_start_ms": 0,
            "availability_end_ms": 1000,
            "source_provenance_sha256": "1" * 64,
            "tzdata_version_sha256": "8" * 64,
        }},
    )
    with pytest.raises(ValueError, match="equality"):
        preflight.verify(*signed, *roster)
    assert source.read_names == ()


def test_adr169_v2_tzdata_digest_must_be_nonplaceholder(tmp_path):
    with pytest.raises(ValueError, match="tzdata"):
        _v2_case(
            tmp_path,
            bootstrap_changes={"scope": {
                "availability_start_ms": 0,
                "availability_end_ms": 1000,
                "source_provenance_sha256": "1" * 64,
                "tzdata_version_sha256": "0" * 64,
            }},
        )


@pytest.mark.parametrize("authority", ["dataset", "roster"])
def test_adr169_v2_authority_refuses_negative_availability_scope(authority):
    if authority == "dataset":
        raw, g1, g2 = cases._synthetic_dataset_grant_fixture()
        value = json.loads(raw)
        value.update(
            schema_version="dskit.dataset-capture-authorization/v2",
            event_schema="dskit.raw-event/v2",
        )
        digest_field = "authorization_sha256"
        resign = cases._resign_synthetic_grant
        verifier = trust.NonAuthorizingSyntheticGrantVerifier().verify
    else:
        raw, g1, g2, _policy = cases._synthetic_roster_bootstrap_fixture()
        value = json.loads(raw)
        value.update(
            schema_version="dskit.roster-bootstrap-authorization/v2",
            event_schema="dskit.raw-event/v2",
        )
        digest_field = "bootstrap_sha256"
        resign = cases._resign_roster_bootstrap_grant
        verifier = trust.NonAuthorizingRosterBootstrapVerifier().verify
    value["scope"] = {
        **value["scope"],
        "availability_start_ms": -2,
        "availability_end_ms": -1,
        "tzdata_version_sha256": TZDATA_SHA256,
    }
    raw = f4._json_bytes(value)
    digest = hashlib.sha256(raw).hexdigest()
    grants = []
    for grant_raw in (g1, g2):
        grant = json.loads(grant_raw)
        grant[digest_field] = digest
        grants.append(resign(grant))
    with pytest.raises(ValueError, match="availability"):
        verifier(raw, *grants)


@pytest.mark.parametrize(("auth_schema", "event_schema"), [
    ("dskit.dataset-capture-authorization/v1", "dskit.raw-event/v2"),
    ("dskit.dataset-capture-authorization/v2", "dskit.raw-event/v1"),
])
def test_adr169_dataset_authority_version_pairing_is_exact(auth_schema, event_schema):
    raw, g1, g2 = cases._synthetic_dataset_grant_fixture()
    value = json.loads(raw)
    value["schema_version"] = auth_schema
    value["event_schema"] = event_schema
    if auth_schema.endswith("/v2"):
        value["scope"]["tzdata_version_sha256"] = TZDATA_SHA256
    raw = f4._json_bytes(value)
    digest = hashlib.sha256(raw).hexdigest()
    grants = []
    for grant_raw in (g1, g2):
        grant = json.loads(grant_raw)
        grant["authorization_sha256"] = digest
        grants.append(cases._resign_synthetic_grant(grant))
    with pytest.raises(ValueError, match="version|schema"):
        trust.NonAuthorizingSyntheticGrantVerifier().verify(raw, *grants)


def test_adr169_v2_roster_basis_records_exact_authorization_schema(tmp_path):
    _publisher, _preflight, _source, _signed, roster = _v2_case(tmp_path)
    basis = json.loads(roster[3])
    reference = next(
        ref for ref in basis["refs"]
        if ref["kind"] == "roster-bootstrap-authorization"
    )
    assert reference["schema"] == "dskit.roster-bootstrap-authorization/v2"


def test_adr169_v2_raw_publisher_refuses_before_any_downstream_effect(tmp_path):
    publisher, preflight, _source, signed, roster = _v2_case(tmp_path)
    proof = preflight.verify(*signed, *roster)
    raw_publisher = trust._SyntheticRawPublisher(preflight)
    before = (
        publisher._reserve._connection.total_changes,
        dict(publisher._broker._storage),
        tuple(publisher._broker._receipt_store._data),
    )
    with pytest.raises(ValueError, match="v1-only"):
        raw_publisher.publish(proof, *signed, *roster)
    assert before == (
        publisher._reserve._connection.total_changes,
        dict(publisher._broker._storage),
        tuple(publisher._broker._receipt_store._data),
    )
    assert proof._used is False


def test_adr169_v2_raw_proof_refuses_caller_schema_downgrade_without_effect(
    tmp_path,
):
    publisher, preflight, _source, signed, roster = _v2_case(tmp_path)
    proof = preflight.verify(*signed, *roster)
    raw_publisher = trust._SyntheticRawPublisher(preflight)
    downgraded_signed = list(signed)
    authorization = json.loads(signed[0])
    authorization.update(
        schema_version="dskit.dataset-capture-authorization/v1",
        event_schema="dskit.raw-event/v1",
    )
    del authorization["scope"]["tzdata_version_sha256"]
    downgraded_signed[0] = f4._json_bytes(authorization)
    downgraded_roster = list(roster)
    bootstrap = json.loads(roster[0])
    bootstrap.update(
        schema_version="dskit.roster-bootstrap-authorization/v1",
        event_schema="dskit.raw-event/v1",
    )
    del bootstrap["scope"]["tzdata_version_sha256"]
    downgraded_roster[0] = f4._json_bytes(bootstrap)
    before = publisher._reserve._connection.total_changes
    with pytest.raises(ValueError, match="proof authority changed"):
        raw_publisher.publish(
            proof, *downgraded_signed, *downgraded_roster,
        )
    assert publisher._reserve._connection.total_changes == before
    assert proof._used is False
    assert publisher._reserve._connection.execute(
        "SELECT state FROM reserve_uses WHERE kind='raw-dataset'"
    ).fetchone() == ("RAW_READ_STARTED",)


@pytest.mark.parametrize(
    ("target", "replacement"),
    [
        ("authorization", None),
        ("authorization", b"[]"),
        ("authorization", b"[" * 2000 + b"]" * 2000),
        ("g1", None),
        ("bootstrap", None),
        ("bootstrap", b"[]"),
    ],
)
def test_adr169_v1_raw_publication_substitution_semantics_stay_frozen(
    tmp_path, monkeypatch, target, replacement,
):
    _path, publisher, roster, signed, members = cases._adr132_raw_case(
        tmp_path, monkeypatch,
    )
    preflight = trust._SyntheticRawPreflight(
        publisher, trust._SyntheticFixtureSource(members),
    )
    proof = preflight.verify(*signed, *roster)
    raw_publisher = trust._SyntheticRawPublisher(preflight)
    changed = list(signed)
    changed_roster = list(roster)
    if target == "authorization":
        changed[0] = changed[0] + b" " if replacement is None else replacement
    elif target == "g1":
        changed[1] += b" "
    else:
        changed_roster[0] = (
            changed_roster[0] + b" " if replacement is None else replacement
        )
    before = publisher._reserve._connection.total_changes
    if replacement is None:
        expected = ValueError
    elif len(replacement) > 1000:
        expected = RecursionError
    else:
        expected = (TypeError, ValueError)
    with pytest.raises(expected):
        raw_publisher.publish(proof, *changed, *changed_roster)
    assert publisher._reserve._connection.total_changes > before
    assert proof._used is True
    assert publisher._reserve._connection.execute(
        "SELECT state FROM reserve_uses WHERE kind='raw-dataset'"
    ).fetchone() == ("QUARANTINED",)


def test_adr169_v1_proof_refuses_genuine_v2_authorities_without_effect(
    tmp_path, monkeypatch,
):
    (tmp_path / "v1").mkdir()
    _path, v1_publisher, v1_roster, v1_signed, members = cases._adr132_raw_case(
        tmp_path / "v1", monkeypatch,
    )
    preflight = trust._SyntheticRawPreflight(
        v1_publisher, trust._SyntheticFixtureSource(members),
    )
    proof = preflight.verify(*v1_signed, *v1_roster)
    raw_publisher = trust._SyntheticRawPublisher(preflight)
    _v2_publisher, _v2_preflight, _source, v2_signed, v2_roster = _v2_case(
        tmp_path / "v2",
    )
    before = v1_publisher._reserve._connection.total_changes
    with pytest.raises(ValueError, match="v1-only"):
        raw_publisher.publish(proof, *v2_signed, *v2_roster)
    assert v1_publisher._reserve._connection.total_changes == before
    assert proof._used is False
    assert v1_publisher._reserve._connection.execute(
        "SELECT state FROM reserve_uses WHERE kind='raw-dataset'"
    ).fetchone() == ("RAW_READ_STARTED",)


def test_adr169_v1_proof_detects_v2_authority_with_malformed_peer(
    tmp_path, monkeypatch,
):
    (tmp_path / "v1").mkdir()
    _path, v1_publisher, v1_roster, v1_signed, members = cases._adr132_raw_case(
        tmp_path / "v1", monkeypatch,
    )
    preflight = trust._SyntheticRawPreflight(
        v1_publisher, trust._SyntheticFixtureSource(members),
    )
    proof = preflight.verify(*v1_signed, *v1_roster)
    raw_publisher = trust._SyntheticRawPublisher(preflight)
    _v2_publisher, _v2_preflight, _source, v2_signed, _v2_roster = _v2_case(
        tmp_path / "v2",
    )
    malformed_roster = (b"{", *v1_roster[1:])
    before = v1_publisher._reserve._connection.total_changes
    with pytest.raises(ValueError, match="v1-only"):
        raw_publisher.publish(proof, *v2_signed, *malformed_roster)
    assert v1_publisher._reserve._connection.total_changes == before
    assert proof._used is False
    assert v1_publisher._reserve._connection.execute(
        "SELECT state FROM reserve_uses WHERE kind='raw-dataset'"
    ).fetchone() == ("RAW_READ_STARTED",)


def test_adr169_root_pis_refuses_v2_inputs_before_reserve_spend(tmp_path, monkeypatch):
    (tmp_path / "v1").mkdir()
    v1_publisher, _v1_roster, _v1_signed, v1_output = cases._adr140_published_raw_case(
        tmp_path / "v1", monkeypatch,
    )
    _v2_publisher, _preflight, _source, v2_signed, v2_roster = _v2_case(
        tmp_path / "v2",
    )
    v1_publisher._reserve._advance_clock(601)
    before = v1_publisher._reserve._connection.total_changes
    with pytest.raises(ValueError, match="v1-only"):
        trust._SyntheticRootPisIssuer(v1_publisher).issue(
            *v2_signed, *v2_roster, *v1_output,
        )
    assert v1_publisher._reserve._connection.total_changes == before
    assert v1_publisher._reserve._connection.execute(
        "SELECT COUNT(*) FROM reserve_uses WHERE kind='root-pis'"
    ).fetchone() == (0,)


def test_adr169_dynamic_graph_refuses_substituted_v2_retained_state(
    tmp_path, monkeypatch,
):
    (tmp_path / "v1").mkdir()
    v1_publisher, v1_roster, v1_signed, v1_output = cases._adr140_published_raw_case(
        tmp_path / "v1", monkeypatch,
    )
    v1_publisher._reserve._advance_clock(601)
    issuer = trust._SyntheticRootPisIssuer(v1_publisher)
    issuer.issue(*v1_signed, *v1_roster, *v1_output)
    retained = issuer._retained
    _v2_publisher, _preflight, _source, v2_signed, v2_roster = _v2_case(
        tmp_path / "v2",
    )
    issuer._retained = (v2_signed, v2_roster, *retained[2:])
    before = v1_publisher._reserve._connection.total_changes
    with pytest.raises(ValueError, match="v1-only"):
        trust.NonAuthorizingDynamicRootGraph(trust._MAKE, issuer)
    assert v1_publisher._reserve._connection.total_changes == before


def test_adr169_bundles_imports_v1_owner_and_still_refuses_v2():
    wire = _wire()
    assert bundles._RAW_EVENT_V1_SCHEMA == "dskit.raw-event/v1"
    assert bundles._RAW_EVENT_V1_FIELDS == wire.RAW_EVENT_FIELDS[
        "dskit.raw-event/v1"
    ]
    event = {
        key: None for key in wire.RAW_EVENT_FIELDS["dskit.raw-event/v2"]
    }
    event.update(
        schema_version="dskit.raw-event/v2", source_id="src:A",
        event_id="event-v2", source_sequence=0, availability_ms=1,
        payload_sha256="a" * 64, exchange_ms=1, receive_ms=1,
        source_provenance_tag="fixture", source_timezone_tag="UTC",
        correction_position=0, corrects_event_id=None,
    )
    assert bundles._check_raw_event_member(event)


def test_adr169_raw_literals_and_field_tuples_have_one_owner():
    wire = _wire()
    forbidden_literals = set(wire.RAW_EVENT_FIELDS)
    forbidden_shapes = {tuple(fields) for fields in wire.RAW_EVENT_FIELDS.values()}
    for module in (trust, bundles):
        tree = ast.parse(inspect.getsource(module))
        literals = {
            node.value for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
        }
        assert not literals & forbidden_literals
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Tuple, ast.List, ast.Set)):
                continue
            values = tuple(
                item.value for item in node.elts
                if isinstance(item, ast.Constant) and isinstance(item.value, str)
            )
            assert values not in forbidden_shapes
