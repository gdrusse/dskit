"""Phase-one stock and factor price wiring through DSKit's bar feature node."""

from __future__ import annotations

from datetime import date
from math import isfinite
from typing import Any, Mapping

from dskit.pipeline.libs.bar_features import DailyBarFeatures
from dskit.pipeline.node import Node, check_int_param, reject_unknown_params


class TechAdjustedReturns(Node):
    """Align configured adjusted-close series and emit same-day log returns.

    Each input port is named in params. The factor port is omitted from
    records and emitted separately as benchmark. No stale factor close is
    carried beyond the last observed date. Filling internal missing sessions
    is optional, bounded by max_forward_fill_sessions, and flagged.
    Downstream decisions must lag these same-day returns.
    """

    role = "transform"
    outputs = ("records", "benchmark", "corporate_events", "summary")
    _PARAMS = ("end_before", "ports", "factor_port", "max_forward_fill_sessions")

    @classmethod
    def validate_params(cls, params: dict[str, Any]) -> list[str]:
        """Validate the source port map and sealed upper date boundary."""
        problems: list[str] = []
        reject_unknown_params(problems, params, cls._PARAMS)
        value = params.get("end_before")
        try:
            if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
                raise ValueError
        except ValueError:
            problems.append("end_before must be an ISO YYYY-MM-DD date")
        ports = params.get("ports")
        if not isinstance(ports, dict) or len(ports) < 2 or not all(
            isinstance(port, str) and port and isinstance(symbol, str) and symbol
            for port, symbol in ports.items()
        ):
            problems.append("ports must map at least two input names to symbols")
        elif len(set(ports.values())) != len(ports):
            problems.append("ports symbols must be distinct")
        if "max_forward_fill_sessions" in params:
            check_int_param(problems, "max_forward_fill_sessions",
                            params["max_forward_fill_sessions"], ge=0)
        factor_port = params.get("factor_port")
        if not isinstance(factor_port, str) or not isinstance(ports, dict) or factor_port not in ports:
            problems.append("factor_port must name a configured port")
        return problems

    def _series(self, port: str, symbol: str, rows: Any) -> dict[str, tuple[float, float, float]]:
        """Validate one manifest-backed price stream and index it by date."""
        if rows is None:
            raise ValueError(f"{self.key}: missing {port} price records")
        out: dict[str, tuple[float, float, float]] = {}
        for row in rows:
            if not isinstance(row, Mapping):
                raise ValueError(f"{self.key}: {port} price row is not a mapping")
            if row.get("symbol") != symbol:
                raise ValueError(f"{self.key}: {port} has wrong symbol {row.get('symbol')!r}")
            day = row.get("date")
            try:
                if not isinstance(day, str) or date.fromisoformat(day).isoformat() != day:
                    raise ValueError
            except ValueError as exc:
                raise ValueError(f"{self.key}: {port} has invalid date {day!r}") from exc
            if day >= self.params["end_before"]:
                raise ValueError(f"{self.key}: future price date {day} reaches sealed period")
            if day in out:
                raise ValueError(f"{self.key}: duplicate {port} price date {day}")
            value = row.get("adjusted_close")
            if isinstance(value, bool):
                raise ValueError(f"{self.key}: {port} adjusted_close must be positive finite")
            try:
                close = float(value)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{self.key}: {port} adjusted_close must be positive finite") from exc
            if not isfinite(close) or close <= 0:
                raise ValueError(f"{self.key}: {port} adjusted_close must be positive finite")
            try:
                split = float(row["split_coefficient"])
                dividend = float(row["dividend_amount"])
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError(f"{self.key}: {port} lacks valid corporate action fields") from exc
            if not isfinite(split) or split <= 0 or not isfinite(dividend) or dividend < 0:
                raise ValueError(f"{self.key}: {port} has invalid corporate action values")
            out[day] = (close, split, dividend)
        if not out:
            raise ValueError(f"{self.key}: {port} has no price rows")
        return out

    def run(self, ctx: Any, inputs: dict[str, Any]) -> dict[str, Any]:
        """Compute returns only on the exact common trading calendar."""
        ports = self.params["ports"]
        factor_port = self.params["factor_port"]
        factor_symbol = ports[factor_port]
        indexed = {
            port: self._series(port, symbol, inputs.get(port))
            for port, symbol in ports.items()
        }
        first = max(min(series) for series in indexed.values())
        last = min(max(series) for series in indexed.values())
        if first >= last:
            raise ValueError(f"{self.key}: fewer than two common price dates")
        calendar = sorted({day for series in indexed.values() for day in series if first <= day <= last})
        fill_limit = self.params.get("max_forward_fill_sessions", 0)
        aligned: dict[str, dict[str, tuple[float, float | None, float | None, bool]]] = {}
        for port, series in indexed.items():
            current: tuple[float, float, float] | None = None
            gap = 0
            aligned[port] = {}
            for day in calendar:
                if day in series:
                    current = series[day]
                    gap = 0
                    aligned[port][day] = (*current, False)
                else:
                    gap += 1
                    if current is not None and gap <= fill_limit:
                        aligned[port][day] = (current[0], None, None, True)
        common = [day for day in calendar if all(day in aligned[port] for port in ports)]
        if len(common) < 2:
            raise ValueError(f"{self.key}: fewer than two common price dates")
        bars = [
            {"symbol": symbol, "date": day, "adjusted_close": aligned[port][day][0]}
            for port, symbol in ports.items()
            for day in common
        ]
        base = DailyBarFeatures(
            f"{self.key}_returns",
            {
                "entity_field": "symbol",
                "date_field": "date",
                "close_field": "adjusted_close",
                "realized_vol": {
                    "windows": [1],
                    "field": "_return_magnitude_{window}",
                    "return_field": "log_return",
                },
            },
        ).run(ctx, {"bars": bars})["records"]
        by_symbol = {symbol: port for port, symbol in ports.items()}
        factor = {row["date"]: row["log_return"] for row in base if row["symbol"] == factor_symbol}
        records = [
            {
                "symbol": row["symbol"],
                "date": row["date"],
                "adjusted_close": aligned[by_symbol[row["symbol"]]][row["date"]][0],
                "split_coefficient": aligned[by_symbol[row["symbol"]]][row["date"]][1],
                "dividend_amount": aligned[by_symbol[row["symbol"]]][row["date"]][2],
                "imputed": aligned[by_symbol[row["symbol"]]][row["date"]][3],
                "log_return": row["log_return"],
                "factor_return": factor[row["date"]],
            }
            for row in base if row["symbol"] != factor_symbol
        ]
        benchmark = [
            {"symbol": factor_symbol, "date": row["date"],
             "adjusted_close": aligned[factor_port][row["date"]][0],
             "log_return": row["log_return"], "imputed": aligned[factor_port][row["date"]][3]}
            for row in base if row["symbol"] == factor_symbol
        ]
        records.sort(key=lambda row: (row["date"], row["symbol"]))
        corporate_events = [
            {"symbol": row["symbol"], "date": row["date"],
             "split_coefficient": row["split_coefficient"],
             "dividend_amount": row["dividend_amount"]}
            for row in records
            if not row["imputed"] and (row["split_coefficient"] != 1.0 or row["dividend_amount"] > 0.0)
        ]
        summary = {
            "common_dates": len(common),
            "start": common[0],
            "end": common[-1],
            "source_dates": {port: len(series) for port, series in indexed.items()},
            "excluded_dates": {port: len(set(series) - set(common)) for port, series in indexed.items()},
            "imputed_dates": {port: sum(aligned[port][day][3] for day in common) for port in ports},
        }
        return {"records": records, "benchmark": benchmark,
                "corporate_events": corporate_events, "summary": summary}
