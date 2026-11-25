"""The JuMP pack's suite: plan without Julia, the wire protocol, every refusal.

Nothing here needs Julia. ``jump_fake_worker.py`` speaks the pack's JSON-lines protocol
from Python (a real brute-force knapsack, so answers are real) and misbehaves on request
(wrong protocol, noise, crash, hang, malformed or duplicated keys, wrong id, error reply,
bad record, deaf, ...), chosen by argv. The tests that need the real worker, parity with
the pyomo ``BudgetedSelect`` and the julia half of the cross-worker conformance class (the
same scenarios run against the fake and the real worker), are skipped with a reason naming
Julia when ``julia`` is not on ``PATH``, or naming the package when ``julia/DskitJuMP`` is
not built yet.

Layout: **Units** (params, one owner, inputs, plan, no Julia), **Protocol** (hello, solve,
one worker per launch command for the process), **Failures** (each refuses by program
name; the next solve starts a fresh worker), **Record** (worker JSON type-checked),
**Pipes** (no deadlock), **Conformance**, **Example**, **Parity** (real Julia only).
"""

from __future__ import annotations

import gc
import json
import os
import pathlib
import shlex
import shutil
import subprocess
import sys
import tempfile

import pytest

from dskit.pipeline.base import ConfigError
from dskit.pipeline.conformance import NodeProbe, conformance_suite
from dskit.pipeline.document import PipelineDocument, load_document
from dskit.pipeline.driver import run_document
from dskit.pipeline.libs import jump
from dskit.pipeline.libs.jump import (
    DEFAULT_SOLVER,
    NODE_KINDS,
    PROTOCOL_VERSION,
    JuliaWorker,
    JumpBudgetedSelect,
    JumpSolve,
    WorkerRefusal,
    register,
)
from dskit.pipeline.libs.pyomo import SolveRecord
from dskit.pipeline.node import (
    DEFAULT_NODE_KINDS,
    NodeContext,
    NodeKindRegistry,
    node_class_errors,
)
from dskit.pipeline.planner import plan

try:
    import pyarrow  # noqa: F401  (only asked whether it is installed)
except ImportError:
    _PYARROW = False
else:
    _PYARROW = True

#: The tests that write or read Arrow files; skipped by name without the extra.
needs_pyarrow = pytest.mark.skipif(
    not _PYARROW, reason="pyarrow is not installed (the 'parquet' extra): the Arrow "
    "handoff cannot be exercised")

HERE = pathlib.Path(__file__).parent
FAKE_WORKER = HERE / "jump_fake_worker.py"
ROOT = pathlib.Path(__file__).parents[2]
EXAMPLE = ROOT / "examples" / "pipeline" / "jump-budgeted-select.json"

#: Sub-second on purpose: the hang test waits this long for a reply that never comes.
SHORT_TIMEOUT = 0.5

#: A fake-worker launch is a Python start-up, not Julia compilation.
FAKE_START_TIMEOUT = 20.0

CANDIDATES = (
    {"id": "ALPHA", "cost": 6.0, "value": 9.0},
    {"id": "BRAVO", "cost": 5.0, "value": 6.0},
    {"id": "GAMMA", "cost": 5.0, "value": 6.0},
    {"id": "NOISE", "cost": 1.0, "value": 50.0},
)
SURVIVORS = ["ALPHA", "BRAVO", "GAMMA"]
BUDGET = 10.0
PARAMS = {"budget": BUDGET, "solver": DEFAULT_SOLVER, "solver_options": {}}


def fake_command(*args):
    """The launch command of the fake worker, with its argv switches."""
    return [sys.executable, str(FAKE_WORKER), *map(str, args)]


def _alive(pid):
    """Whether ``pid`` still runs (a zombie awaiting its reaper does not count)."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    try:
        with open(f"/proc/{pid}/stat", encoding="utf-8") as stat:
            return stat.read().rsplit(")", 1)[-1].split()[0] != "Z"
    except OSError:
        return True


def _ctx(tmp_path):
    return NodeContext(name="t", asof="2026-01-01", run_dir=str(tmp_path / "run"))


def _inputs(survivors=None):
    return {
        "candidates": [dict(row) for row in CANDIDATES],
        "survivors": list(SURVIVORS) if survivors is None else survivors,
    }


def _scratch_dirs(root=None):
    """The pack's scratch directories under ``root`` (default the system temp directory)."""
    base = pathlib.Path(root or tempfile.gettempdir())
    return set(base.glob(jump.SCRATCH_PREFIX + "*"))


@pytest.fixture(autouse=True)
def _no_worker_outlives_a_test():
    """Every pooled worker is shut down after each test, so no test sees another's; no
    scratch directory survives it."""
    before = _scratch_dirs()
    yield
    jump._POOL.close_all()
    assert _scratch_dirs() <= before
    assert not jump._SCRATCH_DIRS


@pytest.fixture()
def nodes():
    """Build nodes whose worker is the fake (the autouse fixture closes the pool)."""

    def build(*args, cls=JumpBudgetedSelect, timeout=SHORT_TIMEOUT, **params):
        class Backed(cls):
            worker_command = fake_command(*args)
            request_timeout = timeout
            start_timeout = FAKE_START_TIMEOUT

        return Backed("select", {**PARAMS, **params})

    return build


@pytest.fixture()
def spawned(monkeypatch):
    """Record every process the pack starts."""
    made = []
    original = subprocess.Popen

    def recording(*args, **kwargs):
        process = original(*args, **kwargs)
        made.append(process)
        return process

    monkeypatch.setattr(subprocess, "Popen", recording)
    return made


def _worker(node):
    return node._the_worker()


def _process(node):
    return _worker(node)._process


# ---------------------------------------------------------------------------
# Units: plan and validation, no Julia anywhere
# ---------------------------------------------------------------------------


class TestPlanWithoutJulia:
    def test_the_module_imports_with_no_third_party_library(self):
        import ast

        tree = ast.parse(pathlib.Path(jump.__file__).read_text(encoding="utf-8"))

        def roots(nodes):
            return {
                (alias.name if isinstance(n, ast.Import) else n.module).split(".")[0]
                for n in nodes
                if isinstance(n, (ast.Import, ast.ImportFrom)) and not getattr(n, "level", 0)
                for alias in (n.names if isinstance(n, ast.Import) else [n])
            }

        allowed = set(sys.stdlib_module_names) | {"dskit", "__future__"}
        writer = next(n for n in ast.walk(tree)
                      if isinstance(n, ast.FunctionDef) and n.name == "_write")
        inside = set(ast.walk(writer))
        outside = [n for n in ast.walk(tree) if n not in inside]
        assert roots(outside) <= allowed, sorted(roots(outside) - allowed)
        assert roots(inside) - allowed == {"pyarrow"}  # the Arrow writer, run path only

    def test_the_default_command_is_derived_from_the_module_not_hardcoded(self):
        command = JuliaWorker.default_command()
        package = pathlib.Path(jump.__file__).resolve().parents[3] / "julia" / "DskitJuMP"
        assert command[0] == "julia"
        assert f"--project={package}" in command

    def test_a_document_naming_the_node_reaches_the_planner_without_julia(self, monkeypatch):
        monkeypatch.setenv("PATH", "")
        assert shutil.which("julia") is None
        private = NodeKindRegistry()
        register(private)
        doc = PipelineDocument.from_obj({
            "name": "plan-only",
            "pipeline": {
                "evidence": {"uses": "dskit.pipeline.libs.jump:JumpBudgetedSelect",
                             "params": dict(PARAMS)},
            },
        })
        # An un-gated capital node refuses to plan: that IS the planner running, no Julia needed.
        with pytest.raises(ConfigError, match="un-gated capital"):
            plan(doc, registry=private)

    def test_the_pack_registers_nothing_on_import_and_registers_idempotently(self):
        assert "jump-budgeted-select" not in DEFAULT_NODE_KINDS
        private = NodeKindRegistry()
        register(private)
        register(private)
        cls, owned = private.get("jump-budgeted-select")
        assert cls is JumpBudgetedSelect and owned is False

    def test_node_kinds_carries_only_the_concrete_subclass(self):
        assert NODE_KINDS == (("jump-budgeted-select", JumpBudgetedSelect),)

    def test_the_abstract_base_can_never_register(self):
        problems = node_class_errors(JumpSolve, "kind 'jump-solve'")
        assert any("abstract" in p for p in problems)
        assert any("payload" in p and "extract" in p for p in problems)
        with pytest.raises(ValueError, match="abstract"):
            NodeKindRegistry().register("jump-solve", JumpSolve)

    def test_the_base_doctrine_matches_the_pyomo_one(self):
        assert JumpSolve.role == "capital"
        assert JumpSolve.outputs is None
        assert JumpSolve._PARAMS == ("solver", "solver_options")
        assert JumpSolve.solve_record is None

    def test_a_non_capital_subclass_escapes_the_capital_gate(self):
        class Assign(JumpSolve):
            role = "transform"
            outputs = ("assignment",)
            program = "assign"

            def payload(self, inputs, params):  # pragma: no cover - plan-only
                raise NotImplementedError

            def extract(self, solution):  # pragma: no cover - plan-only
                raise NotImplementedError

        private = NodeKindRegistry()
        private.register("toy-assign", Assign)
        doc = PipelineDocument.from_obj(
            {"name": "roles", "pipeline": {"assign": {"uses": "toy-assign"}}}
        )
        assert plan(doc, registry=private).role_of("assign") == "transform"


class TestParams:
    def test_the_reference_params_validate_clean(self):
        assert JumpBudgetedSelect.validate_params(PARAMS) == []

    def test_unknown_params_are_refused_by_name(self):
        problems = JumpBudgetedSelect.validate_params({**PARAMS, "time_limit": 3})
        assert any("time_limit" in p and "unknown" in p for p in problems)

    def test_a_subclass_extends_the_knob_set_by_extending_the_tuple(self):
        class Wider(JumpSolve):
            _PARAMS = JumpSolve._PARAMS + ("extra",)

        assert Wider.validate_params({"extra": 1}) == []
        assert Wider.validate_params({"other": 1})

    @pytest.mark.parametrize("name", ["", "9lives", "has space", "a;b", 7, None, ["HiGHS"]])
    def test_a_solver_name_of_the_wrong_shape_is_refused_at_plan(self, name):
        problems = JumpBudgetedSelect.validate_params({**PARAMS, "solver": name})
        assert any("solver" in p for p in problems)

    @pytest.mark.parametrize("name", ["HiGHS", "Gurobi", "SCIP", "Ipopt", "my_solver-2.0"])
    def test_a_plausible_solver_name_passes_without_asking_julia(self, name):
        assert JumpBudgetedSelect.validate_params({**PARAMS, "solver": name}) == []

    @pytest.mark.parametrize("options", [[], "x", 3, {"": 1}, {"k": [1]}, {"k": {"a": 1}},
                                         {"k": float("nan")}])
    def test_bad_solver_options_are_refused(self, options):
        problems = JumpBudgetedSelect.validate_params({**PARAMS, "solver_options": options})
        assert any("solver_options" in p for p in problems)

    def test_scalar_solver_options_pass(self):
        options = {"time_limit": 30.0, "threads": 1, "output_flag": False, "presolve": "on",
                   "x": None}
        assert JumpBudgetedSelect.validate_params({**PARAMS, "solver_options": options}) == []

    @pytest.mark.parametrize("budget", [0, -1, True, "10", float("inf"), None])
    def test_a_bad_budget_is_refused(self, budget):
        assert any("budget" in p
                   for p in JumpBudgetedSelect.validate_params({**PARAMS, "budget": budget}))

    def test_a_missing_budget_is_refused(self):
        params = {k: v for k, v in PARAMS.items() if k != "budget"}
        assert any("budget is required" in p for p in JumpBudgetedSelect.validate_params(params))


class TestOneOwner:
    """The solver-param and budget rules are the pyomo pack's, imported, never restated."""

    BAD = {"solver": "9lives", "solver_options": {"": 1, "k": [1], "n": float("nan")},
           "budget": -1, "surprise": 1}

    def test_the_pack_imports_no_private_name_from_another_module(self):
        import ast

        tree = ast.parse(pathlib.Path(jump.__file__).read_text(encoding="utf-8"))
        private = [alias.name for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
                   for alias in node.names if alias.name.startswith("_")]
        assert private == []

    def test_the_solver_param_problems_are_pyomos_with_this_packs_default(self):
        from dskit.pipeline.libs import pyomo

        class Twin(pyomo.PyomoSolve):
            _PARAMS = JumpSolve._PARAMS

        params = {k: v for k, v in self.BAD.items() if k != "budget"}
        expected = [p.replace(repr(pyomo.DEFAULT_SOLVER), repr(DEFAULT_SOLVER))
                    for p in Twin.validate_params(params)]
        assert JumpSolve.validate_params(params) == expected
        assert len(expected) == 5

    def test_the_budget_problems_are_budgetedselects(self):
        from dskit.pipeline.libs import pyomo

        for budget in (0, -1, True, "10", float("inf"), None):
            params = {"budget": budget}
            assert (JumpBudgetedSelect.validate_params(params)
                    == pyomo.BudgetedSelect.validate_params(params))
        assert JumpBudgetedSelect.validate_params({}) == pyomo.BudgetedSelect.validate_params({})


class TestInputs:
    def test_a_good_deck_validates(self):
        assert JumpBudgetedSelect("s", PARAMS).validate_inputs(_inputs()) == []

    def test_a_one_shot_iterable_is_refused_by_name(self):
        node = JumpBudgetedSelect("s", PARAMS)
        assert any("candidates" in p for p in node.validate_inputs(
            {"candidates": iter(CANDIDATES), "survivors": SURVIVORS}))
        assert any("survivors" in p for p in node.validate_inputs(
            {"candidates": list(CANDIDATES), "survivors": iter(SURVIVORS)}))

    def test_a_duplicate_id_is_refused(self):
        rows = [dict(CANDIDATES[0]), dict(CANDIDATES[0])]
        problems = JumpBudgetedSelect("s", PARAMS).validate_inputs(
            {"candidates": rows, "survivors": SURVIVORS})
        assert any("duplicate" in p for p in problems)

    def test_the_input_check_is_the_pyomo_ones_not_a_copy(self):
        from dskit.pipeline.libs.pyomo import BudgetedSelect

        assert JumpBudgetedSelect.validate_inputs is BudgetedSelect.validate_inputs


class TestOutputDoctrine:
    def test_a_subclass_with_no_outputs_is_refused_before_any_worker_starts(self, tmp_path):
        class NoContract(JumpSolve):
            program = "anything"
            worker_command = ["/nonexistent/never-launched"]

            def payload(self, inputs, params):  # pragma: no cover
                raise AssertionError("must never be reached")

            def extract(self, solution):  # pragma: no cover
                raise AssertionError("must never be reached")

        node = NoContract("bare", {})
        with pytest.raises(TypeError, match="outputs"):
            node.run(_ctx(tmp_path), {})
        assert jump._POOL.live() == []

    def test_a_subclass_naming_no_program_is_refused_before_any_worker_starts(self, tmp_path):
        class NoProgram(JumpSolve):
            outputs = ("x",)
            worker_command = ["/nonexistent/never-launched"]

            def payload(self, inputs, params):  # pragma: no cover
                raise AssertionError("must never be reached")

            def extract(self, solution):  # pragma: no cover
                raise AssertionError("must never be reached")

        with pytest.raises(TypeError, match="program"):
            NoProgram("bare", {}).run(_ctx(tmp_path), {})


# ---------------------------------------------------------------------------
# Protocol: hello, solve, reuse
# ---------------------------------------------------------------------------


