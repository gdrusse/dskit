"""The HPO steps: candidate mapping, the warmup / remaining fold split, manifests, the freeze and the comparison.

Nothing trains: lanes are fake trees (``zoo_fakes``). Expected fold dates and bundle values are restated here."""

import copy
import json
import os
from datetime import date, timedelta

import pytest
from zoo_fakes import write_batch_status, write_lane

from crypto_trading.zoo_hpo import Candidates, HpoPlan
from crypto_trading.zoo_lanes import main as lanes_main
from crypto_trading.zoo_pick import ZooResults, file_sha256, hparams_sha256, load_recipe

CHILD_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIGS = os.path.join(CHILD_ROOT, "configs")
RECIPE_PATH = os.path.join(CONFIGS, "zoo-lanes-recipe.json")
OPTIONS = os.path.join(os.path.dirname(os.path.dirname(CHILD_ROOT)), "children", "index_options", "configs", "run-predictive-cdf-hpo.json")
W2_START_MS = 1_777_680_000_000
W1_END = date(2026, 5, 1)
FIRST, STEP = date(2024, 1, 1), 60


def zoo_cutoffs():
    return [(FIRST + timedelta(days=STEP * i)).isoformat() for i in range(14)]


def winner(cell, scope):
    model, loss = cell.split("-")
    suffixes = {"pooled": ["pooled"], "unpooled": ["btc", "eth"]}[scope]
    return {"cell": f"{cell}-{scope}", "model": model, "loss": loss, "scope": scope, "lanes": [f"{cell}-{s}" for s in suffixes]}


@pytest.fixture(scope="module")
def loaded():
    return load_recipe(RECIPE_PATH)


def plan_for(loaded, cell="gru-crps_tail", scope="pooled"):
    recipe, lanes, _, _ = loaded
    return HpoPlan(recipe, lanes, {"winner": winner(cell, scope)})


def test_the_bundles_are_the_options_childs_predeclared_gaussian_ones(loaded):
    with open(OPTIONS, encoding="utf-8") as handle:
        source = json.load(handle)["experiment"]["candidates"]
    bundles = loaded[0]["hpo"]["candidates"]
    assert list(bundles) == ["a", "b", "c", "d"]
    for letter, bundle in bundles.items():
        for family in ("separate", "pooled"):
            assert bundle["params"] == source[f"{family}_normal_{letter}"]["params"], (letter, family)
    assert bundles["c"]["params"] == {"components": 3, "hidden": [32, 32], "epochs": 40, "batch_size": 1024, "learning_rate": 0.001,
                                      "weight_decay": 0.01, "min_scale": 0.05, "activation": "tanh", "dropout": 0, "seeds": [11],
                                      "device": "cuda"}


def test_the_default_candidate_is_the_cell_exactly_as_the_zoo_ran_it_and_comes_first(loaded):
    plan = plan_for(loaded)
    cells, _ = plan.cells(plan.candidates.names())
    zoo = loaded[1].cells()
    assert plan.candidates.names() == ["default", "a", "b", "c", "d"]
    assert cells["default-gru-crps_tail-pooled"] == zoo["gru-crps_tail-pooled"]


def test_a_bundle_lands_only_on_the_mapped_knobs_and_nothing_else_moves(loaded):
    plan = plan_for(loaded)
    cells, record = plan.cells(["c"])
    fit, zoo = cells["c-gru-crps_tail-pooled"]["fit"], loaded[1].cells()["gru-crps_tail-pooled"]["fit"]
    assert record["c"]["applied"] == {"trunk.hidden": [32, 32], "trunk.activation": "tanh", "trunk.dropout": 0, "lr": 0.001,
                                      "optimizer_params.weight_decay": 0.01, "loader.batch_size": 1024, "head.min_scale": 0.05,
                                      "head.components": 3}
    assert set(record["c"]["skipped"]) == {"epochs", "seeds", "device"}
    expected = copy.deepcopy(zoo)
    expected["trunk"].update(hidden=[32, 32], activation="tanh", dropout=0)
    expected.update(lr=0.001)
    expected["optimizer_params"]["weight_decay"] = 0.01
    expected["loader"]["batch_size"] = 1024
    expected["head"].update(min_scale=0.05, components=3)
    assert fit == expected
    for held in ("epochs", "patience", "row_stride", "monitor", "optimizer", "device", "missing", "bounds", "encoder", "sequence"):
        assert fit.get(held) == zoo.get(held), held
    assert fit["loader"]["seed"] == zoo["loader"]["seed"]


