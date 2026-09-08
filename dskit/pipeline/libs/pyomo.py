"""The generic PyomoSolve doorway — tier-2 library pack (D-146, docs/25 §2).

One base class turns any pyomo program into a pipeline Node: subclass
:class:`PyomoSolve`, implement ``build_model(inputs, params)`` (return a
pyomo ``ConcreteModel``) and ``extract(model, results)`` (read the solved
model back into the node's named outputs), and the base owns the rest —
solver resolution, option pass-through, and the output-contract plumbing.
Any program, any solver. This closes I-225: the doorway lands INSIDE the
toolkit as a library pack, never as a sibling package.

What this is NOT: a specific optimizer. A concrete sizer such as a
fractional-Kelly MIO is venue-specific tier-3 machinery that talks to a
solver like highspy directly; this pack neither wraps nor replaces it — it
is only the generic doorway such a sizer COULD one day sit on.

Role doctrine
-------------
``role = "capital"`` on the base, because sizing cash is what a solve
node in a trading document usually is — and capital carries the
planner's protection (a ``stat_test`` survivors wire is REQUIRED, or the
document refuses to plan). A subclass doing non-capital optimization
(scheduling, assignment, feature selection) declares its own role as a
class attribute; the capital⇐stat_test gate then applies only where the
role stays ``capital``.

Output doctrine
---------------
``outputs`` stays ``None`` on the abstract base — the base cannot know a
program's output names. Every CONCRETE subclass MUST declare its own
``outputs`` tuple: an undeclared contract is a contract nothing can
check, the conformance suite refuses it, and the base's ``run()``
backstops the rule by refusing to solve for a subclass that skipped it.

Knob doctrine (the ``_PARAMS`` pattern)
---------------------------------------
The base owns ``solver`` (default ``"appsi_highs"``) and
``solver_options`` (a flat dict handed to the solver verbatim).
``validate_params`` default-denies everything else BY NAME. A subclass
extends the knob set by extending the tuple::

    _PARAMS = PyomoSolve._PARAMS + ("budget",)

and validating its own knobs on top of ``super().validate_params``.

Solver refusal happens at the earliest checkable instant: the NAME's
shape (a plausible solver identifier) is checked at PLAN, where no pyomo
import is legal; whether pyomo actually registers that name — and
whether the solver is available on this machine — is only knowable
against the installed pyomo, so those refuse at run, by name, before any
model is solved.

Import cost: stdlib + toolkit only. pyomo is imported strictly inside
run-path methods — this module must import (and its documents must
plan) on a machine with no pyomo installed.
"""

from __future__ import annotations

import math
import re
from abc import abstractmethod
from collections.abc import Mapping

from dskit.pipeline.node import DEFAULT_NODE_KINDS, Node, check_int_param
from dskit.pipeline.records import number_ok

__all__ = [
    "DEFAULT_N_SCENARIOS_MAX",
    "DEFAULT_N_TANGENTS",
    "DEFAULT_SOLVER",
    "HARD_N_SCENARIOS_CEILING",
    "NODE_KINDS",
    "BudgetedSelect",
    "PyomoSolve",
    "ScenarioUtilitySolve",
    "register",
    "tangent_utility",
]

#: The default solver name — HiGHS through pyomo's appsi interface
#: (highspy is a pip install away, needs no external binary).
DEFAULT_SOLVER = "appsi_highs"

#: What a solver NAME may look like — the only solver check that is
#: legal at PLAN, where importing pyomo is forbidden. Whether the name
#: is actually registered is checked at run, against the installed pyomo.
_SOLVER_NAME_OK = r"^[A-Za-z][A-Za-z0-9_.+-]*$"


def _is_scalar(value) -> bool:
    """A JSON-legal scalar a solver option may carry: null, bool, finite
    number, string."""
    if value is None or isinstance(value, (bool, str)):
        return True
    return isinstance(value, (int, float)) and math.isfinite(value)


#: Tangent knots over the reachable wealth interval — the measured
#: tractability default from docs/plans/2026-09-intraday-equities-mio.md
#: §4.2 (K=32 matched a K=256 reference on every seed, same names, at
#: 28% of the cost). A caller's own program may declare a different
#: ``n_tangents``; this is only the value used when it does not.
DEFAULT_N_TANGENTS = 32

#: The scenario-count default and hard ceiling (same source, §4.1/§4.4:
#: S=512 broke the measured tractability budget at 13.6s against a 10s
#: limit; S<=256 is the validated candidate ceiling). A subclass may
#: lower ``n_scenarios_max``; it may never raise it past this.
DEFAULT_N_SCENARIOS_MAX = 256
HARD_N_SCENARIOS_CEILING = 256


def tangent_utility(wealth, w0, gamma):
    """Evaluate concave utility and its derivative at given wealth levels.

    ``gamma <= 1`` is ``u(w) = ln(w / w0)`` (full-Kelly log utility);
    ``gamma > 1`` is the CRRA utility ``u(w) = ((w/w0)^(1-gamma) - 1) /
    (1-gamma)``, whose growth-optimal stake is the fractional-Kelly stake
    at fraction ``1/gamma``. Both are zero at ``w = w0``. This is the one
    home for the tangent-plane utility evaluator: :class:`ScenarioUtilitySolve`
    builds its objective's tangent rows from it, and ``pmquant.mio.utility_at``
    delegates to it under its own ``kelly_fraction`` parametrization
    (``gamma = 1/kelly_fraction``) rather than carrying a second copy.

    Parameters
    ----------
    wealth : sequence of float
        Positive wealth levels (a numpy array or anything it accepts).
    w0 : float
        The reference wealth mark, > 0.
    gamma : float
        Relative risk aversion, `<= 1`` reads as log utility.

    Returns
    -------
    tuple of numpy.ndarray
        ``(u, u_prime)`` at each wealth level.

    Examples
    --------
    Log utility at the reference wealth is zero::

        u, du = tangent_utility([100.0], 100.0, gamma=1.0)
        u[0]   # -> 0.0
    """
    import numpy as np

    w = np.asarray(wealth, dtype=float)
    w0f = float(w0)
    g = float(gamma)
    if g <= 1.0:
        return np.log(w / w0f), 1.0 / w
    ratio = w / w0f
    return (ratio ** (1.0 - g) - 1.0) / (1.0 - g), ratio ** (-g) / w0f


