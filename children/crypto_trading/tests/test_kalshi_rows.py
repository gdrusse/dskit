"""The Kalshi readers: the pack's provider-shaped rows into the child's vocabulary.

Projection rules are tested directly on rows shaped as ``scan_stream`` returns them;
two tests go through a scripted venue and a real acquisition so the field names the
pack actually emits are the ones the readers read.
"""

import pytest
from dskit.pipeline.libs.numpy import accessor_narrowing_problems
from synthetic import (
    ScriptedKalshi, Store, candle_payload, iso, market_payload, ms, utc,
)

from crypto_trading.kalshi_rows import CandleRows, MarketRows

PARAMS = {
    "root": "/unused", "source": "kalshi-crypto", "series": ["KXBTC15M", "KXETH15M"],
    "payoff_by_strike_type": {"greater": "above", "greater_or_equal": "above",
                              "less": "below", "less_or_equal": "below", "between": "between"},
    "result_labels": {"yes": 1, "no": 0},
    "settled_statuses": ["finalized", "settled"],
}
CLOSE = utc(2026, 9, 2, 0, 15)


def raw(ticker="KXBTC15M-26SEP020015-15", **over):
    """One ``markets`` row exactly as the pack emits it."""
    base = {"ticker": ticker, "event_ticker": ticker.rsplit("-", 1)[0],
            "series_ticker": ticker.split("-")[0], "strike_type": "greater_or_equal",
            "floor_strike": 60000.0, "cap_strike": None, "status": "finalized",
            "result": "yes", "open_time": iso(utc(2026, 9, 2, 0, 0)), "close_time": iso(CLOSE),
            "yes_sub_title": "t", "yes_bid": 0.0, "yes_ask": 0.0, "last_price": 0.0}
    base.update(over)
    return base


def project(rows, **params):
    node = MarketRows("markets", {**PARAMS, **params})
    return node, node.project(rows)


def test_a_settled_market_becomes_one_row_in_the_child_vocabulary():
    _, rows = project([raw()])
    assert rows == [{
        "ticker": "KXBTC15M-26SEP020015-15", "event_ticker": "KXBTC15M-26SEP020015",
        "series": "KXBTC15M", "strike_type": "greater_or_equal", "payoff": "above",
        "floor_strike": 60000.0, "cap_strike": None,
        "open_ms": ms(utc(2026, 9, 2, 0, 0)), "close_ms": ms(CLOSE), "label": 1}]


@pytest.mark.parametrize("strike_type, floor, cap, payoff", [
    ("greater", 60000.0, None, "above"), ("greater_or_equal", 60000.0, None, "above"),
    ("less", None, 61000.0, "below"), ("less_or_equal", None, 61000.0, "below"),
    ("between", 60000.0, 61000.0, "between")])
def test_every_known_geometry_maps_to_a_payoff(strike_type, floor, cap, payoff):
    _, rows = project([raw(strike_type=strike_type, floor_strike=floor, cap_strike=cap)])
    assert rows[0]["payoff"] == payoff


def test_result_is_the_label_and_nothing_else_is_guessed():
    _, rows = project([raw(result="no")])
    assert rows[0]["label"] == 0


@pytest.mark.parametrize("over, reason", [
    ({"status": "active", "result": ""}, "not_settled"),
    ({"result": ""}, "no_result"),
    ({"result": "void"}, "result_not_binary"),
    ({"strike_type": "", "floor_strike": None}, "no_strike"),
    ({"strike_type": "structured"}, "unknown_strike_type"),
    ({"strike_type": "greater", "floor_strike": None}, "strike_missing"),
    ({"strike_type": "greater", "floor_strike": 0.0}, "strike_missing"),
    ({"strike_type": "greater", "floor_strike": -5.0}, "strike_missing"),
    ({"strike_type": "less", "floor_strike": 60000.0, "cap_strike": None}, "strike_missing"),
    ({"strike_type": "between", "floor_strike": 60000.0, "cap_strike": None}, "strike_missing"),
    ({"close_time": ""}, "bad_time"),
    ({"open_time": "not a time"}, "bad_time"),
])
def test_a_row_that_cannot_be_labelled_or_placed_is_excluded_by_name(over, reason):
    node, rows = project([raw(**over)])
    assert rows == []
    assert node.excluded == [{"ticker": "KXBTC15M-26SEP020015-15", "reason": reason}]


def test_the_tbd_target_price_rows_are_dropped_by_ticker_never_guessed():
    tbd = raw("KXBTC15M-26AUG080215-15", strike_type="", floor_strike=None, status="finalized")
    node, rows = project([tbd, raw()])
    assert [r["ticker"] for r in rows] == ["KXBTC15M-26SEP020015-15"]
    assert node.excluded == [{"ticker": "KXBTC15M-26AUG080215-15", "reason": "no_strike"}]
    assert node.census == {"rows": 2, "kept": 1, "no_strike": 1}


def test_rows_come_out_in_close_order_for_a_reproducible_table():
    later = raw("KXBTC15M-26SEP020030-30", close_time=iso(utc(2026, 9, 2, 0, 30)))
    _, rows = project([later, raw()])
    assert [r["close_ms"] for r in rows] == sorted(r["close_ms"] for r in rows)


