"""The pyomo pack's suite: units, real-solver integration, conformance, example.

Layout mirrors the pack's obligations (pipeline/CLAUDE.md, Author's Guide):

* **Units** — validators and plumbing, all runnable WITHOUT pyomo: the
  empty-gate short-circuit and the undeclared-outputs refusal are proven
  with ``pyomo`` blocked from ``sys.modules``, not merely assumed.
* **Integration** — the real knapsack solved by the real ``appsi_highs``
  (pyomo + highspy are installed here; the solve path is never mocked).
* **Conformance** — the toolkit suite pointed at the pack's registry,
  capital probe fully populated (budget/outlay/gate_port, runnable).
* **The shipped example** — ``examples/pipeline/pyomo-solve.json`` must
  LOAD, HASH stably, PLAN via the real planner, and RUN end to end.

:class:`OpportunityDeck` is the example's toy upstream (referenced from
the shipped document by import path) — deterministic evidence + a
candidate deck rigged so the gate and the optimizer are both load-bearing:
NOISE has the deck's largest value but no edge evidence (the gate must
drop it), and greedy-by-value among the survivors picks ALPHA (value 9)
while the optimum is BRAVO+GAMMA (value 12).
"""

from __future__ import annotations

import json
import pathlib
import sys

import pytest

from dskit.pipeline.base import ConfigError
from dskit.pipeline.conformance import NodeProbe, conformance_suite
from dskit.pipeline.document import PipelineDocument, load_document
from dskit.pipeline.driver import run_document
from dskit.pipeline.libs.pyomo import (
    DEFAULT_SOLVER,
    NODE_KINDS,
    BudgetedSelect,
    PyomoSolve,
    register,
)
from dskit.pipeline.node import (
    DEFAULT_NODE_KINDS,
    Node,
    NodeContext,
    NodeKindRegistry,
    node_class_errors,
)
from dskit.pipeline.planner import plan

EXAMPLES = pathlib.Path(__file__).parents[2] / "examples" / "pipeline"
EXAMPLE = EXAMPLES / "pyomo-solve.json"

#: The deck: NOISE is the value trap the gate must refuse; among the
#: survivors, ALPHA alone (value 9) is the greedy pick while BRAVO+GAMMA
#: (value 12) is the optimum under budget 10 — so a solve that merely
#: sorts by value fails the assertions below.
CANDIDATES = (
    {"id": "ALPHA", "cost": 6.0, "value": 9.0},
    {"id": "BRAVO", "cost": 5.0, "value": 6.0},
    {"id": "GAMMA", "cost": 5.0, "value": 6.0},
    {"id": "NOISE", "cost": 1.0, "value": 50.0},
)

BUDGET = 10.0
PARAMS = {"budget": BUDGET, "solver": DEFAULT_SOLVER, "solver_options": {}}
SURVIVORS = ["ALPHA", "BRAVO", "GAMMA"]

_N_CLUSTERS = 8


def _deck_scores(seed):
    """Per-instrument cluster evidence for the owned stat_test: three
    instruments with uniformly positive paired improvements (decisive
    edge under the add-one bootstrap), one mean-zero decoy."""
    scores = {}
    for inst in ("ALPHA", "BRAVO", "GAMMA"):
        scores[inst] = {
            f"c{i}": 0.02 + 0.001 * ((seed + i) % 5) for i in range(_N_CLUSTERS)
        }
    scores["NOISE"] = {
        f"c{i}": 0.01 if i % 2 == 0 else -0.01 for i in range(_N_CLUSTERS)
    }
    return scores


class OpportunityDeck(Node):
    """The example's toy source (role ``data``): evidence + candidates.

    Referenced from ``examples/pipeline/pyomo-solve.json`` by import
    path. A fixture, not a pack member — it is deliberately NOT in the
    pack's ``NODE_KINDS`` and owes the conformance bar nothing.
    """

    role = "data"
    outputs = ("scores", "candidates")

    @classmethod
    def validate_params(cls, params):
        problems = []
        unknown = sorted(set(params) - {"seed"})
        if unknown:
            problems.append(f"unknown param(s) {unknown} — allowed: ['seed']")
        seed = params.get("seed", 0)
        if isinstance(seed, bool) or not isinstance(seed, int) or seed < 0:
            problems.append(f"seed must be an int >= 0, got {seed!r}")
        return problems

    def fingerprint(self):
        return {"kind": "opportunity-deck", "seed": self.params.get("seed", 0)}

    def run(self, ctx, inputs):
        return {
            "scores": _deck_scores(self.params.get("seed", 0)),
            "candidates": [dict(row) for row in CANDIDATES],
        }


