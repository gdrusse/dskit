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
"""

from __future__ import annotations

import datetime
import hashlib

from dskit.pipeline.document import is_node_ref
from dskit.pipeline.node import DEFAULT_NODE_KINDS, Node, reject_unknown_params

__all__ = ["NODE_KINDS", "ParquetRows", "register"]

#: Bytes per hashing read; a price file is small, an options file is not.
_CHUNK = 1 << 20


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
        problems += cls._window_problems(params.get("window"), params.get("columns"))
        return problems

    @classmethod
    def _window_problems(cls, window, columns):
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
        h = hashlib.sha256()
        with open(path, "rb") as fh:
            for block in iter(lambda: fh.read(_CHUNK), b""):
                h.update(block)
        return h.hexdigest() == digest

    def _day(self, value):
        """Return a date, datetime or ISO text field as a ``datetime.date``, else raise."""
        field = self.params["window"]["field"]
        if isinstance(value, datetime.datetime):
            return value.date()
        if isinstance(value, datetime.date):
            return value
        try:
            return datetime.datetime.fromisoformat(value).date()
        except (TypeError, ValueError):
            raise ValueError(
                f"{self.key}: window field {field!r} holds {value!r}, not a date or ISO text"
            ) from None

    def _windowed(self, records):
        """Keep the records whose date field lies inside ``window``; all without one."""
        window = self.params.get("window")
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
            day = self._day(record[field])
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
