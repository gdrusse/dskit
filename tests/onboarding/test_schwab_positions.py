"""Schwab account positions: a point-in-time snapshot stream (ADR-0279)."""

from datetime import datetime, timezone

import pytest

from dskit.assets.base import AssetError
from dskit.onboarding import check_config, check_message
from dskit.onboarding.connector import resolve_connector
from dskit.onboarding.libs.schwab import SchwabPositionsConnector

CONFIG = {}

ACCOUNTS = [
    {"securitiesAccount": {
        "accountNumber": "12345678",
        "type": "MARGIN",
        "positions": [
            {
                "instrument": {"symbol": "VTI", "assetType": "EQUITY"},
                "longQuantity": 10.0, "shortQuantity": 0.0,
                "averagePrice": 200.0, "marketValue": 2500.0,
            },
            {
                "instrument": {"symbol": "AAPL", "assetType": "EQUITY"},
                "longQuantity": 3.0, "shortQuantity": 0.0,
                "averagePrice": 150.0, "marketValue": 600.0,
            },
        ],
    }},
]

NOW = datetime(2026, 10, 27, 14, 30, tzinfo=timezone.utc)


class Stub(SchwabPositionsConnector):
    """Scripted transport and clock."""

    body = ACCOUNTS
    calls = []

    def _now(self):
        return NOW

    def _access_token(self, knobs):
        return "token"

    def _request(self, token, label, url, params, timeout):
        type(self).calls.append((url, dict(params)))
        return type(self).body


@pytest.fixture(autouse=True)
def reset():
    Stub.body = ACCOUNTS
    Stub.calls = []
    yield


def _read(state=None, mode="backfill"):
    messages = list(Stub().read(CONFIG, ["positions"], state or {}, mode))
    assert all(check_message(message) for message in messages)
    return [m for m in messages if m["type"] == "RECORD"], messages


def test_registered_and_default_deny():
    assert resolve_connector("schwab_positions") is SchwabPositionsConnector
    check_config(SchwabPositionsConnector(), CONFIG)
    with pytest.raises(AssetError, match="unknown key"):
        check_config(SchwabPositionsConnector(), {"symbols": ["AAPL"]})


def test_discover_declares_positions_stream():
    (stream,) = Stub().discover(CONFIG)
    assert stream["stream"] == "positions"
    assert stream["primary_key"] == ["account", "symbol", "as_of"]
    assert "account" in stream["schema"]["fields"]
    assert "cost_basis" in stream["schema"]["fields"]


def test_read_emits_one_row_per_position_without_account_numbers():
    records, messages = _read()
    assert [r["data"]["symbol"] for r in records] == ["AAPL", "VTI"]
    vti = records[1]["data"]
    assert vti["as_of"] == "2026-10-27"
    assert records[1]["effective_date"] == "2026-10-27"
    assert vti["long_quantity"] == 10.0
    assert vti["cost_basis"] == 2000.0
    assert vti["market_value"] == 2500.0
    assert vti["asset_type"] == "EQUITY"
    assert "12345678" not in str(messages)
    assert len(vti["account"]) == 16
    assert messages[-1]["state"]["positions"]["cursor"] == "2026-10-27"
    assert Stub.calls[0][1] == {"fields": "positions"}


def test_same_day_repull_is_skipped_in_backfill_but_not_live():
    state = {"positions": {"cursor": "2026-10-27"}}
    records, _ = _read(state, "backfill")
    assert records == []
    records, _ = _read(state, "live")
    assert len(records) == 2


def test_malformed_body_is_an_asset_error():
    Stub.body = {"not": "a list"}
    with pytest.raises(AssetError, match="accounts list"):
        _read()
    Stub.body = [{"securitiesAccount": {"accountNumber": "1", "positions": [
        {"instrument": {"symbol": "X"}, "longQuantity": "ten"}]}}]
    with pytest.raises(AssetError, match="malformed"):
        _read()
