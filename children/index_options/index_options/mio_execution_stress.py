"""Pinned fixed-selection MIO execution and expiry stress replay.

This reads the saved decisions and verified source bars, then delegates
fills, reserve admission, settlements and marks to the existing study owners.
The JSON document declares every sensitivity and a fresh output root. v1-v4
replay the 2019 cohorts; v5 (ADR-0280 amendment) replays any range the
document pins, below the Path A0003 holdout. v7 (ADR-0316 Decision A) adds the eight severity
cases (full-spread price, quarter depth, volatility-sized and market-beta expiry shocks, and their
joint forms), three per-case statistics, a control reverse stress and a pre-registered verdict,
all from the same saved selections: nothing is solved and no new account is opened.
"""

from __future__ import annotations

import argparse
import copy
import csv
import hashlib
import json
import math
import re
import sqlite3
import subprocess
from abc import ABC, abstractmethod
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

from dskit.evaluation.criteria import Criterion, Scorecard
from dskit.onboarding.artifacts import PinnedPayloadSource
from dskit.pipeline.libs.option_ledger import FundedOptionAccount
from dskit.pipeline.libs.parquet import DateBoundedParquet
from dskit.pipeline.option_exposure import NameShare, ReverseStress
from dskit.pipeline.option_structures import quote_defect, structure_max_loss
from dskit.pipeline.records import number_ok
from dskit.pipeline.method_blocks import DEVELOPMENT_SEAL
from index_options.ledger_studies import (
    ReplayHedge,
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


def scaled_size(size, divisor):
    """Return displayed depth floor-divided by ``divisor``; divisor 1 leaves it as shown."""
    return size if divisor == 1 else size // divisor


def _size_divisor(scenario):
    """Return the depth divisor of a case: v7 declares it, v1-v6 declare ``half_size``."""
    if "size_divisor" in scenario:
        return scenario["size_divisor"]
    return 2 if scenario["half_size"] else 1


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
    source = PinnedPayloadSource(binding["pin"])
    wanted = set(calendar)
    bounds = {"date": {"start": min(wanted),
                       "end_before": (date.fromisoformat(max(wanted)) + timedelta(days=1)).isoformat()}}
    bars = {}
    for symbol in symbols:
        path = source.file(binding["files"][symbol], verify=True)
        selected = {}
        for batch in DateBoundedParquet(path, ["date", "close"], bounds).batches():
            for row in batch.to_pylist():
                day = row["date"].isoformat() if isinstance(row["date"], date) else row["date"]
                if day in wanted:
                    if day in selected or not isinstance(row["close"], (int, float)) or not math.isfinite(row["close"]):
                        raise ValueError("duplicate or invalid bar: " + symbol + ":" + day)
                    selected[day] = float(row["close"])
        if set(selected) != wanted:
            raise ValueError("missing account-session bar: " + symbol)
        bars[symbol] = selected
    return bars


def _verify_packet_receipt(config, run_pin, expected):
    """Bind the exported result digest to the pinned run's own close receipt."""
    from dskit.pipeline.document import PipelineDocument
    from dskit.pipeline.driver import RunAttestation

    document_hash = PipelineDocument.from_obj(config).hash
    close_nodes = [key for key, node in config["pipeline"].items()
                   if node.get("uses") == "dskit.pipeline.study_protocol:StudyPacketClose"]
    if document_hash != run_pin["document_hash"] or len(close_nodes) != 1:
        raise ValueError("original close receipt document identity differs")
    manifest = RunAttestation(run_pin["run_dir"]).attested_output(close_nodes[0], "receipt", document_hash)
    if not manifest or not expected == run_pin["sha256"] == manifest.get("sha256"):
        raise ValueError("original close receipt digest differs from the replay export")


def _packet_sessions(binding, history_start, tail):
    """Read and verify the original packet's bounded session spine."""
    source = PinnedPayloadSource(binding["pin"])
    relative = binding["files"]["shared"]
    path = source.file(relative, verify=True)
    bounds = {"date": {"start": history_start, "end_before": tail}}
    days = [day.isoformat() if isinstance(day, date) else day
            for batch in DateBoundedParquet(path, ["date"], bounds).batches()
            for day in batch.column(0).to_pylist()]
    if not days or days != sorted(set(days)):
        raise ValueError("session source must be unique and ordered")
    source.file(relative, verify=True)
    return days


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


_AVAILABLE_SIZE_SCHEMA = "mio-execution-stress/v4"
_RANGE_SCHEMA = "mio-execution-stress/v5"
# v6 (ADR-0312 Amendment 1 S3) is v5 plus a ``hedge`` document key: the no-solve static_forward_hedge scenarios.
_HEDGE_SCHEMA = "mio-execution-stress/v6"
# v7 (ADR-0316 Decision A) is v5 plus ``stress`` and ``criteria`` and no ``hedge``: the eight severity cases, three
# per-case statistics, a reverse stress and a pre-registered verdict, over the packet layout v6 already reads.
_STRESS_SCHEMA = "mio-execution-stress/v7"
_RANGE_SCHEMAS = (_RANGE_SCHEMA, _HEDGE_SCHEMA, _STRESS_SCHEMA)
#: The schemas whose replay reads the packet's session spine and may settle after the entry window.
_PACKET_SCHEMAS = (_HEDGE_SCHEMA, _STRESS_SCHEMA)
_AVAILABLE_SIZE_SCHEMAS = (_AVAILABLE_SIZE_SCHEMA, _RANGE_SCHEMA, _HEDGE_SCHEMA, _STRESS_SCHEMA)
#: The pre-registered ratio sweep (ADR-0312 A1 section 2): read in this order, the smallest passing ratio adopted.
HEDGE_RATIOS = (0.25, 0.5, 0.75, 1.0)
HEDGE_MARKS = ("parity", "carry_ratio")
_HEDGE_KEYS = frozenset({"block", "parity", "ratios"})
# Path A0003: the holdout opens on this session; no v5 bound may reach it.
_PROTECTED_FROM = "2024-01-02"
# v1-v4 keep the 2019 layout ADR-0280 confined the replay to.
_LEGACY_COHORTS = ("limited36", "broad212")
_LEGACY_BOUNDS = {"methods": 4, "sessions": 252, "end_before": "2020-01-01",
                  "latest_expiry": None}
_CONTROL_CSVS = ("trades.csv", "rejected-orders.csv",
                 "orders-and-fills.csv", "daily-account.csv")
_RANGE_ENTRY = frozenset({"config", "result_dir", "result_sha256", "csv_sha256",
                          "quote_index_sha256", "methods", "sessions",
                          "end_before", "latest_expiry"})
_COHORT_NAME = re.compile(r"[a-z0-9][a-z0-9_]*")


def _iso_day(value, field):
    """Return a canonical YYYY-MM-DD string or refuse the v5 field."""
    try:
        if isinstance(value, str) and date.fromisoformat(value).isoformat() == value:
            return value
    except ValueError:
        pass
    raise ValueError("v5 " + field + " must be a YYYY-MM-DD date")


def _range_bounds(entry, *, v6=False):
    """Validate one v5 cohort's pinned bounds; refuse the protected window."""
    bounds = {}
    for field in ("methods", "sessions"):
        if type(entry[field]) is not int or entry[field] < 1:
            raise ValueError("v5 " + field + " must be a positive integer")
        bounds[field] = entry[field]
    bounds["end_before"] = _iso_day(entry["end_before"], "end_before")
    bounds["latest_expiry"] = _iso_day(entry["latest_expiry"], "latest_expiry")
    if max(bounds["end_before"], bounds["latest_expiry"]) >= _PROTECTED_FROM:
        raise ValueError("v5 bounds reach the protected window from " + _PROTECTED_FROM)
    if not v6 and bounds["latest_expiry"] >= bounds["end_before"]:
        raise ValueError("v5 latest_expiry must precede end_before")
    pins = entry["csv_sha256"]
    if not isinstance(pins, dict) or set(pins) != set(_CONTROL_CSVS):
        raise ValueError("v5 csv_sha256 must pin exactly the four control extracts")
    return bounds


def _cohort_order(document):
    """Return the cohorts in replay order after validating their names and fields."""
    cohorts = document["cohorts"]
    if document["schema"] not in _RANGE_SCHEMAS:
        if set(cohorts) != set(_LEGACY_COHORTS):
            raise ValueError("missing exact cohort")
        return _LEGACY_COHORTS
    label = document["period_label"]
    if not isinstance(label, str) or not label.strip():
        raise ValueError("v5 period_label must be a non-empty string")
    if not isinstance(cohorts, dict) or not cohorts:
        raise ValueError("v5 requires at least one cohort")
    for name, entry in cohorts.items():
        if not _COHORT_NAME.fullmatch(name):
            raise ValueError("v5 cohort name must be lower-case [a-z0-9_]: " + name)
        if not isinstance(entry, dict) or set(entry) != _RANGE_ENTRY:
            raise ValueError("v5 cohort fields differ from the contract: " + name)
        _range_bounds(entry, v6=document["schema"] in _PACKET_SCHEMAS)
    return tuple(cohorts)


def _fill_shocked(scenario):
    """Tell whether a case changes the fill price or the displayed depth."""
    return scenario["price_fraction"] != 0 or _size_divisor(scenario) != 1


def _available_size(study):
    """Tell whether the frozen domain opted into ADR-0282 Amendment 1 sizing."""
    return study.params.get("fill_sizing") == WeeklyOptionPortfolioStudy._AVAILABLE_SIZE


def _proportional_fill(study, book, day, method, scenario):
    """Fill the largest whole multiple of the book's primitive ratio shown at side depth.

    The study's sizing owner chooses the multiple; this adapter only feeds it
    effective (half) depth. The v3 case stays zero-shock; a v4 available-size
    domain also sizes the half-depth and adverse-price cases, checking each
    leg's quote, unit depth and stressed price in order.
    """
    legacy = {key: value for key, value in scenario.items() if key != "proportional_fill"}
    if scenario.get("proportional_fill", False) and _fill_shocked(scenario):
        raise ValueError("proportional fill is approved only as a zero-shock case")
    units = WeeklyOptionPortfolioStudy._units(book["legs"])
    if units == 0:
        return _scenario_fill(study, book, day, method, legacy, resized=True)
    symbol, expiry, factor = book["symbol"], book["expiry"], book["factor"]
    node = study._nodes[symbol]
    if day >= expiry or not math.isclose(study._factors[symbol].get(day, math.nan),
                                         factor, rel_tol=1e-9):
        return None, "fill_basis_or_session"
    multiple = units
    for leg in book["legs"]:
        row = study._quotes[symbol].get((day, expiry, leg["right"], leg["strike"]))
        market = node._usable_quote(row, factor)
        if market is None:
            return None, "missing_fill_quote"
        if leg["quantity"] != 0:
            side = "ask_size" if leg["sign"] > 0 else "bid_size"
            fits = WeeklyOptionPortfolioStudy._depth_multiple(
                leg["quantity"], units, scaled_size(row[side], _size_divisor(scenario)))
            if fits is None:
                return None, "insufficient_fill_size"
            multiple = min(multiple, fits)
        if scenario["price_fraction"] != 0:
            price = adverse_side(*market, leg["sign"], scenario["price_fraction"])
            if not math.isfinite(price) or price <= 0:
                return None, "invalid_stressed_fill_price"
    if multiple == units:
        return _scenario_fill(study, book, day, method, legacy, resized=True)
    downsized = copy.deepcopy(book)
    WeeklyOptionPortfolioStudy._resize(downsized, units, multiple)
    return _scenario_fill(study, downsized, day, method, legacy, resized=True)


def _scenario_fill(study, book, day, method, scenario, *, resized=False):
    """Apply changed price/depth, then the existing payoff and reserve owners.

    ``resized`` marks a book whose quantities the proportional rule already
    fixed, so it is not sized twice.
    """
    if not resized and (scenario.get("proportional_fill", False)
                        or (_available_size(study) and _fill_shocked(scenario))):
        return _proportional_fill(study, book, day, method, scenario)
    if scenario["price_fraction"] == 0 and _size_divisor(scenario) == 1:
        active = [leg for leg in book["legs"] if leg["quantity"] != 0]
        if not scenario.get("single_leg_fill", False) or len(active) != 1:
            return study._fill(book, day, study._charge(method))
        leg = active[0]
        row = study._quotes[book["symbol"]].get(
            (day, book["expiry"], leg["right"], leg["strike"]))
        side = "ask_size" if leg["sign"] > 0 else "bid_size"
        if (row is not None and type(row.get(side)) is int
                and row[side] >= leg["quantity"]):
            return study._fill(book, day, study._charge(method))
    filled = copy.deepcopy(book)
    symbol, expiry, factor = book["symbol"], book["expiry"], book["factor"]
    node = study._nodes[symbol]
    if day >= expiry or not math.isclose(study._factors[symbol].get(day, math.nan),
                                         factor, rel_tol=1e-9):
        return None, "fill_basis_or_session"
    single_leg = (scenario.get("single_leg_fill", False)
                  and sum(leg["quantity"] != 0 for leg in book["legs"]) == 1)
    if single_leg:
        filled["legs"] = [leg for leg in filled["legs"] if leg["quantity"] != 0]
    premium = stress_extra = 0.0
    mult = node.params["solver"]["multiplier"]
    for leg in filled["legs"]:
        row = study._quotes[symbol].get((day, expiry, leg["right"], leg["strike"]))
        market = node._usable_quote(row, factor)
        if market is None:
            return None, "missing_fill_quote"
        side = "ask_size" if leg["sign"] > 0 else "bid_size"
        depth = scaled_size(row[side], _size_divisor(scenario))
        if depth < leg["quantity"]:
            if not single_leg or depth < 1:
                return None, "insufficient_fill_size"
            leg["selected_quantity"] = leg["quantity"]
            leg["quantity"] = depth
        bid, ask = market
        price = adverse_side(bid, ask, leg["sign"], scenario["price_fraction"])
        if not math.isfinite(price) or price <= 0:
            return None, "invalid_stressed_fill_price"
        leg["fill_price"] = price
        leg["effective_capacity"] = depth
        premium -= leg["sign"] * price * mult * leg["quantity"]
        stress_extra += (ask - bid) * mult * leg["quantity"] * node.params["stress_spread_fraction"]
    if single_leg:
        filled["contracts"] = sum(leg["quantity"] for leg in filled["legs"])
    fees = filled["contracts"] * node.params["solver"]["fee_per_contract_usd"]
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
        effective_depth = (scaled_size(original_depth, _size_divisor(scenario))
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


def _scenario_curve(study, account, snapshots, hedge=None):
    """Mark held books at unchanged observed mids while entry credit varies.

    ``hedge`` (a :class:`~index_options.ledger_studies.ReplayHedge`) adds each hedged book's forward leg, marked at its
    day's forward (``ReplayHedge.mark``), to the mark.
    """
    from dskit.pipeline.records import number_ok

    marks, paths = {}, {}
    multiplier = study.params["bridge"]["solver"]["multiplier"]
    for book in account.closed:
        last_value = None
        if hedge is not None and book.get("hedge", {}).get("units"):
            paths[book["id"]] = hedge.path(book)
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
            leg = 0.0
            if hedge is not None and book.get("hedge", {}).get("units"):
                leg = hedge.marked(book["hedge"], *paths[book["id"]][day])
            marks[book["id"], day] = (book["credit"] + last_value + leg, carried)
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


def _scenario_finish(study, method, account, decisions, rejected, snapshots, hedge=None):
    """Reuse the funded account and produce a mark curve for changed entry prices."""
    if account.open:
        raise ValueError("scenario ended with unsettled books")
    curve, nav, max_dd = _scenario_curve(study, account, snapshots, hedge)
    trades = account.closed
    result = {
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
    if hedge is not None:
        result["hedge"] = hedge.record()
    return result


class UnsupportedStress(ValueError):
    """A stress input the replay cannot estimate; the case is unsupported and nothing is substituted."""


class ShockEstimator:
    """Per-name scale and market beta from sessions strictly before a day, over the unshocked bars.

    The estimator owns a read-only study that carries the packet's session
    spine and the pinned, unshocked bars, so a shocked bar never feeds an
    estimate. The window is the ``lookback`` daily log returns ending at the
    last session strictly before the estimate day. A short window, a
    nonfinite return, a zero standard deviation, zero basket variance or any
    other condition under which the beta owner would silently fall back to
    all ones raises :class:`UnsupportedStress`. No estimate is shared between
    instances: build one per method and case.

    Parameters
    ----------
    params : dict
        The run's study params (``symbols``, ``correlation_min_pairs``, ``bridge``).
    history : list of str
        The packet's ordered session spine.
    bars : dict
        Symbol to ``{session: close}`` over the spine, never mutated here.
    factors : dict
        Symbol to ``{session: basis factor}`` (read by the reverse stress).
    lookback : int
        Returns per window; at least ``correlation_min_pairs``.
    """

    def __init__(self, params, history, bars, factors, lookback):
        if type(lookback) is not int or lookback < params["correlation_min_pairs"]:
            raise ValueError("v7 stress lookback must be an integer of at least correlation_min_pairs")
        study = WeeklyOptionPortfolioStudy.__new__(WeeklyOptionPortfolioStudy)
        study.params = params
        study.history_calendar = list(history)
        study._bars = bars
        study._factors = factors
        study._beta_cache = {}
        self.study, self.lookback, self._windows = study, lookback, {}

    def window_end(self, day):
        """Return the last spine session strictly before ``day``."""
        return max(d for d in self.study.history_calendar if d < day)

    def sessions_between(self, day, expiry):
        """Return the spine sessions after ``day`` up to and including ``expiry``."""
        return sum(day < d <= expiry for d in self.study.history_calendar)

    def _window(self, day, symbols):
        """Return the day's returns after checking length and finiteness for ``symbols``."""
        import numpy as np

        if day not in self._windows:
            with np.errstate(all="ignore"):
                self._windows[day] = self.study._returns(day, self.lookback)
        returns, count = self._windows[day]
        if count != self.lookback:
            raise UnsupportedStress(f"window of {count} returns before {day}; {self.lookback} needed")
        for symbol in symbols:
            if not np.all(np.isfinite(returns[symbol])):
                raise UnsupportedStress(f"nonpositive or nonfinite bar for {symbol} in the window before {day}")
        return returns

    def scale(self, symbol, day, expiry):
        """Return ``(daily standard deviation, sessions to expiry, window end)`` of one name."""
        import numpy as np

        std = float(np.std(self._window(day, [symbol])[symbol], ddof=1))
        if not math.isfinite(std) or std <= 0:
            raise UnsupportedStress(f"zero standard deviation for {symbol} in the window before {day}")
        sessions = self.sessions_between(day, expiry)
        if sessions < 1:
            raise UnsupportedStress(f"no sessions between {day} and the expiry {expiry}")
        return std, sessions, self.window_end(day)

    def check_beta_window(self, day):
        """Refuse a window the beta owner would answer with its all-ones fallback."""
        import numpy as np

        symbols = self.study.params["symbols"]
        returns = self._window(day, symbols)
        if not float(np.var(np.mean([returns[s] for s in symbols], axis=0))) > 0:
            raise UnsupportedStress(f"zero basket variance in the window before {day}")

    def beta(self, symbol, day):
        """Return the name's beta on the equal-weight basket of the domain's symbols."""
        self.check_beta_window(day)
        fitted = self.study._betas(day, self.lookback)
        value = fitted["betas"][symbol]
        if fitted["observations"] != self.lookback or not math.isfinite(value):
            raise UnsupportedStress(f"beta for {symbol} is not fitted on {day}")
        return value


class ExpiryShock(ABC):
    """One rule that scales the settlement bar of each (symbol, expiry) a case shocks.

    A rule names which settlement bars it moves (:meth:`points`) and by how
    much (:meth:`estimate`); :meth:`apply` joins the two. Subclasses are the
    only place a kind differs: the replay never branches on it.

    Parameters
    ----------
    value : float
        The rule's one knob; the subclass names it (``_VALUE``).
    """

    KIND = None
    _VALUE = None

    def __init__(self, value):
        self.value = value

    @classmethod
    def _acceptable(cls, value):
        """Say whether ``value`` is a finite number that is not a bool."""
        return type(value) is not bool and number_ok(value)

    @staticmethod
    def of(spec):
        """Build the rule a ``{"kind": ..., <knob>: value}`` mapping names; refuse any other shape."""
        if not isinstance(spec, dict) or spec.get("kind") not in _SHOCKS:
            raise ValueError("expiry shock must name one of " + str(sorted(_SHOCKS)) + ": " + repr(spec))
        cls = _SHOCKS[spec["kind"]]
        if set(spec) != {"kind", cls._VALUE} or not cls._acceptable(spec[cls._VALUE]):
            raise ValueError(f"expiry shock {cls.KIND} takes exactly kind and a valid {cls._VALUE}: {spec!r}")
        return cls(spec[cls._VALUE])

    def spec(self):
        """Return the rule as the mapping the document restates."""
        return {"kind": self.KIND, self._VALUE: self.value}

    def _estimator(self, replay):
        """Return the estimator the rule reads, ``None`` for a rule that needs none."""
        return None

    @abstractmethod
    def points(self, replay, packet):
        """Return ``{(symbol, expiry): estimate day}`` for the settlement bars to move."""

    @abstractmethod
    def estimate(self, estimator, symbol, day, expiry):
        """Return ``(multiplier, record)``; the record is ``None`` for a rule with no estimate."""

    def apply(self, replay, packet):
        """Return ``({(symbol, expiry): multiplier}, [estimate records])`` for one method's packet."""
        points = self.points(replay, packet)
        estimator = self._estimator(replay) if points else None
        multipliers, records = {}, []
        for (symbol, expiry), day in sorted(points.items()):
            multiplier, record = self.estimate(estimator, symbol, day, expiry)
            multipliers[symbol, expiry] = multiplier
            if record is not None:
                records.append(record)
        return multipliers, records


class FlatShock(ExpiryShock):
    """Scale every settlement bar by one fixed multiplier (the ADR-0280 cases)."""

    KIND, _VALUE = "flat", "multiplier"

    @classmethod
    def _acceptable(cls, value):
        return super()._acceptable(value) and value > 0

    def points(self, replay, packet):
        """Move the packet's own expiries (every method's when no packet), none for a multiplier of one."""
        return {} if self.value == 1 else dict.fromkeys(replay.expiry_points(packet))

    def estimate(self, estimator, symbol, day, expiry):
        """Return the fixed multiplier and no record: nothing is estimated."""
        return self.value, None


class EstimatedShock(ExpiryShock):
    """A rule sized from sessions strictly before the earliest fill of each (symbol, expiry)."""

    def _estimator(self, replay):
        return replay.new_estimator()

    def points(self, replay, packet):
        """Move each (symbol, expiry) of the packet's books, estimated at the earliest fill session."""
        return replay.fill_points(packet)

    def _record(self, estimator, symbol, day, expiry, multiplier, **found):
        """Return one estimate row of ``shocks.json``."""
        return {"symbol": symbol, "expiry": expiry, "window_end": estimator.window_end(day),
                "sessions_to_expiry": estimator.sessions_between(day, expiry),
                "std": None, "beta": None, "multiplier": multiplier, **found}


class VolatilityShock(EstimatedShock):
    """Scale by ``exp(sigmas x s)``, ``s`` the name's trailing daily deviation over the sessions to expiry."""

    KIND, _VALUE = "volatility", "sigmas"

    def estimate(self, estimator, symbol, day, expiry):
        """Return ``exp(sigmas x std x sqrt(sessions))`` and its record; refuse a nonpositive result."""
        std, sessions, _end = estimator.scale(symbol, day, expiry)
        try:
            multiplier = math.exp(self.value * std * math.sqrt(sessions))
        except OverflowError:
            multiplier = math.inf
        if not math.isfinite(multiplier) or multiplier <= 0:
            raise UnsupportedStress(f"volatility multiplier for {symbol} is not finite and positive")
        return multiplier, self._record(estimator, symbol, day, expiry, multiplier, std=std)


class MarketShock(EstimatedShock):
    """Scale by ``max(0, 1 + beta x move)``, the rule ``_account_shock`` applies."""

    KIND, _VALUE = "market", "move"

    def estimate(self, estimator, symbol, day, expiry):
        """Return ``max(0, 1 + beta x move)`` and its record."""
        beta = estimator.beta(symbol, day)
        multiplier = max(0.0, 1.0 + beta * self.value)
        return multiplier, self._record(estimator, symbol, day, expiry, multiplier, beta=beta)


_SHOCKS = {cls.KIND: cls for cls in (FlatShock, VolatilityShock, MarketShock)}


def expiry_shock(scenario):
    """Return a case's expiry rule: v7 declares one, v1-v6 declare ``expiry_multiplier``."""
    if "expiry_shock" in scenario:
        return ExpiryShock.of(scenario["expiry_shock"])
    return FlatShock(scenario["expiry_multiplier"])


#: The closed statistics a criterion may name (ADR-0316 A6); worst-over-cases, then the reverse distances.
STRESS_STATISTICS = ("worst_loss_fraction", "worst_drawdown", "worst_breach_days",
                     "worst_name_reserve_share", "reverse_down_move", "reverse_up_move")
_REVERSE_STATISTICS = STRESS_STATISTICS[4:]
#: A criterion on a worst-case statistic is an upper limit; on a reverse distance it is a lower limit.
_UPPER_LIMITS, _LOWER_LIMITS = ("<", "<="), (">", ">=")


class StressSpec:
    """The validated ``stress`` and ``criteria`` entries of a v7 document."""

    def __init__(self, lookback, reverse, criteria):
        self.lookback, self.reverse, self.criteria = lookback, reverse, criteria

    @classmethod
    def from_document(cls, document):
        """Validate the entries (default-deny) and return the spec, before any replay."""
        spec = document.get("stress")
        if not isinstance(spec, dict) or set(spec) != {"lookback", "reverse"}:
            raise ValueError("v7 stress must carry exactly lookback and reverse")
        lookback = spec["lookback"]
        if type(lookback) is not int or lookback < 1:
            raise ValueError("v7 stress lookback must be a positive integer")
        problems = ReverseStress.validate_params(spec["reverse"])
        if problems:
            raise ValueError("v7 stress reverse: " + "; ".join(problems))
        return cls(lookback, ReverseStress(spec["reverse"]), StressVerdict.check(document.get("criteria")))


class CaseStatistics:
    """The three per-case statistics of a v7 summary row (ADR-0316 A5)."""

    KEYS = ("loss_fraction", "breach_days", "worst_name_reserve_share")

    @classmethod
    def of(cls, result, daily):
        """Return the case's loss over contributions, breach sessions and largest name share of equity."""
        books = {trade["id"]: trade for trade in result["trades"]}
        names, share = NameShare({}), None
        for row in daily:
            if not row["open_ids"]:
                continue
            found = names.largest(((books[key]["symbol"], books[key]["reserve"]) for key in row["open_ids"]),
                                  row["equity"])
            share = found[1] if share is None or found[1] > share else share
        return {"loss_fraction": (0.0 - result["pnl"]) / result["contributions"],
                "breach_days": sum(bool(row["reserve_breach"] or row["negative_cash"] or row["negative_funding"])
                                   for row in daily),
                "worst_name_reserve_share": share}


class _Statistic:
    """One table entry as ``Scorecard`` reads it."""

    def __init__(self, value, n):
        self.value, self.n = value, n


class _StressTable:
    """The closed statistics table ``Scorecard`` judges."""

    names = frozenset(STRESS_STATISTICS)

    def __init__(self, entries):
        self._entries = entries

    def get(self, name):
        return _Statistic(self._entries[name]["value"], self._entries[name]["n"])


class StressVerdict:
    """The pre-registered criteria applied to each method's worst case (ADR-0316 A6 and A7).

    Parameters
    ----------
    criteria : list of dict
        ``Criterion`` objects over :data:`STRESS_STATISTICS`; empty is legal and judges nothing.
    """

    def __init__(self, criteria):
        self.criteria = self.check(criteria)

    @staticmethod
    def check(criteria):
        """Return the built criteria or refuse: unknown statistic, wrong direction or malformed entry."""
        if not isinstance(criteria, list):
            raise ValueError("v7 criteria must be a list")
        problems = [p for i, obj in enumerate(criteria) for p in Criterion.problems(obj, f"criteria[{i}]")]
        if problems:
            raise ValueError("; ".join(problems))
        built = [Criterion.from_obj(obj) for obj in criteria]
        unknown = sorted({c.stat for c in built} - set(STRESS_STATISTICS))
        if unknown:
            raise ValueError(f"criteria name unknown statistic(s) {unknown}; known: {list(STRESS_STATISTICS)}")
        for c in built:
            allowed = _LOWER_LIMITS if c.stat in _REVERSE_STATISTICS else _UPPER_LIMITS
            if c.op not in allowed:
                raise ValueError(f"criterion {c.name!r} on {c.stat} takes {list(allowed)}: wrong direction")
        return built

    def rows(self, completed):
        """Return one verdict row per (cohort, method), in first-seen order."""
        groups = {}
        for item in completed:
            groups.setdefault((item[0], item[2]), []).append(item)
        return [self._row(cohort, method, items) for (cohort, method), items in groups.items()]

    @staticmethod
    def _statistics(items):
        """Return the closed table: each worst value over complete cases, then the control's reverse distances."""
        control = next(item for item in items if item[1] == "control")
        reverse = (control[3] or {}).get("reverse_stress") or {}
        filled = control[5]["filled"] or 0
        complete = [item[5] for item in items if item[5]["status"] == "complete"]
        worst_n = min(m["filled"] for m in complete)
        shares = [m["worst_name_reserve_share"] for m in complete if m["worst_name_reserve_share"] is not None]
        values = {
            "worst_loss_fraction": max(m["loss_fraction"] for m in complete),
            "worst_drawdown": -min(m["max_drawdown"] for m in complete),
            "worst_breach_days": max(m["breach_days"] for m in complete),
            "worst_name_reserve_share": max(shares) if shares else None,
            "reverse_down_move": reverse.get("down_move"), "reverse_up_move": reverse.get("up_move")}
        if filled == 0:
            values = dict.fromkeys(values)
        return {name: {"value": value, "n": filled if name in _REVERSE_STATISTICS else worst_n}
                for name, value in values.items()}

    def _row(self, cohort, method, items):
        statistics = self._statistics(items)
        card = Scorecard(self.criteria, _StressTable(statistics))
        unsupported = [item[1] for item in items if item[5]["status"] != "complete"]
        covered = {c.stat for c in self.criteria}
        uncovered = [name for name in STRESS_STATISTICS if name not in covered]
        status = card.status
        if unsupported:
            status = "FAIL"
        elif status == "PASS" and uncovered:
            status = "INCONCLUSIVE"
        reverse = (next(item for item in items if item[1] == "control")[3] or {}).get("reverse_stress") or {}
        return {"cohort": cohort, "method": method, "status": status, "statistics": statistics,
                "unsupported": unsupported, "uncovered": uncovered,
                "cases": [{"scenario": item[1], "status": item[5]["status"],
                           "selected": item[5]["selected"], "filled": item[5]["filled"]} for item in items],
                "reverse": {key: reverse.get(key) for key in
                            ("down_reached", "up_reached", "sessions", "unavailable")},
                "criteria": [verdict.to_obj() for verdict in card.verdicts]}


class FrozenMIOReplay:
    """Replay saved selected books through existing account/ledger mechanics."""

    #: v7 only: each method's shocks are keyed by its own packet and a control reverse stress runs.
    per_method = False
    stress = None
    last_shocks = ()

    def __init__(self, cohort, document, pins):
        self.cohort = cohort
        self.document = document
        self.pins = pins
        self.ranged = document["schema"] in _RANGE_SCHEMAS
        entry = document["cohorts"][cohort]
        v6 = document["schema"] == _HEDGE_SCHEMA
        v7 = document["schema"] == _STRESS_SCHEMA
        later = document["schema"] in _PACKET_SCHEMAS
        label = "v7" if v7 else "v6"
        bounds = _range_bounds(entry, v6=later) if self.ranged else _LEGACY_BOUNDS
        original = self._result_dir(document, cohort)
        self.original_dir = original
        config_path = self._run_document(document, cohort)
        key = entry["config"] if self.ranged else config_path.name
        pinned = pins["original_configs"].get(key)
        if pinned is None or _sha(config_path) != pinned["sha256"]:
            raise ValueError("original run document drift")
        self.config = {"name": key, "sha256": pinned["sha256"]}
        config = _load_json(config_path)
        study = config["pipeline"]["plan"]["params"]["study"]
        self.domain = study["domain"]
        self.hedge_spec = document.get("hedge") if v6 else None
        if v6 and not isinstance(self.hedge_spec, dict):
            raise ValueError("v6 hedge must be a mapping")
        self._sizing_schema(self.domain["params"], document["schema"])
        self.symbols = self.domain["params"]["symbols"]
        tail, packet = bounds["end_before"], None
        if later and study.get("contract") == "index_options.mio_contract:MIOStudyContract":
            from index_options.mio_contract import MIOStudyContract

            packet = MIOStudyContract.packet(study)
            if (packet["end_before"] != bounds["end_before"]
                    or any(self.domain["params"]["bridge"][key] != packet[key]
                           for key in ("start", "end_before"))):
                raise ValueError(label + " entry window differs from the packet")
            if v6 and "corporate_actions" in self.domain["sources"]:
                raise ValueError("hedge_split_unsupported: a hedged replay takes no split adjustment")
            tail, _ = MIOStudyContract.settlement_tail(study)
        if v7 and packet is None:
            raise ValueError("v7 requires an MIOStudyContract packet run document with a pinned session spine")
        self.check_hedge_seal(self.hedge_spec, {**bounds, "end_before": tail}, cohort)
        result_path = original / "result.json"
        expected = entry["result_sha256"]
        run_pin = pins["runs"][cohort]
        if packet is not None:
            _verify_packet_receipt(config, run_pin, expected)
        if _sha(result_path) != expected:
            raise ValueError("original result identity drift: " + cohort)
        self.original = _load_json(result_path)
        self._verify_original_csvs(pins)
        if (not self.original["complete"]
                or len(self.original["results"]) != bounds["methods"]):
            raise ValueError("incomplete original method packet")
        self.calendar = self.original["audit"]["calendar"]
        history = self.calendar
        if packet is not None:
            history = _packet_sessions(self.domain["sources"]["sessions"], self.domain["history_start"], tail)
            if self.calendar != [day for day in history if day >= packet["start"]]:
                raise ValueError("saved account calendar differs from the pinned packet sessions")
            if any(not packet["start"] <= decision["date"] < packet["end_before"]
                   for result in self.original["results"] for decision in result["result"]["decisions"]):
                raise ValueError("saved decision outside the packet entry window")
        if (not self.calendar or len(self.calendar) != bounds["sessions"] or max(self.calendar) >= tail
                or (bounds["latest_expiry"] is not None and bounds["latest_expiry"] >= tail)):
            raise ValueError("protected account calendar")
        if self.ranged and self.domain["params"]["bridge"].get("end_before") != bounds["end_before"]:
            raise ValueError("v5 end_before differs from the run document: " + cohort)
        self.bars = _bar_rows(self.domain["sources"]["bars"], self.symbols, history)
        run_dir = Path(run_pin["run_dir"])
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
        latest = bounds["latest_expiry"]
        if any(day not in self.calendar or day >= tail
               or (latest is not None and day > latest)
               for days in needed.values() for day in days):
            raise ValueError("selected expiry outside the document bounds" if self.ranged
                             else "selected expiry outside 2019 account calendar")
        _missing_expiry_factors(self.domain["sources"]["chain"], self.symbols,
                                needed, self.factors)
        self.params = {**self.domain["params"], "selected_pairs": self.domain["pairs"]}
        self.history = history
        if v7:
            self.stress = StressSpec.from_document(document)
            self.per_method = True
            self.new_estimator()        # refuses a lookback below correlation_min_pairs before any case runs

    def new_estimator(self):
        """Return a fresh estimator over the unshocked bars and the packet spine; none is shared between cases."""
        return ShockEstimator(self.params, self.history, self.bars, self.factors, self.stress.lookback)

    def expiry_points(self, packet=None):
        """Return the (symbol, expiry) of the packet's selected books, or of every method's when ``None``."""
        packets = self.original["results"] if packet is None else [packet]
        return {(book["symbol"], book["expiry"]) for item in packets
                for decision in item["result"]["decisions"] for book in decision.get("books", [])}

    def fill_points(self, packet):
        """Return ``{(symbol, expiry): earliest fill session}`` of one method's selected books.

        A book fills on the first account session after its decision date. Books that share a symbol and an
        expiry share one settlement bar, so they share one shock estimated at the earliest of their fills.

        Raises
        ------
        UnsupportedStress
            When a selected book has no session to fill on.
        """
        points = {}
        for decision in packet["result"]["decisions"]:
            fill = next((day for day in self.calendar if day > decision["date"]), None)
            for book in decision.get("books", []):
                if fill is None:
                    raise UnsupportedStress("no fill session after the decision on " + decision["date"])
                key = (book["symbol"], book["expiry"])
                points[key] = min(fill, points.get(key, fill))
        return points

    @staticmethod
    def check_hedge_seal(spec, bounds, cohort):
        """Refuse a hedged replay whose window or latest expiry passes the development seal."""
        if spec is not None and max(bounds["end_before"], bounds["latest_expiry"] or "") > DEVELOPMENT_SEAL:
            raise ValueError(f"hedge_seal: a hedged replay must end before {DEVELOPMENT_SEAL}: " + cohort)

    @staticmethod
    def _result_dir(document, cohort):
        """v5 names each cohort's result folder; v1-v4 use <original_root>/<cohort>."""
        if document["schema"] in _RANGE_SCHEMAS:
            return Path(document["cohorts"][cohort]["result_dir"])
        return Path(document["original_root"]) / cohort

    @staticmethod
    def _run_document(document, cohort):
        """Return the cohort's run document under the child configs directory.

        v4 names it explicitly (a plain file name that resolves inside the
        directory); v5 may name a relative path in a subdirectory of it;
        v1-v3 keep mio-2019-<cohort>.json and refuse the key.
        """
        configs = Path(document["repository_root"]) / "children/index_options/configs"
        entry = document["cohorts"][cohort]
        if document["schema"] in _RANGE_SCHEMAS:
            name = entry.get("config")
            if (not isinstance(name, str) or not name
                    or any(part in ("", ".", "..") for part in name.split("/"))
                    or not (configs / name).resolve().is_relative_to(configs.resolve())):
                raise ValueError("v5 cohort config must be a relative file inside the "
                                 "child configs: " + cohort)
            return configs / name
        if document["schema"] != _AVAILABLE_SIZE_SCHEMA:
            if "config" in entry:
                raise ValueError("cohort config requires schema v4: " + cohort)
            return configs / ("mio-2019-" + cohort + ".json")
        name = entry.get("config")
        if (not isinstance(name, str) or not name or Path(name).name != name
                or name in (".", "..")
                or (configs / name).resolve().parent != configs.resolve()):
            raise ValueError("v4 cohort config must be a file inside the child configs: " + cohort)
        return configs / name

    @staticmethod
    def _sizing_schema(params, schema):
        """Bind available-size fills to schema v4/v5, and v4/v5 to available-size fills."""
        available = params.get("fill_sizing") == WeeklyOptionPortfolioStudy._AVAILABLE_SIZE
        if available != (schema in _AVAILABLE_SIZE_SCHEMAS):
            version = {_STRESS_SCHEMA: "v7", _HEDGE_SCHEMA: "v6", _RANGE_SCHEMA: "v5"}.get(schema, "v4")
            raise ValueError("available-size fill sizing and schema " + version
                             + " require each other")

    def _verify_original_csvs(self, pins):
        """Keep the four human-facing control extracts bound to original pins."""
        item = pins["evidence"]["artifact-index.json"]
        path = Path(item["path"])
        if _sha(path) != item["sha256"]:
            raise ValueError("original artifact-index identity drift")
        if self.ranged:
            expected = self.document["cohorts"][self.cohort]["csv_sha256"]
        else:
            index = _load_json(path)["csv_files"]
            expected = {name: index[self.cohort + "/" + name]["sha256"]
                        for name in _CONTROL_CSVS}
        for name in _CONTROL_CSVS:
            relative = self.cohort + "/" + name
            source = self.original_dir / name
            if _sha(source) != expected[name]:
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
            if [row[key] for row in observed] != [item[key] for item in objects]:
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
        expected_keys = []
        trades = {book["id"]: book for book in result["trades"]}
        rejected = {book["id"]: book for book in result["rejected"]}
        for decision in result["decisions"]:
            for book in decision.get("books", []):
                trade = trades.get(book["id"])
                refusal = rejected.get(book["id"])
                for leg in book["legs"]:
                    side = "buy" if leg["sign"] > 0 else "sell"
                    key = (decision["date"], book["id"], leg["right"],
                           float(leg["strike"]), side)
                    expected_keys.append(key)
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
        if list(keys) != expected_keys:
            raise ValueError("control order/fill CSV leg order mismatch: " + method)

    def close(self):
        """Verify and close every original read-only quote index."""
        for view in self.quotes.values():
            view.close(verify=True)

    def study(self, scenario, packet=None):
        """Build only the existing study fields needed for frozen replay.

        ``packet`` (v7) keys the expiry shock to that method's own selected books; without it every method's
        expiries are shocked, as v1 to v6 always did.
        """
        study = WeeklyOptionPortfolioStudy.__new__(WeeklyOptionPortfolioStudy)
        study.params = self.params
        study.calendar = self.calendar
        study._quotes = self.quotes
        study._factors = self.factors
        study._bars = copy.deepcopy(self.bars)
        study.adjustments = {}
        study._nodes = {symbol: _AllQuotedOptionContext(
            "prepare", {**self.params["bridge"], "symbol": symbol})
            for symbol in self.symbols}
        multipliers, self.last_shocks = expiry_shock(scenario).apply(self, packet)
        for (symbol, day), multiplier in multipliers.items():
            study._bars[symbol][day] *= multiplier
        return study

    def _reverse_stress(self, opens):
        """Return the control's reverse-stress record; an input error gives nulls and its reason, never a raise."""
        spec = self.stress
        try:
            estimator = self.new_estimator()
            sessions = []
            for day, books, funding in opens:
                if not books:
                    continue
                estimator.check_beta_window(day)
                _estimate, losses = estimator.study._account_shock(
                    books, day, {"moves": spec.reverse.params["moves"], "beta_lookback": spec.lookback})
                if losses is None:
                    raise UnsupportedStress("a book input is missing or nonfinite on " + day)
                sessions.append((funding, {float(move): loss for move, loss in losses.items()}))
            return {**spec.reverse.search(sessions), "sessions": len(sessions), "unavailable": None}
        except (ValueError, KeyError, ArithmeticError) as exc:
            return {"down_move": None, "down_reached": None, "up_move": None, "up_reached": None,
                    "sessions": 0, "unavailable": str(exc)}

    def run_method(self, packet, scenario):
        """Carry one method's frozen orders through a separate scenario account."""
        original = packet["result"]
        method = original["method"]
        decisions = original["decisions"]
        by_day = {item["date"]: item for item in decisions}
        account = FundedOptionAccount(
            self.params["initial"], self.params["deposit"], self.params["deposit_every_days"],
            self.calendar[0], deposit_end_before=self.params["bridge"]["end_before"])
        rejected, snapshots, daily = [], [], []
        self.last_progress = {"daily": daily, "rejected": rejected,
                              "account": account, "date": None,
                              "attempted_ids": [], "attempts": [],
                              "settlements": {}}
        study = self.study(scenario, packet) if self.per_method else self.study(scenario)
        hedge = None
        if "hedge_ratio" in scenario:
            problems = WeeklyOptionPortfolioStudy._BLOCKS.cross_checks({**method, "hedge": self.hedge_spec["block"]})
            if problems:
                raise ValueError("; ".join(problems))       # hedge_with_sizer, as on the replay route
            if getattr(study, "adjustments", None):
                raise ValueError("hedge_split_unsupported: a hedged run takes no split adjustment")
            hedge = ReplayHedge(study, {**self.hedge_spec["block"], "ratio": scenario["hedge_ratio"]},
                                self.hedge_spec["parity"], mark_rule=scenario["hedge_mark"])
        pending = None
        opens = []
        for day in self.calendar:
            attempts, fills, refusals = [], [], []
            attempt_evidence = []
            self.last_progress.update(date=day, attempted_ids=attempts,
                                      attempts=attempt_evidence, settlements={})
            before_cash = account.cash
            before_closed = len(account.closed)
            settlements = study._settlements(account, day)
            self.last_progress["settlements"] = settlements
            deposit = account.advance(day, settlements)
            expiries = account.closed[before_closed:]
            if pending is not None:
                survivors = []
                for book in pending["books"]:
                    attempts.append(book["id"])
                    evidence = _attempt_evidence(study, book, day, scenario)
                    attempt_evidence.append(evidence)
                    filled, reason = _scenario_fill(study, book, day, method, scenario)
                    if filled is not None and hedge is not None:
                        hedge.attach(filled, day, study._charge(method))
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
            opens.append((day, list(account.open.values()), account.funding))
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
                                     "selected_quantity": leg.get(
                                         "selected_quantity", leg["quantity"]),
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
                "reserve": snapshot["reserve"],
                "reserve_breach":
                    snapshot["reserve"] > max(0.0, account.funding) + 1e-5,
                "negative_cash": account.cash < 0,
                "negative_funding": account.funding < 0})
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
            result = _scenario_finish(study, method, account, decisions, rejected, snapshots, hedge)
        if scenario["id"] == "control":
            for key, value in original.items():
                if result.get(key) != value:
                    raise ValueError("zero-shock control mismatch: " + self.cohort +
                                     "/" + method["id"] + "/" + key)
            self._verify_control_csv_rows(result)
            if self.per_method:
                result["reverse_stress"] = self._reverse_stress(opens)
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


_PROXY_NOTE = ("Fills are simulated at end-of-day displayed depth, never broker fills; "
               "this is a depth proxy, not fill probability or decision evidence.")


def _depth_proxy(schema, decisions):
    """v3-v5 only: the selected contract total that turns on proxy wording."""
    if schema not in ("mio-execution-stress/v3", *_AVAILABLE_SIZE_SCHEMAS):
        return None
    return sum(book["contracts"] for item in decisions for book in item.get("books", []))


def _contract_headline(metrics, daily, selected_contracts):
    """Book counts plus selected/filled contract totals and downsized books."""
    fills = [book for row in daily for book in row["fills"]]
    downsized = sum(any(leg["quantity"] != leg["selected_quantity"] for leg in book["legs"])
                    for book in fills)
    filled = sum(leg["quantity"] for book in fills for leg in book["legs"])
    return (f"Books: selected {metrics['selected']}; filled {len(fills)} "
            f"({downsized} downsized); refused {metrics['refused']}. "
            f"Contracts: selected {selected_contracts}; filled {filled}.")


def _daybook(cohort, scenario, method, metrics, daily, incomplete=None,
             selected_contracts=None, period=None):
    """Readable daily opportunities, fills, expiries and losses.

    ``selected_contracts`` (v3-v5, from ``_depth_proxy``) adds the depth-proxy
    note and the contract headline; ``None`` keeps v1/v2 bytes unchanged.
    ``period`` is the v5 document's label; ``None`` keeps the 2019 wording.
    """
    proxy = selected_contracts is not None
    lines = [
        f"# {cohort} / {method} / {scenario}", "",
        ("2019 development" if period is None else period)
        + " diagnostic. Chosen orders are decided after the named",
        "close; fills appear on the next session. Expiry P&L is realized;",
        "marked daily change is interim equity change after deposits.",
        "Executed-leg quantities are shown as filled/selected.",
        *([_PROXY_NOTE] if proxy else []), "",
        *([_contract_headline(metrics, daily, selected_contracts), ""] if proxy else []),
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
                f"{leg['side']} {leg['quantity']}/{leg['selected_quantity']} {leg['right']} "
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
            legs = "; ".join(f"{leg['side']} {leg['quantity']}/{leg['selected_quantity']} {leg['right']} "
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


def _dispositions(cohort, scenario, method, result, daily, *,
                  decisions=None, incomplete=None):
    """One auditable outcome per attempted selected book, including prefixes."""
    if result is None and incomplete is None:
        return []
    attempts = {item["id"]: (row, item) for row in daily
                for item in row["attempts"]}
    if incomplete is not None:
        failure = incomplete["failure"]
        for item in failure["attempts"]:
            attempts.setdefault(item["id"], (failure, item))
    expiries = {item["id"]: (row, item) for row in daily
                for item in row["expiries"]}
    closed = {book["id"]: book for book in (
        result["trades"] if result is not None else incomplete["closed_trades"])}
    open_books = ({book["id"]: book for book in incomplete["open_books"]}
                  if incomplete is not None else {})
    rejected = {book["id"]: book for book in (
        result["rejected"] if result is not None else incomplete["rejected"])}
    decisions = result["decisions"] if result is not None else decisions
    rows = []
    for decision in decisions:
        for selected in decision.get("books", []):
            key = selected["id"]
            if key not in attempts:
                if incomplete is not None:
                    continue
                raise ValueError("selected book lacks fill attempt: " + key)
            fill_day, attempt = attempts[key]
            settled = closed.get(key)
            trade = settled or open_books.get(key)
            refusal = rejected.get(key)
            if result is not None and (trade is None) == (refusal is None):
                raise ValueError("selected book lacks unique disposition: " + key)
            expiry_day, expiry = expiries.get(key, (None, None))
            preflight = (refusal if refusal is not None
                         and "fees" in refusal else None)
            if settled is not None:
                status = "filled"
            elif trade is not None:
                status = "open_at_failure"
            elif refusal is not None:
                status = "refused"
            else:
                status = "undetermined_at_failure"
            payoff = (settled["pnl"] - settled["credit"]
                      if settled is not None else None)
            expiry_date = (settled["close_date"] if settled is not None else None)
            cash_after_expiry = (
                expiry_day["cash"] if expiry_day is not None else
                incomplete["failure"]["cash"] if incomplete is not None
                and expiry_date == incomplete["failure"]["date"] else None)
            rows.append({
                "cohort": cohort, "scenario": scenario, "method": method,
                "id": key, "symbol": selected["symbol"],
                "decision_date": decision["date"], "fill_date": fill_day["date"],
                "expiry": selected["expiry"],
                "possible_books": decision["candidate_books"],
                "possible_option_contracts": decision["candidate_contracts"],
                "status": status,
                "reason": (refusal["reason"] if refusal is not None else
                           incomplete["failure"]["reason"]
                           if status == "undetermined_at_failure" else None),
                "selected_contracts": selected["contracts"],
                "filled_contracts": trade["contracts"] if trade is not None else 0,
                "selected_credit": selected["credit"],
                "selected_reserve": selected["reserve"],
                "attempt_legs": attempt["legs"],
                "preflight_credit": preflight["credit"] if preflight is not None else None,
                "preflight_fees": preflight["fees"] if preflight is not None else None,
                "preflight_reserve": preflight["reserve"] if preflight is not None else None,
                "fill_legs": ([{"side": "buy" if leg["sign"] > 0 else "sell",
                               "right": leg["right"], "strike": leg["strike"],
                               "quantity": leg["quantity"],
                               "selected_quantity": leg.get(
                                   "selected_quantity", leg["quantity"]),
                               "price": leg["fill_price"]}
                              for leg in trade["legs"]] if trade is not None else None),
                "fill_credit": trade["credit"] if trade is not None else None,
                "fill_fees": trade["fees"] if trade is not None else None,
                "fill_reserve": trade["reserve"] if trade is not None else None,
                "cash_delta_at_fill": trade["credit"] if trade is not None else 0.0,
                "cash_after_fill_day": fill_day["cash"],
                "expiry_date": expiry_date,
                "original_expiry_underlying":
                    expiry["original_underlying"] if expiry is not None else None,
                "stressed_expiry_underlying":
                    expiry["stressed_underlying"] if expiry is not None else None,
                "expiry_payoff": payoff,
                "realized_pnl": settled["pnl"] if settled is not None else None,
                "cash_delta_at_expiry": payoff,
                "cash_after_expiry_day": cash_after_expiry,
            })
    return rows


def _failure_state(progress, reason):
    """Expose the attempted settlement and account state at a failed session."""
    account = progress["account"]
    reserve = sum(book["reserve"] for book in account.open.values())
    attempts = []
    breaches = []
    for key, pnl in progress["settlements"].items():
        book = account.open.get(key)
        if book is None:
            continue
        row = {"id": key, "attempted_pnl": pnl,
               "reserved_loss": book["reserve"],
               "excess_loss": max(0.0, -pnl - book["reserve"])}
        attempts.append(row)
        if pnl < -book["reserve"] - 1e-5:
            breaches.append(row)
    aggregate_breach = reserve > max(0.0, account.funding) + 1e-5
    return {"date": progress["date"], "reason": reason,
            "cash": account.cash, "funding": account.funding,
            "contributions": account.contributions,
            "realized": account.realized, "reserve": reserve,
            "open_ids": sorted(account.open),
            "attempted_ids": list(progress["attempted_ids"]),
            "attempts": list(progress["attempts"]),
            "settlement_attempts": attempts,
            "settlement_reserve_breaches": breaches,
            "aggregate_reserve_breach": aggregate_breach,
            "reserve_breach": bool(breaches) or aggregate_breach,
            "negative_cash": account.cash < 0,
            "negative_funding": account.funding < 0}


def _unsupported_metrics(selected, reason, rejected):
    """Keep an incomplete stress visible without inventing a profit."""
    keys = ("filled", "refused", "fill_rate", "final_profit_usd",
            "final_equity_usd", "contributions_usd", "twr", "max_drawdown",
            "fees_usd", "gross_gains_usd", "gross_losses_usd",
            "profit_factor", "win_rate", "worst_trade_usd", "trade_cvar5_usd",
            "worst_expiry_day_usd", "minimum_cash_usd", "peak_reserve_usd")
    return {"status": "unsupported", "unsupported_reason": reason,
            "selected": selected, **{key: None for key in keys},
            "refusal_reasons": dict(Counter(book["reason"] for book in rejected))}


_SCHEMA_VERSIONS = ("mio-execution-stress/v1", "mio-execution-stress/v2",
                    "mio-execution-stress/v3", _AVAILABLE_SIZE_SCHEMA, _RANGE_SCHEMA, _HEDGE_SCHEMA,
                    _STRESS_SCHEMA)
_LEGACY_DOCUMENT = frozenset({"schema", "repository_root", "original_root", "results_pins",
                              "results_pins_sha256", "cohorts", "scenarios", "output_root"})
_RANGE_DOCUMENT = (_LEGACY_DOCUMENT - {"original_root"}) | {"period_label"}
_HEDGE_DOCUMENT = _RANGE_DOCUMENT | {"hedge"}
_STRESS_DOCUMENT = _RANGE_DOCUMENT | {"stress", "criteria"}


def hedge_scenario_id(ratio, mark="parity"):
    """Return the id of the ``static_forward_hedge`` scenario at ``ratio`` (the carry-ratio robustness twin adds a suffix)."""
    return f"static_forward_hedge_r{round(ratio * 100):03d}" + ("" if mark == "parity" else "_carry")


def _require_clean_worktree(repository_root):
    """Refuse a replay whose source worktree has uncommitted changes."""
    if subprocess.check_output(["git", "-C", repository_root, "status", "--porcelain"],
                               text=True).strip():
        raise ValueError("replay source worktree must be clean for pinned publication")


def _hedge_document(hedge):
    """Validate a v6 document's ``hedge`` entry: the block (replay mode, no condition), the parity params, the sweep."""
    if not isinstance(hedge, dict) or set(hedge) != _HEDGE_KEYS:
        raise ValueError("v6 hedge must carry exactly " + str(sorted(_HEDGE_KEYS)))
    if list(hedge["ratios"]) != list(HEDGE_RATIOS):
        raise ValueError(f"v6 hedge ratios are the pre-registered sweep {list(HEDGE_RATIOS)}")
    block = hedge["block"]
    if not isinstance(block, dict) or "ratio" in block or block.get("mode") != "replay" or block.get("condition") is not None:
        raise ValueError("v6 hedge block must be a replay-mode block without a ratio (the sweep sets it) and no condition")
    ReplayHedge.validate_block({**block, "ratio": HEDGE_RATIOS[0]}, hedge["parity"])
    return hedge


#: The seven ADR-0280 cases: id, price fraction, half depth, flat expiry multiplier.
_ADR_0280_CASES = (
    ("control", 0, False, 1), ("adverse_price", 0.25, False, 1),
    ("half_size", 0, True, 1), ("expiry_down_15", 0, False, 0.85),
    ("expiry_up_15", 0, False, 1.15),
    ("joint_down_15", 0.25, True, 0.85),
    ("joint_up_15", 0.25, True, 1.15))
#: The eight ADR-0316 severity cases: id, price fraction, depth divisor, expiry rule kind and its value.
#: The joint cases keep full displayed depth (Amendment 3); ``quarter_size`` is its own case.
_SEVERITY_CASES = (
    ("adverse_price_100", 1.0, 1, "flat", 1), ("quarter_size", 0, 4, "flat", 1),
    ("expiry_down_3s", 0, 1, "volatility", -3), ("expiry_up_3s", 0, 1, "volatility", 3),
    ("market_down_30", 0, 1, "market", -0.30), ("market_up_20", 0, 1, "market", 0.20),
    ("joint_down_severe", 1.0, 1, "market", -0.30), ("joint_up_severe", 1.0, 1, "market", 0.20))


def _stress_scenarios():
    """Return the fifteen approved v7 cases in one uniform shape (price, depth divisor, expiry rule)."""
    cases = [(name, price, 2 if half else 1, "flat", expiry) for name, price, half, expiry in _ADR_0280_CASES]
    return [{"id": name, "price_fraction": price, "size_divisor": divisor,
             "expiry_shock": _SHOCKS[kind](value).spec()}
            for name, price, divisor, kind, value in [*cases, *_SEVERITY_CASES]]


def _approved_scenarios(schema, hedge=None):
    """Return the exact approved scenario list for one document version (v6 appends the hedge sweep)."""
    if schema not in _SCHEMA_VERSIONS:
        raise ValueError("invalid execution-stress document")
    if schema == _STRESS_SCHEMA:
        return _stress_scenarios()
    expected = list(_ADR_0280_CASES)
    expected = [
        {"id": name, "price_fraction": price, "half_size": half,
         "expiry_multiplier": expiry}
        for name, price, half, expiry in expected]
    if schema in ("mio-execution-stress/v2", "mio-execution-stress/v3"):
        expected += [
            {"id": "single_leg_available_size", "price_fraction": 0,
             "half_size": False, "expiry_multiplier": 1,
             "single_leg_fill": True},
            {"id": "single_leg_half_size", "price_fraction": 0,
             "half_size": True, "expiry_multiplier": 1,
             "single_leg_fill": True}]
    if schema == _HEDGE_SCHEMA:
        _hedge_document(hedge)
        expected += [
            {"id": hedge_scenario_id(r, mark), "price_fraction": 0, "half_size": False, "expiry_multiplier": 1,
             "hedge_ratio": r, "hedge_mark": mark}
            for r in HEDGE_RATIOS for mark in HEDGE_MARKS]
    if schema == "mio-execution-stress/v3":
        expected.append(
            {"id": "proportional_available_size", "price_fraction": 0,
             "half_size": False, "expiry_multiplier": 1,
             "proportional_fill": True})
    return expected


def execute(document_path):
    """Validate one JSON contract, replay all methods, then pin fresh outputs."""
    doc_path = Path(document_path).resolve()
    document = _load_json(doc_path)
    required = (_STRESS_DOCUMENT if isinstance(document, dict) and document.get("schema") == _STRESS_SCHEMA
                else _HEDGE_DOCUMENT if isinstance(document, dict) and document.get("schema") == _HEDGE_SCHEMA
                else _RANGE_DOCUMENT if isinstance(document, dict) and document.get("schema") == _RANGE_SCHEMA
                else _LEGACY_DOCUMENT)
    if (not isinstance(document, dict) or set(document) != required
            or document["schema"] not in _SCHEMA_VERSIONS):
        raise ValueError("invalid execution-stress document")
    stress_run = document["schema"] == _STRESS_SCHEMA
    if stress_run:
        StressSpec.from_document(document)      # shape, direction and statistic names, before any replay
    if _sha(document["results_pins"]) != document["results_pins_sha256"]:
        raise ValueError("results pins identity drift")
    pins = _load_json(document["results_pins"])
    scenarios = document["scenarios"]
    approved = _approved_scenarios(document["schema"], document.get("hedge"))
    # v7 compares the canonical JSON text, so 2.0 or true cannot stand in for 2 or 1; v1 to v6 compare as before.
    if scenarios != approved or (stress_run and json.dumps(scenarios, sort_keys=True)
                                 != json.dumps(approved, sort_keys=True)):
        raise ValueError("scenario values differ from approved contract")
    cohorts = _cohort_order(document)
    period = document.get("period_label")
    output = Path(document["output_root"])
    if output.exists():
        raise FileExistsError("fresh output root required: " + str(output))
    completed = []
    if stress_run:
        _require_clean_worktree(document["repository_root"])    # v7 also checks before any replay
    incomplete = {}
    shock_rows = []
    selected_contracts = {}
    source_indexes = {}
    run_documents = {}

    def collect(replay, cohort, packet, scenario, *, control):
        selected = sum(len(d.get("books", []))
                       for d in packet["result"]["decisions"])
        selected_contracts[cohort, packet["id"]] = _depth_proxy(
            document["schema"], packet["result"]["decisions"])
        try:
            result, daily = replay.run_method(packet, scenario)
            metrics = _metrics(result, daily, selected)
            if replay.per_method:
                metrics.update(CaseStatistics.of(result, daily))
                shock_rows.extend({"cohort": cohort, "method": packet["id"], "case": scenario["id"], **record}
                                  for record in replay.last_shocks)
        except ValueError as exc:
            if control:
                raise
            progress = replay.last_progress
            account = progress["account"]
            result, daily = None, list(progress["daily"])
            failure = _failure_state(progress, str(exc))
            incomplete[cohort, scenario["id"], packet["id"]] = {
                "failure": failure,
                "closed_trades": list(account.closed),
                "rejected": list(progress["rejected"]),
                "open_books": list(account.open.values())}
            metrics = _unsupported_metrics(selected, str(exc), progress["rejected"])
            if replay.per_method:
                metrics.update(dict.fromkeys(CaseStatistics.KEYS))
        dispositions = _dispositions(
            cohort, scenario["id"], packet["id"], result, daily,
            decisions=packet["result"]["decisions"],
            incomplete=incomplete.get((cohort, scenario["id"], packet["id"])))
        # Decisions are preserved in the pinned original result. Repeating its
        # large solver arrays in every sensitivity would add no new evidence.
        concise_result = ({k: v for k, v in result.items() if k != "decisions"}
                          if result is not None else None)
        completed.append((cohort, scenario["id"], packet["id"],
                          concise_result, daily, metrics, dispositions))
        print(cohort, scenario["id"], packet["id"], metrics["status"],
              "filled", metrics["filled"], "profit",
              round(metrics["final_profit_usd"], 2)
              if metrics["final_profit_usd"] is not None else "-",
              flush=True)

    # Gate 0: every method's control passes before any stressed case runs.
    # Open one cohort at a time so the 917 MB broad original is not duplicated.
    for cohort in cohorts:
        replay = FrozenMIOReplay(cohort, document, pins)
        try:
            if document["schema"] in _AVAILABLE_SIZE_SCHEMAS:
                run_documents[cohort] = replay.config
            source_indexes[cohort] = {
                symbol: {"sha256": descriptor["sha256"], "rows": descriptor["rows"]}
                for symbol, descriptor in replay.index_descriptors.items()}
            for packet in replay.original["results"]:
                collect(replay, cohort, packet, scenarios[0], control=True)
        finally:
            replay.close()
    for cohort in cohorts:
        replay = FrozenMIOReplay(cohort, document, pins)
        try:
            for scenario in scenarios[1:]:
                for packet in replay.original["results"]:
                    collect(replay, cohort, packet, scenario, control=False)
        finally:
            replay.close()
    output.mkdir(parents=True)
    summary, all_daily, all_attempts, all_dispositions = [], [], [], []
    for cohort, scenario, method, result, daily, metrics, dispositions in completed:
        folder = output / cohort / scenario
        folder.mkdir(parents=True, exist_ok=True)
        all_dispositions.extend(dispositions)
        _write_json(folder / (method + ".json"), {
            "cohort": cohort, "scenario": scenario, "method": method,
            "metrics": metrics, "result": result, "daily": daily,
            "decision_reference": {
                "path": str(FrozenMIOReplay._result_dir(document, cohort) / "result.json"),
                "sha256": document["cohorts"][cohort]["result_sha256"],
                "method": method},
            "dispositions": dispositions,
            "incomplete": incomplete.get((cohort, scenario, method))})
        (folder / (method + ".md")).write_text(
            _daybook(cohort, scenario, method, metrics, daily,
                     incomplete.get((cohort, scenario, method)),
                     selected_contracts[cohort, method], period))
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
    if stress_run:
        verdict = StressVerdict(document["criteria"]).rows(completed)
        _write_json(output / "shocks.json", shock_rows)
        _write_json(output / "verdict.json", {"schema": "mio-execution-stress-verdict/v1",
                                              "criteria": document["criteria"], "rows": verdict})
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
    proxy = _depth_proxy(document["schema"], []) is not None
    lines = ["# " + ("2019" if period is None else period)
             + " MIO fixed-selection execution and expiry stress", "",
             "Each daily row names decision-day possibilities and selected orders; fills are",
             "attempted at the next session's observed NBBO. Expiries show realized P&L.",
             *(("The scenarios are conditional on frozen future selections.", _PROXY_NOTE)
               if proxy else
               ("The seven scenarios are conditional on frozen future selections.",)),
             "See dispositions.csv for each attempted selected book, with quotes, fills,",
             "reserves, expiry payoff and cash impact; daily.csv gives every session.",
             "Unsupported cases stop at the failing session; later frozen choices are unattempted.", "",
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
    lines += ["", ("Open a daybook for all 252 daily opportunities, selections,"
                   if period is None else
                   "Open a daybook for every account session's opportunities, selections,"),
              "fills, expiries and losses. The matching JSON and daily.csv",
              "retain complete machine-readable rows.", "", "## Daybooks", ""]
    for row in summary:
        relative = row["cohort"] + "/" + row["scenario"] + "/" + row["method"] + ".md"
        lines.append("- [" + row["cohort"] + " / " + row["method"] + " / " +
                     row["scenario"] + "](" + relative + ")")
    lines.append("")
    if stress_run:
        lines += _verdict_section(verdict)
    (output / "report.md").write_text("\n".join(lines))
    repository_root = document["repository_root"]
    replay_commit = subprocess.check_output(
        ["git", "-C", repository_root, "rev-parse", "HEAD"], text=True).strip()
    _require_clean_worktree(repository_root)
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
                    for cohort in cohorts},
                "source_quote_indexes": source_indexes,
                "replay_code_sha256": _sha(Path(__file__)),
                "files": {str(path.relative_to(output)): {"sha256": _sha(path), "bytes": path.stat().st_size}
                          for path in sorted(output.rglob("*")) if path.is_file()}}
    if proxy:
        manifest["evidence_kind"] = _PROXY_NOTE
    if run_documents:
        manifest["original_run_documents"] = run_documents
    if period is not None:
        manifest["period_label"] = period
        manifest["cohort_bounds"] = {cohort: _range_bounds(document["cohorts"][cohort], v6=document["schema"] in _PACKET_SCHEMAS)
                                     for cohort in cohorts}
    _write_json(output / "pins.json", manifest)
    return output


def _verdict_section(rows):
    """Return the report's Verdict section: one line per (cohort, method) with its closed statistics."""
    def show(value):
        return "-" if value is None else f"{value:.4g}"

    lines = ["## Verdict", "",
             "Statuses come from the pre-registered criteria; any unsupported case is FAIL, and a PASS needs a",
             "criterion on every statistic. shocks.json lists every estimate behind the sized cases.", "",
             "| Cohort | Method | Status | Unsupported | " + " | ".join(STRESS_STATISTICS) + " |",
             "|---|---|---|---|" + "---:|" * len(STRESS_STATISTICS)]
    for row in rows:
        lines.append("| " + " | ".join([
            row["cohort"], row["method"], row["status"], ", ".join(row["unsupported"]) or "-",
            *(show(row["statistics"][name]["value"]) for name in STRESS_STATISTICS)]) + " |")
    lines.append("")
    return lines


def main(argv=None):
    """Run the pinned replay document supplied on the command line."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("document")
    args = parser.parse_args(argv)
    print(execute(args.document))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
