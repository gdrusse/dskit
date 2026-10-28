"""Schwab daily price history: closed sessions keyed by their New York date (ADR-0279, A0003).

Every instant below is written out in UTC by hand. October 2026 is daylight time: New York
is UTC-4 (00:00 New York = 04:00 UTC) and Schwab stamps a daily candle at 00:00 Central,
UTC-5, i.e. 05:00 UTC of its session. Daylight time ends on Sunday 2026-11-01; after it New
York is UTC-5 and the stamp is 06:00 UTC.
"""

import copy
import json
import re
import urllib.error
import urllib.request
from datetime import datetime, timezone

import pytest

from dskit.assets.base import AssetError
from dskit.onboarding import acquire as acquire_module
from dskit.onboarding import check_config, check_message, run_acquisition
from dskit.onboarding.connector import resolve_connector
from dskit.onboarding.libs import schwab
from dskit.onboarding.libs.schwab import (
    SchwabBarsConnector, SchwabDailyBarsConnector, SchwabPositionsConnector,
)
from dskit.onboarding.observations import scan_stream

from .stub_connectors import ScriptedUrlopen

UTC = timezone.utc
PRICE_URL = "https://api.schwabapi.com/marketdata/v1/pricehistory"
CONFIG = {"symbols": ["SPY", "AAPL"], "start": "2026-10-19"}
# Friday 2026-10-23 at 14:00 New York: that day's session is still open
NOW = datetime(2026, 10, 23, 18, 0, tzinfo=UTC)
DAYS = ("2026-10-19", "2026-10-20", "2026-10-21", "2026-10-22", "2026-10-23")


def _ms(year, month, day, hour, minute=0):
    return int(datetime(year, month, day, hour, minute, tzinfo=UTC).timestamp() * 1000)


def _stamp(day, hour=5):
    """The candle stamp of a session: 00:00 Central, 05:00 UTC in daylight time."""
    year, month, dom = map(int, day.split("-"))
    return _ms(year, month, dom, hour)


def _candle(day, close, hour=5):
    return {"datetime": _stamp(day, hour), "open": close - 1.0, "high": close + 1.0,
            "low": close - 2.0, "close": close, "volume": 1000}


def _body(base, days=DAYS, hour=5):
    return {"candles": [_candle(day, base + i, hour) for i, day in enumerate(days)],
            "symbol": "X", "empty": False}


def _bodies():
    return {"SPY": _body(500.0), "AAPL": _body(200.0)}


class Stub(SchwabDailyBarsConnector):
    """Scripted transport and clock; the class attributes serve an import-path acquisition."""

    bodies = {}
    calls = []
    now = NOW

    def _now(self):
        return type(self).now

    def _access_token(self, knobs):
        return "token"

    def _request(self, token, label, url, params, timeout):
        type(self).calls.append((url, dict(params)))
        body = type(self).bodies[params["symbol"]]
        if isinstance(body, Exception):
            raise body
        return copy.deepcopy(body)


class Clocked(SchwabDailyBarsConnector):
    """The real transport, with only the clock and the token scripted."""

    def _now(self):
        return NOW

    def _access_token(self, knobs):
        return "tok"


@pytest.fixture(autouse=True)
def reset():
    Stub.bodies, Stub.calls, Stub.now = _bodies(), [], NOW
    yield
    Stub.bodies, Stub.calls, Stub.now = {}, [], NOW


def _read(config=CONFIG, state=None, mode="backfill", connector=None):
    messages = list((connector or Stub()).read(config, ["bars"], state or {}, mode))
    assert all(check_message(message) for message in messages)
    return [m for m in messages if m["type"] == "RECORD"], messages


def _keys(records):
    return [(r["data"]["ts"], r["data"]["symbol"]) for r in records]


def _params(symbol, start_ms, end_ms):
    return {"symbol": symbol, "periodType": "year", "frequencyType": "daily", "frequency": 1,
            "startDate": start_ms, "endDate": end_ms, "needExtendedHoursData": "false",
            "needPreviousClose": "false"}


