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


class RobustTwoNameSolve(TwoNameSolve):
    """The same fixture with an opt-in budgeted mean-error set."""

    def mean_uncertainty(self, inputs):
        return inputs.get("mean_uncertainty")


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

    def test_null_cardinality_is_explicitly_unconstrained(self):
        assert TwoNameSolve.validate_params({**SU_PARAMS, "cardinality": None}) == []

    @pytest.mark.parametrize("bad", [0, -1, 1.5, True, "5"])
    def test_a_non_null_cardinality_must_be_a_positive_int(self, bad):
        problems = TwoNameSolve.validate_params({**SU_PARAMS, "cardinality": bad})
        assert any("cardinality" in p for p in problems)

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
                "tranches": {},
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

    def test_null_cardinality_adds_no_cap_row(self, tmp_path):
        # null must mean "no row at all", so it solves exactly like a cap
        # that can never bind (one slot per candidate name).
        fixture = _su_fixture()
        free = _su_node(cardinality=None).run(_ctx(tmp_path), fixture)
        loose = _su_node(cardinality=len(fixture["names"])).run(_ctx(tmp_path), fixture)
        assert free["target"] == loose["target"]
        assert free["trades"] == loose["trades"]
        built = []

        class Spy(TwoNameSolve):
            def build_model(self, inputs, params):
                built.append(super().build_model(inputs, params))
                return built[-1]

        Spy("size", {**SU_PARAMS, "cardinality": None}).run(_ctx(tmp_path), fixture)
        assert built and not hasattr(built[0], "cardinality")

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


# ---------------------------------------------------------------------------
# SolveRecord (ADR-0183 phase 2 item 1) — what every PyomoSolve solve records
# ---------------------------------------------------------------------------

from types import SimpleNamespace  # noqa: E402

from dskit.pipeline.libs.pyomo import BINDING_TOLERANCE, SolveRecord  # noqa: E402


class ToyLP(PyomoSolve):
    """A two-variable LP carrying an imported ``dual`` Suffix."""

    role = "transform"
    outputs = ("x",)

    def build_model(self, inputs, params):
        from pyomo.environ import (
            ConcreteModel,
            Constraint,
            NonNegativeReals,
            Objective,
            Suffix,
            Var,
            maximize,
        )

        model = ConcreteModel()
        model.x = Var([1, 2], domain=NonNegativeReals)
        model.value = Objective(expr=3 * model.x[1] + 2 * model.x[2], sense=maximize)
        model.total = Constraint(expr=model.x[1] + model.x[2] <= 4)
        model.cap = Constraint([1, 2], rule=lambda m, i: m.x[i] <= 3)
        model.dual = Suffix(direction=Suffix.IMPORT)
        return model

    def extract(self, model, results):
        return {"x": {i: float(model.x[i].value) for i in (1, 2)}}


def _binding(record):
    return {row["name"]: row for row in record.binding}


class TestSolveRecord:
    def test_no_record_before_a_solve(self):
        assert _node().solve_record is None
        assert PyomoSolve.solve_record is None

    def test_the_knapsack_solve_is_recorded(self, tmp_path):
        node = _node()
        node.run(_ctx(tmp_path), _inputs())
        record = node.solve_record
        assert isinstance(record, SolveRecord)
        assert record.solver == DEFAULT_SOLVER
        assert record.status == "ok"
        assert record.termination == "optimal"
        assert record.objective == pytest.approx(12.0)
        assert record.bound == pytest.approx(12.0)
        assert record.gap == pytest.approx(0.0)
        assert record.seconds >= 0.0
        assert record.variables == 3  # one binary per surviving candidate
        assert record.constraints == 1
        budget = _binding(record)["budget"]
        # BRAVO + GAMMA spend exactly the budget: the one row binds, no slack.
        assert budget["rows"] == 1 and budget["binding"] == 1
        assert budget["min_slack"] == pytest.approx(0.0, abs=BINDING_TOLERANCE)
        assert "dual" not in budget  # a MIP carries no dual Suffix

    def test_a_slack_row_is_counted_but_not_binding(self, tmp_path):
        node = _node(budget=17.0)
        node.run(_ctx(tmp_path), _inputs())
        budget = _binding(node.solve_record)["budget"]
        assert budget["rows"] == 1 and budget["binding"] == 0
        assert budget["min_slack"] == pytest.approx(1.0)

    def test_an_lp_with_a_dual_suffix_records_duals_per_component(self, tmp_path):
        node = ToyLP("lp", {})
        node.run(_ctx(tmp_path), {})
        rows = _binding(node.solve_record)
        assert set(rows) == {"total", "cap"}
        assert rows["total"] == {"name": "total", "rows": 1, "binding": 1,
                                 "min_slack": pytest.approx(0.0), "dual": pytest.approx(2.0)}
        # x1 = 3 binds cap[1]; x2 = 1 leaves cap[2] two units of slack.
        assert rows["cap"]["rows"] == 2 and rows["cap"]["binding"] == 1
        assert rows["cap"]["min_slack"] == pytest.approx(0.0)
        assert rows["cap"]["dual"] == pytest.approx(1.0)  # the largest-magnitude row dual
        assert node.solve_record.constraints == 3 and node.solve_record.variables == 2

    def test_the_gap_is_relative_and_none_without_a_bound(self):
        from pyomo.environ import ConcreteModel, Objective, Var, maximize

        model = ConcreteModel()
        model.x = Var(initialize=10.0)
        model.value = Objective(expr=model.x, sense=maximize)

        def results(lower, upper, termination="maxTimeLimit"):
            return SimpleNamespace(
                problem=[SimpleNamespace(lower_bound=lower, upper_bound=upper)],
                solver=SimpleNamespace(status="aborted", termination_condition=termination),
            )

        record = SolveRecord.from_solve(model, results(10.0, 12.0), "highs", 0.5)
        assert record.objective == 10.0 and record.bound == 12.0
        assert record.gap == pytest.approx(0.2)
        assert record.status == "aborted" and record.termination == "maxTimeLimit"
        open_bound = SolveRecord.from_solve(model, results(10.0, float("inf")), "highs", 0.5)
        assert open_bound.bound is None and open_bound.gap is None
        model.x.set_value(None)
        unsolved = SolveRecord.from_solve(model, results(None, None, "infeasible"), "highs", 0.1)
        assert unsolved.objective is None and unsolved.gap is None

    def test_to_obj_is_every_field_and_json_ready(self, tmp_path):
        node = _node()
        node.run(_ctx(tmp_path), _inputs())
        obj = node.solve_record.to_obj()
        assert tuple(obj) == SolveRecord.field_names()
        assert json.loads(json.dumps(obj)) == obj
        assert isinstance(obj["binding"], list)

    def test_the_empty_gate_short_circuit_records_nothing(self, tmp_path):
        node = _node()
        node.run(_ctx(tmp_path), _inputs())
        assert node.solve_record is not None
        node.run(_ctx(tmp_path), _inputs(survivors=[]))
        assert node.solve_record is None

    def test_the_scenario_utility_short_circuit_records_nothing(self, tmp_path):
        node = _su_node()
        node.run(_ctx(tmp_path), _su_fixture())
        assert node.solve_record is not None and node.solve_record.termination == "optimal"
        node.run(_ctx(tmp_path), _su_fixture(names=[], rows={}, account={"cash": 1.0}))
        assert node.solve_record is None


