"""libs/polymarket.py: Gamma events, fee regimes, CLOB books, and the pmxt
hour archive through the connector contract — no network, no hub.

Every transport seam on the pack is a METHOD (``get_json``, ``post_json``,
``download``, ``sleep``, ``now``), so the double below is a subclass with
class-level script tables — the ``stub_connectors`` idiom:
``run_acquisition`` instantiates the class itself, so a script must live
on the class, never on an instance.
"""

import json
import os
import sys
import urllib.error
from datetime import datetime, timedelta, timezone

import pytest

from dskit.assets.base import AssetError
from dskit.onboarding import acquire as acquire_module
from dskit.onboarding import (
    check_config,
    check_message,
    resolve_connector,
    run_acquisition,
    scan_stream,
)
from dskit.onboarding.libs import polymarket
from dskit.onboarding.libs.polymarket import (
    ARCHIVE_FIELDS,
    BOOK_FIELDS,
    EVENT_FIELDS,
    FEE_SCHEDULE_FIELDS,
    STREAMS,
    PolymarketConnector,
    fee_rate_of,
)
from dskit.onboarding.state import load_state

GAMMA = "https://gamma-api.polymarket.com/events"
CLOB = "https://clob.polymarket.com/books"
UTC = timezone.utc
NOW = datetime(2026, 3, 2, 12, 0, tzinfo=UTC)
#: A clock read inside read(): 30.25 s past NOW, inside the same minute.
LATE = NOW + timedelta(seconds=30, milliseconds=250)
#: LATE floored to the minute — the capture instant every undated row carries.
CAPTURE = NOW.isoformat()
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def _ms(dt):
    return (dt - EPOCH) // timedelta(milliseconds=1)


NOW_MS = _ms(NOW)
LATE_MS = _ms(LATE)
T1_MS = _ms(datetime(2026, 3, 1, 12, 0, tzinfo=UTC))
T1_ISO = "2026-03-01T12:00:00+00:00"


class ScriptedPolymarket(PolymarketConnector):
    """The pack with every transport replaced by a class-level script."""

    script = {}
    calls = []
    sleeps = []
    when = NOW

    def get_json(self, url, params, headers, timeout_s):
        type(self).calls.append(("GET", url, dict(params), dict(headers), timeout_s))
        return type(self).script["get"](url, dict(params))

    def post_json(self, url, body, headers, timeout_s):
        type(self).calls.append(("POST", url, body, dict(headers), timeout_s))
        return type(self).script["post"](url, body)

    def download(self, repo, path, token):
        type(self).calls.append(("DOWNLOAD", repo, path, token))
        return type(self).script["download"](repo, path, token)

    def sleep(self, seconds):
        type(self).sleeps.append(seconds)

    def now(self):
        return type(self).when


@pytest.fixture(autouse=True)
def reset_script():
    ScriptedPolymarket.script, ScriptedPolymarket.calls = {}, []
    ScriptedPolymarket.sleeps, ScriptedPolymarket.when = [], NOW
    yield
    ScriptedPolymarket.script, ScriptedPolymarket.calls = {}, []
    ScriptedPolymarket.sleeps, ScriptedPolymarket.when = [], NOW


@pytest.fixture
def conn():
    return ScriptedPolymarket()


@pytest.fixture
def write_parquet(tmp_path):
    pa = pytest.importorskip("pyarrow")
    pq = pytest.importorskip("pyarrow.parquet")

    def _write(name, columns):
        path = tmp_path / name
        pq.write_table(pa.table(columns), str(path))
        return str(path)

    return _write


def _read(conn, config, streams, state=None, mode="backfill"):
    msgs = list(conn.read(config, streams, state or {}, mode))
    for m in msgs:
        assert check_message(m) is not None  # every message envelope-valid
    return msgs


def _records(msgs):
    return [m for m in msgs if m["type"] == "RECORD"]


def _gets(kind="GET"):
    return [c for c in ScriptedPolymarket.calls if c[0] == kind]


# -- Gamma payload builders --------------------------------------------------


def _market(mid, slug, end, *, closed=True, tokens=("111", "222"),
            prices=("1", "0"), **extra):
    m = {
        "id": mid, "slug": slug, "question": f"Will {slug}?",
        "conditionId": f"0xc{mid}", "clobTokenIds": json.dumps(list(tokens)),
        "outcomes": json.dumps(["Yes", "No"]),
        "outcomePrices": json.dumps(list(prices)),
        "startDate": "2026-02-01T00:00:00Z", "endDate": end, "closed": closed,
        "umaResolutionStatus": "resolved" if closed else "", "resolvedBy": "0xuma",
        "volume": "123.4",
    }
    m.update(extra)
    return m


def _event(eid, slug, markets, series="poly-wx-nyc", **extra):
    ev = {
        "id": eid, "slug": slug, "endDate": markets[-1]["endDate"],
        "closed": all(m["closed"] for m in markets),
        "series": [{"slug": series}], "markets": markets,
    }
    ev.update(extra)
    return ev


EVENTS_CONFIG = {
    "series_slugs": ["poly-wx-nyc"],
    "start": "2026-03-01T00:00:00+00:00",
    "end": "2026-03-01T12:00:00+00:00",
    "window_hours": 6,
    "page_limit": 2,
}

#: Two 6-hour windows; the first needs two pages (full, then short).
PAGES = {
    ("2026-03-01T00:00:00Z", 0): [
        _event("e1", "nyc-mar-1a", [
            _market("m1", "nyc-1a-lo", "2026-03-01T01:00:00Z"),
            _market("m2", "nyc-1a-hi", "2026-03-01T02:00:00Z"),
        ]),
        _event("e2", "nyc-mar-1b", [_market("m3", "nyc-1b", "2026-03-01T03:00:00Z")]),
    ],
    ("2026-03-01T00:00:00Z", 2): [
        _event("e3", "nyc-mar-1c", [_market("m4", "nyc-1c", "2026-03-01T05:00:00Z")]),
    ],
    ("2026-03-01T06:00:00Z", 0): [
        _event("e4", "nyc-mar-1d", [_market("m5", "nyc-1d", "2026-03-01T07:00:00Z")]),
    ],
}


def _gamma_pages(url, params):
    assert url == GAMMA
    return PAGES[(params["end_date_min"], params["offset"])]


# -- spec / knobs -----------------------------------------------------------


def test_spec_passes_its_own_gate(conn):
    check_config(conn, EVENTS_CONFIG)
    check_config(conn, {**EVENTS_CONFIG, "notes": "documentation is always allowed"})
    check_config(conn, {})  # every knob is stream-scoped; none is globally required
    with pytest.raises(AssetError, match="unknown key"):
        check_config(conn, {**EVENTS_CONFIG, "surprise": 1})
    with pytest.raises(AssetError, match="secret knob"):
        check_config(conn, {"token_env": 5})


#: (module constant, knob it defaults, a sentinel to rebind it to). Every
#: knob with a default is here — a pin that omits a knob claims coverage
#: it lacks, so the set is asserted below against spec().
DEFAULTS = [
    ("DEFAULT_GAMMA_URL", "gamma_url", "https://gamma.example.test"),
    ("DEFAULT_CLOB_URL", "clob_url", "https://clob.example.test"),
    ("DEFAULT_TIMEOUT_S", "timeout_s", 99),
    ("DEFAULT_USER_AGENT", "user_agent", "ua/9.9"),
    ("DEFAULT_RETRIES", "retries", 7),
    ("DEFAULT_PACE_S", "pace_s", 1.5),
    ("DEFAULT_CLOSED", "closed", False),
    ("DEFAULT_WINDOW_HOURS", "window_hours", 3),
    ("DEFAULT_PAGE_LIMIT", "page_limit", 7),
    ("DEFAULT_MAX_OFFSET", "max_offset", 70),
    ("DEFAULT_BOOKS_CHUNK", "books_chunk", 9),
    ("DEFAULT_HF_REPO", "hf_repo", "someone/else"),
    ("DEFAULT_HF_PATH_PATTERN", "hf_path_pattern", "x/%Y.parquet"),
    ("DEFAULT_TOKEN_ENV", "token_env", "OTHER_TOKEN"),
    ("DEFAULT_CLEANUP", "cleanup", False),
]
UNDEFAULTED = {"series_slugs", "slugs", "token_ids", "hours", "start", "end"}


@pytest.mark.parametrize("const,knob,sentinel", DEFAULTS)
def test_every_default_has_one_name(conn, monkeypatch, const, knob, sentinel):
    # Rebind the constant: resolve_knobs AND the spec() notes must both
    # move, or a call site restated the literal.
    monkeypatch.setattr(polymarket, const, sentinel)
    assert conn.resolve_knobs({})[knob] == sentinel
    assert str(sentinel) in conn.spec()["params"][knob]["notes"]


def test_the_default_pin_covers_every_knob(conn):
    params = set(conn.spec()["params"])
    assert {knob for _c, knob, _s in DEFAULTS} == params - UNDEFAULTED
    assert set(conn.resolve_knobs({})) == params  # every knob resolves


