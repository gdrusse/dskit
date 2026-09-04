"""libs/predexon.py: Predexon Kalshi L2 history through the connector contract.

No network and no waiting anywhere: the getter, clock, and sleeper are
injected, so pacing, retry, pagination, normalization, and cursor logic
all run for real against scripted pages. The one exception is the
default urllib getter, exercised against a loopback ``http.server``.
"""

import json
import threading
import urllib.parse
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from dskit.assets.base import AssetError
from dskit.onboarding import (
    check_config,
    check_message,
    parse_utc,
    resolve_connector,
    run_acquisition,
)
from dskit.onboarding.libs import predexon
from dskit.onboarding.libs.predexon import (
    L2_FIELDS,
    L2_KEY_FIELDS,
    L2_STREAM,
    ORDERBOOKS_PATH,
    PredexonConnector,
    native_book,
    urllib_get,
)

from .conftest import norm_read

TICKER = "KXHIGHNY-26MAR01-B50"
OTHER = "KXHIGHNY-26MAR01-B52"
KEY_ENV = "PREDEXON_API_KEY"
KEY_VALUE = "k-123-SECRET"

CONFIG = {
    "tickers": [TICKER, OTHER],
    "coverage_start": "2026-03-01",
    "end": "2026-03-02",
}


EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def _ms(iso):
    # Integer arithmetic on purpose: a float epoch times 1000 can land one
    # millisecond short, and every window bound below must be exact.
    return (parse_utc(iso) - EPOCH) // timedelta(milliseconds=1)


START_MS = _ms("2026-03-01")
END_MS = _ms("2026-03-02")
TS1 = _ms("2026-03-01T12:00:00.123")
TS2 = _ms("2026-03-01T12:00:01")
TS3 = _ms("2026-03-01T12:00:02.500")


def snap(ts, seq, bids=((55, 10), (50, 4)), asks=((57, 5), (60, 2))):
    """One vendor-shaped snapshot: integer cents, dict levels."""
    return {
        "timestamp": ts, "sequence": seq,
        "best_bid": 55, "best_ask": 57, "bid_depth": 100, "ask_depth": 80,
        "yes_bids": [{"price": p, "size": s} for p, s in bids],
        "yes_asks": [{"price": p, "size": s} for p, s in asks],
    }


def page(snaps, key=None, has_more=False):
    return {"data": snaps, "pagination": {"pagination_key": key, "has_more": has_more}}


def query(ticker, start=START_MS, end=END_MS, limit=200, key=None):
    params = {"ticker": ticker, "start_time": start, "end_time": end, "limit": limit}
    if key is not None:
        params["pagination_key"] = key
    return params


def _key(path, params):
    return path, tuple(sorted(params.items()))


class FakeClock:
    """An epoch-seconds clock that moves only when told to."""

    def __init__(self, start):
        self.t = float(start)

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds


class Sleeper:
    """Records every requested sleep and advances the fake clock by it."""

    def __init__(self, clock):
        self.clock = clock
        self.calls = []

    def __call__(self, seconds):
        self.calls.append(seconds)
        self.clock.advance(seconds)


class ScriptedGetter:
    """A transport keyed by (path, params); each key holds a response queue.

    A response is a page dict (-> 200, no headers), a
    ``(status, headers, body_obj)`` tuple, or an Exception to raise.
    ``work_s`` seconds of fake time elapse inside every call.
    """

    def __init__(self, clock=None, work_s=0.0):
        self.responses = {}
        self.calls = []
        self.clock = clock
        self.work_s = work_s

    def script(self, params, *responses, path=ORDERBOOKS_PATH):
        self.responses.setdefault(_key(path, params), []).extend(responses)

    def __call__(self, url, params, headers, timeout):
        path = urllib.parse.urlsplit(url).path
        self.calls.append((url, dict(params), dict(headers), timeout))
        if self.clock is not None and self.work_s:
            self.clock.advance(self.work_s)
        queue = self.responses.get(_key(path, params))
        assert queue, f"unexpected request {path} {params}"
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        if isinstance(item, dict):
            return 200, {}, json.dumps(item).encode("utf-8")
        status, hdrs, obj = item
        return status, hdrs, json.dumps(obj).encode("utf-8")


@pytest.fixture
def clock():
    # "now" sits exactly at the declared end, so a config without `end`
    # resolves the same window as one with it.
    return FakeClock(END_MS / 1000.0)


@pytest.fixture
def sleeper(clock):
    return Sleeper(clock)


@pytest.fixture
def getter(clock):
    return ScriptedGetter(clock)


@pytest.fixture
def conn(getter, clock, sleeper):
    return PredexonConnector(getter=getter, clock=clock, sleeper=sleeper)


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv(KEY_ENV, KEY_VALUE)


def _read(conn, config=CONFIG, state=None, mode="backfill"):
    msgs = list(conn.read(config, [L2_STREAM], state or {}, mode))
    for m in msgs:
        assert check_message(m) is not None
    return msgs


def _records(msgs):
    return [m for m in msgs if m["type"] == "RECORD"]


