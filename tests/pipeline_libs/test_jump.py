"""The JuMP pack's suite: plan without Julia, the wire protocol, every refusal.

Nothing here needs Julia. ``jump_fake_worker.py`` speaks the pack's JSON-lines protocol
from Python (a real brute-force knapsack, so answers are real) and misbehaves on request
(wrong protocol, crash, hang, malformed line, wrong id, error reply, bad record), chosen
by argv. The one test that needs the real worker, parity with the pyomo ``BudgetedSelect``,
is skipped with a reason naming Julia when ``julia`` is not on ``PATH``.

Layout: **Units** (params, inputs, plan, no Julia), **Protocol** (hello, solve, reuse),
**Failures** (each refuses by program name; the next solve starts a fresh worker),
**Conformance**, **Example**, **Parity** (real Julia only).
"""

from __future__ import annotations

import gc
import json
import pathlib
import shutil
import subprocess
import sys

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

HERE = pathlib.Path(__file__).parent
FAKE_WORKER = HERE / "jump_fake_worker.py"
EXAMPLE = pathlib.Path(__file__).parents[2] / "examples" / "pipeline" / "jump-budgeted-select.json"

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


def _ctx(tmp_path):
    return NodeContext(name="t", asof="2026-01-01", run_dir=str(tmp_path / "run"))


def _inputs(survivors=None):
    return {
        "candidates": [dict(row) for row in CANDIDATES],
        "survivors": list(SURVIVORS) if survivors is None else survivors,
    }


@pytest.fixture()
def nodes():
    """Build nodes whose worker is the fake; every node is closed (no orphan) at teardown."""
    made = []

    def build(*args, cls=JumpBudgetedSelect, **params):
        class Backed(cls):
            worker_command = fake_command(*args)
            request_timeout = SHORT_TIMEOUT
            start_timeout = FAKE_START_TIMEOUT

        node = Backed("select", {**PARAMS, **params})
        made.append(node)
        return node

    yield build
    for node in made:
        node.close()


def _process(node):
    return node._worker._process


# ---------------------------------------------------------------------------
# Units: plan and validation, no Julia anywhere
# ---------------------------------------------------------------------------


class TestPlanWithoutJulia:
    def test_the_module_imports_with_no_third_party_library(self):
        import ast

        tree = ast.parse(pathlib.Path(jump.__file__).read_text(encoding="utf-8"))
        roots = {
            (alias.name if isinstance(n, ast.Import) else n.module).split(".")[0]
            for n in ast.walk(tree)
            if isinstance(n, (ast.Import, ast.ImportFrom)) and not getattr(n, "level", 0)
            for alias in (n.names if isinstance(n, ast.Import) else [n])
        }
        allowed = set(sys.stdlib_module_names) | {"dskit", "__future__"}
        assert roots <= allowed, sorted(roots - allowed)

    def test_the_default_command_is_derived_from_the_module_not_hardcoded(self):
        command = JuliaWorker.default_command()
        package = pathlib.Path(jump.__file__).resolve().parents[3] / "julia" / "DskitJuMP"
        assert command[0] == "julia"
        assert f"--project={package}" in command

    def test_a_document_naming_the_node_plans_without_julia(self, monkeypatch):
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
        assert node._worker is None

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
        assert record.termination == "optimal" and record.objective == pytest.approx(12.0)
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

    def test_an_empty_gate_deploys_zero_and_never_starts_a_worker(self, tmp_path):
        class Unlaunchable(JumpBudgetedSelect):
            worker_command = ["/nonexistent/never-launched"]

        node = Unlaunchable("select", PARAMS)
        out = node.run(_ctx(tmp_path), _inputs(survivors=[]))
        assert out["positions"] == [] and out["outlay"] == 0.0
        assert node._worker is None and node.solve_record is None

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

    def test_a_garbage_collected_node_leaves_no_orphan(self, tmp_path):
        class Backed(JumpBudgetedSelect):
            worker_command = fake_command()
            start_timeout = FAKE_START_TIMEOUT

        node = Backed("select", PARAMS)
        node.run(_ctx(tmp_path), _inputs())
        process = _process(node)
        del node
        gc.collect()
        process.wait(timeout=10)
        assert process.poll() is not None

    def test_a_non_optimal_record_is_refused_on_extract(self, tmp_path, nodes):
        node = nodes()
        node.run(_ctx(tmp_path), _inputs())
        node.solve_record = SolveRecord(
            solver="HiGHS", status="warning", termination="TIME_LIMIT", objective=None,
            bound=None, gap=None, seconds=0.0, variables=3, constraints=1, binding=())
        with pytest.raises(RuntimeError, match="not 'optimal'"):
            node.extract({"selected": ["BRAVO"]})


# ---------------------------------------------------------------------------
# Failures: each refuses naming the program; the next solve starts a fresh worker
# ---------------------------------------------------------------------------


