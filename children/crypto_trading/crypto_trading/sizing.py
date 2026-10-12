"""Fractional-Kelly sizing of binary-contract orders, and the sequential backtest that compounds it.

The question: given the out-of-sample blend's P(YES) and the market's own quote at each decision
instant, how many contracts should a bankroll buy, and does sizing beat one contract per trade?

The split follows the repo's tiers. dskit's :class:`~dskit.pipeline.libs.pyomo.ScenarioUtilitySolve`
owns the mechanism (integer positions, self-financing cash, the concave-utility objective, an exact
post-solve recompute). This module owns only what is Kalshi's and this child's:

* :class:`BinaryQuote` is one market's executable state at one instant, with NO label field, so a solve
  cannot see an outcome. ``<ticker>|yes`` pays 1 if YES settles and costs the ask; ``<ticker>|no``
  pays 1 if NO settles and costs ``1 - bid``.
* :class:`LatentCopula` turns the markets' marginals into joint settlement weights (a one-factor
  Gaussian latent copula; ``rho = 0`` is independence).
* :class:`KalshiBinaryKellyMIO` is the per-instant doorway subclass: a per-contract taker fee from the
  venue mechanic in :mod:`crypto_trading.fees`' spec, at most one side per contract, a per-market
  exposure cap as a fraction of wealth, no selling (positions are held to settlement), and an optional
  budgeted confidence haircut.
* :class:`SettlementBook` is the cash book: it pays the EXACT order fee (rounded up to the cent by the
  mechanic) at fill and pays the payout at settlement, and a :class:`LabelLedger` refuses to reveal a
  label before its market closes.
* :class:`KalshiKellyBacktest` loops decision instants in time order, settling what has closed before
  each, sizing what is eligible, and compounding the bankroll. It runs the one-contract rule on the
  same candidates beside it, with the same fees.

Assumptions a reader must carry: candle data has no depth, so ``max_contracts_per_order`` stands in for
the size a touch quote can fill; fills are at the touch with no impact; the fee schedule is today's;
the latent correlation ``copula_rho`` is declared, not estimated, and assumes both markets are the same
"up" direction; the marginal probability of each market is held fixed from its newest row.

Import cost: stdlib + dskit (numpy and pyomo are imported inside the methods that run).
"""

import bisect
import math
from dataclasses import dataclass
from statistics import NormalDist
from types import MappingProxyType

from dskit.pipeline.fee_mechanics import fee_model_from_spec, fee_spec_problems
from dskit.pipeline.libs.pyomo import ScenarioUtilitySolve
from dskit.pipeline.libs.sklearn import REFIT_STATUS_SUFFIX
from dskit.pipeline.node import check_int_param, reject_unknown_params
from dskit.pipeline.records import number_ok
from dskit.pipeline.stats import across_fold_t

from . import fields as f
from .ports import ListPortsNode

__all__ = [
    "DEFAULTS", "ExecutableEdge", "KalshiBinaryKellyMIO", "KalshiKellyBacktest", "LabelLedger", "LatentCopula",
    "LookAheadError", "BinaryQuote", "SIDES", "knob", "SettlementBook", "name_of", "split_name",
]

#: The two sides of a binary contract.
SIDES = ("yes", "no")
#: Joins a ticker and a side into an instrument name.
NAME_SEP = "|"
DAY_MS = 86_400_000

#: Every default of this module's knobs, in ONE place: validation and ``run`` both read it. A knob the
#: doorway makes an owner decision (``risk_aversion_gamma``, ``cvar_*``, ``cardinality``, ``min_ticket``)
#: has no default here either; neither has the venue's ``fee_model`` or the row column ``fair_field``.
DEFAULTS = MappingProxyType({
    "starting_bankroll": 1000.0,
    "max_contracts_per_order": 100,
    "market_cap_frac": 0.1,
    "copula_rho": 0.8,
    "lead_min_minutes": 8,
    "min_edge": 0.02,
    "uncertainty_frac": 0.0,
    "uncertainty_budget": None,
})

#: The wealth envelope's lower edge never falls below this fraction of the wealth mark (the doorway needs it > 0).
_ENVELOPE_FLOOR_FRAC = 0.01
#: The envelope always brackets the wealth mark by at least this fraction on each side.
_ENVELOPE_PAD_FRAC = 0.01
_MIO_PARAMS_PASSED_DOWN = (
    "fee_model", "max_contracts_per_order", "market_cap_frac", "copula_rho", "uncertainty_frac", "uncertainty_budget",
)


def knob(params, name):
    """Return the declared value of a knob of this module, or its default.

    Parameters
    ----------
    params : dict
        A node's declared params.
    name : str
        A key of :data:`DEFAULTS`.

    Returns
    -------
    object
        ``params[name]`` when declared, else ``DEFAULTS[name]``.
    """
    return params.get(name, DEFAULTS[name])


def name_of(ticker, side):
    """Return the instrument name of one side of one contract.

    Parameters
    ----------
    ticker : str
        The market's ticker.
    side : str
        ``"yes"`` or ``"no"``.

    Returns
    -------
    str
        ``"<ticker>|<side>"``.

    Examples
    --------
    The YES side of a market::

        name_of("KXBTC15M-26SEP1500", "yes")
        # -> "KXBTC15M-26SEP1500|yes"
    """
    return f"{ticker}{NAME_SEP}{side}"


def split_name(name):
    """Return ``(ticker, side)`` of an instrument name.

    Parameters
    ----------
    name : str
        A name :func:`name_of` wrote.

    Returns
    -------
    tuple of str
        The ticker and the side.

    Raises
    ------
    ValueError
        When the name is not ``<ticker>|yes`` or ``<ticker>|no``.
    """
    ticker, _, side = str(name).rpartition(NAME_SEP)
    if not ticker or side not in SIDES:
        raise ValueError(f"{name!r} is not an instrument name '<ticker>{NAME_SEP}yes|no'")
    return ticker, side


class LookAheadError(ValueError):
    """A label was asked for before its market closed."""


