"""The production pipeline: the forward window and its W2 refusal, the block lanes, and a saved model per retrain.

The persistence tests run two REAL tiny CPU fits (a few hundred synthetic hourly rows, one epoch) through dskit's
``PanelTrain``, the existing seam that writes ``model.pt`` + ``model.json``; the workflow around them is fake trees."""

import json
import os
import shutil
from datetime import date, datetime, timedelta, timezone

import pytest
from zoo_fakes import write_batch_status, write_lane

from crypto_trading.zoo_lanes import main as lanes_main
from crypto_trading.zoo_pick import file_sha256, hparams_sha256, load_recipe
from crypto_trading.zoo_production import ForwardWindow, ModelIndex, ProductionPlan, w5_start_ms

CHILD_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIGS = os.path.join(CHILD_ROOT, "configs")
RECIPE_PATH = os.path.join(CONFIGS, "zoo-lanes-recipe.json")
W2_START, W2_END = date(2026, 5, 2), date(2026, 8, 1)   # plan: W2 is 2026-05-02 to 2026-08-01 (exclusive)


@pytest.fixture(scope="module")
def loaded():
    return load_recipe(RECIPE_PATH)


def window(loaded, **over):
    return ForwardWindow(loaded[0]["production"], loaded[0]["w2"]["bounds"], loaded[1].base["folds"]["embargo_days"],
                         w5_start_ms(loaded[0]["production"], CONFIGS), over)


def winner(cell, scope):
    model, loss = cell.split("-")
    suffixes = {"pooled": ["pooled"], "unpooled": ["btc", "eth"]}[scope]
    return {"cell": f"{cell}-{scope}", "model": model, "loss": loss, "scope": scope, "lanes": [f"{cell}-{s}" for s in suffixes]}


def records(loaded, cell="gru-crps_tail", scope="pooled", **fit_over):
    recipe, lanes, _, _ = loaded
    sha = {"path": RECIPE_PATH, "sha256": file_sha256(RECIPE_PATH)}
    won = winner(cell, scope)
    params = {}
    for lane in won["lanes"]:
        fit = json.loads(json.dumps(lanes.cells()[lane]["fit"]))
        fit.update(fit_over)
        params[lane] = fit
    pick = {"winner": won, "recipe": sha}
    frozen = {"winner": "c", "pick_winner": won, "recipe": sha, "params": params,
              "hparams_sha256": {lane: hparams_sha256(fit) for lane, fit in params.items()}}
    return pick, frozen


def test_the_defaults_are_w2_and_agree_with_the_recipes_w2_bounds(loaded):
    production, bounds = loaded[0]["production"], loaded[0]["w2"]["bounds"]
    as_date = lambda ms: datetime.fromtimestamp(ms / 1000, tz=timezone.utc).date()  # noqa: E731
    assert date.fromisoformat(production["forward_start"]) == as_date(bounds["val_start_ms"]) == W2_START
    assert date.fromisoformat(production["evaluate_through"]) == as_date(bounds["val_end_ms"] + 1) == W2_END
    assert production["retrain_days"] == 60 and production["allow_past_w2"] is False


def test_the_default_window_is_two_blocks_the_last_clipped_at_the_end_of_w2(loaded):
    blocks = window(loaded).blocks()
    assert [(b["name"], b["first"], b["val_days"], b["val_end"], b["train_end"]) for b in blocks] == [
        ("b01", "2026-05-02", 60, "2026-07-01", "2026-05-01"), ("b02", "2026-07-01", 31, "2026-08-01", "2026-06-30")]
    assert sum(b["val_days"] for b in blocks) == (W2_END - W2_START).days == 91


def test_the_retrain_cadence_changes_the_blocks_but_never_the_end(loaded):
    for days, count, last in ((30, 4, 1), (91, 1, 91), (200, 1, 91)):
        blocks = window(loaded, retrain_days=days).blocks()
        assert len(blocks) == count and blocks[-1]["val_end"] == "2026-08-01" and blocks[-1]["val_days"] == last


def test_a_window_past_w2_is_refused_unless_the_recipe_flag_is_true(loaded):
    with pytest.raises(ValueError, match="W4 and W5 are reserved"):
        window(loaded, evaluate_through="2026-08-02")
    with pytest.raises(ValueError, match="W4 and W5 are reserved"):
        window(loaded, evaluate_through="2026-09-15")
    allowed = window(loaded, evaluate_through="2026-09-15", allow_past_w2=True)
    assert allowed.blocks()[-1]["val_end"] == "2026-09-15"          # W4 is open with the flag, W5 starts there
    assert window(loaded, evaluate_through="2026-08-01").through == W2_END   # exactly W2's end is fine