def test_bad_shapes_refused(conn):
    bad = [
        ({"gamma_url": "ftp://nope"}, "gamma_url"),
        ({"clob_url": 5}, "clob_url"),
        ({"retries": -1}, "retries"),
        ({"retries": True}, "retries"),
        ({"window_hours": 0}, "window_hours"),
        ({"closed": "yes"}, "closed"),
        ({"hours": ["2026-03-01T10:30:00Z"]}, "hour boundary"),
        ({"hours": "2026-03-01T10:00:00Z"}, "hours"),
        ({"start": "2026-03-02", "end": "2026-03-01"}, "must be after"),
        ({"start": "yesterday"}, "start"),
        ({"token_ids": "t1"}, "token_ids"),
        ({"series_slugs": [""]}, "series_slugs"),
        ({"page_limit": 0}, "page_limit"),
        ({"max_offset": -5}, "max_offset"),
        ({"books_chunk": 0}, "books_chunk"),
        ({"timeout_s": 0}, "timeout_s"),
        ({"user_agent": ""}, "user_agent"),
        ({"pace_s": -1}, "pace_s"),
        ({"cleanup": 1}, "cleanup"),
        ({"hf_repo": ""}, "hf_repo"),
        ({"hf_path_pattern": 3}, "hf_path_pattern"),
        ({"token_env": ""}, "token_env"),
    ]
    for config, needle in bad:
        with pytest.raises(AssetError, match=needle):
            conn.resolve_knobs(config)
    with pytest.raises(AssetError, match="config must be a dict"):
        conn.resolve_knobs("nope")


def test_stream_preconditions_are_named(conn):
    conn.script["get"] = lambda url, params: []
    cases = [
        ({}, "events", "series_slugs"),
        ({"series_slugs": ["s"]}, "events", "start"),
        ({"slugs": ["e"]}, "fee_schedules", "series_slugs"),
        ({}, "books", "token_ids"),
        ({"token_ids": ["t"]}, "archive_hours", "hours"),
    ]
    for config, stream, needle in cases:
        with pytest.raises(AssetError, match=needle):
            list(conn.read(config, [stream], {}, "backfill"))
    with pytest.raises(AssetError, match="unknown stream"):
        list(conn.read({}, ["ghost"], {}, "backfill"))
    with pytest.raises(AssetError, match="mode"):
        list(conn.read({}, ["books"], {}, "sometimes"))
    with pytest.raises(AssetError, match="streams"):
        list(conn.read({}, [], {}, "live"))
    with pytest.raises(AssetError, match="state"):
        list(conn.read({}, ["books"], None, "live"))


# -- check ------------------------------------------------------------------


def test_check_pings_gamma_once(conn):
    conn.script["get"] = lambda url, params: [{"id": "x"}]
    conn.check({"token_ids": ["t"]})
    (call,) = _gets()
    assert call[1] == GAMMA and call[2] == {"limit": 1}
    assert call[3]["User-Agent"] == polymarket.DEFAULT_USER_AGENT
    assert call[4] == polymarket.DEFAULT_TIMEOUT_S

    conn.script["get"] = lambda url, params: {"not": "a list"}
    with pytest.raises(AssetError, match="list"):
        conn.check({})


# -- discover ---------------------------------------------------------------


def test_discover_declares_four_streams_offline(conn):
    streams = conn.discover({})
    assert [s["stream"] for s in streams] == list(STREAMS)
    by_name = {s["stream"]: s for s in streams}
    assert by_name["events"]["primary_key"] == ["market_id"]
    assert by_name["fee_schedules"]["primary_key"] == ["series_slug", "from_end_date"]
    assert by_name["books"]["primary_key"] == ["asset_id", "ts"]
    assert by_name["archive_hours"]["primary_key"] == ["asset_id", "ts", "seq"]
    assert by_name["events"]["schema"] == {"fields": list(EVENT_FIELDS)}
    assert by_name["fee_schedules"]["schema"] == {"fields": list(FEE_SCHEDULE_FIELDS)}
    assert by_name["books"]["schema"] == {"fields": list(BOOK_FIELDS)}
    assert by_name["archive_hours"]["schema"] == {"fields": list(ARCHIVE_FIELDS)}
    for stream in STREAMS:  # every key field is one the schema declares
        assert set(polymarket.STREAM_KEYS[stream]) <= set(polymarket.STREAM_FIELDS[stream])
    assert ScriptedPolymarket.calls == []


# -- events -----------------------------------------------------------------


def test_events_page_two_windows_one_record_per_market(conn):
    conn.script["get"] = _gamma_pages
    msgs = _read(conn, EVENTS_CONFIG, ["events"])
    gets = _gets()
    assert [(c[2]["end_date_min"], c[2]["end_date_max"], c[2]["offset"]) for c in gets] == [
        ("2026-03-01T00:00:00Z", "2026-03-01T06:00:00Z", 0),
        ("2026-03-01T00:00:00Z", "2026-03-01T06:00:00Z", 2),
        ("2026-03-01T06:00:00Z", "2026-03-01T12:00:00Z", 0),
    ]
    for c in gets:
        assert c[2]["closed"] == "true" and c[2]["series_slug"] == "poly-wx-nyc"
        assert c[2]["order"] == "endDate" and c[2]["ascending"] == "true"
        assert c[2]["limit"] == 2

    assert msgs[0]["type"] == "SCHEMA" and msgs[0]["schema"] == {"fields": list(EVENT_FIELDS)}
    records = _records(msgs)
    assert [r["data"]["market_id"] for r in records] == ["m1", "m2", "m3", "m4", "m5"]
    assert [r["effective_date"] for r in records] == [
        r["data"]["end_date"] for r in records
    ]
    assert all(r["kind"] == "observation" for r in records)
    first = records[0]["data"]
    assert set(first) == set(EVENT_FIELDS)
    assert first["event_slug"] == "nyc-mar-1a" and first["event_id"] == "e1"
    assert first["series_slug"] == "poly-wx-nyc"
    assert first["slug"] == "nyc-1a-lo" and first["condition_id"] == "0xcm1"
    assert first["clob_token_ids"] == ["111", "222"]
    assert first["outcomes"] == ["Yes", "No"]
    assert first["outcome_prices"] == [1.0, 0.0]
    assert first["closed"] is True
    assert first["fees_enabled"] is False and first["fee_type"] is None
    assert first["fee_rate"] is None and first["fee_schedule"] == {}
    assert first["resolution"] == {"umaResolutionStatus": "resolved", "resolvedBy": "0xuma"}
    assert msgs[-1]["state"] == {"events": {"cursor": "2026-03-01T07:00:00Z"}}


def test_events_offset_too_deep_refuses_and_names_the_window(conn):
    full = [_event(f"e{i}", f"ev-{i}", [_market(f"m{i}", f"mk-{i}", "2026-03-01T01:00:00Z")])
            for i in range(2)]
    conn.script["get"] = lambda url, params: full  # every page is full
    config = {**EVENTS_CONFIG, "max_offset": 2}
    with pytest.raises(AssetError) as exc:
        list(conn.read(config, ["events"], {}, "backfill"))
    text = str(exc.value)
    assert "max_offset" in text and "2026-03-01T00:00:00Z" in text
    assert "window_hours" in text  # the remedy is named
    assert [c[2]["offset"] for c in _gets()] == [0, 2]  # refused before offset 4


def test_events_dedup_a_market_seen_in_two_windows(conn):
    same = _event("e1", "ev-1", [_market("m1", "mk-1", "2026-03-01T06:00:00Z")])
    conn.script["get"] = lambda url, params: [same]
    records = _records(_read(conn, EVENTS_CONFIG, ["events"]))
    assert len(records) == 1


def test_events_by_slug_lookup_needs_no_window(conn):
    def lookup(url, params):
        assert params == {"slug": "ev-x"}
        return [_event("e9", "ev-x", [_market("m9", "mk-9", "2026-03-01T01:00:00Z")],
                       series="poly-other")]

    conn.script["get"] = lookup
    records = _records(_read(conn, {"slugs": ["ev-x"]}, ["events"]))
    assert [r["data"]["market_id"] for r in records] == ["m9"]
    assert records[0]["data"]["series_slug"] == "poly-other"  # from the payload

    conn.script["get"] = lambda url, params: []
    with pytest.raises(AssetError, match="ev-missing"):
        list(conn.read({"slugs": ["ev-missing"]}, ["events"], {}, "backfill"))


