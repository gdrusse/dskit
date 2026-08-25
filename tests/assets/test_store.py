"""store.py: the pin, write-once records, the append-only event log.

The behavior battery is parametrized over every built-in backend
(ADR-0018): a pack that cannot pass it is not a store. Backend-specific
mechanics (file tampering, concurrency) live next to each pack.
"""

import dataclasses
import json
import os
import shutil

import pytest

from dskit.assets import (
    AssetError,
    AssetRecord,
    FileStore,
    copy_store,
    create_store,
    default_model,
    model_hash,
    open_store,
)

#: Every built-in backend passes the identical battery below.
BACKENDS = ("file", "sqlite", "parquet")


def _require_backend(backend):
    """Skip when a backend's library is absent — the suite must pass
    with no optional dependency installed."""
    if backend == "parquet":
        pytest.importorskip("pyarrow")


@pytest.fixture(params=BACKENDS)
def store(tmp_path, request):
    _require_backend(request.param)
    return create_store(str(tmp_path / "s"), default_model(),
                        backend=request.param)


def _entity(**extra):
    return AssetRecord(kind="entity", payload={"name": "AAPL"}, refs={}, **extra)


# -- normal (every backend) ------------------------------------------------


def test_create_pins_the_model_and_reopens(tmp_path, store):
    root = str(tmp_path / "s")
    assert store.model_pin()["model_hash"] == model_hash(default_model())
    assert open_store(root).model_pin() == store.model_pin()


def test_put_get_list_has(store):
    vid = store.put_record(_entity())
    assert store.get_record(vid).payload["name"] == "AAPL"
    assert store.has_record(vid) and not store.has_record("0" * 64)
    assert store.list_records() == [vid] == store.list_records("entity")
    assert store.list_records("feature") == []


def test_list_records_is_sorted(store):
    # The ABC pins "sorted version_ids" — with 3+ records, insertion
    # order and hash order diverge, so an unsorted backend fails here
    # (round-1 review finding: this had zero coverage on any backend).
    vids = [store.put_record(
        AssetRecord(kind="entity", payload={"name": n}, refs={}))
        for n in ("AAPL", "MSFT", "NVDA")]
    assert store.list_records() == sorted(vids)
    assert store.list_records("entity") == sorted(vids)


def test_events_append_and_replay_in_order(store):
    store.append_event({"event": "register", "n": 1})
    store.append_event({"event": "transition", "n": 2})
    assert [e["n"] for e in store.iter_events()] == [1, 2]


def test_iter_events_is_a_snapshot(store):
    # Pinned contract (ADR-0018): iteration never sees appends made
    # after it begins — identical on every backend.
    store.append_event({"n": 1})
    store.append_event({"n": 2})
    events = store.iter_events()
    assert next(events)["n"] == 1
    store.append_event({"n": 3})
    assert [e["n"] for e in events] == [2]
    assert [e["n"] for e in store.iter_events()] == [1, 2, 3]


@pytest.mark.parametrize("backend", BACKENDS)
def test_model_pin_declares_its_backend(tmp_path, backend):
    _require_backend(backend)
    store = create_store(str(tmp_path / "s"), default_model(), backend=backend)
    assert store.model_pin()["backend"] == backend


# -- edge (every backend) --------------------------------------------------


def test_put_is_write_once_first_provenance_wins(store):
    vid = store.put_record(_entity(registered_at="t1", origin="first"))
    assert store.put_record(_entity(registered_at="t2", origin="later")) == vid
    assert store.get_record(vid).origin == "first"


def test_no_events_iterates_empty(store):
    assert list(store.iter_events()) == []


# -- failure (every backend) -----------------------------------------------


def test_recreate_refused(tmp_path, store):
    with pytest.raises(AssetError, match="exactly once"):
        create_store(str(tmp_path / "s"), default_model())


@pytest.mark.parametrize("leftover",
                         ["records", "events.jsonl", "store.sqlite",
                          "store.sqlite-wal", "events"])
