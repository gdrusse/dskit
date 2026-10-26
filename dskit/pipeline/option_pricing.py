"""European option pricing on a forward, and a vol-index smile quote model.

Two pieces, both venue-neutral:

* :func:`black76` — Black (1976) on a forward: the one closed form every
  cash-settled index option, future option or FX forward option shares —
  :func:`black76_delta`, its forward delta, and :func:`black76_implied_vol`,
  its inverse in the volatility (ADR-0265).
* :class:`VolIndexSmileQuotes` — a PROXY bid/ask for one option leg when
  no historical option quote exists, built from a volatility-index close
  (quoted in percent: VIX for SPX, VXN for NDX, RVX for RUT, ...). The
  leg's IV is a smile in standardized moneyness scaled by ``level / 100``,
  clamped to ``[iv_floor, iv_ceiling]``; the mid is Black-76 at that IV and
  a half-spread brackets it. Every knob is explicit and nothing is
  defaulted: a consumer labels its output a proxy, never a quote, and
  recorded chains calibrate the knobs and then replace the proxy.

Calibration evidence (2026-09-23, ONE day, ADR-0182). A recorded SPXW
chain at 30 DTE, VIX 14.21, forward ~7795.8, gave these IVs at ``z_K =
ln(K/F) / (vix/100 * sqrt(T))``::

    z_K    -2.5   -2.0   -1.0    0.0    0.5    1.0    1.5    2.0
    real  0.212  0.190  0.149  0.115  0.107  0.105  0.112  0.125
    fit   0.215  0.188  0.144  0.113  0.110  0.110  0.114  0.120

Least squares on those eight points gives ``atm_ratio`` 0.794,
``put_skew_per_z`` 0.176, ``call_skew_per_z`` -0.064 and
``smile_curvature`` 0.045 (fit minus real within ±0.0051 everywhere). An
earlier flat-call, 10%-per-z-put proxy priced OTM calls ~2x and deep puts
~1/3 of that chain. One day is not a surface: skew steepens and flattens
with the regime, so these are fixed placeholders, and the planned
time-varying upgrade drives the wings from a skew index history. That
day's half-spreads were ~0.15-0.25 pt on 1-10 pt options and ~0.3-0.5 pt
on 25-100 pt options; ``max(0.20, 0.005 * mid)`` matches both ranges.

The ceiling exists because the quadratic term is unbounded in ``z_K``: at
a short expiry a modest percentage move is many standardized units. At a
vol index of 15 and one calendar day (``T = 1/365``), with the knobs above,
a 10% OTM put sits at ``z_K`` ~ -13.4 and prices at ~169% IV, and a 15%
OTM put at ~356% — the latter is what a 2.0 ceiling clamps. And the smile must stay
POSITIVE before the floor touches it — :meth:`VolIndexSmileQuotes.problems`
refuses a knob set whose pre-floor smile dips to zero or below anywhere,
so the floor never quietly masks a bad calibration.

Import cost: stdlib only.
"""

import math
from statistics import NormalDist

from dskit.pipeline.node import JsonArtifact, Node, reject_unknown_params
from dskit.pipeline.records import number_ok, price_ok

__all__ = ["RIGHTS", "OptionScenarioReprice", "VolIndexSmileQuotes", "black76",
           "black76_delta", "black76_implied_vol", "black76_vega"]

#: The two option rights.
RIGHTS = ("put", "call")

_STANDARD_NORMAL = NormalDist()


def _black76_terms(right, forward, strike, vol, years, rate):
    """Refuse a bad input; return ``(d1, d2, discount)`` — the one owner of the checks."""
    if right not in RIGHTS:
        raise ValueError(f"right must be one of {list(RIGHTS)}, got {right!r}")
    for name, value in (("forward", forward), ("strike", strike), ("vol", vol),
                        ("years", years)):
        if not price_ok(value):
            raise ValueError(f"{name} must be a positive finite number, got {value!r}")
    if not number_ok(rate):
        raise ValueError(f"rate must be a finite number, got {rate!r}")
    sd = vol * math.sqrt(years)
    d1 = (math.log(forward / strike) + sd * sd / 2) / sd
    return d1, d1 - sd, math.exp(-rate * years)


