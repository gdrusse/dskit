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
by every later node with the same command and pool settings. Each solve carries its
node's own timeouts. A worker is stopped by :meth:`JumpSolve.close` (the worker of that
node's command), by a protocol failure (the next solve starts a fresh one), or at
interpreter exit (an ``atexit`` hook closes every worker). The real worker also exits on
stdin EOF, but only an IDLE worker is reading stdin: a Python process killed outright
leaves no Julia behind when its worker is idle, while a worker busy in a solve finishes
that solve first (bounded by the solver's own limits, not by this pack's timeout, which
died with the parent), then finds EOF or a broken stdout and exits. A SIGKILLed parent
runs no ``atexit`` hook. The worker starts in its own process group and a discard
SIGKILLs the whole group, so a launch wrapper that forks rather than execs (GNU
``timeout``, a shell) never leaves the real worker orphaned or holding the pipes; a
wrapper that moves the worker out of that group (``setsid``) is outside this reach.

Live models (ADR-0328 amendment 1)
----------------------------------
A model is built once and changed in place. :meth:`JumpSolve.open` (or
:meth:`JuliaWorker.open`) returns a :class:`LiveModel`, a context manager over one handle
on a model the worker keeps in its pool: ``update`` (new numbers or components in place),
``fix`` / ``free`` (variables addressed by group and key), ``start`` (``"last"`` or values
the caller computed, partial allowed), ``solve`` (per-call limits) and ``close`` (the
model stays pooled). Every model reply carries a :class:`Timings` block beside the
unchanged :class:`~dskit.pipeline.libs.pyomo.SolveRecord`, whose ``path`` (``built``,
``reused``, ``updated``, ``rebuilt``) makes every rebuild visible. A handle whose worker was
killed or restarted refuses as ``stale_handle``, one whose model was evicted as
``evicted``; neither is ever rebuilt behind the caller. Inside :meth:`JumpSolve.run`
re-use is opt-in through ``_PERSISTENT_MODEL_REUSE``, the knob
:class:`~dskit.pipeline.libs.pyomo.ScenarioUtilitySolve` uses for its persistent model; a
node that does not opt in sends exactly the v1 one-shot ``solve``.

Wire protocol (version :data:`PROTOCOL_VERSION`)
-------------------------------------------------
The complete specification, every op, field, type, error kind and the digest's byte
layout, is the "Wire protocol v2" block of ADR-0328 amendment 1 in
``docs/architecture/decision-log.md``; ``tests/pipeline_libs/jump_fake_worker.py`` is its
executable reference. In short: one JSON object per line each way, UTF-8, finite numbers
only (``NaN``/``Infinity`` tokens, a literal that overflows a float, duplicate keys and
nesting past :data:`MAX_JSON_DEPTH` are refused, never parsed). A request carries ``id``
(an int, strictly increasing per worker object) and ``op``; every reply carries the same
``id`` (an int, never a bool) and ``ok`` (a bool), and EXACTLY the keys its kind names
(:data:`_REPLY_KEYS`). ``hello`` sends the pool settings and refuses any protocol but
ours (a v1 worker included) before anything else is sent; the handle-less ``solve`` is v1
unchanged; any refusal by the worker is ``{id, ok: false, error: {kind, message}}``.

Refusal doctrine
----------------
Every failure is a :class:`WorkerRefusal` naming the Julia program and a ``kind``. A
well-formed ``solve`` reply keeps the worker (it answered the protocol, so its state is
consistent), whether it is an ``ok: false`` error reply or an ``ok: true`` reply whose
content the subclass refuses (``termination``, not optimal; ``solution``, not the shape the
program promises). Everything else kills it and the NEXT solve starts a fresh one, never a
silent retry: no start (``start``), a protocol mismatch (``protocol``), any
failed ``hello`` (including a well-formed error reply to it), death (``died``), a reply
past the node's time limit (``timeout``, which also bounds writing the request), a line
that is not the protocol (``malformed``), a mistyped record (``record``, every field
type-checked and finite; an integer too large for a float is not finite), a mistyped
timings block (``timings``), and an exchange interrupted on this side by anything, a
``KeyboardInterrupt`` included (re-raised as it is; the late reply could otherwise be
read as the next request's). A payload that is not JSON (``payload``), limits of the
wrong shape (``limits``) and a closed (``closed``) or stale (``stale_handle``) handle
refuse before anything is sent. The one non-refusal is the pyomo twin's over-budget
assertion (``AssertionError``), kept verbatim.

Role, output, knob and solve-record doctrines are :mod:`~dskit.pipeline.libs.pyomo`'s:
``role = "capital"`` and ``outputs = None`` on the base, a concrete subclass MUST declare
``outputs`` (``run()`` backstops it), ``_PARAMS = ("solver", "solver_options")`` default-
denies by name (the pyomo pack's own rule, :meth:`PyomoSolve.solver_param_problems`), the
solver NAME's shape is checked at plan and its availability in Julia at run (the worker's
``unknown_solver`` refusal), and every solve leaves a
:class:`~dskit.pipeline.libs.pyomo.SolveRecord` (the very class, so the evaluation
``solve`` event has one shape whichever language solved) on ``solve_record``. The hello
versions of the worker that solved are on ``worker_info`` (reset with ``solve_record`` at
the start of every run); writing them into a run's record is not done yet (ADR-0328
decision 5, deferred to the Julia half).

