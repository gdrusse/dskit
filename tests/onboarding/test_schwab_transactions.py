"""Schwab account transactions: a ``trades`` stream for lot reconstruction (ADR-0279)."""

import json
import re
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone

import pytest

from dskit.assets.base import AssetError
from dskit.onboarding import check_config, check_message
from dskit.onboarding.connector import resolve_connector
from dskit.onboarding.libs.schwab import (
    MAX_TRANSACTIONS,
    SchwabPositionsConnector,
    SchwabTransactionsConnector,
)

from .stub_connectors import ScriptedUrlopen

ACCOUNTS_URL = "https://api.schwabapi.com/trader/v1/accounts"
NUMBERS_URL = ACCOUNTS_URL + "/accountNumbers"
HASH_A, HASH_B = "AAAA1111", "BBBB2222"
TX_A, TX_B = f"{ACCOUNTS_URL}/{HASH_A}/transactions", f"{ACCOUNTS_URL}/{HASH_B}/transactions"
HASHES = [{"accountNumber": "12345678", "hashValue": HASH_A},
          {"accountNumber": "87654321", "hashValue": HASH_B}]
NOW = datetime(2026, 10, 27, 14, 30, 5, 123456, tzinfo=timezone.utc)
CONFIG = {"start": "2024-06-01"}
USD = {"assetType": "CURRENCY", "symbol": "CURRENCY_USD", "description": "USD currency"}


def _fee(kind="SEC_FEE", cost=0.0):
    return {"instrument": dict(USD), "amount": 0.0, "cost": cost, "feeType": kind}


def _item(symbol, amount, price, asset="EQUITY", effect="OPENING"):
    item = {"instrument": {"assetType": asset, "symbol": symbol, "type": "COMMON_STOCK"},
            "amount": amount, "cost": -amount * price, "price": price}
    if effect is not None:
        item["positionEffect"] = effect
    return item


def _tx(activity, trade_date, net, *items, kind="TRADE", time=None):
    return {"activityId": activity, "time": time or trade_date, "accountNumber": "12345678",
            "type": kind, "status": "VALID", "tradeDate": trade_date,
            "settlementDate": trade_date, "netAmount": net, "activityType": "EXECUTION",
            "transferItems": list(items)}


# account A: VTI bought in the first window, partly sold in the third; an option trade and an
# ETF typed COLLECTIVE_INVESTMENT (skipped by the default asset_types) in the second
A_WINDOWS = {
    "2024-06-01": [_tx(101, "2024-07-15T13:31:00+0000", -1801.0, _fee("COMMISSION", -1.0),
                       _item("VTI", 10.0, 180.0))],
    "2025-06-01": [_tx(102, "2025-08-01T14:00:00+0000", -250.0, _fee(),
                       _item("SPY 261218C00500000", 1.0, 2.5, asset="OPTION")),
                   _tx(103, "2025-09-02T14:00:00+0000", -905.0,
                       _item("SCHD", 10.0, 90.5, asset="COLLECTIVE_INVESTMENT"))],
    "2026-06-01": [_tx(104, "2026-10-26T19:59:00+0000", 2099.97, _fee("SEC_FEE", -0.03),
                       _item("VTI", -7.0, 300.0, effect="CLOSING"))],
}
# account B: one AAPL buy in the last window
B_WINDOWS = {"2024-06-01": [], "2025-06-01": [],
             "2026-06-01": [_tx(201, "2026-07-01T13:45:00+0000", -1500.0,
                                _item("AAPL", 10.0, 150.0))]}


class Stub(SchwabTransactionsConnector):
    """Scripted transport and clock: accountNumbers, then one body per (url, window start)."""

    bodies = {}
    calls = []

    def _now(self):
        return NOW

    def _access_token(self, knobs):
        return "token"

    def _request(self, token, label, url, params, timeout):
        type(self).calls.append((url, dict(params)))
        if url == NUMBERS_URL:
            body = type(self).bodies[url]
        else:
            body = type(self).bodies[url][params["startDate"][:10]]
        if isinstance(body, Exception):
            raise body
        return body


@pytest.fixture(autouse=True)
def reset():
    Stub.bodies = {NUMBERS_URL: HASHES, TX_A: dict(A_WINDOWS), TX_B: dict(B_WINDOWS)}
    Stub.calls = []
    yield


