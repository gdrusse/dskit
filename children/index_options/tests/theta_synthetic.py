"""A small synthetic world for running the ThetaData hypothesis documents end to end (ADR-0265 Amendment 5).

Nothing here is real data. A handful of names trade for a few hundred sessions; their option chains are priced by
Black-76 on known surfaces with earnings crushes planted on known sessions, so the REAL ``ChainDayFeatures`` builds
the panel the documents read, and the REAL ledger, builder, event detector and statistics nodes run on it. The
readers a document names (the Parquet stream and chain readers) are swapped for :class:`StaticRecords`, which
serves the synthetic rows through the SAME declared columns, date bounds and ISO conversion, so a wrong column
name or bound in a document fails here.
"""

import math
import random
from datetime import date, timedelta
from functools import lru_cache

from dskit.pipeline.libs.chain_features import ChainDayFeatures
from dskit.pipeline.node import Node, NodeContext
from dskit.pipeline.option_pricing import black76

NAMED = ["AAPL", "MSFT", "AMZN", "GOOGL", "NVDA"]
SYMBOLS = NAMED + [f"S{i:02d}" for i in range(11)]
INDEX = ["SPX", "SPXW", "XSP"]
WORLD = {}


def sessions(start, n):
    out, day = [], start
    while len(out) < n:
        if day.weekday() < 5:
            out.append(day)
        day += timedelta(days=1)
    return out


def third_friday(year, month):
    first = date(year, month, 1)
    return first + timedelta(days=(4 - first.weekday()) % 7 + 14)


def monthly(after, count):
    """The next ``count`` standard monthly expirations on or after ``after``."""
    out, y, m = [], after.year, after.month
    while len(out) < count:
        e = third_friday(y, m)
        if e >= after:
            out.append(e)
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


class StaticRecords(Node):
    """Serve synthetic rows through a reader's declared projection (test double of the Parquet readers)."""

    role = "data"
    outputs = ("records",)
    _PARAMS = ("dataset", "declared")

    @classmethod
    def validate_params(cls, params):
        return [] if set(params) <= set(cls._PARAMS) else ["unknown knob"]

    def run(self, ctx, inputs):
        rows = WORLD["data"][self.params["dataset"]]
        spec = self.params["declared"]
        if spec["kind"] == "stream":
            out = []
            for source in spec["sources"]:
                if source["dataset_part"] != 0:
                    continue
                columns, bounds = source["columns"], source["read_bounds"]["date"]
                iso = set(source.get("iso_fields", ()))
                for row in rows:
                    day = row["date"] if isinstance(row["date"], str) else row["date"].isoformat()
                    if bounds.get("start", "0") <= day < bounds["end_before"]:
                        projected = {field: row[column] for column, field in columns.items()}
                        out.append({k: (v.isoformat() if k in iso and hasattr(v, "isoformat") else v) for k, v in projected.items()})
            return {"records": out}
        symbols, fields = set(spec["symbols"]), spec["fields"]
        if not symbols & WORLD["symbols"][self.params["dataset"]]:
            raise ValueError(f"no declared root is in the data: {sorted(symbols)[:3]}")
        out = [{f: r[f] for f in fields} for r in rows
               if r["symbol"] in symbols and spec["start"] <= r["date"] < spec["end_before"]]
        return {"records": out}


def _gauss(rng):
    return rng.gauss(0.0, 1.0)


def _base_vol(i):
    return 0.22 + 0.02 * (i % 6)


def _build_stock_world(year, rng):
    dates = sessions(date(year, 2, 1), 170)
    back = sessions(dates[0] - timedelta(days=130), 90)
    path_dates = sorted(set(back) | set(dates) | set(sessions(dates[-1] + timedelta(days=1), 45)))
    events = {}
    for i, sym in enumerate(SYMBOLS):
        if sym == "AAPL" and year == 2018:
            events[sym] = [date(2018, 2, 2), date(2018, 5, 2), date(2018, 8, 1)]
        else:
            events[sym] = [dates[30 + 3 * i + 55 * k] for k in range(2)]
    spot, closes = {}, {}
    for i, sym in enumerate(SYMBOLS):
        base = 100.0 + 25 * (i % 4)
        level, series = 0.0, {}
        for d in path_dates:
            shock = 0.03 if d in events[sym] else 0.0
            move = _base_vol(i) / math.sqrt(252) * _gauss(rng) + (shock * rng.choice([-1, 1]) if shock else 0.0)
            level = max(-0.2, min(0.2, 0.9 * level + move))
            series[d] = base * math.exp(level)
        spot[sym] = series
        closes[sym] = base
    return dates, back, path_dates, events, spot, closes


def _surface(sym_index, sym, d, exp, events, dates, rng_noise):
    """Base vol, ramped up over the five sessions before an announcement (front expiries only); the event day is back at base."""
    mult = 1.0
    if (exp - d).days <= 45:
        for ev in events[sym]:
            at = dates.index(ev) if ev in dates else None
            if at is not None and d in dates[max(at - 5, 0):at]:
                mult = 1.0 + 0.09 * (dates[max(at - 5, 0):at].index(d) + 1)
    return _base_vol(sym_index) * mult * (1.0 + rng_noise)


