"""A declared HiGHS configuration for the Kelly selection, and an offline configuration sweep (ADR-0304).

A kelly method may declare ``kelly.highs``: a block of HiGHS options from :data:`HIGHS_OPTIONS`, validated here by
name, type and range, merged into the week's ``TransportPortfolioSolve`` solver options (so it reaches the LP
relaxation, the support search and the certification alike, every solve of the ``relax_fix`` strategy) and recorded in
the decision as ``kelly.highs``. A block changes how fast HiGHS reaches a certified point, never the program: options
that define feasibility, optimality or the gap, the time limit (the method's own governs) and every thread or
parallelism knob are refused (:data:`HIGHS_EXCLUDED` says why for each); ``random_seed`` must be declared, so the
configuration is complete and the derived and recorded routes stay deterministic. Without the block nothing changes.

The sweep (:class:`HighsConfigSweep`, CLI ``python -m index_options.highs_config``) measures candidate blocks on
the weekly programs a study builds at decision time. The study runs one fixed path (the method's own solves, or the
derived route over a recorded action file, which solves nothing); at every requested week the program it is about to
decide is handed to :meth:`HighsConfigSweep.observe` (the study's ``program_observer`` seam), which solves a copy under
each candidate with the method's own gap and time limit and journals one row of solver performance (outcome, time,
gap, nodes). No row carries a fill, a settlement or a P&L. :func:`freeze` applies the pre-registered rule to the train
quarters' tables. Weeks from :data:`HOLDOUT_FROM` on are refused outright.
"""

import argparse
import csv
import hashlib
import itertools
import json
import math
import os
import random
import sys
import time
from pathlib import Path

from dskit.pipeline.records import number_ok

#: HiGHS 1.15.1 options a kelly method may declare: name -> (kind, domain). ``bool`` and ``int`` values must be exactly
#: of that type; a ``double`` is any finite number in the closed range; a ``choice`` is one of the listed values (of the
#: listed type). Every range is the installed HiGHS's own (a test sets each edge and the step past it on highspy), except
#: where a value is left out on purpose: ``simplex_strategy`` 2 and 3 are the parallel dual simplex variants, ``solver``
#: ``pdlp`` is a first-order method that stops at its own looser tolerances.
HIGHS_OPTIONS = {
    "mip_heuristic_effort": ("double", (0., 1.)),
    "mip_heuristic_run_feasibility_jump": ("bool", None),
    "mip_heuristic_run_rins": ("bool", None),
    "mip_heuristic_run_rens": ("bool", None),
    "mip_heuristic_run_root_reduced_cost": ("bool", None),
    "mip_heuristic_run_zi_round": ("bool", None),
    "mip_heuristic_run_shifting": ("bool", None),
    "mip_detect_symmetry": ("bool", None),
    "mip_allow_restart": ("bool", None),
    "mip_pscost_minreliable": ("int", (0, 2147483647)),
    "mip_lp_age_limit": ("int", (0, 32767)),
    "mip_pool_age_limit": ("int", (0, 1000)),
    "mip_pool_soft_limit": ("int", (1, 2147483647)),
    "presolve": ("choice", ("off", "choose", "on")),
    "simplex_strategy": ("choice", (0, 1, 4)),
    "solver": ("choice", ("choose", "simplex", "ipm")),
    "random_seed": ("int", (0, 2147483647)),
}
#: Options a block may never declare, with the reason; any other name outside :data:`HIGHS_OPTIONS` is refused as unknown.
HIGHS_EXCLUDED = {
    **{name: "defines the gap or optimality: the method's gap governs" for name in (
        "mip_rel_gap", "mip_abs_gap", "objective_bound", "objective_target", "mip_dual_bound",
        "ipm_optimality_tolerance", "optimality_tolerance", "pdlp_optimality_tolerance")},
    **{name: "defines feasibility: the program's rows must hold to the document's tolerance" for name in (
        "mip_feasibility_tolerance", "primal_feasibility_tolerance", "dual_feasibility_tolerance",
        "primal_residual_tolerance", "dual_residual_tolerance", "small_matrix_value", "large_matrix_value",
        "infinite_bound", "infinite_cost")},
    "time_limit": "the method's time_limit governs every solve",
    **{name: "a thread or parallelism setting: not proven replay-safe" for name in (
        "threads", "parallel", "simplex_max_concurrency", "simplex_min_concurrency",
        "mip_min_cliquetable_entries_for_parallelism")},
    **{name: "an iteration or node limit: the timeout envelope recognizes only the time limit" for name in (
        "mip_max_nodes", "mip_max_leaves", "mip_max_stall_nodes", "mip_max_improving_sols",
        "simplex_iteration_limit", "ipm_iteration_limit")},
    "run_crossover": "an interior point without crossover gives no basis and no reliable reduced costs",
}
#: The sweep's quarters (ADR-0304's pre-registered protocol): configure on the train quarters, validate once on the
#: validation quarters with the frozen block; no week from :data:`HOLDOUT_FROM` on is ever opened.
TRAIN_QUARTERS = ("2019Q4", "2020Q1")
VALIDATION_QUARTERS = ("2021Q1", "2021Q2")
HOLDOUT_FROM = "2021-07-01"
#: The freeze rule's margin: a candidate replaces the default only if its mean log time to certify is at least this
#: much lower (ln 1.1, a 10% geometric-mean speed-up) and it certifies no fewer train weeks.
FREEZE_MARGIN = math.log(1.1)
#: The identifier of the absent block (HiGHS's defaults under the document's own solver options).
DEFAULT_ID = "default"
SWEEP_FORMAT = "highs-config-sweep-v1"
JOURNAL_FILE, TABLE_JSON, TABLE_CSV = "journal.jsonl", "table.json", "table.csv"
COLUMNS = ("week", "quarter", "candidate", "highs", "outcome", "certified", "timeout", "termination", "objective",
           "bound", "gap", "seconds", "solve_seconds", "nodes", "iterations", "solves", "options_verified", "error")


