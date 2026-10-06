"""Market state at the decision instant, from 1-minute Kalshi candles that had ENDED.

A candle is usable when its END instant is at or before the decision instant (the
minute it summarises is over). A candle that ends after it summarises time the
decision cannot see; the leak test plants an extreme quote there.
"""

import pytest

from crypto_trading.market_state import MarketState

DECISION = 1_788_307_200_000  # a whole-minute instant, epoch ms
MINUTE = 60_000
PARAMS = {"max_candle_age_ms": 120_000, "quote_floor": 0.0, "quote_ceiling": 1.0}


def candle(end, bid=0.40, ask=0.44, price=0.42, volume=10.0, interest=99.0, ticker="T"):
    return {"ticker": ticker, "end_ms": end, "yes_bid": bid, "yes_ask": ask, "price": price,
            "volume": volume, "open_interest": interest}


def row(decision=DECISION, ticker="T"):
    return {"ticker": ticker, "decision_ms": decision, "label": 1}


def run(rows, candles, **over):
    node = MarketState("state", {**PARAMS, **over})
    return node.run(None, {"records": rows, "candles": candles})["records"]


def test_the_state_is_the_last_candle_that_ended_strictly_before_the_decision():
    candles = [candle(DECISION - 3 * MINUTE, bid=0.10, ask=0.12),
               candle(DECISION - MINUTE, bid=0.40, ask=0.44, price=0.42, volume=7.0, interest=120.0)]
    out = run([row()], candles)[0]
    assert (out["yes_bid"], out["yes_ask"]) == (0.40, 0.44)
    assert out["mid"] == pytest.approx(0.42) and out["spread"] == pytest.approx(0.04)
    assert out["candle_price"] == 0.42 and out["candle_volume"] == 7.0
    assert out["candle_open_interest"] == 120.0 and out["candle_age_ms"] == MINUTE
    assert out["quote_missing"] is False and out["two_sided"] is True


def test_a_candle_ending_exactly_at_the_decision_is_not_yet_known():
    # the venue's end_period_ts is the INCLUSIVE end of the minute: its last second is the decision instant
    out = run([row()], [candle(DECISION - MINUTE), candle(DECISION, bid=0.50, ask=0.54)])[0]
    assert out["yes_bid"] == 0.40 and out["candle_age_ms"] == MINUTE
    assert run([row()], [candle(DECISION, bid=0.50, ask=0.54)])[0]["quote_missing"] is True


def test_a_candle_ending_at_or_after_the_decision_never_reaches_the_row():
    candles = [candle(DECISION - MINUTE, bid=0.40, ask=0.44),
               candle(DECISION, bid=0.97, ask=0.98, price=0.97, volume=1e6, interest=1e6),
               candle(DECISION + 1, bid=0.99, ask=0.995, price=0.99, volume=1e6, interest=1e6),
               candle(DECISION + MINUTE, bid=0.99, ask=0.995)]
    clean = run([row()], candles[:1])[0]
    dirty = run([row()], candles)[0]
    assert clean == dirty, "the candles at and after the decision are invisible"
    assert dirty["yes_bid"] == 0.40


def test_control_the_same_extreme_candle_one_minute_earlier_is_used():
    out = run([row()], [candle(DECISION - MINUTE, bid=0.97, ask=0.99)])[0]
    assert out["yes_bid"] == 0.97, "so the leak test above is not passing vacuously"


def test_milliseconds_and_seconds_are_not_confused():
    # a candle whose END (ms) is the decision's own value in SECONDS lies in 1970: far too old
    seconds_by_mistake = candle(DECISION // 1000)
    out = run([row()], [seconds_by_mistake])[0]
    assert out["yes_bid"] is None and out["quote_missing"] is True


def test_a_candle_older_than_the_age_cap_is_missing_not_stale():
    out = run([row()], [candle(DECISION - 3 * MINUTE)])[0]
    assert out["yes_bid"] is None and out["candle_age_ms"] is None and out["quote_missing"] is True
    assert out["two_sided"] is False and out["mid"] is None
    kept = run([row()], [candle(DECISION - 3 * MINUTE)], max_candle_age_ms=3 * MINUTE)[0]
    assert kept["yes_bid"] == 0.40


def test_another_markets_candle_is_never_used():
    out = run([row()], [candle(DECISION - MINUTE, ticker="OTHER")])[0]
    assert out["quote_missing"] is True


@pytest.mark.parametrize("bid, ask", [(None, 0.44), (0.40, None), (None, None)])
def test_a_missing_side_is_none_and_flagged_with_no_mid(bid, ask):
    out = run([row()], [candle(DECISION - MINUTE, bid=bid, ask=ask)])[0]
    assert out["quote_missing"] is True and out["mid"] is None and out["spread"] is None
    assert out["two_sided"] is False
    assert out["yes_bid"] == bid and out["yes_ask"] == ask, "what exists is kept as it is"


@pytest.mark.parametrize("bid, ask, two_sided", [
    (0.40, 0.44, True), (0.01, 0.99, True), (0.0, 0.44, False), (0.40, 1.0, False),
    (0.50, 0.40, False), (0.45, 0.45, True)])
def test_two_sided_means_a_real_bid_and_ask_inside_the_declared_bounds_and_not_crossed(
        bid, ask, two_sided):
    out = run([row()], [candle(DECISION - MINUTE, bid=bid, ask=ask)])[0]
    assert out["two_sided"] is two_sided
    assert out["quote_missing"] is False
    assert out["spread"] == pytest.approx(ask - bid), "a crossed book shows a negative spread"


def test_bounds_are_params():
    out = run([row()], [candle(DECISION - MINUTE, bid=0.02, ask=0.98)], quote_floor=0.05)[0]
    assert out["two_sided"] is False


def test_rows_keep_their_order_and_other_fields_and_inputs_are_not_mutated():
    rows = [row(DECISION + MINUTE, "A"), row(DECISION, "B")]
    candles = [candle(DECISION - MINUTE, ticker="A"), candle(DECISION - 2 * MINUTE, ticker="B")]
    before = [dict(r) for r in rows]
    out = run(rows, candles)
    assert [r["ticker"] for r in out] == ["A", "B"] and all(r["label"] == 1 for r in out)
    assert rows == before


def test_default_deny_and_required_knobs():
    with pytest.raises(Exception, match="surprise"):
        MarketState("state", {**PARAMS, "surprise": 1})
    for knob in PARAMS:
        with pytest.raises(Exception, match=knob):
            MarketState("state", {k: v for k, v in PARAMS.items() if k != knob})
    with pytest.raises(Exception, match="quote_ceiling"):
        MarketState("state", {**PARAMS, "quote_ceiling": 0.0})
