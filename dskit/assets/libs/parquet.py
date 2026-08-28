"""ParquetStore — the analytics-interop store pack (ADR-0019).

Same contract as :class:`~dskit.assets.store.FileStore`, different
substrate: every record and event is a one-row parquet file. Parquet
files are immutable — no append, no locking — so unlike the sqlite pack
this one lifts NEITHER declared tier-1 limit. What it lifts instead is
the wall between the store and the analytics world: the whole store is
directly scannable by any parquet engine (duckdb, polars, spark)
without going through this API::

    read_parquet('root/records/*/*.parquet')   -- every record
    read_parquet('root/events/*.parquet')      -- the whole log

Layout under one root::

    store.json                          # pin + backend (written LAST)
    records/<kind>/<version_id>.parquet # 1 row: version_id, kind, body
    events/<00000001>.parquet           # 1 row: seq, body

Plain ``<kind>/`` directories, NOT hive ``kind=`` names — ``kind`` is a
real column in every file, and hive-style directory names collide with
an in-file column of the same name in duckdb. A plain glob scan needs
no partition semantics at all.

``body`` is the record/event's canonical JSON — the sqlite body idiom —
and reads re-verify the content hash, so a tampered file is refused
exactly like a tampered row. Writes go through a same-directory temp +
fsync + ``os.replace``, so a crash never leaves a half-written file
where a reader can find it. Every failure crossing the seam — pyarrow's
or the filesystem's — is an ``AssetError``.

**Declared limits.** Single mutating writer per root: the event
sequence is assigned by a check-then-act directory scan, exactly like
the tier-1 append. Queries are directory scans. Concurrent writers need
the sqlite pack; ``copy_store`` migrates either way.

``import pyarrow`` happens inside methods — the lazy-driver discipline
the sqlite pack templates. Import cost: stdlib + this package.
"""

from __future__ import annotations

import json
import os
import re
import tempfile

from ..base import (
    AssetError,
    _check_dict,
    _check_str,
    _raise_if,
    atomic_write_json,
    utc_now,
)
from ..model import AssetModel, model_hash
from ..record import AssetRecord, _VERSION_ID
from ..store import (
    _SEGMENT,
    Store,
    _check_declared_backend,
    _check_kind,
    _check_version_id,
    _read_meta,
    _refuse_existing_store,
)

__all__ = ["ParquetStore"]

#: Event filenames are the zero-padded sequence number, so a lexical
#: sort IS append order. Eight digits caps the log at 10^8 - 1 events —
#: refused loudly if ever reached, unreachable at the declared ~10^4.
#: ASCII digits only (``\d`` would admit Unicode digits) and ``\Z``,
#: not ``$`` (which forgives a trailing newline).
_EVENT_FILE = re.compile(r"^[0-9]{8}\.parquet\Z")
_MAX_SEQ = 10**8 - 1


def _require_pyarrow():
    """Refuse loudly when the driver is absent.

    Called at BOTH create and open, so an instance whose every data
    call would fail can never exist — the missing driver surfaces as
    one clear ``AssetError`` at the seam, never a raw ImportError from
    the middle of a put (round-1 review finding).
    """
    try:
        import pyarrow  # noqa: F401 — availability check only
    except Exception as exc:
        raise AssetError(
            [f"parquet store needs pyarrow (pip install dskit[parquet]): {exc}"]
        ) from exc


def _atomic_write_bytes(path, data):
    """Write ``data`` via a same-directory temp + fsync + ``os.replace``.

    The byte-level twin of :func:`~dskit.assets.base.atomic_write_json`:
    a reader can only ever see a complete file. The file's DATA is
    fsynced before the rename; the rename's directory entry is synced
    best-effort afterwards (the onboarding maildir discipline).

    Parameters
    ----------
    path : str
        Destination file path; its directory must exist.
    data : bytes
        The complete file content.
    """
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
    # After the replace the write is VISIBLE and must be reported as
    # success — a raise here would tell the caller an append failed
    # when it landed, inviting a duplicate on retry (round-2 review
    # finding). So the directory-entry sync is best-effort, exactly
    # like onboarding's _fsync_dir: platforms whose filesystems refuse
    # directory fds lose nothing but rename durability on power loss.
    try:
        dfd = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)
    except OSError:
        pass