def _logs(msgs):
    return [m["message"] for m in msgs if m["type"] == "LOG"]


def _calls_for(getter, ticker):
    return [c for c in getter.calls if c[1]["ticker"] == ticker]


# -- spec / knobs -----------------------------------------------------------


def test_spec_passes_its_own_gate(conn):
    check_config(conn, CONFIG)
    check_config(conn, {**CONFIG, "notes": "documentation is always allowed"})
    with pytest.raises(AssetError, match="unknown key"):
        check_config(conn, {**CONFIG, "surprise": 1})
    with pytest.raises(AssetError, match="required knob"):
        check_config(conn, {"tickers": [TICKER]})
    with pytest.raises(AssetError, match="required knob"):
        check_config(conn, {"coverage_start": "2026-03-01"})
    with pytest.raises(AssetError, match="secret knob"):
        check_config(conn, {**CONFIG, "api_key_env": 5})


@pytest.mark.parametrize("bad, needle", [
    ({"limit": 0}, "config.limit"),
    ({"limit": 201}, "config.limit"),
    ({"limit": "200"}, "config.limit"),
    ({"limit": True}, "config.limit"),
    ({"tickers": []}, "config.tickers"),
    ({"tickers": "KX"}, "config.tickers"),
    ({"tickers": [1]}, "config.tickers"),
    ({"tickers": [TICKER, TICKER]}, "must not repeat"),
    ({"coverage_start": "yesterday"}, "coverage_start"),
    ({"coverage_start": 20260301}, "coverage_start"),
    ({"end": "2026-03-01"}, "must be after"),
    ({"end": 5}, "config.end"),
    ({"min_interval_s": -1}, "min_interval_s"),
    ({"min_interval_s": "1"}, "min_interval_s"),
    ({"retries": 0}, "config.retries"),
    ({"retries": 2.5}, "config.retries"),
    ({"retry_floor_s": -0.1}, "retry_floor_s"),
    ({"max_pages": 0}, "max_pages"),
    ({"timeout_s": 0}, "timeout_s"),
    ({"base_url": "ftp://nope"}, "base_url"),
    ({"api_key_env": ""}, "api_key_env"),
])
def test_knob_shapes_refused(conn, bad, needle):
    with pytest.raises(AssetError, match=needle):
        conn.resolve_knobs({**CONFIG, **bad})


def test_config_must_be_a_dict(conn):
    with pytest.raises(AssetError, match="config must be a dict"):
        conn.resolve_knobs(["tickers"])


def test_every_default_has_one_name(conn, monkeypatch):
    knobs = conn.resolve_knobs(CONFIG)
    assert knobs["base_url"] == predexon.DEFAULT_BASE_URL == "https://api.predexon.com"
    assert knobs["api_key_env"] == predexon.DEFAULT_KEY_ENV == KEY_ENV
    assert knobs["limit"] == predexon.DEFAULT_LIMIT == 200
    assert knobs["min_interval_s"] == predexon.DEFAULT_MIN_INTERVAL_S == 1.0
    assert knobs["retries"] == predexon.DEFAULT_RETRIES == 3
    assert knobs["retry_floor_s"] == predexon.DEFAULT_RETRY_FLOOR_S == 2.0
    assert knobs["max_pages"] == predexon.DEFAULT_MAX_PAGES == 50
    assert knobs["timeout_s"] == predexon.DEFAULT_TIMEOUT_S
    assert predexon.SERVER_FAULT_ATTEMPTS_FLOOR == 6
    assert predexon.LIMIT_BOUNDS == (1, 200)

    # Rebind each constant: the resolved knob AND the advertised default
    # must both move, or a call site hardcoded the literal.
    rebound = {
        "DEFAULT_BASE_URL": ("base_url", "https://mirror.test"),
        "DEFAULT_KEY_ENV": ("api_key_env", "PX_KEY"),
        "DEFAULT_LIMIT": ("limit", 50),
        "DEFAULT_MIN_INTERVAL_S": ("min_interval_s", 0.25),
        "DEFAULT_RETRIES": ("retries", 4),
        "DEFAULT_RETRY_FLOOR_S": ("retry_floor_s", 1.5),
        "DEFAULT_MAX_PAGES": ("max_pages", 7),
        "DEFAULT_TIMEOUT_S": ("timeout_s", 9),
    }
    for constant, (knob, value) in rebound.items():
        monkeypatch.setattr(predexon, constant, value)
    knobs = conn.resolve_knobs(CONFIG)
    params = conn.spec()["params"]
    for constant, (knob, value) in rebound.items():
        assert knobs[knob] == value, knob
        assert f"default {value}" in params[knob]["notes"], knob
    assert "6" in params["retries"]["notes"]
    monkeypatch.setattr(predexon, "SERVER_FAULT_ATTEMPTS_FLOOR", 9)
    assert "9" in conn.spec()["params"]["retries"]["notes"]


# -- credentials ------------------------------------------------------------


