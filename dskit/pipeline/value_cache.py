"""A content-addressed, digest-verified local cache of plain Python values.

A caller builds the key from everything that decides the value (input
digests, parameters, the source of the producing code, runtime versions)
with :func:`key_digest`; the cache stores the value under that key and
hands it back only when its bytes still match the digest written beside
them. Every miss, mismatch, unreadable file or foreign type is a miss: the
caller recomputes, so a cache can only save time, never change a result.

Only *plain* values are stored: ``None``, ``bool``, ``int``, ``float``,
``str``, ``bytes`` and ``list``/``tuple``/``dict``/``set`` of them, exact
types (a subclass such as a numpy scalar refuses). They are written with
:mod:`pickle` because it round-trips every float bit, the container types
and object sharing exactly (JSON turns tuples into lists and loses
sharing), and read back by an unpickler that refuses every global, so a
cache file can never name a class or a callable.

Layout under ``root/namespace``: ``<key[:2]>/<key>.pkl.gz`` (the gzip
stream of the pickle) and ``<key[:2]>/<key>.json`` (format, namespace,
key, sha256 and size of the ``.pkl.gz`` bytes). Both are published by an
fsynced temporary file and ``os.replace``, blob first, so a reader that
finds a manifest finds its blob; a concurrent writer of the same key
writes the same bytes. ``max_bytes`` bounds a namespace: after a store the
least recently used entries (a hit refreshes the mtime) are removed.
"""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import os
import pickle
import tempfile
from pathlib import Path

__all__ = ["MISS", "PlainValueError", "VerifiedValueCache", "key_digest", "plain_digest", "plain_dump",
           "plain_load", "runtime_versions", "source_digest"]

#: What :meth:`VerifiedValueCache.load` returns when nothing usable is stored.
MISS = object()

FORMAT = "dskit-verified-value-cache/1"

_PLAIN = (type(None), bool, int, float, str, bytes, list, tuple, dict, set, frozenset)


class PlainValueError(ValueError):
    """The value holds something other than plain builtin data."""


class _PlainPickler(pickle.Pickler):
    """Pickle exact builtin data only.

    ``reducer_override`` is consulted for every object that is not an exact
    instance of the builtin types pickle saves natively, so anything else
    (a numpy scalar, an array, a class instance) refuses here.
    """

    def reducer_override(self, obj):
        if type(obj) in _PLAIN:
            return NotImplemented
        raise PlainValueError(f"not a plain value: {type(obj).__module__}.{type(obj).__qualname__}")


class _PlainUnpickler(pickle.Unpickler):
    """Load builtin data only: any global (class, function) refuses."""

    def find_class(self, module, name):
        raise pickle.UnpicklingError(f"global refused in a plain value: {module}.{name}")


class _HashingWriter(io.RawIOBase):
    """Write through to ``target`` while hashing and counting the bytes."""

    def __init__(self, target):
        self.target, self.sha, self.size = target, hashlib.sha256(), 0

    def writable(self):
        return True

    def write(self, data):
        view = memoryview(data)
        self.target.write(view)
        self.sha.update(view)
        self.size += len(view)
        return len(view)


def plain_dump(value, handle):
    """Write ``value`` as a gzip stream of its plain pickle to the binary ``handle``.

    Raises
    ------
    PlainValueError
        The value holds a non-plain object.
    """
    with gzip.GzipFile(fileobj=handle, mode="wb", compresslevel=1, mtime=0) as stream:
        _PlainPickler(stream, protocol=5).dump(value)


def plain_load(handle):
    """Read a value written by :func:`plain_dump`, refusing any global."""
    with gzip.GzipFile(fileobj=handle, mode="rb") as stream:
        return _PlainUnpickler(stream).load()


def plain_digest(value):
    """Return the sha256 of ``value``'s plain pickle (no compression).

    Raises
    ------
    PlainValueError
        The value holds a non-plain object.
    """
    writer = _HashingWriter(_Discard())
    _PlainPickler(writer, protocol=5).dump(value)
    return writer.sha.hexdigest()