def black76(right, forward, strike, vol, years, rate=0.0):
    """Price a European option on a forward (Black 1976).

    Parameters
    ----------
    right : str
        ``"put"`` or ``"call"``.
    forward, strike : float
        Positive levels.
    vol : float
        Positive annualized volatility.
    years : float
        Positive time to expiry in years.
    rate : float
        Continuously compounded discount rate; default 0.

    Returns
    -------
    float
        ``DF * (F N(d1) - K N(d2))`` for a call and
        ``DF * (K N(-d2) - F N(-d1))`` for a put, ``DF = exp(-rate * years)``.

    Raises
    ------
    ValueError
        On an unknown right or a non-positive / non-finite input.

    Examples
    --------
    An at-the-money one-year call at 20% vol::

        black76("call", 100.0, 100.0, 0.2, 1.0)  # ~7.9656
    """
    d1, d2, discount = _black76_terms(right, forward, strike, vol, years, rate)
    cdf = _STANDARD_NORMAL.cdf
    if right == "call":
        return discount * (forward * cdf(d1) - strike * cdf(d2))
    return discount * (strike * cdf(-d2) - forward * cdf(-d1))


def black76_delta(right, forward, strike, vol, years, rate=0.0):
    """Return the forward delta of a European option on a forward (Black 1976).

    The derivative of :func:`black76` with respect to ``forward``: what a
    consumer needs to size a hedge in the underlying, per unit of it.

    Parameters
    ----------
    right : str
        ``"put"`` or ``"call"``.
    forward, strike : float
        Positive levels.
    vol : float
        Positive annualized volatility.
    years : float
        Positive time to expiry in years.
    rate : float
        Continuously compounded discount rate; default 0.

    Returns
    -------
    float
        ``DF * N(d1)`` for a call and ``-DF * N(-d1)`` for a put,
        ``DF = exp(-rate * years)``; so ``call - put = DF``.

    Raises
    ------
    ValueError
        On exactly the inputs :func:`black76` refuses, with its messages.

    Examples
    --------
    An at-the-money one-year put at 20% vol::

        black76_delta("put", 100.0, 100.0, 0.2, 1.0)  # ~-0.4602
    """
    d1, _d2, discount = _black76_terms(right, forward, strike, vol, years, rate)
    cdf = _STANDARD_NORMAL.cdf
    if right == "call":
        return discount * cdf(d1)
    return -discount * cdf(-d1)


def black76_vega(right, forward, strike, vol, years, rate=0.0):
    """Return price sensitivity to one unit of annualized volatility.

    Uses the same validated Black-76 inputs as :func:`black76`. The
    result is per underlying unit; multiply by the contract multiplier
    and signed quantity when sizing a position.
    """
    d1, _d2, discount = _black76_terms(right, forward, strike, vol, years, rate)
    return discount * forward * _STANDARD_NORMAL.pdf(d1) * math.sqrt(years)


#: Newton / bisection steps :func:`black76_implied_vol` takes before it gives up.
_IMPLIED_VOL_STEPS = 100


