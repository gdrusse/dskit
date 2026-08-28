"""SqliteStore — the first tier-2 store pack (ADR-0018).

Same contract as :class:`~dskit.assets.store.FileStore`, different
substrate: ``store.json`` still carries the model pin (human-readable,
plus ``"backend": "sqlite"`` for ``open_store`` dispatch); records and
events live in one ``store.sqlite`` database. The pack exists to lift
exactly the two limits the tier-1 store declares:

- **Concurrent writers — at this seam.** Every call opens its own
  connection; the database's locking (WAL journal + busy timeout)
  makes each single call atomic and durable across threads and
  processes. The engine's check-then-act sequences ABOVE the seam
  (Registry's replay-then-append, Lineage's cycle check) still assume
  one mutating writer per root — see the Store ABC docstring.
- **Indexed queries.** get/has hit the primary key and list hits a
  kind index — no directory scans.

Layout under one root::

    store.json     # meta: pin + backend declaration (written last)
    store.sqlite   # records(version_id PK, kind, body) + events(seq, body)

Bodies are the record/event's canonical JSON objects — what a FileStore
holds, compactly serialized — and reads re-verify the content hash, so
a tampered row is refused exactly like a tampered file. Durability:
the database is created ``journal_mode=WAL`` (a persistent property)
and every connection sets ``synchronous=FULL``, so a returned write has
reached disk. Every failure crossing the seam is an ``AssetError`` —
raw ``sqlite3`` exceptions never escape.

``import sqlite3`` happens inside methods: stdlib here, but this pack
is the template for postgres/parquet, whose drivers must stay lazy.

Import cost: stdlib + this package.
"""

from __future__ import annotations

import json
import os

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

__all__ = ["SqliteStore"]

#: Seconds a writer waits on a locked database before failing loudly —
#: generous, because a blocked put is better than a spurious error.
_BUSY_TIMEOUT = 30.0

#: The whole schema. body is canonical JSON; nothing else is ever
#: derived from it, so the tables stay dumb on purpose.
_SCHEMA = (
    "CREATE TABLE records ("
    " version_id TEXT PRIMARY KEY,"
    " kind TEXT NOT NULL,"
    " body TEXT NOT NULL)",
    "CREATE INDEX records_kind ON records (kind)",
    "CREATE TABLE events ("
    " seq INTEGER PRIMARY KEY AUTOINCREMENT,"
    " body TEXT NOT NULL)",
)


