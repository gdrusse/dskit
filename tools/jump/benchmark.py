"""Build, update and solve time: the JuMP worker against the pyomo pack (ADR-0328 am. 1).

The owner's acceptance benchmark for the JuMP doorway: the benefit sought from Julia is
fast constraint construction and in-place change, the solver being the same HiGHS either
way, so the two sides are timed on the SAME program at several sizes and the table says
honestly where JuMP is faster and where it is not.

The program is the ``assignment`` reference (generalized assignment: each item to at
most one bin, each bin within its capacity, maximise the value assigned), written here a
second time in pyomo exactly as the Julia worker builds it (same variables, rows,
coefficients and objective), solved by HiGHS through ``appsi_highs`` on one side and
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
"""

from __future__ import annotations

import argparse
import gc
import random
import resource
import shutil
import statistics
import sys
import time

from dskit.pipeline.libs.jump import JuliaWorker

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
        out.setdefault("pyomo termination", []).append(f"{termination}:{objective:.6g}")
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
                f"{record.termination}:{record.objective:.6g}")
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


def main(argv=None):
    """Run the benchmark and print the table.

    Parameters
    ----------
    argv : list of str or None
        Command-line arguments (None: ``sys.argv``).

    Returns
    -------
    int
        0 when it ran; 1 when Julia is not on ``PATH``.
    """
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--sizes", type=int, nargs="+", default=list(DEFAULT_SIZES))
    parser.add_argument("--bins", type=int, default=DEFAULT_BINS)
    parser.add_argument("--repeats", type=int, default=DEFAULT_REPEATS)
    parser.add_argument("--time-limit", type=float, default=DEFAULT_TIME_LIMIT)
    parser.add_argument("--gap", type=float, default=DEFAULT_GAP)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--scratch", default=None,
                        help="directory for the Arrow files (default: the system temp)")
    args = parser.parse_args(argv)
    if shutil.which("julia") is None:
        print("julia is not on PATH: the worker side cannot run", file=sys.stderr)
        return 1
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
