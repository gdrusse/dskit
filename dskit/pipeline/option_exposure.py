"""Option Greeks aggregation, stock-equivalent attribution and exposure (ADR-0310).

Venue-neutral diagnostics over books that are held to expiry: nothing here is
accounting, so no P&L, mark, reserve or size is computed from a Greek.

* :class:`ParityForward` — the chain-implied forward from put-call parity,
  falling back to the spot (source ``"spot"``) when no usable pair exists.
* :class:`LegGreeks` — one leg's delta, gamma, vega and theta in SPOT terms
  (the chain rule ``delta_spot = delta_F * F/S``, ``gamma_spot = gamma_F *
  (F/S)^2``) and position units (sign x quantity x multiplier).
* :class:`BookGreeks` — a frozen row summing a book's legs.
* :class:`StockEquivalent` — splits a book's P&L into the delta-equivalent
  static-delta part and a ``beyond_static_delta`` part that holds everything else.
* :class:`StaticDeltaHedge` — the book's entry delta hedged once with stock, with
  costs, short borrow and dividends: the P&L left once the delta is gone. Its
  :meth:`~StaticDeltaHedge.carry_costs` is the one cost rule of every hedge.
* :class:`ExpiryDelta` — the payoff-delta split's delta (description only).
* :class:`ForwardHedge` — the forward hedge sized at the decision, struck at the
  fill forward, settled at the settlement close (fee and half-spread, no borrow).
* :class:`HardToBorrowProxy` — the volatility/jump proxy for a short hedge that
  cannot be borrowed; :class:`Concentration` — top shares and leave-one-out signs.
* :class:`CellAttribution` — sums and shares of those parts per cell, with a
  gross share that cannot explode and a net share that is ``None`` near zero.
* :class:`ExposureSnapshot` — beta-weighted dollar delta, vega and dollar
  gamma of open books by remaining-DTE bucket, plus the top-k concentration.
* :func:`fit_betas` — each name's beta on the equal-weight basket of the names.

Exercise is European and Black-76; early exercise and discrete dividends are
not modelled. Every refusal is a stable string code (a class attribute of its
owner); a refused leg or book is never silently dropped or zeroed. Import
cost: stdlib only.
"""

import math
from abc import ABC, abstractmethod
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from statistics import median, stdev

from dskit.pipeline.base import ConfigError
from dskit.pipeline.node import Node, check_int_param, check_number_param, reject_unknown_params
from dskit.pipeline.option_pricing import (
    RIGHTS,
    black76_delta,
    black76_gamma,
    black76_implied_vol,
    black76_theta,
    black76_vega,
)
from dskit.pipeline.option_structures import structure_max_loss
from dskit.pipeline.records import number_ok, price_ok
from dskit.pipeline.stats import sample_quantile

__all__ = ["BookGreeks", "BookGreeksReport", "CellAttribution", "Concentration", "ExpiryDelta",
           "ExposureSnapshot", "ForwardHedge", "HardToBorrowProxy", "LegGreeks", "ParityForward",
           "StaticDeltaHedge", "StockEquivalent", "fit_betas", "DirectionEdge"]

#: Calendar days per year: the one divisor for years-to-expiry and per-day theta.
DAYS_PER_YEAR = 365
#: Where a leg's volatility comes from.
IV_SOURCES = ("vendor", "mid")
#: The default volatility source (vendor ``iv``); named once.
DEFAULT_IV_SOURCE = IV_SOURCES[0]
#: The forward sources a chain gives.
FORWARD_SOURCES = ("parity", "spot")
_INF = float("inf")
#: Trading days per year: annualizes the hard-to-borrow proxy's daily volatility.
TRADING_DAYS_PER_YEAR = 252
#: Per-book forward-hedge outcomes (ADR-0310 A2.3).
HEDGE_OK_FORWARD, HEDGE_UNBOUNDED, HEDGE_HTB, DECISION_UNMARKED = (
    "ok", "hedge_unbounded_above", "hedge_hard_to_borrow", "decision_unmarked")
FORWARD_HEDGE_STATUSES = (HEDGE_OK_FORWARD, HEDGE_UNBOUNDED, HEDGE_HTB, DECISION_UNMARKED,
                          "not_attributed")
#: Where the hedge cost rates come from (ADR-0310 A2.3): a recorded placeholder or measured.
HEDGE_COST_SOURCES = ("placeholder", "measured")
#: The recorded placeholder triple, in ``StaticDeltaHedge._PARAMS`` order (fee, bps, borrow).
PLACEHOLDER_HEDGE_RATES = (0.005, 5.0, 0.005)
#: Carried on every cell row and direction read (ADR-0310 A2.4).
DESCRIPTIVE_ONLY_NOTE = (
    "descriptive only: development quarters, books shared across formulations, no inference")
#: Carried in the summary beside the A1 stock columns (ADR-0310 A2.3).
STOCK_HEDGE_NOTE = "diagnostic: stock hedge at S_fill, not the ADR-0312 instrument"
HEDGE_OK, DIVIDENDS_ABSENT, DIVIDEND_MISSING, NOT_ATTRIBUTED = (
    "ok", "dividends_absent", "dividend_missing", "not_attributed")
_ONE_PERCENT = 0.01


class _Configured(ABC):
    """A class whose knobs are its ``_PARAMS`` tuple, validated by one hook."""

    _PARAMS = ()

    def __init__(self, params=None):
        params = {} if params is None else params
        problems = self.validate_params(params)
        if problems:
            raise ConfigError([f"{type(self).__name__}: {p}" for p in problems])
        self.params = dict(params)

    @classmethod
    def validate_params(cls, params):
        """Return every problem a params mapping has (default-deny, all required)."""
        if not isinstance(params, dict):
            return [f"params must be a mapping, got {type(params).__name__}"]
        problems = []
        reject_unknown_params(problems, params, cls._PARAMS)
        cls._check(problems, params)
        return problems

    @classmethod
    @abstractmethod
    def _check(cls, problems, params):
        """Append the value problems of ``params`` (required keys included)."""


def _required(problems, params, names):
    """Append a problem per absent required key; return the names present."""
    missing = [n for n in names if n not in params]
    if missing:
        problems.append(f"missing required param(s) {missing}")
    return [n for n in names if n in params]


def _is_positive(value):
    return price_ok(value)


def _quote_ok(bid, ask):
    """Say whether a bid/ask pair is finite, non-negative and not crossed."""
    return (number_ok(bid) and number_ok(ask) and bid >= 0 and ask > 0 and ask >= bid)


class ParityForward(_Configured):
    """Chain-implied forward from put-call parity, with a flagged spot fallback.

    Per strike with a usable call AND put quote, ``F = K + C_mid - P_mid``
    (rate 0: rate, dividend and borrow are absorbed into F). The forward is
    the median over the ``parity_strikes`` pairs nearest the spot.

    Parameters
    ----------
    params : dict
        ``parity_strikes`` (int >= 1, required) — how many nearest pairs.
        ``min_pairs`` (int >= 1, required) — fewer usable pairs falls back to
        the spot. ``max_pair_spread_frac`` (number > 0, required) — a leg
        whose ``(ask - bid) / mid`` exceeds it makes its strike unusable.

    Examples
    --------
    One expiry's rows, forward 101 recovered from the pair at 100::

        pf = ParityForward({"parity_strikes": 3, "min_pairs": 1,
                            "max_pair_spread_frac": 0.5})
        rows = [{"right": "call", "strike": 100, "bid": 4.9, "ask": 5.1},
                {"right": "put", "strike": 100, "bid": 3.9, "ask": 4.1}]
        pf.from_chain(rows, 100.5)   # -> (101.0, "parity")
    """

    _PARAMS = ("parity_strikes", "min_pairs", "max_pair_spread_frac")
    SOURCE_PARITY, SOURCE_SPOT = FORWARD_SOURCES
    DUPLICATE_QUOTE = "duplicate_quote"

    @classmethod
    def _check(cls, problems, params):
        for name in _required(problems, params, ("parity_strikes", "min_pairs")):
            check_int_param(problems, name, params[name], ge=1)
        if "max_pair_spread_frac" in params and not _is_positive(params["max_pair_spread_frac"]):
            problems.append("max_pair_spread_frac must be a positive finite number, got "
                            f"{params['max_pair_spread_frac']!r}")
        _required(problems, params, ("max_pair_spread_frac",))

    def _mid(self, row):
        """Return a usable leg's mid, or ``None`` (missing, crossed, or too wide)."""
        bid, ask = row.get("bid"), row.get("ask")
        if not _quote_ok(bid, ask):
            return None
        mid = (bid + ask) / 2
        if mid <= 0 or (ask - bid) / mid > self.params["max_pair_spread_frac"]:
            return None
        return mid

    @staticmethod
    def duplicates(rows):
        """Return the sorted ``(right, strike)`` groups quoted by more than one row.

        The one duplicate rule: :meth:`from_chain` and the report's leg lookup
        both refuse a chain that has any, so neither picks a row arbitrarily.
        """
        seen = Counter((r.get("right"), r.get("strike")) for r in rows
                       if r.get("right") in RIGHTS and _is_positive(r.get("strike")))
        return sorted(key for key, n in seen.items() if n > 1)

    def pairs(self, rows, spot, exclude_strikes=()):
        """Return the pair forwards ``K + C_mid - P_mid``, nearest the spot first.

        The one owner of the parity pairs (ADR-0310 A2.11, ADR-0311 A1 part A,
        ADR-0312 A1 marks): :meth:`from_chain` takes their median.

        Parameters
        ----------
        rows : list of dict
            One expiry's quotes (see :meth:`from_chain`), already screened by
            the caller's own quote rule.
        spot : float
            Positive underlying price: the centre the pairs are ranked around.
        exclude_strikes : iterable of float, optional
            Strikes whose pairs are dropped BEFORE the ``parity_strikes``
            nearest are taken (a mark excludes the book's own strikes), so the
            next-nearest pairs take their place.

        Returns
        -------
        list of float
            At most ``parity_strikes`` forwards, nearest strike first (ties to
            the lower strike); empty when no usable pair remains.

        Raises
        ------
        ValueError
            As :meth:`from_chain`: a non-positive ``spot`` or a duplicate quote.
        """
        if not _is_positive(spot):
            raise ValueError(f"spot must be a positive finite number, got {spot!r}")
        duplicated = self.duplicates(rows)
        if duplicated:
            raise ValueError(f"{self.DUPLICATE_QUOTE}: {duplicated}")
        excluded = set(exclude_strikes)
        mids = {}
        for row in rows:
            right, strike = row.get("right"), row.get("strike")
            if right in RIGHTS and _is_positive(strike) and strike not in excluded:
                mid = self._mid(row)
                if mid is not None:
                    mids.setdefault(strike, {})[right] = mid
        ranked = sorted((k for k, legs in mids.items() if len(legs) == len(RIGHTS)),
                        key=lambda k: (abs(k - spot), k))
        return [k + mids[k]["call"] - mids[k]["put"]
                for k in ranked[:self.params["parity_strikes"]]]

    def from_chain(self, rows, spot, exclude_strikes=()):
        """Return ``(forward, source)`` for one expiry's chain rows.

        Parameters
        ----------
        rows : list of dict
            One expiry's quotes: ``right`` (``"call"``/``"put"``), ``strike``,
            ``bid``, ``ask``.
        spot : float
            Positive underlying price: the fallback forward and the centre.
        exclude_strikes : iterable of float, optional
            Strikes whose pairs are left out (see :meth:`pairs`); default none.

        Returns
        -------
        tuple
            ``(forward, "parity")`` or ``(spot, "spot")``.

        Raises
        ------
        ValueError
            When ``spot`` is not a positive finite number, or (starting
            ``duplicate_quote``) when a right and strike is quoted twice.
        """
        forwards = self.pairs(rows, spot, exclude_strikes)
        if len(forwards) < self.params["min_pairs"]:
            return float(spot), self.SOURCE_SPOT
        forward = median(forwards)
        if not _is_positive(forward):
            return float(spot), self.SOURCE_SPOT
        return float(forward), self.SOURCE_PARITY


