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

Solve record doctrine
---------------------
Every solve the base lifecycle runs leaves a :class:`SolveRecord` on
``PyomoSolve.solve_record`` (ADR-0183 phase 2): status, termination,
objective, bound, relative gap, wall seconds, model size and one binding
row per constraint component. It is read AFTER the solve, off the model
and the results, so no subclass writes a line for it; ``None`` means
this instance has not solved (or took a no-solve short circuit).

Row slacks are the one expensive part: walking every constraint body
through pyomo's evaluator costs ~25 us a row, about a fifth of a
joint-MIO decision. Under the ``appsi_highs`` interface HiGHS already
holds each row's activity and bounds (appsi moves a body's constant into
the bounds), so :meth:`PyomoSolve._highs_row_slacks` reads the slack
straight off those arrays and hands it to ``SolveRecord.from_solve`` as
``row_slacks``. It is an optimization only: it answers ``None`` for any
other solver, interface version or solve state, and a row it did not
answer is evaluated by pyomo exactly as before, so the record is the same
either way (pinned in ``TestSolveRecordRowSlacks``).

Persistent-model update doctrine
--------------------------------
:class:`ScenarioUtilitySolve` can keep one appsi HiGHS model per algebraic
shape and only refresh mutable ``Param`` values each tick. appsi then
re-evaluates every mutable coefficient and bound through pyomo's expression
visitor (~4.5 us each, ~22k per 12-name joint tick, about all of the
~100 ms its ``update_params`` costs; the highspy calls are ~20 ms).
:class:`_CompiledParamUpdate` compiles each of those expressions ONCE into a
closure that does the same float arithmetic and replays the same highspy
calls in appsi's order, so the HiGHS model is bit-identical (pinned in
``TestCompiledParamUpdate``). It is an optimization only: any node, helper
or appsi layout it cannot prove identical is left to appsi's own code.

Import cost: stdlib + toolkit only. pyomo is imported strictly inside
run-path methods — this module must import (and its documents must
plan) on a machine with no pyomo installed.
"""

from __future__ import annotations

import gc
import math
import re
import struct
import time
from abc import ABC, abstractmethod
from collections import OrderedDict
from collections.abc import Mapping
from dataclasses import dataclass, fields
from itertools import chain

from dskit.pipeline.node import DEFAULT_NODE_KINDS, Node, check_int_param
from dskit.pipeline.records import number_ok

__all__ = [
    "BINDING_TOLERANCE",
    "DEFAULT_N_SCENARIOS_MAX",
    "DEFAULT_N_TANGENTS",
    "DEFAULT_SOLVER",
    "HARD_N_SCENARIOS_CEILING",
    "NODE_KINDS",
    "BudgetedSelect",
    "PyomoSolve",
    "ScenarioUtilitySolve",
    "SolveRecord",
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


#: A constraint row BINDS when its slack is within this of zero — the
#: feasibility tolerance HiGHS itself applies by default (1e-7) with an
#: order of headroom, so a row the solver left exactly tight is never
#: reported slack over float noise.
BINDING_TOLERANCE = 1e-6

#: The smallest objective magnitude the relative gap divides by, so an
#: optimum at zero reports a finite gap rather than dividing by zero.
_GAP_FLOOR = 1e-10


def _finite(value):
    """Coerce a pyomo numeric (bound, value, dual) to a float by ``records.number_ok``, else None."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number_ok(number) else None


@dataclass(frozen=True)
class SolveRecord:
    """What one pyomo solve did: outcome, bounds, time, size and binding rows.

    Built by :meth:`from_solve` inside :meth:`PyomoSolve.run` for every
    subclass; :meth:`to_obj` is the JSON body an evaluation ``solve``
    event carries (the key set is pinned to that event's fields by a
    test).

    Parameters
    ----------
    solver : str
        The solver name the node resolved.
    status : str
        The solver status (``ok``, ``warning``, ``aborted``, ...).
    termination : str
        The termination condition (``optimal``, ``maxTimeLimit``, ...).
    objective : float or None
        The active objective's value at the loaded solution.
    bound : float or None
        The solver's best bound on the objective (upper when maximizing,
        lower when minimizing); None when it reported no finite bound.
    gap : float or None
        ``|objective - bound| / |objective|`` (the divisor floored at a
        tiny positive value); None when either is missing.
    seconds : float
        Wall time of ``solver.solve`` (``time.perf_counter``).
    variables, constraints : int
        Variable and active constraint rows in the model.
    binding : tuple of dict
        One row per active constraint COMPONENT: ``name``, ``rows``,
        ``binding`` (rows with ``|slack| <= BINDING_TOLERANCE``),
        ``min_slack`` (None when no row could be evaluated) and, only
        when the model carries an imported ``dual`` Suffix with values,
        ``dual`` (the component's largest-magnitude row dual).

    Examples
    --------
    ::

        record = SolveRecord("appsi_highs", "ok", "optimal", 12.0, 12.0, 0.0, 0.01,
                             3, 1, ({"name": "budget", "rows": 1, "binding": 1,
                                     "min_slack": 0.0},))
        record.to_obj()["termination"]  # 'optimal'
    """

    solver: str
    status: str
    termination: str
    objective: object
    bound: object
    gap: object
    seconds: float
    variables: int
    constraints: int
    binding: tuple

    @classmethod
    def field_names(cls):
        """Return the record's field names, in :meth:`to_obj` order.

        Returns
        -------
        tuple of str
        """
        return tuple(f.name for f in fields(cls))

    @classmethod
    def from_solve(cls, model, results, solver, seconds, tolerance=BINDING_TOLERANCE,
                   row_slacks=None):
        """Read a finished solve off its model and results.

        Parameters
        ----------
        model : pyomo.environ.ConcreteModel
            The solved model (a solution loaded, or none).
        results : object
            What ``solver.solve(model)`` returned.
        solver : str
            The solver name.
        seconds : float
            Wall time of the solve.
        tolerance : float
            The binding tolerance on a row's slack.
        row_slacks : mapping or None
            Optional ``{constraint row: slack}`` the caller already read
            (``PyomoSolve._highs_row_slacks``). A row in it is taken as
            given, ``None`` meaning "no finite slack"; a row absent from it
            is evaluated through pyomo. ``None`` (the default) evaluates
            every row through pyomo, byte for byte as before.

        Returns
        -------
        SolveRecord
        """
        from pyomo.environ import Constraint, Var

        objective, sense = cls._objective(model)
        bound = cls._bound(results, sense)
        gap = (None if objective is None or bound is None
               else abs(objective - bound) / max(abs(objective), _GAP_FLOOR))
        duals = cls._duals(model)
        binding = tuple(
            cls._component_row(component, duals, tolerance, row_slacks)
            for component in model.component_objects(Constraint, active=True,
                                                     descend_into=True)
        )
        outcome = getattr(results, "solver", None)
        return cls(
            solver=str(solver),
            status=str(getattr(outcome, "status", None) or "unknown"),
            termination=str(getattr(outcome, "termination_condition", None) or "unknown"),
            objective=objective,
            bound=bound,
            gap=gap,
            seconds=float(seconds),
            variables=sum(1 for _ in model.component_data_objects(Var, descend_into=True)),
            constraints=sum(row["rows"] for row in binding),
            binding=binding,
        )

    @staticmethod
    def _objective(model):
        """The single active objective's value (or None) and its sense (1 min, -1 max)."""
        from pyomo.environ import Objective, value

        active = list(model.component_data_objects(Objective, active=True, descend_into=True))
        if len(active) != 1:
            return None, None
        return _finite(value(active[0], exception=False)), int(active[0].sense)

    @staticmethod
    def _bound(results, sense):
        """The results' finite bound on the objective's side, or None."""
        problem = getattr(results, "problem", None)
        try:
            problem = problem[0]
        except (TypeError, IndexError, KeyError):
            pass
        if problem is None or sense is None:
            return None
        return _finite(getattr(problem, "upper_bound" if sense < 0 else "lower_bound", None))

    @staticmethod
    def _duals(model):
        """The imported ``dual`` Suffix as ``{row: value}``, or None when absent or empty."""
        from pyomo.environ import Suffix

        suffix = model.component("dual")
        if not isinstance(suffix, Suffix) or not suffix.import_enabled() or not len(suffix):
            return None
        return dict(suffix.items())

    @classmethod
    def _component_row(cls, component, duals, tolerance, row_slacks=None):
        """One constraint component's binding row; a row in ``row_slacks`` is not re-evaluated."""
        slacks, row_duals, rows = [], [], 0
        for data in component.values():
            if not data.active:
                continue
            rows += 1
            if row_slacks is not None and data in row_slacks:
                slack = row_slacks[data]
            else:
                slack = cls._row_slack(data)
            if slack is not None:
                slacks.append(slack)
            if duals is not None and _finite(duals.get(data)) is not None:
                row_duals.append(float(duals[data]))
        out = {
            "name": component.name,
            "rows": rows,
            "binding": sum(1 for s in slacks if abs(s) <= tolerance),
            "min_slack": min(slacks) if slacks else None,
        }
        if row_duals:
            out["dual"] = max(row_duals, key=abs)
        return out

    def to_obj(self):
        """Return the record as a JSON-ready dict over :meth:`field_names`.

        Returns
        -------
        dict
        """
        obj = {name: getattr(self, name) for name in self.field_names()}
        obj["binding"] = [dict(row) for row in self.binding]
        return obj

    @staticmethod
    def _row_slack(data):
        """A constraint row's slack to its nearest bound, or None when unevaluable."""
        from pyomo.environ import value

        body = _finite(value(data.body, exception=False))
        if body is None:
            return None
        gaps = []
        if data.has_lb():
            gaps.append(body - float(value(data.lower)))
        if data.has_ub():
            gaps.append(float(value(data.upper)) - body)
        return min(gaps) if gaps else None


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

    #: The :class:`SolveRecord` of this instance's last solve, set by
    #: :meth:`run` between ``solve`` and ``extract`` (so ``extract`` may
    #: read it, and a non-optimal solve ``extract`` refuses is still
    #: recorded). ``None`` before a solve, when the solve itself raised,
    #: and after a subclass's no-solve short circuit.
    solve_record = None

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
        self.solve_record = None
        model = self.build_model(inputs, self.params)
        solver = self._resolve_solver()
        name = self.params.get("solver", DEFAULT_SOLVER)
        self.log.info("solving with %r", name)
        started = time.perf_counter()
        results = self._solve(solver, model)
        seconds = time.perf_counter() - started
        self.solve_record = self._build_solve_record(solver, model, results, name, seconds)
        extracted = self.extract(model, results)
        if not isinstance(extracted, dict):
            raise TypeError(
                f"{type(self).__name__}.extract() must return the node's named "
                f"outputs as a dict, got {type(extracted).__name__}"
            )
        return extracted

    @staticmethod
    def _build_solve_record(solver, model, results, name, seconds):
        """The SolveRecord of a finished solve, its row slacks read off HiGHS when it can answer."""
        return SolveRecord.from_solve(
            model, results, name, seconds,
            row_slacks=PyomoSolve._highs_row_slacks(solver, model),
        )

    @staticmethod
    def _highs_row_arrays(solver, model):
        """Return ``(activity, lower, upper, rows)`` off an appsi HiGHS solve of ``model``, or None."""
        try:
            from pyomo.contrib.appsi.solvers.highs import Highs
        except ImportError:
            return None
        # `_model` guards against a solver that last solved some OTHER model.
        if not isinstance(solver, Highs) or getattr(solver, "_model", None) is not model:
            return None
        try:
            import numpy as np

            highs, rows = solver._solver_model, solver._pyomo_con_to_solver_con_map
            solution = highs.getSolution()
            if not solution.value_valid:
                return None
            lp = highs.getLp()
            activity, lower, upper = (
                np.asarray(a, dtype=float)
                for a in (solution.row_value, lp.row_lower_, lp.row_upper_)
            )
        except (AttributeError, TypeError, ValueError):
            return None  # another pyomo/highspy layout: keep the exact pyomo evaluation
        if not (activity.ndim == 1 and activity.shape == lower.shape == upper.shape
                and activity.shape[0] == len(rows)):
            return None  # the arrays and appsi's row map disagree: trust neither
        return activity, lower, upper, rows

    @staticmethod
    def _highs_row_slacks(solver, model):
        """Return ``{constraint row: slack}`` read off an appsi HiGHS solve, or None to use pyomo."""
        arrays = PyomoSolve._highs_row_arrays(solver, model)
        if arrays is None:
            return None
        import numpy as np

        activity, lower, upper, rows = arrays
        # A side with no finite bound gives +inf, like pyomo's has_lb()/has_ub() == False.
        with np.errstate(invalid="ignore"):
            slack = np.minimum(
                np.where(np.isfinite(lower), activity - lower, np.inf),
                np.where(np.isfinite(upper), upper - activity, np.inf),
            ).tolist()
        return {row: (slack[i] if math.isfinite(slack[i]) else None) for row, i in rows.items()}

    def _solve(self, solver, model):
        """Run ``solver`` on ``model`` and return its results.

        The generic base adds nothing. A subclass that must name its own
        refusal when the solver RAISES instead of returning a termination
        condition overrides this (:class:`ScenarioUtilitySolve`).
        """
        return solver.solve(model)

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
            self.solve_record = None
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


