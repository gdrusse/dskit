"""Resolve an onboarded source + stream to its acquired files (ADR-0225).

``localblobs`` (and ``huggingface``) land files at
``raw/<source>/<acq_id>/payload/<stream>/<relpath>``, listed in the
snapshot's Merkle manifest. A reader that wants those files should name the
SOURCE and STREAM it consumes, not a directory somewhere on the machine:
:func:`payload_files` finds the latest snapshot of the source that holds
files for the stream and hands back the absolute paths together with the
manifest's digests and the snapshot's identity, so the run can record
exactly which bytes it read.

It is the by-name companion of
:func:`~dskit.onboarding.observations.verified_payload_dir`, which takes a
manifest hash instead (a pinned, content-addressed read). Pin the returned
``manifest_sha256`` to move from one to the other.

Import cost: stdlib + this package.
"""

from __future__ import annotations

import os
from pathlib import Path

from .base import AssetError, _check_segment, _raise_if, parse_utc
from .layout import OnboardingRoot
from .snapshot import read_manifest, snapshot_hash, verify_snapshot

__all__ = ["payload_files", "PinnedPayloadSource"]


def _stream_files(snap_dir, manifest, prefix):
    """Return the manifest's ``(relpath within the stream, sha256)`` pairs under ``prefix``."""
    found = []
    for entry in manifest["files"]:
        rel = entry.get("relpath") if isinstance(entry, dict) else None
        if not isinstance(rel, str):
            raise AssetError([f"{snap_dir}: manifest file entry {entry!r} has no relpath"])
        if rel.startswith(prefix):
            found.append((rel[len(prefix):], entry.get("sha256")))
    return sorted(found)


def _recency(snap_dir, manifest):
    """Sort key of a snapshot: commit instant, then the manifest's write time.

    ``acquired_at`` has second precision, so two pulls inside one second tie;
    the manifest file's mtime (written last, kept by the commit rename)
    orders them, and the acq id is the final deterministic tie-break.
    """
    try:
        committed = parse_utc(manifest.get("acquired_at"))
    except AssetError as exc:
        raise AssetError(
            [f"{snap_dir}: manifest acquired_at is unusable: {e}" for e in exc.errors]
        ) from exc
    return (committed, os.stat(os.path.join(snap_dir, "manifest.json")).st_mtime_ns,
            os.path.basename(snap_dir))


def payload_files(root, source, stream, *, verify=False) -> dict:
    """Return the files of the latest snapshot of ``source`` that holds ``stream``.

    Snapshots are ordered by their commit instant (``acquired_at``, then the
    manifest's write time for pulls in the same second); the newest one
    whose manifest lists files under ``<stream>/`` wins, whatever its mode.
    Every localblobs snapshot is a complete inventory, so the newest is the
    current tree; a stream whose connector emits partial snapshots needs
    :func:`~dskit.onboarding.observations.verified_payload_dir` and a pin.

    The default lookup trusts the manifest and checks nothing on disk
    (cheap: no payload byte is read). ``verify=True`` re-hashes the whole
    snapshot first, as ``verify`` does, and refuses on any drift.

    Parameters
    ----------
    root : str or OnboardingRoot
        An initialized onboarding root, or its path.
    source : str
        The registered source name (the ``raw/`` directory segment).
    stream : str
        The stream whose FILE messages were acquired.
    verify : bool, optional
        Re-hash the chosen snapshot with
        :func:`~dskit.onboarding.snapshot.verify_snapshot` before
        returning; default False.

    Returns
    -------
    dict
        ``source``, ``stream``; ``snapshot`` (the acq id); ``manifest_sha256``
        (the snapshot's identity, as :func:`~dskit.onboarding.snapshot.snapshot_hash`
        and the registry's ``snapshot`` record carry it); ``files`` (relpath
        within the stream -> absolute :class:`~pathlib.Path` of the payload
        file) and ``sha256`` (the same relpaths -> the manifest's digest).

    Raises
    ------
    AssetError
        When ``root`` is not an initialized onboarding root; ``source`` or
        ``stream`` is not filesystem-safe; the source has no ``raw/``
        directory or no committed snapshot; no snapshot holds files for the
        stream; a manifest is unreadable or malformed; or, with
        ``verify=True``, the snapshot fails verification (every problem
        listed).

    Examples
    --------
    Resolve an acquired parquet archive and read one file::

        got = payload_files("./ob", "option-archive", "files", verify=True)
        got["files"]["qqq/options_2012.parquet"]   # absolute Path
        got["manifest_sha256"]                      # record this with the run
    """
    ob = root if isinstance(root, OnboardingRoot) else OnboardingRoot(root)
    errors = []
    _check_segment(errors, "source", source)
    _check_segment(errors, "stream", stream)
    _raise_if(errors)
    raw = ob.raw_dir(source)
    if not os.path.isdir(raw):
        raise AssetError(
            [f"no source {source!r} under {ob.root!r}: {raw!r} does not exist — "
             "acquire it first, or check the name"]
        )
    prefix = f"{stream}/"
    committed = 0
    best = None  # (recency, snapshot dir, manifest, files)
    for acq_id in os.listdir(raw):
        snap_dir = os.path.join(raw, acq_id)
        if acq_id.startswith(".") or not os.path.isfile(
            os.path.join(snap_dir, "manifest.json")
        ):
            continue  # staging debris or a non-snapshot, as find_snapshot_dir skips
        committed += 1
        manifest = read_manifest(snap_dir)
        files = _stream_files(snap_dir, manifest, prefix)
        if not files:
            continue
        key = _recency(snap_dir, manifest)
        if best is None or key > best[0]:
            best = (key, snap_dir, manifest, files)
    if committed == 0:
        raise AssetError([f"source {source!r} has no committed snapshot under {raw!r}"])
    if best is None:
        raise AssetError(
            [f"no snapshot of source {source!r} holds files for stream {stream!r} "
             f"({committed} checked; a records-only stream has no payload/{stream}/ tree)"]
        )
    _key, snap_dir, manifest, files = best
    if verify:
        problems = verify_snapshot(snap_dir)
        if problems:
            raise AssetError(
                [f"snapshot {os.path.basename(snap_dir)} of source {source!r} failed "
                 "verification — refusing to hand out a tree that is not the one "
                 "that was acquired"] + problems
            )
    payload = Path(snap_dir, "payload", stream)
    return {
        "source": source,
        "stream": stream,
        "snapshot": os.path.basename(snap_dir),
        "manifest_sha256": snapshot_hash(manifest),
        "files": {rel: payload.joinpath(*rel.split("/")) for rel, _sha in files},
        "sha256": dict(files),
    }


