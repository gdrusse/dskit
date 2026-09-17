"""Budgeted uncertainty sets: how far a family of numbers may be wrong at once.

A decision made from estimated numbers is only as good as the estimates. The
honest way to say so is not "assume every number is its point estimate" and it
is not "assume every number is simultaneously at its worst" -- the first
pretends to certainty nobody has, the second is so pessimistic that the
decision it produces is usually to do nothing at all.

This module builds the middle answer. A BUDGETED uncertainty set (Bertsimas and
Sim, *The Price of Robustness*, 2004) says: every component may deviate from its
nominal value by at most its own stated adverse deviation, and the NORMALIZED
TOTAL of those deviations may not exceed a budget. A budget of one means "any
single component may be fully wrong"; a budget equal to the component count is
the whole box again. Between those the set stays bounded, stays polyhedral --
so a robust counterpart remains linear and an integer program stays solvable --
and the components cannot all bind at once.

**The budget is not identified by any interval.** It is a
conservatism-and-dependence parameter, chosen by rolling validation of how often
the set is violated and of what the resulting decisions were worth. No
confidence level implies a budget and nothing here derives one. It is taken as
an argument, always, and there is no default.

**Nor is any deviation.** A deviation is the caller's statement about its own
evidence, and this module inherits exactly the quality of that statement and no
more. A widened point estimate passed in as a deviation makes a widened set, not
a bound: if the number a caller supplies does not attain the coverage they think
it does, neither does anything computed from it. Nothing here adds a guarantee
its inputs did not carry, and nothing here claims one.

Three members ship. They differ in three DECLARATIONS, never in mechanism:

* :class:`BudgetedProbabilitySet` -- components are probabilities, so the set
  lives in the unit interval, decision coefficients are non-negative, and the
  adverse extreme is the largest value the linear form can reach.
* :class:`BudgetedMeanSet` -- ESTIMATION uncertainty in an expected value: how
  wrong the average could be, given finite evidence.
* :class:`BudgetedOutcomeSet` -- dispersion of a REALIZED outcome: how far a
  single future draw could land from its centre.

The last two are deliberately separate classes rather than one class with a
label. Estimation uncertainty shrinks as evidence accumulates; outcome
dispersion does not, because it is a property of the world rather than of the
sample. Reading one as the other is the error the separation exists to make
structurally impossible: neither member can absorb the other's numbers, because
neither can see them.

**Two doorways, and they are not equally strong.**

:meth:`~BudgetedUncertaintySet.worst_case` and
:meth:`~BudgetedUncertaintySet.counterpart` are the exact ones. The first gives
the adverse realization over the whole set for a stated decision vector; the
second gives the linear description a solver builds its own protection rows
from, and the two are pinned to agree.

:meth:`~BudgetedUncertaintySet.realizations` is the convenience one, for a
consumer that takes weighted scenarios rather than a robust counterpart. It is
weaker, and the weakness is stated rather than hidden: **a set is not a
distribution.** A budgeted set carries no probability measure at all, so the
weights it emits are a UNIFORM CONVENTION over the points emitted, declared as
such by the returned value's own ``weighting_kind``. A consumer reading them as
estimated probabilities is computing a uniform average over budget-feasible
corners -- a robustness diagnostic in the sampled-constraint sense, never a
calibrated expectation. The emitted points are likewise a deterministic,
seeded SAMPLE of the set's corners plus every single-component corner, not an
exhaustive enumeration, so the maximizer for an arbitrary decision vector need
not be among them. When the exact answer is wanted, ask
:meth:`~BudgetedUncertaintySet.worst_case`.

**That disclosure is a PRECONDITION, not a footnote.** The pair a scenario
optimizer takes -- a weight vector and one array per component -- is the same
numbers whether its weights are estimated probabilities or a convention, and
the consumer builds an expected utility and a tail-risk cap out of them either
way. A tuple cannot carry which it received, so
:meth:`RealizationSet.weighted_draws` does not produce that pair until the
caller NAMES the weighting it is taking (:data:`WEIGHTING_KINDS`), and refuses
when the name is not what the set actually carries. The acknowledgement then
lives in the calling code, at the boundary where the number is acted on. That
stops the limitation being lost SILENTLY; it cannot stop a caller who types the
word and ignores it.

**Fail-closed, and loudly.** A non-finite value, an empty family, a deviation
that would leave the set unbounded, a budget outside its range, mismatched
component names, a decision coefficient the member's own geometry forbids, and
above all a DEGENERATE set -- one whose adverse deviation is zero, in whole or
in one component, and which therefore cannot constrain anything -- each refuse
by name. A set that cannot constrain is precisely the failure this module
exists to prevent, so it is never emitted quietly.

**The family is a class, not a switch.** :class:`BudgetedUncertaintySet` owns
four template methods a member can never replace -- enforced by
``__init_subclass__``, not by a docstring asking nicely. That reaches every
subclass whose definition reaches the hook, which is every ordinary member; a
class that overrides ``__init_subclass__`` itself and never calls ``super()``
escapes it, as it does for every user of the idiom, and the hook's own
docstring says so rather than implying tamper-immunity. A member supplies three
hooks, all abstract, so an incomplete member refuses at construction rather than
failing mid-run.

Nothing here is a node kind and nothing is wired into a document (ADR-0156).

Import cost: stdlib only.
"""

from __future__ import annotations

import itertools
import math
from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

from dskit.pipeline.node import class_ref
from dskit.pipeline.records import number_ok
from dskit.pipeline.stats import _bootstrap_rng

__all__ = [
    "COEFFICIENT_DOMAINS",
    "MAX_REALIZATIONS",
    "UNCERTAINTY_SETS",
    "WEIGHTING_KINDS",
    "WEIGHTS_SUM_TOLERANCE",
    "WORST_CASE_SENSES",
    "BudgetedMeanSet",
    "BudgetedOutcomeSet",
    "BudgetedProbabilitySet",
    "BudgetedUncertaintySet",
    "RealizationSet",
    "RobustCounterpart",
    "WorstCase",
    "register_uncertainty_set",
    "uncertainty_set",
]

#: Which extreme of the linear form a member calls adverse. ``"max"`` is for a
#: quantity that hurts by being LARGER (a cost, a failure probability);
#: ``"min"`` is for one that hurts by being SMALLER (a benefit). Closed by
#: owned-kind doctrine: a third sense would be a third arithmetic, not a
#: registration.
WORST_CASE_SENSES = ("max", "min")

#: What signs a member's decision coefficients may take. ``"nonnegative"``
#: halves the geometry -- only one deviation direction can ever be adverse --
#: and lets the member refuse a negative coefficient by name instead of
#: silently answering a question it was not built for.
COEFFICIENT_DOMAINS = ("nonnegative", "real")

