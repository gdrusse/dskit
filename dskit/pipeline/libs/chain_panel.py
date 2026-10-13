"""The date-partitioned panel of chain-day features, built root by root in bounded memory (ADR-0265 Amendment 3).

A cross-sectional test (H1, H3) wants, for ONE date, one row per (symbol, target DTE) of the chain-day features:
the cross-section on a date is a single small file. :class:`~dskit.pipeline.libs.chain_features.ChainDayFeatures`
makes the rows root by root; :class:`ChainPanelBuild` runs it once per root and lays the rows out by date.

**Phase 1, per root.** For each symbol of the input (a :class:`~dskit.pipeline.libs.chain_parquet.ChainRowSource`),
the feature node runs over ``records.select([symbol])`` alone, so memory is one root (``features.max_symbol_rows``
refuses a root too big to hold). The rows are written to ``<path>/roots/<symbol>.parquet`` (one row group, sorted by
date and target) with the root's identity in the footer: the contributing snapshot files with their sizes and
manifests, the feature params, the rates table, the bounds and rename map, and the root's gate values. A re-run
reads only that footer: a root whose identity is unchanged is SKIPPED, a changed one (a new snapshot landed for
it, a gate value moved, a param changed) is rebuilt; a torn temp file is never trusted. A root with nothing to emit
(never quoted, or no target resolved) leaves a zero-row file so it is skipped next time too, and is counted.

**Phase 2, by date.** All the roots' files are read back a few months at a time, sorted by date, symbol and target,
and cut into ``<path>/panel/<YYYY-MM-DD>.parquet``, written atomically. The whole panel carries one digest of every
root's identity; when it equals the manifest's and every listed file is present, phase 2 is skipped. The
``manifest.json`` (every file's sha256, rows and bytes, and each root's rows) is written LAST, so the directory
registers as a ``localblobs`` stream like an array panel does.

**Universe gate hooks.** ``first_quote_file`` (a JSON ``{root: ISO date}``, the export's ``first_quote.json``) adds
``first_quote_field`` (the date, the earliest over a stitched series' raw roots) and ``gate_field`` (true from that
date); ``census_file`` (a census records JSON-lines file) adds the declared ``census_columns`` (output column ->
[census field, arrow type]), for example the liquidity tercile. A root the files do not name gets nulls and is
counted. The columns are carried, never applied: a test chooses its universe, and a census statistic is a
whole-sample number (see ``ChainCensus``).

**Counters** (``summary``): roots, built, skipped, empty; rows and dates; dates written; roots without a first
quote or census row; the feature node's own counts summed over roots (status counts per target, quote exclusions,
unsolved vols, rows before each root's first bid), complete even on a resumed run; the reader's counters.

A partial panel (a ``symbols`` sample on the reader) is a different panel: write it to its own ``path``.
numpy-free; pyarrow is imported inside the methods that need it.
"""

import hashlib
import json
import os
import time
import urllib.parse
from datetime import date

from dskit.pipeline.base import value_hash
from dskit.pipeline.libs.chain_features import FEATURES, ChainDayFeatures, NoFeatureRows
from dskit.pipeline.node import Node, check_int_param, reject_unknown_params

__all__ = ["CENSUS_TYPES", "NODE_KINDS", "PANEL_VERSION", "ChainPanelBuild"]

#: No node kind is registered: a document names ``dskit.pipeline.libs.chain_panel:ChainPanelBuild``.
NODE_KINDS = ()

#: Bumped when the files' layout or the identity recipe changes; it is part of every root's identity.
PANEL_VERSION = 1
#: The Arrow types a census column may declare.
CENSUS_TYPES = ("int32", "int64", "float64", "string", "bool")

_META = "dskit.panel.meta"
_COUNTS = "dskit.panel.counts"
_RANGE = "dskit.panel.range"
_ROWS = "dskit.panel.rows"
_DIGEST = "dskit.panel.digest"
_ROOTS, _DATES = "roots", "panel"


