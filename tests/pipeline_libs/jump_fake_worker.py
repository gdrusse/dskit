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

It knows ONE program, ``budgeted_select`` (a 0/1 knapsack solved by brute force, so the
answers are real), under ONE solver, ``HiGHS``; any other program or solver gets the error
reply the real worker sends. Fixes and starts genuinely change the answer: a fixed item is
forced in or out, and under a ``node_limit`` the enumeration stops early with the start (if
feasible) as its first incumbent. The one optional component, ``max_items``, caps the
count at ``params.max_items``. The one-shot ``solve`` is v1 unchanged and ignores extra
``data`` keys, so a test can pad a payload.

``open`` and ``update`` check their ``data`` against the declared schema on this side too
(field set, element types, one length per dimension, distinct labels, finite numbers), and
read an Arrow reference (pyarrow, imported only then) after checking its sha256. ONE
search is registered, ``fix_and_reoptimise`` (params exactly ``{free, stall}``): each round
frees ``free`` variables drawn by the spec's seeded draw (:class:`SplitMix64` and a
partial Fisher-Yates over the caller's unfixed keys in ascending order) from those the
caller has not fixed, fixes the rest at the incumbent, re-solves from the incumbent under
the round limits, and accepts a strictly better objective; it is done after ``stall``
rounds in a row without one. The same seed gives the same trajectory in any language
that follows the spec. A search takes the model's last solution as its incumbent only
when that solution was found under the model's current data, params, components and
fixes; otherwise it solves first.

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

``--on OP`` limits these four to the replies of request op ``OP`` (``solve`` meaning a
handle solve), so a test can aim one at an ``update`` or a ``search``.

Answering correctly throughout:

``ok``             the default
``noisy-stderr``   write a megabyte to stderr before every reply
``big-reply``      a record with many binding rows (a reply line of megabytes)
``linger``         on stdin EOF without ``shutdown``, sleep instead of exiting
``slow-solve``     sleep ``--delay`` seconds before answering every solve
``no-update``      the program has no ``update!`` hook: every in-place change rebuilds

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

PROGRAM = "budgeted_select"
SOLVERS = ("HiGHS",)

#: The program's declared data schema (``describe``).
SCHEMA = {
    "fields": {
        "ids": {"type": "string", "dims": ["item"]},
        "cost": {"type": "float64", "dims": ["item"]},
        "value": {"type": "float64", "dims": ["item"]},
    },
    "dims": {"item": "ids"},
}

#: The program's addressable variable groups.
VARIABLES = {"select": {"index": ["item"], "domain": "binary"}}

#: The program's optional constraint groups.
COMPONENTS = ["max_items"]

#: The fake's estimate of one model's memory, per item.
BYTES_PER_ITEM = 1000

#: The per-solve limits the protocol names.
LIMIT_KEYS = ("time_limit", "node_limit", "mip_rel_gap", "solution_limit")

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
                 "extra-round")
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

#: A seed is an integer in [0, SEED_BOUND).
SEED_BOUND = 1 << 53

#: What a schema type is as an Arrow column type name (``str(pyarrow type)``).
ARROW_TYPES = {"float64": "double", "int64": "int64", "string": "string", "bool": "bool"}


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


# -- the canonical digests (the ADR's byte layout, restated nowhere else in the fake) ---


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


def data_digest(data, schema=SCHEMA):
    """The lowercase hex sha256 of ``data``'s canonical form (one-dimensional fields only)."""
    tags = {"float64": b"f", "int64": b"i", "string": b"s", "bool": b"b"}
    out = [b"dskit-data-v1\n"]
    for field in sorted(data, key=lambda k: k.encode("utf-8")):
        kind = schema["fields"][field]["type"]
        values = data[field]
        out += [_name(field), tags[kind], struct.pack("<I", 1), struct.pack("<Q", len(values))]
        out += [_element(kind, v) for v in values]
    return hashlib.sha256(b"".join(out)).hexdigest()


def shape_signature(data, schema=SCHEMA):
    """The default signature: every dimension's name, length and labels."""
    out = [b"dskit-shape-v1\n"]
    for dim in sorted(schema["dims"], key=lambda k: k.encode("utf-8")):
        label = schema["dims"][dim]
        values = data[label]
        out += [_name(dim), struct.pack("<Q", len(values)), b"\x01"]
        out += [_element(schema["fields"][label]["type"], v) for v in values]
    return "shape:" + hashlib.sha256(b"".join(out)).hexdigest()


