"""``zipcsv`` — a vendor's zip of one CSV, reshaped to typed parquet under a layout from config.

Vendors publish history as one zip per day or per symbol, each holding one CSV.
:class:`ZipCsvToParquet` is the ``httpblobs`` ``transform`` hook (ADR-0233,
ADR-0242) for that shape: ``Class(transform_params, as_of)`` is built once and
called ``transform(entity, body)`` -> parquet bytes, with ``note(entity, body)``
feeding the inventory row. Nothing about a vendor lives in the code: the column
layout is the ``transform_params`` JSON block of the source config, so a new
vendor is a new config, never a new class. Name it by import path in the
source's ``transform`` (``dskit.onboarding.libs.zipcsv:ZipCsvToParquet``);
nothing registers it.

What it does to every file, so the parquet a reader gets has one shape:

- **One member, whole.** The zip holds exactly one ``.csv``; its CRC is checked
  by reading it. UTF-8, a leading byte-order mark stripped (so it cannot turn a
  data row into a "header").
- **Header handling** (``header``): ``sniff`` (the default) treats a first row
  whose first cell is not a number as a header, ``required`` demands one,
  ``absent`` never skips a row. A header row must carry the declared vendor
  names (cells stripped), else the file refuses; a vendor renaming a column is
  loud, not a silent shift.
- **One epoch unit.** A ``ts`` column is written in epoch MILLISECONDS. Its
  ``unit`` is ``s``, ``ms``, ``us`` or ``ns`` for a fixed scale, or ``auto``
  (the default) for a feed that changed scale mid-history: a value at or above
  :data:`MICROS_FLOOR` is microseconds (millisecond epochs reach it only in year
  5138), anything below is milliseconds. A converted value is floored. An
  instant outside 1970..9999 refuses, which also catches a wrong ``unit``.
- **Typed columns, vendor values.** ``float`` (finite only), ``int`` (64-bit),
  ``text``, ``ts``; a column with ``"output": null`` is dropped. Nothing else is
  derived, filled or resampled, and row order is never enforced.
- **Row instants.** One kept ``ts`` column (``instant``, default the first) is
  the row's instant: ``unique_instants`` refuses a repeat, otherwise repeats
  are kept and :meth:`ZipCsvToParquet.note` flags them, as it flags a file out
  of order.

A file that cannot be reshaped (not a zip, a bad CRC, not UTF-8, no rows, a
wrong column count, an unparseable value, a header that is not the declared
one, a repeated instant where they must be unique) raises ``ValueError`` naming
the line, which ``httpblobs`` records as one refused entity and moves on. A
layout that cannot be built raises :class:`~dskit.onboarding.base.AssetError`
(a ``ValueError``) listing every problem, when the source config is checked.

Two things to know when writing the block. The block is part of the source's
declaration digest, so editing it re-pulls every file, and so it takes no
``notes`` key (a comment would move the digest): put the commentary in the
source config's own ``notes``. And the parquet bytes are deterministic for the
same input only within one pyarrow version (the writer stamps its version into
the file), so a re-pull under another pyarrow can change a stored file's sha256.

Limits, stated so nobody assumes more: comma-delimited CSV only; integer epochs
(no fractional seconds); the whole member is held in memory (``max_member_bytes``
bounds it); the stored epoch unit is always milliseconds.

Import cost: stdlib; ``pyarrow`` only inside :meth:`ZipCsvToParquet.transform`.
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import math
import zipfile
from abc import ABC, abstractmethod

from ..base import AssetError

__all__ = [
    "DEFAULT_HEADER",
    "DEFAULT_UNIQUE_INSTANTS",
    "DEFAULT_UNIT",
    "HEADER_MODES",
    "KINDS",
    "MICROS_FLOOR",
    "UNITS",
    "ZipCsvToParquet",
]

#: Epoch integers at or above this are microseconds under the ``auto`` unit: a
#: millisecond epoch reaches it only in year 5138, a microsecond one passed it in 1973.
MICROS_FLOOR = 10**14

_AUTO = "auto"
DEFAULT_UNIT = _AUTO
DEFAULT_HEADER = "sniff"
DEFAULT_UNIQUE_INSTANTS = False

#: unit -> (multiplier, divisor) taking an integer in that unit to epoch milliseconds, floored.
_UNIT_TO_MS = {"s": (1000, 1), "ms": (1, 1), "us": (1, 1000), "ns": (1, 10**6)}
#: The ``unit`` values a ``ts`` column accepts.
UNITS = (_AUTO, *_UNIT_TO_MS)

_UTC = dt.timezone.utc
#: The last millisecond the ISO note can print (9999-12-31T23:59:59.999Z). Built from whole seconds: ``datetime.max``
#: carries 999999 microseconds, which a float timestamp rounds up to the NEXT second.
_MAX_EPOCH_MS = int(dt.datetime(9999, 12, 31, 23, 59, 59, tzinfo=_UTC).timestamp()) * 1000 + 999
_INT64_LIMIT = 1 << 63
_CORRUPT = "not a zip archive of UTF-8 CSV, or corrupt: {}"
_NOTES_REASON = ("; notes is not accepted here (these params feed the source's declaration "
                 "digest, so a comment would re-pull every file): put it in the source "
                 "config's own notes")


def _is_number(text):
    """Report whether a CSV cell parses as a number."""
    try:
        float(text)
    except ValueError:
        return False
    return True


def _iso(ms):
    """Return epoch ms as ``YYYY-MM-DDTHH:MM:SSZ`` (whole seconds)."""
    return dt.datetime.fromtimestamp(ms // 1000, tz=_UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class _Kind(ABC):
    """One value kind of the closed vocabulary: how a cell parses and what it is stored as."""

    #: Extra column keys this kind accepts beyond ``vendor``, ``output`` and ``kind``.
    OPTIONS = ()
    #: True when a first cell of this kind parses as a number (a header can be sniffed on it).
    NUMERIC = True
    #: True when a column of this kind can be the row instant.
    INSTANT = False

    def __init__(self, spec):
        self.spec = spec

    @property
    @abstractmethod
    def arrow_type(self):
        """The name of the ``pyarrow`` factory for the stored type."""

    @classmethod
    def problems(cls, spec, where):
        """Return the problems with this kind's own options in a column spec."""
        return []

    @abstractmethod
    def parse(self, text):
        """Return one cell as a Python value, raising ``ValueError`` when it is not one."""


