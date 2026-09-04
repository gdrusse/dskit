"""libs/kalshi.py: the Kalshi trade-API v2 pack, driven through the contract.

No network anywhere: every test injects the ``getter`` transport, a
recording ``sleeper`` and a fixed ``clock``, so pacing, retry, pagination
and the row shapes above them run for real. The acquisition e2e resolves
:class:`StubKalshiConnector` below by class reference — the platform
instantiates a connector with no arguments, so the stub binds its
scripted transport in ``__init__`` and keeps its script on the class.
"""

import json
import urllib.error
from datetime import datetime, timedelta, timezone

import pytest

from dskit.assets.base import AssetError
from dskit.onboarding import acquire as acquire_module
from dskit.onboarding import (
    check_config,
    check_message,
    load_state,
    resolve_connector,
    run_acquisition,
    scan_stream,
)
from dskit.onboarding.libs import kalshi
from dskit.onboarding.libs.kalshi import KalshiConnector

BASE = "https://kalshi.test/trade-api/v2"
NOW = datetime(2026, 9, 4, 12, 0, 30, tzinfo=timezone.utc)
#: ``NOW`` floored to the minute — the capture instant every undated row carries.
CAPTURE = "2026-09-04T12:00:00+00:00"
SERIES = "KXHIGHNY"
CONFIG = {"series": [SERIES], "base_url": BASE}

SETTLED = {
    "ticker": "KXHIGHNY-26JUN17-B74.5",
    "event_ticker": "KXHIGHNY-26JUN17",
    "strike_type": "between",
    "floor_strike": 74,
    "cap_strike": "75",
    "status": "finalized",
    "result": "yes",
    "open_time": "2026-06-16T14:00:00Z",
    "close_time": "2026-06-17T20:00:00Z",
    "yes_sub_title": "74° to 75°",
    "yes_bid_dollars": "0.9900",
    "yes_ask_dollars": "1.0000",
    "last_price_dollars": "0.9700",
}
SETTLED_ROW = {
    "ticker": "KXHIGHNY-26JUN17-B74.5",
    "event_ticker": "KXHIGHNY-26JUN17",
    "series_ticker": "KXHIGHNY",
    "strike_type": "between",
    "floor_strike": 74.0,
    "cap_strike": 75.0,
    "status": "finalized",
    "result": "yes",
    "open_time": "2026-06-16T14:00:00Z",
    "close_time": "2026-06-17T20:00:00Z",
    "yes_sub_title": "74° to 75°",
    "yes_bid": 0.99,
    "yes_ask": 1.0,
    "last_price": 0.97,
}
OPEN = {
    "ticker": "KXHIGHNY-26SEP05-T80",
    "event_ticker": "KXHIGHNY-26SEP05",
    "strike_type": "greater",
    "floor_strike": "80",
    "status": "open",
    "open_time": "2026-09-04T10:00:00Z",
    "close_time": "2026-09-05T20:00:00Z",
    "subtitle": "80° or above",
    "yes_bid_dollars": "0.4000",
    "yes_ask_dollars": "1.2000",  # outside [0, 1] -> None
}
OPEN_ROW = {
    "ticker": "KXHIGHNY-26SEP05-T80",
    "event_ticker": "KXHIGHNY-26SEP05",
    "series_ticker": "KXHIGHNY",  # absent in the payload -> the ticker's first segment
    "strike_type": "greater",
    "floor_strike": 80.0,
    "cap_strike": None,
    "status": "open",
    "result": "",
    "open_time": "2026-09-04T10:00:00Z",
    "close_time": "2026-09-05T20:00:00Z",
    "yes_sub_title": "80° or above",
    "yes_bid": 0.4,
    "yes_ask": None,
    "last_price": None,
}
SERIES_BODY = {"series": {
    "ticker": SERIES, "fee_type": "quadratic", "fee_multiplier": 1,
    "title": "Highest temperature in NYC", "category": "Climate and Weather",
    "frequency": "daily",
}}


def unix(iso):
    return int(datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp())


