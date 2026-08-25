"""libs/parquet.py — what the shared battery can't cover (ADR-0019).

The parametrized battery in ``tests/assets/test_store.py`` proves
ParquetStore behaves like a store; here live the parquet-only
mechanics: the root layout, the analytics-scan claim the pack exists
for, file tampering, half-created roots, and crashed-append leftovers.
"""

import json
import os
import shutil

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
    assert sorted(events.column("seq").to_pylist()) == [1, 2, 3]


# -- edge ------------------------------------------------------------------


def test_event_order_survives_double_digit_sequences(store):
    # Zero-padded filenames: lexical sort must equal numeric order past
    # seq 9, where unpadded names would interleave (1, 10, 11, 2, ...).
    for n in range(1, 12):
        store.append_event({"n": n})
    assert [e["n"] for e in store.iter_events()] == list(range(1, 12))


def test_crashed_append_leftover_is_ignored(tmp_path, store):
    # A temp file from a crashed atomic write is '.'-prefixed, so the
    # engine-discovery exemption makes iteration and the next append
    # walk right past it (it would otherwise be refused as foreign).
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
    # store file, whatever wrote it — BOTH halves of the disjunction
    # (round-1 review finding: a 2-row file WITH a body column slipped
    # a mutant that dropped the row-count check).
    vid = store.put_record(_entity())
    good = pq.read_table(_record_path(tmp_path, vid)).column("body")[0].as_py()
    pq.write_table(pa.table({"other": [1, 2]}), _record_path(tmp_path, vid))
    with pytest.raises(AssetError, match="one-row store file") as exc:
        store.get_record(vid)
    # The accurate label must be TOP-LEVEL: AssetError subclasses
    # ValueError, so a careless except would relabel this as a JSON
    # failure and match= alone can't see it (round-2 review finding).
    assert "not valid JSON" not in str(exc.value)
    pq.write_table(pa.table({"body": [good, good]}), _record_path(tmp_path, vid))
    with pytest.raises(AssetError, match="one-row store file"):
        store.get_record(vid)


def test_non_string_body_refused(tmp_path, store):
    vid = store.put_record(_entity())
    pq.write_table(pa.table({"body": [42]}), _record_path(tmp_path, vid))
    with pytest.raises(AssetError, match="body is not a string") as exc:
        store.get_record(vid)
    assert "not valid JSON" not in str(exc.value)


def test_foreign_body_under_wrong_key_refused(tmp_path, store):
    # Storage-key trust (ADR-0020): a VALID record body planted at
    # another version_id's path is refused on read and re-put — the
    # rehash alone cannot catch it, the planted body is self-consistent.
    vid = store.put_record(_entity())
    other = AssetRecord(kind="entity", payload={"name": "MSFT"}, refs={})
    pq.write_table(
        pa.table({"version_id": [vid], "kind": ["entity"],
                  "body": [json.dumps(other.to_obj(), sort_keys=True)]}),
        _record_path(tmp_path, vid),
    )
    with pytest.raises(AssetError, match="storage key"):
        store.get_record(vid)
    with pytest.raises(AssetError, match="storage key"):
        store.put_record(_entity())


def test_record_planted_under_wrong_kind_refused(tmp_path, store):
    # The KIND axis of storage-key trust (ADR-0020): the directory
    # answers kind-scoped queries and engine scans, so it must agree
    # with the record body; a vid under two kinds proves a plant.
    vid = store.put_record(_entity())
    records = os.path.join(str(tmp_path / "s"), "records")
    os.makedirs(os.path.join(records, "dataset"))
    shutil.copyfile(os.path.join(records, "entity", vid + ".parquet"),
                    os.path.join(records, "dataset", vid + ".parquet"))
    with pytest.raises(AssetError, match="more than one kind"):
        store.list_records()
    with pytest.raises(AssetError, match="stored under kind"):
        store.get_record(vid)


def test_reput_of_tampered_record_refused(tmp_path, store):
    # The ABC pins verify-on-duplicate: a re-put of an already-present
    # version_id is a tamper check, not a no-op (round-1 review
    # finding: this branch had no coverage).
    record = _entity()
    vid = store.put_record(record)
    pq.write_table(pa.table({"other": [1]}), _record_path(tmp_path, vid))
    with pytest.raises(AssetError, match="one-row store file"):
        store.put_record(record)


def test_event_with_invalid_json_body_refused(tmp_path, store):
    store.append_event({"n": 1})
    path = os.path.join(str(tmp_path / "s"), "events", "00000001.parquet")
    pq.write_table(pa.table({"seq": [1], "body": ["{not json"]}), path)
    with pytest.raises(AssetError, match="not valid JSON"):
        list(store.iter_events())


