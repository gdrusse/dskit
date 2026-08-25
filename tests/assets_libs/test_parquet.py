"""libs/parquet.py — what the shared battery can't cover (ADR-0019).

The parametrized battery in ``tests/assets/test_store.py`` proves
ParquetStore behaves like a store; here live the parquet-only
mechanics: the root layout, the analytics-scan claim the pack exists
for, file tampering, half-created roots, and crashed-append leftovers.
"""

import json
import os

import pytest

pa = pytest.importorskip("pyarrow")
import pyarrow.dataset as ds
import pyarrow.parquet as pq

from dskit.assets import (
    AssetError,
    AssetRecord,
    create_store,
    default_model,
    open_store,
)
from dskit.assets.libs.parquet import ParquetStore


@pytest.fixture
def store(tmp_path):
    return create_store(str(tmp_path / "s"), default_model(), backend="parquet")


def _entity(name="AAPL"):
    return AssetRecord(kind="entity", payload={"name": name}, refs={})


def _record_path(tmp_path, vid):
    return os.path.join(str(tmp_path / "s"), "records", "entity",
                        f"{vid}.parquet")


# -- normal ----------------------------------------------------------------


def test_root_layout_is_meta_plus_one_file_per_row(tmp_path, store):
    root = str(tmp_path / "s")
    meta = json.load(open(os.path.join(root, "store.json")))
    assert meta["backend"] == "parquet"
    vid = store.put_record(_entity())
    store.append_event({"event": "register", "version_id": vid})
    assert os.path.isfile(_record_path(tmp_path, vid))
    assert os.path.isfile(os.path.join(root, "events", "00000001.parquet"))


def test_reopen_sees_everything(tmp_path, store):
    vid = store.put_record(_entity())
    store.append_event({"event": "register", "version_id": vid})
    reopened = ParquetStore(str(tmp_path / "s"))
    assert reopened.get_record(vid).payload["name"] == "AAPL"
    assert reopened.list_records() == [vid]
    assert [e["version_id"] for e in reopened.iter_events()] == [vid]


def test_class_ref_backend_round_trips(tmp_path):
    root = str(tmp_path / "s")
    create_store(root, default_model(),
                 backend="dskit.assets.libs.parquet:ParquetStore")
    assert isinstance(open_store(root), ParquetStore)


def test_whole_store_scans_without_the_api(tmp_path, store):
    # The claim the pack exists for (ADR-0019): any parquet engine
    # reads the store directly — uniform schema, plain directories.
    vids = {store.put_record(_entity(name=n)) for n in ("AAPL", "MSFT")}
    for n in (1, 2, 3):
        store.append_event({"n": n})
    root = str(tmp_path / "s")
    records = ds.dataset(os.path.join(root, "records")).to_table()
    assert set(records.column("version_id").to_pylist()) == vids
    assert set(records.column("kind").to_pylist()) == {"entity"}
    events = ds.dataset(os.path.join(root, "events")).to_table()
    bodies = sorted(events.column("seq").to_pylist())
    assert bodies == [1, 2, 3]


# -- edge ------------------------------------------------------------------


def test_event_order_survives_double_digit_sequences(store):
    # Zero-padded filenames: lexical sort must equal numeric order past
    # seq 9, where unpadded names would interleave (1, 10, 11, 2, ...).
    for n in range(1, 12):
        store.append_event({"n": n})
    assert [e["n"] for e in store.iter_events()] == list(range(1, 12))


def test_crashed_append_leftover_is_ignored(tmp_path, store):
    # A temp file from a crashed atomic write never matches the event
    # pattern, so iteration and the next append walk right past it.
    store.append_event({"n": 1})
    events = os.path.join(str(tmp_path / "s"), "events")
    open(os.path.join(events, ".tmp-crashed"), "w").close()
    store.append_event({"n": 2})
    assert [e["n"] for e in store.iter_events()] == [1, 2]


# -- failure ---------------------------------------------------------------


def test_parquet_refuses_a_file_backend_root(tmp_path):
    create_store(str(tmp_path / "s"), default_model())
    with pytest.raises(AssetError, match="open_store"):
        ParquetStore(str(tmp_path / "s"))


def test_tampered_file_refused_on_read(tmp_path, store):
    vid = store.put_record(_entity())
    path = _record_path(tmp_path, vid)
    body = json.loads(pq.read_table(path).column("body")[0].as_py())
    body["payload"]["name"] = "hacked"
    pq.write_table(
        pa.table({"version_id": [vid], "kind": ["entity"],
                  "body": [json.dumps(body, sort_keys=True)]}),
        path,
    )
    with pytest.raises(AssetError, match="does not match"):
        store.get_record(vid)


def test_garbage_file_is_an_asset_error(tmp_path, store):
    # Every failure crossing the seam is an AssetError, never a raw
    # pyarrow exception — parity with the sqlite pack's surface.
    vid = store.put_record(_entity())
    with open(_record_path(tmp_path, vid), "wb") as fh:
        fh.write(b"not parquet at all")
    with pytest.raises(AssetError, match="parquet"):
        store.get_record(vid)


def test_wrong_shape_file_refused(tmp_path, store):
    # A parquet file that is not one row with a body column is not a
    # store file, whatever wrote it.
    vid = store.put_record(_entity())
    pq.write_table(pa.table({"other": [1, 2]}), _record_path(tmp_path, vid))
    with pytest.raises(AssetError, match="one-row store file"):
        store.get_record(vid)


def test_event_with_invalid_json_body_refused(tmp_path, store):
    store.append_event({"n": 1})
    path = os.path.join(str(tmp_path / "s"), "events", "00000001.parquet")
    pq.write_table(pa.table({"seq": [1], "body": ["{not json"]}), path)
    with pytest.raises(AssetError, match="not valid JSON"):
        list(store.iter_events())


def test_half_created_root_refused(tmp_path):
    # A crash can leave events/ without store.json; create must still
    # refuse — a root is initialized exactly once, never repaired.
    root = str(tmp_path / "s")
    os.makedirs(os.path.join(root, "events"))
    with pytest.raises(AssetError, match="exactly once"):
        create_store(root, default_model(), backend="parquet")


def test_missing_directories_refused_on_open(tmp_path, store):
    os.rmdir(os.path.join(str(tmp_path / "s"), "events"))
    with pytest.raises(AssetError, match="damaged or incomplete"):
        open_store(str(tmp_path / "s"))


@pytest.mark.skipif(getattr(os, "geteuid", lambda: 1)() == 0,
                    reason="chmod-based denial is inert for root")
def test_unwritable_root_is_an_asset_error(tmp_path, store):
    # Runtime disk failures cross the seam wrapped too — no raw OSError
    # from a put or append on a read-only root.
    root = tmp_path / "s"
    for sub in ("records", "events"):
        (root / sub).chmod(0o500)
    try:
        with pytest.raises(AssetError, match="parquet store"):
            store.put_record(_entity(name="MSFT"))
        with pytest.raises(AssetError, match="parquet store"):
            store.append_event({"n": 1})
    finally:
        for sub in ("records", "events"):
            (root / sub).chmod(0o755)