def _same_number(a, b):
    """True when ``a`` and ``b`` are one number: same type, same bits (so 0.0 is not -0.0)."""
    if type(a) is not type(b):
        return False
    if isinstance(a, int):
        return a == b
    return struct.pack("<d", a) == struct.pack("<d", b)


def _reader(compiled):
    """A compiled number as a zero-argument callable; a compiled callable as itself."""
    return compiled if callable(compiled) else (lambda: compiled)


class _ExpressionCompiler:
    """Compile a pyomo NPV expression into a zero-argument callable equal to ``value(expr)``.

    The callable applies the same floating-point operations to the same
    operands in the same order as pyomo's evaluator, so its result has the
    same type and bits. Interior nodes call their own ``_apply_operation``;
    only a product and a negation, whose operator is fixed by their class,
    skip the list that call takes. Leaves are a native ``float``/``int``, an
    exact ``ParamData`` (its stored ``_value``) or a ``ScalarParam``
    (``value``). Any other node or leaf compiles to ``None``. Handling one
    more node class is one more entry in ``_handlers``.

    Examples
    --------
    Compile ``3 * p`` and read it after the mutable ``p`` changes::

        compiler = _ExpressionCompiler()
        read = compiler.compile(3 * model.p)
        model.p.set_value(2.5)
        read()  # -> 7.5
    """

    def __init__(self):
        from pyomo.core.base.param import ParamData, ScalarParam
        from pyomo.core.expr import numeric_expr as nodes
        from pyomo.core.expr.numvalue import value

        self._value = value
        self._param = ParamData
        self._handlers = {
            float: self._number,
            int: self._number,
            ParamData: self._param_leaf,
            ScalarParam: self._scalar_leaf,
            nodes.NPV_NegationExpression: self._negation,
            nodes.NPV_ProductExpression: self._product,
            nodes.NPV_SumExpression: self._operation,
            nodes.NPV_DivisionExpression: self._operation,
            nodes.NPV_PowExpression: self._operation,
            nodes.NPV_MaxExpression: self._operation,
            nodes.NPV_MinExpression: self._operation,
        }

    def compile(self, expr):
        """Return ``expr`` as a number, a zero-argument callable, or None when unhandled.

        Parameters
        ----------
        expr : pyomo expression, number
            The expression to compile; only its exact class is looked up.

        Returns
        -------
        number, callable or None
        """
        handler = self._handlers.get(type(expr))
        return None if handler is None else handler(expr)

    @staticmethod
    def _number(expr):
        return expr

    @staticmethod
    def _param_leaf(expr):
        return lambda: expr._value

    def _scalar_leaf(self, expr):
        value = self._value
        return lambda: value(expr)

    def _children(self, expr):
        """The compiled arguments of ``expr``, or None when any is unhandled."""
        kids = [self.compile(arg) for arg in expr.args]
        return None if any(kid is None for kid in kids) else kids

    def _operation(self, expr):
        kids = self._children(expr)
        if kids is None:
            return None
        apply = expr._apply_operation
        reads = [_reader(kid) for kid in kids]
        return lambda: apply([read() for read in reads])

    def _negation(self, expr):
        kids = self._children(expr)
        if kids is None:
            return None
        (arg,) = expr.args
        if type(arg) is self._param:
            return lambda: -arg._value
        read = _reader(kids[0])
        return lambda: -read()

    def _product(self, expr):
        kids = self._children(expr)
        if kids is None:
            return None
        left, right = expr.args
        if type(right) is self._param and type(left) in (float, int):
            return lambda: left * right._value
        if type(left) is self._param and type(right) in (float, int):
            return lambda: left._value * right
        if type(left) is self._param and type(right) is self._param:
            return lambda: left._value * right._value
        read_left, read_right = (_reader(kid) for kid in kids)
        return lambda: read_left() * read_right()


