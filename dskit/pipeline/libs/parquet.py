"""The ``ParquetRows`` data node: one onboarded parquet file as records (ADR-0228).

A price or reference table that arrived as a parquet file in a ``localblobs``
stream is read through the store, never a path: the stream reference is
resolved by :func:`dskit.onboarding.payload_files`, and the file's bytes are
hashed against the manifest digest before pyarrow parses them, so a file that
drifted since acquisition refuses the run instead of feeding it. The class is
a tier-2 wrapper of pyarrow: the library (and the onboarding package, whose
import the purity gate keeps out of the core) is imported only inside
``run()`` and the fingerprint.

Which file is a per-key lookup (``relpath_by_key``), so a ``foreach`` chain
passes its key and the document never types a file name twice.

``ParquetFrameCache`` (ADR-0236 amendment) is the pack's other half: one built
DataFrame kept as parquet under a caller-named identity, so a multi-stage study
builds an expensive panel once and every later stage reuses it.
"""

from __future__ import annotations

import datetime
import hashlib

from dskit.pipeline.document import is_node_ref
from dskit.pipeline.node import DEFAULT_NODE_KINDS, Node, reject_unknown_params

__all__ = ["NODE_KINDS", "ParquetFrameCache", "ParquetRows", "register"]


def _sha256(path):
    """Return a file's sha256 hex digest by the pipeline's one file-hash rule (``path_hash``)."""
    from dskit.pipeline.workflow import path_hash
    return path_hash(str(path))


