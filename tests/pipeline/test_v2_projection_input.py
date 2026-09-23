"""ADR-0172: one-shot verified synthetic v2 projection input."""

import copy
import gc
import hashlib
import inspect
import json
import pickle
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from types import MappingProxyType

import pytest

import dskit.pipeline as pipeline
from dskit.pipeline import trust
from dskit.production import base
from dskit.production import bundles
from dskit.production import verifier as production_verifier
from tests.pipeline.test_captured_authorization import _adr132_raw_case
from tests.pipeline.test_v2_raw_publication import _case, _effect_snapshot


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


def test_in_place_projector_code_mutation_refuses_before_consume(tmp_path):
    """Identity-only pinning cannot authorize mutated executable code."""
    _, _, _, capability = _mint(tmp_path)
    projector = bundles._project_v2_event_envelopes
    original_code = projector.__code__

    def forged(events, source_rank_policy, /):
        del events, source_rank_policy
        return (b"forged",)

    try:
        projector.__code__ = forged.__code__
        with pytest.raises(ValueError, match="dependency changed"):
            production_verifier._project_verified_synthetic_v2_input(capability)
    finally:
        projector.__code__ = original_code
    assert production_verifier._project_verified_synthetic_v2_input(capability)


def test_omitted_projector_global_refuses_before_consume(tmp_path, monkeypatch):
    """Every effective projector global is pinned before the one-shot spend."""
    _, _, _, capability = _mint(tmp_path)
    original = bundles.MappingProxyType
    monkeypatch.setattr(bundles, "MappingProxyType", object)
    with pytest.raises(ValueError, match="dependency changed"):
        production_verifier._project_verified_synthetic_v2_input(capability)
    monkeypatch.setattr(bundles, "MappingProxyType", original)
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


@pytest.mark.parametrize("raw_proof", [None, object(), 0, ""])
def test_prepare_refuses_every_nonexact_proof_type_without_mint(tmp_path, raw_proof):
    """A caller-supplied or wrong-type proof mints nothing."""
    _, _, raw_bytes, _ = _mint(tmp_path)
    with pytest.raises(ValueError, match="raw-root proof"):
        trust._prepare_synthetic_v2_projection_input(raw_proof, raw_bytes)


def test_prepare_refuses_a_genuine_v1_proof_and_leaves_v1_unaffected(
    tmp_path, monkeypatch,
):
    """A v1-schema proof cannot mint a v2 projection input; v1 stays byte-for-byte."""
    v1_path = tmp_path / "v1"
    v1_path.mkdir()
    _path, roster_publisher, roster, signed, members = _adr132_raw_case(
        v1_path, monkeypatch,
    )
    preflight = trust._SyntheticRawPreflight(
        roster_publisher, trust._SyntheticFixtureSource(members),
    )
    fixture = preflight.verify(*signed, *roster)
    raw_publisher = trust._SyntheticRawPublisher(preflight)
    v1_output = raw_publisher.publish(fixture, *signed, *roster)
    v1_proof = raw_publisher.proof()
    v1_bytes = (*signed, *roster, *v1_output)
    with pytest.raises(ValueError, match="v2 raw proof"):
        trust._prepare_synthetic_v2_projection_input(v1_proof, v1_bytes)
    v1_facts_before = v1_proof.verify(*v1_bytes)
    v1_facts_after = v1_proof.verify(*v1_bytes)
    assert v1_facts_before == v1_facts_after


def test_prepare_refuses_every_mutated_raw_byte_without_mint(tmp_path):
    """A single flipped byte anywhere in the twelve-tuple mints nothing."""
    _, proof, raw_bytes, _ = _mint(tmp_path)
    for index in range(len(raw_bytes)):
        tampered = list(raw_bytes)
        tampered[index] = tampered[index] + b"\n"
        with pytest.raises(ValueError):
            trust._prepare_synthetic_v2_projection_input(proof, tuple(tampered))
    assert trust.consume_v2_projection_input(
        trust._prepare_synthetic_v2_projection_input(proof, raw_bytes)
    )


def test_retained_event_tamper_between_mint_and_consume_refuses_and_terminalizes(
    tmp_path,
):
    """A hostile write through the retained event's backing dict is caught, and the spend is final."""
    case, _, _, capability = _mint(tmp_path)
    _, _, fixture, _, _, _, _ = case
    backing = gc.get_referents(fixture._events[0])[0]
    original = dict(backing)
    backing["source_sequence"] = original["source_sequence"] + 1
    with pytest.raises(ValueError):
        trust.consume_v2_projection_input(capability)
    backing.clear()
    backing.update(original)
    with pytest.raises(ValueError, match="spent"):
        trust.consume_v2_projection_input(capability)