class LegGreeks(_Configured):
    """One leg's Greeks in spot terms and position units.

    ``delta_sh`` is shares (spot terms), ``delta_f_sh`` shares per dollar of FORWARD
    (``delta_sh = delta_f_sh x F/S``), ``gamma_sh`` shares per dollar of spot,
    ``vega_usd_per_pt`` dollars per volatility point, ``theta_usd_per_day``
    dollars per calendar day with the forward held fixed; each already times
    ``sign x quantity x multiplier``.

    Parameters
    ----------
    params : dict
        ``rate`` (finite number, required; 0.0 allowed) — the Black-76
        discount rate. ``iv_source`` (``"vendor"`` default, or ``"mid"`` to
        re-imply the vol from the quote mid). ``multiplier`` (number > 0,
        required). ``min_years`` (number > 0, required) — a leg closer to
        expiry is refused. ``vol_floor`` (number >= 0, required) — a vol
        below it is unusable. ``iv_disagree_tol`` (number > 0; required when
        ``iv_source`` is ``"mid"``) — a leg whose mid-implied vol and vendor
        ``iv`` differ by more than it is flagged ``iv_disagree``.

    Examples
    --------
    Price a long call on the day before expiry-week begins::

        lg = LegGreeks({"rate": 0.0, "multiplier": 100, "min_years": 0.001,
                        "vol_floor": 0.01})
        leg = {"right": "call", "strike": 100.0, "sign": 1, "quantity": 2,
               "expiry": "2024-03-15"}
        row, code = lg.of(leg, {"bid": 2.0, "ask": 2.2, "iv": 0.25},
                          100.0, 100.0, "2024-03-01")
    """

    _PARAMS = ("rate", "iv_source", "multiplier", "min_years", "vol_floor", "iv_disagree_tol")
    NO_FILL_QUOTE = "no_fill_quote"
    NO_IV = "leg_no_iv"
    BELOW_MIN_YEARS = "leg_below_min_years"
    REFUSALS = (NO_FILL_QUOTE, NO_IV, BELOW_MIN_YEARS)

    @classmethod
    def _check(cls, problems, params):
        if "rate" in params:
            check_number_param(problems, "rate", params["rate"], lower=-_INF, upper=_INF)
        if params.get("iv_source", DEFAULT_IV_SOURCE) not in IV_SOURCES:
            problems.append(f"iv_source must be one of {list(IV_SOURCES)}, got "
                            f"{params['iv_source']!r}")
        for name in ("multiplier", "min_years", "iv_disagree_tol"):
            if name in params and not _is_positive(params[name]):
                problems.append(f"{name} must be a positive finite number, got {params[name]!r}")
        if "vol_floor" in params:
            check_number_param(problems, "vol_floor", params["vol_floor"], lower=0.0, upper=_INF)
        _required(problems, params, ("rate", "multiplier", "min_years", "vol_floor"))
        if params.get("iv_source") == "mid":
            _required(problems, params, ("iv_disagree_tol",))

    def _vol(self, leg, quote, forward, years):
        """Return the usable vol, or ``None``."""
        if self.params.get("iv_source", DEFAULT_IV_SOURCE) == "vendor":
            vol = quote.get("iv")
        else:
            mid = (quote["bid"] + quote["ask"]) / 2
            vol = black76_implied_vol(leg["right"], mid, forward, leg["strike"], years,
                                      self.params["rate"]) if mid > 0 else None
        if not price_ok(vol) or vol < self.params["vol_floor"]:
            return None
        return vol

    def _disagrees(self, vol, vendor):
        """Say whether the mid-implied ``vol`` and the ``vendor`` iv differ by more than the tolerance."""
        if self.params.get("iv_source", DEFAULT_IV_SOURCE) != "mid" or vendor is None:
            return False
        return abs(vol - vendor) > self.params["iv_disagree_tol"]

    def of(self, leg, quote, forward, spot, day):
        """Return ``(greeks, None)`` or ``(None, refusal_code)`` for one leg.

        Parameters
        ----------
        leg : dict
            ``right``, ``strike``, ``sign`` (+1 bought / -1 sold),
            ``quantity`` (positive), ``expiry`` (ISO date).
        quote : dict or None
            The leg's session quote: ``bid``, ``ask`` and, for the vendor
            source, ``iv``. ``None`` or an unusable bid/ask refuses
            ``no_fill_quote``.
        forward, spot : float
            Positive forward and spot at ``day`` (see :class:`ParityForward`).
        day : str
            ISO date of the quote.

        Returns
        -------
        tuple
            A dict ``delta_sh, delta_f_sh`` (the forward delta, ``delta_sh = delta_f_sh x
            F/S``), ``gamma_sh, vega_usd_per_pt, theta_usd_per_day,
            iv`` (the vol used), ``vendor_iv`` (the quote's, or ``None``) and
            ``iv_disagree`` (bool; only ``"mid"`` source can set it) and ``None``; or ``None`` and one of :attr:`REFUSALS`.

        Raises
        ------
        ValueError
            On a non-positive forward/spot or a malformed leg.
        """
        for name, value in (("forward", forward), ("spot", spot)):
            if not _is_positive(value):
                raise ValueError(f"{name} must be a positive finite number, got {value!r}")
        if leg.get("right") not in RIGHTS or leg.get("sign") not in (1, -1) or not (
                _is_positive(leg.get("strike")) and _is_positive(leg.get("quantity"))):
            raise ValueError(f"malformed leg {leg!r}")
        if not isinstance(quote, dict) or not _quote_ok(quote.get("bid"), quote.get("ask")):
            return None, self.NO_FILL_QUOTE
        years = (date.fromisoformat(leg["expiry"]) - date.fromisoformat(day)).days / DAYS_PER_YEAR
        if years < self.params["min_years"]:
            return None, self.BELOW_MIN_YEARS
        vol = self._vol(leg, quote, forward, years)
        if vol is None:
            return None, self.NO_IV
        vendor = quote.get("iv") if price_ok(quote.get("iv")) else None
        args = (leg["right"], forward, leg["strike"], vol, years, self.params["rate"])
        scale = leg["sign"] * leg["quantity"] * self.params["multiplier"]
        ratio = forward / spot
        return {
            "delta_sh": scale * black76_delta(*args) * ratio,
            "delta_f_sh": scale * black76_delta(*args),
            "gamma_sh": scale * black76_gamma(*args) * ratio * ratio,
            "vega_usd_per_pt": scale * black76_vega(*args) * _ONE_PERCENT,
            "theta_usd_per_day": scale * black76_theta(*args) / DAYS_PER_YEAR,
            "iv": vol, "vendor_iv": vendor,
            "iv_disagree": self._disagrees(vol, vendor),
        }, None


@dataclass(frozen=True)
class BookGreeks:
    """A book's summed Greeks, the forward they were taken at and its status.

    ``status`` is ``"ok"`` for a parity forward and ``"forward_spot"`` (a flag,
    not a refusal) when the forward fell back to the spot. ``iv_disagree``
    counts the legs whose mid-implied and vendor vols differ beyond the tolerance.

    Examples
    --------
    Sum two legs from :meth:`LegGreeks.of`::

        row = BookGreeks.sum([leg_a, leg_b], 101.0, "parity")
        row.delta_sh   # signed shares
    """

    delta_sh: float
    gamma_sh: float
    vega_usd_per_pt: float
    theta_usd_per_day: float
    forward: float
    forward_source: str
    status: str
    iv_disagree: int = 0
    delta_f_sh: float = field(kw_only=True)

    OK = "ok"
    FORWARD_SPOT = "forward_spot"

    @classmethod
    def sum(cls, legs, forward, forward_source):
        """Sum leg rows from :meth:`LegGreeks.of` into one book row.

        Raises
        ------
        ValueError
            On no legs, an unknown forward source or a non-positive forward.
        """
        if not legs:
            raise ValueError("a book needs at least one leg")
        if forward_source not in FORWARD_SOURCES:
            raise ValueError(f"forward_source must be one of {list(FORWARD_SOURCES)}, "
                             f"got {forward_source!r}")
        if not _is_positive(forward):
            raise ValueError(f"forward must be a positive finite number, got {forward!r}")
        totals = {k: math.fsum(leg[k] for leg in legs)
                  for k in ("delta_sh", "gamma_sh", "vega_usd_per_pt", "theta_usd_per_day",
                                 "delta_f_sh")}
        status = cls.OK if forward_source == ParityForward.SOURCE_PARITY else cls.FORWARD_SPOT
        return cls(**totals, forward=float(forward), forward_source=forward_source,
                   status=status, iv_disagree=sum(bool(leg.get("iv_disagree")) for leg in legs))


class StockEquivalent(_Configured):
    """Split a book's P&L into a delta-equivalent stock part and a residual.

    ``static_delta_pnl = entry_delta_sh x (s_settle - s_fill [+ dividends])`` and
    ``beyond_static_delta = book_pnl - static_delta_pnl``. The residual holds gamma, vega, theta,
    delta drift, fill-vs-mid cost, carry and dividends not passed in. It takes
    no params.

    Examples
    --------
    A book short 150 delta shares that earned 80 while the stock fell 2::

        StockEquivalent().split(80.0, -150.0, 100.0, 98.0)   # -> (300.0, -220.0)
    """

    _PARAMS = ()

    @classmethod
    def _check(cls, problems, params):
        """No knobs: the unknown-key rule is the whole check."""

    def split(self, book_pnl, entry_delta_sh, s_fill, s_settle, dividends_per_share=0.0):
        """Return ``(static_delta_pnl, beyond_static_delta)``; the two sum to ``book_pnl``.

        Parameters
        ----------
        book_pnl : float
            The book's realized P&L in dollars.
        entry_delta_sh : float
            Signed delta in shares at the fill (multiplier included).
        s_fill, s_settle : float
            Positive spot at the fill and at settlement.
        dividends_per_share : float
            Cash a stock holder would have received in between; default 0
            (price-only), so a dividend payer's residual carries it.

        Raises
        ------
        ValueError
            On a non-finite input or a non-positive price.
        """
        for name, value in (("book_pnl", book_pnl), ("entry_delta_sh", entry_delta_sh),
                            ("dividends_per_share", dividends_per_share)):
            if not number_ok(value):
                raise ValueError(f"{name} must be a finite number, got {value!r}")
        for name, value in (("s_fill", s_fill), ("s_settle", s_settle)):
            if not _is_positive(value):
                raise ValueError(f"{name} must be a positive finite number, got {value!r}")
        stock = entry_delta_sh * (s_settle - s_fill + dividends_per_share)
        return stock, book_pnl - stock

    def basis_usd(self, entry_delta_sh, s_fill, forward_fill):
        """Return ``entry_delta x (s_fill - forward_fill)``: carry in the residual."""
        for name, value in (("entry_delta_sh", entry_delta_sh), ("s_fill", s_fill),
                            ("forward_fill", forward_fill)):
            if not number_ok(value):
                raise ValueError(f"{name} must be a finite number, got {value!r}")
        return entry_delta_sh * (s_fill - forward_fill)


class ExpiryDelta(_Configured):
    """The delta a book holds at expiry: each leg is all-in or all-out (description only).

    ``payoff_delta_sh = sum(sign x quantity x multiplier x d)`` with ``d = +1`` for a call
    with ``s_settle > strike``, ``-1`` for a put with ``s_settle < strike`` and ``0``
    otherwise. A leg exactly at the money is ``0`` and its ``quantity`` is counted in
    ``atm_legs`` (a flag). It is mechanical: an all-out-of-the-money book has no payoff
    delta, so a split on it says where the P&L sat at settlement, not what was knowable.

    Parameters
    ----------
    params : dict
        ``multiplier`` (number > 0, required).

    Examples
    --------
    Five 450 calls and one 455 call bought, settling at 458::

        ExpiryDelta({"multiplier": 100}).of(legs, 458.0)   # -> (600, 0)
    """

    _PARAMS = ("multiplier",)

    @classmethod
    def _check(cls, problems, params):
        if "multiplier" in params and not _is_positive(params["multiplier"]):
            problems.append(f"multiplier must be a positive finite number, got {params['multiplier']!r}")
        _required(problems, params, cls._PARAMS)

    def of(self, legs, s_settle):
        """Return ``(payoff_delta_sh, atm_legs)`` for the legs at the settlement price.

        Parameters
        ----------
        legs : list of dict
            ``right``, ``strike``, ``sign`` (+1 / -1), ``quantity`` (positive).
        s_settle : float
            Positive settlement price on the strikes' basis.

        Raises
        ------
        ValueError
            On a non-positive ``s_settle`` or a malformed leg.
        """
        if not _is_positive(s_settle):
            raise ValueError(f"s_settle must be a positive finite number, got {s_settle!r}")
        shares, atm = [], 0
        for leg in legs:
            if leg.get("right") not in RIGHTS or leg.get("sign") not in (1, -1) or not (
                    _is_positive(leg.get("strike")) and _is_positive(leg.get("quantity"))):
                raise ValueError(f"malformed leg {leg!r}")
            if s_settle == leg["strike"]:
                atm += leg["quantity"]
                continue
            call = leg["right"] == RIGHTS[1]
            inside = s_settle > leg["strike"] if call else s_settle < leg["strike"]
            if inside:
                shares.append(leg["sign"] * leg["quantity"] * self.params["multiplier"]
                              * (1 if call else -1))
        return math.fsum(shares), atm


