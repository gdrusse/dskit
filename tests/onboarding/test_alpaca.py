"""Alpaca bars pack through its connector contract, without a network."""

from datetime import date, datetime, timedelta, timezone

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


def _option_config(**overrides):
    config = {
        "symbols": ["AMZN"],
        "start": "2024-02-01",
        "end": "2026-09-30",
        "dte_min": 30,
        "dte_max": 45,
        "multiplier": 100,
        "status": "inactive",
    }
    config.update(overrides)
    return config


class _FakeContract:
    def __init__(self, symbol, underlying, size, style):
        self.symbol = symbol
        self.underlying_symbol = underlying
        self.size = size
        self.style = style
        self.expiration_date = date(2024, 2, 2)
        self.strike_price = 125.0


class _FakeContractsResponse:
    def __init__(self, contracts, next_page_token=None):
        self.option_contracts = contracts
        self.next_page_token = next_page_token


class _FakeTradingClient:
    def __init__(self, key, secret):
        pass

    def get_option_contracts(self, request):
        return _FakeContractsResponse([
            _FakeContract("AMZN240202C00125000", "AMZN", "100", "american"),
        ])


class _FakeBar:
    def __init__(self, ts, o, h, low, c, v):
        self.timestamp = ts
        self.open, self.high, self.low, self.close, self.volume = o, h, low, c, v
        self.trade_count = None
        self.vwap = None


class _FakeHistoricalClient:
    def __init__(self, key, secret):
        pass

    def get_option_bars(self, request):
        return type("BarSet", (), {"data": {
            "AMZN240202C00125000": [_FakeBar(
                datetime(2023, 12, 20, 20, 0, tzinfo=timezone.utc),
                124.0, 126.0, 123.0, 125.5, 500.0)],
        }})()


def test_option_fetch_emits_contracts_and_bars_without_network(monkeypatch):
    from alpaca.data import historical as hist
    from alpaca.trading import client as trading

    monkeypatch.setenv("APCA_API_KEY_ID", "k")
    monkeypatch.setenv("APCA_API_SECRET_KEY", "s")
    monkeypatch.setattr(trading, "TradingClient", _FakeTradingClient)
    monkeypatch.setattr(hist, "OptionHistoricalDataClient", _FakeHistoricalClient)

    from dskit.onboarding.libs.alpaca import AlpacaOptionFetchConnector
    connector = AlpacaOptionFetchConnector()
    messages = list(connector.read(
        _option_config(), ["contracts", "bars"], {}, "backfill"))
    assert all(check_message(m) for m in messages)
    records = [m for m in messages if m["type"] == "RECORD"]
    contracts = [m for m in records if m["stream"] == "contracts"]
    bars = [m for m in records if m["stream"] == "bars"]
    assert len(contracts) == 1
    assert contracts[0]["data"]["strike"] == 125.0
    assert contracts[0]["data"]["expiry"] == "2024-02-02"
    assert contracts[0]["data"]["style"] == "american"
    assert contracts[0]["data"]["contract_size"] == 100.0
    assert contracts[0]["data"]["multiplier"] == 100.0
    assert contracts[0]["data"]["contract_terms_status"] == "metadata_present"
    assert len(bars) == 1
    assert bars[0]["data"]["mark"] == 125.5
    assert bars[0]["data"]["quote_date"] == "2023-12-20"
    assert bars[0]["data"]["price_basis"] == "trade_close"


def test_option_fetch_refuses_nonstandard_multiplier(monkeypatch):
    from alpaca.data import historical as hist
    from alpaca.trading import client as trading

    class NonStandard(_FakeTradingClient):
        def get_option_contracts(self, request):
            return _FakeContractsResponse([
                _FakeContract("AMZN240202C00125000", "AMZN", "0", "american"),
            ])

    monkeypatch.setenv("APCA_API_KEY_ID", "k")
    monkeypatch.setenv("APCA_API_SECRET_KEY", "s")
    monkeypatch.setattr(trading, "TradingClient", NonStandard)
    monkeypatch.setattr(hist, "OptionHistoricalDataClient", _FakeHistoricalClient)

    from dskit.onboarding.libs.alpaca import AlpacaOptionFetchConnector
    messages = list(AlpacaOptionFetchConnector().read(
        _option_config(), ["contracts", "bars"], {}, "backfill"))
    records = [m for m in messages if m["type"] == "RECORD"]
    assert records == []


