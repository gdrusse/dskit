"""libs/cboe.py: the Cboe index-history / option-chain pack, driven through the contract.

No network anywhere: every test injects the ``getter`` transport and a
recording ``sleeper``, so parsing, pacing and retry above the getter run
for real. The acquisition e2e resolves :class:`StubCboeConnector` below by
class reference — the platform instantiates a connector with no arguments,
so the stub binds its scripted transport in ``__init__``.
"""

import json
from datetime import datetime, timezone
import urllib.error

import pytest

from dskit.assets.base import AssetError
from dskit.onboarding import (
    MAX_BACKOFF_S,
    check_config,
    check_message,
    load_state,
    resolve_connector,
    run_acquisition,
    scan_stream,
)
from dskit.onboarding import connector as connector_module
from dskit.onboarding.libs import cboe
from dskit.onboarding.libs.cboe import CboeConnector, parse_occ

BASE = "https://cboe.test"
CONFIG = {"symbols": ["SPX"], "base_url": BASE}
SPX_PATH = "/api/global/us_indices/daily_prices/SPX_History.csv"
VIX_PATH = "/api/global/us_indices/daily_prices/VIX_History.csv"
SPX_CHAIN = "/api/global/delayed_quotes/options/_SPX.json"
XSP_CHAIN = "/api/global/delayed_quotes/options/_XSP.json"

SPX_CSV = "DATE,SPX\n01/02/1975,70.230000\n01/03/1975,70.710000\n"
VIX_CSV = (
    "\ufeffDATE,OPEN,HIGH,LOW,CLOSE\r\n"
    "01/02/1990,17.240000,17.240000,17.240000,17.240000\r\n"
    "09/22/2026,14.640000,14.950000,14.190000,14.210000\r\n"
    "\r\n"
)


def option(symbol, **fields):
    """One venue option object: every numeric field present, overridable."""
    base = {
        "option": symbol, "bid": 12.5, "bid_size": 10.0, "ask": 13.0,
        "ask_size": 20.0, "iv": 0.1432, "open_interest": 1500.0,
        "volume": 42.0, "delta": 0.25, "gamma": 0.0012, "vega": 3.1,
        "theta": -1.2, "rho": 0.4, "theo": 12.7, "change": 0.0, "open": 0.0,
        "high": 0.0, "low": 0.0, "tick": "no_change", "last_trade_price": 12.8,
        "last_trade_time": "2026-09-22T15:59:58", "percent_change": 0.0,
        "prev_day_close": 12.1,
    }
    base.update(fields)
    return base


def chain(options, timestamp="2026-09-23 16:05:00", price=6612.4):
    """One chain payload in the venue's shape."""
    return json.dumps({
        "timestamp": timestamp,
        "symbol": "_SPX",
        "data": {"symbol": "^SPX", "current_price": price, "close": price,
                 "options": options},
    })


def http_error(code, headers=None):
    return urllib.error.HTTPError(BASE, code, "scripted", headers or {}, None)


class Script:
    """A scripted ``getter(url, params)`` routed by path under BASE.

    A route value is a body (str/bytes), an Exception instance to raise,
    or a list of those consumed in order. Every call is recorded.
    """

    def __init__(self, routes):
        self.routes = dict(routes)
        self.calls = []

    def __call__(self, url, params):
        assert url.startswith(BASE + "/"), url
        assert params == {}
        path = url[len(BASE):]
        self.calls.append(path)
        handler = self.routes.get(path)
        assert handler is not None, f"unexpected request {path}"
        if isinstance(handler, list):
            assert handler, f"response queue for {path} exhausted"
            handler = handler.pop(0)
        if isinstance(handler, Exception):
            raise handler
        return handler


#: A fetch clock after every fixture stamp, so the New York reading always stands.
LATE_CLOCK = datetime(2030, 1, 1, tzinfo=timezone.utc)


def connector(routes, clock=LATE_CLOCK):
    """A connector over a scripted transport; returns (connector, script, sleeps)."""
    script = Script(routes)
    sleeps = []
    conn = CboeConnector(getter=script, sleeper=sleeps.append, clock=lambda: clock)
    return conn, script, sleeps


