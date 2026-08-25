"""libs/sqlite.py — what the shared battery can't cover (ADR-0018).

The parametrized battery in ``tests/assets/test_store.py`` proves
SqliteStore behaves like a store; here live the sqlite-only mechanics:
the root layout, row tampering, half-created roots, and the concurrent
writers FileStore declares away.
"""

import json
import os
import sqlite3
import threading

import pytest

from dskit.assets import (
    AssetError,
    AssetRecord,
    create_store,
    default_model,
    open_store,
)
from dskit.assets.libs.sqlite import SqliteStore


@pytest.fixture
def store(tmp_path):
    return create_store(str(tmp_path / "s"), default_model(), backend="sqlite")


def _entity(name="AAPL"):
    return AssetRecord(kind="entity", payload={"name": name}, refs={})


# -- normal ----------------------------------------------------------------


def test_root_layout_is_meta_plus_one_db(tmp_path, store):
    root = str(tmp_path / "s")
    meta = json.load(open(os.path.join(root, "store.json")))
    assert meta["backend"] == "sqlite"
    assert os.path.isfile(os.path.join(root, "store.sqlite"))


def test_reopen_sees_everything(tmp_path, store):
    vid = store.put_record(_entity())
    store.append_event({"event": "register", "version_id": vid})
    reopened = SqliteStore(str(tmp_path / "s"))
    assert reopened.get_record(vid).payload["name"] == "AAPL"
    assert reopened.list_records() == [vid]
    assert [e["version_id"] for e in reopened.iter_events()] == [vid]


def test_class_ref_backend_round_trips(tmp_path):
    root = str(tmp_path / "s")
    create_store(root, default_model(),
                 backend="dskit.assets.libs.sqlite:SqliteStore")
    assert isinstance(open_store(root), SqliteStore)


