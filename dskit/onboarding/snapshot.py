"""WORM snapshots — bronze evidence with a Merkle manifest (ADR-0014).

Every acquisition lands as one immutable directory::

    raw/<source>/<acq_id>/
    ├── payload/...          # the data-bearing messages, as received
    └── manifest.json        # mode, effective_range, acquired_at,
                             #   files: [{relpath, sha256, size}]

The manifest is the identity: ``snapshot_hash`` is the canonical hash of
manifest CONTENT (which chains the sha256 of every payload file —
Merkle-style, DVC's trick), so the directory path is provenance and any
byte flipped anywhere is detectable by re-hashing (:func:`verify_snapshot`).
The ``acq_id`` directory name embeds acquisition stamp, mode, and the
hash's first 8 chars — sortable by time, self-describing, collision-safe.

Write discipline: payload and manifest are staged in a hidden sibling
directory, fsynced file by file, then the WHOLE directory is renamed
into place — a snapshot exists completely or not at all, and nothing
under ``raw/`` is ever modified after the rename (maildir, ADR-0012).

Import cost: stdlib + this package.
"""

from __future__ import annotations

import json
import os

from .base import (
    AssetError,
    _check_dict,
    _check_iso,
    _check_mode,
    _check_segment,
    _check_str,
    _check_unknown,
    _raise_if,
    canonical_hash,
    durable_write_json,
    file_digest,
    parse_utc,
)
from .layout import OnboardingRoot

__all__ = [
    "build_manifest",
    "find_snapshot_dir",
    "read_manifest",
    "snapshot_hash",
    "verify_snapshot",
    "write_snapshot",
]

#: Keys a manifest carries — default-deny on read, exactly these on write.
_MANIFEST_KEYS = (
    "manifest_version", "source", "mode", "acquired_at", "effective_range", "files",
)


def build_manifest(payload_dir, *, source, mode, acquired_at,
                   effective_start="", effective_end="") -> dict:
    """Walk a staged payload directory into a Merkle manifest.

    Parameters
    ----------
    payload_dir : str
        The staged ``payload/`` directory to fingerprint.
    source : str
        The source this pull came from (filesystem-safe).
    mode : str
        ``"backfill"`` or ``"live"`` — stamped into the manifest so the
        tracking axis survives on disk, independent of any store.
    acquired_at : str
        ISO timestamp of the pull (system time, ADR-0014).
    effective_start, effective_end : str
        The world-time window the records cover (empty when unknown).

    Returns
    -------
    dict
        The manifest object; hash it with :func:`snapshot_hash`.
    """
    errors = []
    _check_str(errors, "payload_dir", payload_dir)
    _check_segment(errors, "source", source)
    _check_mode(errors, mode)
    _check_iso(errors, "acquired_at", acquired_at)
    _check_iso(errors, "effective_start", effective_start, required=False)
    _check_iso(errors, "effective_end", effective_end, required=False)
    _raise_if(errors)
    if not os.path.isdir(payload_dir):
        raise AssetError([f"payload_dir does not exist: {payload_dir!r}"])

    files = []
    for parent, _dirs, names in sorted(os.walk(payload_dir)):
        for fname in sorted(names):
            path = os.path.join(parent, fname)
            rel = os.path.relpath(path, payload_dir)
            files.append({
                # Forward slashes so a manifest hashes identically on
                # every platform — relpath is identity material.
                "relpath": rel.replace(os.sep, "/"),
                "sha256": file_digest(path),
                "size": os.path.getsize(path),
            })
    return {
        "manifest_version": 1,
        "source": source,
        "mode": mode,
        "acquired_at": acquired_at,
        "effective_range": {"start": effective_start, "end": effective_end},
        "files": files,
    }


def snapshot_hash(manifest) -> str:
    """The snapshot's identity: canonical hash of its manifest.

    Because every payload file's sha256 is IN the manifest, this one
    digest covers every byte of the snapshot — Merkle-style.
    """
    errors = []
    _check_dict(errors, "manifest", manifest)
    _raise_if(errors)
    return canonical_hash(manifest)


def _acq_id(manifest) -> str:
    """``<stamp>-<mode>-<hash8>``: time-sortable, self-describing."""
    stamp = parse_utc(manifest["acquired_at"]).strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}-{manifest['mode']}-{snapshot_hash(manifest)[:8]}"