def test_a_forward_start_before_w2_is_refused_even_with_the_flag(loaded):
    with pytest.raises(ValueError, match="before W2's start"):
        window(loaded, forward_start="2026-05-01", allow_past_w2=True)


def test_bad_cadence_and_empty_windows_are_refused(loaded):
    for over in ({"retrain_days": 0}, {"retrain_days": True}, {"evaluate_through": "2026-05-02"}, {"allow_past_w2": "yes"}):
        with pytest.raises(ValueError):
            window(loaded, **over)


def test_the_block_lanes_are_the_frozen_model_and_the_scopes_baselines_one_set_per_block(loaded):
    pick, frozen = records(loaded, lr=0.123)
    manifest = ProductionPlan(loaded[0], loaded[1], pick, frozen, window(loaded)).manifest("../production-template.json")
    args = manifest["args"]
    assert args["arms"] == [f"{b}-{lane}" for b in ("b01", "b02") for lane in ("empirical-pooled", "ewma_student-pooled", "gru-crps_tail-pooled")]
    assert args["cell"]["b02-gru-crps_tail-pooled"]["fit"]["lr"] == 0.123, "the frozen hyperparameters, not the zoo's"
    assert args["block"]["b02-empirical-pooled"] == {"k": 2, "first": "2026-07-01", "val_days": 31, "embargo_days": 1}
    assert "folds" not in args and manifest["lanes"]["keyed"] == ["cell", "block"] and "folds" not in manifest["steps"]["fit"]["in"]
    assert args["production"]["hparams_sha256"] == {"gru-crps_tail-pooled": hparams_sha256(frozen["params"]["gru-crps_tail-pooled"], ("row_stride",))}


def test_an_unpooled_pick_runs_both_asset_lanes_and_both_baselines_per_asset(loaded):
    pick, frozen = records(loaded, "patchtst-nll", "unpooled")
    manifest = ProductionPlan(loaded[0], loaded[1], pick, frozen, window(loaded)).manifest("t.json")
    per_block = [a for a in manifest["args"]["arms"] if a.startswith("b01-")]
    assert sorted(per_block) == sorted(f"b01-{x}" for x in ("patchtst-nll-btc", "patchtst-nll-eth", "empirical-btc", "empirical-eth",
                                                              "ewma_student-btc", "ewma_student-eth"))


def test_every_lane_expands_to_a_one_fold_walk_forward_that_ends_inside_the_window(loaded):
    import importlib

    from dskit.pipeline.document import PipelineDocument
    from dskit.pipeline.libs.panel_score import PanelScore
    from dskit.pipeline.workflow import lane_flows

    pick, frozen = records(loaded)
    manifest = ProductionPlan(loaded[0], loaded[1], pick, frozen, window(loaded)).manifest("production-template.json")
    flows = lane_flows(manifest, CONFIGS)
    assert len(flows) == 6
    for flow in flows:
        doc = flow.expanded("fit", strict=False, rnd=0)
        PipelineDocument.from_obj(doc)
        wf = doc["walkforward"]
        cutoff = date.fromisoformat(wf["folds"][0])
        assert len(wf["folds"]) == 1 and cutoff >= W2_START and cutoff + timedelta(days=wf["val_days"]) <= W2_END
        fit = doc["pipeline"]["fit"]
        module, _, name = fit["uses"].partition(":")
        assert getattr(importlib.import_module(module), name).validate_params(fit["params"]) == [], flow.lane
        assert PanelScore.validate_params(doc["pipeline"]["score"]["params"]) == [], flow.lane
        assert fit["params"]["bounds"]["val_end_ms"] == "$splits.val_end_ms"


