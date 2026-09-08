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


# ---------------------------------------------------------------------------
# ScenarioUtilitySolve (ADR-0111) — a two-name concrete subclass for tests
# ---------------------------------------------------------------------------

from dskit.pipeline.libs.pyomo import ScenarioUtilitySolve  # noqa: E402

SU_PARAMS = {
    "risk_aversion_gamma": 2.0,
    "n_tangents": 12,
    "n_scenarios_max": 64,
    "cvar_alpha": 0.9,
    "cvar_limit": None,
    "cardinality": 2,
    "min_ticket": 50.0,
}


class TwoNameSolve(ScenarioUtilitySolve):
    """A minimal concrete subclass: two names, deterministic scenario returns,
    no domain constraint of its own. Params carry the whole account and
    instrument shape as a JSON-legal fixture so tests can vary it cheaply."""

    outputs = ("target", "trades", "cash_after", "metrics")
    _PARAMS = ScenarioUtilitySolve._PARAMS

    def instruments(self, inputs):
        return inputs["names"], inputs["rows"], inputs["account"]

    def payoffs(self, inputs):
        return inputs["weights"], inputs["r"]

    def domain_constraints(self, model, inputs, params):
        pass


def _su_fixture(**overrides):
    names = ["AAA", "BBB"]
    rows = {
        "AAA": {"price": 100.0, "held": 0, "x_max": 6000.0, "cost_buy": 0.05, "cost_sell": 0.05},
        "BBB": {"price": 50.0, "held": 0, "x_max": 6000.0, "cost_buy": 0.02, "cost_sell": 0.02},
    }
    weights = [0.5, 0.5]
    r = {"AAA": [0.05, -0.03], "BBB": [0.01, 0.00]}
    account = {
        "cash": 10000.0,
        "buying_power": 10000.0,
        "wealth_lo": 5000.0,
        "wealth_hi": 15000.0,
        "sale_credit": 1.0,
        "cash_reserve": 0.0,
        "gross_limit": 9000.0,
    }
    fixture = {"names": names, "rows": rows, "weights": weights, "r": r, "account": account}
    fixture.update(overrides)
    return fixture


def _su_node(**params):
    return TwoNameSolve("size", {**SU_PARAMS, **params})


class TestScenarioUtilityParams:
    def test_the_reference_params_validate_clean(self):
        assert TwoNameSolve.validate_params(SU_PARAMS) == []

    def test_unknown_knobs_are_refused_by_name(self):
        problems = TwoNameSolve.validate_params({**SU_PARAMS, "bogus": 1})
        assert any("bogus" in p for p in problems)

    @pytest.mark.parametrize(
        "name", ["risk_aversion_gamma", "cvar_alpha", "cvar_limit", "cardinality", "min_ticket"]
    )
    def test_owner_only_knobs_have_no_default(self, name):
        params = {k: v for k, v in SU_PARAMS.items() if k != name}
        problems = TwoNameSolve.validate_params(params)
        assert any(name in p and "required" in p for p in problems)

    def test_gamma_below_one_is_refused(self):
        problems = TwoNameSolve.validate_params({**SU_PARAMS, "risk_aversion_gamma": 0.5})
        assert any("risk_aversion_gamma" in p for p in problems)

    def test_n_scenarios_max_over_the_hard_ceiling_is_refused(self):
        problems = TwoNameSolve.validate_params({**SU_PARAMS, "n_scenarios_max": 300})
        assert any("n_scenarios_max" in p for p in problems)

    def test_cvar_alpha_out_of_range_is_refused(self):
        problems = TwoNameSolve.validate_params({**SU_PARAMS, "cvar_alpha": 1.0})
        assert any("cvar_alpha" in p for p in problems)


class TestScenarioUtilityPlumbingWithoutPyomo:
    def test_the_abstract_base_can_never_register(self):
        assert node_class_errors("scenario-utility-solve", ScenarioUtilitySolve)

    def test_empty_gate_deploys_zero_without_waking_the_solver(self, tmp_path, monkeypatch):
        monkeypatch.setitem(sys.modules, "pyomo", None)
        node = _su_node()
        out = node.run(_ctx(tmp_path), _su_fixture(names=[], rows={}, account={"cash": 42.0}))
        assert out == {
            "target": {},
            "trades": {},
            "cash_after": 42.0,
            "metrics": {
                "objective": 0.0,
                "expected_utility": 0.0,
                "n_held": 0,
                "n_traded": 0,
                "gross_exposure": 0.0,
                "cash_after": 42.0,
                "cvar": 0.0,
                "cvar_eta": 0.0,
                "wealth_min": 42.0,
                "wealth_max": 42.0,
            },
        }