def test_missing_key_refused_by_variable_name(conn, getter, monkeypatch):
    monkeypatch.delenv(KEY_ENV, raising=False)
    with pytest.raises(AssetError, match=KEY_ENV):
        conn.check(CONFIG)
    with pytest.raises(AssetError, match=KEY_ENV):
        list(conn.read(CONFIG, [L2_STREAM], {}, "backfill"))
    monkeypatch.setenv(KEY_ENV, "")  # empty is absent
    with pytest.raises(AssetError, match=KEY_ENV):
        conn.check(CONFIG)
    monkeypatch.delenv("MY_PX_KEY", raising=False)
    with pytest.raises(AssetError, match="MY_PX_KEY"):
        conn.check({**CONFIG, "api_key_env": "MY_PX_KEY"})
    assert getter.calls == []  # refused before any request


def test_the_key_travels_only_in_the_header(conn, getter, env):
    getter.script(query(TICKER), page([snap(TS1, 1)]))
    getter.script(query(OTHER), page([]))
    _read(conn)
    for url, params, headers, _timeout in getter.calls:
        assert headers["x-api-key"] == KEY_VALUE
        assert KEY_VALUE not in url
        assert KEY_VALUE not in json.dumps(params)


# -- check / discover -------------------------------------------------------


def test_check_probes_once_with_limit_one(conn, getter, env):
    getter.script(query(TICKER, limit=1), page([snap(TS1, 1)]))
    conn.check(CONFIG)
    assert len(getter.calls) == 1
    url, params, headers, timeout = getter.calls[0]
    assert url == "https://api.predexon.com/v2/kalshi/orderbooks"
    assert ORDERBOOKS_PATH == "/v2/kalshi/orderbooks"
    assert params == {"ticker": TICKER, "start_time": START_MS,
                      "end_time": END_MS, "limit": 1}
    assert headers["x-api-key"] == KEY_VALUE
    assert timeout == predexon.DEFAULT_TIMEOUT_S


def test_check_uses_the_declared_base_url_and_validates_the_body(conn, getter, env):
    config = {**CONFIG, "base_url": "https://mirror.test/"}
    getter.script(query(TICKER, limit=1), {"nope": []})
    with pytest.raises(AssetError, match="snapshot list"):
        conn.check(config)
    assert getter.calls[0][0] == "https://mirror.test/v2/kalshi/orderbooks"


def test_check_refuses_bad_knobs_before_any_request(conn, getter, env):
    with pytest.raises(AssetError, match="config.limit"):
        conn.check({**CONFIG, "limit": 0})
    assert getter.calls == []


def test_check_refuses_an_empty_window_instead_of_probing_it(conn, getter, env):
    # No `end`: the clock is the end, and it sits AT coverage_start.
    with pytest.raises(AssetError, match="not before"):
        conn.check({"tickers": [TICKER], "coverage_start": "2026-03-02"})
    assert getter.calls == []


def test_discover_declares_the_stream_offline(conn, getter):
    assert conn.discover(CONFIG) == [{
        "stream": "l2_snapshots",
        "schema": {"fields": list(L2_FIELDS)},
        "primary_key": ["ticker", "timestamp", "sequence"],
    }]
    assert L2_STREAM == "l2_snapshots"
    assert L2_KEY_FIELDS == ("ticker", "timestamp", "sequence")
    assert L2_FIELDS == (
        "ticker", "timestamp", "sequence", "best_bid", "best_ask",
        "bid_depth", "ask_depth", "yes_bids", "yes_asks",
    )
    assert getter.calls == []
    with pytest.raises(AssetError, match="config.tickers"):
        conn.discover({**CONFIG, "tickers": []})


# -- read: normalization ----------------------------------------------------


def test_read_normalizes_ladders_and_stamps_effective_date(conn, getter, env):
    s = snap(TS1, 7, bids=((50, 10), (55, 3), (52, 4)),
             asks=((60, 2), (57, 5), (58, 1)))
    getter.script(query(TICKER), page([s]))
    getter.script(query(OTHER), page([]))
    msgs = _read(conn)
    assert [m["type"] for m in msgs] == ["SCHEMA", "RECORD", "STATE"]
    assert msgs[0]["schema"] == {"fields": list(L2_FIELDS)}
    rec = msgs[1]
    assert rec["effective_date"] == "2026-03-01T12:00:00.123+00:00"
    assert rec["kind"] == "observation"
    assert rec["data"] == {
        "ticker": TICKER, "timestamp": TS1, "sequence": 7,
        "best_bid": 55, "best_ask": 57, "bid_depth": 100, "ask_depth": 80,
        "yes_bids": [[0.55, 3], [0.52, 4], [0.5, 10]],
        "yes_asks": [[0.57, 5], [0.58, 1], [0.6, 2]],
    }
    assert list(rec["data"]) == list(L2_FIELDS)
    # Only the ticker that emitted gets a cursor.
    assert msgs[-1]["state"] == {
        L2_STREAM: {TICKER: {"timestamp": TS1, "sequence": 7}},
    }


