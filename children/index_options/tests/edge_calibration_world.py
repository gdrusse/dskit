"""A tiny synthetic world for the edge-calibration walk child tests (ADR-0311 stage 4 and Amendment 1 A4).

Everything is a pure function of (symbol, date, option), built in memory or in ``tmp_path``: no real data, no
forecast value of any run. ``World`` holds the inputs the child's two reading seams return, the pinned MIO
document and the pinned forecast files; ``edit`` poisons or deletes everything dated at or after a day.
"""

import copy
import hashlib
import json
import math
from datetime import date, timedelta
from pathlib import Path

Z = [-3 + .5 * i for i in range(13)]
LABELS = [str(i) for i in range(13)]
STRIKES = (90., 95., 100., 105., 110.)
SCALE = .05
BARS_FROM = "2019-08-05"
LAST = "2019-12-13"
#: Decision sessions of the default window: the first session of each ISO week from 2019-10-01.
WINDOW = {"start": "2019-10-01", "end": "2019-11-24"}

BRIDGE = {
    "symbol": "AAA", "start": "2019-10-01", "end_before": "2019-12-01", "protected_end_before": "2024-01-01",
    "fold": 1, "require_fitted": False, "z_grid": Z, "grid_labels": LABELS, "target_dte": 30, "dte_tolerance": 2,
    "max_abs_z": 2.5, "max_relative_spread": .5, "min_quote_size": 1, "stress_spread_fraction": .25, "rho": 0.,
    "projection": {"quadrature_tolerance": 1e-8, "payoff_tolerance": 1e-6, "cdf_tolerance": None,
                   "w1_tolerance": None, "integration_limit": 100},
    "solver": {"multiplier": 100, "fee_per_contract_usd": .65, "tie_tolerance_usd": 1e-6,
               "max_absolute_gap_usd": 1e-7, "max_relative_gap": 1e-8, "objective_parity_usd": 1e-5,
               "max_total_contracts": 10}}

STUDY_PARAMS = {
    "bridge": BRIDGE, "initial": 10000., "deposit": 1000., "deposit_every_days": 14, "weekly_fraction": .25,
    "correlation_lookback": 3, "correlation_min_pairs": 2, "correlation_threshold": .6,
    "solver": {"absolute_gap": 1e-7, "relative_gap": 0., "parity_tolerance": 1e-5,
               "solver_options": {"mip_abs_gap": 1e-8, "mip_rel_gap": 0., "threads": 1}},
    "methods": [{"id": "nominal", "rho": 0., "portfolio_fraction": 1., "name_fraction": 1.,
                 "component_fraction": None}]}


def sessions(end=LAST):
    day, out = date.fromisoformat(BARS_FROM), []
    while day <= date.fromisoformat(end):
        if day.weekday() < 5:
            out.append(day.isoformat())
        day += timedelta(days=1)
    return out


def friday_after(day, days):
    d = date.fromisoformat(day) + timedelta(days=days)
    return (d + timedelta(days=(4 - d.weekday()) % 7)).isoformat()


def weeks(cal, start, end):
    seen, out = set(), []
    for day in cal:
        if start <= day <= end:
            week = date.fromisoformat(day).isocalendar()[:2]
            if week not in seen:
                seen.add(week)
                out.append(day)
    return out


def factor_of(symbol, day, split):
    return float(split["factor"]) if split and symbol == split["symbol"] and day >= split["from"] else 1.


def close_of(symbol, i, day, split):
    return (100. + .35 * ((i % 7) - 3) + (3. if symbol == "BBB" else 0.)) * factor_of(symbol, day, split)


def model_mid(u, day, expiry, right, strike):
    dte = max((date.fromisoformat(expiry) - date.fromisoformat(day)).days, 0)
    intrinsic = max(u - strike, 0.) if right == "call" else max(strike - u, 0.)
    tv = 1.9 * math.sqrt(dte / 10.) * math.exp(-((strike - u) / 9.) ** 2) + .12
    return max(round(intrinsic + tv, 2), .15)


def quote(symbol, day, expiry, right, strike, u, factor):
    mid = model_mid(u, day, expiry, right, strike)
    half = round(.045 * mid + .01, 2)
    return {"symbol": symbol, "date": day, "expiration": expiry, "type": right, "strike_raw": strike,
            "strike": strike * factor, "bid": round((mid - half) * factor, 4), "ask": round((mid + half) * factor, 4),
            "bid_size": 10, "ask_size": 10, "basis_factor": factor, "underlying_price": u * factor}


def cdf_row(shift):
    return {"method_cdf_" + lab: .5 * (1 + math.erf((z - shift) / 2 ** .5)) for lab, z in zip(LABELS, Z)}


