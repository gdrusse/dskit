"""The generic JumpSolve doorway: a JuMP program as a pipeline Node (ADR-0328, tier-2 pack).

Subclass :class:`JumpSolve`, name the Julia program in ``program``, implement
``payload(inputs, params)`` (the JSON-able data the program needs) and
``extract(solution)`` (the solved values back into the node's named outputs), and the
base owns the rest: the worker process, the protocol, solver pass-through, the solve
record and the refusals. It is the Julia twin of
:class:`~dskit.pipeline.libs.pyomo.PyomoSolve` and keeps its doctrines.

Bridge doctrine
---------------
The model is built and solved in Julia, in a long-lived subprocess, :class:`JuliaWorker`
(stdlib ``subprocess``, ``threading`` and ``json`` only). Python never imports Julia, so a
Julia crash or out-of-memory kill cannot take the run down, the worker can sit under a
memory cap (the launch command is overridable), and a machine with no Julia imports this
module, plans every document that names a JuMP node, and passes the suite.

Worker ownership
----------------
The first solve of a Julia session pays seconds of compilation, and the driver builds a
fresh node instance for every run (so for every walk-forward fold). Workers therefore
belong to the PROCESS, not to a node: one :class:`JuliaWorker` per distinct launch
command, held by a private registry, started on the first solve that needs it and reused
by every later node with the same command. Each solve carries its node's own timeouts. A
worker is stopped by :meth:`JumpSolve.close` (the worker of that node's command), by a
protocol failure (the next solve starts a fresh one), or at interpreter exit (an
``atexit`` hook closes every worker). The real worker also exits on stdin EOF, so a
Python process killed outright leaves no Julia behind.

Wire protocol (version :data:`PROTOCOL_VERSION`)
-------------------------------------------------
One JSON object per line each way, UTF-8, finite numbers only (``NaN``/``Infinity``
tokens and duplicate keys are refused, never parsed). A request carries ``id`` (an int,
strictly increasing per worker object) and ``op``; every reply carries the same ``id``
(an int, never a bool) and ``ok`` (a bool), and EXACTLY the keys its kind names:

* ``hello`` request: ``{id, op}``. Reply ``{id, ok, protocol, julia, packages}``:
  ``protocol`` int, ``julia`` non-empty str, ``packages`` object of str to str. A
  protocol other than ours refuses before any solve.
* ``solve`` request: ``{id, op, program, data, params, solver, solver_options}``.
  Reply ``{id, ok, solution, record}``: ``solution`` is whatever the program returns
  (the subclass validates it), ``record`` the ten :class:`SolveRecord` fields, each
  type-checked (see :func:`_record_problems`).
* ``shutdown`` request: ``{id, op}``. Reply ``{id, ok}``, then the worker exits.
* Any refusal by the worker: ``{id, ok: false, error: {kind, message}}``, both non-empty
  strings.

Refusal doctrine
----------------
Every failure is a :class:`WorkerRefusal` naming the Julia program and a ``kind``. Only a
well-formed ``ok: false`` reply to a ``solve`` keeps the worker (it answered the protocol,
so its state is consistent). Everything else kills it and the NEXT solve starts a fresh
one, never a silent retry: no start (``start``), a protocol mismatch (``protocol``), any
failed ``hello`` (including a well-formed error reply to it), death (``died``), a reply
past the node's time limit (``timeout``, which also bounds writing the request), a line
that is not the protocol (``malformed``), a mistyped record (``record``). A payload that
is not JSON (``payload``) refuses before anything is sent or started.

Role, output, knob and solve-record doctrines are :mod:`~dskit.pipeline.libs.pyomo`'s:
``role = "capital"`` and ``outputs = None`` on the base, a concrete subclass MUST declare
``outputs`` (``run()`` backstops it), ``_PARAMS = ("solver", "solver_options")`` default-
denies by name (the pyomo pack's own rule, :meth:`PyomoSolve.solver_param_problems`), the
solver NAME's shape is checked at plan and its availability in Julia at run (the worker's
``unknown_solver`` refusal), and every solve leaves a
:class:`~dskit.pipeline.libs.pyomo.SolveRecord` (the very class, so the evaluation
``solve`` event has one shape whichever language solved) on ``solve_record``. The hello
versions of the worker that solved are on ``worker_info``.

Import cost: stdlib and toolkit only, no Julia and no third-party library, ever.
"""

from __future__ import annotations

import atexit
import collections
import json
import math
import pathlib
import queue
import subprocess
import threading
import time
from abc import abstractmethod

from dskit.pipeline.libs.pyomo import BudgetedSelect, PyomoSolve, SolveRecord
from dskit.pipeline.node import DEFAULT_NODE_KINDS, Node