# -- the knapsack ----------------------------------------------------------------


def _knapsack(ids, cost, value, budget, fixed=None, start=None, node_limit=None,
              max_items=None):
    """Brute force under fixes, a count cap and a node limit; the start seeds the incumbent.

    Returns ``(chosen or None, objective or None, complete, start_accepted)``; ties go to
    the first subset in (size, sorted ids) order.
    """
    fixed = fixed or {}

    def feasible(subset):
        return (sum(cost[i] for i in subset) <= budget + 1e-9
                and (max_items is None or len(subset) <= max_items)
                and all((i in subset) == bool(v) for i, v in fixed.items()))

    best, best_value, accepted = None, None, None
    if start is not None:
        seeded = tuple(sorted(i for i in ids if fixed.get(i, start.get(i, 0)) == 1))
        accepted = feasible(seeded)
        if accepted:
            best, best_value = seeded, sum(value[i] for i in seeded)
    nodes, complete = 0, True
    for subset in itertools.chain.from_iterable(
            itertools.combinations(sorted(ids), size) for size in range(len(ids) + 1)):
        if node_limit is not None and nodes >= node_limit:
            complete = False
            break
        nodes += 1
        if not feasible(subset):
            continue
        gained = sum(value[i] for i in subset)
        if best is None or gained > best_value + 1e-12:
            best, best_value = subset, gained
    return (None if best is None else list(best)), best_value, complete, accepted


def _record(solver, ids, budget, chosen, objective, complete, cost, seconds):
    if complete:
        termination = "OPTIMAL" if chosen is not None else "INFEASIBLE"
    else:
        termination = "NODE_LIMIT"
    slack = None if chosen is None else budget - sum(cost[i] for i in chosen)
    optimal = termination == "OPTIMAL"
    return {
        "solver": solver,
        "status": "FEASIBLE_POINT" if chosen is not None else "NO_SOLUTION",
        "termination": termination,
        "objective": objective,
        "bound": objective if optimal else None,
        "gap": 0.0 if optimal else None,
        "seconds": seconds,
        "variables": len(ids),
        "constraints": 1,
        "binding": [{"name": "budget", "rows": 1,
                     "binding": int(slack is not None and abs(slack) <= 1e-9),
                     "min_slack": slack}],
    }


def _solve(request):
    """The v1 one-shot solve: stateless, extra data keys ignored."""
    data, params = request["data"], request["params"]
    ids = list(data["ids"])
    cost = dict(zip(ids, data["cost"]))
    value = dict(zip(ids, data["value"]))
    started = time.perf_counter()
    chosen, objective, complete, _ = _knapsack(ids, cost, value, float(params["budget"]))
    record = _record(request["solver"], ids, float(params["budget"]), chosen, objective,
                     complete, cost, time.perf_counter() - started)
    return {"selected": chosen}, record


# -- validation --------------------------------------------------------------------


def _is_int(value, low=None):
    return isinstance(value, int) and not isinstance(value, bool) and (low is None
                                                                       or value >= low)


def _is_number(value):
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value))


def _element_ok(kind, value):
    if kind == "float64":
        return _is_number(value)
    if kind == "int64":
        return _is_int(value) and -(1 << 63) <= value < (1 << 63)
    if kind == "string":
        return isinstance(value, str)
    return isinstance(value, bool)


def _arrow_values(name, ref, kind):
    """Read one Arrow reference whole after checking it; ``bad_data`` naming the field."""
    if not isinstance(ref, dict) or set(ref) != {"path", "sha256", "shape"}:
        raise Refused("bad_data", f"{name!r}: an Arrow reference is exactly "
                      "{path, sha256, shape}")
    path, shape = ref["path"], ref["shape"]
    if (not isinstance(path, str) or not os.path.isabs(path) or not isinstance(shape, list)
            or len(shape) != 1 or not _is_int(shape[0], 0)):
        raise Refused("bad_data", f"{name!r}: an absolute path and a shape over 'item'")
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
    if column.null_count or len(column) != shape[0]:
        raise Refused("bad_data", f"{name!r}: the Arrow column must hold {shape[0]} values "
                      "and no nulls")
    return column.to_pylist()


