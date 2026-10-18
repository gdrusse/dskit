"""Behavioral checks for the prediction-only experiment configurations."""
import copy
import json
import math
from pathlib import Path

import pytest

from dskit.pipeline.kinds_compute import ComputeFields
from dskit.pipeline.kinds_flow import Derive
from dskit.pipeline.kinds_run_write import RecordsWriteRun
from dskit.pipeline.node import NodeContext

CONFIGS = sorted((Path(__file__).parents[1] / "configs").glob("run-predictability15-*.json"))


def _document(path):
    return json.loads(path.read_text())["pipeline"]


def _row():
    return {
        "ticker": "test", "mid": 0.1, "fair_gru": 0.15, "label": 0,
        "lead_minutes": 5, "asset": "ETH", "day": "2026-02-20", "band": "test",
        "decision_ms": 1771545600000, "settlement_ms": 1771545900000,
        "log_moneyness": 0.0001, "rv_rms_60": 0.0001,
        "rv_ewma_30": 0.00012, "spread": 0.01,
        "candle_volume": 200.0, "candle_open_interest": 2000.0,
    }


@pytest.mark.parametrize("path", CONFIGS, ids=lambda p: p.stem)
def test_selected_features_are_label_independent_and_finite(path):
    nodes = _document(path)
    row = _row()
    changed = dict(row, label=1, settlement_ms=row["settlement_ms"] + 9999999)
    first = ComputeFields("features", nodes["features"]["params"]).run(
        None, {"records": [row, changed]})["records"]
    first = Derive("asset", nodes["asset"]["params"]).run(
        None, {"records": first})["records"]
    for feature in nodes["fit"]["params"]["features"]:
        name = feature["field"]
        assert math.isfinite(first[0][name])
        assert first[0][name] == first[1][name]
    assert first[0]["residual"] != first[1]["residual"]
    assert first[0]["known_ms"] == row["settlement_ms"] + 86400000


@pytest.mark.parametrize("path", CONFIGS, ids=lambda p: p.stem)
def test_scores_use_the_same_label_and_fixed_market_bin(path):
    nodes = _document(path)
    rows = [dict(_row(), prediction=0.2, cal_market=0.15)]
    for key in ("losses", "paired"):
        rows = ComputeFields(key, nodes[key]["params"]).run(None, {"records": rows})["records"]
    row = rows[0]
    assert row["delta_logloss"] == pytest.approx(-math.log(0.8) + math.log(0.9))
    assert row["delta_brier"] == pytest.approx(0.04 - 0.01)
    assert row["delta_cal_logloss"] == pytest.approx(-math.log(0.8) + math.log(0.85))
    assert row["market_calibration_gap"] == pytest.approx(-0.1)
    binner = Derive("probability_bin", nodes["probability_bin"]["params"])
    twins = [row, dict(row, prediction=0.99)]
    bins = binner.run(None, {"records": twins})["records"]
    assert bins[0]["probability_bin"] == bins[1]["probability_bin"] == "10-20"


@pytest.mark.parametrize("path", CONFIGS, ids=lambda p: p.stem)
def test_training_cut_and_writer_prevent_silent_empty_result(path, tmp_path):
    nodes = _document(path)
    assert nodes["data"]["params"]["read_bounds"]["day"]["end_before"] == "2026-08-01"
    fit = nodes["fit"]["params"]
    assert fit["known_field"] == "known_ms"
    assert fit["time_field"] == "decision_ms"
    assert fit["block_origin_ms"] + fit["min_train_blocks"] * fit["block_ms"] == 1771459200000
    assert "label" not in [x["field"] for x in fit["features"]]
    # Failed feature transforms must fail the experiment census, not silently report success.
    params = copy.deepcopy(nodes["write_predictions"]["params"])
    params["path"] = str(tmp_path / "scores-{run}.jsonl")
    node = RecordsWriteRun("write_predictions", params)
    ctx = NodeContext(name="test", asof="2026-10-17", run_dir=str(tmp_path / "test-run"))
    with pytest.raises(ValueError, match="expects"):
        node.run(ctx, {"records": []})