def test_events_never_filter_by_cursor_and_open_markets_are_forecasts(conn):
    closed = _market("m1", "mk-1", "2026-03-01T01:00:00Z")
    still_open = _market("m2", "mk-2", "2026-03-09T01:00:00Z", closed=False,
                         prices=("0.4", "0.6"))
    conn.script["get"] = lambda url, params: [_event("e1", "ev-1", [closed, still_open])]
    config = {**EVENTS_CONFIG, "closed": False}

    msgs = _read(conn, config, ["events"])
    assert _gets()[0][2]["closed"] == "false"
    records = {r["data"]["market_id"]: r for r in _records(msgs)}
    assert records["m1"]["kind"] == "observation"
    # The payload's own `closed` flag declares the kind: an open market's
    # end date lies ahead, so as an observation it would be refused as a
    # statement about the future. It is segregated, and never checkpoints.
    assert records["m2"]["kind"] == "forecast"
    assert records["m2"]["data"]["outcome_prices"] == [0.4, 0.6]
    assert msgs[-1]["state"] == {"events": {"cursor": "2026-03-01T01:00:00Z"}}

    # A market closes on a lag the cursor cannot see: m1's end_date sits
    # below a cursor some other market already advanced. Filtering by it
    # would drop m1 for good, so the cursor is recorded, never consulted:
    # the walk is whole every pull, and the cursor never regresses.
    ahead = {"events": {"cursor": "2026-03-01T05:00:00Z"}}
    again = _read(conn, config, ["events"], ahead)
    assert [(r["data"]["market_id"], r["kind"]) for r in _records(again)] == [
        ("m1", "observation"), ("m2", "forecast")]
    assert again[-1]["state"] == ahead


def test_clob_token_ids_decoding(conn):
    decoded = _market("m1", "mk-1", "2026-03-01T01:00:00Z", clobTokenIds=[1, "2"])
    conn.script["get"] = lambda url, params: [_event("e1", "ev-1", [decoded])]
    (record,) = _records(_read(conn, EVENTS_CONFIG, ["events"]))
    assert record["data"]["clob_token_ids"] == ["1", "2"]  # a list passes as strings

    broken = _market("m1", "mk-1", "2026-03-01T01:00:00Z", clobTokenIds="not json")
    conn.script["get"] = lambda url, params: [_event("e1", "ev-1", [broken])]
    with pytest.raises(AssetError, match="'m1'.*clobTokenIds"):
        list(conn.read(EVENTS_CONFIG, ["events"], {}, "backfill"))

    no_end = _market("m1", "mk-1", None)
    conn.script["get"] = lambda url, params: [_event("e1", "ev-1", [no_end], endDate=None)]
    with pytest.raises(AssetError, match="endDate"):
        list(conn.read(EVENTS_CONFIG, ["events"], {}, "backfill"))


def test_fee_rate_decoding_covers_both_spellings():
    assert fee_rate_of({"feeSchedule": {"rate": 0.07, "exponent": 1}}) == (0.07, 1)
    assert fee_rate_of({"feeSchedule": '{"rate": "0.05", "exponent": "2"}'}) == (0.05, 2)
    assert fee_rate_of({"feeSchedule": {"rate": 0.02}}) == (0.02, None)
    assert fee_rate_of({"feeRateBps": 500}) == (0.05, None)
    assert fee_rate_of({"feeRateBps": "0"}) == (0.0, None)
    assert fee_rate_of({"feeSchedule": {"rate": 0.07}, "feeRateBps": 100}) == (0.07, None)
    assert fee_rate_of({}) == (None, None)
    assert fee_rate_of({"feesEnabled": True}) == (None, None)
    with pytest.raises(AssetError, match="feeSchedule"):
        fee_rate_of({"feeSchedule": "not json"})
    with pytest.raises(AssetError, match="feeRateBps"):
        fee_rate_of({"feeRateBps": "lots"})


def test_events_carry_fee_fields_market_over_event(conn):
    market = _market("m1", "mk-1", "2026-03-01T01:00:00Z", feesEnabled=True,
                     feeSchedule={"rate": 0.07, "exponent": 1})
    event = _event("e1", "ev-1", [market], feeType="taker", feesEnabled=False)
    conn.script["get"] = lambda url, params: [event]
    (record,) = _records(_read(conn, EVENTS_CONFIG, ["events"]))
    data = record["data"]
    assert data["fees_enabled"] is True and data["fee_type"] == "taker"
    assert data["fee_rate"] == 0.07
    assert data["fee_schedule"] == {
        "feeType": "taker", "feesEnabled": True,
        "feeSchedule": {"rate": 0.07, "exponent": 1},
    }


# -- fee_schedules ----------------------------------------------------------


def test_fee_regimes_are_distinct_by_config(conn):
    markets = [
        _market("m1", "mk-1", "2026-03-01T01:00:00Z"),
        _market("m2", "mk-2", "2026-03-01T02:00:00Z", feesEnabled=True, feeType="taker",
                feeSchedule={"rate": 0.07, "exponent": 1}),
        _market("m3", "mk-3", "2026-03-01T03:00:00Z", feesEnabled=True, feeType="taker",
                feeSchedule='{"rate": 0.07, "exponent": 1}'),
        _market("m4", "mk-4", "2026-03-01T04:00:00Z", feesEnabled=True, feeType="taker",
                feeRateBps=500),
        _market("m5", "mk-5", "2026-03-01T05:00:00Z", feesEnabled=True, feeType="taker",
                feeSchedule={"rate": 0.07, "exponent": 1}),  # regime 2 again: no row
    ]
    # Served in reverse so the walk's own end_date sort is what orders regimes.
    conn.script["get"] = lambda url, params: (
        [_event("e1", "ev-1", list(reversed(markets)))] if params["offset"] == 0 else []
    )
    config = {**EVENTS_CONFIG, "page_limit": 100, "window_hours": 12}
    msgs = _read(conn, config, ["fee_schedules"])
    assert msgs[0]["schema"] == {"fields": list(FEE_SCHEDULE_FIELDS)}
    records = _records(msgs)
    assert [r["data"]["from_end_date"] for r in records] == [
        "2026-03-01T01:00:00Z", "2026-03-01T02:00:00Z", "2026-03-01T04:00:00Z",
    ]
    assert [r["data"]["example_slug"] for r in records] == ["mk-1", "mk-2", "mk-4"]
    assert [(r["data"]["fees_enabled"], r["data"]["fee_type"], r["data"]["fee_rate"],
             r["data"]["fee_exponent"]) for r in records] == [
        (False, None, None, None), (True, "taker", 0.07, 1), (True, "taker", 0.05, None),
    ]
    for r in records:
        assert set(r["data"]) == set(FEE_SCHEDULE_FIELDS)
        assert r["data"]["series_slug"] == "poly-wx-nyc"
        assert r["data"]["retrieved"] == NOW.isoformat()
        assert r["effective_date"] == NOW.isoformat()  # a table derived at the pull
        assert r["kind"] == "observation"
    assert msgs[-1]["state"] == {"fee_schedules": {"cursor": NOW.isoformat()}}

    # A re-derivation is never filtered by the cursor: the table is whole each pull.
    again = _read(conn, config, ["fee_schedules"], {"fee_schedules": {"cursor": NOW.isoformat()}})
    assert len(_records(again)) == 3


# -- books ------------------------------------------------------------------


BOOKS = {
    "t1": {
        "market": "0xc1", "asset_id": "t1", "timestamp": str(T1_MS), "hash": "h1",
        "bids": [{"price": "0.40", "size": "10"}, {"price": "0.45", "size": "5"},
                 {"price": "0.50", "size": "0"}, {"price": "0.30", "size": "-1"}],
        "asks": [{"price": "0.60", "size": "7"}, {"price": "0.55", "size": "3"},
                 {"price": "bad", "size": "1"}, {"price": "0.70"}],
    },
    "t2": {"asset_id": "t2", "bids": [], "asks": [["0.7", "2"], ["0.65", "1"]]},
}


def _clob(url, body):
    assert url == CLOB
    return [BOOKS[b["token_id"]] for b in body if b["token_id"] in BOOKS]


def test_books_chunk_sort_levels_and_fall_back_to_the_poll_instant(conn):
    conn.script["post"] = _clob
    config = {"token_ids": ["t1", "t2", "t3"], "books_chunk": 2}
    msgs = _read(conn, config, ["books"], mode="live")
    posts = _gets("POST")
    assert [c[2] for c in posts] == [
        [{"token_id": "t1"}, {"token_id": "t2"}], [{"token_id": "t3"}],
    ]
    assert msgs[0]["schema"] == {"fields": list(BOOK_FIELDS)}
    logs = [m for m in msgs if m["type"] == "LOG"]
    assert len(logs) == 1 and "t3" in logs[0]["message"]

    records = _records(msgs)
    assert [r["data"]["asset_id"] for r in records] == ["t1", "t2"]  # ts order
    t1, t2 = (r["data"] for r in records)
    assert set(t1) == set(BOOK_FIELDS)
    assert t1["ts"] == T1_MS and t1["asof_ts_ms"] == NOW_MS and t1["book_hash"] == "h1"
    assert t1["bids"] == [["0.45", "5"], ["0.40", "10"]]  # descending, dead levels dropped
    assert t1["asks"] == [["0.55", "3"], ["0.60", "7"]]  # ascending, unparseable dropped
    assert t1["event_type"] == "book"
    assert t1["price"] is None and t1["size"] is None and t1["side"] is None
    assert records[0]["effective_date"] == T1_ISO
    assert t2["ts"] == NOW_MS and t2["book_hash"] is None  # no server stamp: poll instant
    assert t2["asks"] == [["0.65", "1"], ["0.7", "2"]]  # pair-shaped levels, verbatim
    assert records[1]["effective_date"] == NOW.isoformat()
    assert msgs[-1]["state"] == {"books": {"cursor": NOW.isoformat()}}


