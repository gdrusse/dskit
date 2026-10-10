"""The zoo pick: ranking, eligibility against the baselines, row-weighted unpooled cells, the no-winner exit.

Trees are fake (``zoo_fakes``): the files a walk-forward leaves, with scores chosen so every expectation is arithmetic
restated here, never read back from the code under test."""

import json
import os

import pytest
from zoo_fakes import write_lane

from crypto_trading.zoo_lanes import FoldSchedule
from crypto_trading.zoo_pick import EXIT_NO_WINNER, ZooPick, ZooResults, load_recipe, main

CHILD_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RECIPE_PATH = os.path.join(CHILD_ROOT, "configs", "zoo-lanes-recipe.json")
ROWS = {"pooled": 200, "btc": 50, "eth": 150}


@pytest.fixture(scope="module")
def loaded():
    return load_recipe(RECIPE_PATH)


def wiggle(i):
    return 0.01 * (i % 3)


def tree(root, loaded, score_of, objective=None, rows_of=None):
    """Write every lane of the zoo: ``score_of(lane, fold)`` is its fold score."""
    recipe, lanes, template, _ = loaded
    cutoffs = FoldSchedule(lanes.base["folds"]).cutoffs()
    objective = objective or template["walkforward"]["objective"]
    for lane in lanes.manifest()["args"]["arms"]:
        suffix = lane.rsplit("-", 1)[1]
        scores = [score_of(lane, i) for i in range(len(cutoffs))]
        rows = [(rows_of or (lambda s, i: ROWS[s]))(suffix, i) for i in range(len(cutoffs))]
        write_lane(str(root), recipe, lane, cutoffs, scores, rows, objective)
    return cutoffs


def standard(lane, i):
    """Baselines 2.0 (empirical) and 1.9 (ewma); models 2.2; four designed cells."""
    base = {"empirical": 2.0, "ewma_student": 1.9}
    name = lane.rsplit("-", 1)[0]
    if name in base:
        return base[name] + wiggle(i)
    designed = {"gru-crps_tail-pooled": 1.5, "mlp-crps-pooled": 1.85, "cnn1d-pinball-pooled": 1.95,
                "patchtst-nll-btc": 1.0, "patchtst-nll-eth": 2.0}
    return designed.get(lane, 2.2) + wiggle(i)


@pytest.fixture
def record(tmp_path, loaded):
    tree(tmp_path, loaded, standard)
    recipe, lanes, template, _ = loaded
    return ZooPick(lanes, ZooResults(str(tmp_path), recipe, template)).result()


def test_the_lowest_mean_eligible_cell_wins_and_the_ranking_is_by_mean(record):
    assert record["winner"]["cell"] == "gru-crps_tail-pooled"
    means = [e["mean"] for e in record["ranking"]]
    assert means == sorted(means) and len(means) == 40
    assert [e["rank"] for e in record["ranking"]] == list(range(1, 41))
    assert record["n_folds"] == 14 and record["objective"] == "$score.metrics.weighted_crps"


def test_a_cell_must_beat_both_baselines_of_its_scope(record):
    by = {e["cell"]: e for e in record["ranking"]}
    assert by["mlp-crps-pooled"]["eligible"] is True            # 1.85 < 1.9 and 2.0
    ewma_only = by["cnn1d-pinball-pooled"]                      # 1.95: beats empirical, not ewma
    assert ewma_only["eligible"] is False
    assert ewma_only["beats"] == {"empirical-pooled": True, "ewma_student-pooled": False}
    assert by["gru-crps-pooled"]["eligible"] is False           # 2.2 beats nothing


def test_an_unpooled_cell_is_its_two_lanes_weighted_by_validation_rows(record):
    cell = next(e for e in record["ranking"] if e["cell"] == "patchtst-nll-unpooled")
    assert cell["lanes"] == ["patchtst-nll-btc", "patchtst-nll-eth"]
    expected = [(50 * (1.0 + wiggle(i)) + 150 * (2.0 + wiggle(i))) / 200 for i in range(14)]
    assert cell["per_fold"] == pytest.approx(expected)
    assert cell["mean"] == pytest.approx(sum(expected) / 14) and cell["mean"] == pytest.approx(1.75 + 0.01 * 13 / 14, abs=0.01)
    assert cell["eligible"] is True and cell["beats"] == {"empirical-unpooled": True, "ewma_student-unpooled": True}


def test_the_paired_difference_to_the_best_baseline_is_per_fold_with_mean_and_se(record):
    winner = record["winner"]
    gap = winner["vs_best_baseline"]
    assert gap["reference"] == "ewma_student-pooled"
    assert gap["per_fold"] == pytest.approx([-0.4] * 14) and gap["mean"] == pytest.approx(-0.4) and gap["n"] == 14
    assert gap["se"] == pytest.approx(0, abs=1e-9)


