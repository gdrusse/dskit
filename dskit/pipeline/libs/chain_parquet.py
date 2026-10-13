"""The Parquet-tree source of the chain nodes: columnar reads, the same rows (ADR-0265 Amendment 3).

A daily option-chain snapshot export (``dskit.onboarding.libs.parquet_export``) lands ONE immutable
Parquet file per committed snapshot, named by its acquisition id, sorted by symbol then date, with the date
window of every symbol in the file's footer (``dskit.export.windows``). :class:`ParquetChainRows` reads such a
tree the way :class:`~dskit.pipeline.libs.observations.ObservationUnitRows` reads the gzip snapshots: it yields
the same row dicts (dates as ISO text, the vendor's own field names) and keeps the same rule that the
LATEST-acquired copy of a (symbol, date) unit wins, with the same counters (``skipped_rows``,
``skipped_units``, ``unit_row_mismatch``) in :meth:`~ChainRowSource.report`. What changes is the cost: the scan
reads only the declared columns, in Arrow batches, with the symbol and date predicates pushed down to the row
groups, so a root scan is limited by decoding, not by parsing JSON text.

**Order.** Rows come SYMBOL by SYMBOL (sorted by output name), and within a symbol file by file in window
order, so a root's dates ascend and a root is never split by another root's rows. The gzip reader's member order
(newest acquisition first) can interleave roots whose history spans several snapshots; this reader never does,
which is what the ``symbol_contiguous`` features need. The census and the features are order independent
outputs, so nothing else changes.

**Dedup by footer windows.** Two files of one symbol whose windows are disjoint need no deduplication and cost
nothing. Only when windows overlap (a re-pulled batch after a crash) are the date columns of the two files read,
the older copy of each shared unit skipped and counted, and an older copy with another row count counted as a
split unit. Recency is the acquisition id's name order, across every directory.

**Stitching.** A ``renames_file`` (a :class:`~dskit.pipeline.libs.root_stitch.RootStitch` map, pinned by sha256)
renames an old root to its new root in the rows and in ``symbols`` (and cuts a root declared as a reuse break into an
earlier and a later series at its date); the effective dates are proven against the
footers before anything is read, and overlapping old and new ranges refuse.

**The sealed period.** ``end_before`` is a predicate pushed into the scan, so a row on or after it is never
decoded. ``sealed_from`` (optional) goes further: a file whose footer window reaches it is refused WHOLE, named
by acquisition id, whatever the bounds, and ``end_before`` may not exceed it.

**Two views.** Iterating the source yields row dicts; :meth:`~ChainRowSource.batches` yields the same rows as
Arrow tables (dates as ISO text), for a consumer that works on columns. Both count into :meth:`report`.

pyarrow and numpy are imported inside the methods that need them (the purity gate keeps them out of module
level). The plan (the file list with sizes and footers) is pinned by ``fingerprint()``; a file that changed
since it was planned refuses.
"""

import datetime
import json
import os
from dataclasses import dataclass

from dskit.pipeline.base import value_hash
from dskit.pipeline.libs.parquet import DateBoundedParquet
from dskit.pipeline.libs.root_stitch import RootStitch
from dskit.pipeline.node import Node, check_int_param, reject_unknown_params

__all__ = ["NODE_KINDS", "ChainRowSource", "ParquetChainRows"]

#: No node kind is registered: a document names ``dskit.pipeline.libs.chain_parquet:ParquetChainRows``.
NODE_KINDS = ()

_PREFIX = "dskit.export."
DEFAULT_BATCH_SIZE = 65536


def _iso(value):
    """Return ``value`` when it is an ISO ``YYYY-MM-DD`` date text, else ``None``."""
    try:
        return value if isinstance(value, str) and datetime.date.fromisoformat(value).isoformat() == value else None
    except ValueError:
        return None


