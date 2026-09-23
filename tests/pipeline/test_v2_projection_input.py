"""ADR-0172: one-shot verified synthetic v2 projection input."""

import copy
import inspect
import json
import pickle
from types import MappingProxyType

import pytest

import dskit.pipeline as pipeline
from dskit.pipeline import trust
from dskit.production import base
from dskit.production import bundles
from dskit.production import verifier as production_verifier
from tests.pipeline.test_v2_raw_publication import _case


def _policy(roster):
    """Build the independently expected immutable rank policy."""
    authorization = json.loads(roster[0])
    return MappingProxyType({
        "schema_version": "dskit.source-rank-policy/v1",
        "sources": tuple(
            MappingProxyType({"source_id": source_id, "rank": rank})
            for rank, source_id in enumerate(authorization["source_ids"])
        ),
        "policy_sha256": authorization["source_rank_policy_sha256"],
    })


def _mint(tmp_path, **changes):
    """Publish one genuine v2 root and mint its projection capability."""
    case = _case(tmp_path, **changes)
    _, raw_publisher, fixture, environment, signed, roster, _ = case
    output = raw_publisher.publish_v2(fixture, environment, *signed, *roster)
    proof = raw_publisher.proof()
    raw_bytes = (*signed, *roster, *output)
    capability = trust._prepare_synthetic_v2_projection_input(proof, raw_bytes)
    return case, proof, raw_bytes, capability


def test_projection_input_surfaces_are_exact_and_positional_only(tmp_path):
    """Only trust exports the opaque type and public one-shot consumer."""
    assert "VerifiedV2ProjectionInput" in trust.__all__
    assert "consume_v2_projection_input" in trust.__all__
    assert not hasattr(pipeline, "VerifiedV2ProjectionInput")
    assert not hasattr(pipeline, "consume_v2_projection_input")
    assert "_prepare_synthetic_v2_projection_input" not in trust.__all__
    assert "_project_verified_synthetic_v2_input" not in (
        production_verifier.__all__
    )
    assert tuple(inspect.signature(
        trust._prepare_synthetic_v2_projection_input
    ).parameters) == ("raw_proof", "raw_proof_bytes")
    assert tuple(inspect.signature(
        trust.consume_v2_projection_input
    ).parameters) == ("value",)
    assert tuple(inspect.signature(
        production_verifier._project_verified_synthetic_v2_input
    ).parameters) == ("value",)
    assert all(
        parameter.kind is inspect.Parameter.POSITIONAL_ONLY
        for function in (
            trust._prepare_synthetic_v2_projection_input,
            trust.consume_v2_projection_input,
            production_verifier._project_verified_synthetic_v2_input,
        )
        for parameter in inspect.signature(function).parameters.values()
    )
    _, proof, raw_bytes, capability = _mint(tmp_path)
    with pytest.raises(TypeError, match="positional-only"):
        trust._prepare_synthetic_v2_projection_input(
            raw_proof=proof, raw_proof_bytes=raw_bytes
        )
    with pytest.raises(TypeError, match="positional-only"):
        trust.consume_v2_projection_input(value=capability)


def test_projection_input_is_final_opaque_frozen_and_unserializable(tmp_path):
    """Callers cannot construct, derive, copy, mutate, or serialize a mint."""
    _, _, _, capability = _mint(tmp_path)
    with pytest.raises(TypeError, match="broker-issued"):
        trust.VerifiedV2ProjectionInput()
    with pytest.raises(TypeError, match="final"):
        class _Derived(trust.VerifiedV2ProjectionInput):
            pass
    with pytest.raises(AttributeError, match="frozen"):
        capability.value = object()
    with pytest.raises(TypeError):
        copy.copy(capability)
    with pytest.raises(TypeError):
        copy.deepcopy(capability)
    with pytest.raises(TypeError):
        pickle.dumps(capability)