def _weighted_cvar(losses, weights, alpha):
    """Give the exact ``(cvar, eta)`` of a discrete weighted loss distribution.

    ``eta`` is the weighted ``alpha``-quantile of ``losses`` under
    ``weights`` — the Rockafellar-Uryasev minimizer in closed form for a
    finite scenario set — and ``cvar = eta + mean(max(loss - eta, 0)) /
    (1 - alpha)``. Used only for the POST-SOLVE exact recompute; the
    solver's own ``eta``/``z`` variables never feed a reported number.
    """
    import numpy as np

    order = np.argsort(losses)
    losses_sorted = losses[order]
    cum = np.cumsum(weights[order])
    idx = int(min(np.searchsorted(cum, alpha), len(losses_sorted) - 1))
    eta = float(losses_sorted[idx])
    tail = np.maximum(losses - eta, 0.0)
    cvar = eta + float(np.sum(weights * tail)) / (1.0 - alpha)
    return cvar, eta


class PyomoSolve(Node):
    """The abstract doorway: one pyomo program as one pipeline Node.

    Subclasses implement exactly two hooks:

    * :meth:`build_model` — inputs + params in, a pyomo ``ConcreteModel``
      out. Import pyomo INSIDE the hook (it is run-path by construction:
      the base only ever calls it from :meth:`run`).
    * :meth:`extract` — the solved model (and the solver results object)
      in, the node's named outputs out, matching the subclass's declared
      ``outputs`` contract exactly.

    The base's :meth:`run` is the lifecycle: refuse an undeclared output
    contract, build the model, resolve the solver (refusing an unknown
    or unavailable one by name), apply ``solver_options``, solve, and
    hand the results to ``extract``.

    This class is ABSTRACT on both hooks, so it can never register as a
    kind (``node_class_errors`` refuses abstract classes) and never
    appears in :data:`NODE_KINDS` — only concrete subclasses do.
    """

    #: Capital by default — see the module docstring's role doctrine. A
    #: non-capital subclass overrides this class attribute.
    role = "capital"

    #: Deliberately undeclared on the base; every concrete subclass MUST
    #: declare its own outputs tuple (run() refuses otherwise).
    outputs = None

    #: The base's own knobs. Subclasses EXTEND this tuple; validate_params
    #: default-denies anything outside it by name.
    _PARAMS = ("solver", "solver_options")

    @classmethod
    def validate_params(cls, params):
        problems = []
        unknown = sorted(set(params) - set(cls._PARAMS))
        if unknown:
            problems.append(
                f"unknown param(s) {unknown} — allowed: {sorted(cls._PARAMS)}"
            )
        solver = params.get("solver", DEFAULT_SOLVER)
        if not isinstance(solver, str) or not re.match(_SOLVER_NAME_OK, solver):
            problems.append(
                f"solver must be a solver-name string matching {_SOLVER_NAME_OK} "
                f"(e.g. {DEFAULT_SOLVER!r}), got {solver!r}"
            )
        options = params.get("solver_options", {})
        if not isinstance(options, dict):
            problems.append(
                f"solver_options must be a dict of option name -> scalar, "
                f"got {options!r}"
            )
        else:
            for key in sorted(options, key=repr):
                if not isinstance(key, str) or not key:
                    problems.append(
                        f"solver_options keys must be non-empty strings, got {key!r}"
                    )
                elif not _is_scalar(options[key]):
                    problems.append(
                        f"solver_options[{key!r}] must be a scalar "
                        f"(null/bool/finite number/string), got {options[key]!r}"
                    )
        return problems

    # -- the two subclass hooks --------------------------------------------

    @abstractmethod
    def build_model(self, inputs, params):
        """Return the pyomo ``ConcreteModel`` for this solve.

        ``inputs`` are the node's materialized upstream inputs; ``params``
        is ``self.params`` (passed explicitly so the hook is honest about
        what it depends on). Import pyomo inside the hook body.
        """
        raise NotImplementedError

    @abstractmethod
    def extract(self, model, results):
        """Read the SOLVED model back into the node's named outputs.

        ``results`` is whatever the solver's ``solve()`` returned (check
        the termination condition here — the base does not presume what a
        given program considers acceptable). Must return a dict matching
        the subclass's declared ``outputs`` exactly.
        """
        raise NotImplementedError

    # -- the base-owned lifecycle -------------------------------------------

    def run(self, ctx, inputs):
        declared = type(self).outputs
        if declared is None:
            raise TypeError(
                f"{type(self).__name__} declares no outputs contract — a "
                "concrete PyomoSolve subclass must declare `outputs = (...)` "
                "so its run() return is checkable (an undeclared contract is "
                "a contract nothing can check)"
            )
        model = self.build_model(inputs, self.params)
        solver = self._resolve_solver()
        self.log.info("solving with %r", self.params.get("solver", DEFAULT_SOLVER))
        results = solver.solve(model)
        extracted = self.extract(model, results)
        if not isinstance(extracted, dict):
            raise TypeError(
                f"{type(self).__name__}.extract() must return the node's named "
                f"outputs as a dict, got {type(extracted).__name__}"
            )
        return extracted

    def _solver_options(self):
        """The options :meth:`_resolve_solver` will apply, as one dict.

        The base hands the document's ``solver_options`` through
        VERBATIM — the generic doorway injects nothing. A subclass whose
        program needs pinned solver behavior (determinism, tolerances)
        overrides this to merge its defaults UNDER the document's own
        entries, so the document always wins per key
        (:class:`BudgetedSelect` is the worked example).
        """
        return dict(self.params.get("solver_options") or {})

    def _resolve_solver(self):
        """The named solver, options applied — or a refusal BY NAME.

        Run-path only: the solver universe lives inside the installed
        pyomo, so an unregistered or unavailable name is only checkable
        here. The plan-time half (name shape) already ran in
        ``validate_params``.
        """
        from pyomo.environ import SolverFactory
        from pyomo.opt.base.solvers import UnknownSolver

        name = self.params.get("solver", DEFAULT_SOLVER)
        solver = SolverFactory(name)
        if solver is None or isinstance(solver, UnknownSolver):
            raise ValueError(
                f"unknown solver {name!r} — the installed pyomo registers no "
                "solver under that name (only the name's SHAPE is checkable "
                "at plan time; the name itself resolves here)"
            )
        try:
            available = bool(solver.available(exception_flag=False))
        except TypeError:  # pragma: no cover - older interfaces lack the flag
            available = bool(solver.available())
        if not available:
            raise ValueError(
                f"solver {name!r} is registered but not available on this "
                "machine — install its backend or name an installed solver"
            )
        for key, value in self._solver_options().items():
            solver.options[key] = value
        return solver