class ParquetRows(Node):
    """Emit the declared columns of one onboarded parquet file as records.

    Role ``data`` -- reads the file named by ``relpath_by_key[key]`` inside
    ``stream`` of ``source``, verified against the manifest sha256.

    Parameters
    ----------
    params : dict
        ``root`` (str, onboarding root), ``source`` (str), ``stream`` (str),
        ``relpath_by_key`` (dict, key -> relpath within the stream), ``key``
        (str, normally ``$each``), ``columns`` (non-empty dict, file column ->
        output field). All required. Optional ``window`` (dict or None):
        ``field`` (an output field of ``columns``, the date), ``start`` and
        ``end`` (ISO ``YYYY-MM-DD``, inclusive, at least one) keep only the
        rows whose date lies inside; absent or None keeps every row. Bounds must
        be exactly ``YYYY-MM-DD``; the field's values (date, datetime or ISO
        text) are parsed, never sliced, and a value that is none refuses the run.
        The window cuts the stream and nothing more: an ``end`` that must leave
        room for a horizon is the caller's to set (``horizon-pairs`` drops the
        entry dates whose target lies past the last close).

    Examples
    --------
    Read a ticker's dated closes under chosen field names::

        node = ParquetRows("prices", {
            "root": "./ob", "source": "archive", "stream": "files",
            "relpath_by_key": {"AAA": "aaa/prices.parquet"}, "key": "AAA",
            "columns": {"date": "d", "close": "c"},
        })
        out = node.run(ctx, {})
        # -> out["records"] is [{"d": "...", "c": 1.0}, ...]
    """

    role = "data"
    outputs = ("records",)

    _REQUIRED = ("root", "source", "stream", "relpath_by_key", "key", "columns")
    _PARAMS = _REQUIRED + ("window",)
    _WINDOW_KEYS = ("field", "start", "end")

    @classmethod
    def validate_params(cls, params):
        """Problems with the declared knobs, empty when none.

        Parameters
        ----------
        params : dict
            The node's ``params`` block, possibly carrying ``$`` references.

        Returns
        -------
        list of str
            One message per problem.
        """
        problems = []
        reject_unknown_params(problems, params, cls._PARAMS)
        for name in cls._REQUIRED:
            value = params.get(name)
            if is_node_ref(value):
                continue
            if name in ("relpath_by_key", "columns"):
                if not isinstance(value, dict) or not value:
                    problems.append(f"{name} must be a non-empty dict, got {value!r}")
                elif not all(isinstance(k, str) and isinstance(v, str) and v
                             for k, v in value.items()):
                    problems.append(f"{name} must map strings to non-empty strings")
                elif name == "columns" and len(set(value.values())) < len(value):
                    problems.append("columns maps two file columns to one output field")
            elif not isinstance(value, str) or not value:
                problems.append(f"{name} is required: a non-empty string, got {value!r}")
        problems += cls.window_problems(params.get("window"), params.get("columns"))
        return problems

    @classmethod
    def window_problems(cls, window, columns):
        """Problems with the optional ``window`` block, empty when none or absent."""
        if window is None or is_node_ref(window):
            return []
        if not isinstance(window, dict) or not window:
            return [f"window must be a non-empty dict, got {window!r}"]
        problems = [f"window: unknown key {k!r}" for k in window if k not in cls._WINDOW_KEYS]
        field = window.get("field")
        if not isinstance(field, str) or not field:
            problems.append(f"window.field is required: an output field, got {field!r}")
        elif isinstance(columns, dict) and field not in columns.values():
            problems.append(f"window.field {field!r} is not an output field of columns")
        bounds = {k: window[k] for k in ("start", "end") if window.get(k) is not None}
        if not bounds:
            problems.append("window needs a start or an end")
        parsed = {}
        for name, value in bounds.items():
            try:
                parsed[name] = datetime.date.fromisoformat(value)
                if parsed[name].isoformat() != value:
                    raise ValueError(value)
            except (TypeError, ValueError):
                problems.append(f"window.{name} must be an ISO date YYYY-MM-DD, got {value!r}")
        if {"start", "end"} <= set(parsed) and parsed["start"] > parsed["end"]:
            problems.append("window.start is after window.end")
        return problems

    def _resolve(self):
        """Return (absolute path, manifest digest, relpath) of this key's file."""
        from dskit.onboarding.artifacts import payload_files

        key, table = self.params["key"], self.params["relpath_by_key"]
        if key not in table:
            raise ValueError(
                f"{self.key}: key {key!r} is not in relpath_by_key {sorted(table)}"
            )
        rel = table[key]
        got = payload_files(self.params["root"], self.params["source"], self.params["stream"])
        if rel not in got["files"]:
            raise ValueError(
                f"{self.key}: {rel!r} is not in stream {self.params['stream']!r} of "
                f"source {self.params['source']!r}; it holds {sorted(got['files'])[:5]}..."
            )
        return got["files"][rel], got["sha256"][rel], rel

    def fingerprint(self):
        """Answer the file's manifest identity.

        Returns
        -------
        dict
            ``{"kind", "relpath", "sha256"}`` -- moves when the acquired file does.
        """
        _, digest, rel = self._resolve()
        return {"kind": type(self).__name__, "relpath": rel, "sha256": digest}

    @staticmethod
    def _verified_bytes_ok(path, digest):
        """Say whether the file at ``path`` hashes to ``digest``."""
        return _sha256(path) == digest

    @staticmethod
    def _day(value, field):
        """Return a date, datetime or ISO text field as a ``datetime.date``, else raise."""
        if isinstance(value, datetime.datetime):
            return value.date()
        if isinstance(value, datetime.date):
            return value
        try:
            return datetime.datetime.fromisoformat(value).date()
        except (TypeError, ValueError):
            raise ValueError(
                f"window field {field!r} holds {value!r}, not a date or ISO text"
            ) from None

    def _windowed(self, records):
        """Keep the records inside the declared ``window``; all without one."""
        try:
            return self.cut(records, self.params.get("window"))
        except ValueError as error:
            raise ValueError(f"{self.key}: {error}") from None

    @classmethod
    def cut(cls, records, window):
        """Keep the records whose date field lies inside ``window``: the one window rule.

        Parameters
        ----------
        records : list of dict
            Rows carrying ``window["field"]``.
        window : dict or None
            A validated ``window`` block (see the class); None or empty keeps all.

        Returns
        -------
        list of dict
            The kept rows, in order.

        Raises
        ------
        ValueError
            A date field holds a value that is not a date or ISO text.
        """
        if not window:
            return records
        field = window["field"]
        start, end = (
            datetime.date.fromisoformat(window[k]) if window.get(k) else None
            for k in ("start", "end")
        )
        kept = []
        for record in records:
            if record[field] is None:
                continue
            day = cls._day(record[field], field)
            if (start is None or day >= start) and (end is None or day <= end):
                kept.append(record)
        return kept

    def run(self, ctx, inputs):
        """Read the verified file and project the declared columns.

        Parameters
        ----------
        ctx : NodeContext
            The run frame; unused.
        inputs : dict
            Empty: a data node takes no inputs.

        Returns
        -------
        dict
            ``{"records": [...]}``, one mapping per file row, in file order.

        Raises
        ------
        ValueError
            Unknown key, file absent from the stream, digest drift, or a
            declared column the file lacks.
        """
        import pyarrow.parquet as pq

        path, digest, rel = self._resolve()
        if not self._verified_bytes_ok(path, digest):
            raise ValueError(
                f"{self.key}: {rel!r} no longer matches the manifest sha256 "
                f"{digest} - the acquired file drifted; re-acquire or verify the source"
            )
        columns = self.params["columns"]
        table = pq.read_table(path)
        missing = sorted(set(columns) - set(table.column_names))
        if missing:
            raise ValueError(
                f"{self.key}: {rel!r} has no column(s) {missing}; it has {table.column_names}"
            )
        data = {out: table.column(src).to_pylist() for src, out in columns.items()}
        names = list(data)
        records = [dict(zip(names, row)) for row in zip(*data.values())]
        records = self._windowed(records)
        self.log.info("read %d row(s) of %s", len(records), rel)
        return {"records": records}


