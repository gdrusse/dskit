"""The W4 take test: configs/run-w4-take.json and the refit/panel configs it depends on.

Two halves. The PINS keep every value that appears in two documents in agreement (the decision table is
run-features-15m.json's, the W4 dates are the plan's, the panel the refit trains on is the one the take test reads). The
END-TO-END half runs the shipped document over the synthetic onboarded world of test_features_pipeline and a tiny CPU
model on a synthetic minute panel, and recomputes P(YES) of every row independently from the model's own distribution."""

import copy
import json
import math
import os
from dataclasses import replace
from datetime import datetime, timezone
from statistics import NormalDist

import pytest
from dskit.pipeline import OutputsConfig, run_document
from dskit.pipeline.document import load_document
from dskit.pipeline.kinds_flow import Filter
from synthetic import ms, utc
from test_features_pipeline import LEADS, MARKETS, build_world

import crypto_trading  # noqa: F401  (import = registration)
from crypto_trading.minute_features import BAR_MS, CLOSE_OFFSET_MS

CHILD_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIGS = os.path.join(CHILD_ROOT, "configs")
W4_START_MS, W4_END_MS = 1785542400000, 1789430400000        # 2026-08-01 and 2026-09-15, UTC midnight
MARGINS_CENTS = (0, 1, 2, 3, 5)
LANE = "gru-crps_tail-pooled"
SHIPPED_ROOT = "/home/russell/data/crypto_trading/ob"


def load(name):
    with open(os.path.join(CONFIGS, name), encoding="utf-8") as handle:
        return json.load(handle)


DOC, FEATURES = load("run-w4-take.json"), load("run-features-15m.json")
NODES, SRC = DOC["pipeline"], FEATURES["pipeline"]
OVERLAY, RECIPE = load("production-args-w4.json"), load("zoo-lanes-recipe.json")
BUILD, BUILD_W4 = load("run-panel-build.json"), load("run-panel-build-w4.json")


def as_date(milliseconds):
    return datetime.fromtimestamp(milliseconds / 1000, tz=timezone.utc).date().isoformat()


# --- the window ---------------------------------------------------------------------------------------------------------


def test_the_w4_cuts_are_the_plans_and_the_one_file_that_owns_the_held_out_cut():
    bounds = RECIPE["w2"]["bounds"]
    assert W4_START_MS == bounds["val_end_ms"] + 1 and as_date(W4_START_MS) == "2026-08-01"
    cut = SRC["kill_test"]["params"]["segments"]["heldout"]["start_ms"]
    assert W4_END_MS == cut and as_date(W4_END_MS) == "2026-09-15", "W4 ends where the held-out cut is: W5 is not read"
    assert OVERLAY["forward_start"] == as_date(W4_START_MS) and OVERLAY["evaluate_through"] == as_date(W4_END_MS)
    assert BUILD_W4["pipeline"]["tapes"]["params"]["end_ms"] == W4_END_MS


def test_the_filters_keep_the_markets_closing_in_w4_and_nothing_at_or_after_the_cut():
    rows = [{"close_ms": t, "ticker": str(t)} for t in (W4_START_MS - 1, W4_START_MS, W4_END_MS - 1, W4_END_MS, W4_END_MS + 1)]
    through = Filter("t", NODES["markets_through"]["params"]).run(None, {"records": rows})["records"]
    window = Filter("w", NODES["window"]["params"]).run(None, {"records": through})["records"]
    assert [r["close_ms"] for r in through] == [W4_START_MS - 1, W4_START_MS, W4_END_MS - 1], "anchors keep the markets before W4"
    assert [r["close_ms"] for r in window] == [W4_START_MS, W4_END_MS - 1], "[W4 start, W4 end)"
    candles = Filter("c", NODES["candles_through"]["params"]).run(None, {"records": [{"end_ms": W4_END_MS - 1}, {"end_ms": W4_END_MS}]})
    assert candles["records"] == [{"end_ms": W4_END_MS - 1}]