#: The widest realization set this module will emit. It mirrors
#: ``libs.pyomo.HARD_N_SCENARIOS_CEILING``, the measured tractability ceiling of
#: the scenario-consuming optimizer these sets are built for, so a set too wide
#: to solve refuses HERE, naming the count, rather than from inside a solve. A
#: tier-1 module cannot import a tier-2 pack, so the suite pins the two by
#: importing the pack itself and comparing.
MAX_REALIZATIONS = 256

#: What a weight vector IS, which nothing downstream can read off the numbers.
#: A ``"convention"`` weighting is a stated averaging rule over points that
#: carry no probability measure -- a budgeted set's corners; a ``"measure"``
#: weighting is an estimated distribution. Both are non-negative vectors
#: summing to one, so a consumer cannot tell them apart and an optimizer will
#: happily report a tail-risk number computed from either. The DECLARATION is
#: what carries the difference across a boundary, which is why
#: :meth:`RealizationSet.weighted_draws` makes the caller state it. Closed by
#: owned-kind doctrine: a third kind would be a third claim about evidence, not
#: a registration.
WEIGHTING_KINDS = ("convention", "measure")

#: How far emitted weights may miss summing to exactly one. Strictly TIGHTER
#: than the tolerance the consuming optimizer enforces, so a set this module
#: emits can never be one that consumer rejects; the suite pins the
#: relationship.
WEIGHTS_SUM_TOLERANCE = 1e-9

#: Float slack when reading a budget's integer part. A budget arrives as a
#: float, and ``floor(2.0)`` must not become 1 because the caller's own
#: arithmetic produced ``1.9999999999999998``.
_BUDGET_EPS = 1e-12

#: How many draws per still-unfilled slot the corner sampler will attempt
#: before concluding the corner set is smaller than the request. Bounded so a
#: request the geometry cannot fill terminates instead of spinning.
_SAMPLE_ATTEMPT_FACTOR = 64


def _check_number(value, label):
    """Refuse anything that is not a real number the toolkit can compute on."""
    if not number_ok(value):
        raise ValueError(f"{label} must be a finite number, got {value!r}")
    return float(value)