__all__ = [
    "DEFAULT_REQUEST_TIMEOUT",
    "DEFAULT_SHUTDOWN_TIMEOUT",
    "DEFAULT_SOLVER",
    "DEFAULT_START_TIMEOUT",
    "JULIA_EXECUTABLE",
    "JULIA_PACKAGE",
    "NODE_KINDS",
    "OPS",
    "OPTIMAL_TERMINATION",
    "PROTOCOL_VERSION",
    "WORKER_ENTRY",
    "JuliaWorker",
    "JumpBudgetedSelect",
    "JumpSolve",
    "WorkerRefusal",
    "register",
]

#: The wire protocol version both sides must speak; ``hello`` checks it before any solve.
PROTOCOL_VERSION = 1

#: The operations a request may carry.
OPS = ("hello", "solve", "shutdown")

#: The default solver name, HiGHS through its JuMP package.
DEFAULT_SOLVER = "HiGHS"

#: The record's ``termination`` of an optimal solve: the MOI ``TerminationStatusCode``
#: name, as Julia's ``string(termination_status(model))`` writes it.
OPTIMAL_TERMINATION = "OPTIMAL"

#: Seconds a worker may take to answer one request (a solve, so generous by default).
DEFAULT_REQUEST_TIMEOUT = 3600.0

#: Seconds a worker may take to start and answer ``hello`` (Julia precompiles on first use).
DEFAULT_START_TIMEOUT = 600.0

#: Seconds a worker may take to acknowledge ``shutdown`` and exit before it is killed.
DEFAULT_SHUTDOWN_TIMEOUT = 10.0

#: The Julia executable of the default launch command, looked up on ``PATH``.
JULIA_EXECUTABLE = "julia"

#: The Julia package the default command runs, relative to the repository root.
JULIA_PACKAGE = ("julia", "DskitJuMP")

#: The Julia expression that starts the worker loop of that package.
WORKER_ENTRY = "using DskitJuMP; DskitJuMP.serve()"

#: The exact key set of each ``ok: true`` reply, by request op.
_REPLY_KEYS = {
    "hello": frozenset({"id", "ok", "protocol", "julia", "packages"}),
    "solve": frozenset({"id", "ok", "solution", "record"}),
    "shutdown": frozenset({"id", "ok"}),
}

#: The exact key set of an ``ok: false`` reply and of its ``error`` object.
_ERROR_KEYS = frozenset({"id", "ok", "error"})
_ERROR_BODY_KEYS = frozenset({"kind", "message"})

#: The keys a binding row must carry, and the one it may add.
_BINDING_KEYS = frozenset({"name", "rows", "binding", "min_slack"})
_BINDING_OPTIONAL = frozenset({"dual"})

#: Characters of the worker's stderr kept for a refusal's message (the tail).
_STDERR_TAIL_CHARS = 2000

#: Characters of an offending reply line quoted in a refusal.
_QUOTE_CHARS = 200

#: Seconds a refusal waits for the worker's last stderr to arrive once it is dead.
_STDERR_JOIN_SECONDS = 1.0


class WorkerRefusal(RuntimeError):
    """A refused JuMP solve, naming the Julia program.

    Parameters
    ----------
    program : str
        The Julia program the node asked for.
    kind : str
        Why: ``start``, ``protocol``, ``died``, ``timeout``, ``malformed``, ``payload``,
        ``record``, ``solution``, or the ``kind`` of a worker's own error reply.
    message : str
        The detail.

    Examples
    --------
    ::

        refusal = WorkerRefusal("budgeted_select", "timeout", "timed out after 5 s")
        refusal.program  # 'budgeted_select'
    """

    def __init__(self, program, kind, message):
        super().__init__(f"JuMP program {program!r} refused ({kind}): {message}")
        self.program = program
        self.kind = kind


# -- strict JSON and the record check ----------------------------------------


def _refuse_constant(token):
    """``parse_constant`` hook: a ``NaN``/``Infinity`` token is not protocol JSON."""
    raise ValueError(f"the non-finite token {token} is not protocol JSON")


def _refuse_duplicates(pairs):
    """``object_pairs_hook``: an object naming one key twice is not protocol JSON."""
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise ValueError(f"the key {key!r} appears twice")
        obj[key] = value
    return obj


def _strict_loads(line):
    """Parse one reply line, refusing non-finite tokens and duplicate keys (``ValueError``)."""
    return json.loads(line, parse_constant=_refuse_constant,
                      object_pairs_hook=_refuse_duplicates)


def _is_int(value, low=0):
    """Whether ``value`` is a JSON integer (never a bool) no smaller than ``low``."""
    return isinstance(value, int) and not isinstance(value, bool) and value >= low


def _is_number(value, nullable=False, low=None):
    """Whether ``value`` is a finite JSON number (never a bool), or null when ``nullable``."""
    if value is None:
        return nullable
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return math.isfinite(value) and (low is None or value >= low)