class TestProtocol:
    def test_the_fake_speaks_the_version_the_pack_pins(self):
        sys.path.insert(0, str(HERE))
        try:
            import jump_fake_worker
        finally:
            sys.path.remove(str(HERE))
        assert jump_fake_worker.PROTOCOL_VERSION == PROTOCOL_VERSION

    def test_hello_reports_versions_and_the_node_keeps_them(self, tmp_path, nodes):
        node = nodes()
        node.run(_ctx(tmp_path), _inputs())
        info = node.worker_info
        assert info["protocol"] == PROTOCOL_VERSION
        assert info["julia"] == "fake-1.0"
        assert info["packages"] == {"JuMP": "fake", "HiGHS": "fake"}

    def test_the_happy_path_returns_the_declared_outputs(self, tmp_path, nodes):
        node = nodes()
        out = node.run(_ctx(tmp_path), _inputs())
        assert tuple(out) == JumpBudgetedSelect.outputs
        assert out["positions"] == ["BRAVO", "GAMMA"]
        assert out["outlay"] == pytest.approx(10.0)
        assert out["metrics"] == {"objective": pytest.approx(12.0), "n_selected": 2,
                                  "n_eligible": 3, "n_candidates": 4}
        assert node.validate_outputs(out) == []

    def test_the_solve_record_is_the_pyomo_class_with_the_same_key_set(self, tmp_path, nodes):
        node = nodes()
        assert node.solve_record is None
        node.run(_ctx(tmp_path), _inputs())
        record = node.solve_record
        assert isinstance(record, SolveRecord)
        assert tuple(record.to_obj()) == SolveRecord.field_names()
        assert record.solver == DEFAULT_SOLVER
        assert record.termination == jump.OPTIMAL_TERMINATION == "OPTIMAL"
        assert record.objective == pytest.approx(12.0)
        assert record.variables == 3 and record.constraints == 1
        assert record.binding[0]["name"] == "budget" and record.binding[0]["binding"] == 1
        assert json.loads(json.dumps(record.to_obj())) == record.to_obj()

    def test_a_jump_record_is_a_solve_event_body(self, tmp_path, nodes):
        # The same pin the pyomo pack has (tests/evaluation/test_events.py), for this pack.
        from dskit.evaluation.events import SCHEMA, Event, Solve

        assert set(SolveRecord.field_names()) <= {field.name for field in Solve.FIELDS}
        node = nodes()
        node.run(_ctx(tmp_path), _inputs())
        obj = node.solve_record.to_obj()
        event = {"schema": SCHEMA, "seq": 1, "kind": "solve", "ts_ms": 0, "known_ms": 0,
                 "instrument": None, "model": "select", **obj}
        assert Event.from_obj(event).get("binding") == obj["binding"]

    def test_an_empty_gate_deploys_zero_and_never_starts_a_worker(self, tmp_path, spawned):
        class Unlaunchable(JumpBudgetedSelect):
            worker_command = ["/nonexistent/never-launched"]

        node = Unlaunchable("select", PARAMS)
        out = node.run(_ctx(tmp_path), _inputs(survivors=[]))
        assert out == {"positions": [], "outlay": 0.0,
                       "metrics": {"objective": 0.0, "n_selected": 0, "n_eligible": 0,
                                   "n_candidates": 4}}
        assert spawned == [] and node.solve_record is None

    def test_the_default_solver_is_pinned_deterministic_and_the_document_wins(self, nodes):
        assert nodes()._solver_options() == {"mip_rel_gap": 0, "threads": 1, "random_seed": 0}
        assert nodes(solver_options={"threads": 4})._solver_options()["threads"] == 4
        assert nodes(solver="Gurobi")._solver_options() == {}

    def test_one_worker_serves_two_solves(self, tmp_path, nodes):
        node = nodes()
        node.run(_ctx(tmp_path), _inputs())
        first = _process(node)
        node.run(_ctx(tmp_path), _inputs(survivors=["ALPHA"]))
        assert _process(node) is first and first.poll() is None
        assert node.solve_record.variables == 1

    def test_close_shuts_the_worker_down_and_a_later_run_starts_a_new_one(self, tmp_path, nodes):
        node = nodes()
        node.run(_ctx(tmp_path), _inputs())
        first = _process(node)
        node.close()
        assert first.poll() is not None
        node.close()  # idempotent
        node.run(_ctx(tmp_path), _inputs())
        assert _process(node) is not first

    def test_a_fresh_node_per_fold_reuses_the_one_worker(self, tmp_path, spawned):
        # The driver builds a new node instance for every run, so for every walk-forward
        # fold; Julia start-up is paid once, not per fold (ADR-0328 decision 2).
        command = fake_command()

        class Backed(JumpBudgetedSelect):
            worker_command = command
            start_timeout = FAKE_START_TIMEOUT

        for _ in range(3):
            node = Backed("select", PARAMS)
            assert node.run(_ctx(tmp_path), _inputs())["positions"] == ["BRAVO", "GAMMA"]
            del node
            gc.collect()
        assert len(spawned) == 1 and spawned[0].poll() is None
        assert jump._POOL.live() == [tuple(command)]

    def test_nodes_with_different_commands_get_different_workers(self, tmp_path, nodes, spawned):
        nodes().run(_ctx(tmp_path), _inputs())
        nodes("--mode", "ok").run(_ctx(tmp_path), _inputs())
        assert len(spawned) == 2

    def test_interpreter_exit_leaves_no_orphan(self, tmp_path):
        # "linger" ignores stdin EOF, so only the pack's exit hook stops it.
        script = "\n".join([
            "import sys",
            f"sys.path.insert(0, {str(HERE)!r})",
            "from test_jump import FAKE_START_TIMEOUT, PARAMS, _inputs, fake_command",
            "from dskit.pipeline.libs.jump import JumpBudgetedSelect",
            "from dskit.pipeline.node import NodeContext",
            "class Backed(JumpBudgetedSelect):",
            "    worker_command = fake_command('--mode', 'linger')",
            "    start_timeout = FAKE_START_TIMEOUT",
            "node = Backed('select', PARAMS)",
            f"ctx = NodeContext(name='t', asof='2026-01-01', run_dir={str(tmp_path)!r})",
            "node.run(ctx, _inputs())",
            "print(node._the_worker().pid)",
        ])
        done = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True,
                              timeout=60, env={**os.environ, "PYTHONPATH": str(ROOT)})
        assert done.returncode == 0, done.stderr
        pid = int(done.stdout.strip().splitlines()[-1])
        assert not _alive(pid)

    @pytest.mark.parametrize("termination", ["TIME_LIMIT", "optimal", "LOCALLY_SOLVED"])
    def test_a_non_optimal_record_is_refused_on_extract(self, tmp_path, nodes, termination):
        node = nodes()
        node.run(_ctx(tmp_path), _inputs())
        node.solve_record = SolveRecord(
            solver="HiGHS", status="FEASIBLE_POINT", termination=termination, objective=None,
            bound=None, gap=None, seconds=0.0, variables=3, constraints=1, binding=())
        with pytest.raises(WorkerRefusal,
                           match=r"budgeted_select.*\(termination\).*not 'OPTIMAL'"):
            node.extract({"selected": ["BRAVO"]})

    @pytest.mark.parametrize("termination", ["INFEASIBLE", "TIME_LIMIT"])
    def test_a_non_optimal_solve_on_the_wire_refuses_by_program_and_keeps_the_worker(
        self, tmp_path, nodes, termination
    ):
        node = nodes("--mode", "patch-record", "--patch",
                     json.dumps({"termination": termination}))
        with pytest.raises(WorkerRefusal, match=r"budgeted_select.*\(termination\)"):
            node.run(_ctx(tmp_path), _inputs())
        assert _process(node).poll() is None  # a well-formed reply: the worker is kept

    def test_an_over_budget_selection_from_the_worker_is_refused(self, tmp_path, nodes):
        node = nodes("--mode", "over-budget")
        with pytest.raises(AssertionError, match="budget violated"):
            node.run(_ctx(tmp_path), _inputs())

    def test_the_wire_carries_the_program_params_and_the_pinned_solver_options(
        self, tmp_path, nodes
    ):
        log = tmp_path / "requests.jsonl"
        node = nodes("--request-log", log, solver_options={"threads": 4, "time_limit": 30})
        node.run(_ctx(tmp_path), _inputs())
        requests = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
        assert [r["op"] for r in requests] == ["hello", "solve"]
        solve = requests[1]
        assert set(solve) == {"id", "op", "program", "data", "params", "solver",
                              "solver_options"}
        assert solve["program"] == "budgeted_select" and solve["solver"] == DEFAULT_SOLVER
        assert solve["params"] == {"budget": BUDGET}
        assert solve["solver_options"] == {"mip_rel_gap": 0, "threads": 4, "random_seed": 0,
                                           "time_limit": 30}
        assert solve["data"] == {"ids": ["ALPHA", "BRAVO", "GAMMA"], "cost": [6.0, 5.0, 5.0],
                                 "value": [9.0, 6.0, 6.0]}

    def test_a_refused_solve_clears_the_previous_worker_info(self, tmp_path, nodes):
        node = nodes()
        node.run(_ctx(tmp_path), _inputs())
        assert node.worker_info is not None
        node.worker_command = ["/nonexistent/never-launched"]
        with pytest.raises(WorkerRefusal, match=r"\(start\)"):
            node.run(_ctx(tmp_path), _inputs())
        assert node.worker_info is None and node.solve_record is None

    @pytest.mark.parametrize("solution", [
        None, [], {}, {"selected": "BRAVO"}, {"selected": [1]},
        {"selected": ["BRAVO", "BRAVO"]}, {"selected": ["NOISE"]},
        {"selected": ["BRAVO"], "extra": 1},
    ])
    def test_a_bad_solution_is_refused_by_program(self, tmp_path, nodes, solution):
        node = nodes()
        node.run(_ctx(tmp_path), _inputs())
        with pytest.raises(WorkerRefusal, match=r"budgeted_select.*\(solution\)"):
            node.extract(solution)


# ---------------------------------------------------------------------------
# Failures: each refuses naming the program; the next solve starts a fresh worker
# ---------------------------------------------------------------------------


class TestFailures:
    def test_a_refusal_is_a_runtime_error_carrying_program_and_kind(self):
        refusal = WorkerRefusal("budgeted_select", "timeout", "late")
        assert isinstance(refusal, RuntimeError)
        assert (refusal.program, refusal.kind) == ("budgeted_select", "timeout")

    def test_a_protocol_mismatch_refuses_before_any_solve(self, tmp_path, nodes):
        node = nodes("--mode", "bad-protocol")
        with pytest.raises(WorkerRefusal, match=r"budgeted_select.*\(protocol\)") as excinfo:
            node.run(_ctx(tmp_path), _inputs())
        assert "999" in str(excinfo.value)
        assert node.solve_record is None and _worker(node).pid is None

    @pytest.mark.parametrize(("mode", "kind"), [
        ("stdout-noise", "malformed"),
        ("bool-id", "malformed"),
        ("hello-error", "environment"),
    ])
    def test_a_bad_hello_refuses_and_kills_the_worker(self, tmp_path, nodes, mode, kind):
        node = nodes("--mode", mode)
        with pytest.raises(WorkerRefusal, match=rf"budgeted_select.*\({kind}\)"):
            node.run(_ctx(tmp_path), _inputs())
        worker = _worker(node)
        assert worker.pid is None and worker.info is None
        # The next solve is a fresh start that must pass hello again, never skip it.
        with pytest.raises(WorkerRefusal, match=rf"\({kind}\)"):
            node.run(_ctx(tmp_path), _inputs())
        assert worker.starts == 2

    def test_a_worker_that_will_not_start_is_a_named_refusal(self, tmp_path):
        class Missing(JumpBudgetedSelect):
            worker_command = ["/nonexistent/julia-not-here"]

        with pytest.raises(WorkerRefusal, match=r"budgeted_select.*\(start\)"):
            Missing("select", PARAMS).run(_ctx(tmp_path), _inputs())

    def test_the_default_command_without_julia_refuses_by_name(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PATH", "")
        node = JumpBudgetedSelect("select", PARAMS)
        with pytest.raises(WorkerRefusal, match=r"budgeted_select.*julia"):
            node.run(_ctx(tmp_path), _inputs())

    @pytest.mark.parametrize(
        ("mode", "kind"),
        [
            ("crash", "died"),
            ("hang", "timeout"),
            ("malformed", "malformed"),
            ("wrong-id", "malformed"),
            ("dup-key", "malformed"),
            ("extra-key", "malformed"),
            ("bad-record", "record"),
        ],
    )
    def test_a_broken_worker_refuses_by_program_and_the_next_solve_is_fresh(
        self, tmp_path, nodes, mode, kind
    ):
        flag = tmp_path / "misbehaved"
        node = nodes("--mode", mode, "--once", flag)
        with pytest.raises(WorkerRefusal, match="budgeted_select") as excinfo:
            node.run(_ctx(tmp_path), _inputs())
        assert excinfo.value.kind == kind
        assert node.solve_record is None
        # Never a silent retry: one start for the one attempt, and the broken worker is gone.
        assert _worker(node).starts == 1 and _worker(node).pid is None
        out = node.run(_ctx(tmp_path), _inputs())  # the flag is spent: a fresh worker is clean
        assert out["positions"] == ["BRAVO", "GAMMA"]
        assert _worker(node).starts == 2 and _process(node).poll() is None

    def test_a_failed_solve_is_never_retried_inside_one_run(self, tmp_path, nodes):
        node = nodes("--mode", "crash")  # no --once: every worker crashes
        with pytest.raises(WorkerRefusal, match="budgeted_select"):
            node.run(_ctx(tmp_path), _inputs())
        assert _worker(node).starts == 1  # the refusal came from the one attempt
        with pytest.raises(WorkerRefusal, match="budgeted_select"):
            node.run(_ctx(tmp_path), _inputs())
        assert _worker(node).starts == 2  # the NEXT solve, not a retry, started the second

    def test_an_error_reply_refuses_with_its_kind_and_keeps_the_healthy_worker(
        self, tmp_path, nodes
    ):
        flag = tmp_path / "misbehaved"
        node = nodes("--mode", "error-reply", "--once", flag)
        with pytest.raises(WorkerRefusal, match=r"budgeted_select.*solve_failed.*refuses on request"):
            node.run(_ctx(tmp_path), _inputs())
        assert node.solve_record is None
        process = _process(node)
        assert process.poll() is None  # a clean error reply leaves a consistent worker
        assert node.run(_ctx(tmp_path), _inputs())["positions"] == ["BRAVO", "GAMMA"]
        assert _process(node) is process

    def test_an_unknown_program_is_refused_by_the_worker_by_name(self, tmp_path, nodes):
        class Other(JumpBudgetedSelect):
            program = "no_such_program"

        node = nodes(cls=Other)
        with pytest.raises(WorkerRefusal, match=r"no_such_program.*unknown_program"):
            node.run(_ctx(tmp_path), _inputs())

    def test_a_solver_the_environment_lacks_is_refused_by_name_at_run(self, tmp_path, nodes):
        node = nodes(solver="Gurobi")
        assert node.validate_params(node.params) == []  # the shape passes at plan
        with pytest.raises(WorkerRefusal, match=r"budgeted_select.*unknown_solver.*Gurobi"):
            node.run(_ctx(tmp_path), _inputs())
        assert _process(node).poll() is None

    def test_a_non_json_payload_refuses_before_any_worker_starts(self, tmp_path, nodes, spawned):
        class Nan(JumpBudgetedSelect):
            def payload(self, inputs, params):
                data = super().payload(inputs, params)
                data["value"][0] = float("nan")
                return data

        node = nodes(cls=Nan)
        with pytest.raises(WorkerRefusal, match=r"budgeted_select.*\(payload\).*JSON"):
            node.run(_ctx(tmp_path), _inputs())
        assert spawned == []

    def test_every_misbehaviour_leaves_no_orphan_after_close(self, tmp_path, nodes, spawned):
        modes = ("crash", "hang", "malformed", "wrong-id", "bad-record", "bad-protocol",
                 "stdout-noise", "hello-error", "deaf")
        for mode in modes:
            node = nodes("--mode", mode)
            with pytest.raises(WorkerRefusal):
                node.run(_ctx(tmp_path), _inputs())
            node.close()
        healthy = nodes()
        healthy.run(_ctx(tmp_path), _inputs())
        healthy.close()
        assert len(spawned) == len(modes) + 1
        assert all(p.poll() is not None for p in spawned)
        assert jump._POOL.live() == []

    def test_the_hang_timeout_is_configurable_and_fast(self, tmp_path, nodes):
        import time

        node = nodes("--mode", "hang")
        started = time.perf_counter()
        with pytest.raises(WorkerRefusal, match="timed out") as excinfo:
            node.run(_ctx(tmp_path), _inputs())
        # The solve is bounded by the node's request_timeout, never its start_timeout.
        assert f"after {SHORT_TIMEOUT:g} s" in str(excinfo.value)
        assert time.perf_counter() - started < FAKE_START_TIMEOUT / 2

    @pytest.mark.parametrize(("mode", "kind"), [
        ("hang", "timeout"),
        ("malformed", "malformed"),
        ("wrong-id", "malformed"),
        ("bad-record", "record"),
    ])
    def test_a_wrapped_worker_is_killed_whole_and_never_hangs_the_refusal(
        self, tmp_path, mode, kind
    ):
        # A wrapper that forks rather than execs (GNU timeout, a shell that does not exec)
        # keeps the real worker as a grandchild holding the pipes: killing only the direct
        # child would leave the readers blocked and the worker orphaned.
        import time

        pidfile = tmp_path / "worker.pid"
        inner = " ".join(map(shlex.quote, fake_command("--mode", mode, "--pidfile", pidfile)))

        class Wrapped(JumpBudgetedSelect):
            worker_command = ["sh", "-c", f"{inner}; true"]
            request_timeout = SHORT_TIMEOUT
            start_timeout = FAKE_START_TIMEOUT

        node = Wrapped("select", PARAMS)
        started = time.perf_counter()
        with pytest.raises(WorkerRefusal, match=r"budgeted_select") as excinfo:
            node.run(_ctx(tmp_path), _inputs())
        assert excinfo.value.kind == kind
        assert time.perf_counter() - started < FAKE_START_TIMEOUT / 2
        grandchild = int(pidfile.read_text(encoding="utf-8"))
        assert not _alive(grandchild)
        assert _worker(node).pid is None

    def test_close_of_a_wrapped_worker_that_ignores_shutdown_leaves_no_grandchild(
        self, tmp_path
    ):
        # "deaf" answers hello, then never reads: close() times out on shutdown and kills.
        pidfile = tmp_path / "worker.pid"
        inner = " ".join(map(shlex.quote, fake_command("--mode", "deaf", "--pidfile", pidfile)))
        worker = JuliaWorker(["sh", "-c", f"{inner}; true"], shutdown_timeout=SHORT_TIMEOUT)
        worker._ensure_started("budgeted_select", FAKE_START_TIMEOUT)
        grandchild = int(pidfile.read_text(encoding="utf-8"))
        assert _alive(grandchild)
        worker.close()
        assert not _alive(grandchild) and worker.pid is None


# ---------------------------------------------------------------------------
# The solve record from worker JSON: every field type-checked and finite
# ---------------------------------------------------------------------------


GOOD_ROW = {"name": "budget", "rows": 1, "binding": 1, "min_slack": 0.0}


class TestRecordFromTheWire:
    @pytest.mark.parametrize("patch", [
        {"solver": "Gurobi"},                 # not the solver that was asked for
        {"solver": 7},
        {"status": None},
        {"termination": ""},
        {"objective": "12"},
        {"objective": True},
        {"bound": [1]},
        {"gap": -0.5},
        {"seconds": None},
        {"seconds": -1.0},
        {"variables": 3.0},
        {"variables": True},
        {"variables": -1},
        {"constraints": "1"},
        {"binding": {}},
        {"binding": [[1]]},
        {"binding": [{**GOOD_ROW, "rows": 1.5}]},
        {"binding": [{**GOOD_ROW, "binding": 2}]},
        {"binding": [{**GOOD_ROW, "min_slack": "0"}]},
        {"binding": [{**GOOD_ROW, "name": ""}]},
        {"binding": [{**GOOD_ROW, "dual": None}]},
        {"binding": [{**GOOD_ROW, "extra": 1}]},
        {"binding": [{k: v for k, v in GOOD_ROW.items() if k != "min_slack"}]},
        {"extra_field": 1},
    ], ids=repr)
    def test_a_wrongly_typed_record_is_refused_and_the_worker_killed(self, tmp_path, nodes,
                                                                      patch):
        node = nodes("--mode", "patch-record", "--patch", json.dumps(patch))
        with pytest.raises(WorkerRefusal, match=r"budgeted_select.*\(record\)"):
            node.run(_ctx(tmp_path), _inputs())
        assert node.solve_record is None and _worker(node).pid is None

    @pytest.mark.parametrize("token", ["NaN", "Infinity", "-Infinity"])
    def test_a_non_finite_number_on_the_wire_is_refused_never_parsed(self, tmp_path, nodes,
                                                                     token):
        # Python's json.loads accepts these tokens by default; the pack must not.
        node = nodes("--mode", "patch-record", "--patch", f'{{"objective": {token}}}')
        with pytest.raises(WorkerRefusal, match=r"budgeted_select.*\(malformed\)"):
            node.run(_ctx(tmp_path), _inputs())

    @pytest.mark.parametrize("key", ["objective", "seconds", "gap", "bound"])
    def test_an_integer_too_large_for_a_float_is_a_record_refusal(self, tmp_path, nodes, key):
        node = nodes("--mode", "patch-record", "--patch", f'{{"{key}": 1{"0" * 400}}}')
        with pytest.raises(WorkerRefusal, match=r"budgeted_select.*\(record\)"):
            node.run(_ctx(tmp_path), _inputs())
        assert _worker(node).pid is None

    def test_a_record_with_nulls_and_a_dual_is_accepted(self, tmp_path, nodes):
        patch = {"objective": None, "bound": None, "gap": None,
                 "binding": [{**GOOD_ROW, "min_slack": None, "dual": -2.5}]}
        node = nodes("--mode", "patch-record", "--patch", json.dumps(patch))
        node.run(_ctx(tmp_path), _inputs())
        record = node.solve_record
        assert record.objective is None and record.binding[0]["dual"] == -2.5
        assert record.to_obj()["binding"] == patch["binding"]


# ---------------------------------------------------------------------------
# Pipes: no deadlock on big payloads, big replies or a chatty stderr
# ---------------------------------------------------------------------------

#: Far beyond any pipe buffer (64 KiB on Linux).
BIG_BYTES = 4 << 20


class _Padded(JumpBudgetedSelect):
    """Pads the payload with a field the program ignores."""

    def payload(self, inputs, params):
        return {**super().payload(inputs, params), "pad": "x" * BIG_BYTES}


class TestPipes:
    def test_a_payload_bigger_than_the_pipe_round_trips(self, tmp_path, nodes):
        node = nodes(cls=_Padded, timeout=60.0)
        assert node.run(_ctx(tmp_path), _inputs())["positions"] == ["BRAVO", "GAMMA"]

    def test_a_worker_that_stops_reading_cannot_hang_a_big_write(self, tmp_path, nodes):
        import time

        node = nodes("--mode", "deaf", cls=_Padded)
        started = time.perf_counter()
        with pytest.raises(WorkerRefusal, match=r"budgeted_select.*\(timeout\)"):
            node.run(_ctx(tmp_path), _inputs())
        assert time.perf_counter() - started < 10 * SHORT_TIMEOUT + FAKE_START_TIMEOUT
        assert _worker(node).pid is None

    def test_a_reply_of_megabytes_is_read_whole(self, tmp_path, nodes):
        node = nodes("--mode", "big-reply", timeout=60.0)
        node.run(_ctx(tmp_path), _inputs())
        assert len(node.solve_record.binding) == jump_fake_worker().BIG_REPLY_ROWS

    def test_a_worker_flooding_stderr_never_blocks(self, tmp_path, nodes):
        node = nodes("--mode", "noisy-stderr", timeout=60.0)
        for _ in range(3):
            assert node.run(_ctx(tmp_path), _inputs())["positions"] == ["BRAVO", "GAMMA"]

    def test_a_refusal_carries_the_stderr_tail_but_bounded(self, tmp_path):
        class Crashing(JumpBudgetedSelect):
            worker_command = [sys.executable, "-c",
                              "import sys; sys.stdin.readline(); "
                              "sys.stderr.write('boom ' * 100000 + '\\nlast words\\n'); "
                              "sys.exit(9)"]
            start_timeout = FAKE_START_TIMEOUT

        with pytest.raises(WorkerRefusal, match=r"\(died\)") as excinfo:
            Crashing("select", PARAMS).run(_ctx(tmp_path), _inputs())
        message = str(excinfo.value)
        assert "last words" in message and "exit 9" in message
        assert len(message) < 4 * jump._STDERR_TAIL_CHARS


def jump_fake_worker():
    """The fake worker module, imported from this directory."""
    sys.path.insert(0, str(HERE))
    try:
        import jump_fake_worker as module
    finally:
        sys.path.remove(str(HERE))
    return module


# ---------------------------------------------------------------------------
# Conformance: the toolkit suite pointed at the pack (the fake behind it)
# ---------------------------------------------------------------------------


class _ConformantSelect(JumpBudgetedSelect):
    """The pack's kind with the fake worker behind it, closing its worker after each run."""

    worker_command = fake_command()
    start_timeout = FAKE_START_TIMEOUT

    def run(self, ctx, inputs):
        try:
            return super().run(ctx, inputs)
        finally:
            self.close()


def probes(tmp_path):
    return {
        "jump-budgeted-select": NodeProbe(
            params=dict(PARAMS),
            required=("budget",),
            inputs=_inputs(),
            stream_ports=("candidates", "survivors"),
            runnable=True,
            budget=BUDGET,
            outlay=lambda outputs: float(outputs["outlay"]),
            gate_port="survivors",
        ),
    }


TestJumpConformance = conformance_suite(
    registry=(("jump-budgeted-select", _ConformantSelect),),
    module="dskit.pipeline.libs.jump",
    probes=probes,
    expected_roles={"jump-budgeted-select": "capital"},
    name="TestJumpConformance",
)


# ---------------------------------------------------------------------------
# The shipped example
# ---------------------------------------------------------------------------

EXPECTED_ORDER = ("evidence", "edge_test", "select")


class TestShippedExample:
    def test_it_loads_and_hashes_stably(self):
        doc = load_document(str(EXAMPLE))
        assert doc.name == "jump-select-demo"
        assert tuple(doc.pipeline) == EXPECTED_ORDER
        assert doc.hash == load_document(str(EXAMPLE)).hash

    def test_it_plans_with_no_julia(self, monkeypatch):
        monkeypatch.setenv("PATH", "")
        resolved = plan(load_document(str(EXAMPLE)))
        assert tuple(resolved.order) == EXPECTED_ORDER
        assert resolved.role_of("select") == "capital"
        assert resolved.resolved["select"].owned is False

    def test_capital_is_gated_by_the_owned_stat_test(self):
        doc = load_document(str(EXAMPLE))
        assert doc.pipeline["select"].inputs["survivors"] == "$edge_test.survivors"

    def test_it_runs_end_to_end_through_the_fake_worker(self, tmp_path, monkeypatch):
        monkeypatch.setattr(JumpBudgetedSelect, "worker_command", fake_command())
        obj = json.loads(EXAMPLE.read_text(encoding="utf-8"))
        obj["outputs"]["run_root"] = str(tmp_path / "runs")
        result = run_document(PipelineDocument.from_obj(obj), asof="2026-01-01")
        assert result.error == ""
        assert result.state == "ran" and result.exit_code == 0
        select = result.outputs["select"]
        assert select["positions"] == ["BRAVO", "GAMMA"]
        assert select["outlay"] == pytest.approx(10.0)

    def test_two_runs_in_one_process_start_one_worker(self, tmp_path, monkeypatch, spawned):
        # A walk-forward is one run_document per fold, each with fresh node instances.
        monkeypatch.setattr(JumpBudgetedSelect, "worker_command", fake_command())
        obj = json.loads(EXAMPLE.read_text(encoding="utf-8"))
        for fold in ("2026-01-01", "2026-02-01"):
            obj["outputs"]["run_root"] = str(tmp_path / fold)
            result = run_document(PipelineDocument.from_obj(obj), asof=fold)
            assert result.error == "" and result.state == "ran"
        assert len(spawned) == 1


# ---------------------------------------------------------------------------
# Parity with the pyomo BudgetedSelect: real Julia only
# ---------------------------------------------------------------------------

JULIA = shutil.which("julia")
JULIA_PACKAGE_DIR = ROOT.joinpath(*jump.JULIA_PACKAGE)


@pytest.mark.skipif(JULIA is None, reason="Julia is not installed (no `julia` on PATH): "
                    "the real-worker parity test needs it")
@pytest.mark.skipif(not (JULIA_PACKAGE_DIR / "Project.toml").is_file(),
                    reason=f"the Julia package {JULIA_PACKAGE_DIR} is not built yet "
                    "(ADR-0328 phase 1, Julia half): the real-worker parity test needs it")
class TestParityWithPyomo:
    def test_the_julia_knapsack_matches_the_pyomo_one(self, tmp_path):
        pytest.importorskip("pyomo")
        from dskit.pipeline.libs.pyomo import BudgetedSelect

        jump_node = JumpBudgetedSelect("select", PARAMS)
        try:
            theirs = jump_node.run(_ctx(tmp_path), _inputs())
        finally:
            jump_node.close()
        ours = BudgetedSelect("select", {**PARAMS, "solver": "appsi_highs"}).run(
            _ctx(tmp_path), _inputs())
        assert theirs["positions"] == ours["positions"]
        assert theirs["outlay"] == pytest.approx(ours["outlay"])
        assert theirs["metrics"] == ours["metrics"] | {
            "objective": pytest.approx(ours["metrics"]["objective"])}
        assert tuple(jump_node.solve_record.to_obj()) == SolveRecord.field_names()


# ---------------------------------------------------------------------------
# Protocol v2 (ADR-0328 amendment 1): live models, through the fake worker
# ---------------------------------------------------------------------------


def _requests(log):
    """Every request that crossed the wire, parsed."""
    if not log.exists():
        return []
    return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]


