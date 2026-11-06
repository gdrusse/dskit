"""Coordinator for parallel MIO method units across WSL and an SSH worker (ADR-0297).

Run it as ``python children/index_options/tools/mio_parallel_queue.py run config.json``
with ``PYTHONPATH=<runtime>:<runtime>/children/index_options``. It lives OUTSIDE the
pinned ``index_options`` package on purpose: a study pins every ``.py`` of that
package, so a coordinator fix inside it would force a re-freeze of every quarter.
The worker (``index_options.mio_worker``) must be pinned, since it runs from each
bundle's own code tree; this file is not part of any bundle.

A quarter-arm MIO document must run on WSL: it claims a registry, binds and
closes a receipt. Its methods, however, only share read-only inputs, so each
method is a unit a worker can solve anywhere from a bundle. This module is the
single writer that leases those units, launches workers on the hosts it is
given, fetches and checks their action files, and launches the frozen document
of an arm once every unit of that arm is settled. The document's own
re-derivation and exact replay are the acceptance test; the checks here only
keep a corrupt or stale file out of ``recorded/``. They cannot prove that a
worker's solve was optimal.

State is one SQLite table under ``BEGIN IMMEDIATE``. Workers never write queue
state. Nothing here shows a method's economics: ``status.json`` carries states,
hashes, attempts, hosts, seconds, counts and reasons only, and the parity check
prints pass or fail plus hashes. Action files carry each week's solver certificate
(an objective and a bound) and a document's run.log carries MIO_EVENT lines, so
all of it lives under the queue root, which must be on WSL ext4 where a 0700
directory is real (``_sealed_dir`` refuses a Windows drive, where chmod does
nothing) and the coordinator runs with umask 077. That is hygiene against
accidental display, not protection from another process of the same user.
Hosts, the resource probe and the document launcher are injected objects, so
the scheduling logic is testable with fakes; ``main`` wires the real ones.
"""

import argparse
import calendar
import fcntl
import hashlib
import json
import math
import os
import re
import shlex
import shutil
import sqlite3
import stat
import subprocess
import sys
import threading
import time
from abc import ABC, abstractmethod
from pathlib import Path

from dskit.pipeline.node import atomic_write
from index_options.mio_worker import BLINDED_POLICY, STAMP_FORMAT, THREAD_VARIABLES, PayloadCapError, stamp

__all__ = ["UnitQueue", "Host", "LocalHost", "SshHost", "Coordinator", "DocumentRunner", "ResourceProbe",
           "Similarity", "PidLock", "windows_reader", "skill_certificate", "ticker_lock_ok",
           "recorded_from_report", "RejectionLedger", "rejections_from_log", "Phases", "STATUSES", "main"]

#: Every state a unit can be in; transitions are enforced by UnitQueue.
STATUSES = ("pending", "leased", "returned", "verified", "rejected", "superseded",
            "replayed", "solved_local")
_TERMINAL = {"verified", "rejected", "superseded"}
#: Where the mac-compute-worker skill keeps its shared state and resource helpers (read, never edited).
SKILL_SCRIPTS = "/mnt/c/Users/russe/.codex/skills/mac-compute-worker/scripts"
#: The C: mount the disk safeguard reads. A config may not name another (it could point the 3 GB stop at a roomy disk).
C_MOUNT = "/mnt/c"
#: The marker file a Mac RAM disk carries so an SSD directory of the same name is never mistaken for it.
SENTINEL_FILE = ".mio-sentinel"
#: The one certificate job type of this queue (compute-run certify mio-method-unit); a config cannot name another.
CERT_JOB_TYPE = "mio-method-unit"
#: A config can raise a safeguard but never lower it below these floors.
LIMIT_FLOORS = {"c_floor_gb": 3., "c_job_gb": 3.4, "unit_ram_gb": 4., "document_ram_gb": 4., "mac_reserve_gb": 4.,
                "wsl_reserve_gb": 6., "windows_reserve_gb": 2., "ramp_seconds": 120., "stale_seconds": 1800.}


def _limit_number(name, value):
    """Return a limit as a finite float, else refuse.

    ``max(nan, floor)`` is ``nan`` and every comparison with ``nan`` is False, so a bare ``NaN`` literal (Python's
    ``json`` accepts it) would pass a floor and then switch the safeguard off; an infinite one is no limit either.
    """
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"limit {name} must be a number") from None
    if not math.isfinite(number):
        raise ValueError(f"limit {name} must be a number, and finite")
    return number


def _utc():
    """ISO UTC seconds."""
    return stamp()


def _read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return default


#: The reason categories a rejection may carry (``RecordedCheckRefusal.CATEGORIES``; anything else reads ``other``).
REJECTION_CATEGORIES = ("tolerance", "numeric", "invalid_trade", "certificate", "gap", "pricing", "file", "replay", "other")


def _clean_rejection(raw):
    """Keep only the sanitized fields of one rejection: week, check, category, ratio (and the method id).

    Every value is validated against a fixed shape, so text from a log or an audit can carry no figure:
    a week is an ISO date or None, a check a short snake-case word, a category one of
    ``REJECTION_CATEGORIES``, a ratio a finite number (a miss over its tolerance) or None.
    """
    raw = raw if isinstance(raw, dict) else {}
    week, check, category, ratio, method = (raw.get(k) for k in ("week", "check", "category", "ratio", "method"))
    return {"method": method if isinstance(method, str) and re.fullmatch(r"[A-Za-z0-9._-]{1,80}", method) else None,
            "week": week if isinstance(week, str) and re.fullmatch(r"\d{4}-\d\d-\d\d", week) else None,
            "check": check if isinstance(check, str) and re.fullmatch(r"[a-z][a-z0-9_]{0,47}", check) else "unclassified",
            "category": category if category in REJECTION_CATEGORIES else "other",
            "ratio": float(ratio) if isinstance(ratio, (int, float)) and not isinstance(ratio, bool)
            and math.isfinite(ratio) else None}


def _run_key(run_dir):
    """The ledger's run name of a document run directory: its step root plus its own name.

    The directory name alone repeats when an arm is reopened (the retry gets a ``.retryN`` step root), which
    would make a rejection repeated by the rerun look like the one the owner cleared.
    """
    path = Path(str(run_dir))
    return f"{path.parent.name}/{path.name}" if path.parent.name else path.name


def _event_rejection(line):
    """The sanitized rejection of one ``MIO_EVENT`` log line, or None when the line is another event."""
    if '"type":"recorded_rejected"' not in line or "MIO_EVENT " not in line:
        return None
    try:
        event = json.loads(line.split("MIO_EVENT ", 1)[1])
    except ValueError:
        event = {}
    return _clean_rejection(event)