def test_the_metric_tables_cover_the_winner_and_every_baseline_per_fold_and_overall(record, loaded):
    cells = record["metrics"]["cells"]
    assert set(cells) == {"gru-crps_tail-pooled", "empirical-pooled", "ewma_student-pooled", "empirical-unpooled", "ewma_student-unpooled"}
    names = loaded[0]["pick"]["metrics"]
    assert record["metrics"]["names"] == names
    table = cells["gru-crps_tail-pooled"]
    assert list(table["per_fold"]) == record["cutoffs"] and set(table["overall"]) == set(names)
    assert table["overall"]["crps"] == pytest.approx(1.5 + 0.01 * sum(wiggle(i) for i in range(14)) / 0.01 / 14)


def test_near_ties_are_reported_and_the_lowest_mean_is_still_the_pick(tmp_path, loaded):
    def scores(lane, i):
        if lane == "mlp-nll-pooled":                     # 0.001 above the best, +-0.02 noisy: within one SE
            return 1.5 + 0.001 + wiggle(i) + 0.02 * (-1) ** i
        return standard(lane, i)
    tree(tmp_path, loaded, scores)
    recipe, lanes, template, _ = loaded
    record = ZooPick(lanes, ZooResults(str(tmp_path), recipe, template)).result()
    assert record["winner"]["cell"] == "gru-crps_tail-pooled"
    assert [t["cell"] for t in record["near_ties"]] == ["mlp-nll-pooled"]


def test_no_winner_writes_the_ranking_and_exits_nonzero(tmp_path, loaded, capsys):
    tree(tmp_path / "zoo", loaded, lambda lane, i: 2.0 + wiggle(i) if lane.split("-")[0] in ("empirical", "ewma_student") else 2.5)
    out = tmp_path / "pick"
    code = main(["--root", str(tmp_path / "zoo"), "--recipe", RECIPE_PATH, "--out", str(out)])
    assert code == EXIT_NO_WINNER == 3
    record = json.loads((out / "zoo-pick.json").read_text(encoding="utf-8"))
    assert record["winner"] is None and len(record["ranking"]) == 40
    assert "No cell beats both baselines" in (out / "zoo-pick.md").read_text(encoding="utf-8")
    assert "no model cell beats both baselines" in capsys.readouterr().out


def test_the_cli_writes_json_and_a_short_markdown_table(tmp_path, loaded):
    tree(tmp_path / "zoo", loaded, standard)
    out = tmp_path / "pick"
    assert main(["--root", str(tmp_path / "zoo"), "--recipe", RECIPE_PATH, "--out", str(out)]) == 0
    record = json.loads((out / "zoo-pick.json").read_text(encoding="utf-8"))
    assert record["winner"]["cell"] == "gru-crps_tail-pooled" and record["recipe"]["sha256"]
    md = (out / "zoo-pick.md").read_text(encoding="utf-8")
    assert "Winner: **gru-crps_tail-pooled**" in md
    assert len(md.splitlines()) < 40


def test_the_objective_comes_from_the_template_and_a_foreign_summary_is_refused(tmp_path, loaded):
    recipe, lanes, template, _ = loaded
    tree(tmp_path, loaded, standard, objective="$score.metrics.crps")
    with pytest.raises(ValueError, match="the template's is"):
        ZooPick(lanes, ZooResults(str(tmp_path), recipe, template)).result()
    swapped = json.loads(json.dumps(template))
    swapped["walkforward"]["objective"] = "$score.metrics.crps"
    assert ZooPick(lanes, ZooResults(str(tmp_path), recipe, swapped)).result()["objective"] == "$score.metrics.crps"


def test_lanes_of_one_scope_scored_on_other_rows_are_refused(tmp_path, loaded):
    recipe, lanes, template, _ = loaded
    tree(tmp_path, loaded, standard, rows_of=lambda s, i: ROWS[s] + (1 if (s, i) == ("pooled", 3) else 0))
    # one pooled lane differs: rewrite it with another row count at fold 3
    cutoffs = FoldSchedule(lanes.base["folds"]).cutoffs()
    write_lane(str(tmp_path), recipe, "mlp-crps-pooled", cutoffs, [1.0] * 14, [ROWS["pooled"]] * 14, template["walkforward"]["objective"])
    with pytest.raises(ValueError, match="other rows"):
        ZooPick(lanes, ZooResults(str(tmp_path), recipe, template)).result()


def test_a_missing_lane_summary_is_refused_by_name(tmp_path, loaded):
    recipe, lanes, template, _ = loaded
    with pytest.raises(ValueError, match="no walk-forward summary"):
        ZooPick(lanes, ZooResults(str(tmp_path), recipe, template)).result()


def test_the_lane_runs_pattern_is_the_manifests_layout(loaded):
    recipe, lanes, _, _ = loaded
    layout = lanes.manifest()["layout"]["fit"]["runs"]
    assert layout.replace("{W}", "{root}/{A}").replace("{A}", "{lane}") == recipe["lane_runs"]
