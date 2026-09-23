"""ADR-0170 RED: one bounded, nondeployment synthetic environment fact."""

import copy
import gc
import pickle
from types import MappingProxyType
import weakref

import pytest

import dskit.pipeline as pipeline
from dskit.pipeline import trust


TZDATA_SHA256 = "cd690e4a500811dbc1ca0a79f0e5a8d9eb99debd5dc3c8d9bef5e278bf350cd0"
EXPECTED = {
    "schema_version": "dskit.synthetic-environment-fact/v1",
    "environment_id": "dskit.synthetic-environment/v1",
    "tzdata_version": "synthetic-2026a",
    "tzdata_version_sha256": TZDATA_SHA256,
    "deployment_eligible": False,
}


def _mint():
    return trust._synthetic_environment_identity()


def test_adr170_surface_is_private_and_closed():
    assert "_SyntheticEnvironmentIdentity" not in trust.__all__
    assert "_synthetic_environment_identity" not in trust.__all__
    assert "_synthetic_environment_facts" not in trust.__all__
    assert "_require_synthetic_tzdata" not in trust.__all__
    assert not hasattr(pipeline, "_SyntheticEnvironmentIdentity")
    assert not hasattr(pipeline, "_synthetic_environment_identity")
    assert not hasattr(pipeline, "_synthetic_environment_facts")
    assert not hasattr(pipeline, "_require_synthetic_tzdata")
    for name in (
        "_SYNTHETIC_ENVIRONMENT_PAYLOAD",
        "_SYNTHETIC_ENVIRONMENT_PAYLOAD_SHA256",
        "_SYNTHETIC_ENVIRONMENT_STATE_DOMAIN_SHA256",
        "_SYNTHETIC_ENVIRONMENT_FACTS",
        "_SYNTHETIC_ENVIRONMENT_ISSUED",
        "_SYNTHETIC_ENVIRONMENT_MINT_TOKEN",
    ):
        assert name not in vars(trust)


def test_adr170_factory_has_no_caller_control_and_returns_distinct_facts():
    with pytest.raises(TypeError):
        trust._synthetic_environment_identity("override")
    first = _mint()
    second = _mint()
    assert first is not second
    first_facts = trust._synthetic_environment_facts(first)
    second_facts = trust._synthetic_environment_facts(second)
    assert type(first_facts) is MappingProxyType
    assert type(second_facts) is MappingProxyType
    assert dict(first_facts) == EXPECTED == dict(second_facts)
    assert first_facts is not second_facts
    with pytest.raises(TypeError):
        first_facts["deployment_eligible"] = True


def test_adr170_class_is_final_nonconstructible_and_nonserializable():
    identity = _mint()
    cls = trust._SyntheticEnvironmentIdentity
    with pytest.raises(TypeError):
        cls()
    with pytest.raises(TypeError):
        type("Subclass", (cls,), {})
    with pytest.raises((TypeError, pickle.PicklingError)):
        copy.copy(identity)
    with pytest.raises((TypeError, pickle.PicklingError)):
        copy.deepcopy(identity)
    with pytest.raises((TypeError, pickle.PicklingError)):
        pickle.dumps(identity)


def test_adr170_object_new_and_exact_slots_do_not_forge_identity():
    cls = trust._SyntheticEnvironmentIdentity
    forged = object.__new__(cls)
    for key, value in EXPECTED.items():
        object.__setattr__(forged, "_" + key, value)
    with pytest.raises(ValueError, match="synthetic environment identity"):
        trust._synthetic_environment_facts(forged)
    with pytest.raises(ValueError, match="synthetic environment identity"):
        trust._require_synthetic_tzdata(forged, TZDATA_SHA256)


@pytest.mark.parametrize(("slot", "value"), [
    ("_schema_version", "dskit.synthetic-environment-fact/v2"),
    ("_environment_id", "caller"),
    ("_tzdata_version", "synthetic-2026b"),
    ("_tzdata_version_sha256", "0" * 64),
    ("_deployment_eligible", True),
])
def test_adr170_slot_mutation_refuses(slot, value):
    identity = _mint()
    object.__setattr__(identity, slot, value)
    with pytest.raises(ValueError, match="synthetic environment identity"):
        trust._synthetic_environment_facts(identity)
    with pytest.raises(ValueError, match="synthetic environment identity"):
        trust._require_synthetic_tzdata(identity, TZDATA_SHA256)


def test_adr170_same_named_module_globals_cannot_replace_closure_state(monkeypatch):
    cls = trust._SyntheticEnvironmentIdentity
    mint = trust._synthetic_environment_identity
    facts = trust._synthetic_environment_facts
    require = trust._require_synthetic_tzdata
    monkeypatch.setattr(trust, "_SyntheticEnvironmentIdentity", object)
    monkeypatch.setattr(trust, "_synthetic_environment_identity", lambda: object())
    monkeypatch.setattr(trust, "_synthetic_environment_facts", lambda value: {})
    monkeypatch.setattr(trust, "_require_synthetic_tzdata", lambda *args: None)
    identity = mint()
    assert type(identity) is cls
    assert dict(facts(identity)) == EXPECTED
    assert require(identity, TZDATA_SHA256) is None


@pytest.mark.parametrize("value", [
    None,
    False,
    7,
    "",
    "0" * 64,
    TZDATA_SHA256.upper(),
    TZDATA_SHA256[:-1] + "1",
])
def test_adr170_tzdata_comparison_refuses_nonexact_values(value):
    with pytest.raises(ValueError, match="synthetic tzdata"):
        trust._require_synthetic_tzdata(_mint(), value)


def test_adr170_tzdata_comparison_accepts_exact_digest_without_authority():
    identity = _mint()
    assert trust._require_synthetic_tzdata(identity, TZDATA_SHA256) is None
    assert dict(trust._synthetic_environment_facts(identity)) == EXPECTED


def test_adr170_identity_is_weakly_retained_only():
    identity = _mint()
    reference = weakref.ref(identity)
    del identity
    gc.collect()
    assert reference() is None


def test_adr170_broker_closures_have_no_ambient_capability_names():
    forbidden = {
        "open", "Path", "os", "environ", "getenv", "time", "datetime",
        "random", "secrets", "socket", "subprocess", "provider", "broker",
        "reserve", "session", "publish", "ReplayRun",
    }
    for function in (
        trust._synthetic_environment_identity,
        trust._synthetic_environment_facts,
        trust._require_synthetic_tzdata,
    ):
        assert forbidden.isdisjoint(function.__code__.co_names)
