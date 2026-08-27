"""Payload/observation codecs — extension-declared, deterministic (ADR-0036).

The codec a file uses is declared by its NAME: ``<stream>.jsonl`` is
plain UTF-8, ``<stream>.jsonl.gz`` is gzip. Never a manifest field —
``relpath`` is already identity material, digests are over the stored
(post-compression) bytes, and ``verify`` never parses a payload, so the
whole integrity chain is codec-agnostic without changing shape.

Determinism: the gzip member is written with ``filename=""`` (no FNAME
field), ``mtime=0``, and a pinned ``compresslevel``, so the same records
produce the same bytes — and therefore the same acq_id — on a fixed
build. The envelope is per (CPython io/gzip layer, zlib build): zlib-ng
may emit different valid bytes, and CPython's TextIOWrapper/GzipFile
flush behavior contributes sync-flush framing of its own. Tests assert
write-twice equality, never pinned compressed digests. One deliberate
codec asymmetry: gzip text pins ``newline="\\n"`` (bytes must not vary
by platform) while ``"none"`` keeps the platform-default translation —
exact byte parity with the pre-codec tree wins there.

Sources opt in through a reserved ``"storage"`` namespace inside the
source config's ``config`` object (see :func:`check_storage`); both
codecs default ``"none"``, so an undeclared source produces byte-for-byte
the tree it always did.

Loud-not-silent (ADR-0020 parity): a corrupt member crosses every seam
as :class:`AssetError`, never a raw ``OSError``/``zlib.error``.

Import cost: stdlib + this package.
"""

from __future__ import annotations

import gzip
import io
import os
import zlib

from .base import AssetError, _raise_if

__all__ = [
    "CODECS",
    "check_storage",
    "iter_text_lines",
    "open_text_writer",
    "resolve_stream_file",
    "storage_problems",
    "stream_filename",
    "verify_member",
]

#: The closed codec vocabulary. gzip is the one stdlib codec the size
#: argument was measured against (~96x on gz-class archives); zstd-class
#: codecs are barred at tier 1 by the purity gate.
CODECS = ("none", "gzip")

_EXT = {"none": "", "gzip": ".gz"}

#: The storage block's own default-deny surface.
_STORAGE_KEYS = ("notes", "observations_codec", "payload_codec")

#: Pinned: part of the byte-determinism contract, never a knob.
_COMPRESSLEVEL = 9

#: The decode-side failure family a corrupt member can raise.
#: ``gzip.BadGzipFile`` subclasses ``OSError``; ``UnicodeDecodeError``
#: subclasses ``ValueError`` and is named separately on purpose.
_DECODE_ERRORS = (OSError, EOFError, zlib.error, UnicodeDecodeError)


def storage_problems(storage) -> list:
    """Every problem with a ``"storage"`` block, accumulated (never raises)."""
    if not isinstance(storage, dict):
        return [f"storage must be a dict, got {type(storage).__name__}"]
    problems = []
    for key in sorted(storage):
        if key not in _STORAGE_KEYS:
            problems.append(
                f"storage.{key}: unknown key — allowed: {list(_STORAGE_KEYS)}"
            )
    for key in ("payload_codec", "observations_codec"):
        codec = storage.get(key, "none")
        if not isinstance(codec, str) or codec not in CODECS:
            problems.append(
                f"storage.{key} must be one of {list(CODECS)}, got {codec!r}"
            )
    return problems


def check_storage(storage) -> dict:
    """Validate a ``"storage"`` block; return the normalized codec pair.

    Parameters
    ----------
    storage : dict
        The reserved block from a source config's ``config`` object —
        ``{}`` when the source never declared one.

    Returns
    -------
    dict
        ``{"payload_codec": ..., "observations_codec": ...}``, each a
        member of :data:`CODECS` (default ``"none"``).

    Raises
    ------
    AssetError
        Listing every violation at once.
    """
    _raise_if(storage_problems(storage))
    return {
        "payload_codec": storage.get("payload_codec", "none"),
        "observations_codec": storage.get("observations_codec", "none"),
    }