def black76_implied_vol(right, price, forward, strike, years, rate=0.0, vol_min=1e-4,
                        vol_max=10.0, tolerance=1e-10):
    """Invert :func:`black76` in the volatility (ADR-0265).

    The price is monotone increasing in ``vol``, so the root inside
    ``[vol_min, vol_max]`` is unique when it exists. Newton steps on the
    vega are safeguarded by the shrinking bracket, so a flat wing falls back
    to bisection rather than diverging.

    Parameters
    ----------
    right : str
        ``"put"`` or ``"call"``.
    price : float
        The option price to reproduce; finite and nonnegative.
    forward, strike : float
        Positive levels.
    years : float
        Positive time to expiry in years.
    rate : float
        Continuously compounded discount rate; default 0.
    vol_min, vol_max : float
        The bracket the answer must lie in, ``0 < vol_min < vol_max``.
    tolerance : float
        Stop when the model price is within this of ``price`` (price units).

    Returns
    -------
    float or None
        The volatility, or ``None`` when no volatility in the bracket
        prices the option: at or below its intrinsic value, at or above its
        upper bound, or outside ``[vol_min, vol_max]``.

    Raises
    ------
    ValueError
        On an unknown right, a negative / non-finite price, a non-positive
        or non-finite level, or a malformed bracket.

    Examples
    --------
    Recover a 25% vol from its own price::

        p = black76("call", 100.0, 105.0, 0.25, 0.5)
        black76_implied_vol("call", p, 100.0, 105.0, 0.5)  # ~0.25
    """
    _black76_terms(right, forward, strike, vol_min, years, rate)
    if not number_ok(price) or price < 0:
        raise ValueError(f"price must be a finite number >= 0, got {price!r}")
    if not price_ok(vol_max) or vol_max <= vol_min:
        raise ValueError(f"vol_max must be a finite number > vol_min, got {vol_max!r}")
    if black76(right, forward, strike, vol_min, years, rate) > price:
        return None
    if black76(right, forward, strike, vol_max, years, rate) < price:
        return None
    low, high = vol_min, vol_max
    vol = 0.5 * (low + high)
    root_years = math.sqrt(years)
    discount = math.exp(-rate * years)
    for _ in range(_IMPLIED_VOL_STEPS):
        gap = black76(right, forward, strike, vol, years, rate) - price
        if abs(gap) <= tolerance:
            return vol
        low, high = (low, vol) if gap > 0 else (vol, high)
        d1 = (math.log(forward / strike) + vol * vol * years / 2) / (vol * root_years)
        vega = discount * forward * _STANDARD_NORMAL.pdf(d1) * root_years
        step = vol - gap / vega if vega > 0 else None
        vol = step if step is not None and low < step < high else 0.5 * (low + high)
    return None


def _wing_minimum(atm_ratio, slope, curvature):
    """Return the smallest ``atm + slope u + curvature u**2`` over ``u >= 0``.

    ``-inf`` when the wing falls without bound (a negative slope with no
    curvature to turn it).
    """
    if slope >= 0:
        return atm_ratio
    if curvature <= 0:
        return -math.inf
    return atm_ratio - slope * slope / (4 * curvature)