def test_option_fetch_paginates_contracts(monkeypatch):
    from alpaca.data import historical as hist
    from alpaca.trading import client as trading

    class Paged(_FakeTradingClient):
        def __init__(self, key, secret):
            self.tokens = []

        def get_option_contracts(self, request):
            self.tokens.append(request.page_token)
            if request.page_token is None:
                return _FakeContractsResponse(
                    [_FakeContract("AMZN240202C00125000", "AMZN", "100", "american")],
                    next_page_token="tok")
            if request.page_token == "tok":
                return _FakeContractsResponse(
                    [_FakeContract("AMZN240202P00125000", "AMZN", "100", "american")])
            raise AssertionError(f"unexpected page_token {request.page_token!r}")

    monkeypatch.setenv("APCA_API_KEY_ID", "k")
    monkeypatch.setenv("APCA_API_SECRET_KEY", "s")
    client = Paged("k", "s")
    monkeypatch.setattr(trading, "TradingClient", lambda k, s: client)
    monkeypatch.setattr(hist, "OptionHistoricalDataClient", _FakeHistoricalClient)

    from dskit.onboarding.libs.alpaca import AlpacaOptionFetchConnector
    messages = list(AlpacaOptionFetchConnector().read(
        _option_config(), ["contracts"], {}, "backfill"))
    contracts = [m for m in messages if m["type"] == "RECORD"]
    assert len(contracts) == 2
    assert {c["data"]["contract"] for c in contracts} == {
        "AMZN240202C00125000", "AMZN240202P00125000"}
    assert client.tokens == [None, "tok"]


def test_option_fetch_contract_is_admissible_to_the_panel(monkeypatch):
    from alpaca.data import historical as hist
    from alpaca.trading import client as trading

    monkeypatch.setenv("APCA_API_KEY_ID", "k")
    monkeypatch.setenv("APCA_API_SECRET_KEY", "s")
    monkeypatch.setattr(trading, "TradingClient", _FakeTradingClient)
    monkeypatch.setattr(hist, "OptionHistoricalDataClient", _FakeHistoricalClient)

    from dskit.onboarding.libs.alpaca import AlpacaOptionFetchConnector
    from dskit.pipeline.libs.predictive_cdf import OptionCDFPanel

    messages = list(AlpacaOptionFetchConnector().read(
        _option_config(), ["contracts", "bars"], {}, "backfill"))
    bar = [m for m in messages if m["type"] == "RECORD" and m["stream"] == "bars"][0]["data"]
    panel = OptionCDFPanel("panel", {
        "probabilities": [0.1, 0.5, 0.9],
        "min_forward_pairs": 2, "min_wing_nodes": 2,
        "max_inner_gap": 0.2, "max_outer_gap": 0.2,
        "max_projection_distance": None, "max_forward_dispersion": None,
        "required_multiplier": 100, "required_style": "american",
        "max_quote_age_seconds": 900,
    })
    label = {"spot": 125.0, "entry_close_at": "2026-09-30T20:00:00+00:00"}
    assert panel._admission(bar, label) == []


# ---------------------------------------------------------------------------
# AlpacaOptionActivityConnector: underlyings + per-session chain volume
# ---------------------------------------------------------------------------


def _activity_config(**overrides):
    config = {"sessions": 2, "end": "2026-10-02"}
    config.update(overrides)
    return config


class _Asset:
    def __init__(self, symbol, name="Co Inc", exchange="NYSE", attributes=("has_options",)):
        self.symbol, self.name, self.exchange = symbol, name, exchange
        self.attributes = list(attributes)


class _Day:
    def __init__(self, day):
        self.date = day


class _ActContract:
    def __init__(self, symbol, root, underlying):
        self.symbol, self.root_symbol, self.underlying_symbol = symbol, root, underlying


