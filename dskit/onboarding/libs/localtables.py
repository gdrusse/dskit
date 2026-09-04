"""``localtables`` — parquet / newline-JSON table files in a directory (ADR-0076).

A corpus that already lives on disk as tables — per-series parquet
stores, newline-JSON ledgers, gzip-member archives — becomes an
onboarding source with no code: importing it through ``acquire`` is
what gives it WORM snapshots, bitemporal dedup and the fingerprintable
read seam (``scan_stream``). ``localfiles`` stays CSV/JSONL and stdlib
by design (it is the conformance reference); this pack is where pyarrow
enters, inside the verbs, and only when a parquet shard is in scope.
Install it through the existing ``parquet`` extra (``pip install
"dskit[parquet]"``) — one library, one extra.

Two layouts, declared. ``"directory"``: every immediate SUBDIRECTORY of
``path`` is a stream and each table file in it a SHARD of that stream —
the per-series layout, where ``markets/<SERIES>.parquet`` is one stream
``markets`` and ``stamp_stem_as`` writes each shard's stem (the series
the file implies) onto its rows, refusing a row that already carries a
different value. ``"file"``: every table file directly under ``path`` is
a stream named by its stem. Stream names must satisfy the platform's
segment rule to be acquirable; the directory layout plus a stamp is how
per-series files whose stems do not (an upper-case series code, say)
enter as one stream.

Formats by extension — ``parquet``, ``ndjson``, ``ndjson.gz``, ``jsonl``,
``jsonl.gz`` (``formats`` narrows the set; other files are ignored). The
gzip forms may be CONCATENATED members, one per append, and are read
whole. Newline-JSON rows must be objects; a ``NaN`` token reads as null
(the missing marker), an ``Infinity`` refuses — JSON has no spelling for
it. Parquet rows are normalized to JSON values one record batch at a
time: timestamps, dates and times become their own ISO spelling (a
naive timestamp stays naive — ``parse_utc`` reads it as UTC), NaN
becomes null, lists and structs recurse, bytes and decimals refuse —
cast them in the source.

The row's instant: ``effective_field`` names it and ``effective_unit``
says how to read it — ``iso`` (an ISO-8601 string, naive = UTC), ``ms``
(integer epoch milliseconds) or ``s`` (integer epoch seconds), the epoch
units converted in exact integer arithmetic. ``effective_date`` on every
RECORD is the normalized UTC spelling — ``YYYY-MM-DDTHH:MM:SS+00:00``,
with ``.mmm`` when the source carries milliseconds and ``.ffffff`` when
it carries microseconds — while ``data`` keeps the ORIGINAL value.
Cursor semantics are ``localfiles``' exactly: state maps stream ->
``{"cursor": <max effective_date emitted>}``; a pull emits rows strictly
after it, sorted by ``(instant, shard stem, row)`` so the order is stable
across shards, and checkpoints once per stream — identical in both
modes (the platform keys cursors per mode, ADR-0014). Every RECORD is
``kind="observation"``.

Config knobs (default-deny, per ``spec()``):

- ``path`` (required) — the directory.
- ``layout`` (required) — ``"directory"`` or ``"file"``, as above.
- ``effective_field`` (required) — the row field holding the instant.
- ``effective_unit`` — ``iso`` | ``ms`` | ``s``; default iso.
- ``stamp_stem_as`` — field to write the shard's stem onto each row.
- ``streams`` — restrict discover/read to these stream names.
- ``formats`` — the accepted extensions; default all five.
- ``encoding`` — text encoding of the newline-JSON formats, a codec name
  Python knows; default utf-8.

Refusals name ``path:n`` — the line for newline-JSON, the row ordinal
for parquet — and, for a value JSON cannot carry, the field.

Import cost: stdlib. pyarrow is imported inside the verbs.
"""

from __future__ import annotations

import codecs
import gzip
import json
import math
import os
import zlib
from datetime import date, datetime, time, timedelta, timezone

from ..base import AssetError, _check_dict, _raise_if, parse_utc
from ..connector import PROTOCOL, Connector

