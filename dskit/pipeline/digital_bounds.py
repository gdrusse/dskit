"""Executable bounds on a digital from the vertical spreads a listed option chain can trade.

A binary contract that pays 1 when a terminal value ``S`` lands in a region has a price that
listed calls and puts on the same ``S`` and expiry already constrain: a vertical spread pays a
ramp from 0 to 1 between two strikes, so a spread placed entirely on one side of a strike ``K``
sits below or above the step ``1{S >= K}`` in every outcome. Trading those spreads at the quotes
on offer (selling at the bid, buying at the ask) turns the mid-price inequality into an
EXECUTABLE band: a digital priced outside it can be locked in against the chain. This module
computes that band for the three payoff geometries of :mod:`dskit.pipeline.binary_pricing`
(above, below, between), from whatever chain each contract row names.

Units. Prices are read as forward values of a claim paying at expiry: a quote divided by the
row's discount factor when ``discount_field`` names one, the quote as given otherwise (that is,
UNDISCOUNTED: the put-side bounds then treat a bond paying 1 at expiry as worth 1). The bound is
in price units of one contract paying 1, the spread being normalised by its strike width ``h``.

The four spreads, proved. Write ``c(k)`` and ``p(k)`` for forward call and put values and take
two listed strikes ``a < b``, ``h = b - a``. The call spread ``((S - a)^+ - (S - b)^+) / h`` is 0
for ``S <= a``, 1 for ``S >= b`` and between them in ``[0, 1]``; the put spread
``((b - S)^+ - (a - S)^+) / h`` is 1 for ``S <= a`` and 0 for ``S >= b``. Then for ``K``:

1. Calls AHEAD, ``K <= a < b``: the call spread is 0 wherever ``S < K`` (there ``S < a``) and at
   most 1 elsewhere, so it is at most ``1{S >= K}`` in every outcome. A digital priced below what
   the spread SELLS for is bought against it at a riskless credit, so
   ``P(S >= K) >= (bid c(a) - ask c(b)) / h``.
2. Calls BEHIND, ``a < b <= K``: the call spread is 1 wherever ``S >= K`` (there ``S >= b``) and
   at least 0 elsewhere, so it is at least the step; a digital priced above what the spread COSTS
   is sold against it: ``P(S >= K) <= (ask c(a) - bid c(b)) / h``.
3. Puts AHEAD, ``K <= a < b``: the put spread is 1 wherever ``S < K`` (there ``S < a``), so it
   super-replicates ``1{S < K}``: ``P(S < K) <= (ask p(b) - bid p(a)) / h``, hence
   ``P(S >= K) >= 1 - (ask p(b) - bid p(a)) / h``.
4. Puts BEHIND, ``a < b <= K``: the put spread is 0 wherever ``S >= K`` (there ``S >= b``), so it
   sub-replicates ``1{S < K}``: ``P(S < K) >= (bid p(b) - ask p(a)) / h``, hence
   ``P(S >= K) <= 1 - (bid p(b) - ask p(a)) / h``.

In all four the leg nearest ``K`` is SOLD at its bid and the next listed strike away is BOUGHT at
its ask, which is how :class:`_Strip` finds them: the nearest strike on the right side of ``K``
whose bid is sellable, then the nearest strike beyond it whose ask is buyable. Any such pair is
valid; the nearest is the tightest a convex price curve allows and the one the ADR names. When
the nearest pair's edge is UNUSABLE (below), the next farther pair is tried (the next ask out,
then the next bid out), because any pair bounds the digital. The call and put bands bound the
same number, so the TIGHTER edge of each is kept (the larger lower, the smaller upper), clipped
to ``[0, 1]``, each carrying its own error. A ``K`` with no usable pair on a side has no bound on
that side and is marked, never interpolated between strikes.

Float error, proved. Every input float ``x`` (a quote, a strike, a discount factor) is read as
standing for some real within half an ulp, ``|x* - x| <= u |x|``, ``u = 2^-53``; every float
operation rounds with ``fl(a op b) = (a op b)(1 + e)``, ``|e| <= u`` (no underflow: values far above
``1e-290``). An edge ``offset + sign * (q_n - q_f) / h`` then carries the bound ``E`` that
:func:`_spread_edge` computes, derived term by term:

- a price ``q = fl(b / d)`` against ``q* = b* / d*`` is within ``e_q = 3u|q|`` (one rounding plus the
  two inputs' half ulps, to first order); undiscounted, ``q = b`` is within ``e_q = u|q|``;
- the spread ``n = fl(q_n - q_f)`` is within ``E_n = u|n| + e_qn + e_qf`` of ``n* = q_n* - q_f*``;
- the width ``h = fl(|K_f - K_n|)`` is within ``E_h = u(h + |K_n| + |K_f|)`` of ``h*``: the strikes'
  own half ulps, ``u(|K_n| + |K_f|)``, dominate when two strikes are nearly one (``0.1 + 0.2`` and
  ``0.3``). A pair is usable only when ``h > 2 E_h``, so ``h* >= h - E_h > 0`` and the strikes'
  order is known;
- ``c = fl(n / h)`` is within ``E_c = (E_n + |c| E_h) / (h - E_h) + u|c|`` of ``c* = n* / h*``, from
  ``|n/h - n*/h*| <= |n - n*| / h* + |n| |h - h*| / (h h*)`` and ``h* >= h - E_h``;
- ``fl(1 + sign c)`` adds ``u|edge|`` when the offset is 1; a sign flip is exact.

The first-order sum is DOUBLED (``_SLACK``), which covers every dropped second-order term (each
below ``4u`` relative) and the rounding of the bound's own arithmetic, so ``|edge - edge*| <= E``
always; ``tests/pipeline/test_digital_bounds.py`` checks it against exact ``fractions.Fraction``
arithmetic on random edges, input half-ulps included. An edge whose ``E`` exceeds
:data:`EDGE_MAX_ERROR` is UNUSABLE and never reported. Clipping to ``[0, 1]`` is 1-Lipschitz, so a
clipped edge keeps its ``E``. The interval arithmetic below adds ``|coefficient| E`` per term plus
its own roundings, and a band is ``crossed_band`` only when ``lower > upper + E_lower + E_upper``: a
crossing larger than both edges' proven error is a parity violation, never float dust, and no
quote elsewhere in the chain moves the allowance. Accepted limit: strikes near ``1e5`` spaced one
cent apart carry ``|c| E_h / h`` of about ``4e-9`` per unit edge, above the cap, so such a pair is
unusable (with no farther pair, the side is marked ``no_*_bracket``).

Geometries. Every shipped geometry is affine in the survival values it reads (above:
``s(L)``; below: ``1 - s(U)``; between: ``s(L) - s(U)``), so its band follows from each read
strike's band by interval arithmetic: a positive coefficient takes the strike's lower edge into
the lower bound, a negative one its upper edge. The coefficients are probed from the geometry
itself (:func:`_affine_terms`), so this module adds no payoff vocabulary and an empty range
(``lower == upper``) is exactly zero. A lower bound above the upper (the call and put bands
disagree beyond their spreads, a parity violation) is reported as ``crossed_band``, not hidden.

Quotes. :func:`quote_problems` is the one owner of the quote rule (graduated from a child's
contracts module, unchanged): nonnegative and uncrossed, a provider 0 on the side traded is no
market, and a size must cover the count asked for. This node adds ONE rule of its own, the proof's
premise: a traded price, a strike or a discount factor whose magnitude is nonzero and below
:data:`PRICE_FLOOR` is refused by name, because a subnormal input rounds with an ABSOLUTE error the
relative bound above does not cover. Every refused quote side is listed by name on the
``refusals`` output; a refused side is never used.

Keys. A chain is named by the columns ``chain_fields`` (contract side) and ``quote_chain_fields``
(quote side) list, matched in order; each cell is read by :func:`~dskit.pipeline.binary_curve.row_key`
(a str or a non-bool int; ``1`` and ``"1"`` are different chains). A contract whose chain has a
missing cell (None, absent or ``""``) is ``no_chain``, one with a refused cell ``bad_chain_key``; a
quote with either is refused on both sides. The right column is read by the same rule
(:meth:`DigitalBounds._right`).

Accepted limit, cost. Each side tries the nearest usable pair first and falls back farther only
when an edge is unusable, so a chain whose near pairs are all unusable costs ``O(n^2)`` pairs per
strike side (``n`` listed strikes); every real chain stops at the first pair. Bands are memoised
per strike.

Import cost: stdlib only.
"""