class World:
    """The synthetic inputs of one run and the files that pin them."""

    def __init__(self, symbols=("AAA",), split=None, last=LAST, window=None):
        self.symbols, self.split, self.last = tuple(symbols), split, last
        self.window = dict(window or WINDOW)
        self.cal = sessions(last)
        self.decisions = weeks(self.cal, self.window["start"], self.window["end"])
        self.market, self.forecasts = {}, {}
        for symbol in self.symbols:
            chain, bars, fs, cs = [], [], [], []
            for i, day in enumerate(self.cal):
                factor = factor_of(symbol, day, split)
                bars.append({"symbol": symbol, "date": day, "close": close_of(symbol, i, day, split),
                             "basis_factor": factor})
            closes = {b["date"]: b["close"] / b["basis_factor"] for b in bars}
            for day in self.decisions:
                for days in (8, 15):
                    expiry = friday_after(day, days)
                    if expiry > self.cal[-1]:
                        continue
                    identity = {"symbol": symbol, "quote_date": day, "expiry": expiry, "fold": 1, "band": "val"}
                    h = int(hashlib.sha256(f"{symbol}{day}".encode()).hexdigest()[:8], 16)
                    k = (h % 3 - 1) * .2
                    scale = (.05, .09)[(h >> 4) % 2]
                    fs.append({**identity, "fitted": False, **cdf_row(k)})
                    cs.append({**identity, "spot": closes[day] * factor_of(symbol, day, split),
                               "reference_scale": scale, "settlement_date": expiry})
            expiries = {r["expiry"] for r in fs}
            first = min(r["quote_date"] for r in fs)
            for day in self.cal:
                if day < first:
                    continue
                factor = factor_of(symbol, day, split)
                for expiry in sorted(expiries):
                    if day > expiry:
                        continue
                    for right in ("call", "put"):
                        for strike in STRIKES:
                            chain.append(quote(symbol, day, expiry, right, strike, closes[day], factor))
            self.market[symbol] = {"chain": chain, "bars": bars}
            self.forecasts[symbol] = {"forecasts": fs, "contexts": cs}

    # -- the pinned files ------------------------------------------------------------------

    def files(self, root):
        """Write the pinned MIO document and the forecast pin files under ``root``; return the config blocks."""
        root = Path(root)
        root.mkdir(parents=True, exist_ok=True)
        quarter = "2019Q4"
        forecast_sha = hashlib.sha256(b"synthetic forecasts " + quarter.encode()).hexdigest()
        context_sha = hashlib.sha256(b"synthetic contexts " + quarter.encode()).hexdigest()
        domain = {"params": {**copy.deepcopy(STUDY_PARAMS), "symbols": list(self.symbols)},
                  "pairs": [{"id": f"p{i}", "symbol": s, "dte_min": 1, "dte_max": 40}
                            for i, s in enumerate(self.symbols)],
                  "history_start": BARS_FROM,
                  "sources": {"forecasts": {"pin": {"files": {f"{quarter}/forecasts.parquet": forecast_sha,
                                                              f"{quarter}/contexts.parquet": context_sha}},
                                            "files": {"shared": f"{quarter}/forecasts.parquet"}}}}
        document = {"pipeline": {"plan": {"params": {"study": {"domain": domain}}}}}
        path = root / "mio-document.json"
        raw = (json.dumps(document, sort_keys=True) + "\n").encode()
        path.write_bytes(raw)
        self.document_path, self.document_sha = str(path), hashlib.sha256(raw).hexdigest()
        self.forecast_pins = {quarter: {"forecasts": forecast_sha, "contexts": context_sha}}
        return {"mio_document": {"path": self.document_path, "sha256": self.document_sha},
                "forecast_pins": copy.deepcopy(self.forecast_pins)}


def inputs_of(world, from_day=None, poison_from=None, delete_from=None, quote=None, bar=None, fill=None):
    """Return ``(market, forecasts)`` copies with everything dated at or after a day poisoned or deleted.

    ``quote`` maps ``(date, expiry, right, strike)`` to field overrides; ``bar`` maps a date to a close.
    """
    market, forecasts = copy.deepcopy(world.market), copy.deepcopy(world.forecasts)
    for symbol in market:
        chain, bars = market[symbol]["chain"], market[symbol]["bars"]
        fs, cs = forecasts[symbol]["forecasts"], forecasts[symbol]["contexts"]
        if delete_from is not None:
            chain[:] = [r for r in chain if r["date"] < delete_from]
            bars[:] = [r for r in bars if r["date"] < delete_from]
            fs[:] = [r for r in fs if r["quote_date"] < delete_from]
            cs[:] = [r for r in cs if r["quote_date"] < delete_from]
        if poison_from is not None:
            for r in chain:
                if r["date"] >= poison_from:
                    r["bid"], r["ask"] = 777., 778.
            for r in bars:
                if r["date"] >= poison_from:
                    r["close"] = 1e6
            for r in fs:
                if r["quote_date"] >= poison_from:
                    r.update(cdf_row(-2.5))
            for r in cs:
                if r["quote_date"] >= poison_from:
                    r["reference_scale"] = .5
        for r in chain:
            edit = (quote or {}).get((r["date"], r["expiration"], r["type"], r["strike_raw"]))
            if edit:
                r.update(edit)
        for r in bars:
            if bar and r["date"] in bar:
                r["close"] = bar[r["date"]]
    return market, forecasts