def test_the_decisions_read_the_windowed_markets_and_the_anchors_the_unwindowed_ones():
    assert NODES["markets_through"]["inputs"] == {"records": "$markets.records"}
    assert NODES["anchors"]["inputs"] == {"records": "$markets_through.records"}
    assert NODES["window"]["inputs"] == {"records": "$markets_through.records"}
    assert NODES["decisions"]["inputs"] == {"records": "$window.records"}
    assert NODES["state"]["inputs"]["candles"] == "$candles_through.records"
    assert NODES["candles_through"]["inputs"] == {"records": "$candles.merged"}


# --- the decision table is run-features-15m's ---------------------------------------------------------------------------


@pytest.mark.parametrize("key", ["markets", "anchors", "candles_btc", "candles_eth", "candles", "fee_schedules", "streams",
                                 "decisions", "spot", "state", "fair_rms", "fair_ewma", "fair_bvol"])
def test_the_decision_table_nodes_are_run_features_15ms(key):
    assert NODES[key]["uses"] == SRC[key]["uses"] and NODES[key]["params"] == SRC[key]["params"], key


def test_only_the_fee_size_differs_in_the_fee_node():
    assert {k: v for k, v in NODES["fees"]["params"].items() if k != "contracts"} == {
        k: v for k, v in SRC["fees"]["params"].items() if k != "contracts"}
    assert NODES["fees"]["params"]["contracts"] == 1, "the test trades one contract: the per-order rounding is paid in full"
    assert NODES["fees"]["inputs"]["records"] == "$fair_gru.records" and NODES["fair_gru"]["inputs"]["records"] == "$fair_bvol.records"


def test_the_exec_lag_agrees_across_the_decision_and_the_closed_form_baselines():
    lag = NODES["decisions"]["params"]["exec_lag_s"]
    assert {NODES[k]["params"]["exec_lag_s"] for k in ("fair_rms", "fair_ewma", "fair_bvol")} == {lag}


# --- the margin grid ----------------------------------------------------------------------------------------------------


def test_the_margin_grid_is_declared_and_each_node_agrees_with_the_first_in_everything_else():
    takes = {k: v for k, v in NODES.items() if k.startswith("take_m")}
    assert sorted(takes) == sorted(f"take_m{c:02d}" for c in MARGINS_CENTS)
    first = takes["take_m00"]["params"]
    for cents in MARGINS_CENTS:
        node = takes[f"take_m{cents:02d}"]
        assert node["params"]["margin"] == pytest.approx(cents / 100), "the key says the margin in cents"
        assert {k: v for k, v in node["params"].items() if k != "margin"} == {k: v for k, v in first.items() if k != "margin"}
        assert node["inputs"] == {"records": "$fees.records"} and node["uses"] == "dskit.pipeline.binary_scoring:BucketedBinaryScore"


def test_the_scorers_read_w4_only_by_close_and_share_the_kill_tests_everything_else():
    params, kill = NODES["take_m00"]["params"], SRC["kill_test"]["params"]
    assert params["segments"] == {"w4": {"start_ms": W4_START_MS, "end_ms": W4_END_MS}} and params["report_segments"] == ["w4"]
    assert params["model_fields"] == ["fair_gru", "fair_rms", "fair_ewma", "fair_bvol"]
    assert params["by"] == ["lead_minutes"]
    for key in ("market_field", "label_field", "settle_field", "bid_field", "ask_field", "fee_yes_field", "fee_no_field", "eligible_field",
                "bucket_edges", "cluster_field", "cluster_block_s"):
        assert params[key] == kill[key], key
    assert params["cluster_block_s"] == 86400, "day blocks"


# --- the GRU fair value agrees with the panel, the refit and the decision table -------------------------------------------


def test_the_gru_fair_value_columns_are_the_ones_the_closed_form_baselines_price_with():
    gru, rms = NODES["fair_gru"]["params"], NODES["fair_rms"]["params"]
    assert gru["reference_field"] == rms["spot_field"] == "spot_brti", "the index-unit spot, never the raw Binance price"
    assert (gru["payoff_field"], gru["lower_field"], gru["upper_field"]) == (rms["payoff_field"], rms["lower_field"], rms["upper_field"])
    assert gru["time_field"] == rms["decision_field"] == "decision_ms" and gru["horizon_field"] == "lead_minutes"
    assert gru["fair_field"] == "fair_gru" and gru["fair_field"] in NODES["take_m00"]["params"]["model_fields"]