def test_the_manifest_cli_refuses_a_window_past_w2_and_a_mismatched_freeze(loaded, tmp_path):
    frozen = frozen_files(loaded, tmp_path)
    base = ["production-manifest", "--pick", str(tmp_path / "pick.json"), "--frozen", str(tmp_path / "frozen.json"), "--out", str(tmp_path / "m.json")]
    with pytest.raises(ValueError, match="W4 and W5 are reserved"):
        lanes_main(base + ["--evaluate-through", "2026-09-15"])
    assert not (tmp_path / "m.json").exists()
    assert lanes_main(base + ["--evaluate-through", "2026-09-15", "--allow-past-w2", "true"]) == 0
    assert json.loads((tmp_path / "m.json").read_text(encoding="utf-8"))["args"]["production"]["blocks"][-1]["val_end"] == "2026-09-15"
    other = json.loads(json.dumps(frozen))
    other["pick_winner"] = winner("mlp-crps", "pooled")
    (tmp_path / "frozen.json").write_text(json.dumps(other), encoding="utf-8")
    with pytest.raises(ValueError, match="hpo-frozen.json is for"):
        lanes_main(base)


# --- a saved model per retrain ---------------------------------------------------------------------------------------

STEP = 3_600_000
T0 = 1_700_000_000_000 - (1_700_000_000_000 % STEP)
WINDOW, STRIDE = 8, 2
MIN_RUN = (WINDOW - 1) * STRIDE + 1
GROUPS = {10: 420, 20: 360}


def tiny_panel(tmp_path):
    np = pytest.importorskip("numpy")
    from dskit.pipeline.libs.array_panel import ArrayPanelWrite
    from dskit.pipeline.node import NodeContext

    rng = np.random.default_rng(0)
    time = np.concatenate([T0 + np.arange(n, dtype=np.int64) * STEP for n in GROUPS.values()])
    group = np.concatenate([np.full(n, g, dtype=np.int64) for g, n in GROUPS.items()])
    n = len(time)
    cols = {"t": time, "g": group, "ref": rng.uniform(0.5, 2.0, n)}
    for k in range(3):
        cols[f"f{k}"] = rng.normal(size=n)
    cols["seq__a"], cols["seq__b"] = rng.normal(size=n), rng.normal(size=n)
    for h in (1, 3):
        y = 0.7 * cols["seq__a"] + 0.3 * rng.normal(size=n)
        for g in GROUPS:
            y[np.flatnonzero(group == g)[-h:]] = np.nan
        cols[f"y_{h}"] = y
    ArrayPanelWrite("w", {"time": "t", "group": "g", "reference": "ref", "label_prefix": "y_", "horizons": [1, 3], "step_ms": STEP,
                          "path": str(tmp_path / "panel")}).run(NodeContext(name="t", asof="2026-01-01", run_dir=str(tmp_path)), {"columns": cols})
    return str(tmp_path / "panel")


def tiny_params(path, train_end_row):
    return {"panel": {"root": "r", "source": "s", "stream": "st", "relpath": path}, "assets": [10, 20], "asset_indicator": True,
            "bounds": {"train_end_ms": T0 + train_end_row * STEP, "val_start_ms": T0 + (train_end_row + 10) * STEP,
                       "val_end_ms": T0 + (train_end_row + 40) * STEP, "cal_days": 2, "min_run": MIN_RUN},
            "sequence": {"columns": {"prefixes": ["seq__"]}, "window": WINDOW, "stride": STRIDE}, "context": {"prefixes": ["f"]},
            "encoder": {"arch": "gru", "width": 6, "arch_params": {"hidden_size": 6}}, "trunk": {"hidden": [8], "activation": "relu"},
            "head": {"kind": "mixture", "components": 2, "family": {"kind": "gaussian"}, "losses": [{"kind": "nll", "weight": 1.0}],
                     "min_scale": 0.1},
            "epochs": 1, "lr": 0.02, "optimizer": "torch.optim.Adam", "loader": {"batch_size": 64, "seed": 3}, "device": "cpu"}


