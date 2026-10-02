"""artifacts.payload_files: an onboarded source + stream resolved to its
acquired files (ADR-0225) — latest snapshot wins, digests come from the
manifest, ``verify`` re-hashes, and every miss refuses by name."""

import hashlib
import itertools
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import dskit.onboarding
import dskit.onboarding.acquire as acquire_module
from dskit.assets.base import AssetError
from dskit.onboarding import (
    payload_files,
    read_manifest,
    run_acquisition,
    snapshot_hash,
    verified_payload_dir,
)

AS_OF = "2026-01-01T00:00:00+00:00"

FILES = {
    "qqq/options_2012.parquet": b"PAR1\x00\xff qqq 2012 PAR1",
    "qqq/options_2013.parquet": bytes(range(256)) * 4,
    "underlying_prices.parquet": b"PAR1 prices",
}


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _register(registry, name, archive, **config):
    vid = registry.register("source_config", {
        "name": name,
        "catalog_source": f"{name}-src",
        "connector": "localblobs",
        "config": {"path": str(archive), "as_of": AS_OF, **config},
    }, origin="test")
    registry.transition(vid, "active", origin="test")
    return vid


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    """The commit clock, one second per pull, so snapshot order is pull order."""
    ticks = itertools.count()
    start = datetime(2026, 6, 1, 12, tzinfo=timezone.utc)
    monkeypatch.setattr(
        acquire_module, "utc_now",
        lambda: (start + timedelta(seconds=next(ticks))).isoformat(timespec="seconds"),
    )


@pytest.fixture
def archive(tmp_path):
    d = tmp_path / "archive"
    for rel, data in FILES.items():
        (d / rel).parent.mkdir(parents=True, exist_ok=True)
        (d / rel).write_bytes(data)
    return d


@pytest.fixture
def source(registry, archive):
    return _register(registry, "blobs", archive)


def _acquire(root, registry, name="blobs", stream="files"):
    return run_acquisition(root, registry, name, stream, "backfill")


def test_payload_files_is_exported_from_the_package():
    assert dskit.onboarding.payload_files is payload_files
    assert "payload_files" in dskit.onboarding.__all__


def test_it_names_the_snapshot_its_files_and_the_manifest_digests(root, registry, source):
    s = _acquire(root, registry)
    got = payload_files(root, "blobs", "files")
    assert set(got) == {"source", "stream", "snapshot", "manifest_sha256", "files", "sha256"}
    assert (got["source"], got["stream"], got["snapshot"]) == ("blobs", "files", s["acq_id"])
    snap_dir = root.snapshot_dir("blobs", s["acq_id"])
    assert got["manifest_sha256"] == snapshot_hash(read_manifest(snap_dir))
    assert got["manifest_sha256"] == registry.get(s["snapshot"]).payload["manifest_hash"]
    assert list(got["files"]) == sorted(FILES) and list(got["sha256"]) == sorted(FILES)
    for rel, data in FILES.items():
        path = got["files"][rel]
        assert isinstance(path, Path) and path.is_absolute() and path.is_file()
        assert path == Path(snap_dir, "payload", "files", *rel.split("/"))
        assert path.read_bytes() == data
        assert got["sha256"][rel] == _sha(data)


def test_a_path_string_and_an_onboarding_root_are_the_same_root(root, registry, source):
    _acquire(root, registry)
    assert payload_files(root.root, "blobs", "files") == payload_files(root, "blobs", "files")


def test_the_returned_hash_pins_the_same_tree_for_the_hash_keyed_reader(root, registry, source):
    _acquire(root, registry)
    got = payload_files(root, "blobs", "files")
    pinned = Path(verified_payload_dir(root.root, got["manifest_sha256"], "files"))
    assert {rel: pinned.joinpath(*rel.split("/")) for rel in got["files"]} == got["files"]


def test_the_latest_snapshot_wins_after_a_changed_reacquire(root, registry, archive, source):
    first = _acquire(root, registry)
    assert payload_files(root, "blobs", "files")["snapshot"] == first["acq_id"]
    (archive / "qqq" / "options_2012.parquet").write_bytes(b"PAR1 revised")
    (archive / "added.parquet").write_bytes(b"PAR1 added")
    second = _acquire(root, registry)
    assert second["acq_id"] != first["acq_id"]
    got = payload_files(root, "blobs", "files")
    assert got["snapshot"] == second["acq_id"]
    assert got["sha256"]["qqq/options_2012.parquet"] == _sha(b"PAR1 revised")
    assert "added.parquet" in got["files"]
    assert got["files"]["added.parquet"].read_bytes() == b"PAR1 added"
    # WORM: the first snapshot is still there, untouched, just no longer current.
    assert os.path.isdir(root.snapshot_dir("blobs", first["acq_id"]))


def test_an_unchanged_reacquire_leaves_the_current_snapshot_current(root, registry, source):
    first = _acquire(root, registry)
    assert _acquire(root, registry)["snapshot"] is None  # nothing new, no snapshot
    assert payload_files(root, "blobs", "files")["snapshot"] == first["acq_id"]


