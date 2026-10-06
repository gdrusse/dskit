"""The stage B document end to end, over synthetic onboarded stores.

``configs/run-features-15m.json`` is run through the same entry the CLI uses. The only
things the test changes are placement (the store root, the output path) and the held-out
cut, which has to fall inside the synthetic week; a pin asserts nothing else moved. The
feature table it writes is then published through a ``localtables`` registration and read
back, which is the path a later modelling run takes.
"""

import json
import math
import os
import subprocess
import sys
from dataclasses import replace
from datetime import timedelta

import pytest
from dskit.onboarding import check_config, run_acquisition
from dskit.onboarding.libs.localtables import LocalTablesConnector
from dskit.pipeline import OutputsConfig, run_document
from dskit.pipeline.document import load_document
from dskit.pipeline.libs.observations import ObservationRows
from synthetic import (
    Store, ScriptedKalshi, bvol_days, candle_payload, kline_days, market_payload, ms, shipped,
    utc, walk,
)

import crypto_trading  # noqa: F401  (import = registration, as --adapter crypto_trading does)

CHILD_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOC = os.path.join(CHILD_ROOT, "configs", "run-features-15m.json")
SERIES = ("KXBTC", "KXBTCD", "KXBTC15M", "KXETH", "KXETHD", "KXETH15M")
CUT = "2026-09-03T00:00:00Z"
LEADS = (2, 5, 10)

#: (ticker tail, asset series, close instant, strike offset from spot in ln, result)
MARKETS = [
    ("KXBTC15M-26SEP020115-15", "BTC", utc(2026, 9, 2, 1, 15), 0.001, "yes"),
    ("KXBTC15M-26SEP020130-30", "BTC", utc(2026, 9, 2, 1, 30), -0.002, "no"),
    ("KXBTC15M-26SEP030115-15", "BTC", utc(2026, 9, 3, 1, 15), 0.0, "yes"),
    ("KXETH15M-26SEP020115-15", "ETH", utc(2026, 9, 2, 1, 15), 0.002, "no"),
    ("KXETH15M-26SEP030130-30", "ETH", utc(2026, 9, 3, 1, 30), -0.001, "yes"),
]


def doc_dict():
    with open(DOC, encoding="utf-8") as handle:
        return json.load(handle)


def decision_instants():
    return [ms(close - timedelta(minutes=lead)) for _, _, close, _, _ in MARKETS for lead in LEADS]


def build_world(tmp_path, monkeypatch):
    """Markets, candles, fees, klines and BVOL for the five fixture markets."""
    store = Store(tmp_path / "world", monkeypatch)
    start = ms(utc(2026, 9, 1, 16, 0))  # 9 hours of history before the first decision: EWMA needs 300 bars
    bars = {"BTC": walk(start, 35 * 60, price=60000.0, seed=11),
            "ETH": walk(start, 35 * 60, price=3000.0, seed=12)}

    def spot_at(asset, close):
        before = [b for b in bars[asset] if b["close_time_ms"] < ms(close - timedelta(minutes=5))]
        return before[-1]["close"]

    markets, candles = [], {}
    for ticker, asset, close, offset, result in MARKETS:
        floor = round(spot_at(asset, close) * math.exp(offset), 2)
        markets.append(market_payload(ticker, close, floor=floor, result=result))
        candles[ticker] = [candle_payload(close - timedelta(minutes=15 - k), bid=0.40, ask=0.44,
                                          price=0.42) for k in range(1, 16)]
    api = ScriptedKalshi(markets, candles, {s: ("quadratic", 1) for s in SERIES})
    store.kalshi("kalshi-crypto", "source-kalshi-crypto.json", api, ["markets", "fee_schedules"])
    store.kalshi("kalshi-crypto-candles-btc", "source-kalshi-crypto-candles-btc.json", api, ["candles"])
    store.kalshi("kalshi-crypto-candles-eth", "source-kalshi-crypto-candles-eth.json", api, ["candles"])
    bvol = sorted({(d - 1000 * k, 50.0 + k) for d in decision_instants() for k in (0, 1, 2)})
    store.blobs("binance-btcusdt-1m", kline_days(bars["BTC"]))
    store.blobs("binance-ethusdt-1m", kline_days(bars["ETH"]))
    store.blobs("binance-btcbvol", bvol_days(bvol))
    store.blobs("binance-ethbvol", bvol_days(bvol, symbol="ETHBVOLUSDT"))
    store.bars = bars
    store.floors = {m["ticker"]: m["floor_strike"] for m in markets}
    return store