class _Ts(_Kind):
    """An integer epoch instant, normalised to epoch milliseconds."""

    OPTIONS = ("unit",)
    INSTANT = True
    arrow_type = "int64"

    def __init__(self, spec):
        super().__init__(spec)
        self._unit = spec.get("unit", DEFAULT_UNIT)

    @classmethod
    def problems(cls, spec, where):
        """Return the problem with the column's ``unit``, if any."""
        unit = spec.get("unit", DEFAULT_UNIT)
        return [] if unit in UNITS else [f"{where}.unit must be one of {list(UNITS)}, got {unit!r}"]

    def parse(self, text):
        """Return the cell's epoch in milliseconds, refusing an instant outside 1970..9999."""
        value = int(text)
        multiplier, divisor = _UNIT_TO_MS[self._unit_of(value)]
        ms = value * multiplier // divisor
        if not 0 <= ms <= _MAX_EPOCH_MS:
            raise ValueError(f"epoch {ms} ms is out of range (1970-01-01 to 9999-12-31); "
                             "check the column's unit")
        return ms

    def _unit_of(self, value):
        """Return the unit to read ``value`` in: the declared one, or the ms/us call for ``auto``."""
        if self._unit != _AUTO:
            return self._unit
        return "us" if value >= MICROS_FLOOR else "ms"


