"""A world with zero edge must score zero edge, through the real nodes.

An efficient market quotes P(YES | everything known at that minute's end) from the true volatility. The fair
value sees the same path through Binance bars and the same minute. If the pipeline ever compares a fair value
computed at one instant with a quote from another (a candle a minute older than the spot), the fair value
looks like it beats the market and a naive take rule looks profitable: a timing artefact, not an edge. This
test generates a seeded continuous price path, derives bars, strikes, labels and efficient quotes from it, and
runs decision rows, anchors, spot features, market state, fair value, fees and the scorer. A control makes the
quotes one minute stale and must show the spurious edge, so the test is known to have power.
"""

import math

import numpy as np
from dskit.pipeline.binary_pricing import AveragedLognormal, BinaryFairValue
from dskit.pipeline.binary_scoring import BucketedBinaryScore
from dskit.pipeline.libs.parquet_series import StreamManifests
from synthetic import Store, bvol_days, kline_days, ms, shipped, utc
from test_spot_features import params as spot_params

from crypto_trading.anchors import StrikeAnchors
from crypto_trading.decisions import DecisionRows
from crypto_trading.fees import FeeColumns
from crypto_trading.market_state import MarketState
from crypto_trading.spot_features import SpotFeatures

SIGMA = 0.0045 / math.sqrt(15 * 60)  # log-return sd per sqrt(second): 0.45% over a 15-minute market
HISTORY_S = 6 * 3600
MARKET_S = 900
LAG_S = 30  # the strike's publication lag
EXEC_LAG_S = 5
START = ms(utc(2026, 9, 1, 0, 0))
N_MARKETS = 800


class Ctx:
    def __init__(self, run_dir):
        self.run_dir = str(run_dir)


def path(seed, n):
    rng = np.random.default_rng(seed)
    steps = SIGMA * rng.standard_normal(HISTORY_S + n * MARKET_S)
    return 60000.0 * np.exp(np.cumsum(steps))


def bars_of(p):
    """1-minute bars of a per-second price path: open at the first second, close at the last."""
    minutes = p.size // 60
    grid = p[: minutes * 60].reshape(minutes, 60)
    return [{"open_time_ms": START + 60_000 * m, "open": float(grid[m, 0]), "close": float(grid[m, -1]),
             "high": float(grid[m].max()), "low": float(grid[m].min()), "volume": 1.0,
             "close_time_ms": START + 60_000 * m + 59_999} for m in range(minutes)]


def efficient_mid(price, strike, tau):
    """What an efficient market quotes with exactly the information in ``price`` and ``tau`` seconds to go."""
    if tau < 60:
        return 0.5
    return AveragedLognormal(SIGMA, tau, 60.0).survival(price, strike)


def world(n, seed, stale_s=0):
    """Markets, candles (quotes ``stale_s`` seconds older than their stamp) and the bar files of one path."""
    p = path(seed, n)
    markets, candles = [], []
    for i in range(n):
        opened = HISTORY_S + MARKET_S * i
        closed = opened + MARKET_S
        strike = float(p[opened - 60:opened].mean())
        settle = float(p[closed - 60:closed].mean())
        ticker = f"KXBTC15M-{i:04d}"
        markets.append({
            "ticker": ticker, "event_ticker": ticker, "series": "KXBTC15M", "strike_type": "greater_or_equal",
            "payoff": "above", "floor_strike": strike, "cap_strike": None,
            "open_ms": START + 1000 * opened, "close_ms": START + 1000 * closed,
            "strike_known_ms": START + 1000 * (opened + LAG_S), "label": int(settle >= strike)})
        for j in range(1, 16):
            end = opened + 60 * j
            known = end - stale_s
            mid = min(max(efficient_mid(float(p[known - 1]), strike, closed - known), 0.03), 0.97)
            candles.append({"ticker": ticker, "end_ms": START + 1000 * end, "yes_bid": mid - 0.01,
                            "yes_ask": mid + 0.01, "price": mid, "volume": 1.0, "open_interest": 1.0})
    return p, markets, candles