@pytest.mark.parametrize(
    "raw_bytes",
    [None, (), [], (b"x",) * 11, (b"x",) * 13, (b"x",) * 11 + ("x",)],
)
def test_prepare_refuses_every_nonexact_raw_byte_tuple(tmp_path, raw_bytes):
    """The broker accepts only the exact twelve-byte proof tuple."""
    _, proof, _, _ = _mint(tmp_path)
    with pytest.raises(ValueError):
        trust._prepare_synthetic_v2_projection_input(proof, raw_bytes)


def test_consume_returns_only_fresh_immutable_payload_and_spends(tmp_path):
    """The public consumer returns exact closed immutable payload shapes."""
    case, _, _, capability = _mint(tmp_path)
    _, _, fixture, _, _, roster, _ = case
    events, policy = trust.consume_v2_projection_input(capability)
    assert type(events) is tuple and events
    assert all(type(event) is MappingProxyType for event in events)
    assert events == tuple(fixture._events)
    assert type(policy) is MappingProxyType
    assert type(policy["sources"]) is tuple
    assert all(type(row) is MappingProxyType for row in policy["sources"])
    assert policy == _policy(roster)
    with pytest.raises(ValueError, match="spent"):
        trust.consume_v2_projection_input(capability)


def test_two_capabilities_from_one_fresh_proof_are_independent(tmp_path):
    """Pure projection permits remint while each capability stays one-shot."""
    _, proof, raw_bytes, first = _mint(tmp_path)
    second = trust._prepare_synthetic_v2_projection_input(proof, raw_bytes)
    assert first is not second
    first_value = trust.consume_v2_projection_input(first)
    second_value = trust.consume_v2_projection_input(second)
    assert first_value == second_value
    with pytest.raises(ValueError, match="spent"):
        trust.consume_v2_projection_input(first)
    with pytest.raises(ValueError, match="spent"):
        trust.consume_v2_projection_input(second)


def test_cross_publisher_bytes_refuse_without_mint(tmp_path):
    """A genuine proof cannot authorize another publisher's retained bytes."""
    _, first_proof, first_bytes, first_capability = _mint(tmp_path / "first")
    _, _, second_bytes, second_capability = _mint(
        tmp_path / "second",
        dataset_changes={"authorization_id": "raw-authorization-v2-2"},
    )
    with pytest.raises(ValueError):
        trust._prepare_synthetic_v2_projection_input(first_proof, second_bytes)
    assert trust.consume_v2_projection_input(first_capability)
    assert trust.consume_v2_projection_input(second_capability)
    assert first_bytes != second_bytes


@pytest.mark.parametrize("target", ["bundle_encoder", "base_plain"])
def test_preconsume_dependency_refusal_leaves_capability_fresh(
    tmp_path, monkeypatch, target,
):
    """Executable drift before consume refuses but permits trusted retry."""
    _, _, _, capability = _mint(tmp_path)
    if target == "bundle_encoder":
        original = bundles._canonical_bytes
        monkeypatch.setattr(bundles, "_canonical_bytes", lambda value: b"bad")
    else:
        original = base._plain
        monkeypatch.setattr(base, "_plain", lambda value: value)
    with pytest.raises(ValueError, match="dependency changed"):
        production_verifier._project_verified_synthetic_v2_input(capability)
    if target == "bundle_encoder":
        monkeypatch.setattr(bundles, "_canonical_bytes", original)
    else:
        monkeypatch.setattr(base, "_plain", original)
    assert production_verifier._project_verified_synthetic_v2_input(capability)


def test_genuine_v2_root_projects_once_to_exact_adr0171_bytes(tmp_path):
    """A genuine ADR-0173 root is the sole input to the pure projector."""
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
    raw_proof = raw_publisher.proof()
    capability = trust._prepare_synthetic_v2_projection_input(
        raw_proof, (*signed, *roster, *output)
    )

    projected = production_verifier._project_verified_synthetic_v2_input(
        capability
    )

    assert projected == bundles._project_v2_event_envelopes(
        fixture._events, _policy(roster)
    )
    with pytest.raises(ValueError, match="spent"):
        production_verifier._project_verified_synthetic_v2_input(capability)
