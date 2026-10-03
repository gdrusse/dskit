"""Run a lane-keyed workflow one lane at a time, in parallel (``workflow-batch``).

A lane manifest (``lanes.key``) already runs every lane in one process and one
ledger. That is serial, and a lane that crashes the process takes the rest with
it. This module runs ONE ``workflow`` subprocess per lane (``--only @lane``),
each with its own ``work_dir`` derived from a pattern, so every lane has its own
``workflow.json`` and parallel lanes never race on a ledger. At most
``parallel`` lanes run at once; a failed lane never stops the others; a failed
lane is retried up to ``retry`` times (the runner resumes from its ledger); a
lane is never retried after a deterministic exit (3 halted, 5 refused). Re-running
the command resumes: every selected lane is relaunched and the runner's own
unchanged-step rule skips the steps that are finished, so a changed arg or hash
reruns exactly what it should. A lane is ``ok`` only when its process exited 0
AND every step of the manifest has exit 0 in its ledger. ``batch.json`` (written
atomically after every lane event, merged over the previous file so lanes not
run this time keep their earlier record) holds per-lane status, exit code,
attempts, seconds, ``work_dir``, the halting step and the peak resident memory.

The memory guard polls ``/proc`` (Linux) once per poll for every lane. It sums
the resident set of the lane's whole process tree, so memory shared between
processes (mapped libraries, forked pages) is counted more than once: the figure
is an upper bound. GPU memory is NOT guarded. A lane over ``mem_limit_mb`` is
killed and reported ``killed`` (never retried). Without ``/proc`` the peak is
null and nothing is killed. Every lane runs in its own session; its whole process
group is killed when the lane ends and when the batch is interrupted. The
environment is inherited unchanged. Nothing project-specific lives here: the
manifest, the argument names and the work-dir pattern all come from the caller.
Stdlib only.

Exit codes: 0 every selected lane ok, 1 otherwise.
"""

from __future__ import annotations

import json
import os
import signal
import string
import subprocess
import sys
import time

from dskit.pipeline.node import atomic_write
from dskit.pipeline.records import number_ok
from dskit.pipeline.workflow import (
    EXIT_ERROR, EXIT_OK, LANE_SEP, LEDGER_NAME, Workflow, WorkflowError, load_json, load_overlays,
)

__all__ = [
    "BatchError", "ProcessTree", "WorkflowBatch", "land_bytes", "add_arguments",
    "run_cli", "main", "BATCH_NAME", "STATUS_OK", "STATUS_FAILED", "STATUS_KILLED",
    "STATUS_PENDING", "EXIT_HALTED", "EXIT_REFUSED",
]

ENCODING = "utf-8"
BATCH_NAME = "batch.json"
LANE_ARGS_DIR = "lane_args"
LOGS_DIR = "logs"
DEFAULT_PARALLEL = 1
DEFAULT_RETRY = 0
DEFAULT_POLL_SECONDS = 1.0
DEFAULT_WORK_DIR_ARG = "work_dir"
DEFAULT_WORK_DIR_PATTERN = "{root}/{lane}"
DEFAULT_ARGV_PREFIX = (sys.executable, "-m", "dskit.pipeline", "workflow")
PATTERN_FIELDS = ("root", "lane")
BYTES_PER_MB = 1024 * 1024
PROC_ROOT = "/proc"
EXIT_HALTED = 3
EXIT_REFUSED = 5
DETERMINISTIC_EXITS = (EXIT_HALTED, EXIT_REFUSED)
STATUS_OK = "ok"
STATUS_FAILED = "failed"
STATUS_KILLED = "killed"
STATUS_PENDING = "pending"
STOP_SIGNALS = (signal.SIGTERM, signal.SIGHUP)
SPEC_KEYS = {
    "manifest", "root", "args", "work_dir_arg", "work_dir_pattern", "parallel",
    "retry", "lanes_per_batch", "only_lanes", "mem_limit_mb", "poll_seconds",
    "argv_prefix",
}


class BatchError(ValueError):
    """A refused batch: ``problems`` lists every reason."""

    def __init__(self, problems):
        self.problems = [problems] if isinstance(problems, str) else list(problems)
        super().__init__("; ".join(self.problems))


