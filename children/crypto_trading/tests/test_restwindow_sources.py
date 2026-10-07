"""The shipped Coinbase and Deribit sources and suites against scripted vendors (ADR-0240).

The restwindow pack's one HTTP seam (``getter``) is a fake that answers the shapes probed from the two public
APIs on 2026-10-07: Coinbase Exchange's candles (a bare list of positional rows, newest first, epoch seconds, both
window bounds inclusive) and Deribit's volatility index (a JSON-RPC envelope, rows at ``result.data``, epoch
milliseconds, a ``continuation`` marker when the vendor cut a range short). Only one knob of each shipped source is
swapped, the span's ``start``, so a pull is a few windows, not thousands; a test pins that the shipped span fits its
``max_windows``. Each shipped suite then runs over a real snapshot.
"""

import copy
import json
import math
import os
from datetime import datetime, timezone

import pytest
from dskit.onboarding import OnboardingRoot, check_config, load_suite, run_acquisition, run_suite, scan_stream
from dskit.onboarding.base import AssetError
from dskit.onboarding.libs.restwindow import RestWindowConnector
from synthetic import shipped

CONFIGS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "configs")
NOW = datetime(2026, 10, 7, 0, 30, tzinfo=timezone.utc)
START = "2026-10-06T12:00:00Z"
COINBASE = ("source-coinbase-btcusd-1m.json", "source-coinbase-ethusd-1m.json")
DERIBIT = ("source-deribit-btc-dvol.json", "source-deribit-eth-dvol.json")


def epoch(text):
    return int(datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp())


def minutes(first, last):
    """The epoch-second starts of the one-minute bars in ``[first, last]`` (both inclusive)."""
    return range(first - first % 60 + (60 if first % 60 else 0), last + 1, 60)