def _read(state=None, mode="backfill", config=None):
    messages = list(Stub().read(config or CONFIG, ["trades"], state or {}, mode))
    assert all(check_message(message) for message in messages)
    return [m["data"] for m in messages if m["type"] == "RECORD"], messages


def test_registered_and_default_deny():
    assert resolve_connector("schwab_transactions") is SchwabTransactionsConnector
    check_config(SchwabTransactionsConnector(), CONFIG)
    with pytest.raises(AssetError, match="unknown key"):
        check_config(SchwabTransactionsConnector(), {**CONFIG, "symbols": ["AAPL"]})
    with pytest.raises(AssetError, match="start"):
        check_config(SchwabTransactionsConnector(), {})


def test_discover_declares_the_trades_stream():
    (stream,) = Stub().discover(CONFIG)
    assert stream["stream"] == "trades"
    assert stream["primary_key"] == ["account", "transaction_id", "symbol"]
    assert stream["schema"]["fields"] == [
        "account", "transaction_id", "trade_date", "time", "symbol", "asset_type", "quantity",
        "price", "amount", "position_effect"]
    assert Stub.calls == [], "discover is offline"


def test_windows_of_at_most_a_year_cover_start_to_now_for_every_account():
    _read()
    numbers, *pulls = Stub.calls
    assert numbers == (NUMBERS_URL, {})
    windows = [("2024-06-01T00:00:00.000Z", "2025-06-01T00:00:00.000Z"),
               ("2025-06-01T00:00:00.000Z", "2026-06-01T00:00:00.000Z"),
               ("2026-06-01T00:00:00.000Z", "2026-10-27T14:30:05.123Z")]
    assert pulls == [(url, {"startDate": s, "endDate": e, "types": "TRADE"})
                     for url in (TX_A, TX_B) for s, e in windows]


def test_one_row_per_kept_item_signed_quantity_and_net_cash():
    rows, messages = _read()
    assert [(r["account"], r["transaction_id"], r["symbol"]) for r in rows] == [
        (HASH_A, 101, "VTI"), (HASH_B, 201, "AAPL"), (HASH_A, 104, "VTI")]
    buy, aapl, sell = rows
    assert buy == {"account": HASH_A, "transaction_id": 101, "trade_date": "2024-07-15",
                   "time": "2024-07-15T13:31:00+0000", "symbol": "VTI", "asset_type": "EQUITY",
                   "quantity": 10.0, "price": 180.0, "amount": -1801.0,
                   "position_effect": "OPENING"}
    assert (sell["quantity"], sell["amount"], sell["position_effect"]) == (-7.0, 2099.97, "CLOSING")
    assert sell["trade_date"] == "2026-10-26"
    records = [m for m in messages if m["type"] == "RECORD"]
    assert [m["effective_date"] for m in records] == ["2024-07-15", "2026-07-01", "2026-10-26"]
    assert "12345678" not in json.dumps(messages) and "87654321" not in json.dumps(messages)


def test_skipped_items_and_fee_lines_are_counted_in_a_log_and_the_state():
    _, messages = _read()
    state = messages[-1]
    assert state["type"] == "STATE"
    assert state["state"]["trades"] == {"cursor": "2026-10-26", "start": "2024-06-01",
                                        "last_pull": {
        "transactions": 5, "rows": 3, "fee_items": 3,
        "skipped": {"COLLECTIVE_INVESTMENT": 1, "OPTION": 1}, "unattributed": 0}}
    (log,) = [m for m in messages if m["type"] == "LOG"]
    assert "skipped by asset type {'COLLECTIVE_INVESTMENT': 1, 'OPTION': 1}" in log["message"]
    assert "3 fee line(s)" in log["message"]


def test_asset_types_and_types_are_knobs():
    rows, _ = _read(config={**CONFIG, "asset_types": ["EQUITY", "COLLECTIVE_INVESTMENT"],
                            "types": ["TRADE", "DIVIDEND_OR_INTEREST"]})
    assert [r["symbol"] for r in rows] == ["VTI", "SCHD", "AAPL", "VTI"]
    assert {params["types"] for url, params in Stub.calls if url != NUMBERS_URL} == {
        "TRADE,DIVIDEND_OR_INTEREST"}