def _spec_digest(fields):
    """The ADR's canonical data digest, restated from the spec (1-D fields of str/float)."""
    import hashlib
    import struct

    def name(text):
        raw = text.encode("utf-8")
        return struct.pack("<I", len(raw)) + raw

    out = [b"dskit-data-v1\n"]
    for key in sorted(fields, key=lambda k: k.encode("utf-8")):
        values = fields[key]
        strings = all(isinstance(v, str) for v in values)
        out += [name(key), b"s" if strings else b"f", struct.pack("<I", 1),
                struct.pack("<Q", len(values))]
        out += [name(v) if strings else struct.pack("<d", float(v) + 0.0) for v in values]
    return hashlib.sha256(b"".join(out)).hexdigest()


ELIGIBLE = {"ids": ["ALPHA", "BRAVO", "GAMMA"], "cost": [6.0, 5.0, 5.0],
            "value": [9.0, 6.0, 6.0]}


@pytest.fixture()
def live(tmp_path):
    """Open live models on a fake worker; every model and the pool close after the test."""
    opened = []

    def build(*args, log=None, models=None, nbytes=None, **params):
        class Backed(JumpBudgetedSelect):
            worker_command = fake_command(*args, *(("--request-log", log) if log else ()))
            request_timeout = 30.0
            start_timeout = FAKE_START_TIMEOUT
            if models is not None:
                _PERSISTENT_CACHE_SIZE = models
            if nbytes is not None:
                _PERSISTENT_CACHE_BYTES = nbytes

        node = Backed("select", {**PARAMS, **params})
        model = node.open(_inputs())
        opened.append(model)
        return node, model

    yield build
    for model in opened:
        try:
            model.close()
        except WorkerRefusal:
            pass


class TestProtocolV2:
    def test_the_pack_speaks_protocol_two(self):
        assert PROTOCOL_VERSION == 2

    def test_hello_carries_the_pool_settings_from_the_class_knobs(self, tmp_path, nodes):
        log = tmp_path / "requests.jsonl"
        node = nodes("--request-log", log)
        node.run(_ctx(tmp_path), _inputs())
        hello = _requests(log)[0]
        assert hello == {"id": 1, "op": "hello",
                         "settings": {"pool_models": JumpSolve._PERSISTENT_CACHE_SIZE,
                                      "pool_bytes": JumpSolve._PERSISTENT_CACHE_BYTES}}
        assert (JumpSolve._PERSISTENT_CACHE_SIZE, JumpSolve._PERSISTENT_CACHE_BYTES) == (
            jump.DEFAULT_POOL_MODELS, jump.DEFAULT_POOL_BYTES)

    def test_a_v1_only_worker_is_refused_before_any_other_request(self, tmp_path, nodes):
        log = tmp_path / "requests.jsonl"
        node = nodes("--mode", "v1-only", "--request-log", log)
        with pytest.raises(WorkerRefusal, match=r"budgeted_select.*\(protocol\).*1") as excinfo:
            node.run(_ctx(tmp_path), _inputs())
        assert excinfo.value.kind == "protocol"
        assert [r["op"] for r in _requests(log)] == ["hello"]
        assert _worker(node).pid is None

    def test_a_document_that_does_not_opt_in_sends_only_the_v1_solve(self, tmp_path, nodes):
        log = tmp_path / "requests.jsonl"
        node = nodes("--request-log", log)
        assert JumpSolve._PERSISTENT_MODEL_REUSE is False
        node.run(_ctx(tmp_path), _inputs())
        assert [r["op"] for r in _requests(log)] == ["hello", "solve"]
        assert node.timings == ()


class TestDescribe:
    def test_describe_returns_the_programs_contract(self, nodes):
        described = nodes(timeout=30.0).describe()
        assert described["program"] == "budgeted_select"
        assert described["schema"]["dims"] == {"item": "ids"}
        assert described["schema"]["fields"]["cost"] == {"type": "float64", "dims": ["item"]}
        assert described["variables"] == {"select": {"index": ["item"], "domain": "binary"}}
        assert described["components"] == ["max_items"]
        assert described["update"] is True

    def test_describe_of_an_unknown_program_refuses_and_keeps_the_worker(self, nodes):
        class Other(JumpBudgetedSelect):
            program = "no_such_program"

        node = nodes(cls=Other, timeout=30.0)
        with pytest.raises(WorkerRefusal, match=r"no_such_program.*unknown_program"):
            node.describe()
        assert _process(node).poll() is None


class TestLiveModel:
    def test_open_builds_and_reports_its_digest_signature_and_timings(self, live):
        _, model = live()
        assert model.handle == "h1"
        assert model.signature.startswith("shape:")
        assert model.digest == _spec_digest(ELIGIBLE)
        timings = model.timings
        assert isinstance(timings, jump.Timings)
        assert tuple(timings.to_obj()) == jump.Timings.field_names()
        assert timings.path == "built" and timings.solve_seconds == 0
        assert timings.pool == {"models": 1, "bytes": 3000, "evicted": 0, "evictions": 0}
        assert timings.start_source is None and timings.start_accepted is None

    def test_a_handle_solve_returns_the_solution_record_and_timings(self, live):
        _, model = live()
        solution, record = model.solve()
        assert solution == {"selected": ["BRAVO", "GAMMA"]}
        assert isinstance(record, SolveRecord) and record.termination == "OPTIMAL"
        assert model.solve_record is record
        assert model.timings.path == "reused" and model.timings.solve_seconds >= 0

    def test_an_update_of_numbers_is_applied_in_place(self, live):
        _, model = live()
        model.update(params={"budget": 6.0})
        assert model.timings.path == "updated"
        assert model.digest == _spec_digest(ELIGIBLE)  # params are not data
        assert model.solve()[0] == {"selected": ["ALPHA"]}
        model.update(data={"value": [1.0, 6.0, 6.0]})
        assert model.timings.path == "updated"
        assert model.digest == _spec_digest({**ELIGIBLE, "value": [1.0, 6.0, 6.0]})
        assert model.solve()[0] == {"selected": ["BRAVO"]}

    def test_an_update_that_changes_nothing_is_reused(self, live):
        _, model = live()
        model.update(data=dict(ELIGIBLE), params={"budget": BUDGET})
        assert model.timings.path == "reused"

    def test_an_update_that_changes_the_shape_refuses_and_keeps_the_handle(self, live):
        node, model = live()
        with pytest.raises(WorkerRefusal, match=r"budgeted_select.*shape_changed"):
            model.update(data={"ids": ["ALPHA", "BRAVO", "DELTA"]})
        assert _process(node).poll() is None
        assert model.solve()[0] == {"selected": ["BRAVO", "GAMMA"]}

    def test_a_component_is_attached_by_name(self, live):
        _, model = live()
        model.update(params={"budget": BUDGET, "max_items": 1}, components={"max_items": True})
        assert model.timings.path == "updated"
        assert model.solve()[0] == {"selected": ["ALPHA"]}
        with pytest.raises(WorkerRefusal, match="unknown_component"):
            model.update(components={"no_such": True})

    def test_fix_and_free_change_the_answer(self, live):
        _, model = live()
        assert model.fix({"select": {"ALPHA": 1}}) == 1
        assert model.timings.path == "reused"
        assert model.solve()[0] == {"selected": ["ALPHA"]}
        assert model.fix({"select": {("BRAVO",): 0}}) == 2
        assert model.free({"select": ["ALPHA"]}) == 1
        assert model.solve()[0] == {"selected": ["ALPHA"]}  # BRAVO out: ALPHA alone wins
        assert model.free() == 0
        assert model.solve()[0] == {"selected": ["BRAVO", "GAMMA"]}

    def test_fixing_infeasibly_is_a_well_formed_non_optimal_solve(self, live):
        node, model = live()
        model.fix({"select": {"ALPHA": 1, "BRAVO": 1}})
        solution, record = model.solve()
        assert record.termination == "INFEASIBLE" and record.objective is None
        assert solution == {"selected": None}
        assert _process(node).poll() is None

    @pytest.mark.parametrize(("values", "kind"), [
        ({"select": {"NOISE": 1}}, "unknown_key"),
        ({"select": {"ALPHA": 2}}, "bad_value"),
        ({"weights": {"ALPHA": 1}}, "unknown_group"),
    ])
    def test_a_bad_fix_is_refused_by_the_worker_and_changes_nothing(self, live, values, kind):
        node, model = live()
        with pytest.raises(WorkerRefusal, match=rf"budgeted_select.*{kind}"):
            model.fix(values)
        assert _process(node).poll() is None
        assert model.solve()[0] == {"selected": ["BRAVO", "GAMMA"]}

    @pytest.mark.parametrize("values", [{"select": {True: 1}}, {"select": {"ALPHA": "1"}},
                                        {"select": {"ALPHA": float("nan")}}, {"select": []},
                                        {"": {"ALPHA": 1}}, []])
    def test_a_fix_of_the_wrong_shape_is_refused_before_sending(self, tmp_path, live, values):
        log = tmp_path / "requests.jsonl"
        node, model = live(log=log)
        with pytest.raises(WorkerRefusal, match=r"\(payload\)"):
            model.fix(values)
        assert [r["op"] for r in _requests(log)] == ["hello", "describe", "open"]
        assert _process(node).poll() is None

    def test_a_caller_supplied_start_seeds_a_limited_solve(self, live):
        _, model = live()
        assert model.solve(limits={"node_limit": 1})[0] == {"selected": []}
        assert model.solve_record.termination == "NODE_LIMIT"
        assert model.timings.start_accepted is None
        assert model.start({"select": {"ALPHA": 1}}) == 1  # a partial start
        assert model.timings.start_source == "values"
        assert model.solve(limits={"node_limit": 1})[0] == {"selected": ["ALPHA"]}
        assert model.timings.start_accepted is True
        assert model.timings.start_source == "values"

    def test_an_infeasible_start_is_reported_not_accepted(self, live):
        _, model = live()
        model.start({"select": {"ALPHA": 1, "BRAVO": 1, "GAMMA": 1}})
        model.solve(limits={"node_limit": 1})
        assert model.timings.start_accepted is False

    def test_start_last_uses_the_models_own_last_solution(self, live):
        _, model = live()
        with pytest.raises(WorkerRefusal, match=r"budgeted_select.*no_solution"):
            model.start("last")
        model.solve()
        assert model.start("last") == 3
        assert model.solve(limits={"node_limit": 1})[0] == {"selected": ["BRAVO", "GAMMA"]}
        assert model.timings.start_source == "last"
        assert model.start(None) == 0
        assert model.timings.start_source is None

    @pytest.mark.parametrize("limits", [{"nodes": 1}, {"node_limit": 0}, {"node_limit": True},
                                        {"node_limit": 1.5}, {"time_limit": -1},
                                        {"time_limit": float("inf")}, {"mip_rel_gap": -0.1},
                                        {"solution_limit": 0}, [], "x"])
    def test_limits_of_the_wrong_shape_are_refused_before_sending(self, tmp_path, live, limits):
        log = tmp_path / "requests.jsonl"
        node, model = live(log=log)
        with pytest.raises(WorkerRefusal, match=r"budgeted_select.*\(limits\)"):
            model.solve(limits=limits)
        assert [r["op"] for r in _requests(log)] == ["hello", "describe", "open"]
        assert _process(node).poll() is None

    def test_limits_cross_the_wire_exactly(self, tmp_path, live):
        log = tmp_path / "requests.jsonl"
        _, model = live(log=log)
        model.solve(limits={"time_limit": 5.0, "node_limit": 10, "mip_rel_gap": 0.0,
                            "solution_limit": 2})
        solve = _requests(log)[-1]
        assert solve == {"id": 4, "op": "solve", "handle": "h1",
                         "limits": {"time_limit": 5.0, "node_limit": 10, "mip_rel_gap": 0.0,
                                    "solution_limit": 2}}

    def test_the_open_request_is_exact(self, tmp_path, live):
        log = tmp_path / "requests.jsonl"
        live(log=log)
        assert _requests(log)[1] == {"id": 2, "op": "describe", "program": "budgeted_select"}
        opened = _requests(log)[2]
        assert opened == {"id": 3, "op": "open", "program": "budgeted_select",
                          "data": ELIGIBLE, "params": {"budget": BUDGET},
                          "solver": DEFAULT_SOLVER,
                          "solver_options": {"mip_rel_gap": 0, "threads": 1, "random_seed": 0},
                          "components": {}}

    def test_the_context_manager_closes_and_a_closed_handle_refuses(self, tmp_path):
        log = tmp_path / "requests.jsonl"

        class Backed(JumpBudgetedSelect):
            worker_command = fake_command("--request-log", log)
            start_timeout = FAKE_START_TIMEOUT

        with Backed("select", PARAMS).open(_inputs()) as model:
            model.solve()
        assert model.closed
        assert _requests(log)[-1] == {"id": 5, "op": "close", "handle": "h1"}
        with pytest.raises(WorkerRefusal, match=r"budgeted_select.*\(closed\)"):
            model.solve()
        model.close()  # idempotent
        assert len(_requests(log)) == 5

    def test_a_closed_model_is_reused_from_the_pool_with_its_fixes_released(self, live):
        node, model = live()
        model.fix({"select": {"ALPHA": 1}})
        model.close()
        again = node.open(_inputs())
        try:
            assert again.handle == "h2" and again.timings.path == "reused"
            assert again.timings.pool["models"] == 1
            assert again.solve()[0] == {"selected": ["BRAVO", "GAMMA"]}
        finally:
            again.close()

    def test_a_pool_hit_with_new_numbers_is_updated_not_rebuilt(self, live):
        node, model = live()
        model.close()
        node.params = {**node.params, "budget": 6.0}
        again = node.open(_inputs())
        try:
            assert again.timings.path == "updated" and again.timings.build_seconds == 0
            assert again.solve()[0] == {"selected": ["ALPHA"]}
        finally:
            again.close()

    def test_a_held_model_is_never_shared(self, live):
        node, model = live()
        second = node.open(_inputs())
        try:
            assert second.timings.path == "built" and second.timings.pool["models"] == 2
        finally:
            second.close()

    def test_past_the_count_cap_the_least_recent_model_is_evicted(self, live):
        node, model = live(models=1)
        other = node.open(_inputs(survivors=["ALPHA"]))
        try:
            assert other.timings.pool == {"models": 1, "bytes": 1000, "evicted": 1,
                                          "evictions": 1}
            with pytest.raises(WorkerRefusal, match=r"budgeted_select.*\(evicted\)"):
                model.solve()
            assert _worker(node).starts == 1  # refused, never rebuilt behind the caller
            assert _process(node).poll() is None
            assert other.solve()[0] == {"selected": ["ALPHA"]}
        finally:
            other.close()
        model.close()  # an evicted handle closes cleanly
        assert model.closed

    def test_past_the_byte_cap_the_least_recent_model_is_evicted(self, live):
        node, model = live(nbytes=3500)
        other = node.open(_inputs(survivors=["ALPHA"]))
        try:
            assert other.timings.pool["evicted"] == 1
            assert other.timings.pool["bytes"] == 1000
        finally:
            other.close()

    def test_a_handle_from_a_killed_worker_is_stale_and_never_reopened(self, tmp_path, live):
        log = tmp_path / "requests.jsonl"
        node, model = live(log=log)
        _worker(node).close()
        with pytest.raises(WorkerRefusal, match=r"budgeted_select.*\(stale_handle\)"):
            model.solve()
        assert _worker(node).pid is None and _worker(node).starts == 1
        node.run(_ctx(tmp_path), _inputs())  # a fresh worker for the next caller
        with pytest.raises(WorkerRefusal, match=r"\(stale_handle\)"):
            model.fix({"select": {"ALPHA": 1}})
        assert "fix" not in [r["op"] for r in _requests(log)]

    def test_without_an_update_hook_every_change_is_a_visible_rebuild(self, live):
        node, model = live("--mode", "no-update")
        assert node.describe()["update"] is False
        model.update(params={"budget": 6.0})
        assert model.timings.path == "rebuilt"
        assert model.solve()[0] == {"selected": ["ALPHA"]}
        model.close()
        again = node.open(_inputs())
        try:
            assert again.timings.path == "rebuilt"
        finally:
            again.close()

    @pytest.mark.parametrize("patch", [
        {"path": "cached"}, {"build_seconds": -1}, {"solve_seconds": "0"},
        {"transfer_seconds": None}, {"start_accepted": "yes"}, {"start_source": "guess"},
        {"pool": {"models": 1}}, {"pool": {"models": -1, "bytes": 0, "evicted": 0,
                                           "evictions": 0}},
        {"extra": 1},
    ], ids=repr)
    def test_a_mistyped_timings_block_kills_the_worker(self, tmp_path, patch):
        class Backed(JumpBudgetedSelect):
            worker_command = fake_command("--mode", "bad-timings", "--patch", json.dumps(patch))
            start_timeout = FAKE_START_TIMEOUT

        node = Backed("select", PARAMS)
        with pytest.raises(WorkerRefusal, match=r"budgeted_select.*\(timings\)"):
            node.open(_inputs())
        assert _worker(node).pid is None

    def test_a_non_json_payload_refuses_at_open_before_any_worker_starts(self, spawned):
        class Nan(JumpBudgetedSelect):
            worker_command = fake_command()

            def payload(self, inputs, params):
                return {**super().payload(inputs, params), "value": [float("nan")] * 3}

        with pytest.raises(WorkerRefusal, match=r"\(payload\)"):
            Nan("select", PARAMS).open(_inputs())
        assert spawned == []

    def test_bad_data_is_refused_by_the_worker_and_keeps_it(self, nodes):
        class Short(JumpBudgetedSelect):
            def payload(self, inputs, params):
                return {**super().payload(inputs, params), "cost": [1.0]}

        node = nodes(cls=Short, timeout=30.0)
        with pytest.raises(WorkerRefusal, match=r"budgeted_select.*bad_data"):
            node.open(_inputs())
        assert _process(node).poll() is None


