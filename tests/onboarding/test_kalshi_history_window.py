"""kalshi_history ``close_window`` (ADR-0267): trades only for markets that close inside a declared window.

The window is half-open (``start`` inclusive, ``end`` exclusive) over a market's ``close_time``. The stream's
checkpoint also records every pull per key (the close span asked for and the capture instant). A market is
complete when a pull of its key covered its close and was captured after it; only a market that is not complete
is requested, and then whole, so no window shift, key or cursor can hide a trade.
"""

import copy
import json
from datetime import datetime, timezone

import pytest
from dskit.assets.base import AssetError
from dskit.onboarding import check_config
from dskit.onboarding.libs.kalshi_history import KalshiHistoryConnector

from .test_kalshi_history import (
    CONFIG, CUT_S, NOW, connector, data, http_error, market, read, records, trade, trade_routes, unix,
)

SPAN = {"start": "2026-10-03", "end": "2026-10-05"}
WINDOWED = {**CONFIG, "close_window": SPAN}
#: How the checkpoint keys what was covered: the series walked under its (sorted) default statuses.
KEY = "KXBTCD|closed,open,settled"
ETH_KEY = "KXETHD|closed,open,settled"


CAP = "2026-10-07T12:00:00+00:00"  # the capture instant of every pull made at NOW


def pulled(start, end, at=CAP):
    """One recorded pull: the close span asked for (None, None: every market) and when it was captured."""
    return {"start": start, "end": end, "captured_at": at}


def coverage(msgs):
    return msgs[-1]["state"]["trades"]["coverage"]


D2, D3, D4, D5, D6, D7 = ("2026-10-0%dT00:00:00+00:00" % d for d in (2, 3, 4, 5, 6, 7))


def closing(name, close):
    """A live market that closes at ``close`` (ISO)."""
    return market(f"KXBTCD-{name}", event=f"KXBTCD-{name}", open_time="2026-10-01T00:00:00Z", close_time=close)


BEFORE = closing("BEFORE", "2026-10-02T23:59:59Z")
AT_START = closing("ATSTART", "2026-10-03T00:00:00Z")
INSIDE = closing("INSIDE", "2026-10-04T12:00:00Z")
LAST = closing("LAST", "2026-10-04T23:59:59Z")
AT_END = closing("ATEND", "2026-10-05T00:00:00Z")
AFTER = closing("AFTER", "2026-10-06T00:00:00Z")
UNCLOSED = closing("UNCLOSED", "")
EVERY = (BEFORE, AT_START, INSIDE, LAST, AT_END, AFTER, UNCLOSED)


def trade_book(markets):
    """``/markets/trades`` answering each requested ticker with one trade at its close."""
    by_ticker = {}
    for m in markets:
        close = datetime.fromisoformat((m["close_time"] or "2026-10-06T12:00:00Z").replace("Z", "+00:00").replace("z", "+00:00"))
        stamp = close.isoformat().replace("+00:00", "Z")
        by_ticker[m["ticker"]] = [trade(f"{m['ticker']}-t", stamp, m["ticker"])]
    return lambda p: {"trades": by_ticker[p["ticker"]], "cursor": ""}


def pull(markets, config=WINDOWED, state=None, now=NOW):
    """Read the trades stream; return (messages, script)."""
    conn, script, _ = connector(trade_routes(
        settled=markets, history=(), **{"/markets/trades": trade_book(markets)}), now=now)
    return read(conn, ["trades"], config=config, state=state), script


def asked(script):
    return [p["ticker"] for p in script.params("/markets/trades")]


def names(tickers):
    return [t.removeprefix("KXBTCD-") for t in tickers]


# -- the filter and its boundaries ---------------------------------------------------------------


def test_only_markets_closing_inside_the_window_are_requested_start_inclusive_end_exclusive():
    msgs, script = pull(EVERY)
    assert names(asked(script)) == ["ATSTART", "INSIDE", "LAST"]
    assert [d["ticker"] for d in data(msgs)] == [m["ticker"] for m in (AT_START, INSIDE, LAST)]


