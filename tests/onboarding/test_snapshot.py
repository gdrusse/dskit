"""snapshot.py: Merkle manifests, WORM commits, verify, rediscovery."""

import os
import shutil

import pytest

from dskit.assets.base import AssetError
from dskit.onboarding import (
    build_manifest,
    find_snapshot_dir,
    read_manifest,
    snapshot_hash,
    verify_snapshot,
    write_snapshot,
)


def _stage(tmp_path, name="stage", files=None):
    staged = tmp_path / name
    payload = staged / "payload"
    payload.mkdir(parents=True)
    for rel, content in (files or {"s.jsonl": b'{"v":1}\n'}).items():
        p = payload / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(content)
    return str(staged)


def _manifest(staged, **kw):
    args = {"source": "vendor", "mode": "live",
            "acquired_at": "2026-08-23T12:00:00+00:00",
            "effective_start": "2026-01-01", "effective_end": "2026-01-05"}
    args.update(kw)
    return build_manifest(os.path.join(staged, "payload"), **args)


def test_manifest_lists_every_file_with_digest_and_size(tmp_path):
    staged = _stage(tmp_path, files={"a.jsonl": b"aa\n", "sub/b.bin": b"b"})
    m = _manifest(staged)
    assert [f["relpath"] for f in m["files"]] == ["a.jsonl", "sub/b.bin"]
    assert all(len(f["sha256"]) == 64 and f["size"] > 0 for f in m["files"])


def test_hash_covers_payload_bytes_merkle_style(tmp_path):
    a = _manifest(_stage(tmp_path, "s1", {"s.jsonl": b"one\n"}))
    b = _manifest(_stage(tmp_path, "s2", {"s.jsonl": b"two\n"}))
    assert snapshot_hash(a) != snapshot_hash(b)
    # Same bytes, same identity — regardless of which staging dir.
    c = _manifest(_stage(tmp_path, "s3", {"s.jsonl": b"one\n"}))
    assert snapshot_hash(a) == snapshot_hash(c)


def test_write_snapshot_commits_atomically_and_names_by_content(root, tmp_path):
    staged = _stage(tmp_path)
    m = _manifest(staged)
    acq_id, final = write_snapshot(root, staged, m)
    assert acq_id == f"20260823T120000Z-live-{snapshot_hash(m)[:8]}"
    assert not os.path.exists(staged)  # consumed by the rename
    assert read_manifest(final) == m
    assert verify_snapshot(final) == []


def test_worm_never_overwritten(root, tmp_path):
    staged = _stage(tmp_path, "s1")
    m = _manifest(staged)
    write_snapshot(root, staged, m)
    with pytest.raises(AssetError, match="WORM"):
        write_snapshot(root, _stage(tmp_path, "s2"), m)


def test_verify_detects_every_drift_mode(root, tmp_path):
    staged = _stage(tmp_path, files={"a.jsonl": b"aa\n", "b.jsonl": b"bb\n"})
    _acq, final = write_snapshot(root, staged, _manifest(staged))
    payload = os.path.join(final, "payload")

    with open(os.path.join(payload, "a.jsonl"), "wb") as fh:
        fh.write(b"XX\n")  # same size, different bytes
    os.unlink(os.path.join(payload, "b.jsonl"))
    with open(os.path.join(payload, "c.jsonl"), "wb") as fh:
        fh.write(b"new\n")

    problems = "\n".join(verify_snapshot(final))
    assert "content drift: a.jsonl" in problems
    assert "listed file missing: b.jsonl" in problems
    assert "unlisted file present: c.jsonl" in problems


def test_verify_detects_missing_payload_dir(root, tmp_path):
    staged = _stage(tmp_path)
    _acq, final = write_snapshot(root, staged, _manifest(staged))
    shutil.rmtree(os.path.join(final, "payload"))
    assert any("payload/ missing" in p for p in verify_snapshot(final))


def test_find_snapshot_dir_by_hash(root, tmp_path):
    staged = _stage(tmp_path)
    m = _manifest(staged)
    _acq, final = write_snapshot(root, staged, m)
    assert find_snapshot_dir(root, snapshot_hash(m)) == final
    assert find_snapshot_dir(root, "0" * 64) is None


def test_manifest_refuses_unknown_keys_and_bad_modes(root, tmp_path):
    staged = _stage(tmp_path)
    with pytest.raises(AssetError, match="mode"):
        _manifest(staged, mode="sideways")
    m = _manifest(staged)
    m["surprise"] = 1
    with pytest.raises(AssetError, match="unknown key"):
        write_snapshot(root, staged, m)


# -- non-regular members (the review round over ADR-0082) -------------------------


def test_verify_reports_a_symlinked_directory_as_an_unlisted_symlink(root, tmp_path):
    # os.walk never descends a symlinked directory, so without inspecting
    # ``dirs`` a planted link under payload/ was invisible to the gate.
    staged = _stage(tmp_path, files={"a.jsonl": b"aa\n"})
    _acq, final = write_snapshot(root, staged, _manifest(staged))
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "planted.bin").write_bytes(b"x")
    os.symlink(str(elsewhere), os.path.join(final, "payload", "link"))
    assert any("unlisted symlink present: link" in p for p in verify_snapshot(final))


def test_verify_refuses_a_non_regular_member_before_digesting_it(root, tmp_path):
    # A FIFO where a listed file should be would hang the trust gate on
    # open(); the member is judged by lstat FIRST and never opened.
    if not hasattr(os, "mkfifo"):
        pytest.skip("no FIFOs on this platform")
    staged = _stage(tmp_path, files={"a.jsonl": b"aa\n", "b.jsonl": b"bb\n"})
    _acq, final = write_snapshot(root, staged, _manifest(staged))
    payload = os.path.join(final, "payload")
    os.unlink(os.path.join(payload, "a.jsonl"))
    os.mkfifo(os.path.join(payload, "a.jsonl"))
    problems = verify_snapshot(final)
    assert any("not a regular file: a.jsonl" in p for p in problems)
    assert not any("drift: a.jsonl" in p for p in problems)


def test_verify_reports_a_symlinked_member_as_not_regular(root, tmp_path):
    # Identical bytes behind a link would verify clean by digest alone — but
    # a snapshot's member is its own bytes, not a pointer somewhere else.
    staged = _stage(tmp_path, files={"a.jsonl": b"aa\n"})
    _acq, final = write_snapshot(root, staged, _manifest(staged))
    payload = os.path.join(final, "payload")
    real = tmp_path / "real.jsonl"
    real.write_bytes(b"aa\n")
    os.unlink(os.path.join(payload, "a.jsonl"))
    os.symlink(str(real), os.path.join(payload, "a.jsonl"))
    assert any("not a regular file: a.jsonl" in p for p in verify_snapshot(final))