class TestWrongHandleEcho:
    def test_a_reply_echoing_another_handle_is_malformed_and_kills(self, tmp_path):
        class Backed(JumpBudgetedSelect):
            worker_command = fake_command("--mode", "wrong-handle", "--once",
                                          tmp_path / "flag")
            start_timeout = FAKE_START_TIMEOUT

        node = Backed("select", PARAMS)
        model = node.open(_inputs())  # open issues the handle: nothing to echo
        with pytest.raises(WorkerRefusal, match=r"budgeted_select.*\(malformed\).*handle"):
            model.solve()
        assert _worker(node).pid is None


class TestRunReuse:
    """Opt-in re-use inside run(): the same knob ScenarioUtilitySolve uses."""

    def _reusing(self, log, **attrs):
        class Reusing(JumpBudgetedSelect):
            _PERSISTENT_MODEL_REUSE = True
            worker_command = fake_command("--request-log", log)
            start_timeout = FAKE_START_TIMEOUT

        for name, value in attrs.items():
            setattr(Reusing, name, value)
        return Reusing

    def test_the_knob_is_opt_in_and_named_as_the_pyomo_one(self):
        from dskit.pipeline.libs.pyomo import ScenarioUtilitySolve

        assert JumpSolve._PERSISTENT_MODEL_REUSE is False
        assert ScenarioUtilitySolve._PERSISTENT_MODEL_REUSE is False
        assert JumpSolve._PERSISTENT_CACHE_SIZE == ScenarioUtilitySolve._PERSISTENT_CACHE_SIZE

    def test_fresh_nodes_per_fold_build_once_and_reuse_after(self, tmp_path):
        log = tmp_path / "requests.jsonl"
        cls = self._reusing(log)
        outs, paths = [], []
        for _ in range(3):
            node = cls("select", PARAMS)
            outs.append(node.run(_ctx(tmp_path), _inputs()))
            paths.append([(op, t.path) for op, t in node.timings])
            assert node.solve_record.termination == "OPTIMAL"
        assert outs[0]["positions"] == ["BRAVO", "GAMMA"] and outs[0] == outs[1] == outs[2]
        assert paths == [[("open", "built"), ("solve", "reused")],
                         [("open", "reused"), ("solve", "reused")],
                         [("open", "reused"), ("solve", "reused")]]
        assert [r["op"] for r in _requests(log)] == (
            ["hello", "describe"] + ["open", "solve", "close"] * 3)  # described once

    def test_new_numbers_update_and_a_new_shape_builds(self, tmp_path):
        cls = self._reusing(tmp_path / "requests.jsonl")
        cls("select", PARAMS).run(_ctx(tmp_path), _inputs())
        node = cls("select", {**PARAMS, "budget": 6.0})
        assert node.run(_ctx(tmp_path), _inputs())["positions"] == ["ALPHA"]
        assert node.timings[0][1].path == "updated"
        node = cls("select", PARAMS)
        node.run(_ctx(tmp_path), _inputs(survivors=["ALPHA", "BRAVO"]))
        assert node.timings[0][1].path == "built"

    def test_reuse_returns_what_the_one_shot_returns(self, tmp_path, nodes):
        plain = nodes(timeout=30.0)
        expected = plain.run(_ctx(tmp_path), _inputs())
        reusing = self._reusing(tmp_path / "requests.jsonl")("select", PARAMS)
        assert reusing.run(_ctx(tmp_path), _inputs()) == expected
        assert reusing.solve_record.to_obj() | {"seconds": 0} == (
            plain.solve_record.to_obj() | {"seconds": 0})

    @pytest.mark.parametrize(("name", "value"), [
        ("_PERSISTENT_MODEL_REUSE", 1), ("_PERSISTENT_MODEL_REUSE", "yes"),
        ("_PERSISTENT_CACHE_SIZE", 0), ("_PERSISTENT_CACHE_SIZE", True),
        ("_PERSISTENT_CACHE_SIZE", 2.0), ("_PERSISTENT_CACHE_BYTES", 0),
    ])
    def test_a_bad_knob_refuses_by_name_before_any_worker_starts(self, tmp_path, spawned,
                                                                  name, value):
        cls = self._reusing(tmp_path / "requests.jsonl", **{name: value})
        with pytest.raises(TypeError, match=name):
            cls("select", PARAMS).run(_ctx(tmp_path), _inputs())
        assert spawned == []


# ---------------------------------------------------------------------------
# The reviewed backlog of the v1 candidate
# ---------------------------------------------------------------------------


class TestBacklog:
    def test_a_float_literal_that_overflows_is_malformed_not_infinity(self, tmp_path, nodes):
        node = nodes("--mode", "float-overflow")
        with pytest.raises(WorkerRefusal, match=r"budgeted_select.*\(malformed\)"):
            node.run(_ctx(tmp_path), _inputs())
        assert _worker(node).pid is None

    def test_a_deeply_nested_reply_is_malformed_and_kills_the_worker(self, tmp_path, nodes):
        node = nodes("--mode", "deep-reply", timeout=30.0)
        with pytest.raises(WorkerRefusal, match=r"budgeted_select.*\(malformed\)"):
            node.run(_ctx(tmp_path), _inputs())
        assert _worker(node).pid is None

    def test_nesting_past_the_protocol_depth_is_malformed(self):
        worker = JuliaWorker(fake_command())
        line = json.dumps({"id": 1, "ok": True, "solution": [[[]]], "record": {}})
        assert jump._json_depth(json.loads(line)) == 4
        deep = '{"id": 1, "ok": true, "solution": ' + "[" * 600 + "]" * 600 + ', "record": {}}'
        with pytest.raises(WorkerRefusal, match=r"\(malformed\).*deep"):
            worker._reply("budgeted_select", "solve", deep, 1)

    def test_an_interrupted_solve_discards_the_worker(self, tmp_path, nodes):
        import signal
        import threading

        flag = tmp_path / "misbehaved"
        node = nodes("--mode", "hang", "--once", flag, timeout=30.0)
        timer = threading.Timer(1.0, os.kill, (os.getpid(), signal.SIGINT))
        timer.start()
        try:
            with pytest.raises(KeyboardInterrupt):
                node.run(_ctx(tmp_path), _inputs())
        finally:
            timer.cancel()
        assert _worker(node).pid is None
        assert node.run(_ctx(tmp_path), _inputs())["positions"] == ["BRAVO", "GAMMA"]
        assert _worker(node).starts == 2

    def test_the_start_timeout_bounds_a_slow_hello(self, tmp_path):
        import time

        class Slow(JumpBudgetedSelect):
            worker_command = fake_command("--mode", "slow-hello")
            request_timeout = 60.0
            start_timeout = SHORT_TIMEOUT

        started = time.perf_counter()
        with pytest.raises(WorkerRefusal, match=r"budgeted_select.*\(timeout\)") as excinfo:
            Slow("select", PARAMS).run(_ctx(tmp_path), _inputs())
        assert f"after {SHORT_TIMEOUT:g} s" in str(excinfo.value)
        assert time.perf_counter() - started < FAKE_START_TIMEOUT / 2

    def test_the_libs_package_names_the_jump_pack(self):
        from dskit.pipeline import libs

        assert "``jump.py``" in libs.__doc__

    def test_a_busy_worker_outlives_a_killed_parent_only_until_its_solve_ends(self, tmp_path):
        import signal
        import time

        pidfile = tmp_path / "worker.pid"
        script = "\n".join([
            "import sys",
            f"sys.path.insert(0, {str(HERE)!r})",
            "from test_jump import FAKE_START_TIMEOUT, PARAMS, _inputs, fake_command",
            "from dskit.pipeline.libs.jump import JumpBudgetedSelect",
            "from dskit.pipeline.node import NodeContext",
            "class Busy(JumpBudgetedSelect):",
            f"    worker_command = fake_command('--mode', 'slow-solve', '--delay', '4',"
            f" '--pidfile', {str(pidfile)!r})",
            "    start_timeout = FAKE_START_TIMEOUT",
            f"ctx = NodeContext(name='t', asof='2026-01-01', run_dir={str(tmp_path)!r})",
            "Busy('select', PARAMS).run(ctx, _inputs())",
        ])
        parent = subprocess.Popen([sys.executable, "-c", script],
                                  env={**os.environ, "PYTHONPATH": str(ROOT)})
        try:
            deadline = time.monotonic() + FAKE_START_TIMEOUT
            while not pidfile.exists() and time.monotonic() < deadline:
                time.sleep(0.05)
            time.sleep(1.0)  # the worker has answered hello and is inside the solve
            worker = int(pidfile.read_text(encoding="utf-8"))
            parent.send_signal(signal.SIGKILL)
            parent.wait(timeout=10)
            assert _alive(worker)  # busy: it does not see EOF until the solve ends
            deadline = time.monotonic() + 20
            while _alive(worker) and time.monotonic() < deadline:
                time.sleep(0.1)
            assert not _alive(worker)
        finally:
            if parent.poll() is None:
                parent.kill()


# ---------------------------------------------------------------------------
# Part 2 of amendment 1: the data contract (A4), the Arrow handoff, the search (A6)
# ---------------------------------------------------------------------------

#: A schema over every element type and a two-dimensional field, for the unit tests.
WIDE_SCHEMA = {
    "fields": {"name": {"type": "string", "dims": ["row"]},
               "code": {"type": "int64", "dims": ["col"]},
               "grid": {"type": "float64", "dims": ["row", "col"]},
               "flag": {"type": "bool", "dims": ["row"]},
               "scale": {"type": "float64", "dims": []}},
    "dims": {"row": "name", "col": None},
}
WIDE = {"name": ["r1", "r2"], "code": [7, -1, 0], "grid": [[1.0, -0.0, 2], [3.5, 4.0, 5.0]],
        "flag": [True, False], "scale": 2}


def _canon(schema, data):
    """The wire spec's digest, restated INDEPENDENTLY of the pack (its byte layout)."""
    import hashlib
    import struct

    def name(text):
        raw = text.encode("utf-8")
        return struct.pack("<I", len(raw)) + raw

    def flat(value, depth):
        return [value] if depth == 0 else [x for item in value for x in flat(item, depth - 1)]

    def shape(value, depth):
        return [] if depth == 0 else [len(value)] + (shape(value[0], depth - 1) if value
                                                     else [0] * (depth - 1))

    tags = {"float64": b"f", "int64": b"i", "string": b"s", "bool": b"b"}
    out = [b"dskit-data-v1\n"]
    for key in sorted(data, key=lambda k: k.encode("utf-8")):
        spec = schema["fields"][key]
        depth = len(spec["dims"])
        out += [name(key), tags[spec["type"]], struct.pack("<I", depth)]
        out += [struct.pack("<Q", n) for n in shape(data[key], depth)]
        for value in flat(data[key], depth):
            out.append({"float64": lambda v: struct.pack("<d", 0.0 if v == 0 else float(v)),
                        "int64": lambda v: struct.pack("<q", v),
                        "string": name,
                        "bool": lambda v: b"\x01" if v else b"\x00"}[spec["type"]](value))
    return hashlib.sha256(b"".join(out)).hexdigest()


class TestDataContract:
    def test_a_valid_payload_checks_and_digests_as_the_spec_states(self):
        contract = jump.DataContract("p", WIDE_SCHEMA)
        full = contract.check(WIDE)
        assert full["grid"] == ((2, 3), (1.0, -0.0, 2, 3.5, 4.0, 5.0))
        assert full["scale"] == ((), (2,))
        assert contract.digest(full) == _canon(WIDE_SCHEMA, WIDE)

    def test_the_digest_ignores_how_a_float_is_spelled_and_the_sign_of_zero(self):
        contract = jump.DataContract("p", WIDE_SCHEMA)
        same = {**WIDE, "grid": [[1, 0.0, 2.0], [3.5, 4, 5]], "scale": 2.0}
        assert contract.digest(contract.check(same)) == contract.digest(contract.check(WIDE))

    def test_the_contract_keeps_the_workers_schema_not_a_copy(self):
        assert jump.DataContract("p", WIDE_SCHEMA).schema is WIDE_SCHEMA

    @pytest.mark.parametrize(("patch", "field"), [
        ({"extra": [1.0]}, "extra"),
        ({"code": [7, -1, "0"]}, "code"),
        ({"code": [7, -1, True]}, "code"),
        ({"code": [7, -1, 0.5]}, "code"),
        ({"code": [7, -1, 1 << 63]}, "code"),
        ({"flag": [1, 0]}, "flag"),
        ({"name": ["r1", 2]}, "name"),
        ({"grid": [[1.0, 2.0, 3.0], [4.0, 5.0]]}, "grid"),
        ({"grid": [1.0, 2.0]}, "grid"),
        ({"grid": [[1.0, 2.0], [3.0, 4.0]]}, "grid"),
        ({"flag": [True]}, "flag"),
        ({"name": ["r1", "r1"]}, "name"),
        ({"scale": [2.0]}, "scale"),
        ({"scale": float("inf")}, "scale"),
    ], ids=repr)
    def test_a_defect_refuses_as_bad_data_naming_program_and_field(self, patch, field):
        with pytest.raises(WorkerRefusal, match=rf"'p'.*\(bad_data\).*'{field}'") as excinfo:
            jump.DataContract("p", WIDE_SCHEMA).check({**WIDE, **patch})
        assert excinfo.value.kind == "bad_data"

    def test_a_missing_field_refuses_at_open_but_not_at_update(self):
        contract = jump.DataContract("p", WIDE_SCHEMA)
        partial = {k: v for k, v in WIDE.items() if k != "flag"}
        with pytest.raises(WorkerRefusal, match=r"\(bad_data\).*'flag'.*required"):
            contract.check(partial)
        full = contract.check(WIDE)
        merged = contract.check({"scale": 3.0}, full)
        assert merged["scale"] == ((), (3.0,)) and merged["grid"] == full["grid"]

    def test_an_update_disagreeing_with_the_live_data_names_the_field_sent(self):
        contract = jump.DataContract("p", WIDE_SCHEMA)
        with pytest.raises(WorkerRefusal, match=r"field 'code' has 2 along 'col'"):
            contract.check({"code": [1, 2]}, contract.check(WIDE))

    @pytest.mark.parametrize("schema", [
        {"fields": {"x": {"type": "float64", "dims": ["i"]}}, "dims": {"i": "x"}},
        {"fields": {"x": {"type": "string", "dims": ["i", "j"]}}, "dims": {"i": "x", "j": None}},
        {"fields": {"x": {"type": "string", "dims": ["i"]}}, "dims": {}},
    ], ids=["float-label", "two-dim-label", "undeclared-dim"])
    def test_a_describe_whose_schema_breaks_the_label_rules_is_malformed(self, schema):
        reply = {"program": "p", "schema": schema, "variables": {}, "components": [],
                 "update": True}
        assert jump._describe_problems(reply, "p")


def _logged(node_cls, log, **attrs):
    """A fake-backed subclass of ``node_cls`` with a request log and extra attributes."""

    class Backed(node_cls):
        worker_command = fake_command("--request-log", log)
        start_timeout = FAKE_START_TIMEOUT

    for name, value in attrs.items():
        setattr(Backed, name, value)
    return Backed("select", PARAMS)


class _Payload(JumpBudgetedSelect):
    """A JumpBudgetedSelect whose payload a test patches."""

    patch = {}

    def payload(self, inputs, params):
        return {**super().payload(inputs, params), **self.patch}