def test_a_market_without_a_close_time_is_outside_any_window():
    _, script = pull((UNCLOSED,))
    assert asked(script) == []


def test_no_window_is_the_full_pull_and_records_every_market_for_its_key():
    msgs, script = pull(EVERY, config=CONFIG)
    assert len(asked(script)) == len(EVERY)
    assert coverage(msgs) == {KEY: [pulled(None, None)]}, "every market of the key, recorded without a span"


@pytest.mark.parametrize("window", [
    {"start": "2026-10-03", "end": "2026-10-05"},
    {"start": "2026-10-03T00:00:00Z", "end": "2026-10-05T00:00:00+00:00"},
    {"start": "2026-10-02T20:00:00-04:00", "end": "2026-10-05"},
])
def test_a_window_takes_dates_or_datetimes_read_as_utc(window):
    _, script = pull(EVERY, config={**CONFIG, "close_window": window})
    assert names(asked(script)) == ["ATSTART", "INSIDE", "LAST"]


def test_the_window_bounds_trades_only_markets_are_still_a_full_pull():
    conn, _, _ = connector({
        "/historical/markets": {"markets": [], "cursor": ""},
        "/markets": lambda p: {"markets": list(EVERY) if p["status"] == "settled" else [], "cursor": ""}})
    rows = data(read(conn, ["markets"], config=WINDOWED))
    assert len(rows) == len(EVERY)


# -- the checkpoint and the widened resume -------------------------------------------------------


def test_a_windowed_pull_records_the_span_and_the_capture_it_covered():
    msgs, _ = pull(EVERY)
    state = msgs[-1]["state"]["trades"]
    assert state["cursor"] == "2026-10-04T23:59:59Z"
    assert state["coverage"] == {KEY: [pulled(D3, D5)]}
    json.dumps(msgs[-1]["state"], allow_nan=False)


def test_a_complete_market_is_never_requested_again():
    first, _ = pull(EVERY)
    again, script = pull(EVERY, state=first[-1]["state"])
    assert asked(script) == [] and records(again) == []
    assert again[-1]["state"] == first[-1]["state"]  # nothing new to record: the same span, the same capture


def test_a_widened_window_fetches_only_the_new_markets_whole():
    first, _ = pull(EVERY)
    widened = {**CONFIG, "close_window": {"start": "2026-10-02", "end": "2026-10-06"}}
    msgs, script = pull(EVERY, config=widened, state=first[-1]["state"])
    # BEFORE closes before the old window and AT_END after it: both are new to this key, so read whole.
    assert names(asked(script)) == ["BEFORE", "ATEND"]
    assert {p["ticker"]: p["min_ts"] for p in script.params("/markets/trades")} == {
        BEFORE["ticker"]: CUT_S, AT_END["ticker"]: CUT_S}, "bounded only by the archive cutoff, never by any cursor"
    assert [d["trade_id"] for d in data(msgs)] == ["KXBTCD-BEFORE-t", "KXBTCD-ATEND-t"]
    assert coverage(msgs) == {KEY: [pulled(D2, D6)]}, "the narrower first pull is held by the wider one"


def test_a_market_open_at_capture_is_read_whole_by_a_later_pull_then_never_again():
    open_one = closing("OPEN", "2026-10-04T18:00:00Z")
    markets = (INSIDE, open_one)
    first, script = pull(markets, now=datetime(2026, 10, 4, 15, 0, 30, tzinfo=timezone.utc))
    assert names(asked(script)) == ["INSIDE", "OPEN"]
    assert [d["trade_id"] for d in data(first)] == ["KXBTCD-INSIDE-t"], "OPEN's trade is after the 15:00 capture"
    second, script = pull(markets, state=first[-1]["state"], now=datetime(2026, 10, 5, 0, 0, 30, tzinfo=timezone.utc))
    assert asked(script) == [open_one["ticker"]], "INSIDE closed before the first capture: complete; OPEN was open then"
    assert script.params("/markets/trades")[0]["min_ts"] == CUT_S, "whole, not from a cursor"
    assert [d["trade_id"] for d in data(second)] == ["KXBTCD-OPEN-t"]
    third, script = pull(markets, state=second[-1]["state"], now=datetime(2026, 10, 5, 1, 0, 30, tzinfo=timezone.utc))
    assert asked(script) == []


