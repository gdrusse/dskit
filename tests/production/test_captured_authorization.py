"""P4 method gating must preserve both existing v1 capture facades."""

import inspect
import subprocess
import sys

import pytest

from dskit.production import verifier as verifier_module
from dskit.pipeline import trust
from tests.pipeline import test_trust as f4
from tests.production import test_capture_lifecycle as legacy
from tests.pipeline import test_captured_authorization as p4


@pytest.mark.parametrize("facade", ["verifier", "driver"])
@pytest.mark.parametrize("dependency", [
    "terminal_verifier", "terminal_policy", "anchors", "snapshot", "generation",
])
def test_terminal_authority_cannot_be_injected_through_either_p4_facade(facade, dependency):
    p4._terminal_type("TerminalArtifactVerifier")
    broker = p4._factory()()
    verifier = verifier_module.HistoricalStudyVerifier(broker)
    doorway = verifier if facade == "verifier" else verifier_module.HistoricalStudyCaptureDriver(verifier)
    with pytest.raises(TypeError):
        doorway.authorize_capture_set(
            (), p4._request(), **p4._runtime(), **{dependency: object()},
        )
    assert not broker._session_events and not broker._member_events


@pytest.mark.parametrize("name", [
    "HistoricalStudyEnvelopePreflight", "HistoricalStudyRevocations",
    "NonAuthorizingAdr0125StructuralSignaturePreflight",
])
def test_packet3_shared_validator_is_the_same_public_class_at_both_imports(name):
    shared = getattr(trust, name, None)
    assert shared is not None, "Packet 3 shared validator relocation is missing"
    assert shared is getattr(verifier_module, name)
    assert name in trust.__all__ and name in verifier_module.__all__


def test_packet3_shared_validator_keeps_signatures_and_pipeline_import_direction():
    shared = getattr(trust, "HistoricalStudyEnvelopePreflight", None)
    assert shared is not None, "Packet 3 shared validator relocation is missing"
    assert tuple(inspect.signature(shared.__init__).parameters) == (
        "self", "keyring", "clock", "revocations",
    )
    assert tuple(inspect.signature(shared.verify).parameters) == (
        "self", "root_pis_bytes", "phase_pis_bytes", "scope_intent_bytes",
        "action_intent_set_bytes", "edge_set_bytes", "replay_intent_set_bytes",
        "ces_bytes", "pea_bytes", "bvp_bytes", "cas_bytes",
        "selected_admission_bytes",
    )
    result = subprocess.run(
        [sys.executable, "-c", (
            "import sys; from dskit.pipeline.trust import HistoricalStudyEnvelopePreflight; "
            "assert not any(name == 'dskit.production' or "
            "name.startswith('dskit.production.') for name in sys.modules)"
        )],
        check=False, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("facade", ["verifier", "driver"])
def test_v1_p4_refusal_has_no_effect_and_does_not_burn_legacy_admission(facade):
    broker, published, frozen, port = legacy._setup()
    verifier = verifier_module.HistoricalStudyVerifier(broker)
    verifier.bind(**legacy._PLAN)
    doorway = (
        verifier if facade == "verifier"
        else verifier_module.HistoricalStudyCaptureDriver(verifier)
    )
    method = getattr(type(doorway), "authorize_capture_set", None)
    assert callable(method), "Matrix v7 checked P4 facade method is missing"
    before = broker._receipt_audit(published)
    session_events = tuple(broker._session_events)
    member_events = tuple(broker._member_events)
    admission = {
        "kind": "action-execution-admission",
        "role": "study-lifecycle",
        "schema": "dskit.action-execution-admission/v1",
        "sha256": "a" * 64,
    }
    with pytest.raises((TypeError, ValueError), match="capability|P4|authority"):
        method(
            doorway, ((published, frozen, port),), admission,
            consumer_run_identity="consumer-run",
            process_measurement_sha256=f4._SHA["consumer_process"],
            runtime_sha256=f4._SHA["consumer_runtime"],
            transition_nonces=("p4-refused",),
        )
    assert broker._receipt_audit(published) == before
    assert tuple(broker._session_events) == session_events
    assert tuple(broker._member_events) == member_events

    captured, session = legacy._capture(doorway, published, frozen, port)
    assert captured is not None and session is not None
    assert broker._receipt_audit(published)[-1]["event"] == "CAPTURED"
    assert len(broker._session_events) == len(session_events) + 1
    assert tuple(broker._member_events) == member_events


@pytest.mark.parametrize("owner", [
    verifier_module.HistoricalStudyVerifier,
    verifier_module.HistoricalStudyCaptureDriver,
])
def test_p4_facades_do_not_accept_another_authority_or_dependency(owner):
    method = getattr(owner, "authorize_capture_set", None)
    assert callable(method), "Matrix v7 checked P4 facade method is missing"
    parameters = inspect.signature(method).parameters
    assert tuple(parameters) == (
        "self", "captures", "admission_ref", "consumer_run_identity",
        "process_measurement_sha256", "runtime_sha256", "transition_nonces",
    )
    for name in tuple(parameters)[3:]:
        assert parameters[name].kind is inspect.Parameter.KEYWORD_ONLY
        assert parameters[name].default is inspect.Parameter.empty


@pytest.mark.parametrize("facade", ["verifier", "driver"])
def test_p4_facade_rejects_copied_state_and_late_authority_substitution(facade):
    original = p4._factory()()
    replacement = p4._factory()()
    verifier = verifier_module.HistoricalStudyVerifier(original)
    doorway = (
        verifier if facade == "verifier"
        else verifier_module.HistoricalStudyCaptureDriver(verifier)
    )
    verifier._authority = replacement
    method = getattr(type(doorway), "authorize_capture_set", None)
    assert callable(method), "Matrix v7 checked P4 facade method is missing"
    with pytest.raises(ValueError, match="bound.*authority"):
        method(doorway, (), p4._request(), **p4._runtime())
    assert not original._session_events and not replacement._session_events
    verifier._authority = original
    twin = object.__new__(verifier_module.HistoricalStudyVerifier)
    twin.__dict__.update(verifier.__dict__)
    with pytest.raises(ValueError, match="bound.*authority"):
        verifier_module.HistoricalStudyVerifier.authorize_capture_set(
            twin, (), p4._request(), **p4._runtime()
        )


@pytest.mark.parametrize("facade", ["verifier", "driver"])
def test_issued_p4_facades_reach_only_the_same_held_admission_lookup(facade):
    broker = p4._factory()()
    producer, published, _values = f4._publish(broker)
    broker.end_session(producer)
    _document, frozen = f4._freeze(broker, published)
    port = broker.derive_consumer_port(frozen)
    verifier = verifier_module.HistoricalStudyVerifier(broker)
    verifier.bind(**legacy._PLAN)
    doorway = (
        verifier if facade == "verifier"
        else verifier_module.HistoricalStudyCaptureDriver(verifier)
    )
    before = (broker._receipt_audit(published), tuple(broker._session_events))
    with pytest.raises(ValueError, match="missing admission"):
        type(doorway).authorize_capture_set(
            doorway, ((published, frozen, port),), p4._request(), **p4._runtime()
        )
    assert (broker._receipt_audit(published), tuple(broker._session_events)) == before
    captured, session = legacy._capture(doorway, published, frozen, port)
    assert captured is not None and session is not None
    assert broker._receipt_audit(published)[-1]["event"] == "CAPTURED"
    assert not broker._member_events
