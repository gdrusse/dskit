"""Black-76 on a forward and the VIX-proxy option quote model (ADR-0182).

No historical option quotes are free, so the backtest prices each leg from
the VIX close: a smile in standardized moneyness scaled by ``VIX / 100``
(an ATM ratio, separate linear put and call wings, a quadratic term), a
floor, and a half-spread around the Black-76 mid. Every knob is explicit
and every output is labelled ``pricing: "vix_proxy"`` by its consumer — a
proxy, never a quote. Recorded Cboe chains later calibrate the knobs and
then replace the proxy.

Calibration (2026-09-23, ONE day). A recorded SPXW chain at 30 DTE, VIX
14.21, forward ~7795.8, gave these IVs at ``z_K = ln(K/F) / (vix/100 *
sqrt(T))``::

    z_K    -2.5   -2.0   -1.0    0.0    0.5    1.0    1.5    2.0
    real  0.212  0.190  0.149  0.115  0.107  0.105  0.112  0.125
    fit   0.215  0.188  0.144  0.113  0.110  0.110  0.114  0.120

Least squares on those eight points gives ``atm_ratio`` 0.794,
``put_skew_per_z`` 0.176, ``call_skew_per_z`` -0.064 and
``smile_curvature`` 0.045 (fit minus real within ±0.0051 everywhere). The
earlier flat-call, 10%-per-z-put proxy priced OTM calls ~2x and deep puts
~1/3 of that chain. One day is not a surface: skew steepens and flattens
with the regime, so these are fixed placeholders. The planned time-varying
upgrade drives the wings from Cboe SKEW history (since 1990). That day's
half-spreads were ~0.15-0.25 pt on 1-10 pt options and ~0.3-0.5 pt on
25-100 pt options; ``max(0.20, 0.005 * mid)`` matches both ranges.
"""

import math
from statistics import NormalDist

from dskit.pipeline.records import number_ok, price_ok

__all__ = ["RIGHTS", "VixProxyQuotes", "black76"]

#: The two option rights, spelled as ``contracts.leg_intrinsic`` spells them.
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


class VixProxyQuotes:
    """Bid/ask for one index option leg from the VIX close.

    Leg IV is ``vix/100 * (atm_ratio + put_skew_per_z * max(0, -z_K) +
    call_skew_per_z * max(0, z_K) + smile_curvature * z_K**2)`` floored at
    ``iv_floor``, where ``z_K = ln(K/F) / (vix/100 * sqrt(T))`` — puts
    below the forward get richer, calls follow their own (possibly
    negative) wing and the quadratic term lifts both tails. The mid is
    Black-76 at that IV; ``bid = max(0, mid - hs)``, ``ask = mid + hs``
    with ``hs = max(half_spread_min, half_spread_frac * mid)``.

    Parameters
    ----------
    atm_ratio : float
        Positive ATM IV as a multiple of ``vix/100``.
    put_skew_per_z : float
        Nonnegative uplift, in ``vix/100`` units, per standardized unit
        below the forward.
    call_skew_per_z : float
        Finite slope, in ``vix/100`` units, per standardized unit above the
        forward; negative dips the call wing.
    smile_curvature : float
        Nonnegative coefficient on ``z_K**2``, in ``vix/100`` units.
    iv_floor : float
        Positive lower bound on any leg's IV.
    half_spread_min : float
        Nonnegative minimum half-spread, index points.
    half_spread_frac : float
        Half-spread as a fraction of mid, in ``[0, 1)``.
    rate : float
        Discount rate (finite).

    Examples
    --------
    One 21-trading-day SPX put at VIX 20::

        quotes = VixProxyQuotes(0.794, 0.176, -0.064, 0.045, 0.05, 0.20, 0.005, 0.0)
        bid, mid, ask = quotes.quote("put", 5000.0, 4800.0, 20.0, 21 / 252)
    """

    KNOBS = ("atm_ratio", "put_skew_per_z", "call_skew_per_z", "smile_curvature",
             "iv_floor", "half_spread_min", "half_spread_frac", "rate")

    def __init__(self, atm_ratio, put_skew_per_z, call_skew_per_z, smile_curvature,
                 iv_floor, half_spread_min, half_spread_frac, rate):
        problems = self.problems(atm_ratio, put_skew_per_z, call_skew_per_z,
                                 smile_curvature, iv_floor, half_spread_min,
                                 half_spread_frac, rate)
        if problems:
            raise ValueError("; ".join(problems))
        self.atm_ratio = float(atm_ratio)
        self.put_skew_per_z = float(put_skew_per_z)
        self.call_skew_per_z = float(call_skew_per_z)
        self.smile_curvature = float(smile_curvature)
        self.iv_floor = float(iv_floor)
        self.half_spread_min = float(half_spread_min)
        self.half_spread_frac = float(half_spread_frac)
        self.rate = float(rate)

    @staticmethod
    def problems(atm_ratio, put_skew_per_z, call_skew_per_z, smile_curvature,
                 iv_floor, half_spread_min, half_spread_frac, rate):
        """List what is wrong with a declaration.

        Parameters
        ----------
        atm_ratio, put_skew_per_z, call_skew_per_z, smile_curvature : object
            The candidate smile knobs.
        iv_floor, half_spread_min, half_spread_frac, rate : object
            The candidate floor, spread and rate knobs.

        Returns
        -------
        list of str
            Empty when usable.
        """
        problems = []
        for name, value in (("put_skew_per_z", put_skew_per_z),
                            ("smile_curvature", smile_curvature),
                            ("half_spread_min", half_spread_min)):
            if not number_ok(value) or value < 0:
                problems.append(f"{name} must be a nonnegative number, got {value!r}")
        for name, value in (("atm_ratio", atm_ratio), ("iv_floor", iv_floor)):
            if not price_ok(value):
                problems.append(f"{name} must be a positive number, got {value!r}")
        if not number_ok(call_skew_per_z):
            problems.append(f"call_skew_per_z must be a finite number, got {call_skew_per_z!r}")
        if not number_ok(half_spread_frac) or not 0 <= half_spread_frac < 1:
            problems.append(f"half_spread_frac must be in [0, 1), got {half_spread_frac!r}")
        if not number_ok(rate):
            problems.append(f"rate must be a finite number, got {rate!r}")
        return problems

    def iv(self, forward, strike, vix, years):
        """Return the proxy implied vol of one strike.

        Parameters
        ----------
        forward, strike : float
            Positive levels.
        vix : float
            Positive VIX close (percent).
        years : float
            Positive time to expiry.

        Returns
        -------
        float
            The smiled, floored annualized IV.
        """
        if not price_ok(vix):
            raise ValueError(f"vix must be a positive finite number, got {vix!r}")
        vol = vix / 100.0
        z_k = math.log(strike / forward) / (vol * math.sqrt(years))
        smile = (self.atm_ratio + self.put_skew_per_z * max(0.0, -z_k)
                 + self.call_skew_per_z * max(0.0, z_k) + self.smile_curvature * z_k * z_k)
        return max(self.iv_floor, vol * smile)

    def quote(self, right, forward, strike, vix, years):
        """Return ``(bid, mid, ask)`` for one leg, index points per unit.

        Parameters
        ----------
        right : str
            ``"put"`` or ``"call"``.
        forward, strike, vix, years : float
            As for :meth:`iv`.

        Returns
        -------
        tuple of float
            Bid (never negative), mid and ask.
        """
        mid = black76(right, forward, strike, self.iv(forward, strike, vix, years),
                      years, self.rate)
        half = max(self.half_spread_min, self.half_spread_frac * mid)
        return max(0.0, mid - half), mid, mid + half
