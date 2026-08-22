"""store.py: the pin, write-once records, the append-only event log."""

import json
import os

import pytest

from dskit.assets import AssetError, AssetRecord, FileStore, default_model, model_hash


@pytest.fixture
def store(tmp_path):
    return FileStore.create(str(tmp_path / "s"), default_model())


def _entity(**extra):
    return AssetRecord(kind="entity", payload={"name": "AAPL"}, refs={}, **extra)


# -- normal ----------------------------------------------------------------


def test_create_pins_the_model_and_reopens(tmp_path):
    root = str(tmp_path / "s")
    store = FileStore.create(root, default_model())
    assert store.model_pin()["model_hash"] == model_hash(default_model())
    assert FileStore(root).model_pin() == store.model_pin()


def test_put_get_list_has(store):
    vid = store.put_record(_entity())
    assert store.get_record(vid).payload["name"] == "AAPL"
    assert store.has_record(vid) and not store.has_record("0" * 64)
    assert store.list_records() == [vid] == store.list_records("entity")
    assert store.list_records("feature") == []


def test_events_append_and_replay_in_order(store):
    store.append_event({"event": "register", "n": 1})
    store.append_event({"event": "transition", "n": 2})
    assert [e["n"] for e in store.iter_events()] == [1, 2]


# -- edge ------------------------------------------------------------------


def test_put_is_write_once_first_provenance_wins(store):
    vid = store.put_record(_entity(registered_at="t1", origin="first"))
    assert store.put_record(_entity(registered_at="t2", origin="later")) == vid
    assert store.get_record(vid).origin == "first"


def test_no_events_file_iterates_empty(store):
    assert list(store.iter_events()) == []


# -- failure ---------------------------------------------------------------


def test_recreate_refused(tmp_path, store):
    with pytest.raises(AssetError, match="exactly once"):
        FileStore.create(str(tmp_path / "s"), default_model())


def test_open_uninitialized_root_refused(tmp_path):
    with pytest.raises(AssetError, match="not an initialized store"):
        FileStore(str(tmp_path / "nowhere"))


def test_corrupted_record_refused_on_read(tmp_path, store):
    vid = store.put_record(_entity())
    path = os.path.join(str(tmp_path / "s"), "records", "entity", vid + ".json")
    obj = json.load(open(path))
    obj["payload"]["name"] = "hacked"
    json.dump(obj, open(path, "w"))
    with pytest.raises(AssetError, match="does not match"):
        store.get_record(vid)


def test_path_unsafe_kind_refused(store):
    with pytest.raises(AssetError, match="filesystem-safe"):
        store.put_record(AssetRecord(kind="../evil", payload={"name": "x"}, refs={}))


def test_unserializable_event_refused(store):
    with pytest.raises(AssetError, match="not JSON-serializable"):
        store.append_event({"x": object()})