class _Step(ABC):
    """One appsi mutable helper as a precompiled highspy call.

    A concrete step names the appsi helper class it replaces (``helper_name``)
    and the helper attributes holding the expressions it needs
    (``expressions``); :meth:`apply` makes that helper's ``update`` call.
    """

    __slots__ = ()
    helper_name = None
    expressions = ()

    @abstractmethod
    def apply(self):
        """Make the highspy call the appsi helper's ``update`` would make."""


class _FallbackStep(_Step):
    """A helper that keeps its own ``update`` (a class or expression this module does not compile)."""

    __slots__ = ("helper",)

    def __init__(self, helper):
        self.helper = helper

    def apply(self):
        self.helper.update()


class _CoefficientStep(_Step):
    """appsi ``_MutableLinearCoefficient.update``: ``changeCoeff(row, col, value)``."""

    __slots__ = ("change", "con_map", "con", "var_map", "var_id", "value")
    helper_name = "_MutableLinearCoefficient"
    expressions = ("expr",)

    def __init__(self, helper, programs):
        self.change = helper.highs.changeCoeff
        self.con_map, self.con = helper.con_map, helper.pyomo_con
        self.var_map, self.var_id = helper.var_map, helper.pyomo_var_id
        self.value = _reader(programs[0])

    def apply(self):
        self.change(self.con_map[self.con], self.var_map[self.var_id], self.value())


class _RowBoundsStep(_Step):
    """appsi ``_MutableConstraintBounds.update``: ``changeRowBounds(row, lower, upper)``."""

    __slots__ = ("change", "con_map", "con", "lower", "upper")
    helper_name = "_MutableConstraintBounds"
    expressions = ("lower_expr", "upper_expr")

    def __init__(self, helper, programs):
        self.change = helper.highs.changeRowBounds
        self.con_map, self.con = helper.con_map, helper.con
        self.lower, self.upper = programs

    def apply(self):
        lower, upper = self.lower, self.upper
        self.change(
            self.con_map[self.con],
            lower() if callable(lower) else lower,
            upper() if callable(upper) else upper,
        )


class _ColumnBoundsStep(_Step):
    """appsi ``_MutableVarBounds.update``: ``changeColBounds(col, lower, upper)``."""

    __slots__ = ("change", "var_map", "var_id", "lower", "upper")
    helper_name = "_MutableVarBounds"
    expressions = ("lower_expr", "upper_expr")

    def __init__(self, helper, programs):
        self.change = helper.highs.changeColBounds
        self.var_map, self.var_id = helper.var_map, helper.pyomo_var_id
        self.lower, self.upper = programs

    def apply(self):
        lower, upper = self.lower, self.upper
        self.change(
            self.var_map[self.var_id],
            lower() if callable(lower) else lower,
            upper() if callable(upper) else upper,
        )


class _CostStep(_Step):
    """appsi ``_MutableObjectiveCoefficient.update``: ``changeColCost(col, value)``."""

    __slots__ = ("change", "var_map", "var_id", "value")
    helper_name = "_MutableObjectiveCoefficient"
    expressions = ("expr",)

    def __init__(self, helper, programs):
        self.change = helper.highs.changeColCost
        self.var_map, self.var_id = helper.var_map, helper.pyomo_var_id
        self.value = _reader(programs[0])

    def apply(self):
        self.change(self.var_map[self.var_id], self.value())


class _OffsetStep(_Step):
    """appsi ``_MutableObjectiveOffset.update``: ``changeObjectiveOffset(value)``."""

    __slots__ = ("change", "value")
    helper_name = "_MutableObjectiveOffset"
    expressions = ("expr",)

    def __init__(self, helper, programs):
        self.change = helper.highs.changeObjectiveOffset
        self.value = _reader(programs[0])

    def apply(self):
        self.change(self.value())


class _CompiledParamUpdate:
    """A compiled replacement for appsi ``Highs.update_params`` on one persistent solver.

    :meth:`attach` reads the solver's mutable-helper registry once, compiles
    every helper into a step (see :class:`_ExpressionCompiler`), verifies that
    each compiled value equals pyomo's own ``value(expr)`` bit for bit on the
    current parameters, and switches appsi's ``update_params`` off.
    :meth:`apply` then replays appsi's loop: the same bookkeeping
    (``_sol = None``, the last solution loader invalidated), then the same
    highspy calls in the same order, row and column indices looked up at call
    time as appsi does. It must run after the parameters are refreshed and
    before ``solver.solve``; appsi's structure scans stay on.

    A helper of another class, one bound to another HiGHS object, or with a
    node that does not compile or verify keeps its own ``update`` (counted in
    :attr:`n_fallback`). A solver whose registry is not the known layout is
    never attached (appsi keeps its own path). If the registry's helpers are
    no longer the ones compiled (appsi rebuilt a constraint), :meth:`apply`
    retires the updater and appsi's own ``update_params`` is switched back on.

    Parameters
    ----------
    solver : appsi Highs
        The persistent solver, its model already loaded.
    steps : list
        One step per helper, in appsi's call order.
    snapshot : tuple
        The registries' helpers at compile time, checked on every apply.

    Examples
    --------
    Attach to a solver whose model was solved once, refresh, then apply::

        updater = _CompiledParamUpdate.attach(solver)
        model.input_price["AAA"].set_value(101.5)
        updater.apply()  # -> True; solver.solve(model) now skips update_params
    """

    #: The compiler, built on first use so this module imports without pyomo.
    _compiler = None

    #: appsi's helper classes by name; ``helper_name`` on each step.
    _STEPS = {
        step.helper_name: step
        for step in (_CoefficientStep, _RowBoundsStep, _ColumnBoundsStep, _CostStep, _OffsetStep)
    }

    #: The module those helper classes live in (a same-named class elsewhere is not one).
    _HELPER_MODULE = "pyomo.contrib.appsi.solvers.highs"

    def __init__(self, solver, steps, snapshot):
        self._solver = solver
        self._steps = steps
        self._snapshot = snapshot
        self._highs = solver._solver_model
        self._retired = False
        self.n_fallback = sum(1 for step in steps if type(step) is _FallbackStep)
        self.n_compiled = len(steps) - self.n_fallback

    @property
    def retired(self):
        """Whether appsi's own ``update_params`` has been given back its job."""
        return self._retired

    @classmethod
    def program(cls, expr):
        """Return ``expr`` compiled: a number, a zero-argument callable, or None when unhandled.

        Parameters
        ----------
        expr : pyomo expression, number
            A helper's expression.

        Returns
        -------
        number, callable or None
        """
        if cls._compiler is None:
            cls._compiler = _ExpressionCompiler()
        return cls._compiler.compile(expr)

    @staticmethod
    def _registries(solver):
        """Return ``(constraint helpers, variable-bound helpers, objective helpers)`` as lists, or None."""
        try:
            registry, bounds = solver._mutable_helpers, solver._mutable_bounds
            objective = solver._objective_helpers
            if type(registry) is not dict or type(bounds) is not dict or type(objective) is not list:
                return None
            return (
                list(chain.from_iterable(registry.values())),
                [helper for _, helper in bounds.values()],
                list(objective),
            )
        except (AttributeError, TypeError, ValueError):
            return None

    @classmethod
    def attach(cls, solver):
        """Compile ``solver``'s mutable helpers and switch its ``update_params`` off.

        Parameters
        ----------
        solver : appsi Highs
            A persistent solver whose model has been loaded.

        Returns
        -------
        _CompiledParamUpdate or None
            None when the solver is not the known appsi layout; appsi's
            ``update_params`` is then untouched.
        """
        snapshot = cls._registries(solver)
        try:
            config = solver.update_config
            known = (
                snapshot is not None
                and solver._solver_model is not None
                and config.update_params is True
                and hasattr(solver, "_sol")
                and hasattr(solver, "_last_results_object")
            )
        except AttributeError:
            return None
        if not known:
            return None
        highs = solver._solver_model
        # ~100k closures are built and kept alive here; the cyclic collector
        # would re-walk the whole pyomo model for each young generation.
        collecting = gc.isenabled()
        gc.disable()
        try:
            steps = [cls._step(helper, highs) for helper in chain.from_iterable(snapshot)]
        finally:
            if collecting:
                gc.enable()
        updater = cls(solver, steps, snapshot)
        config.update_params = False
        return updater

    @classmethod
    def _step(cls, helper, highs):
        """The step for one helper: compiled when every expression verifies, else its own ``update``."""
        step = cls._STEPS.get(type(helper).__name__)
        if step is None or type(helper).__module__ != cls._HELPER_MODULE:
            return _FallbackStep(helper)
        try:
            if helper.highs is not highs:
                return _FallbackStep(helper)
            programs = [cls._verified(getattr(helper, name)) for name in step.expressions]
            if any(program is None for program in programs):
                return _FallbackStep(helper)
            return step(helper, programs)
        except Exception:  # whatever a helper does, appsi's own update stays the answer
            return _FallbackStep(helper)

    @classmethod
    def _verified(cls, expr):
        """``program(expr)`` when it equals pyomo's ``value(expr)`` now, bit for bit; else None."""
        from pyomo.core.expr.numvalue import value

        program = cls.program(expr)
        if program is None:
            return None
        got = program() if callable(program) else program
        return program if _same_number(got, value(expr)) else None

    def apply(self):
        """Replace appsi's ``update_params`` for one tick.

        Returns
        -------
        bool
            True when the compiled calls ran. False when the updater is
            retired (appsi's ``update_params`` is on and runs itself).
        """
        if self._retired:
            return False
        if not self._current():
            self.retire()
            return False
        solver = self._solver
        solver._sol = None
        last = solver._last_results_object
        if last is not None:
            last.solution_loader.invalidate()
        for step in self._steps:
            step.apply()
        return True

    def _current(self):
        """Whether the solver still holds exactly the helpers this updater compiled."""
        solver = self._solver
        try:
            return solver._solver_model is self._highs and self._registries(solver) == self._snapshot
        except AttributeError:
            return False

    def retire(self):
        """Give appsi's own ``update_params`` its job back, for good."""
        self._retired = True
        self._steps = ()
        self._solver.update_config.update_params = True