def test_the_panel_clock_and_units_agree_with_the_feature_node_and_the_build():
    gru, build = NODES["fair_gru"]["params"], BUILD_W4["pipeline"]
    assert gru["row_offset_ms"] == -CLOSE_OFFSET_MS, "the panel row is the bar closed just before the decision minute"
    assert gru["horizon_unit_ms"] == BAR_MS == build["panel"]["params"]["step_ms"], "a lead is in minutes, a panel step one minute"
    horizons = build["panel"]["params"]["horizons"]
    assert all(lead in horizons for lead in NODES["decisions"]["params"]["leads_minutes"]), "every lead is an exact label horizon"
    assert gru["horizon_rule"] == "nearest"
    assets = build["features"]["params"]["assets"]
    assert gru["group_codes"] == {"KXBTC15M": assets.index("BTC"), "KXETH15M": assets.index("ETH")}, "MinuteFeatures codes assets by position"
    assert set(gru["group_codes"]) == set(NODES["markets"]["params"]["series"])


def test_the_panel_the_model_is_scored_on_is_the_panel_the_refit_trained_on():
    panel, build = NODES["fair_gru"]["params"]["panel"], BUILD_W4["pipeline"]["panel"]["params"]
    source = load("source-panel-minute-w4.json")
    assert (panel["source"], panel["relpath"]) == (OVERLAY["panel_source"], OVERLAY["panel_relpath"]) and panel["stream"] == "files"
    assert os.path.basename(build["path"]) == panel["relpath"] and os.path.basename(source["path"]) == os.path.dirname(build["path"])
    assert source["include"] == [f"{panel['relpath']}/*"] and source["stream"] == "files"
    assert panel["root"] == NODES["markets"]["params"]["root"]
    assert panel["source"] != "panel-minute" and panel["relpath"] != "minute-w1w2", "the W1/W2 panel is never overwritten or renamed"


def test_the_saved_model_is_where_the_refit_writes_it():
    artifact = NODES["fair_gru"]["params"]["artifact"]
    files = RECIPE["production"]["files"]
    expected = os.path.join(OVERLAY["work_dir"], files["models_dir"], LANE, f"block-1-{OVERLAY['forward_start']}", files["weights"])
    assert os.path.normpath(artifact) == os.path.normpath(expected)
    assert not os.path.isabs(artifact), "relative to the child root, under the git-ignored pipeline_runs"
    state_hash = NODES["fair_gru"]["params"]["state_hash"]
    assert state_hash.startswith("<") or (len(state_hash) == 64 and int(state_hash, 16) >= 0), "a placeholder, or the model's hash"


def test_the_panel_build_is_the_w1_w2_build_to_w4s_end_and_nothing_else():
    def stripped(doc):
        doc = copy.deepcopy(doc)
        for key in ("name", "notes"):
            doc.pop(key)
        doc["pipeline"]["tapes"]["params"].pop("end_ms")
        doc["pipeline"]["panel"]["params"].pop("path")
        return doc

    assert stripped(BUILD) == stripped(BUILD_W4)
    assert BUILD["pipeline"]["tapes"]["params"]["end_ms"] == W4_START_MS, "the W1/W2 panel stops where W4 starts"
    assert BUILD_W4["pipeline"]["panel"]["params"]["path"] != BUILD["pipeline"]["panel"]["params"]["path"]


def test_no_machine_path_but_the_store_root_and_the_derived_table_origin():
    found = []

    def walk(node, path=""):
        if isinstance(node, dict):
            for key, value in node.items():
                walk(value, f"{path}.{key}")
        elif isinstance(node, list):
            for i, value in enumerate(node):
                walk(value, f"{path}[{i}]")
        elif isinstance(node, str) and (node.startswith(("/", "~")) or "/home/" in node) and not path.endswith(".notes"):
            found.append((path, node))

    walk(DOC)
    assert {p.rsplit(".", 1)[1] for p, _ in found} == {"root", "path"}, found
    assert all(v == SHIPPED_ROOT for p, v in found if p.endswith(".root")) and len([p for p, _ in found if p.endswith(".path")]) == 1


