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


def test_declared_end_clamps_the_window_and_is_exclusive():
    """A hard data cut is declared, not trimmed after the fact."""
    connector = StubAlpacaBarsConnector()
    assert connector.resolve_knobs(CONFIG)["end"] == ""

    cut = "2026-01-02T14:31:00+00:00"
    knobs = connector.resolve_knobs({**CONFIG, "end": cut})
    _start, end = connector._window(knobs, "", "backfill")
    assert end == datetime(2026, 1, 2, 14, 31, tzinfo=timezone.utc)

    records, _messages = _records(connector, {**CONFIG, "end": cut})
    assert [record["data"]["ts"] for record in records] == [
        "2026-01-02T14:30:00+00:00", "2026-01-02T14:30:00+00:00",
    ]

    with pytest.raises(AssetError, match="must be after config.start"):
        connector.resolve_knobs({**CONFIG, "end": CONFIG["start"]})
    with pytest.raises(AssetError, match="config.end"):
        connector.resolve_knobs({**CONFIG, "end": 20260102})


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

    naive = start.replace(tzinfo=None)
    assert seen == [
        (naive, naive + timedelta(days=2)),
        (naive + timedelta(days=2), naive + timedelta(days=4)),
        (naive + timedelta(days=4), end.replace(tzinfo=None)),
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



def test_option_archive_connector_exists():
    assert hasattr(alpaca, "AlpacaOptionArchiveConnector"), (
        "saved option archives cannot yet use standard acquisition")


def _archive_config(tmp_path, payloads):
    import gzip
    import hashlib
    import json
    files = {}
    for name, payload in payloads.items():
        raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        path = tmp_path/(name+".json.gz")
        path.write_bytes(gzip.compress(raw))
        files[name] = {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    return {"files": files, "archive_observed_at": "2026-10-01T12:00:00Z",
            "session_timezone": "America/New_York"}


def _contract(multiplier="100"):
    return {"symbol": "ABC240503C00100000", "underlying_symbol": "ABC",
            "root_symbol": "ABC", "expiration_date": "2024-05-03",
            "type": "call", "strike_price": "100", "multiplier": multiplier,
            "size": "100", "style": "american"}


def test_option_archive_preserves_terms_and_trade_provenance(tmp_path):
    import json
    contract = _contract("0")
    bar = {"t": "2024-03-22T04:00:00Z", "o": 2, "h": 3, "l": 1, "c": 2.5, "v": 10}
    page = {"expiry": "2024-05-03", "bars": {contract["symbol"]: [bar, bar]}}
    config = _archive_config(tmp_path, {"contracts": {"contracts": [contract]},
                                       "bars": json.dumps(page).encode()})
    rows, messages = _records(alpaca.AlpacaOptionArchiveConnector(), config)
    assert len(rows) == 1
    row = rows[0]["data"]
    assert row["mark"] == 2.5 and row["multiplier"] == 0
    assert row["quote_date"] == "2024-03-22" and row["quote_timestamp"] is None
    assert row["implied_volatility"] is None and row["bid"] is None
    assert row["timestamp_basis"] == "session_label"
    assert row["historical_available_at"] is None
    resumed, _ = _records(alpaca.AlpacaOptionArchiveConnector(), config, messages[-1]["state"])
    assert resumed == []


def test_option_archive_refuses_changed_bytes_and_conflicting_duplicate(tmp_path):
    import json
    contract = _contract()
    bars = [{"t": "2024-03-22T04:00:00Z", "o": 2, "h": 3, "l": 1, "c": c, "v": 10}
            for c in (2, 2.5)]
    page = {"bars": {contract["symbol"]: bars}}
    config = _archive_config(tmp_path, {"contracts": {"contracts": [contract]},
                                       "bars": json.dumps(page).encode()})
    with pytest.raises(AssetError, match="conflicting duplicate"):
        _records(alpaca.AlpacaOptionArchiveConnector(), config)
    config["files"]["bars"]["sha256"] = "0"*64
    with pytest.raises(AssetError, match="sha256 mismatch"):
        _records(alpaca.AlpacaOptionArchiveConnector(), config)


def test_option_archive_retains_orphan_snapshot_and_does_not_backfill_terms(tmp_path):
    config = _archive_config(tmp_path, {"snapshots": {"pages": [{"snapshots": {
        "ABC261030C00100000": {"latestQuote": {"t": "2026-09-30T19:59:00Z",
                                             "bp": 2, "ap": 3}}}}]}})
    rows = [m["data"] for m in alpaca.AlpacaOptionArchiveConnector().read(
        config, ["snapshots"], {}, "backfill") if m["type"] == "RECORD"]
    assert len(rows) == 1 and rows[0]["mark"] == 2.5
    assert rows[0]["contract_terms_status"] == "unverified_contract_terms"
    assert rows[0]["multiplier"] is None and rows[0]["style"] is None


def test_option_archive_refuses_metadata_identity_contradiction(tmp_path):
    item = dict(_contract(), strike_price="101")
    config = _archive_config(tmp_path, {"contracts": {"contracts": [item]}})
    with pytest.raises(AssetError, match="disagrees"):
        list(alpaca.AlpacaOptionArchiveConnector().read(config, ["contracts"], {}, "backfill"))


@pytest.mark.parametrize("stream", ["contracts", "snapshots"])
def test_option_archive_refuses_provider_error_payload(tmp_path, stream):
    config = _archive_config(tmp_path, {stream: {"message": "upstream error"}})
    with pytest.raises(AssetError, match="container"):
        list(alpaca.AlpacaOptionArchiveConnector().read(config, [stream], {}, "backfill"))


def test_option_archive_refuses_multiple_snapshots_per_contract_session(tmp_path):
    pages = [{"snapshots": {"ABC240503C00100000": {"latestQuote": {
        "t": t, "bp": 2, "ap": 3}}}} for t in
        ("2024-03-22T19:58:00Z", "2024-03-22T19:59:00Z")]
    config = _archive_config(tmp_path, {"snapshots": {"pages": pages}})
    with pytest.raises(AssetError, match="conflicting duplicate"):
        list(alpaca.AlpacaOptionArchiveConnector().read(config, ["snapshots"], {}, "backfill"))
