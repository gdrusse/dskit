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
    assert by_name["archive_hours"]["primary_key"] == ["asset_id", "ts", "event_type"]
    assert by_name["events"]["schema"] == {"fields": list(EVENT_FIELDS)}
    assert by_name["fee_schedules"]["schema"] == {"fields": list(FEE_SCHEDULE_FIELDS)}
    assert by_name["books"]["schema"] == {"fields": list(BOOK_FIELDS)}
    assert by_name["archive_hours"]["schema"] == {"fields": list(ARCHIVE_FIELDS)}
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
    assert ScriptedPolymarket.calls == [("DOWNLOAD", "r/x", HOUR_PATH, None)]
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
    assert [c[2] for c in _gets("DOWNLOAD")] == [paths[10], paths[11]]  # 9 skipped, stop at 11
    logs = [m["message"] for m in msgs if m["type"] == "LOG"]
    assert len(logs) == 1 and paths[11] in logs[0]
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
    assert [c[2] for c in _gets("DOWNLOAD")] == [
        f"hours/2026/03/02/polymarket_orderbook_2026-03-02T{h:02d}.parquet" for h in (9, 10, 11)
    ]
    assert msgs[-1]["state"] == {"archive_hours": {"cursor": "2026-03-02T11:00:00+00:00"}}

    ScriptedPolymarket.calls = []
    explicit = {**config, "hours": ["2026-03-02T10:00:00+00:00"], "end": "2026-03-02T12:00:00Z"}
    _archive_records(conn, explicit)
    assert len(_gets("DOWNLOAD")) == 1  # an explicit list takes precedence


def test_archive_token_env_is_optional_and_named(conn, write_parquet, monkeypatch):
    empty = write_parquet("empty.parquet", {
        "asset_id": ["tokB"], "timestamp": [T1_MS], "event_type": ["book"],
        "bids": ["[]"], "asks": ["[]"], "price": [None], "size": [None], "side": [None],
    })
    conn.script["download"] = lambda repo, path, token: empty
    monkeypatch.delenv("HF_TOKEN", raising=False)
    _archive_records(conn, {**ARCHIVE_CONFIG, "cleanup": False})
    assert _gets("DOWNLOAD")[0][3] is None  # anonymous when the variable is unset

    monkeypatch.setenv("MY_HF", "hf_secret")
    _archive_records(conn, {**ARCHIVE_CONFIG, "cleanup": False, "token_env": "MY_HF"})
    assert _gets("DOWNLOAD")[1][3] == "hf_secret"


def test_archive_unrecognized_schema_refused(conn, write_parquet):
    path = write_parquet("odd.parquet", {"foo": [1]})
    conn.script["download"] = _serve({("r/x", HOUR_PATH): path})
    with pytest.raises(AssetError, match="schema.*foo"):
        list(conn.read(ARCHIVE_CONFIG, ["archive_hours"], {}, "backfill"))


def test_archive_without_the_hub_client_refuses_by_name(monkeypatch):
    monkeypatch.setitem(sys.modules, "huggingface_hub", None)  # import -> ImportError
    with pytest.raises(AssetError, match="huggingface_hub"):
        list(PolymarketConnector().read(ARCHIVE_CONFIG, ["archive_hours"], {}, "backfill"))


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