def test_concurrent_writers_all_land(store):
    # The limit FileStore declares (single writer) is exactly what this
    # pack lifts: N threads, each its own per-call connection.
    errors = []

    def work(i):
        try:
            vid = store.put_record(_entity(name=f"E{i:02d}"))
            store.append_event({"event": "register", "version_id": vid, "n": i})
        except Exception as exc:  # noqa: BLE001 — the assertion IS the test
            errors.append(exc)

    threads = [threading.Thread(target=work, args=(i,)) for i in range(16)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    assert len(store.list_records("entity")) == 16
    assert sorted(e["n"] for e in store.iter_events()) == list(range(16))


# -- edge ------------------------------------------------------------------


def test_concurrent_same_record_one_row_first_wins(store):
    # Two writers racing the SAME content: write-once must hold.
    results = []
    barrier = threading.Barrier(2)

    def work(origin):
        barrier.wait()
        results.append(store.put_record(
            AssetRecord(kind="entity", payload={"name": "AAPL"}, refs={},
                        origin=origin)))

    threads = [threading.Thread(target=work, args=(o,)) for o in ("a", "b")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(set(results)) == 1
    assert store.list_records("entity") == [results[0]]


# -- failure ---------------------------------------------------------------


def test_sqlite_refuses_a_file_backend_root(tmp_path):
    create_store(str(tmp_path / "s"), default_model())
    with pytest.raises(AssetError, match="open_store"):
        SqliteStore(str(tmp_path / "s"))


def test_garbage_database_is_an_asset_error(tmp_path, store):
    # Every failure crossing the seam is an AssetError, never a raw
    # sqlite3 exception — parity with FileStore's corruption surface.
    with open(os.path.join(str(tmp_path / "s"), "store.sqlite"), "wb") as fh:
        fh.write(b"not a database at all")
    with pytest.raises(AssetError, match="sqlite"):
        store.list_records()


def test_locked_database_fails_as_asset_error(tmp_path, store, monkeypatch):
    import dskit.assets.libs.sqlite as sqlite_pack

    # Shrink the busy timeout so contention surfaces instantly, then
    # hold a write lock from a second connection.
    monkeypatch.setattr(sqlite_pack, "_BUSY_TIMEOUT", 0.05)
    blocker = sqlite3.connect(os.path.join(str(tmp_path / "s"), "store.sqlite"))
    try:
        blocker.execute("BEGIN IMMEDIATE")
        with pytest.raises(AssetError, match="locked"):
            store.append_event({"n": 1})
    finally:
        blocker.rollback()
        blocker.close()


def test_tampered_row_refused_on_read(tmp_path, store):
    vid = store.put_record(_entity())
    conn = sqlite3.connect(os.path.join(str(tmp_path / "s"), "store.sqlite"))
    body = json.loads(conn.execute("SELECT body FROM records").fetchone()[0])
    body["payload"]["name"] = "hacked"
    conn.execute("UPDATE records SET body = ?", (json.dumps(body),))
    conn.commit()
    conn.close()
    with pytest.raises(AssetError, match="does not match"):
        store.get_record(vid)
    # Verify-on-duplicate (ADR-0020 battery gap): the re-put IS the
    # tamper check the ABC pins.
    with pytest.raises(AssetError, match="does not match"):
        store.put_record(_entity())


def test_foreign_row_under_wrong_key_refused(tmp_path, store):
    # Storage-key trust (ADR-0020): a VALID record body planted under
    # another version_id's row is refused on read and re-put — the
    # rehash alone cannot catch it, the planted body is self-consistent.
    vid = store.put_record(_entity())
    other = AssetRecord(kind="entity", payload={"name": "MSFT"}, refs={})
    conn = sqlite3.connect(os.path.join(str(tmp_path / "s"), "store.sqlite"))
    conn.execute("UPDATE records SET body = ? WHERE version_id = ?",
                 (json.dumps(other.to_obj(), sort_keys=True), vid))
    conn.commit()
    conn.close()
    with pytest.raises(AssetError, match="storage key"):
        store.get_record(vid)
    with pytest.raises(AssetError, match="storage key"):
        store.put_record(_entity())


def test_kind_column_divergence_refused(tmp_path, store):
    # The KIND axis of storage-key trust (ADR-0020): the indexed kind
    # column answers kind-scoped queries, so it must agree with the
    # record body — on read and on the verify-on-duplicate path.
    vid = store.put_record(_entity())
    conn = sqlite3.connect(os.path.join(str(tmp_path / "s"), "store.sqlite"))
    conn.execute("UPDATE records SET kind = 'feature' WHERE version_id = ?",
                 (vid,))
    conn.commit()
    conn.close()
    with pytest.raises(AssetError, match="stored under kind"):
        store.get_record(vid)
    with pytest.raises(AssetError, match="stored under kind"):
        store.put_record(_entity())


def test_damaged_root_never_grows_a_stray_db(tmp_path, store):
    # URI mode=rw (ADR-0020): a runtime call against a root whose
    # database vanished fails loudly and leaves NO stray empty
    # store.sqlite behind its own failure.
    db = os.path.join(str(tmp_path / "s"), "store.sqlite")
    os.remove(db)
    with pytest.raises(AssetError, match="sqlite"):
        store.append_event({"n": 1})
    assert not os.path.exists(db)


@pytest.mark.skipif(getattr(os, "geteuid", lambda: 1)() == 0,
                    reason="chmod-based denial is inert for root")
def test_unwritable_root_is_an_asset_error(tmp_path):
    # create-time failures cross the seam wrapped too — the connect
    # itself, not just the schema build (round-2 review finding).
    root = tmp_path / "s"
    root.mkdir()
    root.chmod(0o500)
    try:
        with pytest.raises(AssetError, match="sqlite"):
            create_store(str(root), default_model(), backend="sqlite")
    finally:
        root.chmod(0o755)


def test_half_created_root_refused(tmp_path):
    # A crash can leave store.sqlite without store.json; create must
    # still refuse — a root is initialized exactly once, never repaired.
    root = str(tmp_path / "s")
    os.makedirs(root)
    open(os.path.join(root, "store.sqlite"), "w").close()
    with pytest.raises(AssetError, match="exactly once"):
        create_store(root, default_model(), backend="sqlite")


def test_missing_db_file_refused(tmp_path, store):
    os.remove(os.path.join(str(tmp_path / "s"), "store.sqlite"))
    with pytest.raises(AssetError, match="store.sqlite"):
        open_store(str(tmp_path / "s"))
