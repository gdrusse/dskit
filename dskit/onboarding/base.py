"""Shared mechanics for onboarding — reuse from assets, plus durability.

ADR-0013: this package REUSES the assets engine rather than copying it.
Identity hashing, error accumulation, and the validation checkers come
from :mod:`dskit.assets.base`, re-exported here so sibling modules have
one import home and the two packages can never disagree on identity.

What this module ADDS is what the onboarding design demands and the
assets engine does not need:

1. **Maildir-grade durability** (ADR-0012). :func:`durable_write_json` /
   :func:`durable_write_bytes` stage in the destination directory,
   ``fsync`` the file, atomically ``os.replace``, then ``fsync`` the
   directory — a publication or raw snapshot survives a crash or it
   never happened; a reader can never see a torn file.
2. **Byte digests.** :func:`file_digest` is the sha256 of a file's
   bytes — the anchor of a snapshot's Merkle manifest (identity is
   content; paths are provenance).
3. **Bitemporal discipline** (ADR-0014). :func:`parse_utc` turns ISO
   date/datetime strings into aware UTC datetimes (naive treated as
   UTC), so ``effective_date <= acquired_at`` is a real comparison, and
   :func:`_check_iso` refuses malformed dates at the boundary.
4. **Declared modes.** :data:`MODES` is the closed backfill/live
   vocabulary — the project's tracking axis is a declared fact, and a
   typo'd mode is an error, never a silent third cursor.

Import cost: stdlib + :mod:`dskit.assets`.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import tempfile
from datetime import datetime, timezone

# Reused verbatim from the assets engine (ADR-0013): one hash recipe,
# one error type, one checker idiom across both packages.
from dskit.assets.base import (  # noqa: F401  (re-exports)
    AssetError,
    _check_dict,
    _check_str,
    _check_unknown,
    _raise_if,
    atomic_write_json,
    canonical_hash,
    utc_now,
)

__all__ = [
    "AssetError",
    "MODES",
    "canonical_hash",
    "durable_copy_file",
    "durable_write_bytes",
    "durable_write_json",
    "dir_digest",
    "file_digest",
    "fsync_dir",
    "parse_utc",
    "utc_now",
]

#: The acquisition modes (ADR-0014) — backfill pulls history, live pulls
#: forward. Closed vocabulary: checkpoints are keyed per mode, so an
#: unknown mode would silently start a third cursor. Refused instead.
MODES = ("backfill", "live")

#: Sources, streams, datasets, and modes become directory names — the
#: same filesystem-safe rule the assets store applies to kinds.
#: \Z, not $ — $ forgives a trailing newline (ADR-0020).
_SEGMENT = re.compile(r"^[a-z0-9][a-z0-9_-]*\Z")


def _check_segment(errors, name, value):
    """A path segment: lowercase/digits/_/-, because it becomes a directory."""
    if not isinstance(value, str) or not _SEGMENT.match(value):
        errors.append(
            f"{name} must be filesystem-safe (lowercase/digits/_/-), got {value!r}"
        )


def _check_mode(errors, mode):
    if mode not in MODES:
        errors.append(f"mode must be one of {list(MODES)}, got {mode!r}")


def _check_iso(errors, name, value, *, required=True):
    """An ISO date/datetime string, appended to ``errors`` if malformed."""
    if value == "" and not required:
        return
    if not isinstance(value, str) or not value:
        errors.append(f"{name} must be a non-empty ISO date/datetime string, got {value!r}")
        return
    try:
        parse_utc(value)
    except AssetError:
        errors.append(f"{name} must be an ISO date/datetime, got {value!r}")


def parse_utc(value):
    """An ISO date or datetime string as an aware UTC datetime.

    Naive values are treated as UTC — the bitemporal comparison
    ``effective_date <= acquired_at`` (ADR-0014) must never crash on a
    date-only ``effective_date`` against a timezoned ``acquired_at``.

    Parameters
    ----------
    value : str
        e.g. ``"2026-01-31"`` or ``"2026-01-31T12:00:00+00:00"``.

    Returns
    -------
    datetime.datetime
        Aware, in UTC.

    Raises
    ------
    AssetError
        If ``value`` does not parse as ISO-8601.
    """
    if not isinstance(value, str) or not value:
        raise AssetError([f"expected an ISO date/datetime string, got {value!r}"])
    try:
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        # astimezone stays INSIDE the try: a boundary-year offset stamp
        # ("9999-12-31T23:00:00-05:00") parses fine and then leaves the
        # year range here as OverflowError, not ValueError.
        return dt.astimezone(timezone.utc)
    except (ValueError, OverflowError) as exc:
        raise AssetError([f"{value!r} is not an ISO date/datetime: {exc}"]) from exc


# ---------------------------------------------------------------------------
# Durability — the maildir discipline of ADR-0012, for raw/ and published/.
# ---------------------------------------------------------------------------


def fsync_dir(directory):
    """Make a directory entry durable, so a rename or a create survives power loss.

    Best-effort: a platform whose filesystem refuses a directory fd is
    left alone, since the rename itself is still atomic there. Public
    because it is the barrier a WRITER needs, not an internal step of
    :func:`durable_write_bytes` — ``dskit.production.ledger`` fsyncs the
    ledger directory after creating a segment, which is a create, not a
    rename, and must not copy this walk.

    Parameters
    ----------
    directory : str
        The directory to fsync; a path that cannot be opened is a no-op.

    Returns
    -------
    None
        Returns once the directory entry is durable, where the platform
        supports it.
    """
    try:
        fd = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


#: The private spelling this module's own writers were built on; the same
#: object, kept so no internal caller has to change.
_fsync_dir = fsync_dir


def durable_write_bytes(path, data) -> None:
    """Write bytes with maildir discipline: stage, fsync, rename, fsync dir.

    Stronger than :func:`~dskit.assets.base.atomic_write_json`'s
    atomicity (which protects readers): this also survives power loss,
    which the outbox contract (ADR-0012: a publication must never be
    lost) and WORM snapshots (ADR-0014) require.

    Parameters
    ----------
    path : str
        Destination; its directory must exist.
    data : bytes
        The exact bytes to persist.
    """
    errors = []
    _check_str(errors, "path", path)
    if not isinstance(data, bytes):
        errors.append(f"data must be bytes, got {type(data).__name__}")
    _raise_if(errors)
    directory = os.path.dirname(path) or "."
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".tmp-")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    _fsync_dir(directory)


def durable_copy_file(src, dst) -> None:
    """Copy a regular file with maildir discipline: stage, fsync, rename, fsync dir.

    The landing of a ``FILE`` message (ADR-0082): the connector names a
    file it holds, the platform copies it into the staged snapshot with
    the same discipline :func:`durable_write_bytes` gives rows — chunked,
    never held whole, and either fully present at ``dst`` or absent.

    Parameters
    ----------
    src : str
        An existing regular file. It is read, never moved: the
        connector owns its own staging. A symlinked source is FOLLOWED
        (``os.stat``, not ``lstat``): the bytes behind it are copied, so
        the destination is always a regular file of its own.
    dst : str
        Destination path; its directory must exist. Replaced atomically.

    Raises
    ------
    AssetError
        When ``src`` is missing, unreadable or not a regular file, or
        when ``dst`` cannot be written (a directory squatting the path).
    """
    errors = []
    _check_str(errors, "src", src)
    _check_str(errors, "dst", dst)
    _raise_if(errors)
    try:
        st = os.stat(src)
    except OSError as exc:
        raise AssetError([f"cannot read source file {src!r}: {exc}"]) from exc
    if not stat.S_ISREG(st.st_mode):
        raise AssetError([f"source {src!r} is not a regular file"])
    directory = os.path.dirname(dst) or "."
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".tmp-")
    try:
        with os.fdopen(fd, "wb") as out, open(src, "rb") as inp:
            shutil.copyfileobj(inp, out, 1 << 20)
            out.flush()
            os.fsync(out.fileno())
        os.replace(tmp, dst)
    except OSError as exc:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise AssetError([f"cannot copy {src!r} to {dst!r}: {exc}"]) from exc
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    _fsync_dir(directory)


def durable_write_json(path, obj) -> None:
    """:func:`durable_write_bytes` for a JSON object, pretty and sorted.

    Serialization is checked before anything touches disk; output is
    indented with sorted keys — outbox manifests and checkpoints are
    meant to be human-diffable, like store records.

    Raises
    ------
    AssetError
        If ``obj`` is not JSON-serializable (NaN/Infinity refused).
    """
    try:
        text = json.dumps(obj, indent=2, sort_keys=True, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise AssetError([f"object is not JSON-serializable: {exc}"]) from exc
    durable_write_bytes(path, (text + "\n").encode("utf-8"))


def dir_digest(path) -> str:
    """sha256 over every file under ``path`` — names and bytes, sorted.

    Two trees digest equal iff they are byte-identical. This is the cheap
    half of "has this stream changed?": hashing an acquisition dir's
    compressed bytes costs about a second where PARSING the same bytes
    into records costs minutes, so a caller that memoizes a scan can key
    the memo on CONTENT rather than on an mtime a rewrite-in-place would
    not move.

    Parameters
    ----------
    path : str
        The directory to digest. A path that does not exist digests as
        the empty tree — an absent stream is a state, not an error.

    Returns
    -------
    str
        Hex sha256 over each file's tree-relative name (NUL-terminated,
        ``/``-separated) followed by its bytes, in sorted order.

    Raises
    ------
    AssetError
        When ``path`` is not a string, or a file under it cannot be read.
    """
    errors = []
    _check_str(errors, "path", path)
    _raise_if(errors)
    h = hashlib.sha256()
    for root, dirs, files in os.walk(path):
        dirs.sort()
        for name in sorted(files):
            full = os.path.join(root, name)
            rel = os.path.relpath(full, path).replace(os.sep, "/")
            h.update(rel.encode("utf-8"))
            h.update(b"\0")
            try:
                with open(full, "rb") as fh:
                    for chunk in iter(lambda: fh.read(1 << 20), b""):
                        h.update(chunk)
            except OSError as exc:
                raise AssetError([f"cannot read {full!r}: {exc}"]) from exc
    return h.hexdigest()


def file_digest(path) -> str:
    """sha256 of a file's bytes — a manifest entry's identity anchor.

    Parameters
    ----------
    path : str
        The file to digest.

    Returns
    -------
    str
        Hex sha256. Re-hash and compare to detect tampering (the
        ``verify`` command's whole job).
    """
    errors = []
    _check_str(errors, "path", path)
    _raise_if(errors)
    h = hashlib.sha256()
    try:
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
    except OSError as exc:
        raise AssetError([f"cannot read {path!r}: {exc}"]) from exc
    return h.hexdigest()
