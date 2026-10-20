"""Pins for the ADR-0272 prediction-only documents: same evidence, same calendar, frozen tuning, no trading."""
import hashlib
import json
import os

import pytest

from dskit.pipeline.document import load_document
from dskit.pipeline.planner import plan

CHILD_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIGS = os.path.join(CHILD_ROOT, "configs")
DEVELOPMENT = "run-tech-prediction-development.json"
VALIDATION = "run-tech-prediction-validation.json"
OFFSET1 = "run-tech-prediction-validation-offset1.json"
SELECTION = "prediction-development-selection.json"
TARGETS = ("atm", "skew")
HORIZONS = (5, 20)
READS = {"surface": "surface_rows.jsonl", "precision_edges": "graph_edges.jsonl",
         "correlation_edges": "corr_graph_edges.jsonl"}


def _path(name):
    return os.path.join(CONFIGS, name)


def _raw(name):
    with open(_path(name), encoding="utf-8") as fh:
        return json.load(fh)


@pytest.fixture(params=[DEVELOPMENT, VALIDATION, OFFSET1], ids=["development", "validation", "offset1"])
def document(request):
    return request.param, load_document(_path(request.param)), _raw(request.param)


def _refits(raw):
    return {k: v for k, v in raw["pipeline"].items()
            if v["uses"] == "dskit.pipeline.libs.sklearn:SklearnExpandingRefit"}


def _skills(raw):
    return {k: v for k, v in raw["pipeline"].items()
            if v["uses"] == "dskit.pipeline.kinds_stats:ChronologicalForecastSkill"}


def test_both_documents_plan_and_read_exactly_the_replays_pinned_evidence(document):
    name, doc, raw = document
    assert doc.hash and plan(doc)
    backtest = _raw("run-tech-backtest.json")
    for key, artifact in READS.items():
        node = raw["pipeline"][key]
        assert node["uses"] == "dskit.pipeline.kinds_table:RecordsFile", (name, key)
        pinned = backtest["structural_rows"][artifact]
        assert node["params"]["path"] == pinned["path"], (name, key)
        assert node["params"]["sha256"] == pinned["sha256"], (name, key)
    calendar = raw["pipeline"]["calendar"]
    assert calendar["uses"] == "table-file"
    assert calendar["params"]["sha256"] == backtest["validation_calendar"]["sha256"]
    with open(os.path.join(CHILD_ROOT, "..", "..", calendar["params"]["path"]), "rb") as fh:
        assert hashlib.sha256(fh.read()).hexdigest() == calendar["params"]["sha256"]


def test_folds_copy_the_calendar_and_the_two_documents_never_score_the_same_year(document):
    name, _, raw = document
    calendar = _raw("validation-calendar.json")
    annual = {str(b["year"]): {"start": b["start"], "end_before": b["end_before"]}
              for b in calendar["annual_validation"]}
    selection = calendar["initial_development"]["selection"]
    development = {"2020-development": {"start": selection["score_start"],
                                        "end_before": selection["score_end_before"]}}
    for key, node in _skills(raw).items():
        folds = node["params"]["folds"]
        assert folds == (development if name == DEVELOPMENT else annual), (name, key)


def test_every_refit_purges_on_its_own_horizon_and_shares_one_clock(document):
    name, _, raw = document
    refits = _refits(raw)
    assert len(refits) == 4 * (5 if name == DEVELOPMENT else 4)
    clocks = set()
    for key, node in refits.items():
        p = node["params"]
        target, horizon, model = key.split("_", 2)
        assert target in TARGETS and horizon in {f"h{h}" for h in HORIZONS}, key
        assert p["label"] == f"y_{horizon[1:]}" and p["known_field"] == f"known_ms_{horizon[1:]}", key
        assert p["time_field"] == "decision_ms" and p["group_field"] == "pair", key
        assert p["predict_method"] == "predict" and p["output_field"] == f"f_{model}", key
        clocks.add((p["block_ms"], p["block_origin_ms"], p["min_train_blocks"]))
        if model == "mean":
            assert p["estimator"] == "sklearn.dummy.DummyRegressor"
            assert p["estimator_params"] == {"strategy": "mean"}
        else:
            assert p["estimator"] == "sklearn.linear_model.Ridge", key
    assert len(clocks) == 1
    block_ms, origin_ms, min_train = clocks.pop()
    assert block_ms == 7 * 86_400_000 and origin_ms == 1_546_214_400_000 and min_train == 52