def highs_problems(block):
    """Return every reason a declared ``kelly.highs`` block is refused; an empty list accepts it.

    Parameters
    ----------
    block : object
        The declared block: a nonempty dict of option name to value that declares ``random_seed``.

    Returns
    -------
    list of str
    """
    if not isinstance(block, dict) or not block:
        return ["kelly.highs must be a nonempty dict of HiGHS option name to value"]
    problems = []
    for name in sorted(block, key=repr):
        value = block[name]
        if name in HIGHS_EXCLUDED:
            problems.append(f"kelly.highs may not declare {name!r}: {HIGHS_EXCLUDED[name]}")
            continue
        if name not in HIGHS_OPTIONS:
            problems.append(f"kelly.highs declares {name!r}, not a configurable HiGHS option "
                            f"(allowed: {sorted(HIGHS_OPTIONS)})")
            continue
        kind, domain = HIGHS_OPTIONS[name]
        if kind == "bool":
            ok = type(value) is bool
        elif kind == "int":
            ok = type(value) is int and domain[0] <= value <= domain[1]
        elif kind == "double":
            ok = type(value) is not bool and number_ok(value) and domain[0] <= value <= domain[1]
        else:
            ok = any(type(value) is type(choice) and value == choice for choice in domain)
        if not ok:
            problems.append(f"kelly.highs {name!r} must be {kind} in {domain}, got {value!r}")
    if "random_seed" not in block:
        problems.append("kelly.highs must declare random_seed (the configuration is complete and replayable)")
    return problems


def document_problems(solver, block):
    """Return every reason a valid block cannot run under the document's solver block.

    A declared block needs the document to pin ``threads`` to 1 (HiGHS's parallel paths are not proven deterministic)
    and must not name an option the document's ``solver_options`` already sets (one source per option).
    """
    options = solver.get("solver_options") or {}
    problems = []
    if options.get("threads") != 1 or type(options.get("threads")) is not int:
        problems.append("a kelly.highs block needs the document's solver_options to declare threads 1")
    overlap = sorted(set(block) & set(options))
    if overlap:
        problems.append(f"kelly.highs and the document's solver_options both declare {overlap}")
    return problems


