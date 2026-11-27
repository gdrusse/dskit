"""A stand-in for the Julia worker: the JuMP pack's JSON-lines protocol v2 in Python.

Run as ``python jump_fake_worker.py [--mode MODE] [--once FLAG_FILE] [--patch JSON]
[--delay SECONDS] [--pidfile FILE] [--request-log FILE]``. It reads one JSON request per
line on stdin and answers one JSON object per line on stdout, exactly as
``dskit.pipeline.libs.jump.JuliaWorker`` expects of the real worker, so the pack's suite
needs no Julia. It is the executable reference of the wire protocol v2 (the ADR-0328
amendment 1 entry of ``docs/architecture/decision-log.md``), every op and the Arrow
reference included: it holds live models in a pool keyed ``(program, signature, solver,
solver_options)`` with a count and a byte cap and LRU eviction, and reports the real path
(built / reused / updated / rebuilt) of every change.

It knows TWO programs, both solved by brute force so the answers are real, under ONE
solver, ``HiGHS``; any other program or solver gets the error reply the real worker sends:

``budgeted_select``  a 0/1 knapsack over one labelled dimension ``item``: variable group
                     ``select[item]``, the optional component ``max_items`` (the count
                     capped at ``params.max_items``).
``assignment``       a generalized assignment over the labelled dimensions ``item`` and
                     ``bin``: variable group ``x[item, bin]``, a two-dimensional field per
                     coefficient, the optional component ``max_per_bin``.

The data contract, digest and default signature are written once, generically over any
schema (several dimensions, labelled or not), so the fake is the reference for both.
Fixes and starts genuinely change the answer: a fixed variable is forced, and under a
``node_limit`` the enumeration stops early with the start (if feasible) as its first
incumbent. The one-shot ``solve`` is v1 unchanged and ignores extra ``data`` keys, so a
test can pad a payload.

ONE search is registered, ``fix_and_reoptimise`` (params exactly ``{free, stall}``): each
round frees ``free`` variables drawn by the spec's seeded draw (:class:`SplitMix64` and a
partial Fisher-Yates over the caller's unfixed keys in ascending order), fixes the rest at
the incumbent, re-solves from the incumbent under the round limits, and accepts a strictly
better objective; it is done after ``stall`` rounds in a row without one. A search takes
the model's last solution as its incumbent only when THIS handle found it under the
model's current data, params, components and fixes; otherwise it solves first. Its record
names the search's stop reason, never a solver's termination.

``--mode`` picks a behaviour, never an edit to this file. At ``hello``:

``bad-protocol``   report a protocol version the pack does not speak
``v1-only``        a v1 worker: ignore ``settings`` and report protocol 1
``stdout-noise``   print a banner line that is not JSON before the ``hello`` reply
``bool-id``        answer ``hello`` under the id ``true`` instead of the integer
``hello-error``    answer ``hello`` with a well-formed ``ok: false``
``deaf``           answer ``hello``, then never read stdin again
``slow-hello``     sleep ``--delay`` seconds before answering ``hello``

At the first one-shot ``solve`` (with ``--once``, only until the flag file exists):

``crash``          exit without a reply
``hang``           never answer
``malformed``      answer with a line that is not JSON
``wrong-id``       answer under another request id
``error-reply``    answer with a well-formed ``ok: false``
``bad-record``     answer with a record missing ``gap``
``patch-record``   answer with the record updated by ``--patch`` (a JSON object)
``dup-key``        answer with the key ``id`` twice (a wrong one first)
``extra-key``      answer with a key the protocol does not name
``over-budget``    answer OPTIMAL with every candidate selected, budget ignored
``float-overflow`` answer with the objective written as the literal ``1e999``
``deep-reply``     answer with a solution nested far deeper than any parser allows

At the first session reply that can carry it (``--once`` as above):

``bad-timings``    the first timings block updated by ``--patch`` (a JSON object)
``wrong-handle``   the first reply to a request naming a handle echoes another one
``bad-digest``     the first ``open``/``update`` reply carries a digest of other data
``bad-trajectory`` the first search trajectory's first row updated by ``--patch``
``extra-round``    the first search trajectory gains one well-typed row past its last
``patch-search``   the first search record updated by ``--patch``

``--on OP`` limits these to the replies of request op ``OP`` (``solve`` meaning a handle
solve), so a test can aim one at an ``update`` or a ``search``.

Answering correctly throughout:

``ok``             the default
``noisy-stderr``   write a megabyte to stderr before every reply
``big-reply``      a record with many binding rows (a reply line of megabytes)
``linger``         on stdin EOF without ``shutdown``, sleep instead of exiting
``slow-solve``     sleep ``--delay`` seconds before answering every solve
``no-update``      the programs have no ``update!`` hook: every in-place change rebuilds

``--pidfile`` writes this process's id on start (so a test can find it behind a
wrapper); ``--request-log`` appends every request line received (so a test can read what
crossed the wire).
"""

from __future__ import annotations

import argparse
import collections
import hashlib
import itertools
import json
import math
import os
import struct
import sys
import time

#: The protocol version this fake speaks; ``dskit.pipeline.libs.jump.PROTOCOL_VERSION``
#: pins it from the other side in the suite.
PROTOCOL_VERSION = 2

#: What ``v1-only`` reports.
V1_PROTOCOL_VERSION = 1

#: What ``bad-protocol`` reports.
WRONG_PROTOCOL_VERSION = 999

SOLVERS = ("HiGHS",)

#: The fake's estimate of one model's memory, per variable.
BYTES_PER_VARIABLE = 1000

#: The per-solve limits the protocol names.
LIMIT_KEYS = ("time_limit", "node_limit", "mip_rel_gap", "solution_limit")

#: What a ``start`` request's ``source`` may be.
START_SOURCES = ("last", "values", "none")

#: The exact key set of each request, by op (a handle solve is told apart by ``handle``).
REQUEST_KEYS = {
    "hello": {"id", "op", "settings"},
    "describe": {"id", "op", "program"},
    "open": {"id", "op", "program", "data", "params", "solver", "solver_options",
             "components"},
    "update": {"id", "op", "handle", "data", "params", "components"},
    "fix": {"id", "op", "handle", "values"},
    "free": {"id", "op", "handle", "targets"},
    "start": {"id", "op", "handle", "source", "values"},
    "solve": {"id", "op", "program", "data", "params", "solver", "solver_options"},
    "handle-solve": {"id", "op", "handle", "limits"},
    "search": {"id", "op", "handle", "search", "params", "seed", "limits", "round_limits"},
    "close": {"id", "op", "handle"},
    "shutdown": {"id", "op"},
}

HELLO_MODES = ("bad-protocol", "v1-only", "stdout-noise", "bool-id", "hello-error", "deaf",
               "slow-hello")
SOLVE_MODES = ("crash", "hang", "malformed", "wrong-id", "error-reply", "bad-record",
               "patch-record", "dup-key", "extra-key", "over-budget", "float-overflow",
               "deep-reply")