def test_the_vocabularies_are_params_a_new_geometry_is_config_not_code():
    # a payoff name must exist in the closed payoff set; an unknown one is refused at plan time
    bad = {**PARAMS, "payoff_by_strike_type": {"greater": "mystery"}}
    with pytest.raises(Exception, match="mystery"):
        MarketRows("markets", bad)
    for knob in ("series", "payoff_by_strike_type", "result_labels", "settled_statuses"):
        with pytest.raises(Exception, match=knob):
            MarketRows("markets", {k: v for k, v in PARAMS.items() if k != knob})
    with pytest.raises(Exception, match="surprise"):
        MarketRows("markets", {**PARAMS, "surprise": 1})
    with pytest.raises(Exception, match="result_labels"):
        MarketRows("markets", {**PARAMS, "result_labels": {"yes": 2}})


def test_pinned_vocabulary_knobs_are_narrowed_away_not_left_to_the_document():
    for cls in (MarketRows, CandleRows):
        assert accessor_narrowing_problems(cls) == []
        node = cls("n", {**{k: PARAMS[k] for k in ("root", "source")},
                        **({k: PARAMS[k] for k in ("series", "payoff_by_strike_type",
                                                   "result_labels", "settled_statuses")}
                           if cls is MarketRows else {})})
        assert node.stream() == ("markets" if cls is MarketRows else "candles")
    with pytest.raises(Exception, match="stream"):
        CandleRows("n", {"root": "r", "source": "s", "stream": "markets"})


def test_markets_read_through_a_real_acquisition(tmp_path, monkeypatch):
    markets = [
        market_payload("KXBTC15M-26SEP020015-15", CLOSE, floor=60000.0, result="yes"),
        market_payload("KXETH15M-26SEP020015-15", CLOSE, floor=3000.0, result="no"),
        market_payload("KXBTC15M-26SEP020030-15", utc(2026, 9, 2, 0, 30), strike_type="",
                       floor=None, result="yes"),
        market_payload("KXBTC15M-26SEP020045-15", utc(2026, 9, 2, 0, 45), floor=60000.0,
                       result=""),
    ]
    store = Store(tmp_path, monkeypatch)
    store.kalshi("kalshi-crypto", "source-kalshi-crypto.json", ScriptedKalshi(markets), ["markets"])
    node = MarketRows("markets", {**PARAMS, "root": store.path})
    out = node.run(None, {})
    assert [r["ticker"] for r in out["records"]] == [
        "KXBTC15M-26SEP020015-15", "KXETH15M-26SEP020015-15"]
    assert [r["label"] for r in out["records"]] == [1, 0]
    assert {e["reason"] for e in out["excluded"]} == {"no_strike", "no_result"}
    assert out["census"] == {"rows": 4, "kept": 2, "no_strike": 1, "no_result": 1}
    assert node.fingerprint()["rows"] == 2, "rows counts what the node emits; the digest covers the raw stream"


# -- candles -----------------------------------------------------------------------


def candle_raw(ts, **over):
    base = {"ticker": "KXBTC15M-26SEP020015-15", "ts": ts, "open": 0.4, "high": 0.4, "low": 0.4,
            "close": 0.41, "mean": 0.4, "yes_bid_close": 0.39, "yes_ask_close": 0.43,
            "volume": 12.0, "open_interest": 99.0}
    base.update(over)
    return base


def test_a_candle_end_instant_is_converted_from_seconds_to_milliseconds():
    end = utc(2026, 9, 2, 0, 5)
    out = CandleRows("c", {"root": "r", "source": "s"}).project([candle_raw(int(end.timestamp()))])
    assert out == [{"ticker": "KXBTC15M-26SEP020015-15", "end_ms": ms(end), "yes_bid": 0.39,
                    "yes_ask": 0.43, "price": 0.41, "volume": 12.0, "open_interest": 99.0}]
    assert out[0]["end_ms"] == out[0]["end_ms"] // 1000 * 1000 == int(end.timestamp()) * 1000


def test_a_candle_whose_ts_already_looks_like_milliseconds_is_refused():
    node = CandleRows("c", {"root": "r", "source": "s"})
    with pytest.raises(ValueError, match="milliseconds"):
        node.project([candle_raw(int(utc(2026, 9, 2).timestamp()) * 1000)])


def test_a_quiet_minute_keeps_none_never_a_filled_value():
    out = CandleRows("c", {"root": "r", "source": "s"}).project(
        [candle_raw(1_788_000_060, close=None, yes_bid_close=None, yes_ask_close=None,
                    volume=None)])
    assert out[0]["price"] is None and out[0]["yes_bid"] is None
    assert out[0]["yes_ask"] is None and out[0]["volume"] is None


def test_candles_come_out_ordered_by_ticker_then_end(tmp_path, monkeypatch):
    t = "KXBTC15M-26SEP020015-15"
    candles = {t: [candle_payload(utc(2026, 9, 2, 0, m), bid=0.39, ask=0.43, price=0.41)
                   for m in (3, 1, 2)]}
    store = Store(tmp_path, monkeypatch)
    store.kalshi("kalshi-crypto-candles-btc", "source-kalshi-crypto-candles-btc.json",
                 ScriptedKalshi([market_payload(t, CLOSE, floor=60000.0)], candles), ["candles"])
    rows = CandleRows("c", {"root": store.path, "source": "kalshi-crypto-candles-btc"}).run(
        None, {})["records"]
    assert [r["end_ms"] for r in rows] == [ms(utc(2026, 9, 2, 0, m)) for m in (1, 2, 3)]
    assert rows[0]["yes_bid"] == 0.39 and rows[0]["open_interest"] == 99.0
