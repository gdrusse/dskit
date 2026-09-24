"""Black-76 on a forward and the VIX-proxy option quote model (ADR-0182).

No historical option quotes are free, so the backtest prices each leg from
the VIX close: an at-the-money implied vol of ``VIX / 100``, a linear put
skew in standardized moneyness, a floor, and a half-spread around the
Black-76 mid. Every knob is explicit and every output is labelled
``pricing: "vix_proxy"`` by its consumer — a proxy, never a quote. Recorded
Cboe chains later calibrate the knobs and then replace the proxy.
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

    Leg IV is ``vix/100 * (1 + skew_per_z * max(0, -z_K))`` floored at
    ``iv_floor``, where ``z_K = ln(K/F) / (vix/100 * sqrt(T))`` — puts
    below the forward get richer, calls stay at the ATM vol. The mid is
    Black-76 at that IV; ``bid = max(0, mid - hs)``, ``ask = mid + hs``
    with ``hs = max(half_spread_min, half_spread_frac * mid)``.

    Parameters
    ----------
    skew_per_z : float
        Nonnegative IV uplift per standardized unit below the forward.
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

        quotes = VixProxyQuotes(0.1, 0.05, 0.05, 0.03, 0.0)
        bid, mid, ask = quotes.quote("put", 5000.0, 4800.0, 20.0, 21 / 252)
    """

    KNOBS = ("skew_per_z", "iv_floor", "half_spread_min", "half_spread_frac", "rate")

    def __init__(self, skew_per_z, iv_floor, half_spread_min, half_spread_frac, rate):
        problems = self.problems(skew_per_z, iv_floor, half_spread_min,
                                 half_spread_frac, rate)
        if problems:
            raise ValueError("; ".join(problems))
        self.skew_per_z = float(skew_per_z)
        self.iv_floor = float(iv_floor)
        self.half_spread_min = float(half_spread_min)
        self.half_spread_frac = float(half_spread_frac)
        self.rate = float(rate)

    @staticmethod
    def problems(skew_per_z, iv_floor, half_spread_min, half_spread_frac, rate):
        """List what is wrong with a declaration.

        Parameters
        ----------
        skew_per_z, iv_floor, half_spread_min, half_spread_frac, rate : object
            The candidate knobs.

        Returns
        -------
        list of str
            Empty when usable.
        """
        problems = []
        for name, value in (("skew_per_z", skew_per_z), ("half_spread_min", half_spread_min)):
            if not number_ok(value) or value < 0:
                problems.append(f"{name} must be a nonnegative number, got {value!r}")
        if not price_ok(iv_floor):
            problems.append(f"iv_floor must be a positive number, got {iv_floor!r}")
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
            The skewed, floored annualized IV.
        """
        if not price_ok(vix):
            raise ValueError(f"vix must be a positive finite number, got {vix!r}")
        atm = vix / 100.0
        z_k = math.log(strike / forward) / (atm * math.sqrt(years))
        return max(self.iv_floor, atm * (1.0 + self.skew_per_z * max(0.0, -z_k)))

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
