"""The HPO steps: candidate mapping, the warmup / remaining fold split, manifests, the freeze and the comparison.

Nothing trains: lanes are fake trees (``zoo_fakes``). Expected fold dates and bundle values are restated here."""

import copy
import json
import os
from datetime import date, timedelta

import pytest
from zoo_fakes import write_batch_status, write_lane

from crypto_trading.zoo_hpo import DEFAULT_N_DRAWS, KNOBS, Candidates, HpoPlan, SearchSpace
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


def plan_for(loaded, cell="gru-crps_tail", scope="pooled", wide=False):
    """The HPO plan of a pick; the bundles-only one (the recipe without ``hpo.space``) unless ``wide``."""
    recipe, lanes, _, _ = loaded
    if not wide:
        recipe = copy.deepcopy(recipe)
        del recipe["hpo"]["space"]
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
    assert sum(1 for line in lines if line.startswith("lane ")) == len(plan_for(loaded, wide=True).candidates.names()) == 5 + 16 and not [x for x in lines if x.startswith("unfilled arg")]


def test_a_pick_without_a_winner_and_a_changed_recipe_are_refused(loaded, tmp_path):
    with pytest.raises(ValueError, match="no winner"):
        HpoPlan(loaded[0], loaded[1], {"winner": None})
    pick = {"winner": winner("gru-crps", "pooled"), "recipe": {"path": RECIPE_PATH, "sha256": "0" * 64}}
    (tmp_path / "p.json").write_text(json.dumps(pick), encoding="utf-8")
    with pytest.raises(ValueError, match="recipe changed since the pick"):
        lanes_main(["hpo-manifest", "--pick", str(tmp_path / "p.json"), "--out", str(tmp_path / "m.json")])


SLOW_DRAWS = {f"s{i:02d}": 5.0 for i in range(1, 17)}


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
    plan = plan_for(loaded, wide=True)
    root = tmp_path / "search"
    candidate_tree(root, loaded, plan, {"default": 1.0, "a": 1.1, "b": 0.9, "c": 0.8, "d": 0.95, **SLOW_DRAWS})
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
    assert set(frozen["sources"]) == {"default", "a", "b", "c", "d", *SLOW_DRAWS}


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
    plan = plan_for(loaded, wide=True)
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


# --- hpo.space: the seeded, bounded random draw -------------------------------------------------------------------------------------------

SPEC_DOMAINS = {"width": [32, 64, 128], "num_layers": [1, 2], "trunk_hidden": [[16], [32, 16], [64, 32]],
                "dropout": {"low": 0.0, "high": 0.3, "log": False}, "lr": {"low": 1e-4, "high": 3e-3, "log": True},
                "weight_decay": {"low": 1e-4, "high": 1e-1, "log": True}, "batch_size": [512, 1024]}
DRAW_NAMES = [f"s{i:02d}" for i in range(1, 17)]


def test_the_shipped_space_is_the_owners_default_domains_and_the_code_defaults_agree(loaded):
    space = loaded[0]["hpo"]["space"]
    assert space["knobs"] == SPEC_DOMAINS and space["n_draws"] == 16 == DEFAULT_N_DRAWS and type(space["seed"]) is int
    assert {name: knob.domain for name, knob in KNOBS.items()} == SPEC_DOMAINS
    assert SearchSpace({"seed": space["seed"]}).draws == SearchSpace(space).draws


def test_draws_are_reproducible_from_the_seed_and_a_longer_run_keeps_the_earlier_draws():
    first, again = SearchSpace({"seed": 3}), SearchSpace({"seed": 3})
    assert first.draws == again.draws and first.names() == DRAW_NAMES
    assert SearchSpace({"seed": 4}).draws != first.draws
    longer = SearchSpace({"seed": 3, "n_draws": 20})
    assert all(longer.draws[n] == first.draws[n] for n in DRAW_NAMES) and len(longer.names()) == 20 and longer.names()[-1] == "s20"


def test_a_knobs_draws_do_not_depend_on_another_knobs_domain():
    base = SearchSpace({"seed": 3})
    narrowed = SearchSpace({"seed": 3, "knobs": {"lr": {"low": 1e-3, "high": 2e-3, "log": True}}})
    for name in DRAW_NAMES:
        assert {k: v for k, v in narrowed.draws[name].items() if k != "lr"} == {k: v for k, v in base.draws[name].items() if k != "lr"}


def test_every_draw_stays_inside_its_domain_and_log_ranges_are_log_uniform():
    space = SearchSpace({"seed": 11, "n_draws": 600})
    draws = list(space.draws.values())
    for knob in ("width", "num_layers", "trunk_hidden", "batch_size"):
        assert {json.dumps(d[knob]) for d in draws} == {json.dumps(v) for v in SPEC_DOMAINS[knob]}, knob
    for knob in ("dropout", "lr", "weight_decay"):
        low, high = SPEC_DOMAINS[knob]["low"], SPEC_DOMAINS[knob]["high"]
        values = [d[knob] for d in draws]
        assert low <= min(values) and max(values) <= high, knob
    geometric_mid = (1e-4 * 3e-3) ** 0.5
    below = sum(d["lr"] < geometric_mid for d in draws) / len(draws)
    assert 0.4 < below < 0.6, "lr is drawn log-uniformly: half the draws sit below the geometric midpoint"
    assert sum(d["dropout"] < 0.15 for d in draws) / len(draws) == pytest.approx(0.5, abs=0.1)