class BudgetedSelect(PyomoSolve):
    """The concrete reference subclass: a 0/1 knapsack behind the gate.

    Inputs: ``candidates`` (a materialized list of ``{id, cost, value}``
    rows) and ``survivors`` (the stat_test survivors wire — ids). Only
    candidates whose ``id`` is IN ``survivors`` enter the model at all:
    the gate is honoured structurally, not decoratively. The solve picks
    the value-maximal subset of the eligible candidates whose total cost
    stays inside ``params.budget``.

    Two hard guarantees, both refusals by name:

    * an EMPTY gate deploys nothing and never wakes the solver — zero
      positions, zero outlay, straight return;
    * the reported ``outlay`` can never exceed ``budget`` — a post-solve
      assertion, because a budget that does not bind is the F-220 #1
      ghost (orders costing 19x the deployable, with full test coverage).

    And one softer guarantee, S1 #4: under the DEFAULT ``appsi_highs``
    the solve is pinned deterministic (:attr:`_HIGHS_DETERMINISM`) —
    see that attribute's note for why a knapsack, of all programs,
    needs it.
    """

    role = "capital"
    outputs = ("positions", "outlay", "metrics")

    _PARAMS = PyomoSolve._PARAMS + ("budget",)

    #: HiGHS determinism pins, injected UNDER the document's own options
    #: whenever the solver is the default ``appsi_highs``: zero MIP gap
    #: (the true optimum, not "close enough"), one thread and a fixed
    #: seed. A knapsack routinely has TIES — equal-value subsets at the
    #: budget — and gap tolerance, thread races and RNG jitter can each
    #: flip WHICH optimal vertex comes back, so an unpinned solve hands a
    #: gate-consumed selection that flaps machine to machine while both
    #: runs claim the same identity. The document's ``solver_options``
    #: override per key; any OTHER solver gets no injection at all — its
    #: option names differ, and a wrong name is rejected or (worse)
    #: silently ignored.
    _HIGHS_DETERMINISM = {"mip_rel_gap": 0, "threads": 1, "random_seed": 0}

    def _solver_options(self):
        options = super()._solver_options()
        if self.params.get("solver", DEFAULT_SOLVER) != DEFAULT_SOLVER:
            return options
        return {**self._HIGHS_DETERMINISM, **options}

    @classmethod
    def validate_params(cls, params):
        problems = super().validate_params(params)
        if "budget" not in params:
            problems.append(
                "budget is required — the spend ceiling the selection must "
                "stay inside (refused at plan, not after the solve)"
            )
        else:
            budget = params["budget"]
            if (
                isinstance(budget, bool)
                or not isinstance(budget, (int, float))
                or not math.isfinite(budget)
                or budget <= 0
            ):
                problems.append(f"budget must be a finite number > 0, got {budget!r}")
        return problems

    def validate_inputs(self, inputs):
        problems = []
        candidates = inputs.get("candidates")
        if not isinstance(candidates, (list, tuple)):
            problems.append(
                "candidates must be a materialized list of {id, cost, value} "
                f"rows, got {type(candidates).__name__} — a one-shot iterable "
                "is refused by name rather than walked (walking it here would "
                "hand run() an exhausted stream)"
            )
        else:
            seen = set()
            for i, row in enumerate(candidates):
                if not isinstance(row, Mapping):
                    problems.append(
                        f"candidates[{i}] must be a mapping with id/cost/value, "
                        f"got {row!r}"
                    )
                    continue
                cid = row.get("id")
                if not isinstance(cid, str) or not cid:
                    problems.append(
                        f"candidates[{i}].id must be a non-empty string, got {cid!r}"
                    )
                elif cid in seen:
                    problems.append(
                        f"candidates[{i}].id {cid!r} is a duplicate — one row "
                        "per id, or the model's variables collide"
                    )
                else:
                    seen.add(cid)
                cost = row.get("cost")
                if (
                    isinstance(cost, bool)
                    or not isinstance(cost, (int, float))
                    or not math.isfinite(cost)
                    or cost < 0
                ):
                    problems.append(
                        f"candidates[{i}].cost must be a finite number >= 0, "
                        f"got {cost!r}"
                    )
                value = row.get("value")
                if (
                    isinstance(value, bool)
                    or not isinstance(value, (int, float))
                    or not math.isfinite(value)
                ):
                    problems.append(
                        f"candidates[{i}].value must be a finite number, got {value!r}"
                    )
        survivors = inputs.get("survivors")
        if not isinstance(survivors, (list, tuple, set, frozenset)):
            problems.append(
                "survivors must be a materialized collection of ids (the "
                f"stat_test survivors wire), got {type(survivors).__name__} — "
                "a one-shot iterable is refused by name rather than walked"
            )
        else:
            for s in survivors:
                if not isinstance(s, str):
                    problems.append(f"survivors entries must be strings, got {s!r}")
        return problems

    def run(self, ctx, inputs):
        budget = float(self.params["budget"])
        survivors = set(inputs["survivors"])
        eligible = [row for row in inputs["candidates"] if row["id"] in survivors]
        if not eligible:
            # The gate cleared no one: deploy NOTHING, and never wake the
            # solver — an empty program solved anyway is capital treating
            # its gate as decoration.
            self.log.info(
                "gate cleared none of %d candidate(s) — zero outlay, "
                "solver not invoked",
                len(inputs["candidates"]),
            )
            return {
                "positions": [],
                "outlay": 0.0,
                "metrics": {
                    "objective": 0.0,
                    "n_selected": 0,
                    "n_eligible": 0,
                    "n_candidates": len(inputs["candidates"]),
                },
            }
        out = super().run(ctx, inputs)
        outlay = float(out["outlay"])
        if outlay > budget * (1.0 + 1e-9):
            raise AssertionError(
                f"budget violated: outlay {outlay!r} exceeds budget "
                f"{budget!r} — refusing to report an over-budget selection "
                "(a budget that does not bind is the F-220 #1 ghost)"
            )
        return out

    def build_model(self, inputs, params):
        from pyomo.environ import (
            Binary,
            ConcreteModel,
            Constraint,
            Objective,
            Var,
            maximize,
        )

        survivors = set(inputs["survivors"])
        rows = [row for row in inputs["candidates"] if row["id"] in survivors]
        ids = [row["id"] for row in rows]
        cost = {row["id"]: float(row["cost"]) for row in rows}
        value = {row["id"]: float(row["value"]) for row in rows}

        model = ConcreteModel(name="budgeted-select")
        model.x = Var(ids, domain=Binary)
        model.total_value = Objective(
            expr=sum(value[i] * model.x[i] for i in ids), sense=maximize
        )
        model.budget = Constraint(
            expr=sum(cost[i] * model.x[i] for i in ids) <= float(params["budget"])
        )
        # Underscore-prefixed: plain bookkeeping for extract(), invisible
        # to pyomo's component machinery.
        model._select = {
            "ids": ids,
            "cost": cost,
            "value": value,
            "n_candidates": len(inputs["candidates"]),
        }
        return model

    def extract(self, model, results):
        condition = str(
            getattr(getattr(results, "solver", None), "termination_condition", None)
        )
        if condition != "optimal":
            raise RuntimeError(
                f"solver finished with termination condition {condition!r}, "
                "not 'optimal' — refusing to read a selection off a "
                "non-optimal solve"
            )
        meta = model._select
        chosen = sorted(cid for cid in meta["ids"] if (model.x[cid].value or 0.0) > 0.5)
        return {
            "positions": chosen,
            "outlay": float(sum(meta["cost"][cid] for cid in chosen)),
            "metrics": {
                "objective": float(sum(meta["value"][cid] for cid in chosen)),
                "n_selected": len(chosen),
                "n_eligible": len(meta["ids"]),
                "n_candidates": meta["n_candidates"],
            },
        }