def test_books_cursor_drops_books_not_stamped_after_it(conn):
    conn.script["post"] = _clob
    config = {"token_ids": ["t1", "t2"]}
    msgs = _read(conn, config, ["books"], {"books": {"cursor": T1_ISO}}, mode="live")
    assert [r["data"]["asset_id"] for r in _records(msgs)] == ["t2"]
    caught_up = _read(conn, config, ["books"], {"books": {"cursor": NOW.isoformat()}}, "live")
    assert _records(caught_up) == []
    assert caught_up[-1]["state"] == {"books": {"cursor": NOW.isoformat()}}


def test_undated_rows_and_late_book_stamps_take_the_capture_minute(conn):
    # The platform stamps acquired_at (second precision) BEFORE read() and
    # refuses an observation dated after it; a clock read inside read() is
    # always later. Undated rows take the minute floor; a book the venue
    # stamped inside that minute keeps its stamp as the key, dated at the
    # floor too.
    ScriptedPolymarket.when = LATE
    conn.script["get"] = lambda url, params: (
        [_event("e1", "ev-1", [_market("m1", "mk-1", "2026-03-01T01:00:00Z")])]
        if params["offset"] == 0 else [])
    (fee,) = _records(_read(conn, {"series_slugs": ["poly-wx-nyc"], "start": "2026-03-01"},
                            ["fee_schedules"]))
    assert fee["effective_date"] == CAPTURE and fee["data"]["retrieved"] == CAPTURE

    fresh = {"asset_id": "t9", "timestamp": str(LATE_MS + 1), "bids": [], "asks": []}
    conn.script["post"] = lambda url, body: [fresh, BOOKS["t2"]]
    msgs = _read(conn, {"token_ids": ["t9", "t2"]}, ["books"], mode="live")
    by_id = {r["data"]["asset_id"]: r for r in _records(msgs)}
    assert by_id["t2"]["data"]["ts"] == LATE_MS  # no venue stamp: the precise poll instant
    assert by_id["t9"]["data"]["ts"] == LATE_MS + 1  # the venue's stamp, verbatim
    assert [r["effective_date"] for r in by_id.values()] == [CAPTURE, CAPTURE]
    assert msgs[-1]["state"] == {
        "books": {"cursor": (LATE + timedelta(milliseconds=1)).isoformat()}}


def test_books_response_shape_refused(conn):
    conn.script["post"] = lambda url, body: {"not": "a list"}
    with pytest.raises(AssetError, match="list"):
        list(conn.read({"token_ids": ["t1"]}, ["books"], {}, "live"))
    conn.script["post"] = lambda url, body: [{"bids": []}]
    with pytest.raises(AssetError, match="asset_id"):
        list(conn.read({"token_ids": ["t1"]}, ["books"], {}, "live"))


# -- archive_hours ----------------------------------------------------------


HOUR = "2026-03-01T10:00:00+00:00"
HOUR_PATH = "hours/2026/03/01/polymarket_orderbook_2026-03-01T10.parquet"
ARCHIVE_CONFIG = {"token_ids": ["tokA"], "hours": [HOUR], "hf_repo": "r/x"}


def _serve(files):
    """A download script: (repo, path) -> local path; None when absent."""
    return lambda repo, path, token: files.get((repo, path))


def _archive_records(conn, config, state=None):
    msgs = _read(conn, config, ["archive_hours"], state)
    assert msgs[0]["schema"] == {"fields": list(ARCHIVE_FIELDS)}
    return _records(msgs), msgs


def test_archive_new_schema_filters_tokens_and_event_types(conn, write_parquet):
    path = write_parquet("new.parquet", {
        "asset_id": ["tokA", "tokA", "tokB", "tokA"],
        "timestamp": [T1_MS, T1_MS + 1, T1_MS + 2, T1_MS + 3],
        "event_type": ["book", "price_change", "book", "tick_size_change"],
        "bids": ['[["0.4","10"],["0.45","5"]]', None, '[["0.1","1"]]', None],
        "asks": ['[["0.6","7"],["0.55","3"]]', None, None, None],
        "price": [None, "0.45", None, None],
        "size": [None, "5", None, None],
        "side": [None, "BUY", None, None],
    })
    conn.script["download"] = _serve({("r/x", HOUR_PATH): path})
    records, msgs = _archive_records(conn, ARCHIVE_CONFIG)
    assert ScriptedPolymarket.calls == [
        ("DOWNLOAD", "r/x", polymarket.SYNC_STATE_PATH, None),  # the mirror's sync state
        ("DOWNLOAD", "r/x", HOUR_PATH, None),
    ]
    assert [r["data"]["event_type"] for r in records] == ["book", "price_change"]
    book, change = (r["data"] for r in records)
    assert set(book) == set(ARCHIVE_FIELDS)
    assert book["asset_id"] == "tokA" and book["ts"] == T1_MS
    assert book["bids"] == [["0.45", "5"], ["0.4", "10"]]
    assert book["asks"] == [["0.55", "3"], ["0.6", "7"]]
    assert change["price"] == "0.45" and change["size"] == "5" and change["side"] == "BUY"
    assert change["bids"] == [] and change["asks"] == []
    assert records[0]["effective_date"] == T1_ISO
    assert records[1]["effective_date"] == "2026-03-01T12:00:00.001000+00:00"
    assert msgs[-1]["state"] == {"archive_hours": {"cursor": HOUR}}
    assert not os.path.exists(path)  # cleanup default: the hour file is gone


def test_archive_mid_schema_rebuilds_pairs_and_cleanup_can_be_kept(conn, write_parquet):
    path = write_parquet("mid.parquet", {
        "asset_id": ["tokA", "tokA"],
        "timestamp": [str(T1_MS), str(T1_MS + 5)],
        "event_type": ["book", "price_change"],
        "bid_prices": [["0.4", "0.45"], []],
        "bid_sizes": [["10", "5"], []],
        "ask_prices": [["0.6"], []],
        "ask_sizes": [["7"], []],
        "price": [None, 0.45],
        "size": [None, 5.0],
        "side": [None, "SELL"],
    })
    conn.script["download"] = _serve({("r/x", HOUR_PATH): path})
    records, _msgs = _archive_records(conn, {**ARCHIVE_CONFIG, "cleanup": False})
    book, change = (r["data"] for r in records)
    assert book["bids"] == [["0.45", "5"], ["0.4", "10"]] and book["asks"] == [["0.6", "7"]]
    assert book["ts"] == T1_MS  # a string stamp decodes to int ms
    assert change["price"] == "0.45" and change["size"] == "5.0" and change["side"] == "SELL"
    assert os.path.exists(path)


def test_archive_old_schema_extracts_the_json_message(conn, write_parquet):
    path = write_parquet("old.parquet", {
        "update_type": ["book", "price_change", "book"],
        "data": [
            json.dumps({"token_id": "tokA", "timestamp": str(T1_MS),
                        "bids": [{"price": "0.4", "size": "10"}], "asks": [],
                        "hash": "h"}),
            json.dumps({"asset_id": "tokA", "timestamp": T1_MS + 1,
                        "change_price": "0.45", "change_size": "5",
                        "change_side": "SELL"}),
            json.dumps({"asset_id": "tokZ", "timestamp": T1_MS + 2, "bids": [], "asks": []}),
        ],
    })
    conn.script["download"] = _serve({("r/x", HOUR_PATH): path})
    records, _msgs = _archive_records(conn, ARCHIVE_CONFIG)
    book, change = (r["data"] for r in records)
    assert book["asset_id"] == "tokA" and book["ts"] == T1_MS
    assert book["bids"] == [["0.4", "10"]] and book["asks"] == []
    assert change["event_type"] == "price_change"
    assert (change["price"], change["size"], change["side"]) == ("0.45", "5", "SELL")


def test_archive_cursor_skips_done_hours_and_an_absent_hour_stops(conn, write_parquet):
    empty = write_parquet("empty.parquet", {
        "asset_id": ["tokB"], "timestamp": [T1_MS], "event_type": ["book"],
        "bids": ["[]"], "asks": ["[]"], "price": [None], "size": [None], "side": [None],
    })
    hours = [f"2026-03-01T{h:02d}:00:00+00:00" for h in (9, 10, 11, 12)]
    paths = {h: f"hours/2026/03/01/polymarket_orderbook_2026-03-01T{h:02d}.parquet"
             for h in (9, 10, 11, 12)}
    conn.script["download"] = _serve({("r/x", paths[10]): empty})  # 11 and 12 absent
    config = {**ARCHIVE_CONFIG, "hours": hours, "cleanup": False}
    records, msgs = _archive_records(conn, config, {"archive_hours": {"cursor": hours[0]}})
    assert records == []
    assert [c[2] for c in _gets("DOWNLOAD")] == [
        polymarket.SYNC_STATE_PATH, paths[10], paths[11]]  # 9 skipped, stop at 11
    logs = [m["message"] for m in msgs if m["type"] == "LOG"]
    assert len(logs) == 2 and paths[11] in logs[1]  # logs[0]: no sync state is served here
    assert msgs[-1]["state"] == {"archive_hours": {"cursor": hours[1]}}


