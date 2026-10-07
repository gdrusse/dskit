"""kalshi_history pins (ADR-0239): the boundaries and refusals the main file left to mutation.

- A status the pack does not rank is scanned FIRST, whatever ranked statuses sit beside it. The documented rule ("one the
  pack does not rank goes first, as given") was pinned only beside ``settled``, whose rank is the highest, so an unranked
  status that sorted as if it were a low-ranked one (``open``) passed. Here it sits beside the lowest ranks.
- A price of exactly 0.0 or 1.0 is a price (LB2-03): in the real pull ``yes_bid_close`` is 0.0 in 46 percent of the candle
  rows and ``yes_ask_close`` is 1.0 in 35 percent, so a bound that turned one into None would blank nearly half of them.
  The same bounds hold for a trade's price and an order-book level, and the knob minimums are accepted AT their minimum.
"""

import pytest
from dskit.assets.base import AssetError
from dskit.onboarding.libs import kalshi_history

from .test_kalshi_history import (
    CONFIG, NEW, OPEN, archive_candle, archive_routes, book, by_status, connector, data, market, read, trade,
)


@pytest.mark.parametrize("configured, scanned", [
    (["open", "zzz", "unopened", "closed"], ["zzz", "unopened", "open", "closed"]),
    (["unopened", "zzz"], ["zzz", "unopened"]),
    (["closed", "open", "aaa", "zzz"], ["aaa", "zzz", "open", "closed"]),
])
def test_an_unranked_status_is_scanned_before_every_ranked_one_in_the_order_given(configured, scanned):
    conn, script, _ = connector({"/markets": by_status()})
    read(conn, ["markets"], config={**CONFIG, "statuses": configured})
    assert [p["status"] for p in script.params("/markets")] == scanned


# -- a price is in [0, 1], bounds included --------------------------------------------------------------------


def archived_with(yes_bid, yes_ask, price="0.55"):
    candle = archive_candle("2026-08-06T20:00:00Z", close=price)
    candle["yes_bid"], candle["yes_ask"] = {"close": yes_bid}, {"close": yes_ask}
    return candle


@pytest.mark.parametrize("quote, expected", [
    ("0.0000", 0.0), ("0", 0.0), (0.0, 0.0), ("1.0000", 1.0), (1, 1.0), ("0.0001", 0.0001), ("0.9999", 0.9999),
    ("-0.0001", None), ("1.0001", None), ("62", None), ("-1", None), ("nan", None), ("", None), (None, None)])
def test_a_candle_quote_or_price_of_exactly_zero_or_one_is_kept_and_one_outside_is_none(quote, expected):
    conn, _, _ = connector(archive_routes([archived_with(quote, quote, price=quote)]))
    row = data(read(conn, ["candles"]))[0]
    assert (row["yes_bid_close"], row["yes_ask_close"], row["close"]) == (expected,) * 3


@pytest.mark.parametrize("quote, expected", [
    ("0.0000", 0.0), ("1.0000", 1.0), ("0.5000", 0.5), ("-0.0001", None), ("1.0001", None), ("46", None), (None, None)])
def test_a_trade_price_of_exactly_zero_or_one_is_kept_and_one_outside_is_none(quote, expected):
    conn, _, _ = connector({
        "/historical/markets": {"markets": [], "cursor": ""},
        "/markets": by_status(settled=[NEW]),
        "/markets/trades": {"trades": [trade("t1", "2026-10-07T00:10:00Z", NEW["ticker"], price=quote)], "cursor": ""}})
    assert data(read(conn, ["trades"]))[0]["yes_price"] == expected


def levels(rows):
    conn, _, _ = connector({
        "/markets": by_status(open=[OPEN]),
        f"/markets/{OPEN['ticker']}/orderbook": rows})
    row = data(read(conn, ["orderbooks"]))[0]
    return row["yes_bids"], row["no_bids"]


def test_an_order_book_level_priced_at_exactly_zero_or_one_is_kept_and_one_outside_is_dropped():
    yes, no = levels(book(
        [["0.0000", "1"], ["1.0000", "2"], ["0.5000", "3"], ["-0.0001", "4"], ["1.0001", "5"]],
        [["1.0000", "6"], ["0.0000", "7"], ["2", "8"]]))
    assert yes == [[1.0, 2.0], [0.5, 3.0], [0.0, 1.0]], "best (highest) first, both bounds in, both outsiders out"
    assert no == [[1.0, 6.0], [0.0, 7.0]]


def test_a_cents_book_level_at_zero_and_one_hundred_is_kept_and_past_them_dropped():
    yes, no = levels({"orderbook": {"yes": [[0, 1], [100, 2], [50, 3], [101, 4], [-1, 5]], "no": [[100, 6], [101, 7]]}})
    assert yes == [[1.0, 2.0], [0.5, 3.0], [0.0, 1.0]] and no == [[1.0, 6.0]]


