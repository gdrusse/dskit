"""P4 method gating must preserve both existing v1 capture facades."""

import inspect

import pytest

from dskit.production import verifier as verifier_module
from tests.pipeline import test_trust as f4
from tests.production import test_capture_lifecycle as legacy


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
