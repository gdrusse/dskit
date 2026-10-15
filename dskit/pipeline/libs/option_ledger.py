"""The option-structure ledger: open at NBBO sides, mark at the mid, close by a declared rule (ADR-0265).

:class:`OptionStructureLedger` (role ``transform``, by import path, ``NODE_KINDS`` empty) turns a
stream of chain rows and a stream of structure orders into a per-trade record and a daily mark
series, deterministically and with every refusal counted. A structure is a *leg set*
(``(right, signed quantity)`` pairs, declared by the document's ``structures`` map) with one strike
per leg and one expiration; the leg algebra is :mod:`dskit.pipeline.option_structures`.

The rules, fixed by the node and not by the data:

* **Open** at the NBBO of the open date: a bought leg fills at the ask, a sold leg at the bid
  (``fill: "mid"`` is a sensitivity switch, never the default). The order is REFUSED, and counted
  under one reason, when the symbol has no rows that day, a leg has no row, a quote is not finite,
  negative, crossed or locked, a sold leg has no bid or a bought leg no ask.
* **Mark** each later date of the chain's calendar at the NBBO mid (``close`` is never a mark). A
  trade that cannot be marked on a day (no rows, a leg missing, an unusable quote) carries the last
  mark forward and the row says so (``carried`` and ``carry_reason``).
* **Close** by the ledger's ``close_rule``. ``expiry``: settle intrinsic on the expiration date
  from the chain's underlying column. ``exit``: the order names a later date and the legs are
  closed at the NBBO that day (sell what was bought at the bid, buy back what was sold at the ask),
  under the open's side rules for the closing side: a short with no ask is never a free buy-back, and
  a long with no bid refuses the exit (``exit_zero_bid_long``) unless ``exit_zero_bid: "allow"``
  closes it for nothing and counts it. An exit that cannot be
  quoted is counted and the trade runs on to its expiration. A trade whose expiry has no underlying
  is ``unsettled``; one the data ends before is ``open_at_end``. Neither has a realized P&L.
* **Delta hedge** (optional ``hedge``): Black-76 deltas from each leg's own mid-implied vol
  (:func:`~dskit.pipeline.option_pricing.black76_implied_vol`, forward ``S exp(rate T)``), the
  hedge held in the underlying column's price and rebalanced by a declared rule. The hedge pays no
  financing; its P&L is ``shares held * change in the underlying``.

The calendar is the union of dates the chain shows for the ordered symbols. When rows of one symbol and day
disagree on the underlying, the first in input order is used and the disagreement is counted. The chain is read once
and only the contracts and dates the orders need are kept. Settlement is European and cash-like: no
early exercise, no dividend and no delivery model (the child's American charge is not applied here).

Import cost: stdlib and dskit only.
"""

import bisect
import math
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date

from dskit.pipeline.document import is_node_ref
from dskit.pipeline.node import Node, check_int_param, reject_unknown_params
from dskit.pipeline.option_pricing import RIGHTS, black76_delta, black76_implied_vol
from dskit.pipeline.option_structures import (
    leg_intrinsic,
    quote_defect,
    quote_defects,
    structure_credit,
    structure_max_loss,
    structure_payoff,
)
from dskit.pipeline.records import number_ok, price_ok

__all__ = ["CHAIN_FIELDS", "EXIT_ZERO_BID", "FILLS", "NODE_KINDS", "ORDER_FIELDS", "REBALANCES",
           "OptionStructureLedger"]

NODE_KINDS = ()

#: The chain columns a document names, in the order the intake reads them.
CHAIN_FIELDS = ("symbol", "date", "expiration", "strike", "right", "bid", "ask", "underlying")
#: The order columns: the exit date is required exactly when ``close_rule`` is ``exit``.
ORDER_FIELDS = ("date", "symbol", "expiration", "strikes", "structure", "exit")
#: The fill models: the NBBO sides (default) or the mid.
FILLS = ("nbbo", "mid")
#: The hedge rebalance rules.
REBALANCES = ("daily", "every_n", "threshold")
#: What an exit does with a long leg that has no bid: refuse (default) or close it at nothing.
EXIT_ZERO_BID = ("refuse", "allow")
_CLOSE_RULES = ("expiry", "exit")
_RIGHT_NAMES = {"put": "put", "p": "put", "call": "call", "c": "call"}


def _day(value):
    """Return a ``datetime.date`` for a date or an ISO ``YYYY-MM-DD`` string, else ``None``."""
    if type(value) is date:
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError:
            return None
    return None


def _day_memo(memo, value):
    """:func:`_day` through ``memo``, a ``[cell, answer]`` pair holding only the latest exact-``str`` cell.

    A ``str`` is immutable, so a repeat of the held cell has the answer it had
    (``None`` included); any other type takes :func:`_day` every time. One cell
    at most: O(1) memory however many distinct cells the pass reads, and a run
    of one date (a sorted chain's day or expiration) parses once.
    """
    if type(value) is not str:
        return _day(value)
    if value != memo[0]:
        memo[0], memo[1] = value, _day(value)
    return memo[1]