class PinnedPayloadSource:
    """Resolve explicit acquired file pins without an implicit latest snapshot.

    Parameters
    ----------
    pin : dict
        root, source, snapshot, stream, manifest_sha256 and files (relative
        filename to SHA256). Metadata lookup never opens a payload.

    Examples
    --------
    source = PinnedPayloadSource(pin)
    path = source.file("rows.parquet", verify=True)
    """

    def __init__(self, pin):
        from pathlib import Path
        from dskit.onboarding.layout import OnboardingRoot
        from dskit.onboarding.snapshot import read_manifest, snapshot_hash

        if set(pin) != {"root", "source", "snapshot", "stream", "manifest_sha256", "files"}:
            raise ValueError("explicit pinned source required")
        if (not isinstance(pin["files"], dict) or not pin["files"]
                or any(not isinstance(v, str) or len(v) != 64 for v in pin["files"].values())):
            raise ValueError("nonempty pinned file digests required")
        self.pin = pin
        snapshot = Path(OnboardingRoot(pin["root"]).snapshot_dir(pin["source"], pin["snapshot"]))
        if snapshot.resolve().parent != Path(OnboardingRoot(pin["root"]).raw_dir(pin["source"])).resolve():
            raise ValueError("unsafe snapshot")
        manifest = read_manifest(str(snapshot))
        if snapshot_hash(manifest) != pin["manifest_sha256"] or manifest.get("source") != pin["source"]:
            raise ValueError("source manifest drift or source mismatch")
        if (not isinstance(pin["stream"], str) or Path(pin["stream"]).name != pin["stream"]
                or pin["stream"] in {".", ".."}):
            raise ValueError("unsafe source stream")
        self.directory = snapshot/"payload"/pin["stream"]
        entries = {e["relpath"]: e["sha256"] for e in manifest["files"]}
        for rel, digest in pin["files"].items():
            if Path(rel).is_absolute() or ".." in Path(rel).parts:
                raise ValueError("unsafe relative payload")
            if entries.get(pin["stream"]+"/"+rel) != digest:
                raise ValueError("payload pin absent from manifest: "+rel)

    def file(self, relative, *, verify=False):
        """Resolve one declared file, optionally hashing bytes after authorization.

        Parameters
        ----------
        relative : str
            Exact declared relative filename.
        verify : bool
            Rehash this file; callers must acquire a read permit first.

        Returns
        -------
        str
            Resolved source path.

        Raises
        ------
        ValueError
            Undeclared file, path escape or byte drift.
        """
        from .base import file_digest

        if relative not in self.pin["files"]:
            raise ValueError("undeclared source file")
        path = self.directory/relative
        if not path.resolve().is_relative_to(self.directory.resolve()):
            raise ValueError("payload escaped source")
        if verify and file_digest(str(path)) != self.pin["files"][relative]:
            raise ValueError("source payload drift")
        return str(path)