def test_a_market_with_no_close_time_is_never_complete_so_an_unwindowed_pull_reads_it_whole_each_time():
    first, _ = pull(EVERY, config=CONFIG)
    again, script = pull(EVERY, config=CONFIG, state=first[-1]["state"])
    assert names(asked(script)) == ["UNCLOSED"]


def test_a_window_inside_what_was_covered_fetches_nothing_and_records_nothing_new():
    first, _ = pull(EVERY)
    narrower = {**CONFIG, "close_window": {"start": "2026-10-03", "end": "2026-10-04"}}
    msgs, script = pull(EVERY, config=narrower, state=first[-1]["state"])
    assert asked(script) == [] and records(msgs) == []
    assert msgs[-1]["state"] == first[-1]["state"]


def test_a_shifted_window_asks_only_for_what_no_pull_completed():
    first, _ = pull(EVERY)
    later = {**CONFIG, "close_window": {"start": "2026-10-04", "end": "2026-10-07"}}
    msgs, script = pull(EVERY, config=later, state=first[-1]["state"])
    assert names(asked(script)) == ["ATEND", "AFTER"]
    assert coverage(msgs) == {KEY: [pulled(D3, D5), pulled(D4, D7)]}


def test_disjoint_spans_stay_separate_and_a_gap_fill_fetches_only_the_gap():
    first, _ = pull(EVERY)
    apart = {**CONFIG, "close_window": {"start": "2026-10-06", "end": "2026-10-07"}}
    second, script = pull(EVERY, config=apart, state=first[-1]["state"])
    assert names(asked(script)) == ["AFTER"]
    assert coverage(second) == {KEY: [pulled(D3, D5), pulled(D6, D7)]}
    gap = {**CONFIG, "close_window": {"start": "2026-10-05", "end": "2026-10-06"}}
    third, script = pull(EVERY, config=gap, state=second[-1]["state"])
    assert names(asked(script)) == ["ATEND"]
    assert len(coverage(third)[KEY]) == 3


def test_dropping_the_window_reads_what_no_pull_completed_and_records_every_market():
    first, _ = pull(EVERY)
    msgs, script = pull(EVERY, config=CONFIG, state=first[-1]["state"])
    assert names(asked(script)) == ["BEFORE", "ATEND", "AFTER", "UNCLOSED"]
    assert coverage(msgs) == {KEY: [pulled(None, None)]}


LEGACY = {"trades": {"cursor": "2026-10-04T12:00:00+00:00"}}


def test_a_windowed_pull_of_a_checkpoint_from_before_the_record_refuses_by_name():
    conn, script, _ = connector(trade_routes(settled=EVERY, history=(), **{"/markets/trades": trade_book(EVERY)}))
    with pytest.raises(AssetError, match="close_window.*before ADR-0267.*fresh source name"):
        list(conn.read(WINDOWED, ["trades"], LEGACY, "backfill"))
    assert script.params("/markets/trades") == [], "refused before any trades request"


def test_an_unwindowed_pull_of_a_checkpoint_from_before_the_record_is_as_it_was_and_records_nothing():
    msgs, script = pull(EVERY, config=CONFIG, state=LEGACY)
    # Closed at or before the cursor: skipped; the rest are asked from the cursor, as before ADR-0267.
    assert names(asked(script)) == ["LAST", "ATEND", "AFTER", "UNCLOSED"]
    assert {p["min_ts"] for p in script.params("/markets/trades")} == {unix("2026-10-04T12:00:00Z")}
    assert "coverage" not in msgs[-1]["state"]["trades"], "no key is marked complete for a series it may not have walked"
    again, script = pull(EVERY, config=CONFIG, state=msgs[-1]["state"])
    assert "coverage" not in again[-1]["state"]["trades"] and {p["min_ts"] for p in script.params("/markets/trades")} == {
        unix(msgs[-1]["state"]["trades"]["cursor"])}, "and it stays on the cursor rule"