class ParquetStore(Store):
    """Tier-2 store: one-row parquet files under the root. See the
    module docstring for layout and what this pack lifts.

    Parameters
    ----------
    root : str
        An initialized store root declaring ``"backend": "parquet"``
        (see :meth:`create`, or ``create_store(..., backend="parquet")``).

    Examples
    --------
    Create a store, write a record, and read it back::

        import tempfile
        from dskit.assets.default_model import default_model
        from dskit.assets.record import AssetRecord
        store = ParquetStore.create(tempfile.mkdtemp() + "/s", default_model())
        vid = store.put_record(
            AssetRecord(kind="entity", payload={"name": "AAPL"}, refs={}))
        store.get_record(vid).payload["name"]
        'AAPL'
        store.list_records("entity") == [vid]
        True
    """

    def __init__(self, root):
        self._root, self._meta = _read_meta(root)
        _check_declared_backend(self, self._meta, self._root)
        _require_pyarrow()
        self._records = os.path.join(self._root, "records")
        self._events = os.path.join(self._root, "events")
        missing = [d for d in ("records", "events")
                   if not os.path.isdir(os.path.join(self._root, d))]
        if missing:
            raise AssetError(
                [f"{self._root!r} declares a parquet store but has no "
                 f"{'/'.join(missing)} directory — the root is damaged "
                 "or incomplete"]
            )

    @classmethod
    def create(cls, root, model) -> "ParquetStore":
        """Initialize a new parquet store root governed by ``model``.

        The directories are built first and ``store.json`` written LAST
        — the meta file is the commit point, exactly like FileStore, so
        a crash mid-create leaves nothing openable. pyarrow is imported
        BEFORE anything touches disk: a root whose puts can never work
        is refused up front, not discovered later.

        Parameters
        ----------
        root : str
            Directory to initialize; created if absent. Refused if it
            already holds any store — creation happens exactly once.
        model : AssetModel
            The governing model; its hash is pinned in ``store.json``.

        Returns
        -------
        ParquetStore
            The opened store.
        """
        errors = []
        _check_str(errors, "root", root)
        if not isinstance(model, AssetModel):
            errors.append(f"model must be an AssetModel, got {type(model).__name__}")
        _raise_if(errors)
        _require_pyarrow()
        root = os.path.abspath(os.path.expanduser(root))
        # Any artifact present means SOME create ran here before — even
        # a crashed one, even another backend's. Refuse; a root is
        # never silently repaired (shared rule, store.py).
        _refuse_existing_store(root)
        try:
            os.makedirs(os.path.join(root, "records"), exist_ok=True)
            os.makedirs(os.path.join(root, "events"), exist_ok=True)
            atomic_write_json(
                os.path.join(root, "store.json"),
                {
                    "model_name": model.name,
                    "model_hash": model_hash(model),
                    "created_at": utc_now(),
                    # A subclass inheriting this create must record
                    # ITSELF, or reopening silently downgrades to
                    # ParquetStore.
                    "backend": "parquet" if cls is ParquetStore
                    else f"{cls.__module__}:{cls.__name__}",
                },
            )
        except OSError as exc:
            raise AssetError(
                [f"cannot initialize store root {root!r}: {exc}"]
            ) from exc
        return cls(root)

    # -- plumbing ---------------------------------------------------------

    def _write_table(self, path, columns):
        """Serialize a one-row table to bytes, then land it atomically.

        Serialization happens fully in memory so the only thing that
        ever reaches the destination directory is a complete file.
        """
        import pyarrow as pa
        import pyarrow.parquet as pq

        try:
            sink = pa.BufferOutputStream()
            pq.write_table(pa.table(columns), sink)
            data = sink.getvalue().to_pybytes()
        except (pa.ArrowException, OSError) as exc:
            raise AssetError([f"parquet store {path!r}: {exc}"]) from exc
        try:
            _atomic_write_bytes(path, data)
        except OSError as exc:
            raise AssetError([f"parquet store {path!r}: {exc}"]) from exc

    def _read_body(self, path):
        """The ``body`` string of a one-row parquet file, shape-checked."""
        import pyarrow as pa
        import pyarrow.parquet as pq

        try:
            table = pq.read_table(path)
        except (pa.ArrowException, OSError) as exc:
            raise AssetError([f"parquet store {path!r}: {exc}"]) from exc
        if "body" not in table.column_names or table.num_rows != 1:
            raise AssetError(
                [f"parquet file {path!r} is not a one-row store file "
                 f"(columns {table.column_names}, {table.num_rows} rows)"]
            )
        body = table.column("body")[0].as_py()
        if not isinstance(body, str):
            raise AssetError([f"parquet file {path!r} body is not a string"])
        return body

    def _load(self, path, expected_vid) -> AssetRecord:
        # _read_body stays OUTSIDE the try: AssetError subclasses
        # ValueError, so wrapping it here would relabel every corrupt-
        # parquet failure as a JSON one (round-1 review finding).
        body = self._read_body(path)
        try:
            obj = json.loads(body)
        except ValueError as exc:
            raise AssetError(
                [f"record file {path!r} body is not valid JSON: {exc}"]
            ) from exc
        # from_obj recomputes the hash — a tampered file is refused.
        record = AssetRecord.from_obj(obj)
        # Storage-key trust (ADR-0020): a VALID record planted under
        # another key must be refused, not returned as the wrong asset.
        if record.version_id() != expected_vid:
            raise AssetError(
                [f"record at storage key {expected_vid!r} holds different "
                 "content — the store was mutated out of band"]
            )
        # And the KIND axis: the directory answers kind-scoped queries
        # and engine scans, so it must agree with the body (round-2).
        stored_kind = os.path.basename(os.path.dirname(path))
        if record.kind != stored_kind:
            raise AssetError(
                [f"record at storage key {expected_vid!r} is stored under "
                 f"kind {stored_kind!r} but declares {record.kind!r} — the "
                 "store was mutated out of band"]
            )
        return record

    def _find(self, version_id):
        """The path holding version_id, or None — kind dirs are scanned
        because a caller with only an id does not know the kind."""
        try:
            kinds = sorted(os.listdir(self._records))
        except OSError as exc:
            raise AssetError(
                [f"parquet store {self._root!r}: {exc}"]
            ) from exc
        # Same '.'/'_' skip as list_records: an entry enumeration
        # cannot see must be equally invisible to a point lookup, or
        # list and has/get disagree about the same root (ADR-0020).
        kinds = [k for k in kinds if not k.startswith((".", "_"))]
        for kind in kinds:
            path = os.path.join(self._records, kind, f"{version_id}.parquet")
            if os.path.isfile(path):
                return path
            if os.path.lexists(path):
                # Key-conforming name that is not a regular file: a
                # point lookup refuses it as loudly as an enumeration
                # would (ADR-0020).
                raise AssetError(
                    [f"records/{kind} holds foreign entry "
                     f"{version_id + '.parquet'!r} — not a record file"]
                )
        return None

    # -- records ----------------------------------------------------------

    def put_record(self, record) -> str:
        if not isinstance(record, AssetRecord):
            raise AssetError(
                [f"record must be an AssetRecord, got {type(record).__name__}"]
            )
        # version_id first, then the kind check — FileStore's error
        # precedence, so a doubly-invalid record fails identically.
        vid = record.version_id()
        _check_kind(record.kind)
        path = os.path.join(self._records, record.kind, f"{vid}.parquet")
        if os.path.isfile(path):
            # Already present: verify it, keep the FIRST registration's
            # provenance, write nothing.
            self._load(path, vid)
            return vid
        try:
            body = json.dumps(record.to_obj(), sort_keys=True, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise AssetError([f"record is not JSON-serializable: {exc}"]) from exc
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
        except OSError as exc:
            raise AssetError([f"parquet store {self._root!r}: {exc}"]) from exc
        self._write_table(
            path, {"version_id": [vid], "kind": [record.kind], "body": [body]}
        )
        return vid

    def get_record(self, version_id) -> AssetRecord:
        _check_version_id(version_id)
        path = self._find(version_id)
        if path is None:
            raise AssetError([f"no record with version_id {version_id!r}"])
        return self._load(path, version_id)

    def has_record(self, version_id) -> bool:
        return isinstance(version_id, str) and bool(_VERSION_ID.match(version_id)) and (
            self._find(version_id) is not None
        )

    def list_records(self, kind=None) -> list:
        if kind is None:
            try:
                entries = sorted(os.listdir(self._records))
            except OSError as exc:
                raise AssetError([f"parquet store {self._root!r}: {exc}"]) from exc
            # The kind LEVEL gets the same engine-discovery rules as
            # the files below it: '.'/'_' names skipped (Finder's
            # .DS_Store, Spark's _SUCCESS land here too — round-4
            # review finding), anything else that is not a kind
            # directory refused loudly.
            kinds = []
            for k in entries:
                if k.startswith((".", "_")):
                    continue
                if not os.path.isdir(os.path.join(self._records, k)):
                    raise AssetError(
                        [f"records/ holds foreign entry {k!r} — an engine "
                         "scan would not agree with the API about the store"]
                    )
                kinds.append(k)
        else:
            if not isinstance(kind, str) or not _SEGMENT.match(kind):
                raise AssetError([f"kind must be a filesystem-safe string, got {kind!r}"])
            kdir = os.path.join(self._records, kind)
            if os.path.isdir(kdir):
                kinds = [kind]
            elif os.path.lexists(kdir):
                # Present but not a directory: the same foreign-entry
                # refusal as the kind=None branch — a silent [] here
                # would disagree with that branch AND with an engine
                # scan of the same root (round-5 review finding).
                raise AssetError(
                    [f"records/ holds foreign entry {kind!r} — an engine "
                     "scan would not agree with the API about the store"]
                )
            else:
                kinds = []
        out = []
        try:
            for k in kinds:
                for f in os.listdir(os.path.join(self._records, k)):
                    # '.'/'_' prefixes mirror engine discovery, exactly
                    # as in _event_files.
                    if f.startswith((".", "_")):
                        continue
                    # Same rule as events/, extension-blind: any name
                    # the API cannot account for is one an engine scan
                    # WOULD read — refuse loudly, never return a
                    # garbage stem that detonates later in get_record
                    # (round-2/4 findings). fullmatch: belt-and-braces
                    # with the pattern's own \Z anchor (ADR-0020). And
                    # it must be a regular FILE — a directory named
                    # like a record passes every name check but
                    # get_record denies it (round-6 finding).
                    stem = f[: -len(".parquet")] if f.endswith(".parquet") else None
                    if (stem is None or not _VERSION_ID.fullmatch(stem)
                            or not os.path.isfile(
                                os.path.join(self._records, k, f))):
                        raise AssetError(
                            [f"records/{k} holds foreign entry {f!r} — an "
                             "engine scan would not agree with the API "
                             "about the store"]
                        )
                    out.append(stem)
        except OSError as exc:
            raise AssetError([f"parquet store {self._root!r}: {exc}"]) from exc
        out.sort()
        # A version_id under two kinds is impossible legitimately (the
        # kind is inside the hash), so a duplicate proves a plant.
        for a, b in zip(out, out[1:]):
            if a == b:
                raise AssetError(
                    [f"version_id {a!r} appears under more than one kind — "
                     "the store was mutated out of band"]
                )
        return out

    # -- events -----------------------------------------------------------

    def model_pin(self) -> dict:
        return dict(self._meta)

    def _event_files(self):
        """Sorted event filenames — lexical order IS append order.

        A ``*.parquet`` file that does NOT match the event pattern is
        refused loudly: an engine scan of ``events/`` would see it, so
        silently skipping it here would let the two sanctioned read
        paths disagree (round-1 review finding). Names with a ``.`` or
        ``_`` prefix are exempt — engine discovery ignores those
        prefixes (pyarrow/Spark/dask), so AppleDouble sidecars and
        crashed-write temps are invisible to BOTH paths and must not
        brick the store (round-3 review finding).
        """
        try:
            names = os.listdir(self._events)
        except OSError as exc:
            raise AssetError([f"parquet store {self._root!r}: {exc}"]) from exc
        names = [n for n in names if not n.startswith((".", "_"))]
        # Extension-blind on purpose: engine discovery reads EVERY
        # non-prefixed name (a .PARQUET or extensionless file too), so
        # suffix-scoping the check would leave silent divergence
        # (round-4 review finding). And a conforming NAME is not
        # enough — a directory named like an event (engines write
        # datasets as directories) would be silently replayed as one
        # (round-6 finding). Anything unaccountable is foreign.
        foreign = sorted(
            n for n in names
            if not _EVENT_FILE.match(n)
            or not os.path.isfile(os.path.join(self._events, n))
        )
        if foreign:
            raise AssetError(
                [f"events/ holds foreign entries {foreign} — an engine "
                 "scan would not agree with the API about the log"]
            )
        return sorted(names)

    def append_event(self, event) -> None:
        errors = []
        _check_dict(errors, "event", event)
        _raise_if(errors)
        try:
            line = json.dumps(event, sort_keys=True, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise AssetError([f"event is not JSON-serializable: {exc}"]) from exc
        # Check-then-act sequence assignment: atomic enough under the
        # declared single-writer limit, and the reason multi-writer
        # needs the sqlite pack.
        files = self._event_files()
        seq = (int(files[-1][:8]) if files else 0) + 1
        if seq > _MAX_SEQ:
            raise AssetError(
                [f"event log is full ({_MAX_SEQ} events) — the declared "
                 "scale is ~10^4; something is very wrong"]
            )
        self._write_table(
            os.path.join(self._events, f"{seq:08d}.parquet"),
            {"seq": [seq], "body": [line]},
        )

    def iter_events(self):
        # The sorted file list captured here IS the snapshot the ABC
        # pins: files appended after iteration begins have higher
        # sequence names and are never in this list.
        for name in self._event_files():
            path = os.path.join(self._events, name)
            # As in _load: _read_body outside the try, or its own
            # AssetErrors would be relabeled as JSON failures.
            body = self._read_body(path)
            try:
                obj = json.loads(body)
            except ValueError as exc:
                raise AssetError(
                    [f"event file {path!r} body is not valid JSON: {exc}"]
                ) from exc
            yield obj