class TestScenarioUtilityTranches:
    """ADR-0188 formulation B: a name's payoff may be a matrix of exit-horizon
    tranches (one scenario row per horizon); the doorway allocates the target
    shares across tranches with continuous ``e`` variables. A flat payoff is
    the one-tranche case and must build exactly today's model."""

    def test_flat_payoffs_build_no_tranche_variables(self, tmp_path):
        built = []

        class Spy(TwoNameSolve):
            def build_model(self, inputs, params):
                built.append(super().build_model(inputs, params))
                return built[-1]

        Spy("size", SU_PARAMS).run(_ctx(tmp_path), _su_fixture())
        assert built and not hasattr(built[0], "e")
        assert not hasattr(built[0], "tranche_allocation")

    def test_a_one_tranche_matrix_solves_exactly_like_a_flat_payoff(self, tmp_path):
        flat = _su_fixture()
        matrix = _su_fixture()
        matrix["r"] = {name: [list(values)] for name, values in flat["r"].items()}
        out_flat = _su_node().run(_ctx(tmp_path), flat)
        out_matrix = _su_node().run(_ctx(tmp_path), matrix)
        assert out_matrix["target"] == out_flat["target"]
        assert out_matrix["trades"] == out_flat["trades"]
        assert out_matrix["metrics"]["expected_utility"] == pytest.approx(
            out_flat["metrics"]["expected_utility"]
        )

    def test_tranches_send_every_target_share_to_the_horizon_that_pays(self, tmp_path):
        fixture = _su_fixture()
        # AAA: exiting at horizon 1 loses in both scenarios, exiting at
        # horizon 2 gains in both; BBB stays flat, one tranche.
        fixture["r"] = {"AAA": [[-0.02, -0.03], [0.05, 0.04]], "BBB": [0.0, 0.0]}
        node = _su_node(cvar_limit=None, cardinality=None, min_ticket=0.0)
        out = node.run(_ctx(tmp_path), fixture)
        assert out["target"].get("AAA", 0) > 0
        tranches = out["metrics"]["tranches"]["AAA"]
        assert len(tranches) == 2
        assert tranches[0] == pytest.approx(0.0, abs=1e-6)
        assert tranches[1] == pytest.approx(out["target"]["AAA"], abs=1e-6)
        assert "BBB" not in out["metrics"]["tranches"]

    def test_tranche_allocation_sums_to_the_target_in_every_name(self, tmp_path):
        fixture = _su_fixture()
        fixture["r"] = {"AAA": [[0.01, 0.02], [0.02, 0.01]], "BBB": [[0.00, 0.01], [0.01, 0.00]]}
        node = _su_node(cvar_limit=None, cardinality=None, min_ticket=0.0)
        out = node.run(_ctx(tmp_path), fixture)
        for name, target in out["target"].items():
            assert sum(out["metrics"]["tranches"][name]) == pytest.approx(target, abs=1e-6)

    def test_mismatched_tranche_lengths_are_refused_by_name(self, tmp_path):
        fixture = _su_fixture()
        fixture["r"] = {"AAA": [[0.01, 0.02], [0.02]], "BBB": [0.0, 0.0]}
        with pytest.raises(ValueError, match="AAA"):
            _su_node().run(_ctx(tmp_path), fixture)

    @pytest.mark.parametrize(
        "bad",
        [
            [[[0.01, 0.02]], [[0.02, 0.01]]],  # three dimensions
            [[0.01, 0.02, 0.03], [0.02, 0.01, 0.0]],  # a real matrix with the wrong scenario count
            [[0.01, 0.02], [float("nan"), 0.01]],  # non-finite inside a K > 1 matrix
            [[0.01, 0.02], [float("inf"), 0.01]],
        ],
    )
    def test_a_malformed_tranche_matrix_is_refused_by_name(self, tmp_path, bad):
        # Skeptic round 1 (tests lens, M1): the shape rules past the ragged case.
        fixture = _su_fixture()
        fixture["r"] = {"AAA": bad, "BBB": [0.0, 0.0]}
        with pytest.raises(ValueError, match="AAA"):
            _su_node().run(_ctx(tmp_path), fixture)

    def test_a_binding_cardinality_counts_names_not_tranches(self, tmp_path):
        # Skeptic round 1 (tests lens, M2): model.y is per name, so a
        # cardinality of one with two two-tranche names holds at most ONE
        # name, whose split still sums to its integer target.
        fixture = _su_fixture()
        fixture["r"] = {"AAA": [[0.01, 0.02], [0.02, 0.01]], "BBB": [[0.01, 0.02], [0.02, 0.01]]}
        out = _su_node(cvar_limit=None, cardinality=1, min_ticket=0.0).run(_ctx(tmp_path), fixture)
        held = {name: shares for name, shares in out["target"].items() if shares}
        assert len(held) == 1
        for name, target in held.items():
            assert len(out["metrics"]["tranches"][name]) == 2
            assert sum(out["metrics"]["tranches"][name]) == pytest.approx(target, abs=1e-6)
        assert out["metrics"]["n_held"] == 1

    def test_carried_wealth_is_wealth_but_never_cash(self, tmp_path):
        base = _su_fixture()
        carried = _su_fixture()
        carried["account"]["carried_wealth"] = 2500.0
        carried["account"]["wealth_lo"] = 7500.0
        carried["account"]["wealth_hi"] = 17500.0
        out_base = _su_node(cvar_limit=None).run(_ctx(tmp_path), base)
        out_carried = _su_node(cvar_limit=None).run(_ctx(tmp_path), carried)
        assert out_carried["trades"] == out_base["trades"]
        assert out_carried["cash_after"] == pytest.approx(out_base["cash_after"])
        assert out_carried["metrics"]["wealth_min"] == pytest.approx(
            out_base["metrics"]["wealth_min"] + 2500.0
        )
        assert out_carried["metrics"]["wealth_max"] == pytest.approx(
            out_base["metrics"]["wealth_max"] + 2500.0
        )

    def test_carried_wealth_cannot_fund_a_buy(self, tmp_path):
        fixture = _su_fixture()
        fixture["account"]["cash"] = 120.0
        fixture["account"]["buying_power"] = 120.0
        fixture["account"]["carried_wealth"] = 50000.0
        fixture["account"]["wealth_lo"] = 25000.0
        fixture["account"]["wealth_hi"] = 75000.0
        out = _su_node(cvar_limit=None, cardinality=None, min_ticket=0.0).run(_ctx(tmp_path), fixture)
        notional = sum(
            shares * (100.0 if name == "AAA" else 50.0) for name, shares in out["target"].items()
        )
        assert notional <= 120.0 + 1e-6

    def test_the_empty_gate_still_carries_the_outside_wealth(self, tmp_path):
        # Skeptic round 1: the empty-gate return path must report the same
        # wealth and the same metrics keys as a solved tick.
        fixture = _su_fixture()
        fixture["names"] = []
        fixture["rows"] = {}
        fixture["account"]["carried_wealth"] = 2500.0
        out = _su_node().run(_ctx(tmp_path), fixture)
        assert out["target"] == {} and out["trades"] == {}
        assert out["cash_after"] == pytest.approx(fixture["account"]["cash"])
        assert out["metrics"]["wealth_min"] == pytest.approx(fixture["account"]["cash"] + 2500.0)
        assert out["metrics"]["wealth_max"] == pytest.approx(fixture["account"]["cash"] + 2500.0)
        assert out["metrics"]["tranches"] == {}

    @pytest.mark.parametrize("bad", [float("nan"), -1.0, "10", True])
    def test_a_bad_carried_wealth_is_refused_on_the_empty_gate_too(self, tmp_path, bad):
        fixture = _su_fixture()
        fixture["names"] = []
        fixture["rows"] = {}
        fixture["account"]["carried_wealth"] = bad
        with pytest.raises(ValueError, match="carried_wealth"):
            _su_node().run(_ctx(tmp_path), fixture)

    @pytest.mark.parametrize("bad", [float("nan"), float("inf"), -1.0, "10", True])
    def test_a_bad_carried_wealth_is_refused_by_name(self, tmp_path, bad):
        fixture = _su_fixture()
        fixture["account"]["carried_wealth"] = bad
        with pytest.raises(ValueError, match="carried_wealth"):
            _su_node().run(_ctx(tmp_path), fixture)


