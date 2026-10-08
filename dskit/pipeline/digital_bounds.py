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
valid; the nearest is the tightest a convex price curve allows and the one the ADR names. The
call and put bands bound the same number, so the TIGHTER edge of each is kept (the larger lower,
the smaller upper), clipped to ``[0, 1]``. A ``K`` with no usable pair on a side has no bound on
that side and is marked, never interpolated between strikes.

Geometries. Every shipped geometry is affine in the survival values it reads (above:
``s(L)``; below: ``1 - s(U)``; between: ``s(L) - s(U)``), so its band follows from each read
strike's band by interval arithmetic: a positive coefficient takes the strike's lower edge into
the lower bound, a negative one its upper edge. The coefficients are probed from the geometry
itself (:func:`_affine_terms`), so this module adds no payoff vocabulary and an empty range
(``lower == upper``) is exactly zero. A lower bound above the upper (the call and put bands
disagree beyond their spreads, a parity violation) is reported as ``crossed_band``, not hidden.

Quotes. :func:`quote_problems` is the one owner of the quote rule (graduated from a child's
contracts module, unchanged): nonnegative and uncrossed, a provider 0 on the side traded is no
market, and a size must cover the count asked for. Every refused quote side is listed by name on
the ``refusals`` output; a refused side is never used.

