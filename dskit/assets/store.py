"""The store — where records live, behind the durability seam of ADR-0010.

:class:`Store` is the ABC every backend implements; :class:`FileStore` is
the tier-1 implementation: human-diffable JSON files, no dependencies.
Tier-2 packs live in ``libs/`` (sqlite, parquet; postgres when needed)
— the ABC exists precisely because the tier-1 limits are real
and declared:

- **Single writer per store root.** Nothing here locks; concurrent
  writers can interleave events.
- **Queries are directory scans.** Fine to ~10^4 assets, no further.

Layout under one root::

    store.json                       # meta: the governing model, pinned
    records/<kind>/<version_id>.json # one record, write-once, atomic
    events.jsonl                     # append-only: register/transition

The model pin is the governance anchor (ADR-0007): ``store.json`` fixes
the model's hash at creation, so what the store PERMITS is auditable and
any drift is a new, visible identity. Records are write-once — a put of
an already-present version_id verifies the existing file and changes
nothing, preserving first-registration provenance (reuse before
duplication). State lives only in the event log; records never mutate.

**Backend selection (ADR-0018).** ``store.json`` also declares WHICH
backend holds the root's data: an optional ``"backend"`` key, absent
meaning ``"file"`` — every pre-ADR root opens unchanged. Callers open
roots through :func:`open_store`, which reads the declaration and
dispatches; :func:`create_store` dispatches creation the same way. A
backend is a built-in name or a ``pkg.module:Class`` reference (the
connector idiom, ADR-0013), so a tier-3 store needs no entry here. The
class contract behind the dispatch: ``cls(root)`` opens, and
``cls.create(root, model)`` initializes exactly once AND writes its own
backend name into ``store.json`` so :func:`open_store` can round-trip.

Import cost: stdlib + this package.
"""

from __future__ import annotations

import abc
import importlib
import json
import os
import re

from .base import (
    AssetError,
    _check_dict,
    _check_str,
    _raise_if,
    atomic_write_json,
    utc_now,
)
from .model import AssetModel, model_hash
from .record import AssetRecord, _VERSION_ID

__all__ = ["FileStore", "Store", "copy_store", "create_store", "open_store"]

#: Kind names become directory names, so they must be filesystem-safe:
#: lowercase, digits, ``_``/``-``, no separators — refused loudly otherwise.
#: Every backend enforces the same rule, so a store copies anywhere.
#: \Z, not $ — $ forgives a trailing newline (ADR-0020).
_SEGMENT = re.compile(r"^[a-z0-9][a-z0-9_-]*\Z")

#: A ``pkg.module:ClassName`` backend reference — the connector idiom.
_CLASS_REF = re.compile(r"^[A-Za-z_][\w.]*:[A-Za-z_]\w*$")

#: Built-in backends, by the name ``store.json`` declares. Packs import
#: lazily at resolve time, so an unused backend costs nothing.
_BACKENDS = {
    "file": "dskit.assets.store:FileStore",
    "sqlite": "dskit.assets.libs.sqlite:SqliteStore",
    "parquet": "dskit.assets.libs.parquet:ParquetStore",
}


def _read_meta(root):
    """Load and shape-check a root's ``store.json``.

    The one reader every backend and :func:`open_store` share, so an
    uninitialized or malformed root fails identically everywhere.

    Parameters
    ----------
    root : str
        A store root directory.

    Returns
    -------
    tuple
        ``(absolute_root, meta_dict)``.
    """
    errors = []
    _check_str(errors, "root", root)
    _raise_if(errors)
    root = os.path.abspath(os.path.expanduser(root))
    meta_path = os.path.join(root, "store.json")
    try:
        with open(meta_path, encoding="utf-8") as fh:
            meta = json.load(fh)
    except OSError as exc:
        raise AssetError(
            [f"{root!r} is not an initialized store (no readable store.json): {exc}"]
        ) from exc
    except ValueError as exc:
        raise AssetError([f"store.json in {root!r} is not valid JSON: {exc}"]) from exc
    _check_dict(errors, "store.json", meta)
    _raise_if(errors)
    for key in ("model_name", "model_hash", "created_at"):
        _check_str(errors, f"store.json {key}", meta.get(key, ""))
    # Optional by design: absent means "file", so pre-ADR-0018 roots
    # keep opening without a rewrite.
    _check_str(errors, "store.json backend", meta.get("backend", "file"))
    _raise_if(errors)
    return root, meta