@pytest.fixture
def forward_run(tmp_path, loaded, monkeypatch):
    """Two retrain blocks of a pooled GRU pick: real tiny fits for the model lane, fake artifacts for the baselines."""
    pytest.importorskip("torch")
    from dskit.pipeline.libs.array_panel import ArrayPanel
    from dskit.pipeline.libs.panel_torch import PanelTrain
    from dskit.pipeline.node import NodeContext

    monkeypatch.setattr(ArrayPanel, "from_source", classmethod(lambda cls, spec: cls.open(spec["relpath"])))
    recipe, lanes, template, _ = loaded
    pick, frozen = records(loaded)
    manifest = ProductionPlan(recipe, lanes, pick, frozen, window(loaded)).manifest("production-template.json")
    root = tmp_path / "train"
    panel = tiny_panel(tmp_path)
    fits = {}
    for block in manifest["args"]["production"]["blocks"]:
        for lane in ("gru-crps_tail-pooled", "empirical-pooled", "ewma_student-pooled"):
            name = f"{block['name']}-{lane}"
            score = {"gru-crps_tail-pooled": 1.0, "empirical-pooled": 1.4, "ewma_student-pooled": 1.2}[lane] + 0.1 * block["k"]
            write_lane(str(root), recipe, name, [block["first"]], [score], [100 * block["val_days"]], template["walkforward"]["objective"],
                       extra_files=None if lane.startswith("gru") else {"state.json": json.dumps({"lane": name})},
                       fit=manifest["args"]["cell"][name]["fit"])
            if lane.startswith("gru"):
                run = os.path.join(recipe["lane_runs"].format(root=str(root), lane=name), f"{name}-wf-{block['first']}-0000")
                node = PanelTrain("fit", tiny_params(panel, 300 + 20 * block["k"]))
                fits[block["name"]] = (node, node.run(NodeContext(name="fit", asof="2026-01-01", run_dir=run), {}), panel, run)
    write_batch_status(str(root), manifest["args"]["arms"])
    return manifest, str(root), fits, tmp_path / "out"


def test_every_retrain_is_saved_under_a_stable_block_path_and_listed_in_the_index(loaded, forward_run):
    recipe, _, template, _ = loaded
    manifest, root, fits, out = forward_run
    production_template = json.load(open(os.path.join(CONFIGS, "production-template.json"), encoding="utf-8"))
    index = ModelIndex(recipe, production_template, manifest, root, records(loaded)[1])
    entries = index.persist(str(out))
    listed = json.load(open(out / "models.json", encoding="utf-8"))["models"]
    assert listed == entries and len(entries) == 6
    assert {(e["lane"], e["block"]) for e in entries} == {(lane, k) for lane in ("gru-crps_tail-pooled", "empirical-pooled", "ewma_student-pooled") for k in (1, 2)}
    gru = {e["block"]: e for e in entries if e["lane"] == "gru-crps_tail-pooled"}
    assert gru[1]["path"] == os.path.join("models", "gru-crps_tail-pooled", "block-1-2026-05-02")
    assert gru[2]["path"] == os.path.join("models", "gru-crps_tail-pooled", "block-2-2026-07-01")
    assert gru[1]["train_end"] == "2026-05-01" and gru[2]["train_end"] == "2026-06-30"
    assert gru[1]["hparams_sha256"] == gru[2]["hparams_sha256"] == hparams_sha256(manifest["args"]["cell"]["b01-gru-crps_tail-pooled"]["fit"], ("row_stride",))
    assert gru[1]["row_stride"] == gru[2]["row_stride"] == 1
    assert set(gru[1]["files"]) == {"model.pt", "model.json", "training_curve.json"} and gru[1]["files"] != gru[2]["files"]
    for entry in gru.values():
        assert entry["files"]["model.pt"] == file_sha256(str(out / entry["path"] / "model.pt"))
        sidecar = json.load(open(out / entry["path"] / "model.json", encoding="utf-8"))
        assert entry["state_hash"] == sidecar["state_hash"] and entry["sha256"]
        assert set(sidecar["adapter_state"]["panel"])  # scaler, column names and band hash travel with the weights
        assert sidecar["params"]["sequence"]["window"] == WINDOW and sidecar["params"]["assets"] == [10, 20]
    base = next(e for e in entries if e["lane"] == "empirical-pooled" and e["block"] == 2)
    assert base["state_hash"] is None and "state.json" in base["files"]


def test_a_saved_model_loads_and_forecasts_exactly_as_the_retrain_did(loaded, forward_run):
    torch = pytest.importorskip("torch")
    from dskit.pipeline.libs.array_panel import ArrayPanel, PanelBands
    from dskit.pipeline.libs.panel_torch import PanelTrain
    from dskit.pipeline.node import NodeContext

    manifest, root, fits, out = forward_run
    ModelIndex(loaded[0], json.load(open(os.path.join(CONFIGS, "production-template.json"), encoding="utf-8")), manifest, root, records(loaded)[1]).persist(str(out))
    for k, name in ((1, "b01"), (2, "b02")):
        node, trained, panel_path, _ = fits[name]
        saved = out / "models" / "gru-crps_tail-pooled" / f"block-{k}-{manifest['args']['production']['blocks'][k - 1]['first']}" / "model.pt"
        restored = PanelTrain("again", node.params, mode="load", artifact=str(saved)).run(
            NodeContext(name="load", asof="2026-01-01", run_dir=str(out / f"load{k}")), {})
        assert restored["signal"].loaded
        panel = ArrayPanel.open(panel_path)
        bands = PanelBands(panel, assets=[10, 20], **node.params["bounds"])
        rows = bands.val_idx[:10]
        a, b = trained["model"].forecast(panel, rows, 10), restored["model"].forecast(panel, rows, 10)
        assert torch.equal(a.mu, b.mu) and torch.equal(a.sigma, b.sigma) and torch.equal(a.logw, b.logw)


