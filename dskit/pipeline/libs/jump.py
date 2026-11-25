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
(stdlib ``subprocess`` and ``json`` only). Python never imports Julia, so a Julia crash
or out-of-memory kill cannot take the run down, the worker can sit under a memory cap
(the launch command is overridable), and a machine with no Julia imports this module,
plans every document that names a JuMP node, and passes the suite. The first solve of a
Julia session pays seconds of compilation, so one worker serves every solve a node runs.

Wire protocol (version :data:`PROTOCOL_VERSION`)
-------------------------------------------------
One JSON object per line each way, UTF-8, ``null`` for a non-finite number the Julia side
cannot send. A request carries ``id`` (an int, answered under the same id) and ``op``:

* ``hello``: no other field. The reply carries ``protocol`` (int), ``julia`` (str) and
  ``packages`` (name to version). A protocol other than ours refuses before any solve.
* ``solve``: ``program``, ``data`` (the node's payload), ``params`` (the node's own knobs,
  never ``solver`` or ``solver_options``), ``solver`` (str) and ``solver_options`` (flat
  mapping handed to the optimizer verbatim). The reply carries ``solution`` (whatever the
  program returns) and ``record`` (the ten :class:`SolveRecord` fields).
* ``shutdown``: the worker acknowledges and exits.

Every reply has ``id`` and ``ok``. ``ok: false`` carries ``error`` as ``{kind, message}``.

