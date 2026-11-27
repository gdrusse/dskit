"""Build, update and solve time: the JuMP worker against the pyomo pack (ADR-0328 am. 1).

The owner's acceptance benchmark for the JuMP doorway: the benefit sought from Julia is
fast constraint construction and in-place change, the solver being the same HiGHS either
way, so the two sides are timed on the SAME program at several sizes and the table says
honestly where JuMP is faster and where it is not.

The program is the ``assignment`` reference (generalized assignment: each item to at
most one bin, each bin within its capacity, maximise the value assigned), written here a
second time in pyomo as the same program the Julia worker builds (same variables, rows,
coefficients and objective; the variables are created item-major here and bin-major in
the worker, and HiGHS's solve time depends on that column order), solved by HiGHS through ``appsi_highs`` on one side and
through the worker on the other, with the same time limit and the same HiGHS options.

Measured per size (``items`` items, ``bins`` bins; rows = items + bins):

* pyomo cold build: construct the ``ConcreteModel`` and load it into HiGHS
  (``appsi`` ``set_instance``), so both sides end with the model inside HiGHS;
* JuMP cold build: the worker's ``build_seconds`` of an ``open`` that builds;
* worker start: launching Julia, ``hello`` and ``describe`` (paid once per run);
* open wall: the whole ``open`` seen from Python (encode, pipe, parse, validate, build);
* transfer: the worker's ``transfer_seconds`` with the data as JSON and as Arrow files;
* JuMP update: every capacity and a tenth of the weight coefficients changed in place
  (``update_seconds``), and the same seen from Python;
* pyomo re-build: pyomo's equivalent of that update, a new model from the new data;
* solve: HiGHS's time on each side under the same limit and options.

Each measure runs ``--repeats`` times after one first call; the table prints the median
of the repeats and, in brackets, the first call (Julia compiles on its first call, pyomo
imports and caches on its own). Every Julia process runs alone (the JSON worker is closed
before the Arrow worker starts). Peak resident memory of each process is printed.

Run from the repository root with ``julia`` on ``PATH``, under the host's admission
control (a storage guard) with the scratch directory and a modest RAM declaration::

    <guard> run --write SCRATCH 1 --peak-ram-gib 3 -- python tools/jump/benchmark.py --scratch SCRATCH

The opt-in solver comparison uses two pinned JuMP reference programs with bounded
synthetic cases and a common five-second solve limit. Run from this checkout with an
already-resolved SCIP overlay and guarded scratch, depot and results paths::

    python -B -m tools.jump.benchmark --compare-solvers --julia-overlay OVERLAY --scratch SCRATCH

Set JULIA_DEPOT_PATH to the writable benchmark depot followed by the installed
depot. Save stdout as the JSON report and retain the guard receipt with it. The
default Pyomo benchmark keeps writing its timing table to stdout.
"""

from __future__ import annotations

import argparse
import gc
import hashlib
import itertools
import json
import math
import os
import platform
import random
import resource
import shutil
import statistics
import subprocess
import sys
import time
import tomllib
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from dskit.pipeline.libs.jump import JuliaWorker, WorkerRefusal

#: The program both sides build.
PROGRAM = "assignment"

#: The solver both sides use.
SOLVER = "HiGHS"

#: The default sizes, in items; with the default two bins the largest is ~200,000 rows.
DEFAULT_SIZES = (2_000, 20_000, 200_000)
DEFAULT_BINS = 2
DEFAULT_REPEATS = 3
DEFAULT_TIME_LIMIT = 60.0
DEFAULT_GAP = 1e-4
DEFAULT_SEED = 20261125

#: The share of weight coefficients an update changes.
UPDATE_SHARE = 0.1

#: Each bin's capacity as a share of the total weight it would carry with every item.
CAPACITY_SHARE = 0.3

#: Uniform ranges of the generated values and weights.
VALUE_RANGE = (1.0, 10.0)
WEIGHT_RANGE = (1.0, 10.0)

#: Seconds a worker request may take, and its start (a first start may precompile).
REQUEST_TIMEOUT = 3600.0
START_TIMEOUT = 900.0

#: An Arrow threshold no array reaches (JSON) and one every array passes (Arrow).
JSON_ONLY = 1 << 62
ARROW_ALL = 0

#: What the termination row shows when a solve ended with no feasible point.
NO_POINT = "no point"


def make_data(items, bins, rng):
    """Return one instance of the ``assignment`` schema (row-major lists).

    Parameters
    ----------
    items, bins : int
        The dimension lengths.
    rng : random.Random
        The seeded generator.

    Returns
    -------
    dict
        ``items``, ``bins``, ``value``, ``weight`` (items x bins) and ``capacity``.
    """
    value = [[rng.uniform(*VALUE_RANGE) for _ in range(bins)] for _ in range(items)]
    weight = [[rng.uniform(*WEIGHT_RANGE) for _ in range(bins)] for _ in range(items)]
    capacity = [CAPACITY_SHARE * sum(row[b] for row in weight) for b in range(bins)]
    return {"items": [f"i{i}" for i in range(items)], "bins": [f"b{b}" for b in range(bins)],
            "value": value, "weight": weight, "capacity": capacity}


def changed(data, rng):
    """Return ``data`` with every capacity and a tenth of the weights changed.

    Parameters
    ----------
    data : dict
        An instance from :func:`make_data`.
    rng : random.Random
        The seeded generator.

    Returns
    -------
    dict
        The changed fields only: ``weight`` and ``capacity``.
    """
    weight = [list(row) for row in data["weight"]]
    cells = [(i, b) for i in range(len(weight)) for b in range(len(weight[0]))]
    for i, b in rng.sample(cells, int(UPDATE_SHARE * len(cells))):
        weight[i][b] = rng.uniform(*WEIGHT_RANGE)
    capacity = [c * rng.uniform(0.9, 1.1) for c in data["capacity"]]
    return {"weight": weight, "capacity": capacity}


def options(seed, gap):
    """Return the HiGHS options both sides set (one thread, the seed, the gap)."""
    return {"threads": 1, "random_seed": seed, "mip_rel_gap": gap}


class PyomoSide:
    """The ``assignment`` program in pyomo, loaded into HiGHS through ``appsi``.

    Parameters
    ----------
    time_limit, gap : float
        The solve's time limit and relative gap.

    Examples
    --------
    ::

        side = PyomoSide(60.0, 1e-4)
        seconds, solver = side.build(make_data(100, 2, random.Random(0)), seed=0)
    """

    def __init__(self, time_limit, gap):
        self.time_limit = time_limit
        self.gap = gap

    def build(self, data, seed):
        """Construct the model and load it into a new HiGHS; return ``(seconds, solver)``.

        Parameters
        ----------
        data : dict
            An instance from :func:`make_data`.
        seed : int
            HiGHS's random seed.

        Returns
        -------
        tuple
            Seconds taken, and the loaded ``appsi`` solver (its model on ``.model``).
        """
        import pyomo.environ as pe
        from pyomo.contrib.appsi.solvers import Highs

        started = time.perf_counter()
        n, nb = len(data["items"]), len(data["bins"])
        value, weight, capacity = data["value"], data["weight"], data["capacity"]
        m = pe.ConcreteModel()
        m.I = pe.RangeSet(0, n - 1)
        m.B = pe.RangeSet(0, nb - 1)
        m.x = pe.Var(m.I, m.B, within=pe.Binary)
        m.obj = pe.Objective(expr=pe.quicksum(value[i][b] * m.x[i, b]
                                              for b in range(nb) for i in range(n)),
                             sense=pe.maximize)
        m.one_bin = pe.Constraint(m.I, rule=lambda m, i: pe.quicksum(
            m.x[i, b] for b in range(nb)) <= 1)
        m.capacity = pe.Constraint(m.B, rule=lambda m, b: pe.quicksum(
            weight[i][b] * m.x[i, b] for i in range(n)) <= capacity[b])
        solver = Highs()
        solver.config.time_limit = self.time_limit
        solver.config.load_solution = False
        solver.highs_options = options(seed, self.gap)
        solver.set_instance(m)
        solver.model = m
        return time.perf_counter() - started, solver

    @staticmethod
    def solve(solver):
        """Solve the loaded model; return ``(seconds, termination, objective)``."""
        started = time.perf_counter()
        results = solver.solve(solver.model)
        return (time.perf_counter() - started, str(results.termination_condition),
                results.best_feasible_objective)


