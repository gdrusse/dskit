r"""Per-snapshot Parquet export with incremental quality checks (ADR-0265 Amendment 2).

An onboarding store keeps each pull twice as gzip JSON lines (``raw/`` and
``observations/``), about 113 B a row each. Research reads the rows, not the
envelope, so every COMMITTED snapshot is converted once into one immutable
Parquet file, ``<out>/<source>/<acq_id>.parquet``, about 72 B a row:

- Explicit schema from the :meth:`~SnapshotParquetExport.schema` hook. The JSON
  is parsed with ``unexpected_field_behavior="error"``: a field the schema does
  not name refuses the conversion and is NAMED. Every cast is safe (an
  overflowing or unparseable value raises, naming the column). Nothing is
  pruned at write time; pruning is the reader's choice.
- zstd level 9, dictionary encoding, row groups of 256k rows; 65k input lines
  are parsed at a time, so peak memory stays near one row group.
- Written to a hidden temp name, fsynced, then renamed: a reader sees a whole
  file or none. The footer carries the raw manifest's sha256, the snapshot hash,
  the schema version and a digest of the schema. A re-run SKIPS a snapshot whose
  file carries matching metadata and REFUSES (never overwrites) one that
  carries different metadata.
- "Committed" means the directory has no leading dot, ``raw/<source>/<id>/
  manifest.json`` exists AND the observations stream file exists. Staging
  (``.stage-*``) and half-landed snapshots are skipped, picked up next run.

After each snapshot the quality hook runs on the WRITTEN file (only the columns
it names are read back, one row group at a time) and one JSON line is appended to
``<out>/verify.jsonl``: snapshot, rows, the record count the raw payload holds,
files, seconds, the numbers measured, every threshold used, the alarm names, the
per-group date windows and the count of rows missing a schema field. The base
class itself alarms on ``row_count_mismatch``, ``expected_rows_unavailable`` (the
raw payload could not be counted) and ``overlapping_symbol_window`` (a new
snapshot's date window for a group overlaps an earlier file's: a crash re-pull).
The run stops after the first snapshot that raised an alarm (exit 3), once that
snapshot is converted and recorded; a restart skips it (the restart is the
acknowledgement) and goes on. Exit codes: 0 clean, 3 an alarm fired, 1 an error
(a refusal included). A row missing a key field refuses the conversion.

``verify`` re-counts every file's rows from its footer against ``verify.jsonl``,
re-checks every file for window overlaps and, given ``--root``, lists committed
raw snapshots that still lack their observations file (``raw_only``, a warning).

Usage::

    python -m dskit.onboarding.libs.parquet_export --export-class pkg.mod:Class \\
        --root R --source S --stream T --out DIR [--max-snapshots N] [--params F.json]
    python -m dskit.onboarding.libs.parquet_export verify --out DIR [--source S] [--root R --stream T]

Import cost: stdlib; pyarrow is imported inside the verbs and refused loudly
when absent.
"""

from __future__ import annotations

import argparse
import contextlib
import copy
import fcntl
import gzip
import hashlib
import importlib
import itertools
import json
import os
import re
import sys
import time
import zlib
from abc import ABC, abstractmethod
from collections import OrderedDict

from ..base import AssetError, durable_write_json, file_digest, fsync_dir, utc_now
from ..codec import resolve_stream_file
from ..layout import OnboardingRoot
from ..observations import stream_dir, stream_members
from ..snapshot import read_manifest, snapshot_hash

__all__ = ["SnapshotParquetExport", "main", "raw_only_snapshots", "verify_tree"]

#: The footer keys (all under this prefix) that name what a file was made from.
_PREFIX = "dskit.export."
#: Footer keys that must agree for an existing file to count as "the same export".
_IDENTITY = ("manifest_sha256", "snapshot_hash", "schema_version", "schema_digest")
#: The envelope acquire wraps every observation row in; ``data`` holds the fields.
_ENVELOPE = ("acquired_at", "data", "effective_date", "kind", "mode", "stream")
#: The envelope timestamp column the export appends after the schema's own fields.
_ACQUIRED = "acquired_at"
#: Base-class alarms (the subclass adds its own).
ALARM_ROWS, ALARM_UNKNOWN, ALARM_OVERLAP = (
    "row_count_mismatch", "expected_rows_unavailable", "overlapping_symbol_window")
_CLASS_REF = re.compile(r"^[A-Za-z_][\w.]*:[A-Za-z_]\w*\Z")
_DECODE_ERRORS = (OSError, EOFError, zlib.error)
_LOG = "verify.jsonl"
_LOCK = ".export.lock"
_TMP = ".{}.parquet.tmp"
EXIT_OK, EXIT_ERROR, EXIT_ALARM = 0, 1, 3


