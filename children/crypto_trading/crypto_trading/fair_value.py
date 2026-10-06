"""Baseline fair value: a driftless lognormal, settled on a short average.

Kalshi's crypto contracts settle on the CF Benchmarks index: the average of its prices
over the last ``W`` seconds before the close (``W`` = 60 per the rulebook, a param
here). The model prices P(YES) at a decision instant ``tau`` seconds before the close.

Derivation of the variance. Let ``S`` be a martingale with log-vol ``sigma`` per sqrt
second, so ``ln S_u = ln S_0 + sigma B_u - sigma^2 u / 2`` for a Brownian motion ``B``.
The settlement value is ``A = (1/W) * integral of S_u du`` over ``[tau - W, tau]``.
Approximating ``ln A`` by the average of ``ln S`` over the window (the Jensen gap is
``O(sigma^2 W)``, tiny against ``sigma^2 tau`` here), the random part is
``sigma * (1/W) * integral of B_u du``. For ``a = tau - W``::

    Var[(1/W) int_a^(a+W) B_u du] = a + W/3 = tau - W + W/3 = tau - 2W/3

because ``int int min(u, v) du dv`` over ``[a, a+W]^2`` is ``a W^2 + W^3/3``. So the part
of the horizon spent averaging contributes one third of its point variance, and
``v = sigma^2 (tau - 2W/3)``. The mean is fixed by ``E[A] = S_0`` (``S`` is a martingale),
so ``ln A ~ Normal(ln S_0 - v/2, v)`` and ``P(A >= K) = Phi((ln(S_0/K) - v/2) / sqrt(v))``.
A discrete 60-print average has variance ``tau - 2W/3`` to within 0.2% at ``tau`` = 400
and ``W`` = 60 (a test checks this exactly).

The model needs ``tau >= W``: inside the window part of ``A`` is already observed. A row
there is marked, never priced. Nothing here is fitted; ``sigma`` comes from a column.

Import cost: stdlib + dskit.
"""

import math
from statistics import NormalDist

from dskit.pipeline.node import Node, reject_unknown_params
from dskit.pipeline.records import number_ok, price_ok

from .fields import CAP, FLOOR, PAYOFF, SPOT, TAU_S
from .payoffs import payoff

__all__ = ["AveragedLognormal", "FairValue", "STATUS_OK"]

#: The status a priced row carries; every other status names why it was not priced.
STATUS_OK = "ok"

_NORMAL = NormalDist()


class AveragedLognormal:
    """The lognormal law of a ``window_s``-second average, seen ``tau_s`` seconds before it ends.

    Parameters
    ----------
    sigma : float
        Log-return standard deviation per square root of a second, positive.
    tau_s : float
        Seconds from now to the END of the averaging window, at least ``window_s``.
    window_s : float
        Length of the averaging window in seconds, at least 0.

    Examples
    --------
    ATM with no averaging, ``sigma * sqrt(tau) = 0.2``::

        model = AveragedLognormal(0.01, 400.0, 0.0)
        model.variance            # -> 0.04
        model.survival(100.0, 100.0)   # -> 0.4601721627 (Phi(-0.1))
    """

    def __init__(self, sigma, tau_s, window_s):
        if not (number_ok(sigma) and sigma > 0.0):
            raise ValueError(f"sigma must be a positive finite number, got {sigma!r}")
        if not (number_ok(window_s) and window_s >= 0.0):
            raise ValueError(f"window_s must be a finite number >= 0, got {window_s!r}")
        if not (number_ok(tau_s) and tau_s > 0.0 and tau_s >= window_s):
            raise ValueError(
                f"tau_s must be positive and at least the averaging window ({window_s!r}), got {tau_s!r}")
        self.variance = sigma * sigma * (tau_s - window_s + window_s / 3.0)

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


class FairValue(Node):
    """Add the baseline fair value of YES to each decision row (role ``transform``).

    Inputs: ``records`` with ``spot``, ``tau_s``, ``payoff``, the strikes and the volatility
    column. Output ``records``: every row with three more columns, ``<fair_field>`` (P(YES),
    or None), ``<fair_field>_var`` (the log variance of the settlement value) and
    ``<fair_field>_status`` (``ok`` or why the row was not priced: ``no_spot``, ``no_vol``,
    ``no_tau``, ``tau_inside_window``). A row that cannot be priced is never guessed.

    Parameters
    ----------
    params : dict
        ``vol_field`` (str, REQUIRED) the column holding sigma per sqrt second;
        ``fair_field`` (str, REQUIRED) the output column; ``averaging_window_s`` (number
        >= 0, REQUIRED) the settlement average's length, 60 for the BRTI.

    Examples
    --------
    Price every row from a realised-vol column::

        node = FairValue("fair", {"vol_field": "rv_rms_60", "fair_field": "fair_rms",
                                  "averaging_window_s": 60})
        out = node.run(ctx, {"records": rows})
        # -> out["records"][0]["fair_rms"] is P(YES), or None with fair_rms_status saying why
    """

    role = "transform"
    outputs = ("records",)
    _PARAMS = ("vol_field", "fair_field", "averaging_window_s")

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
        for name in ("vol_field", "fair_field"):
            value = params.get(name)
            if not isinstance(value, str) or not value:
                problems.append(f"{name} is required: a non-empty column name, got {value!r}")
        window = params.get("averaging_window_s")
        if not (number_ok(window) and window >= 0.0):
            problems.append(
                f"averaging_window_s is required: a number of seconds >= 0, got {window!r}")
        return problems

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
        """Refuse a ``records`` port that is not a list of rows.

        Parameters
        ----------
        inputs : dict
            The wired ports.

        Returns
        -------
        list of str
            One problem when ``records`` is not a list.
        """
        if not isinstance(inputs.get("records"), list):
            return [f"records must be a list of rows, got {type(inputs.get('records')).__name__}"]
        return []

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
            ``{"records": [...]}``, new dicts in input order.
        """
        records = [self._priced(dict(row)) for row in inputs["records"]]
        priced = sum(1 for r in records if r[self._status_name()] == STATUS_OK)
        self.log.info("priced %d of %d row(s) into %s", priced, len(records), self.params["fair_field"])
        return {"records": records}

    def _status_name(self):
        """Name the status column."""
        return f"{self.params['fair_field']}_status"

    def _refusal(self, row):
        """Return why ``row`` cannot be priced, or None when it can."""
        window = self.params["averaging_window_s"]
        sigma, tau = row.get(self.params["vol_field"]), row.get(TAU_S)
        if not price_ok(row.get(SPOT)):
            return "no_spot"
        if not (number_ok(sigma) and sigma > 0.0):
            return "no_vol"
        if not number_ok(tau):
            return "no_tau"
        return "tau_inside_window" if tau < window else None

    def _priced(self, row):
        """Write the three fair-value columns onto ``row`` (a copy)."""
        name = self.params["fair_field"]
        reason = self._refusal(row)
        if reason is not None:
            row.update({name: None, f"{name}_var": None, self._status_name(): reason})
            return row
        model = AveragedLognormal(row[self.params["vol_field"]], row[TAU_S],
                                  self.params["averaging_window_s"])
        shape = payoff(row[PAYOFF])
        spot = row[SPOT]
        row[name] = shape.yes_probability(lambda k: model.survival(spot, k), row.get(FLOOR), row.get(CAP))
        row[f"{name}_var"] = model.variance
        row[self._status_name()] = STATUS_OK
        return row