def outcome(termination, objective):
    """One cell for a solve's end: the termination and its objective, or ``no point``.

    A solve that stops at its time limit before any feasible point has no
    objective; the table still needs a row for it.
    """
    return f"{termination}:{NO_POINT}" if objective is None else f"{termination}:{objective:.6g}"


def median_first(values):
    """Return ``(median of the repeats, the first call)`` of a measure's runs."""
    return statistics.median(values[1:]), values[0]


def run_pyomo(data, update, repeats, time_limit, gap):
    """Time the pyomo side; return measure name to the list of runs (first call first)."""
    side, out = PyomoSide(time_limit, gap), {}
    for r in range(repeats + 1):
        seconds, solver = side.build(data, seed=r)
        out.setdefault("pyomo cold build", []).append(seconds)
        solve_seconds, termination, objective = side.solve(solver)
        out.setdefault("pyomo solve", []).append(solve_seconds)
        out.setdefault("pyomo termination", []).append(outcome(termination, objective))
        del solver
        gc.collect()
        seconds, solver = side.build({**data, **update}, seed=r)
        out.setdefault("pyomo re-build (update)", []).append(seconds)
        del solver
        gc.collect()
    return out


def peak_rss_mib(pid):
    """Return the peak resident memory of process ``pid`` in MiB (Linux), or None."""
    try:
        with open(f"/proc/{pid}/status", encoding="utf-8") as status:
            for line in status:
                if line.startswith("VmHWM:"):
                    return int(line.split()[1]) / 1024
    except OSError:
        return None
    return None


def run_jump(data, update, repeats, time_limit, gap, scratch, arrow):
    """Time the worker side; return measure name to the list of runs (first call first).

    Each repeat opens a model under its own ``random_seed`` (a new pool key, so the
    ``open`` builds; the pool holds one model, so the previous one is evicted).
    """
    label = "Arrow" if arrow else "JSON"
    worker = JuliaWorker(JuliaWorker.default_command(),
                         settings={"pool_models": 1, "pool_bytes": 1 << 40},
                         transport={"arrow_threshold": ARROW_ALL if arrow else JSON_ONLY,
                                    "scratch_root": scratch})
    out = {}
    try:
        started = time.perf_counter()
        worker.describe(PROGRAM, start_timeout=START_TIMEOUT)
        out[f"worker start + hello + describe ({label})"] = [time.perf_counter() - started] * 2
        for r in range(repeats + 1):
            started = time.perf_counter()
            model = worker.open(PROGRAM, data, {}, SOLVER, options(r, gap),
                                timeout=REQUEST_TIMEOUT, start_timeout=START_TIMEOUT)
            wall = time.perf_counter() - started
            assert model.timings.path == "built", model.timings
            out.setdefault(f"JuMP open wall ({label})", []).append(wall)
            out.setdefault(f"transfer ({label})", []).append(model.timings.transfer_seconds)
            if arrow:
                model.close()
                continue
            out.setdefault("JuMP cold build", []).append(model.timings.build_seconds)
            _, record = model.solve({"time_limit": time_limit})
            out.setdefault("JuMP solve", []).append(model.timings.solve_seconds)
            out.setdefault("JuMP termination", []).append(
                outcome(record.termination, record.objective))
            started = time.perf_counter()
            model.update(data=update)
            wall = time.perf_counter() - started
            assert model.timings.path == "updated", model.timings
            out.setdefault("JuMP update (in place)", []).append(model.timings.update_seconds)
            out.setdefault("JuMP update wall", []).append(wall)
            out.setdefault("transfer of the update (JSON)", []).append(
                model.timings.transfer_seconds)
            model.close()
        out[f"Julia peak RSS MiB ({label})"] = [peak_rss_mib(worker.pid)] * 2
    finally:
        worker.close()
    return out


def table(results, sizes, bins):
    """Return the printed table: one row per measure, one column per size."""
    measures = []
    for per_size in results.values():
        for name in per_size:
            if name not in measures:
                measures.append(name)
    head = ["measure (median of repeats [first call]), seconds"] + [
        f"{n:,} items x {bins} ({n + bins:,} rows)" for n in sizes]
    rows = [head]
    for name in measures:
        cells = [name]
        for n in sizes:
            runs = results[n].get(name)
            if runs is None:
                cells.append("-")
            elif isinstance(runs[0], str):
                cells.append(", ".join(sorted(set(runs))))
            elif runs[0] is None:
                cells.append("n/a")
            else:
                median, first = median_first(runs)
                cells.append(f"{median:.3f} [{first:.3f}]")
        rows.append(cells)
    widths = [max(len(row[i]) for row in rows) for i in range(len(head))]
    lines = [" | ".join(c.ljust(w) for c, w in zip(row, widths)) for row in rows]
    lines.insert(1, "-|-".join("-" * w for w in widths))
    return "\n".join(lines)


# Keep the synthetic comparison independent of the configurable Pyomo workload.
_COMPARISON_SEEDS = (17, 29)
_COMPARISON_REPEATS = 4
_COMPARISON_SECONDS = 5.0
_COMPARISON_SOLVERS = (SOLVER, "SCIP")
_COMPARISON_HISTORIES = ("warm", "warm_with_start", "first_incumbent")
_SELECTION = "budgeted_select"
_OPTIMAL = "OPTIMAL"
_LIMITED = {"TIME_LIMIT", "SOLUTION_LIMIT", "OTHER_LIMIT"}
_IMPOSSIBLE = {"INFEASIBLE", "INFEASIBLE_OR_UNBOUNDED", "DUAL_INFEASIBLE",
               "LOCALLY_INFEASIBLE"}
_OBJECTIVE_TOLERANCE = 1e-6
_SOLUTION_KEYS = {_SELECTION: "selected", PROGRAM: "assigned"}
_SOLUTION_CAP = {SOLVER: "mip_max_improving_sols", "SCIP": "limits/bestsol"}
_SCIP_VERSION = "0.12.8"
_SCIP_TREE = "cfc4a317a683a8cd368bde015791055ddd09b9bd"


def _canonical_json(value):
    """Serialize fixture and report data without non-finite numbers."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _digest(value):
    """Return a SHA-256 of the report's canonical JSON representation."""
    return hashlib.sha256(_canonical_json(value).encode()).hexdigest()


