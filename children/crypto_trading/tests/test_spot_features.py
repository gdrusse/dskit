"""Point-in-time spot and volatility features: nothing at or after the decision instant.

The leak tests plant a spike in every datum the rule must exclude and assert the
features do not move; a control plants the same spike in the last LEGAL bar and
asserts they do, so the test is known to have teeth. Expected values are restated
here in plain Python from the fixture bars.
"""

import math

import pytest
from synthetic import Store, bvol_days, kline_days, ms, utc, walk

from crypto_trading.day_series import StreamManifests
from crypto_trading.spot_features import SpotFeatures

START = ms(utc(2026, 9, 1, 22, 0))
BARS = 360  # 22:00 on the 1st to 04:00 on the 2nd
DECISION = ms(utc(2026, 9, 2, 1, 0))
SQRT_BAR_S = math.sqrt(60.0)

STREAMS = {
    "BTC_klines": {"source": "btc-1m", "stream": "files"},
    "BTC_bvol": {"source": "btc-bvol", "stream": "files"},
    "ETH_klines": {"source": "eth-1m", "stream": "files"},
    "ETH_bvol": {"source": "eth-bvol", "stream": "files"},
}


def params(root, **over):
    base = {
        "root": root,
        "assets": {
            "BTC": {"series": ["KXBTC15M"], "klines": STREAMS["BTC_klines"], "bvol": STREAMS["BTC_bvol"]},
            "ETH": {"series": ["KXETH15M"], "klines": STREAMS["ETH_klines"], "bvol": STREAMS["ETH_bvol"]},
        },
        "columns": {
            "klines": {"open_time": "open_time_ms", "close_time": "close_time_ms", "high": "high",
                       "low": "low", "close": "close"},
            "bvol": {"time": "calc_time_ms", "value": "index_value"},
        },
        "day_relpath_template": "{day}.parquet",
        "bar_ms": 60_000,
        "estimators": [{"kind": "rms", "window": 30}, {"kind": "ewma", "half_life": 10, "lookback": 60},
                       {"kind": "high_low", "window": 30}],
        "max_spot_age_ms": 120_000,
        "max_bvol_age_ms": 120_000,
        "bvol_scale": 0.01,
        "seconds_per_year": 31_536_000,
    }
    base.update(over)
    return base


def bvol_rows(center=DECISION, seconds=120, level=50.0):
    return [(center + 1000 * k, level + 0.01 * k) for k in range(-seconds, seconds + 1)]


def world(tmp_path, monkeypatch, btc=None, eth=None, bvol=None):
    store = Store(tmp_path, monkeypatch)
    btc = btc if btc is not None else walk(START, BARS, price=60000.0, seed=1)
    eth = eth if eth is not None else walk(START, BARS, price=3000.0, seed=2)
    rows = bvol if bvol is not None else bvol_rows()
    store.blobs("btc-1m", kline_days(btc))
    store.blobs("eth-1m", kline_days(eth))
    store.blobs("btc-bvol", bvol_days(rows))
    store.blobs("eth-bvol", bvol_days(rows, symbol="ETHBVOLUSDT"))
    store.bars = {"BTC": btc, "ETH": eth}
    return store


def decision_row(decision=DECISION, series="KXBTC15M", payoff="above", floor=60000.0, cap=None, **over):
    row = {"ticker": f"{series}-X", "series": series, "payoff": payoff, "floor_strike": floor,
           "cap_strike": cap, "decision_ms": decision, "close_ms": decision + 300_000,
           "lead_minutes": 5, "tau_s": 300.0, "label": 1}
    row.update(over)
    return row


def spot(store, rows, **over):
    manifests = StreamManifests("manifests", {"root": store.path, "streams": STREAMS}).run(
        None, {})["manifests"]
    node = SpotFeatures("spot", params(store.path, **over))
    return node.run(None, {"records": rows, "manifests": manifests})


def one(store, **kw):
    return spot(store, [decision_row(**kw)])["records"][0]


# -- restated expectations ----------------------------------------------------------


def last_closed(bars, decision):
    """Index of the last bar whose CLOSE time is strictly before the decision."""
    return max(i for i, b in enumerate(bars) if b["close_time_ms"] < decision)


def log_returns(bars, i, count):
    return [math.log(bars[j]["close"] / bars[j - 1]["close"]) for j in range(i - count + 1, i + 1)]


def rms_per_sqrt_s(bars, i, window):
    return math.sqrt(sum(r * r for r in log_returns(bars, i, window)) / window) / SQRT_BAR_S


def ewma_per_sqrt_s(bars, i, half_life, lookback):
    lam = 0.5 ** (1.0 / half_life)
    rets = log_returns(bars, i, lookback)[::-1]  # newest first
    weights = [lam**k for k in range(lookback)]
    return math.sqrt(sum(w * r * r for w, r in zip(weights, rets)) / sum(weights)) / SQRT_BAR_S


def parkinson_per_sqrt_s(bars, i, window):
    total = sum(math.log(bars[j]["high"] / bars[j]["low"]) ** 2 / (4.0 * math.log(2.0))
                for j in range(i - window + 1, i + 1))
    return math.sqrt(total / window) / SQRT_BAR_S