def test_archive_window_mode_walks_complete_hours_only(conn, write_parquet):
    empty = write_parquet("empty.parquet", {
        "asset_id": ["tokB"], "timestamp": [T1_MS], "event_type": ["book"],
        "bids": ["[]"], "asks": ["[]"], "price": [None], "size": [None], "side": [None],
    })
    conn.script["download"] = lambda repo, path, token: empty
    # NOW is 12:00; the 12:00 hour is incomplete, so 09, 10 and 11 are pulled.
    config = {"token_ids": ["tokA"], "start": "2026-03-02T09:30:00+00:00", "cleanup": False}
    _records_, msgs = _archive_records(conn, config)
    assert [c[2] for c in _gets("DOWNLOAD")] == [polymarket.SYNC_STATE_PATH] + [
        f"hours/2026/03/02/polymarket_orderbook_2026-03-02T{h:02d}.parquet" for h in (9, 10, 11)
    ]
    assert msgs[-1]["state"] == {"archive_hours": {"cursor": "2026-03-02T11:00:00+00:00"}}

    ScriptedPolymarket.calls = []
    explicit = {**config, "hours": ["2026-03-02T10:00:00+00:00"], "end": "2026-03-02T12:00:00Z"}
    _archive_records(conn, explicit)
    assert len(_gets("DOWNLOAD")) == 2  # the sync state, then the one explicit hour


def test_archive_token_env_is_optional_and_named(conn, write_parquet, monkeypatch):
    empty = write_parquet("empty.parquet", {
        "asset_id": ["tokB"], "timestamp": [T1_MS], "event_type": ["book"],
        "bids": ["[]"], "asks": ["[]"], "price": [None], "size": [None], "side": [None],
    })
    conn.script["download"] = lambda repo, path, token: empty
    monkeypatch.delenv("HF_TOKEN", raising=False)
    _archive_records(conn, {**ARCHIVE_CONFIG, "cleanup": False})
    assert _gets("DOWNLOAD")[-1][3] is None  # anonymous when the variable is unset

    monkeypatch.setenv("MY_HF", "hf_secret")
    _archive_records(conn, {**ARCHIVE_CONFIG, "cleanup": False, "token_env": "MY_HF"})
    assert _gets("DOWNLOAD")[-1][3] == "hf_secret"


def test_archive_unrecognized_schema_refused(conn, write_parquet):
    path = write_parquet("odd.parquet", {"foo": [1]})
    conn.script["download"] = _serve({("r/x", HOUR_PATH): path})
    with pytest.raises(AssetError, match="schema.*foo"):
        list(conn.read(ARCHIVE_CONFIG, ["archive_hours"], {}, "backfill"))


def test_archive_without_the_hub_client_refuses_by_name(monkeypatch):
    monkeypatch.setitem(sys.modules, "huggingface_hub", None)  # import -> ImportError
    with pytest.raises(AssetError, match="huggingface_hub"):
        list(PolymarketConnector().read(ARCHIVE_CONFIG, ["archive_hours"], {}, "backfill"))
    with pytest.raises(AssetError, match="huggingface_hub"):
        PolymarketConnector().download("r/x", HOUR_PATH, None)


def test_default_archive_coordinates_render_the_real_hub_layout():
    # A deliberate restatement of the hub tree as listed on 2026-09-04 —
    # never derived from the pattern under test.
    hour = datetime(2026, 8, 10, 0, tzinfo=UTC)
    assert hour.strftime(polymarket.DEFAULT_HF_PATH_PATTERN) == (
        "hours/2026/08/10/polymarket_orderbook_2026-08-10T00.parquet")
    assert polymarket.DEFAULT_HF_REPO == "phobia76/pmxt-l2-dump"


def test_archive_streams_batches_and_never_reads_an_hour_whole(conn, monkeypatch, tmp_path):
    # An hour file is ~360 MB: rows must stream out of ParquetFile.iter_batches
    # before the next batch exists, and the file is never read() whole.
    pa = pytest.importorskip("pyarrow")
    pq = pytest.importorskip("pyarrow.parquet")
    path = str(tmp_path / "hour.parquet")
    pq.write_table(pa.table({
        "asset_id": ["tokA", "tokB", "tokA", "tokB"],
        "timestamp": [T1_MS, T1_MS + 1, T1_MS + 2, T1_MS + 3],
        "event_type": ["book"] * 4, "bids": ["[]"] * 4, "asks": ["[]"] * 4,
        "price": [None] * 4, "size": [None] * 4, "side": [None] * 4,
    }), path)
    produced = []
    iter_batches = pq.ParquetFile.iter_batches

    def spying_iter_batches(self, *args, **kwargs):
        for batch in iter_batches(self, *args, **kwargs):
            produced.append(batch.num_rows)
            yield batch

    monkeypatch.setattr(pq.ParquetFile, "iter_batches", spying_iter_batches)
    monkeypatch.setattr(pq.ParquetFile, "read",
                        lambda self, *a, **k: pytest.fail("the hour file was read whole"))
    monkeypatch.setattr(polymarket, "_ARCHIVE_BATCH_ROWS", 2)  # two batches of two rows
    conn.script["download"] = _serve({("r/x", HOUR_PATH): path})
    messages = conn.read({**ARCHIVE_CONFIG, "cleanup": False}, ["archive_hours"], {}, "backfill")
    assert next(messages)["type"] == "SCHEMA"
    assert next(messages)["type"] == "LOG"  # no sync state is served here
    first = next(messages)
    assert first["data"]["ts"] == T1_MS and produced == [2]  # out before batch two is read
    rest = list(messages)
    assert [m["data"]["ts"] for m in _records(rest)] == [T1_MS + 2] and produced == [2, 2]


def test_cleanup_removes_the_cache_link_and_its_blob(conn, write_parquet, tmp_path):
    # hf_hub_download returns a snapshot SYMLINK into the blob store, so a
    # cleanup that unlinked only the returned path would leave ~360 MB behind.
    blob = write_parquet("blob.parquet", {
        "asset_id": ["tokB"], "timestamp": [T1_MS], "event_type": ["book"],
        "bids": ["[]"], "asks": ["[]"], "price": [None], "size": [None], "side": [None],
    })
    link = str(tmp_path / "link.parquet")
    os.symlink(blob, link)
    conn.script["download"] = _serve({("r/x", HOUR_PATH): link})
    _archive_records(conn, {**ARCHIVE_CONFIG, "cleanup": False})
    assert os.path.lexists(link) and os.path.exists(blob)
    _archive_records(conn, ARCHIVE_CONFIG)  # cleanup default: True
    assert not os.path.lexists(link) and not os.path.exists(blob)


# -- the real download(): the hub client under the seam ----------------------


def _hub_double(monkeypatch, outcome):
    """Script the real ``download()``'s ``hf_hub_download``; return its kwargs log.

    ``outcome`` is returned, or raised when it is an exception. Keyword-only,
    so a positional call from the pack is a TypeError here.
    """
    hub = pytest.importorskip("huggingface_hub")
    calls = []

    def fake_hf_hub_download(**kwargs):
        calls.append(kwargs)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    monkeypatch.setattr(hub, "hf_hub_download", fake_hf_hub_download)
    return calls


def _hub_http_error(status, message):
    """A real ``HfHubHTTPError``, as the hub raises it on a refused request."""
    httpx = pytest.importorskip("httpx")
    utils = pytest.importorskip("huggingface_hub.utils")
    response = httpx.Response(status, request=httpx.Request("GET", "https://huggingface.co/x"))
    return utils.HfHubHTTPError(message, response=response)


def test_download_passes_the_dataset_coordinates_and_the_token(monkeypatch, tmp_path):
    local = str(tmp_path / "hour.parquet")
    calls = _hub_double(monkeypatch, local)
    conn = PolymarketConnector()  # the real body, no override
    assert conn.download("r/x", HOUR_PATH, "hf_SECRET") == local
    assert conn.download("r/x", HOUR_PATH, None) == local
    assert calls == [
        {"repo_id": "r/x", "filename": HOUR_PATH, "repo_type": "dataset", "token": "hf_SECRET"},
        {"repo_id": "r/x", "filename": HOUR_PATH, "repo_type": "dataset", "token": None},
    ]


def test_download_maps_a_missing_entry_to_none_but_refuses_offline_and_http_faults(monkeypatch):
    utils = pytest.importorskip("huggingface_hub.utils")
    conn = PolymarketConnector()
    _hub_double(monkeypatch, utils.EntryNotFoundError("no such file"))
    assert conn.download("r/x", HOUR_PATH, None) is None

    # LocalEntryNotFoundError IS an EntryNotFoundError, but it means the hub
    # was unreachable and nothing is cached — not that the hour is absent,
    # so it must never answer None (the LOG would say "step over it").
    _hub_double(monkeypatch, utils.LocalEntryNotFoundError("connection error"))
    with pytest.raises(AssetError, match="unreachable") as exc:
        conn.download("r/x", HOUR_PATH, None)
    assert HOUR_PATH in str(exc.value)

    _hub_double(monkeypatch, _hub_http_error(401, "401 Client Error: Unauthorized"))
    with pytest.raises(AssetError, match="401") as exc:
        conn.download("r/x", HOUR_PATH, "hf_SECRET")
    assert "r/x" in str(exc.value) and HOUR_PATH in str(exc.value)
    assert "hf_SECRET" not in str(exc.value)