Import cost: stdlib only.
"""

import math
from bisect import bisect_left, bisect_right
from decimal import Decimal

from dskit.pipeline.binary_pricing import PAYOFFS, STATUS_SUFFIX
from dskit.pipeline.node import Node, reject_unknown_params
from dskit.pipeline.records import number_ok, price_ok

__all__ = [
    "BAND_TOLERANCE",
    "DEFAULT_MIN_SIZE",
    "LOWER_SUFFIX",
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
    STATUS_OK, "unknown_payoff", "bad_bounds", "no_chain", "no_bracket", "no_lower_bracket",
    "no_upper_bracket", "crossed_band",
)

#: Float dust allowed before a lower edge above the upper edge counts as ``crossed_band``: a locked,
#: arbitrage-free chain can compute its two edges a few ulps apart, which is no parity violation.
BAND_TOLERANCE = 1e-12

#: Suffixes of the columns written beside ``bound_field``.
LOWER_SUFFIX = "_lower"
UPPER_SUFFIX = "_upper"

#: Contracts a quote's sizes must cover when size columns are named and ``min_size`` is not.
DEFAULT_MIN_SIZE = 1

_SIDES = (None, "sell", "buy")
_SELL, _BUY = "sell", "buy"


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


class _Strip:
    """One right of one chain: sellable bids and buyable asks by strike, as forward values."""

    def __init__(self):
        self.bids, self.asks = {}, {}

    def freeze(self):
        """Sort the strikes once, after every quote is in."""
        self.bid_strikes, self.ask_strikes = sorted(self.bids), sorted(self.asks)

    def ahead(self, strike):
        """Return ``(near bid, far ask, width)`` selling the first strike >= ``strike``, or None."""
        i = bisect_left(self.bid_strikes, strike)
        if i == len(self.bid_strikes):
            return None
        near = self.bid_strikes[i]
        j = bisect_right(self.ask_strikes, near)
        if j == len(self.ask_strikes):
            return None
        far = self.ask_strikes[j]
        return self.bids[near], self.asks[far], far - near

    def behind(self, strike):
        """Return ``(near bid, far ask, width)`` selling the last strike <= ``strike``, or None."""
        i = bisect_right(self.bid_strikes, strike)
        if i == 0:
            return None
        near = self.bid_strikes[i - 1]
        j = bisect_left(self.ask_strikes, near)
        if j == 0:
            return None
        far = self.ask_strikes[j - 1]
        return self.bids[near], self.asks[far], near - far


class _Chain:
    """The call and put strips of one chain, and the band of ``P(S >= K)`` they bound."""

    def __init__(self):
        self.calls, self.puts = _Strip(), _Strip()
        self._bands = {}

    def freeze(self):
        """Sort both strips."""
        self.calls.freeze()
        self.puts.freeze()

    def band(self, strike):
        """Return ``(lower, upper)`` of ``P(S >= strike)``, each None when no spread bounds it."""
        if strike not in self._bands:
            self._bands[strike] = self._band(strike)
        return self._bands[strike]

    def _band(self, strike):
        """Return the tighter of the call and put edges (module docstring, cases 1-4), clipped to [0, 1]."""
        # Every case sells the near leg and buys the far one, so its credit is
        # c = (near bid - far ask) / h. Case 1 (calls ahead): lower = c. Case 2 (calls behind):
        # upper = -c. Case 3 (puts ahead): P(S < K) <= -c, so lower = 1 + c. Case 4 (puts behind):
        # P(S < K) >= c, so upper = 1 - c.
        lowers, uppers = [], []
        for spread, edges, offset, sign in ((self.calls.ahead(strike), lowers, 0.0, 1.0),
                                            (self.calls.behind(strike), uppers, 0.0, -1.0),
                                            (self.puts.ahead(strike), lowers, 1.0, 1.0),
                                            (self.puts.behind(strike), uppers, 1.0, -1.0)):
            if spread is not None:
                near_bid, far_ask, width = spread
                edges.append(offset + sign * (near_bid - far_ask) / width)
        return (max(0.0, max(lowers)) if lowers else None, min(1.0, min(uppers)) if uppers else None)


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
        ``chain_field`` on a contract row; ``quote_chain_field``, ``strike_field``,
        ``right_field``, ``bid_field`` and ``ask_field`` on a quote row; ``call_value`` and
        ``put_value``, the two values ``right_field`` carries (distinct); ``bound_field``, the
        output prefix. OPTIONAL: ``discount_field`` (a quote's discount factor to expiry; absent
        means undiscounted), ``bid_size_field`` and ``ask_size_field`` (both or neither) with
        ``min_size`` (int >= 0, default :data:`DEFAULT_MIN_SIZE`, only with sizes).

    Examples
    --------
    Bound each contract from the chain its ``snapshot`` column names::

        node = DigitalBounds("bounds", {
            "payoff_field": "payoff", "lower_field": "lo", "upper_field": "hi",
            "chain_field": "snapshot", "quote_chain_field": "snapshot", "strike_field": "strike",
            "right_field": "right", "call_value": "C", "put_value": "P", "bid_field": "bid",
            "ask_field": "ask", "bound_field": "digital"})
        out = node.run(ctx, {"records": contracts, "quotes": chain_quotes})
        # -> out["records"][0]["digital_lower"] <= YES fair value <= out["records"][0]["digital_upper"]
    """

    role = "transform"
    outputs = ("records", "refusals", "census")
    _CONTRACT_FIELDS = ("payoff_field", "lower_field", "upper_field", "chain_field")
    _QUOTE_FIELDS = ("quote_chain_field", "strike_field", "right_field", "bid_field", "ask_field")
    _REQUIRED = (*_CONTRACT_FIELDS, *_QUOTE_FIELDS, "call_value", "put_value", "bound_field")
    _SIZE_FIELDS = ("bid_size_field", "ask_size_field")
    _PARAMS = (*_REQUIRED, "discount_field", *_SIZE_FIELDS, "min_size")

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
            if not _name_ok(params.get(name)):
                problems.append(f"{name} is required: a non-empty string, got {params.get(name)!r}")
        for name in ("discount_field", *cls._SIZE_FIELDS):
            if name in params and not _name_ok(params[name]):
                problems.append(f"{name} must be a non-empty column name, got {params[name]!r}")
        if _name_ok(params.get("call_value")) and params.get("call_value") == params.get("put_value"):
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
        """Problems with an output column that would overwrite a contract input column."""
        name = params.get("bound_field")
        if not _name_ok(name):
            return []
        outputs = {name + LOWER_SUFFIX, name + UPPER_SUFFIX, name + STATUS_SUFFIX}
        clash = sorted(outputs & {params.get(k) for k in cls._CONTRACT_FIELDS if _name_ok(params.get(k))})
        return [f"bound_field {name!r} writes {sorted(outputs)}, which would overwrite the input column(s) {clash}"
                ] if clash else []

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

    def _row_problems(self, quote):
        """Problems that make a quote row unusable on both sides."""
        p = self.params
        problems = []
        if not price_ok(quote.get(p["strike_field"])):
            problems.append(f"strike must be a positive finite number, got {quote.get(p['strike_field'])!r}")
        if quote.get(p["right_field"]) not in (p["call_value"], p["put_value"]):
            problems.append(f"right must be {p['call_value']!r} or {p['put_value']!r}, got "
                            f"{quote.get(p['right_field'])!r}")
        if quote.get(p["quote_chain_field"]) is None:
            problems.append("chain key is missing")
        if "discount_field" in p and not price_ok(quote.get(p["discount_field"])):
            problems.append(f"discount factor must be a positive finite number, got {quote.get(p['discount_field'])!r}")
        return problems

    def _side_problems(self, quote, side):
        """Return what :func:`quote_problems` says about trading ``quote`` on ``side``."""
        p = self.params
        sized = "bid_size_field" in p
        return quote_problems(quote.get(p["bid_field"]), quote.get(p["ask_field"]),
                              quote.get(p["bid_size_field"]) if sized else 0,
                              quote.get(p["ask_size_field"]) if sized else 0, self._count(), side)

    def _chains(self, quotes):
        """Build every chain's strips; return ``(chains, refusals, refused quote count)``."""
        p = self.params
        keys = [(q.get(p["quote_chain_field"]), q.get(p["right_field"]), q.get(p["strike_field"])) for q in quotes]
        seen = {}
        for key in keys:
            seen[_hashable(key)] = seen.get(_hashable(key), 0) + 1
        chains, refusals, refused = {}, [], 0
        for quote, key in zip(quotes, keys):
            chain, right, strike = key
            base = {"chain": chain, "strike": strike, "right": right}
            problems = self._row_problems(quote)
            if not problems and seen[_hashable(key)] > 1:
                problems = ["duplicate quote: the chain lists this strike and right more than once"]
            if problems:
                refusals.append({**base, "side": "both", "problems": problems})
                refused += 1
                continue
            discount = float(quote[p["discount_field"]]) if "discount_field" in p else 1.0
            strip = chains.setdefault(_hashable(chain), _Chain())
            strip = strip.calls if right == p["call_value"] else strip.puts
            any_refused = False
            for side, book, field in ((_SELL, strip.bids, "bid_field"), (_BUY, strip.asks, "ask_field")):
                side_problems = self._side_problems(quote, side)
                if side_problems:
                    refusals.append({**base, "side": side, "problems": side_problems})
                    any_refused = True
                else:
                    book[float(strike)] = float(quote[p[field]]) / discount
            refused += any_refused
        for chain in chains.values():
            chain.freeze()
        return chains, refusals, refused

    def _refusal(self, row, chains):
        """Return the status that says why ``row`` cannot be bounded at all, or None."""
        p = self.params
        geometry = PAYOFFS.get(row.get(p["payoff_field"])) if isinstance(row.get(p["payoff_field"]), str) else None
        if geometry is None:
            return "unknown_payoff"
        lower, upper = row.get(p["lower_field"]), row.get(p["upper_field"])
        given = {"lower": lower, "upper": upper}
        if geometry.bounds_problem(lower, upper) is not None or not all(price_ok(given[b]) for b in geometry.bounds):
            return "bad_bounds"
        if _hashable(row.get(p["chain_field"])) not in chains:
            return "no_chain"
        return None

    def _bounded(self, row, chains):
        """Write the three band columns onto ``row`` (a copy)."""
        p, name = self.params, self.params["bound_field"]
        status = self._refusal(row, chains)
        low = high = None
        if status is None:
            geometry = PAYOFFS[row[p["payoff_field"]]]
            constant, terms = _affine_terms(geometry, row[p["lower_field"]], row[p["upper_field"]])
            chain = chains[_hashable(row[p["chain_field"]])]
            low, high = _interval(constant, terms, chain.band)
            status = _band_status(low, high)
        row.update({name + LOWER_SUFFIX: low, name + UPPER_SUFFIX: high, name + STATUS_SUFFIX: status})
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


def _name_ok(value):
    """Say whether ``value`` is a non-empty string."""
    return isinstance(value, str) and bool(value)


def _hashable(value):
    """Return ``value`` when it can key a dict, else its repr (a list key is still one chain)."""
    try:
        hash(value)
    except TypeError:
        return repr(value)
    return value


def _interval(constant, terms, band):
    """Interval arithmetic over an affine geometry: ``(lower, upper)``, a side None when an edge it needs is."""
    low = high = constant
    for strike, coefficient in terms.items():
        edges = band(strike)
        take_low, take_high = (edges[0], edges[1]) if coefficient > 0 else (edges[1], edges[0])
        low = None if low is None or take_low is None else low + coefficient * take_low
        high = None if high is None or take_high is None else high + coefficient * take_high
    return (None if low is None else max(0.0, low), None if high is None else min(1.0, high))


def _band_status(low, high):
    """Return the status of a computed band."""
    if low is None and high is None:
        return "no_bracket"
    if low is None:
        return "no_lower_bracket"
    if high is None:
        return "no_upper_bracket"
    return "crossed_band" if low > high + BAND_TOLERANCE else STATUS_OK