def test_one_sided_and_empty_books_are_records_too(conn, getter, env):
    s = snap(TS1, 1, bids=(), asks=((57, 5),))
    bare = {"timestamp": TS2, "sequence": 2}  # no ladders, no best fields
    getter.script(query(TICKER), page([s, bare]))
    getter.script(query(OTHER), page([]))
    data = [m["data"] for m in _records(_read(conn))]
    assert data[0]["yes_bids"] == [] and data[0]["yes_asks"] == [[0.57, 5]]
    assert data[1]["yes_bids"] == [] and data[1]["yes_asks"] == []
    assert data[1]["best_bid"] is None and data[1]["ask_depth"] is None


@pytest.mark.parametrize("bids, asks, needle", [
    (((101, 1),), (), "101"),
    ((), ((-1, 1),), "-1"),
    ((("55", 1),), (), "price"),
    (((55, 1), "level"), (), "level 1"),
    ("ladder", (), "yes_bids"),
])
def test_bad_ladders_refuse_loudly(conn, getter, env, bids, asks, needle):
    s = snap(TS1, 1)
    s["yes_bids"] = (
        bids if isinstance(bids, str)
        else [b if isinstance(b, str) else {"price": b[0], "size": b[1]} for b in bids]
    )
    s["yes_asks"] = [{"price": p, "size": q} for p, q in asks]
    getter.script(query(TICKER), page([s]))
    with pytest.raises(AssetError, match=needle):
        list(conn.read(CONFIG, [L2_STREAM], {}, "backfill"))


def test_a_snapshot_without_a_sequence_refuses_the_ticker(conn, getter, env):
    missing = snap(TS2, 1)
    del missing["sequence"]
    getter.script(query(TICKER), page([snap(TS1, 1), missing]))
    seen = []
    with pytest.raises(AssetError, match=f"{TICKER}.*sequence"):
        for m in conn.read(CONFIG, [L2_STREAM], {}, "backfill"):
            seen.append(m)
    # Buffered per ticker: not one record of the ticker leaked out.
    assert [m["type"] for m in seen] == ["SCHEMA"]

    getter.script(query(TICKER), page([{**snap(TS1, 1), "sequence": None}]))
    with pytest.raises(AssetError, match="sequence"):
        list(conn.read(CONFIG, [L2_STREAM], {}, "backfill"))
    getter.script(query(TICKER), page([{**snap(TS1, 1), "sequence": True}]))
    with pytest.raises(AssetError, match="sequence"):
        list(conn.read(CONFIG, [L2_STREAM], {}, "backfill"))


def test_a_snapshot_without_a_timestamp_refuses(conn, getter, env):
    getter.script(query(TICKER), page([{"sequence": 1}]))
    with pytest.raises(AssetError, match="timestamp"):
        list(conn.read(CONFIG, [L2_STREAM], {}, "backfill"))
    getter.script(query(TICKER), page([{"timestamp": "noon", "sequence": 1}]))
    with pytest.raises(AssetError, match="timestamp"):
        list(conn.read(CONFIG, [L2_STREAM], {}, "backfill"))


def test_snapshot_list_may_live_under_orderbooks(conn, getter, env):
    body = {"orderbooks": [snap(TS1, 1)], "pagination": {"has_more": False}}
    getter.script(query(TICKER), body)
    getter.script(query(OTHER), {"orderbooks": []})  # pagination absent: one page
    assert len(_records(_read(conn))) == 1

    getter.script(query(TICKER), {"rows": []})
    with pytest.raises(AssetError, match="snapshot list"):
        list(conn.read(CONFIG, [L2_STREAM], {}, "backfill"))
    getter.script(query(TICKER), {"data": "nope"})
    with pytest.raises(AssetError, match="snapshot list"):
        list(conn.read(CONFIG, [L2_STREAM], {}, "backfill"))
    getter.script(query(TICKER), {"data": [1]})
    with pytest.raises(AssetError, match="not an object"):
        list(conn.read(CONFIG, [L2_STREAM], {}, "backfill"))
    getter.script(query(TICKER), (200, {}, [1, 2]))
    with pytest.raises(AssetError, match="not a JSON object"):
        list(conn.read(CONFIG, [L2_STREAM], {}, "backfill"))
    getter.script(query(TICKER), {"data": [], "pagination": "later"})
    with pytest.raises(AssetError, match="pagination"):
        list(conn.read(CONFIG, [L2_STREAM], {}, "backfill"))


# -- read: pagination -------------------------------------------------------


def test_two_pages_chain_on_the_pagination_key(conn, getter, env):
    getter.script(query(TICKER), page([snap(TS1, 1)], key="k2", has_more=True))
    getter.script(query(TICKER, key="k2"), page([snap(TS2, 1)]))
    getter.script(query(OTHER), page([]))
    msgs = _read(conn)
    assert [m["data"]["timestamp"] for m in _records(msgs)] == [TS1, TS2]
    calls = _calls_for(getter, TICKER)
    assert len(calls) == 2
    assert "pagination_key" not in calls[0][1]
    assert calls[1][1]["pagination_key"] == "k2"
    assert _logs(msgs) == []
    assert msgs[-1]["state"][L2_STREAM][TICKER] == {"timestamp": TS2, "sequence": 1}