def _number(value):
    """Return ``float(value)`` for a finite non-bool number, else ``None``."""
    if type(value) is float:
        return value if math.isfinite(value) else None
    return float(value) if number_ok(value) else None


@dataclass(frozen=True)
class _Order:
    """One validated order: what to open, where it ends."""

    index: int
    symbol: str
    opened: date
    expiration: date
    exit: object
    structure: str
    legs: tuple
    strikes: tuple


class _Chain:
    """The slice of the chain the orders need: quotes, underlying prices and the calendar."""

    def __init__(self):
        self.quotes = {}
        self.under = {}
        self.seen = set()
        self.days = set()
        self.sorted_days = []
        self.dropped = Counter()
        self.conflicts = 0
        self.rows_in = 0
        self.rows_used = 0

    def finish(self):
        """Freeze the calendar once the stream is read."""
        self.sorted_days = sorted(self.days)

    def window(self, after, until):
        """Return the calendar dates ``d`` with ``after < d <= until``, ascending."""
        lo = bisect.bisect_right(self.sorted_days, after)
        hi = bisect.bisect_right(self.sorted_days, until)
        return self.sorted_days[lo:hi]

    def covers(self, day):
        """Whether the calendar reaches ``day`` (some date on or after it was seen)."""
        return bool(self.sorted_days) and self.sorted_days[-1] >= day

    def underlying(self, symbol, day):
        """Return the underlying price of ``symbol`` on ``day``, or ``None``."""
        return self.under.get((symbol, day))

    def has_rows(self, symbol, day):
        """Whether the chain has any row for ``symbol`` on ``day``."""
        return (symbol, day) in self.seen

    def quote(self, symbol, day, key):
        """Return the raw ``(bid, ask)`` of a contract on ``day``, or ``None`` when it has no row."""
        return self.quotes.get((symbol, day, key))


class _Hedge:
    """The delta hedge of one trade: shares held, their P&L and fees, and the rebalance rule."""

    def __init__(self, spec, year_days, multiplier, contracts):
        self.spec = spec
        self.rate = float(spec.get("rate", 0.0))
        self.fee = float(spec.get("fee_per_share", 0.0))
        self.year_days = year_days
        self.scale = multiplier * contracts
        self.shares = 0.0
        self.last_under = None
        self.pnl = 0.0
        self.fees = 0.0
        self.steps = 0

    def accrue(self, under):
        """Book the held shares' P&L from the last underlying to ``under``."""
        if under is not None:
            if self.last_under is not None:
                self.pnl += self.shares * (under - self.last_under)
            self.last_under = under

    def due(self, target):
        """Whether the rule rebalances today toward ``target`` shares."""
        rule = self.spec["rebalance"]
        if rule == "daily":
            return True
        if rule == "every_n":
            return self.steps % int(self.spec["every_n"]) == 0
        return abs(target - self.shares) > self.spec["threshold"]

    def rebalance(self, target):
        """Trade to ``target`` shares, paying the per-share fee on the change."""
        self.fees += self.fee * abs(target - self.shares)
        self.shares = target

    def flatten(self):
        """Close the hedge at the last underlying, paying the fee."""
        self.fees += self.fee * abs(self.shares)
        self.shares = 0.0

    def delta(self, legs, strikes, expiration, day, under, mids):
        """``(target shares, None)`` from the legs' mid-implied deltas, else ``(None, reason)``."""
        years = (expiration - day).days / self.year_days
        if years <= 0:
            return None, "no_time_to_expiry"
        if not price_ok(under):
            return None, "no_underlying"
        forward = under * math.exp(self.rate * years)
        total = 0.0
        for (right, sign), strike, mid in zip(legs, strikes, mids, strict=True):
            vol = black76_implied_vol(right, mid, forward, strike, years, self.rate)
            if vol is None:
                return None, "no_implied_vol"
            spot_delta = black76_delta(right, forward, strike, vol, years, self.rate) * math.exp(
                self.rate * years)
            total += sign * spot_delta
        return -total * self.scale, None