import math
from bisect import bisect_left, bisect_right
from collections import Counter
from decimal import Decimal

from dskit.pipeline.binary_curve import (
    KEY_MISSING,
    fields_key,
    key_fields_problems,
    name_ok,
    named_payoff,
    output_collision_problems,
    row_key,
)
from dskit.pipeline.binary_pricing import PAYOFFS, STATUS_SUFFIX
from dskit.pipeline.node import Node, reject_unknown_params
from dskit.pipeline.records import number_ok, price_ok

__all__ = [
    "DEFAULT_MIN_SIZE",
    "EDGE_MAX_ERROR",
    "LOWER_SUFFIX",
    "PRICE_FLOOR",
    "STATUSES",
    "STATUS_OK",
    "STATUS_SUFFIX",
    "UPPER_SUFFIX",
    "DigitalBounds",
    "quote_problems",
]

#: The status a bounded row carries; every other status in :data:`STATUSES` names what is missing.
STATUS_OK = "ok"
#: Every status the node writes, in the order its checks run.
STATUSES = (
    STATUS_OK, "unknown_payoff", "bad_bounds", "bad_chain_key", "no_chain", "no_bracket", "no_lower_bracket",
    "no_upper_bracket", "crossed_band",
)

#: The largest proven float error an edge may carry and still be used (module docstring, "Float
#: error, proved"). An edge whose bound exceeds it is unusable: the next farther pair is tried.
EDGE_MAX_ERROR = 1e-9

