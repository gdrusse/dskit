"""Pinned fixed-selection MIO execution and expiry stress replay.

This reads the saved 2019 decisions and verified source bars, then delegates
fills, reserve admission, settlements and marks to the existing study owners.
The JSON document declares every sensitivity and a fresh output root.
"""

from __future__ import annotations

import argparse
import copy
import csv
import hashlib
import json
import math
import sqlite3
import subprocess
from collections import Counter
from pathlib import Path

from dskit.onboarding.artifacts import PinnedPayloadSource
from dskit.pipeline.libs.option_ledger import FundedOptionAccount
from dskit.pipeline.option_structures import quote_defect, structure_max_loss
from index_options.ledger_studies import (
    WeeklyOptionPortfolioStudy,
    _AllQuotedOptionContext,
    _SQLiteQuoteView,
)


def adverse_side(bid, ask, sign, fraction=0.25):
    """Move one observed NBBO side adversely by a fraction of its spread."""
    spread = ask - bid
    return ask + fraction * spread if sign > 0 else bid - fraction * spread


def effective_size(size, half):
    """Return original displayed depth or its integer half."""
    return size // 2 if half else size


def _sha(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _load_json(path):
    return json.loads(Path(path).read_text())


def _write_json(path, data):
    Path(path).write_text(json.dumps(data, indent=2, sort_keys=True, allow_nan=False) + "\n")


def _bar_rows(binding, symbols, calendar):
    import pyarrow.parquet as pq

    source = PinnedPayloadSource(binding["pin"])
    wanted = set(calendar)
    bars = {}
    for symbol in symbols:
        path = source.file(binding["files"][symbol], verify=True)
        selected = {}
        for row in pq.read_table(path, columns=["date", "close"]).to_pylist():
            day = row["date"]
            if day in wanted:
                if day in selected or not isinstance(row["close"], (int, float)) or not math.isfinite(row["close"]):
                    raise ValueError("duplicate or invalid bar: " + symbol + ":" + day)
                selected[day] = float(row["close"])
        if set(selected) != wanted:
            raise ValueError("missing account-session bar: " + symbol)
        bars[symbol] = selected
    return bars


def _index_view(path, symbol, expected_sha):
    actual_sha = _sha(path)
    if actual_sha != expected_sha:
        raise ValueError("original quote-index identity drift: " + symbol)
    with sqlite3.connect(path) as conn:
        row = conn.execute("SELECT value FROM metadata WHERE name='descriptor'").fetchone()
    if row is None:
        raise ValueError("missing quote-index descriptor: " + symbol)
    descriptor = {**json.loads(row[0]), "path": str(path), "sha256": actual_sha}
    view = _SQLiteQuoteView(descriptor)
    view.symbol = symbol
    return view, descriptor


def _index_factors(view, calendar):
    factors = {}
    for day in calendar:
        row = next(view.iter_for(day, day), None)  # Usually empty: quotes partition by expiry.
        if row is None:
            found = view.index.connection.execute(
                "SELECT payload FROM rows WHERE date=? LIMIT 1", (day,)).fetchone()
            row = json.loads(found[0]) if found else None
        if row is not None:
            factor = row.get("basis_factor")
            if not isinstance(factor, (int, float)) or not math.isfinite(factor) or factor <= 0:
                raise ValueError("invalid quote basis factor")
            factors[day] = float(factor)
    return factors


def _missing_expiry_factors(binding, symbols, needed, factors):
    import pyarrow.dataset as ds

    source = PinnedPayloadSource(binding["pin"])
    for symbol in symbols:
        missing = sorted(day for day in needed.get(symbol, ()) if day not in factors[symbol])
        if not missing:
            continue
        path = source.file(binding["files"][symbol], verify=True)
        dataset = ds.dataset(path, format="parquet")
        for day in missing:
            table = dataset.to_table(columns=["basis_factor"], filter=ds.field("date") == day)
            values = {float(x) for x in table.column(0).to_pylist() if x is not None}
            if len(values) != 1 or not math.isfinite(next(iter(values))) or next(iter(values)) <= 0:
                raise ValueError("missing or inconsistent expiry basis: " + symbol + ":" + day)
            factors[symbol][day] = values.pop()


def _scenario_fill(study, book, day, method, scenario):
    """Apply changed price/depth, then the existing payoff and reserve owners."""
    if scenario["price_fraction"] == 0 and not scenario["half_size"]:
        return study._fill(book, day, study._charge(method))
    filled = copy.deepcopy(book)
    symbol, expiry, factor = book["symbol"], book["expiry"], book["factor"]
    node = study._nodes[symbol]
    if day >= expiry or not math.isclose(study._factors[symbol].get(day, math.nan),
                                         factor, rel_tol=1e-9):
        return None, "fill_basis_or_session"
    premium = stress_extra = 0.0
    mult = node.params["solver"]["multiplier"]
    for leg in filled["legs"]:
        row = study._quotes[symbol].get((day, expiry, leg["right"], leg["strike"]))
        market = node._usable_quote(row, factor)
        if market is None:
            return None, "missing_fill_quote"
        side = "ask_size" if leg["sign"] > 0 else "bid_size"
        depth = effective_size(row[side], scenario["half_size"])
        if depth < leg["quantity"]:
            return None, "insufficient_fill_size"
        bid, ask = market
        price = adverse_side(bid, ask, leg["sign"], scenario["price_fraction"])
        if not math.isfinite(price) or price <= 0:
            return None, "invalid_stressed_fill_price"
        leg["fill_price"] = price
        leg["effective_capacity"] = depth
        premium -= leg["sign"] * price * mult * leg["quantity"]
        stress_extra += (ask - bid) * mult * leg["quantity"] * node.params["stress_spread_fraction"]
    fees = book["contracts"] * node.params["solver"]["fee_per_contract_usd"]
    credit = premium - fees
    loss = structure_max_loss(
        [(leg["right"], leg["sign"] * leg["quantity"]) for leg in filled["legs"]],
        [leg["strike"] for leg in filled["legs"]], credit, mult)
    filled.update(
        fill_date=day, decision_credit=book["credit"], credit=credit,
        reserve=study._floor(max(0.0, loss, -credit), study._charge(method), filled["legs"], mult),
        max_loss=loss, premium=premium, fees=fees, stress_extra=stress_extra, status="filled")
    return filled, None


def _attempt_evidence(study, book, day, scenario):
    """Record the observed and stressed sides/depths for every attempted leg."""
    symbol, expiry, factor = book["symbol"], book["expiry"], book["factor"]
    node = study._nodes[symbol]
    legs = []
    for leg in book["legs"]:
        row = study._quotes[symbol].get((day, expiry, leg["right"], leg["strike"]))
        market = node._usable_quote(row, factor)
        side = "ask_size" if leg["sign"] > 0 else "bid_size"
        original_depth = row.get(side) if row is not None else None
        effective_depth = (effective_size(original_depth, scenario["half_size"])
                           if type(original_depth) is int else None)
        bid, ask = market if market is not None else (None, None)
        original_price = (ask if leg["sign"] > 0 else bid) if market else None
        proposed_price = (adverse_side(bid, ask, leg["sign"],
                                       scenario["price_fraction"]) if market else None)
        price_valid = (proposed_price is not None
                       and math.isfinite(proposed_price) and proposed_price > 0)
        if proposed_price is not None and not math.isfinite(proposed_price):
            proposed_price = None
        legs.append({"side": "buy" if leg["sign"] > 0 else "sell",
                     "right": leg["right"], "strike": leg["strike"],
                     "quantity": leg["quantity"], "bid": bid, "ask": ask,
                     "original_side_price": original_price,
                     "proposed_fill_price": proposed_price,
                     "proposed_price_valid": price_valid,
                     "original_depth": original_depth,
                     "effective_depth": effective_depth})
    return {"id": book["id"], "symbol": symbol, "expiry": expiry,
            "decision_date": book["decision_date"], "fill_date": day,
            "legs": legs, "outcome": None}


def _scenario_curve(study, account, snapshots):
    """Mark held books at unchanged observed mids while entry credit varies."""
    from dskit.pipeline.records import number_ok

    marks = {}
    multiplier = study.params["bridge"]["solver"]["multiplier"]
    for book in account.closed:
        last_value = None
        for day in study.calendar:
            if not book["fill_date"] <= day < book["expiry"]:
                continue
            mids = []
            for leg in book["legs"]:
                row = study._quotes[book["symbol"]].get(
                    (day, book["expiry"], leg["right"], leg["strike"]))
                if row is None:
                    mids = None
                    break
                factor = book["factor"]
                bid, ask = row.get("bid"), row.get("ask")
                if (not number_ok(bid) or not number_ok(ask)
                        or not math.isclose(row.get("basis_factor", math.nan), factor, rel_tol=1e-9)):
                    mids = None
                    break
                bid, ask = bid / factor, ask / factor
                if quote_defect(bid, ask, None, locked_ok=True):
                    mids = None
                    break
                mids.append(leg["sign"] * leg["quantity"] * (bid + ask) / 2.0)
            carried = mids is None
            if mids is not None:
                last_value = multiplier * sum(mids)
            if last_value is None:
                raise ValueError("no opening mark for filled book: " + book["id"])
            marks[book["id"], day] = (book["credit"] + last_value, carried)
    previous, nav, peak, max_dd = account.initial, 1.0, 1.0, 0.0
    curve = []
    for snap in snapshots:
        held = [marks[key, snap["date"]] for key in snap["open_ids"]]
        equity = snap["funding"] + sum(mark for mark, _carried in held)
        denominator = previous + snap["deposit"]
        if denominator <= 0 or equity <= 0:
            raise ValueError("nonpositive marked equity under scenario on " + snap["date"])
        nav *= equity / denominator
        peak = max(peak, nav)
        drawdown = nav / peak - 1.0
        max_dd = min(max_dd, drawdown)
        curve.append({**snap, "equity": equity, "nav": nav, "drawdown": drawdown,
                      "carried_books": sum(carried for _mark, carried in held)})
        previous = equity
    return curve, nav, max_dd


def _scenario_finish(study, method, account, decisions, rejected, snapshots):
    """Reuse the funded account and produce a mark curve for changed entry prices."""
    if account.open:
        raise ValueError("scenario ended with unsettled books")
    curve, nav, max_dd = _scenario_curve(study, account, snapshots)
    trades = account.closed
    return {
        "method": method, "decisions": decisions, "trades": trades,
        "rejected": rejected, "curve": curve, "deposits": account.deposits,
        "contributions": account.contributions, "pnl": account.realized,
        "stress_pnl": None, "final_equity": account.funding, "twr": nav - 1.0,
        "max_drawdown": max_dd, "peak_open_books": max(len(s["open_ids"]) for s in snapshots),
        "peak_reserve": max(s["reserve"] for s in snapshots),
        "wins": sum(t["pnl"] > 0 for t in trades),
        "losses": sum(t["pnl"] < 0 for t in trades), "unfilled": len(rejected),
        "contracts": sum(t["contracts"] for t in trades),
        "unbounded_books": sum(not math.isfinite(t["max_loss"]) for t in trades),
        "fees": sum(t["fees"] for t in trades),
        "ledger_summary": {"stage": "fixed-selection scenario mark at original mid",
                           "closed_trades": len(trades),
                           "pnl_usd": account.realized,
                           "carried_marks": sum(c["carried_books"] for c in curve)}}


class FrozenMIOReplay:
    """Replay saved selected books through existing account/ledger mechanics."""

    def __init__(self, cohort, document, pins):
        self.cohort = cohort
        self.document = document
        self.pins = pins
        original = Path(document["original_root"]) / cohort
        self.original_dir = original
        result_path = original / "result.json"
        expected = document["cohorts"][cohort]["result_sha256"]
        if _sha(result_path) != expected:
            raise ValueError("original result identity drift: " + cohort)
        self.original = _load_json(result_path)
        self._verify_original_csvs(pins)
        if not self.original["complete"] or len(self.original["results"]) != 4:
            raise ValueError("incomplete original method packet")
        config_path = Path(document["repository_root"]) / "children/index_options/configs" / (
            "mio-2019-" + cohort + ".json")
        if _sha(config_path) != pins["original_configs"][config_path.name]["sha256"]:
            raise ValueError("original run document drift")
        config = _load_json(config_path)
        self.domain = config["pipeline"]["plan"]["params"]["study"]["domain"]
        self.calendar = self.original["audit"]["calendar"]
        self.symbols = self.domain["params"]["symbols"]
        if len(self.calendar) != 252 or max(self.calendar) >= "2020-01-01":
            raise ValueError("protected account calendar")
        self.bars = _bar_rows(self.domain["sources"]["bars"], self.symbols, self.calendar)
        run_dir = Path(pins["runs"][cohort]["run_dir"])
        self.quotes, self.factors, self.index_descriptors = {}, {}, {}
        expected_indexes = document["cohorts"][cohort]["quote_index_sha256"]
        if set(expected_indexes) != set(self.symbols):
            raise ValueError("quote-index pin coverage drift: " + cohort)
        for symbol in self.symbols:
            path = run_dir / "artifacts/mio-quotes" / (symbol + ".sqlite")
            view, descriptor = _index_view(path, symbol, expected_indexes[symbol])
            self.quotes[symbol] = view
            self.factors[symbol] = _index_factors(view, self.calendar)
            self.index_descriptors[symbol] = descriptor
        needed = {}
        for packet in self.original["results"]:
            for decision in packet["result"]["decisions"]:
                for book in decision.get("books", []):
                    needed.setdefault(book["symbol"], set()).add(book["expiry"])
        if any(day not in self.calendar or day >= "2020-01-01"
               for days in needed.values() for day in days):
            raise ValueError("selected expiry outside 2019 account calendar")
        _missing_expiry_factors(self.domain["sources"]["chain"], self.symbols,
                                needed, self.factors)
        self.params = {**self.domain["params"], "selected_pairs": self.domain["pairs"]}

    def _verify_original_csvs(self, pins):
        """Keep the four human-facing control extracts bound to original pins."""
        item = pins["evidence"]["artifact-index.json"]
        path = Path(item["path"])
        if _sha(path) != item["sha256"]:
            raise ValueError("original artifact-index identity drift")
        index = _load_json(path)["csv_files"]
        for name in ("trades.csv", "rejected-orders.csv",
                     "orders-and-fills.csv", "daily-account.csv"):
            relative = self.cohort + "/" + name
            source = self.original_dir / name
            if _sha(source) != index[relative]["sha256"]:
                raise ValueError("original control CSV identity drift: " + relative)

    @staticmethod
    def _csv_equal(cell, value):
        if value is None:
            return cell == ""
        if isinstance(value, (dict, list)):
            return json.loads(cell) == value
        if isinstance(value, bool):
            return cell == str(value)
        if isinstance(value, (int, float)):
            return float(cell) == float(value)
        return cell == str(value)

    def _verify_control_csv_rows(self, result):
        """Match recomputed books and account rows to four pinned extracts."""
        method = result["method"]["id"]

        def rows(name):
            with (self.original_dir / name).open(newline="") as stream:
                return [row for row in csv.DictReader(stream) if row["method"] == method]

        for name, objects, key in (
                ("trades.csv", result["trades"], "id"),
                ("rejected-orders.csv", result["rejected"], "id"),
                ("daily-account.csv", result["curve"], "date")):
            expected = {item[key]: item for item in objects}
            observed = rows(name)
            if len(observed) != len(expected) or {row[key] for row in observed} != set(expected):
                raise ValueError("control CSV row identity mismatch: " + name + "/" + method)
            for row in observed:
                item = expected[row[key]]
                for field, cell in row.items():
                    if field in ("cohort", "method"):
                        continue
                    if not self._csv_equal(cell, item.get(field)):
                        raise ValueError("control CSV value mismatch: " +
                                         name + "/" + method + "/" + row[key] + "/" + field)

        observed = rows("orders-and-fills.csv")
        keys = {}
        for row in observed:
            key = (row["date"], row["group_id"], row["right"],
                   float(row["strike"]), row["side"])
            if key in keys:
                raise ValueError("duplicate original order leg")
            keys[key] = row
        expected_count = 0
        trades = {book["id"]: book for book in result["trades"]}
        rejected = {book["id"]: book for book in result["rejected"]}
        for decision in result["decisions"]:
            for book in decision.get("books", []):
                trade = trades.get(book["id"])
                refusal = rejected.get(book["id"])
                for leg in book["legs"]:
                    expected_count += 1
                    side = "buy" if leg["sign"] > 0 else "sell"
                    key = (decision["date"], book["id"], leg["right"],
                           float(leg["strike"]), side)
                    row = keys.get(key)
                    if row is None:
                        raise ValueError("control order leg missing from CSV")
                    actual_leg = (next((item for item in trade["legs"]
                                        if item["right"] == leg["right"]
                                        and item["strike"] == leg["strike"]
                                        and item["sign"] == leg["sign"]), None)
                                  if trade is not None else None)
                    fields = {
                        "selected_by_optimizer": 1,
                        "selected_quantity": leg["quantity"],
                        "filled_quantity": actual_leg["quantity"] if actual_leg else 0,
                        "fill_price": actual_leg["fill_price"] if actual_leg else None,
                        "fill_date": trade["fill_date"] if trade is not None else None,
                        "rejection_reason": refusal["reason"] if refusal else None}
                    if any(not self._csv_equal(row[field], value)
                           for field, value in fields.items()):
                        raise ValueError("control order/fill CSV mismatch: " + book["id"])
        if len(observed) != expected_count:
            raise ValueError("control order/fill CSV leg count mismatch: " + method)

    def close(self):
        """Verify and close every original read-only quote index."""
        for view in self.quotes.values():
            view.close(verify=True)

    def study(self, scenario):
        """Build only the existing study fields needed for frozen replay."""
        study = WeeklyOptionPortfolioStudy.__new__(WeeklyOptionPortfolioStudy)
        study.params = self.params
        study.calendar = self.calendar
        study._quotes = self.quotes
        study._factors = self.factors
        study._bars = copy.deepcopy(self.bars)
        study._nodes = {symbol: _AllQuotedOptionContext(
            "prepare", {**self.params["bridge"], "symbol": symbol})
            for symbol in self.symbols}
        multiplier = scenario["expiry_multiplier"]
        if multiplier != 1:
            expiries = {(book["symbol"], book["expiry"])
                        for packet in self.original["results"]
                        for decision in packet["result"]["decisions"]
                        for book in decision.get("books", [])}
            for symbol, day in expiries:
                study._bars[symbol][day] *= multiplier
        return study

    def run_method(self, packet, scenario):
        """Carry one method's frozen orders through a separate scenario account."""
        original = packet["result"]
        method = original["method"]
        decisions = original["decisions"]
        by_day = {item["date"]: item for item in decisions}
        account = FundedOptionAccount(
            self.params["initial"], self.params["deposit"], self.params["deposit_every_days"],
            self.calendar[0], deposit_end_before=self.params["bridge"]["end_before"])
        study = self.study(scenario)
        pending = None
        rejected, snapshots, daily = [], [], []
        self.last_progress = {"daily": daily, "rejected": rejected,
                              "account": account, "date": None,
                              "attempted_ids": [], "attempts": []}
        for day in self.calendar:
            attempts, fills, refusals = [], [], []
            attempt_evidence = []
            self.last_progress.update(date=day, attempted_ids=attempts,
                                      attempts=attempt_evidence)
            before_cash = account.cash
            before_closed = len(account.closed)
            deposit = account.advance(day, study._settlements(account, day))
            expiries = account.closed[before_closed:]
            if pending is not None:
                survivors = []
                for book in pending["books"]:
                    attempts.append(book["id"])
                    evidence = _attempt_evidence(study, book, day, scenario)
                    attempt_evidence.append(evidence)
                    filled, reason = _scenario_fill(study, book, day, method, scenario)
                    if filled is None:
                        refusal = {**book, "status": "unfilled", "fill_date": day, "reason": reason}
                        rejected.append(refusal)
                        evidence["outcome"] = reason
                        refusals.append({"id": book["id"], "reason": reason})
                    else:
                        survivors.append(filled)
                        evidence["outcome"] = "preflight_pass"
                # The pinned eight methods do not declare the opt-in filled-account gate.
                if method.get("shock", {}).get("scope") == study._FILLED_ACCOUNT:
                    raise ValueError("unexpected filled-account gate in frozen method")
                limits = study._limits(account, method, pending["components"])
                if survivors and not account.admit(day, survivors, limits):
                    for book in survivors:
                        rejected.append({**book, "status": "unfilled", "reason": "repriced_shared_cap"})
                        refusals.append({"id": book["id"], "reason": "repriced_shared_cap"})
                        next(item for item in attempt_evidence if item["id"] == book["id"])[
                            "outcome"] = "repriced_shared_cap"
                else:
                    fills = survivors
                    for book in fills:
                        next(item for item in attempt_evidence if item["id"] == book["id"])[
                            "outcome"] = "filled"
                pending = None
            decision = by_day.get(day)
            if decision is not None and decision.get("books"):
                pending = {"books": decision["books"], "components": decision["components"]}
            snapshot = {
                "date": day, "deposit": deposit, "contributions": account.contributions,
                "realized": account.realized, "funding": account.funding, "cash": account.cash,
                "open_ids": sorted(account.open),
                "reserve": sum(book["reserve"] for book in account.open.values())}
            snapshots.append(snapshot)
            daily.append({
                "date": day,
                "candidate_books": decision["candidate_books"] if decision else 0,
                "possible_option_contracts": decision["candidate_contracts"] if decision else 0,
                "decision_status": decision["status"] if decision else "",
                "selected_ids": [book["id"] for book in decision.get("books", [])] if decision else [],
                "candidate_refusals": decision["refusals"] if decision else [],
                "attempted_ids": attempts,
                "attempts": attempt_evidence,
                "fills": [{"id": book["id"], "symbol": book["symbol"], "expiry": book["expiry"],
                           "legs": [{"side": "buy" if leg["sign"] > 0 else "sell",
                                     "right": leg["right"], "strike": leg["strike"],
                                     "quantity": leg["quantity"], "price": leg["fill_price"]}
                                    for leg in book["legs"]],
                           "fees": book["fees"], "credit": book["credit"]} for book in fills],
                "refusals": refusals,
                "expiries": [{"id": book["id"], "symbol": book["symbol"], "pnl": book["pnl"],
                              "original_underlying":
                                  self.bars[book["symbol"]][day] / book["factor"],
                              "stressed_underlying":
                                  study._bars[book["symbol"]][day] / book["factor"]}
                             for book in expiries],
                "expiry_profit": sum(book["pnl"] for book in expiries),
                "expiry_losses": sum(min(0.0, book["pnl"]) for book in expiries),
                "cash_change": account.cash - before_cash,
                "cash": account.cash, "funding": account.funding,
                "contributions": account.contributions, "open_ids": sorted(account.open),
                "reserve": snapshot["reserve"]})
        if pending is not None:
            raise ValueError("unprocessed final selection")
        if scenario["id"] == "control":
            result = study._finish(method, account, decisions, rejected, snapshots)
            scenario_curve, _nav, _dd = _scenario_curve(study, account, snapshots)
            for expected, observed in zip(result["curve"], scenario_curve, strict=True):
                if (abs(expected["equity"] - observed["equity"]) > 1e-6
                        or expected["carried_books"] != observed["carried_books"]):
                    raise ValueError("scenario mark diverges from control")
        else:
            result = _scenario_finish(study, method, account, decisions, rejected, snapshots)
        if scenario["id"] == "control":
            for key, value in original.items():
                if result.get(key) != value:
                    raise ValueError("zero-shock control mismatch: " + self.cohort +
                                     "/" + method["id"] + "/" + key)
            self._verify_control_csv_rows(result)
        previous_equity = account.initial
        for row, point in zip(daily, result["curve"], strict=True):
            row.update(equity=point["equity"], drawdown=point["drawdown"],
                       nav=point["nav"], carried_books=point["carried_books"],
                       marked_day_change=point["equity"] - previous_equity
                       - point["deposit"])
            previous_equity = point["equity"]
        return result, daily


def _money(value):
    return f"${value:,.2f}"


def _daybook(cohort, scenario, method, metrics, daily, incomplete=None):
    """Readable daily opportunities, fills, expiries and losses."""
    lines = [
        f"# {cohort} / {method} / {scenario}", "",
        "2019 development diagnostic. Chosen orders are decided after the named",
        "close; fills appear on the next session. Expiry P&L is realized;",
        "marked daily change is interim equity change after deposits.", "",
        (f"Selected {metrics['selected']}; filled {metrics['filled']}; "
         f"refused {metrics['refused']}; final profit "
         f"{_money(metrics['final_profit_usd'])}; "
         f"max drawdown {metrics['max_drawdown']:.1%}."
         if metrics["status"] == "complete" else
         f"UNSUPPORTED after {len(daily)} completed sessions: "
         f"{metrics['unsupported_reason']}. No final profit is reported."), "",
        "Fill refusals by reason: " + (", ".join(
            f"{key} {value}" for key, value in sorted(metrics["refusal_reasons"].items()))
            or "none"), "",
        "| Date | Possible books / contracts | Chosen | Filled | "
        "Expired (realized P&L) | Fill refusals | Realized loss | "
        "Marked day change | Cash | Equity |",
        "|---|---:|---|---|---|---|---:|---:|---:|---:|",
    ]
    for row in daily:
        chosen = ", ".join(row["selected_ids"]) or "-"
        filled = "; ".join(
            item["id"] + ": " + ", ".join(
                f"{leg['side']} {leg['quantity']} {leg['right']} "
                f"{leg['strike']:g} at {chr(36)}{leg['price']:.4f}"
                for leg in item["legs"])
            for item in row["fills"]) or "-"
        expired = ", ".join(item["id"] + " (" + _money(item["pnl"]) + ")"
                            for item in row["expiries"]) or "-"
        refused = ", ".join(item["id"] + ": " + item["reason"]
                            for item in row["refusals"]) or "-"
        lines.append("| " + " | ".join([
            row["date"], str(row["candidate_books"]) + " / " +
            str(row["possible_option_contracts"]), chosen, filled, expired,
            refused, _money(row["expiry_losses"]),
            (_money(row["marked_day_change"])
             if "marked_day_change" in row else "-"),
            _money(row["cash"]),
            (_money(row["equity"]) if "equity" in row else "-")]) + " |")
    if incomplete is not None:
        failure = incomplete["failure"]
        lines.extend(["", "## Unsupported state", "",
                      f"Failed on {failure['date']}: {failure['reason']}.",
                      f"Cash {_money(failure['cash'])}; funding "
                      f"{_money(failure['funding'])}; reserve "
                      f"{_money(failure['reserve'])}; open books "
                      f"{', '.join(failure['open_ids']) or 'none'}."])
    lines.extend(["", "## Executed legs", ""])
    for row in daily:
        for book in row["fills"]:
            legs = "; ".join(f"{leg['side']} {leg['quantity']} {leg['right']} "
                             f"{leg['strike']:g} at ${leg['price']:.4f}"
                             for leg in book["legs"])
            lines.append(f"- {row['date']} {book['id']}: {legs}; "
                         f"fee {_money(book['fees'])}, entry credit "
                         f"{_money(book['credit'])}.")
    lines.extend(["", "## Candidate preparation refusals", ""])
    for row in daily:
        for item in row["candidate_refusals"]:
            lines.append(f"- {row['date']}: {item.get('symbol', '?')} "
                         f"{item.get('expiry', '')} - "
                         f"{item.get('reason', 'unspecified')}.")
    return '\n'.join(lines) + '\n'


def _metrics(result, daily, selected):
    trades = result["trades"]
    profits = [book["pnl"] for book in trades]
    losses = sorted(x for x in profits if x < 0)
    n_tail = max(1, math.ceil(len(profits) * 0.05)) if profits else 0
    return {
        "status": "complete", "unsupported_reason": None,
        "selected": selected, "filled": len(trades), "refused": len(result["rejected"]),
        "fill_rate": len(trades) / selected if selected else None,
        "final_profit_usd": result["pnl"], "final_equity_usd": result["final_equity"],
        "contributions_usd": result["contributions"], "twr": result["twr"],
        "max_drawdown": result["max_drawdown"], "fees_usd": result["fees"],
        "gross_gains_usd": sum(max(0.0, x) for x in profits),
        "gross_losses_usd": sum(min(0.0, x) for x in profits),
        "profit_factor": (sum(max(0.0, x) for x in profits) / -sum(losses)
                          if losses else None),
        "win_rate": sum(x > 0 for x in profits) / len(profits) if profits else None,
        "worst_trade_usd": min(profits) if profits else None,
        "trade_cvar5_usd": sum(sorted(profits)[:n_tail]) / n_tail if n_tail else None,
        "worst_expiry_day_usd": min(row["expiry_profit"] for row in daily),
        "minimum_cash_usd": min(row["cash"] for row in daily),
        "peak_reserve_usd": result["peak_reserve"],
        "refusal_reasons": dict(Counter(book["reason"] for book in result["rejected"])),
    }


def _dispositions(cohort, scenario, method, result, daily):
    """One auditable outcome for each frozen selected book."""
    if result is None:
        return []
    attempts = {item["id"]: (row, item) for row in daily
                for item in row["attempts"]}
    expiries = {item["id"]: (row, item) for row in daily
                for item in row["expiries"]}
    trades = {book["id"]: book for book in result["trades"]}
    rejected = {book["id"]: book for book in result["rejected"]}
    rows = []
    for decision in result["decisions"]:
        for selected in decision.get("books", []):
            key = selected["id"]
            if key not in attempts:
                raise ValueError("selected book lacks fill attempt: " + key)
            fill_day, attempt = attempts[key]
            trade = trades.get(key)
            refusal = rejected.get(key)
            if (trade is None) == (refusal is None):
                raise ValueError("selected book lacks unique disposition: " + key)
            expiry_day, expiry = expiries[key] if trade is not None else (None, None)
            rows.append({
                "cohort": cohort, "scenario": scenario, "method": method,
                "id": key, "symbol": selected["symbol"],
                "decision_date": decision["date"], "fill_date": fill_day["date"],
                "expiry": selected["expiry"],
                "possible_books": decision["candidate_books"],
                "possible_option_contracts": decision["candidate_contracts"],
                "status": "filled" if trade is not None else "refused",
                "reason": refusal["reason"] if refusal is not None else None,
                "selected_credit": selected["credit"],
                "selected_reserve": selected["reserve"],
                "attempt_legs": attempt["legs"],
                "fill_legs": ([{"side": "buy" if leg["sign"] > 0 else "sell",
                               "right": leg["right"], "strike": leg["strike"],
                               "quantity": leg["quantity"],
                               "price": leg["fill_price"]}
                              for leg in trade["legs"]] if trade is not None else None),
                "fill_credit": trade["credit"] if trade is not None else None,
                "fill_fees": trade["fees"] if trade is not None else None,
                "fill_reserve": trade["reserve"] if trade is not None else None,
                "cash_delta_at_fill": trade["credit"] if trade is not None else 0.0,
                "cash_after_fill_day": fill_day["cash"],
                "expiry_date": expiry_day["date"] if expiry_day is not None else None,
                "original_expiry_underlying":
                    expiry["original_underlying"] if expiry is not None else None,
                "stressed_expiry_underlying":
                    expiry["stressed_underlying"] if expiry is not None else None,
                "expiry_payoff": (trade["pnl"] - trade["credit"]
                                  if trade is not None else None),
                "realized_pnl": trade["pnl"] if trade is not None else None,
                "cash_delta_at_expiry":
                    (trade["pnl"] - trade["credit"] if trade is not None else None),
                "cash_after_expiry_day":
                    expiry_day["cash"] if expiry_day is not None else None,
            })
    return rows


def _unsupported_metrics(selected, reason):
    """Keep an incomplete stress visible without inventing a profit."""
    keys = ("filled", "refused", "fill_rate", "final_profit_usd",
            "final_equity_usd", "contributions_usd", "twr", "max_drawdown",
            "fees_usd", "gross_gains_usd", "gross_losses_usd",
            "profit_factor", "win_rate", "worst_trade_usd", "trade_cvar5_usd",
            "worst_expiry_day_usd", "minimum_cash_usd", "peak_reserve_usd")
    return {"status": "unsupported", "unsupported_reason": reason,
            "selected": selected, **{key: None for key in keys},
            "refusal_reasons": {}}


def execute(document_path):
    """Validate one JSON contract, replay all methods, then pin fresh outputs."""
    doc_path = Path(document_path).resolve()
    document = _load_json(doc_path)
    required = {"schema", "repository_root", "original_root", "results_pins",
                "results_pins_sha256", "cohorts", "scenarios", "output_root"}
    if set(document) != required or document["schema"] != "mio-execution-stress/v1":
        raise ValueError("invalid execution-stress document")
    if _sha(document["results_pins"]) != document["results_pins_sha256"]:
        raise ValueError("results pins identity drift")
    pins = _load_json(document["results_pins"])
    scenarios = document["scenarios"]
    if [s["id"] for s in scenarios] != [
            "control", "adverse_price", "half_size", "expiry_down_15",
            "expiry_up_15", "joint_down_15", "joint_up_15"]:
        raise ValueError("scenario identity/order differs from approved contract")
    expected = [
        ("control", 0, False, 1), ("adverse_price", 0.25, False, 1),
        ("half_size", 0, True, 1), ("expiry_down_15", 0, False, 0.85),
        ("expiry_up_15", 0, False, 1.15),
        ("joint_down_15", 0.25, True, 0.85),
        ("joint_up_15", 0.25, True, 1.15)]
    for item, (name, price, half, expiry) in zip(scenarios, expected, strict=True):
        if (set(item) != {"id", "price_fraction", "half_size", "expiry_multiplier"}
                or item != {"id": name, "price_fraction": price,
                            "half_size": half, "expiry_multiplier": expiry}):
            raise ValueError("scenario values differ from approved contract")
    if set(document["cohorts"]) != {"limited36", "broad212"}:
        raise ValueError("missing exact cohort")
    output = Path(document["output_root"])
    if output.exists():
        raise FileExistsError("fresh output root required: " + str(output))
    completed = []
    incomplete = {}
    source_indexes = {}
    replays = {}
    try:
        for cohort in ("limited36", "broad212"):
            replay = FrozenMIOReplay(cohort, document, pins)
            replays[cohort] = replay
            source_indexes[cohort] = {
                symbol: {"sha256": descriptor["sha256"], "rows": descriptor["rows"]}
                for symbol, descriptor in replay.index_descriptors.items()}
        # Gate 0: all eight controls pass before any stressed case runs.
        for cohort in ("limited36", "broad212"):
            replay = replays[cohort]
            for packet in replay.original["results"]:
                result, daily = replay.run_method(packet, scenarios[0])
                selected = sum(len(d.get("books", []))
                               for d in packet["result"]["decisions"])
                metrics = _metrics(result, daily, selected)
                completed.append((cohort, "control", packet["id"], result, daily, metrics))
                print(cohort, "control", packet["id"], "gate passed", flush=True)
        for scenario in scenarios[1:]:
            for cohort in ("limited36", "broad212"):
                replay = replays[cohort]
                for packet in replay.original["results"]:
                    selected = sum(len(d.get("books", []))
                                   for d in packet["result"]["decisions"])
                    try:
                        result, daily = replay.run_method(packet, scenario)
                        metrics = _metrics(result, daily, selected)
                        print(cohort, scenario["id"], packet["id"],
                              "filled", metrics["filled"], "profit",
                              round(metrics["final_profit_usd"], 2), flush=True)
                    except ValueError as exc:
                        progress = replay.last_progress
                        account = progress["account"]
                        result, daily = None, list(progress["daily"])
                        failure = {"date": progress["date"], "reason": str(exc),
                                   "cash": account.cash, "funding": account.funding,
                                   "contributions": account.contributions,
                                   "realized": account.realized,
                                   "reserve": sum(book["reserve"]
                                                  for book in account.open.values()),
                                   "open_ids": sorted(account.open),
                                   "attempted_ids": list(progress["attempted_ids"]),
                                   "attempts": list(progress["attempts"])}
                        incomplete[cohort, scenario["id"], packet["id"]] = {
                            "failure": failure,
                            "closed_trades": list(account.closed),
                            "rejected": list(progress["rejected"]),
                            "open_books": list(account.open.values())}
                        metrics = _unsupported_metrics(selected, str(exc))
                        print(cohort, scenario["id"], packet["id"],
                              "unsupported:", exc, flush=True)
                    completed.append((cohort, scenario["id"], packet["id"],
                                      result, daily, metrics))
    finally:
        for replay in replays.values():
            replay.close()
    output.mkdir(parents=True)
    summary, all_daily, all_attempts, all_dispositions = [], [], [], []
    for cohort, scenario, method, result, daily, metrics in completed:
        folder = output / cohort / scenario
        folder.mkdir(parents=True, exist_ok=True)
        dispositions = _dispositions(cohort, scenario, method, result, daily)
        all_dispositions.extend(dispositions)
        _write_json(folder / (method + ".json"), {
            "cohort": cohort, "scenario": scenario, "method": method,
            "metrics": metrics, "result": result, "daily": daily,
            "dispositions": dispositions,
            "incomplete": incomplete.get((cohort, scenario, method))})
        (folder / (method + ".md")).write_text(
            _daybook(cohort, scenario, method, metrics, daily,
                     incomplete.get((cohort, scenario, method))))
        summary.append({"cohort": cohort, "scenario": scenario, "method": method, **metrics})
        for row in daily:
            all_daily.append({"cohort": cohort, "scenario": scenario, "method": method, **row})
            for attempt in row["attempts"]:
                all_attempts.append({"cohort": cohort, "scenario": scenario,
                                     "method": method, "date": row["date"], **attempt})
    _write_json(output / "summary.json", summary)
    _write_json(output / "daily.json", all_daily)
    _write_json(output / "attempts.json", all_attempts)
    _write_json(output / "dispositions.json", all_dispositions)
    with (output / "summary.csv").open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(summary[0]))
        writer.writeheader()
        for row in summary:
            writer.writerow({k: json.dumps(v) if isinstance(v, dict) else v for k, v in row.items()})
    fields = list(all_daily[0])
    with (output / "daily.csv").open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for row in all_daily:
            writer.writerow({k: json.dumps(v, separators=(",", ":")) if isinstance(v, (dict, list))
                             else v for k, v in row.items()})
    with (output / "attempts.csv").open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(all_attempts[0]))
        writer.writeheader()
        for row in all_attempts:
            writer.writerow({k: json.dumps(v, separators=(",", ":"))
                             if isinstance(v, (dict, list)) else v for k, v in row.items()})
    with (output / "dispositions.csv").open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(all_dispositions[0]))
        writer.writeheader()
        for row in all_dispositions:
            writer.writerow({k: json.dumps(v, separators=(",", ":"))
                             if isinstance(v, (dict, list)) else v for k, v in row.items()})
    lines = ["# 2019 MIO fixed-selection execution and expiry stress", "",
             "Each daily row names decision-day possibilities and selected orders; fills are",
             "attempted at the next session's observed NBBO. Expiries show realized P&L.",
             "The seven scenarios are conditional on frozen future selections.",
             "See dispositions.csv for each selected book, with quotes, fills,",
             "reserves, expiry payoff and cash impact; daily.csv gives every session.", "",
             "| Cohort | Scenario | Method | Status | Selected | Filled | Refused | Final profit | Max drawdown |",
             "|---|---|---|---|---:|---:|---:|---:|---:|"]
    for row in summary:
        profit = (f"{chr(36)}{row['final_profit_usd']:,.2f}"
                  if row["final_profit_usd"] is not None else "-")
        drawdown = (f"{row['max_drawdown']:.1%}"
                    if row["max_drawdown"] is not None else "-")
        lines.append("| " + " | ".join([
            row["cohort"], row["scenario"], row["method"], row["status"],
            str(row["selected"]), str(row["filled"]), str(row["refused"]),
            profit, drawdown]) + " |")
    lines += ["", "Open a daybook for all 252 daily opportunities, selections,",
              "fills, expiries and losses. The matching JSON and daily.csv",
              "retain complete machine-readable rows.", "", "## Daybooks", ""]
    for row in summary:
        relative = row["cohort"] + "/" + row["scenario"] + "/" + row["method"] + ".md"
        lines.append("- [" + row["cohort"] + " / " + row["method"] + " / " +
                     row["scenario"] + "](" + relative + ")")
    lines.append("")
    (output / "report.md").write_text("\n".join(lines))
    repository_root = document["repository_root"]
    replay_commit = subprocess.check_output(
        ["git", "-C", repository_root, "rev-parse", "HEAD"], text=True).strip()
    if subprocess.check_output(["git", "-C", repository_root, "status", "--porcelain"],
                               text=True).strip():
        raise ValueError("replay source worktree must be clean for pinned publication")
    manifest = {"schema": "mio-execution-stress-pins/v1",
                "document_sha256": _sha(doc_path),
                "replay_commit": replay_commit,
                "original_results_pins_sha256": document["results_pins_sha256"],
                "original_artifact_index_sha256":
                    pins["evidence"]["artifact-index.json"]["sha256"],
                "original_source_manifests": pins["sources"],
                "original_runs": {
                    cohort: {"result_sha256": document["cohorts"][cohort]["result_sha256"],
                             "run_hash": pins["runs"][cohort]["run_hash"],
                             "receipt_sha256": pins["runs"][cohort]["sha256"],
                             "document_hash": pins["runs"][cohort]["document_hash"]}
                    for cohort in ("limited36", "broad212")},
                "source_quote_indexes": source_indexes,
                "replay_code_sha256": _sha(Path(__file__)),
                "files": {str(path.relative_to(output)): {"sha256": _sha(path), "bytes": path.stat().st_size}
                          for path in sorted(output.rglob("*")) if path.is_file()}}
    _write_json(output / "pins.json", manifest)
    return output


def main(argv=None):
    """Run the pinned replay document supplied on the command line."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("document")
    args = parser.parse_args(argv)
    print(execute(args.document))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
