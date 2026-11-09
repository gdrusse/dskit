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
  stock part and a residual that holds everything else.
* :class:`StaticDeltaHedge` — the book's entry delta hedged once with stock, with
  costs, short borrow and dividends: the P&L left once the delta is gone.
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
from dataclasses import dataclass
from datetime import date
from statistics import median

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
from dskit.pipeline.records import number_ok, price_ok

__all__ = ["BookGreeks", "BookGreeksReport", "CellAttribution", "ExposureSnapshot", "LegGreeks",
           "ParityForward", "StaticDeltaHedge", "StockEquivalent", "fit_betas"]

#: Calendar days per year: the one divisor for years-to-expiry and per-day theta.
DAYS_PER_YEAR = 365
#: Where a leg's volatility comes from.
IV_SOURCES = ("vendor", "mid")
#: The default volatility source (vendor ``iv``); named once.
DEFAULT_IV_SOURCE = IV_SOURCES[0]
#: The forward sources a chain gives.
FORWARD_SOURCES = ("parity", "spot")
_INF = float("inf")
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

    def from_chain(self, rows, spot):
        """Return ``(forward, source)`` for one expiry's chain rows.

        Parameters
        ----------
        rows : list of dict
            One expiry's quotes: ``right`` (``"call"``/``"put"``), ``strike``,
            ``bid``, ``ask``.
        spot : float
            Positive underlying price: the fallback forward and the centre.

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
        if not _is_positive(spot):
            raise ValueError(f"spot must be a positive finite number, got {spot!r}")
        duplicated = self.duplicates(rows)
        if duplicated:
            raise ValueError(f"{self.DUPLICATE_QUOTE}: {duplicated}")
        mids = {}
        for row in rows:
            right, strike = row.get("right"), row.get("strike")
            if right in RIGHTS and _is_positive(strike):
                mid = self._mid(row)
                if mid is not None:
                    mids.setdefault(strike, {})[right] = mid
        pairs = sorted((k for k, legs in mids.items() if len(legs) == len(RIGHTS)),
                       key=lambda k: (abs(k - spot), k))
        forwards = [k + mids[k]["call"] - mids[k]["put"]
                    for k in pairs[:self.params["parity_strikes"]]]
        if len(forwards) < self.params["min_pairs"]:
            return float(spot), self.SOURCE_SPOT
        forward = median(forwards)
        if not _is_positive(forward):
            return float(spot), self.SOURCE_SPOT
        return float(forward), self.SOURCE_PARITY


class LegGreeks(_Configured):
    """One leg's Greeks in spot terms and position units.

    ``delta_sh`` is shares, ``gamma_sh`` shares per dollar of spot,
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
            A dict ``delta_sh, gamma_sh, vega_usd_per_pt, theta_usd_per_day,
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
                  for k in ("delta_sh", "gamma_sh", "vega_usd_per_pt", "theta_usd_per_day")}
        status = cls.OK if forward_source == ParityForward.SOURCE_PARITY else cls.FORWARD_SPOT
        return cls(**totals, forward=float(forward), forward_source=forward_source,
                   status=status, iv_disagree=sum(bool(leg.get("iv_disagree")) for leg in legs))


class StockEquivalent(_Configured):
    """Split a book's P&L into a delta-equivalent stock part and a residual.

    ``stock_pnl = entry_delta_sh x (s_settle - s_fill [+ dividends])`` and
    ``residual = book_pnl - stock_pnl``. The residual holds gamma, vega, theta,
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
        """Return ``(stock_pnl, residual)``; the two sum to ``book_pnl``.

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
        if not number_ok(hedge_days) or hedge_days < 0:
            raise ValueError(f"hedge_days must be a finite number >= 0, got {hedge_days!r}")
        dividend = 0.0 if dividends_per_share is None else dividends_per_share
        equivalent = StockEquivalent()
        price_stock, _ = equivalent.split(book_pnl, entry_delta_sh, s_fill, s_settle)
        total_stock, residual = equivalent.split(book_pnl, entry_delta_sh, s_fill, s_settle,
                                                 dividends_per_share=dividend)
        p = self.params
        costs = abs(entry_delta_sh) * (2 * p["hedge_fee_per_share"]
                                       + (s_fill + s_settle) * p["hedge_half_spread_bps"] / self._BPS)
        borrow = (max(entry_delta_sh, 0.0) * s_fill * p["hedge_borrow_rate"] * hedge_days
                  / DAYS_PER_YEAR)
        return {"stock_costs": costs, "short_borrow": borrow,
                "dividend_adjust": None if dividends_per_share is None else total_stock - price_stock,
                "hedged_pnl": residual - costs - borrow}


