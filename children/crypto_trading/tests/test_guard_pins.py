"""Edge-of-data and degenerate-input guards of the child's nodes, each pinned by one case only that guard answers.

A mutation pass found these rules unprotected: single-token changes that every other test still passed (the first
loaded bar, a candle ``ts`` that is a bool or None, a zero multiplier, one bad name among good ones). None is a
demonstrated wrong result; each is a guard the child's own rules (no look-ahead, no imputation, a refusal for every
unusable reading) rely on, so a regression of it should fail a test. Helpers are the neighbouring test files' own.
"""

import logging

import pytest
from dskit.pipeline.base import ConfigError
from dskit.pipeline.libs.parquet_series import StreamManifests
from synthetic import Store, bvol_days, kline_days, ms, utc, walk
from test_fees import FEE_TYPES, row as fee_row, run as run_fees, schedule
from test_kalshi_rows import PARAMS as MARKET_PARAMS, candle_raw
from test_market_state import MINUTE, PARAMS as STATE_PARAMS, candle, row as state_row, run as run_state
from test_spot_features import (
    BAR, BARS, DECISION, START, STREAMS, anchor, decision_row, ewma_per_sqrt_s, last_closed, one, params, parkinson_per_sqrt_s,
    reference, rms_per_sqrt_s, spot, world,
)

from crypto_trading import fields as f
from crypto_trading.anchors import StrikeAnchors
from crypto_trading.decisions import DecisionRows
from crypto_trading.fees import FeeColumns
from crypto_trading.kalshi_rows import SECONDS_CEILING, CandleRows, FeeRows, MarketRows
from crypto_trading.market_state import MarketState
from crypto_trading.spot_features import SpotFeatures

# -- kalshi_rows: a candle's ts is whole epoch SECONDS --------------------------------------------


def candles(ts):
    return CandleRows("c", {"root": "r", "source": "s"}).project([candle_raw(ts)])


@pytest.mark.parametrize("ts", [True, False, None, -1, 1.5, 12.0, "5"])
def test_a_candle_ts_that_is_not_a_non_negative_integer_is_refused_by_its_type(ts):
    with pytest.raises(ValueError, match="non-negative integer of epoch seconds"):
        candles(ts)


def test_the_first_second_of_1970_and_the_last_second_before_the_ceiling_are_seconds():
    assert candles(0)[0]["end_ms"] == 0
    assert candles(SECONDS_CEILING - 1)[0]["end_ms"] == (SECONDS_CEILING - 1) * 1000


def test_the_ceiling_itself_reads_as_milliseconds_and_is_refused():
    with pytest.raises(ValueError, match="milliseconds"):
        candles(SECONDS_CEILING)


def test_the_seconds_ceiling_is_year_5138_by_an_independent_literal():
    """The two tests above import the constant, so a changed constant moved them with it; these numbers are written out."""
    assert candles(99_999_999_999)[0]["end_ms"] == 99_999_999_999_000
    with pytest.raises(ValueError, match="milliseconds"):
        candles(100_000_000_000)


def test_a_fee_schedule_row_without_a_usable_retrieved_instant_is_refused_by_series():
    node = FeeRows("fee_schedules", {"root": "r", "source": "s"})
    good = {"series_ticker": "KXBTC15M", "fee_type": "quadratic", "fee_multiplier": 1, "retrieved": "2026-10-06T00:00:00+00:00"}
    assert node.project([good])[0]["retrieved_ms"] == ms(utc(2026, 10, 6))
    for bad in (None, "", "yesterday"):
        with pytest.raises(ValueError, match=r"'KXBTC15M' has no usable retrieved instant"):
            node.project([{**good, "retrieved": bad}])


# -- fees: a rate of zero is a rate, an empty mapping is not a mapping ---------------------------------


def test_a_series_multiplier_of_zero_is_a_free_series_not_a_missing_one():
    out = run_fees([fee_row()], [schedule(multiplier=0.0)])[0]
    assert out["fee_status"] == "ok" and out["fee_rate"] == 0.0 and out["fee_buy_yes"] == 0.0 and out["fee_buy_no"] == 0.0