def test_an_empty_pull_with_a_window_still_records_the_span():
    msgs, _ = pull((AFTER,))
    assert records(msgs) == []
    assert msgs[-1]["state"]["trades"] == {"cursor": "", "coverage": {KEY: [pulled(D3, D5)]}}


def test_a_first_windowed_pull_ignores_a_state_that_holds_only_other_streams():
    msgs, script = pull(EVERY, state={"candles": {"cursor": "2026-10-06T00:00:00+00:00"}})
    assert names(asked(script)) == ["ATSTART", "INSIDE", "LAST"]
    assert msgs[-1]["state"]["candles"] == {"cursor": "2026-10-06T00:00:00+00:00"}


def test_the_state_it_was_given_is_never_mutated():
    first, _ = pull(EVERY)
    state = first[-1]["state"]
    frozen = json.loads(json.dumps(state))
    pull(EVERY, config={**CONFIG, "close_window": {"start": "2026-10-01", "end": "2026-10-09"}}, state=state)
    assert state == frozen


@pytest.mark.parametrize("bad", [
    "nope", {KEY: "nope"}, {KEY: ["nope"]}, {KEY: [pulled(D3, D5, at="yesterday")]}, {KEY: [pulled(D3, D5, at=None)]},
    {KEY: [pulled(D5, D3)]}, {KEY: [pulled(D3, None)]}, {KEY: [pulled(None, D5)]}, {KEY: [pulled("a", "b")]},
    {KEY: [{"start": D3, "end": D5}]}, {KEY: [{**pulled(D3, D5), "extra": 1}]}])
def test_a_malformed_recorded_coverage_refuses_the_pull(bad):
    state = {"trades": {"cursor": "2026-10-04T12:00:00+00:00", "coverage": bad}}
    conn, _, _ = connector(trade_routes(settled=EVERY, history=(), **{"/markets/trades": trade_book(EVERY)}))
    with pytest.raises(AssetError, match="coverage"):
        list(conn.read(WINDOWED, ["trades"], state, "backfill"))


# -- the knob: default-deny ------------------------------------------------------------------------


def test_the_knob_is_declared_and_passes_the_spec_gate():
    conn = KalshiHistoryConnector()
    assert "close_window" in conn.spec()["params"]
    check_config(conn, WINDOWED)
    assert conn.resolve_knobs(CONFIG)["close_window"] is None
    start, end = conn.resolve_knobs(WINDOWED)["close_window"]
    assert (start, end) == (datetime(2026, 10, 3, tzinfo=timezone.utc), datetime(2026, 10, 5, tzinfo=timezone.utc))


@pytest.mark.parametrize("bad", [
    "2026-10-03", ["2026-10-03", "2026-10-05"], {}, {"start": "2026-10-03"}, {"end": "2026-10-05"},
    {"start": "2026-10-03", "end": "2026-10-05", "step": 1},
    {"start": "2026-10-05", "end": "2026-10-05"}, {"start": "2026-10-05", "end": "2026-10-03"},
    {"start": "yesterday", "end": "2026-10-05"}, {"start": "2026-10-03", "end": 5},
    {"start": "", "end": "2026-10-05"}, {"start": None, "end": "2026-10-05"}, None])
def test_a_malformed_window_is_refused_by_name(bad):
    with pytest.raises(AssetError, match="config.close_window"):
        KalshiHistoryConnector().resolve_knobs({**CONFIG, "close_window": bad})


# -- a new series or status set is uncovered ---------------------------------------------------------


def per_series(markets_by_series):
    """``/markets`` answering each series' settled markets; the other statuses are empty."""
    return lambda p: {"markets": markets_by_series.get(p["series_ticker"], []) if p["status"] == "settled" else [],
                      "cursor": ""}


ETH_INSIDE = market("KXETHD-INSIDE", event="KXETHD-INSIDE", open_time="2026-10-01T00:00:00Z",
                    close_time="2026-10-04T12:00:00Z")


