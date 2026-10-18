"""Prove chronological development/validation boundaries and causal features."""
import copy
import json
import math
from pathlib import Path

import pytest

from dskit.pipeline.kinds_compute import ComputeFields
from dskit.pipeline.kinds_flow import Derive
from dskit.pipeline.libs.sklearn import SklearnExpandingRefit
from dskit.pipeline.node import NodeContext

ROOT = Path(__file__).parents[1] / "configs"
PATHS = sorted(ROOT.glob("run-warmup20-*.json"))
CUT = 1777680000000
DAY = 86400000


def test_complete_twenty_pairs():
    assert len(PATHS) == 114


@pytest.mark.parametrize("path", PATHS, ids=lambda p: p.stem)
def test_stage_boundaries_and_features(path):
    doc = json.loads(path.read_text())
    nodes = doc["pipeline"]
    valid = "-validation-" in path.name
    assert nodes["data"]["params"]["read_bounds"]["day"]["end_before"] == (
        "2026-08-01" if valid else "2026-05-02")
    row = dict(ticker="test", mid=.15, fair_gru=.2, label=0, lead_minutes=5,
               asset="ETH", day="2026-02-20", band="test", decision_ms=1771545600000,
               settlement_ms=1771545900000, log_moneyness=.0001, rv_rms_60=.0001,
               rv_ewma_30=.00012, spread=.01, candle_volume=200,
               candle_open_interest=2000)
    rows = [row, dict(row, label=1, settlement_ms=row["settlement_ms"] + 999999)]
    for key in ["features", "asset", "extended", "basis", "weighted", "group"]:
        if key in nodes:
            cls = Derive if nodes[key]["uses"] == "derive" else ComputeFields
            rows = cls(key, nodes[key]["params"]).run(None, {"records": rows})["records"]
    for key in ["fit", "prior_ard", "prior_newton", "calibration_baseline"]:
        fit = nodes[key]["params"]
        assert fit["known_field"] == "known_ms"
        for spec in fit["features"]:
            a, b = [r[spec["field"]] for r in rows]
            assert math.isfinite(a) and a == b
        if valid:
            assert fit["block_ms"] == 365 * DAY
            assert fit["block_origin_ms"] == CUT - 365 * DAY
            assert fit["min_train_blocks"] == 1
    counts = dict(predictions=84728 if valid else 52999,
                  markets=17158 if valid else 11213,
                  cells=100, leads=5, asset_leads=10, calibration=50)
    for key, count in counts.items():
        assert nodes["write_" + key]["params"]["expect"] == count
    assert {"delta_newton_logloss", "delta_newton_brier"} <= set(
        nodes["paired"]["params"]["fields"])


def test_validation_labels_cannot_change_frozen_predictions(tmp_path):
    """Exercise the actual generic refitter, including the embargo boundary."""
    params = dict(estimator="sklearn.linear_model.Ridge",
                  estimator_params={"alpha": 1.0},
                  features=[{"field": "x", "transform": "identity"}],
                  label="y", time_field="decision_ms", known_field="known_ms",
                  block_ms=365 * DAY, block_origin_ms=CUT - 365 * DAY,
                  min_train_blocks=1, output_field="p", predict_method="predict")
    rows = [dict(x=i, y=i % 2, decision_ms=CUT-3*DAY,
                 known_ms=CUT-DAY) for i in range(12)]
    rows += [dict(x=2, y=1000, decision_ms=CUT-DAY, known_ms=CUT)]
    rows += [dict(x=i, y=i % 2, decision_ms=CUT+i*DAY, known_ms=CUT+(i+1)*DAY)
             for i in range(10)]
    def run(data, name):
        ctx = NodeContext(name=name, asof="2026-10-18", run_dir=str(tmp_path / name))
        return SklearnExpandingRefit("fit", params).run(ctx, {"records": data})
    a = run(rows, "original")
    altered = copy.deepcopy(rows)
    for row in altered[12:]:
        row["y"] = 9999
    b = run(altered, "perturbed")
    assert a["fits"][0]["n_train"] == 12
    assert len(a["fits"]) == 1
    assert [r.get("p") for r in a["records"]] == [r.get("p") for r in b["records"]]