class TestScenarioUtilityMeanRobustness:
    """Bertsimas-Sim mean uncertainty is opt-in, shared by every scenario,
    and budgeted by name rather than by fractional exit tranche."""

    @staticmethod
    def _node():
        return RobustTwoNameSolve(
            "robust", {**SU_PARAMS, "cardinality": None, "min_ticket": 0.0}
        )

    @staticmethod
    def _fixture(budget=1.5):
        fixture = _su_fixture()
        fixture["r"] = {
            "AAA": [[0.08, 0.08], [0.10, 0.10]],
            "BBB": [0.06, 0.06],
        }
        fixture["mean_uncertainty"] = {
            "budget": budget,
            "deviation_below": {"AAA": [0.01, 0.03], "BBB": [0.02]},
            # Deliberately asymmetric: long-only target shares make only
            # the downward half adverse, but the exact set still carries both.
            "deviation_above": {"AAA": [0.07, 0.09], "BBB": [0.08]},
        }
        return fixture

    def test_the_default_hook_keeps_the_nominal_output_contract_exact(self, tmp_path):
        out = _su_node(cvar_limit=None).run(_ctx(tmp_path), _su_fixture())
        assert "robust_protection" not in out["metrics"]
        assert "nominal_wealth_min" not in out["metrics"]
        assert "wealth_lo" not in out["metrics"]

    @pytest.mark.parametrize("budget", [1.0, 1.5, 2.0])
    def test_exact_protection_matches_budgeted_mean_set_by_name(self, tmp_path, budget):
        from dskit.pipeline.uncertainty_set import BudgetedMeanSet

        fixture = self._fixture(budget)
        out = self._node().run(_ctx(tmp_path), fixture)
        target = out["target"]
        tranches = out["metrics"]["tranches"]
        impacts = {
            "AAA": 100.0
            * sum(
                width * shares
                for width, shares in zip(
                    fixture["mean_uncertainty"]["deviation_below"]["AAA"],
                    tranches["AAA"],
                )
            ),
            "BBB": 50.0
            * fixture["mean_uncertainty"]["deviation_below"]["BBB"][0]
            * target.get("BBB", 0),
        }
        exact = BudgetedMeanSet(
            nominal={name: 0.0 for name in impacts},
            deviation_below={name: 1.0 for name in impacts},
            deviation_above={name: 1.0 for name in impacts},
            budget=min(budget, len(impacts)),
        ).protection(impacts)
        assert out["metrics"]["robust_protection"] == pytest.approx(exact)
        assert out["metrics"]["nominal_wealth_min"] - out["metrics"]["wealth_min"] == pytest.approx(exact)
        assert out["metrics"]["nominal_wealth_max"] - out["metrics"]["wealth_max"] == pytest.approx(exact)

    def test_fractional_tranche_splitting_cannot_dilute_one_names_component(self, tmp_path):
        class SplitRobust(RobustTwoNameSolve):
            def domain_constraints(self, model, inputs, params):
                from pyomo.environ import Constraint

                model.equal_split = Constraint(expr=2.0 * model.e["AAA", 0] == model.q["AAA"])

        fixture = self._fixture(1.0)
        fixture["names"] = ["AAA"]
        fixture["rows"] = {"AAA": fixture["rows"]["AAA"]}
        fixture["r"] = {"AAA": [[0.08, 0.08], [0.08, 0.08]]}
        fixture["mean_uncertainty"] = {
            "budget": 1.0,
            "deviation_below": {"AAA": [0.02, 0.02]},
            "deviation_above": {"AAA": [0.03, 0.03]},
        }
        out = SplitRobust(
            "robust", {**SU_PARAMS, "cardinality": None, "min_ticket": 0.0}
        ).run(_ctx(tmp_path), fixture)
        shares = out["target"]["AAA"]
        assert 0.0 < out["metrics"]["tranches"]["AAA"][0] < shares
        assert out["metrics"]["robust_protection"] == pytest.approx(
            100.0 * 0.02 * shares
        )

    def test_cvar_reads_robust_not_nominal_wealth(self, tmp_path):
        fixture = self._fixture(1.0)
        out = self._node().run(_ctx(tmp_path), fixture)
        w0 = fixture["account"]["cash"]
        protection = out["metrics"]["robust_protection"]
        # Both scenario columns are identical, so CVaR is exactly the one
        # robust loss: nominal loss plus the shared protection scalar.
        nominal_loss = w0 - out["metrics"]["nominal_wealth_min"]
        assert out["metrics"]["cvar"] == pytest.approx(nominal_loss + protection)
        assert out["metrics"]["wealth_lo"] == fixture["account"]["wealth_lo"]

    @pytest.mark.parametrize(
        "uncertainty, match",
        [
            ({"budget": 1.0, "deviation_below": {"AAA": [0.1]}, "deviation_above": {}}, "names"),
            (
                {
                    "budget": 1.0,
                    "deviation_below": {"AAA": [0.1]},
                    "deviation_above": {"AAA": [0.1]},
                },
                "tranches",
            ),
            (
                {
                    "budget": 1.0,
                    "deviation_below": {"ZZZ": [0.1]},
                    "deviation_above": {"ZZZ": [0.1]},
                },
                "instruments",
            ),
        ],
    )
    def test_malformed_mean_uncertainty_refuses_by_name(self, tmp_path, uncertainty, match):
        fixture = self._fixture()
        fixture["mean_uncertainty"] = uncertainty
        with pytest.raises(ValueError, match=match):
            self._node().run(_ctx(tmp_path), fixture)


class ReusableRobustTwoNameSolve(RobustTwoNameSolve):
    """The explicit persistent-model opt-in used by the lifecycle tests."""

    _PERSISTENT_MODEL_REUSE = True


class TestScenarioUtilityPersistentReuse:
    """ADR-0188: same-shape ticks reuse one APPsi model and solver safely."""

    @staticmethod
    def _node(cls=ReusableRobustTwoNameSolve):
        return cls("reuse", {**SU_PARAMS, "cardinality": None, "min_ticket": 0.0})

    @staticmethod
    def _first():
        return TestScenarioUtilityMeanRobustness._fixture(1.0)

    @classmethod
    def _second(cls):
        fixture = json.loads(json.dumps(cls._first()))
        fixture["weights"] = [0.7, 0.3]
        fixture["rows"]["AAA"].update(
            price=103.0, held=3, x_max=7200.0, cost_buy=0.08,
            cost_sell=0.09, exit_cost_per_share=0.04,
        )
        fixture["rows"]["BBB"].update(
            price=47.0, held=2, x_max=5400.0, cost_buy=0.03,
            cost_sell=0.035, exit_cost_per_share=0.02,
        )
        fixture["r"] = {
            "AAA": [[0.031, 0.025], [0.044, 0.038]],
            "BBB": [0.019, 0.012],
        }
        fixture["account"].update(
            cash=9300.0, buying_power=8800.0, wealth_lo=4800.0,
            wealth_hi=17000.0, cash_reserve=125.0, gross_limit=9100.0,
            carried_wealth=350.0,
        )
        fixture["mean_uncertainty"] = {
            "budget": 1.7,
            "deviation_below": {"AAA": [0.012, 0.027], "BBB": [0.016]},
            "deviation_above": {"AAA": [0.021, 0.035], "BBB": [0.024]},
        }
        return fixture

    def test_same_shape_reuses_model_solver_and_refreshes_all_base_families(self, tmp_path):
        built, solvers, solve_calls = [], [], []

        class Spy(ReusableRobustTwoNameSolve):
            def build_model(self, inputs, params):
                model = super().build_model(inputs, params)
                built.append(model)
                return model

            def _resolve_solver(self):
                solver = super()._resolve_solver()
                solvers.append(solver)
                solve = solver.solve

                def recorded(model, **kwargs):
                    solve_calls.append((id(model), dict(kwargs)))
                    return solve(model, **kwargs)

                solver.solve = recorded
                return solver

        node = self._node(Spy)
        node.run(_ctx(tmp_path), self._first())
        warm = node.run(_ctx(tmp_path), self._second())
        cold = RobustTwoNameSolve("cold", node.params).run(_ctx(tmp_path), self._second())

        assert len(built) == len(solvers) == 1
        assert [model_id for model_id, _ in solve_calls] == [id(built[0]), id(built[0])]
        assert solve_calls[0][1].get("warmstart", False) is False
        assert solve_calls[1][1]["warmstart"] is True
        assert built[0]._scn["rows"]["AAA"]["price"] == 103.0
        assert list(built[0]._scn["weights"]) == [0.7, 0.3]
        assert built[0]._scn["cash0"] == 9300.0
        assert built[0]._scn["mean_uncertainty"]["budget"] == pytest.approx(1.7)
        assert built[0].input_price["AAA"].value == pytest.approx(103.0)
        assert built[0].input_held["AAA"].value == 3
        assert built[0].input_x_max["BBB"].value == pytest.approx(5400.0)
        assert built[0].input_cost_buy["AAA"].value == pytest.approx(0.08)
        assert built[0].input_cost_sell["BBB"].value == pytest.approx(0.035)
        assert built[0].input_exit_cost["AAA"].value == pytest.approx(0.04)
        assert built[0].input_cash.value == pytest.approx(9300.0)
        assert built[0].input_buying_power.value == pytest.approx(8800.0)
        assert built[0].input_cash_reserve.value == pytest.approx(125.0)
        assert built[0].input_carried_wealth.value == pytest.approx(350.0)
        assert built[0].input_scenario_weight[0].value == pytest.approx(0.7)
        assert built[0].input_robust_budget.value == pytest.approx(1.7)
        assert built[0].input_mean_deviation["AAA", 1].value == pytest.approx(0.027)
        assert built[0].W[0].lb == pytest.approx(4800.0)
        assert built[0].W[0].ub == pytest.approx(17000.0)
        assert built[0].s["AAA"].ub == 3
        assert warm["target"] == cold["target"]
        assert warm["trades"] == cold["trades"]
        for key in (
            "cash_after", "gross_exposure", "cvar", "expected_utility",
            "robust_protection", "wealth_min", "wealth_max",
        ):
            assert warm["metrics"][key] == pytest.approx(cold["metrics"][key])

    def test_a_shape_change_builds_an_isolated_entry_then_reuses_the_old_shape(self, tmp_path):
        built = []

        class Spy(ReusableRobustTwoNameSolve):
            def build_model(self, inputs, params):
                model = super().build_model(inputs, params)
                built.append(model)
                return model

        node = self._node(Spy)
        first = self._first()
        node.run(_ctx(tmp_path), first)
        changed = json.loads(json.dumps(first))
        changed["weights"] = [0.4, 0.3, 0.3]
        changed["r"] = {
            "AAA": [[0.08, 0.08, 0.07], [0.10, 0.10, 0.09]],
            "BBB": [0.06, 0.06, 0.05],
        }
        node.run(_ctx(tmp_path), changed)
        node.run(_ctx(tmp_path), first)
        assert len(built) == 2
        assert len({id(model) for model in built}) == 2

    def test_shape_cache_is_lru_bounded(self, tmp_path):
        built = []

        class Spy(ReusableRobustTwoNameSolve):
            def build_model(self, inputs, params):
                model = super().build_model(inputs, params)
                built.append(model)
                return model

        node = self._node(Spy)

        def shaped(n_scenarios):
            fixture = json.loads(json.dumps(self._first()))
            fixture["weights"] = [1.0 / n_scenarios] * n_scenarios
            fixture["r"] = {
                "AAA": [[0.08] * n_scenarios, [0.10] * n_scenarios],
                "BBB": [0.06] * n_scenarios,
            }
            return fixture

        for n_scenarios in range(2, 7):
            node.run(_ctx(tmp_path), shaped(n_scenarios))
        assert len(node._persistent_cache) == node._PERSISTENT_CACHE_SIZE == 4
        node.run(_ctx(tmp_path), shaped(2))
        assert len(built) == 6  # the least-recently-used two-scenario shape was evicted

    def test_solver_without_persistent_warm_support_stays_cold(self, tmp_path):
        built, solve_calls = [], []

        class NoReuse(ReusableRobustTwoNameSolve):
            def build_model(self, inputs, params):
                model = super().build_model(inputs, params)
                built.append(model)
                return model

            @staticmethod
            def _solver_supports_reuse(solver):
                del solver
                return False

            def _resolve_solver(self):
                solver = super()._resolve_solver()
                solve = solver.solve

                def recorded(model, **kwargs):
                    solve_calls.append(dict(kwargs))
                    return solve(model, **kwargs)

                solver.solve = recorded
                return solver

        node = self._node(NoReuse)
        node.run(_ctx(tmp_path), self._first())
        node.run(_ctx(tmp_path), self._second())
        assert len(built) == 2
        assert solve_calls == [{}, {}]
        assert not node._persistent_cache

    def test_any_cached_hit_failure_evicts_and_retries_the_same_input_cold(self, tmp_path):
        built, resolved, warm_attempts = [], [], []

        class WarmFails(ReusableRobustTwoNameSolve):
            def build_model(self, inputs, params):
                model = super().build_model(inputs, params)
                built.append(model)
                return model

            def _resolve_solver(self):
                solver = super()._resolve_solver()
                resolved.append(solver)
                solve = solver.solve

                def fail_warm(model, **kwargs):
                    if kwargs.get("warmstart"):
                        warm_attempts.append(id(model))
                        raise RuntimeError("synthetic invalid warm incumbent")
                    return solve(model, **kwargs)

                solver.solve = fail_warm
                return solver

        node = self._node(WarmFails)
        node.run(_ctx(tmp_path), self._first())
        got = node.run(_ctx(tmp_path), self._second())
        cold = RobustTwoNameSolve("cold", node.params).run(_ctx(tmp_path), self._second())
        assert got["target"] == cold["target"] and got["trades"] == cold["trades"]
        assert len(warm_attempts) == 1
        assert len(built) == len(resolved) == 2
        assert built[0] is not built[1]

    def test_a_cached_extraction_failure_also_retries_cold(self, tmp_path):
        built = []

        class StaleExtract(ReusableRobustTwoNameSolve):
            calls = 0

            def build_model(self, inputs, params):
                model = super().build_model(inputs, params)
                built.append(model)
                return model

            def extract(self, model, results):
                self.calls += 1
                if self.calls == 2:
                    raise AssertionError("synthetic stale persistent coefficients")
                return super().extract(model, results)

        node = self._node(StaleExtract)
        node.run(_ctx(tmp_path), self._first())
        got = node.run(_ctx(tmp_path), self._second())
        assert got["target"]
        assert node.calls == 3 and len(built) == 2

    def test_a_forced_tie_is_primary_optimal_and_repeatable_not_cold_vertex_pinned(self, tmp_path):
        first = _su_fixture()
        first["r"] = {"AAA": [0.06, 0.06], "BBB": [0.01, 0.01]}
        tied = _su_fixture()
        tied["rows"]["AAA"]["price"] = tied["rows"]["BBB"]["price"] = 100.0
        tied["rows"]["AAA"]["x_max"] = tied["rows"]["BBB"]["x_max"] = 5000.0
        tied["r"] = {"AAA": [0.04, 0.04], "BBB": [0.04, 0.04]}
        tied["account"]["gross_limit"] = 5000.0

        def sequence():
            node = ReusableRobustTwoNameSolve(
                "tie", {**SU_PARAMS, "cardinality": 1, "min_ticket": 0.0}
            )
            node.run(_ctx(tmp_path), first)
            return node.run(_ctx(tmp_path), tied)

        one, two = sequence(), sequence()
        cold = _su_node(cardinality=1, min_ticket=0.0).run(_ctx(tmp_path), tied)
        assert one["target"] == two["target"] and one["trades"] == two["trades"]
        assert one["metrics"]["objective"] == pytest.approx(cold["metrics"]["objective"])
        assert one["metrics"]["gross_exposure"] <= 5000.0 + 1e-6