def test_download_maps_the_hubs_own_404_to_none(monkeypatch):
    # What hub 1.x actually raises for a missing file is
    # RemoteEntryNotFoundError — an HfHubHTTPError AND an EntryNotFoundError
    # — so the EntryNotFoundError clause must come BEFORE the HfHubHTTPError
    # one. The bare base class scripted above is not an HfHubHTTPError and
    # would pass with the two clauses swapped, turning every absent hour
    # into a refusal.
    errors = pytest.importorskip("huggingface_hub.errors")
    httpx = pytest.importorskip("httpx")
    response = httpx.Response(404, request=httpx.Request("GET", "https://huggingface.co/x"))
    missing = errors.RemoteEntryNotFoundError("404 Client Error: Entry Not Found",
                                              response=response)
    assert isinstance(missing, errors.HfHubHTTPError)  # the order this pins
    _hub_double(monkeypatch, missing)
    assert PolymarketConnector().download("r/x", HOUR_PATH, None) is None


def test_token_is_read_at_pull_time_and_never_written_anywhere(monkeypatch, write_parquet):
    served = write_parquet("hour.parquet", {
        "asset_id": ["tokA"], "timestamp": [T1_MS], "event_type": ["book"],
        "bids": ["[]"], "asks": ["[]"], "price": [None], "size": [None], "side": [None],
    })
    calls = _hub_double(monkeypatch, served)
    monkeypatch.setenv("MY_HF", "hf_SECRET")
    config = {**ARCHIVE_CONFIG, "token_env": "MY_HF", "cleanup": False}
    msgs = _read(PolymarketConnector(), config, ["archive_hours"])  # the real download()
    assert calls[0]["token"] == "hf_SECRET"  # reaches the hub ...
    assert len(_records(msgs)) == 1
    assert "hf_SECRET" not in json.dumps(msgs)  # ... and nothing else: no record, log or state

    monkeypatch.setenv("MY_HF", "hf_ROTATED")  # read at each pull, never cached
    _read(PolymarketConnector(), config, ["archive_hours"])
    assert calls[-1]["token"] == "hf_ROTATED"

    _hub_double(monkeypatch, _hub_http_error(403, "403 Client Error: Forbidden"))
    with pytest.raises(AssetError) as exc:
        list(PolymarketConnector().read(config, ["archive_hours"], {}, "backfill"))
    assert "hf_ROTATED" not in str(exc.value) and "hf_ROTATED" not in repr(exc.value)


# -- retry, backoff, pacing -------------------------------------------------


def _http_error(code, headers=None):
    return urllib.error.HTTPError(GAMMA, code, "scripted", headers or {}, None)


def test_retries_back_off_honor_retry_after_then_give_up(conn):
    queue = [_http_error(429, {"Retry-After": "3"}), _http_error(503),
             OSError("connection reset"), [{"id": "x"}]]

    def flaky(url, params):
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    conn.script["get"] = flaky
    conn.check({})  # recovered on the fourth attempt
    assert ScriptedPolymarket.sleeps == [3.0, 1.0, 2.0]  # header, then 0.5 * 2**attempt

    queue[:] = [_http_error(503), _http_error(503)]
    ScriptedPolymarket.sleeps = []
    with pytest.raises(AssetError, match="giving up.*HTTP 503"):
        conn.check({"retries": 1})
    assert ScriptedPolymarket.sleeps == [0.5]

    queue[:] = [_http_error(404)]
    ScriptedPolymarket.sleeps = []
    with pytest.raises(AssetError, match="HTTP 404"):
        conn.check({})
    assert ScriptedPolymarket.sleeps == []  # a client error never retries


def test_retry_after_is_capped_and_non_numeric_falls_back(conn):
    queue = [_http_error(429, {"Retry-After": "9999"}),
             _http_error(429, {"Retry-After": "Fri, 31 Dec 1999 23:59:59 GMT"}), []]

    def flaky(url, params):
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    conn.script["get"] = flaky
    conn.check({})
    assert ScriptedPolymarket.sleeps == [polymarket.MAX_BACKOFF_S, 1.0]


def test_retry_after_nan_is_unusable_and_falls_back(conn):
    # float("nan") parses, and min(max(nan, 0.0), cap) is still NaN — so
    # without the guard the wait reached time.sleep as NaN: a ValueError,
    # neither capped nor an AssetError. Unusable means the exponential
    # backoff, exactly like a non-numeric header. This pack's guard is now
    # `connector.retry_after`, the one owner of the rule (ADR-0101).
    assert polymarket.retry_after({"Retry-After": "nan"}, 0.5) == 0.5
    queue = [_http_error(429, {"Retry-After": "nan"}), []]

    def flaky(url, params):
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    conn.script["get"] = flaky
    conn.check({})
    assert ScriptedPolymarket.sleeps == [0.5]


def test_requests_are_paced_between_calls_not_before_the_first(conn):
    conn.script["get"] = _gamma_pages
    _read(conn, {**EVENTS_CONFIG, "pace_s": 0.1}, ["events"])
    assert len(_gets()) == 3
    assert ScriptedPolymarket.sleeps == [0.1, 0.1]

    ScriptedPolymarket.sleeps = []
    _read(conn, {**EVENTS_CONFIG, "pace_s": 0}, ["events"])
    assert ScriptedPolymarket.sleeps == []


# -- the platform -----------------------------------------------------------


def test_registered_kind_resolves():
    assert resolve_connector("polymarket") is PolymarketConnector


def test_acquisition_commits_one_books_snapshot(root, registry, monkeypatch):
    # The platform stamps acquired_at (second precision) just before
    # read(); the stub clock inside read() is 250 ms later. A book dated
    # at that clock is refused as an observation about the future; dated
    # at the capture minute it commits.
    monkeypatch.setattr(acquire_module, "utc_now", lambda: "2026-03-02T12:00:30+00:00")
    ScriptedPolymarket.when = LATE
    ScriptedPolymarket.script = {"get": lambda url, params: [{"id": "ping"}], "post": _clob}
    version = registry.register("source_config", {
        "name": "poly",
        "catalog_source": "poly-source",
        "connector": "tests.onboarding.test_polymarket:ScriptedPolymarket",
        "config": {"token_ids": ["t1", "t2"]},
    }, origin="test")
    registry.transition(version, "active", origin="test")

    first = run_acquisition(root, registry, "poly", "books", "live")
    assert first["records"] == 2 and first["snapshot"]
    assert first["state_saved"]
    caught_up = run_acquisition(root, registry, "poly", "books", "live")
    assert caught_up["records"] == 0 and caught_up["snapshot"] is None


def test_acquisition_walks_two_archive_hours_and_stops_at_a_missing_one(
        root, registry, write_parquet):
    hours = [f"2026-03-01T{h:02d}:00:00+00:00" for h in (10, 11, 12)]
    paths = {h: f"hours/2026/03/01/polymarket_orderbook_2026-03-01T{h:02d}.parquet"
             for h in (10, 11, 12)}
    files = {("r/x", paths[h]): write_parquet(f"h{h}.parquet", {
        "asset_id": ["tokA", "tokB", "tokA"],
        "timestamp": [T1_MS + h, T1_MS + h + 1, T1_MS + h + 2],
        "event_type": ["book", "book", "price_change"],
        "bids": ["[]", "[]", None], "asks": ["[]", "[]", None],
        "price": [None, None, "0.5"], "size": [None, None, "1"], "side": [None, None, "BUY"],
    }) for h in (10, 11)}  # hour 12 absent
    ScriptedPolymarket.script = {"get": lambda url, params: [{"id": "ping"}],
                                 "download": _serve(files)}
    version = registry.register("source_config", {
        "name": "poly",
        "catalog_source": "poly-source",
        "connector": "tests.onboarding.test_polymarket:ScriptedPolymarket",
        "config": {"token_ids": ["tokA"], "hours": hours, "hf_repo": "r/x"},
    }, origin="test")
    registry.transition(version, "active", origin="test")

    first = run_acquisition(root, registry, "poly", "archive_hours", "backfill")
    assert first["records"] == 4 and first["snapshot"] and first["state_saved"]
    # No sync state is served: the walk behaves exactly as it did before one
    # existed — every hour tried in turn, the first absent hour stopping it.
    assert [c[2] for c in _gets("DOWNLOAD")] == [
        polymarket.SYNC_STATE_PATH, paths[10], paths[11], paths[12]]
    assert len(first["logs"]) == 2 and polymarket.SYNC_STATE_PATH in first["logs"][0]
    assert paths[12] in first["logs"][1]
    assert not any(os.path.exists(p) for p in files.values())  # cleanup default: True
    landed = {"archive_hours": {"cursor": hours[1]}}  # the last hour fully read
    assert load_state(root, "poly", "archive_hours", "backfill") == landed

    ScriptedPolymarket.calls = []
    again = run_acquisition(root, registry, "poly", "archive_hours", "backfill")
    assert again["records"] == 0 and again["snapshot"] is None
    assert [c[2] for c in _gets("DOWNLOAD")] == [
        polymarket.SYNC_STATE_PATH, paths[12]]  # 10 and 11 never re-read
    assert len(again["logs"]) == 2 and paths[12] in again["logs"][1]
    assert load_state(root, "poly", "archive_hours", "backfill") == landed  # unchanged