def _check_count(value, label, minimum=None):
    """Refuse anything that is not a plain int, or falls below ``minimum``."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{label} must be an integer, got {value!r}")
    if minimum is not None and value < minimum:
        raise ValueError(f"{label} must be >= {minimum}, got {value!r}")
    return value


def _frozen_floats(mapping):
    """Return a read-only float copy of ``mapping``, safe from later edits."""
    return MappingProxyType({str(k): float(v) for k, v in mapping.items()})


def _frozen_tree(value):
    """Return a read-only copy of nested mappings and sequences; other types as given."""
    if isinstance(value, Mapping):
        return MappingProxyType({str(k): _frozen_tree(v) for k, v in value.items()})
    if isinstance(value, (list, tuple, set, frozenset)):
        return tuple(_frozen_tree(v) for v in value)
    return value


def _adverse_directions(sense, coefficient_domain):
    """Name the deviation halves a member's geometry can ever read.

    The one owner of that rule. Construction uses it to decide which halves
    must be positive and which must be zero; the realization draw uses it to
    decide which corners exist; a second copy would let a set validate against
    one geometry and be sampled against another.

    Parameters
    ----------
    sense : str
        A member of :data:`WORST_CASE_SENSES`.
    coefficient_domain : str
        A member of :data:`COEFFICIENT_DOMAINS`.

    Returns
    -------
    tuple of str
        ``("below", "above")`` when a coefficient may take either sign;
        otherwise the single half a non-negative coefficient can reach.
    """
    if coefficient_domain == "real":
        return ("below", "above")
    return ("above",) if sense == "max" else ("below",)


def _adverse_half(below, above, sense, coefficient):
    """Give the adverse half-width of one component at one decision coefficient.

    The one owner of the direction rule. ``worst_case`` and
    :meth:`RobustCounterpart.adverse_deviation` both read it, because a set
    whose counterpart names a different half from the one its own worst case
    moved would hand a solver rows that protect the wrong side.

    Parameters
    ----------
    below, above : float
        The component's two adverse half-widths, both non-negative.
    sense : str
        A member of :data:`WORST_CASE_SENSES`.
    coefficient : float
        This component's coefficient in the decision's linear form.

    Returns
    -------
    float
        The half-width that moves the linear form adversely, or ``0.0`` for a
        coefficient of zero -- a component the decision does not read cannot
        hurt it, and must not be charged budget for the privilege.
    """
    if coefficient == 0.0:
        return 0.0
    upward = (coefficient > 0.0) == (sense == "max")
    return above if upward else below


@dataclass(frozen=True)
class WorstCase:
    """The adverse realization over a set, and what it cost.

    Built by :meth:`BudgetedUncertaintySet.worst_case`, never by hand. The
    realization is complete -- every component, moved or not -- so a caller can
    replay the reported value from it rather than trusting the arithmetic.

    Parameters
    ----------
    value : float
        The linear form evaluated at the adverse realization.
    nominal_value : float
        The same form at the nominal point.
    protection : float
        The gap between the two, never negative. This is the quantity a robust
        counterpart subtracts from (or adds to) the nominal objective.
    realization : mapping
        ``name -> float``, the component values that attained ``value``.
    deviating : tuple of str
        The components that actually moved, worst-first. Empty when the budget
        bought nothing -- a decision reading no uncertain component.

    Raises
    ------
    ValueError
        On a non-finite field, a negative protection, or an empty realization.

    Examples
    --------
    Read off a member rather than constructed::

        u = BudgetedMeanSet(
            nominal={"a": 1.0},
            deviation_below={"a": 0.1},
            deviation_above={"a": 0.1},
            budget=1.0,
        )
        u.worst_case({"a": 1.0}).protection
        # -> 0.1
    """

    value: float
    nominal_value: float
    protection: float
    realization: dict
    deviating: tuple

    def __post_init__(self):
        """Refuse any combination a consuming robust program could not use."""
        for name in ("value", "nominal_value", "protection"):
            object.__setattr__(self, name, _check_number(getattr(self, name), name))
        if self.protection < 0.0:
            raise ValueError(
                f"protection must be >= 0, got {self.protection!r} — a set cannot make "
                "a decision better than its own nominal point"
            )
        if not self.realization:
            raise ValueError("realization must name at least one component")
        for name, value in self.realization.items():
            _check_number(value, f"realization[{name!r}]")
        object.__setattr__(self, "realization", _frozen_floats(self.realization))
        object.__setattr__(self, "deviating", tuple(str(n) for n in self.deviating))


@dataclass(frozen=True)
class RobustCounterpart:
    """The linear description a solver builds its own protection rows from.

    A budgeted set's protection term has an exact linear-programming form: with
    one scalar and one non-negative variable per component, the adverse total is
    representable without enumerating a single corner. This value carries
    everything those rows need and nothing a solver would have to guess.

    Parameters
    ----------
    budget : float
        The normalized total deviation the set allows.
    sense : str
        A member of :data:`WORST_CASE_SENSES`.
    deviation_below, deviation_above : mapping
        ``name -> float``, the EFFECTIVE half-widths after any domain clamp --
        never the raw ones the caller passed, because a solver protecting
        against a deviation the set cannot reach is over-conservative for a
        reason nothing records.
    coefficient_domain : str
        A member of :data:`COEFFICIENT_DOMAINS`.
    lower_bound, upper_bound : float or None
        The feasible interval a realization may not leave, or ``None`` on that
        side when the member declares no clamp.
    set_ref : str
        The member that produced this, as
        :func:`~dskit.pipeline.node.class_ref` spells it.

    Raises
    ------
    ValueError
        On an unknown sense or coefficient domain, an empty family, mismatched
        component names, or a non-finite or negative half-width.

    Examples
    --------
    Read off a member rather than constructed::

        u = BudgetedProbabilitySet(
            nominal={"a": 0.2},
            deviation_below={"a": 0.0},
            deviation_above={"a": 0.1},
            budget=1.0,
        )
        u.counterpart().sense
        # -> 'max'
    """

    budget: float
    sense: str
    deviation_below: dict
    deviation_above: dict
    coefficient_domain: str
    lower_bound: float
    upper_bound: float
    set_ref: str

    def __post_init__(self):
        """Refuse a description a solver could not build correct rows from."""
        object.__setattr__(self, "budget", _check_number(self.budget, "budget"))
        if self.sense not in WORST_CASE_SENSES:
            raise ValueError(f"sense must be one of {WORST_CASE_SENSES}, got {self.sense!r}")
        if self.coefficient_domain not in COEFFICIENT_DOMAINS:
            raise ValueError(
                f"coefficient_domain must be one of {COEFFICIENT_DOMAINS}, got "
                f"{self.coefficient_domain!r}"
            )
        if not self.deviation_below:
            raise ValueError("a counterpart must name at least one component")
        if set(self.deviation_below) != set(self.deviation_above):
            raise ValueError(
                "deviation_below and deviation_above must describe the same components; "
                f"only in deviation_below: {sorted(set(self.deviation_below) - set(self.deviation_above))}, "
                f"only in deviation_above: {sorted(set(self.deviation_above) - set(self.deviation_below))}"
            )
        for label in ("deviation_below", "deviation_above"):
            for name, value in getattr(self, label).items():
                if _check_number(value, f"{label}[{name!r}]") < 0.0:
                    raise ValueError(f"{label}[{name!r}] must be >= 0, got {value!r}")
            object.__setattr__(self, label, _frozen_floats(getattr(self, label)))

    def adverse_deviation(self, name, coefficient):
        """Give the half-width that hurts a decision reading ``name`` this way.

        Parameters
        ----------
        name : str
            A component of this counterpart's family.
        coefficient : float
            That component's coefficient in the decision's linear form.

        Returns
        -------
        float
            The adverse half-width, ``0.0`` for a coefficient of zero.

        Raises
        ------
        ValueError
            When ``name`` is not a component here, or ``coefficient`` is not a
            finite number.
        """
        if name not in self.deviation_below:
            raise ValueError(
                f"{name!r} is not a component of this set; it holds "
                f"{sorted(self.deviation_below)}"
            )
        coefficient = _check_number(coefficient, f"coefficient[{name!r}]")
        return _adverse_half(
            self.deviation_below[name], self.deviation_above[name], self.sense, coefficient
        )


@dataclass(frozen=True)
class RealizationSet:
    """Weighted points of one set: shared weights and one array per component.

    Point ``k`` means the SAME simultaneous realization in every array, which is
    the property a consumer taking joint scenarios depends on and the reason
    these are not drawn per component.

    **The weights are a convention, not a measure.** What a budgeted set emits
    is uniform over the points emitted, and ``weighting_kind`` declares it.
    Read those weights as estimated probabilities and the result is a uniform
    average over budget-feasible corners, not an expectation -- so
    :meth:`weighted_draws` will not hand out the solver-shaped pair until the
    caller names the weighting it is taking.

    Parameters
    ----------
    weights : sequence of float
        Non-empty, finite, non-negative, summing to one within
        :data:`WEIGHTS_SUM_TOLERANCE`.
    draws : mapping
        ``name -> sequence of float``, each exactly as long as ``weights``,
        every value finite, and not all equal -- a component with no spread is
        a point estimate wearing a set's clothes, refused rather than emitted.
    weighting_kind : str
        What these weights ARE, a member of :data:`WEIGHTING_KINDS`. It defaults
        to ``"convention"`` because that is the WEAKER claim: silence can demote
        an estimated measure to a convention, which loses only precision, but it
        can never promote a convention into a measure, which would invent
        evidence. A caller holding estimated weights says ``"measure"``.
    provenance : mapping
        The set, budget, sense, seed, requested and emitted counts, and the
        weighting convention. Frozen through every nested mapping, list, tuple
        and set; a value of any other type is stored as it was handed over, so
        a caller putting a mutable object of its own in here keeps a handle to
        it.

    Raises
    ------
    ValueError
        On an empty family, weights that are negative, non-finite or miss
        summing to one, a draw array whose length does not match the weights, a
        non-finite draw, a component with no spread, or a ``weighting_kind``
        outside :data:`WEIGHTING_KINDS`.

    Examples
    --------
    Two equally weighted points over one component::

        s = RealizationSet(
            weights=(0.5, 0.5), draws={"a": (0.9, 1.1)}, provenance={}
        )
        s.weighted_draws(reading_weights_as="convention")[0]
        # -> [0.5, 0.5]
    """

    weights: tuple
    draws: dict
    weighting_kind: str = "convention"
    provenance: dict = field(default_factory=dict)

    def __post_init__(self):
        """Refuse a set a consumer could not safely optimize against."""
        if self.weighting_kind not in WEIGHTING_KINDS:
            raise ValueError(
                f"weighting_kind must be one of {WEIGHTING_KINDS}, got {self.weighting_kind!r}"
            )
        weights = tuple(float(w) for w in self.weights)
        if not weights:
            raise ValueError("weights must be a non-empty sequence")
        for index, weight in enumerate(weights):
            if not number_ok(weight) or weight < 0.0:
                raise ValueError(f"weight {index} must be finite and >= 0, got {weight!r}")
        total = math.fsum(weights)
        if abs(total - 1.0) > WEIGHTS_SUM_TOLERANCE:
            raise ValueError(
                f"weights must sum to 1 within {WEIGHTS_SUM_TOLERANCE}, got {total!r}"
            )
        draws = {str(n): tuple(float(v) for v in values) for n, values in self.draws.items()}
        if not draws:
            raise ValueError("draws must name at least one component")
        for name, values in draws.items():
            if len(values) != len(weights):
                raise ValueError(
                    f"draws[{name!r}] has length {len(values)}, expected {len(weights)} "
                    "(one value per weight)"
                )
            for index, value in enumerate(values):
                if not number_ok(value):
                    raise ValueError(
                        f"draws[{name!r}] point {index} is not a finite number: {value!r}"
                    )
            if len(set(values)) < 2:
                raise ValueError(
                    f"draws[{name!r}] is degenerate — every point carries {values[0]!r}, "
                    "which is a point estimate rather than a set"
                )
        object.__setattr__(self, "weights", weights)
        object.__setattr__(self, "draws", MappingProxyType(draws))
        object.__setattr__(self, "provenance", _frozen_tree(dict(self.provenance)))

    def weighted_draws(self, reading_weights_as):
        """Return the ``(weights, arrays)`` pairing a scenario consumer takes.

        The pair cannot carry what its weights ARE: a vector drawn from an
        estimated distribution and one that is a stated averaging rule are the
        same numbers, and the consumer builds an expectation and a tail-risk
        cap out of either. So the caller states which it is taking, here, and a
        mismatch refuses -- the disclosure ends up in the calling code, at the
        boundary where a wrong reading is acted on, instead of in a field the
        returned tuple drops.

        Parameters
        ----------
        reading_weights_as : str
            The weighting the caller is taking these weights to be, a member of
            :data:`WEIGHTING_KINDS`, which must be this set's own
            ``weighting_kind``. There is no default: the whole point is that
            the caller says it.

        Returns
        -------
        tuple
            ``(weights, draws)`` where ``weights`` is a list of floats summing
            to one and ``draws`` is ``{name: list of float}``, every list as
            long as ``weights``.

        Raises
        ------
        ValueError
            When ``reading_weights_as`` is outside :data:`WEIGHTING_KINDS`, or
            names a weighting this set does not carry.
        """
        if reading_weights_as not in WEIGHTING_KINDS:
            raise ValueError(
                f"reading_weights_as must be one of {WEIGHTING_KINDS}, got "
                f"{reading_weights_as!r}"
            )
        if reading_weights_as != self.weighting_kind:
            raise ValueError(
                f"these weights are a {self.weighting_kind!r} weighting, not a "
                f"{reading_weights_as!r} one. A convention weighting is a stated averaging "
                "rule over points that carry no probability measure: an average under it is "
                "NOT an expectation and a tail measured under it is not a tail probability. "
                "The two are the same numbers, so nothing downstream can tell them apart — "
                "name the one you actually hold, and ask worst_case()/counterpart() when the "
                "exact answer over the whole set is what is wanted"
            )
        return list(self.weights), {name: list(values) for name, values in self.draws.items()}


class BudgetedUncertaintySet(ABC):
    """The family: nominal values, adverse deviations, and a budget over them.

    ``worst_case``, ``protection``, ``counterpart`` and ``realizations`` are
    TEMPLATE methods. A member can never replace one -- ``__init_subclass__``
    raises at class definition, so the guarantee is enforced rather than
    requested, for every subclass whose definition reaches the hook; see
    :meth:`__init_subclass__` for the one shape that does not reach it. A
    member supplies three hooks, all abstract, so an incomplete member refuses
    at construction instead of failing mid-run:

    * :meth:`worst_case_sense` -- which extreme of the linear form is adverse.
    * :meth:`component_bounds` -- the feasible interval a realization may not
      leave, which CLAMPS the deviations at construction.
    * :meth:`coefficient_domain` -- what signs a decision coefficient may take.

    Nothing is defaulted. The budget and both deviation halves are arguments,
    because the only available default for each is the answer that claims more
    certainty than anybody has.

    Parameters
    ----------
    nominal : mapping
        ``name -> float``, the point estimates. Non-empty; every value finite
        and inside :meth:`component_bounds`.
    deviation_below, deviation_above : mapping
        ``name -> float >= 0``, how far each component may move down and up.
        The same component names as ``nominal``. A half the member's geometry
        can never read must be zero -- a number nothing reads is where a silent
        divergence hides -- and a half it CAN read must be strictly positive
        after clamping, or the set cannot constrain that component.
    budget : float
        The normalized total deviation the set allows, in ``(0, n]`` for ``n``
        components. One means any single component may be fully wrong; ``n`` is
        the whole box. **Tuned by rolling validation; no interval identifies
        it.**

    Raises
    ------
    ValueError
        On an empty family, a non-finite or out-of-domain nominal, a non-finite,
        negative, never-read-but-non-zero or zero-after-clamping deviation,
        mismatched component names, a budget outside ``(0, n]``, or a hook
        answering outside its vocabulary.

    Examples
    --------
    A member is constructed with all four arguments, always::

        u = BudgetedMeanSet(
            nominal={"a": 1.0, "b": 2.0},
            deviation_below={"a": 0.1, "b": 0.3},
            deviation_above={"a": 0.1, "b": 0.3},
            budget=1.0,
        )
        u.protection({"a": 1.0, "b": 1.0})
        # -> 0.3
    """

    def __init_subclass__(cls, **kwargs):
        """Refuse a subclass that replaces a template; every HOOK stays overridable.

        Scoped honestly: this runs for every subclass whose definition REACHES
        it. An intermediate class that defines its own ``__init_subclass__``
        and does not call ``super()`` stops it running for anything built below
        that intermediate, which can then replace a template. That is inherent
        to the hook rather than particular to this module -- every user of the
        idiom in this repo shares it -- and the guarantee claimed here is
        against an ordinary member getting the arithmetic wrong, never against
        a subclass built to defeat the check.
        """
        super().__init_subclass__(**kwargs)
        for final in ("worst_case", "protection", "counterpart", "realizations"):
            if final in vars(cls):
                raise TypeError(
                    f"{cls.__name__} overrides {final}, which is final: the set's geometry "
                    "is the seam, its arithmetic and its screens are not — override a hook "
                    "instead"
                )

    def __init__(self, *, nominal, deviation_below, deviation_above, budget):
        sense = self.worst_case_sense()
        if sense not in WORST_CASE_SENSES:
            raise ValueError(
                f"{type(self).__name__}.worst_case_sense returned {sense!r}, not one of "
                f"{WORST_CASE_SENSES}"
            )
        domain = self.coefficient_domain()
        if domain not in COEFFICIENT_DOMAINS:
            raise ValueError(
                f"{type(self).__name__}.coefficient_domain returned {domain!r}, not one of "
                f"{COEFFICIENT_DOMAINS}"
            )
        low, high = self._checked_bounds()

        if not isinstance(nominal, Mapping) or not nominal:
            raise ValueError(f"nominal must name at least one component, got {nominal!r}")
        for label, given in (
            ("deviation_below", deviation_below),
            ("deviation_above", deviation_above),
        ):
            if not isinstance(given, Mapping) or set(given) != set(nominal):
                raise ValueError(
                    f"{label} must describe the same components as nominal; only in nominal: "
                    f"{sorted(set(nominal) - set(given or ()))}, only in {label}: "
                    f"{sorted(set(given or ()) - set(nominal))}"
                )

        adverse = _adverse_directions(sense, domain)
        centres, belows, aboves = {}, {}, {}
        for name in nominal:
            if not isinstance(name, str) or not name:
                raise ValueError(f"a component name must be a non-empty string, got {name!r}")
            centre = _check_number(nominal[name], f"nominal[{name!r}]")
            if (low is not None and centre < low) or (high is not None and centre > high):
                raise ValueError(
                    f"nominal[{name!r}] is {centre!r}, outside this set's component bounds "
                    f"({low!r}, {high!r})"
                )
            halves = {}
            for label, given, room in (
                ("deviation_below", deviation_below, None if low is None else centre - low),
                ("deviation_above", deviation_above, None if high is None else high - centre),
            ):
                half = _check_number(given[name], f"{label}[{name!r}]")
                if half < 0.0:
                    raise ValueError(f"{label}[{name!r}] must be >= 0, got {half!r}")
                side = label.rsplit("_", 1)[1]
                if side not in adverse:
                    if half != 0.0:
                        raise ValueError(
                            f"{label}[{name!r}] is {half!r}, but this set's geometry can never "
                            f"read it (adverse directions: {adverse}) — pass 0.0 rather than a "
                            "number nothing will use"
                        )
                    halves[side] = 0.0
                    continue
                effective = half if room is None else min(half, room)
                if effective <= 0.0:
                    raise ValueError(
                        f"{label}[{name!r}] leaves no room to deviate (asked {half!r}, the "
                        f"component bounds ({low!r}, {high!r}) allow {room!r}) — a component "
                        "that cannot move cannot constrain anything"
                    )
                halves[side] = effective
            centres[name] = centre
            belows[name] = halves["below"]
            aboves[name] = halves["above"]

        budget = _check_number(budget, "budget")
        if not 0.0 < budget <= len(centres):
            raise ValueError(
                f"budget must be in (0, {len(centres)}] — above zero, or the set is the "
                f"nominal point and constrains nothing, and at most the component count, "
                f"which is already the whole box — got {budget!r}"
            )

        self._sense = sense
        self._domain = domain
        self._bounds = (low, high)
        self._adverse = adverse
        self._nominal = _frozen_floats(centres)
        self._deviation_below = _frozen_floats(belows)
        self._deviation_above = _frozen_floats(aboves)
        self._budget = budget
        self._names = tuple(sorted(centres))

    # -- the three member hooks ----------------------------------------------

    @abstractmethod
    def worst_case_sense(self):
        """Say which extreme of the linear form this set calls adverse.

        Returns
        -------
        str
            ``"max"`` when the quantity hurts by being larger, ``"min"`` when
            it hurts by being smaller. A member of :data:`WORST_CASE_SENSES`.
        """
        raise NotImplementedError

    @abstractmethod
    def component_bounds(self):
        """Give the feasible interval a realization of one component may not leave.

        Returns
        -------
        tuple
            ``(low, high)``, either entry ``None`` for no clamp on that side.
            A stated bound CLAMPS the deviations at construction, so a set can
            never propose a realization its own components could not take.
        """
        raise NotImplementedError

    @abstractmethod
    def coefficient_domain(self):
        """Say what signs a decision coefficient may take against this set.

        Returns
        -------
        str
            A member of :data:`COEFFICIENT_DOMAINS`. ``"nonnegative"`` halves
            the geometry and makes a negative coefficient a refusal rather than
            an answer to a question this set was not built for.
        """
        raise NotImplementedError

    # -- what a member reads --------------------------------------------------

    @property
    def names(self):
        """Give the component names, sorted.

        Returns
        -------
        tuple of str
            Every component, in one canonical order.
        """
        return self._names

    @property
    def nominal(self):
        """Give the point estimates.

        Returns
        -------
        mapping
            ``name -> float``, read-only.
        """
        return self._nominal

    @property
    def deviation_below(self):
        """Give the EFFECTIVE downward half-widths, after any domain clamp.

        Returns
        -------
        mapping
            ``name -> float``, read-only.
        """
        return self._deviation_below

    @property
    def deviation_above(self):
        """Give the EFFECTIVE upward half-widths, after any domain clamp.

        Returns
        -------
        mapping
            ``name -> float``, read-only.
        """
        return self._deviation_above

    @property
    def budget(self):
        """Give the normalized total deviation this set allows.

        Returns
        -------
        float
            The budget, in ``(0, len(names)]``.
        """
        return self._budget

    # -- the templates --------------------------------------------------------

    def worst_case(self, coefficients):
        """Find the adverse realization over this set for one decision vector.

        Spends the budget on whichever components hurt this decision most: the
        deviations, each scaled by the size of its own coefficient, are ranked
        and the budget is spent down that ranking, the last one fractionally.
        That ranking IS the maximum (or minimum) of the linear form over the
        set -- the budget constrains only the normalized total, so buying the
        most expensive movements first cannot be beaten.

        Parameters
        ----------
        coefficients : mapping
            ``name -> float``, one finite coefficient per component, exactly
            this set's names. Every coefficient must be non-negative when
            :meth:`coefficient_domain` says so.

        Returns
        -------
        WorstCase
            The adverse value, the nominal value, the gap between them, the
            complete realization, and the components that moved.

        Raises
        ------
        ValueError
            When ``coefficients`` is not a mapping, names a component this set
            does not hold, omits one it does, carries a non-finite number, or
            carries a sign this set's coefficient domain forbids.
        """
        coefficients = self._checked_coefficients(coefficients)
        full, frac = self._budget_split()
        ranked = sorted(
            (
                (
                    abs(coefficients[n])
                    * _adverse_half(
                        self._deviation_below[n],
                        self._deviation_above[n],
                        self._sense,
                        coefficients[n],
                    ),
                    n,
                )
                for n in self._names
            ),
            key=lambda pair: (-pair[0], pair[1]),
        )
        spent, realization, deviating = [], dict(self._nominal), []
        for index, (term, name) in enumerate(ranked):
            share = 1.0 if index < full else (frac if index == full else 0.0)
            if share <= 0.0 or term <= 0.0:
                continue
            half = _adverse_half(
                self._deviation_below[name],
                self._deviation_above[name],
                self._sense,
                coefficients[name],
            )
            upward = (coefficients[name] > 0.0) == (self._sense == "max")
            realization[name] = self._nominal[name] + (1.0 if upward else -1.0) * share * half
            spent.append(share * term)
            deviating.append(name)

        protection = math.fsum(spent)
        nominal_value = math.fsum(coefficients[n] * self._nominal[n] for n in self._names)
        signed = protection if self._sense == "max" else -protection
        return WorstCase(
            value=nominal_value + signed,
            nominal_value=nominal_value,
            protection=protection,
            realization=realization,
            deviating=tuple(deviating),
        )

    def protection(self, coefficients):
        """Measure how far this set can move one decision's linear form.

        Parameters
        ----------
        coefficients : mapping
            ``name -> float``, as :meth:`worst_case` takes.

        Returns
        -------
        float
            The gap between the nominal value and the adverse one, never
            negative. This is what a robust counterpart's protection rows must
            reproduce.

        Raises
        ------
        ValueError
            Everything :meth:`worst_case` refuses.
        """
        return self.worst_case(coefficients).protection

    def counterpart(self):
        """Describe this set the way a solver builds its own protection rows.

        Returns
        -------
        RobustCounterpart
            The budget, sense, EFFECTIVE half-widths, coefficient domain,
            component bounds and the member's own class reference.
        """
        return RobustCounterpart(
            budget=self._budget,
            sense=self._sense,
            deviation_below=dict(self._deviation_below),
            deviation_above=dict(self._deviation_above),
            coefficient_domain=self._domain,
            lower_bound=self._bounds[0],
            upper_bound=self._bounds[1],
            set_ref=class_ref(type(self)),
        )

    def realizations(self, n_realizations, seed):
        """Draw a finite weighted set of points from this set, deterministically.

        The nominal point and EVERY single-component corner are always emitted,
        which is what makes each component vary and the set non-degenerate; a
        request too small to hold them refuses, naming the number needed. Any
        remaining room is filled with budget-saturating corners -- enumerated
        exactly when they fit, otherwise sampled from ``seed``. When the
        geometry offers fewer points than asked for, the whole of it is emitted
        and ``provenance`` records both counts.

        **The weights are uniform by convention, not by estimation.** The
        returned value declares that as its ``weighting_kind``, and
        :meth:`RealizationSet.weighted_draws` will not produce the
        solver-shaped pair for a caller who does not name it. See the module
        docstring.

        Parameters
        ----------
        n_realizations : int
            How many points to emit, at least ``1 + len(names) * d`` where
            ``d`` is the number of adverse directions this set's geometry has,
            and at most :data:`MAX_REALIZATIONS`.
        seed : int
            The base seed. The draw is also keyed by the member's own class, so
            two different sets drawn with one seed do not draw alike -- which
            is what keeps separately-stated uncertainties from being silently
            correlated.

        Returns
        -------
        RealizationSet
            Weights, one array per component, and the provenance.

        Raises
        ------
        ValueError
            When either argument is not a plain integer, when
            ``n_realizations`` is below the floor this set's geometry needs or
            above :data:`MAX_REALIZATIONS`.
        """
        # The set's OWN floor is judged before the request is, or a family too
        # wide to emit at all would be reported as a bad `n_realizations` —
        # blaming the caller for a fact about the set, and for a number no
        # legal request could satisfy.
        floor = 1 + len(self._names) * len(self._adverse)
        if floor > MAX_REALIZATIONS:
            raise ValueError(
                f"this set needs at least {floor} points for every one of its "
                f"{len(self._names)} components to vary, past the {MAX_REALIZATIONS} "
                "ceiling — it has too many components to emit as points, so no request "
                "can succeed; use counterpart() instead"
            )
        _check_count(seed, "seed")
        _check_count(n_realizations, "n_realizations", floor)
        if n_realizations > MAX_REALIZATIONS:
            raise ValueError(
                f"n_realizations must be <= {MAX_REALIZATIONS}, the widest set a scenario "
                f"consumer is measured to take, got {n_realizations!r}"
            )

        # No slice guards the length here on purpose: both producers below are
        # bounded by the room they are handed, and a mutation probe proved a
        # trailing `rows[:n_realizations]` unreachable. The bound is pinned by
        # the suite as a property of the result rather than by a clamp that
        # would silently hide a producer that stopped respecting its room.
        rows = [tuple(self._nominal[n] for n in self._names)]
        rows.extend(self._single_corners())
        rows.extend(self._saturating_corners(n_realizations - len(rows), seed))

        weight = 1.0 / len(rows)
        return RealizationSet(
            weights=tuple(weight for _ in rows),
            draws={
                name: tuple(row[index] for row in rows)
                for index, name in enumerate(self._names)
            },
            # A budgeted set has no measure to estimate one from, so this is the
            # only value this template can ever pass — pinned by the suite.
            weighting_kind="convention",
            provenance={
                "set": class_ref(type(self)),
                "budget": self._budget,
                "sense": self._sense,
                "coefficient_domain": self._domain,
                "seed": seed,
                "requested": n_realizations,
                "emitted": len(rows),
                "weighting": "uniform",
                "weighting_note": (
                    "Uniform over the points emitted. A budgeted set carries no probability "
                    "measure, so these are NOT estimated probabilities and an average taken "
                    "under them is not an expectation."
                ),
            },
        )

    # -- the internals the templates own --------------------------------------

    def _budget_split(self):
        """Split the budget into whole components and the leftover fraction.

        The one owner of that reading. The worst case, the corner enumeration
        and the corner builder all ask it, and three copies of a floor with a
        float tolerance in it is exactly the duplication that diverges: a
        budget of ``2.0`` arriving as ``1.9999999999999998`` must buy two whole
        components everywhere or nowhere.

        The leftover comes back UNCLAMPED, so it is a hair below zero whenever
        the tolerance rounded the whole part up. Every reader gates on its sign
        before reading its magnitude, which made the old ``max(0.0, ...)``
        unreachable; keeping it would have silently absorbed a future reader
        that stopped gating, the way the unreachable row-slice in
        :meth:`realizations` would have. The suite pins the behaviour instead,
        at exact float equality against the same budget stated exactly.
        """
        full = int(math.floor(self._budget + _BUDGET_EPS))
        return full, self._budget - full

    def _checked_bounds(self):
        """Read the member's component bounds, refusing an unusable interval."""
        bounds = self.component_bounds()
        if not isinstance(bounds, tuple) or len(bounds) != 2:
            raise ValueError(
                f"{type(self).__name__}.component_bounds must return a (low, high) tuple, got "
                f"{bounds!r}"
            )
        low, high = bounds
        low = None if low is None else _check_number(low, "component_bounds low")
        high = None if high is None else _check_number(high, "component_bounds high")
        if low is not None and high is not None and low >= high:
            raise ValueError(
                f"component_bounds low {low!r} must be < high {high!r}"
            )
        return low, high

    def _checked_coefficients(self, coefficients):
        """Refuse a decision vector this set's geometry cannot answer for."""
        if not isinstance(coefficients, Mapping):
            raise ValueError(f"coefficients must be a mapping, got {coefficients!r}")
        if set(coefficients) != set(self._names):
            raise ValueError(
                "coefficients must name exactly this set's components; missing: "
                f"{sorted(set(self._names) - set(coefficients))}, unknown: "
                f"{sorted(set(coefficients) - set(self._names))}"
            )
        checked = {}
        for name in self._names:
            value = _check_number(coefficients[name], f"coefficients[{name!r}]")
            if self._domain == "nonnegative" and value < 0.0:
                raise ValueError(
                    f"coefficients[{name!r}] is {value!r}, but this set declares a "
                    "non-negative coefficient domain — a negative coefficient asks a "
                    "question its geometry was not built to answer"
                )
            checked[name] = value
        return checked

    def _single_corners(self):
        """Yield the point where exactly one component deviates, for every component."""
        share = min(1.0, self._budget)
        corners = []
        for moved in self._names:
            for side in self._adverse:
                half = getattr(self, f"_deviation_{side}")[moved]
                offset = (share * half) * (1.0 if side == "above" else -1.0)
                corners.append(
                    tuple(
                        self._nominal[n] + (offset if n == moved else 0.0) for n in self._names
                    )
                )
        return corners

    def _saturating_corners(self, room, seed):
        """Yield corners that spend the whole budget, enumerated or sampled."""
        full, frac = self._budget_split()
        n = len(self._names)
        if room <= 0 or full < 1 or (full == 1 and frac <= 0.0) or full > n:
            return []
        directions = self._adverse
        extras = (n - full) * len(directions) if frac > 0.0 else 1
        total = math.comb(n, full) * len(directions) ** full * extras
        if total <= room:
            return list(self._enumerate_saturating(full, frac))
        return self._sample_saturating(full, frac, room, seed)

    def _enumerate_saturating(self, full, frac):
        """Walk every budget-saturating corner in one canonical order."""
        for chosen in itertools.combinations(self._names, full):
            for sides in itertools.product(self._adverse, repeat=full):
                base = {name: side for name, side in zip(chosen, sides)}
                if frac <= 0.0:
                    yield self._corner(base, {})
                    continue
                for extra in self._names:
                    if extra in base:
                        continue
                    for side in self._adverse:
                        yield self._corner(base, {extra: side})

    def _sample_saturating(self, full, frac, room, seed):
        """Draw distinct budget-saturating corners from the pinned seeded recipe."""
        rng = _bootstrap_rng(seed, class_ref(type(self)))
        names = list(self._names)
        seen, corners = set(), []
        for _ in range(room * _SAMPLE_ATTEMPT_FACTOR):
            if len(corners) >= room:
                break
            chosen = rng.sample(names, full)
            base = {name: rng.choice(self._adverse) for name in chosen}
            partial = {}
            if frac > 0.0:
                rest = [n for n in names if n not in base]
                partial = {rng.choice(rest): rng.choice(self._adverse)}
            corner = self._corner(base, partial)
            if corner in seen:
                continue
            seen.add(corner)
            corners.append(corner)
        return corners

    def _corner(self, full_sides, partial_sides):
        """Build one realization row from the components that move and how far."""
        frac = self._budget_split()[1]
        row = []
        for name in self._names:
            offset = 0.0
            if name in full_sides:
                side, share = full_sides[name], 1.0
            elif name in partial_sides:
                side, share = partial_sides[name], frac
            else:
                side, share = None, 0.0
            if side is not None:
                half = getattr(self, f"_deviation_{side}")[name]
                offset = share * half * (1.0 if side == "above" else -1.0)
            row.append(self._nominal[name] + offset)
        return tuple(row)


