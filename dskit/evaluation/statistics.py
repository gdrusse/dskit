"""The report's statistics table, read off the book and the log.

Every number here is either a reading of :class:`~dskit.evaluation.book.EvaluationBook`
or a call into :mod:`dskit.pipeline.stats` — the estimators (Sharpe with
its interval, PSR, DSR, profit factor, payoff ratio, the trade t-test via
``across_fold_t``, the tail mean, the drawdown) live there so a second
report cannot drift from this one.

Two disciplines from the research spec shape the table. Sharpe is computed
on DAILY returns and never annualised from bars (Lo 2002); CAGR, Sortino
and Calmar are absent on purpose — on a short intraday run they are noise
dressed as precision. And every statistic whose meaning depends on a
sample carries its ``n`` and the ``min_n`` it needs: below it the row is
labelled "insufficient n" rather than hidden, so the reader sees both the
number and why not to trust it.

The table's names are :data:`STAT_NAMES`; criteria cite them.

Import cost: stdlib plus ``dskit.pipeline`` and ``dskit.production``.
"""

from __future__ import annotations

import statistics as _stdstats
from dataclasses import dataclass

from dskit.pipeline.stats import (
    across_fold_t,
    deflated_sharpe_ratio,
    lower_tail_mean,
    payoff_ratio,
    probabilistic_sharpe_ratio,
    profit_factor,
    sharpe_ratio,
)

__all__ = ["DEFAULT_MIN_N", "STAT_NAMES", "Statistic", "StatisticsTable"]

#: The sample below which a sample-dependent statistic is "insufficient n"
#: (~30 independent trades or days — the research spec's floor).
DEFAULT_MIN_N = 30

#: The tail level of the trade expected-shortfall row.
_TAIL_ALPHA = 0.95

#: Milliseconds per minute, for holding-time rows.
_MINUTE_MS = 60_000

#: Every statistic the table defines, in rendering order. A test pins that
#: the table builds exactly these.
STAT_NAMES = (
    "net_pnl", "gross_pnl", "fees", "cost_share", "twr",
    "max_drawdown", "max_drawdown_pct", "max_drawdown_minutes",
    "trades", "hit_rate", "avg_win", "avg_loss", "payoff_ratio", "profit_factor",
    "trade_mean", "trade_t", "trade_p", "trade_es95",
    "days", "daily_mean_return", "daily_sharpe", "daily_sharpe_ci_low",
    "daily_sharpe_ci_high", "psr", "dsr",
    "decisions", "orders", "fills", "fill_ratio", "refusal_rate", "skip_rate",
    "turnover_per_day", "time_in_market", "avg_exposure",
    "holding_median_minutes", "holding_p90_minutes",
)


@dataclass(frozen=True)
class Statistic:
    """One row of the table.

    Parameters
    ----------
    name : str
    value : float or int or None
        ``None`` when undefined on this sample.
    n : int or None
        The sample it rests on; ``None`` for an accounting identity.
    min_n : int
        The sample it needs (0 for an identity).
    note : str
        How it was computed, or why it is undefined.

    Examples
    --------
    ::

        Statistic("trade_mean", 1.2, 12, 30, "mean trade P&L").sufficient  # False
    """

    name: str
    value: object
    n: object
    min_n: int
    note: str

    @property
    def sufficient(self):
        """True unless the statistic has a sample smaller than ``min_n``."""
        return self.n is None or self.n >= self.min_n

    def to_obj(self):
        """Return the row as a JSON-ready dict.

        Returns
        -------
        dict
        """
        return {"name": self.name, "value": self.value, "n": self.n, "min_n": self.min_n,
                "sufficient": self.sufficient, "note": self.note}