class TestContractOnTheWire:
    @pytest.mark.parametrize(("patch", "field"), [
        ({"weight": [1.0, 1.0, 1.0]}, "weight"),
        ({"cost": [6.0, 5.0, "5"]}, "cost"),
        ({"cost": [6.0, 5.0]}, "cost"),
        ({"ids": ["ALPHA", "ALPHA", "GAMMA"]}, "ids"),
    ], ids=repr)
    def test_a_bad_payload_refuses_before_open_is_sent(self, tmp_path, patch, field):
        log = tmp_path / "requests.jsonl"
        node = _logged(_Payload, log, patch=patch)
        with pytest.raises(WorkerRefusal, match=rf"budgeted_select.*\(bad_data\).*'{field}'"):
            node.open(_inputs())
        assert [r["op"] for r in _requests(log)] == ["hello", "describe"]
        assert _process(node).poll() is None

    def test_a_non_finite_value_refuses_by_field_before_any_worker_starts(self, spawned):
        node = _logged(_Payload, "unused.jsonl", patch={"value": [1.0, float("nan"), 1.0]})
        with pytest.raises(WorkerRefusal, match=r"budgeted_select.*\(payload\).*'value'"):
            node.open(_inputs())
        assert spawned == []

    def test_a_bad_update_refuses_before_it_is_sent(self, tmp_path, live):
        log = tmp_path / "requests.jsonl"
        node, model = live(log=log)
        with pytest.raises(WorkerRefusal, match=r"\(bad_data\).*'cost' has 2 along 'item'"):
            model.update(data={"cost": [1.0, 2.0]})
        with pytest.raises(WorkerRefusal, match=r"\(bad_data\).*'budget'"):
            model.update(data={"budget": [1.0]})
        assert "update" not in [r["op"] for r in _requests(log)]
        assert model.solve()[0] == {"selected": ["BRAVO", "GAMMA"]}

    def test_describe_is_asked_once_per_worker_and_program(self, tmp_path):
        log = tmp_path / "requests.jsonl"
        node = _logged(JumpBudgetedSelect, log)
        for _ in range(2):
            with node.open(_inputs()) as model:
                model.update(data={"value": [1.0, 1.0, 1.0]})
        node.describe()
        assert [r["op"] for r in _requests(log)].count("describe") == 1
        _worker(node).close()
        node.open(_inputs()).close()
        assert [r["op"] for r in _requests(log)].count("describe") == 2  # a new process

    @pytest.mark.parametrize(("data", "field"), [
        ({**ELIGIBLE, "weight": [1.0, 1.0, 1.0]}, "weight"),
        ({"ids": ELIGIBLE["ids"], "cost": ELIGIBLE["cost"]}, "value"),
        ({**ELIGIBLE, "cost": [6.0, 5.0, "5"]}, "cost"),
        ({**ELIGIBLE, "cost": [6.0, 5.0]}, "item"),
        ({**ELIGIBLE, "cost": {"arrow": {"path": "/nonexistent.arrow", "sha256": "0" * 64,
                                         "shape": [3]}}}, "cost"),
    ], ids=repr)
    def test_the_fake_worker_checks_the_contract_again_on_its_side(self, nodes, data, field):
        node = nodes(timeout=30.0)
        worker = _worker(node)
        node.describe()
        request = {"op": "open", "program": "budgeted_select", "data": data,
                   "params": {"budget": BUDGET}, "solver": DEFAULT_SOLVER,
                   "solver_options": {}, "components": {}}
        with worker._lock, pytest.raises(WorkerRefusal, match=rf"\(bad_data\).*{field}"):
            worker._exchange("budgeted_select", request, 30.0)
        assert _process(node).poll() is None

    def test_a_digest_other_than_the_data_sent_kills_the_worker(self, tmp_path):
        node = _logged(JumpBudgetedSelect, tmp_path / "r.jsonl",
                       worker_command=fake_command("--mode", "bad-digest"))
        with pytest.raises(WorkerRefusal, match=r"budgeted_select.*\(digest\)"):
            node.open(_inputs())
        assert _worker(node).pid is None

    def test_every_update_reply_digest_is_checked_against_the_data_sent(self, live):
        _, model = live()
        model.update(data={"value": [1.0, 6.0, 6.0]})
        assert model.digest == _spec_digest({**ELIGIBLE, "value": [1.0, 6.0, 6.0]})
        assert model.data["value"] == ((3,), (1.0, 6.0, 6.0))

    def test_the_one_shot_solve_stays_v1_with_no_contract(self, tmp_path, nodes):
        log = tmp_path / "requests.jsonl"
        nodes("--request-log", log, cls=_Padded, timeout=30.0).run(_ctx(tmp_path), _inputs())
        assert [r["op"] for r in _requests(log)] == ["hello", "solve"]


def _arrow_node(log, root, threshold=2, **attrs):
    return _logged(JumpBudgetedSelect, log, arrow_threshold=threshold,
                   scratch_root=str(root), **attrs)


def _files(root):
    return sorted(p for p in pathlib.Path(root).rglob("*") if p.is_file())


class TestArrowHandoff:
    @needs_pyarrow
    def test_an_array_past_the_threshold_crosses_as_an_arrow_file(self, tmp_path):
        log, root = tmp_path / "requests.jsonl", tmp_path / "scratch"
        root.mkdir()
        node = _arrow_node(log, root)
        with node.open(_inputs()) as model:
            sent = _requests(log)[2]["data"]
            assert set(sent) == {"ids", "cost", "value"}
            for name, ref in sent.items():
                assert set(ref) == {"arrow"} and set(ref["arrow"]) == {"path", "sha256",
                                                                       "shape"}
                assert ref["arrow"]["shape"] == [3]
                assert pathlib.Path(ref["arrow"]["path"]).is_absolute()
                assert pathlib.Path(ref["arrow"]["path"]).parent.parent == root.resolve()
            assert model.digest == _spec_digest(ELIGIBLE)  # the same digest as JSON
            assert _files(root) == []  # removed once the request was answered
            assert model.solve()[0] == {"selected": ["BRAVO", "GAMMA"]}
            model.update(data={"value": [20.0, 1.0, 1.0]})
            assert "arrow" in _requests(log)[-1]["data"]["value"]
            assert model.digest == _spec_digest({**ELIGIBLE, "value": [20.0, 1.0, 1.0]})
            assert model.solve()[0] == {"selected": ["ALPHA"]}
            assert _files(root) == []
        assert len(list(root.iterdir())) == 1  # the worker's directory, until it goes
        node.close()
        assert list(root.iterdir()) == []

    def test_the_threshold_is_exclusive_and_small_arrays_stay_json(self, tmp_path):
        log, root = tmp_path / "requests.jsonl", tmp_path / "scratch"
        root.mkdir()
        _arrow_node(log, root, threshold=3).open(_inputs()).close()
        assert _requests(log)[2]["data"] == ELIGIBLE
        assert list(root.iterdir()) == []  # no directory until a file is needed

    @needs_pyarrow
    def test_the_arrow_file_is_the_one_column_ipc_file_the_spec_names(self, tmp_path,
                                                                      monkeypatch):
        import pyarrow as pa

        root = tmp_path / "scratch"
        root.mkdir()
        seen = []
        original = jump._ArrowHandoff.remove

        def keep(files):
            seen.extend(pa.ipc.open_file(str(p)).read_all() for p in files)
            original(files)

        monkeypatch.setattr(jump._ArrowHandoff, "remove", staticmethod(keep))
        _arrow_node(tmp_path / "r.jsonl", root).open(_inputs()).close()
        types = {table.schema.field("values").type for table in seen}
        assert {tuple(t.column_names) for t in seen} == {("values",)}
        assert types == {pa.string(), pa.float64()}
        floats = sorted(t.column("values").to_pylist() for t in seen
                        if t.schema.field("values").type == pa.float64())
        strings = [t.column("values").to_pylist() for t in seen
                   if t.schema.field("values").type == pa.string()]
        assert floats == sorted([ELIGIBLE["cost"], ELIGIBLE["value"]])
        assert strings == [ELIGIBLE["ids"]]

    @needs_pyarrow
    def test_a_refused_request_leaves_no_arrow_file(self, tmp_path):
        root = tmp_path / "scratch"
        root.mkdir()
        node = _arrow_node(tmp_path / "r.jsonl", root)
        with node.open(_inputs()) as model:
            with pytest.raises(WorkerRefusal, match="shape_changed"):
                model.update(data={"ids": ["ALPHA", "BRAVO", "DELTA"]})
            assert _files(root) == []

    @needs_pyarrow
    def test_a_killed_worker_takes_its_scratch_directory_with_it(self, tmp_path):
        root = tmp_path / "scratch"
        root.mkdir()
        node = _arrow_node(tmp_path / "r.jsonl", root,
                           worker_command=fake_command("--mode", "bad-digest"))
        with pytest.raises(WorkerRefusal, match=r"\(digest\)"):
            node.open(_inputs())
        assert list(root.iterdir()) == []

    def test_without_pyarrow_a_big_array_refuses_naming_the_extra(self, tmp_path,
                                                                   monkeypatch):
        log, root = tmp_path / "requests.jsonl", tmp_path / "scratch"
        root.mkdir()
        monkeypatch.setitem(sys.modules, "pyarrow", None)
        node = _arrow_node(log, root)
        with pytest.raises(WorkerRefusal, match=rf"budgeted_select.*\(arrow\).*'cost'.*"
                           rf"dskit\[{jump.ARROW_EXTRA}\]"):
            node.open(_inputs())
        assert [r["op"] for r in _requests(log)] == ["hello", "describe"]
        assert _process(node).poll() is None
        assert _files(root) == []

    def test_the_named_extra_installs_pyarrow(self):
        import tomllib

        extras = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))[
            "project"]["optional-dependencies"]
        assert any(dep.startswith("pyarrow") for dep in extras[jump.ARROW_EXTRA])

    @pytest.mark.parametrize(("name", "value"), [
        ("arrow_threshold", -1), ("arrow_threshold", True), ("arrow_threshold", 1.5),
        ("scratch_root", "/nonexistent/dskit-scratch"), ("scratch_root", 3),
    ])
    def test_a_bad_transport_knob_refuses_by_name_before_any_worker_starts(
            self, spawned, name, value):
        node = _logged(JumpBudgetedSelect, "unused.jsonl", **{name: value})
        with pytest.raises(TypeError, match=name):
            node.open(_inputs())
        assert spawned == []

    def test_workers_with_other_transport_settings_are_other_workers(self, tmp_path):
        root = tmp_path / "scratch"
        root.mkdir()
        one = _arrow_node(tmp_path / "r.jsonl", root, threshold=2)
        two = _arrow_node(tmp_path / "r.jsonl", root, threshold=3)
        assert _worker(one) is not _worker(two)
        assert _worker(one).transport == {"arrow_threshold": 2,
                                          "scratch_root": str(root.resolve())}

    def test_interpreter_exit_leaves_no_scratch_directory(self, tmp_path):
        root = tmp_path / "scratch"
        root.mkdir()
        script = "\n".join([
            "import sys",
            f"sys.path.insert(0, {str(HERE)!r})",
            "from test_jump import FAKE_START_TIMEOUT, PARAMS, _inputs, fake_command",
            "from dskit.pipeline.libs.jump import JumpBudgetedSelect, JuliaWorker",
            "class Big(JumpBudgetedSelect):",
            "    worker_command = fake_command()",
            "    start_timeout = FAKE_START_TIMEOUT",
            "    arrow_threshold = 0",
            f"    scratch_root = {str(root)!r}",
            "Big('select', PARAMS).open(_inputs())  # never closed",
            "w = JuliaWorker(fake_command(), transport={'arrow_threshold': 0,"
            f" 'scratch_root': {str(root)!r}}})",
            "w.open('budgeted_select', {'ids': ['A'], 'cost': [1.0], 'value': [1.0]},"
            " {'budget': 1.0}, 'HiGHS', {}, timeout=20, start_timeout=20)",
            "assert len(list(__import__('pathlib').Path(sys.argv[1]).iterdir())) == 2",
        ])
        subprocess.run([sys.executable, "-c", script, str(root)], check=True, timeout=60,
                       env={**os.environ, "PYTHONPATH": str(ROOT)})
        assert list(root.iterdir()) == []

    def test_the_example_plans_with_neither_pyarrow_nor_julia(self, tmp_path):
        script = "\n".join([
            "import sys",
            "tried = []",
            "class Block:",
            "    def find_spec(self, name, path=None, target=None):",
            "        if name.split('.')[0] == 'pyarrow':",
            "            tried.append(name)",
            "            raise ImportError('pyarrow is blocked')",
            "sys.meta_path.insert(0, Block())",
            "from dskit.pipeline.document import load_document",
            "from dskit.pipeline.libs import jump",
            "from dskit.pipeline.node import DEFAULT_NODE_KINDS",
            "from dskit.pipeline.planner import plan",
            "jump.register()",
            f"plan(load_document({str(EXAMPLE)!r}), DEFAULT_NODE_KINDS)",
            "assert tried == [], tried  # planning never even tried to import pyarrow",
        ])
        env = {**os.environ, "PYTHONPATH": str(ROOT), "PATH": str(tmp_path)}
        subprocess.run([sys.executable, "-c", script], check=True, timeout=60, env=env)


#: A six-item knapsack on which a two-item neighbourhood search improves and stalls.
SEARCH_DATA = {"ids": ["A", "B", "C", "D", "E", "F"],
               "cost": [4.0, 3.0, 3.0, 2.0, 2.0, 5.0],
               "value": [5.0, 4.0, 4.0, 3.0, 2.5, 6.0]}
SEARCH_PARAMS = {"free": 2, "stall": 3}


@pytest.fixture()
def searching(tmp_path):
    """A live model of SEARCH_DATA whose last solution is the poor point {F}."""
    log = tmp_path / "requests.jsonl"
    worker = JuliaWorker(fake_command("--request-log", log))
    models = []

    def build(*args):
        nonlocal worker
        if args:
            worker.close()
            worker = JuliaWorker(fake_command(*args))
        model = worker.open("budgeted_select", SEARCH_DATA, {"budget": 9.0}, DEFAULT_SOLVER,
                            {}, timeout=30.0, start_timeout=FAKE_START_TIMEOUT)
        models.append(model)
        model.start({"select": {"F": 1}})
        assert model.solve({"node_limit": 1})[0] == {"selected": ["F"]}
        model.start(None)
        return model, log

    yield build
    worker.close()


def _rows(trajectory):
    return [(r.round, r.objective, r.bound, r.accepted, r.neighbourhood) for r in trajectory]


class TestSearch:
    def test_a_seeded_search_returns_its_incumbent_record_and_pinned_trajectory(
            self, searching):
        model, _ = searching()
        solution, record, trajectory = model.search("fix_and_reoptimise", SEARCH_PARAMS,
                                                    limits={"rounds": 20}, seed=7)
        assert solution == {"selected": ["D", "E", "F"]}
        assert isinstance(record, SolveRecord) and record.objective == 11.5
        assert all(isinstance(row, jump.SearchRound) for row in trajectory)
        assert _rows(trajectory) == [(1, 9.0, 9.0, True, 2), (2, 11.5, 11.5, True, 2),
                                     (3, 11.5, 11.5, False, 2), (4, 11.5, 11.5, False, 2),
                                     (5, 11.5, 11.5, False, 2)]
        assert model.solve_record is record and model.trajectory == trajectory
        assert model.timings.path == "reused" and model.timings.start_accepted is None

    def test_the_same_seed_gives_the_same_trajectory_and_another_seed_another(
            self, searching):
        first, _ = searching()
        again, _ = searching()
        other, _ = searching()
        one = first.search("fix_and_reoptimise", SEARCH_PARAMS, seed=7)
        two = again.search("fix_and_reoptimise", SEARCH_PARAMS, seed=7)
        three = other.search("fix_and_reoptimise", SEARCH_PARAMS, seed=1)
        assert one[0] == two[0] and _rows(one[2]) == _rows(two[2])
        assert three[0] == {"selected": ["A", "F"]}

    def test_the_seeded_draw_is_the_specs_splitmix64_and_partial_fisher_yates(self):
        """Restated from the spec, so a Julia search can match the fake draw for draw."""
        u64 = (1 << 64) - 1

        def splitmix(seed):
            state = seed
            while True:
                state = (state + 0x9E3779B97F4A7C15) & u64
                z = ((state ^ (state >> 30)) * 0xBF58476D1CE4E5B9) & u64
                z = ((z ^ (z >> 27)) * 0x94D049BB133111EB) & u64
                yield z ^ (z >> 31)

        # SplitMix64's published test vector for seed 0.
        outputs = splitmix(0)
        assert [next(outputs) for _ in range(3)] == [
            0xE220A8397B1DCDAF, 0x6E789E6AA1B965F4, 0x06C45D188009454F]
        stream, keys = splitmix(7), ["A", "B", "C", "D", "E", "F"]
        expected = []
        for _ in range(2):
            pool = list(keys)
            for j in range(2):
                t = j + next(stream) % (len(pool) - j)
                pool[j], pool[t] = pool[t], pool[j]
            expected.append(pool[:2])
        fake = jump_fake_worker().SplitMix64(7)
        assert [fake.draw(keys, 2), fake.draw(keys, 2)] == expected == [["D", "F"],
                                                                       ["A", "E"]]

    def test_the_request_is_exact_on_the_wire(self, searching):
        model, log = searching()
        model.search("fix_and_reoptimise", SEARCH_PARAMS, limits={"rounds": 3,
                     "time_limit": 60.0}, seed=5, round_limits={"node_limit": 50})
        assert _requests(log)[-1] == {
            "id": _requests(log)[-1]["id"], "op": "search", "handle": model.handle,
            "search": "fix_and_reoptimise", "params": SEARCH_PARAMS, "seed": 5,
            "limits": {"rounds": 3, "time_limit": 60.0}, "round_limits": {"node_limit": 50}}

    def test_the_round_limit_caps_the_trajectory(self, searching):
        model, _ = searching()
        assert len(model.search("fix_and_reoptimise", SEARCH_PARAMS, limits={"rounds": 1},
                                seed=2)[2]) == 1

    def test_the_callers_fixes_survive_and_the_incumbent_becomes_the_last_solution(
            self, searching):
        model, _ = searching()
        model.fix({"select": {"E": 1}})
        solution, _, _ = model.search("fix_and_reoptimise", SEARCH_PARAMS, seed=11,
                                      start={"select": {"E": 1}})
        assert "E" in solution["selected"]
        assert model.free({"select": []}) == 1  # the caller's fix is still there
        model.free()
        model.start("last")
        assert model.solve({"node_limit": 1})[0] == solution

    def test_a_start_is_sent_first_and_seeds_the_first_solve(self, searching):
        model, log = searching()
        model.search("fix_and_reoptimise", SEARCH_PARAMS, seed=2,
                     start={"select": {"A": 1, "B": 1}}, round_limits={"node_limit": 1})
        assert [r["op"] for r in _requests(log)][-2:] == ["start", "search"]
        assert model.timings.start_accepted is True
        assert model.timings.start_source == "values"

    @pytest.mark.parametrize(("kwargs", "kind"), [
        ({"limits": {"rounds": 0}}, "limits"),
        ({"limits": {"rounds": True}}, "limits"),
        ({"limits": {"time_limit": -1.0}}, "limits"),
        ({"limits": {"node_limit": 1}}, "limits"),
        ({"limits": []}, "limits"),
        ({"round_limits": {"rounds": 1}}, "limits"),
        ({"seed": -1}, "payload"),
        ({"seed": 1 << 53}, "payload"),
        ({"seed": True}, "payload"),
        ({"seed": 1.0}, "payload"),
        ({"algorithm": ""}, "payload"),
        ({"params": [1]}, "payload"),
        ({"params": {"free": float("nan")}}, "payload"),
    ], ids=repr)
    def test_a_request_of_the_wrong_shape_refuses_before_sending(self, searching, kwargs,
                                                                 kind):
        model, log = searching()
        sent = len(_requests(log))
        call = {"algorithm": "fix_and_reoptimise", "params": SEARCH_PARAMS,
                "start": {"select": {"A": 1}}, **kwargs}
        with pytest.raises(WorkerRefusal, match=rf"budgeted_select.*\({kind}\)"):
            model.search(**call)
        assert len(_requests(log)) == sent  # not even the start

    @pytest.mark.parametrize(("algorithm", "params", "kind"), [
        ("tabu", SEARCH_PARAMS, "unknown_search"),
        ("fix_and_reoptimise", {"free": 2}, "bad_request"),
    ])
    def test_the_worker_refuses_an_unknown_search_or_bad_params_and_is_kept(
            self, searching, algorithm, params, kind):
        model, _ = searching()
        with pytest.raises(WorkerRefusal, match=rf"budgeted_select.*{kind}"):
            model.search(algorithm, params)
        assert model.solve()[0] == {"selected": ["A", "B", "D"]}

    @pytest.mark.parametrize("patch", [
        {"round": 2}, {"objective": "11"}, {"bound": float("inf")}, {"seconds": -1},
        {"accepted": 1}, {"neighbourhood": -1}, {"neighbourhood": True}, {"extra": 1},
    ], ids=repr)
    def test_a_mistyped_trajectory_kills_the_worker(self, searching, patch):
        model, _ = searching("--mode", "bad-trajectory", "--patch", json.dumps(patch))
        with pytest.raises(WorkerRefusal, match=r"budgeted_select.*\((trajectory|malformed)\)"):
            model.search("fix_and_reoptimise", SEARCH_PARAMS, seed=2)
        assert model._worker.pid is None

    def test_a_trajectory_longer_than_the_round_limit_is_refused(self):
        row = {"round": 1, "objective": 1.0, "bound": None, "seconds": 0.0, "accepted": True,
               "neighbourhood": 0}
        assert jump._trajectory_problems([row, {**row, "round": 2}], 1)
        assert not jump._trajectory_problems([row, {**row, "round": 2}], 2)

    def test_python_holds_no_search_algorithm(self):
        import ast
        import inspect
        import textwrap

        source = textwrap.dedent(inspect.getsource(jump.LiveModel.search))
        loops = [n for n in ast.walk(ast.parse(source)) if isinstance(n, (ast.For, ast.While))]
        assert loops == []
        assert "search" in jump.OPS