# -- events: the venue's closedTime dates a closed market ---

#: The venue's own spelling of the instant a market actually resolved.
CLOSED_TIME = "2026-03-02 11:59:01+00"
CLOSED_TIME_ISO = "2026-03-02T11:59:01+00:00"
#: A scheduled end still ahead of the pull clock (NOW).
AHEAD = "2026-03-05T00:00:00Z"


def _one_event(conn, market, slug="ev-1"):
    conn.script["get"] = lambda url, params: [_event("e1", slug, [market])]
    return _read(conn, {"slugs": [slug]}, ["events"])


def test_a_market_that_resolved_early_is_dated_at_the_closed_time(conn):
    # ADR-0080: closed: true with the scheduled end still ahead. Dated at
    # end_date this is a statement about the future and the platform
    # refuses the pull whole; dated at the venue's closedTime it is what
    # it is — an observation of a market that resolved early.
    msgs = _one_event(conn, _market("m1", "mk-1", AHEAD, closedTime=CLOSED_TIME))
    (record,) = _records(msgs)
    assert record["kind"] == "observation"
    assert record["effective_date"] == CLOSED_TIME_ISO
    assert record["data"]["closed_time"] == CLOSED_TIME_ISO
    assert record["data"]["end_date"] == AHEAD  # nothing already read is lost
    assert set(record["data"]) == set(EVENT_FIELDS)
    assert msgs[-1]["state"] == {"events": {"cursor": CLOSED_TIME_ISO}}


def test_a_closed_market_the_venue_never_dated_still_dates_at_its_end(conn):
    msgs = _one_event(conn, _market("m1", "mk-1", "2026-03-01T01:00:00Z"))
    (record,) = _records(msgs)
    assert record["kind"] == "observation"
    assert record["effective_date"] == "2026-03-01T01:00:00Z"  # today's behaviour
    assert record["data"]["closed_time"] is None
    assert msgs[-1]["state"] == {"events": {"cursor": "2026-03-01T01:00:00Z"}}


def test_a_closed_market_with_neither_instant_behind_the_pull_refuses_by_name(conn):
    undated = _market("m1", "mk-1", AHEAD)  # closed, and the venue dated no close
    later = _market("m2", "mk-2", AHEAD, closedTime="2026-03-06 00:00:00+00")
    conn.script["get"] = lambda url, params: [_event("e1", "ev-1", [undated, later])]
    with pytest.raises(AssetError) as exc:
        list(conn.read({"slugs": ["ev-1"]}, ["events"], {}, "backfill"))
    text = str(exc.value)
    assert "'m1'" in text and "'m2'" in text  # both named, errors accumulate
    assert AHEAD in text and "2026-03-06" in text  # both instants named
    assert "closed_time" in text and "end_date" in text


def test_a_closed_market_dated_ahead_refuses_even_when_its_end_is_behind(conn):
    # The venue's closedTime IS the instant (never date arithmetic), so a
    # closed market the venue dates in the future is self-contradictory:
    # the pack refuses by name rather than quietly falling back to the
    # end_date it did not choose.
    contradictory = _market("m1", "mk-1", "2026-03-01T01:00:00Z",
                            closedTime="2026-03-06 00:00:00+00")
    conn.script["get"] = lambda url, params: [_event("e1", "ev-1", [contradictory])]
    with pytest.raises(AssetError) as exc:
        list(conn.read({"slugs": ["ev-1"]}, ["events"], {}, "backfill"))
    text = str(exc.value)
    assert "'m1'" in text and "2026-03-06" in text and "2026-03-01T01:00:00Z" in text


def test_an_open_market_is_untouched_by_the_closed_time_rule(conn):
    closed = _market("m1", "mk-1", "2026-03-01T01:00:00Z")
    # Open markets carry closed: false and a blank closedTime; a blank
    # stamp is no stamp, and the kind stays the venue's own flag.
    still_open = _market("m2", "mk-2", AHEAD, closed=False, closedTime="")
    conn.script["get"] = lambda url, params: [_event("e1", "ev-1", [closed, still_open])]
    msgs = _read(conn, {"slugs": ["ev-1"]}, ["events"])
    records = {r["data"]["market_id"]: r for r in _records(msgs)}
    assert records["m2"]["kind"] == "forecast"
    assert records["m2"]["effective_date"] == AHEAD
    assert records["m2"]["data"]["closed_time"] is None
    # An open market still never moves the cursor.
    assert msgs[-1]["state"] == {"events": {"cursor": "2026-03-01T01:00:00Z"}}


def test_the_venues_closed_time_spelling_round_trips():
    label = "market 'm1'"
    for spelling in (CLOSED_TIME, "2026-03-02T11:59:01Z", CLOSED_TIME_ISO):
        assert polymarket._closed_time(spelling, label) == CLOSED_TIME_ISO
    assert polymarket._closed_time(None, label) is None
    assert polymarket._closed_time("   ", label) is None  # blank is absent
    with pytest.raises(AssetError, match="m1.*closedTime"):
        polymarket._closed_time("resolved yesterday", label)


def test_acquisition_commits_a_market_that_resolved_early(root, registry, monkeypatch):
    # The commit instant (ADR-0079) sits before the scheduled end and
    # after the close: dated at end_date the ADR-0014 assertion refuses
    # the pull, dated at closedTime it commits.
    monkeypatch.setattr(acquire_module, "utc_now", lambda: "2026-03-02T12:00:30+00:00")
    early = _market("m1", "mk-1", AHEAD, closedTime=CLOSED_TIME)
    ScriptedPolymarket.script = {"get": lambda url, params: [_event("e1", "ev-1", [early])]}
    version = registry.register("source_config", {
        "name": "poly-events",
        "catalog_source": "poly-source",
        "connector": "tests.onboarding.test_polymarket:ScriptedPolymarket",
        "config": {"slugs": ["ev-1"]},
    }, origin="test")
    registry.transition(version, "active", origin="test")

    result = run_acquisition(root, registry, "poly-events", "events", "backfill")
    assert result["records"] == 1 and result["snapshot"] and result["state_saved"]
    assert load_state(root, "poly-events", "events", "backfill") == {
        "events": {"cursor": CLOSED_TIME_ISO}}


# -- archive_hours: token resolution and the mirror's sync state ---


#: The token ids the Gamma answer below carries; tokZ is a stranger.
RESOLVED = ("tokA", "tokX")
#: An archive walk that declares NO token_ids — they come from Gamma.
RESOLVE_CONFIG = {
    "series_slugs": ["poly-wx-nyc"], "hours": [HOUR], "hf_repo": "r/x",
    "start": "2026-03-01T00:00:00+00:00", "end": "2026-03-01T06:00:00+00:00",
    "cleanup": False,
}


def _resolve_pages(url, params):
    """One event, one market, two clobTokenIds — the resolution walk's answer."""
    assert url == GAMMA
    return [_event("e9", "nyc-mar-1z",
                   [_market("m9", "nyc-1z", "2026-03-01T05:00:00Z", tokens=RESOLVED)])]


def _three_token_hour(write_parquet, name="resolve.parquet"):
    """An hour file with one book row for each of tokA, tokX and tokZ."""
    return write_parquet(name, {
        "asset_id": ["tokA", "tokX", "tokZ"],
        "timestamp": [T1_MS, T1_MS + 1, T1_MS + 2],
        "event_type": ["book"] * 3, "bids": ["[]"] * 3, "asks": ["[]"] * 3,
        "price": [None] * 3, "size": [None] * 3, "side": [None] * 3,
    })


def _one_token_hour(write_parquet, name="one.parquet", at=T1_MS):
    """An hour file with a single tokA book row."""
    return write_parquet(name, {
        "asset_id": ["tokA"], "timestamp": [at], "event_type": ["book"],
        "bids": ["[]"], "asks": ["[]"], "price": [None], "size": [None], "side": [None],
    })


def _write_meta(tmp_path, payload, name="sync-state.json"):
    """Write a pmxt sync-state document and return its local path."""
    path = tmp_path / name
    path.write_text(json.dumps(payload), encoding="utf-8")
    return str(path)


def test_archive_resolves_token_ids_from_gamma_when_none_are_declared(conn, write_parquet):
    conn.script["get"] = _resolve_pages
    conn.script["download"] = _serve({("r/x", HOUR_PATH): _three_token_hour(write_parquet)})
    records, _msgs = _archive_records(conn, RESOLVE_CONFIG)
    assert [r["data"]["asset_id"] for r in records] == list(RESOLVED)  # tokZ is not ours
    # Resolved by CALLING the events walk over the same window, not by a copy.
    (call,) = _gets()
    assert call[2]["series_slug"] == "poly-wx-nyc"
    assert (call[2]["end_date_min"], call[2]["end_date_max"]) == (
        "2026-03-01T00:00:00Z", "2026-03-01T06:00:00Z")
    # And it carries the declared `closed` filter, exactly as `events` does:
    # under the default an unsettled market resolves no token (knob note).
    assert call[2]["closed"] == "true"