#: appsi_highs' own words when a solve ends with no loadable solution (pyomo
#: 6.10, ``contrib/appsi/solvers/highs.py``): an infeasible program, or a time
#: limit reached before any incumbent.
_APPSI_NO_SOLUTION = (
    "A feasible solution was not found, so no solution can be loaded. If using the "
    "appsi.solvers.Highs interface, you can set opt.config.load_solution=False. If using "
    "the environ.SolverFactory interface, you can set opt.solve(model, load_solutions = "
    "False). Then you can check results.termination_condition and "
    "results.best_feasible_objective before loading a solution."
)


class HaltedSolve(TwoNameSolve):
    """TwoNameSolve whose real, resolved solver ends every solve with no loadable
    solution: appsi's own RuntimeError, raised deterministically rather than
    depending on how far HiGHS gets before a time limit."""

    def _resolve_solver(self):
        solver = super()._resolve_solver()

        def halted(model, **kwargs):
            del model, kwargs
            raise RuntimeError(_APPSI_NO_SOLUTION)

        solver.solve = halted
        return solver


def _su_large_fixture(seed=7, n_names=12, n_tranches=3, n_omega=32):
    """A seeded instance HiGHS cannot finish at a zero time limit.

    12 names (three already held), three exit tranches each, 32 scenarios
    sharing one common factor; with ``cardinality`` 4 the optimum is
    combinatorial and takes a real branch-and-bound search.
    """
    import numpy as np

    rng = np.random.default_rng(seed)
    names = [f"N{i:02d}" for i in range(n_names)]
    prices = rng.uniform(20.0, 400.0, n_names)
    held = [int(rng.integers(0, 15)) if i < 3 else 0 for i in range(n_names)]
    rows = {
        name: {
            "price": float(prices[i]),
            "held": held[i],
            "x_max": 6000.0,
            "cost_buy": float(prices[i] * 3e-4),
            "cost_sell": float(prices[i] * 3e-4),
            "exit_cost_per_share": float(prices[i] * 3e-4),
        }
        for i, name in enumerate(names)
    }
    common = rng.normal(0.0, 0.01, n_omega)
    r = {}
    for name in names:
        drift = rng.normal(0.002, 0.002, n_tranches).cumsum()
        r[name] = [
            [
                float(drift[k] + 0.6 * common[o] * np.sqrt(k + 1) + rng.normal(0.0, 0.01))
                for o in range(n_omega)
            ]
            for k in range(n_tranches)
        ]
    cash = 30_000.0
    w0 = cash + sum(rows[name]["price"] * rows[name]["held"] for name in names)
    account = {
        "cash": cash,
        "buying_power": cash,
        "wealth_lo": 0.5 * w0,
        "wealth_hi": 1.5 * w0,
        "sale_credit": 1.0,
        "cash_reserve": 0.0,
        "gross_limit": 25_000.0,
    }
    weights = [1.0 / n_omega] * n_omega
    return {"names": names, "rows": rows, "weights": weights, "r": r, "account": account}


class TestScenarioUtilityTimeLimit:
    """Plan §4.4's breaker: a declared solver time limit is a HALT, never a
    degraded fill. Under appsi_highs the limit is HiGHS's own ``time_limit``
    option, declared through ``solver_options`` and applied verbatim (the
    determinism pins merge under it). A halt that found an incumbent
    returns ``maxTimeLimit``, which ``extract`` refuses by name; a halt
    before any incumbent makes appsi raise from ``solve`` itself, which the
    doorway re-raises naming the node. Either way nothing is sized.

    The named re-raise is pinned DETERMINISTICALLY (``HaltedSolve``: the
    resolved solver raises appsi's own error), because a zero time limit on
    a small program may still return optimal. One real-HiGHS integration
    check runs a zero limit on an instance too large to finish."""

    def test_the_time_limit_reaches_highs_through_solver_options(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "pyomo", None)
        options = _su_node(solver_options={"time_limit": 8.0})._solver_options()
        assert options == {"mip_rel_gap": 0, "threads": 1, "random_seed": 0, "time_limit": 8.0}

    def test_a_generous_time_limit_solves_exactly_like_none(self, tmp_path):
        free = _su_node().run(_ctx(tmp_path), _su_fixture())
        limited = _su_node(solver_options={"time_limit": 30.0}).run(_ctx(tmp_path), _su_fixture())
        assert limited["target"] == free["target"]
        assert limited["trades"] == free["trades"]

    def test_a_halt_before_any_incumbent_refuses_by_name(self, tmp_path):
        node = HaltedSolve("size", SU_PARAMS)
        with pytest.raises(
            RuntimeError, match=r"^size: the solver returned no loadable solution"
        ) as caught:
            node.run(_ctx(tmp_path), _su_fixture())
        assert isinstance(caught.value.__cause__, RuntimeError)
        assert str(caught.value.__cause__) == _APPSI_NO_SOLUTION
        assert node.solve_record is None  # the solve raised: no record, no target

    def test_the_refusal_keeps_the_solvers_own_words(self, tmp_path):
        with pytest.raises(RuntimeError) as caught:
            HaltedSolve("size", SU_PARAMS).run(_ctx(tmp_path), _su_fixture())
        assert _APPSI_NO_SOLUTION in str(caught.value)

    def test_a_real_zero_time_limit_halt_refuses_by_name(self, tmp_path):
        """The INTEGRATION check: real HiGHS, a real time limit of zero.

        The instance solves to a non-trivial optimum without a limit, so a
        refusal under the limit is the halt, never an infeasible program.
        Whether HiGHS halts before an incumbent (appsi raises, re-raised by
        name) or after one (``maxTimeLimit``, refused by ``extract``), the
        refusal names the node and nothing is sized.
        """
        params = {**SU_PARAMS, "cardinality": 4, "min_ticket": 0.0}
        free = TwoNameSolve("size", params).run(_ctx(tmp_path), _su_large_fixture())
        assert free["trades"]
        node = TwoNameSolve("size", {**params, "solver_options": {"time_limit": 0.0}})
        with pytest.raises(
            RuntimeError,
            match=(
                r"^size: (the solver returned no loadable solution"
                r"|solver finished with termination condition 'maxTimeLimit')"
            ),
        ):
            node.run(_ctx(tmp_path), _su_large_fixture())

    def test_a_halt_with_an_incumbent_is_refused_by_extract(self):
        from types import SimpleNamespace

        results = SimpleNamespace(solver=SimpleNamespace(termination_condition="maxTimeLimit"))
        with pytest.raises(RuntimeError, match=r"^size: .*'maxTimeLimit', not 'optimal'"):
            _su_node().extract(None, results)