@dataclass(frozen=True)
class BinaryQuote:
    """One market's executable state at one decision instant, label-free by construction.

    Parameters
    ----------
    ticker, series : str
        The market and its series.
    close_ms, decision_ms : int
        The market's close and the information instant, epoch milliseconds.
    lead_minutes : int
        The close minus the decision, in minutes.
    yes_bid, yes_ask : float
        The YES quote.
    fair : float
        P(YES) from the model, in ``[0, 1]``.
    fee_rate : float
        The series' effective fee rate.
    fee_yes, fee_no : float
        The row's ONE-contract taker fees for buying YES at the ask and NO at ``1 - bid``.

    Examples
    --------
    A market quoted 0.38 / 0.40 where the model says 0.55::

        quote = BinaryQuote("T", "S", 2_000_000, 1_000_000, 10, 0.38, 0.40, 0.55, 0.07, 0.02, 0.02)
        quote.price("no")       # -> 0.62
        quote.win_prob("no")    # -> 0.45
    """

    ticker: str
    series: str
    close_ms: int
    decision_ms: int
    lead_minutes: int
    yes_bid: float
    yes_ask: float
    fair: float
    fee_rate: float
    fee_yes: float
    fee_no: float

    @classmethod
    def from_row(cls, row, fair_field):
        """Build the quote of a decision row, or None when the row cannot be traded.

        Parameters
        ----------
        row : dict
            A decision row with the quote, fee and fair-value columns.
        fair_field : str
            The column that holds P(YES).

        Returns
        -------
        BinaryQuote or None
            None when the quote is not two-sided, the fee is not priced, or the fair value is not a probability.
        """
        needed = (f.YES_BID, f.YES_ASK, f.FEE_BUY_YES, f.FEE_BUY_NO, "fee_rate", fair_field)
        if not (row.get(f.TWO_SIDED) is True and row.get("fee_status") == "ok"):
            return None
        if not all(number_ok(row.get(key)) for key in needed) or not 0.0 <= row[fair_field] <= 1.0:
            return None
        return cls(row[f.TICKER], row[f.SERIES], int(row[f.CLOSE_MS]), int(row[f.DECISION_MS]), int(row[f.LEAD]),
                   float(row[f.YES_BID]), float(row[f.YES_ASK]), float(row[fair_field]), float(row["fee_rate"]),
                   float(row[f.FEE_BUY_YES]), float(row[f.FEE_BUY_NO]))

    def price(self, side):
        """Return the price per contract of buying ``side``: the ask for YES, ``1 - bid`` for NO."""
        return self.yes_ask if side == "yes" else 1.0 - self.yes_bid

    def win_prob(self, side):
        """Return the model's probability that ``side`` pays."""
        return self.fair if side == "yes" else 1.0 - self.fair

    def mark(self, side):
        """Return the mid-price value of one contract of ``side``: the YES mid, or one minus it for NO."""
        mid = 0.5 * (self.yes_bid + self.yes_ask)
        return mid if side == "yes" else 1.0 - mid

    def fee_one(self, side):
        """Return the row's one-contract taker fee of buying ``side``."""
        return self.fee_yes if side == "yes" else self.fee_no


class ExecutableEdge:
    """The candidate rule: a side is eligible when its edge at the touch clears the fee by a margin.

    The edge of a side is ``win_prob - price - fee`` at the price one actually pays (the ask for YES,
    ``1 - bid`` for NO), with the row's one-contract fee. It must exceed ``min_edge`` strictly, and the
    lead must be at least ``lead_min_minutes``.

    Parameters
    ----------
    lead_min_minutes : int
        The smallest eligible lead.
    min_edge : float
        The margin over the fee, in dollars per contract.

    Examples
    --------
    Two cents over the fee, leads of 8 minutes or more::

        gate = ExecutableEdge(8, 0.02)
        gate.sides(quote)
        # -> ["yes"] when 0.55 - 0.40 - 0.02 > 0.02
    """

    def __init__(self, lead_min_minutes, min_edge):
        self.lead_min_minutes = lead_min_minutes
        self.min_edge = min_edge

    def sides(self, quote):
        """Return the sides of ``quote`` that are candidates.

        Parameters
        ----------
        quote : BinaryQuote
            The market's state at this instant.

        Returns
        -------
        list of str
            The eligible sides, possibly empty.
        """
        if quote.lead_minutes < self.lead_min_minutes:
            return []
        return [s for s in SIDES if quote.win_prob(s) - quote.price(s) - quote.fee_one(s) > self.min_edge]


class LatentCopula:
    """Joint settlement weights of several binary markets from a one-factor Gaussian latent copula.

    Market ``j`` pays YES when a latent ``Z_j`` exceeds the threshold that gives it its marginal
    probability; the ``Z_j`` share one factor and have pairwise correlation ``rho``. ``rho = 0`` is
    independence, exactly the product of the marginals. The factor integral is a trapezoid sum on a
    fixed grid, renormalised to total weight one.

    Parameters
    ----------
    rho : float
        The latent correlation, in ``[0, 1)``.

    Examples
    --------
    Two coin-flip markets, latent correlation 0.8::

        weights = LatentCopula(0.8).weights([0.5, 0.5])
        # -> four weights over (NO,NO), (YES,NO), (NO,YES), (YES,YES); the last is 0.25 + asin(0.8) / (2 pi)
    """

    GRID_POINTS = 2001
    GRID_HALF_WIDTH = 8.0
    PROB_CLIP = 1e-9

    def __init__(self, rho):
        if not (number_ok(rho) and 0.0 <= rho < 1.0):
            raise ValueError(f"rho must be a number in [0, 1), got {rho!r}")
        self.rho = float(rho)

    @staticmethod
    def outcomes(n_markets):
        """Return the ``(2**n, n)`` 0/1 matrix of settlements; row ``w`` has market ``j`` YES when bit ``j`` of ``w`` is 1.

        Parameters
        ----------
        n_markets : int
            The number of markets, at least 1.

        Returns
        -------
        numpy.ndarray
            The settlement of every market in every state.
        """
        import numpy as np

        states = np.arange(2 ** n_markets)
        return ((states[:, None] >> np.arange(n_markets)[None, :]) & 1).astype(float)

    def weights(self, probs):
        """Return the probability of each joint settlement state.

        Parameters
        ----------
        probs : sequence of float
            Each market's P(YES), in ``[0, 1]``; clipped away from the ends by :attr:`PROB_CLIP`.

        Returns
        -------
        numpy.ndarray
            ``(2**n,)`` weights summing to one, in the order of :meth:`outcomes`.
        """
        import numpy as np

        p = np.clip(np.asarray(probs, dtype=float), self.PROB_CLIP, 1.0 - self.PROB_CLIP)
        yes = self.outcomes(len(p))
        if self.rho == 0.0 or len(p) == 1:
            weights = np.prod(np.where(yes == 1.0, p[None, :], 1.0 - p[None, :]), axis=1)
        else:
            weights = self._factor_weights(p, yes)
        return weights / weights.sum()

    def _factor_weights(self, p, yes):
        """Integrate the conditional-independence product over the common factor."""
        import numpy as np

        grid = np.linspace(-self.GRID_HALF_WIDTH, self.GRID_HALF_WIDTH, self.GRID_POINTS)
        density = np.exp(-0.5 * grid ** 2) / math.sqrt(2.0 * math.pi) * (grid[1] - grid[0])
        thresholds = np.array([NormalDist().inv_cdf(1.0 - v) for v in p])
        scale = math.sqrt(1.0 - self.rho)
        arg = (math.sqrt(self.rho) * grid[:, None] - thresholds[None, :]) / scale
        p_yes = 0.5 * (1.0 + np.array([[math.erf(a / math.sqrt(2.0)) for a in line] for line in arg]))
        weights = np.empty(yes.shape[0])
        for w in range(yes.shape[0]):
            product = np.where(yes[w][None, :] == 1.0, p_yes, 1.0 - p_yes).prod(axis=1)
            weights[w] = float((density * product).sum())
        return weights