@pytest.mark.parametrize("backend", BACKENDS)
def test_create_refused_over_any_store_artifact(tmp_path, backend, leftover):
    # A crashed create (ANY backend's) is never silently built over —
    # the symmetric half of "a root is initialized exactly once".
    _require_backend(backend)
    root = tmp_path / "s"
    root.mkdir()
    if leftover in ("records", "events"):
        (root / leftover).mkdir()
    else:
        (root / leftover).touch()
    with pytest.raises(AssetError, match="exactly once"):
        create_store(str(root), default_model(), backend=backend)


def test_path_unsafe_kind_refused(store):
    with pytest.raises(AssetError, match="filesystem-safe"):
        store.put_record(AssetRecord(kind="../evil", payload={"name": "x"}, refs={}))


def test_newline_suffixed_identifiers_refused(store):
    # \Z anchors (ADR-0020): $ forgives one trailing newline, so a
    # malformed key or kind could slip the boundary on every backend.
    evil_vid = "f" * 64 + "\n"
    assert not store.has_record(evil_vid)
    with pytest.raises(AssetError, match="64-char"):
        store.get_record(evil_vid)
    with pytest.raises(AssetError, match="filesystem-safe"):
        store.put_record(
            AssetRecord(kind="entity\n", payload={"name": "x"}, refs={}))
    with pytest.raises(AssetError, match="filesystem-safe"):
        store.list_records("entity\n")


def test_unserializable_event_refused(store):
    with pytest.raises(AssetError, match="not JSON-serializable"):
        store.append_event({"x": object()})


# -- the seam: open_store / create_store dispatch (ADR-0018) ---------------


def test_open_store_dispatches_on_declared_backend(tmp_path):
    from dskit.assets.libs.sqlite import SqliteStore

    froot, qroot = str(tmp_path / "f"), str(tmp_path / "q")
    create_store(froot, default_model())
    create_store(qroot, default_model(), backend="sqlite")
    assert isinstance(open_store(froot), FileStore)
    assert isinstance(open_store(qroot), SqliteStore)


def test_open_uninitialized_root_refused(tmp_path):
    with pytest.raises(AssetError, match="not an initialized store"):
        open_store(str(tmp_path / "nowhere"))
    with pytest.raises(AssetError, match="not an initialized store"):
        FileStore(str(tmp_path / "nowhere"))


def test_open_store_unknown_backend_refused(tmp_path):
    root = str(tmp_path / "s")
    create_store(root, default_model())
    meta_path = os.path.join(root, "store.json")
    meta = json.load(open(meta_path))
    meta["backend"] = "carrier-pigeon"
    json.dump(meta, open(meta_path, "w"))
    with pytest.raises(AssetError, match="unknown store backend"):
        open_store(root)


@pytest.mark.skipif(getattr(os, "geteuid", lambda: 1)() == 0,
                    reason="chmod-based denial is inert for root")
@pytest.mark.parametrize("backend", BACKENDS)
def test_create_into_unwritable_parent_is_an_asset_error(tmp_path, backend):
    # Disk failures during create cross the seam wrapped, on every
    # backend — no raw OSError tracebacks (round-3 review finding).
    _require_backend(backend)
    parent = tmp_path / "p"
    parent.mkdir()
    parent.chmod(0o500)
    try:
        with pytest.raises(AssetError, match="cannot initialize"):
            create_store(str(parent / "s"), default_model(), backend=backend)
    finally:
        parent.chmod(0o755)


@pytest.mark.parametrize("backend", BACKENDS)
def test_create_over_a_file_path_is_an_asset_error(tmp_path, backend):
    _require_backend(backend)
    stray = tmp_path / "s"
    stray.touch()
    with pytest.raises(AssetError, match="cannot initialize"):
        create_store(str(stray), default_model(), backend=backend)