def candle(end_iso, close="0.55"):
    return {
        "end_period_ts": unix(end_iso),
        "price": {"open_dollars": "0.50", "high_dollars": "0.60",
                  "low_dollars": "0.40", "close_dollars": close,
                  "mean_dollars": "0.52"},
        "yes_bid": {"close_dollars": "0.54"},
        "yes_ask": {"close_dollars": "0.56"},
        "volume_fp": "12.5",
        "open_interest_fp": "100",
    }


def http_error(code, headers=None):
    return urllib.error.HTTPError(BASE, code, "scripted", headers or {}, None)


def markets_pages(params):
    """Two settled pages chained by cursor, then one open page."""
    if params["status"] == "settled":
        if "cursor" not in params:
            return {"markets": [SETTLED], "cursor": "page-2"}
        assert params["cursor"] == "page-2"
        return {"markets": [dict(SETTLED, ticker="KXHIGHNY-26JUN16-B73.5",
                                 event_ticker="KXHIGHNY-26JUN16")], "cursor": ""}
    assert params["status"] == "open"
    return {"markets": [OPEN], "cursor": ""}


class Script:
    """A scripted ``getter(url, params)`` routed by path under BASE.

    A route value is a body dict, an Exception instance to raise, a
    callable of ``params`` returning either, or a list of those consumed
    in order (a response queue). Every call is recorded.
    """

    def __init__(self, routes):
        self.routes = dict(routes)
        self.calls = []

    def __call__(self, url, params):
        assert url.startswith(BASE + "/"), url
        path = url[len(BASE):]
        self.calls.append((path, dict(params)))
        handler = self.routes.get(path)
        assert handler is not None, f"unexpected request {path} {params}"
        if isinstance(handler, list):
            assert handler, f"response queue for {path} exhausted"
            handler = handler.pop(0)
        if callable(handler):
            handler = handler(params)
        if isinstance(handler, Exception):
            raise handler
        return handler

    def paths(self):
        return [path for path, _params in self.calls]


ROUTES = {
    "/markets": markets_pages,
    f"/series/{SERIES}": SERIES_BODY,
}


def connector(routes=ROUTES, now=NOW):
    """A connector over a scripted transport; returns (connector, script, sleeps)."""
    script = Script(routes)
    sleeps = []
    conn = KalshiConnector(getter=script, sleeper=sleeps.append, clock=lambda: now)
    return conn, script, sleeps


def read(conn, streams, config=CONFIG, state=None, mode="backfill"):
    msgs = list(conn.read(config, streams, state or {}, mode))
    for msg in msgs:
        assert check_message(msg) is not None  # every message envelope-valid
    return msgs


def records(msgs):
    return [m for m in msgs if m["type"] == "RECORD"]


# -- spec / knobs -----------------------------------------------------------


def test_spec_passes_its_own_gate():
    conn = KalshiConnector()
    check_config(conn, CONFIG)
    check_config(conn, {**CONFIG, "notes": "documentation is always allowed"})
    with pytest.raises(AssetError, match="unknown key"):
        check_config(conn, {**CONFIG, "surprise": 1})
    with pytest.raises(AssetError, match="required knob"):
        check_config(conn, {"base_url": BASE})


def test_bad_knob_shapes_refused():
    conn = KalshiConnector()
    bad = {
        "series": ([], ["KXA", ""], "KXA", ["KXA", "KXA"]),
        "statuses": ([], "open", [1]),
        "limit": (0, 1.5, True),
        "max_pages": (0,),
        "period_interval": (0,),
        "retries": (-1, 0.5),
        "pace_s": (-0.1, "fast", "0.5"),  # a numeric STRING is refused, never compared
        "timeout_s": (0, -1, "30"),
        "base_url": ("ftp://kalshi", 5),
    }
    for knob, values in bad.items():
        for value in values:
            with pytest.raises(AssetError, match=f"config.{knob}"):
                conn.resolve_knobs({**CONFIG, knob: value})
    with pytest.raises(AssetError, match="config must be a dict"):
        conn.resolve_knobs("nope")