def without_notes(node):
    if isinstance(node, dict):
        return {k: without_notes(v) for k, v in node.items() if k != "notes"}
    return [without_notes(v) for v in node] if isinstance(node, list) else node


def test_no_param_names_a_w5_instant():
    text = json.dumps(without_notes([OVERLAY, DOC["pipeline"], BUILD_W4["pipeline"]]))
    assert all(day not in text for day in ("2026-09-16", "2026-09-2", "2026-09-30", "2026-10"))
    assert str(W4_END_MS) in text and "heldout" not in json.dumps(NODES), "the held-out segment is never reported here"


# --- end to end ----------------------------------------------------------------------------------------------------------

NORMAL = NormalDist()
PANEL_FIRST = ms(utc(2026, 8, 28))
PANEL_LAST = ms(utc(2026, 9, 3, 3, 0))
TRAIN_END = ms(utc(2026, 8, 31)) - 1
STEP = 60_000


def synthetic_panel(path):
    """A two-group minute panel over the world's days: random features, labels, and a per-row scale."""
    np = pytest.importorskip("numpy")
    from dskit.pipeline.libs.array_panel import ArrayPanelWrite
    from dskit.pipeline.node import NodeContext

    rng = np.random.default_rng(5)
    stamps = np.arange(PANEL_FIRST + STEP - 1, PANEL_LAST, STEP, dtype=np.int64)
    n = len(stamps)
    time, group = np.concatenate([stamps, stamps]), np.repeat(np.array([0, 1], dtype=np.int64), n)
    total = len(time)
    cols = {"t": time, "g": group, "ref": rng.uniform(4e-4, 9e-4, total)}
    for k in range(3):
        cols[f"f{k}"] = rng.normal(size=total)
    for h in range(1, 16):
        y = 0.3 * cols["f0"] + math.sqrt(h) * rng.normal(size=total)
        for g in (0, 1):
            y[(group == g).nonzero()[0][-h:]] = np.nan
        cols[f"y_h{h}"] = y
    ArrayPanelWrite("w", {"time": "t", "group": "g", "reference": "ref", "label_prefix": "y_h", "horizons": list(range(1, 16)),
                          "step_ms": STEP, "path": path}).run(NodeContext(name="t", asof="2026-09-03", run_dir=os.path.dirname(path)), {"columns": cols})


def tiny_fit(panel_path, run_dir):
    from dskit.pipeline.libs.panel_torch import PanelTrain
    from dskit.pipeline.node import NodeContext

    params = {"panel": {"root": "r", "source": "s", "stream": "st", "relpath": panel_path}, "assets": [0, 1], "asset_indicator": True,
              "bounds": {"train_end_ms": TRAIN_END, "val_start_ms": ms(utc(2026, 9, 1)), "val_end_ms": PANEL_LAST, "cal_days": 1, "min_run": 1},
              "context": {"prefixes": ["f"]}, "trunk": {"hidden": [8], "activation": "relu"},
              "head": {"kind": "mixture", "components": 2, "family": {"kind": "gaussian"}, "losses": [{"kind": "nll", "weight": 1.0}],
                       "min_scale": 0.1},
              "epochs": 1, "lr": 0.02, "optimizer": "torch.optim.Adam", "loader": {"batch_size": 256, "seed": 3}, "device": "cpu"}
    node = PanelTrain("fit", params)
    return node.run(NodeContext(name="fit", asof="2026-09-03", run_dir=run_dir), {})