class _Float(_Kind):
    """A finite float64."""

    arrow_type = "float64"

    def parse(self, text):
        """Return the cell as a float, refusing nan and infinity."""
        value = float(text)
        if not math.isfinite(value):
            raise ValueError(f"not a finite number: {text!r}")
        return value


class _Int(_Kind):
    """A 64-bit integer."""

    arrow_type = "int64"

    def parse(self, text):
        """Return the cell as an int that fits 64 bits."""
        value = int(text)
        if not -_INT64_LIMIT <= value < _INT64_LIMIT:
            raise ValueError(f"{value} does not fit a 64-bit integer")
        return value


class _Text(_Kind):
    """A string, as published."""

    NUMERIC = False
    arrow_type = "string"

    def parse(self, text):
        """Return the cell unchanged."""
        return text


_KINDS = {"ts": _Ts, "float": _Float, "int": _Int, "text": _Text}
#: The ``kind`` values a column accepts.
KINDS = tuple(_KINDS)


class _HeaderRule(ABC):
    """How the first non-empty row is told to be a header or data."""

    @classmethod
    def problems(cls, first):
        """Return the problems this rule has with a layout whose first column is ``first``."""
        return []

    @abstractmethod
    def is_header(self, row):
        """Report whether ``row``, the first non-empty one, is a header."""


class _SniffHeader(_HeaderRule):
    """A first row whose first cell is not a number is a header."""

    @classmethod
    def problems(cls, first):
        """Return the problem when the first column is not numeric, so nothing can be sniffed."""
        if first.kind.NUMERIC:
            return []
        return ["header 'sniff' needs a numeric first column (kind ts, int or float); "
                "set header to 'required' or 'absent'"]

    def is_header(self, row):
        """Report whether the first cell is not a number."""
        return not _is_number(row[0])


class _RequiredHeader(_HeaderRule):
    """The first row is always a header."""

    def is_header(self, row):
        """Report True: a file without one fails the name check."""
        return True


class _AbsentHeader(_HeaderRule):
    """The first row is data."""

    def is_header(self, row):
        """Report False: no row is skipped."""
        return False


_HEADER_RULES = {"sniff": _SniffHeader, "required": _RequiredHeader, "absent": _AbsentHeader}
#: The ``header`` values the layout accepts.
HEADER_MODES = tuple(_HEADER_RULES)


class _Column:
    """One declared CSV column: the vendor's name, the stored name (``None`` drops it), its kind."""

    def __init__(self, vendor, output, kind):
        self.vendor, self.output, self.kind = vendor, output, kind


