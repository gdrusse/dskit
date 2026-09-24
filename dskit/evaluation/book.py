"""The evaluation book: equity, cash, exposure and round trips folded from the log.

There is ONE P&L fold in dskit — :class:`dskit.production.accounting.WindowBook`
— and this module feeds it rather than restating it (ADR-0183). Each
``fill`` event is adapted to the fill shape ``WindowBook.apply`` reads
(``instrument``, ``side``, ``qty``, ``price``, ``fee``) and applied twice:
once as recorded (the NET book) and once with the fee zeroed (the GROSS
book, "before costs"). Equity at every mark, fill or cash-flow instant is
``external cash + WindowBook.pnl(mark_of)``, where ``mark_of`` answers an
instrument's latest mark or, before its first mark, its latest fill price.

What the book adds is bookkeeping ``WindowBook`` does not keep: the cash
balance (external flows in, fill notionals and fees out), the signed
position per instrument (for exposure), and FIFO round trips. A round
trip is an ATTRIBUTION of closed quantity to the lot that opened it, with
the decision and reason behind each side; its P&L is FIFO while the
book's realised P&L is average-cost, so per-trip numbers and the realised
total agree exactly only once the book is flat (a test pins that).

Returns exclude external flows: the time-weighted return chains
subperiods through :class:`dskit.production.report.PerformanceCalculator`,
the one owner of TWR, so a deposit is never a return.

Import cost: stdlib plus ``dskit.pipeline`` and ``dskit.production``.
"""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from fractions import Fraction

from dskit.pipeline.stats import max_drawdown
from dskit.production.accounting import WindowBook, decimal_of
from dskit.production.report import PerformanceCalculator, PerformanceObservation

__all__ = ["DayRow", "EquityPoint", "EvaluationBook", "RoundTrip"]

_ZERO = Fraction(0)


def _exact(value):
    """Read a JSON number as the decimal it was written as (``100.05`` -> 2001/20)."""
    return Fraction(repr(value)) if isinstance(value, float) else Fraction(value)


@dataclass(frozen=True)
class _WindowFill:
    """The fill shape :meth:`WindowBook.apply` reads — the adapter, nothing more."""

    instrument: str
    side: str
    qty: Fraction
    price: Fraction
    fee: Fraction


@dataclass(frozen=True)
class EquityPoint:
    """The account at one instant, exact.

    Parameters
    ----------
    ts_ms : int
        The instant (the last event at it wins).
    equity, gross_equity : fractions.Fraction
        ``external + pnl`` with and without fees.
    cash : fractions.Fraction
        External flows plus sale proceeds, minus purchases and fees.
    external : fractions.Fraction
        Cumulative external cash flow.
    realised, unrealised : fractions.Fraction
        The net book's two parts.
    fees : fractions.Fraction
        Cumulative fees.
    gross_notional, net_notional : fractions.Fraction
        ``sum |qty * price|`` and ``sum qty * price`` over open positions.

    Examples
    --------
    ::

        point = EquityPoint(0, Fraction(100), Fraction(100), Fraction(100), Fraction(100),
                            Fraction(0), Fraction(0), Fraction(0), Fraction(0), Fraction(0))
        point.exposure  # 0.0
    """

    ts_ms: int
    equity: Fraction
    gross_equity: Fraction
    cash: Fraction
    external: Fraction
    realised: Fraction
    unrealised: Fraction
    fees: Fraction
    gross_notional: Fraction
    net_notional: Fraction

    @property
    def trading_pnl(self):
        """Equity minus external flows: what the trading alone came to."""
        return self.equity - self.external

    @property
    def exposure(self):
        """Gross notional over equity, or None when equity is not positive."""
        return float(self.gross_notional / self.equity) if self.equity > 0 else None

    def to_obj(self):
        """Return the point as floats.

        Returns
        -------
        dict
        """
        out = {"ts_ms": self.ts_ms}
        for name in ("equity", "gross_equity", "cash", "external", "realised", "unrealised",
                     "fees", "gross_notional", "net_notional"):
            out[name] = float(getattr(self, name))
        out["exposure"] = self.exposure
        return out