class ParquetFrameCache:
    """Keep one built DataFrame on disk per identity, and reuse it while the identity holds.

    A memo, never a data source (ADR-0236 amendment): the caller's identity
    names everything the frame is a function of (its config, the store tokens
    of what it read, the code and environment that built it), so a reuse is
    the frame a rebuild would give. One slot per directory: a build under a
    new identity replaces the old frame. Every caller, the building one
    included, gets the frame and payload read back from disk, so a reuse and
    a build can never differ in dtype or JSON shape. A frame whose object
    columns hold anything but scalars (dicts, lists) is never stored: parquet
    would hand its containers back as arrays.

    Parameters
    ----------
    directory : str or Path
        The slot: ``frame.parquet`` and ``record.json`` (the identity, the
        frame's sha256 and the caller's JSON payload).

    Examples
    --------
    Build a panel once per data identity::

        cache = ParquetFrameCache("runs/study/panel-cache")
        frame, payload, state = cache.load_or_build(
            lambda: {"config": digest, "store": token}, lambda: (build(), {"rows": 3}))
        # state: "reused", "stored" or "unstored"
    """

    FRAME, RECORD = "frame.parquet", "record.json"
    #: What ``pandas.api.types.infer_dtype`` may call an object column of a storable frame.
    SCALAR_KINDS = frozenset({"string", "bytes", "empty", "boolean", "integer", "floating",
                              "mixed-integer-float", "decimal"})

    def __init__(self, directory):
        from pathlib import Path
        self.directory = Path(directory)

    @staticmethod
    def _canonical(identity):
        """Return the identity's canonical JSON, the one form two identities compare in."""
        import json
        return json.dumps(identity, sort_keys=True, separators=(",", ":"), allow_nan=False)

    def load(self, identity):
        """Return ``(frame, payload)`` stored under ``identity``, or None.

        Parameters
        ----------
        identity : dict
            JSON-serializable.

        Returns
        -------
        tuple or None
            None when the slot is empty, holds another identity, or its
            frame no longer hashes to the recorded digest.
        """
        import json

        import pandas as pd

        record, frame = self.directory/self.RECORD, self.directory/self.FRAME
        if not (record.is_file() and frame.is_file()):
            return None
        stored = json.loads(record.read_text())
        if (stored.get("identity") != self._canonical(identity)
                or _sha256(frame) != stored.get("frame_sha256")):
            return None
        return pd.read_parquet(frame), stored["payload"]

    @classmethod
    def storable(cls, frame):
        """Say whether parquet hands ``frame`` back unchanged: every object column scalar.

        Parameters
        ----------
        frame : DataFrame

        Returns
        -------
        bool
        """
        from pandas.api.types import infer_dtype
        return all(infer_dtype(frame[name], skipna=True) in cls.SCALAR_KINDS
                   for name in frame.columns if frame[name].dtype == object)

    def store(self, identity, frame, payload):
        """Replace the slot with ``frame`` and ``payload`` under ``identity``.

        Parameters
        ----------
        identity : dict
            JSON-serializable.
        frame : DataFrame
            A :meth:`storable` frame, written as parquet without its index.
        payload : dict
            JSON-serializable; stored and returned as JSON gives it back.

        Raises
        ------
        ValueError
            When ``frame`` is not :meth:`storable`.
        """
        import json
        import os
        import tempfile

        from dskit.pipeline.node import atomic_write

        if not self.storable(frame):
            raise ValueError("a frame with container-valued object columns is not stored")
        self.directory.mkdir(parents=True, exist_ok=True)
        (self.directory/self.RECORD).unlink(missing_ok=True)  # never a record over a new frame
        handle, partial = tempfile.mkstemp(dir=self.directory, suffix=".partial")
        os.close(handle)
        frame.to_parquet(partial, index=False)
        digest = _sha256(partial)  # of these bytes: a concurrent writer's frame cannot pass load
        os.replace(partial, self.directory/self.FRAME)
        atomic_write(str(self.directory/self.RECORD), json.dumps({
            "identity": self._canonical(identity), "frame_sha256": digest,
            "payload": payload}, indent=1, allow_nan=False).encode())

    def load_or_build(self, identity, build):
        """Return the stored frame for the current identity, building and storing it if absent.

        Parameters
        ----------
        identity : callable
            Returns the JSON identity; asked before and after a build, and a
            build during which it moved is returned but never stored.
        build : callable
            Returns ``(frame, payload)``.

        Returns
        -------
        tuple
            ``(frame, payload, state)``: ``reused`` (from the slot), ``stored``
            (built, stored and read back) or ``unstored`` (built; not
            :meth:`storable`, the identity moved, or another writer took the slot).
        """
        import json

        before = identity()
        held = self.load(before)
        if held is not None:
            return (*held, "reused")
        frame, payload = build()
        if self.storable(frame) and self._canonical(identity()) == self._canonical(before):
            self.store(before, frame, payload)
            held = self.load(before)
            if held is not None:
                return (*held, "stored")
        return frame, json.loads(json.dumps(payload)), "unstored"

    @staticmethod
    def code_digest(*packages):
        """Return a sha256 over every ``.py`` file of the given packages, by path and bytes.

        Parameters
        ----------
        *packages : module
            Packages (or modules) whose directory holds the code that builds a frame.

        Returns
        -------
        str
            A hex digest that moves with any edit of that code (compiled files
            and data beside it never move it).
        """
        from pathlib import Path

        digest = hashlib.sha256()
        for package in packages:
            top = Path(package.__file__).parent
            for path in sorted(top.rglob("*.py")):
                digest.update(f"{package.__name__}/{path.relative_to(top).as_posix()}:"
                              f"{_sha256(path)}\n".encode())
        return digest.hexdigest()


#: The pack's kinds.
NODE_KINDS = (("parquet-rows", ParquetRows),)


def register(registry=None):
    """Claim the pack's kind names in ``registry`` (default the toolkit's).

    Parameters
    ----------
    registry : NodeKindRegistry or None
        Where to register; ``None`` means the default. Idempotent.

    Returns
    -------
    NodeKindRegistry
        The registry registered into.
    """
    registry = DEFAULT_NODE_KINDS if registry is None else registry
    for name, cls in NODE_KINDS:
        if name not in registry:
            registry.register(name, cls)
    return registry
