"""The hourly stage B document end to end, over synthetic onboarded stores, with leak tests.

``configs/run-features-hourly.json`` is run through the entry the CLI uses over a world of two hourly events per asset
(BTC and ETH, four hourly markets each) whose strikes, settlement values and quotes are derived from the Binance
fixture. Event one is ARCHIVED (it settles before the scripted cutoff: its candles are requested market by market),
event two is live (its candles come per event); the 15-minute markets that anchor the index units sit on both sides.
The shipped document is changed only in placement, the store root (by the runbook's own sed) and the held-out cut.

The leak tests are differential: a second world differs from the first ONLY in what a decision could not have
known, and every feature column of the earlier decision must be equal while a CONTROL row (a later decision of the
same market) must move, so the test is known to have teeth.
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
    ScriptedHistory, ScriptedKalshi, Store, archive_candle_payload, bvol_days, candle_payload, history_market_payload, kline_days, ms,
    shipped, utc, walk,
)

import crypto_trading  # noqa: F401  (import = registration, as --adapter crypto_trading does)

CHILD_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOC = os.path.join(CHILD_ROOT, "configs", "run-features-hourly.json")
RUNBOOK = os.path.join(CHILD_ROOT, "docs", "plans", "2026-10-06-wsl-data-pull-runbook.md")
SIX = ("KXBTC", "KXBTCD", "KXBTC15M", "KXETH", "KXETHD", "KXETH15M")
FAMILIES = {"BTC": ("KXBTC", "KXBTCD", "KXBTC15M"), "ETH": ("KXETH", "KXETHD", "KXETH15M")}
CLOSES = (utc(2026, 9, 2, 2, 0), utc(2026, 9, 3, 2, 0))
CUTOFF = "2026-09-02T12:00:00Z"
CUT_MS = ms(utc(2026, 9, 3))  # the held-out cut of the synthetic world: event one is development, event two held out
LEADS = (5, 15, 30)
DELTA = 4e-4  # the settlement index sits this far below Binance in the fixtures
SPREAD = 0.04
SETTLED_AFTER_S = 140
HOUR_MS = 3_600_000
MINUTE_MS = 60_000
#: Columns that are LABELS (known only at settlement): a feature may never depend on them.
LABEL_COLUMNS = {"label", "settle_value", "settlement_ms"}


def bid_at(index, minute):
    """The fixture's YES bid in the candle that ends ``minute`` minutes after the open of hourly market ``index``."""
    return round(0.20 + 0.005 * minute + 0.02 * index, 4)