class StaticDeltaHedge(_Configured):
    """Hedge a book's entry delta once with stock and report the P&L left over.

    The hedge is ``-entry_delta_sh`` shares, traded at the fill close ``s_fill``,
    held to expiry and unwound at the settlement close ``s_settle``::

        hedged = book_pnl - delta x (s_settle - s_fill) - stock_costs - short_borrow
                 - delta x dividends_per_share
        stock_costs = |delta| x (2 x fee + (s_fill + s_settle) x half_spread_bps / 1e4)
        short_borrow = max(delta, 0) x s_fill x borrow_rate x hedge_days / 365

    The hedge is short stock exactly when the book's delta is long, so only
    then is borrow paid and a dividend owed; a long hedge receives it. The
    price-plus-dividend leg is :class:`StockEquivalent`'s, not restated here.
    Diagnostics only: no financing of long stock, no interest on short proceeds.

    Parameters
    ----------
    params : dict
        ``hedge_fee_per_share`` (>= 0, dollars per share per trade),
        ``hedge_half_spread_bps`` (>= 0, per trade), ``hedge_borrow_rate``
        (annual, >= 0 and < 1). All required: a default would hide a cost.

    Examples
    --------
    A book long 290 delta shares, the stock down 4 over 10 days::

        h = StaticDeltaHedge({"hedge_fee_per_share": 0.005, "hedge_half_spread_bps": 5.0,
                              "hedge_borrow_rate": 0.005})
        h.of(-938.90, 290.0, 452.0, 448.0, 10, 0.0)["hedged_pnl"]   # -> 69.74
    """

    _PARAMS = ("hedge_fee_per_share", "hedge_half_spread_bps", "hedge_borrow_rate")
    _BPS = 1e4

    @classmethod
    def _check(cls, problems, params):
        for name in ("hedge_fee_per_share", "hedge_half_spread_bps"):
            if name in params:
                check_number_param(problems, name, params[name], lower=0.0, upper=_INF)
        if "hedge_borrow_rate" in params:
            check_number_param(problems, "hedge_borrow_rate", params["hedge_borrow_rate"],
                               lower=0.0, upper=1.0)
            if params["hedge_borrow_rate"] == 1.0:
                problems.append("hedge_borrow_rate must be below 1, got 1.0")
        _required(problems, params, cls._PARAMS)

    def of(self, book_pnl, entry_delta_sh, s_fill, s_settle, hedge_days, dividends_per_share):
        """Return ``stock_costs``, ``short_borrow``, ``dividend_adjust`` and ``hedged_pnl``.

        Parameters
        ----------
        book_pnl, entry_delta_sh, s_fill, s_settle : float
            As :meth:`StockEquivalent.split`.
        hedge_days : float
            Calendar days from the fill to expiry (>= 0).
        dividends_per_share : float or None
            Cash per share with ex-date in ``(fill, expiry]``; ``None`` when the
            inputs carry no dividends (computed as 0, ``dividend_adjust`` is ``None``).

        Returns
        -------
        dict
            ``dividend_adjust = entry_delta_sh x D``; ``hedged_pnl`` is the book
            less the stock leg, costs and borrow.

        Raises
        ------
        ValueError
            On a bad ``hedge_days`` or a non-finite or non-positive input.
        """
        dividend = 0.0 if dividends_per_share is None else dividends_per_share
        equivalent = StockEquivalent()
        price_stock, _ = equivalent.split(book_pnl, entry_delta_sh, s_fill, s_settle)
        total_stock, residual = equivalent.split(book_pnl, entry_delta_sh, s_fill, s_settle,
                                                 dividends_per_share=dividend)
        costs = self.carry_costs(-entry_delta_sh, s_fill, s_settle, hedge_days)
        return {"stock_costs": costs["trade_costs"], "short_borrow": costs["borrow"],
                "dividend_adjust": None if dividends_per_share is None else total_stock - price_stock,
                "hedged_pnl": residual - costs["trade_costs"] - costs["borrow"]}

    def carry_costs(self, units, p_fill, p_settle, days):
        """Return ``trade_costs`` and ``borrow`` of a hedge of ``units`` HELD shares.

        The one cost rule of every hedge (the stock hedge here, the forward hedge, ADR-0312's
        overlay)::

            trade_costs = |units| x (2 x fee + (p_fill + p_settle) x half_spread_bps / 1e4)
            borrow      = max(-units, 0) x p_fill x borrow_rate x days / 365

        Parameters
        ----------
        units : float
            Held shares of the instrument, negative = short (so only a short pays borrow).
        p_fill, p_settle : float
            The instrument's positive reference price at the fill and at settlement.
        days : float
            Calendar days from the fill to expiry (>= 0).

        Returns
        -------
        dict
            ``trade_costs`` (fee at entry and unwind, half-spread on both prices) and
            ``borrow``; the caller decides which of them it charges.

        Raises
        ------
        ValueError
            On a non-finite ``units``, a non-positive price or a bad ``days``.
        """
        if not number_ok(units):
            raise ValueError(f"units must be a finite number, got {units!r}")
        for name, value in (("p_fill", p_fill), ("p_settle", p_settle)):
            if not _is_positive(value):
                raise ValueError(f"{name} must be a positive finite number, got {value!r}")
        if not number_ok(days) or days < 0:
            raise ValueError(f"hedge_days must be a finite number >= 0, got {days!r}")
        p = self.params
        trade = abs(units) * (2 * p["hedge_fee_per_share"]
                              + (p_fill + p_settle) * p["hedge_half_spread_bps"] / self._BPS)
        borrow = max(-units, 0.0) * p_fill * p["hedge_borrow_rate"] * days / DAYS_PER_YEAR
        return {"trade_costs": trade, "borrow": borrow}


class ForwardHedge(_Configured):
    """A cash-settled forward hedge sized at the decision and held to settlement.

    The units are the decision-session forward delta, truncated ONCE toward zero in
    :meth:`units`; the leg is struck at the fill forward ``f_fill`` and settles at
    the settlement close (``F_T = S_T``)::

        forward_hedged_pnl = book_pnl + units x (s_settle - f_fill) - trade_costs
        hedge_pnl          = forward_hedged_pnl - book_pnl

    ``trade_costs`` (fee and half-spread) come from :meth:`StaticDeltaHedge.carry_costs`.
    No borrow is charged: the parity forward already prices carry and borrow. The borrow
    that rule computes is reported as ``borrow_usd`` only.

    Parameters
    ----------
    params : dict
        ``hedge_cost_source`` (``"placeholder"`` or ``"measured"``, required) — where the
        three :class:`StaticDeltaHedge` rates come from; echoed on every row.

    Examples
    --------
    A book short 288.4 forward-delta shares of hedge, struck at 451 and settled at 448::

        fh = ForwardHedge({"hedge_cost_source": "placeholder"})
        units = fh.units(288.4)                                  # -288
        costs = hedge.carry_costs(units, 451.0, 448.0, 10)
        fh.of(-938.90, units, 451.0, 448.0, costs)["forward_hedged_pnl"]   # -> -207.236
    """

    _PARAMS = ("hedge_cost_source",)

    @classmethod
    def _check(cls, problems, params):
        if "hedge_cost_source" in params and params["hedge_cost_source"] not in HEDGE_COST_SOURCES:
            problems.append(f"hedge_cost_source must be one of {list(HEDGE_COST_SOURCES)}, "
                            f"got {params['hedge_cost_source']!r}")
        _required(problems, params, cls._PARAMS)

    @staticmethod
    def units(delta_f_sh_decision):
        """Return the held shares ``trunc(-delta_f_sh_decision)``: whole units toward zero.

        The one place truncation happens; every later step takes these units unchanged.
        """
        if not number_ok(delta_f_sh_decision):
            raise ValueError(f"delta_f_sh_decision must be a finite number, got {delta_f_sh_decision!r}")
        return math.trunc(-delta_f_sh_decision)

    @staticmethod
    def rounding(units, delta_f_sh_decision):
        """Return ``units + delta_f_sh_decision``: what truncation left unhedged."""
        return units + delta_f_sh_decision

    def of(self, book_pnl, units, f_fill, s_settle, costs):
        """Return the forward hedge's cost and P&L fields; nothing is truncated here.

        Parameters
        ----------
        book_pnl : float
            The book's realized P&L in dollars.
        units : float
            Held shares from :meth:`units` (negative = short).
        f_fill, s_settle : float
            Positive fill forward (the strike) and settlement close.
        costs : dict
            ``carry_costs(units, f_fill, s_settle, days)``.

        Returns
        -------
        dict
            ``trade_costs_usd``, ``borrow_usd`` (reported, not charged), ``forward_hedge_costs``
            (= ``trade_costs_usd``), ``forward_hedged_pnl`` and ``hedge_pnl``.

        Raises
        ------
        ValueError
            On a non-finite ``book_pnl`` or ``units`` or a non-positive price.
        """
        for name, value in (("book_pnl", book_pnl), ("units", units)):
            if not number_ok(value):
                raise ValueError(f"{name} must be a finite number, got {value!r}")
        for name, value in (("f_fill", f_fill), ("s_settle", s_settle)):
            if not _is_positive(value):
                raise ValueError(f"{name} must be a positive finite number, got {value!r}")
        leg = units * (s_settle - f_fill) - costs["trade_costs"]
        return {"trade_costs_usd": costs["trade_costs"], "borrow_usd": costs["borrow"],
                "forward_hedge_costs": costs["trade_costs"],
                "forward_hedged_pnl": book_pnl + leg, "hedge_pnl": leg}