def test_every_default_has_one_name(monkeypatch):
    # Each default lives in ONE module constant read by resolve_knobs AND
    # by the spec() notes: rebind them and watch both move. A call site
    # that hardcoded the literal would keep the old value here and go red.
    pins = {
        "statuses": ("DEFAULT_STATUSES", ("settled", "open"), ["closed"]),
        "limit": ("DEFAULT_LIMIT", 1000, 77),
        "max_pages": ("DEFAULT_MAX_PAGES", 10000, 5),
        "period_interval": ("DEFAULT_PERIOD_INTERVAL", 60, 1440),
        "pace_s": ("DEFAULT_PACE_S", 0.2, 0.9),
        "retries": ("DEFAULT_RETRIES", 4, 7),
        "timeout_s": ("DEFAULT_TIMEOUT_S", 30, 99),
        "base_url": ("DEFAULT_BASE_URL",
                     "https://external-api.kalshi.com/trade-api/v2",
                     "https://elsewhere.test/v2"),
    }
    conn = KalshiConnector()
    for knob, (name, current, _sentinel) in pins.items():
        assert getattr(kalshi, name) == current, name
    for knob, (name, _current, sentinel) in pins.items():
        monkeypatch.setattr(kalshi, name, sentinel)
    knobs = conn.resolve_knobs({"series": [SERIES]})
    notes = conn.spec()["params"]
    for knob, (_name, _current, sentinel) in pins.items():
        assert knobs[knob] == (list(sentinel) if knob == "statuses" else sentinel)
        shown = list(sentinel) if knob == "statuses" else sentinel
        assert f"default {shown}" in notes[knob]["notes"], knob


def test_constructor_refuses_non_callables():
    with pytest.raises(AssetError, match="getter must be callable"):
        KalshiConnector(getter="nope")
    with pytest.raises(AssetError, match="clock must be callable"):
        KalshiConnector(clock=3)


# -- check / discover -------------------------------------------------------


def test_check_pings_the_first_series_once():
    conn, script, sleeps = connector()
    conn.check({"series": ["KXHIGHNY", "KXOTHER"], "base_url": BASE})
    assert script.calls == [(f"/series/{SERIES}", {})]
    assert sleeps == []  # the connector's first request is never paced


def test_check_surfaces_a_failed_ping():
    conn, script, _ = connector({f"/series/{SERIES}": http_error(404)})
    with pytest.raises(AssetError, match="HTTP 404"):
        conn.check(CONFIG)
    assert len(script.calls) == 1  # a client error never retries


def test_discover_declares_four_streams_offline():
    conn, script, _ = connector({})
    streams = conn.discover(CONFIG)
    assert script.calls == []
    assert [s["stream"] for s in streams] == [
        "candles", "fee_schedules", "markets", "orderbooks"]
    by_name = {s["stream"]: s for s in streams}
    assert by_name["markets"]["primary_key"] == ["ticker"]
    assert by_name["candles"]["primary_key"] == ["ticker", "ts"]
    assert by_name["fee_schedules"]["primary_key"] == ["series_ticker", "retrieved"]
    assert by_name["orderbooks"]["primary_key"] == ["ticker", "captured_at"]
    assert by_name["markets"]["schema"] == {"fields": list(SETTLED_ROW)}
    assert len(by_name["markets"]["schema"]["fields"]) == 14
    assert by_name["candles"]["schema"]["fields"] == [
        "ticker", "ts", "open", "high", "low", "close", "mean",
        "yes_bid_close", "yes_ask_close", "volume", "open_interest"]
    assert by_name["fee_schedules"]["schema"]["fields"] == [
        "series_ticker", "fee_type", "fee_multiplier", "title", "category",
        "frequency", "retrieved"]
    assert by_name["orderbooks"]["schema"]["fields"] == [
        "ticker", "event_ticker", "series_ticker", "captured_at", "yes_bids",
        "no_bids", "strike_type", "floor_strike", "cap_strike", "close_time"]
    with pytest.raises(AssetError, match="config.series"):
        conn.discover({"series": []})


def test_registered_kind_resolves():
    assert resolve_connector("kalshi") is KalshiConnector


# -- read: contract basics --------------------------------------------------