def test_two_pulls_in_one_second_are_ordered_by_the_manifest_write_time(
    root, registry, archive, source, monkeypatch
):
    monkeypatch.setattr(acquire_module, "utc_now", lambda: "2026-06-01T12:00:00+00:00")
    first = _acquire(root, registry)
    (archive / "underlying_prices.parquet").write_bytes(b"PAR1 prices v2")
    second = _acquire(root, registry)
    assert first["acq_id"][:16] == second["acq_id"][:16]  # same second, as constructed

    def manifest(acq_id):
        return os.path.join(root.snapshot_dir("blobs", acq_id), "manifest.json")

    os.utime(manifest(first["acq_id"]), ns=(10 * 10**9, 10 * 10**9))
    os.utime(manifest(second["acq_id"]), ns=(20 * 10**9, 20 * 10**9))
    assert payload_files(root, "blobs", "files")["snapshot"] == second["acq_id"]
    os.utime(manifest(first["acq_id"]), ns=(30 * 10**9, 30 * 10**9))
    assert payload_files(root, "blobs", "files")["snapshot"] == first["acq_id"]


def test_streams_are_matched_whole_so_a_longer_name_is_not_a_prefix_hit(
    root, registry, archive, source
):
    _register(registry, "other", archive, stream="files2")
    _acquire(root, registry, "other", "files2")
    with pytest.raises(AssetError, match="holds files for stream 'files'"):
        payload_files(root, "other", "files")
    assert list(payload_files(root, "other", "files2")["files"]) == sorted(FILES)


# -- verify ----------------------------------------------------------------------


def test_verify_returns_the_same_answer_for_an_intact_snapshot(root, registry, source):
    _acquire(root, registry)
    assert payload_files(root, "blobs", "files", verify=True) == payload_files(
        root, "blobs", "files")


def test_verify_detects_a_tampered_payload_byte_that_the_cheap_lookup_trusts(
    root, registry, source
):
    _acquire(root, registry)
    got = payload_files(root, "blobs", "files")
    target = got["files"]["qqq/options_2013.parquet"]
    data = bytearray(target.read_bytes())
    data[100] ^= 0xFF  # one flipped byte; the size is unchanged
    target.write_bytes(bytes(data))
    assert payload_files(root, "blobs", "files") == got  # default: manifest-only, no byte read
    with pytest.raises(AssetError, match="failed verification") as exc:
        payload_files(root, "blobs", "files", verify=True)
    assert any("content drift" in e and "qqq/options_2013.parquet" in e for e in exc.value.errors)


def test_verify_lists_every_problem(root, registry, source):
    _acquire(root, registry)
    got = payload_files(root, "blobs", "files")
    got["files"]["underlying_prices.parquet"].unlink()
    got["files"]["qqq/options_2012.parquet"].write_bytes(b"short")
    stray = got["files"]["qqq/options_2013.parquet"].with_name("stray.bin")
    stray.write_bytes(b"x")
    with pytest.raises(AssetError) as exc:
        payload_files(root, "blobs", "files", verify=True)
    text = "\n".join(exc.value.errors)
    assert "listed file missing: files/underlying_prices.parquet" in text
    assert "size drift: files/qqq/options_2012.parquet" in text
    assert "unlisted file present: files/qqq/stray.bin" in text


# -- refusals --------------------------------------------------------------------


def test_an_unknown_source_refuses_by_name(root):
    with pytest.raises(AssetError, match="no source 'ghost'"):
        payload_files(root, "ghost", "files")


def test_a_source_with_no_committed_snapshot_refuses(root):
    raw = root.raw_dir("blobs")
    os.makedirs(os.path.join(raw, ".stage-abc"))  # a crashed pull's debris is not a snapshot
    os.makedirs(os.path.join(raw, "20260601T120000Z-backfill-deadbeef"))  # no manifest
    with pytest.raises(AssetError, match="no committed snapshot"):
        payload_files(root, "blobs", "files")


def test_a_stream_with_no_files_refuses_and_says_what_was_checked(root, registry, source):
    _acquire(root, registry)
    with pytest.raises(AssetError, match="holds files for stream 'prices'") as exc:
        payload_files(root, "blobs", "prices")
    assert "1 checked" in str(exc.value)


def test_a_records_only_stream_has_no_files(root, registry, data_dir):
    # A row connector's snapshot holds payload/prices.jsonl and no payload/prices/ tree.
    vid = registry.register("source_config", {
        "name": "vendor", "catalog_source": "vendor-src", "connector": "localfiles",
        "config": {"path": data_dir, "effective_field": "date"},
    }, origin="test")
    registry.transition(vid, "active", origin="test")
    assert run_acquisition(root, registry, "vendor", "prices", "backfill")["records"] == 3
    with pytest.raises(AssetError, match="holds files for stream 'prices'") as exc:
        payload_files(root, "vendor", "prices")
    assert "records-only" in str(exc.value)


@pytest.mark.parametrize("source_name, stream", [
    ("Blobs", "files"), ("blobs", "Files"), ("blobs", ".."), ("../blobs", "files"), ("", "files"),
])
def test_names_that_are_not_filesystem_safe_refuse(root, source_name, stream):
    with pytest.raises(AssetError, match="filesystem-safe"):
        payload_files(root, source_name, stream)


def test_a_path_that_is_not_an_onboarding_root_refuses(tmp_path):
    with pytest.raises(AssetError, match="not an initialized onboarding root"):
        payload_files(str(tmp_path), "blobs", "files")


def test_an_unreadable_sibling_manifest_is_loud_not_skipped(root, registry, source):
    _acquire(root, registry)
    broken = os.path.join(root.raw_dir("blobs"), "20990101T000000Z-backfill-badbad00")
    os.makedirs(broken)
    with open(os.path.join(broken, "manifest.json"), "w", encoding="utf-8") as fh:
        fh.write("{not json")
    with pytest.raises(AssetError, match="cannot read manifest"):
        payload_files(root, "blobs", "files")  # never a silent fall back to a staler tree
