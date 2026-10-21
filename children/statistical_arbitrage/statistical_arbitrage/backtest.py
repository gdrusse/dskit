"""Research-only volatility-pair trade scheduling and quote replay.

Signal decisions are made after the date's close; orders use the next
observed session. Pricing and reporting are separate from this clock.
"""
from __future__ import annotations

from bisect import bisect_left
from collections import Counter, defaultdict
from datetime import date
from hashlib import sha256
import json
from math import isfinite
import os
from pathlib import Path
import re
import shutil
import tempfile
from typing import Iterable

from dskit.pipeline.libs.pair_option_ledger import PairedOptionLedger
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

    def entry_candidates(self, signals: Iterable[dict], sessions: Iterable[str]) -> list[dict]:
        """Rank a single entry decision per close without reserving future exposure."""
        days = list(sessions)
        if days != sorted(set(days)):
            raise ValueError("sessions must be unique and ascending")
        by_day: dict[str, list[dict]] = defaultdict(list)
        for row in signals:
            if row.get("status") == "ok" and isfinite(float(row.get("z", float("nan")))):
                by_day[row["date"]].append(row)
        candidates = []
        for index, day in enumerate(days[:-1]):
            choices = [row for row in by_day.get(day, [])
                       if abs(float(row["z"])) >= self.entry_z]
            choices.sort(key=lambda row: (-abs(float(row["z"])),
                                          row["symbol_a"], row["symbol_b"]))
            if not choices:
                continue
            chosen = choices[0]
            a, b = chosen["symbol_a"], chosen["symbol_b"]
            positive = float(chosen["z"]) > 0
            candidates.append({
                "symbol_a": a, "symbol_b": b, "decision_date": day,
                "entry_date": days[index + 1], "short_symbol": a if positive else b,
                "long_symbol": b if positive else a,
                "direction": 1 if positive else -1, "entry_z": float(chosen["z"]),
                "exit_date": None, "exit_reason": "open_at_end",
            })
        return candidates

    def intents(self, signals: Iterable[dict], sessions: Iterable[str], *,
                accepted_entries: set[str] | None = None,
                latest_exit: dict[str, str] | None = None,
                corporate_events: Iterable[dict] = ()) -> list[dict]:
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
        candidates = {row["decision_date"]: row
                      for row in self.entry_candidates(signals, days)}
        event_days = {(row.get("date"), row.get("symbol")) for row in corporate_events
                      if row.get("split_coefficient", 1) != 1
                      or row.get("dividend_amount", 0) != 0}
        planned: list[dict] = []
        active: dict | None = None
        pending: dict | None = None
        for index, day in enumerate(days):
            if pending is not None and pending["entry_date"] == day:
                identity = (f"{pending['decision_date']}:{pending['symbol_a']}:"
                            f"{pending['symbol_b']}")
                if accepted_entries is None or identity in accepted_entries:
                    active = dict(pending, entry_index=index)
                pending = None
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
                identity = (f"{active['decision_date']}:{active['symbol_a']}:"
                            f"{active['symbol_b']}")
                if reason is None and any((day, symbol) in event_days for symbol in
                                          (active["short_symbol"], active["long_symbol"])):
                    reason = "corporate_event_observed"
                if (reason is None and latest_exit is not None and index + 1 < len(days)
                        and days[index + 1] >= latest_exit[identity]):
                    reason = "expiry_or_year_boundary"
                if reason and index + 1 < len(days):
                    active["exit_date"] = days[index + 1]
                    active["exit_reason"] = reason
                    planned.append({k: v for k, v in active.items()
                                    if k not in {"direction", "entry_index"}})
                    active = None
                    continue
            if active is None and index + 1 < len(days):
                pending = candidates.get(day)
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

        Surface rows decide contract selection at the entry date. Eligibility
        uses only entry-date facts: shared future expiry, quote quality, rate,
        observed same-day corporate action and initial vega matching. The
        scheduler sets the eventual exit after entry acceptance.
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
            if exit_day is not None and exit_day <= entry:
                refused["no_valid_exit"] += 1
                continue
            if any(
                event.get("symbol") in {intent["short_symbol"], intent["long_symbol"]}
                and event.get("date") == entry
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
            if expiry <= entry:
                refused["expired_at_entry"] += 1
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
            within_vega = False
            for q_short in range(1, max_contracts + 1):
                for q_long in range(1, max_contracts + 1):
                    short_v, long_v = vegas[0] * q_short, vegas[1] * q_long
                    mismatch = abs(short_v - long_v) / max(short_v, long_v)
                    if mismatch > max_vega_mismatch:
                        continue
                    within_vega = True
                    gross = 100 * (
                        q_short * float(short["atm_strike"]) +
                        q_long * float(long["atm_strike"])
                    )
                    if (max_gross_notional_usd is not None
                            and gross > max_gross_notional_usd):
                        continue
                    candidates.append((mismatch, q_short + q_long,
                                       q_short, q_long, gross))
            if not candidates:
                refused["gross_notional" if within_vega else "vega_mismatch"] += 1
                continue
            mismatch, _sum, q_short, q_long, gross_notional = min(candidates)
            def order(row: dict, structure: str) -> dict:
                return {
                    "date": entry,
                    "symbol": row["symbol"],
                    "expiration": expiry,
                    "strikes": [float(row["atm_strike"])] * 2,
                    "structure": structure,
                    "exit": exit_day or expiry,
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


class TechReplayPipeline:
    """Consume a pinned structural run and replay four identical-cost research books.

    The CLI entry point is ``python -m statistical_arbitrage.backtest CONFIG``.
    It refuses changed structural bytes before opening any market data, reads the
    Theta chain once for all books, and creates a new evidence directory only
    after the replay and reports succeed.
    """

    _ROWS = ("surface_rows.jsonl", "graph_fits.jsonl", "graph_edges.jsonl",
             "graph_centrality.jsonl", "corr_graph_fits.jsonl",
             "corr_graph_edges.jsonl", "corr_graph_centrality.jsonl",
             "atm_network_rows.jsonl", "atm_corr_rows.jsonl",
             "atm_baseline_rows.jsonl", "skew_network_rows.jsonl",
             "skew_corr_rows.jsonl", "skew_baseline_rows.jsonl")

    def __init__(self, config_path: str | Path) -> None:
        self.path = Path(config_path).resolve()
        with self.path.open(encoding="utf-8") as stream:
            self.config = json.load(stream)
        if not isinstance(self.config, dict):
            raise ValueError("backtest config must be an object")

    @staticmethod
    def _digest(path: Path) -> str:
        hash_ = sha256()
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                hash_.update(block)
        return hash_.hexdigest()

    def _config_path(self, named: str) -> Path:
        """Resolve child-local paths without relying on the process working directory."""
        path = Path(named)
        if path.is_absolute():
            return path
        if path.parts and path.parts[0] == "configs":
            return self.path.parent.parent / path
        return self.path.parent / path

    def _pinned_bytes(self, declaration: dict) -> tuple[Path, bytes]:
        """Return the exact verified bytes subsequently parsed by this replay."""
        if not isinstance(declaration, dict) or set(declaration) != {"path", "sha256"}:
            raise ValueError("pinned input requires path and sha256")
        expected = declaration["sha256"]
        if not isinstance(expected, str) or re.fullmatch(r"[0-9a-f]{64}", expected) is None:
            raise ValueError("invalid sha256 pin")
        path = self._config_path(declaration["path"])
        raw = path.read_bytes()
        if sha256(raw).hexdigest() != expected:
            raise ValueError(f"sha256 mismatch: {path}")
        return path, raw

    def _pinned_json(self, declaration: dict) -> tuple[Path, dict]:
        """Hash the same bytes that are parsed into the replay contract."""
        path, raw = self._pinned_bytes(declaration)
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError(f"pinned JSON must be an object: {path}")
        return path, data

    @staticmethod
    def _jsonl(path: Path, raw: bytes) -> list[dict]:
        rows = [json.loads(line) for line in raw.decode("utf-8").splitlines()
                if line.strip()]
        if not all(isinstance(row, dict) for row in rows):
            raise ValueError(f"non-object JSONL row in {path}")
        return rows

    def _verified_structural(self) -> tuple[dict, dict[str, list[dict]], dict]:
        cfg = self.config
        _, structural = self._pinned_json(cfg["structural_config"])
        declared = cfg["structural_rows"]
        if not isinstance(declared, dict) or set(declared) != set(self._ROWS):
            raise ValueError("structural_rows must pin all approved outputs")
        rows = {name: self._jsonl(*self._pinned_bytes(declared[name]))
                for name in self._ROWS}
        source_declarations = cfg["source_configs"]
        if set(source_declarations) != {"stock", "theta"}:
            raise ValueError("source_configs must pin stock and theta")
        self._source_documents = {
            name: self._pinned_json(source_declarations[name])
            for name in ("stock", "theta")
        }
        resolved_path, resolved = self._pinned_json(cfg["structural_resolved"])
        fingerprints = resolved.get("data_fingerprint")
        if not isinstance(fingerprints, dict):
            raise ValueError("resolved structural run lacks data_fingerprint")
        self._resolved_fingerprints = fingerprints
        calendar_path, calendar = self._pinned_json(cfg["validation_calendar"])
        pipeline = structural.get("pipeline", {})
        graph, correlation = pipeline.get("graph", {}), pipeline.get("corr_graph", {})
        if (graph.get("uses") !=
                "dskit.pipeline.libs.graphical_lasso:RollingPrecisionGraph"
                or correlation.get("uses") !=
                "dskit.pipeline.libs.graphical_lasso:RollingCorrelationGraph"
                or graph.get("params") != correlation.get("params")):
            raise ValueError("precision and correlation graph contracts disagree")
        self._calendar_record = {"data": calendar, "path": str(calendar_path),
                                 "sha256": cfg["validation_calendar"]["sha256"]}
        return structural, rows, {"structural_config": cfg["structural_config"],
                                  "structural_rows": declared,
                                  "structural_resolved": {"path": str(resolved_path),
                                      "sha256": cfg["structural_resolved"]["sha256"]},
                                  "source_configs": source_declarations,
                                  "validation_calendar": cfg["validation_calendar"],
                                  "backtest_config_sha256": self._digest(self.path)}

    @staticmethod
    def _verified_reader(name: str, reader: object, fingerprints: dict) -> dict:
        """Pin the source plan on the instance that will execute the read."""
        expected = fingerprints.get(name)
        actual = reader.fingerprint()
        if expected is None or actual != expected:
            raise ValueError(f"source fingerprint mismatch for {name}")
        return actual

    @staticmethod
    def _valid_rate(row: dict, field: str) -> bool:
        """Accept only numeric finite FRED observations."""
        try:
            return isfinite(float(row.get(field)))
        except (TypeError, ValueError):
            return False

    def _load_inputs(self, structural: dict) -> tuple[dict, list[dict], Iterable[dict]]:
        """Use the very same manifest-verified DSKit readers as the structural run."""
        from dskit.pipeline.libs.chain_parquet import ParquetChainRows
        from dskit.pipeline.libs.observations import ObservationRows
        from dskit.pipeline.libs.parquet import ParquetRows

        pipeline = structural["pipeline"]
        stock_path, stock = self._source_documents["stock"]
        theta_path, theta = self._source_documents["theta"]
        self._source_provenance = {
            "stock": {"path": str(stock_path), "sha256": self._digest(stock_path)},
            "theta": {"path": str(theta_path), "sha256": self._digest(theta_path)},
        }
        if (theta["rate"]["date_field"] != "observation_date"
                or theta["rate"]["value_field"] != "DGS3MO"
                or theta["rate"]["scale"] != 0.01
                or theta["rate"]["strictly_prior"] is not True
                or theta["rate"]["max_age_days"] != self.config["execution"]["max_rate_age_days"]):
            raise ValueError("rate source and replay rate policy disagree")
        ports = pipeline["returns"]["params"]["ports"]
        if set(ports.values()) != set(stock["symbols"]) | {stock["factor"]["symbol"]}:
            raise ValueError("stock source and structural universe disagree")
        if set(theta["symbols"]) != set(stock["symbols"]):
            raise ValueError("Theta and stock tradable symbols disagree")
        if pipeline["chain"]["params"]["end_before"] != self.config["evaluation"]["end_before"]:
            raise ValueError("chain end and evaluation end disagree")
        self._reader_fingerprints: dict[str, dict] = {}
        prices: dict[str, list[dict]] = {}
        for port, symbol in ports.items():
            node = pipeline[f"prices_{port}"]
            if node["uses"] != "dskit.pipeline.libs.parquet:ParquetRows":
                raise ValueError(f"unexpected price reader for {symbol}")
            params = node["params"]
            expected = stock["factor"] if symbol == stock["factor"]["symbol"] else stock
            if (params["root"] != stock["onboarding_root"]
                    or params["source"] != expected["source"]
                    or params["stream"] != expected["stream"]):
                raise ValueError(f"price source disagrees for {symbol}")
            if symbol == stock["factor"]["symbol"] and (
                params["relpath_by_key"][symbol] != stock["factor"]["relpath"]
            ):
                raise ValueError("factor file disagrees")
            if set(params["columns"].values()) != set(stock["required_columns"]):
                raise ValueError(f"price projection disagrees for {symbol}")
            if params["read_bounds"]["date"]["end_before"] != self.config["evaluation"]["end_before"]:
                raise ValueError(f"price read bound disagrees for {symbol}")
            reader = ParquetRows(f"replay_price_{port}", params)
            self._reader_fingerprints[f"prices_{port}"] = self._verified_reader(
                f"prices_{port}", reader, self._resolved_fingerprints)
            prices[symbol] = reader.run(None, {})["records"]
        rate_node = pipeline["rates_raw"]
        if rate_node["uses"] != "dskit.pipeline.libs.observations:ObservationRows":
            raise ValueError("unexpected rate reader")
        if any(rate_node["params"][field] != theta["rate"][field_name]
               for field, field_name in (("root", "onboarding_root"),
                                         ("source", "source"), ("stream", "stream"))):
            raise ValueError("rate reader source disagrees")
        rate_reader = ObservationRows("replay_rates", rate_node["params"])
        self._reader_fingerprints["rates_raw"] = self._verified_reader(
            "rates_raw", rate_reader, self._resolved_fingerprints)
        rates = rate_reader.run(None, {})["records"]
        rate_field = theta["rate"]["value_field"]
        rates = [row for row in rates if self._valid_rate(row, rate_field)
                 and row.get(theta["rate"]["date_field"], "") < self.config["evaluation"]["end_before"]]
        chain_node = pipeline["chain"]
        if chain_node["uses"] != "dskit.pipeline.libs.chain_parquet:ParquetChainRows":
            raise ValueError("unexpected chain reader")
        chain_params = dict(chain_node["params"])
        if set(chain_params["symbols"]) != set(theta["symbols"]) or (
            chain_params["directories"] != theta["parquet_export"]["directories"]
        ):
            raise ValueError("Theta export and structural chain disagree")
        if (chain_params["start"] < theta["start"]
                or chain_params["end_before"] > theta["end_before"]
                or chain_params["sealed_from"] != theta["parquet_export"]["sealed_from"]):
            raise ValueError("Theta chain calendar disagrees")
        frozen_chain = self._resolved_fingerprints.get("chain", {})
        members = frozen_chain.get("members") if isinstance(frozen_chain, dict) else None
        if not isinstance(members, list) or not members:
            raise ValueError("pinned structural chain lacks exact members")
        if "members" in chain_params and chain_params["members"] != members:
            raise ValueError("structural chain members disagree with pinned resolved plan")
        chain_params["members"] = list(members)
        chain_reader = ParquetChainRows("replay_chain", chain_params)
        self._reader_fingerprints["chain"] = self._verified_reader(
            "chain", chain_reader, self._resolved_fingerprints)
        chain = chain_reader.run(None, {})["records"]
        return prices, rates, chain

    def _calendar(self) -> dict:
        return self._calendar_record

    @staticmethod
    def _closed_in_window(trades: list[dict], first: str, end: str) -> list[dict]:
        """Assign full-trade win and duration to the fold of final close."""
        return [trade for trade in trades if first <= trade["exit_date"] < end]

    @staticmethod
    def _edge_frequency(fits: list[dict], edges: list[dict], symbols: list[str]) -> dict:
        """Count each selected pair once per annual fit, not once per carried date."""
        from itertools import combinations

        annual = {row["fit_date"]: row for row in fits if row.get("refit") is True}
        if len(annual) != sum(row.get("refit") is True for row in fits):
            raise ValueError("duplicate annual graph fit")
        pair_names = {f"{a}:{b}": 0 for a, b in combinations(sorted(symbols), 2)}
        selected = set()
        for row in edges:
            pair = f"{row['symbol_a']}:{row['symbol_b']}"
            fit_date = row["fit_date"]
            if (pair not in pair_names or fit_date not in annual
                    or annual[fit_date].get("status") != "ok"
                    or row["date"] < fit_date):
                raise ValueError("edge disagrees with annual fitted graph")
            selected.add((fit_date, pair))
        for _, pair in selected:
            pair_names[pair] += 1
        total = len(annual)
        return {"annual_fits": total, "pairs": {
            pair: {"selected_fits": count, "frequency": count / total if total else None}
            for pair, count in sorted(pair_names.items())}}

    @staticmethod
    def _block_unclosed(pairs: list[dict], outcome: dict) -> tuple[dict, list[dict]]:
        """Suppress entries while an earlier pair remains open in the same book.

        A failed stock hedge can disqualify a completed trade without extending
        its actual option exposure beyond the two delegated close dates.
        """
        intervals: dict[str, list[tuple[str, str | None]]] = defaultdict(list)
        for exposure in outcome["exposures"]:
            book = exposure["id"].split(":", 1)[0]
            sides = (exposure.get("short_trade", {}), exposure.get("long_trade", {}))
            closes = [side.get("close_date") for side in sides]
            end = max(closes) if all(closes) else None
            intervals[book].append((exposure["open_date"], end))
        blocked = [
            {"id": pair["id"], "status": "blocked_by_unclosed_exposure"}
            for pair in pairs
            if any(
                pair["entry_date"] > start
                and (end is None or pair["entry_date"] <= end)
                for start, end in intervals.get(pair["id"].split(":", 1)[0], ())
            )
        ]
        blocked_ids = {row["id"] for row in blocked}
        filtered = dict(outcome)
        for key in ("trades", "exposures", "marks", "cashflows", "refusals"):
            filtered[key] = [row for row in outcome[key] if row["id"] not in blocked_ids]
        filtered["refusals"].extend(blocked)
        filtered["summary"] = {"raw_ledger": outcome["summary"],
                               "blocked_later_pairs": len(blocked),
                               "pairs_in": len(pairs),
                               "trades_retained": len(filtered["trades"]),
                               "exposures_retained": len(filtered["exposures"])}
        return filtered, blocked

    def _evaluate(self, rows: dict, structural: dict, prices: dict,
                  rates: list[dict], chain: Iterable[dict], provenance: dict) -> dict:
        from statistical_arbitrage.data import TechAdjustedReturns
        from statistical_arbitrage.report import PerformanceReporter

        cfg, evaluation = self.config, self.config["evaluation"]
        if set(cfg["variants"]) != {"atm", "skew"} or any(
            set(books) != {"network", "correlation", "baseline"}
            for books in cfg["variants"].values()
        ):
            raise ValueError("phase-one replay requires ATM/skew precision, correlation and all-pairs books")
        provenance["source_configs"] = getattr(self, "_source_provenance", {})
        provenance["reader_fingerprints"] = getattr(self, "_reader_fingerprints", {})
        returns = TechAdjustedReturns("replay_returns", structural["pipeline"]["returns"]["params"])
        ports = returns.params["ports"]
        aligned = returns.run(None, {port: prices[symbol] for port, symbol in ports.items()})
        benchmark = aligned["benchmark"]
        days = [row["date"] for row in benchmark
                if evaluation["start"] <= row["date"] < evaluation["end_before"]]
        if not days or days != sorted(set(days)):
            raise ValueError("evaluation has no ordered common price calendar")
        panel = {symbol: {} for symbol in ports.values()}
        for row in [*aligned["records"], *benchmark]:
            panel[row["symbol"]][row["date"]] = row["adjusted_close"]
        adjusted = {symbol: [panel[symbol][day] for day in days] for symbol in panel}
        orderer = VolatilityArbitrageBacktester(
            entry_z=cfg["signal"]["entry_z"], stop_z=cfg["signal"]["stop_z"],
            max_hold_sessions=cfg["signal"]["max_hold_sessions"])
        if cfg["signal"]["one_active_pair_per_book"] is not True:
            raise ValueError("only one active pair per book is implemented")
        execution = cfg["execution"]
        chain = chain if isinstance(chain, list) else list(chain)
        candidate_pairs, order_summaries, schedules = [], {}, {}
        keys = {}
        for variant, books in cfg["variants"].items():
            for book, row_name in books.items():
                if row_name not in rows or book not in ("network", "correlation", "baseline"):
                    raise ValueError("variant references an unpinned book")
                key = f"{variant}_{book}"
                keys[key] = row_name
                yearly = {}
                for year in range(evaluation["development_year"],
                                  max(evaluation["validation_years"]) + 1):
                    sessions = [day for day in days if day[:4] == str(year)]
                    if not sessions:
                        continue
                    signals = [row for row in rows[row_name] if row.get("date") in set(sessions)]
                    intents = orderer.entry_candidates(signals, sessions)
                    generated = orderer.pair_orders(
                        intents, rows["surface_rows.jsonl"],
                        max_contracts=execution["max_contracts_per_leg"],
                        max_vega_mismatch=execution["max_initial_vega_mismatch"],
                        max_half_spread_over_premium=execution["max_half_spread_over_premium"],
                        max_gross_notional_usd=execution["max_gross_underlying_notional_usd"],
                        corporate_events=aligned["corporate_events"] if execution["refuse_corporate_events"] else (),
                        rates=rates, require_rate=execution["require_prior_rate"],
                        max_rate_age_days=execution["max_rate_age_days"])
                    yearly[str(year)] = generated["summary"]
                    templates, deadlines = {}, {}
                    for pair in generated["pairs"]:
                        safe = [day for day in sessions if pair["entry_date"] < day
                                < pair["short_order"]["expiration"]]
                        if not safe:
                            yearly[str(year)]["refused"]["no_safe_exit"] = (
                                yearly[str(year)]["refused"].get("no_safe_exit", 0) + 1)
                            yearly[str(year)]["accepted"] -= 1
                            continue
                        deadlines[pair["id"]] = safe[-1]
                        pair["id"] = f"{key}:{pair['id']}"
                        templates[pair["id"].split(":", 1)[1]] = pair
                        candidate_pairs.append(pair)
                    schedules[key, str(year)] = (signals, sessions, templates, deadlines)
                order_summaries[key] = yearly
        ledger = PairedOptionLedger("replay_pairs", cfg["ledger"])
        entry_days = {(pair["entry_date"], order["symbol"])
                      for pair in candidate_pairs
                      for order in (pair["short_order"], pair["long_order"])}
        entry_chain = [row for row in chain
                       if (row.get("date"), row.get("symbol")) in entry_days]
        preflight = ledger.run(None, {"pairs": candidate_pairs, "chain": entry_chain,
                                      "dividends": aligned["corporate_events"]})
        entered = {row["id"] for row in (*preflight["trades"], *preflight["exposures"])}
        entry_refusals = [row for row in preflight["refusals"]
                          if row["id"] not in entered]
        banned: set[str] = set()
        while True:
            all_pairs = []
            for (key, year), (signals, sessions, templates, deadlines) in schedules.items():
                allowed = {stem for stem in templates
                           if f"{key}:{stem}" in entered and f"{key}:{stem}" not in banned}
                intents = orderer.intents(
                    signals, sessions, accepted_entries=allowed,
                    latest_exit=deadlines,
                    corporate_events=(aligned["corporate_events"]
                                      if execution["refuse_corporate_events"] else ()))
                for intent in intents:
                    stem = (f"{intent['decision_date']}:{intent['symbol_a']}:"
                            f"{intent['symbol_b']}")
                    if intent["exit_date"] is None:
                        raise ValueError(f"accepted entry lacks a safe exit: {key}:{stem}")
                    template = templates[stem]
                    pair = dict(template, exit_date=intent["exit_date"],
                                exit_reason=intent["exit_reason"])
                    pair["short_order"] = dict(template["short_order"],
                                               exit=intent["exit_date"])
                    pair["long_order"] = dict(template["long_order"],
                                              exit=intent["exit_date"])
                    if intent["exit_date"] >= pair["short_order"]["expiration"]:
                        raise ValueError(f"scheduled exit after expiry: {pair['id']}")
                    all_pairs.append(pair)
            outcome = ledger.run(None, {"pairs": all_pairs, "chain": chain,
                                        "dividends": aligned["corporate_events"]})
            outcome, blocked = self._block_unclosed(all_pairs, outcome)
            new_bans = {row["id"] for row in blocked} - banned
            if not new_bans:
                break
            banned.update(new_bans)
        for (key, year), (_, _, templates, _) in schedules.items():
            summary = order_summaries[key][year]
            summary["entry_quote_refused"] = sum(
                row["id"] in {pair["id"] for pair in templates.values()}
                for row in entry_refusals)
            summary["entered"] = sum(
                pair["id"] in {item["id"] for item in all_pairs}
                for pair in templates.values())
        reporter = PerformanceReporter(capital_usd=evaluation["initial_capital_usd"],
                                       sessions_per_year=evaluation["sessions_per_year"])
        calendar = self._calendar()
        books_out = {}
        global_session_index = {day: i for i, day in enumerate(days)}
        for key in keys:
            own_marks = [mark for mark in outcome["marks"] if mark["id"].startswith(key + ":")]
            daily_pnl = reporter.daily_pnl_from_marks(own_marks, days)
            own_trades = [dict(trade, entry_date=trade["open_date"],
                               exit_date=trade["close_date"])
                          for trade in outcome["trades"] if trade["id"].startswith(key + ":")]
            books_out[key] = {"daily_pnl_usd": daily_pnl, "trades": own_trades,
                              "order_summary": order_summaries[key],
                              "exposures": [exposure for exposure in outcome["exposures"]
                                            if exposure["id"].startswith(key + ":")],
                              "refusals": [refusal for refusal in outcome["refusals"]
                                           if refusal["id"].startswith(key + ":")]}
        for variant in cfg["variants"]:
            baseline = books_out[f"{variant}_baseline"]
            def fold(model: dict, first: str, end: str) -> dict | None:
                indices = [i for i, day in enumerate(days) if first <= day < end]
                if not indices:
                    return None
                selected = [days[i] for i in indices]
                selected_prices = {symbol: [values[i] for i in indices]
                                   for symbol, values in adjusted.items()}
                prior_prices = ({symbol: values[indices[0] - 1]
                                 for symbol, values in adjusted.items()}
                                if indices[0] > 0 else None)
                return reporter.evaluate(
                    dates=selected, model_pnl=[model["daily_pnl_usd"][i] for i in indices],
                    baseline_pnl=[baseline["daily_pnl_usd"][i] for i in indices],
                    adjusted_prices=selected_prices,
                    prior_adjusted_prices=prior_prices,
                    trade_date_index=global_session_index,
                    model_trades=self._closed_in_window(model["trades"], first, end),
                    baseline_trades=self._closed_in_window(baseline["trades"], first, end))
            for book in ("network", "correlation"):
                model = books_out[f"{variant}_{book}"]
                model["annual"] = {
                    str(year): fold(model, f"{year}-01-01", f"{year + 1}-01-01")
                    for year in range(evaluation["development_year"],
                                      max(evaluation["validation_years"]) + 1)}
                model["subfolds"] = {
                    str(block["block"]): fold(model, block["origin_start"],
                                               block["origin_end_before"])
                    for block in calendar["data"]["blocks"]}
                model["full"] = fold(model, f"{evaluation['development_year']}-01-01",
                                     evaluation["end_before"])
        fits = [row for row in rows["graph_fits.jsonl"] if row.get("refit") is True]
        diagnostics = [{name: fit.get(name) for name in
                        ("date", "fit_date", "trained_through", "status", "alpha",
                         "cv_score", "train_log_likelihood", "n_iter", "n_window",
                         "n_edges", "warning")}
                       for fit in fits]
        symbols = structural["pipeline"]["graph"]["params"]["symbols"]
        edge_frequency = {
            "precision": self._edge_frequency(rows["graph_fits.jsonl"],
                                               rows["graph_edges.jsonl"], symbols),
            "correlation": self._edge_frequency(rows["corr_graph_fits.jsonl"],
                                                 rows["corr_graph_edges.jsonl"], symbols),
        }
        return {"name": cfg["name"], "calendar": calendar,
                "provenance": provenance, "dates": days,
                "training_cv_diagnostics": diagnostics,
                "edge_selection_frequency": edge_frequency,
                "training_epoch_loss": None,
                "training_note": "GraphicalLassoCV is not an epoch-trained neural model.",
                "books": books_out, "ledger_summary": outcome["summary"],
                "blocked_later_pairs": blocked,
                "cashflows": outcome["cashflows"]}

    def _write_output(self, result: dict, rows: dict, structural: dict,
                      provenance: dict) -> Path:
        """Publish plots and JSON together after writing a private sibling directory."""
        from statistical_arbitrage.report import PerformanceReporter

        root = Path(self.config["output_dir"])
        name = self.config["name"]
        if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", name) is None:
            raise ValueError("unsafe replay name")
        stem = f"{name}-{provenance['backtest_config_sha256'][:12]}"
        target = root / stem
        if target.exists():
            raise FileExistsError(f"completed replay already exists: {target}")
        root.mkdir(parents=True, exist_ok=True)
        stage = Path(tempfile.mkdtemp(prefix=f".{stem}.", dir=root))
        try:
            reporter = PerformanceReporter(
                capital_usd=self.config["evaluation"]["initial_capital_usd"],
                sessions_per_year=self.config["evaluation"]["sessions_per_year"],
            )
            result["plots"] = reporter.save_plots(
                stage, result,
                {variant: rows[self.config["variants"][variant]["network"]]
                 for variant in ("atm", "skew")},
                symbols=structural["pipeline"]["graph"]["params"]["symbols"],
                graph_fits=rows["graph_fits.jsonl"],
                graph_edges=rows["graph_edges.jsonl"],
                graph_centrality=rows["graph_centrality.jsonl"],
                entry_z=self.config["signal"]["entry_z"],
            )
            result["output_path"] = str(target)
            with (stage / "evaluation.json").open("x", encoding="utf-8") as stream:
                json.dump(result, stream, indent=2, sort_keys=True, allow_nan=False)
            if target.exists():
                raise FileExistsError(f"completed replay already exists: {target}")
            os.rename(stage, target)
        finally:
            if (stage.exists() and not stage.is_symlink()
                    and stage.resolve().parent == root.resolve()
                    and stage.name.startswith(f".{stem}.")):
                shutil.rmtree(stage)
        return target

    def run(self) -> dict:
        """Verify pinned inputs, replay, then atomically publish a new result."""
        structural, rows, provenance = self._verified_structural()
        prices, rates, chain = self._load_inputs(structural)
        result = self._evaluate(rows, structural, prices, rates, chain, provenance)
        self._write_output(result, rows, structural, provenance)
        return result


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Pinned retrospective tech option replay")
    parser.add_argument("config", type=Path)
    arguments = parser.parse_args()
    completed = TechReplayPipeline(arguments.config).run()
    print(completed["output_path"])