class VolIndexSmileQuotes:
    """Bid/ask for one option leg from a volatility-index close.

    Leg IV is ``level/100 * (atm_ratio + put_skew_per_z * max(0, -z_K) +
    call_skew_per_z * max(0, z_K) + smile_curvature * z_K**2)`` clamped to
    ``[iv_floor, iv_ceiling]``, where ``level`` is the vol index in percent
    and ``z_K = ln(K/F) / (level/100 * sqrt(T))`` — strikes below the
    forward get the put wing, strikes above follow their own (possibly
    negative) call wing, and the quadratic term lifts both tails. The mid
    is Black-76 at that IV; ``bid = max(0, mid - hs)``, ``ask = mid + hs``
    with ``hs = max(half_spread_min, half_spread_frac * mid)``. The module
    docstring holds the one-day calibration evidence.

    Parameters
    ----------
    atm_ratio : float
        Positive ATM IV as a multiple of ``level/100``.
    put_skew_per_z : float
        Nonnegative uplift, in ``level/100`` units, per standardized unit
        below the forward.
    call_skew_per_z : float
        Finite slope, in ``level/100`` units, per standardized unit above
        the forward; negative dips the call wing (the curvature must then
        turn it before it reaches zero).
    smile_curvature : float
        Nonnegative coefficient on ``z_K**2``, in ``level/100`` units.
    iv_floor : float
        Positive lower bound on any leg's IV.
    iv_ceiling : float
        Upper bound on any leg's IV, above ``iv_floor``.
    half_spread_min : float
        Nonnegative minimum half-spread, price points.
    half_spread_frac : float
        Half-spread as a fraction of mid, in ``[0, 1)``.
    rate : float
        Discount rate (finite).

    Examples
    --------
    One 21-trading-day index put at a vol-index close of 20::

        quotes = VolIndexSmileQuotes(0.794, 0.176, -0.064, 0.045, 0.05, 2.0,
                                     0.20, 0.005, 0.0)
        bid, mid, ask = quotes.quote("put", 5000.0, 4800.0, 20.0, 21 / 252)
    """

    #: Every knob, in constructor order — the one list a config reader uses.
    KNOBS = ("atm_ratio", "put_skew_per_z", "call_skew_per_z", "smile_curvature",
             "iv_floor", "iv_ceiling", "half_spread_min", "half_spread_frac", "rate")

    def __init__(self, atm_ratio, put_skew_per_z, call_skew_per_z, smile_curvature,
                 iv_floor, iv_ceiling, half_spread_min, half_spread_frac, rate):
        problems = self.problems(atm_ratio, put_skew_per_z, call_skew_per_z,
                                 smile_curvature, iv_floor, iv_ceiling,
                                 half_spread_min, half_spread_frac, rate)
        if problems:
            raise ValueError("; ".join(problems))
        self.atm_ratio = float(atm_ratio)
        self.put_skew_per_z = float(put_skew_per_z)
        self.call_skew_per_z = float(call_skew_per_z)
        self.smile_curvature = float(smile_curvature)
        self.iv_floor = float(iv_floor)
        self.iv_ceiling = float(iv_ceiling)
        self.half_spread_min = float(half_spread_min)
        self.half_spread_frac = float(half_spread_frac)
        self.rate = float(rate)

    @staticmethod
    def problems(atm_ratio, put_skew_per_z, call_skew_per_z, smile_curvature,
                 iv_floor, iv_ceiling, half_spread_min, half_spread_frac, rate):
        """List what is wrong with a declaration.

        Parameters
        ----------
        atm_ratio, put_skew_per_z, call_skew_per_z, smile_curvature : object
            The candidate smile knobs.
        iv_floor, iv_ceiling, half_spread_min, half_spread_frac, rate : object
            The candidate clamp, spread and rate knobs.

        Returns
        -------
        list of str
            Empty when usable. A smile that dips to zero or below for some
            ``z_K`` is refused: the floor must never be what keeps an IV
            positive.
        """
        problems = []
        for name, value in (("put_skew_per_z", put_skew_per_z),
                            ("smile_curvature", smile_curvature),
                            ("half_spread_min", half_spread_min)):
            if not number_ok(value) or value < 0:
                problems.append(f"{name} must be a nonnegative number, got {value!r}")
        for name, value in (("atm_ratio", atm_ratio), ("iv_floor", iv_floor),
                            ("iv_ceiling", iv_ceiling)):
            if not price_ok(value):
                problems.append(f"{name} must be a positive number, got {value!r}")
        if not number_ok(call_skew_per_z):
            problems.append(f"call_skew_per_z must be a finite number, got {call_skew_per_z!r}")
        if not number_ok(half_spread_frac) or not 0 <= half_spread_frac < 1:
            problems.append(f"half_spread_frac must be in [0, 1), got {half_spread_frac!r}")
        if not number_ok(rate):
            problems.append(f"rate must be a finite number, got {rate!r}")
        if not problems and not iv_ceiling > iv_floor:
            problems.append(f"iv_ceiling ({iv_ceiling!r}) must exceed iv_floor ({iv_floor!r})")
        if not problems:
            problems.extend(VolIndexSmileQuotes._smile_problems(
                atm_ratio, put_skew_per_z, call_skew_per_z, smile_curvature))
        return problems

    @staticmethod
    def _smile_problems(atm_ratio, put_skew_per_z, call_skew_per_z, smile_curvature):
        """Refuse a pre-floor smile that reaches zero or below on either wing."""
        problems = []
        for wing, slope in (("put", put_skew_per_z), ("call", call_skew_per_z)):
            lowest = _wing_minimum(atm_ratio, slope, smile_curvature)
            if not lowest > 0:
                problems.append(
                    f"the {wing} wing of the smile reaches {lowest!r} (x level/100) "
                    "before the floor: atm_ratio + slope*z + smile_curvature*z^2 must "
                    "stay positive for every z, so the floor never masks a bad "
                    "calibration")
        return problems

    def iv(self, forward, strike, level, years):
        """Return the proxy implied vol of one strike.

        Parameters
        ----------
        forward, strike : float
            Positive levels.
        level : float
            Positive vol-index close (percent).
        years : float
            Positive time to expiry.

        Returns
        -------
        float
            The smiled IV, clamped to ``[iv_floor, iv_ceiling]``.

        Raises
        ------
        ValueError
            When ``level`` is not a positive finite number.
        """
        if not price_ok(level):
            raise ValueError(f"level must be a positive finite number, got {level!r}")
        vol = level / 100.0
        z_k = math.log(strike / forward) / (vol * math.sqrt(years))
        smile = (self.atm_ratio + self.put_skew_per_z * max(0.0, -z_k)
                 + self.call_skew_per_z * max(0.0, z_k) + self.smile_curvature * z_k * z_k)
        return min(self.iv_ceiling, max(self.iv_floor, vol * smile))

    def quote(self, right, forward, strike, level, years):
        """Return ``(bid, mid, ask)`` for one leg, price points per unit.

        Parameters
        ----------
        right : str
            ``"put"`` or ``"call"``.
        forward, strike, level, years : float
            As for :meth:`iv`.

        Returns
        -------
        tuple of float
            Bid (never negative), mid and ask.
        """
        mid = black76(right, forward, strike, self.iv(forward, strike, level, years),
                      years, self.rate)
        half = max(self.half_spread_min, self.half_spread_frac * mid)
        return max(0.0, mid - half), mid, mid + half


