"""The shipped kalshi_history sources and suites against a scripted venue (ADR-0236).

The history pack's one HTTP seam (``getter``) is a fake serving the endpoints it walks (the cutoff, the live
and archived market listings, event-level and archived candlesticks, trades), in the payload shapes probed from
the public API on 2026-10-07. Each shipped suite then runs over a real snapshot, so a rule naming a field the
pack does not emit fails HERE, and a deliberately bad row proves each new rule can trip.
"""

import os
from datetime import timedelta

import pytest
from dskit.onboarding import load_suite, run_suite, scan_stream
from synthetic import (
    NOW, ScriptedHistory, Store, archive_candle_payload, candle_payload, history_market_payload, iso, ms, utc,
)

CONFIGS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "configs")
CUTOFF = utc(2026, 9, 2, 12, 0)
OLD = utc(2026, 9, 1, 2, 0)  # closes before the cutoff: the archive owns it
NEW = utc(2026, 9, 3, 2, 0)  # closes after it: the live API owns it
SIX = ("KXBTC", "KXBTCD", "KXBTC15M", "KXETH", "KXETHD", "KXETH15M")
HOURLY_BTC = ("KXBTC", "KXBTCD")


def event_of(series, close):
    """The venue's event spelling: the series, the close as YYMONDDHH (hourly) or YYMONDDHHMM (15-minute)."""
    stamp = f"{close:%y%b%d%H%M}" if series.endswith("15M") else f"{close:%y%b%d%H}"
    return f"{series}-{stamp}".upper()


def market(series, close, strike=60000.0, value=60250.0, **over):
    event = event_of(series, close)
    minutes = 15 if series.endswith("15M") else 60
    kind = "greater_or_equal" if minutes == 15 else "greater"
    payload = history_market_payload(f"{event}-T{strike:.0f}", close, expiration_value=value, minutes_open=minutes,
                                     strike_type=kind, floor=strike, result="yes" if value >= strike else "no", **over)
    payload["event_ticker"] = event
    return payload


def world(**over):
    """One archived and one live market per series, the archived one still listed live too (the pack drops that copy)."""
    old = [market(s, OLD) for s in SIX]
    new = [market(s, NEW) for s in SIX]
    return ScriptedHistory(live=new, archived=old, also_live=old[:1], **over)


def verdict(store, suite, out):
    return run_suite(store.onboarding, store.registry, load_suite(os.path.join(CONFIGS, suite)), out["snapshot"])


def tripped(result):
    return {r["id"]: r["severity"] for r in result["statistics"]["results"] if r["tripped"]}


def pull(tmp_path, monkeypatch, source, config, stream, api):
    store = Store(tmp_path, monkeypatch)
    return store, store.kalshi_history(source, config, api, [stream])[stream]


# -- markets: the realised settlement value ------------------------------------------------------------


def test_the_markets_pull_reads_both_archives_once_each_and_the_suite_passes(tmp_path, monkeypatch):
    api = world()
    store, out = pull(tmp_path, monkeypatch, "kalshi-history-crypto", "source-kalshi-history-crypto.json", "markets", api)
    assert out["records"] == 12, "the live copy of an archived market is dropped: 6 archived + 6 live"
    assert len(api.paths("/historical/cutoff")) == 2, "served, never typed: read once by the platform's check, once by the pull"
    assert {p["status"] for path, p in api.calls if path == "/markets"} == {"settled"}
    assert len(api.paths("/historical/markets")) == 6, "one archive walk per series"
    assert tripped(verdict(store, "suite-kalshi-history-markets.json", out)) == {}
    assert verdict(store, "suite-kalshi-history-markets.json", out)["gating"] == "pass"


def test_every_row_carries_the_value_the_settlement_instant_and_the_volume(tmp_path, monkeypatch):
    store, _ = pull(tmp_path, monkeypatch, "kalshi-history-crypto", "source-kalshi-history-crypto.json", "markets", world())
    rows = list(scan_stream(store.path, "kalshi-history-crypto", "markets", key_fields=["ticker"]))
    assert len(rows) == 12
    for data in rows:
        assert data["expiration_value"] == 60250.0 and isinstance(data["volume"], float)
        assert data["settlement_ts"] > data["close_time"], "the label exists only after the close"
        assert data["result"] in ("yes", "no")