#: The smallest nonzero magnitude a traded price, a strike or a discount factor may have (module
#: docstring, "Quotes"): far below any real price, far above the subnormal range the proof excludes.
PRICE_FLOOR = 1e-12

#: Suffixes of the columns written beside ``bound_field``.
LOWER_SUFFIX = "_lower"
UPPER_SUFFIX = "_upper"

#: Contracts a quote's sizes must cover when size columns are named and ``min_size`` is not.
DEFAULT_MIN_SIZE = 1

_SIDES = (None, "sell", "buy")
_SELL, _BUY = "sell", "buy"
_CALL, _PUT = "call", "put"

#: The unit roundoff of a binary64 float, ``2**-53``.
_U = 2.0 ** -53
#: The factor the first-order error sum is multiplied by: it covers every dropped second-order term
#: and the rounding of the bound's own arithmetic (module docstring).
_SLACK = 2.0


def _amount(value):
    """Say whether ``value`` is a finite Decimal or a finite non-bool number."""
    if type(value) is float:        # the hot case: number_ok's answer without its isinstance chain
        return math.isfinite(value)
    if isinstance(value, Decimal):
        return value.is_finite()
    return number_ok(value)


def _size(value):
    """Say whether ``value`` is a usable quote size: a finite non-bool number >= 0."""
    return number_ok(value) and value >= 0