# ---------------------------------------------------------------------------
# SolveRecord row slacks read off HiGHS's own arrays — the SAME record, faster
# ---------------------------------------------------------------------------


class SlackToyLP(PyomoSolve):
    """An LP with one component per bound shape and an imported ``dual`` Suffix.

    Lower-only, upper-only, equality and ranged rows, a literal constant in
    a body, and a MUTABLE-Param constant in a body on each side — the
    shapes appsi moves into row bounds — so the HiGHS-array slack is held to
    the Pyomo evaluation on every one. Optimum: x1 = 2.5, x2 = 1.5.
    """

    role = "transform"
    outputs = ("x",)

    def build_model(self, inputs, params):
        from pyomo.environ import (
            ConcreteModel,
            Constraint,
            NonNegativeReals,
            Objective,
            Param,
            Suffix,
            Var,
            maximize,
        )

        model = ConcreteModel()
        model.x = Var([1, 2], domain=NonNegativeReals)
        model.p = Param(initialize=2.0, mutable=True)
        model.value = Objective(expr=3 * model.x[1] + 2 * model.x[2], sense=maximize)
        model.lower_only = Constraint(expr=model.x[1] >= 0.5)
        model.upper_only = Constraint(expr=model.x[1] + model.x[2] <= 4)
        model.equality = Constraint(expr=model.x[1] - model.x[2] == 1)
        model.ranged = Constraint(expr=(0.0, model.x[2], 10.0))
        model.literal_constant = Constraint(expr=2 * model.x[1] + model.x[2] + 5 <= 20)
        model.mutable_lower = Constraint(expr=model.x[2] + model.p >= 0.25)
        model.mutable_upper = Constraint(expr=model.x[2] + model.p <= 9)
        model.per_index = Constraint([1, 2], rule=lambda m, i: m.x[i] <= 3 + m.p)
        model.dual = Suffix(direction=Suffix.IMPORT)
        return model

    def _solver_options(self):
        # HiGHS sizes a PROCESS-GLOBAL thread scheduler at the first solve that
        # uses one and refuses a later solve pinning another count (the
        # determinism pins say threads=1), so every solve in this suite pins 1.
        return {"threads": 1, **super()._solver_options()}

    def extract(self, model, results):
        return {"x": {i: float(model.x[i].value) for i in (1, 2)}}


class _Capture:
    """Mixin: keep the ``(solver, model, results)`` of the last solve, so one
    finished solve can be recorded BOTH ways."""

    captured = None

    def _solve(self, solver, model, **kwargs):
        results = super()._solve(solver, model, **kwargs)
        self.captured = (solver, model, results)
        return results


class CapturedToy(_Capture, SlackToyLP):
    pass


class CapturedSelect(_Capture, BudgetedSelect):
    pass


class CapturedTwoName(_Capture, TwoNameSolve):
    pass


class CapturedReusable(_Capture, ReusableRobustTwoNameSolve):
    pass


# ---------------------------------------------------------------------------
# The compiled parameter update: appsi's update_params minus pyomo's evaluator
# ---------------------------------------------------------------------------


def _lp_arrays(solver):
    """Every array of the LP HiGHS holds (matrix, costs, both bound pairs, offset), copied."""
    import numpy as np

    lp = solver._solver_model.getLp()
    arrays = {
        f"a_matrix_.{name}": np.asarray(getattr(lp.a_matrix_, name))
        for name in ("start_", "index_", "value_")
    }
    for name in ("col_cost_", "col_lower_", "col_upper_", "row_lower_", "row_upper_"):
        arrays[name] = np.asarray(getattr(lp, name))
    arrays["offset_"] = np.asarray([lp.offset_])
    return {name: array.copy() for name, array in arrays.items()}


def _assert_same_lp(a, b, where):
    """Bit equality: ``array_equal`` AND identical bytes (so -0.0 vs 0.0 and NaN cannot pass)."""
    import numpy as np

    assert a.keys() == b.keys()
    for name in a:
        assert np.array_equal(a[name], b[name]), (where, name)
        assert a[name].dtype == b[name].dtype and a[name].tobytes() == b[name].tobytes(), (where, name)


class _LpSpy:
    """Mixin: record the LP HiGHS holds after appsi's update and before it optimizes."""

    lps = None

    def _resolve_solver(self):
        solver = super()._resolve_solver()
        if self.lps is None:
            self.lps = []
        optimize = solver._solve  # appsi's private step that follows update()

        def spy(timer):
            self.lps.append(_lp_arrays(solver))
            return optimize(timer)

        solver._solve = spy
        return solver


class CompiledReusable(_LpSpy, _Capture, ReusableRobustTwoNameSolve):
    """The persistent opt-in with the compiled update on (the default)."""


class StockReusable(CompiledReusable):
    """The reference arm: appsi's own ``update_params``."""

    _PERSISTENT_COMPILED_UPDATE = False


class SqrtRowReusable(CompiledReusable):
    """A domain row whose coefficient holds a node the compiler does not handle."""

    def domain_constraints(self, model, inputs, params):
        from pyomo.environ import Constraint, Param, sqrt

        model.input_cap = Param(mutable=True, initialize=float(inputs.get("cap", 4.0)))
        model.capped = Constraint(expr=sqrt(model.input_cap) * model.x["AAA"] <= 1.0e9)

    def _persistent_domain_signature(self, inputs, prepared, params):
        del inputs, prepared, params
        return ("sqrt_row",)

    def _refresh_domain_constraints(self, model, inputs, params):
        del params
        model.input_cap.set_value(float(inputs.get("cap", 4.0)))


class SqrtRowStock(SqrtRowReusable):
    _PERSISTENT_COMPILED_UPDATE = False


class SwappedRowReusable(CompiledReusable):
    """A domain row that a persistent hit REPLACES: appsi finds it inside its own update."""

    refreshes = 0

    def domain_constraints(self, model, inputs, params):
        from pyomo.environ import Constraint, Param

        model.input_cap = Param(mutable=True, initialize=4.0)
        model.capped = Constraint(expr=model.input_cap * model.x["AAA"] <= 1.0e9)

    def _persistent_domain_signature(self, inputs, prepared, params):
        del inputs, prepared, params
        return ("swapped_row",)

    def _refresh_domain_constraints(self, model, inputs, params):
        del inputs, params
        self.refreshes += 1
        model.input_cap.set_value(4.0 + self.refreshes)
        if self.refreshes == 2:
            model.capped.set_value(2.0 * model.input_cap * model.x["AAA"] <= 1.0e9)


class SwappedRowStock(SwappedRowReusable):
    _PERSISTENT_COMPILED_UPDATE = False


