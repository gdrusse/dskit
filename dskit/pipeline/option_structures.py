"""Leg-set algebra for option structures: credit, payoff, maximum loss, quote defects.

The venue-neutral half of what the ``index_options`` child's ``contracts`` module
owned, graduated into the core by ADR-0265 so a generic ledger can price a
structure without importing a child. A *leg set* is a sequence of
``(right, signed quantity)`` pairs, ``right`` being ``"put"`` or ``"call"`` and the
sign ``+1`` for a bought leg and ``-1`` for a sold one; a structure is a leg set
with one strike per leg. This module names no structure (no condor, no straddle):
the caller declares its leg sets. The child keeps its named vocabulary and
re-exports these functions.

* :func:`structure_credit` - shorts at the bid, longs at the ask, plus the vertical widths.
* :func:`structure_payoff` / :func:`leg_intrinsic` / :func:`payoff_total` - the settlement
  payoff of the leg set, exact for Decimal, bit-identical fast path for float.
* :func:`structure_max_loss` - the most a leg set can lose at settlement.
* :func:`quote_defects` / :func:`quote_defect` - why a bid/ask pair cannot fill on a side, as a stable code
  (:data:`QUOTE_DEFECTS`) a caller can count by reason.

Import cost: stdlib only.
"""

import math
from decimal import Decimal
from itertools import pairwise

from dskit.pipeline.node import check_int_param
from dskit.pipeline.records import number_ok

__all__ = [
    "QUOTE_DEFECTS",
    "is_amount",
    "leg_intrinsic",
    "payoff_total",
    "quote_defect",
    "quote_defects",
    "structure_credit",
    "structure_max_loss",
    "structure_payoff",
]

#: The codes :func:`quote_defect` returns, in the order it checks them.
QUOTE_DEFECTS = ("not_finite", "negative", "crossed", "locked",
                 "sold_leg_no_bid", "bought_leg_no_ask")

_SIDES = (None, "sell", "buy")


def _check_multiplier(multiplier):
    """Require a whole-number multiplier >= 1, with the shared bound and a stricter type."""
    problems = []
    check_int_param(problems, "multiplier", multiplier, ge=1)
    if not isinstance(multiplier, int):
        problems.append("multiplier must use an integer, not a float or string")
    if problems:
        raise ValueError("; ".join(problems))


def leg_intrinsic(right, strike, level):
    """Return one European cash-settled leg's payoff per unit at ``level``.

    The one owner of the put/call intrinsic rule: exact Decimal cashflows
    and float scenario payoffs both call it.

    Parameters
    ----------
    right : str
        ``"put"`` or ``"call"``.
    strike, level : Decimal or float
        Strike and settlement level, of one numeric type.

    Returns
    -------
    Decimal or float
        ``max(strike - level, 0)`` for a put, ``max(level - strike, 0)``
        for a call, in the inputs' type.
    """
    gap = strike - level if right == "put" else level - strike
    return max(type(gap)(0), gap)


def structure_payoff(legs, level, strikes):
    """Return a leg set's settlement payoff per unit, credit excluded.

    The one owner of the leg-sum (ADR-0193, generalized from the condor):
    each leg contributes ``sign x`` its intrinsic value at ``level``. It lives
    here, beside :func:`leg_intrinsic` and :func:`structure_credit`, so that
    :func:`structure_max_loss` can call it (ADR-0197);
    ``index_options.distribution`` re-exports it.

    Parameters
    ----------
    legs : sequence of (str, int)
        ``(right, signed quantity)`` per leg, e.g. :data:`CONDOR_LEGS`.
    level : float
        Settlement level.
    strikes : sequence of float
        One strike per leg, in leg order.

    Returns
    -------
    float
        The signed sum: ``0`` when every leg expires worthless, negative
        when the short legs finish in the money.

    Raises
    ------
    ValueError
        When ``strikes`` do not match ``legs`` in number.

    Examples
    --------
    A put credit spread settling between its strikes::

        structure_payoff(PUT_SPREAD_LEGS, 93.0, (90.0, 95.0))
        # -> -2.0
    """
    terms = _float_terms(legs, level, strikes)
    if terms is not None:
        return sum(terms)
    return sum(sign * leg_intrinsic(right, k, level)
               for (right, sign), k in zip(legs, strikes, strict=True))