def read(conn, streams, config=CONFIG, state=None, mode="backfill"):
    msgs = list(conn.read(config, streams, state or {}, mode))
    for msg in msgs:
        assert check_message(msg) is not None  # every message envelope-valid
    return msgs


def records(msgs):
    return [m for m in msgs if m["type"] == "RECORD"]


def cursor_of(msgs, stream):
    return msgs[-1]["state"][stream]["cursor"]


# -- spec / knobs -----------------------------------------------------------


def test_spec_passes_its_own_gate_and_denies_unknown_knobs():
    conn = CboeConnector()
    check_config(conn, CONFIG)
    check_config(conn, {**CONFIG, "roots": ["SPXW"], "max_dte": 45,
                        "user_agent": "x", "notes": "always allowed"})
    with pytest.raises(AssetError, match="unknown key"):
        check_config(conn, {**CONFIG, "surprise": 1})
    with pytest.raises(AssetError, match="required knob"):
        check_config(conn, {"base_url": BASE})


def test_bad_knob_shapes_refused():
    conn = CboeConnector()
    bad = {
        "symbols": ([], ["SPX", ""], "SPX", ["SPX", "SPX"], ["spx"], ["SP/X"],
                    ["SPX\n"]),
        "roots": ([], "SPXW", ["SPXW", "SPXW"]),
        "max_dte": (-1, 1.5, True, "30"),
        "retries": (-1, 0.5),
        "pace_s": (-0.1, "0.5"),
        "timeout_s": (0, "30"),
        "user_agent": ("", "  ", 5),
        "base_url": ("ftp://cboe", 5),
    }
    for knob, values in bad.items():
        for value in values:
            with pytest.raises(AssetError, match=f"config.{knob}"):
                conn.resolve_knobs({**CONFIG, knob: value})
    with pytest.raises(AssetError, match="config must be a dict"):
        conn.resolve_knobs("nope")


def test_every_default_has_one_name(monkeypatch):
    # Each default lives in ONE module constant read by resolve_knobs AND
    # by the spec() notes: rebind them and watch both move.
    pins = {
        "pace_s": ("DEFAULT_PACE_S", 0.5, 0.9),
        "retries": ("DEFAULT_RETRIES", 4, 7),
        "timeout_s": ("DEFAULT_TIMEOUT_S", 120, 99),
        "user_agent": ("DEFAULT_USER_AGENT",
                       "Mozilla/5.0 (compatible; dskit-onboarding)", "ua/1"),
        "base_url": ("DEFAULT_BASE_URL", "https://cdn.cboe.com",
                     "https://elsewhere.test"),
    }
    for _knob, (name, current, _sentinel) in pins.items():
        assert getattr(cboe, name) == current, name
    for _knob, (name, _current, sentinel) in pins.items():
        monkeypatch.setattr(cboe, name, sentinel)
    conn = CboeConnector()
    knobs = conn.resolve_knobs({"symbols": ["SPX"]})
    notes = conn.spec()["params"]
    for knob, (_name, _current, sentinel) in pins.items():
        assert knobs[knob] == sentinel
        assert f"default {sentinel}" in notes[knob]["notes"], knob
    assert knobs["roots"] is None and knobs["max_dte"] is None


def test_constructor_refuses_non_callables():
    with pytest.raises(AssetError, match="getter must be callable"):
        CboeConnector(getter="nope")
    with pytest.raises(AssetError, match="sleeper must be callable"):
        CboeConnector(sleeper=3)


# -- check / discover -------------------------------------------------------


def test_check_reads_the_first_symbols_history_once():
    conn, script, sleeps = connector({SPX_PATH: SPX_CSV})
    conn.check({"symbols": ["SPX", "VIX"], "base_url": BASE})
    assert script.calls == [SPX_PATH]
    assert sleeps == []  # the connector's first request is never paced


def test_check_surfaces_an_unknown_symbol():
    # The CDN answers an unknown symbol 403: refused at once, never retried.
    conn, script, _ = connector({SPX_PATH: http_error(403)})
    with pytest.raises(AssetError, match="HTTP 403"):
        conn.check(CONFIG)
    assert len(script.calls) == 1