def test_a_key_that_does_not_advance_is_refused(conn, getter, env):
    getter.script(query(TICKER), page([snap(TS1, 1)], key="k2", has_more=True))
    getter.script(query(TICKER, key="k2"), page([snap(TS2, 1)], key="k2", has_more=True))
    with pytest.raises(AssetError, match="did not advance"):
        list(conn.read(CONFIG, [L2_STREAM], {}, "backfill"))


def test_has_more_with_a_null_key_logs_and_stops(conn, getter, env):
    getter.script(query(TICKER), page([snap(TS1, 1)], key=None, has_more=True))
    getter.script(query(OTHER), page([]))
    msgs = _read(conn)
    logs = _logs(msgs)
    assert len(logs) == 1
    assert TICKER in logs[0] and "pagination_key" in logs[0] and "has_more" in logs[0]
    assert len(_records(msgs)) == 1
    assert len(_calls_for(getter, TICKER)) == 1  # never looped
    assert msgs[-1]["state"][L2_STREAM][TICKER] == {"timestamp": TS1, "sequence": 1}


def test_max_pages_logs_when_more_remain(conn, getter, env):
    config = {**CONFIG, "max_pages": 2, "tickers": [TICKER]}
    getter.script(query(TICKER), page([snap(TS1, 1)], key="k2", has_more=True))
    getter.script(query(TICKER, key="k2"), page([snap(TS2, 1)], key="k3", has_more=True))
    msgs = _read(conn, config)
    logs = _logs(msgs)
    assert len(logs) == 1 and "max_pages" in logs[0] and "2" in logs[0]
    assert len(getter.calls) == 2
    assert [m["data"]["timestamp"] for m in _records(msgs)] == [TS1, TS2]
    # What was fetched is durable: the cursor covers it and the next pull resumes.
    assert msgs[-1]["state"][L2_STREAM][TICKER] == {"timestamp": TS2, "sequence": 1}


# -- read: dedup and cursor -------------------------------------------------


def test_duplicates_within_a_pull_collapse_and_ties_sort_by_sequence(conn, getter, env):
    getter.script(query(TICKER), page([snap(TS2, 2), snap(TS1, 1)], key="k2", has_more=True))
    getter.script(query(TICKER, key="k2"), page([snap(TS1, 1), snap(TS2, 1)]))
    getter.script(query(OTHER), page([]))
    msgs = _read(conn)
    keys = [(m["data"]["timestamp"], m["data"]["sequence"]) for m in _records(msgs)]
    assert keys == [(TS1, 1), (TS2, 1), (TS2, 2)]
    assert msgs[-1]["state"][L2_STREAM][TICKER] == {"timestamp": TS2, "sequence": 2}


def test_cursor_skips_durable_snapshots_and_moves_the_window(conn, getter, env):
    state = {L2_STREAM: {TICKER: {"timestamp": TS2, "sequence": 3}}}
    getter.script(query(TICKER, start=TS2),
                  page([snap(TS2, 3), snap(TS2, 4), snap(TS1, 9), snap(TS3, 1)]))
    getter.script(query(OTHER), page([snap(TS1, 1)]))
    msgs = _read(conn, state=state)
    keys = [(m["data"]["ticker"], m["data"]["timestamp"], m["data"]["sequence"])
            for m in _records(msgs)]
    assert keys == [(TICKER, TS2, 4), (TICKER, TS3, 1), (OTHER, TS1, 1)]
    assert _calls_for(getter, TICKER)[0][1]["start_time"] == TS2
    assert msgs[-1]["state"] == {L2_STREAM: {
        TICKER: {"timestamp": TS3, "sequence": 1},
        OTHER: {"timestamp": TS1, "sequence": 1},
    }}
    assert state == {L2_STREAM: {TICKER: {"timestamp": TS2, "sequence": 3}}}  # input untouched


def test_a_caught_up_ticker_keeps_its_cursor(conn, getter, env):
    state = {L2_STREAM: {TICKER: {"timestamp": TS3, "sequence": 1}}}
    getter.script(query(TICKER, start=TS3), page([snap(TS3, 1)]))
    getter.script(query(OTHER), page([]))
    msgs = _read(conn, state=state)
    assert _records(msgs) == []
    assert msgs[-1]["state"] == state


def test_a_cursor_at_or_past_the_end_skips_with_a_log(conn, getter, env):
    state = {L2_STREAM: {TICKER: {"timestamp": END_MS, "sequence": 1}}}
    getter.script(query(OTHER), page([]))
    msgs = _read(conn, state=state)
    logs = _logs(msgs)
    assert len(logs) == 1 and TICKER in logs[0]
    assert _calls_for(getter, TICKER) == []
    assert msgs[-1]["state"] == state


