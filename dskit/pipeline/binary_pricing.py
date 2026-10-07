"""Fair value of a binary contract that settles on an average: the law, the payoff geometries, the node.

A binary contract pays 1 when a settlement value ``A`` lands in a region and 0 otherwise, and
``A`` is not a price at one instant but the AVERAGE of a price ``S`` over the last ``W`` units
of time before the settlement instant. A prediction market on an averaged index and a digital
option on one need the same three pieces, and this module owns them: the law of ``A``, the
regions (payoff geometries) a contract can pay on, and a node that prices a table of rows.

The variance of the average. Let ``S`` be a martingale with log-volatility ``sigma`` per square
root of a time unit, so ``ln S_u = ln S_0 + sigma B_u - sigma^2 u / 2`` for a Brownian motion
``B``. The settlement value is ``A = (1 / W) * integral of S_u du`` over ``[tau - W, tau]``,
``tau`` being the time from now to the END of the window. Approximating ``ln A`` by the average
of ``ln S`` over the window (the Jensen gap is of order ``sigma^2 W``, small against
``sigma^2 tau`` when the window is short against the horizon), the random part is
``sigma * (1 / W) * integral of B_u du``. For ``a = tau - W``::

    Var[(1 / W) int_a^(a + W) B_u du] = a + W / 3 = tau - 2 W / 3

because the double integral of ``min(u, v)`` over ``[a, a + W]^2`` is ``a W^2 + W^3 / 3``. So
the part of the horizon spent averaging contributes one third of its point variance, and
``v = sigma^2 (tau - 2 W / 3)``. The mean is fixed by ``E[A] = S_0`` (``S`` is a martingale), so
``ln A ~ Normal(ln S_0 - v / 2, v)`` and ``P(A >= K) = Phi((ln(S_0 / K) - v / 2) / sqrt(v))``.
A discrete average of 60 one-second prints has variance ``tau - 2 W / 3`` to within 0.2 percent
at ``tau`` = 400 and ``W`` = 60 (a test checks this exactly). The law needs ``tau >= W``: inside
the window part of ``A`` is already observed, so a row there is marked, never priced.

Timing. A decision is made on information known at ONE instant ``I`` (the spot, the quote and
the volatility are all as of it), and an order placed on it can only fill at ``E = I + lag``
with ``lag > 0``: a fill cannot precede the information it acts on. The horizon ``tau`` runs
from ``E`` to the settlement instant, which is shorter than the horizon from ``I`` by the lag,
so the fair value is slightly overconfident, never slightly optimistic about the market. A row
whose ``E`` is not strictly before settlement is not priced and is listed on the node's census.

Which venue string means which payoff, and which column holds which input, is configuration:
:class:`BinaryFairValue` takes every column name as a param and carries no vocabulary of its own.

Import cost: stdlib only.
"""

import math
from abc import ABC, abstractmethod
from statistics import NormalDist
from types import MappingProxyType

from dskit.pipeline.node import Node, reject_unknown_params
from dskit.pipeline.records import number_ok, price_ok

__all__ = [
    "ABOVE",
    "BELOW",
    "BETWEEN",
    "LOWER",
    "PAYOFFS",
    "STATUSES",
    "STATUS_OK",
    "STATUS_SUFFIX",
    "TAU_SUFFIX",
    "UPPER",
    "VAR_SUFFIX",
    "Above",
    "AveragedLognormal",
    "Below",
    "Between",
    "BinaryFairValue",
    "BinaryPayoff",
    "payoff",
]

#: The two bounds a geometry may read: the contract's lower and upper limit on the settlement value.
LOWER = "lower"
UPPER = "upper"

#: The geometry names a document's ``payoff_field`` may carry.
ABOVE = "above"
BELOW = "below"
BETWEEN = "between"

#: The status a priced row carries; every other status in :data:`STATUSES` names why it was not priced.
STATUS_OK = "ok"
#: Every status the node writes, in the order its checks run.
STATUSES = (
    STATUS_OK, "no_spot", "no_vol", "no_instant", "exec_not_before_settle", "tau_inside_window",
    "unknown_payoff", "bad_bounds",
)