def test_a_quantile_head_skips_the_mixture_only_knobs_and_says_so(loaded):
    plan = plan_for(loaded, "mlp-pinball")
    cells, record = plan.cells(["a"])
    skipped = record["a"]["skipped"]
    assert "head.min_scale" in skipped["min_scale"] and "quantile" in skipped["components"]
    assert "head.min_scale" not in record["a"]["applied"] and "head.components" not in record["a"]["applied"]
    assert cells["a-mlp-pinball-pooled"]["fit"]["head"] == loaded[1].cells()["mlp-pinball-pooled"]["fit"]["head"]
    assert record["a"]["applied"]["trunk.hidden"] == [16]


def test_a_bundle_knob_with_no_mapping_and_no_excuse_refuses(loaded):
    hpo = copy.deepcopy(loaded[0]["hpo"])
    hpo["candidates"]["a"]["params"]["mystery"] = 1
    with pytest.raises(ValueError, match="neither in hpo.map nor in hpo.unmapped"):
        Candidates(hpo)


def test_the_search_folds_are_the_first_four_of_the_zoo_schedule_and_the_rest_are_5_to_14(loaded):
    plan = plan_for(loaded)
    assert plan.search_schedule.cutoffs() == zoo_cutoffs()[:4]
    assert plan.evaluation_schedule.cutoffs() == zoo_cutoffs()[4:] and len(plan.evaluation_schedule.cutoffs()) == 10
    assert plan.evaluation_schedule.cutoffs()[0] == "2024-08-28"
    base = loaded[1].base["folds"]
    for schedule in (plan.search_schedule, plan.evaluation_schedule):
        assert {k: schedule.folds[k] for k in ("step_days", "val_days", "embargo_days")} == {k: base[k] for k in ("step_days", "val_days", "embargo_days")}
    assert plan.warmup + plan.evaluation_schedule.folds["count"] == base["count"]


def test_every_fold_sits_inside_w1_and_nothing_reaches_w2(loaded):
    plan = plan_for(loaded)
    w2_start = date(2026, 5, 2)
    for schedule in (plan.search_schedule, plan.evaluation_schedule):
        assert schedule.val_end() <= W1_END < w2_start
    template = "zoo-template.json"
    for manifest in (plan.search_manifest(template), plan.evaluation_manifest(plan_frozen(plan), template)):
        for lane, cell in manifest["args"]["cell"].items():
            if lane == "notes":
                continue
            for node in ("fit", "score"):
                assert cell[node]["bounds"]["val_end_ms"] == "$splits.val_end_ms"
        assert str(W2_START_MS) not in json.dumps(manifest) and "2026-05-02" not in json.dumps(manifest)


def plan_frozen(plan, winner_name="c"):
    cells, record = plan.cells(plan.candidates.names())
    params = {lane: cells[plan.lane(winner_name, lane)]["fit"] for lane in plan.lane_names}
    return {"winner": winner_name, "pick_winner": plan.winner, "recipe": {},
            "hparams_sha256": {lane: hparams_sha256(fit) for lane, fit in params.items()}, "params": params,
            "default_params": {lane: cells[plan.lane("default", lane)]["fit"] for lane in plan.lane_names}}


def test_the_search_manifest_has_one_lane_per_candidate_and_both_asset_lanes_for_an_unpooled_pick(loaded):
    pooled = plan_for(loaded).search_manifest("zoo-template.json")["args"]
    assert pooled["arms"] == [f"{c}-gru-crps_tail-pooled" for c in ("default", "a", "b", "c", "d")]
    unpooled = plan_for(loaded, "patchtst-nll", "unpooled").search_manifest("zoo-template.json")["args"]
    assert unpooled["arms"] == [f"{c}-patchtst-nll-{s}" for c in ("default", "a", "b", "c", "d") for s in ("btc", "eth")]
    assert unpooled["folds"]["count"] == 4 and unpooled["folds"]["first"] == "2024-01-01"
    assert unpooled["cell"]["c-patchtst-nll-btc"]["fit"]["assets"] == [0]