def _finite_number(value):
    """Recognize finite numeric results, excluding booleans."""
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


def _selection_value(data, params, components, selected):
    """Reconstruct and check a selected set, then return its integer objective."""
    items = {label: i for i, label in enumerate(data["ids"])}
    seen = set()
    cost = value = 0
    for label in selected:
        if not isinstance(label, str) or label not in items:
            raise ValueError(f"unknown selection label {label!r}")
        if label in seen:
            raise ValueError(f"duplicate selection label {label!r}")
        seen.add(label)
        i = items[label]
        cost += data["cost"][i]
        value += data["value"][i]
    if cost > params["budget"]:
        raise ValueError("selection exceeds budget")
    if components.get("max_items") and len(seen) > params["max_items"]:
        raise ValueError("selection exceeds max_items")
    return value


def _assignment_value(data, params, components, assigned):
    """Reconstruct and check assignment rows, then return their integer objective."""
    items = {label: i for i, label in enumerate(data["items"])}
    bins = {label: j for j, label in enumerate(data["bins"])}
    by_item = {}
    loads = [0] * len(bins)
    counts = [0] * len(bins)
    value = 0
    for pair in assigned:
        if (not isinstance(pair, list) or len(pair) != 2
                or not all(isinstance(label, str) for label in pair)):
            raise ValueError(f"invalid assignment pair {pair!r}")
        item, bin_label = pair
        if item not in items or bin_label not in bins:
            raise ValueError(f"unknown assignment key {pair!r}")
        if item in by_item:
            if by_item[item] == bin_label:
                raise ValueError(f"duplicate assignment pair {pair!r}")
            raise ValueError(f"item {item!r} is assigned to more than one bin")
        by_item[item] = bin_label
        i, j = items[item], bins[bin_label]
        loads[j] += data["weight"][i][j]
        counts[j] += 1
        value += data["value"][i][j]
    if any(load > cap for load, cap in zip(loads, data["capacity"])):
        raise ValueError("assignment exceeds bin capacity")
    if components.get("max_per_bin") and max(counts, default=0) > params["max_per_bin"]:
        raise ValueError("assignment exceeds max_per_bin")
    return value


def _verify_point(program, data, params, components, solution, record, *, optimum=None):
    """Verify a binary point and the stated maximization objective and upper bound."""
    key = _SOLUTION_KEYS[program]
    if not isinstance(solution, dict) or set(solution) != {key}:
        raise ValueError(f"unexpected {program} solution fields")
    values = solution[key]
    objective, bound = record.objective, record.bound
    if bound is not None and not _finite_number(bound):
        raise ValueError("upper bound is invalid")
    if record.termination in _IMPOSSIBLE:
        raise ValueError("solver reports an impossible termination for a bounded feasible case")
    if bound is not None and bound < (0 if optimum is None else optimum) - _OBJECTIVE_TOLERANCE:
        raise ValueError("upper bound excludes a known feasible objective")
    if values is None:
        if (record.status == "FEASIBLE_POINT" or objective is not None or record.gap is not None
                or record.termination == _OPTIMAL):
            raise ValueError("no-point reply carries incompatible solve metadata")
        return {"outcome": "no_point", "verified_objective": None, "common_gap": None}
    if not isinstance(values, list) or record.status != "FEASIBLE_POINT":
        raise ValueError("point reply carries incompatible solve metadata")
    check = _selection_value if program == _SELECTION else _assignment_value
    verified = check(data, params, components, values)
    if not _finite_number(objective) or abs(objective - verified) > _OBJECTIVE_TOLERANCE:
        raise ValueError(f"reported objective differs from verified objective {verified}")
    if bound is not None and bound < verified - _OBJECTIVE_TOLERANCE:
        raise ValueError("upper bound is below the verified objective")
    if optimum is not None and verified > optimum:
        raise ValueError("point exceeds the exhaustive optimum")
    if record.termination == _OPTIMAL:
        if optimum is not None and verified != optimum:
            raise ValueError("optimal reply disagrees with the exhaustive optimum")
        if bound is None or abs(bound - verified) > _OBJECTIVE_TOLERANCE:
            raise ValueError("optimal reply's upper bound does not match its objective")
    gap = None if bound is None else (bound - verified) / max(1, abs(verified))
    return {"outcome": "verified_point", "verified_objective": verified, "common_gap": gap}


def _exhaustive_optimum(program, data, params, components):
    """Enumerate both tiny programs independently of the verifier and greedy start."""
    if program == _SELECTION:
        costs, values = data["cost"], data["value"]
        cap = params["max_items"] if components.get("max_items") else len(costs)
        return max(
            sum(value for value, used in zip(values, bits) if used)
            for bits in itertools.product((False, True), repeat=len(costs))
            if sum(bits) <= cap
            and sum(cost for cost, used in zip(costs, bits) if used) <= params["budget"]
        )
    n, bins = len(data["items"]), len(data["bins"])
    cap = params["max_per_bin"] if components.get("max_per_bin") else n
    best = 0
    for choices in itertools.product(range(-1, bins), repeat=n):
        loads = [0] * bins
        counts = [0] * bins
        value = 0
        for i, j in enumerate(choices):
            if j != -1:
                loads[j] += data["weight"][i][j]
                counts[j] += 1
                value += data["value"][i][j]
        if max(counts) <= cap and all(load <= limit for load, limit in
                                      zip(loads, data["capacity"])):
            best = max(best, value)
    return best


def _greedy_start(program, data, params, components):
    """Return a deterministic full binary start and its checked objective."""
    if program == _SELECTION:
        ids, costs, values = data["ids"], data["cost"], data["value"]
        chosen = set()
        budget = params["budget"]
        cap = params["max_items"] if components.get("max_items") else len(ids)
        for i in sorted(range(len(ids)), key=lambda i: (-values[i] / costs[i], i)):
            if costs[i] <= budget and len(chosen) < cap:
                chosen.add(i)
                budget -= costs[i]
        start = {"select": {"keys": [[item] for item in ids],
                            "values": [int(i in chosen) for i in range(len(ids))]}}
        selected = [item for i, item in enumerate(ids) if i in chosen]
        return start, _selection_value(data, params, components, selected)

    items, bins = data["items"], data["bins"]
    weight, value = data["weight"], data["value"]
    cells = list(itertools.product(range(len(items)), range(len(bins))))
    free = list(data["capacity"])
    cap = params["max_per_bin"] if components.get("max_per_bin") else len(items)
    counts = [0] * len(bins)
    chosen = {}
    ranked = sorted(cells, key=lambda ij: (-value[ij[0]][ij[1]] / weight[ij[0]][ij[1]], ij))
    for i, j in ranked:
        if i not in chosen and weight[i][j] <= free[j] and counts[j] < cap:
            chosen[i] = j
            free[j] -= weight[i][j]
            counts[j] += 1
    start = {"x": {"keys": [[items[i], bins[j]] for i, j in cells],
                   "values": [int(chosen.get(i) == j) for i, j in cells]}}
    assigned = [[items[i], bins[j]] for i, j in chosen.items()]
    return start, _assignment_value(data, params, components, assigned)


