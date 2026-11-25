"""A stand-in for the Julia worker: the JuMP pack's JSON-lines protocol in Python.

Run as ``python jump_fake_worker.py [--mode MODE] [--once FLAG_FILE] [--patch JSON]
[--pidfile FILE] [--request-log FILE]``. It
reads one JSON request per line on stdin and answers one JSON object per line on stdout,
exactly as ``dskit.pipeline.libs.jump.JuliaWorker`` expects of the real worker, so the
pack's suite needs no Julia. It solves ONE program, ``budgeted_select`` (a 0/1 knapsack),
by brute force so the answers are real, under ONE solver, ``HiGHS``; any other program or
solver gets the error reply the real worker sends. Extra ``data`` keys are ignored, so a
test can pad a payload.

``--mode`` picks a behaviour, never an edit to this file. At ``hello``:

``bad-protocol``   report a protocol version the pack does not speak
``stdout-noise``   print a banner line that is not JSON before the ``hello`` reply
``bool-id``        answer ``hello`` under the id ``true`` instead of the integer
``hello-error``    answer ``hello`` with a well-formed ``ok: false``
``deaf``           answer ``hello``, then never read stdin again

At the first ``solve`` (with ``--once``, only until the flag file exists):

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

Answering correctly throughout:

``ok``             the default
``noisy-stderr``   write a megabyte to stderr before every reply
``big-reply``      a record with many binding rows (a reply line of megabytes)
``linger``         on stdin EOF without ``shutdown``, sleep instead of exiting

``--pidfile`` writes this process's id on start (so a test can find it behind a
wrapper); ``--request-log`` appends every request line received (so a test can read what
crossed the wire).
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
SOLVERS = ("HiGHS",)

HELLO_MODES = ("bad-protocol", "stdout-noise", "bool-id", "hello-error", "deaf")
SOLVE_MODES = ("crash", "hang", "malformed", "wrong-id", "error-reply", "bad-record",
               "patch-record", "dup-key", "extra-key", "over-budget")
CLEAN_MODES = ("ok", "noisy-stderr", "big-reply", "linger")
MODES = CLEAN_MODES + HELLO_MODES + SOLVE_MODES

#: Bytes ``noisy-stderr`` writes before each reply (far beyond any pipe buffer).
STDERR_NOISE_BYTES = 1 << 20

#: Binding rows ``big-reply`` sends.
BIG_REPLY_ROWS = 40_000


def _write(text):
    sys.stdout.write(text + "\n")
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
        "status": "FEASIBLE_POINT",
        "termination": "OPTIMAL",
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
    """The request loop, with one switchable behaviour."""

    def __init__(self, mode, once, patch, request_log=None):
        self.mode = mode
        self.request_log = request_log
        self.once = once
        self.patch = patch
        self.deaf = False

    def _reply(self, obj):
        if self.mode == "noisy-stderr":
            sys.stderr.write("n" * STDERR_NOISE_BYTES + "\n")
            sys.stderr.flush()
        _write(json.dumps(obj))

    def _misbehaves(self):
        """Whether this solve is the one that misbehaves (``--once`` consumes the flag file)."""
        if self.mode not in SOLVE_MODES:
            return False
        if self.once is None:
            return True
        if os.path.exists(self.once):
            return False
        with open(self.once, "w", encoding="utf-8") as flag:
            flag.write(self.mode)
        return True

    def _hello(self, request):
        if self.mode == "hello-error":
            self._reply({"id": request["id"], "ok": False,
                         "error": {"kind": "environment", "message": "unresolved manifest"}})
            return
        if self.mode == "stdout-noise":
            _write("Precompiling DskitJuMP...")
        version = WRONG_PROTOCOL_VERSION if self.mode == "bad-protocol" else PROTOCOL_VERSION
        rid = True if self.mode == "bool-id" else request["id"]
        self._reply({"id": rid, "ok": True, "protocol": version, "julia": "fake-1.0",
                     "packages": {"JuMP": "fake", "HiGHS": "fake"}})
        self.deaf = self.mode == "deaf"

    def _misbehave(self, request):
        """Act out the chosen failure for this solve."""
        if self.mode == "crash":
            os._exit(3)
        if self.mode == "hang":
            time.sleep(3600)
        if self.mode == "malformed":
            _write("this is not json")
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
        elif self.mode == "dup-key":
            # A lenient parser keeps the LAST "id" (the right one) and accepts the reply.
            _write('{"id": 999999, ' + json.dumps(reply)[1:])
            return
        self._reply(reply)

    def _error(self, request, kind, message):
        self._reply({"id": request["id"], "ok": False, "error": {"kind": kind, "message": message}})

    def _answer(self, request):
        if request["program"] != PROGRAM:
            self._error(request, "unknown_program", f"no program named {request['program']!r}")
            return
        if request["solver"] not in SOLVERS:
            self._error(request, "unknown_solver",
                        f"the environment provides no solver {request['solver']!r}")
            return
        if self._misbehaves():
            self._misbehave(request)
            return
        solution, record = _solve(request)
        if self.mode == "big-reply":
            record["binding"] = record["binding"] * BIG_REPLY_ROWS
        self._reply({"id": request["id"], "ok": True, "solution": solution, "record": record})

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
            elif op == "solve":
                self._answer(request)
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
    parser.add_argument("--patch", default="{}", help="patch-record: JSON object for the record")
    parser.add_argument("--pidfile", default=None, help="write this process's id here on start")
    parser.add_argument("--request-log", default=None, help="append every request line here")
    args = parser.parse_args(argv)
    if args.pidfile is not None:
        with open(args.pidfile, "w", encoding="utf-8") as out:
            out.write(str(os.getpid()))
    FakeWorker(args.mode, args.once, json.loads(args.patch), args.request_log).serve()


if __name__ == "__main__":
    main()
