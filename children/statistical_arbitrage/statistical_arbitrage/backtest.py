"""Research-only volatility-pair trade scheduling and quote replay.

Signal decisions are made after the date's close; orders use the next
observed session. Pricing and reporting are separate from this clock.
"""
from __future__ import annotations

from bisect import bisect_left
from collections import Counter, defaultdict
from datetime import date
from math import isfinite
from typing import Iterable

from dskit.pipeline.option_pricing import black76_vega


class VolatilityArbitrageBacktester:
    """Schedule one active spread at a time from dated, lag-safe signals."""

    def __init__(self, *, entry_z: float, stop_z: float, max_hold_sessions: int) -> None:
        if not (0 < entry_z < stop_z):
            raise ValueError("require 0 < entry_z < stop_z")
        if max_hold_sessions < 1:
            raise ValueError("max_hold_sessions must be positive")
        self.entry_z = float(entry_z)
        self.stop_z = float(stop_z)
        self.max_hold_sessions = int(max_hold_sessions)

    def intents(self, signals: Iterable[dict], sessions: Iterable[str]) -> list[dict]:
        """Return next-session entry and exit intents, with no same-date fill.

        A single pair can be active for this research sleeve. Competing entry
        signals choose the largest absolute Z, then lexical pair order.
        """
        days = list(sessions)
        if days != sorted(set(days)):
            raise ValueError("sessions must be unique and ascending")
        by_day: dict[str, list[dict]] = defaultdict(list)
        for row in signals:
            if row.get("status") == "ok" and isfinite(float(row.get("z", float("nan")))):
                by_day[row["date"]].append(row)
        planned: list[dict] = []
        active: dict | None = None
        for index, day in enumerate(days):
            today = by_day.get(day, [])
            if active is not None:
                own = next((r for r in today if (r["symbol_a"], r["symbol_b"])
                            == (active["symbol_a"], active["symbol_b"])), None)
                z = float(own["z"]) if own is not None else None
                direction = active["direction"]
                reason = None
                if z is not None and direction * z <= 0:
                    reason = "mean_cross"
                elif z is not None and direction * z >= self.stop_z:
                    reason = "stop_z"
                elif index - active["entry_index"] >= self.max_hold_sessions - 1:
                    reason = "max_hold"
                if reason and index + 1 < len(days):
                    active["exit_date"] = days[index + 1]
                    active["exit_reason"] = reason
                    planned.append({k: v for k, v in active.items()
                                    if k not in {"direction", "entry_index"}})
                    active = None
                    continue
            if active is None and index + 1 < len(days):
                choices = [r for r in today if abs(float(r["z"])) >= self.entry_z]
                choices.sort(key=lambda r: (-abs(float(r["z"])), r["symbol_a"], r["symbol_b"]))
                if choices:
                    chosen = choices[0]
                    positive = float(chosen["z"]) > 0
                    a, b = chosen["symbol_a"], chosen["symbol_b"]
                    active = {
                        "symbol_a": a, "symbol_b": b, "decision_date": day,
                        "entry_date": days[index + 1], "entry_index": index + 1,
                        "short_symbol": a if positive else b,
                        "long_symbol": b if positive else a,
                        "direction": 1 if positive else -1,
                        "entry_z": float(chosen["z"]),
                        "exit_date": None, "exit_reason": "open_at_end",
                    }
        if active is not None:
            planned.append({k: v for k, v in active.items()
                            if k not in {"direction", "entry_index"}})
        return planned

    def pair_orders(
        self,
        intents: Iterable[dict],
        surfaces: Iterable[dict],
        *,
        max_contracts: int,
        max_vega_mismatch: float,
        max_half_spread_over_premium: float | None = None,
        max_gross_notional_usd: float | None = None,
        corporate_events: Iterable[dict] = (),
        rates: Iterable[dict] = (),
        require_rate: bool = False,
        rate_date_field: str = "observation_date",
        rate_value_field: str = "DGS3MO",
        rate_scale: float = 0.01,
        max_rate_age_days: int = 5,
    ) -> dict:
        """Select matched next-session ATM straddles and integer vega quantities.

        Surface rows decide contract selection at the entry date. A pair is
        emitted only if both nodes offer the same expiry, an exit date before
        that expiry, and acceptable initial vega matching.
        """
        if max_contracts < 1 or not 0 <= max_vega_mismatch < 1:
            raise ValueError("invalid integer-contract or vega-mismatch limit")
        if (max_half_spread_over_premium is not None
                and not 0 <= max_half_spread_over_premium < 1):
            raise ValueError("invalid quote-width limit")
        if max_gross_notional_usd is not None and max_gross_notional_usd <= 0:
            raise ValueError("invalid gross-notional limit")
        panel = {}
        for row in surfaces:
            key = row["date"], row["symbol"]
            if key in panel:
                raise ValueError(f"duplicate surface {key}")
            panel[key] = row
        pairs = []
        refused = Counter()
        events = list(corporate_events)
        rate_panel = {}
        for row in rates:
            rate_day = row[rate_date_field]
            if rate_day in rate_panel:
                raise ValueError(f"duplicate rate date {rate_day}")
            try:
                rate_panel[rate_day] = float(row[rate_value_field]) * rate_scale
            except (TypeError, ValueError) as exc:
                raise ValueError(f"invalid rate on {rate_day}") from exc
        rate_days = sorted(rate_panel)
        for intent in intents:
            entry = intent["entry_date"]
            exit_day = intent.get("exit_date")
            if exit_day is None or exit_day <= entry:
                refused["no_valid_exit"] += 1
                continue
            if entry[:4] != exit_day[:4]:
                refused["cross_year"] += 1
                continue
            if any(
                event.get("symbol") in {intent["short_symbol"], intent["long_symbol"]}
                and entry <= event.get("date", "") <= exit_day
                and (event.get("split_coefficient", 1) != 1
                     or event.get("dividend_amount", 0) != 0)
                for event in events
            ):
                refused["corporate_event"] += 1
                continue
            rate_index = bisect_left(rate_days, entry) - 1
            rate_day = rate_days[rate_index] if rate_index >= 0 else None
            hedge_rate = rate_panel[rate_day] if rate_day is not None else None
            if (require_rate and
                    (rate_day is None or
                     (date.fromisoformat(entry) - date.fromisoformat(rate_day)).days
                     > max_rate_age_days)):
                refused["missing_prior_rate"] += 1
                continue
            short = panel.get((entry, intent["short_symbol"]))
            long = panel.get((entry, intent["long_symbol"]))
            if short is None or long is None or any(
                row.get("status") != "ok" or row.get("stale_spot") is True
                for row in (short, long)
            ):
                refused["missing_surface"] += 1
                continue
            if max_half_spread_over_premium is not None and any(
                row.get("half_spread_over_premium") is None
                or not isfinite(float(row["half_spread_over_premium"]))
                or float(row["half_spread_over_premium"]) > max_half_spread_over_premium
                for row in (short, long)
            ):
                refused["wide_quote"] += 1
                continue
            expiry = short.get("expiration")
            if not expiry or expiry != long.get("expiration"):
                refused["expiry_mismatch"] += 1
                continue
            if exit_day >= expiry:
                refused["exit_not_before_expiry"] += 1
                continue
            try:
                vegas = [
                    2 * black76_vega(
                        "call", float(row["forward"]), float(row["atm_strike"]),
                        float(row["atm_iv"]), float(row["dte"]) / 365
                    )
                    for row in (short, long)
                ]
            except (KeyError, TypeError, ValueError, ZeroDivisionError):
                refused["invalid_vega"] += 1
                continue
            candidates = []
            for q_short in range(1, max_contracts + 1):
                for q_long in range(1, max_contracts + 1):
                    short_v, long_v = vegas[0] * q_short, vegas[1] * q_long
                    mismatch = abs(short_v - long_v) / max(short_v, long_v)
                    candidates.append((mismatch, q_short + q_long, q_short, q_long))
            mismatch, _sum, q_short, q_long = min(candidates)
            if mismatch > max_vega_mismatch:
                refused["vega_mismatch"] += 1
                continue
            gross_notional = 100 * (
                q_short * float(short["atm_strike"]) +
                q_long * float(long["atm_strike"])
            )
            if (max_gross_notional_usd is not None
                    and gross_notional > max_gross_notional_usd):
                refused["gross_notional"] += 1
                continue
            def order(row: dict, structure: str) -> dict:
                return {
                    "date": entry,
                    "symbol": row["symbol"],
                    "expiration": expiry,
                    "strikes": [float(row["atm_strike"])] * 2,
                    "structure": structure,
                    "exit": exit_day,
                }
            pairs.append({
                "id": f"{intent['decision_date']}:{intent['symbol_a']}:{intent['symbol_b']}",
                "decision_date": intent["decision_date"],
                "entry_date": entry,
                "exit_date": exit_day,
                "exit_reason": intent["exit_reason"],
                "entry_z": intent["entry_z"],
                "short_order": order(short, "short_straddle"),
                "long_order": order(long, "long_straddle"),
                "short_contracts": q_short,
                "long_contracts": q_long,
                "initial_vega_mismatch": mismatch,
                "gross_underlying_notional_usd": gross_notional,
                **({"hedge_rate": hedge_rate} if hedge_rate is not None else {}),
            })
        return {"pairs": pairs, "summary": {
            "intents": len(pairs) + sum(refused.values()),
            "accepted": len(pairs),
            "refused": dict(sorted(refused.items())),
        }}