def pull_series(config, state=None):
    both = {"KXBTCD": [INSIDE], "KXETHD": [ETH_INSIDE]}
    book = trade_book([INSIDE, ETH_INSIDE])
    conn, script, _ = connector({
        "/historical/markets": {"markets": [], "cursor": ""}, "/markets": per_series(both),
        "/markets/trades": book})
    return read(conn, ["trades"], config=config, state=state), script


def test_a_series_added_after_a_windowed_pull_is_fetched_whole_not_skipped():
    first, script = pull_series({**WINDOWED})
    assert names(asked(script)) == ["INSIDE"]
    both = {**WINDOWED, "series": ["KXBTCD", "KXETHD"]}
    msgs, script = pull_series(both, state=first[-1]["state"])
    assert asked(script) == [ETH_INSIDE["ticker"]], "BTC is complete; ETH was never walked"
    assert script.params("/markets/trades")[0]["min_ts"] == CUT_S
    assert set(coverage(msgs)) == {KEY, ETH_KEY}


def test_a_changed_status_set_is_uncovered_so_its_markets_are_asked_again():
    first, _ = pull_series({**WINDOWED})
    settled_only = {**WINDOWED, "statuses": ["settled"]}
    msgs, script = pull_series(settled_only, state=first[-1]["state"])
    assert asked(script) == [INSIDE["ticker"]]
    assert set(coverage(msgs)) == {KEY, "KXBTCD|settled"}


def test_dropping_the_window_records_every_market_per_key_without_inventing_other_series():
    first, _ = pull_series({**WINDOWED})
    msgs, _ = pull_series({**CONFIG}, state=first[-1]["state"])
    assert coverage(msgs) == {KEY: [pulled(None, None)]}
    later, script = pull_series({**WINDOWED, "series": ["KXBTCD", "KXETHD"]}, state=msgs[-1]["state"])
    assert asked(script) == [ETH_INSIDE["ticker"]], "the series no pull walked is still uncovered"


def test_a_series_added_to_a_record_less_checkpoint_cannot_be_windowed():
    with pytest.raises(AssetError, match="fresh source name"):
        pull_series({**WINDOWED, "series": ["KXBTCD", "KXETHD"]}, state=LEGACY)


# -- a close_time the window cannot place, and a failed pull --------------------------------------------


def test_a_close_time_that_is_not_an_iso_instant_refuses_a_windowed_pull_by_name():
    odd = closing("ODD", "2026-10-04T12:00:00z")  # lowercase z: not an instant the pack reads
    with pytest.raises(AssetError, match="KXBTCD-ODD.*not an ISO instant"):
        pull((INSIDE, odd))
    _, script = pull((odd,), config=CONFIG)  # without a window it is requested, as ever
    assert asked(script) == [odd["ticker"]]


def test_a_failed_windowed_pull_leaves_the_prior_checkpoint_and_coverage_as_they_were():
    first, _ = pull(EVERY)
    state = first[-1]["state"]
    frozen = copy.deepcopy(state)
    widened = {**CONFIG, "close_window": {"start": "2026-10-02", "end": "2026-10-06"}}
    broken = lambda p: (_ for _ in ()).throw(http_error(400)) if p["ticker"] == AT_END["ticker"] else (
        trade_book(EVERY)(p))
    conn, _, _ = connector(trade_routes(settled=EVERY, history=(), **{"/markets/trades": broken}))
    seen = []
    with pytest.raises(AssetError):
        for msg in conn.read(widened, ["trades"], state, "backfill"):
            seen.append(msg)
    assert [m["type"] for m in seen if m["type"] == "STATE"] == [], "no checkpoint is emitted for a pull that failed"
    assert state == frozen, "and the one it resumed from is untouched"
    # The same pull, rerun from the same checkpoint, is a whole pull: nothing was half-recorded.
    again, script = pull(EVERY, config=widened, state=state)
    assert names(asked(script)) == ["BEFORE", "ATEND"]
    assert coverage(again) == {KEY: [pulled(D2, D6)]}


# -- no other pull can hide a trade ---------------------------------------------------------------------


def minute(hhmm, day=4):
    return f"2026-10-{day:02d}T{hhmm[:2]}:{hhmm[2:]}:00Z"