def test_spot_is_the_close_of_the_last_bar_that_had_closed_by_the_decision(tmp_path, monkeypatch):
    store = world(tmp_path, monkeypatch)
    out = one(store)
    bars = store.bars["BTC"]
    i = last_closed(bars, DECISION)
    assert bars[i]["open_time_ms"] == DECISION - 60_000, "the 00:59 bar, closed at 00:59:59.999"
    assert out["spot"] == bars[i]["close"]
    assert out["spot_age_ms"] == 1 and out["spot_missing"] is False


def test_the_volatility_columns_equal_the_hand_computed_per_second_values(tmp_path, monkeypatch):
    store = world(tmp_path, monkeypatch)
    out = one(store)
    bars, i = store.bars["BTC"], last_closed(store.bars["BTC"], DECISION)
    assert out["rv_rms_30"] == pytest.approx(rms_per_sqrt_s(bars, i, 30), rel=1e-9)
    assert out["rv_ewma_10"] == pytest.approx(ewma_per_sqrt_s(bars, i, 10, 60), rel=1e-9)
    assert out["rv_hl_30"] == pytest.approx(parkinson_per_sqrt_s(bars, i, 30), rel=1e-9)


def test_a_decision_inside_a_minute_still_excludes_the_bar_that_is_forming(tmp_path, monkeypatch):
    # Bars are labelled by their START: at 01:00:30 the 01:00 bar has opened but is not closed.
    store = world(tmp_path, monkeypatch)
    decision = DECISION + 30_000
    out = one(store, decision=decision)
    bars = store.bars["BTC"]
    assert last_closed(bars, decision) == last_closed(bars, DECISION)
    assert out["spot"] == bars[last_closed(bars, DECISION)]["close"]
    assert out["spot_age_ms"] == 30_001


def spiked(bars, decision, factor=10.0):
    """The same bars with every bar that CLOSES at or after ``decision`` replaced by a spike."""
    out = []
    for bar in bars:
        if bar["close_time_ms"] >= decision:
            bar = {**bar, "close": bar["close"] * factor, "high": bar["high"] * factor,
                   "low": bar["low"], "open": bar["open"] * factor}
        out.append(bar)
    return out


@pytest.mark.parametrize("offset_ms", [0, 30_000, 59_999])
def test_a_spike_at_or_after_the_decision_never_reaches_a_feature(tmp_path, monkeypatch, offset_ms):
    decision = DECISION + offset_ms
    clean = world(tmp_path / "clean", monkeypatch)
    dirty = world(tmp_path / "dirty", monkeypatch,
                  btc=spiked(clean.bars["BTC"], decision), eth=spiked(clean.bars["ETH"], decision))
    a, b = one(clean, decision=decision), one(dirty, decision=decision)
    keys = ("spot", "spot_age_ms", "rv_rms_30", "rv_ewma_10", "rv_hl_30", "log_moneyness")
    assert {k: a[k] for k in keys} == {k: b[k] for k in keys}
    assert a["rv_rms_30"] is not None


def test_control_the_same_spike_one_bar_earlier_does_move_the_features(tmp_path, monkeypatch):
    decision = DECISION
    clean = world(tmp_path / "clean", monkeypatch)
    dirty = world(tmp_path / "dirty", monkeypatch,
                  btc=spiked(clean.bars["BTC"], decision - 60_000))  # the last legal bar is spiked
    a, b = one(clean), one(dirty)
    assert a["spot"] != b["spot"] and a["rv_rms_30"] != b["rv_rms_30"]
    assert a["rv_hl_30"] != b["rv_hl_30"]


def test_bvol_is_the_last_second_strictly_before_the_decision(tmp_path, monkeypatch):
    rows = [(t, v) for t, v in bvol_rows()]
    store = world(tmp_path, monkeypatch, bvol=rows)
    out = one(store)
    expected = dict(rows)[DECISION - 1000]  # the row AT the decision instant is excluded
    assert out["bvol_iv"] == pytest.approx(expected * 0.01)
    assert out["bvol_age_ms"] == 1000 and out["bvol_missing"] is False
    assert out["bvol_per_sqrt_s"] == pytest.approx(expected * 0.01 / math.sqrt(31_536_000))


def test_a_bvol_spike_at_or_after_the_decision_never_reaches_the_feature(tmp_path, monkeypatch):
    clean_rows = bvol_rows()
    dirty_rows = [(t, 9999.0 if t >= DECISION else v) for t, v in clean_rows]
    a = one(world(tmp_path / "a", monkeypatch, bvol=clean_rows))
    b = one(world(tmp_path / "b", monkeypatch, bvol=dirty_rows))
    assert (a["bvol_iv"], a["bvol_age_ms"]) == (b["bvol_iv"], b["bvol_age_ms"])