@dataclass(frozen=True)
class _File:
    """One planned Parquet file: where it is, what it held when it was planned, and its symbol windows."""

    name: str
    path: str
    size: int
    mtime_ns: int
    manifest_sha256: str
    snapshot_hash: str
    windows: dict           # raw symbol -> (first date, last date) as ISO text
    columns: tuple


@dataclass(frozen=True)
class _Part:
    """The rows of one raw symbol in one file."""

    file: _File
    raw: str
    first: str
    last: str
    since: str = None       # a reuse break: only dates from here (inclusive) belong to this series
    before: str = None      # ... and only dates before here


class _Plan:
    """The pinned file list and, per output symbol, the parts that hold it."""

    def __init__(self, files, stitch, symbol_field=None, date_field=None):
        self.files, self.stitch = files, stitch
        self.symbol_field, self.date_field = symbol_field, date_field
        self.names = tuple(f.name for f in files)
        self.widest = {}
        for file in files:
            for raw, (first, last) in file.windows.items():
                lo, hi = self.widest.get(raw, (first, last))
                self.widest[raw] = (min(lo, first), max(hi, last))
        self.parts = {}
        for file in files:
            for raw, (first, last) in sorted(file.windows.items()):
                for series, since, before in stitch.segments(raw):
                    if (since and last < since) or (before and first >= before):
                        continue
                    clipped = (max(first, since) if since else first,
                               min(last, (datetime.date.fromisoformat(before) - datetime.timedelta(days=1)).isoformat())
                               if before else last)
                    self.parts.setdefault(series, []).append(_Part(file, raw, *clipped, since, before))

    def identity(self):
        """Return the JSON-small identity of the planned files."""
        entries = [[f.name, f.size, f.manifest_sha256, f.snapshot_hash] for f in self.files]
        return {"kind": "ParquetChainRows", "members": list(self.names), "sha256": value_hash(entries)}