def _float_terms(legs, level, strikes):
    """Return :func:`structure_payoff`'s per-leg terms for an all-float input (the hot case), else ``None``."""
    # Same rule as leg_intrinsic (the owner), inlined for speed: ``max(0.0, gap)`` is 0.0 unless gap > 0, so every term and the
    # sum are bit-identical; anything else (Decimal, int, a length mismatch) takes the owner's path.
    if (type(level) is not float or not isinstance(legs, (tuple, list))
            or not isinstance(strikes, (tuple, list)) or len(legs) != len(strikes)):
        return None
    terms = []
    for (right, sign), k in zip(legs, strikes):
        if type(k) is not float:
            return None
        gap = k - level if right == "put" else level - k
        terms.append(sign * (gap if gap > 0.0 else 0.0))
    return terms


def payoff_total(legs, levels, strikes):
    """Return ``sum(structure_payoff(legs, level, strikes) for level in levels)``, in that order."""
    if (type(levels) is list and levels and all(type(level) is float for level in levels)
            and _float_terms(legs, levels[0], strikes) is not None):
        return sum([sum(_float_terms(legs, level, strikes)) for level in levels])
    return sum(structure_payoff(legs, level, strikes) for level in levels)


def structure_max_loss(legs, strikes, credit_usd, multiplier):
    """Return the most a leg set can lose at settlement, in USD, credit included.

    The one owner of the maximum-loss rule (ADR-0197). The settlement P&L is
    ``credit_usd + multiplier x`` :func:`structure_payoff`, piecewise linear in
    the settlement level with its kinks at ``0`` and at every strike, so its
    minimum over levels ``>= 0`` lies on one of those points unless it keeps
    falling above the top strike, which it does exactly when the calls' signed
    quantities sum negative (a naked short call, a short strangle, a ratio):
    then the loss is unbounded. For the credit structures it equals
    ``multiplier x`` the widest vertical less the credit, and for a long
    structure it is the debit.

    Parameters
    ----------
    legs : sequence of (str, int)
        ``(right, signed quantity)`` per leg, at least one.
    strikes : sequence of Decimal or float
        One positive strike per leg, in leg order.
    credit_usd : Decimal or float
        The cash received at entry in USD, net of fees; negative for a debit.
    multiplier : int
        Shares per contract, >= 1.

    Returns
    -------
    Decimal, float or float("inf")
        ``-min`` of the settlement P&L, in the strikes' type; negative when
        the structure cannot lose (a free lunch), ``math.inf`` when the
        loss is unbounded.

    Raises
    ------
    ValueError
        When there are no legs, ``strikes`` do not match them, a strike is not
        a positive number, ``credit_usd`` is not finite or ``multiplier`` is not
        an int >= 1.

    Examples
    --------
    An iron condor with a 5-point put wing and a 10-point call wing, credit 150::

        structure_max_loss(CONDOR_LEGS, (90.0, 95.0, 105.0, 115.0), 150.0, 100)
        # -> 850.0
    """
    if not legs:
        raise ValueError("a structure needs at least one leg")
    if len(strikes) != len(legs):
        raise ValueError(f"strikes must give one per leg: {len(legs)} legs, got {strikes!r}")
    _check_multiplier(multiplier)
    if not is_amount(credit_usd):
        raise ValueError(f"credit_usd must be a finite number, got {credit_usd!r}")
    if not all(is_amount(k) and k > 0 for k in strikes):
        raise ValueError(f"strikes must be positive finite numbers, got {strikes!r}")
    if sum(sign for right, sign in legs if right == "call") < 0:
        return math.inf
    return -min(credit_usd + multiplier * structure_payoff(legs, level, strikes)
                for level in (0, *strikes))


def is_amount(value):
    """Say whether ``value`` is a finite Decimal or a finite non-bool number."""
    if type(value) is float:        # the hot case: number_ok's answer without its isinstance chain
        return math.isfinite(value)
    if isinstance(value, Decimal):
        return value.is_finite()
    return number_ok(value)