class FakeCoinbase:
    """Bars for every minute from the fixture's first to NOW; a window is both-bounds-inclusive and newest first."""

    def __init__(self, level=60000.0, volume_scale=1.0, product=None):
        self.level, self.volume_scale, self.product, self.calls = level, volume_scale, product, []

    def row(self, t):
        price = self.level + (t // 60) % 97
        return [t, price - 1.0, price + 1.0, price, price + 0.5, 1.0 * self.volume_scale]

    def __call__(self, url, params):
        assert url.startswith("https://api.exchange.coinbase.com/products/") and url.endswith("-USD/candles"), url
        if self.product is not None:
            assert url == f"https://api.exchange.coinbase.com/products/{self.product}/candles", "the asset the source names"
        self.calls.append(dict(params))
        lo, hi = epoch(params["start"]), epoch(params["end"])
        assert (hi - lo) // 60 <= 300, "the venue refuses a span of more than 300 granules (probed: 18000 s answers 301 rows, 18060 s is a 400)"
        return [self.row(t) for t in reversed(minutes(lo, hi)) if t <= int(NOW.timestamp())]


class FakeDeribit:
    def __init__(self, continuation=None, level=35.0):
        self.continuation, self.level, self.calls = continuation, level, []

    def __call__(self, url, params):
        assert url.endswith("/api/v2/public/get_volatility_index_data"), url
        self.calls.append(dict(params))
        lo, hi = params["start_timestamp"] // 1000, params["end_timestamp"] // 1000
        rows = [[t * 1000, self.level, self.level + 0.1, self.level - 0.1, self.level + 0.05] for t in minutes(lo, hi)]
        return {"jsonrpc": "2.0", "result": {"data": rows, "continuation": self.continuation}}


def acquire(tmp_path, monkeypatch, name, stream, api, start=START):
    config = copy.deepcopy(shipped(name))
    config["streams"][stream]["pagination"]["start"] = start
    connector = RestWindowConnector(getter=api, sleeper=lambda seconds: None, clock=lambda: NOW)
    monkeypatch.setattr("dskit.onboarding.acquire.resolve_connector", lambda ref: (lambda: connector))
    root = OnboardingRoot.create(str(tmp_path / "ob"))
    registry = root.registry()
    vid = registry.register("source_config", {
        "name": "src", "catalog_source": "src", "connector": "dskit.onboarding.libs.restwindow:RestWindowConnector",
        "config": config}, origin="test")
    registry.transition(vid, "active", origin="test")
    return root, registry, run_acquisition(root, registry, "src", stream, "backfill")


def verdict(root, registry, suite, out):
    return run_suite(root, registry, load_suite(os.path.join(CONFIGS, suite)), out["snapshot"])


def tripped(result):
    return {r["id"] for r in result["statistics"]["results"] if r["tripped"]}


# -- the shipped declarations ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", COINBASE + DERIBIT)
def test_the_shipped_sources_pass_default_deny_and_describe_one_stream(name):
    config = shipped(name)
    check_config(RestWindowConnector(), config)
    stream = {"source-coinbase-btcusd-1m.json": "candles", "source-coinbase-ethusd-1m.json": "candles",
              "source-deribit-btc-dvol.json": "dvol", "source-deribit-eth-dvol.json": "dvol"}[name]
    assert list(config["streams"]) == [stream]
    declared = RestWindowConnector().discover({k: v for k, v in config.items() if k != "storage"})
    assert [d["stream"] for d in declared] == [stream]


@pytest.mark.parametrize("name", COINBASE + DERIBIT)
def test_the_shipped_span_fits_its_window_budget_with_the_headroom_the_notes_state(name):
    config = shipped(name)
    (declaration,) = config["streams"].values()
    page = declaration["pagination"]
    seconds = (datetime(2026, 10, 7, tzinfo=timezone.utc) - datetime.fromisoformat(page["start"].replace("Z", "+00:00"))).total_seconds()
    windows = math.ceil(seconds / page["step"])
    assert windows < page["max_windows"], "a span that has outgrown max_windows refuses before any request"
    if "coinbase" in name:
        assert page["step"] == 299 * 60, "299 granules: 300 rows with both bounds, one granule under the venue's 300-granule ceiling"
        assert (page["max_windows"] - windows) * page["step"] / 86_400 >= 200, "the notes promise about 235 days of headroom"
        assert "4,860 windows" in config["notes"] and 4860 <= windows <= 4870


@pytest.mark.parametrize("name", COINBASE)
def test_coinbase_is_documented_as_the_live_safe_alternative_to_binance_and_not_wired_in(name):
    notes = shipped(name)["notes"]
    for phrase in ("LIVE-SAFE alternative to Binance", "BRTI", "CC BY-NC-SA", "research use only",
                   "a Coinbase-backed reader is not built", "START of the minute"):
        assert phrase in notes, f"{name}: the notes must say {phrase!r}"


def test_the_shipped_run_documents_still_read_binance_for_spot():
    for name in ("run-features-15m.json", "run-features-hourly.json"):
        spot = shipped(name)["pipeline"]["spot"]["params"]
        for asset in spot["assets"].values():
            assert asset["klines"]["source"].startswith("binance-"), name
            assert asset["bvol"]["source"].startswith("binance-"), name
        assert not [s for s in shipped(name)["pipeline"]["streams"]["params"]["streams"].values()
                    if not s["source"].startswith("binance-")]


@pytest.mark.parametrize("btc, eth", [(COINBASE[0], COINBASE[1]), (DERIBIT[0], DERIBIT[1])])
def test_the_btc_and_eth_twins_differ_only_by_the_asset(btc, eth):
    """B1-03: a copy and paste slip (ETH DVOL pulled as BTC, 5-minute bars under a 1-minute name) must not pass."""
    def swapped(config):
        text = json.dumps({k: v for k, v in config.items() if k != "notes"}, sort_keys=True)
        return text.replace("BTC", "ETH").replace("btc", "eth")
    assert swapped(shipped(btc)) == json.dumps({k: v for k, v in shipped(eth).items() if k != "notes"}, sort_keys=True)
    assert "ETH" in shipped(eth)["notes"] and "BTC" not in shipped(eth)["notes"].replace("the BTC index read", "")


@pytest.mark.parametrize("name", DERIBIT)
def test_deribit_start_lies_inside_the_one_minute_retention_the_vendor_was_probed_to_keep(name):
    """A-R1-02: 1-minute DVOL exists only from 2026-04-04T19:21Z (probed 2026-10-07); an earlier start is windows of nothing that still gate pass."""
    config = shipped(name)
    start = datetime.fromisoformat(config["streams"]["dvol"]["pagination"]["start"].replace("Z", "+00:00"))
    assert start >= datetime(2026, 4, 4, 19, 21, tzinfo=timezone.utc), "no 1-minute row is served before the first one probed"
    assert config["streams"]["dvol"]["params"]["resolution"] == "60"
    for phrase in ("RETENTION", "185 days", "2026-04-04T19:21Z", "resolution 3600", "empty result.data"):
        assert phrase in config["notes"], f"{name}: the notes must say {phrase!r}"
    assert "2,020" not in config["notes"], "the old window count described a span the vendor does not serve"


# -- Coinbase ----------------------------------------------------------------------------------------------


@pytest.mark.parametrize("name, product, level", [("source-coinbase-btcusd-1m.json", "BTC-USD", 60000.0),
                                                  ("source-coinbase-ethusd-1m.json", "ETH-USD", 3000.0)])
def test_coinbase_candles_are_pulled_in_windows_stored_once_and_converted_to_iso(tmp_path, monkeypatch, name, product, level):
    """B1-03: both twins are pinned alike (the product the URL names, a granule of 60 s), not only the BTC one."""
    api = FakeCoinbase(level=level, product=product)
    root, registry, out = acquire(tmp_path, monkeypatch, name, "candles", api)
    first, last = epoch(START), int(NOW.timestamp()) - 60  # lag 60: the minute still forming is never stored
    expected = [t for t in minutes(first, last) if t < last]
    assert out["records"] == len(expected), "a bound both windows return is stored once"
    assert len(api.calls) == math.ceil((last - first) / 17940) + 1, "the windows, and the platform's check() probes the first once"
    assert api.calls[0] == api.calls[1]
    assert all(c["granularity"] == 60 for c in api.calls)
    rows = list(scan_stream(root.root, "src", "candles", key_fields=["time"]))
    assert sorted(r["time"] for r in rows) == expected
    sample = rows[0]
    assert set(sample) >= {"time", "time_iso", "low", "high", "open", "close", "volume"}
    assert sample["time_iso"] == datetime.fromtimestamp(sample["time"], tz=timezone.utc).isoformat()
    assert verdict(root, registry, "suite-coinbase-candles.json", out)["gating"] == "pass"


def test_the_candle_that_is_still_forming_is_not_stored(tmp_path, monkeypatch):
    root, _, _ = acquire(tmp_path, monkeypatch, "source-coinbase-ethusd-1m.json", "candles", FakeCoinbase(level=3000.0, product="ETH-USD"))
    newest = max(r["time"] for r in scan_stream(root.root, "src", "candles", key_fields=["time"]))
    assert newest + 60 <= int(NOW.timestamp()) - 60, "the newest stored bar had ended a full minute before now"


def test_the_coinbase_suite_blocks_a_negative_price(tmp_path, monkeypatch):
    class Bad(FakeCoinbase):
        def row(self, t):
            row = super().row(t)
            return [*row[:4], -1.0, row[5]] if t == epoch(START) + 660 else row

    root, registry, out = acquire(tmp_path, monkeypatch, "source-coinbase-btcusd-1m.json", "candles", Bad())
    result = verdict(root, registry, "suite-coinbase-candles.json", out)
    assert result["gating"] == "block" and tripped(result) == {"candles-close-nonnegative"}


def test_the_connector_refuses_a_millisecond_time_before_the_suite_is_asked(tmp_path, monkeypatch):
    class Slip(FakeCoinbase):
        def row(self, t):
            row = super().row(t)
            return [t * 1000, *row[1:]]

    with pytest.raises(AssetError, match="epoch"):
        acquire(tmp_path, monkeypatch, "source-coinbase-btcusd-1m.json", "candles", Slip())


# -- Deribit -----------------------------------------------------------------------------------------------


@pytest.mark.parametrize("name, currency", [("source-deribit-btc-dvol.json", "BTC"), ("source-deribit-eth-dvol.json", "ETH")])
def test_deribit_dvol_is_pulled_in_windows_with_rows_at_result_data_and_the_suite_passes(tmp_path, monkeypatch, name, currency):
    """B1-03: both twins are pinned alike (the currency asked for, a resolution of 60 s), not only the BTC one."""
    api = FakeDeribit()
    root, registry, out = acquire(tmp_path, monkeypatch, name, "dvol", api)
    first, last = epoch(START), int(NOW.timestamp()) - 60
    expected = [t * 1000 for t in minutes(first, last) if t < last]
    assert out["records"] == len(expected)
    assert all(c["currency"] == currency and c["resolution"] == "60" for c in api.calls)
    assert {"start_timestamp", "end_timestamp"} <= set(api.calls[0]), "epoch_ms bounds, the way the vendor reads them"
    rows = list(scan_stream(root.root, "src", "dvol", key_fields=["ts"]))
    assert sorted(r["ts"] for r in rows) == expected and rows[0]["close"] == pytest.approx(35.05)
    assert verdict(root, registry, "suite-deribit-dvol.json", out)["gating"] == "pass"


def test_a_window_the_vendor_cut_short_is_refused_not_stored_partial(tmp_path, monkeypatch):
    with pytest.raises(AssetError, match="continuation|truncat|cut"):
        acquire(tmp_path, monkeypatch, "source-deribit-eth-dvol.json", "dvol", FakeDeribit(continuation="more"))


def test_the_dvol_suite_warns_on_a_fraction_and_blocks_a_negative_level(tmp_path, monkeypatch):
    root, registry, out = acquire(tmp_path / "a", monkeypatch, "source-deribit-btc-dvol.json", "dvol", FakeDeribit(level=0.35))
    result = verdict(root, registry, "suite-deribit-dvol.json", out)
    assert result["gating"] == "warn" and tripped(result) == {"dvol-close-in-percentage-points"}
    root, registry, out = acquire(tmp_path / "b", monkeypatch, "source-deribit-btc-dvol.json", "dvol", FakeDeribit(level=-4.0))
    assert verdict(root, registry, "suite-deribit-dvol.json", out)["gating"] == "block"