class ChainRowSource:
    """Chain rows of a Parquet tree: re-iterable dicts, an Arrow view and the reader's counters.

    Built by :class:`ParquetChainRows`; not constructed directly. Iteration order and counters are described in
    the module docstring.
    """

    def __init__(self, plan, params, symbols):
        self._plan, self._params, self._symbols = plan, params, tuple(symbols)
        self._dedup = {}
        self._reset()

    # -- the view of the plan ------------------------------------------------------------------

    def symbols(self):
        """Return the output symbols this source reads, sorted."""
        return list(self._symbols)

    def select(self, symbols):
        """Return a source over a subset of this one's symbols (the same plan and bounds).

        Raises
        ------
        ValueError
            A requested symbol is not one of :meth:`symbols`.
        """
        unknown = sorted(set(symbols) - set(self._symbols))
        if unknown:
            raise ValueError(f"select: {unknown} are not symbols of this source")
        return ChainRowSource(self._plan, self._params, sorted(set(symbols)))

    def regime_start(self, symbol):
        """Return the first date of a series that a reuse break starts (the later company), else ``None``."""
        return self._plan.stitch.regime_start(symbol)

    def raws(self, symbol):
        """Return the raw roots an output symbol reads (the old names of a stitched series, then its own)."""
        return self._plan.stitch.raws(symbol)

    def raw_windows(self):
        """Return ``{raw root: (first date, last date)}`` over every planned file (the footers' windows)."""
        return dict(sorted(self._plan.widest.items()))

    def identity(self):
        """Return what besides the files decides these rows: the bounds and the rename map's digest."""
        return {"end_before": self._params["end_before"], "start": self._params.get("start"),
                "renames": self._plan.stitch.digest()}

    def contributing(self, symbol):
        """Return the planned files that hold ``symbol`` inside the bounds, as ``(name, size, manifest_sha256)``."""
        return [(p.file.name, p.file.size, p.file.manifest_sha256) for p in self._parts(symbol)]

    def report(self):
        """Return the last pass's counters as a plain dict (the unit reader's keys, plus ``files``/``symbols``)."""
        return {"members": len(self._members), "rows": self.rows, "units": self.units,
                "skipped_rows": self.skipped_rows, "skipped_units": self.skipped_units,
                "unit_row_mismatch": self.unit_row_mismatch, "files": len(self._plan.files),
                "symbols": len(self._symbols), "symbols_missing": self._params["missing"],
                "stitch": self._stitch_report()}

    def _stitch_report(self):
        """Count the declared renames and breaks, name declared roots the data lacks, and the pending entries."""
        stitch, seen = self._plan.stitch, self._plan.widest
        pending = stitch.pending_roots()
        return {"renames": len(stitch.renames()), "breaks": len(stitch.breaks()),
                "absent": sorted(stitch.declared_roots() - set(seen)), "pending": len(stitch.pending()),
                "pending_landed": sorted(pending & set(seen))}

    def _reset(self):
        self.rows = self.units = self.skipped_rows = self.skipped_units = self.unit_row_mismatch = 0
        self._members = set()

    # -- parts and deduplication ---------------------------------------------------------------

    def _parts(self, symbol):
        """Return the parts of ``symbol`` whose window meets the bounds, in window order."""
        start, end_before = self._params.get("start"), self._params["end_before"]
        found = [p for p in self._plan.parts[symbol]
                 if p.last >= (start or "") and p.first < end_before]
        return sorted(found, key=lambda p: (p.first, p.file.name))

    @staticmethod
    def _overlap(parts):
        """Say whether any two parts' windows share a date."""
        ordered = sorted(parts, key=lambda p: (p.first, p.last))
        return any(a.last >= b.first for a, b in zip(ordered, ordered[1:]))

    def _bounded(self, part):
        """Return the verified, date-bounded scan of one part."""
        p = self._params
        file = part.file
        if os.stat(file.path).st_size != file.size or os.stat(file.path).st_mtime_ns != file.mtime_ns:
            raise ValueError(f"{file.name}: the file changed since it was planned (size or mtime differs) - "
                             "an exported snapshot is immutable; re-run so the plan is rebuilt")
        limits = {"end_before": min(p["end_before"], part.before) if part.before else p["end_before"]}
        low = max(p.get("start") or "", part.since or "")
        if low:
            limits["start"] = low
        return DateBoundedParquet(file.path, list(p["read"]), {p["date_field"]: limits})

    def _unit_counts(self, part):
        """Return ``{date as ISO text: rows}`` of one part inside the bounds (the overlap analysis)."""
        import pyarrow as pa
        import pyarrow.compute as pc
        import pyarrow.dataset as ds

        counts = {}
        bounded = self._bounded(part)
        bounded.columns = [self._params["date_field"]]
        for batch in bounded.batches(where=ds.field(self._params["symbol_field"]) == part.raw,
                                     batch_size=self._params["batch_size"]):
            table = pa.Table.from_batches([batch]).group_by(self._params["date_field"]).aggregate(
                [([], "count_all")])
            for date, n in zip(pc.cast(table.column(self._params["date_field"]), pa.string()).to_pylist(),
                               table.column("count_all").to_pylist()):
                counts[date] = counts.get(date, 0) + n
        return counts

    def _analysis(self, symbol, parts):
        """Decide, for a symbol with overlapping parts, which part owns each date (memoized).

        The newest-acquired part holding a date owns it. Returns ``(segments, rows, units, mismatched)``:
        ``segments`` is the date-ordered list of ``(part, first date, last date)`` runs over which one part owns
        every date of the union, so reading the runs in order yields the symbol's dates ascending; ``rows``,
        ``units`` and ``mismatched`` are the older copies skipped, as rows, as distinct dates, and as dates whose
        older copy had another row count.
        """
        if symbol in self._dedup:
            return self._dedup[symbol]
        counts = {id(p): self._unit_counts(p) for p in parts}
        kept, owner, rows, units, mismatched = {}, {}, 0, set(), set()
        for part in sorted(parts, key=lambda p: p.file.name, reverse=True):
            mine = counts[id(part)]
            for date in mine:
                if date in kept:
                    rows += mine[date]
                    units.add(date)
                    if mine[date] != kept[date]:
                        mismatched.add(date)
                else:
                    kept[date], owner[date] = mine[date], part
        segments = []
        for date in sorted(owner):
            if segments and segments[-1][0] is owner[date]:
                segments[-1][2] = date
            else:
                segments.append([owner[date], date, date])
        self._dedup[symbol] = ([tuple(x) for x in segments], rows, units, mismatched)
        return self._dedup[symbol]

    # -- reading -------------------------------------------------------------------------------

    def _runs(self, symbol):
        """Yield ``(part, extra predicate)`` for each stretch of the symbol to read, dates ascending overall."""
        import pyarrow as pa
        import pyarrow.dataset as ds

        p, parts = self._params, self._parts(symbol)
        if not self._overlap(parts):
            for part in parts:
                yield part, ds.field(p["symbol_field"]) == part.raw
            return
        segments, rows, units, mismatched = self._analysis(symbol, parts)
        self.skipped_rows += rows
        self.skipped_units += len(units)
        self.unit_row_mismatch += len(mismatched)
        for part, first, last in segments:
            field = ds.field(p["date_field"]).cast(pa.date32())
            low, high = (datetime.date.fromisoformat(d) for d in (first, last))
            yield part, (ds.field(p["symbol_field"]) == part.raw) & (field >= pa.scalar(low, pa.date32())) \
                & (field <= pa.scalar(high, pa.date32()))

    def _tables(self, symbol, seen_dates):
        """Yield the symbol's Arrow tables (dates as ISO text, the symbol renamed), counting as it goes."""
        import pyarrow as pa
        import pyarrow.compute as pc

        p = self._params
        for part, where in self._runs(symbol):
            self._members.add(part.file.name)
            for batch in self._bounded(part).batches(where=where, batch_size=p["batch_size"]):
                if not batch.num_rows:
                    continue
                dates = batch.column(batch.schema.get_field_index(p["date_field"]))
                seen_dates.update(pc.unique(dates).to_pylist())
                columns = []
                for name in p["fields"]:
                    column = batch.column(batch.schema.get_field_index(name))
                    if name == p["symbol_field"] and part.raw != symbol:
                        column = pa.repeat(pa.scalar(symbol, pa.string()), batch.num_rows)
                    elif pa.types.is_date(column.type):
                        column = pc.cast(column, pa.string())
                    columns.append(column)
                self.rows += batch.num_rows
                yield pa.Table.from_arrays(columns, names=list(p["fields"]))

    def batches(self):
        """Yield the rows as Arrow tables of the declared ``fields`` (dates as ISO text), symbol by symbol."""
        self._reset()
        for symbol in self._symbols:
            seen = set()
            yield from self._tables(symbol, seen)
            self.units += len(seen)

    def __iter__(self):
        """Yield the rows as dicts of the declared ``fields``, symbol by symbol (see :meth:`batches`)."""
        names = list(self._params["fields"])
        for table in self.batches():
            columns = [table.column(n).to_pylist() for n in names]
            for values in zip(*columns):
                yield dict(zip(names, values))