class ScenarioUtilitySolve(PyomoSolve):
    """A per-tick fractional-Kelly MILP over joint scenario returns (role ``capital``).

    Sizes a PORTFOLIO of named instruments at one decision tick: how many
    of each to hold, funded by buys and sells from current inventory,
    maximizing expected tangent-plane-approximated concave utility of
    terminal wealth subject to an optional cardinality cap (``null`` =
    explicitly unconstrained, like ``cvar_limit``), a minimum ticket size, gross
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
      optionally ``"cash_reserve"`` (default 0), ``"gross_limit"``
      (default ``None`` — unconstrained) and ``"carried_wealth"`` (default
      0; finite, ``>= 0``): wealth the caller holds OUTSIDE this solve
      (positions it is not sizing this tick). It is a constant in the
      wealth mark and in every scenario wealth, never in cash, so it can
      neither be spent nor fund a buy. ``names`` empty (no eligible
      candidate AND nothing held) is the ONLY case that skips the solver.
    * :meth:`payoffs` — ``(weights, r)``: scenario weights (summing to
      one) and ``name -> (n_omega,) scenario gross-return array``,
      ALREADY carrying any belief haircut or recentering the caller's
      own domain requires (a false-signal or parameter-uncertainty
      correction on ``mu`` is domain policy, not this class's job). A
      name may instead carry a ``(n_tranches, n_omega)`` matrix: one
      scenario row per EXIT HORIZON (ADR-0188 formulation B). The
      doorway then splits that name's target shares across tranches with
      continuous ``e[name, k]`` variables summing to ``q[name]``, values
      tranche ``k`` at its own row, and reports the split in
      ``metrics["tranches"]``. A flat vector is the one-tranche case and
      builds exactly today's rows.
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

    #: Persistent reuse is deliberately opt-in: an arbitrary subclass's
    #: domain rows may bake input values that the generic doorway cannot
    #: know how to refresh. Opting in accepts the build/refresh/signature
    #: contract below. Four shapes covers the common full-book/pending-name
    #: alternation without retaining an unbounded family of solver models.
    _PERSISTENT_MODEL_REUSE = False
    _PERSISTENT_CACHE_SIZE = 4

    #: On a cached hit, replace appsi's ``update_params`` (the pyomo
    #: evaluator over every mutable coefficient and bound) with
    #: :class:`_CompiledParamUpdate`: bit-identical HiGHS model, a fraction
    #: of the time. Compiled on a shape's FIRST hit, so a shape seen once
    #: pays nothing. ``False`` keeps appsi's own path (the tests' reference).
    _PERSISTENT_COMPILED_UPDATE = True

    def _solver_options(self):
        options = super()._solver_options()
        if self.params.get("solver", DEFAULT_SOLVER) != DEFAULT_SOLVER:
            return options
        return {**self._HIGHS_DETERMINISM, **options}

    def _solve(self, solver, model, *, warmstart=False):
        """Solve; a solve that returns no loadable solution refuses by name.

        A declared solver time limit is a HALT, never a degraded fill
        (plan §4.4). Under appsi_highs the limit is HiGHS's own
        ``time_limit`` option, declared as ``solver_options: {"time_limit":
        seconds}`` and applied verbatim. A halt that found an incumbent
        returns ``maxTimeLimit``, which :meth:`extract` refuses by name. A
        halt before any incumbent, like an infeasible program, makes appsi
        raise ``RuntimeError`` from ``solve`` itself; that is re-raised
        here naming this node and keeping the solver's own words, so the
        caller's refusal record says what happened. Nothing is sized
        either way, and :attr:`solve_record` stays ``None`` for a solve
        that raised.

        Raises
        ------
        RuntimeError
            The solver returned no loadable solution.
        """
        try:
            if warmstart:
                return solver.solve(model, warmstart=True)
            return solver.solve(model)
        except RuntimeError as exc:
            raise RuntimeError(
                f"{self.key}: the solver returned no loadable solution (an infeasible "
                "program, or a time limit reached before any incumbent) — refusing: no "
                f"target is read and nothing trades ({exc})"
            ) from exc

    def _persistent_params(self):
        """Model params for the persistent path; subclasses may add fixed doorway pins."""
        return self.params

    def _persistent_domain_signature(self, inputs, prepared, params):
        """Hashable shape identity for an opting-in subclass's own rows."""
        del inputs, prepared, params
        return ()

    def _refresh_domain_constraints(self, model, inputs, params):
        """Refresh mutable coefficients owned by an opting-in subclass."""
        del model, inputs, params

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
                "cardinality is required — declare the maximum number of names held at "
                "once, or null for explicitly unconstrained (an owner risk decision either "
                "way, there is no default)"
            )
        elif params["cardinality"] is not None:
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

    def mean_uncertainty(self, inputs):
        """Return an optional name-budgeted mean-error set.

        The default is ``None`` and preserves the nominal model exactly. An
        opting-in subclass returns ``budget`` plus ``deviation_below`` and
        ``deviation_above`` mappings. Each mapping may cover a subset of the
        live instrument names (certain mandatory exits stay absent), and each
        value is one non-negative return deviation per payoff tranche. The
        budget is spent over NAMES: every horizon of one name moves together.
        """
        del inputs
        return None

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
            self.solve_record = None
            self.log.info(
                "%s: no eligible or held instrument — zero target, solver not invoked", self.key
            )
            cash = float(account.get("cash", 0.0)) if account else 0.0
            # The wealth held outside this solve is wealth on the empty gate
            # too (skeptic round 1 on ADR-0188 slice 1: the sibling return
            # path had dropped it), validated exactly as build_model does.
            carried = account.get("carried_wealth", 0.0) if account else 0.0
            if not number_ok(carried) or carried < 0.0:
                raise ValueError(
                    f"{self.key}: account['carried_wealth'] must be a finite number >= 0 "
                    f"when given, got {carried!r} — it is wealth held outside this solve, "
                    "never cash"
                )
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
                    "wealth_min": cash + float(carried),
                    "wealth_max": cash + float(carried),
                    "tranches": {},
                },
            }
        self._scn = {"names": list(names), "rows": rows, "account": account}
        try:
            if self._PERSISTENT_MODEL_REUSE:
                return self._run_persistent(ctx, inputs)
            return super().run(ctx, inputs)
        finally:
            self._scn = None

    def _run_persistent(self, ctx, inputs):
        """Reuse one model/solver per algebraic shape, with a safe cold fallback."""
        del ctx
        declared = type(self).outputs
        if declared is None:
            raise TypeError(
                f"{type(self).__name__} declares no outputs contract — a concrete "
                "PyomoSolve subclass must declare `outputs = (...)` so its run() "
                "return is checkable (an undeclared contract is a contract nothing "
                "can check)"
            )
        params = self._persistent_params()
        prepared = self._prepare_model(inputs, params)
        signature = self._persistent_signature(inputs, prepared, params)
        cache = getattr(self, "_persistent_cache", None)
        if cache is None:
            cache = self._persistent_cache = OrderedDict()
        entry = cache.pop(signature, None)
        if entry is None:
            return self._cold_persistent_attempt(inputs, params, prepared, signature, cache)

        cache[signature] = entry
        self.solve_record = None
        try:
            self._refresh_model(entry["model"], inputs, params, prepared)
            self._apply_compiled_update(entry)
            return self._solve_and_extract(
                entry["solver"], entry["model"], warmstart=True
            )
        except Exception:
            # A persistent-interface miss can surface as a solve error,
            # non-optimal result, OR an exact-extraction failure. None is a
            # refusal until the same current inputs fail on a new cold model.
            cache.pop(signature, None)
            return self._cold_persistent_attempt(inputs, params, prepared, signature, cache)

    def _apply_compiled_update(self, entry):
        """Run the entry's compiled parameter update, compiling it on the entry's first hit.

        Parameters
        ----------
        entry : dict
            A persistent cache entry (``model``, ``solver``; ``updater`` once
            compiled, None when the solver's layout is not the known one).
        """
        if not self._PERSISTENT_COMPILED_UPDATE:
            return
        if "updater" not in entry:
            entry["updater"] = _CompiledParamUpdate.attach(entry["solver"])
        if entry["updater"] is not None:
            entry["updater"].apply()

    def _cold_persistent_attempt(self, inputs, params, prepared, signature, cache):
        """Build and solve once; cache only a fully extracted optimal result."""
        self.solve_record = None
        self._prepared_for_build = prepared
        try:
            model = self.build_model(inputs, params)
        finally:
            self._prepared_for_build = None
        solver = self._resolve_solver()
        out = self._solve_and_extract(solver, model, warmstart=False)
        if self._solver_supports_reuse(solver):
            cache[signature] = {"model": model, "solver": solver}
            cache.move_to_end(signature)
            while len(cache) > self._PERSISTENT_CACHE_SIZE:
                cache.popitem(last=False)
        return out

    def _solve_and_extract(self, solver, model, *, warmstart):
        """One timed solve plus the ordinary SolveRecord/output contract."""
        name = self.params.get("solver", DEFAULT_SOLVER)
        self.log.info("solving with %r%s", name, " (warm start)" if warmstart else "")
        started = time.perf_counter()
        results = self._solve(solver, model, warmstart=warmstart)
        seconds = time.perf_counter() - started
        self.solve_record = self._build_solve_record(solver, model, results, name, seconds)
        extracted = self.extract(model, results)
        if not isinstance(extracted, dict):
            raise TypeError(
                f"{type(self).__name__}.extract() must return the node's named "
                f"outputs as a dict, got {type(extracted).__name__}"
            )
        return extracted

    @staticmethod
    def _solver_supports_reuse(solver):
        """Whether this resolved interface promises both required contracts."""
        try:
            return bool(solver.is_persistent() and solver.warm_start_capable())
        except (AttributeError, TypeError):
            return False

    def _persistent_signature(self, inputs, prepared, params):
        """The exact algebraic shape and every base coefficient baked as a constant."""
        mean = prepared["mean_uncertainty"]
        domain = self._persistent_domain_signature(inputs, prepared, params)
        signature = (
            tuple(prepared["names"]),
            len(prepared["weights"]),
            tuple((name, prepared["tranches"][name]) for name in prepared["names"]),
            () if mean is None else tuple(mean["names"]),
            prepared["gross_limit"] is not None,
            prepared["cardinality"],
            prepared["cvar_limit"],
            prepared["min_ticket"],
            prepared["cvar_alpha"],
            prepared["gamma"],
            prepared["n_tangents"],
            self.params.get("solver", DEFAULT_SOLVER),
            tuple(sorted((self.params.get("solver_options") or {}).items())),
            domain,
        )
        try:
            hash(signature)
        except TypeError as exc:
            raise TypeError(
                f"{self.key}: _persistent_domain_signature() must return hashable "
                f"shape data, got {domain!r}"
            ) from exc
        return signature

    def _prepare_model(self, inputs, params):
        """Validate and normalize one tick without constructing a Pyomo model."""
        import numpy as np

        state = self._scn
        if state is None:
            raise RuntimeError(
                f"{self.key}: build_model is driven by run(), which resolves instruments() "
                "and the empty-gate check first — no current state is set"
            )
        names = list(state["names"])
        rows, account = state["rows"], state["account"]
        weights, payoffs_r = self.payoffs(inputs)
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
        if set(payoffs_r) != set(names):
            raise ValueError(
                f"{self.key}: payoffs() names {sorted(payoffs_r)} do not match "
                f"instruments() names {sorted(names)}"
            )
        r, tranches = {}, {}
        for name in names:
            try:
                arr = np.asarray(payoffs_r[name], dtype=float)
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    f"{self.key}: payoffs()[{name!r}] must be a scenario vector or a "
                    "matrix of equal-length tranche vectors"
                ) from exc
            if arr.ndim == 1:
                if arr.shape != (n_omega,):
                    raise ValueError(
                        f"{self.key}: payoffs()[{name!r}] has shape {arr.shape}, expected "
                        f"({n_omega},)"
                    )
                tranches[name] = 1
            elif arr.ndim == 2:
                if arr.shape[0] < 1 or arr.shape[1] != n_omega:
                    raise ValueError(
                        f"{self.key}: payoffs()[{name!r}] has shape {arr.shape}, expected "
                        f"(n_tranches >= 1, {n_omega})"
                    )
                tranches[name] = int(arr.shape[0])
                if tranches[name] == 1:
                    arr = arr[0]
            else:
                raise ValueError(
                    f"{self.key}: payoffs()[{name!r}] has {arr.ndim} dimensions; a "
                    "scenario vector or a tranche matrix is expected"
                )
            if not np.all(np.isfinite(arr)):
                raise ValueError(
                    f"{self.key}: payoffs()[{name!r}] must be all finite numbers, got {arr!r}"
                )
            r[name] = arr

        raw_mean = self.mean_uncertainty(inputs)
        mean_uncertainty = None
        if raw_mean is not None:
            wanted = {"budget", "deviation_below", "deviation_above"}
            if not isinstance(raw_mean, Mapping) or set(raw_mean) != wanted:
                raise ValueError(
                    f"{self.key}: mean_uncertainty() must return None or a mapping "
                    f"carrying exactly {sorted(wanted)!r}, got {raw_mean!r}"
                )
            budget = raw_mean["budget"]
            if not number_ok(budget) or budget <= 0.0:
                raise ValueError(
                    f"{self.key}: mean_uncertainty budget must be a finite number > 0, "
                    f"got {budget!r}"
                )
            below, above = raw_mean["deviation_below"], raw_mean["deviation_above"]
            if not isinstance(below, Mapping) or not isinstance(above, Mapping):
                raise ValueError(
                    f"{self.key}: mean_uncertainty deviation halves must be mappings"
                )
            if set(below) != set(above):
                raise ValueError(
                    f"{self.key}: mean_uncertainty deviation names differ: "
                    f"below={sorted(str(v) for v in below)!r}, "
                    f"above={sorted(str(v) for v in above)!r}"
                )
            uncertain_names = sorted(below)
            if not uncertain_names:
                raise ValueError(
                    f"{self.key}: mean_uncertainty carries no names; return None for the "
                    "nominal model"
                )
            unknown = sorted(set(uncertain_names) - set(names))
            if unknown:
                raise ValueError(
                    f"{self.key}: mean_uncertainty names {unknown!r} are not live instruments"
                )
            halves = {}
            for label, source in (("deviation_below", below), ("deviation_above", above)):
                parsed = {}
                for name in uncertain_names:
                    values = source[name]
                    if not isinstance(values, (list, tuple)) or len(values) != tranches[name]:
                        raise ValueError(
                            f"{self.key}: mean_uncertainty {label}[{name!r}] must carry "
                            f"{tranches[name]} tranches, got {values!r}"
                        )
                    if any(not number_ok(value) or value < 0.0 for value in values):
                        raise ValueError(
                            f"{self.key}: mean_uncertainty {label}[{name!r}] must contain "
                            f"finite deviations >= 0, got {values!r}"
                        )
                    parsed[name] = tuple(float(value) for value in values)
                halves[label] = parsed
            mean_uncertainty = {
                "budget": min(float(budget), float(len(uncertain_names))),
                "declared_budget": float(budget),
                "names": uncertain_names,
                **halves,
            }

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
        carried = account.get("carried_wealth", 0.0)
        if not number_ok(carried) or carried < 0.0:
            raise ValueError(
                f"{self.key}: account['carried_wealth'] must be a finite number >= 0 "
                f"when given, got {carried!r} — it is wealth held outside this solve, "
                "never cash"
            )
        carried = float(carried)
        cash0 = float(account["cash"])
        buying_power0 = float(account["buying_power"])
        sale_credit = float(account["sale_credit"])
        cash_reserve = float(account.get("cash_reserve", 0.0))
        gross_limit = account.get("gross_limit")
        w0_mark = (
            cash0
            + sum(float(rows[name]["price"]) * float(rows[name]["held"]) for name in names)
            + carried
        )
        if w0_mark <= 0.0:
            raise ValueError(
                f"{self.key}: account net worth (cash + mark value of held instruments) "
                f"must be > 0, got {w0_mark!r} — the tangent-plane utility objective "
                "(tangent_utility, gamma-relative to this mark) is undefined at or below "
                "zero wealth; a zero/negative-cash account with nothing held cannot be "
                "sized, and must refuse by name rather than reach the solver as NaN"
            )
        w_lo, w_hi = float(account["wealth_lo"]), float(account["wealth_hi"])
        if w_lo <= 0.0:
            raise ValueError(
                f"{self.key}: account wealth_lo {w_lo!r} must be > 0 — tangent_utility "
                "(and the tangent knots built from this interval) is undefined at or below "
                "zero wealth"
            )
        if not w_lo < w_hi:
            raise ValueError(
                f"{self.key}: account wealth_lo {w_lo!r} must be < wealth_hi {w_hi!r}"
            )

        gamma = float(params["risk_aversion_gamma"])
        n_tangents = int(params.get("n_tangents", DEFAULT_N_TANGENTS))
        cardinality = None if params["cardinality"] is None else int(params["cardinality"])
        min_ticket = float(params["min_ticket"])
        cvar_alpha = float(params["cvar_alpha"])
        cvar_limit = params["cvar_limit"]
        omega_ix = list(range(n_omega))
        buy_room = {
            name: int(float(rows[name]["x_max"]) / float(rows[name]["price"]))
            + int(rows[name]["held"])
            + 1
            if float(rows[name]["price"]) > 0
            else int(rows[name]["held"]) + 1
            for name in names
        }
        knots = np.linspace(w_lo, w_hi, n_tangents)
        utility, slope = tangent_utility(knots, w0_mark, gamma)
        return {
            "names": names,
            "rows": rows,
            "account": account,
            "weights": weights,
            "r": r,
            "tranches": tranches,
            "mean_uncertainty": mean_uncertainty,
            "cash0": cash0,
            "buying_power0": buying_power0,
            "sale_credit": sale_credit,
            "cash_reserve": cash_reserve,
            "gross_limit": None if gross_limit is None else float(gross_limit),
            "carried_wealth": carried,
            "w0_mark": w0_mark,
            "w_lo": w_lo,
            "w_hi": w_hi,
            "gamma": gamma,
            "n_tangents": n_tangents,
            "cardinality": cardinality,
            "min_ticket": min_ticket,
            "cvar_alpha": cvar_alpha,
            "cvar_limit": cvar_limit,
            "omega_ix": omega_ix,
            "buy_room": buy_room,
            "tangent_intercept": [
                float(utility[j] - slope[j] * knots[j]) for j in range(n_tangents)
            ],
            "tangent_slope": [float(slope[j]) for j in range(n_tangents)],
        }

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
            Param,
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
        payoffs_r = r
        r, tranches = {}, {}
        for i in names:
            try:
                arr = np.asarray(payoffs_r[i], dtype=float)
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    f"{self.key}: payoffs()[{i!r}] must be a scenario vector or a matrix "
                    "of equal-length tranche vectors"
                ) from exc
            if arr.ndim == 1:
                if arr.shape != (n_omega,):
                    raise ValueError(
                        f"{self.key}: payoffs()[{i!r}] has shape {arr.shape}, expected "
                        f"({n_omega},)"
                    )
                tranches[i] = 1
            elif arr.ndim == 2:
                if arr.shape[0] < 1 or arr.shape[1] != n_omega:
                    raise ValueError(
                        f"{self.key}: payoffs()[{i!r}] has shape {arr.shape}, expected "
                        f"(n_tranches >= 1, {n_omega})"
                    )
                tranches[i] = int(arr.shape[0])
                if tranches[i] == 1:
                    arr = arr[0]  # a one-tranche matrix IS the flat case
            else:
                raise ValueError(
                    f"{self.key}: payoffs()[{i!r}] has {arr.ndim} dimensions; a scenario "
                    "vector or a tranche matrix is expected"
                )
            if not np.all(np.isfinite(arr)):
                raise ValueError(
                    f"{self.key}: payoffs()[{i!r}] must be all finite numbers, got {arr!r}"
                )
            r[i] = arr

        raw_mean = self.mean_uncertainty(inputs)
        mean_uncertainty = None
        if raw_mean is not None:
            wanted = {"budget", "deviation_below", "deviation_above"}
            if not isinstance(raw_mean, Mapping) or set(raw_mean) != wanted:
                raise ValueError(
                    f"{self.key}: mean_uncertainty() must return None or a mapping "
                    f"carrying exactly {sorted(wanted)!r}, got {raw_mean!r}"
                )
            budget = raw_mean["budget"]
            if not number_ok(budget) or budget <= 0.0:
                raise ValueError(
                    f"{self.key}: mean_uncertainty budget must be a finite number > 0, "
                    f"got {budget!r}"
                )
            below, above = raw_mean["deviation_below"], raw_mean["deviation_above"]
            if not isinstance(below, Mapping) or not isinstance(above, Mapping):
                raise ValueError(
                    f"{self.key}: mean_uncertainty deviation halves must be mappings"
                )
            if set(below) != set(above):
                raise ValueError(
                    f"{self.key}: mean_uncertainty deviation names differ: "
                    f"below={sorted(str(v) for v in below)!r}, "
                    f"above={sorted(str(v) for v in above)!r}"
                )
            uncertain_names = sorted(below)
            if not uncertain_names:
                raise ValueError(
                    f"{self.key}: mean_uncertainty carries no names; return None for the "
                    "nominal model"
                )
            unknown = sorted(set(uncertain_names) - set(names))
            if unknown:
                raise ValueError(
                    f"{self.key}: mean_uncertainty names {unknown!r} are not live instruments"
                )
            halves = {}
            for label, source in (("deviation_below", below), ("deviation_above", above)):
                parsed = {}
                for i in uncertain_names:
                    values = source[i]
                    if not isinstance(values, (list, tuple)) or len(values) != tranches[i]:
                        raise ValueError(
                            f"{self.key}: mean_uncertainty {label}[{i!r}] must carry "
                            f"{tranches[i]} tranches, got {values!r}"
                        )
                    if any(not number_ok(v) or v < 0.0 for v in values):
                        raise ValueError(
                            f"{self.key}: mean_uncertainty {label}[{i!r}] must contain "
                            f"finite deviations >= 0, got {values!r}"
                        )
                    parsed[i] = tuple(float(v) for v in values)
                halves[label] = parsed
            mean_uncertainty = {
                "budget": min(float(budget), float(len(uncertain_names))),
                "declared_budget": float(budget),
                "names": uncertain_names,
                **halves,
            }

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
        carried = account.get("carried_wealth", 0.0)
        if not number_ok(carried) or carried < 0.0:
            raise ValueError(
                f"{self.key}: account['carried_wealth'] must be a finite number >= 0 "
                f"when given, got {carried!r} — it is wealth held outside this solve, "
                "never cash"
            )
        carried = float(carried)
        cash0 = float(account["cash"])
        buying_power0 = float(account["buying_power"])
        sale_credit = float(account["sale_credit"])
        cash_reserve = float(account.get("cash_reserve", 0.0))
        gross_limit = account.get("gross_limit")
        # Checked BEFORE wealth_lo/wealth_hi, deliberately: a negative-net-
        # worth account derives an already-nonsensical envelope (a round-11
        # skeptic review found a deeply negative net worth surfacing the
        # generic "wealth_lo must be < wealth_hi" message instead of this
        # one, since a caller's own envelope math can accidentally produce
        # wealth_lo < wealth_hi even when net worth itself is deeply
        # negative) — net worth is the actual thing an operator would fix,
        # so its refusal must win the race.
        w0_mark = (
            cash0
            + sum(float(rows[i]["price"]) * float(rows[i]["held"]) for i in names)
            + carried
        )
        if w0_mark <= 0.0:
            raise ValueError(
                f"{self.key}: account net worth (cash + mark value of held instruments) "
                f"must be > 0, got {w0_mark!r} — the tangent-plane utility objective "
                "(tangent_utility, gamma-relative to this mark) is undefined at or below "
                "zero wealth; a zero/negative-cash account with nothing held cannot be "
                "sized, and must refuse by name rather than reach the solver as NaN"
            )
        w_lo, w_hi = float(account["wealth_lo"]), float(account["wealth_hi"])
        if w_lo <= 0.0:
            raise ValueError(
                f"{self.key}: account wealth_lo {w_lo!r} must be > 0 — tangent_utility "
                "(and the tangent knots built from this interval) is undefined at or "
                "below zero wealth"
            )
        if not w_lo < w_hi:
            raise ValueError(
                f"{self.key}: account wealth_lo {w_lo!r} must be < wealth_hi {w_hi!r}"
            )

        gamma = float(params["risk_aversion_gamma"])
        n_tangents = int(params.get("n_tangents", DEFAULT_N_TANGENTS))
        cardinality = None if params["cardinality"] is None else int(params["cardinality"])
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
        knots = np.linspace(w_lo, w_hi, n_tangents)
        u, du = tangent_utility(knots, w0_mark, gamma)

        model = ConcreteModel(name="scenario-utility-solve")
        all_tranche_ix = [(i, k) for i in names for k in range(tranches[i])]
        unit_ix = [(i, k, o) for i, k in all_tranche_ix for o in omega_ix]

        def _unit_value_now(i, k, o):
            row_return = float(r[i][o]) if tranches[i] == 1 else float(r[i][k][o])
            return float(rows[i]["price"]) * (1.0 + row_return) - float(
                rows[i].get("exit_cost_per_share", 0.0)
            )

        model.input_price = Param(
            names, mutable=True, initialize=lambda m, i: float(rows[i]["price"])
        )
        model.input_held = Param(
            names, mutable=True, initialize=lambda m, i: int(rows[i]["held"])
        )
        model.input_x_max = Param(
            names, mutable=True, initialize=lambda m, i: float(rows[i]["x_max"])
        )
        model.input_cost_buy = Param(
            names, mutable=True, initialize=lambda m, i: float(rows[i]["cost_buy"])
        )
        model.input_cost_sell = Param(
            names, mutable=True, initialize=lambda m, i: float(rows[i]["cost_sell"])
        )
        model.input_exit_cost = Param(
            names,
            mutable=True,
            initialize=lambda m, i: float(rows[i].get("exit_cost_per_share", 0.0)),
        )
        model.input_buy_room = Param(
            names, mutable=True, initialize=lambda m, i: buy_room[i]
        )
        model.input_cash = Param(mutable=True, initialize=cash0)
        model.input_buying_power = Param(mutable=True, initialize=buying_power0)
        model.input_sale_credit = Param(mutable=True, initialize=sale_credit)
        model.input_cash_reserve = Param(mutable=True, initialize=cash_reserve)
        model.input_carried_wealth = Param(mutable=True, initialize=carried)
        model.input_w0_mark = Param(mutable=True, initialize=w0_mark)
        model.input_scenario_weight = Param(
            omega_ix, mutable=True, initialize=lambda m, o: float(weights[o])
        )
        model.input_unit_value = Param(
            unit_ix, mutable=True, initialize=lambda m, i, k, o: _unit_value_now(i, k, o)
        )
        model.input_tangent_intercept = Param(
            range(n_tangents),
            mutable=True,
            initialize=lambda m, j: float(u[j] - du[j] * knots[j]),
        )
        model.input_tangent_slope = Param(
            range(n_tangents), mutable=True, initialize=lambda m, j: float(du[j])
        )
        if gross_limit is not None:
            model.input_gross_limit = Param(mutable=True, initialize=float(gross_limit))
        if mean_uncertainty is not None:
            model.input_robust_budget = Param(
                mutable=True, initialize=float(mean_uncertainty["budget"])
            )
            model.input_mean_deviation = Param(
                [(i, k) for i in mean_uncertainty["names"] for k in range(tranches[i])],
                mutable=True,
                initialize=lambda m, i, k: float(
                    mean_uncertainty["deviation_below"][i][k]
                ),
            )
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
            names, rule=lambda m, i: m.input_held[i] + m.b[i] - m.s[i]
        )
        model.x = Expression(names, rule=lambda m, i: m.input_price[i] * m.q[i])

        model.nonneg_q = Constraint(names, rule=lambda m, i: m.q[i] >= 0)
        model.buy_only = Constraint(
            names, rule=lambda m, i: m.b[i] <= m.input_buy_room[i] * m.d[i]
        )
        model.sell_only = Constraint(
            names, rule=lambda m, i: m.s[i] <= m.input_held[i] * (1 - m.d[i])
        )
        model.elig_hi = Constraint(
            names, rule=lambda m, i: m.x[i] <= m.input_x_max[i] * m.y[i]
        )
        model.elig_lo = Constraint(
            names, rule=lambda m, i: m.x[i] >= min_ticket * m.d[i]
        )
        if cardinality is not None:
            model.cardinality = Constraint(
                expr=sum(model.y[i] for i in names) <= cardinality
            )

        model.cash_after = Expression(
            expr=model.input_cash
            + sum(
                (model.input_price[i] - model.input_cost_sell[i]) * model.s[i]
                - (model.input_price[i] + model.input_cost_buy[i]) * model.b[i]
                for i in names
            )
        )
        model.cash_floor = Constraint(expr=model.cash_after >= model.input_cash_reserve)
        model.buying_power = Constraint(
            expr=sum(
                (model.input_price[i] + model.input_cost_buy[i]) * model.b[i]
                for i in names
            )
            <= model.input_buying_power
            + model.input_sale_credit
            * sum(
                (model.input_price[i] - model.input_cost_sell[i]) * model.s[i]
                for i in names
            )
        )
        if gross_limit is not None:
            model.gross_exposure = Constraint(
                expr=sum(model.x[i] for i in names) <= model.input_gross_limit
            )

        # Exit-horizon tranches (ADR-0188 formulation B): a name whose payoff
        # is a (K, S) matrix splits its target shares across K continuous
        # tranche variables, each valued at its own scenario row. A flat
        # payoff (K = 1) keeps today's single q[i] term and adds nothing.
        tranche_names = [i for i in names if tranches[i] > 1]
        tranche_ix = [(i, k) for i in tranche_names for k in range(tranches[i])]
        if tranche_ix:
            model.e = Var(tranche_ix, domain=NonNegativeReals)
            model.tranche_allocation = Constraint(
                tranche_names,
                rule=lambda m, i: sum(m.e[i, k] for k in range(tranches[i])) == m.q[i],
            )

        if mean_uncertainty is not None:
            uncertain_names = mean_uncertainty["names"]
            model.robust_theta = Var(domain=NonNegativeReals)
            model.robust_rho = Var(uncertain_names, domain=NonNegativeReals)

            def _impact(m, i):
                if tranches[i] == 1:
                    return m.input_price[i] * m.input_mean_deviation[i, 0] * m.q[i]
                return m.input_price[i] * sum(
                    m.input_mean_deviation[i, k] * m.e[i, k]
                    for k in range(tranches[i])
                )

            model.robust_impact = Expression(uncertain_names, rule=_impact)
            model.robust_counterpart = Constraint(
                uncertain_names,
                rule=lambda m, i: m.robust_theta + m.robust_rho[i]
                >= m.robust_impact[i],
            )
            model.robust_protection = Expression(
                expr=model.input_robust_budget * model.robust_theta
                + sum(model.robust_rho[i] for i in uncertain_names)
            )

        def _wealth_rule(m, o):
            nominal = m.cash_after + m.input_carried_wealth + sum(
                (
                    m.q[i] * m.input_unit_value[i, 0, o]
                    if tranches[i] == 1
                    else sum(
                        m.e[i, k] * m.input_unit_value[i, k, o]
                        for k in range(tranches[i])
                    )
                )
                for i in names
            )
            protection = m.robust_protection if mean_uncertainty is not None else 0.0
            return m.W[o] == nominal - protection

        model.wealth = Constraint(omega_ix, rule=_wealth_rule)

        tangent_ix = [(o, j) for o in omega_ix for j in range(n_tangents)]
        model.tangent = Constraint(
            tangent_ix,
            rule=lambda m, o, j: m.t[o]
            <= m.input_tangent_intercept[j] + m.input_tangent_slope[j] * m.W[o],
        )

        model.cvar_row = Constraint(
            omega_ix,
            rule=lambda m, o: m.z[o] >= (m.input_w0_mark - m.W[o]) - m.eta,
        )
        if cvar_limit is not None:
            model.cvar_cap = Constraint(
                expr=model.eta
                + (1.0 / (1.0 - cvar_alpha))
                * sum(model.input_scenario_weight[o] * model.z[o] for o in omega_ix)
                <= float(cvar_limit)
            )

        model.objective = Objective(
            expr=sum(model.input_scenario_weight[o] * model.t[o] for o in omega_ix),
            sense=maximize,
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
            "tranches": tranches,
            "carried_wealth": carried,
            "mean_uncertainty": mean_uncertainty,
        }

        self.domain_constraints(model, inputs, params)
        return model

    @staticmethod
    def _metadata(prepared):
        """Complete current-call snapshot used only by exact extraction."""
        return {
            "names": list(prepared["names"]),
            "rows": prepared["rows"],
            "weights": prepared["weights"],
            "r": prepared["r"],
            "w_lo": prepared["w_lo"],
            "w_hi": prepared["w_hi"],
            "w0_mark": prepared["w0_mark"],
            "cash0": prepared["cash0"],
            "buying_power0": prepared["buying_power0"],
            "sale_credit": prepared["sale_credit"],
            "cash_reserve": prepared["cash_reserve"],
            "gross_limit": prepared["gross_limit"],
            "cardinality": prepared["cardinality"],
            "min_ticket": prepared["min_ticket"],
            "cvar_alpha": prepared["cvar_alpha"],
            "cvar_limit": prepared["cvar_limit"],
            "gamma": prepared["gamma"],
            "tranches": prepared["tranches"],
            "carried_wealth": prepared["carried_wealth"],
            "mean_uncertainty": prepared["mean_uncertainty"],
        }

    def _refresh_model(self, model, inputs, params, prepared):
        """Replace every mutable coefficient and bound for one cache hit."""
        names = prepared["names"]
        rows = prepared["rows"]
        tranches = prepared["tranches"]
        returns = prepared["r"]
        for name in names:
            row = rows[name]
            model.input_price[name].set_value(float(row["price"]))
            model.input_held[name].set_value(int(row["held"]))
            model.input_x_max[name].set_value(float(row["x_max"]))
            model.input_cost_buy[name].set_value(float(row["cost_buy"]))
            model.input_cost_sell[name].set_value(float(row["cost_sell"]))
            model.input_exit_cost[name].set_value(
                float(row.get("exit_cost_per_share", 0.0))
            )
            model.input_buy_room[name].set_value(prepared["buy_room"][name])
            model.s[name].setub(int(row["held"]))
            for k in range(tranches[name]):
                for outcome in prepared["omega_ix"]:
                    ret = (
                        float(returns[name][outcome])
                        if tranches[name] == 1
                        else float(returns[name][k][outcome])
                    )
                    model.input_unit_value[name, k, outcome].set_value(
                        float(row["price"]) * (1.0 + ret)
                        - float(row.get("exit_cost_per_share", 0.0))
                    )
        model.input_cash.set_value(prepared["cash0"])
        model.input_buying_power.set_value(prepared["buying_power0"])
        model.input_sale_credit.set_value(prepared["sale_credit"])
        model.input_cash_reserve.set_value(prepared["cash_reserve"])
        model.input_carried_wealth.set_value(prepared["carried_wealth"])
        model.input_w0_mark.set_value(prepared["w0_mark"])
        for outcome, weight in enumerate(prepared["weights"]):
            model.input_scenario_weight[outcome].set_value(float(weight))
            model.W[outcome].setlb(prepared["w_lo"])
            model.W[outcome].setub(prepared["w_hi"])
        for j in range(prepared["n_tangents"]):
            model.input_tangent_intercept[j].set_value(
                prepared["tangent_intercept"][j]
            )
            model.input_tangent_slope[j].set_value(prepared["tangent_slope"][j])
        if prepared["gross_limit"] is not None:
            model.input_gross_limit.set_value(prepared["gross_limit"])
        mean = prepared["mean_uncertainty"]
        if mean is not None:
            model.input_robust_budget.set_value(mean["budget"])
            for name in mean["names"]:
                for k, deviation in enumerate(mean["deviation_below"][name]):
                    model.input_mean_deviation[name, k].set_value(deviation)
        model._scn = self._metadata(prepared)
        self._refresh_domain_constraints(model, inputs, params)

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
        if meta["cardinality"] is not None and len(target) > meta["cardinality"]:
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
        tranches, carried = meta["tranches"], meta["carried_wealth"]
        n_omega = len(weights)
        # The tranche split is a continuous EXPECTATION (never an order), so
        # the solver's values are read, then checked: non-negative and
        # summing to the integer target of the name.
        allocation = {}
        for i in names:
            if tranches[i] == 1:
                continue
            values = [float(model.e[i, k].value or 0.0) for k in range(tranches[i])]
            q = target.get(i, 0)
            if min(values) < -1e-6 or abs(sum(values) - q) > 1e-6 * max(1.0, float(q)):
                raise AssertionError(
                    f"{self.key}: {i}: tranche allocation {values!r} does not split the "
                    f"target {q} (non-negative, summing to the target)"
                )
            allocation[i] = [max(v, 0.0) for v in values]

        def _unit_value(i, k, o):
            row_return = float(r[i][o]) if tranches[i] == 1 else float(r[i][k][o])
            return float(rows[i]["price"]) * (1.0 + row_return) - float(
                rows[i].get("exit_cost_per_share", 0.0)
            )

        nominal_wealth = np.array(
            [
                cash_after
                + carried
                + sum(
                    (
                        target.get(i, 0) * _unit_value(i, 0, o)
                        if tranches[i] == 1
                        else sum(
                            allocation[i][k] * _unit_value(i, k, o)
                            for k in range(tranches[i])
                        )
                    )
                    for i in names
                )
                for o in range(n_omega)
            ],
            dtype=float,
        )
        mean_uncertainty = meta["mean_uncertainty"]
        protection = 0.0
        if mean_uncertainty is not None:
            from dskit.pipeline.uncertainty_set import BudgetedMeanSet

            impacts = {}
            below = mean_uncertainty["deviation_below"]
            for i in mean_uncertainty["names"]:
                pieces = [float(target.get(i, 0))] if tranches[i] == 1 else allocation[i]
                impacts[i] = float(rows[i]["price"]) * sum(
                    float(width) * float(shares)
                    for width, shares in zip(below[i], pieces)
                )
            unit_set = BudgetedMeanSet(
                nominal={i: 0.0 for i in impacts},
                deviation_below={i: 1.0 for i in impacts},
                deviation_above={i: 1.0 for i in impacts},
                budget=float(mean_uncertainty["budget"]),
            )
            protection = float(unit_set.protection(impacts))
            solved_protection = float(model.robust_protection())
            if abs(solved_protection - protection) > 1e-6 * max(1.0, protection):
                raise AssertionError(
                    f"{self.key}: robust counterpart protection {solved_protection!r} "
                    f"differs from BudgetedMeanSet.protection {protection!r}"
                )
        wealth = nominal_wealth - protection
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
        metrics = {
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
            "tranches": allocation,
        }
        if mean_uncertainty is not None:
            metrics.update({
                "robust_protection": protection,
                "mean_uncertainty_budget": float(mean_uncertainty["declared_budget"]),
                "mean_uncertainty_budget_effective": float(mean_uncertainty["budget"]),
                "nominal_wealth_min": float(nominal_wealth.min()),
                "nominal_wealth_max": float(nominal_wealth.max()),
                "wealth_lo": float(meta["w_lo"]),
            })
        return {
            "target": target,
            "trades": trades,
            "cash_after": float(cash_after),
            "metrics": metrics,
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