def test_registered_beside_the_other_schwab_packs_and_default_deny():
    assert resolve_connector("schwab_daily") is SchwabDailyBarsConnector
    assert resolve_connector("schwab") is SchwabBarsConnector
    assert resolve_connector("schwab_positions") is SchwabPositionsConnector
    check_config(SchwabDailyBarsConnector(), CONFIG)
    check_config(SchwabDailyBarsConnector(), {**CONFIG, "overlap_days": 3})
    with pytest.raises(AssetError, match="unknown key"):
        check_config(SchwabDailyBarsConnector(), {**CONFIG, "timeframe": [1, "Day"]})
    with pytest.raises(AssetError, match="required knob"):
        check_config(SchwabDailyBarsConnector(), {"start": "2026-10-19"})
    with pytest.raises(AssetError, match="required knob"):
        check_config(SchwabDailyBarsConnector(), {"symbols": ["SPY"]})


@pytest.mark.parametrize("change, named", [
    ({"symbols": "AAPL"}, "config.symbols"),
    ({"symbols": []}, "config.symbols"),
    ({"symbols": ["AAPL", ""]}, "config.symbols"),
    ({"start": "2026-10-19T00:00:00+00:00"}, "config.start"),
    ({"start": "20261019"}, "config.start"),
    ({"start": "2026-02-30"}, "config.start"),
    ({"start": 20261019}, "config.start"),
    ({"overlap_days": 0}, "config.overlap_days"),
    ({"overlap_days": -1}, "config.overlap_days"),
    ({"overlap_days": 1.5}, "config.overlap_days"),
    ({"overlap_days": True}, "config.overlap_days"),
    ({"client_id_env": ""}, "config.client_id_env"),
    ({"max_retries": -1}, "config.max_retries"),
    ({"timeout": 0}, "config.timeout"),
])
def test_each_bad_knob_is_one_named_problem(change, named):
    with pytest.raises(AssetError) as err:
        SchwabDailyBarsConnector().resolve_knobs({**CONFIG, **change})
    assert len(err.value.errors) == 1 and err.value.errors[0].startswith(named)


def test_problems_accumulate_and_a_non_dict_config_refuses():
    with pytest.raises(AssetError) as err:
        SchwabDailyBarsConnector().resolve_knobs({"symbols": [], "start": "soon", "overlap_days": 0})
    assert [e.split(" ")[0] for e in err.value.errors] == [
        "config.symbols", "config.start", "config.overlap_days"]
    with pytest.raises(AssetError, match="config must be a dict"):
        SchwabDailyBarsConnector().resolve_knobs(["SPY"])


def test_discover_declares_the_provider_neutral_daily_bars_stream():
    (stream,) = SchwabDailyBarsConnector().discover(CONFIG)
    assert stream == {
        "stream": "bars",
        "schema": {"fields": ["symbol", "ts", "open", "high", "low", "close", "volume",
                              "trade_count", "vwap"]},
        "primary_key": ["symbol", "ts"],
        "timeframe": [1, "Day"],
    }


def test_backfill_emits_every_closed_session_keyed_by_its_new_york_date():
    records, messages = _read()
    # 2026-10-23 is the pull's own New York date, so its candle is not closed: four sessions
    assert _keys(records) == [(day, symbol) for day in DAYS[:4] for symbol in ("AAPL", "SPY")]
    first = records[0]
    assert first["data"] == {"symbol": "AAPL", "ts": "2026-10-19", "open": 199.0, "high": 201.0,
                             "low": 198.0, "close": 200.0, "volume": 1000.0,
                             "trade_count": None, "vwap": None}
    assert first["effective_date"] == "2026-10-19" and first["kind"] == "observation"
    assert records[-1]["data"]["close"] == 503.0
    assert messages[0]["type"] == "SCHEMA"
    assert messages[-1]["state"] == {"bars": {"cursor": "2026-10-22"}}