class BudgetedProbabilitySet(BudgetedUncertaintySet):
    """A budgeted set over per-component PROBABILITIES, adverse when larger.

    For a family of estimated probabilities that enter a decision through a
    non-negatively weighted linear constraint -- the share of chosen components
    expected to be wrong, say, capped at some tolerance. Larger is worse, so the
    adverse extreme is the maximum; probabilities live in the unit interval, so
    a deviation is clamped to what the interval actually allows and a component
    already at one refuses rather than pretending to a widening it cannot take.

    **The deviation is the caller's statement, and this set inherits exactly
    its quality.** Handed a widened point estimate it makes a widened set, not a
    bound. If the number passed in does not attain the coverage its producer
    hoped for, nothing computed here does either. No coverage is claimed.

    Parameters
    ----------
    nominal : mapping
        ``name -> float`` in ``[0, 1]``.
    deviation_below : mapping
        ``name -> 0.0``. A non-negative coefficient can never make a SMALLER
        probability adverse, so this half is never read and must be zero.
    deviation_above : mapping
        ``name -> float > 0``, how much larger each probability might be.
    budget : float
        As :class:`BudgetedUncertaintySet` takes it.

    Examples
    --------
    Two estimated probabilities, at most one of them badly wrong::

        u = BudgetedProbabilitySet(
            nominal={"a": 0.2, "b": 0.3},
            deviation_below={"a": 0.0, "b": 0.0},
            deviation_above={"a": 0.1, "b": 0.05},
            budget=1.0,
        )
        u.worst_case({"a": 1.0, "b": 1.0}).value
        # -> 0.6
    """

    def worst_case_sense(self):
        """Report that a larger probability is the adverse one.

        Returns
        -------
        str
            ``"max"``.
        """
        return "max"

    def component_bounds(self):
        """Report the unit interval a probability may not leave.

        Returns
        -------
        tuple
            ``(0.0, 1.0)``.
        """
        return (0.0, 1.0)

    def coefficient_domain(self):
        """Report that a decision reads these components non-negatively.

        Returns
        -------
        str
            ``"nonnegative"``.
        """
        return "nonnegative"


