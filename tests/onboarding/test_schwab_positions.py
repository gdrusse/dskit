"""Schwab account positions: a point-in-time snapshot stream (ADR-0279)."""

import json
import urllib.error
import urllib.request
from datetime import datetime, timezone

import pytest

from dskit.assets.base import AssetError
from dskit.onboarding import check_config, check_message
from dskit.onboarding.connector import resolve_connector
from dskit.onboarding.libs.schwab import SchwabBarsConnector, SchwabPositionsConnector

from .stub_connectors import ScriptedUrlopen

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


def _one(**over):
    """An accounts body with one position, fields overridden."""
    position = {"instrument": {"symbol": "X", "assetType": "EQUITY"}, "longQuantity": 1.0,
                "shortQuantity": 0.0, "averagePrice": 1.0, "marketValue": 1.0}
    position.update(over)
    return [{"securitiesAccount": {"accountNumber": "12345678", "positions": [position]}}]


def test_symbol_must_be_a_non_empty_string_never_coerced():
    for bad in (123, None, "", ["VTI"], True):
        Stub.bodies[ACCOUNTS_URL] = _one(instrument={"symbol": bad, "assetType": "EQUITY"})
        with pytest.raises(AssetError, match="position 0 in account 0 is malformed"):
            _read()
    Stub.bodies[ACCOUNTS_URL] = _one(instrument={"assetType": "EQUITY"})
    with pytest.raises(AssetError, match="malformed"):
        _read()
    Stub.bodies[ACCOUNTS_URL] = _one(instrument={"symbol": "VTI", "assetType": "EQUITY"})
    assert _read()[0][0]["data"]["symbol"] == "VTI"


def test_position_numbers_must_be_finite():
    bad = (float("nan"), float("inf"), float("-inf"), 10 ** 400)
    for name in ("longQuantity", "shortQuantity", "averagePrice", "marketValue"):
        for value in bad:
            Stub.bodies[ACCOUNTS_URL] = _one(**{name: value})
            with pytest.raises(AssetError, match="position 0 in account 0 is malformed"):
                _read()
    # finite factors whose product overflows: the derived cost basis is held to the same rule
    Stub.bodies[ACCOUNTS_URL] = _one(longQuantity=1e300, averagePrice=1e300)
    with pytest.raises(AssetError, match="position 0 in account 0 is malformed"):
        _read()
    # a negative market value is a short, finite and so still emitted (consumers filter it)
    Stub.bodies[ACCOUNTS_URL] = _one(marketValue=-180.0)
    assert _read()[0][0]["data"]["market_value"] == -180.0


def test_an_account_without_a_positions_key_refuses_never_defaults_to_empty():
    Stub.bodies[ACCOUNTS_URL] = [{"securitiesAccount": {"accountNumber": "12345678"}}]
    with pytest.raises(AssetError, match="account 0 has no positions"):
        _read()
    # an explicit empty list is the provider saying "none", and emits nothing
    Stub.bodies[ACCOUNTS_URL] = [{"securitiesAccount": {"accountNumber": "12345678",
                                                         "positions": []}}]
    assert _read()[0] == []
    Stub.bodies[ACCOUNTS_URL] = [{"securitiesAccount": {"accountNumber": "12345678",
                                                         "positions": None}}]
    with pytest.raises(AssetError, match="positions is not a list"):
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


def test_a_malformed_position_names_its_symbol_when_it_has_one():
    # the symbol is known: the message says which holding
    Stub.bodies[ACCOUNTS_URL] = _one(longQuantity="ten")
    with pytest.raises(AssetError) as caught:
        _read()
    assert caught.value.errors == ["Schwab position 0 in account 0 is malformed (symbol 'X')"]
    # no usable symbol: no suffix, nothing invented
    for bad in (_one(instrument={"assetType": "EQUITY"}),
                _one(instrument={"symbol": 123, "assetType": "EQUITY"}),
                _one(instrument="VTI")):
        Stub.bodies[ACCOUNTS_URL] = bad
        with pytest.raises(AssetError) as caught:
            _read()
        assert caught.value.errors == ["Schwab position 0 in account 0 is malformed"]
    # a non-mapping position has no symbol either
    Stub.bodies[ACCOUNTS_URL] = [{"securitiesAccount": {"accountNumber": "12345678",
                                                         "positions": ["VTI"]}}]
    with pytest.raises(AssetError) as caught:
        _read()
    assert caught.value.errors == ["Schwab position 0 in account 0 is malformed"]