def _chain_row(sym, d, exp, k, right, vol, spot, half, oi):
    years = max((exp - d).days, 0.001) / 365
    mid = black76(right, spot, k, vol * (1.0 + 0.4 * abs(math.log(k / spot)) * (1.0 if right == "put" else 0.5)), years)
    return {"symbol": sym, "date": d.isoformat(), "expiration": exp.isoformat(), "strike": k, "right": right,
            "bid": max(mid - half, 0.01), "ask": mid + half, "volume": 5, "open_interest": oi, "underlying_price": spot}


def build(year=2018, seed=7):
    """Build (and cache) the world of ``year``: rows for every dataset a document may read."""
    if year in _CACHE:
        return _CACHE[year]
    rng = random.Random(seed + year)
    dates, back, path_dates, events, spot, base_close = _build_stock_world(year, rng)
    chain, bars = [], []
    for i, sym in enumerate(SYMBOLS):
        step = base_close[sym] * 0.05
        grid = [round(base_close[sym] * 0.75 + step * j, 2) for j in range(11)]
        for d in path_dates:
            px = spot[sym][d]
            bars.append({"symbol": sym, "date": d.isoformat(), "open": px, "high": px, "low": px, "close": px,
                         "adjusted_close": px * 0.99, "volume": 1000})
        for d in dates:
            px = spot[sym][d]
            fridays = [d + timedelta(days=(4 - d.weekday()) % 7 + 7 * w) for w in range(1, 7)]
            expiries = sorted(set(monthly(d, 4) + fridays))
            for exp in expiries:
                vol = _surface(i, sym, d, exp, events, dates, 0.01 * _gauss(rng))
                for k in grid:
                    oi_peak = 5000 if abs(k - round(px / step) * step) < step * 1.01 else 100
                    for right in ("call", "put"):
                        chain.append(_chain_row(sym, d, exp, k, right, vol, px, 0.01 + 0.002 * (i % 5), oi_peak + rng.randint(0, 50)))
    return _finish(year, dates, bars, chain, rng)


def _finish(year, dates, bars, chain, rng):
    params = _panel_features_params()
    rows = ChainDayFeatures("features", params).run(NodeContext(name="t", asof="2025-12-31", run_dir="/nonexistent"), {"records": chain})["records"]
    panel = []
    for r in rows:
        i = SYMBOLS.index(r["symbol"])
        panel.append({**r, "date": date.fromisoformat(r["date"]), "expiration": date.fromisoformat(r["expiration"]) if r.get("expiration") else None,
                      "first_quote_date": dates[0], "after_first_quote": True, "liquidity_tercile": i % 3,
                      "median_quoted_development": 100.0 + i})
    spy = []
    level = 0.0
    for d in sorted({date.fromisoformat(b["date"]) for b in bars}):
        level = 0.9 * level + 0.01 * _gauss(rng)
        px = 300.0 * math.exp(max(-0.2, min(0.2, level)))
        spy.append({"symbol": "SPY", "date": d.isoformat(), "open": px, "high": px, "low": px, "close": px, "adjusted_close": px, "volume": 1})
    index_panel = [{"symbol": "SPX", "date": d, "target_dte": 30, "status": "ok", "atm_iv": 0.12 + 0.02 * abs(math.sin(n / 9))}
                   for n, d in enumerate(dates)]
    index_chain = _index_chain(dates, rng)
    world = {"dates": dates, "data": {"panel": panel, "index_panel": index_panel, "bars": bars, "bench": spy,
                                       "stock_chain": chain, "index_chain": index_chain},
             "symbols": {"stock_chain": set(SYMBOLS), "index_chain": set(INDEX)}}
    _CACHE[year] = world
    return world


def _index_chain(dates, rng):
    rows, level = [], 0.0
    for n, d in enumerate(dates):
        level = 0.9 * level + 0.006 * _gauss(rng)
        px = 2800.0 * math.exp(max(-0.15, min(0.15, level)))
        nxt = [x for x in dates if x > d][:4]
        for sym in INDEX:
            exps = monthly(d, 3) if sym == "SPX" else sorted(set(nxt + monthly(d, 3)))
            for exp in exps:
                for j in range(21):
                    k = round(2800.0 * (0.8 + 0.02 * j), 1)
                    for right in ("call", "put"):
                        rows.append(_chain_row(sym, d, exp, k, right, 0.15, px, 0.15, 10))
    rows.sort(key=lambda r: (INDEX.index(r["symbol"]), r["date"]))
    return rows


def _panel_features_params():
    import json
    from pathlib import Path
    doc = json.loads((Path(__file__).resolve().parents[1] / "configs" / "run-theta-chain-panel.json").read_text())
    params = dict(doc["pipeline"]["panel"]["params"]["features"])
    for key in ("rate_date_field", "rate_value_field", "rate_max_age_days", "rate_scale", "max_symbol_rows"):
        params.pop(key, None)
    params.update(rate=0.0, min_forward_pairs=3, end_before="2026-01-01")
    return params


_CACHE = {}