class _Trade:
    """One order's life: open, mark, hedge, close. ``run`` returns its record and its mark rows."""

    def __init__(self, ledger, chain, order, tally):
        self.p = ledger.params
        self.chain = chain
        self.order = order
        self.tally = tally
        self.mult = self.p["multiplier"]
        self.n = int(self.p.get("contracts", 1))
        self.legs = order.legs
        self.units = sum(abs(sign) for _right, sign in order.legs)
        self.keys = tuple((order.expiration, k, right)
                          for (right, _sign), k in zip(order.legs, order.strikes, strict=True))
        self.hedge = (_Hedge(self.p["hedge"], int(self.p.get("year_days", 365)), self.mult, self.n)
                      if self.p.get("hedge") else None)
        self.marks = []
        self.premium = 0.0
        self.open_fees = 0.0
        self.last_value = 0.0
        self.carried_days = 0

    # -- quotes ------------------------------------------------------------------------

    def _quotes(self, day):
        """``(quotes, None)`` for every leg on ``day``, else ``(None, reason)``."""
        symbol = self.order.symbol
        if not self.chain.has_rows(symbol, day):
            return None, "no_rows"
        quotes = [self.chain.quote(symbol, day, key) for key in self.keys]
        if any(q is None for q in quotes):
            return None, "leg_not_quoted"
        return quotes, None

    def _usable(self, day):
        """``(quotes, None)`` when every leg quotes usably (locked allowed), else ``(None, reason)``."""
        quotes, reason = self._quotes(day)
        if quotes is None:
            return None, reason
        for bid, ask in quotes:
            defect = quote_defect(bid, ask, None, locked_ok=True)
            if defect:
                return None, f"quote_{defect}"
        return quotes, None

    def _exit_quotes(self, day):
        """Return ``(quotes, None)`` when every leg can be CLOSED on ``day``, else ``(None, reason)``.

        A closing trade has the sides of the opposite trade: a short is bought back at the ask and
        a long is sold at the bid, so a short with no ask is no market (never a free buy-back). A long
        with no bid closes for nothing only under ``exit_zero_bid: "allow"``, and is counted.
        """
        quotes, reason = self._quotes(day)
        if quotes is None:
            return None, reason
        zero_bids = 0
        for (_right, sign), (bid, ask) in zip(self.legs, quotes, strict=True):
            for defect in quote_defects(bid, ask, "sell" if sign > 0 else "buy", locked_ok=True):
                if defect == "sold_leg_no_bid" and self.p.get("exit_zero_bid", "refuse") == "allow":
                    zero_bids += 1
                else:
                    return None, ("exit_zero_bid_long" if defect == "sold_leg_no_bid"
                                  else f"quote_{defect}")
        self.tally["exit_zero_bid_long"] += zero_bids
        return quotes, None

    def _fill_quotes(self, quotes):
        """Return the (bid, ask) pairs the fill model prices from: the NBBO as is, or the mid twice."""
        if self.p.get("fill", "nbbo") == "mid":
            return [((bid + ask) / 2.0,) * 2 for bid, ask in quotes]
        return quotes

    def _mid_value(self, quotes):
        """Cash per share if the legs were closed at the mid: ``sum(sign * mid)``."""
        return sum(sign * (bid + ask) / 2.0
                   for (_right, sign), (bid, ask) in zip(self.legs, quotes, strict=True))

    # -- the lifecycle -------------------------------------------------------------------

    def _open(self):
        """Fill the order at the open date, or return the one reason it is refused."""
        o = self.order
        quotes, reason = self._quotes(o.opened)
        if quotes is None:
            return "no_rows_on_open_date" if reason == "no_rows" else reason
        for (_right, sign), (bid, ask) in zip(self.legs, quotes, strict=True):
            defect = quote_defect(bid, ask, "sell" if sign < 0 else "buy")
            if defect:
                return defect
        credit, _widths = structure_credit(self.legs, o.strikes, self._fill_quotes(quotes))
        self.premium = self.mult * self.n * credit
        self.open_fees = self._fees(self.p.get("fee_per_contract", 0.0), self.units)
        self.last_value = self.mult * self.n * self._mid_value(quotes)
        if self.hedge:
            under = self.chain.underlying(o.symbol, o.opened)
            self.hedge.accrue(under)
            self._rebalance(o.opened, under, quotes)
        self._row(o.opened, "open", self.last_value, 0.0, False, None)
        return None

    def _fees(self, per_contract, units):
        """Return the fees for ``units`` option units (summed absolute leg quantities) of ``n`` contracts."""
        return float(per_contract) * self.n * units

    def _rebalance(self, day, under, quotes):
        """Rebalance the hedge toward its mid-implied target; count why it could not."""
        hedge = self.hedge
        mids = None if quotes is None else [(bid + ask) / 2.0 for bid, ask in quotes]
        target, reason = (None, "no_leg_quote") if mids is None else hedge.delta(
            self.legs, self.order.strikes, self.order.expiration, day, under, mids)
        if target is not None and hedge.due(target):
            hedge.rebalance(target)
            self.tally["hedge_rebalances"] += 1
        elif target is None:
            self.tally[f"hedge_skipped:{reason}"] += 1
        hedge.steps += 1

    def _row(self, day, phase, value, fees_to_date, carried, reason):
        """Append one mark row; ``value`` is the signed cash of closing the legs now."""
        symbol, hedge = self.order.symbol, self.hedge
        hedge_pnl = hedge.pnl if hedge else 0.0
        hedge_fees = hedge.fees if hedge else 0.0
        option_pnl = self.premium + value - self.open_fees - fees_to_date
        self.marks.append({
            "order_index": self.order.index, "symbol": symbol, "date": day.isoformat(),
            "phase": phase, "mark_value_usd": value, "option_pnl_usd": option_pnl,
            "hedge_shares": hedge.shares if hedge else 0.0, "hedge_pnl_usd": hedge_pnl,
            "pnl_usd": option_pnl + hedge_pnl - hedge_fees,
            "underlying": self.chain.underlying(symbol, day),
            "carried": carried, "carry_reason": reason})

    def _mark_day(self, day):
        """Mark a non-closing day at the mid, carrying the last mark when it cannot."""
        quotes, reason = self._usable(day)
        under = self.chain.underlying(self.order.symbol, day)
        if self.hedge:
            self.hedge.accrue(under)
        if quotes is None:
            self.carried_days += 1
            self.tally[f"marks_carried:{reason}"] += 1
            if self.hedge:
                self.tally["hedge_skipped:no_leg_quote"] += 1
                self.hedge.steps += 1
            self._row(day, "mark", self.last_value, 0.0, True, reason)
            return
        self.last_value = self.mult * self.n * self._mid_value(quotes)
        if self.hedge:
            self._rebalance(day, under, quotes)
        self._row(day, "mark", self.last_value, 0.0, False, None)

    def _try_exit(self, day):
        """Close at the NBBO on ``day``: ``(cash, fees)`` or ``None`` after counting why not."""
        quotes, reason = self._exit_quotes(day)
        if quotes is None:
            self.tally[f"exit_refused:{reason}"] += 1
            return None
        reversed_legs = tuple((right, -sign) for right, sign in self.legs)
        credit, _widths = structure_credit(reversed_legs, self.order.strikes, self._fill_quotes(quotes))
        return (self.mult * self.n * credit,
                self._fees(self.p.get("fee_per_contract", 0.0), self.units))

    def _exit_date_missing(self):
        """Count an exit date the chain never shows; the trade runs on to its expiration."""
        self.tally["exit_refused:exit_date_not_in_chain"] += 1

    def _settle(self, level):
        """Settlement cash and fees at the expiration level."""
        cash = self.mult * self.n * structure_payoff(self.legs, level, self.order.strikes)
        itm = sum(abs(sign) for (right, sign), k in zip(self.legs, self.order.strikes, strict=True)
                  if leg_intrinsic(right, k, level) > 0)
        return cash, self._fees(self.p.get("settle_fee_per_contract", 0.0), itm)

    def _close(self, day, phase, cash, fees):
        """Book the closing row, flatten the hedge and return the realized record parts."""
        under = self.chain.underlying(self.order.symbol, day)
        if self.hedge:
            self.hedge.accrue(under)
            self.hedge.flatten()
        self._row(day, phase, cash, fees, False, None)
        return cash, fees

    def run(self):
        """Simulate the order; return ``(trade record, mark rows)`` or ``None`` when refused."""
        o = self.order
        reason = self._open()
        if reason:
            self.tally[f"refused_at_open:{reason}"] += 1
            return None
        self.tally["orders_opened"] += 1
        closed, exit_refused = None, False
        wants_exit = self.p.get("close_rule", "expiry") == "exit"
        for day in self.chain.window(o.opened, o.expiration):
            if wants_exit and not exit_refused and day >= o.exit:
                done = self._try_exit(day) if day == o.exit else self._exit_date_missing()
                if done is not None:
                    closed = ("exit", day, *self._close(day, "exit", *done))
                    break
                exit_refused = True
            if day == o.expiration:
                under = self.chain.underlying(o.symbol, day)
                if under is not None:
                    closed = ("expiry", day, *self._close(day, "settle", *self._settle(under)))
                break
            self._mark_day(day)
        return self._record(closed, exit_refused), self.marks

    def _status(self, closed):
        """``(status, unsettled reason)`` for a trade that ran out of calendar."""
        if closed:
            return "closed", None
        if self.chain.covers(self.order.expiration):
            seen = self.chain.has_rows(self.order.symbol, self.order.expiration)
            return "unsettled", "no_underlying_on_expiry" if seen else "no_rows_on_expiry"
        return "open_at_end", None

    def _record(self, closed, exit_refused):
        """Return the per-trade record."""
        o = self.order
        status, why = self._status(closed)
        self.tally[f"closed:{closed[0]}" if closed else (
            f"unsettled:{why}" if why else "open_at_end")] += 1
        credit_usd = self.premium - self.open_fees
        loss = structure_max_loss(self.legs, o.strikes, credit_usd / self.n, self.mult)
        record = {
            "order_index": o.index, "symbol": o.symbol, "structure": o.structure,
            "strikes": list(o.strikes), "expiration": o.expiration.isoformat(),
            "contracts": self.n, "open_date": o.opened.isoformat(), "close_date": None,
            "reason_closed": None, "status": status, "unsettled_reason": why,
            "premium_usd": self.premium, "credit_usd": credit_usd,
            "open_fees_usd": self.open_fees, "close_cash_usd": None, "close_fees_usd": None,
            "option_pnl_usd": None, "hedge_pnl_usd": self.hedge.pnl if self.hedge else 0.0,
            "hedge_fees_usd": self.hedge.fees if self.hedge else 0.0, "fees_usd": None,
            "pnl_usd": None, "pnl_over_premium": None,
            "max_loss_usd": None if math.isinf(loss) else loss * self.n,
            "max_loss_unbounded": math.isinf(loss),
            "marked_pnl_usd": self.marks[-1]["pnl_usd"], "mark_days": len(self.marks) - 1,
            "carried_days": self.carried_days, "exit_refused": exit_refused}
        if closed:
            reason, day, cash, fees = closed
            option_pnl = self.premium + cash - self.open_fees - fees
            total = option_pnl + record["hedge_pnl_usd"] - record["hedge_fees_usd"]
            record.update(
                close_date=day.isoformat(), reason_closed=reason, close_cash_usd=cash,
                close_fees_usd=fees, option_pnl_usd=option_pnl, pnl_usd=total,
                fees_usd=self.open_fees + fees + record["hedge_fees_usd"],
                pnl_over_premium=None if self.premium == 0 else total / abs(self.premium))
        return record