def test_read_validates_its_arguments():
    conn, _, _ = connector()
    with pytest.raises(AssetError, match="state must be a dict"):
        list(conn.read(CONFIG, ["markets"], [], "live"))
    with pytest.raises(AssetError, match="state.markets must be a dict"):
        list(conn.read(CONFIG, ["markets"], {"markets": "x"}, "live"))
    with pytest.raises(AssetError, match="streams must be a non-empty list"):
        list(conn.read(CONFIG, [], {}, "live"))
    with pytest.raises(AssetError, match="mode must be one of"):
        list(conn.read(CONFIG, ["markets"], {}, "nightly"))
    with pytest.raises(AssetError, match="unknown stream.*ghost"):
        list(conn.read(CONFIG, ["markets", "ghost"], {}, "live"))


@pytest.mark.parametrize("mode", ["backfill", "live"])
def test_read_emits_schema_records_state_in_either_mode(mode):
    conn, _, _ = connector()
    msgs = read(conn, ["fee_schedules"], mode=mode)
    assert [m["type"] for m in msgs] == ["SCHEMA", "RECORD", "STATE"]
    assert msgs[0]["schema"] == {"fields": list(kalshi.FEE_FIELDS)}
    assert all(m["kind"] == "observation" for m in records(msgs))


def test_read_serves_several_streams_in_one_pass():
    conn, script, _ = connector()
    msgs = read(conn, ["fee_schedules", "markets"])
    assert [m["type"] for m in msgs] == [
        "SCHEMA", "RECORD", "SCHEMA", "RECORD", "RECORD", "RECORD", "STATE"]
    assert set(msgs[-1]["state"]) == {"fee_schedules", "markets"}
    assert script.paths()[0] == f"/series/{SERIES}"


# -- markets ----------------------------------------------------------------


def test_markets_paginate_by_cursor_across_both_statuses():
    conn, script, _ = connector()
    msgs = read(conn, ["markets"])
    common = {"series_ticker": SERIES, "limit": 1000}
    assert script.calls == [
        ("/markets", {**common, "status": "settled"}),  # None cursor dropped
        ("/markets", {**common, "status": "settled", "cursor": "page-2"}),
        ("/markets", {**common, "status": "open"}),
    ]
    tickers = [m["data"]["ticker"] for m in records(msgs)]
    assert tickers == ["KXHIGHNY-26JUN17-B74.5", "KXHIGHNY-26JUN16-B73.5",
                       "KXHIGHNY-26SEP05-T80"]


def test_markets_row_is_the_fourteen_field_shape():
    conn, _, _ = connector()
    recs = records(read(conn, ["markets"]))
    assert recs[0]["data"] == SETTLED_ROW
    assert recs[2]["data"] == OPEN_ROW
    for rec in recs:
        json.dumps(rec["data"], allow_nan=False)  # every row is serializable


def test_markets_effective_is_close_time_or_the_capture_minute():
    # A closed market describes itself as of its close; an open market's
    # close lies in the FUTURE, which the platform refuses on an
    # observation, so its row is dated at the pull's capture minute.
    conn, _, _ = connector()
    msgs = read(conn, ["markets"])
    effs = [m["effective_date"] for m in records(msgs)]
    assert effs == ["2026-06-17T20:00:00Z", "2026-06-17T20:00:00Z", CAPTURE]
    assert msgs[-1]["state"] == {"markets": {"cursor": CAPTURE}}


def test_markets_never_filter_by_cursor_but_the_cursor_never_regresses():
    conn, _, _ = connector()
    later = "2026-09-04T13:00:00+00:00"
    msgs = read(conn, ["markets"], state={"markets": {"cursor": later}})
    assert len(records(msgs)) == 3  # a full re-pull, by design
    assert msgs[-1]["state"] == {"markets": {"cursor": later}}


def test_markets_honor_the_statuses_and_series_knobs():
    conn, script, _ = connector({"/markets": lambda p: {"markets": [], "cursor": ""}})
    config = {**CONFIG, "series": ["KXA", "KXB"], "statuses": ["closed"]}
    msgs = read(conn, ["markets"], config=config)
    assert [(p["series_ticker"], p["status"]) for _, p in script.calls] == [
        ("KXA", "closed"), ("KXB", "closed")]
    assert records(msgs) == []
    assert msgs[-1]["state"] == {"markets": {"cursor": ""}}