class HardToBorrowProxy(_Configured):
    """A volatility/jump proxy for a name whose short hedge may not be borrowable.

    A volatility rule, not a locate. Over the ``htb_lookback`` closes strictly before the
    DECISION session (on the split-adjusted ``bars`` basis, so log returns across a split
    date are clean and no factor is applied) the name is flagged when the sample standard
    deviation of its daily log returns times ``sqrt(252)`` exceeds ``htb_vol_threshold``,
    or its largest absolute 5-session log return exceeds ``htb_jump_threshold``.

    Parameters
    ----------
    params : dict
        ``htb_lookback`` (int >= 6: a 5-session return needs six closes),
        ``htb_vol_threshold`` and ``htb_jump_threshold`` (numbers > 0); all required.

    Examples
    --------
    One name's window and the closes before a decision day::

        px = HardToBorrowProxy({"htb_lookback": 20, "htb_vol_threshold": 2.5,
                                "htb_jump_threshold": 0.75})
        closes = px.closes_before(bars["XYZ"], "2021-03-01")
        flagged = px.of(closes) if closes is not None else False
    """

    _PARAMS = ("htb_lookback", "htb_vol_threshold", "htb_jump_threshold")
    MIN_LOOKBACK = 6
    _JUMP_SESSIONS = 5

    @classmethod
    def _check(cls, problems, params):
        if "htb_lookback" in params:
            check_int_param(problems, "htb_lookback", params["htb_lookback"], ge=cls.MIN_LOOKBACK)
        for name in ("htb_vol_threshold", "htb_jump_threshold"):
            if name in params:
                check_number_param(problems, name, params[name], lower=0.0, upper=_INF)
                if params[name] == 0:
                    problems.append(f"{name} must be above 0, got {params[name]!r}")
        _required(problems, params, cls._PARAMS)

    def closes_before(self, series, day):
        """Return the ``htb_lookback`` closes strictly before ``day``, or ``None``.

        ``series`` maps ISO dates to closes. ``None`` when fewer sessions exist or any close
        in the window is not positive and finite (the proxy cannot be read).
        """
        window = [series[d] for d in sorted(d for d in series if d < day)][-self.params["htb_lookback"]:]
        if len(window) < self.params["htb_lookback"] or not all(_is_positive(c) for c in window):
            return None
        return window

    def of(self, closes):
        """Say whether the window of ``closes`` flags the name hard to borrow.

        Raises
        ------
        ValueError
            When ``closes`` is not ``htb_lookback`` positive finite numbers.
        """
        if len(closes) != self.params["htb_lookback"] or not all(_is_positive(c) for c in closes):
            raise ValueError(f"closes must be {self.params['htb_lookback']} positive finite numbers")
        returns = [math.log(b / a) for a, b in zip(closes, closes[1:])]
        vol = stdev(returns) * math.sqrt(TRADING_DAYS_PER_YEAR)
        jump = max(abs(math.log(closes[i + self._JUMP_SESSIONS] / closes[i]))
                   for i in range(len(closes) - self._JUMP_SESSIONS))
        return bool(vol > self.params["htb_vol_threshold"] or jump > self.params["htb_jump_threshold"])


class Concentration(_Configured):
    """How much of a sample one book (or three) carries, and whether its sign survives dropping one.

    Description only: ``loo_sign_stable`` is a one-book leave-out, not a bootstrap.

    Parameters
    ----------
    params : dict
        ``min_books`` (int >= 1, required) — fewer books than this is a ``small_cell``.

    Examples
    --------
    Three books, the sign of the total surviving none of the leave-outs::

        Concentration({"min_books": 5}).of(rows, "pnl", ["pnl"])["loo_sign_stable_pnl"]
    """

    _PARAMS = ("min_books",)
    _TOP = 3

    @classmethod
    def _check(cls, problems, params):
        if "min_books" in params:
            check_int_param(problems, "min_books", params["min_books"], ge=1)
        _required(problems, params, cls._PARAMS)

    @staticmethod
    def _sign(x):
        return (x > 0) - (x < 0)

    def of(self, rows, value_field, sign_fields):
        """Return ``n_books``, the gross shares, ``loo_sign_stable_<f>`` per field and ``small_cell``.

        ``top1_share_gross`` is ``max |v| / sum |v|`` and ``top3_share_gross`` the three largest
        (``None`` when the gross is 0). ``loo_sign_stable_<f>`` is true when the sign of the total
        of ``f`` is unchanged by dropping any one row (a zero counts as a change); ``None`` with
        fewer than two rows.

        Raises
        ------
        ValueError
            On a row whose ``value_field`` or ``sign_fields`` value is not a finite number.
        """
        values = []
        for row in rows:
            for name in {value_field, *sign_fields}:
                if not number_ok(row.get(name)):
                    raise ValueError(f"{name} must be a finite number, got {row.get(name)!r}")
            values.append(abs(row[value_field]))
        gross = math.fsum(values)
        ranked = sorted(values, reverse=True)
        out = {"n_books": len(rows),
               "top1_share_gross": ranked[0] / gross if gross > 0 else None,
               "top3_share_gross": math.fsum(ranked[:self._TOP]) / gross if gross > 0 else None}
        for name in sign_fields:
            out[f"loo_sign_stable_{name}"] = self._stable([r[name] for r in rows])
        out["small_cell"] = len(rows) < self.params["min_books"]
        return out

    def _stable(self, xs):
        """Return whether every leave-one-out total keeps the full total's (nonzero) sign."""
        if len(xs) <= 1:
            return None
        sign = self._sign(math.fsum(xs))
        return sign != 0 and all(
            self._sign(math.fsum(xs[:i] + xs[i + 1:])) == sign for i in range(len(xs)))


class CellAttribution(_Configured):
    """Sums and shares of the static-delta split per cell.

    Per cell: ``n``, ``pnl_usd``, ``static_delta_usd``, ``beyond_static_usd``; the
    optional row field ``hedged_pnl`` (``None`` off ``hedge_status == ok``) is
    summed over the rows that have one to ``hedged_pnl_usd`` with ``n_stock_hedged``
    rows counted (a null book does not null the sum; ``None`` only when no row has
    one), and the optional ``hedge_status`` counts ``n_dividends_absent`` and
    ``n_dividend_missing``; the net
    shares ``static_share_net`` / ``beyond_static_share_net`` (part over the cell
    total, ``None`` when ``|total| < min_total_usd``); and ``gross_pnl_usd``
    (``sum |pnl|``) with ``static_share_gross`` / ``beyond_static_share_gross`` (part
    over the gross, ``None`` when the gross is zero), which cannot explode
    where the total crosses zero.

    The optional forward-hedge row fields (``payoff_delta_pnl``, ``beyond_payoff_delta``,
    ``forward_hedge_status`` and, on ``ok`` rows, ``forward_hedged_pnl``, ``hedge_pnl``,
    ``forward_hedge_costs``) add the payoff split sums, the forward-hedge counts and dollar
    effects (:meth:`hedge_effect`) and, given a :class:`Concentration`, its guards.

    Parameters
    ----------
    params : dict
        ``cell_by`` (non-empty list of row field names, required) — the cell
        key. ``min_total_usd`` (number >= 0, required).

    Examples
    --------
    Group book rows by name and expiry rank::

        ca = CellAttribution({"cell_by": ["symbol", "rank"], "min_total_usd": 1.0})
        cells = ca.summarize(rows)   # rows: symbol, rank, pnl, static_delta_pnl, beyond_static_delta
    """

    _PARAMS = ("cell_by", "min_total_usd")
    CELL_FIELD_MISSING = "cell_field_missing"
    PNL_NOT_FINITE = "pnl_not_finite"
    REFUSALS = (CELL_FIELD_MISSING, PNL_NOT_FINITE)
    _FIELDS = ("pnl", "static_delta_pnl", "beyond_static_delta")
    _OPTIONAL = "hedged_pnl"
    _STATUS = "hedge_status"
    #: Optional forward-hedge and payoff-split row fields (ADR-0310 A2); absent ones read as null.
    _FORWARD_FIELDS = ("payoff_delta_pnl", "beyond_payoff_delta", "forward_hedge_status",
                       "forward_hedged_pnl", "hedge_pnl", "forward_hedge_costs", "htb_unreadable")
    _HEDGE_NUMBERS = ("forward_hedged_pnl", "hedge_pnl", "forward_hedge_costs")

    @classmethod
    def _check(cls, problems, params):
        by = params.get("cell_by")
        if "cell_by" in params and (not isinstance(by, list) or not by or not all(
                isinstance(f, str) and f for f in by) or len(set(by)) != len(by)):
            problems.append(f"cell_by must be a non-empty list of distinct field names, got {by!r}")
        if "min_total_usd" in params:
            check_number_param(problems, "min_total_usd", params["min_total_usd"],
                               lower=0.0, upper=_INF)
        _required(problems, params, cls._PARAMS)

    def summarize(self, rows, concentration=None):
        """Return the cell rows, sorted by key.

        Parameters
        ----------
        rows : list of dict
            Each carries every ``cell_by`` field and finite ``pnl``,
            ``static_delta_pnl`` and ``beyond_static_delta`` (dollars).
        concentration : Concentration, optional
            When given, every cell also carries ``top1_share_gross``, ``top3_share_gross``,
            ``loo_sign_stable_pnl``, ``loo_sign_stable_hedge_pnl`` (over the ``ok``-hedged
            rows) and ``small_cell``.

        Raises
        ------
        ValueError
            Starting ``cell_field_missing`` or ``pnl_not_finite`` on a bad row.
        """
        by = self.params["cell_by"]
        cells = {}
        for row in rows:
            missing = [f for f in by if f not in row]
            if missing:
                raise ValueError(f"{self.CELL_FIELD_MISSING}: row lacks {missing}")
            hedged = row.get(self._OPTIONAL)
            optional = [row.get(f) for f in ("payoff_delta_pnl", "beyond_payoff_delta")]
            if row.get("forward_hedge_status") == HEDGE_OK_FORWARD:
                optional += [row.get(f) for f in self._HEDGE_NUMBERS]
                optional = [x if x is not None else math.nan for x in optional]
            if not all(number_ok(row.get(f)) for f in self._FIELDS) or (
                    hedged is not None and not number_ok(hedged)) or not all(
                    x is None or number_ok(x) for x in optional):
                raise ValueError(f"{self.PNL_NOT_FINITE}: "
                                 f"{[row.get(f) for f in (*self._FIELDS, self._OPTIONAL)]}")
            cells.setdefault(tuple(row[f] for f in by), []).append(row)
        return [self._cell(by, key, group, concentration)
                for key, group in sorted(cells.items(), key=lambda kv: tuple(map(str, kv[0])))]

    @staticmethod
    def _sum_of(rows, name):
        """Return the sum of ``name`` over the rows that have it, or ``None`` when none does."""
        values = [r[name] for r in rows if r.get(name) is not None]
        return math.fsum(values) if values else None

    @classmethod
    def hedge_effect(cls, rows):
        """Return the forward-hedge counts and dollar effects of ``rows`` (a cell or a method x quarter).

        Counts by ``forward_hedge_status`` (``n_hedged`` is ``ok``); over the ``ok`` rows only:
        ``forward_hedged_pnl_usd``, ``hedge_pnl_usd`` (= hedged less book P&L, same books),
        ``hedge_loss_reduction_usd`` (= ``sum max(-pnl, 0) - sum max(-forward_hedged_pnl, 0)``)
        and ``hedge_costs_usd`` (``None`` with no ``ok`` row); ``hedgeable_share_gross`` is
        the ``ok`` rows' ``sum |pnl|`` over every attributed row's.
        """
        status = Counter(r.get("forward_hedge_status") for r in rows)
        ok = [r for r in rows if r.get("forward_hedge_status") == HEDGE_OK_FORWARD]
        gross = math.fsum(abs(r["pnl"]) for r in rows
                          if r.get("forward_hedge_status") != NOT_ATTRIBUTED)
        hedged = math.fsum(r["forward_hedged_pnl"] for r in ok)
        return {
            "n_hedged": len(ok), "n_unbounded_above": status[HEDGE_UNBOUNDED],
            "n_hard_to_borrow": status[HEDGE_HTB], "n_decision_unmarked": status[DECISION_UNMARKED],
            "n_not_attributed": status[NOT_ATTRIBUTED],
            "n_htb_unreadable": sum(bool(r.get("htb_unreadable")) for r in rows),
            "hedgeable_share_gross": (math.fsum(abs(r["pnl"]) for r in ok) / gross
                                      if gross > 0 else None),
            "forward_hedged_pnl_usd": hedged if ok else None,
            "hedge_pnl_usd": hedged - math.fsum(r["pnl"] for r in ok) if ok else None,
            "hedge_loss_reduction_usd": (
                math.fsum(max(-r["pnl"], 0.0) for r in ok)
                - math.fsum(max(-r["forward_hedged_pnl"], 0.0) for r in ok)) if ok else None,
            "hedge_costs_usd": math.fsum(r["forward_hedge_costs"] for r in ok) if ok else None}

    def _cell(self, by, key, group, concentration=None):
        """Return one cell's row."""
        total, stock, resid = (math.fsum(r[f] for r in group) for f in self._FIELDS)
        gross = math.fsum(abs(r["pnl"]) for r in group)
        hedged = [h for h in (r.get(self._OPTIONAL) for r in group) if h is not None]
        statuses = Counter(r.get(self._STATUS) for r in group)
        net_ok = abs(total) >= self.params["min_total_usd"] and total != 0
        effect = self.hedge_effect(group)
        cell = {
            **dict(zip(by, key)), "n": len(group), "pnl_usd": total,
            "static_delta_usd": stock, "beyond_static_usd": resid,
            "hedged_pnl_usd": math.fsum(hedged) if hedged else None,
            "n_stock_hedged": len(hedged),
            "n_dividends_absent": statuses[DIVIDENDS_ABSENT],
            "n_dividend_missing": statuses[DIVIDEND_MISSING],
            "static_share_net": stock / total if net_ok else None,
            "beyond_static_share_net": resid / total if net_ok else None,
            "gross_pnl_usd": gross,
            "static_share_gross": stock / gross if gross > 0 else None,
            "beyond_static_share_gross": resid / gross if gross > 0 else None,
            "payoff_delta_usd": self._sum_of(group, "payoff_delta_pnl"),
            "beyond_payoff_usd": self._sum_of(group, "beyond_payoff_delta"),
            **{k: effect[k] for k in (
                "n_hedged", "n_unbounded_above", "n_hard_to_borrow", "n_not_attributed",
                "n_decision_unmarked", "n_htb_unreadable",
                "hedgeable_share_gross", "forward_hedged_pnl_usd", "hedge_pnl_usd",
                "hedge_loss_reduction_usd", "hedge_costs_usd")},
        }
        if concentration is not None:
            ok = [r for r in group if r.get("forward_hedge_status") == HEDGE_OK_FORWARD]
            book = concentration.of(group, "pnl", ["pnl"])
            hedge = concentration.of(ok, "hedge_pnl", ["hedge_pnl"])
            cell.update(top1_share_gross=book["top1_share_gross"],
                        top3_share_gross=book["top3_share_gross"],
                        loo_sign_stable_pnl=book["loo_sign_stable_pnl"],
                        loo_sign_stable_hedge_pnl=hedge["loo_sign_stable_hedge_pnl"],
                        small_cell=book["small_cell"])
        return cell