def test_the_request_asks_for_daily_candles_between_new_york_midnights():
    _read()
    # 00:00 New York of the first day and of the pull's own date: 04:00 UTC in daylight time
    assert Stub.calls == [(PRICE_URL, _params(symbol, _ms(2026, 10, 19, 4), _ms(2026, 10, 23, 4)))
                          for symbol in ("AAPL", "SPY")]
    # in standard time New York midnight is 05:00 UTC
    Stub.calls, Stub.now = [], datetime(2026, 12, 4, 18, 0, tzinfo=UTC)
    Stub.bodies = {"SPY": {"candles": []}, "AAPL": {"candles": []}}
    records, messages = _read({**CONFIG, "start": "2026-11-30"})
    assert records == [] and messages[-1]["state"] == {"bars": {"cursor": ""}}
    assert Stub.calls[0] == (PRICE_URL, _params("AAPL", _ms(2026, 11, 30, 5), _ms(2026, 12, 4, 5)))


def test_backfill_resumes_the_day_after_its_cursor():
    records, messages = _read(state={"bars": {"cursor": "2026-10-20"}})
    assert _keys(records) == [(day, symbol) for day in DAYS[2:4] for symbol in ("AAPL", "SPY")]
    assert Stub.calls[0][1]["startDate"] == _ms(2026, 10, 21, 4)
    assert messages[-1]["state"] == {"bars": {"cursor": "2026-10-22"}}


def test_nothing_closed_since_the_cursor_means_no_request_and_an_unmoved_cursor():
    records, messages = _read(state={"bars": {"cursor": "2026-10-22"}})
    assert records == [] and Stub.calls == []
    assert messages[-1]["state"] == {"bars": {"cursor": "2026-10-22"}}
    # a start on the pull's own date has nothing closed yet either
    records, messages = _read({**CONFIG, "start": "2026-10-23"})
    assert records == [] and Stub.calls == []
    assert messages[-1]["state"] == {"bars": {"cursor": ""}}


def test_live_re_requests_the_declared_overlap_and_re_emits_it():
    state = {"bars": {"cursor": "2026-10-22"}}
    records, messages = _read({**CONFIG, "overlap_days": 2}, state, "live")
    assert _keys(records) == [(day, symbol) for day in DAYS[1:4] for symbol in ("AAPL", "SPY")]
    assert Stub.calls[0][1]["startDate"] == _ms(2026, 10, 20, 4)
    assert messages[-1]["state"] == {"bars": {"cursor": "2026-10-22"}}
    # the default overlap is five calendar days, never earlier than the declared start
    Stub.calls = []
    records, _ = _read(CONFIG, state, "live")
    assert Stub.calls[0][1]["startDate"] == _ms(2026, 10, 19, 4)
    assert len(records) == 8
    Stub.calls = []
    _read({**CONFIG, "start": "2026-10-01"}, state, "live")
    assert Stub.calls[0][1]["startDate"] == _ms(2026, 10, 17, 4)
    # a first live pull, with no cursor yet, reads from the declared start
    Stub.calls = []
    records, _ = _read(CONFIG, None, "live")
    assert Stub.calls[0][1]["startDate"] == _ms(2026, 10, 19, 4) and len(records) == 8


@pytest.mark.parametrize("now", [
    datetime(2026, 10, 23, 13, 0, tzinfo=UTC),    # 09:00 New York, before the open
    datetime(2026, 10, 23, 23, 30, tzinfo=UTC),   # 19:30 New York, after the close
    datetime(2026, 10, 24, 3, 30, tzinfo=UTC),    # 23:30 New York; the UTC date is already the 24th
])
def test_the_pulls_own_new_york_date_is_never_emitted(now):
    Stub.now = now
    records, _ = _read()
    assert max(r["data"]["ts"] for r in records) == "2026-10-22"
    assert Stub.calls[0][1]["endDate"] == _ms(2026, 10, 23, 4)


def test_the_session_closes_when_its_new_york_date_ends():
    Stub.now = datetime(2026, 10, 24, 4, 30, tzinfo=UTC)   # 00:30 New York on Saturday the 24th
    records, messages = _read()
    assert max(r["data"]["ts"] for r in records) == "2026-10-23"
    assert messages[-1]["state"] == {"bars": {"cursor": "2026-10-23"}}


def test_a_standard_time_stamp_maps_to_its_own_session():
    Stub.now = datetime(2026, 11, 4, 18, 0, tzinfo=UTC)
    days = ("2026-10-29", "2026-10-30", "2026-11-02", "2026-11-03")
    Stub.bodies = {"SPY": {"candles": [_candle(d, 500.0, hour=5 if d < "2026-11-01" else 6)
                                       for d in days]},
                   "AAPL": {"candles": []}}
    records, _ = _read({"symbols": ["SPY"], "start": "2026-10-29"})
    assert [r["data"]["ts"] for r in records] == list(days)