def test_discover_declares_two_streams_offline():
    conn, script, _ = connector({})
    streams = conn.discover(CONFIG)
    assert script.calls == []
    by_name = {s["stream"]: s for s in streams}
    assert [s["stream"] for s in streams] == ["index_daily", "option_chain"]
    assert by_name["index_daily"]["primary_key"] == ["symbol", "date"]
    assert by_name["option_chain"]["primary_key"] == ["option", "quote_time"]
    assert by_name["index_daily"]["schema"]["fields"] == [
        "symbol", "date", "open", "high", "low", "close"]
    assert len(by_name["option_chain"]["schema"]["fields"]) == 21
    with pytest.raises(AssetError, match="config.symbols"):
        conn.discover({"symbols": []})


def test_registered_kind_resolves():
    assert resolve_connector("cboe") is CboeConnector


def test_the_pack_binds_the_one_retry_policy():
    assert cboe.backoff is connector_module.backoff
    assert cboe.retry_after is connector_module.retry_after
    assert cboe.MAX_BACKOFF_S is MAX_BACKOFF_S


# -- read: contract basics --------------------------------------------------


def test_read_validates_its_arguments():
    conn, _, _ = connector({})
    with pytest.raises(AssetError, match="state must be a dict"):
        list(conn.read(CONFIG, ["index_daily"], [], "live"))
    with pytest.raises(AssetError, match="state.index_daily must be a dict"):
        list(conn.read(CONFIG, ["index_daily"], {"index_daily": "x"}, "live"))
    with pytest.raises(AssetError, match="streams must be a non-empty list"):
        list(conn.read(CONFIG, [], {}, "live"))
    with pytest.raises(AssetError, match="mode must be one of"):
        list(conn.read(CONFIG, ["index_daily"], {}, "nightly"))
    with pytest.raises(AssetError, match="unknown stream.*ghost"):
        list(conn.read(CONFIG, ["index_daily", "ghost"], {}, "live"))


# -- index_daily --------------------------------------------------------------


def test_close_only_header_reads_the_value_column_as_close():
    conn, _, _ = connector({SPX_PATH: SPX_CSV})
    msgs = read(conn, ["index_daily"])
    assert [m["type"] for m in msgs] == ["SCHEMA", "RECORD", "RECORD", "STATE"]
    assert [(r["effective_date"], r["data"]) for r in records(msgs)] == [
        ("1975-01-02", {"symbol": "SPX", "date": "1975-01-02", "open": None,
                        "high": None, "low": None, "close": 70.23}),
        ("1975-01-03", {"symbol": "SPX", "date": "1975-01-03", "open": None,
                        "high": None, "low": None, "close": 70.71}),
    ]
    assert cursor_of(msgs, "index_daily") == "1975-01-03"


def test_ohlc_header_bytes_bom_crlf_and_blank_lines():
    conn, _, _ = connector({VIX_PATH: VIX_CSV.encode("utf-8")})
    msgs = read(conn, ["index_daily"], {"symbols": ["VIX"], "base_url": BASE})
    assert [r["data"] for r in records(msgs)] == [
        {"symbol": "VIX", "date": "1990-01-02", "open": 17.24, "high": 17.24,
         "low": 17.24, "close": 17.24},
        {"symbol": "VIX", "date": "2026-09-22", "open": 14.64, "high": 14.95,
         "low": 14.19, "close": 14.21},
    ]


def test_generic_single_column_header_and_symbols_walk_in_order():
    conn, script, sleeps = connector({
        VIX_PATH: "DATE,VIX\n02/03/2004,17.0\n", SPX_PATH: SPX_CSV})
    msgs = read(conn, ["index_daily"],
                {"symbols": ["VIX", "SPX"], "base_url": BASE, "pace_s": 0.25})
    assert script.calls == [VIX_PATH, SPX_PATH]
    assert sleeps == [0.25]  # paced between requests, never before the first
    assert [r["data"]["symbol"] for r in records(msgs)] == ["VIX", "SPX", "SPX"]
    assert records(msgs)[0]["data"]["close"] == 17.0


@pytest.mark.parametrize("mode", ["backfill", "live"])
def test_index_emits_only_dates_after_the_cursor(mode):
    conn, _, _ = connector({SPX_PATH: SPX_CSV})
    msgs = read(conn, ["index_daily"], state={"index_daily": {"cursor": "1975-01-02"}},
                mode=mode)
    assert [r["data"]["date"] for r in records(msgs)] == ["1975-01-03"]
    assert cursor_of(msgs, "index_daily") == "1975-01-03"