class CellAttribution(_Configured):
    """Sums and shares of the stock/residual split per cell.

    Per cell: ``n``, ``pnl_usd``, ``stock_pnl_usd``, ``residual_usd``; the
    optional row field ``hedged_pnl`` is summed to ``hedged_pnl_usd`` (``None``
    when any row's is ``None`` or absent) with ``n_hedged`` rows counted; the net
    shares ``stock_share_net`` / ``residual_share_net`` (part over the cell
    total, ``None`` when ``|total| < min_total_usd``); and ``gross_pnl_usd``
    (``sum |pnl|``) with ``stock_share_gross`` / ``residual_share_gross`` (part
    over the gross, ``None`` when the gross is zero), which cannot explode
    where the total crosses zero.

    Parameters
    ----------
    params : dict
        ``cell_by`` (non-empty list of row field names, required) — the cell
        key. ``min_total_usd`` (number >= 0, required).

    Examples
    --------
    Group book rows by name and expiry rank::

        ca = CellAttribution({"cell_by": ["symbol", "rank"], "min_total_usd": 1.0})
        cells = ca.summarize(rows)   # rows: symbol, rank, pnl, stock_pnl, residual
    """

    _PARAMS = ("cell_by", "min_total_usd")
    CELL_FIELD_MISSING = "cell_field_missing"
    PNL_NOT_FINITE = "pnl_not_finite"
    REFUSALS = (CELL_FIELD_MISSING, PNL_NOT_FINITE)
    _FIELDS = ("pnl", "stock_pnl", "residual")
    _OPTIONAL = "hedged_pnl"

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

    def summarize(self, rows):
        """Return the cell rows, sorted by key.

        Parameters
        ----------
        rows : list of dict
            Each carries every ``cell_by`` field and finite ``pnl``,
            ``stock_pnl`` and ``residual`` (dollars).

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
            if not all(number_ok(row.get(f)) for f in self._FIELDS) or (
                    hedged is not None and not number_ok(hedged)):
                raise ValueError(f"{self.PNL_NOT_FINITE}: "
                                 f"{[row.get(f) for f in (*self._FIELDS, self._OPTIONAL)]}")
            cells.setdefault(tuple(row[f] for f in by), []).append(row)
        return [self._cell(by, key, group)
                for key, group in sorted(cells.items(), key=lambda kv: tuple(map(str, kv[0])))]

    def _cell(self, by, key, group):
        """Return one cell's row."""
        total, stock, resid = (math.fsum(r[f] for r in group) for f in self._FIELDS)
        gross = math.fsum(abs(r["pnl"]) for r in group)
        hedged = [r.get(self._OPTIONAL) for r in group]
        n_hedged = sum(h is not None for h in hedged)
        net_ok = abs(total) >= self.params["min_total_usd"] and total != 0
        return {
            **dict(zip(by, key)), "n": len(group), "pnl_usd": total,
            "stock_pnl_usd": stock, "residual_usd": resid,
            "hedged_pnl_usd": math.fsum(hedged) if n_hedged == len(group) else None,
            "n_hedged": n_hedged,
            "stock_share_net": stock / total if net_ok else None,
            "residual_share_net": resid / total if net_ok else None,
            "gross_pnl_usd": gross,
            "stock_share_gross": stock / gross if gross > 0 else None,
            "residual_share_gross": resid / gross if gross > 0 else None,
        }


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
    maps its own saved files to the generic inputs by three hooks
    (:meth:`_load_books`, :meth:`_load_quotes`, :meth:`_cell_key`) and may
    refuse a read through :meth:`_guard_window`.

    Per book (a normalized dict, see :meth:`_load_books`): the Greeks at the
    fill session on a parity forward, ``stock_pnl`` / ``residual`` against the
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
    _OWNERS = (LegGreeks, ParityForward, CellAttribution, ExposureSnapshot, StaticDeltaHedge)
    _OWN_PARAMS = ("exposure_days", "beta_lookback", "beta_min_pairs")
    _PARAMS = tuple(dict.fromkeys(name for owner in _OWNERS for name in owner._PARAMS)) + _OWN_PARAMS
    EXPOSURE_DAYS = ("decision", "fill", "both")
    BASIS_CHANGED = "basis_changed"
    NO_SPOT = "no_spot"
    HEDGE_OK, DIVIDENDS_ABSENT, DIVIDEND_MISSING, NOT_ATTRIBUTED = (
        "ok", "dividends_absent", "dividend_missing", "not_attributed")
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
                    "stock_pnl", "residual", "basis_usd", "iv_disagree_legs", "hedge_sh",
                    "hedge_days", "stock_costs", "short_borrow", "dividend_adjust", "hedged_pnl",
                    "hedge_status")
    EXPOSURE_COLUMNS = ("date", "bucket", "dte_min", "dte_max", "n_books", "beta_delta_usd",
                        "vega_usd_per_pt", "gamma_usd_per_1pct", "top_k_share", "excluded")
    CELL_COLUMNS = ("n", "pnl_usd", "stock_pnl_usd", "residual_usd", "hedged_pnl_usd", "n_hedged",
                    "stock_share_net",
                    "residual_share_net", "gross_pnl_usd", "stock_share_gross",
                    "residual_share_gross")

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
        return problems

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
        owned = {owner: owner({k: v for k, v in p.items() if k in owner._PARAMS})
                 for owner in self._OWNERS}
        self._tools = owned
        rows = [self._book_row(book, quotes, bars) for book in books]
        attributed = [r for r in rows if r["stock_pnl"] is not None]
        cells = owned[CellAttribution].summarize(
            [{k: r[k] for k in (*p["cell_by"], "pnl", "stock_pnl", "residual", "hedged_pnl") if k in r}
             for r in attributed])
        exposure, unmarked, all_one_days, dropped = self._exposure(books, quotes, bars)
        return {"books": rows, "cells": cells, "exposure": exposure,
                "summary": self._summary(rows, attributed, exposure, (unmarked, all_one_days,
                                                                      dropped))}

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
        forward, source = self._tools[ParityForward].from_chain(rows, spot)
        found = []
        for leg in book["legs"]:
            quote = next((r for r in rows if r.get("right") == leg["right"]
                          and math.isclose(r.get("strike", math.nan), leg["strike"],
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
               **dict.fromkeys(self._GREEK_FIELDS), "stock_pnl": None, "residual": None,
               "basis_usd": None, "iv_disagree_legs": None,
               **dict.fromkeys(("hedge_sh", "hedge_days", "stock_costs", "short_borrow",
                                "dividend_adjust", "hedged_pnl")),
               "hedge_status": self.NOT_ATTRIBUTED}
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
                   stock_pnl=stock, residual=residual,
                   basis_usd=StockEquivalent().basis_usd(greeks.delta_sh, s_fill, greeks.forward),
                   iv_disagree_legs=greeks.iv_disagree)
        row.update(self._hedge_columns(book, greeks.delta_sh, s_fill, s_settle))
        row.update(self._cell_key(book, quotes))
        return row

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
        missing = status == self.DIVIDEND_MISSING
        return {"hedge_sh": 0.0 - delta_sh, "hedge_days": days,
                "stock_costs": hedge["stock_costs"], "short_borrow": hedge["short_borrow"],
                "dividend_adjust": hedge["dividend_adjust"],
                "hedged_pnl": None if missing else hedge["hedged_pnl"], "hedge_status": status}

    def _betas(self, bars, day):
        """Return ``(betas, all_one, dropped)``: the class docstring's beta rule at ``day``."""
        p = self.params
        calendar = sorted({d for series in bars.values() for d in series})
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
        dropped_days = 0
        for day in self._days(books):
            betas, all_one, dropped = self._betas(bars, day)
            all_one_days += all_one
            dropped_days += len(dropped)
            open_books = []
            for book in (b for b in books if b["fill_date"] <= day < b["expiry"]):
                item = {"symbol": book["symbol"], "dte": (date.fromisoformat(book["expiry"])
                                                         - date.fromisoformat(day)).days}
                if book["factor"] != book["end_factor"]:
                    item["status"] = self.BASIS_CHANGED
                else:
                    greeks, code = self._greeks_at(book, day, quotes, bars)
                    if code:
                        item["status"] = self.OPEN_LEG_UNMARKED
                        unmarked += 1
                    else:
                        item.update(status=greeks.status, spot=self._spot(bars, book, day),
                                    **{k: getattr(greeks, k) for k in self._GREEK_FIELDS})
                open_books.append(item)
            snap = snapshot.of(betas, open_books)
            rows += self._day_rows(day, snap)
        return rows, unmarked, all_one_days, dropped_days

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
        refusals = Counter(r["status"] for r in rows if r["stock_pnl"] is None)
        good = math.fsum(r["pnl"] for r in attributed)
        bad = math.fsum(r["pnl"] for r in rows if r["stock_pnl"] is None)
        hedged = [r["hedged_pnl"] for r in attributed]
        hedge_params = {k: self.params[k] for k in StaticDeltaHedge._PARAMS}
        return {"books": len(rows), "attributed": len(attributed),
                "unattributed": len(rows) - len(attributed),
                "attributed_pnl_usd": good, "unattributed_pnl_usd": bad,
                "total_pnl_usd": math.fsum(r["pnl"] for r in rows),
                "refusals": dict(sorted(refusals.items())),
                "forward_spot": sum(r["status"] == BookGreeks.FORWARD_SPOT for r in rows),
                "hedged_pnl_usd": None if None in hedged else math.fsum(hedged),
                "hedge_status": dict(sorted(Counter(r["hedge_status"] for r in rows).items())),
                "hedge_dividend_source": self.hedge_dividend_source,
                "hedge_params": hedge_params,
                "exposure_days": len({r["date"] for r in exposure}),
                "open_leg_unmarked": unmarked, "beta_all_one_days": all_one_days,
                "beta_dropped_name_days": dropped_days,
                "iv_disagree_legs": sum(r["iv_disagree_legs"] or 0 for r in attributed),
                "rate": self.params["rate"], "iv_source": self.params.get("iv_source",
                                                                           DEFAULT_IV_SOURCE)}