def test_each_spec_is_its_own_copy_of_the_shared_knobs():
    first = SchwabPositionsConnector().spec()
    first["params"]["timeout"]["notes"] = "tampered"
    first["params"]["client_id_env"]["secret"] = False
    again = SchwabPositionsConnector().spec()
    assert again["params"]["timeout"]["notes"].startswith("HTTP timeout in seconds")
    assert again["params"]["client_id_env"]["secret"] is True
    bars = SchwabBarsConnector().spec()
    assert bars["params"]["timeout"]["notes"].startswith("HTTP timeout in seconds")
    assert bars["params"]["max_retries"] is not again["params"]["max_retries"]


class Wire(SchwabPositionsConnector):
    """The real transport; only the clock and the OAuth token are stubbed."""

    def _now(self):
        return NOW

    def _access_token(self, knobs):
        return "tok"


def _http(code):
    return urllib.error.HTTPError(ACCOUNTS_URL, code, "scripted", {}, None)


def _body(value):
    return (200, json.dumps(value).encode("utf-8"))


@pytest.fixture
def waits(monkeypatch):
    seen = []
    monkeypatch.setattr("dskit.onboarding.libs.schwab.time.sleep", seen.append)
    return seen


def test_wire_sends_a_bearer_header_and_encodes_params_in_the_url(monkeypatch):
    fake = ScriptedUrlopen(_body(HASHES), _body(ACCOUNTS))
    monkeypatch.setattr(urllib.request, "urlopen", fake)
    messages = list(Wire().read(CONFIG, ["positions"], {}, "backfill"))
    rows = [m["data"] for m in messages if m["type"] == "RECORD"]
    assert [r["symbol"] for r in rows] == ["AAPL", "VTI"]
    (numbers_url, numbers_headers, _), (accounts_url, accounts_headers, timeout) = fake.requests
    assert numbers_url == NUMBERS_URL                      # no params, no query string
    assert accounts_url == ACCOUNTS_URL + "?fields=positions"
    assert numbers_headers == accounts_headers == {"Authorization": "Bearer tok"}
    assert timeout == 30                                   # the shared default


def test_wire_503_is_retried_then_refused_after_max_retries(monkeypatch, waits):
    fake = ScriptedUrlopen(_http(503), _http(503), _http(503))
    monkeypatch.setattr(urllib.request, "urlopen", fake)
    with pytest.raises(AssetError) as caught:
        Wire().check({"max_retries": 2})
    assert caught.value.errors == [
        "Schwab accounts for 'accounts' failed after 3 attempt(s): HTTP 503"]
    assert len(fake.requests) == 3
    assert waits == [0.5, 1.0]                             # backoff before attempts 2 and 3
    # a transient failure that clears is not an error: 429, then a good body
    fake = ScriptedUrlopen(_http(429), _body(ACCOUNTS))
    monkeypatch.setattr(urllib.request, "urlopen", fake)
    Wire().check({"max_retries": 2})
    assert len(fake.requests) == 2


def test_wire_a_returned_retryable_status_is_retried_too(monkeypatch, waits):
    fake = ScriptedUrlopen((502, b""), (502, b""))
    monkeypatch.setattr(urllib.request, "urlopen", fake)
    with pytest.raises(AssetError, match=r"failed after 2 attempt\(s\): HTTP 502"):
        Wire().check({"max_retries": 1})
    assert len(fake.requests) == 2


def test_wire_404_is_refused_at_once_naming_the_noun(monkeypatch, waits):
    fake = ScriptedUrlopen(_http(404))
    monkeypatch.setattr(urllib.request, "urlopen", fake)
    with pytest.raises(AssetError) as caught:
        Wire().check({"max_retries": 3})
    assert caught.value.errors == ["Schwab accounts failed for 'accounts' (HTTP 404)"]
    assert len(fake.requests) == 1 and waits == []         # not retried, not waited on
    # the same refusal when the status comes back as a response instead of an exception
    fake = ScriptedUrlopen((404, b""))
    monkeypatch.setattr(urllib.request, "urlopen", fake)
    with pytest.raises(AssetError, match=r"Schwab accounts failed for 'accounts' \(HTTP 404\)"):
        Wire().check({"max_retries": 3})


def test_wire_malformed_json_is_refused_naming_the_noun(monkeypatch, waits):
    for raw in (b"{not json", b"\xff\xfe"):
        fake = ScriptedUrlopen((200, raw))
        monkeypatch.setattr(urllib.request, "urlopen", fake)
        with pytest.raises(AssetError) as caught:
            Wire().check({"max_retries": 3})
        assert caught.value.errors == ["Schwab accounts for 'accounts' returned malformed JSON"]
        assert len(fake.requests) == 1 and waits == []