class RejectionLedger:
    """Append-only record of every worker result a WSL check refused (owner ruling 2026-11-05 ~18:35).

    The owner trusts the Mac's solve and has WSL check it, so a refusal must never be silent: each one
    is a line with its unit, decision week, check name, reason category and the size of the miss as a ratio
    to its tolerance (no objective, bound or dollar value). While any rejection is open, the coordinator
    places no new Mac unit; ``clear`` (the owner's ``clear-rejections`` command) closes the open ones and
    keeps them in the file as history. Lines are appended whole, so the owner's command and the
    coordinator never overwrite each other. An unreadable line counts as an open rejection (fail closed).

    Parameters
    ----------
    path : str or pathlib.Path
        The JSON-lines file (created on the first line).

    Examples
    --------
    ::

        ledger = RejectionLedger(root / "rejections.jsonl")
        ledger.record("2020q1/rules/w1_010", {"week": "2020-02-03", "check": "certificate_objective",
                      "category": "tolerance", "ratio": 2.5}, run="run-2020q1-rules", at=stamp())
        ledger.state()["open"]  # 1: Mac placement is stopped until ledger.clear(...)
    """

    def __init__(self, path):
        self.path = Path(path)

    def _lines(self):
        try:
            text = self.path.read_text(encoding="utf-8", errors="replace")
        except FileNotFoundError:
            return []  # no file yet is an empty ledger; any other failure (permissions, a directory, I/O) refuses
        rows = []
        for line in text.splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
                if not isinstance(row, dict) or row.get("kind") not in ("reject", "clear"):
                    raise ValueError
            except ValueError:
                row = {"kind": "reject", "unit": "unreadable", "week": None, "check": "unreadable_ledger_line",
                       "category": "other", "ratio": None, "run": None, "at": None}
            rows.append(row)
        return rows

    def _append(self, row):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = (json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n").encode()
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            size = os.fstat(fd).st_size
            if size and os.pread(fd, 1, size - 1) != b"\n":
                data = b"\n" + data  # a torn tail (a crash mid-write) stays its own unreadable line; this row never glues to it
            view = memoryview(data)
            while view:  # os.write may write fewer bytes than asked
                view = view[os.write(fd, view):]
            os.fsync(fd)
        finally:
            os.close(fd)

    def record(self, unit, fields, run, at):
        """Append one rejection; False when the same unit, week, check, ratio and run is already open."""
        unit = unit if isinstance(unit, str) and re.fullmatch(r"[A-Za-z0-9._@/-]{1,200}", unit) else "unknown"
        row = {"kind": "reject", "unit": unit, **{k: v for k, v in _clean_rejection(fields).items() if k != "method"},
               "run": run if isinstance(run, str) and re.fullmatch(r"[A-Za-z0-9._@/-]{1,200}", run) else None, "at": at}
        key = lambda r: (r.get("unit"), r.get("week"), r.get("check"), r.get("category"), r.get("ratio"), r.get("run"))
        # Open AND cleared rows: a restart that repeats a document's rejections must not re-open what the owner cleared.
        if any(key(r) == key(row) for r in self._lines() if r["kind"] == "reject"):
            return False
        self._append(row)
        return True

    def state(self):
        """Counts and the open records: ``open``, ``cleared``, ``reasons`` (open ones by ``category:check``), ``records``.

        Every rejection row has a ``seq`` (its 1-based place among the ledger's rejection lines), the handle
        ``clear`` closes by.
        """
        opened, cleared, seq = [], 0, 0
        for row in self._lines():
            if row["kind"] == "clear":
                through = row.get("through")
                if type(through) is not int or through <= 0:
                    continue  # a clear line that names no row closes nothing: an open rejection never closes by accident
                kept = [r for r in opened if r["seq"] > through]
                cleared += len(opened) - len(kept)
                opened = kept
            else:
                seq += 1
                opened.append({**{k: row.get(k) for k in ("unit", "week", "check", "category", "ratio", "run", "at")}, "seq": seq})
        reasons = {}
        for row in opened:
            name = f"{row['category']}:{row['check']}"
            reasons[name] = reasons.get(name, 0) + 1
        return {"open": len(opened), "cleared": cleared, "reasons": dict(sorted(reasons.items())), "records": opened}

    def clear(self, at, through=None):
        """Close the open rejections up to row ``through`` (their ``seq`` in status.json); return how many.

        ``through`` None closes every rejection open at the moment of the read: a rejection recorded after it has
        a higher ``seq`` and stays open, as does one recorded after the owner read status.json and passed the
        highest ``seq`` it showed. Repeating the same command therefore never closes a row the owner has not seen.

        Raises
        ------
        ValueError
            ``through`` is not a positive integer (zero or a negative number used to close every open row).
        """
        if through is not None and (type(through) is not int or through <= 0):
            raise ValueError("through must be a positive row number (the seq of a record in status.json)")
        opened = self.state()["records"]
        bound = max((r["seq"] for r in opened), default=0) if through is None else through
        count = sum(1 for r in opened if r["seq"] <= bound)
        if count:
            self._append({"kind": "clear", "through": bound, "count": count, "at": at})
        return count


def rejections_from_log(run_dir):
    """The sanitized ``recorded_rejected`` events of a run's log, for a run that never reached its close artifact.

    Reads only the structured fields (week, check, category, ratio) of lines the document logged as
    ``recorded_rejected``; no other line's content is parsed into the result.
    """
    out, log = [], Path(run_dir) / "run.log"
    if not log.exists():
        return out
    with open(log, encoding="utf-8", errors="replace") as handle:
        for line in handle:
            row = _event_rejection(line)
            if row is not None:
                out.append(row)
    return out


class Phases:
    """The owner's selection-then-holdout shape of the MIO test (Path row A0010), config driven.

    Nine formulations run on the selection quarters; the owner reads those results with
    ``report-selection``, picks ``picks`` formulations with ``record-selection`` (never ranked by a
    rule), and only those run on the holdout quarters, whose results stay sealed until the owner
    runs ``unseal``. Sealed means: the holdout quarters' recorded files, aside copies and document
    run roots live under ``<queue_root>/holdout/`` (mode 0700), ``status.json`` shows their states
    and nothing else, and no report command exists for them. A holdout quarter is not planned,
    and the coordinator refuses to start, until the selection is recorded.

    Parameters
    ----------
    root : str or pathlib.Path
        The queue root (holds ``selection.json`` and ``unsealed.json``).
    selection_quarters, holdout_quarters : list of str
        Disjoint quarter ids.
    formulations : list of str
        Every selection formulation id; each selection quarter runs all of them.
    picks : int
        How many the owner picks.
    spec_hashes : dict or None
        ``{formulation id: value_hash of its method spec}``. A document whose method of that id hashes differently
        is refused, so an id cannot name a different formulation in the holdout than it did in the selection.
        ``from_config`` requires it (``selection_formulation_hashes``); ``None`` skips the check (unit tests).

    Examples
    --------
    ::

        phases = Phases.from_config(config)
        phases.record(["w1_010_shock10_lp", "nominal_lp", "w1_003_shock10_lp"], stamp(), closed=True)
    """

    SELECTION_FILE, UNSEALED_FILE, HOLDOUT_DIR = "selection.json", "unsealed.json", "holdout"
    _KEYS = ("selection_quarters", "holdout_quarters", "selection_formulations", "selection_picks",
             "selection_formulation_hashes")

    def __init__(self, root, selection_quarters, holdout_quarters, formulations, picks=3, spec_hashes=None):
        self.root = Path(root)
        self.selection_quarters, self.holdout_quarters = list(selection_quarters), list(holdout_quarters)
        self.formulations, self.picks = list(formulations), picks
        self.spec_hashes = None if spec_hashes is None else dict(spec_hashes)

    @staticmethod
    def spec_hash(method):
        """The hash that pins one formulation: ``value_hash`` of its whole method spec (id included), numbers normalised (``1`` is ``1.0``)."""
        from dskit.pipeline.base import value_hash
        return value_hash(Phases._numbers(method))

    @staticmethod
    def _numbers(value):
        """The same tree with every integer written as a float, so ``1`` and ``1.0`` (a config may write either) hash alike."""
        if isinstance(value, dict):
            return {k: Phases._numbers(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [Phases._numbers(v) for v in value]
        return float(value) if type(value) is int else value

    @classmethod
    def from_config(cls, config):
        """Build from a coordinator config, or None when it names no phase (the old, unsealed behaviour).

        Raises
        ------
        ValueError
            Some but not all phase keys, overlapping or unknown quarters, repeated formulations, a pick count
            that is not between 1 and the number of formulations, or spec hashes that are not exactly one
            64-hex pin per formulation.
        """
        if not any(k in config for k in cls._KEYS):
            return None
        selection, holdout = config.get("selection_quarters"), config.get("holdout_quarters")
        formulations, picks = config.get("selection_formulations"), config.get("selection_picks", 3)
        strings = lambda v: isinstance(v, list) and v and all(isinstance(x, str) and x for x in v) and len(set(v)) == len(v)
        if not (strings(selection) and strings(holdout) and strings(formulations)):
            raise ValueError("selection_quarters, holdout_quarters and selection_formulations must be nonempty "
                             "lists of distinct names")
        if set(selection) & set(holdout) or not set(selection) | set(holdout) <= set(config.get("quarters", [])):
            raise ValueError("selection and holdout quarters must be disjoint members of quarters")
        if type(picks) is not int or not 1 <= picks <= len(formulations):
            raise ValueError("selection_picks must be an integer from 1 to the number of formulations")
        hashes = config.get("selection_formulation_hashes")
        if (not isinstance(hashes, dict) or set(hashes) != set(formulations)
                or any(not (isinstance(v, str) and re.fullmatch(r"[0-9a-f]{64}", v)) for v in hashes.values())):
            raise ValueError("selection_formulation_hashes must pin one 64-hex spec hash per selection formulation")
        return cls(config["queue_root"], selection, holdout, formulations, picks, hashes)

    def holds(self, quarter):
        """True for a holdout quarter: its files live under the sealed path whether or not it was unsealed."""
        return quarter in self.holdout_quarters

    @property
    def holdout_path(self):
        return self.root / self.HOLDOUT_DIR

    def picked(self):
        """The owner's picks in the order given, or None while the selection is not recorded."""
        body = _read_json(self.root / self.SELECTION_FILE)
        picked = body.get("picked") if isinstance(body, dict) else None
        return picked if isinstance(picked, list) and picked else None

    def record(self, ids, at, closed):
        """Fix the owner's picks (once; ``closed``: every selection-quarter arm has closed).

        Raises
        ------
        ValueError
            The selection quarters are not closed, the count is not ``picks``, an id is repeated or not a
            selection formulation, or a selection is already recorded.
        """
        if self.picked() is not None:
            raise ValueError("a selection is already recorded: the picks are fixed")
        if not closed:
            raise ValueError("the selection quarters are not closed: nothing to pick from yet")
        if len(ids) != self.picks or len(set(ids)) != len(ids) or not set(ids) <= set(self.formulations):
            raise ValueError(f"pick exactly {self.picks} distinct selection formulations")
        temporary = self.root / (self.SELECTION_FILE + ".new")
        atomic_write(temporary, json.dumps({"picked": list(ids), "recorded_at": at}, indent=1).encode())
        try:
            os.link(temporary, self.root / self.SELECTION_FILE)  # fails if another record won
        except FileExistsError:
            raise ValueError("a selection is already recorded: the picks are fixed") from None
        finally:
            temporary.unlink(missing_ok=True)

    def unsealed(self):
        return (self.root / self.UNSEALED_FILE).exists()

    def unseal(self, at):
        """The owner opens the holdout results to status (requires the recorded selection)."""
        if self.picked() is None:
            raise ValueError("the selection is not recorded: nothing to unseal")
        atomic_write(self.root / self.UNSEALED_FILE, json.dumps({"unsealed_at": at}).encode())

    def check_methods(self, quarter, methods):
        """Refuse a quarter whose documents do not run exactly what its phase allows.

        A selection quarter runs every selection formulation; a holdout quarter runs exactly the picks. ``methods``
        are the documents' method specs (dicts with an ``id``); each must hash to the spec pinned for its id.
        """
        pinned, methods = self.spec_hashes, list(methods)
        ids = [m["id"] if isinstance(m, dict) else m for m in methods]
        if pinned is not None:
            for method, name in zip(methods, ids):
                observed = self.spec_hash(method) if isinstance(method, dict) else None
                if name in pinned and observed != pinned[name]:
                    raise ValueError(f"formulation {name} differs from its pinned spec (hash {str(observed)[:12]} "
                                     f"against the pinned {pinned[name][:12]}): the id names another formulation")
        methods = ids
        if quarter in self.holdout_quarters:
            picked = self.picked()
            if picked is None:
                raise ValueError(f"{quarter} is a sealed holdout quarter and the selection is not recorded: "
                                 "run record-selection first")
            if sorted(methods) != sorted(picked):
                raise ValueError(f"{quarter} must run exactly the owner's picked formulations")
        elif quarter in self.selection_quarters and sorted(methods) != sorted(self.formulations):
            raise ValueError(f"{quarter} is a selection quarter: it must run every selection formulation "
                             f"({len(self.formulations)}; the formulation set differs)")

    def selection_closed(self, arms):
        """True when every selection quarter has arms and every one of them is closed."""
        return all((rows := [a for a in arms if a["quarter"] == q]) and all(a["status"] == "closed" for a in rows)
                   for q in self.selection_quarters)

    #: The metric keys of one method's saved result an owner reads per row (no weekly detail, no curve).
    REPORT_METRICS = ("pnl", "twr", "max_drawdown", "final_equity", "contracts", "unfilled")

    def report(self, arms):
        """Rows of the selection quarters' closed arms, one per formulation, in quarter, arm and id order.

        This is the one command that shows economics, and it exists for the selection quarters only: a holdout
        arm is never read. Nothing is ranked or scored; the owner chooses.

        Raises
        ------
        ValueError
            A selection arm is missing or not closed.
        """
        if not self.selection_closed(arms):
            raise ValueError("a selection quarter is not closed: nothing to report yet")
        rows = []
        for quarter in self.selection_quarters:
            for arm in (a for a in arms if a["quarter"] == quarter):
                for item in sorted(_close_artifact(arm["run_dir"]).get("results", []), key=lambda r: r["id"]):
                    result = item.get("result") or {}
                    rows.append({"quarter": quarter, "arm": arm["arm"], "method": item["id"], "status": item["status"],
                                 **{k: result.get(k) for k in self.REPORT_METRICS}})
        return rows


def _close_artifact(run_dir):
    """The close receipt a finished document wrote (``carry.json`` names it)."""
    run_dir = Path(run_dir)
    carry = json.loads((run_dir / "carry.json").read_text())
    return json.loads((run_dir / carry["close_packet"]["receipt"]["path"]).read_text())


class PidLock:
    """An exclusive advisory lock on a file, held until ``release``; a second holder is refused.

    Parameters
    ----------
    path : str or pathlib.Path
        The lock file (created).

    Examples
    --------
    ::

        lock = PidLock(root / "coordinator.lock")
        if not lock.acquire():
            sys.exit("another coordinator holds this queue root")
    """

    def __init__(self, path):
        self.path, self._handle = Path(path), None

    def acquire(self):
        """Take the lock without blocking; return False when another process holds it."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = open(self.path, "a+")
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.close()
            return False
        handle.seek(0)
        handle.truncate()
        handle.write(str(os.getpid()) + "\n")
        handle.flush()
        self._handle = handle
        return True

    def release(self):
        """Drop the lock."""
        if self._handle is not None:
            self._handle.close()
            self._handle = None


class UnitQueue:
    """Single-writer lease table for method units.

    Parameters
    ----------
    path : str or pathlib.Path
        SQLite file; created on first use.

    Examples
    --------
    Lease a unit and record its return::

        queue = UnitQueue("/data/mio/queue.sqlite")
        queue.add("2022q1/rules/w1_010", "2022q1", "rules", "w1_010")
        attempt = queue.lease("2022q1/rules/w1_010", "mac", pid=4242, pid_start="123")
        queue.transition("2022q1/rules/w1_010", "returned", attempt)
    """

    def __init__(self, path):
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self._tx() as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS units (
                unit_id TEXT PRIMARY KEY, quarter TEXT NOT NULL, arm TEXT NOT NULL, method TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'primary',
                status TEXT NOT NULL, attempt INTEGER NOT NULL DEFAULT 0, host TEXT, pid INTEGER,
                pid_start TEXT, started TEXT, last_progress TEXT, result_sha256 TEXT,
                history_json TEXT NOT NULL DEFAULT '[]')""")
            conn.execute("""CREATE TABLE IF NOT EXISTS arms (
                arm_key TEXT PRIMARY KEY, quarter TEXT NOT NULL, arm TEXT NOT NULL, status TEXT NOT NULL,
                run_dir TEXT, detail_json TEXT NOT NULL DEFAULT '{}')""")

    class _Tx:
        def __init__(self, path):
            self.path = path

        def __enter__(self):
            self.conn = sqlite3.connect(self.path, timeout=30)
            self.conn.row_factory = sqlite3.Row
            self.conn.execute("BEGIN IMMEDIATE")
            return self.conn

        def __exit__(self, kind, value, tb):
            try:
                if kind is None:
                    self.conn.commit()
                else:
                    self.conn.rollback()
            finally:
                self.conn.close()
            return False

    def _tx(self):
        return self._Tx(self.path)

    @staticmethod
    def _row(row):
        d = dict(row)
        d["history"] = json.loads(d.pop("history_json"))
        return d

    def add_arm(self, quarter, arm):
        """Register an arm as waiting once (an arm with no units runs as a plain WSL document)."""
        with self._tx() as conn:
            conn.execute("INSERT OR IGNORE INTO arms(arm_key, quarter, arm, status) VALUES (?,?,?,'waiting')",
                         (f"{quarter}/{arm}", quarter, arm))

    def add(self, unit_id, quarter, arm, method, role="primary"):
        """Register a unit as pending once; an existing unit is left as it is."""
        with self._tx() as conn:
            conn.execute("INSERT OR IGNORE INTO units(unit_id, quarter, arm, method, role, status) "
                         "VALUES (?,?,?,?,?,'pending')", (unit_id, quarter, arm, method, role))
        self.add_arm(quarter, arm)

    def units(self, **where):
        """Return unit rows, optionally filtered by equality on columns."""
        clause = " AND ".join(f"{k} = ?" for k in where) or "1=1"
        with self._tx() as conn:
            rows = conn.execute(f"SELECT * FROM units WHERE {clause} ORDER BY unit_id", tuple(where.values())).fetchall()
        return [self._row(r) for r in rows]

    def unit(self, unit_id):
        """Return one unit row; an unknown id refuses."""
        rows = self.units(unit_id=unit_id)
        if not rows:
            raise ValueError("unknown unit: "+unit_id)
        return rows[0]

    def lease(self, unit_id, host, pid=None, pid_start=None, now=None):
        """Lease a pending unit to a host; a second lease of a live unit refuses.

        Returns
        -------
        int
            The new attempt number.

        Raises
        ------
        ValueError
            The unit is not pending (a double claim).
        """
        now = now or _utc()
        with self._tx() as conn:
            row = conn.execute("SELECT * FROM units WHERE unit_id = ?", (unit_id,)).fetchone()
            if row is None:
                raise ValueError("unknown unit: "+unit_id)
            if row["status"] != "pending":
                raise ValueError(f"unit {unit_id} is {row['status']}, not pending: double claim refused")
            attempt = row["attempt"] + 1
            history = json.loads(row["history_json"])
            history.append({"attempt": attempt, "host": host, "event": "leased", "at": now})
            conn.execute("""UPDATE units SET status='leased', attempt=?, host=?, pid=?, pid_start=?, started=?,
                            last_progress=?, history_json=? WHERE unit_id = ?""",
                         (attempt, host, pid, pid_start, now, now, json.dumps(history), unit_id))
        return attempt

    def progress(self, unit_id, when):
        """Record the latest progress observation of a leased unit."""
        with self._tx() as conn:
            conn.execute("UPDATE units SET last_progress=? WHERE unit_id = ?", (when, unit_id))

    def handle(self, unit_id, pid, pid_start):
        """Record the pid and start time of a spawned worker."""
        with self._tx() as conn:
            conn.execute("UPDATE units SET pid=?, pid_start=? WHERE unit_id=?", (pid, pid_start, unit_id))

    def transition(self, unit_id, status, attempt, *, result_sha256=None, note=None, now=None, refund=False):
        """Move a unit to ``status`` for the given attempt; stale attempts are refused.

        A move to ``pending`` requeues the unit (its attempt count is kept so
        the next lease increments it). ``refund`` marks the lease the requeue
        ends as not the host's fault, so it does not count against the cap.
        """
        if status not in STATUSES:
            raise ValueError("unknown unit status: "+str(status))
        now = now or _utc()
        with self._tx() as conn:
            row = conn.execute("SELECT * FROM units WHERE unit_id = ?", (unit_id,)).fetchone()
            if row is None:
                raise ValueError("unknown unit: "+unit_id)
            if row["attempt"] != attempt:
                raise ValueError(f"unit {unit_id} attempt {attempt} is stale (current {row['attempt']})")
            if row["status"] in _TERMINAL and status not in ("replayed", "solved_local"):
                raise ValueError(f"unit {unit_id} is already {row['status']}")
            history = json.loads(row["history_json"])
            history.append({"attempt": attempt, "host": row["host"], "event": status, "at": now,
                            **({"note": note} if note else {}), **({"refund": True} if refund else {})})
            conn.execute("UPDATE units SET status=?, result_sha256=COALESCE(?, result_sha256), history_json=? WHERE unit_id = ?",
                         (status, result_sha256, json.dumps(history), unit_id))

    def arm(self, quarter, arm):
        """Return one arm row (status, run_dir, detail)."""
        with self._tx() as conn:
            row = conn.execute("SELECT * FROM arms WHERE arm_key = ?", (f"{quarter}/{arm}",)).fetchone()
        if row is None:
            raise ValueError("unknown arm")
        d = dict(row)
        d["detail"] = json.loads(d.pop("detail_json"))
        return d

    def arms(self):
        """Return every arm row in key order."""
        with self._tx() as conn:
            rows = conn.execute("SELECT * FROM arms ORDER BY arm_key").fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["detail"] = json.loads(d.pop("detail_json"))
            out.append(d)
        return out

    def set_arm(self, quarter, arm, status, run_dir=None, detail=None):
        """Move an arm to ``status`` (waiting, running, closed, failed) with its launch detail."""
        with self._tx() as conn:
            conn.execute("UPDATE arms SET status=?, run_dir=COALESCE(?, run_dir), detail_json=? WHERE arm_key=?",
                         (status, run_dir, json.dumps(detail or {}), f"{quarter}/{arm}"))


class Similarity:
    """The one first-unit parity check: do two hosts' action files for the SAME method look alike?

    The owner trusts the Mac's answer (2026-11-05) and wants no routine re-solve, so this is
    applied to ONE unit before the Mac is scaled up, never to later units. Pass needs the same
    identity digests (method, bundle, plan, study, portable inputs, dependencies), the same
    decision-week set, and every week's objective within ``REL`` relative (Path row A0010 records the
    1% first-unit check). Whether the integer actions of the two hosts match is recorded as information
    (``actions_match``) and never decides the verdict: the owner wants similar, not equal. Nothing is
    returned but a verdict and a short reason code, never a value.

    Examples
    --------
    ::

        passed, reason = Similarity.compare(mac_body, wsl_body)
    """

    #: Relative objective tolerance of every decision week.
    REL = 1e-2
    #: Dollar scale below which the relative test is taken against this much instead. An objective is a dollar
    #: figure and the smallest priced step of one contract is one dollar (a $0.01 tick on a 100-share contract),
    #: so 1% of ``FLOOR`` is a cent, far below any one-contract difference: it only lets two near-zero weeks
    #: (a cash week, or noise around zero) agree, and cannot hide a real difference in a held book.
    FLOOR = 1.0
    _IDENTITY = ("method", "bundle_manifest_sha256", "plan_sha256", "study_sha256",
                 "portable_inputs_sha256", "dependencies")

    @classmethod
    def thresholds_sha256(cls):
        """Hash of the threshold, recorded with every verdict."""
        from dskit.pipeline.base import value_hash
        return value_hash([cls.REL, cls.FLOOR])

    @staticmethod
    def _near(a, b, rel):
        if a is None or b is None:
            return a is None and b is None
        return abs(a - b) <= rel * max(abs(a), abs(b), Similarity.FLOOR)

    @classmethod
    def actions_match(cls, first, second):
        """Information only: ``True`` when every decision week carries the same integer actions on both hosts.

        ``None`` when the files cannot be read that way. Never part of the verdict.
        """
        try:
            a, b = first["actions"]["decisions"], second["actions"]["decisions"]
            return set(a) == set(b) and all(a[day]["actions"] == b[day]["actions"] for day in a)
        except (KeyError, TypeError):
            return None

    @classmethod
    def compare(cls, first, second):
        """Return ``(passed, reason)``; the reason is a fixed code: ok, identity, weeks, objective, malformed."""
        try:
            if any(first[k] != second[k] for k in cls._IDENTITY):
                return False, "identity"
            if first["actions"]["weeks"] != second["actions"]["weeks"]:
                return False, "weeks"
            a, b = first["actions"]["decisions"], second["actions"]["decisions"]
            if set(a) != set(b):
                return False, "weeks"
            for day in a:
                if not cls._near(a[day]["certificates"][0]["objective"], b[day]["certificates"][0]["objective"], cls.REL):
                    return False, "objective"
            return True, "ok"
        except (KeyError, IndexError, TypeError, ArithmeticError):  # ArithmeticError: an integer objective past float range
            return False, "malformed"


class Host(ABC):
    """One place a unit can be solved; the coordinator asks only these questions.

    Parameters
    ----------
    name : str
        The host label recorded in leases.
    slots : int
        Concurrent units this host may run when online.

    Examples
    --------
    A test double::

        class Fake(Host):
            ...
        host = Fake("mac", slots=2)
    """

    #: True for the coordinator's own host, whose RAM and C: the shared probe bounds.
    probe_limited = False
    #: True when ``stage`` can take minutes (a bundle over ssh): the coordinator runs it on a thread.
    slow_stage = False

    def __init__(self, name, slots):
        self.name, self.slots = name, int(slots)

    def stage(self, unit):
        """Make the unit's bundle available on the host (no-op where it already is)."""
        return None

    def available_gb(self):
        """Return the host's own free RAM in GiB, or None when unknown (the coordinator then places nothing)."""
        return None

    @abstractmethod
    def online(self):
        """Tell whether the host can accept work now (bool)."""

    @abstractmethod
    def spawn(self, unit, attempt):
        """Start a detached worker; return ``{"pid", "pid_start"}``."""

    def prune(self, unit, attempt):
        """Delete the attempt's working files once its result is accepted or refused (no-op by default)."""
        return None

    def drop_bundle(self, unit):
        """Delete the staged bundle of the unit's arm once the arm needs no more units (no-op by default)."""
        return None

    @abstractmethod
    def alive(self, unit, attempt, handle):
        """Tell whether the worker is still running: True, False, or None when that cannot be told now
        (a failed ssh). Unknown is never dead: the coordinator neither requeues nor kills on it."""

    @abstractmethod
    def progress(self, unit, attempt):
        """Return an opaque token that changes whenever the worker made progress, or None."""

    @abstractmethod
    def fetch(self, unit, attempt):
        """``(bytes, sidecar_text)`` of a finished action file, else None."""

    @abstractmethod
    def kill(self, unit, attempt, handle):
        """Stop a worker (idempotent)."""


def _pid_start(pid):
    """Read the start-time field of a local pid from /proc (None when gone)."""
    try:
        with open(f"/proc/{pid}/stat", encoding="utf-8") as handle:
            return handle.read().rsplit(")", 1)[1].split()[19]
    except (OSError, IndexError):
        return None


class LocalHost(Host):
    """Units run as detached local subprocesses of the worker CLI.

    Parameters
    ----------
    name, slots : see Host.
    python : str
        Interpreter path.
    bundle_root_for : callable
        ``unit -> bundle root`` (per quarter-arm).
    work_root, out_root : pathlib.Path
        ``work_root`` holds quote indexes (market data: scratch, D: in production); ``out_root`` holds action
        files with certificates and the worker logs, so it must be sealed WSL ext4 (``_sealed_dir`` refuses
        anything else when the first worker starts).
    reuse_root : pathlib.Path or None
        Same-device index cache root passed to the worker.
    sealed_out_root, sealed_quarter : pathlib.Path or None, callable or None
        Where the units of a sealed (holdout) quarter write: ``sealed_quarter(quarter)`` true sends their action
        files, specs, logs and progress files to ``sealed_out_root`` (under the holdout directory) instead of
        ``out_root``.

    Examples
    --------
    ::

        host = LocalHost("wsl", 2, "/home/me/dskit/.venv/bin/python", bundles.get,
                         Path("/mnt/d/mio/work"), Path("/home/me/mio/out"), Path("/mnt/d/mio/index-cache"))
    """

    probe_limited = True

    def __init__(self, name, slots, python, bundle_root_for, work_root, out_root, reuse_root=None,
                 sealed_out_root=None, sealed_quarter=None):
        super().__init__(name, slots)
        self.python, self.bundle_root_for = python, bundle_root_for
        self.work_root, self.out_root, self.reuse_root = Path(work_root), Path(out_root), reuse_root
        self.sealed_out_root = Path(sealed_out_root) if sealed_out_root else None
        self.sealed_quarter = sealed_quarter

    def online(self):
        """Report the coordinator's own host as always online."""
        return True

    def _out(self, unit, attempt):
        base = self.sealed_out_root if self.sealed_out_root and self.sealed_quarter and self.sealed_quarter(unit["quarter"]) \
            else self.out_root
        return base / f"{unit['unit_id'].replace('/', '__').replace('@', '_')}.{attempt}.json"

    def spawn(self, unit, attempt):
        """Start the worker CLI in its own session; return its pid and start time."""
        out = self._out(unit, attempt)
        _sealed_dir(out.parent)  # action files hold certificates (objective, bound) and logs may hold errors
        self.work_root.mkdir(parents=True, exist_ok=True)
        bundle = Path(self.bundle_root_for(unit))
        spec = {"bundle_root": str(bundle), "method": unit["method"], "attempt": attempt,
                "work_dir": str(self.work_root / f"{out.stem}"), "out": str(out),
                "unit": {k: unit[k] for k in ("quarter", "arm", "method")}, "host": self.name}
        if self.reuse_root:
            spec["reuse_root"] = str(self.reuse_root)
        for stale in (out, *(Path(str(out)+suffix) for suffix in (".done", ".sha256", ".progress", ".log"))):
            stale.unlink(missing_ok=True)       # a finished file of an earlier run under this attempt's name
        spec_path = out.with_suffix(".spec.json")
        spec_path.write_text(json.dumps(spec, sort_keys=True))
        env = {**os.environ, "PYTHONPATH": str(bundle / "code"), "PYTHONDONTWRITEBYTECODE": "1",
               **{v: "1" for v in THREAD_VARIABLES}}
        with os.fdopen(os.open(str(out) + ".log", os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600), "ab") as log:
            proc = subprocess.Popen([self.python, "-B", "-m", "index_options.mio_worker", str(spec_path)],
                                    env=env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
                                    cwd=str(self.work_root))
        return {"pid": proc.pid, "pid_start": _pid_start(proc.pid)}

    def alive(self, unit, attempt, handle):
        """Tell whether the pid is alive, not a zombie and still the same process."""
        pid = handle.get("pid")
        if not pid:
            return False
        try:
            os.kill(pid, 0)
        except OSError:
            return False
        start = _pid_start(pid)
        if handle.get("pid_start") and start != handle["pid_start"]:
            return False
        try:
            with open(f"/proc/{pid}/stat", encoding="utf-8") as fh:
                return fh.read().rsplit(")", 1)[1].split()[0] != "Z"
        except OSError:
            return False

    def progress(self, unit, attempt):
        """Return the progress file's modification stamp, or None."""
        path = Path(str(self._out(unit, attempt)) + ".progress")
        try:
            return stamp(path.stat().st_mtime)
        except OSError:
            return None

    def fetch(self, unit, attempt):
        """Return the finished action file and its sidecar, or None until ``.done`` exists."""
        out = self._out(unit, attempt)
        if not Path(str(out) + ".done").exists():
            return None
        try:
            return out.read_bytes(), Path(str(out) + ".sha256").read_text()
        except OSError:
            return None

    def prune(self, unit, attempt):
        """Delete the attempt's work directory (its quote indexes) once the result is dealt with."""
        shutil.rmtree(self.work_root / self._out(unit, attempt).stem, ignore_errors=True)

    def kill(self, unit, attempt, handle):
        """Terminate the worker's whole session group when it is still alive."""
        import signal
        pid = handle.get("pid")
        if pid and self.alive(unit, attempt, handle):
            try:
                os.killpg(os.getpgid(pid), signal.SIGTERM)
            except OSError:
                pass


class SshHost(Host):
    """Units run detached on an ssh host from a staged bundle under its RAM-disk root.

    The root must be a mount point holding the sentinel the operator wrote
    when creating the RAM disk; otherwise the host is offline. That refuses a
    plain directory on the SSD that happens to have the same name (a detached
    or never-created RAM disk), and every spawn repeats the check.

    Parameters
    ----------
    name, slots : see Host.
    alias : str
        The ssh alias (BatchMode; an offline host is simply not online).
    root : str
        Worker root on the host: the RAM disk's mount point.
    python : str
        Interpreter path on the host.
    bundle_dir_for : callable
        ``unit -> remote bundle directory`` under ``root``.
    worker_script : str
        Remote shell script that runs one unit (see the runbook's mac_worker.sh).
    sentinel : str
        The token ``<root>/.mio-sentinel`` must hold.
    certificate : callable or None
        ``() -> bool``; when given, the host is online only while it is true.
    local_bundle_for : callable or None
        ``unit -> local bundle root`` to stream to the host on first use.
    ssh : str
        The ssh executable (a test points it at a local shell).

    Examples
    --------
    ::

        host = SshHost("mac", 1, "mac", "/Volumes/mio-ram", "/Users/me/mio-worker/bin/python",
                       lambda unit: "/Volumes/mio-ram/bundles/2022q1-rules", "/Volumes/mio-ram/mac_worker.sh",
                       sentinel="token-from-ram-disk-creation")
    """

    slow_stage = True

    def __init__(self, name, slots, alias, root, python, bundle_dir_for, worker_script, sentinel,
                 certificate=None, ssh_timeout=20, local_bundle_for=None, ssh="ssh"):
        super().__init__(name, slots)
        self.alias, self.root, self.python = alias, root.rstrip("/"), python
        self.bundle_dir_for, self.worker_script, self.sentinel = bundle_dir_for, worker_script, sentinel
        self.certificate = certificate
        self.ssh_timeout, self.local_bundle_for, self.ssh = ssh_timeout, local_bundle_for, ssh

    def _argv(self, command):
        return [self.ssh, "-o", "BatchMode=yes", "-o", f"ConnectTimeout={self.ssh_timeout}", self.alias, command]

    def _ssh(self, command, timeout=None, input=None, text=True):
        return subprocess.run(self._argv(command), capture_output=True, text=text, input=input,
                              timeout=timeout or self.ssh_timeout + 40)

    def _identity(self):
        """Shell test: the root is a mount point and carries the sentinel."""
        root = shlex.quote(self.root)
        return (f'[ "$(df -P {root} 2>/dev/null | awk \'NR==2{{print $NF}}\')" = {root} ] && '
                f'[ "$(cat {root}/{SENTINEL_FILE} 2>/dev/null)" = {shlex.quote(self.sentinel)} ]')

    def stage(self, unit):
        """Stream the unit's bundle to the host once; it appears under its name only when whole.

        The archive is unpacked into a temporary directory, its file count and byte total are compared
        with the local bundle, and only then is it renamed into place. A manifest therefore exists at the
        final path only beside a complete payload, so the presence probe below can be trusted and an
        interrupted transfer is simply copied again.

        Raises
        ------
        RuntimeError
            The transfer fails, is incomplete, or does not reproduce the local manifest digest.
        """
        if self.local_bundle_for is None:
            return None
        from index_options.mio_worker import MIOMethodBundle

        local = Path(self.local_bundle_for(unit))
        remote = self.bundle_dir_for(unit)
        manifest = json.loads((local / MIOMethodBundle.MANIFEST_FILE).read_text())
        probe = self._ssh(f"cat {shlex.quote(remote)}/{MIOMethodBundle.MANIFEST_FILE} 2>/dev/null")
        if probe.returncode == 0 and probe.stdout.strip():
            try:
                if json.loads(probe.stdout)["sha256"] == manifest["sha256"]:
                    return manifest["sha256"]
            except (ValueError, KeyError):
                pass
        sent = [p for p in local.rglob("*") if p.is_file()]
        count, total = len(sent), sum(p.stat().st_size for p in sent)
        q, parent = shlex.quote, os.path.dirname(remote)
        temp = f"{parent}/.stage"
        tree = f"{q(temp)}/{q(local.name)}"
        tar = subprocess.Popen(["tar", "-C", str(local.parent), "-cf", "-", local.name], stdout=subprocess.PIPE)
        extract = (f"{self._identity()} && mkdir -p {q(parent)} && rm -rf {q(temp)} && mkdir {q(temp)} && "
                   f"tar -C {q(temp)} -xf - && "
                   f"[ \"$(cd {tree} && find . -type f | wc -l | tr -d ' ')\" = {count} ] && "
                   f"[ \"$(cd {tree} && find . -type f -exec cat {{}} + | wc -c | tr -d ' ')\" = {total} ] && "
                   f"rm -rf {q(remote)} && mv {tree} {q(remote)} && rmdir {q(temp)}")
        ssh = subprocess.run(self._argv(extract), stdin=tar.stdout, capture_output=True, text=True, timeout=3600)
        tar.stdout.close()
        tar.wait()
        if tar.returncode != 0 or ssh.returncode != 0:
            raise RuntimeError("bundle staging failed or was incomplete: "+ssh.stderr.strip())
        check = self._ssh(f"cat {q(remote)}/{MIOMethodBundle.MANIFEST_FILE}")
        if check.returncode != 0 or json.loads(check.stdout)["sha256"] != manifest["sha256"]:
            raise RuntimeError("staged bundle manifest differs")
        return manifest["sha256"]

    def online(self):
        """Tell whether the certificate holds, ssh answers and the root is the sentinel-marked RAM disk."""
        if self.certificate is not None and not self.certificate():
            return False
        try:
            return self._ssh(f"{self._identity()} && echo ok").stdout.strip() == "ok"
        except (subprocess.SubprocessError, OSError):
            return False

    def available_gb(self):
        """Free plus inactive plus speculative pages from ``vm_stat`` in GiB, or None when unreadable."""
        awk = ("/page size of/{ps=$8} /Pages free/{f=$3} /Pages inactive/{i=$3} /Pages speculative/{s=$3} "
               "END{printf \"%.3f\\n\", (f+i+s)*ps/1073741824}")
        try:
            out = self._ssh(f"vm_stat | awk {shlex.quote(awk)}").stdout.strip()
            return float(out)
        except (subprocess.SubprocessError, OSError, ValueError):
            return None

    #: Every file an attempt leaves beside its stem; ``spawn`` removes them before a new attempt of that name.
    RESULT_SUFFIXES = (".json", ".json.done", ".json.sha256", ".json.progress", ".log")

    def _stem(self, unit, attempt):
        return f"{self.root}/out/{unit['unit_id'].replace('/', '__').replace('@', '_')}.{attempt}"

    def spawn(self, unit, attempt):
        """Write the spec over ssh and start the worker script detached; return its pid and start time."""
        stem = self._stem(unit, attempt)
        name = os.path.basename(stem)
        spec = {"bundle_root": self.bundle_dir_for(unit), "method": unit["method"], "attempt": attempt,
                "work_dir": f"{self.root}/work/{name}", "out": stem + ".json",
                "unit": {k: unit[k] for k in ("quarter", "arm", "method")},
                "host": self.name, "reuse_root": f"{self.root}/cache/sqlite-parquet-index"}
        root, q = self.root, shlex.quote
        # A new queue root restarts attempt numbers at 1, so a finished file of an earlier run may sit under this
        # attempt's name: clear every result file first, or ``fetch`` would hand it back as this run's answer.
        stale = " ".join(q(stem + suffix) for suffix in self.RESULT_SUFFIXES)
        # POSIX sh: the spec is written in the foreground, only the worker is backgrounded.
        command = (f"if umask 077 && {self._identity()} && mkdir -p {q(root)}/out {q(root)}/work {q(root)}/tmp && "
                   f"rm -f {stale} && cat > {q(stem + '.spec.json')}; then "
                   f"MIO_SENTINEL={q(self.sentinel)} nohup sh {q(self.worker_script)} {q(self.python)} {q(stem + '.spec.json')} "
                   f"> {q(stem + '.log')} 2>&1 < /dev/null & echo $!; else exit 3; fi")
        proc = self._ssh(command, input=json.dumps(spec, sort_keys=True))
        if proc.returncode != 0 or not proc.stdout.strip().isdigit():
            raise RuntimeError("remote spawn failed: "+proc.stderr.strip())
        pid = int(proc.stdout.strip())
        return {"pid": pid, "pid_start": self._ssh(f"ps -o lstart= -p {pid}").stdout.strip() or None}

    def alive(self, unit, attempt, handle):
        """True or False from ``ps``; None when ssh itself failed (unknown is not dead)."""
        pid = handle.get("pid")
        if not pid:
            return False
        try:
            done = self._ssh(f"ps -o lstart= -p {int(pid)}")
        except (subprocess.SubprocessError, OSError):
            return None
        if done.returncode == 255:
            return None
        out = done.stdout.strip()
        return bool(out) and (not handle.get("pid_start") or out == handle["pid_start"])

    def progress(self, unit, attempt):
        """Return the last remote progress line's first word (a token that changes with progress), or None."""
        try:
            out = self._ssh(f"tail -n 1 {shlex.quote(self._stem(unit, attempt) + '.json.progress')} 2>/dev/null").stdout.strip()
        except (subprocess.SubprocessError, OSError):
            return None
        return out.split()[0] if out else None

    def fetch(self, unit, attempt):
        """Stream the finished remote action file and sidecar back, or None until ``.done`` exists."""
        stem = self._stem(unit, attempt)
        try:
            if self._ssh(f"test -f {shlex.quote(stem + '.json.done')} && echo ok").stdout.strip() != "ok":
                return None
            raw = self._ssh(f"cat {shlex.quote(stem + '.json')}", text=False, timeout=600)
            side = self._ssh(f"cat {shlex.quote(stem + '.json.sha256')}")
        except (subprocess.SubprocessError, OSError):
            return None
        if raw.returncode != 0 or side.returncode != 0:
            return None
        return raw.stdout, side.stdout

    def prune(self, unit, attempt):
        """Delete the attempt's work directory on the RAM disk (its quote indexes)."""
        work = f"{self.root}/work/{os.path.basename(self._stem(unit, attempt))}"
        try:
            self._ssh(f"{self._identity()} && rm -rf {shlex.quote(work)}")
        except (subprocess.SubprocessError, OSError):
            pass

    def drop_bundle(self, unit):
        """Delete the arm's staged bundle from the RAM disk once no more of its units will run."""
        try:
            self._ssh(f"{self._identity()} && rm -rf {shlex.quote(self.bundle_dir_for(unit))}")
        except (subprocess.SubprocessError, OSError):
            pass

    def kill(self, unit, attempt, handle):
        """Stop the remote worker: by pid only when its start time still matches, and by its spec path.

        The spec path names one attempt of one unit, so it can never match another process; the pid is
        not trusted alone because a pid can be reused. The pattern is written so that it does not match
        the shell that carries it.
        """
        pid, start = handle.get("pid"), handle.get("pid_start")
        pattern = shlex.quote((self._stem(unit, attempt) + ".spec.json").replace(".", "[.]"))
        by_pid = (f"[ \"$(ps -o lstart= -p {int(pid)})\" = {shlex.quote(start)} ] && kill {int(pid)}; "
                  if pid and start else "")
        try:
            self._ssh(f"{by_pid}pkill -f {pattern} 2>/dev/null; true")
        except (subprocess.SubprocessError, OSError):
            pass


class ResourceProbe:
    """Live WSL/Windows RAM and C: headroom, in GiB; any figure it cannot read is None.

    Parameters
    ----------
    c_mount : str
        Where the host's C: drive is mounted (``/mnt/c``).
    windows : callable or None
        ``() -> float`` Windows available GiB, or None when not measurable.
        The coordinator treats an unknown figure as a stop (fail closed).

    Examples
    --------
    ::

        probe = ResourceProbe("/mnt/c", windows_reader(SKILL_SCRIPTS))
        probe()["wsl_available_gb"]
    """

    def __init__(self, c_mount="/mnt/c", windows=None):
        self.c_mount, self.windows = c_mount, windows

    def __call__(self):
        """Return ``wsl_available_gb``, ``windows_available_gb`` and ``c_free_gb`` (None when unknown)."""
        available = None
        try:
            with open("/proc/meminfo", encoding="utf-8") as handle:
                available = next(int(line.split()[1]) for line in handle if line.startswith("MemAvailable:")) / 2**20
        except (OSError, StopIteration, ValueError):
            pass
        try:
            c_free = shutil.disk_usage(self.c_mount).free / 2**30
        except OSError:
            c_free = None
        windows = None
        if self.windows is not None:
            try:
                windows = self.windows()
                windows = None if windows is None else float(windows)
            except Exception:
                windows = None
        return {"wsl_available_gb": available, "windows_available_gb": windows, "c_free_gb": c_free}


def _skill_module(scripts, name):
    """Import one helper of the mac-compute-worker skill (read-only), or return None."""
    if scripts and os.path.isdir(scripts) and scripts not in sys.path:
        sys.path.insert(0, scripts)
    try:
        import importlib
        return importlib.import_module(name)
    except Exception:
        return None


def windows_reader(scripts=SKILL_SCRIPTS, max_age=120):
    """Return a ``() -> GiB or None`` reader of Windows available RAM from the skill's probe cache.

    It reuses ``compute_state.windows_available_cached`` (never runs PowerShell in the foreground) but
    accepts a figure only up to ``max_age`` seconds old, not the skill's 900 s: placement happens within
    a ramp window of 300 s, so an older reading is unknown (a stop) and a detached refresh is requested.
    """
    def read():
        state = _skill_module(scripts, "compute_state")
        value = state.windows_available_cached(max_age=max_age, refresh=True) if state else None
        return None if value is None else value / 2**30
    return read


def skill_certificate(job_type, scripts=SKILL_SCRIPTS, mac_host="mac"):
    """Return a ``() -> bool`` that is true while the skill holds a valid Mac certificate for ``job_type``.

    ``compute-run certify`` writes the certificate; a missing, failed, expired or other-host
    certificate, or an unimportable skill, is False (the Mac stays offline).
    """
    def valid():
        run = _skill_module(scripts, "compute_run")
        try:
            return bool(run and run.valid_cert(job_type, mac_host))
        except Exception:
            return False
    return valid


def ticker_lock_ok(python, runtime, document):
    """Run ``python -m index_options.ticker_lock DOC`` against the runtime (ADR-0295).

    Returns
    -------
    bool
        True on exit 0; False only when the check ran and REFUSED the document (exit 1 with no
        traceback).

    Raises
    ------
    RuntimeError
        The check itself could not run (no interpreter, a timeout, an import error, a crash):
        that is a fault to fix, never a ticker list to blame.
    """
    env = dict(os.environ, PYTHONPATH=f"{runtime}:{runtime}/children/index_options", PYTHONDONTWRITEBYTECODE="1")
    try:
        done = subprocess.run([python, "-B", "-m", "index_options.ticker_lock", str(document)], env=env,
                              cwd=str(runtime), capture_output=True, timeout=300)
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError("ticker lock check could not run: "+type(exc).__name__) from exc
    if done.returncode == 0:
        return True
    if done.returncode == 1 and b"Traceback" not in done.stderr:
        return False
    raise RuntimeError(f"ticker lock check could not run: exit {done.returncode}")


def _sealed_dir(path):
    """Create a directory only its owner can enter, or refuse when the filesystem cannot make it so.

    A Windows drive mounted in WSL (/mnt/d, 9p) accepts chmod and keeps its old mode, so the mode is read
    back: anything but 0700 owned by this user is an error, never a silently open directory.

    Raises
    ------
    RuntimeError
        The directory is not mode 0700 after chmod (use a WSL ext4 path).
    """
    path = Path(path)
    path.mkdir(parents=True, exist_ok=True)
    os.chmod(path, 0o700)
    info = path.stat()
    if stat.S_IMODE(info.st_mode) != 0o700 or info.st_uid != os.geteuid():
        raise RuntimeError(f"{path} cannot be made private (mode {oct(stat.S_IMODE(info.st_mode))}): "
                           "keep sealed files on WSL ext4, not on a Windows drive")
    return path


class DocumentRunner:
    """Launch the frozen quarter-arm documents (initialize, run, publish, review) on WSL.

    Every step runs from a derived copy of its config whose ``outputs.run_root`` is a sealed directory
    of its own (``<sealed>/runs/<step>``, mode 0700 on WSL ext4): a MIO run writes weekly books and
    objectives to run.log, so nothing about a document lands in a configured, possibly open, place. Run
    roots are not part of a document's identity, so nothing else changes.

    A step is launched at most once while its run is alive or finished: a live process is waited for and
    a SUCCESSFUL earlier run (state ``ran``, exit 0) is adopted. A failed or interrupted run is never
    adopted and never deleted; the next launch gets a fresh root (``<step>.retryN``), because the
    pipeline refuses to reuse a non-empty run directory. A run the owner retired (``retire``) is
    skipped as well. Every document passes the ticker lock first.

    Parameters
    ----------
    python, runtime : str
        Interpreter and the pinned checkout (PYTHONPATH root).
    generated : pathlib.Path
        Where derived run, publish and review documents are written.
    sealed : pathlib.Path
        Private directory (WSL ext4) for document logs and run roots.
    poll : float
        Seconds between ``pgrep`` checks while an earlier launch runs.
    retired : pathlib.Path or None
        JSON list of run directories that must not be adopted again.

    Examples
    --------
    ::

        runner = DocumentRunner(python, runtime, root/"generated", root/"sealed", retired=root/"retired_runs.json")
        outcome = runner.run_arm(spec, "2022q1", "rules", recorded_dir, asof)
    """

    def __init__(self, python, runtime, generated, sealed, poll=30, retired=None):
        self.python, self.runtime, self.poll = python, runtime, poll
        self.generated = Path(generated)
        self.generated.mkdir(parents=True, exist_ok=True)
        self.sealed = _sealed_dir(sealed)
        self.runs = _sealed_dir(self.sealed / "runs")
        self.launches = self.generated / "launch"
        self.retired = Path(retired) if retired else None

    @staticmethod
    def retire(path, run_dir):
        """Record a run directory so no later launch adopts it (the directory itself is left untouched)."""
        items = _read_json(path, [])
        if str(run_dir) not in items:
            atomic_write(path, json.dumps(items + [str(run_dir)]).encode())

    def _roots(self, step):
        return [self.runs / step] + sorted(self.runs.glob(step + ".retry*"))

    def _dirs(self, step):
        return [d for root in self._roots(step) if root.is_dir() for d in root.iterdir() if d.is_dir()]

    def run_dirs(self, step):
        """Every run directory a step has now (running, failed or finished): where a live document logs."""
        return self._dirs(step)

    def latest_run(self, step):
        """Return the newest SUCCESSFUL, not retired run directory of a step, or None."""
        retired = set(_read_json(self.retired, [])) if self.retired else set()
        good = []
        for path in self._dirs(step):
            result = _read_json(path / "result.json")
            if (isinstance(result, dict) and result.get("state") == "ran" and result.get("exit_code") == 0
                    and str(path) not in retired):
                good.append(path)
        return max(good, key=lambda p: p.stat().st_mtime) if good else None

    def _launch_config(self, step, cfg):
        """Write the config this launch runs: the step's own sealed run root, a fresh one after any leftover."""
        used = [r for r in self._roots(step) if r.is_dir() and any(r.iterdir())]
        root = self.runs / (step if not used else f"{step}.retry{len(used)}")
        _sealed_dir(root)
        base = json.loads(Path(cfg).read_text())
        base["outputs"] = {**base.get("outputs", {}), "run_root": str(root)}
        self.launches.mkdir(parents=True, exist_ok=True)
        out = self.launches / (f"{step}.json" if not used else f"{step}.retry{len(used)}.json")
        out.write_text(json.dumps(base, indent=1))
        return out

    def _alive(self, step):
        pattern = f"dskit.pipeline run {re.escape(str(self.launches / step))}\\."
        return subprocess.run(["pgrep", "-f", pattern], capture_output=True).returncode == 0

    def run(self, step, cfg, asof):
        """Ticker-lock then run one pipeline document once; adopt a live or successful earlier launch."""
        started = time.time()
        try:
            locked = ticker_lock_ok(self.python, self.runtime, cfg)
        except RuntimeError:
            return {"step": step, "rc": None, "minutes": 0., "ok": False, "run_dir": None,
                    "document_hash": None, "refused": "ticker_lock_unavailable"}
        if not locked:
            return {"step": step, "rc": None, "minutes": 0., "ok": False, "run_dir": None,
                    "document_hash": None, "refused": "ticker_lock"}
        env = dict(os.environ, PYTHONPATH=f"{self.runtime}:{self.runtime}/children/index_options",
                   OMP_NUM_THREADS="1", MKL_NUM_THREADS="1", OPENBLAS_NUM_THREADS="1", PYTHONUNBUFFERED="1")
        while self._alive(step):
            time.sleep(self.poll)
        rc = 0
        if self.latest_run(step) is None:
            launch = self._launch_config(step, cfg)
            fd = os.open(self.sealed / f"{step}.log", os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            with os.fdopen(fd, "w") as fh:
                rc = subprocess.run([self.python, "-m", "dskit.pipeline", "run", str(launch), "--asof", asof],
                                    cwd=self.runtime, env=env, stdout=fh, stderr=subprocess.STDOUT).returncode
        good = self.latest_run(step)
        newest = good or max(self._dirs(step), key=lambda p: p.stat().st_mtime, default=None)
        result = _read_json(newest / "result.json", {}) if newest else {}
        return {"step": step, "rc": rc, "minutes": round((time.time() - started) / 60, 1),
                "ok": rc == 0 and good is not None, "run_dir": str(newest) if newest else None,
                "document_hash": result.get("document_hash")}

    def recorded_config(self, run_cfg, label, recorded_dir):
        """Write the run config with ``recorded`` on its MIOPacketStudy node."""
        base = json.loads(Path(run_cfg).read_text())
        nodes = [k for k, v in base["pipeline"].items() if str(v.get("uses", "")).endswith(":MIOPacketStudy")]
        if len(nodes) != 1:
            raise ValueError("run config must hold exactly one MIOPacketStudy node")
        base["pipeline"][nodes[0]]["params"]["recorded"] = str(recorded_dir)
        base["notes"] = (base.get("notes", "") + f" Methods recorded by workers are re-derived from {recorded_dir}.").strip()
        out = self.generated / f"run-{label}.json"
        out.write_text(json.dumps(base, indent=1))
        return out

    def closing(self, run_cfg, kind, entry, label, complete):
        """Write the publish, review or abandon document for one closed run."""
        base = json.loads(Path(run_cfg).read_text())
        study = base["pipeline"]["study"]["params"]["study"] if "study" in base["pipeline"] else next(
            v["params"]["study"] for v in base["pipeline"].values() if str(v.get("uses", "")).endswith(":MIOPacketStudy"))
        receipt = {"run_dir": entry["run_dir"], "document_hash": entry["document_hash"],
                   "node": "close_packet", "port": "receipt"}
        doc = {"name": f"{kind}-{label}",
               "outputs": {"run_root": str(self.runs / f"{kind}-{label}")}}
        if kind == "publish":
            doc["notes"] = f"Publish {label} replay receipt."
            doc["pipeline"] = {"results": {"uses": "dskit.pipeline.study_protocol:StudyPacketResults",
                                           "params": {"study": study, "result_receipts": [receipt]}}}
        else:
            action = "review" if complete else "abandon"
            reason = (f"{label} walk-forward replay of frozen rules: close receipt complete." if complete
                      else f"{label} replay incomplete; retained.")
            params = {"study": study, "action": action, "reason": reason}
            if complete:
                params["result_receipt"] = receipt
            doc["notes"] = reason
            doc["pipeline"] = {"review": {"uses": "dskit.pipeline.study_protocol:StudyProtocolControl",
                                          "params": params}}
        out = self.generated / f"{kind}-{label}.json"
        out.write_text(json.dumps(doc, indent=1))
        return out

    def run_arm(self, spec, quarter, arm, recorded_dir, asof):
        """Run initialize, the run, publish and review/abandon; return states and hashes only.

        ``recorded_dir`` None runs the arm exactly as a plain WSL document (no worker units).
        """
        label = f"{quarter}-{arm}"
        init = self.run(f"init-{label}", spec["initialize"], asof)
        if not init["ok"]:
            return {"ok": False, "stage": "initialize", "entry": init}
        cfg = spec["run"] if recorded_dir is None else self.recorded_config(spec["run"], label, recorded_dir)
        entry = self.run(f"run-{label}", cfg, asof)
        if not entry["ok"]:
            # A run that died after rejecting a worker file still says why: the log's structured fields only.
            return {"ok": False, "stage": "run", "entry": entry,
                    "rejections": rejections_from_log(entry["run_dir"]) if entry.get("run_dir") else []}
        report = recorded_from_report(entry["run_dir"])
        complete = report["complete"]
        if not complete and (report["recorded_from"] or report["recorded_rejected"]):
            # Abandoning spends the packet's exposure. When a worker file was in play the owner decides, not this loop.
            return {"ok": False, "stage": "incomplete_with_worker_files", "entry": entry,
                    "rejections": report.get("rejections", [])}
        if complete and not self.run(f"publish-{label}", self.closing(cfg, "publish", entry, label, True), asof)["ok"]:
            return {"ok": False, "stage": "publish", "entry": entry, "rejections": report.get("rejections", [])}
        kind = "review" if complete else "abandon"
        if not self.run(f"{kind}-{label}", self.closing(cfg, kind, entry, label, complete), asof)["ok"]:
            return {"ok": False, "stage": kind, "entry": entry, "rejections": report.get("rejections", [])}
        return {"ok": True, "complete": complete, "run_dir": entry["run_dir"], "minutes": entry["minutes"],
                "document_hash": entry["document_hash"], "recorded_from": sorted(report["recorded_from"]),
                "methods": report["methods"], "rejections": report.get("rejections", [])}


def recorded_from_report(run_dir):
    """Hash-only account of a closed quarter-arm run: which methods a worker supplied, and from whom.

    Reads the close artifact's ``audit.recorded_from`` and COUNTS (never prints) the
    ``recorded_rejected`` and ``recorded_absent`` events in the run log.

    Returns
    -------
    dict
        ``complete``, ``methods`` (id to status), ``recorded_from`` (id to host label,
        attempt, action-file sha256, bundle digest), ``recorded_rejected`` and
        ``recorded_absent`` counts, and ``rejections`` (method, week, check, category,
        ratio of each rejected worker result: the audit's list, else the log's sanitized
        structured fields) and ``sealed`` (the directory is under ``holdout/``). No value from any result.
        The list is always whole: the coordinator records it in the owner-only ledger. What a person is shown
        goes through :func:`recorded_from_view`, which cuts a sealed run's list to unit, check and count.
    """
    run_dir = Path(run_dir)
    art = _close_artifact(run_dir)
    origin = {}
    for method, entry in (art.get("audit", {}).get("recorded_from") or {}).items():
        host = entry.get("host")
        origin[method] = {"host": host.get("label") if isinstance(host, dict) else None,
                          "attempt": entry.get("attempt"), "sha256": entry.get("sha256"),
                          "bundle_manifest_sha256": entry.get("bundle_manifest_sha256")}
    counts = {"recorded_rejected": 0, "recorded_absent": 0}
    log = run_dir / "run.log"
    if log.exists():
        with open(log, encoding="utf-8", errors="replace") as handle:
            for line in handle:
                if "MIO_EVENT " in line:
                    for kind in counts:
                        if f'"type":"{kind}"' in line:
                            counts[kind] += 1
    rejected = art.get("audit", {}).get("recorded_rejections")
    rejections = ([_clean_rejection(r) for r in rejected] if isinstance(rejected, list)
                  else rejections_from_log(run_dir))
    report = {"complete": bool(art.get("complete")), "recorded_from": origin, **counts, "rejections": rejections,
              "methods": {m["id"]: m["status"] for m in art.get("results", [])}}
    report["sealed"] = Phases.HOLDOUT_DIR in run_dir.resolve().parts
    return report


def recorded_from_view(report):
    """What the ``recorded-from`` command prints: a sealed (holdout) run shows each rejected method, check and a count only.

    A ratio, week, category or run of a sealed quarter can be turned back into a dollar value or a timeline, and this
    command cannot know whether the owner unsealed the quarter, so it never prints them for a holdout directory.
    """
    if not report.get("sealed"):
        return report
    counts = {}
    for row in report["rejections"]:
        key = (row.get("method"), row.get("check"))
        counts[key] = counts.get(key, 0) + 1
    # Per-method status, host, attempt and digests would date the quarter's progress: counts only.
    shown = {k: v for k, v in report.items() if k not in ("rejections", "recorded_from", "methods")}
    shown["recorded_from_count"], shown["methods_count"] = len(report["recorded_from"]), len(report["methods"])
    shown["rejections"] = []
    shown["rejections_sealed"] = [{"method": m, "check": c, "count": n} for (m, c), n in sorted(counts.items(), key=str)]
    return shown


class Coordinator:
    """The scheduling loop over a UnitQueue, hosts, a probe and a document launcher.

    Parameters
    ----------
    root : str or pathlib.Path
        Queue root: ``queue.sqlite``, ``recorded/<quarter>-<arm>/``, ``status.json``, ``parity.json``.
    plan : dict
        ``quarters``: ordered list of ``{"id", "asof", "arms": [{"id", "methods",
        "bundle_sha256", "plan_sha256", "spec", "registry"}]}``. An arm with no methods runs as a
        plain WSL document. ``spec`` is passed to the document launcher untouched. ``registry``
        names the arm's exposure registry (default: the arm id): documents of one registry run
        one at a time, in plan order, because the registry refuses a second active claim and
        needs the predecessor's disposition; different registries overlap.
    hosts : list of Host
        In placement order (the first online host with a free slot wins).
    documents : callable
        ``(spec, quarter, arm, recorded_dir_or_None, asof) -> dict`` with ``ok``; run on a thread.
    probe : callable
        ``() -> {"wsl_available_gb", "windows_available_gb", "c_free_gb"}``; None fails closed.
    limits : dict
        ``unit_ram_gb`` (8 is a placeholder, never measured on a real unit), ``document_ram_gb``,
        ``wsl_reserve_gb``, ``windows_reserve_gb``, ``mac_reserve_gb``, ``c_floor_gb`` (the 3 GB
        stop), ``c_job_gb`` (growth one WSL job puts on C:), ``ramp_seconds`` (a started job's
        RAM and C: are counted as not yet used for this long), ``stale_seconds``,
        ``max_attempts`` (leases per host name; a host not named gets 2, never unlimited),
        ``max_refunds`` (resource stops and spawn failures refunded per unit and host, 3),
        ``parity_grace_seconds`` (4 h). A value below ``LIMIT_FLOORS`` is raised to the floor and an
        unknown key refuses: a config can tighten a safeguard, never loosen it.
    clock : callable
        ``() -> float`` seconds; injectable for tests.
    live_runs : callable or None
        ``(quarter, arm) -> [run directory, ...]``: where the arm's running document logs. Each pass the coordinator
        reads the new ``recorded_rejected`` lines of those logs, so a WSL rejection pauses the Mac within one pass
        instead of when the document returns (which can be hours later). None: rejections reach the ledger when
        the document returns.
    parity : dict
        ``{"unit": "quarter/arm/method"}``, REQUIRED when a Mac host is given (the gate cannot be
        skipped): the first unit, solved on the Mac and on WSL and compared with ``Similarity``.
        Until a verdict, the Mac runs only that unit and WSL only its twin. ONLY a comparison writes
        a verdict. If the pair is not placed within ``parity_grace_seconds``, or a parity unit
        ends without a result (attempts spent, no host), no verdict is written: the Mac keeps its unit,
        shows the reason in status, and the other units are released to WSL slots or to the documents.
        A failed comparison disables the Mac for the queue root.

    Examples
    --------
    ::

        coordinator = Coordinator(root, plan, [mac, wsl], runner.run_arm, ResourceProbe(), limits,
                                  parity={"unit": "2022q1/rules/w1_010"})
        while coordinator.tick():
            time.sleep(60)
    """

    DEFAULT_ATTEMPTS = 2
    SOLVED_STATES = ("verified", "replayed", "solved_local")

    def __init__(self, root, plan, hosts, documents, probe, limits, clock=time.time, parity=None, phases=None,
                 live_runs=None):
        self.root, self.phases, self.live_runs = Path(root), phases, live_runs
        self._log_pos, self.ledger_error = {}, None
        self.plan, self.hosts, self.documents, self.probe, self.clock = plan, list(hosts), documents, probe, clock
        defaults = {"unit_ram_gb": 8., "document_ram_gb": 8., "wsl_reserve_gb": 6., "windows_reserve_gb": 2.,
                    "mac_reserve_gb": 6., "c_floor_gb": 3., "c_job_gb": 3.4, "ramp_seconds": 300.,
                    "stale_seconds": 75*60., "max_attempts": {}, "max_refunds": 3, "parity_grace_seconds": 4*3600.}
        unknown = set(limits or {}) - set(defaults)
        if unknown:
            raise ValueError("unknown limits: "+", ".join(sorted(unknown)))
        self.limits = {**defaults, **(limits or {})}
        for name in set(defaults)-{"max_attempts"}:
            self.limits[name] = _limit_number(name, self.limits[name])
        for name, floor in LIMIT_FLOORS.items():
            self.limits[name] = max(self.limits[name], floor)
        self.limits["max_refunds"] = int(self.limits["max_refunds"])
        self.caps = {"mac": self.DEFAULT_ATTEMPTS, "wsl": self.DEFAULT_ATTEMPTS, **(self.limits["max_attempts"] or {})}
        if any(type(v) is not int or v < 1 for v in self.caps.values()):
            raise ValueError("max_attempts must be a positive integer per host")
        has_mac = any(not h.probe_limited for h in self.hosts)
        unit = parity.get("unit") if isinstance(parity, dict) else None
        if has_mac and not (isinstance(unit, str) and unit):
            raise ValueError("a Mac host needs parity.unit: the first-parity gate cannot be skipped")
        self.queue = UnitQueue(self.root / "queue.sqlite")
        self.rejections, self._rejections_shown = RejectionLedger(self.root / "rejections.jsonl"), (0, 0)
        self._fallbacks_shown = frozenset()
        self.handles, self.arm_specs = {}, {}
        self._seen, self._online, self.reasons = {}, {}, {}
        self._released = False  # parity pending but not decisive: the other units may use WSL (no verdict)
        self._threads, self._done, self._doc_started = {}, {}, {}
        self._spawning, self._stage_locks, self._unplaced_since = {}, {}, None
        self.skipped, self.orphans, self.no_units = {}, {}, {}
        self.parity_unit = unit if has_mac else None
        self.twin_unit = self.parity_unit + "@parity" if self.parity_unit else None
        self.arm_order, self.registry_of, self.chains, self.plan_units = [], {}, {}, set()
        self.quarter_order = {q["id"]: i for i, q in enumerate(plan["quarters"])}
        for quarter in plan["quarters"]:
            for arm in quarter["arms"]:
                key = f"{quarter['id']}/{arm['id']}"
                self.arm_specs[key] = {**arm, "quarter": quarter["id"], "asof": quarter.get("asof")}
                self.arm_order.append(key)
                registry = os.path.realpath(arm["registry"]) if arm.get("registry") else arm["id"]
                self.registry_of[key] = registry
                self.chains.setdefault(registry, []).append(key)
                self.queue.add_arm(quarter["id"], arm["id"])
                for method in arm["methods"]:
                    self.queue.add(f"{key}/{method}", quarter["id"], arm["id"], method)
                    self.plan_units.add(f"{key}/{method}")
        if self.plan_units and not has_mac and not any(h.slots > 0 for h in self.hosts):
            # Nothing could ever run a unit: the first tick would reject every waiting unit, so a Mac added later gets none.
            raise ValueError("no Mac host and wsl_slots is 0: no host can run a unit. Set wsl_slots to 1 or 2, or configure "
                             "the mac block with parity.unit")
        if self.parity_unit:
            if self.parity_unit not in self.plan_units:
                raise ValueError("parity unit is not in the plan: "+self.parity_unit)
            first = self.queue.unit(self.parity_unit)
            self._parity_arm = f"{first['quarter']}/{first['arm']}"
            self.queue.add(self.twin_unit, first["quarter"], first["arm"], first["method"], role="parity")
            self.plan_units.add(self.twin_unit)
            macs = {h.name for h in self.hosts if not h.probe_limited}
            leased_on = {h["host"] for h in first["history"] if h["event"] == "leased"}
            elsewhere = sorted(leased_on - macs)
            if self.parity_state() == "pending" and elsewhere:
                # A unit WSL once held would finish on WSL and the twin too: WSL against WSL, the Mac never compared.
                raise ValueError(f"parity.unit {self.parity_unit} was leased on a host other than the Mac ({', '.join(elsewhere)}): "
                                 "name a unit that is still pending with no leases, so the Mac solves it for the comparison")
            if (self.parity_state() == "pending" and first["status"] in self.SOLVED_STATES and not leased_on & macs
                    and not self._parity_file(first, first["attempt"]).exists()):
                # E.g. a WSL-first queue gaining its Mac later: the Mac would never be given a unit that is already solved.
                # A unit the Mac held and lost is not this case: _parity_watch reports it and releases the others.
                raise ValueError(f"parity.unit {self.parity_unit} is already solved ({first['status']}) without a held parity "
                                 "file: name a unit that has not been solved yet, so the Mac can solve it for the comparison")
        for row in self.queue.units(status="returned"):
            if row["role"] == "primary" and row["unit_id"] != self.parity_unit:  # only a live parity pair settles a held result
                self._transition(row["unit_id"], "rejected", row["attempt"],
                                 note="no comparison: held for a parity unit the config no longer names; the document solves it")
        for row in self.queue.units(status="leased"):
            self.handles[row["unit_id"]] = {"pid": row["pid"], "pid_start": row["pid_start"]}

    # -- helpers -----------------------------------------------------------
    def _host(self, name):
        return next((h for h in self.hosts if h.name == name), None)

    def _is_online(self, host):
        """Ask a host once per pass (an ssh round trip is not free)."""
        if host.name not in self._online:
            self._online[host.name] = bool(host.online())
        return self._online[host.name]

    def _stamp(self):
        """Format the coordinator clock as the one UTC stamp the queue stores."""
        return stamp(self.clock())

    @staticmethod
    def _epoch(text):
        """Epoch seconds of a stamp, or None when it does not parse (never an exception, never 0)."""
        try:
            return calendar.timegm(time.strptime(text, STAMP_FORMAT))
        except (TypeError, ValueError):
            return None

    def _age(self, text):
        """Seconds since a stamp; an unparseable stamp reads as no age, so it can never kill work."""
        epoch = self._epoch(text)
        return 0 if epoch is None else self.clock() - epoch

    def _transition(self, unit_id, status, attempt, **fields):
        fields.setdefault("now", self._stamp())
        return self.queue.transition(unit_id, status, attempt, **fields)

    def recorded_dir(self, quarter, arm):
        """Return the directory the arm's document reads its action files from (under ``holdout/`` for a sealed quarter)."""
        base = self.phases.holdout_path if self.phases and self.phases.holds(quarter) else self.root
        return base / "recorded" / f"{quarter}-{arm}"

    def _units(self, **where):
        """Unit rows that are part of the CURRENT plan (a restarted queue may hold units of a retired one)."""
        return [u for u in self.queue.units(**where) if u["unit_id"] in self.plan_units]

    def _wsl_hosts_busy(self):
        return [u for u in self.queue.units(status="leased")
                if (self._host(u["host"]) or Host).probe_limited]

    def _priority(self, unit):
        remaining = sum(1 for u in self._units(quarter=unit["quarter"], arm=unit["arm"])
                        if u["status"] not in _TERMINAL)
        return (self.quarter_order[unit["quarter"]], remaining, unit["unit_id"])

    def _attempts_on(self, unit, host):
        """Leases on a host that count against its cap (a resource stop is refunded, up to ``max_refunds``)."""
        leases = sum(1 for h in unit["history"] if h["event"] == "leased" and h["host"] == host)
        refunds = sum(1 for h in unit["history"] if h.get("refund") and h["host"] == host)
        return leases - min(refunds, self.limits["max_refunds"])

    def _eligible(self, unit):
        """Hosts that could ever run this unit: slots above zero, the parity roles, and no Mac after a failed parity."""
        if unit["role"] == "parity":
            return [h for h in self.hosts if h.probe_limited]  # the twin runs even at wsl_slots 0
        state = self.parity_state()
        hosts = [h for h in self.hosts if h.slots > 0]
        if unit["unit_id"] == self.parity_unit and state == "pending":
            return [h for h in hosts if not h.probe_limited]
        if state == "failed" or (state == "pending" and self._released):
            return [h for h in hosts if h.probe_limited]
        return hosts

    def _exhausted(self, unit):
        """True when no eligible host is left for the unit (none exists, or each has used its attempts)."""
        return all(self._attempts_on(unit, h.name) >= self.caps.get(h.name, self.DEFAULT_ATTEMPTS)
                   for h in self._eligible(unit))

    # -- parity -----------------------------------------------------------------
    def _parity_current(self, saved):
        """A verdict counts only for this unit, these thresholds and this bundle."""
        return (saved.get("unit") == self.parity_unit
                and saved.get("thresholds_sha256") == Similarity.thresholds_sha256()
                and saved.get("bundle_sha256") == self.arm_specs[self._parity_arm]["bundle_sha256"])

    def parity_state(self):
        """``None`` (no Mac host), ``pending``, ``passed`` or ``failed`` (a stale verdict reads as failed)."""
        if not self.parity_unit:
            return None
        saved = _read_json(self.root / "parity.json")
        if not isinstance(saved, dict):
            return "pending"
        return "passed" if saved.get("passed") is True and self._parity_current(saved) else "failed"

    def parity_reason(self):
        """The fixed reason code of a failed verdict (``stale_verdict`` when it names another unit, bundle or thresholds)."""
        saved = _read_json(self.root / "parity.json")
        if not isinstance(saved, dict):
            return None
        return saved.get("reason") if self._parity_current(saved) else "stale_verdict"

    def _parity_file(self, unit, attempt):
        return self.root / "parity" / f"{unit['unit_id'].replace('/', '__').replace('@', '_')}.{attempt}.json"

    def _write_parity(self, passed, reason, primary, twin, raws):
        """Write the verdict. Only a completed comparison may call this, and a pass writes it LAST."""
        verdict = {"passed": passed, "reason": reason, "unit": self.parity_unit,
                   "thresholds_sha256": Similarity.thresholds_sha256(),
                   "bundle_sha256": self.arm_specs[self._parity_arm]["bundle_sha256"],
                   "hosts": {"first": primary["host"], "second": twin["host"]},
                   "sha256": {"first": hashlib.sha256(raws[0]).hexdigest(), "second": hashlib.sha256(raws[1]).hexdigest()},
                   # information only (the owner wants similar, not equal): do the two hosts' integer actions match?
                   "actions_match": Similarity.actions_match(json.loads(raws[0]), json.loads(raws[1]))}
        atomic_write(self.root / "parity.json", json.dumps(verdict, indent=1, sort_keys=True).encode())

    def check_parity(self):
        """Compare the held parity pair once both returned, and act on the outcome crash-safely.

        A pass is acted on FIRST (publish the Mac file, verify the twin) and the verdict is written last,
        so a crash or a full disk in between leaves no verdict and the next pass finishes the job (units
        already verified are skipped). A failure writes the verdict FIRST and rejects the held units
        after, which ``settle_failed_parity`` re-applies on every pass. Nothing but a comparison writes
        a verdict: a unit that ended without a result, or a held file that cannot be read, writes none.
        """
        state = self.parity_state()
        if state == "failed":
            self._settle_failed_parity()
            return
        if state != "pending":
            return
        primary, twin = self.queue.unit(self.parity_unit), self.queue.unit(self.twin_unit)
        if not all(u["status"] in ("returned", "verified") for u in (primary, twin)):
            return
        if not any(h.name == primary["host"] and not h.probe_limited for h in self.hosts):
            self.reasons["parity"] = (f"the first parity result came from {primary['host']}, not the Mac: no pass is written "
                                      "(the Mac must solve the first unit for the comparison)")
            return
        try:
            raws = [self._parity_file(u, u["attempt"]).read_bytes() for u in (primary, twin)]
            passed, reason = Similarity.compare(json.loads(raws[0]), json.loads(raws[1]))
        except (OSError, ValueError):
            self.reasons["parity"] = "a held parity file cannot be read: no comparison yet, retrying next pass"
            return
        if not passed:
            self._write_parity(False, reason, primary, twin, raws)
            self._settle_failed_parity()
            return
        if primary["status"] == "returned":
            self._publish(primary, primary["attempt"], raws[0], hashlib.sha256(raws[0]).hexdigest(), json.loads(raws[0]))
        if twin["status"] == "returned":
            self._transition(twin["unit_id"], "verified", twin["attempt"], result_sha256=hashlib.sha256(raws[1]).hexdigest())
        self._write_parity(True, reason, primary, twin, raws)

    def _settle_failed_parity(self):
        """Reject a failed pair's held units (idempotent; a crash after the verdict is repaired here)."""
        for unit_id in (self.parity_unit, self.twin_unit):
            unit = self.queue.unit(unit_id)
            if unit["status"] == "returned":
                self._transition(unit_id, "rejected", unit["attempt"], note="parity failed: "+str(self.parity_reason()))

    def _parity_watch(self):
        """Say why parity is not moving, and release the OTHER units to WSL without ending the gate.

        A parity unit not placed within the grace period, or ended without a result, is an
        infrastructure fault: nothing is killed and no verdict is written. The Mac keeps its unit and
        the verdict still comes from a real comparison, if one ever happens.
        """
        state = self.parity_state()
        self._released = False
        if state == "failed":
            slots = sum(h.slots for h in self.hosts if h.probe_limited)
            self.reasons["parity"] = (f"failed ({self.parity_reason()}): the Mac is disabled; "
                                      + ("units run on WSL slots" if slots else
                                         "wsl_slots is 0, so every unit is rejected and the documents solve locally"))
            return
        if state != "pending":
            self._unplaced_since = None
            return
        primary, twin = self.queue.unit(self.parity_unit), self.queue.unit(self.twin_unit)
        ended = [u["unit_id"] for u in (primary, twin) if u["status"] in ("rejected", "superseded")]
        if primary["status"] in self.SOLVED_STATES and self._primary_ended(primary):
            ended.append(primary["unit_id"])
        if ended:
            self._released = True
            if primary["status"] == "returned":  # its twin ended: nothing can compare this held result, so it must not hold the arm
                self._transition(primary["unit_id"], "rejected", primary["attempt"],
                                 note="no comparison: the parity twin ended without a result; the document solves this unit")
            self.reasons["parity"] = (f"no comparison: {', '.join(ended)} ended without a result; no verdict is written, "
                                      "the Mac stays gated and the other units use WSL slots or the documents. To use the "
                                      "Mac again, restart with parity.unit naming a unit still pending with no leases")
            return
        unplaced = [u["unit_id"] for u in (primary, twin) if u["status"] == "pending"]
        if not unplaced:
            self._unplaced_since = None
            self.reasons["parity"] = "pending: both parity units are running or held for the comparison"
            return
        now = self.clock()
        self._unplaced_since = now if self._unplaced_since is None else self._unplaced_since
        waited = now - self._unplaced_since
        self._released = waited > self.limits["parity_grace_seconds"]
        self.reasons["parity"] = (f"pending: {', '.join(unplaced)} not placed for {int(waited)} s"
                                  + ("; the other units are released to WSL (no verdict, nothing killed)" if self._released
                                     else f"; other units wait, released after {int(self.limits['parity_grace_seconds'])} s"))

    # -- resources -----------------------------------------------------------
    def _recent_wsl_starts(self):
        """WSL units and documents started within the ramp window: their RAM and C: growth is not yet visible."""
        ramp = self.limits["ramp_seconds"]
        units = sum(1 for u in self._wsl_hosts_busy() if u["started"] and self._age(u["started"]) < ramp)
        docs = sum(1 for t in self._doc_started.values() if self.clock() - t < ramp)
        return units + docs

    def _wsl_admit(self, ram_gb, keep_document_room):
        """How many jobs of ``ram_gb`` the shared WSL/Windows/C: limits admit now, and why not.

        ``keep_document_room`` holds back one document's RAM, and the caller passes it only while a
        document is due; every figure the probe cannot read (Windows included) admits nothing.
        """
        r, lim = self.probe(), self.limits
        recent = self._recent_wsl_starts()
        c = r.get("c_free_gb")
        if c is None:
            return 0, "C: free unknown"
        c_slots = int((c - lim["c_floor_gb"] - recent * lim["c_job_gb"]) // lim["c_job_gb"])
        if c_slots < 1:
            return 0, "C: free below the floor after this job's projected growth"
        if r.get("windows_available_gb") is None:
            return 0, "Windows available RAM unknown"
        if r.get("wsl_available_gb") is None:
            return 0, "WSL memory unknown"
        # WSL and Windows share physical RAM, so each is checked on its own against its own reserve.
        wsl = r["wsl_available_gb"] - lim["wsl_reserve_gb"] - recent * ram_gb
        if keep_document_room:
            wsl -= lim["document_ram_gb"]
        windows = r["windows_available_gb"] - lim["windows_reserve_gb"] - recent * ram_gb
        slots = max(0, min(int(min(wsl, windows) // ram_gb), c_slots))
        return slots, (None if slots else "RAM below the reserve plus one job")

    def _mac_slots(self, host):
        """Mac slots by its own free RAM; unknown RAM is zero slots."""
        available = host.available_gb()
        if available is None:
            return 0
        recent = sum(1 for u in self.queue.units(status="leased")
                     if u["host"] == host.name and u["started"] and self._age(u["started"]) < self.limits["ramp_seconds"])
        spare = available - self.limits["mac_reserve_gb"] - recent * self.limits["unit_ram_gb"]
        return max(0, int(spare // self.limits["unit_ram_gb"]))

    def stop_for_disk(self):
        """Apply the coordinator's own 3 GB stop to WSL WORKERS (a finished one is fetched first, not killed).

        An unreadable C: is reported and stops all new placement (``place``). Documents are stopped
        only by ``disk_guard.sh`` (SIGTERM to ``dskit.pipeline run`` below 3 GB), never here: a
        killed document is found failed (or, after a restart, relaunched) and reported in status.
        """
        c = self.probe().get("c_free_gb")
        if c is None:
            self.reasons["disk"] = "C: free unreadable: nothing is placed on any host until it reads again"
            return
        if c >= self.limits["c_floor_gb"]:
            return
        for unit in self._wsl_hosts_busy():
            host = self._host(unit["host"])
            attempt = unit["attempt"]
            fetched = host.fetch(unit, attempt) if unit["unit_id"] in self.plan_units else None
            if fetched is not None:  # it finished before the stop: keep the result, do not kill a finished unit
                self._accept(unit, attempt, *fetched)
                continue
            host.kill(unit, attempt, self.handles.get(unit["unit_id"], {}))
            self._transition(unit["unit_id"], "pending", attempt, note="C: stop", refund=True)
            self.reasons["wsl"] = "C: free below the floor: workers stopped"

    # -- one scheduling pass -------------------------------------------------
    def _remember_kill(self, host, unit, handle):
        """Remember a worker on an unreachable host: it is killed when the host answers again."""
        path = self.root / "pending_kills.json"
        items = _read_json(path, [])
        items.append({"host": host.name, "unit_id": unit["unit_id"], "attempt": unit["attempt"],
                      "pid": handle.get("pid"), "pid_start": handle.get("pid_start")})
        atomic_write(path, json.dumps(items).encode())

    def _flush_kills(self):
        """Kill the workers left behind on a host that was offline when their lease was requeued."""
        path = self.root / "pending_kills.json"
        items = _read_json(path, [])
        keep = []
        for item in items:
            host = self._host(item["host"])
            if host is None:
                continue
            if not self._is_online(host):
                keep.append(item)
                continue
            unit, handle = {"unit_id": item["unit_id"]}, {"pid": item["pid"], "pid_start": item["pid_start"]}
            host.kill(unit, item["attempt"], handle)
            if host.alive(unit, item["attempt"], handle) is not False:  # still running, or not known to be gone
                keep.append(item)
        if keep != items:
            atomic_write(path, json.dumps(keep).encode())

    def _adopt_handle(self, unit_id, handle):
        self.handles[unit_id] = handle
        self.queue.handle(unit_id, handle.get("pid"), handle.get("pid_start"))

    def _collect_spawns(self):
        """Settle staging threads that finished: record the worker handle, or requeue a failed spawn."""
        for unit_id, (thread, box, attempt, host) in list(self._spawning.items()):
            if thread.is_alive():
                continue
            del self._spawning[unit_id]
            row = self.queue.unit(unit_id)
            if "handle" not in box:
                if row["status"] == "leased" and row["attempt"] == attempt:
                    self._transition(unit_id, "pending", attempt, note=f"spawn failed: {box.get('error')}", refund=True)
            elif row["status"] == "leased" and row["attempt"] == attempt:
                self._adopt_handle(unit_id, box["handle"])
            else:  # the lease ended while the worker was being started: do not leak it
                host.kill(row, attempt, box["handle"])

    def join_spawns(self, timeout=None):
        """Wait for every staging thread (tests and shutdown), then settle them."""
        for thread, *_ in list(self._spawning.values()):
            thread.join(timeout)
        self._collect_spawns()

    def reap(self):
        """Fetch finished units, requeue dead or stale ones."""
        self._collect_spawns()
        self._flush_kills()
        for unit in self.queue.units(status="leased"):
            host = self._host(unit["host"])
            handle = self.handles.get(unit["unit_id"], {"pid": unit["pid"], "pid_start": unit["pid_start"]})
            attempt = unit["attempt"]
            if unit["unit_id"] not in self.plan_units:
                if host is not None:
                    host.kill(unit, attempt, handle)
                self._transition(unit["unit_id"], "pending", attempt, note="unit is not in the current plan", refund=True)
                continue
            if unit["unit_id"] in self._spawning:
                continue  # still being staged or started
            if host is None:
                self._transition(unit["unit_id"], "pending", attempt, note="host no longer configured", refund=True)
                continue
            online = self._is_online(host)
            fetched = host.fetch(unit, attempt) if online else None
            if fetched is not None:
                self._accept(unit, attempt, *fetched)
                continue
            if not online:
                if self._age(unit["last_progress"] or unit["started"]) > self.limits["stale_seconds"]:
                    self._remember_kill(host, unit, handle)  # it may still run: stop it when the host is back
                    self._transition(unit["unit_id"], "pending", attempt, note="host offline past stale limit")
                continue
            token = host.progress(unit, attempt)
            if token is not None and token != self._seen.get(unit["unit_id"]):
                # Progress is judged by CHANGE, stamped with this coordinator's clock: a skewed or
                # unparseable remote stamp can never make a live worker look stale.
                self._seen[unit["unit_id"]] = token
                unit["last_progress"] = self._stamp()
                self.queue.progress(unit["unit_id"], unit["last_progress"])
            alive = host.alive(unit, attempt, handle)
            if alive is False:
                fetched = host.fetch(unit, attempt)  # it may have finished between the fetch and the check
                if fetched is not None:
                    self._accept(unit, attempt, *fetched)
                else:
                    if not handle.get("pid"):
                        # A crash between the remote spawn and the handle record leaves a lease with no pid, and
                        # "no pid" reads as dead: the orphan may still run. Its spec path names this attempt only.
                        host.kill(unit, attempt, handle)
                        self._remember_kill(host, unit, handle)
                    self._transition(unit["unit_id"], "pending", attempt, note="worker exited without a result")
                continue
            if self._age(unit["last_progress"] or unit["started"]) > self.limits["stale_seconds"]:
                if alive is None:  # cannot even ask: do not requeue beside a worker that may still run
                    self._remember_kill(host, unit, handle)
                else:
                    host.kill(unit, attempt, handle)
                self._transition(unit["unit_id"], "pending", attempt, note="stale lease killed")

    def _reject(self, unit, attempt, raw, why, check="action_file_identity"):
        """Keep a refused return aside and record it: a rejection is never silent (category ``file``, no week or ratio)."""
        self.rejections.record(unit["unit_id"], {"check": check, "category": "file"}, run=f"attempt{attempt}",
                               at=self._stamp())
        self._keep_aside("rejected", unit, attempt, raw)
        self._transition(unit["unit_id"], "pending" if not self._exhausted(unit) else "rejected", attempt,
                         note=f"rejected: {why}")

    def _accept(self, unit, attempt, raw, sidecar):
        """Deal with a finished attempt, then delete its working files (a failure above keeps them)."""
        accepted = self._accept_checked(unit, attempt, raw, sidecar)
        host = self._host(unit["host"])
        if host is not None:
            try:
                host.prune(unit, attempt)
            except Exception:  # pruning is housekeeping and must never fail a pass
                pass
        return accepted

    def _accept_checked(self, unit, attempt, raw, sidecar):
        """Cheap gates; a parity unit is held for comparison, any other is published into recorded/."""
        from index_options.ledger_studies import MIOPacketStudy

        digest = hashlib.sha256(raw).hexdigest()
        spec = self.arm_specs[f"{unit['quarter']}/{unit['arm']}"]
        current = self.queue.unit(unit["unit_id"])
        if current["status"] != "leased" or current["attempt"] != attempt:
            # A late result of an earlier attempt: evidence only, never counted.
            self._keep_aside("superseded", unit, attempt, raw)
            return False
        check = "sidecar_digest"
        try:
            parts = (sidecar or "").split()
            if not parts or parts[0] != digest:
                raise ValueError("sidecar digest mismatch")
            check = "action_file_schema"
            body = json.loads(raw.decode("utf-8"))
            check = "action_file_identity"
            if (not isinstance(body, dict) or body.get("format") != MIOPacketStudy.RECORDED_FORMAT
                    or body.get("method") != unit["method"] or body.get("attempt") != attempt
                    or body.get("bundle_manifest_sha256") != spec["bundle_sha256"]
                    or body.get("plan_sha256") != spec["plan_sha256"]
                    or not isinstance(body.get("host"), dict)
                    or not isinstance(body.get("seconds"), (int, float)) or isinstance(body.get("seconds"), bool)
                    or type(body.get("timeouts")) is not int
                    or not isinstance(body.get("actions"), dict)
                    or body["actions"].get("method") != unit["method"]
                    or not isinstance(body["actions"].get("weeks"), list) or not body["actions"]["weeks"]
                    or not isinstance(body["actions"].get("decisions"), dict)):
                raise ValueError("action file identity differs from the lease")
        except (ValueError, UnicodeDecodeError, RecursionError) as exc:  # RecursionError: a file nested past the parser's depth
            self._reject(unit, attempt, raw, exc if not isinstance(exc, RecursionError) else "file nested too deeply", check)
            return False
        if self.parity_state() == "pending" and unit["unit_id"] in (self.parity_unit, self.twin_unit):
            held = self._parity_file(unit, attempt)
            _sealed_dir(held.parent)
            atomic_write(held, raw)
            self._transition(unit["unit_id"], "returned", attempt, result_sha256=digest,
                             note=f"held for the parity comparison; seconds {body['seconds']} timeouts {body['timeouts']}")
            return True
        return self._publish(unit, attempt, raw, digest, body)

    def _publish(self, unit, attempt, raw, digest, body):
        """Move a verified action file into recorded/ atomically; an existing file or sidecar is never replaced.

        The sidecar is linked first and is the exclusive claim; the action file follows. A crash
        between the two leaves a sidecar without its file, which the document reads as absent.
        """
        target = _sealed_dir(self.recorded_dir(unit["quarter"], unit["arm"]))
        final, side = target / f"{unit['method']}.json", target / f"{unit['method']}.json.sha256"
        temp, temp_side = target / f".{unit['method']}.{attempt}.part", target / f".{unit['method']}.{attempt}.sha256.part"
        won = False
        if side.exists() and not final.exists():
            os.unlink(side)  # a crash left a claim without its file; this coordinator is the only writer
        if final.exists() and side.exists() and final.read_bytes() == raw:
            won = True  # this very file was published before a crash; finish the bookkeeping
        elif not final.exists() and not side.exists():
            atomic_write(temp, raw)
            atomic_write(temp_side, f"{digest}  {unit['method']}.json\n".encode())
            try:
                os.link(temp_side, side)
                try:
                    os.link(temp, final)
                    won = True
                except FileExistsError:
                    os.unlink(side)  # a file we did not write: leave no sidecar that vouches for it
            except FileExistsError:
                pass
            finally:
                for path in (temp, temp_side):
                    if path.exists():
                        os.unlink(path)
        if not won:
            self._keep_aside("superseded", unit, attempt, raw)
            self._transition(unit["unit_id"], "superseded", attempt, result_sha256=digest)
            return False
        self._transition(unit["unit_id"], "verified", attempt, result_sha256=digest,
                         note=f"seconds {body['seconds']} timeouts {body['timeouts']}")
        return True

    def _keep_aside(self, kind, unit, attempt, raw):
        name = unit["unit_id"].replace("/", "__").replace("@", "_")
        base = self.phases.holdout_path if self.phases and self.phases.holds(unit["quarter"]) else self.root
        _sealed_dir(base / kind)
        atomic_write(base / kind / f"{name}.{attempt}.json", raw)

    def _fits(self, unit, host, parity):
        """Whether the first-parity gate lets a pending unit run on a host now (the parity roles)."""
        if unit["role"] == "parity":
            return host.probe_limited  # the twin is the WSL half of the comparison
        if parity == "pending":
            if unit["unit_id"] == self.parity_unit:
                return not host.probe_limited
            return self._released and host.probe_limited  # released: the others may use WSL, never the gated Mac
        return True

    def _placeable(self, arm_rows):
        """Pending units of waiting arms whose registry chain still runs, in priority order."""
        out = []
        for unit in self._units(status="pending"):
            key = f"{unit['quarter']}/{unit['arm']}"
            if arm_rows[key]["status"] != "waiting":
                continue  # a running arm's document settles its units; a closed or failed arm needs none
            if unit["role"] == "parity" and (unit["unit_id"] != self.twin_unit or not self._comparison_possible()):
                continue  # a twin only exists for a comparison that can still happen
            if (self._chain_blocker(key, arm_rows) or "").startswith(self.CHAIN_STOPPED):
                continue
            out.append(unit)
        return sorted(out, key=self._priority)

    def place(self):
        """Lease pending units to online hosts with free slots, by priority."""
        arm_rows = {a["arm_key"]: a for a in self.queue.arms()}
        pending = self._placeable(arm_rows)
        for unit in list(pending):
            if self._exhausted(unit):
                self._transition(unit["unit_id"], "rejected", unit["attempt"],
                                 note="no usable host left (slots, parity or attempts): the document solves it locally")
                pending.remove(unit)
        if pending and self.probe().get("c_free_gb") is None:
            self.reasons["disk"] = "C: free unreadable: nothing is placed on any host until it reads again"
            return []
        placed, parity, why = [], self.parity_state(), {}
        ledger = self._ledger_state()
        held = True if ledger is None else ledger["open"]  # an unreadable ledger holds the Mac too (fail closed)
        if ledger is None:
            self.reasons["rejections"] = (f"the rejection ledger cannot be read ({self.ledger_error}): no new Mac unit is "
                                          "placed until it reads again")
        elif held and pending:
            self.reasons["rejections"] = (f"{held} worker result(s) rejected by the WSL checks: no new Mac unit is placed "
                                          "until the owner reviews them and runs clear-rejections")
        for host in self.hosts:
            candidates = [u for u in pending if host in self._eligible(u) and self._fits(u, host, parity)]
            if not candidates:
                continue
            if held and not host.probe_limited:
                why[host.name] = "stopped: open rejections (clear-rejections)"
                continue  # running work finishes; the fallback solve of a rejected method stays recorded
            if not self._is_online(host):
                why[host.name] = "offline"
                continue
            running = sum(1 for u in self.queue.units(status="leased") if u["host"] == host.name)
            cap = host.slots
            if parity == "pending":  # first-parity gate: one Mac unit, and the WSL twin even at wsl_slots 0
                cap = min(cap, 1) if not host.probe_limited else max(cap, 1)
            free = cap - running
            if host.probe_limited:
                admitted, reason = self._wsl_admit(self.limits["unit_ram_gb"], bool(self._due_arms(arm_rows)))
                if reason:
                    self.reasons["wsl"] = reason
                    why[host.name] = reason
                free = min(free, admitted)
            else:
                free = min(free, self._mac_slots(host))
                if free <= 0 < cap - running:
                    why[host.name] = "free RAM below the reserve plus one unit"
            if free <= 0 and host.name not in why:
                why[host.name] = "no free slot"
            cap_attempts = self.caps.get(host.name, self.DEFAULT_ATTEMPTS)
            for unit in candidates:
                if free <= 0:
                    break
                if self._attempts_on(unit, host.name) >= cap_attempts:
                    continue
                attempt = self.queue.lease(unit["unit_id"], host.name, now=self._stamp())
                self._launch(host, unit, attempt)
                pending.remove(unit)
                placed.append((unit["unit_id"], host.name, attempt))
                free -= 1
        if pending and why:
            self.reasons["place"] = "; ".join(f"{name}: {text}" for name, text in sorted(why.items()))
        return placed

    def _launch(self, host, unit, attempt):
        """Stage and start a worker: inline for a fast host, on a thread for one whose staging can take minutes."""
        if host.slow_stage:
            box, lock = {}, self._stage_locks.setdefault(host.name, threading.Lock())

            def work():
                try:
                    with lock:  # one bundle transfer at a time per host
                        host.stage(unit)
                        box["handle"] = host.spawn(unit, attempt)
                except Exception as exc:
                    box["error"] = type(exc).__name__

            thread = threading.Thread(target=work, daemon=True)
            self._spawning[unit["unit_id"]] = (thread, box, attempt, host)
            thread.start()
            return
        try:
            host.stage(unit)
            handle = host.spawn(unit, attempt)
        except Exception as exc:
            self._transition(unit["unit_id"], "pending", attempt, note=f"spawn failed: {type(exc).__name__}", refund=True)
            return
        self._adopt_handle(unit["unit_id"], handle)

    # -- documents ------------------------------------------------------------
    CHAIN_STOPPED = "registry chain stopped"
    LOG_CHUNK_BYTES = 1 << 20  # a document log is read this much at a time (one read of a whole log could be gigabytes)
    LOG_LINE_BYTES = 8 << 20  # an unfinished line longer than this is dropped: it is not an event

    @staticmethod
    def _failure_text(detail):
        """A short, value-free account of why a document failed."""
        entry = detail.get("entry") or {}
        text = str(detail.get("stage", "unknown"))
        if entry.get("refused"):
            text += f" refused ({entry['refused']})"
        rc = entry.get("rc")
        if isinstance(rc, int) and rc < 0:
            text += (f"; killed by signal {-rc} (disk_guard.sh sends TERM to dskit.pipeline run below 3 GB on C:); "
                     "the registry claim may still be active")
        elif isinstance(rc, int) and rc != 0:
            text += f"; exit {rc}"
        if detail.get("error"):
            text += f"; {detail['error']}"
        return text

    def _chain_blocker(self, key, rows):
        """Why the arm's document may not start yet (one active document per registry, in plan order), else None."""
        for earlier in self.chains[self.registry_of[key]]:
            if earlier == key:
                return None
            row = rows[earlier]
            if row["status"] == "closed":
                continue
            if row["status"] == "failed":
                return f"{self.CHAIN_STOPPED}: {earlier} failed ({self._failure_text(row['detail'])})"
            return f"waits for {earlier} (one document per registry, in plan order)"
        return None

    def _run_document(self, key, spec, quarter, arm, recorded, asof):
        try:
            outcome = self.documents(spec.get("spec"), quarter, arm, recorded, asof)
        except Exception as exc:  # a launcher bug must not strand the arm as running
            outcome = {"ok": False, "stage": "launcher", "error": type(exc).__name__}
        self._done[key] = outcome

    def _start_document(self, arm, adopt=False):
        key = f"{arm['quarter']}/{arm['arm']}"
        spec = self.arm_specs[key]
        has_units = bool(spec["methods"])
        recorded = self.recorded_dir(arm["quarter"], arm["arm"]) if has_units else None
        thread = threading.Thread(target=self._run_document, daemon=True,
                                  args=(key, spec, arm["quarter"], arm["arm"], recorded, spec.get("asof")))
        self._threads[key] = thread
        self._doc_started[key] = self.clock()
        thread.start()

    def _document_arms(self):
        """Adopt arms a previous coordinator left running, start due ones in registry order, harvest finished ones."""
        rows = self.queue.arms()
        self.orphans = {f"{a['arm_key']}": a["status"] for a in rows if a["arm_key"] not in self.arm_specs}
        if self.orphans:
            self.reasons["orphans"] = (f"{len(self.orphans)} arm(s) in queue.sqlite are not in the current plan "
                                       "and are ignored: " + ", ".join(sorted(self.orphans)))
        for arm in rows:
            key = f"{arm['quarter']}/{arm['arm']}"
            if arm["status"] == "running" and key in self.arm_specs and key not in self._threads:
                self._start_document(arm, adopt=True)  # the runner waits for or reuses the earlier launch
        for key in self.arm_order:
            arm_rows = {a["arm_key"]: a for a in self.queue.arms()}
            arm = arm_rows[key]
            if arm["status"] != "waiting":
                continue
            blocker = self._chain_blocker(key, arm_rows)
            if blocker:
                self.reasons[key] = blocker
                continue
            units = self._arm_units(arm)
            if not all(u["status"] in _TERMINAL for u in units):
                self.reasons[key] = f"{sum(1 for u in units if u['status'] not in _TERMINAL)} of {len(units)} units unsettled"
                continue
            admitted, reason = self._wsl_admit(self.limits["document_ram_gb"], False)
            if admitted < 1:
                self.reasons[key] = reason or "RAM"
                continue
            for host in self.hosts:  # no unit of this arm runs any more: free the staged bundle (a RAM disk)
                if units and not host.probe_limited:
                    host.drop_bundle(units[0])
            self.queue.set_arm(arm["quarter"], arm["arm"], "running")
            self._start_document(arm)

    def _arm_units(self, arm):
        """The units an arm's document waits for: its primaries, and the current twin while a comparison can happen."""
        return [u for u in self._units(quarter=arm["quarter"], arm=arm["arm"])
                if u["role"] == "primary" or (u["unit_id"] == self.twin_unit and self._comparison_possible())]

    def _primary_ended(self, primary):
        """Return whether the parity unit ended with no result the comparison could read."""
        return (primary["status"] in ("rejected", "superseded")
                or (primary["status"] in self.SOLVED_STATES and not self._parity_file(primary, primary["attempt"]).exists()))

    def _comparison_possible(self):
        """Return whether a parity verdict is open and its Mac unit can still give one."""
        return self.parity_state() == "pending" and not self._primary_ended(self.queue.unit(self.parity_unit))

    def _due_arms(self, arm_rows):
        """Waiting arms whose units are all settled and whose registry chain is clear: a document is due."""
        return [k for k in self.arm_order if arm_rows[k]["status"] == "waiting"
                and not self._chain_blocker(k, arm_rows)
                and all(u["status"] in _TERMINAL for u in self._arm_units(arm_rows[k]))]

    def documents_due(self):
        """Start due documents on threads, adopt running ones, and settle the finished; never blocks."""
        self._document_arms()
        finished = []
        for key in list(self._done):
            outcome = self._done.pop(key)
            self._threads.pop(key, None)
            self._doc_started.pop(key, None)
            quarter, arm = key.split("/")
            run_dir = outcome.get("run_dir") or (outcome.get("entry") or {}).get("run_dir")
            run = _run_key(run_dir) if run_dir else None
            for row in outcome.get("rejections") or []:  # before the arm is settled: a crash here repeats, never loses
                self.rejections.record(f"{key}/{_clean_rejection(row)['method'] or 'unknown'}", row, run=run,
                                       at=self._stamp())
            if outcome.get("ok"):
                self.queue.set_arm(quarter, arm, "closed", outcome.get("run_dir"), outcome)
                recorded = set(outcome.get("recorded_from", []))
                for u in self._units(quarter=quarter, arm=arm):
                    if u["role"] == "primary":
                        self._transition(u["unit_id"], "replayed" if u["method"] in recorded else "solved_local",
                                         u["attempt"])
            else:
                # The failed run stays as it is: its run directory, claim and exposure are retained for the owner.
                self.queue.set_arm(quarter, arm, "failed", (outcome.get("entry") or {}).get("run_dir"), outcome)
            finished.append((quarter, arm, outcome.get("ok")))
        return finished

    def join_documents(self, timeout=None):
        """Wait for every running document thread (tests and shutdown), then settle them."""
        for thread in list(self._threads.values()):
            thread.join(timeout)
        return self.documents_due()

    def _more(self):
        """False when every planned arm ended, or when nothing can move because every open arm's registry chain stopped."""
        arm_rows = {a["arm_key"]: a for a in self.queue.arms()}
        open_arms = [k for k in self.arm_order if arm_rows[k]["status"] not in ("closed", "failed")]
        if not open_arms:
            return False
        moving = self._threads or self._spawning or self._units(status="leased")
        stopped = all(arm_rows[k]["status"] == "waiting"
                      and (self._chain_blocker(k, arm_rows) or "").startswith(self.CHAIN_STOPPED) for k in open_arms)
        return bool(moving) or not stopped

    def tick(self):
        """One pass: reap, stop on low C:, place, launch due documents, write status; False when nothing more can run."""
        self._online, self.reasons = {}, {}
        try:
            self.stop_for_disk()
            self.reap()
            self.check_parity()
            self._parity_watch()
            self.watch_documents()
            self.place()
            self.documents_due()
        except OSError as exc:  # say why in status.json before the loop prints it and retries or gives up
            self.reasons["pass_failed"] = f"{type(exc).__name__}: {str(exc)[:160]}"
            try:
                self.write_status()
            except Exception:
                pass
            raise
        self.write_status()
        return self._more()

    def _ledger_state(self):
        """The ledger state, or None (with ``ledger_error`` set) when the file cannot be read: unreadable is never empty."""
        try:
            state = self.rejections.state()
        except OSError as exc:
            self.ledger_error = type(exc).__name__
            return None
        self.ledger_error = None
        return state

    def watch_documents(self):
        """Record the ``recorded_rejected`` lines of every running document's log as they appear.

        A rejection reaches the ledger (and pauses the Mac, see ``place``) within one pass of the document logging it,
        not when the whole document returns. ``documents_due`` records the document's own list again at the end; the
        ledger drops the repeat (same unit, week, check, ratio and run directory). Reads only the new complete lines of
        each log; nothing is printed here and nothing of a sealed quarter is shown (see ``_rejections_view``).
        """
        if self.live_runs is None:
            return
        for key in list(self._threads):
            quarter, arm = key.split("/")
            for run_dir in self.live_runs(quarter, arm):
                for row in self._new_rejections(Path(run_dir) / "run.log"):
                    self.rejections.record(f"{key}/{row['method'] or 'unknown'}", row, run=_run_key(run_dir), at=self._stamp())

    def _new_rejections(self, log):
        """Sanitized rejections in the complete lines appended to ``log`` since the last call."""
        rows = []
        try:
            with open(log, "rb") as handle:
                stat = os.fstat(handle.fileno())
                inode, position = self._log_pos.get(log, (None, 0))
                if inode != stat.st_ino or stat.st_size < position:
                    position = 0  # replaced (another inode) or truncated: read it from its start
                handle.seek(position)
                tail = b""  # an unfinished last line waits for the next call
                while True:
                    chunk = handle.read(self.LOG_CHUNK_BYTES)
                    if not chunk:
                        break
                    data = tail + chunk
                    end = data.rfind(b"\n")
                    if end < 0:
                        tail = data if len(data) <= self.LOG_LINE_BYTES else b""  # a line this long is not an event
                        if not tail:
                            position += len(data)
                        continue
                    position += end + 1
                    tail = data[end + 1:]
                    rows += [_event_rejection(line) for line in data[:end].decode("utf-8", errors="replace").splitlines()]
        except OSError:
            return []  # not there yet, or unreadable: the document's own list still arrives at the end
        self._log_pos[log] = (stat.st_ino, position)
        return [row for row in rows if row is not None]

    def write_status(self):
        """Write ``status.json``: unit and arm states, attempts, hosts, hashes and reasons; never economics."""
        sealed = self._sealed_quarters()
        units = [{k: u[k] for k in ("unit_id", "status", "attempt", "host", "result_sha256", "started", "last_progress")}
                 for u in self._units() if u["quarter"] not in sealed]
        counts = {}  # a sealed arm shows how many units it has and how many settled, never which state each is in
        for u in self._units():
            if u["quarter"] in sealed and u["role"] == "primary":
                row = counts.setdefault(f"{u['quarter']}/{u['arm']}", {"units": 0, "settled": 0})
                row["units"] += 1
                row["settled"] += u["status"] in _TERMINAL or u["status"] in ("replayed", "solved_local")
        arms = []
        # A sealed arm's reason is a bare word: a failure text (stage, refusal, exit, error) or "N of M units unsettled"
        # would date the quarter's progress, and a reason that names a sealed arm (a chain wait) is cut the same way.
        sealed_arms = [k for k in self.arm_specs if k.split("/")[0] in sealed]
        waiting = {k: ("sealed" if k.split("/")[0] in sealed and k in self.arm_specs
                       or any(arm in str(v) for arm in sealed_arms) else v) for k, v in self.reasons.items()}
        for a in self.queue.arms():
            if a["arm_key"] not in self.arm_specs:
                continue
            if a["quarter"] in sealed:  # a state word beyond failed would date the quarter's progress
                failed = a["status"] == "failed"
                arms.append({"arm_key": a["arm_key"], "status": "failed" if failed else "sealed", "run_dir": None,
                             "why": "failed: the owner reads the sealed run log" if failed else None,
                             "registry": self.registry_of[a["arm_key"]]})
                continue
            why = (self._failure_text(a["detail"]) if a["status"] == "failed"
                   else waiting.get(a["arm_key"]) if a["status"] == "waiting" else None)
            arms.append({"arm_key": a["arm_key"], "status": a["status"], "run_dir": a["run_dir"], "why": why,
                         "registry": self.registry_of[a["arm_key"]]})
        status = {"updated_at": _utc(), "units": units, "arms": arms,
                  "hosts": {h.name: {"online": self._is_online(h), "slots": h.slots} for h in self.hosts},
                  "moving": bool(self._threads or self._spawning or self._units(status="leased")),
                  "sealed_counts": dict(sorted(counts.items())),
                  "waiting": dict(sorted(waiting.items())), "parity": self.parity_state(),
                  "parity_reason": self.parity_reason() if self.parity_state() == "failed" else None,
                  "skipped": dict(self.skipped), "orphans": dict(sorted(self.orphans.items())),
                  "no_worker_units": dict(sorted(self.no_units.items())),
                  "rejections": self._rejections_view(),
                  "fallbacks": self.fallbacks(),
                  "sealed": None if not self.phases else {
                      "quarters": sorted(self.phases.holdout_quarters), "selection": self.phases.picked(),
                      "unsealed": self.phases.unsealed()}}
        atomic_write(self.root / "status.json", json.dumps(status, indent=1, sort_keys=True).encode())
        return status

    def _sealed_quarters(self):
        """The holdout quarters while they are sealed (no phases, or after ``unseal``: none)."""
        return set(self.phases.holdout_quarters) if self.phases and not self.phases.unsealed() else set()

    def _rejections_view(self):
        """The ledger state as a reader may see it: a sealed quarter's rows show unit, check and count only.

        A ratio of ``model_over_audit`` or ``incumbent_negative`` is a miss over a tolerance a reader can turn back
        into a dollar value, and a week, run, time or category dates the quarter's progress, so a row whose unit is
        not in a selection quarter (a holdout unit, or one that names no known quarter) shows its unit, check and
        ``seq`` only, and counts under ``sealed:<check>``. Without phases, or after ``unseal``, nothing is cut.
        An unreadable ledger shows its cause (``unreadable``) and ``open`` None.
        """
        state = self._ledger_state()
        if state is None:
            return {"open": None, "cleared": None, "reasons": {}, "records": [], "unreadable": self.ledger_error}
        if not self.phases or self.phases.unsealed():
            return state
        visible, records, reasons = set(self.phases.selection_quarters), [], {}
        for row in state["records"]:
            if str(row["unit"]).split("/")[0] in visible:
                records.append(row)
                name = f"{row['category']}:{row['check']}"
            else:
                records.append({"unit": row["unit"], "check": row["check"], "seq": row["seq"]})
                name = f"sealed:{row['check']}"
            reasons[name] = reasons.get(name, 0) + 1
        return {**state, "records": records, "reasons": dict(sorted(reasons.items()))}

    def rejection_line(self):
        """One line for the coordinator's output when the rejection count changed since it was last shown, else None.

        Counts, ``category:check`` reasons and the largest ratio of the open rejections a reader may see (a sealed
        quarter's ratio is never shown); the units and weeks are in ``status.json``. The ratio is what tells a small
        tolerance miss (a few times 1) from a garbage file (a huge one, category ``numeric``).
        """
        state = self._rejections_view()
        shown = (state["open"], state["cleared"])
        if shown == self._rejections_shown:
            return None
        self._rejections_shown = shown
        if state.get("unreadable"):
            return (f"{_utc()} rejections: the ledger cannot be read ({state['unreadable']}); "
                    "no new Mac unit is placed until it reads again")
        reasons = ", ".join(f"{name} x{count}" for name, count in state["reasons"].items()) or "none open"
        ratios = [r["ratio"] for r in state["records"] if r.get("ratio") is not None]
        largest = f", largest ratio {max(ratios):.3g}" if ratios else ""
        stop = "; no new Mac unit is placed until clear-rejections" if state["open"] else ""
        return f"{_utc()} rejections: open {state['open']}, cleared {state['cleared']} ({reasons}{largest}){stop}"

    def fallbacks(self):
        """Units that ended without a usable worker result and will be solved by the document on WSL, with why.

        ``{unit_id: "attempt 1 on mac: stale lease killed; attempt 2 on mac: ..."}``: the notes of each ended
        attempt (no figure in any of them). Visible in ``status.json`` and the console line; it pauses nothing.
        """
        out, sealed = {}, self._sealed_quarters()
        for unit in self._units():
            if unit["role"] != "primary" or not any(h.get("event") == "rejected" for h in unit["history"]):
                continue  # the document may already have solved it (status solved_local): the history still says why
            if unit["quarter"] in sealed:
                out[unit["unit_id"]] = "sealed"  # the unit only: its attempts and notes would date the quarter
                continue
            notes = [f"attempt {h.get('attempt')} on {h.get('host')}: {h.get('note')}" for h in unit["history"]
                     if h.get("note") and h.get("event") in ("pending", "rejected")]
            out[unit["unit_id"]] = "; ".join(notes) or "no worker result"
        return dict(sorted(out.items()))

    def fallback_line(self):
        """One line when the set of units falling back to a WSL solve changed since last shown, else None."""
        found = self.fallbacks()
        if frozenset(found) == self._fallbacks_shown:
            return None
        self._fallbacks_shown = frozenset(found)
        if not found:
            return None
        text = " | ".join(f"{unit}: {why}" for unit, why in found.items())
        return f"{_utc()} fallback: {len(found)} unit(s) solved by the document on WSL, no pause ({text})"


def _registry_of(run_config):
    """The exposure registry a run document's MIOPacketStudy names, as a real path (symlinks resolved)."""
    base = json.loads(Path(run_config).read_text())
    node = next(v for v in base["pipeline"].values() if str(v.get("uses", "")).endswith(":MIOPacketStudy"))
    return os.path.realpath(str(node["params"]["study"]["registry"]["path"]))


def _quarter_allowed(quarter):
    """Refuse a quarter after 2023Q4 (owner ruling 2026-11-05: MIO only through 2023). ``2023q4`` style ids."""
    match = re.fullmatch(r"(\d{4})[qQ]([1-4])", quarter)
    if match is None:
        return True  # not a calendar quarter id; the packet's own end date is checked as well
    return (int(match.group(1)), int(match.group(2))) <= (2023, 4)


def _load_plan(config, python=None):
    """Build the coordinator plan from READY markers; stage one bundle per arm.

    A quarter that cannot be planned (after 2023Q4, the contract refuses it, a pinned byte differs, the
    ticker list is not the locked one, the marker is malformed) is skipped with its reason, never a
    coordinator crash, and so is every later quarter that names a registry of a skipped one (a registry
    cannot open a quarter before its predecessor; quarters with their own registries are independent).
    Each arm carries its exposure registry's real path, which orders its documents. An arm gets no worker
    units, and runs as a plain WSL document, when its forecasts are a blinded cache (ADR-0293) or when a
    payload file holds a date on or after 2024-01-01 (``PayloadCapError``: pin cut files to give it
    units); ``no_units`` says which. An existing bundle is never trusted: ``build`` re-verifies every
    file against the study and refuses a changed one.
    """
    from index_options.mio_contract import OWNER_TAIL_CAP, MIOStudyContract
    from index_options.mio_worker import MIOMethodBundle

    root, python = Path(config["queue_root"]), python or config["python"]
    scratch = Path(config.get("scratch_root") or root)
    phases = Phases.from_config(config)
    quarters, skipped, broken = [], {}, set()
    for quarter in config["quarters"]:
        if not _quarter_allowed(quarter):
            skipped[quarter] = "refused: owner ruling, MIO only through 2023Q4 (nothing dated 2024 or later)"
            continue
        marker = root / f"READY-{quarter}.json"
        if not marker.exists():
            break
        if phases and phases.holds(quarter) and phases.picked() is None:
            raise ValueError(f"{quarter} is a sealed holdout quarter and the selection is not recorded: "
                             "run record-selection after reading the selection quarters (a refused start)")
        try:
            m = json.loads(marker.read_text())
            if phases:  # each phase runs exactly its formulations, whatever the documents say
                found = []
                for spec in m["runs"]:
                    document = json.loads(Path(spec["run"]).read_text())
                    node = next(v for v in document["pipeline"].values() if str(v.get("uses", "")).endswith(":MIOPacketStudy"))
                    found.extend(node["params"]["study"]["domain"]["params"]["methods"])
                phases.check_methods(quarter, found)
            arms = []
            for spec in m["runs"]:
                registry = _registry_of(spec["run"])
                if registry in broken:
                    raise ValueError("its registry belongs to an earlier skipped quarter: it cannot open before it")
                for name in ("initialize", "run"):
                    if not ticker_lock_ok(python, config["runtime"], spec[name]):
                        raise ValueError(f"{name} document is not on the locked ticker list")
                base = json.loads(Path(spec["run"]).read_text())
                node = next(v for v in base["pipeline"].values() if str(v.get("uses", "")).endswith(":MIOPacketStudy"))
                study = node["params"]["study"]
                if MIOStudyContract.packet(study)["end_before"] > OWNER_TAIL_CAP:
                    raise ValueError(f"the selected packet ends after {OWNER_TAIL_CAP}: MIO only through 2023Q4")
                arm = {"id": spec["name"], "methods": [], "bundle_sha256": None, "plan_sha256": None,
                       "spec": spec, "bundle_root": None, "registry": registry}
                predictive = study["domain"].get("predictive")
                if isinstance(predictive, dict) and predictive.get("policy") == BLINDED_POLICY:
                    arm["no_units"] = "blinded-cache forecasts (ADR-0293): the document solves every method on WSL"
                    arms.append(arm)
                    continue
                bundle_root = scratch / "bundles" / f"{quarter}-{spec['name']}"
                try:
                    manifest = MIOMethodBundle(study, bundle_root).build()
                except PayloadCapError as exc:
                    arm["no_units"] = f"{exc}: pin cut source files to give this arm worker units"
                    arms.append(arm)
                    continue
                plan = json.loads((bundle_root / MIOMethodBundle.PLAN_FILE).read_text())
                arm.update(methods=[x["id"] for x in study["domain"]["params"]["methods"]],
                           bundle_sha256=manifest["sha256"], plan_sha256=plan["sha256"], bundle_root=str(bundle_root))
                arms.append(arm)
            quarters.append({"id": quarter, "asof": m["asof"], "arms": arms})
        except (ValueError, KeyError, OSError, StopIteration, TypeError, AttributeError, RuntimeError) as exc:
            text = str(exc)[:200]
            if phases and phases.holds(quarter) and not phases.unsealed():  # a sealed quarter: no value in the message
                from index_options.ledger_studies import MIOPacketStudy
                text = MIOPacketStudy.scrub(text)
            skipped[quarter] = f"{type(exc).__name__}: {text}"
            try:  # best effort: a later quarter on one of these registries cannot open before this one
                broken.update(_registry_of(spec["run"]) for spec in json.loads(marker.read_text())["runs"])
            except (ValueError, KeyError, OSError, TypeError, AttributeError, StopIteration):
                pass
    return {"quarters": quarters, "skipped": skipped}


def _build(config):
    """Wire the real hosts, probe and document runner from the JSON config; refuse an unsafe one (ValueError)."""
    for key, pinned in (("c_mount", C_MOUNT), ("compute_scripts", SKILL_SCRIPTS)):
        # Pinned, not configurable: a config that names another C: mount or another skill directory could fake the
        # disk stop, the Windows RAM figure and the Mac certificate. Absent or exactly the pin is accepted.
        if config.get(key, pinned) != pinned:
            raise ValueError(f"{key} is pinned to {pinned} and cannot be configured")
    root = _sealed_dir(config["queue_root"])  # action files, run roots and logs live here: real 0700 or refuse
    scratch = Path(config.get("scratch_root") or root)  # bundles, work dirs and the index cache only
    scripts = SKILL_SCRIPTS
    mac = config.get("mac")
    if mac and mac.get("certificate_job_type", CERT_JOB_TYPE) != CERT_JOB_TYPE:
        raise ValueError(f"mac.certificate_job_type must be {CERT_JOB_TYPE} (a Mac certificate for this job type is required)")
    plan = _load_plan(config)
    phases = Phases.from_config(config)
    bundles = {f"{q['id']}/{a['id']}": a["bundle_root"] for q in plan["quarters"] for a in q["arms"]}
    hosts = []
    if mac:
        remote = mac["root"].rstrip("/")
        hosts.append(SshHost("mac", mac.get("slots", 1), mac.get("alias", "mac"), remote, mac["python"],
                             lambda unit: f"{remote}/bundles/{unit['quarter']}-{unit['arm']}", mac["worker_script"],
                             mac["sentinel"], certificate=skill_certificate(CERT_JOB_TYPE, scripts, mac.get("alias", "mac")),
                             local_bundle_for=lambda unit: bundles.get(f"{unit['quarter']}/{unit['arm']}") or
                             str(scratch / "bundles" / f"{unit['quarter']}-{unit['arm']}")))
    hosts.append(LocalHost("wsl", config.get("wsl_slots", 0), config["python"],
                           lambda unit: bundles[f"{unit['quarter']}/{unit['arm']}"],
                           scratch / "work", root / "out",
                           Path(config["reuse_root"]) if config.get("reuse_root") else None,
                           sealed_out_root=(_sealed_dir(phases.holdout_path) / "out") if phases else None,
                           sealed_quarter=phases.holds if phases else None))
    runner = DocumentRunner(config["python"], config["runtime"], root / "generated", root / "sealed",
                            retired=root / "retired_runs.json")
    documents = runner.run_arm
    live_runner = lambda quarter: runner  # noqa: E731  (which runner's run roots hold this quarter's document logs)
    if phases:  # a holdout quarter's documents run from, and write to, the sealed holdout path
        sealed_runner = DocumentRunner(config["python"], config["runtime"], root / "generated",
                                       _sealed_dir(phases.holdout_path) / "sealed", retired=root / "retired_runs.json")
        documents = lambda spec, quarter, arm, recorded, asof: (
            sealed_runner if phases.holds(quarter) else runner).run_arm(spec, quarter, arm, recorded, asof)
        live_runner = lambda quarter: sealed_runner if phases.holds(quarter) else runner  # noqa: E731
    probe = ResourceProbe(C_MOUNT, windows_reader(scripts))
    state = _skill_module(scripts, "compute_state")
    limits = dict(config.get("limits") or {})
    # One owner of the reserves: the compute skill's own constants are a FLOOR; a config can raise them, never lower them.
    floors = {"wsl_reserve_gb": getattr(state, "WSL_RESERVE", 6 * 2**30) / 2**30 if state else 6.,
              "windows_reserve_gb": getattr(state, "WIN_RESERVE", 2 * 2**30) / 2**30 if state else 2.}
    for name, floor in floors.items():
        limits[name] = max(_limit_number(name, limits.get(name, floor)), floor)
    coordinator = Coordinator(root, plan, hosts, documents, probe, limits, parity=config.get("parity"), phases=phases,
                              live_runs=lambda quarter, arm: live_runner(quarter).run_dirs(f"run-{quarter}-{arm}"))
    coordinator.skipped = plan["skipped"]
    coordinator.no_units = {f"{q['id']}/{a['id']}": a["no_units"] for q in plan["quarters"] for a in q["arms"] if a.get("no_units")}
    return coordinator


def check_study(study, weeks):
    """A copy of ``study`` whose selected packet ends after at most ``weeks`` (1 or 2) weeks: the Mac certificate's short check.

    Raises
    ------
    ValueError
        ``weeks`` is not 1 or 2.
    """
    import copy
    from datetime import date, timedelta
    from index_options.mio_contract import MIOStudyContract

    if type(weeks) is not int or weeks not in (1, 2):
        raise ValueError("a check bundle covers 1 or 2 weeks")
    study = copy.deepcopy(study)
    packet = MIOStudyContract.packet(study)
    packet["end_before"] = min(packet["end_before"], (date.fromisoformat(packet["start"]) + timedelta(days=7 * weeks)).isoformat())
    bridge = study.get("domain", {}).get("params", {}).get("bridge")
    if bridge is not None:  # the contract binds the bridge's entry clock to the selected packet
        bridge["end_before"] = packet["end_before"]
    return study


def check_bundle(config, arm_key, weeks=2):
    """Build the short bundle the Mac certificate runs on, beside the real ones; return ``(manifest digest, bundle root)``.

    The arm's own run document, its selected packet cut to ``weeks`` weeks (``check_study``), bundled by the same
    ``MIOMethodBundle`` (so the same cut, pins and manifest). It is never a plan unit, never read by a document
    and never stored in the queue. Selection quarters only: a sealed holdout quarter has no check bundle.

    Raises
    ------
    ValueError
        The quarter is after 2023Q4 or a sealed holdout quarter, its READY marker or arm is absent, or the arm
        has no MIOPacketStudy node.
    """
    from index_options.mio_worker import MIOMethodBundle

    quarter, _, arm = arm_key.partition("/")
    if not _quarter_allowed(quarter):
        raise ValueError("refused: owner ruling, MIO only through 2023Q4")
    phases = Phases.from_config(config)
    if phases and phases.holds(quarter):
        raise ValueError(f"{quarter} is a sealed holdout quarter: no check bundle is built for it")
    root = Path(config["queue_root"])
    try:
        marker = json.loads((root / f"READY-{quarter}.json").read_text())
        spec = next(r for r in marker["runs"] if r["name"] == arm)
    except (OSError, ValueError, KeyError, StopIteration):
        raise ValueError(f"{arm_key} is not in the READY markers of {root}") from None
    document = json.loads(Path(spec["run"]).read_text())
    node = next(v for v in document["pipeline"].values() if str(v.get("uses", "")).endswith(":MIOPacketStudy"))
    bundle_root = Path(config.get("scratch_root") or root) / "bundles" / f"check-{quarter}-{arm}"
    return MIOMethodBundle(check_study(node["params"]["study"], weeks), bundle_root).build()["sha256"], bundle_root


def stage_bundle(coordinator, arm_key):
    """Stream one quarter-arm's bundle to the Mac RAM disk before the Mac has a certificate; return its digest.

    A fresh start is circular without this: the Mac is online only once the ``mio-method-unit`` certificate is
    valid, the certificate command needs the bundle already on the Mac, and only a placed unit stages it. This
    calls the host's own ``stage``, so it keeps the mount-identity gate (the root must be the sentinel-marked RAM
    disk), the whole-or-nothing rename and the manifest comparison; it needs no certificate and places nothing.

    Raises
    ------
    ValueError
        No Mac host, or the arm is not in the plan.
    RuntimeError
        The host refuses or the transfer fails.
    """
    host = next((h for h in coordinator.hosts if not h.probe_limited), None)
    if host is None:
        raise ValueError("no Mac host is configured: there is nothing to stage")
    if arm_key.startswith("check:"):  # the Mac certificate's short bundle, built by ``check_bundle`` beside the real ones
        quarter, _, arm = arm_key[len("check:"):].partition("/")
        return host.stage({"unit_id": f"check-{quarter}/{arm}", "quarter": f"check-{quarter}", "arm": arm})
    spec = coordinator.arm_specs.get(arm_key)
    if spec is None:
        raise ValueError(f"{arm_key} is not in the plan (is its READY marker written, and is it allowed?)")
    return host.stage({"quarter": spec["quarter"], "arm": spec["id"]})


def main(argv=None):
    """``run config.json [--once]`` (the coordinator loop), ``check-bundle config.json QUARTER/ARM [--weeks 1|2]``, ``stage config.json QUARTER/ARM`` (or ``check:QUARTER/ARM``), ``reopen config.json QUARTER/ARM``, ``recorded-from RUN_DIR`` or ``clear-rejections config.json``."""
    os.umask(0o077)  # every file the coordinator, its workers and its documents create is owner-only
    parser = argparse.ArgumentParser(description="parallel MIO method-unit coordinator")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="run the coordinator (see docs/plans/mio-parallel/RUNBOOK.md)")
    run.add_argument("config")
    run.add_argument("--once", action="store_true", help="one scheduling pass, then exit")
    run.add_argument("--poll", type=int, default=60, help="seconds between passes")
    reopen = sub.add_parser("reopen", help="let a FAILED arm's document run again, after its registry claim was disposed of by hand")
    reopen.add_argument("config")
    reopen.add_argument("arm", help="QUARTER/ARM")
    check = sub.add_parser("check-bundle", help="build the Mac certificate's short bundle (1-2 weeks of an arm's packet); no Mac contact")
    check.add_argument("config")
    check.add_argument("arm", help="QUARTER/ARM")
    check.add_argument("--weeks", type=int, default=2, choices=(1, 2))
    stage = sub.add_parser("stage", help="stream one quarter-arm's bundle to the Mac RAM disk BEFORE its first certificate")
    stage.add_argument("config")
    stage.add_argument("arm", help="QUARTER/ARM")
    report = sub.add_parser("recorded-from", help="which methods a worker supplied: ids, hosts, hashes, counts")
    report.add_argument("run_dir")
    clear = sub.add_parser("clear-rejections", help="the owner has reviewed the open rejections: let the Mac take new units again")
    clear.add_argument("config")
    clear.add_argument("--through", type=int, default=None,
                       help="close the rejections up to this seq (the highest seq you read in status.json); a row after it, "
                            "or recorded after this command read the ledger, stays open. Must be positive; omitted: all open now")
    pick = sub.add_parser("record-selection", help="the owner fixes the picked formulations once the selection quarters closed")
    pick.add_argument("config")
    pick.add_argument("formulations", nargs="+")
    unseal = sub.add_parser("unseal", help="the owner opens the sealed holdout quarters to status.json")
    unseal.add_argument("config")
    selection = sub.add_parser("report-selection", help="OWNER ONLY: per-formulation results of the selection quarters (none exists for the holdout)")
    selection.add_argument("config")
    args = parser.parse_args(argv)
    if args.command == "recorded-from":
        print(json.dumps(recorded_from_view(recorded_from_report(args.run_dir)), indent=1, sort_keys=True))
        return 0
    config = json.loads(Path(args.config).read_text())
    root = Path(config["queue_root"])
    if args.command == "check-bundle":  # no coordinator lock: it writes one new directory under scratch/bundles
        try:
            digest, path = check_bundle(config, args.arm, args.weeks)
        except (ValueError, PayloadCapError, OSError, KeyError) as exc:
            print(f"check-bundle refused: {exc}", file=sys.stderr)
            return 1
        print(f"check bundle {path}, manifest sha256 {digest}")
        return 0
    if args.command == "clear-rejections":  # no coordinator lock: it appends one line, the coordinator reads each pass
        try:
            count = RejectionLedger(root / "rejections.jsonl").clear(_utc(), args.through)
        except ValueError as exc:
            print(f"clear-rejections refused: {exc}", file=sys.stderr)
            return 1
        except OSError as exc:  # an unreadable ledger is never read as an empty one
            print(f"clear-rejections refused: the ledger cannot be read ({type(exc).__name__})", file=sys.stderr)
            return 1
        if not count:
            print("nothing to clear: no open rejection", file=sys.stderr)
            return 1
        print(f"cleared {count} rejection(s); the next pass places Mac units again")
        return 0
    if args.command in ("record-selection", "unseal", "report-selection"):  # owner commands: no coordinator lock
        try:
            phases = Phases.from_config(config)
            if phases is None:
                raise ValueError("the config names no selection phases (selection_quarters, holdout_quarters, ...)")
            arms = UnitQueue(root / "queue.sqlite").arms()
            if args.command == "record-selection":
                phases.record(args.formulations, _utc(), phases.selection_closed(arms))
                print("selection recorded: " + ", ".join(args.formulations))
            elif args.command == "unseal":
                phases.unseal(_utc())
                print("holdout quarters unsealed in status.json")
            else:
                print(json.dumps({"note": "economics of the selection quarters only, in formulation id order; "
                                          "the owner chooses", "rows": phases.report(arms)}, indent=1))
        except (ValueError, KeyError, OSError) as exc:
            print(f"{args.command} refused: {exc}", file=sys.stderr)
            return 1
        return 0
    try:
        _sealed_dir(root)
    except RuntimeError as exc:
        print("coordinator refused to start: "+str(exc), file=sys.stderr)
        return 2
    lock = PidLock(root / "coordinator.lock")
    if not lock.acquire():
        print("another coordinator already holds "+str(root / "coordinator.lock"), file=sys.stderr)
        return 4
    try:
        if args.command == "reopen":
            quarter, _, arm = args.arm.partition("/")
            queue = UnitQueue(root / "queue.sqlite")
            row = queue.arm(quarter, arm)
            if row["status"] != "failed":
                print(f"{args.arm} is not failed: nothing reopened", file=sys.stderr)
                return 1
            if row["detail"].get("stage") == "incomplete_with_worker_files" and row["run_dir"]:
                # That run finished but incomplete; its claim is disposed of, so it must not be adopted again.
                DocumentRunner.retire(root / "retired_runs.json", row["run_dir"])
            queue.set_arm(quarter, arm, "waiting")
            return 0
        try:
            coordinator = _build(config)
        except (ValueError, RuntimeError) as exc:
            print("coordinator refused to start: "+str(exc), file=sys.stderr)
            return 2
        if args.command == "stage":
            try:
                print("staged, bundle manifest sha256 "+str(stage_bundle(coordinator, args.arm)))
            except (ValueError, RuntimeError) as exc:
                print("not staged: "+str(exc), file=sys.stderr)
                return 1
            return 0
        failures = 0
        while True:
            try:
                more = coordinator.tick()
                failures = 0
                for line in (coordinator.rejection_line(), coordinator.fallback_line()):
                    if line:
                        print(line, flush=True)
            except (OSError, sqlite3.Error) as exc:  # a full disk or a locked database: say so and try again
                if args.once:
                    raise
                failures += 1
                print(f"{_utc()} pass failed ({type(exc).__name__}: {exc}); {failures} in a row", file=sys.stderr, flush=True)
                if failures >= 10:
                    print("giving up after 10 failed passes in a row", file=sys.stderr)
                    return 5
                more = True
            if args.once or not more or (root / "STOP").exists():
                break
            time.sleep(args.poll)
        coordinator.join_spawns()  # a worker being started on a thread must have its handle recorded
        failed = [a["arm_key"] for a in coordinator.queue.arms() if a["status"] == "failed"]
        if failed and not more:
            print("stopped with failed arms (see status.json 'arms' and the RUNBOOK): "+", ".join(failed), file=sys.stderr)
            return 3
    finally:
        lock.release()
    return 0


if __name__ == "__main__":
    sys.exit(main())