def test_a_retrain_that_saved_nothing_and_drifting_hyperparameters_are_refused(loaded, forward_run, tmp_path):
    manifest, root, fits, out = forward_run
    template = json.load(open(os.path.join(CONFIGS, "production-template.json"), encoding="utf-8"))
    shutil.rmtree(os.path.join(fits["b02"][3], "artifacts", "fit"))
    with pytest.raises(ValueError, match="saved no model"):
        ModelIndex(loaded[0], template, manifest, root, records(loaded)[1]).persist(str(out))
    drifted = json.loads(json.dumps(manifest))
    drifted["args"]["cell"]["b01-gru-crps_tail-pooled"]["fit"]["lr"] = 9.0
    with pytest.raises(ValueError, match="realized config differs from the manifest"):
        ModelIndex(loaded[0], template, drifted, root, records(loaded)[1]).persist(str(out))


def test_the_forward_report_scores_the_model_against_both_baselines_row_weighted(loaded, forward_run):
    manifest, root, _, _ = forward_run
    template = json.load(open(os.path.join(CONFIGS, "production-template.json"), encoding="utf-8"))
    report = ModelIndex(loaded[0], template, manifest, root, records(loaded)[1]).report()
    rows = (6000, 3100)
    scores = {"gru-crps_tail": (1.1, 1.2), "empirical": (1.5, 1.6), "ewma_student": (1.3, 1.4)}
    for name, (a, b) in scores.items():
        assert report["overall"][name] == pytest.approx((a * rows[0] + b * rows[1]) / sum(rows))
    assert report["model_beats_baselines"] is True and report["rows"] == sum(rows)
    assert [b["block"] for b in report["blocks"]] == [1, 2] and report["blocks"][0]["best_baseline"] == "ewma_student"
    assert report["blocks"][0]["model_minus_best_baseline"] == pytest.approx(-0.2)
    assert "Model gru-crps_tail" in ModelIndex(loaded[0], template, manifest, root, records(loaded)[1]).markdown(report)


# --- W5 is never open; the freeze is verified; the realized config is what is checked ----------------------------------


def test_w5s_start_is_read_from_the_one_file_that_owns_the_cut_and_is_the_plans_date(loaded):
    cut = w5_start_ms(loaded[0]["production"], CONFIGS)
    assert datetime.fromtimestamp(cut / 1000, tz=timezone.utc).date() == date(2026, 9, 15)
    features = json.load(open(os.path.join(CONFIGS, "run-features-15m.json"), encoding="utf-8"))
    segments = features["pipeline"]["kill_test"]["params"]["segments"]
    assert cut == segments["heldout"]["start_ms"] == segments["development"]["end_ms"]
    assert set(loaded[0]["production"]["w5"]) == {"file", "path", "notes"} and "1789430400000" not in json.dumps(loaded[0])


def test_no_setting_opens_w5_and_the_flag_opens_w4_only(loaded):
    for flag in (True, False):
        with pytest.raises(ValueError, match="W5's start"):
            window(loaded, evaluate_through="2026-10-08", allow_past_w2=flag)
        with pytest.raises(ValueError, match="W5's start"):
            window(loaded, evaluate_through="2026-09-16", allow_past_w2=flag)
    assert window(loaded, evaluate_through="2026-09-15", allow_past_w2=True).through == date(2026, 9, 15)
    with pytest.raises(ValueError, match="W4 and W5 are reserved"):
        window(loaded, evaluate_through="2026-09-15")
    with pytest.raises(ValueError, match="W4 and W5 are reserved"):
        window(loaded, evaluate_through="2026-08-02")
    recipe_flag = {**loaded[0]["production"], "evaluate_through": "2026-10-08", "allow_past_w2": True}      # the recipe itself cannot open it
    with pytest.raises(ValueError, match="W5's start"):
        ForwardWindow(recipe_flag, loaded[0]["w2"]["bounds"], 1, w5_start_ms(loaded[0]["production"], CONFIGS))