class KalshiBinaryKellyMIO(ScenarioUtilitySolve):
    """Size one decision instant's Kalshi binary-contract orders with a fractional-Kelly MILP (role ``capital``).

    Inputs: ``asof_ms`` (the instant), ``cash`` (dollars), ``held`` (``name -> contracts`` already owned in
    markets still open), ``quotes`` (``ticker -> BinaryQuote`` of every open market; the newest state of each,
    which is where each market's probability is taken and then held fixed) and ``candidates`` (the names
    that may be bought now). Outputs are the doorway's: ``target`` (contracts held after), ``trades``
    (``name -> {"buy": n, "sell": 0}``), ``cash_after`` and ``metrics``.

    Scenarios are the joint settlements of every market in the program (up to ``2**markets`` states), weighted
    by :class:`LatentCopula` from the markets' fair values. The fee in the program is the per-contract fee of
    an order of ``max_contracts_per_order``, from the venue mechanic in ``fee_model``; the book pays the
    exact rounded fee afterwards. Domain rows: at most one side per contract, exposure per market at most
    ``market_cap_frac`` of the wealth mark (or what is already held, if more: never buy past the cap),
    ``max_contracts_per_order`` per name, no purchase of a non-candidate, no sale.

    Parameters
    ----------
    params : dict
        The doorway's ``solver``, ``solver_options``, ``risk_aversion_gamma`` (2 is half-Kelly), ``n_tangents``,
        ``n_scenarios_max``, ``cvar_alpha``, ``cvar_limit``, ``cardinality``, ``min_ticket``; and
        ``fee_model`` (REQUIRED, a dskit fee spec: mechanic and rounding), ``max_contracts_per_order`` (int >= 1),
        ``market_cap_frac`` (in (0, 1]), ``copula_rho`` (in [0, 1)), ``uncertainty_frac`` (>= 0; the per-name
        deviation is this times the absolute edge, 0 is off) and ``uncertainty_budget`` (the doorway's budget,
        null for every name). Defaults are :data:`DEFAULTS`.

    Examples
    --------
    Half-Kelly on one market::

        node = KalshiBinaryKellyMIO("size", {
            "risk_aversion_gamma": 2.0, "n_tangents": 64, "n_scenarios_max": 4, "cvar_alpha": 0.95,
            "cvar_limit": None, "cardinality": None, "min_ticket": 0.0,
            "fee_model": {"mechanic": "probability_quadratic",
                          "rounding": {"policy": "ceil_to_tick", "tick": 0.01}}})
        out = node.run(None, {"asof_ms": t, "cash": 1000.0, "held": {}, "quotes": {quote.ticker: quote},
                              "candidates": [name_of(quote.ticker, "yes")]})
        # -> out["trades"] is {"<ticker>|yes": {"buy": n, "sell": 0}}
    """

    outputs = ("target", "trades", "cash_after", "metrics")
    _PARAMS = ScenarioUtilitySolve._PARAMS + _MIO_PARAMS_PASSED_DOWN
    #: No domain row here touches scenario wealth, CVaR or the protection variables.
    _WINDOW_PAST_PROTECTION = True
    #: Bookkeeping for :meth:`payoffs` and :meth:`domain_constraints`, set by :meth:`instruments`.
    _tick = None
    _fee_cache = None

    @classmethod
    def validate_params(cls, params):
        """List problems with ``params``, empty when none: the doorway's, then this kind's.

        Parameters
        ----------
        params : dict
            The node's declared params.

        Returns
        -------
        list of str
            One problem per unknown, missing or unusable knob.
        """
        problems = super().validate_params(params)
        if "fee_model" not in params:
            problems.append("fee_model is required: the venue's fee spec (mechanic and rounding), no default")
        else:
            problems += [f"fee_model: {p}" for p in fee_spec_problems(params["fee_model"])]
        check_int_param(problems, "max_contracts_per_order",
                        params.get("max_contracts_per_order", DEFAULTS["max_contracts_per_order"]), ge=1)
        cap = params.get("market_cap_frac", DEFAULTS["market_cap_frac"])
        if not (number_ok(cap) and 0.0 < cap <= 1.0):
            problems.append(f"market_cap_frac must be a number in (0, 1], got {cap!r}")
        rho = params.get("copula_rho", DEFAULTS["copula_rho"])
        if not (number_ok(rho) and 0.0 <= rho < 1.0):
            problems.append(f"copula_rho must be a number in [0, 1), got {rho!r}")
        frac = params.get("uncertainty_frac", DEFAULTS["uncertainty_frac"])
        if not (number_ok(frac) and frac >= 0.0):
            problems.append(f"uncertainty_frac must be a number >= 0, got {frac!r}")
        budget = params.get("uncertainty_budget", DEFAULTS["uncertainty_budget"])
        if budget is not None and not (number_ok(budget) and budget > 0.0):
            problems.append(f"uncertainty_budget must be a number > 0, or null for every name, got {budget!r}")
        return problems

    def _fee(self):
        """Return the venue's fee model, built once from ``fee_model``."""
        if self._fee_cache is None:
            self._fee_cache = fee_model_from_spec(self.params["fee_model"])
        return self._fee_cache

    def unit_fee(self, quote, side):
        """Return the per-contract fee the program charges: that of an order of ``max_contracts_per_order``.

        Parameters
        ----------
        quote : BinaryQuote
            The market's state.
        side : str
            ``"yes"`` or ``"no"``.

        Returns
        -------
        float
            Dollars per contract; the rounding of one order is spread over its contracts.
        """
        count = knob(self.params, "max_contracts_per_order")
        return self._fee().order_fee(count, quote.price(side), quote.fee_rate) / count

    def run(self, ctx, inputs):
        """Size one instant; the tick bookkeeping lives only for the call.

        Parameters
        ----------
        ctx : NodeContext
            Passed to the doorway.
        inputs : dict
            ``asof_ms``, ``cash``, ``held``, ``quotes`` and ``candidates``.

        Returns
        -------
        dict
            The doorway's ``target``, ``trades``, ``cash_after`` and ``metrics``.
        """
        try:
            return super().run(ctx, inputs)
        finally:
            self._tick = None

    def _program_names(self, inputs):
        """Return the sorted names in the program, refusing a name no quote backs or a bad held count."""
        names = sorted(set(inputs["candidates"]) | set(inputs["held"]))
        for name in names:
            ticker, _side = split_name(name)
            if ticker not in inputs["quotes"]:
                raise ValueError(f"{self.key}: {name!r} has no quote at this instant")
        for name, count in inputs["held"].items():
            if not (isinstance(count, int) and count >= 1):
                raise ValueError(f"{self.key}: held[{name!r}] must be an int >= 1, got {count!r}")
        return names

    def instruments(self, inputs):
        """Return ``(names, rows, account)`` for the doorway: the candidates and what is held, priced.

        Parameters
        ----------
        inputs : dict
            See the class docstring.

        Returns
        -------
        tuple
            The names, ``name -> row``, and the account with a wealth envelope that brackets every state.
        """
        names = self._program_names(inputs)
        quotes, held, candidates = inputs["quotes"], inputs["held"], set(inputs["candidates"])
        cash = float(inputs["cash"])
        sides = {name: split_name(name) for name in names}
        price = {n: quotes[sides[n][0]].price(sides[n][1]) for n in names}
        wealth = cash + sum(price[n] * held.get(n, 0) for n in names)
        held_x = {}
        for n in names:
            held_x[sides[n][0]] = held_x.get(sides[n][0], 0.0) + price[n] * held.get(n, 0)
        cap = {t: max(knob(self.params, "market_cap_frac") * wealth, x) for t, x in held_x.items()}
        unit = {n: self.unit_fee(quotes[sides[n][0]], sides[n][1]) for n in names}
        rows = {n: {"price": price[n], "held": int(held.get(n, 0)), "cost_buy": unit[n], "cost_sell": 0.0,
                    "x_max": cap[sides[n][0]] if n in candidates else price[n] * held.get(n, 0)} for n in names}
        self._tick = {"names": names, "sides": sides, "cap": cap, "candidates": candidates, "quotes": quotes,
                      "markets": sorted(cap), "unit": unit}
        return names, rows, self._account(rows, cash, wealth)

    def _account(self, rows, cash, wealth):
        """Return the doorway's account: cash terms and a wealth envelope that no feasible state leaves."""
        tick = self._tick
        room = {n: self._buy_room(n, rows) for n in tick["names"]}
        spend, payout = {}, {}
        for n in tick["names"]:
            ticker = tick["sides"][n][0]
            spend[ticker] = max(spend.get(ticker, 0.0), room[n] * (rows[n]["price"] + rows[n]["cost_buy"]))
            payout[ticker] = max(payout.get(ticker, 0.0), rows[n]["held"] + room[n])
        low = max(cash - sum(spend.values()), _ENVELOPE_FLOOR_FRAC * wealth)
        high = cash + sum(payout.values())
        return {"cash": cash, "buying_power": cash, "sale_credit": 0.0, "cash_reserve": 0.0, "gross_limit": None,
                "wealth_lo": min(low, wealth * (1.0 - _ENVELOPE_PAD_FRAC)),
                "wealth_hi": max(high, wealth * (1.0 + _ENVELOPE_PAD_FRAC))}

    def _buy_room(self, name, rows):
        """Return the most contracts of ``name`` that can be bought now: the order cap and the exposure headroom."""
        if name not in self._tick["candidates"]:
            return 0
        ticker = self._tick["sides"][name][0]
        taken = sum(rows[n]["price"] * rows[n]["held"] for n in self._tick["names"] if self._tick["sides"][n][0] == ticker)
        headroom = max(self._tick["cap"][ticker] - taken, 0.0)
        return min(knob(self.params, "max_contracts_per_order"), int(headroom / rows[name]["price"]))

    def payoffs(self, inputs):
        """Return the joint-settlement weights and each name's scenario gross return.

        Parameters
        ----------
        inputs : dict
            See the class docstring.

        Returns
        -------
        tuple
            ``(weights, r)``; a name's return is ``payoff / price - 1`` in each state.
        """
        import numpy as np

        tick = self._tick
        markets = tick["markets"]
        copula = LatentCopula(knob(self.params, "copula_rho"))
        weights = copula.weights([tick["quotes"][t].fair for t in markets])
        yes = copula.outcomes(len(markets))
        column = {t: yes[:, i] for i, t in enumerate(markets)}
        r = {}
        for n in tick["names"]:
            ticker, side = tick["sides"][n]
            pays = column[ticker] if side == "yes" else 1.0 - column[ticker]
            r[n] = np.asarray(pays / tick["quotes"][ticker].price(side) - 1.0)
        return weights, r

    def _edge(self, name):
        """Return the program's per-contract edge of a name: win probability less price less the unit fee."""
        ticker, side = self._tick["sides"][name]
        quote = self._tick["quotes"][ticker]
        return quote.win_prob(side) - quote.price(side) - self._tick["unit"][name]

    def mean_uncertainty(self, inputs):
        """Return the budgeted confidence haircut, or None when ``uncertainty_frac`` is 0.

        Parameters
        ----------
        inputs : dict
            See the class docstring.

        Returns
        -------
        dict or None
            ``budget`` and per-name ``deviation_below`` / ``deviation_above``: ``uncertainty_frac * |edge|`` dollars
            per contract, as a return on the price.
        """
        frac = knob(self.params, "uncertainty_frac")
        if frac == 0.0:
            return None
        names = self._tick["names"]
        deviation = {n: [frac * abs(self._edge(n)) / self._tick["quotes"][self._tick["sides"][n][0]].price(
            self._tick["sides"][n][1])] for n in names}
        budget = knob(self.params, "uncertainty_budget")
        return {"budget": float(len(names)) if budget is None else float(budget),
                "deviation_below": deviation, "deviation_above": {n: list(v) for n, v in deviation.items()}}

    def domain_constraints(self, model, inputs, params):
        """Add the venue's rows: one side per contract, the exposure cap, the order cap, no sale, no off-list buy.

        Parameters
        ----------
        model : pyomo.environ.ConcreteModel
            The doorway's model, extended in place.
        inputs : dict
            Unused.
        params : dict
            Unused.
        """
        from pyomo.environ import Constraint

        tick = self._tick
        names, cap = tick["names"], tick["cap"]
        by_market = {t: [n for n in names if tick["sides"][n][0] == t] for t in tick["markets"]}
        model.market_cap = Constraint(tick["markets"], rule=lambda m, t: sum(m.x[n] for n in by_market[t]) <= cap[t])
        both = [t for t, members in by_market.items() if len(members) == 2]
        if both:
            model.one_side = Constraint(both, rule=lambda m, t: sum(m.y[n] for n in by_market[t]) <= 1)
        model.order_cap = Constraint(names, rule=lambda m, n: m.b[n] <= knob(self.params, "max_contracts_per_order"))
        model.no_sale = Constraint(names, rule=lambda m, n: m.s[n] == 0)
        refused = [n for n in names if n not in tick["candidates"]]
        if refused:
            model.no_new_buy = Constraint(refused, rule=lambda m, n: m.b[n] == 0)