Refusal doctrine
----------------
A worker that cannot start, speaks another protocol, dies, exceeds the node's time limit,
answers a line that is not the protocol, or answers under another id is a
:class:`WorkerRefusal` naming the Julia program. Never a silent retry: the broken worker
is killed and the NEXT solve starts a fresh one. A well-formed ``ok: false`` reply is also
a refusal (naming the program and the worker's ``kind``), but it leaves a consistent
worker, which is kept.

Role, output, knob and solve-record doctrines are :mod:`~dskit.pipeline.libs.pyomo`'s:
``role = "capital"`` and ``outputs = None`` on the base, a concrete subclass MUST declare
``outputs`` (``run()`` backstops it), ``_PARAMS = ("solver", "solver_options")`` default-
denies by name, the solver NAME's shape is checked at plan and its availability in Julia at
run, and every solve leaves a :class:`~dskit.pipeline.libs.pyomo.SolveRecord` (the very
class, so the evaluation ``solve`` event has one shape whichever language solved) on
``solve_record``. The hello versions of the worker that solved are on ``worker_info``.

Import cost: stdlib and toolkit only, no Julia and no third-party library, ever.
"""

from __future__ import annotations

import atexit
import collections
import json
import math
import pathlib
import queue
import re
import subprocess
import threading
import weakref
from abc import abstractmethod

from dskit.pipeline.libs.pyomo import (
    BudgetedSelect,
    SolveRecord,
    _is_scalar,
    _SOLVER_NAME_OK,
)
from dskit.pipeline.node import DEFAULT_NODE_KINDS, Node

__all__ = [
    "DEFAULT_REQUEST_TIMEOUT",
    "DEFAULT_SHUTDOWN_TIMEOUT",
    "DEFAULT_SOLVER",
    "DEFAULT_START_TIMEOUT",
    "NODE_KINDS",
    "OPS",
    "PROTOCOL_VERSION",
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

#: Lines of the worker's stderr kept for a refusal's message.
_STDERR_TAIL_LINES = 20

#: Seconds a refusal waits for the worker's last stderr lines to arrive.
_STDERR_JOIN_SECONDS = 1.0


class WorkerRefusal(RuntimeError):
    """A refused JuMP solve, naming the Julia program.

    Parameters
    ----------
    program : str
        The Julia program the node asked for.
    kind : str
        Why: ``start``, ``protocol``, ``died``, ``timeout``, ``malformed``, ``payload``,
        ``record``, or the ``kind`` of a worker's own error reply.
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
    """Thread body: keep the last lines of ``stream`` in ``tail`` for refusal messages."""
    try:
        for line in stream:
            tail.append(line.rstrip("\n"))
    except (OSError, ValueError):
        pass


#: Every worker with a live process, so interpreter exit leaves no orphan.
_LIVE = weakref.WeakSet()


def _close_live():
    """Close every worker still alive (the ``atexit`` hook)."""
    for worker in list(_LIVE):
        worker.close()


atexit.register(_close_live)


class JuliaWorker:
    """One long-lived Julia worker process and the JSON-lines protocol to it.

    Starts lazily on the first :meth:`solve`, checks ``hello``, and is reused for every
    later solve. Not thread-safe: one caller at a time (a node runs one solve at a time).

    Parameters
    ----------
    command : sequence of str
        The launch command (``default_command()`` for the real worker; tests pass the
        fake worker's).
    timeout : float
        Seconds a request may take to be answered.
    start_timeout : float
        Seconds the start and ``hello`` may take.
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
                                        "HiGHS", {})
        worker.close()
    """

    def __init__(self, command, *, timeout=DEFAULT_REQUEST_TIMEOUT,
                 start_timeout=DEFAULT_START_TIMEOUT,
                 shutdown_timeout=DEFAULT_SHUTDOWN_TIMEOUT):
        self.command = [str(part) for part in command]
        self.timeout = float(timeout)
        self.start_timeout = float(start_timeout)
        self.shutdown_timeout = float(shutdown_timeout)
        self.info = None
        self.starts = 0
        self._process = None
        self._lines = None
        self._stderr = None
        self._stderr_thread = None
        self._next_id = 0

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

    def solve(self, program, data, params, solver, solver_options):
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
        self._ensure_started(program)
        reply = self._exchange(program, {
            "op": "solve", "program": program, "data": data, "params": params,
            "solver": solver, "solver_options": solver_options,
        }, self.timeout)
        for key in ("solution", "record"):
            if key not in reply:
                self._discard()
                raise WorkerRefusal(program, "malformed", f"the solve reply has no {key!r}")
        return reply["solution"], self._record(program, reply["record"])

    def close(self):
        """Shut the worker down (idempotent): ask politely, then kill after the timeout."""
        process = self._process
        if process is None:
            return
        try:
            if process.poll() is None:
                self._send({"op": "shutdown"})
                try:
                    self._lines.get(timeout=self.shutdown_timeout)
                    process.wait(timeout=self.shutdown_timeout)
                except (queue.Empty, subprocess.TimeoutExpired):
                    pass
        except (OSError, ValueError):
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

    def _ensure_started(self, program):
        """Start a process and check ``hello``, unless one is running."""
        if self._process is not None and self._process.poll() is None:
            return
        self._discard()
        try:
            process = subprocess.Popen(
                self.command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, text=True, encoding="utf-8", bufsize=1,
            )
        except OSError as exc:
            raise WorkerRefusal(program, "start",
                                f"cannot start the worker {self.command!r}: {exc}") from exc
        self.starts += 1
        self._process = process
        self._lines = queue.Queue()
        self._stderr = collections.deque(maxlen=_STDERR_TAIL_LINES)
        threading.Thread(target=_read_lines, args=(process.stdout, self._lines),
                         daemon=True).start()
        self._stderr_thread = threading.Thread(
            target=_keep_tail, args=(process.stderr, self._stderr), daemon=True)
        self._stderr_thread.start()
        _LIVE.add(self)
        reply = self._exchange(program, {"op": "hello"}, self.start_timeout)
        info = {key: reply.get(key) for key in ("protocol", "julia", "packages")}
        if info["protocol"] != PROTOCOL_VERSION:
            self._discard()
            raise WorkerRefusal(
                program, "protocol",
                f"the worker speaks protocol {info['protocol']!r}, this pack speaks "
                f"{PROTOCOL_VERSION} (julia {info['julia']!r}): refused before any solve")
        self.info = info

    def _discard(self):
        """Kill the process (if any) and drop its pipes; the next solve starts afresh."""
        process, self._process = self._process, None
        self.info = None
        _LIVE.discard(self)
        if process is None:
            return
        try:
            if process.poll() is None:
                process.kill()
            process.wait()
        finally:
            for stream in (process.stdin, process.stdout, process.stderr):
                try:
                    stream.close()
                except (OSError, ValueError):
                    pass

    # -- one request/reply --------------------------------------------------

    def _send(self, request):
        """Write one request line under a fresh id; return the id."""
        self._next_id += 1
        line = json.dumps({"id": self._next_id, **request}, allow_nan=False)
        self._process.stdin.write(line + "\n")
        self._process.stdin.flush()
        return self._next_id

    def _tail(self):
        """Return the worker's recent stderr as a message suffix (empty when it said nothing)."""
        if self._stderr_thread is not None:
            self._stderr_thread.join(timeout=_STDERR_JOIN_SECONDS)  # EOF, once the process is gone
        lines = list(self._stderr or ())
        return " | stderr: " + " / ".join(lines) if lines else ""

    def _exchange(self, program, request, timeout):
        """Send ``request``, return its validated ``ok`` reply, or refuse naming ``program``."""
        try:
            sent = self._send(request)
        except (TypeError, ValueError) as exc:
            raise WorkerRefusal(program, "payload",
                                f"the request is not JSON-serialisable: {exc}") from exc
        except OSError as exc:
            code = self._process.poll()
            self._discard()
            raise WorkerRefusal(program, "died",
                                f"the worker died before the request was sent "
                                f"(exit {code}): {exc}") from exc
        try:
            line = self._lines.get(timeout=timeout)
        except queue.Empty:
            tail = self._tail()
            self._discard()
            raise WorkerRefusal(program, "timeout",
                                f"timed out after {timeout:g} s waiting for a reply; "
                                f"the worker was killed{tail}") from None
        if line is None:
            self._process.wait()
            code, tail = self._process.poll(), self._tail()
            self._discard()
            raise WorkerRefusal(program, "died",
                                f"the worker died without a reply (exit {code}){tail}")
        return self._reply(program, line, sent)

    def _reply(self, program, line, sent):
        """Parse one reply line against the protocol; discard the worker if it is broken."""
        try:
            reply = json.loads(line)
        except ValueError:
            self._discard()
            raise WorkerRefusal(program, "malformed",
                                f"a malformed reply, not JSON: {line.strip()[:200]!r}") from None
        if not isinstance(reply, dict) or not isinstance(reply.get("ok"), bool):
            self._discard()
            raise WorkerRefusal(program, "malformed",
                                f"a malformed reply, not an object with a boolean 'ok': "
                                f"{line.strip()[:200]!r}")
        if reply.get("id") != sent:
            self._discard()
            raise WorkerRefusal(program, "malformed",
                                f"the reply carries id {reply.get('id')!r}, the request "
                                f"was id {sent}")
        if not reply["ok"]:
            error = reply.get("error")
            if not (isinstance(error, dict) and isinstance(error.get("kind"), str)
                    and isinstance(error.get("message"), str)):
                self._discard()
                raise WorkerRefusal(program, "malformed",
                                    f"an error reply without {{kind, message}}: {error!r}")
            raise WorkerRefusal(program, error["kind"], error["message"])
        return reply

    def _record(self, program, obj):
        """Turn the worker's record object into the pyomo pack's :class:`SolveRecord`."""
        names = set(SolveRecord.field_names())
        if (not isinstance(obj, dict) or set(obj) != names
                or not isinstance(obj["binding"], list)
                or not all(isinstance(row, dict) for row in obj["binding"])):
            self._discard()
            missing = sorted(names - set(obj)) if isinstance(obj, dict) else sorted(names)
            raise WorkerRefusal(program, "record",
                                f"the solve record must be an object with exactly "
                                f"{sorted(names)} and a list of binding rows; missing "
                                f"{missing}, got {obj!r:.200}")
        return SolveRecord(**{**obj, "binding": tuple(dict(row) for row in obj["binding"])})


class JumpSolve(Node):
    """The abstract doorway: one JuMP program, solved in the Julia worker, as one Node.

    Subclasses name the Julia program in :attr:`program` and implement exactly two hooks:
    :meth:`payload` (inputs and params in, the JSON-able data the program needs out) and
    :meth:`extract` (the program's solution in, the node's named outputs out, matching the
    subclass's declared ``outputs``). The base's :meth:`run` refuses an undeclared output
    contract or program, builds the payload, solves in the worker (one per node, started
    on first use, reused), records the solve and hands the solution to ``extract``.

    Abstract on both hooks, so it can never register as a kind. Call :meth:`close` (or let
    the node be collected) to stop its worker.

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

    _worker = None

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
        problems = []
        unknown = sorted(set(params) - set(cls._PARAMS))
        if unknown:
            problems.append(f"unknown param(s) {unknown} — allowed: {sorted(cls._PARAMS)}")
        solver = params.get("solver", DEFAULT_SOLVER)
        if not isinstance(solver, str) or not re.match(_SOLVER_NAME_OK, solver):
            problems.append(
                f"solver must be a solver-name string matching {_SOLVER_NAME_OK} "
                f"(e.g. {DEFAULT_SOLVER!r}), got {solver!r}"
            )
        options = params.get("solver_options", {})
        if not isinstance(options, dict):
            problems.append(
                f"solver_options must be a dict of option name -> scalar, got {options!r}"
            )
        else:
            for key in sorted(options, key=repr):
                if not isinstance(key, str) or not key:
                    problems.append(f"solver_options keys must be non-empty strings, got {key!r}")
                elif not _is_scalar(options[key]):
                    problems.append(
                        f"solver_options[{key!r}] must be a scalar "
                        f"(null/bool/finite number/string), got {options[key]!r}"
                    )
        return problems

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
            program, data, self._program_params(), name, self._solver_options())
        self.worker_info = dict(worker.info)
        extracted = self.extract(solution)
        if not isinstance(extracted, dict):
            raise TypeError(
                f"{type(self).__name__}.extract() must return the node's named outputs "
                f"as a dict, got {type(extracted).__name__}"
            )
        return extracted

    def close(self):
        """Stop this node's worker (idempotent); a later run starts a fresh one."""
        worker, self._worker = self._worker, None
        if worker is not None:
            worker.close()

    def _program_params(self):
        """Return the knobs the Julia program receives: every param but the solver's two."""
        return {k: v for k, v in self.params.items() if k not in JumpSolve._PARAMS}

    def _solver_options(self):
        """Return the options handed to the optimizer (a subclass merges defaults UNDER)."""
        return dict(self.params.get("solver_options") or {})

    def _the_worker(self):
        """Return the node's worker, created (not started) on first use."""
        if self._worker is None:
            command = self.worker_command
            self._worker = JuliaWorker(
                JuliaWorker.default_command() if command is None else command,
                timeout=self.request_timeout, start_timeout=self.start_timeout)
        return self._worker


class JumpBudgetedSelect(JumpSolve):
    """The reference subclass: a 0/1 knapsack behind the gate, solved by JuMP.

    The same node as :class:`~dskit.pipeline.libs.pyomo.BudgetedSelect`: inputs
    ``candidates`` (``{id, cost, value}`` rows) and ``survivors`` (the stat_test survivors
    wire), only surviving candidates enter the model, ``params.budget`` is the spend
    ceiling. Two hard guarantees, both refusals by name: an EMPTY gate deploys nothing
    and never starts a worker, and the reported ``outlay`` can never exceed ``budget``.
    Under the default solver the solve is pinned deterministic (one thread, zero MIP gap,
    a fixed seed) for the same tie-flapping reason as the pyomo twin. The Julia program is
    ``budgeted_select``: it receives ``{ids, cost, value}`` and ``{budget}`` and returns
    ``{selected: [ids]}``.

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
        """Return the base's problems plus the budget's (required, finite, > 0).

        Parameters
        ----------
        params : dict
            The node's params from the document.

        Returns
        -------
        list of str
            One problem per defect; empty when well-formed.
        """
        problems = super().validate_params(params)
        if "budget" not in params:
            problems.append(
                "budget is required — the spend ceiling the selection must stay inside "
                "(refused at plan, not after the solve)"
            )
        else:
            budget = params["budget"]
            if (isinstance(budget, bool) or not isinstance(budget, (int, float))
                    or not math.isfinite(budget) or budget <= 0):
                problems.append(f"budget must be a finite number > 0, got {budget!r}")
        return problems

    def _solver_options(self):
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
        survivors = set(inputs["survivors"])
        if not any(row["id"] in survivors for row in inputs["candidates"]):
            # The gate cleared no one: deploy NOTHING and never wake the worker.
            self.solve_record = None
            self.log.info("gate cleared none of %d candidate(s) — zero outlay, "
                          "worker not started", len(inputs["candidates"]))
            return {"positions": [], "outlay": 0.0,
                    "metrics": {"objective": 0.0, "n_selected": 0, "n_eligible": 0,
                                "n_candidates": len(inputs["candidates"])}}
        out = super().run(ctx, inputs)
        if float(out["outlay"]) > budget * (1.0 + 1e-9):
            raise AssertionError(
                f"budget violated: outlay {out['outlay']!r} exceeds budget {budget!r} — "
                "refusing to report an over-budget selection (a budget that does not "
                "bind is the F-220 #1 ghost)"
            )
        return out

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
        survivors = set(inputs["survivors"])
        rows = [row for row in inputs["candidates"] if row["id"] in survivors]
        ids = [row["id"] for row in rows]
        cost = [float(row["cost"]) for row in rows]
        value = [float(row["value"]) for row in rows]
        # Bookkeeping for extract(): what was sent, and how many candidates there were.
        self._select = {"cost": dict(zip(ids, cost)), "value": dict(zip(ids, value)),
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
            When the solve was not optimal, or selected an id that was never sent.
        """
        record = self.solve_record
        termination = None if record is None else str(record.termination)
        if termination is None or termination.lower() != "optimal":
            raise RuntimeError(
                f"solver finished with termination {termination!r}, not 'optimal' — "
                "refusing to read a selection off a non-optimal solve"
            )
        meta = self._select
        chosen = sorted(solution["selected"])
        unknown = [cid for cid in chosen if cid not in meta["cost"]]
        if unknown:
            raise RuntimeError(f"the worker selected ids that were never sent: {unknown}")
        return {
            "positions": chosen,
            "outlay": float(sum(meta["cost"][cid] for cid in chosen)),
            "metrics": {
                "objective": float(sum(meta["value"][cid] for cid in chosen)),
                "n_selected": len(chosen),
                "n_eligible": len(meta["cost"]),
                "n_candidates": meta["n_candidates"],
            },
        }


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
