"""The shipped Kalshi crypto sources and suites against a scripted venue.

The ``kalshi`` pack's one HTTP seam (``getter``) is a fake serving the four
endpoints the pack walks, in the payload shapes probed from the public API on
2026-10-06 (dollar strings, ``finalized`` / ``active`` statuses, no
``series_ticker`` on a market). Each shipped suite then runs over a real
snapshot, so a rule naming a field the pack does not emit fails HERE.
"""

import json
import os
from datetime import datetime, timezone

import pytest

from dskit.onboarding import OnboardingRoot, load_suite, run_acquisition, run_suite
from dskit.onboarding.libs.kalshi import KalshiConnector

CHILD_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIGS = os.path.join(CHILD_ROOT, "configs")
#: Strictly after every fixture instant and before the real clock.
NOW = datetime(2026, 10, 6, 0, 30, tzinfo=timezone.utc)
PAGE = 2  # the fake serves two markets a page, so the cursor walk is exercised

HOURLY = ("KXBTC", "KXBTCD", "KXETH", "KXETHD")
FIFTEEN = ("KXBTC15M", "KXETH15M")


def shipped(name):
    with open(os.path.join(CONFIGS, name), encoding="utf-8") as handle:
        return json.load(handle)


def market(series, event, tail, strike_type, result="yes", status="finalized", **over):
    """One market payload in the shape the venue serves (no ``series_ticker``)."""
    payload = {
        "ticker": f"{event}-{tail}", "event_ticker": event, "strike_type": strike_type,
        "floor_strike": 86000.0, "status": status, "result": result,
        "open_time": "2026-10-05T15:00:00Z", "close_time": "2026-10-05T16:00:00Z",
        "yes_sub_title": "$86,000 or above", "yes_bid_dollars": "0.0000",
        "yes_ask_dollars": "0.0100", "last_price_dollars": "0.0100",
    }
    payload.update(over)
    return payload


def settled(series):
    event = f"{series}-26OCT0512" if series in HOURLY else f"{series}-26OCT051200"
    if series in ("KXBTC", "KXETH"):
        return [
            market(series, event, "T85000", "less", "no", floor_strike=None, cap_strike=85000.0),
            market(series, event, "B86125", "between", "yes", cap_strike=86249.99),
            market(series, event, "T87000", "greater", "no", floor_strike=87000.0),
        ]
    if series in HOURLY:
        return [market(series, event, f"T{k}", "greater", r, floor_strike=float(k))
                for k, r in ((85999.99, "yes"), (86499.99, "no"), (86999.99, "no"))]
    return [market(series, event, "00", "greater_or_equal", "yes",
                   open_time="2026-10-05T15:45:00Z", close_time="2026-10-05T16:00:00Z")]


def candle(end_ts, price=0.4):
    return {"end_period_ts": end_ts, "volume_fp": "10.50", "open_interest_fp": "99.00",
            "price": {"open_dollars": f"{price:.4f}", "high_dollars": f"{price:.4f}",
                      "low_dollars": f"{price:.4f}", "close_dollars": f"{price:.4f}",
                      "mean_dollars": f"{price:.4f}"},
            "yes_bid": {"close_dollars": "0.3900"}, "yes_ask": {"close_dollars": "0.4100"}}


class FakeKalshi:
    """A scripted ``getter(url, params) -> dict`` over the pack's four endpoints."""

    def __init__(self, fee_type="quadratic", open_markets=(), fee_multiplier=1, candle_price=0.4):
        self.fee_type = fee_type
        self.fee_multiplier = fee_multiplier
        self.candle_price = candle_price
        self.open_markets = list(open_markets)
        self.calls = []

    def listing(self, series, status):
        if status == "settled":
            return settled(series)
        return [m for m in self.open_markets if m["ticker"].startswith(series + "-")]

    def __call__(self, url, params):
        path = url.split("/trade-api/v2", 1)[1]
        self.calls.append((path, dict(params)))
        if path == "/markets":
            rows = self.listing(params["series_ticker"], params["status"])
            start = int(params.get("cursor") or 0)
            following = start + PAGE
            return {"markets": rows[start:following],
                    "cursor": str(following) if following < len(rows) else ""}
        if path.endswith("/candlesticks"):
            first = int(params["start_ts"]) + 60
            return {"candlesticks": [candle(first, self.candle_price),
                                     {"end_period_ts": first + 60, "price": {}},
                                     candle(first + 120, self.candle_price)]}
        if path.endswith("/orderbook"):
            return {"orderbook_fp": {"yes_dollars": [["0.4100", "50.00"], ["0.4000", "20.00"]],
                                     "no_dollars": [["0.5800", "30.00"]]}}
        if path.startswith("/series/"):
            return {"series": {"ticker": path.rsplit("/", 1)[-1], "fee_type": self.fee_type,
                               "fee_multiplier": self.fee_multiplier, "title": "t", "category": "Crypto",
                               "frequency": "hourly"}}
        raise AssertionError(f"the pack walked an endpoint the venue fixture lacks: {path}")

    def paths(self, suffix):
        return [p for p, _ in self.calls if p.endswith(suffix)]


