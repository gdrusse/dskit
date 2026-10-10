"""The zoo lane generator: the shipped manifests equal a fresh generation, and every lane is a document the dskit
panel kinds accept. Expectations (lane counts, W2 instants, the grid's cells) are restated here on purpose."""

import copy
import json
import os

import pytest

from crypto_trading.zoo_lanes import ZooLanes, main

CHILD_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIGS = os.path.join(CHILD_ROOT, "configs")
MODELS = ("patchtst", "cnn1d", "gru", "mlp", "transformer")
LOSSES = ("pinball", "crps", "nll", "crps_tail")
REGIMES = ("pooled", "btc", "eth")
BASELINES = ("empirical", "ewma_student")
DAY_MS = 86_400_000
W1_END_MS = 1_777_593_600_000      # 2026-05-01 00:00 UTC, exclusive
W2_START_MS = 1_777_680_000_000    # 2026-05-02 00:00 UTC
W2_END_MS = 1_785_542_400_000      # 2026-08-01 00:00 UTC, exclusive


def _node_class(path):
    import importlib

    module, _, name = path.partition(":")
    return getattr(importlib.import_module(module), name)


def _load(name):
    with open(os.path.join(CONFIGS, name), encoding="utf-8") as handle:
        return json.load(handle)


@pytest.fixture(scope="module")
def recipe():
    return _load("zoo-lanes-recipe.json")


@pytest.fixture(scope="module")
def lanes(recipe):
    return ZooLanes(recipe, _load(recipe["base"]))


def test_the_shipped_manifests_equal_a_fresh_generation(lanes, recipe):
    assert lanes.manifest() == _load("workflow-zoo-w1.json")
    w2 = recipe["w2"]
    assert lanes.w2_manifest(w2["finalists"], w2["bounds"], w2["template"]) == _load("workflow-zoo-w2.json")


def test_the_cli_writes_what_the_library_returns(tmp_path, lanes):
    out = tmp_path / "w1.json"
    assert main([os.path.join(CONFIGS, "zoo-lanes-recipe.json"), "--out", str(out)]) == 0
    assert json.loads(out.read_text(encoding="utf-8")) == lanes.manifest()


def test_the_lanes_are_the_grid_cells_in_every_regime_plus_the_baselines(lanes):
    expected = [f"{m}-{loss}-{r}" for r in REGIMES for m in MODELS for loss in LOSSES] + \
        [f"{b}-{r}" for r in REGIMES for b in BASELINES]
    assert list(lanes.manifest()["args"]["arms"]) == expected and len(expected) == 5 * 4 * 3 + 2 * 3
    assert set(lanes.manifest()["args"]["cell"]) - {"notes"} == set(expected)


def test_a_pooled_lane_pools_both_assets_with_an_indicator_and_the_others_one_asset_without(lanes):
    cells = lanes.manifest()["args"]["cell"]
    for model in MODELS:
        pooled = cells[f"{model}-crps-pooled"]["fit"]
        assert pooled["assets"] == [0, 1] and pooled["asset_indicator"] is True
        for name, code in (("btc", 0), ("eth", 1)):
            single = cells[f"{model}-crps-{name}"]["fit"]
            assert single["assets"] == [code] and single["asset_indicator"] is False
            assert cells[f"{model}-crps-{name}"]["score"]["assets"] == [code]


def test_only_the_sequence_models_carry_a_sequence_and_an_encoder(lanes):
    cells = lanes.manifest()["args"]["cell"]
    for model in MODELS:
        fit = cells[f"{model}-nll-pooled"]["fit"]
        assert ("encoder" in fit and "sequence" in fit) == (model != "mlp"), model


def test_the_heads_agree_with_the_grid_the_lanes_are_scored_on(lanes):
    args = lanes.manifest()["args"]
    common = args["grid"]["common"]
    for lane, cell in args["cell"].items():
        if lane == "notes" or "-" not in lane or lane.split("-")[0] not in MODELS:
            continue
        head, loss = cell["fit"]["head"], lane.split("-")[1]
        assert cell["score"]["grid"] == common and cell["score"]["tail_weight"] == args["grid"]["tail_weight"]
        if loss == "pinball":
            assert head == {"kind": "quantile", "quantiles": common["levels"], "bounds": [common["z_min"], common["z_max"]]}
        else:
            assert head["kind"] == "mixture" and head["components"] == 3
            assert ("tail_intervals" in head) == (loss == "crps_tail")
            if loss == "crps_tail":
                assert head["tail_intervals"] == common["tail_intervals"]
        kinds = [t["kind"] for t in head.get("losses", [])]
        assert kinds == {"crps": ["crps"], "nll": ["nll"], "crps_tail": ["crps", "tail_crps"], "pinball": []}[loss]