TIMINGS_MODES = ("bad-timings", "wrong-handle", "bad-digest", "bad-trajectory",
                 "extra-round", "patch-search")
CLEAN_MODES = ("ok", "noisy-stderr", "big-reply", "linger", "slow-solve", "no-update")
MODES = CLEAN_MODES + HELLO_MODES + SOLVE_MODES + TIMINGS_MODES

#: Bytes ``noisy-stderr`` writes before each reply (far beyond any pipe buffer).
STDERR_NOISE_BYTES = 1 << 20

#: Binding rows ``big-reply`` sends.
BIG_REPLY_ROWS = 40_000

#: Nesting depth of ``deep-reply`` (far past any recursion limit).
DEEP_REPLY_DEPTH = 100_000

#: The registered searches and the exact params each takes.
SEARCHES = {"fix_and_reoptimise": ("free", "stall")}

#: The overall limits a search takes.
SEARCH_LIMIT_KEYS = ("time_limit", "rounds")

#: A seed is an integer in [0, SEED_BOUND); a float64 element given as an integer must
#: have a magnitude of at most FLOAT64_EXACT (both 2^53: exact in a float64).
FLOAT64_EXACT = 1 << 53
SEED_BOUND = FLOAT64_EXACT

#: A search record's ``termination``: the search's stop reason, never a solver's.
SEARCH_DONE = "SEARCH_DONE"
SEARCH_ROUND_LIMIT = "SEARCH_ROUND_LIMIT"
SEARCH_TIME_LIMIT = "SEARCH_TIME_LIMIT"
SEARCH_ROUND_NO_POINT = "SEARCH_ROUND_NO_POINT"
SEARCH_NO_INCUMBENT = "SEARCH_NO_INCUMBENT"

#: What a schema type is as an Arrow column type name (``str(pyarrow type)``).
ARROW_TYPES = {"float64": "double", "int64": "int64", "string": "string", "bool": "bool"}

#: What a rebuild carries over to the model constructed again (the spec's list): the
#: caller can observe a rebuild only through ``path`` and the timings.
CARRIED_BY_A_REBUILD = ("fixed", "start", "start_source", "last", "last_record",
                        "last_state", "last_handle", "held")

#: Numeric tolerance of the brute force (feasibility and a strictly better objective).
FEASIBILITY_TOL = 1e-9
IMPROVEMENT_TOL = 1e-12

#: SplitMix64's constants (the spec's seeded draw).
_SPLITMIX_GAMMA = 0x9E3779B97F4A7C15
_SPLITMIX_MIX1 = 0xBF58476D1CE4E5B9
_SPLITMIX_MIX2 = 0x94D049BB133111EB
_U64 = (1 << 64) - 1


class SplitMix64:
    """The spec's generator: SplitMix64 seeded with the request's ``seed``."""

    def __init__(self, seed):
        self.state = seed & _U64

    def next(self):
        """The next 64-bit output."""
        self.state = (self.state + _SPLITMIX_GAMMA) & _U64
        z = self.state
        z = ((z ^ (z >> 30)) * _SPLITMIX_MIX1) & _U64
        z = ((z ^ (z >> 27)) * _SPLITMIX_MIX2) & _U64
        return z ^ (z >> 31)

    def draw(self, candidates, k):
        """``k`` of ``candidates`` by a partial Fisher-Yates: one output per pick."""
        pool = list(candidates)
        for j in range(k):
            t = j + self.next() % (len(pool) - j)
            pool[j], pool[t] = pool[t], pool[j]
        return pool[:k]


class Refused(Exception):
    """A well-formed error reply: ``kind`` and ``message``."""

    def __init__(self, kind, message):
        super().__init__(message)
        self.kind = kind


def _write(text):
    sys.stdout.write(text + "\n")
    sys.stdout.flush()


# -- JSON values -------------------------------------------------------------------


def _is_int(value, low=None):
    return isinstance(value, int) and not isinstance(value, bool) and (low is None
                                                                       or value >= low)


def _is_number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def _element_ok(kind, value):
    if kind == "float64":
        return _is_number(value) and not (isinstance(value, int)
                                          and abs(value) > FLOAT64_EXACT)
    if kind == "int64":
        return _is_int(value) and -(1 << 63) <= value < (1 << 63)
    if kind == "string":
        return isinstance(value, str)
    return isinstance(value, bool)


def _lone_surrogate(obj):
    """Whether any string (key or value) in ``obj`` holds a lone surrogate (not UTF-8)."""
    stack = [obj]
    while stack:
        value = stack.pop()
        if isinstance(value, dict):
            stack.extend(value.keys())
            stack.extend(value.values())
        elif isinstance(value, list):
            stack.extend(value)
        elif isinstance(value, str) and not value.isascii():
            try:
                value.encode("utf-8")
            except UnicodeEncodeError:
                return True
    return False


def _nested(value, ndim):
    """``(shape, flat)`` of ``value`` nested ``ndim`` lists deep, row-major; ValueError if not."""
    level, shape = [value], []
    for _ in range(ndim):
        if not all(isinstance(item, list) for item in level):
            raise ValueError("is not nested one list per dimension")
        lengths = {len(item) for item in level}
        if len(lengths) > 1:
            raise ValueError("is ragged")
        shape.append(lengths.pop() if lengths else 0)
        level = [x for item in level for x in item]
    return shape, level


def _nest(flat, shape):
    """The nested lists of ``flat`` (row-major) under ``shape`` (the inverse of _nested)."""
    if not shape:
        return flat[0]
    if len(shape) == 1:
        return list(flat)
    step = math.prod(shape[1:])
    return [_nest(flat[i * step:(i + 1) * step], shape[1:]) for i in range(shape[0])]


def _key_order(key):
    """A variable key's place in ascending order: strings by UTF-8 bytes, integers numerically."""
    return tuple(e.encode("utf-8") if isinstance(e, str) else e for e in key)


# -- the canonical digests (the ADR's byte layout, restated nowhere else in the fake) ---

_TAGS = {"float64": b"f", "int64": b"i", "string": b"s", "bool": b"b"}


def _element(kind, value):
    """One element's canonical bytes."""
    if kind == "float64":
        return struct.pack("<d", 0.0 if value == 0 else float(value))
    if kind == "int64":
        return struct.pack("<q", value)
    if kind == "string":
        raw = value.encode("utf-8")
        return struct.pack("<I", len(raw)) + raw
    return b"\x01" if value else b"\x00"


def _name(text):
    raw = text.encode("utf-8")
    return struct.pack("<I", len(raw)) + raw


def data_digest(data, schema):
    """The lowercase hex sha256 of ``data``'s canonical form (any number of dimensions)."""
    out = [b"dskit-data-v1\n"]
    for field in sorted(data, key=lambda k: k.encode("utf-8")):
        spec = schema["fields"][field]
        shape, flat = _nested(data[field], len(spec["dims"]))
        out += [_name(field), _TAGS[spec["type"]], struct.pack("<I", len(shape))]
        out += [struct.pack("<Q", n) for n in shape]
        out += [_element(spec["type"], v) for v in flat]
    return hashlib.sha256(b"".join(out)).hexdigest()