class BudgetedMeanSet(BudgetedUncertaintySet):
    """A budgeted set over ESTIMATION uncertainty in expected values.

    For a family of estimated means whose deviations say how far the AVERAGE
    could be from its point estimate given finite, dependent evidence. That
    uncertainty shrinks as evidence accumulates, which is exactly what
    distinguishes it from :class:`BudgetedOutcomeSet`, and the two are separate
    classes so that neither can absorb the other: a set built here can only ever
    see the estimation numbers it was handed.

    A decision coefficient may take either sign, so BOTH halves are read and
    both must be positive. Which half is adverse follows from the coefficient's
    sign -- larger is better for a positive coefficient, so smaller is adverse.

    Parameters
    ----------
    nominal : mapping
        ``name -> float``, the point estimates.
    deviation_below, deviation_above : mapping
        ``name -> float > 0``, how far below and above the point estimate the
        true expected value might sit. A two-sided interval's two distances are
        the natural source; nothing here requires them to be equal.
    budget : float
        As :class:`BudgetedUncertaintySet` takes it.

    Examples
    --------
    Two estimated means, at most one of them at its adverse end::

        u = BudgetedMeanSet(
            nominal={"a": 1.0, "b": 2.0},
            deviation_below={"a": 0.1, "b": 0.3},
            deviation_above={"a": 0.1, "b": 0.3},
            budget=1.0,
        )
        u.worst_case({"a": 1.0, "b": 1.0}).value
        # -> 2.7
    """

    def worst_case_sense(self):
        """Report that a smaller expected value is the adverse one.

        Returns
        -------
        str
            ``"min"``.
        """
        return "min"

    def component_bounds(self):
        """Report that an expected value has no natural clamp.

        Returns
        -------
        tuple
            ``(None, None)``.
        """
        return (None, None)

    def coefficient_domain(self):
        """Report that a decision may read these components with either sign.

        Returns
        -------
        str
            ``"real"``.
        """
        return "real"