# ---------------------------------------------------------------------------
# Candidate ca735fd3 skeptic corrections
# ---------------------------------------------------------------------------

#: Three items on which the last solution at budget 10 (all three) breaks a fix or a
#: smaller budget.
STALE_DATA = {"ids": ["A", "B", "C"], "cost": [1.0, 2.0, 3.0], "value": [5.0, 4.0, 9.0]}
STALE_SEARCH = {"free": 1, "stall": 2}


@pytest.fixture()
def stale():
    """Open STALE_DATA models on one fake worker at a budget; the worker closes after."""
    worker = JuliaWorker(fake_command())

    def build(budget):
        return worker.open("budgeted_select", STALE_DATA, {"budget": budget}, DEFAULT_SOLVER,
                           {}, timeout=30.0, start_timeout=FAKE_START_TIMEOUT)

    yield build
    worker.close()


class TestSearchNeverReturnsAStaleIncumbent:
    """A last solution found under other fixes, data, params or components is no incumbent."""

    def test_a_fix_made_after_the_last_solve_binds_the_search(self, stale):
        model = stale(10.0)
        assert model.solve()[0] == {"selected": ["A", "B", "C"]}
        model.fix({"select": {"A": 0}})
        solution, record, _ = model.search("fix_and_reoptimise", STALE_SEARCH, seed=3)
        assert solution == {"selected": ["B", "C"]}
        assert record.objective == 13.0 and record.termination == "SEARCH_DONE"
        assert record.bound == 13.0 and record.gap == 0.0  # the whole model's first solve

    def test_new_params_after_the_last_solve_bind_the_search(self, stale):
        model = stale(10.0)
        model.solve()
        model.update(params={"budget": 3.0})
        solution, record, _ = model.search("fix_and_reoptimise", STALE_SEARCH, seed=3)
        assert solution == {"selected": ["C"]} and record.objective == 9.0

    def test_new_data_after_the_last_solve_binds_the_search(self, stale):
        model = stale(10.0)
        model.solve()
        model.update(data={"cost": [1.0, 2.0, 30.0]})
        solution, record, _ = model.search("fix_and_reoptimise", STALE_SEARCH, seed=3)
        assert solution == {"selected": ["A", "B"]} and record.objective == 9.0

    def test_another_nodes_solution_never_comes_back_through_a_pool_hit(self, stale):
        first = stale(10.0)
        first.solve()
        first.close()
        second = stale(3.0)
        assert second.timings.path == "updated"
        solution, record, _ = second.search("fix_and_reoptimise", STALE_SEARCH, seed=3)
        assert solution == {"selected": ["C"]} and record.objective == 9.0
        assert second.start("last") == 3  # the pool hit kept it, as a start only


def _rewrite(monkeypatch, op, change, nth=1):
    """Rewrite the ``nth`` ``ok`` reply to a request of ``op`` before the pack checks it."""
    original = JuliaWorker._exchange_once
    seen = []

    def rewriting(self, program, request, timeout, expect):
        reply = original(self, program, request, timeout, expect)
        if request["op"] == op:
            seen.append(op)
            if len(seen) == nth:
                change(reply)
        return reply

    monkeypatch.setattr(JuliaWorker, "_exchange_once", rewriting)


GOOD_DESCRIBE = {"program": "p",
                 "schema": {"fields": {"ids": {"type": "string", "dims": ["item"]},
                                       "cost": {"type": "float64", "dims": ["item"]}},
                            "dims": {"item": "ids"}},
                 "variables": {"select": {"index": ["item"], "domain": "binary"}},
                 "components": [], "update": True}


class TestDescribeNeverRaisesRaw:
    @pytest.mark.parametrize("path", [
        ("schema", "fields", "cost", "type"), ("schema", "fields", "ids", "type"),
        ("variables", "select", "domain"), ("schema", "fields", "cost", "dims"),
        ("schema", "dims", "item"),
    ], ids=repr)
    def test_an_unhashable_value_is_a_problem_not_a_type_error(self, path):
        reply = json.loads(json.dumps(GOOD_DESCRIBE))
        assert jump._describe_problems(reply, "p") == []
        target = reply
        for key in path[:-1]:
            target = target[key]
        target[path[-1]] = [target[path[-1]]]
        assert jump._describe_problems(reply, "p")

    def test_a_mistyped_describe_on_the_wire_is_malformed_and_kills(self, monkeypatch, nodes):
        _rewrite(monkeypatch, "describe",
                 lambda r: r["schema"]["fields"]["cost"].update(type=["float64"]))
        node = nodes(timeout=30.0)
        with pytest.raises(WorkerRefusal, match=r"budgeted_select.*\(malformed\)"):
            node.open(_inputs())
        assert _worker(node).pid is None


class TestBookkeepingFollowsTheWorker:
    def test_an_interrupt_after_an_update_reply_kills_the_worker(self, monkeypatch, live):
        node, model = live()
        original = jump.LiveModel._settle

        def interrupted(self, reply, data=None):
            if data is not None:
                raise KeyboardInterrupt
            original(self, reply, data)

        monkeypatch.setattr(jump.LiveModel, "_settle", interrupted)
        with pytest.raises(KeyboardInterrupt):
            model.update(data={"cost": [1.0, 2.0, 100.0]})
        assert _worker(node).pid is None
        with pytest.raises(WorkerRefusal, match=r"\(stale_handle\)"):
            model.solve()

    def test_an_interrupt_while_opening_the_handle_kills_the_worker(self, monkeypatch, nodes):
        def interrupted(self, *args, **kwargs):
            raise KeyboardInterrupt

        node = nodes(timeout=30.0)
        node.describe()
        monkeypatch.setattr(jump.LiveModel, "__init__", interrupted)
        with pytest.raises(KeyboardInterrupt):
            node.open(_inputs())
        assert _worker(node).pid is None  # no held handle leaks in a live pool

    def test_the_digest_is_computed_before_the_update_is_sent(self, tmp_path, monkeypatch,
                                                              live):
        log = tmp_path / "requests.jsonl"
        node, model = live(log=log)
        sent, data = len(_requests(log)), model.data

        def interrupted(self, full):
            raise KeyboardInterrupt

        monkeypatch.setattr(jump.DataContract, "digest", interrupted)
        with pytest.raises(KeyboardInterrupt):
            model.update(data={"cost": [1.0, 2.0, 100.0]})
        assert len(_requests(log)) == sent and model.data is data
        assert _process(node).poll() is None
        assert model.solve()[0] == {"selected": ["BRAVO", "GAMMA"]}


class TestARefusedSearchSaysItsStartStays:
    def test_the_refusal_names_the_start_left_in_effect(self, searching):
        model, _ = searching()
        with pytest.raises(WorkerRefusal, match=r"\(unknown_search\).*start sent before "
                                                r"the search IS in effect") as excinfo:
            model.search("nope", {}, start={"select": {"A": 1}})
        assert excinfo.value.kind == "unknown_search"
        assert model.timings.start_source == "values"

    def test_without_a_start_the_refusal_is_the_workers_own(self, searching):
        model, _ = searching()
        with pytest.raises(WorkerRefusal) as excinfo:
            model.search("nope", {})
        assert "in effect" not in str(excinfo.value)


class TestTimingsRules:
    @pytest.mark.parametrize(("op", "patch"), [
        ("fix", {"path": "built"}),
        ("fix", {"path": "updated"}),
        ("fix", {"start_accepted": True}),
        ("solve", {"start_accepted": True}),
        ("solve", {"path": "updated"}),
        ("open", {"solve_seconds": 1.0}),
        ("open", {"path": "updated", "build_seconds": 1.0}),
        ("open", {"pool": {"models": 1, "bytes": 3000, "evicted": 1, "evictions": 0}}),
        ("fix", {"pool": {"models": 1, "bytes": 3000, "evicted": 1, "evictions": 1}}),
        ("update", {"path": "built"}),
    ], ids=repr)
    def test_a_timings_block_breaking_the_spec_rules_kills(self, op, patch):
        class Backed(JumpBudgetedSelect):
            worker_command = fake_command("--mode", "bad-timings", "--on", op,
                                          "--patch", json.dumps(patch))
            request_timeout = 30.0
            start_timeout = FAKE_START_TIMEOUT

        node = Backed("select", PARAMS)
        calls = {"fix": lambda m: m.fix({"select": {"ALPHA": 1}}),
                 "solve": lambda m: m.solve(),
                 "update": lambda m: m.update(params={"budget": 6.0})}
        with pytest.raises(WorkerRefusal, match=r"budgeted_select.*\(timings\)"):
            model = node.open(_inputs())
            calls[op](model)
        assert _worker(node).pid is None

    def test_an_update_reply_changing_the_signature_is_malformed(self, monkeypatch, live):
        node, model = live()
        _rewrite(monkeypatch, "update", lambda r: r.update(signature="shape:other"))
        with pytest.raises(WorkerRefusal, match=r"\(malformed\).*shape_changed"):
            model.update(params={"budget": 6.0})
        assert _worker(node).pid is None

    @pytest.mark.parametrize(("nth", "handle"), [(1, "x1"), (1, "h0"), (1, "h01"),
                                                 (2, "h1")])
    def test_an_open_reply_with_a_handle_off_the_counter_is_malformed(self, monkeypatch,
                                                                      nodes, nth, handle):
        _rewrite(monkeypatch, "open", lambda r: r.update(handle=handle), nth=nth)
        node = nodes(timeout=30.0)
        with pytest.raises(WorkerRefusal, match=r"\(malformed\).*counter"):
            for _ in range(nth):
                node.open(_inputs())
        assert _worker(node).pid is None


class TestPoolRecency:
    """The fake's pool is the executable reference: pin LRU order and the strict caps."""

    def test_untouched_the_least_recently_opened_model_is_the_victim(self, live):
        node, first = live(models=2)
        second = node.open(_inputs(["ALPHA"]))
        third = node.open(_inputs(["BRAVO"]))
        try:
            assert third.timings.pool["evicted"] == 1
            with pytest.raises(WorkerRefusal, match=r"\(evicted\)"):
                first.solve()
            assert second.solve()[0] == {"selected": ["ALPHA"]}
            assert first.close() is False  # an evicted handle closes unpooled
        finally:
            second.close()
            third.close()

    def test_a_successful_op_makes_its_model_the_most_recent(self, live):
        node, first = live(models=2)
        second = node.open(_inputs(["ALPHA"]))
        first.solve()
        third = node.open(_inputs(["BRAVO"]))
        try:
            with pytest.raises(WorkerRefusal, match=r"\(evicted\)"):
                second.solve()
            assert first.solve()[0] == {"selected": ["BRAVO", "GAMMA"]}
        finally:
            second.close()
            third.close()

    def test_an_error_reply_leaves_recency_unchanged(self, live):
        node, first = live(models=2)
        second = node.open(_inputs(["ALPHA"]))
        with pytest.raises(WorkerRefusal, match=r"\(unknown_key\)"):
            first.fix({"select": {"NOPE": 1}})
        third = node.open(_inputs(["BRAVO"]))
        try:
            with pytest.raises(WorkerRefusal, match=r"\(evicted\)"):
                first.solve()
            assert second.solve()[0] == {"selected": ["ALPHA"]}
        finally:
            second.close()
            third.close()

    def test_close_makes_the_model_the_most_recent(self, live):
        node, first = live(models=2)
        second = node.open(_inputs(["ALPHA"]))
        assert first.close() is True
        third = node.open(_inputs(["BRAVO"]))
        again = node.open(_inputs())
        try:
            with pytest.raises(WorkerRefusal, match=r"\(evicted\)"):
                second.solve()
            assert again.timings.path == "reused"  # the closed model stayed pooled
        finally:
            for model in (second, third, again):
                model.close()

    def test_the_count_cap_evicts_only_past_it(self, live):
        node, first = live(models=2)
        second = node.open(_inputs(["ALPHA"]))
        try:
            assert second.timings.pool == {"models": 2, "bytes": 4000, "evicted": 0,
                                           "evictions": 0}
        finally:
            second.close()

    def test_the_byte_cap_evicts_only_past_it(self, live):
        node, first = live(nbytes=4000)
        second = node.open(_inputs(["ALPHA"]))
        try:
            assert second.timings.pool["evicted"] == 0 and second.timings.pool["bytes"] == 4000
        finally:
            second.close()

    def test_evictions_count_since_the_worker_started(self, live):
        node, first = live(models=1)
        second = node.open(_inputs(["ALPHA"]))
        third = node.open(_inputs(["BRAVO"]))
        try:
            assert third.timings.pool == {"models": 1, "bytes": 1000, "evicted": 1,
                                          "evictions": 2}
        finally:
            second.close()
            third.close()

    def test_a_pool_hit_keeps_the_last_solution_for_start_last(self, live):
        node, model = live()
        model.solve()
        model.close()
        again = node.open(_inputs())
        try:
            assert again.timings.path == "reused"
            assert again.start("last") == 3 and again.timings.start_source == "last"
        finally:
            again.close()


class TestWireRefusalsOnEveryPath:
    def test_a_wrong_update_digest_kills_the_worker(self, live):
        node, model = live("--mode", "bad-digest", "--on", "update")
        with pytest.raises(WorkerRefusal, match=r"\(digest\)"):
            model.update(data={"value": [1.0, 6.0, 6.0]})
        assert _worker(node).pid is None

    def test_a_wrong_handle_echo_on_an_update_kills_the_worker(self, live):
        node, model = live("--mode", "wrong-handle", "--on", "update")
        with pytest.raises(WorkerRefusal, match=r"\(malformed\).*handle"):
            model.update(params={"budget": 6.0})
        assert _worker(node).pid is None

    def test_a_trajectory_past_the_round_limit_kills_the_worker(self, searching):
        model, _ = searching("--mode", "extra-round")
        with pytest.raises(WorkerRefusal, match=r"\(trajectory\).*the request allowed 1"):
            model.search("fix_and_reoptimise", SEARCH_PARAMS, limits={"rounds": 1}, seed=7)
        assert model._worker.pid is None


class TestArrowEncoding:
    @needs_pyarrow
    def test_every_type_and_shape_is_written_as_the_spec_names(self, tmp_path):
        import pyarrow as pa

        contract = jump.DataContract("p", WIDE_SCHEMA)
        handoff = jump._ArrowHandoff(0, str(tmp_path))
        try:
            wire, files = handoff.encode(contract, WIDE, contract.check(WIDE))
            assert wire["scale"] == 2  # a scalar always travels as JSON
            read = {}
            for name in ("name", "code", "grid", "flag"):
                ref = wire[name]["arrow"]
                table = pa.ipc.open_file(ref["path"]).read_all()
                read[name] = (ref["shape"], table.schema.field("values").type,
                              table.column("values").to_pylist())
            assert read == {
                "name": ([2], pa.string(), ["r1", "r2"]),
                "code": ([3], pa.int64(), [7, -1, 0]),
                "grid": ([2, 3], pa.float64(), [1.0, -0.0, 2.0, 3.5, 4.0, 5.0]),
                "flag": ([2], pa.bool_(), [True, False]),
            }
            handoff.remove(files)
            assert _files(tmp_path) == []
        finally:
            handoff.discard()

    @needs_pyarrow
    def test_a_failed_write_removes_the_files_already_written(self, tmp_path, monkeypatch):
        contract = jump.DataContract("p", WIDE_SCHEMA)
        handoff = jump._ArrowHandoff(0, str(tmp_path))
        original, calls = jump._ArrowHandoff._write, []

        def failing(self, *args):
            calls.append(args)
            if len(calls) == 2:
                raise OSError("disk full")
            return original(self, *args)

        monkeypatch.setattr(jump._ArrowHandoff, "_write", failing)
        try:
            with pytest.raises(OSError, match="disk full"):
                handoff.encode(contract, WIDE, contract.check(WIDE))
            assert len(calls) == 2 and _files(tmp_path) == []
        finally:
            handoff.discard()


class _Raw:
    """A bare JSON-lines client of the fake worker (no pack in between)."""

    def __init__(self):
        self.process = subprocess.Popen(fake_command(), stdin=subprocess.PIPE,
                                        stdout=subprocess.PIPE, text=True)
        self.next_id = 0

    def send(self, **request):
        self.next_id += 1
        self.process.stdin.write(json.dumps({"id": self.next_id, **request}) + "\n")
        self.process.stdin.flush()
        line = self.process.stdout.readline()
        assert line, "the fake died without a reply"
        return json.loads(line)

    def close(self):
        self.process.kill()
        self.process.wait()
        self.process.stdin.close()
        self.process.stdout.close()


@pytest.fixture()
def raw():
    client = _Raw()
    assert client.send(op="hello", settings={"pool_models": 4, "pool_bytes": 1 << 32})["ok"]
    yield client
    client.close()


def _raw_open(raw, data=None):
    reply = raw.send(op="open", program="budgeted_select", data=data or ELIGIBLE,
                     params={"budget": 10.0}, solver=DEFAULT_SOLVER, solver_options={},
                     components={})
    return reply


class TestTheFakeIsAsStrictAsTheSpec:
    @pytest.mark.parametrize("limits", [
        {"node_limit": "x"}, {"node_limit": 0}, {"node_limit": True}, {"time_limit": -1},
        {"time_limit": 0}, {"mip_rel_gap": "a"}, {"mip_rel_gap": -0.1},
        {"solution_limit": 1.5},
    ], ids=repr)
    def test_a_mistyped_limit_is_bad_request(self, raw, limits):
        handle = _raw_open(raw)["handle"]
        for request in ({"op": "solve", "handle": handle, "limits": limits},
                        {"op": "search", "handle": handle, "search": "fix_and_reoptimise",
                         "params": SEARCH_PARAMS, "seed": 1, "limits": {},
                         "round_limits": limits}):
            reply = raw.send(**request)
            assert reply["ok"] is False and reply["error"]["kind"] == "bad_request"
        assert raw.send(op="solve", handle=handle, limits={})["ok"] is True

    @pytest.mark.parametrize("change", [
        {"extra": 1}, {"params": None},
    ], ids=["extra-key", "missing-params"])
    def test_a_one_shot_solve_of_the_wrong_key_set_is_bad_request(self, raw, change):
        request = {"op": "solve", "program": "budgeted_select", "data": ELIGIBLE,
                   "params": {"budget": 10.0}, "solver": DEFAULT_SOLVER,
                   "solver_options": {}, **change}
        request = {k: v for k, v in request.items() if v is not None}
        reply = raw.send(**request)
        assert reply["ok"] is False and reply["error"]["kind"] == "bad_request"

    def test_a_non_number_fix_value_is_bad_request(self, raw):
        handle = _raw_open(raw)["handle"]
        reply = raw.send(op="fix", handle=handle,
                         values={"select": {"keys": [["ALPHA"]], "values": [True]}})
        assert reply["ok"] is False and reply["error"]["kind"] == "bad_request"

    @needs_pyarrow
    def test_an_arrow_stream_file_is_bad_data(self, raw, tmp_path):
        import hashlib

        import pyarrow as pa

        path = tmp_path / "cost.arrows"
        table = pa.table({"values": pa.array(ELIGIBLE["cost"], type=pa.float64())})
        with pa.OSFile(str(path), "wb") as sink, pa.ipc.new_stream(sink, table.schema) as out:
            out.write_table(table)
        ref = {"arrow": {"path": str(path), "sha256": hashlib.sha256(
            path.read_bytes()).hexdigest(), "shape": [3]}}
        reply = _raw_open(raw, {**ELIGIBLE, "cost": ref})
        assert reply["ok"] is False and reply["error"]["kind"] == "bad_data"


# ---------------------------------------------------------------------------
# Candidate ff5d6b5e skeptic corrections
# ---------------------------------------------------------------------------


