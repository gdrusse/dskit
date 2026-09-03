"""Alpaca minute-quote pack through its connector contract, no network."""

import pytest

from dskit.assets.base import AssetError
from dskit.onboarding import check_config, check_message
from dskit.onboarding.libs.alpaca_quotes import (
    QUOTE_FIELDS,
    QUOTE_STREAM,
    AlpacaQuoteMinutesConnector,
    minute_rows,
)

CONFIG = {
    "symbols": ["LLY", "XOM"],
    "start": "2026-02-25",
    "end": "2026-02-27T00:00:00+00:00",
    "feed": "sip",
}


def _quote(second, bid, ask, minute=30, bs=100, a_s=200):
    return {
        "t": f"2026-02-25T14:{minute:02d}:{second:02d}.500000000Z",
        "bp": bid, "ap": ask, "bs": bs, "as": a_s, "bx": "Z", "ax": "N",
    }


def test_the_row_is_the_last_two_sided_quote_of_its_own_minute():
    rows = list(minute_rows("LLY", [
        _quote(1, 10.0, 10.2),
        _quote(30, 10.1, 10.3),
        _quote(59, 10.2, 10.4),
        _quote(5, 20.0, 20.4, minute=31),
    ]))
    assert [row["ts"] for row in rows] == [
        "2026-02-25T14:30:00Z", "2026-02-25T14:31:00Z"
    ]
    assert rows[0]["bid"] == 10.2 and rows[0]["ask"] == 10.4
    assert rows[0]["mid"] == pytest.approx(10.3)
    assert rows[0]["spread"] == pytest.approx(0.2)
    assert rows[0]["spread_bps"] == pytest.approx(10000.0 * 0.2 / 10.3)
    assert rows[0]["quote_age_ms"] == 500
    assert rows[0]["n_quotes"] == 3
    assert set(rows[0]) == set(QUOTE_FIELDS)


def test_crossed_locked_and_one_sided_quotes_are_counted_never_selected():
    rows = list(minute_rows("LLY", [
        _quote(10, 10.0, 10.2),
        _quote(20, 10.5, 10.4),          # crossed
        _quote(30, 10.6, 10.6),          # locked
        _quote(40, 0.0, 10.9),           # one-sided
        _quote(50, None, None),          # empty book
    ]))
    assert len(rows) == 1
    assert rows[0]["bid"] == 10.0 and rows[0]["ask"] == 10.2
    assert rows[0]["n_quotes"] == 5
    assert rows[0]["n_crossed"] == 1
    assert rows[0]["n_locked"] == 1


def test_a_minute_with_nothing_usable_yields_no_row():
    assert list(minute_rows("LLY", [_quote(10, 10.5, 10.4)])) == []
    assert list(minute_rows("LLY", [])) == []


def test_a_boundary_quote_older_than_its_own_minute_is_refused():
    quotes = [_quote(1, 10.0, 10.2)]
    assert len(list(minute_rows("LLY", quotes, max_age_ms=60000))) == 1
    assert list(minute_rows("LLY", quotes, max_age_ms=10000)) == []


def test_spec_is_default_deny_and_end_is_required():
    connector = AlpacaQuoteMinutesConnector()
    check_config(connector, CONFIG)
    with pytest.raises(AssetError, match="unknown key"):
        check_config(connector, {**CONFIG, "surprise": True})
    with pytest.raises(AssetError, match="required knob"):
        check_config(connector, {"symbols": ["LLY"], "start": "2026-02-25"})


def test_knobs_reject_repeats_bad_windows_and_oversized_pages():
    connector = AlpacaQuoteMinutesConnector()
    with pytest.raises(AssetError, match="must not repeat"):
        connector.resolve_knobs({**CONFIG, "symbols": ["LLY", "LLY"]})
    with pytest.raises(AssetError, match="must be after"):
        connector.resolve_knobs({**CONFIG, "end": "2026-02-25"})
    with pytest.raises(AssetError, match="rth_end_minutes"):
        connector.resolve_knobs({**CONFIG, "rth_end_minutes": 570})
    with pytest.raises(AssetError, match="page_limit"):
        connector.resolve_knobs({**CONFIG, "page_limit": 50000})
    with pytest.raises(AssetError, match="not a known zone"):
        connector.resolve_knobs({**CONFIG, "session_tz": "Mars/Olympus"})


