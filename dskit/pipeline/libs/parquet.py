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
import fnmatch

from dskit.pipeline.base import value_hash
from dskit.pipeline.document import is_node_ref
from dskit.pipeline.kinds_flow import clause_holds, clause_problems
from dskit.pipeline.node import DEFAULT_NODE_KINDS, Node, reject_unknown_params

__all__ = ["DateBoundedParquet", "SQLiteParquetIndex", "NODE_KINDS", "ParquetFrameCache", "ParquetRows", "ParquetStreamRows", "register"]


def _sha256(path):
    """Return a file's sha256 hex digest by the pipeline's one file-hash rule (``path_hash``)."""
    import os

    from dskit.pipeline.workflow import path_hash
    if not os.path.isfile(path):
        raise FileNotFoundError(f"no file to hash at {path}")
    return path_hash(str(path))




class DateBoundedParquet:
    """Project a flat Parquet table behind ANDed calendar-date predicates.

    bounds maps source names to ISO start (inclusive) and/or end_before
    (exclusive). Date32/date64 and canonical date strings are supported.
    Missing/null temporal metadata refuses before scanning. Arrow evaluates
    predicates internally; excluded payload rows never become Python records.
    This is not byte-level file isolation or a concurrent-mutation defense.
    """

    @staticmethod
    def problems(bounds):
        """Accumulate malformed declarations, independently of the file schema."""
        if not isinstance(bounds, dict) or not bounds:
            return ["read_bounds must be a non-empty mapping"]
        problems = []
        for field, limits in bounds.items():
            if not isinstance(field, str) or not field:
                problems.append("read_bounds columns must be non-empty strings")
            if not isinstance(limits, dict) or not limits:
                problems.append(f"read_bounds[{field!r}] needs a bound")
                continue
            parsed = {}
            for key, value in limits.items():
                if key not in ("start", "end_before"):
                    problems.append(f"read_bounds[{field!r}]: unknown key {key!r}")
                    continue
                try:
                    day = datetime.date.fromisoformat(value)
                    if day.isoformat() != value:
                        raise ValueError(value)
                    parsed[key] = day
                except (TypeError, ValueError):
                    problems.append(f"read_bounds[{field!r}].{key} needs YYYY-MM-DD")
            if len(parsed) == 2 and parsed["start"] >= parsed["end_before"]:
                problems.append(f"read_bounds[{field!r}] is empty or reversed")
        return problems

    def __init__(self, path, columns, bounds):
        import copy

        problems = self.problems(bounds)
        if (not isinstance(columns, (list, tuple)) or not columns
                or not all(isinstance(c, str) and c for c in columns)
                or len(set(columns)) != len(columns)):
            problems.append("columns must be non-empty unique names")
        if problems:
            raise ValueError("; ".join(problems))
        self.path, self.columns, self.bounds = path, list(columns), copy.deepcopy(bounds)

    def _predicate(self):
        """Verify the temporal columns and return ``(path, columns, predicate)`` for the Arrow scan."""
        import pyarrow as pa
        import pyarrow.dataset as ds
        import pyarrow.parquet as pq

        checked = type(self)(self.path, self.columns, self.bounds)
        columns, bounds = checked.columns, checked.bounds
        parquet = pq.ParquetFile(self.path)
        schema = parquet.schema_arrow
        if (len(set(schema.names)) != len(schema.names)
                or any(pa.types.is_nested(f.type) for f in schema)):
            raise ValueError("temporal predicate requires flat unique schema")
        missing = sorted((set(columns) | set(bounds)) - set(schema.names))
        if missing:
            raise ValueError(f"temporal predicate or projection missing columns: {missing}")
        predicate = None
        for name, limits in bounds.items():
            dtype = schema.field(name).type
            if not (pa.types.is_date(dtype) or pa.types.is_string(dtype)
                    or pa.types.is_large_string(dtype)):
                raise ValueError(f"temporal predicate unsupported type: {name}")
            index = schema.get_field_index(name)
            for group in range(parquet.metadata.num_row_groups):
                stats = parquet.metadata.row_group(group).column(index).statistics
                if stats is None or stats.null_count != 0:
                    raise ValueError(f"null or unverified temporal predicate column: {name}")
            field = ds.field(name).cast(pa.date32())
            for key, value in limits.items():
                day = datetime.date.fromisoformat(value)
                part = field >= day if key == "start" else field < day
                predicate = part if predicate is None else predicate & part
        return columns, predicate

    def read(self):
        """Return the bounded Arrow table, refusing unverified temporal data."""
        import pyarrow.dataset as ds

        columns, predicate = self._predicate()
        return ds.dataset(self.path, format="parquet").to_table(
            columns=columns, filter=predicate)

    def batches(self, where=None, batch_size=65536, order_by=None):
        """Yield the bounded rows as Arrow record batches, optionally narrowed by one more predicate.

        The same temporal verification and bounds as :meth:`read`, but the table is never held whole:
        batches stream out of the scan, and ``where`` (a ``pyarrow.dataset`` expression, for example an
        entity equality or ``isin``) is ANDed with the bounds so Arrow prunes row groups by their statistics
        before decoding them.

        Parameters
        ----------
        where : pyarrow.dataset.Expression, optional
            An extra predicate; its columns need not be in ``columns``.
        batch_size : int
            Rows per batch (the last of a row group may be shorter).
        order_by : str, optional
            A projected column the stream must not decrease in. The row groups that survive the
            predicates are ranked by the bounded minimum and maximum of that column (one pass over the
            column alone), read in that order and each sorted by it before it is sliced into batches,
            so the physical row order of the file (an export written newest first, say) never reaches
            the caller. The column must be null-free among the kept rows, and two row groups whose
            bounded ranges interleave cannot be ordered this way: both refuse by name. One row group is
            held at a time.

        Yields
        ------
        pyarrow.RecordBatch
            Batches with exactly ``columns``.
        """
        import pyarrow.dataset as ds

        columns, predicate = self._predicate()
        if where is not None:
            predicate = where if predicate is None else predicate & where
        dataset = ds.dataset(self.path, format="parquet")
        if order_by is None:
            yield from dataset.scanner(columns=columns, filter=predicate, batch_size=batch_size).to_batches()
            return
        if order_by not in columns:
            raise ValueError(f"order_by must name a projected column: {order_by!r} is not in {columns}")
        for _, _, fragment in self._ranked_groups(dataset, predicate, order_by):
            table = fragment.to_table(columns=columns, filter=predicate).sort_by([(order_by, "ascending")])
            for start in range(0, table.num_rows, batch_size):
                yield from table.slice(start, batch_size).combine_chunks().to_batches()

    def _ranked_groups(self, dataset, predicate, name):
        """Return ``(min, max, fragment)`` of every non-empty row group, ranked by its bounded range of ``name``."""
        import pyarrow.compute as pc

        ranked = []
        for piece in dataset.get_fragments(filter=predicate):
            for fragment in piece.split_by_row_group(filter=predicate, schema=dataset.schema):
                column = fragment.to_table(columns=[name], filter=predicate).column(name)
                if column.null_count:
                    raise ValueError(f"{self.path}: order_by column {name!r} holds nulls; a null has no place in order")
                if column.length():
                    ranked.append((pc.min(column).as_py(), pc.max(column).as_py(), fragment))
        ranked.sort(key=lambda item: (item[0], item[1]))
        for (_, high, _), (low, _, _) in zip(ranked, ranked[1:]):
            if high > low:
                raise ValueError(f"{self.path}: row groups interleave in {name!r} ({high} after {low}); "
                                 "a file sorted neither way cannot be streamed in order")
        return ranked