def test_create_store_unknown_backend_refused(tmp_path):
    root = str(tmp_path / "s")
    with pytest.raises(AssetError, match="unknown store backend"):
        create_store(root, default_model(), backend="carrier-pigeon")
    # Refused BEFORE touching disk — nothing half-created to clean up.
    assert not os.path.exists(root)


def test_backend_ref_must_resolve_to_a_store(tmp_path):
    with pytest.raises(AssetError, match="not a Store"):
        create_store(str(tmp_path / "s"), default_model(), backend="json:loads")


def test_unimportable_backend_ref_refused(tmp_path):
    with pytest.raises(AssetError, match="cannot import"):
        create_store(str(tmp_path / "s"), default_model(),
                     backend="no_such_pkg.mod:Cls")


def test_class_ref_to_builtin_backend_round_trips(tmp_path):
    # The documented vocabulary allows pkg.module:Class for ANY backend,
    # builtins included — the guard must accept what the ref resolves to.
    root = str(tmp_path / "s")
    create_store(root, default_model())
    meta_path = os.path.join(root, "store.json")
    meta = json.load(open(meta_path))
    meta["backend"] = "dskit.assets.store:FileStore"
    json.dump(meta, open(meta_path, "w"))
    assert isinstance(open_store(root), FileStore)


def test_tier3_subclass_with_inherited_create_round_trips(tmp_path, monkeypatch):
    # A Store subclass that does NOT override create must still record
    # ITSELF as the backend — otherwise reopening silently downgrades
    # to the parent class (round-2 review finding).
    monkeypatch.syspath_prepend(str(tmp_path))
    (tmp_path / "sub_store.py").write_text(
        "from dskit.assets.store import FileStore\n"
        "class MySub(FileStore):\n"
        "    pass\n"
    )
    root = str(tmp_path / "s")
    created = create_store(root, default_model(), backend="sub_store:MySub")
    assert type(created).__name__ == "MySub"
    meta = json.load(open(os.path.join(root, "store.json")))
    assert meta["backend"] == "sub_store:MySub"
    assert type(open_store(root)).__name__ == "MySub"


def test_backend_module_failing_at_import_refused(tmp_path, monkeypatch):
    # An import-time crash in a backend module (common for native
    # drivers) must surface as AssetError, not escape raw.
    monkeypatch.syspath_prepend(str(tmp_path))
    (tmp_path / "boom_store.py").write_text("raise RuntimeError('kaboom')\n")
    with pytest.raises(AssetError, match="cannot import"):
        create_store(str(tmp_path / "s"), default_model(),
                     backend="boom_store:Whatever")


def test_filestore_refuses_a_foreign_backend_root(tmp_path):
    root = str(tmp_path / "s")
    create_store(root, default_model(), backend="sqlite")
    with pytest.raises(AssetError, match="open_store"):
        FileStore(root)


# -- copy_store: any backend to any backend (ADR-0018) ---------------------


@pytest.mark.parametrize("src_backend,dst_backend",
                         [("file", "sqlite"), ("sqlite", "file"),
                          ("file", "parquet"), ("parquet", "sqlite")])
def test_copy_store_replays_records_and_events(tmp_path, src_backend, dst_backend):
    _require_backend(src_backend)
    _require_backend(dst_backend)
    src = create_store(str(tmp_path / "src"), default_model(),
                       backend=src_backend)
    vid = src.put_record(_entity(origin="first"))
    src.append_event({"event": "register", "version_id": vid})
    dst = create_store(str(tmp_path / "dst"), default_model(),
                       backend=dst_backend)
    assert copy_store(src, dst) == {"records": 1, "events": 1}
    assert dst.get_record(vid).to_obj() == src.get_record(vid).to_obj()
    assert list(dst.iter_events()) == list(src.iter_events())


def test_copy_store_empty_copies_nothing(tmp_path):
    src = create_store(str(tmp_path / "src"), default_model())
    dst = create_store(str(tmp_path / "dst"), default_model(), backend="sqlite")
    assert copy_store(src, dst) == {"records": 0, "events": 0}