class BudgetedOutcomeSet(BudgetedUncertaintySet):
    """A budgeted set over the dispersion of a REALIZED outcome.

    For a family of single future draws whose deviations say how far one
    realization could land from its centre. That spread is a property of the
    world, not of the sample, so it does NOT shrink as evidence accumulates --
    the opposite of :class:`BudgetedMeanSet`, and the reason the two are
    separate classes rather than one class with a label. Reading an estimation
    interval as an outcome band is the error the separation makes structurally
    impossible.

    Its :meth:`~BudgetedUncertaintySet.realizations` output is the weighted
    joint form a scenario-consuming optimizer takes directly: shared weights and
    one array per component, point ``k`` meaning the same simultaneous state in
    every array. Taking that pair means naming the weighting it carries, which
    is a CONVENTION and never an estimated measure; read the module docstring
    on the difference before using those weights as probabilities.

    Parameters
    ----------
    nominal : mapping
        ``name -> float``, the centre of each component's outcome.
    deviation_below, deviation_above : mapping
        ``name -> float > 0``, how far below and above its centre one
        realization could land. Empirical tail quantiles of out-of-sample
        residuals are the natural source; nothing here requires symmetry.
    budget : float
        As :class:`BudgetedUncertaintySet` takes it.

    Examples
    --------
    Two outcomes, at most one of them at its adverse end::

        u = BudgetedOutcomeSet(
            nominal={"a": 1.0, "b": 1.0},
            deviation_below={"a": 0.02, "b": 0.01},
            deviation_above={"a": 0.02, "b": 0.01},
            budget=1.0,
        )
        weights, draws = u.realizations(5, seed=0).weighted_draws(
            reading_weights_as="convention"
        )
        len(weights)
        # -> 5
    """

    def worst_case_sense(self):
        """Report that a smaller realized outcome is the adverse one.

        Returns
        -------
        str
            ``"min"``.
        """
        return "min"

    def component_bounds(self):
        """Report that a realized outcome has no natural clamp.

        Returns
        -------
        tuple
            ``(None, None)``.
        """
        return (None, None)

    def coefficient_domain(self):
        """Report that a decision may read these components with either sign.

        Returns
        -------
        str
            ``"real"``.
        """
        return "real"