def timed_pull(now, by_series, trades, series, window, state=None):
    """One pull at ``now`` (``(day, hhmm)``) of ``series`` over ``window``; the venue answers ``min_ts`` like the real one."""
    def answer(p):
        floor = p.get("min_ts", 0)
        return {"trades": [t for t in trades[p["ticker"]] if unix(t["created_time"]) >= floor], "cursor": ""}

    clock = datetime.fromisoformat(minute(now[1], now[0]).replace("Z", "+00:00"))
    conn, script, _ = connector({
        "/historical/markets": {"markets": [], "cursor": ""}, "/markets": per_series(by_series),
        "/markets/trades": answer}, now=clock)
    config = {**CONFIG, "series": series, "close_window": window}
    return read(conn, ["trades"], config=config, state=state), script


DAY4 = {"start": "2026-10-04", "end": "2026-10-05"}
DAY5 = {"start": "2026-10-05", "end": "2026-10-06"}


def test_a_pull_of_another_key_cannot_hide_a_late_trade_of_this_one():
    btc = closing("B", minute("1200"))
    eth = market("KXETHD-E", event="KXETHD-E", open_time="2026-10-01T00:00:00Z", close_time=minute("1200"))
    by_series = {"KXBTCD": [btc], "KXETHD": [eth]}
    a10, a11 = (trade(f"a{h}", minute(f"{h}00"), btc["ticker"]) for h in (10, 11))
    trades = {btc["ticker"]: [a10, a11], eth["ticker"]: [trade("e1145", minute("1145"), eth["ticker"])]}

    first, _ = timed_pull((4, "1030"), by_series, trades, ["KXBTCD"], DAY4)
    assert [d["trade_id"] for d in data(first)] == ["a10"], "a11 is not visible yet at 10:30"
    second, _ = timed_pull((4, "1200"), by_series, trades, ["KXETHD"], DAY4, state=first[-1]["state"])
    assert [d["trade_id"] for d in data(second)] == ["e1145"]
    assert second[-1]["state"]["trades"]["cursor"] == minute("1145"), "the global cursor moved past a11"
    third, script = timed_pull((4, "1230"), by_series, trades, ["KXBTCD"], DAY4, state=second[-1]["state"])
    assert script.params("/markets/trades")[0]["min_ts"] == CUT_S, "BTC's market was open at 10:30: read whole"
    assert "a11" in [d["trade_id"] for d in data(third)]
    assert set(coverage(third)) == {KEY, ETH_KEY}, "ETH's record is carried, not lost"


def test_a_pull_of_another_window_cannot_hide_a_late_trade_of_this_one():
    m = closing("M", minute("1200"))
    p = closing("P", minute("1200", day=5))
    by_series = {"KXBTCD": [m, p]}
    trades = {m["ticker"]: [trade("m1005", minute("1005"), m["ticker"]), trade("m1105", minute("1105"), m["ticker"])],
              p["ticker"]: [trade("p1115", minute("1115"), p["ticker"])]}

    first, _ = timed_pull((4, "1030"), by_series, trades, ["KXBTCD"], DAY4)
    assert [d["trade_id"] for d in data(first)] == ["m1005"], "m1105 is after the 10:30 capture"
    second, script = timed_pull((4, "1130"), by_series, trades, ["KXBTCD"], DAY5, state=first[-1]["state"])
    assert [d["trade_id"] for d in data(second)] == ["p1115"] and asked(script) == [p["ticker"]]
    assert second[-1]["state"]["trades"]["cursor"] == minute("1115")
    third, script = timed_pull((4, "1230"), by_series, trades, ["KXBTCD"], DAY4, state=second[-1]["state"])
    assert asked(script) == [m["ticker"]] and script.params("/markets/trades")[0]["min_ts"] == CUT_S
    assert "m1105" in [d["trade_id"] for d in data(third)]
    fourth, script = timed_pull((4, "1330"), by_series, trades, ["KXBTCD"], DAY4, state=third[-1]["state"])
    assert asked(script) == [], "captured after it closed, the market is complete"
