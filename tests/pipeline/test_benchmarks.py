"""ADR-0097/0099 JSON-declared benchmark protocol tests."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from dskit.pipeline.benchmarks import (
    BenchmarkApproval,
    BenchmarkCompare,
    BenchmarkPlan,
    PathBenchmarkCompare,
    BenchmarkRun,
)
from dskit.pipeline.stages import StageContext


def _context(tmp_path, document=None):
    document = document or SimpleNamespace(hash="b" * 64)
    return StageContext(
        document=document,
        source_path=str(tmp_path / "suite.json"),
        asof="2026-02-28",
        key="stage",
        run_dir=str(tmp_path),
        artifact_dir=str(tmp_path),
    )


def _protocol():
    return {
        "attempt_family": "zoo",
        "primary_score": "ic",
        "null_baseline": "incumbent and ridge",
        "cost_model": "equal pair weight",
        "lockbox": "future",
        "decision_rule": "simplest indistinguishable",
        "alpha": 0.05,
        "comparison_hac_lags": 0,
        "multiplicity_method": "bonferroni_all_pairwise",
        "indifference_method": "paired_fold_newey_west_no_detectable_difference",
    }


def _approval(approved=True):
    return {
        "approved": approved,
        "inventory_sha256": "d" * 64,
        "approved_by": "reviewer" if approved else "PENDING-PLAN-REVIEW",
        "approval_note": "reviewed" if approved else "review first",
        "state": "approved" if approved else "awaiting-plan-review",
    }


def _metadata(candidate_id, compute_class="cpu-small", compute_rank=1):
    return {
        "id": candidate_id,
        "group": "h01",
        "family": candidate_id,
        "representation": "tabular",
        "feature_policy": "intrinsic",
        "seed_policy": "fixed",
        "compute_class": compute_class,
        "compute_rank": compute_rank,
    }


def _summary(path, document_hash, scores, select="max"):
    folds = [
        {
            "cutoff": f"2025-0{index + 1}-01",
            "run_dir": "",
            "state": "ran",
            "score": score,
        }
        for index, score in enumerate(scores)
    ]
    mean = sum(scores) / len(scores)
    payload = {
        "name": "candidate",
        "asof": "2026-02-28",
        "document_hash": document_hash,
        "objective": "$score.metrics.ic",
        "select": select,
        "state": "ran",
        "folds": folds,
        "aggregate": {
            "n_folds": len(scores),
            "n_scored": len(scores),
            "mean": mean,
            "std": 0.0,
            "min": min(scores),
            "max": max(scores),
            "best_cutoff": folds[0]["cutoff"],
            "best_score": scores[0],
        },
    }
    path.mkdir(parents=True)
    (path / "walkforward.json").write_text(json.dumps(payload))
    return [fold["cutoff"] for fold in folds]


def _run_row(candidate_id, document_hash, summary_dir, cutoffs, **metadata):
    return {
        **_metadata(candidate_id, **metadata),
        "state": "ran",
        "exit_code": 0,
        "document_hash": document_hash,
        "summary_dir": str(summary_dir),
        "asof": "2026-02-28",
        "objective": "$score.metrics.ic",
        "select": "max",
        "expected_fold_count": len(cutoffs),
        "expected_cutoffs": cutoffs,
    }


def test_plan_params_default_deny_and_require_disabled_prerequisite():
    params = {
        "candidates": [{**_metadata("future"), "enabled": False}],
        "contract_paths": ["walkforward.objective"],
        "protocol": _protocol(),
        "surprise": True,
    }
    problems = BenchmarkPlan.validate_params(params)
    assert any("unknown param" in problem for problem in problems)
    assert any("prerequisite" in problem for problem in problems)


def test_plan_refuses_outer_fold_search_as_evidence(tmp_path, monkeypatch):
    params = {
        "candidates": [
            {**_metadata("searched"), "path": "candidate.json", "enabled": True}
        ],
        "contract_paths": ["walkforward.objective"],
        "protocol": _protocol(),
    }
    walkforward = SimpleNamespace(
        objective="$score.metrics.ic",
        select="max",
        to_obj=lambda: {"objective": "$score.metrics.ic", "select": "max"},
        fold_cutoffs=lambda: ("2025-01-01", "2025-02-01"),
    )
    document = SimpleNamespace(
        hash="a" * 64,
        name="candidate",
        walkforward=walkforward,
        to_obj=lambda: {"walkforward": {"objective": "$score.metrics.ic"}},
    )
    planned = SimpleNamespace(order=("search",), role_of=lambda key: "search")
    monkeypatch.setattr("dskit.pipeline.benchmarks.load_document", lambda path: document)
    monkeypatch.setattr("dskit.pipeline.benchmarks.plan_document", lambda doc: planned)
    with pytest.raises(ValueError, match="inner-training search"):
        BenchmarkPlan("plan", params).run(_context(tmp_path), {})


def test_approval_keeps_the_first_invocation_plan_only(tmp_path):
    stage = BenchmarkApproval(
        "approval",
        {
            "approved_inventory_sha256": "PENDING-PLAN-REVIEW",
            "approved_by": "PENDING-PLAN-REVIEW",
            "approval_note": "review first",
        },
    )
    result = stage.run(_context(tmp_path), {"inventory_sha256": "a" * 64})
    assert result["approval"]["approved"] is False
    assert result["approval"]["state"] == "awaiting-plan-review"


def test_approval_refuses_inventory_drift(tmp_path):
    stage = BenchmarkApproval(
        "approval",
        {
            "approved_inventory_sha256": "a" * 64,
            "approved_by": "reviewer",
            "approval_note": "reviewed",
        },
    )
    with pytest.raises(ValueError, match="approved inventory hash changed"):
        stage.run(_context(tmp_path), {"inventory_sha256": "c" * 64})


def test_run_refuses_candidate_drift_before_execution(tmp_path, monkeypatch):
    candidate = {
        **_metadata("ridge"),
        "state": "planned",
        "path": "candidate.json",
        "document_hash": "a" * 64,
        "name": "candidate",
        "objective": "$score.metrics.ic",
        "select": "max",
    }
    monkeypatch.setattr(
        "dskit.pipeline.benchmarks.load_document",
        lambda path: SimpleNamespace(hash="c" * 64),
    )
    called = []
    monkeypatch.setattr(
        "dskit.pipeline.benchmarks.run_walk_forward",
        lambda *args, **kwargs: called.append((args, kwargs)),
    )
    inputs = {"candidates": [candidate], "approval": _approval()}
    with pytest.raises(ValueError, match="moved after planning"):
        BenchmarkRun("run").run(_context(tmp_path), inputs)
    assert called == []


def test_pending_approval_never_calls_a_candidate(tmp_path, monkeypatch):
    called = []
    monkeypatch.setattr(
        "dskit.pipeline.benchmarks.run_walk_forward",
        lambda *args, **kwargs: called.append((args, kwargs)),
    )
    candidate = {**_metadata("ridge"), "state": "planned"}
    result = BenchmarkRun("run").run(
        _context(tmp_path),
        {"candidates": [candidate], "approval": _approval(False)},
    )
    assert called == []
    assert result["runs"][0]["state"] == "awaiting_approval"


def test_compare_emits_paired_rows_multiplicity_and_frontier(tmp_path):
    small_hash = "a" * 64
    large_hash = "c" * 64
    small_dir = tmp_path / "small"
    large_dir = tmp_path / "large"
    small_cutoffs = _summary(small_dir, small_hash, [0.10, 0.20])
    large_cutoffs = _summary(large_dir, large_hash, [0.20, 0.30])
    runs = [
        _run_row("small", small_hash, small_dir, small_cutoffs),
        _run_row(
            "large",
            large_hash,
            large_dir,
            large_cutoffs,
            compute_class="cpu-large",
            compute_rank=3,
        ),
        {
            **_metadata("future", "gpu-large", 3),
            "state": "disabled",
            "prerequisite": "true history",
        },
    ]
    result = BenchmarkCompare("compare").run(
        _context(tmp_path),
        {
            "runs": runs,
            "protocol": _protocol(),
            "contracts": {},
            "approval": _approval(),
        },
    )
    assert [row["cutoff"] for row in result["paired"]] == small_cutoffs
    assert result["paired"][0]["scores"] == {"large": 0.2, "small": 0.1}
    assert [row["fixed_compute_rank"] for row in result["ranking"]] == [1, 1]
    assert {row["id"] for row in result["frontier"]} == {"small", "large"}
    assert result["pairwise"][0]["family_size"] == 1
    assert 0.0 < result["pairwise"][0]["p_value"] <= 1.0
    assert result["provenance"]["disabled"][0]["id"] == "future"


def test_compare_refuses_unpaired_outer_folds(tmp_path):
    left_hash = "a" * 64
    right_hash = "c" * 64
    left_dir = tmp_path / "left"
    right_dir = tmp_path / "right"
    left_cutoffs = _summary(left_dir, left_hash, [0.1, 0.2])
    right_cutoffs = _summary(right_dir, right_hash, [0.1])
    runs = [
        _run_row("left", left_hash, left_dir, left_cutoffs),
        _run_row("right", right_hash, right_dir, right_cutoffs),
    ]
    with pytest.raises(ValueError, match="ordered cutoffs"):
        BenchmarkCompare("compare").run(
            _context(tmp_path),
            {
                "runs": runs,
                "protocol": _protocol(),
                "contracts": {},
                "approval": _approval(),
            },
        )


def test_compare_emits_no_launch_evidence_while_pending(tmp_path):
    result = BenchmarkCompare("compare").run(
        _context(tmp_path),
        {
            "runs": [{**_metadata("ridge"), "state": "awaiting_approval"}],
            "protocol": _protocol(),
            "contracts": {},
            "approval": _approval(False),
        },
    )
    assert result["ranking"] == []
    assert result["provenance"]["no_launch"] is True


def test_run_recovers_a_complete_summary_after_checkpoint_window(
    tmp_path, monkeypatch
):
    document_hash = "a" * 64
    cutoffs = ["2025-01-01", "2025-02-01"]
    document = SimpleNamespace(
        hash=document_hash,
        name="candidate",
        outputs=SimpleNamespace(run_root=str(tmp_path / "runs")),
    )
    candidate = {
        **_metadata("ridge"),
        "state": "planned",
        "path": "candidate.json",
        "document_hash": document_hash,
        "name": "candidate",
        "asof": "2026-02-28",
        "objective": "$score.metrics.ic",
        "select": "max",
        "expected_fold_count": 2,
        "expected_cutoffs": cutoffs,
    }
    monkeypatch.setattr(
        "dskit.pipeline.benchmarks.load_document", lambda path: document
    )
    expected = tmp_path / "runs" / (
        f"candidate-walkforward-2026-02-28-{document_hash[:8]}"
    )

    def interrupted(*args, **kwargs):
        _summary(expected, document_hash, [0.1, 0.2])
        raise RuntimeError("interrupted after summary")

    monkeypatch.setattr(
        "dskit.pipeline.benchmarks.run_walk_forward", interrupted
    )
    stage = BenchmarkRun("run")
    inputs = {"candidates": [candidate], "approval": _approval()}
    with pytest.raises(RuntimeError, match="interrupted after summary"):
        stage.run(_context(tmp_path), inputs)

    called = []
    monkeypatch.setattr(
        "dskit.pipeline.benchmarks.run_walk_forward",
        lambda *args, **kwargs: called.append((args, kwargs)),
    )
    result = stage.run(_context(tmp_path), inputs)
    assert called == []
    assert result["runs"][0]["state"] == "ran"
    assert result["runs"][0]["recovered_after_interruption"] is True



def test_path_compare_persists_loss_tensor_and_uncertainty_sets(tmp_path):
    pytest.importorskip("pyarrow")
    from dskit.pipeline.predictions import PredictionWriter

    runs = []
    for candidate_id, yhat in (("strong", 0.8), ("weak", 0.1)):
        document_hash = ("a" if candidate_id == "strong" else "c") * 64
        summary_dir = tmp_path / f"{candidate_id}-summary"
        cutoffs = _summary(summary_dir, document_hash, [0.4, 0.3])
        payload = json.loads((summary_dir / "walkforward.json").read_text())
        for fold_index, fold in enumerate(payload["folds"]):
            run_dir = tmp_path / f"{candidate_id}-fold-{fold_index}"
            stamps = [
                (10 + fold_index * 10 + day) * 86_400_000 + 60_000
                for day in range(4)
            ]
            realized = [1.0, -1.0, 1.0, -1.0]
            for lead in (1, 2):
                with PredictionWriter(
                    str(run_dir / "artifacts" / f"scan_h{lead:02d}"),
                    ["JPM"],
                    fold=fold_index,
                    period_minutes=1,
                ) as writer:
                    writer.append(
                        "JPM", lead, stamps, realized,
                        [yhat if value > 0 else -yhat for value in realized], 0.0,
                    )
            (run_dir / "carry.json").write_text(
                json.dumps(
                    {
                        "path": {
                            "records": [
                                {"lead": 1, "train_scale": 1.0},
                                {"lead": 2, "train_scale": 1.0},
                            ]
                        }
                    }
                )
            )
            fold["run_dir"] = str(run_dir)
        (summary_dir / "walkforward.json").write_text(json.dumps(payload))
        row = _run_row(
            candidate_id,
            document_hash,
            summary_dir,
            cutoffs,
            compute_class="cpu-small" if candidate_id == "strong" else "cpu-large",
            compute_rank=1 if candidate_id == "strong" else 3,
        )
        row.update(
            {
                "forecast_strategy": "direct_per_lead",
                "max_horizon": 2,
                "leads": [1, 2],
                "horizon_weights": [0.5, 0.5],
            }
        )
        runs.append(row)
    protocol = _protocol()
    protocol.update(
        {
            "path_evidence": "path.records",
            "path_loss": "training-scale-normalized squared-error improvement",
            "path_resampling": "whole UTC trading sessions",
            "horizon_diagnostic": "bootstrap horizon confidence sets",
            "path_selection": "bootstrap model confidence set and SPA",
            "tie_break": "fold stability, worst-horizon regret, compute rank, id",
        }
    )
    result = PathBenchmarkCompare(
        "compare", {"bootstrap_draws": 99, "bootstrap_seed": 0}
    ).run(
        _context(tmp_path),
        {
            "runs": runs,
            "protocol": protocol,
            "contracts": {},
            "approval": _approval(),
        },
    )
    assert result["loss_evidence"]["rows"] == 32
    assert Path(result["loss_evidence"]["path"]).is_file()
    assert {row["lead"] for row in result["horizon_confidence_sets"]} == {1, 2}
    assert result["model_confidence_sets"][0]["best_mean"] == "strong"
    assert result["selection"][0]["candidate"] == "strong"
    assert result["selection"][0]["auto_promote"] is False
    assert len(result["superior_predictive_ability"]) == 2
    assert all(
        row["average"]["method"]
        == "shared recentered whole-session family max-t"
        for row in result["superior_predictive_ability"]
    )


def test_path_compare_is_no_launch_while_approval_is_pending(tmp_path):
    protocol = _protocol()
    result = PathBenchmarkCompare(
        "compare", {"bootstrap_draws": 99, "bootstrap_seed": 0}
    ).run(
        _context(tmp_path),
        {
            "runs": [{**_metadata("ridge"), "state": "awaiting_approval"}],
            "protocol": protocol,
            "contracts": {},
            "approval": _approval(False),
        },
    )
    assert result["loss_evidence"] == {}
    assert result["selection"] == []
    assert result["provenance"]["no_launch"] is True