def patched_doc(store, tmp_path):
    """The shipped document with placement and the held-out cut moved, written to tmp."""
    doc = doc_dict()
    for spec in doc["pipeline"].values():
        if "root" in spec.get("params", {}):
            spec["params"]["root"] = store.path
    out = tmp_path / "features"
    out.mkdir()
    doc["pipeline"]["write"]["params"]["path"] = str(out / "decision_features.jsonl")
    segments = doc["pipeline"]["kill_test"]["params"]["segments"]
    segments["development"]["end"] = CUT
    segments["heldout"]["start"] = CUT
    path = tmp_path / "run.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    return path, out


def run(store, tmp_path):
    path, out = patched_doc(store, tmp_path)
    document = replace(load_document(str(path)), outputs=OutputsConfig(run_root=str(tmp_path / "runs")))
    return run_document(document, asof="2026-10-06"), out


@pytest.fixture
def ran(tmp_path, monkeypatch):
    store = build_world(tmp_path, monkeypatch)
    result, out = run(store, tmp_path)
    assert result.state == "ran", (result.state, result.error)
    return store, result, out


def test_the_document_runs_and_every_node_is_ok(ran):
    _, result, _ = ran
    assert set(result.node_states.values()) == {"ok"}
    assert set(result.node_states) == set(doc_dict()["pipeline"])


def test_the_table_has_one_row_per_market_and_lead_with_the_label(ran):
    _, result, _ = ran
    rows = result.outputs["fees"]["records"]
    assert len(rows) == len(MARKETS) * len(LEADS)
    result_by_ticker = {t: r for t, _, _, _, r in MARKETS}
    for row in rows:
        assert row["label"] == (1 if result_by_ticker[row["ticker"]] == "yes" else 0)
        assert row["close_ms"] - row["decision_ms"] == row["lead_minutes"] * 60_000
        assert row["tau_s"] == row["lead_minutes"] * 60.0
    assert {r["lead_minutes"] for r in rows} == set(LEADS)


def test_every_feature_family_is_present_and_leak_free_on_the_pipeline_path(ran):
    store, result, _ = ran
    rows = {(r["ticker"], r["lead_minutes"]): r for r in result.outputs["fees"]["records"]}
    for ticker, asset, close, _, _ in MARKETS:
        for lead in LEADS:
            r = rows[(ticker, lead)]
            decision = ms(close - timedelta(minutes=lead))
            usable = [b for b in store.bars[asset] if b["close_time_ms"] < decision]
            assert r["spot"] == usable[-1]["close"], "spot is the last bar closed before the decision"
            assert r["spot_missing"] is False and r["bvol_missing"] is False
            assert r["rv_rms_60"] is not None and r["rv_ewma_30"] is not None
            assert r["two_sided"] is True and r["mid"] == pytest.approx(0.42)
            assert r["candle_age_ms"] == 0, "a candle ends exactly on every whole-minute decision"
            assert 0.0 < r["fair_rms"] < 1.0 and r["fair_rms_status"] == "ok"
            assert 0.0 < r["fair_bvol"] < 1.0
            assert r["fee_buy_yes"] == pytest.approx(0.0173, abs=1e-4) and r["fee_status"] == "ok"
            assert r["ln_floor_over_spot"] == pytest.approx(math.log(store.floors[ticker] / r["spot"]))


