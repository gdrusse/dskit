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
from crypto_trading.fair_value import FairValue
from crypto_trading.spot_features import SpotFeatures

START = ms(utc(2026, 9, 1, 22, 0))
BARS = 360  # 22:00 on the 1st to 04:00 on the 2nd
DECISION = ms(utc(2026, 9, 2, 1, 0))
SQRT_BAR_S = math.sqrt(60.0)

ASSET_OF = {"KXBTC15M": "BTC", "KXBTC": "BTC", "KXETH15M": "ETH"}
LAG_MS = 30_000
BAR = 60_000

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
            "klines": {"open_time": "open_time_ms", "close_time": "close_time_ms", "open": "open",
                       "high": "high", "low": "low", "close": "close"},
            "bvol": {"time": "calc_time_ms", "value": "index_value"},
        },
        "day_relpath_template": "{day}.parquet",
        "bar_ms": 60_000,
        "estimators": [{"kind": "rms", "window": 30}, {"kind": "ewma", "half_life": 10, "lookback": 60},
                       {"kind": "high_low", "window": 30}],
        "max_spot_age_ms": 120_000,
        "max_bvol_age_ms": 120_000,
        "max_basis_age_ms": 1_200_000,
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


def reference(store, asset, anchor_ms):
    """The Binance price the anchor is compared with: mid of open and close of the bar before its window end."""
    bar = [b for b in store.bars[asset] if b["open_time_ms"] == anchor_ms - BAR]
    return (bar[0]["open"] + bar[0]["close"]) / 2.0 if bar else None


def anchor(store, asset, anchor_ms, basis=1.0, series="KXBTC15M", lag_ms=LAG_MS):
    """An anchor whose value is ``basis`` times the Binance reference (None when there is no bar)."""
    ref = reference(store, asset, anchor_ms)
    if ref is None:
        return None
    return {"ticker": f"{series}-A{anchor_ms}", "series": series, "anchor_ms": anchor_ms,
            "known_ms": anchor_ms + lag_ms, "anchor_value": ref * basis}


def unit_anchors(store, rows, basis=1.0):
    """One anchor per row, 10 minutes before its decision, at a constant ``basis`` to Binance."""
    out = [anchor(store, ASSET_OF[r["series"]], r["decision_ms"] - 600_000, basis,
                  series="KXETH15M" if ASSET_OF[r["series"]] == "ETH" else "KXBTC15M")
           for r in rows if r["series"] in ASSET_OF]
    return [a for a in out if a is not None]


def spot(store, rows, anchors=None, **over):
    manifests = StreamManifests("manifests", {"root": store.path, "streams": STREAMS}).run(
        None, {})["manifests"]
    node = SpotFeatures("spot", params(store.path, **over))
    given = unit_anchors(store, rows) if anchors is None else anchors
    return node.run(None, {"records": rows, "manifests": manifests, "anchors": given})


def one(store, anchors=None, **kw):
    return spot(store, [decision_row(**kw)], anchors=anchors)["records"][0]


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
        node.run(None, {"records": [decision_row()], "manifests": manifests, "anchors": []})


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


# -- the settlement-index basis (a strike anchor against Binance) ------------------------

DELTA = 4e-4  # BRTI sits this far BELOW Binance in the fixtures, as it did on average in real data


def test_a_row_with_a_usable_anchor_gets_the_basis_its_age_and_a_brti_unit_spot(tmp_path, monkeypatch):
    store = world(tmp_path, monkeypatch)
    anchors = [anchor(store, "BTC", DECISION - 600_000, basis=1.0 - DELTA)]
    out = one(store, anchors=anchors)
    assert out["basis"] == pytest.approx(1.0 - DELTA)
    assert out["basis_age_ms"] == 600_000 - LAG_MS and out["basis_missing"] is False
    assert out["spot_brti"] == pytest.approx(out["spot"] * (1.0 - DELTA))
    assert out["spot"] == store.bars["BTC"][last_closed(store.bars["BTC"], DECISION)]["close"], "raw spot is kept"