def test_a_base_rate_of_zero_is_accepted_and_a_negative_one_is_not():
    entry = FEE_TYPES["quadratic"]
    node = FeeColumns("fees", {"fee_types": {"quadratic": {**entry, "base_rate": 0.0}}, "contracts": 1})
    assert node.run(None, {"records": [fee_row()], "schedules": [schedule()]})["records"][0]["fee_buy_yes"] == 0.0
    with pytest.raises(ConfigError, match="base_rate"):
        FeeColumns("fees", {"fee_types": {"quadratic": {**entry, "base_rate": -0.001}}, "contracts": 1})


@pytest.mark.parametrize("types", [{}, [], ["quadratic"], "quadratic", None, 0])
def test_fee_types_must_be_a_non_empty_map_whatever_else_it_is(types):
    with pytest.raises(ConfigError, match="fee_types"):
        FeeColumns("fees", {"fee_types": types, "contracts": 1})


def test_two_schedules_retrieved_at_the_same_instant_keep_the_first_listed():
    first, second = schedule(multiplier=1.0), schedule(multiplier=2.0)
    assert first["retrieved_ms"] == second["retrieved_ms"]
    assert run_fees([fee_row()], [first, second])[0]["fee_rate"] == pytest.approx(0.07), "a tie never replaces what is held"
    assert run_fees([fee_row()], [second, first])[0]["fee_rate"] == pytest.approx(0.14)


# -- decisions and anchors: one bad element among good ones refuses the list -------------------------------


@pytest.mark.parametrize("leads", [[5, -1], [5, 0], [5, True], [5, "x"], [5, None], [5, float("nan")], [2, 2]])
def test_one_unusable_lead_among_good_ones_refuses_the_list(leads):
    with pytest.raises(ConfigError, match="leads_minutes"):
        DecisionRows("decisions", {"leads_minutes": leads, "exec_lag_s": 5})


def test_an_execution_lag_of_one_second_is_valid_and_zero_is_not():
    assert DecisionRows("decisions", {"leads_minutes": [5], "exec_lag_s": 1})
    assert DecisionRows("decisions", {"leads_minutes": [5], "exec_lag_s": 0.5})
    for lag in (0, -1, True, None):
        with pytest.raises(ConfigError, match="exec_lag_s"):
            DecisionRows("decisions", {"leads_minutes": [5], "exec_lag_s": lag})


@pytest.mark.parametrize("series", [["KXBTC15M", ""], ["KXBTC15M", 5], ["KXBTC15M", None], [""], [], "KXBTC15M", None])
def test_one_unusable_anchor_series_among_good_ones_refuses_the_list(series):
    with pytest.raises(ConfigError, match="anchor_series"):
        StrikeAnchors("anchors", {"anchor_series": series})


# -- market_state: a one-millisecond age cap is a cap; the quote-less census counts rows once ------------------


def test_a_candle_age_cap_of_one_millisecond_is_valid_and_zero_is_not():
    assert MarketState("state", {**STATE_PARAMS, "max_candle_age_ms": 1})
    with pytest.raises(ConfigError, match="max_candle_age_ms"):
        MarketState("state", {**STATE_PARAMS, "max_candle_age_ms": 0})


def test_the_log_counts_each_row_without_a_quote_once(caplog):
    rows = [state_row(DECISION, "A"), state_row(DECISION, "B"), state_row(DECISION, "C")]
    with caplog.at_level(logging.INFO):
        out = run_state(rows, [candle(DECISION - MINUTE, ticker="A")])
    assert [r["quote_missing"] for r in out] == [False, True, True]
    assert any("market state on 3 row(s); 2 without a quote" in message for message in caplog.messages)


# -- spot_features: the first loaded bar, an empty span, an anchor past the data, bad anchor prices ---------------


@pytest.fixture
def store(tmp_path, monkeypatch):
    """The one-day fixture world: BTC and ETH klines from ``START`` and BVOL seconds around ``DECISION``."""
    return world(tmp_path, monkeypatch)