class ZipCsvToParquet:
    """One vendor zip holding one CSV -> parquet bytes, the layout supplied by config.

    The ``httpblobs`` ``transform`` hook: build it from the source's
    ``transform_params`` and ``as_of``, then call :meth:`transform` per entity.

    Parameters
    ----------
    params : dict
        The layout, from the source config's ``transform_params`` (default-deny:
        any other key refuses, ``notes`` included).

        - ``columns`` (required) — non-empty list of ``{"vendor": str,
          "output": str or null, "kind": one of KINDS}`` in CSV file order. The
          vendor name is what a header row must say; a null ``output`` drops
          the column; output names are unique and give the parquet's column
          order. A ``ts`` column also takes ``"unit"`` (one of UNITS, default
          ``auto``).
        - ``header`` — one of HEADER_MODES, default ``sniff`` (which needs a
          numeric first column).
        - ``unique_instants`` — bool, default False: refuse a repeated row
          instant (else keep it and flag it in :meth:`note`).
        - ``instant`` — the output name of the ``ts`` column that is the row
          instant; default the first kept ``ts`` column.
        - ``max_member_bytes`` — optional int >= 1: refuse a zip member whose
          declared size is larger, before reading it. No default: unset is no cap.
    as_of : str
        The pull's declared ISO instant, handed over by ``httpblobs``; the files
        written here carry no stamp, so equal input bytes give equal output
        bytes under one pyarrow version.

    Raises
    ------
    AssetError
        Listing every problem when the layout cannot be built.

    Examples
    --------
    A source config names it as the transform and carries the layout::

        {"transform": "dskit.onboarding.libs.zipcsv:ZipCsvToParquet",
         "transform_params": {
             "columns": [
                 {"vendor": "t", "output": "t_ms", "kind": "ts"},
                 {"vendor": "px", "output": "price", "kind": "float"},
                 {"vendor": "junk", "output": null, "kind": "int"}],
             "unique_instants": true}}

    Direct use::

        layout = {"columns": [{"vendor": "t", "output": "t_ms", "kind": "ts"},
                              {"vendor": "px", "output": "price", "kind": "float"}]}
        node = ZipCsvToParquet(layout, "2026-10-06T00:00:00+00:00")
        node.note("2026-10-01", zip_bytes)
        # -> rows 3, 2026-10-01T00:00:00Z .. 2026-10-01T00:00:02Z
        parquet_bytes = node.transform("2026-10-01", zip_bytes)
    """

    _PARAMS = ("columns", "header", "unique_instants", "instant", "max_member_bytes")
    _COLUMN_KEYS = ("vendor", "output", "kind")

    def __init__(self, params, as_of):
        params = {} if params is None else params
        if not isinstance(params, dict):
            raise AssetError([f"params must be an object, got {type(params).__name__}"])
        problems = self._unknown(params, self._PARAMS, "unknown params")
        columns = self._build_columns(params.get("columns"), problems)
        self._header = self._build_header(params.get("header", DEFAULT_HEADER), columns, problems)
        self._unique = self._build_unique(params.get("unique_instants", DEFAULT_UNIQUE_INSTANTS),
                                          problems)
        self._cap = self._build_cap(params.get("max_member_bytes"), problems)
        self._instant = self._build_instant(params.get("instant"), columns, problems)
        if problems:
            raise AssetError(problems)
        self.as_of = as_of
        self._columns = columns
        self._kept = tuple(c for c in columns if c.output)
        self._memo = None

    # -- the layout -----------------------------------------------------------------

    @staticmethod
    def _unknown(given, allowed, label):
        """Return the problem lines for keys of ``given`` outside ``allowed``."""
        unknown = sorted(set(given) - set(allowed), key=str)
        if not unknown:
            return []
        reason = _NOTES_REASON if "notes" in unknown else ""
        return [f"{label} {unknown}; allowed {list(allowed)}{reason}"]

    @classmethod
    def _build_columns(cls, specs, problems):
        """Return the tuple of ``_Column``, or ``None`` after appending what is wrong."""
        if not isinstance(specs, list) or not specs:
            problems.append("columns must be a non-empty list of {vendor, output, kind} objects")
            return None
        before = len(problems)
        columns = [cls._build_column(spec, index, problems) for index, spec in enumerate(specs)]
        if len(problems) > before:
            return None
        cls._check_outputs(columns, problems)
        return tuple(columns)

    @classmethod
    def _build_column(cls, spec, index, problems):
        """Return one ``_Column``, or ``None`` after appending what is wrong with its spec."""
        where = f"columns[{index}]"
        if not isinstance(spec, dict):
            problems.append(f"{where} must be an object")
            return None
        before = len(problems)
        kind = spec.get("kind")
        kind_cls = _KINDS.get(kind) if isinstance(kind, str) else None
        allowed = (*cls._COLUMN_KEYS, *(kind_cls.OPTIONS if kind_cls else ()))
        problems.extend(cls._unknown(spec, allowed, f"{where}: unknown key(s)"))
        if not isinstance(spec.get("vendor"), str):
            problems.append(f"{where}.vendor must be a string (the name the vendor's header uses)")
        output = spec.get("output")
        if "output" not in spec or not (output is None or (isinstance(output, str) and output)):
            problems.append(f"{where}.output must be a non-empty string, or null to drop the column")
        if kind_cls is None:
            problems.append(f"{where}.kind must be one of {list(KINDS)}, got {kind!r}")
        else:
            problems.extend(kind_cls.problems(spec, where))
        if len(problems) > before:
            return None
        return _Column(spec["vendor"], output, kind_cls(spec))

    @staticmethod
    def _check_outputs(columns, problems):
        """Append a problem for a repeated output name, or for a layout that keeps nothing."""
        seen = {}
        for index, column in enumerate(columns):
            if column.output is None:
                continue
            if column.output in seen:
                problems.append(f"columns[{index}].output {column.output!r} repeats "
                                f"columns[{seen[column.output]}]")
            seen.setdefault(column.output, index)
        if not seen:
            problems.append("columns keep no column: every output is null")

    @staticmethod
    def _build_header(mode, columns, problems):
        """Return the header rule for ``mode``, or ``None`` after appending what is wrong."""
        rule = _HEADER_RULES.get(mode) if isinstance(mode, str) else None
        if rule is None:
            problems.append(f"header must be one of {list(HEADER_MODES)}, got {mode!r}")
            return None
        if columns is not None:
            problems.extend(rule.problems(columns[0]))
        return rule()

    @staticmethod
    def _build_unique(value, problems):
        """Return ``unique_instants`` as a bool, or ``None`` after appending what is wrong."""
        if not isinstance(value, bool):
            problems.append(f"unique_instants must be a bool, got {value!r}")
            return None
        return value

    @staticmethod
    def _build_cap(value, problems):
        """Return ``max_member_bytes`` (``None`` when unset), or append what is wrong."""
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            problems.append(f"max_member_bytes must be an integer >= 1, got {value!r}")
        return value

    @staticmethod
    def _build_instant(name, columns, problems):
        """Return the output name of the row-instant column, or ``None`` after appending why not."""
        if columns is None:
            return None
        choices = [c.output for c in columns if c.output and c.kind.INSTANT]
        if name is None and choices:
            return choices[0]
        if name is None:
            problems.append("no row instant: the layout keeps no ts column (add one, or fix "
                            "the kinds)")
        elif name in choices:
            return name
        else:
            problems.append(f"instant must name a kept ts column {choices}, got {name!r}")
        return None

    # -- one file ---------------------------------------------------------------------

    @staticmethod
    def _the_csv(archive):
        """Return the ``ZipInfo`` of the archive's one ``.csv`` member."""
        members = [info for info in archive.infolist() if info.filename.lower().endswith(".csv")]
        if not members:
            raise ValueError("no .csv member in the archive")
        if len(members) != 1:
            raise ValueError(f"exactly one .csv member expected, got {len(members)}")
        return members[0]

    def _check_size(self, info):
        """Refuse a member whose declared size is over ``max_member_bytes``."""
        if self._cap is not None and info.file_size > self._cap:
            raise ValueError(f"member {info.filename!r} is {info.file_size} bytes, over "
                             f"max_member_bytes {self._cap}")

    @staticmethod
    def _read(archive, info):
        """Return the member's bytes, every decompression failure becoming a ``ValueError``."""
        try:
            return archive.read(info)
        except Exception as exc:  # one type per codec (zlib, bz2, lzma), CRC and password
            raise ValueError(_CORRUPT.format(exc)) from exc

    def _csv_text(self, body):
        """Return the one CSV member's text, refusing a bad zip, a bad CRC, or a wrong member."""
        try:
            archive = zipfile.ZipFile(io.BytesIO(body))
        except zipfile.BadZipFile as exc:
            raise ValueError(_CORRUPT.format(exc)) from exc
        with archive:
            info = self._the_csv(archive)
            self._check_size(info)
            data = self._read(archive, info)
        try:
            return data.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise ValueError(_CORRUPT.format(exc)) from exc

    def _check_header(self, row, line):
        """Refuse a header row whose names are not the layout's vendor names."""
        names = [cell.strip() for cell in row]
        expected = [column.vendor.strip() for column in self._columns]
        if names != expected:
            raise ValueError(f"line {line}: unexpected header {names}; expected {expected}")

    def _append_row(self, columns, row, line):
        """Parse one data row onto the kept columns, refusing a wrong width or a bad value."""
        if len(row) != len(self._columns):
            raise ValueError(f"line {line}: expected {len(self._columns)} columns, got {len(row)}")
        for column, cell in zip(self._columns, row):
            if column.output is None:
                continue
            try:
                columns[column.output].append(column.kind.parse(cell))
            except ValueError as exc:
                raise ValueError(f"line {line}: column {column.vendor!r}: {exc}") from exc

    def _read_rows(self, text):
        """Return ``{output name: [values]}`` for the CSV text, refusing by line."""
        reader = csv.reader(io.StringIO(text, newline=""))
        columns = {column.output: [] for column in self._kept}
        first = True
        try:
            for row in reader:
                if not row:
                    continue
                if first:
                    first = False
                    if self._header.is_header(row):
                        self._check_header(row, reader.line_num)
                        continue
                self._append_row(columns, row, reader.line_num)
        except csv.Error as exc:
            raise ValueError(f"line {reader.line_num}: {exc}") from exc
        if not columns[self._instant]:
            raise ValueError("no rows in the CSV")
        return columns

    def _repeats(self, columns):
        """Return how many rows repeat an earlier row's instant."""
        instants = columns[self._instant]
        return len(instants) - len(set(instants))

    def _parse(self, body):
        """Return ``{output name: [values]}`` for one zip, memoised on the body object."""
        if self._memo is not None and self._memo[0] is body:
            return self._memo[1]
        columns = self._read_rows(self._csv_text(body))
        repeats = self._repeats(columns)
        if repeats and self._unique:
            raise ValueError(f"{repeats} duplicate row instant(s); this layout's instant is "
                             "a unique key")
        self._memo = (body, columns)
        return columns

    # -- the httpblobs transform contract -----------------------------------------------

    def note(self, entity, body):
        """Describe one file for the inventory row.

        Parameters
        ----------
        entity : str
            The requested entity (not used: a file describes itself).
        body : bytes
            The raw zip.

        Returns
        -------
        str
            ``"rows N, <first instant> .. <last instant>"``, with ``" OUT OF ORDER"``
            appended when the instants are not non-decreasing in file order and
            ``" DUPLICATE INSTANTS n"`` when ``n`` rows repeat an instant.

        Raises
        ------
        ValueError
            If the file cannot be reshaped.
        """
        columns = self._parse(body)
        instants = columns[self._instant]
        ordered = all(a <= b for a, b in zip(instants, instants[1:]))
        text = f"rows {len(instants)}, {_iso(instants[0])} .. {_iso(instants[-1])}"
        text += "" if ordered else " OUT OF ORDER"
        repeats = self._repeats(columns)
        return text + (f" DUPLICATE INSTANTS {repeats}" if repeats else "")

    def transform(self, entity, body):
        """Return the parquet file bytes for one zip.

        Parameters
        ----------
        entity : str
            The requested entity (not used: a file describes itself).
        body : bytes
            The raw zip, as the vendor served it.

        Returns
        -------
        bytes
            A parquet file whose columns are the layout's kept output names, in
            declared order.

        Raises
        ------
        ValueError
            If the file is not a zip of one CSV, is corrupt or oversized, is
            empty, or a row has the wrong width or an unparseable value.
        """
        import pyarrow as pa
        import pyarrow.parquet as pq

        columns = self._parse(body)
        schema = pa.schema([(column.output, getattr(pa, column.kind.arrow_type)())
                            for column in self._kept])
        buffer = io.BytesIO()
        pq.write_table(pa.table(columns, schema=schema), buffer)
        return buffer.getvalue()