def test_copy_store_pin_mismatch_refused(tmp_path):
    src = create_store(str(tmp_path / "src"), default_model())
    other = dataclasses.replace(default_model(), name="other")
    dst = create_store(str(tmp_path / "dst"), other, backend="sqlite")
    with pytest.raises(AssetError, match="pin"):
        copy_store(src, dst)


@pytest.mark.parametrize("poison", ["record", "event"])
def test_copy_store_nonempty_destination_refused(tmp_path, poison):
    src = create_store(str(tmp_path / "src"), default_model())
    dst = create_store(str(tmp_path / "dst"), default_model(), backend="sqlite")
    if poison == "record":
        dst.put_record(_entity())
    else:
        dst.append_event({"event": "register"})
    with pytest.raises(AssetError, match="empty"):
        copy_store(src, dst)


# -- FileStore-specific: corruption is a file edit -------------------------


def test_corrupted_record_refused_on_read(tmp_path):
    store = create_store(str(tmp_path / "s"), default_model())
    vid = store.put_record(_entity())
    path = os.path.join(str(tmp_path / "s"), "records", "entity", vid + ".json")
    obj = json.load(open(path))
    obj["payload"]["name"] = "hacked"
    json.dump(obj, open(path, "w"))
    with pytest.raises(AssetError, match="does not match"):
        store.get_record(vid)
    # Verify-on-duplicate (ADR-0020 battery gap): a re-put of the same
    # record is the tamper check the ABC pins, not a silent no-op.
    with pytest.raises(AssetError, match="does not match"):
        store.put_record(_entity())


def test_foreign_record_under_wrong_key_refused(tmp_path):
    # Storage-key trust (ADR-0020): a VALID record copied under another
    # record's filename is refused on read AND on re-put — the rehash
    # alone cannot catch it, the planted body is self-consistent.
    store = create_store(str(tmp_path / "s"), default_model())
    vid_a = store.put_record(_entity())
    vid_b = store.put_record(
        AssetRecord(kind="entity", payload={"name": "MSFT"}, refs={}))
    records = os.path.join(str(tmp_path / "s"), "records", "entity")
    shutil.copyfile(os.path.join(records, vid_b + ".json"),
                    os.path.join(records, vid_a + ".json"))
    with pytest.raises(AssetError, match="storage key"):
        store.get_record(vid_a)
    with pytest.raises(AssetError, match="storage key"):
        store.put_record(_entity())


def test_record_planted_under_wrong_kind_refused(tmp_path):
    # The KIND axis of storage-key trust (ADR-0020): a valid record
    # copied under its own vid but another kind directory must not
    # answer kind-scoped queries — a vid under two kinds proves a
    # plant, since the kind lives inside the hash.
    store = create_store(str(tmp_path / "s"), default_model())
    vid = store.put_record(_entity())
    records = os.path.join(str(tmp_path / "s"), "records")
    os.makedirs(os.path.join(records, "dataset"))
    shutil.copyfile(os.path.join(records, "entity", vid + ".json"),
                    os.path.join(records, "dataset", vid + ".json"))
    with pytest.raises(AssetError, match="more than one kind"):
        store.list_records()
    # _find hits the earlier-sorting plant first — refused loudly.
    with pytest.raises(AssetError, match="stored under kind"):
        store.get_record(vid)


def test_events_log_dir_squat_refused(tmp_path):
    # A directory squatting events.jsonl must not read as an empty
    # history while appends fail loudly on the same root (ADR-0020).
    store = create_store(str(tmp_path / "s"), default_model())
    os.mkdir(os.path.join(str(tmp_path / "s"), "events.jsonl"))
    with pytest.raises(AssetError, match="not a regular file"):
        list(store.iter_events())
    with pytest.raises(AssetError, match="not a regular file"):
        store.append_event({"n": 1})