@pytest.fixture(scope="module")
def model(tmp_path_factory):
    pytest.importorskip("torch")
    from dskit.pipeline.libs.array_panel import ArrayPanel

    base = tmp_path_factory.mktemp("w4model")
    panel = str(base / "panel")
    synthetic_panel(panel)
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(ArrayPanel, "from_source", classmethod(lambda cls, spec: cls.open(spec["relpath"])))
        out = tiny_fit(panel, str(base / "fit"))
    sidecar = json.load(open(out["artifact_path"].replace(".pt", ".json"), encoding="utf-8"))
    return {"panel": panel, "artifact": out["artifact_path"], "hash": sidecar["state_hash"], "forecaster": out["model"]}


def shipped_doc(store, tmp_path, model, *, pin=True):
    """The shipped document with only placement moved: the store root, the panel and model paths, the output path."""
    doc = copy.deepcopy(DOC)

    def reroot(node):
        if isinstance(node, dict):
            for key, value in node.items():
                if key == "root" and value == SHIPPED_ROOT:
                    node[key] = store.path
                else:
                    reroot(value)
        elif isinstance(node, list):
            for value in node:
                reroot(value)

    reroot(doc)
    gru = doc["pipeline"]["fair_gru"]["params"]
    gru["panel"]["relpath"], gru["artifact"] = model["panel"], model["artifact"]
    if pin:
        gru["state_hash"] = model["hash"]
    out = tmp_path / "w4-table"
    out.mkdir(exist_ok=True)
    doc["pipeline"]["write"]["params"]["path"] = str(out / os.path.basename(doc["pipeline"]["write"]["params"]["path"]))
    path = tmp_path / "run-w4-take.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    return path


def run(store, tmp_path, model, monkeypatch, *, pin=True):
    from dskit.pipeline.libs.array_panel import ArrayPanel

    monkeypatch.setattr(ArrayPanel, "from_source", classmethod(lambda cls, spec: cls.open(spec["relpath"])))
    document = replace(load_document(str(shipped_doc(store, tmp_path, model, pin=pin))), outputs=OutputsConfig(run_root=str(tmp_path / "runs")))
    return run_document(document, asof="2026-09-15")


@pytest.fixture
def ran(tmp_path, monkeypatch, model):
    store = build_world(tmp_path, monkeypatch)
    result = run(store, tmp_path, model, monkeypatch)
    assert result.state == "ran", (result.state, result.error)
    return store, result


def test_the_document_runs_every_node_ok_and_the_model_is_scored_beside_the_baselines(ran, model):
    _, result = ran
    assert set(result.node_states.values()) == {"ok"} and set(result.node_states) == set(NODES)
    scores = result.outputs["take_m02"]["scores"]
    assert {s["model"] for s in scores} == {"fair_gru", "fair_rms", "fair_ewma", "fair_bvol"} and {s["segment"] for s in scores} == {"w4"}
    assert {s["group"] for s in scores} == {"all", *(f"lead_minutes={lead}" for lead in LEADS)}
    census = result.outputs["fair_gru"]["census"]
    assert census["priced"] == census["rows"] == len(MARKETS) * len(LEADS) and census["state_hash"] == model["hash"]


def test_every_gru_fair_value_is_the_models_survival_at_the_strike_recomputed_independently(ran, model):
    import numpy as np
    from dskit.pipeline.libs.array_panel import ArrayPanel

    _, result = ran
    panel, forecaster = ArrayPanel.open(model["panel"]), model["forecaster"]
    stamps = {(int(g), int(t)): i for i, (g, t) in enumerate(zip(np.asarray(panel.group), np.asarray(panel.time)))}
    rows = result.outputs["fair_gru"]["records"]
    assert len(rows) == len(MARKETS) * len(LEADS)
    for row in rows:
        group = {"KXBTC15M": 0, "KXETH15M": 1}[row["series"]]
        index = stamps[(group, row["decision_ms"] - 1)]
        dist = forecaster.forecast(panel, np.array([index]), 4)
        h = row["lead_minutes"] - 1                               # horizons are 1..15, columns 0..14
        scale = float(panel.reference[index])
        z = math.log(row["floor_strike"] / row["spot_brti"]) / scale
        weights = np.exp(dist.logw[0, h].numpy().astype(float))
        weights /= weights.sum()
        cdf = sum(w * NORMAL.cdf((z - m) / s) for w, m, s in zip(weights, dist.mu[0, h].tolist(), dist.sigma[0, h].tolist()))
        assert row["fair_gru_status"] == "ok" and row["fair_gru_horizon"] == row["lead_minutes"]
        assert row["fair_gru"] == pytest.approx(1.0 - cdf, abs=1e-5), (row["ticker"], row["lead_minutes"])