def quote_problems(bid, ask, bid_size, ask_size, count, side=None):
    """List what stops a quote from filling ``count`` contracts on ``side``.

    The one owner of the quote rules (ADR-0187, graduated by ADR-0249): a quote is nonnegative
    and uncrossed; with ``side=None`` both sizes must cover ``count``; a sale (``"sell"``) needs a
    positive bid and a bid size that covers it; a purchase (``"buy"``) needs a positive ask and an
    ask size that covers it. A provider ``0`` on the side traded means no market, never a free
    fill — while a far-strike wing with no bid stays buyable.

    Parameters
    ----------
    bid, ask : Decimal or float
        The quote, in one numeric family.
    bid_size, ask_size : int or float
        The sizes as the source sends them.
    count : int
        Contracts to fill, >= 0; ``0`` is the row-level rule (a valid quote, nothing to cover).
    side : None, "sell" or "buy"
        Which side is traded; ``None`` judges both sizes.

    Returns
    -------
    list of str
        Every problem found; empty when the quote fills.

    Raises
    ------
    ValueError
        When ``side`` or ``count`` is not one of the above.

    Examples
    --------
    A wing with no bid can be bought but not sold::

        quote_problems(0.0, 0.05, 0, 4, 2, side="buy")    # []
        quote_problems(0.0, 0.05, 0, 4, 2, side="sell")
        # -> ['bid must be positive to sell, got 0.0', 'bid_size 0 does not cover count 2']
    """
    if side not in _SIDES:
        raise ValueError(f"side must be one of {_SIDES}, got {side!r}")
    if isinstance(count, bool) or not isinstance(count, int) or count < 0:
        raise ValueError(f"count must be an int >= 0, got {count!r}")
    problems = []
    for name, value in (("bid", bid), ("ask", ask)):
        if not _amount(value):
            problems.append(f"{name} must be a finite number, got {value!r}")
    if not problems:
        if bid < 0 or ask < 0:
            problems.append(f"quote must be nonnegative, got bid {bid} ask {ask}")
        elif ask < bid:
            problems.append(f"quote must be uncrossed, got bid {bid} above ask {ask}")
        if side == "sell" and not bid > 0:
            problems.append(f"bid must be positive to sell, got {bid}")
        if side == "buy" and not ask > 0:
            problems.append(f"ask must be positive to buy, got {ask}")
    for name, value in (("bid_size", bid_size), ("ask_size", ask_size)):
        if side is not None and name != f"{'bid' if side == 'sell' else 'ask'}_size":
            continue
        if not _size(value):
            problems.append(f"{name} must be a number >= 0, got {value!r}")
        elif value < count:
            problems.append(f"{name} {value} does not cover count {count}")
    return problems


def _affine_terms(geometry, lower, upper):
    """Return ``(constant, {strike: coefficient})`` of a geometry affine in the survival values it reads."""
    given = {"lower": lower, "upper": upper}
    strikes = sorted({given[bound] for bound in geometry.bounds})
    constant = geometry.yes_probability(lambda k: 0.0, lower, upper)
    terms = {}
    for strike in strikes:
        coefficient = geometry.yes_probability(lambda k, s=strike: 1.0 if k == s else 0.0, lower, upper) - constant
        if coefficient:
            terms[strike] = coefficient
    return constant, terms


def _forward(quote, discount):
    """Return ``(forward value, proven error)`` of a quote, divided by ``discount`` unless it is None."""
    if discount is None:
        return quote, _U * abs(quote)
    value = quote / discount
    return value, 3.0 * _U * abs(value)


def _spread_edge(near_k, near, far_k, far, offset, sign):
    """Return ``(offset + sign * (near - far) / |far_k - near_k|, its proven error)``, or None.

    ``near`` and ``far`` are ``(value, error)`` pairs from :func:`_forward`. None when the two
    strikes are too close for their order to be known (module docstring, "Float error, proved").
    """
    h = abs(far_k - near_k)
    e_h = _U * (h + abs(near_k) + abs(far_k))
    if not h > 2.0 * e_h:
        return None
    n = near[0] - far[0]
    credit = n / h
    e_c = (_U * abs(n) + near[1] + far[1] + abs(credit) * e_h) / (h - e_h) + _U * abs(credit)
    edge = offset + sign * credit
    return edge, _SLACK * (e_c + (_U * abs(edge) if offset else 0.0))


def _clip(edge, lower):
    """Clip a lower edge at 0 or an upper edge at 1, keeping its error (clipping is 1-Lipschitz)."""
    value, error = edge
    return (max(0.0, value) if lower else min(1.0, value)), error


class _Strip:
    """One right of one chain: sellable bids and buyable asks by strike, as ``(forward value, error)``."""

    def __init__(self):
        self.bids, self.asks = {}, {}

    def freeze(self):
        """Sort the strikes once, after every quote is in."""
        self.bid_strikes, self.ask_strikes = sorted(self.bids), sorted(self.asks)

    def ahead(self, strike):
        """Yield ``(near, near bid, far, far ask)`` selling a strike >= ``strike``, the nearest pair first."""
        for i in range(bisect_left(self.bid_strikes, strike), len(self.bid_strikes)):
            near = self.bid_strikes[i]
            for far in self.ask_strikes[bisect_right(self.ask_strikes, near):]:
                yield near, self.bids[near], far, self.asks[far]

    def behind(self, strike):
        """Yield ``(near, near bid, far, far ask)`` selling a strike <= ``strike``, the nearest pair first."""
        for i in range(bisect_right(self.bid_strikes, strike) - 1, -1, -1):
            near = self.bid_strikes[i]
            for far in reversed(self.ask_strikes[:bisect_left(self.ask_strikes, near)]):
                yield near, self.bids[near], far, self.asks[far]


