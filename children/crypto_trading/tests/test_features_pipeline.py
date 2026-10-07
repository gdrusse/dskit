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
import re
import shutil
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
CUT_MS = ms(utc(2026, 9, 3))  # the synthetic week's held-out cut, epoch ms (the shipped one is 2026-09-15)
LEADS = (2, 5, 10)
DELTA = 4e-4  # the settlement index sits this far below Binance in the fixtures (about 24 USD at 60000)

#: (ticker, asset, close instant, result); each strike is the index at the market's open
MARKETS = [
    ("KXBTC15M-26SEP020115-15", "BTC", utc(2026, 9, 2, 1, 15), "yes"),
    ("KXBTC15M-26SEP020130-30", "BTC", utc(2026, 9, 2, 1, 30), "no"),
    ("KXBTC15M-26SEP030115-15", "BTC", utc(2026, 9, 3, 1, 15), "yes"),
    ("KXETH15M-26SEP020115-15", "ETH", utc(2026, 9, 2, 1, 15), "no"),
    ("KXETH15M-26SEP030130-30", "ETH", utc(2026, 9, 3, 1, 30), "yes"),
]


SPREAD = 0.04


def bid_at(market_index, minute):
    """The fixture's YES bid in the candle that ends ``minute`` minutes after the open: it moves every minute."""
    return round(0.30 + 0.01 * minute + 0.02 * market_index, 4)


def doc_dict():
    with open(DOC, encoding="utf-8") as handle:
        return json.load(handle)


def decision_instants(fixtures=MARKETS):
    return [ms(close - timedelta(minutes=lead)) for _, _, close, _ in fixtures for lead in LEADS]


def build_world(tmp_path, monkeypatch, fixtures=MARKETS, first_bar=utc(2026, 9, 1, 16, 0), hours=35):
    """Markets, candles, fees, klines and BVOL for the fixture markets (``fixtures``: the five of ``MARKETS`` unless given).

    ``first_bar`` and ``hours`` size the Binance history: at least 9 hours before the first decision (EWMA needs
    300 bars) and bars past the last close.
    """
    store = Store(tmp_path / "world", monkeypatch)
    start = ms(first_bar)
    bars = {"BTC": walk(start, hours * 60, price=60000.0, seed=11),
            "ETH": walk(start, hours * 60, price=3000.0, seed=12)}

    def index_at_open(asset, close):
        """The strike of a 15-minute up/down market: the index (Binance mid of the minute before the open, less
        the fixture's basis) at the market's open."""
        opened = ms(close - timedelta(minutes=15))
        bar = [b for b in bars[asset] if b["open_time_ms"] == opened - 60_000][0]
        return (bar["open"] + bar["close"]) / 2.0 * (1.0 - DELTA)

    markets, candles = [], {}
    for index, (ticker, asset, close, result) in enumerate(fixtures):
        floor = round(index_at_open(asset, close), 2)  # strikes are index dollars, set at the open
        markets.append(market_payload(ticker, close, floor=floor, result=result))
        candles[ticker] = [candle_payload(close - timedelta(minutes=15 - k), bid=bid_at(index, k), ask=bid_at(index, k) + SPREAD,
                                          price=bid_at(index, k) + SPREAD / 2) for k in range(1, 16)]
    api = ScriptedKalshi(markets, candles, {s: ("quadratic", 1) for s in SERIES})
    store.kalshi("kalshi-crypto", "source-kalshi-crypto.json", api, ["markets", "fee_schedules"])
    store.kalshi("kalshi-crypto-candles-btc", "source-kalshi-crypto-candles-btc.json", api, ["candles"])
    store.kalshi("kalshi-crypto-candles-eth", "source-kalshi-crypto-candles-eth.json", api, ["candles"])
    bvol = sorted({(d - 1000 * k, 50.0 + k) for d in decision_instants(fixtures) for k in (0, 1, 2)})
    store.blobs("binance-btcusdt-1m", kline_days(bars["BTC"]))
    store.blobs("binance-ethusdt-1m", kline_days(bars["ETH"]))
    store.blobs("binance-btcbvol", bvol_days(bvol))
    store.blobs("binance-ethbvol", bvol_days(bvol, symbol="ETHBVOLUSDT"))
    store.bars = bars
    store.floors = {m["ticker"]: m["floor_strike"] for m in markets}
    return store