class _ActTrading:
    """Records requests; AAA has an active and an inactive contract, BBB one."""

    assets = [_Asset("AAA", "Alpha Corp"), _Asset("BBB", "Beta Corp"),
              _Asset("FND", "Some Index Fund ETF"), _Asset("NOO", attributes=())]
    calendar = [date(2026, 9, 30), date(2026, 10, 1), date(2026, 10, 2)]
    listed = {("AAA", "active"): ["AAA261120C00010000"],
              ("AAA", "inactive"): ["AAA261002C00010000", "1AAA261002C00010000"],
              ("BBB", "active"): ["BBB261120C00010000"]}
    requests = []

    def __init__(self, key, secret):
        pass

    def get_all_assets(self, request):
        type(self).requests.append(("assets", request))
        return [a for a in self.assets if request.attributes in a.attributes]

    def get_calendar(self, request):
        type(self).requests.append(("calendar", request))
        return [_Day(d) for d in self.calendar]

    def get_option_contracts(self, request):
        type(self).requests.append(("contracts", request))
        sym = request.underlying_symbols[0]
        status = getattr(request.status, "value", request.status)
        names = self.listed.get((sym, status), [])
        return _FakeContractsResponse([
            _ActContract(n, "1AAA" if n.startswith("1") else sym, sym) for n in names])


class _ActHistorical:
    batches = []

    def __init__(self, key, secret):
        pass

    def get_option_bars(self, request):
        type(self).batches.append(list(request.symbol_or_symbols))
        day = lambda d: datetime(2026, 10, d, 4, 0, tzinfo=timezone.utc)  # noqa: E731
        data = {
            "AAA261120C00010000": [_FakeBar(day(1), 1, 1, 1, 1, 10.0),
                                   _FakeBar(day(2), 1, 1, 1, 1, 5.0)],
            "AAA261002C00010000": [_FakeBar(day(2), 1, 1, 1, 1, 7.0)],
            "BBB261120C00010000": [_FakeBar(day(2), 1, 1, 1, 1, 2.0)],
        }
        return type("BarSet", (), {"data": {
            s: data[s] for s in request.symbol_or_symbols if s in data}})()


def _activity(monkeypatch, config, streams):
    from alpaca.data import historical as hist
    from alpaca.trading import client as trading

    monkeypatch.setenv("APCA_API_KEY_ID", "k")
    monkeypatch.setenv("APCA_API_SECRET_KEY", "s")
    _ActTrading.requests = []
    _ActHistorical.batches = []
    monkeypatch.setattr(trading, "TradingClient", _ActTrading)
    monkeypatch.setattr(hist, "OptionHistoricalDataClient", _ActHistorical)
    messages = list(alpaca.AlpacaOptionActivityConnector().read(
        config, streams, {}, "backfill"))
    assert all(check_message(m) for m in messages)
    return [m["data"] for m in messages if m["type"] == "RECORD"
            if m["stream"] in streams]


def test_activity_underlyings_filter_by_attribute_and_name_regex(monkeypatch):
    rows = _activity(monkeypatch, _activity_config(exclude_name_regex="ETF|Fund"),
                     ["underlyings"])
    assert [r["symbol"] for r in rows] == ["AAA", "BBB"]
    assert rows[0] == {"symbol": "AAA", "name": "Alpha Corp", "exchange": "NYSE"}
    request = next(r for k, r in _ActTrading.requests if k == "assets")
    assert request.attributes == "has_options"


def test_activity_chain_volume_sums_contracts_per_session(monkeypatch):
    rows = _activity(monkeypatch, _activity_config(symbols=["AAA", "BBB"]),
                     ["chain_volume"])
    got = {(r["symbol"], r["session"]): (r["volume"], r["contracts_traded"])
           for r in rows}
    assert got == {("AAA", "2026-10-01"): (10.0, 1), ("AAA", "2026-10-02"): (12.0, 2),
                   ("BBB", "2026-10-02"): (2.0, 1)}


def test_activity_window_comes_from_the_calendar_and_lists_both_statuses(monkeypatch):
    _activity(monkeypatch, _activity_config(symbols=["AAA"]), ["chain_volume"])
    contracts = [r for k, r in _ActTrading.requests if k == "contracts"]
    assert sorted(getattr(r.status, "value", r.status) for r in contracts) == [
        "active", "inactive"]
    assert all(str(r.expiration_date_gte) == "2026-10-01" for r in contracts)
    # adjusted-root contract (1AAA...) never reaches the bars request
    assert _ActHistorical.batches == [["AAA261002C00010000", "AAA261120C00010000"]]