def _check_data(data, partial):
    """Refuse ``data`` (``bad_data``) unless it fits the schema; return it, Arrow read."""
    if not isinstance(data, dict):
        raise Refused("bad_request", "data must be an object")
    fields = SCHEMA["fields"]
    unknown = sorted(set(data) - set(fields))
    if unknown:
        raise Refused("bad_data", f"no field named {unknown[0]!r}")
    missing = sorted(set(fields) - set(data))
    if missing and not partial:
        raise Refused("bad_data", f"the field {missing[0]!r} is required")
    out = {}
    for name, values in data.items():
        kind = fields[name]["type"]
        if isinstance(values, dict) and set(values) == {"arrow"}:
            values = _arrow_values(name, values["arrow"], kind)
        if not isinstance(values, list):
            raise Refused("bad_data", f"{name!r} must be a list over 'item'")
        if not all(_element_ok(kind, v) for v in values):
            raise Refused("bad_data", f"{name!r} must hold {kind} values")
        out[name] = values
    return out


def _check_full(data):
    lengths = {len(v) for v in data.values()}
    if len(lengths) != 1:
        raise Refused("bad_data", "every field over 'item' must have the same length")
    if len(set(data["ids"])) != len(data["ids"]):
        raise Refused("bad_data", "the label field 'ids' must hold distinct values")


def _check_components(components, partial):
    if not isinstance(components, dict) or not all(isinstance(v, bool)
                                                   for v in components.values()):
        raise Refused("bad_request", "components must be an object of name to bool")
    for name in components:
        if name not in COMPONENTS:
            raise Refused("unknown_component", f"no component named {name!r}")
    return components


def _check_params(params, attached):
    if not isinstance(params, dict) or not _is_number(params.get("budget")):
        raise Refused("bad_request", "params must carry a finite budget")
    if "max_items" in attached and not _is_int(params.get("max_items"), 0):
        raise Refused("bad_request", "the max_items component needs params.max_items")


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


def _keys(group_obj, model, with_values):
    """The (id, value) pairs of one group's aligned keys (and values)."""
    want = {"keys", "values"} if with_values else {"keys"}
    if not isinstance(group_obj, dict) or set(group_obj) != want:
        raise Refused("bad_request", f"a group must be exactly {sorted(want)}")
    keys = group_obj["keys"]
    values = group_obj["values"] if with_values else [None] * len(keys)
    if not isinstance(keys, list) or not isinstance(values, list) or len(keys) != len(values):
        raise Refused("bad_request", "keys and values must be lists of the same length")
    pairs = []
    for key, value in zip(keys, values):
        if not isinstance(key, list) or len(key) != 1 or not isinstance(key[0], str):
            raise Refused("bad_request", f"a select key is [id], got {key!r}")
        if key[0] not in model.data["ids"]:
            raise Refused("unknown_key", f"select has no key {key!r}")
        if with_values and not _is_number(value):
            raise Refused("bad_request", f"a value must be a number, got {value!r}")
        if with_values and value not in (0, 1):
            raise Refused("bad_value", f"select is binary: {value!r} is not 0 or 1")
        pairs.append((key[0], value))
    if len({k for k, _ in pairs}) != len(pairs):
        raise Refused("bad_request", "a key appears twice")
    return pairs


def _addressed(obj, model, with_values):
    if not isinstance(obj, dict):
        raise Refused("bad_request", "variables must be an object of group name to keys")
    pairs = []
    for group, group_obj in obj.items():
        if group not in VARIABLES:
            raise Refused("unknown_group", f"no variable group named {group!r}")
        pairs.extend(_keys(group_obj, model, with_values))
    return pairs


# -- live models and the pool --------------------------------------------------------