class StatisticsTable:
    """Every statistic of a run, keyed by :data:`STAT_NAMES`.

    Parameters
    ----------
    book : EvaluationBook
    min_n : int
        The floor applied to trade- and day-sample statistics.

    Examples
    --------
    ::

        table = StatisticsTable(EvaluationBook(log))
        table.get("net_pnl").value
    """

    def __init__(self, book, min_n=DEFAULT_MIN_N):
        self.book, self.min_n = book, min_n
        self.log = book.log
        self.pnls = [trip.pnl for trip in book.round_trips]
        self.daily = [row.ret for row in book.days() if row.ret is not None]
        rows = (self._pnl_rows() + self._trade_rows() + self._daily_rows()
                + self._activity_rows() + self._exposure_rows())
        self._rows = {row.name: row for row in rows}

    @property
    def names(self):
        """Every statistic name, in rendering order."""
        return tuple(self._rows)

    @property
    def rows(self):
        """Every :class:`Statistic`, in rendering order."""
        return tuple(self._rows.values())

    def get(self, name):
        """Return the statistic called ``name``, or None.

        Parameters
        ----------
        name : str

        Returns
        -------
        Statistic or None
        """
        return self._rows.get(name)

    def to_obj(self):
        """Return ``{name: row}`` as JSON-ready dicts.

        Returns
        -------
        dict
        """
        return {name: row.to_obj() for name, row in self._rows.items()}

    # -- groups --------------------------------------------------------------

    def _identity(self, name, value, note):
        """Build an accounting row: no sample requirement."""
        return Statistic(name, value, None, 0, note)

    def _sampled(self, name, value, n, note):
        """Build a sample-dependent row under the table's ``min_n``."""
        return Statistic(name, value, n, self.min_n, note)

    def _pnl_rows(self):
        """Net/gross P&L, fees, cost share, TWR and the drawdown rows."""
        points = self.book.points
        last = points[-1] if points else None
        net = float(last.trading_pnl) if last else 0.0
        gross = float(last.gross_equity - last.external) if last else 0.0
        fees = float(last.fees) if last else 0.0
        drawdowns = self.book.drawdowns()
        return [
            self._identity("net_pnl", net, "equity minus external flows, fees included"),
            self._identity("gross_pnl", gross, "the same before fees"),
            self._identity("fees", fees, "sum of fill fees"),
            self._identity("cost_share", fees / abs(gross) if gross else None,
                           "fees / |gross P&L|"),
            self._identity("twr", self.book.time_weighted_return(),
                           "time-weighted return, external flows excluded; not annualised"),
            self._identity("max_drawdown", self.book.max_drawdown(),
                           "largest trading-P&L fall from a peak (short windows understate it)"),
            self._identity("max_drawdown_pct", drawdowns["max_pct"],
                           "worst drawdown over the equity at its peak"),
            self._identity("max_drawdown_minutes", drawdowns["max_duration_ms"] / _MINUTE_MS,
                           "longest time below a prior peak"),
        ]

    def _trade_rows(self):
        """Build the round-trip rows: count, hit rate, win/loss, ratios, t-test, tail."""
        pnls, n = self.pnls, len(self.pnls)
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p < 0]
        tested = across_fold_t(pnls) if n >= 2 else None
        return [
            self._identity("trades", n, "closed FIFO round trips"),
            self._sampled("hit_rate", len(wins) / n if n else None, n, "share of trips > 0"),
            self._sampled("avg_win", sum(wins) / len(wins) if wins else None, n,
                          "mean winning trip"),
            self._sampled("avg_loss", sum(losses) / len(losses) if losses else None, n,
                          "mean losing trip"),
            self._sampled("payoff_ratio", payoff_ratio(pnls) if pnls else None, n,
                          "avg win / |avg loss|"),
            self._sampled("profit_factor", profit_factor(pnls) if pnls else None, n,
                          "sum wins / |sum losses|; undefined without a loss"),
            self._sampled("trade_mean", sum(pnls) / n if n else None, n, "mean trip P&L"),
            self._sampled("trade_t", None if tested is None else tested["t"], n,
                          "one-sample t of mean trip P&L (overlapping trips inflate n)"),
            self._sampled("trade_p", None if tested is None else tested["p_value"], n,
                          "one-sided p that mean trip P&L <= 0"),
            self._sampled("trade_es95", lower_tail_mean(pnls, _TAIL_ALPHA) if pnls else None,
                          n, "mean of the worst 5% of trips"),
        ]

    def _daily_rows(self):
        """Daily-return rows: Sharpe with CI, PSR and DSR against the recorded trials."""
        daily, n = self.daily, len(self.daily)
        start = self.log.run_start
        trials = start.get("trials", 1) if start is not None else 1
        sharpe = sharpe_ratio(daily) if n >= 2 else {}
        psr = probabilistic_sharpe_ratio(daily) if n >= 2 else None
        dsr = deflated_sharpe_ratio(daily, trials)["dsr"] if n >= 2 else None
        return [
            self._identity("days", n, "session days with a daily return"),
            self._sampled("daily_mean_return", sum(daily) / n if n else None, n,
                          "mean daily time-weighted return"),
            self._sampled("daily_sharpe", sharpe.get("sharpe"), n,
                          "daily Sharpe, not annualised (Lo 2002)"),
            self._sampled("daily_sharpe_ci_low", sharpe.get("ci_low"), n,
                          "95% interval, skew/kurtosis-aware SE"),
            self._sampled("daily_sharpe_ci_high", sharpe.get("ci_high"), n,
                          "95% interval, skew/kurtosis-aware SE"),
            self._sampled("psr", psr, n, "P(true daily Sharpe > 0)"),
            self._sampled("dsr", dsr, n, f"PSR against the best of {trials} trial(s)"),
        ]

    def _activity_rows(self):
        """Decision/order/fill counts and the fill, refusal and skip rates."""
        census = self.log.census().to_obj()
        decisions, orders = census["decisions"], census["orders"]
        filled = census["order_states"].get("filled", 0) + census["order_states"].get(
            "partial", 0)
        return [
            self._identity("decisions", decisions, "decision events"),
            self._identity("orders", orders, "order events"),
            self._identity("fills", census["fills"], "fill events"),
            self._identity("fill_ratio", filled / orders if orders else None,
                           "orders with any fill / orders"),
            self._identity("refusal_rate",
                           census["actions"]["refuse"] / decisions if decisions else None,
                           "refuse decisions / decisions"),
            self._identity("skip_rate",
                           census["actions"]["skip"] / decisions if decisions else None,
                           "skip decisions / decisions"),
        ]

    def _exposure_rows(self):
        """Turnover, time in market, average exposure and holding times."""
        points = self.book.points
        equities = [float(p.equity) for p in points if p.equity > 0]
        days = max(1, len(self.book.days()))
        mean_equity = sum(equities) / len(equities) if equities else None
        exposures = [p.exposure for p in points if p.exposure is not None]
        held = [trip.holding_ms / _MINUTE_MS for trip in self.book.round_trips]
        n = len(held)
        return [
            self._identity("turnover_per_day",
                           float(self.book.traded_notional) / mean_equity / days
                           if mean_equity else None,
                           "traded notional / mean equity / session days"),
            self._identity("time_in_market",
                           sum(1 for p in points if p.gross_notional) / len(points)
                           if points else None,
                           "share of instants with an open position"),
            self._identity("avg_exposure",
                           sum(exposures) / len(exposures) if exposures else None,
                           "mean gross notional / equity"),
            self._sampled("holding_median_minutes", _stdstats.median(held) if held else None,
                          n, "median round-trip holding time"),
            self._sampled("holding_p90_minutes", _p90(held), n,
                          "90th percentile holding time"),
        ]


def _p90(values):
    """Return the 90th percentile (inclusive method), the value itself for one."""
    if not values:
        return None
    if len(values) == 1:
        return values[0]
    return _stdstats.quantiles(values, n=10, method="inclusive")[8]