@pytest.mark.parametrize("cursor", [
    {"timestamp": "noon", "sequence": 1},
    {"timestamp": TS1},
    {"timestamp": TS1, "sequence": 1, "extra": 0},
    [TS1, 1],
])
def test_a_malformed_cursor_is_refused(conn, getter, env, cursor):
    with pytest.raises(AssetError, match="cursor"):
        list(conn.read(CONFIG, [L2_STREAM], {L2_STREAM: {TICKER: cursor}}, "backfill"))
    assert getter.calls == []


def test_end_defaults_to_the_clock(conn, getter, env):
    config = {"tickers": [TICKER], "coverage_start": "2026-03-01"}
    getter.script(query(TICKER), page([snap(TS1, 1)]))  # END_MS is the clock
    assert len(_records(_read(conn, config))) == 1
    assert getter.calls[0][1]["end_time"] == END_MS


def test_live_mode_shares_the_logic(conn, getter, env):
    getter.script(query(TICKER), page([snap(TS1, 1)]))
    getter.script(query(OTHER), page([]))
    assert len(_records(_read(conn, mode="live"))) == 1


def test_bad_arguments_refused(conn, getter, env):
    with pytest.raises(AssetError, match="unknown stream"):
        list(conn.read(CONFIG, ["books"], {}, "backfill"))
    with pytest.raises(AssetError, match="state must be a dict"):
        list(conn.read(CONFIG, [L2_STREAM], [], "backfill"))
    with pytest.raises(AssetError, match="streams must be a non-empty list"):
        list(conn.read(CONFIG, [], {}, "backfill"))
    with pytest.raises(AssetError, match="mode must be one of"):
        list(conn.read(CONFIG, [L2_STREAM], {}, "weekly"))
    assert getter.calls == []


# -- retry, pacing, failure shapes -----------------------------------------


def test_429_honors_retry_after_then_succeeds(conn, getter, sleeper, env):
    config = {**CONFIG, "min_interval_s": 0, "tickers": [TICKER]}
    getter.script(query(TICKER), (429, {"Retry-After": "7"}, {}), page([snap(TS1, 1)]))
    assert len(_records(_read(conn, config))) == 1
    assert len(getter.calls) == 2
    assert sleeper.calls == [7.0]


def test_429_without_retry_after_backs_off_from_the_floor(conn, getter, sleeper, env):
    config = {**CONFIG, "min_interval_s": 0, "retry_floor_s": 1.5, "tickers": [TICKER]}
    getter.script(query(TICKER), (429, {}, {}), (429, {"retry-after": "soon"}, {}),
                  page([snap(TS1, 1)]))
    assert len(_records(_read(conn, config))) == 1
    assert sleeper.calls == [1.5, 3.0]  # floor * 2**(attempt-1); a non-numeric header falls back


def test_429_retry_after_is_capped_at_max_backoff(conn, getter, sleeper, env):
    config = {**CONFIG, "min_interval_s": 0, "tickers": [TICKER]}
    getter.script(query(TICKER), (429, {"Retry-After": "100000"}, {}), page([snap(TS1, 1)]))
    assert len(_records(_read(conn, config))) == 1
    assert len(getter.calls) == 2
    assert sleeper.calls == [predexon.MAX_BACKOFF_S]


def test_backoff_doubling_is_capped_at_max_backoff(conn, getter, sleeper, env):
    # The default floor doubles past the ceiling on the sixth failure (2 * 2**5 = 64).
    config = {**CONFIG, "min_interval_s": 0, "retries": 8, "tickers": [TICKER]}
    getter.script(query(TICKER), *([(503, {}, {})] * 6), page([snap(TS1, 1)]))
    assert len(_records(_read(conn, config))) == 1
    assert sleeper.calls == [2.0, 4.0, 8.0, 16.0, 32.0, predexon.MAX_BACKOFF_S]


def test_429_exhausts_the_retries_budget(conn, getter, sleeper, env):
    config = {**CONFIG, "min_interval_s": 0, "retries": 2, "tickers": [TICKER]}
    getter.script(query(TICKER), (429, {}, {}), (429, {}, {}))
    with pytest.raises(AssetError, match="HTTP 429.*2 attempt"):
        list(conn.read(config, [L2_STREAM], {}, "backfill"))
    assert len(getter.calls) == 2


def test_server_faults_get_the_attempts_floor(conn, getter, sleeper, env):
    config = {**CONFIG, "min_interval_s": 0, "retries": 3, "tickers": [TICKER]}
    getter.script(query(TICKER), *([(503, {}, {})] * 5), page([snap(TS1, 1)]))
    assert len(_records(_read(conn, config))) == 1
    assert len(getter.calls) == 6  # max(retries, 6)
    assert sleeper.calls == [2.0, 4.0, 8.0, 16.0, 32.0]

    getter.calls.clear()
    getter.script(query(TICKER), *([(503, {}, {})] * 6))
    with pytest.raises(AssetError, match="HTTP 503.*6 attempt"):
        list(conn.read(config, [L2_STREAM], {}, "backfill"))
    assert len(getter.calls) == 6

    getter.calls.clear()
    getter.script(query(TICKER), *([(502, {}, {})] * 7), page([snap(TS1, 1)]))
    assert len(_records(_read(conn, {**config, "retries": 8}))) == 1
    assert len(getter.calls) == 8  # a larger budget is honoured as-is