def acquire(tmp_path, monkeypatch, config_name, stream, api):
    """Acquire ``stream`` from the shipped source through the real platform; no network."""
    connector = KalshiConnector(getter=api, sleeper=lambda seconds: None, clock=lambda: NOW)
    monkeypatch.setattr("dskit.onboarding.acquire.resolve_connector",
                        lambda ref: (lambda: connector))
    root = OnboardingRoot.create(str(tmp_path / "ob"))
    registry = root.registry()
    vid = registry.register("source_config", {
        "name": "src", "catalog_source": "src", "connector": "kalshi",
        "config": shipped(config_name),
    }, origin="test")
    registry.transition(vid, "active", origin="test")
    out = run_acquisition(root, registry, "src", stream, "backfill")
    return root, registry, out


def verdict(root, registry, suite_name, out):
    suite = load_suite(os.path.join(CONFIGS, suite_name))
    return run_suite(root, registry, suite, out["snapshot"])


def test_markets_pull_settled_history_for_all_six_series_and_the_suite_passes(
        tmp_path, monkeypatch):
    api = FakeKalshi()
    root, registry, out = acquire(tmp_path, monkeypatch, "source-kalshi-crypto.json",
                                  "markets", api)
    assert out["records"] == 3 * 4 + 1 * 2
    statuses = {params["status"] for path, params in api.calls if path == "/markets"}
    assert statuses == {"settled"}, "history is settled markets; open ones are the books' job"
    result = verdict(root, registry, "suite-kalshi-crypto-markets.json", out)
    assert result["gating"] == "pass", result["statistics"]


@pytest.mark.parametrize("bad, rule", [
    ({"result": ""}, "markets-result-vocabulary"),
    ({"strike_type": "mystery"}, "markets-strike-type"),
])
def test_markets_suite_blocks_an_unsettled_or_unknown_geometry_row(
        tmp_path, monkeypatch, bad, rule):
    api = FakeKalshi()
    original = api.listing

    def listing(series, status):
        rows = original(series, status)
        if series == "KXBTCD":
            rows[0] = {**rows[0], **bad}
        return rows

    api.listing = listing
    root, registry, out = acquire(tmp_path, monkeypatch, "source-kalshi-crypto.json",
                                  "markets", api)
    result = verdict(root, registry, "suite-kalshi-crypto-markets.json", out)
    assert result["gating"] == "block"
    assert rule in {r["id"] for r in result["statistics"]["results"] if r["tripped"]}


@pytest.mark.parametrize("config, series", [
    ("source-kalshi-crypto-candles-btc.json", "KXBTC15M"),
    ("source-kalshi-crypto-candles-eth.json", "KXETH15M"),
])
def test_candles_are_one_minute_one_fifteen_minute_series_per_source(
        tmp_path, monkeypatch, config, series):
    api = FakeKalshi()
    root, registry, out = acquire(tmp_path, monkeypatch, config, "candles", api)
    assert out["records"] == 3, "three fake candles for the one settled 15m market"
    assert {p.split("/")[2] for p in api.paths("/candlesticks")} == {series}
    assert {params["period_interval"] for path, params in api.calls
            if path.endswith("/candlesticks")} == {1}
    result = verdict(root, registry, "suite-kalshi-crypto-candles.json", out)
    assert result["gating"] == "pass", result["statistics"]


def test_candles_suite_blocks_a_price_outside_one_dollar(tmp_path, monkeypatch):
    root, registry, out = acquire(tmp_path, monkeypatch, "source-kalshi-crypto-candles-btc.json",
                                  "candles", FakeKalshi(candle_price=1.5))
    result = verdict(root, registry, "suite-kalshi-crypto-candles.json", out)
    assert result["gating"] == "block"
    assert "candles-close-in-dollars" in {r["id"] for r in result["statistics"]["results"]
                                          if r["tripped"]}


def test_fee_schedules_one_row_per_series_and_a_maker_fee_type_warns(tmp_path, monkeypatch):
    root, registry, out = acquire(tmp_path, monkeypatch, "source-kalshi-crypto.json",
                                  "fee_schedules", FakeKalshi())
    assert out["records"] == 6
    assert verdict(root, registry, "suite-kalshi-crypto-fees.json", out)["gating"] == "pass"

    root2 = tmp_path / "second"
    root2.mkdir()
    root, registry, out = acquire(root2, monkeypatch, "source-kalshi-crypto.json",
                                  "fee_schedules", FakeKalshi(fee_type="quadratic_with_maker_fees"))
    result = verdict(root, registry, "suite-kalshi-crypto-fees.json", out)
    assert result["gating"] == "warn", "a maker fee changes the quoting economics; it must be seen"