def test_index_cursor_never_regresses_when_nothing_is_new():
    conn, _, _ = connector({SPX_PATH: SPX_CSV})
    msgs = read(conn, ["index_daily"], state={"index_daily": {"cursor": "2026-01-01"}})
    assert records(msgs) == []
    assert cursor_of(msgs, "index_daily") == "2026-01-01"


@pytest.mark.parametrize("text, match", [
    ("", "header must start with DATE"),
    ("WHEN,SPX\n01/02/1975,1\n", "header must start with DATE"),
    ("DATE,OPEN,HIGH\n01/02/1975,1,2\n", "header must be"),
    ("DATE,OPEN,CLOSE,VOLUME\n01/02/1975,1,2,3\n", "header must be"),
    ("DATE,SPX\n01/02/1975,70.23\n1975-01-03,70.71\n", "line 3: DATE must be MM/DD/YYYY"),
    ("DATE,SPX\n02/30/1975,70.23\n", "line 2: DATE must be MM/DD/YYYY"),
    ("DATE,SPX\n01/02/1975,70.23,1\n", "line 2: 3 cell"),
    ("DATE,SPX\n01/02/1975,\n", "line 2: CLOSE is not a finite number"),
    ("DATE,SPX\n01/02/1975,nan\n", "line 2: CLOSE is not a finite number"),
    ("DATE,OPEN,HIGH,LOW,CLOSE\n01/02/1990,1,x,1,1\n", "line 2: HIGH is not"),
    ("DATE,SPX\n01/02/1975,1\n01/02/1975,2\n", "line 3: date 1975-01-02 repeats"),
])
def test_malformed_history_is_refused_with_its_location(text, match):
    conn, _, _ = connector({SPX_PATH: text})
    with pytest.raises(AssetError, match=match) as info:
        read(conn, ["index_daily"])
    assert "SPX_History.csv" in str(info.value)


def test_a_body_that_is_not_text_is_refused():
    conn, _, _ = connector({SPX_PATH: b"\xff\xfe\x00"})
    with pytest.raises(AssetError, match="not UTF-8 text"):
        read(conn, ["index_daily"])
    conn, _, _ = connector({SPX_PATH: {"a": 1}})
    with pytest.raises(AssetError, match="not UTF-8 text"):
        read(conn, ["index_daily"])


# -- OCC ----------------------------------------------------------------------


@pytest.mark.parametrize("symbol, parsed", [
    ("SPXW261016C07000000", ("SPXW", "2026-10-16", "call", 7000.0)),
    ("SPX261218P05500000", ("SPX", "2026-12-18", "put", 5500.0)),
    # XSP is 1/10 SPX: half-point strikes ride the three implied decimals.
    ("XSP261016P00575500", ("XSP", "2026-10-16", "put", 575.5)),
    ("XSP260922C00460000", ("XSP", "2026-09-22", "call", 460.0)),
    ("A260101C00000125", ("A", "2026-01-01", "call", 0.125)),
])
def test_parse_occ(symbol, parsed):
    assert parse_occ(symbol) == parsed


@pytest.mark.parametrize("symbol", [
    None, "", "SPXW261016X07000000", "SPXW261016C7000000", "spxw261016C07000000",
    "TOOLONGR261016C07000000", "SPXW261016C07000000\n", "SPXW 261016C07000000",
])
def test_parse_occ_refuses_non_occ(symbol):
    with pytest.raises(AssetError, match="not an OCC option symbol"):
        parse_occ(symbol)


def test_parse_occ_refuses_an_impossible_date():
    with pytest.raises(AssetError, match="names no date"):
        parse_occ("SPXW261332C07000000")


# -- option_chain ---------------------------------------------------------------