#: Suffixes of the three columns written beside ``fair_field``: the log variance used, the horizon
#: priced over, and the status.
VAR_SUFFIX = "_var"
TAU_SUFFIX = "_tau"
STATUS_SUFFIX = "_status"

_NORMAL = NormalDist()
_MS_PER_S = 1000


def _ms(seconds):
    """Return a duration in seconds as whole milliseconds, the unit instants are in."""
    return round(seconds * _MS_PER_S)


def _geometry(name):
    """Return the geometry a row's payoff name picks, or None when it names none."""
    return PAYOFFS.get(name) if isinstance(name, str) else None


class AveragedLognormal:
    """The lognormal law of a ``window``-long average, seen ``tau`` before it ends.

    Time is in any one unit the caller chooses, with ``sigma`` per square root of that unit.

    Parameters
    ----------
    sigma : float
        Log-return standard deviation per square root of a time unit, positive.
    tau : float
        Time from now to the END of the averaging window, positive and at least ``window``.
    window : float
        Length of the averaging window, at least 0 (0 is the law of the price at one instant).

    Raises
    ------
    ValueError
        When an argument is not a finite number in its range.

    Examples
    --------
    ATM with no averaging, ``sigma * sqrt(tau) = 0.2``::

        model = AveragedLognormal(0.01, 400.0, 0.0)
        model.variance                 # 0.04
        model.survival(100.0, 100.0)   # 0.4601721627 (Phi(-0.1))
    """

    def __init__(self, sigma, tau, window):
        if not (number_ok(sigma) and sigma > 0.0):
            raise ValueError(f"sigma must be a positive finite number, got {sigma!r}")
        if not (number_ok(window) and window >= 0.0):
            raise ValueError(f"window must be a finite number >= 0, got {window!r}")
        if not (number_ok(tau) and tau > 0.0 and tau >= window):
            raise ValueError(f"tau must be positive and at least the averaging window ({window!r}), got {tau!r}")
        #: Variance of ``ln A``: ``sigma^2 (tau - window + window / 3)``, that is ``sigma^2 (tau - 2 window / 3)``.
        self.variance = sigma * sigma * (tau - window + window / 3.0)

    def survival(self, spot, strike):
        """Return ``P(A >= strike)`` for a martingale spot.

        Parameters
        ----------
        spot : float
            The current price, positive.
        strike : float
            The strike, positive.

        Returns
        -------
        float
            The probability the settlement value is at or above ``strike``.

        Raises
        ------
        ValueError
            When ``spot`` or ``strike`` is not a positive finite number.
        """
        if not (price_ok(spot) and price_ok(strike)):
            raise ValueError(f"spot and strike must be positive finite numbers, got {spot!r}, {strike!r}")
        d = (math.log(spot / strike) - self.variance / 2.0) / math.sqrt(self.variance)
        return _NORMAL.cdf(d)


