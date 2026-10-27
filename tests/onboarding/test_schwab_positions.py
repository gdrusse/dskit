"""Schwab account positions: a point-in-time snapshot stream (ADR-0279)."""

from datetime import datetime, timezone

import pytest

from dskit.assets.base import AssetError
from dskit.onboarding import check_config, check_message
from dskit.onboarding.connector import resolve_connector
from dskit.onboarding.libs.schwab import SchwabPositionsConnector

CONFIG = {}

HASHES = [{"accountNumber": "12345678", "hashValue": "F00DBEEFCAFE4C0DE"}]

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
    """Scripted transport and clock: one body per endpoint."""

    bodies = {}
    calls = []

    def _now(self):
        return NOW

    def _access_token(self, knobs):
        return "token"

    def _request(self, token, label, url, params, timeout):
        type(self).calls.append((url, dict(params)))
        body = type(self).bodies[url]
        if isinstance(body, Exception):
            raise body
        return body


ACCOUNTS_URL = "https://api.schwabapi.com/trader/v1/accounts"
NUMBERS_URL = ACCOUNTS_URL + "/accountNumbers"


@pytest.fixture(autouse=True)
def reset():
    Stub.bodies = {ACCOUNTS_URL: ACCOUNTS, NUMBERS_URL: HASHES}
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
    assert vti["account"] == "F00DBEEFCAFE4C0DE"
    assert messages[-1]["state"]["positions"]["cursor"] == "2026-10-27"
    assert [call[0] for call in Stub.calls] == [NUMBERS_URL, ACCOUNTS_URL]
    assert Stub.calls[1][1] == {"fields": "positions"}


def test_a_short_position_has_a_negative_basis_and_as_of_is_the_utc_day(monkeypatch):
    Stub.bodies[ACCOUNTS_URL] = [{"securitiesAccount": {"accountNumber": "12345678", "positions": [
        {"instrument": {"symbol": "XYZ", "assetType": "EQUITY"}, "longQuantity": 0.0,
         "shortQuantity": 4.0, "averagePrice": 50.0, "marketValue": -180.0}]}}]
    records, _ = _read()
    row = records[0]["data"]
    assert row["cost_basis"] == -200.0 and row["market_value"] == -180.0
    late = datetime(2026, 10, 27, 23, 30, tzinfo=timezone.utc)   # 19:30 New York
    monkeypatch.setattr(Stub, "_now", lambda self: late)
    records, _ = _read()
    assert records[0]["data"]["as_of"] == "2026-10-27"
    monkeypatch.setattr(Stub, "_now",
                        lambda self: datetime(2026, 10, 28, 0, 30, tzinfo=timezone.utc))
    records, _ = _read()
    assert records[0]["data"]["as_of"] == "2026-10-28"   # 20:30 New York the day before


def test_same_day_repull_is_skipped_in_backfill_but_not_live():
    state = {"positions": {"cursor": "2026-10-27"}}
    records, _ = _read(state, "backfill")
    assert records == []
    records, _ = _read(state, "live")
    assert len(records) == 2


def test_malformed_bodies_are_asset_errors():
    Stub.bodies[ACCOUNTS_URL] = {"not": "a list"}
    with pytest.raises(AssetError, match="accounts list"):
        _read()
    Stub.bodies[ACCOUNTS_URL] = [{"securitiesAccount": {"accountNumber": "12345678", "positions": [
        {"instrument": {"symbol": "X"}, "longQuantity": "ten", "shortQuantity": 0.0,
         "averagePrice": 1.0, "marketValue": 1.0}]}}]
    with pytest.raises(AssetError, match="malformed"):
        _read()
    # a missing number is malformed, never defaulted to zero
    Stub.bodies[ACCOUNTS_URL] = [{"securitiesAccount": {"accountNumber": "12345678", "positions": [
        {"instrument": {"symbol": "X"}, "longQuantity": 1.0, "shortQuantity": 0.0,
         "averagePrice": 1.0}]}}]
    with pytest.raises(AssetError, match="malformed"):
        _read()
    # an account absent from accountNumbers refuses rather than inventing a digest
    Stub.bodies[NUMBERS_URL] = []
    Stub.bodies[ACCOUNTS_URL] = ACCOUNTS
    with pytest.raises(AssetError, match="hashValue"):
        _read()
    Stub.bodies[NUMBERS_URL] = [{"accountNumber": "12345678"}]
    with pytest.raises(AssetError, match="accountNumbers entry 0"):
        _read()


def test_check_probes_accounts_and_transient_failures_retry_then_refuse(monkeypatch):
    monkeypatch.setattr("dskit.onboarding.libs.schwab.time.sleep", lambda s: None)
    Stub().check({"max_retries": 0})
    assert [call[0] for call in Stub.calls] == [ACCOUNTS_URL]
    Stub.calls = []
    Stub.bodies[ACCOUNTS_URL] = ConnectionError("HTTP 503")
    with pytest.raises(AssetError, match="after 3 attempt"):
        Stub().check({"max_retries": 2})
    assert len(Stub.calls) == 3