class _Discard:
    def write(self, data):
        return len(data)


def key_digest(*parts):
    """Return the sha256 of the canonical JSON of ``parts`` (sorted keys, no NaN)."""
    raw = json.dumps(parts, sort_keys=True, separators=(",", ":"), allow_nan=False, ensure_ascii=True)
    return hashlib.sha256(raw.encode("ascii")).hexdigest()


_SOURCE_MEMO = {}


def source_digest(paths):
    """Return one digest of the named source files' names and bytes.

    Parameters
    ----------
    paths : iterable of str or Path
        Files; the digest covers each file's base name and bytes in sorted
        path order. A file is re-read when its size or mtime changes.
    """
    sha = hashlib.sha256()
    for path in sorted(str(p) for p in paths):
        stat = os.stat(path)
        memo = _SOURCE_MEMO.get(path)
        if memo is None or memo[0] != (stat.st_size, stat.st_mtime_ns):
            with open(path, "rb") as handle:
                memo = ((stat.st_size, stat.st_mtime_ns), hashlib.sha256(handle.read()).hexdigest())
            _SOURCE_MEMO[path] = memo
        sha.update(f"{os.path.basename(path)}\0{memo[1]}\n".encode("utf-8"))
    return sha.hexdigest()


def runtime_versions(*distributions):
    """Return the Python version and each named distribution's installed version (None when absent)."""
    import sys
    from importlib import metadata

    out = {"python": list(sys.version_info[:3]), "implementation": sys.implementation.name}
    for name in distributions:
        try:
            out[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            out[name] = None
    return out


class VerifiedValueCache:
    """One namespace of digest-verified plain values under a local root.

    Parameters
    ----------
    root : str or Path
        Absolute cache root (created with mode 0700 when absent).
    namespace : str
        Subdirectory naming what the values are; part of every manifest.
    max_bytes : int or None, optional
        Bound on the namespace's bytes; least recently used entries are
        removed after a store. None keeps everything.
    log : logging.Logger or None, optional
        Receives one warning per refused entry or failed store.
    """

    def __init__(self, root, namespace, *, max_bytes=None, log=None):
        root = Path(root)
        if not root.is_absolute() or not namespace or "/" in namespace or namespace.startswith("."):
            raise ValueError("an absolute cache root and a plain namespace are required")
        if max_bytes is not None and (type(max_bytes) is not int or max_bytes <= 0):
            raise ValueError("max_bytes must be a positive int or None")
        self.directory = root / namespace
        self.namespace, self.max_bytes, self.log = namespace, max_bytes, log
        self.counts = {"hit": 0, "miss": 0, "refused": 0, "stored": 0, "store_failed": 0}

    def _paths(self, key):
        if (not isinstance(key, str) or len(key) != 64
                or any(c not in "0123456789abcdef" for c in key)):
            raise ValueError("a cache key is a sha256 hex digest")
        folder = self.directory / key[:2]
        return folder / f"{key}.pkl.gz", folder / f"{key}.json"

    def _warn(self, message, *args):
        if self.log is not None:
            self.log.warning("value cache %s: " + message, self.namespace, *args)

    @staticmethod
    def _regular(path):
        import stat
        info = os.lstat(path)
        if not stat.S_ISREG(info.st_mode):
            raise ValueError("cache entry is not a regular file")
        return info

    def load(self, key):
        """Return the value stored under ``key``, or :data:`MISS`.

        A missing entry is a miss; an entry whose manifest, digest, size or
        pickle does not verify is a refused miss (and is removed).
        """
        blob, manifest = self._paths(key)
        if not manifest.exists() and not blob.exists():
            self.counts["miss"] += 1
            return MISS
        try:
            self._regular(manifest)
            record = json.loads(manifest.read_text(encoding="utf-8"))
            if (not isinstance(record, dict)
                    or record != {"format": FORMAT, "namespace": self.namespace, "key": key,
                                  "sha256": record.get("sha256"), "bytes": record.get("bytes")}
                    or not isinstance(record["sha256"], str) or type(record["bytes"]) is not int):
                raise ValueError("invalid cache manifest")
            self._regular(blob)
            raw = blob.read_bytes()  # one read: the bytes verified are the bytes decoded
            if len(raw) != record["bytes"]:
                raise ValueError("cache blob size mismatch")
            if hashlib.sha256(raw).hexdigest() != record["sha256"]:
                raise ValueError("cache blob digest mismatch")
            value = plain_load(io.BytesIO(raw))
            del raw
        except (OSError, ValueError, EOFError, pickle.UnpicklingError, gzip.BadGzipFile) as exc:
            self.counts["refused"] += 1
            self._warn("refused %s (%s); recomputing", key[:12], exc)
            for path in (manifest, blob):
                try:
                    path.unlink()
                except OSError:
                    pass
            return MISS
        except Exception as exc:  # any other decode failure is still only a miss
            self.counts["refused"] += 1
            self._warn("refused %s (%s: %s); recomputing", key[:12], type(exc).__name__, exc)
            return MISS
        try:
            os.utime(blob)
        except OSError:
            pass
        self.counts["hit"] += 1
        return value

    def _publish(self, folder, suffix, write):
        fd, temp = tempfile.mkstemp(prefix=".tmp-", suffix=suffix, dir=folder)
        try:
            with os.fdopen(fd, "wb") as handle:
                result = write(handle)
                handle.flush()
                os.fsync(handle.fileno())
            return temp, result
        except BaseException:
            os.unlink(temp)
            raise

    def store(self, key, value):
        """Store ``value`` under ``key``; return True when it was published.

        A non-plain value or an I/O failure stores nothing and returns False;
        the caller's result is unaffected either way.
        """
        blob, manifest = self._paths(key)
        temps = []
        try:
            blob.parent.mkdir(mode=0o700, parents=True, exist_ok=True)

            def write_blob(handle):
                writer = _HashingWriter(handle)
                plain_dump(value, writer)
                return writer.sha.hexdigest(), writer.size

            temp, (sha, size) = self._publish(blob.parent, ".pkl.gz", write_blob)
            temps.append(temp)
            os.replace(temp, blob)
            temps.remove(temp)
            body = json.dumps({"format": FORMAT, "namespace": self.namespace, "key": key,
                               "sha256": sha, "bytes": size}, sort_keys=True).encode("utf-8")
            temp, _ = self._publish(blob.parent, ".json", lambda handle: handle.write(body))
            temps.append(temp)
            os.replace(temp, manifest)
            temps.remove(temp)
        except PlainValueError as exc:
            self.counts["store_failed"] += 1
            self._warn("not stored (%s)", exc)
            return False
        except OSError as exc:
            self.counts["store_failed"] += 1
            self._warn("store failed (%s)", exc)
            for temp in temps:
                try:
                    os.unlink(temp)
                except OSError:
                    pass
            return False
        self.counts["stored"] += 1
        if self.max_bytes is not None:
            self.prune()
        return True

    def prune(self):
        """Remove least recently used entries until the namespace fits ``max_bytes``."""
        entries, total = [], 0
        try:
            for blob in self.directory.glob("??/*.pkl.gz"):
                info = blob.stat()
                manifest = blob.with_name(blob.name[:-len(".pkl.gz")] + ".json")
                size = info.st_size + (manifest.stat().st_size if manifest.exists() else 0)
                entries.append((info.st_mtime_ns, str(blob), manifest, size))
                total += size
        except OSError as exc:
            self._warn("prune scan failed (%s)", exc)
            return
        for _mtime, blob, manifest, size in sorted(entries):
            if total <= self.max_bytes:
                break
            for path in (manifest, Path(blob)):
                try:
                    path.unlink()
                except OSError:
                    pass
            total -= size