def _ctx(tmp_path):
    return NodeContext(name="t", asof="2026-01-01", run_dir=str(tmp_path / "run"))


def _node(**params):
    merged = {**PARAMS, **params}
    return BudgetedSelect("select", merged)


def _inputs(survivors=None):
    return {
        "candidates": [dict(row) for row in CANDIDATES],
        "survivors": list(SURVIVORS) if survivors is None else survivors,
    }


# ---------------------------------------------------------------------------
# Units — validators and plumbing, no pyomo required
# ---------------------------------------------------------------------------


class TestParams:
    def test_the_reference_params_validate_clean(self):
        assert BudgetedSelect.validate_params(dict(PARAMS)) == []

    def test_unknown_knobs_are_refused_by_name(self):
        problems = BudgetedSelect.validate_params({**PARAMS, "bankrol": 5.0})
        assert any("bankrol" in p for p in problems)

    def test_budget_is_required_at_plan(self):
        params = {k: v for k, v in PARAMS.items() if k != "budget"}
        problems = BudgetedSelect.validate_params(params)
        assert any("budget" in p for p in problems)

    @pytest.mark.parametrize(
        "budget", [0, -1.0, True, False, float("nan"), float("inf"), "1,000", [], None]
    )
    def test_bad_budgets_are_refused(self, budget):
        problems = BudgetedSelect.validate_params({**PARAMS, "budget": budget})
        assert any("budget" in p for p in problems)

    @pytest.mark.parametrize("solver", [123, "", "no spaces allowed", ["x"], None])
    def test_solver_shape_is_checked_at_plan(self, solver):
        problems = BudgetedSelect.validate_params({**PARAMS, "solver": solver})
        assert any("solver" in p for p in problems)

    def test_a_plausible_solver_name_passes_plan(self):
        # The NAME resolves only at run, against the installed pyomo —
        # plan checks the shape, which is all it honestly can.
        assert BudgetedSelect.validate_params({**PARAMS, "solver": "gurobi"}) == []

    @pytest.mark.parametrize(
        "options",
        [
            "not-a-dict",
            [("a", 1)],
            {"": 1},
            {1: 2},
            {"depth": [1]},
            {"depth": float("nan")},
        ],
    )
    def test_bad_solver_options_are_refused(self, options):
        problems = BudgetedSelect.validate_params({**PARAMS, "solver_options": options})
        assert any("solver_options" in p for p in problems)

    def test_scalar_solver_options_pass(self):
        options = {"time_limit": 30.0, "presolve": "on", "threads": 1, "flag": True}
        params = {**PARAMS, "solver_options": options}
        assert BudgetedSelect.validate_params(params) == []


class TestInputs:
    def test_the_reference_inputs_validate_clean(self):
        assert _node().validate_inputs(_inputs()) == []

    def test_one_shot_candidates_are_refused_by_name_not_walked(self):
        seen = []

        def one_shot():
            for row in CANDIDATES:
                seen.append(row)
                yield row

        problems = _node().validate_inputs(
            {"candidates": one_shot(), "survivors": list(SURVIVORS)}
        )
        assert problems and not seen

    def test_one_shot_survivors_are_refused_by_name_not_walked(self):
        problems = _node().validate_inputs(
            {"candidates": [dict(r) for r in CANDIDATES], "survivors": iter(SURVIVORS)}
        )
        assert any("survivors" in p for p in problems)

    def test_duplicate_ids_are_refused(self):
        rows = [dict(CANDIDATES[0]), dict(CANDIDATES[0])]
        problems = _node().validate_inputs({"candidates": rows, "survivors": []})
        assert any("duplicate" in p for p in problems)

    @pytest.mark.parametrize(
        "row",
        [
            "not-a-mapping",
            {"id": "", "cost": 1.0, "value": 1.0},
            {"id": "A", "cost": -1.0, "value": 1.0},
            {"id": "A", "cost": float("nan"), "value": 1.0},
            {"id": "A", "cost": True, "value": 1.0},
            {"id": "A", "cost": 1.0, "value": float("inf")},
            {"id": "A", "cost": 1.0},
        ],
    )
    def test_broken_candidate_rows_are_refused(self, row):
        problems = _node().validate_inputs({"candidates": [row], "survivors": []})
        assert problems

    def test_non_string_survivors_are_refused(self):
        problems = _node().validate_inputs(
            {"candidates": [dict(r) for r in CANDIDATES], "survivors": [1]}
        )
        assert any("survivors" in p for p in problems)


