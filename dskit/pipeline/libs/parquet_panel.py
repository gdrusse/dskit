"""Materialize a bounded, pinned panel with prior-date keyed feature tables.

The Parquet reader owns source verification and temporal predicates;
ObservationTables owns matching, age and missingness. This adapter adds global
identity checks and immutable directory publication. It never fits a model.
Resource limits are post-reader admission limits, not streaming RAM limits.
"""
import datetime
import json
import math
import os
from pathlib import Path
import shutil
import tempfile

from dskit.pipeline.base import value_hash
from dskit.pipeline.libs.observation_tables import ObservationTables
from dskit.pipeline.libs.parquet import DateBoundedParquet, ParquetStreamRows
from dskit.pipeline.node import check_int_param, reject_unknown_params
from dskit.pipeline.workflow import path_hash

__all__ = ["ParquetPanelAugment"]


class ParquetPanelAugment(ObservationTables):
    """Attach prior-date tables and atomically publish a verified Parquet panel.

    Parameters
    ----------
    params : dict
        Required base is one ParquetStreamRows source declaration. key and
        identity name output columns; temporal_fields names all protected base
        dates. tables maps names to sources (a list of bounded pinned source
        declarations), key_fields, columns, max_age_days and strict_prior=True.
        end_before caps every temporal read. output is a new directory outside
        the sources. max_rows caps each source and combined table after reading;
        max_output_bytes caps the completed temporary directory. age_suffix and
        missing_suffix are required explicit companion-column suffixes.

    Examples
    --------
    Materialize caller-declared, pinned sources::

        node = ParquetPanelAugment("panel", declaration)
        result = node.run(context, {})
        # -> result contains preserved records and publication provenance.
    """

    role = "data"
    outputs = ("records", "provenance")
    _PARAMS = ("base", "key", "identity", "temporal_fields", "tables", "age_suffix",
               "missing_suffix", "end_before", "output", "max_rows", "max_output_bytes")
    _TABLE_KEYS = ("sources", "key_fields", "columns", "max_age_days", "strict_prior")
    _PANEL = "panel.parquet"
    _MANIFEST = "manifest.json"

    @staticmethod
    def _names(value):
        """Recognize a nonempty list of distinct nonempty column names."""
        return (isinstance(value, list) and bool(value)
                and all(isinstance(v, str) and v for v in value)
                and len(set(value)) == len(value))

    @classmethod
    def _table_problems(cls, name, spec, key, root):
        """Reuse the attachment owner's schema checks with bounded source declarations."""
        if not isinstance(spec, dict):
            return [f"tables.{name} must be a mapping"]
        problems = []
        reject_unknown_params(problems, spec, cls._TABLE_KEYS)
        translated = {k: v for k, v in spec.items() if k != "sources"}
        translated.update(root="bounded", source="bounded", stream="bounded")
        problems += ObservationTables._table_problems(name, translated, key, None)
        problems += ParquetStreamRows.validate_params({"sources": spec.get("sources")})
        if spec.get("strict_prior") is not True:
            problems.append(f"tables.{name}.strict_prior must be explicitly true")
        if "max_age_days" not in spec or not cls._names(spec.get("key_fields")):
            problems.append(f"tables.{name} needs max_age_days and unique key_fields")
        return problems

    @classmethod
    def validate_params(cls, params):
        """Validate declarations without resolving any source.

        Parameters
        ----------
        params : dict
            Complete materialization declaration.

        Returns
        -------
        list
            Every malformed shape, bound or column contract.
        """
        problems = super().validate_params(params)
        problems += [f"{key} is required" for key in cls._PARAMS if key not in params]
        for key in ("key", "identity", "temporal_fields"):
            if not cls._names(params.get(key)):
                problems.append(f"{key} must contain unique names")
        for key in ("max_rows", "max_output_bytes"):
            if key in params:
                check_int_param(problems, key, params[key], ge=1)
        if not isinstance(params.get("output"), str) or not params.get("output"):
            problems.append("output must be a nonempty path")
        end = params.get("end_before")
        problems += DateBoundedParquet.problems({"date": {"end_before": end}})
        base = params.get("base")
        problems += ParquetStreamRows.validate_params({"sources": [base]})
        if problems:
            return problems
        columns = set(base["columns"].values())
        required = set(params["key"] + params["identity"] + params["temporal_fields"])
        if not required <= columns or params["key"][-1] not in params["temporal_fields"]:
            problems.append("base projection must include all keys/temporal fields and protect join date")
        problems += cls._bounded(base, params["temporal_fields"], end)
        for name, table in params["tables"].items():
            for source in table["sources"]:
                available = set(source["columns"].values())
                if not set(table["key_fields"] + list(table["columns"])) <= available:
                    problems.append(f"{name}: source projection omits key or attachment columns")
                problems += cls._bounded(source, [table["key_fields"][-1]], end)
        return problems

    @staticmethod
    def _bounded(source, fields, end):
        """Map output fields back to required source-side upper predicates."""
        inverse = {v: k for k, v in source["columns"].items()}
        problems = []
        for field in fields:
            bound = source["read_bounds"].get(inverse.get(field), {}).get("end_before")
            if bound is None or bound > end:
                problems.append(f"protected field {field!r} needs end_before <= {end}")
        for bound in source["read_bounds"].values():
            if bound.get("end_before") is None or bound["end_before"] > end:
                problems.append("every temporal predicate requires the declared upper bound")
        return problems

    def _sources(self):
        """Yield named single-source declarations in stable order."""
        yield "base", self.params["base"]
        for name, table in self.params["tables"].items():
            for index, source in enumerate(table["sources"]):
                yield (name, index), source

    def _preflight(self):
        """Refuse changed/unsafe declarations and placeholder pins before any I/O."""
        problems = self.validate_params(self.params)
        if problems:
            raise ValueError("; ".join(problems))
        if any(s["snapshot"] == "0" * 64 for _, s in self._sources()):
            raise ValueError("unresolved source pin; no source may be resolved")
        identity = value_hash(self.params)
        if hasattr(self, "_declared") and self._declared != identity:
            raise ValueError("declaration changed after fingerprint")
        dest = Path(self.params["output"]).absolute()
        if dest.exists() or dest.is_symlink():
            raise FileExistsError(f"output already exists: {dest}")
        self._outside(dest, [Path(s["root"]) for _, s in self._sources()])
        return dest, identity

    @staticmethod
    def _outside(dest, sources):
        """Keep a destination disjoint from every resolved input location."""
        target = dest.resolve()
        for source in sources:
            source = source.resolve()
            if target.is_relative_to(source) or source.is_relative_to(target):
                raise ValueError("output overlaps a source location")

    def fingerprint(self):
        """Pin all readers before any records are materialized.

        Returns
        -------
        dict
            Config identity and all source fingerprints; no payload records.

        Raises
        ------
        ValueError
            Unsafe declarations, unresolved pins or source identity drift.
        """
        dest, identity = self._preflight()
        if not hasattr(self, "_readers"):
            readers = {name: ParquetStreamRows("source", {"sources": [source]})
                       for name, source in self._sources()}
            prints = {name: reader.fingerprint() for name, reader in readers.items()}
            paths = [path for reader in readers.values() for snap in reader._pinned
                     for _, path, _ in snap["files"]]
            self._outside(dest, paths)
            self._readers, self._prints, self._declared = readers, prints, identity
        return {"kind": type(self).__name__, "config_sha256": identity,
                "sources": self._source_report()}

    def _source_report(self):
        """Encode source keys without ambiguity between base and table sources."""
        return {"base": self._prints["base"], "tables": {
            name: [self._prints[(name, i)] for i in range(len(table["sources"]))]
            for name, table in self.params["tables"].items()}}

    def _load(self, name, ctx):
        """Read one pinned source, checking post-materialization admission limits."""
        rows = self._readers[name].run(ctx, {})["records"]
        if len(rows) > self.params["max_rows"]:
            raise ValueError("source row limit exceeded")
        projected = set(self._readers[name].params["sources"][0]["columns"].values())
        if any(set(row) != projected for row in rows):
            raise ValueError("source row differs from declared projection")
        self._counts["base" if name == "base" else f"table:{name[0]}:{name[1]}"] = len(rows)
        return rows

    def _check_rows(self, rows, keys, dates):
        """Refuse ambiguous keys and noncanonical/out-of-bound protected dates."""
        seen = set()
        for row in rows:
            try:
                identity = tuple(row[k] for k in keys)
                if any(v is None or isinstance(v, (list, dict))
                       or (isinstance(v, float) and not math.isfinite(v)) for v in identity):
                    raise ValueError("null or non-scalar identity")
                if identity in seen:
                    raise ValueError("duplicate identity/key")
                seen.add(identity)
                for field in dates:
                    value = row[field]
                    if (not isinstance(value, str)
                            or datetime.date.fromisoformat(value).isoformat() != value
                            or value >= self.params["end_before"]):
                        raise ValueError("noncanonical or out-of-bound date")
            except (KeyError, TypeError) as error:
                raise ValueError("invalid identity/date contract") from error

    def _read(self, ctx, name, spec):
        """Supply the existing matching owner with globally unique bounded rows."""
        rows = []
        for index in range(len(spec["sources"])):
            rows.extend(self._load((name, index), ctx))
            if len(rows) > self.params["max_rows"]:
                raise ValueError("combined table row limit exceeded")
        self._check_rows(rows, spec["key_fields"], [spec["key_fields"][-1]])
        return rows, {"rows": len(rows), "sha256": value_hash(rows)}

    def _check_collisions(self, base):
        """Check all base fields and generated companions through the one owner."""
        columns = set().union(*(row.keys() for row in base))
        super()._check_collisions([dict.fromkeys(columns)])

    def validate_inputs(self, inputs):
        """Require a source node with no input ports.

        Parameters
        ----------
        inputs : dict
            Resolved graph inputs.

        Returns
        -------
        list
            A problem if a caller supplies any inputs.
        """
        return ["panel augmentation accepts no inputs"] if inputs else []

    def _publish(self, dest, rows, provenance):
        """Write in an owned sibling directory and publish while the caller holds its lock."""
        import pyarrow as pa
        import pyarrow.parquet as pq
        temp = Path(tempfile.mkdtemp(prefix=f".{dest.name}.partial-", dir=dest.parent))
        try:
            path = temp / self._PANEL
            pq.write_table(pa.Table.from_pylist(rows), path, compression="zstd")
            digest = path_hash(str(path))
            manifest = {"config_sha256": self._declared, "sources": self._source_report(),
                        "source_rows": self._counts, "rows": len(rows),
                        "panel": str(dest / self._PANEL), "panel_sha256": digest,
                        "files": {self._PANEL: digest}, "attachment": provenance}
            (temp / self._MANIFEST).write_text(json.dumps(manifest, sort_keys=True, indent=2,
                                                        allow_nan=False) + "\n")
            if sum(p.stat().st_size for p in temp.iterdir()) > self.params["max_output_bytes"]:
                raise ValueError("completed output byte limit exceeded")
            if dest.exists() or dest.is_symlink():
                raise FileExistsError(f"output already exists: {dest}")
            os.rename(temp, dest)
            return manifest
        finally:
            if temp.exists():
                shutil.rmtree(temp)

    def run(self, ctx, inputs):
        """Read, attach and publish while excluding concurrent cooperative writers.

        Parameters
        ----------
        ctx : NodeContext
            Passed to existing source readers.
        inputs : dict
            Must be empty.

        Returns
        -------
        dict
            Unchanged base records with new fields, and publication provenance.

        Raises
        ------
        ValueError, FileExistsError, OSError
            Contract violation, existing output or failed publication; no partial
            final directory is published.
        """
        import fcntl
        if self.validate_inputs(inputs):
            raise ValueError("panel augmentation accepts no inputs")
        dest, _ = self._preflight()
        self.fingerprint()
        dest.parent.mkdir(parents=True, exist_ok=True)
        with (dest.parent / f".{dest.name}.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self._preflight()
            self._counts = {}
            base = self._load("base", ctx)
            if not base:
                raise ValueError("empty base panel")
            self._check_rows(base, self.params["identity"], self.params["temporal_fields"])
            if any(any(row.get(key) is None for key in self.params["key"]) for row in base):
                raise ValueError("null or absent join key")
            attached = super().run(ctx, {"records": base})
            manifest = self._publish(dest, attached["records"], attached["provenance"])
            return {"records": attached["records"], "provenance": manifest}

    @classmethod
    def serving_effect(cls, params, verified_run_evidence):
        """Keep materialization outside serving graphs.

        Parameters
        ----------
        params, verified_run_evidence : dict
            Unused; this is an offline data preparation component.

        Returns
        -------
        str
            Always forbidden.
        """
        return "forbidden"