def test_a_transaction_with_several_kept_items_carries_no_amount_and_is_counted():
    Stub.bodies[TX_A]["2024-06-01"] = [_tx(105, "2024-07-15T13:31:00+0000", -2700.0,
                                           _item("VTI", 10.0, 180.0), _item("VOO", 2.0, 450.0))]
    rows, messages = _read()
    pair = [r for r in rows if r["transaction_id"] == 105]
    assert [(r["symbol"], r["amount"]) for r in pair] == [("VOO", None), ("VTI", None)]
    assert messages[-1]["state"]["trades"]["last_pull"]["unattributed"] == 1


def test_the_cursor_day_is_requested_again_in_both_modes():
    for mode in ("backfill", "live"):
        Stub.calls = []
        Stub.bodies[TX_A]["2026-10-20"] = A_WINDOWS["2026-06-01"]
        Stub.bodies[TX_B]["2026-10-20"] = []
        rows, messages = _read({"trades": {"cursor": "2026-10-20"}}, mode)
        assert [(url, p["startDate"]) for url, p in Stub.calls[1:]] == [
            (TX_A, "2026-10-20T00:00:00.000Z"), (TX_B, "2026-10-20T00:00:00.000Z")]
        assert [r["transaction_id"] for r in rows] == [104]
        assert messages[-1]["state"]["trades"]["cursor"] == "2026-10-26"
    # a cursor before start does not reach back past it; an empty pull keeps the cursor
    Stub.calls = []
    Stub.bodies[TX_A] = {k: [] for k in A_WINDOWS}
    Stub.bodies[TX_B] = {k: [] for k in B_WINDOWS}
    rows, messages = _read({"trades": {"cursor": "2020-01-01"}})
    assert Stub.calls[1][1]["startDate"] == "2024-06-01T00:00:00.000Z"
    assert rows == [] and messages[-1]["state"]["trades"]["cursor"] == "2020-01-01"


def test_window_days_narrows_each_request():
    Stub.bodies[TX_A] = {}
    Stub.bodies[TX_B] = {}
    for url in (TX_A, TX_B):
        for day in ("2026-10-01", "2026-10-11", "2026-10-21"):
            Stub.bodies[url][day] = []
    _read(config={"start": "2026-10-01", "window_days": 10})
    assert [(p["startDate"][:10], p["endDate"][:10]) for _, p in Stub.calls[1:4]] == [
        ("2026-10-01", "2026-10-11"), ("2026-10-11", "2026-10-21"), ("2026-10-21", "2026-10-27")]


def test_a_start_after_today_requests_nothing():
    rows, messages = _read(config={"start": "2026-10-28"})
    assert rows == [] and Stub.calls == [(NUMBERS_URL, {})]
    assert messages[-1]["state"]["trades"]["last_pull"]["transactions"] == 0


def test_a_window_at_the_response_cap_refuses_as_truncated():
    Stub.bodies[TX_A]["2024-06-01"] = [
        _tx(1000 + i, "2024-07-15T13:31:00+0000", -1.0, _fee()) for i in range(MAX_TRANSACTIONS)]
    with pytest.raises(AssetError) as caught:
        _read()
    assert caught.value.errors == [
        f"Schwab transactions for account 0 2024-06-01..2025-06-01 returned {MAX_TRANSACTIONS} "
        "transactions, the response cap: the window may be truncated; lower window_days"]


def test_a_key_repeated_across_windows_is_one_row_unless_it_differs():
    repeat = A_WINDOWS["2024-06-01"][0]
    Stub.bodies[TX_A]["2025-06-01"] = [repeat]
    rows, _ = _read()
    assert [r["transaction_id"] for r in rows].count(101) == 1
    Stub.bodies[TX_A]["2025-06-01"] = [{**repeat, "netAmount": -1802.0}]
    with pytest.raises(AssetError, match=r"key \('AAAA1111', 101, 'VTI'\) twice with different"):
        _read()