def stream_filename(stream, codec) -> str:
    """The stream's on-disk filename under ``codec`` — the extension IS
    the codec declaration."""
    if codec not in _EXT:
        raise AssetError([f"unknown codec {codec!r} — known: {list(CODECS)}"])
    return f"{stream}.jsonl{_EXT[codec]}"


class _GzipTextWriter:
    """A deterministic gzip text writer whose single ``close()`` settles
    everything in the load-bearing order: text buffer -> gzip trailer ->
    raw file. ``GzipFile`` never closes a passed-in fileobj, so the raw
    handle is this class's to close — a member without its trailer is a
    corrupt member, which is why callers must close BEFORE hashing."""

    def __init__(self, path):
        self._raw = open(path, "wb")
        try:
            # filename="" drops the FNAME header field and mtime=0 zeroes
            # MTIME — the two nondeterministic header bytes gzip would
            # otherwise write. The level is pinned (XFL follows it).
            self._gz = gzip.GzipFile(
                filename="",
                mode="wb",
                fileobj=self._raw,
                compresslevel=_COMPRESSLEVEL,
                mtime=0,
            )
            self._fh = io.TextIOWrapper(self._gz, encoding="utf-8", newline="\n")
        except Exception:
            self._raw.close()
            raise

    def write(self, text):
        return self._fh.write(text)

    def close(self):
        try:
            self._fh.close()  # flushes text, closes gz, writes the trailer
        finally:
            self._raw.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


def open_text_writer(path, codec):
    """A text writer for ``path`` under ``codec``.

    ``"none"`` is EXACTLY today's ``open(path, "w", encoding="utf-8")`` —
    byte parity with the pre-codec tree is the contract. ``"gzip"`` is
    the deterministic member (see :class:`_GzipTextWriter`). Both are
    context managers whose close order is safe to rely on.
    """
    if codec == "none":
        return open(path, "w", encoding="utf-8")
    if codec == "gzip":
        return _GzipTextWriter(path)
    raise AssetError([f"unknown codec {codec!r} — known: {list(CODECS)}"])


def iter_text_lines(path):
    """Yield ``path``'s text lines, sniffing the codec from the extension.

    Every decode-side failure — at open or MID-ITERATION, where gzip
    corruption actually surfaces — crosses the seam as
    :class:`AssetError` naming the path (ADR-0020: loud, typed, never a
    raw ``zlib.error``).
    """
    try:
        if path.endswith(".gz"):
            fh = gzip.open(path, "rt", encoding="utf-8")
        else:
            fh = open(path, encoding="utf-8")
    except _DECODE_ERRORS as exc:
        raise AssetError([f"cannot open {path}: {exc}"]) from exc
    with fh:
        n = 0
        while True:
            try:
                line = fh.readline()
            except _DECODE_ERRORS as exc:
                raise AssetError(
                    [f"corrupt or unreadable stream at {path}:{n + 1}: {exc}"]
                ) from exc
            if not line:
                return
            n += 1
            yield line


def resolve_stream_file(dirpath, stream):
    """The one FILE holding ``stream``'s rows in ``dirpath``, or ``None``.

    Both spellings present at once is tamper-shaped — ``observations/``
    has no manifest, so this refusal is its only both-exist detector.
    A path that exists but is not a regular file (a squatting directory)
    refuses by name rather than being opened or silently skipped.
    """
    plain = os.path.join(dirpath, f"{stream}.jsonl")
    gz = plain + ".gz"
    for path in (plain, gz):
        if os.path.exists(path) and not os.path.isfile(path):
            raise AssetError(
                [f"{path} exists but is not a regular file — the stream "
                 "location is squatted; refusing"]
            )
    has_plain, has_gz = os.path.isfile(plain), os.path.isfile(gz)
    if has_plain and has_gz:
        raise AssetError(
            [f"both {plain} and {gz} exist — ambiguous stream storage; "
             "refusing to pick one"]
        )
    if has_plain:
        return plain
    if has_gz:
        return gz
    return None


def verify_member(path) -> int:
    """Fully decode ``path``; return its line count.

    The pre-commit guard: a buffered writer bug (a member digested
    before its trailer landed) would otherwise mint a snapshot whose
    digests verify forever over undecodable bytes."""
    return sum(1 for _ in iter_text_lines(path))