class OptionStructureLedger(Node):
    """Open option structures at NBBO sides, mark at the mid, close by a declared rule (role ``transform``).

    Wired by import path (``dskit.pipeline.libs.option_ledger:OptionStructureLedger``). Inputs
    ``chain`` (rows with a symbol, date, expiration, strike, right, bid, ask and an underlying
    price) and ``orders`` (one row per structure to open: open date, symbol, expiration, one strike
    per leg and a structure name). The module docstring states the open, mark, close and hedge rules.
    Fully deterministic: orders are processed in input order and a trade's index is its order's
    position; every refusal is counted in ``summary``. A chain row for an ordered symbol that cannot
    be read (a bad date, strike, right or expiration) is dropped and counted; a second row for one
    needed contract and day refuses, as does an order missing a field. Empty input refuses.

    Outputs
    -------
    ``trades``
        One record per OPENED order: ``order_index``, ``symbol``, ``structure``, ``strikes``,
        ``expiration``, ``contracts``, ``open_date``, ``close_date``, ``reason_closed`` (``expiry``,
        ``exit`` or null), ``status`` (``closed``, ``open_at_end``, ``unsettled``),
        ``premium_usd`` (signed cash at the fills, a debit negative), ``credit_usd`` (premium less
        open fees), ``open_fees_usd``, ``close_cash_usd``, ``close_fees_usd``, ``option_pnl_usd``,
        ``hedge_pnl_usd`` (gross), ``hedge_fees_usd``, ``fees_usd`` (all fees), ``pnl_usd`` (net of
        everything), ``pnl_over_premium`` (``pnl_usd`` over the absolute premium),
        ``max_loss_usd`` (:func:`~dskit.pipeline.option_structures.structure_max_loss` on the
        credit net of open fees, times contracts; null with ``max_loss_unbounded`` when
        unbounded), ``marked_pnl_usd``, ``mark_days``, ``carried_days`` and ``exit_refused``. The
        realized fields are null for a trade that is not closed.
    ``marks``
        One row per trade and calendar date from the open to the close: ``phase`` (``open``,
        ``mark``, ``exit``, ``settle``), ``mark_value_usd`` (cash of closing at the mid; the
        realized cash on a closing row), ``option_pnl_usd``, ``hedge_shares``, ``hedge_pnl_usd``,
        ``pnl_usd`` (running total), ``underlying``, ``carried`` and ``carry_reason``.
    ``summary``
        Counts: orders in and opened, ``refused_at_open`` and ``exit_refused`` and ``unsettled``
        and ``marks_carried`` by reason, ``closed`` by rule, ``open_at_end``, the hedge's
        rebalances and skips, ``totals`` over closed trades, and the chain intake census.

    Parameters
    ----------
    params : dict
        REQUIRED: ``chain_fields`` (a dict naming the chain columns, exactly :data:`CHAIN_FIELDS`),
        ``order_fields`` (a dict naming the order columns :data:`ORDER_FIELDS`; ``exit`` only
        under ``close_rule: "exit"``), ``structures`` (``{name: [[right, signed quantity], ...]}``,
        the document's leg-set vocabulary) and ``multiplier`` (int >= 1). Optional: ``contracts``
        (int >= 1, default 1), ``fee_per_contract`` (>= 0, per contract of each leg quantity on open and exit),
        ``settle_fee_per_contract`` (>= 0, per contract of each in-the-money leg quantity at settlement), ``fill`` (one of
        :data:`FILLS`, default ``nbbo``), ``close_rule`` (``expiry`` default, or ``exit``),
        ``year_days`` (int >= 1, default 365), ``exit_zero_bid`` (one of :data:`EXIT_ZERO_BID`, default
        ``refuse``) and ``hedge``: ``{"rebalance": one of
        :data:`REBALANCES`, "every_n": int (with ``every_n``), "threshold": shares (with
        ``threshold``), "rate": float, "fee_per_share": >= 0}``.

    Examples
    --------
    A put credit spread opened at the NBBO and held to expiry::

        node = OptionStructureLedger("ledger", {
            "chain_fields": {"symbol": "symbol", "date": "date", "expiration": "expiration",
                             "strike": "strike", "right": "right", "bid": "bid", "ask": "ask",
                             "underlying": "underlying_price"},
            "order_fields": {"date": "open_date", "symbol": "symbol", "expiration": "expiration",
                             "strikes": "strikes", "structure": "structure"},
            "structures": {"put_spread": [["put", 1], ["put", -1]]},
            "multiplier": 100, "fee_per_contract": 0.65})
        out = node.run(ctx, {"chain": chain_rows,
                             "orders": [{"open_date": "2020-03-02", "symbol": "ABC",
                                         "expiration": "2020-03-27", "strikes": [95, 100],
                                         "structure": "put_spread"}]})
        out["trades"][0]["pnl_usd"]
    """

    role = "transform"
    outputs = ("trades", "marks", "summary")
    _PARAMS = ("chain_fields", "order_fields", "structures", "multiplier", "contracts",
               "fee_per_contract", "settle_fee_per_contract", "fill", "close_rule", "year_days",
               "hedge", "exit_zero_bid")
    _REQUIRED = ("chain_fields", "order_fields", "structures", "multiplier")
    _HEDGE_KEYS = ("rebalance", "every_n", "threshold", "rate", "fee_per_share")

    @staticmethod
    def _fields_problems(name, value, allowed, required):
        """Problems with a dict that names columns: exactly ``required`` plus any allowed extras."""
        if not isinstance(value, dict):
            return [f"{name} must be a dict naming columns, got {value!r}"]
        problems = []
        unknown = sorted(set(value) - set(allowed))
        if unknown:
            problems.append(f"{name}: unknown key(s) {unknown} — allowed: {list(allowed)}")
        for key in required:
            if key not in value:
                problems.append(f"{name}.{key} is required")
        for key, field in value.items():
            if not isinstance(field, str) or not field:
                problems.append(f"{name}.{key} must be a non-empty column name, got {field!r}")
        return problems

    @staticmethod
    def _structures_problems(structures):
        """Problems with the leg-set vocabulary."""
        if not isinstance(structures, dict) or not structures:
            return [f"structures must be a non-empty dict of name -> legs, got {structures!r}"]
        problems = []
        for name, legs in structures.items():
            where = f"structures[{name!r}]"
            if not isinstance(name, str) or not name:
                problems.append(f"{where}: a structure name is a non-empty string")
            elif not isinstance(legs, list) or not legs:
                problems.append(f"{where} must be a non-empty list of [right, quantity] pairs")
            else:
                for leg in legs:
                    ok = (isinstance(leg, list) and len(leg) == 2 and leg[0] in RIGHTS
                          and isinstance(leg[1], int) and not isinstance(leg[1], bool) and leg[1] != 0)
                    if not ok:
                        problems.append(f"{where}: a leg is [\"put\" or \"call\", a nonzero int], got {leg!r}")
        return problems

    @classmethod
    def _hedge_problems(cls, hedge):
        """Problems with the hedge block."""
        if not isinstance(hedge, dict):
            return [f"hedge must be a dict, got {hedge!r}"]
        problems = []
        unknown = sorted(set(hedge) - set(cls._HEDGE_KEYS))
        if unknown:
            problems.append(f"hedge: unknown key(s) {unknown} — allowed: {list(cls._HEDGE_KEYS)}")
        rule = hedge.get("rebalance")
        if rule not in REBALANCES:
            problems.append(f"hedge.rebalance must be one of {list(REBALANCES)}, got {rule!r}")
        if rule == "every_n":
            check_int_param(problems, "hedge.every_n", hedge.get("every_n"), ge=1)
        elif "every_n" in hedge:
            problems.append("hedge.every_n is only for rebalance 'every_n'")
        if rule == "threshold":
            if not (number_ok(hedge.get("threshold")) and hedge["threshold"] >= 0):
                problems.append(f"hedge.threshold must be a number >= 0, got {hedge.get('threshold')!r}")
        elif "threshold" in hedge:
            problems.append("hedge.threshold is only for rebalance 'threshold'")
        if "rate" in hedge and not number_ok(hedge["rate"]):
            problems.append(f"hedge.rate must be a finite number, got {hedge['rate']!r}")
        fee = hedge.get("fee_per_share", 0.0)
        if not (number_ok(fee) and fee >= 0):
            problems.append(f"hedge.fee_per_share must be a number >= 0, got {fee!r}")
        return problems

    @classmethod
    def validate_params(cls, params):
        """Problems with the declared knobs, empty when none.

        Parameters
        ----------
        params : dict
            The node's ``params`` block.

        Returns
        -------
        list of str
            One message per problem.
        """
        problems = []
        reject_unknown_params(problems, params, cls._PARAMS)
        for name in cls._REQUIRED:
            if name not in params:
                problems.append(f"{name} is required")
        close_rule = params.get("close_rule", "expiry")
        if close_rule not in _CLOSE_RULES:
            problems.append(f"close_rule must be one of {list(_CLOSE_RULES)}, got {close_rule!r}")
        if "chain_fields" in params:
            problems += cls._fields_problems("chain_fields", params["chain_fields"],
                                             CHAIN_FIELDS, CHAIN_FIELDS)
        if "order_fields" in params:
            needed = ORDER_FIELDS if close_rule == "exit" else ORDER_FIELDS[:-1]
            problems += cls._fields_problems("order_fields", params["order_fields"],
                                             needed, needed)
        if "structures" in params:
            problems += cls._structures_problems(params["structures"])
        if "multiplier" in params:
            check_int_param(problems, "multiplier", params["multiplier"], ge=1)
        for name, floor in (("contracts", 1), ("year_days", 1)):
            if name in params:
                check_int_param(problems, name, params[name], ge=floor)
        for name in ("fee_per_contract", "settle_fee_per_contract"):
            if name in params and not (number_ok(params[name]) and params[name] >= 0):
                problems.append(f"{name} must be a number >= 0, got {params[name]!r}")
        if "exit_zero_bid" in params and params["exit_zero_bid"] not in EXIT_ZERO_BID:
            problems.append(f"exit_zero_bid must be one of {list(EXIT_ZERO_BID)}, "
                            f"got {params['exit_zero_bid']!r}")
        if "fill" in params and params["fill"] not in FILLS:
            problems.append(f"fill must be one of {list(FILLS)}, got {params['fill']!r}")
        if "hedge" in params and not is_node_ref(params["hedge"]):
            problems += cls._hedge_problems(params["hedge"])
        return problems

    # -- intake ----------------------------------------------------------------------------

    def _orders(self, rows):
        """Parse the orders; schema faults refuse by row, data faults are counted."""
        p = self.params
        fields = p["order_fields"]
        use_exit = p.get("close_rule", "expiry") == "exit"
        orders, refused = [], Counter()
        if not isinstance(rows, (list, tuple)) or not rows:
            raise ValueError(f"{self.key}: no orders to open")
        for index, row in enumerate(rows):
            where = f"{self.key}: order {index}"
            if not isinstance(row, Mapping):
                raise ValueError(f"{where} must be a mapping, got {type(row).__name__}")
            symbol = self._need(where, row, fields["symbol"])
            if not isinstance(symbol, str) or not symbol:
                raise ValueError(f"{where}: {fields['symbol']!r} must be a non-empty string")
            opened = self._need_day(where, row, fields["date"])
            expiration = self._need_day(where, row, fields["expiration"])
            exit_day = self._need_day(where, row, fields["exit"]) if use_exit else None
            name = self._need(where, row, fields["structure"])
            if name not in p["structures"]:
                raise ValueError(f"{where}: unknown structure {name!r} — declared: "
                                 f"{sorted(p['structures'])}")
            legs = tuple((right, sign) for right, sign in p["structures"][name])
            strikes = self._need(where, row, fields["strikes"])
            if (not isinstance(strikes, (list, tuple)) or len(strikes) != len(legs)
                    or not all(price_ok(k) for k in strikes)):
                raise ValueError(f"{where}: {fields['strikes']!r} must give one positive strike per "
                                 f"leg of {name!r} ({len(legs)}), got {strikes!r}")
            if expiration <= opened:
                refused["expiry_not_after_open"] += 1
            elif use_exit and exit_day <= opened:
                refused["exit_not_after_open"] += 1
            elif use_exit and exit_day > expiration:
                refused["exit_after_expiry"] += 1
            else:
                orders.append(_Order(index, symbol, opened, expiration, exit_day, name, legs,
                                     tuple(float(k) for k in strikes)))
        return orders, refused

    @staticmethod
    def _need(where, row, field):
        """Return the row's value of a field it must carry, refusing otherwise."""
        if field not in row or row[field] is None:
            raise ValueError(f"{where} lacks the field {field!r}")
        return row[field]

    @classmethod
    def _need_day(cls, where, row, field):
        """Return the row's ISO-date field as a date, refusing otherwise."""
        value = _day(cls._need(where, row, field))
        if value is None:
            raise ValueError(f"{where}: {field!r} must be an ISO date, got {row[field]!r}")
        return value

    def _plan(self, orders):
        """Per symbol: the contracts the orders touch and the date window they span."""
        plan = {}
        for o in orders:
            entry = plan.setdefault(o.symbol, {"keys": set(), "lo": o.opened, "hi": o.expiration})
            entry["keys"].update((o.expiration, k, right)
                                 for (right, _s), k in zip(o.legs, o.strikes, strict=True))
            entry["lo"], entry["hi"] = min(entry["lo"], o.opened), max(entry["hi"], o.expiration)
        return plan

    def _chain(self, rows, plan):
        """Read the chain once, keeping only the needed contracts, dates and underlying prices."""
        f = self.params["chain_fields"]
        chain = _Chain()
        if not isinstance(rows, (list, tuple)) or not rows:
            raise ValueError(f"{self.key}: no chain rows")
        days, expirations = [None, None], [None, None]
        for index, row in enumerate(rows):
            if type(row) is not dict and not isinstance(row, Mapping):
                raise ValueError(f"{self.key}: chain row {index} must be a mapping, "
                                 f"got {type(row).__name__}")
            chain.rows_in += 1
            symbol = row.get(f["symbol"])
            entry = plan.get(symbol) if isinstance(symbol, str) else None
            if entry is None:
                chain.dropped["other_symbol"] += 1
                continue
            day = _day_memo(days, row.get(f["date"]))
            if day is None:
                chain.dropped["bad_date"] += 1
            elif not entry["lo"] <= day <= entry["hi"]:
                chain.dropped["outside_window"] += 1
            else:
                self._take(chain, entry, symbol, day, row, f, expirations)
        chain.finish()
        return chain

    @staticmethod
    def _take(chain, entry, symbol, day, row, f, expirations):
        """Keep one in-window row: its day, its underlying and, if needed, its quote."""
        chain.seen.add((symbol, day))
        chain.days.add(day)
        under = _number(row.get(f["underlying"]))
        if under is not None and under > 0:
            held = chain.under.setdefault((symbol, day), under)
            if abs(held - under) > 1e-9 * max(abs(held), 1.0):
                chain.conflicts += 1
        else:
            chain.dropped["bad_underlying"] += 1
        expiration, strike = _day_memo(expirations, row.get(f["expiration"])), _number(row.get(f["strike"]))
        right = row.get(f["right"])
        right = _RIGHT_NAMES.get(right.strip().lower()) if isinstance(right, str) else None
        if expiration is None or strike is None or right is None:
            chain.dropped["bad_contract"] += 1
            return
        key = (expiration, strike, right)
        if key not in entry["keys"]:
            chain.dropped["not_needed"] += 1
            return
        slot = (symbol, day, key)
        if slot in chain.quotes:
            raise ValueError(f"the chain repeats {symbol} {expiration} {strike} {right} on {day}")
        chain.quotes[slot] = (_number(row.get(f["bid"])), _number(row.get(f["ask"])))
        chain.rows_used += 1

    # -- run -------------------------------------------------------------------------------

    def run(self, ctx, inputs):
        """Open every order that fills, then walk each through the calendar to its close.

        Parameters
        ----------
        ctx : NodeContext
            The run frame; unused.
        inputs : dict
            ``chain`` and ``orders`` (both REQUIRED, non-empty lists of mapping rows).

        Returns
        -------
        dict
            ``trades``, ``marks`` and ``summary`` as the class docstring states.

        Raises
        ------
        ValueError
            No orders or no chain rows; every order refused (the counts by reason are in the message); an order that is not a mapping, lacks a field, names an
            undeclared structure or has a bad date or strikes; a chain row that is not a mapping;
            a needed contract quoted twice on one day.
        """
        orders, refused = self._orders(inputs["orders"])
        chain = self._chain(inputs["chain"], self._plan(orders))
        tally = Counter()
        trades, marks = [], []
        for order in orders:
            done = _Trade(self, chain, order, tally).run()
            if done is not None:
                trades.append(done[0])
                marks.extend(done[1])
        for reason, count in refused.items():
            tally[f"refused_at_open:{reason}"] += count
        summary = self._summary(len(inputs["orders"]), trades, marks, tally, chain)
        if not trades:
            raise ValueError(f"{self.key}: every order was refused ({summary['orders_refused']} of "
                             f"{summary['orders_in']}): {summary['refused_at_open']}")
        self.log.info("%s: %d order(s), %d opened, %d mark row(s)", self.key,
                      summary["orders_in"], summary["orders_opened"], len(marks))
        return {"trades": trades, "marks": marks, "summary": summary}

    @staticmethod
    def _by(tally, prefix):
        """Return the counts under ``prefix:`` as ``{reason: n}``, sorted."""
        cut = len(prefix) + 1
        return {k[cut:]: v for k, v in sorted(tally.items()) if k.startswith(prefix + ":")}

    def _summary(self, n_orders, trades, marks, tally, chain):
        """Return the counted report."""
        p = self.params
        refused = self._by(tally, "refused_at_open")
        closed = [t for t in trades if t["status"] == "closed"]
        out = {
            "stage": f"option structure ledger: open at {p.get('fill', 'nbbo')}, mark at the mid, "
                     f"close by {p.get('close_rule', 'expiry')}"
                     + (f", delta hedge {p['hedge']['rebalance']}" if p.get("hedge") else ""),
            "orders_in": n_orders, "orders_opened": tally["orders_opened"],
            "orders_refused": sum(refused.values()), "refused_at_open": refused,
            "closed": self._by(tally, "closed"), "open_at_end": tally["open_at_end"],
            "unsettled": self._by(tally, "unsettled"),
            "exit_refused": self._by(tally, "exit_refused"),
            "exit_zero_bid_long": tally["exit_zero_bid_long"],
            "marks": len(marks), "marks_carried": self._by(tally, "marks_carried"),
            "totals": {"closed_trades": len(closed),
                       "pnl_usd": math.fsum(t["pnl_usd"] for t in closed),
                       "fees_usd": math.fsum(t["fees_usd"] for t in closed)},
            "chain": {"rows_in": chain.rows_in, "rows_used": chain.rows_used,
                      "dropped": dict(sorted(chain.dropped.items())),
                      "underlying_conflicts": chain.conflicts, "dates": len(chain.sorted_days)},
        }
        if p.get("hedge"):
            out["hedge"] = {"rebalances": tally["hedge_rebalances"],
                            "skipped": self._by(tally, "hedge_skipped")}
        return out