class TestPlumbingWithoutPyomo:
    """The paths that must never pay the pyomo import."""

    @pytest.fixture()
    def no_pyomo(self, monkeypatch):
        # Blocking the root package makes `from pyomo.environ import ...`
        # raise ImportError immediately, whether or not pyomo was ever
        # imported before this test.
        monkeypatch.setitem(sys.modules, "pyomo", None)

    def test_empty_gate_deploys_zero_without_waking_the_solver(
        self, tmp_path, no_pyomo
    ):
        out = _node().run(_ctx(tmp_path), _inputs(survivors=[]))
        assert out == {
            "positions": [],
            "outlay": 0.0,
            "metrics": {
                "objective": 0.0,
                "n_selected": 0,
                "n_eligible": 0,
                "n_candidates": len(CANDIDATES),
            },
        }

    def test_undeclared_outputs_are_refused_before_any_solve(self, tmp_path, no_pyomo):
        class NoContract(PyomoSolve):
            # outputs stays None — the rule this subclass breaks.
            def build_model(self, inputs, params):  # pragma: no cover
                raise AssertionError("must never be reached")

            def extract(self, model, results):  # pragma: no cover
                raise AssertionError("must never be reached")

        node = NoContract("bare", {})
        with pytest.raises(TypeError, match="outputs"):
            node.run(_ctx(tmp_path), {})


class TestDeterministicSolveDefaults:
    """S1 #4: under the default ``appsi_highs``, BudgetedSelect pins
    ``{mip_rel_gap: 0, threads: 1, random_seed: 0}`` — a knapsack
    routinely has TIES, and gap tolerance, thread races and RNG jitter
    can each flip WHICH optimal vertex comes back, so an unpinned solve
    hands a gate-consumed selection that flaps machine to machine. The
    document's own options override per key; a non-default solver gets
    nothing injected (its option names differ). Pure plumbing — no
    pyomo import."""

    PINNED = {"mip_rel_gap": 0, "threads": 1, "random_seed": 0}

    def test_defaults_present_when_unset(self, no_pyomo):
        assert BudgetedSelect("s", {"budget": 10.0})._solver_options() == self.PINNED
        # the shipped-params shape (explicit default solver, empty options)
        assert _node()._solver_options() == self.PINNED

    def test_user_overrides_win_per_key(self, no_pyomo):
        options = _node(
            solver_options={"threads": 4, "time_limit": 30.0}
        )._solver_options()
        assert options == {
            "mip_rel_gap": 0,
            "threads": 4,  # the document's word beats the pin
            "random_seed": 0,
            "time_limit": 30.0,
        }

    def test_no_injection_for_a_non_default_solver(self, no_pyomo):
        # cbc/gurobi/... spell these options differently — injecting HiGHS
        # names would be rejected or, worse, silently ignored.
        assert _node(solver="cbc")._solver_options() == {}
        assert _node(solver="cbc", solver_options={"sec": 5})._solver_options() == {
            "sec": 5
        }

    def test_the_generic_base_injects_nothing(self, no_pyomo):
        # The pin is BudgetedSelect's, not the doorway's: a plain subclass
        # keeps the document's options verbatim, whatever the solver.
        node = ToyAssignment("assign", {"solver_options": {"anything": 1}})
        assert node._solver_options() == {"anything": 1}
        assert ToyAssignment("assign", {})._solver_options() == {}

    @pytest.fixture()
    def no_pyomo(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "pyomo", None)


class TestPackShape:
    def test_the_abstract_base_can_never_register(self):
        problems = node_class_errors(PyomoSolve, "kind 'pyomo-solve'")
        assert any("abstract" in p for p in problems)
        assert any("build_model" in p and "extract" in p for p in problems)
        with pytest.raises(ValueError, match="abstract"):
            NodeKindRegistry().register("pyomo-solve", PyomoSolve)

    def test_node_kinds_carries_only_the_concrete_subclass(self):
        assert NODE_KINDS == (("pyomo-budgeted-select", BudgetedSelect),)

    def test_importing_the_pack_registers_nothing(self):
        # Pack convention: registration is the USER's explicit call, never
        # an import side effect — and no test in this file ever registers
        # into the default registry.
        assert "pyomo-budgeted-select" not in DEFAULT_NODE_KINDS

    def test_register_is_explicit_and_idempotent(self):
        private = NodeKindRegistry()
        register(private)
        assert "pyomo-budgeted-select" in private
        register(private)  # second call skips, never shadows
        cls, owned = private.get("pyomo-budgeted-select")
        assert cls is BudgetedSelect and owned is False

    def test_a_non_capital_subclass_escapes_the_capital_gate(self):
        # The role is a class attribute: a subclass doing non-capital
        # optimization declares its own, and the planner's
        # capital⇐stat_test rule applies only where role stays capital.
        private = NodeKindRegistry()
        private.register("toy-assign", ToyAssignment)
        doc = PipelineDocument.from_obj(
            {"name": "roles", "pipeline": {"assign": {"uses": "toy-assign"}}}
        )
        resolved = plan(doc, registry=private)  # un-gated, and legal
        assert resolved.role_of("assign") == "transform"