def dimension_keys(data, schema):
    """Each dimension's keys: its label field's values, or the positions 0..n-1."""
    keys = {}
    for dim, label in schema["dims"].items():
        if label is not None:
            keys[dim] = list(data[label])
            continue
        for name, spec in schema["fields"].items():
            if dim in spec["dims"] and name in data:
                shape, _ = _nested(data[name], len(spec["dims"]))
                keys[dim] = list(range(shape[spec["dims"].index(dim)]))
                break
        else:
            keys[dim] = []
    return keys


def shape_signature(data, schema):
    """The default signature: every dimension's name, length and labels (or the 0x00 flag)."""
    keys = dimension_keys(data, schema)
    out = [b"dskit-shape-v1\n"]
    for dim in sorted(schema["dims"], key=lambda k: k.encode("utf-8")):
        label = schema["dims"][dim]
        out += [_name(dim), struct.pack("<Q", len(keys[dim]))]
        if label is None:
            out.append(b"\x00")
        else:
            kind = schema["fields"][label]["type"]
            out += [b"\x01"] + [_element(kind, v) for v in keys[dim]]
    return "shape:" + hashlib.sha256(b"".join(out)).hexdigest()


# -- the two programs ----------------------------------------------------------------


class Program:
    """One reference program: its declared contract and its brute-force model.

    A point is the frozenset of ``(group, key)`` pairs at 1 (every variable is binary).
    """

    name = None
    schema = None
    variables = None
    components = None

    def keys(self, data):
        """Every ``(group, key)`` of the model, in the spec's ascending order."""
        dims = dimension_keys(data, self.schema)
        out = []
        for group in sorted(self.variables, key=lambda g: g.encode("utf-8")):
            index = self.variables[group]["index"]
            keys = itertools.product(*(dims[d] for d in index))
            out += [(group, key) for key in sorted(keys, key=_key_order)]
        return out

    def check_params(self, params, attached):
        """Refuse (``bad_request``) params the program cannot build from."""
        raise NotImplementedError

    def points(self, data):
        """Every point, in the enumeration order a ``node_limit`` cuts."""
        raise NotImplementedError

    def feasible(self, data, params, attached, point):
        raise NotImplementedError

    def objective(self, data, point):
        raise NotImplementedError

    def constraints(self, data, attached):
        raise NotImplementedError

    def binding(self, data, params, attached, point):
        raise NotImplementedError

    def solution(self, data, point):
        raise NotImplementedError


class BudgetedSelect(Program):
    """The 0/1 knapsack: maximise value, total cost within ``params.budget``."""

    name = "budgeted_select"
    schema = {
        "fields": {
            "ids": {"type": "string", "dims": ["item"]},
            "cost": {"type": "float64", "dims": ["item"]},
            "value": {"type": "float64", "dims": ["item"]},
        },
        "dims": {"item": "ids"},
    }
    variables = {"select": {"index": ["item"], "domain": "binary"}}
    components = ["max_items"]

    def check_params(self, params, attached):
        if not isinstance(params, dict) or not _is_number(params.get("budget")):
            raise Refused("bad_request", "params must carry a finite budget")
        if "max_items" in attached and not _is_int(params.get("max_items"), 0):
            raise Refused("bad_request", "the max_items component needs params.max_items")

    def points(self, data):
        ids = sorted(data["ids"])
        for size in range(len(ids) + 1):
            for subset in itertools.combinations(ids, size):
                yield frozenset(("select", (i,)) for i in subset)

    @staticmethod
    def _chosen(point):
        return [key[0] for _, key in point]

    def feasible(self, data, params, attached, point):
        cost = dict(zip(data["ids"], data["cost"]))
        chosen = self._chosen(point)
        return (sum(cost[i] for i in chosen) <= float(params["budget"]) + FEASIBILITY_TOL
                and ("max_items" not in attached or len(chosen) <= params["max_items"]))

    def objective(self, data, point):
        value = dict(zip(data["ids"], data["value"]))
        return sum(value[i] for i in self._chosen(point))

    def constraints(self, data, attached):
        return 1 + ("max_items" in attached)

    def binding(self, data, params, attached, point):
        cost = dict(zip(data["ids"], data["cost"]))
        slack = (None if point is None
                 else float(params["budget"]) - sum(cost[i] for i in self._chosen(point)))
        return [{"name": "budget", "rows": 1,
                 "binding": int(slack is not None and abs(slack) <= FEASIBILITY_TOL),
                 "min_slack": slack}]

    def solution(self, data, point):
        return {"selected": None if point is None else sorted(self._chosen(point))}


class Assignment(Program):
    """Generalized assignment: each item to at most one bin, within each bin's capacity."""

    name = "assignment"
    schema = {
        "fields": {
            "items": {"type": "string", "dims": ["item"]},
            "bins": {"type": "string", "dims": ["bin"]},
            "value": {"type": "float64", "dims": ["item", "bin"]},
            "weight": {"type": "float64", "dims": ["item", "bin"]},
            "capacity": {"type": "float64", "dims": ["bin"]},
        },
        "dims": {"item": "items", "bin": "bins"},
    }
    variables = {"x": {"index": ["item", "bin"], "domain": "binary"}}
    components = ["max_per_bin"]

    def check_params(self, params, attached):
        if not isinstance(params, dict) or set(params) - {"max_per_bin"}:
            raise Refused("bad_request", "assignment params are at most {max_per_bin}")
        if "max_per_bin" in params and not _is_int(params["max_per_bin"], 0):
            raise Refused("bad_request", "max_per_bin must be an integer >= 0")
        if "max_per_bin" in attached and "max_per_bin" not in params:
            raise Refused("bad_request", "the max_per_bin component needs params.max_per_bin")

    def points(self, data):
        items, bins = data["items"], data["bins"]
        for choice in itertools.product([None, *bins], repeat=len(items)):
            yield frozenset(("x", (i, b)) for i, b in zip(items, choice) if b is not None)

    @staticmethod
    def _index(data):
        return ({i: n for n, i in enumerate(data["items"])},
                {b: n for n, b in enumerate(data["bins"])})

    def _loads(self, data, point):
        """Per item its bin count; per bin its weight and its item count."""
        item_at, bin_at = self._index(data)
        per_item = collections.Counter(i for _, (i, _) in point)
        weight = {b: 0.0 for b in data["bins"]}
        count = collections.Counter(b for _, (_, b) in point)
        for _, (i, b) in point:
            weight[b] += data["weight"][item_at[i]][bin_at[b]]
        return per_item, weight, count

    def feasible(self, data, params, attached, point):
        per_item, weight, count = self._loads(data, point)
        _, bin_at = self._index(data)
        cap = data["capacity"]
        return (all(n <= 1 for n in per_item.values())
                and all(weight[b] <= cap[bin_at[b]] + FEASIBILITY_TOL for b in data["bins"])
                and ("max_per_bin" not in attached
                     or all(count[b] <= params["max_per_bin"] for b in data["bins"])))

    def objective(self, data, point):
        item_at, bin_at = self._index(data)
        return sum(data["value"][item_at[i]][bin_at[b]] for _, (i, b) in point)

    def constraints(self, data, attached):
        return len(data["items"]) + len(data["bins"]) * (1 + ("max_per_bin" in attached))

    def binding(self, data, params, attached, point):
        rows = {"one_bin": len(data["items"]), "capacity": len(data["bins"])}
        if "max_per_bin" in attached:
            rows["max_per_bin"] = len(data["bins"])
        if point is None:
            return [{"name": n, "rows": r, "binding": 0, "min_slack": None}
                    for n, r in rows.items()]
        per_item, weight, count = self._loads(data, point)
        _, bin_at = self._index(data)
        slacks = {"one_bin": [1 - per_item[i] for i in data["items"]],
                  "capacity": [data["capacity"][bin_at[b]] - weight[b] for b in data["bins"]]}
        if "max_per_bin" in attached:
            slacks["max_per_bin"] = [params["max_per_bin"] - count[b] for b in data["bins"]]
        return [{"name": n, "rows": rows[n],
                 "binding": sum(abs(s) <= FEASIBILITY_TOL for s in slacks[n]),
                 "min_slack": min(slacks[n]) if slacks[n] else None} for n in rows]

    def solution(self, data, point):
        if point is None:
            return {"assigned": None}
        bin_of = {i: b for _, (i, b) in point}
        return {"assigned": [[i, bin_of[i]] for i in data["items"] if i in bin_of]}


