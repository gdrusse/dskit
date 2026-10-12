"""libs/thetadata.py: ThetaData daily option chains through the connector contract.

No network and no waiting: the getter and sleeper are injected, so request
shaping, retry, the 570 split, merge, cursor and ordering all run for real
against a scripted terminal.
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from dskit.assets.base import AssetError
from dskit.onboarding import check_config, check_message, resolve_connector
from dskit.onboarding.libs.thetadata import (
    OPTION_EOD_STREAM,
    OPTION_QUOTE_AT_STREAM,
    OPTION_FIELDS,
    OPTION_KEY_FIELDS,
    UNDERLYING_STREAM,
    ThetaDataConnector,
    urllib_get,
)

BASE = "http://127.0.0.1:25503"
CONFIG = {"symbols": ["AAA"], "start": "2025-01-02", "end": "2025-01-07"}
# 2025-01-02 Thu, 03 Fri, 04 Sat, 05 Sun, 06 Mon; end is exclusive.
DATES_DESC = ["2025-01-06", "2025-01-03", "2025-01-02"]


def eod_row(expiration="2025-02-21", strike=100.0, right="CALL", **extra):
    row = {
        "symbol": "AAA", "expiration": expiration, "strike": strike,
        "right": right, "open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5,
        "volume": 10, "count": 3, "bid": 1.4, "ask": 1.6,
        "delta": 0.5, "implied_vol": 0.3, "underlying_price": 100.0,
    }
    row.update(extra)
    return row


def oi_row(expiration="2025-02-21", strike=100.0, right="CALL", oi=7):
    return {"symbol": "AAA", "expiration": expiration, "strike": strike,
            "right": right, "open_interest": oi}


class Terminal:
    """A scripted Theta Terminal: (path, date) -> rows, or a status."""

    def __init__(self):
        self.chain = {}      # (path, "YYYYMMDD") -> rows
        self.status = {}     # (path, "YYYYMMDD") -> list of statuses to serve first
        self.expirations = ["2025-02-21"]
        self.stock = {}
        self.calls = []
        self.lock = threading.Lock()

    def __call__(self, url, params, timeout):
        path = url[len(BASE):]
        with self.lock:
            self.calls.append((path, dict(params)))
        day = params.get("date") or params.get("start_date")
        queue = self.status.get((path, day))
        if queue:
            with self.lock:
                code = queue.pop(0)
            if code != 200:
                return code, {"Retry-After": "2"} if code == 429 else {}, b"x"
        if path == "/v3/option/list/expirations":
            return 200, {}, json.dumps(
                [{"symbol": "AAA", "expiration": e} for e in self.expirations]
            ).encode()
        if path == "/v3/stock/history/eod":
            return 200, {}, json.dumps(self.stock.get(params["symbol"], [])).encode()
        if path == "/v3/stock/list/symbols":
            return 200, {}, b'[{"symbol": "AAA"}]'
        rows = self.chain.get((path, day))
        if rows is None:
            return 472, {}, b"No data"
        if params.get("expiration") not in ("*", None):
            rows = [r for r in rows if r["expiration"] == params["expiration"]]
        return 200, {}, json.dumps(rows).encode()


EOD = "/v3/option/history/greeks/eod"
EOD_FREE = "/v3/option/history/eod"
OI = "/v3/option/history/open_interest"


def connector(terminal, sleeps=None):
    waits = [] if sleeps is None else sleeps
    return ThetaDataConnector(getter=terminal, sleeper=waits.append)


def pull(conn, config=CONFIG, streams=(OPTION_EOD_STREAM,), state=None):
    messages = list(conn.read(config, list(streams), state or {}, "backfill"))
    for message in messages:
        check_message(message)
    records = [m for m in messages if m["type"] == "RECORD"]
    final = [m for m in messages if m["type"] == "STATE"][-1]["state"]
    return records, final


def populated():
    t = Terminal()
    for day in ("20250106", "20250103"):
        t.chain[(EOD, day)] = [eod_row(), eod_row(strike=105.0, right="PUT")]
        t.chain[(OI, day)] = [oi_row(), oi_row(strike=105.0, right="PUT", oi=9),
                              oi_row(strike=110.0, oi=4)]
    return t


def test_spec_is_default_deny_and_registered():
    conn = ThetaDataConnector()
    check_config(conn, CONFIG)
    with pytest.raises(AssetError):
        check_config(conn, {**CONFIG, "surprise": 1})
    with pytest.raises(AssetError):
        check_config(conn, {"symbols": ["AAA"], "start": "2025-01-02"})
    assert resolve_connector("thetadata") is ThetaDataConnector
    assert not any(k.get("secret") for k in conn.spec()["params"].values())


@pytest.mark.parametrize("bad", [
    {"symbols": []},
    {"end": "2025-01-02"},
    {"concurrency": 9},
    {"concurrency": 0},
    {"batch_units": 0},
    {"greeks": "yes"},
])
def test_bad_knobs_refuse(bad):
    with pytest.raises(AssetError):
        ThetaDataConnector().resolve_knobs({**CONFIG, **bad})


def test_discover_names_streams_and_keys():
    found = {s["stream"]: s for s in ThetaDataConnector().discover(CONFIG)}
    assert set(found) == {OPTION_EOD_STREAM, UNDERLYING_STREAM, OPTION_QUOTE_AT_STREAM}
    assert found[OPTION_EOD_STREAM]["primary_key"] == list(OPTION_KEY_FIELDS)


def test_records_merge_eod_and_open_interest_newest_first():
    records, state = pull(connector(populated()))
    dates = [r["data"]["date"] for r in records]
    assert dates == sorted(dates, reverse=True)
    assert set(dates) == {"2025-01-06", "2025-01-03"}
    first = [r["data"] for r in records if r["data"]["date"] == "2025-01-06"]
    assert len(first) == 3                       # 2 traded + 1 open-interest-only
    call = next(d for d in first if d["strike"] == 100.0)
    assert call["open_interest"] == 7 and call["close"] == 1.5
    assert call["right"] == "call"
    only = next(d for d in first if d["strike"] == 110.0)
    assert only["open_interest"] == 4 and only["close"] is None
    assert set(OPTION_FIELDS) <= set(call)
    assert records[0]["effective_date"] == "2025-01-06"
    assert state[OPTION_EOD_STREAM]["AAA"]["oldest"] == "2025-01-02"


def test_vendor_extras_ride_through():
    t = populated()
    t.chain[(EOD, "20250106")] = [eod_row(brand_new_field=3)]
    records, _ = pull(connector(t))
    top = [r["data"] for r in records if r["data"]["date"] == "2025-01-06"]
    assert any(d.get("brand_new_field") == 3 for d in top)


def test_end_is_exclusive_and_weekends_skipped():
    t = populated()
    pull(connector(t))
    asked = {c[1].get("start_date") or c[1].get("date") for c in t.calls
             if c[0] in (EOD, OI)}
    assert asked == {"20250106", "20250103", "20250102"}


def test_resume_from_cursor_fetches_only_older_days():
    t = populated()
    t.chain[(EOD, "20250102")] = [eod_row()]
    state = {OPTION_EOD_STREAM: {"AAA": {"oldest": "2025-01-03"}}}
    records, new = pull(connector(t), state=state)
    assert {r["data"]["date"] for r in records} == {"2025-01-02"}
    assert new[OPTION_EOD_STREAM]["AAA"]["oldest"] == "2025-01-02"
    again, final = pull(connector(t), state=new)
    assert again == [] and final == new


def test_batch_units_bounds_one_pull():
    t = populated()
    records, state = pull(connector(t), {**CONFIG, "batch_units": 2})
    assert {r["data"]["date"] for r in records} == {"2025-01-06", "2025-01-03"}
    assert state[OPTION_EOD_STREAM]["AAA"]["oldest"] == "2025-01-03"


def test_symbols_run_in_declared_priority_order():
    t = populated()
    cfg = {**CONFIG, "symbols": ["ZZZ", "AAA"], "batch_units": 3}
    _, state = pull(connector(t), cfg)
    assert state[OPTION_EOD_STREAM]["ZZZ"]["oldest"] == "2025-01-02"
    assert "AAA" not in state[OPTION_EOD_STREAM]


def test_free_tier_uses_plain_eod_endpoint():
    t = Terminal()
    t.chain[(EOD_FREE, "20250106")] = [eod_row()]
    t.chain[(OI, "20250106")] = [oi_row()]
    cfg = {**CONFIG, "greeks": False, "open_interest": False, "end": "2025-01-07",
           "start": "2025-01-06"}
    records, _ = pull(connector(t), cfg)
    assert len(records) == 1
    assert all(c[0] == EOD_FREE for c in t.calls)


def test_429_honours_retry_after_and_5xx_retries():
    t = populated()
    t.status[(EOD, "20250106")] = [429, 503]
    sleeps = []
    records, _ = pull(connector(t, sleeps))
    assert len(records) > 0
    assert sleeps[0] == 2.0 and len(sleeps) == 2


def test_other_4xx_refuses_naming_status():
    t = populated()
    t.status[(EOD, "20250106")] = [403]
    with pytest.raises(AssetError) as err:
        pull(connector(t))
    assert "403" in str(err.value)


def test_unreachable_terminal_refuses_after_retries():
    def down(url, params, timeout):
        raise OSError("connection refused")
    sleeps = []
    with pytest.raises(AssetError) as err:
        pull(ThetaDataConnector(getter=down, sleeper=sleeps.append))
    assert "Theta Terminal" in str(err.value) and sleeps


def test_large_request_570_splits_by_expiration():
    t = Terminal()
    t.expirations = ["2025-02-21", "2025-03-21"]
    rows = [eod_row(), eod_row(expiration="2025-03-21")]
    t.chain[(EOD, "20250106")] = rows
    t.chain[(OI, "20250106")] = [oi_row(), oi_row(expiration="2025-03-21")]
    real = t.__call__

    def too_large_on_star(url, params, timeout):
        if params.get("expiration") == "*" and url.endswith(("greeks/eod",
                                                           "open_interest")):
            t.calls.append((url[len(BASE):], dict(params)))
            return 570, {}, b"LARGE_REQUEST"
        return real(url, params, timeout)

    cfg = {**CONFIG, "start": "2025-01-06", "end": "2025-01-07"}
    records, _ = pull(ThetaDataConnector(getter=too_large_on_star,
                                         sleeper=lambda s: None), cfg)
    assert {r["data"]["expiration"] for r in records} == {"2025-02-21", "2025-03-21"}


def test_concurrency_preserves_order_and_content():
    serial, _ = pull(connector(populated()))
    parallel, _ = pull(connector(populated()), {**CONFIG, "concurrency": 3})
    assert parallel == serial


def test_underlying_stream_and_cursor():
    t = Terminal()
    t.stock["AAA"] = [
        {"created": "2025-01-03T17:15:00", "open": 9, "high": 11, "low": 8,
         "close": 10, "volume": 100, "count": 5, "bid": 9.9, "ask": 10.1},
        {"created": "2025-01-06T17:15:00", "open": 10, "high": 12, "low": 9,
         "close": 11, "volume": 120, "count": 6, "bid": 10.9, "ask": 11.1},
    ]
    records, state = pull(connector(t), streams=(UNDERLYING_STREAM,))
    assert [r["data"]["date"] for r in records] == ["2025-01-03", "2025-01-06"]
    assert records[0]["data"]["symbol"] == "AAA"
    again, _ = pull(connector(t), streams=(UNDERLYING_STREAM,), state=state)
    assert again == []


def test_live_mode_and_unknown_stream_refuse():
    conn = connector(populated())
    with pytest.raises(AssetError):
        list(conn.read(CONFIG, [OPTION_EOD_STREAM], {}, "live"))
    with pytest.raises(AssetError):
        list(conn.read(CONFIG, ["nope"], {}, "backfill"))


def test_check_probes_the_terminal():
    ThetaDataConnector(getter=Terminal(), sleeper=lambda s: None).check(CONFIG)

    def down(url, params, timeout):
        raise OSError("refused")
    with pytest.raises(AssetError):
        ThetaDataConnector(getter=down, sleeper=lambda s: None).check(CONFIG)


def test_urllib_getter_against_loopback_server():
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            code = 472 if "empty" in self.path else 200
            self.send_response(code)
            self.end_headers()
            self.wfile.write(b'[{"symbol": "AAA"}]')

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_port}/v3/x"
        assert urllib_get(url, {"a": "1"}, 5)[0] == 200
        assert urllib_get(url + "empty", {}, 5)[0] == 472
    finally:
        server.shutdown()


# ---- review round: silent-hole and wedge guards ----------------------------

def test_non_record_body_refuses_instead_of_reading_empty():
    def columnar(url, params, timeout):
        return 200, {}, b'{"header": {}, "strike": [100]}'
    with pytest.raises(AssetError):
        pull(ThetaDataConnector(getter=columnar, sleeper=lambda s: None))

    def scalars(url, params, timeout):
        return 200, {}, b"[1, 2]"
    with pytest.raises(AssetError):
        pull(ThetaDataConnector(getter=scalars, sleeper=lambda s: None))


def test_root_that_never_returns_a_chain_is_refused_not_marked_done():
    t = Terminal()                     # every day answers 472
    with pytest.raises(AssetError) as err:
        pull(connector(t), {**CONFIG, "empty_guard": 2})
    assert "AAA" in str(err.value)


def test_empty_days_after_real_data_are_fine():
    t = populated()                    # 01-02 is empty but older than real data
    records, state = pull(connector(t), {**CONFIG, "empty_guard": 1})
    assert records and state[OPTION_EOD_STREAM]["AAA"]["oldest"] == "2025-01-02"


def test_extending_end_refuses_rather_than_leave_a_gap():
    _, state = pull(connector(populated()))
    with pytest.raises(AssetError) as err:
        pull(connector(populated()), {**CONFIG, "end": "2025-01-09"}, state=state)
    assert "gap" in str(err.value)


def test_lowering_start_resumes_below_oldest():
    t = populated()
    _, state = pull(connector(t), {**CONFIG, "start": "2025-01-03"})
    t.chain[(EOD, "20250102")] = [eod_row()]
    records, _ = pull(connector(t), CONFIG, state=state)
    assert {r["data"]["date"] for r in records} == {"2025-01-02"}


def test_malformed_row_names_root_date_and_endpoint():
    t = populated()
    t.chain[(EOD, "20250106")] = [{"symbol": "AAA", "right": "CALL", "bid": 1.0}]
    with pytest.raises(AssetError) as err:
        pull(connector(t))
    assert "AAA" in str(err.value) and "2025-01-06" in str(err.value)


def test_expiration_spellings_merge_into_one_contract():
    t = Terminal()
    t.chain[(EOD, "20250106")] = [eod_row(expiration="2025-02-21")]
    t.chain[(OI, "20250106")] = [oi_row(expiration=20250221)]
    cfg = {**CONFIG, "start": "2025-01-06", "end": "2025-01-07"}
    records, _ = pull(connector(t), cfg)
    assert len(records) == 1
    assert records[0]["data"]["open_interest"] == 7
    assert records[0]["data"]["expiration"] == "2025-02-21"


def test_duplicate_contract_in_one_response_refuses():
    t = populated()
    t.chain[(EOD, "20250106")] = [eod_row(), eod_row()]
    with pytest.raises(AssetError) as err:
        pull(connector(t))
    assert "repeats" in str(err.value)


def test_mid_batch_failure_emits_no_state():
    t = populated()
    t.status[(EOD, "20250103")] = [403]
    seen = []
    with pytest.raises(AssetError):
        for message in connector(t).read(CONFIG, [OPTION_EOD_STREAM], {}, "backfill"):
            seen.append(message["type"])
    assert "RECORD" in seen and "STATE" not in seen


def test_570_skips_past_expirations_merges_open_interest_and_refuses_alone():
    t = Terminal()
    t.expirations = ["2024-12-20", "2025-02-21"]
    t.chain[(EOD, "20250106")] = [eod_row(), eod_row(expiration="2024-12-20")]
    t.chain[(OI, "20250106")] = [oi_row(), oi_row(expiration="2024-12-20")]
    real = t.__call__
    def too_large(url, params, timeout):
        if params.get("expiration") == "*" and url.endswith(("greeks/eod", "open_interest")):
            return 570, {}, b"LARGE_REQUEST"
        return real(url, params, timeout)
    cfg = {**CONFIG, "start": "2025-01-06", "end": "2025-01-07"}
    records, _ = pull(ThetaDataConnector(getter=too_large, sleeper=lambda s: None), cfg)
    assert [r["data"]["expiration"] for r in records] == ["2025-02-21"]
    assert records[0]["data"]["open_interest"] == 7

    def always_large(url, params, timeout):
        if url.endswith(("greeks/eod", "open_interest")):
            return 570, {}, b"LARGE_REQUEST"
        return real(url, params, timeout)
    with pytest.raises(AssetError):
        pull(ThetaDataConnector(getter=always_large, sleeper=lambda s: None), cfg)


def test_underlying_bounds_duplicates_and_missing_report_date():
    def stock(rows):
        t = Terminal()
        t.stock["AAA"] = rows
        return t

    inside = {"created": "2025-01-06T17:15:00", "close": 11}
    outside = [{"created": "2025-01-01T17:15:00", "close": 1},
               {"created": "2025-01-07T17:15:00", "close": 2}]
    records, _ = pull(connector(stock(outside + [inside])), streams=(UNDERLYING_STREAM,))
    assert [r["data"]["date"] for r in records] == ["2025-01-06"]
    for bad in ([inside, dict(inside)], [{"close": 3, "last_trade": "2025-01-06"}], []):
        with pytest.raises(AssetError):
            pull(connector(stock(bad)), streams=(UNDERLYING_STREAM,))


def test_concurrency_order_with_many_units_and_reversed_completion():
    import time
    t = populated()
    for day in range(2, 32):
        if day % 7 not in (0, 6):
            t.chain[(EOD, f"202501{day:02d}")] = [eod_row()]
    inner = t.__call__

    def slow_for_newest(url, params, timeout):
        day = int((params.get("date") or params.get("start_date"))[-2:])
        time.sleep(0.0004 * day)
        return inner(url, params, timeout)

    cfg = {"symbols": ["AAA"], "start": "2025-01-02", "end": "2025-02-01"}
    serial, s_state = pull(ThetaDataConnector(getter=slow_for_newest, sleeper=lambda s: None), cfg)
    parallel, p_state = pull(ThetaDataConnector(getter=slow_for_newest, sleeper=lambda s: None),
                             {**cfg, "concurrency": 4})
    assert len(serial) > 15 and parallel == serial and p_state == s_state


def test_ndjson_body_and_unknown_symbol_472():
    lines = "\n".join(json.dumps(eod_row(strike=k)) for k in (100.0, 105.0)).encode()

    def ndjson(url, params, timeout):
        assert params["format"] == "ndjson"
        return 200, {}, lines
    cfg = {**CONFIG, "start": "2025-01-06", "end": "2025-01-07", "open_interest": False}
    records, _ = pull(ThetaDataConnector(getter=ndjson, sleeper=lambda s: None), cfg)
    assert [r["data"]["strike"] for r in records] == [100.0, 105.0]

    def single(url, params, timeout):
        return 200, {}, json.dumps(eod_row()).encode()
    records, _ = pull(ThetaDataConnector(getter=single, sleeper=lambda s: None), cfg)
    assert len(records) == 1

    def unknown(url, params, timeout):
        return 472, {}, b"Unknown symbol: AAA. Use /v3/option/list/symbols"
    with pytest.raises(AssetError) as err:
        pull(ThetaDataConnector(getter=unknown, sleeper=lambda s: None), cfg)
    assert "Unknown symbol" in str(err.value)


# ---- at-time quotes and index underlyings ----------------------------------

AT_TIME = "/v3/option/at_time/quote"


def quote_row(strike=100.0, right="CALL", **extra):
    row = {"symbol": "AAA", "expiration": "2025-02-21", "strike": strike, "right": right,
           "bid": 1.0, "ask": 1.2, "bid_size": 5, "ask_size": 6,
           "timestamp": "2025-01-06T15:44:59.647"}
    row.update(extra)
    return row


def test_quote_at_stream_one_request_per_time_and_own_cursor():
    t = Terminal()
    seen = []

    def getter(url, params, timeout):
        path = url[len(BASE):]
        if path == AT_TIME:
            seen.append((params["time_of_day"], params["start_date"]))
            return 200, {}, json.dumps([quote_row(), quote_row(105.0, "PUT")]).encode()
        return t(url, params, timeout)

    cfg = {**CONFIG, "start": "2025-01-06", "end": "2025-01-07",
           "quote_times": ["09:45:00", "15:45:00"]}
    records, state = pull(ThetaDataConnector(getter=getter, sleeper=lambda s: None),
                          cfg, streams=("option_quote_at",))
    assert seen == [("09:45:00", "20250106"), ("15:45:00", "20250106")]
    assert [(r["data"]["time_of_day"], r["data"]["right"]) for r in records] == [
        ("09:45:00", "call"), ("09:45:00", "put"), ("15:45:00", "call"), ("15:45:00", "put")]
    assert records[0]["data"]["date"] == "2025-01-06" and records[0]["data"]["bid"] == 1.0
    assert state["option_quote_at"]["AAA"]["oldest"] == "2025-01-06"
    assert "option_eod" not in state


def test_quote_at_needs_times_and_validates_them():
    with pytest.raises(AssetError):
        pull(connector(Terminal()), CONFIG, streams=("option_quote_at",))
    for bad in (["9:45"], ["15:45:00", "15:45:00"], "15:45:00"):
        with pytest.raises(AssetError):
            ThetaDataConnector().resolve_knobs({**CONFIG, "quote_times": bad})


def test_quote_at_duplicate_contract_refuses():
    def getter(url, params, timeout):
        return 200, {}, json.dumps([quote_row(), quote_row()]).encode()
    cfg = {**CONFIG, "start": "2025-01-06", "end": "2025-01-07", "quote_times": ["15:45:00"]}
    with pytest.raises(AssetError):
        pull(ThetaDataConnector(getter=getter, sleeper=lambda s: None), cfg,
             streams=("option_quote_at",))


def test_index_underlying_uses_index_endpoint():
    paths = []

    def getter(url, params, timeout):
        paths.append(url[len(BASE):])
        return 200, {}, json.dumps([{"created": "2025-01-06T17:19:49", "close": 5900.0}]).encode()

    cfg = {**CONFIG, "symbols": ["SPX"], "underlying_kind": "index"}
    records, _ = pull(ThetaDataConnector(getter=getter, sleeper=lambda s: None), cfg,
                      streams=(UNDERLYING_STREAM,))
    assert paths == ["/v3/index/history/eod"] and records[0]["data"]["close"] == 5900.0
    with pytest.raises(AssetError):
        ThetaDataConnector().resolve_knobs({**CONFIG, "underlying_kind": "future"})


def test_open_interest_resent_through_the_morning_keeps_the_latest_report():
    t = Terminal()
    t.chain[(EOD, "20250106")] = [eod_row()]
    first = {**oi_row(oi=4), "timestamp": "2025-01-06T07:01:23.000"}
    later = {**oi_row(oi=5), "timestamp": "2025-01-06T09:11:47.000"}
    t.chain[(OI, "20250106")] = [later, first]          # arrival order must not matter
    cfg = {**CONFIG, "start": "2025-01-06", "end": "2025-01-07"}
    records, _ = pull(connector(t), cfg)
    assert len(records) == 1 and records[0]["data"]["open_interest"] == 5


def test_open_interest_window_one_request_per_window_same_rows():
    t = populated()
    for day in ("20250106", "20250103"):
        t.chain[(OI, day)] = [{**r, "timestamp": f"{day[:4]}-{day[4:6]}-{day[6:]}T07:01:23.000"}
                              for r in t.chain[(OI, day)]]
    calls = []
    inner = t.__call__

    def window_aware(url, params, timeout):
        path = url[len(BASE):]
        if path == OI and "start_date" in params:
            calls.append((params["start_date"], params["end_date"]))
            rows = []
            for day in sorted(d for (p, d) in t.chain if p == OI):
                if params["start_date"] <= day <= params["end_date"]:
                    rows.extend(t.chain[(OI, day)])
            return 200, {}, json.dumps(rows).encode()
        return inner(url, params, timeout)

    plain, _ = pull(connector(t))
    windowed, _ = pull(ThetaDataConnector(getter=window_aware, sleeper=lambda s: None),
                       {**CONFIG, "oi_window_days": 20})
    assert [r["data"] for r in windowed] == [r["data"] for r in plain]
    assert 1 <= len(calls) <= 2                      # 3 units, one or two aligned windows




def test_open_interest_window_too_large_falls_back_per_day():
    t = populated()
    for day in ("20250106", "20250103"):
        t.chain[(OI, day)] = [{**r, "timestamp": f"{day[:4]}-{day[4:6]}-{day[6:]}T07:01:23.000"}
                              for r in t.chain[(OI, day)]]
    inner = t.__call__

    def too_large_window(url, params, timeout):
        if url.endswith("open_interest") and "start_date" in params:
            return 570, {}, b"LARGE_REQUEST"
        return inner(url, params, timeout)

    windowed, _ = pull(ThetaDataConnector(getter=too_large_window, sleeper=lambda s: None),
                       {**CONFIG, "oi_window_days": 20})
    plain, _ = pull(connector(t))
    assert [r["data"] for r in windowed] == [r["data"] for r in plain]


def test_open_interest_window_validates():
    for bad in (0, 400, True, "20"):
        with pytest.raises(AssetError):
            ThetaDataConnector().resolve_knobs({**CONFIG, "oi_window_days": bad})