class ExposureSnapshot(_Configured):
    """One day's portfolio exposure by remaining-DTE bucket.

    Per bucket and in total: ``beta_delta_usd`` (``delta_sh x spot x beta``,
    dollars of the basket), ``vega_usd_per_pt`` (net) and ``gamma_usd_per_1pct``
    (``gamma_sh x spot^2 x 0.01``, not beta-weighted). Buckets are half-open
    ``[edge_i, edge_i+1)``; a book outside ``[edges[0], edges[-1])`` is
    excluded and counted.

    Parameters
    ----------
    params : dict
        ``dte_edges`` (strictly increasing list of >= 2 numbers, required).
        ``top_k`` (int >= 1, required) — names in the concentration share.

    Examples
    --------
    Three weekly buckets and the top two names::

        es = ExposureSnapshot({"dte_edges": [0, 7, 14, 21], "top_k": 2})
        out = es.of(betas, books)   # books: symbol, dte, spot, delta_sh, ...
    """

    _PARAMS = ("dte_edges", "top_k")
    DTE_OUTSIDE_EDGES = "dte_outside_edges"
    BETA_MISSING = "beta_missing"
    REFUSALS = (DTE_OUTSIDE_EDGES, BETA_MISSING)
    _SUMS = ("beta_delta_usd", "vega_usd_per_pt", "gamma_usd_per_1pct")

    @classmethod
    def _check(cls, problems, params):
        edges = params.get("dte_edges")
        if "dte_edges" in params and (
                not isinstance(edges, list) or len(edges) < 2
                or not all(number_ok(e) for e in edges)
                or any(a >= b for a, b in zip(edges, edges[1:]))):
            problems.append("dte_edges must be a strictly increasing list of at least two "
                            f"numbers, got {edges!r}")
        if "top_k" in params:
            check_int_param(problems, "top_k", params["top_k"], ge=1)
        _required(problems, params, cls._PARAMS)

    def _bucket(self, dte):
        """Return the bucket index of ``dte``, or ``None`` outside the edges."""
        edges = self.params["dte_edges"]
        for i, (low, high) in enumerate(zip(edges, edges[1:])):
            if low <= dte < high:
                return i
        return None

    def of(self, betas, books):
        """Return ``{"buckets", "total", "top_k_share", "excluded"}``.

        Parameters
        ----------
        betas : dict
            Symbol to beta (see :func:`fit_betas`).
        books : list of dict
            Open books: ``symbol``, ``dte`` (remaining), ``spot``,
            ``delta_sh``, ``gamma_sh``, ``vega_usd_per_pt``, ``status``. A
            status other than ``"ok"``/``"forward_spot"`` (for example
            ``"open_leg_unmarked"``) excludes the book, counted by status.

        Returns
        -------
        dict
            ``buckets``: one dict per bucket (``dte_min``, ``dte_max``,
            ``n_books`` and the sums); ``total``: the same over all included
            books; ``top_k_share``: the top-k names' share of absolute
            beta-weighted delta (``None`` when it is all zero); ``excluded``:
            counts by code.

        Raises
        ------
        ValueError
            Starting ``beta_missing`` when a book's symbol has no beta.
        """
        edges = self.params["dte_edges"]
        groups = [[] for _ in edges[1:]]
        names, excluded = {}, {}
        for book in books:
            if book["status"] not in (BookGreeks.OK, BookGreeks.FORWARD_SPOT):
                excluded[book["status"]] = excluded.get(book["status"], 0) + 1
                continue
            index = self._bucket(book["dte"])
            if index is None:
                excluded[self.DTE_OUTSIDE_EDGES] = excluded.get(self.DTE_OUTSIDE_EDGES, 0) + 1
                continue
            if book["symbol"] not in betas:
                raise ValueError(f"{self.BETA_MISSING}: {book['symbol']!r}")
            row = {
                "beta_delta_usd": book["delta_sh"] * book["spot"] * betas[book["symbol"]],
                "vega_usd_per_pt": book["vega_usd_per_pt"],
                "gamma_usd_per_1pct": book["gamma_sh"] * book["spot"] ** 2 * _ONE_PERCENT,
            }
            groups[index].append(row)
            names[book["symbol"]] = names.get(book["symbol"], 0.0) + row["beta_delta_usd"]
        buckets = [{"dte_min": low, "dte_max": high, **self._sums(group)}
                   for (low, high), group in zip(zip(edges, edges[1:]), groups)]
        total = self._sums([r for group in groups for r in group])
        gross = math.fsum(abs(v) for v in names.values())
        top = math.fsum(sorted((abs(v) for v in names.values()), reverse=True)
                        [:self.params["top_k"]])
        return {"buckets": buckets, "total": total,
                "top_k_share": top / gross if gross > 0 else None, "excluded": excluded}

    def _sums(self, group):
        """Return a group's book count and sums."""
        return {"n_books": len(group),
                **{k: math.fsum(r[k] for r in group) for k in self._SUMS}}


def fit_betas(returns, symbols, count, min_pairs):
    """Fit each name's beta on the equal-weight basket of ``symbols``.

    The shock gate's rule: all 1.0 when the window cannot fit them (fewer
    than ``min_pairs`` return pairs, no names, a flat basket, or a non-finite
    fit). Variance and covariance are population (ddof 0).

    Parameters
    ----------
    returns : dict
        Symbol to a sequence of equal-length returns.
    symbols : list of str
        The basket (and the names fitted).
    count : int
        Return pairs in the window.
    min_pairs : int
        Fewest pairs that may be fitted.

    Returns
    -------
    dict
        Symbol to beta.

    Examples
    --------
    Two names, the second twice as volatile as the first::

        fit_betas({"A": [0.01, -0.01, 0.02], "B": [0.02, -0.02, 0.04]},
                  ["A", "B"], 3, 2)   # -> A about 0.667, B about 1.333
    """
    betas = {s: 1.0 for s in symbols}
    if count < min_pairs or not symbols:
        return betas
    length = len(returns[symbols[0]])
    market = [math.fsum(returns[s][i] for s in symbols) / len(symbols) for i in range(length)]
    m_mean = math.fsum(market) / length
    variance = math.fsum((m - m_mean) ** 2 for m in market) / length
    if not variance > 0:
        return betas
    fitted = {}
    for s in symbols:
        mean = math.fsum(returns[s]) / length
        fitted[s] = math.fsum((r - mean) * (m - m_mean)
                              for r, m in zip(returns[s], market)) / length / variance
    return fitted if all(math.isfinite(b) for b in fitted.values()) else betas