PROGRAMS = {program.name: program for program in (BudgetedSelect(), Assignment())}

#: The program of the v1 one-shot misbehaviours and of ``bad-digest``.
PROGRAM = BudgetedSelect.name


def _brute(program, data, params, attached, fixed=None, start=None, node_limit=None):
    """Brute force under fixes and a node limit; the start seeds the incumbent.

    Returns ``(point or None, objective or None, complete, start_accepted)``; ties go to the
    first point in the program's enumeration order.
    """
    fixed = fixed or {}

    def ok(point):
        return (program.feasible(data, params, attached, point)
                and all((key in point) == bool(v) for key, v in fixed.items()))

    best, best_value, accepted = None, None, None
    if start is not None:
        seeded = frozenset(k for k in program.keys(data) if fixed.get(k, start.get(k, 0)) == 1)
        accepted = ok(seeded)
        if accepted:
            best, best_value = seeded, program.objective(data, seeded)
    nodes, complete = 0, True
    for point in program.points(data):
        if node_limit is not None and nodes >= node_limit:
            complete = False
            break
        nodes += 1
        if not ok(point):
            continue
        gained = program.objective(data, point)
        if best is None or gained > best_value + IMPROVEMENT_TOL:
            best, best_value = point, gained
    return best, best_value, complete, accepted


def _record(program, solver, data, params, attached, point, objective, complete, seconds):
    """A solver's record of one solve."""
    if complete:
        termination = "OPTIMAL" if point is not None else "INFEASIBLE"
    else:
        termination = "NODE_LIMIT"
    optimal = termination == "OPTIMAL"
    return {
        "solver": solver,
        "status": "FEASIBLE_POINT" if point is not None else "NO_SOLUTION",
        "termination": termination,
        "objective": objective,
        "bound": objective if optimal else None,
        "gap": 0.0 if optimal else None,
        "seconds": seconds,
        "variables": len(program.keys(data)),
        "constraints": program.constraints(data, attached),
        "binding": program.binding(data, params, attached, point),
    }


def search_gap(objective, bound):
    """The search record's gap: |objective - bound| / |objective|, null when unknown."""
    if objective is None or bound is None:
        return None
    if objective == bound:
        return 0.0
    return None if objective == 0 else abs(objective - bound) / abs(objective)


def _solve(request):
    """The v1 one-shot solve: stateless, extra data keys ignored."""
    program = PROGRAMS[request["program"]]
    data = {k: v for k, v in request["data"].items() if k in program.schema["fields"]}
    started = time.perf_counter()
    point, objective, complete, _ = _brute(program, data, request["params"], set())
    record = _record(program, request["solver"], data, request["params"], set(), point,
                     objective, complete, time.perf_counter() - started)
    return program.solution(data, point), record


# -- validation --------------------------------------------------------------------


def _arrow_values(name, ref, spec):
    """Read one Arrow reference whole after checking it; ``bad_data`` naming the field."""
    kind, ndim = spec["type"], len(spec["dims"])
    if not isinstance(ref, dict) or set(ref) != {"path", "sha256", "shape"}:
        raise Refused("bad_data", f"{name!r}: an Arrow reference is exactly "
                      "{path, sha256, shape}")
    path, shape = ref["path"], ref["shape"]
    if (ndim == 0 or not isinstance(path, str) or not os.path.isabs(path)
            or not isinstance(shape, list) or len(shape) != ndim
            or not all(_is_int(n, 0) for n in shape)):
        raise Refused("bad_data", f"{name!r}: an absolute path and one length per "
                      f"dimension of {spec['dims']}")
    try:
        with open(path, "rb") as handle:
            raw = handle.read()
    except OSError as exc:
        raise Refused("bad_data", f"{name!r}: cannot read the Arrow file: {exc}") from None
    if hashlib.sha256(raw).hexdigest() != ref["sha256"]:
        raise Refused("bad_data", f"{name!r}: the Arrow file's sha256 is not the one sent")
    import pyarrow as pa

    try:
        table = pa.ipc.open_file(pa.BufferReader(raw)).read_all()
    except (pa.ArrowInvalid, OSError) as exc:
        raise Refused("bad_data", f"{name!r}: not an Arrow IPC file: {exc}") from None
    if table.column_names != ["values"] or str(table.schema.field("values").type) != (
            ARROW_TYPES[kind]):
        raise Refused("bad_data", f"{name!r}: the Arrow file must be one column 'values' "
                      f"of {kind}")
    column = table.column("values")
    if column.null_count or len(column) != math.prod(shape):
        raise Refused("bad_data", f"{name!r}: the Arrow column must hold "
                      f"{math.prod(shape)} values and no nulls")
    return _nest(column.to_pylist(), shape)