def test_log_moneyness_follows_the_payoff_geometry(tmp_path, monkeypatch):
    store = world(tmp_path, monkeypatch)
    s = store.bars["BTC"][last_closed(store.bars["BTC"], DECISION)]["close"]
    above = one(store, payoff="above", floor=s * math.exp(0.01), cap=None)
    assert above["log_moneyness"] == pytest.approx(0.01)
    assert above["ln_floor_over_spot"] == pytest.approx(0.01) and above["ln_cap_over_spot"] is None
    below = one(store, payoff="below", floor=None, cap=s * math.exp(-0.02))
    assert below["log_moneyness"] == pytest.approx(-0.02)
    assert below["ln_cap_over_spot"] == pytest.approx(-0.02) and below["ln_floor_over_spot"] is None
    between = one(store, payoff="between", floor=s * math.exp(-0.01), cap=s * math.exp(0.03))
    assert between["log_moneyness"] is None, "two strikes have no single moneyness"
    assert between["ln_floor_over_spot"] == pytest.approx(-0.01)
    assert between["ln_cap_over_spot"] == pytest.approx(0.03)


def test_each_asset_reads_its_own_tape(tmp_path, monkeypatch):
    store = world(tmp_path, monkeypatch)
    out = spot(store, [decision_row(), decision_row(series="KXETH15M", floor=3000.0)])["records"]
    assert out[0]["spot"] > 50_000 and out[1]["spot"] < 4_000
    assert out[1]["spot"] == store.bars["ETH"][last_closed(store.bars["ETH"], DECISION)]["close"]


def test_output_follows_input_order_and_does_not_mutate_the_input(tmp_path, monkeypatch):
    store = world(tmp_path, monkeypatch)
    rows = [decision_row(decision=DECISION + 60_000 * k, ticker=f"T{k}") for k in (5, 1, 3)]
    before = [dict(r) for r in rows]
    out = spot(store, rows)["records"]
    assert [r["ticker"] for r in out] == ["T5", "T1", "T3"]
    assert rows == before


def test_a_window_reaching_back_across_midnight_reads_the_previous_day(tmp_path, monkeypatch):
    store = world(tmp_path, monkeypatch)
    decision = ms(utc(2026, 9, 2, 0, 10))  # a 30-bar window needs bars from 23:40 on the 1st
    bars = store.bars["BTC"]
    out = one(store, decision=decision)
    assert out["rv_rms_30"] == pytest.approx(rms_per_sqrt_s(bars, last_closed(bars, decision), 30))


def test_a_missing_minute_inside_a_window_blanks_that_feature_only(tmp_path, monkeypatch):
    bars = walk(START, BARS, seed=1)
    gapped = [b for b in bars if b["open_time_ms"] != DECISION - 10 * 60_000]
    store = world(tmp_path, monkeypatch, btc=gapped)
    out = one(store)
    assert out["spot"] is not None
    assert out["rv_rms_30"] is None and out["rv_hl_30"] is None and out["rv_ewma_10"] is None


def test_stale_data_is_missing_never_carried_forward(tmp_path, monkeypatch):
    store = world(tmp_path, monkeypatch, btc=walk(START, 200, seed=1))  # tape ends 01:20 on the 2nd
    late = one(store, decision=ms(utc(2026, 9, 2, 3, 0)))
    assert late["spot"] is None and late["spot_missing"] is True and late["spot_age_ms"] is None
    assert late["rv_rms_30"] is None and late["log_moneyness"] is None
    assert late["bvol_missing"] is True and late["bvol_iv"] is None and late["bvol_per_sqrt_s"] is None


def test_an_unknown_series_is_refused_by_name(tmp_path, monkeypatch):
    store = world(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="KXSOL15M"):
        spot(store, [decision_row(series="KXSOL15M")])


def test_the_run_refuses_a_store_that_moved_since_the_manifest_was_fingerprinted(tmp_path, monkeypatch):
    store = world(tmp_path, monkeypatch)
    manifests = StreamManifests("m", {"root": store.path, "streams": STREAMS}).run(None, {})["manifests"]
    manifests["BTC_klines"] = {**manifests["BTC_klines"], "manifest_sha256": "0" * 64}
    node = SpotFeatures("spot", params(store.path))
    with pytest.raises(ValueError, match="BTC_klines.*moved"):
        node.run(None, {"records": [decision_row()], "manifests": manifests})


def test_default_deny_required_params_and_the_serving_class(tmp_path):
    good = params("/r")
    with pytest.raises(Exception, match="surprise"):
        SpotFeatures("spot", {**good, "surprise": 1})
    for knob in good:
        with pytest.raises(Exception, match=knob):
            SpotFeatures("spot", {k: v for k, v in good.items() if k != knob})
    with pytest.raises(Exception, match="estimators"):
        SpotFeatures("spot", {**good, "estimators": [{"kind": "mystery"}]})
    with pytest.raises(Exception, match="series"):
        SpotFeatures("spot", {**good, "assets": {
            "BTC": good["assets"]["BTC"], "ETH": {**good["assets"]["ETH"], "series": ["KXBTC15M"]}}})
    assert SpotFeatures.serving_effect({}, {}) == "forbidden", "Binance data is research-only"