class LabelLedger:
    """The settled outcomes of the markets, revealed only at or after each market's close.

    Parameters
    ----------
    rows : list of dict
        Rows with ``ticker``, ``close_ms`` and ``label`` (1 when YES settled); every row of a ticker agrees.

    Examples
    --------
    A label is not available until the close::

        ledger = LabelLedger([{"ticker": "T", "close_ms": 1000, "label": 1}])
        ledger.reveal("T", 1000)   # -> 1
        ledger.reveal("T", 999)    # raises LookAheadError
    """

    def __init__(self, rows):
        self._labels = {}
        for row in rows:
            known = self._labels.setdefault(row[f.TICKER], (row[f.CLOSE_MS], row[f.LABEL]))
            if known != (row[f.CLOSE_MS], row[f.LABEL]):
                raise ValueError(f"{row[f.TICKER]!r} carries two different (close_ms, label): {known!r}, "
                                 f"{(row[f.CLOSE_MS], row[f.LABEL])!r}")

    def reveal(self, ticker, asof_ms):
        """Return the label of ``ticker`` as known at ``asof_ms``.

        Parameters
        ----------
        ticker : str
            The market.
        asof_ms : int
            The instant asking.

        Returns
        -------
        int
            1 when YES settled, 0 when NO did.

        Raises
        ------
        LookAheadError
            When ``asof_ms`` is before the market's close.
        ValueError
            When the market has no usable label.
        """
        close_ms, label = self._labels[ticker]
        if asof_ms < close_ms:
            raise LookAheadError(f"{ticker!r}: label asked at {asof_ms} before its close at {close_ms}")
        if label not in (0, 1):
            raise ValueError(f"{ticker!r} has no 0/1 label, got {label!r}")
        return int(label)