class BinaryPayoff(ABC):
    """One payoff geometry: which bounds it reads and how it prices from a survival function.

    A region the contract pays on is a union of half-lines on its bounds, so each geometry
    combines ``survival(K) = P(A >= K)`` into the probability of YES. Abstract: a subclass names
    its ``bounds`` and supplies ``_probability``; the check that the bounds it reads are usable
    is the base's, in :meth:`bounds_problem`.

    Examples
    --------
    Price a range from a survival function ``P(A >= K)``::

        survival = lambda strike: 0.6 if strike < 100 else 0.3
        PAYOFFS["between"].yes_probability(survival, lower=99.0, upper=101.0)
        # -> 0.3
    """

    #: The geometry's name, the value a document's ``payoff_field`` carries.
    name = ""

    @property
    @abstractmethod
    def bounds(self):
        """Name the bounds this geometry reads, in order (tuple of ``"lower"`` / ``"upper"``)."""

    def bounds_problem(self, lower, upper):
        """Say what is wrong with the bounds this geometry would read, or None when they are usable.

        Parameters
        ----------
        lower : float or None
            The contract's lower bound.
        upper : float or None
            The contract's upper bound.

        Returns
        -------
        str or None
            A problem naming the unusable bound, None when every bound read is a finite number.
        """
        given = {LOWER: lower, UPPER: upper}
        for bound in self.bounds:
            if not number_ok(given[bound]):
                return f"{self.name} reads {bound}, which must be a finite number, got {given[bound]!r}"
        return None

    def yes_probability(self, survival, lower, upper):
        """Return P(YES) given ``survival(K) = P(A >= K)`` and the contract's bounds.

        Parameters
        ----------
        survival : callable
            ``survival(strike) -> float``, the probability the settlement value is at or above
            ``strike``.
        lower : float or None
            The contract's lower bound.
        upper : float or None
            The contract's upper bound.

        Returns
        -------
        float
            The probability the contract pays.

        Raises
        ------
        ValueError
            When a bound the geometry reads is not usable (see :meth:`bounds_problem`).
        """
        problem = self.bounds_problem(lower, upper)
        if problem is not None:
            raise ValueError(problem)
        return self._probability(survival, lower, upper)

    @abstractmethod
    def _probability(self, survival, lower, upper):
        """Return P(YES) for bounds already checked."""


class Above(BinaryPayoff):
    """YES when the value is at or above the lower bound.

    Examples
    --------
    Price it from a survival function::

        PAYOFFS["above"].yes_probability(lambda k: 0.4, lower=100.0, upper=None)
        # -> 0.4
    """

    name = ABOVE
    bounds = (LOWER,)

    def _probability(self, survival, lower, upper):
        return survival(lower)


class Below(BinaryPayoff):
    """YES when the value is below the upper bound.

    Examples
    --------
    Price it from a survival function::

        PAYOFFS["below"].yes_probability(lambda k: 0.4, lower=None, upper=100.0)
        # -> 0.6
    """

    name = BELOW
    bounds = (UPPER,)

    def _probability(self, survival, lower, upper):
        return 1.0 - survival(upper)


class Between(BinaryPayoff):
    """YES when the value is at or above the lower bound and below the upper bound.

    A range with ``lower == upper`` is empty and pays on no value; ``lower > upper`` is refused.

    Examples
    --------
    Price it from a survival function::

        PAYOFFS["between"].yes_probability(lambda k: 0.6 if k < 100 else 0.3, lower=99.0, upper=101.0)
        # -> 0.3
    """

    name = BETWEEN
    bounds = (LOWER, UPPER)

    def bounds_problem(self, lower, upper):
        """Say what is wrong with a range: a missing bound, or a lower bound above the upper.

        Parameters
        ----------
        lower : float or None
            The range's lower bound.
        upper : float or None
            The range's upper bound.

        Returns
        -------
        str or None
            A problem, or None when both bounds are finite numbers with ``lower <= upper``.
        """
        problem = super().bounds_problem(lower, upper)
        if problem is None and lower > upper:
            return f"between needs lower <= upper, got {lower!r} > {upper!r}"
        return problem

    def _probability(self, survival, lower, upper):
        return survival(lower) - survival(upper)


#: name -> the one instance of that geometry, read-only; a project adds its own by subclassing, not by editing this.
PAYOFFS = MappingProxyType({cls.name: cls() for cls in (Above, Below, Between)})


def payoff(name):
    """Return the geometry called ``name``.

    Parameters
    ----------
    name : str
        One of :data:`PAYOFFS`.

    Returns
    -------
    BinaryPayoff
        The geometry.

    Raises
    ------
    ValueError
        When ``name`` is not a known geometry (the message lists the known ones).

    Examples
    --------
    Look up a geometry by the name a row carries::

        payoff("below").bounds   # ("upper",)
    """
    try:
        return PAYOFFS[name]
    except (KeyError, TypeError):
        raise ValueError(f"unknown payoff {name!r}; known: {sorted(PAYOFFS)}") from None