RUNBOOK = os.path.join(CHILD_ROOT, "docs", "plans", "2026-10-06-wsl-data-pull-runbook.md")


def runbook_root_command():
    """The one command the runbook (B1) gives for pointing the document at the owner's store."""
    text = open(RUNBOOK, encoding="utf-8").read()
    (line,) = re.findall(r'^(sed -i "s#[^#\n]+#\$OB#g" configs/run-features-15m\.json)$', text, re.M)
    return line


def patched_doc(store, tmp_path, tweak=None, name="run.json"):
    """The shipped document with its roots rewritten EXACTLY as the runbook says, then placement and the cut moved.

    ``tweak(doc)`` may change the document further (a second, different configuration).
    """
    work = tmp_path / ("shipped-" + name)
    (work / "configs").mkdir(parents=True)
    shutil.copy(DOC, work / "configs" / "run-features-15m.json")
    done = subprocess.run(["bash", "-c", runbook_root_command()], cwd=work, capture_output=True, text=True,
                          env={**os.environ, "OB": store.path})
    assert done.returncode == 0, done.stderr
    doc = json.loads((work / "configs" / "run-features-15m.json").read_text(encoding="utf-8"))
    out = tmp_path / "features"
    out.mkdir(exist_ok=True)
    shipped_path = doc["pipeline"]["write"]["params"]["path"]
    doc["pipeline"]["write"]["params"]["path"] = str(out / os.path.basename(shipped_path))  # keeps the {run} template
    kill = doc["pipeline"]["kill_test"]["params"]
    kill["segments"]["development"]["end_ms"] = CUT_MS
    kill["segments"]["heldout"]["start_ms"] = CUT_MS
    kill["report_segments"] = ["development", "heldout"]  # the shipped document reports development only
    if tweak is not None:
        tweak(doc)
    path = tmp_path / name
    path.write_text(json.dumps(doc), encoding="utf-8")
    return path, out


def run(store, tmp_path, tweak=None, name="run.json"):
    path, out = patched_doc(store, tmp_path, tweak, name)
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
    result_by_ticker = {t: r for t, _, _, r in MARKETS}
    for row in rows:
        assert row["label"] == (1 if result_by_ticker[row["ticker"]] == "yes" else 0)
        assert row["close_ms"] - row["decision_ms"] == row["lead_minutes"] * 60_000
        assert row["exec_ms"] == row["decision_ms"] + 5_000 and row["tau_s"] == row["lead_minutes"] * 60.0 - 5
    assert {r["lead_minutes"] for r in rows} == set(LEADS)


def test_every_feature_family_is_present_and_leak_free_on_the_pipeline_path(ran):
    store, result, _ = ran
    rows = {(r["ticker"], r["lead_minutes"]): r for r in result.outputs["fees"]["records"]}
    for ticker, asset, close, _ in MARKETS:
        for lead in LEADS:
            r = rows[(ticker, lead)]
            decision = ms(close - timedelta(minutes=lead))
            usable = [b for b in store.bars[asset] if b["close_time_ms"] < decision]
            assert r["spot"] == usable[-1]["close"], "spot is the last bar closed before the decision"
            assert r["spot_missing"] is False and r["bvol_missing"] is False
            assert r["rv_rms_60"] is not None and r["rv_ewma_30"] is not None
            index = [m[0] for m in MARKETS].index(ticker)
            minute = 15 - lead  # the decision is `lead` minutes before the close; the candle ending then
            assert r["yes_bid"] == bid_at(index, minute) and r["yes_ask"] == pytest.approx(bid_at(index, minute) + SPREAD)
            assert r["two_sided"] is True and r["mid"] == pytest.approx(bid_at(index, minute) + SPREAD / 2)
            assert r["candle_age_ms"] == 0, "the candle ending AT the information instant is the spot bar's minute"
            assert abs(r["candle_age_ms"] - r["spot_age_ms"]) <= 5_000, "one information instant for spot and quote"
            assert r["basis_missing"] is False and r["basis"] == pytest.approx(1.0 - DELTA, rel=2e-3)
            assert r["spot_brti"] == pytest.approx(r["spot"] * r["basis"])
            assert 0.0 < r["fair_rms"] < 1.0 and r["fair_rms_status"] == "ok"
            assert 0.0 < r["fair_bvol"] < 1.0
            ask = bid_at(index, minute) + SPREAD
            assert r["fee_buy_yes"] == pytest.approx(math.ceil(round(0.07 * 100 * ask * (1 - ask) * 100, 9)) / 100 / 100)
            assert r["fee_status"] == "ok"
            assert r["ln_floor_over_spot"] == pytest.approx(math.log(store.floors[ticker] / r["spot_brti"]))