def write_snapshot(root, staged_dir, manifest) -> tuple:
    """Finalize a staged snapshot: write its manifest, rename into raw/.

    Parameters
    ----------
    root : OnboardingRoot
        The onboarding root.
    staged_dir : str
        A staging directory containing ``payload/`` — consumed by this
        call (renamed away).
    manifest : dict
        The manifest built over ``staged_dir/payload`` by
        :func:`build_manifest`.

    Returns
    -------
    tuple
        ``(acq_id, final_dir)`` — the snapshot's directory name and its
        durable location under ``raw/<source>/``.

    Raises
    ------
    AssetError
        If the destination already exists — WORM means a snapshot is
        written exactly once, never overwritten.
    """
    if not isinstance(root, OnboardingRoot):
        raise AssetError([f"root must be an OnboardingRoot, got {type(root).__name__}"])
    errors = []
    _check_str(errors, "staged_dir", staged_dir)
    _check_dict(errors, "manifest", manifest)
    _raise_if(errors)
    _check_unknown(errors, manifest, _MANIFEST_KEYS, "manifest")
    _raise_if(errors)
    if not os.path.isdir(os.path.join(staged_dir, "payload")):
        raise AssetError([f"staged_dir has no payload/: {staged_dir!r}"])

    # Manifest lands inside the staged dir first; the rename below is
    # the commit point that makes the whole snapshot exist at once.
    durable_write_json(os.path.join(staged_dir, "manifest.json"), manifest)
    acq_id = _acq_id(manifest)
    final_dir = root.snapshot_dir(manifest["source"], acq_id)
    if os.path.exists(final_dir):
        raise AssetError(
            [f"snapshot {acq_id!r} already exists — raw/ is WORM, never overwritten"]
        )
    os.makedirs(os.path.dirname(final_dir), exist_ok=True)
    os.rename(staged_dir, final_dir)
    return acq_id, final_dir


def read_manifest(snapshot_dir) -> dict:
    """Load and shape-check one snapshot's manifest.json."""
    errors = []
    _check_str(errors, "snapshot_dir", snapshot_dir)
    _raise_if(errors)
    path = os.path.join(snapshot_dir, "manifest.json")
    try:
        with open(path, encoding="utf-8") as fh:
            manifest = json.load(fh)
    except (OSError, ValueError) as exc:
        raise AssetError([f"cannot read manifest at {path!r}: {exc}"]) from exc
    _check_dict(errors, "manifest", manifest)
    _raise_if(errors)
    _check_unknown(errors, manifest, _MANIFEST_KEYS, "manifest")
    if not isinstance(manifest.get("files"), list):
        errors.append(f"manifest.files must be a list, got {manifest.get('files')!r}")
    _raise_if(errors)
    return manifest


def find_snapshot_dir(root, manifest_hash):
    """Locate the raw/ directory whose manifest hashes to ``manifest_hash``.

    A store record carries the hash, not the path (paths are provenance,
    ADR-0014) — so consumers rediscover the directory by scanning and
    re-hashing, which doubles as an integrity check. O(snapshots), priced
    for the tier-1 scale.

    Returns
    -------
    str or None
        The snapshot directory, or None if no manifest matches.
    """
    if not isinstance(root, OnboardingRoot):
        raise AssetError([f"root must be an OnboardingRoot, got {type(root).__name__}"])
    errors = []
    _check_str(errors, "manifest_hash", manifest_hash)
    _raise_if(errors)
    raw = os.path.join(root.root, "raw")
    for source in sorted(os.listdir(raw)):
        source_dir = os.path.join(raw, source)
        if not os.path.isdir(source_dir):
            continue
        for acq_id in sorted(os.listdir(source_dir)):
            snap_dir = os.path.join(source_dir, acq_id)
            if not os.path.isfile(os.path.join(snap_dir, "manifest.json")):
                continue
            if snapshot_hash(read_manifest(snap_dir)) == manifest_hash:
                return snap_dir
    return None


def verify_snapshot(snapshot_dir) -> list:
    """Re-hash one snapshot against its manifest; return every problem.

    Detects: missing payload files, extra files the manifest never
    listed, and content whose digest or size drifted — DVC-style tamper
    evidence for WORM storage.

    Returns
    -------
    list of str
        Problems found; empty means the snapshot is intact.
    """
    manifest = read_manifest(snapshot_dir)
    payload_dir = os.path.join(snapshot_dir, "payload")
    problems = []

    listed = {f["relpath"]: f for f in manifest["files"]}
    on_disk = set()
    if os.path.isdir(payload_dir):
        for parent, _dirs, names in sorted(os.walk(payload_dir)):
            for fname in sorted(names):
                rel = os.path.relpath(os.path.join(parent, fname), payload_dir)
                on_disk.add(rel.replace(os.sep, "/"))
    elif listed:
        problems.append(f"{snapshot_dir}: payload/ missing entirely")
        return problems

    for rel in sorted(set(listed) - on_disk):
        problems.append(f"{snapshot_dir}: listed file missing: {rel}")
    for rel in sorted(on_disk - set(listed)):
        problems.append(f"{snapshot_dir}: unlisted file present: {rel}")
    for rel in sorted(set(listed) & on_disk):
        path = os.path.join(payload_dir, rel.replace("/", os.sep))
        entry = listed[rel]
        if os.path.getsize(path) != entry.get("size"):
            problems.append(f"{snapshot_dir}: size drift: {rel}")
        elif file_digest(path) != entry.get("sha256"):
            problems.append(f"{snapshot_dir}: content drift: {rel}")
    return problems