def land_bytes(path, raw):
    """Write bytes atomically, creating the directory first.

    Parameters
    ----------
    path : str
        Destination file.
    raw : bytes
        The content.
    """
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    atomic_write(path, raw)


def _land(path, document):
    """Write a JSON document atomically."""
    land_bytes(path, json.dumps(document, indent=2, sort_keys=True).encode(ENCODING))


def _set_dotted(target, dotted, value):
    """Set a dotted key path in a dict; refuse a parent that is not an object."""
    *parents, leaf = dotted.split(".")
    for part in parents:
        target = target.setdefault(part, {})
        if not isinstance(target, dict):
            raise BatchError(f"work_dir_arg {dotted!r}: {part!r} is not an object in the args")
    target[leaf] = value


def _stop(signum, frame):
    """Signal handler: unwind through ``run()``'s finally block."""
    raise SystemExit(128 + signum)


def _kill_group(pid):
    """SIGKILL a process group; a group that is already gone is fine."""
    try:
        os.killpg(pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


class ProcessTree:
    """Resident memory of processes and their descendants, read from ``/proc``.

    Parameters
    ----------
    root : str, default "/proc"
        The process table directory.

    Examples
    --------
    Measure a running child::

        tree = ProcessTree()
        tree.measure([child.pid])
        # -> {4242: 52428800}
    """

    def __init__(self, root=PROC_ROOT):
        self.root = root
        self.page = os.sysconf("SC_PAGE_SIZE") if hasattr(os, "sysconf") else 4096

    def available(self):
        """Return True when the process table can be read."""
        return os.path.isdir(os.path.join(self.root, "self"))

    def _parents(self):
        """Return {pid: parent pid} for every readable process (one scan)."""
        parents = {}
        for name in os.listdir(self.root):
            if not name.isdigit():
                continue
            try:
                with open(os.path.join(self.root, name, "stat"), encoding=ENCODING) as handle:
                    text = handle.read()
                parents[int(name)] = int(text.rsplit(")", 1)[1].split()[1])
            except (OSError, ValueError, IndexError):
                continue
        return parents

    def _rss(self, pid):
        """Return one process's resident bytes; 0 when it is gone."""
        try:
            with open(os.path.join(self.root, str(pid), "statm"), encoding=ENCODING) as handle:
                return int(handle.read().split()[1]) * self.page
        except (OSError, ValueError, IndexError):
            return 0

    def measure(self, roots):
        """Return the summed resident bytes of each root and its descendants.

        Parameters
        ----------
        roots : iterable of int
            Root process ids.

        Returns
        -------
        dict
            ``{root pid: bytes}``, from ONE scan of the process table. Shared
            pages are counted once per process, so each figure is an upper bound.
        """
        parents = self._parents()
        sizes = {}
        for root in roots:
            members, grew = {root}, True
            while grew:
                found = {p for p, q in parents.items() if q in members} - members
                grew = bool(found)
                members |= found
            sizes[root] = sum(self._rss(p) for p in members)
        return sizes


class _Lane:
    """The mutable record of one lane across attempts."""

    def __init__(self, name, work_dir):
        self.name = name
        self.work_dir = work_dir
        self.status = STATUS_PENDING
        self.exit_code = None
        self.attempts = 0
        self.seconds = 0.0
        self.peak_rss_mb = None
        self.halting_step = None
        self.process = None
        self.started = 0.0
        self.log = None

    def seed(self, prior):
        """Take the earlier ``batch.json`` record of a lane that is not run now."""
        self.status = prior.get("status", STATUS_PENDING)
        self.exit_code = prior.get("exit_code")
        self.attempts = prior.get("attempts", 0)
        self.seconds = prior.get("seconds", 0.0)
        self.peak_rss_mb = prior.get("peak_rss_mb")
        self.halting_step = prior.get("halting_step")

    def reset(self):
        """Forget an earlier record: this invocation runs the lane afresh."""
        self.__init__(self.name, self.work_dir)

    def record(self):
        """Return the lane's ``batch.json`` entry."""
        return {
            "lane": self.name, "status": self.status, "exit_code": self.exit_code,
            "attempts": self.attempts, "seconds": round(self.seconds, 3),
            "work_dir": self.work_dir, "halting_step": self.halting_step,
            "peak_rss_mb": self.peak_rss_mb,
        }


def _is_int(value, floor):
    return isinstance(value, int) and not isinstance(value, bool) and value >= floor


def _safe_lane(name):
    """Return a problem text when a lane value is unsafe as a directory name."""
    bad = (
        not name or name in (".", "..") or LANE_SEP in name
        or any(c in name for c in ("/", "\\", "\0")) or name != name.strip()
        or any(ord(c) < 32 for c in name)
    )
    return f"lane {name!r} is not a safe directory name" if bad else None


class WorkflowBatch:
    """Run each lane of a lane manifest as its own ``workflow`` subprocess.

    Parameters
    ----------
    spec : dict
        ``manifest`` (path, required), ``root`` (directory for ``batch.json``,
        logs and per-lane args, required), ``args`` (overlay JSON path or None),
        ``work_dir_arg`` (dotted arg name the lane's work dir is written to,
        default ``work_dir``), ``work_dir_pattern`` (``{root}`` and ``{lane}``
        placeholders, ``{lane}`` required, default ``{root}/{lane}``),
        ``parallel`` (int >= 1, default 1), ``retry`` (int >= 0, default 0),
        ``lanes_per_batch`` (int >= 0: run at most this many pending lanes per
        invocation, 0 = all), ``only_lanes`` (list of lane values to restrict
        to), ``mem_limit_mb`` (0 = report only), ``poll_seconds`` (> 0),
        ``argv_prefix`` (the command that precedes ``manifest --args F --only
        @lane``).

    Raises
    ------
    BatchError
        On any invalid spec, manifest, lane value or work dir pattern.

    Examples
    --------
    Two lanes, two at a time, one retry each::

        batch = WorkflowBatch({"manifest": "study.json", "root": "runs",
                               "parallel": 2, "retry": 1})
        batch.run()
        # -> 0
    """

    def __init__(self, spec):
        problems = self._spec_problems(spec)
        if problems:
            raise BatchError(problems)
        self.spec = dict(spec)
        self.root = spec["root"]
        self.work_dir_arg = spec.get("work_dir_arg", DEFAULT_WORK_DIR_ARG)
        self.pattern = spec.get("work_dir_pattern", DEFAULT_WORK_DIR_PATTERN)
        self.parallel = spec.get("parallel", DEFAULT_PARALLEL)
        self.retry = spec.get("retry", DEFAULT_RETRY)
        self.poll = spec.get("poll_seconds", DEFAULT_POLL_SECONDS)
        self.limit = spec.get("mem_limit_mb", 0)
        self.prefix = list(spec.get("argv_prefix") or DEFAULT_ARGV_PREFIX)
        self.tree = ProcessTree()
        self._pattern_problems()
        try:
            self.base_args = load_overlays(spec.get("args"))
            manifest = load_json(spec["manifest"], "manifest")
            probe = Workflow(manifest, os.path.dirname(os.path.abspath(spec["manifest"])), self.base_args)
        except WorkflowError as err:
            raise BatchError(err.problems)
        if not probe.lane_spec():
            raise BatchError("the manifest declares no lanes")
        self.planned = list(probe.steps)
        names = probe.lane_values()
        self._lane_problems(names)
        wanted = spec.get("only_lanes") or []
        missing = [x for x in wanted if x not in names]
        if missing:
            raise BatchError(f"unknown lane(s) {missing}; the manifest has {names}")
        self.lanes = [_Lane(n, self._work_dir(n)) for n in names]
        self._dir_problems()
        self.selected = [x for x in self.lanes if not wanted or x.name in wanted]
        self._load_prior()

    @staticmethod
    def _spec_problems(spec):
        if not isinstance(spec, dict):
            return ["spec must be an object"]
        problems = [f"unknown spec key {k!r}" for k in sorted(set(spec) - SPEC_KEYS)]
        problems += [f"spec key {k!r} is required" for k in ("manifest", "root") if k not in spec]
        for key in ("manifest", "root", "work_dir_arg", "work_dir_pattern"):
            if key in spec and not (isinstance(spec[key], str) and spec[key]):
                problems.append(f"{key} must be non-empty text")
        given = spec.get("args")
        if given is not None and not (isinstance(given, str) or (
                isinstance(given, list) and all(isinstance(x, str) for x in given))):
            problems.append("args must be a path, a list of paths or None")
        for key, floor in (("parallel", 1), ("retry", 0), ("lanes_per_batch", 0)):
            if key in spec and not _is_int(spec[key], floor):
                problems.append(f"{key} must be an integer >= {floor}")
        limit = spec.get("mem_limit_mb", 0)
        if not number_ok(limit) or limit < 0:
            problems.append("mem_limit_mb must be a number >= 0")
        poll = spec.get("poll_seconds", DEFAULT_POLL_SECONDS)
        if not number_ok(poll) or poll <= 0:
            problems.append("poll_seconds must be a number > 0")
        only = spec.get("only_lanes", [])
        if not isinstance(only, list) or not all(isinstance(x, str) for x in only):
            problems.append("only_lanes must be a list of text")
        prefix = spec.get("argv_prefix")
        if prefix is not None and (not isinstance(prefix, list) or not prefix
                                   or not all(isinstance(x, str) for x in prefix)):
            problems.append("argv_prefix must be a non-empty list of text")
        return problems

    def _pattern_problems(self):
        try:
            parsed = [(f, spec, conv) for _, f, spec, conv in string.Formatter().parse(self.pattern)
                      if f is not None]
        except ValueError as err:
            raise BatchError(f"work_dir_pattern {self.pattern!r}: {err}")
        unknown = sorted({f for f, _, _ in parsed if f not in PATTERN_FIELDS})
        if unknown:
            raise BatchError(f"work_dir_pattern {self.pattern!r}: unknown field(s) {unknown}; use {list(PATTERN_FIELDS)}")
        if any(spec or conv for _, spec, conv in parsed):
            raise BatchError(f"work_dir_pattern {self.pattern!r}: format specs and conversions are not allowed")
        if "lane" not in {f for f, _, _ in parsed}:
            raise BatchError(f"work_dir_pattern {self.pattern!r} must contain {{lane}}")

    @staticmethod
    def _lane_problems(names):
        texts = [n for n in names if not isinstance(n, str)]
        if texts:
            raise BatchError(f"lane values must be text, got {texts!r}")
        own = {BATCH_NAME.casefold(), LANE_ARGS_DIR.casefold(), LOGS_DIR.casefold()}
        problems = [f"lane {n!r} would shadow one of the batch's own entries" for n in names
                    if n.casefold() in own]
        problems += [p for p in map(_safe_lane, names) if p]
        problems += [f"lanes {a!r} and {b!r} collide on a case-insensitive file system"
                     for i, a in enumerate(names) for b in names[i + 1:]
                     if a.casefold() == b.casefold()]
        if problems:
            raise BatchError(problems)

    def _dir_problems(self):
        seen = {}
        for lane in self.lanes:
            key = os.path.abspath(lane.work_dir).casefold()
            if key in seen:
                raise BatchError(f"lanes {seen[key]!r} and {lane.name!r} share work dir {lane.work_dir!r}")
            seen[key] = lane.name

    def _work_dir(self, lane):
        return self.pattern.format(root=self.root, lane=lane)

    def _load_prior(self):
        """Seed lanes from the previous batch.json; lanes run now reset."""
        try:
            with open(os.path.join(self.root, BATCH_NAME), encoding=ENCODING) as handle:
                prior = {x["lane"]: x for x in json.load(handle)["lanes"]}
        except (OSError, ValueError, KeyError, TypeError):
            prior = {}
        for lane in self.lanes:
            if lane.name in prior and isinstance(prior[lane.name], dict):
                lane.seed(prior[lane.name])

    def _lane_args(self, lane):
        """Write and return the lane's args overlay path."""
        merged = json.loads(json.dumps(self.base_args))
        _set_dotted(merged, self.work_dir_arg, lane.work_dir)
        path = os.path.join(self.root, LANE_ARGS_DIR, f"{lane.name}.json")
        _land(path, merged)
        return path

    def _ledger_steps(self, lane):
        """Return the lane's ledger entries keyed by step name, matching the key exactly."""
        path = os.path.join(lane.work_dir, LEDGER_NAME)
        try:
            with open(path, encoding=ENCODING) as handle:
                steps = json.load(handle)["steps"]
        except (OSError, ValueError, KeyError):
            return {}
        found = {}
        for key, entry in steps.items():
            name, sep, owner = key.rpartition(LANE_SEP)
            if sep and owner == lane.name and isinstance(entry, dict):
                found[name] = entry
        return found

    def _halting(self, lane):
        """Return the first planned step (manifest order) without an exit-0 ledger entry."""
        steps = self._ledger_steps(lane)
        for name in self.planned:
            if (steps.get(name) or {}).get("exit_code") != EXIT_OK:
                return f"{name}{LANE_SEP}{lane.name}"
        return None

    def _argv(self, lane):
        return self.prefix + [
            self.spec["manifest"], "--args", self._lane_args(lane), "--only", f"{LANE_SEP}{lane.name}",
        ]

    def _spawn(self, lane):
        os.makedirs(os.path.join(self.root, LOGS_DIR), exist_ok=True)
        lane.log = open(os.path.join(self.root, LOGS_DIR, f"{lane.name}.log"), "ab")
        try:
            lane.attempts += 1
            lane.started = time.monotonic()
            lane.process = subprocess.Popen(
                self._argv(lane), stdout=lane.log, stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        except BaseException:
            lane.log.close()
            lane.log = None
            raise

    def _launch(self, queue, running):
        """Start queued lanes up to the parallel limit; a lane that cannot start fails."""
        while queue and len(running) < self.parallel:
            lane = queue.pop(0)
            try:
                self._spawn(lane)
            except OSError:
                lane.status, lane.exit_code = STATUS_FAILED, EXIT_ERROR
                lane.halting_step = self._halting(lane)
                self._write()
                continue
            running.append(lane)

    def _guard(self, lane, used_bytes):
        """Update the peak memory; kill the lane when over the limit."""
        used = used_bytes / BYTES_PER_MB
        lane.peak_rss_mb = round(max(lane.peak_rss_mb or 0.0, used), 1)
        if self.limit and used > self.limit:
            _kill_group(lane.process.pid)
            lane.status = STATUS_KILLED

    def _finish(self, lane, code):
        """Close a lane's attempt; return True when it should be retried."""
        _kill_group(lane.process.pid)
        lane.process.wait()
        lane.seconds += time.monotonic() - lane.started
        lane.log.close()
        lane.process = lane.log = None
        lane.exit_code = code
        lane.halting_step = self._halting(lane)
        if lane.status != STATUS_KILLED:
            done = code == EXIT_OK and lane.halting_step is None
            lane.status = STATUS_OK if done else STATUS_FAILED
        if lane.status == STATUS_OK:
            lane.halting_step = None
        return (lane.status == STATUS_FAILED and code not in DETERMINISTIC_EXITS
                and lane.attempts <= self.retry)

    def _write(self):
        data = {"manifest": self.spec["manifest"], "lanes": [x.record() for x in self.lanes]}
        _land(os.path.join(self.root, BATCH_NAME), data)

    def _queue(self):
        """Return the lanes this invocation runs; they restart their record."""
        queue = list(self.selected)
        cap = self.spec.get("lanes_per_batch", 0)
        queue = queue[:cap] if cap else queue
        for lane in queue:
            lane.reset()
        return queue

    def _poll_lanes(self, queue, running):
        """Guard memory (one /proc scan) and collect finished lanes."""
        sizes = {}
        if self.tree.available():
            sizes = self.tree.measure([x.process.pid for x in running])
        for lane in list(running):
            if lane.process.pid in sizes:
                self._guard(lane, sizes[lane.process.pid])
            code = lane.process.poll()
            if code is None:
                continue
            running.remove(lane)
            if self._finish(lane, code):
                queue.insert(0, lane)
            self._write()

    @staticmethod
    def _install_handlers():
        """Route SIGTERM and SIGHUP through the finally block; return the old handlers."""
        old = {}
        try:
            for signum in STOP_SIGNALS:
                old[signum] = signal.signal(signum, _stop)
        except ValueError:
            pass
        return old

    def _reap(self, running):
        """Kill and wait for every lane still running (interrupt or error)."""
        for lane in running:
            _kill_group(lane.process.pid)
            lane.process.wait()
            lane.log.close()
            lane.status, lane.exit_code = STATUS_FAILED, EXIT_ERROR
            lane.halting_step = self._halting(lane)
        if running:
            self._write()

    def run(self):
        """Run the selected lanes; return 0 when all succeeded.

        Returns
        -------
        int
            0 every selected lane ok, 1 when a lane failed or was killed. A lane
            left pending by ``lanes_per_batch`` is not a failure.

        Raises
        ------
        BaseException
            Whatever interrupted the run (Ctrl-C included; SIGTERM and SIGHUP
            become ``SystemExit``), after every running lane's process group has
            been killed and ``batch.json`` written. Outside the main thread no
            signal handler is installed.
        """
        queue = self._queue()
        self._write()
        running = []
        old = self._install_handlers()
        try:
            while queue or running:
                self._launch(queue, running)
                time.sleep(self.poll)
                self._poll_lanes(queue, running)
        finally:
            self._reap(running)
            for signum, handler in old.items():
                signal.signal(signum, handler)
        failed = [x for x in self.selected if x.status in (STATUS_FAILED, STATUS_KILLED)]
        return EXIT_ERROR if failed else EXIT_OK


def add_arguments(parser):
    """Add the ``workflow-batch`` arguments to an argparse parser.

    Parameters
    ----------
    parser : argparse.ArgumentParser
        The (sub)parser to fill.

    Returns
    -------
    None
        The parser is changed in place.
    """
    parser.add_argument("manifest", help="path to the lane manifest JSON")
    parser.add_argument("--args", action="append", default=None,
                        help="JSON overlaid on manifest args; repeat to layer files in order")
    parser.add_argument("--root", required=True, help="directory for batch.json, logs, per-lane args")
    parser.add_argument("--work-dir-arg", default=DEFAULT_WORK_DIR_ARG, help="dotted arg set to each lane's work dir")
    parser.add_argument("--work-dir-pattern", default=DEFAULT_WORK_DIR_PATTERN, help="{root} and {lane} placeholders")
    parser.add_argument("--lanes-per-batch", type=int, default=0, help="run at most N pending lanes (0 = all)")
    parser.add_argument("--parallel", type=int, default=DEFAULT_PARALLEL, help="lanes at once")
    parser.add_argument("--retry", type=int, default=DEFAULT_RETRY, help="retries of a failed lane")
    parser.add_argument("--only-lanes", nargs="+", default=[], help="restrict to these lanes")
    parser.add_argument("--mem-limit-mb", type=float, default=0, help="kill a lane above this resident MB (0 = report)")
    parser.add_argument("--poll-seconds", type=float, default=DEFAULT_POLL_SECONDS, help="poll interval")
    parser.add_argument("--argv-prefix", nargs="+", default=None, help="command before the workflow arguments")


def run_cli(args, out=print):
    """Run a batch from parsed arguments.

    Parameters
    ----------
    args : argparse.Namespace
        Parsed with :func:`add_arguments`.
    out : callable, default print
        Receives each refusal line.

    Returns
    -------
    int
        0 every lane ok, 1 a lane failed or the batch was refused.
    """
    spec = {
        "manifest": args.manifest, "root": args.root, "args": args.args,
        "work_dir_arg": args.work_dir_arg, "work_dir_pattern": args.work_dir_pattern,
        "lanes_per_batch": args.lanes_per_batch, "parallel": args.parallel,
        "retry": args.retry, "only_lanes": args.only_lanes,
        "mem_limit_mb": args.mem_limit_mb,
        "poll_seconds": args.poll_seconds, "argv_prefix": args.argv_prefix,
    }
    try:
        return WorkflowBatch(spec).run()
    except BatchError as err:
        for problem in err.problems:
            out(f"refused: {problem}")
        return EXIT_ERROR


def main(argv=None, out=print):
    """Command-line entry: ``python -m dskit.pipeline.workflow_batch ...``.

    Parameters
    ----------
    argv : list of str, optional
        Arguments; defaults to ``sys.argv``.
    out : callable, default print
        Receives each refusal line.

    Returns
    -------
    int
        The exit code of :func:`run_cli`.
    """
    import argparse

    parser = argparse.ArgumentParser(prog="workflow-batch")
    add_arguments(parser)
    return run_cli(parser.parse_args(argv), out)


if __name__ == "__main__":
    raise SystemExit(main())