def test_retained_event_tamper_before_prepare_refuses_without_mint(tmp_path):
    """The same hostile write, applied before prepare, refuses at mint time."""
    case = _case(tmp_path)
    _, raw_publisher, fixture, environment, signed, roster, _ = case
    output = raw_publisher.publish_v2(fixture, environment, *signed, *roster)
    proof = raw_publisher.proof()
    raw_bytes = (*signed, *roster, *output)
    backing = gc.get_referents(fixture._events[0])[0]
    original = dict(backing)
    backing["source_sequence"] = original["source_sequence"] + 1
    try:
        with pytest.raises(ValueError):
            trust._prepare_synthetic_v2_projection_input(proof, raw_bytes)
    finally:
        backing.clear()
        backing.update(original)
    assert trust.consume_v2_projection_input(
        trust._prepare_synthetic_v2_projection_input(proof, raw_bytes)
    )


def test_concurrent_consume_of_one_capability_has_at_most_one_winner(tmp_path):
    """Under contended concurrent consume, no capability ever yields two payloads."""
    _, _, _, capability = _mint(tmp_path)
    workers = 6
    barrier = Barrier(workers)

    def contender(_index):
        barrier.wait(timeout=5)
        try:
            return trust.consume_v2_projection_input(capability)
        except Exception:
            return None

    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(contender, range(workers)))
    winners = [result for result in results if result is not None]
    assert len(winners) <= 1
    with pytest.raises(ValueError, match="spent"):
        trust.consume_v2_projection_input(capability)


def test_prepare_and_consume_have_exactly_the_direct_raw_verify_effects(tmp_path):
    """The bridge's only effect is the two existing raw-verify calls, nothing more."""
    bridge_case = _case(tmp_path / "bridge")
    bridge_roster_publisher, bridge_raw_publisher, bridge_fixture, environment, signed, roster, _ = (
        bridge_case
    )
    bridge_output = bridge_raw_publisher.publish_v2(
        bridge_fixture, environment, *signed, *roster
    )
    bridge_proof = bridge_raw_publisher.proof()
    bridge_bytes = (*signed, *roster, *bridge_output)
    bridge_before = _effect_snapshot(
        bridge_roster_publisher, bridge_raw_publisher, bridge_fixture
    )
    capability = trust._prepare_synthetic_v2_projection_input(
        bridge_proof, bridge_bytes
    )
    trust.consume_v2_projection_input(capability)
    bridge_after = _effect_snapshot(
        bridge_roster_publisher, bridge_raw_publisher, bridge_fixture
    )

    direct_case = _case(tmp_path / "direct")
    direct_roster_publisher, direct_raw_publisher, direct_fixture, d_environment, d_signed, d_roster, _ = (
        direct_case
    )
    direct_output = direct_raw_publisher.publish_v2(
        direct_fixture, d_environment, *d_signed, *d_roster
    )
    direct_proof = direct_raw_publisher.proof()
    direct_bytes = (*d_signed, *d_roster, *direct_output)
    direct_before = _effect_snapshot(
        direct_roster_publisher, direct_raw_publisher, direct_fixture
    )
    direct_proof.verify(*direct_bytes)
    direct_proof.verify(*direct_bytes)
    direct_after = _effect_snapshot(
        direct_roster_publisher, direct_raw_publisher, direct_fixture
    )

    bridge_changed = tuple(
        index for index in range(len(bridge_before))
        if bridge_before[index] != bridge_after[index]
    )
    direct_changed = tuple(
        index for index in range(len(direct_before))
        if direct_before[index] != direct_after[index]
    )
    assert bridge_changed == direct_changed
    for index in bridge_changed:
        assert len(bridge_after[index]) - len(bridge_before[index]) == (
            len(direct_after[index]) - len(direct_before[index])
        )


@pytest.mark.parametrize(
    "target, replacement",
    [
        ("json_dumps", lambda *args, **kwargs: "tampered"),
        ("sha256", lambda *args, **kwargs: hashlib.sha256(b"tampered")),
        ("get_referents", lambda *args, **kwargs: []),
    ],
)
def test_preconsume_stdlib_dependency_replacement_refuses_then_permits_retry(
    tmp_path, target, replacement,
):
    """Every effective stdlib dependency the projector reaches is pinned, not just its own globals."""
    _, _, _, capability = _mint(tmp_path)
    if target == "json_dumps":
        original = json.dumps
        json.dumps = replacement
    elif target == "sha256":
        original = hashlib.sha256
        hashlib.sha256 = replacement
    else:
        original = gc.get_referents
        gc.get_referents = replacement
    try:
        with pytest.raises(ValueError, match="dependency changed"):
            production_verifier._project_verified_synthetic_v2_input(capability)
    finally:
        if target == "json_dumps":
            json.dumps = original
        elif target == "sha256":
            hashlib.sha256 = original
        else:
            gc.get_referents = original
    assert production_verifier._project_verified_synthetic_v2_input(capability)