class World:
    """The synthetic store and the numbers a test checks rows against."""

    def __init__(self, tmp_path, monkeypatch, *, bars=None, anchor_shift=None, value_shift=0.0, candle_shift=None):
        self.store = Store(tmp_path / "world", monkeypatch)
        start = ms(utc(2026, 9, 1, 16, 0))  # nine hours of history before the first decision: EWMA needs 300 bars
        self.bars = bars or {"BTC": walk(start, 35 * 60, price=60000.0, seed=11), "ETH": walk(start, 35 * 60, price=3000.0, seed=12)}
        self.anchor_shift, self.value_shift, self.candle_shift = anchor_shift or {}, value_shift, candle_shift
        self.hourly, self.live, self.archived, self.live_candles, self.archive_candles = [], [], [], {}, {}
        self.floors = {}
        self._markets()
        self._acquire()

    # -- the index the fixtures settle on ---------------------------------------------------------------

    def index(self, asset, bar_open_ms):
        bar = [b for b in self.bars[asset] if b["open_time_ms"] == bar_open_ms][0]
        return (bar["open"] + bar["close"]) / 2.0 * (1.0 - DELTA)

    def _place(self, payload, close):
        archived = close + timedelta(seconds=SETTLED_AFTER_S) < utc(2026, 9, 2, 12, 0)
        (self.archived if archived else self.live).append(payload)
        return archived

    def _hourly(self, asset, close):
        series_range, series_above, _ = FAMILIES[asset]
        opened = ms(close) - HOUR_MS
        ref = self.index(asset, opened - MINUTE_MS)
        settle = self.index(asset, ms(close) - MINUTE_MS) + self.value_shift
        event = lambda s: f"{s}-{close:%y%b%d%H}".upper()  # noqa: E731
        specs = [
            (series_above, f"T{round(ref * 0.998, 2):.2f}", "greater", round(ref * 0.998, 2), None),
            (series_above, f"T{round(ref * 1.002, 2):.2f}", "greater", round(ref * 1.002, 2), None),
            (series_range, f"B{round(ref * 0.999, 2):.2f}", "between", round(ref * 0.999, 2), round(ref * 1.001, 2)),
            (series_range, f"T{round(ref * 0.997, 2):.2f}", "less", None, round(ref * 0.997, 2)),
        ]
        for series, tail, kind, floor, cap in specs:
            yes = {"greater": settle >= (floor or 0), "between": (floor or 0) <= settle < (cap or 0), "less": settle < (cap or 0)}[kind]
            ticker = f"{event(series)}-{tail}"
            payload = history_market_payload(ticker, close, expiration_value=settle, minutes_open=60, strike_type=kind, floor=floor,
                                             cap=cap, result="yes" if yes else "no")
            payload["event_ticker"] = event(series)
            index = len(self.hourly)
            self.hourly.append((ticker, asset, close, index))
            self.floors[ticker] = (floor, cap)
            archived = self._place(payload, close)
            ends = [close - timedelta(minutes=60 - k) for k in range(1, 61)]
            shift = self.candle_shift or (lambda end, bid: bid)
            if archived:
                self.archive_candles[ticker] = [archive_candle_payload(e, bid=shift(e, bid_at(index, k)), ask=shift(e, bid_at(index, k)) + SPREAD,
                                                                       price=bid_at(index, k)) for k, e in enumerate(ends, 1)]
            else:
                self.live_candles[ticker] = [candle_payload(e, bid=shift(e, bid_at(index, k)), ask=shift(e, bid_at(index, k)) + SPREAD,
                                                            price=bid_at(index, k)) for k, e in enumerate(ends, 1)]

    def _fifteen(self, asset, close):
        series = FAMILIES[asset][2]
        for back in (90, 75, 60, 45, 30, 15):
            end = close - timedelta(minutes=back - 15)
            opened = ms(end) - 15 * MINUTE_MS
            strike = round(self.index(asset, opened - MINUTE_MS) + self.anchor_shift.get((asset, opened), 0.0), 2)
            settle = self.index(asset, ms(end) - MINUTE_MS)
            ticker = f"{series}-{end:%y%b%d%H%M}-{end:%M}".upper()
            payload = history_market_payload(ticker, end, expiration_value=settle, minutes_open=15, strike_type="greater_or_equal",
                                             floor=strike, result="yes" if settle >= strike else "no")
            self._place(payload, end)

    def _markets(self):
        for close in CLOSES:
            for asset in FAMILIES:
                self._hourly(asset, close)
                self._fifteen(asset, close)

    def decision_ms(self, close, lead):
        return ms(close) - lead * MINUTE_MS

    def _acquire(self):
        api = ScriptedHistory(live=self.live, archived=self.archived, candles=self.live_candles, archived_candles=self.archive_candles,
                              cutoff=CUTOFF, also_live=self.archived[:1])
        self.api = api
        store = self.store
        store.kalshi_history("kalshi-history-crypto", "source-kalshi-history-crypto.json", api, ["markets"])
        store.kalshi_history("kalshi-history-candles-hourly-btc", "source-kalshi-history-candles-hourly-btc.json", api, ["candles"])
        store.kalshi_history("kalshi-history-candles-hourly-eth", "source-kalshi-history-candles-hourly-eth.json", api, ["candles"])
        fees = ScriptedKalshi([], {}, {s: ("quadratic", 1) for s in SIX})
        store.kalshi("kalshi-crypto", "source-kalshi-crypto.json", fees, ["fee_schedules"])
        instants = [self.decision_ms(c, lead) for c in CLOSES for lead in LEADS]
        bvol = sorted({(d - 1000 * k, 50.0 + k) for d in instants for k in (0, 1, 2)})
        store.blobs("binance-btcusdt-1m", kline_days(self.bars["BTC"]))
        store.blobs("binance-ethusdt-1m", kline_days(self.bars["ETH"]))
        store.blobs("binance-btcbvol", bvol_days(bvol))
        store.blobs("binance-ethbvol", bvol_days(bvol, symbol="ETHBVOLUSDT"))