def structure_credit(legs, strikes, quotes):
    """Return a leg set's per-share entry credit and its vertical widths.

    Short legs sell at the bid, long legs buy at the ask — the one owner of
    that rule (ADR-0187, generalized from the condor to any leg tuple by
    ADR-0193). It never raises on the answer: the cashflow owner refuses a
    credit outside ``(0, narrower width)`` and a backtest classifies it.

    A *vertical pair* is two consecutive legs of one right with opposite
    signs (one long, one short, in either order); its width is the later
    leg's strike less the earlier leg's, in leg order, so it is positive
    when the strikes ascend as the tuple lists them. Legs of two rights, or
    of one sign, pair with nothing.

    Parameters
    ----------
    legs : sequence of (str, int)
        ``(right, signed quantity)`` per leg, e.g. :data:`CONDOR_LEGS`.
    strikes : sequence of Decimal or float
        One strike per leg, in leg order.
    quotes : sequence of (bid, ask)
        One pair per leg, in leg order and the strikes' numeric family.

    Returns
    -------
    tuple
        ``(credit, widths)``: the credit, and a tuple with one width per
        vertical pair in leg order (empty when there is none).

    Raises
    ------
    ValueError
        When ``strikes`` or ``quotes`` do not match ``legs`` in number.

    Examples
    --------
    A put credit spread, short the 100 at its bid, long the 95 at its ask::

        credit, widths = structure_credit(PUT_SPREAD_LEGS, (95.0, 100.0),
                                          ((1.0, 1.1), (2.0, 2.2)))
        # -> credit 0.9 (2.0 - 1.1), widths (5.0,)
    """
    placed = list(zip(legs, strikes, quotes, strict=True))
    credit = sum(-sign * (ask if sign > 0 else bid)
                 for (_right, sign), _strike, (bid, ask) in placed)
    widths = tuple(later - earlier
                   for ((right, sign), earlier, _quote), ((next_right, next_sign), later, _next)
                   in pairwise(placed) if right == next_right and sign * next_sign < 0)
    return credit, widths


def quote_defects(bid, ask, side=None, locked_ok=False):
    """Return every reason a bid/ask pair cannot fill on ``side``, as stable codes.

    The code form of the quote rules, so a ledger can count refusals by reason.
    A non-finite (or ``None``, or boolean) side is ``not_finite`` and ends the
    check. Otherwise: a negative side, or else an ask below the bid
    (``crossed``), or else an ask equal to the bid (``locked``, unless
    ``locked_ok``); and, independently, the side traded: a sale needs a
    positive bid and a purchase a positive ask. A provider ``0`` on the side
    traded means no market, never a free fill; a far wing with no bid stays
    buyable.

    Parameters
    ----------
    bid, ask : float or Decimal
        The quote; ``None`` and NaN are ``not_finite``.
    side : None, "sell" or "buy"
        Which side is traded; ``None`` judges the structure of the quote only.
    locked_ok : bool
        Accept ``ask == bid`` (a mark can use a locked quote; an open does not).

    Returns
    -------
    list of str
        Members of :data:`QUOTE_DEFECTS` in check order; empty when the quote fills.

    Raises
    ------
    ValueError
        When ``side`` is not one of ``None``, ``"sell"``, ``"buy"``.

    Examples
    --------
    A sale into a quote with no bid::

        quote_defects(0.0, 0.4, "sell")  # -> ["sold_leg_no_bid"]
    """
    if side not in _SIDES:
        raise ValueError(f"side must be one of {_SIDES}, got {side!r}")
    if not (is_amount(bid) and is_amount(ask)):
        return ["not_finite"]
    found = []
    if bid < 0 or ask < 0:
        found.append("negative")
    elif ask < bid:
        found.append("crossed")
    elif ask == bid and not locked_ok and (side is None or bid > 0):
        # A zero-zero quote traded on a side is "no market" below, not "locked".
        found.append("locked")
    if side == "sell" and not bid > 0:
        found.append("sold_leg_no_bid")
    if side == "buy" and not ask > 0:
        found.append("bought_leg_no_ask")
    return found


def quote_defect(bid, ask, side=None, locked_ok=False):
    """Return the first of :func:`quote_defects`, or ``None`` when the quote fills.

    Parameters
    ----------
    bid, ask : float or Decimal
        The quote; ``None`` and NaN are ``not_finite``.
    side : None, "sell" or "buy"
        Which side is traded.
    locked_ok : bool
        Accept ``ask == bid``.

    Returns
    -------
    str or None
        A member of :data:`QUOTE_DEFECTS`, or ``None``.

    Raises
    ------
    ValueError
        When ``side`` is not one of ``None``, ``"sell"``, ``"buy"``.
    """
    found = quote_defects(bid, ask, side, locked_ok)
    return found[0] if found else None