def test_markets_refuse_a_stuck_or_truncated_walk():
    conn, _, _ = connector({"/markets": lambda p: {"markets": [OPEN], "cursor": "same"}})
    with pytest.raises(AssetError, match="did not advance"):
        list(conn.read(CONFIG, ["markets"], {}, "live"))

    pages = iter(range(10 ** 6))
    conn, _, _ = connector(
        {"/markets": lambda p: {"markets": [OPEN], "cursor": f"c{next(pages)}"}})
    with pytest.raises(AssetError, match="still paging after 3 page"):
        list(conn.read({**CONFIG, "max_pages": 3}, ["markets"], {}, "live"))


def test_markets_refuse_a_row_without_a_ticker():
    conn, _, _ = connector({"/markets": {"markets": [{"event_ticker": "X"}], "cursor": ""}})
    with pytest.raises(AssetError, match="lacks a ticker"):
        list(conn.read(CONFIG, ["markets"], {}, "live"))
    conn, _, _ = connector({"/markets": {"markets": {"not": "a list"}}})
    with pytest.raises(AssetError, match="'markets' is not a list"):
        list(conn.read(CONFIG, ["markets"], {}, "live"))


# -- candles ----------------------------------------------------------------


def candle_routes(settled_candles, open_candles):
    return {
        "/markets": markets_pages,
        f"/series/{SERIES}/markets/{SETTLED['ticker']}/candlesticks":
            {"candlesticks": settled_candles},
        f"/series/{SERIES}/markets/KXHIGHNY-26JUN16-B73.5/candlesticks":
            {"candlesticks": []},
        f"/series/{SERIES}/markets/{OPEN['ticker']}/candlesticks":
            {"candlesticks": open_candles},
    }


def test_candles_window_each_market_and_shape_rows():
    conn, script, _ = connector(candle_routes(
        [candle("2026-06-17T19:00:00Z"), candle("2026-06-17T20:00:00Z")],
        [candle("2026-09-04T11:00:00Z", close="0.41")],
    ))
    msgs = read(conn, ["candles"])
    windows = {p.split("/")[4]: q for p, q in script.calls if "candlesticks" in p}
    assert windows[SETTLED["ticker"]] == {
        "start_ts": unix(SETTLED["open_time"]), "end_ts": unix(SETTLED["close_time"]),
        "period_interval": 60}
    assert windows[OPEN["ticker"]]["end_ts"] == unix(OPEN["close_time"])
    recs = records(msgs)
    assert recs[0]["data"] == {
        "ticker": SETTLED["ticker"], "ts": unix("2026-06-17T19:00:00Z"),
        "open": 0.5, "high": 0.6, "low": 0.4, "close": 0.55, "mean": 0.52,
        "yes_bid_close": 0.54, "yes_ask_close": 0.56,
        "volume": 12.5, "open_interest": 100.0}
    assert recs[0]["effective_date"] == "2026-06-17T19:00:00+00:00"
    assert recs[2]["data"]["close"] == 0.41
    assert msgs[-1]["state"] == {"candles": {"cursor": "2026-09-04T11:00:00+00:00"}}


def test_candles_skip_markets_closed_at_or_before_the_cursor():
    conn, script, _ = connector(candle_routes(
        [candle("2026-06-17T20:00:00Z")], [candle("2026-09-04T11:00:00Z")]))
    state = {"candles": {"cursor": SETTLED["close_time"]}}
    msgs = read(conn, ["candles"], state=state)
    requested = [p for p in script.paths() if "candlesticks" in p]
    assert requested == [f"/series/{SERIES}/markets/{OPEN['ticker']}/candlesticks"]
    assert [m["data"]["ticker"] for m in records(msgs)] == [OPEN["ticker"]]