def test_moneyness_is_measured_against_the_brti_unit_spot(tmp_path, monkeypatch):
    store = world(tmp_path, monkeypatch)
    anchors = [anchor(store, "BTC", DECISION - 600_000, basis=1.0 - DELTA)]
    raw = store.bars["BTC"][last_closed(store.bars["BTC"], DECISION)]["close"]
    strike = raw * (1.0 - DELTA) * math.exp(0.01)
    out = one(store, anchors=anchors, floor=strike)
    assert out["log_moneyness"] == pytest.approx(0.01), "K is in index units, so S must be too"
    assert out["ln_floor_over_spot"] == pytest.approx(0.01)


def test_the_latest_anchor_known_strictly_before_the_decision_is_used(tmp_path, monkeypatch):
    store = world(tmp_path, monkeypatch)
    early = anchor(store, "BTC", DECISION - 900_000, basis=0.9996)
    later = anchor(store, "BTC", DECISION - 300_000, basis=0.9990)
    at_decision = {**anchor(store, "BTC", DECISION - 60_000, basis=0.5), "known_ms": DECISION}
    published_after = {**anchor(store, "BTC", DECISION - 60_000, basis=2.0), "known_ms": DECISION + 7_000}
    out = one(store, anchors=[early, published_after, later, at_decision])
    assert out["basis"] == pytest.approx(0.9990), "the anchor known AT the decision, or after it, is invisible"
    assert out["basis_age_ms"] == 300_000 - LAG_MS


def test_an_anchor_published_after_the_decision_is_never_used(tmp_path, monkeypatch):
    store = world(tmp_path, monkeypatch)
    legal = anchor(store, "BTC", DECISION - 600_000, basis=0.9996)
    future = [anchor(store, "BTC", DECISION + 60_000 * k, basis=3.0) for k in range(1, 6)]
    clean, dirty = one(store, anchors=[legal]), one(store, anchors=[legal, *future])
    assert {k: clean[k] for k in ("basis", "basis_age_ms", "spot_brti")} == {
        k: dirty[k] for k in ("basis", "basis_age_ms", "spot_brti")}
    # control: the same wild anchor, known before the decision, does move it
    wild = anchor(store, "BTC", DECISION - 300_000, basis=3.0)
    assert one(store, anchors=[legal, wild])["basis"] == pytest.approx(3.0)


def test_the_reference_bar_ends_before_the_anchor_is_known(tmp_path, monkeypatch):
    # the anchor averages the minute BEFORE its window end; Binance's minute that opens at the window end
    # is not part of it and must not move the basis
    clean = world(tmp_path / "clean", monkeypatch)
    anchor_ms = DECISION - 600_000
    legal = anchor(clean, "BTC", anchor_ms, basis=0.9996)
    dirty = world(tmp_path / "dirty", monkeypatch, btc=spiked(clean.bars["BTC"], anchor_ms))
    assert one(dirty, anchors=[legal])["basis"] == one(clean, anchors=[legal])["basis"]
    # control: a spike in the bar that IS the reference does move it
    inside = world(tmp_path / "inside", monkeypatch, btc=spiked(clean.bars["BTC"], anchor_ms - BAR))
    assert one(inside, anchors=[legal])["basis"] != one(clean, anchors=[legal])["basis"]


def test_a_stale_anchor_is_missing_never_carried_forward(tmp_path, monkeypatch):
    store = world(tmp_path, monkeypatch)
    old = anchor(store, "BTC", DECISION - 1_500_000, basis=0.9996)  # known 24.5 minutes ago, cap is 20
    out = one(store, anchors=[old])
    assert out["basis"] is None and out["basis_age_ms"] is None and out["basis_missing"] is True
    assert out["spot_brti"] is None and out["log_moneyness"] is None and out["ln_floor_over_spot"] is None
    assert out["spot"] is not None and out["rv_rms_30"] is not None, "only the index-unit columns are blanked"
    wider = spot(store, [decision_row()], anchors=[old], max_basis_age_ms=1_800_000)["records"][0]
    assert wider["basis"] == pytest.approx(0.9996)


def test_no_anchor_at_all_is_a_missing_basis(tmp_path, monkeypatch):
    out = one(world(tmp_path, monkeypatch), anchors=[])
    assert out["basis_missing"] is True and out["spot_brti"] is None