class _Chain:
    """The call and put strips of one chain, and the band of ``P(S >= K)`` they bound."""

    def __init__(self):
        self.calls, self.puts = _Strip(), _Strip()
        self._bands = {}

    def freeze(self):
        """Sort both strips once every quote is filed."""
        self.calls.freeze()
        self.puts.freeze()

    def band(self, strike):
        """Return ``(lower, upper)`` of ``P(S >= strike)``: each ``(value, error)``, or None when no spread bounds it."""
        if strike not in self._bands:
            self._bands[strike] = self._band(strike)
        return self._bands[strike]

    def _band(self, strike):
        """Return the tighter usable call and put edges (module docstring, cases 1-4), each with its own error."""
        # Every case sells the near leg and buys the far one, so its credit is
        # c = (near bid - far ask) / h. Case 1 (calls ahead): lower = c. Case 2 (calls behind):
        # upper = -c. Case 3 (puts ahead): P(S < K) <= -c, so lower = 1 + c. Case 4 (puts behind):
        # P(S < K) >= c, so upper = 1 - c.
        lowers = [e for e in (_usable_edge(self.calls.ahead(strike), 0.0, 1.0, True),
                              _usable_edge(self.puts.ahead(strike), 1.0, 1.0, True)) if e is not None]
        uppers = [e for e in (_usable_edge(self.calls.behind(strike), 0.0, -1.0, False),
                              _usable_edge(self.puts.behind(strike), 1.0, -1.0, False)) if e is not None]
        return (max(lowers, key=lambda e: e[0]) if lowers else None,
                min(uppers, key=lambda e: e[0]) if uppers else None)


def _usable_edge(pairs, offset, sign, lower):
    """Return the clipped ``(edge, error)`` of the nearest pair whose error is within :data:`EDGE_MAX_ERROR`, or None."""
    for near_k, near, far_k, far in pairs:
        edge = _spread_edge(near_k, near, far_k, far, offset, sign)
        if edge is not None and edge[1] <= EDGE_MAX_ERROR:
            return _clip(edge, lower)
    return None