def test_in_place_nested_helper_code_mutation_refuses_before_consume(tmp_path):
    """The recursive walk reaches a helper the projector calls transitively, not only itself."""
    _, _, _, capability = _mint(tmp_path)
    original_code = base._plain.__code__

    def forged(value, path):
        del value, path
        return "forged"

    try:
        base._plain.__code__ = forged.__code__
        with pytest.raises(ValueError, match="dependency changed"):
            production_verifier._project_verified_synthetic_v2_input(capability)
    finally:
        base._plain.__code__ = original_code
    assert production_verifier._project_verified_synthetic_v2_input(capability)


@pytest.mark.parametrize(
    "attribute", ["DATASET_AUTHORIZATION_EVENT_SCHEMAS", "RAW_EVENT_FIELDS"]
)
def test_mutable_schema_table_replacement_refuses_before_consume(
    tmp_path, monkeypatch, attribute,
):
    """A replaced schema-vocabulary table is caught like any other captured global."""
    _, _, _, capability = _mint(tmp_path)
    original = getattr(bundles, attribute)
    replacement = MappingProxyType(dict(original))
    monkeypatch.setattr(bundles, attribute, replacement)
    with pytest.raises(ValueError, match="dependency changed"):
        production_verifier._project_verified_synthetic_v2_input(capability)
    monkeypatch.setattr(bundles, attribute, original)
    assert production_verifier._project_verified_synthetic_v2_input(capability)


# ---------------------------------------------------------------------------
# ADR-0174: v2 captured-tape composition from a verified projection input.
# ---------------------------------------------------------------------------


def test_prepare_v2_projection_input_is_the_exact_same_public_mint(tmp_path):
    """The new public alias is identity-equal to the private mint, not a copy."""
    assert "prepare_v2_projection_input" in trust.__all__
    assert trust.prepare_v2_projection_input is trust._prepare_synthetic_v2_projection_input
    assert not hasattr(pipeline, "prepare_v2_projection_input")
    assert tuple(inspect.signature(
        trust.prepare_v2_projection_input
    ).parameters) == ("raw_proof", "raw_proof_bytes")
    assert all(
        parameter.kind is inspect.Parameter.POSITIONAL_ONLY
        for parameter in inspect.signature(
            trust.prepare_v2_projection_input
        ).parameters.values()
    )
    _, proof, raw_bytes, _capability = _mint(tmp_path)
    minted = trust.prepare_v2_projection_input(proof, raw_bytes)
    assert trust.consume_v2_projection_input(minted)


def test_compose_v2_replay_tape_is_private_positional_only_and_not_exported():
    """The new composer is private, positional-only, and reaches no __all__."""
    assert "_compose_v2_replay_tape" not in production_verifier.__all__
    assert not hasattr(pipeline, "_compose_v2_replay_tape")
    assert not hasattr(bundles, "_compose_v2_replay_tape")
    assert tuple(inspect.signature(
        production_verifier._compose_v2_replay_tape
    ).parameters) == ("raw_proof", "raw_proof_bytes", "capability")
    assert all(
        parameter.kind is inspect.Parameter.POSITIONAL_ONLY
        for parameter in inspect.signature(
            production_verifier._compose_v2_replay_tape
        ).parameters.values()
    )


def test_compose_v2_replay_tape_builds_a_genuine_verified_causally_ordered_tape(
    tmp_path,
):
    """A genuine still-fresh root composes a tape bound to that exact root."""
    _, proof, raw_bytes, capability = _mint(tmp_path)
    expected_envelope_bytes = bundles._project_v2_event_envelopes(
        *trust.consume_v2_projection_input(
            trust._prepare_synthetic_v2_projection_input(proof, raw_bytes)
        )
    )
    tape = production_verifier._compose_v2_replay_tape(
        proof, raw_bytes, capability
    )
    assert isinstance(tape, bundles.CapturedReplayTape)
    assert tape.data_capture_root == hashlib.sha256(raw_bytes[9]).hexdigest()
    assert tape.data_captured_receipt == hashlib.sha256(raw_bytes[11]).hexdigest()
    assert tape.envelope_count == len(expected_envelope_bytes)
    assert tuple(tape.ordered_envelope_digests) == tuple(
        hashlib.sha256(envelope).hexdigest()
        for envelope in expected_envelope_bytes
    )
    reparsed = bundles.CapturedReplayTape.parse(tape.canonical_bytes())
    bundles.verify_causal_order(reparsed, expected_envelope_bytes)