def test_candles_drop_the_forming_candle_and_take_the_fallback_window():
    undated = dict(OPEN, ticker="KXHIGHNY-UNDATED", open_time="", close_time="not-a-date")
    conn, script, _ = connector({
        "/markets": {"markets": [undated], "cursor": ""},
        f"/series/{SERIES}/markets/KXHIGHNY-UNDATED/candlesticks": {"candlesticks": [
            candle("2026-09-04T12:00:00Z"),  # ends AT the capture minute: kept
            candle("2026-09-04T13:00:00Z"),  # still forming: dropped
        ]},
    })
    msgs = read(conn, ["candles"], config={**CONFIG, "statuses": ["open"],
                                           "period_interval": 1})
    _path, query = [c for c in script.calls if "candlesticks" in c[0]][0]
    end = unix(CAPTURE)
    assert query == {"start_ts": end - 14 * 86400, "end_ts": end, "period_interval": 1}
    assert [m["effective_date"] for m in records(msgs)] == [CAPTURE]


def test_candles_refuse_malformed_payloads():
    conn, _, _ = connector({
        "/markets": {"markets": [OPEN], "cursor": ""},
        f"/series/{SERIES}/markets/{OPEN['ticker']}/candlesticks":
            {"candlesticks": [{"price": {}}]},
    })
    with pytest.raises(AssetError, match="end_period_ts"):
        list(conn.read(CONFIG, ["candles"], {}, "live"))
    conn, _, _ = connector({
        "/markets": {"markets": [OPEN], "cursor": ""},
        f"/series/{SERIES}/markets/{OPEN['ticker']}/candlesticks":
            {"candlesticks": {"not": "a list"}},
    })
    with pytest.raises(AssetError, match="'candlesticks' is not a list"):
        list(conn.read(CONFIG, ["candles"], {}, "live"))


# -- fee_schedules ----------------------------------------------------------


def test_fee_schedules_record_the_series_payload_at_the_capture_minute():
    conn, script, _ = connector()
    msgs = read(conn, ["fee_schedules"])
    (rec,) = records(msgs)
    assert rec["data"] == {
        "series_ticker": SERIES, "fee_type": "quadratic", "fee_multiplier": 1.0,
        "title": "Highest temperature in NYC", "category": "Climate and Weather",
        "frequency": "daily", "retrieved": CAPTURE}
    assert rec["effective_date"] == CAPTURE
    assert msgs[-1]["state"] == {"fee_schedules": {"cursor": CAPTURE}}
    assert script.calls == [(f"/series/{SERIES}", {})]
    # No cursor filtering: a schedule can change, so every pull re-pulls.
    again = read(conn, ["fee_schedules"], state=msgs[-1]["state"])
    assert len(records(again)) == 1


def test_fee_schedules_refuse_a_payload_without_series():
    conn, _, _ = connector({f"/series/{SERIES}": {"nope": 1}})
    with pytest.raises(AssetError, match="'series' object"):
        list(conn.read(CONFIG, ["fee_schedules"], {}, "live"))


# -- orderbooks -------------------------------------------------------------


FP_BOOK = {"orderbook_fp": {
    "yes_dollars": [["0.40", "10.5"], ["0.42", "3"], ["bad"], ["1.50", "2"],
                    ["0.30", "0"], ["x", "1"], "junk", ["0.35", "-1"]],
    "no_dollars": [["0.55", "7"], ["0.58", 2]],
}}
CENTS_BOOK = {"orderbook": {"yes": [[40, 10], [42, 3], [101, 1]], "no": [[55, 7]]}}


def book_routes(book):
    return {
        "/markets": {"markets": [OPEN], "cursor": ""},
        f"/markets/{OPEN['ticker']}/orderbook": book,
    }


def test_orderbooks_capture_open_markets_from_the_fp_book():
    conn, script, _ = connector(book_routes(FP_BOOK))
    msgs = read(conn, ["orderbooks"])
    assert script.calls[0] == ("/markets", {
        "series_ticker": SERIES, "status": "open", "limit": 1000})  # OPEN only
    (rec,) = records(msgs)
    assert rec["data"] == {
        "ticker": OPEN["ticker"], "event_ticker": OPEN["event_ticker"],
        "series_ticker": SERIES, "captured_at": CAPTURE,
        "yes_bids": [[0.42, 3.0], [0.4, 10.5]],  # malformed/out-of-range dropped, best first
        "no_bids": [[0.58, 2.0], [0.55, 7.0]],
        "strike_type": "greater", "floor_strike": 80.0, "cap_strike": None,
        "close_time": OPEN["close_time"]}
    assert rec["effective_date"] == CAPTURE
    assert msgs[-1]["state"] == {"orderbooks": {"cursor": CAPTURE}}


