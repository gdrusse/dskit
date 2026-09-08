"""Post-Gate-3 model-zoo materialization and representation tests."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from dskit.pipeline.document import load_document
from dskit.pipeline.predictions import PredictionWriter, find_predictions

from intraday_equities.model_zoo import (
    DirectPathScore,
    EmpiricalSelectRegressor,
    FinalistCandidate,
    FinalModelGateInventory,
    FinalModelGates,
    KronosFusionRows,
    PooledDirectPathScore,
    PooledGate3ZooCandidates,
    SequenceFusionRows,
    SequenceOnlyZooEstimator,
    _cache_for,
    _document,
    _gate3_rows,
)


def _pass_row(index):
    return {
        "asset": f"A{index:02d}",
        "gate1_h": 1 + index % 10,
        "gate3_passes": True,
        "gate3_status": "pass",
    }


def _write_json(path, payload):
    import json

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return __import__("hashlib").sha256(path.read_bytes()).hexdigest()


def _final_gate_fixture(tmp_path):
    from datetime import datetime, timezone

    summary_dir = tmp_path / "winner-walkforward"
    fold_dirs = [tmp_path / "fold-1", tmp_path / "fold-2"]
    for fold_index, fold_dir in enumerate(fold_dirs):
        stamps = [
            int(datetime(2024 + fold_index, month, 15, 15, tzinfo=timezone.utc).timestamp() * 1000)
            for month in (1, 4, 7, 10)
        ]
        for horizon in (1, 2):
            y = [-1.0, 1.0, -1.0, 1.0]
            yhat = [0.5 * value for value in y]
            if horizon == 2:
                yhat[2] = 0.0
            with PredictionWriter(
                str(fold_dir / "artifacts" / f"scan_h{horizon:02d}"),
                ["AAA"], fold=fold_index, period_minutes=1,
            ) as writer:
                writer.append("AAA", horizon, stamps, y, yhat, 0.0)
    summary_path = summary_dir / "walkforward.json"
    summary_sha = _write_json(
        summary_path,
        {
            "state": "ran", "document_hash": "c" * 64,
            "asof": "2026-02-28", "objective": "$path.metrics.path_score",
            "select": "max",
            "folds": [
                {"cutoff": f"fold-{index}", "state": "ran", "run_dir": str(path)}
                for index, path in enumerate(fold_dirs, 1)
            ],
        },
    )
    manifest = {
        "schema_version": 1,
        "benchmark_identity": "a" * 64,
        "evidence_scope": "developmental_post_selection",
        "winner": {
            "id": "lean-pooled-h02", "feature_policy": "lean mask",
            "mean_path_score": 0.2,
            "selection_rule": "simplicity_heuristic_after_no_detected_difference",
            "document_hash": "c" * 64, "asof": "2026-02-28",
            "objective": "$path.metrics.path_score", "select": "max",
        },
        "summary": {"path": str(summary_path), "sha256": summary_sha},
        "expected_units": [
            {"symbol": "AAA", "horizon": 1},
            {"symbol": "AAA", "horizon": 2},
        ],
        "folds": [
            {
                "cutoff": f"fold-{index}", "run_dir": str(path),
                "predictions": [
                    {"path": prediction, "sha256": __import__("hashlib").sha256(Path(prediction).read_bytes()).hexdigest()}
                    for prediction in find_predictions(str(path))
                ],
            }
            for index, path in enumerate(fold_dirs, 1)
        ],
    }
    manifest_path = tmp_path / "manifest.json"
    manifest_sha = _write_json(manifest_path, {"outputs": {"manifest": manifest}})
    params = {
        "manifest_artifact": str(manifest_path), "manifest_sha256": manifest_sha,
        "alpha": 0.05, "correction": "bonferroni",
        "evidence_scope": "developmental_post_selection",
        "season_timezone": "America/New_York",
        "season_months": {
            "winter": [12, 1, 2], "spring": [3, 4, 5],
            "summer": [6, 7, 8], "fall": [9, 10, 11],
        },
    }
    return params, manifest_path, Path(manifest["folds"][0]["predictions"][0]["path"])


def test_final_model_gate_inventory_pins_the_approved_complete_ladder(
    tmp_path, monkeypatch,
):
    from types import SimpleNamespace

    candidate_path = tmp_path / "candidate.json"
    candidate_path.write_text("{}", encoding="utf-8")
    fold_dirs = [tmp_path / "fold-1", tmp_path / "fold-2"]
    cutoffs = ["2024-01-01", "2025-01-01"]
    folds = []
    for cutoff, fold_dir in zip(cutoffs, fold_dirs):
        prediction = fold_dir / "artifacts" / "scan" / "predictions.parquet"
        prediction.parent.mkdir(parents=True)
        prediction.write_bytes(cutoff.encode())
        carry = fold_dir / "carry.json"
        carry.write_text("{}")
        folds.append({"cutoff": cutoff, "state": "ran", "run_dir": str(fold_dir)})
    summary_dir = tmp_path / "summary"
    summary_dir.mkdir()
    summary_path = summary_dir / "walkforward.json"
    summary_sha = _write_json(
        summary_path,
        {
            "state": "ran", "document_hash": "c" * 64,
            "asof": "2026-02-28", "objective": "$path.metrics.path_score",
            "select": "max", "folds": folds,
            "evidence": {
                "schema_version": 2,
                "contract": "walkforward_fold_artifacts_at_summary_publish",
                "folds": [
                    {
                        "cutoff": cutoff,
                        "run_dir": str(fold_dir),
                        "carry": {
                            "path": str(fold_dir / "carry.json"),
                            "sha256": __import__("hashlib").sha256(
                                (fold_dir / "carry.json").read_bytes()
                            ).hexdigest(),
                        },
                        "predictions": [{
                            "path": str(
                                fold_dir / "artifacts" / "scan"
                                / "predictions.parquet"
                            ),
                            "sha256": __import__("hashlib").sha256(
                                cutoff.encode()
                            ).hexdigest(),
                        }],
                    }
                    for cutoff, fold_dir in zip(cutoffs, fold_dirs)
                ],
            },
        },
    )
    run = {
        "stage_token": "bench:run",
        "outputs": {"runs": [{
            "id": "lean", "state": "ran", "path": str(candidate_path),
            "document_hash": "c" * 64, "summary_dir": str(summary_dir),
            "evidence_manifest_path": str(summary_path),
            "evidence_manifest_sha256": summary_sha,
            "expected_fold_count": 2, "expected_cutoffs": cutoffs,
            "asof": "2026-02-28", "objective": "$path.metrics.path_score",
            "select": "max", "feature_policy": "lean mask",
        }]},
    }
    compare = {
        "stage_token": "bench:compare",
        "outputs": {"ranking": [{
            "id": "lean", "mean": 0.1,
            "selected_simplest_not_detectably_different": True,
        }], "provenance": {
            "benchmark_hash": "bench",
            "asof": "2026-02-28",
        }},
    }
    run_path, compare_path = tmp_path / "run.json", tmp_path / "compare.json"
    run_sha = _write_json(run_path, run)
    compare_sha = _write_json(compare_path, compare)

    fake_document = SimpleNamespace(
        hash="c" * 64,
        pipeline={
            "path": SimpleNamespace(
                params={"asset_horizons": [
                    {"asset": "AAA", "horizon": 2},
                    {"asset": "BBB", "horizon": 1},
                ]}
            )
        },
    )
    monkeypatch.setattr(
        "dskit.pipeline.document.load_document", lambda _: fake_document
    )
    monkeypatch.setattr(
        "dskit.pipeline.predictions.find_predictions",
        lambda run_dir: [str(Path(run_dir) / "artifacts" / "scan" / "predictions.parquet")],
    )
    stage = FinalModelGateInventory("inventory", {
        "run_artifact": str(run_path), "run_sha256": run_sha,
        "compare_artifact": str(compare_path), "compare_sha256": compare_sha,
    })
    result = stage.run(SimpleNamespace(source_path=str(tmp_path / "gate.json")), {})
    manifest = result["manifest"]
    assert manifest["evidence_scope"] == "developmental_post_selection"
    assert manifest["winner"]["selection_rule"] == (
        "simplicity_heuristic_after_no_detected_difference"
    )
    assert manifest["expected_units"] == [
        {"symbol": "AAA", "horizon": 1},
        {"symbol": "AAA", "horizon": 2},
        {"symbol": "BBB", "horizon": 1},
    ]
    assert [row["cutoff"] for row in manifest["folds"]] == cutoffs
    assert all(len(row["predictions"]) == 1 for row in manifest["folds"])


def test_final_model_gate_inventory_refuses_cross_asof_comparison():
    from intraday_equities.final_gates import _validate_compare_provenance

    compare = {
        "outputs": {
            "provenance": {
                "benchmark_hash": "bench",
                "asof": "2025-02-28",
            }
        }
    }
    with pytest.raises(ValueError, match="identity and asof"):
        _validate_compare_provenance(
            compare, "bench", {"asof": "2026-02-28"}
        )


def test_pinned_json_parses_the_same_bytes_whose_digest_it_verifies(
    tmp_path, monkeypatch,
):
    import intraday_equities.final_gates as final_gates

    path = tmp_path / "artifact.json"
    original = b'{"value": "sealed"}'
    path.write_bytes(original)
    digest = __import__("hashlib").sha256(original).hexdigest()

    def old_two_open_digest(target):
        observed = __import__("hashlib").sha256(Path(target).read_bytes()).hexdigest()
        Path(target).write_bytes(b'{"value": "swapped"}')
        return observed

    monkeypatch.setattr(final_gates, "_digest", old_two_open_digest)
    _, value = final_gates._read_pinned_json(
        str(tmp_path / "config.json"), str(path), digest, "artifact"
    )
    assert value == {"value": "sealed"}
    assert path.read_bytes() == original


def test_final_model_gates_use_pinned_folds_and_stop_on_a_flat_season(tmp_path):
    from types import SimpleNamespace

    params, _, _ = _final_gate_fixture(tmp_path)
    stage = FinalModelGates("gates", params)
    ctx = SimpleNamespace(source_path=str(tmp_path / "gates.json"))
    result = stage.run(ctx, {})
    assert result["winner"]["id"] == "lean-pooled-h02"
    assert result["caps"] == [{
        "unit": "AAA", "capped_horizon": 1, "first_failing_horizon": 2,
        "n_passed": 1, "n_horizons": 2,
        "passing_checks": ["beats_mean", "skill_pass_adjusted", "season_r2oos"],
        "slice_evidence": {season: True for season in ("fall", "spring", "summer", "winter")},
    }]
    summer_h2 = next(
        row for row in result["evidence"]
        if row["horizon"] == 2 and row["season"] == "summer"
    )
    assert summer_h2["season_r2oos"] == pytest.approx(0.0)
    assert summer_h2["skill_pass_raw"] is True
    assert summer_h2["skill_pass_adjusted"] is True
    assert summer_h2["family_size"] == 2
    assert result["metrics"]["n_served_units"] == 1
    assert result["metrics"]["deployment_eligible"] is False


def test_final_model_gates_refuse_prediction_digest_drift(tmp_path):
    from types import SimpleNamespace

    params, _, prediction = _final_gate_fixture(tmp_path)
    prediction.write_bytes(prediction.read_bytes() + b"tamper")
    stage = FinalModelGates("gates", params)
    ctx = SimpleNamespace(source_path=str(tmp_path / "gates.json"))
    with pytest.raises(ValueError, match="prediction artifact hash changed"):
        stage.run(ctx, {})


def test_final_model_gates_refuse_consistently_missing_approved_unit(tmp_path):
    import json
    from types import SimpleNamespace

    params, manifest_path, _ = _final_gate_fixture(tmp_path)
    artifact = json.loads(manifest_path.read_text())
    artifact["outputs"]["manifest"]["expected_units"].append({"symbol": "BBB", "horizon": 1})
    params["manifest_sha256"] = _write_json(manifest_path, artifact)
    stage = FinalModelGates("gates", params)
    ctx = SimpleNamespace(source_path=str(tmp_path / "gates.json"))
    with pytest.raises(ValueError, match="prediction units differ from approved inventory"):
        stage.run(ctx, {})


def test_gate3_inventory_requires_exactly_twenty_five_unique_passers():
    rows = [_pass_row(index) for index in range(25)]
    rows.append(
        {
            "asset": "FAILED",
            "gate1_h": None,
            "gate3_passes": False,
            "gate3_status": "not_reached",
        }
    )
    artifact = {"outputs": {"rows": rows}}
    eligible = _gate3_rows(artifact)
    assert len(eligible) == 25
    assert {row["asset"] for row in eligible} == {
        f"A{index:02d}" for index in range(25)
    }


def test_gate3_inventory_refuses_duplicate_asset_horizon_families():
    rows = [_pass_row(index) for index in range(25)]
    artifact = {"outputs": {"rows": rows + [dict(rows[0])]}}
    with pytest.raises(ValueError, match="duplicate Gate-3 family"):
        _gate3_rows(artifact)


def test_cache_assignment_must_be_exactly_one_group():
    groups = {
        "a": {
            "symbols": ["SPY", "AAA"],
            "cache": "cache/a",
            "manifest_sha256": "a" * 64,
            "universe": "configs/a.json",
        },
        "b": {
            "symbols": ["SPY", "BBB"],
            "cache": "cache/b",
            "manifest_sha256": "b" * 64,
            "universe": "configs/b.json",
        },
    }
    assert _cache_for("AAA", groups)["cache"] == "cache/a"
    with pytest.raises(ValueError, match="belongs to 0 caches"):
        _cache_for("CCC", groups)


def test_empirical_selector_excludes_symbol_code_and_predicts():
    x = np.asarray(
        [
            [0.0, 10.0, 0.0],
            [1.0, 9.0, 0.0],
            [2.0, 8.0, 0.0],
            [3.0, 7.0, 0.0],
        ]
    )
    y = np.asarray([0.0, 1.0, 2.0, 3.0])
    model = EmpiricalSelectRegressor(
        "sklearn.linear_model.Ridge", k_features=1, scale=True, alpha=0.1
    )
    model.fit(x, y, feature_names=["ret_lag_0", "vol", "symbol_code"])
    assert model._indices == [0, 1]
    assert model.predict(x).shape == (4,)


def test_sequence_estimator_uses_only_contiguous_return_history(monkeypatch):
    captured = {}

    class FakeZoo:
        def __init__(self, arch, **knobs):
            captured["arch"] = arch
            captured["knobs"] = knobs

        def fit(self, x, y, feature_names=None):
            captured["fit_x"] = x
            captured["feature_names"] = feature_names
            return self

        def predict(self, x):
            captured["predict_x"] = x
            return np.zeros(x.shape[0])

    monkeypatch.setattr("intraday_equities.model_zoo.ZooEstimator", FakeZoo)
    x = np.arange(12, dtype=float).reshape(3, 4)
    model = SequenceOnlyZooEstimator("gru", context_length=2, epochs=1)
    model.fit(
        x,
        np.zeros(3),
        feature_names=["ret_lag_0", "vol", "ret_lag_1", "symbol_code"],
    )
    model.predict(x)
    assert captured["feature_names"] == ["ret_lag_0", "ret_lag_1"]
    assert captured["fit_x"].shape == (3, 2)
    assert captured["predict_x"].shape == (3, 2)


def test_direct_path_score_aggregates_complete_common_origin_heads():
    node = DirectPathScore(
        "path",
        {
            "split": "val",
            "asset": "JPM",
            "max_horizon": 2,
            "horizon_weights": [0.5, 0.5],
            "score": "train_scaled_improvement",
        },
    )
    inputs = {}
    for lead, score in ((1, 0.2), (2, -0.1)):
        inputs[f"records_h{lead:02d}"] = [
            {
                "symbol": "JPM",
                "lead": lead,
                "n": 12.0,
                "train_scale": 0.01,
                "train_scaled_improvement": score,
                "origin_sha256": "a" * 64,
            }
        ]
        inputs[f"metrics_h{lead:02d}"] = {
            "train_ic": 0.1,
            "val_ic": 0.05,
            "train_calibration_slope": 1.0,
            "val_calibration_slope": 0.9,
        }
    result = node.run(None, inputs)
    assert result["metrics"]["path_score"] == pytest.approx(0.05)
    assert result["metrics"]["worst_horizon_score"] == pytest.approx(-0.1)
    assert result["metrics"]["n_common_origins"] == 12.0
    assert [row["lead"] for row in result["records"]] == [1, 2]
    inputs["records_h02"][0]["origin_sha256"] = "b" * 64
    with pytest.raises(ValueError, match="common validation origins"):
        node.run(None, inputs)


def test_candidate_document_emits_every_lead_through_stock_h_i():
    root = Path(__file__).parents[1]
    source = load_document(str(root / "configs" / "run-p12-modelability.json"))
    template = {
        "id": "ridge",
        "model": {
            "estimator": "sklearn.linear_model.Ridge",
            "estimator_params": {"alpha": 1.0},
        },
    }
    cache = {
        "universe": "configs/universe-p12-a.json",
        "cache": "./pipeline_cache/p12/a",
        "manifest_sha256": "a" * 64,
    }
    document = _document(
        source, template, "JPM", 5, cache, "ridge-jpm-h05", [0.2] * 5
    ).to_obj()
    assert "scan" not in document["pipeline"]
    assert [key for key in document["pipeline"] if key.startswith("scan_h")] == [
        f"scan_h{lead:02d}" for lead in range(1, 6)
    ]
    for lead in range(1, 6):
        params = document["pipeline"][f"scan_h{lead:02d}"]["params"]
        assert (params["lead_start"], params["lead_step"], params["lead_stop"]) == (
            lead,
            lead,
            lead,
        )
        assert params["common_lead_stop"] == 5
        assert params["common_origin_policy"] == "all_head_labels_finite"
    assert document["pipeline"]["path"]["params"]["max_horizon"] == 5
    assert document["walkforward"]["objective"] == "$path.metrics.path_score"


def test_pooled_path_score_weights_assets_then_their_heads_equally():
    eligible = [{"asset": "AAA", "horizon": 1}, {"asset": "BBB", "horizon": 2}]
    node = PooledDirectPathScore(
        "path",
        {
            "split": "val",
            "asset_horizons": eligible,
            "horizon_weighting": "equal_asset_equal_within_asset",
            "score": "train_scaled_improvement",
        },
    )
    inputs = {
        "records_h01": [
            {
                "symbol": "AAA",
                "lead": 1,
                "n": 10,
                "origin_sha256": "a" * 64,
                "train_scaled_improvement": 0.4,
            },
            {
                "symbol": "BBB",
                "lead": 1,
                "n": 12,
                "origin_sha256": "b" * 64,
                "train_scaled_improvement": 0.2,
            },
        ],
        "metrics_h01": {"val_ic": 0.1},
        "records_h02": [
            {
                "symbol": "BBB",
                "lead": 2,
                "n": 12,
                "origin_sha256": "b" * 64,
                "train_scaled_improvement": -0.2,
            },
        ],
        "metrics_h02": {"val_ic": 0.0},
    }
    result = node.run(None, inputs)
    assert result["metrics"]["path_score"] == pytest.approx(0.2)
    assert sum(row["weight"] for row in result["records"]) == pytest.approx(1.0)
    assert result["metrics"]["n_asset_paths"] == 2.0


def test_pooled_materializer_emits_four_valid_shared_fit_documents(tmp_path):
    from types import SimpleNamespace

    from dskit.pipeline.planner import plan

    root = Path(__file__).parents[1]
    source_path = root / "configs" / "run-p13-pooled-model-zoo.json"
    source = load_document(str(source_path))
    params = source.to_obj()["stages"]["materialize"]["params"]
    stage = PooledGate3ZooCandidates("materialize", params)
    memory_path = (
        root
        / "pipeline_runs"
        / "p12-63-asset-modelability-staged-2026-02-28-2d203f5c"
        / "stages"
        / "memory.json"
    )
    with open(memory_path) as handle:
        caches = __import__("json").load(handle)["outputs"]["groups"]
    ctx = SimpleNamespace(
        source_path=str(source_path), document=source,
        artifact_dir=str(tmp_path / "materialize"),
    )
    result = stage.run(ctx, {"preflight": True, "caches": caches})
    assert [row["family"] for row in result["candidates"]] == [
        "pooled-lightgbm",
        "pooled-torch-mlp",
        "pooled-kronos-lightgbm",
        "pooled-kronos-torch-mlp",
    ]
    for candidate in result["candidates"]:
        document = load_document(candidate["path"])
        plan(document)
        first = document.pipeline["scan_h01"].params
        assert len(first["fit_symbols"]) == 25
        assert len(first["score_symbols"]) == 25
        assert document.pipeline["scan_h10"].params["score_symbols"] == [
            "LRCX", "MSTR"
        ]
        assert document.pipeline["pooled_features"].uses == "concat"
        assert set(document.pipeline["pooled_features"].inputs) == {"a", "c", "d", "e"}
        assert document.pipeline["reference_tape"].uses == "concat"
    for candidate in result["candidates"][2:]:
        document = load_document(candidate["path"])
        assert document.pipeline["kronos"].params["input_identity"]
        assert document.pipeline["scan_h01"].inputs["records"] == (
            "$fusion_features.records"
        )


def test_kronos_fusion_inner_aligns_and_allows_only_declared_side_features():
    features = [
        {
            "symbol": "AAA",
            "asof_ms": np.array([1, 2, 3]),
            "close": np.array([10.0, 11.0, 12.0]),
            "names": ["ret_lag_0", "tod_sin"],
            "X": np.array([[9.0, 0.1], [8.0, 0.2], [7.0, 0.3]]),
        }
    ]
    embeddings = [
        {
            "symbol": "AAA",
            "asof_ms": np.array([1, 3]),
            "names": ["kronos_000", "kronos_001"],
            "X": np.array([[1.0, 2.0], [3.0, 4.0]]),
        }
    ]
    node = KronosFusionRows("fusion", {"feature_names": ["tod_sin"]})
    out = node.run(None, {"features": features, "embeddings": embeddings})
    frame = out["records"][0]
    assert frame["names"] == ["kronos_000", "kronos_001", "tod_sin"]
    np.testing.assert_allclose(frame["X"][:, -1], [0.1, 0.3])
    np.testing.assert_allclose(frame["close"], [10.0, 12.0])


def test_sequence_fusion_inner_aligns_and_allows_only_declared_side_features():
    features = [
        {
            "symbol": "AAA",
            "asof_ms": np.array([1, 2, 3]),
            "close": np.array([10.0, 11.0, 12.0]),
            "names": ["ret_lag_0", "tod_sin"],
            "X": np.array([[9.0, 0.1], [8.0, 0.2], [7.0, 0.3]]),
        }
    ]
    sequences = [
        {
            "symbol": "AAA",
            "asof_ms": np.array([1, 3]),
            "names": ["ohlcv_t000_open", "ohlcv_t000_close"],
            "X": np.array([[1.0, 2.0], [3.0, 4.0]]),
        }
    ]
    node = SequenceFusionRows("fusion", {"feature_names": ["tod_sin"]})
    out = node.run(None, {"features": features, "sequences": sequences})
    frame = out["records"][0]
    assert frame["names"] == [
        "ohlcv_t000_open", "ohlcv_t000_close", "tod_sin"
    ]
    np.testing.assert_allclose(frame["X"][:, -1], [0.1, 0.3])
    np.testing.assert_allclose(frame["close"], [10.0, 12.0])


def _finalist_stage():
    document = load_document(
        str(Path(__file__).parents[1] / "configs" / "run-final-hpo.json")
    )
    params = document.stages["finalist"].params
    return FinalistCandidate("finalist", params)


def test_the_finalist_stage_requires_the_selectors_choice():
    """Its parent takes preflight and caches; this one also takes a winner."""
    stage = _finalist_stage()
    assert stage.validate_inputs({"preflight": True, "caches": {}}) == [
        "inputs must contain exactly preflight, caches and selection"
    ]


def test_the_finalist_stage_refuses_a_selection_that_claims_promotion():
    """`final_hpo` selects hyperparameters; nothing here promotes a model."""
    stage = _finalist_stage()
    problems = stage.validate_inputs(
        {
            "preflight": True,
            "caches": {},
            "selection": {"candidate": "lgbm-pooled-h10", "auto_promote": True},
        }
    )
    assert problems == [
        "selection.auto_promote must be False — this phase never promotes"
    ]


def test_the_finalist_stage_refuses_a_nameless_selection():
    """A selection with no candidate would leave WHICH model undecided."""
    stage = _finalist_stage()
    problems = stage.validate_inputs(
        {"preflight": True, "caches": {}, "selection": {"auto_promote": False}}
    )
    assert problems == ["selection.candidate must name the selected candidate"]


def test_the_finalist_stage_accepts_a_well_formed_selection():
    """The shape BenchmarkSelect actually emits must pass."""
    stage = _finalist_stage()
    assert (
        stage.validate_inputs(
            {
                "preflight": True,
                "caches": {},
                "selection": {
                    "candidate": "lgbm-pooled-h10",
                    "auto_promote": False,
                },
            }
        )
        == []
    )