MALFORMED = [
    ({"time": None}, "transaction 0 .* is malformed"),
    ({"time": 1721050260}, "transaction 0 .* is malformed"),
    ({"time": "yesterday"}, "transaction 0 .* is malformed"),
    ({"status": None}, "transaction 0 .* is malformed"),
    ({"status": 1}, "transaction 0 .* is malformed"),
    ({"activityId": None}, "transaction 0 .* is malformed"),
    ({"activityId": True}, "transaction 0 .* is malformed"),
    ({"activityId": "101"}, "transaction 0 .* is malformed"),
    ({"tradeDate": "15/07/2024"}, "transaction 0 .* is malformed"),
    ({"tradeDate": None}, "transaction 0 .* is malformed"),
    ({"netAmount": "1801"}, "transaction 0 .* is malformed"),
    ({"netAmount": float("nan")}, "transaction 0 .* is malformed"),
    ({"netAmount": False}, "transaction 0 .* is malformed"),
    ({"transferItems": None}, "transaction 0 .* is malformed"),
    ({"type": "JOURNAL"}, "transaction 0 .* has type 'JOURNAL', not one requested"),
    ({"transferItems": ["VTI"]}, "item 0 of transaction 0 .* is malformed"),
    ({"transferItems": [{"amount": 1.0}]}, "item 0 of transaction 0 .* is malformed"),
    ({"transferItems": [{"instrument": {"symbol": "VTI"}}]}, "item 0 of transaction 0"),
    ({"transferItems": [_item("", 1.0, 1.0)]}, "item 0 of transaction 0 .* is malformed"),
    ({"transferItems": [_item(None, 1.0, 1.0)]}, "item 0 of transaction 0 .* is malformed"),
    ({"transferItems": [_item("VTI", 0.0, 1.0)]}, "item 0 of transaction 0 .* is malformed"),
    ({"transferItems": [_item("VTI", True, 1.0)]}, "item 0 of transaction 0 .* is malformed"),
    ({"transferItems": [_item("VTI", float("inf"), 1.0)]}, "item 0 of transaction 0"),
    ({"transferItems": [_item("VTI", 1.0, -1.0)]}, "item 0 of transaction 0 .* is malformed"),
    ({"transferItems": [{**_item("VTI", 1.0, 1.0), "price": None}]}, "item 0 of transaction 0"),
    ({"transferItems": [_item("VTI", 1.0, 1.0, effect="SIDEWAYS")]}, "item 0 of transaction 0"),
    ({"transferItems": [{**_fee(), "feeType": 5}]}, "item 0 of transaction 0 .* is malformed"),
]


@pytest.mark.parametrize("over, match", MALFORMED)
def test_an_unrecognised_shape_refuses_by_index(over, match):
    Stub.bodies[TX_A]["2024-06-01"] = [{**A_WINDOWS["2024-06-01"][0], **over}]
    with pytest.raises(AssetError) as caught:
        _read()
    (error,) = caught.value.errors
    assert error.startswith("Schwab ") and re.search(match, error), error
    assert "12345678" not in error


def test_a_non_list_body_or_a_non_mapping_transaction_refuses():
    for bad, match in (({"transactions": []}, "is not a list"),
                       (["x"], "transaction 0 .* is malformed")):
        Stub.bodies[TX_A]["2024-06-01"] = bad
        with pytest.raises(AssetError, match=match):
            _read()


def test_an_absent_position_effect_is_null_not_invented():
    Stub.bodies[TX_A]["2024-06-01"] = [_tx(101, "2024-07-15T13:31:00+0000", -1800.0,
                                           _item("VTI", 10.0, 180.0, effect=None))]
    rows, _ = _read()
    assert rows[0]["position_effect"] is None


BAD_CONFIG = [
    ({"start": None}, "config.start must be an ISO date"),
    ({"start": "2024-06-01T00:00:00"}, "config.start must be an ISO date"),
    ({"start": "June 2024"}, "config.start must be an ISO date"),
    ({"types": []}, "config.types must be a non-empty list"),
    ({"types": "TRADE"}, "config.types must be a non-empty list"),
    ({"types": ["TRADE", "TRADE"]}, "config.types must be a non-empty list"),
    ({"types": ["TRADES"]}, "config.types must be a non-empty list"),
    ({"asset_types": []}, "config.asset_types must be a non-empty list"),
    ({"asset_types": ["ETF"]}, "config.asset_types must be a non-empty list"),
    ({"window_days": 0}, "config.window_days must be an int in"),
    ({"window_days": 366}, "config.window_days must be an int in"),
    ({"window_days": True}, "config.window_days must be an int in"),
    ({"window_days": 30.0}, "config.window_days must be an int in"),
    ({"timeout": 0}, "config.timeout must be a positive number"),
    ({"max_retries": -1}, "config.max_retries must be an int >= 0"),
    ({"token_path_env": ""}, "config.token_path_env must be a non-empty"),
]