def _persistent_minutes(n=6):
    """Seeded same-shape minutes: prices, holdings (some zero), returns, carried wealth,
    the robust block and a zero deviation all move; AAA has two tranches, BBB one."""
    import numpy as np

    rng = np.random.default_rng(20260929)
    base = TestScenarioUtilityPersistentReuse._second()
    held = [(3, 2), (0, 4), (5, 0), (0, 0), (2, 3), (4, 1)]
    carried = [350.0, 0.0, 125.0, 900.0, 0.0, 60.0]
    minutes = []
    for k in range(n):
        fixture = json.loads(json.dumps(base))
        for name, prices in (("AAA", (90.0, 115.0)), ("BBB", (40.0, 60.0))):
            fixture["rows"][name].update(
                price=float(rng.uniform(*prices)),
                held=held[k % len(held)][0 if name == "AAA" else 1],
                cost_buy=float(rng.uniform(0.02, 0.09)),
                cost_sell=float(rng.uniform(0.02, 0.09)),
                exit_cost_per_share=float(rng.uniform(0.01, 0.05)),
            )
        w = float(rng.uniform(0.3, 0.7))
        fixture["weights"] = [w, 1.0 - w]
        fixture["r"] = {
            "AAA": rng.normal(0.02, 0.02, (2, 2)).tolist(),
            "BBB": rng.normal(0.015, 0.02, 2).tolist(),
        }
        cash = float(rng.uniform(8000.0, 10000.0))
        fixture["account"].update(
            cash=cash, buying_power=cash, carried_wealth=carried[k % len(carried)],
            cash_reserve=float(rng.uniform(0.0, 200.0)),
        )

        def deviation():
            return float(rng.uniform(0.0, 0.03))

        fixture["mean_uncertainty"] = {
            "budget": float(rng.uniform(0.5, 2.0)),
            "deviation_below": {"AAA": [deviation(), deviation()], "BBB": [deviation()]},
            "deviation_above": {"AAA": [deviation(), deviation()], "BBB": [deviation()]},
        }
        if k == 3:
            fixture["mean_uncertainty"]["deviation_below"]["AAA"][0] = 0.0
        if k == 2:  # a falling AAA that is held: the book sells
            fixture["r"]["AAA"] = [[-0.04, -0.03], [-0.05, -0.04]]
        fixture["cap"] = float(rng.uniform(2.0, 9.0))
        minutes.append(fixture)
    return minutes


def _run_minutes(cls, tmp_path, minutes=None, node=None):
    """One cold solve then one cache hit per minute; returns ``(node, outputs, records)``.

    A ``node`` the caller already ran cold skips the cold solve here.
    """
    fixtures = list(minutes or _persistent_minutes())
    if node is None:
        node = cls("arm", {**SU_PARAMS, "cardinality": None, "min_ticket": 0.0})
        fixtures.insert(0, TestScenarioUtilityPersistentReuse._first())
    outputs, records = [], []
    for fixture in fixtures:
        outputs.append(node.run(_ctx(tmp_path), fixture))
        records.append(node.solve_record.to_obj())
        records[-1].pop("seconds")
    return node, outputs, records


def _only_entry(node):
    (entry,) = node._persistent_cache.values()
    return entry


class TestCompiledParamUpdate:
    """The persistent path's per-minute ``update_params`` is compiled once and replayed.

    appsi evaluates ~22k mutable expressions per minute through pyomo's
    visitor (~4.5 us each, ~100 ms of a joint solve's ~100 ms update). The
    compiled update makes the SAME highspy calls in the SAME order with
    values from the SAME arithmetic, so the HiGHS model is bit-identical;
    anything it cannot prove identical it hands back to appsi.
    """

    @pytest.mark.parametrize(
        "cls_fast, cls_stock",
        [(CompiledReusable, StockReusable), (SqrtRowReusable, SqrtRowStock)],
        ids=["base_rows", "an_unhandled_node_in_a_domain_row"],
    )
    def test_the_highs_model_is_bit_identical_to_appsis_own_update_every_minute(
        self, tmp_path, cls_fast, cls_stock
    ):
        fast, fast_out, fast_rec = _run_minutes(cls_fast, tmp_path)
        stock, stock_out, stock_rec = _run_minutes(cls_stock, tmp_path)
        updater = _only_entry(fast).get("updater")
        assert updater is not None, "the compiled update never engaged"
        assert updater.n_compiled > 100 and not updater.retired
        assert _only_entry(fast)["solver"].update_config.update_params is False
        assert _only_entry(stock).get("updater") is None
        assert _only_entry(stock)["solver"].update_config.update_params is True
        assert len(fast.lps) == len(stock.lps) == 7
        for minute, (a, b) in enumerate(zip(fast.lps, stock.lps)):
            _assert_same_lp(a, b, minute)
        # not vacuous: every minute moved the matrix values, bounds and costs
        for name in ("a_matrix_.value_", "row_upper_", "col_upper_", "col_cost_"):
            assert len({lp[name].tobytes() for lp in fast.lps}) == len(fast.lps), name
        assert fast_out == stock_out
        assert fast_rec == stock_rec
        assert all(rec["termination"] == "optimal" for rec in fast_rec)
        assert any(o["trades"]["AAA"]["sell"] > 0 for o in fast_out)  # sells and buys both occur

    def test_every_appsi_helper_kind_matches_appsi_on_a_plain_model(self):
        import numpy as np
        from pyomo.environ import (
            ConcreteModel, Constraint, Objective, Param, SolverFactory, Var, maximize,
        )

        from dskit.pipeline.libs.pyomo import _CompiledParamUpdate

        def build():
            m = ConcreteModel()
            m.lo = Param(mutable=True, initialize=0.5)
            m.hi = Param(mutable=True, initialize=4.0)
            m.a = Param([0, 1], mutable=True, initialize=1.0)
            m.off = Param(mutable=True, initialize=1.0)
            m.rhs = Param(mutable=True, initialize=6.0)
            m.x = Var([0, 1], bounds=lambda m, i: (m.lo, m.hi))
            m.row = Constraint(expr=m.a[0] * m.x[0] + m.a[1] * m.x[1] <= m.rhs)
            m.flip = Constraint(expr=m.x[0] - 2 * m.a[0] * m.x[1] >= -m.rhs)
            m.obj = Objective(expr=m.a[1] * m.x[0] - m.x[1] + m.off, sense=maximize)
            return m

        fast_model, stock_model = build(), build()
        fast, stock = SolverFactory("appsi_highs"), SolverFactory("appsi_highs")
        fast.set_instance(fast_model)
        stock.set_instance(stock_model)
        updater = _CompiledParamUpdate.attach(fast)
        helpers = [h for group in _CompiledParamUpdate._registries(fast) for h in group]
        assert {type(h).__name__ for h in helpers} == {
            "_MutableLinearCoefficient", "_MutableConstraintBounds", "_MutableVarBounds",
            "_MutableObjectiveCoefficient", "_MutableObjectiveOffset",
        }
        assert updater.n_compiled == len(helpers) and updater.n_fallback == 0
        rng = np.random.default_rng(3)
        for step in range(6):
            draw = {
                "lo": float(rng.uniform(0.0, 1.0)), "hi": float(rng.uniform(2.0, 5.0)),
                "off": float(rng.normal()), "rhs": float(rng.uniform(1.0, 9.0)),
                "a": [0.0 if step == 3 else float(rng.normal()), float(rng.normal())],
            }
            for m in (fast_model, stock_model):
                m.lo.set_value(draw["lo"])
                m.hi.set_value(draw["hi"])
                m.off.set_value(draw["off"])
                m.rhs.set_value(draw["rhs"])
                for i, value in enumerate(draw["a"]):
                    m.a[i].set_value(value)
            assert updater.apply() is True
            fast.update()
            stock.update()
            _assert_same_lp(_lp_arrays(fast), _lp_arrays(stock), step)

    def test_an_unhandled_node_falls_back_per_helper_and_the_rest_stay_compiled(self, tmp_path):
        node, _, _ = _run_minutes(SqrtRowReusable, tmp_path)
        updater = _only_entry(node)["updater"]
        assert 1 <= updater.n_fallback < updater.n_compiled
        clean, _, _ = _run_minutes(CompiledReusable, tmp_path)
        assert _only_entry(clean)["updater"].n_fallback == 0

    def test_a_compiled_value_that_differs_from_pyomos_is_never_trusted(self, tmp_path, monkeypatch):
        from dskit.pipeline.libs.pyomo import _CompiledParamUpdate

        monkeypatch.setattr(
            _CompiledParamUpdate, "program", staticmethod(lambda expr: (lambda: 1.0))
        )
        wrong, wrong_out, _ = _run_minutes(CompiledReusable, tmp_path)
        stock, stock_out, _ = _run_minutes(StockReusable, tmp_path)
        updater = _only_entry(wrong)["updater"]
        assert updater.n_fallback > 0.9 * (updater.n_fallback + updater.n_compiled)
        for minute, (a, b) in enumerate(zip(wrong.lps, stock.lps)):
            _assert_same_lp(a, b, minute)
        assert wrong_out == stock_out

    @pytest.mark.parametrize(
        "missing",
        [
            "_mutable_helpers", "_mutable_bounds", "_objective_helpers", "_solver_model", "_sol",
            "_last_results_object", "update_config",
        ],
    )
    def test_an_unknown_appsi_layout_keeps_appsis_own_update(self, missing):
        import types

        from dskit.pipeline.libs.pyomo import _CompiledParamUpdate

        def stub():
            return types.SimpleNamespace(
                _mutable_helpers={}, _mutable_bounds={}, _objective_helpers=[],
                _solver_model=object(), _sol=object(), _last_results_object=None,
                update_config=types.SimpleNamespace(update_params=True),
            )

        whole = stub()
        assert _CompiledParamUpdate.attach(whole) is not None  # the stub is a valid layout
        assert whole.update_config.update_params is False
        broken = stub()
        delattr(broken, missing)
        assert _CompiledParamUpdate.attach(broken) is None
        if missing != "update_config":
            assert broken.update_config.update_params is True  # nothing was switched off

    @pytest.mark.parametrize(
        "attribute, wrong",
        [
            ("_mutable_helpers", []), ("_mutable_bounds", []), ("_objective_helpers", {}),
        ],
    )
    def test_a_registry_of_another_shape_keeps_appsis_own_update(self, attribute, wrong):
        import types

        from dskit.pipeline.libs.pyomo import _CompiledParamUpdate

        solver = types.SimpleNamespace(
            _mutable_helpers={}, _mutable_bounds={}, _objective_helpers=[],
            _solver_model=object(), _sol=None, _last_results_object=None,
            update_config=types.SimpleNamespace(update_params=True),
        )
        setattr(solver, attribute, wrong)
        assert _CompiledParamUpdate.attach(solver) is None
        assert solver.update_config.update_params is True

    def test_a_real_solver_with_another_registry_layout_runs_appsis_own_update(self, tmp_path):
        class OtherDict(dict):
            pass

        node = CompiledReusable("arm", {**SU_PARAMS, "cardinality": None, "min_ticket": 0.0})
        node.run(_ctx(tmp_path), TestScenarioUtilityPersistentReuse._first())
        solver = _only_entry(node)["solver"]
        solver._mutable_helpers = OtherDict(solver._mutable_helpers)
        _, out, rec = _run_minutes(CompiledReusable, tmp_path, node=node)
        assert _only_entry(node)["updater"] is None
        assert solver.update_config.update_params is True
        stock, stock_out, stock_rec = _run_minutes(StockReusable, tmp_path)
        assert out == stock_out[1:] and rec == stock_rec[1:]  # the hit minutes; [0] is the cold solve
        for minute, (a, b) in enumerate(zip(node.lps, stock.lps)):
            _assert_same_lp(a, b, minute)

    def test_a_registry_that_moved_since_compile_retires_to_appsis_own_update(self, tmp_path):
        fast, fast_out, fast_rec = _run_minutes(SwappedRowReusable, tmp_path)
        stock, stock_out, stock_rec = _run_minutes(SwappedRowStock, tmp_path)
        updater = _only_entry(fast)["updater"]
        assert updater.retired
        assert _only_entry(fast)["solver"].update_config.update_params is True
        assert fast.refreshes == stock.refreshes == 6
        for minute, (a, b) in enumerate(zip(fast.lps, stock.lps)):
            _assert_same_lp(a, b, minute)
        assert fast_out == stock_out and fast_rec == stock_rec

    def test_the_previous_solution_is_invalid_after_a_compiled_update(self, tmp_path):
        node, _, _ = _run_minutes(CompiledReusable, tmp_path)
        solver, model, _ = node.captured
        loader = solver._last_results_object.solution_loader  # appsi's own record of the last solve
        assert PyomoSolve._highs_row_slacks(solver, model) is not None  # valid right after a solve
        assert solver._sol is not None
        loader.get_primals()
        assert _only_entry(node)["updater"].apply() is True
        assert solver._sol is None
        assert PyomoSolve._highs_row_slacks(solver, model) is None  # no stale arrays
        with pytest.raises(RuntimeError, match="no longer valid"):
            loader.get_primals()

    def test_appsis_own_update_params_invalidates_the_same_way(self, tmp_path):
        node, _, _ = _run_minutes(StockReusable, tmp_path)
        solver, model, _ = node.captured
        loader = solver._last_results_object.solution_loader
        solver.update_params()
        assert solver._sol is None and PyomoSolve._highs_row_slacks(solver, model) is None
        with pytest.raises(RuntimeError, match="no longer valid"):
            loader.get_primals()