def test_the_grid_is_monotone_in_trades_and_every_margin_reports_the_same_rows(ran):
    _, result = ran
    pooled = {}
    for cents in MARGINS_CENTS:
        scores = result.outputs[f"take_m{cents:02d}"]["scores"]
        cell = next(s for s in scores if s["model"] == "fair_gru" and s["group"] == "all" and s["bucket"] == "all")
        pooled[cents] = cell
        assert result.outputs[f"take_m{cents:02d}"]["summary"]["margin"] == pytest.approx(cents / 100)
    assert len({c["n"] for c in pooled.values()}) == 1, "the margin moves the trades, never the rows scored"
    trades = [pooled[c]["n_trades"] for c in MARGINS_CENTS]
    assert trades == sorted(trades, reverse=True), trades
    for cell in pooled.values():
        assert {"pnl_total", "pnl_mean", "pnl_se", "pnl_se_event", "n_trades", "hit_rate"} <= set(cell)


def test_the_run_refuses_while_the_model_hash_is_the_shipped_placeholder(tmp_path, monkeypatch, model):
    """Until runbook step 5 pins the refit's hash, the document cannot price a single row."""
    assert DOC["pipeline"]["fair_gru"]["params"]["state_hash"].startswith("<")
    result = run(build_world(tmp_path, monkeypatch), tmp_path, model, monkeypatch, pin=False)
    assert result.state != "ran" and "state_hash" in str(result.error), (result.state, result.error)


def test_a_decision_the_training_could_have_seen_stops_the_run(tmp_path, monkeypatch, model):
    """Moving the document's model to one that claims to be trained past the decisions makes the whole run refuse."""
    import torch

    from dskit.pipeline.libs.panel_torch import TorchHorizonForecaster

    store = build_world(tmp_path, monkeypatch)
    real = TorchHorizonForecaster.restore.__func__

    def late(cls, artifact, device=None):
        forecaster = real(cls, artifact, device)
        forecaster._adapter.params["bounds"]["train_end_ms"] = ms(utc(2026, 9, 2, 1, 0))
        return forecaster

    monkeypatch.setattr(TorchHorizonForecaster, "restore", classmethod(late))
    assert torch is not None
    result = run(store, tmp_path, model, monkeypatch)
    assert result.state != "ran" and "train_end" in str(result.error)


# --- the runbook's B8 snippets that touch these files run ------------------------------------------------------------------