def test_the_manifests_expand_into_nodes_the_panel_kinds_accept(loaded, tmp_path):
    import importlib

    from dskit.pipeline.document import PipelineDocument
    from dskit.pipeline.libs.panel_score import PanelScore
    from dskit.pipeline.workflow import lane_flows

    plan = plan_for(loaded, "mlp-pinball", "unpooled")
    for manifest, count in ((plan.search_manifest("zoo-template.json"), 10), (plan.evaluation_manifest(plan_frozen(plan), "zoo-template.json"), 4)):
        flows = lane_flows(manifest, CONFIGS)
        assert len(flows) == count
        for flow in flows:
            doc = flow.expanded("fit", strict=False, rnd=0)
            PipelineDocument.from_obj(doc)
            fit = doc["pipeline"]["fit"]
            module, _, name = fit["uses"].partition(":")
            assert getattr(importlib.import_module(module), name).validate_params(fit["params"]) == [], flow.lane
            assert PanelScore.validate_params(doc["pipeline"]["score"]["params"]) == [], flow.lane
            assert doc["walkforward"]["count"] == manifest["args"]["folds"]["count"]


def test_the_cli_writes_manifests_the_workflow_plans(loaded, tmp_path, capsys):
    from dskit.pipeline.workflow import run_workflow

    pick = {"winner": winner("gru-crps_tail", "pooled"), "recipe": {"path": RECIPE_PATH, "sha256": file_sha256(RECIPE_PATH)}}
    (tmp_path / "zoo-pick.json").write_text(json.dumps(pick), encoding="utf-8")
    out = tmp_path / "sub" / "hpo-lanes.json"
    assert lanes_main(["hpo-manifest", "--pick", str(tmp_path / "zoo-pick.json"), "--out", str(out)]) == 0
    lines = []
    assert run_workflow(str(out), plan=True, out=lines.append) == 0
    assert sum(1 for line in lines if line.startswith("lane ")) == 5 and not [x for x in lines if x.startswith("unfilled arg")]


def test_a_pick_without_a_winner_and_a_changed_recipe_are_refused(loaded, tmp_path):
    with pytest.raises(ValueError, match="no winner"):
        HpoPlan(loaded[0], loaded[1], {"winner": None})
    pick = {"winner": winner("gru-crps", "pooled"), "recipe": {"path": RECIPE_PATH, "sha256": "0" * 64}}
    (tmp_path / "p.json").write_text(json.dumps(pick), encoding="utf-8")
    with pytest.raises(ValueError, match="recipe changed since the pick"):
        lanes_main(["hpo-manifest", "--pick", str(tmp_path / "p.json"), "--out", str(tmp_path / "m.json")])


def candidate_tree(root, loaded, plan, means, objective="$score.metrics.weighted_crps", names=None, cutoffs=None):
    """Write the lanes of ``names`` candidates, each with a constant fold score ``means[name]`` plus a shared wiggle."""
    recipe = loaded[0]
    cutoffs = cutoffs or plan.search_schedule.cutoffs()
    for name in names or plan.candidates.names():
        for lane in plan.lane_names:
            suffix = lane.rsplit("-", 1)[1]
            rows = {"pooled": 200, "btc": 50, "eth": 150}[suffix]
            write_lane(str(root), recipe, plan.lane(name, lane), cutoffs,
                       [means[name] + 0.001 * i + (0.5 if suffix == "btc" else 0.0) for i in range(len(cutoffs))],
                       [rows] * len(cutoffs), objective)


