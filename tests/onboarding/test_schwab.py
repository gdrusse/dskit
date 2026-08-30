"""Schwab price-history bars: OAuth, overlap, and closed-minute evidence."""

from datetime import datetime, timezone

import pytest

from dskit.assets.base import AssetError
from dskit.onboarding import acquire as acquire_module
from dskit.onboarding import check_config, check_message, run_acquisition
from dskit.onboarding.libs import schwab
from dskit.onboarding.libs.schwab import SchwabBarsConnector

from .stub_connectors import StubSchwabBarsConnector

CONFIG = {
    "symbols": ["AAPL"],
    "start": "2026-01-02T14:29:00+00:00",
    "timeframe": [1, "Minute"],
    "live_lookback_minutes": 30,
    "overlap_minutes": 2,
}

CANDLES = {
    "candles": [
        {
            "datetime": 1767364200000,
            "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.5,
            "volume": 1000,
        },
        {
            "datetime": 1767364260000,
            "open": 100.5, "high": 102.0, "low": 100.0, "close": 101.5,
            "volume": 1200,
        },
    ],
}


@pytest.fixture(autouse=True)
def scripted_schwab():
    """Reset import-path state around every test."""
    StubSchwabBarsConnector.responses = {"AAPL": CANDLES}
    StubSchwabBarsConnector.calls = []
    yield
    StubSchwabBarsConnector.responses = {}
    StubSchwabBarsConnector.calls = []


def _read(connector, config=CONFIG, state=None, mode="live"):
    messages = list(connector.read(config, ["bars"], state or {}, mode))
    assert all(check_message(message) for message in messages)
    return [message for message in messages if message["type"] == "RECORD"], messages


def test_spec_is_default_deny_and_one_minute_only():
    connector = SchwabBarsConnector()
    check_config(connector, CONFIG)
    with pytest.raises(AssetError, match="unknown key"):
        check_config(connector, {**CONFIG, "surprise": True})
    with pytest.raises(AssetError, match="required knob"):
        check_config(connector, {"start": CONFIG["start"]})
    with pytest.raises(AssetError, match="timeframe"):
        connector.resolve_knobs({**CONFIG, "timeframe": [5, "Minute"]})


def test_discover_matches_alpaca_provider_neutral_schema():
    (stream,) = StubSchwabBarsConnector().discover(CONFIG)
    assert stream == {
        "stream": "bars",
        "schema": {"fields": [
            "symbol", "ts", "open", "high", "low", "close", "volume",
            "trade_count", "vwap",
        ]},
        "primary_key": ["symbol", "ts"],
        "timeframe": [1, "Minute"],
    }


def test_read_normalizes_candles_and_nulls_unavailable_fields():
    connector = StubSchwabBarsConnector()
    connector._now = lambda: datetime(2026, 1, 2, 14, 33, tzinfo=timezone.utc)
    records, messages = _read(connector, mode="backfill")
    assert len(records) == 2
    assert records[0]["data"] == {
        "symbol": "AAPL",
        "ts": "2026-01-02T14:30:00+00:00",
        "open": 100.0,
        "high": 101.0,
        "low": 99.0,
        "close": 100.5,
        "volume": 1000.0,
        "trade_count": None,
        "vwap": None,
    }
    assert messages[-1]["state"]["bars"]["cursor"] == \
        "2026-01-02T14:31:00+00:00"


def test_live_read_re_requests_overlap_but_excludes_open_minute():
    connector = StubSchwabBarsConnector()
    connector._now = lambda: datetime(
        2026, 1, 2, 14, 32, 30, tzinfo=timezone.utc
    )
    StubSchwabBarsConnector.responses = {"AAPL": {
        "candles": CANDLES["candles"] + [{
            "datetime": 1767364320000,
            "open": 101.5, "high": 103.0, "low": 101.0, "close": 102.5,
            "volume": 900,
        }],
    }}
    state = {"bars": {"cursor": "2026-01-02T14:31:00+00:00"}}

    records, messages = _read(connector, state=state)

    assert [record["effective_date"] for record in records] == [
        "2026-01-02T14:30:00+00:00",
        "2026-01-02T14:31:00+00:00",
    ]
    _symbol, params, _timeout = StubSchwabBarsConnector.calls[0]
    assert params["startDate"] == 1767364140000
    assert params["endDate"] == 1767364320000
    assert params["frequencyType"] == "minute"
    assert params["frequency"] == 1
    assert messages[-1]["state"] == state


def test_read_resolves_oauth_access_token(monkeypatch):
    seen = []
    monkeypatch.setattr(
        schwab.OAuth2TokenService,
        "ensure_access_token",
        lambda self: seen.append(self) or "access-token",
    )
    connector = SchwabBarsConnector()
    connector._now = lambda: datetime(2026, 1, 2, 14, 33, tzinfo=timezone.utc)
    connector._fetch = lambda token, symbol, params, timeout: (
        seen.append((token, symbol)) or {"candles": []}
    )

    list(connector.read(CONFIG, ["bars"], {}, "live"))

    assert len([item for item in seen
                if isinstance(item, schwab.OAuth2TokenService)]) == 1
    assert ("access-token", "AAPL") in seen


def test_oauth_defaults_name_environment_not_material():
    knobs = SchwabBarsConnector().resolve_knobs(CONFIG)
    assert knobs["client_id_env"] == "SCHWAB_APP_KEY"
    assert knobs["client_secret_env"] == "SCHWAB_APP_SECRET"
    assert knobs["callback_url_env"] == "SCHWAB_CALLBACK_URL"
    assert knobs["token_path_env"] == "SCHWAB_TOKEN_PATH"


def test_acquisition_commits_repeated_overlap_as_new_evidence(
        root, registry, monkeypatch):
    version = registry.register("source_config", {
        "name": "schwab",
        "catalog_source": "schwab-source",
        "connector": "tests.onboarding.stub_connectors:StubSchwabBarsConnector",
        "config": CONFIG,
    }, origin="test")
    registry.transition(version, "active", origin="test")
    stamps = iter([
        "2026-01-02T15:00:00+00:00",
        "2026-01-02T15:01:00+00:00",
    ])
    monkeypatch.setattr(acquire_module, "utc_now", lambda: next(stamps))

    first = run_acquisition(root, registry, "schwab", "bars", "live")
    second = run_acquisition(root, registry, "schwab", "bars", "live")

    assert first["records"] == second["records"] == 2
    assert first["snapshot"] != second["snapshot"]