#: The shipped members, by name. A project registers its own geometry beside
#: them with :func:`register_uncertainty_set`.
UNCERTAINTY_SETS = {
    "probability": {
        "cls": BudgetedProbabilitySet,
        "doc": "Per-component probabilities in the unit interval; larger is adverse.",
    },
    "mean": {
        "cls": BudgetedMeanSet,
        "doc": "Estimation uncertainty in an expected value; shrinks with evidence.",
    },
    "outcome": {
        "cls": BudgetedOutcomeSet,
        "doc": "Dispersion of a single realized outcome; does not shrink with evidence.",
    },
}


def register_uncertainty_set(name, cls, doc=""):
    """Add an uncertainty-set family member. Duplicates raise.

    Parameters
    ----------
    name : str
        The registry key.
    cls : type
        A :class:`BudgetedUncertaintySet` subclass.
    doc : str
        One line on what the member assumes and when to reach for it.

    Raises
    ------
    ValueError
        When ``name`` is already registered, or ``cls`` is not a
        :class:`BudgetedUncertaintySet` subclass.

    Examples
    --------
    A project brings its own geometry::

        register_uncertainty_set(
            "duration", MyDurationSet, doc="Task durations, never negative."
        )
    """
    if name in UNCERTAINTY_SETS:
        raise ValueError(f"uncertainty set {name!r} is already registered")
    if not (isinstance(cls, type) and issubclass(cls, BudgetedUncertaintySet)):
        raise ValueError(
            f"uncertainty set {name!r} must be a BudgetedUncertaintySet subclass, got {cls!r}"
        )
    UNCERTAINTY_SETS[name] = {"cls": cls, "doc": doc}


def uncertainty_set(name):
    """Look up a registered uncertainty-set member, loudly.

    Parameters
    ----------
    name : str
        The registry key.

    Returns
    -------
    dict
        ``{"cls", "doc"}``.

    Raises
    ------
    ValueError
        When nothing is registered under ``name``; the message lists what is.

    Examples
    --------
    Reach a member by name::

        entry = uncertainty_set("mean")
        entry["cls"].__name__
        # -> 'BudgetedMeanSet'
    """
    if name not in UNCERTAINTY_SETS:
        raise ValueError(
            f"unknown uncertainty set {name!r}; registered: {sorted(UNCERTAINTY_SETS)}"
        )
    return UNCERTAINTY_SETS[name]
