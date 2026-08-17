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

from dskit.pipeline.node import DEFAULT_NODE_KINDS, Node

__all__ = ["DEFAULT_SOLVER", "NODE_KINDS", "BudgetedSelect", "PyomoSolve", "register"]

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