def runbook_root_command():
    """The one command the runbook (B6) gives for pointing the hourly document at the owner's store."""
    text = open(RUNBOOK, encoding="utf-8").read()
    (line,) = re.findall(r'^(sed -i "s#[^#\n]+#\$OB#g" configs/run-features-hourly\.json)$', text, re.M)
    return line


def patched_doc(world, tmp_path, name="run-hourly.json"):
    """The shipped document with its roots rewritten EXACTLY as the runbook says, then placement and the cut moved."""
    work = tmp_path / ("shipped-" + name)
    (work / "configs").mkdir(parents=True)
    shutil.copy(DOC, work / "configs" / "run-features-hourly.json")
    done = subprocess.run(["bash", "-c", runbook_root_command()], cwd=work, capture_output=True, text=True,
                          env={**os.environ, "OB": world.store.path})
    assert done.returncode == 0, done.stderr
    doc = json.loads((work / "configs" / "run-features-hourly.json").read_text(encoding="utf-8"))
    out = tmp_path / "features"
    out.mkdir(exist_ok=True)
    doc["pipeline"]["write"]["params"]["path"] = str(out / os.path.basename(doc["pipeline"]["write"]["params"]["path"]))
    kill = doc["pipeline"]["kill_test"]["params"]
    kill["segments"]["development"]["end_ms"] = CUT_MS
    kill["segments"]["heldout"]["start_ms"] = CUT_MS
    kill["report_segments"] = ["development", "heldout"]
    path = tmp_path / name
    path.write_text(json.dumps(doc), encoding="utf-8")
    return path, out


def run(world, tmp_path, name="run-hourly.json"):
    path, out = patched_doc(world, tmp_path, name)
    document = replace(load_document(str(path)), outputs=OutputsConfig(run_root=str(tmp_path / "runs")))
    return run_document(document, asof="2026-10-06"), out


@pytest.fixture
def ran(tmp_path, monkeypatch):
    world = World(tmp_path, monkeypatch)
    result, out = run(world, tmp_path)
    assert result.state == "ran", (result.state, result.error)
    return world, result, out


def rows_of(result):
    return {(r["ticker"], r["lead_minutes"]): r for r in result.outputs["fees"]["records"]}


def features(row):
    return {k: v for k, v in row.items() if k not in LABEL_COLUMNS}


# -- the document runs and its table is the hourly ladders' --------------------------------------------------


def test_the_document_runs_and_every_node_is_ok(ran):
    _, result, _ = ran
    assert set(result.node_states.values()) == {"ok"}
    assert set(result.node_states) == set(json.load(open(DOC, encoding="utf-8"))["pipeline"])


def test_one_row_per_hourly_market_and_lead_and_not_one_for_the_fifteen_minute_markets(ran):
    world, result, _ = ran
    rows = result.outputs["fees"]["records"]
    assert len(rows) == len(world.hourly) * len(LEADS) == 16 * 3
    assert {r["series"] for r in rows} == {"KXBTC", "KXBTCD", "KXETH", "KXETHD"}, "the 15-minute markets only anchor the units"
    assert {r["lead_minutes"] for r in rows} == set(LEADS)
    census = result.outputs["markets"]["census"]
    assert census == {"rows": 16, "kept": 16} and result.outputs["markets"]["excluded"] == []
    assert result.outputs["anchor_markets"]["census"]["kept"] == 24
    assert {a["series"] for a in result.outputs["anchors"]["records"]} == {"KXBTC15M", "KXETH15M"}