@pytest.mark.parametrize("space, message", [
    ({}, "seed must be an integer"),
    ({"seed": True}, "seed must be an integer"),
    ({"seed": 1, "n_draws": 0}, "n_draws must be an integer >= 1"),
    ({"seed": 1, "extra": 1}, "unknown keys"),
    ({"seed": 1, "knobs": {"momentum": [0.9]}}, "unknown knobs"),
    ({"seed": 1, "knobs": {"lr": {"low": 0.0, "high": 1.0, "log": True}}}, "legal ends"),
    ({"seed": 1, "knobs": {"lr": {"low": 1e-3, "high": 1e-3}}}, "low < high"),
    ({"seed": 1, "knobs": {"lr": {"low": 1e-3, "high": 1e-2, "scale": "log"}}}, "a range is"),
    ({"seed": 1, "knobs": {"width": {"low": 8, "high": 16}}}, "expected a list of values"),
    ({"seed": 1, "knobs": {"width": []}}, "non-empty list"),
    ({"seed": 1, "knobs": {"width": [32, 0]}}, "legal values"),
    ({"seed": 1, "knobs": {"dropout": {"low": 0.0, "high": 1.0}}}, "legal ends"),
    ({"seed": 1, "knobs": {"trunk_hidden": [[]]}}, "legal values"),
])
def test_an_illegal_space_refuses(space, message):
    with pytest.raises(ValueError, match=message):
        SearchSpace(space)


def test_a_draw_named_like_a_bundle_or_the_default_refuses(loaded):
    hpo = copy.deepcopy(loaded[0]["hpo"])
    hpo["candidates"]["s01"] = hpo["candidates"].pop("a")
    with pytest.raises(ValueError, match="named like the default or a bundle"):
        Candidates(hpo)


def fits(loaded, cell, draw="s02"):
    plan = plan_for(loaded, cell, "pooled", wide=True)
    cells, record = plan.cells([draw])
    lane = plan.lane(draw, plan.lane_names[0])
    return loaded[1].cells()[plan.lane_names[0]]["fit"], cells[lane]["fit"], record[draw], plan.candidates.draw(draw)


def test_a_gru_draw_ties_the_width_and_sets_the_depth_and_nothing_else_moves(loaded):
    zoo, fit, record, draw = fits(loaded, "gru-crps_tail")
    expected = copy.deepcopy(zoo)
    expected["encoder"].update(width=draw["width"])
    expected["encoder"]["arch_params"].update(hidden_size=draw["width"], num_layers=draw["num_layers"])
    expected["trunk"].update(hidden=draw["trunk_hidden"], dropout=draw["dropout"])
    expected.update(lr=draw["lr"])
    expected["optimizer_params"]["weight_decay"] = draw["weight_decay"]
    expected["loader"]["batch_size"] = draw["batch_size"]
    assert fit == expected and record["skipped"] == {} and record["draw"] == draw
    assert set(record["applied"]) == {"encoder.width", "encoder.arch_params.hidden_size", "encoder.arch_params.num_layers", "trunk.hidden",
                                      "trunk.dropout", "lr", "optimizer_params.weight_decay", "loader.batch_size"}
    assert (draw["width"], draw["num_layers"]) == (32, 2), "s02 of seed 20261011 (a pin that the shipped seed still draws what the notes say)"


def test_a_cnn1d_draw_ties_the_width_and_skips_the_depth_with_a_reason(loaded):
    zoo, fit, record, draw = fits(loaded, "cnn1d-crps")
    assert fit["encoder"]["width"] == fit["encoder"]["arch_params"]["hidden_size"] == draw["width"]
    assert fit["encoder"]["arch_params"]["dilations"] == zoo["encoder"]["arch_params"]["dilations"]
    assert "num_layers" not in fit["encoder"]["arch_params"] and "arch_params.num_layers" in record["skipped"]["num_layers"]
    assert "encoder.arch_params.num_layers" not in record["applied"]


def test_a_patchtst_draw_sets_the_hf_encoders_d_model_and_skips_the_depth(loaded):
    zoo, fit, record, draw = fits(loaded, "patchtst-nll")
    assert fit["encoder"]["params"]["config"]["d_model"] == draw["width"]
    expected = copy.deepcopy(zoo["encoder"])
    expected["params"]["config"]["d_model"] = draw["width"]
    assert fit["encoder"] == expected and set(record["skipped"]) == {"num_layers"}
    assert record["applied"]["encoder.params.config.d_model"] == draw["width"]