class SettlementBook:
    """Cash and positions of one strategy: exact fees at fill, payouts at settlement.

    A position is keyed by instrument name and held to the close. Money is conserved: cash falls by
    ``contracts * price + fee`` on a fill and rises by ``contracts * payoff`` on settlement, nothing else.

    Parameters
    ----------
    cash : float
        Starting dollars.
    fee_model : dskit.pipeline.fee_mechanics.FeeModel
        The venue's fee mechanic; the order fee it returns is charged as is.

    Examples
    --------
    Buy 10 YES at 0.40 and let it settle YES::

        book = SettlementBook(1000.0, fee_model_from_spec(spec))
        book.buy(quote, "yes", 10)
        book.settle(quote.close_ms, ledger)
        # -> book.cash is 1000 - 10 * 0.40 - fee + 10
    """

    def __init__(self, cash, fee_model):
        self.cash = float(cash)
        self.fee_model = fee_model
        self.positions = {}
        self.fills = []

    def held(self):
        """Return ``name -> contracts`` of every open position."""
        return {name: pos["contracts"] for name, pos in self.positions.items()}

    def open_cost(self):
        """Return the dollars paid for open contracts, fees excluded."""
        return sum(pos["cost"] for pos in self.positions.values())

    def equity(self):
        """Return cash plus the cost of open contracts: the COST-BASIS wealth (fees are already spent)."""
        return self.cash + self.open_cost()

    def worst_equity(self):
        """Return the wealth if every open contract paid nothing: the cash."""
        return self.cash

    def mtm_equity(self, quotes):
        """Return cash plus every open contract at the mid of its market's newest quote.

        Parameters
        ----------
        quotes : dict
            ``ticker -> BinaryQuote``, the newest state at or before the instant being valued.

        Returns
        -------
        float
            The mark-to-market wealth.

        Raises
        ------
        ValueError
            When an open position's market has no quote.
        """
        total = self.cash
        for name, position in self.positions.items():
            if position["ticker"] not in quotes:
                raise ValueError(f"no quote to mark {name!r}")
            total += position["contracts"] * quotes[position["ticker"]].mark(position["side"])
        return total

    def holds_opposite(self, ticker, side):
        """Say whether the book holds the other side of ``ticker``."""
        return name_of(ticker, "no" if side == "yes" else "yes") in self.positions

    def order_cost(self, quote, side, contracts):
        """Return ``(contracts * price, fee)`` of an order, the fee exact and rounded by the mechanic."""
        price = quote.price(side)
        return contracts * price, self.fee_model.order_fee(contracts, price, quote.fee_rate)

    def buy(self, quote, side, contracts):
        """Fill an order at the touch and return the fill record.

        Parameters
        ----------
        quote : BinaryQuote
            The market's state at the fill.
        side : str
            ``"yes"`` or ``"no"``.
        contracts : int
            At least 1.

        Returns
        -------
        dict
            The fill: instant, ticker, side, lead, contracts, price, fee, cost, fair, close.

        Raises
        ------
        ValueError
            When the order costs more than the cash.
        """
        cost, fee = self.order_cost(quote, side, contracts)
        if cost + fee > self.cash + 1e-9:
            raise ValueError(f"order of {contracts} {quote.ticker}|{side} costs {cost + fee} but cash is {self.cash}")
        name = name_of(quote.ticker, side)
        fill = {"decision_ms": quote.decision_ms, "ticker": quote.ticker, "series": quote.series, "side": side,
                "lead_minutes": quote.lead_minutes, "close_ms": quote.close_ms, "contracts": contracts,
                "price": quote.price(side), "fee": fee, "cost": cost, "fair": quote.win_prob(side)}
        self.cash -= cost + fee
        position = self.positions.setdefault(name, {"contracts": 0, "cost": 0.0, "close_ms": quote.close_ms,
                                                    "ticker": quote.ticker, "side": side, "fills": []})
        position["contracts"] += contracts
        position["cost"] += cost
        position["fills"].append(fill)
        self.fills.append(fill)
        return fill

    def settle(self, asof_ms, ledger):
        """Settle every position whose market has closed by ``asof_ms``; return the settled fills.

        Parameters
        ----------
        asof_ms : int
            The instant; positions with ``close_ms <= asof_ms`` pay out.
        ledger : LabelLedger
            Where the outcomes are revealed.

        Returns
        -------
        list of dict
            The fills of the settled positions, each with ``payoff`` (0 or 1 per contract) and ``pnl`` added.
        """
        settled = []
        for name in [n for n, pos in self.positions.items() if pos["close_ms"] <= asof_ms]:
            position = self.positions.pop(name)
            label = ledger.reveal(position["ticker"], asof_ms)
            payoff = label if position["side"] == "yes" else 1 - label
            self.cash += position["contracts"] * payoff
            for fill in position["fills"]:
                fill["payoff"] = payoff
                fill["pnl"] = fill["contracts"] * payoff - fill["cost"] - fill["fee"]
                settled.append(fill)
        return settled