def test_both_archives_fed_the_markets_and_the_candles(ran):
    world, result, _ = ran
    api = world.api
    assert api.paths("/historical/markets"), "event one is archived"
    assert any(p.startswith("/historical/markets/") and p.endswith("/candlesticks") for p, _ in api.calls), "archived: one by one"
    assert any(p.startswith("/series/") and "/events/" in p for p, _ in api.calls), "live: per event"
    tickers = {t for t, *_ in world.hourly}
    archived = {m["ticker"] for m in world.archived}
    assert {r["ticker"] for r in result.outputs["fees"]["records"]} == tickers
    assert tickers & archived and tickers - archived, "the table spans both sides of the cutoff"


def test_the_label_is_the_geometry_applied_to_the_settlement_value_known_only_afterwards(ran):
    _, result, _ = ran
    for row in result.outputs["fees"]["records"]:
        floor, cap = row["floor_strike"], row["cap_strike"]
        value = row["settle_value"]
        yes = {"above": value >= (floor or 0), "below": value < (cap or 0), "between": (floor or 0) <= value < (cap or 0)}[row["payoff"]]
        assert row["label"] == int(yes), row["ticker"]
        assert row["settlement_ms"] == row["close_ms"] + SETTLED_AFTER_S * 1000
        assert row["settlement_ms"] > row["exec_ms"] > row["decision_ms"], "the label never exists at the decision"


def test_every_feature_family_is_present_and_point_in_time_on_the_pipeline_path(ran):
    world, result, _ = ran
    rows = rows_of(result)
    for ticker, asset, close, index in world.hourly:
        for lead in LEADS:
            r = rows[(ticker, lead)]
            decision = world.decision_ms(close, lead)
            usable = [b for b in world.bars[asset] if b["close_time_ms"] < decision]
            assert r["spot"] == usable[-1]["close"], "spot is the last bar closed before the decision"
            assert r["spot_missing"] is False and r["bvol_missing"] is False and r["basis_missing"] is False
            assert r["strike_known_ms"] == r["open_ms"], "an hourly strike is fixed at the open"
            minute = 60 - lead
            assert r["yes_bid"] == bid_at(index, minute) and r["yes_ask"] == pytest.approx(bid_at(index, minute) + SPREAD)
            assert r["two_sided"] is True and r["candle_age_ms"] == 0, "the quote is the candle that ended AT the information instant"
            assert r["basis"] == pytest.approx(1.0 - DELTA, rel=2e-3) and r["spot_brti"] == pytest.approx(r["spot"] * r["basis"])
            assert r["fair_rms_status"] == "ok" and 0.0 < r["fair_rms"] < 1.0 and 0.0 < r["fair_bvol"] < 1.0
            assert r["fair_rms_tau"] == r["tau_s"] == lead * 60.0 - 5
            assert r["fee_status"] == "ok" and r["fee_buy_yes"] > 0.0


def test_the_basis_comes_from_the_fifteen_minute_anchors_known_before_each_decision(ran):
    world, result, _ = ran
    anchors = result.outputs["anchors"]["records"]
    rows = rows_of(result)
    for ticker, asset, close, _ in world.hourly:
        for lead in LEADS:
            r = rows[(ticker, lead)]
            decision = world.decision_ms(close, lead)
            usable = [a for a in anchors if a["series"] in FAMILIES[asset] and a["known_ms"] < decision]
            latest = max(usable, key=lambda a: a["known_ms"])
            assert r["basis_age_ms"] == decision - latest["known_ms"] and r["basis_age_ms"] <= 1_200_000