__all__ = ["EFFECTIVE_UNITS", "FORMATS", "LAYOUTS", "LocalTablesConnector"]

_DIRECTORY_LAYOUT = "directory"

#: The closed layout vocabulary: subdirectories-as-streams, or files-as-streams.
LAYOUTS = (_DIRECTORY_LAYOUT, "file")

_PARQUET = "parquet"

#: Accepted file extensions (without the dot). Anything else is not a table.
FORMATS = (_PARQUET, "ndjson", "ndjson.gz", "jsonl", "jsonl.gz")

_ISO_UNIT = "iso"

#: Epoch units and their ticks per second — the exact-arithmetic table.
_TICKS_PER_SECOND = {"ms": 1000, "s": 1}

#: The closed ``effective_unit`` vocabulary.
EFFECTIVE_UNITS = (_ISO_UNIT,) + tuple(_TICKS_PER_SECOND)

#: Default ``config.effective_unit`` — ONE name, read by spec() and the code.
_DEFAULT_EFFECTIVE_UNIT = _ISO_UNIT

#: Default ``config.encoding`` — shared by discover() and read() so the
#: fallback can only ever drift once, not twice.
_DEFAULT_ENCODING = "utf-8"

_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)

#: The decode-side failure family a text or gzip shard can raise.
#: ``gzip.BadGzipFile`` subclasses ``OSError``; ``UnicodeDecodeError``
#: subclasses ``ValueError`` and is named separately on purpose.
_DECODE_ERRORS = (OSError, EOFError, zlib.error, UnicodeDecodeError)


class _NoJsonSpelling(Exception):
    """A cell value JSON cannot carry; the reader names the row and field."""


def _pyarrow():
    """Import pyarrow (with its parquet submodule) inside a verb; refuse loudly when absent."""
    try:
        import pyarrow
        import pyarrow.parquet  # noqa: F401 — binds the submodule attribute
    except Exception as exc:
        raise AssetError(
            [f"localtables needs pyarrow for parquet files (pip install "
             f"'dskit[parquet]'): {exc}"]
        ) from exc
    return pyarrow


def _json_constant(name):
    """``NaN`` reads as null (the missing marker); ``Infinity`` is not JSON."""
    if name == "NaN":
        return None
    raise ValueError(f"{name} is not JSON")


def _jsonable(value):
    """Convert a parquet cell to a JSON value; raise _NoJsonSpelling for what JSON cannot carry."""
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        if math.isnan(value):
            return None
        if math.isinf(value):
            raise _NoJsonSpelling(f"{value!r} has no JSON spelling")
        return value
    if isinstance(value, (date, time)):  # datetime is a date
        return value.isoformat()
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if hasattr(value, "as_py"):  # a pyarrow scalar
        return _jsonable(value.as_py())
    if hasattr(value, "tolist"):  # a numpy scalar or array
        return _jsonable(value.tolist())
    raise _NoJsonSpelling(
        f"{type(value).__name__} has no JSON spelling — cast it in the source"
    )


def _json_row(row):
    """Convert a parquet row to JSON values; _NoJsonSpelling names the offending field."""
    out = {}
    for field, value in row.items():
        try:
            out[field] = _jsonable(value)
        except _NoJsonSpelling as exc:
            raise _NoJsonSpelling(f"field {field!r}: {exc}") from None
    return out