def test_a_decision_at_the_first_loaded_bars_close_reads_that_bar(store):
    out = one(store, decision=START + BAR, anchors=[])
    assert out[f.SPOT] == pytest.approx(store.bars["BTC"][0]["close"]) and out["spot_missing"] is False
    assert out["spot_age_ms"] == 1, "the bar closed 1 ms before the decision"


def test_a_decision_on_a_day_with_no_klines_has_no_spot_and_no_crash(store):
    out = one(store, decision=DECISION + 30 * 86_400_000, anchors=[])
    assert out["spot_missing"] is True and out[f.SPOT] is None and out[f.BASIS] is None


def test_a_store_of_two_bars_is_read_from_its_first_bar(tmp_path, monkeypatch):
    bars = walk(START, 2, price=60000.0, seed=1)
    small = world(tmp_path, monkeypatch, btc=bars, eth=walk(START, 2, price=3000.0, seed=2))
    out = one(small, decision=START + 2 * BAR, anchors=[])
    assert out[f.SPOT] == pytest.approx(bars[1]["close"]) and out["spot_age_ms"] == 1


def test_a_tape_whose_manifest_is_not_wired_is_refused_by_name(store):
    manifests = StreamManifests("manifests", {"root": store.path, "streams": STREAMS}).run(None, {})["manifests"]
    for name in sorted(manifests):
        wired = {k: v for k, v in manifests.items() if k != name}
        node = SpotFeatures("spot", params(store.path))
        with pytest.raises(ValueError, match=rf"the manifests input has no '{name}'"):
            both = [decision_row(series="KXBTC15M"), decision_row(series="KXETH15M")]  # every tape is read only for a row that needs it
            node.run(None, {"records": both, "manifests": wired, "anchors": []})


def test_an_anchor_whose_window_lies_beyond_the_loaded_bars_is_ignored_not_a_crash(store):
    good = anchor(store, "BTC", DECISION - 600_000)
    far = {"ticker": "X", "series": "KXBTC15M", "anchor_ms": DECISION + 40 * 86_400_000,
           "known_ms": DECISION + 40 * 86_400_000 + 30_000, "anchor_value": 60000.0}
    out = spot(store, [decision_row()], anchors=[far, good])["records"][0]
    assert out[f.BASIS] == pytest.approx(1.0) and out[f.BASIS_MISSING] is False
    assert out[f.BASIS_AGE_MS] == 600_000 - 30_000


@pytest.mark.parametrize("value", [0.0, -5.0, float("nan"), None, True])
def test_an_anchor_without_a_positive_value_is_not_an_anchor(store, value):
    bad = {**anchor(store, "BTC", DECISION - 600_000), "anchor_value": value}
    out = spot(store, [decision_row()], anchors=[bad])["records"][0]
    assert out[f.BASIS] is None and out[f.BASIS_MISSING] is True and out[f.SPOT_BRTI] is None


def test_a_positive_anchor_value_below_one_is_still_an_anchor(store):
    anchor_ms = DECISION - 600_000
    small = {**anchor(store, "BTC", anchor_ms), "anchor_value": 0.5}
    out = spot(store, [decision_row()], anchors=[small])["records"][0]
    assert out[f.BASIS] == pytest.approx(0.5 / reference(store, "BTC", anchor_ms)) and out[f.BASIS_MISSING] is False


def test_a_reference_price_below_one_is_still_a_price(tmp_path, monkeypatch):
    cheap = world(tmp_path, monkeypatch, btc=walk(START, BARS, price=0.5, seed=1))
    out = spot(cheap, [decision_row(floor=0.5)], anchors=[anchor(cheap, "BTC", DECISION - 600_000)])["records"][0]
    assert out[f.BASIS] == pytest.approx(1.0) and out[f.BASIS_MISSING] is False