@pytest.mark.parametrize("over, match", BAD_CONFIG)
def test_each_bad_config_value_is_one_named_problem(over, match):
    with pytest.raises(AssetError) as caught:
        SchwabTransactionsConnector().resolve_knobs({**CONFIG, **over})
    (error,) = caught.value.errors
    assert error.startswith(match), error


def test_config_problems_accumulate_and_read_arguments_refuse_by_name():
    connector = SchwabTransactionsConnector()
    with pytest.raises(AssetError, match="config must be a dict, got list"):
        connector.resolve_knobs([])
    with pytest.raises(AssetError) as caught:
        connector.resolve_knobs({"start": "x", "types": [], "window_days": 0})
    assert len(caught.value.errors) == 3
    for state, streams, mode, match in (
        ([], ["trades"], "backfill", "state must be a dict"),
        ({}, [], "backfill", "streams must be a non-empty list"),
        ({}, ["trades"], "weekly", "mode must be one of"),
        ({}, ["positions"], "backfill", r"unknown stream 'positions'; discovered: \['trades'\]"),
    ):
        with pytest.raises(AssetError, match=match):
            list(Stub().read(CONFIG, streams, state, mode))
    assert Stub.calls == []


def test_check_probes_account_numbers_only():
    Stub().check(CONFIG)
    assert Stub.calls == [(NUMBERS_URL, {})]


def test_the_accounts_listing_is_the_positions_pack_one():
    # one owner of the accountNumbers read: both packs inherit the base's
    assert SchwabTransactionsConnector._hashes is SchwabPositionsConnector._hashes
    Stub.bodies[NUMBERS_URL] = [{"accountNumber": "12345678"}]
    with pytest.raises(AssetError, match="accountNumbers entry 0 is malformed"):
        _read()


def test_each_spec_is_its_own_copy_of_the_shared_knobs():
    first = SchwabTransactionsConnector().spec()
    assert first["params"]["start"]["required"] is True
    first["params"]["timeout"]["notes"] = "tampered"
    assert SchwabTransactionsConnector().spec()["params"]["timeout"]["notes"].startswith("HTTP")


class Wire(SchwabTransactionsConnector):
    """The real transport; only the clock and the OAuth token are stubbed."""

    def _now(self):
        return NOW

    def _access_token(self, knobs):
        return "tok"


def _body(value):
    return (200, json.dumps(value).encode("utf-8"))


def test_wire_encodes_the_hash_and_the_window_and_sends_a_bearer_header(monkeypatch):
    hashes = [{"accountNumber": "12345678", "hashValue": "A/B+C"}]
    fake = ScriptedUrlopen(_body(hashes), _body([]))
    monkeypatch.setattr(urllib.request, "urlopen", fake)
    list(Wire().read({"start": "2026-10-01"}, ["trades"], {}, "backfill"))
    (_, _, _), (url, headers, timeout) = fake.requests
    assert url == (f"{ACCOUNTS_URL}/A%2FB%2BC/transactions?startDate=2026-10-01T00%3A00%3A00.000Z"
                   "&endDate=2026-10-27T14%3A30%3A05.123Z&types=TRADE")
    assert headers == {"Authorization": "Bearer tok"} and timeout == 30


def test_a_late_posting_dated_before_the_cursor_never_moves_it_back():
    # Schwab returns a trade dated the 19th inside the window that starts on the cursor (20th)
    Stub.bodies[TX_A]["2026-10-20"] = [_tx(105, "2026-10-19T15:00:00+0000", -100.0,
                                           _item("VTI", 1.0, 100.0))]
    Stub.bodies[TX_B]["2026-10-20"] = []
    rows, messages = _read({"trades": {"cursor": "2026-10-20"}})
    assert [r["trade_date"] for r in rows] == ["2026-10-19"], "emitted, never dropped"
    assert messages[-1]["state"]["trades"]["cursor"] == "2026-10-20"


def test_the_time_is_emitted_as_given_and_orders_a_day_the_trade_date_cannot():
    """Two fills of one day: ids 7 (15:00) and 9 (10:00). The row carries Schwab's `time` string
    untouched (its own offset, no conversion), beside the calendar date of `tradeDate`."""
    Stub.bodies[TX_A]["2024-06-01"] = [
        _tx(7, "2024-07-15T00:00:00+0000", 900.0, _item("VTI", -5.0, 180.0, effect="CLOSING"),
            time="2024-07-15T11:00:00-0400"),
        _tx(9, "2024-07-15T00:00:00+0000", -1800.0, _item("VTI", 10.0, 180.0),
            time="2024-07-15T10:00:00-0400")]
    rows, _ = _read()
    by_id = {r["transaction_id"]: r for r in rows}
    assert (by_id[7]["time"], by_id[9]["time"]) == ("2024-07-15T11:00:00-0400",
                                                    "2024-07-15T10:00:00-0400")
    assert by_id[7]["trade_date"] == by_id[9]["trade_date"] == "2024-07-15"