class ScenarioUtilitySolve(PyomoSolve):
    """A per-tick fractional-Kelly MILP over joint scenario returns (role ``capital``).

    Sizes a PORTFOLIO of named instruments at one decision tick: how many
    of each to hold, funded by buys and sells from current inventory,
    maximizing expected tangent-plane-approximated concave utility of
    terminal wealth subject to cardinality, a minimum ticket size, gross
    exposure, self-financing cash, buying power, and an optional
    Rockafellar-Uryasev CVaR cap. It names no market: the same class
    could size an ad-spend allocation or a project portfolio exactly as
    well as an equity book (docs/plans/2026-09-intraday-equities-mio.md
    §6 — the tier-2/tier-3 split this class embodies).

    Three hooks stay abstract; a concrete subclass supplies all three:

    * :meth:`instruments` — ``(names, rows, account)``. ``names`` is the
      union of currently-held and newly-eligible instrument ids; ``rows``
      is ``name -> {"price", "held", "x_max", "cost_buy", "cost_sell"}``,
      optionally ``"exit_cost_per_share"`` (default 0); ``account`` is
      ``{"cash", "buying_power", "wealth_lo", "wealth_hi", "sale_credit"}``,
      optionally ``"cash_reserve"`` (default 0) and ``"gross_limit"``
      (default ``None`` — unconstrained). ``names`` empty (no eligible
      candidate AND nothing held) is the ONLY case that skips the solver.
    * :meth:`payoffs` — ``(weights, r)``: scenario weights (summing to
      one) and ``name -> (n_omega,) scenario gross-return array``,
      ALREADY carrying any belief haircut or recentering the caller's
      own domain requires (a false-signal or parameter-uncertainty
      correction on ``mu`` is domain policy, not this class's job).
    * :meth:`domain_constraints` — add the caller's own rows to the
      model in place (an HFDR cap, a no-trade band, an opportunity-cost
      charge): nothing here, because those ARE domain policy.

    What this class owns, and a subclass never restates: inventory
    transition (``q = held + buy - sell``, long-only, ONE direction per
    name per tick — buying and selling the same name in the same solve is
    structurally impossible, not merely discouraged, so a subclass's own
    trade-size floor can never be defeated by a wash trade), self-financing
    cash and buying-power inequalities, cardinality and ``min_ticket``
    eligibility gating (the ``min_ticket`` floor binds a tick that BUYS,
    never a pre-existing position a subclass's own document never asked to
    touch — "do nothing" is always feasible regardless of a position's
    size relative to the current ``min_ticket``), the tangent-plane utility
    objective (built from :func:`tangent_utility`), the CVaR block, the
    empty-gate short circuit, and a post-solve EXACT recompute of every
    reported number (never the solver's own variable values) that raises
    on any violation or on a non-``optimal`` termination.

    Every reported cost coefficient (``cost_buy``/``cost_sell``/
    ``exit_cost_per_share``) is a flat, ALREADY-COMPUTED dollars-per-share
    figure — venue fee schedules, spread assumptions and per-order caps
    are the subclass's domain, resolved once in :meth:`instruments`
    before this class ever sees a number.

    Examples
    --------
    A concrete two-name subclass appears in the test suite
    (``tests/pipeline_libs/test_pyomo.py::TestScenarioUtilitySolve``); see
    ``EquityKellyMIO`` in ``children/intraday_equities`` for a worked
    real subclass.
    """

    role = "capital"
    outputs = None

    _PARAMS = PyomoSolve._PARAMS + (
        "risk_aversion_gamma",
        "n_tangents",
        "n_scenarios_max",
        "cvar_alpha",
        "cvar_limit",
        "cardinality",
        "min_ticket",
    )

    #: HiGHS determinism pins (the ``BudgetedSelect`` precedent) — a
    #: portfolio MILP over integer shares routinely ties (two names at
    #: identical marginal utility), so gap tolerance, thread races and
    #: RNG jitter would each flip which optimal vertex comes back.
    _HIGHS_DETERMINISM = {"mip_rel_gap": 0, "threads": 1, "random_seed": 0}

    #: Set by :meth:`run` for the duration of one solve; ``None`` otherwise.
    _scn = None

    def _solver_options(self):
        options = super()._solver_options()
        if self.params.get("solver", DEFAULT_SOLVER) != DEFAULT_SOLVER:
            return options
        return {**self._HIGHS_DETERMINISM, **options}

    @classmethod
    def validate_params(cls, params):
        problems = super().validate_params(params)
        if "risk_aversion_gamma" not in params:
            problems.append(
                "risk_aversion_gamma is required — the CRRA risk-aversion "
                "coefficient (1 = full-Kelly log utility) is an owner risk "
                "decision, there is no default"
            )
        elif not number_ok(params["risk_aversion_gamma"]) or params["risk_aversion_gamma"] < 1.0:
            problems.append(
                f"risk_aversion_gamma must be a finite number >= 1, got "
                f"{params['risk_aversion_gamma']!r}"
            )
        if "n_tangents" in params:
            check_int_param(problems, "n_tangents", params["n_tangents"], ge=2)
        if "n_scenarios_max" in params:
            check_int_param(problems, "n_scenarios_max", params["n_scenarios_max"], ge=2)
            n_max = params["n_scenarios_max"]
            if number_ok(n_max) and n_max > HARD_N_SCENARIOS_CEILING:
                problems.append(
                    f"n_scenarios_max must be <= {HARD_N_SCENARIOS_CEILING} (the measured "
                    f"tractability ceiling, docs/plans/2026-09-intraday-equities-mio.md §4.1) "
                    f"— a wider scenario set needs a re-measured envelope before it is trusted, "
                    f"got {n_max!r}"
                )
        if "cvar_alpha" not in params:
            problems.append(
                "cvar_alpha is required — the CVaR confidence level is an owner risk "
                "decision, there is no default"
            )
        elif not number_ok(params["cvar_alpha"]) or not 0.0 < params["cvar_alpha"] < 1.0:
            problems.append(
                f"cvar_alpha must be a finite number in (0, 1), got {params['cvar_alpha']!r}"
            )
        if "cvar_limit" not in params:
            problems.append(
                "cvar_limit is required — declare a finite dollar cap, or null for "
                "explicitly unconstrained (an owner decision either way, never a silent "
                "default)"
            )
        elif params["cvar_limit"] is not None and (
            not number_ok(params["cvar_limit"]) or params["cvar_limit"] <= 0.0
        ):
            problems.append(
                f"cvar_limit must be a finite number > 0, or null, got {params['cvar_limit']!r}"
            )
        if "cardinality" not in params:
            problems.append(
                "cardinality is required — the maximum number of names held at once is an "
                "owner risk decision, there is no default"
            )
        else:
            check_int_param(problems, "cardinality", params["cardinality"], ge=1)
        if "min_ticket" not in params:
            problems.append(
                "min_ticket is required — the smallest dollar position size is an owner "
                "risk decision, there is no default"
            )
        elif not number_ok(params["min_ticket"]) or params["min_ticket"] < 0.0:
            problems.append(
                f"min_ticket must be a finite number >= 0, got {params['min_ticket']!r}"
            )
        return problems

    # -- the three subclass hooks -------------------------------------------

    @abstractmethod
    def instruments(self, inputs):
        """Return ``(names, rows, account)`` — see the class docstring.

        ``names`` empty is the empty-gate signal: :meth:`run` returns a
        zero result without calling :meth:`payoffs`, :meth:`build_model`
        or the solver.
        """
        raise NotImplementedError

    @abstractmethod
    def payoffs(self, inputs):
        """Return ``(weights, r)`` — see the class docstring."""
        raise NotImplementedError

    @abstractmethod
    def domain_constraints(self, model, inputs, params):
        """Add the caller's own rows to ``model`` IN PLACE; return nothing.

        Called once, from inside :meth:`build_model`, after every
        base-owned variable, expression and constraint exists — so a
        domain row may reference ``model.b[name]``/``model.s[name]``
        (integer buy/sell shares), ``model.q[name]``/``model.x[name]``
        (target shares / target notional, as pyomo ``Expression``
        objects), ``model.y[name]`` (binary held-at-all indicator),
        ``model.d[name]`` (binary trade direction — 1 buys this tick, 0
        sells or holds; ``b[name]`` and ``s[name]`` can never both be
        positive for the same name in the same tick, by construction),
        ``model.W[o]`` (scenario wealth) and ``model.cash_after``.
        ``model._scn["names"]`` carries the instrument order.
        """
        raise NotImplementedError

    # -- the base-owned lifecycle --------------------------------------------

    def run(self, ctx, inputs):
        names, rows, account = self.instruments(inputs)
        if not names:
            self.log.info(
                "%s: no eligible or held instrument — zero target, solver not invoked", self.key
            )
            cash = float(account.get("cash", 0.0)) if account else 0.0
            return {
                "target": {},
                "trades": {},
                "cash_after": cash,
                "metrics": {
                    "objective": 0.0,
                    "expected_utility": 0.0,
                    "n_held": 0,
                    "n_traded": 0,
                    "gross_exposure": 0.0,
                    "cash_after": cash,
                    "cvar": 0.0,
                    "cvar_eta": 0.0,
                    "wealth_min": cash,
                    "wealth_max": cash,
                },
            }
        self._scn = {"names": list(names), "rows": rows, "account": account}
        try:
            return super().run(ctx, inputs)
        finally:
            self._scn = None

    def build_model(self, inputs, params):
        import numpy as np
        from pyomo.environ import (
            Binary,
            ConcreteModel,
            Constraint,
            Expression,
            NonNegativeIntegers,
            NonNegativeReals,
            Objective,
            Reals,
            Var,
            maximize,
        )

        state = self._scn
        if state is None:
            raise RuntimeError(
                f"{self.key}: build_model is driven by run(), which resolves instruments() "
                "and the empty-gate check first — no current state is set"
            )
        names, rows, account = state["names"], state["rows"], state["account"]
        weights, r = self.payoffs(inputs)
        weights = np.asarray(weights, dtype=float)
        if weights.ndim != 1 or weights.size == 0:
            raise ValueError(f"{self.key}: payoffs() weights must be a non-empty 1-d vector")
        if not np.all(np.isfinite(weights)) or bool(np.any(weights < 0.0)):
            raise ValueError(f"{self.key}: payoffs() weights must be finite and >= 0")
        if abs(float(weights.sum()) - 1.0) > 1e-8:
            raise ValueError(
                f"{self.key}: payoffs() weights must sum to 1, got {float(weights.sum())!r}"
            )
        n_omega = int(weights.shape[0])
        n_scenarios_max = int(params.get("n_scenarios_max", DEFAULT_N_SCENARIOS_MAX))
        if n_omega > n_scenarios_max:
            raise ValueError(
                f"{self.key}: payoffs() carries {n_omega} scenarios, exceeding the "
                f"declared n_scenarios_max={n_scenarios_max}"
            )
        if set(r) != set(names):
            raise ValueError(
                f"{self.key}: payoffs() names {sorted(r)} do not match instruments() names "
                f"{sorted(names)}"
            )
        r = {i: np.asarray(r[i], dtype=float) for i in names}
        for i in names:
            if r[i].shape != (n_omega,):
                raise ValueError(
                    f"{self.key}: payoffs()[{i!r}] has shape {r[i].shape}, expected "
                    f"({n_omega},)"
                )
            if not np.all(np.isfinite(r[i])):
                raise ValueError(
                    f"{self.key}: payoffs()[{i!r}] must be all finite numbers, got {r[i]!r}"
                )

        for key in ("wealth_lo", "wealth_hi", "cash", "buying_power", "sale_credit"):
            if key not in account or not number_ok(account[key]):
                raise ValueError(
                    f"{self.key}: account[{key!r}] must be a finite number, got "
                    f"{account.get(key)!r}"
                )
        if "cash_reserve" in account and not number_ok(account["cash_reserve"]):
            raise ValueError(
                f"{self.key}: account['cash_reserve'] must be a finite number when given, "
                f"got {account['cash_reserve']!r}"
            )
        if account.get("gross_limit") is not None and not number_ok(account["gross_limit"]):
            raise ValueError(
                f"{self.key}: account['gross_limit'] must be a finite number or null "
                f"(unconstrained), got {account['gross_limit']!r}"
            )
        w_lo, w_hi = float(account["wealth_lo"]), float(account["wealth_hi"])
        if not w_lo < w_hi:
            raise ValueError(
                f"{self.key}: account wealth_lo {w_lo!r} must be < wealth_hi {w_hi!r}"
            )
        cash0 = float(account["cash"])
        buying_power0 = float(account["buying_power"])
        sale_credit = float(account["sale_credit"])
        cash_reserve = float(account.get("cash_reserve", 0.0))
        gross_limit = account.get("gross_limit")
        w0_mark = cash0 + sum(float(rows[i]["price"]) * float(rows[i]["held"]) for i in names)

        gamma = float(params["risk_aversion_gamma"])
        n_tangents = int(params.get("n_tangents", DEFAULT_N_TANGENTS))
        cardinality = int(params["cardinality"])
        min_ticket = float(params["min_ticket"])
        cvar_alpha = float(params["cvar_alpha"])
        cvar_limit = params["cvar_limit"]

        omega_ix = list(range(n_omega))

        # A safe (generous, never-binding-early) upper bound on shares
        # BOUGHT this tick: the post-trade target is capped at x_max_i by
        # elig_hi, so q_i (and thus b_i, since s_i >= 0) can never usefully
        # exceed x_max_i/price_i + held_i regardless of what d_i does.
        buy_room = {
            i: int(float(rows[i]["x_max"]) / float(rows[i]["price"])) + int(rows[i]["held"]) + 1
            if float(rows[i]["price"]) > 0
            else int(rows[i]["held"]) + 1
            for i in names
        }

        model = ConcreteModel(name="scenario-utility-solve")
        model.b = Var(names, domain=NonNegativeIntegers)
        model.s = Var(
            names, domain=NonNegativeIntegers,
            bounds=lambda m, i: (0, int(rows[i]["held"])),
        )
        model.y = Var(names, domain=Binary)
        # Direction: 1 buys this tick, 0 sells (or holds). Two jobs, one
        # binary: (a) makes a same-tick buy+sell of one name STRUCTURALLY
        # impossible — a "wash trade" that pads gross trade size to clear
        # a no-trade band's floor while the NET position barely moves is
        # otherwise legal MILP behavior, not a caller bug; (b) lets
        # elig_lo apply the min_ticket floor only to a tick that actually
        # BUYS, so a pre-existing position smaller than a later-declared
        # min_ticket can be left untouched (d_i=0, b_i=s_i=0 stays
        # feasible) rather than forced to trade — the base's own
        # min_ticket gate must never be the thing that makes "do nothing"
        # infeasible for a name a caller never asked to touch.
        model.d = Var(names, domain=Binary)
        model.W = Var(omega_ix, bounds=(w_lo, w_hi))
        model.t = Var(omega_ix, domain=Reals)
        model.eta = Var(domain=Reals)
        model.z = Var(omega_ix, domain=NonNegativeReals)

        model.q = Expression(
            names, rule=lambda m, i: float(rows[i]["held"]) + m.b[i] - m.s[i]
        )
        model.x = Expression(names, rule=lambda m, i: float(rows[i]["price"]) * m.q[i])

        model.nonneg_q = Constraint(names, rule=lambda m, i: m.q[i] >= 0)
        model.buy_only = Constraint(
            names, rule=lambda m, i: m.b[i] <= buy_room[i] * m.d[i]
        )
        model.sell_only = Constraint(
            names, rule=lambda m, i: m.s[i] <= int(rows[i]["held"]) * (1 - m.d[i])
        )
        model.elig_hi = Constraint(
            names, rule=lambda m, i: m.x[i] <= float(rows[i]["x_max"]) * m.y[i]
        )
        model.elig_lo = Constraint(
            names, rule=lambda m, i: m.x[i] >= min_ticket * m.d[i]
        )
        model.cardinality = Constraint(expr=sum(model.y[i] for i in names) <= cardinality)

        model.cash_after = Expression(
            expr=cash0
            + sum(
                (float(rows[i]["price"]) - float(rows[i]["cost_sell"])) * model.s[i]
                - (float(rows[i]["price"]) + float(rows[i]["cost_buy"])) * model.b[i]
                for i in names
            )
        )
        model.cash_floor = Constraint(expr=model.cash_after >= cash_reserve)
        model.buying_power = Constraint(
            expr=sum(
                (float(rows[i]["price"]) + float(rows[i]["cost_buy"])) * model.b[i] for i in names
            )
            <= buying_power0
            + sale_credit
            * sum(
                (float(rows[i]["price"]) - float(rows[i]["cost_sell"])) * model.s[i]
                for i in names
            )
        )
        if gross_limit is not None:
            model.gross_exposure = Constraint(
                expr=sum(model.x[i] for i in names) <= float(gross_limit)
            )

        def _wealth_rule(m, o):
            return m.W[o] == m.cash_after + sum(
                m.q[i]
                * (
                    float(rows[i]["price"]) * (1.0 + float(r[i][o]))
                    - float(rows[i].get("exit_cost_per_share", 0.0))
                )
                for i in names
            )

        model.wealth = Constraint(omega_ix, rule=_wealth_rule)

        knots = np.linspace(w_lo, w_hi, n_tangents)
        u, du = tangent_utility(knots, w0_mark, gamma)
        tangent_ix = [(o, j) for o in omega_ix for j in range(n_tangents)]
        model.tangent = Constraint(
            tangent_ix,
            rule=lambda m, o, j: m.t[o]
            <= float(u[j]) + float(du[j]) * (m.W[o] - float(knots[j])),
        )

        model.cvar_row = Constraint(
            omega_ix, rule=lambda m, o: m.z[o] >= (w0_mark - m.W[o]) - m.eta
        )
        if cvar_limit is not None:
            model.cvar_cap = Constraint(
                expr=model.eta
                + (1.0 / (1.0 - cvar_alpha)) * sum(float(weights[o]) * model.z[o] for o in omega_ix)
                <= float(cvar_limit)
            )

        model.objective = Objective(
            expr=sum(float(weights[o]) * model.t[o] for o in omega_ix), sense=maximize
        )

        # Underscore-prefixed: plain bookkeeping for extract(), invisible
        # to pyomo's component machinery (the BudgetedSelect precedent).
        # Set BEFORE domain_constraints() runs, so a subclass's hook can
        # read model._scn (e.g. to size a big-M from rows/x_max) exactly
        # as extract() later does.
        model._scn = {
            "names": names,
            "rows": rows,
            "weights": weights,
            "r": r,
            "w_lo": w_lo,
            "w_hi": w_hi,
            "w0_mark": w0_mark,
            "cash0": cash0,
            "buying_power0": buying_power0,
            "sale_credit": sale_credit,
            "cash_reserve": cash_reserve,
            "gross_limit": None if gross_limit is None else float(gross_limit),
            "cardinality": cardinality,
            "min_ticket": min_ticket,
            "cvar_alpha": cvar_alpha,
            "cvar_limit": cvar_limit,
            "gamma": gamma,
        }

        self.domain_constraints(model, inputs, params)
        return model

    def extract(self, model, results):
        """Read the solved model and recompute every reported number exactly.

        Two different kinds of check live here, and a reviewer should not
        expect the same independence from both. Cardinality, ``min_ticket``
        and CVaR are recomputed by a GENUINELY DIFFERENT method than the
        model encodes (counting the target dict; a closed-form weighted
        quantile via :func:`_weighted_cvar` rather than reading the
        solver's own ``eta``/``z``), so each can catch a conceptual error
        in its own constraint rows. Cash, buying power, gross exposure and
        scenario wealth are DEFINITIONAL identities — self-financing cash
        after trades, notional times target shares — so recomputing them
        here re-derives the same arithmetic from the solved integer
        variables rather than from the model's own continuous relaxation;
        what that catches is the solver reporting a value inconsistent
        with its own INTEGER solution (rounding, a stale ``.value``,
        infeasible float dust), not a shared conceptual error in those
        rows' formulas — there is no second, independently-derivable
        formula for what cash after a set of trades IS.
        """
        import numpy as np

        condition = str(
            getattr(getattr(results, "solver", None), "termination_condition", None)
        )
        if condition != "optimal":
            raise RuntimeError(
                f"{self.key}: solver finished with termination condition {condition!r}, "
                "not 'optimal' — refusing to read a target off a non-optimal solve (a "
                "time-limit hit is a halt, not a degraded fill)"
            )
        meta = model._scn
        names, rows = meta["names"], meta["rows"]

        b, s = {}, {}
        for i in names:
            bv = float(model.b[i].value or 0.0)
            sv = float(model.s[i].value or 0.0)
            bi, si = int(round(bv)), int(round(sv))
            if abs(bv - bi) > 1e-6 or abs(sv - si) > 1e-6:
                raise AssertionError(
                    f"{self.key}: {i}: integer variable came back fractional "
                    f"(buy={bv!r}, sell={sv!r})"
                )
            b[i], s[i] = bi, si

        target, trades = {}, {}
        cash_after = meta["cash0"]
        for i in names:
            q = int(rows[i]["held"]) + b[i] - s[i]
            if q < 0:
                raise AssertionError(f"{self.key}: {i}: target shares negative ({q})")
            if q:
                target[i] = q
            if b[i] or s[i]:
                trades[i] = {"buy": b[i], "sell": s[i]}
            cash_after += (float(rows[i]["price"]) - float(rows[i]["cost_sell"])) * s[i]
            cash_after -= (float(rows[i]["price"]) + float(rows[i]["cost_buy"])) * b[i]

        if cash_after < meta["cash_reserve"] - 1e-6:
            raise AssertionError(
                f"{self.key}: cash reserve violated on exact recompute: {cash_after!r} < "
                f"{meta['cash_reserve']!r}"
            )
        buy_notional = sum(
            (float(rows[i]["price"]) + float(rows[i]["cost_buy"])) * b[i] for i in names
        )
        sell_credit = meta["sale_credit"] * sum(
            (float(rows[i]["price"]) - float(rows[i]["cost_sell"])) * s[i] for i in names
        )
        if buy_notional > meta["buying_power0"] + sell_credit + 1e-6:
            raise AssertionError(
                f"{self.key}: buying power violated on exact recompute: buy notional "
                f"{buy_notional!r} exceeds {meta['buying_power0']!r} + sale credit "
                f"{sell_credit!r}"
            )
        gross = sum(float(rows[i]["price"]) * target.get(i, 0) for i in names)
        if meta["gross_limit"] is not None and gross > meta["gross_limit"] + 1e-6:
            raise AssertionError(
                f"{self.key}: gross exposure violated on exact recompute: {gross!r} exceeds "
                f"{meta['gross_limit']!r}"
            )
        if len(target) > meta["cardinality"]:
            raise AssertionError(
                f"{self.key}: cardinality violated on exact recompute: {len(target)} held "
                f"exceeds {meta['cardinality']!r}"
            )
        for i, q in target.items():
            if b[i] == 0:
                # min_ticket binds a tick that BUYS (elig_lo is
                # min_ticket * d_i in build_model) — a pre-existing
                # position left untouched, or only ever sold down, is
                # exempt by design, not a gap in this check.
                continue
            notional = float(rows[i]["price"]) * q
            if notional < meta["min_ticket"] - 1e-6:
                raise AssertionError(
                    f"{self.key}: {i}: {notional!r} is below min_ticket {meta['min_ticket']!r} "
                    "on exact recompute"
                )

        weights, r = meta["weights"], meta["r"]
        n_omega = len(weights)
        wealth = np.array(
            [
                cash_after
                + sum(
                    target.get(i, 0)
                    * (
                        float(rows[i]["price"]) * (1.0 + float(r[i][o]))
                        - float(rows[i].get("exit_cost_per_share", 0.0))
                    )
                    for i in names
                )
                for o in range(n_omega)
            ],
            dtype=float,
        )
        if not bool(np.all(wealth > 0.0)):
            raise AssertionError(
                f"{self.key}: insolvent in some scenario on exact recompute: {wealth!r}"
            )

        u, _du = tangent_utility(wealth, meta["w0_mark"], meta["gamma"])
        expected_utility = float(np.sum(weights * u))
        losses = meta["w0_mark"] - wealth
        cvar, cvar_eta = _weighted_cvar(losses, weights, meta["cvar_alpha"])
        if meta["cvar_limit"] is not None and cvar > float(meta["cvar_limit"]) + 1e-6:
            raise AssertionError(
                f"{self.key}: CVaR violated on exact recompute: {cvar!r} exceeds "
                f"{meta['cvar_limit']!r}"
            )

        objective = model.objective()
        return {
            "target": target,
            "trades": trades,
            "cash_after": float(cash_after),
            "metrics": {
                "objective": float(objective) if objective is not None else 0.0,
                "expected_utility": expected_utility,
                "n_held": len(target),
                "n_traded": len(trades),
                "gross_exposure": float(gross),
                "cash_after": float(cash_after),
                "cvar": cvar,
                "cvar_eta": cvar_eta,
                "wealth_min": float(wealth.min()),
                "wealth_max": float(wealth.max()),
            },
        }


#: The pack's kinds — CONCRETE classes only. The abstract base never
#: registers (node_class_errors refuses abstract classes); documents
#: subclassing PyomoSolve themselves need no registration at all
#: ("uses": "their.module:TheirSolve" resolves directly).
NODE_KINDS = (("pyomo-budgeted-select", BudgetedSelect),)


def register(registry=None) -> None:
    """Claim the pack's kind names in ``registry`` (default
    :data:`~dskit.pipeline.node.DEFAULT_NODE_KINDS`).

    Deliberate and explicit, never an import side effect (pack
    convention: importing the toolkit — or this module — must not
    mutate any registry). Idempotent: a name already present is
    SKIPPED, never shadowed.
    """
    registry = DEFAULT_NODE_KINDS if registry is None else registry
    for name, cls in NODE_KINDS:
        if name not in registry:
            registry.register(name, cls)