def test_events_log_broken_symlink_append_refused(tmp_path):
    # "a" through a dangling symlink lands the write wherever the
    # out-of-band link points and silently HEALS the refusal reads
    # gave the same root — so append mirrors the iter_events squat
    # guard and leaves the target uncreated (ADR-0020 round-3 residual).
    root = tmp_path / "s"
    store = create_store(str(root), default_model())
    os.symlink(str(root / "nowhere"), str(root / "events.jsonl"))
    with pytest.raises(AssetError, match="not a regular file"):
        store.append_event({"n": 1})
    assert not (root / "nowhere").exists()
    with pytest.raises(AssetError, match="not a regular file"):
        list(store.iter_events())


def test_events_log_valid_symlink_allowed(tmp_path):
    # The ALLOWED half of the parity: a symlink RESOLVING to a regular
    # file passes lexists+isfile on both append and read — an
    # over-broad islink refusal would break it.
    root = tmp_path / "s"
    store = create_store(str(root), default_model())
    target = tmp_path / "elsewhere.jsonl"
    target.write_text("", encoding="utf-8")
    os.symlink(str(target), str(root / "events.jsonl"))
    store.append_event({"n": 1})
    assert [e["n"] for e in store.iter_events()] == [1]


def test_filestore_sidecars_ignored_and_foreign_entries_refused(tmp_path):
    # ADR-0020: FileStore adopts the foreign-entry doctrine — '.'/'_'
    # prefixed names invisible, anything else unaccountable refused.
    store = create_store(str(tmp_path / "s"), default_model())
    vid = store.put_record(_entity())
    records = os.path.join(str(tmp_path / "s"), "records")
    open(os.path.join(records, ".DS_Store"), "w").close()
    open(os.path.join(records, "_SUCCESS"), "w").close()
    open(os.path.join(records, "entity", ".hidden.json"), "w").close()
    assert store.list_records() == [vid]
    open(os.path.join(records, "entity", "notes.json"), "w").close()
    with pytest.raises(AssetError, match="foreign"):
        store.list_records()


def test_filestore_kind_path_as_file_refused(tmp_path):
    store = create_store(str(tmp_path / "s"), default_model())
    open(os.path.join(str(tmp_path / "s"), "records", "entity"), "w").close()
    with pytest.raises(AssetError, match="foreign"):
        store.list_records("entity")
    with pytest.raises(AssetError, match="foreign"):
        store.list_records()


def test_filestore_dir_squat_refused_on_point_lookup(tmp_path):
    # A directory squatting a record filename splits list (loud) from
    # get/has (silent miss) unless the point lookup refuses too.
    store = create_store(str(tmp_path / "s"), default_model())
    fake = "e" * 64
    os.makedirs(os.path.join(str(tmp_path / "s"), "records", "entity",
                             fake + ".json"))
    with pytest.raises(AssetError, match="foreign"):
        store.get_record(fake)
    with pytest.raises(AssetError, match="foreign"):
        store.has_record(fake)


@pytest.mark.skipif(getattr(os, "geteuid", lambda: 1)() == 0,
                    reason="chmod-based denial is inert for root")
def test_filestore_runtime_io_failures_are_asset_errors(tmp_path):
    # ADR-0020: runtime disk failures cross the seam wrapped, matching
    # the packs — no raw OSError from a put or append on a bad root.
    store = create_store(str(tmp_path / "s"), default_model())
    store.put_record(_entity())
    root = tmp_path / "s"
    (root / "records" / "entity").chmod(0o500)
    root.chmod(0o500)
    try:
        with pytest.raises(AssetError, match="cannot write"):
            store.put_record(
                AssetRecord(kind="entity", payload={"name": "MSFT"}, refs={}))
        with pytest.raises(AssetError, match="cannot append"):
            store.append_event({"n": 1})
    finally:
        root.chmod(0o755)
        (root / "records" / "entity").chmod(0o755)