@pytest.fixture()
def worker_of():
    """Fake workers by argv; every one closes after the test."""
    made = []

    def build(*args):
        made.append(JuliaWorker(fake_command(*args)))
        return made[-1]

    yield build
    for worker in made:
        worker.close()


def _open(worker, program="budgeted_select", data=None, params=None, components=None):
    defaults = {"budgeted_select": (SEARCH_DATA, {"budget": 9.0}),
                "assignment": (ASSIGN, {})}[program]
    return worker.open(program, defaults[0] if data is None else data,
                       defaults[1] if params is None else params, DEFAULT_SOLVER, {},
                       components, timeout=30.0, start_timeout=FAKE_START_TIMEOUT)


class TestAPoolHitNeverHandsOverAnIncumbent:
    """One reading: a pool hit keeps the last solution as start material only."""

    def test_another_holders_point_is_no_incumbent_even_in_the_same_state(self, worker_of):
        worker = worker_of()
        first = _open(worker)
        first.start({"select": {"F": 1}})
        assert first.solve({"node_limit": 1})[0] == {"selected": ["F"]}  # a poor point
        first.start(None)
        first.close()
        second = _open(worker)  # the same data, params and components: nothing changed
        assert second.timings.path == "reused"
        solution, record, trajectory = second.search("fix_and_reoptimise", SEARCH_PARAMS,
                                                     limits={"rounds": 20}, seed=7)
        # The old fake took {F} as the incumbent and climbed to the local 11.5 from it.
        assert solution == {"selected": ["A", "B", "D"]} and record.objective == 12.0
        assert not any(row.accepted for row in trajectory)
        assert record.bound == 12.0  # the second handle's own first solve, whole model

    def test_a_pool_hit_clears_fixes_and_starts_and_keeps_the_point_for_start_last(
            self, worker_of):
        worker = worker_of()
        first = _open(worker)
        first.fix({"select": {"A": 1}})
        first.start({"select": {"B": 1}})
        first.solve()
        first.close()
        second = _open(worker)
        assert second.timings.start_source is None
        assert second.fix({}) == 0
        assert second.start("last") == len(SEARCH_DATA["ids"])

    def test_the_same_handle_in_the_same_state_keeps_its_incumbent(self, worker_of):
        model = _open(worker_of())
        model.start({"select": {"F": 1}})
        model.solve({"node_limit": 1})
        model.start(None)
        _, record, trajectory = model.search("fix_and_reoptimise", SEARCH_PARAMS, seed=7)
        assert trajectory[0].accepted  # it improved {F}: {F} stood as the incumbent
        assert record.bound is None and record.gap is None  # the NODE_LIMIT solve's


def _observed(mode):
    """Every caller-visible answer of one session script, path and seconds aside."""
    worker = JuliaWorker(fake_command("--mode", mode))
    try:
        model = _open(worker)
        seen = [model.fix({"select": {"A": 1}}), model.start({"select": {"B": 1}})]
        model.solve()
        model.update(params={"budget": 7.0})
        seen += [model.timings.path, model.timings.start_source, model.fix({})]
        solution, record = model.solve({"node_limit": 2})
        seen += [solution, record.objective, record.termination, model.timings.start_accepted]
        model.update(data={"value": [5.0, 4.0, 4.0, 3.0, 2.5, 9.0]},
                     components={"max_items": True}, params={"budget": 7.0, "max_items": 2})
        seen += [model.timings.start_source, model.start("last"), model.free(),
                 model.search("fix_and_reoptimise", SEARCH_PARAMS, seed=3)[0]]
        return seen
    finally:
        worker.close()


class TestARebuildLosesNothing:
    def test_a_rebuild_is_visible_only_through_path_and_timings(self):
        rebuilt, updated = _observed("no-update"), _observed("ok")
        assert rebuilt[2] == "rebuilt" and updated[2] == "updated"
        assert rebuilt[:2] + rebuilt[3:] == updated[:2] + updated[3:]
        assert updated[3] == "values" and updated[4] == 1  # the start and the fix stayed

    def test_the_fake_carries_the_specs_list_over_a_rebuild(self):
        fake = jump_fake_worker()
        assert set(fake.CARRIED_BY_A_REBUILD) == {
            "fixed", "start", "start_source", "last", "last_record", "last_state",
            "last_handle", "held"}


class TestARebuildIsAlwaysRecorded:
    def test_the_handle_counts_paths_and_keeps_the_last_rebuild(self, worker_of):
        model = _open(worker_of("--mode", "no-update"))
        assert model.path_counts == {"built": 1, "reused": 0, "updated": 0, "rebuilt": 0}
        model.update(params={"budget": 8.0})
        rebuild = model.timings
        assert rebuild.path == "rebuilt" and model.last_rebuild is rebuild
        model.fix({"select": {"A": 1}})
        model.solve()
        assert model.timings.path == "reused"  # the reply no longer shows it ...
        assert model.last_rebuild is rebuild  # ... the handle still does
        assert model.path_counts == {"built": 1, "reused": 2, "updated": 0, "rebuilt": 1}

    def test_an_updating_program_records_no_rebuild(self, worker_of):
        model = _open(worker_of())
        model.update(params={"budget": 8.0})
        assert model.last_rebuild is None and model.path_counts["updated"] == 1


class TestTheSearchRecordIsNoProof:
    def test_a_done_search_names_its_stop_and_the_whole_models_bound(self, searching):
        model, _ = searching()
        model.fix({"select": {"A": 0}})
        _, record, _ = model.search("fix_and_reoptimise", SEARCH_PARAMS, seed=7)
        assert record.termination == "SEARCH_DONE" and record.status == "FEASIBLE_POINT"
        assert record.bound == record.objective == 11.5 and record.gap == 0.0

    def test_a_neighbourhood_round_never_lends_its_bound(self, searching):
        model, _ = searching()  # {F} stands: found under a node limit, no bound
        _, record, trajectory = model.search("fix_and_reoptimise", SEARCH_PARAMS, seed=7)
        assert any(row.bound == row.objective for row in trajectory)  # rounds: "optimal"
        assert record.termination == "SEARCH_DONE"
        assert record.bound is None and record.gap is None

    def test_the_round_and_time_limits_are_named(self, searching):
        model, _ = searching()
        assert model.search("fix_and_reoptimise", SEARCH_PARAMS, limits={"rounds": 1},
                            seed=7)[1].termination == "SEARCH_ROUND_LIMIT"
        model.fix({"select": {"A": 1}})  # a new state: the search solves first
        _, record, trajectory = model.search("fix_and_reoptimise", SEARCH_PARAMS,
                                             limits={"time_limit": 1e-9}, seed=7)
        assert record.termination == "SEARCH_TIME_LIMIT" and trajectory == ()

    def test_no_incumbent_is_named_with_an_empty_trajectory(self, searching):
        model, _ = searching()
        model.fix({"select": {"A": 1, "B": 1, "F": 1}})  # over the budget
        solution, record, trajectory = model.search("fix_and_reoptimise", SEARCH_PARAMS)
        assert solution == {"selected": None} and trajectory == ()
        assert (record.termination, record.status) == ("SEARCH_NO_INCUMBENT", "NO_SOLUTION")
        assert record.objective is None and record.gap is None

    def test_the_fake_gap_is_the_specs(self):
        gap = jump_fake_worker().search_gap
        assert [gap(None, 1.0), gap(2.0, None), gap(2.0, 2.0), gap(0.0, 1.0),
                gap(4.0, 5.0), gap(-4.0, -3.0)] == [None, None, 0.0, None, 0.25, 0.25]

    @pytest.mark.parametrize("patch", [
        {"termination": "OPTIMAL"},
        {"termination": "SEARCH_NO_INCUMBENT"},
        {"status": "NO_SOLUTION"},
        {"bound": None, "gap": 0.0},
        {"objective": None},
    ], ids=repr)
    def test_python_kills_a_search_record_breaking_the_rules(self, searching, patch):
        model, _ = searching("--mode", "patch-search", "--patch", json.dumps(patch))
        with pytest.raises(WorkerRefusal, match=r"budgeted_select.*\(record\)"):
            model.search("fix_and_reoptimise", SEARCH_PARAMS, seed=7)
        assert model._worker.pid is None

    def test_a_solve_record_naming_a_search_stop_is_refused(self, monkeypatch, live):
        node, model = live()
        _rewrite(monkeypatch, "solve", lambda r: r["record"].update(termination="SEARCH_DONE"))
        with pytest.raises(WorkerRefusal, match=r"\(record\).*search stop"):
            model.solve()
        assert _worker(node).pid is None

    def test_a_solve_reporting_update_seconds_breaks_the_timings_rules(self, tmp_path):
        class Backed(JumpBudgetedSelect):
            worker_command = fake_command("--mode", "bad-timings", "--on", "solve",
                                          "--patch", json.dumps({"update_seconds": 0.5}))
            start_timeout = FAKE_START_TIMEOUT

        model = Backed("select", PARAMS).open(_inputs())
        with pytest.raises(WorkerRefusal, match=r"\(timings\).*update_seconds"):
            model.solve()


LONE = "\ud800"


class TestValuesThatPassTheSchemaButNotTheWire:
    def test_a_lone_surrogate_in_data_refuses_by_field_before_any_worker_starts(
            self, spawned):
        node = _logged(_Payload, "unused.jsonl", patch={"ids": ["ALPHA", LONE, "GAMMA"]})
        with pytest.raises(WorkerRefusal, match=r"budgeted_select.*\(payload\).*'ids'.*UTF-8"):
            node.open(_inputs())
        assert spawned == []

    @pytest.mark.parametrize("call", [
        lambda m: m.fix({"select": {LONE: 1}}),
        lambda m: m.start({"select": {LONE: 1}}),
        lambda m: m.free({"select": [LONE]}),
        lambda m: m.search("fix_and_reoptimise", {LONE: 1}),
        lambda m: m.search(LONE, {}),
        lambda m: m.update(params={"budget": 1.0, "note": LONE}),
    ], ids=["fix", "start", "free", "search-params", "search-name", "update-params"])
    def test_a_lone_surrogate_anywhere_refuses_before_sending(self, tmp_path, live, call):
        log = tmp_path / "requests.jsonl"
        node, model = live(log=log)
        with pytest.raises(WorkerRefusal, match=r"\(payload\)"):
            call(model)
        assert [r["op"] for r in _requests(log)] == ["hello", "describe", "open"]
        assert _process(node).poll() is None

    def test_a_lone_surrogate_program_name_refuses_describe(self, worker_of, spawned):
        with pytest.raises(WorkerRefusal, match=r"\(payload\)"):
            worker_of().describe(LONE)
        assert spawned == []

    @pytest.mark.parametrize("line", [f'{{"id": 1, "x": "{chr(92)}ud800"}}',
                                      f'{{"id": 1, "{chr(92)}udc00": 1}}'])
    def test_a_reply_holding_a_lone_surrogate_is_not_protocol_json(self, line):
        with pytest.raises(ValueError, match="surrogate"):
            jump._strict_loads(line)

    def test_the_fake_refuses_a_lone_surrogate_as_bad_request(self, raw):
        reply = raw.send(op="describe", program=LONE)
        assert reply["ok"] is False and reply["error"]["kind"] == "bad_request"

    @pytest.mark.parametrize("big", [(1 << 53) + 1, -(1 << 60), 1 << 1100])
    def test_an_integer_inexact_in_a_float64_is_bad_data_naming_the_field(self, big):
        with pytest.raises(WorkerRefusal, match=r"\(bad_data\).*'scale'.*2\^53"):
            jump.DataContract("p", WIDE_SCHEMA).check({**WIDE, "scale": big})

    def test_two_to_the_53_is_still_exact(self):
        contract = jump.DataContract("p", WIDE_SCHEMA)
        assert contract.check({**WIDE, "scale": 1 << 53})["scale"] == ((), (1 << 53,))

    @needs_pyarrow
    def test_on_the_arrow_path_too_it_is_bad_data_and_nothing_is_written(self, tmp_path):
        log, root = tmp_path / "requests.jsonl", tmp_path / "scratch"
        root.mkdir()
        node = _logged(_Payload, log, arrow_threshold=2, scratch_root=str(root),
                       patch={"value": [1 << 60, 1.0, 1.0]})
        with pytest.raises(WorkerRefusal, match=r"\(bad_data\).*'value'.*2\^53"):
            node.open(_inputs())
        assert [r["op"] for r in _requests(log)] == ["hello", "describe"]
        assert _files(root) == []

    @needs_pyarrow
    def test_an_exact_large_integer_crosses_the_arrow_path(self, tmp_path):
        log, root = tmp_path / "requests.jsonl", tmp_path / "scratch"
        root.mkdir()
        node = _logged(_Payload, log, arrow_threshold=2, scratch_root=str(root),
                       patch={"value": [1 << 53, 1.0, 1.0]})
        with node.open(_inputs()) as model:
            assert model.solve()[0] == {"selected": ["ALPHA"]}

    def test_the_fake_refuses_an_inexact_float64_integer(self, raw):
        reply = _raw_open(raw, {**ELIGIBLE, "cost": [1 << 60, 5.0, 5.0]})
        assert reply["ok"] is False and reply["error"]["kind"] == "bad_data"


class TestDeclaredConstantsAreTheOnesUsed:
    def test_every_reply_kind_is_an_op_or_the_handle_solve(self):
        assert set(jump._REPLY_KEYS) == set(jump.OPS) | {"handle-solve"}

    def test_start_sources_are_the_fakes(self):
        assert jump.START_SOURCES == jump_fake_worker().START_SOURCES

    def test_the_search_stops_are_the_fakes(self):
        fake = jump_fake_worker()
        assert jump.SEARCH_TERMINATIONS == (
            fake.SEARCH_DONE, fake.SEARCH_ROUND_LIMIT, fake.SEARCH_TIME_LIMIT,
            fake.SEARCH_ROUND_NO_POINT, fake.SEARCH_NO_INCUMBENT)
        assert jump.SEED_BOUND == jump.FLOAT64_EXACT == fake.FLOAT64_EXACT

    def test_fix_free_and_start_report_their_update_seconds(self, worker_of):
        model = _open(worker_of())
        model.fix({"select": {"A": 1}})
        assert model.timings.update_seconds > 0
        model.start({"select": {"B": 1}})
        assert model.timings.update_seconds > 0
        model.free()
        assert model.timings.update_seconds > 0
        model.solve()
        assert model.timings.update_seconds == 0


#: A tiny generalized assignment with a unique optimum: P to big (8), Q and R to small.
ASSIGN = {"items": ["P", "Q", "R"], "bins": ["big", "small"],
          "value": [[8.0, 3.0], [5.0, 4.0], [6.0, 5.0]],
          "weight": [[4.0, 2.0], [3.0, 2.0], [3.0, 2.0]],
          "capacity": [5.0, 4.0]}


def _brute_assignment(data, max_per_bin=None, fixed=None):
    """The optimum restated independently: every item to a bin or none."""
    items, bins = data["items"], data["bins"]
    best = None
    for choice in __import__("itertools").product([None, *range(len(bins))], repeat=len(items)):
        load, count, ok = [0.0] * len(bins), [0] * len(bins), True
        for i, b in enumerate(choice):
            if b is not None:
                load[b] += data["weight"][i][b]
                count[b] += 1
        ok = all(load[b] <= data["capacity"][b] + 1e-9 for b in range(len(bins)))
        ok = ok and (max_per_bin is None or max(count) <= max_per_bin)
        for (item, bin_), v in (fixed or {}).items():
            ok = ok and ((choice[items.index(item)] == bins.index(bin_)) == bool(v))
        value = sum(data["value"][i][b] for i, b in enumerate(choice) if b is not None)
        if ok and (best is None or value > best[0] + 1e-12):
            best = (value, [[items[i], bins[b]] for i, b in enumerate(choice) if b is not None])
    return best


class TestTheAssignmentReference:
    def test_describe_publishes_a_two_dimensional_contract(self, worker_of):
        described = worker_of().describe("assignment")
        assert described["schema"]["fields"]["value"] == {"type": "float64",
                                                          "dims": ["item", "bin"]}
        assert described["schema"]["dims"] == {"item": "items", "bin": "bins"}
        assert described["variables"] == {"x": {"index": ["item", "bin"],
                                                "domain": "binary"}}
        assert described["components"] == ["max_per_bin"]

    def test_the_brute_force_is_the_optimum_and_the_record_the_specs(self, worker_of):
        model = _open(worker_of(), "assignment")
        assert model.digest == _canon(jump_fake_worker().Assignment.schema, ASSIGN)
        assert model.timings.pool["bytes"] == 6000  # 1000 per variable
        solution, record = model.solve()
        value, assigned = _brute_assignment(ASSIGN)
        assert solution == {"assigned": assigned} == {"assigned": [["P", "big"],
                                                                   ["Q", "small"],
                                                                   ["R", "small"]]}
        assert record.objective == value == 17.0 and record.termination == "OPTIMAL"
        assert (record.variables, record.constraints) == (6, 5)
        assert [(r["name"], r["rows"]) for r in record.binding] == [("one_bin", 3),
                                                                     ("capacity", 2)]

    def test_a_fix_on_a_two_dimensional_key_changes_the_answer(self, worker_of):
        model = _open(worker_of(), "assignment")
        assert model.fix({"x": {("R", "big"): 1}}) == 1
        value, assigned = _brute_assignment(ASSIGN, fixed={("R", "big"): 1})
        assert model.solve()[0] == {"assigned": assigned} and model.solve_record.objective == value
        with pytest.raises(WorkerRefusal, match=r"\(unknown_key\)"):
            model.fix({"x": {("R", "huge"): 1}})
        with pytest.raises(WorkerRefusal, match=r"\(bad_request\)"):
            model.fix({"x": {"R": 1}})

    def test_numbers_and_the_component_change_in_place(self, worker_of):
        model = _open(worker_of(), "assignment")
        changed = {**ASSIGN, "capacity": [9.0, 4.0]}
        model.update(data={"capacity": [9.0, 4.0]}, params={"max_per_bin": 1},
                     components={"max_per_bin": True})
        assert model.timings.path == "updated"
        value, assigned = _brute_assignment(changed, max_per_bin=1)
        assert model.solve()[0] == {"assigned": assigned}
        assert model.solve_record.constraints == 7
        with pytest.raises(WorkerRefusal, match=r"\(shape_changed\)"):
            model.update(data={"bins": ["big", "tiny"]})
        with pytest.raises(WorkerRefusal, match=r"\(bad_data\)"):
            model.update(data={"value": [[1.0, 2.0], [3.0, 4.0]]})

    def test_params_other_than_max_per_bin_are_bad_request(self, worker_of):
        with pytest.raises(WorkerRefusal, match=r"\(bad_request\)"):
            _open(worker_of(), "assignment", params={"budget": 1.0})

    def test_a_search_runs_over_two_dimensional_keys(self, worker_of):
        model = _open(worker_of(), "assignment")
        solution, record, trajectory = model.search("fix_and_reoptimise",
                                                    {"free": 2, "stall": 2}, seed=7)
        assert solution == {"assigned": _brute_assignment(ASSIGN)[1]}
        assert record.termination == "SEARCH_DONE" and len(trajectory) == 2

    @needs_pyarrow
    def test_a_two_dimensional_field_crosses_as_arrow(self, tmp_path):
        root = tmp_path / "scratch"
        root.mkdir()
        log = tmp_path / "requests.jsonl"
        worker = JuliaWorker(fake_command("--request-log", log),
                             transport={"arrow_threshold": 2, "scratch_root": str(root)})
        try:
            model = _open(worker, "assignment")
            assert _requests(log)[2]["data"]["value"]["arrow"]["shape"] == [3, 2]
            assert model.digest == _canon(jump_fake_worker().Assignment.schema, ASSIGN)
            assert model.solve()[0] == {"assigned": _brute_assignment(ASSIGN)[1]}
        finally:
            worker.close()