def test_the_exclusion_lists_and_provenance_are_kept_in_the_run_directory(ran):
    """The run record keeps only shapes; what was dropped, and why, is an artifact of the node that dropped it."""
    _, result, _ = ran
    kept = lambda node, name: json.loads(open(os.path.join(result.run_dir, "artifacts", node, name),  # noqa: E731
                                              encoding="utf-8").read())
    assert kept("markets", "census.json") == result.outputs["markets"]["census"] == {"rows": 5, "kept": 5}
    assert kept("markets", "excluded.json") == result.outputs["markets"]["excluded"] == []
    assert kept("decisions", "excluded.json") == result.outputs["decisions"]["excluded"]
    provenance = kept("spot", "provenance.json")
    assert provenance == result.outputs["spot"]["provenance"]
    assert provenance["BTC"]["klines"]["manifest_sha256"] and provenance["BTC"]["spot_missing"] == 0


def test_the_table_is_written_as_json_lines_for_the_next_run(ran):
    _, result, out = ran
    (table,) = list(out.iterdir())
    assert table.name == f"decision_features-{os.path.basename(result.run_dir)}.jsonl", "the file names its run"
    lines = table.read_text(encoding="utf-8").splitlines()
    assert len(lines) == len(result.outputs["fees"]["records"])
    first = json.loads(lines[0])
    assert {"ticker", "decision_ms", "label", "spot", "fair_rms", "fee_buy_yes"} <= set(first)
    assert {json.loads(line)["run_id"] for line in lines} == {os.path.basename(result.run_dir)}, (
        "every row says which run produced it")


def test_the_kill_test_scores_both_segments_and_writes_its_report(ran):
    _, result, _ = ran
    scores = result.outputs["kill_test"]["scores"]
    segments = {s["segment"] for s in scores}
    assert segments == {"development", "heldout"}
    held = [s for s in scores if (s["segment"], s["group"], s["bucket"], s["model"]) ==
            ("heldout", "all", "all", "fair_rms")][0]
    assert held["n"] == 2 * len(LEADS), "two held-out 15-minute markets"
    artifacts = os.path.join(result.run_dir, "artifacts", "kill_test")
    assert sorted(os.listdir(artifacts)) == ["binary_score.json", "binary_score.md"]


def test_nothing_but_placement_and_the_cut_was_changed_for_this_run(tmp_path, monkeypatch):
    store = build_world(tmp_path, monkeypatch)
    path, _ = patched_doc(store, tmp_path)
    shipped_doc, patched = doc_dict(), json.loads(path.read_text(encoding="utf-8"))
    assert all(s["params"]["root"] == store.path for s in patched["pipeline"].values() if "root" in s.get("params", {}))

    def strip(doc):
        doc = json.loads(json.dumps(doc))
        for spec in doc["pipeline"].values():
            spec.get("params", {}).pop("root", None)
        doc["pipeline"]["write"]["params"].pop("path")
        doc["pipeline"]["kill_test"]["params"]["segments"] = None
        doc["pipeline"]["kill_test"]["params"]["report_segments"] = None
        return doc

    assert strip(patched) == strip(shipped_doc)


def stream_of(result):
    """The stream a run's table is published under: one per run, so tables never merge or shadow."""
    return f"decision_features-{os.path.basename(result.run_dir)}"


def read_back(store, stream):
    node = ObservationRows("features", {
        "root": store.path, "source": "features-15m", "stream": stream,
        "key_fields": ["ticker", "lead_minutes"], "ts_field": "decision_ms", "ts_unit": "ms"})
    return node.run(None, {})["records"]


def register_features(store, out):
    config = shipped("source-features-15m.json")
    config["path"] = str(out)
    check_config(LocalTablesConnector(), config)
    store._register("features-15m", "localtables", config)