def test_an_anchor_with_no_binance_bar_to_compare_with_is_skipped_for_an_earlier_one(tmp_path, monkeypatch):
    store = world(tmp_path, monkeypatch)
    good = anchor(store, "BTC", DECISION - 900_000, basis=0.9996)
    no_bar = {**good, "anchor_ms": START - 3_600_000, "known_ms": DECISION - 400_000}  # window before the tape
    out = one(store, anchors=[good, no_bar])
    assert out["basis"] == pytest.approx(0.9996), "an anchor that cannot be compared is not an anchor"


def test_hourly_rows_use_the_15_minute_anchors_of_their_asset(tmp_path, monkeypatch):
    store = world(tmp_path, monkeypatch)
    btc = anchor(store, "BTC", DECISION - 600_000, basis=0.9996)
    eth = anchor(store, "ETH", DECISION - 300_000, basis=0.9900, series="KXETH15M")
    rows = [decision_row(series="KXBTC", floor=60000.0), decision_row(series="KXBTC15M"),
            decision_row(series="KXETH15M", floor=3000.0)]
    base = params(store.path)["assets"]
    assets = {**base, "BTC": {**base["BTC"], "series": ["KXBTC15M", "KXBTC"]}}
    out = spot(store, rows, anchors=[btc, eth], assets=assets)["records"]
    assert out[0]["basis"] == pytest.approx(0.9996), "a KXBTC row prices off the KXBTC15M anchor"
    assert out[1]["basis"] == pytest.approx(0.9996)
    assert out[2]["basis"] == pytest.approx(0.9900), "ETH has its own basis"


def test_an_anchor_of_a_series_no_asset_claims_is_refused_by_name(tmp_path, monkeypatch):
    store = world(tmp_path, monkeypatch)
    bad = {**anchor(store, "BTC", DECISION - 600_000), "series": "KXSOL15M"}
    with pytest.raises(ValueError, match="KXSOL15M"):
        one(store, anchors=[bad])


def test_a_constant_unit_basis_does_not_bias_the_fair_value(tmp_path, monkeypatch):
    """Strikes are in index (BRTI) dollars and Binance is a few bp above them: the fair value of a coin flip.

    Binance is flat from 12 minutes before the decision, so at the decision it sits exactly where it
    was when the strike was set; the contract is then a fair coin whatever the units. Priced against the raw
    Binance price the strike looks 4 bp out of the money in the wrong direction and the probability is far
    from one half.
    """
    flat_from, level = DECISION - 12 * BAR, 60000.0
    bars = [b if b["open_time_ms"] < flat_from or b["open_time_ms"] >= DECISION + 5 * BAR
            else {**b, "open": level, "high": level, "low": level, "close": level}
            for b in walk(START, BARS, price=level, seed=5)]
    store = world(tmp_path, monkeypatch, btc=bars)
    open_ms = DECISION - 10 * BAR
    anchors = [anchor(store, "BTC", open_ms, basis=1.0 - DELTA)]
    strike = anchors[0]["anchor_value"]
    assert strike == pytest.approx(level * (1.0 - DELTA)), "premise: the strike is in index units"
    row = decision_row(floor=strike, tau_s=300.0)
    out = spot(store, [row], anchors=anchors)["records"]
    price = lambda field: FairValue("fair", {"vol_field": "rv_rms_30", "fair_field": "fair",  # noqa: E731
                                            "spot_field": field, "averaging_window_s": 60}
                                    ).run(None, {"records": out})["records"][0]["fair"]
    assert out[0]["basis"] == pytest.approx(1.0 - DELTA)
    assert price("spot_brti") == pytest.approx(0.5, abs=0.005)
    assert price("spot") > 0.6, "what the raw Binance price would have said: a unit mismatch, not an edge"


def test_the_basis_params_are_required_and_the_open_column_is_declared(tmp_path):
    good = params("/r")
    with pytest.raises(Exception, match="max_basis_age_ms"):
        SpotFeatures("spot", {k: v for k, v in good.items() if k != "max_basis_age_ms"})
    klines = {k: v for k, v in good["columns"]["klines"].items() if k != "open"}
    with pytest.raises(Exception, match="open"):
        SpotFeatures("spot", {**good, "columns": {**good["columns"], "klines": klines}})