def _pyarrow():
    """Import pyarrow (json, compute, parquet) inside a verb; refuse loudly when absent."""
    try:
        import pyarrow
        import pyarrow.compute
        import pyarrow.json
        import pyarrow.parquet
    except ImportError as exc:
        raise AssetError(
            ["parquet_export needs pyarrow (pip install \"dskit[parquet]\")"]
        ) from exc
    return pyarrow


def _resolve_export(ref):
    """Turn ``pkg.module:Class`` into a :class:`SnapshotParquetExport` subclass."""
    if not isinstance(ref, str) or not _CLASS_REF.match(ref):
        raise AssetError([f"--export-class must look like pkg.module:Class, got {ref!r}"])
    module_name, attr = ref.split(":", 1)
    try:
        cls = getattr(importlib.import_module(module_name), attr, None)
    except Exception as exc:  # a module that crashes at import must not escape raw
        raise AssetError([f"cannot import export class {ref!r}: {exc}"]) from exc
    if not (isinstance(cls, type) and issubclass(cls, SnapshotParquetExport)):
        raise AssetError([f"{ref!r} is not a SnapshotParquetExport subclass"])
    return cls


def raw_only_snapshots(root, source, stream) -> list:
    """Return committed raw snapshots of ``stream`` whose observations file has not landed yet.

    A pull renames ``raw/`` first and the rows after, so one of these is normal for a
    moment; one that stays is a half-landed commit. Never an alarm: a warning.
    """
    layout = OnboardingRoot(root) if not isinstance(root, OnboardingRoot) else root
    base = layout.raw_dir(source)
    if not os.path.isdir(base):
        return []
    found = []
    for acq_id in sorted(os.listdir(base)):
        if acq_id.startswith("."):
            continue
        snap = os.path.join(base, acq_id)
        if not os.path.isfile(os.path.join(snap, "manifest.json")):
            continue
        if resolve_stream_file(os.path.join(snap, "payload"), stream) is None:
            continue  # a snapshot of another stream of this source
        records = layout.records_dir(source, acq_id)
        if not os.path.isdir(records) or resolve_stream_file(records, stream) is None:
            found.append(acq_id)
    return found


def _read_log(path, repair=False):
    """Return ``(lines, torn)``: every parsed verify line, and the torn tail text or ``None``.

    Only the LAST line may be torn (a crash mid-append): it is reported, and with
    ``repair`` cut off so the next append starts on a clean line. A bad line anywhere else
    refuses. A final line without its newline that still parses is kept and terminated.
    """
    lines, torn = [], None
    if not os.path.exists(path):
        return lines, torn
    size = 0
    with open(path, "rb") as fh:
        for n, raw in enumerate(fh, 1):
            if not raw.endswith(b"\n"):  # the last line, never terminated
                try:
                    lines.append(json.loads(raw))
                    if repair:
                        with open(path, "ab") as add:
                            add.write(b"\n")
                except ValueError:
                    torn = raw.decode("utf-8", "replace")
                    if repair:
                        os.truncate(path, size)
                break
            size += len(raw)
            if raw.strip():
                try:
                    lines.append(json.loads(raw))
                except ValueError as exc:
                    raise AssetError([f"{path}:{n} is not JSON: {exc}"]) from exc
    return lines, torn


def _overlap_pairs(windows_by_snapshot):
    """Yield ``(group, first snapshot, second snapshot, (lo, hi))`` for every overlapping pair of windows."""
    by_group = {}
    for snap, windows in windows_by_snapshot.items():
        for group, (lo, hi) in windows.items():
            by_group.setdefault(group, []).append((lo, hi, snap))
    for group, spans in sorted(by_group.items()):
        spans.sort()
        reach = None
        for lo, hi, snap in spans:
            if reach is not None and lo <= reach[0]:
                yield group, reach[1], snap, (lo, min(hi, reach[0]))
            if reach is None or hi > reach[0]:
                reach = (hi, snap)