@dataclass(frozen=True)
class RoundTrip:
    """Closed quantity matched FIFO to the lot that opened it.

    Parameters
    ----------
    instrument, direction : str
        ``direction`` is ``long`` or ``short``.
    qty : float
        The matched quantity.
    entry_ms, exit_ms : int
    entry_price, exit_price : float
    gross_pnl, fees, pnl : float
        ``pnl = gross_pnl - fees``; fees are the matched shares of both fills'.
    entry_fill_id, exit_fill_id : str
    entry_decision_id, exit_decision_id, entry_reason, exit_reason : str or None
        The decisions behind the two fills, linked through their orders.

    Examples
    --------
    ::

        trip = RoundTrip("AAA", "long", 10.0, 0, 60000, 100.0, 101.0, 10.0, 1.0, 9.0,
                         "f1", "f2", "d1", "d2", "edge", "stop")
        trip.holding_ms  # 60000
    """

    instrument: str
    direction: str
    qty: float
    entry_ms: int
    exit_ms: int
    entry_price: float
    exit_price: float
    gross_pnl: float
    fees: float
    pnl: float
    entry_fill_id: str
    exit_fill_id: str
    entry_decision_id: object
    exit_decision_id: object
    entry_reason: object
    exit_reason: object

    #: The CSV / table column order.
    COLUMNS = ("instrument", "direction", "qty", "entry_ms", "exit_ms", "holding_ms",
               "entry_price", "exit_price", "gross_pnl", "fees", "pnl", "entry_fill_id",
               "exit_fill_id", "entry_decision_id", "exit_decision_id", "entry_reason",
               "exit_reason")

    @property
    def holding_ms(self):
        """Milliseconds from entry to exit."""
        return self.exit_ms - self.entry_ms

    def to_obj(self):
        """Return the trip as a flat dict in :attr:`COLUMNS` order.

        Returns
        -------
        dict
        """
        return {name: getattr(self, name) for name in self.COLUMNS}


@dataclass(frozen=True)
class DayRow:
    """One local session day of the book.

    Parameters
    ----------
    day : str
        ``YYYY-MM-DD`` in the run's zone.
    pnl, gross_pnl, fees, flows : float
        The day's trading P&L (net, gross), fees and external flows.
    end_equity : float
    ret : float or None
        The day's time-weighted return (flows excluded).
    fills, trips : int
        Fills executed and round trips closed that day.

    Examples
    --------
    ::

        DayRow("2026-09-01", 12.0, 14.0, 2.0, 0.0, 10012.0, 0.0012, 4, 2).ret  # 0.0012
    """

    day: str
    pnl: float
    gross_pnl: float
    fees: float
    flows: float
    end_equity: float
    ret: object
    fills: int
    trips: int

    def to_obj(self):
        """Return the row as a dict.

        Returns
        -------
        dict
        """
        return dict(self.__dict__)


class _Lot:
    """An open FIFO lot: what is left of one fill's quantity, and its fee share."""

    def __init__(self, fill, qty, sign, decision):
        self.fill, self.qty, self.sign, self.decision = fill, qty, sign, decision
        self.fee_per_unit = _exact(fill.fee) / _exact(fill.get("qty"))