def _program_expressions():
    """``(label, builder)`` for each shape the doorway's mutable rows produce, plus the rest of the family."""
    from pyomo.core.expr.numeric_expr import NPV_MaxExpression, NPV_MinExpression

    return {
        "bare_param": lambda m: m.p[0],
        "bare_scalar_param": lambda m: m.s,
        "negated_param": lambda m: -m.p[0],
        "int_times_param": lambda m: 3 * m.p[0],
        "param_times_float": lambda m: m.p[0] * 0.1,
        "param_times_param": lambda m: m.p[0] * m.p[1],
        "int_param_times_int": lambda m: m.i * 7,
        "int_param_times_int_param": lambda m: m.i * m.i,
        "sum_of_params": lambda m: m.p[0] + m.p[1],
        "difference": lambda m: m.p[0] - m.p[1],
        "scaled_difference": lambda m: 3 * (m.p[0] - m.s),
        "negated_sum": lambda m: -(m.p[0] + m.p[1]),
        "nary_sum": lambda m: m.p[0] + m.p[1] + m.p[2] + m.s,
        "negated_scaled_sum": lambda m: -(2 * m.s + 3 * m.p[2]),
        "deep_sum_of_products": lambda m: m.s - ((m.p[0] * m.p[1] + m.p[1] * m.p[2]) + m.p[2] * m.p[0]),
        "division": lambda m: m.p[0] / m.p[1],
        "power": lambda m: m.p[0] ** 2,
        "max": lambda m: NPV_MaxExpression((m.p[0], 0.5)),
        "min": lambda m: NPV_MinExpression((m.p[0], m.p[1])),
        "constant_float": lambda m: 0.25,
        "constant_int": lambda m: 7,
    }


class TestExpressionProgram:
    """``_CompiledParamUpdate.program`` reproduces ``value(expr)`` bit for bit, type included."""

    @staticmethod
    def _model(rng):
        from pyomo.environ import ConcreteModel, Param

        m = ConcreteModel()
        m.p = Param([0, 1, 2], mutable=True, initialize=1.0)
        m.s = Param(mutable=True, initialize=1.0)
        m.i = Param(mutable=True, initialize=1)
        return m

    @staticmethod
    def _draw(rng):
        pool = [0.0, -0.0, 1e-310, 1e300, -1e-300, 0.1, 0.7, 3.0, -2.5, 1.0 / 3.0]
        if rng.random() < 0.4:
            return pool[int(rng.integers(len(pool)))]
        return float(rng.normal()) * 10.0 ** int(rng.integers(-6, 7))

    @pytest.mark.parametrize("label", sorted(_program_expressions()))
    def test_every_supported_shape_matches_pyomos_evaluator(self, label):
        import numpy as np
        import struct

        from pyomo.environ import value

        from dskit.pipeline.libs.pyomo import _CompiledParamUpdate

        rng = np.random.default_rng(11)
        m = self._model(rng)
        expr = _program_expressions()[label](m)
        program = _CompiledParamUpdate.program(expr)
        assert program is not None, label
        for _ in range(60):
            for p in m.p.values():
                p.set_value(self._draw(rng))
            m.s.set_value(self._draw(rng))
            m.i.set_value(int(rng.integers(-50, 50)))
            try:
                want = value(expr)
            except (ArithmeticError, ValueError) as exc:  # e.g. 1/0, 1e300**2: the program raises too
                with pytest.raises(type(exc)):
                    program()
                continue
            got = program() if callable(program) else program
            assert type(got) is type(want), (label, got, want)
            assert struct.pack("<d", float(got)) == struct.pack("<d", float(want)), (label, got, want)

    @pytest.mark.parametrize("label", ["sqrt_of_param", "abs_of_param", "a_variable", "a_string"])
    def test_an_unhandled_node_or_leaf_compiles_to_none(self, label):
        from pyomo.environ import ConcreteModel, Param, Var, sqrt

        from dskit.pipeline.libs.pyomo import _CompiledParamUpdate

        m = ConcreteModel()
        m.p = Param(mutable=True, initialize=2.0)
        m.v = Var(initialize=1.0)
        expr = {
            "sqrt_of_param": lambda: sqrt(m.p) * 3,
            "abs_of_param": lambda: abs(-m.p),
            "a_variable": lambda: m.v,
            "a_string": lambda: "x",
        }[label]()
        assert _CompiledParamUpdate.program(expr) is None


def _toy_run(tmp_path):
    node = CapturedToy("lp", {})
    node.run(_ctx(tmp_path), {})
    return node


def _select_run(tmp_path):
    node = CapturedSelect("select", PARAMS)
    node.run(_ctx(tmp_path), _inputs())
    return node


def _two_name_run(tmp_path):
    node = CapturedTwoName("size", {**SU_PARAMS, "cardinality": None, "min_ticket": 0.0})
    node.run(_ctx(tmp_path), _su_fixture(names=["AAA", "BBB"]))
    return node


def _persistent_runs(tmp_path):
    """Yield the node after its cold solve and again after its warm re-solve."""
    node = CapturedReusable("reuse", {**SU_PARAMS, "cardinality": None, "min_ticket": 0.0})
    node.run(_ctx(tmp_path), TestScenarioUtilityPersistentReuse._first())
    yield node
    node.run(_ctx(tmp_path), TestScenarioUtilityPersistentReuse._second())
    yield node


def _every_solved_node(tmp_path):
    yield _toy_run(tmp_path)
    yield _select_run(tmp_path)
    yield _two_name_run(tmp_path)
    yield from _persistent_runs(tmp_path)