class OptionScenarioReprice(Node):
    """Fully reprice a declared European option book under JSON market shocks.

    One forward and rate apply to every leg. Each leg declares its own strike,
    IV, days to expiry, signed contract quantity and multiplier. A scenario
    applies a relative forward move and parallel absolute IV/rate shifts;
    ``days_elapsed`` reduces each leg's remaining life. An expired leg uses
    intrinsic value. P&L is the change from the *model* baseline, excluding
    entry fills, fees, liquidity and margin. This is a hypothetical diagnostic,
    never an executable or decision-eligible trade result.

    A scenario's omitted shock is zero. ``iv_shift`` is in volatility units
    (0.10 means ten percentage points), and ``rate_shift`` is in continuous
    rate units. The report keeps every leg's contribution and the worst
    declared scenario; that worst loss has no probability or coverage claim.

    Parameters
    ----------
    key : str
        Pipeline node key.
    params : dict
        ``forward`` and ``rate`` of one European-option underlying;
        ``year_basis_days`` converts declared calendar days to Black-76 years;
        ``legs`` is a non-empty list of named signed option contracts;
        ``scenarios`` is a non-empty list of named market shocks.

    Examples
    --------
    Value a single long call under a 5% forward rise::

        node = OptionScenarioReprice("stress", {
            "forward": 100, "rate": 0, "year_basis_days": 365,
            "legs": [{"name": "call", "right": "call", "strike": 100, "iv": 0.2,
                      "days_to_expiry": 30, "quantity": 1, "multiplier": 100}],
            "scenarios": [{"name": "up", "forward_return": 0.05}]})
        report = node.run(None, {})["report"]
    """

    role = "transform"
    outputs = ("metrics", "report")
    _PARAMS = ("forward", "rate", "year_basis_days", "legs", "scenarios")
    _LEG_KEYS = ("name", "right", "strike", "iv", "days_to_expiry",
                 "quantity", "multiplier", "notes")
    _SCENARIO_KEYS = ("name", "forward_return", "iv_shift", "days_elapsed",
                      "rate_shift", "notes")
    _SHOCK_DEFAULTS = {"forward_return": 0.0, "iv_shift": 0.0,
                       "days_elapsed": 0, "rate_shift": 0.0}

    @classmethod
    def _leg_problems(cls, legs):
        """Validate the named book, accumulating every structural defect."""
        if not isinstance(legs, list) or not legs:
            return ["legs must be a non-empty list"]
        problems, names = [], set()
        for index, leg in enumerate(legs):
            where = f"legs[{index}]"
            if not isinstance(leg, dict):
                problems.append(f"{where} must be an object")
                continue
            unknown = sorted(str(key) for key in set(leg) - set(cls._LEG_KEYS))
            if unknown:
                problems.append(f"{where} has unknown keys {unknown}")
            name = leg.get("name")
            if not isinstance(name, str) or not name:
                problems.append(f"{where}.name must be a non-empty string")
            elif name in names:
                problems.append(f"{where}.name is duplicate: {name!r}")
            else:
                names.add(name)
            if "notes" in leg and not isinstance(leg["notes"], str):
                problems.append(f"{where}.notes must be a string")
            if leg.get("right") not in RIGHTS:
                problems.append(f"{where}.right must be one of {RIGHTS}")
            for field in ("strike", "iv", "multiplier"):
                if not price_ok(leg.get(field)):
                    problems.append(f"{where}.{field} must be positive and finite")
            days = leg.get("days_to_expiry")
            if type(days) is not int or days <= 0:
                problems.append(f"{where}.days_to_expiry must be a positive integer")
            quantity = leg.get("quantity")
            if type(quantity) is not int or quantity == 0:
                problems.append(f"{where}.quantity must be a nonzero integer")
        return problems

    @classmethod
    def _scenario_problems(cls, scenarios):
        """Validate scenario names and each supported shock."""
        if not isinstance(scenarios, list) or not scenarios:
            return ["scenarios must be a non-empty list"]
        problems, names = [], set()
        for index, scenario in enumerate(scenarios):
            where = f"scenarios[{index}]"
            if not isinstance(scenario, dict):
                problems.append(f"{where} must be an object")
                continue
            unknown = sorted(str(key) for key in set(scenario) - set(cls._SCENARIO_KEYS))
            if unknown:
                problems.append(f"{where} has unknown keys {unknown}")
            name = scenario.get("name")
            if not isinstance(name, str) or not name:
                problems.append(f"{where}.name must be a non-empty string")
            elif name in names:
                problems.append(f"{where}.name is duplicate: {name!r}")
            else:
                names.add(name)
            if "notes" in scenario and not isinstance(scenario["notes"], str):
                problems.append(f"{where}.notes must be a string")
            move = scenario.get("forward_return", cls._SHOCK_DEFAULTS["forward_return"])
            if not number_ok(move) or move <= -1.0:
                problems.append(f"{where}.forward_return must be finite and > -1")
            for field in ("iv_shift", "rate_shift"):
                if not number_ok(scenario.get(field, cls._SHOCK_DEFAULTS[field])):
                    problems.append(f"{where}.{field} must be finite")
            days = scenario.get("days_elapsed", cls._SHOCK_DEFAULTS["days_elapsed"])
            if type(days) is not int or days < 0:
                problems.append(f"{where}.days_elapsed must be an integer >= 0")
        return problems

    @classmethod
    def validate_params(cls, params):
        """Refuse malformed books and shocks before a run can start.

        Parameters
        ----------
        params : dict
            Candidate JSON parameters.

        Returns
        -------
        list of str
            Every known problem; empty when the declaration can be priced.
        """
        problems = []
        reject_unknown_params(problems, params, cls._PARAMS)
        if not price_ok(params.get("forward")):
            problems.append("forward must be positive and finite")
        if not number_ok(params.get("rate")):
            problems.append("rate must be finite")
        basis = params.get("year_basis_days")
        if type(basis) is not int or basis <= 0:
            problems.append("year_basis_days must be a positive integer")
        problems.extend(cls._leg_problems(params.get("legs")))
        problems.extend(cls._scenario_problems(params.get("scenarios")))
        if problems:
            return problems
        baseline_marks = [None] * len(params["legs"])
        for index, scenario in enumerate((None, *params["scenarios"])):
            before, values, deltas = len(problems), [], []
            for leg_index, leg in enumerate(params["legs"]):
                try:
                    mark = cls._mark(params, leg, scenario)
                    if not number_ok(mark):
                        raise ValueError("non-finite model price")
                    value = mark * leg["quantity"] * leg["multiplier"]
                    if not number_ok(value):
                        raise ValueError("non-finite signed contract value")
                    values.append(value)
                    if index == 0:
                        baseline_marks[leg_index] = mark
                    elif baseline_marks[leg_index] is not None:
                        delta = (mark - baseline_marks[leg_index]) * leg["quantity"] * leg["multiplier"]
                        if not number_ok(delta):
                            raise ValueError("non-finite leg P&L")
                        deltas.append(delta)
                except (OverflowError, ValueError, ZeroDivisionError) as exc:
                    label = "baseline" if index == 0 else f"scenarios[{index - 1}]"
                    problems.append(f"{label} cannot price leg {leg['name']!r}: {exc}")
            if len(problems) == before:
                for field, amounts in (("value", values), ("P&L", deltas)):
                    if field == "P&L" and index == 0:
                        continue
                    try:
                        total = math.fsum(amounts)
                        if not number_ok(total):
                            raise ValueError("non-finite sum")
                    except (OverflowError, ValueError) as exc:
                        label = "baseline" if index == 0 else f"scenarios[{index - 1}]"
                        problems.append(f"{label} portfolio {field} cannot be summed: {exc}")
        return problems

    def validate_inputs(self, inputs):
        """Require the scenario book to come solely from declared JSON.

        Parameters
        ----------
        inputs : dict
            Candidate pipeline input wires.

        Returns
        -------
        list of str
            Empty for no wires, otherwise a refusal.
        """
        return [] if not inputs else ["inputs must be empty; the book is declared in params"]

    @classmethod
    def _mark(cls, params, leg, scenario):
        """Price one leg at the baseline or one fully shocked market state."""
        shock = scenario or {}
        forward = params["forward"] * (1.0 + shock.get(
            "forward_return", cls._SHOCK_DEFAULTS["forward_return"]))
        rate = params["rate"] + shock.get("rate_shift", cls._SHOCK_DEFAULTS["rate_shift"])
        vol = leg["iv"] + shock.get("iv_shift", cls._SHOCK_DEFAULTS["iv_shift"])
        if not price_ok(forward):
            raise ValueError("shocked forward must remain positive and finite")
        if not number_ok(rate):
            raise ValueError("shocked rate must remain finite")
        if not price_ok(vol):
            raise ValueError("iv_shift leaves a nonpositive or non-finite IV")
        remaining = leg["days_to_expiry"] - shock.get(
            "days_elapsed", cls._SHOCK_DEFAULTS["days_elapsed"])
        if remaining <= 0:
            intrinsic = (forward - leg["strike"] if leg["right"] == "call"
                         else leg["strike"] - forward)
            return max(0.0, intrinsic)
        return black76(leg["right"], forward, leg["strike"], vol,
                       remaining / params["year_basis_days"], rate)

    def run(self, ctx, inputs):
        """Return per-scenario, per-leg hypothetical USD P&L and worst loss.

        Parameters
        ----------
        ctx : NodeContext or None
            Unused; the analysis is fully declared in the JSON document.
        inputs : dict
            Must be empty.

        Returns
        -------
        dict
            Numeric ``metrics`` and a persistent JSON ``report``.

        Raises
        ------
        ValueError
            If an input wire would be ignored.
        """
        problems = self.validate_inputs(inputs)
        if problems:
            raise ValueError("; ".join(problems))
        params = self.params
        baseline_marks = [self._mark(params, leg, None) for leg in params["legs"]]
        base_value = math.fsum(
            leg["quantity"] * leg["multiplier"] * mark
            for leg, mark in zip(params["legs"], baseline_marks))
        rows = []
        for scenario in params["scenarios"]:
            leg_rows = []
            for leg, base_mark in zip(params["legs"], baseline_marks):
                mark = self._mark(params, leg, scenario)
                pnl = leg["quantity"] * leg["multiplier"] * (mark - base_mark)
                leg_rows.append({"name": leg["name"], "base_mark": base_mark,
                                 "scenario_mark": mark, "pnl_usd": pnl})
            pnl = math.fsum(row["pnl_usd"] for row in leg_rows)
            rows.append({"name": scenario["name"], "shocks": {
                key: scenario.get(key, default) for key, default in self._SHOCK_DEFAULTS.items()},
                "value_usd": base_value + pnl, "pnl_usd": pnl, "legs": leg_rows})
        worst = min(rows, key=lambda row: row["pnl_usd"])
        metrics = {"n_scenarios": len(rows), "base_value_usd": base_value,
                   "worst_pnl_usd": worst["pnl_usd"],
                   "worst_loss_usd": max(0.0, -worst["pnl_usd"])}
        report = {"kind": "option_scenario_reprice", "decision_eligible": False,
                  "pricing": "black76_forward", "units": "USD",
                  "basis": "model mid; excludes fills, fees, liquidity and margin",
                  "base_value_usd": base_value, "worst_scenario": worst["name"],
                  "scenarios": rows, "metrics": metrics}
        return {"metrics": metrics, "report": JsonArtifact(report)}