def test_fee_suite_blocks_a_negative_multiplier(tmp_path, monkeypatch):
    root, registry, out = acquire(tmp_path, monkeypatch, "source-kalshi-crypto.json",
                                  "fee_schedules", FakeKalshi(fee_multiplier=-1))
    result = verdict(root, registry, "suite-kalshi-crypto-fees.json", out)
    assert result["gating"] == "block"
    assert "fees-multiplier-nonnegative" in {r["id"] for r in result["statistics"]["results"]
                                             if r["tripped"]}


def open_book(series, event, tail, strike_type="greater_or_equal", **over):
    return market(series, event, tail, strike_type, "", status="active",
                  close_time="2026-10-06T01:00:00Z", **over)


LIVE = [open_book("KXBTC15M", "KXBTC15M-26OCT060100", "00"),
        open_book("KXETH15M", "KXETH15M-26OCT060100", "00"),
        open_book("KXBTCD", "KXBTCD-26OCT0601", "T86000", "greater"),
        open_book("KXETHD", "KXETHD-26OCT0601", "T4000", "greater")]


@pytest.mark.parametrize("config, series", [
    ("source-kalshi-crypto-books-15m.json", {"KXBTC15M", "KXETH15M"}),
    ("source-kalshi-crypto-books-hourly.json", {"KXBTC", "KXBTCD", "KXETH", "KXETHD"}),
])
def test_each_books_source_records_only_its_own_series_and_the_suite_passes(
        tmp_path, monkeypatch, config, series):
    api = FakeKalshi(open_markets=LIVE)
    root, registry, out = acquire(tmp_path, monkeypatch, config, "orderbooks", api)
    wanted = [m["ticker"] for m in LIVE if m["ticker"].split("-")[0] in series]
    assert out["records"] == len(wanted) == len(api.paths("/orderbook"))
    assert {p.split("/")[2] for p in api.paths("/orderbook")} == set(wanted)
    result = verdict(root, registry, "suite-kalshi-crypto-books.json", out)
    assert result["gating"] == "pass", result["statistics"]


def test_books_suite_blocks_an_unknown_geometry_and_warns_on_an_unset_one(tmp_path, monkeypatch):
    bad = [open_book("KXBTC15M", "KXBTC15M-26OCT060100", "00", "mystery")]
    root, registry, out = acquire(tmp_path, monkeypatch, "source-kalshi-crypto-books-15m.json",
                                  "orderbooks", FakeKalshi(open_markets=bad))
    result = verdict(root, registry, "suite-kalshi-crypto-books.json", out)
    assert result["gating"] == "block"
    assert "books-strike-type" in {r["id"] for r in result["statistics"]["results"] if r["tripped"]}

    # A 15-minute market lists before its target price is set: strike_type is empty.
    tbd = [open_book("KXBTC15M", "KXBTC15M-26OCT060100", "00", None, floor_strike=None,
                     yes_sub_title="Target price: TBD")]
    (tmp_path / "second").mkdir()
    root, registry, out = acquire(tmp_path / "second", monkeypatch,
                                  "source-kalshi-crypto-books-15m.json", "orderbooks",
                                  FakeKalshi(open_markets=tbd))
    result = verdict(root, registry, "suite-kalshi-crypto-books.json", out)
    assert result["gating"] == "warn"
    assert {r["id"] for r in result["statistics"]["results"] if r["tripped"]} == {"books-strike-type-set"}


def test_settled_markets_whose_strike_was_never_set_warn_but_do_not_block(tmp_path, monkeypatch):
    """Real 2026-08 rows: finalized, result 'no', strike_type absent, 'Target price: TBD'."""
    api = FakeKalshi()
    original = api.listing

    def listing(series, status):
        rows = original(series, status)
        if series == "KXBTC15M":
            rows = rows + [market(series, "KXBTC15M-26AUG080215", "15", None, "no",
                                  floor_strike=None, yes_sub_title="Target price: TBD")]
        return rows

    api.listing = listing
    root, registry, out = acquire(tmp_path, monkeypatch, "source-kalshi-crypto.json", "markets", api)
    result = verdict(root, registry, "suite-kalshi-crypto-markets.json", out)
    assert result["gating"] == "warn", result["statistics"]
    assert {r["id"] for r in result["statistics"]["results"] if r["tripped"]} == {"markets-strike-type-set"}
