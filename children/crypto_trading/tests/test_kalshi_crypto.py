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

    def __init__(self, fee_type="quadratic", open_markets=()):
        self.fee_type = fee_type
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
            return {"candlesticks": [candle(first), {"end_period_ts": first + 60, "price": {}},
                                     candle(first + 120, 0.45)]}
        if path.endswith("/orderbook"):
            return {"orderbook_fp": {"yes_dollars": [["0.4100", "50.00"], ["0.4000", "20.00"]],
                                     "no_dollars": [["0.5800", "30.00"]]}}
        if path.startswith("/series/"):
            return {"series": {"ticker": path.rsplit("/", 1)[-1], "fee_type": self.fee_type,
                               "fee_multiplier": 1, "title": "t", "category": "Crypto",
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
    assert {r["id"] for r in result["statistics"]["results"] if r["tripped"]} == {rule}


def test_candles_are_one_minute_and_for_the_fifteen_minute_series_only(tmp_path, monkeypatch):
    api = FakeKalshi()
    root, registry, out = acquire(tmp_path, monkeypatch, "source-kalshi-crypto-candles.json",
                                  "candles", api)
    assert out["records"] == 3 * 2, "three fake candles for each of the two settled 15m markets"
    series_walked = {p.split("/")[2] for p in api.paths("/candlesticks")}
    assert series_walked == set(FIFTEEN)
    assert {params["period_interval"] for path, params in api.calls
            if path.endswith("/candlesticks")} == {1}
    result = verdict(root, registry, "suite-kalshi-crypto-candles.json", out)
    assert result["gating"] == "pass", result["statistics"]


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


def test_orderbooks_record_one_book_per_open_market_and_the_suite_passes(tmp_path, monkeypatch):
    live = [market("KXBTC15M", "KXBTC15M-26OCT060100", "00", "greater_or_equal", "",
                   status="active", close_time="2026-10-06T01:00:00Z"),
            market("KXETHD", "KXETHD-26OCT0601", "T4000", "greater", "", status="active",
                   close_time="2026-10-06T01:00:00Z")]
    api = FakeKalshi(open_markets=live)
    root, registry, out = acquire(tmp_path, monkeypatch, "source-kalshi-crypto.json",
                                  "orderbooks", api)
    assert out["records"] == 2
    assert len(api.paths("/orderbook")) == 2
    result = verdict(root, registry, "suite-kalshi-crypto-books.json", out)
    assert result["gating"] == "pass", result["statistics"]