#: Filenames/dirs any built-in backend's create may leave behind. Create
#: refuses a root containing ANY of them: a crashed create — whichever
#: backend started it — is deleted and redone, never built over (a file
#: store silently re-pinned over a stray database would be worse than
#: the crash).
_STORE_ARTIFACTS = ("store.json", "store.sqlite", "store.sqlite-wal",
                    "store.sqlite-shm", "records", "events.jsonl", "events")


def _refuse_existing_store(root):
    """Refuse creation wherever any store artifact already exists."""
    for name in _STORE_ARTIFACTS:
        if os.path.exists(os.path.join(root, name)):
            raise AssetError(
                [f"{root!r} already holds a store (found {name!r}) — a root is "
                 "initialized exactly once and never repaired in place"]
            )


def _check_declared_backend(store, meta, root):
    """Refuse opening a root whose declared backend is not this class.

    The wrong-class open is a silent empty view, so every backend's
    ``__init__`` calls this. The check resolves the declaration and
    accepts any class the instance satisfies — so ``pkg.module:Class``
    references to a built-in, and tier-3 subclasses that declare
    themselves, both round-trip (ADR-0018).
    """
    declared = _resolve_backend(meta.get("backend", "file"))
    if not isinstance(store, declared):
        raise AssetError(
            [f"{root!r} declares store backend "
             f"{meta.get('backend', 'file')!r} — open it with open_store()"]
        )


def _check_kind(kind):
    """Refuse a kind name no backend may accept (see ``_SEGMENT``)."""
    if not isinstance(kind, str) or not _SEGMENT.match(kind):
        raise AssetError(
            [f"kind {kind!r} is not filesystem-safe (need lowercase/digits/_/-)"]
        )


def _check_version_id(version_id):
    """Refuse anything that is not a canonical version_id."""
    if not isinstance(version_id, str) or not _VERSION_ID.match(version_id):
        raise AssetError(
            [f"version_id must be 64-char sha256 hex, got {version_id!r}"]
        )


def _resolve_backend(ref):
    """Turn a backend reference into a Store subclass.

    A built-in name is looked up in :data:`_BACKENDS`; a
    ``pkg.module:ClassName`` reference is imported directly — the
    connector ``resolve`` idiom (ADR-0013), so import = registration
    and a project's own store needs no entry here.

    Parameters
    ----------
    ref : str
        A built-in backend name (``"file"``, ``"sqlite"``, ``"parquet"``)
        or an import
        reference (``"my_pkg.stores:PostgresStore"``).

    Returns
    -------
    type
        The Store subclass (not an instance).

    Raises
    ------
    AssetError
        If the reference is unknown, unimportable, or resolves to
        something that is not a Store subclass.
    """
    errors = []
    _check_str(errors, "store backend", ref)
    _raise_if(errors)
    if not _CLASS_REF.match(ref):
        target = _BACKENDS.get(ref)
        if target is None:
            raise AssetError(
                [f"unknown store backend {ref!r} — built in: "
                 f"{sorted(_BACKENDS)}; or use pkg.module:Class"]
            )
        ref = target
    module_name, attr = ref.split(":", 1)
    try:
        module = importlib.import_module(module_name)
    # Everything, not just ImportError: a backend module crashing at
    # import (native drivers do) must not escape the seam raw.
    except Exception as exc:
        raise AssetError([f"cannot import store backend {ref!r}: {exc}"]) from exc
    cls = getattr(module, attr, None)
    if cls is None:
        raise AssetError([f"store backend {ref!r}: module has no attribute {attr!r}"])
    if not (isinstance(cls, type) and issubclass(cls, Store)) or cls is Store:
        raise AssetError([f"store backend {ref!r} is not a Store subclass"])
    return cls


def open_store(root):
    """Open an initialized store root with whatever backend it declares.

    The one opener every caller should use (ADR-0018): reads the
    root's ``store.json``, resolves its ``backend`` declaration (absent
    = ``"file"``), and returns that backend opened on the root.

    Parameters
    ----------
    root : str
        A root previously initialized by :func:`create_store` (or a
        backend's own ``create``).

    Returns
    -------
    Store
        The opened store.
    """
    _, meta = _read_meta(root)
    return _resolve_backend(meta.get("backend", "file"))(root)


def create_store(root, model, backend="file"):
    """Initialize a new store root with the chosen backend (exactly once).

    Resolution happens BEFORE anything touches disk, so an unknown
    backend leaves no half-created root behind.

    Parameters
    ----------
    root : str
        Directory to initialize; refused if it already holds a store.
    model : AssetModel
        The governing model; its hash is pinned in ``store.json``.
    backend : str, optional
        Built-in name (``"file"``, ``"sqlite"``, ``"parquet"``) or
        ``pkg.module:Class``.

    Returns
    -------
    Store
        The opened store.
    """
    return _resolve_backend(backend).create(root, model)


