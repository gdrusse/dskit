"""Synthetic onboarded stores for the stage B tests.

Everything here is offline: a scripted Kalshi venue behind the real ``kalshi`` pack,
real acquisitions into a tmp onboarding root, and Binance-shaped parquet day files
laid into a real ``localblobs`` stream (the same ``payload_files`` read path an
``httpblobs`` pull gives). Column names come from ``crypto_trading.binance_vision``
so a layout change there breaks these fixtures instead of passing silently.
"""

import io
import json
import math
import os
import random
from datetime import datetime, timedelta, timezone

from dskit.onboarding import OnboardingRoot, run_acquisition
from dskit.onboarding.libs.kalshi import KalshiConnector

from crypto_trading.binance_vision import BVOL_COLUMNS, KLINE_COLUMNS

CHILD_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIGS = os.path.join(CHILD_ROOT, "configs")
#: Strictly after every fixture instant (the pack drops candles that end after it).
NOW = datetime(2026, 10, 6, 0, 30, tzinfo=timezone.utc)
BAR_MS = 60_000
DAY_MS = 86_400_000


def utc(*args):
    """Return an aware UTC datetime."""
    return datetime(*args, tzinfo=timezone.utc)


def ms(moment):
    """Return epoch milliseconds of a whole-second aware datetime."""
    return int(moment.timestamp()) * 1000


def iso(moment):
    """Return the venue's ISO spelling of an instant."""
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def shipped(name):
    """Load a shipped config by file name."""
    with open(os.path.join(CONFIGS, name), encoding="utf-8") as handle:
        return json.load(handle)


# -- the scripted venue ---------------------------------------------------------


def market_payload(ticker, close, *, minutes_open=15, strike_type="greater_or_equal",
                   floor=None, cap=None, result="yes", status="finalized", **over):
    """One market payload in the shape the venue serves (no ``series_ticker``)."""
    payload = {
        "ticker": ticker, "event_ticker": ticker.rsplit("-", 1)[0],
        "strike_type": strike_type, "floor_strike": floor, "cap_strike": cap,
        "status": status, "result": result,
        "open_time": iso(close - timedelta(minutes=minutes_open)), "close_time": iso(close),
        "yes_sub_title": "t", "yes_bid_dollars": "0.0000", "yes_ask_dollars": "0.0000",
        "last_price_dollars": "0.0000",
    }
    payload.update(over)
    return payload


def candle_payload(end, bid=None, ask=None, price=None, volume="10.00", interest="99.00"):
    """One 1-minute candlestick payload; ``end`` is the END instant (aware datetime)."""
    def dollars(value):
        return {} if value is None else {"close_dollars": f"{value:.4f}"}

    out = {"end_period_ts": int(end.timestamp()), "volume_fp": volume,
           "open_interest_fp": interest, "yes_bid": dollars(bid), "yes_ask": dollars(ask),
           "price": {} if price is None else {
               "open_dollars": f"{price:.4f}", "high_dollars": f"{price:.4f}",
               "low_dollars": f"{price:.4f}", "close_dollars": f"{price:.4f}",
               "mean_dollars": f"{price:.4f}"}}
    return out


class ScriptedKalshi:
    """A ``getter(url, params) -> dict`` serving the given markets, candles and fees.

    ``fees`` maps a series to ``(fee_type, fee_multiplier)``; ``candles`` maps a ticker
    to its candlestick payloads. The series of a market is its ticker's first segment.
    """

    def __init__(self, markets=(), candles=None, fees=None):
        self.markets = list(markets)
        self.candles = dict(candles or {})
        self.fees = dict(fees or {})

    def __call__(self, url, params):
        path = url.split("/trade-api/v2", 1)[1]
        if path == "/markets":
            rows = [m for m in self.markets
                    if m["ticker"].split("-", 1)[0] == params["series_ticker"]]
            return {"markets": rows, "cursor": ""}
        if path.endswith("/candlesticks"):
            return {"candlesticks": self.candles.get(path.split("/")[-2], [])}
        if path.startswith("/series/"):
            series = path.rsplit("/", 1)[-1]
            fee_type, multiplier = self.fees.get(series, ("quadratic", 1))
            return {"series": {"ticker": series, "fee_type": fee_type,
                               "fee_multiplier": multiplier, "title": "t",
                               "category": "Crypto", "frequency": "fifteen_min"}}
        raise AssertionError(f"unscripted endpoint: {path}")


