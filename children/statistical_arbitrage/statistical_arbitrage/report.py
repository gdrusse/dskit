"""Evaluation metrics and plots for the statistical arbitrage study."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from math import sqrt
from typing import Any


class PerformanceReporter:
    """Compare net option books on a common calendar and capital base."""

    def __init__(self, *, capital_usd: float, sessions_per_year: int = 252) -> None:
        if capital_usd <= 0 or sessions_per_year < 2:
            raise ValueError("capital and sessions_per_year must be positive")
        self.capital_usd = float(capital_usd)
        self.sessions_per_year = int(sessions_per_year)

    @staticmethod
    def daily_pnl_from_marks(marks: Sequence[Mapping[str, Any]], dates: Sequence[str]) -> list[float]:
        """Difference each pair's running net mark, then aggregate by calendar date."""
        import pandas as pd

        days = list(dates)
        if days != sorted(set(days)):
            raise ValueError("dates must be unique and ascending")
        if not marks:
            return [0.0] * len(days)
        frame = pd.DataFrame(marks)
        if frame.duplicated(["id", "date"]).any():
            raise ValueError("duplicate pair mark date")
        if not set(frame["date"]).issubset(days):
            raise ValueError("mark date falls outside evaluation calendar")
        frame = frame.sort_values(["id", "date"])
        frame["change"] = frame.groupby("id", sort=False)["pnl_usd"].diff()
        frame["change"] = frame["change"].fillna(frame["pnl_usd"])
        by_day = frame.groupby("date")["change"].sum()
        return [float(by_day.get(day, 0.0)) for day in days]

    def _metrics(
        self,
        returns: Any,
        equity: Any,
        trades: Sequence[Mapping[str, Any]],
        date_index: Mapping[str, int],
        comparison: Any | None = None,
    ) -> dict[str, Any]:
        """Compute one book's net metrics, leaving undefined ratios null."""
        import numpy as np

        ret = np.asarray(returns, dtype=float)
        curve = np.asarray(equity, dtype=float)
        excess = ret - np.asarray(comparison, dtype=float) if comparison is not None else ret
        spread = float(np.std(excess, ddof=1)) if len(excess) > 1 else 0.0
        sharpe = sqrt(self.sessions_per_year) * float(np.mean(ret)) / float(
            np.std(ret, ddof=1)
        ) if len(ret) > 1 and np.std(ret, ddof=1) > 0 else None
        ratio = sqrt(self.sessions_per_year) * float(np.mean(excess)) / spread if spread > 0 else None
        peak = np.maximum.accumulate(np.r_[self.capital_usd, curve])[1:]
        closed = [t for t in trades if t.get("pnl_usd") is not None]
        durations = [
            date_index[t["exit_date"]] - date_index[t["entry_date"]]
            for t in closed
            if t.get("entry_date") in date_index and t.get("exit_date") in date_index
        ]
        return {
            "total_return": float(curve[-1] / self.capital_usd - 1),
            "sharpe": sharpe,
            "information_ratio": ratio if comparison is not None else None,
            "max_drawdown": float(np.min(curve / peak - 1)),
            "trade_count": len(closed),
            "win_rate": (sum(float(t["pnl_usd"]) > 0 for t in closed) / len(closed)
                         if closed else None),
            "average_trade_duration_sessions": (
                float(np.mean(durations)) if durations else None
            ),
            "equity_end_usd": float(curve[-1]),
        }

    def evaluate(
        self,
        *,
        dates: Sequence[str],
        model_pnl: Sequence[float],
        baseline_pnl: Sequence[float],
        adjusted_prices: Mapping[str, Sequence[float]],
        model_trades: Sequence[Mapping[str, Any]],
        baseline_trades: Sequence[Mapping[str, Any]],
        qqq_symbol: str = "QQQ",
    ) -> dict[str, Any]:
        """Measure paired books and adjusted-price benchmarks on matching dates."""
        import numpy as np

        days = list(dates)
        if not days or days != sorted(set(days)):
            raise ValueError("dates must be nonempty, unique and ascending")
        n = len(days)
        if len(model_pnl) != n or len(baseline_pnl) != n:
            raise ValueError("P&L streams must match dates")
        if qqq_symbol not in adjusted_prices or len(adjusted_prices) < 2:
            raise ValueError("QQQ and at least one equity benchmark symbol required")
        price = {symbol: np.asarray(values, dtype=float)
                 for symbol, values in adjusted_prices.items()}
        if any(len(v) != n or not np.isfinite(v).all() or (v <= 0).any()
               for v in price.values()):
            raise ValueError("adjusted prices must be positive finite and match dates")
        stock = [v for symbol, v in price.items() if symbol != qqq_symbol]
        if not stock:
            raise ValueError("equal-weight benchmark has no stocks")
        stock_returns = np.stack([np.r_[0.0, v[1:] / v[:-1] - 1.0] for v in stock])
        market_returns = stock_returns.mean(axis=0)
        qqq = price[qqq_symbol]
        qqq_returns = np.r_[0.0, qqq[1:] / qqq[:-1] - 1.0]
        model_cash = np.asarray(model_pnl, dtype=float)
        baseline_cash = np.asarray(baseline_pnl, dtype=float)
        if not np.isfinite(model_cash).all() or not np.isfinite(baseline_cash).all():
            raise ValueError("P&L must be finite")
        model_equity = self.capital_usd + np.cumsum(model_cash)
        baseline_equity = self.capital_usd + np.cumsum(baseline_cash)
        if (model_equity <= 0).any() or (baseline_equity <= 0).any():
            raise ValueError("book equity exhausted")
        model_returns = model_cash / np.r_[self.capital_usd, model_equity[:-1]]
        baseline_returns = baseline_cash / np.r_[self.capital_usd, baseline_equity[:-1]]
        market_equity = self.capital_usd * np.cumprod(1 + market_returns)
        qqq_equity = self.capital_usd * np.cumprod(1 + qqq_returns)
        index = {day: i for i, day in enumerate(days)}
        zero_trades: list[dict] = []
        model = self._metrics(model_returns, model_equity, model_trades, index, market_returns)
        model["information_ratio_vs_baseline"] = self._metrics(
            model_returns, model_equity, model_trades, index, baseline_returns
        )["information_ratio"]
        return {
            "model": model,
            "baseline": self._metrics(
                baseline_returns, baseline_equity, baseline_trades, index, market_returns
            ),
            "equal_weight": self._metrics(
                market_returns, market_equity, zero_trades, index
            ),
            "qqq": self._metrics(qqq_returns, qqq_equity, zero_trades, index),
            "daily": [
                {"date": day, "model_equity": float(model_equity[i]),
                 "baseline_equity": float(baseline_equity[i]),
                 "equal_weight_equity": float(market_equity[i]),
                 "qqq_equity": float(qqq_equity[i])}
                for i, day in enumerate(days)
            ],
        }
