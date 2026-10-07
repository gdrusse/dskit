"""Venue-neutral fee mechanics: a per-order fee rule, a rounding policy, a config-built model.

A trading cost that depends on the price of a binary contract is usually not a flat rate. It
is a rule of the form ``rate * C * P * (1 - P)`` for an order of ``C`` contracts at a price
``P`` in ``[0, 1]`` (a fee that vanishes at the ends and peaks at a coin flip), charged ONCE
PER ORDER and then rounded to a billing grid. This module owns those mechanics and nothing
else: how the exact amount is formed, how an order's exact amount becomes a charge, and how a
document names both.

What it deliberately does NOT own is anything a particular venue decides. The rate, its
multiplier per series, the venue's own names for its fee types and the grid it bills on are
data a project supplies (a config, or its own thin code); the mechanics take the rate as a
call argument and the grid as a policy object, and carry no table of either. A project that
prices a second venue adds a config, never a line here.

Two seams, both abstract so an incomplete subclass refuses to construct:

- :class:`RoundingPolicy` turns an order's exact fee into the charged fee. :class:`NoRounding`
  returns it as is; :class:`CeilToTick` rounds UP to the next multiple of a tick, after snapping
  the amount to ``guard_decimals`` places of ticks. The snap is a numerical guard, not a
  business value: binary floating point cannot hold ``0.07 * 100 * 0.25 * 100`` exactly (it is
  175.00000000000003), and a bare ceiling would bill a whole extra tick for that dust.
- :class:`FeeModel` is the rule for one order. Its :meth:`~FeeModel.order_fee` checks the order,
  asks the subclass for the exact amount (``_exact_fee``) and hands it to the policy. Rounding
  applies to the ORDER, so the fee per contract depends on the order size: one contract at a
  coin flip can pay a whole tick for a fraction of one, a hundred pay it once.

:data:`FEE_MECHANICS` and :data:`ROUNDING_POLICIES` name the shipped classes so a JSON document
can choose them; :func:`fee_model_from_spec` builds a model from such a spec and
:func:`fee_spec_problems` lists what is wrong with one (default-deny: an unknown key is a
problem, so a typo cannot silently fall back to a default).

Import cost: stdlib only.
"""

import math
from abc import ABC, abstractmethod
from types import MappingProxyType

from dskit.pipeline.records import number_ok

__all__ = [
    "DEFAULT_GUARD_DECIMALS",
    "FEE_MECHANICS",
    "ROUNDING_POLICIES",
    "CeilToTick",
    "FeeModel",
    "NoRounding",
    "ProbabilityQuadraticFee",
    "RoundingPolicy",
    "ZeroFee",
    "fee_model_from_spec",
    "fee_spec_problems",
]

#: Places the amount (counted in ticks) is rounded to before the ceiling: a numerical guard
#: that absorbs binary representation dust without masking a genuine fraction-of-a-tick
#: overage (which is many orders of magnitude above 1e-9 of a tick).
DEFAULT_GUARD_DECIMALS = 9

_SPEC_NOTES = ("notes",)


def _check_amount(exact):
    """Refuse an exact fee that is not a finite number of at least zero."""
    if not (number_ok(exact) and exact >= 0.0):
        raise ValueError(f"exact must be a finite number >= 0, got {exact!r}")


def _check_order(contracts, price, rate):
    """Refuse a fill no fee formula may price."""
    if isinstance(contracts, bool) or not isinstance(contracts, int) or contracts < 0:
        raise ValueError(f"contracts must be a non-negative integer, got {contracts!r}")
    if not (number_ok(price) and 0.0 <= price <= 1.0):
        raise ValueError(f"price must be a number in [0, 1], got {price!r}")
    if not (number_ok(rate) and rate >= 0.0):
        raise ValueError(f"rate must be a finite number >= 0, got {rate!r}")


class RoundingPolicy(ABC):
    """How an order's exact fee becomes the fee charged.

    Abstract: a subclass supplies :meth:`round_fee`. ``_PARAMS`` names the knobs a JSON spec may
    set on the policy and ``_REQUIRED`` the ones it must (default-deny: anything else is a
    problem); both are empty for a policy with no knobs.

    Examples
    --------
    A policy that bills whole units, used by a model like any shipped one::

        class WholeUnits(RoundingPolicy):
            def round_fee(self, exact):
                return float(math.ceil(exact))

        model = ProbabilityQuadraticFee(WholeUnits())
        model.order_fee(100, 0.5, 0.07)   # 2.0
    """

    #: The name a spec's ``rounding.policy`` gives; set by each shipped subclass.
    name = ""
    #: Knobs a spec may set, in addition to ``policy`` and ``notes``.
    _PARAMS = ()
    #: The subset of ``_PARAMS`` a spec must set.
    _REQUIRED = ()

    @abstractmethod
    def round_fee(self, exact):
        """Return the charge for an order whose exact fee is ``exact``.

        Parameters
        ----------
        exact : float
            The exact fee of one order, finite and at least 0.

        Returns
        -------
        float
            The amount charged.

        Raises
        ------
        ValueError
            When ``exact`` is not a finite number of at least 0.
        """