def test_the_cli_refuses_w5_even_with_the_flag(loaded, tmp_path):
    pick, frozen = records(loaded)
    (tmp_path / "pick.json").write_text(json.dumps(pick), encoding="utf-8")
    (tmp_path / "frozen.json").write_text(json.dumps({**frozen, "pick_file": {"path": "p", "sha256": file_sha256(str(tmp_path / "pick.json"))}}), encoding="utf-8")
    argv = ["production-manifest", "--pick", str(tmp_path / "pick.json"), "--frozen", str(tmp_path / "frozen.json"), "--out", str(tmp_path / "m.json")]
    with pytest.raises(ValueError, match="W5's start"):
        lanes_main(argv + ["--evaluate-through", "2026-10-08", "--allow-past-w2", "true"])
    assert not (tmp_path / "m.json").exists()
    assert lanes_main(argv + ["--evaluate-through", "2026-09-15", "--allow-past-w2", "true"]) == 0


def frozen_files(loaded, tmp_path):
    pick, frozen = records(loaded)
    (tmp_path / "pick.json").write_text(json.dumps(pick), encoding="utf-8")
    frozen = {**frozen, "pick_file": {"path": str(tmp_path / "pick.json"), "sha256": file_sha256(str(tmp_path / "pick.json"))}}
    (tmp_path / "frozen.json").write_text(json.dumps(frozen), encoding="utf-8")
    return frozen


def test_an_edited_frozen_file_is_refused_by_every_step_that_loads_it(loaded, tmp_path):
    frozen = frozen_files(loaded, tmp_path)
    lane = next(iter(frozen["params"]))
    frozen["params"][lane]["lr"] = 0.5
    (tmp_path / "frozen.json").write_text(json.dumps(frozen), encoding="utf-8")
    common = ["--pick", str(tmp_path / "pick.json"), "--frozen", str(tmp_path / "frozen.json")]
    for argv in (["production-manifest", *common, "--out", str(tmp_path / "m.json")],
                 ["frozen-manifest", *common, "--out", str(tmp_path / "m.json")],
                 ["hpo-evaluate", *common, "--batch", str(tmp_path / "batch.json"), "--out", str(tmp_path / "e.json")]):
        with pytest.raises(ValueError, match="no longer hash to the recorded hparams_sha256"):
            lanes_main(argv)
    pick, other = records(loaded)
    del other["hparams_sha256"]
    with pytest.raises(ValueError, match="same non-empty set of lanes"):
        ProductionPlan(loaded[0], loaded[1], pick, other, window(loaded))


def test_a_freeze_from_another_pick_is_refused(loaded, tmp_path):
    frozen_files(loaded, tmp_path)
    (tmp_path / "pick.json").write_text(json.dumps({**json.loads((tmp_path / "pick.json").read_text(encoding="utf-8")), "extra": 1}), encoding="utf-8")
    common = ["--pick", str(tmp_path / "pick.json"), "--frozen", str(tmp_path / "frozen.json")]
    for argv in (["production-manifest", *common, "--out", str(tmp_path / "m.json")],
                 ["hpo-evaluate", *common, "--batch", str(tmp_path / "batch.json"), "--out", str(tmp_path / "e.json")]):
        with pytest.raises(ValueError, match="is not the zoo pick"):
            lanes_main(argv)


def _persist(loaded, forward_run):
    manifest, root, _, out = forward_run
    template = json.load(open(os.path.join(CONFIGS, "production-template.json"), encoding="utf-8"))
    return ModelIndex(loaded[0], template, manifest, root, records(loaded)[1]).persist(str(out))


def _run_dir(loaded, forward_run, block, lane="gru-crps_tail-pooled"):
    manifest, root, _, _ = forward_run
    name = f"{block['name']}-{lane}"
    return os.path.join(loaded[0]["lane_runs"].format(root=root, lane=name), f"{name}-wf-{block['first']}-0000")