def test_full_event_log_refused(tmp_path, store):
    # The _MAX_SEQ guard: pre-seed the last representable sequence and
    # the next append must refuse rather than break lexical ordering.
    pq.write_table(
        pa.table({"seq": [99999999], "body": ["{}"]}),
        os.path.join(str(tmp_path / "s"), "events", "99999999.parquet"),
    )
    with pytest.raises(AssetError, match="event log is full"):
        store.append_event({"n": 1})


def test_foreign_parquet_in_events_refused(tmp_path, store):
    # A *.parquet file the API would skip is one an engine scan WOULD
    # see — the two sanctioned read paths must never silently disagree.
    store.append_event({"n": 1})
    pq.write_table(
        pa.table({"seq": [7], "body": ["{}"]}),
        os.path.join(str(tmp_path / "s"), "events", "extra.parquet"),
    )
    with pytest.raises(AssetError, match="foreign"):
        list(store.iter_events())


def test_wrong_shape_event_file_refused(tmp_path, store):
    # The iter_events read path must report shape failures accurately
    # too, not relabeled as JSON ones (round-2 review finding: this
    # path had no shape coverage at all).
    store.append_event({"n": 1})
    path = os.path.join(str(tmp_path / "s"), "events", "00000001.parquet")
    pq.write_table(pa.table({"other": [1]}), path)
    with pytest.raises(AssetError, match="one-row store file") as exc:
        list(store.iter_events())
    assert "not valid JSON" not in str(exc.value)


def test_unicode_digit_event_filename_refused(tmp_path, store):
    # \d would admit these eight Arabic-Indic digits and int() would
    # parse them, silently hijacking the sequence counter — [0-9]
    # classifies them as foreign instead (round-2 review finding).
    store.append_event({"n": 1})
    name = "٠٠٠٠٠٠٠٧.parquet"
    pq.write_table(
        pa.table({"seq": [7], "body": ["{}"]}),
        os.path.join(str(tmp_path / "s"), "events", name),
    )
    with pytest.raises(AssetError, match="foreign"):
        list(store.iter_events())


def test_engine_ignored_sidecars_are_ignored_here_too(tmp_path, store):
    # Engine discovery skips '.'/'_'-prefixed names (pyarrow, Spark,
    # dask) — so AppleDouble sidecars from a Mac browsing an SMB share
    # are invisible to BOTH read paths and must not brick the store
    # (round-3 review finding: they were refused as "foreign").
    vid = store.put_record(_entity())
    store.append_event({"n": 1})
    root = str(tmp_path / "s")
    for sidecar in (
        os.path.join(root, "events", "._00000001.parquet"),
        os.path.join(root, "events", "_committed.parquet"),
        os.path.join(root, "records", "entity", f"._{vid}.parquet"),
    ):
        with open(sidecar, "wb") as fh:
            fh.write(b"\x00\x05\x16\x07 AppleDouble junk")
    assert [e["n"] for e in store.iter_events()] == [1]
    assert store.list_records() == [vid]
    store.append_event({"n": 2})
    assert [e["n"] for e in store.iter_events()] == [1, 2]


def test_newline_suffixed_stem_refused(tmp_path, store):
    # $ forgives a trailing newline, so the foreign-stem guard must
    # use fullmatch — a '<64hex>\n.parquet' file would otherwise sail
    # through and detonate later in get_record (round-3 finding).
    store.put_record(_entity())
    evil = os.path.join(str(tmp_path / "s"), "records", "entity",
                        "f" * 64 + "\n.parquet")
    pq.write_table(pa.table({"body": ["{}"]}), evil)
    with pytest.raises(AssetError, match="foreign"):
        store.list_records()


def test_dir_fsync_failure_after_replace_is_success(tmp_path, store, monkeypatch):
    # Once os.replace has landed, the write is visible — a failing
    # directory fsync (platforms that refuse directory fds) must NOT
    # surface as an error, or a retry would append a duplicate event
    # (round-2/3 review finding: this pins the best-effort discipline).
    import stat

    real_fsync = os.fsync

    def fsync_refusing_dirs(fd):
        if stat.S_ISDIR(os.fstat(fd).st_mode):
            raise OSError("directory fsync refused")
        return real_fsync(fd)

    monkeypatch.setattr(os, "fsync", fsync_refusing_dirs)
    store.append_event({"n": 1})
    assert [e["n"] for e in store.iter_events()] == [1]


def test_broken_driver_import_still_an_asset_error(tmp_path, store, monkeypatch):
    # Native drivers crash at import with more than ImportError (ABI
    # mismatch, broken wheel) — the probe must wrap ANY exception, so
    # a narrowing "cleanup" of its except clause fails here (round-3).
    import builtins

    real_import = builtins.__import__

    def broken_pyarrow(name, *args, **kwargs):
        if name == "pyarrow" or name.startswith("pyarrow."):
            raise RuntimeError("ABI mismatch")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", broken_pyarrow)
    with pytest.raises(AssetError, match="needs pyarrow"):
        ParquetStore(str(tmp_path / "s"))