def test_an_mlp_draw_skips_both_encoder_knobs_and_sets_the_trunk_and_optimizer_ones(loaded):
    zoo, fit, record, draw = fits(loaded, "mlp-pinball")
    assert "encoder" not in fit and set(record["skipped"]) == {"width", "num_layers"}
    assert all("no sequence encoder" in why for why in record["skipped"].values())
    assert fit["trunk"]["hidden"] == draw["trunk_hidden"] and fit["lr"] == draw["lr"] and fit["head"] == zoo["head"]


def test_the_draws_do_not_depend_on_the_picked_architecture(loaded):
    gru, mlp = (plan_for(loaded, cell, wide=True).candidates.draw("s05") for cell in ("gru-crps", "mlp-pinball"))
    assert gru == mlp


def test_the_wide_search_manifest_has_5_plus_n_candidates_and_records_every_draw(loaded):
    plan = plan_for(loaded, wide=True)
    n = loaded[0]["hpo"]["space"]["n_draws"]
    assert plan.candidates.names() == ["default", "a", "b", "c", "d", *DRAW_NAMES] and len(plan.candidates.names()) == 5 + n
    args = plan.search_manifest("zoo-template.json")["args"]
    assert args["arms"] == [f"{c}-gru-crps_tail-pooled" for c in plan.candidates.names()] and set(args["hpo"]["candidates"]) == set(plan.candidates.names())
    space = SearchSpace(loaded[0]["hpo"]["space"])
    assert args["hpo"]["space"] == space.record()
    for name in DRAW_NAMES:
        assert args["hpo"]["candidates"][name]["draw"] == space.draws[name]
        assert args["cell"][f"{name}-gru-crps_tail-pooled"]["fit"]["lr"] == space.draws[name]["lr"]
    assert "draw" not in args["hpo"]["candidates"]["a"] and "draw" not in args["hpo"]["candidates"]["default"]
    assert plan_for(loaded).search_manifest("zoo-template.json")["args"]["hpo"].get("space") is None


def test_a_wide_unpooled_search_has_both_asset_lanes_per_candidate(loaded):
    args = plan_for(loaded, "patchtst-nll", "unpooled", wide=True).search_manifest("zoo-template.json")["args"]
    assert len(args["arms"]) == 2 * 21 and args["arms"][-2:] == ["s16-patchtst-nll-btc", "s16-patchtst-nll-eth"]


def test_a_drawn_winner_is_frozen_with_its_draw_and_the_space_and_ties_still_go_to_the_default(loaded, tmp_path):
    plan = plan_for(loaded, wide=True)
    names = plan.candidates.names()
    means = {n: 1.0 for n in names}
    candidate_tree(tmp_path / "tie", loaded, plan, means)
    results = {n: ZooResults(str(tmp_path / "tie"), loaded[0], loaded[2], f"{n}-") for n in names}
    assert plan.freeze(results)["winner"] == "default"
    means["s07"] = 0.5
    candidate_tree(tmp_path / "win", loaded, plan, means)
    results = {n: ZooResults(str(tmp_path / "win"), loaded[0], loaded[2], f"{n}-") for n in names}
    frozen = plan.freeze(results)
    draw = SearchSpace(loaded[0]["hpo"]["space"]).draws["s07"]
    lane = plan.lane_names[0]
    assert frozen["winner"] == "s07" and frozen["candidates"]["s07"]["draw"] == draw and frozen["space"] == plan.candidates.space.record()
    assert frozen["params"][lane]["lr"] == draw["lr"] and frozen["params"][lane]["encoder"]["width"] == draw["width"]
    assert frozen["default_params"][lane] == loaded[1].cells()[lane]["fit"]
    assert frozen["hparams_sha256"][lane] == hparams_sha256(frozen["params"][lane])
    evaluation = plan.evaluation_manifest({**frozen, "pick_winner": plan.winner, "recipe": {}}, "zoo-template.json")
    assert evaluation["args"]["cell"]["frozen-gru-crps_tail-pooled"]["fit"]["lr"] == draw["lr"]


@pytest.mark.parametrize("cell", ["gru-crps_tail", "patchtst-nll", "cnn1d-crps", "mlp-pinball", "transformer-nll"])
def test_every_wide_lane_of_every_architecture_is_accepted_by_the_panel_kinds(loaded, cell):
    import importlib

    from dskit.pipeline.workflow import lane_flows

    plan = plan_for(loaded, cell, wide=True)
    flows = lane_flows(plan.search_manifest("zoo-template.json"), CONFIGS)
    assert len(flows) == 21
    for flow in flows:
        fit = flow.expanded("fit", strict=False, rnd=0)["pipeline"]["fit"]
        module, _, name = fit["uses"].partition(":")
        assert getattr(importlib.import_module(module), name).validate_params(fit["params"]) == [], flow.lane