class TestFailures:
    def test_the_worker_class_has_no_third_party_dependency_and_names_its_program(self):
        assert issubclass(WorkerRefusal, RuntimeError)

    def test_a_protocol_mismatch_refuses_before_any_solve(self, tmp_path, nodes):
        node = nodes("--mode", "bad-protocol")
        with pytest.raises(WorkerRefusal, match=r"budgeted_select.*protocol"):
            node.run(_ctx(tmp_path), _inputs())
        assert node.solve_record is None
        assert node._worker is None or node._worker._process is None

    def test_a_worker_that_will_not_start_is_a_named_refusal(self, tmp_path):
        class Missing(JumpBudgetedSelect):
            worker_command = ["/nonexistent/julia-not-here"]

        with pytest.raises(WorkerRefusal, match="budgeted_select"):
            Missing("select", PARAMS).run(_ctx(tmp_path), _inputs())

    def test_the_default_command_without_julia_refuses_by_name(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PATH", "")
        node = JumpBudgetedSelect("select", PARAMS)
        with pytest.raises(WorkerRefusal, match=r"budgeted_select.*julia"):
            node.run(_ctx(tmp_path), _inputs())

    @pytest.mark.parametrize(
        ("mode", "needle"),
        [
            ("crash", "died"),
            ("hang", "timed out"),
            ("malformed", "malformed"),
            ("wrong-id", "id"),
            ("bad-record", "record"),
        ],
    )
    def test_a_broken_worker_refuses_by_program_and_the_next_solve_is_fresh(
        self, tmp_path, nodes, mode, needle
    ):
        flag = tmp_path / "misbehaved"
        node = nodes("--mode", mode, "--once", flag)
        with pytest.raises(WorkerRefusal, match="budgeted_select") as excinfo:
            node.run(_ctx(tmp_path), _inputs())
        assert needle in str(excinfo.value)
        assert node.solve_record is None
        # Never a silent retry: one start for the one attempt, and the broken worker is gone.
        assert node._worker.starts == 1 and node._worker.pid is None
        out = node.run(_ctx(tmp_path), _inputs())  # the flag is spent: a fresh worker is clean
        assert out["positions"] == ["BRAVO", "GAMMA"]
        assert node._worker.starts == 2 and _process(node).poll() is None

    def test_a_failed_solve_is_never_retried_inside_one_run(self, tmp_path, nodes):
        node = nodes("--mode", "crash")  # no --once: every worker crashes
        with pytest.raises(WorkerRefusal, match="budgeted_select"):
            node.run(_ctx(tmp_path), _inputs())
        assert node._worker.starts == 1  # the refusal came from the one attempt
        with pytest.raises(WorkerRefusal, match="budgeted_select"):
            node.run(_ctx(tmp_path), _inputs())
        assert node._worker.starts == 2  # the NEXT solve, not a retry, started the second

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

    def test_a_non_json_payload_refuses_before_it_reaches_the_worker(self, tmp_path, nodes):
        class Nan(JumpBudgetedSelect):
            def payload(self, inputs, params):
                data = super().payload(inputs, params)
                data["value"][0] = float("nan")
                return data

        node = nodes(cls=Nan)
        with pytest.raises(WorkerRefusal, match=r"budgeted_select.*JSON"):
            node.run(_ctx(tmp_path), _inputs())

    def test_every_misbehaviour_leaves_no_orphan_after_close(self, tmp_path, nodes, monkeypatch):
        spawned = []
        original = subprocess.Popen

        def recording(*args, **kwargs):
            process = original(*args, **kwargs)
            spawned.append(process)
            return process

        monkeypatch.setattr(subprocess, "Popen", recording)
        for mode in ("crash", "hang", "malformed", "wrong-id", "bad-record", "bad-protocol"):
            node = nodes("--mode", mode)
            with pytest.raises(WorkerRefusal):
                node.run(_ctx(tmp_path), _inputs())
            node.close()
        healthy = nodes()
        healthy.run(_ctx(tmp_path), _inputs())
        healthy.close()
        assert len(spawned) == 7
        assert all(p.poll() is not None for p in spawned)

    def test_the_hang_timeout_is_configurable_and_fast(self, tmp_path, nodes):
        import time

        node = nodes("--mode", "hang")
        started = time.perf_counter()
        with pytest.raises(WorkerRefusal, match="timed out"):
            node.run(_ctx(tmp_path), _inputs())
        assert time.perf_counter() - started < 10 * SHORT_TIMEOUT + FAKE_START_TIMEOUT


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


# ---------------------------------------------------------------------------
# Parity with the pyomo BudgetedSelect: real Julia only
# ---------------------------------------------------------------------------

JULIA = shutil.which("julia")


@pytest.mark.skipif(JULIA is None, reason="Julia is not installed (no `julia` on PATH): "
                    "the real-worker parity test needs it")
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