def test_sessions_are_regular_hours_weekdays_and_stop_before_end():
    connector = AlpacaQuoteMinutesConnector()
    knobs = connector.resolve_knobs({
        **CONFIG, "start": "2026-02-25", "end": "2026-03-03T00:00:00+00:00",
    })
    spans = list(connector._sessions(knobs))
    starts = [span[0].isoformat() for span in spans]
    # Wed 25th, Thu 26th, Fri 27th, Mon Mar 2nd — the weekend is absent,
    # and 14:30Z is 09:30 in New York on a winter date.
    assert starts == [
        "2026-02-25T14:30:00+00:00",
        "2026-02-26T14:30:00+00:00",
        "2026-02-27T14:30:00+00:00",
        "2026-03-02T14:30:00+00:00",
    ]
    assert spans[0][1].isoformat() == "2026-02-25T21:00:00+00:00"


def test_a_midnight_utc_end_does_not_pull_the_previous_evenings_session():
    connector = AlpacaQuoteMinutesConnector()
    knobs = connector.resolve_knobs({
        **CONFIG, "start": "2026-02-25", "end": "2026-02-26T00:00:00+00:00",
    })
    spans = list(connector._sessions(knobs))
    assert [span[0].date().isoformat() for span in spans] == ["2026-02-25"]


def test_summer_sessions_follow_the_declared_zone_not_a_fixed_offset():
    connector = AlpacaQuoteMinutesConnector()
    knobs = connector.resolve_knobs({
        **CONFIG, "start": "2026-06-15", "end": "2026-06-16T00:00:00+00:00",
    })
    spans = list(connector._sessions(knobs))
    assert spans[0][0].isoformat() == "2026-06-15T13:30:00+00:00"


def test_discover_declares_the_reduced_stream_offline():
    streams = AlpacaQuoteMinutesConnector().discover(CONFIG)
    assert streams == [{
        "stream": QUOTE_STREAM,
        "schema": {"fields": list(QUOTE_FIELDS)},
        "primary_key": ["symbol", "ts"],
    }]


class _ScriptedConnector(AlpacaQuoteMinutesConnector):
    """The pack with its transport replaced by a scripted page list."""

    pages = {}
    asked = []

    def _credentials(self, knobs):
        return "key", "secret"

    def _pages(self, symbol, start, end, knobs, headers, pacer):
        self.asked.append((symbol, start.isoformat()))
        yield self.pages.get((symbol, start.date().isoformat()), [])


def _read(connector, config, state=None):
    messages = list(
        connector.read(config, [QUOTE_STREAM], state or {}, "backfill")
    )
    assert all(check_message(message) for message in messages)
    return messages


def test_read_emits_schema_rows_and_one_cursor_per_symbol():
    connector = _ScriptedConnector()
    _ScriptedConnector.asked = []
    _ScriptedConnector.pages = {
        ("LLY", "2026-02-25"): [_quote(10, 10.0, 10.2)],
        ("XOM", "2026-02-26"): [_quote(10, 20.0, 20.4)],
    }
    messages = _read(connector, CONFIG)
    records = [m for m in messages if m["type"] == "RECORD"]
    assert [m["data"]["symbol"] for m in records] == ["LLY", "XOM"]
    assert all(m["kind"] == "observation" for m in records)
    assert messages[-1]["type"] == "STATE"
    cursors = messages[-1]["state"][QUOTE_STREAM]["cursors"]
    assert cursors == {
        "LLY": "2026-02-26T21:00:00Z", "XOM": "2026-02-26T21:00:00Z",
    }
    # Symbol-major: every LLY session is asked for before the first XOM one.
    assert [pair[0] for pair in _ScriptedConnector.asked] == [
        "LLY", "LLY", "XOM", "XOM",
    ]


def test_a_per_symbol_cursor_resumes_one_name_and_backfills_a_new_one():
    connector = _ScriptedConnector()
    _ScriptedConnector.asked = []
    _ScriptedConnector.pages = {}
    prior = {QUOTE_STREAM: {"cursors": {"LLY": "2026-02-25T21:00:00Z"}}}
    _read(connector, CONFIG, prior)
    assert _ScriptedConnector.asked == [
        ("LLY", "2026-02-26T14:30:00+00:00"),
        ("XOM", "2026-02-25T14:30:00+00:00"),
        ("XOM", "2026-02-26T14:30:00+00:00"),
    ]


def test_the_stream_is_backfill_only_and_named():
    connector = _ScriptedConnector()
    with pytest.raises(AssetError, match="backfill-only"):
        list(connector.read(CONFIG, [QUOTE_STREAM], {}, "live"))
    with pytest.raises(AssetError, match="unknown stream"):
        list(connector.read(CONFIG, ["bars"], {}, "backfill"))