def copy_store(src, dst):
    """Replay one store's whole content into another, backend-agnostic.

    The migration path between backends (ADR-0018): records first, then
    events, through the public Store surface only — so any pair works,
    and every copied record re-verifies its content hash on the way in.

    Parameters
    ----------
    src : Store
        The store to copy from (untouched).
    dst : Store
        A freshly created, EMPTY store pinned to the same model.

    Returns
    -------
    dict
        ``{"records": n, "events": m}`` — what was copied.

    Raises
    ------
    AssetError
        If the pins disagree (a store copy must not change what the
        content is governed by) or the destination is not empty.

    Notes
    -----
    A copy that fails midway leaves the destination partially filled,
    and a retry is refused by the empty-destination check — delete the
    destination root and re-create it before retrying. The source is
    never touched.
    """
    errors = []
    for name, obj in (("src", src), ("dst", dst)):
        if not isinstance(obj, Store):
            errors.append(f"{name} must be a Store, got {type(obj).__name__}")
    _raise_if(errors)
    src_pin, dst_pin = src.model_pin(), dst.model_pin()
    for key in ("model_name", "model_hash"):
        if src_pin.get(key) != dst_pin.get(key):
            errors.append(
                f"model pin mismatch on {key}: "
                f"src {src_pin.get(key)!r} != dst {dst_pin.get(key)!r}"
            )
    _raise_if(errors)
    if dst.list_records() or next(iter(dst.iter_events()), None) is not None:
        raise AssetError(
            ["destination store is not empty — copy_store fills a fresh root only"]
        )
    vids = src.list_records()
    for vid in vids:
        dst.put_record(src.get_record(vid))
    n_events = 0
    for event in src.iter_events():
        dst.append_event(event)
        n_events += 1
    return {"records": len(vids), "events": n_events}


class Store(abc.ABC):
    """What any asset store must provide — the seam behind the registry.

    Implementations persist three things: the model pin (write-once
    meta), records (write-once by version_id), and events (append-only).
    All validation of CONTENT happens above this seam; a store checks
    only what durability requires.

    **Concurrency guarantees stop at this seam.** A backend that admits
    concurrent writers (the sqlite pack) makes each single call atomic
    and durable — but the engine's check-then-act sequences above the
    seam (Registry's replay-then-append, Lineage's cycle check) still
    assume ONE mutating writer per root. Concurrent engine-level
    mutation needs coordination above the store; concurrent READERS are
    always fine.
    """

    @abc.abstractmethod
    def model_pin(self) -> dict:
        """The governing model's identity, fixed at creation.

        Returns
        -------
        dict
            ``{"model_name": ..., "model_hash": ..., "created_at": ...,
            "backend": ...}`` — ``backend`` absent only on roots created
            before ADR-0018 (meaning ``"file"``).
        """

    @abc.abstractmethod
    def put_record(self, record) -> str:
        """Persist a record write-once; return its version_id.

        Idempotent: an already-present version_id is verified (tamper
        check) and left untouched — the FIRST registration's provenance
        survives.
        """

    @abc.abstractmethod
    def get_record(self, version_id) -> AssetRecord:
        """Load one record by version_id; raise AssetError if absent."""

    @abc.abstractmethod
    def has_record(self, version_id) -> bool:
        """Whether a version_id is present. Absence is a bool, never an
        exception; store DAMAGE (a squatted key, an unreadable root)
        raises AssetError like every other call (ADR-0020)."""

    @abc.abstractmethod
    def list_records(self, kind=None) -> list:
        """Sorted version_ids, for one kind or the whole store."""

    @abc.abstractmethod
    def append_event(self, event) -> None:
        """Append one event dict to the immutable log."""

    @abc.abstractmethod
    def iter_events(self):
        """Yield every event in append order, as a SNAPSHOT.

        The snapshot is taken when iteration begins: events appended
        after that are not seen. Pinned across backends by the shared
        battery, so replay-derived state never depends on the backend.
        """