def _mutated(**fields):
    body = _body(200.0)
    body["candles"][1].update(fields)
    return body


@pytest.mark.parametrize("body, message", [
    (_mutated(close="201"), "Schwab daily candle 1 for 'AAPL' is malformed"),
    (_mutated(close=float("nan")), "Schwab daily candle 1 for 'AAPL' is malformed"),
    (_mutated(high=float("inf")), "Schwab daily candle 1 for 'AAPL' is malformed"),
    (_mutated(volume=True), "Schwab daily candle 1 for 'AAPL' is malformed"),
    (_mutated(volume=None), "Schwab daily candle 1 for 'AAPL' is malformed"),
    (_mutated(datetime="2026-10-20"), "Schwab daily candle 1 for 'AAPL' is malformed"),
    (_mutated(datetime=True), "Schwab daily candle 1 for 'AAPL' is malformed"),
    (_mutated(datetime=1e30), "Schwab daily candle 1 for 'AAPL' is malformed"),
    ({"candles": [_candle("2026-10-19", 200.0), ["not", "an", "object"]]},
     "Schwab daily candle 1 for 'AAPL' is not an object"),
    # 09:30 New York is the open: a stamp there or later is not a session-start label
    (_mutated(datetime=_ms(2026, 10, 20, 13, 30)),
     "Schwab daily candle 1 for 'AAPL' is stamped 09:30 New York"),
    # 00:00 UTC is 20:00 New York the evening before: refused rather than dated a day early
    (_mutated(datetime=_ms(2026, 10, 20, 0)),
     "Schwab daily candle 1 for 'AAPL' is stamped 20:00 New York"),
    (_mutated(datetime=_ms(2026, 10, 19, 6)),
     "Schwab daily candles 0 and 1 for 'AAPL' are both session 2026-10-19"),
    ([{"candles": []}], "Schwab daily history for 'AAPL' returned a non-object"),
    ({"empty": True}, "Schwab daily history for 'AAPL' lacks a candles list"),
])
def test_a_malformed_response_refuses_by_symbol_and_index(body, message):
    Stub.bodies["AAPL"] = body
    with pytest.raises(AssetError, match=re.escape(message)):
        _read()


def test_a_malformed_candle_refuses_even_outside_the_window():
    # today's open session is excluded from the rows, but a malformed candle there still refuses
    body = _body(200.0)
    del body["candles"][4]["close"]
    Stub.bodies["AAPL"] = body
    with pytest.raises(AssetError, match="Schwab daily candle 4 for 'AAPL' is malformed"):
        _read()


def test_a_cursor_that_is_not_an_iso_date_refuses_and_a_repeated_symbol_is_asked_once():
    for cursor in ("2026-10-22T00:00:00+00:00", "yesterday"):
        with pytest.raises(AssetError, match=re.escape(f"cursor {cursor!r} is not an ISO date")):
            _read(state={"bars": {"cursor": cursor}})
    assert Stub.calls == []
    records, _ = _read({**CONFIG, "symbols": ["SPY", "AAPL", "SPY"]})
    assert [call[1]["symbol"] for call in Stub.calls] == ["AAPL", "SPY"] and len(records) == 8


@pytest.mark.parametrize("args, message", [
    (("not a dict", ["bars"], "backfill"), "state must be a dict"),
    (({}, [], "backfill"), "streams must be a non-empty list"),
    (({}, ["positions"], "backfill"), "unknown stream 'positions'"),
    (({}, ["bars"], "weekly"), "mode must be one of"),
])
def test_read_guards_its_arguments(args, message):
    state, streams, mode = args
    with pytest.raises(AssetError, match=message):
        list(Stub().read(CONFIG, streams, state, mode))
    assert Stub.calls == []


def test_check_probes_the_first_declared_symbol_over_the_last_week():
    assert Stub().check(CONFIG) is None
    assert Stub.calls == [(PRICE_URL, _params("SPY", _ms(2026, 10, 16, 4), _ms(2026, 10, 23, 4)))]