def _make_case(program, n, bins, seed, *, smoke):
    """Freeze base data, two sequential edits and the full starts before launch."""
    rng = random.Random(seed)
    labels = [f"i{i}" for i in range(n)]
    weight = [rng.randint(5, 30) for _ in range(n * (bins or 1))]
    bonus = [rng.randint(0, 4) for _ in weight]
    limits = ([sum(weight[j::bins]) * 7 // 60 for j in range(bins)] if bins else
              [sum(weight) * 35 // 100])
    component = "max_per_bin" if bins else "max_items"
    components = {component: smoke}
    stages = []
    for index in range(3):
        if index:
            for cell in rng.sample(range(len(weight)), len(weight) // 10):
                # Draw from the remaining weights so each edit changes a cell.
                replacement = rng.randint(5, 29)
                weight[cell] = replacement + (replacement >= weight[cell])
            limits = [limit + rng.randint(1, 3) for limit in limits]
        values = [w + b for w, b in zip(weight, bonus)]
        params = {component: 1 if bins else 2} if smoke else {}
        if bins:
            data = {"items": labels, "bins": [f"b{j}" for j in range(bins)],
                    "weight": [weight[i:i + bins] for i in range(0, len(weight), bins)],
                    "value": [values[i:i + bins] for i in range(0, len(values), bins)],
                    "capacity": limits}
            edit = {key: data[key] for key in ("weight", "value", "capacity")}
        else:
            params["budget"] = limits[0]
            data = {"ids": labels, "cost": list(weight), "value": values}
            edit = {key: data[key] for key in ("cost", "value")}
        update = {"data": edit, "params": params} if index else None
        start, objective = _greedy_start(program, data, params, components)
        stages.append({"data": data, "params": params, "update": update, "start": start,
                       "input_digest": _digest([program, data, params, components]),
                       "update_digest": _digest(update) if update is not None else None,
                       "start_digest": _digest(start), "start_objective": objective,
                       "optimum": _exhaustive_optimum(program, data, params, components)
                       if smoke else None})
    return {"program": program, "seed": seed, "smoke": smoke,
            "components": components, "stages": stages}

def _solver_options(solver, seed, arm):
    """Return paired raw attributes, including the first-incumbent cap where used."""
    if solver == SOLVER:
        options = {"threads": 1, "random_seed": seed, "mip_feasibility_tolerance": 1e-6,
                   "mip_rel_gap": 0.0, "mip_abs_gap": 0.0}
    else:
        options = {"parallel/maxnthreads": 1, "lp/threads": 1,
                   "randomization/randomseedshift": seed, "numerics/feastol": 1e-6,
                   "limits/gap": 0.0, "limits/absgap": 0.0, "timing/clocktype": 2}
    if arm == "first_incumbent":
        options[_SOLUTION_CAP[solver]] = 1
    return options


def _worker_command(julia, overlay, solver, *, probe=None):
    """Use the normal startup options and the experiment's already-resolved project."""
    entry = JuliaWorker.default_command()[-1]
    if solver == "SCIP":
        registration = 'import SCIP; DskitJuMP.SOLVERS["SCIP"] = SCIP.Optimizer; '
        entry = entry.replace("DskitJuMP.serve()", registration + "DskitJuMP.serve()")
    if probe is not None:
        entry = entry.replace("DskitJuMP.serve()", probe)
    return [julia, "--startup-file=no", f"--project={overlay}", "--threads=1",
            "--compiled-modules=existing", "--pkgimages=existing", "-e", entry]


def _start_mapping(start):
    """Translate the frozen wire-form start back to LiveModel.start input."""
    return {
        group: {tuple(key): value for key, value in zip(body["keys"], body["values"])}
        for group, body in start.items()
    }


def _measure_solve(model, case, fixture, stage):
    """Record the actual reply and wall times before independently verifying it."""
    dispatched = time.perf_counter()
    solution, record = model.solve({"time_limit": _COMPARISON_SECONDS})
    returned = time.perf_counter()
    try:
        point = _verify_point(case["program"], fixture["data"], fixture["params"],
                              case["components"], solution, record, optimum=fixture["optimum"])
    finally:
        verified_at = time.perf_counter()
        stage.update({"solve_wall_seconds": returned - dispatched,
                      "verification_seconds": verified_at - returned,
                      "solve_timings": model.timings.to_obj(), "record": record.to_obj(),
                      "solution": solution, "budget_censored": record.termination == "TIME_LIMIT"})
    stage.update(point)
    if point["outcome"] == "verified_point":
        stage["dispatch_to_verified_seconds"] = verified_at - dispatched
    if record.termination not in _LIMITED | {_OPTIMAL}:
        raise ValueError(f"unexpected solver termination {record.termination}")
    if case["smoke"] and record.termination != _OPTIMAL:
        raise ValueError(f"tiny smoke control stopped at {record.termination}")


def _run_trial(case, history, solver, command, scratch):
    """Run a fresh process-cold worker, recording each verified stage and any refusal."""
    worker = JuliaWorker(command, settings={"pool_models": 1, "pool_bytes": 64 << 20},
                         transport={"arrow_threshold": JSON_ONLY, "scratch_root": scratch})
    result = {"outcome": "completed", "stages": []}
    program, components = case["program"], case["components"]
    arm = "first_incumbent" if history == "first_incumbent" else "full"
    fixtures = case["stages"][:1] if arm == "first_incumbent" else case["stages"]
    options = _solver_options(solver, case["seed"], arm)
    stage, model = None, None
    step = "describe"
    launched = time.perf_counter()
    try:
        worker.describe(program, timeout=_OP_TIMEOUT, start_timeout=START_TIMEOUT)
        result["launch_hello_describe_seconds"] = time.perf_counter() - launched
        result["hello"] = worker.info
        for index, fixture in enumerate(fixtures):
            stage = {"stage": index, "input_digest": fixture["input_digest"],
                     "update_digest": fixture["update_digest"], "start_digest": None,
                     "start_objective": None, "start_supplied": 0}
            result["stages"].append(stage)
            started = time.perf_counter()
            if index == 0:
                step = "open"
                model = worker.open(program, fixture["data"], fixture["params"], solver,
                                    options, components, timeout=_OP_TIMEOUT,
                                    start_timeout=START_TIMEOUT)
                expected_path = "built"
            else:
                step = "update"
                model.update(**fixture["update"])
                expected_path = "updated"
            stage[f"{step}_wall_seconds"] = time.perf_counter() - started
            stage[f"{step}_timings"] = model.timings.to_obj()
            stage["worker_data_digest"] = model.digest
            if model.timings.path != expected_path:
                raise ValueError(f"{step} took {model.timings.path!r}, expected {expected_path!r}")
            if index and history == "warm_with_start":
                step = "start"
                start = _start_mapping(fixture["start"])
                started = time.perf_counter()
                count = model.start(start)
                stage["start_wall_seconds"] = time.perf_counter() - started
                stage["start_timings"] = model.timings.to_obj()
                stage.update({"start_supplied": count, "start_digest": fixture["start_digest"],
                              "start_objective": fixture["start_objective"]})
                if count != sum(map(len, start.values())):
                    raise ValueError("worker did not apply every supplied start value")
            step = "solve"
            _measure_solve(model, case, fixture, stage)
            expected_source = "values" if stage["start_supplied"] else None
            if model.timings.start_source != expected_source:
                raise ValueError("worker reports an unexpected start source")
    except (WorkerRefusal, ValueError) as exc:
        diagnostic = _diagnostic(exc)
        result.update(diagnostic)
        result["failed_operation"] = step
        if stage is not None:
            stage.update(diagnostic)
    finally:
        result["worker_peak_rss_mib"] = peak_rss_mib(worker.pid)
        try:
            if model is not None:
                model.close()
        except WorkerRefusal as exc:
            result["handle_close_diagnostic"] = _diagnostic(exc)
            if result["outcome"] == "completed":
                result.update(_diagnostic(exc))
                result["failed_operation"] = "close"
        finally:
            worker.close()
        result["launch_through_close_seconds"] = time.perf_counter() - launched
        result["driver_peak_rss_mib"] = peak_rss_mib(os.getpid())
    return result


def _diagnostic(exc):
    """Keep unsupported protocol operations distinct from failures and invalid replies."""
    if isinstance(exc, WorkerRefusal):
        unsupported = exc.kind in {"unknown_solver", "bad_limits", "unsupported"}
        return {"outcome": "unsupported" if unsupported else "worker_refusal",
                "kind": exc.kind, "message": exc.message}
    return {"outcome": "failed", "message": str(exc)}


def _python_bridge_evidence():
    """Require the bridge module imported from this benchmark's checkout."""
    module_path = Path(sys.modules[JuliaWorker.__module__].__file__).resolve()
    if module_path != _ROOT / "dskit" / "pipeline" / "libs" / "jump.py":
        raise ValueError(f"Python imported the bridge from {module_path}; "
                         "run python -m tools.jump.benchmark from the checkout root")
    return {"path": str(module_path), "sha256": _file_hash(module_path)}


def _trial_entry():
    """Receive one frozen trial in a fresh Python process for per-trial driver VmHWM."""
    payload = json.load(sys.stdin)
    if payload.pop("python_bridge") != _python_bridge_evidence():
        raise ValueError("trial imported a different Python bridge")
    result = _run_trial(**payload)
    print(_canonical_json(result))


def _launch_trial(case, history, solver, config):
    """Launch one driver and its one worker serially under the supplied environment."""
    command = _worker_command(config["julia"], config["overlay"], solver)
    payload = {"case": case, "history": history, "solver": solver, "command": command,
               "scratch": config["scratch"], "python_bridge": config["python_bridge"]}
    try:
        child = subprocess.run(
            [sys.executable, "-B", "-c",
             "from tools.jump.benchmark import _trial_entry; _trial_entry()"],
            cwd=_ROOT, env=config["env"], input=_canonical_json(payload),
            text=True, capture_output=True, check=False,
        )
    except OSError as exc:
        return {"outcome": "child_failed", "message": str(exc)}
    if child.returncode:
        return {"outcome": "child_failed", "returncode": child.returncode,
                "message": child.stderr[-4000:]}
    try:
        result = json.loads(child.stdout)
    except ValueError as exc:
        return {"outcome": "child_failed", "message": f"invalid trial output: {exc}"}
    if not isinstance(result, dict) or "outcome" not in result:
        return {"outcome": "child_failed", "message": "invalid trial result"}
    return result


def _paired_trials(case, history, repeat, preflight, config):
    """Run AB/BA pairs only when both sides passed this arm's preflight."""
    order = _COMPARISON_SOLVERS if repeat % 2 == 0 else _COMPARISON_SOLVERS[::-1]
    arm = "first_incumbent" if history == "first_incumbent" else "full"
    checks = {solver: preflight[solver][arm] for solver in order}
    pair = {"program": case["program"], "seed": case["seed"], "history": history,
            "repeat": repeat + 1, "order": order, "preflight": checks, "trials": {}}
    if any(check["outcome"] != "passed" for check in checks.values()):
        pair["outcome"] = "preflight_blocked"
        return pair
    for solver in order:
        pair["trials"][solver] = _launch_trial(case, history, solver, config)
    pair["outcome"] = ("completed" if all(trial["outcome"] == "completed"
                                         for trial in pair["trials"].values())
                       else "trial_failed")
    return pair


def _sample_stats(values):
    """Return median, observed range and sample count, keeping empty samples null."""
    return ({"median": statistics.median(values), "range": [min(values), max(values)],
             "n": len(values)} if values else None)


def _stage_metrics(trial, stage, history):
    """Expose comparable measurements, omitting first-incumbent latency without a point."""
    metrics = {key: value for key, value in stage.items() if key.endswith("_seconds")}
    first_latency = metrics.pop("dispatch_to_verified_seconds", None)
    if history == "first_incumbent":
        metrics["first_verified_reply_seconds"] = first_latency
    for operation, measure in (("open", "build"), ("update", "update"),
                               ("start", "update"), ("solve", "solve")):
        if f"{operation}_timings" in stage:
            metrics[f"worker_{operation}_seconds"] = stage[f"{operation}_timings"][
                f"{measure}_seconds"]
    if stage["stage"] == 0:
        metrics.update({key: value for key, value in trial.items()
                        if key.endswith(("_seconds", "_rss_mib"))})
    metrics.update({"verified_objective": stage["verified_objective"],
                    "upper_bound": stage["record"]["bound"], "common_gap": stage["common_gap"]})
    return {key: value for key, value in metrics.items() if value is not None}


def _summarize(pairs):
    """Summarize jointly valid pairs and retain per-solver counts of all outcomes."""
    groups = {}
    for pair in pairs:
        key = (pair["program"], pair["seed"], pair["history"])
        groups.setdefault(key, []).append(pair)
    summary = []
    for (program, seed, history), group in groups.items():
        for index in range(1 if history == "first_incumbent" else 3):
            values = {solver: {} for solver in _COMPARISON_SOLVERS}
            counts = {solver: Counter() for solver in _COMPARISON_SOLVERS}
            joint = Counter()
            for pair in group:
                metrics = {}
                for solver in _COMPARISON_SOLVERS:
                    trial = pair["trials"].get(solver, {})
                    stages = trial.get("stages", [])
                    stage = stages[index] if index < len(stages) else {}
                    outcome = stage.get("outcome", trial.get("outcome", pair["outcome"]))
                    counts[solver][outcome] += 1
                    if "record" in stage:
                        counts[solver][stage["record"]["termination"]] += 1
                    if stage.get("budget_censored"):
                        counts[solver]["budget_censored"] += 1
                    if pair["outcome"] == "completed":
                        metrics[solver] = _stage_metrics(trial, stage, history)
                if metrics:
                    left, right = (metrics[solver].keys() for solver in _COMPARISON_SOLVERS)
                    for metric in left & right:
                        joint[metric] += 1
                        for solver in _COMPARISON_SOLVERS:
                            values[solver].setdefault(metric, []).append(metrics[solver][metric])
            stats = {solver: {name: _sample_stats(v) for name, v in value.items()}
                     for solver, value in values.items()}
            overlap = {}
            for metric in joint:
                a, b = (stats[solver][metric]["range"] for solver in _COMPARISON_SOLVERS)
                overlap[metric] = max(a[0], b[0]) <= min(a[1], b[1])
            summary.append({"program": program, "seed": seed, "history": history, "stage": index,
                            "pairs_attempted": len(group), "contributing_pairs": dict(joint),
                            "counts": counts, "metrics": stats, "overlapping_ranges": overlap})
    return summary

_ROOT = Path(__file__).resolve().parents[2]
_WORKER_PROJECT = _ROOT / "julia" / "DskitJuMP"
_OP_TIMEOUT = 120.0
_PROBE_SECONDS = 1e-9
_ARTIFACT_PACKAGES = ("HiGHS_jll", "SCIP_jll", "SCIP_PaPILO_jll")
_THREAD_ENV = {"JULIA_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1", "OMP_NUM_THREADS": "1",
               "MKL_NUM_THREADS": "1", "BLIS_NUM_THREADS": "1"}
_PROBE = r"""
import Libdl, LinearAlgebra, Pkg
const DJ = DskitJuMP
const MOI = DJ.MOI

function raw_probe(program, solver, options, cap, arm, seconds)
    model = DJ.new_model(program, solver, options)
    backend = DJ.JuMP.backend(model)
    observed = Dict(k => MOI.get(backend, MOI.RawOptimizerAttribute(k)) for k in keys(options))
    observed == options || return Dict("outcome" => "mismatch", "observed" => observed)
    count = MOI.get(backend, MOI.RawOptimizerAttribute(cap))
    cap_ok = arm == "first_incumbent" ? count == 1 :
             solver == "HiGHS" ? count >= typemax(Int32) : count == -1
    attr = MOI.TimeLimitSec()
    MOI.supports(backend, attr) || return Dict("outcome" => "unsupported",
                                             "message" => "MOI.TimeLimitSec")
    before = MOI.get(backend, attr)
    within = DJ.with_limits(() -> MOI.get(backend, attr), model,
                            DJ.Limits(seconds, nothing, nothing, nothing))
    after = MOI.get(backend, attr)
    return Dict("outcome" => cap_ok && within == seconds && before == after ? "passed" : "mismatch",
                "observed" => observed, "improving_solution_limit" => count,
                "time_limit" => [before, within, after])
end

function handle_probe(cfg, options)
    worker = DJ.Worker()
    send(body) = DJ.reply_to(worker, DJ.canonical_json(body))
    hello = send(Dict("id" => 1, "op" => "hello",
                      "settings" => Dict("pool_models" => 1, "pool_bytes" => 64 << 20)))
    hello["ok"] || return Dict("outcome" => "failed", "reply" => hello)
    opened = send(merge(cfg["open"], Dict("id" => 2, "op" => "open", "solver" => cfg["solver"],
                                         "solver_options" => options)))
    opened["ok"] || return Dict("outcome" => "failed", "reply" => opened)
    handle = opened["handle"]
    backend = DJ.JuMP.backend(DJ.held_model(worker.pool, handle).model)
    limit() = MOI.get(backend, MOI.TimeLimitSec())
    solve(id, seconds) = send(Dict("id" => id, "op" => "solve", "handle" => handle,
                                  "limits" => Dict("time_limit" => seconds)))
    before = limit()
    early = solve(3, cfg["tiny_seconds"])
    after_early = limit()
    final = solve(4, cfg["seconds"])
    after_final = limit()
    valid = early["ok"] && final["ok"] && early["record"]["termination"] == "TIME_LIMIT" &&
            final["record"]["termination"] == "OPTIMAL" &&
            before == after_early == after_final
    closing = send(Dict("id" => 5, "op" => "close", "handle" => handle))
    return Dict("outcome" => valid && closing["ok"] ? "passed" : "mismatch",
                "limited_reply" => early, "retry_reply" => final,
                "time_limit" => [before, after_early, after_final], "close_reply" => closing)
end

function audit(cfg)
    solver = cfg["solver"]
    program = DJ.lookup_program(cfg["open"]["program"])
    records = Dict{String,Any}()
    for (key, config) in cfg["configs"]
        records[key] = try
            raw_probe(program, solver, config["options"], cfg["cap"], config["arm"], cfg["seconds"])
        catch err
            unsupported = err isa MOI.UnsupportedAttribute ||
                          (err isa DJ.Refusal && err.kind == "bad_request")
            Dict("outcome" => unsupported ? "unsupported" : "failed",
                 "message" => sprint(showerror, err))
        end
    end
    first_full = cfg["configs"][cfg["full_key"]]["options"]
    limit = try
        handle_probe(cfg, first_full)
    catch err
        Dict("outcome" => "failed", "message" => sprint(showerror, err))
    end
    library = solver == "HiGHS" ? DJ.HiGHS.libhighs : SCIP.libscip
    optimizer = DJ.SOLVERS[solver]()
    deps = Pkg.dependencies()
    sources = Dict(info.name => info.source for info in values(deps)
                   if info.name in cfg["source_packages"])
    hashes = Dict(name => bytes2hex(Pkg.GitTools.tree_hash(path)) for (name, path) in sources)
    return Dict("julia" => string(VERSION), "packages" => DJ.package_versions(),
                "project" => Base.active_project(), "source" => pathof(DJ),
                "package_sources" => sources, "source_trees" => hashes,
                "julia_threads" => Threads.nthreads(),
                "blas_threads" => LinearAlgebra.BLAS.get_num_threads(),
                "blas" => sprint(show, LinearAlgebra.BLAS.get_config()),
                "native_version" => MOI.get(optimizer, MOI.SolverVersion()),
                "native_library" => realpath(Libdl.dlpath(library)),
                "raw_options" => records, "handle_limit" => limit)
end

result = redirect_stdout(stderr) do
    audit(DJ.parse_json(read(stdin, String)))
end
println(DJ.canonical_json(result))
"""


def _file_hash(path):
    """Return a SHA-256 for one small source or project file."""
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def _source_hashes(project):
    """Fingerprint the checked-out or overlaid worker source files."""
    return {str(path.relative_to(project)): _file_hash(path)
            for path in sorted((project / "src").rglob("*.jl"))}


def _manifest_dependencies(manifest):
    """Index standard Julia manifest entries by UUID rather than package name."""
    packages = {}
    for name, entries in manifest["deps"].items():
        if len(entries) != 1 or entries[0]["uuid"] in packages:
            raise ValueError(f"ambiguous manifest entry for {name}")
        entry = entries[0]
        packages[entry["uuid"]] = (name, entry)
    return packages


def _overlay_evidence(overlay):
    """Verify the overlay preserves the checked-out Julia pins and worker source."""
    if overlay == _WORKER_PROJECT.resolve():
        raise ValueError("comparison needs a distinct resolved Julia overlay")
    base = tomllib.loads((_WORKER_PROJECT / "Manifest.toml").read_text())
    candidate = tomllib.loads((overlay / "Manifest.toml").read_text())
    if candidate["julia_version"] != base["julia_version"]:
        raise ValueError("overlay changed the pinned Julia version")
    baseline, packages = map(_manifest_dependencies, (base, candidate))
    identity = ("version", "git-tree-sha1", "path", "repo-rev", "repo-url")
    for uuid, (name, entry) in baseline.items():
        new_name, new_entry = packages.get(uuid, (None, {}))
        if new_name != name or any(entry.get(key) != new_entry.get(key) for key in identity):
            raise ValueError(f"overlay changed pinned dependency {name} ({uuid})")
    project = tomllib.loads((overlay / "Project.toml").read_text())
    base_project = tomllib.loads((_WORKER_PROJECT / "Project.toml").read_text())
    scip = packages.get(project["deps"].get("SCIP"), (None, {}))
    if (scip[0] != "SCIP" or scip[1].get("version") != _SCIP_VERSION
            or scip[1].get("git-tree-sha1") != _SCIP_TREE):
        raise ValueError(f"overlay must resolve SCIP.jl {_SCIP_VERSION} at its pinned tree")
    direct = dict(project["deps"])
    del direct["SCIP"]
    if direct != base_project["deps"]:
        raise ValueError("overlay changed the direct worker dependencies")
    sources = _source_hashes(overlay)
    if not sources or sources != _source_hashes(_WORKER_PROJECT):
        raise ValueError("overlay worker src/ differs from the checkout")
    by_name = {name: {**entry} for name, entry in packages.values()}
    for name in _ARTIFACT_PACKAGES:
        if name not in by_name:
            raise ValueError(f"overlay is missing {name}")
    return {"julia": base["julia_version"], "project_sha256": _file_hash(overlay / "Project.toml"),
            "manifest_sha256": _file_hash(overlay / "Manifest.toml"),
            "baseline_manifest_sha256": _file_hash(_WORKER_PROJECT / "Manifest.toml"),
            "source_sha256": sources, "packages": by_name}


def _comparison_config(julia, overlay, scratch):
    """Require resolved paths and an explicit writable/installed depot pair."""
    overlay, scratch = (Path(path).resolve(strict=True) for path in (overlay, scratch))
    if not overlay.is_dir() or not scratch.is_dir() or not os.access(scratch, os.W_OK):
        raise ValueError("overlay and writable scratch must be existing directories")
    depot_value = os.environ.get("JULIA_DEPOT_PATH", "").split(os.pathsep)
    if len(depot_value) < 2 or any(not Path(path).is_absolute() for path in depot_value):
        raise ValueError("set JULIA_DEPOT_PATH to the guarded writable depot and installed fallback")
    depots = [str(Path(path).resolve(strict=True)) for path in depot_value]
    if (len(set(depots)) != len(depots) or any(not Path(path).is_dir() for path in depots)
            or not os.access(depots[0], os.W_OK)):
        raise ValueError("Julia depot paths must be distinct directories, with a writable first depot")
    env = {**os.environ, **_THREAD_ENV, "JULIA_LOAD_PATH": "@:@stdlib",
           "JULIA_DEPOT_PATH": os.pathsep.join(depots), "JULIA_PKG_PRECOMPILE_AUTO": "0",
           "JULIA_PKG_OFFLINE": "true", "TMPDIR": str(scratch), "PYTHONDONTWRITEBYTECODE": "1"}
    return {"julia": julia, "overlay": str(overlay), "scratch": str(scratch),
            "env": env, "depots": depots, "python_bridge": _python_bridge_evidence()}


def _cache_state(depots, julia, packages):
    """Describe the pre-existing package cache files without creating any."""
    version_dir = "v" + ".".join(julia.split(".")[:2])
    state = {}
    for depot in depots:
        state[depot] = {}
        for package in packages:
            base = Path(depot) / "compiled" / version_dir / package
            files = []
            if base.is_dir():
                for path in sorted(base.iterdir()):
                    if path.is_file():
                        stat = path.stat()
                        files.append({"name": path.name, "bytes": stat.st_size,
                                      "mtime_ns": stat.st_mtime_ns})
            state[depot][package] = files
    return state


def _probe_solver(config, evidence, solver, smoke_case):
    """Read back native settings and verify Julia, source trees and actual limit restoration."""
    option_configs = {
        f"{seed}/{arm}": {"arm": arm, "options": _solver_options(solver, seed, arm)}
        for seed, arm in itertools.product(_COMPARISON_SEEDS, ("full", "first_incumbent"))
    }
    initial = smoke_case["stages"][0]
    source_packages = ["HiGHS", "JuMP", "MathOptInterface", "HiGHS_jll"]
    if solver == "SCIP":
        source_packages.extend(("SCIP", "SCIP_jll", "SCIP_PaPILO_jll"))
    payload = {"solver": solver, "cap": _SOLUTION_CAP[solver], "configs": option_configs,
               "full_key": f"{smoke_case['seed']}/full", "seconds": _COMPARISON_SECONDS,
               "tiny_seconds": _PROBE_SECONDS, "source_packages": source_packages,
               "open": {"program": smoke_case["program"], "data": initial["data"],
                        "params": initial["params"], "components": smoke_case["components"]}}
    try:
        call = subprocess.run(
            _worker_command(config["julia"], config["overlay"], solver, probe=_PROBE),
            env=config["env"], input=_canonical_json(payload), text=True, capture_output=True,
            check=False, timeout=START_TIMEOUT + _OP_TIMEOUT,
        )
        if call.returncode != 0:
            return {"outcome": "probe_failed", "returncode": call.returncode,
                    "message": call.stderr[-4000:]}
        result = json.loads(call.stdout)
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        return {"outcome": "probe_failed", "message": str(exc)}
    fields = {"julia", "packages", "project", "source", "source_trees", "package_sources",
              "julia_threads", "blas_threads", "native_version", "native_library",
              "raw_options", "handle_limit"}
    if not isinstance(result, dict) or not fields <= result.keys():
        return {"outcome": "probe_failed", "message": "invalid native preflight result"}
    result["requested"] = option_configs
    expected_trees = {name: evidence["packages"][name]["git-tree-sha1"] for name in source_packages}
    packages = evidence["packages"]
    known_package_versions = {name: packages[name]["version"] for name in result["packages"]
                              if name in packages}
    if (result["julia"] != evidence["julia"]
            or result["project"] != str(Path(config["overlay"]) / "Project.toml")
            or result["source"] != str(Path(config["overlay"]) / "src" / "DskitJuMP.jl")
            or result["source_trees"] != expected_trees
            or result["julia_threads"] != 1 or result["blas_threads"] != 1
            or any(result["packages"][name] != version
                   for name, version in known_package_versions.items())):
        result["outcome"] = "environment_mismatch"
        return result
    artifact = _native_artifact(result, config["depots"], solver, packages)
    if artifact is None:
        result["outcome"] = "environment_mismatch"
        result["message"] = "native library did not come from a pinned Julia artifact"
        return result
    result["native_artifact"] = artifact
    result["outcome"] = "passed"
    return result


def _native_artifact(result, depots, solver, packages):
    """Identify the installed native library against the pinned JLL artifact recipes."""
    library = Path(result["native_library"])
    tree = None
    for depot in depots:
        artifacts = Path(depot) / "artifacts"
        if library.is_relative_to(artifacts):
            relative = library.relative_to(artifacts)
            if len(relative.parts) > 1:
                tree = relative.parts[0]
            break
    if tree is None or len(tree) != 40 or any(c not in "0123456789abcdef" for c in tree):
        return None
    names = _ARTIFACT_PACKAGES[:1] if solver == SOLVER else _ARTIFACT_PACKAGES[1:]
    for name in names:
        toml = Path(result["package_sources"][name]) / "Artifacts.toml"
        if not toml.is_file():
            continue
        for artifact, raw in tomllib.loads(toml.read_text()).items():
            for entry in raw if isinstance(raw, list) else [raw]:
                if entry["git-tree-sha1"] == tree:
                    return {"package": name, "version": packages[name]["version"],
                            "artifact": artifact, **entry}
    return None


def _arm_check(probe, seed, arm, smoke):
    """Refuse comparison when native settings, limit restoration or a smoke trial failed."""
    if probe["outcome"] != "passed":
        return {key: probe[key] for key in ("outcome", "message") if key in probe}
    setting = probe["raw_options"][f"{seed}/{arm}"]
    if setting["outcome"] != "passed":
        return setting
    if probe["handle_limit"]["outcome"] != "passed":
        return {"outcome": "limit_probe_failed"}
    return {"outcome": "passed"} if smoke["outcome"] == "completed" else {
        "outcome": "smoke_failed", "reason": smoke["outcome"]}


def _cpu_model():
    """Return Linux's recorded CPU model when it is available."""
    try:
        with Path("/proc/cpuinfo").open() as cpu:
            for line in cpu:
                key, _, value = line.partition(":")
                if key.strip() == "model name":
                    return value.strip()
    except OSError:
        pass
    return platform.processor() or platform.machine()


def _run_comparison(config):
    """Prepare and run the bounded matrix, returning raw evidence and paired summaries."""
    evidence = _overlay_evidence(Path(config["overlay"]))
    smoke = {
        program: _make_case(program, n, bins, _COMPARISON_SEEDS[0], smoke=True)
        for program, n, bins in ((_SELECTION, 12, 0), (PROGRAM, 6, 3))
    }
    cases = [_make_case(program, 128, bins, seed, smoke=False)
             for program, bins in ((_SELECTION, 0), (PROGRAM, 6)) for seed in _COMPARISON_SEEDS]
    packages = ("DskitJuMP", "JuMP", "MathOptInterface", *_COMPARISON_SOLVERS, *_ARTIFACT_PACKAGES)
    caches = _cache_state(config["depots"], evidence["julia"], packages)
    report = {"created_utc": datetime.now(timezone.utc).isoformat(),
              "os": platform.platform(), "machine": platform.machine(),
              "cpu": _cpu_model(), "cpus": os.cpu_count(), "python": sys.version,
              "python_bridge": config["python_bridge"], "overlay": config["overlay"],
              "overlay_evidence": evidence,
              "threads": _THREAD_ENV, "cache_state_before": caches,
              "cache_policy": "process-cold, compiled modules and pkgimages existing",
              "peak_memory_method": "separate Linux VmHWM values for driver and worker",
              "seed_policy": "paired numeric seeds with solver-specific random generators",
              "time_limit_seconds": _COMPARISON_SECONDS, "objective_tolerance": _OBJECTIVE_TOLERANCE,
              "smoke_fixtures": smoke, "fixtures": cases, "native_preflight": {}, "smoke": {},
              "pairs": []}
    for solver in _COMPARISON_SOLVERS:
        print(f"{solver} native option and limit preflight", file=sys.stderr, flush=True)
        probe = _probe_solver(config, evidence, solver, smoke[_SELECTION])
        report["native_preflight"][solver] = probe
        results = {}
        report["smoke"][solver] = results
        for program, case in smoke.items():
            results[program] = {}
            for history in ("warm", "warm_with_start"):
                passed = _arm_check(probe, case["seed"], "full", {"outcome": "completed"})
                results[program][history] = (_launch_trial(case, history, solver, config)
                                             if passed["outcome"] == "passed" else passed)
    for case in cases:
        for history in _COMPARISON_HISTORIES:
            check = {}
            for solver, probe in report["native_preflight"].items():
                runs = report["smoke"][solver][case["program"]]
                check[solver] = {
                    "full": _arm_check(probe, case["seed"], "full",
                                       runs.get(history, runs["warm"])),
                    "first_incumbent": _arm_check(probe, case["seed"], "first_incumbent",
                                                  runs["warm"]),
                }
            print(f"{case['program']} seed {case['seed']} {history}", file=sys.stderr, flush=True)
            report["pairs"].extend(_paired_trials(case, history, repeat, check, config)
                                   for repeat in range(_COMPARISON_REPEATS))
    report["cache_state_after"] = _cache_state(config["depots"], evidence["julia"], packages)
    report["summary"] = _summarize(report["pairs"])
    return report


def main(argv=None):
    """Run the selected benchmark and report its results.

    Parameters
    ----------
    argv : list of str or None
        Command-line arguments (None: ``sys.argv``).

    Returns
    -------
    int
        0 on completion, 1 on a failed check or if Julia is not on ``PATH``.
    """
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--sizes", type=int, nargs="+", default=list(DEFAULT_SIZES))
    parser.add_argument("--bins", type=int, default=DEFAULT_BINS)
    parser.add_argument("--repeats", type=int, default=DEFAULT_REPEATS)
    parser.add_argument("--time-limit", type=float, default=DEFAULT_TIME_LIMIT)
    parser.add_argument("--gap", type=float, default=DEFAULT_GAP)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--scratch", default=None,
                        help="directory for Arrow files or guarded solver comparison scratch")
    parser.add_argument("--compare-solvers", action="store_true",
                        help="compare pinned HiGHS and SCIP on fixed bounded JuMP fixtures")
    parser.add_argument("--julia-overlay",
                        help="resolved Julia project with pinned SCIP for --compare-solvers")
    args = parser.parse_args(argv)
    if args.compare_solvers:
        if not args.julia_overlay or not args.scratch:
            parser.error("--compare-solvers requires --julia-overlay and --scratch")
        legacy = (args.sizes, args.bins, args.repeats, args.time_limit, args.gap, args.seed)
        if legacy != (list(DEFAULT_SIZES), DEFAULT_BINS, DEFAULT_REPEATS, DEFAULT_TIME_LIMIT,
                      DEFAULT_GAP, DEFAULT_SEED):
            parser.error("--sizes, --bins, --repeats, --time-limit, --gap and --seed "
                         "configure the default Pyomo benchmark")
    elif args.julia_overlay:
        parser.error("--julia-overlay requires --compare-solvers")
    julia = shutil.which("julia")
    if julia is None:
        print("julia is not on PATH: the worker side cannot run", file=sys.stderr)
        return 1
    if args.compare_solvers:
        try:
            config = _comparison_config(julia, args.julia_overlay, args.scratch)
            report = _run_comparison(config)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            print(f"solver comparison could not run: {exc}", file=sys.stderr)
            return 1
        print(_canonical_json(report))
        return int(any(pair["outcome"] != "completed" for pair in report["pairs"]))
    results = {}
    for n in args.sizes:
        rng = random.Random(args.seed + n)
        data = make_data(n, args.bins, rng)
        update = changed(data, rng)
        print(f"size {n:,} x {args.bins}: pyomo ...", file=sys.stderr, flush=True)
        results[n] = run_pyomo(data, update, args.repeats, args.time_limit, args.gap)
        gc.collect()
        for arrow in (False, True):
            print(f"size {n:,} x {args.bins}: JuMP {'Arrow' if arrow else 'JSON'} ...",
                  file=sys.stderr, flush=True)
            results[n].update(run_jump(data, update, args.repeats, args.time_limit,
                                       args.gap, args.scratch, arrow))
    print(table(results, args.sizes, args.bins))
    print(f"python peak RSS MiB: {resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024:.0f}")
    print(f"time limit {args.time_limit:g} s, mip_rel_gap {args.gap:g}, threads 1, "
          f"repeats {args.repeats}, seed {args.seed}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