def solver_options(block):
    """Return the block as HiGHS takes it: a double option's value as a float, every other value unchanged."""
    return {name: float(value) if HIGHS_OPTIONS[name][0] == "double" else value for name, value in block.items()}


def candidate_id(block):
    """Return a candidate's stable identifier: ``default`` for the absent block, else a digest of its canonical form."""
    if block is None:
        return DEFAULT_ID
    return "h" + hashlib.sha256(_canonical(block).encode("utf-8")).hexdigest()[:10]


def candidates_from_grid(grid, base=None, *, sample=None, seed=None):
    """Return the default plus a declared grid's points, each a full block, in a deterministic order.

    Parameters
    ----------
    grid : dict
        Option name to a nonempty list of distinct values (a handful of options, two or three values each).
    base : dict or None
        Options every point carries beneath the grid's (``random_seed`` at least, unless the grid varies it).
    sample : int or None
        Keep a random subset of this many grid points (without replacement), drawn by ``random.Random(seed)``;
        the kept points stay in grid order.
    seed : int or None
        The subset's declared seed; required with ``sample``.

    Returns
    -------
    list of dict
        ``{"id", "highs"}`` rows: the default (``highs`` None) first, then the points in the order of the
        product over the sorted option names and each option's declared value order.
    """
    if not isinstance(grid, dict) or not grid or any(not isinstance(v, list) or not v for v in grid.values()):
        raise ValueError("a grid maps option names to nonempty value lists")
    if any(len({_canonical(x) for x in values}) != len(values) for values in grid.values()):
        raise ValueError("a grid lists each value once")
    base = dict(base or {})
    names = sorted(grid)
    points = [{**base, **dict(zip(names, values))} for values in itertools.product(*(grid[n] for n in names))]
    if sample is not None:
        if type(sample) is not int or not 1 <= sample <= len(points) or type(seed) is not int:
            raise ValueError("a sample is an int between 1 and the grid's size, with an int seed")
        kept = sorted(random.Random(seed).sample(range(len(points)), sample))
        points = [points[i] for i in kept]
    return check_candidates([{"id": DEFAULT_ID, "highs": None}] + [{"id": candidate_id(p), "highs": p} for p in points])


def check_candidates(candidates):
    """Validate a candidate list: the default first, unique blocks, each valid, ids their digests; return it."""
    if (not isinstance(candidates, list) or not candidates or candidates[0] != {"id": DEFAULT_ID, "highs": None}
            or any(not isinstance(c, dict) or set(c) != {"id", "highs"} for c in candidates)):
        raise ValueError("candidates are {id, highs} rows with the default (highs null) first")
    for c in candidates[1:]:
        problems = highs_problems(c["highs"])
        if problems:
            raise ValueError("candidate "+str(c["id"])+": "+"; ".join(problems))
        if c["id"] != candidate_id(c["highs"]):
            raise ValueError("candidate id "+str(c["id"])+" is not its block's digest "+candidate_id(c["highs"]))
    if len({c["id"] for c in candidates}) != len(candidates):
        raise ValueError("a candidate block is listed twice")
    return candidates


def load_candidates(spec):
    """Return the candidate list a sweep spec declares: ``{"candidates": [blocks]}`` or ``{"grid", "base", ...}``."""
    if not isinstance(spec, dict):
        raise ValueError("a candidate spec is a dict")
    if set(spec) == {"candidates"}:
        blocks = spec["candidates"]
        if not isinstance(blocks, list):
            raise ValueError("candidates is a list of blocks")
        return check_candidates([{"id": DEFAULT_ID, "highs": None}]
                                + [{"id": candidate_id(b) if isinstance(b, dict) else None, "highs": b} for b in blocks])
    if "grid" in spec and set(spec) <= {"grid", "base", "sample", "seed"}:
        return candidates_from_grid(spec["grid"], spec.get("base"), sample=spec.get("sample"), seed=spec.get("seed"))
    raise ValueError("a candidate spec declares candidates, or a grid with optional base, sample and seed")