class DigitalBounds(Node):
    """Add the executable vertical-spread band of each binary contract's YES price (role ``transform``).

    Inputs: ``records``, the contract rows (payoff name, bounds, the chain they read), and
    ``quotes``, the option quote rows of every chain (chain key, strike, right, bid, ask; sizes
    and a discount factor when named). Outputs: ``records``, each contract row with
    ``<bound_field>_lower``, ``<bound_field>_upper`` (None on a side nothing bounds) and
    ``<bound_field>_status`` (one of :data:`STATUSES`); ``refusals``, one row per refused quote
    side ``{chain, strike, right, side, problems}``; and ``census``, ``{"rows", "bounded",
    "by_status", "quotes": {"rows", "refused", "by_side"}}``. The derivation, units and
    inequality directions are in the module docstring.

    Parameters
    ----------
    params : dict
        REQUIRED column names: ``payoff_field``, ``lower_field``, ``upper_field`` and
        ``chain_fields`` (a list of columns) on a contract row; ``quote_chain_fields`` (the same
        number of columns, matched in order), ``strike_field``, ``right_field``, ``bid_field`` and
        ``ask_field`` on a quote row; ``call_value`` and
        ``put_value``, the two values ``right_field`` carries (distinct); ``bound_field``, the
        output prefix. OPTIONAL: ``discount_field`` (a quote's discount factor to expiry; absent
        means undiscounted), ``bid_size_field`` and ``ask_size_field`` (both or neither) with
        ``min_size`` (int >= 0, default :data:`DEFAULT_MIN_SIZE`, only with sizes).

    Examples
    --------
    Bound each contract from the chain its ``snapshot`` column names::

        node = DigitalBounds("bounds", {
            "payoff_field": "payoff", "lower_field": "lo", "upper_field": "hi",
            "chain_fields": ["snapshot"], "quote_chain_fields": ["snapshot"], "strike_field": "strike",
            "right_field": "right", "call_value": "C", "put_value": "P", "bid_field": "bid",
            "ask_field": "ask", "bound_field": "digital"})
        out = node.run(ctx, {"records": contracts, "quotes": chain_quotes})
        # -> out["records"][0]["digital_lower"] <= YES fair value <= out["records"][0]["digital_upper"]
    """

    role = "transform"
    outputs = ("records", "refusals", "census")
    _CONTRACT_FIELDS = ("payoff_field", "lower_field", "upper_field")
    _QUOTE_FIELDS = ("strike_field", "right_field", "bid_field", "ask_field")
    _REQUIRED = (*_CONTRACT_FIELDS, *_QUOTE_FIELDS, "call_value", "put_value", "bound_field")
    _KEY_PARAMS = ("chain_fields", "quote_chain_fields")
    _SIZE_FIELDS = ("bid_size_field", "ask_size_field")
    _PARAMS = (*_REQUIRED, *_KEY_PARAMS, "discount_field", *_SIZE_FIELDS, "min_size")

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
        for name in cls._REQUIRED:
            if not name_ok(params.get(name)):
                problems.append(f"{name} is required: a non-empty string, got {params.get(name)!r}")
        problems += key_fields_problems(params, *cls._KEY_PARAMS)
        for name in ("discount_field", *cls._SIZE_FIELDS):
            if name in params and not name_ok(params[name]):
                problems.append(f"{name} must be a non-empty column name, got {params[name]!r}")
        if name_ok(params.get("call_value")) and params.get("call_value") == params.get("put_value"):
            problems.append(f"call_value and put_value must differ, both are {params['call_value']!r}")
        sizes = [name in params for name in cls._SIZE_FIELDS]
        if any(sizes) and not all(sizes):
            problems.append("bid_size_field and ask_size_field are declared together: name both sizes or neither")
        if "min_size" in params:
            size = params["min_size"]
            if not any(sizes):
                problems.append("min_size needs bid_size_field and ask_size_field: there is no size to cover it")
            if isinstance(size, bool) or not isinstance(size, int) or size < 0:
                problems.append(f"min_size must be an int >= 0, got {size!r}")
        return problems + cls._collision_problems(params)

    @classmethod
    def _collision_problems(cls, params):
        """Problems with an output column that would overwrite a contract or chain input column."""
        name, chains = params.get("bound_field"), params.get("chain_fields")
        if not name_ok(name):
            return []
        outputs = (name + LOWER_SUFFIX, name + UPPER_SUFFIX, name + STATUS_SUFFIX)
        named = [*(params.get(k) for k in cls._CONTRACT_FIELDS), *(chains if isinstance(chains, list) else ())]
        return output_collision_problems("bound_field", name, outputs, named)

    @classmethod
    def serving_effect(cls, params, verified_run_evidence):
        """Answer ``pure``: the columns are a function of the rows and the params.

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
        """Refuse a ``records`` or ``quotes`` port that is not a list.

        Parameters
        ----------
        inputs : dict
            The wired ports.

        Returns
        -------
        list of str
            One problem per port that is not a list of rows.
        """
        return [f"{port} must be a list of rows, got {type(inputs.get(port)).__name__}"
                for port in ("records", "quotes") if not isinstance(inputs.get(port), list)]

    def _count(self):
        """Contracts a quote side must cover: ``min_size`` with sizes, 0 without."""
        if "bid_size_field" not in self.params:
            return 0
        return self.params.get("min_size", DEFAULT_MIN_SIZE)

    def _right(self, quote):
        """Return ``"call"``, ``"put"`` or None: the right a quote's cell names, read by its characters.

        The one reading of the right column, by :func:`~dskit.pipeline.binary_curve.row_key`:
        ``numpy.str_("C")`` and a ``StrEnum`` whose value is ``"C"`` are ``"C"``; a missing or refused
        cell, or one that is neither declared value, is None.
        """
        p = self.params    # a missing or refused cell's key is None, which neither declared value is
        return {row_key(p["call_value"])[0]: _CALL, row_key(p["put_value"])[0]: _PUT}.get(
            row_key(quote.get(p["right_field"]))[0])

    def _chain(self, row, fields_param):
        """Return ``(key, problem)`` of the chain a row's ``fields_param`` columns name."""
        return fields_key(row, self.params[fields_param])

    def _row_problems(self, quote):
        """Problems that make a quote row unusable on both sides."""
        p = self.params
        problems = []
        if not price_ok(quote.get(p["strike_field"])):
            problems.append(f"strike must be a positive finite number, got {quote.get(p['strike_field'])!r}")
        if self._right(quote) is None:
            problems.append(f"right must be {p['call_value']!r} or {p['put_value']!r}, got "
                            f"{quote.get(p['right_field'])!r}")
        why = self._chain(quote, "quote_chain_fields")[1]
        if why:
            problems.append(f"chain key {'is missing' if why == KEY_MISSING else why}")
        if "discount_field" in p and not price_ok(quote.get(p["discount_field"])):
            problems.append(f"discount factor must be a positive finite number, got {quote.get(p['discount_field'])!r}")
        for name in ("strike_field", "discount_field"):
            if name in p and _below_floor(quote.get(p[name])):
                problems.append(f"{name[:-6]} {quote.get(p[name])!r} is below PRICE_FLOOR {PRICE_FLOOR}")
        return problems

    def _side_problems(self, quote, side):
        """Return what :func:`quote_problems` says about trading ``quote`` on ``side``."""
        p = self.params
        sized = "bid_size_field" in p
        problems = quote_problems(quote.get(p["bid_field"]), quote.get(p["ask_field"]),
                                  quote.get(p["bid_size_field"]) if sized else 0,
                                  quote.get(p["ask_size_field"]) if sized else 0, self._count(), side)
        price = quote.get(p["bid_field" if side == _SELL else "ask_field"])
        if not problems and _below_floor(price):
            problems.append(f"{'bid' if side == _SELL else 'ask'} {price!r} is below PRICE_FLOOR {PRICE_FLOOR}")
        return problems

    def _quote_key(self, quote):
        """Return the key two listings of one strike and right share (``100`` and ``100.0`` are one strike)."""
        p = self.params
        return self._chain(quote, "quote_chain_fields")[0], self._right(quote), float(quote[p["strike_field"]])

    def _file(self, quote, chains, base):
        """File a usable quote's sides into its chain; return a refusal row per side it cannot trade."""
        p = self.params
        discount = float(quote[p["discount_field"]]) if "discount_field" in p else None
        chain = chains.setdefault(self._chain(quote, "quote_chain_fields")[0], _Chain())
        strip = chain.calls if self._right(quote) == _CALL else chain.puts
        refusals = []
        for side, book, field in ((_SELL, strip.bids, "bid_field"), (_BUY, strip.asks, "ask_field")):
            side_problems = self._side_problems(quote, side)
            if side_problems:
                refusals.append({**base, "side": side, "problems": side_problems})
            else:
                book[float(quote[p["strike_field"]])] = _forward(float(quote[p[field]]), discount)
        return refusals

    def _chains(self, quotes):
        """Build every chain's strips; return ``(chains, refusals, refused quote count)``."""
        p = self.params
        problems = [self._row_problems(quote) for quote in quotes]
        counts = Counter(self._quote_key(q) for q, found in zip(quotes, problems) if not found)
        chains, refusals, refused = {}, [], 0
        for quote, found in zip(quotes, problems):
            base = {"chain": [quote.get(f) for f in p["quote_chain_fields"]], "strike": quote.get(p["strike_field"]),
                    "right": quote.get(p["right_field"])}
            if not found and counts[self._quote_key(quote)] > 1:
                found = ["duplicate quote: the chain lists this strike and right more than once"]
            rows = [{**base, "side": "both", "problems": found}] if found else self._file(quote, chains, base)
            refusals += rows
            refused += bool(rows)
        for chain in chains.values():
            chain.freeze()
        return chains, refusals, refused

    def _refusal(self, row, chains):
        """Return the status that says why ``row`` cannot be bounded at all, or None."""
        p = self.params
        geometry = named_payoff(row.get(p["payoff_field"]))
        if geometry is None:
            return "unknown_payoff"
        lower, upper = row.get(p["lower_field"]), row.get(p["upper_field"])
        given = {"lower": lower, "upper": upper}
        if geometry.bounds_problem(lower, upper) is not None or not all(price_ok(given[b]) for b in geometry.bounds):
            return "bad_bounds"
        key, why = self._chain(row, "chain_fields")
        if why is not None:
            return "no_chain" if why == KEY_MISSING else "bad_chain_key"
        if key not in chains:
            return "no_chain"
        return None

    def _bounded(self, row, chains):
        """Write the three band columns onto ``row`` (a copy)."""
        p, name = self.params, self.params["bound_field"]
        status = self._refusal(row, chains)
        low = high = None
        if status is None:
            geometry = PAYOFFS[row[p["payoff_field"]]]
            constant, terms = _affine_terms(geometry, row.get(p["lower_field"]), row.get(p["upper_field"]))
            low, high = _interval(constant, terms, chains[self._chain(row, "chain_fields")[0]].band)
            status = _band_status(low, high)
        row.update({name + LOWER_SUFFIX: None if low is None else low[0],
                    name + UPPER_SUFFIX: None if high is None else high[0], name + STATUS_SUFFIX: status})
        return row

    def run(self, ctx, inputs):
        """Bound every contract row from its chain's quotes.

        Parameters
        ----------
        ctx : NodeContext
            Unused.
        inputs : dict
            ``records``: the contract rows; ``quotes``: the option quote rows.

        Returns
        -------
        dict
            ``{"records": [...], "refusals": [...], "census": {...}}``, new row dicts in input order.
        """
        chains, refusals, refused = self._chains(inputs["quotes"])
        records = [self._bounded(dict(row), chains) for row in inputs["records"]]
        statuses = [row[self.params["bound_field"] + STATUS_SUFFIX] for row in records]
        census = {"rows": len(records), "bounded": statuses.count(STATUS_OK),
                  "by_status": {status: statuses.count(status) for status in STATUSES},
                  "quotes": {"rows": len(inputs["quotes"]), "refused": refused,
                             "by_side": {side: sum(r["side"] == side for r in refusals)
                                         for side in ("both", _SELL, _BUY)}}}
        self.log.info("bounded %d of %d contract(s); %d of %d quote(s) refused on some side",
                      census["bounded"], census["rows"], refused, len(inputs["quotes"]))
        return {"records": records, "refusals": refusals, "census": census}


