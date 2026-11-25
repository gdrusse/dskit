"""A stand-in for the Julia worker: the JuMP pack's JSON-lines protocol in Python.

Run as ``python jump_fake_worker.py [--mode MODE] [--once FLAG_FILE]``. It reads one JSON
request per line on stdin and answers one JSON object per line on stdout, exactly as
``dskit.pipeline.libs.jump.JuliaWorker`` expects of the real worker, so the pack's suite
needs no Julia. It solves ONE program, ``budgeted_select`` (a 0/1 knapsack), by brute
force so the answers are real; any other program name gets an ``unknown_program`` error
reply.

``--mode`` picks a behaviour, never an edit to this file:

``ok``             answer everything correctly (the default)
``bad-protocol``   ``hello`` reports a protocol version the pack does not speak
``crash``          exit without a reply when the first ``solve`` arrives
``hang``           read the first ``solve`` and never answer it
``malformed``      answer the first ``solve`` with a line that is not JSON
``wrong-id``       answer the first ``solve`` under another request id
``error-reply``    answer the first ``solve`` with ``ok: false`` and an error body
``bad-record``     answer the first ``solve`` with a record missing a field

``--once FLAG_FILE`` makes the misbehaviour fire only if FLAG_FILE does not exist yet,
and creates it as it fires: the first worker misbehaves, the fresh one the pack starts
afterwards answers correctly (the way a one-off crash looks to the pack).
"""

from __future__ import annotations

import argparse
import itertools
import json
import os
import sys
import time

#: The protocol version this fake speaks; ``dskit.pipeline.libs.jump.PROTOCOL_VERSION``
#: pins it from the other side in the suite.
PROTOCOL_VERSION = 1

#: What ``bad-protocol`` reports.
WRONG_PROTOCOL_VERSION = 999

PROGRAM = "budgeted_select"

MODES = ("ok", "bad-protocol", "crash", "hang", "malformed", "wrong-id", "error-reply", "bad-record")


def _reply(obj):
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def _knapsack(ids, cost, value, budget):
    """The best subset by value inside the budget, ties to the smaller sorted id list."""
    best, best_value = (), 0.0
    for size in range(len(ids) + 1):
        for subset in itertools.combinations(sorted(ids), size):
            spend = sum(cost[i] for i in subset)
            if spend > budget + 1e-9:
                continue
            gained = sum(value[i] for i in subset)
            if gained > best_value + 1e-12:
                best, best_value = subset, gained
    return list(best), best_value


def _solve(request):
    data, params = request["data"], request["params"]
    ids = list(data["ids"])
    cost = dict(zip(ids, data["cost"]))
    value = dict(zip(ids, data["value"]))
    started = time.perf_counter()
    chosen, objective = _knapsack(ids, cost, value, float(params["budget"]))
    spend = sum(cost[i] for i in chosen)
    slack = float(params["budget"]) - spend
    record = {
        "solver": request["solver"],
        "status": "ok",
        "termination": "optimal",
        "objective": objective,
        "bound": objective,
        "gap": 0.0,
        "seconds": time.perf_counter() - started,
        "variables": len(ids),
        "constraints": 1,
        "binding": [{"name": "budget", "rows": 1, "binding": int(abs(slack) <= 1e-9),
                     "min_slack": slack}],
    }
    return {"selected": chosen}, record


class FakeWorker:
    """The request loop, with one switchable misbehaviour."""

    def __init__(self, mode, once):
        self.mode = mode
        self.once = once

    def _misbehaves(self):
        """Whether this solve is the one that misbehaves (``--once`` consumes the flag file)."""
        if self.mode in ("ok", "bad-protocol"):
            return False
        if self.once is None:
            return True
        if os.path.exists(self.once):
            return False
        with open(self.once, "w", encoding="utf-8") as flag:
            flag.write(self.mode)
        return True

    def _hello(self, request):
        version = WRONG_PROTOCOL_VERSION if self.mode == "bad-protocol" else PROTOCOL_VERSION
        _reply({"id": request["id"], "ok": True, "protocol": version, "julia": "fake-1.0",
                "packages": {"JuMP": "fake", "HiGHS": "fake"}})

    def _misbehave(self, request):
        """Act out the chosen failure for this solve; return True when it consumed the request."""
        if self.mode == "crash":
            os._exit(3)
        if self.mode == "hang":
            time.sleep(3600)
        if self.mode == "malformed":
            sys.stdout.write("this is not json\n")
            sys.stdout.flush()
        elif self.mode == "wrong-id":
            solution, record = _solve(request)
            _reply({"id": request["id"] + 1000, "ok": True, "solution": solution, "record": record})
        elif self.mode == "error-reply":
            _reply({"id": request["id"], "ok": False,
                    "error": {"kind": "solve_failed", "message": "the fake worker refuses on request"}})
        elif self.mode == "bad-record":
            solution, record = _solve(request)
            del record["gap"]
            _reply({"id": request["id"], "ok": True, "solution": solution, "record": record})
        return True

    def _answer(self, request):
        if request["program"] != PROGRAM:
            _reply({"id": request["id"], "ok": False,
                    "error": {"kind": "unknown_program",
                              "message": f"no program named {request['program']!r}"}})
            return
        if self._misbehaves():
            self._misbehave(request)
            return
        solution, record = _solve(request)
        _reply({"id": request["id"], "ok": True, "solution": solution, "record": record})

    def serve(self):
        for line in sys.stdin:
            request = json.loads(line)
            op = request["op"]
            if op == "hello":
                self._hello(request)
            elif op == "solve":
                self._answer(request)
            elif op == "shutdown":
                _reply({"id": request["id"], "ok": True})
                return
            else:
                _reply({"id": request["id"], "ok": False,
                        "error": {"kind": "unknown_op", "message": f"no op {op!r}"}})


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--mode", choices=MODES, default="ok")
    parser.add_argument("--once", default=None, help="flag file: misbehave only until it exists")
    args = parser.parse_args(argv)
    FakeWorker(args.mode, args.once).serve()


if __name__ == "__main__":
    main()
