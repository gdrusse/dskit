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

The data contract (ADR-0328 A4)
-------------------------------
Julia owns each program's schema (field name, element type, shape in named
dimensions). Python reads it through ``describe``, once per worker process and program,
and never keeps a schema of its own: :class:`DataContract` wraps the worker's reply.
Every ``open`` and ``update`` is checked against it BEFORE anything is sent (an unknown
or missing field, a wrong element type, a ragged value, dimensions that disagree across
fields, repeated labels: each refuses as ``bad_data`` naming the program and the field;
a non-finite number refuses as ``payload`` naming the field, before any worker starts),
and the worker checks again. Small values travel as JSON; an array of more than the
worker's ``arrow_threshold`` elements travels as an Arrow IPC file in the worker's own
scratch directory (under ``scratch_root``), referenced by path and sha256, removed as
soon as its request is answered or refused, and the directory with the worker. pyarrow
is imported only there, on the run path; without it such an array refuses as ``arrow``,
naming the extra to install. Every ``open`` and ``update`` reply carries the digest of
the data the worker applied, and :meth:`DataContract.digest` recomputes it from what was
sent: a mismatch is the refusal ``digest`` and kills the worker. The v1 one-shot
``solve`` is unchanged: JSON only, no contract.

Search (ADR-0328 A6)
--------------------
:meth:`LiveModel.search` runs a search algorithm REGISTERED IN THE WORKER on the live
model and returns the incumbent, its record and the per-round trajectory, each field
checked like every other reply. Python contains no search algorithm.

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
timings block (``timings``), a data digest other than that of the data sent (``digest``),
a mistyped search trajectory (``trajectory``), and an exchange interrupted on this side by anything, a
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