def test_chain_row_shape_and_quote_time_in_utc():
    conn, _, _ = connector({SPX_CHAIN: chain([option("SPXW261016C07000000")])})
    msgs = read(conn, ["option_chain"], mode="live")
    (rec,) = records(msgs)
    assert rec["effective_date"] == "2026-09-23T20:05:00+00:00"  # EDT = UTC-4
    assert rec["data"] == {
        "underlying": "SPX", "option": "SPXW261016C07000000", "root": "SPXW",
        "expiry": "2026-10-16", "right": "call", "strike": 7000.0,
        "bid": 12.5, "bid_size": 10.0, "ask": 13.0, "ask_size": 20.0,
        "iv": 0.1432, "open_interest": 1500.0, "volume": 42.0, "delta": 0.25,
        "gamma": 0.0012, "vega": 3.1, "theta": -1.2, "last_trade_price": 12.8,
        "last_trade_time": "2026-09-22T15:59:58", "underlying_price": 6612.4,
        "quote_time": "2026-09-23T20:05:00+00:00",
    }
    assert list(rec["data"]) == list(cboe.CHAIN_FIELDS)
    assert cursor_of(msgs, "option_chain") == "2026-09-23T20:05:00+00:00"


def test_chain_tolerates_absent_or_junk_numbers():
    raw = option("SPX261218P05500000", bid=None, iv="n/a", volume=float("nan"),
                 last_trade_time=None)
    del raw["ask"]
    conn, _, _ = connector({SPX_CHAIN: chain([raw], price=None)})
    data = records(read(conn, ["option_chain"]))[0]["data"]
    assert (data["bid"], data["ask"], data["iv"], data["volume"]) == (None,) * 4
    assert data["last_trade_time"] is None and data["underlying_price"] is None
    assert data["right"] == "put"


@pytest.mark.parametrize("stamp, utc", [
    ("2026-03-06 16:00:00", "2026-03-06T21:00:00+00:00"),  # EST, before spring-forward
    ("2026-03-09 16:00:00", "2026-03-09T20:00:00+00:00"),  # EDT, after it
    ("2026-10-30 16:00:00", "2026-10-30T20:00:00+00:00"),  # EDT, before fall-back
    ("2026-11-02 16:00:00", "2026-11-02T21:00:00+00:00"),  # EST, after it
    ("2026-11-01 01:30:00", "2026-11-01T05:30:00+00:00"),  # repeated hour: EDT (fold=0)
    ("2026-09-23 23:30:00", "2026-09-24T03:30:00+00:00"),  # crosses the UTC date
])
def test_quote_time_is_new_york_wall_clock_across_dst(stamp, utc):
    conn, _, _ = connector({SPX_CHAIN: chain([option("SPXW261016C07000000")], stamp)})
    assert records(read(conn, ["option_chain"]))[0]["data"]["quote_time"] == utc



@pytest.mark.parametrize("stamp, fetched, utc", [
    # Cboe's 2026-09-24 switch: a UTC stamp read as New York lands 4 h ahead of the fetch
    ("2026-09-25 19:13:09", "2026-09-25T19:13:22+00:00", "2026-09-25T19:13:09+00:00"),
    # a New York stamp fetched at once keeps its New York reading
    ("2026-09-24 10:30:00", "2026-09-24T14:30:05+00:00", "2026-09-24T14:30:00+00:00"),
    # within CLOCK_SKEW_S of the fetch the New York reading still stands
    ("2026-09-24 10:34:00", "2026-09-24T14:30:05+00:00", "2026-09-24T14:34:00+00:00"),
])
def test_quote_time_zone_resolved_against_the_fetch_clock(stamp, fetched, utc):
    clock = datetime.fromisoformat(fetched)
    conn, _, _ = connector({SPX_CHAIN: chain([option("SPXW261016C07000000")], stamp)}, clock)
    rec = records(read(conn, ["option_chain"]))[0]
    assert rec["data"]["quote_time"] == rec["effective_date"] == utc


def test_quote_time_in_the_future_under_both_zones_refuses():
    clock = datetime(2026, 9, 25, 19, 13, tzinfo=timezone.utc)
    conn, _, _ = connector(
        {SPX_CHAIN: chain([option("SPXW261016C07000000")], "2026-09-25 20:00:00")}, clock)
    with pytest.raises(AssetError, match="after the fetch instant"):
        read(conn, ["option_chain"])


def test_clock_must_be_callable():
    with pytest.raises(AssetError, match="clock must be callable"):
        CboeConnector(clock="now")