def test_the_freeze_takes_the_lowest_warmup_mean_and_records_its_full_params(loaded, tmp_path):
    plan = plan_for(loaded)
    candidate_tree(tmp_path, loaded, plan, {"default": 1.0, "a": 1.1, "b": 0.9, "c": 0.8, "d": 0.95})
    results = {n: ZooResults(str(tmp_path), loaded[0], loaded[2], f"{n}-") for n in plan.candidates.names()}
    frozen = plan.freeze(results)
    assert frozen["winner"] == "c"
    assert frozen["candidates"]["c"]["mean"] == pytest.approx(0.8 + 0.001 * 1.5)
    lane = plan.lane_names[0]
    assert frozen["params"][lane]["trunk"]["hidden"] == [32, 32] and frozen["params"][lane]["lr"] == 0.001
    assert frozen["default_params"][lane] == loaded[1].cells()[lane]["fit"]
    assert frozen["hparams_sha256"][lane] == hparams_sha256(frozen["params"][lane])
    assert frozen["warmup"]["cutoffs"] == zoo_cutoffs()[:4]


def test_the_default_wins_ties_and_then_recipe_order_decides(loaded, tmp_path):
    plan = plan_for(loaded)
    candidate_tree(tmp_path, loaded, plan, {"default": 1.0, "a": 1.0, "b": 1.0, "c": 1.2, "d": 1.2})
    results = {n: ZooResults(str(tmp_path), loaded[0], loaded[2], f"{n}-") for n in plan.candidates.names()}
    assert plan.freeze(results)["winner"] == "default"
    other = tmp_path / "second"
    candidate_tree(other, loaded, plan, {"default": 1.2, "a": 1.2, "b": 1.0, "c": 1.0, "d": 1.2})
    results = {n: ZooResults(str(other), loaded[0], loaded[2], f"{n}-") for n in plan.candidates.names()}
    assert plan.freeze(results)["winner"] == "b"


def test_an_unpooled_candidate_is_scored_row_weighted_over_its_two_lanes(loaded, tmp_path):
    plan = plan_for(loaded, "patchtst-nll", "unpooled")
    candidate_tree(tmp_path, loaded, plan, {"default": 1.0, "a": 1.0, "b": 1.0, "c": 1.0, "d": 1.0})
    results = {n: ZooResults(str(tmp_path), loaded[0], loaded[2], f"{n}-") for n in plan.candidates.names()}
    frozen = plan.freeze(results)
    # btc lanes carry +0.5, 50 of 200 rows: 0.125 above the constant
    assert frozen["candidates"]["a"]["mean"] == pytest.approx(1.0 + 0.125 + 0.001 * 1.5)


def test_the_freeze_refuses_candidates_scored_on_other_folds(loaded, tmp_path):
    plan = plan_for(loaded)
    candidate_tree(tmp_path, loaded, plan, {n: 1.0 for n in plan.candidates.names()}, cutoffs=zoo_cutoffs()[1:5])
    results = {n: ZooResults(str(tmp_path), loaded[0], loaded[2], f"{n}-") for n in plan.candidates.names()}
    with pytest.raises(ValueError, match="not the warmup schedule"):
        plan.freeze(results)


def test_the_hpo_pick_cli_needs_every_search_lane_ok_and_writes_hpo_frozen_json(loaded, tmp_path):
    plan = plan_for(loaded)
    root = tmp_path / "search"
    candidate_tree(root, loaded, plan, {"default": 1.0, "a": 1.1, "b": 0.9, "c": 0.8, "d": 0.95})
    lanes = [plan.lane(n, ln) for n in plan.candidates.names() for ln in plan.lane_names]
    pick = {"winner": winner("gru-crps_tail", "pooled"), "recipe": {"path": RECIPE_PATH, "sha256": file_sha256(RECIPE_PATH)}}
    (tmp_path / "zoo-pick.json").write_text(json.dumps(pick), encoding="utf-8")
    argv = ["hpo-pick", "--pick", str(tmp_path / "zoo-pick.json"), "--batch", str(root / "batch.json"), "--out", str(tmp_path / "hpo-frozen.json")]
    write_batch_status(str(root), lanes, "failed")
    with pytest.raises(ValueError, match="not every lane is ok"):
        lanes_main(argv)
    write_batch_status(str(root), lanes)
    assert lanes_main(argv) == 0
    frozen = json.loads((tmp_path / "hpo-frozen.json").read_text(encoding="utf-8"))
    assert frozen["winner"] == "c" and frozen["pick_winner"]["cell"] == "gru-crps_tail-pooled" and frozen["recipe"] == pick["recipe"]
    assert set(frozen["sources"]) == {"default", "a", "b", "c", "d"}


