"""European option pricing on a forward, and a vol-index smile quote model.

Two pieces, both venue-neutral:

* :func:`black76` — Black (1976) on a forward: the one closed form every
  cash-settled index option, future option or FX forward option shares.
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

from dskit.pipeline.records import number_ok, price_ok

__all__ = ["RIGHTS", "VolIndexSmileQuotes", "black76"]

#: The two option rights.
RIGHTS = ("put", "call")

_STANDARD_NORMAL = NormalDist()


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
    d2 = d1 - sd
    cdf = _STANDARD_NORMAL.cdf
    discount = math.exp(-rate * years)
    if right == "call":
        return discount * (forward * cdf(d1) - strike * cdf(d2))
    return discount * (strike * cdf(-d2) - forward * cdf(-d1))


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