@pytest.mark.parametrize("price", [0.0, -1.0])
def test_an_anchor_whose_reference_bar_has_no_positive_price_is_not_an_anchor(tmp_path, monkeypatch, price):
    bars = walk(START, BARS, price=60000.0, seed=1)
    anchor_ms = DECISION - 600_000
    (hit,) = [i for i, b in enumerate(bars) if b["open_time_ms"] == anchor_ms - BAR]
    bars[hit] = {**bars[hit], "open": price, "close": price, "high": price, "low": price}
    broken = world(tmp_path, monkeypatch, btc=bars)
    present = {"ticker": "A", "series": "KXBTC15M", "anchor_ms": anchor_ms, "known_ms": anchor_ms + 30_000, "anchor_value": 60000.0}
    out = spot(broken, [decision_row()], anchors=[present])["records"][0]
    assert out[f.BASIS] is None and out[f.BASIS_MISSING] is True, "a zero or negative reference prices nothing, it does not divide"


# -- spot_features: an anchor in the PREVIOUS day file is read when it is recent enough ---------------------------

MIDNIGHT = ms(utc(2026, 9, 2, 0, 0))
SHORT_REACH = [{"kind": "rms", "window": 1}]  # a reach of three bars: the day file of the 1st is NOT reached by it


@pytest.fixture
def two_days(tmp_path, monkeypatch):
    """BTC and ETH klines from 23:00 on the 1st to 00:59 on the 2nd: two day files."""
    start = MIDNIGHT - 60 * BAR
    world = Store(tmp_path, monkeypatch)
    btc, eth = walk(start, 120, price=60000.0, seed=1), walk(start, 120, price=3000.0, seed=2)
    world.blobs("btc-1m", kline_days(btc))
    world.blobs("eth-1m", kline_days(eth))
    seconds = [(MIDNIGHT + 1000 * k, 50.0) for k in range(600)]
    world.blobs("btc-bvol", bvol_days(seconds))
    world.blobs("eth-bvol", bvol_days(seconds, symbol="ETHBVOLUSDT"))
    world.bars = {"BTC": btc, "ETH": eth}
    return world


@pytest.mark.parametrize("back_bars, lag_ms", [
    pytest.param(15, 30_000, id="inside-the-age-cap"),
    pytest.param(20, 0, id="known-exactly-one-cap-before-the-decision"),
])
def test_an_anchor_on_the_previous_day_is_loaded_for_the_basis_of_a_decision_after_midnight(two_days, back_bars, lag_ms):
    decision = MIDNIGHT + 5 * BAR
    prior = anchor(two_days, "BTC", decision - back_bars * BAR, lag_ms=lag_ms)
    assert prior["known_ms"] < MIDNIGHT, "premise: the anchor and its bar are on the previous day"
    out = spot(two_days, [decision_row(decision=decision)], anchors=[prior], estimators=SHORT_REACH)["records"][0]
    assert out[f.BASIS] == pytest.approx(1.0) and out[f.BASIS_AGE_MS] == back_bars * BAR - lag_ms


def test_an_anchor_known_one_millisecond_past_the_age_cap_is_not_used(two_days):
    decision = MIDNIGHT + 5 * BAR
    stale = anchor(two_days, "BTC", decision - 21 * BAR, lag_ms=BAR - 1)  # known 20 minutes and 1 ms before the decision
    assert decision - stale["known_ms"] == 20 * BAR + 1
    out = spot(two_days, [decision_row(decision=decision)], anchors=[stale], estimators=SHORT_REACH)["records"][0]
    assert out[f.BASIS] is None and out[f.BASIS_MISSING] is True


def test_the_earliest_decision_sets_what_is_loaded_even_when_a_later_one_is_a_day_on(two_days):
    """Loading reaches back from the EARLIEST instant (and its anchors' own bars), not from the latest one."""
    early, late = MIDNIGHT + 5 * BAR, MIDNIGHT + 86_400_000 + 5 * BAR  # the second has no klines at all
    edge = anchor(two_days, "BTC", MIDNIGHT)  # its window ends at midnight, so its own bar (23:59) is on the previous day file
    assert edge["anchor_ms"] - BAR < MIDNIGHT <= edge["anchor_ms"], "premise: the anchor's bar is the previous day's last"
    rows = [decision_row(decision=early), decision_row(decision=late)]
    first, second = spot(two_days, rows, anchors=[edge], estimators=SHORT_REACH)["records"]
    assert first[f.SPOT] is not None and first["spot_missing"] is False, "the early decision still has its spot"
    assert first[f.BASIS] == pytest.approx(1.0) and first[f.BASIS_AGE_MS] == early - edge["known_ms"]
    assert second["spot_missing"] is True and second[f.BASIS] is None, "a day with no klines prices nothing"