def test_the_suite_warns_on_a_missing_value_and_blocks_on_an_impossible_one(tmp_path, monkeypatch):
    missing = market("KXBTCD", NEW, strike=61000.0)
    del missing["expiration_value"]
    store, out = pull(tmp_path / "a", monkeypatch, "kalshi-history-crypto", "source-kalshi-history-crypto.json", "markets",
                      ScriptedHistory(live=[*(market(s, NEW) for s in SIX), missing]))
    result = verdict(store, "suite-kalshi-history-markets.json", out)
    assert result["gating"] == "warn" and tripped(result) == {"markets-expiration-value-present": "warn"}

    negative = market("KXBTCD", NEW, strike=61000.0, value=-5.0)
    store, out = pull(tmp_path / "b", monkeypatch, "kalshi-history-crypto", "source-kalshi-history-crypto.json", "markets",
                      ScriptedHistory(live=[*(market(s, NEW) for s in SIX), negative]))
    result = verdict(store, "suite-kalshi-history-markets.json", out)
    assert result["gating"] == "block" and "markets-expiration-value-nonnegative" in tripped(result)


def test_a_negative_volume_blocks_and_an_unexpected_status_warns(tmp_path, monkeypatch):
    bad = market("KXBTCD", NEW, strike=61000.0, volume="-1.00")
    store, out = pull(tmp_path / "a", monkeypatch, "kalshi-history-crypto", "source-kalshi-history-crypto.json", "markets",
                      ScriptedHistory(live=[*(market(s, NEW) for s in SIX), bad]))
    result = verdict(store, "suite-kalshi-history-markets.json", out)
    assert result["gating"] == "block" and "markets-volume-nonnegative" in tripped(result)

    odd = market("KXETHD", OLD, strike=3000.0, value=3100.0)
    odd["status"] = "closed"  # a market the archive lists whose status is not a final one
    store, out = pull(tmp_path / "b", monkeypatch, "kalshi-history-crypto", "source-kalshi-history-crypto.json", "markets",
                      ScriptedHistory(live=[market(s, NEW) for s in SIX], archived=[odd]))
    result = verdict(store, "suite-kalshi-history-markets.json", out)
    assert result["gating"] == "warn" and tripped(result) == {"markets-status-settled": "warn"}


# -- candles: event level for live markets, one by one for archived ones ------------------------------------------


def live_candles(close, minutes=60, bid=0.40):
    opened = close - timedelta(minutes=minutes)
    return [candle_payload(opened + timedelta(minutes=k), bid=bid, ask=bid + 0.04, price=bid + 0.02) for k in range(1, minutes + 1)]


def archived_candles(close, minutes=60, bid=0.40):
    opened = close - timedelta(minutes=minutes)
    return [archive_candle_payload(opened + timedelta(minutes=k), bid=bid, ask=bid + 0.04, price=bid + 0.02)
            for k in range(1, minutes + 1)]


def candle_world(**kw):
    old = [market(s, OLD) for s in HOURLY_BTC]
    new = [market(s, NEW) for s in HOURLY_BTC]
    return ScriptedHistory(
        live=new, archived=old,
        candles={m["ticker"]: live_candles(NEW) for m in new},
        archived_candles={m["ticker"]: archived_candles(OLD, **kw) for m in old})


def test_hourly_candles_are_pulled_per_event_when_live_and_one_by_one_from_the_archive(tmp_path, monkeypatch):
    api = candle_world()
    store, out = pull(tmp_path, monkeypatch, "kalshi-history-candles-hourly-btc",
                      "source-kalshi-history-candles-hourly-btc.json", "candles", api)
    assert out["records"] == 4 * 60
    live = [(p, q) for p, q in api.calls if p.startswith("/series/")]
    assert [p for p, _ in live] == [f"/series/{s}/events/{event_of(s, NEW)}/candlesticks" for s in HOURLY_BTC], (
        "one request chain per live EVENT, every strike at once")
    assert all(q["period_interval"] == 1 for _, q in live), "1-minute candles are the modelling resolution"
    archive = [(p, q) for p, q in api.calls if p.startswith("/historical/markets/")]
    assert len(archive) == 2 and all(q["period_interval"] == 1 for _, q in archive), "an archived market is asked alone"
    assert verdict(store, "suite-kalshi-history-candles.json", out)["gating"] == "pass"


def test_archived_and_live_candles_land_in_one_row_shape(tmp_path, monkeypatch):
    store, _ = pull(tmp_path, monkeypatch, "kalshi-history-candles-hourly-btc",
                    "source-kalshi-history-candles-hourly-btc.json", "candles", candle_world())
    rows = list(scan_stream(store.path, "kalshi-history-candles-hourly-btc", "candles", key_fields=["ticker", "ts"]))
    assert len({tuple(sorted(r)) for r in rows}) == 1
    old = [r for r in rows if r["ticker"].startswith(event_of("KXBTCD", OLD))]
    assert old and old[0]["yes_bid_close"] == 0.40 and old[0]["yes_ask_close"] == pytest.approx(0.44)
    assert all(isinstance(r["ts"], int) for r in rows), "epoch seconds, as the reader expects"