def test_client_errors_refuse_immediately_naming_url_and_status(conn, getter, env):
    for status in (400, 401, 404):
        getter.calls.clear()
        # The body ECHOES the key: the message quotes the body, redacted.
        getter.script(query(TICKER), (status, {}, {"error": f"bad key {KEY_VALUE}"}))
        with pytest.raises(AssetError) as exc:
            list(conn.read(CONFIG, [L2_STREAM], {}, "backfill"))
        text = str(exc.value)
        assert f"HTTP {status}" in text
        assert "https://api.predexon.com/v2/kalshi/orderbooks" in text
        assert KEY_VALUE not in text and "<api key>" in text
        assert len(getter.calls) == 1

    # A key straddling the 200-character cut is redacted BEFORE the cut:
    # no fragment of it survives.
    getter.script(query(TICKER), (401, {}, {"error": "x" * 180 + KEY_VALUE}))
    with pytest.raises(AssetError) as exc:
        list(conn.read(CONFIG, [L2_STREAM], {}, "backfill"))
    assert "k-12" not in str(exc.value) and "<api key>" in str(exc.value)


def test_network_errors_are_retried(conn, getter, sleeper, env):
    config = {**CONFIG, "min_interval_s": 0, "tickers": [TICKER]}
    getter.script(query(TICKER), OSError("connection reset"), TimeoutError("timed out"),
                  page([snap(TS1, 1)]))
    assert len(_records(_read(conn, config))) == 1
    assert len(getter.calls) == 3
    assert sleeper.calls == [2.0, 4.0]

    getter.script(query(TICKER), OSError("connection refused"))
    with pytest.raises(AssetError, match="network error.*connection refused"):
        list(conn.read({**config, "retries": 1}, [L2_STREAM], {}, "backfill"))


def test_non_json_body_is_refused(conn, getter, env):
    def raw(url, params, headers, timeout):
        return 200, {}, b"<html>"
    conn = PredexonConnector(getter=raw, clock=FakeClock(0), sleeper=lambda s: None)
    with pytest.raises(AssetError, match="not JSON"):
        list(conn.read(CONFIG, [L2_STREAM], {}, "backfill"))


def test_limiter_spaces_calls_through_the_sleeper(conn, getter, sleeper, env):
    getter.script(query(TICKER), page([snap(TS1, 1)]))
    getter.script(query(OTHER), page([]))
    _read(conn)
    # The first call never blocks; the second arrives with no time elapsed.
    assert sleeper.calls == [1.0]


def test_limiter_credits_work_done_between_calls(clock, sleeper, env):
    getter = ScriptedGetter(clock, work_s=0.4)
    conn = PredexonConnector(getter=getter, clock=clock, sleeper=sleeper)
    getter.script(query(TICKER), page([snap(TS1, 1)]))
    getter.script(query(OTHER), page([]))
    _read(conn)
    assert sleeper.calls == [pytest.approx(0.6)]

    # Enough work between calls and the limiter never sleeps at all.
    getter = ScriptedGetter(clock, work_s=1.5)
    conn = PredexonConnector(getter=getter, clock=clock, sleeper=sleeper)
    getter.script(query(TICKER), page([snap(TS1, 1)]))
    getter.script(query(OTHER), page([]))
    sleeper.calls.clear()
    _read(conn)
    assert sleeper.calls == []


def test_every_retry_attempt_passes_through_the_limiter(conn, getter, sleeper, env):
    config = {**CONFIG, "min_interval_s": 1.0, "retry_floor_s": 0, "tickers": [TICKER]}
    getter.script(query(TICKER), (503, {}, {}), page([snap(TS1, 1)]))
    _read(conn, config)
    # A zero backoff is skipped; the retry itself still paid the interval.
    assert sleeper.calls == [1.0]


def test_limiter_never_credits_a_clock_that_steps_backwards(clock, sleeper):
    limiter = predexon.RateLimiter(1.0, clock, sleeper)
    limiter.wait()
    clock.advance(-5.0)
    limiter.wait()
    assert sleeper.calls == [1.0]


def test_default_transport_is_stdlib_urllib():
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path.startswith(ORDERBOOKS_PATH):
                body = json.dumps({"path": self.path,
                                   "key": self.headers.get("x-api-key")}).encode()
                self.send_response(200)
            else:
                body = b"{}"
                self.send_response(429)
                self.send_header("Retry-After", "3")
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    try:
        server = HTTPServer(("127.0.0.1", 0), Handler)
    except OSError as exc:  # pragma: no cover - sandbox without loopback
        pytest.skip(f"no loopback socket: {exc}")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{server.server_address[1]}"
        status, headers, body = urllib_get(
            base + ORDERBOOKS_PATH, {"ticker": "T", "limit": 1}, {"x-api-key": "k"}, 5,
        )
        assert status == 200
        assert json.loads(body) == {"path": ORDERBOOKS_PATH + "?ticker=T&limit=1", "key": "k"}
        status, headers, body = urllib_get(base + "/other", {}, {}, 5)
        assert status == 429
        assert {k.lower(): v for k, v in headers.items()}["retry-after"] == "3"
        with pytest.raises(OSError):
            urllib_get("http://127.0.0.1:9/closed", {}, {}, 1)
    finally:
        server.shutdown()
        server.server_close()


