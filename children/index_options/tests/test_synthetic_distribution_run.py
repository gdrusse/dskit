"""The shipped synthetic distribution harness runs end to end (ADR-0168)."""

import json

from dskit.pipeline.document import PipelineDocument
from dskit.pipeline.driver import run_walk_forward
from dskit.pipeline.planner import plan


def _document(child_root, run_root, **market):
    obj = json.loads((child_root / "configs/run-synthetic-distribution.json").read_text())
    obj["pipeline"]["market"]["params"].update(market)
    obj["outputs"]["run_root"] = str(run_root)
    return PipelineDocument.from_obj(obj)


def test_config_plans_with_every_node(child_root, tmp_path):
    the_plan = plan(_document(child_root, tmp_path))
    assert {"market", "rv", "labels", "model", "score", "condor"} <= set(the_plan.order)


def test_walk_forward_scores_every_fold_on_the_locked_metrics(child_root, tmp_path):
    result = run_walk_forward(_document(child_root, tmp_path), asof="1978-06-01")
    folds = result.folds
    assert len(folds) == 4 and all(f["state"] == "ran" for f in folds)
    assert all(0 < f["score"] < 2 for f in folds)
    carry = json.loads((tmp_path / folds[-1]["run_dir"] / "carry.json").read_text())
    score, condor = carry["score"]["metrics"], carry["condor"]["metrics"]
    assert score["n"] > 0 and score["twcrps"] < score["crps"]
    for key in ("brier", "pit_ks_pvalue", "berkowitz_pvalue"):
        assert key in score
    assert condor["n"] == score["n"] and "realized_mean_pnl" in condor


def test_the_path_seed_moves_the_scores(child_root, tmp_path):
    a = run_walk_forward(_document(child_root, tmp_path / "a"), asof="1978-06-01")
    b = run_walk_forward(_document(child_root, tmp_path / "b", seed=8), asof="1978-06-01")
    assert [f["score"] for f in a.folds] != [f["score"] for f in b.folds]