Import cost: stdlib and toolkit only, no Julia and no third-party library, ever.
"""

from __future__ import annotations

import atexit
import collections
import json
import os
import pathlib
import queue
import signal
import subprocess
import threading
import time
from abc import abstractmethod
from dataclasses import dataclass, fields

from dskit.pipeline.libs.pyomo import BudgetedSelect, PyomoSolve, SolveRecord
from dskit.pipeline.node import DEFAULT_NODE_KINDS, Node
from dskit.pipeline.records import number_ok

__all__ = [
    "DEFAULT_POOL_BYTES",
    "DEFAULT_POOL_MODELS",
    "DEFAULT_REQUEST_TIMEOUT",
    "DEFAULT_SHUTDOWN_TIMEOUT",
    "DEFAULT_SOLVER",
    "DEFAULT_START_TIMEOUT",
    "JULIA_EXECUTABLE",
    "JULIA_PACKAGE",
    "LIMIT_KEYS",
    "MAX_JSON_DEPTH",
    "NODE_KINDS",
    "OPS",
    "OPTIMAL_TERMINATION",
    "PATHS",
    "PROTOCOL_VERSION",
    "START_SOURCES",
    "WORKER_ENTRY",
    "JuliaWorker",
    "JumpBudgetedSelect",
    "JumpSolve",
    "LiveModel",
    "Timings",
    "WorkerRefusal",
    "register",
]

#: The wire protocol version both sides must speak; ``hello`` checks it before any solve.
#: Version 2 is ADR-0328 amendment 1 (live models); a version-1 worker is refused.
PROTOCOL_VERSION = 2

#: The operations this pack sends (the protocol's ``search`` is not sent yet).
OPS = ("hello", "describe", "open", "update", "fix", "free", "start", "solve", "close",
       "shutdown")

#: What a timings block's ``path`` may say happened to the model.
PATHS = ("built", "reused", "updated", "rebuilt")

#: The per-solve limits a handle ``solve`` may carry.
LIMIT_KEYS = ("time_limit", "node_limit", "mip_rel_gap", "solution_limit")

#: What a ``start`` request's ``source`` may be (``"none"`` clears every start value).
START_SOURCES = ("last", "values", "none")

#: The worker's default pool caps, sent in ``hello`` (the node knobs
#: ``_PERSISTENT_CACHE_SIZE`` and ``_PERSISTENT_CACHE_BYTES`` default to these). Four
#: models is the pyomo pack's persistent-cache size.
DEFAULT_POOL_MODELS = 4
DEFAULT_POOL_BYTES = 1 << 32

#: Arrays and objects one reply line may nest (the reply object counts as one).
MAX_JSON_DEPTH = 512

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

#: The exact key set of each ``ok: true`` reply, by request op (``handle-solve`` is a
#: ``solve`` that names a handle).
_REPLY_KEYS = {
    "hello": frozenset({"id", "ok", "protocol", "julia", "packages"}),
    "describe": frozenset({"id", "ok", "program", "schema", "variables", "components",
                           "update"}),
    "open": frozenset({"id", "ok", "handle", "signature", "digest", "timings"}),
    "update": frozenset({"id", "ok", "handle", "signature", "digest", "timings"}),
    "fix": frozenset({"id", "ok", "handle", "fixed", "timings"}),
    "free": frozenset({"id", "ok", "handle", "fixed", "timings"}),
    "start": frozenset({"id", "ok", "handle", "started", "timings"}),
    "solve": frozenset({"id", "ok", "solution", "record"}),
    "handle-solve": frozenset({"id", "ok", "handle", "solution", "record", "timings"}),
    "close": frozenset({"id", "ok", "handle", "pooled"}),
    "shutdown": frozenset({"id", "ok"}),
}

#: The exact key set of a timings block's ``pool`` object.
_POOL_KEYS = ("models", "bytes", "evicted", "evictions")

#: The schema's element types and the variable groups' domains (``describe``).
_FIELD_TYPES = frozenset({"float64", "int64", "string", "bool"})
_DOMAINS = frozenset({"binary", "integer", "continuous"})

#: Characters of a lowercase hex sha256 (a data digest).
_DIGEST_CHARS = 64

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
        ``record``, ``timings``, ``limits``, ``closed``, ``stale_handle``,
        ``termination``, ``solution``, or the ``kind`` of a worker's own error reply
        (``evicted``, ``shape_changed``, ``bad_data``, ...; the ADR lists them all).
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


def _finite_float(text):
    """``parse_float`` hook: a literal that overflows a float (``1e999``) is not protocol JSON."""
    value = float(text)
    if value != value or value in (float("inf"), float("-inf")):
        raise ValueError(f"the number {text} overflows a float")
    return value


def _json_depth(obj):
    """Return how many arrays and objects ``obj`` nests (a scalar is 0), without recursion."""
    deepest, stack = 0, [(obj, 1)]
    while stack:
        value, depth = stack.pop()
        if isinstance(value, dict):
            children = value.values()
        elif isinstance(value, list):
            children = value
        else:
            continue
        deepest = max(deepest, depth)
        stack.extend((child, depth + 1) for child in children)
    return deepest


def _strict_loads(line):
    """Parse one reply line strictly (``ValueError``): finite, unique keys, bounded depth."""
    try:
        obj = json.loads(line, parse_constant=_refuse_constant, parse_float=_finite_float,
                         object_pairs_hook=_refuse_duplicates)
    except RecursionError:
        raise ValueError("nested too deep for the parser") from None
    if _json_depth(obj) > MAX_JSON_DEPTH:
        raise ValueError(f"nested deeper than {MAX_JSON_DEPTH} levels")
    return obj


def _is_int(value, low=0):
    """Whether ``value`` is a JSON integer (never a bool) no smaller than ``low``."""
    return isinstance(value, int) and not isinstance(value, bool) and value >= low


def _is_number(value, nullable=False, low=None):
    """Whether ``value`` is a finite JSON number (``records.number_ok``), or null when ``nullable``.

    A JSON integer too large for a float is not a number the toolkit can compute on: it
    answers ``False`` here, never a raw ``OverflowError``.
    """
    if value is None:
        return nullable
    try:
        return number_ok(value) and (low is None or value >= low)
    except OverflowError:
        return False


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


@dataclass(frozen=True)
class Timings:
    """Where one model op's time went and what happened to the model (ADR-0328 A7).

    Kept BESIDE the :class:`~dskit.pipeline.libs.pyomo.SolveRecord`, never in it (that
    record's key set stays pinned to the evaluation ``solve`` event), so build time and
    solve time are separable in every run and a rebuild is never silent.

    Parameters
    ----------
    path : str
        One of :data:`PATHS`: ``built`` (a new model), ``reused`` (taken or used as it
        was), ``updated`` (changed in place), ``rebuilt`` (built again because the
        program has no ``update!`` hook).
    build_seconds, update_seconds, transfer_seconds, solve_seconds : float
        Constructing the model, changing it in place, turning the data into typed values,
        and inside ``optimize!``.
    start_source : str or None
        ``"last"``, ``"values"`` or None: the start in effect after the op.
    start_accepted : bool or None
        Whether the solver reported using the start (solve only); None when it did not
        say, when there was no start, and for every other op. Never inferred.
    pool : dict
        ``models`` and ``bytes`` (the pool after the op), ``evicted`` (by this op) and
        ``evictions`` (since the worker started).

    Examples
    --------
    ::

        timings = Timings(path="built", build_seconds=0.2, update_seconds=0.0,
                          transfer_seconds=0.01, solve_seconds=0.0, start_source=None,
                          start_accepted=None,
                          pool={"models": 1, "bytes": 3000, "evicted": 0, "evictions": 0})
        timings.to_obj()["path"]  # 'built'
    """

    path: str
    build_seconds: float
    update_seconds: float
    transfer_seconds: float
    solve_seconds: float
    start_source: object
    start_accepted: object
    pool: dict

    @classmethod
    def field_names(cls):
        """Return the wire keys of a timings block, in order.

        Returns
        -------
        tuple of str
        """
        return tuple(f.name for f in fields(cls))

    def to_obj(self):
        """Return the timings block as JSON-able data.

        Returns
        -------
        dict
            The wire keys, ``pool`` copied.
        """
        return {name: (dict(self.pool) if name == "pool" else getattr(self, name))
                for name in self.field_names()}


def _timings_problems(obj):
    """Return the problems of a worker's timings block against its wire shape."""
    names = set(Timings.field_names())
    if not isinstance(obj, dict) or set(obj) != names:
        return [f"the timings block must be exactly {sorted(names)}, got "
                f"{obj!r:.{_QUOTE_CHARS}}"]
    problems = []
    if obj["path"] not in PATHS:
        problems.append(f"timings.path must be one of {list(PATHS)}")
    for key in ("build_seconds", "update_seconds", "transfer_seconds", "solve_seconds"):
        if not _is_number(obj[key], low=0):
            problems.append(f"timings.{key} must be a finite number >= 0")
    if obj["start_source"] not in (None, "last", "values"):
        problems.append("timings.start_source must be null, 'last' or 'values'")
    if not (obj["start_accepted"] is None or isinstance(obj["start_accepted"], bool)):
        problems.append("timings.start_accepted must be a bool or null")
    pool = obj["pool"]
    if (not isinstance(pool, dict) or set(pool) != set(_POOL_KEYS)
            or not all(_is_int(pool[k]) for k in _POOL_KEYS)):
        problems.append(f"timings.pool must be exactly {list(_POOL_KEYS)}, integers >= 0")
    return problems


def _names(value):
    """Whether ``value`` is a list of distinct non-empty strings."""
    return (isinstance(value, list) and all(_is_name(v) for v in value)
            and len(set(value)) == len(value))


def _describe_problems(reply, program):
    """Return the problems of an ``ok`` describe reply's body."""
    problems = []
    if reply["program"] != program:
        problems.append(f"program must echo {program!r}")
    schema = reply["schema"]
    if (not isinstance(schema, dict) or set(schema) != {"fields", "dims"}
            or not isinstance(schema["fields"], dict) or not isinstance(schema["dims"], dict)):
        return problems + ["schema must be exactly {fields, dims}, two objects"]
    for name, spec in schema["fields"].items():
        if (not _is_name(name) or not isinstance(spec, dict) or set(spec) != {"type", "dims"}
                or spec["type"] not in _FIELD_TYPES or not _names(spec["dims"])):
            problems.append(f"schema.fields[{name!r}] must be {{type, dims}}")
    for name, label in schema["dims"].items():
        if not _is_name(name) or not (label is None or label in schema["fields"]):
            problems.append(f"schema.dims[{name!r}] must name a field or be null")
    variables = reply["variables"]
    if not isinstance(variables, dict) or not all(
            _is_name(name) and isinstance(spec, dict) and set(spec) == {"index", "domain"}
            and _names(spec["index"]) and spec["domain"] in _DOMAINS
            for name, spec in variables.items()):
        problems.append("variables must map group names to {index, domain}")
    if not _names(reply["components"]):
        problems.append("components must be a list of distinct names")
    if not isinstance(reply["update"], bool):
        problems.append("update must be a bool")
    return problems


def _session_problems(op, reply):
    """Return the problems of an ``ok`` session reply's own fields (handle echo aside)."""
    if op in ("open", "update"):
        digest = reply["digest"]
        ok = (_is_name(reply["signature"]) and isinstance(digest, str)
              and len(digest) == _DIGEST_CHARS and set(digest) <= set("0123456789abcdef"))
        return [] if ok else ["signature must be a name and digest 64 lowercase hex"]
    if op in ("fix", "free") and not _is_int(reply["fixed"]):
        return ["fixed must be an integer >= 0"]
    if op == "start" and not _is_int(reply["started"]):
        return ["started must be an integer >= 0"]
    if op == "close" and not isinstance(reply["pooled"], bool):
        return ["pooled must be a bool"]
    return []


def _limits_problems(limits):
    """Return the problems of a handle solve's ``limits`` (checked before sending)."""
    if not isinstance(limits, dict):
        return [f"limits must be an object over {list(LIMIT_KEYS)}, got {limits!r}"]
    problems = [f"unknown limit {key!r} (known: {list(LIMIT_KEYS)})"
                for key in sorted(set(limits) - set(LIMIT_KEYS), key=str)]
    if "time_limit" in limits and not (_is_number(limits["time_limit"])
                                       and limits["time_limit"] > 0):
        problems.append("time_limit must be a finite number of seconds > 0")
    for key in ("node_limit", "solution_limit"):
        if key in limits and not _is_int(limits[key], low=1):
            problems.append(f"{key} must be an integer >= 1")
    if "mip_rel_gap" in limits and not _is_number(limits["mip_rel_gap"], low=0):
        problems.append("mip_rel_gap must be a finite number >= 0")
    return problems


def _wire_key(key):
    """Return one variable key as its wire list, or None when it is not one."""
    parts = list(key) if isinstance(key, tuple) else [key]
    ok = all(isinstance(p, (str, int)) and not isinstance(p, bool) for p in parts)
    return parts if ok else None


def _wire_variables(values, with_values):
    """Return ``{group: {key: value}}`` (or ``{group: [keys]}``) as the aligned wire form.

    Raises ``ValueError`` naming the defect when the shape is wrong.
    """
    if not isinstance(values, dict):
        raise ValueError(f"variables must be an object of group name to keys, got {values!r}")
    wire = {}
    for group, entries in values.items():
        if not _is_name(group):
            raise ValueError(f"a variable group must be a non-empty string, got {group!r}")
        if with_values and not isinstance(entries, dict):
            raise ValueError(f"{group}: values must be a mapping of key to number")
        if not with_values and not isinstance(entries, (list, tuple)):
            raise ValueError(f"{group}: targets must be a list of keys")
        keys = [_wire_key(key) for key in entries]
        if any(key is None for key in keys):
            raise ValueError(f"{group}: a key is a string, an integer or a tuple of them")
        wire[group] = {"keys": keys}
        if with_values:
            numbers = list(entries.values())
            if not all(_is_number(v) for v in numbers):
                raise ValueError(f"{group}: every value must be a finite number")
            wire[group]["values"] = numbers
    return wire


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


def _check_payload(program, request):
    """Refuse (``payload``) a request that is not JSON with finite numbers, before sending."""
    try:
        json.dumps(request, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise WorkerRefusal(program, "payload",
                            f"the request is not JSON-serialisable: {exc}") from exc


def _kill_group(process):
    """SIGKILL the process group ``process`` leads (it was started in its own session).

    Sent even when the leader has already exited: a wrapper's children may outlive it in
    the group. Linux never hands out a pid still in use as a process-group id, so the
    group named by the leader's pid is ours until it is empty (then ``ESRCH``). Falls back
    to killing the leader alone where the group cannot be signalled.
    """
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        if process.poll() is None:
            process.kill()


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
    settings : dict or None
        The pool caps sent in ``hello``, exactly ``{pool_models, pool_bytes}`` (integers
        >= 1); None means :data:`DEFAULT_POOL_MODELS` and :data:`DEFAULT_POOL_BYTES`.

    Attributes
    ----------
    info : dict or None
        The ``hello`` reply (``protocol``, ``julia``, ``packages``) of the running worker.
    starts : int
        How many processes this object has started; a :class:`LiveModel` is valid only
        while this is the count at its ``open``.

    Examples
    --------
    ::

        worker = JuliaWorker(JuliaWorker.default_command())
        solution, record = worker.solve("budgeted_select", data, {"budget": 10.0},
                                        "HiGHS", {}, timeout=60.0, start_timeout=600.0)
        with worker.open("budgeted_select", data, {"budget": 10.0}, "HiGHS", {},
                         timeout=60.0, start_timeout=600.0) as model:
            model.update(params={"budget": 8.0})
            solution, record = model.solve(limits={"time_limit": 5.0})
        worker.close()
    """

    def __init__(self, command, *, shutdown_timeout=DEFAULT_SHUTDOWN_TIMEOUT, settings=None):
        self.command = [str(part) for part in command]
        self.shutdown_timeout = float(shutdown_timeout)
        self.settings = dict(settings or {"pool_models": DEFAULT_POOL_MODELS,
                                          "pool_bytes": DEFAULT_POOL_BYTES})
        self.info = None
        self.starts = 0
        self._process = None
        self._lines = None
        self._stderr = None
        self._stdout_thread = None
        self._stderr_thread = None
        self._writer_thread = None
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
        _check_payload(program, body)
        with self._lock:
            self._ensure_started(program, float(start_timeout))
            reply = self._exchange(program, body, float(timeout))
            return reply["solution"], self._record(program, reply["record"], solver)

    def describe(self, program, *, timeout=DEFAULT_REQUEST_TIMEOUT,
                 start_timeout=DEFAULT_START_TIMEOUT):
        """Return a program's declared contract: schema, variable groups, components.

        Parameters
        ----------
        program : str
            The Julia program's registry name.
        timeout, start_timeout : float
            As :meth:`solve`.

        Returns
        -------
        dict
            ``program``, ``schema`` (``{fields, dims}``), ``variables``, ``components``
            and ``update`` (whether the program changes a model in place).

        Raises
        ------
        WorkerRefusal
            As :meth:`solve`; a mistyped body is ``malformed`` and kills the worker.
        """
        with self._lock:
            self._ensure_started(program, float(start_timeout))
            reply = self._exchange(program, {"op": "describe", "program": program},
                                   float(timeout))
            problems = _describe_problems(reply, program)
            if problems:
                raise self._malformed(program, "; ".join(problems), json.dumps(reply))
            return {key: reply[key] for key in _REPLY_KEYS["describe"] - {"id", "ok"}}

    def open(self, program, data, params, solver, solver_options, components=None, *,
             timeout=DEFAULT_REQUEST_TIMEOUT, start_timeout=DEFAULT_START_TIMEOUT):
        """Open a live model: build it, or take the pooled one of the same shape.

        Parameters
        ----------
        program : str
            The Julia program's registry name.
        data : dict
            Every field of the program's schema.
        params : dict
            The program's own knobs.
        solver : str
            The optimizer name.
        solver_options : dict
            Flat option mapping handed to the optimizer verbatim.
        components : dict or None
            Optional constraint group name to bool (attached); unnamed ones are detached.
        timeout : float
            Seconds each request on the model may take.
        start_timeout : float
            Seconds a start and its ``hello`` may take, when a start is needed.

        Returns
        -------
        LiveModel
            The handle, its ``timings`` saying whether the model was built or reused.

        Raises
        ------
        WorkerRefusal
            As :meth:`solve`.
        """
        body = {"op": "open", "program": program, "data": data, "params": params,
                "solver": solver, "solver_options": solver_options,
                "components": dict(components or {})}
        _check_payload(program, body)
        with self._lock:
            self._ensure_started(program, float(start_timeout))
            reply = self._exchange(program, body, float(timeout))
            if not _is_name(reply["handle"]):
                raise self._malformed(program, "an open reply naming no handle",
                                      json.dumps(reply))
            self._session_checks(program, "open", reply)
            return LiveModel(self, self.starts, program, solver, reply, float(timeout))

    def _session(self, model, op, body, check_payload=True):
        """Send one op on ``model``'s handle; return the validated ``ok`` reply."""
        program = model.program
        if model.closed:
            raise WorkerRefusal(program, "closed",
                                f"handle {model.handle!r} was closed; open a new model")
        request = {"op": op, "handle": model.handle, **body}
        if check_payload:
            _check_payload(program, request)
        with self._lock:
            if self.starts != model.generation or self._process is None or not self.info:
                raise WorkerRefusal(
                    program, "stale_handle",
                    f"handle {model.handle!r} belongs to a worker that has since been "
                    "killed or restarted; it is never rebuilt behind the caller: open a "
                    "new model")
            expect = "handle-solve" if op == "solve" else op
            reply = self._exchange(program, request, model.timeout, expect=expect)
            if reply["handle"] != model.handle:
                raise self._malformed(program, f"a reply for handle {reply['handle']!r}, "
                                      f"the request named {model.handle!r}",
                                      json.dumps(reply))
            self._session_checks(program, expect, reply)
            if expect == "handle-solve":
                reply["record"] = self._record(program, reply["record"], model.solver)
            return reply

    def _session_checks(self, program, op, reply):
        """Kill the worker on a mistyped session body (``malformed``) or timings (``timings``)."""
        problems = _session_problems(op, reply)
        if problems:
            raise self._malformed(program, "; ".join(problems), json.dumps(reply))
        if "timings" in reply:
            problems = _timings_problems(reply["timings"])
            if problems:
                self._discard()
                raise WorkerRefusal(program, "timings", "; ".join(problems))

    def _record(self, program, record, solver):
        """Return the :class:`SolveRecord` of a reply's record, or kill the worker (``record``)."""
        problems = _record_problems(record, solver)
        if problems:
            self._discard()
            raise WorkerRefusal(program, "record", "; ".join(problems))
        return SolveRecord(**{**record, "binding": tuple(dict(row) for row in record["binding"])})

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
                bufsize=1, start_new_session=True,
            )
        except OSError as exc:
            raise WorkerRefusal(program, "start",
                                f"cannot start the worker {self.command!r}: {exc}") from exc
        self.starts += 1
        self._process = process
        self._lines = queue.Queue()
        self._stderr = collections.deque(maxlen=_STDERR_TAIL_CHARS)
        self._stdout_thread = threading.Thread(
            target=_read_lines, args=(process.stdout, self._lines), daemon=True)
        self._stdout_thread.start()
        self._stderr_thread = threading.Thread(
            target=_keep_tail, args=(process.stderr, self._stderr), daemon=True)
        self._stderr_thread.start()
        try:
            reply = self._exchange(program, {"op": "hello", "settings": self.settings},
                                   start_timeout)
        except WorkerRefusal:
            self._discard()  # a worker that failed hello is never used, even if it lives
            raise
        self.info = {key: reply[key] for key in ("protocol", "julia", "packages")}

    def _discard(self):
        """Kill the worker's whole process group, reap it, drop its pipes; return the stderr tail.

        The worker starts in its own process group (``start_new_session``), so a launch
        wrapper that forks rather than execs (GNU ``timeout``, a shell) dies with the real
        worker it started: no grandchild is orphaned and none keeps the pipes open. A
        stream is closed only once the thread servicing it has ended; a thread still
        blocked on it (a process outside the group holding the pipe) is left to the
        daemon, never waited on, so a discard is always bounded.
        """
        process, self._process = self._process, None
        self.info = None
        if process is None:
            return ""
        try:
            _kill_group(process)
            process.wait()
        finally:
            deadline = time.monotonic() + _STDERR_JOIN_SECONDS
            for thread, stream in ((self._writer_thread, process.stdin),
                                   (self._stdout_thread, process.stdout),
                                   (self._stderr_thread, process.stderr)):
                if thread is not None:
                    thread.join(timeout=max(0.0, deadline - time.monotonic()))
                if thread is not None and thread.is_alive():
                    continue
                try:
                    stream.close()
                except (OSError, ValueError):
                    pass
            self._writer_thread = None
        tail = "".join(self._stderr or ()).strip()
        return f" | stderr tail: {tail[-_STDERR_TAIL_CHARS:]}" if tail else ""

    # -- one request/reply --------------------------------------------------

    def _exchange(self, program, request, timeout, expect=None):
        """Send ``request``, return its validated ``ok`` reply, or refuse naming ``program``.

        One deadline covers writing the request and reading the reply. ``expect`` names
        the reply's kind in :data:`_REPLY_KEYS` (default the request's op). An exchange
        interrupted by anything that is not a refusal (a ``KeyboardInterrupt`` included)
        discards the worker before the exception goes on: a reply may still be coming,
        and it would be read as the next request's.
        """
        try:
            return self._exchange_once(program, request, timeout, expect or request["op"])
        except WorkerRefusal:
            raise
        except BaseException:
            self._discard()
            raise

    def _exchange_once(self, program, request, timeout, expect):
        """One write and one read of :meth:`_exchange`, refusals included."""
        deadline = time.monotonic() + timeout
        self._next_id += 1
        sent = self._next_id
        line = json.dumps({"id": sent, **request}, allow_nan=False) + "\n"
        errors = []
        writer = threading.Thread(target=_write_line,
                                  args=(self._process.stdin, line, errors), daemon=True)
        self._writer_thread = writer
        writer.start()
        writer.join(timeout=max(0.0, deadline - time.monotonic()))
        if writer.is_alive():
            tail = self._discard()  # the killed group's pipe breaks and frees the writer
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
        return self._reply(program, expect, reply_line, sent)

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


class LiveModel:
    """One handle on a model the Julia worker keeps live: change it in place, solve it again.

    Made by :meth:`JuliaWorker.open` or :meth:`JumpSolve.open`, never directly. Every op
    refuses (:class:`WorkerRefusal`) rather than rebuild behind the caller: ``closed``
    after :meth:`close`, ``stale_handle`` once the worker that issued the handle has been
    killed or restarted (nothing is sent), ``evicted`` when the worker's pool evicted the
    model. A context manager: leaving the block closes the handle (the model stays in the
    worker's pool for the next ``open`` of the same shape).

    Parameters
    ----------
    worker : JuliaWorker
        The worker that issued the handle.
    generation : int
        ``worker.starts`` when the handle was issued.
    program, solver : str
        The program and the optimizer the model was opened with.
    reply : dict
        The ``open`` reply: ``handle``, ``signature``, ``digest``, ``timings``.
    timeout : float
        Seconds each later request on the handle may take.

    Attributes
    ----------
    handle, signature, digest : str
        The worker's handle, the model's shape key and the digest of its current data.
    timings : Timings
        The timings block of the last op.
    solve_record : SolveRecord or None
        The record of the last :meth:`solve`.
    closed : bool
        Whether :meth:`close` ran.

    Examples
    --------
    ::

        node = JumpBudgetedSelect("select", {"budget": 10.0})
        with node.open(inputs) as model:
            model.timings.path  # 'built' (or 'reused' from the pool)
            model.fix({"select": {"ALPHA": 1}})
            model.start({"select": {"BRAVO": 1}})
            solution, record = model.solve(limits={"time_limit": 5.0})
            model.update(params={"budget": 8.0})
            model.timings.path  # 'updated'
    """

    def __init__(self, worker, generation, program, solver, reply, timeout):
        self._worker = worker
        self.generation = generation
        self.program = program
        self.solver = solver
        self.handle = reply["handle"]
        self.signature = reply["signature"]
        self.digest = reply["digest"]
        self.timings = Timings(**reply["timings"])
        self.timeout = timeout
        self.solve_record = None
        self.closed = False

    def _op(self, op, body, check_payload=True):
        """Send ``op`` on this handle, keep its timings, return the reply."""
        reply = self._worker._session(self, op, body, check_payload)
        if "timings" in reply:
            self.timings = Timings(**reply["timings"])
        return reply

    def _variables(self, values, with_values):
        """Return the wire form of ``values``, refusing a bad shape (``payload``)."""
        try:
            return _wire_variables(values, with_values)
        except ValueError as exc:
            raise WorkerRefusal(self.program, "payload", str(exc)) from None

    def update(self, data=None, params=None, components=None):
        """Change the live model in place: new numbers, params or components.

        Parameters
        ----------
        data : dict or None
            Any subset of the schema's fields (None changes none); a change of shape is
            the worker's ``shape_changed`` refusal, never a rebuild.
        params : dict or None
            The program's params, replacing the current ones; None keeps them.
        components : dict or None
            Component name to bool (attach or detach); only the named ones change.

        Returns
        -------
        str
            The new data digest (also on :attr:`digest`); :attr:`timings` says
            ``updated``, ``reused`` (nothing changed) or ``rebuilt`` (no ``update!``).
        """
        reply = self._op("update", {"data": dict(data or {}), "params": params,
                                    "components": dict(components or {})})
        self.signature, self.digest = reply["signature"], reply["digest"]
        return self.digest

    def fix(self, values):
        """Fix variables at values: ``{group: {key: value}}``.

        Parameters
        ----------
        values : dict
            Group name to a mapping of key (a label, a position, or a tuple of them for a
            group over several dimensions) to a finite number.

        Returns
        -------
        int
            How many variables are fixed on the model now.
        """
        return self._op("fix", {"values": self._variables(values, True)})["fixed"]

    def free(self, targets="all"):
        """Release fixed variables: ``"all"`` or ``{group: [keys]}``.

        Parameters
        ----------
        targets : str or dict
            ``"all"``, or group name to a list of keys.

        Returns
        -------
        int
            How many variables are still fixed.
        """
        wire = "all" if targets == "all" else self._variables(targets, False)
        return self._op("free", {"targets": wire})["fixed"]

    def start(self, source):
        """Set the start values of the next solves.

        Parameters
        ----------
        source : str, dict or None
            ``"last"`` (the model's own last feasible solution), ``{group: {key:
            value}}`` (values the caller computed; a partial start is allowed, the rest
            carry none), or None (clear every start value).

        Returns
        -------
        int
            How many variables carry a start value now.
        """
        if source is None:
            body = {"source": "none", "values": {}}
        elif source == "last":
            body = {"source": "last", "values": {}}
        else:
            body = {"source": "values", "values": self._variables(source, True)}
        return self._op("start", body)["started"]

    def solve(self, limits=None):
        """Solve the live model under per-call limits.

        Parameters
        ----------
        limits : dict or None
            Any of :data:`LIMIT_KEYS` (``time_limit`` seconds > 0, ``node_limit`` and
            ``solution_limit`` integers >= 1, ``mip_rel_gap`` >= 0), for this solve only.

        Returns
        -------
        tuple
            ``(solution, record)``; the record is also on :attr:`solve_record` and the
            timings (``start_accepted`` among them) on :attr:`timings`.

        Raises
        ------
        WorkerRefusal
            Kind ``limits`` (nothing sent) for limits of the wrong shape; as
            :meth:`JuliaWorker.solve` otherwise.
        """
        limits = {} if limits is None else limits
        problems = _limits_problems(limits)
        if problems:
            raise WorkerRefusal(self.program, "limits", "; ".join(problems))
        reply = self._op("solve", {"limits": dict(limits)})
        self.solve_record = reply["record"]
        return reply["solution"], self.solve_record

    def close(self):
        """Release the handle (idempotent); the model stays in the worker's pool.

        A handle whose worker is gone is closed locally, nothing sent.

        Returns
        -------
        bool
            Whether the model stayed pooled (False when it was evicted or the worker is
            gone).
        """
        if self.closed:
            return False
        try:
            return self._op("close", {}, check_payload=False)["pooled"]
        except WorkerRefusal as refusal:
            if refusal.kind != "stale_handle":
                raise
            return False
        finally:
            self.closed = True

    def __enter__(self):
        """Return self."""
        return self

    def __exit__(self, exc_type, exc, tb):
        """Close the handle; a close refusal never masks the block's own exception."""
        try:
            self.close()
        except WorkerRefusal:
            if exc_type is None:
                raise


class _WorkerRegistry:
    """The process's workers, one per launch command and pool settings (see the module)."""

    def __init__(self):
        self._workers = {}
        self._lock = threading.Lock()

    @staticmethod
    def _key(command, settings):
        """Return the registry key of ``command`` under ``settings``."""
        return (tuple(str(part) for part in command), tuple(sorted(settings.items())))

    def worker(self, command, settings=None):
        """Return the worker of ``command`` and ``settings``, created (not started) on first use."""
        settings = dict(settings or {"pool_models": DEFAULT_POOL_MODELS,
                                     "pool_bytes": DEFAULT_POOL_BYTES})
        key = self._key(command, settings)
        with self._lock:
            if key not in self._workers:
                self._workers[key] = JuliaWorker(key[0], settings=settings)
            return self._workers[key]

    def live(self):
        """Return the launch commands whose worker has a running process, sorted."""
        with self._lock:
            return sorted(key[0] for key, worker in self._workers.items()
                          if worker.pid is not None)

    def close(self, command, settings=None):
        """Shut down and forget the worker of ``command`` (every settings when None)."""
        command = tuple(str(part) for part in command)
        with self._lock:
            keys = [key for key in self._workers if key[0] == command
                    and (settings is None or key == self._key(command, settings))]
            workers = [self._workers.pop(key) for key in keys]
        for worker in workers:
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

    #: The ``(op, Timings)`` pairs of the last run's model ops, ``()`` for a one-shot
    #: solve (whose v1 reply carries none); kept beside ``solve_record``, never in it.
    timings = ()

    #: Re-use of live models inside :meth:`run` is opt-in, under the knob name
    #: :class:`~dskit.pipeline.libs.pyomo.ScenarioUtilitySolve` uses for its persistent
    #: model: ``True`` opens a model (the worker's pool hands back the one of the same
    #: shape, changed in place), solves it and closes the handle, so each shape is built
    #: once per worker; ``False`` sends the v1 one-shot solve, exactly as before. The two
    #: caps are the worker's pool settings (sent in ``hello``; workers with other
    #: settings are other workers): models, as the pyomo cache size, and estimated bytes.
    _PERSISTENT_MODEL_REUSE = False
    _PERSISTENT_CACHE_SIZE = DEFAULT_POOL_MODELS
    _PERSISTENT_CACHE_BYTES = DEFAULT_POOL_BYTES

    @classmethod
    def persistent_problems(cls):
        """Return the problems of the class's re-use knobs (checked before any worker starts).

        Returns
        -------
        list of str
            One problem per defect: ``_PERSISTENT_MODEL_REUSE`` must be a bool, the two
            caps integers >= 1.
        """
        problems = []
        if not isinstance(cls._PERSISTENT_MODEL_REUSE, bool):
            problems.append(f"_PERSISTENT_MODEL_REUSE must be a bool, got "
                            f"{cls._PERSISTENT_MODEL_REUSE!r}")
        for name in ("_PERSISTENT_CACHE_SIZE", "_PERSISTENT_CACHE_BYTES"):
            if not _is_int(getattr(cls, name), low=1):
                problems.append(f"{name} must be an integer >= 1, got {getattr(cls, name)!r}")
        return problems

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
        program = self._program_name()
        self.solve_record = None
        self.worker_info = None
        self.timings = ()
        worker = self._the_worker()
        data = self.payload(inputs, self.params)
        name = self.params.get("solver", DEFAULT_SOLVER)
        self.log.info("solving %r with %r", program, name)
        if self._PERSISTENT_MODEL_REUSE:
            with self._open_data(worker, data) as model:
                opened = model.timings
                self.log.info("model %s for %r (handle %s)", opened.path, program,
                              model.handle)
                solution, self.solve_record = model.solve()
                self.timings = (("open", opened), ("solve", model.timings))
        else:
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

    def describe(self):
        """Return the program's declared contract from the worker (see :meth:`JuliaWorker.describe`).

        Returns
        -------
        dict
            ``program``, ``schema``, ``variables``, ``components``, ``update``.
        """
        return self._the_worker().describe(self._program_name(), timeout=self.request_timeout,
                                           start_timeout=self.start_timeout)

    def open(self, inputs, components=None):
        """Open a live model of these inputs (built, or the pooled one of the same shape).

        Parameters
        ----------
        inputs : dict
            The node's inputs; :meth:`payload` turns them into the program's data.
        components : dict or None
            Optional constraint group name to bool (attached).

        Returns
        -------
        LiveModel
            A context manager; its ``timings.path`` says how the model was obtained.
        """
        worker = self._the_worker()
        return self._open_data(worker, self.payload(inputs, self.params), components)

    def close(self):
        """Stop the worker of this node's launch command (idempotent); it restarts on use."""
        _POOL.close(self._command(), self._settings())

    def _open_data(self, worker, data, components=None):
        """Open a live model of the program's ``data`` on ``worker``."""
        return worker.open(self._program_name(), data, self._program_params(),
                           self.params.get("solver", DEFAULT_SOLVER), self._solver_options(),
                           components, timeout=self.request_timeout,
                           start_timeout=self.start_timeout)

    def _program_name(self):
        """Return the declared program, refusing a subclass that names none (``TypeError``)."""
        program = type(self).program
        if not isinstance(program, str) or not program:
            raise TypeError(
                f"{type(self).__name__} names no Julia program — a concrete JumpSolve "
                "subclass must set `program = \"<registry name>\"`"
            )
        return program

    def _settings(self):
        """Return the worker's pool settings from the class knobs, refusing bad ones."""
        problems = self.persistent_problems()
        if problems:
            raise TypeError(f"{type(self).__name__}: " + "; ".join(problems))
        return {"pool_models": self._PERSISTENT_CACHE_SIZE,
                "pool_bytes": self._PERSISTENT_CACHE_BYTES}

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
        """Return the process's worker for this node's command and pool settings."""
        return _POOL.worker(self._command(), self._settings())


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
        WorkerRefusal
            Kind ``termination`` when the solve was not optimal; kind ``solution`` when
            the solution is not ``{"selected": [ids]}`` over distinct ids that were sent.
            Both judge a well-formed reply, so the worker is kept.
        """
        record = self.solve_record
        termination = None if record is None else record.termination
        if termination != OPTIMAL_TERMINATION:
            raise WorkerRefusal(
                self.program, "termination",
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
