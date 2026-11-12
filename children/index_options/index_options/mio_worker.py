"""One MIO method of one quarter-arm, solved on any host from a self-contained bundle (ADR-0297).

A quarter-arm MIO document is a registry run that can only exist on the
host whose absolute code pins and registry path it names. The largest
registry-free unit inside it is one method: its weeks are sequential, but
methods share only read-only inputs. This module moves exactly that unit.

``MIOMethodBundle`` (the WSL side) copies the study's pinned code files and
pinned source snapshots under one root, rewrites the study's code pins and
source pin roots to that root, saves the WSL-built plan and a SHA-256
manifest of every file. ``MIOMethodWorker`` (any host) verifies the manifest,
the dependency pins and the rewritten code pins with the same
``validate_implementation`` the document applies, reads the bounded inputs
with byte verification, solves one method and writes an action file: the
integer actions and solver certificates per decision week, the hashes that
tie them to the plan, study and bounded inputs, and the host's identity.

The accepting document re-derives every reserve, value, cleanup and gap from
those integer actions with its own floats (``TransportPortfolioSolve.derived``)
and then replays that result exactly, so cross-host bit equality is not
required. The action file DOES carry each week's solver certificate, so an
objective and a bound, in the clear on disk (it goes only into sealed, owner-only
directories); WSL does not verify that certificate's optimality. Nothing is
shown: the worker writes no result file, ``--print`` shows two hashes only,
a failure prints one scrubbed line (no traceback, no number), and progress
lines carry a stamp, a week count and a status word.

ADR-0293: a bundle or worker never reads forecast payloads of a blinded-cache
study, because only the registry document can verify them against an active
permit. Such a study is refused here before any payload byte is touched.
"""

import argparse
import copy
import hashlib
import json
import os
import platform
import re
import shutil
import socket
import sys
import threading
import time
from pathlib import Path

from dskit.onboarding.artifacts import PinnedPayloadSource
from dskit.pipeline.base import value_hash
from dskit.pipeline.node import atomic_write
from dskit.pipeline.study_protocol import StudyContract
from dskit.pipeline.workflow import path_hash

from .observations import MIOPacketInputs

__all__ = ["MIOMethodBundle", "MIOMethodWorker", "Heartbeat", "PayloadCapError", "BUNDLE_TOKEN", "THREAD_VARIABLES",
           "STAMP_FORMAT", "stamp", "main"]

#: The one UTC stamp format of every progress line and queue timestamp (the coordinator parses it too).
STAMP_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
#: Predictive policy whose forecast payloads only an active-permit verification may read (ADR-0293).
BLINDED_POLICY = "owner_selected_blinded_cache"

#: Every numerical thread pool a worker pins to one thread before importing numpy.
THREAD_VARIABLES = ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS",
                    "VECLIB_MAXIMUM_THREADS", "NUMEXPR_NUM_THREADS")
#: The root a bundle's study.json names; a worker relocates it to the real bundle root, so one
#: bundle (one manifest digest) serves every host root.
BUNDLE_TOKEN = "/__MIO_BUNDLE__"


class PayloadCapError(ValueError):
    """A bundle would ship a date on or after the owner cap (ADR-0297: nothing dated 2024 or later)."""


def stamp(now=None):
    """Format epoch seconds (default now) as the one UTC stamp, ``YYYY-MM-DDTHH:MM:SSZ``."""
    return time.strftime(STAMP_FORMAT, time.gmtime(now))


def _refuse_blinded(study):
    """Refuse a blinded-cache study before any payload is read (ADR-0293)."""
    predictive = study.get("domain", {}).get("predictive")
    if isinstance(predictive, dict) and predictive.get("policy") == BLINDED_POLICY:
        raise ValueError("blinded-cache study refused (ADR-0293): forecast payloads are read only after an "
                         "active-permit verification, which a bundle or worker cannot hold")


class Heartbeat:
    """Call ``beat`` now and then every ``interval`` seconds on a daemon thread until the block exits.

    The thread cannot tell whether the main thread still works, so every stage has a hard
    cap: after ``max_seconds`` it stops beating, the progress token stops changing and the
    coordinator's stale limit applies to a hung stage.

    Parameters
    ----------
    beat : callable
        Zero-argument function (it writes one progress line).
    interval : float
        Seconds between beats.
    max_seconds : float or None
        Stop beating this long after the start (None: never stop).

    Examples
    --------
    ::

        with Heartbeat(lambda: print("alive"), 60.):
            build_a_slow_index()
    """

    def __init__(self, beat, interval, max_seconds=None):
        self.beat, self.interval, self.max_seconds = beat, interval, max_seconds
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True)

    def _loop(self):
        started = time.monotonic()
        while True:
            if self.max_seconds is None or time.monotonic() - started < self.max_seconds:
                try:
                    self.beat()
                except Exception:
                    pass
            if self._stop.wait(self.interval):
                return

    def __enter__(self):
        """Start beating."""
        self._thread.start()
        return self

    def __exit__(self, kind, value, tb):
        """Stop beating and wait for the thread."""
        self._stop.set()
        self._thread.join()
        return False