def test_every_lane_shares_one_panel_one_set_of_bounds_and_the_selected_feature_context(lanes):
    args = lanes.manifest()["args"]
    cells = [c for k, c in args["cell"].items() if k != "notes"]
    assert {json.dumps(c["fit"]["panel"], sort_keys=True) for c in cells} == {json.dumps(c["score"]["panel"], sort_keys=True) for c in cells}
    assert len({json.dumps(c["score"]["bounds"], sort_keys=True) for c in cells}) == 1
    assert len({json.dumps(c["fit"]["bounds"], sort_keys=True) for c in cells}) == 1
    trained = [c["fit"] for c in cells if c["uses"].endswith("PanelTrain")]
    assert len(trained) == 60 and all(f["context"] == args["features"]["context"] for f in trained)
    selection = _load("workflow-select-sequence.json")["args"]
    assert args["features"]["context"] == selection["arm"]["all"]["context"], "the default is the selection's `all` arm"


def test_the_patchtst_cell_is_the_selections_encoder(lanes):
    selection = _load("workflow-select-sequence.json")["args"]["zoo"]
    assert lanes.manifest()["args"]["cell"]["patchtst-crps-pooled"]["fit"]["encoder"] == selection["encoder"]


def test_every_lane_expands_into_nodes_the_panel_kinds_accept():
    from dskit.pipeline.document import PipelineDocument
    from dskit.pipeline.libs.panel_score import PanelScore
    from dskit.pipeline.workflow import lane_flows

    manifest = _load("workflow-zoo-w1.json")
    flows = lane_flows(manifest, CONFIGS)
    assert len(flows) == 66
    for flow in flows:
        doc = flow.expanded("fit", strict=False, rnd=0)
        PipelineDocument.from_obj(doc)
        fit = doc["pipeline"]["fit"]
        assert _node_class(fit["uses"]).validate_params(fit["params"]) == [], flow.lane
        assert PanelScore.validate_params(doc["pipeline"]["score"]["params"]) == [], flow.lane
        assert doc["walkforward"]["count"] == manifest["args"]["folds"]["count"]


def test_the_w1_manifest_plans_with_nothing_unfilled():
    from dskit.pipeline.workflow import run_workflow

    lines = []
    assert run_workflow(os.path.join(CONFIGS, "workflow-zoo-w1.json"), plan=True, out=lines.append) == 0
    assert sum(1 for line in lines if line.startswith("lane ")) == 66
    assert not [line for line in lines if line.startswith("unfilled arg")]


def test_the_w2_bounds_are_the_plans_and_only_the_w2_read_may_use_them(recipe):
    bounds = recipe["w2"]["bounds"]
    assert bounds["train_end_ms"] + 1 == W1_END_MS, "training stops with W1"
    assert bounds["val_start_ms"] == W2_START_MS and bounds["val_start_ms"] - bounds["train_end_ms"] == DAY_MS + 1
    assert bounds["val_end_ms"] + 1 == W2_END_MS, "W2 ends before 2026-08-01 (W4 starts there)"
    assert not any(m["fit"]["bounds"]["val_end_ms"] == bounds["val_end_ms"]
                   for m in _load("workflow-zoo-w1.json")["args"]["cell"].values() if isinstance(m, dict) and "fit" in m)


def test_the_w2_skeleton_has_one_unfilled_lane_and_frozen_finalists_get_fixed_bounds(lanes, recipe):
    w2 = recipe["w2"]
    from dskit.pipeline.workflow import run_workflow

    lines = []
    run_workflow(os.path.join(CONFIGS, "workflow-zoo-w2.json"), plan=True, out=lines.append)
    assert any(line.startswith("unfilled arg") for line in lines)
    chosen = lanes.w2_manifest(["gru-crps_tail-pooled", "empirical-pooled"], w2["bounds"], w2["template"])
    assert chosen["args"]["arms"] == ["gru-crps_tail-pooled", "empirical-pooled"]
    for cell in (chosen["args"]["cell"][k] for k in chosen["args"]["arms"]):
        for node in ("fit", "score"):
            b = cell[node]["bounds"]
            assert {k: b[k] for k in ("train_end_ms", "val_start_ms", "val_end_ms")} == {
                k: w2["bounds"][k] for k in ("train_end_ms", "val_start_ms", "val_end_ms")}
    with pytest.raises(KeyError):
        lanes.w2_manifest(["not-a-lane"], w2["bounds"], w2["template"])


def test_the_recipe_models_and_grid_are_the_owner_approved_ones(recipe):
    assert tuple(recipe["models"]) == MODELS and tuple(recipe["losses"]) == LOSSES and tuple(recipe["baselines"]) == BASELINES


def test_every_config_object_explains_itself(recipe):
    for name in ("zoo-lanes-recipe.json", "zoo-template.json", "w2-read-template.json", "workflow-zoo-w1.json", "workflow-zoo-w2.json"):
        doc = _load(name)
        assert doc.get("notes"), name
        for key, node in doc.get("pipeline", {}).items():
            assert node.get("notes"), (name, key)
    for section in ("models", "losses", "baselines"):
        assert all(spec.get("notes") for spec in recipe[section].values()), section


def test_the_generator_does_not_edit_its_inputs(recipe):
    before = copy.deepcopy(recipe)
    ZooLanes(recipe, _load(recipe["base"])).manifest()
    assert recipe == before