def test_a_level_with_no_size_is_dropped_and_the_smallest_positive_size_is_kept():
    yes, _ = levels(book([["0.40", "0"], ["0.41", "-1"], ["0.42", "0.01"], ["0.43", "1"], ["0.44", None]], []))
    assert yes == [[0.43, 1.0], [0.42, 0.01]]


# -- ids, tickers, series --------------------------------------------------------------------------------------


@pytest.mark.parametrize("ticker", ["", 5, None, ["T"]])
def test_a_market_without_a_usable_ticker_is_refused(ticker):
    conn, _, _ = connector({
        "/historical/markets": {"markets": [], "cursor": ""},
        "/markets": by_status(settled=[{**market("KXBTCD-26OCT0621-T1"), "ticker": ticker}])})
    with pytest.raises(AssetError, match="lacks a ticker"):
        list(conn.read(CONFIG, ["markets"], {}, "live"))


@pytest.mark.parametrize("trade_id", ["", 5, None, ["t"]])
def test_a_trade_without_a_usable_trade_id_is_refused(trade_id):
    bad = {**trade("t1", "2026-10-07T00:10:00Z", NEW["ticker"]), "trade_id": trade_id}
    conn, _, _ = connector({
        "/historical/markets": {"markets": [], "cursor": ""},
        "/markets": by_status(settled=[NEW]),
        "/markets/trades": {"trades": [bad], "cursor": ""}})
    with pytest.raises(AssetError, match="lacks a trade_id"):
        list(conn.read(CONFIG, ["trades"], {}, "live"))


@pytest.mark.parametrize("ticker, listed, expected", [
    ("KXBTCD-26OCT0621-T1", None, "KXBTCD"), ("KXBTCD-26OCT0621-T1", "", "KXBTCD"),
    ("KXBTCD-26OCT0621-T1", "KXOTHER", "KXOTHER"), ("KXBTCD", None, "KXBTCD"), ("A-B-C-D", None, "A")])
def test_the_series_of_a_market_is_the_listed_one_else_the_first_segment_of_its_ticker(ticker, listed, expected):
    conn, _, _ = connector({
        "/historical/markets": {"markets": [], "cursor": ""},
        "/markets": by_status(settled=[market(ticker, series_ticker=listed)])})
    assert data(read(conn, ["markets"]))[0]["series_ticker"] == expected


# -- knobs: the minimum is accepted, one below it is not ----------------------------------------------------------


@pytest.mark.parametrize("knob, minimum", [
    ("limit", 1), ("max_pages", 1), ("period_interval", 1), ("batch_size", 1), ("max_candles", 2),
    ("archive_max_candles", 2), ("retries", 0), ("pace_s", 0), ("pace_s", 0.0)])
def test_each_knob_is_accepted_at_its_minimum_and_refused_below_it(knob, minimum):
    conn = kalshi_history.KalshiHistoryConnector()
    assert conn.resolve_knobs({**CONFIG, knob: minimum})[knob] == minimum
    with pytest.raises(AssetError, match=f"config.{knob}"):
        conn.resolve_knobs({**CONFIG, knob: minimum - 1 if isinstance(minimum, int) else -0.1})


@pytest.mark.parametrize("timeout_s", [1, 0.5, 0.001, 30, 600])
def test_any_positive_timeout_is_accepted_and_zero_is_not(timeout_s):
    conn = kalshi_history.KalshiHistoryConnector()
    assert conn.resolve_knobs({**CONFIG, "timeout_s": timeout_s})["timeout_s"] == timeout_s
    for bad in (0, 0.0, -0.001):
        with pytest.raises(AssetError, match="config.timeout_s"):
            conn.resolve_knobs({**CONFIG, "timeout_s": bad})


@pytest.mark.parametrize("statuses", [[""], ["open", ""], [1], ["open", None], [["open"]]])
def test_a_status_must_be_a_non_empty_string(statuses):
    with pytest.raises(AssetError, match="config.statuses"):
        kalshi_history.KalshiHistoryConnector().resolve_knobs({**CONFIG, "statuses": statuses})


def test_no_pacing_gap_means_no_sleep_at_all():
    conn, _, sleeps = connector({"/historical/markets": {"markets": [], "cursor": ""}, "/markets": by_status()})
    read(conn, ["markets"], config={**CONFIG, "pace_s": 0})
    assert sleeps == [], "a zero gap is no gap: the sleeper is never called"


# -- the checkpoint is the first-seen text of the newest instant ---------------------------------------------------


def test_a_trade_at_exactly_the_checkpoint_instant_leaves_the_checkpoint_text_alone():
    # the cursor is compared as an instant: a trade dated the same instant in another spelling is not "newer"
    cursor = "2026-10-07T00:30:00+00:00"
    conn, _, _ = connector({
        "/historical/markets": {"markets": [], "cursor": ""},
        "/markets": by_status(settled=[NEW]),
        "/markets/trades": {"trades": [trade("t1", "2026-10-07T00:30:00Z", NEW["ticker"])], "cursor": ""}})
    msgs = read(conn, ["trades"], state={"trades": {"cursor": cursor}})
    assert msgs[-1]["state"] == {"trades": {"cursor": cursor}}