def _is_name(value):
    """Whether ``value`` is a non-empty string."""
    return isinstance(value, str) and bool(value)


def _row_problems(i, row):
    """Return the problems of one binding row of a worker's record."""
    if not isinstance(row, dict):
        return [f"binding[{i}] must be an object, got {row!r:.{_QUOTE_CHARS}}"]
    keys = set(row)
    if not _BINDING_KEYS <= keys or keys - _BINDING_KEYS - _BINDING_OPTIONAL:
        return [f"binding[{i}] must carry exactly {sorted(_BINDING_KEYS)} "
                f"(plus optional {sorted(_BINDING_OPTIONAL)}), got {sorted(keys)}"]
    problems = []
    if not _is_name(row["name"]):
        problems.append(f"binding[{i}].name must be a non-empty string")
    if not _is_int(row["rows"]):
        problems.append(f"binding[{i}].rows must be an integer >= 0")
    if not _is_int(row["binding"]) or (_is_int(row["rows"]) and row["binding"] > row["rows"]):
        problems.append(f"binding[{i}].binding must be an integer in [0, rows]")
    if not _is_number(row["min_slack"], nullable=True):
        problems.append(f"binding[{i}].min_slack must be a finite number or null")
    if "dual" in row and not _is_number(row["dual"]):
        problems.append(f"binding[{i}].dual must be a finite number when present")
    return problems


def _record_problems(obj, solver):
    """Return the problems of a worker's record object against the SolveRecord wire shape.

    ``solver`` is the name the request asked for; the record must name the same one.
    """
    names = set(SolveRecord.field_names())
    if not isinstance(obj, dict):
        return [f"the record must be an object, got {obj!r:.{_QUOTE_CHARS}}"]
    if set(obj) != names:
        return [f"the record must carry exactly {sorted(names)}; missing "
                f"{sorted(names - set(obj))}, unexpected {sorted(set(obj) - names)}"]
    problems = []
    if obj["solver"] != solver:
        problems.append(f"record.solver must be the requested {solver!r}, "
                        f"got {obj['solver']!r:.{_QUOTE_CHARS}}")
    for key in ("status", "termination"):
        if not _is_name(obj[key]):
            problems.append(f"record.{key} must be a non-empty string")
    for key in ("objective", "bound"):
        if not _is_number(obj[key], nullable=True):
            problems.append(f"record.{key} must be a finite number or null")
    if not _is_number(obj["gap"], nullable=True, low=0):
        problems.append("record.gap must be a finite number >= 0 or null")
    if not _is_number(obj["seconds"], low=0):
        problems.append("record.seconds must be a finite number >= 0")
    for key in ("variables", "constraints"):
        if not _is_int(obj[key]):
            problems.append(f"record.{key} must be an integer >= 0")
    if not isinstance(obj["binding"], list):
        problems.append("record.binding must be a list of rows")
    else:
        for i, row in enumerate(obj["binding"]):
            problems.extend(_row_problems(i, row))
    return problems


def _hello_problems(reply):
    """Return the problems of an ``ok`` hello reply's fields (protocol already matched)."""
    problems = []
    if not _is_name(reply["julia"]):
        problems.append("julia must be a non-empty string")
    packages = reply["packages"]
    if not (isinstance(packages, dict)
            and all(_is_name(k) and _is_name(v) for k, v in packages.items())):
        problems.append("packages must be an object of package name to version string")
    return problems


# -- the pipes -----------------------------------------------------------------


def _read_lines(stream, sink):
    """Thread body: put every line of ``stream`` on ``sink``, then ``None`` at EOF."""
    try:
        for line in stream:
            sink.put(line)
    except (OSError, ValueError):
        pass
    finally:
        sink.put(None)


def _keep_tail(stream, tail):
    """Thread body: keep the last characters of ``stream`` in the ``tail`` deque."""
    try:
        while True:
            chunk = stream.read(4096)
            if not chunk:
                return
            tail.extend(chunk)
    except (OSError, ValueError):
        pass


def _write_line(stream, text, errors):
    """Thread body: write ``text`` and flush; record any failure in ``errors``."""
    try:
        stream.write(text)
        stream.flush()
    except (OSError, ValueError) as exc:
        errors.append(exc)