def score(tmp_path, monkeypatch, n, seed, stale_s):
    p, markets, candles = world(n, seed, stale_s)
    store = Store(tmp_path, monkeypatch)
    files = kline_days(bars_of(p))
    tape_rows = [(START + 1000, 50.0)]
    store.blobs("btc-1m", files)
    store.blobs("btc-bvol", bvol_days(tape_rows))
    store.blobs("eth-1m", files)
    store.blobs("eth-bvol", bvol_days(tape_rows, symbol="ETHBVOLUSDT"))
    streams = {"BTC_klines": {"source": "btc-1m", "stream": "files"}, "BTC_bvol": {"source": "btc-bvol", "stream": "files"},
               "ETH_klines": {"source": "eth-1m", "stream": "files"}, "ETH_bvol": {"source": "eth-bvol", "stream": "files"}}
    manifests = StreamManifests("m", {"root": store.path, "streams": streams}).run(None, {})["manifests"]
    anchors = StrikeAnchors("a", {"anchor_series": ["KXBTC15M"]}).run(None, {"records": markets})["records"]
    rows = DecisionRows("d", {"leads_minutes": [2, 5, 10], "exec_lag_s": EXEC_LAG_S}).run(
        None, {"records": markets})["records"]
    spot = SpotFeatures("s", spot_params(store.path, estimators=[{"kind": "rms", "window": 60}])).run(
        None, {"records": rows, "manifests": manifests, "anchors": anchors})["records"]
    state = MarketState("q", {"max_candle_age_ms": 120_000, "quote_floor": 0.0, "quote_ceiling": 1.0}).run(
        None, {"records": spot, "candles": candles})["records"]
    fair = BinaryFairValue("f", {
        "vol_field": "rv_rms_60", "spot_field": "spot_brti", "fair_field": "fair", "averaging_window_s": 60,
        "payoff_field": "payoff", "lower_field": "floor_strike", "upper_field": "cap_strike",
        "decision_field": "decision_ms", "settle_field": "close_ms", "exec_lag_s": EXEC_LAG_S,
    }).run(None, {"records": state})["records"]
    schedule = [{"series": "KXBTC15M", "fee_type": "quadratic", "fee_multiplier": 1.0,
                 "retrieved": "2026-10-06T00:00:00+00:00", "retrieved_ms": 1}]
    fee_types = shipped("run-features-15m.json")["pipeline"]["fees"]["params"]["fee_types"]  # the SHIPPED Kalshi mapping
    priced = FeeColumns("fees", {"fee_types": fee_types, "contracts": 100}).run(
        None, {"records": fair, "schedules": schedule})["records"]
    out = BucketedBinaryScore("kill", {
        "model_fields": ["fair"], "market_field": "mid", "label_field": "label", "settle_field": "close_ms",
        "bid_field": "yes_bid", "ask_field": "yes_ask", "fee_yes_field": "fee_buy_yes",
        "fee_no_field": "fee_buy_no", "eligible_field": "two_sided",
        "bucket_edges": [0.0, 1.0], "margin": 0.02, "by": ["lead_minutes"],
        "cluster_field": "event_ticker", "cluster_block_s": 86_400,
        "segments": {"all": {"start_ms": 0}}, "report_segments": ["all"],
    }).run(Ctx(tmp_path), {"records": priced})
    return priced, {(s["group"], s["bucket"]): s for s in out["scores"]}


def test_the_fixture_is_a_sane_world(tmp_path, monkeypatch):
    priced, cells = score(tmp_path, monkeypatch, 200, seed=1, stale_s=0)
    assert len(priced) == 600 and all(r["spot_brti"] is not None and r["two_sided"] for r in priced)
    base = sum(r["label"] for r in priced) / len(priced)
    assert 0.35 < base < 0.65, "an up/down strike set at the open: about half settle yes"
    assert all(abs(r["candle_age_ms"] - r["spot_age_ms"]) <= 5_000 for r in priced), (
        "spot and quote carry ONE information instant: neither is a minute older than the other")


def test_a_world_with_no_edge_shows_no_edge_over_the_market_and_no_profit_from_the_take_rule(tmp_path, monkeypatch):
    priced, cells = score(tmp_path, monkeypatch, N_MARKETS, seed=3, stale_s=0)
    for group in ("all", "lead_minutes=2", "lead_minutes=5", "lead_minutes=10"):
        cell = cells[(group, "all")]
        assert cell["brier_diff"] > -3.0 * cell["brier_diff_se"], (
            f"{group}: the fair value beat an efficient market by {cell['brier_diff']:.4f}: a timing artefact")
        if cell["n_trades"] > 30:
            assert cell["pnl_mean"] < 3.0 * cell["pnl_se"], f"{group}: a naive take rule must not profit from nothing"
    pooled = cells[("all", "all")]
    assert abs(pooled["mean_model"] - pooled["base_rate"]) < 0.05, "calibrated in the large: no unit or basis bias"


def test_control_a_stale_quote_manufactures_exactly_the_edge_the_test_guards_against(tmp_path, monkeypatch):
    priced, cells = score(tmp_path, monkeypatch, N_MARKETS, seed=3, stale_s=60)
    pooled = cells[("all", "all")]
    assert pooled["brier_diff"] < -3.0 * pooled["brier_diff_se"], "so the test above is not passing vacuously"
