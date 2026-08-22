"""registry.py: the engine — governance, idempotence, derived lifecycle."""

import pytest

from dskit.assets import (
    AssetError,
    AssetModel,
    FieldSpec,
    FileStore,
    KindSpec,
    Registry,
    default_model,
)


# -- normal ----------------------------------------------------------------


def test_register_get_find_list(registry):
    e = registry.register("entity", {"name": "AAPL"}, origin="test")
    assert registry.get(e).origin == "test"
    assert registry.find("entity", "AAPL") == [e]
    assert registry.list("entity") == [e] and registry.list() == [e]


def test_lifecycle_walks_the_spec_chain(registry):
    e = registry.register("entity", {"name": "AAPL"})
    assert registry.state(e) == "draft"
    for state in ("validated", "certified", "published", "deprecated", "retired"):
        registry.transition(e, state)
        assert registry.state(e) == state


def test_alias_with_many_versions(registry):
    e = registry.register("entity", {"name": "AAPL"})
    f1 = registry.register("feature", {"name": "mom"}, refs={"entity": e})
    f2 = registry.register("feature", {"name": "mom", "description": "v2"},
                           refs={"entity": e})
    assert sorted(registry.find("feature", "mom")) == sorted([f1, f2])


# -- edge ------------------------------------------------------------------


def test_reregister_is_idempotent_one_event_only(registry):
    e = registry.register("entity", {"name": "AAPL"})
    assert registry.register("entity", {"name": "AAPL"}) == e
    events = [ev for ev in registry.store.iter_events() if ev["version_id"] == e]
    assert len(events) == 1 and events[0]["state"] == "draft"


def test_record_only_kind_has_no_state(registry):
    run = registry.register("run_observation", {"name": "run-1"})
    assert registry.state(run) is None


# -- failure ---------------------------------------------------------------


def test_model_not_matching_pin_refused(tmp_path):
    store = FileStore.create(str(tmp_path / "s"), default_model())
    other = AssetModel(name="other", kinds={"thing": KindSpec(
        fields={"name": FieldSpec(type="string", required=True)})})
    with pytest.raises(AssetError, match="does not match the store's pin"):
        Registry(store, other)


def test_undeclared_kind_refused(registry):
    with pytest.raises(AssetError, match="not declared"):
        registry.register("gizmo", {"name": "x"})


def test_dangling_and_wrong_kind_refs_refused(registry):
    src = registry.register("source", {"name": "vendor-x"})
    with pytest.raises(AssetError, match="no record"):
        registry.register("feature", {"name": "f"}, refs={"entity": "0" * 64})
    with pytest.raises(AssetError, match="model requires 'entity'"):
        registry.register("feature", {"name": "f"}, refs={"entity": src})


def test_transition_default_deny(registry):
    e = registry.register("entity", {"name": "AAPL"})
    with pytest.raises(AssetError, match="model allows"):
        registry.transition(e, "certified")  # skips validated
    run = registry.register("run_observation", {"name": "run-1"})
    with pytest.raises(AssetError, match="record-only"):
        registry.transition(run, "draft")