def test_orderbooks_fall_back_to_the_cents_book():
    conn, _, _ = connector(book_routes(CENTS_BOOK))
    (rec,) = records(read(conn, ["orderbooks"]))
    assert rec["data"]["yes_bids"] == [[0.42, 3.0], [0.4, 10.0]]
    assert rec["data"]["no_bids"] == [[0.55, 7.0]]


def test_orderbooks_prefer_fp_and_tolerate_an_empty_book():
    both = {**CENTS_BOOK, "orderbook_fp": {"yes_dollars": [["0.20", "1"]], "no_dollars": None}}
    conn, _, _ = connector(book_routes(both))
    (rec,) = records(read(conn, ["orderbooks"]))
    assert rec["data"]["yes_bids"] == [[0.2, 1.0]] and rec["data"]["no_bids"] == []
    conn, _, _ = connector(book_routes({}))
    (rec,) = records(read(conn, ["orderbooks"]))
    assert rec["data"]["yes_bids"] == [] and rec["data"]["no_bids"] == []


# -- transport: retry, pacing, the default getter --------------------------


def test_retry_on_429_honors_retry_after_then_succeeds():
    conn, script, sleeps = connector({
        f"/series/{SERIES}": [http_error(429, {"Retry-After": "3"}), SERIES_BODY]})
    conn.check(CONFIG)
    assert len(script.calls) == 2
    assert sleeps == [3.0]


def test_retry_backs_off_exponentially_on_5xx_and_network_errors():
    conn, script, sleeps = connector({f"/series/{SERIES}": [
        http_error(503), urllib.error.URLError("connection refused"),
        http_error(500, {"Retry-After": "soon"}), SERIES_BODY]})
    conn.check(CONFIG)
    assert len(script.calls) == 4
    assert sleeps == [0.5, 1.0, 2.0]  # a non-numeric Retry-After falls back


def test_retries_exhausted_names_the_url():
    conn, script, sleeps = connector({f"/series/{SERIES}": [http_error(429)] * 3})
    with pytest.raises(AssetError, match=r"giving up.*after 2 attempt") as exc:
        conn.check({**CONFIG, "retries": 1})
    assert f"{BASE}/series/{SERIES}" in str(exc.value)
    assert "HTTP 429" in str(exc.value)
    assert len(script.calls) == 2 and sleeps == [0.5]

    conn, script, _ = connector({"/markets": [urllib.error.URLError("dns")]})
    with pytest.raises(AssetError, match="network error") as exc:
        list(conn.read({**CONFIG, "retries": 0}, ["markets"], {}, "live"))
    assert "/markets?series_ticker=KXHIGHNY" in str(exc.value)  # the query, named
    assert len(script.calls) == 1


def test_non_json_and_non_object_bodies_are_refused_not_retried():
    conn, script, _ = connector({f"/series/{SERIES}": ValueError("Expecting value")})
    with pytest.raises(AssetError, match="not JSON"):
        conn.check(CONFIG)
    conn, script, _ = connector({f"/series/{SERIES}": lambda p: ["a", "list"]})
    with pytest.raises(AssetError, match="not a JSON object"):
        conn.check(CONFIG)
    assert len(script.calls) == 1


def test_pacing_sleeps_between_requests_only():
    conn, script, sleeps = connector({
        "/series/KXA": SERIES_BODY, "/series/KXB": SERIES_BODY, "/series/KXC": SERIES_BODY})
    read(conn, ["fee_schedules"], config={**CONFIG, "series": ["KXA", "KXB", "KXC"]})
    assert len(script.calls) == 3
    assert sleeps == [0.2, 0.2]  # never before the first request

    conn, _, sleeps = connector({"/series/KXA": SERIES_BODY, "/series/KXB": SERIES_BODY})
    read(conn, ["fee_schedules"],
         config={**CONFIG, "series": ["KXA", "KXB"], "pace_s": 0})
    assert sleeps == []