def test_the_table_is_written_as_json_lines_for_the_next_run(ran):
    _, result, out = ran
    lines = (out / "decision_features.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == len(result.outputs["fees"]["records"])
    first = json.loads(lines[0])
    assert {"ticker", "decision_ms", "label", "spot", "fair_rms", "fee_buy_yes"} <= set(first)


def test_the_kill_test_scores_both_segments_and_writes_its_report(ran):
    _, result, _ = ran
    scores = result.outputs["kill_test"]["scores"]
    segments = {s["segment"] for s in scores}
    assert segments == {"development", "heldout"}
    held = [s for s in scores if (s["segment"], s["group"], s["bucket"], s["model"]) ==
            ("heldout", "all", "all", "fair_rms")][0]
    assert held["n"] == 2 * len(LEADS), "two held-out 15-minute markets"
    artifacts = os.path.join(result.run_dir, "artifacts", "kill_test")
    assert sorted(os.listdir(artifacts)) == ["kill_test.json", "kill_test.md"]


def test_nothing_but_placement_and_the_cut_was_changed_for_this_run(tmp_path, monkeypatch):
    store = build_world(tmp_path, monkeypatch)
    path, _ = patched_doc(store, tmp_path)
    shipped_doc, patched = doc_dict(), json.loads(path.read_text(encoding="utf-8"))

    def strip(doc):
        doc = json.loads(json.dumps(doc))
        for spec in doc["pipeline"].values():
            spec.get("params", {}).pop("root", None)
        doc["pipeline"]["write"]["params"].pop("path")
        doc["pipeline"]["kill_test"]["params"]["segments"] = None
        return doc

    assert strip(patched) == strip(shipped_doc)


def test_the_feature_table_publishes_through_localtables_and_reads_back(ran, tmp_path):
    store, result, out = ran
    config = shipped("source-features-15m.json")
    config["path"] = str(out)
    check_config(LocalTablesConnector(), config)
    store._register("features-15m", "localtables", config)
    run_acquisition(store.onboarding, store.registry, "features-15m", "decision_features", "backfill")
    node = ObservationRows("features", {
        "root": store.path, "source": "features-15m", "stream": "decision_features",
        "key_fields": ["ticker", "lead_minutes"], "ts_field": "decision_ms", "ts_unit": "ms"})
    rows = node.run(None, {})["records"]
    written = result.outputs["fees"]["records"]
    assert len(rows) == len(written)
    assert {(r["ticker"], r["lead_minutes"]) for r in rows} == {
        (r["ticker"], r["lead_minutes"]) for r in written}
    assert all(r["asof_ms"] == r["decision_ms"] for r in rows)


def test_the_shipped_document_validates_and_plans_without_any_store(capsys):
    from dskit.pipeline.__main__ import cmd_plan, cmd_validate

    assert cmd_validate(DOC, adapters=("crypto_trading",)) == 0
    capsys.readouterr()
    assert cmd_plan(DOC, adapters=("crypto_trading",)) == 0
    plan = json.loads(capsys.readouterr().out)
    assert "fees" in json.dumps(plan)


def test_the_package_imports_and_the_document_plans_with_numpy_and_pyarrow_blocked():
    """Heavy libraries load only inside run(): a machine without them can still plan (ADR-0021)."""
    code = (
        "import sys\n"
        "for name in ('numpy', 'pyarrow'):\n"
        "    sys.modules[name] = None\n"
        "from dskit.pipeline.__main__ import cmd_plan\n"
        "import crypto_trading\n"
        f"raise SystemExit(cmd_plan({DOC!r}, ('crypto_trading',)))\n")
    done = subprocess.run([sys.executable, "-c", code], cwd=CHILD_ROOT, capture_output=True, text=True,
                          timeout=120)
    assert done.returncode == 0, done.stderr[-2000:]