def test_skill_nodes_score_every_model_of_their_cell_against_the_mean(document):
    name, _, raw = document
    refits = _refits(raw)
    for key, node in _skills(raw).items():
        p = node["params"]
        _, target, horizon = key.split("_")
        h = int(horizon[1:])
        assert p["h_steps"] == h and p["outcome_field"] == f"y_{h}" and p["benchmark"] == "mean"
        cell = {k.split("_", 2)[2]: v for k, v in refits.items() if k.startswith(f"{target}_{horizon}_")}
        assert p["forecasts"] == {model: f"f_{model}" for model in cell}
        assert p["bootstrap"] == {"block_days": 21, "replicates": 1000, "seed": 20261020, "level": 0.9}
        assert p["alpha"] == 0.05 and p["corrections"] == ["bh", "bonferroni"]


def test_development_tunes_only_the_lagged_alpha_grid():
    raw = _raw(DEVELOPMENT)
    for key, node in _refits(raw).items():
        model = key.split("_", 2)[2]
        if model == "mean":
            continue
        assert model.startswith("lagged_a"), key
        assert [f["field"] for f in node["params"]["features"]] == ["z", "spread_change_5"], key
    assert {n["params"]["estimator_params"]["alpha"] for k, n in _refits(raw).items() if "lagged" in k} == {0.1, 1.0, 10.0, 100.0}
    assert all(n["params"].get("comparisons", []) == [] for n in _skills(raw).values())


@pytest.mark.parametrize("name", [VALIDATION, OFFSET1])
def test_validation_freezes_the_recorded_development_selection_and_nests_the_features(name):
    raw = _raw(name)
    selection = _raw(SELECTION)
    with open(_path(SELECTION), "rb") as fh:
        digest = hashlib.sha256(fh.read()).hexdigest()
    pin = raw["pipeline"]["development_selection"]
    assert pin["uses"] == "table-file" and pin["params"]["sha256"] == digest
    assert pin["params"]["path"].endswith("configs/" + SELECTION)
    nested = {"lagged": ["z", "spread_change_5"],
              "correlation": ["z", "spread_change_5", "correlation_weight", "z_x_correlation_weight"],
              "precision": ["z", "spread_change_5", "precision_weight", "z_x_precision_weight"]}
    for key, node in _refits(raw).items():
        target, _, model = key.split("_", 2)
        if model == "mean":
            continue
        assert node["params"]["estimator_params"] == {"alpha": selection["alpha"][target]}, key
        assert [f["field"] for f in node["params"]["features"]] == nested[model], key
    for node in _skills(raw).values():
        assert node["params"]["comparisons"] == [["precision", "lagged"], ["correlation", "lagged"]]


def test_prediction_documents_never_reach_a_ledger_order_or_backtest(document):
    name, _, raw = document
    for key, node in raw["pipeline"].items():
        uses = node["uses"].lower()
        assert not any(word in uses for word in ("ledger", "order", "backtest", "replay", "capital")), (name, key)
    assert "end_before" in raw["pipeline"]["spread_atm"]["params"]
    assert raw["pipeline"]["spread_atm"]["params"]["end_before"] == "2025-12-16"


def test_the_offset_document_differs_from_validation_only_by_the_executable_clock_label():
    validation, offset = _raw(VALIDATION), _raw(OFFSET1)
    assert set(validation["pipeline"]) == set(offset["pipeline"])
    for key, node in validation["pipeline"].items():
        other = offset["pipeline"][key]
        assert node["uses"] == other["uses"] and node.get("inputs") == other.get("inputs"), key
        if key in ("rows_atm", "rows_skew"):
            assert "label_offset" not in node["params"] and other["params"]["label_offset"] == 1
            assert {k: v for k, v in other["params"].items() if k != "label_offset"} == node["params"]
        elif node["uses"] in ("records-write", "table-write"):
            assert other["params"]["path"] == node["params"]["path"].replace("/validation/", "/validation-offset1/")
        else:
            assert node["params"] == other["params"], key