class BinaryFairValue(Node):
    """Add the fair value of YES to each decision row (role ``transform``).

    Inputs: ``records`` with the spot, the volatility, the payoff name, the bounds, the
    information instant and the settlement instant. Outputs: ``records``, every row with four
    more columns, ``<fair_field>`` (P(YES), or None), ``<fair_field>_var`` (the log variance of
    the settlement value), ``<fair_field>_tau`` (the horizon from the fill to settlement, in
    seconds) and ``<fair_field>_status`` (``ok`` or why the row was not priced, one of
    :data:`STATUSES`); and ``census``, ``{"rows", "priced", "by_status"}``, so a row set aside
    is counted by reason and never dropped quietly. A row that cannot be priced is never guessed.

    Instants are epoch milliseconds, durations seconds, ``sigma`` per square root of a second.
    The spot must be in the units the bounds are in.

    Parameters
    ----------
    params : dict
        All REQUIRED. ``vol_field``, ``spot_field``, ``payoff_field``, ``lower_field``,
        ``upper_field``, ``decision_field`` (the information instant ``I``) and
        ``settle_field`` (the settlement instant, the end of the averaging window): the input
        column names. ``fair_field``: the output column; it and its ``_var`` / ``_tau`` /
        ``_status`` companions may not be any input column. ``exec_lag_s``: seconds from ``I``
        to the fill, at least one millisecond (a fill cannot precede its information).
        ``averaging_window_s``: the window's length in seconds, at least 0.

    Examples
    --------
    Price every row from a realised-volatility column, filling 5 seconds after the information::

        node = BinaryFairValue("fair", {
            "vol_field": "rv", "spot_field": "spot", "payoff_field": "payoff",
            "lower_field": "lo", "upper_field": "hi", "decision_field": "decision_ms",
            "settle_field": "settle_ms", "fair_field": "fair", "exec_lag_s": 5,
            "averaging_window_s": 60})
        out = node.run(ctx, {"records": rows})
        # -> out["records"][0]["fair"] is P(YES), or None with fair_status saying why
    """

    role = "transform"
    outputs = ("records", "census")
    _INPUT_FIELDS = ("vol_field", "spot_field", "payoff_field", "lower_field", "upper_field",
                     "decision_field", "settle_field")
    _PARAMS = ("fair_field", *_INPUT_FIELDS, "exec_lag_s", "averaging_window_s")

    @classmethod
    def _collision_problems(cls, params):
        """Problems with an output column that would overwrite an input column."""
        name = params.get("fair_field")
        if not isinstance(name, str) or not name:
            return []
        outputs = {name, name + VAR_SUFFIX, name + TAU_SUFFIX, name + STATUS_SUFFIX}
        clash = sorted(outputs & {params.get(k) for k in cls._INPUT_FIELDS})
        return ([f"fair_field {name!r} writes the columns {sorted(outputs)}, which would overwrite the input "
                 f"column(s) {clash}"] if clash else [])

    @classmethod
    def validate_params(cls, params):
        """List problems with ``params``, empty when none.

        Parameters
        ----------
        params : dict
            The node's declared params.

        Returns
        -------
        list of str
            One problem per unknown, missing or unusable knob.
        """
        problems = []
        reject_unknown_params(problems, params, cls._PARAMS)
        for name in ("fair_field", *cls._INPUT_FIELDS):
            value = params.get(name)
            if not isinstance(value, str) or not value:
                problems.append(f"{name} is required: a non-empty column name, got {value!r}")
        window, lag = params.get("averaging_window_s"), params.get("exec_lag_s")
        if not (number_ok(window) and window >= 0.0):
            problems.append(f"averaging_window_s is required: a number of seconds >= 0, got {window!r}")
        if not (number_ok(lag) and _ms(lag) >= 1):
            problems.append(f"exec_lag_s is required: a number of seconds of at least one millisecond "
                            f"(a fill cannot precede its information), got {lag!r}")
        return problems + cls._collision_problems(params)

    @classmethod
    def serving_effect(cls, params, verified_run_evidence):
        """Answer ``pure``: the columns are a function of the row and the params.

        Parameters
        ----------
        params : dict
            Unused.
        verified_run_evidence : dict
            Unused.

        Returns
        -------
        str
            ``"pure"``.
        """
        return "pure"

    def validate_inputs(self, inputs):
        """Refuse a ``records`` port that is not a list (a one-shot iterable would arrive consumed).

        Parameters
        ----------
        inputs : dict
            The wired ports.

        Returns
        -------
        list of str
            A problem when ``records`` is not a list; empty otherwise.
        """
        if isinstance(inputs.get("records"), list):
            return []
        return [f"records must be a list of rows, got {type(inputs.get('records')).__name__}"]

    def _horizon(self, row):
        """Return the seconds from the fill to settlement, or None when an instant is not a number."""
        decision, settle = row.get(self.params["decision_field"]), row.get(self.params["settle_field"])
        if not (number_ok(decision) and number_ok(settle)):
            return None
        return (settle - (decision + _ms(self.params["exec_lag_s"]))) / _MS_PER_S

    def _bounds_usable(self, geometry, row):
        """Say whether the bounds ``geometry`` reads are usable and strictly positive (the law's strikes)."""
        lower, upper = row.get(self.params["lower_field"]), row.get(self.params["upper_field"])
        given = {LOWER: lower, UPPER: upper}
        return geometry.bounds_problem(lower, upper) is None and all(price_ok(given[b]) for b in geometry.bounds)

    def _refusal(self, row, tau):
        """Return the status that says why ``row`` cannot be priced, or None when it can."""
        sigma = row.get(self.params["vol_field"])
        if not price_ok(row.get(self.params["spot_field"])):
            return "no_spot"
        if not (number_ok(sigma) and sigma > 0.0):
            return "no_vol"
        if tau is None:
            return "no_instant"
        if tau <= 0.0:
            return "exec_not_before_settle"
        if tau < self.params["averaging_window_s"]:
            return "tau_inside_window"
        geometry = _geometry(row.get(self.params["payoff_field"]))
        if geometry is None:
            return "unknown_payoff"
        return None if self._bounds_usable(geometry, row) else "bad_bounds"

    def _priced(self, row):
        """Write the four fair-value columns onto ``row`` (a copy)."""
        name, params = self.params["fair_field"], self.params
        tau = self._horizon(row)
        reason = self._refusal(row, tau)
        row.update({name: None, name + VAR_SUFFIX: None, name + TAU_SUFFIX: tau,
                    name + STATUS_SUFFIX: reason or STATUS_OK})
        if reason is None:
            model = AveragedLognormal(row[params["vol_field"]], tau, params["averaging_window_s"])
            spot = row[params["spot_field"]]
            row[name] = PAYOFFS[row[params["payoff_field"]]].yes_probability(
                lambda strike: model.survival(spot, strike), row[params["lower_field"]], row[params["upper_field"]])
            row[name + VAR_SUFFIX] = model.variance
        return row

    def run(self, ctx, inputs):
        """Price every row; rows that cannot be priced say why.

        Parameters
        ----------
        ctx : NodeContext
            Unused.
        inputs : dict
            ``records``: the rows to price.

        Returns
        -------
        dict
            ``{"records": [...], "census": {...}}``, new row dicts in input order.
        """
        records = [self._priced(dict(row)) for row in inputs["records"]]
        statuses = [row[self.params["fair_field"] + STATUS_SUFFIX] for row in records]
        census = {"rows": len(records), "priced": statuses.count(STATUS_OK),
                  "by_status": {status: statuses.count(status) for status in STATUSES}}
        self.log.info("priced %d of %d row(s) into %s", census["priced"], census["rows"], self.params["fair_field"])
        return {"records": records, "census": census}