Import cost: stdlib and toolkit only, no Julia, ever; the one third-party library,
pyarrow, is imported inside the Arrow writer and only when an array crosses the threshold.
"""

from __future__ import annotations

import atexit
import collections
import contextlib
import hashlib
import json
import math
import os
import pathlib
import queue
import re
import shutil
import signal
import struct
import subprocess
import tempfile
import threading
import time
import weakref
from abc import abstractmethod
from dataclasses import dataclass, fields

from dskit.pipeline.libs.pyomo import BudgetedSelect, PyomoSolve, SolveRecord
from dskit.pipeline.node import DEFAULT_NODE_KINDS, Node
from dskit.pipeline.records import number_ok

__all__ = [
    "ARROW_EXTRA",
    "DEFAULT_ARROW_THRESHOLD",
    "DEFAULT_POOL_BYTES",
    "DEFAULT_POOL_MODELS",
    "DEFAULT_REQUEST_TIMEOUT",
    "DEFAULT_SCRATCH_ROOT",
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
    "SCRATCH_PREFIX",
    "SEARCH_LIMIT_KEYS",
    "SEED_BOUND",
    "START_SOURCES",
    "WORKER_ENTRY",
    "DataContract",
    "JuliaWorker",
    "JumpBudgetedSelect",
    "JumpSolve",
    "LiveModel",
    "SearchRound",
    "Timings",
    "WorkerRefusal",
    "register",
]

#: The wire protocol version both sides must speak; ``hello`` checks it before any solve.
#: Version 2 is ADR-0328 amendment 1 (live models); a version-1 worker is refused.
PROTOCOL_VERSION = 2

#: The operations this pack sends: every op of the protocol.
OPS = ("hello", "describe", "open", "update", "fix", "free", "start", "solve", "search",
       "close", "shutdown")

#: What a timings block's ``path`` may say happened to the model.
PATHS = ("built", "reused", "updated", "rebuilt")

#: The per-solve limits a handle ``solve`` may carry.
LIMIT_KEYS = ("time_limit", "node_limit", "mip_rel_gap", "solution_limit")

#: What a ``start`` request's ``source`` may be (``"none"`` clears every start value).
START_SOURCES = ("last", "values", "none")

#: The overall limits a ``search`` may carry (its rounds take :data:`LIMIT_KEYS`).
SEARCH_LIMIT_KEYS = ("time_limit", "rounds")

#: A search ``seed`` is an integer in ``[0, SEED_BOUND)``, exact in a float64 (Julia
#: and JSON parsers agree on every such integer).
SEED_BOUND = 1 << 53

#: Elements above which one array of an ``open``/``update`` travels as an Arrow file
#: instead of JSON (a worker setting; the node knob ``arrow_threshold`` defaults to it).
DEFAULT_ARROW_THRESHOLD = 1 << 16

#: Where a worker's scratch directory is made; ``None`` is the system temporary
#: directory (``tempfile.gettempdir()``). The node knob ``scratch_root`` defaults to it.
DEFAULT_SCRATCH_ROOT = None

#: The name prefix of every worker's scratch directory (one per worker process).
SCRATCH_PREFIX = "dskit-jump-"

#: The dskit extra that installs pyarrow, named in the refusal when it is missing.
ARROW_EXTRA = "parquet"

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
    "search": frozenset({"id", "ok", "handle", "solution", "record", "trajectory",
                         "timings"}),
    "close": frozenset({"id", "ok", "handle", "pooled"}),
    "shutdown": frozenset({"id", "ok"}),
}

#: The reply kinds that run the solver (``solve_seconds`` and ``start_accepted`` apply).
_SOLVING_REPLIES = frozenset({"handle-solve", "search"})

#: A handle: ``h`` and a decimal counter from 1 (the wire spec).
_HANDLE = re.compile(r"h[1-9][0-9]*")

#: The exact key set of a timings block's ``pool`` object.
_POOL_KEYS = ("models", "bytes", "evicted", "evictions")

#: The schema's element types, each with its one-byte digest tag (the wire spec's
#: byte layout), and the variable groups' domains (``describe``).
_TYPE_TAGS = {"float64": b"f", "int64": b"i", "string": b"s", "bool": b"b"}
_FIELD_TYPES = frozenset(_TYPE_TAGS)
_DOMAINS = frozenset({"binary", "integer", "continuous"})

#: The element types a label field may have.
_LABEL_TYPES = frozenset({"string", "int64"})

#: The first bytes of the canonical data digest's input (the wire spec).
_DIGEST_MAGIC = b"dskit-data-v1\n"

#: The closed range of an ``int64`` element.
_INT64_MIN, _INT64_MAX = -(1 << 63), (1 << 63) - 1

#: The one column of an Arrow handoff file.
_ARROW_COLUMN = "values"

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
        ``record``, ``timings``, ``trajectory``, ``digest``, ``limits``, ``arrow``,
        ``closed``, ``stale_handle``, ``termination``, ``solution``, ``bad_data`` (also
        raised here, before sending), or the ``kind`` of a worker's own error reply
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
        self.message = message


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


def _timings_rule_problems(op, obj):
    """Return the breaches of the spec's cross-field timings rules for reply kind ``op``.

    ``obj`` is a well-typed timings block. The rules: ``path`` is ``reused`` for every
    op but ``open`` and ``update`` (and an ``update`` never ``built``); ``build_seconds``
    is 0 unless ``built`` or ``rebuilt``; ``solve_seconds`` is 0 and ``start_accepted``
    null for every op but a solve or a search; ``start_accepted`` is null when no start
    is in effect; only an ``open`` evicts, and never more than the evictions so far.
    """
    problems = []
    path = obj["path"]
    if op not in ("open", "update") and path != "reused":
        problems.append(f"timings.path must be 'reused' for {op}, got {path!r}")
    if op == "update" and path == "built":
        problems.append("timings.path of an update is never 'built'")
    if path not in ("built", "rebuilt") and obj["build_seconds"] != 0:
        problems.append(f"timings.build_seconds must be 0 when the path is {path!r}")
    solving = op in _SOLVING_REPLIES
    if not solving and obj["solve_seconds"] != 0:
        problems.append(f"timings.solve_seconds must be 0 for {op}")
    if obj["start_accepted"] is not None and (not solving or obj["start_source"] is None):
        problems.append("timings.start_accepted must be null but for a solve or a search "
                        "with a start in effect")
    pool = obj["pool"]
    if op != "open" and pool["evicted"] != 0:
        problems.append(f"timings.pool.evicted must be 0 for {op}: only an open evicts")
    if pool["evicted"] > pool["evictions"]:
        problems.append("timings.pool.evicted exceeds the evictions since the worker started")
    return problems


@dataclass(frozen=True)
class SearchRound:
    """One round of a worker-side search (ADR-0328 A6): a row of the trajectory.

    Parameters
    ----------
    round : int
        1, 2, ... in order.
    objective, bound : float or None
        The round's solve, None when it found no point (or no bound).
    seconds : float
        The round's wall time.
    accepted : bool
        Whether the search took the round's point as its new incumbent.
    neighbourhood : int
        How many variables the round freed.

    Examples
    --------
    ::

        row = SearchRound(round=1, objective=12.0, bound=12.0, seconds=0.01,
                          accepted=True, neighbourhood=2)
        row.to_obj()["accepted"]  # True
    """

    round: int
    objective: object
    bound: object
    seconds: float
    accepted: bool
    neighbourhood: int

    @classmethod
    def field_names(cls):
        """Return the wire keys of a trajectory row, in order.

        Returns
        -------
        tuple of str
        """
        return tuple(f.name for f in fields(cls))

    def to_obj(self):
        """Return the row as JSON-able data.

        Returns
        -------
        dict
            The wire keys.
        """
        return {name: getattr(self, name) for name in self.field_names()}


def _trajectory_problems(rows, rounds):
    """Return the problems of a search reply's trajectory; ``rounds`` is the request's cap."""
    if not isinstance(rows, list):
        return [f"the trajectory must be a list of rows, got {rows!r:.{_QUOTE_CHARS}}"]
    names = set(SearchRound.field_names())
    problems = []
    if rounds is not None and len(rows) > rounds:
        problems.append(f"the trajectory has {len(rows)} rounds, the request allowed {rounds}")
    for i, row in enumerate(rows):
        if not isinstance(row, dict) or set(row) != names:
            problems.append(f"trajectory[{i}] must be exactly {sorted(names)}")
            continue
        if not _is_int(row["round"]) or row["round"] != i + 1:
            problems.append(f"trajectory[{i}].round must be the integer {i + 1}")
        for key in ("objective", "bound"):
            if not _is_number(row[key], nullable=True):
                problems.append(f"trajectory[{i}].{key} must be a finite number or null")
        if not _is_number(row["seconds"], low=0):
            problems.append(f"trajectory[{i}].seconds must be a finite number >= 0")
        if not isinstance(row["accepted"], bool):
            problems.append(f"trajectory[{i}].accepted must be a bool")
        if not _is_int(row["neighbourhood"]):
            problems.append(f"trajectory[{i}].neighbourhood must be an integer >= 0")
    return problems


def _names(value):
    """Whether ``value`` is a list of distinct non-empty strings."""
    return (isinstance(value, list) and all(_is_name(v) for v in value)
            and len(set(value)) == len(value))


def _one_of(value, allowed):
    """Whether ``value`` is a string in ``allowed`` (an unhashable value is simply not)."""
    return isinstance(value, str) and value in allowed


def _describe_problems(reply, program):
    """Return the problems of an ``ok`` describe reply's body (any JSON value: never raises)."""
    problems = []
    if reply["program"] != program:
        problems.append(f"program must echo {program!r}")
    schema = reply["schema"]
    if (not isinstance(schema, dict) or set(schema) != {"fields", "dims"}
            or not isinstance(schema["fields"], dict) or not isinstance(schema["dims"], dict)):
        return problems + ["schema must be exactly {fields, dims}, two objects"]
    for name, spec in schema["fields"].items():
        if (not _is_name(name) or not isinstance(spec, dict) or set(spec) != {"type", "dims"}
                or not _one_of(spec["type"], _FIELD_TYPES) or not _names(spec["dims"])):
            problems.append(f"schema.fields[{name!r}] must be {{type, dims}}")
    for name, label in schema["dims"].items():
        spec = schema["fields"].get(label) if isinstance(label, str) else None
        if not _is_name(name) or not (label is None or (
                isinstance(spec, dict) and spec.get("dims") == [name]
                and _one_of(spec.get("type"), _LABEL_TYPES))):
            problems.append(f"schema.dims[{name!r}] must be null or name a one-dimensional "
                            f"string or int64 field over {name!r}")
    used = {dim for spec in schema["fields"].values() if isinstance(spec, dict)
            and _names(spec.get("dims")) for dim in spec["dims"]}
    undeclared = sorted(used - set(schema["dims"]), key=str)
    if undeclared:
        problems.append(f"schema.dims must declare every dimension a field uses; missing "
                        f"{undeclared}")
    variables = reply["variables"]
    if not isinstance(variables, dict) or not all(
            _is_name(name) and isinstance(spec, dict) and set(spec) == {"index", "domain"}
            and _names(spec["index"]) and _one_of(spec["domain"], _DOMAINS)
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


def _search_limits_problems(limits):
    """Return the problems of a search's overall ``limits`` (checked before sending)."""
    if not isinstance(limits, dict):
        return [f"limits must be an object over {list(SEARCH_LIMIT_KEYS)}, got {limits!r}"]
    problems = [f"unknown search limit {key!r} (known: {list(SEARCH_LIMIT_KEYS)})"
                for key in sorted(set(limits) - set(SEARCH_LIMIT_KEYS), key=str)]
    if "time_limit" in limits and not (_is_number(limits["time_limit"])
                                       and limits["time_limit"] > 0):
        problems.append("time_limit must be a finite number of seconds > 0")
    if "rounds" in limits and not _is_int(limits["rounds"], low=1):
        problems.append("rounds must be an integer >= 1")
    return problems


# -- the data contract (ADR-0328 A4) ---------------------------------------------


def _name_bytes(text):
    """Return a name as the digest writes it: UTF-8 byte length (u32 LE), then the bytes."""
    raw = text.encode("utf-8")
    return struct.pack("<I", len(raw)) + raw


def _element_bytes(kind, values):
    """Every element of ``values`` (one type) as the digest writes it, concatenated."""
    if kind == "float64":
        return struct.pack(f"<{len(values)}d", *(float(v) + 0.0 for v in values))
    if kind == "int64":
        return struct.pack(f"<{len(values)}q", *values)
    if kind == "bool":
        return bytes(1 if v else 0 for v in values)
    return b"".join(_name_bytes(v) for v in values)


def _element_ok(kind, value):
    """Whether one element is of the schema type ``kind``."""
    if kind == "float64":
        return _is_number(value)
    if kind == "int64":
        return (isinstance(value, int) and not isinstance(value, bool)
                and _INT64_MIN <= value <= _INT64_MAX)
    if kind == "string":
        return isinstance(value, str)
    return isinstance(value, bool)


def _flatten(value, ndim):
    """Return ``(shape, flat)`` of nested lists ``ndim`` deep, row-major; ``ValueError`` if ragged.

    An empty list at one level gives length 0 to every deeper dimension.
    """
    level, shape = [value], []
    for depth in range(ndim):
        if not all(isinstance(item, (list, tuple)) for item in level):
            raise ValueError(f"must be lists nested {ndim} deep (one per dimension); "
                             f"level {depth + 1} holds a non-list")
        lengths = {len(item) for item in level}
        if len(lengths) > 1:
            raise ValueError(f"is ragged at level {depth + 1} (lengths {sorted(lengths)})")
        size = lengths.pop() if lengths else 0
        shape.append(size)
        level = [element for item in level for element in item]
    return tuple(shape), tuple(level)


class DataContract:
    """A program's data schema, as the worker's ``describe`` published it (ADR-0328 A4).

    Julia owns the schema; this object only wraps the ``schema`` of a ``describe``
    reply (it copies nothing of its own), validates a payload against it before anything
    is sent, and computes the canonical data digest exactly as the wire spec states, so a
    reply's ``digest`` is checked against what was sent.

    Parameters
    ----------
    program : str
        The program the schema belongs to (every refusal names it).
    schema : dict
        ``{fields, dims}`` from a well-formed ``describe`` reply.

    Examples
    --------
    ::

        contract = DataContract("budgeted_select", {
            "fields": {"ids": {"type": "string", "dims": ["item"]},
                       "cost": {"type": "float64", "dims": ["item"]}},
            "dims": {"item": "ids"}})
        data = contract.check({"ids": ["A", "B"], "cost": [1.0, 2.0]})
        data["cost"]  # ((2,), (1.0, 2.0))
        len(contract.digest(data))  # 64
    """

    def __init__(self, program, schema):
        self.program = program
        self.schema = schema

    def element_type(self, name):
        """Return the element type of field ``name``.

        Parameters
        ----------
        name : str
            A schema field.

        Returns
        -------
        str
            ``float64``, ``int64``, ``string`` or ``bool``.
        """
        return self.schema["fields"][name]["type"]

    def _refuse(self, message):
        """Return the ``bad_data`` refusal naming the program."""
        return WorkerRefusal(self.program, "bad_data", message)

    def _field(self, name, value):
        """Return ``(shape, flat)`` of one field's value, refusing a wrong type or shape."""
        spec = self.schema["fields"][name]
        dims, kind = spec["dims"], spec["type"]
        if dims:
            try:
                shape, flat = _flatten(value, len(dims))
            except ValueError as exc:
                raise self._refuse(f"field {name!r} over {dims} {exc}") from None
        else:
            shape, flat = (), (value,)
        bad = next((v for v in flat if not _element_ok(kind, v)), _MISSING)
        if bad is not _MISSING:
            raise self._refuse(f"field {name!r} holds {kind} elements, got "
                               f"{bad!r:.{_QUOTE_CHARS}}")
        return shape, flat

    def check(self, data, current=None):
        """Validate ``data`` against the schema; return the model's full data after it.

        Parameters
        ----------
        data : dict
            Field name to value: every field at ``open`` (``current`` None), any subset
            at ``update``.
        current : dict or None
            The model's full data (as this method returns it) for an ``update``.

        Returns
        -------
        dict
            Field name to ``(shape, flat elements)`` over every field.

        Raises
        ------
        WorkerRefusal
            Kind ``bad_data`` naming the program and the field: an unknown or (at open)
            missing field, a wrong element type, a value nested other than its
            dimensions, two fields disagreeing on a dimension's length, a label field
            with a repeated value.
        """
        fields = self.schema["fields"]
        if not isinstance(data, dict):
            raise self._refuse(f"data must be an object of field name to value, got "
                               f"{type(data).__name__}")
        unknown = sorted(set(data) - set(fields), key=str)
        if unknown:
            raise self._refuse(f"no field named {unknown[0]!r} (the schema's fields: "
                               f"{sorted(fields)})")
        missing = sorted(set(fields) - set(data))
        if current is None and missing:
            raise self._refuse(f"the field {missing[0]!r} is required at open")
        full = {**(current or {}), **{name: self._field(name, data[name])
                                      for name in sorted(data)}}
        lengths = {}
        for name in [n for n in sorted(full) if n not in data] + sorted(data):
            for dim, size in zip(fields[name]["dims"], full[name][0]):
                other = lengths.setdefault(dim, (size, name))
                if other[0] != size:
                    raise self._refuse(
                        f"field {name!r} has {size} along {dim!r}, field {other[1]!r} "
                        f"has {other[0]}")
        for dim, label in self.schema["dims"].items():
            flat = full[label][1] if label is not None else ()
            if len(set(flat)) != len(flat):
                raise self._refuse(f"the label field {label!r} of {dim!r} must hold "
                                   "distinct values")
        return full

    def digest(self, full):
        """Return the canonical digest of the full data (the wire spec's byte layout).

        Parameters
        ----------
        full : dict
            What :meth:`check` returned.

        Returns
        -------
        str
            64 lowercase hex characters (sha256).
        """
        out = hashlib.sha256(_DIGEST_MAGIC)
        for name in sorted(full, key=lambda key: key.encode("utf-8")):
            shape, flat = full[name]
            kind = self.element_type(name)
            out.update(_name_bytes(name) + _TYPE_TAGS[kind] + struct.pack("<I", len(shape))
                       + b"".join(struct.pack("<Q", size) for size in shape))
            out.update(_element_bytes(kind, flat))
        return out.hexdigest()


#: A sentinel no element can be.
_MISSING = object()

#: Live scratch directories, removed at interpreter exit even if a worker never closed.
_SCRATCH_DIRS = set()


def _remove_scratch(directory):
    """Remove one scratch directory and forget it (missing is fine)."""
    _SCRATCH_DIRS.discard(directory)
    shutil.rmtree(directory, ignore_errors=True)


def _remove_every_scratch():
    """Remove every scratch directory still live (interpreter exit: no file outlives it)."""
    for directory in list(_SCRATCH_DIRS):
        _remove_scratch(directory)


class _ArrowHandoff:
    """One worker's Arrow transport: its scratch directory and the files of one request."""

    def __init__(self, threshold, root):
        self.threshold = threshold
        self.root = root
        self.directory = None
        self._count = 0

    def encode(self, contract, data, full):
        """Return ``(wire data, files)``: JSON values, or Arrow references past the threshold."""
        wire, files = {}, []
        try:
            for name in sorted(data):
                shape, flat = full[name]
                if shape and math.prod(shape) > self.threshold:
                    wire[name] = {"arrow": self._write(contract, name, shape, flat, files)}
                else:
                    wire[name] = data[name]
        except BaseException:
            self.remove(files)
            raise
        return wire, files

    def _write(self, contract, name, shape, flat, files):
        """Write one field as an Arrow IPC file; return its reference."""
        try:
            import pyarrow as pa
        except ImportError:
            raise WorkerRefusal(
                contract.program, "arrow",
                f"field {name!r} holds {math.prod(shape)} elements, more than the Arrow "
                f"threshold {self.threshold}, and pyarrow is not installed: install the "
                f"{ARROW_EXTRA!r} extra (pip install 'dskit[{ARROW_EXTRA}]')") from None
        if self.directory is None:
            self.directory = tempfile.mkdtemp(prefix=SCRATCH_PREFIX, dir=self.root)
            _SCRATCH_DIRS.add(self.directory)
        self._count += 1
        path = pathlib.Path(self.directory, f"a{self._count}.arrow").resolve()
        files.append(path)
        types = {"float64": pa.float64(), "int64": pa.int64(), "string": pa.string(),
                 "bool": pa.bool_()}
        table = pa.table({_ARROW_COLUMN: pa.array(flat, type=types[contract.element_type(name)])})
        with pa.OSFile(str(path), "wb") as sink, pa.ipc.new_file(sink, table.schema) as out:
            out.write_table(table)
        sha = hashlib.sha256(path.read_bytes()).hexdigest()
        return {"path": str(path), "sha256": sha, "shape": list(shape)}

    @staticmethod
    def remove(files):
        """Remove one request's files (missing is fine)."""
        for path in files:
            try:
                os.unlink(path)
            except FileNotFoundError:
                pass

    def discard(self):
        """Remove the scratch directory and everything in it (idempotent)."""
        directory, self.directory = self.directory, None
        if directory is not None:
            _remove_scratch(directory)


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
    """Refuse (``payload``) a request that is not JSON with finite numbers, before sending.

    A ``data`` object is checked field by field first, so the refusal names the field;
    the rest of the request is then checked without it, so no field is serialised twice.
    """
    data = request.get("data")
    if isinstance(data, dict):
        for name, value in data.items():
            try:
                json.dumps(value, allow_nan=False)
            except (TypeError, ValueError) as exc:
                raise WorkerRefusal(program, "payload",
                                    f"data field {name!r} is not JSON with finite numbers: "
                                    f"{exc}") from exc
        request = {**request, "data": {name: None for name in data}}
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


def _default_transport():
    """Return the data handoff settings when none are given (the named defaults)."""
    return {"arrow_threshold": DEFAULT_ARROW_THRESHOLD, "scratch_root": DEFAULT_SCRATCH_ROOT}


#: Every worker object alive, registered or not, so interpreter exit closes each one
#: while its pipe threads still run (a collector-time ``__del__`` at finalization would
#: close a pipe a daemon reader still holds, which aborts the interpreter).
_EVERY_WORKER = weakref.WeakSet()


def _close_every_worker():
    """Close every worker object, then every scratch directory (the ``atexit`` hook)."""
    for worker in list(_EVERY_WORKER):
        worker.close()
    _remove_every_scratch()


atexit.register(_close_every_worker)


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
    transport : dict or None
        The data handoff, exactly ``{arrow_threshold, scratch_root}``: arrays of more
        elements than ``arrow_threshold`` (int >= 0) travel as Arrow files in a scratch
        directory made under ``scratch_root`` (None: the system temporary directory),
        one per worker process and removed with it; None means
        :data:`DEFAULT_ARROW_THRESHOLD` and :data:`DEFAULT_SCRATCH_ROOT`.

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

    def __init__(self, command, *, shutdown_timeout=DEFAULT_SHUTDOWN_TIMEOUT, settings=None,
                 transport=None):
        self.command = [str(part) for part in command]
        self.shutdown_timeout = float(shutdown_timeout)
        self.settings = dict(settings or {"pool_models": DEFAULT_POOL_MODELS,
                                          "pool_bytes": DEFAULT_POOL_BYTES})
        self.transport = dict(transport or _default_transport())
        self._handoff = _ArrowHandoff(self.transport["arrow_threshold"],
                                      self.transport["scratch_root"])
        self._described = {}
        self._handles = set()
        _EVERY_WORKER.add(self)
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

        Notes
        -----
        Asked once per worker process and program: the reply is kept until the worker
        is discarded (a restarted worker may run another package version).
        """
        with self._lock:
            self._ensure_started(program, float(start_timeout))
            return json.loads(json.dumps(self._described_body(program, float(timeout))))

    def _described_body(self, program, timeout):
        """Return the kept ``describe`` body of ``program``, asking the worker the first time."""
        if program not in self._described:
            reply = self._exchange(program, {"op": "describe", "program": program}, timeout)
            problems = _describe_problems(reply, program)
            if problems:
                raise self._malformed(program, "; ".join(problems), json.dumps(reply))
            self._described[program] = {
                key: reply[key] for key in _REPLY_KEYS["describe"] - {"id", "ok"}}
        return self._described[program]

    def _send_data(self, program, contract, request, data, full, timeout, expect):
        """Send a request whose ``data`` is checked: Arrow files written, removed, digest checked.

        The digest of the data sent is computed BEFORE sending, so nothing slow runs
        between the reply and the bookkeeping that follows it.
        """
        expected = contract.digest(full)
        wire, files = self._handoff.encode(contract, data, full)
        try:
            reply = self._exchange(program, {**request, "data": wire}, timeout, expect=expect)
        finally:
            self._handoff.remove(files)
        with self._settling():
            self._echo_checks(program, request, expect, reply)
            self._session_checks(program, expect, reply)
        if reply["digest"] != expected:
            self._discard()
            raise WorkerRefusal(program, "digest",
                                f"the worker applied data of digest {reply['digest']}, the "
                                f"data sent has digest {expected}; the worker was killed")
        return reply

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
            As :meth:`solve`; ``bad_data`` (nothing but ``describe`` sent) when ``data``
            fails the program's schema, ``arrow`` when an array past the threshold needs
            the missing pyarrow, ``digest`` (the worker killed) when the reply's digest is
            not that of the data sent.
        """
        body = {"op": "open", "program": program, "data": data, "params": params,
                "solver": solver, "solver_options": solver_options,
                "components": dict(components or {})}
        _check_payload(program, body)
        with self._lock:
            self._ensure_started(program, float(start_timeout))
            contract = DataContract(program,
                                    self._described_body(program, float(timeout))["schema"])
            full = contract.check(data)
            reply = self._send_data(program, contract, body, data, full, float(timeout),
                                    "open")
            with self._settling():
                self._handles.add(reply["handle"])
                return LiveModel(self, self.starts, program, solver, reply, float(timeout),
                                 contract, full)

    def _session(self, model, op, body, check_payload=True):
        """Send one op on ``model``'s handle; return the validated ``ok`` reply.

        An ``update`` body's ``data`` is checked against the model's contract and sent
        through :meth:`_send_data`; the model's full data follows a successful reply.
        """
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
            if op == "update":
                full = model.contract.check(body["data"], model.data)
                reply = self._send_data(program, model.contract, request, body["data"],
                                        full, model.timeout, expect)
                with self._settling():
                    if reply["signature"] != model.signature:
                        raise self._malformed(
                            program, f"an update reply changing the signature from "
                            f"{model.signature!r} (the worker must refuse it as "
                            f"shape_changed)", json.dumps(reply))
                    model._settle(reply, data=full)
                return reply
            reply = self._exchange(program, request, model.timeout, expect=expect)
            with self._settling():
                self._echo_checks(program, request, expect, reply)
                self._session_checks(program, expect, reply)
                if expect in ("handle-solve", "search"):
                    reply["record"] = self._record(program, reply["record"], model.solver)
                if expect == "search":
                    problems = _trajectory_problems(reply["trajectory"],
                                                    body["limits"].get("rounds"))
                    if problems:
                        self._discard()
                        raise WorkerRefusal(program, "trajectory", "; ".join(problems))
                model._settle(reply)
            return reply

    @contextlib.contextmanager
    def _settling(self):
        """Kill the worker when anything but a refusal interrupts the work after a reply.

        The worker has applied the request by then, so Python's record of the model (its
        data, digest, signature, timings) must follow it or the worker must go: a
        ``KeyboardInterrupt`` or ``MemoryError`` here would otherwise leave Python
        believing the model is in a state it is not in.
        """
        try:
            yield
        except WorkerRefusal:
            raise
        except BaseException:
            self._discard()
            raise

    def _echo_checks(self, program, request, op, reply):
        """Kill the worker (``malformed``) on a wrong handle echo or a reissued handle."""
        if "handle" in request and reply["handle"] != request["handle"]:
            raise self._malformed(program, f"a reply for handle {reply['handle']!r}, the "
                                  f"request named {request['handle']!r}", json.dumps(reply))
        if op == "open" and not (isinstance(reply["handle"], str)
                                 and _HANDLE.fullmatch(reply["handle"])
                                 and reply["handle"] not in self._handles):
            raise self._malformed(program, "an open reply whose handle is not 'h' and a "
                                  "counter this worker process has not issued before",
                                  json.dumps(reply))

    def _session_checks(self, program, op, reply):
        """Kill the worker on a mistyped session body (``malformed``) or timings (``timings``)."""
        problems = _session_problems(op, reply)
        if problems:
            raise self._malformed(program, "; ".join(problems), json.dumps(reply))
        if "timings" in reply:
            problems = _timings_problems(reply["timings"]) or _timings_rule_problems(
                op, reply["timings"])
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
        self._described = {}
        self._handles = set()
        self._handoff.discard()
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
    contract : DataContract
        The program's schema, as the worker described it.
    data : dict
        The model's full data, as :meth:`DataContract.check` returned it.

    Attributes
    ----------
    handle, signature, digest : str
        The worker's handle, the model's shape key and the digest of its current data
        (checked against :attr:`data` on every change).
    timings : Timings
        The timings block of the last op.
    solve_record : SolveRecord or None
        The record of the last :meth:`solve` or :meth:`search`.
    trajectory : tuple of SearchRound
        The rounds of the last :meth:`search`; ``()`` before one.
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
            solution, record, rounds = model.search(
                "fix_and_reoptimise", {"free": 2, "stall": 3}, limits={"rounds": 10},
                seed=7)
    """

    def __init__(self, worker, generation, program, solver, reply, timeout, contract, data):
        self._worker = worker
        self.generation = generation
        self.program = program
        self.solver = solver
        self.handle = reply["handle"]
        self.signature = reply["signature"]
        self.digest = reply["digest"]
        self.timings = Timings(**reply["timings"])
        self.timeout = timeout
        self.contract = contract
        self.data = data
        self.solve_record = None
        self.trajectory = ()
        self.closed = False

    def _op(self, op, body, check_payload=True):
        """Send ``op`` on this handle; return the reply (the worker settles this record)."""
        return self._worker._session(self, op, body, check_payload)

    def _settle(self, reply, data=None):
        """Follow a validated reply: timings, data and digest, record and trajectory.

        Called by the worker under its lock, inside the guard that kills the worker when
        this is interrupted, so this record never parts from the worker's model.
        """
        if "timings" in reply:
            self.timings = Timings(**reply["timings"])
        if data is not None:
            self.data, self.signature, self.digest = data, reply["signature"], reply["digest"]
        if "record" in reply:
            self.solve_record = reply["record"]
        if "trajectory" in reply:
            self.trajectory = tuple(SearchRound(**row) for row in reply["trajectory"])

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
            Any subset of the schema's fields (None changes none), checked against the
            program's schema before sending (``bad_data``); a change of shape is the
            worker's ``shape_changed`` refusal, never a rebuild.
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
        self._op("update", {"data": dict(data or {}), "params": params,
                            "components": dict(components or {})})
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
        return reply["solution"], self.solve_record

    def search(self, algorithm, params=None, limits=None, seed=0, start=None,
               round_limits=None):
        """Run a search algorithm registered in the worker on this live model.

        The loop runs in the worker (ADR-0328 A6): each round frees the algorithm's
        neighbourhood, fixes the rest at the incumbent and re-solves the live model.
        Python sends the request and checks the reply; it holds no search algorithm.

        Parameters
        ----------
        algorithm : str
            The search's registry name in the worker.
        params : dict or None
            The search's own knobs (the worker checks them); None is ``{}``.
        limits : dict or None
            Overall limits, any of :data:`SEARCH_LIMIT_KEYS` (``time_limit`` seconds > 0,
            ``rounds`` an integer >= 1); None is ``{}``.
        seed : int
            The search's only source of randomness, in ``[0, SEED_BOUND)``.
        start : str, dict or None
            Sent as :meth:`start` first when not None (``"last"`` or ``{group: {key:
            value}}``); the search's first solve then runs from it. The start is its own
            request, so a search the worker then refuses leaves it in effect, and the
            refusal says so.
        round_limits : dict or None
            Per-solve limits of every round (as :meth:`solve`); None is ``{}``.

        Returns
        -------
        tuple
            ``(solution, record, trajectory)``: the incumbent, the
            :class:`~dskit.pipeline.libs.pyomo.SolveRecord` of its solve (``seconds`` the
            whole search's), and a tuple of :class:`SearchRound`; also on
            :attr:`solve_record` and :attr:`trajectory`.

        Raises
        ------
        WorkerRefusal
            Kind ``limits`` or ``payload`` (nothing sent) for limits, a name, params or a
            seed of the wrong shape; ``trajectory`` (the worker killed) for a mistyped
            trajectory; the worker's own kinds (``unknown_search``, ...) otherwise.
        """
        limits = {} if limits is None else limits
        round_limits = {} if round_limits is None else round_limits
        params = {} if params is None else params
        problems = _search_limits_problems(limits) + [
            f"round_limits: {p}" for p in _limits_problems(round_limits)]
        if problems:
            raise WorkerRefusal(self.program, "limits", "; ".join(problems))
        if not _is_name(algorithm) or not isinstance(params, dict):
            raise WorkerRefusal(self.program, "payload",
                                "a search is a non-empty name and an object of params")
        if not _is_int(seed) or seed >= SEED_BOUND:
            raise WorkerRefusal(self.program, "payload",
                                f"seed must be an integer in [0, {SEED_BOUND}), got {seed!r}")
        body = {"search": algorithm, "params": dict(params), "seed": seed,
                "limits": dict(limits), "round_limits": dict(round_limits)}
        _check_payload(self.program, body)
        if start is not None:
            self.start(start)
        try:
            reply = self._op("search", body, check_payload=False)
        except WorkerRefusal as refusal:
            alive = (self._worker._process is not None
                     and self._worker.starts == self.generation)
            if start is None or not alive or refusal.kind in ("closed", "stale_handle"):
                raise
            raise WorkerRefusal(
                self.program, refusal.kind,
                f"{refusal.message}; the start sent before the search IS in effect on the model (start_source "
                f"{self.timings.start_source!r}): clear it with start(None)") from refusal
        return reply["solution"], self.solve_record, self.trajectory

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
    """The process's workers, one per launch command, pool settings and transport."""

    def __init__(self):
        self._workers = {}
        self._lock = threading.Lock()

    @staticmethod
    def _key(command, settings, transport):
        """Return the registry key of ``command`` under ``settings`` and ``transport``."""
        return (tuple(str(part) for part in command), tuple(sorted(settings.items())),
                tuple(sorted(transport.items())))

    def worker(self, command, settings=None, transport=None):
        """Return the worker of ``command``, ``settings`` and ``transport``, made on first use."""
        settings = dict(settings or {"pool_models": DEFAULT_POOL_MODELS,
                                     "pool_bytes": DEFAULT_POOL_BYTES})
        transport = dict(transport or _default_transport())
        key = self._key(command, settings, transport)
        with self._lock:
            if key not in self._workers:
                self._workers[key] = JuliaWorker(key[0], settings=settings,
                                                 transport=transport)
            return self._workers[key]

    def live(self):
        """Return the launch commands whose worker has a running process, sorted."""
        with self._lock:
            return sorted(key[0] for key, worker in self._workers.items()
                          if worker.pid is not None)

    def close(self, command, settings=None, transport=None):
        """Shut down and forget the worker of ``command`` (every settings when None)."""
        command = tuple(str(part) for part in command)
        with self._lock:
            keys = [key for key in self._workers if key[0] == command
                    and (settings is None or key == self._key(
                        command, settings, transport or _default_transport()))]
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

    #: The worker's data handoff (ADR-0328 A4): arrays of more elements than this go as
    #: Arrow files, in a scratch directory made under ``scratch_root`` (None: the system
    #: temporary directory). Workers with other values are other workers.
    arrow_threshold = DEFAULT_ARROW_THRESHOLD
    scratch_root = DEFAULT_SCRATCH_ROOT

    @classmethod
    def transport_problems(cls):
        """Return the problems of the class's data-handoff knobs (checked before any start).

        Returns
        -------
        list of str
            ``arrow_threshold`` must be an integer >= 0, ``scratch_root`` None or an
            existing directory.
        """
        problems = []
        if not _is_int(cls.arrow_threshold):
            problems.append(f"arrow_threshold must be an integer >= 0, got "
                            f"{cls.arrow_threshold!r}")
        root = cls.scratch_root
        if root is not None and not (isinstance(root, (str, os.PathLike))
                                     and os.path.isdir(root)):
            problems.append(f"scratch_root must be None or an existing directory, got "
                            f"{root!r}")
        return problems

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
        _POOL.close(self._command(), self._settings(), self._transport())

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

    def _transport(self):
        """Return the worker's data handoff from the class knobs, refusing bad ones."""
        problems = self.transport_problems()
        if problems:
            raise TypeError(f"{type(self).__name__}: " + "; ".join(problems))
        root = self.scratch_root
        return {"arrow_threshold": self.arrow_threshold,
                "scratch_root": None if root is None else str(pathlib.Path(root).resolve())}

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
        return _POOL.worker(self._command(), self._settings(), self._transport())


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