def _quoted(symbol):
    """Return a file-name-safe spelling of a root."""
    return urllib.parse.quote(symbol, safe="")


class ChainPanelBuild(Node):
    """Build the per-date panel of chain-day features, one root at a time, resumable per root.

    Role ``transform``. Inputs: ``records`` (a :class:`~dskit.pipeline.libs.chain_parquet.ChainRowSource`: it must
    answer ``symbols()``, ``select()``, ``contributing()``, ``identity()``, ``raws()`` and ``report()``) and
    optional ``rates`` (the rates table the feature node reads). Outputs ``summary`` and ``manifest``.

    Parameters
    ----------
    params : dict
        ``features`` (REQUIRED): the :class:`~dskit.pipeline.libs.chain_features.ChainDayFeatures` params,
        validated by that node. ``path`` (REQUIRED): the output directory, created if absent.
        ``first_quote_file`` with ``first_quote_field`` and ``gate_field`` (all or none): see the module docstring.
        ``census_file`` with ``census_columns`` (both or neither), ``census_symbol_field`` (default
        ``"symbol"``). ``chunk_months`` (int >= 1, default 3): months read back per phase-2 step. ``repartition``
        (bool, default true): false builds the root files only.

    Examples
    --------
    The 30- and 90-day panel of every landed root::

        node = ChainPanelBuild("panel", {"features": features_params, "path": "/data/panel"})
        out = node.run(ctx, {"records": chain_rows, "rates": rates})
        out["summary"]["roots_built"]
    """

    role = "transform"
    outputs = ("summary", "manifest")
    _PARAMS = ("features", "path", "first_quote_file", "first_quote_field", "gate_field", "census_file",
               "census_columns", "census_symbol_field", "chunk_months", "repartition")
    _FIRST = ("first_quote_file", "first_quote_field", "gate_field")

    # -- validation ----------------------------------------------------------------------------------

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
            One message per problem; the feature node's problems are prefixed ``features:``.
        """
        problems = []
        reject_unknown_params(problems, params, cls._PARAMS)
        features = params.get("features")
        if not isinstance(features, dict) or not features:
            problems.append(f"features must be the ChainDayFeatures params object, got {features!r}")
        else:
            problems += [f"features: {p}" for p in ChainDayFeatures.validate_params(features)]
        if not isinstance(params.get("path"), str) or not params.get("path"):
            problems.append(f"path must be a non-empty string, got {params.get('path')!r}")
        given = [n for n in cls._FIRST if n in params]
        if given and len(given) < len(cls._FIRST):
            problems.append(f"declare all of {list(cls._FIRST)} or none; first_quote_field and gate_field name the "
                            f"two columns written; got {given}")
        for name in given:
            if not isinstance(params[name], str) or not params[name]:
                problems.append(f"{name} must be a non-empty string, got {params[name]!r}")
        cls._census_problems(problems, params)
        if "chunk_months" in params:
            check_int_param(problems, "chunk_months", params["chunk_months"], ge=1)
        if "repartition" in params and not isinstance(params["repartition"], bool):
            problems.append(f"repartition must be a boolean, got {params['repartition']!r}")
        return problems

    @staticmethod
    def _census_problems(problems, params):
        """Check the census file declaration: a file needs columns, each ``[field, type]``."""
        if ("census_file" in params) != ("census_columns" in params):
            problems.append("declare census_file and census_columns together (census_columns maps an output "
                            "column to [census field, arrow type])")
        if "census_file" in params and (not isinstance(params["census_file"], str) or not params["census_file"]):
            problems.append(f"census_file must be a non-empty string, got {params['census_file']!r}")
        columns = params.get("census_columns")
        if columns is None:
            return
        if not isinstance(columns, dict) or not columns:
            problems.append(f"census_columns must be a non-empty object, got {columns!r}")
            return
        for name, spec in columns.items():
            if (not isinstance(spec, (list, tuple)) or len(spec) != 2 or not all(isinstance(x, str) and x for x in spec)
                    or spec[1] not in CENSUS_TYPES):
                problems.append(f"census_columns[{name!r}] must be [census field, type] with type in "
                                f"{list(CENSUS_TYPES)}, got {spec!r}")

    # -- the schema ----------------------------------------------------------------------------------

    def _feature_columns(self):
        """Return the ordered ``(output name, arrow type)`` of the feature columns the params name."""
        import pyarrow as pa

        kinds = {"expiration": pa.date32(), "dte": pa.int32()}
        fields = self.params["features"]["fields"]
        return [(out, kinds.get(name, pa.float64())) for name, out in fields.items() if name in FEATURES]

    def _gate_columns(self):
        """Return the ordered ``(name, arrow type)`` of the universe-gate columns."""
        import pyarrow as pa

        p, out = self.params, []
        if "first_quote_file" in p:
            out += [(p["first_quote_field"], pa.date32()), (p["gate_field"], pa.bool_())]
        types = {"int32": pa.int32(), "int64": pa.int64(), "float64": pa.float64(), "string": pa.string(),
                 "bool": pa.bool_()}
        out += [(name, types[spec[1]]) for name, spec in p.get("census_columns", {}).items()]
        return out

    def _schema(self):
        """Return the Arrow schema of a root file and of a date file."""
        import pyarrow as pa

        f = self.params["features"]
        head = [(f["symbol_field"], pa.string()), (f["date_field"], pa.date32()), (f["target_field"], pa.int32()),
                (f["status_field"], pa.string())]
        return pa.schema(head + self._feature_columns() + self._gate_columns())

    # -- the gate files ------------------------------------------------------------------------------

    def _first_quotes(self):
        """Read ``first_quote_file``: ``{raw root: ISO date}`` (empty when not declared)."""
        path = self.params.get("first_quote_file")
        if path is None:
            return {}
        with open(path, encoding="utf-8") as handle:
            found = json.load(handle)
        if not isinstance(found, dict):
            raise ValueError(f"{self.key}: {path} must hold an object of root -> ISO date")
        return found

    def _census(self):
        """Read ``census_file``: ``{symbol: {census field: value}}`` (empty when not declared)."""
        path = self.params.get("census_file")
        if path is None:
            return {}
        key = self.params.get("census_symbol_field", "symbol")
        found = {}
        with open(path, encoding="utf-8") as handle:
            for number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                row = json.loads(line)
                if not isinstance(row, dict) or key not in row:
                    raise ValueError(f"{self.key}: {path}:{number} is not an object with {key!r}")
                found[row[key]] = row
        return found

    def _gates(self, source, symbol, raws, first_quotes, census):
        """Return one root's gate values: ``(first quote date or None, {column: value})`` and the identity part."""
        p = self.params
        dates = [first_quotes[r] for r in raws if r in first_quotes]
        first = min(dates) if dates else None
        start = getattr(source, "regime_start", lambda name: None)(symbol)
        if first is not None and start is not None:
            first = max(first, start)       # the later company of a reused root cannot borrow the earlier one's quotes
        values = {}
        row = census.get(symbol)
        for name, (field, _) in p.get("census_columns", {}).items():
            values[name] = None if row is None else row.get(field)
        return first, values

    # -- phase 1 -------------------------------------------------------------------------------------

    def _identity(self, source, symbol, first, census_values, rates_digest):
        """Return the identity dict of one root: everything its file's rows depend on."""
        return {"panel_version": PANEL_VERSION, "features": value_hash(self.params["features"]),
                "rates": rates_digest, "reader": source.identity(), "files": source.contributing(symbol),
                "first_quote": first, "census": census_values,
                "gate": [self.params.get(n) for n in ("first_quote_field", "gate_field")],
                "columns": {k: v for k, v in sorted(self.params.get("census_columns", {}).items())}}

    @staticmethod
    def _footer(path):
        """Read a root file's footer keys, or ``None`` when it is absent or unreadable."""
        import pyarrow as pa
        import pyarrow.parquet as pq

        if not os.path.isfile(path):
            return None
        try:
            raw = pq.read_metadata(path).metadata or {}
        except (OSError, pa.ArrowException):
            return None
        return {k.decode(): v.decode() for k, v in raw.items()}

    def _rows_table(self, rows, first, census_values):
        """Build the Arrow table of one root's feature rows with the gate columns, sorted by date and target."""
        import pyarrow as pa

        p, schema = self.params, self._schema()
        f = p["features"]
        rows = sorted(rows, key=lambda r: (r[f["date_field"]], r[f["target_field"]]))
        data = {name: [r[name] for r in rows] for name in schema.names[:4]}
        data[f["date_field"]] = [date.fromisoformat(d) for d in data[f["date_field"]]]
        for name, _ in self._feature_columns():
            values = [r[name] for r in rows]
            data[name] = ([date.fromisoformat(v) if v is not None else None for v in values]
                          if schema.field(name).type == pa.date32() else values)
        if "first_quote_file" in p:
            fq = date.fromisoformat(first) if first else None
            data[p["first_quote_field"]] = [fq] * len(rows)
            data[p["gate_field"]] = [None if fq is None else d >= fq for d in data[f["date_field"]]]
        for name in p.get("census_columns", {}):
            data[name] = [census_values[name]] * len(rows)
        return pa.table({name: pa.array(data[name], type=schema.field(name).type) for name in schema.names},
                        schema=schema)

    def _write(self, directory, name, table, meta):
        """Write ``table`` to ``directory/name`` atomically with ``meta`` (a dict of footer strings)."""
        import pyarrow.parquet as pq

        os.makedirs(directory, exist_ok=True)
        final, tmp = os.path.join(directory, name), os.path.join(directory, f".tmp-{name}")
        pq.write_table(table.replace_schema_metadata(meta), tmp, compression="zstd", row_group_size=1 << 20)
        with open(tmp, "rb") as handle:
            os.fsync(handle.fileno())
        os.replace(tmp, final)

    def _root(self, ctx, source, symbol, rates, rates_digest, first_quotes, census, counters):
        """Make or confirm one root's file; return its footer facts ``(identity hash, rows, counts, range)``."""
        p = self.params
        first, census_values = self._gates(source, symbol, source.raws(symbol), first_quotes, census)
        meta = self._identity(source, symbol, first, census_values, rates_digest)
        digest = value_hash(meta)
        path = os.path.join(p["path"], _ROOTS, _quoted(symbol) + ".parquet")
        have = self._footer(path)
        if have is not None and have.get(_META) == json.dumps(meta, sort_keys=True):
            counters["roots_skipped"] += 1
            return digest, have
        started = time.time()
        sub = source.select([symbol])
        inputs = {"records": sub} if rates is None else {"records": sub, "rates": rates}
        try:
            out = ChainDayFeatures("panel_features", p["features"]).run(ctx, inputs)
            rows, counts = out["records"], out["summary"]
        except NoFeatureRows as nothing:
            rows, counts = [], nothing.counts
        counts = {k: v for k, v in counts.items() if k not in ("reader",)}
        table = self._rows_table(rows, first, census_values)
        days = [r[p["features"]["date_field"]] for r in rows]
        footer = {_META: json.dumps(meta, sort_keys=True), _COUNTS: json.dumps(counts, sort_keys=True),
                  _RANGE: json.dumps([min(days), max(days)] if days else None),
                  _ROWS: json.dumps(len(rows))}
        self._write(os.path.join(p["path"], _ROOTS), _quoted(symbol) + ".parquet", table, footer)
        counters["roots_built"] += 1
        counters["seconds_features"] += time.time() - started
        return digest, footer

    # -- phase 2 -------------------------------------------------------------------------------------

    @staticmethod
    def _months(first, last, step):
        """Yield ``(start, end_before)`` ISO windows of ``step`` months that cover ``first``..``last``."""
        year, month = int(first[:4]), int(first[5:7])
        end = date.fromisoformat(last)
        while date(year, month, 1) <= end:
            nyear, nmonth = year + (month - 1 + step) // 12, (month - 1 + step) % 12 + 1
            yield date(year, month, 1), date(nyear, nmonth, 1)
            year, month = nyear, nmonth

    def _repartition(self, files, panel_digest, counters):
        """Cut the roots' rows into one file per date; return ``{relative path: manifest entry}``."""
        import numpy as np
        import pyarrow as pa
        import pyarrow.dataset as ds

        f = self.params["features"]
        d, s, t = f["date_field"], f["symbol_field"], f["target_field"]
        schema = self._schema()
        ranges = [json.loads(footer[_RANGE]) for footer in files.values() if json.loads(footer[_RANGE])]
        entries = {}
        if not ranges:
            return entries
        dataset = ds.dataset([p for p in files], format="parquet", schema=schema)
        for start, end in self._months(min(r[0] for r in ranges), max(r[1] for r in ranges),
                                       self.params.get("chunk_months", 3)):
            table = dataset.to_table(filter=(ds.field(d) >= start) & (ds.field(d) < end))
            if not table.num_rows:
                continue
            table = table.sort_by([(d, "ascending"), (s, "ascending"), (t, "ascending")])
            codes = table.column(d).cast(pa.int32()).to_numpy()
            cuts = np.flatnonzero(np.diff(codes)) + 1
            for lo, hi in zip([0, *cuts.tolist()], [*cuts.tolist(), table.num_rows]):
                piece = table.slice(lo, hi - lo)
                text = piece.column(d)[0].as_py().isoformat()
                name = f"{text}.parquet"
                self._write(os.path.join(self.params["path"], _DATES), name, piece,
                            {_DIGEST: panel_digest, "dskit.panel.date": text})
                raw = open(os.path.join(self.params["path"], _DATES, name), "rb").read()
                entries[f"{_DATES}/{name}"] = {"sha256": hashlib.sha256(raw).hexdigest(), "rows": piece.num_rows,
                                               "bytes": len(raw)}
                counters["dates_written"] += 1
        return entries

    def _manifest_path(self):
        return os.path.join(self.params["path"], "manifest.json")

    def _current(self, panel_digest):
        """Return the manifest on disk when it is this panel and every listed file is present, else ``None``."""
        path = self._manifest_path()
        if not os.path.isfile(path):
            return None
        try:
            with open(path, encoding="utf-8") as handle:
                manifest = json.load(handle)
        except ValueError:
            return None
        listed = manifest.get("files", {})
        ok = (manifest.get("panel_digest") == panel_digest
              and all(os.path.isfile(os.path.join(self.params["path"], n)) for n in listed))
        return manifest if ok else None

    # -- the node ------------------------------------------------------------------------------------

    def run(self, ctx, inputs):
        """Build or confirm every root's file, then the per-date files and the manifest.

        Parameters
        ----------
        ctx : NodeContext
            Unused.
        inputs : dict
            ``records``: a ``ChainRowSource``; ``rates`` (optional): the rates table.

        Returns
        -------
        dict
            ``summary`` (the counters of the module docstring) and ``manifest`` (the dict written to
            ``manifest.json``).

        Raises
        ------
        ValueError
            The input cannot ``select`` roots, a gate file is malformed, or the feature node refuses a root for
            a reason other than having nothing to emit (a malformed row, a repeated contract, a root over
            ``max_symbol_rows``).
        """
        source = inputs["records"]
        missing = [m for m in ("symbols", "select", "contributing", "identity", "raws") if not hasattr(source, m)]
        if missing:
            raise ValueError(f"{self.key}: the records input must be a ChainRowSource (it cannot {missing[0]}) - "
                             "the panel builds root by root through source.select")
        p, rates = self.params, inputs.get("rates")
        rates_digest = None if rates is None else value_hash(list(rates))
        counters = {"roots_built": 0, "roots_skipped": 0, "dates_written": 0, "seconds_features": 0.0}
        first_quotes, census = self._first_quotes(), self._census()
        symbols = source.symbols()
        footers = {}
        for symbol in symbols:
            digest, footer = self._root(ctx, source, symbol, rates, rates_digest, first_quotes, census, counters)
            footers[symbol] = (digest, footer)
        files = {os.path.join(p["path"], _ROOTS, _quoted(s) + ".parquet"): footers[s][1] for s in symbols}
        panel_digest = value_hash({"v": PANEL_VERSION, "roots": [[s, footers[s][0]] for s in symbols]})
        manifest = self._current(panel_digest)
        if manifest is None and p.get("repartition", True):
            started = time.time()
            entries = self._repartition(files, panel_digest, counters)
            counters["seconds_repartition"] = time.time() - started
            manifest = self._write_manifest(symbols, footers, entries, panel_digest, source)
        elif manifest is None:
            manifest = {"panel_digest": panel_digest, "files": {}, "rows": 0, "partial": True}
        summary = self._summary(symbols, footers, manifest, counters, source, first_quotes, census)
        self.log.info("panel: %d root(s) (%d built, %d skipped, %d empty), %d date file(s)", len(symbols),
                      counters["roots_built"], counters["roots_skipped"], summary["roots_empty"], summary["dates"])
        return {"summary": summary, "manifest": manifest}

    def _write_manifest(self, symbols, footers, entries, panel_digest, source):
        """Write ``manifest.json`` (last) and return it."""
        p, f = self.params, self.params["features"]
        manifest = {"panel_version": PANEL_VERSION, "panel_digest": panel_digest, "layout": "panel/<date>.parquet",
                    "columns": [[n, str(t)] for n, t in zip(self._schema().names, self._schema().types)],
                    "keys": [f["date_field"], f["symbol_field"], f["target_field"]], "end_before": f["end_before"],
                    "reader": source.identity(), "files": dict(sorted(entries.items())),
                    "rows": sum(e["rows"] for e in entries.values()),
                    "roots": {s: {"rows": json.loads(footers[s][1][_ROWS])} for s in symbols}}
        raw = json.dumps(manifest, sort_keys=True, indent=1).encode("utf-8")
        os.makedirs(p["path"], exist_ok=True)
        tmp = self._manifest_path() + ".tmp"
        with open(tmp, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, self._manifest_path())
        return manifest

    @staticmethod
    def _add(total, part):
        """Sum the numbers of ``part`` into ``total``, recursing through nested objects."""
        for key, value in part.items():
            if isinstance(value, dict):
                ChainPanelBuild._add(total.setdefault(key, {}), value)
            elif isinstance(value, (int, float)) and not isinstance(value, bool):
                total[key] = total.get(key, 0) + value

    def _summary(self, symbols, footers, manifest, counters, source, first_quotes, census):
        """Assemble the run summary from the counters and every root's footer."""
        features = {}
        for symbol in symbols:
            self._add(features, json.loads(footers[symbol][1][_COUNTS]))
        p = self.params
        unnamed = [s for s in symbols if not any(r in first_quotes for r in source.raws(s))]
        return {"roots": len(symbols), **{k: (round(v, 3) if isinstance(v, float) else v) for k, v in counters.items()},
                "roots_empty": sum(1 for s in symbols if not json.loads(footers[s][1][_RANGE])),
                "rows": manifest.get("rows", 0), "dates": len(manifest.get("files", {})),
                "roots_without_first_quote": len(unnamed) if "first_quote_file" in p else None,
                "roots_without_census": sum(1 for s in symbols if s not in census) if "census_file" in p else None,
                "features": features, "reader": source.report(), "path": p["path"],
                "first_date": min((json.loads(footers[s][1][_RANGE])[0] for s in symbols
                                   if json.loads(footers[s][1][_RANGE])), default=None),
                "last_date": max((json.loads(footers[s][1][_RANGE])[1] for s in symbols
                                  if json.loads(footers[s][1][_RANGE])), default=None),
                "end_before": p["features"]["end_before"]}
