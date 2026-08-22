"""The store — where records live, behind the durability seam of ADR-0010.

:class:`Store` is the ABC every backend implements; :class:`FileStore` is
the tier-1 implementation: human-diffable JSON files, no dependencies.
sqlite/postgres/parquet arrive later as tier-2 ``libs/`` packs — the ABC
exists precisely because the tier-1 limits are real and declared:

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

Import cost: stdlib + this package.
"""

from __future__ import annotations

import abc
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

__all__ = ["FileStore", "Store"]

#: Kind names become directory names, so they must be filesystem-safe:
#: lowercase, digits, ``_``/``-``, no separators — refused loudly otherwise.
_SEGMENT = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


class Store(abc.ABC):
    """What any asset store must provide — the seam behind the registry.

    Implementations persist three things: the model pin (write-once
    meta), records (write-once by version_id), and events (append-only).
    All validation of CONTENT happens above this seam; a store checks
    only what durability requires.
    """

    @abc.abstractmethod
    def model_pin(self) -> dict:
        """The governing model's identity, fixed at creation.

        Returns
        -------
        dict
            ``{"model_name": ..., "model_hash": ..., "created_at": ...}``.
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
        """Whether a version_id is present (no exception control flow)."""

    @abc.abstractmethod
    def list_records(self, kind=None) -> list:
        """Sorted version_ids, for one kind or the whole store."""

    @abc.abstractmethod
    def append_event(self, event) -> None:
        """Append one event dict to the immutable log."""

    @abc.abstractmethod
    def iter_events(self):
        """Yield every event in append order."""


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
        errors = []
        _check_str(errors, "root", root)
        _raise_if(errors)
        self._root = os.path.abspath(os.path.expanduser(root))
        meta_path = os.path.join(self._root, "store.json")
        try:
            with open(meta_path, encoding="utf-8") as fh:
                meta = json.load(fh)
        except OSError as exc:
            raise AssetError(
                [f"{self._root!r} is not an initialized store (no readable store.json): {exc}"]
            ) from exc
        except ValueError as exc:
            raise AssetError([f"store.json in {self._root!r} is not valid JSON: {exc}"]) from exc
        _check_dict(errors, "store.json", meta)
        _raise_if(errors)
        for key in ("model_name", "model_hash", "created_at"):
            _check_str(errors, f"store.json {key}", meta.get(key, ""))
        _raise_if(errors)
        self._meta = meta

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
        meta_path = os.path.join(root, "store.json")
        if os.path.exists(meta_path):
            raise AssetError(
                [f"{root!r} already holds a store — a root is initialized exactly once"]
            )
        os.makedirs(os.path.join(root, "records"), exist_ok=True)
        atomic_write_json(
            meta_path,
            {
                "model_name": model.name,
                "model_hash": model_hash(model),
                "created_at": utc_now(),
            },
        )
        return cls(root)

    # -- records ----------------------------------------------------------

    def _record_path(self, kind, version_id) -> str:
        if not _SEGMENT.match(kind):
            raise AssetError(
                [f"kind {kind!r} is not filesystem-safe (need lowercase/digits/_/-)"]
            )
        if not isinstance(version_id, str) or not _VERSION_ID.match(version_id):
            raise AssetError(
                [f"version_id must be 64-char sha256 hex, got {version_id!r}"]
            )
        return os.path.join(self._root, "records", kind, f"{version_id}.json")

    def _load(self, path) -> AssetRecord:
        try:
            with open(path, encoding="utf-8") as fh:
                obj = json.load(fh)
        except ValueError as exc:
            raise AssetError([f"record file {path!r} is not valid JSON: {exc}"]) from exc
        # from_obj recomputes the hash — a corrupted/edited file is refused.
        return AssetRecord.from_obj(obj)

    def _find(self, version_id):
        """The path holding version_id, or None — kind dirs are scanned
        because a caller with only an id does not know the kind."""
        records = os.path.join(self._root, "records")
        for kind in sorted(os.listdir(records)):
            path = os.path.join(records, kind, f"{version_id}.json")
            if os.path.isfile(path):
                return path
        return None

    def put_record(self, record) -> str:
        if not isinstance(record, AssetRecord):
            raise AssetError(
                [f"record must be an AssetRecord, got {type(record).__name__}"]
            )
        vid = record.version_id()
        path = self._record_path(record.kind, vid)
        if os.path.isfile(path):
            self._load(path)  # verify, keep original provenance, write nothing
            return vid
        os.makedirs(os.path.dirname(path), exist_ok=True)
        atomic_write_json(path, record.to_obj())
        return vid

    def get_record(self, version_id) -> AssetRecord:
        if not isinstance(version_id, str) or not _VERSION_ID.match(version_id):
            raise AssetError(
                [f"version_id must be 64-char sha256 hex, got {version_id!r}"]
            )
        path = self._find(version_id)
        if path is None:
            raise AssetError([f"no record with version_id {version_id!r}"])
        return self._load(path)

    def has_record(self, version_id) -> bool:
        return isinstance(version_id, str) and bool(_VERSION_ID.match(version_id)) and (
            self._find(version_id) is not None
        )

    def list_records(self, kind=None) -> list:
        records = os.path.join(self._root, "records")
        if kind is None:
            kinds = sorted(os.listdir(records))
        else:
            if not isinstance(kind, str) or not _SEGMENT.match(kind):
                raise AssetError([f"kind must be a filesystem-safe string, got {kind!r}"])
            kinds = [kind] if os.path.isdir(os.path.join(records, kind)) else []
        out = []
        for k in kinds:
            out.extend(
                f[: -len(".json")]
                for f in os.listdir(os.path.join(records, k))
                if f.endswith(".json")
            )
        return sorted(out)

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
        # Plain append: atomic enough under the declared single-writer
        # limit, and the reason multi-writer needs a tier-2 store.
        with open(os.path.join(self._root, "events.jsonl"), "a", encoding="utf-8") as fh:
            fh.write(line + "\n")

    def iter_events(self):
        path = os.path.join(self._root, "events.jsonl")
        if not os.path.isfile(path):
            return
        with open(path, encoding="utf-8") as fh:
            for lineno, line in enumerate(fh, start=1):
                if not line.strip():
                    continue
                try:
                    yield json.loads(line)
                except ValueError as exc:
                    raise AssetError(
                        [f"events.jsonl line {lineno} is not valid JSON: {exc}"]
                    ) from exc