class EvaluationBook:
    """Fold an :class:`~dskit.evaluation.events.EventLog` into the account's paths.

    Parameters
    ----------
    log : EventLog
        A validated log.

    Examples
    --------
    ::

        book = EvaluationBook(log)
        book.points[-1].equity   # exact Fraction
        len(book.round_trips)    # closed FIFO trips
    """

    def __init__(self, log):
        self.log = log
        self._links = log.links
        self._net, self._gross = WindowBook(), WindowBook()
        self._price = {}
        self._held = defaultdict(Fraction)
        self._cash = self._external = self._fees = self._traded = _ZERO
        self._lots = defaultdict(deque)
        self._trips, self._points = [], []
        self.trips_of_fill = defaultdict(list)
        self.role_of_fill = {}
        handlers = {"fill": self._on_fill, "mark": self._on_mark, "cashflow": self._on_cashflow}
        for event in log:
            handler = handlers.get(event.kind)
            if handler is not None:
                handler(event)
                self._record(event.ts_ms)

    # -- the fold ------------------------------------------------------------

    def _on_fill(self, fill):
        """Apply one fill to both WindowBooks, the cash, the position and the lots."""
        instrument = fill.instrument
        qty, price, fee = (_exact(fill.get("qty")), _exact(fill.get("price")),
                           _exact(fill.fee))
        side = fill.get("side")
        self._net.apply(_WindowFill(instrument, side, qty, price, fee))
        self._gross.apply(_WindowFill(instrument, side, qty, price, _ZERO))
        signed = _exact(fill.signed_qty)
        self._cash -= signed * price + fee
        self._held[instrument] += signed
        self._price[instrument] = price
        self._fees += fee
        self._traded += qty * price
        self._match(fill, signed, price)

    def _on_mark(self, mark):
        """Record the instrument's latest mark."""
        self._price[mark.instrument] = _exact(mark.get("price"))

    def _on_cashflow(self, flow):
        """Move external cash in or out."""
        amount = _exact(flow.get("amount"))
        self._cash += amount
        self._external += amount

    def _match(self, fill, signed, price):
        """Close opposite lots FIFO, then open a lot with whatever remains."""
        lots = self._lots[fill.instrument]
        sign = 1 if signed > 0 else -1
        remaining = abs(signed)
        exit_fee_unit = _exact(fill.fee) / _exact(fill.get("qty"))
        decision = self._links.decision_of_fill(fill)
        self.role_of_fill[fill.get("fill_id")] = "entry"
        while remaining > 0 and lots and lots[0].sign != sign:
            lot = lots[0]
            matched = min(remaining, lot.qty)
            trip = self._trip(lot, fill, matched, price, exit_fee_unit, decision)
            self._trips.append(trip)
            self.trips_of_fill[lot.fill.get("fill_id")].append(trip)
            self.trips_of_fill[fill.get("fill_id")].append(trip)
            self.role_of_fill[fill.get("fill_id")] = "exit"
            lot.qty -= matched
            remaining -= matched
            if lot.qty == 0:
                lots.popleft()
        if remaining > 0:
            lots.append(_Lot(fill, remaining, sign, decision))

    @staticmethod
    def _trip(lot, fill, matched, price, exit_fee_unit, decision):
        """Build the round trip of ``matched`` units of ``lot`` closed by ``fill``."""
        entry_price = _exact(lot.fill.get("price"))
        gross = matched * (price - entry_price) * lot.sign
        fees = matched * (lot.fee_per_unit + exit_fee_unit)
        opened, closed = lot.decision, decision
        return RoundTrip(
            instrument=fill.instrument,
            direction="long" if lot.sign > 0 else "short",
            qty=float(matched),
            entry_ms=lot.fill.ts_ms,
            exit_ms=fill.ts_ms,
            entry_price=float(entry_price),
            exit_price=float(price),
            gross_pnl=float(gross),
            fees=float(fees),
            pnl=float(gross - fees),
            entry_fill_id=lot.fill.get("fill_id"),
            exit_fill_id=fill.get("fill_id"),
            entry_decision_id=None if opened is None else opened.get("decision_id"),
            exit_decision_id=None if closed is None else closed.get("decision_id"),
            entry_reason=None if opened is None else opened.get("reason"),
            exit_reason=None if closed is None else closed.get("reason"),
        )

    def _record(self, ts_ms):
        """Append the account at ``ts_ms``, replacing a point at the same instant."""
        mark_of = self._price.get
        values = [held * self._price[i] for i, held in self._held.items() if held]
        point = EquityPoint(
            ts_ms=ts_ms,
            equity=self._external + self._net.pnl(mark_of),
            gross_equity=self._external + self._gross.pnl(mark_of),
            cash=self._cash,
            external=self._external,
            realised=self._net.realised,
            unrealised=self._net.unrealised(mark_of),
            fees=self._fees,
            gross_notional=sum((abs(v) for v in values), _ZERO),
            net_notional=sum(values, _ZERO),
        )
        if self._points and self._points[-1].ts_ms == ts_ms:
            self._points[-1] = point
        else:
            self._points.append(point)

    # -- readings ------------------------------------------------------------

    @property
    def points(self):
        """The account path, one point per distinct instant, in time order."""
        return tuple(self._points)

    @property
    def round_trips(self):
        """Every closed FIFO round trip, in exit order."""
        return tuple(self._trips)

    @property
    def realised(self):
        """The net book's realised P&L (average cost, fees included), exact."""
        return self._net.realised

    @property
    def traded_notional(self):
        """``sum |qty * price|`` over every fill, exact."""
        return self._traded

    def open_lots(self):
        """Return what is still open, FIFO, per instrument.

        Returns
        -------
        list of dict
            ``instrument``, ``direction``, ``qty``, ``entry_ms``,
            ``entry_price``, ``entry_fees`` (the unmatched fee share).
        """
        return [
            {
                "instrument": instrument,
                "direction": "long" if lot.sign > 0 else "short",
                "qty": float(lot.qty),
                "entry_ms": lot.fill.ts_ms,
                "entry_price": float(lot.fill.get("price")),
                "entry_fees": float(lot.qty * lot.fee_per_unit),
            }
            for instrument, lots in sorted(self._lots.items())
            for lot in lots
        ]

    def pnl_increments(self):
        """Return the trading P&L change between consecutive points (flows excluded).

        Returns
        -------
        list of float
            Starting from the zero baseline before the first point.
        """
        out, last = [], _ZERO
        for point in self._points:
            out.append(float(point.trading_pnl - last))
            last = point.trading_pnl
        return out

    def max_drawdown(self):
        """Return the largest trading-P&L drawdown, through ``pipeline.stats.max_drawdown``.

        Returns
        -------
        float
        """
        return max_drawdown(self.pnl_increments())

    def drawdowns(self):
        """Return the drawdown path and its worst relative depth and duration.

        Returns
        -------
        dict
            ``series`` (list of ``(ts_ms, drawdown)``), ``max_pct`` (the
            worst drawdown over the equity at its peak, or None) and
            ``max_duration_ms`` (longest time below a prior peak).
        """
        series, peak, peak_equity, peak_ms = [], _ZERO, None, None
        worst_pct, longest = None, 0
        for point in self._points:
            if point.trading_pnl >= peak:
                peak, peak_equity, peak_ms = point.trading_pnl, point.equity, point.ts_ms
            depth = peak - point.trading_pnl
            series.append((point.ts_ms, float(depth)))
            if depth > 0 and peak_equity is not None and peak_equity > 0:
                pct = float(depth / peak_equity)
                worst_pct = pct if worst_pct is None else max(worst_pct, pct)
            if depth > 0:
                longest = max(longest, point.ts_ms - (peak_ms if peak_ms is not None else
                                                      self._points[0].ts_ms))
        return {"series": series, "max_pct": worst_pct, "max_duration_ms": longest}

    def _observations(self, points, from_capital):
        """PerformanceObservations over ``points`` from the first positive equity on.

        With ``from_capital`` the first observation is the capital itself
        (its external flows), so trading at the same instant as the first
        deposit lands in the first subperiod instead of being absorbed.
        """
        start = next((i for i, p in enumerate(points) if p.equity > 0), None)
        if start is None or any(p.equity < 0 for p in points[start:]):
            return None
        first = points[start]
        base = first.external if from_capital and first.external > 0 else first.equity
        return (PerformanceObservation(first.ts_ms, decimal_of(base),
                                       decimal_of(first.external)),) + tuple(
            PerformanceObservation(p.ts_ms, decimal_of(p.equity), decimal_of(p.external))
            for p in points[start + 1:]
        )

    def time_weighted_return(self, points=None, from_capital=True):
        """Return the flow-neutral time-weighted return over ``points`` (all by default).

        Parameters
        ----------
        points : sequence of EquityPoint or None
        from_capital : bool
            Start from the first point's external capital rather than its
            equity (the whole run and its first day do; later days start
            from the previous close).

        Returns
        -------
        float or None
            ``None`` below two positive-equity observations or when
            equity turns negative (the return is undefined there).
        """
        points = list(self._points if points is None else points)
        observations = self._observations(points, from_capital)
        if observations is None or len(observations) < 2:
            return None
        found = PerformanceCalculator(observations).time_weighted_return()
        return None if found is None else float(found)

    def days(self):
        """Return one :class:`DayRow` per local session day with activity.

        Returns
        -------
        list of DayRow
        """
        local = self.log.local_time
        by_day = defaultdict(list)
        for point in self._points:
            by_day[local.day(point.ts_ms)].append(point)
        fills = defaultdict(int)
        for fill in self.log.of_kind("fill"):
            fills[local.day(fill.ts_ms)] += 1
        trips = defaultdict(int)
        for trip in self._trips:
            trips[local.day(trip.exit_ms)] += 1
        rows, previous = [], None
        for day in sorted(by_day):
            points = by_day[day]
            rows.append(self._day_row(day, points, previous, fills[day], trips[day]))
            previous = points[-1]
        return rows

    def _day_row(self, day, points, previous, fills, trips):
        """Build one day's row against the previous day's closing point."""
        last = points[-1]
        base = previous if previous is not None else EquityPoint(
            points[0].ts_ms, _ZERO, _ZERO, _ZERO, _ZERO, _ZERO, _ZERO, _ZERO, _ZERO, _ZERO)
        window = ([previous] if previous is not None else []) + points
        return DayRow(
            day=day,
            pnl=float(last.trading_pnl - base.trading_pnl),
            gross_pnl=float((last.gross_equity - last.external)
                            - (base.gross_equity - base.external)),
            fees=float(last.fees - base.fees),
            flows=float(last.external - base.external),
            end_equity=float(last.equity),
            ret=self.time_weighted_return(window, from_capital=previous is None),
            fills=fills,
            trips=trips,
        )