def test_foreign_parquet_in_records_refused(tmp_path, store):
    # Same rule as events/: a stem the API cannot account for must be
    # refused loudly, never returned as a garbage version_id.
    store.put_record(_entity())
    pq.write_table(
        pa.table({"body": ["{}"]}),
        os.path.join(str(tmp_path / "s"), "records", "entity", "notes.parquet"),
    )
    with pytest.raises(AssetError, match="foreign"):
        store.list_records()


def test_kind_level_sidecars_ignored_and_strays_refused(tmp_path, store):
    # The kind LEVEL of records/ gets the same rules as the files
    # below it (round-4 review finding: a Finder .DS_Store or Spark
    # _SUCCESS at this level bricked list_records).
    vid = store.put_record(_entity())
    records = os.path.join(str(tmp_path / "s"), "records")
    for sidecar in (".DS_Store", "_SUCCESS"):
        open(os.path.join(records, sidecar), "wb").close()
    assert store.list_records() == [vid]
    # A NON-prefixed stray at kind level is unaccountable — refused.
    open(os.path.join(records, "stray.parquet"), "wb").close()
    with pytest.raises(AssetError, match="foreign"):
        store.list_records()


def test_directory_with_conforming_name_refused(tmp_path, store):
    # Engines write datasets as DIRECTORIES — one named like a record
    # or event passes every name check but splits the API against
    # itself (listed yet unreadable; silently replayed). A conforming
    # name that is not a regular file is foreign (round-6 finding).
    root = str(tmp_path / "s")
    store.put_record(_entity())
    fake = "e" * 64
    os.makedirs(os.path.join(root, "records", "entity", fake + ".parquet"))
    with pytest.raises(AssetError, match="foreign"):
        store.list_records()
    # Point lookups refuse the squat as loudly as enumeration does —
    # never a silent miss that splits list from has/get (ADR-0020).
    with pytest.raises(AssetError, match="foreign"):
        store.get_record(fake)
    with pytest.raises(AssetError, match="foreign"):
        store.has_record(fake)
    os.makedirs(os.path.join(root, "events", "00000001.parquet"))
    with pytest.raises(AssetError, match="foreign"):
        list(store.iter_events())


def test_kind_path_as_file_refused_for_given_kind(tmp_path, store):
    # kind-given must agree with kind=None: records/<kind> existing as
    # a FILE is foreign, not silently an empty kind (round-5 review
    # finding — an engine scan reads that file's rows).
    pq.write_table(
        pa.table({"body": ["{}"]}),
        os.path.join(str(tmp_path / "s"), "records", "entity"),
    )
    with pytest.raises(AssetError, match="foreign"):
        store.list_records("entity")
    with pytest.raises(AssetError, match="foreign"):
        store.list_records()


def test_foreign_check_is_extension_blind(tmp_path, store):
    # Engine discovery reads EVERY non-prefixed name, whatever its
    # extension — so a .PARQUET or extensionless valid parquet file
    # must be refused, not silently skipped (round-4 review finding).
    root = str(tmp_path / "s")
    store.append_event({"n": 1})
    pq.write_table(pa.table({"seq": [99], "body": ["{}"]}),
                   os.path.join(root, "events", "extra.PARQUET"))
    with pytest.raises(AssetError, match="foreign"):
        list(store.iter_events())
    os.remove(os.path.join(root, "events", "extra.PARQUET"))
    store.put_record(_entity())
    pq.write_table(pa.table({"body": ["{}"]}),
                   os.path.join(root, "records", "entity", "rogue"))
    with pytest.raises(AssetError, match="foreign"):
        store.list_records()


def test_missing_driver_refused_loudly(tmp_path, store, monkeypatch):
    # A root created where pyarrow exists, opened where it doesn't
    # (env drift): the missing driver must surface as ONE AssetError at
    # open — never a raw ImportError mid-put (round-1 review finding).
    import builtins

    real_import = builtins.__import__

    def no_pyarrow(name, *args, **kwargs):
        if name == "pyarrow" or name.startswith("pyarrow."):
            raise ImportError("pyarrow is not installed")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_pyarrow)
    with pytest.raises(AssetError, match="needs pyarrow"):
        ParquetStore(str(tmp_path / "s"))
    # create refuses BEFORE touching disk — nothing half-made behind.
    with pytest.raises(AssetError, match="needs pyarrow"):
        create_store(str(tmp_path / "t"), default_model(), backend="parquet")
    assert not os.path.exists(str(tmp_path / "t"))


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