class Performance:
    """The numbers of one strategy's run: wealth, growth, drawdown, orders, P&L by lead and the per-day t.

    Parameters
    ----------
    bankroll : float
        The starting wealth.
    fills : list of dict
        The strategy's settled fills (``pnl`` and ``close_ms`` set).
    curves : dict
        ``cost`` (cash plus the cost of open contracts), ``worst`` (open contracts at 0) and ``mtm`` (open
        contracts at the mid of the newest quote), each a list of ``(asof_ms, equity)`` points in time order,
        the first being the start and the last the settled end.
    days : range
        The UTC day indices to count P&L over; the same for every strategy being compared.

    Examples
    --------
    Summarise a strategy that grew 1000 to 1100::

        path = [(0, 1000.0), (60_000, 1100.0)]
        Performance(1000.0, fills, {"cost": path, "worst": path, "mtm": path}, range(0, 1)).summary()
        # -> {"final_wealth": 1100.0, "log_growth": 0.0953, ...}
    """

    def __init__(self, bankroll, fills, curves, days):
        self.bankroll = bankroll
        self.fills = fills
        self.curves = curves
        self.path = curves["cost"]
        self.days = days

    @staticmethod
    def _drawdown(path):
        """Return ``(fraction, dollars)`` of the largest peak-to-trough fall of an equity path."""
        peak, frac, usd = path[0][1], 0.0, 0.0
        for _, value in path:
            peak = max(peak, value)
            frac, usd = max(frac, (peak - value) / peak), max(usd, peak - value)
        return frac, usd

    def _equity_at(self, instant):
        """Return the equity of the last path point at or before ``instant`` (the start if none)."""
        index = bisect.bisect_right([t for t, _ in self.path], instant) - 1
        return self.path[max(index, 0)][1]

    def _day_stats(self):
        """Return the per-day dollar P&L and per-day return t-statistics."""
        pnl = {d: 0.0 for d in self.days}
        for fill in self.fills:
            pnl[fill["close_ms"] // DAY_MS] += fill["pnl"]
        dollars = [pnl[d] for d in self.days]
        returns = [pnl[d] / self._equity_at(d * DAY_MS) for d in self.days]
        out = {}
        for key, series in (("day_pnl", dollars), ("day_return", returns)):
            test = across_fold_t(series) if len(series) >= 2 else {"n": len(series), "mean": None, "t": None}
            out[key] = {"n": test["n"], "mean": test["mean"], "t": test["t"]}
        return out

    def _by_lead(self):
        """Return ``lead -> {orders, contracts, pnl}``."""
        out = {}
        for fill in self.fills:
            cell = out.setdefault(str(fill["lead_minutes"]), {"orders": 0, "contracts": 0, "pnl": 0.0})
            cell["orders"] += 1
            cell["contracts"] += fill["contracts"]
            cell["pnl"] += fill["pnl"]
        return dict(sorted(out.items(), key=lambda item: int(item[0])))

    def summary(self):
        """Return the strategy's numbers as plain JSON values.

        Returns
        -------
        dict
            ``final_wealth``, ``log_growth``, and the largest peak-to-trough fall as a fraction of the peak
            (``max_drawdown_cost``, ``max_drawdown_mtm``, ``max_drawdown_worst``) and in dollars (the same names
            ending ``_usd``): cost basis, mark to the mid of the newest quote, and every open contract at 0.
            ``orders``, ``contracts``, ``fees``, ``pnl``, ``pnl_by_lead`` and ``day_pnl`` / ``day_return``
            (``n``, ``mean``, ``t``).
        """
        final = self.path[-1][1]
        falls = {}
        for key, path in self.curves.items():
            falls[f"max_drawdown_{key}"], falls[f"max_drawdown_{key}_usd"] = self._drawdown(path)
        return {"final_wealth": final, "log_growth": math.log(final / self.bankroll), **falls, "orders": len(self.fills), "contracts": sum(x["contracts"] for x in self.fills),
                "fees": sum(x["fee"] for x in self.fills), "pnl": sum(x["pnl"] for x in self.fills),
                "pnl_by_lead": self._by_lead(), **self._day_stats()}


class KalshiKellyBacktest(ListPortsNode):
    """Loop decision instants in time order, sizing with half-Kelly beside the one-contract rule (role ``transform``).

    Input ``records``: decision rows with the quote, the fee columns, the label and ``<fair_field>`` with
    ``<fair_field>_status``; only rows whose status is ``ok`` are used. At each instant, in order: (1) every
    position whose market has closed by then settles; (2) the instant's rows refresh each market's newest
    :class:`BinaryQuote` (no label); (3) :class:`ExecutableEdge` picks the candidates; (4) the flat book buys one
    contract of each candidate, skipping a side opposite to one it already holds (as Kelly never holds both)
    and an order its cash cannot pay, each skip counted; (5) :class:`KalshiBinaryKellyMIO` sizes the candidates against the Kelly book's
    cash and holdings, and the orders fill at the touch with the exact fee. After the last instant everything
    settles. Wealth is reported three ways: cash plus the cost of open contracts (``*_wealth``, fees spent),
    cash alone (``*_worst``, every open contract pays nothing) and cash plus open contracts at the mid of the
    newest quote at or before the instant (``*_mtm``). Drawdowns come from all three, sampled at every instant;
    the wealth records are written at the instants that settled or traded.

    Outputs: ``trades`` (every fill of both strategies, with ``strategy`` and, once settled, ``payoff`` and
    ``pnl``), ``wealth`` (one record per change: ``kelly_*`` and ``flat_*``) and ``summary`` (a
    :class:`Performance` summary per strategy, counts, and a note). The summary is also written to the
    node's artifact directory as ``kelly_summary.json``.

    Parameters
    ----------
    params : dict
        :class:`KalshiBinaryKellyMIO`'s knobs (``fee_model`` and the doorway's owner knobs REQUIRED), plus
        ``fair_field`` (REQUIRED, the column of P(YES)), ``starting_bankroll`` (> 0), ``lead_min_minutes``
        (int >= 0) and ``min_edge`` (>= 0). Defaults are :data:`DEFAULTS`.

    Examples
    --------
    Half-Kelly from a 1000-dollar bankroll on the blend, leads of 8 minutes or more::

        node = KalshiKellyBacktest("kelly", {
            "fair_field": "fair_blend", "starting_bankroll": 1000.0, "lead_min_minutes": 8, "min_edge": 0.02,
            "risk_aversion_gamma": 2.0, "n_tangents": 64, "n_scenarios_max": 4, "cvar_alpha": 0.95,
            "cvar_limit": None, "cardinality": None, "min_ticket": 0.0,
            "fee_model": {"mechanic": "probability_quadratic",
                          "rounding": {"policy": "ceil_to_tick", "tick": 0.01}}})
        out = node.run(ctx, {"records": rows})
        # -> out["summary"]["kelly"]["final_wealth"], out["summary"]["flat"]["final_wealth"]
    """

    role = "transform"
    outputs = ("trades", "wealth", "summary")
    _SOLVER = KalshiBinaryKellyMIO
    _DRIVER_PARAMS = ("fair_field", "starting_bankroll", "lead_min_minutes", "min_edge")
    _PARAMS = KalshiBinaryKellyMIO._PARAMS + _DRIVER_PARAMS
    SUMMARY_ARTIFACT = "kelly_summary.json"

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
            The solver's problems on its knobs, then this node's.
        """
        problems = []
        reject_unknown_params(problems, params, cls._PARAMS)
        problems += cls._SOLVER.validate_params({k: v for k, v in params.items() if k in cls._SOLVER._PARAMS})
        if not (isinstance(params.get("fair_field"), str) and params["fair_field"]):
            problems.append(f"fair_field is required: the column of P(YES), got {params.get('fair_field')!r}")
        bankroll = params.get("starting_bankroll", DEFAULTS["starting_bankroll"])
        if not (number_ok(bankroll) and bankroll > 0.0):
            problems.append(f"starting_bankroll must be a number > 0, got {bankroll!r}")
        check_int_param(problems, "lead_min_minutes", params.get("lead_min_minutes", DEFAULTS["lead_min_minutes"]), ge=0)
        edge = params.get("min_edge", DEFAULTS["min_edge"])
        if not (number_ok(edge) and edge >= 0.0):
            problems.append(f"min_edge must be a number >= 0, got {edge!r}")
        return problems

    @classmethod
    def serving_effect(cls, params, verified_run_evidence):
        """Answer ``pure``: a function of the rows and the params.

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

    def _solver(self):
        """Build the per-instant solver from this node's own params."""
        return self._SOLVER(f"{self.key}_size", {k: v for k, v in self.params.items() if k in self._SOLVER._PARAMS})

    @staticmethod
    def _ticks(quotes):
        """Group quotes by decision instant, ascending, each group ordered by ticker."""
        grouped = {}
        for quote in quotes:
            grouped.setdefault(quote.decision_ms, []).append(quote)
        return [(t, sorted(grouped[t], key=lambda q: q.ticker)) for t in sorted(grouped)]

    def _usable(self, rows):
        """Return the quotes of the rows whose fair value is out of sample and tradeable, and those rows."""
        status = self.params["fair_field"] + REFIT_STATUS_SUFFIX
        kept = [row for row in rows if row.get(status) == "ok"]
        quotes = [BinaryQuote.from_row(row, self.params["fair_field"]) for row in kept]
        return [q for q in quotes if q is not None], kept

    def _size(self, solver, ctx, book, t, quotes, names):
        """Solve one instant against the Kelly book and return ``name -> contracts to buy``."""
        out = solver.run(ctx, {"asof_ms": t, "cash": book.cash, "held": book.held(), "quotes": quotes,
                               "candidates": names})
        return {name: trade["buy"] for name, trade in out["trades"].items() if trade["buy"] > 0}

    def _fill(self, book, quote, side, contracts):
        """Fill ``contracts`` at the touch, trimmed to what the cash pays for with the exact fee; return the fill or None."""
        while contracts > 0 and sum(book.order_cost(quote, side, contracts)) > book.cash:
            contracts -= 1
        return book.buy(quote, side, contracts) if contracts > 0 else None

    def run(self, ctx, inputs):
        """Run both strategies over the rows and return the ledger, the wealth path and the summary.

        Parameters
        ----------
        ctx : NodeContext
            Gives the artifact directory.
        inputs : dict
            ``records``: the decision rows.

        Returns
        -------
        dict
            ``trades``, ``wealth`` and ``summary``.
        """
        quotes_all, kept = self._usable(inputs["records"])
        ledger = LabelLedger(kept)
        fee_model = fee_model_from_spec(self.params["fee_model"])
        bankroll = float(knob(self.params, "starting_bankroll"))
        kelly, flat = SettlementBook(bankroll, fee_model), SettlementBook(bankroll, fee_model)
        gate = ExecutableEdge(knob(self.params, "lead_min_minutes"), knob(self.params, "min_edge"))
        solver, newest = self._solver(), {}
        wealth, samples = [], []
        counts = {"ticks": 0, "candidate_ticks": 0, "candidates": 0, "solves": 0, "trimmed": 0,
                  "flat_opposite_skipped": 0, "flat_cash_skipped": 0}
        ticks = self._ticks(quotes_all)
        if not ticks:
            raise ValueError(f"{self.key}: no usable row (status ok, two-sided, priced) to simulate")
        wealth.append(self._point(ticks[0][0], kelly, flat, {}))
        samples.append(wealth[0])
        for t, quotes in ticks:
            counts["ticks"] += 1
            changed = bool(kelly.settle(t, ledger) + flat.settle(t, ledger))
            newest = {k: q for k, q in newest.items() if q.close_ms > t}
            newest.update({q.ticker: q for q in quotes})
            picks = [(q, s) for q in quotes for s in gate.sides(q)]
            for quote, side in picks:
                self._flat_buy(flat, quote, side, counts)
            if picks:
                counts["candidate_ticks"] += 1
                counts["candidates"] += len(picks)
                counts["solves"] += 1
                sizes = self._size(solver, ctx, kelly, t, newest, [name_of(q.ticker, s) for q, s in picks])
                for name, contracts in sizes.items():
                    ticker, side = split_name(name)
                    changed = True
                    if self._fill(kelly, newest[ticker], side, contracts) is None:
                        counts["trimmed"] += 1
                if counts["solves"] % 500 == 0:
                    self.log.info("sized %d instants, Kelly equity %.2f", counts["solves"], kelly.equity())
            samples.append(self._point(t, kelly, flat, newest))
            if changed or picks:
                wealth.append(samples[-1])
        last = max(q.close_ms for q in quotes_all)
        kelly.settle(last, ledger)
        flat.settle(last, ledger)
        wealth.append(self._point(last, kelly, flat, {}))
        samples.append(wealth[-1])
        return self._outputs(ctx, kelly, flat, (wealth, samples), counts, quotes_all)

    @staticmethod
    def _flat_buy(flat, quote, side, counts):
        """Buy one contract for the flat rule unless it would hold both sides or the cash is short; count each skip."""
        if flat.holds_opposite(quote.ticker, side):
            counts["flat_opposite_skipped"] += 1
        elif sum(flat.order_cost(quote, side, 1)) > flat.cash:
            counts["flat_cash_skipped"] += 1
        else:
            flat.buy(quote, side, 1)

    @staticmethod
    def _point(t, kelly, flat, newest):
        """Return one wealth-path record: cost-basis, worst-case (open at 0) and mark-to-market wealth of both books."""
        record = {"asof_ms": t}
        for name, book in (("kelly", kelly), ("flat", flat)):
            record.update({f"{name}_wealth": book.equity(), f"{name}_worst": book.worst_equity(),
                           f"{name}_mtm": book.mtm_equity(newest), f"{name}_cash": book.cash,
                           f"{name}_open_cost": book.open_cost()})
        return record

    def _outputs(self, ctx, kelly, flat, paths, counts, quotes):
        """Assemble the node's three outputs and write the summary artifact."""
        wealth, samples = paths
        bankroll = float(knob(self.params, "starting_bankroll"))
        days = range(min(q.close_ms for q in quotes) // DAY_MS, max(q.close_ms for q in quotes) // DAY_MS + 1)
        summary = {
            name: Performance(bankroll, book.fills, {key: [(w["asof_ms"], w[f"{name}_{col}"]) for w in samples]
                                                     for key, col in (("cost", "wealth"), ("worst", "worst"), ("mtm", "mtm"))},
                              days).summary()
            for name, book in (("kelly", kelly), ("flat", flat))
        }
        summary["counts"] = {**counts, "days": len(days)}
        summary["gamma"] = self.params["risk_aversion_gamma"]
        trades = sorted(
            [{"strategy": name, **fill} for name, book in (("kelly", kelly), ("flat", flat)) for fill in book.fills],
            key=lambda x: (x["decision_ms"], x["strategy"], x["ticker"], x["side"]))
        self.write_artifact(ctx, self.SUMMARY_ARTIFACT, summary)
        return {"trades": trades, "wealth": wealth, "summary": summary}