class SqliteStore(Store):
    """Tier-2 store: one sqlite database under the root. See the module
    docstring for layout and what this pack lifts.

    Parameters
    ----------
    root : str
        An initialized store root declaring ``"backend": "sqlite"``
        (see :meth:`create`, or ``create_store(..., backend="sqlite")``).

    Examples
    --------
    Create a store, write a record, and read it back::

        import tempfile
        from dskit.assets.default_model import default_model
        from dskit.assets.record import AssetRecord
        store = SqliteStore.create(tempfile.mkdtemp() + "/s", default_model())
        vid = store.put_record(
            AssetRecord(kind="entity", payload={"name": "AAPL"}, refs={}))
        store.get_record(vid).payload["name"]
        # -> 'AAPL'
        store.list_records("entity") == [vid]
        # -> True
    """

    def __init__(self, root):
        self._root, self._meta = _read_meta(root)
        _check_declared_backend(self, self._meta, self._root)
        self._db = os.path.join(self._root, "store.sqlite")
        if not os.path.isfile(self._db):
            raise AssetError(
                [f"{self._root!r} declares a sqlite store but has no "
                 "store.sqlite — the root is damaged or incomplete"]
            )

    @classmethod
    def create(cls, root, model) -> "SqliteStore":
        """Initialize a new sqlite store root governed by ``model``.

        The database is built first and ``store.json`` written LAST —
        the meta file is the commit point, exactly like FileStore, so a
        crash mid-create leaves nothing openable.

        Parameters
        ----------
        root : str
            Directory to initialize; created if absent. Refused if it
            already holds any store — creation happens exactly once.
        model : AssetModel
            The governing model; its hash is pinned in ``store.json``.

        Returns
        -------
        SqliteStore
            The opened store.
        """
        errors = []
        _check_str(errors, "root", root)
        if not isinstance(model, AssetModel):
            errors.append(f"model must be an AssetModel, got {type(model).__name__}")
        _raise_if(errors)
        root = os.path.abspath(os.path.expanduser(root))
        db_path = os.path.join(root, "store.sqlite")
        # Any artifact present means SOME create ran here before — even
        # a crashed one, even another backend's. Refuse; a root is
        # never silently repaired (shared rule, store.py).
        _refuse_existing_store(root)
        # Disk work wrapped like everything else (round-3 finding): an
        # unwritable parent or a file where a directory belongs crosses
        # the seam as AssetError, not a raw OSError.
        try:
            os.makedirs(root, exist_ok=True)
        except OSError as exc:
            raise AssetError(
                [f"cannot initialize store root {root!r}: {exc}"]
            ) from exc
        import sqlite3

        conn = None
        try:
            # The connect itself is inside the wrap: an unwritable root
            # fails here, and that failure crosses the seam as an
            # AssetError like every other (round-2 review finding).
            conn = sqlite3.connect(db_path)
            # WAL is a property of the database file, set once here;
            # synchronous is per-connection — this connection included.
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=FULL")
            for statement in _SCHEMA:
                conn.execute(statement)
            conn.commit()
        except sqlite3.Error as exc:
            raise AssetError([f"sqlite store {db_path!r}: {exc}"]) from exc
        finally:
            if conn is not None:
                conn.close()
        try:
            atomic_write_json(
                os.path.join(root, "store.json"),
                {
                    "model_name": model.name,
                    "model_hash": model_hash(model),
                    "created_at": utc_now(),
                    # A subclass inheriting this create must record
                    # ITSELF, or reopening silently downgrades to
                    # SqliteStore.
                    "backend": "sqlite" if cls is SqliteStore
                    else f"{cls.__module__}:{cls.__name__}",
                },
            )
        except OSError as exc:
            raise AssetError(
                [f"cannot initialize store root {root!r}: {exc}"]
            ) from exc
        return cls(root)

    # -- plumbing ---------------------------------------------------------

    def _connect(self):
        """One connection per call: nothing held open, so any number of
        threads/processes can hold their own — the point of the pack.

        URI ``mode=rw`` (ADR-0020): a plain connect CREATES a missing
        database, so a call against a damaged root would leave a stray
        empty ``store.sqlite`` behind its own failure. ``mode=rw``
        opens what exists or fails loudly, touching nothing.
        """
        import sqlite3
        from urllib.parse import quote

        try:
            conn = sqlite3.connect(
                f"file:{quote(self._db)}?mode=rw",
                timeout=_BUSY_TIMEOUT, uri=True,
            )
        except sqlite3.Error as exc:
            raise AssetError([f"sqlite store {self._db!r}: {exc}"]) from exc
        try:
            conn.execute("PRAGMA synchronous=FULL")
        except sqlite3.Error as exc:
            conn.close()
            raise AssetError([f"sqlite store {self._db!r}: {exc}"]) from exc
        return conn

    def _load_body(self, body, expected_vid, stored_kind) -> AssetRecord:
        try:
            obj = json.loads(body)
        except ValueError as exc:
            raise AssetError([f"record row is not valid JSON: {exc}"]) from exc
        # from_obj recomputes the hash — a tampered row is refused.
        record = AssetRecord.from_obj(obj)
        # Storage-key trust (ADR-0020): a VALID record planted under
        # another key must be refused, not returned as the wrong asset.
        if record.version_id() != expected_vid:
            raise AssetError(
                [f"record at storage key {expected_vid!r} holds different "
                 "content — the store was mutated out of band"]
            )
        # And the KIND axis: the indexed kind column answers kind-scoped
        # queries, so it must agree with the body (round-2 finding).
        if record.kind != stored_kind:
            raise AssetError(
                [f"record at storage key {expected_vid!r} is stored under "
                 f"kind {stored_kind!r} but declares {record.kind!r} — the "
                 "store was mutated out of band"]
            )
        return record

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
        body = json.dumps(record.to_obj(), sort_keys=True, allow_nan=False)
        import sqlite3

        conn = self._connect()
        try:
            with conn:
                cur = conn.execute(
                    "INSERT OR IGNORE INTO records (version_id, kind, body)"
                    " VALUES (?, ?, ?)",
                    (vid, record.kind, body),
                )
            if cur.rowcount == 0:
                # Already present (possibly a concurrent writer's row):
                # verify it, keep the FIRST registration's provenance.
                row = conn.execute(
                    "SELECT kind, body FROM records WHERE version_id = ?", (vid,)
                ).fetchone()
                if row is None:
                    raise AssetError(
                        [f"record {vid!r} vanished during verify — the "
                         "store was mutated out of band"]
                    )
                self._load_body(row[1], vid, row[0])
        except sqlite3.Error as exc:
            raise AssetError([f"sqlite store {self._db!r}: {exc}"]) from exc
        finally:
            conn.close()
        return vid

    def get_record(self, version_id) -> AssetRecord:
        _check_version_id(version_id)
        import sqlite3

        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT kind, body FROM records WHERE version_id = ?", (version_id,)
            ).fetchone()
        except sqlite3.Error as exc:
            raise AssetError([f"sqlite store {self._db!r}: {exc}"]) from exc
        finally:
            conn.close()
        if row is None:
            raise AssetError([f"no record with version_id {version_id!r}"])
        return self._load_body(row[1], version_id, row[0])

    def has_record(self, version_id) -> bool:
        if not isinstance(version_id, str) or not _VERSION_ID.match(version_id):
            return False
        import sqlite3

        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT 1 FROM records WHERE version_id = ?", (version_id,)
            ).fetchone()
        except sqlite3.Error as exc:
            raise AssetError([f"sqlite store {self._db!r}: {exc}"]) from exc
        finally:
            conn.close()
        return row is not None

    def list_records(self, kind=None) -> list:
        import sqlite3

        conn = self._connect()
        try:
            if kind is None:
                rows = conn.execute(
                    "SELECT version_id FROM records ORDER BY version_id"
                ).fetchall()
            else:
                if not isinstance(kind, str) or not _SEGMENT.match(kind):
                    raise AssetError(
                        [f"kind must be a filesystem-safe string, got {kind!r}"]
                    )
                rows = conn.execute(
                    "SELECT version_id FROM records WHERE kind = ?"
                    " ORDER BY version_id",
                    (kind,),
                ).fetchall()
        except sqlite3.Error as exc:
            raise AssetError([f"sqlite store {self._db!r}: {exc}"]) from exc
        finally:
            conn.close()
        return [row[0] for row in rows]

    # -- events -----------------------------------------------------------

    def model_pin(self) -> dict:
        return dict(self._meta)

    def append_event(self, event) -> None:
        errors = []
        _check_dict(errors, "event", event)
        _raise_if(errors)
        try:
            line = json.dumps(event, sort_keys=True, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise AssetError([f"event is not JSON-serializable: {exc}"]) from exc
        import sqlite3

        conn = self._connect()
        try:
            with conn:
                conn.execute("INSERT INTO events (body) VALUES (?)", (line,))
        except sqlite3.Error as exc:
            raise AssetError([f"sqlite store {self._db!r}: {exc}"]) from exc
        finally:
            conn.close()

    def iter_events(self):
        # fetchall before yielding — this IS the snapshot the ABC pins,
        # and the connection closes immediately, so a slow consumer
        # never holds a read lock. Whole-log-in-memory is fine at the
        # ~10^4-events scale the platform declares.
        import sqlite3

        conn = self._connect()
        try:
            rows = conn.execute("SELECT body FROM events ORDER BY seq").fetchall()
        except sqlite3.Error as exc:
            raise AssetError([f"sqlite store {self._db!r}: {exc}"]) from exc
        finally:
            conn.close()
        for rownum, (body,) in enumerate(rows, start=1):
            try:
                yield json.loads(body)
            except ValueError as exc:
                raise AssetError(
                    [f"events row {rownum} is not valid JSON: {exc}"]
                ) from exc