class ToyAssignment(PyomoSolve):
    """A concrete non-capital subclass — exists to prove the role
    override in :class:`TestPackShape` (plan-time only; never run)."""

    role = "transform"
    outputs = ("assignment",)

    def build_model(self, inputs, params):  # pragma: no cover - plan-only
        raise NotImplementedError

    def extract(self, model, results):  # pragma: no cover - plan-only
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Integration — the real solver (pyomo + highspy installed; never mocked)
# ---------------------------------------------------------------------------


class TestRealSolve:
    def test_the_knapsack_is_solved_optimally_not_greedily(self, tmp_path):
        out = _node().run(_ctx(tmp_path), _inputs())
        assert out["positions"] == ["BRAVO", "GAMMA"]  # greedy takes ALPHA
        assert out["outlay"] == pytest.approx(10.0)
        assert out["metrics"]["objective"] == pytest.approx(12.0)
        assert out["metrics"] == {
            "objective": pytest.approx(12.0),
            "n_selected": 2,
            "n_eligible": 3,
            "n_candidates": 4,
        }

    def test_only_surviving_ids_enter_the_model(self, tmp_path):
        # NOISE carries the deck's largest value and is NOT a survivor —
        # a solve that sees it would always take it.
        out = _node().run(_ctx(tmp_path), _inputs())
        assert "NOISE" not in out["positions"]
        assert out["metrics"]["n_eligible"] == 3

    @pytest.mark.parametrize(
        ("budget", "expected", "outlay"),
        [
            (5.9, ["BRAVO"], 5.0),  # only one 5-cost pick fits
            (6.0, ["ALPHA"], 6.0),  # value 9 beats a single value-6 pick
            (16.0, ["ALPHA", "BRAVO", "GAMMA"], 16.0),  # room for all
        ],
    )
    def test_the_budget_binds(self, tmp_path, budget, expected, outlay):
        out = _node(budget=budget).run(_ctx(tmp_path), _inputs())
        assert out["positions"] == expected
        assert out["outlay"] == pytest.approx(outlay)
        assert out["outlay"] <= budget

    def test_solver_options_pass_through(self, tmp_path):
        node = _node(solver_options={"time_limit": 30.0})
        out = node.run(_ctx(tmp_path), _inputs())
        assert out["positions"] == ["BRAVO", "GAMMA"]

    def test_the_determinism_pins_reach_the_real_solver(self, tmp_path):
        # S1 #4 end to end: the injected pins must be option names the
        # real appsi_highs accepts — a rejected name would raise here —
        # and the pinned solve still lands on the true optimum.
        out = BudgetedSelect("select", {"budget": BUDGET}).run(
            _ctx(tmp_path), _inputs()
        )
        assert out["positions"] == ["BRAVO", "GAMMA"]
        assert out["metrics"]["objective"] == pytest.approx(12.0)

    def test_an_unknown_solver_is_refused_by_name_at_run(self, tmp_path):
        node = _node(solver="definitely_not_a_solver")
        with pytest.raises(ValueError, match="definitely_not_a_solver"):
            node.run(_ctx(tmp_path), _inputs())

    def test_an_unavailable_solver_is_refused_by_name_at_run(self, tmp_path):
        from pyomo.environ import SolverFactory

        if SolverFactory("cbc").available(exception_flag=False):
            pytest.skip(
                "cbc is installed here; the unavailable branch needs "
                "a registered solver with no backend"
            )
        node = _node(solver="cbc")
        with pytest.raises(ValueError, match="not available"):
            node.run(_ctx(tmp_path), _inputs())

    def test_a_non_dict_extract_is_refused(self, tmp_path):
        class ListExtract(BudgetedSelect):
            def extract(self, model, results):
                return ["not", "a", "dict"]

        node = ListExtract("select", dict(PARAMS))
        with pytest.raises(TypeError, match="dict"):
            node.run(_ctx(tmp_path), _inputs())

    def test_the_budget_assertion_catches_an_unbound_model(self, tmp_path):
        # The F-220 #1 shape: a model whose budget constraint silently
        # stopped binding. The post-solve assertion must refuse by name.
        class UnboundedSelect(BudgetedSelect):
            def build_model(self, inputs, params):
                model = super().build_model(inputs, params)
                model.budget.deactivate()
                return model

        node = UnboundedSelect("select", dict(PARAMS))  # budget 10 < cost 16
        with pytest.raises(AssertionError, match="budget violated"):
            node.run(_ctx(tmp_path), _inputs())

    def test_an_infeasible_program_fails_loudly(self, tmp_path):
        # appsi refuses to load a solution for an infeasible program and
        # raises from solve() itself — loud is the contract; what must
        # never happen is a quiet return dressed as a selection.
        class Infeasible(BudgetedSelect):
            def build_model(self, inputs, params):
                from pyomo.environ import Constraint

                model = super().build_model(inputs, params)
                ids = model._select["ids"]
                model.impossible = Constraint(
                    expr=sum(model.x[i] for i in ids) >= len(ids) + 1
                )
                return model

        node = Infeasible("select", dict(PARAMS))
        with pytest.raises(RuntimeError):
            node.run(_ctx(tmp_path), _inputs())

    def test_extract_refuses_a_non_optimal_termination(self):
        # The guard for solvers that RETURN a non-optimal status instead
        # of raising (time limits, gap limits): extract must refuse by
        # name before reading a single variable.
        from types import SimpleNamespace

        results = SimpleNamespace(
            solver=SimpleNamespace(termination_condition="maxTimeLimit")
        )
        with pytest.raises(RuntimeError, match="not 'optimal'"):
            _node().extract(None, results)