def test_the_realized_config_not_the_manifest_is_what_is_checked_against_the_freeze(loaded, forward_run):
    manifest = forward_run[0]
    block = manifest["args"]["production"]["blocks"][1]
    path = os.path.join(_run_dir(loaded, forward_run, block), "config.json")
    config = json.load(open(path, encoding="utf-8"))
    config["pipeline"]["fit"]["params"]["lr"] = 0.999      # the run drifted; the manifest and freeze are untouched
    json.dump(config, open(path, "w", encoding="utf-8"))
    with pytest.raises(ValueError, match="realized config differs from the manifest"):
        _persist(loaded, forward_run)


def test_a_realized_config_that_matches_the_manifest_but_not_the_freeze_is_refused(loaded, forward_run):
    manifest = forward_run[0]
    manifest["args"]["production"]["hparams_sha256"]["gru-crps_tail-pooled"] = "0" * 64     # a stale freeze hash
    with pytest.raises(ValueError, match="differ from the frozen ones"):
        _persist(loaded, forward_run)


def test_bounds_are_the_only_thing_the_hyperparameter_hash_ignores(loaded):
    fit = records(loaded)[1]["params"]["gru-crps_tail-pooled"]
    moved = json.loads(json.dumps(fit))
    moved["bounds"]["train_end_ms"] = 1
    assert hparams_sha256(moved) == hparams_sha256(fit)
    moved["trunk"]["dropout"] = 0.77
    assert hparams_sha256(moved) != hparams_sha256(fit)


@pytest.mark.parametrize("missing", ["model.pt", "model.json"])
def test_a_model_lane_without_its_weights_or_sidecar_is_refused_though_the_directory_is_not_empty(loaded, forward_run, missing):
    block = forward_run[0]["args"]["production"]["blocks"][0]
    os.remove(os.path.join(_run_dir(loaded, forward_run, block), "artifacts", "fit", missing))
    with pytest.raises(ValueError, match="saved no model"):
        _persist(loaded, forward_run)


def test_a_baseline_lane_needs_only_a_non_empty_artifact_directory(loaded, forward_run):
    assert len(_persist(loaded, forward_run)) == 6


def test_a_stale_block_directory_is_cleared_before_the_copy(loaded, forward_run):
    out = forward_run[3]
    stale = out / "models" / "gru-crps_tail-pooled" / "block-1-2026-05-02" / "left-over.bin"
    stale.parent.mkdir(parents=True)
    stale.write_text("old", encoding="utf-8")
    entries = _persist(loaded, forward_run)
    assert not stale.exists() and "left-over.bin" not in next(e for e in entries if e["lane"] == "gru-crps_tail-pooled")["files"]


def test_a_production_lane_must_be_exactly_one_fold_at_its_blocks_cutoff(loaded, forward_run):
    recipe = loaded[0]
    manifest, root, _, _ = forward_run
    block = manifest["args"]["production"]["blocks"][0]
    name = f"{block['name']}-empirical-pooled"
    template = json.load(open(os.path.join(CONFIGS, "production-template.json"), encoding="utf-8"))
    shutil.rmtree(recipe["lane_runs"].format(root=root, lane=name))
    write_lane(root, recipe, name, [block["first"], "2026-06-01"], [1.0, 1.0], [10, 10], template["walkforward"]["objective"],
               extra_files={"state.json": "{}"}, fit=manifest["args"]["cell"][name]["fit"])
    with pytest.raises(ValueError, match="one fold at the block's cutoff"):
        _persist(loaded, forward_run)


def test_each_realized_config_is_checked_against_hpo_frozen_json_itself(loaded, forward_run):
    manifest, root, _, out = forward_run
    template = json.load(open(os.path.join(CONFIGS, "production-template.json"), encoding="utf-8"))
    other = records(loaded, lr=0.4242)[1]          # a different, internally consistent freeze: the manifest and runs are the original
    with pytest.raises(ValueError, match=r"differ from hpo-frozen.json's"):
        ModelIndex(loaded[0], template, manifest, root, other).persist(str(out))
    tampered = records(loaded)[1]
    tampered["params"]["gru-crps_tail-pooled"]["lr"] = 0.5
    with pytest.raises(ValueError, match="no longer hash"):
        ModelIndex(loaded[0], template, manifest, root, tampered)


def test_a_block_directory_that_cannot_be_cleared_is_a_refusal_not_a_silent_skip(loaded, forward_run, monkeypatch):
    stale = forward_run[3] / "models" / "gru-crps_tail-pooled" / "block-1-2026-05-02"
    stale.mkdir(parents=True)

    def fail(path):
        raise PermissionError("denied")
    monkeypatch.setattr(shutil, "rmtree", fail)
    with pytest.raises(ValueError, match="cannot clear the previous block directory"):
        _persist(loaded, forward_run)