def _below_floor(value):
    """Say whether ``value`` is a finite number whose magnitude is nonzero and below :data:`PRICE_FLOOR`."""
    return _amount(value) and 0 < abs(value) < PRICE_FLOOR


def _interval(constant, terms, band):
    """Interval arithmetic over an affine geometry: ``(lower, upper)`` as ``(value, error)``, None where an edge is.

    Each term adds ``|coefficient| * error`` of the edge it takes, plus the rounding of its own
    multiply and add; the final clip to ``[0, 1]`` keeps the error (it is 1-Lipschitz).
    """
    low = high = (constant, 0.0)
    for strike, coefficient in terms.items():
        edges = band(strike)
        take_low, take_high = (edges[0], edges[1]) if coefficient > 0 else (edges[1], edges[0])
        low = _add_term(low, coefficient, take_low)
        high = _add_term(high, coefficient, take_high)
    return (None if low is None else (max(0.0, low[0]), low[1]),
            None if high is None else (min(1.0, high[0]), high[1]))


def _add_term(total, coefficient, edge):
    """Return ``total + coefficient * edge`` as ``(value, error)``, or None when either is None."""
    if total is None or edge is None:
        return None
    product = coefficient * edge[0]
    value = total[0] + product
    return value, total[1] + abs(coefficient) * edge[1] + _SLACK * _U * (abs(product) + abs(value))


def _band_status(low, high):
    """Return the status of a computed band: crossed only beyond both edges' proven errors."""
    if low is None and high is None:
        return "no_bracket"
    if low is None:
        return "no_lower_bracket"
    if high is None:
        return "no_upper_bracket"
    return "crossed_band" if low[0] > high[0] + low[1] + high[1] else STATUS_OK