class Model:
    """One live model: the full data, params, components, fixes, start, last solution."""

    def __init__(self, key, data, params, components):
        self.key = key
        self.data = {k: list(v) for k, v in data.items()}
        self.params = dict(params)
        self.attached = {name for name, on in components.items() if on}
        self.fixed = {}
        self.start = None
        self.start_source = None
        self.last = None
        self.last_record = None
        self.last_state = None
        self.held = False

    @property
    def bytes(self):
        return BYTES_PER_ITEM * len(self.data["ids"])

    def state(self):
        return (data_digest(self.data), json.dumps(self.params, sort_keys=True),
                tuple(sorted(self.attached)))

    def solve_state(self):
        """What a solution depends on: the data, params, components and fixes."""
        return self.state(), tuple(sorted(self.fixed.items()))

    def keep(self, solution, record):
        """Make ``solution`` the last one, recording the state it was found under."""
        self.last, self.last_record, self.last_state = solution, record, self.solve_state()

    def release(self):
        """A pool hit: every fix released and every start cleared; the last solution kept."""
        self.fixed, self.start, self.start_source = {}, None, None


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
        self.pool = collections.OrderedDict()  # id(model) -> model, oldest first
        self.handles = {}
        self.evicted_handles = set()
        self.issued = 0
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

    def _known(self, request):
        if request["program"] != PROGRAM:
            raise Refused("unknown_program", f"no program named {request['program']!r}")

    def _answer(self, request):
        if set(request) != REQUEST_KEYS["solve"]:
            self._error(request, "bad_request",
                        f"a solve request is exactly {sorted(REQUEST_KEYS['solve'])}")
            return
        if request["program"] != PROGRAM:
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

    def _describe(self, request):
        self._known(request)
        return {"program": PROGRAM, "schema": SCHEMA, "variables": VARIABLES,
                "components": COMPONENTS, "update": self.mode != "no-update"}

    def _evict(self, keep):
        """Evict least recently used models (never ``keep``) past either cap."""
        evicted = 0
        while (len(self.pool) > self.settings["pool_models"]
               or sum(m.bytes for m in self.pool.values()) > self.settings["pool_bytes"]):
            victim = next((m for m in self.pool.values() if m is not keep), None)
            if victim is None:
                break
            del self.pool[id(victim)]
            for handle, model in list(self.handles.items()):
                if model is victim:
                    del self.handles[handle]
                    self.evicted_handles.add(handle)
            evicted += 1
            self.evictions += 1
        return evicted

    def _open(self, request):
        self._known(request)
        if request["solver"] not in SOLVERS:
            raise Refused("unknown_solver",
                          f"the environment provides no solver {request['solver']!r}")
        started = time.perf_counter()
        data = _check_data(request["data"], partial=False)
        _check_full(data)
        components = _check_components(request["components"], partial=False)
        attached = {name for name, on in components.items() if on}
        _check_params(request["params"], attached)
        if not isinstance(request["solver_options"], dict):
            raise Refused("bad_request", "solver_options must be an object")
        transfer = time.perf_counter() - started
        key = (PROGRAM, shape_signature(data), request["solver"],
               json.dumps(request["solver_options"], sort_keys=True))
        model = next((m for m in self.pool.values() if m.key == key and not m.held), None)
        started = time.perf_counter()
        if model is None:
            model, path = Model(key, data, request["params"], components), "built"
            self.pool[id(model)] = model
        else:
            fresh = Model(key, data, request["params"], components)
            path = ("reused" if fresh.state() == model.state()
                    else "rebuilt" if self.mode == "no-update" else "updated")
            model.data, model.params, model.attached = fresh.data, fresh.params, fresh.attached
            model.release()
            self.pool.move_to_end(id(model))
        seconds = time.perf_counter() - started
        model.held = True
        evicted = self._evict(keep=model)
        self.issued += 1
        handle = f"h{self.issued}"
        self.handles[handle] = model
        build = seconds if path in ("built", "rebuilt") else 0.0
        update = seconds if path == "updated" else 0.0
        return {"handle": handle, "signature": key[1], "digest": data_digest(model.data),
                "timings": self._timings(model, path, build=build, update=update,
                                         transfer=transfer, evicted=evicted)}

    def _update(self, request):
        model = self._model(request)
        started = time.perf_counter()
        data = {**model.data, **_check_data(request["data"], partial=True)}
        _check_full(data)
        components = _check_components(request["components"], partial=True)
        attached = (model.attached | {n for n, on in components.items() if on}) - {
            n for n, on in components.items() if not on}
        params = model.params if request["params"] is None else request["params"]
        _check_params(params, attached)
        transfer = time.perf_counter() - started
        if shape_signature(data) != model.key[1]:
            raise Refused("shape_changed", "the update changes the model's shape: open a "
                          "new handle")
        before = model.state()
        started = time.perf_counter()
        model.data, model.params, model.attached = data, dict(params), attached
        seconds = time.perf_counter() - started
        path = ("reused" if model.state() == before
                else "rebuilt" if self.mode == "no-update" else "updated")
        build = seconds if path == "rebuilt" else 0.0
        update = seconds if path == "updated" else 0.0
        return {"handle": request["handle"], "signature": model.key[1],
                "digest": data_digest(model.data),
                "timings": self._timings(model, path, build=build, update=update,
                                         transfer=transfer)}

    def _fix(self, request):
        model = self._model(request)
        pairs = _addressed(request["values"], model, with_values=True)
        model.fixed.update({key: int(value) for key, value in pairs})
        return {"handle": request["handle"], "fixed": len(model.fixed),
                "timings": self._timings(model, "reused")}

    def _free(self, request):
        model = self._model(request)
        targets = request["targets"]
        if targets == "all":
            model.fixed = {}
        else:
            for key, _ in _addressed(targets, model, with_values=False):
                model.fixed.pop(key, None)
        return {"handle": request["handle"], "fixed": len(model.fixed),
                "timings": self._timings(model, "reused")}

    def _start(self, request):
        model = self._model(request)
        source, values = request["source"], request["values"]
        if source not in ("last", "values", "none"):
            raise Refused("bad_request", "source must be 'last', 'values' or 'none'")
        if source != "values" and values != {}:
            raise Refused("bad_request", "values must be {} unless source is 'values'")
        if source == "last":
            if model.last is None:
                raise Refused("no_solution", "the model has no last solution to start from")
            model.start, model.start_source = dict(model.last), "last"
        elif source == "values":
            pairs = _addressed(values, model, with_values=True)
            model.start = {key: int(value) for key, value in pairs}
            model.start_source = "values"
        else:
            model.start, model.start_source = None, None
        started = 0 if model.start is None else len(model.start)
        return {"handle": request["handle"], "started": started,
                "timings": self._timings(model, "reused")}

    def _handle_solve(self, request):
        model = self._model(request)
        limits = _check_limits(request["limits"])
        if self.mode == "slow-solve":
            time.sleep(self.delay)
        ids = model.data["ids"]
        cost = dict(zip(ids, model.data["cost"]))
        value = dict(zip(ids, model.data["value"]))
        budget = float(model.params["budget"])
        max_items = model.params["max_items"] if "max_items" in model.attached else None
        started = time.perf_counter()
        chosen, objective, complete, accepted = _knapsack(
            ids, cost, value, budget, fixed=model.fixed, start=model.start,
            node_limit=limits.get("node_limit"), max_items=max_items)
        seconds = time.perf_counter() - started
        record = _record(model.key[2], ids, budget, chosen, objective, complete, cost,
                         seconds)
        if chosen is not None:
            model.keep({i: int(i in chosen) for i in ids}, record)
        return {"handle": request["handle"], "solution": {"selected": chosen},
                "record": record,
                "timings": self._timings(model, "reused", solve=seconds, accepted=accepted)}

    def _search(self, request):
        """The base loop and the one reference search (see the module docstring)."""
        model = self._model(request)
        if request["search"] not in SEARCHES:
            raise Refused("unknown_search", f"no search named {request['search']!r}")
        params, seed = request["params"], request["seed"]
        if (not isinstance(params, dict) or set(params) != set(SEARCHES[request["search"]])
                or not all(_is_int(v, 1) for v in params.values())):
            raise Refused("bad_request", "fix_and_reoptimise takes exactly {free, stall}, "
                          "integers >= 1")
        if not _is_int(seed, 0) or seed >= SEED_BOUND:
            raise Refused("bad_request", f"seed must be an integer in [0, {SEED_BOUND})")
        limits = request["limits"]
        if (not isinstance(limits, dict) or set(limits) - set(SEARCH_LIMIT_KEYS)
                or ("rounds" in limits and not _is_int(limits["rounds"], 1))
                or ("time_limit" in limits and not (_is_number(limits["time_limit"])
                                                    and limits["time_limit"] > 0))):
            raise Refused("bad_request", f"limits must be an object over "
                          f"{list(SEARCH_LIMIT_KEYS)}")
        node_limit = _check_limits(request["round_limits"]).get("node_limit")
        ids = model.data["ids"]
        cost = dict(zip(ids, model.data["cost"]))
        value = dict(zip(ids, model.data["value"]))
        budget = float(model.params["budget"])
        max_items = model.params["max_items"] if "max_items" in model.attached else None
        rng = SplitMix64(seed)
        began = time.perf_counter()
        solving = 0.0

        def solve(fixed, start):
            nonlocal solving
            started = time.perf_counter()
            chosen, objective, complete, accepted = _knapsack(
                ids, cost, value, budget, fixed=fixed, start=start, node_limit=node_limit,
                max_items=max_items)
            seconds = time.perf_counter() - started
            solving += seconds
            record = _record(model.key[2], ids, budget, chosen, objective, complete, cost,
                             seconds)
            return chosen, objective, record, accepted, seconds

        caller = dict(model.fixed)
        first_accepted = None
        incumbent, record = model.last, model.last_record
        if (incumbent is None or model.start is not None
                or model.last_state != model.solve_state()):
            chosen, objective, record, first_accepted, _ = solve(caller, model.start)
            incumbent = None if chosen is None else {i: int(i in chosen) for i in ids}
        trajectory = []
        if incumbent is not None:
            best = sum(value[i] for i in ids if incumbent[i])
            pool = sorted((i for i in ids if i not in caller), key=lambda k: k.encode("utf-8"))
            stall = 0
            while stall < params["stall"]:
                if "rounds" in limits and len(trajectory) >= limits["rounds"]:
                    break
                if ("time_limit" in limits
                        and time.perf_counter() - began >= limits["time_limit"]):
                    break
                hood = set(rng.draw(pool, min(params["free"], len(pool))))
                fixed = {**{i: incumbent[i] for i in ids if i not in hood}, **caller}
                chosen, objective, found, _, seconds = solve(fixed, incumbent)
                better = chosen is not None and objective > best + 1e-12
                trajectory.append({"round": len(trajectory) + 1, "objective": objective,
                                   "bound": found["bound"], "seconds": seconds,
                                   "accepted": better, "neighbourhood": len(hood)})
                if chosen is None:
                    break
                if better:
                    incumbent, best, record, stall = (
                        {i: int(i in chosen) for i in ids}, objective, found, 0)
                else:
                    stall += 1
            model.keep(incumbent, record)
        record = {**record, "seconds": time.perf_counter() - began}
        selected = None if incumbent is None else [i for i in sorted(ids) if incumbent[i]]
        return {"handle": request["handle"], "solution": {"selected": selected},
                "record": record, "trajectory": trajectory,
                "timings": self._timings(model, "reused", solve=solving,
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
        if self.touched is not None and id(self.touched) in self.pool:
            self.pool.move_to_end(id(self.touched))  # every successful handle op, close too
        aimed = self.on is None or self.on == request["op"]
        if self.mode == "bad-timings" and aimed and "timings" in body and self._misbehaves(
                TIMINGS_MODES):
            body["timings"].update(self.patch)
        elif self.mode == "wrong-handle" and aimed and "handle" in request and (
                self._misbehaves(TIMINGS_MODES)):
            body["handle"] = "h999"
        elif self.mode == "bad-digest" and aimed and "digest" in body and self._misbehaves(
                TIMINGS_MODES):
            body["digest"] = data_digest({"ids": []})
        elif self.mode == "bad-trajectory" and aimed and body.get("trajectory") and (
                self._misbehaves(TIMINGS_MODES)):
            body["trajectory"][0].update(self.patch)
        elif self.mode == "extra-round" and aimed and body.get("trajectory") and (
                self._misbehaves(TIMINGS_MODES)):
            rows = body["trajectory"]
            rows.append({**rows[-1], "round": len(rows) + 1, "accepted": False})
        self._reply({"id": request["id"], "ok": True, **body})

    def serve(self):
        for line in sys.stdin:
            if self.request_log is not None:
                with open(self.request_log, "a", encoding="utf-8") as log:
                    log.write(line)
            request = json.loads(line)
            op = request["op"]
            if op == "hello":
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
    parser.add_argument("--patch", default="{}", help="patch-record/bad-timings: JSON object")
    parser.add_argument("--delay", type=float, default=3600.0,
                        help="slow-hello/slow-solve: seconds to sleep")
    parser.add_argument("--on", default=None,
                        help="aim the session misbehaviours at this request op only")
    parser.add_argument("--pidfile", default=None, help="write this process's id here on start")
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