def test_the_production_pipeline_hands_the_freeze_to_the_index_step():
    manifest = json.load(open(os.path.join(CONFIGS, "workflow-production.json"), encoding="utf-8"))
    assert "--frozen {in.frozen}" in manifest["registry"]["index"]["command"] and manifest["steps"]["index"]["in"]["frozen"] == "$args.frozen"


# --- the production retrain trains on every minute ---------------------------------------------------------------------


def _differences(a, b, path=""):
    """Return the dotted paths where two nested dicts differ."""
    if isinstance(a, dict) and isinstance(b, dict):
        return [d for k in sorted(set(a) | set(b)) for d in _differences(a.get(k), b.get(k), f"{path}.{k}" if path else k)]
    return [] if a == b else [path]


def test_the_recipe_declares_the_production_stride_once_and_it_is_every_minute(loaded):
    assert loaded[0]["production"]["row_stride"] == 1
    assert loaded[1].base["training"]["row_stride"] == 5, "the zoo and the HPO keep their stride"


def test_a_stride_1_manifest_changes_the_frozen_fit_in_row_stride_and_nothing_else(loaded):
    pick, frozen = records(loaded)
    assert frozen["params"]["gru-crps_tail-pooled"]["row_stride"] == 5
    manifest = ProductionPlan(loaded[0], loaded[1], pick, frozen, window(loaded)).manifest("production-template.json")
    cells = manifest["args"]["cell"]
    for block in ("b01", "b02"):
        fit = cells[f"{block}-gru-crps_tail-pooled"]["fit"]
        assert fit["row_stride"] == 1
        assert _differences(fit, frozen["params"]["gru-crps_tail-pooled"]) == ["row_stride"]
        assert "row_stride" not in cells[f"{block}-empirical-pooled"]["fit"]
        assert cells[f"{block}-gru-crps_tail-pooled"]["score"] == loaded[1].cells()["gru-crps_tail-pooled"]["score"], "scoring is unchanged"
    assert manifest["args"]["production"]["row_stride"] == 1


def test_the_declared_stride_must_be_a_positive_integer(loaded):
    pick, frozen = records(loaded)
    for bad in (0, -1, 1.5, True, None):
        recipe = {**loaded[0], "production": {**loaded[0]["production"], "row_stride": bad}}
        with pytest.raises(ValueError, match="row_stride must be an integer >= 1"):
            ProductionPlan(recipe, loaded[1], pick, frozen, window(loaded))


def _mutate_realized(loaded, forward_run, **changes):
    block = forward_run[0]["args"]["production"]["blocks"][0]
    path = os.path.join(_run_dir(loaded, forward_run, block), "config.json")
    config = json.load(open(path, encoding="utf-8"))
    config["pipeline"]["fit"]["params"].update(changes)
    json.dump(config, open(path, "w", encoding="utf-8"))
    manifest = forward_run[0]
    manifest["args"]["cell"][f"{block['name']}-gru-crps_tail-pooled"]["fit"].update(changes)   # the manifest agrees with the run


def test_a_production_run_that_trained_on_stride_5_is_refused(loaded, forward_run):
    _mutate_realized(loaded, forward_run, row_stride=5)
    with pytest.raises(ValueError, match=r"realized row_stride 5 is not the declared production.row_stride 1"):
        _persist(loaded, forward_run)


def test_a_mutated_lr_is_still_refused_under_the_stride_override(loaded, forward_run):
    _mutate_realized(loaded, forward_run, lr=0.999)
    with pytest.raises(ValueError, match="differ from the frozen ones"):
        _persist(loaded, forward_run)


def test_the_index_refuses_a_manifest_built_with_another_stride_than_the_recipe_declares(loaded, forward_run):
    forward_run[0]["args"]["production"]["row_stride"] = 5
    with pytest.raises(ValueError, match="recipe declares 1"):
        _persist(loaded, forward_run)


def test_the_pipeline_notes_describe_a_flat_args_overlay():
    for name in ("workflow-hpo.json", "workflow-production.json"):
        notes = json.load(open(os.path.join(CONFIGS, name), encoding="utf-8"))["notes"]
        assert "FLAT overlay" in notes and '{"args"' not in notes, name