def test_default_transport_is_stdlib_urllib(monkeypatch):
    import urllib.request

    seen = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return json.dumps(SERIES_BODY).encode("utf-8")

    def urlopen(request, timeout):
        seen["url"] = request.full_url
        seen["headers"] = dict(request.header_items())
        seen["timeout"] = timeout
        return Response()

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    conn = KalshiConnector(sleeper=lambda s: None)
    conn.check({**CONFIG, "timeout_s": 7})
    assert seen["url"] == f"{BASE}/series/{SERIES}"
    assert seen["timeout"] == 7
    assert seen["headers"] == {"User-agent": "dskit-onboarding",
                               "Accept": "application/json"}

    body = conn._http_get(f"{BASE}/markets", {"limit": 5, "status": "open"}, 7)
    assert body == SERIES_BODY
    assert seen["url"] == f"{BASE}/markets?limit=5&status=open"


# -- acquisition e2e --------------------------------------------------------


class StubKalshiConnector(KalshiConnector):
    """The pack over a class-level script — acquisition resolves a fresh
    instance with no arguments, so the transport is bound in ``__init__``
    from state the test module owns."""

    script = None
    now = NOW

    def __init__(self):
        super().__init__(getter=type(self).script, sleeper=lambda s: None,
                         clock=lambda: type(self).now)


@pytest.fixture
def kalshi_source(registry):
    vid = registry.register("source_config", {
        "name": "kalshi",
        "catalog_source": "kalshi-src",
        "connector": "tests.onboarding.test_kalshi:StubKalshiConnector",
        "config": CONFIG,
    }, origin="test")
    registry.transition(vid, "active", origin="test")
    StubKalshiConnector.script = Script(ROUTES)
    yield vid
    StubKalshiConnector.script = None


def test_acquisition_repulls_markets_and_dedup_keeps_the_settled_row(
        root, registry, kalshi_source, monkeypatch):
    # The platform stamps acquired_at just before each read(); the stub
    # clock moves two days between the pulls, so the stamps follow it.
    stamps = iter(["2026-09-04T12:00:45+00:00", "2026-09-06T12:00:45+00:00"])
    monkeypatch.setattr(acquire_module, "utc_now", lambda: next(stamps))

    first = run_acquisition(root, registry, "kalshi", "markets", "backfill")
    assert first["records"] == 3 and first["snapshot"] is not None
    assert first["state_saved"]
    assert load_state(root, "kalshi", "markets", "backfill") == {
        "markets": {"cursor": CAPTURE}}
    # check() pinged, then the three market pages — through the stub transport.
    assert StubKalshiConnector.script.paths() == [
        f"/series/{SERIES}", "/markets", "/markets", "/markets"]

    # The open market settles: the SAME ticker comes back finalized, and
    # the stream re-emits every market rather than filtering by cursor.
    settled_now = dict(OPEN, status="finalized", result="no",
                       yes_bid_dollars="0.0000", yes_ask_dollars="0.0100")
    StubKalshiConnector.now = NOW + timedelta(days=2)
    StubKalshiConnector.script = Script({
        **ROUTES,
        "/markets": lambda p: (
            {"markets": [settled_now], "cursor": ""} if p["status"] == "settled"
            else {"markets": [], "cursor": ""}),
    })
    second = run_acquisition(root, registry, "kalshi", "markets", "backfill")
    assert second["records"] == 1 and second["snapshot"] != first["snapshot"]

    rows = scan_stream(root.root, "kalshi", "markets", key_fields=("ticker",))
    by_ticker = {row["ticker"]: row for row in rows}
    assert len(by_ticker) == 3
    assert by_ticker[OPEN["ticker"]]["result"] == "no"  # latest acquisition wins
    assert by_ticker[OPEN["ticker"]]["status"] == "finalized"
    assert by_ticker[SETTLED["ticker"]] == SETTLED_ROW
    StubKalshiConnector.now = NOW