def test_chain_ignores_the_cursor_and_keeps_it_monotone():
    conn, _, _ = connector({SPX_CHAIN: chain([option("SPXW261016C07000000")])})
    later = "2027-01-01T00:00:00+00:00"
    msgs = read(conn, ["option_chain"], state={"option_chain": {"cursor": later}})
    assert len(records(msgs)) == 1  # current state: never cursor-filtered
    assert cursor_of(msgs, "option_chain") == later


def test_roots_and_max_dte_filter_the_chain():
    # Quote 2026-09-23 23:30 ET is 2026-09-24 UTC: dte counts from the NEW YORK date.
    options = [
        option("SPXW260922C07000000"),  # expired: dte -1
        option("SPXW260923C07000000"),  # 0DTE
        option("SPXW261023P06000000"),  # dte 30
        option("SPXW261024P06000000"),  # dte 31
        option("SPX261023P06000000"),   # dte 30, root not allowed
    ]
    conn, _, _ = connector({SPX_CHAIN: chain(options, "2026-09-23 23:30:00")})
    config = {**CONFIG, "roots": ["SPXW"], "max_dte": 30}
    kept = [r["data"]["option"] for r in records(read(conn, ["option_chain"], config))]
    assert kept == ["SPXW260923C07000000", "SPXW261023P06000000"]
    conn, _, _ = connector({SPX_CHAIN: chain(options, "2026-09-23 23:30:00")})
    everything = records(read(conn, ["option_chain"]))
    assert len(everything) == 5  # absent knobs keep every root and expiry


def test_several_underlyings_in_one_pull():
    conn, script, _ = connector({
        SPX_CHAIN: chain([option("SPXW261016C07000000")]),
        XSP_CHAIN: chain([option("XSP261016P00575500")], price=661.2),
    })
    recs = records(read(conn, ["option_chain"],
                        {"symbols": ["SPX", "XSP"], "base_url": BASE}))
    assert script.calls == [SPX_CHAIN, XSP_CHAIN]
    assert [(r["data"]["underlying"], r["data"]["strike"], r["data"]["underlying_price"])
            for r in recs] == [("SPX", 7000.0, 6612.4), ("XSP", 575.5, 661.2)]


@pytest.mark.parametrize("body, match", [
    ("<html>", "response is not JSON"),
    ("[]", "lacks a 'data.options' list"),
    (json.dumps({"timestamp": "2026-09-23 16:05:00", "data": {}}),
     "lacks a 'data.options' list"),
    (chain([option("SPXW261016C07000000")], "2026-09-23T16:05:00Z"),
     "timestamp must be"),
    (json.dumps({"data": {"options": []}}), "timestamp must be"),
    (chain(["SPXW261016C07000000"]), "option 0: option is not a dict"),
    (chain([option("SPXW261016C07000000"), option("BOGUS")]),
     "option 1: not an OCC option symbol"),
])
def test_malformed_chains_are_refused_with_their_location(body, match):
    conn, _, _ = connector({SPX_CHAIN: body})
    with pytest.raises(AssetError, match=match) as info:
        read(conn, ["option_chain"])
    assert "_SPX.json" in str(info.value)


# -- transport: retry, backoff, pacing ------------------------------------------


def test_retry_on_429_honors_retry_after_then_succeeds():
    conn, script, sleeps = connector({SPX_PATH: [
        http_error(429, {"Retry-After": "3"}), SPX_CSV]})
    assert len(records(read(conn, ["index_daily"]))) == 2
    assert script.calls == [SPX_PATH, SPX_PATH]
    assert sleeps == [3.0]


def test_retry_backs_off_exponentially_on_5xx_and_network_errors():
    conn, _, sleeps = connector({SPX_PATH: [
        http_error(503), urllib.error.URLError("reset"), http_error(502), SPX_CSV]})
    read(conn, ["index_daily"])
    assert sleeps == [0.5, 1.0, 2.0]


def test_every_wait_is_capped_at_max_backoff():
    conn, _, sleeps = connector({SPX_PATH: [
        http_error(429, {"Retry-After": "86400"}), SPX_CSV]})
    read(conn, ["index_daily"])
    assert sleeps == [MAX_BACKOFF_S]
    failures = [http_error(500)] * 9 + [SPX_CSV]
    conn, _, sleeps = connector({SPX_PATH: failures})
    read(conn, ["index_daily"], {**CONFIG, "retries": 9})
    assert max(sleeps) == MAX_BACKOFF_S == sleeps[-1]