class SQLiteParquetIndex:
    """Run-local source-ordered index over a bounded Parquet row stream.

    Parameters
    ----------
    descriptor : dict
        Completed run-local index descriptor returned by :meth:`build`.
    cache_kib : int, optional
        SQLite reader cache size in KiB.
    fetch_size : int, optional
        Maximum rows fetched per SQLite read.

    Examples
    --------
    Open a previously built run-local index::

        index = SQLiteParquetIndex(descriptor)
        index.close(verify=True)
    """

    @staticmethod
    def _ident(name):
        import re
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
            raise ValueError("unsafe row-index column")
        return '"' + name + '"'

    @staticmethod
    def _reuse_key(identity, columns, bounds, unique_by, partition_by,
                   float_keys, batch_size, cache_kib):
        """Bind a caller-verified source/filter/validator identity to index format."""
        import hashlib
        import json
        import re
        import sqlite3
        import sys
        import pyarrow
        from dskit.pipeline.workflow import path_hash

        source_digest = identity.get("verified_source_sha256") if isinstance(identity, dict) else None
        if (not isinstance(source_digest, str)
                or not re.fullmatch(r"[0-9a-f]{64}", source_digest)):
            raise ValueError("verified source digest required for index reuse")
        payload = {"identity": identity, "columns": list(columns), "bounds": bounds,
                   "unique_by": list(unique_by), "partition_by": list(partition_by),
                   "float_keys": list(float_keys), "batch_size": batch_size,
                   "cache_kib": cache_kib, "python": list(sys.version_info[:3]),
                   "sqlite": sqlite3.sqlite_version, "pyarrow": pyarrow.__version__,
                   "builder_sha256": path_hash(__file__)}
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    @staticmethod
    def _cache_blob(root, digest):
        """Return one content-addressed SQLite cache path."""
        return root / "objects" / digest[:2] / (digest + ".sqlite")

    @staticmethod
    def _cache_manifest(root, key):
        """Return one verified-input cache manifest path."""
        return root / "inputs" / key[:2] / (key + ".json")

    @staticmethod
    def _read_manifest(path):
        """Read one exact cache mapping without accepting links or partial data."""
        import json
        import re

        if not path.exists():
            return None
        if path.is_symlink() or not path.is_file():
            raise ValueError("invalid index cache manifest")
        try:
            record = json.loads(path.read_text())
        except (OSError, ValueError) as exc:
            raise ValueError("invalid index cache manifest") from exc
        if (not isinstance(record, dict) or set(record) != {"input_sha256", "sha256", "metadata"}
                or record["input_sha256"] != path.stem
                or not isinstance(record["sha256"], str)
                or not re.fullmatch(r"[0-9a-f]{64}", record["sha256"])
                or not isinstance(record["metadata"], dict)):
            raise ValueError("invalid index cache manifest")
        return record

    @staticmethod
    def _verify_cache_blob(blob, digest, metadata, input_key=None):
        """Verify cached bytes, descriptor and source-input provenance."""
        import json
        import sqlite3
        from dskit.pipeline.workflow import path_hash

        if (blob.is_symlink() or not blob.is_file() or blob.stat().st_mode & 0o222
                or path_hash(str(blob), evict=True) != digest):
            raise ValueError("index cache digest mismatch")
        connection = None
        try:
            connection = sqlite3.connect(blob.as_uri() + "?mode=ro&immutable=1", uri=True)
            row = connection.execute("SELECT value FROM metadata WHERE name='descriptor'").fetchone()
            if row is None or json.loads(row[0]) != metadata:
                raise ValueError("index cache metadata mismatch")
            if input_key is not None:
                row = connection.execute(
                    "SELECT value FROM metadata WHERE name='reuse_input_sha256'").fetchone()
                if row is None or row[0] != input_key:
                    raise ValueError("index cache input identity mismatch")
        except (sqlite3.DatabaseError, json.JSONDecodeError) as exc:
            raise ValueError("index cache metadata mismatch") from exc
        finally:
            if connection is not None:
                connection.close()

    @classmethod
    def _link_run(cls, blob, destination, validator=None, cache_kib=None):
        """Atomically install a cache inode at the run path; report cross-device misses."""
        import errno
        import os
        import uuid

        candidate = destination.with_name(destination.name + ".link-" + uuid.uuid4().hex)
        try:
            try:
                os.link(blob, candidate)
            except OSError as exc:
                if exc.errno == errno.EXDEV:
                    return False
                raise
            cls._replay_validator(blob, validator, cache_kib)
            os.replace(candidate, destination)
            return True
        finally:
            candidate.unlink(missing_ok=True)

    @staticmethod
    def _replay_validator(blob, validator, cache_kib):
        """Replay a row-mutation-free validator in stored source order."""
        import json
        import os
        import sqlite3

        if validator is None:
            return
        connection = sqlite3.connect(blob.as_uri() + "?mode=ro&immutable=1", uri=True)
        try:
            connection.execute("PRAGMA query_only=ON")
            connection.execute(f"PRAGMA cache_size=-{int(cache_kib)}")
            cursor = connection.execute("SELECT payload FROM rows ORDER BY ordinal")
            while True:
                rows = cursor.fetchmany(2048)
                if not rows:
                    break
                for (payload,) in rows:
                    validator(json.loads(payload))
        finally:
            connection.close()
            if hasattr(os, "posix_fadvise") and hasattr(os, "POSIX_FADV_DONTNEED"):
                try:
                    with open(blob, "rb") as handle:
                        os.posix_fadvise(handle.fileno(), 0, 0, os.POSIX_FADV_DONTNEED)
                except OSError:
                    pass

    @classmethod
    def _reuse_hit(cls, root, key, destination, expected, validator, cache_kib):
        """Verify an input-key hit and install it, or request a source rebuild."""
        record = cls._read_manifest(cls._cache_manifest(root, key))
        if record is None:
            return None
        metadata = record["metadata"]
        if (set(metadata) != set(expected) | {"rows"}
                or any(metadata[k] != value for k, value in expected.items())
                or type(metadata["rows"]) is not int or metadata["rows"] < 0):
            raise ValueError("index cache metadata mismatch")
        blob = cls._cache_blob(root, record["sha256"])
        if not blob.exists():
            return None
        cls._verify_cache_blob(blob, record["sha256"], metadata, key)
        if not cls._link_run(blob, destination, validator, cache_kib):
            return None
        return {**metadata, "path": str(destination), "sha256": record["sha256"]}

    @classmethod
    def _publish_cache(cls, root, key, destination, descriptor):
        """Publish one immutable blob, then an exclusive input-key mapping."""
        import errno
        import json
        import os
        import uuid

        digest = descriptor["sha256"]
        metadata = {k: v for k, v in descriptor.items() if k not in {"path", "sha256"}}
        blob = cls._cache_blob(root, digest)
        blob.parent.mkdir(parents=True, exist_ok=True)
        if blob.parent.stat().st_dev != destination.stat().st_dev:
            return
        created_blob = False
        try:
            os.link(destination, blob)
            created_blob = True
        except FileExistsError:
            cls._verify_cache_blob(blob, digest, metadata, key)
            if not cls._link_run(blob, destination):
                return
        except OSError as exc:
            if exc.errno == errno.EXDEV:
                return
            raise
        if key is None:
            return
        manifest_accepted = False
        try:
            manifest = cls._cache_manifest(root, key)
            manifest.parent.mkdir(parents=True, exist_ok=True)
            record = {"input_sha256": key, "sha256": digest, "metadata": metadata}
            candidate = manifest.with_name(manifest.name + ".building-" + uuid.uuid4().hex)
            try:
                with open(candidate, "x") as handle:
                    json.dump(record, handle, sort_keys=True, separators=(",", ":"))
                    handle.flush()
                    os.fsync(handle.fileno())
                candidate.chmod(0o444)
                try:
                    os.link(candidate, manifest)
                    manifest_accepted = True
                except FileExistsError:
                    if cls._read_manifest(manifest) != record:
                        raise ValueError("index cache input identity collision")
                    manifest_accepted = True
            finally:
                candidate.unlink(missing_ok=True)
        except BaseException:
            # A keyed blob cannot serve another input: its own bytes carry key.
            if created_blob and not manifest_accepted:
                blob.unlink(missing_ok=True)
            raise

    @classmethod
    def build(cls, source_path, index_path, columns, bounds, *, where=None,
              unique_by=(), partition_by=(), float_keys=(), validator=None,
              batch_size=8192, cache_kib=16384, reuse_root=None,
              reuse_identity=None, replay_validator=False):
        """Stream bounded Arrow batches to an atomic SQLite index.

        Parameters
        ----------
        source_path : str
            Parquet file to scan; its bytes must match ``reuse_identity`` when set.
        index_path : str
            Run-local SQLite destination.
        columns : sequence of str
            Exact projected fields in source order.
        bounds : dict
            Date bounds passed to the bounded Parquet reader.
        where : object, optional
            PyArrow predicate; a reuse identity must cover it completely.
        unique_by, partition_by, float_keys : sequence of str, optional
            Index key, partition and normalized float key fields.
        validator : callable, optional
            Called on every selected row before insertion in source order.
        batch_size, cache_kib : int, optional
            Bounded scan and SQLite cache sizes.
        reuse_root : str or Path, optional
            Absolute content-addressed cache root on the run filesystem.
        reuse_identity : dict, optional
            Caller-verified source SHA-256 and complete predicate, validator,
            code and dependency identity for a pre-build hit.
        replay_validator : bool, optional
            Certify that ``validator`` does not mutate row values; replay it on
            cached rows in source order before publishing a hit.

        Returns
        -------
        dict
            Run-local path, byte SHA-256 and embedded index metadata.

        Raises
        ------
        ValueError
            Invalid schema, unverified cache, or conflicting input identity.

        Notes
        -----
        Numeric ``float_keys`` match Python tuple-key equality. A cache hit
        skips the Parquet scan; callers must verify source bytes before and
        after a hit. A keyed miss rehashes the source after scanning, before
        publishing the index. Concurrent replacement and restoration during
        a scan is outside this check.
        """
        import datetime
        import json
        import math
        import os
        import sqlite3
        from pathlib import Path
        from dskit.pipeline.workflow import path_hash

        if (type(batch_size) is not int or not 1 <= batch_size <= 65536
                or type(cache_kib) is not int or not 1 <= cache_kib <= 65536
                or not columns or len(set(columns)) != len(columns)
                or not set(unique_by) <= set(columns)
                or not set(partition_by) <= set(columns)
                or not set(float_keys) <= set(unique_by)
                or not unique_by or not partition_by
                or type(replay_validator) is not bool
                or (reuse_identity is not None and reuse_root is None)
                or (reuse_identity is not None and not isinstance(reuse_identity, dict))
                or (replay_validator and reuse_identity is None)):
            raise ValueError("invalid row-index schema")
        for name in (*columns, *unique_by, *partition_by):
            cls._ident(name)
        destination = Path(index_path)
        root = None
        key = None
        verified_source_sha = None
        expected = {"version": 1, "columns": list(columns), "unique_by": list(unique_by),
                    "partition_by": list(partition_by), "float_keys": list(float_keys),
                    "batch_size": int(batch_size), "cache_kib": int(cache_kib)}
        if reuse_root is not None:
            root = Path(reuse_root)
            if not root.is_absolute():
                raise ValueError("absolute index reuse root required")
            if reuse_identity is not None:
                key = cls._reuse_key(reuse_identity, columns, bounds, unique_by,
                                     partition_by, float_keys, batch_size, cache_kib)
                verified_source_sha = reuse_identity["verified_source_sha256"]
        destination.parent.mkdir(parents=True, exist_ok=True)
        temp = destination.with_name(destination.name + ".building")
        for candidate in (temp, Path(str(temp)+"-journal"), Path(str(temp)+"-wal"), Path(str(temp)+"-shm")):
            if candidate.exists():
                candidate.unlink()
        if key is not None and (validator is None or replay_validator):
            hit = cls._reuse_hit(root, key, destination, expected, validator, cache_kib)
            if hit is not None:
                return hit
        conn = sqlite3.connect(str(temp))
        count = 0
        try:
            conn.execute("PRAGMA journal_mode=DELETE")
            conn.execute("PRAGMA synchronous=FULL")
            conn.execute("PRAGMA temp_store=FILE")
            conn.execute(f"PRAGMA cache_size=-{int(cache_kib)}")
            conn.execute("CREATE TABLE metadata(name TEXT PRIMARY KEY,value TEXT NOT NULL)")
            part_cols = ",".join(f"{cls._ident(k)} TEXT NOT NULL" for k in partition_by)
            conn.execute("CREATE TABLE rows(ordinal INTEGER PRIMARY KEY,unique_key TEXT UNIQUE,payload TEXT NOT NULL,"
                         + part_cols + ")")
            insert_cols = ["ordinal", "unique_key", "payload", *partition_by]
            insert = ("INSERT INTO rows(" + ",".join(cls._ident(k) for k in insert_cols)
                      + ") VALUES(" + ",".join("?" for _ in insert_cols) + ")")
            ordinal = 0
            from dskit.pipeline.libs.parquet import DateBoundedParquet
            for batch in DateBoundedParquet(source_path, columns, bounds).batches(
                    where=where, batch_size=batch_size):
                values = []
                for row in batch.to_pylist():
                    for name, value in tuple(row.items()):
                        if isinstance(value, (datetime.date, datetime.datetime)):
                            row[name] = value.isoformat()
                    if validator is not None:
                        validator(row)
                    identity = []
                    for name in unique_by:
                        value = row[name]
                        if name in float_keys:
                            if isinstance(value, bool) or not isinstance(value, (int, float)):
                                raise ValueError(f"invalid numeric index key: {name}")
                            value = float(value)
                            if not math.isfinite(value):
                                raise ValueError(f"invalid numeric index key: {name}")
                        identity.append(value)
                    unique_key = json.dumps(identity, separators=(",", ":"), allow_nan=False)
                    payload = json.dumps(row, separators=(",", ":"), allow_nan=True)
                    values.append((ordinal, unique_key, payload, *(str(row[k]) for k in partition_by)))
                    ordinal += 1
                if values:
                    conn.executemany(insert, values)
                    conn.commit()
                count += len(values)
            conn.execute("CREATE INDEX rows_partition_order ON rows("
                         + ",".join(cls._ident(k) for k in (*partition_by, "ordinal")) + ")")
            metadata = {**expected, "rows": count}
            conn.execute("INSERT INTO metadata VALUES('descriptor',?)",
                         (json.dumps(metadata, sort_keys=True, separators=(",", ":")),))
            if key is not None:
                conn.execute("INSERT INTO metadata VALUES('reuse_input_sha256',?)", (key,))
            conn.commit()
        except BaseException:
            conn.close()
            for candidate in (temp, Path(str(temp)+"-journal"), Path(str(temp)+"-wal"), Path(str(temp)+"-shm")):
                if candidate.exists():
                    candidate.unlink()
            raise
        conn.close()
        if root is None:
            os.replace(temp, destination)
            return {**metadata, "path": str(destination), "sha256": path_hash(str(destination))}
        try:
            if key is not None and path_hash(str(source_path), evict=True) != verified_source_sha:
                raise ValueError("source digest drift before index cache publication")
            descriptor = {**metadata, "path": str(destination),
                          "sha256": path_hash(str(temp), evict=True)}
            temp.chmod(0o444)
            cls._publish_cache(root, key, temp, descriptor)
            os.replace(temp, destination)
            return descriptor
        except BaseException:
            temp.unlink(missing_ok=True)
            raise

    def __init__(self, descriptor, *, cache_kib=16384, fetch_size=2048):
        import json
        import sqlite3
        from pathlib import Path
        from dskit.pipeline.workflow import path_hash

        if (type(cache_kib) is not int or not 1 <= cache_kib <= 65536
                or type(fetch_size) is not int or not 1 <= fetch_size <= 8192):
            raise ValueError("bounded SQLite row-index reader settings required")
        self.descriptor = dict(descriptor)
        self.path = str(Path(descriptor["path"]).resolve())
        self.digest = descriptor["sha256"]
        if path_hash(self.path, evict=True) != self.digest:
            raise ValueError("SQLite row index digest mismatch")
        uri = Path(self.path).as_uri() + "?mode=ro&immutable=1"
        self.connection = sqlite3.connect(uri, uri=True)
        self.connection.execute("PRAGMA query_only=ON")
        self.connection.execute(f"PRAGMA cache_size=-{int(cache_kib)}")
        row = self.connection.execute("SELECT value FROM metadata WHERE name='descriptor'").fetchone()
        if row is None or json.loads(row[0]) != {k: descriptor[k] for k in (
                "version", "columns", "unique_by", "partition_by", "float_keys",
                "rows", "batch_size", "cache_kib")}:
            self.connection.close()
            raise ValueError("SQLite row index descriptor mismatch")
        self.fetch_size = int(fetch_size)

    def items(self, partition):
        """Yield source-ordered rows for one exact partition using bounded fetches."""
        import json
        fields = self.descriptor["partition_by"]
        if not isinstance(partition, dict) or set(partition) != set(fields):
            raise ValueError("exact row-index partition required")
        clause = " AND ".join(self._ident(k) + "=?" for k in fields)
        cursor = self.connection.execute(
            "SELECT payload FROM rows WHERE " + clause + " ORDER BY ordinal",
            tuple(str(partition[k]) for k in fields))
        while True:
            chunk = cursor.fetchmany(self.fetch_size)
            if not chunk:
                break
            for (payload,) in chunk:
                yield json.loads(payload)

    def iter_partition(self, **partition):
        """Yield rows for a named exact partition in physical source order."""
        return self.items(partition)

    def get(self, key):
        """Get one exact row by its normalized identity."""
        import json
        fields = self.descriptor["unique_by"]
        if not isinstance(key, dict) or set(key) != set(fields):
            raise ValueError("exact row-index identity required")
        values = []
        float_keys = set(self.descriptor["float_keys"])
        for name in fields:
            value = float(key[name]) if name in float_keys else key[name]
            values.append(value)
        token = json.dumps(values, separators=(",", ":"), allow_nan=False)
        found = self.connection.execute(
            "SELECT payload FROM rows WHERE unique_key=?", (token,)).fetchone()
        return None if found is None else json.loads(found[0])

    def verify_digest(self):
        """Recheck the immutable index file at a study/replay boundary."""
        from dskit.pipeline.workflow import path_hash
        if path_hash(self.path, evict=True) != self.digest:
            raise ValueError("SQLite row index drift")

    def close(self, *, verify=False):
        """Close the reader and optionally recheck its byte digest.

        Parameters
        ----------
        verify : bool, optional
            Rehash the index after closing the SQLite connection.
        """
        self.connection.close()
        if verify:
            self.verify_digest()


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
        rows whose date lies inside; absent or None keeps every row. This legacy
        window is NOT a protected-read boundary. Optional read_bounds maps SOURCE
        columns to start inclusive / end_before exclusive ISO dates, applying the
        shared DateBoundedParquet gate before materialization. Bounds must
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
    _PARAMS = _REQUIRED + ("window", "read_bounds")
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
        bounds = params.get("read_bounds")
        if "read_bounds" in params and not is_node_ref(bounds):
            problems += DateBoundedParquet.problems(bounds)
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
        schema = pq.read_schema(path)
        missing = sorted(set(columns) - set(schema.names))
        if missing:
            raise ValueError(
                f"{self.key}: {rel!r} has no column(s) {missing}; it has {schema.names}"
            )
        if "read_bounds" in self.params:
            table = DateBoundedParquet(path, list(columns), self.params["read_bounds"]).read()
        else:
            table = pq.read_table(path, columns=list(columns))
        data = {out: table.column(src).to_pylist() for src, out in columns.items()}
        names = list(data)
        records = [dict(zip(names, row)) for row in zip(*data.values())]
        records = self._windowed(records)
        self.log.info("read %d row(s) of %s", len(records), rel)
        return {"records": records}