# -- native_book -----------------------------------------------------------


def test_native_book_golden_case():
    data = {"yes_bids": [[0.52, 4], [0.55, 3]], "yes_asks": [[0.6, 2], [0.57, 5]]}
    assert native_book(data) == {
        "yes_dollars": [[0.55, 3], [0.52, 4]],
        "no_dollars": [[0.43, 5], [0.4, 2]],
    }
    assert native_book({"yes_bids": [], "yes_asks": []}) == {
        "yes_dollars": [], "no_dollars": [],
    }
    assert native_book({}) == {"yes_dollars": [], "no_dollars": []}
    # Round-trips floating error: 1 - 0.57 is 0.42999999999999994 unrounded.
    assert native_book({"yes_asks": [[0.57, 1]]})["no_dollars"] == [[0.43, 1]]


@pytest.mark.parametrize("bad, needle", [
    ({"yes_bids": [[1.5, 1]]}, "1.5"),
    ({"yes_asks": [[-0.01, 1]]}, "-0.01"),
    ({"yes_asks": [[0.5]]}, "pair"),
    ({"yes_bids": "ladder"}, "yes_bids"),
    ("snapshot", "dict"),
])
def test_native_book_refuses_bad_shapes(bad, needle):
    with pytest.raises(AssetError, match=needle):
        native_book(bad)


def test_native_book_reads_an_emitted_record(conn, getter, env):
    getter.script(query(TICKER), page([snap(TS1, 1)]))
    getter.script(query(OTHER), page([]))
    rec = _records(_read(conn))[0]
    assert native_book(rec["data"]) == {
        "yes_dollars": [[0.55, 10], [0.5, 4]],
        "no_dollars": [[0.43, 5], [0.4, 2]],
    }


# -- registration + acquisition e2e ----------------------------------------


def test_registered_kind_resolves():
    assert resolve_connector("predexon") is PredexonConnector


class StubPredexonConnector(PredexonConnector):
    """The pack with its transport scripted on the CLASS.

    ``run_acquisition`` instantiates the connector itself with no
    arguments, so the script cannot be injected per instance: the test
    sets ``responses`` (keyed ``(ticker, pagination_key)``, window and
    limit ignored) and the constructor wires the stub getter plus a
    frozen clock and a non-sleeping sleeper. ``tests/__init__.py`` makes
    this module importable as ``tests.onboarding.test_predexon``, so the
    class resolved by import path IS this class object.
    """

    responses = {}

    def __init__(self):
        clock = FakeClock(END_MS / 1000.0)
        super().__init__(getter=self._scripted, clock=clock, sleeper=Sleeper(clock))

    def _scripted(self, url, params, headers, timeout):
        body = type(self).responses.get(
            (params["ticker"], params.get("pagination_key")), page([]),
        )
        return 200, {}, json.dumps(body).encode("utf-8")


@pytest.fixture
def stub_pages():
    StubPredexonConnector.responses = {
        (TICKER, None): page([snap(TS1, 1), snap(TS2, 1)], key="k2", has_more=True),
        (TICKER, "k2"): page([snap(TS3, 1)]),
        (OTHER, None): page([snap(TS1, 5)]),
    }
    yield
    StubPredexonConnector.responses = {}


def test_acquisition_commits_one_snapshot_then_catches_up(root, registry, env, stub_pages):
    version = registry.register("source_config", {
        "name": "predexon",
        "catalog_source": "predexon-l2",
        "connector": "tests.onboarding.test_predexon:StubPredexonConnector",
        "config": CONFIG,
    }, origin="test")
    registry.transition(version, "active", origin="test")

    summary = run_acquisition(root, registry, "predexon", L2_STREAM, "backfill")
    assert summary["records"] == 4
    assert summary["snapshot"] and summary["state_saved"]
    rows = norm_read(root, "predexon", summary["acq_id"], L2_STREAM)
    assert [(r["data"]["ticker"], r["data"]["timestamp"], r["data"]["sequence"])
            for r in rows] == [
        (TICKER, TS1, 1), (TICKER, TS2, 1), (TICKER, TS3, 1), (OTHER, TS1, 5),
    ]
    assert all(r["effective_date"] <= r["acquired_at"] for r in rows)
    assert rows[0]["effective_date"] == datetime.fromtimestamp(
        TS1 / 1000, tz=timezone.utc).isoformat(timespec="milliseconds")

    # The cursor persisted per ticker; the same pages now yield nothing new.
    caught_up = run_acquisition(root, registry, "predexon", L2_STREAM, "backfill")
    assert caught_up["records"] == 0
    assert caught_up["snapshot"] is None
    assert caught_up["state_saved"]
