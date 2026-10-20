"""Evaluation metrics and plots for the statistical arbitrage study."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date
from math import isfinite, sqrt
from pathlib import Path
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
        prior_adjusted_prices: Mapping[str, float] | None = None,
        trade_date_index: Mapping[str, int] | None = None,
    ) -> dict[str, Any]:
        """Score daily marks in-window; trade cohorts are supplied by close date.

        ``trade_date_index`` is the full session calendar, so a trade opening
        before the reporting window still has its true duration.
        """
        import numpy as np

        days = list(dates)
        if not days or days != sorted(set(days)):
            raise ValueError("dates must be nonempty, unique and ascending")
        n = len(days)
        if len(model_pnl) != n or len(baseline_pnl) != n:
            raise ValueError("P&L streams must match dates")
        cohort = [*model_trades, *baseline_trades]
        close_days = set(days)
        if any(trade.get("exit_date") not in close_days for trade in cohort):
            raise ValueError("trade close date falls outside reporting cohort")
        if trade_date_index is not None and any(
            trade.get("entry_date") not in trade_date_index
            or trade.get("exit_date") not in trade_date_index
            for trade in cohort
        ):
            raise ValueError("trade duration needs both dates in global session index")
        if qqq_symbol not in adjusted_prices or len(adjusted_prices) < 2:
            raise ValueError("QQQ and at least one equity benchmark symbol required")
        price = {symbol: np.asarray(values, dtype=float)
                 for symbol, values in adjusted_prices.items()}
        if any(len(v) != n or not np.isfinite(v).all() or (v <= 0).any()
               for v in price.values()):
            raise ValueError("adjusted prices must be positive finite and match dates")
        if prior_adjusted_prices is not None and (
            set(prior_adjusted_prices) != set(price)
            or any(not np.isfinite(value) or value <= 0
                   for value in prior_adjusted_prices.values())
        ):
            raise ValueError("prior adjusted prices must match symbols and be positive finite")
        stock = [v for symbol, v in price.items() if symbol != qqq_symbol]
        if not stock:
            raise ValueError("equal-weight benchmark has no stocks")
        stock_returns = np.stack([
            np.r_[v[0] / prior_adjusted_prices[symbol] - 1.0
                  if prior_adjusted_prices is not None else 0.0,
                  v[1:] / v[:-1] - 1.0]
            for symbol, v in price.items() if symbol != qqq_symbol
        ])
        market_returns = stock_returns.mean(axis=0)
        qqq = price[qqq_symbol]
        qqq_returns = np.r_[
            qqq[0] / prior_adjusted_prices[qqq_symbol] - 1.0
            if prior_adjusted_prices is not None else 0.0,
            qqq[1:] / qqq[:-1] - 1.0]
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
        index = trade_date_index if trade_date_index is not None else {
            day: i for i, day in enumerate(days)
        }
        zero_trades: list[dict] = []
        model = self._metrics(model_returns, model_equity, model_trades, index, market_returns)
        model["information_ratio_vs_baseline"] = self._metrics(
            model_returns, model_equity, model_trades, index, baseline_returns
        )["information_ratio"]
        return {
            "trade_cohort_policy": "close_date",
            "pnl_cohort_policy": "daily_mark_date",
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

    def save_plots(
        self,
        output_dir: str | Path,
        evaluation: Mapping[str, Any],
        network_rows: Mapping[str, Sequence[Mapping[str, Any]]],
        *,
        symbols: Sequence[str],
        graph_fits: Sequence[Mapping[str, Any]] = (),
        entry_z: float | None = None,
    ) -> list[str]:
        """Save descriptive plots from scored books and pinned structural rows.

        Edge widths are deliberately uniform: pair signal rows retain membership,
        but not fitted partial-correlation weights. Trade flags mark actual fill
        dates at the most recent strictly earlier close Z for the selected pair.
        """
        import matplotlib

        matplotlib.use("Agg")
        from matplotlib import pyplot as plt
        import networkx as nx

        output = Path(output_dir)
        output.mkdir(parents=True, exist_ok=True)
        if not set(network_rows) >= {"atm", "skew"}:
            raise ValueError("ATM and skew pinned network rows required")
        names: list[str] = []
        atm = list(network_rows["atm"])
        refits = [row for row in graph_fits if row.get("refit") is True]
        fit_date = (max(row["fit_date"] for row in refits)
                    if refits else max((row["fit_date"] for row in atm), default=None))
        edges = sorted({(row["symbol_a"], row["symbol_b"])
                        for row in atm if row.get("fit_date") == fit_date
                        and row.get("status") == "ok"})
        graph = nx.Graph()
        graph.add_nodes_from(symbols)
        graph.add_edges_from(edges)
        figure, axis = plt.subplots(figsize=(7, 5))
        try:
            locations = nx.spring_layout(graph, seed=23)
            nx.draw_networkx(graph, pos=locations, ax=axis, with_labels=True,
                             node_color="#b9d9f3", edge_color="#356b9b",
                             width=2.0, node_size=1400, font_size=11)
            axis.set_title(f"Selected ATM network at fit {fit_date or 'none'}")
            axis.text(0.5, -0.08,
                      "Equal-width links show selected pairs; saved rows have no edge weights.",
                      transform=axis.transAxes, ha="center", fontsize=8)
            axis.axis("off")
            figure.tight_layout()
            name = "network-atm.png"
            figure.savefig(output / name, dpi=150, bbox_inches="tight")
            names.append(name)
        finally:
            plt.close(figure)

        for variant in ("atm", "skew"):
            book = evaluation["books"][f"{variant}_network"]
            rows = [row for row in network_rows[variant]
                    if row.get("status") == "ok" and row.get("z") is not None
                    and isfinite(float(row["z"]))]
            pairs = sorted({(row["symbol_a"], row["symbol_b"]) for row in rows})
            trade_counts = {
                pair: sum(trade.get("id", "").endswith(f":{pair[0]}:{pair[1]}")
                          for trade in book["trades"])
                for pair in pairs
            }
            selected = max(pairs, key=lambda pair: (
                trade_counts[pair],
                sum((row["symbol_a"], row["symbol_b"]) == pair for row in rows),
                pair,
            )) if pairs else None
            series = sorted(
                (row for row in rows
                 if (row["symbol_a"], row["symbol_b"]) == selected),
                key=lambda row: row["date"],
            )
            figure, axis = plt.subplots(figsize=(11, 4))
            try:
                if series:
                    x = [date.fromisoformat(row["date"]) for row in series]
                    y = [float(row["z"]) for row in series]
                    axis.plot(x, y, color="#285f9e", linewidth=1,
                              label=f"{selected[0]} − {selected[1]} close Z")
                    known = {row["date"]: float(row["z"]) for row in series}
                    selected_trades = [trade for trade in book["trades"]
                                       if trade.get("id", "").endswith(
                                           f":{selected[0]}:{selected[1]}")]
                    for field, marker, color, label in (
                        ("entry_date", "^", "#15803d", "entry fill"),
                        ("exit_date", "v", "#b91c1c", "exit fill"),
                    ):
                        points = []
                        for trade in selected_trades:
                            fill = trade.get(field)
                            prior = (max((day for day in known if day < fill), default=None)
                                     if fill else None)
                            if prior is not None:
                                points.append((date.fromisoformat(fill), known[prior]))
                        if points:
                            axis.scatter([point[0] for point in points],
                                         [point[1] for point in points],
                                         marker=marker, color=color, s=45, label=label,
                                         zorder=3)
                else:
                    axis.text(0.5, 0.5, "No valid pinned Z series", ha="center",
                              va="center", transform=axis.transAxes)
                axis.axhline(0, color="#555555", linewidth=0.8)
                if entry_z is not None:
                    axis.axhline(entry_z, color="#9a6700", linestyle="--",
                                 linewidth=0.8)
                    axis.axhline(-entry_z, color="#9a6700", linestyle="--",
                                 linewidth=0.8)
                axis.set_title(f"{variant.upper()} rolling pair Z")
                axis.set_ylabel("Z score")
                axis.set_xlabel("Session")
                axis.text(0.5, -0.28,
                          "Most-traded selected pair; flags at fills use prior-close Z.",
                          transform=axis.transAxes, ha="center", fontsize=8)
                if axis.get_legend_handles_labels()[0]:
                    axis.legend(loc="best")
                figure.autofmt_xdate()
                figure.tight_layout()
                name = f"z-{variant}-network.png"
                figure.savefig(output / name, dpi=150, bbox_inches="tight")
                names.append(name)
            finally:
                plt.close(figure)

        figure, axes = plt.subplots(2, 1, figsize=(11, 7), sharex=True)
        try:
            for axis, variant in zip(axes, ("atm", "skew")):
                daily = evaluation["books"][f"{variant}_network"]["full"]["daily"]
                days = [date.fromisoformat(row["date"]) for row in daily]
                for field, label in (
                    ("model_equity", f"{variant.upper()} network"),
                    ("baseline_equity", f"{variant.upper()} all-pairs baseline"),
                    ("equal_weight_equity", "Equal-weight tech"),
                    ("qqq_equity", "QQQ"),
                ):
                    axis.plot(days, [row[field] for row in daily], label=label)
                axis.set_title(f"{variant.upper()} scored cumulative equity")
                axis.set_ylabel("USD")
                axis.grid(alpha=0.25)
                axis.legend(loc="best")
            axes[-1].set_xlabel("Session")
            figure.autofmt_xdate()
            figure.tight_layout()
            name = "cumulative-equity.png"
            figure.savefig(output / name, dpi=150, bbox_inches="tight")
            names.append(name)
        finally:
            plt.close(figure)
        return names