class SnapshotParquetExport(ABC):
    """Convert every committed snapshot of one source stream to Parquet and check it.

    Subclass hooks: :meth:`schema` (required), :meth:`checks`,
    :meth:`check_columns`, :meth:`key_fields`, :meth:`window_fields`,
    :meth:`timezone_notes`, :meth:`expected_rows`.
    Threshold parameters are declared in the class's ``_PARAMS`` tuple of
    ``(name, default)`` pairs (collected up the bases); a ``params`` mapping or
    a ``--params`` JSON file overrides defaults, may name only declared ones, and
    must match each default's type.

    Parameters
    ----------
    root : str
        The onboarding root (read only: nothing under it is ever written).
    source, stream : str
        The registered source and stream.
    out_dir : str
        Where ``<source>/<acq_id>.parquet`` and ``verify.jsonl`` go.
    params : dict, optional
        Overrides for the declared parameters.
    """

    #: Bumped when the schema() contract changes; part of every file's identity.
    SCHEMA_VERSION = 1
    #: Input lines parsed at a time.
    LINE_BLOCK = 65536
    #: Rows per Parquet row group.
    ROW_GROUP_ROWS = 262144
    COMPRESSION = "zstd"
    COMPRESSION_LEVEL = 9
    #: A small JSON mapping kept in ``<out>/<STATE_FILE>`` across snapshots (None = none).
    STATE_FILE = None
    _PARAMS = ()

    def __init__(self, root, source, stream, out_dir, params=None):
        self.root = OnboardingRoot(root)
        self.source, self.stream, self.out_dir = source, stream, os.path.abspath(out_dir)
        declared = self.param_defaults()
        if not isinstance(params or {}, dict):
            raise AssetError([f"params must be a JSON object, got {type(params).__name__}"])
        unknown = sorted(set(params or ()) - set(declared))
        if unknown:
            raise AssetError(
                [f"unknown export param(s) {unknown} — declared: {sorted(declared)}"])
        self.params = {**declared, **(params or {})}
        problems = self.param_problems()
        if problems:
            raise AssetError(problems)
        self.state, self._windows, self._history_torn = {}, {}, None

    @classmethod
    def param_defaults(cls) -> dict:
        """Return the declared parameters and their defaults, bases first."""
        merged = OrderedDict()
        for klass in reversed(cls.__mro__):
            merged.update(dict(vars(klass).get("_PARAMS", ())))
        return dict(merged)

    def param_problems(self) -> list:
        """Return every problem with the merged parameters (a wrong type); subclasses may add."""
        problems = []
        for name, default in self.param_defaults().items():
            value = self.params[name]
            if isinstance(default, bool):
                good = isinstance(value, bool)
            elif isinstance(default, (int, float)):
                good = isinstance(value, (int, float)) and not isinstance(value, bool)
            elif isinstance(default, str):
                good = isinstance(value, str)
            else:
                good = True
            if not good:
                problems.append(f"param {name!r} must be a {type(default).__name__}, got {value!r}")
        return problems

    # -- hooks --------------------------------------------------------------

    @abstractmethod
    def schema(self) -> "OrderedDict":
        """Ordered ``{field: arrow type}`` of the row's ``data`` object.

        ``acquired_at`` (timestamp[ms, UTC]) is appended by the export; the other
        envelope keys are constant per snapshot and dropped.
        """

    def check_columns(self):
        """Columns :meth:`checks` needs read back (``None`` = all)."""
        return None

    def key_fields(self) -> tuple:
        """Fields that identify a row; a row lacking one refuses the conversion."""
        return ()

    def window_fields(self):
        """``(group field, date field)`` whose per-group date window is recorded, or ``None``."""
        return None

    def timezone_notes(self) -> dict:
        """Return ``{field: how to read its instants}``, written to the footer and the identity."""
        return {_ACQUIRED: "UTC"}

    def checks(self, batches, report) -> list:
        """Per-snapshot quality checks over the written file; return alarm names.

        ``batches`` yields pyarrow Tables of :meth:`check_columns`, one per row
        group, in file order: a snapshot can hold tens of millions of rows, so a
        check accumulates instead of holding the file. ``report`` is a dict: write the
        numbers measured into ``report["quality"]``, a human line per alarm into
        ``report["detail"]``, and anything the next snapshot needs into
        ``report["carry"]`` (a flat str -> JSON mapping; the latest value of a
        key wins across snapshots and arrives as ``report["carried"]``).
        ``report["thresholds"]`` holds the merged parameters, ``report["state"]``
        the mutable :attr:`STATE_FILE` mapping (saved after the snapshot) and
        ``report["reread"]()`` a fresh ``batches`` for a second pass.
        """
        return []

    def expected_rows(self, acq_id):
        """Observation records the snapshot's RAW payload holds (``None`` = unknown)."""
        path = resolve_stream_file(os.path.join(self._snapshot_dir(acq_id), "payload"), self.stream)
        if path is None:
            return None
        count = 0
        try:
            with (gzip.open(path, "rb") if path.endswith(".gz") else open(path, "rb")) as fh:
                for line in fh:
                    if line.rfind(b'"type": "RECORD"') >= 0 and b'"kind": "forecast"' not in line:
                        count += 1
        except _DECODE_ERRORS as exc:
            raise AssetError([f"corrupt raw payload {path}: {exc}"]) from exc
        return count

    # -- paths and discovery -------------------------------------------------

    def _snapshot_dir(self, acq_id):
        return self.root.snapshot_dir(self.source, acq_id)

    def parquet_path(self, acq_id):
        """Return where ``acq_id``'s Parquet file lives."""
        return os.path.join(self.out_dir, self.source, f"{acq_id}.parquet")

    def committed_snapshots(self) -> list:
        """Return the committed snapshot ids in acquisition order (see the module docstring)."""
        if not os.path.isdir(stream_dir(self.root.root, self.source)):
            raise AssetError(
                [f"no observations directory for source {self.source!r} under "
                 f"{self.root.root} — wrong root, or a source never acquired"])
        found = []
        for acq_id, *_rest in stream_members(self.root.root, self.source, self.stream):
            if acq_id.startswith("."):
                continue
            if os.path.isfile(os.path.join(self._snapshot_dir(acq_id), "manifest.json")):
                found.append(acq_id)
        return found

    # -- identity -------------------------------------------------------------

    def _schema_digest(self, fields) -> str:
        spec = [[[name, str(kind)] for name, kind in fields.items()], sorted(self.timezone_notes().items())]
        return hashlib.sha256(json.dumps(spec).encode()).hexdigest()

    def metadata(self, acq_id, fields) -> dict:
        """Return the footer key/values naming what this file is made from."""
        snap = self._snapshot_dir(acq_id)
        return {
            "acq_id": acq_id, "source": self.source, "stream": self.stream,
            "export_class": f"{type(self).__module__}:{type(self).__qualname__}",
            "manifest_sha256": file_digest(os.path.join(snap, "manifest.json")),
            "snapshot_hash": snapshot_hash(read_manifest(snap)),
            "schema_version": str(self.SCHEMA_VERSION),
            "schema_digest": self._schema_digest(fields),
            "timezones": json.dumps(self.timezone_notes(), sort_keys=True),
        }

    def _footer(self, path) -> dict:
        pa = _pyarrow()
        try:
            raw = pa.parquet.read_metadata(path).metadata or {}
        except (OSError, pa.ArrowException) as exc:
            raise AssetError([f"existing output {path} is unreadable: {exc} — refusing to overwrite"]) from exc
        return {k.decode(): v.decode() for k, v in raw.items() if k.decode().startswith(_PREFIX)}

    def _same_export(self, path, wanted) -> dict:
        """Return ``path``'s footer when it carries ``wanted``'s identity; refuse (naming the keys) otherwise."""
        have = self._footer(path)
        differs = [k for k in _IDENTITY if have.get(_PREFIX + k) != wanted[k]]
        if differs:
            detail = "; ".join(f"{k}: file {have.get(_PREFIX + k)!r} vs now {wanted[k]!r}" for k in differs)
            raise AssetError(
                [f"{path} exists with different metadata ({detail}) — refusing to overwrite "
                 "an immutable export; move it aside deliberately if the change is intended"])
        return have

    # -- conversion -------------------------------------------------------------

    def _wire_schema(self, fields):
        pa = _pyarrow()
        wire = []
        for name, kind in fields.items():
            if pa.types.is_integer(kind):
                kind = pa.int64()
            elif pa.types.is_floating(kind):
                kind = pa.float64()
            elif not pa.types.is_boolean(kind):
                kind = pa.string()
            wire.append((name, kind))
        return pa.schema([(_ACQUIRED, pa.string()), ("data", pa.struct(wire)),
                          ("effective_date", pa.string()), ("kind", pa.string()),
                          ("mode", pa.string()), ("stream", pa.string())])

    def _blocks(self, path):
        """Yield ``(first_line_no, [bytes lines])``, ``LINE_BLOCK`` lines at a time."""
        try:
            with (gzip.open(path, "rb") if path.endswith(".gz") else open(path, "rb")) as fh:
                first = 1
                while True:
                    block = list(itertools.islice(fh, self.LINE_BLOCK))
                    if not block:
                        return
                    yield first, block
                    first += len(block)
        except _DECODE_ERRORS as exc:
            raise AssetError([f"corrupt or unreadable observations {path}: {exc}"]) from exc

    def _diagnose(self, first, block, fields, original):
        """Name the unexpected field (or the bad line) behind a failed block parse."""
        allowed, data_allowed = set(_ENVELOPE), set(fields)
        for offset, line in enumerate(block):
            where = f"line {first + offset}"
            try:
                row = json.loads(line)
            except ValueError as exc:
                return f"{where} is not JSON: {exc}"
            extra = sorted(set(row) - allowed)
            if extra:
                return f"{where}: unexpected envelope key(s) {extra}"
            data = row.get("data")
            if not isinstance(data, dict):
                return f"{where}: data is not an object"
            extra = sorted(set(data) - data_allowed)
            if extra:
                return f"{where}: unexpected field(s) {extra} not in the schema"
        return f"block starting at line {first}: {original}"

    def _audit(self, acq_id, raw, data, payload, first, fields, missing):
        """Count the rows missing a field (absent, not an explicit null); refuse a null key field."""
        keys = set(self.key_fields())
        columns = [(name, data.field(name)) for name in fields] + [
            (name, raw.column(name)) for name in _ENVELOPE if name != "data"]
        for name, column in columns:
            if name in keys and column.null_count:
                raise AssetError(
                    [f"{acq_id}: key field {name!r} is missing or null in {column.null_count} row(s) of the "
                     f"block starting at line {first} — refusing to convert"])
        # Fast path: every row carries every key, so the count of '": ' is exact. Only a short
        # count (a missing field) pays for attributing it column by column.
        if payload.count(b'": ') >= raw.num_rows * (len(fields) + len(_ENVELOPE)):
            return
        for name, column in columns:
            if column.null_count:
                gap = column.null_count - payload.count(f'"{name}": null'.encode())
                if gap > 0:
                    missing[name] = missing.get(name, 0) + gap

    def _typed(self, data, raw, fields, first):
        """Cast one parsed block to the output types, safely, naming the column that fails."""
        pa, pc = _pyarrow(), _pyarrow().compute
        columns = []
        for name, kind in fields.items():
            column = data.field(name)
            if column.type != kind:
                try:
                    column = pc.cast(column, kind, safe=True)
                except (pa.ArrowInvalid, pa.ArrowNotImplementedError) as exc:
                    raise AssetError(
                        [f"column {name!r} (block from line {first}): cannot cast "
                         f"{column.type} to {kind} safely: {exc}"]) from exc
            columns.append(column)
        try:
            stamp = pc.cast(raw.column(_ACQUIRED).combine_chunks(), pa.timestamp("ms", "UTC"))
        except (pa.ArrowInvalid, pa.ArrowNotImplementedError) as exc:
            raise AssetError([f"column {_ACQUIRED!r} (block from line {first}): {exc}"]) from exc
        return pa.Table.from_arrays(columns + [stamp], names=list(fields) + [_ACQUIRED])

    def _write_groups(self, writer, buffered, final):
        """Write whole row groups from ``buffered`` (the tail too when ``final``); return the rest."""
        pa = _pyarrow()
        table = pa.concat_tables(buffered)
        size = self.ROW_GROUP_ROWS
        cut = table.num_rows if final else (table.num_rows // size) * size
        for start in range(0, cut, size):
            writer.write_table(table.slice(start, min(size, cut - start)), row_group_size=size)
        rest = table.slice(cut)
        return [rest] if rest.num_rows else []

    @staticmethod
    def _widen(windows, table, group, day):
        """Fold one block's per-group ``(min, max)`` of ``day`` into ``windows``."""
        found = table.select([group, day]).group_by(group, use_threads=False).aggregate(
            [(day, "min"), (day, "max")])
        for name, lo, hi in zip(found[group].to_pylist(), found[f"{day}_min"].to_pylist(),
                                found[f"{day}_max"].to_pylist()):
            lo, hi = str(lo), str(hi)
            now = windows.get(name)
            windows[name] = [lo, hi] if now is None else [min(now[0], lo), max(now[1], hi)]

    def convert(self, acq_id) -> dict:
        """Write one snapshot's Parquet file; return ``rows``, ``path``, ``seconds``, ``windows``, ``missing``."""
        pa, pj, pq = _pyarrow(), _pyarrow().json, _pyarrow().parquet
        fields = self.schema()
        if _ACQUIRED in fields:
            raise AssetError([f"schema() must not declare {_ACQUIRED!r}; the export appends it"])
        meta = {_PREFIX + k: v for k, v in self.metadata(acq_id, fields).items()}
        out_schema = pa.schema(
            [(n, t) for n, t in fields.items()] + [(_ACQUIRED, pa.timestamp("ms", "UTC"))]
        ).with_metadata(meta)
        source_file = resolve_stream_file(self.root.records_dir(self.source, acq_id), self.stream)
        final = self.parquet_path(acq_id)
        tmp = os.path.join(os.path.dirname(final), _TMP.format(acq_id))
        parse = pj.ParseOptions(explicit_schema=self._wire_schema(fields),
                                unexpected_field_behavior="error")
        window = self.window_fields()
        started, rows, buffered, held = time.monotonic(), 0, [], 0
        windows, missing, writer = {}, {}, None
        with contextlib.suppress(FileNotFoundError):
            os.remove(tmp)  # a crash between the temp write and the rename leaves this behind
        try:
            writer = pq.ParquetWriter(tmp, out_schema, compression=self.COMPRESSION,
                                      compression_level=self.COMPRESSION_LEVEL, use_dictionary=True)
            for first, block in self._blocks(source_file):
                payload = b"".join(block)
                try:
                    raw = pj.read_json(pa.py_buffer(payload), parse_options=parse)
                except pa.ArrowInvalid as exc:
                    raise AssetError(
                        [f"{acq_id}: {self._diagnose(first, block, fields, exc)}"]) from exc
                data = raw.column("data").combine_chunks()
                self._audit(acq_id, raw, data, payload, first, fields, missing)
                table = self._typed(data, raw, fields, first)
                if window:
                    self._widen(windows, table, *window)
                rows += table.num_rows
                buffered.append(table)
                held += table.num_rows
                if held >= self.ROW_GROUP_ROWS:
                    buffered = self._write_groups(writer, buffered, final=False)
                    held = sum(t.num_rows for t in buffered)
            if buffered:
                self._write_groups(writer, buffered, final=True)
            writer.add_key_value_metadata({
                _PREFIX + "windows": json.dumps(windows, sort_keys=True),
                _PREFIX + "missing_fields": json.dumps(missing, sort_keys=True)})
            writer.close()
            writer = None
            with open(tmp, "rb") as fh:
                os.fsync(fh.fileno())
            os.replace(tmp, final)
            fsync_dir(os.path.dirname(final))
        except BaseException:
            if writer is not None:
                with contextlib.suppress(Exception):
                    writer.close()
            with contextlib.suppress(FileNotFoundError):
                os.remove(tmp)
            raise
        return {"rows": rows, "path": final, "windows": windows, "missing": missing,
                "seconds": round(time.monotonic() - started, 3)}

    # -- checking -----------------------------------------------------------------

    def _read_back(self, path):
        """Yield the written file's :meth:`check_columns` one row group at a time."""
        pa = _pyarrow()
        with pa.parquet.ParquetFile(path) as handle:
            for batch in handle.iter_batches(batch_size=self.ROW_GROUP_ROWS, columns=self.check_columns()):
                yield pa.Table.from_batches([batch])

    def _overlaps(self, acq_id, windows) -> list:
        """Return ``(group, earlier snapshot, (lo, hi))`` where ``windows`` overlaps a recorded file's."""
        clashes = []
        for group, (lo, hi) in windows.items():
            for other_lo, other_hi, other in self._windows.get(group, ()):
                if other != acq_id and lo <= other_hi and other_lo <= hi:
                    clashes.append((group, other, (max(lo, other_lo), min(hi, other_hi))))
        return clashes

    def _check(self, acq_id, path, rows, carried, windows) -> dict:
        started = time.monotonic()
        expected = self.expected_rows(acq_id)
        counted = time.monotonic()
        report = {"quality": {}, "detail": {}, "carry": {}, "carried": carried, "state": self.state,
                  "thresholds": dict(self.params), "rows": rows, "expected_rows": expected,
                  "reread": lambda: self._read_back(path)}
        alarms = []
        if expected is None:
            alarms.append(ALARM_UNKNOWN)
            report["detail"][ALARM_UNKNOWN] = "the raw payload's record count could not be read"
        elif expected != rows:
            alarms.append(ALARM_ROWS)
            report["detail"][ALARM_ROWS] = f"parquet has {rows} rows, the raw payload holds {expected} records"
        footer_rows = _pyarrow().parquet.read_metadata(path).num_rows
        if footer_rows != rows:
            alarms.append(ALARM_ROWS)
            report["detail"][ALARM_ROWS] = f"wrote {rows} rows, footer says {footer_rows}"
        clashes = self._overlaps(acq_id, windows)
        if clashes:
            alarms.append(ALARM_OVERLAP)
            report["detail"][ALARM_OVERLAP] = f"{len(clashes)} window(s) overlap an earlier file: " + "; ".join(
                f"{g} {acq_id} vs {other} on {lo}..{hi}" for g, other, (lo, hi) in clashes[:5])
        alarms.extend(a for a in self.checks(self._read_back(path), report) if a not in alarms)
        report["alarms"] = alarms
        report["count_seconds"] = round(counted - started, 3)
        report["check_seconds"] = round(time.monotonic() - counted, 3)
        return report

    # -- the run ----------------------------------------------------------------

    @contextlib.contextmanager
    def _lock(self):
        os.makedirs(self.out_dir, exist_ok=True)
        with open(os.path.join(self.out_dir, _LOCK), "w") as fh:
            try:
                fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise AssetError([f"an export into {self.out_dir} is already running"]) from exc
            try:
                yield
            finally:
                fcntl.flock(fh, fcntl.LOCK_UN)

    def _history(self):
        """Return ``(recorded snapshot ids, merged carry)`` and index this stream's windows."""
        recorded, carried, self._windows = set(), {}, {}
        lines, self._history_torn = _read_log(os.path.join(self.out_dir, _LOG), repair=True)
        for line in lines:
            if line.get("source") == self.source and line.get("stream") == self.stream:
                recorded.add(line["snapshot"])
                carried.update(line.get("carry") or {})
                self._index(line["snapshot"], line.get("windows") or {})
        return recorded, carried

    def _index(self, acq_id, windows):
        for group, (lo, hi) in windows.items():
            self._windows.setdefault(group, []).append((lo, hi, acq_id))

    def _load_state(self):
        self.state = {}
        if self.STATE_FILE:
            path = os.path.join(self.out_dir, self.STATE_FILE)
            if os.path.exists(path):
                try:
                    with open(path, encoding="utf-8") as fh:
                        self.state = json.load(fh)
                except ValueError as exc:
                    raise AssetError([f"{path} is not JSON: {exc}"]) from exc

    def _save_state(self, before):
        if self.STATE_FILE and self.state != before:
            durable_write_json(os.path.join(self.out_dir, self.STATE_FILE), self.state)

    def _append(self, line):
        """Append one whole line in a single write, then fsync."""
        data = (json.dumps(line, allow_nan=False) + "\n").encode()
        path = os.path.join(self.out_dir, _LOG)
        created = not os.path.exists(path)
        fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
        try:
            view = memoryview(data)
            while view:
                view = view[os.write(fd, view):]
            os.fsync(fd)
        finally:
            os.close(fd)
        if created:
            fsync_dir(self.out_dir)

    def process(self, acq_id, carried) -> dict:
        """Convert (or reuse) one snapshot, check it, and append its verify line."""
        started = time.monotonic()
        fields = self.schema()
        path = self.parquet_path(acq_id)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        if os.path.exists(path):
            have = self._same_export(path, self.metadata(acq_id, fields))
            converted, convert_seconds = False, 0.0
            rows = _pyarrow().parquet.read_metadata(path).num_rows
            windows = json.loads(have.get(_PREFIX + "windows", "{}"))
            missing = json.loads(have.get(_PREFIX + "missing_fields", "{}"))
        else:
            result = self.convert(acq_id)
            converted, rows, convert_seconds = True, result["rows"], result["seconds"]
            windows, missing = result["windows"], result["missing"]
        before = copy.deepcopy(self.state)
        report = self._check(acq_id, path, rows, carried, windows)
        line = {
            "snapshot": acq_id, "source": self.source, "stream": self.stream, "at": utc_now(),
            "export_class": f"{type(self).__module__}:{type(self).__qualname__}",
            "schema_version": self.SCHEMA_VERSION, "converted": converted,
            "rows": rows, "expected_rows": report["expected_rows"],
            "manifest_files": len(read_manifest(self._snapshot_dir(acq_id))["files"]),
            "files": [{"path": os.path.relpath(path, self.out_dir), "bytes": os.path.getsize(path)}],
            "seconds": round(time.monotonic() - started, 3), "convert_seconds": convert_seconds,
            "count_seconds": report["count_seconds"], "check_seconds": report["check_seconds"],
            "quality": report["quality"], "thresholds": report["thresholds"],
            "alarms": report["alarms"], "detail": report["detail"], "carry": report["carry"],
            "windows": windows, "missing_fields": missing,
        }
        self._save_state(before)
        self._append(line)
        self._index(acq_id, windows)
        return line

    def run(self, max_snapshots=None, out=None) -> int:
        """Process unconverted committed snapshots in order; return the exit code.

        Stops after the first snapshot that raised an alarm (it is converted and
        recorded first) and returns 3; returns 0 when none did. A snapshot already
        recorded in ``verify.jsonl`` is skipped whatever it recorded, once its file's
        identity is confirmed.
        """
        out = out or sys.stdout
        with self._lock():
            recorded, carried = self._history()
            self._load_state()
            if self._history_torn is not None:
                print(f"[export] WARN torn last line cut from {_LOG}: {self._history_torn[:80]!r}", file=out)
            half = raw_only_snapshots(self.root, self.source, self.stream)
            if half:
                print(f"[export] WARN raw_only (raw committed, observations not landed): {half}", file=out)
            done = 0
            for acq_id in self.committed_snapshots():
                if max_snapshots is not None and done >= max_snapshots:
                    break
                if acq_id in recorded:
                    path = self.parquet_path(acq_id)
                    if not os.path.exists(path):
                        raise AssetError([f"{acq_id} is recorded in {_LOG} but {path} is missing"])
                    self._same_export(path, self.metadata(acq_id, self.schema()))
                    continue
                line = self.process(acq_id, carried)
                carried.update(line["carry"])
                done += 1
                print(f"[export] {acq_id} rows={line['rows']} bytes={line['files'][0]['bytes']} "
                      f"s={line['seconds']} alarms={line['alarms']}", file=out, flush=True)
                if line["alarms"]:
                    for name in line["alarms"]:
                        print(f"[export] ALARM {name}: {line['detail'].get(name, '')}", file=out, flush=True)
                    return EXIT_ALARM
        return EXIT_OK


# -- verify ---------------------------------------------------------------------


def verify_tree(out_dir, only_source=None, out=None, root=None, stream=None) -> int:
    """Re-count footers against ``verify.jsonl``; print a summary per source; 0 when consistent."""
    out = out or sys.stdout
    pq = _pyarrow().parquet
    log = os.path.join(out_dir, _LOG)
    if not os.path.isfile(log):
        raise AssetError([f"no {_LOG} under {out_dir}"])
    entries, torn = _read_log(log)
    latest, problems = {}, []
    if torn is not None:
        problems.append(f"{_LOG} ends in a torn line {torn[:60]!r} (the next export run cuts it)")
    for line in entries:
        if only_source in (None, line["source"]):
            key = (line["source"], line["snapshot"])
            if key in latest:
                problems.append(f"{line['snapshot']}: recorded more than once in {_LOG} (last one counts)")
            latest[key] = line
    sources = OrderedDict()
    footers = {}
    for (source, snap), line in latest.items():
        stats = sources.setdefault(source, {"snapshots": 0, "rows": 0, "bytes": 0, "alarmed": {}})
        path = os.path.join(out_dir, source, f"{snap}.parquet")
        if not os.path.isfile(path):
            problems.append(f"{snap}: parquet file missing ({path})")
            continue
        meta = pq.read_metadata(path)
        if meta.num_rows != line["rows"]:
            problems.append(f"{snap}: rows {line['rows']} in {_LOG} but {meta.num_rows} in the parquet footer")
        footers.setdefault(source, {})[snap] = json.loads((meta.metadata or {}).get(
            (_PREFIX + "windows").encode(), b"{}"))
        stats["snapshots"] += 1
        stats["rows"] += meta.num_rows
        stats["bytes"] += os.path.getsize(path)
        for name in line["alarms"]:
            stats["alarmed"].setdefault(name, []).append(snap)
    for source in sources:
        listed = {f"{s}.parquet" for (src, s) in latest if src == source}
        folder = os.path.join(out_dir, source)
        for name in sorted(os.listdir(folder)) if os.path.isdir(folder) else ():
            if name.endswith(".parquet") and not name.startswith(".") and name not in listed:
                problems.append(f"{source}/{name}: parquet file not in {_LOG}")
        for group, first, second, (lo, hi) in _overlap_pairs(footers.get(source, {})):
            problems.append(f"{source}: {group} overlap between {first} and {second} on {lo}..{hi}")
    if root is not None:
        for source in sources:
            half = raw_only_snapshots(root, source, stream)
            if half:
                print(f"WARN raw_only ({source}): {len(half)} committed raw snapshot(s) without observations: "
                      f"{half}", file=out)
    for source, s in sources.items():
        per_row = s["bytes"] / s["rows"] if s["rows"] else 0.0
        total = sum(len(v) for v in s["alarmed"].values())
        print(f"{source}: snapshots {s['snapshots']}  rows {s['rows']}  bytes {s['bytes']}  "
              f"bytes/row {per_row:.1f}  alarms {total}", file=out)
        for name, snaps in sorted(s["alarmed"].items()):
            print(f"    {name}: {len(snaps)} snapshot(s), latest {snaps[-1]}", file=out)
    for problem in problems:
        print(f"PROBLEM {problem}", file=out)
    print("verify: OK" if not problems else f"verify: {len(problems)} problem(s)", file=out)
    return EXIT_OK if not problems else EXIT_ERROR


# -- CLI ------------------------------------------------------------------------


def _parser():
    parser = argparse.ArgumentParser(prog="python -m dskit.onboarding.libs.parquet_export")
    parser.add_argument("--export-class", required=True, help="pkg.module:Class (a SnapshotParquetExport)")
    parser.add_argument("--root", required=True, help="the onboarding root (read only)")
    parser.add_argument("--source", required=True)
    parser.add_argument("--stream", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--max-snapshots", type=int, default=None)
    parser.add_argument("--params", default=None, help="JSON file overriding the declared thresholds")
    return parser


def _verify_parser():
    parser = argparse.ArgumentParser(prog="python -m dskit.onboarding.libs.parquet_export verify")
    parser.add_argument("--out", required=True)
    parser.add_argument("--source", default=None)
    parser.add_argument("--root", default=None, help="also list committed raw snapshots lacking observations")
    parser.add_argument("--stream", default=None, help="the stream --root is checked for")
    return parser


def main(argv=None) -> int:
    """CLI entry; returns the exit code (0 clean, 3 alarm, 1 error)."""
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        if argv and argv[0] == "verify":
            args = _verify_parser().parse_args(argv[1:])
            if args.root and not args.stream:
                raise AssetError(["verify --root needs --stream"])
            return verify_tree(args.out, args.source, root=args.root, stream=args.stream)
        args = _parser().parse_args(argv)
        params = None
        if args.params:
            with open(args.params, encoding="utf-8") as fh:
                params = json.load(fh)
        cls = _resolve_export(args.export_class)
        pa = _pyarrow()
        pa.set_cpu_count(2)
        pa.set_io_thread_count(2)
        return cls(args.root, args.source, args.stream, args.out, params).run(args.max_snapshots)
    except AssetError as exc:
        print("error: " + "; ".join(exc.errors).replace("\n", " "), file=sys.stderr)
        return EXIT_ERROR
    except (OSError, ValueError) as exc:
        print(f"error: {type(exc).__name__}: {exc}".replace("\n", " "), file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":
    # Run the importable module's main: as ``__main__`` this file would define a second
    # SnapshotParquetExport that a subclass imported by path does not inherit from.
    from dskit.onboarding.libs.parquet_export import main as _main

    sys.exit(_main())
