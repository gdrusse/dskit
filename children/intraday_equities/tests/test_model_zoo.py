"""Post-Gate-3 model-zoo materialization and representation tests."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from dskit.pipeline.document import load_document

from intraday_equities.model_zoo import (
    DirectPathScore,
    EmpiricalSelectRegressor,
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
    assert [
        key for key in document["pipeline"] if key.startswith("scan_h")
    ] == [f"scan_h{lead:02d}" for lead in range(1, 6)]
    for lead in range(1, 6):
        params = document["pipeline"][f"scan_h{lead:02d}"]["params"]
        assert (params["lead_start"], params["lead_step"], params["lead_stop"]) == (
            lead,
            lead,
            lead,
        )
        assert params["common_lead_stop"] == 5
    assert document["pipeline"]["path"]["params"]["max_horizon"] == 5
    assert document["walkforward"]["objective"] == "$path.metrics.path_score"