class NoRounding(RoundingPolicy):
    """Charge the exact amount.

    Examples
    --------
    The bare formula, nothing billed on a grid::

        NoRounding().round_fee(1.7248)   # 1.7248
    """

    name = "none"

    def round_fee(self, exact):
        """Return ``exact`` unchanged.

        Parameters
        ----------
        exact : float
            The exact fee of one order, finite and at least 0.

        Returns
        -------
        float
            ``exact``.

        Raises
        ------
        ValueError
            When ``exact`` is not a finite number of at least 0.
        """
        _check_amount(exact)
        return exact


class CeilToTick(RoundingPolicy):
    """Round an order's fee UP to the next multiple of ``tick``, after a float guard.

    The amount is expressed in ticks, rounded to ``guard_decimals`` places (so a hair of
    binary dust above a whole tick is not billed as another tick), then raised to the next
    whole tick. A positive amount below one tick therefore bills one full tick.

    Parameters
    ----------
    tick : float
        The billing grid, a positive finite number in the fee's own unit.
    guard_decimals : int, optional
        Places, counted in ticks, the amount is snapped to before the ceiling; a non-negative
        integer, :data:`DEFAULT_GUARD_DECIMALS` when omitted.

    Raises
    ------
    ValueError
        When ``tick`` is not a positive finite number or ``guard_decimals`` is not a
        non-negative integer.

    Examples
    --------
    Bill on a grid of 0.01 and watch the guard absorb one ulp of dust::

        policy = CeilToTick(0.01)
        policy.round_fee(0.0175)              # 0.02
        policy.round_fee(1.7500000000000002)  # 1.75
    """

    name = "ceil_to_tick"
    _PARAMS = ("tick", "guard_decimals")
    _REQUIRED = ("tick",)

    def __init__(self, tick, guard_decimals=DEFAULT_GUARD_DECIMALS):
        if not (number_ok(tick) and tick > 0.0):
            raise ValueError(f"tick must be a positive finite number, got {tick!r}")
        if isinstance(guard_decimals, bool) or not isinstance(guard_decimals, int) or guard_decimals < 0:
            raise ValueError(f"guard_decimals must be a non-negative integer, got {guard_decimals!r}")
        self.tick = tick
        self.guard_decimals = guard_decimals
        #: Ticks per unit: one division here keeps a decimal grid (0.01, 1e-5) exact in both directions.
        self._per_unit = 1.0 / tick

    def round_fee(self, exact):
        """Return ``exact`` rounded up to the next tick.

        Parameters
        ----------
        exact : float
            The exact fee of one order, finite and at least 0.

        Returns
        -------
        float
            The charge, a whole number of ticks.

        Raises
        ------
        ValueError
            When ``exact`` is not a finite number of at least 0.
        """
        _check_amount(exact)
        ticks = round(exact * self._per_unit, self.guard_decimals)
        return math.ceil(ticks) / self._per_unit


#: name -> shipped rounding policy class, read-only; a project adds its own by subclassing, not by editing this.
ROUNDING_POLICIES = MappingProxyType({cls.name: cls for cls in (NoRounding, CeilToTick)})


class FeeModel(ABC):
    """The fee rule for one order: validate it, form the exact amount, round it.

    Abstract: a subclass supplies ``_exact_fee``. The rate is an argument of
    :meth:`order_fee`, never a property of the model, so one model prices every series a
    project has, each at its own rate.

    Parameters
    ----------
    rounding : RoundingPolicy, optional
        How the order's exact fee becomes the charge; :class:`NoRounding` when omitted.

    Raises
    ------
    ValueError
        When ``rounding`` is not a :class:`RoundingPolicy`.

    Examples
    --------
    A new mechanic is one hook; validation and rounding come with the base::

        class FlatPerContract(FeeModel):
            name = "flat_per_contract"

            def _exact_fee(self, contracts, price, rate):
                return rate * contracts

        FlatPerContract(CeilToTick(0.05)).order_fee(3, 0.5, 0.02)   # 0.1
    """

    #: The name a spec's ``mechanic`` gives; set by each shipped subclass.
    name = ""

    def __init__(self, rounding=None):
        rounding = NoRounding() if rounding is None else rounding
        if not isinstance(rounding, RoundingPolicy):
            raise ValueError(f"rounding must be a RoundingPolicy, got {rounding!r}")
        self.rounding = rounding

    def order_fee(self, contracts, price, rate):
        """Return the fee charged for ONE order.

        Parameters
        ----------
        contracts : int
            Contracts in the order, at least 0.
        price : float
            Price per contract, in ``[0, 1]``.
        rate : float
            The series' effective rate, at least 0; the caller forms it from its own schedule.

        Returns
        -------
        float
            The order's fee after the rounding policy.

        Raises
        ------
        ValueError
            When the order is one no formula may price.
        """
        _check_order(contracts, price, rate)
        return self.rounding.round_fee(self._exact_fee(contracts, price, rate))

    @abstractmethod
    def _exact_fee(self, contracts, price, rate):
        """Return the order's exact fee, before rounding, for an order already checked."""


