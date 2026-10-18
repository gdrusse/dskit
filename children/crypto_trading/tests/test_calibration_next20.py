"""Check causal transforms, loss reconstruction and complete experiment reporting."""
import copy
import json
import math
from pathlib import Path

import pytest

from dskit.pipeline.kinds_compute import ComputeFields
from dskit.pipeline.kinds_flow import Derive
from dskit.pipeline.kinds_run_write import RecordsWriteRun
from dskit.pipeline.node import NodeContext

CONFIGS = sorted((Path(__file__).parents[1] / "configs").glob("run-calibration-next20-*.json"))


def test_twenty_two_complete_method_documents():
    assert len(CONFIGS) == 22


@pytest.mark.parametrize("path", CONFIGS, ids=lambda p: p.stem)
def test_features_are_causal_and_finite(path):
    nodes = json.loads(path.read_text())["pipeline"]
    row = dict(ticker="test", mid=.15, fair_gru=.2, label=0, lead_minutes=5,
               asset="ETH", day="2026-02-20", band="test", decision_ms=1771545600000,
               settlement_ms=1771545900000, log_moneyness=.0001, rv_rms_60=.0001,
               rv_ewma_30=.00012, spread=.01, candle_volume=200,
               candle_open_interest=2000)
    rows = [row, dict(row, label=1, settlement_ms=row["settlement_ms"] + 999999)]
    for key in ["features", "asset", "extended", "weighted", "group"]:
        if key not in nodes:
            continue
        cls = Derive if nodes[key]["uses"] == "derive" else ComputeFields
        rows = cls(key, nodes[key]["params"]).run(None, {"records": rows})["records"]
    for spec in nodes["fit"]["params"]["features"]:
        a, b = [r[spec["field"]] for r in rows]
        assert math.isfinite(a) and a == b
    fit = nodes["fit"]["params"]
    assert fit["known_field"] in {"known_ms", "known_week_ms"}
    assert rows[0]["known_ms"] == row["settlement_ms"] + 86400000
    assert rows[0]["known_week_ms"] == row["settlement_ms"] + 7 * 86400000
    assert nodes["data"]["params"]["read_bounds"]["day"]["end_before"] == "2026-08-01"


@pytest.mark.parametrize("path", CONFIGS, ids=lambda p: p.stem)
def test_probability_and_scores_are_finite_at_tails(path):
    nodes = json.loads(path.read_text())["pipeline"]
    for mid in [.001, .15, .5, .95, .999]:
        rows = [dict(ticker="test", decision_ms=0, settlement_ms=1, lead_minutes=5,
                     asset="ETH", day="2026-02-20", band="test",
                     mid=mid, label=y, raw_prediction=raw, ard_raw=.01, cal_market=mid)
                for y in [0, 1] for raw in [-100, 0, 100]]
        for key in ["probability", "losses", "paired"]:
            rows = ComputeFields(key, nodes[key]["params"]).run(None, {"records": rows})["records"]
        for r in rows:
            assert 1e-6 <= r["prediction"] <= 1 - 1e-6
            expected = -math.log(r["prediction"] if r["label"] else 1-r["prediction"])
            assert r["model_logloss"] == pytest.approx(expected)
            assert r["delta_ard_logloss"] == pytest.approx(r["model_logloss"]-r["ard_logloss"])
            assert r["delta_cal_brier"] == pytest.approx(r["model_brier"]-r["cal_brier"])


@pytest.mark.parametrize("path", CONFIGS, ids=lambda p: p.stem)
def test_all_report_writers_refuse_missing_results(path, tmp_path):
    nodes = json.loads(path.read_text())["pipeline"]
    expected = {"write_predictions": 137737, "write_markets": 28373,
                "write_cells": 100, "write_leads": 5,
                "write_asset_leads": 10, "write_calibration": 50}
    for key, count in expected.items():
        params = copy.deepcopy(nodes[key]["params"])
        assert params["expect"] == count
        params["path"] = str(tmp_path / (key + "-{run}.jsonl"))
        ctx = NodeContext(name="test", asof="2026-10-17", run_dir=str(tmp_path / "run"))
        with pytest.raises(ValueError, match="expects"):
            RecordsWriteRun(key, params).run(ctx, {"records": []})


def test_newton_probability_uses_logit_correction_instead_of_probability_shift():
    path = next(p for p in CONFIGS if "-23-" in p.name)
    nodes = json.loads(path.read_text())["pipeline"]
    mid = .15
    variance = mid * (1-mid)
    row = dict(ticker="test", decision_ms=0, settlement_ms=1, lead_minutes=5,
               asset="ETH", day="2026-02-20", band="test", label=0, mid=mid,
               raw_prediction=math.sqrt(variance)*math.log(2), ard_raw=0,
               cal_market=mid)
    result = ComputeFields("probability", nodes["probability"]["params"]).run(
        None, {"records": [row]})["records"][0]
    odds = mid/(1-mid) * math.sqrt(2)
    assert result["prediction"] == pytest.approx(odds/(1+odds))
    assert result["ard_market"] == pytest.approx(mid)
    row["raw_prediction"] = 0
    result = ComputeFields("probability", nodes["probability"]["params"]).run(
        None, {"records": [row]})["records"][0]
    assert result["prediction"] == pytest.approx(mid)


def test_grouping_uses_market_bins_and_never_the_label():
    path = next(p for p in CONFIGS if "-21-" in p.name)
    params = json.loads(path.read_text())["pipeline"]["group"]["params"]
    node = Derive("group", params)
    rows = [dict(asset=a, lead_minutes=lead, mid=(b+.5)/10, label=y)
            for a in ["BTC", "ETH"] for lead in [2, 5, 8, 11, 14]
            for b in range(10) for y in [0, 1]]
    result = node.run(None, {"records": rows})["records"]
    assert len({r["fit_group"] for r in result}) == 100
    for a, b in zip(result[::2], result[1::2]):
        assert a["fit_group"] == b["fit_group"] != "invalid"