STATUS_REFUSALS = ["PENDING", "INVALID", "UNKNOWN", "valid", ""]


@pytest.mark.parametrize("status", STATUS_REFUSALS)
def test_a_transaction_that_is_not_valid_refuses_by_index_and_status(status):
    """A PENDING trade has not settled into the account and an INVALID one was cancelled: either
    would become a lot that never existed (or never will), so the pull stops rather than skips."""
    Stub.bodies[TX_A]["2024-06-01"] = [
        A_WINDOWS["2024-06-01"][0], {**A_WINDOWS["2024-06-01"][0], "activityId": 102,
                                     "status": status}]
    with pytest.raises(AssetError) as caught:
        _read()
    assert caught.value.errors == [
        f"Schwab transaction 1 for account 0 2024-06-01..2025-06-01 has status {status!r}, "
        "not VALID: only a valid transaction is a trade"]
    assert "12345678" not in str(caught.value)


def _lowered_bodies():
    """Every account's windows from 2024-01-01: one year at a time, today 2026-10-27."""
    first = defaultdict(list, {
        "2024-01-01": [A_WINDOWS["2024-06-01"][0]],              # VTI buy, trade date 2024-07-15
        "2025-12-31": A_WINDOWS["2026-06-01"]})                  # VTI sale, trade date 2026-10-26
    Stub.bodies[TX_A], Stub.bodies[TX_B] = first, defaultdict(list)


def test_lowering_start_after_a_pull_repulls_from_the_new_start():
    """The cursor says everything before 2026-10-26 was pulled, true only for trades on or after
    the OLD start: a lowered start must reach back, so the stored start is compared and the
    cursor cleared for that stream. Without the stored start the pull would begin at the cursor."""
    _, messages = _read()
    state = messages[-1]["state"]
    assert state["trades"]["start"] == "2024-06-01" and state["trades"]["cursor"] == "2026-10-26"
    Stub.calls = []
    _lowered_bodies()
    rows, messages = _read(state, config={"start": "2024-01-01"})
    pulls = [params["startDate"] for url, params in Stub.calls if url == TX_A]
    assert pulls == ["2024-01-01T00:00:00.000Z", "2024-12-31T00:00:00.000Z",
                     "2025-12-31T00:00:00.000Z"]
    assert [r["transaction_id"] for r in rows] == [101, 104], "the whole history, again"
    assert messages[-1]["state"]["trades"]["start"] == "2024-01-01"
    assert messages[-1]["state"]["trades"]["cursor"] == "2026-10-26"
    # the old cursor is cleared, not kept: a re-pull that finds nothing leaves none
    Stub.bodies[TX_A], Stub.bodies[TX_B] = defaultdict(list), defaultdict(list)
    rows, messages = _read(state, config={"start": "2024-01-01"})
    assert rows == [] and messages[-1]["state"]["trades"]["cursor"] == ""


def test_an_unchanged_or_raised_start_resumes_from_the_cursor():
    state = {"trades": {"cursor": "2026-10-20", "start": "2024-06-01"}}
    for start in ("2024-06-01", "2025-01-01"):
        Stub.calls = []
        Stub.bodies[TX_A] = defaultdict(list, {"2026-10-20": A_WINDOWS["2026-06-01"]})
        Stub.bodies[TX_B] = defaultdict(list)
        rows, messages = _read(state, config={"start": start})
        assert [params["startDate"] for url, params in Stub.calls if url == TX_A] == [
            "2026-10-20T00:00:00.000Z"]
        assert [r["transaction_id"] for r in rows] == [104]
        assert messages[-1]["state"]["trades"]["start"] == start
        assert messages[-1]["state"]["trades"]["cursor"] == "2026-10-26"


def test_a_stored_start_that_is_not_a_date_refuses():
    for bad in ("soon", 20240601, None):
        with pytest.raises(AssetError, match="state start is not an ISO date"):
            _read({"trades": {"cursor": "2026-10-20", "start": bad}})