def test_the_kill_test_scores_both_segments_and_writes_its_report(ran):
    _, result, _ = ran
    scores = result.outputs["kill_test"]["scores"]
    assert {s["segment"] for s in scores} == {"development", "heldout"}
    held = [s for s in scores if (s["segment"], s["group"], s["bucket"], s["model"]) == ("heldout", "all", "all", "fair_rms")][0]
    assert held["n"] == 8 * len(LEADS), "eight held-out hourly markets, every one two-sided at every lead"
    assert sorted(os.listdir(os.path.join(result.run_dir, "artifacts", "kill_test"))) == ["binary_score.json", "binary_score.md"]


def test_a_fair_value_matches_an_independent_hand_computation(ran):
    _, result, _ = ran

    def phi(x):
        return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))

    for r in result.outputs["fees"]["records"]:
        v = r["rv_rms_60"] ** 2 * (r["tau_s"] - 40.0)  # variance: tau minus 2/3 of the 60 s window
        if r["payoff"] == "above":
            expected = phi((math.log(r["spot_brti"] / r["floor_strike"]) - v / 2.0) / math.sqrt(v))
        elif r["payoff"] == "below":
            expected = 1.0 - phi((math.log(r["spot_brti"] / r["cap_strike"]) - v / 2.0) / math.sqrt(v))
        else:
            expected = (phi((math.log(r["spot_brti"] / r["floor_strike"]) - v / 2.0) / math.sqrt(v))
                        - phi((math.log(r["spot_brti"] / r["cap_strike"]) - v / 2.0) / math.sqrt(v)))
        assert r["fair_rms"] == pytest.approx(expected, abs=1e-9)


# -- the leak tests: a second world that differs only in what the decision could not have known ---------------------


def pair(tmp_path, monkeypatch, **second):
    a = World(tmp_path / "a", monkeypatch)
    first, _ = run(a, tmp_path / "a")
    b = World(tmp_path / "b", monkeypatch, **second)
    other, _ = run(b, tmp_path / "b")
    assert first.state == other.state == "ran", (first.error, other.error)
    return a, rows_of(first), rows_of(other)


def spiked(bars, from_ms, factor=10.0):
    """The same bars with every bar that CLOSES at or after ``from_ms`` replaced by a spike (prices times ``factor``)."""
    out = []
    for bar in bars:
        if bar["close_time_ms"] >= from_ms:
            bar = {**bar, "open": bar["open"] * factor, "close": bar["close"] * factor, "high": bar["high"] * factor,
                   "low": bar["low"] * factor}
        out.append(bar)
    return out


def test_bars_that_close_at_or_after_the_earliest_decision_never_reach_that_decisions_features(tmp_path, monkeypatch):
    world = World(tmp_path / "probe", monkeypatch)
    cut = world.decision_ms(CLOSES[0], 30)
    bars = {asset: spiked(b, cut) for asset, b in world.bars.items()}
    a, clean, dirty = pair(tmp_path, monkeypatch, bars=bars)
    early = [k for k in clean if k[1] == 30 and clean[k]["close_ms"] == ms(CLOSES[0])]
    assert len(early) == 8
    for key in early:
        assert features(clean[key]) == features(dirty[key]), key
    control = [k for k in clean if k[1] == 5 and clean[k]["close_ms"] == ms(CLOSES[0])]
    assert all(clean[k]["spot"] != dirty[k]["spot"] for k in control), "the spike is legal for the later decision: the control moves"


def test_a_candle_that_ends_after_the_decision_never_reaches_the_quote(tmp_path, monkeypatch):
    close = CLOSES[1]  # the live event, requested per event
    cut = close - timedelta(minutes=30)

    def shift(end, bid):
        return bid + 0.05 if end > cut else bid

    a, clean, dirty = pair(tmp_path, monkeypatch, candle_shift=shift)
    for key, row in clean.items():
        if key[1] == 30 and row["close_ms"] == ms(close):
            assert features(row) == features(dirty[key]), key
        if key[1] == 15 and row["close_ms"] == ms(close):
            assert row["yes_bid"] != dirty[key]["yes_bid"], "control: a later decision legitimately reads the later candle"