def test_activity_batches_bar_requests(monkeypatch):
    _activity(monkeypatch, _activity_config(symbols=["AAA"], max_symbols_per_request=1),
              ["chain_volume"])
    assert len(_ActHistorical.batches) == 2


def test_activity_refuses_a_short_calendar(monkeypatch):
    with pytest.raises(AssetError, match="calendar"):
        _activity(monkeypatch, _activity_config(sessions=9), ["chain_volume"])


def test_activity_is_default_deny():
    connector = alpaca.AlpacaOptionActivityConnector()
    for bad in ({"sessions": 0}, {"sessions": 2, "typo": 1},
                {"sessions": 2, "symbols": []},
                {"sessions": 2, "exclude_name_regex": "("},
                {"sessions": 2, "end": "x"}):
        with pytest.raises(AssetError):
            connector.discover(bad)
    streams = connector.discover({"sessions": 2})
    assert [s["stream"] for s in streams] == ["underlyings", "chain_volume"]


def test_activity_accepts_notes():
    alpaca.AlpacaOptionActivityConnector().discover({"sessions": 2, "notes": "why"})


def _symbols_file(tmp_path, tickers=("AAA", "BBB")):
    import hashlib
    import json
    path = tmp_path / "u.json"
    path.write_text(json.dumps({"tickers": list(tickers)}))
    return path, hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.mark.parametrize("connector,base", [
    (alpaca.AlpacaOptionFetchConnector, {"start": "2024-02-01"}),
    (alpaca.AlpacaOptionActivityConnector, {"sessions": 2})])
def test_symbols_file_needs_its_hash_and_refuses_drift(tmp_path, connector, base):
    path, digest = _symbols_file(tmp_path)
    ok = {**base, "symbols_file": str(path), "symbols_key": "tickers",
          "symbols_sha256": digest}
    assert connector().resolve_knobs(ok)["symbols"] == ["AAA", "BBB"]
    without = {k: v for k, v in ok.items() if k != "symbols_sha256"}
    with pytest.raises(AssetError, match="symbols_sha256"):
        connector().resolve_knobs(without)
    path.write_text('{"tickers": ["AAA", "CCC"]}')
    with pytest.raises(AssetError, match="sha256"):
        connector().resolve_knobs(ok)


def test_symbols_sha256_without_a_file_refuses():
    with pytest.raises(AssetError, match="symbols_sha256"):
        alpaca.AlpacaOptionFetchConnector().resolve_knobs(
            {"symbols": ["A"], "symbols_sha256": "0" * 64, "start": "2024-02-01"})


def test_symbols_file_must_be_anchored_not_cwd_relative(tmp_path, monkeypatch):
    path, digest = _symbols_file(tmp_path)
    monkeypatch.setenv("UNIVERSE_ROOT", str(tmp_path))
    knobs = alpaca.AlpacaOptionFetchConnector().resolve_knobs({
        "symbols_file": "$UNIVERSE_ROOT/u.json", "symbols_key": "tickers",
        "symbols_sha256": digest, "start": "2024-02-01"})
    assert knobs["symbols"] == ["AAA", "BBB"]
    monkeypatch.chdir(tmp_path)
    with pytest.raises(AssetError, match="absolute"):
        alpaca.AlpacaOptionFetchConnector().resolve_knobs({
            "symbols_file": "u.json", "symbols_key": "tickers",
            "symbols_sha256": digest, "start": "2024-02-01"})


@pytest.mark.parametrize("extra", [
    {"symbols": ["A"], "symbols_file": "/x", "symbols_key": "k"},
    {"symbols_file": "/x"},
    {"symbols_file": "/nonexistent/u.json", "symbols_key": "k", "symbols_sha256": "0" * 64},
])
def test_option_fetch_symbol_source_is_exactly_one_and_readable(extra):
    with pytest.raises(AssetError):
        alpaca.AlpacaOptionFetchConnector().resolve_knobs({"start": "2024-02-01", **extra})


def test_option_fetch_file_without_the_key_refuses(tmp_path):
    path, digest = _symbols_file(tmp_path)
    with pytest.raises(AssetError, match="symbols_key"):
        alpaca.AlpacaOptionFetchConnector().resolve_knobs({
            "symbols_file": str(path), "symbols_key": "nope",
            "symbols_sha256": digest, "start": "2024-02-01"})