def _sha256_bytes(raw):
    """Hex SHA-256 of bytes."""
    return hashlib.sha256(raw).hexdigest()


def _canonical(body):
    """Encode an action file as canonical JSON bytes (sorted keys, NaN refused)."""
    return json.dumps(body, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


class MIOMethodBundle:
    """A self-contained copy of one study's code and pinned inputs for a worker.

    Parameters
    ----------
    study : dict
        The executable study as the WSL document carries it; its code pins
        and source pins must resolve on this host.
    root : str or pathlib.Path
        Destination directory (created; symlink-free, since every code pin
        must resolve to itself on the worker).

    Examples
    --------
    Stage a bundle and read back its manifest digest::

        bundle = MIOMethodBundle(study, "/mnt/d/mio-scratch/bundles/2022q1-rules")
        manifest = bundle.build()
        manifest["sha256"]  # the bundle_manifest_sha256 every action file records
    """

    CODE_DIR = "code"
    SOURCES_DIR = "sources"
    STUDY_FILE = "study.json"
    PLAN_FILE = "plan.json"
    MANIFEST_FILE = "manifest.json"

    def __init__(self, study, root):
        if not isinstance(study, dict):
            raise ValueError("executable study required")
        self.study = copy.deepcopy(study)
        self.root = Path(root).resolve()
        self.metadata_stamp_columns = {}  # relative payload path -> exempted pull-stamp columns (written to identity.json)
        self.emptied_payload_files = []  # relative payload paths shipped as empty tables by the date cut (written to identity.json)

    IDENTITY_FILE = "identity.json"
    #: String columns that carry an ISO date; date and timestamp typed columns are found by type.
    DATE_COLUMNS = ("date", "quote_date", "expiry", "expiration", "settlement_date")

    #: Metadata columns that carry a PULL stamp (when the file was made or acquired, not a market date). Real files
    #: hold 2026 stamps in them, so they are never cut and never scanned; any other column that names a date at or
    #: past the cap still refuses (``known_at`` is NOT here: in this repo it can be a point-in-time availability date, so it is
    #: cut or refused like any date column). An explicit, named list: a column joins it only by editing this tuple. Each file's
    #: exempted columns are recorded in ``identity.json`` (``metadata_stamp_columns``), never silently.
    METADATA_STAMP_COLUMNS = ("created_at", "acquired_at")

    #: Payload suffixes read as text and scanned for any ISO date (``refuse_dates_from``); any other suffix but
    #: ``.parquet`` cannot be checked here and refuses, so an unrecognised file never ships whole by default.
    TEXT_SUFFIXES = (".json", ".jsonl", ".csv", ".tsv", ".txt")

    @classmethod
    def _column_roles(cls, schema):
        """Sort a parquet schema's columns into ``(cut, scan)`` name lists, or refuse a type it cannot check.

        ``cut``: date or timestamp typed columns and the string columns named in ``DATE_COLUMNS``; a
        dictionary column is read through its value type. ``scan``: every other string column, searched for
        an ISO date text. Integer, floating, boolean, decimal, time, duration and null columns cannot name an
        ISO date and are left alone. Any other type (list, struct, map, binary, union, extension, ...) cannot
        be checked here, so it refuses: the cut fails closed on a type it does not know.
        """
        import pyarrow as pa

        cut, scan = [], []
        for field in schema:
            stamp = field.name in cls.METADATA_STAMP_COLUMNS  # a pull stamp, not a market date; its TYPE is still checked
            kind = field.type.value_type if pa.types.is_dictionary(field.type) else field.type
            text = (pa.types.is_string(kind) or pa.types.is_large_string(kind)
                    or (hasattr(pa.types, "is_string_view") and pa.types.is_string_view(kind)))
            if pa.types.is_date(kind) or pa.types.is_timestamp(kind) or (text and field.name in cls.DATE_COLUMNS):
                if not stamp:
                    cut.append(field.name)
            elif text:
                if not stamp:
                    scan.append(field.name)
            elif not (pa.types.is_integer(kind) or pa.types.is_floating(kind) or pa.types.is_boolean(kind)
                      or pa.types.is_decimal(kind) or pa.types.is_time(kind) or pa.types.is_duration(kind)
                      or pa.types.is_null(kind)):
                raise PayloadCapError(f"payload column {field.name!r} has type {field.type}: "
                                      "it cannot be checked for dates, so it cannot be shipped")
        return cut, scan

    @classmethod
    def metadata_stamps(cls, schema):
        """The columns of a parquet schema that ``METADATA_STAMP_COLUMNS`` exempts from the date cut, in file order."""
        return [field.name for field in schema if field.name in cls.METADATA_STAMP_COLUMNS]

    @staticmethod
    def _before_cap(column, cap):
        """Boolean mask: the rows of a date, timestamp or date-text column strictly before ``cap`` (nulls kept).

        A text column must hold ISO dates (``YYYY-MM-DD``, optionally followed by ``T`` or a space and an ``hh:mm[:ss[.f]]`` time and a zone) in every
        non-null row, else it refuses: strings compare lexicographically, so ``12/31/2019`` would pass the cut and
        ``Jan 5 2025`` fall out of it by accident.

        A zoned timestamp is cut on BOTH its UTC instant and its own wall-clock date, so a row dated
        2023-12-31 in New York but 2024-01-01 in UTC does not ship. A zone this host cannot resolve
        raises ``PayloadCapError`` (the arm then runs as a plain WSL document).
        """
        import datetime
        import pyarrow as pa
        import pyarrow.compute as pc

        if pa.types.is_dictionary(column.type):
            column = column.dictionary_decode()
        if pa.types.is_timestamp(column.type) and column.type.tz is not None:
            try:
                moment = datetime.datetime.fromisoformat(cap)
                instant = pa.scalar(moment, pa.timestamp(column.type.unit, tz="UTC")).cast(column.type)
                wall = pa.scalar(moment, pa.timestamp(column.type.unit))
                inside = pc.and_(pc.less(column, instant), pc.less(pc.local_timestamp(column), wall))
            except (pa.ArrowException, ValueError) as exc:
                raise PayloadCapError(f"a zoned timestamp column cannot be cut at {cap} here: {type(exc).__name__}")
            return pc.fill_null(inside, True)
        if pa.types.is_string(column.type) or pa.types.is_large_string(column.type) \
                or (hasattr(pa.types, "is_string_view") and pa.types.is_string_view(column.type)):
            iso = pc.match_substring_regex(column, r"^\d{4}-\d{2}-\d{2}([T ]\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:?\d{2})?)?$")
            if not pc.all(pc.fill_null(iso, True)).as_py():
                raise PayloadCapError("a date column holds text that is not an ISO date (YYYY-MM-DD): "
                                      "it cannot be cut by comparing strings")
        inside = pc.fill_null(pc.less(column, pa.scalar(cap).cast(column.type)), True)
        if pa.types.is_string(column.type) or pa.types.is_large_string(column.type) \
                or (hasattr(pa.types, "is_string_view") and pa.types.is_string_view(column.type)):
            zoned = pc.fill_null(pc.match_substring_regex(column, r"[T ]\d{2}:\d{2}.*(Z|[+-]\d{2}:?\d{2})$"), False)
            if pc.any(zoned).as_py():
                # Text with a zone is cut like a zoned timestamp: its wall date (compared above) AND its UTC instant.
                limit = datetime.datetime.fromisoformat(cap).replace(tzinfo=datetime.timezone.utc)
                utc_inside = []
                for value, is_zoned in zip(column.to_pylist(), zoned.to_pylist()):
                    try:
                        utc_inside.append(True if not is_zoned else
                                          datetime.datetime.fromisoformat(value).astimezone(datetime.timezone.utc) < limit)
                    except ValueError:
                        raise PayloadCapError("a date column holds a zoned time this host cannot read") from None
                inside = pc.and_(inside, pa.array(utc_inside, pa.bool_()))
        return inside

    @classmethod
    def cut_parquet(cls, source, destination, cap):
        """Write ``destination`` with only the rows whose every date column is before ``cap``.

        Row order and the schema are kept, so a bounded read of the cut file returns the same rows in
        the same order as the original for any bound at or before ``cap``. Returns False, writing
        nothing, when no row is cut. A file whose every row is cut by a recognised market-date column
        (``DATE_COLUMNS``, exact names) is written as an empty table with its schema (the caller records it
        in ``identity.json``); one emptied by any other column refuses. Date columns are those of date or timestamp type (zoned or not,
        dictionary encoded or not) and the string columns named in ``DATE_COLUMNS``. Any OTHER string
        column that names an ISO date on or after ``cap`` in a row that is kept cannot be cut by name,
        so it refuses; so does a column of a type that cannot be checked at all (fail closed).

        Raises
        ------
        PayloadCapError
            A kept row names such a date in a string column ``DATE_COLUMNS`` does not know, a column's
            type cannot be checked, or a zone cannot be resolved here.
        """
        import pyarrow as pa
        import pyarrow.compute as pc
        import pyarrow.parquet as pq

        parquet = pq.ParquetFile(source)
        columns, others = cls._column_roles(parquet.schema_arrow)
        if not columns and not others:
            return False
        temporary, writer, removed, rows, named_kept = Path(str(destination) + ".cut"), None, 0, 0, 0
        named = [name for name in columns if name in cls.DATE_COLUMNS]  # a recognised market-date column, exact name
        try:
            for batch in parquet.iter_batches(batch_size=65536, columns=None if columns else others):
                keep, by_name = None, None  # by_name: the rows the recognised market-date columns alone keep
                for name in columns:
                    try:
                        before = cls._before_cap(batch.column(name), cap)
                    except PayloadCapError as exc:
                        raise PayloadCapError(f"payload column {name!r}: {exc}") from None
                    keep = before if keep is None else pc.and_(keep, before)
                    if name in named:
                        by_name = before if by_name is None else pc.and_(by_name, before)
                kept = batch if keep is None else batch.filter(keep)
                if named:
                    named_kept += len(batch.filter(by_name))
                for name in others:
                    text = kept.column(name)
                    text = text.dictionary_decode() if pa.types.is_dictionary(text.type) else text
                    dates = pc.struct_field(pc.extract_regex(text, r"(?P<d>\d{4}-\d{2}-\d{2})"), "d")
                    late = pc.any(pc.fill_null(pc.greater_equal(dates, pa.scalar(cap)), False)).as_py()
                    if not late:  # a value with several dates: every one of them counts, not only the first
                        many = pc.greater(pc.count_substring_regex(text, r"\d{4}-\d{2}-\d{2}"), 1)
                        late = any(d >= cap for v in text.filter(pc.fill_null(many, False)).to_pylist()
                                   for d in re.findall(r"\d{4}-\d{2}-\d{2}", v))
                    if late:
                        raise PayloadCapError(f"payload column {name!r} names a date on or after {cap}: "
                                              "it is not a known date column, so it cannot be cut")
                if not columns:
                    continue
                removed += len(batch) - len(kept)
                rows += len(batch)
                writer = writer or pq.ParquetWriter(temporary, parquet.schema_arrow)
                writer.write_batch(kept)
            if writer is not None:
                writer.close()
                writer = None
            if removed and removed == rows and not (named and named_kept == 0):
                # Emptied by a column this class does not recognise as a market date (a case variant such as
                # ``Created_At``, ``known_at``, any other name): that is not a market-date cut, so it refuses.
                raise PayloadCapError(f"every row of the payload file is dated on or after {cap}, but not by a recognised "
                                      f"market-date column ({', '.join(cls.DATE_COLUMNS)}): cutting it would ship an empty file")
            if not removed:
                temporary.unlink(missing_ok=True)
                return False
            os.replace(temporary, destination)
            return True
        finally:
            if writer is not None:
                writer.close()
            temporary.unlink(missing_ok=True)

    @staticmethod
    def refuse_dates_from(path, cap, label):
        """Refuse a text payload (JSON, CSV, ...) that names a date on or after ``cap`` (it cannot be cut row by row).

        Raises
        ------
        PayloadCapError
            The file names such a date, or is too large to check.
        """
        if os.path.getsize(path) > 256 * 2**20:
            raise PayloadCapError(f"payload {label} is too large to check for dates on or after {cap}")
        with open(path, "rb") as handle:
            if any(m.group().decode() >= cap for m in re.finditer(rb"\d{4}-\d{2}-\d{2}", handle.read())):
                raise PayloadCapError(f"payload {label} names a date on or after {cap}")

    def _put(self, files, source, relative):
        """Link or copy one file under the root and record its digest."""
        destination = self.root / relative
        if destination.exists():
            if path_hash(str(destination)) != path_hash(str(source)):
                raise ValueError("bundle destination already holds other bytes: "+relative)
        else:
            destination.parent.mkdir(parents=True, exist_ok=True)
            try:
                os.link(source, destination)
            except OSError:
                shutil.copyfile(source, destination)
        files[relative] = path_hash(str(destination))

    def _put_payload(self, files, source, relative, cap):
        """Stage one verified payload file with no date on or after ``cap``: linked as it is, or a cut copy."""
        destination = self.root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        if str(source).endswith(".parquet"):
            import pyarrow.parquet as pq

            exempt = self.metadata_stamps(pq.read_schema(source))
            if exempt:
                self.metadata_stamp_columns[relative] = exempt
            candidate = Path(str(destination) + ".new")
            try:
                if self.cut_parquet(source, candidate, cap):
                    if pq.ParquetFile(candidate).metadata.num_rows == 0:
                        self.emptied_payload_files.append(relative)
                    if destination.exists() and path_hash(str(destination)) != path_hash(str(candidate)):
                        raise ValueError("bundle destination already holds other bytes: "+relative)
                    os.replace(candidate, destination)
                    files[relative] = path_hash(str(destination))
                    return files[relative]
            finally:
                candidate.unlink(missing_ok=True)
        elif str(source).endswith(self.TEXT_SUFFIXES):
            self.refuse_dates_from(source, cap, relative)
        else:
            raise PayloadCapError(f"payload {relative} is neither parquet nor a known text type: "
                                  f"it cannot be checked for dates on or after {cap}")
        self._put(files, source, relative)
        return files[relative]

    def build(self):
        """Stage the bundle and write its manifest.

        Whole pinned files are shipped, so a payload file with rows dated on or after the owner cap
        (2024-01-01) is written as a CUT COPY that keeps only earlier rows, with its own digest. The
        bundled study then pins the cut digests, and ``identity.json`` records the ORIGINAL study
        identity and pins so the worker's action file still names what the WSL document pins.

        Returns
        -------
        dict
            ``files`` (relative path to SHA-256) and ``sha256`` (the digest of
            that mapping, the bundle's identity).

        Raises
        ------
        PayloadCapError
            The packet ends after the cap, or a JSON payload names a date on or after it.
        ValueError
            The plan does not build here, a pin does not verify, or the root
            already holds different bytes.
        """
        from dskit.onboarding.snapshot import read_manifest, snapshot_hash
        from . import mio_contract

        _refuse_blinded(self.study)
        cap = mio_contract.OWNER_TAIL_CAP
        if mio_contract.MIOStudyContract.packet(self.study)["end_before"] > cap:
            raise PayloadCapError(f"a bundle is built only for a packet ending by {cap} (owner ruling: through 2023Q4)")
        plan = StudyContract.build(self.study)
        self.root.mkdir(parents=True, exist_ok=True)
        files, study = {}, copy.deepcopy(self.study)
        code = {}
        for name, digest in self.study["code"].items():
            relative = self.CODE_DIR + "/" + MIOPacketInputs._portable_code_path(name)
            self._put(files, name, relative)
            if files[relative] != digest:
                raise ValueError("code pin drift: "+name)
            code[BUNDLE_TOKEN + "/" + relative] = digest
        study["code"] = code
        roots, cuts, snapshots = {}, {}, {}
        for binding in study["domain"]["sources"].values():
            pin = binding["pin"]
            source = PinnedPayloadSource(pin)
            key = roots.setdefault(pin["root"], f"{self.SOURCES_DIR}/root{len(roots)}")
            snapshot = Path("raw") / pin["source"] / pin["snapshot"]
            snap = (pin["root"], pin["source"], pin["snapshot"])
            snapshots.setdefault(snap, (key, snapshot))
            self._put(files, Path(pin["root"]) / "store" / "store.json", f"{key}/store/store.json")
            for relative in binding["files"].values():
                staged = f"{key}/{snapshot.as_posix()}/payload/{pin['stream']}/{relative}"
                digest = self._put_payload(files, source.file(relative, verify=True), staged, cap)
                if digest != pin["files"][relative]:
                    cuts.setdefault(snap, {})[f"{pin['stream']}/{relative}"] = digest
        for snap, (key, snapshot) in snapshots.items():
            original = Path(snap[0]) / snapshot / "manifest.json"
            relative = f"{key}/{snapshot.as_posix()}/manifest.json"
            if snap not in cuts:
                self._put(files, original, relative)
                continue
            manifest = read_manifest(str(original.parent))
            for entry in manifest["files"]:
                if entry["relpath"] in cuts[snap]:
                    cut = self.root / f"{key}/{snapshot.as_posix()}/payload/{entry['relpath']}"
                    entry["sha256"], entry["size"] = cuts[snap][entry["relpath"]], cut.stat().st_size
            atomic_write(self.root / relative, _canonical(manifest))
            files[relative] = path_hash(str(self.root / relative))
            for binding in study["domain"]["sources"].values():
                pin = binding["pin"]
                if (pin["root"], pin["source"], pin["snapshot"]) == snap:
                    pin["manifest_sha256"] = snapshot_hash(manifest)
                    pin["files"] = {rel: cuts[snap].get(f"{pin['stream']}/{rel}", digest)
                                    for rel, digest in pin["files"].items()}
        identity = {"study_sha256": MIOPacketInputs.portable_study_sha256(self.study),
                    "pins": {role: b["pin"] for role, b in self.study["domain"]["sources"].items()},
                    "metadata_stamp_columns": dict(sorted(self.metadata_stamp_columns.items())),
                    "emptied_payload_files": sorted(self.emptied_payload_files)}
        for binding in study["domain"]["sources"].values():
            binding["pin"]["root"] = BUNDLE_TOKEN + "/" + roots[binding["pin"]["root"]]
        atomic_write(self.root / self.IDENTITY_FILE, _canonical(identity))
        atomic_write(self.root / self.STUDY_FILE, _canonical(study))
        atomic_write(self.root / self.PLAN_FILE, _canonical(plan))
        for name in (self.IDENTITY_FILE, self.STUDY_FILE, self.PLAN_FILE):
            files[name] = path_hash(str(self.root / name))
        manifest = {"files": files, "sha256": value_hash(files)}
        atomic_write(self.root / self.MANIFEST_FILE, _canonical(manifest))
        return manifest


class MIOMethodWorker:
    """Solve one method of a bundled study and write its action file.

    Parameters
    ----------
    spec : dict
        ``bundle_root`` (absolute), ``method`` (a configured method id),
        ``attempt`` (int >= 1), ``work_dir`` (absolute; quote indexes and
        progress go here), ``out`` (absolute action-file path), optional
        ``unit`` (free-form identity carried into the action file),
        ``reuse_root`` (same-device index cache root or null) and
        ``host`` (a label; defaults to the hostname).

    Examples
    --------
    Solve one method from a staged bundle::

        worker = MIOMethodWorker({"bundle_root": "/Volumes/mio-ram/b", "method": "w1_010",
                                  "attempt": 1, "work_dir": "/Volumes/mio-ram/work/u.1",
                                  "out": "/Volumes/mio-ram/out/w1_010.json"})
        body = worker.run()
        body["portable_inputs_sha256"]  # what the WSL document must reproduce
    """

    _REQUIRED = {"bundle_root", "method", "attempt", "work_dir", "out"}
    _OPTIONAL = {"unit", "reuse_root", "host"}
    #: The index-build heartbeat's period and hard cap: a cold build is minutes, so an hour of beating
    #: with no end is a hang the coordinator's stale limit must catch.
    HEARTBEAT_SECONDS = 60.
    HEARTBEAT_MAX_SECONDS = 3600.

    #: Whether this run is a ``--check`` timing run (the only run that may carry a ``decision_weeks`` result).
    timing_check = False

    def __init__(self, spec):
        if (not isinstance(spec, dict) or not self._REQUIRED <= set(spec)
                or set(spec) - self._REQUIRED - self._OPTIONAL
                or type(spec["attempt"]) is not int or spec["attempt"] < 1
                or any(not isinstance(spec[k], str) or not os.path.isabs(spec[k])
                       for k in ("bundle_root", "work_dir", "out"))):
            raise ValueError("worker spec requires absolute bundle_root, work_dir, out, a method and attempt >= 1")
        self.spec = copy.deepcopy(spec)
        self.root = Path(spec["bundle_root"])

    @staticmethod
    def evidence_guard(result, timing_check):
        """Refuse to digest a ``decision_weeks`` check-week result as evidence (ADR-0313 I2 review).

        Only a ``--check`` timing run may carry one; its action file says so (``timing_check``).
        Returns whether the result is a timing check.
        """
        from .ledger_studies import WeeklyOptionPortfolioStudy

        if timing_check:
            return "decision_weeks" in result
        WeeklyOptionPortfolioStudy.check_document_not_evidence(result)
        return False

    @staticmethod
    def relocate(study, root):
        """Return the bundled study with ``BUNDLE_TOKEN`` replaced by the real bundle root.

        Parameters
        ----------
        study : dict
            ``study.json`` as the bundle stores it.
        root : str or pathlib.Path
            The bundle root on this host (resolved; a symlinked root refuses
            later in ``validate_implementation``).

        Returns
        -------
        dict
            A deep copy with every code pin and source pin root rewritten.

        Raises
        ------
        ValueError
            A pin does not start with the token (not a bundle study).
        """
        root = str(Path(root).resolve()).rstrip("/")
        out = copy.deepcopy(study)

        def moved(name):
            if not name.startswith(BUNDLE_TOKEN + "/"):
                raise ValueError("bundle study names a path outside the bundle: "+name)
            return root + name[len(BUNDLE_TOKEN):]

        out["code"] = {moved(name): digest for name, digest in study["code"].items()}
        for binding in out["domain"]["sources"].values():
            binding["pin"]["root"] = moved(binding["pin"]["root"])
        return out

    def verify(self):
        """Verify every bundle byte, the dependency pins and the code pins.

        Returns
        -------
        tuple
            ``(study, plan, manifest)`` as the bundle stored them.

        Raises
        ------
        ValueError
            Any file digest, dependency version or code pin differs.
        """
        from .mio_contract import MIOStudyContract

        _refuse_blinded(json.loads((self.root / MIOMethodBundle.STUDY_FILE).read_text(encoding="utf-8")))
        manifest = json.loads((self.root / MIOMethodBundle.MANIFEST_FILE).read_text(encoding="utf-8"))
        if set(manifest) != {"files", "sha256"} or value_hash(manifest["files"]) != manifest["sha256"]:
            raise ValueError("bundle manifest identity differs")
        for relative, digest in manifest["files"].items():
            path = self.root / relative
            if not path.is_file() or path_hash(str(path)) != digest:
                raise ValueError("bundle file drift: "+relative)
        study = self.relocate(json.loads((self.root / MIOMethodBundle.STUDY_FILE).read_text(encoding="utf-8")),
                              self.root)
        plan = json.loads((self.root / MIOMethodBundle.PLAN_FILE).read_text(encoding="utf-8"))
        StudyContract.check_dependencies(study["dependencies"])
        code_root = self.root / MIOMethodBundle.CODE_DIR
        for name in study["code"]:
            if Path(name).parent != code_root and code_root not in Path(name).parents:
                raise ValueError("code pin outside the bundle: "+name)
        StudyContract.validate_implementation(study["code"], (StudyContract, MIOStudyContract))
        if plan.get("sha256") is None or plan.get("read_bounds") is None:
            raise ValueError("bundle plan lacks identity or read bounds")
        return study, plan, manifest

    @staticmethod
    def actions(result, weeks):
        """Reduce one solved method result to its integer actions per decision week.

        Parameters
        ----------
        result : dict
            A ``WeeklyOptionPortfolioStudy._method`` result.
        weeks : list of str
            The study's decision weeks.

        Returns
        -------
        dict
            ``method``, ``weeks`` and ``decisions`` (date to ``actions`` and
            ``certificates``) for every week that carried a solve; a method
            declaring ``kelly.search`` or ``kelly.warm_start`` (ADR-0302)
            also carries the week's records of them (``kelly_strategy``).
            ADR-0307 carries the stage-one actions and certificate alongside
            the final local-search quantities so this host can rerun the climb.
        """
        from dskit.pipeline.libs.pyomo import TransportPortfolioSolve

        kelly = result["method"].get("kelly")
        declared = [k for k in ("search", "warm_start") if isinstance(kelly, dict) and k in kelly]
        local = isinstance(kelly, dict) and "local_search" in kelly
        decisions = {}
        for decision in result["decisions"]:
            if "selection" in decision:
                selection = decision["selection"]
                decisions[decision["date"]] = {
                    "actions": copy.deepcopy(selection["quantities"]) if local
                    else TransportPortfolioSolve.actions(selection),
                    "certificates": copy.deepcopy(decision["certificates"][-1:] if local
                                                  else decision["certificates"])}
                if local:
                    stage1 = decision["stage1"]
                    decisions[decision["date"]]["stage1"] = {
                        "actions": TransportPortfolioSolve.actions(stage1["selection"]),
                        "certificates": copy.deepcopy(stage1["certificates"])}
                if declared:
                    decisions[decision["date"]]["kelly_strategy"] = {
                        k: copy.deepcopy(decision["kelly"][k]) for k in declared}
        return {"method": result["method"]["id"], "weeks": list(weeks), "decisions": decisions}

    @staticmethod
    def host_identity(label=None):
        """Describe the solving host: name, platform, interpreter and BLAS."""
        blas = None
        try:
            import numpy
            config = numpy.show_config(mode="dicts")
            blas = config.get("Build Dependencies", {}).get("blas", {}).get("name")
        except Exception:
            blas = None
        return {"label": label or socket.gethostname(), "hostname": socket.gethostname(),
                "platform": platform.platform(), "machine": platform.machine(),
                "python": platform.python_version(), "blas": blas}

    def _progress(self, done, total, status):
        """Append one progress line beside the action file: a week count, never the week's status word.

        The status (filled, no eligible expiry, timeout, ...) is the quarter's trading timeline; progress is judged
        by the line changing, and the count changes every week, so the word is not needed and a sealed quarter's
        progress file must not carry it.
        """
        line = f"{stamp()} week {done}/{total}\n"
        with open(self.spec["out"] + ".progress", "a", encoding="utf-8") as handle:
            handle.write(line)

    def _heartbeat(self):
        """Append one progress line that says the index build is still running."""
        with open(self.spec["out"] + ".progress", "a", encoding="utf-8") as handle:
            handle.write(f"{stamp()} index building\n")

    def run(self):
        """Verify, read, solve one method and write the action file atomically.

        Returns
        -------
        dict
            The action-file body that was written.

        Raises
        ------
        ValueError
            Verification, intake or the solve refuses; nothing is written then.
        """
        from .ledger_studies import MIOPacketStudy, WeeklyOptionPortfolioStudy
        from .mio_contract import MIOStudyContract

        study, plan, manifest = self.verify()
        d = study["domain"]
        matches = [m for m in d["params"]["methods"] if m["id"] == self.spec["method"]]
        if len(matches) != 1:
            raise ValueError("unknown or ambiguous method: "+str(self.spec["method"]))
        method = matches[0]
        work = Path(self.spec["work_dir"])
        work.mkdir(parents=True, exist_ok=True)
        Path(self.spec["out"]).parent.mkdir(parents=True, exist_ok=True)
        reuse = self.spec.get("reuse_root")
        with Heartbeat(self._heartbeat, self.HEARTBEAT_SECONDS, self.HEARTBEAT_MAX_SECONDS):
            bounded = MIOPacketInputs.read_bounded(  # a cold quote-index build is minutes of silence
                study, plan["read_bounds"], MIOStudyContract.packet(study),
                work / "artifacts" / "mio-quotes", Path(reuse) if reuse else None)
        # The bundle may pin cut copies of the source files; the action file names the ORIGINAL pins the
        # WSL document holds, which identity.json (a verified bundle file) records.
        identity = json.loads((self.root / MIOMethodBundle.IDENTITY_FILE).read_text(encoding="utf-8"))
        for data in bounded["inputs"].values():
            data["chain_index"]["source_pin"] = identity["pins"]["chain"]
        params = {**d["params"], "selected_pairs": d["pairs"], "clocks": bounded["clocks"]}
        weekly = WeeklyOptionPortfolioStudy(params, bounded["inputs"], adjustments=bounded.get("adjustments"))
        started = time.perf_counter()
        try:
            result = weekly._method(method, lambda mid, done, total, status: self._progress(done, total, status))
            weekly.close_quote_indexes(verify=True)
        finally:
            if any(type(q).__name__ == "_SQLiteQuoteView" for q in weekly._quotes.values()):
                try:
                    weekly.close_quote_indexes()
                except Exception:
                    pass
        seconds = time.perf_counter() - started
        timing = self.evidence_guard(result, self.timing_check)
        body = {"format": MIOPacketStudy.RECORDED_FORMAT, "method": method["id"],
                "attempt": self.spec["attempt"], "host": self.host_identity(self.spec.get("host")),
                "bundle_manifest_sha256": manifest["sha256"], "plan_sha256": plan["sha256"],
                "study_sha256": identity["study_sha256"],
                "portable_inputs_sha256": MIOPacketInputs.portable_inputs_sha256(bounded),
                "dependencies": study["dependencies"], "actions": self.actions(result, weekly.weeks),
                "result_sha256": value_hash(result), "seconds": seconds,
                "timeouts": sum(1 for x in result["decisions"] if x.get("status") == "timeout_skipped")}
        if timing:
            body["timing_check"] = True
        if "unit" in self.spec:
            body["unit"] = self.spec["unit"]
        out = Path(self.spec["out"])
        raw = _canonical(body)
        atomic_write(out, raw)
        atomic_write(Path(str(out) + ".sha256"), (_sha256_bytes(raw) + "  " + out.name + "\n").encode("utf-8"))
        atomic_write(Path(str(out) + ".done"), b"done\n")
        return body


def _decision_certificates(entry):
    """Return the dollar-solve certificates followed by the local-search evidence, when present."""
    return [*entry.get("stage1", {}).get("certificates", []), *entry["certificates"]]


def _termination(certificate):
    """Name the solver's termination or the local-search heuristic outcome."""
    if "termination" in certificate:
        return certificate["termination"]
    return certificate["strategy_outcome"]


def main(argv=None):
    """Run one worker from a JSON spec: ``python -m index_options.mio_worker spec.json [--print]``."""
    for name in THREAD_VARIABLES:
        os.environ.setdefault(name, "1")
    parser = argparse.ArgumentParser(description="solve one MIO method from a bundle")
    parser.add_argument("spec", help="worker spec JSON (bundle_root, method, attempt, work_dir, out)")
    parser.add_argument("--print", action="store_true",
                        help="print only the method id and the digest of the integer actions and terminations")
    parser.add_argument("--check", action="store_true",
                        help="print the method id, the week count and, per week, EVERY certificate's objective and termination word, for "
                             "a tolerance comparison (the Mac certificate); no actions are printed or matched")
    args = parser.parse_args(argv)
    with open(args.spec, encoding="utf-8") as handle:
        spec = json.load(handle)
    try:
        worker = MIOMethodWorker(spec)
        worker.timing_check = args.check
        body = worker.run()
    except Exception as exc:  # one scrubbed line: a message can carry a gap or a value, a traceback more
        try:
            from .ledger_studies import MIOPacketStudy
            message = MIOPacketStudy.scrub(str(exc))
        except Exception:
            message = "(message withheld)"
        sys.stderr.write(f"{type(exc).__name__}: {message}\n")
        return 1
    if args.check:
        # Two hosts may return different optimal books of equal value, so this compares objectives (within the
        # certify tolerance) and termination words, not actions. The parity gate is the real similarity check.
        days = sorted(body["actions"]["decisions"])
        weeks = [_decision_certificates(body["actions"]["decisions"][day]) for day in days]
        if not weeks or not all(weeks):  # nothing was solved: a pass here would compare two empty outputs
            sys.stderr.write("RuntimeError: --check found no solved week (zero weeks, or a week with no certificate)\n")
            return 1
        sys.stdout.write(json.dumps({"method": body["method"], "weeks": len(days),
                                     "objectives": [[c["objective"] for c in week] for week in weeks],
                                     "terminations": [[_termination(c) for c in week] for week in weeks]},
                                    sort_keys=True) + "\n")
    if args.print:
        # Integer actions and each week's termination word only: no float and no timing, so the line is
        # comparable across hosts (the digest compute-run certify compares).
        digest = {day: {"actions": entry["actions"], "termination": [_termination(c) for c in _decision_certificates(entry)]}
                  for day, entry in body["actions"]["decisions"].items()}
        sys.stdout.write(json.dumps({"method": body["method"], "actions_sha256": value_hash(digest)}, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