class BookGreeksReport(Node):
    """Greeks, stock-equivalent attribution and exposure of settled option books.

    Composes :class:`ParityForward`, :class:`LegGreeks`, :class:`BookGreeks`,
    :class:`StockEquivalent`, :class:`StaticDeltaHedge`, :class:`CellAttribution`,
    :class:`ExposureSnapshot` and :func:`fit_betas`; no arithmetic of theirs is repeated here. A subclass
    maps its own saved files to the generic inputs by three required hooks
    (:meth:`_load_books`, :meth:`_load_quotes`, :meth:`_cell_key`) and the optional
    :meth:`_hedge_dividend`, and may refuse a read through :meth:`_guard_window`.

    Per book (a normalized dict, see :meth:`_load_books`): the Greeks at the
    fill session on a parity forward, ``static_delta_pnl`` / ``beyond_static_delta`` against the
    settlement spot, then the cell sums. Per decision and/or fill day: every
    open book (``fill_date <= day < expiry``) re-marked on that day's chain.
    A book that cannot be attributed keeps its P&L in ``unattributed_pnl_usd``
    under one status code (``leg_no_iv``, ``no_fill_quote``,
    ``leg_below_min_years``, ``basis_changed``, ``no_settlement_bar``, ``no_spot``,
    ``duplicate_quote``); the
    ``forward_spot`` status is a flag, not a refusal. A day's open book that
    cannot be marked is excluded from that day as ``open_leg_unmarked``.

    Parameters
    ----------
    key : str
        Pipeline node key.
    params : dict
        Every knob of :class:`LegGreeks` (``rate``, ``iv_source``,
        ``multiplier``, ``min_years``, ``vol_floor``), :class:`ParityForward`
        (``parity_strikes``, ``min_pairs``, ``max_pair_spread_frac``),
        :class:`StaticDeltaHedge` (``hedge_fee_per_share``, ``hedge_half_spread_bps``,
        ``hedge_borrow_rate``; dividends come from the :meth:`_hedge_dividend` hook),
        :class:`CellAttribution` (``cell_by``, ``min_total_usd``) and
        :class:`ExposureSnapshot` (``dte_edges``, ``top_k``), plus
        ``exposure_days`` (``"decision"``, ``"fill"`` or ``"both"``),
        ``beta_lookback`` (int >= 2: trailing sessions strictly before the
        day) and ``beta_min_pairs`` (int >= 1: fewer return pairs make every
        beta 1.0, counted in the summary).

    Inputs
    ------
    ``books``, ``quotes``
        Raw handles for the hooks. ``bars``: ``{symbol: {date: close}}`` on
        the split-adjusted basis; a book's ``factor`` divides it to the
        chain's basis. The beta basket is every symbol in ``bars`` whose close
        is positive on every session of the window (the sessions any name has,
        strictly before the day); any other name gets beta 1.0 and is counted
        in ``beta_dropped_name_days``, as the shock gate drops a non-finite name.

    Outputs
    -------
    ``books``, ``cells``, ``exposure``, ``summary``
        Row lists (``books`` one per book, ``cells`` from
        :class:`CellAttribution`, ``exposure`` one row per day and DTE bucket
        plus a ``"total"`` row) and the counts dict.

    Examples
    --------
    A subclass reading in-memory books and a dict of chains::

        class Mem(BookGreeksReport):
            def _load_books(self, raw):
                return raw
            def _load_quotes(self, raw):
                return MemQuotes(raw)       # .chain(symbol, day, expiry) -> rows
            def _cell_key(self, book, quotes):
                return {"symbol": book["symbol"]}

        node = Mem("report", params)
        out = node.run(None, {"books": books, "quotes": chains, "bars": bars})
    """

    role = "transform"
    kind = "book_greeks_report"
    outputs = ("books", "cells", "exposure", "summary")
    _OWNERS = (LegGreeks, ParityForward, CellAttribution, ExposureSnapshot, StaticDeltaHedge,
               ExpiryDelta, ForwardHedge, Concentration, HardToBorrowProxy)
    #: ADR-0310 A2 keys frozen in stage 0; their owners are :class:`ForwardHedge`,
    #: :class:`Concentration` and :class:`HardToBorrowProxy`.
    NEW_CORE_PARAMS = ("hedge_cost_source", "min_books", "htb_lookback", "htb_vol_threshold",
                       "htb_jump_threshold")
    HEDGE_COST_SOURCE_MISMATCH = "hedge_cost_source_mismatch"
    #: Fewest closes a hard-to-borrow window may hold: a 5-session return needs six.
    HTB_MIN_LOOKBACK = HardToBorrowProxy.MIN_LOOKBACK
    _OWN_PARAMS = ("exposure_days", "beta_lookback", "beta_min_pairs")
    _PARAMS = tuple(dict.fromkeys(name for owner in _OWNERS for name in owner._PARAMS)) + _OWN_PARAMS
    EXPOSURE_DAYS = ("decision", "fill", "both")
    BASIS_CHANGED = "basis_changed"
    NO_SPOT = "no_spot"
    HEDGE_OK, DIVIDENDS_ABSENT, DIVIDEND_MISSING, NOT_ATTRIBUTED = (
        HEDGE_OK, DIVIDENDS_ABSENT, DIVIDEND_MISSING, NOT_ATTRIBUTED)
    HEDGE_STATUSES = (HEDGE_OK, DIVIDENDS_ABSENT, DIVIDEND_MISSING, NOT_ATTRIBUTED)
    #: Where the dividends come from; a subclass that reads them sets its own name.
    hedge_dividend_source = "absent"
    DUPLICATE_QUOTE = ParityForward.DUPLICATE_QUOTE
    NO_SETTLEMENT_BAR = "no_settlement_bar"
    OPEN_LEG_UNMARKED = "open_leg_unmarked"
    TOTAL_BUCKET = "total"
    _GREEK_FIELDS = ("delta_sh", "gamma_sh", "vega_usd_per_pt", "theta_usd_per_day")
    #: The columns each output has (a row list that is empty still has them).
    BOOK_COLUMNS = ("id", "symbol", "decision_date", "fill_date", "expiry", "pnl", "status",
                    "forward", "forward_source", "s_fill", "s_settle", *_GREEK_FIELDS,
                    "static_delta_pnl", "beyond_static_delta", "basis_usd", "iv_disagree_legs",
                    "hedge_sh",
                    "hedge_days", "stock_costs", "short_borrow", "dividend_adjust", "hedged_pnl",
                    "hedge_status",
                    "payoff_delta_sh", "payoff_delta_pnl", "beyond_payoff_delta", "atm_legs",
                    "delta_f_sh", "delta_f_sh_decision", "forward_hedge_units", "hedge_rounding_sh",
                    "trade_costs_usd", "borrow_usd", "forward_hedge_costs", "forward_hedged_pnl",
                    "hedge_pnl", "forward_hedge_status", "hard_to_borrow", "hedge_cost_source",
                    "htb_unreadable")
    #: The forward-hedge and payoff columns a refused book leaves null.
    _FORWARD_COLUMNS = ("payoff_delta_sh", "payoff_delta_pnl", "beyond_payoff_delta", "atm_legs",
                        "delta_f_sh", "delta_f_sh_decision", "forward_hedge_units",
                        "hedge_rounding_sh", "trade_costs_usd", "borrow_usd", "forward_hedge_costs",
                        "forward_hedged_pnl", "hedge_pnl", "hard_to_borrow", "htb_unreadable")
    EXPOSURE_COLUMNS = ("date", "bucket", "dte_min", "dte_max", "n_books", "beta_delta_usd",
                        "vega_usd_per_pt", "gamma_usd_per_1pct", "top_k_share", "excluded")
    CELL_COLUMNS = ("n", "pnl_usd", "static_delta_usd", "beyond_static_usd", "hedged_pnl_usd",
                    "n_stock_hedged", "n_dividends_absent", "n_dividend_missing",
                    "static_share_net", "beyond_static_share_net", "gross_pnl_usd",
                    "static_share_gross", "beyond_static_share_gross",
                    "payoff_delta_usd", "beyond_payoff_usd", "n_hedged", "n_unbounded_above",
                    "n_hard_to_borrow", "n_not_attributed", "n_decision_unmarked",
                    "n_htb_unreadable", "hedgeable_share_gross",
                    "forward_hedged_pnl_usd", "hedge_pnl_usd", "hedge_loss_reduction_usd",
                    "hedge_costs_usd", "top1_share_gross", "top3_share_gross",
                    "loo_sign_stable_pnl", "loo_sign_stable_hedge_pnl", "small_cell",
                    "descriptive_only", "hedge_cost_source")

    @classmethod
    def validate_params(cls, params):
        """Return every problem ``params`` has (default-deny; each owner checks its knobs).

        Parameters
        ----------
        params : dict
            The node's declared params.

        Returns
        -------
        list of str
            Empty when valid.
        """
        if not isinstance(params, dict):
            return [f"params must be a mapping, got {type(params).__name__}"]
        problems = []
        reject_unknown_params(problems, params, cls._PARAMS)
        for owner in cls._OWNERS:
            known = {k: v for k, v in params.items() if k in owner._PARAMS}
            problems += [f"{owner.__name__}: {p}" for p in owner.validate_params(known)]
        for name in _required(problems, params, cls._OWN_PARAMS):
            if name == "exposure_days" and params[name] not in cls.EXPOSURE_DAYS:
                problems.append(f"exposure_days must be one of {list(cls.EXPOSURE_DAYS)}, "
                                f"got {params[name]!r}")
            elif name == "beta_lookback":
                check_int_param(problems, name, params[name], ge=2)
            elif name == "beta_min_pairs":
                check_int_param(problems, name, params[name], ge=1)
        problems += cls._cost_source_problems(params)
        return problems

    @classmethod
    def _cost_source_problems(cls, params):
        """Refuse ``measured`` while the three rates still equal the recorded placeholders."""
        rates = tuple(params.get(k) for k in StaticDeltaHedge._PARAMS)
        if params.get("hedge_cost_source") == "measured" and rates == PLACEHOLDER_HEDGE_RATES:
            return [f"{cls.HEDGE_COST_SOURCE_MISMATCH}: hedge_cost_source is 'measured' but the "
                    f"rates {list(rates)} are the recorded placeholders"]
        return []

    # -- the hooks ---------------------------------------------------------

    @abstractmethod
    def _load_books(self, raw):
        """Return the normalized books of the raw ``books`` input.

        Each book is a dict: ``key`` (unique str), ``symbol``,
        ``decision_date``, ``fill_date``, ``expiry`` (ISO dates), ``pnl``
        (dollars), ``factor`` (the basis divisor at the fill), ``end_factor``
        (at expiry; a difference is ``basis_changed``), ``legs`` (``right``,
        ``strike``, ``sign``, ``quantity``) and ``tags`` (a dict copied into
        the book's row).
        """

    @abstractmethod
    def _load_quotes(self, raw):
        """Return a quote provider for the raw ``quotes`` input.

        The provider has ``chain(symbol, day, expiry)`` returning that
        session's rows for one expiry: ``right``, ``strike``, ``bid``,
        ``ask`` and, for the vendor source, ``iv``.
        """

    @abstractmethod
    def _cell_key(self, book, quotes):
        """Return the dict of cell fields (every ``cell_by`` name) of an attributed book."""

    def _guard_window(self, books):
        """Refuse books outside the data a caller may read (default: accept all)."""

    def _parity_rows(self, book, day, rows):
        """Return the chain rows the parity FORWARD is taken from (default: the rows unchanged).

        Called by :meth:`_greeks_at` for the forward only; the leg-quote lookup and the
        ``duplicate_quote`` rule keep the full ``rows``. A project with its own quote-usability
        rule overrides it (ADR-0310 A2.11); the default leaves a non-MIO project untouched.
        """
        return rows

    def _unbounded_above(self, book, units, f_fill):
        """Say whether a linear hedge of ``units`` held shares struck at ``f_fill`` is unbounded above.

        The one owner of the rule is :func:`~dskit.pipeline.option_structures.structure_max_loss`
        with ``hedge=(units, f_fill)`` (truncated held shares, ADR-0312 Amendment 1): ``inf`` is
        unbounded. The credit does not enter that test, so ``0.0`` is passed; neither the slope rule
        nor the shares-per-unit conversion is restated here.
        """
        legs = [(leg["right"], leg["sign"] * leg["quantity"]) for leg in book["legs"]]
        strikes = [leg["strike"] for leg in book["legs"]]
        loss = structure_max_loss(legs, strikes, 0.0, self.params["multiplier"],
                                  hedge=(units, f_fill))
        return loss == _INF

    def _hedge_dividend(self, book):
        """Return ``(per_share, hedge_status)``: dividends a stock holder gets in ``(fill, expiry]``.

        ``per_share`` is on the ``bars`` basis (the core divides it by the book's
        ``factor``). The default carries no dividends: ``(None, "dividends_absent")``.
        A subclass returns ``(amount, "ok")`` or ``(None, "dividend_missing")`` when a
        session in the window has no usable value.
        """
        return None, self.DIVIDENDS_ABSENT

    # -- the run -----------------------------------------------------------

    def run(self, ctx, inputs):
        """Attribute every book and snapshot the exposure; see the class docstring.

        Parameters
        ----------
        ctx : NodeContext
            Unused.
        inputs : dict
            ``books``, ``quotes``, ``bars``.

        Returns
        -------
        dict
            ``books``, ``cells``, ``exposure`` and ``summary``.

        Raises
        ------
        ValueError
            From a hook, :meth:`_guard_window`, or a malformed book or leg.
        """
        p, bars = self.params, inputs["bars"]
        books = self._load_books(inputs["books"])
        quotes = self._load_quotes(inputs["quotes"])
        self._guard_window(books)
        owned = self._owned()
        self._decision_refusals = Counter()
        rows = [self._book_row(book, quotes, bars) for book in books]
        for i in sorted(range(len(rows)), key=lambda j: books[j]["symbol"]):
            if rows[i]["static_delta_pnl"] is not None:
                rows[i].update(self._forward_columns(books[i], rows[i], quotes, bars))
        attributed = [r for r in rows if r["static_delta_pnl"] is not None]
        cells = [{**cell, "descriptive_only": DESCRIPTIVE_ONLY_NOTE,
                  "hedge_cost_source": p["hedge_cost_source"]}
                 for cell in owned[CellAttribution].summarize(
                     [{k: r[k] for k in (*p["cell_by"], *CellAttribution._FIELDS, "hedged_pnl",
                                         "hedge_status", *CellAttribution._FORWARD_FIELDS)
                       if k in r}
                      for r in attributed], concentration=owned[Concentration])]
        exposure, unmarked, all_one_days, dropped = self._exposure(books, quotes, bars)
        return {"books": rows, "cells": cells, "exposure": exposure,
                "summary": self._summary(rows, attributed, exposure, (unmarked, all_one_days,
                                                                      dropped))}

    def _owned(self):
        """Return the owners built once from this node's params (``{class: instance}``)."""
        if getattr(self, "_tools", None) is None:
            self._tools = {owner: owner({k: v for k, v in self.params.items() if k in owner._PARAMS})
                           for owner in self._OWNERS}
        return self._tools

    def _spot(self, bars, book, day):
        """Return the book's spot on ``day`` on the chain's basis, or ``None``."""
        close = bars.get(book["symbol"], {}).get(day)
        return None if not _is_positive(close) else close / book["factor"]

    def _greeks_at(self, book, day, quotes, bars):
        """Return ``(BookGreeks, None)`` or ``(None, code)`` for the book on ``day``."""
        spot = self._spot(bars, book, day)
        if spot is None:
            return None, self.NO_SPOT
        rows = quotes.chain(book["symbol"], day, book["expiry"])
        if ParityForward.duplicates(rows):
            return None, self.DUPLICATE_QUOTE
        forward, source = self._tools[ParityForward].from_chain(
            self._parity_rows(book, day, rows), spot)
        found = []
        for leg in book["legs"]:
            quote = next((r for r in rows if r.get("right") == leg["right"]
                          and _is_positive(r.get("strike"))
                          and math.isclose(r["strike"], leg["strike"],
                                           rel_tol=1e-9, abs_tol=1e-12)), None)
            row, code = self._tools[LegGreeks].of({**leg, "expiry": book["expiry"]}, quote,
                                                  forward, spot, day)
            if code:
                return None, code
            found.append(row)
        return BookGreeks.sum(found, forward, source), None

    def _book_row(self, book, quotes, bars):
        """Return one book's output row (status ``ok``, ``forward_spot`` or a refusal code)."""
        row = {"id": book["key"], "symbol": book["symbol"], **book["tags"],
               "decision_date": book["decision_date"], "fill_date": book["fill_date"],
               "expiry": book["expiry"], "pnl": book["pnl"], "status": None,
               "forward": None, "forward_source": None, "s_fill": None, "s_settle": None,
               **dict.fromkeys(self._GREEK_FIELDS), "static_delta_pnl": None, "beyond_static_delta": None,
               "basis_usd": None, "iv_disagree_legs": None,
               **dict.fromkeys(("hedge_sh", "hedge_days", "stock_costs", "short_borrow",
                                "dividend_adjust", "hedged_pnl")),
               "hedge_status": self.NOT_ATTRIBUTED,
               **dict.fromkeys(self._FORWARD_COLUMNS),
               "forward_hedge_status": self.NOT_ATTRIBUTED,
               "hedge_cost_source": self.params["hedge_cost_source"]}
        if book["factor"] != book["end_factor"]:
            row["status"] = self.BASIS_CHANGED
            return row
        greeks, code = self._greeks_at(book, book["fill_date"], quotes, bars)
        if code is None and not _is_positive(bars.get(book["symbol"], {}).get(book["expiry"])):
            code = self.NO_SETTLEMENT_BAR
        if code is not None:
            row["status"] = code
            return row
        s_fill = self._spot(bars, book, book["fill_date"])
        s_settle = self._spot(bars, book, book["expiry"])
        stock, residual = StockEquivalent().split(book["pnl"], greeks.delta_sh, s_fill, s_settle)
        row.update({k: getattr(greeks, k) for k in self._GREEK_FIELDS})
        row.update(status=greeks.status, forward=greeks.forward,
                   forward_source=greeks.forward_source, s_fill=s_fill, s_settle=s_settle,
                   static_delta_pnl=stock, beyond_static_delta=residual,
                   basis_usd=StockEquivalent().basis_usd(greeks.delta_sh, s_fill, greeks.forward),
                   iv_disagree_legs=greeks.iv_disagree)
        row.update(self._hedge_columns(book, greeks.delta_sh, s_fill, s_settle))
        row.update(self._payoff_columns(book, s_fill, s_settle), delta_f_sh=greeks.delta_f_sh)
        row.update(self._cell_key(book, quotes))
        return row

    def _payoff_columns(self, book, s_fill, s_settle):
        """Return the expiry payoff-delta split columns (description only)."""
        payoff, atm = self._tools[ExpiryDelta].of(book["legs"], s_settle)
        part, beyond = StockEquivalent().split(book["pnl"], payoff, s_fill, s_settle)
        return {"payoff_delta_sh": payoff, "payoff_delta_pnl": part, "beyond_payoff_delta": beyond,
                "atm_legs": atm}

    def _hard_to_borrow(self, book, bars):
        """Return ``(flag, unreadable)`` from the closes before the DECISION session.

        An unreadable window gives ``(False, True)``: fail-open, kept as built and COUNTED
        (``htb_unreadable``); fail-open versus fail-closed is an open owner item (ADR-0310 A2.3).
        """
        proxy = self._tools[HardToBorrowProxy]
        closes = proxy.closes_before(bars.get(book["symbol"], {}), book["decision_date"])
        return (False, True) if closes is None else (proxy.of(closes), False)

    def _forward_columns(self, book, row, quotes, bars):
        """Return the forward-hedge columns of an attributed book row (ADR-0310 A2.3).

        Sized from the DECISION session's forward delta (truncated once), struck at the fill
        forward ``row["forward"]``, settled at ``row["s_settle"]``; fee and half-spread only. A
        book the decision chain cannot mark is ``decision_unmarked`` (the refusal code is
        counted in the summary).
        """
        forward_fill, s_settle = row["forward"], row["s_settle"]
        flag, unreadable = self._hard_to_borrow(book, bars)
        out = {"hard_to_borrow": flag, "htb_unreadable": unreadable,
               "forward_hedge_status": DECISION_UNMARKED}
        decision, code = self._greeks_at(book, book["decision_date"], quotes, bars)
        if code is not None:
            self._decision_refusals[code] += 1
            return out
        forward, hedge = self._tools[ForwardHedge], self._tools[StaticDeltaHedge]
        units = forward.units(decision.delta_f_sh)
        days = (date.fromisoformat(book["expiry"]) - date.fromisoformat(book["fill_date"])).days
        result = forward.of(book["pnl"], units, forward_fill, s_settle,
                            hedge.carry_costs(units, forward_fill, s_settle, days))
        out.update(delta_f_sh_decision=decision.delta_f_sh, forward_hedge_units=units,
                   hedge_rounding_sh=forward.rounding(units, decision.delta_f_sh),
                   trade_costs_usd=result["trade_costs_usd"], borrow_usd=result["borrow_usd"])
        if units < 0 and out["hard_to_borrow"]:
            out["forward_hedge_status"] = HEDGE_HTB
            return out
        if self._unbounded_above(book, units, forward_fill):
            out["forward_hedge_status"] = HEDGE_UNBOUNDED
        else:
            out.update(forward_hedge_status=HEDGE_OK_FORWARD,
                       **{k: result[k] for k in ("forward_hedge_costs", "forward_hedged_pnl",
                                                 "hedge_pnl")})
        return out

    def _hedge_columns(self, book, delta_sh, s_fill, s_settle):
        """Return the static-hedge columns of an attributed book."""
        per_share, status = self._hedge_dividend(book)
        if status == self.DIVIDEND_MISSING:
            per_share = None
        elif per_share is not None:
            per_share = per_share / book["factor"]
        days = (date.fromisoformat(book["expiry"]) - date.fromisoformat(book["fill_date"])).days
        hedge = self._tools[StaticDeltaHedge].of(
            book["pnl"], delta_sh, s_fill, s_settle, days, per_share)
        return {"hedge_sh": 0.0 - delta_sh, "hedge_days": days,
                "stock_costs": hedge["stock_costs"], "short_borrow": hedge["short_borrow"],
                "dividend_adjust": hedge["dividend_adjust"],
                "hedged_pnl": hedge["hedged_pnl"] if status == self.HEDGE_OK else None,
                "hedge_status": status}

    @staticmethod
    def sign_table(rows, part_field):
        """Return the sign table of a delta part against the book P&L (description only).

        Keys ``pos_pos``, ``pos_neg``, ``neg_pos``, ``neg_neg`` (first the sign of ``part_field``,
        then of ``pnl``) and ``zero`` (either is zero), each ``{n, pnl_usd, gross_pnl_usd}``.
        Rows whose part is null are left out. The table is mechanical: it describes where the P&L
        sat, and no gate reads it.
        """
        names = {1: "pos", -1: "neg"}
        groups = {k: [] for k in ("pos_pos", "pos_neg", "neg_pos", "neg_neg", "zero")}
        for row in rows:
            part = row.get(part_field)
            if part is None:
                continue
            a, b = (part > 0) - (part < 0), (row["pnl"] > 0) - (row["pnl"] < 0)
            groups["zero" if 0 in (a, b) else f"{names[a]}_{names[b]}"].append(row["pnl"])
        return {k: {"n": len(v), "pnl_usd": math.fsum(v), "gross_pnl_usd": math.fsum(abs(x) for x in v)}
                for k, v in groups.items()}

    def direction_read(self, rows):
        """Return the method x quarter block of core-computable fields over ``rows`` (ADR-0310 A2.1.3).

        ``rows`` are one method's book rows of one decision quarter (attributed or not). The child
        adds ``quarter``, ``method`` and its exclusion counts. Returns ``n_books`` (attributed),
        ``n_books_total``, ``pnl_usd``, ``gross_pnl_usd``; ``static`` and ``payoff``
        (``delta_part_usd``, ``beyond_usd``, ``sign_table``); ``forward_hedge`` (the counts and
        dollar effects of :meth:`CellAttribution.hedge_effect`, ``min_books`` and
        ``hedge_cost_source``); ``basis_usd`` (``n, sum, min, p25, median, p75, max,
        abs_sum_over_gross``); ``concentration`` (over the ``ok``-hedged rows, value ``hedge_pnl``),
        ``concentration_pnl`` (over all attributed rows, value ``pnl``) and ``descriptive_only``.
        """
        tools = self._owned()
        attributed = [r for r in rows if r.get("static_delta_pnl") is not None]
        gross = math.fsum(abs(r["pnl"]) for r in attributed)
        ok = [r for r in rows if r.get("forward_hedge_status") == HEDGE_OK_FORWARD]
        min_books = self.params["min_books"]
        effect = tools[CellAttribution].hedge_effect(rows)
        return {
            "n_books": len(attributed), "n_books_total": len(rows),
            "pnl_usd": math.fsum(r["pnl"] for r in attributed), "gross_pnl_usd": gross,
            "static": self._split_part(attributed, "static_delta_pnl", "beyond_static_delta"),
            "payoff": self._split_part(attributed, "payoff_delta_pnl", "beyond_payoff_delta"),
            "forward_hedge": {**effect, "min_books": min_books,
                              "hedge_cost_source": self.params["hedge_cost_source"]},
            "basis_usd": self._basis_distribution(attributed, gross),
            "concentration": {**tools[Concentration].of(ok, "hedge_pnl", ["hedge_pnl"]),
                              "min_books": min_books},
            "concentration_pnl": {**tools[Concentration].of(attributed, "pnl", ["pnl"]),
                                  "min_books": min_books},
            "descriptive_only": DESCRIPTIVE_ONLY_NOTE}

    @classmethod
    def _split_part(cls, attributed, part_field, beyond_field):
        """Return one split's ``delta_part_usd``, ``beyond_usd`` and ``sign_table``."""
        sum_of = CellAttribution._sum_of
        return {"delta_part_usd": sum_of(attributed, part_field),
                "beyond_usd": sum_of(attributed, beyond_field),
                "sign_table": cls.sign_table(attributed, part_field)}

    @staticmethod
    def _basis_distribution(attributed, gross):
        """Return the distribution of ``basis_usd`` over the attributed rows (``sample_quantile``)."""
        values = [r["basis_usd"] for r in attributed if r.get("basis_usd") is not None]
        if not values:
            return dict.fromkeys(("sum", "min", "p25", "median", "p75", "max", "abs_sum_over_gross")) | {"n": 0}
        return {"n": len(values), "sum": math.fsum(values), "min": min(values),
                "p25": sample_quantile(values, 0.25), "median": sample_quantile(values, 0.5),
                "p75": sample_quantile(values, 0.75), "max": max(values),
                "abs_sum_over_gross": math.fsum(abs(v) for v in values) / gross if gross > 0 else None}

    @staticmethod
    def _calendar(bars):
        """Return the sorted sessions any name in ``bars`` has."""
        return sorted({d for series in bars.values() for d in series})

    def _betas(self, bars, day, calendar=None):
        """Return ``(betas, all_one, dropped)``: the class docstring's beta rule at ``day``.

        ``calendar`` is :meth:`_calendar` of ``bars``; the run builds it once and passes it,
        a direct call may omit it.
        """
        p = self.params
        calendar = self._calendar(bars) if calendar is None else calendar
        past = [d for d in calendar if d < day][-p["beta_lookback"] - 1:]
        usable = sorted(s for s in bars if all(_is_positive(bars[s].get(d)) for d in past))
        dropped = sorted(set(bars) - set(usable))
        returns = {s: [math.log(b / a) for a, b in zip(
            [bars[s][d] for d in past], [bars[s][d] for d in past[1:]])] for s in usable}
        fitted = fit_betas(returns, usable, max(0, len(past) - 1), p["beta_min_pairs"])
        betas = {**dict.fromkeys(dropped, 1.0), **fitted}
        return betas, all(v == 1.0 for v in betas.values()), dropped

    def _days(self, books):
        """Return the sorted exposure days the ``exposure_days`` knob asks for."""
        mode, days = self.params["exposure_days"], set()
        for book in books:
            if mode in ("decision", "both"):
                days.add(book["decision_date"])
            if mode in ("fill", "both"):
                days.add(book["fill_date"])
        return sorted(days)

    def _exposure(self, books, quotes, bars):
        """Return ``(rows, unmarked, all_one_days, dropped_days)``: rows and the three day counts."""
        snapshot, rows, unmarked, all_one_days = self._tools[ExposureSnapshot], [], 0, 0
        dropped_days, calendar = 0, self._calendar(bars)
        for day in self._days(books):
            betas, all_one, dropped = self._betas(bars, day, calendar)
            all_one_days += all_one
            dropped_days += len(dropped)
            open_books = [b for b in books if b["fill_date"] <= day < b["expiry"]]
            items = [None] * len(open_books)
            for i in sorted(range(len(open_books)), key=lambda j: open_books[j]["symbol"]):
                items[i], marked = self._open_item(open_books[i], day, quotes, bars)
                unmarked += not marked
            snap = snapshot.of(betas, items)
            rows += self._day_rows(day, snap)
        return rows, unmarked, all_one_days, dropped_days

    def _open_item(self, book, day, quotes, bars):
        """Return ``(item, marked)``: the open book's exposure item on ``day`` (read per symbol)."""
        item = {"symbol": book["symbol"], "dte": (date.fromisoformat(book["expiry"])
                                                 - date.fromisoformat(day)).days}
        if book["factor"] != book["end_factor"]:
            item["status"] = self.BASIS_CHANGED
            return item, True
        greeks, code = self._greeks_at(book, day, quotes, bars)
        if code:
            item["status"] = self.OPEN_LEG_UNMARKED
            return item, False
        item.update(status=greeks.status, spot=self._spot(bars, book, day),
                    **{k: getattr(greeks, k) for k in self._GREEK_FIELDS})
        return item, True

    def _day_rows(self, day, snap):
        """Return the day's bucket rows and its total row."""
        rows = [{"date": day, "bucket": f"[{b['dte_min']},{b['dte_max']})", **b,
                 "top_k_share": None, "excluded": {}} for b in snap["buckets"]]
        rows.append({"date": day, "bucket": self.TOTAL_BUCKET, "dte_min": None, "dte_max": None,
                     **snap["total"], "top_k_share": snap["top_k_share"],
                     "excluded": snap["excluded"]})
        return rows

    def _summary(self, rows, attributed, exposure, day_counts):
        """Return the counts dict: attribution totals, refusals, flags, exposure days.

        ``day_counts``: ``(unmarked book-days, all-one-beta days, dropped name-days)``.
        """
        unmarked, all_one_days, dropped_days = day_counts
        refusals = Counter(r["status"] for r in rows if r["static_delta_pnl"] is None)
        good = math.fsum(r["pnl"] for r in attributed)
        bad = math.fsum(r["pnl"] for r in rows if r["static_delta_pnl"] is None)
        hedged = [r["hedged_pnl"] for r in attributed if r["hedged_pnl"] is not None]
        hedge_params = {k: self.params[k] for k in StaticDeltaHedge._PARAMS}
        return {"books": len(rows), "attributed": len(attributed),
                "unattributed": len(rows) - len(attributed),
                "attributed_pnl_usd": good, "unattributed_pnl_usd": bad,
                "total_pnl_usd": math.fsum(r["pnl"] for r in rows),
                "refusals": dict(sorted(refusals.items())),
                "forward_spot": sum(r["status"] == BookGreeks.FORWARD_SPOT for r in rows),
                "hedged_pnl_usd": math.fsum(hedged) if hedged else None,
                "n_stock_hedged": len(hedged),
                "hedge_cost_source": self.params["hedge_cost_source"],
                "stock_hedge_note": STOCK_HEDGE_NOTE,
                "forward_hedge_status": dict(sorted(Counter(
                    r["forward_hedge_status"] for r in rows).items())),
                "decision_greeks_refusals": dict(sorted(self._decision_refusals.items())),
                "htb_unreadable": sum(bool(r["htb_unreadable"]) for r in rows),
                "hedge_status": dict(sorted(Counter(r["hedge_status"] for r in rows).items())),
                "hedge_dividend_source": self.hedge_dividend_source,
                "hedge_params": hedge_params,
                "exposure_days": len({r["date"] for r in exposure}),
                "open_leg_unmarked": unmarked, "beta_all_one_days": all_one_days,
                "beta_dropped_name_days": dropped_days,
                "iv_disagree_legs": sum(r["iv_disagree_legs"] or 0 for r in attributed),
                "rate": self.params["rate"], "iv_source": self.params.get("iv_source",
                                                                           DEFAULT_IV_SOURCE)}