def test_one_authorization_serves_every_schwab_pack():
    daily = SchwabDailyBarsConnector().resolve_knobs(CONFIG)
    positions = SchwabPositionsConnector().resolve_knobs({})
    names = ("client_id_env", "client_secret_env", "callback_url_env", "token_path_env")
    assert {n: daily[n] for n in names} == {n: positions[n] for n in names} == {
        "client_id_env": "SCHWAB_APP_KEY", "client_secret_env": "SCHWAB_APP_SECRET",
        "callback_url_env": "SCHWAB_CALLBACK_URL", "token_path_env": "SCHWAB_TOKEN_PATH"}
    shared = SchwabPositionsConnector().spec()["params"]
    assert {k: v for k, v in SchwabDailyBarsConnector().spec()["params"].items()
            if k in shared} == shared


def test_the_real_transport_sends_the_bearer_token_and_every_param(monkeypatch):
    empty = json.dumps({"candles": []}).encode()
    fake = ScriptedUrlopen((200, empty), (200, empty))
    monkeypatch.setattr(urllib.request, "urlopen", fake)
    records, _ = _read(connector=Clocked())
    assert records == []
    (url, headers, timeout), _spy = fake.requests
    assert url == (PRICE_URL + "?symbol=AAPL&periodType=year&frequencyType=daily&frequency=1"
                   f"&startDate={_ms(2026, 10, 19, 4)}&endDate={_ms(2026, 10, 23, 4)}"
                   "&needExtendedHoursData=false&needPreviousClose=false")
    assert headers == {"Authorization": "Bearer tok"} and timeout == 30


@pytest.mark.parametrize("outcomes, message", [
    ([urllib.error.HTTPError(PRICE_URL, 404, "scripted", {}, None)],
     r"Schwab daily history failed for 'AAPL' \(HTTP 404\)"),
    ([(200, b"{not json")], r"Schwab daily history for 'AAPL' returned malformed JSON"),
    ([(200, b"[]")], r"Schwab daily history for 'AAPL' returned a non-object"),
])
def test_transport_refusals_name_the_daily_history(monkeypatch, outcomes, message):
    monkeypatch.setattr(schwab.time, "sleep", lambda s: None)
    monkeypatch.setattr(urllib.request, "urlopen", ScriptedUrlopen(*outcomes))
    with pytest.raises(AssetError, match=message):
        _read(connector=Clocked())


def test_acquisition_keeps_the_latest_correction_of_an_overlapping_session(
        root, registry, monkeypatch):
    version = registry.register("source_config", {
        "name": "daily",
        "catalog_source": "daily-source",
        "connector": "tests.onboarding.test_schwab_daily:Stub",
        "config": {**CONFIG, "overlap_days": 2},
    }, origin="test")
    registry.transition(version, "active", origin="test")
    stamps = iter(["2026-10-23T18:00:00+00:00", "2026-10-24T14:00:00+00:00",
                   "2026-10-24T14:05:00+00:00"])
    monkeypatch.setattr(acquire_module, "utc_now", lambda: next(stamps))
    first = run_acquisition(root, registry, "daily", "bars", "backfill")
    assert first["records"] == 8                       # 19th-22nd: the 23rd was still open
    Stub.now = datetime(2026, 10, 24, 14, 0, tzinfo=UTC)   # Saturday: the 23rd has closed
    second = run_acquisition(root, registry, "daily", "bars", "live")
    assert second["records"] == 10                     # the first live pull reads from start
    Stub.bodies["AAPL"]["candles"][3]["close"] = 203.5     # Schwab corrects the 22nd's close
    third = run_acquisition(root, registry, "daily", "bars", "live")
    assert third["records"] == 6                       # cursor the 23rd, two days of overlap
    rows = scan_stream(root.root, "daily", "bars", key_fields=("symbol", "ts"))
    keyed = {(row["symbol"], row["ts"]): row for row in rows}
    assert len(keyed) == len(rows) == 10
    assert keyed[("AAPL", "2026-10-22")]["close"] == 203.5
    assert keyed[("AAPL", "2026-10-21")]["close"] == 202.0