def _check_data(program, data, partial):
    """Refuse ``data`` (``bad_data``) unless it fits the schema; return it, Arrow read."""
    if not isinstance(data, dict):
        raise Refused("bad_request", "data must be an object")
    fields = program.schema["fields"]
    unknown = sorted(set(data) - set(fields))
    if unknown:
        raise Refused("bad_data", f"no field named {unknown[0]!r}")
    missing = sorted(set(fields) - set(data))
    if missing and not partial:
        raise Refused("bad_data", f"the field {missing[0]!r} is required")
    out = {}
    for name, value in data.items():
        spec = fields[name]
        if spec["dims"] and isinstance(value, dict) and set(value) == {"arrow"}:
            value = _arrow_values(name, value["arrow"], spec)
        try:
            _, flat = _nested(value, len(spec["dims"]))
        except ValueError as exc:
            raise Refused("bad_data", f"{name!r} over {spec['dims']} {exc}") from None
        if not all(_element_ok(spec["type"], v) for v in flat):
            raise Refused("bad_data", f"{name!r} must hold {spec['type']} values")
        out[name] = value
    return out


def _check_full(program, data):
    """Refuse full data whose fields disagree on a dimension or repeat a label."""
    fields, lengths = program.schema["fields"], {}
    for name in sorted(data):
        shape, _ = _nested(data[name], len(fields[name]["dims"]))
        for dim, n in zip(fields[name]["dims"], shape):
            if lengths.setdefault(dim, n) != n:
                raise Refused("bad_data", f"{name!r} disagrees on the length of {dim!r}")
    for dim, label in program.schema["dims"].items():
        if label is not None and len(set(data[label])) != len(data[label]):
            raise Refused("bad_data", f"the label field {label!r} must hold distinct values")


def _check_components(program, components):
    if not isinstance(components, dict) or not all(isinstance(v, bool)
                                                   for v in components.values()):
        raise Refused("bad_request", "components must be an object of name to bool")
    for name in components:
        if name not in program.components:
            raise Refused("unknown_component", f"no component named {name!r}")
    return components


def _check_limits(limits):
    """Refuse (``bad_request``) per-solve limits of the wrong key set or type."""
    if not isinstance(limits, dict) or set(limits) - set(LIMIT_KEYS):
        raise Refused("bad_request", f"limits must be an object over {list(LIMIT_KEYS)}")
    ok = {"time_limit": lambda v: _is_number(v) and v > 0,
          "node_limit": lambda v: _is_int(v, 1),
          "mip_rel_gap": lambda v: _is_number(v) and v >= 0,
          "solution_limit": lambda v: _is_int(v, 1)}
    for key, value in limits.items():
        if not ok[key](value):
            raise Refused("bad_request", f"limit {key!r} has the wrong type or range")
    return limits


def _keys(group, group_obj, model, with_values):
    """The ((group, key), value) pairs of one group's aligned keys (and values)."""
    want = {"keys", "values"} if with_values else {"keys"}
    if not isinstance(group_obj, dict) or set(group_obj) != want:
        raise Refused("bad_request", f"a group must be exactly {sorted(want)}")
    keys = group_obj["keys"]
    values = group_obj["values"] if with_values else [None] * len(keys)
    if not isinstance(keys, list) or not isinstance(values, list) or len(keys) != len(values):
        raise Refused("bad_request", "keys and values must be lists of the same length")
    index = model.program.variables[group]["index"]
    known = set(model.keys)
    pairs = []
    for key, value in zip(keys, values):
        if (not isinstance(key, list) or len(key) != len(index)
                or not all(isinstance(e, (str, int)) and not isinstance(e, bool)
                           for e in key)):
            raise Refused("bad_request", f"a {group} key is a list over {index}, got {key!r}")
        if (group, tuple(key)) not in known:
            raise Refused("unknown_key", f"{group} has no key {key!r}")
        if with_values and not _is_number(value):
            raise Refused("bad_request", f"a value must be a number, got {value!r}")
        if with_values and value not in (0, 1):
            raise Refused("bad_value", f"{group} is binary: {value!r} is not 0 or 1")
        pairs.append(((group, tuple(key)), value))
    if len({k for k, _ in pairs}) != len(pairs):
        raise Refused("bad_request", "a key appears twice")
    return pairs


def _addressed(obj, model, with_values):
    if not isinstance(obj, dict):
        raise Refused("bad_request", "variables must be an object of group name to keys")
    pairs = []
    for group, group_obj in obj.items():
        if group not in model.program.variables:
            raise Refused("unknown_group", f"no variable group named {group!r}")
        pairs.extend(_keys(group, group_obj, model, with_values))
    return pairs


# -- live models and the pool --------------------------------------------------------


class Model:
    """One live model: the full data, params, components, fixes, start, last solution.

    ``serial`` names the pooled model; a rebuild replaces the object under the same serial.
    """

    def __init__(self, serial, key, program, data, params, attached):
        self.serial = serial
        self.key = key
        self.program = program
        self.data = {k: v for k, v in data.items()}
        self.params = dict(params)
        self.attached = set(attached)
        self.keys = program.keys(self.data)
        self.fixed = {}
        self.start = None
        self.start_source = None
        self.last = None
        self.last_record = None
        self.last_state = None
        self.last_handle = None
        self.held = False

    @property
    def bytes(self):
        return BYTES_PER_VARIABLE * len(self.keys)

    def state(self):
        return (data_digest(self.data, self.program.schema),
                json.dumps(self.params, sort_keys=True), tuple(sorted(self.attached)))

    def solve_state(self):
        """What a solution depends on: the data, params, components and fixes."""
        return self.state(), tuple(sorted(self.fixed.items(), key=lambda kv: (
            kv[0][0], _key_order(kv[0][1]))))

    def keep(self, point, record, handle):
        """Make ``point`` the last solution, recording the state and handle that found it."""
        self.last, self.last_record = point, record
        self.last_state, self.last_handle = self.solve_state(), handle

    def release(self):
        """A pool hit: every fix released and every start cleared; the last solution kept."""
        self.fixed, self.start, self.start_source = {}, None, None

    def rebuilt(self):
        """The model constructed again from its full current data, under the same serial.

        Carries :data:`CARRIED_BY_A_REBUILD` over (fixes re-applied at their values, the
        start values and their source, the last solution with the state and handle that
        found it, whether it is held), so nothing the caller can observe is lost.
        """
        fresh = Model(self.serial, self.key, self.program, self.data, self.params,
                      self.attached)
        for name in CARRIED_BY_A_REBUILD:
            value = getattr(self, name)
            setattr(fresh, name, dict(value) if isinstance(value, dict) else value)
        return fresh