# ---- ADR-0311 Amendment 1 part A: the direction edge ---------------------------------------
class DirectionEdge(_Configured):
    """The drift part of one item's edge: ``e_dir = delta_f_sh x (m_F - F_t)``.

    ``m_F`` is the forecast's mean, ``F_t`` the decision-session parity forward
    and ``delta_f_sh`` the Black-76 FORWARD delta of :class:`LegGreeks` (already
    times ``sign x quantity x multiplier``; nothing is re-derived here). Every
    input is the decision session's: there is no fill-session argument. The
    forward's standard error ``F_se`` comes from the dispersion of the parity
    pairs, and ``sigma2_usd = (delta_f_sh x F_se)^2`` is the error variance the
    state turns into a reliability ratio. ``rate`` must be 0.0 (the edge is
    undiscounted and the parity forward is rate 0).

    Parameters
    ----------
    params : dict
        :attr:`LegGreeks._PARAMS` and :attr:`ParityForward._PARAMS` (their
        rules; ``rate`` must be 0.0) plus ``forward_se_floor_frac`` (number > 0,
        required) — the floor on ``F_se`` as a fraction of the forward.

    Examples
    --------
    A bought call, forecast mean 101.4 against a parity forward of 101::

        de = DirectionEdge({"rate": 0.0, "multiplier": 100, "min_years": 0.001,
                            "vol_floor": 0.01, "parity_strikes": 5, "min_pairs": 2,
                            "max_pair_spread_frac": 0.5, "forward_se_floor_frac": 0.001})
        m_f = de.forecast_mean([0.5, 0.5], [101.0, 101.8])     # 101.4
        out, code = de.of(item, quote, usable_rows, 100.5, m_f, "2024-03-01")
    """

    _PARAMS = LegGreeks._PARAMS + ParityForward._PARAMS + ("forward_se_floor_frac",)
    NO_DIRECTION_EDGE = "no_direction_edge"
    RATE_NONZERO = "direction_rate_nonzero"
    #: Leg refusal -> the sub-code counted beside ``no_direction_edge``.
    _SUBCODES = {LegGreeks.NO_FILL_QUOTE: "no_quote", LegGreeks.NO_IV: LegGreeks.NO_IV,
                 LegGreeks.BELOW_MIN_YEARS: LegGreeks.BELOW_MIN_YEARS}
    #: Normal-consistent scale of the median absolute deviation.
    MAD_SCALE = 1.4826
    #: Fewer pairs than this cannot give a dispersion.
    MIN_DISPERSION_PAIRS = 3

    @classmethod
    def _check(cls, problems, params):
        for owner in (LegGreeks, ParityForward):
            problems.extend(owner.validate_params(
                {k: v for k, v in params.items() if k in owner._PARAMS}))
        if "rate" in params and params["rate"] != 0.0:
            problems.append(f"{cls.RATE_NONZERO}: rate must be 0.0, got {params['rate']!r}")
        if "forward_se_floor_frac" in params and not _is_positive(params["forward_se_floor_frac"]):
            problems.append("forward_se_floor_frac must be a positive finite number, got "
                            f"{params['forward_se_floor_frac']!r}")
        _required(problems, params, ("forward_se_floor_frac",))

    def __init__(self, params=None):
        super().__init__(params)
        self._legs = LegGreeks({k: v for k, v in self.params.items() if k in LegGreeks._PARAMS})
        self._parity = ParityForward(
            {k: v for k, v in self.params.items() if k in ParityForward._PARAMS})

    @staticmethod
    def forecast_mean(masses, grid):
        """Return ``m_F = sum masses x grid`` (no renormalisation).

        Raises
        ------
        ValueError
            On empty or unequal-length inputs or a non-finite entry.
        """
        if not masses or len(masses) != len(grid):
            raise ValueError("masses and grid must be non-empty and the same length, got "
                             f"{len(masses)} and {len(grid)}")
        if not all(number_ok(v) for v in (*masses, *grid)):
            raise ValueError("masses and grid must be finite numbers")
        return math.fsum(m * g for m, g in zip(masses, grid))

    def _forward_se(self, pairs, forward, name_week_se):
        """Return ``(F_se, fallback)``: pair dispersion, or the fallback with the floor."""
        floor = self.params["forward_se_floor_frac"] * forward
        if len(pairs) >= self.MIN_DISPERSION_PAIRS:
            centre = median(pairs)
            mad = median(abs(p - centre) for p in pairs)
            return max(self.MAD_SCALE * mad / math.sqrt(len(pairs)), floor), False
        if name_week_se is not None and _is_positive(name_week_se):
            return max(name_week_se, floor), True
        return floor, True

    def of(self, item, quote, usable_rows, spot, forecast_mean, day, name_week_se=None):
        """Return ``(row, None)`` or ``(None, "no_direction_edge:<sub>")`` for one item.

        Parameters
        ----------
        item : dict
            ``right``, ``strike``, ``sign`` (+1 bought / -1 sold), ``expiry``;
            one contract is valued (``quantity`` 1).
        quote : dict or None
            The item's decision-session quote: ``bid``, ``ask``, ``iv``.
        usable_rows : list of dict
            The decision-session chain rows of the item's expiry, already
            through the caller's quote filter.
        spot : float
            Positive decision-session underlying price.
        forecast_mean : float
            ``m_F`` from :meth:`forecast_mean`.
        day : str
            ISO decision date.
        name_week_se : float, optional
            Median ``F_se`` of the same name's other expiries that day: the
            stand-in for the dispersion when fewer than 3 pairs exist.

        Returns
        -------
        tuple
            A dict ``e_dir, forward, forward_source, forward_se, forward_se_fallback,
            sigma2_usd, delta_f_sh`` (``forward_se`` and ``sigma2_usd`` are ``None`` on
            a spot forward) and ``None``; or ``None`` and the code. Sub-codes:
            ``no_quote``, ``leg_no_iv``, ``leg_below_min_years``, ``duplicate_quote``.

        Raises
        ------
        ValueError
            On a non-positive ``spot``, a non-finite ``forecast_mean`` or a malformed item.
        """
        if not number_ok(forecast_mean):
            raise ValueError(f"forecast_mean must be a finite number, got {forecast_mean!r}")
        try:
            pairs = self._parity.pairs(usable_rows, spot)
        except ValueError as exc:
            if str(exc).startswith(ParityForward.DUPLICATE_QUOTE):
                return None, f"{self.NO_DIRECTION_EDGE}:{ParityForward.DUPLICATE_QUOTE}"
            raise
        forward, source = self._parity.from_chain(usable_rows, spot)
        leg = {"right": item.get("right"), "strike": item.get("strike"),
               "sign": item.get("sign"), "quantity": 1, "expiry": item.get("expiry")}
        greeks, refusal = self._legs.of(leg, quote, forward, spot, day)
        if greeks is None:
            return None, f"{self.NO_DIRECTION_EDGE}:{self._SUBCODES[refusal]}"
        delta = greeks["delta_f_sh"]
        spot_source = source == ParityForward.SOURCE_SPOT
        se, fallback = (None, False) if spot_source else self._forward_se(pairs, forward,
                                                                          name_week_se)
        return {"e_dir": delta * (forecast_mean - forward), "forward": forward,
                "forward_source": source, "forward_se": se, "forward_se_fallback": fallback,
                "sigma2_usd": None if se is None else (delta * se) ** 2,
                "delta_f_sh": delta}, None
