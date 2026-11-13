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

Tangent-window doctrine
-----------------------
The utility is a tangent-plane envelope, one row per scenario and knot
(~4,100 of a joint tick's ~4,400 rows), while a tick moves each scenario's
wealth through a small part of the knots' wide envelope. :class:`_TangentWindow`
derives, from data the doorway already holds, a range every scenario's wealth
provably stays inside, bounds ``W[o]`` to it, and frees the tangent rows that
cannot be the lowest line there: the same program, about a fifth of the live rows.
Freed rows stay in the model with an infinite right-hand side, so the shape,
the cache signature and :class:`_CompiledParamUpdate` are unchanged, and the
cold build and every cache hit take the window from ONE owner,
:meth:`ScenarioUtilitySolve._tangent_window`. The exactness argument is on
:class:`_TangentWindow`; any doubt keeps the full envelope and every row.

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
    "DeviationCharge",
    "MeanAbsoluteDeviation",
    "PyomoSolve",
    "RecordedCheckRefusal",
    "ScenarioUtilitySolve",
    "SolveRecord",
    "WassersteinDual",
    "TransportActionScore",
    "TransportPortfolioSolve",
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
    (1-gamma)``. That is CRRA with ``gamma = 1/f``, whose growth-optimal
    stake is roughly ``f`` times the full-Kelly stake, and only to first
    order: for +100 / -400 at probability .9 on 10,000 the integer CRRA
    stakes at ``f`` .25 / .5 / 1 are 4 / 7 / 12 against ``f`` times the
    12.5 Kelly stake = 3.1 / 6.25 / 12.5 (29% and 12% above at the small
    fractions). Both are zero at ``w = w0``. This is the one
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
        gap = cls._gap(objective, bound)
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
    def _gap(objective, bound):
        """Use the same native relative-gap definition for live and saved solves."""
        return (None if objective is None or bound is None
                else abs(objective-bound)/max(abs(objective), _GAP_FLOOR))

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


@dataclass(frozen=True)
class _UtilitySolveRecord(SolveRecord):
    """A :class:`SolveRecord` of a utility program, saying how the strategy decided the week (ADR-0299).

    ``termination`` and ``status`` keep the meaning they have for every solve, so the timeout policy decides a week
    after a declined certification as it does after a native limit; these two fields say what actually happened.

    Parameters
    ----------
    strategy_outcome : str
        ``relaxation_certified`` (the support incumbent is within the allowance of the LP relaxation's bound, no
        certification solve), ``certified`` (a certification solve ran on the program with the actions the reduced
        costs rule out fixed), ``declined`` (too many actions left free to certify: no limit was hit, the
        incumbent stands under the relaxation's bound), ``relaxation_failed`` (the relaxation did not solve, no
        incumbent), ``solver_error`` (the solver ended in ``error`` with no solution and no objective or bound, under
        ``timeout_policy="return"``: a no-incumbent week recorded as the error it was, not as a timeout) or ``direct``
        (the whole integer program was handed to the solver).
    bound_source : str
        ``relaxation`` (the bound is the LP relaxation's), ``certification`` (the tighter of the relaxation and the
        certification solve with the fixed actions' reach), ``solver`` (the solver's own bound, ``direct``) or
        ``none``.
    """

    strategy_outcome: str
    bound_source: str

    #: The values the two fields may hold.
    OUTCOMES = ("relaxation_certified", "certified", "declined", "relaxation_failed", "direct", "solver_error")
    SOURCES = ("relaxation", "certification", "solver", "none")

    def coherent(self):
        """Whether both fields hold a value the strategy can write."""
        return self.strategy_outcome in self.OUTCOMES and self.bound_source in self.SOURCES


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


#: What an inert tangent row adds to its right-hand side: infinity, so the
#: row keeps its place in the matrix and its mutable bound while HiGHS reads it
#: as free. It works because the row is ``t - slope * W - intercept - relax <=
#: 0``: Pyomo keeps its upper bound (0) whatever the parameters hold, and appsi
#: evaluates the moved constant only when it writes the row. A finite stand-in
#: such as 1e30 would make HiGHS log "treated as +Infinity" once per changed row.
_INERT_ROW_BOUND = math.inf

#: How far the wealth window is widened past the range it derives, as a
#: fraction of the envelope's top edge. The range is exact arithmetic on the
#: rows; this only covers float rounding, so a range can never cut a point
#: the rounding put a few ulps outside it. It is far below every solver
#: tolerance (HiGHS's primal feasibility default is 1e-7).
_WINDOW_SLACK = 1e-9


class _TangentWindow:
    """Per-scenario wealth range, and the tangent rows that can still be the minimum in it.

    The tangent envelope is one row per scenario ``o`` and knot ``j``,
    ``t[o] <= intercept_j + slope_j * W[o]``, so ``t[o]`` is the LOWEST of
    the knot lines at ``W[o]``. The knots are laid over a wide envelope
    ``[w_lo, w_hi]`` while a tick's decisions move ``W[o]`` through a small
    part of it, so most rows can never be the lowest line. This object holds

    * ``lower``/``upper``: a range ``W[o]`` provably stays inside, and
    * ``live``: the ``(o, j)`` rows whose line is the lowest somewhere in
      that range. The rest are made inert (their right-hand side is raised
      by :data:`_INERT_ROW_BOUND`) and stay in the model, so the persistent
      model's shape and its compiled update are unchanged.

    Any doubt returns the full envelope with every tangent live: a
    non-finite number, a price that is not positive, a negative cost,
    tangents that are not a strictly concave, increasing envelope, or a range
    that is empty once cut to the envelope. Keeping a row is always safe;
    dropping one needs the argument in the notes.

    Parameters
    ----------
    lower, upper : numpy.ndarray
        ``(n_scenarios,)`` bounds ``W[o]`` is held to (inside the envelope).
    live : numpy.ndarray
        ``(n_scenarios, n_tangents)`` booleans: the rows that stay active.
    windowed : bool
        False when this is the full envelope with every tangent live.

    Notes
    -----
    Inside ``[lower[o], upper[o]]`` a dropped line lies above another line
    everywhere, so ``t[o] <= min over kept lines`` is the SAME set of
    ``(t[o], W[o])`` as ``t[o] <= min over all lines``. Bounding ``W[o]`` to
    the range then cuts no feasible point, provided the range holds at every
    feasible point (or, for the protection term below, at some optimal one).

    Write ``p_i, h_i`` for price and held, ``c_i, f_i`` for the buy and sell
    cost per share, ``N_i = p_i q_i >= 0`` for the target notional and
    ``g_io`` for the scenario value per dollar of notional (exit cost
    inside; a multi-tranche name ranges over its tranches, since the split is
    free). With ``C`` the cash after trades and ``A = C + sum N_i`` the book
    value::

        W[o] = carried + C + sum g_io N_i - P = carried + A + sum (g_io - 1) N_i - P
        A    = A_hi - sum c_i b_i - sum f_i s_i,   A_hi = cash + sum p_i h_i   (exact)

    ``A <= A_hi`` for costs ``>= 0``, and ``sum N_i = A - C <= A_hi - reserve``
    because ``C >= reserve`` always holds. With ``N_i <= max(x_max_i, 0)``,
    ``sum N_i <= gross_limit`` when given and ``P >= 0``::

        W[o] <= carried + A_hi + sum max(g_io^max - 1, 0) N_i
        W[o] >= carried + A_lo - sum max(1 - g_io^min, 0) N_i - P

    Each sum is at most the largest coefficient times the total notional, and
    at most the per-name caps' sum. ``A_lo = A_hi - fees_max``: sells cost at
    most ``sum f_i h_i``, buys at most the largest ``c / (p + c)`` times the
    spend, and the spend is capped by the cash and buying-power rows.

    ``P``, the mean-error block's protection, has NO upper bound over the
    feasible set: its variables are free above. What holds is that a larger
    ``W`` never hurts (utility, CVaR and the wealth rows all favor it), so an
    optimal solution can be taken with the LEAST protection its decisions
    need, at most ``min(sum I_i, budget * max I_i)`` where ``I_i = max
    deviation_i * N_i``, lifted only as far as the envelope's top edge forces
    (``max_o upper_o - w_hi``). That needs every domain row to leave ``W``,
    ``t``, ``eta``, ``z`` and the protection variables alone, which the
    doorway cannot check: the lower edge moves past the protection only for a
    subclass that sets :attr:`ScenarioUtilitySolve._WINDOW_PAST_PROTECTION`,
    and stays at ``w_lo`` otherwise.

    Examples
    --------
    Derive one tick's window from its prepared data and read what it kept::

        window = _TangentWindow.derive(prepared, past_protection=False)
        window.windowed          # -> True
        window.live.sum(axis=1)  # -> live tangents per scenario
        window.lower[0], window.upper[0]
    """

    __slots__ = ("lower", "upper", "live", "windowed")

    def __init__(self, lower, upper, live, windowed):
        self.lower = lower
        self.upper = upper
        self.live = live
        self.windowed = windowed

    @classmethod
    def full(cls, data):
        """Return the whole envelope for every scenario with every tangent live.

        Parameters
        ----------
        data : mapping
            The doorway's prepared data: ``weights``, ``w_lo``, ``w_hi`` and
            ``n_tangents`` are read.

        Returns
        -------
        _TangentWindow
        """
        import numpy as np

        n = len(data["weights"])
        return cls(
            np.full(n, float(data["w_lo"])),
            np.full(n, float(data["w_hi"])),
            np.ones((n, int(data["n_tangents"])), dtype=bool),
            False,
        )

    @classmethod
    def derive(cls, data, past_protection):
        """Derive the range and the live rows, or the full envelope on any doubt.

        Parameters
        ----------
        data : mapping
            The doorway's prepared data: ``names``, ``rows``, ``r``,
            ``weights``, ``mean_uncertainty``, ``cash0``, ``buying_power0``,
            ``sale_credit``, ``cash_reserve``, ``gross_limit``,
            ``carried_wealth``, ``w_lo``, ``w_hi``, ``n_tangents``,
            ``tangent_intercept`` and ``tangent_slope``.
        past_protection : bool
            Whether the lower edge may move past the mean-error protection
            (see the notes); False keeps it at ``w_lo`` under that block.

        Returns
        -------
        _TangentWindow
        """
        import numpy as np

        with np.errstate(all="ignore"):
            book = cls._book(data)
            edges = cls._line_edges(data)
            if book is None or edges is None:
                return cls.full(data)
            reach = cls._reach(data, book, past_protection)
            if reach is None:
                return cls.full(data)
            slack = _WINDOW_SLACK * book["w_hi"]
            lower = np.maximum(reach[0] - slack, book["w_lo"])
            upper = np.minimum(reach[1] + slack, book["w_hi"])
            if not (np.all(np.isfinite(lower)) and np.all(np.isfinite(upper))
                    and bool(np.all(lower < upper))):
                return cls.full(data)
            first, last = edges
            live = (first[None, :] <= upper[:, None]) & (last[None, :] >= lower[:, None])
            if not bool(live.any(axis=1).all()):
                return cls.full(data)
            return cls(lower, upper, live, True)

    @staticmethod
    def _column(rows, names, key, default=None):
        """One row field per name, as a float array."""
        import numpy as np

        return np.array(
            [float(rows[i][key] if default is None else rows[i].get(key, default)) for i in names],
            dtype=float,
        )

    @classmethod
    def _book(cls, data):
        """Return the per-name arrays and account scalars the range reads; None if a premise fails."""
        import numpy as np

        names, rows = data["names"], data["rows"]
        gross = data["gross_limit"]
        book = {
            "price": cls._column(rows, names, "price"),
            "held": cls._column(rows, names, "held"),
            "cap": cls._column(rows, names, "x_max"),
            "buy": cls._column(rows, names, "cost_buy"),
            "sell": cls._column(rows, names, "cost_sell"),
            "exit": cls._column(rows, names, "exit_cost_per_share", 0.0),
            "cash": float(data["cash0"]),
            "power": float(data["buying_power0"]),
            "credit": float(data["sale_credit"]),
            "reserve": float(data["cash_reserve"]),
            "carried": float(data["carried_wealth"]),
            "gross": None if gross is None else float(gross),
            "w_lo": float(data["w_lo"]),
            "w_hi": float(data["w_hi"]),
        }
        finite = all(v is None or bool(np.all(np.isfinite(v))) for v in book.values())
        signs = bool(
            np.all(book["price"] > 0.0) and np.all(book["held"] >= 0.0)
            and np.all(book["buy"] >= 0.0) and np.all(book["sell"] >= 0.0)
        )
        return book if finite and signs else None

    @staticmethod
    def _line_edges(data):
        """Where each knot line starts and stops being the lowest line; None if not concave.

        The tangents of a strictly concave, increasing function have falling
        slopes and rising crossings, and line ``j`` is the lowest between its
        crossings with lines ``j - 1`` and ``j + 1``.
        """
        import numpy as np

        n = int(data["n_tangents"])
        intercept = np.asarray(data["tangent_intercept"], dtype=float)
        slope = np.asarray(data["tangent_slope"], dtype=float)
        if n < 2 or intercept.shape != (n,) or slope.shape != (n,):
            return None
        fall = slope[:-1] - slope[1:]
        if not (np.all(np.isfinite(intercept)) and np.all(np.isfinite(slope))
                and np.all(fall > 0.0) and np.all(slope > 0.0)):
            return None
        cross = (intercept[1:] - intercept[:-1]) / fall
        if not (np.all(np.isfinite(cross)) and bool(np.all(np.diff(cross) >= 0.0))):
            return None
        return np.concatenate(([-np.inf], cross)), np.concatenate((cross, [np.inf]))

    @staticmethod
    def _multipliers(data, book):
        """Return ``(gain, loss)``, each ``(n_names, n_scenarios)``; None on an unexpected shape.

        ``gain`` is ``max(g_max - 1, 0)`` and ``loss`` is ``max(1 - g_min, 0)``,
        with ``g`` the scenario value per dollar of notional, exit cost inside.
        """
        import numpy as np

        n = len(data["weights"])
        gain = np.empty((len(data["names"]), n))
        loss = np.empty_like(gain)
        for k, name in enumerate(data["names"]):
            grid = np.atleast_2d(np.asarray(data["r"][name], dtype=float))
            if grid.ndim != 2 or grid.shape[1] != n:
                return None
            per_dollar = 1.0 + grid - book["exit"][k] / book["price"][k]
            gain[k] = np.maximum(per_dollar.max(axis=0) - 1.0, 0.0)
            loss[k] = np.maximum(1.0 - per_dollar.min(axis=0), 0.0)
        return gain, loss

    @staticmethod
    def _book_value_range(book):
        """Return ``(A_lo, A_hi, notional)``: the book value's range and the notional it allows."""
        import numpy as np

        price, held = book["price"], book["held"]
        a_hi = book["cash"] + float(price @ held)
        proceeds = float(np.maximum(price - book["sell"], 0.0) @ held)
        spend = book["cash"] - book["reserve"] + proceeds
        if book["credit"] >= 0.0:
            spend = min(spend, book["power"] + book["credit"] * proceeds)
        buy_share = float(np.max(book["buy"] / (price + book["buy"])))
        fees = buy_share * max(spend, 0.0) + float(book["sell"] @ held)
        notional = min(a_hi - book["reserve"], float(np.maximum(book["cap"], 0.0).sum()))
        if book["gross"] is not None:
            notional = min(notional, book["gross"])
        return a_hi - fees, a_hi, max(notional, 0.0)

    @staticmethod
    def _protection_cap(data, book, notional):
        """Return an upper bound on the protection a least-protection optimum carries."""
        import numpy as np

        mean = data["mean_uncertainty"]
        index = {name: k for k, name in enumerate(data["names"])}
        caps = np.minimum(np.maximum(book["cap"], 0.0), notional)
        room = caps[[index[name] for name in mean["names"]]]
        worst = np.array([max(mean["deviation_below"][name]) for name in mean["names"]])
        impacts = worst * room
        return float(min(
            impacts.sum(), float(worst.max()) * notional, float(mean["budget"]) * float(impacts.max())
        ))

    @classmethod
    def _reach(cls, data, book, past_protection):
        """Return ``(lower, upper)`` per scenario, before the envelope; None on any doubt."""
        import numpy as np

        multipliers = cls._multipliers(data, book)
        if multipliers is None:
            return None
        gain, loss = multipliers
        a_lo, a_hi, notional = cls._book_value_range(book)
        caps = np.minimum(np.maximum(book["cap"], 0.0), notional)[:, None]
        up = np.minimum(gain.max(axis=0) * notional, (gain * caps).sum(axis=0))
        down = np.minimum(loss.max(axis=0) * notional, (loss * caps).sum(axis=0))
        upper = book["carried"] + a_hi + up
        lower = book["carried"] + a_lo - down
        if data["mean_uncertainty"] is None:
            return lower, upper
        if not past_protection:
            return np.full_like(lower, -np.inf), upper
        cap = max(cls._protection_cap(data, book, notional), float(upper.max()) - book["w_hi"], 0.0)
        return lower - cap, upper


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
    empty-gate short circuit, the per-scenario wealth window that frees the
    tangent rows which cannot bind (:class:`_TangentWindow`; a subclass whose
    domain rows leave wealth alone may also set ``_WINDOW_PAST_PROTECTION``),
    and a post-solve EXACT recompute of every
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

    #: Bound each ``W[o]`` to the range it can reach and make inert the
    #: tangent rows that cannot be the lowest line inside it
    #: (:class:`_TangentWindow`): the same program, about a fifth of the
    #: live rows. ``False`` keeps the full envelope and every row (the
    #: exactness tests' reference arm).
    _TANGENT_WINDOW = True

    #: Whether the window's LOWER edge may move past the mean-error block's
    #: protection. That is an optimality argument, not a feasibility one (see
    #: :class:`_TangentWindow`), so it holds only when no domain row touches
    #: ``W``, ``t``, ``eta``, ``z`` or the protection variables. A subclass
    #: whose ``domain_constraints`` never do sets it ``True``; the default
    #: keeps the lower edge at ``w_lo`` under a mean-error block.
    _WINDOW_PAST_PROTECTION = False

    def _solver_options(self):
        options = super()._solver_options()
        if self.params.get("solver", DEFAULT_SOLVER) != DEFAULT_SOLVER:
            return options
        return {**self._HIGHS_DETERMINISM, **options}

    def _tangent_window(self, data):
        """Return the tick's :class:`_TangentWindow`: the ONE owner of range and live rows.

        Both the cold build and every cache hit call this with the same
        fields, so the two paths cannot disagree about which rows are live.

        Parameters
        ----------
        data : mapping
            The prepared data (see :meth:`_TangentWindow.derive`).

        Returns
        -------
        _TangentWindow
        """
        if not self._TANGENT_WINDOW:
            return _TangentWindow.full(data)
        return _TangentWindow.derive(data, self._WINDOW_PAST_PROTECTION)

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
        # The same fields, under the same names, that _prepare_model hands a
        # cache hit: _tangent_window is the one owner of range and live rows.
        window = self._tangent_window({
            "names": names,
            "rows": rows,
            "r": r,
            "weights": weights,
            "mean_uncertainty": mean_uncertainty,
            "cash0": cash0,
            "buying_power0": buying_power0,
            "sale_credit": sale_credit,
            "cash_reserve": cash_reserve,
            "gross_limit": None if gross_limit is None else float(gross_limit),
            "carried_wealth": carried,
            "w_lo": w_lo,
            "w_hi": w_hi,
            "n_tangents": n_tangents,
            "tangent_intercept": u - du * knots,
            "tangent_slope": du,
        })

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
        # 0 keeps a tangent row as it is; _INERT_ROW_BOUND makes it free (the
        # row cannot be the lowest line inside its scenario's wealth window).
        tangent_ix = [(o, j) for o in omega_ix for j in range(n_tangents)]
        model.input_tangent_relax = Param(
            tangent_ix,
            mutable=True,
            initialize=lambda m, o, j: 0.0 if window.live[o, j] else _INERT_ROW_BOUND,
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
        model.W = Var(
            omega_ix,
            bounds=lambda m, o: (float(window.lower[o]), float(window.upper[o])),
        )
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

        model.tangent = Constraint(
            tangent_ix,
            rule=lambda m, o, j: m.t[o]
            <= m.input_tangent_intercept[j]
            + m.input_tangent_relax[o, j]
            + m.input_tangent_slope[j] * m.W[o],
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

        # Underscore-prefixed bookkeeping like _scn: what the cache hit diffs against.
        model._window = window

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
        self._refresh_window(model, self._tangent_window(prepared))
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

    @staticmethod
    def _refresh_window(model, window):
        """Move a cached model to ``window``: every ``W`` bound, and only the rows that flipped.

        Parameters
        ----------
        model : pyomo.environ.ConcreteModel
            A model built by :meth:`build_model`, holding the previous window.
        window : _TangentWindow
            This tick's window.
        """
        import numpy as np

        for outcome in range(len(window.lower)):
            model.W[outcome].setlb(float(window.lower[outcome]))
            model.W[outcome].setub(float(window.upper[outcome]))
        previous = getattr(model, "_window", None)
        moved = window.live != previous.live if previous is not None else np.ones_like(window.live)
        for outcome, knot in np.argwhere(moved):
            live = bool(window.live[outcome, knot])
            model.input_tangent_relax[int(outcome), int(knot)].set_value(
                0.0 if live else _INERT_ROW_BOUND
            )
        model._window = window

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


class WassersteinDual:
    """Embed an exact finite-grid W1 worst-expectation dual in a Pyomo model.

    Parameters
    ----------
    grid, masses : sequence of float
        Ordered support and simplex probabilities, validated by DiscreteCDFGrid.
    radius, scale : float
        Nonnegative dimensionless radius and positive support-distance divisor.
    q_lo, q_hi : sequence of float or None
        Optional nominal-containing cumulative bands on every grid node.

    Examples
    --------
    Construct the ambiguity set, then attach losses (constants or expressions)::

        dual = WassersteinDual([80., 100., 120.], [.2, .6, .2], .01, 100.)
    """

    def __init__(self, grid, masses, radius, scale, *, q_lo=None, q_hi=None):
        from dskit.pipeline.libs.predictive_cdf import DiscreteCDFGrid

        if (not number_ok(radius) or radius < 0
                or not number_ok(scale) or scale <= 0):
            raise ValueError("radius must be nonnegative and scale positive finite numbers")
        self.distribution = DiscreteCDFGrid.from_masses(grid, masses)
        self.bands = self.distribution.validate_bands(q_lo, q_hi)
        self.radius, self.scale = float(radius), float(scale)

    def attach(self, model, losses, name="wasserstein", *, sparse=False):
        """Add a block whose cost is the worst expected loss when minimized.

        Parameters
        ----------
        model : pyomo.environ.ConcreteModel
            Caller-owned model; the component name must be unused.
        losses : sequence of numeric expressions
            One finite numeric constant or caller-owned linear expression per node.
        name : str
            Unique block component name.
        sparse : bool
            Use adjacent unrestricted potentials for the unbanded problem.
            The default preserves the cumulative dual and supports CDF bands.

        Returns
        -------
        pyomo.environ.Block
            Dual variables, constraints and cost expression; no objective is added.

        Raises
        ------
        ValueError
            Invalid component name, loss shape, numeric constant or nonlinear loss.
        """
        from pyomo.environ import Block, Constraint, Expression, NonNegativeReals, Reals, Var
        from pyomo.core.expr.numvalue import value
        from pyomo.repn.standard_repn import generate_standard_repn
        from pyomo.core.expr.visitor import identify_variables

        if type(sparse) is not bool:
            raise ValueError("sparse must be a boolean")
        if sparse and self.bands is not None:
            raise ValueError("sparse attachment does not support CDF bands")
        grid = self.distribution.grid
        losses = list(losses)
        if len(losses) != len(grid) or not isinstance(name, str) or not name or hasattr(model, name):
            raise ValueError("loss shape or unused block name is invalid")
        for loss in losses:
            repn = generate_standard_repn(loss)
            if not repn.is_linear():
                raise ValueError("losses must be linear expressions")
            if any(not number_ok(value(coefficient, exception=False))
                   for coefficient in (repn.constant, *repn.linear_coefs)):
                raise ValueError("losses must have finite coefficients")
            if any(variable.model() is not model
                   for variable in identify_variables(loss, include_fixed=True)):
                raise ValueError("losses reference foreign-model variables")
        block = Block(concrete=True)
        model.add_component(name, block)
        indices = range(len(grid) - 1)
        if sparse:
            # Potentials are the cumulative dual levels after telescoping.
            # Zero-mass support points remain admissible transport destinations.
            block.potential = Var(range(len(grid)), domain=Reals)
            block.gamma = Var(domain=NonNegativeReals)
            block.loss_bound = Constraint(
                range(len(grid)), rule=lambda b, k: b.potential[k] >= losses[k])
            block.up = Constraint(
                indices, rule=lambda b, j:
                b.potential[j+1]-b.potential[j]
                <= b.gamma * float(grid[j+1]-grid[j]) / self.scale)
            block.down = Constraint(
                indices, rule=lambda b, j:
                b.potential[j]-b.potential[j+1]
                <= b.gamma * float(grid[j+1]-grid[j]) / self.scale)
            block.cost = Expression(
                expr=sum(float(p)*block.potential[k]
                         for k, p in enumerate(self.distribution.masses))
                + self.radius*block.gamma)
            return block
        block.level = Var(domain=Reals)
        block.alpha = Var(indices, domain=NonNegativeReals)
        block.beta = Var(indices, domain=NonNegativeReals)
        block.gamma = Var(domain=NonNegativeReals)
        block.mu = Var(indices, domain=NonNegativeReals)
        block.nu = Var(indices, domain=NonNegativeReals)
        if self.bands is None:
            for j in indices:
                block.mu[j].fix(0.)
                block.nu[j].fix(0.)
        block.loss_bound = Constraint(
            range(len(grid)), rule=lambda b, k:
            b.level + sum(b.alpha[j] - b.beta[j] + b.mu[j] - b.nu[j]
                          for j in indices if j >= k) >= losses[k])
        block.transport = Constraint(
            indices, rule=lambda b, j:
            b.alpha[j] + b.beta[j] <= b.gamma * float(grid[j+1]-grid[j]) / self.scale)
        cumulative = self.distribution.masses.cumsum()
        band_cost = (0. if self.bands is None else
                     sum(block.mu[j] * float(self.bands[1][j])
                         - block.nu[j] * float(self.bands[0][j]) for j in indices))
        block.cost = Expression(expr=block.level + self.radius * block.gamma
                               + sum((block.alpha[j]-block.beta[j]) * float(cumulative[j])
                                     for j in indices) + band_cost)
        return block


class DeviationCharge(ABC):
    """Price a deviation of one book's outcome losses into a linear objective.

    The charge is ``price * D(L)``. ``L`` holds the book's loss at each
    support point of a finite outcome grid, and ``D`` is a deviation of ``L``
    under the nominal ``masses``: convex and positively homogeneous in the
    actions (``D(2x) = 2 D(x)``), and zero for a loss that is the same in
    every outcome. A subclass states ``D`` twice, on purpose.
    :meth:`deviation` evaluates it exactly in numpy for the independent
    audit. :meth:`epigraph` states it as linear rows over the losses and
    auxiliary variables whose least cost is ``D``, so a maximizing program
    that subtracts ``price`` times that cost stays a mixed-integer LP.

    Parameters
    ----------
    masses : sequence of float
        Finite nonnegative nominal outcome probabilities, one per outcome.
    price : float
        Finite nonnegative objective units per unit of deviation. Zero is
        inactive: a caller adds nothing to its program.

    Examples
    --------
    Declare a charge in configuration and build it for one outcome grid::

        charge = DeviationCharge.from_spec({"measure": "mad", "price": .5}, [.2, .6, .2])
    """

    #: The configuration name a concrete subclass answers to.
    measure = None

    def __init__(self, masses, price):
        import numpy as np

        self.masses = np.asarray(masses, dtype=float)
        if (self.masses.ndim != 1 or not len(self.masses)
                or not np.isfinite(self.masses).all() or (self.masses < 0).any()):
            raise ValueError("deviation masses must be finite and nonnegative")
        if not number_ok(price) or price < 0:
            raise ValueError("risk charge price must be finite and nonnegative")
        self.price = float(price)

    @staticmethod
    def check_spec(spec):
        """Refuse a malformed declaration ``{"measure": name, "price": >= 0}``.

        Parameters
        ----------
        spec : object
            Proposed declaration.

        Returns
        -------
        dict
            The declaration, unchanged.

        Raises
        ------
        ValueError
            Keys differ, the measure is unknown or the price is negative or not finite.
        """
        if (not isinstance(spec, dict) or set(spec) != {"measure", "price"}
                or not isinstance(spec["measure"], str)
                or spec["measure"] not in _DEVIATION_CHARGES
                or not number_ok(spec["price"]) or spec["price"] < 0):
            raise ValueError("risk_charge must be {measure: one of "
                             f"{sorted(_DEVIATION_CHARGES)}, price: a finite number >= 0}}")
        return spec

    @classmethod
    def from_spec(cls, spec, masses):
        """Return the declared charge on one outcome grid's masses.

        Parameters
        ----------
        spec : dict
            Declaration accepted by :meth:`check_spec`.
        masses : sequence of float
            Nominal outcome probabilities.

        Returns
        -------
        DeviationCharge
            The concrete subclass the measure names.

        Raises
        ------
        ValueError
            The declaration or the masses are invalid.
        """
        cls.check_spec(spec)
        return _DEVIATION_CHARGES[spec["measure"]](masses, spec["price"])

    @property
    def active(self):
        """Whether the charge can change a program (a positive price)."""
        return self.price > 0.

    def charge(self, losses):
        """Return ``price * D`` of one numeric loss vector, exactly."""
        return self.price*self.deviation(losses)

    @abstractmethod
    def deviation(self, losses):
        """Evaluate ``D`` of one finite numeric loss vector in closed form.

        Parameters
        ----------
        losses : array-like
            One loss per outcome, aligned with ``masses``.

        Returns
        -------
        float
            The nonnegative deviation.
        """

    @abstractmethod
    def epigraph(self):
        """State ``D`` as linear rows whose least auxiliary cost equals it.

        Returns
        -------
        tuple
            ``(lower, cost, rows)``: one lower bound per auxiliary variable
            (``None`` is free), the cost coefficient of each auxiliary, and
            rows ``(kind, loss_terms, aux_terms)`` reading
            ``sum(c * L[k]) + sum(c * aux[t])`` ``== 0`` (kind ``"eq"``) or
            ``<= 0`` (kind ``"ub"``), each terms dict mapping an index to its
            coefficient.
        """

    def attach(self, model, losses, name):
        """Add a block whose ``cost`` is ``price`` times the epigraph cost.

        Subtracting the cost from a maximized objective charges ``price * D``
        at every optimum, because the auxiliaries then sit at their least
        feasible cost; at any other point the cost can only be larger.

        Parameters
        ----------
        model : pyomo.environ.ConcreteModel
            Caller-owned model; the component name must be unused.
        losses : sequence of numeric expressions
            One linear expression (or constant) per outcome.
        name : str
            Unique block component name.

        Returns
        -------
        pyomo.environ.Block
            Auxiliary variables, rows and the ``cost`` expression; no objective.

        Raises
        ------
        ValueError
            The loss shape or block name is invalid.
        """
        from pyomo.environ import Block, ConstraintList, Expression, Reals, Var

        losses = list(losses)
        if (len(losses) != len(self.masses) or not isinstance(name, str) or not name
                or hasattr(model, name)):
            raise ValueError("loss shape or unused block name is invalid")
        lower, cost, rows = self.epigraph()
        block = Block(concrete=True)
        model.add_component(name, block)
        block.aux = Var(range(len(lower)), domain=Reals,
                        bounds=lambda b, t: (lower[t], None))
        block.rows = ConstraintList()
        for kind, loss_terms, aux_terms in rows:
            expr = (sum(float(c)*losses[k] for k, c in loss_terms.items())
                    + sum(float(c)*block.aux[t] for t, c in aux_terms.items()))
            block.rows.add(expr == 0. if kind == "eq" else expr <= 0.)
        block.cost = Expression(expr=self.price*sum(
            float(c)*block.aux[t] for t, c in enumerate(cost) if c))
        return block

    def relaxation(self, losses):
        """Return the epigraph as dense LP rows over actions and auxiliaries.

        Parameters
        ----------
        losses : array-like
            The ``(outcomes, actions)`` loss coefficient matrix.

        Returns
        -------
        dict
            ``ub`` and ``eq``: ``(action_rows, aux_rows)`` pairs with a zero
            right-hand side; ``cost``: ``price`` times each auxiliary's cost;
            ``bounds``: one ``(lower, None)`` per auxiliary.
        """
        import numpy as np

        matrix = np.asarray(losses, dtype=float)
        lower, cost, rows = self.epigraph()
        parts = {"ub": ([], []), "eq": ([], [])}
        for kind, loss_terms, aux_terms in rows:
            action = np.zeros(matrix.shape[1])
            for k, c in loss_terms.items():
                action += float(c)*matrix[k]
            aux = np.zeros(len(lower))
            for t, c in aux_terms.items():
                aux[t] = float(c)
            parts[kind][0].append(action)
            parts[kind][1].append(aux)
        shaped = {kind: (np.reshape(a, (-1, matrix.shape[1])), np.reshape(b, (-1, len(lower))))
                  for kind, (a, b) in parts.items()}
        return {**shaped, "cost": self.price*np.asarray(cost, dtype=float),
                "bounds": [(bound, None) for bound in lower]}


class MeanAbsoluteDeviation(DeviationCharge):
    """The mean absolute deviation of a book's loss under its nominal masses.

    ``D = sum_k p_k |L_k - mu|`` with ``mu = sum_j p_j L_j``. It costs one
    free auxiliary (``mu``) and one nonnegative ``d_k`` per positive-mass
    outcome, with ``d_k >= +-(L_k - mu)`` and cost ``sum_k p_k d_k``; a
    zero-mass outcome adds nothing to ``D`` and gets no row. For a binary
    payoff of size ``A`` with probability ``q``, ``D = 2 A q (1 - q)``.

    Examples
    --------
    A lottery ticket deviates more than a spread with the same mean::

        MeanAbsoluteDeviation([.5, .3, .2], 1.).deviation([0., 0., -10.])  # 3.2
    """

    measure = "mad"

    def deviation(self, losses):
        """Return ``sum_k p_k |L_k - sum_j p_j L_j|`` (see :class:`DeviationCharge`)."""
        import numpy as np

        values = np.asarray(losses, dtype=float)
        if values.shape != self.masses.shape or not np.isfinite(values).all():
            raise ValueError("invalid deviation losses")
        mean = float(np.dot(self.masses, values))
        return float(np.dot(self.masses, np.abs(values-mean)))

    def epigraph(self):
        """Return the free mean and the two-sided absolute-deviation rows."""
        support = [k for k, p in enumerate(self.masses) if p > 0]
        lower = [None]+[0.]*len(support)
        cost = [0.]+[float(self.masses[k]) for k in support]
        rows = [("eq", {k: float(self.masses[k]) for k in support}, {0: -1.})]
        for t, k in enumerate(support, start=1):
            rows.append(("ub", {k: 1.}, {0: -1., t: -1.}))
            rows.append(("ub", {k: -1.}, {0: 1., t: -1.}))
        return lower, cost, rows


class _AtomDeviation(DeviationCharge):
    """A lower bound on a book's standard deviation over EVERY atom of an outcome grid, as a linear epigraph.

    ``losses`` are the book's payoff at each atom, ordered by the grid (``masses`` the atoms' true probabilities,
    all positive, summing to one); with ``v = L - E[L]`` and ``t = |v|`` the measure ``D`` is the largest of
    ``sum_k p_k t_k`` (the mean absolute deviation), ``sqrt(p_k) t_k / price`` for each atom and, for every lowest
    set ``T`` of atoms and every highest set, ``sum_T p_k t_k / (price sqrt(sum_T p_k))``. Each is at most the sd by
    Cauchy-Schwarz (``sum_T p t <= sqrt(P_T) sqrt(sum_T p v^2) <= sqrt(P_T) sd``), so ``price x D`` is the larger
    of ``price`` MADs (a normal's sd over its MAD) and the tails' sd bounds: a rare loss cannot fall between
    points, since every atom is one. The charge is ``price x D``.

    The epigraph keeps every row sparse: one free ``P_k`` per atom with ``P_k = L_k`` (the only rows over the
    actions; an action that pays at only some atoms ties in only those), the free mean ``mu = sum p_k P_k``,
    ``t_k >= +-(P_k - mu)`` and the running sums ``lo_j >= lo_{j-1} + p_j t_j`` (and ``hi_j`` from the top), so a
    tail bound is two variables: about ``8`` rows of at most three auxiliaries per atom, none dense. It is not a
    ``risk_charge`` measure (no ``measure`` name): the utility dispersion charge builds it
    (:meth:`TransportPortfolioSolve._charge_plan`).
    """

    def __init__(self, masses, price):
        super().__init__(masses, price)
        if len(self.masses) < 2 or (self.masses <= 0).any() or self.price <= 0:
            raise ValueError("an atom deviation needs two or more positive masses and a positive price")

    def columns(self, payoffs):
        """Return ``D`` of every column of an ``(atoms, columns)`` payoff matrix (the vectorized :meth:`deviation`)."""
        import numpy as np

        values = np.asarray(payoffs, dtype=float)
        if values.ndim != 2 or values.shape[0] != len(self.masses) or not np.isfinite(values).all():
            raise ValueError("invalid deviation payoffs")
        p = self.masses[:, None]
        t = np.abs(values-(p*values).sum(axis=0))
        low = np.cumsum(p*t, axis=0)[:-1]/np.sqrt(np.cumsum(p)[:-1, None])
        high = np.cumsum((p*t)[::-1], axis=0)[:-1]/np.sqrt(np.cumsum(p[::-1])[:-1, None])
        return np.maximum.reduce([(p*t).sum(axis=0), low.max(axis=0)/self.price, high.max(axis=0)/self.price,
                                  (np.sqrt(p)*t).max(axis=0)/self.price])

    def deviation(self, losses):
        """Return ``D`` of one payoff vector (see :class:`DeviationCharge`)."""
        import numpy as np

        values = np.asarray(losses, dtype=float)
        if values.shape != self.masses.shape:
            raise ValueError("invalid deviation losses")
        return float(self.columns(values[:, None])[0])

    def epigraph(self):
        """Return the auxiliaries ``P, mu, t, lo, hi, D`` and the rows that make ``D`` the least feasible value."""
        m, p = len(self.masses), [float(x) for x in self.masses]
        mu, t0, lo0, hi0, top = m, m+1, 2*m+1, 3*m+1, 4*m+1
        lower = [None]*(m+1)+[0.]*(3*m+1)
        rows = [("eq", {k: -1.}, {k: 1.}) for k in range(m)]
        rows.append(("eq", {}, {**{k: p[k] for k in range(m)}, mu: -1.}))
        for k in range(m):
            rows.append(("ub", {}, {k: 1., mu: -1., t0+k: -1.}))
            rows.append(("ub", {}, {k: -1., mu: 1., t0+k: -1.}))
        total = 0.
        for j in range(m):   # lowest j+1 atoms, then the highest j+1
            total += p[j]
            rows.append(("ub", {}, {**({lo0+j-1: 1.} if j else {}), t0+j: p[j], lo0+j: -1.}))
            if j < m-1:
                rows.append(("ub", {}, {lo0+j: 1./(self.price*total**.5), top: -1.}))
        total = 0.
        for j in range(m):
            k = m-1-j
            total += p[k]
            rows.append(("ub", {}, {**({hi0+j-1: 1.} if j else {}), t0+k: p[k], hi0+j: -1.}))
            if j < m-1:
                rows.append(("ub", {}, {hi0+j: 1./(self.price*total**.5), top: -1.}))
        rows.append(("ub", {}, {**{t0+k: p[k] for k in range(m)}, top: -1.}))
        rows.extend(("ub", {}, {t0+k: p[k]**.5/self.price, top: -1.}) for k in range(1, m-1))
        return lower, [0.]*top+[1.], rows


#: Every deviation a ``risk_charge`` declaration may name, by measure.
_DEVIATION_CHARGES = {cls.measure: cls for cls in (MeanAbsoluteDeviation,)}


class RecordedCheckRefusal(ValueError):
    """A foreign solve's evidence failed one named check on this host.

    The message is the ordinary refusal text; ``check``, ``category`` and ``ratio`` are the structured
    account an operator can read without any objective, bound or dollar value. ``ratio`` sizes a numeric
    miss against the tolerance that refused it (1.0 is exactly the tolerance; ``None`` where no
    tolerance applies); ``week`` is set by the caller that knows the decision date.

    Parameters
    ----------
    message : str
        The refusal text.
    check : str
        Short snake-case name of the check that failed.
    category : str
        One of :attr:`CATEGORIES`.
    ratio : float or None
        Size of the miss over its tolerance, three significant figures.
    week : str or None
        The decision date, when known.

    Examples
    --------
    ::

        try:
            solve.derived(program, actions, certificates)
        except RecordedCheckRefusal as exc:
            row = RecordedCheckRefusal.describe(exc)  # check, category, ratio, week
    """

    #: ``tolerance`` a numeric miss over its tolerance, at most :attr:`TOLERANCE_RATIO_LIMIT` times it (float noise
    #: and solver tolerance sizes); ``numeric`` a miss beyond that limit or one with no tolerance to size it against
    #: (not a tolerance issue: a wrong or garbage file reads like this); ``invalid_trade`` actions the program forbids;
    #: ``certificate`` malformed or contradictory solver evidence; ``gap`` an uncertified gap; ``pricing`` the
    #: dual pricing of foreign actions failed; ``file``/``replay`` are the caller's (file identity, exact replay).
    CATEGORIES = ("tolerance", "numeric", "invalid_trade", "certificate", "gap", "pricing", "file", "replay", "other")
    #: A numeric miss is called ``tolerance`` only up to this multiple of its tolerance. Differences between two hosts'
    #: floats are a few times the tolerance at most; a miss a hundred times larger is not float noise.
    TOLERANCE_RATIO_LIMIT = 100.

    def __init__(self, message, check, category, ratio=None, week=None):
        super().__init__(message)
        if category not in self.CATEGORIES:
            raise ValueError("unknown refusal category")
        self.check, self.category, self.week = check, category, week
        self.ratio = None if ratio is None or not math.isfinite(ratio) else float(f"{ratio:.3g}")

    @classmethod
    def sized(cls, message, check, difference, tolerance, category="tolerance"):
        """Build the refusal for ``difference`` over ``tolerance`` (ratio ``None`` when the tolerance is not positive).

        A ``tolerance`` category is kept only when the ratio is known and at most :attr:`TOLERANCE_RATIO_LIMIT`;
        otherwise the refusal is ``numeric``.
        """
        ratio = difference/tolerance if tolerance > 0 else None
        if category == "tolerance" and not (ratio is not None and math.isfinite(ratio) and ratio <= cls.TOLERANCE_RATIO_LIMIT):
            category = "numeric"
        return cls(message, check, category, ratio)

    @staticmethod
    def describe(exc):
        """Return ``check``, ``category``, ``ratio`` and ``week`` of any exception (``unclassified``/``other`` for a plain one)."""
        if isinstance(exc, RecordedCheckRefusal):
            return {"check": exc.check, "category": exc.category, "ratio": exc.ratio, "week": exc.week}
        return {"check": "unclassified", "category": "other", "ratio": None, "week": None}


class _TangentEnvelope:
    """The tangent-plane envelope of one concave utility over a wealth range.

    Knots are ``n`` geometric points from ``lo`` to ``hi`` (the envelope's
    overestimate of a log or CRRA utility is uniform in log-wealth spacing)
    plus ``n`` more over the core band ``[w0/core, w0*core]``, where a sized
    book keeps its scenario wealth and the error must be small, plus ``w0``
    itself, so the envelope is exactly zero there and cash is worth exactly
    nothing. ``u`` and its slope come from :func:`tangent_utility`, the one
    utility home. ``value`` is the envelope (the least tangent) at given
    wealths; ``live`` names the tangents that can be that least line somewhere
    in a range; ``error`` and ``core_error`` are the largest overestimate on
    ``[lo, hi]`` and on the core band, which a concave utility reaches only
    where two adjacent tangents cross.

    Why this stands beside ``ScenarioUtilitySolve``'s knot builder rather than
    sharing it: that one lays ``linspace(w_lo, w_hi, n)`` knots over one
    account's reachable wealth, caches a per-scenario window around them and
    owns the tick solver's parameters; this one needs geometric spacing (the
    error is uniform in log wealth), a second knot set over the core band,
    ``w0`` as a knot, ``live`` and the crossing-based error, for scenario
    ranges that differ per group. The two share the one thing that is the
    same, the utility itself (:func:`tangent_utility`); the knot placement is
    not a few lines of common code.
    """

    def __init__(self, w0, gamma, lo, hi, n, core):
        import numpy as np

        if not 0 < lo < w0 <= hi or type(n) is not int or n < 2 or not number_ok(core) or core <= 1:
            raise ValueError("envelope needs 0 < lo < w0 <= hi, at least two knots and a core band above 1")
        self.core = (max(float(lo), float(w0)/float(core)), min(float(hi), float(w0)*float(core)))
        knots = np.unique(np.concatenate([np.geomspace(lo, hi, n), np.geomspace(*self.core, n), [float(w0)]]))
        u, du = tangent_utility(knots, w0, gamma)
        self.w0, self.gamma, self.lo, self.hi = float(w0), float(gamma), float(lo), float(hi)
        self.knots, self.intercept, self.slope = knots, u-du*knots, du
        # Where adjacent tangents cross: tangent j is the least line between crossings j-1 and j.
        self.crossings = (self.intercept[1:]-self.intercept[:-1])/(self.slope[:-1]-self.slope[1:])

    def value(self, wealth):
        """Return the envelope at each wealth: the least tangent there."""
        import numpy as np

        w = np.atleast_1d(np.asarray(wealth, dtype=float))
        return np.min(self.intercept[None, :]+self.slope[None, :]*w[:, None], axis=1)

    def exact(self, wealth):
        """Return the utility itself at each wealth."""
        return tangent_utility(wealth, self.w0, self.gamma)[0]

    def live(self, lo, hi):
        """Return the indices of the tangents that are the least line somewhere in ``[lo, hi]``."""
        import numpy as np

        starts = np.r_[-np.inf, self.crossings]
        ends = np.r_[self.crossings, np.inf]
        return [int(j) for j in np.flatnonzero((starts <= hi) & (ends >= lo))]

    def _largest_error(self, lo, hi):
        """Return the largest envelope-minus-utility on ``[lo, hi]``: at the crossings, where it peaks."""
        inside = [x for x in self.crossings if lo <= x <= hi]
        if not inside:
            return 0.
        return float(max(self.value(inside)-self.exact(inside)))

    @property
    def error(self):
        """The largest overestimate anywhere on ``[lo, hi]``."""
        return self._largest_error(self.lo, self.hi)

    @property
    def core_error(self):
        """The largest overestimate on the core band around ``w0``."""
        return self._largest_error(*self.core)


#: The envelope's public name: ``index_options.kelly_sizer`` builds its stage-2 program on it (ADR-0300).
TangentEnvelope = _TangentEnvelope


def _records_agree(claimed, audit, tolerance=1e-9):
    """Whether a recorded value matches this host's: the same type, floats within ``tolerance`` relative (and a small
    absolute floor for values at float noise), dicts and lists element by element, ``None`` only for ``None``."""
    if isinstance(audit, dict):
        return (isinstance(claimed, dict) and set(claimed) == set(audit)
                and all(_records_agree(claimed[k], audit[k], tolerance) for k in audit))
    if isinstance(audit, (list, tuple)):
        return (isinstance(claimed, (list, tuple)) and len(claimed) == len(audit)
                and all(_records_agree(a, b, tolerance) for a, b in zip(claimed, audit)))
    if isinstance(audit, float):
        return (type(claimed) is float and math.isfinite(claimed)
                and abs(claimed-audit) <= tolerance*max(abs(claimed), abs(audit))+1e-12)
    return type(claimed) is type(audit) and claimed == audit


class _ChargeLines:
    """The tangent lines under one convex dispersion charge ``slope x reserve^2``.

    ``n`` knots geometric over ``[ceiling / 2^((n-1)/2), ceiling]`` (ratio
    root two, so the lines undercount the square by at most about three
    percent between knots) plus the zero line: the charge variable sits at
    their maximum, the piecewise-linear underestimate a maximizing program
    pays. ``value`` is that maximum; ``exact`` the square itself. A ceiling
    that is not finite and positive (no cap, or a zero cap) gives the one
    zero line, and the exact square stands alone.
    """

    def __init__(self, slope, ceiling, n):
        import numpy as np

        self.slope, self.ceiling = float(slope), float(ceiling)
        self.lines = [(0., 0.)]
        if math.isfinite(ceiling) and ceiling > 0:
            knots = ceiling*2.**(-np.arange(n)[::-1]/2.)
            # The tangent of slope*r^2 at knot k: 2*slope*k*r - slope*k^2.
            self.lines.extend((2.*self.slope*float(k), -self.slope*float(k)**2) for k in knots)

    def value(self, reserve):
        """Return the lines' maximum at one reserve."""
        return max(a*float(reserve)+b for a, b in self.lines)

    def exact(self, reserve):
        """Return ``slope x reserve^2``."""
        return self.slope*float(reserve)**2


class TransportPortfolioSolve(PyomoSolve):
    """Size integer actions with marginal W1 profits and shared reserves.

    Parameters
    ----------
    params : dict
        Solver policies plus required absolute_gap, relative_gap,
        and parity_tolerance in objective units. Optional timeout_policy is
        "raise" (default) or "return" (appsi_highs only). Return mode emits
        a non-executable timeout diagnostic when certification is unavailable.
        Optional group_screen is "none" (default) or "lp": before the joint
        integer program is built, each group's own LP relaxation is solved
        alone (no shared caps); a group whose relaxed optimum is within
        parity_tolerance of zero can never add more than that, so it is
        left out of the model and reported with zero actions (when every
        group is screened out the best one is kept so a solver certificate
        still exists). The joint optimum is unchanged within
        parity_tolerance; only the model size and the certificate's
        variable/constraint counts shrink. Replay re-derives the screen.
        Optional utility_solve, read for a utility program only, is
        "relax_fix" (default) or "direct" (:attr:`UTILITY_SOLVES`).
        Optional group_screen_keep (an int >= 1, with group_screen "lp";
        ADR-0301) keeps, of the groups the screen leaves live, only that
        many with the largest standalone relaxed value (ties by index): a
        further candidate restriction, so the certificate covers the kept
        program and the objective can only be lower than the unrestricted
        one. Replay re-derives it with the screen.
        Inputs contain
        groups (id, grid, masses, radius, scale, credit, losses, capacity,
        exclusive, reserve_rows, linear_rows), caps (groups, upper) and
        optionally joint_rows (id, terms, upper). Each loss row is one
        outcome's linear loss coefficients. Reserve rows are linear lower
        bounds on a nonnegative group reserve; linear rows contain
        coefficients and a nonnegative upper bound. A joint row is one
        linear bound across groups: terms maps a group id to that group's
        coefficients and the summed products may not exceed its
        nonnegative upper bound (cash stays feasible). The LP screen ignores
        joint rows and a screened-out group contributes nothing, so with
        joint rows the screen is a candidate restriction, not an exact
        reduction: a group without standalone value is excluded even where a
        negative joint coefficient would let it loosen a row for another
        group (a hedge); the certificate then covers the restricted program.
        The reduction stays exact when no screened group carries a negative
        joint coefficient. All actions are nonnegative integers.
        Exclusive pairs cannot both be positive.
        A group may add risk_charge {"measure", "price"} (ADR-0278
        amendment, estimation risk): its value becomes credit minus the
        W1-worst expected loss minus price times the measure's deviation of
        its loss under the nominal masses (:class:`DeviationCharge`; "mad"
        is :class:`MeanAbsoluteDeviation`), a linear epigraph, so the
        program stays a MILP. The LP screen relaxes the same charged value.
        With any positive price the selection adds risk_charges, one
        charge per group; group_values stay uncharged (credit minus
        W1-worst loss) and value is their sum minus the charges. An absent
        key or a zero price builds the identical program and selection.
        Optional ``utility`` (see :meth:`_checked_utility`) replaces the
        dollar objective by ``w0`` times the expected tangent-envelope
        utility of scenario wealth (:func:`tangent_utility`, the one home;
        ``gamma`` 1 is full Kelly; CRRA with ``gamma = 1/f`` sizes roughly
        ``f`` times the Kelly stake, to first order only) less a dispersion
        charge: every scenario's wealth is ``w0`` plus every group's
        expected profit under its nominal masses plus the scenario's own
        declared profit terms, held at or above ``wealth_lo`` by a hard
        bound; the charge is ``gamma x S^2 / (2 w0)`` per name, the
        second-order utility cost of its books' own dispersion, charged on
        the book the solve chooses: ``S = sum_g M_g`` over the name's
        groups (the ``aggregate`` key), ``M_g`` the larger of 1.25 x the mean
        absolute deviation (a normal's sd over its MAD) of the group's book
        payoff over EVERY atom of its grid and the Cauchy-Schwarz lower bounds
        on its sd at each atom and over each lowest and each highest set of
        atoms (:class:`_AtomDeviation`, a linear epigraph with one payoff
        variable per atom, about 8 sparse rows per atom; :meth:`_charge_plan`,
        :class:`_ChargeLines`). It needs radius 0 and no
        risk charge; the selection then carries a ``utility`` block
        (envelope and exact utility, the charges, envelope error, knot
        count, every scenario's wealth) and ``value`` is the envelope
        objective less the charges; group_values stay the dollar
        expectations. The LP screen reads dollar expectations: with a
        utility it is a candidate restriction, as with joint rows: it drops
        a group with no dollar edge even where that group would hedge a
        scenario, so the certificate covers the restricted program.
        ``last_screen`` reports how many groups the last screen kept and
        dropped.

    Examples
    --------
    Construct a selector without prescribing an application or policy::

        solver = TransportPortfolioSolve("joint", {
            "absolute_gap": .05, "relative_gap": .0001,
            "parity_tolerance": .001})
    """

    role = "transform"
    outputs = ("selection",)
    _PARAMS = PyomoSolve._PARAMS + (
        "absolute_gap", "relative_gap", "parity_tolerance", "timeout_policy",
        "group_screen", "group_screen_keep", "utility_solve", "utility_search", "utility_warm_start")

    #: ADR-0302: the keys of ``utility_search``, the deterministic incumbent local search of the relax_fix strategy
    #: (``iterations`` neighbourhoods at most, each freeing ``neighbourhood`` groups and fixing every other group at its
    #: incumbent counts, solved under a HiGHS node limit ``node_limit``, drawn from ``random.Random(seed)``; the clock
    #: may only halt it, :attr:`UTILITY_SEARCH_SHARE`), and of ``utility_warm_start`` (``node_limit``: the node budget
    #: of a certification run from the complete incumbent on a week the action limit would otherwise decline).
    UTILITY_SEARCH_KEYS = ("iterations", "neighbourhood", "node_limit", "seed")
    UTILITY_WARM_START_KEYS = ("node_limit",)
    #: The keys of the two records (``last_search`` and ``last_warm_start``) the strategy leaves for its caller.
    SEARCH_RECORD_KEYS = ("seed", "iterations", "improved", "value_before", "value_after", "accepted_neighbourhoods",
                          "budget_exhausted", "halted_by", "failed", "seconds", "allowance")
    WARM_START_RECORD_KEYS = ("supplied", "accepted", "over_cap", "termination", "value_before", "value_after", "kept")
    #: ADR-0302 fix round 1: the share of the document's time limit the incumbent search may take, ON TOP of the week's
    #: budget (the certification's remaining time is measured with the search's seconds left out, so it sees what the
    #: arm without the search would see). The clock can only halt the search: a neighbourhood the clock stops is
    #: discarded and the search ends (``halted_by`` "clock"), so the traded point is always the deterministic search's
    #: result after the recorded number of neighbourhoods. The search record's ``allowance`` is that share in seconds
    #: (None: no clock), recorded beside the ``seconds`` it took (fix round 2).
    UTILITY_SEARCH_SHARE = .25
    #: The terminations of a node-limited solve that do not depend on the clock: a solve that ended so is a
    #: deterministic function of its inputs and budgets (HiGHS maps a node limit to ``maxIterations``).
    DETERMINISTIC_TERMINATIONS = ("optimal", "maxIterations")
    #: A relaxed count at least this far from the incumbent's count marks its group as one where the LP relaxation and
    #: the incumbent disagree (the RINS half of a neighbourhood is drawn from those groups).
    UTILITY_SEARCH_DISAGREE = .5
    #: HiGHS's own defaults of the options the search and the warm start set, written back after them: appsi keeps an
    #: option on its persistent HiGHS model once it was set, so deleting it from ``options`` would not undo it.
    _HIGHS_DEFAULTS = {"time_limit": float("inf"), "mip_max_nodes": 2147483647, "random_seed": 0}
    #: What the last relax_fix solve's search and warm-started certification did (None: not declared, or not reached).
    last_search = None
    last_warm_start = None

    #: How a utility program is solved (a dollar program ignores it): ``relax_fix`` (the default) solves the LP
    #: relaxation first, finds an incumbent on the integer program reduced to that relaxation's support, then
    #: certifies on the program with every action the relaxation's reduced costs rule out of any better solution
    #: fixed at zero (exact, so the final bound holds for the whole program); ``direct`` hands HiGHS the whole
    #: integer program, whose root probing on a hundred thousand interior integer variables never ends.
    UTILITY_SOLVES = ("relax_fix", "direct")
    #: Share of the time limit the incumbent search (the support-reduced program) may take before certification.
    UTILITY_INCUMBENT_SHARE = .4
    #: A relaxed action below this many contracts is outside the incumbent search's support.
    UTILITY_SUPPORT_EPS = 1e-6
    #: The most integer actions a certification solve is handed. HiGHS's root work on interior integer columns
    #: (probing, which no option turns off) grows faster than their count and ignores the time limit: measured on
    #: real 2021Q1 groups, 5,556 free actions ran 540 s against a 300 s cap and improved nothing. Above this the
    #: strategy stops at the support incumbent and certifies against the relaxation alone.
    UTILITY_CERTIFY_ACTIONS = 2000
    #: The relative and absolute gaps the support search and the certification solve stop at, whatever the document's
    #: allowance (never looser than the document's own HiGHS gaps): the document's gap is the acceptance test of the
    #: week, not a reason to stop searching early (at the document's 5% the support solve stopped up to 7.4% short
    #: of the value it could reach, a contract and more).
    UTILITY_SEARCH_REL_GAP = 1e-4
    UTILITY_SEARCH_ABS_GAP = 1e-6

    @classmethod
    def validate_params(cls, params):
        """Check explicit numerical certificate policies.

        Parameters
        ----------
        params : dict
            Proposed configuration.

        Returns
        -------
        list of str
            All defects.

        Raises
        ------
        TypeError
            Configuration is not a mapping.
        """
        problems = super().validate_params(params)
        for key in ("absolute_gap", "relative_gap", "parity_tolerance"):
            if not number_ok(params.get(key)) or params[key] < 0:
                problems.append(f"{key} must be finite and nonnegative")
        policy = params.get("timeout_policy", "raise")
        if not isinstance(policy, str) or policy not in ("raise", "return"):
            problems.append("timeout_policy must be raise or return")
        if policy == "return" and params.get("solver", DEFAULT_SOLVER) != "appsi_highs":
            problems.append("return timeout policy requires appsi_highs")
        screen = params.get("group_screen", "none")
        if not isinstance(screen, str) or screen not in ("none", "lp"):
            problems.append("group_screen must be none or lp")
        keep = params.get("group_screen_keep")
        if keep is not None and (type(keep) is not int or keep < 1 or screen != "lp"):
            problems.append("group_screen_keep must be an int >= 1 and needs group_screen lp")
        strategy = params.get("utility_solve", cls.UTILITY_SOLVES[0])
        if not isinstance(strategy, str) or strategy not in cls.UTILITY_SOLVES:
            problems.append("utility_solve must be one of "+", ".join(cls.UTILITY_SOLVES))
        problems.extend(cls.strategy_problems(params.get("utility_search"), params.get("utility_warm_start")))
        if ((params.get("utility_search") is not None or params.get("utility_warm_start") is not None)
                and strategy != "relax_fix"):
            problems.append("utility_search and utility_warm_start act inside the relax_fix utility strategy")
        return problems

    @classmethod
    def strategy_problems(cls, search, warm_start):
        """Return the defects of an ADR-0302 search and warm-start declaration (None: not declared).

        Every budget is an int (never a bool): ``iterations``, ``neighbourhood`` and both ``node_limit`` at least 1
        (``iterations`` at most 1000, ``neighbourhood`` at most 10000), ``seed`` within HiGHS's ``random_seed`` range.
        """
        problems = []

        def budget(value, low, high):
            return type(value) is int and low <= value <= high

        if search is not None and (not isinstance(search, dict) or set(search) != set(cls.UTILITY_SEARCH_KEYS)
                                   or not budget(search["iterations"], 1, 1000)
                                   or not budget(search["neighbourhood"], 1, 10000)
                                   or not budget(search["node_limit"], 1, 2147483646)
                                   or not budget(search["seed"], 0, 2147483647)):
            problems.append("utility_search must be {iterations (int 1..1000), neighbourhood (int 1..10000), "
                            "node_limit (int >= 1), seed (int 0..2**31-1)}")
        if warm_start is not None and (not isinstance(warm_start, dict)
                                       or set(warm_start) != set(cls.UTILITY_WARM_START_KEYS)
                                       or not budget(warm_start["node_limit"], 1, 2147483646)):
            problems.append("utility_warm_start must be {node_limit (int >= 1)}")
        return problems

    def _checked(self, inputs):
        """Validate and privately normalize the complete finite program."""
        defects = self.validate_params(self.params)
        if defects:
            raise ValueError("; ".join(defects))
        return self._checked_program(inputs)

    @classmethod
    def _checked_program(cls, inputs):
        """Validate and normalize a finite transport program for shared callers."""
        import copy
        import numpy as np

        if (not isinstance(inputs, dict) or set(inputs)-{"joint_rows", "utility"} != {"groups", "caps"}
                or not isinstance(inputs["groups"], list)):
            raise ValueError("inputs must contain groups and caps")
        groups, caps = copy.deepcopy(inputs["groups"]), copy.deepcopy(inputs["caps"])
        joint = copy.deepcopy(inputs.get("joint_rows", []))
        if not groups or not isinstance(caps, list):
            raise ValueError("nonempty groups and a caps list required")
        ids, duals, charges = set(), [], []
        required = {"id", "grid", "masses", "radius", "scale", "credit",
                    "losses", "capacity", "exclusive", "reserve_rows", "linear_rows"}
        for g in groups:
            if not isinstance(g, dict) or set(g)-{"risk_charge", "value_adjust"} != required:
                raise ValueError("group fields differ from contract")
            if not isinstance(g["id"], str) or not g["id"] or g["id"] in ids:
                raise ValueError("group identities must be unique")
            ids.add(g["id"])
            dual = WassersteinDual(g["grid"], g["masses"], g["radius"], g["scale"])
            duals.append(dual)
            charges.append(cls._group_charge(g))
            n = len(g["capacity"])
            if not n or any(type(v) is not int or v < 1 for v in g["capacity"]):
                raise ValueError("capacities must be positive integers")
            for name, shape in (("credit", (n,)), ("losses", (len(g["grid"]), n))):
                a = np.asarray(g[name], dtype=float)
                if a.shape != shape or not np.isfinite(a).all():
                    raise ValueError(f"invalid {name} shape or coefficients")
                g[name] = a.tolist()
            if "value_adjust" in g:
                g["value_adjust"] = cls._checked_value_adjust(g["value_adjust"], n)
            reserves = np.asarray(g["reserve_rows"], dtype=float)
            if reserves.ndim != 2 or reserves.shape[1] != n or not np.isfinite(reserves).all():
                raise ValueError("invalid reserve rows")
            for pair in g["exclusive"]:
                if (not isinstance(pair, (list, tuple)) or len(pair) != 2
                        or any(type(j) is not int or not 0 <= j < n for j in pair)
                        or pair[0] == pair[1]):
                    raise ValueError("invalid exclusive pair")
            for row in g["linear_rows"]:
                if (set(row) != {"coefficients", "upper"}
                        or not number_ok(row["upper"]) or row["upper"] < 0
                        or len(row["coefficients"]) != n
                        or any(not number_ok(c) for c in row["coefficients"])):
                    raise ValueError("invalid linear row or cash infeasible")
        for cap in caps:
            if (not isinstance(cap, dict) or set(cap) != {"groups", "upper"}
                    or not isinstance(cap["groups"], list) or not cap["groups"]
                    or len(set(cap["groups"])) != len(cap["groups"])
                    or set(cap["groups"])-ids or not number_ok(cap["upper"])
                    or cap["upper"] < 0):
                raise ValueError("invalid shared reserve cap")
        if ids != {key for c in caps for key in c["groups"]}:
            raise ValueError("every group must have a shared reserve cap")
        sizes = {g["id"]: len(g["capacity"]) for g in groups}
        cls._checked_joint(joint, sizes)
        utility = cls._checked_utility(inputs.get("utility"), groups, charges)
        return groups, caps, duals, joint, charges, utility

    @staticmethod
    def _checked_value_adjust(raw, n):
        """Return ``value_adjust`` as ``n`` finite floats; refuse any other shape or value."""
        import numbers

        import numpy as np

        if not isinstance(raw, (list, tuple)) or len(raw) != n or any(
                isinstance(v, (bool, np.bool_)) or not isinstance(v, numbers.Real) for v in raw):
            raise ValueError("invalid value_adjust shape or coefficients")
        a = np.asarray(raw, dtype=float)
        if not np.isfinite(a).all():
            raise ValueError("invalid value_adjust shape or coefficients")
        return a.tolist()

    @staticmethod
    def _value_adjust(group):
        """The one owner of a group's per-item linear objective term, ``None`` when the key is absent.

        ADR-0313 A1: a group's value is ``credit . x - cost(x) + value_adjust . x``. ``credit`` stays cash (the
        reserves, shock rows and book parity read it); the adjust is objective-only. The mirror-side rule is the
        CALLER's: the two sides of one option are separate items, so a caller that adds ``+a`` to one must add
        ``-a`` to its mirror, or a buy-and-sell round trip would be paid; this solver does not enforce it.
        Under a utility objective the adjust enters only through :meth:`_expected_profit` (the mean wealth), never
        the dispersion charge or the scenario terms; the child refuses ``calibration_with_kelly``.
        """
        adjust = group.get("value_adjust")
        return None if adjust is None else [float(v) for v in adjust]

    @classmethod
    def _effective_credit(cls, group):
        """Per-item ``credit + value_adjust`` for the objective arithmetic; ``credit`` itself when absent."""
        adjust = cls._value_adjust(group)
        credit = group["credit"]
        return credit if adjust is None else [c+a for c, a in zip(credit, adjust)]

    @staticmethod
    def _group_charge(group):
        """Return the group's priced deviation charge, None when absent or free."""
        if "risk_charge" not in group:
            return None
        charge = DeviationCharge.from_spec(group["risk_charge"], group["masses"])
        return charge if charge.active else None

    @staticmethod
    def _checked_terms(terms, sizes, what):
        """Normalize one ``group id -> coefficients`` map in place; refuse an unknown, short or non-finite one."""
        import numpy as np

        if not isinstance(terms, dict) or not terms or set(terms)-set(sizes):
            raise ValueError(f"invalid {what}")
        for key, coefficients in terms.items():
            a = np.asarray(coefficients, dtype=float)
            if a.shape != (sizes[key],) or not np.isfinite(a).all():
                raise ValueError(f"invalid {what} coefficients")
            terms[key] = a.tolist()

    @classmethod
    def _checked_joint(cls, joint, sizes):
        """Refuse a malformed, unknown, non-finite or cash-infeasible joint row."""
        if not isinstance(joint, list):
            raise ValueError("joint_rows must be a list")
        seen = set()
        for row in joint:
            if (not isinstance(row, dict) or set(row) != {"id", "terms", "upper"}
                    or not isinstance(row["id"], str) or not row["id"] or row["id"] in seen
                    or not isinstance(row["terms"], dict) or not row["terms"]
                    or set(row["terms"])-set(sizes)
                    or not number_ok(row["upper"]) or row["upper"] < 0):
                raise ValueError("invalid joint row")
            seen.add(row["id"])
            cls._checked_terms(row["terms"], sizes, "joint row")

    #: The keys of a ``utility`` block, every one explicit.
    UTILITY_KEYS = frozenset({"w0", "gamma", "n_tangents", "core", "wealth_lo", "aggregate", "joint"})
    #: How far the scenario weights may miss one in total (float sums of configured weights).
    UTILITY_WEIGHT_TOLERANCE = 1e-9

    @classmethod
    def _checked_utility(cls, utility, groups, charges):
        """Validate and normalize the optional ``utility`` block; None when absent.

        ``w0`` is the account wealth the utility is taken relative to;
        ``gamma`` the relative risk aversion handed to :func:`tangent_utility`
        (``1`` is log utility, full Kelly; ``gamma = 1/f`` sizes roughly
        ``f`` times the Kelly stake, see :func:`tangent_utility`);
        ``n_tangents`` the knots of the envelope over the whole reachable
        range and again over the core band ``[w0/core, w0*core]`` (``core``
        above 1), and of each group's charge lines; ``wealth_lo`` the
        positive floor every scenario's wealth must keep (a hard cut, so the
        utility is finite); ``aggregate`` maps EVERY group id to the key of
        the name whose risk it carries (the groups of one key are charged
        together, see :meth:`_charge_plan`); ``joint`` the scenarios,
        each ``{"id", "weight", "terms"}``, terms mapping a group id to that
        group's PROFIT per contract in the scenario beyond its expected
        profit (a scenario with no terms is the expectation itself). The
        weights sum to one. A utility objective is an expectation under the
        nominal masses: every group's radius must be zero and no risk charge
        may be priced.
        """
        if utility is None:
            return None
        if (not isinstance(utility, dict) or set(utility) != cls.UTILITY_KEYS
                or not number_ok(utility["w0"]) or utility["w0"] <= 0
                or not number_ok(utility["gamma"]) or utility["gamma"] < 1
                or type(utility["n_tangents"]) is not int or utility["n_tangents"] < 2
                or not number_ok(utility["core"]) or utility["core"] <= 1
                or not number_ok(utility["wealth_lo"]) or not 0 < utility["wealth_lo"] < utility["w0"]
                or not isinstance(utility["aggregate"], dict)
                or set(utility["aggregate"]) != {g["id"] for g in groups}
                or any(not isinstance(v, str) or not v for v in utility["aggregate"].values())
                or not isinstance(utility["joint"], list) or not utility["joint"]):
            raise ValueError("invalid utility block")
        if any(g["radius"] != 0 for g in groups) or any(charge is not None for charge in charges):
            raise ValueError("a utility objective needs radius 0 and no priced risk charge")
        sizes = {g["id"]: len(g["capacity"]) for g in groups}
        seen, total = set(), 0.
        for scenario in utility["joint"]:
            if (not isinstance(scenario, dict) or set(scenario) != {"id", "weight", "terms"}
                    or not isinstance(scenario["id"], str) or not scenario["id"] or scenario["id"] in seen
                    or not number_ok(scenario["weight"]) or scenario["weight"] < 0
                    or not isinstance(scenario["terms"], dict)):
                raise ValueError("invalid utility scenario")
            seen.add(scenario["id"])
            if scenario["terms"]:
                cls._checked_terms(scenario["terms"], sizes, "utility scenario")
            total += float(scenario["weight"])
        if abs(total-1.) > cls.UTILITY_WEIGHT_TOLERANCE:
            raise ValueError("utility scenario weights must sum to one")
        return {**utility, "w0": float(utility["w0"]), "gamma": float(utility["gamma"]), "core": float(utility["core"]),
                "wealth_lo": float(utility["wealth_lo"]), "aggregate": dict(utility["aggregate"])}

    @staticmethod
    def _expected_profit(groups):
        """Return each group's expected profit per contract under its nominal masses, ``credit - masses . losses``."""
        import numpy as np

        return [np.asarray(TransportPortfolioSolve._effective_credit(g), dtype=float)
                - np.asarray(g["masses"], dtype=float) @ np.asarray(g["losses"], dtype=float) for g in groups]

    @staticmethod
    def _utility_scenarios(groups, utility):
        """Return every utility scenario as ``(id, weight, {group index: profit coefficients beyond the expectation})``."""
        import numpy as np

        lookup = {g["id"]: i for i, g in enumerate(groups)}
        return [(scenario["id"], float(scenario["weight"]),
                 {lookup[key]: np.asarray(row, dtype=float) for key, row in scenario["terms"].items()})
                for scenario in utility["joint"]]

    @staticmethod
    def _scenario_range(expected, terms, groups, active):
        """Return the wealth change a scenario can reach over the active groups: ``(lowest, highest)``."""
        lo = hi = 0.
        for i in active:
            row = expected[i] if i not in terms else expected[i]+terms[i]
            for coefficient, capacity in zip(row, groups[i]["capacity"]):
                lo += min(0., float(coefficient))*capacity
                hi += max(0., float(coefficient))*capacity
        return lo, hi

    @classmethod
    def _utility_envelope(cls, groups, utility, expected, scenarios):
        """Return the one tangent envelope of a program: knots from ``wealth_lo`` to the highest reachable wealth."""
        every = range(len(groups))
        highest = max((cls._scenario_range(expected, terms, groups, every)[1] for _id, _w, terms in scenarios),
                      default=0.)
        return _TangentEnvelope(utility["w0"], utility["gamma"], utility["wealth_lo"],
                                utility["w0"]+highest, utility["n_tangents"], utility["core"])

    #: A normal's standard deviation over its mean absolute deviation, ``sqrt(pi / 2)`` to two places: ``S = 1.25 x MAD``.
    SD_PER_MAD = 1.25

    @classmethod
    def _charge_plan(cls, groups, utility):
        """Return the name-level dispersion charge: each group's atoms, its deviation charge and the tangent lines.

        A group's dispersion ``M_g`` is read for the book the solve chooses, over EVERY positive-mass atom of its
        grid in price order and no reference book (:class:`_AtomDeviation`; pure arithmetic, no LP, no solver,
        the same on every host): the larger of ``1.25 x`` the mean absolute deviation of the book's payoff, a
        normal's sd over its MAD (``SD_PER_MAD``), and the lower bounds on its sd at each single atom and over
        every lowest and every highest set of atoms (Cauchy-Schwarz), so a rare loss cannot fall between
        points and no direction of the actions has a zero charge unless its payoff is the same at every atom.
        The payoff is the book's terminal payoff ``-losses . x`` (the credit is the same at every atom and
        cancels in a deviation, and an action that pays at only some atoms ties in only those). The groups of
        one ``aggregate`` key move together: with ``S_a = sum_g M_g`` the charge is
        ``gamma x S_a^2 / (2 w0)``, the variance of perfectly correlated books, so a key cannot cut its charge
        by splitting its book across groups. Its tangent lines run to the ceiling ``S_a`` cannot pass,
        ``sum_g sum_j capacity_j x (item j's own M per contract)`` (``M`` is a seminorm in the actions, so the
        triangle inequality bounds it). A group whose atoms never differ carries no charge.
        Returns ``{"rows": {index: atoms x items payoff array}, "charge": {index: _AtomDeviation},
        "members": {key: [index]}, "lines": {key: _ChargeLines}}``.
        """
        import numpy as np

        rows, charge, members, ceiling = {}, {}, {}, {}
        for i, g in enumerate(groups):
            masses = np.asarray(g["masses"], dtype=float)
            atoms = [k for k in np.argsort(np.asarray(g["grid"], dtype=float), kind="stable") if masses[k] > 0]
            if len(atoms) < 2:
                continue
            payoff = -np.asarray(g["losses"], dtype=float)[atoms]
            probability = masses[atoms]/masses[atoms].sum()
            measure = _AtomDeviation(probability, cls.SD_PER_MAD)
            own = measure.columns(payoff)   # each item's own D per contract
            if own.max() <= 1e-9*max(1., float(np.abs(payoff).max())):
                continue
            rows[i], charge[i] = payoff, measure
            members.setdefault(utility["aggregate"][g["id"]], []).append(i)
            ceiling[i] = cls.SD_PER_MAD*float(own @ np.asarray(g["capacity"], dtype=float))
        slope = utility["gamma"]/(2.*utility["w0"])
        lines = {name: _ChargeLines(slope, sum(ceiling[i] for i in indices), utility["n_tangents"])
                 for name, indices in members.items()}
        return {"rows": rows, "charge": charge, "members": members, "lines": lines}

    @staticmethod
    def _group_lp_bound(group):
        """Upper-bound one group's best value by its own LP relaxation, caps ignored.

        The relaxation drops integrality only; it keeps capacities, linear
        rows, the sparse W1 dual and a priced risk_charge epigraph, so it is
        a valid bound on every integer book of the group and is exact at
        radius zero up to integrality. A charge only lowers the bound.
        """
        return TransportPortfolioSolve._group_lp_relaxation(group)[0]

    @staticmethod
    def _group_lp_relaxation(group):
        """Return ``(bound, actions)``: :meth:`_group_lp_bound`'s value and the relaxation's fractional actions.

        One LP, solved once: the bound is exactly the screen's, and the actions (one float per item, within the
        capacities) are the relaxed book that attains it (ADR-0315 rounds them into local-search starts).
        """
        import numpy as np
        from scipy.optimize import linprog
        from scipy.sparse import csr_matrix, lil_matrix

        grid = np.asarray(group["grid"], dtype=float)
        losses = np.asarray(group["losses"], dtype=float)
        n, m = len(group["capacity"]), len(grid)
        charge = TransportPortfolioSolve._group_charge(group)
        extra = None if charge is None else charge.relaxation(losses)
        aux = 0 if extra is None else len(extra["bounds"])
        width = n+m+1+aux  # actions, potentials, gamma, charge auxiliaries
        rows = lil_matrix((m+2*(m-1)+len(group["linear_rows"])
                           + (0 if extra is None else len(extra["ub"][0])), width))
        rhs, r = [], 0
        for k in range(m):
            rows[r, :n] = losses[k]
            rows[r, n+k] = -1.
            rhs.append(0.)
            r += 1
        distance = np.diff(grid)/float(group["scale"])
        for j in range(m-1):
            for sign in (1., -1.):
                rows[r, n+j+1] = sign
                rows[r, n+j] = -sign
                rows[r, n+m] = -distance[j]
                rhs.append(0.)
                r += 1
        for row in group["linear_rows"]:
            rows[r, :n] = np.asarray(row["coefficients"], dtype=float)
            rhs.append(float(row["upper"]))
            r += 1
        cost = np.r_[-np.asarray(TransportPortfolioSolve._effective_credit(group), dtype=float),
                     np.asarray(group["masses"], dtype=float), float(group["radius"])]
        bounds = ([(0., float(c)) for c in group["capacity"]]
                  + [(None, None)]*m + [(0., None)])
        equalities = {}
        if extra is not None:
            for action, auxiliary in zip(*extra["ub"]):
                rows[r, :n] = action
                for t in np.flatnonzero(auxiliary):
                    rows[r, n+m+1+t] = auxiliary[t]
                rhs.append(0.)
                r += 1
            cost = np.r_[cost, extra["cost"]]
            bounds += extra["bounds"]
            if len(extra["eq"][0]):
                filler = np.zeros((len(extra["eq"][0]), m+1))
                equalities = {"A_eq": csr_matrix(np.hstack([extra["eq"][0], filler, extra["eq"][1]])),
                              "b_eq": np.zeros(len(extra["eq"][0]))}
        result = linprog(cost, A_ub=csr_matrix(rows), b_ub=np.asarray(rhs),
                         bounds=bounds, method="highs", **equalities)
        if not result.success or not np.isfinite(result.fun):
            raise ValueError(f"group LP relaxation failed: {result.message}")
        return float(-result.fun), np.asarray(result.x[:n], dtype=float)

    #: Optional :class:`dskit.pipeline.value_cache.VerifiedValueCache` of whole-screen group LP bounds. None (the
    #: default) solves every group's LP; a caller that sets it gets the same bounds bit for bit, read back for a
    #: program whose checked groups, this module's source, the LP functions and the runtime versions are unchanged.
    screen_cache = None

    def _screen_key(self, groups):
        """The screen cache key of ``groups``, or None when the LP functions are not this module's own."""
        import sys
        from dskit.pipeline.value_cache import key_digest, plain_digest, runtime_versions, source_digest

        here = TransportPortfolioSolve.__dict__
        if (here.get("_group_lp_bound") is not _SCREEN_LP_FUNCTIONS[0]
                or here.get("_group_lp_relaxation") is not _SCREEN_LP_FUNCTIONS[1]
                or type(self)._group_lp_bound is not TransportPortfolioSolve._group_lp_bound):
            return None  # a replaced bound function (a test double, a subclass) is never cached
        return key_digest("transport-group-lp-bounds/1", source_digest([sys.modules[__name__].__file__]),
                          runtime_versions("numpy", "scipy", "highspy"), plain_digest(groups))

    def _screen_bounds(self, groups):
        """Each group's :meth:`_group_lp_bound`, read from :attr:`screen_cache` when the same program was screened."""
        from dskit.pipeline.value_cache import MISS, PlainValueError

        cache = self.screen_cache
        key = None
        if cache is not None:
            try:
                key = self._screen_key(groups)
            except PlainValueError:
                key = None  # a program holding non-plain values is screened, never cached
            if key is not None:
                found = cache.load(key)
                if (found is not MISS and isinstance(found, list) and len(found) == len(groups)
                        and all(type(b) is float for b in found)):
                    return found
        bounds = [self._group_lp_bound(group) for group in groups]
        if key is not None:
            cache.store(key, bounds)
        return bounds

    def _screen_metadata(self, groups):
        """Return the shared screen decision and its bound metadata."""
        screen = self.params.get("group_screen", "none")
        if screen == "lp":
            bounds = self._screen_bounds(groups)
            # A tolerance, not a strict zero: LP noise of 1e-8 must not change
            # which groups a replay keeps across solver builds.
            active = [i for i, bound in enumerate(bounds)
                      if bound > self.params["parity_tolerance"]]
            fallback = None
            if not active:
                # Keep the best group so that a solver certificate still exists.
                fallback = max(range(len(groups)), key=lambda i: bounds[i])
                active = [fallback]
            keep = self.params.get("group_screen_keep")
            if keep is not None and len(active) > keep:
                # ADR-0301: of the live groups keep the ``keep`` with the largest standalone relaxed value (ties by
                # index), a candidate restriction like the screen itself; the certificate covers the kept program.
                active = sorted(sorted(active, key=lambda i: (-bounds[i], i))[:keep])
            self.log.info("group screen kept %d of %d groups", len(active), len(groups))
        else:
            bounds = [None]*len(groups)
            active = list(range(len(groups)))
            fallback = None
        active_set = set(active)
        # The last screen's size, for a caller to record beside its decision (not only the log line above).
        self.last_screen = {"rule": screen, "candidates": len(groups), "kept": len(active),
                            "screened": len(groups)-len(active), "fallback": fallback}
        if self.params.get("group_screen_keep") is not None:
            # Only a program that declares the cap records it: a kelly result recorded before ADR-0301 replays unchanged.
            self.last_screen["keep"] = self.params["group_screen_keep"]
        return {
            "screen": screen,
            "parity_tolerance": self.params["parity_tolerance"],
            "groups": [
                {"index": index, "id": group["id"], "lp_bound": bounds[index],
                 "active": index in active_set}
                for index, group in enumerate(groups)
            ],
            "active_indices": active,
            "active_ids": [groups[index]["id"] for index in active],
            "fallback_index": fallback,
        }

    def candidate_screen(self, inputs):
        """Describe the exact candidate-group screen used by model construction.

        Parameters
        ----------
        inputs : dict
            A checked transport portfolio program with groups, caps and optional
            joint rows.

        Returns
        -------
        dict
            Screen name, tolerance, each group's identity/index/LP bound and
            active status, the active identities/indices, and any fallback index.
            The result is independent of integer portfolio solving.

        Raises
        ------
        ValueError
            Program or screen parameters are invalid, or a group LP fails.
        """
        groups, _caps, _duals, _joint, _charges, _utility = self._checked(inputs)
        return self._screen_metadata(groups)

    def _screened(self, groups):
        """Return the indices of the groups the joint model keeps."""
        return self._screen_metadata(groups)["active_indices"]

    @staticmethod
    def _costly_roundtrip(group, a, b, joint=()):
        """Prove that cancelling equal opposite quantities improves the program.

        Exact comparisons are required: a tiny residual can become material at
        large capacities. Subtraction comparisons avoid overflowing pair sums.
        ``joint`` holds the group's coefficient lists from every joint row:
        a pair that loosens one (a negative pair sum) is not a pure cost.
        """
        effective = TransportPortfolioSolve._effective_credit(group)
        return (all(row[a] == -row[b] for row in group["losses"])
                and effective[a] < -effective[b]
                and all(float(row[a]) >= -float(row[b]) for row in group["reserve_rows"])
                and all(float(row["coefficients"][a]) >= -float(row["coefficients"][b])
                        for row in group["linear_rows"])
                and all(float(row[a]) >= -float(row[b]) for row in joint))

    def _joint_terms(self, key):
        """Return one group's LOSS coefficients from each joint row and utility scenario that names it.

        A utility scenario states profit per contract; negated here so every
        row reads as a loss and one round-trip test serves both.
        """
        rows = [row["terms"][key] for row in self._joint if key in row["terms"]]
        if self._utility is not None:
            rows.extend([-float(c) for c in scenario["terms"][key]]
                        for scenario in self._utility["joint"] if key in scenario["terms"])
        return rows

    def _attach_utility(self, m, groups, active):
        """Add the scenario wealths, the tangent rows, the charges and the W0-scaled expected utility to ``m``.

        ``mean`` is the active groups' expected profit, written once; one
        ``W[s]`` and ``U[s]`` per scenario, ``W[s] = w0 + mean + terms . x``,
        bounded to the wealth the scenario can reach (so ``wealth_lo`` is a
        hard cut) with only the tangents that can be the least line in that
        range. Each active group with a dispersion adds the epigraph rows of its deviation charge
        (``dispersion_i``: 2K+1 rows over its own items and K+1 auxiliaries); one ``charge[a]`` per
        name above the tangent lines of its summed dispersion. The objective is ``w0`` times the expected envelope
        less the charges, so its units are dollars to first order and the gap
        policies keep their meaning. Rows grow with the scenarios and the
        groups, never with the groups' grid points.
        """
        from pyomo.environ import ConstraintList, Expression, NonNegativeReals, Reals, Var

        utility = self._utility
        expected = self._expected_profit(groups)
        scenarios = self._utility_scenarios(groups, utility)
        envelope = self._utility_envelope(groups, utility, expected, scenarios)
        plan = self._charge_plan(groups, utility)
        # A name with an active member: one summed dispersion, n_tangents sparse lines.
        names = sorted(name for name, indices in plan["members"].items() if set(indices) & set(active))
        bounds = {}
        for s, (_id, _weight, terms) in enumerate(scenarios):
            lo, hi = self._scenario_range(expected, terms, groups, active)
            bounds[s] = (max(envelope.lo, utility["w0"]+lo), min(envelope.hi, utility["w0"]+hi))
        m.mean = Var(domain=Reals)
        m.W = Var(list(bounds), domain=Reals, bounds=lambda m, s: bounds[s])
        m.U = Var(list(bounds), domain=Reals)
        m.charge = Var(names, domain=NonNegativeReals)
        # An aggregate dispersion variable only for a key with more than one active group.
        m.total = Var([n for n in names if len([i for i in plan["members"][n] if i in active]) > 1],
                      domain=NonNegativeReals)
        m.wealth = ConstraintList()
        m.tangent = ConstraintList()
        m.charges = ConstraintList()
        m.wealth.add(m.mean == sum(float(c)*m.x[i, j] for i in active
                                   for j, c in enumerate(expected[i]) if c != 0))
        for s, (_id, _weight, terms) in enumerate(scenarios):
            m.wealth.add(m.W[s] == utility["w0"]+m.mean+sum(
                float(c)*m.x[i, j] for i, row in terms.items() if i in active
                for j, c in enumerate(row) if c != 0))
            for j in envelope.live(*bounds[s]):
                m.tangent.add(m.U[s] <= float(envelope.intercept[j])+float(envelope.slope[j])*m.W[s])
        spread = {}
        for i in active:
            if i in plan["rows"]:
                # The charge on the chosen book: |cell_k . x - mean| at the group's K cells, S_g = 1.25 x their MAD.
                cells = [sum(float(c)*m.x[i, j] for j, c in enumerate(row) if c != 0) for row in plan["rows"][i]]
                spread[i] = plan["charge"][i].attach(m, cells, f"dispersion_{i}").cost
        for name in names:
            live = [i for i in plan["members"][name] if i in active]
            if len(live) == 1:
                # One group: its lines read its dispersion directly, no aggregate variable or equality row.
                total = spread[live[0]]
            else:
                m.charges.add(m.total[name] == sum(spread[i] for i in live))
                total = m.total[name]
            for slope, intercept in plan["lines"][name].lines:
                m.charges.add(m.charge[name] >= slope*total+intercept)
        m.profit = Expression(expr=utility["w0"]*sum(weight*m.U[s] for s, (_id, weight, _t) in enumerate(scenarios))
                              - sum(m.charge[name] for name in names))

    def build_model(self, inputs, params):
        """Build a joint finite bounded integer program.

        Parameters
        ----------
        inputs, params : dict
            Prepared program and configured solver policies.

        Returns
        -------
        object
            Pyomo model, with cash feasible.

        Raises
        ------
        ValueError
            A program coefficient, identity or bound is invalid.
        """
        from pyomo.environ import (
            Binary, ConcreteModel, ConstraintList, Expression, NonNegativeIntegers,
            NonNegativeReals, Objective, Var, maximize,
        )

        groups, caps, duals, joint, charges, utility = self._checked(inputs)
        self._groups, self._caps, self._duals, self._joint = groups, caps, duals, joint
        self._charges, self._utility = charges, utility
        self._charged = any(charge is not None for charge in charges)
        self._active = active = self._screened(groups)
        m = ConcreteModel()
        indices = [(i, j) for i in active for j in range(len(groups[i]["capacity"]))]
        m.x = Var(indices, domain=NonNegativeIntegers,
                  bounds=lambda m, i, j: (0, groups[i]["capacity"][j]))
        m.reserve = Var(active, domain=NonNegativeReals)
        self._redundant = {(i, k) for i, g in enumerate(groups)
                           for k, (a, b) in enumerate(g["exclusive"])
                           if self._costly_roundtrip(g, a, b, self._joint_terms(g["id"]))}
        pairs = [(i, k) for i in active for k in range(len(groups[i]["exclusive"]))
                 if (i, k) not in self._redundant]
        m.side = Var(pairs, domain=Binary)
        m.rows = ConstraintList()
        values = []
        for i in active:
            g, dual = groups[i], duals[i]
            def dot(row):
                return sum(float(c)*m.x[i, j] for j, c in enumerate(row) if c != 0)
            for k, (a, b) in enumerate(g["exclusive"]):
                if (i, k) in self._redundant:
                    continue
                m.rows.add(m.x[i, a] <= g["capacity"][a]*m.side[i, k])
                m.rows.add(m.x[i, b] <= g["capacity"][b]*(1-m.side[i, k]))
            for row in g["reserve_rows"]:
                m.rows.add(m.reserve[i] >= dot(row))
            for row in g["linear_rows"]:
                expr = dot(row["coefficients"])
                if isinstance(expr, (int, float)):
                    continue
                m.rows.add(expr <= row["upper"])
            losses = [dot(row) for row in g["losses"]]
            if g["radius"] == 0:
                cost = sum(float(p)*loss for p, loss in zip(g["masses"], losses))
            else:
                cost = dual.attach(m, losses, name=f"transport_{i}", sparse=True).cost
            value = dot(g["credit"])-cost
            adjust = self._value_adjust(g)
            if adjust is not None:
                value = value+dot(adjust)
            if charges[i] is not None:
                value = value-charges[i].attach(m, losses, name=f"risk_{i}").cost
            values.append(value)
        lookup = {g["id"]: i for i, g in enumerate(groups)}
        for cap in caps:
            members = [lookup[key] for key in cap["groups"] if lookup[key] in m.reserve]
            if members:
                m.rows.add(sum(m.reserve[i] for i in members) <= cap["upper"])
        for row in joint:
            expr = sum(float(c)*m.x[lookup[key], j] for key, coefficients in row["terms"].items()
                       if lookup[key] in m.reserve for j, c in enumerate(coefficients) if c != 0)
            if not isinstance(expr, (int, float)):
                m.rows.add(expr <= row["upper"])
        if utility is None:
            m.profit = Expression(expr=sum(values))
        else:
            self._attach_utility(m, groups, list(active))
        m.objective = Objective(expr=m.profit, sense=maximize)
        for variable in m.component_data_objects(Var):
            # Cash: nothing held, and every scenario wealth at w0.
            variable.set_value(utility["w0"] if utility is not None and variable.parent_component() is m.W else 0)
        return m

    def _solve(self, solver, model):
        """Solve the model: a utility program by its declared strategy, a dollar program directly."""
        if (getattr(self, "_utility", None) is not None
                and self.params.get("utility_solve", self.UTILITY_SOLVES[0]) == "relax_fix"):
            return self._solve_relax_fix(solver, model)
        if getattr(self, "_utility", None) is not None:
            model._transport_outcome, model._transport_bound_source = "direct", "solver"
        return self._solve_once(solver, model)

    def _solve_once(self, solver, model, time_limit=None):
        """Offer a feasible cash start to a capable backend; ``time_limit`` overrides the configured one for this call."""
        capability = getattr(solver, "warm_start_capable", None)
        warm = callable(capability) and capability()
        if time_limit is not None:
            solver.options["time_limit"] = max(1., float(time_limit))
        if self.params.get("timeout_policy", "raise") == "return":
            from pyomo.environ import Var
            options = {"warmstart": True} if warm else {}
            result = solver.solve(model, load_solutions=False, **options)
            model._transport_no_incumbent = not bool(len(result.solution))
            if model._transport_no_incumbent:
                for variable in model.component_data_objects(Var):
                    variable.set_value(None)
            else:
                model.solutions.load_from(result)
            return result
        if warm:
            return solver.solve(model, warmstart=True)
        return super()._solve(solver, model)

    def _relaxation(self, solver, model, time_limit):
        """Solve the LP relaxation; return ``(value, {action: relaxed value}, {action: reduced cost})`` or None.

        Integer domains are made continuous for the solve and restored after it, whatever happens. Reduced costs
        come from HiGHS through an imported ``rc`` suffix.
        """
        from pyomo.common.collections import ComponentMap
        from pyomo.environ import Reals, Suffix, Var, value

        domains = ComponentMap()
        for variable in model.component_data_objects(Var, active=True):
            if variable.is_integer():
                lower, upper = variable.bounds   # the domain's own bounds (a Binary's 0 and 1) made explicit
                domains[variable] = variable.domain
                variable.setlb(lower)
                variable.setub(upper)
                variable.domain = Reals
        model.rc = Suffix(direction=Suffix.IMPORT)
        try:
            if time_limit is not None:
                solver.options["time_limit"] = max(1., float(time_limit))
            try:
                results = solver.solve(model)
            except RuntimeError:   # appsi raises when the relaxation has no solution to load
                return None
            if str(getattr(results.solver, "termination_condition", "")) != "optimal":
                return None
            bound = float(value(model.profit))
            relaxed = {key: float(v.value or 0.) for key, v in model.x.items()}
            costs = {key: float(model.rc.get(v, 0.) or 0.) for key, v in model.x.items()}
        finally:
            model.del_component(model.rc)
            for variable, domain in domains.items():
                variable.domain = domain
        return bound, relaxed, costs

    def _search_gaps(self, solver):
        """A context that tightens the solver's MIP gaps to the strategy's own small ones, and restores the document's."""
        import contextlib

        @contextlib.contextmanager
        def tightened():
            wanted = {"mip_rel_gap": self.UTILITY_SEARCH_REL_GAP, "mip_abs_gap": self.UTILITY_SEARCH_ABS_GAP}
            saved = {key: solver.options.get(key) for key in wanted}
            for key, small in wanted.items():
                solver.options[key] = small if saved[key] is None else min(small, saved[key])
            try:
                yield
            finally:
                for key, value in saved.items():
                    if value is None:
                        del solver.options[key]
                    else:
                        solver.options[key] = value
        return tightened()

    def _highs_options(self, solver, wanted):
        """A context that sets HiGHS options for the solves inside it and writes back the document's, or HiGHS's defaults.

        A key the document never set is written back as HiGHS's own default (:attr:`_HIGHS_DEFAULTS`), not deleted:
        appsi keeps an option on its persistent model once it was set. An empty ``wanted`` touches nothing.
        """
        import contextlib

        @contextlib.contextmanager
        def held():
            saved = {key: solver.options.get(key) for key in wanted}
            for key, value in wanted.items():
                solver.options[key] = value
            try:
                yield
            finally:
                for key, value in saved.items():
                    solver.options[key] = self._HIGHS_DEFAULTS[key] if value is None else value
        return held()

    @staticmethod
    def _snapshot(model):
        """Every variable of ``model`` with its loaded value (reloaded by :meth:`_reload`)."""
        from pyomo.environ import Var

        return [(v, v.value) for v in model.component_data_objects(Var)]

    @staticmethod
    def _reload(model, saved):
        """Reload a :meth:`_snapshot`; the loaded point is an incumbent again."""
        for variable, value in saved:
            variable.set_value(value, skip_validation=True)
        model._transport_no_incumbent = False

    def _exact_utility(self, model):
        """The exact sampled utility of the loaded point (the audit's value, :meth:`_incumbent`), or None.

        The audit's cleanup writes ``x``; the solver's own point is reloaded after it, so what is kept is never the
        cleaned point (the week's own audit cleans it the same way later).
        """
        if getattr(model, "_transport_no_incumbent", False):
            return None
        saved = self._snapshot(model)
        try:
            return self._incumbent(model)["value"]
        except ValueError:
            return None
        finally:
            self._reload(model, saved)

    def _search_incumbent(self, solver, model, bound, stakes, seconds=None):
        """ADR-0302: improve the loaded incumbent by exact sampled utility over seeded neighbourhoods; return the record.

        Each of at most ``iterations`` neighbourhoods frees ``neighbourhood`` groups (half drawn from the groups whose
        LP relaxed counts and incumbent counts disagree, RINS-style, the rest from the other groups) and fixes every
        other group at the incumbent's counts. The restricted program is solved from the complete incumbent (a MIP
        start through appsi's warm start) under HiGHS's node limit ``node_limit``, with ``random_seed`` the declared
        seed, so the same inputs and declaration give the same neighbourhoods and solves. A candidate is kept only
        when its exact sampled utility (:meth:`_exact_utility`, the audit the week's selection is valued by:
        :class:`TransportActionScore` after the cleanup) beats the incumbent's by more than the parity tolerance;
        otherwise the incumbent is reloaded. The search stops early once the incumbent is within the document's
        allowance of the relaxation's bound (nothing left to find). Every bound is restored after it and the best
        incumbent is left loaded. Nothing here certifies anything: the bound stays the relaxation's.

        ``seconds`` (None: no clock) is the search's own wall-clock allowance. The clock can only halt the search:
        once it is spent no further neighbourhood starts, and a neighbourhood solve the clock stops (termination
        ``maxTimeLimit``) is discarded, so the incumbent left is the deterministic search's after the neighbourhoods
        the record counts (``halted_by`` "clock"). A neighbourhood whose solve raises is counted in ``failed`` and
        logged, never silently taken for a rejected one.
        """
        import math
        import random

        search = self.params["utility_search"]
        tolerance = max(self.params["parity_tolerance"], 1e-9)
        groups = sorted({i for i, _ in model.x})
        members = {i: [] for i in groups}
        for key in model.x:
            members[key[0]].append(key)
        bounds = {key: (model.x[key].lb, model.x[key].ub) for key in model.x}
        started = time.perf_counter()
        best, before = self._snapshot(model), self._exact_utility(model)
        record = {"seed": search["seed"], "iterations": 0, "improved": False, "value_before": before,
                  "value_after": before, "accepted_neighbourhoods": [], "budget_exhausted": False,
                  "halted_by": None, "failed": 0, "seconds": 0.,
                  "allowance": (None if seconds is None or not math.isfinite(seconds)
                                else round(float(seconds), 3))}
        if before is None:
            return record
        rng = random.Random(search["seed"])
        size = min(search["neighbourhood"], len(groups))
        stopped = False
        try:
            for k in range(search["iterations"]):
                current = self._objective_value(model)
                if current is not None and bound-current <= max(self.params["absolute_gap"],
                                                                  self.params["relative_gap"]*abs(current)):
                    stopped = True
                    break
                left = None if seconds is None else seconds-(time.perf_counter()-started)
                if left is not None and left < 1.:
                    record["halted_by"] = "clock"
                    break
                counts = {key: round(model.x[key].value or 0) for key in model.x}
                disagree = [i for i in groups if any(abs(stakes[key]-counts[key]) >= self.UTILITY_SEARCH_DISAGREE
                                                     for key in members[i])]
                near = rng.sample(disagree, min(len(disagree), math.ceil(size/2)))
                taken = set(near)
                rest = [i for i in groups if i not in taken]
                chosen = sorted(near+rng.sample(rest, size-len(near)))
                taken = set(chosen)
                for key in model.x:
                    if key[0] in taken:
                        model.x[key].setlb(bounds[key][0])
                        model.x[key].setub(bounds[key][1])
                    else:
                        model.x[key].setlb(counts[key])
                        model.x[key].setub(counts[key])
                record["iterations"] = k+1
                try:
                    with self._search_gaps(solver), self._highs_options(solver, {
                            "time_limit": float("inf") if left is None else max(1., left),
                            "mip_max_nodes": search["node_limit"], "random_seed": search["seed"]}):
                        result = self._solve_once(solver, model)
                    termination = self._termination(result)
                    halted = termination == "maxTimeLimit"
                    value = None
                    if termination in self.DETERMINISTIC_TERMINATIONS:
                        value = self._exact_utility(model)
                    elif not halted:
                        record["failed"] += 1
                        self.log.warning("utility search: neighbourhood %d ended %s; the incumbent stands", k,
                                         termination)
                except (RuntimeError, ValueError) as error:
                    halted, value = False, None
                    record["failed"] += 1
                    self.log.warning("utility search: neighbourhood %d failed (%s: %s); the incumbent stands", k,
                                     type(error).__name__, error)
                if value is not None and value > record["value_after"]+tolerance:
                    best = self._snapshot(model)
                    record["value_after"] = value
                    record["accepted_neighbourhoods"].append(
                        {"iteration": k, "groups": [self._groups[i]["id"] for i in chosen], "value": value})
                else:
                    self._reload(model, best)
                if halted:
                    record["halted_by"] = "clock"
                    break
        finally:
            for key, (lower, upper) in bounds.items():
                model.x[key].setlb(lower)
                model.x[key].setub(upper)
            self._reload(model, best)
        record["improved"] = bool(record["accepted_neighbourhoods"])
        record["budget_exhausted"] = (not stopped and record["halted_by"] is None
                                      and record["iterations"] == search["iterations"])
        record["seconds"] = round(time.perf_counter()-started, 3)
        self.log.info("utility search: %d of %d neighbourhoods of %d groups, %d accepted, %d failed, halted by %s, "
                      "%.0fs", record["iterations"], search["iterations"], size, len(record["accepted_neighbourhoods"]),
                      record["failed"], record["halted_by"], record["seconds"])
        return record

    @staticmethod
    def _termination(result):
        """A solve's termination condition as a string (``maxTimeLimit`` is the clock, ``maxIterations`` a node limit)."""
        return str(getattr(getattr(result, "solver", None), "termination_condition", None))

    def _warm_start_record(self, model, result, supplied, start, over, tolerance, value_before):
        """ADR-0302: what the certification did with the incumbent it was handed (its MIP start).

        ``supplied``: every variable held the incumbent's value when the certification began, so appsi handed HiGHS
        the complete incumbent (``Highs.setSolution``). ``accepted``: the certification's own incumbent is at least as
        good as that start (HiGHS kept it or beat it). ``over_cap``: the certification ran above the action limit
        because the warm start was declared. ``termination``: how the certification solve ended. ``value_before`` and
        ``value_after``: the exact sampled utility of the start and of the certification's own incumbent (None: none).
        ``kept``: set by the strategy when an unproved over-cap certification's better incumbent is traded uncertified.
        """
        reached = None if getattr(model, "_transport_no_incumbent", False) else self._objective_value(model)
        return {"supplied": bool(supplied),
                "accepted": bool(supplied and start is not None and reached is not None and reached >= start-tolerance),
                "over_cap": bool(over),
                "termination": self._termination(result),
                "value_before": value_before, "value_after": self._exact_utility(model), "kept": False}

    def _warm_certified(self, model, result, bound, costs, fixed):
        """Whether a warm-started certification above the action limit proved the document's gap (ADR-0302).

        Proved: HiGHS ended ``optimal`` with an incumbent, and the bound its certificate will carry (the relaxation's,
        or the solver's widened to the fixed actions' reach, whichever is tighter, as :meth:`_build_solve_record` takes
        it) is within the document's allowance of the incumbent's model value (never above its audited value).
        """
        if getattr(model, "_transport_no_incumbent", False):
            return False
        if str(getattr(getattr(result, "solver", None), "termination_condition", "")) != "optimal":
            return False
        reached, own = self._objective_value(model), SolveRecord._bound(result, -1)
        if reached is None or own is None:
            return False
        outside = max((bound+costs[key] for key in fixed), default=None)
        proved = min(bound, own if outside is None else max(own, outside))
        return proved-reached <= max(self.params["absolute_gap"], self.params["relative_gap"]*abs(reached))

    def _relaxation_failed(self, model):
        """A week whose relaxation did not solve: refused outside ``return`` mode, else a no-incumbent timeout.

        The direct solve never finishes on a real week (ADR-0299 speed amendment), so it is not the fallback; the
        certificate's ``strategy_outcome`` carries the reason.
        """
        from types import SimpleNamespace
        from pyomo.environ import Var

        if self.params.get("timeout_policy", "raise") != "return":
            raise ValueError("the utility relaxation did not solve (failed or no time left); the direct integer "
                             "solve is not a fallback: declare timeout_policy return to skip the week")
        self.log.info("utility relaxation did not solve: the week is a no-incumbent timeout")
        for variable in model.component_data_objects(Var):
            variable.set_value(None)
        model._transport_no_incumbent = True
        model._transport_outcome, model._transport_bound_source = "relaxation_failed", "none"
        return SimpleNamespace(solver=SimpleNamespace(status="aborted", termination_condition="maxTimeLimit"),
                               problem=None, solution=())

    def _solve_relax_fix(self, solver, model):
        """The utility strategy: relax, search the support for an incumbent, certify on the reduced-cost-fixed program.

        Any action with no stake in the relaxed optimum is left out of the incumbent search (a restriction that
        only serves to find a good integer point fast). For the certification every action whose reduced cost
        rules it out of any solution better than the incumbent (``bound + rc_j < incumbent``, an LP fact that
        every integer solution obeys) is fixed at zero: the fixed program holds every better solution, so its
        own bound, and the relaxation's, hold for the whole program; ``model._transport_bound`` carries the
        tighter of the two into the certificate. The search and the certification stop at the strategy's own small
        gaps (:attr:`UTILITY_SEARCH_REL_GAP`), the document's gap being only the acceptance test. A certification
        that ends without a solution keeps the support incumbent. A relaxation that does not solve is a skipped
        week (:meth:`_relaxation_failed`), never a direct solve. Counts and seconds are logged; no value is.
        """
        from pyomo.environ import Var

        model._transport_bound, model._transport_declined = None, False
        model._transport_outcome, model._transport_bound_source = "certified", "certification"
        self.last_search = self.last_warm_start = None
        budget = float(self.params.get("solver_options", {}).get("time_limit", 0) or 0)
        clock, searched = time.perf_counter(), 0.
        # The search's own seconds (ADR-0302) are left out of the time the rest of the strategy sees (``searched`` is
        # rebound after it), so the certification gets what it would get without the search.
        remaining = lambda: None if not budget else max(1., budget-(time.perf_counter()-clock-searched))
        relaxed = self._relaxation(solver, model, remaining())
        if relaxed is None:
            return self._relaxation_failed(model)
        bound, stakes, costs = relaxed
        lp_seconds = time.perf_counter()-clock
        tolerance = max(self.params["parity_tolerance"], 1e-9)
        capacity = {key: model.x[key].ub for key in model.x}
        outside = [key for key, stake in stakes.items() if stake < self.UTILITY_SUPPORT_EPS]
        for key in outside:
            model.x[key].setub(0)
        for variable in model.component_data_objects(Var, active=True):
            if not variable.fixed:   # a cash start (every scenario wealth at w0); the relaxed point is no integer start
                variable.set_value(self._utility["w0"] if variable.parent_component() is model.W else 0)
        share = None if not budget else max(1., self.UTILITY_INCUMBENT_SHARE*budget)
        with self._search_gaps(solver):
            result = self._solve_once(solver, model, None if share is None else min(share, remaining()))
        search_seconds = time.perf_counter()-clock-lp_seconds
        incumbent = None if getattr(model, "_transport_no_incumbent", False) else self._objective_value(model)
        for key in outside:
            model.x[key].setub(capacity[key])
        if self.params.get("utility_search") is not None:
            # ADR-0302: improve the support incumbent by exact sampled utility before anything certifies it; a better
            # incumbent also lets the reduced costs below fix more actions. Its clock is its own share, charged to
            # the week on top of the document's budget; without a support incumbent it records that it never ran.
            began = time.perf_counter()
            self.last_search = self._search_incumbent(
                solver, model, bound, stakes, None if not budget else self.UTILITY_SEARCH_SHARE*budget)
            searched = time.perf_counter()-began
            if incumbent is not None:
                incumbent = self._objective_value(model)
        if incumbent is not None:
            allowance = max(self.params["absolute_gap"], self.params["relative_gap"]*abs(incumbent))
            if bound-incumbent <= allowance:
                # The support program's own bound covers only the support: the relaxation is the certificate.
                model._transport_bound, model._transport_outside = bound, bound
                model._transport_outcome, model._transport_bound_source = "relaxation_certified", "relaxation"
                self.log.info("utility solve: relaxation %.0fs, support %d of %d actions, incumbent certified "
                              "against the relaxation in %.0fs, peak rss %.2f GB", lp_seconds,
                              len(stakes)-len(outside), len(stakes), search_seconds, self._peak_rss())
                return result
        # Certification: a solution holding action j has value at most bound + rc_j (an LP fact every integer
        # point obeys), so every action that cannot beat the incumbent is fixed at zero: the fixed program holds
        # every better solution and its own bound covers them all (exact).
        fixed = [] if incumbent is None else [key for key, cost in costs.items()
                                              if stakes[key] < self.UTILITY_SUPPORT_EPS and bound+cost < incumbent-tolerance]
        free = len(stakes)-len(fixed)
        warm = self.params.get("utility_warm_start")
        over = free > self.UTILITY_CERTIFY_ACTIONS
        if over and (warm is None or incumbent is None):
            # Too many actions for HiGHS to certify in any time: the support incumbent stands, bounded by the
            # relaxation (every outside action could reach it); no limit was hit, and the certificate says so.
            model._transport_bound, model._transport_outside, model._transport_declined = bound, bound, True
            model._transport_outcome, model._transport_bound_source = "declined", "relaxation"
            self.log.info("utility solve: relaxation %.0fs, support %d of %d actions, incumbent search %.0fs, "
                          "certification declined (%d free actions, limit %d); the relaxation is the bound, "
                          "peak rss %.2f GB", lp_seconds, len(stakes)-len(outside), len(stakes), search_seconds,
                          free, self.UTILITY_CERTIFY_ACTIONS, self._peak_rss())
            return result
        support = None if incumbent is None else [(v, v.value) for v in model.component_data_objects(Var)]
        supplied = support is not None and all(v.value is not None
                                               for v in model.component_data_objects(Var, active=True))
        start_value = None if warm is None or support is None else self._exact_utility(model)
        declined_result = result
        for key in fixed:
            model.x[key].setub(0)
        solver.options["mip_dual_bound"] = bound   # HiGHS stops at the gap against the bound it cannot improve on
        # ADR-0302: above the action limit only a declared warm start reaches here; its node budget, not the clock,
        # is what may stop it (the document's time limit stays a halt, and the record says which stopped it).
        nodes = {"mip_max_nodes": warm["node_limit"]} if over else {}
        try:
            with self._search_gaps(solver), self._highs_options(solver, nodes):
                result = self._solve_once(solver, model, remaining())
        finally:
            del solver.options["mip_dual_bound"]
            for key in fixed:
                model.x[key].setub(capacity[key])
        if warm is not None:
            self.last_warm_start = self._warm_start_record(model, result, supplied, incumbent, over, tolerance,
                                                           start_value)
        if over and not self._warm_certified(model, result, bound, costs, fixed):
            w = self.last_warm_start
            if (w["termination"] in self.DETERMINISTIC_TERMINATIONS and w["value_after"] is not None
                    and start_value is not None and w["value_after"] > start_value+tolerance):
                # The node budget, not the clock, stopped it and its incumbent beats the start by exact utility: that
                # incumbent is traded UNCERTIFIED, the week declined and bounded by the relaxation as before.
                w["kept"] = True
                model._transport_bound, model._transport_outside, model._transport_declined = bound, bound, True
                model._transport_outcome, model._transport_bound_source = "declined", "relaxation"
                self.log.info("utility solve: warm-started certification above the action limit (%d free actions, "
                              "node limit %d) did not prove the gap (%s) but improved the incumbent; it is kept "
                              "uncertified, peak rss %.2f GB", free, warm["node_limit"], w["termination"],
                              self._peak_rss())
                return result
            # Not proved and not improved, or stopped by the clock: the week is the declined week it was, its
            # incumbent and its relaxation bound untouched (a clock-dependent point is never traded).
            for variable, value in support:
                variable.set_value(value, skip_validation=True)
            model._transport_no_incumbent = False
            model._transport_bound, model._transport_outside, model._transport_declined = bound, bound, True
            model._transport_outcome, model._transport_bound_source = "declined", "relaxation"
            self.log.info("utility solve: warm-started certification above the action limit (%d free actions, node "
                          "limit %d) did not prove the gap (%s); the week stays declined, peak rss %.2f GB", free,
                          warm["node_limit"], self.last_warm_start["termination"], self._peak_rss())
            return declined_result
        if support is not None and getattr(model, "_transport_no_incumbent", False):
            # The certification found no solution in its time: the support incumbent it started from still stands.
            for variable, value in support:
                variable.set_value(value)
            model._transport_no_incumbent = False
        # Every solution outside the fixed program holds a fixed action j, so its value is at most bound + rc_j.
        model._transport_bound = bound
        model._transport_outside = max((bound+costs[key] for key in fixed), default=None)
        self.log.info("utility solve: relaxation %.0fs, support %d of %d actions, incumbent search %.0fs, "
                      "certification with %d actions fixed by reduced cost in %.0fs, peak rss %.2f GB",
                      lp_seconds, len(stakes)-len(outside), len(stakes), search_seconds, len(fixed),
                      time.perf_counter()-clock-lp_seconds-search_seconds-searched, self._peak_rss())
        return result

    @staticmethod
    def _peak_rss():
        """This process's peak resident set in GB (0 where the platform does not report it)."""
        try:
            import resource
            return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1048576.
        except (ImportError, AttributeError):
            return 0.

    @staticmethod
    def _objective_value(model):
        """The model's profit at its loaded variable values, or None when it cannot be read."""
        from pyomo.environ import value

        profit = value(model.profit, exception=False)
        return None if not number_ok(profit) else float(profit)

    @staticmethod
    def _build_solve_record(solver, model, results, name, seconds):
        """Do not represent an unloaded warm start as a native incumbent; a relaxation bound tightens the record's."""
        from dataclasses import replace
        record = PyomoSolve._build_solve_record(solver, model, results, name, seconds)
        if getattr(model, "_transport_no_incumbent", False):
            record = replace(record, objective=None, gap=None, binding=())
            if (record.termination == "error" and record.bound is None
                    and getattr(model, "_transport_outcome", None) is not None):   # a utility program
                # Not a timeout: a solver that ended in error with nothing to load (the week is skipped all the same).
                model._transport_outcome, model._transport_bound_source = "solver_error", "none"
        relaxed = getattr(model, "_transport_bound", None)
        if relaxed is not None and number_ok(relaxed):
            # The solver's bound covers the program it saw, with the actions fixed by reduced cost; every solution
            # outside it is bounded by the relaxation plus the largest fixed reduced cost (None: nothing fixed), so
            # the whole program is bounded by the larger of the two, and by the relaxation itself.
            outside = getattr(model, "_transport_outside", None)
            own = record.bound if record.bound is not None and number_ok(record.bound) else relaxed
            bound = min(relaxed, own if outside is None else max(own, outside))
            record = replace(record, bound=bound, gap=SolveRecord._gap(record.objective, bound))
            if getattr(model, "_transport_declined", False) and record.gap is not None:
                # A declined certification keeps the label a native limit would carry (the timeout policy decides the
                # week and nothing is misread as certified); no limit was hit, and strategy_outcome says so.
                record = replace(record, termination="maxTimeLimit", status="aborted")
        outcome = getattr(model, "_transport_outcome", None)
        if outcome is not None:
            record = _UtilitySolveRecord(**{name: getattr(record, name) for name in SolveRecord.field_names()},
                                         strategy_outcome=outcome,
                                         bound_source=getattr(model, "_transport_bound_source", "none"))
        return record

    def _timeout_reason(self, certificate, value):
        """Identify only an explicitly permitted uncertified native timeout."""
        if (self.params.get("timeout_policy", "raise") != "return"
                or certificate["termination"] != "maxTimeLimit"):
            return None
        if certificate["bound"] is None:
            return "missing_bound"
        allowance = max(self.params["absolute_gap"], self.params["relative_gap"]*abs(value))
        return "gap" if max(0., certificate["bound"]-value) > allowance else None

    def _empty_timeout(self, model, certificates, recorded=None):
        """Validate absent-incumbent evidence against the reconstructed model.

        A ``maxTimeLimit`` termination is a ``no_incumbent`` week; an ``error`` termination with no solution, no
        objective, no bound and no gap is a ``solver_error`` week (the same cash week, named for what happened).
        ``recorded`` is the envelope's claimed reason on a replay, which must be the one the certificate implies.
        """
        import copy
        from pyomo.environ import Constraint, Var
        fields = self._certificate_fields(self._utility is not None)
        if (self.params.get("timeout_policy", "raise") != "return"
                or not isinstance(certificates, list) or len(certificates) != 1
                or not isinstance(certificates[0], dict) or set(certificates[0]) != fields):
            raise ValueError("incomplete no-incumbent timeout evidence")
        c = certificates[0]
        errored = c["termination"] == "error"
        reason = "solver_error" if errored else "no_incumbent"
        if (c["solver"] != self.params.get("solver", DEFAULT_SOLVER)
                or c["termination"] not in ("maxTimeLimit", "error")
                or c["status"] not in (("error", "aborted") if errored else ("ok", "warning", "aborted"))
                or c["objective"] is not None or c["gap"] is not None
                or not number_ok(c["seconds"]) or c["seconds"] < 0
                or (errored and c["bound"] is not None)
                or (c["bound"] is not None
                    and (not number_ok(c["bound"]) or c["bound"] < -self.params["parity_tolerance"]))
                or (errored and "strategy_outcome" in c and c["strategy_outcome"] != "solver_error")
                or (recorded is not None and recorded != reason)):
            raise ValueError("contradictory no-incumbent timeout evidence")
        for key, component in (("variables", Var), ("constraints", Constraint)):
            expected = sum(1 for _ in model.component_data_objects(component, active=True))
            if type(c[key]) is not int or c[key] != expected:
                raise ValueError("recorded model dimensions differ")
        return {"timeout": reason, "candidate": None,
                "certificates": copy.deepcopy(certificates)}

    def _incumbent(self, model):
        """Independently reprice and check actual integer actions and reserves."""
        from pyomo.environ import value

        tolerance = max(self.params["parity_tolerance"], 1e-6)
        quantities = []
        cleanup = {"removed_contracts": 0, "profit_gain": 0., "pairs": []}
        for i, g in enumerate(self._groups):
            if i not in self._active:
                quantities.append([0]*len(g["capacity"]))
                continue
            raw = [value(model.x[i, j]) for j in range(len(g["capacity"]))]
            if any(not number_ok(v) or abs(v-round(v)) > 1e-5 for v in raw):
                raise ValueError("nonintegral incumbent")
            q = [round(v) for v in raw]
            if any(v < 0 or v > cap for v, cap in zip(q, g["capacity"])):
                raise ValueError("incumbent exceeds capacity")
            changed = False
            for k, (a, b) in enumerate(g["exclusive"]):
                if (i, k) not in self._redundant:
                    continue
                matched = min(q[a], q[b])
                if matched:
                    q[a] -= matched
                    q[b] -= matched
                    changed = True
                    cleanup["removed_contracts"] += 2*matched
                    effective = self._effective_credit(g)
                    cleanup["profit_gain"] -= matched*(effective[a]+effective[b])
                    cleanup["pairs"].append({"group": g["id"], "pair": [a, b],
                                             "matched_quantity": matched})
            if changed:
                for j, quantity in enumerate(q):
                    model.x[i, j].set_value(quantity)
            quantities.append(q)
        program = self._program()
        scored = TransportActionScore(program, {"parity_tolerance": tolerance}).evaluate(quantities)
        audited = {"quantities": scored["quantities"], "reserves": scored["reserves"],
                   "group_values": scored["group_values"], "value": scored["value"],
                   "canonicalization": cleanup}
        if self._charged:
            audited["risk_charges"] = scored["risk_charges"]
        if self._utility is not None:
            audited["utility"] = scored["utility"]
        return audited

    def _model_cleanup_gain(self, cleanup):
        """Return how far the cleanup moves ``model.profit``: its dollar gain, or nothing under a utility.

        A dollar program's ``profit`` reads ``x`` and the cleanup's dollar gain is exactly its change. A
        utility program's ``profit`` reads the ``U`` variables the solver left, which the cleanup does
        not touch, so the model's own change is zero; the dollar gain is then no part of the
        comparison and the audit (``model_over_audit``) values what the cleanup bought.
        """
        return 0. if self._utility is not None else cleanup["profit_gain"]

    def _program(self):
        """Return the checked program the built model stands for."""
        program = {"groups": self._groups, "caps": self._caps, "joint_rows": self._joint}
        if self._utility is not None:
            program["utility"] = self._utility
        return program

    @staticmethod
    def _certificate_fields(utility):
        """The keys of a certificate: a utility program's carries ``strategy_outcome`` and ``bound_source`` too."""
        return set((_UtilitySolveRecord if utility else SolveRecord).field_names())-{"binding"}

    def _cash(self):
        """Return the audited cash selection: nothing held, nothing reserved, nothing earned."""
        zero = [[0]*len(g["capacity"]) for g in self._groups]
        chosen = {"quantities": zero, "reserves": [0.]*len(zero),
                  "group_values": [0.]*len(zero), "value": 0.}
        if self._charged:
            chosen["risk_charges"] = [0.]*len(zero)
        if self._utility is not None:
            chosen["utility"] = TransportActionScore(
                self._program(), {"parity_tolerance": self.params["parity_tolerance"]}).value(zero)["utility"]
        return chosen

    def _recorded(self, inputs, selection, certificates):
        """Recheck executable selections or explicit non-executable timeouts."""
        import json
        json.dumps([selection, certificates], allow_nan=False)
        if isinstance(selection, dict) and "timeout" in selection:
            if (self.params.get("timeout_policy", "raise") != "return"
                    or set(selection) != {"timeout", "candidate"}
                    or selection["timeout"] not in ("gap", "missing_bound", "no_incumbent", "solver_error")):
                raise ValueError("invalid recorded timeout envelope")
            if selection["timeout"] in ("no_incumbent", "solver_error"):
                if selection["candidate"] is not None:
                    raise ValueError("no-incumbent timeout has candidate actions")
                return self._empty_timeout(self.build_model(inputs, self.params), certificates,
                                           selection["timeout"])
            candidate = self._recorded_candidate(
                inputs, selection["candidate"], certificates, timeout=selection["timeout"])
            return {"timeout": selection["timeout"],
                    "candidate": {k: v for k, v in candidate.items() if k != "certificates"},
                    "certificates": candidate["certificates"]}
        return self._recorded_candidate(inputs, selection, certificates)

    @staticmethod
    def actions(selection):
        """Return the raw integer incumbent a recorded selection was extracted from.

        Parameters
        ----------
        selection : dict
            One decision's recorded ``selection`` (an executable selection or
            a ``timeout`` envelope), as :meth:`extract` returns it.

        Returns
        -------
        dict or None
            ``{"primary_quantities", "pairs"}``: the primary integer
            quantities per group and the cancelled pairs (group id, pair,
            matched quantity), every value an int; None for a no-incumbent
            timeout.

        Raises
        ------
        ValueError
            The selection lacks integer primary quantities or a sound cleanup.
        """
        import copy

        if isinstance(selection, dict) and "timeout" in selection:
            selection = selection.get("candidate")
            if selection is None:
                return None
        if (not isinstance(selection, dict) or "primary_quantities" not in selection
                or not isinstance(selection.get("canonicalization"), dict)
                or not isinstance(selection["canonicalization"].get("primary"), dict)):
            raise ValueError("incomplete recorded selection")
        quantities = copy.deepcopy(selection["primary_quantities"])
        pairs = copy.deepcopy(selection["canonicalization"]["primary"].get("pairs"))
        if (not isinstance(quantities, list) or not isinstance(pairs, list)
                or any(not isinstance(q, list) or any(type(v) is not int or v < 0 for v in q)
                       for q in quantities)
                or any(not isinstance(pair, dict) or set(pair) != {"group", "pair", "matched_quantity"}
                       or type(pair["matched_quantity"]) is not int
                       or not isinstance(pair["pair"], list) or len(pair["pair"]) != 2
                       or any(type(j) is not int for j in pair["pair"]) for pair in pairs)):
            raise ValueError("invalid recorded primary quantities")
        return {"primary_quantities": quantities, "pairs": pairs}

    def derived(self, inputs, actions, certificates):
        """Re-derive a selection from integer actions and a foreign solve certificate.

        The model is built here; the certificate's solver evidence is installed
        as this solve's record, and the unchanged :meth:`extract` reprices,
        cleans up, bounds and classifies the incumbent with this host's floats.

        Parameters
        ----------
        inputs : dict
            The transport program (groups, caps, joint rows).
        actions : dict or None
            :meth:`actions` of the foreign selection: primary quantities per
            group plus the cancelled pairs; None for a no-incumbent timeout.
        certificates : list of dict
            Exactly one certificate without ``binding`` (``SolveRecord``
            fields), as a recorded decision carries it.

        Returns
        -------
        dict
            ``{"selection": ...}`` exactly as :meth:`extract` returns it.

        Raises
        ------
        ValueError
            The actions do not fit the model, the certificate is malformed or
            its model dimensions differ, or the extraction refuses.
        """
        import copy
        import json
        from pyomo.environ import Constraint, Var

        json.dumps([actions, certificates], allow_nan=False)
        fields = self._certificate_fields(isinstance(inputs, dict) and inputs.get("utility") is not None)
        if (not isinstance(certificates, list) or len(certificates) != 1
                or not isinstance(certificates[0], dict) or set(certificates[0]) != fields):
            raise RecordedCheckRefusal("incomplete recorded certificate", "certificate_fields", "certificate")
        certificate = certificates[0]
        if "strategy_outcome" in fields and not _UtilitySolveRecord(
                **{**certificate, "binding": ()}).coherent():
            raise RecordedCheckRefusal("contradictory recorded certificate", "certificate_strategy", "certificate")
        if certificate["solver"] != self.params.get("solver", DEFAULT_SOLVER):
            raise RecordedCheckRefusal("contradictory recorded certificate", "certificate_solver", "certificate")
        self.solve_record = None
        model = self.build_model(inputs, self.params)
        for key, component in (("variables", Var), ("constraints", Constraint)):
            expected = sum(1 for _ in model.component_data_objects(component, active=True))
            if type(certificate[key]) is not int or certificate[key] != expected:
                raise RecordedCheckRefusal("recorded model dimensions differ", "model_dimensions", "certificate")
        if actions is None:
            model._transport_no_incumbent = True
        else:
            if not isinstance(actions, dict) or set(actions) != {"primary_quantities", "pairs"}:
                raise RecordedCheckRefusal("invalid recorded actions", "actions_shape", "invalid_trade")
            quantities = copy.deepcopy(actions["primary_quantities"])
            if (not isinstance(quantities, list) or len(quantities) != len(self._groups)
                    or any(not isinstance(q, list) or len(q) != len(g["capacity"])
                           or any(type(v) is not int or v < 0 for v in q)
                           for q, g in zip(quantities, self._groups))):
                raise RecordedCheckRefusal("invalid recorded primary quantities", "actions_shape", "invalid_trade")
            lookup = {g["id"]: i for i, g in enumerate(self._groups)}
            for pair in actions["pairs"]:
                if (not isinstance(pair, dict) or set(pair) != {"group", "pair", "matched_quantity"}
                        or pair["group"] not in lookup
                        or not isinstance(pair["pair"], list) or len(pair["pair"]) != 2
                        or any(type(j) is not int for j in pair["pair"])
                        or type(pair["matched_quantity"]) is not int or pair["matched_quantity"] <= 0):
                    raise RecordedCheckRefusal("invalid recorded cleanup pair", "cleanup_pair", "invalid_trade")
                i, (a, b) = lookup[pair["group"]], pair["pair"]
                if pair["pair"] not in self._groups[i]["exclusive"]:
                    raise RecordedCheckRefusal("invalid recorded cleanup pair", "cleanup_pair", "invalid_trade")
                quantities[i][a] += pair["matched_quantity"]
                quantities[i][b] += pair["matched_quantity"]
            for i, group in enumerate(quantities):
                if any(v > cap for v, cap in zip(group, self._groups[i]["capacity"])):
                    raise RecordedCheckRefusal("recorded actions exceed capacity", "capacity", "invalid_trade")
                if i not in self._active:
                    if any(group):
                        raise RecordedCheckRefusal("recorded actions in a screened-out group",
                                                   "screened_group", "invalid_trade")
                    continue
                for j, quantity in enumerate(group):
                    model.x[i, j].set_value(quantity)
            self._price_duals(model)
        self.solve_record = (_UtilitySolveRecord if self._utility is not None else SolveRecord)(
            **{**certificate, "binding": ()})
        return self.extract(model, None)

    def _price_duals(self, model):
        """Solve for the continuous duals worth exactly the actions set on ``model``.

        A foreign certificate carries integer actions but no dual variables, and the model's profit
        reads them (the worst-case W1 cost and every risk charge), so unset duals would price the
        actions as if the ambiguity were zero. With every action fixed what remains is a linear
        program whose optimum is those actions' worst-case cost; the foreign integer solve is not
        repeated.
        """
        from pyomo.environ import Var

        for variable in model.component_data_objects(Var, active=True):
            if variable.parent_component() is model.x:
                variable.fix()
        name = self.params.get("solver", DEFAULT_SOLVER)
        try:
            results = self._resolve_solver().solve(model)
        except RuntimeError as exc:
            # appsi raises instead of returning when the fixed actions are infeasible. Only a utility program
            # converts it (its foreign actions can breach the wealth floor); a dollar program keeps the raise.
            if self._utility is None:
                raise
            raise RecordedCheckRefusal("recorded actions could not be priced: infeasible",
                                       "dual_pricing", "pricing") from exc
        priced = SolveRecord.from_solve(model, results, name, 0.)
        if priced.termination != "optimal":
            raise RecordedCheckRefusal("recorded actions could not be priced: "+str(priced.termination),
                                       "dual_pricing", "pricing")

    def _recorded_candidate(self, inputs, selection, certificates, *, timeout=None):
        """Use the same feasibility and valuation audit for every saved incumbent."""
        import copy
        import json
        from pyomo.environ import Constraint, Var

        json.dumps([selection, certificates], allow_nan=False)
        required = {"quantities", "reserves", "group_values", "value", "model_value",
                    "bound", "gap", "canonicalization", "primary_quantities"}
        if (not isinstance(selection, dict) or set(selection)-{"risk_charges", "utility"} != required
                or not isinstance(certificates, list) or len(certificates) != 1):
            raise ValueError("incomplete recorded selection")
        certificate = certificates[0]
        fields = self._certificate_fields(isinstance(inputs, dict) and inputs.get("utility") is not None)
        if not isinstance(certificate, dict) or set(certificate) != fields:
            raise ValueError("incomplete recorded certificate")
        if "strategy_outcome" in fields and not _UtilitySolveRecord(**{**certificate, "binding": ()}).coherent():
            raise ValueError("contradictory recorded certificate")
        statuses = (("ok", "warning") if certificate["termination"] == "optimal"
                    else ("ok", "warning", "aborted")
                    if certificate["termination"] == "maxTimeLimit" else ())
        missing_bound = timeout == "missing_bound"
        numeric = ("objective", "seconds") if missing_bound else ("objective", "bound", "gap", "seconds")
        if (certificate["solver"] != self.params.get("solver", DEFAULT_SOLVER)
                or certificate["status"] not in statuses
                or any(not number_ok(certificate[k]) for k in numeric)
                or certificate["seconds"] < 0
                or (missing_bound and (certificate["bound"] is not None or certificate["gap"] is not None))
                or certificate["gap"] != SolveRecord._gap(
                    certificate["objective"], certificate["bound"])):
            raise ValueError("contradictory recorded certificate")
        numeric = ("value", "model_value") if missing_bound else ("value", "model_value", "bound", "gap")
        if (any(not number_ok(selection[k]) for k in numeric)
                or (missing_bound and (selection["bound"] is not None or selection["gap"] is not None))):
            raise ValueError("invalid recorded objective fields")
        cleanup = selection["canonicalization"]
        if (not isinstance(cleanup, dict) or set(cleanup) != {"primary", "secondary"}
                or cleanup["secondary"] is not None):
            raise ValueError("invalid recorded cleanup")
        primary = cleanup["primary"]
        if (not isinstance(primary, dict)
                or set(primary) != {"removed_contracts", "profit_gain", "pairs"}
                or type(primary["removed_contracts"]) is not int
                or primary["removed_contracts"] < 0
                or not number_ok(primary["profit_gain"]) or primary["profit_gain"] < 0
                or not isinstance(primary["pairs"], list)):
            raise ValueError("invalid recorded primary cleanup")
        model = self.build_model(inputs, self.params)
        if ("risk_charges" in selection) != self._charged:
            raise ValueError("recorded risk charges differ from the program")
        if ("utility" in selection) != (self._utility is not None):
            raise ValueError("recorded utility differs from the program")
        for key, component in (("variables", Var), ("constraints", Constraint)):
            expected = sum(1 for _ in model.component_data_objects(component, active=True))
            if type(certificate[key]) is not int or certificate[key] != expected:
                raise ValueError("recorded model dimensions differ")
        quantities = copy.deepcopy(selection["primary_quantities"])
        if (not isinstance(quantities, list) or len(quantities) != len(self._groups)
                or any(not isinstance(q, list) or len(q) != len(g["capacity"])
                       for q, g in zip(quantities, self._groups))
                or any(type(q) is not int or q < 0 for group in quantities for q in group)):
            raise ValueError("invalid recorded primary quantities")
        lookup = {g["id"]: i for i, g in enumerate(self._groups)}
        seen = set()
        for pair in primary["pairs"]:
            if (not isinstance(pair, dict) or set(pair) != {"group", "pair", "matched_quantity"}
                    or pair["group"] not in lookup
                    or not isinstance(pair["pair"], list) or len(pair["pair"]) != 2
                    or any(type(j) is not int for j in pair["pair"])
                    or type(pair["matched_quantity"]) is not int or pair["matched_quantity"] <= 0):
                raise ValueError("invalid recorded cleanup pair")
            i, (a, b) = lookup[pair["group"]], pair["pair"]
            group = self._groups[i]
            if (pair["pair"] not in group["exclusive"] or (i, a, b) in seen
                    or not self._costly_roundtrip(group, a, b, self._joint_terms(group["id"]))):
                raise ValueError("unproved recorded cleanup pair")
            seen.add((i, a, b))
            quantities[i][a] += pair["matched_quantity"]
            quantities[i][b] += pair["matched_quantity"]
        for i, group in enumerate(quantities):
            if i not in self._active:
                if any(group):
                    raise ValueError("recorded actions in a screened-out group")
                continue
            for j, quantity in enumerate(group):
                model.x[i, j].set_value(quantity)
        audited = self._incumbent(model)
        if (audited["quantities"] != selection["primary_quantities"]
                or audited["canonicalization"] != primary):
            raise ValueError("recorded cleanup disagrees with original actions")
        tol = self.params["parity_tolerance"]
        if (audited["value"] < -tol
                or selection["model_value"] > audited["value"]+tol
                or abs(selection["model_value"]-certificate["objective"]-self._model_cleanup_gain(primary)) > tol
                or (not missing_bound and (certificate["bound"] < certificate["objective"]-tol
                    or certificate["bound"] < audited["value"]-tol))):
            raise ValueError("invalid recorded primary valuation")
        charged = ("risk_charges",) if self._charged else ()
        if audited["value"] <= 0:
            audited.update(self._cash())
        if (not isinstance(selection["quantities"], list)
                or any(not isinstance(q, list) or any(type(v) is not int for v in q)
                       for q in selection["quantities"])
                or any(not isinstance(selection[k], list)
                       or any(not number_ok(v) for v in selection[k])
                       for k in ("reserves", "group_values", *charged))):
            raise ValueError("invalid recorded final audit types")
        audited_keys = ("quantities", "reserves", "group_values", "value", *charged,
                        *(("utility",) if self._utility is not None else ()))
        for key in audited_keys:
            claimed, audit = selection[key], audited[key]
            if key == "utility":
                # The record-only fields are sums this host orders its own way: same keys and types, values within a
                # relative 1e-9 (a last-bit difference replays; a forged record does not reach the saved row).
                skip = TransportActionScore._RECORD_ONLY
                if (not isinstance(claimed, dict) or set(claimed) != set(audit)
                        or not all(_records_agree(claimed[k], audit[k]) for k in skip)):
                    raise ValueError("recorded independent portfolio audit differs: "+key)
                claimed = {k: v for k, v in claimed.items() if k not in skip}
                audit = {k: v for k, v in audit.items() if k not in skip}
            if claimed != audit:
                raise ValueError("recorded independent portfolio audit differs: "+key)
        gap = None if missing_bound else max(0., certificate["bound"]-audited["value"])
        allowance = max(self.params["absolute_gap"], self.params["relative_gap"]*abs(audited["value"]))
        if selection["bound"] != certificate["bound"] or selection["gap"] != gap:
            raise ValueError("contradictory recorded dollar gap")
        if timeout is not None:
            if self._timeout_reason(certificate, audited["value"]) != timeout:
                raise ValueError("recorded timeout was not uncertified")
        elif gap > allowance:
            raise ValueError("uncertified recorded dollar gap")
        return {**copy.deepcopy(selection), "certificates": copy.deepcopy(certificates)}

    @staticmethod
    def _refuse_over_tolerance(message, misses, tolerance):
        """Raise the first ``(check, difference)`` whose difference exceeds ``tolerance``, sized as a ratio of it."""
        for check, difference in misses:
            if difference > tolerance:
                raise RecordedCheckRefusal.sized(message, check, difference, tolerance)

    def extract(self, model, results):
        """Certify and return the primary profit portfolio without another solve.

        Parameters
        ----------
        model, results : object
            Solved Pyomo model and solver result.

        Returns
        -------
        dict
            Selection, independent value, upper bound and one solve certificate.

        Raises
        ------
        ValueError
            Solver evidence, integer feasibility, valuation or final gap fails.
        """
        from pyomo.environ import value

        first = self.solve_record
        if getattr(model, "_transport_no_incumbent", False):
            certificates = [{k: v for k, v in first.to_obj().items() if k != "binding"}]
            return {"selection": self._empty_timeout(model, certificates)}
        tol = self.params["parity_tolerance"]
        missing_bound = (self.params.get("timeout_policy", "raise") == "return"
                         and first.termination == "maxTimeLimit" and first.bound is None)
        statuses = (("ok", "warning") if first.termination == "optimal"
                    else ("ok", "warning", "aborted")
                    if first.termination == "maxTimeLimit" else ())
        raw_value = value(model.profit, exception=False)
        message = "invalid primary solver certificate"
        if (first.status not in statuses or not number_ok(first.objective)
                or (not missing_bound and not number_ok(first.bound)) or not number_ok(raw_value)):
            raise RecordedCheckRefusal(message, "certificate_fields", "certificate")
        misses = [("certificate_objective", abs(first.objective-raw_value))]
        if not missing_bound:
            misses.append(("certificate_bound", first.objective-first.bound))
        self._refuse_over_tolerance(message, misses, tol)
        chosen = self._incumbent(model)
        cleanup = chosen["canonicalization"]
        model_value = value(model.profit)
        misses = [("incumbent_negative", -chosen["value"]), ("model_over_audit", model_value-chosen["value"]),
                  ("cleanup_gain", abs(model_value-first.objective-self._model_cleanup_gain(cleanup)))]
        if not missing_bound:
            misses.append(("bound_below_value", chosen["value"]-first.bound))
        self._refuse_over_tolerance("invalid primary dual/primal incumbent value", misses, tol)
        primary_quantities = [q.copy() for q in chosen["quantities"]]
        # Cash is constructively feasible. Preserve every strictly positive
        # profit; only zero or parity-sized negative noise is replaced.
        if chosen["value"] <= 0.:
            chosen = self._cash()
        gap = None if missing_bound else max(0., first.bound-chosen["value"])
        allowance = max(self.params["absolute_gap"],
                        self.params["relative_gap"]*abs(chosen["value"]))
        reason = self._timeout_reason(first.to_obj(), chosen["value"])
        if not reason and gap > allowance:
            raise RecordedCheckRefusal(f"uncertified primary objective: {first.termination}, gap={gap}",
                                       "gap", "gap", gap/allowance if allowance > 0 else None)
        chosen.update(bound=first.bound, gap=gap, model_value=model_value,
                      primary_quantities=primary_quantities,
                      canonicalization={"primary": cleanup, "secondary": None},
                      certificates=[first.to_obj()])
        if reason:
            chosen = {"timeout": reason,
                      "candidate": {k: v for k, v in chosen.items() if k != "certificates"},
                      "certificates": chosen["certificates"]}
        return {"selection": chosen}


#: The screen's own LP functions as defined above; :meth:`TransportPortfolioSolve._screen_key` caches only these.
_SCREEN_LP_FUNCTIONS = (TransportPortfolioSolve.__dict__["_group_lp_bound"],
                        TransportPortfolioSolve.__dict__["_group_lp_relaxation"])


class _TransportActionInfeasible(ValueError):
    """A quantity vector fails a declared transport constraint."""


class TransportActionScore:
    """Value and check finite integer actions against a shared transport program.

    Parameters
    ----------
    program : dict
        Groups, shared reserve caps and optional joint rows accepted by the
        transport portfolio solver.
    params : dict
        parity_tolerance (finite nonnegative objective units).

    Examples
    --------
    Score a cash action without constructing or solving a MIO::

        scorer = TransportActionScore(program, {"parity_tolerance": 1e-8})
        result = scorer.evaluate([[0, 0]])
    """

    _PARAMS = ("parity_tolerance",)

    @classmethod
    def validate_params(cls, params):
        """Return configuration defects for the scorer's default-deny parameters.

        Parameters
        ----------
        params : dict
            Proposed scorer parameters.

        Returns
        -------
        list of str
            Configuration defects.
        """
        if (not isinstance(params, dict)
                or any(not isinstance(key, str) for key in params)):
            return ["params must be a dict with string keys"]
        unknown = sorted(set(params)-set(cls._PARAMS))
        problems = [] if not unknown else [f"unknown param(s) {unknown}"]
        tolerance = params.get("parity_tolerance", 0.)
        if not number_ok(tolerance) or tolerance < 0:
            problems.append("parity_tolerance must be finite and nonnegative")
        return problems

    def __init__(self, program, params):
        """Validate and retain a private copy of one transport program.

        Parameters
        ----------
        program : dict
            Finite transport objective and constraints.
        params : dict
            Scorer parameters.

        Raises
        ------
        ValueError
            Program or parameter validation fails.
        """
        import copy
        import hashlib
        import json

        defects = self.validate_params(params)
        if defects:
            raise ValueError("; ".join(defects))
        self.params = copy.deepcopy(params)
        self.program = copy.deepcopy(program)
        (self.groups, self.caps, self.duals, self.joint,
         self.charges, self.utility) = TransportPortfolioSolve._checked_program(self.program)
        identity = {"groups": self.groups, "caps": self.caps, "joint_rows": self.joint}
        self.scenarios = self.envelope = self.expected = self.charge_plan = None
        if self.utility is not None:
            identity["utility"] = self.utility
            solve = TransportPortfolioSolve
            self.expected = solve._expected_profit(self.groups)
            self.scenarios = solve._utility_scenarios(self.groups, self.utility)
            self.envelope = solve._utility_envelope(self.groups, self.utility, self.expected, self.scenarios)
            self.charge_plan = solve._charge_plan(self.groups, self.utility)
        self.program_identity = hashlib.sha256(json.dumps(
            identity, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")).hexdigest()

    def _quantities(self, quantities):
        """Validate nested action shape, integer bounds and exclusivity."""
        if not isinstance(quantities, (list, tuple)) or len(quantities) != len(self.groups):
            raise ValueError("quantity groups differ from program")
        result = []
        for group, action in zip(self.groups, quantities):
            if (not isinstance(action, (list, tuple))
                    or len(action) != len(group["capacity"])
                    or any(type(q) is not int or q < 0 or q > cap
                           for q, cap in zip(action, group["capacity"]))):
                raise ValueError("quantities must be in-range nonnegative integers")
            if any(action[a] and action[b] for a, b in group["exclusive"]):
                raise _TransportActionInfeasible("quantities violate exclusive sides")
            result.append(list(action))
        return result

    def _reserves(self, quantities):
        """Calculate each group's reserve using the shared reserve-row rule."""
        import numpy as np

        return [float(max(0., *(np.asarray(group["reserve_rows"]) @ action)))
                for group, action in zip(self.groups, quantities)]

    def _feasible_quantities(self, quantities):
        """Validate shape and constraints; return normalized quantities and reserves."""
        import numpy as np

        quantities = self._quantities(quantities)
        tolerance = float(self.params.get("parity_tolerance", 0.))
        for group, action in zip(self.groups, quantities):
            for row in group["linear_rows"]:
                if float(np.dot(row["coefficients"], action)) > row["upper"]+tolerance:
                    raise _TransportActionInfeasible("quantities violate group linear bound")
        reserves = self._reserves(quantities)
        reserve_by_id = {group["id"]: reserves[index]
                         for index, group in enumerate(self.groups)}
        for cap in self.caps:
            if sum(reserve_by_id[key] for key in cap["groups"]) > cap["upper"]+tolerance:
                raise _TransportActionInfeasible("quantities violate shared reserve cap")
        index = {group["id"]: i for i, group in enumerate(self.groups)}
        for row in self.joint:
            total = sum(float(np.dot(coefficients, quantities[index[key]]))
                        for key, coefficients in row["terms"].items())
            if total > row["upper"]+tolerance:
                raise _TransportActionInfeasible("quantities violate joint row "+row["id"])
        if self.utility is not None:
            for name, wealth in zip(*self._wealth(quantities)):
                if wealth < self.utility["wealth_lo"]-tolerance:
                    raise _TransportActionInfeasible("quantities breach the wealth floor in scenario "+name)
        return quantities, reserves

    def _wealth(self, quantities):
        """Return every utility scenario's id and the wealth these quantities reach in it."""
        import numpy as np

        mean = sum(float(np.dot(row, q)) for row, q in zip(self.expected, quantities))
        names, wealth = [], []
        for name, _weight, terms in self.scenarios:
            names.append(name)
            wealth.append(self.utility["w0"]+mean+sum(float(np.dot(row, quantities[i])) for i, row in terms.items()))
        return names, wealth

    #: A dispersion at or below this fraction of ``w0`` is float noise: the book's ratio and effective fraction are None.
    BOOK_NOISE = 1e-9
    #: The ``utility`` keys that only record: a replay compares them to a relative 1e-9, not exactly (host float order).
    _RECORD_ONLY = ("group_sd", "book")

    def _book_record(self, plan, exact_sd, totals):
        """Return how the charge compares with the chosen book's exact sd (a record: nothing in the solve reads it).

        ``sd`` and ``dispersion`` are the book's exact standard deviation and the charge's ``S`` combined the way the
        charge combines them: the groups of one ``aggregate`` key add (perfectly correlated), the keys in quadrature.
        ``ratio`` is ``sd / dispersion`` and ``effective_fraction`` the Kelly fraction the charge actually applies,
        ``f x ratio^2`` with ``f = 1 / gamma``: the label ``f`` is nominal when the ratio is not one. A book whose
        dispersion is float noise (at most :attr:`BOOK_NOISE` of ``w0``: cash, or legs that offset) records no ratio.
        """
        sd = math.sqrt(sum(sum(exact_sd[i] for i in indices)**2 for indices in plan["members"].values()))
        dispersion = math.sqrt(sum(total**2 for total in totals.values()))
        ratio = sd/dispersion if dispersion > self.BOOK_NOISE*self.utility["w0"] else None
        return {"sd": float(sd), "dispersion": float(dispersion), "ratio": ratio,
                "effective_fraction": None if ratio is None else ratio**2/self.utility["gamma"]}

    def _utility_components(self, quantities):
        """Value the quantities under the tangent envelope and the charge lines (what the program maximizes), and exactly."""
        import numpy as np

        names, wealth = self._wealth(quantities)
        weights = np.asarray([weight for _name, weight, _terms in self.scenarios], dtype=float)
        plan = self.charge_plan
        spread = {i: float(plan["charge"][i].charge(plan["rows"][i] @ np.asarray(quantities[i], dtype=float)))
                  for i in plan["rows"]}   # S_g of the book chosen, not of a reference book
        totals = {name: sum(spread[i] for i in indices) for name, indices in sorted(plan["members"].items())}
        exact_sd = {}   # each group's exact sd of the book's payoff over its whole grid, for the record only
        for i, rows in plan["rows"].items():
            payoff, p = rows @ np.asarray(quantities[i], dtype=float), plan["charge"][i].masses
            exact_sd[i] = float(np.sqrt(np.dot(p, (payoff-np.dot(p, payoff))**2)))
        charges = [plan["lines"][name].value(total) for name, total in totals.items()]
        exact_charges = [plan["lines"][name].exact(total) for name, total in totals.items()]
        envelope = float(self.utility["w0"]*np.dot(weights, self.envelope.value(wealth)))
        return {"envelope": envelope, "charges": charges, "value": envelope-sum(charges),
                "charge_names": list(totals), "dispersion": [spread.get(i, 0.) for i in range(len(self.groups))],
                "group_sd": [exact_sd.get(i, 0.) for i in range(len(self.groups))],
                "book": self._book_record(plan, exact_sd, totals),
                "exact": float(self.utility["w0"]*np.dot(weights, self.envelope.exact(wealth))-sum(exact_charges)),
                "exact_charges": exact_charges,
                "envelope_error": self.envelope.error, "core_error": self.envelope.core_error,
                "knots": int(len(self.envelope.knots)),
                "wealth_min": float(min(wealth, default=self.utility["w0"])),
                "wealth_max": float(max(wealth, default=self.utility["w0"])),
                "wealth": {name: float(w) for name, w in zip(names, wealth)}}

    def check_feasible(self, quantities):
        """Check feasibility without evaluating the action's economic objective.

        Parameters
        ----------
        quantities : sequence of sequence of int
            One nonnegative contract quantity per configured action.

        Returns
        -------
        dict
            Feasible flag and reason. A false flag names an exclusivity, linear,
            reserve-cap or joint-row violation.

        Raises
        ------
        ValueError
            Quantity shape, type or capacity is malformed.
        """
        try:
            self._feasible_quantities(quantities)
        except _TransportActionInfeasible as exc:
            return {"feasible": False, "reason": str(exc)}
        return {"feasible": True, "reason": None}

    def _value_components(self, quantities, reserves):
        """Calculate economic components for normalized integer quantities."""
        import numpy as np

        group_values, nominal_values, deductions, risk_charges = [], [], [], []
        for group, dual, charge, action in zip(
            self.groups, self.duals, self.charges, quantities
        ):
            q = np.asarray(action, dtype=float)
            loss = np.asarray(group["losses"], dtype=float) @ q
            nominal_loss = float(np.dot(group["masses"], loss))
            worst_loss = float(dual.distribution.worst_expected_loss(
                loss, group["radius"], group["scale"]))
            credit = TransportPortfolioSolve._effective_credit(group)
            nominal_values.append(float(np.dot(credit, q)-nominal_loss))
            group_values.append(float(np.dot(credit, q)-worst_loss))
            deductions.append(float(worst_loss-nominal_loss))
            risk_charges.append(0. if charge is None else float(charge.charge(loss)))
        result = {
            "quantities": quantities, "reserves": reserves,
            "group_values": group_values, "nominal_values": nominal_values,
            "uncertainty_deductions": deductions, "risk_charges": risk_charges,
            "value": float(sum(group_values)-sum(risk_charges)),
            "program_identity": self.program_identity,
        }
        if self.utility is not None:
            # The objective is the W0-scaled expected envelope utility less the charges; the dollar group values
            # stay beside it.
            result["utility"] = self._utility_components(quantities)
            result["value"] = result["utility"]["value"]
        return result

    def value(self, quantities):
        """Return economic components for one integer action.

        Parameters
        ----------
        quantities : sequence of sequence of int
            One nonnegative contract quantity per configured action.

        Returns
        -------
        dict
            Quantities, reserves, robust group values, nominal values,
            uncertainty deductions, risk charges, objective value and
            program identity. This is economic valuation, not admission.
        """
        normalized = self._quantities(quantities)
        return self._value_components(normalized, self._reserves(normalized))

    def evaluate(self, quantities):
        """Value an action and refuse it unless every shared constraint holds.

        Parameters
        ----------
        quantities : sequence of sequence of int
            One nonnegative contract quantity per configured action.

        Returns
        -------
        dict
            Economic valuation with feasible set true.

        Raises
        ------
        ValueError
            An action violates a group linear row, shared reserve cap or joint row.
        """
        normalized, _reserves = self._feasible_quantities(quantities)
        return {**self.value(normalized), "feasible": True}

    def catalogue(self, max_combinations):
        """Enumerate every feasible integer action under a full-space bound.

        Parameters
        ----------
        max_combinations : int
            Positive upper bound on the complete Cartesian action space.

        Returns
        -------
        list of sequence of sequence of int
            Every feasible action, with cash first.

        Raises
        ------
        ValueError
            Bound invalid or full action space exceeds the bound.
        """
        import itertools

        if type(max_combinations) is not int or max_combinations < 1:
            raise ValueError("max_combinations must be a positive integer")
        size = 1
        for group in self.groups:
            for capacity in group["capacity"]:
                size *= capacity+1
                if size > max_combinations:
                    raise ValueError("complete action space exceeds max_combinations")
        bounds = [range(capacity+1) for group in self.groups
                  for capacity in group["capacity"]]
        actions = []
        for flat in itertools.product(*bounds):
            nested, offset = [], 0
            for group in self.groups:
                width = len(group["capacity"])
                nested.append(list(flat[offset:offset+width]))
                offset += width
            try:
                self.evaluate(nested)
            except _TransportActionInfeasible:
                continue
            actions.append(nested)
        actions.sort(key=lambda action: (sum(sum(group) for group in action),
                                         tuple(q for group in action for q in group)))
        return actions