class JuliaWorker:
    """One long-lived Julia worker process and the JSON-lines protocol to it.

    Starts lazily on the first :meth:`solve`, checks ``hello``, and is reused for every
    later solve. Thread-safe: one request at a time, under a lock. Every pipe is
    serviced off the calling thread (stdout and stderr by reader threads, each request by
    a writer thread), so a large payload, a large reply or a worker that floods stderr
    cannot deadlock, and every exchange is bounded by its timeout.

    Parameters
    ----------
    command : sequence of str
        The launch command (``default_command()`` for the real worker; tests pass the
        fake worker's).
    shutdown_timeout : float
        Seconds ``close`` waits for the worker to acknowledge ``shutdown`` and exit.

    Attributes
    ----------
    info : dict or None
        The ``hello`` reply (``protocol``, ``julia``, ``packages``) of the running worker.
    starts : int
        How many processes this object has started.

    Examples
    --------
    ::

        worker = JuliaWorker(JuliaWorker.default_command())
        solution, record = worker.solve("budgeted_select", data, {"budget": 10.0},
                                        "HiGHS", {}, timeout=60.0, start_timeout=600.0)
        worker.close()
    """

    def __init__(self, command, *, shutdown_timeout=DEFAULT_SHUTDOWN_TIMEOUT):
        self.command = [str(part) for part in command]
        self.shutdown_timeout = float(shutdown_timeout)
        self.info = None
        self.starts = 0
        self._process = None
        self._lines = None
        self._stderr = None
        self._stderr_thread = None
        self._next_id = 0
        self._lock = threading.Lock()

    @staticmethod
    def default_command():
        """Return the real worker's launch command, derived from this module's location.

        Returns
        -------
        list of str
            ``julia --project=<repo>/julia/DskitJuMP -e "<worker entry>"``.
        """
        root = pathlib.Path(__file__).resolve().parents[3]
        package = root.joinpath(*JULIA_PACKAGE)
        return [JULIA_EXECUTABLE, f"--project={package}", "-e", WORKER_ENTRY]

    @property
    def pid(self):
        """The running process id, or None when no process is running."""
        return None if self._process is None else self._process.pid

    def solve(self, program, data, params, solver, solver_options, *,
              timeout=DEFAULT_REQUEST_TIMEOUT, start_timeout=DEFAULT_START_TIMEOUT):
        """Run one solve, starting (and ``hello``-checking) the worker first when needed.

        Parameters
        ----------
        program : str
            The Julia program's registry name.
        data : object
            The program's JSON-able data.
        params : dict
            The program's own knobs.
        solver : str
            The optimizer name.
        solver_options : dict
            Flat option mapping handed to the optimizer verbatim.
        timeout : float
            Seconds the solve request may take, writing it included.
        start_timeout : float
            Seconds a start and its ``hello`` may take, when a start is needed.

        Returns
        -------
        tuple
            ``(solution, record)``: the program's solution as parsed JSON, and the
            :class:`~dskit.pipeline.libs.pyomo.SolveRecord` of the solve.

        Raises
        ------
        WorkerRefusal
            Naming ``program``, for every failure of the worker or the protocol; the
            worker is discarded on all but a well-formed error reply.
        """
        body = {"op": "solve", "program": program, "data": data, "params": params,
                "solver": solver, "solver_options": solver_options}
        try:
            json.dumps(body, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise WorkerRefusal(program, "payload",
                                f"the request is not JSON-serialisable: {exc}") from exc
        with self._lock:
            self._ensure_started(program, float(start_timeout))
            reply = self._exchange(program, body, float(timeout))
            problems = _record_problems(reply["record"], solver)
            if problems:
                self._discard()
                raise WorkerRefusal(program, "record", "; ".join(problems))
            record = reply["record"]
            return reply["solution"], SolveRecord(
                **{**record, "binding": tuple(dict(row) for row in record["binding"])})

    def close(self):
        """Shut the worker down (idempotent): ask politely, then kill after the timeout."""
        with self._lock:
            process = self._process
            if process is None:
                return
            try:
                if process.poll() is None:
                    self._exchange("shutdown", {"op": "shutdown"}, self.shutdown_timeout)
                    process.wait(timeout=self.shutdown_timeout)
            except (WorkerRefusal, subprocess.TimeoutExpired):
                pass
            finally:
                self._discard()

    def __del__(self):
        """Leave no orphan when the object is collected."""
        try:
            self._discard()
        except Exception:  # noqa: BLE001 - interpreter teardown may have lost anything
            pass

    def __enter__(self):
        """Return self; the worker starts on first use."""
        return self

    def __exit__(self, *exc):
        """Close the worker."""
        self.close()

    # -- the process --------------------------------------------------------

    def _ensure_started(self, program, start_timeout):
        """Start a process and check ``hello``, unless a hello-checked one is running."""
        if self._process is not None and self._process.poll() is None and self.info:
            return
        self._discard()
        try:
            process = subprocess.Popen(
                self.command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace",
                bufsize=1,
            )
        except OSError as exc:
            raise WorkerRefusal(program, "start",
                                f"cannot start the worker {self.command!r}: {exc}") from exc
        self.starts += 1
        self._process = process
        self._lines = queue.Queue()
        self._stderr = collections.deque(maxlen=_STDERR_TAIL_CHARS)
        threading.Thread(target=_read_lines, args=(process.stdout, self._lines),
                         daemon=True).start()
        self._stderr_thread = threading.Thread(
            target=_keep_tail, args=(process.stderr, self._stderr), daemon=True)
        self._stderr_thread.start()
        try:
            reply = self._exchange(program, {"op": "hello"}, start_timeout)
        except WorkerRefusal:
            self._discard()  # a worker that failed hello is never used, even if it lives
            raise
        self.info = {key: reply[key] for key in ("protocol", "julia", "packages")}

    def _discard(self):
        """Kill the process (if any), reap it, drop its pipes; return its stderr tail."""
        process, self._process = self._process, None
        self.info = None
        if process is None:
            return ""
        try:
            if process.poll() is None:
                process.kill()
            process.wait()
        finally:
            if self._stderr_thread is not None:
                self._stderr_thread.join(timeout=_STDERR_JOIN_SECONDS)
            for stream in (process.stdin, process.stdout, process.stderr):
                try:
                    stream.close()
                except (OSError, ValueError):
                    pass
        tail = "".join(self._stderr or ()).strip()
        return f" | stderr tail: {tail[-_STDERR_TAIL_CHARS:]}" if tail else ""

    # -- one request/reply --------------------------------------------------

    def _exchange(self, program, request, timeout):
        """Send ``request``, return its validated ``ok`` reply, or refuse naming ``program``.

        One deadline covers writing the request and reading the reply.
        """
        deadline = time.monotonic() + timeout
        self._next_id += 1
        sent = self._next_id
        line = json.dumps({"id": sent, **request}, allow_nan=False) + "\n"
        errors = []
        writer = threading.Thread(target=_write_line,
                                  args=(self._process.stdin, line, errors), daemon=True)
        writer.start()
        writer.join(timeout=max(0.0, deadline - time.monotonic()))
        if writer.is_alive():
            tail = self._discard()  # closing the pipe frees the blocked writer
            raise WorkerRefusal(program, "timeout",
                                f"timed out after {timeout:g} s writing the request (the "
                                f"worker stopped reading); the worker was killed{tail}")
        try:
            reply_line = None if errors else self._lines.get(
                timeout=max(0.0, deadline - time.monotonic()))
        except queue.Empty:
            tail = self._discard()
            raise WorkerRefusal(program, "timeout",
                                f"timed out after {timeout:g} s waiting for a reply; "
                                f"the worker was killed{tail}") from None
        if reply_line is None:
            try:
                code = self._process.wait(timeout=_STDERR_JOIN_SECONDS)
            except subprocess.TimeoutExpired:
                code = None
            tail = self._discard()
            raise WorkerRefusal(program, "died",
                                f"the worker died without a reply (exit {code}){tail}")
        return self._reply(program, request["op"], reply_line, sent)

    def _malformed(self, program, why, line):
        """Discard the worker and refuse a reply that is not the protocol."""
        self._discard()
        return WorkerRefusal(program, "malformed",
                             f"{why}: {line.strip()[:_QUOTE_CHARS]!r}")

    def _reply(self, program, op, line, sent):
        """Parse one reply line against the protocol; discard the worker if it is broken."""
        try:
            reply = _strict_loads(line)
        except ValueError as exc:
            raise self._malformed(program, f"a reply that is not protocol JSON ({exc})",
                                  line) from None
        if not isinstance(reply, dict) or not isinstance(reply.get("ok"), bool):
            raise self._malformed(program, "a reply that is not an object with a boolean "
                                  "'ok'", line)
        rid = reply.get("id")
        if not _is_int(rid, low=1) or rid != sent:
            raise self._malformed(program, f"a reply under id {rid!r}, the request was id "
                                  f"{sent}", line)
        if not reply["ok"]:
            error = reply.get("error")
            if (set(reply) != _ERROR_KEYS or not isinstance(error, dict)
                    or set(error) != _ERROR_BODY_KEYS
                    or not all(_is_name(error[k]) for k in _ERROR_BODY_KEYS)):
                raise self._malformed(program, "an error reply that is not exactly "
                                      "{id, ok, error: {kind, message}}", line)
            raise WorkerRefusal(program, error["kind"], error["message"])
        if op == "hello" and reply.get("protocol") != PROTOCOL_VERSION:
            protocol = reply.get("protocol")
            self._discard()
            raise WorkerRefusal(
                program, "protocol",
                f"the worker speaks protocol {protocol!r}, this pack speaks "
                f"{PROTOCOL_VERSION} (julia {reply.get('julia')!r}): refused before any solve")
        if set(reply) != _REPLY_KEYS[op]:
            raise self._malformed(program, f"a {op} reply that is not exactly "
                                  f"{sorted(_REPLY_KEYS[op])}", line)
        if op == "hello" and (not _is_int(reply["protocol"]) or _hello_problems(reply)):
            raise self._malformed(program, "; ".join(_hello_problems(reply))
                                  or "protocol must be an integer", line)
        return reply


class _WorkerRegistry:
    """The process's workers, one per launch command (see the module's ownership doctrine)."""

    def __init__(self):
        self._workers = {}
        self._lock = threading.Lock()

    def worker(self, command):
        """Return the worker of ``command``, created (not started) on first use."""
        key = tuple(str(part) for part in command)
        with self._lock:
            if key not in self._workers:
                self._workers[key] = JuliaWorker(key)
            return self._workers[key]

    def live(self):
        """Return the launch commands whose worker has a running process, sorted."""
        with self._lock:
            return sorted(key for key, worker in self._workers.items()
                          if worker.pid is not None)

    def close(self, command):
        """Shut down and forget the worker of ``command`` (idempotent)."""
        with self._lock:
            worker = self._workers.pop(tuple(str(part) for part in command), None)
        if worker is not None:
            worker.close()

    def close_all(self):
        """Shut down and forget every worker (the ``atexit`` hook)."""
        with self._lock:
            workers, self._workers = list(self._workers.values()), {}
        for worker in workers:
            worker.close()


#: The process-wide worker registry.
_POOL = _WorkerRegistry()
atexit.register(_POOL.close_all)


class JumpSolve(Node):
    """The abstract doorway: one JuMP program, solved in the Julia worker, as one Node.

    Subclasses name the Julia program in :attr:`program` and implement exactly two hooks:
    :meth:`payload` (inputs and params in, the JSON-able data the program needs out) and
    :meth:`extract` (the program's solution in, the node's named outputs out, matching the
    subclass's declared ``outputs``). The base's :meth:`run` refuses an undeclared output
    contract or program, builds the payload, solves in the process's worker for this
    node's launch command (started on first use, shared by every node with the same
    command), records the solve and hands the solution to ``extract``.

    Abstract on both hooks, so it can never register as a kind. :meth:`close` stops the
    worker of this node's command; otherwise it lives until interpreter exit.

    Parameters
    ----------
    params : dict
        ``solver`` (name, default :data:`DEFAULT_SOLVER`) and ``solver_options`` (a flat
        mapping of option name to scalar, handed to the optimizer verbatim); a subclass
        extends :attr:`_PARAMS`.

    Examples
    --------
    A concrete subclass is a program name, two hooks and a declared contract::

        class PickTwo(JumpSolve):
            role = "transform"
            outputs = ("picked",)
            program = "pick_two"

            def payload(self, inputs, params):
                return {"scores": inputs["scores"]}

            def extract(self, solution):
                return {"picked": solution["picked"]}

        node = PickTwo("pick", {"solver": "HiGHS"})
    """

    #: Capital by default (see the pyomo pack's role doctrine); a non-capital subclass
    #: overrides this class attribute.
    role = "capital"

    #: Deliberately undeclared on the base; every concrete subclass MUST declare it.
    outputs = None

    #: The base's own knobs; a subclass extends the tuple. ``validate_params``
    #: default-denies everything else by name.
    _PARAMS = ("solver", "solver_options")

    #: The Julia program's registry name; every concrete subclass MUST name one.
    program = None

    #: The worker launch command; ``None`` means :meth:`JuliaWorker.default_command`.
    #: Tests substitute the fake worker, a deployment a memory-capped wrapper.
    worker_command = None

    #: Seconds a solve may take before the worker is killed and the solve refused.
    request_timeout = DEFAULT_REQUEST_TIMEOUT

    #: Seconds the worker may take to start and answer ``hello``.
    start_timeout = DEFAULT_START_TIMEOUT

    #: The :class:`~dskit.pipeline.libs.pyomo.SolveRecord` of the last solve; ``None``
    #: before a solve, when the solve was refused, and after a no-solve short circuit.
    solve_record = None

    #: The ``hello`` reply of the worker that made the last solve (protocol, Julia version,
    #: package versions), so a run records what solved it.
    worker_info = None

    @classmethod
    def validate_params(cls, params):
        """Return the problems with ``params``: unknown names, a bad solver name or options.

        Parameters
        ----------
        params : dict
            The node's params from the document.

        Returns
        -------
        list of str
            One problem per defect; empty when the params are well-formed. Only the
            solver name's SHAPE is checked here (no Julia at plan time).
        """
        return PyomoSolve.solver_param_problems(params, cls._PARAMS, DEFAULT_SOLVER)

    # -- the two subclass hooks ---------------------------------------------

    @abstractmethod
    def payload(self, inputs, params):
        """Return the JSON-able data the Julia program builds its model from.

        Parameters
        ----------
        inputs : dict
            The node's materialized upstream inputs.
        params : dict
            ``self.params``, passed so the hook is honest about what it depends on.

        Returns
        -------
        object
            Anything ``json.dumps`` accepts with finite numbers only.
        """
        raise NotImplementedError

    @abstractmethod
    def extract(self, solution):
        """Read the program's solution back into the node's named outputs.

        Parameters
        ----------
        solution : object
            What the Julia program returned, as parsed JSON. ``solve_record`` is already
            set, so a program that accepts only an optimal solve checks it here.

        Returns
        -------
        dict
            The node's outputs, matching the declared ``outputs`` exactly.
        """
        raise NotImplementedError

    # -- the base-owned lifecycle -------------------------------------------

    def run(self, ctx, inputs):
        """Solve in the worker and return the node's outputs.

        Parameters
        ----------
        ctx : NodeContext
            The run context (unused by the base).
        inputs : dict
            The node's materialized upstream inputs.

        Returns
        -------
        dict
            What ``extract`` returned for the solution.

        Raises
        ------
        TypeError
            When the subclass declares no ``outputs`` or names no ``program``.
        WorkerRefusal
            For any worker or protocol failure, naming the program.
        """
        declared = type(self).outputs
        if declared is None:
            raise TypeError(
                f"{type(self).__name__} declares no outputs contract — a concrete "
                "JumpSolve subclass must declare `outputs = (...)` so its run() return "
                "is checkable (an undeclared contract is a contract nothing can check)"
            )
        program = type(self).program
        if not isinstance(program, str) or not program:
            raise TypeError(
                f"{type(self).__name__} names no Julia program — a concrete JumpSolve "
                "subclass must set `program = \"<registry name>\"`"
            )
        self.solve_record = None
        data = self.payload(inputs, self.params)
        name = self.params.get("solver", DEFAULT_SOLVER)
        self.log.info("solving %r with %r", program, name)
        worker = self._the_worker()
        solution, self.solve_record = worker.solve(
            program, data, self._program_params(), name, self._solver_options(),
            timeout=self.request_timeout, start_timeout=self.start_timeout)
        self.worker_info = dict(worker.info)
        extracted = self.extract(solution)
        if not isinstance(extracted, dict):
            raise TypeError(
                f"{type(self).__name__}.extract() must return the node's named outputs "
                f"as a dict, got {type(extracted).__name__}"
            )
        return extracted

    def close(self):
        """Stop the worker of this node's launch command (idempotent); it restarts on use."""
        _POOL.close(self._command())

    def _command(self):
        """Return this node's launch command."""
        command = self.worker_command
        return JuliaWorker.default_command() if command is None else command

    def _program_params(self):
        """Return the knobs the Julia program receives: every param but the solver's two."""
        return {k: v for k, v in self.params.items() if k not in JumpSolve._PARAMS}

    def _solver_options(self):
        """Return the options handed to the optimizer (a subclass merges defaults UNDER)."""
        return dict(self.params.get("solver_options") or {})

    def _the_worker(self):
        """Return the process's worker for this node's command."""
        return _POOL.worker(self._command())


class JumpBudgetedSelect(JumpSolve):
    """The reference subclass: a 0/1 knapsack behind the gate, solved by JuMP.

    The same node as :class:`~dskit.pipeline.libs.pyomo.BudgetedSelect`, whose contract
    it imports rather than restates (input and budget checks, the empty-gate outputs,
    the over-budget refusal, the output assembly): inputs ``candidates`` (``{id, cost,
    value}`` rows) and ``survivors`` (the stat_test survivors wire), only surviving
    candidates enter the model, ``params.budget`` is the spend ceiling. An EMPTY gate
    deploys nothing and never starts a worker; the reported ``outlay`` can never exceed
    ``budget``. Under the default solver the solve is pinned deterministic (the pyomo
    twin's pins). The Julia program is ``budgeted_select``: ``data`` is ``{ids, cost,
    value}`` (parallel lists over the eligible candidates, deck order), ``params`` is
    ``{budget}``, and the solution is ``{selected: [ids]}`` (or anything, null included,
    when the termination is not :data:`OPTIMAL_TERMINATION`).

    Parameters
    ----------
    params : dict
        ``budget`` (finite number > 0, required), ``solver``, ``solver_options``.

    Examples
    --------
    ::

        node = JumpBudgetedSelect("select", {"budget": 10.0})
        out = node.run(ctx, {"candidates": rows, "survivors": ["ALPHA", "BRAVO"]})
        node.close()
    """

    role = "capital"
    outputs = ("positions", "outlay", "metrics")
    program = "budgeted_select"
    _PARAMS = JumpSolve._PARAMS + ("budget",)

    #: The pyomo twin's determinism pins (one owner), injected UNDER the document's own
    #: options when the solver is the default. HiGHS names these options the same way
    #: through either language.
    _HIGHS_DETERMINISM = BudgetedSelect._HIGHS_DETERMINISM

    #: The input check is the pyomo twin's, not a copy.
    validate_inputs = BudgetedSelect.validate_inputs

    @classmethod
    def validate_params(cls, params):
        """Return the base's problems plus the budget's (the pyomo twin's rule).

        Parameters
        ----------
        params : dict
            The node's params from the document.

        Returns
        -------
        list of str
            One problem per defect; empty when well-formed.
        """
        return super().validate_params(params) + BudgetedSelect.budget_problems(params)

    def _solver_options(self):
        """Return the document's options over the determinism pins (default solver only)."""
        options = super()._solver_options()
        if self.params.get("solver", DEFAULT_SOLVER) != DEFAULT_SOLVER:
            return options
        return {**self._HIGHS_DETERMINISM, **options}

    def run(self, ctx, inputs):
        """Return the selection; an empty gate deploys zero and never starts the worker.

        Parameters
        ----------
        ctx : NodeContext
            The run context.
        inputs : dict
            ``candidates`` and ``survivors``.

        Returns
        -------
        dict
            ``positions``, ``outlay`` and ``metrics``.

        Raises
        ------
        AssertionError
            When the reported outlay exceeds the budget.
        """
        budget = float(self.params["budget"])
        if not BudgetedSelect.eligible(inputs):
            # The gate cleared no one: deploy NOTHING and never wake the worker.
            self.solve_record = None
            self.log.info("gate cleared none of %d candidate(s) — zero outlay, "
                          "worker not started", len(inputs["candidates"]))
            return BudgetedSelect.nothing_cleared(len(inputs["candidates"]))
        return BudgetedSelect.refuse_over_budget(super().run(ctx, inputs), budget)

    def payload(self, inputs, params):
        """Return ``{ids, cost, value}`` of the surviving candidates, and remember them.

        Parameters
        ----------
        inputs : dict
            ``candidates`` and ``survivors``.
        params : dict
            Unused; the budget travels as a program param.

        Returns
        -------
        dict
            Parallel lists over the eligible candidates.
        """
        rows = BudgetedSelect.eligible(inputs)
        ids = [row["id"] for row in rows]
        cost = [float(row["cost"]) for row in rows]
        value = [float(row["value"]) for row in rows]
        # Bookkeeping for extract(): what was sent, and how many candidates there were.
        self._select = {"ids": ids, "cost": dict(zip(ids, cost)),
                        "value": dict(zip(ids, value)),
                        "n_candidates": len(inputs["candidates"])}
        return {"ids": ids, "cost": cost, "value": value}

    def extract(self, solution):
        """Return the outputs of an optimal solve, refusing any other termination.

        Parameters
        ----------
        solution : dict
            ``{"selected": [ids]}`` from the Julia program.

        Returns
        -------
        dict
            ``positions``, ``outlay`` and ``metrics``.

        Raises
        ------
        RuntimeError
            When the solve was not optimal.
        WorkerRefusal
            Kind ``solution``, when the solution is not ``{"selected": [ids]}`` over
            distinct ids that were sent.
        """
        record = self.solve_record
        termination = None if record is None else record.termination
        if termination != OPTIMAL_TERMINATION:
            raise RuntimeError(
                f"solver finished with termination {termination!r}, not "
                f"{OPTIMAL_TERMINATION!r} — refusing to read a selection off a "
                "non-optimal solve"
            )
        meta = self._select
        selected = solution.get("selected") if isinstance(solution, dict) else None
        if (not isinstance(solution, dict) or set(solution) != {"selected"}
                or not isinstance(selected, list)
                or not all(isinstance(cid, str) for cid in selected)
                or len(set(selected)) != len(selected)
                or not set(selected) <= set(meta["cost"])):
            raise WorkerRefusal(
                self.program, "solution",
                f"the solution must be exactly {{\"selected\": [distinct ids that were "
                f"sent]}}, got {solution!r:.{_QUOTE_CHARS}}")
        return BudgetedSelect.selection(sorted(selected), meta["cost"], meta["value"],
                                        len(meta["ids"]), meta["n_candidates"])


#: The pack's kinds, concrete classes only (the abstract base never registers; documents
#: subclassing JumpSolve themselves reference it by import path).
NODE_KINDS = (("jump-budgeted-select", JumpBudgetedSelect),)


def register(registry=None) -> None:
    """Claim the pack's kind names in ``registry`` (default the toolkit's).

    Deliberate and explicit, never an import side effect. Idempotent: a name already
    present is skipped, never shadowed.

    Parameters
    ----------
    registry : NodeKindRegistry or None
        Where to register; ``None`` means ``DEFAULT_NODE_KINDS``.
    """
    registry = DEFAULT_NODE_KINDS if registry is None else registry
    for name, cls in NODE_KINDS:
        if name not in registry:
            registry.register(name, cls)