def test_the_realised_value_and_the_result_never_reach_a_feature(tmp_path, monkeypatch):
    a, clean, dirty = pair(tmp_path, monkeypatch, value_shift=250.0)
    assert any(clean[k]["settle_value"] != dirty[k]["settle_value"] for k in clean), "the world did change its labels"
    assert any(clean[k]["label"] != dirty[k]["label"] for k in clean), "and some results flipped"
    for key in clean:
        assert features(clean[key]) == features(dirty[key]), key


def test_a_fifteen_minute_strike_published_after_the_decision_is_not_the_anchor_of_that_decision(tmp_path, monkeypatch):
    close = CLOSES[1]
    opened = ms(close) - 15 * MINUTE_MS  # the last 15-minute market of the hour: its strike is known 30 s after this
    shift = {("BTC", opened): 120.0, ("ETH", opened): 6.0}
    a, clean, dirty = pair(tmp_path, monkeypatch, anchor_shift=shift)
    for key, row in clean.items():
        if row["close_ms"] != ms(close):
            continue
        if key[1] in (15, 30):  # decisions at close-15 and close-30 are not later than the changed strike's open + 30 s
            assert features(row) == features(dirty[key]), key
        if key[1] == 5:
            assert row["basis"] != dirty[key]["basis"], "control: the decision after it uses the new anchor"


# -- the table is published and read back like the 15-minute one ----------------------------------------------


def stream_of(result):
    return f"decision_features-{os.path.basename(result.run_dir)}"


def test_the_table_publishes_through_localtables_and_reads_back(ran):
    world, result, out = ran
    (table,) = list(out.iterdir())
    assert table.name == f"{stream_of(result)}.jsonl"
    config = shipped("source-features-hourly.json")
    config["path"] = str(out)
    check_config(LocalTablesConnector(), config)
    world.store._register("features-hourly", "localtables", config)
    run_acquisition(world.store.onboarding, world.store.registry, "features-hourly", stream_of(result), "backfill")
    node = ObservationRows("features", {"root": world.store.path, "source": "features-hourly", "stream": stream_of(result),
                                        "key_fields": ["ticker", "lead_minutes"], "ts_field": "decision_ms", "ts_unit": "ms"})
    rows = node.run(None, {})["records"]
    assert len(rows) == len(result.outputs["fees"]["records"]) == 48
    assert {r["run_id"] for r in rows} == {os.path.basename(result.run_dir)}
    assert {"settle_value", "settlement_ms"} <= set(rows[0]), "the label columns ride along, named as labels"


# -- offline: validate and plan need no store, no numpy, no pyarrow -----------------------------------------------


def test_the_shipped_document_validates_and_plans_without_any_store(capsys):
    from dskit.pipeline.__main__ import cmd_plan, cmd_validate

    assert cmd_validate(DOC, adapters=("crypto_trading",)) == 0
    capsys.readouterr()
    assert cmd_plan(DOC, adapters=("crypto_trading",)) == 0
    plan = json.loads(capsys.readouterr().out)
    assert "anchor_markets" in json.dumps(plan) and "candles_btc" in plan["order"]


def test_the_document_plans_with_numpy_and_pyarrow_blocked():
    code = (
        "import sys\n"
        "for name in ('numpy', 'pyarrow'):\n"
        "    sys.modules[name] = None\n"
        "from dskit.pipeline.__main__ import cmd_plan\n"
        "import crypto_trading\n"
        f"raise SystemExit(cmd_plan({DOC!r}, ('crypto_trading',)))\n")
    done = subprocess.run([sys.executable, "-c", code], cwd=CHILD_ROOT, capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stderr[-2000:]