def test_runbook_step_5_pins_the_models_hash_into_the_document_and_changes_nothing_else(tmp_path, monkeypatch):
    from test_runbook import snippet

    work = tmp_path / "pipeline_runs" / "zoo-production-w4"
    work.mkdir(parents=True)
    path = f"models/{LANE}/block-1-{OVERLAY['forward_start']}"
    models = [{"lane": LANE, "block": 2, "path": "elsewhere", "state_hash": "no"}, {"lane": LANE, "block": 1, "path": path, "state_hash": "a" * 64}]
    (work / "models.json").write_text(json.dumps({"models": models}), encoding="utf-8")
    (tmp_path / "configs").mkdir()
    shipped_text = open(os.path.join(CONFIGS, "run-w4-take.json"), encoding="utf-8").read()
    assert shipped_text == json.dumps(DOC, indent=2) + "\n", "the file is in the form step 5 writes, so its edit is one line"
    (tmp_path / "configs" / "run-w4-take.json").write_text(shipped_text, encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    exec(compile(snippet("# 5. Pin the refit"), "B8-5", "exec"), {})
    pinned = json.loads((tmp_path / "configs" / "run-w4-take.json").read_text(encoding="utf-8"))
    assert pinned["pipeline"]["fair_gru"]["params"]["state_hash"] == "a" * 64
    pinned["pipeline"]["fair_gru"]["params"]["state_hash"] = DOC["pipeline"]["fair_gru"]["params"]["state_hash"]
    assert pinned == DOC


def test_runbook_step_7_reads_every_margin_with_its_t_statistic(ran, tmp_path, monkeypatch, capsys):
    from test_runbook import snippet

    os.symlink(tmp_path / "runs", tmp_path / "pipeline_runs")
    monkeypatch.chdir(tmp_path)
    exec(compile(snippet("# 7. Read it"), "B8-7", "exec"), {})
    lines = capsys.readouterr().out.splitlines()
    assert lines and {line.split()[1] for line in lines} <= {f"{c / 100:.2f}" for c in MARGINS_CENTS}, lines
    assert any("fair_gru" in line for line in lines) or any("fair_rms" in line for line in lines), "the model columns are printed"
    assert all(" t " in line and "hit" in line for line in lines)


def test_runbook_step_8_compares_models_on_common_trades_and_selects_on_the_executable_price(ran, tmp_path, monkeypatch, capsys):
    import glob
    import re
    import shutil

    from test_runbook import snippet

    _, result = ran
    table = tmp_path / "home" / "data" / "crypto_trading" / "w4-take"
    table.mkdir(parents=True)
    for source in glob.glob(str(tmp_path / "w4-table" / "decision_features-*.jsonl")):
        shutil.copy(source, table)
    (tmp_path / "configs").mkdir(exist_ok=True)
    shutil.copy(os.path.join(CONFIGS, "run-w4-take.json"), tmp_path / "configs" / "run-w4-take.json")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.chdir(tmp_path)
    exec(compile(snippet("# 8. The same trades"), "B8-8", "exec"), {})
    out = capsys.readouterr().out.splitlines()
    pattern = re.compile(r"(mid|exec)\s+(own|common)\s+margin ([\d.]+) (\w+)\s+trades\s+(\d+) pnl\s+(-?[\d.]+) mean ([+-][\d.]+) se (\S+) t (\S+) hit ([\d.]+)")
    cells = {m.group(1, 2, 3, 4): m for m in map(pattern.match, out) if m}
    assert out[0].startswith("rows with a two-sided quote") and cells
    assert any("fair_gru" in line for line in out[:1])
    # the mid rule on a model's own rows is the dskit scorer's take rule: same trades, same total, same day-block error
    compared = 0
    for cents in MARGINS_CENTS:
        for cell in result.outputs[f"take_m{cents:02d}"]["scores"]:
            if cell["group"] == "all" and cell["bucket"] == "all":
                mine = cells.get(("mid", "own", f"{cents / 100:.2f}", cell["model"]))
                if cell["n_trades"] == 0:
                    assert mine is None
                else:
                    compared += 1
                    assert int(mine.group(5)) == cell["n_trades"] and float(mine.group(6)) == pytest.approx(cell["pnl_total"], abs=0.006)
                    if cell["pnl_se"] is not None:
                        assert float(mine.group(8)) == pytest.approx(cell["pnl_se"], abs=6e-5)
    assert compared >= 4, "the fixture trades, so the comparison with the scorer is not vacuous"
    # a common set is a subset of every model's own set; an exec trade pays a net edge above the margin by construction
    for (rule, kind, margin, model), cell in cells.items():
        if kind == "common":
            assert int(cell.group(5)) <= int(cells[(rule, "own", margin, model)].group(5))
    rows = result.outputs["fees"]["records"]
    for margin in (0.0, 0.02):
        want = sum(1 for r in rows if r["two_sided"] and r["fair_gru"] is not None and (
            r["fair_gru"] - r["yes_ask"] - r["fee_buy_yes"] > margin or r["yes_bid"] - r["fair_gru"] - r["fee_buy_no"] > margin))
        got = cells.get(("exec", "own", f"{margin:.2f}", "fair_gru"))
        assert (int(got.group(5)) if got else 0) == want