class FileStore(Store):
    """Tier-1 store: JSON files under one root. See the module docstring
    for layout and declared limits.

    Parameters
    ----------
    root : str
        An initialized store root (see :meth:`create`).

    Examples
    --------
    >>> import tempfile
    >>> from dskit.assets.default_model import default_model
    >>> from dskit.assets.record import AssetRecord
    >>> store = FileStore.create(tempfile.mkdtemp(), default_model())
    >>> vid = store.put_record(
    ...     AssetRecord(kind="entity", payload={"name": "AAPL"}, refs={}))
    >>> store.put_record(
    ...     AssetRecord(kind="entity", payload={"name": "AAPL"}, refs={})) == vid
    True
    >>> store.get_record(vid).payload["name"]
    'AAPL'
    >>> store.list_records("entity") == [vid]
    True
    """

    def __init__(self, root):
        self._root, self._meta = _read_meta(root)
        _check_declared_backend(self, self._meta, self._root)

    @classmethod
    def create(cls, root, model) -> "FileStore":
        """Initialize a new store root governed by ``model``.

        Parameters
        ----------
        root : str
            Directory to initialize; created if absent. Refused if it
            already holds a store — creation happens exactly once.
        model : AssetModel
            The governing model; its hash is pinned in ``store.json``.

        Returns
        -------
        FileStore
            The opened store.
        """
        errors = []
        _check_str(errors, "root", root)
        if not isinstance(model, AssetModel):
            errors.append(f"model must be an AssetModel, got {type(model).__name__}")
        _raise_if(errors)
        root = os.path.abspath(os.path.expanduser(root))
        _refuse_existing_store(root)
        # All disk work wrapped: an unwritable parent or a file where a
        # directory belongs crosses the seam as AssetError, like every
        # other failure (round-3 review finding).
        try:
            os.makedirs(os.path.join(root, "records"), exist_ok=True)
            atomic_write_json(
                os.path.join(root, "store.json"),
                {
                    "model_name": model.name,
                    "model_hash": model_hash(model),
                    "created_at": utc_now(),
                    # Explicit even though absent means the same:
                    # create's contract is that the backend names
                    # itself (ADR-0018). A subclass inheriting this
                    # create records ITSELF, or reopening would
                    # silently downgrade to FileStore.
                    "backend": "file" if cls is FileStore
                    else f"{cls.__module__}:{cls.__name__}",
                },
            )
        except OSError as exc:
            raise AssetError(
                [f"cannot initialize store root {root!r}: {exc}"]
            ) from exc
        return cls(root)

    # -- records ----------------------------------------------------------

    def _record_path(self, kind, version_id) -> str:
        _check_kind(kind)
        _check_version_id(version_id)
        return os.path.join(self._root, "records", kind, f"{version_id}.json")

    def _load(self, path, expected_vid) -> AssetRecord:
        # open() inside the wrap: a damaged/read-only root crosses the
        # seam as AssetError, the packs' standard (ADR-0020).
        try:
            with open(path, encoding="utf-8") as fh:
                obj = json.load(fh)
        except OSError as exc:
            raise AssetError([f"cannot read record file {path!r}: {exc}"]) from exc
        except ValueError as exc:
            raise AssetError([f"record file {path!r} is not valid JSON: {exc}"]) from exc
        # from_obj recomputes the hash — a corrupted/edited file is refused.
        record = AssetRecord.from_obj(obj)
        # Storage-key trust (ADR-0020): a VALID record planted under
        # another key must be refused, not returned as the wrong asset.
        if record.version_id() != expected_vid:
            raise AssetError(
                [f"record at storage key {expected_vid!r} holds different "
                 "content — the store was mutated out of band"]
            )
        # And the KIND axis of the same trust: a valid record planted
        # under its own vid but the wrong kind directory would answer
        # kind-scoped queries wrongly (round-2 review finding).
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
        records = os.path.join(self._root, "records")
        try:
            kinds = sorted(os.listdir(records))
        except OSError as exc:
            raise AssetError([f"cannot scan store root {self._root!r}: {exc}"]) from exc
        # Same '.'/'_' skip as list_records: an entry enumeration
        # cannot see must be equally invisible to a point lookup, or
        # list and has/get disagree about the same root (ADR-0020).
        kinds = [k for k in kinds if not k.startswith((".", "_"))]
        for kind in kinds:
            path = os.path.join(records, kind, f"{version_id}.json")
            if os.path.isfile(path):
                return path
            if os.path.lexists(path):
                # Key-conforming name that is not a regular file: a
                # point lookup must refuse it as loudly as an
                # enumeration would (ADR-0020).
                raise AssetError(
                    [f"records/{kind} holds foreign entry "
                     f"{version_id + '.json'!r} — not a record file"]
                )
        return None

    def put_record(self, record) -> str:
        if not isinstance(record, AssetRecord):
            raise AssetError(
                [f"record must be an AssetRecord, got {type(record).__name__}"]
            )
        vid = record.version_id()
        path = self._record_path(record.kind, vid)
        if os.path.isfile(path):
            self._load(path, vid)  # verify, keep original provenance, write nothing
            return vid
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            atomic_write_json(path, record.to_obj())
        except OSError as exc:
            raise AssetError([f"cannot write to store root {self._root!r}: {exc}"]) from exc
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
        records = os.path.join(self._root, "records")
        if kind is None:
            try:
                entries = sorted(os.listdir(records))
            except OSError as exc:
                raise AssetError(
                    [f"cannot scan store root {self._root!r}: {exc}"]
                ) from exc
            # The foreign-entry doctrine (ADR-0019/0020): '.'/'_'
            # prefixed names invisible (Finder/Spark droppings),
            # anything else that is not a kind directory refused
            # loudly — never a garbage result that detonates later.
            kinds = []
            for k in entries:
                if k.startswith((".", "_")):
                    continue
                if not os.path.isdir(os.path.join(records, k)):
                    raise AssetError(
                        [f"records/ holds foreign entry {k!r} — "
                         "a store root holds only kind directories"]
                    )
                kinds.append(k)
        else:
            if not isinstance(kind, str) or not _SEGMENT.match(kind):
                raise AssetError([f"kind must be a filesystem-safe string, got {kind!r}"])
            kdir = os.path.join(records, kind)
            if os.path.isdir(kdir):
                kinds = [kind]
            elif os.path.lexists(kdir):
                raise AssetError(
                    [f"records/ holds foreign entry {kind!r} — "
                     "a store root holds only kind directories"]
                )
            else:
                kinds = []
        out = []
        try:
            for k in kinds:
                for f in os.listdir(os.path.join(records, k)):
                    if f.startswith((".", "_")):
                        continue
                    stem = f[: -len(".json")] if f.endswith(".json") else None
                    if (stem is None or not _VERSION_ID.match(stem)
                            or not os.path.isfile(os.path.join(records, k, f))):
                        raise AssetError(
                            [f"records/{k} holds foreign entry {f!r} — "
                             "a kind directory holds only record files"]
                        )
                    out.append(stem)
        except OSError as exc:
            raise AssetError([f"cannot scan store root {self._root!r}: {exc}"]) from exc
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

    def append_event(self, event) -> None:
        errors = []
        _check_dict(errors, "event", event)
        _raise_if(errors)
        try:
            line = json.dumps(event, sort_keys=True, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise AssetError([f"event is not JSON-serializable: {exc}"]) from exc
        path = os.path.join(self._root, "events.jsonl")
        if os.path.lexists(path) and not os.path.isfile(path):
            # Mirror of the iter_events squat guard: "a" through a
            # dangling symlink would land the write wherever the
            # out-of-band link points and silently HEAL the refusal
            # reads gave the same root (ADR-0020 round-3 residual).
            raise AssetError(
                [f"events.jsonl in {self._root!r} is not a regular file — "
                 "the store was mutated out of band"]
            )
        # Plain append: atomic enough under the declared single-writer
        # limit, and the reason multi-writer needs a tier-2 store.
        try:
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except OSError as exc:
            raise AssetError(
                [f"cannot append to event log in {self._root!r}: {exc}"]
            ) from exc

    def iter_events(self):
        path = os.path.join(self._root, "events.jsonl")
        if not os.path.lexists(path):
            return
        if not os.path.isfile(path):
            # Present but not a regular file: reading it as "no events
            # yet" would silently reset replay-derived state while
            # append fails loudly on the same root (round-2 finding).
            raise AssetError(
                [f"events.jsonl in {self._root!r} is not a regular file — "
                 "the store was mutated out of band"]
            )
        # Snapshot before yielding (the pinned cross-backend contract):
        # appends made after iteration begins are never seen, so every
        # backend replays identically.
        try:
            with open(path, encoding="utf-8") as fh:
                lines = fh.readlines()
        except OSError as exc:
            raise AssetError(
                [f"cannot read event log in {self._root!r}: {exc}"]
            ) from exc
        for lineno, line in enumerate(lines, start=1):
            if not line.strip():
                continue
            try:
                yield json.loads(line)
            except ValueError as exc:
                raise AssetError(
                    [f"events.jsonl line {lineno} is not valid JSON: {exc}"]
                ) from exc