def test_compose_v2_replay_tape_refuses_a_capability_from_an_unrelated_root(
    tmp_path,
):
    """The Revision-1 mixing attack: unrelated capability plus genuine root refuses."""
    _, proof_a, raw_bytes_a, capability_a = _mint(tmp_path / "a")
    _, proof_b, raw_bytes_b, _capability_b = _mint(
        tmp_path / "b",
        event_changes={"payload_sha256": "b" * 64},
    )
    with pytest.raises(ValueError):
        production_verifier._compose_v2_replay_tape(
            proof_b, raw_bytes_b, capability_a
        )
    # The mismatch is terminal: capability_a is spent even though it was
    # never bound to proof_b/raw_bytes_b (ADR-0174 Decision point 3).
    with pytest.raises(ValueError, match="spent"):
        trust.consume_v2_projection_input(capability_a)


def test_compose_v2_replay_tape_refuses_every_nonexact_raw_byte_tuple(tmp_path):
    """Malformed raw_proof_bytes reuses ADR-0172's own prepare refusals."""
    _, proof, _raw_bytes, capability = _mint(tmp_path)
    with pytest.raises(ValueError):
        production_verifier._compose_v2_replay_tape(proof, (), capability)


def test_compose_v2_replay_tape_refuses_a_spent_capability(tmp_path):
    """A capability already consumed elsewhere refuses cleanly."""
    _, proof, raw_bytes, capability = _mint(tmp_path)
    trust.consume_v2_projection_input(capability)
    with pytest.raises(ValueError, match="spent"):
        production_verifier._compose_v2_replay_tape(proof, raw_bytes, capability)


def test_compose_v2_replay_tape_refuses_double_composition(tmp_path):
    """A second composition from the same capability refuses; the first succeeds."""
    _, proof, raw_bytes, capability = _mint(tmp_path)
    first = production_verifier._compose_v2_replay_tape(
        proof, raw_bytes, capability
    )
    assert first is not None
    with pytest.raises(ValueError, match="spent"):
        production_verifier._compose_v2_replay_tape(proof, raw_bytes, capability)


def test_compose_v2_replay_tape_catches_manifest_or_receipt_tamper_after_mint(
    tmp_path,
):
    """A mutated manifest/receipt byte is caught by ADR-0172's own re-verification."""
    _, proof, raw_bytes, capability = _mint(tmp_path)
    tampered = list(raw_bytes)
    tampered[9] = tampered[9] + b"\n"
    with pytest.raises(ValueError):
        production_verifier._compose_v2_replay_tape(
            proof, tuple(tampered), capability
        )


def test_compose_v2_replay_tape_has_no_effect_beyond_three_raw_verify_calls(
    tmp_path,
):
    """The only effects are exactly three raw_proof.verify(...) calls (ADR-0174 point 5)."""
    case = _case(tmp_path)
    roster_publisher, raw_publisher, fixture, environment, signed, roster, _ = case
    output = raw_publisher.publish_v2(fixture, environment, *signed, *roster)
    proof = raw_publisher.proof()
    raw_bytes = (*signed, *roster, *output)
    capability = trust._prepare_synthetic_v2_projection_input(proof, raw_bytes)

    before = _effect_snapshot(roster_publisher, raw_publisher, fixture)
    production_verifier._compose_v2_replay_tape(proof, raw_bytes, capability)
    after_compose = _effect_snapshot(roster_publisher, raw_publisher, fixture)

    proof.verify(*raw_bytes)
    proof.verify(*raw_bytes)
    proof.verify(*raw_bytes)
    after_three_direct_verifies = _effect_snapshot(
        roster_publisher, raw_publisher, fixture
    )

    compose_changed = tuple(
        index for index in range(len(before))
        if before[index] != after_compose[index]
    )
    direct_changed = tuple(
        index for index in range(len(after_compose))
        if after_compose[index] != after_three_direct_verifies[index]
    )
    assert compose_changed == direct_changed
    for index in compose_changed:
        assert len(after_compose[index]) - len(before[index]) == (
            len(after_three_direct_verifies[index]) - len(after_compose[index])
        )