class TestScenarioUtilityRealSolve:
    def test_it_solves_within_declared_caps(self, tmp_path):
        node = _su_node()
        out = node.run(_ctx(tmp_path), _su_fixture())
        assert len(out["target"]) <= SU_PARAMS["cardinality"]
        assert out["metrics"]["gross_exposure"] <= 9000.0 + 1e-6
        for name in out["target"]:
            notional = out["target"][name] * (100.0 if name == "AAA" else 50.0)
            assert notional >= SU_PARAMS["min_ticket"] - 1e-6

    def test_exit_cost_per_share_is_load_bearing(self, tmp_path):
        # A round-3 skeptic review proved by mutation that this suite never
        # set exit_cost_per_share, so a regression in the wealth formula's
        # use of it (dskit/pipeline/libs/pyomo.py's _wealth_rule AND the
        # extract() recompute both subtract it — nothing pins them to
        # agree) would pass every existing test silently. Prove it changes
        # the reported numbers at the BASE level, not just in the child.
        fixture_free = _su_fixture()
        fixture_costly = _su_fixture()
        for row in fixture_costly["rows"].values():
            row["exit_cost_per_share"] = 5.0
        out_free = _su_node(cvar_limit=None).run(_ctx(tmp_path), fixture_free)
        out_costly = _su_node(cvar_limit=None).run(_ctx(tmp_path), fixture_costly)
        assert out_free["target"], "fixture must actually hold something for this to test anything"
        assert out_costly["metrics"]["wealth_max"] < out_free["metrics"]["wealth_max"]
        assert out_costly["metrics"]["expected_utility"] < out_free["metrics"]["expected_utility"]

    def test_held_inventory_alone_still_solves(self, tmp_path):
        fixture = _su_fixture()
        fixture["rows"]["AAA"]["held"] = 10
        fixture["rows"]["AAA"]["x_max"] = 0.0  # forced exit, no eligible new candidates
        fixture["rows"]["BBB"]["x_max"] = 0.0
        node = _su_node()
        out = node.run(_ctx(tmp_path), fixture)
        assert out["target"] == {}
        assert out["trades"]["AAA"] == {"buy": 0, "sell": 10}

    def test_negative_covariance_survives_via_cvar_not_a_return_filter(self, tmp_path):
        # BBB has zero/near-zero mean return but is anti-correlated with AAA's
        # loss scenario — a per-name expected-return filter would drop it;
        # the doorway must still be ABLE to hold it (never filters by name).
        fixture = _su_fixture()
        fixture["r"] = {"AAA": [0.06, -0.06], "BBB": [-0.02, 0.03]}
        node = _su_node(cvar_limit=200.0)
        out = node.run(_ctx(tmp_path), fixture)
        assert out["metrics"]["cvar"] <= 200.0 + 1e-6

    def test_unchanged_inventory_incurs_zero_cost(self, tmp_path):
        fixture = _su_fixture()
        fixture["rows"]["AAA"]["held"] = 0
        fixture["rows"]["AAA"]["x_max"] = 0.0
        fixture["rows"]["BBB"]["x_max"] = 0.0
        node = _su_node()
        out = node.run(_ctx(tmp_path), fixture)
        assert out["trades"] == {}
        assert out["cash_after"] == pytest.approx(fixture["account"]["cash"])

    def test_gross_exposure_never_exceeds_the_declared_limit(self, tmp_path):
        fixture = _su_fixture()
        fixture["account"]["gross_limit"] = 3000.0
        node = _su_node()
        out = node.run(_ctx(tmp_path), fixture)
        assert out["metrics"]["gross_exposure"] <= 3000.0 + 1e-6

    def test_cardinality_never_exceeds_the_declared_cap(self, tmp_path):
        fixture = _su_fixture()
        node = _su_node(cardinality=1)
        out = node.run(_ctx(tmp_path), fixture)
        assert len(out["target"]) <= 1

    def test_identical_inputs_give_identical_shares_twice(self, tmp_path):
        fixture = _su_fixture()
        node_a, node_b = _su_node(), _su_node()
        out_a = node_a.run(_ctx(tmp_path), fixture)
        out_b = node_b.run(_ctx(tmp_path), fixture)
        assert out_a["target"] == out_b["target"]
        assert out_a["trades"] == out_b["trades"]

    def test_a_sub_min_ticket_legacy_position_can_be_left_untouched(self, tmp_path):
        # Regression for a round-4 skeptic-review BLOCKER: a held position
        # smaller than min_ticket used to force elig_lo (x_i >= min_ticket
        # whenever held-at-all) to demand EITHER a full exit or a top-up —
        # and with thin buying power and a subclass's own no-trade band
        # (which floors exit size too), NEITHER could be satisfied, so the
        # solver's own doorway made the WHOLE joint solve infeasible over a
        # position nobody asked to touch. min_ticket must bind only a tick
        # that actually buys.
        fixture = _su_fixture()
        fixture["rows"]["AAA"]["held"] = 1  # $100 notional, far under min_ticket=$50
        fixture["account"]["cash"] = 0.0
        fixture["account"]["buying_power"] = 0.0  # cannot top up OR buy anything else
        fixture["account"]["wealth_lo"] = 1.0  # must bracket the tiny achievable wealth —
        fixture["account"]["wealth_hi"] = 300.0  # a fixture-consistency detail, not part of the fix
        node = _su_node(min_ticket=5000.0)  # far above AAA's $100 legacy notional
        out = node.run(_ctx(tmp_path), fixture)
        assert out["target"].get("AAA") == 1  # left exactly as held, untouched
        assert "AAA" not in out["trades"]

    def test_buying_and_selling_the_same_name_in_one_tick_is_impossible(self, tmp_path):
        # Regression for a round-4 skeptic-review MAJOR: without a
        # direction binary, the solver could "wash trade" (buy X, sell X)
        # to satisfy a subclass's no-trade-band floor on GROSS trade size
        # while the NET position barely moved — defeating the very
        # guarantee the band exists to provide. Proven generically here at
        # the base level: min(buy, sell) must be exactly 0 for every name,
        # under an objective explicitly REWARDING large gross churn (which
        # would otherwise incentivize exactly this).
        class RewardChurn(TwoNameSolve):
            def domain_constraints(self, model, inputs, params):
                model.objective.expr += 1e-6 * sum(model.b[i] + model.s[i] for i in model._scn["names"])

        fixture = _su_fixture()
        fixture["rows"]["AAA"]["held"] = 20
        node = RewardChurn("size", SU_PARAMS)
        out = node.run(_ctx(tmp_path), fixture)
        for name, trade in out["trades"].items():
            assert trade["buy"] == 0 or trade["sell"] == 0, (name, trade)

    def test_a_domain_constraint_row_is_wired_in(self, tmp_path):
        class ForbidAAA(TwoNameSolve):
            def domain_constraints(self, model, inputs, params):
                from pyomo.environ import Constraint

                model.no_aaa = Constraint(expr=model.x["AAA"] <= 0)

        node = ForbidAAA("size", SU_PARAMS)
        out = node.run(_ctx(tmp_path), _su_fixture())
        assert "AAA" not in out["target"]

    def test_an_infeasible_program_fails_loudly(self, tmp_path):
        fixture = _su_fixture()
        # cash_reserve above cash with nothing held to sell: infeasible.
        # appsi_highs raises directly on "no feasible solution" rather than
        # handing back a gracefully-typed termination condition — still a
        # loud refusal, just not routed through extract()'s own check.
        fixture["account"]["cash_reserve"] = 999999.0
        node = _su_node()
        with pytest.raises(RuntimeError, match="[Ff]easible solution"):
            node.run(_ctx(tmp_path), fixture)

    def test_mismatched_payoff_names_are_refused(self, tmp_path):
        fixture = _su_fixture()
        fixture["r"] = {"AAA": [0.05, -0.03], "ZZZ": [0.01, 0.0]}
        node = _su_node()
        with pytest.raises(ValueError, match="do not match"):
            node.run(_ctx(tmp_path), fixture)

    def test_weights_not_summing_to_one_are_refused(self, tmp_path):
        fixture = _su_fixture()
        fixture["weights"] = [0.5, 0.4]
        node = _su_node()
        with pytest.raises(ValueError, match="sum to 1"):
            node.run(_ctx(tmp_path), fixture)

    def test_too_many_scenarios_is_refused_at_run(self, tmp_path):
        fixture = _su_fixture()
        fixture["weights"] = [1.0 / 3] * 3
        fixture["r"] = {"AAA": [0.01, 0.02, -0.01], "BBB": [0.0, 0.01, -0.01]}
        node = _su_node(n_scenarios_max=2)
        with pytest.raises(ValueError, match="n_scenarios_max"):
            node.run(_ctx(tmp_path), fixture)

    def test_non_finite_scenario_returns_are_refused(self, tmp_path):
        # Regression for a round-8 skeptic-review finding: payoffs()'s
        # weights were checked for finiteness but the return matrix r was
        # only shape-checked — NaN/Inf in r reached the real solver and
        # produced a raw, unhelpful solver-plumbing failure instead of a
        # named refusal.
        fixture = _su_fixture()
        fixture["r"]["AAA"] = [float("nan"), -0.03]
        node = _su_node()
        with pytest.raises(ValueError, match="finite"):
            node.run(_ctx(tmp_path), fixture)

    def test_a_nan_account_cash_reserve_is_refused(self, tmp_path):
        # Regression for a round-8 skeptic-review finding: the base
        # validated 5 of 7 documented account keys (cash/buying_power/
        # wealth_lo/wealth_hi/sale_credit) but not cash_reserve or
        # gross_limit — a bad value in either reached raw pyomo/solver
        # internals for any FUTURE subclass that doesn't happen to
        # duplicate the check itself (as EquityKellyMIO now does).
        fixture = _su_fixture()
        fixture["account"]["cash_reserve"] = float("nan")
        node = _su_node()
        with pytest.raises(ValueError, match="cash_reserve"):
            node.run(_ctx(tmp_path), fixture)

    def test_a_non_finite_account_gross_limit_is_refused(self, tmp_path):
        fixture = _su_fixture()
        fixture["account"]["gross_limit"] = float("inf")
        node = _su_node()
        with pytest.raises(ValueError, match="gross_limit"):
            node.run(_ctx(tmp_path), fixture)

    def test_zero_net_worth_is_refused_by_name_not_corrupted_into_nan(self, tmp_path):
        # Regression for a round-9 skeptic-review finding: w0_mark (cash +
        # mark value of held positions) feeds tangent_utility as the
        # reference wealth, whose own docstring requires w0 > 0. Nothing
        # enforced that precondition, so a completely ordinary account
        # state — cash=0, nothing held (e.g. an unfunded account, or one
        # funded entirely through buying_power/margin rather than cash) —
        # divided by zero inside tangent_utility, corrupted the model with
        # NaN tangent coefficients, and crashed with an opaque solver
        # "infeasible" internals message instead of a named refusal.
        fixture = _su_fixture()
        fixture["account"]["cash"] = 0.0
        fixture["rows"]["AAA"]["held"] = 0
        fixture["rows"]["BBB"]["held"] = 0
        node = _su_node()
        with pytest.raises(ValueError, match="net worth"):
            node.run(_ctx(tmp_path), fixture)

    def test_negative_net_worth_is_refused_by_name(self, tmp_path):
        fixture = _su_fixture()
        fixture["account"]["cash"] = -50.0  # a margin debit
        fixture["rows"]["AAA"]["held"] = 0
        fixture["rows"]["BBB"]["held"] = 0
        node = _su_node()
        with pytest.raises(ValueError, match="net worth"):
            node.run(_ctx(tmp_path), fixture)

    def test_a_zero_wealth_lo_is_refused_by_name_not_corrupted_into_inf(self, tmp_path):
        # Regression for a round-10 skeptic-review finding: wealth_lo
        # feeds tangent_utility's own wealth axis (the tangent knots run
        # from wealth_lo to wealth_hi), which is undefined at wealth <= 0
        # exactly like w0_mark (round 9). Nothing enforced wealth_lo > 0
        # specifically, so a caller computing its own envelope without
        # EquityKellyMIO's floor (max(1.0, ...)) could feed 0 or negative
        # and corrupt the tangent rows with inf/nan coefficients, reaching
        # an opaque solver "infeasible" instead of a named refusal.
        fixture = _su_fixture()
        fixture["account"]["wealth_lo"] = 0.0
        node = _su_node()
        with pytest.raises(ValueError, match="wealth_lo"):
            node.run(_ctx(tmp_path), fixture)

    def test_a_negative_wealth_lo_is_refused_by_name(self, tmp_path):
        fixture = _su_fixture()
        fixture["account"]["wealth_lo"] = -500.0
        node = _su_node()
        with pytest.raises(ValueError, match="wealth_lo"):
            node.run(_ctx(tmp_path), fixture)