class FakeWorker:
    """The request loop, the pool, and one switchable behaviour."""

    def __init__(self, mode, once, patch, delay, request_log=None):
        self.mode = mode
        self.request_log = request_log
        self.once = once
        self.patch = patch
        self.delay = delay
        self.deaf = False
        self.settings = None
        self.pool = collections.OrderedDict()  # serial -> model, oldest first
        self.handles = {}
        self.evicted_handles = set()
        self.issued = 0
        self.serials = 0
        self.evictions = 0
        self.on = None
        self.touched = None

    def _reply(self, obj):
        if self.mode == "noisy-stderr":
            sys.stderr.write("n" * STDERR_NOISE_BYTES + "\n")
            sys.stderr.flush()
        _write(json.dumps(obj))

    def _misbehaves(self, modes):
        """Whether this reply is the one that misbehaves (``--once`` consumes the flag)."""
        if self.mode not in modes:
            return False
        if self.once is None:
            return True
        if os.path.exists(self.once):
            return False
        with open(self.once, "w", encoding="utf-8") as flag:
            flag.write(self.mode)
        return True

    def _hello(self, request):
        if self.mode == "slow-hello":
            time.sleep(self.delay)
        if self.mode == "hello-error":
            self._error(request, "environment", "unresolved manifest")
            return
        if self.mode != "v1-only":
            settings = request.get("settings")
            if (set(request) != REQUEST_KEYS["hello"] or not isinstance(settings, dict)
                    or set(settings) != {"pool_models", "pool_bytes"}
                    or not all(_is_int(v, 1) for v in settings.values())):
                self._error(request, "bad_request",
                            "hello must carry settings {pool_models, pool_bytes}")
                return
            self.settings = settings
        if self.mode == "stdout-noise":
            _write("Precompiling DskitJuMP...")
        version = {"bad-protocol": WRONG_PROTOCOL_VERSION,
                   "v1-only": V1_PROTOCOL_VERSION}.get(self.mode, PROTOCOL_VERSION)
        rid = True if self.mode == "bool-id" else request["id"]
        self._reply({"id": rid, "ok": True, "protocol": version, "julia": "fake-1.0",
                     "packages": {"JuMP": "fake", "HiGHS": "fake"}})
        self.deaf = self.mode == "deaf"

    def _misbehave(self, request):
        """Act out the chosen failure for this one-shot solve."""
        if self.mode == "crash":
            os._exit(3)
        if self.mode == "hang":
            time.sleep(3600)
        if self.mode == "malformed":
            _write("this is not json")
            return
        if self.mode == "deep-reply":
            nested = "[" * DEEP_REPLY_DEPTH + "]" * DEEP_REPLY_DEPTH
            _write(f'{{"id": {request["id"]}, "ok": true, "solution": {nested}, '
                   f'"record": {{}}}}')
            return
        solution, record = _solve(request)
        reply = {"id": request["id"], "ok": True, "solution": solution, "record": record}
        if self.mode == "wrong-id":
            reply["id"] = request["id"] + 1000
        elif self.mode == "error-reply":
            reply = {"id": request["id"], "ok": False,
                     "error": {"kind": "solve_failed",
                               "message": "the fake worker refuses on request"}}
        elif self.mode == "bad-record":
            del record["gap"]
        elif self.mode == "patch-record":
            record.update(self.patch)
        elif self.mode == "extra-key":
            reply["debug"] = "not a protocol field"
        elif self.mode == "over-budget":
            reply["solution"] = {"selected": sorted(request["data"]["ids"])}
        elif self.mode == "float-overflow":
            record["objective"] = "__OVERFLOW__"
            _write(json.dumps(reply).replace('"__OVERFLOW__"', "1e999"))
            return
        elif self.mode == "dup-key":
            # A lenient parser keeps the LAST "id" (the right one) and accepts the reply.
            _write('{"id": 999999, ' + json.dumps(reply)[1:])
            return
        self._reply(reply)

    def _error(self, request, kind, message):
        self._reply({"id": request["id"], "ok": False, "error": {"kind": kind, "message": message}})

    @staticmethod
    def _program(name):
        if name not in PROGRAMS:
            raise Refused("unknown_program", f"no program named {name!r}")
        return PROGRAMS[name]

    def _answer(self, request):
        if set(request) != REQUEST_KEYS["solve"]:
            self._error(request, "bad_request",
                        f"a solve request is exactly {sorted(REQUEST_KEYS['solve'])}")
            return
        if request["program"] not in PROGRAMS:
            self._error(request, "unknown_program", f"no program named {request['program']!r}")
            return
        if request["solver"] not in SOLVERS:
            self._error(request, "unknown_solver",
                        f"the environment provides no solver {request['solver']!r}")
            return
        if self.mode == "slow-solve":
            time.sleep(self.delay)
        if self._misbehaves(SOLVE_MODES):
            self._misbehave(request)
            return
        solution, record = _solve(request)
        if self.mode == "big-reply":
            record["binding"] = record["binding"] * BIG_REPLY_ROWS
        self._reply({"id": request["id"], "ok": True, "solution": solution, "record": record})

    # -- the session ops ------------------------------------------------------

    def _timings(self, model, path, *, build=0.0, update=0.0, transfer=0.0, solve=0.0,
                 accepted=None, evicted=0):
        return {"path": path, "build_seconds": build, "update_seconds": update,
                "transfer_seconds": transfer, "solve_seconds": solve,
                "start_source": model.start_source, "start_accepted": accepted,
                "pool": {"models": len(self.pool),
                         "bytes": sum(m.bytes for m in self.pool.values()),
                         "evicted": evicted, "evictions": self.evictions}}

    def _model(self, request):
        handle = request["handle"]
        if handle in self.evicted_handles:
            raise Refused("evicted", f"the model of handle {handle!r} was evicted")
        if handle not in self.handles:
            raise Refused("unknown_handle", f"no open handle {handle!r}")
        model = self.handles[handle]
        self.touched = model  # most recent only once the op succeeds
        return model

    def _swap(self, old, new):
        """Put a rebuilt model where ``old`` was: same serial, pool place and handles."""
        self.pool[old.serial] = new
        for handle, model in self.handles.items():
            if model is old:
                self.handles[handle] = new
        if self.touched is old:
            self.touched = new

    def _describe(self, request):
        program = self._program(request["program"])
        return {"program": program.name, "schema": program.schema,
                "variables": program.variables, "components": program.components,
                "update": self.mode != "no-update"}

    def _evict(self, keep):
        """Evict least recently used models (never ``keep``) past either cap."""
        evicted = 0
        while (len(self.pool) > self.settings["pool_models"]
               or sum(m.bytes for m in self.pool.values()) > self.settings["pool_bytes"]):
            victim = next((m for m in self.pool.values() if m is not keep), None)
            if victim is None:
                break
            del self.pool[victim.serial]
            for handle, model in list(self.handles.items()):
                if model is victim:
                    del self.handles[handle]
                    self.evicted_handles.add(handle)
            evicted += 1
            self.evictions += 1
        return evicted

    def _changed_path(self, changed):
        """The path of an in-place change: ``reused`` when nothing changed."""
        if not changed:
            return "reused"
        return "rebuilt" if self.mode == "no-update" else "updated"

    def _open(self, request):
        program = self._program(request["program"])
        if request["solver"] not in SOLVERS:
            raise Refused("unknown_solver",
                          f"the environment provides no solver {request['solver']!r}")
        started = time.perf_counter()
        data = _check_data(program, request["data"], partial=False)
        _check_full(program, data)
        components = _check_components(program, request["components"])
        attached = {name for name, on in components.items() if on}
        program.check_params(request["params"], attached)
        if not isinstance(request["solver_options"], dict):
            raise Refused("bad_request", "solver_options must be an object")
        transfer = time.perf_counter() - started
        key = (program.name, shape_signature(data, program.schema), request["solver"],
               json.dumps(request["solver_options"], sort_keys=True))
        model = next((m for m in self.pool.values() if m.key == key and not m.held), None)
        build = update = 0.0
        started = time.perf_counter()
        if model is None:
            self.serials += 1
            model = Model(self.serials, key, program, data, request["params"], attached)
            self.pool[model.serial] = model
            path, build = "built", time.perf_counter() - started
        else:
            before = model.state()
            model.data, model.params, model.attached = data, dict(request["params"]), attached
            path = self._changed_path(model.state() != before)
            if path == "rebuilt":
                fresh = model.rebuilt()
                self._swap(model, fresh)
                model = fresh
                build = time.perf_counter() - started
                started = time.perf_counter()
            model.release()
            update = time.perf_counter() - started
            self.pool.move_to_end(model.serial)
        model.held = True
        evicted = self._evict(keep=model)
        self.issued += 1
        handle = f"h{self.issued}"
        self.handles[handle] = model
        return {"handle": handle, "signature": key[1],
                "digest": data_digest(model.data, program.schema),
                "timings": self._timings(model, path, build=build, update=update,
                                         transfer=transfer, evicted=evicted)}

    def _update(self, request):
        model = self._model(request)
        program = model.program
        started = time.perf_counter()
        data = {**model.data, **_check_data(program, request["data"], partial=True)}
        _check_full(program, data)
        components = _check_components(program, request["components"])
        attached = (model.attached | {n for n, on in components.items() if on}) - {
            n for n, on in components.items() if not on}
        params = model.params if request["params"] is None else request["params"]
        program.check_params(params, attached)
        transfer = time.perf_counter() - started
        if shape_signature(data, program.schema) != model.key[1]:
            raise Refused("shape_changed", "the update changes the model's shape: open a "
                          "new handle")
        before = model.state()
        build = update = 0.0
        started = time.perf_counter()
        model.data, model.params, model.attached = data, dict(params), attached
        path = self._changed_path(model.state() != before)
        if path == "rebuilt":
            fresh = model.rebuilt()
            self._swap(model, fresh)
            model = fresh
            build = time.perf_counter() - started
        elif path == "updated":
            update = time.perf_counter() - started
        return {"handle": request["handle"], "signature": model.key[1],
                "digest": data_digest(model.data, program.schema),
                "timings": self._timings(model, path, build=build, update=update,
                                         transfer=transfer)}

    def _fix(self, request):
        model = self._model(request)
        pairs = _addressed(request["values"], model, with_values=True)
        started = time.perf_counter()
        model.fixed.update({key: int(value) for key, value in pairs})
        return {"handle": request["handle"], "fixed": len(model.fixed),
                "timings": self._timings(model, "reused",
                                         update=time.perf_counter() - started)}

    def _free(self, request):
        model = self._model(request)
        targets = request["targets"]
        pairs = None if targets == "all" else _addressed(targets, model, with_values=False)
        started = time.perf_counter()
        if pairs is None:
            model.fixed = {}
        else:
            for key, _ in pairs:
                model.fixed.pop(key, None)
        return {"handle": request["handle"], "fixed": len(model.fixed),
                "timings": self._timings(model, "reused",
                                         update=time.perf_counter() - started)}

    def _start(self, request):
        model = self._model(request)
        source, values = request["source"], request["values"]
        if source not in START_SOURCES:
            raise Refused("bad_request", f"source must be one of {list(START_SOURCES)}")
        if source != "values" and values != {}:
            raise Refused("bad_request", "values must be {} unless source is 'values'")
        if source == "last" and model.last is None:
            raise Refused("no_solution", "the model has no last solution to start from")
        pairs = _addressed(values, model, with_values=True) if source == "values" else None
        started = time.perf_counter()
        if source == "last":
            model.start = {k: int(k in model.last) for k in model.keys}
            model.start_source = "last"
        elif source == "values":
            model.start = {key: int(value) for key, value in pairs}
            model.start_source = "values"
        else:
            model.start, model.start_source = None, None
        count = 0 if model.start is None else len(model.start)
        return {"handle": request["handle"], "started": count,
                "timings": self._timings(model, "reused",
                                         update=time.perf_counter() - started)}

    def _handle_solve(self, request):
        model = self._model(request)
        limits = _check_limits(request["limits"])
        if self.mode == "slow-solve":
            time.sleep(self.delay)
        program = model.program
        started = time.perf_counter()
        point, objective, complete, accepted = _brute(
            program, model.data, model.params, model.attached, fixed=model.fixed,
            start=model.start, node_limit=limits.get("node_limit"))
        seconds = time.perf_counter() - started
        record = _record(program, model.key[2], model.data, model.params, model.attached,
                         point, objective, complete, seconds)
        if point is not None:
            model.keep(point, record, request["handle"])
        return {"handle": request["handle"], "solution": program.solution(model.data, point),
                "record": record,
                "timings": self._timings(model, "reused", solve=seconds, accepted=accepted)}

    def _search_request(self, request):
        """Refuse (``bad_request``/``unknown_search``) a search request of the wrong shape."""
        if request["search"] not in SEARCHES:
            raise Refused("unknown_search", f"no search named {request['search']!r}")
        params, seed, limits = request["params"], request["seed"], request["limits"]
        if (not isinstance(params, dict) or set(params) != set(SEARCHES[request["search"]])
                or not all(_is_int(v, 1) for v in params.values())):
            raise Refused("bad_request", "fix_and_reoptimise takes exactly {free, stall}, "
                          "integers >= 1")
        if not _is_int(seed, 0) or seed >= SEED_BOUND:
            raise Refused("bad_request", f"seed must be an integer in [0, {SEED_BOUND})")
        if (not isinstance(limits, dict) or set(limits) - set(SEARCH_LIMIT_KEYS)
                or ("rounds" in limits and not _is_int(limits["rounds"], 1))
                or ("time_limit" in limits and not (_is_number(limits["time_limit"])
                                                    and limits["time_limit"] > 0))):
            raise Refused("bad_request", f"limits must be an object over "
                          f"{list(SEARCH_LIMIT_KEYS)}")
        return params, seed, limits, _check_limits(request["round_limits"])

    def _search(self, request):
        """The base loop and the one reference search (see the module docstring)."""
        model = self._model(request)
        params, seed, limits, round_limits = self._search_request(request)
        program, data = model.program, model.data
        rng = SplitMix64(seed)
        began = time.perf_counter()
        solving = changing = 0.0

        def solve(fixed, start):
            nonlocal solving
            started = time.perf_counter()
            point, objective, complete, accepted = _brute(
                program, data, model.params, model.attached, fixed=fixed, start=start,
                node_limit=round_limits.get("node_limit"))
            seconds = time.perf_counter() - started
            solving += seconds
            record = _record(program, model.key[2], data, model.params, model.attached,
                             point, objective, complete, seconds)
            return point, objective, record, accepted, seconds

        caller = dict(model.fixed)
        first_accepted = None
        incumbent, found = model.last, model.last_record
        stands = (incumbent is not None and model.start is None
                  and model.last_handle == request["handle"]
                  and model.last_state == model.solve_state())
        if not stands:
            incumbent, _, found, first_accepted, _ = solve(caller, model.start)
        bound = found["bound"]  # the whole model's, never a round's neighbourhood
        trajectory = []
        if incumbent is None:
            stop = SEARCH_NO_INCUMBENT
        else:
            best = program.objective(data, incumbent)
            pool = [k for k in model.keys if k not in caller]
            stall = 0
            while True:
                if stall >= params["stall"]:
                    stop = SEARCH_DONE
                    break
                if "rounds" in limits and len(trajectory) >= limits["rounds"]:
                    stop = SEARCH_ROUND_LIMIT
                    break
                if ("time_limit" in limits
                        and time.perf_counter() - began >= limits["time_limit"]):
                    stop = SEARCH_TIME_LIMIT
                    break
                started = time.perf_counter()
                hood = set(rng.draw(pool, min(params["free"], len(pool))))
                values = {k: int(k in incumbent) for k in model.keys}
                fixed = {**{k: v for k, v in values.items() if k not in hood}, **caller}
                changing += time.perf_counter() - started
                point, objective, round_record, _, seconds = solve(fixed, values)
                better = point is not None and objective > best + IMPROVEMENT_TOL
                trajectory.append({"round": len(trajectory) + 1, "objective": objective,
                                   "bound": round_record["bound"], "seconds": seconds,
                                   "accepted": better, "neighbourhood": len(hood)})
                if point is None:
                    stop = SEARCH_ROUND_NO_POINT
                    break
                if better:
                    incumbent, best, found, stall = point, objective, round_record, 0
                else:
                    stall += 1
        objective = None if incumbent is None else program.objective(data, incumbent)
        record = {**found, "status": "FEASIBLE_POINT" if incumbent is not None
                  else "NO_SOLUTION", "termination": stop, "objective": objective,
                  "bound": bound, "gap": search_gap(objective, bound),
                  "seconds": time.perf_counter() - began}
        if incumbent is not None:
            model.keep(incumbent, record, request["handle"])
        return {"handle": request["handle"], "solution": program.solution(data, incumbent),
                "record": record, "trajectory": trajectory,
                "timings": self._timings(model, "reused", update=changing, solve=solving,
                                         accepted=first_accepted)}

    def _close(self, request):
        handle = request["handle"]
        if handle in self.evicted_handles:
            self.evicted_handles.discard(handle)
            return {"handle": handle, "pooled": False}
        if handle not in self.handles:
            raise Refused("unknown_handle", f"no open handle {handle!r}")
        model = self.handles.pop(handle)
        model.held, self.touched = False, model
        return {"handle": handle, "pooled": True}

    def _session(self, request, op):
        """Answer one session op: its body, a well-formed error, or a chosen misbehaviour."""
        ops = {"describe": self._describe, "open": self._open, "update": self._update,
               "fix": self._fix, "free": self._free, "start": self._start,
               "handle-solve": self._handle_solve, "search": self._search,
               "close": self._close}
        self.touched = None
        try:
            if set(request) != REQUEST_KEYS[op]:
                raise Refused("bad_request", f"a {op} request is exactly "
                              f"{sorted(REQUEST_KEYS[op])}")
            body = ops[op](request)
        except Refused as refused:
            self._error(request, refused.kind, str(refused))
            return
        if self.touched is not None and self.touched.serial in self.pool:
            self.pool.move_to_end(self.touched.serial)  # every successful handle op, close too
        self._reply({"id": request["id"], "ok": True, **self._misbehaved(request, body)})

    def _misbehaved(self, request, body):
        """The body, changed by the chosen session misbehaviour when this is its reply."""
        if self.on is not None and self.on != request["op"]:
            return body
        if self.mode == "bad-timings" and "timings" in body and self._misbehaves(
                TIMINGS_MODES):
            body["timings"].update(self.patch)
        elif self.mode == "wrong-handle" and "handle" in request and (
                self._misbehaves(TIMINGS_MODES)):
            body["handle"] = "h999"
        elif self.mode == "bad-digest" and "digest" in body and self._misbehaves(
                TIMINGS_MODES):
            body["digest"] = data_digest({"ids": []}, PROGRAMS[PROGRAM].schema)
        elif self.mode == "bad-trajectory" and body.get("trajectory") and (
                self._misbehaves(TIMINGS_MODES)):
            body["trajectory"][0].update(self.patch)
        elif self.mode == "extra-round" and body.get("trajectory") and (
                self._misbehaves(TIMINGS_MODES)):
            rows = body["trajectory"]
            rows.append({**rows[-1], "round": len(rows) + 1, "accepted": False})
        elif self.mode == "patch-search" and "trajectory" in body and (
                self._misbehaves(TIMINGS_MODES)):
            body["record"] = {**body["record"], **self.patch}
        return body

    def serve(self):
        for line in sys.stdin:
            if self.request_log is not None:
                with open(self.request_log, "a", encoding="utf-8") as log:
                    log.write(line)
            request = json.loads(line)
            op = request["op"]
            if _lone_surrogate(request):
                self._error(request, "bad_request", "a string holds a lone surrogate: not "
                            "UTF-8 text")
            elif op == "hello":
                self._hello(request)
                if self.deaf:
                    time.sleep(3600)
            elif op == "solve" and "handle" not in request:
                self._answer(request)
            elif op == "solve":
                self._session(request, "handle-solve")
            elif op in ("describe", "open", "update", "fix", "free", "start", "search",
                        "close"):
                self._session(request, op)
            elif op == "shutdown":
                self._reply({"id": request["id"], "ok": True})
                return
            else:
                self._error(request, "unknown_op", f"no op {op!r}")
        if self.mode == "linger":
            time.sleep(60)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--mode", choices=MODES, default="ok")
    parser.add_argument("--once", default=None, help="flag file: misbehave only until it exists")
    parser.add_argument("--patch", default="{}",
                        help="patch-record/bad-timings/patch-search: JSON object")
    parser.add_argument("--delay", type=float, default=3600.0,
                        help="slow-hello/slow-solve: seconds to sleep")
    parser.add_argument("--on", default=None,
                        help="aim the session misbehaviours at this request op only")
    parser.add_argument("--pidfile", default=None, help="write this process's id on start")
    parser.add_argument("--request-log", default=None, help="append every request line here")
    args = parser.parse_args(argv)
    if args.pidfile is not None:
        with open(args.pidfile, "w", encoding="utf-8") as out:
            out.write(str(os.getpid()))
    worker = FakeWorker(args.mode, args.once, json.loads(args.patch), args.delay,
                        args.request_log)
    worker.on = args.on
    worker.serve()


if __name__ == "__main__":
    main()