def test_retries_exhausted_names_the_url_and_last_failure():
    conn, script, _ = connector({SPX_PATH: [http_error(503)] * 3})
    with pytest.raises(AssetError, match=r"SPX_History.csv: giving up after 3 .*HTTP 503"):
        read(conn, ["index_daily"], {**CONFIG, "retries": 2})
    assert len(script.calls) == 3


def test_pacing_spans_streams_but_never_the_first_request():
    conn, _, sleeps = connector({
        SPX_PATH: SPX_CSV, SPX_CHAIN: chain([option("SPXW261016C07000000")])})
    read(conn, ["index_daily", "option_chain"], {**CONFIG, "pace_s": 0.7})
    assert sleeps == [0.7]


def test_default_transport_is_stdlib_urllib_with_the_user_agent(monkeypatch):
    import urllib.request

    seen = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return SPX_CSV.encode("utf-8")

    def urlopen(request, timeout):
        seen["url"] = request.full_url
        seen["agent"] = request.get_header("User-agent")
        seen["timeout"] = timeout
        return Response()

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    conn = CboeConnector(sleeper=lambda s: None)
    msgs = read(conn, ["index_daily"],
                {**CONFIG, "user_agent": "probe/1", "timeout_s": 9})
    assert len(records(msgs)) == 2
    assert seen == {"url": BASE + SPX_PATH, "agent": "probe/1", "timeout": 9}


# -- end to end through the platform --------------------------------------------


class StubCboeConnector(CboeConnector):
    """The pack over a class-level script — acquisition resolves a fresh
    instance with no arguments, so the transport is bound in ``__init__``."""

    script = None

    def __init__(self):
        super().__init__(getter=type(self).script, sleeper=lambda s: None)


@pytest.fixture
def cboe_source(registry):
    vid = registry.register("source_config", {
        "name": "cboe",
        "catalog_source": "cboe-src",
        "connector": "tests.onboarding.test_cboe:StubCboeConnector",
        "config": CONFIG,
    }, origin="test")
    registry.transition(vid, "active", origin="test")
    yield vid
    StubCboeConnector.script = None


def test_acquisition_appends_only_new_history_days(root, registry, cboe_source):
    StubCboeConnector.script = Script({SPX_PATH: SPX_CSV})
    first = run_acquisition(root, registry, "cboe", "index_daily", "backfill")
    assert first["records"] == 2 and first["state_saved"]
    assert load_state(root, "cboe", "index_daily", "backfill") == {
        "index_daily": {"cursor": "1975-01-03"}}
    # The file grows by one day: the next pull re-downloads it whole and
    # emits only the new date.
    StubCboeConnector.script = Script({SPX_PATH: SPX_CSV + "01/06/1975,71.07\n"})
    second = run_acquisition(root, registry, "cboe", "index_daily", "backfill")
    assert second["records"] == 1
    rows = scan_stream(root.root, "cboe", "index_daily", key_fields=("symbol", "date"))
    assert sorted(row["date"] for row in rows) == [
        "1975-01-02", "1975-01-03", "1975-01-06"]

def test_equity_symbols_fetch_the_unprefixed_chain():
    spy_chain = "/api/global/delayed_quotes/options/SPY.json"
    conn, script, _ = connector({
        SPX_CHAIN: chain([option("SPXW261016C07000000")]),
        spy_chain: chain([option("SPY261016P00650000")], price=661.2),
    })
    recs = records(read(conn, ["option_chain"], {
        "symbols": ["SPX", "SPY"], "equity_symbols": ["SPY"], "base_url": BASE}))
    assert script.calls == [SPX_CHAIN, spy_chain]  # index keeps "_", the ETF has none
    assert [(r["data"]["underlying"], r["data"]["root"]) for r in recs] == \
        [("SPX", "SPXW"), ("SPY", "SPY")]


@pytest.mark.parametrize("equities", [["QQQ"], [], "SPY", ["SPY", "SPY"]])
def test_equity_symbols_must_be_a_declared_subset(equities):
    with pytest.raises(AssetError):
        CboeConnector().resolve_knobs({"symbols": ["SPX", "SPY"], "equity_symbols": equities})