class ParquetChainRows(Node):
    """Emit a Parquet tree of chain snapshots as rows, symbol by symbol, newest-acquired copy of a unit wins.

    Role ``data``, no inputs; output ``records`` is a :class:`ChainRowSource` (a re-iterable of row dicts with
    ``report()``, ``symbols()``, ``select()`` and ``batches()``). See the module docstring for the order, the
    deduplication, the stitching and the sealed period.

    Parameters
    ----------
    params : dict
        ``directories`` (non-empty list of str, REQUIRED): directories of ``<acquisition id>.parquet`` files
        (hidden names and other files are ignored); every directory is one stream, ordered together by
        acquisition id. ``fields`` (non-empty list of column names, REQUIRED): the columns each row keeps, named
        as in the file. ``symbol_field`` and ``date_field`` (REQUIRED): the root and date columns (a date32 or an
        ISO ``YYYY-MM-DD`` text column; either way rows carry ISO text, and the footer windows must match the data). ``end_before`` (REQUIRED ISO date): exclusive upper bound, pushed into the scan.
        ``start`` (ISO, optional): inclusive lower bound. ``sealed_from`` (ISO, optional, at or after
        ``end_before``): a file whose footer window reaches it refuses. ``symbols`` (non-empty list, optional):
        the OUTPUT symbols to read; a name with no data is counted in ``report()['symbols_missing']``.
        ``renames_file``, ``renames_key`` and ``renames_sha256`` (all or none): the stitch map; ``pending_key`` (optional): the
        member of entries awaiting proof, only reported (never applied); ``breaks_key``
        (optional): the member of the same file holding the reuse breaks (a root cut into an earlier and a later series). ``batch_size``
        (int >= 1, default 65536).

    Examples
    --------
    The chain of a decade, only the columns a census reads::

        node = ParquetChainRows("chain", {
            "directories": ["/data/parquet-v2/options-tested/theta"],
            "fields": ["symbol", "date", "strike", "bid", "ask"], "symbol_field": "symbol",
            "date_field": "date", "end_before": "2026-01-01"})
        rows = node.run(ctx, {})["records"]
        rows.symbols()           # ["AAPL", "ABBV", ...]
    """

    role = "data"
    outputs = ("records",)
    _PARAMS = ("directories", "fields", "symbol_field", "date_field", "end_before", "start", "sealed_from",
               "symbols", "batch_size", "renames_file", "renames_key", "renames_sha256", "breaks_key",
               "pending_key")
    _RENAMES = ("renames_file", "renames_key", "renames_sha256")

    @classmethod
    def validate_params(cls, params):
        """List problems with ``params``, empty when none.

        Parameters
        ----------
        params : dict
            The node's declared params.

        Returns
        -------
        list of str
            One message per problem.
        """
        problems = []
        reject_unknown_params(problems, params, cls._PARAMS)
        cls._list_problems(problems, params)
        for name in ("symbol_field", "date_field"):
            if not isinstance(params.get(name), str) or not params.get(name):
                problems.append(f"{name} must be a non-empty string, got {params.get(name)!r}")
        for name in ("end_before", "start", "sealed_from"):
            if (name in params or name == "end_before") and _iso(params.get(name)) is None:
                problems.append(f"{name} must be an ISO date text YYYY-MM-DD, got {params.get(name)!r}")
        dates = {n: _iso(params.get(n)) for n in ("start", "end_before", "sealed_from")}
        if dates["start"] and dates["end_before"] and dates["start"] >= dates["end_before"]:
            problems.append("start must be before end_before")
        if dates["sealed_from"] and dates["end_before"] and dates["end_before"] > dates["sealed_from"]:
            problems.append("end_before may not exceed sealed_from (the scan would read a sealed date)")
        if "batch_size" in params:
            check_int_param(problems, "batch_size", params["batch_size"], ge=1)
        given = [n for n in cls._RENAMES if n in params]
        if given and len(given) < len(cls._RENAMES):
            problems.append(f"declare all of the renames params {list(cls._RENAMES)} or none; got {given}")
        for name in (*given, *(n for n in ("breaks_key", "pending_key") if n in params)):
            if not isinstance(params[name], str) or not params[name]:
                problems.append(f"{name} must be a non-empty string, got {params[name]!r}")
        if ("breaks_key" in params or "pending_key" in params) and not given:
            problems.append("breaks_key and pending_key need the renames params (they live in the same pinned file)")
        return problems

    @staticmethod
    def _list_problems(problems, params):
        """Check the three list params: directories, fields and symbols."""
        for name, required in (("directories", True), ("fields", True), ("symbols", False)):
            if name not in params and not required:
                continue
            value = params.get(name)
            if (not isinstance(value, (list, tuple)) or not value
                    or not all(isinstance(v, str) and v for v in value) or len(set(value)) != len(value)):
                problems.append(f"{name} must be a non-empty list of distinct non-empty strings, got {value!r}")

    # -- planning ------------------------------------------------------------------------------

    def _footer(self, path):
        """Read one file's footer: size, mtime, identity keys, windows and column names."""
        import pyarrow as pa
        import pyarrow.parquet as pq

        stat = os.stat(path)
        name = os.path.basename(path)
        try:
            meta = pq.read_metadata(path)
        except (OSError, pa.ArrowException) as err:
            raise ValueError(f"{name}: unreadable Parquet footer: {err}") from err
        have = {k.decode(): v.decode() for k, v in (meta.metadata or {}).items() if k.decode().startswith(_PREFIX)}
        if _PREFIX + "windows" not in have:
            raise ValueError(f"{name}: no {_PREFIX}windows in the footer - not a snapshot export, or one from before "
                             "windows were recorded; this reader plans by windows and refuses to guess")
        if not have.get(_PREFIX + "manifest_sha256"):
            raise ValueError(f"{name}: no {_PREFIX}manifest_sha256 in the footer - a file with no source identity "
                             "cannot be pinned or told apart from another copy")
        windows = {s: (w[0], w[1]) for s, w in json.loads(have[_PREFIX + "windows"]).items()}
        self._prove_windows(name, meta, windows)
        return _File(name=name, path=path, size=stat.st_size, mtime_ns=stat.st_mtime_ns,
                     manifest_sha256=have.get(_PREFIX + "manifest_sha256", ""),
                     snapshot_hash=have.get(_PREFIX + "snapshot_hash", ""), windows=windows,
                     columns=tuple(meta.schema.names))

    def _prove_windows(self, name, meta, windows):
        """Check the footer's symbol windows against the row-group statistics; refuse a mismatch.

        The reader plans and prunes by the windows but reads by predicates, so a window the data contradicts
        would silently drop or admit rows. A row group whose symbol statistics are a single root bounds that
        root's dates exactly; a group that spans roots only bounds them. Per root: every pure group must lie
        inside the window; the window must lie inside the union of the groups that can hold the root; when every
        such group is pure the window must EQUAL their union. Groups without statistics are not checked.
        """
        p = self.params
        names = list(meta.schema.names)
        si, di = names.index(p["symbol_field"]), names.index(p["date_field"])
        groups = []
        for g in range(meta.num_row_groups):
            sym, day = meta.row_group(g).column(si).statistics, meta.row_group(g).column(di).statistics
            if sym is None or day is None or not sym.has_min_max or not day.has_min_max:
                return
            groups.append((str(sym.min), str(sym.max), str(day.min), str(day.max)))
        for root, (first, last) in sorted(windows.items()):
            holders = [g for g in groups if g[0] <= root <= g[1]]
            if not holders:
                raise ValueError(f"{self.key}: {name}: the footer window names root {root!r} but no row group "
                                 "can hold it")
            lo, hi = min(g[2] for g in holders), max(g[3] for g in holders)
            pure = [g for g in holders if g[0] == g[1] == root]
            bad = [g for g in pure if g[2] < first or g[3] > last]
            if bad or first < lo or last > hi or (len(pure) == len(holders) and (first, last) != (lo, hi)):
                raise ValueError(f"{self.key}: {name}: the footer window of {root!r} ({first}..{last}) contradicts "
                                 f"the data's row-group statistics ({lo}..{hi}) - the window decides which files "
                                 "are read, so a wrong one drops or admits rows")

    def _list_files(self):
        """Return the tree's Parquet paths in acquisition-id order across every directory."""
        paths = []
        for directory in self.params["directories"]:
            if not os.path.isdir(directory):
                raise ValueError(f"{self.key}: {directory} is not a directory")
            paths += [os.path.join(directory, n) for n in os.listdir(directory)
                      if n.endswith(".parquet") and not n.startswith(".")]
        if not paths:
            raise ValueError(f"{self.key}: no Parquet files under {self.params['directories']}")
        return sorted(paths, key=lambda p: (os.path.basename(p), p))

    def _stitch(self):
        """Build the rename map the params declare (the identity when none)."""
        p = self.params
        if "renames_file" not in p:
            return RootStitch([])
        return RootStitch.from_file(p["renames_file"], p["renames_key"], p["renames_sha256"], p.get("breaks_key"),
                                   p.get("pending_key"))

    def _plan_files(self):
        """Plan the tree: read every footer, refuse a sealed window, a missing column or a bad hand-over."""
        p = self.params
        files = [self._footer(path) for path in self._list_files()]
        needed = {*p["fields"], p["symbol_field"], p["date_field"]}
        for file in files:
            missing = sorted(needed - set(file.columns))
            if missing:
                raise ValueError(f"{self.key}: {file.name} has no column(s) {missing}")
            if p.get("sealed_from"):
                late = sorted(s for s, w in file.windows.items() if w[1] >= p["sealed_from"])
                if late:
                    raise ValueError(f"{self.key}: {file.name} holds dates on or after the sealed date "
                                     f"{p['sealed_from']} (symbols {late[:5]}) - a sealed file is refused whole")
        plan = _Plan(files, self._stitch(), p["symbol_field"], p["date_field"])
        try:
            plan.stitch.check_windows(plan.widest, lambda root: self._root_dates(plan, root))
        except ValueError as err:
            raise ValueError(f"{self.key}: {err}") from None
        return plan

    @staticmethod
    def _root_dates(plan, root):
        """Return the sorted observed ISO dates of one raw root over every planned file that holds it."""
        import pyarrow as pa
        import pyarrow.compute as pc
        import pyarrow.dataset as ds

        seen = set()
        for file in plan.files:
            if root in file.windows:
                table = ds.dataset(file.path, format="parquet").to_table(
                    columns=[plan.date_field], filter=ds.field(plan.symbol_field) == root)
                column = table.column(0)
                seen.update(pc.unique(pc.cast(column, pa.string())).to_pylist())
        return sorted(seen)

    def fingerprint(self):
        """Pin the file list and answer its identity.

        Returns
        -------
        dict
            ``{"kind", "members", "sha256"}`` over each planned file's name, size and footer identity; the run
            reads exactly these files.
        """
        if getattr(self, "_pinned", None) is None:
            self._pinned = self._plan_files()
        return self._pinned.identity()

    def run(self, ctx, inputs):
        """Return the symbol-by-symbol source over the pinned (or freshly planned) tree.

        Parameters
        ----------
        ctx : NodeContext
            Unused.
        inputs : dict
            Empty: role ``data`` takes no inputs.

        Returns
        -------
        dict
            ``{"records": ChainRowSource}``.

        Raises
        ------
        ValueError
            The tree is empty, a file lacks windows or a declared column, a file reaches ``sealed_from``, a
            rename is contradicted by the data, or the rename file's hash differs.
        """
        self.fingerprint()                  # pin the file set before anything is read, so every consumer agrees
        plan = self._pinned
        p = self.params
        read = list(dict.fromkeys([*p["fields"], p["symbol_field"], p["date_field"]]))
        params = {"fields": p["fields"], "symbol_field": p["symbol_field"], "date_field": p["date_field"],
                  "end_before": p["end_before"], "start": p.get("start"), "read": read,
                  "batch_size": p.get("batch_size", DEFAULT_BATCH_SIZE), "missing": 0}
        source = ChainRowSource(plan, params, [])
        wanted = p.get("symbols")
        found = sorted(s for s in plan.parts if source._parts(s))
        if wanted is not None:
            params["missing"] = len(set(wanted) - set(found))
            found = [s for s in found if s in set(wanted)]
        source = ChainRowSource(plan, params, found)
        self.log.info("parquet chain: %d file(s), %d symbol(s)", len(plan.files), len(found))
        return {"records": source}