def quarter_of(day):
    """Return the calendar quarter label (``2020Q1``) of an ISO date."""
    return f"{day[:4]}Q{(int(day[5:7])-1)//3+1}"


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _source_digests():
    """Return the sha256 of the modules a sweep row depends on, so a table names the code that measured it."""
    from dskit.pipeline.libs import pyomo
    from . import ledger_studies

    return {Path(m.__file__).name: hashlib.sha256(Path(m.__file__).read_bytes()).hexdigest()
            for m in (pyomo, ledger_studies, sys.modules[__name__])}


def _highs_object(solver):
    """Return the highspy object behind an appsi solver, or None (another backend, or no solve yet)."""
    model = getattr(solver, "_solver_model", None)
    return model if model is not None and hasattr(model, "getOptionValue") else None


def _option_value(highs, name):
    value = highs.getOptionValue(name)
    return value[1] if isinstance(value, tuple) else value


class HighsConfigSweep:
    """Measure candidate HiGHS blocks on the weekly programs one study path builds at decision time.

    Parameters
    ----------
    study : WeeklyOptionPortfolioStudy
        The study whose method is measured (one kelly method of its params).
    method_id : str
        The kelly method's id.
    candidates : list of dict
        :func:`check_candidates` rows, the default first.
    weeks : list of str
        Decision weeks to measure, every one a study week before :data:`HOLDOUT_FROM`.
    out_dir : path
        Where the journal and the tables go; an existing journal of the same sweep resumes.
    derived : dict or None
        The path: None solves the method's own configuration week by week; a worker action file (or its
        ``actions`` body) drives the derived route, which solves nothing.
    host : str or None
        A label for the table header (one host per sweep: times are compared within it).
    clock : callable
        The wall clock (``time.perf_counter``).
    """

    def __init__(self, study, method_id, candidates, weeks, out_dir, *, derived=None, host=None,
                 clock=time.perf_counter):
        matches = [m for m in study.params["methods"] if m["id"] == method_id]
        if len(matches) != 1 or "kelly" not in matches[0]:
            raise ValueError("the sweep measures exactly one declared kelly method")
        self.study, self.method, self.clock = study, matches[0], clock
        self.candidates = check_candidates(candidates)
        threads = (study.params["solver"].get("solver_options") or {}).get("threads")
        if threads != 1 or type(threads) is not int:
            # HiGHS sizes one task scheduler per process from its first solve: every solve here shares threads 1.
            raise ValueError("the sweep needs the document's solver_options to declare threads 1")
        for c in self.candidates[1:]:
            problems = document_problems(study.params["solver"], c["highs"])
            if problems:
                raise ValueError("candidate "+c["id"]+": "+"; ".join(problems))
        if (not isinstance(weeks, list) or not weeks or len(set(weeks)) != len(weeks)
                or any(w not in study.weeks for w in weeks)):
            raise ValueError("weeks are distinct decision weeks of the study")
        if any(w >= HOLDOUT_FROM for w in weeks):
            raise ValueError("a week from "+HOLDOUT_FROM+" on is holdout: the sweep never opens it")
        self.weeks = [w for w in study.weeks if w in set(weeks)]
        if isinstance(derived, dict) and "actions" in derived and "format" in derived:
            derived = derived["actions"]
        self.derived = derived
        self.out = Path(out_dir)
        kelly = self.method["kelly"]
        self.header = {"format": SWEEP_FORMAT, "method": method_id, "weeks": self.weeks,
                       "candidates": self.candidates, "time_limit": kelly["time_limit"], "gap": kelly["gap"],
                       "path": "solve" if derived is None else "derived", "host": host,
                       "solver": {k: v for k, v in study.params["solver"].items()}, "code": _source_digests()}
        self.rows = {}

    # -- the journal ----------------------------------------------------------------------------------------------

    def _resume(self):
        """Load the rows an earlier run of the same sweep journaled; refuse a journal of another sweep."""
        path = self.out/JOURNAL_FILE
        if not path.exists():
            return
        lines = path.read_text(encoding="utf-8").splitlines()
        if not lines or json.loads(lines[0]) != json.loads(_canonical(self.header)):
            raise ValueError("the journal in "+str(self.out)+" belongs to another sweep (header differs)")
        keep = [lines[0]]
        for line in lines[1:]:
            try:
                row = json.loads(line)
            except ValueError:   # a row cut by a crash mid-write: dropped and measured again
                break
            self.rows[(row["week"], row["candidate"])] = row
            keep.append(line)
        if len(keep) != len(lines):
            path.write_text("\n".join(keep)+"\n", encoding="utf-8")

    def _append(self, row):
        path = self.out/JOURNAL_FILE
        new = not path.exists()
        with open(path, "a", encoding="utf-8") as handle:
            if new:
                handle.write(_canonical(self.header)+"\n")
            handle.write(_canonical(row)+"\n")
            handle.flush()
            os.fsync(handle.fileno())
        self.rows[(row["week"], row["candidate"])] = row

    # -- the measurement --------------------------------------------------------------------------------------------

    def _params(self, block):
        """Return the candidate's solver parameters, built exactly as the study builds the method's own."""
        kelly = {k: v for k, v in self.method["kelly"].items() if k != "highs"}
        if block is not None:
            kelly["highs"] = block
        return type(self.study)._kelly_solver_params(self.study.params["solver"], kelly)

    def measure(self, day, program, candidate):
        """Solve one copy of a week's program under one candidate; return its row (solver performance only)."""
        import copy
        from dskit.pipeline.libs.pyomo import TransportPortfolioSolve

        node = TransportPortfolioSolve("portfolio", self._params(candidate["highs"]))
        infos, applied = [], (solver_options(candidate["highs"]) if candidate["highs"] is not None else {})
        resolve = node._resolve_solver

        def counted_resolve():
            solver = resolve()
            solve = solver.solve

            def counted(*args, **kwargs):
                try:
                    return solve(*args, **kwargs)
                finally:
                    highs = _highs_object(solver)
                    if highs is not None:
                        info = highs.getInfo()
                        infos.append({"nodes": max(0, int(getattr(info, "mip_node_count", 0) or 0)),
                                      "iterations": max(0, int(getattr(info, "simplex_iteration_count", 0) or 0)),
                                      "verified": all(_option_value(highs, k) == v for k, v in applied.items())})
            solver.solve = counted
            return solver
        node._resolve_solver = counted_resolve
        start = self.clock()
        selection, error = None, None
        try:
            selection = node.run(None, copy.deepcopy(program))["selection"]
        except Exception as exc:  # the class only: a message can carry a value
            error = type(exc).__name__
        seconds = self.clock()-start
        certificates = [] if selection is None else (selection.get("certificates") or [{}])
        c = certificates[0] if certificates else {}
        return {"week": day, "quarter": quarter_of(day), "candidate": candidate["id"],
                "highs": None if candidate["highs"] is None else _canonical(candidate["highs"]),
                "outcome": "error" if error else c.get("strategy_outcome"),
                "certified": selection is not None and "timeout" not in selection,
                "timeout": None if selection is None else selection.get("timeout"),
                "termination": c.get("termination"), "objective": c.get("objective"), "bound": c.get("bound"),
                "gap": c.get("gap"), "seconds": seconds, "solve_seconds": c.get("seconds"),
                "nodes": sum(i["nodes"] for i in infos), "iterations": sum(i["iterations"] for i in infos),
                "solves": len(infos),
                "options_verified": None if candidate["highs"] is None else bool(infos) and all(i["verified"] for i in infos),
                "error": error}

    def observe(self, day, method, program, solver_params):
        """Measure every candidate not yet journaled on a requested week (the study's ``program_observer``)."""
        if method["id"] != self.method["id"] or day not in self.weeks:
            return
        for candidate in self.candidates:
            if (day, candidate["id"]) not in self.rows:
                self._append(self.measure(day, program, candidate))

    def run(self):
        """Run the path once, measuring every requested week; write and return the ordered table.

        A requested week the study decides without a program (no candidate, a risk input, the last session)
        gets one ``no_program`` row per candidate. The path's own result is discarded unread.
        """
        self.out.mkdir(parents=True, exist_ok=True)
        self._resume()
        if any((w, c["id"]) not in self.rows for w in self.weeks for c in self.candidates):
            previous = getattr(self.study, "program_observer", None)
            self.study.program_observer = self.observe
            try:
                self.study._method(self.method, None, derived=self.derived)
            finally:
                self.study.program_observer = previous
        order = {c["id"]: i for i, c in enumerate(self.candidates)}
        rows = []
        for w in self.weeks:
            for c in self.candidates:
                rows.append(self.rows.get((w, c["id"])) or {
                    **{k: None for k in COLUMNS}, "week": w, "quarter": quarter_of(w), "candidate": c["id"],
                    "highs": None if c["highs"] is None else _canonical(c["highs"]), "outcome": "no_program",
                    "certified": False})
        rows.sort(key=lambda r: (self.weeks.index(r["week"]), order[r["candidate"]]))
        table = {**self.header, "rows": rows}
        (self.out/TABLE_JSON).write_text(_canonical(table)+"\n", encoding="utf-8")
        with open(self.out/TABLE_CSV, "w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=COLUMNS)
            writer.writeheader()
            writer.writerows(rows)
        return table


def freeze(tables, *, quarters=TRAIN_QUARTERS, margin=FREEZE_MARGIN):
    """Apply the pre-registered freeze rule to train-quarter sweep tables; return the choice and every score.

    Every table must be a sweep of the same method, candidates, time limit and gap, and hold only weeks of the
    declared train ``quarters`` (a validation or holdout week refuses: the choice never sees one). Weeks where no
    candidate had a program are dropped. A week's time to certify is the wall seconds of a certified solve and the
    method's time limit otherwise (a declined, failed, timed-out or errored week is censored at the limit). A
    candidate's score is the mean natural log of that time (each floored at one second). The chosen block is the
    lowest score among the candidates that certify at least as many weeks as the default, if it beats the default's
    score by at least ``margin``; otherwise the default (no block) stands. Ties go to the earlier-listed candidate.
    No P&L, objective value or book enters the rule.
    """
    if not tables:
        raise ValueError("freeze needs at least one train table")
    first = tables[0]
    keys = ("format", "method", "candidates", "time_limit", "gap")
    if any(t.get(k) != first.get(k) for t in tables for k in keys) or first.get("format") != SWEEP_FORMAT:
        raise ValueError("the tables are not one sweep (format, method, candidates, time limit or gap differ)")
    rows = [r for t in tables for r in t["rows"]]
    if any(r["quarter"] not in quarters for r in rows):
        raise ValueError("a table holds a week outside the train quarters "+str(list(quarters)))
    candidates, limit = first["candidates"], float(first["time_limit"])
    weeks = sorted({r["week"] for r in rows if r["outcome"] != "no_program"})
    by = {(r["week"], r["candidate"]): r for r in rows}
    if len(by) != len(rows) or any((w, c["id"]) not in by for w in weeks for c in candidates):
        raise ValueError("every candidate needs exactly one row per train week")
    if not weeks:
        raise ValueError("no train week carried a program")
    scores = {}
    for c in candidates:
        times = [by[(w, c["id"])]["seconds"] if by[(w, c["id"])]["certified"] else limit for w in weeks]
        scores[c["id"]] = {"certified": sum(bool(by[(w, c["id"])]["certified"]) for w in weeks),
                           "mean_log_time": sum(math.log(max(1., t)) for t in times)/len(times),
                           "weeks": len(weeks)}
    default = scores[DEFAULT_ID]
    eligible = [c for c in candidates[1:] if scores[c["id"]]["certified"] >= default["certified"]]
    best = min(eligible, key=lambda c: scores[c["id"]]["mean_log_time"], default=None)
    chosen = (best if best is not None and scores[best["id"]]["mean_log_time"] <= default["mean_log_time"]-margin
              else candidates[0])
    return {"chosen": chosen["id"], "highs": chosen["highs"], "scores": scores, "margin": margin,
            "quarters": list(quarters), "weeks": weeks}


def main(argv=None):
    """``sweep`` measures candidates on a bundle's weeks; ``freeze`` applies the rule to train tables."""
    parser = argparse.ArgumentParser(description="offline HiGHS configuration sweep for a kelly method (ADR-0304)")
    sub = parser.add_subparsers(dest="command", required=True)
    s = sub.add_parser("sweep", help="measure candidate blocks on a bundled study's decision weeks")
    s.add_argument("--bundle", required=True, help="an MIOMethodBundle root (the Mac path the worker reads)")
    s.add_argument("--method", required=True, help="the kelly method id in the bundled study")
    s.add_argument("--candidates", required=True, help="JSON: {candidates: [blocks]} or {grid, base, sample, seed}")
    s.add_argument("--weeks", default="all", help="JSON list of decision weeks, or 'all' (every week before holdout)")
    s.add_argument("--work-dir", required=True, help="quote-index work directory")
    s.add_argument("--out", required=True, help="output directory (journal.jsonl, table.json, table.csv); resumes")
    s.add_argument("--path-actions", help="a worker action file of the same method: the derived path (no path solve)")
    s.add_argument("--reuse-root", help="same-device quote-index cache root")
    s.add_argument("--host", help="a host label for the table header")
    f = sub.add_parser("freeze", help="apply the pre-registered rule to train tables")
    f.add_argument("tables", nargs="+", help="table.json files of the train quarters")
    f.add_argument("--out", required=True, help="where the frozen choice is written (JSON)")
    args = parser.parse_args(argv)
    if args.command == "freeze":
        tables = [json.loads(Path(p).read_text(encoding="utf-8")) for p in args.tables]
        result = freeze(tables)
        Path(args.out).write_text(_canonical(result)+"\n", encoding="utf-8")
        sys.stdout.write(json.dumps({"chosen": result["chosen"], "highs": result["highs"]}, sort_keys=True)+"\n")
        return 0
    from .mio_worker import THREAD_VARIABLES, MIOMethodWorker

    for name in THREAD_VARIABLES:
        os.environ.setdefault(name, "1")
    out = Path(args.out).resolve()
    candidates = load_candidates(json.loads(Path(args.candidates).read_text(encoding="utf-8")))
    spec = {"bundle_root": str(Path(args.bundle).resolve()), "method": args.method, "attempt": 1,
            "work_dir": str(Path(args.work_dir).resolve()), "out": str(out/"unused-action-file.json")}
    if args.reuse_root:
        spec["reuse_root"] = str(Path(args.reuse_root).resolve())
    weekly = MIOMethodWorker(spec).load()["weekly"]
    try:
        weeks = ([w for w in weekly.weeks if w < HOLDOUT_FROM] if args.weeks == "all"
                 else json.loads(Path(args.weeks).read_text(encoding="utf-8")))
        derived = (json.loads(Path(args.path_actions).read_text(encoding="utf-8")) if args.path_actions else None)
        table = HighsConfigSweep(weekly, args.method, candidates, weeks, out, derived=derived, host=args.host).run()
        weekly.close_quote_indexes(verify=True)
    finally:
        if any(type(q).__name__ == "_SQLiteQuoteView" for q in weekly._quotes.values()):
            try:
                weekly.close_quote_indexes()
            except Exception:
                pass
    sys.stdout.write(json.dumps({"rows": len(table["rows"]), "out": str(out)}, sort_keys=True)+"\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