def _spec_signature(schema, data):
    """The default signature restated from the spec, unlabelled dimensions included."""
    import hashlib
    import struct

    def name(text):
        raw = text.encode("utf-8")
        return struct.pack("<I", len(raw)) + raw

    def length(dim):
        for spec_name, spec in schema["fields"].items():
            if dim in spec["dims"]:
                value = data[spec_name]
                for _ in range(spec["dims"].index(dim)):
                    value = value[0]
                return len(value)
        return 0

    out = [b"dskit-shape-v1\n"]
    for dim in sorted(schema["dims"], key=lambda k: k.encode("utf-8")):
        label = schema["dims"][dim]
        out += [name(dim), struct.pack("<Q", length(dim))]
        if label is None:
            out.append(b"\x00")
        else:
            out.append(b"\x01")
            for v in data[label]:
                out.append(name(v) if isinstance(v, str) else struct.pack("<q", v))
    return "shape:" + hashlib.sha256(b"".join(out)).hexdigest()


class TestTheFakeIsAReferenceForAnySchema:
    def test_the_digest_of_a_wide_schema_is_the_specs(self):
        assert jump_fake_worker().data_digest(WIDE, WIDE_SCHEMA) == _canon(WIDE_SCHEMA, WIDE)

    def test_the_signature_of_an_unlabelled_dimension_is_the_specs(self):
        fake = jump_fake_worker()
        assert fake.shape_signature(WIDE, WIDE_SCHEMA) == _spec_signature(WIDE_SCHEMA, WIDE)
        wider = {**WIDE, "code": [7, -1, 0, 4], "grid": [[1.0] * 4, [2.0] * 4]}
        assert fake.shape_signature(wider, WIDE_SCHEMA) != fake.shape_signature(
            WIDE, WIDE_SCHEMA)  # an unlabelled length is structure too

    def test_the_signature_of_two_labelled_dimensions_is_the_specs(self):
        schema = jump_fake_worker().Assignment.schema
        assert jump_fake_worker().shape_signature(ASSIGN, schema) == _spec_signature(
            schema, ASSIGN)


# ---------------------------------------------------------------------------
# Cross-worker conformance: the SAME scenarios against the fake and the real Julia worker
# ---------------------------------------------------------------------------

#: The two workers held to the wire spec; the Julia one is skipped by name when absent.
WORKERS = (
    pytest.param("fake", id="fake"),
    pytest.param("julia", id="julia", marks=[
        pytest.mark.skipif(JULIA is None, reason="Julia is not installed (no `julia` on "
                           "PATH): the real worker's conformance needs it"),
        pytest.mark.skipif(not (JULIA_PACKAGE_DIR / "Project.toml").is_file(),
                           reason=f"the Julia package {JULIA_PACKAGE_DIR} is not built: "
                           "the real worker's conformance needs it")]),
)

#: Seconds a real worker may take to start (a first start may precompile the package).
JULIA_START_TIMEOUT = 600.0

#: Seconds each conformance request may take, and the time limit on every solve sent.
CONFORMANCE_TIMEOUT = 120.0
CONFORMANCE_TIME_LIMIT = 30.0

#: Options both workers take: HiGHS solves to a zero gap on one thread (the fake ignores
#: them; they are part of the pool key on both).
CONFORMANCE_OPTIONS = {"mip_rel_gap": 0, "threads": 1, "random_seed": 0}

#: A six-item knapsack with a UNIQUE optimum (A, B, D = 12 under budget 9), so both
#: workers must return the same point.
UNIQUE = {"ids": ["A", "B", "C", "D", "E", "F"], "cost": [4.0, 3.0, 3.0, 2.0, 2.0, 5.0],
          "value": [5.0, 4.0, 3.5, 3.0, 2.5, 6.0]}

#: Tolerance on a number the SOLVER reports (bound, gap, a round's objective): the fake
#: computes them exactly, HiGHS in floating point; the spec fixes the meaning, not the bits.
SOLVER_TOL = 1e-6


def _schema(program):
    return getattr(jump_fake_worker(), {"budgeted_select": "BudgetedSelect",
                                        "assignment": "Assignment"}[program]).schema


@pytest.fixture()
def conformant(request):
    """Workers of the parametrized kind, each closed after the test."""
    made = []

    def build(**kwargs):
        command = (fake_command() if request.param == "fake"
                   else JuliaWorker.default_command())
        made.append(JuliaWorker(command, **kwargs))
        return made[-1]

    build.kind = request.param
    yield build
    for worker in made:
        worker.close()


def _start_timeout(worker):
    return FAKE_START_TIMEOUT if worker.command[0] == sys.executable else JULIA_START_TIMEOUT


def _copen(worker, program="budgeted_select", data=None, params=None, components=None):
    defaults = {"budgeted_select": (ELIGIBLE, {"budget": BUDGET}),
                "assignment": (ASSIGN, {})}[program]
    return worker.open(program, defaults[0] if data is None else data,
                       defaults[1] if params is None else params, DEFAULT_SOLVER,
                       CONFORMANCE_OPTIONS, components, timeout=CONFORMANCE_TIMEOUT,
                       start_timeout=_start_timeout(worker))


def _raw(worker, body, program="budgeted_select"):
    """Send one request as written (no Python-side check) and return the refusal kind."""
    with worker._lock:
        worker._ensure_started(program, _start_timeout(worker))
        with pytest.raises(WorkerRefusal) as refused:
            worker._exchange(program, body, CONFORMANCE_TIMEOUT)
    return refused.value.kind


def _limits():
    return {"time_limit": CONFORMANCE_TIME_LIMIT}


def _optimal(record, objective):
    """The record of an optimal solve whose objective is ``objective`` (exactly: binaries)."""
    assert record.status == "FEASIBLE_POINT" and record.termination == "OPTIMAL"
    assert record.objective == objective
    assert record.bound == pytest.approx(objective, abs=SOLVER_TOL)
    assert record.gap == pytest.approx(0.0, abs=SOLVER_TOL)


@pytest.mark.parametrize("conformant", WORKERS, indirect=True)
class TestCrossWorkerConformance:
    """What the wire spec fixes, asserted identically of the fake and of the Julia worker.

    Every expectation is the spec's (or an optimum restated independently), never one
    worker's output copied as the other's answer. Solver-reported floats (a bound, a gap,
    a round's objective) are compared within :data:`SOLVER_TOL`; ``start_accepted`` is
    whatever the solver says (the fake's brute force says, HiGHS does not: null).
    """

    def test_hello_speaks_protocol_two(self, conformant):
        worker = conformant()
        worker.describe("budgeted_select", start_timeout=_start_timeout(worker))
        assert worker.info["protocol"] == PROTOCOL_VERSION
        assert all(isinstance(v, str) and v for v in worker.info["packages"].values())

    def test_describe_publishes_both_reference_contracts(self, conformant):
        worker = conformant()
        timeout = _start_timeout(worker)
        assert worker.describe("budgeted_select", start_timeout=timeout) == {
            "program": "budgeted_select",
            "schema": {"fields": {"ids": {"type": "string", "dims": ["item"]},
                                  "cost": {"type": "float64", "dims": ["item"]},
                                  "value": {"type": "float64", "dims": ["item"]}},
                       "dims": {"item": "ids"}},
            "variables": {"select": {"index": ["item"], "domain": "binary"}},
            "components": ["max_items"], "update": True}
        assert worker.describe("assignment", start_timeout=timeout) == {
            "program": "assignment",
            "schema": {"fields": {"items": {"type": "string", "dims": ["item"]},
                                  "bins": {"type": "string", "dims": ["bin"]},
                                  "value": {"type": "float64", "dims": ["item", "bin"]},
                                  "weight": {"type": "float64", "dims": ["item", "bin"]},
                                  "capacity": {"type": "float64", "dims": ["bin"]}},
                       "dims": {"item": "items", "bin": "bins"}},
            "variables": {"x": {"index": ["item", "bin"], "domain": "binary"}},
            "components": ["max_per_bin"], "update": True}

    def test_open_reports_the_specs_signature_digest_and_path(self, conformant):
        worker = conformant()
        model = _copen(worker)
        schema = _schema("budgeted_select")
        assert model.handle == "h1" and model.timings.path == "built"
        assert model.digest == _canon(schema, ELIGIBLE)
        assert model.signature == _spec_signature(schema, ELIGIBLE)
        t = model.timings
        assert t.build_seconds > 0 and t.update_seconds == 0 and t.solve_seconds == 0
        assert t.start_source is None and t.start_accepted is None
        assert (t.pool["models"], t.pool["evicted"], t.pool["evictions"]) == (1, 0, 0)
        model.close()
        again = _copen(worker)  # the same data: a pool hit, taken as it was
        assert again.handle == "h2" and again.timings.path == "reused"
        assert again.timings.pool["models"] == 1 and again.digest == model.digest
        again.close()
        changed = {**ELIGIBLE, "value": [1.0, 2.0, 3.0]}
        third = _copen(worker, data=changed)  # a pool hit with new numbers: in place
        assert third.timings.path == "updated" and third.timings.build_seconds == 0
        assert third.digest == _canon(schema, changed)
        assign = _copen(worker, "assignment")
        assert assign.digest == _canon(_schema("assignment"), ASSIGN)
        assert assign.signature == _spec_signature(_schema("assignment"), ASSIGN)

    def test_update_applies_in_place_and_reports_the_digest(self, conformant):
        worker = conformant()
        model = _copen(worker)
        schema = _schema("budgeted_select")
        model.update(data={"value": [20.0, 1.0, 1.0]})
        assert model.timings.path == "updated" and model.timings.build_seconds == 0
        assert model.digest == _canon(schema, {**ELIGIBLE, "value": [20.0, 1.0, 1.0]})
        assert model.solve(_limits())[0] == {"selected": ["ALPHA"]}
        model.update()
        assert model.timings.path == "reused" and model.timings.update_seconds == 0
        model.update(params={"budget": 4.0})
        assert model.timings.path == "updated"
        assert model.solve(_limits())[0] == {"selected": []}
        with pytest.raises(WorkerRefusal, match=r"\(shape_changed\)"):
            model.update(data={"ids": ["ALPHA", "BRAVO", "DELTA"]})
        assert model.update(params={"budget": BUDGET}) == model.digest

    def test_fix_free_and_start(self, conformant):
        worker = conformant()
        model = _copen(worker)
        solution, record = model.solve(_limits())
        assert solution == {"selected": ["BRAVO", "GAMMA"]}
        _optimal(record, 12.0)
        assert model.fix({"select": {"ALPHA": 1}}) == 1
        assert model.timings.path == "reused" and model.timings.solve_seconds == 0
        assert model.solve(_limits())[0] == {"selected": ["ALPHA"]}
        assert model.fix({"select": {"BRAVO": 0}}) == 2
        assert model.free({"select": ["ALPHA"]}) == 1
        solution, record = model.solve(_limits())
        assert solution == {"selected": ["ALPHA"]}  # GAMMA (6) < ALPHA (9) alone
        _optimal(record, 9.0)
        assert model.free() == 0
        assert model.start({"select": {"BRAVO": 1}}) == 1
        assert model.timings.start_source == "values"
        solution, _ = model.solve(_limits())
        assert solution == {"selected": ["BRAVO", "GAMMA"]}  # a start never moves the optimum
        assert model.timings.start_source == "values"
        assert model.timings.start_accepted in (None, True)  # feasible: the solver's word
        assert model.start("last") == 3 and model.timings.start_source == "last"
        assert model.start(None) == 0 and model.timings.start_source is None
        model.solve(_limits())
        assert model.timings.start_accepted is None  # no start: null on every worker

    def test_solutions_and_records_of_both_reference_programs(self, conformant):
        worker = conformant()
        knapsack = _copen(worker, data=UNIQUE, params={"budget": 9.0})
        solution, record = knapsack.solve(_limits())
        assert solution == {"selected": ["A", "B", "D"]}
        _optimal(record, 12.0)
        assert (record.solver, record.variables, record.constraints) == ("HiGHS", 6, 1)
        assert list(record.binding) == [{"name": "budget", "rows": 1, "binding": 1,
                                         "min_slack": 0.0}]
        knapsack.update(params={"budget": 9.0, "max_items": 2},
                        components={"max_items": True})
        solution, record = knapsack.solve(_limits())
        assert solution == {"selected": ["A", "F"]} and record.constraints == 2
        model = _copen(worker, "assignment")
        solution, record = model.solve(_limits())
        value, assigned = _brute_assignment(ASSIGN)
        assert solution == {"assigned": assigned}
        _optimal(record, value)
        assert (record.variables, record.constraints) == (6, 5)
        assert [(r["name"], r["rows"], r["binding"]) for r in record.binding] == [
            ("one_bin", 3, 3), ("capacity", 2, 1)]
        assert [r["min_slack"] for r in record.binding] == [0.0, 0.0]
        model.update(data={"capacity": [9.0, 4.0]}, params={"max_per_bin": 1},
                     components={"max_per_bin": True})
        assert model.timings.path == "updated"
        value, assigned = _brute_assignment({**ASSIGN, "capacity": [9.0, 4.0]},
                                            max_per_bin=1)
        solution, record = model.solve(_limits())
        assert solution == {"assigned": assigned} and record.objective == value
        assert [r["name"] for r in record.binding] == ["one_bin", "capacity", "max_per_bin"]
        assert record.constraints == 7
        assert model.fix({"x": {("R", "small"): 0}}) == 1
        value, assigned = _brute_assignment({**ASSIGN, "capacity": [9.0, 4.0]},
                                            max_per_bin=1, fixed={("R", "small"): 0})
        assert model.solve(_limits())[0] == {"assigned": assigned}

    def test_the_pool_hit_and_eviction_paths(self, conformant):
        worker = conformant(settings={"pool_models": 2, "pool_bytes": 1 << 40})
        first = _copen(worker)
        first.fix({"select": {"GAMMA": 0}})
        first.solve(_limits())
        first.close()
        held = _copen(worker)  # a pool hit: the fix released, the start cleared
        assert held.timings.path == "reused" and held.timings.pool["models"] == 1
        assert held.solve(_limits())[0] == {"selected": ["BRAVO", "GAMMA"]}
        other = _copen(worker, "assignment")  # a new key: built
        assert other.timings.path == "built" and other.timings.pool["models"] == 2
        third = _copen(worker, data={"ids": ["X"], "cost": [1.0], "value": [1.0]})
        t = third.timings
        assert (t.path, t.pool["models"], t.pool["evicted"], t.pool["evictions"]) == (
            "built", 2, 1, 1)
        with pytest.raises(WorkerRefusal, match=r"\(evicted\)"):
            held.solve(_limits())
        assert held.close() is False
        assert other.solve(_limits())[1].objective == 17.0
        assert worker.pid is not None

    def test_every_error_kind_reachable_without_misbehaviour(self, conformant):
        worker = conformant()
        model = _copen(worker)
        pid = worker.pid
        with pytest.raises(WorkerRefusal, match=r"\(unknown_program\)"):
            worker.describe("no_such_program")
        with pytest.raises(WorkerRefusal, match=r"\(unknown_solver\)"):
            worker.open("budgeted_select", ELIGIBLE, {"budget": BUDGET}, "NoSuchSolver", {},
                        timeout=CONFORMANCE_TIMEOUT)
        with pytest.raises(WorkerRefusal, match=r"\(unknown_component\)"):
            _copen(worker, components={"no_such": True})
        with pytest.raises(WorkerRefusal, match=r"\(no_solution\)"):
            model.start("last")
        for values, kind in (({"nope": {"ALPHA": 1}}, "unknown_group"),
                             ({"select": {"DELTA": 1}}, "unknown_key"),
                             ({"select": {"ALPHA": 0.5}}, "bad_value")):
            with pytest.raises(WorkerRefusal, match=rf"\({kind}\)"):
                model.fix(values)
        with pytest.raises(WorkerRefusal, match=r"\(shape_changed\)"):
            model.update(data={"ids": ["A", "B", "C"]})
        with pytest.raises(WorkerRefusal, match=r"\(unknown_search\)"):
            model.search("no_such_search", {})
        with pytest.raises(WorkerRefusal, match=r"\(bad_request\)"):
            model.search("fix_and_reoptimise", {"free": 0, "stall": 1})
        full = {"program": "budgeted_select", "data": ELIGIBLE, "params": {"budget": BUDGET},
                "solver": DEFAULT_SOLVER, "solver_options": {}, "components": {}}
        assert _raw(worker, {"op": "no_such_op"}) == "unknown_op"
        assert _raw(worker, {"op": "open", **full, "extra": 1}) == "bad_request"
        assert _raw(worker, {"op": "open", **full, "data": {**ELIGIBLE, "cost": [1.0]}}
                    ) == "bad_data"
        assert _raw(worker, {"op": "open", **full, "data": {**ELIGIBLE, "cost": [1, 2**53 + 1,
                                                                                3]}}
                    ) == "bad_data"
        assert _raw(worker, {"op": "solve", "handle": "h99", "limits": {}}) == "unknown_handle"
        assert _raw(worker, {"op": "solve", "handle": model.handle,
                             "limits": {"node_limit": 0}}) == "bad_request"
        assert worker.pid == pid  # every refusal kept the worker
        assert model.solve(_limits())[0] == {"selected": ["BRAVO", "GAMMA"]}

    @needs_pyarrow
    def test_an_arrow_transported_array(self, conformant, tmp_path):
        root = tmp_path / "scratch"
        root.mkdir()
        worker = conformant(transport={"arrow_threshold": 2, "scratch_root": str(root)})
        model = _copen(worker)  # every field of three elements crosses as Arrow
        assert model.digest == _canon(_schema("budgeted_select"), ELIGIBLE)
        assert model.solve(_limits())[0] == {"selected": ["BRAVO", "GAMMA"]}
        model.update(data={"value": [20.0, 1.0, 1.0]})
        assert model.timings.path == "updated"
        assert model.solve(_limits())[0] == {"selected": ["ALPHA"]}
        assign = _copen(worker, "assignment")  # a [3, 2] field, row-major
        assert assign.digest == _canon(_schema("assignment"), ASSIGN)
        assert assign.solve(_limits())[0] == {"assigned": _brute_assignment(ASSIGN)[1]}
        assert [p for p in root.rglob("*.arrow")] == []  # each removed once answered
        worker.close()
        assert list(root.iterdir()) == []

    def test_a_seeded_search_trajectory_and_its_record(self, conformant):
        worker = conformant()
        model = _copen(worker, data=UNIQUE, params={"budget": 9.0})
        limits, rounds = {"rounds": 20}, _limits()
        solution, record, trajectory = model.search(
            "fix_and_reoptimise", {"free": 2, "stall": 3}, limits=limits, seed=7,
            round_limits=rounds)
        assert solution == {"selected": ["A", "B", "D"]}
        assert (record.status, record.termination, record.objective) == (
            "FEASIBLE_POINT", "SEARCH_DONE", 12.0)
        assert record.bound == pytest.approx(12.0, abs=SOLVER_TOL)
        assert record.gap == pytest.approx(0.0, abs=SOLVER_TOL)
        assert [(r.round, r.accepted, r.neighbourhood) for r in trajectory] == [
            (1, False, 2), (2, False, 2), (3, False, 2)]
        assert [r.objective for r in trajectory] == pytest.approx([12.0] * 3, abs=SOLVER_TOL)
        assert model.timings.path == "reused" and model.timings.build_seconds == 0
        # the caller's fixes bind every round; the round limit is the stop reason
        assert model.fix({"select": {"A": 0}}) == 1
        solution, record, trajectory = model.search(
            "fix_and_reoptimise", {"free": 10, "stall": 3}, limits={"rounds": 2}, seed=7,
            round_limits=rounds)
        assert solution == {"selected": ["D", "E", "F"]} and record.objective == 11.5
        assert record.termination == "SEARCH_ROUND_LIMIT"
        assert [(r.round, r.accepted, r.neighbourhood) for r in trajectory] == [
            (1, False, 5), (2, False, 5)]
        assert model.fix({"select": {"B": 1, "C": 1, "F": 1}}) == 4  # cost 11 > 9
        solution, record, trajectory = model.search(
            "fix_and_reoptimise", {"free": 2, "stall": 3}, seed=7, round_limits=rounds)
        assert solution == {"selected": None} and trajectory == ()
        assert (record.status, record.termination, record.objective, record.gap) == (
            "NO_SOLUTION", "SEARCH_NO_INCUMBENT", None, None)
        assert model.free() == 0  # the search restored exactly the caller's four fixes