def _active_rows(model):
    from pyomo.environ import Constraint

    return list(model.component_data_objects(Constraint, active=True, descend_into=True))


def _assert_same_record(fast, slow):
    """Every field equal; every component row equal, ``min_slack`` within 1e-9."""
    assert fast.to_obj().keys() == slow.to_obj().keys() == set(SolveRecord.field_names())
    for name in SolveRecord.field_names():
        if name != "binding":
            assert getattr(fast, name) == getattr(slow, name), name
    assert len(fast.binding) == len(slow.binding) > 0
    for a, b in zip(fast.binding, slow.binding):
        assert a.keys() == b.keys(), a["name"]
        for key in a:
            if key == "min_slack" and a[key] is not None and b[key] is not None:
                assert a[key] == pytest.approx(b[key], abs=1e-9), a["name"]
            else:
                assert a[key] == b[key], (a["name"], key)


def _both_records(node):
    """The record from the HiGHS arrays and the record from Pyomo, one solve."""
    solver, model, results = node.captured
    slacks = PyomoSolve._highs_row_slacks(solver, model)
    assert slacks is not None
    name = node.params.get("solver", DEFAULT_SOLVER)
    slow = SolveRecord.from_solve(model, results, name, 0.25)
    fast = SolveRecord.from_solve(model, results, name, 0.25, row_slacks=slacks)
    return model, slacks, fast, slow


class TestSolveRecordRowSlacks:
    """Row slacks read off the appsi HiGHS arrays (activity and row bounds)
    instead of walking every constraint body through Pyomo's evaluator.

    The fast path is an OPTIMIZATION: the record it builds must equal the
    Pyomo record on every real solve; every other solver, and every row the
    arrays did not answer, keeps the exact Pyomo evaluation.
    """

    def test_the_highs_array_record_equals_the_pyomo_record(self, tmp_path):
        seen = 0
        for node in _every_solved_node(tmp_path):
            model, slacks, fast, slow = _both_records(node)
            _assert_same_record(fast, slow)
            active = _active_rows(model)
            assert len(active) == slow.constraints and set(active) <= set(slacks)
            seen += 1
        assert seen == 5

    def test_every_bound_shape_and_the_duals_are_covered(self, tmp_path):
        model, slacks, fast, slow = _both_records(_toy_run(tmp_path))
        rows = _binding(fast)
        assert set(rows) == {
            "lower_only", "upper_only", "equality", "ranged", "literal_constant",
            "mutable_lower", "mutable_upper", "per_index",
        }
        # x = (2.5, 1.5): the sum row and the equality bind; a lower-only row
        # (2.5 - 0.5), a ranged row (1.5 to its lower bound) and a row with a
        # constant in its body (20 - 11.5) keep their own slack.
        assert rows["upper_only"]["binding"] == 1 and rows["equality"]["binding"] == 1
        assert rows["lower_only"]["min_slack"] == pytest.approx(2.0)
        assert rows["ranged"]["min_slack"] == pytest.approx(1.5)
        assert rows["literal_constant"]["min_slack"] == pytest.approx(8.5)
        assert rows["mutable_lower"]["min_slack"] == pytest.approx(3.25)
        assert rows["mutable_upper"]["min_slack"] == pytest.approx(5.5)
        assert rows["per_index"]["rows"] == 2 and rows["per_index"]["min_slack"] == pytest.approx(2.5)
        # The dual path is untouched: present, and equal row for row (_assert_same_record).
        assert any("dual" in row for row in fast.binding)
        _assert_same_record(fast, slow)

    def test_the_run_never_evaluates_a_row_through_pyomo_under_appsi_highs(
        self, tmp_path, monkeypatch
    ):
        def refuse(data):
            raise AssertionError(f"pyomo evaluated {data.name} though HiGHS answered it")

        monkeypatch.setattr(SolveRecord, "_row_slack", staticmethod(refuse))
        seen = 0
        for node in _every_solved_node(tmp_path):
            record = node.solve_record
            assert record is not None and record.termination == "optimal"
            assert record.constraints > 0
            assert any(row["min_slack"] is not None for row in record.binding)
            seen += 1
        assert seen == 5

    def test_no_row_slacks_is_the_pyomo_record(self, tmp_path):
        solver, model, results = _toy_run(tmp_path).captured
        default = SolveRecord.from_solve(model, results, DEFAULT_SOLVER, 0.25)
        assert SolveRecord.from_solve(
            model, results, DEFAULT_SOLVER, 0.25, row_slacks=None
        ) == default
        assert SolveRecord.from_solve(
            model, results, DEFAULT_SOLVER, 0.25, row_slacks={}
        ) == default

    def test_a_row_missing_from_the_mapping_falls_back_to_pyomo_per_row(
        self, tmp_path, monkeypatch
    ):
        node = _toy_run(tmp_path)
        model, slacks, _, slow = _both_records(node)
        results = node.captured[2]
        partial = {row: slack for row, slack in slacks.items()
                   if row is not model.upper_only and row is not model.per_index[2]}
        asked, real = [], SolveRecord._row_slack

        def spy(data):
            asked.append(data)
            return real(data)

        monkeypatch.setattr(SolveRecord, "_row_slack", staticmethod(spy))
        record = SolveRecord.from_solve(model, results, DEFAULT_SOLVER, 0.25, row_slacks=partial)
        assert sorted(row.name for row in asked) == ["per_index[2]", "upper_only"]
        _assert_same_record(record, slow)

    def test_a_mapped_row_is_used_as_given_and_none_means_no_slack(self, tmp_path, monkeypatch):
        solver, model, results = _toy_run(tmp_path).captured
        monkeypatch.setattr(SolveRecord, "_row_slack", staticmethod(lambda data: 1 / 0))
        every = {row: 5.0 for row in _active_rows(model)}
        every[model.upper_only] = 7.5  # differs from the truth (0.0): the mapping must win
        every[model.equality] = None  # no finite slack: recorded as none, never re-evaluated
        record = SolveRecord.from_solve(model, results, DEFAULT_SOLVER, 0.25, row_slacks=every)
        rows = _binding(record)
        assert rows["upper_only"]["min_slack"] == 7.5 and rows["upper_only"]["binding"] == 0
        assert rows["equality"]["rows"] == 1 and rows["equality"]["min_slack"] is None
        assert rows["equality"]["binding"] == 0
        assert rows["lower_only"]["min_slack"] == 5.0

    @pytest.mark.parametrize(
        "solver",
        [
            None,
            SimpleNamespace(),
            # every private attribute the reader wants, on something that is NOT appsi HiGHS
            SimpleNamespace(_solver_model=object(), _pyomo_con_to_solver_con_map={}, _model=None),
        ],
        ids=["none", "bare-object", "lookalike"],
    )
    def test_a_solver_that_is_not_appsi_highs_reads_nothing(self, tmp_path, solver):
        _, model, _ = _toy_run(tmp_path).captured
        assert PyomoSolve._highs_row_slacks(solver, model) is None

    def test_an_appsi_solver_missing_a_private_attribute_reads_nothing(self, tmp_path):
        for attribute in ("_solver_model", "_pyomo_con_to_solver_con_map"):
            solver, model, _ = _toy_run(tmp_path).captured
            assert PyomoSolve._highs_row_slacks(solver, model) is not None
            delattr(solver, attribute)
            assert PyomoSolve._highs_row_slacks(solver, model) is None

    def test_a_row_map_that_disagrees_with_the_arrays_reads_nothing(self, tmp_path):
        solver, model, _ = _toy_run(tmp_path).captured
        solver._pyomo_con_to_solver_con_map[object()] = 10_000  # a row HiGHS never held
        assert PyomoSolve._highs_row_slacks(solver, model) is None

    def test_a_solver_that_solved_another_model_reads_nothing(self, tmp_path):
        solver, _, _ = _toy_run(tmp_path).captured
        _, other, _ = _select_run(tmp_path).captured
        assert PyomoSolve._highs_row_slacks(solver, other) is None

    def test_a_solve_with_no_solution_reads_nothing(self):
        from pyomo.environ import ConcreteModel, Constraint, Objective, SolverFactory, Var

        model = ConcreteModel()
        model.x = Var(bounds=(0.0, 5.0))
        model.value = Objective(expr=model.x)
        model.impossible = Constraint(expr=model.x >= 10.0)
        solver = SolverFactory(DEFAULT_SOLVER)
        solver.options["threads"] = 1  # see SlackToyLP._solver_options
        with pytest.raises(RuntimeError):
            solver.solve(model)
        assert PyomoSolve._highs_row_slacks(solver, model) is None

    def test_another_highs_interface_keeps_the_pyomo_evaluation(self, tmp_path):
        node = CapturedToy("lp", {"solver": "highs"})
        node.run(_ctx(tmp_path), {})
        solver, model, _ = node.captured
        assert PyomoSolve._highs_row_slacks(solver, model) is None
        rows = _binding(node.solve_record)
        assert rows["equality"]["binding"] == 1 and rows["upper_only"]["binding"] == 1
        assert rows["lower_only"]["min_slack"] == pytest.approx(2.0)