def _instant(value, unit):
    """Resolve a row's effective value under ``unit`` to an aware UTC datetime."""
    if unit == _ISO_UNIT:
        if not isinstance(value, str) or not value:
            raise AssetError(
                [f"must be a non-empty ISO-8601 string under effective_unit "
                 f"{unit!r}, got {value!r}"]
            )
        return parse_utc(value)
    integral = isinstance(value, int) or (
        isinstance(value, float) and math.isfinite(value) and value.is_integer()
    )
    if isinstance(value, bool) or not integral:
        raise AssetError(
            [f"must be an integer epoch count under effective_unit {unit!r}, "
             f"got {value!r}"]
        )
    per_second = _TICKS_PER_SECOND[unit]
    seconds, ticks = divmod(int(value), per_second)
    try:
        return _EPOCH + timedelta(
            seconds=seconds, microseconds=ticks * (1_000_000 // per_second)
        )
    except OverflowError as exc:
        raise AssetError(
            [f"epoch value {value!r} ({unit}) is outside the datetime range: {exc}"]
        ) from exc


def _iso(dt):
    """Spell an aware UTC datetime: seconds, or .mmm / .ffffff only when carried."""
    if dt.microsecond and dt.microsecond % 1000 == 0:
        return dt.isoformat(timespec="milliseconds")
    return dt.isoformat()


def _split_name(fname, formats):
    """``(stem, format)`` for a recognized table filename, else None — longest extension first."""
    if fname.startswith("."):
        return None
    for fmt in formats:
        ext = "." + fmt
        if fname.endswith(ext) and len(fname) > len(ext):
            return fname[: -len(ext)], fmt
    return None


def _is_name_list(value):
    """Tell whether ``value`` is a non-empty list of non-empty strings."""
    return (isinstance(value, list) and bool(value)
            and all(isinstance(v, str) and v for v in value))


class LocalTablesConnector(Connector):
    """Parquet / newline-JSON table files under one directory, as streams.

    Notes
    -----
    Stateless like every connector: every knob arrives in ``config``
    (see :meth:`spec` and the module docstring), every output leaves as
    an envelope message. ``check`` lists the directory and imports
    pyarrow only if a parquet shard is in scope; ``discover`` reads one
    schema (parquet) or one line (newline-JSON) per stream; ``read``
    holds one stream's post-cursor rows at a time, one shard file at a
    time.

    Examples
    --------
    Import per-series parquet files as one ``markets`` stream, each row
    stamped with its series::

        conn = LocalTablesConnector()
        config = {"path": "~/data/kalshi", "layout": "directory",
                  "effective_field": "ts", "stamp_stem_as": "series"}
        conn.check(config)
        [s["stream"] for s in conn.discover(config)]
        # -> ['markets']
        msgs = list(conn.read(config, ["markets"], {}, "backfill"))
        (msgs[0]["type"], msgs[-1]["type"])
        # -> ('SCHEMA', 'STATE')
    """

    def spec(self) -> dict:
        """Declare the config knobs, default-deny.

        Returns
        -------
        dict
            ``{"params": {<knob>: {"required", "notes"}}}`` — the module
            docstring explains each knob's semantics.
        """
        return {
            "params": {
                "path": {
                    "required": True,
                    "notes": "Directory holding the table files (layout "
                             "'file') or the per-stream subdirectories "
                             "(layout 'directory').",
                },
                "layout": {
                    "required": True,
                    "notes": f"One of {list(LAYOUTS)}: 'directory' = each "
                             "subdirectory is a stream and its files are "
                             "shards; 'file' = each file is a stream named "
                             "by its stem.",
                },
                "effective_field": {
                    "required": True,
                    "notes": "Row field holding the row's instant.",
                },
                "effective_unit": {
                    "notes": f"How to read it — one of {list(EFFECTIVE_UNITS)}: "
                             "ISO-8601 string (naive = UTC), integer epoch "
                             "milliseconds, or integer epoch seconds; "
                             f"default {_DEFAULT_EFFECTIVE_UNIT}.",
                },
                "stamp_stem_as": {
                    "notes": "Field written onto every row with the shard's "
                             "file stem (the series a per-series file "
                             "implies); a row already carrying a different "
                             "value refuses.",
                },
                "streams": {
                    "notes": "Restrict discover/read to these stream names; "
                             "a name with no files refuses.",
                },
                "formats": {
                    "notes": f"Accepted extensions, a subset of {list(FORMATS)}; "
                             "default all. Other files are ignored.",
                },
                "encoding": {
                    "notes": "Text encoding of the newline-JSON formats — a "
                             "codec name Python knows; "
                             f"default {_DEFAULT_ENCODING}.",
                },
            },
        }

    # -- knobs and files -----------------------------------------------------

    def _knobs(self, config):
        """Every knob shape-checked and defaulted ONCE; the normalized dict."""
        errors = []
        path = config.get("path")
        if not isinstance(path, str) or not path:
            errors.append(f"config.path must be a non-empty string, got {path!r}")
        layout = config.get("layout")
        if layout not in LAYOUTS:
            errors.append(f"config.layout must be one of {list(LAYOUTS)}, got {layout!r}")
        field = config.get("effective_field")
        if not isinstance(field, str) or not field:
            errors.append(
                f"config.effective_field must be a non-empty string, got {field!r}"
            )
        unit = config.get("effective_unit", _DEFAULT_EFFECTIVE_UNIT)
        if unit not in EFFECTIVE_UNITS:
            errors.append(
                f"config.effective_unit must be one of {list(EFFECTIVE_UNITS)}, "
                f"got {unit!r}"
            )
        stamp = config.get("stamp_stem_as")
        if stamp is not None and (not isinstance(stamp, str) or not stamp):
            errors.append(
                f"config.stamp_stem_as must be a non-empty string, got {stamp!r}"
            )
        streams = config.get("streams")
        if streams is not None and not _is_name_list(streams):
            errors.append(
                f"config.streams must be a non-empty list of stream names, "
                f"got {streams!r}"
            )
        formats = config.get("formats", list(FORMATS))
        if not _is_name_list(formats) or any(f not in FORMATS for f in formats):
            errors.append(
                f"config.formats must be a non-empty subset of {list(FORMATS)}, "
                f"got {formats!r}"
            )
        encoding = config.get("encoding", _DEFAULT_ENCODING)
        if not isinstance(encoding, str) or not encoding:
            errors.append(
                f"config.encoding must be a non-empty string, got {encoding!r}"
            )
        else:
            # A codec Python cannot find surfaces from open() as a raw
            # LookupError mid-read; a typo'd knob is check()'s to refuse.
            try:
                codecs.lookup(encoding)
            except LookupError:
                errors.append(
                    f"config.encoding {encoding!r} is not a text encoding "
                    "Python knows"
                )
        _raise_if(errors)
        return {
            "path": os.path.abspath(os.path.expanduser(path)),
            "layout": layout,
            "field": field,
            "unit": unit,
            "stamp": stamp,
            "streams": streams,
            # Longest extension first, so a compound one is never split.
            "formats": tuple(sorted(set(formats), key=len, reverse=True)),
            "encoding": encoding,
        }

    def _table_files(self, dirpath, formats):
        """``[(stem, path, fmt)]`` for every table file directly in ``dirpath``, by stem."""
        try:
            names = sorted(os.listdir(dirpath))
        except OSError as exc:
            raise AssetError([f"cannot list {dirpath}: {exc}"]) from exc
        out = {}
        for fname in names:
            split = _split_name(fname, formats)
            full = os.path.join(dirpath, fname)
            if split is None or not os.path.isfile(full):
                continue
            stem, fmt = split
            if stem in out:
                raise AssetError(
                    [f"{dirpath}: stem {stem!r} appears as both .{out[stem][2]} "
                     f"and .{fmt} — one file per stem"]
                )
            out[stem] = (stem, full, fmt)
        return [out[stem] for stem in sorted(out)]

    def _shards(self, knobs):
        """Map stream -> ``[(stem, path, fmt)]`` for every stream in scope."""
        directory = knobs["path"]
        if not os.path.isdir(directory):
            raise AssetError([f"config.path is not a directory: {directory!r}"])
        out = {}
        if knobs["layout"] == _DIRECTORY_LAYOUT:
            try:
                names = sorted(os.listdir(directory))
            except OSError as exc:
                raise AssetError([f"cannot list {directory}: {exc}"]) from exc
            for name in names:
                sub = os.path.join(directory, name)
                if name.startswith(".") or not os.path.isdir(sub):
                    continue
                files = self._table_files(sub, knobs["formats"])
                if files:  # a subdirectory with no table files is not a stream
                    out[name] = files
        else:
            for entry in self._table_files(directory, knobs["formats"]):
                out[entry[0]] = [entry]
        wanted = knobs["streams"]
        if wanted is not None:
            unknown = sorted(set(wanted) - set(out))
            if unknown:
                raise AssetError(
                    [f"config.streams names unknown stream(s) {unknown} — "
                     f"discovered: {sorted(out)}"]
                )
            out = {stream: out[stream] for stream in sorted(set(wanted))}
        return out

    # -- readers, one per format family -------------------------------------

    def _json_rows(self, path, fmt, encoding):
        """Yield ``(line, object)`` from a newline-JSON file, gzip members included."""
        opener = gzip.open if fmt.endswith(".gz") else open
        try:
            fh = opener(path, "rt", encoding=encoding)
        except _DECODE_ERRORS as exc:
            raise AssetError([f"cannot open {path}: {exc}"]) from exc
        with fh:
            n = 0
            while True:
                try:
                    line = fh.readline()
                except _DECODE_ERRORS as exc:
                    raise AssetError(
                        [f"corrupt or unreadable text at {path}:{n + 1}: {exc}"]
                    ) from exc
                if not line:
                    return
                n += 1
                if not line.strip():
                    continue
                try:
                    obj = json.loads(line, parse_constant=_json_constant)
                except ValueError as exc:
                    raise AssetError([f"{path}:{n} is not valid JSON: {exc}"]) from exc
                if not isinstance(obj, dict):
                    raise AssetError([f"{path}:{n} must be a JSON object"])
                yield n, obj

    def _parquet_rows(self, path):
        """Yield ``(row, JSON row)`` from a parquet file, one record batch at a time."""
        pa = _pyarrow()
        try:
            reader = pa.parquet.ParquetFile(path)
        except (OSError, pa.ArrowException) as exc:
            raise AssetError([f"cannot read parquet {path}: {exc}"]) from exc
        n = 0
        with reader:
            try:
                for batch in reader.iter_batches():
                    for row in batch.to_pylist():
                        n += 1
                        try:
                            data = _json_row(row)
                        except _NoJsonSpelling as exc:
                            raise AssetError([f"{path}:{n} {exc}"]) from exc
                        yield n, data
            except (OSError, pa.ArrowException) as exc:
                raise AssetError(
                    [f"corrupt or unreadable parquet at {path} after row {n}: {exc}"]
                ) from exc

    def _parquet_fields(self, path):
        """Read a parquet file's column names from its footer — no rows read."""
        pa = _pyarrow()
        try:
            return list(pa.parquet.read_schema(path).names)
        except (OSError, pa.ArrowException) as exc:
            raise AssetError([f"cannot read parquet {path}: {exc}"]) from exc

    def _rows(self, path, fmt, encoding):
        """Yield ``(n, JSON row)`` from one shard, by its format."""
        if fmt == _PARQUET:
            yield from self._parquet_rows(path)
        else:
            yield from self._json_rows(path, fmt, encoding)

    def _fields(self, files, knobs):
        """Sorted field names of a stream's first shard, plus the stamp field."""
        _stem, path, fmt = files[0]
        if fmt == _PARQUET:
            names = self._parquet_fields(path)
        else:
            names = []
            for _n, row in self._json_rows(path, fmt, knobs["encoding"]):
                names = list(row)
                break
        fields = set(names)
        if knobs["stamp"] is not None:
            fields.add(knobs["stamp"])
        return sorted(fields)

    def _pending_rows(self, files, knobs, cursor_dt):
        """Collect a stream's rows strictly after the cursor, stamped, sorted ``(instant, stem, row)``."""
        field, unit, stamp = knobs["field"], knobs["unit"], knobs["stamp"]
        rows = []
        for stem, path, fmt in files:  # one shard file open at a time
            for n, row in self._rows(path, fmt, knobs["encoding"]):
                try:
                    instant = _instant(row.get(field), unit)
                except AssetError as exc:
                    raise AssetError(
                        [f"{path}:{n}: field {field!r}: {e}" for e in exc.errors]
                    ) from exc
                if cursor_dt is not None and instant <= cursor_dt:
                    continue  # already durable per the checkpoint
                if stamp is not None:
                    held = row.get(stamp, stem)
                    if held != stem:
                        raise AssetError(
                            [f"{path}:{n}: field {stamp!r} already holds {held!r}, "
                             f"not the shard stem {stem!r} — stamp_stem_as never "
                             "rewrites a row"]
                        )
                    row[stamp] = stem
                rows.append((instant, stem, n, row))
        rows.sort(key=lambda t: t[:3])
        return rows

    # -- the four verbs ----------------------------------------------------

    def check(self, config) -> None:
        """Fail fast: knobs well-formed, at least one stream with a file, pyarrow present if needed.

        Parameters
        ----------
        config : dict
            Knobs already validated by ``check_config``.

        Raises
        ------
        AssetError
            Every knob problem at once; then the first structural one —
            a missing directory, an empty source, a ``streams`` name
            with no files, a stem present twice, pyarrow absent while a
            parquet shard is in scope.
        """
        knobs = self._knobs(config)
        shards = self._shards(knobs)
        if not shards:
            raise AssetError(
                [f"no table files ({list(knobs['formats'])}) under "
                 f"{knobs['path']!r} in layout {knobs['layout']!r}"]
            )
        if any(fmt == _PARQUET for files in shards.values() for _s, _p, fmt in files):
            _pyarrow()

    def discover(self, config) -> list:
        """Name the streams: one per subdirectory or file, schema from the first shard.

        Parameters
        ----------
        config : dict
            Knobs already validated by ``check_config``.

        Returns
        -------
        list of dict
            ``{"stream", "schema": {"fields": sorted names}, "primary_key":
            []}`` per stream, in stream-name order; the stamp field is
            among the names when declared.

        Raises
        ------
        AssetError
            As :meth:`check`, plus an unreadable first shard.
        """
        knobs = self._knobs(config)
        return [
            {"stream": stream, "schema": {"fields": self._fields(files, knobs)},
             "primary_key": []}
            for stream, files in sorted(self._shards(knobs).items())
        ]

    def read(self, config, streams, state, mode):
        """Yield SCHEMA, cursor-filtered RECORDs, then one STATE — per stream.

        Parameters
        ----------
        config : dict
            Knobs already validated by ``check_config`` (``storage``
            stripped by the platform).
        streams : list of str
            Streams to pull; each must be one :meth:`discover` names.
        state : dict
            ``{stream: {"cursor": <ISO>}}`` from the last STATE, or ``{}``
            on a first pull.
        mode : str
            Unused — the platform keys cursors per mode (ADR-0014), so
            the pull is identical under both.

        Yields
        ------
        dict
            Envelope messages. Every RECORD is ``kind="observation"``,
            its ``effective_date`` the normalized UTC spelling and its
            ``data`` the original row as JSON values, plus the stamp.

        Raises
        ------
        AssetError
            An unknown stream; a row whose instant is missing or
            unreadable, a value JSON cannot carry, a stamp conflict, or
            an unreadable file — each naming ``path:n``.
        """
        errors = []
        _check_dict(errors, "state", state)
        if not isinstance(streams, list) or not streams:
            errors.append(f"streams must be a non-empty list, got {streams!r}")
        _raise_if(errors)
        knobs = self._knobs(config)
        shards = self._shards(knobs)
        new_state = {k: dict(v) for k, v in state.items()}

        for stream in streams:
            files = shards.get(stream)
            if files is None:
                raise AssetError(
                    [f"unknown stream {stream!r} — discovered: {sorted(shards)}"]
                )
            cursor = state.get(stream, {}).get("cursor", "")
            cursor_dt = parse_utc(cursor) if cursor else None
            yield {"protocol": PROTOCOL, "type": "SCHEMA", "stream": stream,
                   "schema": {"fields": self._fields(files, knobs)}}

            emitted_max = cursor
            for instant, _stem, _n, data in self._pending_rows(files, knobs, cursor_dt):
                eff = _iso(instant)
                yield {"protocol": PROTOCOL, "type": "RECORD", "stream": stream,
                       "effective_date": eff, "kind": "observation", "data": data}
                emitted_max = eff
            new_state.setdefault(stream, {})["cursor"] = emitted_max

        yield {"protocol": PROTOCOL, "type": "STATE", "state": new_state}