# ---------------------------------------------------------------------------
# Conformance — the toolkit bar, capital probe fully populated
# ---------------------------------------------------------------------------


def probes(tmp_path):
    return {
        "pyomo-budgeted-select": NodeProbe(
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


TestPyomoConformance = conformance_suite(
    registry=NODE_KINDS,
    module="dskit.pipeline.libs.pyomo",
    probes=probes,
    expected_roles={"pyomo-budgeted-select": "capital"},
    name="TestPyomoConformance",
)


# ---------------------------------------------------------------------------
# The shipped example — LOAD, HASH, PLAN, RUN
# ---------------------------------------------------------------------------

EXPECTED_ORDER = ("evidence", "edge_test", "select")


class TestShippedExample:
    def test_it_loads_and_hashes_stably(self):
        doc = load_document(str(EXAMPLE))
        assert doc.name == "pyomo-select-demo"
        assert tuple(doc.pipeline) == EXPECTED_ORDER
        assert doc.hash == load_document(str(EXAMPLE)).hash

    def test_it_plans_via_the_real_planner(self):
        resolved = plan(load_document(str(EXAMPLE)))
        assert tuple(resolved.order) == EXPECTED_ORDER
        assert resolved.role_of("evidence") == "data"
        assert resolved.role_of("edge_test") == "stat_test"
        assert resolved.role_of("select") == "capital"
        # Doctrine: the gate is the toolkit-OWNED kind; the pack is not.
        assert resolved.resolved["edge_test"].owned is True
        assert resolved.resolved["select"].owned is False

    def test_capital_is_gated_by_the_owned_stat_test(self):
        doc = load_document(str(EXAMPLE))
        assert doc.pipeline["select"].inputs["survivors"] == "$edge_test.survivors"

    def test_removing_the_survivors_wire_refuses_to_plan(self):
        obj = json.loads(EXAMPLE.read_text(encoding="utf-8"))
        del obj["pipeline"]["select"]["inputs"]["survivors"]
        with pytest.raises(ConfigError, match="un-gated capital"):
            plan(PipelineDocument.from_obj(obj))

    def test_it_runs_end_to_end(self, tmp_path):
        obj = json.loads(EXAMPLE.read_text(encoding="utf-8"))
        obj["outputs"]["run_root"] = str(tmp_path / "runs")
        result = run_document(PipelineDocument.from_obj(obj), asof="2026-01-01")

        assert result.error == ""
        assert result.state == "ran"
        assert result.exit_code == 0
        assert set(result.node_states) == set(EXPECTED_ORDER)
        assert set(result.node_states.values()) == {"ok"}

        edge = result.outputs["edge_test"]
        assert edge["verdict"] == "GO"
        assert edge["survivors"] == ["ALPHA", "BRAVO", "GAMMA"]
        assert edge["pvalues"]["NOISE"] > 0.05  # the decoy never survives

        select = result.outputs["select"]
        assert select["positions"] == ["BRAVO", "GAMMA"]
        assert "NOISE" not in select["positions"]
        assert select["outlay"] == pytest.approx(10.0)
        assert select["outlay"] <= 10.0 * (1 + 1e-9)