def test_the_evaluation_runs_the_default_and_the_frozen_winner_on_folds_5_to_14(loaded):
    plan = plan_for(loaded)
    manifest = plan.evaluation_manifest(plan_frozen(plan), "zoo-template.json")
    assert manifest["args"]["arms"] == ["default-gru-crps_tail-pooled", "frozen-gru-crps_tail-pooled"]
    assert manifest["args"]["folds"]["first"] == "2024-08-28" and manifest["args"]["folds"]["count"] == 10
    cells = manifest["args"]["cell"]
    assert cells["frozen-gru-crps_tail-pooled"]["fit"]["lr"] == 0.001
    assert cells["default-gru-crps_tail-pooled"]["fit"] == loaded[1].cells()["gru-crps_tail-pooled"]["fit"]
    assert "search" not in json.dumps(manifest["steps"]) and "walkforward" not in manifest["args"]


def test_when_the_default_won_there_is_nothing_to_compare_and_one_lane_runs(loaded):
    plan = plan_for(loaded)
    frozen = plan_frozen(plan, "default")
    assert plan.evaluation_manifest(frozen, "zoo-template.json")["args"]["arms"] == ["default-gru-crps_tail-pooled"]


def test_an_evaluation_after_the_recipes_default_moved_is_refused(loaded):
    plan = plan_for(loaded)
    frozen = plan_frozen(plan)
    frozen["default_params"] = copy.deepcopy(frozen["default_params"])
    frozen["default_params"][plan.lane_names[0]]["lr"] = 9.0
    with pytest.raises(ValueError, match="differs from the one the HPO ran against"):
        plan.evaluation_manifest(frozen, "zoo-template.json")


def test_the_evaluation_record_is_the_paired_tuned_minus_default_gap(loaded, tmp_path):
    plan = plan_for(loaded)
    frozen = plan_frozen(plan)
    cutoffs = plan.evaluation_schedule.cutoffs()
    candidate_tree(tmp_path, loaded, plan, {"default": 1.0, "frozen": 0.9}, names=["default", "frozen"], cutoffs=cutoffs)
    results = {n: ZooResults(str(tmp_path), loaded[0], loaded[2], f"{n}-") for n in ("default", "frozen")}
    record = plan.evaluation(results, frozen)
    gap = record["tuned_vs_default"]
    assert gap["mean"] == pytest.approx(-0.1) and gap["n"] == 10 and record["tuned_better"] is True
    assert record["cutoffs"] == zoo_cutoffs()[4:]


def test_hpo_pick_with_a_root_runs_the_same_all_ok_check_as_with_a_batch(loaded, tmp_path):
    plan = plan_for(loaded)
    root = tmp_path / "search"
    candidate_tree(root, loaded, plan, {n: 1.0 for n in plan.candidates.names()})
    pick = {"winner": winner("gru-crps_tail", "pooled"), "recipe": {"path": RECIPE_PATH, "sha256": file_sha256(RECIPE_PATH)}}
    (tmp_path / "zoo-pick.json").write_text(json.dumps(pick), encoding="utf-8")
    argv = ["hpo-pick", "--pick", str(tmp_path / "zoo-pick.json"), "--root", str(root), "--out", str(tmp_path / "hpo-frozen.json")]
    with pytest.raises(ValueError, match="no batch.json"):
        lanes_main(argv)
    lanes = [plan.lane(n, ln) for n in plan.candidates.names() for ln in plan.lane_names]
    write_batch_status(str(root), lanes, "failed")
    with pytest.raises(ValueError, match="not every lane is ok"):
        lanes_main(argv)
    write_batch_status(str(root), lanes)
    assert lanes_main(argv) == 0
