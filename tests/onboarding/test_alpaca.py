"""Alpaca bars pack through its connector contract, without a network."""

from datetime import datetime, timedelta, timezone

import pytest

from dskit.assets.base import AssetError
from dskit.onboarding import check_config, check_message, run_acquisition
from dskit.onboarding.libs import alpaca
from dskit.onboarding.libs.alpaca import AlpacaBarsConnector

from .stub_connectors import StubAlpacaBarsConnector

CONFIG = {
    "symbols": ["AAPL", "JPM"],
    "start": "2026-01-02T14:30:00+00:00",
    "feed": "iex",
    "adjustment": "raw",
}

ROWS = [
    ("AAPL", {
        "symbol": "AAPL", "ts": "2026-01-02T14:30:00+00:00",
        "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.5,
        "volume": 1000.0, "trade_count": 10, "vwap": 100.25,
    }),
    ("JPM", {
        "symbol": "JPM", "ts": "2026-01-02T14:30:00+00:00",
        "open": 50.0, "high": 51.0, "low": 49.0, "close": 50.5,
        "volume": 800.0, "trade_count": 8, "vwap": 50.25,
    }),
    ("AAPL", {
        "symbol": "AAPL", "ts": "2026-01-02T14:31:00+00:00",
        "open": 100.5, "high": 102.0, "low": 100.0, "close": 101.5,
        "volume": 1200.0, "trade_count": 12, "vwap": 101.0,
    }),
]


@pytest.fixture(autouse=True)
def scripted_rows():
    """Reset import-path state around every test."""
    StubAlpacaBarsConnector.rows = list(ROWS)
    yield
    StubAlpacaBarsConnector.rows = []


def _records(connector, config=CONFIG, state=None, mode="backfill"):
    messages = list(connector.read(config, ["bars"], state or {}, mode))
    assert all(check_message(message) for message in messages)
    return [message for message in messages if message["type"] == "RECORD"], messages


def test_spec_is_default_deny_and_defaults_are_generic():
    connector = AlpacaBarsConnector()
    check_config(connector, CONFIG)
    with pytest.raises(AssetError, match="unknown key"):
        check_config(connector, {**CONFIG, "surprise": True})
    with pytest.raises(AssetError, match="required knob"):
        check_config(connector, {"start": CONFIG["start"]})

    knobs = connector.resolve_knobs({
        "symbols": ["AAPL"], "start": CONFIG["start"],
    })
    assert knobs["feed"] == "sip"
    assert knobs["adjustment"] == "raw"
    assert knobs["timeframe"] == (1, "Minute")


def test_generic_pack_accepts_vendor_timeframe_units():
    connector = AlpacaBarsConnector()
    for unit in alpaca.TIMEFRAME_UNITS:
        knobs = connector.resolve_knobs({
            **CONFIG, "timeframe": [2, unit],
        })
        assert knobs["timeframe"] == (2, unit)
    with pytest.raises(AssetError, match="timeframe"):
        connector.resolve_knobs({**CONFIG, "timeframe": [0, "Minute"]})


def test_chunk_default_has_one_name(monkeypatch):
    connector = AlpacaBarsConnector()
    assert connector.resolve_knobs(CONFIG)["chunk_days"] == \
        alpaca.DEFAULT_CHUNK_DAYS
    monkeypatch.setattr(alpaca, "DEFAULT_CHUNK_DAYS", 7)
    assert connector.resolve_knobs(CONFIG)["chunk_days"] == 7
    assert "default 7." in connector.spec()["params"]["chunk_days"]["notes"]


def test_discover_and_read_share_the_provider_neutral_schema():
    connector = StubAlpacaBarsConnector()
    (stream,) = connector.discover(CONFIG)
    assert stream["stream"] == "bars"
    assert stream["primary_key"] == ["symbol", "ts"]
    assert stream["schema"]["fields"] == [
        "symbol", "ts", "open", "high", "low", "close", "volume",
        "trade_count", "vwap",
    ]

    records, messages = _records(connector)
    assert [message["type"] for message in messages] == [
        "SCHEMA", "RECORD", "RECORD", "RECORD", "STATE",
    ]
    assert {record["data"]["symbol"] for record in records} == {"AAPL", "JPM"}
    assert messages[-1]["state"]["bars"]["cursor"] == \
        "2026-01-02T14:31:00+00:00"


def test_cursor_filters_already_durable_bars():
    records, messages = _records(
        StubAlpacaBarsConnector(),
        state={"bars": {"cursor": "2026-01-02T14:30:00+00:00"}},
    )
    assert [record["data"]["symbol"] for record in records] == ["AAPL"]
    assert messages[-1]["state"]["bars"]["cursor"] == \
        "2026-01-02T14:31:00+00:00"


def test_live_window_is_bounded_and_sip_end_is_clamped():
    connector = StubAlpacaBarsConnector()
    live = connector.resolve_knobs({
        **CONFIG, "live_lookback_minutes": 30,
    })
    start, end = connector._window(live, "", "live")
    assert timedelta(minutes=29) <= end - start <= timedelta(minutes=31)

    sip = connector.resolve_knobs({
        **CONFIG, "feed": "sip", "live_lookback_minutes": 30,
    })
    _start, end = connector._window(sip, "", "live")
    assert end <= datetime.now(timezone.utc) - timedelta(minutes=15)
    with pytest.raises(AssetError, match="live_lookback_minutes"):
        connector.resolve_knobs({
            **CONFIG, "feed": "sip", "live_lookback_minutes": 16,
        })


def test_fetch_chunks_large_windows_before_the_sdk_buffers_them(monkeypatch):
    historical = pytest.importorskip("alpaca.data.historical")
    seen = []

    class _Bars:
        data = {}

    class _Client:
        def __init__(self, key, secret):
            pass

        def get_stock_bars(self, request):
            seen.append((request.start, request.end))
            return _Bars()

    monkeypatch.setattr(historical, "StockHistoricalDataClient", _Client)
    connector = AlpacaBarsConnector()
    monkeypatch.setattr(
        connector, "_credentials", lambda knobs: ("key", "secret")
    )
    config = {**CONFIG, "chunk_days": 2}
    check_config(connector, config)
    knobs = connector.resolve_knobs(config)
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    end = start + timedelta(days=5)

    list(connector._fetch(knobs, start, end))

    assert seen == [
        (start, start + timedelta(days=2)),
        (start + timedelta(days=2), start + timedelta(days=4)),
        (start + timedelta(days=4), end),
    ]


def test_acquisition_commits_one_alpaca_snapshot(root, registry):
    version = registry.register("source_config", {
        "name": "alpaca",
        "catalog_source": "alpaca-source",
        "connector": "tests.onboarding.stub_connectors:StubAlpacaBarsConnector",
        "config": CONFIG,
    }, origin="test")
    registry.transition(version, "active", origin="test")

    summary = run_acquisition(root, registry, "alpaca", "bars", "backfill")
    assert summary["records"] == 3
    assert summary["snapshot"]
    caught_up = run_acquisition(root, registry, "alpaca", "bars", "backfill")
    assert caught_up["records"] == 0
    assert caught_up["snapshot"] is None