def test_declared_token_ids_win_over_the_gamma_resolution(conn, write_parquet):
    conn.script["download"] = _serve({("r/x", HOUR_PATH): _three_token_hour(write_parquet)})
    records, _msgs = _archive_records(conn, {**RESOLVE_CONFIG, "token_ids": ["tokZ"]})
    assert [r["data"]["asset_id"] for r in records] == ["tokZ"]  # declared, never unioned
    assert _gets() == []  # and Gamma is not walked at all


def test_the_resolution_preconditions_and_an_empty_answer_are_named(conn):
    conn.script["get"] = lambda url, params: []
    with pytest.raises(AssetError, match="no markets resolved for"):
        list(conn.read(RESOLVE_CONFIG, ["archive_hours"], {}, "backfill"))
    with pytest.raises(AssetError, match="token_ids, or series_slugs"):
        list(conn.read({"hours": [HOUR]}, ["archive_hours"], {}, "backfill"))
    with pytest.raises(AssetError, match="start is required"):
        list(conn.read({"series_slugs": ["s"], "hours": [HOUR]}, ["archive_hours"],
                       {}, "backfill"))


def test_an_unreadable_sync_state_logs_and_walks_exactly_as_before(
        conn, write_parquet, tmp_path):
    junk = tmp_path / "junk.json"
    junk.write_text("<html>404</html>", encoding="utf-8")
    conn.script["download"] = _serve({
        ("r/x", HOUR_PATH): _one_token_hour(write_parquet),
        ("r/x", polymarket.SYNC_STATE_PATH): str(junk),
    })
    records, msgs = _archive_records(conn, {**ARCHIVE_CONFIG, "cleanup": False})
    logs = [m["message"] for m in msgs if m["type"] == "LOG"]
    assert len(records) == 1  # the hour is walked as if no sync state existed
    assert len(logs) == 1 and polymarket.SYNC_STATE_PATH in logs[0]


def test_unparseable_meta_stamps_are_named_and_ignored(conn, write_parquet, tmp_path):
    conn.script["download"] = _serve({
        ("r/x", HOUR_PATH): _one_token_hour(write_parquet),
        ("r/x", polymarket.SYNC_STATE_PATH): _write_meta(tmp_path, {
            "pending_gap_hours": ["not an hour"], "latest_available_hour": 17,
            "selection_source": "manifest"}),
    })
    records, msgs = _archive_records(conn, {**ARCHIVE_CONFIG, "cleanup": False})
    logs = [m["message"] for m in msgs if m["type"] == "LOG"]
    assert len(records) == 1  # neither stamp skips or stops anything
    assert len(logs) == 1 and "not an hour" in logs[0] and "17" in logs[0]


def test_the_cursor_advances_past_a_pending_gap_hour(conn, write_parquet, tmp_path):
    hours = [f"2026-03-01T{h:02d}:00:00+00:00" for h in (10, 11)]
    conn.script["download"] = _serve({
        ("r/x", HOUR_PATH): _one_token_hour(write_parquet),
        # Stamped mid-hour: a gap names an HOUR, so it is floored to the
        # boundary the walk steps on — otherwise it would match nothing.
        ("r/x", polymarket.SYNC_STATE_PATH): _write_meta(
            tmp_path, {"pending_gap_hours": ["2026-03-01T11:37:42+00:00"]}),
    })
    records, msgs = _archive_records(
        conn, {**ARCHIVE_CONFIG, "hours": hours, "cleanup": False})
    assert len(records) == 1
    logs = [m["message"] for m in msgs if m["type"] == "LOG"]
    assert len(logs) == 1 and hours[1] in logs[0]  # the LOG names the hour skipped
    assert [c[2] for c in _gets("DOWNLOAD")] == [
        polymarket.SYNC_STATE_PATH, HOUR_PATH]  # the gap hour is never fetched
    assert msgs[-1]["state"] == {"archive_hours": {"cursor": hours[1]}}  # past the gap


def _register_archive_source(registry, config):
    """Register and activate the scripted pack for an archive_hours pull."""
    version = registry.register("source_config", {
        "name": "poly",
        "catalog_source": "poly-source",
        "connector": "tests.onboarding.test_polymarket:ScriptedPolymarket",
        "config": config,
    }, origin="test")
    registry.transition(version, "active", origin="test")


def test_acquisition_skips_a_mirror_gap_and_stops_before_an_unmirrored_hour(
        root, registry, write_parquet, tmp_path):
    hours = [f"2026-03-01T{h:02d}:00:00+00:00" for h in (10, 11, 12, 13)]
    paths = {h: f"hours/2026/03/01/polymarket_orderbook_2026-03-01T{h:02d}.parquet"
             for h in (10, 11, 12, 13)}
    files = {("r/x", paths[h]): _one_token_hour(write_parquet, f"g{h}.parquet", T1_MS + h)
             for h in (10, 12)}  # 11 is a permanent gap, 13 is not mirrored yet
    files[("r/x", polymarket.SYNC_STATE_PATH)] = _write_meta(tmp_path, {
        "archive_root": "hours", "pending_gap_hours": [hours[1]],
        "latest_available_hour": "2026-03-01T12:00:00Z",
        "scan_cursor_hour": hours[2], "selection_source": "manifest",
    })
    ScriptedPolymarket.script = {"get": lambda url, params: [{"id": "ping"}],
                                 "download": _serve(files)}
    _register_archive_source(registry, {"token_ids": ["tokA"], "hours": hours,
                                        "hf_repo": "r/x"})

    out = run_acquisition(root, registry, "poly", "archive_hours", "backfill")
    assert out["records"] == 2  # hours 10 and 12
    assert [c[2] for c in _gets("DOWNLOAD")] == [
        polymarket.SYNC_STATE_PATH, paths[10], paths[12]]
    assert len(out["logs"]) == 2
    assert hours[1] in out["logs"][0] and "pending_gap_hours" in out["logs"][0]
    assert hours[3] in out["logs"][1] and "not mirrored yet" in out["logs"][1]
    landed = {"archive_hours": {"cursor": hours[2]}}  # 13 is retried next pull
    assert load_state(root, "poly", "archive_hours", "backfill") == landed


def test_seq_is_the_files_own_order_never_the_kept_rows_order(conn, write_parquet):
    # seq keys stored evidence, so it must not shift when the pack's event
    # vocabulary changes: numbered after the filter, widening
    # ARCHIVE_EVENT_TYPES would renumber — rewrite — keys already written.
    path = write_parquet("levels.parquet", {
        "asset_id": ["tokA"] * 3, "timestamp": [T1_MS] * 3,
        "event_type": ["price_change", "tick_size_change", "price_change"],
        "bids": [None] * 3, "asks": [None] * 3,
        "price": ["0.42", None, "0.43"], "size": ["10", None, "4"],
        "side": ["BUY", None, "BUY"],
    })
    conn.script["download"] = _serve({("r/x", HOUR_PATH): path})
    records, _msgs = _archive_records(conn, {**ARCHIVE_CONFIG, "cleanup": False})
    assert [(r["data"]["seq"], r["data"]["price"]) for r in records] == [
        (0, "0.42"), (2, "0.43")]  # 1 is the dropped row's place in the file


def test_seq_orders_the_rows_one_millisecond_carries(root, registry, tmp_path):
    # pmxt writes one price_change per LEVEL, several inside one millisecond and
    # the same level up to three times, so (asset_id, ts, event_type) is NOT
    # unique and the archive's own row order is the sequence.
    pa = pytest.importorskip("pyarrow")
    pq = pytest.importorskip("pyarrow.parquet")
    path = str(tmp_path / "hour.parquet")
    pq.write_table(pa.table({
        "asset_id": ["tokA", "tokA"], "timestamp": [T1_MS, T1_MS],
        "event_type": ["price_change", "price_change"],
        "bids": [None, None], "asks": [None, None],
        "price": ["0.42", "0.42"], "size": ["10", "4"], "side": ["BUY", "BUY"],
    }), path, row_group_size=1)  # two row groups: seq must span batches
    ScriptedPolymarket.script = {"get": lambda url, params: [{"id": "ping"}],
                                 "download": _serve({("r/x", HOUR_PATH): path})}
    _register_archive_source(registry, {"token_ids": ["tokA"], "hours": [HOUR],
                                        "hf_repo": "r/x", "cleanup": False})

    out = run_acquisition(root, registry, "poly", "archive_hours", "backfill")
    assert out["records"] == 2
    # A deliberate restatement of the documented key, never read from the pack.
    rows = scan_stream(root.root, "poly", "archive_hours", ["asset_id", "ts", "seq"])
    assert [(r["seq"], r["size"]) for r in rows] == [(0, "10"), (1, "4")]  # file order
    assert list(polymarket.STREAM_KEYS["archive_hours"]) == ["asset_id", "ts", "seq"]