class Store:
    """A tmp onboarding root that scripted pulls and day-file blobs are acquired into."""

    def __init__(self, tmp_path, monkeypatch):
        self.monkeypatch = monkeypatch
        self.base = tmp_path
        tmp_path.mkdir(parents=True, exist_ok=True)
        self.onboarding = OnboardingRoot.create(str(tmp_path / "ob"))
        self.registry = self.onboarding.registry()
        self.path = self.onboarding.root

    def _register(self, source, connector, config):
        vid = self.registry.register("source_config", {
            "name": source, "catalog_source": source, "connector": connector,
            "config": config}, origin="test")
        self.registry.transition(vid, "active", origin="test")

    def kalshi(self, source, config_name, api, streams):
        """Register ``source`` from a shipped config and acquire each stream through ``api``."""
        connector = KalshiConnector(getter=api, sleeper=lambda seconds: None,
                                    clock=lambda: NOW)
        self._register(source, "kalshi", shipped(config_name))
        with self.monkeypatch.context() as patch:
            patch.setattr("dskit.onboarding.acquire.resolve_connector",
                          lambda ref: (lambda: connector))
            for stream in streams:
                run_acquisition(self.onboarding, self.registry, source, stream, "backfill")

    def blobs(self, source, files):
        """Lay ``{relpath: bytes}`` on disk and acquire it as stream ``files`` of ``source``."""
        directory = self.base / "blobs" / source
        for relpath, body in files.items():
            target = directory / relpath
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(body)
        self._register(source, "localblobs", {
            "path": str(directory), "as_of": "2026-10-06T00:00:00+00:00", "stream": "files"})
        run_acquisition(self.onboarding, self.registry, source, "files", "backfill")


# -- Binance-shaped day files ---------------------------------------------------


def walk(start_ms, count, *, price=60000.0, sigma=0.0005, seed=7):
    """Return ``count`` contiguous 1-minute bars as dicts, a seeded driftless walk."""
    rng = random.Random(seed)
    bars, last = [], price
    for i in range(count):
        opened = start_ms + i * BAR_MS
        close = last * math.exp(sigma * rng.gauss(0.0, 1.0))
        spread = 1.0 + 1e-4 * rng.random()
        bars.append({"open_time_ms": opened, "open": last, "close": close,
                     "high": max(last, close) * spread, "low": min(last, close) / spread,
                     "volume": 1.0 + i % 5, "close_time_ms": opened + BAR_MS - 1})
        last = close
    return bars


def _parquet(columns, schema_types):
    import pyarrow as pa
    import pyarrow.parquet as pq

    buffer = io.BytesIO()
    pq.write_table(pa.table(columns, schema=pa.schema(schema_types)), buffer)
    return buffer.getvalue()


def kline_days(bars):
    """Group bars by UTC open day into ``{"<day>.parquet": bytes}`` with the shipped layout."""
    import pyarrow as pa

    by_day = {}
    for bar in bars:
        day = (datetime.fromtimestamp(bar["open_time_ms"] / 1000, tz=timezone.utc)
               .strftime("%Y-%m-%d"))
        by_day.setdefault(day, []).append(bar)
    types = {"open_time_ms": pa.int64(), "close_time_ms": pa.int64(), "trades": pa.int64()}
    files = {}
    for day, rows in by_day.items():
        columns = {name: [r.get(name, 0.0) for r in rows] for name in KLINE_COLUMNS}
        columns["trades"] = [1] * len(rows)
        files[f"{day}.parquet"] = _parquet(
            columns, [(name, types.get(name, pa.float64())) for name in KLINE_COLUMNS])
    return files


def bvol_days(rows, symbol="BTCBVOLUSDT"):
    """Group ``(calc_time_ms, index_value)`` rows by UTC day into the shipped BVOL layout."""
    import pyarrow as pa

    by_day = {}
    for stamp, value in rows:
        day = (datetime.fromtimestamp(stamp / 1000, tz=timezone.utc).strftime("%Y-%m-%d"))
        by_day.setdefault(day, []).append((stamp, value))
    files = {}
    for day, group in by_day.items():
        columns = {"calc_time_ms": [s for s, _ in group], "symbol": [symbol] * len(group),
                   "base_asset": ["BTC"] * len(group), "quote_asset": ["USDT"] * len(group),
                   "index_value": [v for _, v in group]}
        types = {"calc_time_ms": pa.int64(), "symbol": pa.string(), "base_asset": pa.string(),
                 "quote_asset": pa.string(), "index_value": pa.float64()}
        files[f"{day}.parquet"] = _parquet(columns, [(n, types[n]) for n in BVOL_COLUMNS])
    return files