def test_the_loaded_span_covers_every_decision_on_either_side_of_a_day_boundary(two_days):
    """One call over decisions in two day files reads from the earliest back-reach to the latest instant."""
    early, late = MIDNIGHT - 30 * BAR, MIDNIGHT + 30 * BAR
    rows = [decision_row(decision=late), decision_row(decision=early)]  # the later one first: order must not matter
    after, before = spot(two_days, rows, anchors=[], estimators=SHORT_REACH)["records"]
    for out, instant in ((before, early), (after, late)):
        last_close = max(b["close_time_ms"] for b in two_days.bars["BTC"] if b["close_time_ms"] < instant)
        closes = {b["close_time_ms"]: b["close"] for b in two_days.bars["BTC"]}
        assert out[f.SPOT] == pytest.approx(closes[last_close]) and out["spot_age_ms"] == instant - last_close


# -- spot_features: the config refusals and the history reach (LB2-05) -------------------------------------------------


def refused_spot(match, **over):
    with pytest.raises(ConfigError, match=match):
        SpotFeatures("spot", params("/store", **over))


BTC_TAPES = {"klines": STREAMS["BTC_klines"], "bvol": STREAMS["BTC_bvol"]}


@pytest.mark.parametrize("assets", [{}, [], None, "BTC", 5])
def test_assets_must_be_a_non_empty_map(assets):
    refused_spot("assets is required", assets=assets)


@pytest.mark.parametrize("spec", [
    "KXBTC15M", ["KXBTC15M"], None, {"series": ["KXBTC15M"], "klines": STREAMS["BTC_klines"]},
    {**BTC_TAPES, "series": ["KXBTC15M"], "extra": 1}, {"klines": STREAMS["BTC_klines"], "bvol": STREAMS["BTC_bvol"]}])
def test_an_asset_spec_has_exactly_its_three_keys(spec):
    refused_spot("must have exactly the keys", assets={"BTC": spec})


@pytest.mark.parametrize("series", ["KXBTC15M", [], [""], [1], ["KXBTC15M", ""], ["KXBTC15M", None], None, {"KXBTC15M": 1}])
def test_an_assets_series_is_a_non_empty_list_of_non_empty_strings(series):
    refused_spot("series must be a non-empty list of strings", assets={"BTC": {**BTC_TAPES, "series": series}})


def test_a_series_listed_by_two_assets_is_refused_and_one_listed_twice_by_one_is_not():
    both = {"BTC": {**BTC_TAPES, "series": ["KXBTC15M"]}, "ETH": {**BTC_TAPES, "series": ["KXBTC15M"]}}
    refused_spot("claimed by both", assets=both)
    SpotFeatures("spot", params("/store", assets={"BTC": {**BTC_TAPES, "series": ["KXBTC15M", "KXBTC15M"]}}))


@pytest.mark.parametrize("columns", [
    {}, [], None, "klines", {"klines": params("r")["columns"]["klines"]}, {**params("r")["columns"], "extra": {}},
    {"klines": params("r")["columns"]["klines"], "bvol": []},
    {"klines": {**params("r")["columns"]["klines"], "open": ""}, "bvol": params("r")["columns"]["bvol"]},
    {"klines": {**params("r")["columns"]["klines"], "open": 5}, "bvol": params("r")["columns"]["bvol"]},
    {"klines": {**params("r")["columns"]["klines"], "extra": "x"}, "bvol": params("r")["columns"]["bvol"]},
    {"klines": params("r")["columns"]["klines"], "bvol": {"time": "calc_time_ms"}}])
def test_columns_name_exactly_the_two_tapes_and_every_column_each_needs(columns):
    refused_spot("columns", columns=columns)


@pytest.mark.parametrize("root", ["", 5, None, ["/store"]])
def test_the_root_is_a_non_empty_string(root):
    with pytest.raises(ConfigError, match="root is required"):
        SpotFeatures("spot", {**params("/store"), "root": root})