def test_the_feature_table_publishes_through_localtables_and_reads_back(ran, tmp_path):
    store, result, out = ran
    register_features(store, out)
    run_acquisition(store.onboarding, store.registry, "features-15m", stream_of(result), "backfill")
    rows = read_back(store, stream_of(result))
    written = result.outputs["fees"]["records"]
    assert len(rows) == len(written)
    assert {(r["ticker"], r["lead_minutes"]) for r in rows} == {
        (r["ticker"], r["lead_minutes"]) for r in written}
    assert all(r["asof_ms"] == r["decision_ms"] for r in rows)
    assert {r["run_id"] for r in rows} == {os.path.basename(result.run_dir)}


def test_a_changed_document_publishes_its_own_table_and_never_shadows_the_old_one(ran, tmp_path):
    """localtables is incremental on decision_ms: re-acquiring a CHANGED table under one stream name returns
    `snapshot: null` and the OLD rows. One stream per run makes that impossible."""
    store, first, out = ran
    second, _ = run(store, tmp_path, name="run2.json",
                    tweak=lambda doc: doc["pipeline"]["decisions"]["params"].update(exec_lag_s=10))
    assert second.state == "ran", (second.state, second.error)
    assert second.run_dir != first.run_dir, "a changed document is a different run"
    assert sorted(p.name for p in out.iterdir()) == sorted(f"{stream_of(r)}.jsonl" for r in (first, second))

    register_features(store, out)
    for result in (first, second):
        done = run_acquisition(store.onboarding, store.registry, "features-15m", stream_of(result), "backfill")
        assert done["snapshot"], "each run's table is a NEW stream: its first acquisition always lands"
    old, new = read_back(store, stream_of(first)), read_back(store, stream_of(second))
    assert len(old) == len(new) == len(first.outputs["fees"]["records"])
    assert {r["exec_ms"] - r["decision_ms"] for r in old} == {5_000}, "the old table is still retrievable, unchanged"
    assert {r["exec_ms"] - r["decision_ms"] for r in new} == {10_000}, "and the new one is the new table"
    assert {r["run_id"] for r in old} != {r["run_id"] for r in new}
    again = run_acquisition(store.onboarding, store.registry, "features-15m", stream_of(first), "backfill")
    assert again["snapshot"] is None, "re-publishing an UNCHANGED table makes no new snapshot"


def test_the_horizon_the_fair_values_priced_is_the_one_the_decision_rows_carry(ran):
    """BinaryFairValue prices from decision_ms + exec_lag_s itself: its horizon equals the table's tau_s column."""
    _, result, _ = ran
    for r in result.outputs["fees"]["records"]:
        for key in ("fair_rms", "fair_ewma", "fair_bvol"):
            assert r[f"{key}_tau"] == r["tau_s"], key


def test_the_shipped_document_does_not_report_the_held_out_segment():
    assert doc_dict()["pipeline"]["kill_test"]["params"]["report_segments"] == ["development"]


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


def test_a_fair_value_and_a_kill_test_cell_match_independent_hand_computations(ran):
    """Quotes move minute by minute in the fixture, so these are real numbers, not a constant surviving a pipeline."""
    _, result, _ = ran
    rows = result.outputs["fees"]["records"]

    def phi(x):
        return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))

    for r in rows:
        v = r["rv_rms_60"] ** 2 * (r["tau_s"] - 40.0)  # variance: tau minus 2/3 of the 60 s window
        expected = phi((math.log(r["spot_brti"] / r["floor_strike"]) - v / 2.0) / math.sqrt(v))
        assert r["fair_rms"] == pytest.approx(expected, abs=1e-9)
    held = [r for r in rows if r["close_ms"] >= ms(utc(2026, 9, 3)) and r["lead_minutes"] == 2]
    assert len(held) == 2 and len({r["mid"] for r in held}) == 2, "two held-out markets, two different quotes"
    cell = [s for s in result.outputs["kill_test"]["scores"]
            if (s["model"], s["segment"], s["group"], s["bucket"]) == ("fair_rms", "heldout", "lead_minutes=2", "all")][0]
    assert cell["n"] == 2
    assert cell["brier_model"] == pytest.approx(sum((r["fair_rms"] - r["label"]) ** 2 for r in held) / 2)
    assert cell["brier_market"] == pytest.approx(sum((r["mid"] - r["label"]) ** 2 for r in held) / 2)
    assert cell["mean_market"] == pytest.approx(sum(r["mid"] for r in held) / 2)
    assert cell["base_rate"] == pytest.approx(sum(r["label"] for r in held) / 2)