class ParquetStreamRows(Node):
    """Read every Parquet member of one or more onboarded snapshots.

    Each source declares its own projection and non-empty protected date
    bounds.  Each explicit manifest pin resolves one snapshot, every member
    is hash-checked, and :class:`DateBoundedParquet` applies the bounds before
    records are materialized.  Sources are read in declared order and members
    in lexical manifest order; their records are concatenated in that order.
    This node has no cross-source deduplication or latest-vintage claim: roots
    are independent snapshots and a caller that needs a winner must name that
    authority upstream. An optional per-source ``include`` (a non-empty list of glob patterns on the member
    path inside the stream, e.g. ``"*/underlying_prices.parquet"``) reads only the matching members, for a
    snapshot that also holds non-Parquet files such as raw API responses; it must match at least one member
    (ADR-0265 Amendment 5). An optional per-source ``iso_fields`` (output field names) turns date and datetime
    cells of those fields into ISO text (a Parquet ``date32`` column arrives as ``datetime.date``, and the
    flow and statistics nodes key on ISO text); a null stays null. An optional per-source ``where`` (clauses
    ``{field, op, value}`` on the OUTPUT field names, the ``filter`` kind's grammar) keeps only the rows that pass
    every clause, applied one file at a time so the dropped rows never accumulate in memory.
    """

    role = "data"
    outputs = ("records",)
    _PARAMS = ("sources",)
    _SOURCE_KEYS = ("root", "source", "stream", "snapshot", "columns", "read_bounds", "include", "iso_fields", "where")

    @classmethod
    def validate_params(cls, params):
        """Return declaration problems without opening an onboarded payload."""
        problems = []
        reject_unknown_params(problems, params, cls._PARAMS)
        sources = params.get("sources")
        if not isinstance(sources, (list, tuple)) or not sources:
            return problems + ["sources is required: a non-empty list of source declarations"]
        for number, source in enumerate(sources):
            prefix = f"sources[{number}]"
            if not isinstance(source, dict):
                problems.append(f"{prefix} must be a mapping")
                continue
            unknown = sorted(set(source) - set(cls._SOURCE_KEYS))
            problems += [f"{prefix}: unknown key {key!r}" for key in unknown]
            for key in ("root", "source", "stream"):
                if not isinstance(source.get(key), str) or not source[key]:
                    problems.append(f"{prefix}.{key} must be a non-empty string")
            pin = source.get("snapshot")
            if (not isinstance(pin, str) or len(pin) != 64
                    or any(c not in "0123456789abcdef" for c in pin)):
                problems.append(f"{prefix}.snapshot must be a lowercase SHA256 manifest pin")
            columns = source.get("columns")
            if (not isinstance(columns, dict) or not columns
                    or not all(isinstance(k, str) and k and isinstance(v, str) and v
                               for k, v in columns.items())
                    or len(set(columns.values())) != len(columns)):
                problems.append(f"{prefix}.columns must map unique output names from non-empty source names")
            problems += [f"{prefix}.{problem}" for problem in
                         DateBoundedParquet.problems(source.get("read_bounds"))]
            include = source.get("include")
            if "include" in source and (not isinstance(include, list) or not include or not all(
                    isinstance(pattern, str) and pattern for pattern in include)):
                problems.append(f"{prefix}.include must be a non-empty list of glob patterns on the member path")
            where = source.get("where")
            if "where" in source:
                if not isinstance(where, list) or not where:
                    problems.append(f"{prefix}.where must be a non-empty list of {{field, op, value}} clauses")
                else:
                    outputs = set(columns.values()) if isinstance(columns, dict) else set()
                    for n, clause in enumerate(where):
                        problems += [f"{prefix}.where[{n}]: {p}" for p in clause_problems(f"where[{n}]", clause)]
                        if isinstance(clause, dict) and outputs and clause.get("field") not in outputs:
                            problems.append(f"{prefix}.where[{n}]: field {clause.get('field')!r} is not an output field of columns")
            iso = source.get("iso_fields")
            if "iso_fields" in source and (not isinstance(iso, list) or not iso or not all(
                    isinstance(name, str) and name for name in iso)
                    or (isinstance(columns, dict) and not set(iso) <= set(columns.values()))):
                problems.append(f"{prefix}.iso_fields must be a non-empty list of output field names from columns")
        return problems

    def _snapshots(self):
        """Resolve explicit, verified manifests; never infer a latest inventory."""
        from pathlib import Path

        from dskit.onboarding.observations import verified_payload_dir
        from dskit.onboarding.snapshot import read_manifest, snapshot_hash

        snapshots = []
        for source in self.params["sources"]:
            payload = Path(verified_payload_dir(
                source["root"], source["snapshot"], source["stream"]))
            snapshot_dir = payload.parent.parent
            manifest = read_manifest(str(snapshot_dir))
            if snapshot_hash(manifest) != source["snapshot"]:
                raise ValueError(f"{self.key}: pinned manifest identity changed")
            if manifest["source"] != source["source"]:
                raise ValueError(f"{self.key}: snapshot belongs to another source")
            prefix = f"{source['stream']}/"
            patterns = source.get("include")
            files = sorted(
                (entry["relpath"][len(prefix):],
                 payload / entry["relpath"][len(prefix):], entry["sha256"])
                for entry in manifest["files"]
                if entry["relpath"].startswith(prefix)
                and (patterns is None or any(fnmatch.fnmatchcase(entry["relpath"][len(prefix):], pattern)
                                             for pattern in patterns)))
            if not files:
                raise ValueError(f"{self.key}: pinned stream has no manifest members"
                                 + (f" matching include {patterns}" if patterns else ""))
            snapshots.append({
                "source": source["source"], "stream": source["stream"],
                "snapshot": snapshot_dir.name, "manifest_sha256": source["snapshot"],
                "columns": source["columns"], "read_bounds": source["read_bounds"],
                "iso_fields": tuple(source.get("iso_fields", ())), "where": source.get("where", []), "files": files})
        return snapshots

    @staticmethod
    def _identity(snapshots):
        """Return the manifest-level identity, deliberately excluding local paths."""
        return [{"source": snap["source"], "stream": snap["stream"],
                 "snapshot": snap["snapshot"], "manifest_sha256": snap["manifest_sha256"],
                 "members": [(rel, digest) for rel, _path, digest in snap["files"]]}
                for snap in snapshots]

    def fingerprint(self):
        """Pin every source snapshot and every manifest member digest."""
        if getattr(self, "_pinned", None) is None:
            self._pinned = self._snapshots()
        items = self._identity(self._pinned)
        return {"kind": type(self).__name__, "snapshots": [
            {"source": item["source"], "stream": item["stream"], "snapshot": item["snapshot"],
             "manifest_sha256": item["manifest_sha256"],
             "members": [rel for rel, _digest in item["members"]]} for item in items],
                "sha256": value_hash(items)}

    def run(self, ctx, inputs):
        """Verify pinned members, bound them in Arrow, then emit projected records."""
        pinned = getattr(self, "_pinned", None)
        snapshots = self._snapshots()
        if pinned is not None and self._identity(snapshots) != self._identity(pinned):
            raise ValueError(f"{self.key}: source snapshot changed between fingerprint and run")
        records = []
        for snapshot in snapshots:
            for rel, path, digest in snapshot["files"]:
                if not self._verified_bytes_ok(path, digest):
                    raise ValueError(f"{self.key}: {rel!r} no longer matches manifest sha256 {digest}")
                columns = snapshot["columns"]
                table = DateBoundedParquet(path, list(columns), snapshot["read_bounds"]).read()
                data = {out: table.column(src).to_pylist() for src, out in columns.items()}
                for name in snapshot["iso_fields"]:
                    data[name] = [v.isoformat() if hasattr(v, "isoformat") else v for v in data[name]]
                for clause in snapshot["where"]:
                    sample = next((v for v in data[clause["field"]] if v is not None), None)    # the first non-null value
                    if sample is not None and hasattr(sample, "isoformat"):
                        raise ValueError(f"{self.key}: where reads {clause['field']!r}, a date column: list it in iso_fields "
                                         "so the clause compares ISO text (a date against text drops every row)")
                rows = (dict(zip(data, row)) for row in zip(*data.values()))
                records.extend(row for row in rows if all(clause_holds(row, c) for c in snapshot["where"]))
        return {"records": records}

    _verified_bytes_ok = staticmethod(ParquetRows._verified_bytes_ok)


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

    def load(self, identity):
        """Return ``(frame, payload)`` stored under ``identity``, or None.

        Parameters
        ----------
        identity : dict
            JSON-serializable; compared by its canonical digest.

        Returns
        -------
        tuple or None
            None when the slot is empty, holds another identity, its record
            is unreadable, or its frame no longer hashes to the recorded digest
            or does not parse.
        """
        import json

        import pandas as pd

        record, frame = self.directory/self.RECORD, self.directory/self.FRAME
        if not (record.is_file() and frame.is_file()):
            return None
        try:
            stored = json.loads(record.read_text())
        except ValueError:
            return None
        if (not isinstance(stored, dict) or "payload" not in stored
                or stored.get("identity_sha256") != value_hash(identity)
                or _sha256(frame) != stored.get("frame_sha256")):
            return None
        try:
            return pd.read_parquet(frame), stored["payload"]
        except (OSError, ValueError, TypeError, NotImplementedError):
            return None   # bytes that hash right but do not parse are a miss, never a crash

    @classmethod
    def storable(cls, frame):
        """Say whether parquet hands ``frame`` back unchanged.

        Every column label must be a unique string and every object column
        scalar: parquet returns a container's lists as arrays.

        Parameters
        ----------
        frame : DataFrame

        Returns
        -------
        bool
        """
        from pandas.api.types import infer_dtype
        return (frame.columns.is_unique and all(isinstance(n, str) for n in frame.columns)
                and all(infer_dtype(frame[name], skipna=True) in cls.SCALAR_KINDS
                        for name in frame.columns if frame[name].dtype == object))

    def store(self, identity, frame, payload):
        """Replace the slot with ``frame`` and ``payload`` under ``identity``.

        Parameters
        ----------
        identity : dict
            JSON-serializable; recorded by its canonical digest.
        frame : DataFrame
            A :meth:`storable` frame, written as parquet without its index.
        payload : dict
            JSON-serializable; stored and returned as JSON gives it back.

        Raises
        ------
        ValueError
            When ``frame`` is not :meth:`storable`; whatever writing raises,
            with no partial file left behind.
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
        try:
            frame.to_parquet(partial, index=False)
            digest = _sha256(partial)  # of these bytes: another writer's frame cannot pass load
            os.replace(partial, self.directory/self.FRAME)
        finally:
            if os.path.exists(partial):
                os.unlink(partial)
        atomic_write(str(self.directory/self.RECORD), json.dumps({
            "identity_sha256": value_hash(identity), "frame_sha256": digest,
            "payload": payload}, indent=1, allow_nan=False).encode())

    def load_or_build(self, identity, build):
        """Return the stored frame for the current identity, building and storing it if absent.

        A memo degrades, it never fails a caller: a frame it cannot store is
        handed back as built.

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
            :meth:`storable`, the identity moved, writing failed, or another
            writer took the slot).
        """
        before = identity()
        held = self.load(before)
        if held is not None:
            return (*held, "reused")
        frame, payload = build()
        if self.storable(frame) and value_hash(identity()) == value_hash(before):
            try:
                self.store(before, frame, payload)
                held = self.load(before)
            except (OSError, ValueError, TypeError, NotImplementedError):
                held = None   # parquet refused a column or a read-back, or the disk is full
            if held is not None:
                return (*held, "stored")
        return frame, self.as_stored(payload), "unstored"

    @staticmethod
    def as_stored(payload):
        """Return ``payload`` as the slot gives it back: through JSON (tuples become lists).

        Parameters
        ----------
        payload : dict
            JSON-serializable.

        Returns
        -------
        dict
        """
        import json
        return json.loads(json.dumps(payload))

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

        return value_hash([[package.__name__, path.relative_to(top).as_posix(), _sha256(path)]
                           for package in packages
                           for top in [Path(package.__file__).parent]
                           for path in sorted(top.rglob("*.py"))])


#: The pack's kinds.
NODE_KINDS = (("parquet-rows", ParquetRows),
              ("parquet-stream-rows", ParquetStreamRows))


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