@pytest.mark.parametrize("knob", ["bar_ms", "max_spot_age_ms", "max_bvol_age_ms", "max_basis_age_ms"])
def test_an_age_or_bar_length_of_one_millisecond_is_valid_and_zero_is_not(knob):
    SpotFeatures("spot", params("/store", **{knob: 1}))
    for bad in (0, -1, 1.5, True, "1", None):
        refused_spot(knob, **{knob: bad})


@pytest.mark.parametrize("knob", ["bvol_scale", "seconds_per_year"])
def test_a_scale_of_any_positive_number_is_valid_and_zero_is_not(knob):
    SpotFeatures("spot", params("/store", **{knob: 1e-9}))
    SpotFeatures("spot", params("/store", **{knob: 1}))
    for bad in (0, 0.0, -1.0, float("nan"), float("inf"), True, "1", None):
        refused_spot(knob, **{knob: bad})


def test_the_history_loaded_is_the_estimators_reach_not_the_spot_age_cap(tmp_path, monkeypatch):
    # With the spot age cap at its 1 ms minimum the span loaded is the estimators' reach alone. The longest window
    # (the EWMA's 60 returns, 61 closes) crosses midnight from a decision an hour into the day, so a reach that
    # stopped short of it would drop the previous day's file and starve the window. The default cap of two bars used
    # to cover for a reach short by up to two. (Files load whole, so a reach short by a bar or two inside a day is
    # not observable here: that mutant is equivalent.)
    store = world(tmp_path, monkeypatch)
    bars = store.bars["BTC"]
    for offset in (0, 5 * BAR):
        decision = DECISION + offset
        out = one(store, decision=decision, anchors=[], max_spot_age_ms=1 + offset)
        i = last_closed(bars, decision)
        assert out["rv_rms_30"] == pytest.approx(rms_per_sqrt_s(bars, i, 30), rel=1e-9), offset
        assert out["rv_ewma_10"] == pytest.approx(ewma_per_sqrt_s(bars, i, 10, 60), rel=1e-9), offset
        assert out["rv_hl_30"] == pytest.approx(parkinson_per_sqrt_s(bars, i, 30), rel=1e-9), offset


# -- kalshi_rows: the vocabulary knobs refuse an empty map and accept a zero lag ---------------------------------------


def refused_market_rows(match, **over):
    with pytest.raises(ConfigError, match=match):
        MarketRows("markets", {**MARKET_PARAMS, **over})


@pytest.mark.parametrize("name", ["series", "settled_statuses"])
@pytest.mark.parametrize("value", [[], "x", [""], [1], ["ok", ""], None, {"a": 1}])
def test_a_vocabulary_list_is_a_non_empty_list_of_non_empty_strings(name, value):
    refused_market_rows(f"{name} is required", **{name: value})


@pytest.mark.parametrize("name", ["payoff_by_strike_type", "result_labels"])
@pytest.mark.parametrize("value", [{}, [], "x", None, 5])
def test_a_vocabulary_map_is_a_non_empty_map(name, value):
    refused_market_rows(f"{name} is required", **{name: value})


@pytest.mark.parametrize("labels", [{"yes": 2}, {"yes": True}, {"yes": 1, "no": -1}, {"yes": "1"}, {"yes": 0.5}])
def test_a_result_label_is_zero_or_one(labels):
    refused_market_rows("result_labels", result_labels=labels)


def test_a_zero_strike_lag_is_valid_and_a_negative_or_unlisted_one_is_not():
    MarketRows("markets", {**MARKET_PARAMS, "strike_known_lag_s": {"KXBTC15M": 0, "KXETH15M": 0.0}})
    MarketRows("markets", {**MARKET_PARAMS, "strike_known_lag_s": {}})
    for bad in ({"KXBTC15M": -0.001}, {"KXBTC15M": True}, {"KXBTC15M": float("nan")}, {"KXOTHER": 0}, "30", None):
        refused_market_rows("strike_known_lag_s", strike_known_lag_s=bad)