class ProbabilityQuadraticFee(FeeModel):
    """``rate * C * P * (1 - P)`` for an order of ``C`` contracts at price ``P``.

    Zero at both ends of the price range, largest at ``P = 0.5``, symmetric about it.

    Examples
    --------
    A hundred contracts at a coin flip, billed on a grid of 0.01::

        model = ProbabilityQuadraticFee(CeilToTick(0.01))
        model.order_fee(100, 0.5, 0.07)   # 1.75
        model.order_fee(1, 0.5, 0.07)     # 0.02
    """

    name = "probability_quadratic"

    def _exact_fee(self, contracts, price, rate):
        return rate * contracts * price * (1.0 - price)


class ZeroFee(FeeModel):
    """No fee, for any valid order.

    Examples
    --------
    A fee-free series, still refusing a price outside ``[0, 1]``::

        ZeroFee().order_fee(100, 0.5, 0.07)   # 0.0
    """

    name = "zero"

    def _exact_fee(self, contracts, price, rate):
        return 0.0


#: name -> shipped fee model class, read-only; a project adds its own by subclassing, not by editing this.
FEE_MECHANICS = MappingProxyType({cls.name: cls for cls in (ProbabilityQuadraticFee, ZeroFee)})


def _vocabulary_problem(what, value, vocabulary):
    """Return the problem with a named choice, or None when ``value`` is one of ``vocabulary``."""
    if isinstance(value, str) and value in vocabulary:
        return None
    return f"{what} must be one of {sorted(vocabulary)}, got {value!r}"


def _knobs_of(rounding):
    """Return the policy knobs of a rounding block: everything but ``policy`` and ``notes``."""
    return {k: v for k, v in rounding.items() if k != "policy" and k not in _SPEC_NOTES}


def _rounding_problems(rounding):
    """List problems with a spec's ``rounding`` block, empty when it builds."""
    if not isinstance(rounding, dict):
        return [f"rounding is required: a map with a policy, got {rounding!r}"]
    problem = _vocabulary_problem("rounding.policy", rounding.get("policy"), ROUNDING_POLICIES)
    if problem:
        return [problem]
    cls, knobs = ROUNDING_POLICIES[rounding["policy"]], _knobs_of(rounding)
    problems = [f"rounding.{k} is not a knob of policy {cls.name!r}; its knobs are {list(cls._PARAMS)}"
                for k in knobs if k not in cls._PARAMS]
    problems += [f"rounding.{k} is required by policy {cls.name!r}" for k in cls._REQUIRED if k not in knobs]
    if problems:
        return problems
    try:
        cls(**knobs)
    except ValueError as exc:
        problems.append(f"rounding: {exc}")
    return problems


def fee_spec_problems(spec):
    """List what is wrong with a fee-model spec, empty when it builds.

    Parameters
    ----------
    spec : dict
        ``{"mechanic": <name in FEE_MECHANICS>, "rounding": {"policy": <name in
        ROUNDING_POLICIES>, ...that policy's knobs}}``; ``notes`` is allowed at either level.

    Returns
    -------
    list of str
        One problem per unknown key, missing required key or unusable value.

    Examples
    --------
    Check a document's fee block before building it::

        fee_spec_problems({"mechanic": "zero", "rounding": {"policy": "none"}})   # []
        fee_spec_problems({"mechanic": "zero"})
        # -> ["rounding is required: a map with a policy, got None"]
    """
    if not isinstance(spec, dict):
        return [f"fee spec must be a map with a mechanic and a rounding, got {spec!r}"]
    problems = [f"{k} is not a key of a fee spec; its keys are ['mechanic', 'rounding', 'notes']"
                for k in spec if k not in ("mechanic", "rounding") + _SPEC_NOTES]
    problem = _vocabulary_problem("mechanic", spec.get("mechanic"), FEE_MECHANICS)
    if problem:
        problems.append(problem)
    return problems + _rounding_problems(spec.get("rounding"))


def fee_model_from_spec(spec):
    """Build a fee model from a JSON-able spec.

    Parameters
    ----------
    spec : dict
        As :func:`fee_spec_problems` describes.

    Returns
    -------
    FeeModel
        The chosen mechanic, holding the chosen rounding policy.

    Raises
    ------
    ValueError
        When the spec has any problem; the message lists every one.

    Examples
    --------
    A quadratic fee billed on a grid of 0.01, as a document would declare it::

        model = fee_model_from_spec({
            "mechanic": "probability_quadratic",
            "rounding": {"policy": "ceil_to_tick", "tick": 0.01}})
        model.order_fee(100, 0.3, 0.07)   # 1.47
    """
    problems = fee_spec_problems(spec)
    if problems:
        raise ValueError("invalid fee spec: " + "; ".join(problems))
    rounding = spec["rounding"]
    return FEE_MECHANICS[spec["mechanic"]](ROUNDING_POLICIES[rounding["policy"]](**_knobs_of(rounding)))