def test_the_candle_suite_trips_on_a_price_outside_a_dollar_and_a_negative_open_interest(tmp_path, monkeypatch):
    new = [market(s, NEW) for s in HOURLY_BTC]
    bad = live_candles(NEW)
    bad[3] = candle_payload(NEW - timedelta(minutes=56), bid=-0.1, ask=1.4, price=0.5)
    bad[4] = {**live_candles(NEW)[4], "open_interest_fp": "-3.00"}
    api = ScriptedHistory(live=new, candles={new[0]["ticker"]: bad, new[1]["ticker"]: live_candles(NEW)})
    store, out = pull(tmp_path, monkeypatch, "kalshi-history-candles-hourly-btc",
                      "source-kalshi-history-candles-hourly-btc.json", "candles", api)
    result = verdict(store, "suite-kalshi-history-candles.json", out)
    assert result["gating"] == "block"
    assert {"candles-yes-bid-close-in-dollars", "candles-yes-ask-close-in-dollars",
            "candles-open-interest-nonnegative"} <= set(tripped(result))


# -- trades --------------------------------------------------------------------------------------------------------


def trade(trade_id, ticker, created, taker="yes", price="0.4600", count="16.04"):
    return {"trade_id": trade_id, "ticker": ticker, "created_time": iso(created), "yes_price_dollars": price,
            "no_price_dollars": "0.5400", "count_fp": count, "taker_side": taker}


def old_ticker():
    """The ticker of the archived KXBTC15M market trade_world scripts."""
    return market("KXBTC15M", OLD, strike=60000.0)["ticker"]


def trade_world(extra=()):
    old = [market(s, OLD, strike=60000.0) for s in ("KXBTC15M", "KXETH15M")]
    new = [market(s, NEW, strike=60000.0) for s in ("KXBTC15M", "KXETH15M")]
    trades = {m["ticker"]: [trade(f"{m['ticker']}-{k}", m["ticker"], OLD + timedelta(minutes=k) if m in old else NEW + timedelta(minutes=k - 10))
                            for k in range(1, 4)] for m in old + new}
    for item in extra:
        trades[old[0]["ticker"]].append(item)
    return ScriptedHistory(live=new, archived=old, trades=trades), old, new


def test_trades_are_routed_to_the_archive_that_owns_each_period_and_the_suite_passes(tmp_path, monkeypatch):
    api, old, new = trade_world()
    store, out = pull(tmp_path, monkeypatch, "kalshi-history-trades-15m", "source-kalshi-history-trades-15m.json", "trades", api)
    assert out["records"] == 12
    cutoff_s = int(CUTOFF.timestamp())
    assert sorted(p["ticker"] for p in (q for pth, q in api.calls if pth == "/historical/trades")) == sorted(m["ticker"] for m in old)
    assert sorted(p["ticker"] for p in (q for pth, q in api.calls if pth == "/markets/trades")) == sorted(m["ticker"] for m in new)
    assert all(q["max_ts"] == cutoff_s for pth, q in api.calls if pth == "/historical/trades")
    assert verdict(store, "suite-kalshi-history-trades.json", out)["gating"] == "pass"


def test_the_trade_suite_warns_on_an_unknown_taker_side_and_a_unit_slip_price(tmp_path, monkeypatch):
    api, old, _ = trade_world(extra=[trade("odd-side", old_ticker(), OLD, taker="maybe"),
                                     trade("odd-price", old_ticker(), OLD, price="46.0")])
    store, out = pull(tmp_path, monkeypatch, "kalshi-history-trades-15m", "source-kalshi-history-trades-15m.json", "trades", api)
    result = verdict(store, "suite-kalshi-history-trades.json", out)
    assert result["gating"] == "warn"
    assert tripped(result) == {"trades-taker-side": "warn", "trades-yes-price-present": "warn"}


def test_a_trade_after_the_pulls_capture_instant_is_not_emitted(tmp_path, monkeypatch):
    api, old, _ = trade_world(extra=[trade("future", old_ticker(), NOW + timedelta(hours=2))])
    store, out = pull(tmp_path, monkeypatch, "kalshi-history-trades-15m", "source-kalshi-history-trades-15m.json", "trades", api)
    ids = {r["trade_id"] for r in scan_stream(store.path, "kalshi-history-trades-15m", "trades", key_fields=["trade_id"])}
    assert "future" not in ids and out["records"] == 12, "the next pull asks for it again: nothing is future-dated"


def test_the_fixtures_sit_before_the_clock_every_pull_runs_at():
    assert ms(NEW) + 3_600_000 < ms(NOW), "a candle or trade after the capture would be dropped, hiding a defect"
