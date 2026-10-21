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


# -- ADR-0273 ten-method documents --------------------------------------------------------------

METHODS_DEVELOPMENT = "run-tech-prediction-methods-development.json"
METHODS_VALIDATION = "run-tech-prediction-methods-validation.json"
METHODS_SAMEDAY = "run-tech-prediction-methods-validation-sameday.json"
METHODS_SELECTION = "prediction-methods-selection.json"
GREEKS_PASS = "run-tech-structural-greeks.json"
METHOD_KEYS = ["m01_multiscale", "m02_legs", "m03_smile", "m04_changes", "m05_term", "m06_positioning",
               "m07_realized", "m08_pooled", "m09_lasso", "m10_boost"]
STANDARDIZED = "dskit.pipeline.libs.sklearn.StandardizedEstimator"


@pytest.fixture(params=[METHODS_DEVELOPMENT, METHODS_VALIDATION, METHODS_SAMEDAY],
                ids=["m-development", "m-validation", "m-sameday"])
def methods_document(request):
    return request.param, load_document(_path(request.param)), _raw(request.param)


def _methods_refits(raw):
    return {k: v for k, v in raw["pipeline"].items()
            if v["uses"] == "dskit.pipeline.libs.sklearn:SklearnExpandingRefit"}


def test_greeks_pass_reads_the_same_theta_members_and_only_sums_vendor_numbers():
    raw, base = _raw(GREEKS_PASS), _raw("run-tech-structural.json")
    assert raw["pipeline"]["chain"]["params"]["members"] == base["pipeline"]["chain"]["params"]["members"]
    assert raw["pipeline"]["chain"]["params"]["end_before"] == base["pipeline"]["chain"]["params"]["end_before"]
    assert set(raw["pipeline"]["chain"]["params"]["fields"]) >= {"implied_vol", "iv_error", "open_interest",
                                                                 "volume", "delta", "gamma", "vega", "vanna"}
    pos = raw["pipeline"]["positioning"]
    assert pos["uses"] == "dskit.pipeline.libs.chain_greeks:ChainDayPositioning"
    assert pos["params"]["spot_power"] == {"gamma": 2, "delta": 1, "vanna": 1, "charm": 1}
    assert pos["params"]["end_before"] == "2025-12-16" and pos["params"]["iv_error_max"] == 0.01
    term = raw["pipeline"]["surface_term"]["params"]
    assert term["term_pair"] == [30, 90] and [t["dte"] for t in term["targets"]] == [30, 90]
    assert load_document(_path(GREEKS_PASS)).hash


def test_methods_documents_plan_pin_every_input_and_keep_the_frozen_baselines(methods_document):
    name, doc, raw = methods_document
    assert doc.hash and plan(doc)
    backtest = _raw("run-tech-backtest.json")
    for key, artifact in READS.items():
        node = raw["pipeline"][key]
        assert node["params"]["sha256"] == backtest["structural_rows"][artifact]["sha256"], (name, key)
    for key in ("surface_term", "positioning"):
        node = raw["pipeline"][key]
        assert node["uses"] == "dskit.pipeline.kinds_table:RecordsFile"
        assert node["params"]["path"].startswith("/home/russell/data/statistical_arbitrage/structural-greeks-20261020/")
        with open(node["params"]["path"], "rb") as fh:
            assert hashlib.sha256(fh.read()).hexdigest() == node["params"]["sha256"], (name, key)
    base = _raw("run-tech-structural.json")["pipeline"]
    for key in ("prices_aapl", "prices_amzn", "prices_googl", "prices_qqq", "returns"):
        assert raw["pipeline"][key] == base[key], (name, key)
    refits = _methods_refits(raw)
    for target in TARGETS:
        for h in HORIZONS:
            lagged = refits[f"{target}_h{h}_lagged"]["params"]
            assert lagged["estimator"] == "sklearn.linear_model.Ridge"
            assert lagged["estimator_params"] == {"alpha": {"atm": 100.0, "skew": 0.1}[target]}
            assert [f["field"] for f in lagged["features"]] == ["z", "spread_change_5"]
            mean = refits[f"{target}_h{h}_mean"]["params"]
            assert mean["estimator"] == "sklearn.dummy.DummyRegressor"


def test_methods_refits_share_the_clock_nest_the_lagged_features_and_standardize(methods_document):
    name, _, raw = methods_document
    clocks = set()
    for key, node in _methods_refits(raw).items():
        p = node["params"]
        target, horizon, model = key.split("_", 2)
        h = int(horizon[1:])
        assert p["label"] == f"y_{h}" and p["known_field"] == f"known_ms_{h}" and p["time_field"] == "decision_ms"
        clocks.add((p["block_ms"], p["block_origin_ms"], p["min_train_blocks"]))
        if model in ("mean", "lagged"):
            continue
        fields = [f["field"] for f in p["features"]]
        assert fields[:2] == ["z", "spread_change_5"], key
        if model.startswith("m10_boost"):
            assert p["estimator"] == "sklearn.ensemble.HistGradientBoostingRegressor"
        else:
            assert p["estimator"] == STANDARDIZED, key
            assert p["estimator_params"]["estimator"] in ("sklearn.linear_model.Ridge", "sklearn.linear_model.Lasso")
        assert ("group_field" not in p) == model.startswith("m08_pooled"), key
    assert clocks == {(7 * 86_400_000, 1_546_214_400_000, 52)}


def test_methods_folds_and_label_clock_follow_the_documents_role(methods_document):
    name, _, raw = methods_document
    calendar = _raw("validation-calendar.json")
    annual = {str(b["year"]): {"start": b["start"], "end_before": b["end_before"]} for b in calendar["annual_validation"]}
    dev = calendar["initial_development"]["selection"]
    expected = ({"2020-development": {"start": dev["score_start"], "end_before": dev["score_end_before"]}}
                if name == METHODS_DEVELOPMENT else annual)
    for key, node in raw["pipeline"].items():
        if node["uses"] == "dskit.pipeline.kinds_stats:ChronologicalForecastSkill":
            assert node["params"]["folds"] == expected, (name, key)
            assert node["params"]["benchmark"] == "mean"
            if name != METHODS_DEVELOPMENT:
                assert node["params"]["comparisons"] == [[m, "lagged"] for m in METHOD_KEYS]
    for target in TARGETS:
        rows = raw["pipeline"][f"rows_{target}"]["params"]
        assert rows.get("label_offset", 0) == (0 if name == METHODS_SAMEDAY else 1), name


def test_methods_validation_freezes_the_recorded_selection():
    selection = _raw(METHODS_SELECTION)["selected"]
    with open(_path(METHODS_SELECTION), "rb") as fh:
        digest = hashlib.sha256(fh.read()).hexdigest()
    for name in (METHODS_VALIDATION, METHODS_SAMEDAY):
        raw = _raw(name)
        pin = raw["pipeline"]["methods_selection"]
        assert pin["uses"] == "table-file" and pin["params"]["sha256"] == digest
        for key, node in _methods_refits(raw).items():
            target, _, model = key.split("_", 2)
            if model in ("mean", "lagged"):
                continue
            knob = {"m09_lasso": "alpha", "m10_boost": "max_iter"}.get(model, "alpha")
            assert node["params"]["estimator_params"][knob] == selection[target][model], (name, key)
    dev = _raw(METHODS_DEVELOPMENT)
    grid_models = {k.split("_", 2)[2] for k in _methods_refits(dev)}
    for method in METHOD_KEYS:
        assert sum(1 for m in grid_models if m.startswith(method + "_")) == (2 if method == "m10_boost" else 4), method


def test_methods_documents_never_reach_a_ledger_order_or_backtest(methods_document):
    name, _, raw = methods_document
    for key, node in raw["pipeline"].items():
        uses = node["uses"].lower()
        assert not any(word in uses for word in ("ledger", "order", "backtest", "replay", "capital")), (name, key)


# -- ADR-0274 documents: scaled label, Kalman-OU state, cohort pooling -------------------------------

ADR74_DEVELOPMENT = "run-tech-prediction-adr0274-development.json"
ADR74_VALIDATION = "run-tech-prediction-adr0274-validation.json"
ADR74_SELECTION = "prediction-adr0274-selection.json"
UNIVERSE_PASS = "run-tech-structural-universe.json"
COHORT = ["ISRG", "CMG", "BA", "GS", "CHTR", "CAT", "AVGO", "BIDU", "ABBV", "GILD", "CRM", "HUM"]
THREE = ["AAPL|AMZN", "AAPL|GOOGL", "AMZN|GOOGL"]


@pytest.fixture(params=[ADR74_DEVELOPMENT, ADR74_VALIDATION], ids=["adr74-dev", "adr74-val"])
def adr74_document(request):
    return request.param, load_document(_path(request.param)), _raw(request.param)


def test_cohort_rule_reproduces_from_the_census_and_the_universe_pass_reads_it():
    census = "/home/russell/data/index_options/theta-strike-screen-20261018/features/census/census-roots.jsonl"
    if not os.path.exists(census):
        pytest.skip("census artifact not on this machine")
    with open(census, encoding="utf-8") as fh:
        rows = [json.loads(line) for line in fh]
    ok = [r for r in rows if r["first_quoted_date"] <= "2018-01-02" and r["last_quoted_date"] >= "2025-12-15"
          and r["symbol"] not in ("AAPL", "AMZN", "GOOGL", "GOOG") and not r["never_quoted"]]
    ok.sort(key=lambda r: -r["median_quoted_development"])
    assert [r["symbol"] for r in ok[:12]] == COHORT
    raw = _raw(UNIVERSE_PASS)
    assert raw["pipeline"]["chain"]["params"]["symbols"] == ["AAPL", "AMZN", "GOOGL"] + COHORT
    assert "members" not in raw["pipeline"]["chain"]["params"]
    assert raw["pipeline"]["chain"]["params"]["sealed_from"] == "2026-01-01"
    base = _raw("run-tech-structural.json")["pipeline"]["surface"]["params"]
    assert raw["pipeline"]["surface"]["params"] == base
    assert load_document(_path(UNIVERSE_PASS)).hash


def test_adr74_documents_plan_pin_inputs_and_score_only_the_three_pairs(adr74_document):
    name, doc, raw = adr74_document
    assert doc.hash and plan(doc)
    backtest = _raw("run-tech-backtest.json")
    assert raw["pipeline"]["surface"]["params"]["sha256"] == backtest["structural_rows"]["surface_rows.jsonl"]["sha256"]
    uni = raw["pipeline"]["surface_universe"]
    assert uni["uses"] == "dskit.pipeline.kinds_table:RecordsFile"
    with open(uni["params"]["path"], "rb") as fh:
        assert hashlib.sha256(fh.read()).hexdigest() == uni["params"]["sha256"]
    for key, node in raw["pipeline"].items():
        if node["uses"] == "filter" and key.startswith("three_"):
            assert node["params"]["where"] == [{"field": "pair", "op": "in", "value": THREE}]
        if node["uses"] == "dskit.pipeline.libs.pair_spread:RollingPairSpread":
            assert node["params"]["symbols"] in (["AAPL", "AMZN", "GOOGL"], ["AAPL", "AMZN", "GOOGL"] + COHORT)
        if node["uses"] == "dskit.pipeline.libs.pair_spread:PairSpreadHorizonRows":
            assert node["params"]["label_offset"] == 1 and "edges" not in node["params"]
        if node["uses"] == "dskit.pipeline.libs.kalman_spread:KalmanSpreadState":
            assert node["params"]["window"] == 252 and node["params"]["label_offset"] == 1
            assert node["params"]["fit_dates"] == _raw("validation-calendar.json")["retraining"]["fit_dates"]
        if node["uses"] == "dskit.pipeline.kinds_stats:ChronologicalForecastSkill":
            assert node["params"]["benchmark"] == "mean"
            calendar = _raw("validation-calendar.json")
            annual = {str(b["year"]): {"start": b["start"], "end_before": b["end_before"]} for b in calendar["annual_validation"]}
            dev = calendar["initial_development"]["selection"]
            assert node["params"]["folds"] == ({"2020-development": {"start": dev["score_start"], "end_before": dev["score_end_before"]}}
                                               if name == ADR74_DEVELOPMENT else annual)
        uses = node["uses"].lower()
        assert not any(word in uses for word in ("ledger", "order", "backtest", "replay", "capital")), (name, key)


def test_adr74_refits_keep_the_frozen_baselines_and_the_declared_cadences(adr74_document):
    name, _, raw = adr74_document
    refits = {k: v for k, v in raw["pipeline"].items() if v["uses"] == "dskit.pipeline.libs.sklearn:SklearnExpandingRefit"}
    week, year = 7 * 86_400_000, 52 * 7 * 86_400_000
    for key, node in refits.items():
        p = node["params"]
        model = key.split("_", 2)[2]
        if model.endswith("lagged"):
            assert p["estimator"] == "sklearn.linear_model.Ridge" and p["block_ms"] == week
            assert p["estimator_params"] == {"alpha": {"atm": 100.0, "skew": 0.1}[key.split("_")[0]]}
        elif model.endswith("mean"):
            assert p["estimator"] == "sklearn.dummy.DummyRegressor" and p["block_ms"] == week
        elif "pooled" in model:
            assert "group_field" not in p and p["block_ms"] == year and p["min_train_blocks"] == 1
        elif "pair_yearly" in model:
            assert p["group_field"] == "pair" and p["block_ms"] == year and p["min_train_blocks"] == 1
        elif "scaledraw" in model:
            assert p["label"].startswith("ys_") and p["known_field"] == "known_ms_" + p["label"][3:]
        else:
            assert p["block_ms"] == week and p["group_field"] == "pair", key
        assert p["block_origin_ms"] == 1_546_214_400_000 and p["time_field"] == "decision_ms"


def test_adr74_validation_freezes_the_recorded_selection():
    raw, selection = _raw(ADR74_VALIDATION), _raw(ADR74_SELECTION)["selected"]
    with open(_path(ADR74_SELECTION), "rb") as fh:
        digest = hashlib.sha256(fh.read()).hexdigest()
    assert raw["pipeline"]["adr0274_selection"]["params"]["sha256"] == digest
    for key, node in raw["pipeline"].items():
        if node["uses"] != "dskit.pipeline.libs.sklearn:SklearnExpandingRefit":
            continue
        target, model = key.split("_")[0], key.split("_", 2)[2]
        want = {"scaledraw": "scaled", "kalman_ridge": "kalman_ridge", "u_pooled": "pooled", "u_pair_yearly": "pair_yearly"}.get(model)
        if want:
            assert node["params"]["estimator_params"]["alpha"] == selection[target][want], key
    for target in ("atm", "skew"):
        for h in (5, 20):
            blend = raw["pipeline"][f"blend_{target}_h{h}"]["params"]["fields"]["f_blend"]
            assert blend["args"][0]["args"][0]["const"] == selection[target]["blend_weight"]


def test_adr74_scaled_label_and_direct_forecast_wiring_is_exact(adr74_document):
    """The headline result's arithmetic: ys_h = y_h / sd60(own), f_scaled = f_scaledraw * sd60(own), f_kalman_direct = kf_fc_h."""
    name, _, raw = adr74_document
    own = {"atm": "sd60_atm_c", "skew": "sd60_rr_c"}
    for target in TARGETS:
        scale = raw["pipeline"][f"scale_{target}"]["params"]["fields"]
        for h in HORIZONS:
            assert scale[f"ys_{h}"] == {"op": "div", "args": [{"field": f"y_{h}"}, {"field": own[target]}]}, (name, target, h)
            fields = raw["pipeline"][f"compute_{target}_h{h}"]["params"]["fields"]
            assert fields["f_kalman_direct"] == {"field": f"kf_fc_{h}"}
            for out, expr in fields.items():
                if out.startswith("f_scaled"):
                    assert expr == {"op": "mul", "args": [{"field": out.replace("f_scaled", "f_scaledraw")}, {"field": own[target]}]}, (name, out)
            if name == ADR74_VALIDATION:
                blend = raw["pipeline"][f"blend_{target}_h{h}"]["params"]["fields"]["f_blend"]
                w = blend["args"][0]["args"][0]["const"]
                assert blend == {"op": "add", "args": [{"op": "mul", "args": [{"const": w}, {"field": "f_pooled"}]},
                                                        {"op": "mul", "args": [{"const": 1.0 - w}, {"field": "f_pair_yearly"}]}]}
    for key, node in raw["pipeline"].items():
        if node["uses"] in ("records-write", "table-write"):
            assert node["params"]["overwrite"] is False, (name, key)


# -- ADR-0275 documents: fifteen methods on the vol-scaled label ---------------------------------------

FIFTEEN_DEVELOPMENT = "run-tech-prediction-fifteen-development.json"
FIFTEEN_VALIDATION = "run-tech-prediction-fifteen-validation.json"
FIFTEEN_SELECTION = "prediction-fifteen-selection.json"
VENDOR_PASS = "run-tech-structural-vendor.json"
VENDOR_DIR = "/home/russell/data/statistical_arbitrage/structural-vendor-20261021/"
FIFTEEN_RIDGE = ["m01_term", "m02_legs", "m03_term_legs", "m04_curve", "m05_vskew", "m06_basis", "m07_convexity",
                 "m08_profile", "m09_flow", "m10_carry", "m11_vxindex", "m12_market"]
FIFTEEN = FIFTEEN_RIDGE + ["m13_pooled_scaled", "m14_blend", "m15_residual"]
TWO_STAGE = "dskit.pipeline.libs.sklearn.TwoStageResidualEstimator"
REFIT = "dskit.pipeline.libs.sklearn:SklearnExpandingRefit"


@pytest.fixture(params=[FIFTEEN_DEVELOPMENT, FIFTEEN_VALIDATION], ids=["fifteen-dev", "fifteen-val"])
def fifteen_document(request):
    return request.param, load_document(_path(request.param)), _raw(request.param)


def test_vendor_pass_reads_the_same_theta_members_and_only_aggregates_vendor_numbers():
    raw, base = _raw(VENDOR_PASS), _raw("run-tech-structural.json")
    chain = raw["pipeline"]["chain"]["params"]
    assert chain["members"] == base["pipeline"]["chain"]["params"]["members"]
    assert chain["end_before"] == base["pipeline"]["chain"]["params"]["end_before"] == "2025-12-16"
    assert set(chain["fields"]) >= {"implied_vol", "iv_error", "open_interest", "volume", "count", "bid_size", "ask_size",
                                    "delta", "gamma", "vega", "theta", "vanna", "vomma"}
    curve = raw["pipeline"]["surface_curve"]["params"]
    assert [t["dte"] for t in curve["targets"]] == [14, 30, 60, 90] and curve["term_pair"] == [30, 90]
    base_surface = base["pipeline"]["surface"]["params"]
    assert {k: v for k, v in curve.items() if k not in ("targets", "term_pair", "fields")} == \
        {k: v for k, v in base_surface.items() if k not in ("targets", "fields")}
    profile = raw["pipeline"]["positioning_profile"]["params"]
    assert profile["profiles"] == {"moneyness_edges": [-0.05, -0.025, 0.0, 0.025, 0.05], "dte_edges": [1, 31, 61], "greeks": ["gamma", "vanna"]}
    assert profile["spot_power"] == {"gamma": 2, "vanna": 1} and profile["moneyness_band"] == 0.15 and profile["iv_error_max"] == 0.01
    near = raw["pipeline"]["positioning_near"]["params"]
    assert near["moneyness_band"] == 0.05 and near["greek_fields"] == ["vega", "vanna", "vomma", "gamma"] and "profiles" not in near
    delta = raw["pipeline"]["delta_surface"]
    assert delta["uses"] == "dskit.pipeline.libs.chain_greeks:ChainDayDeltaSurface"
    assert delta["params"]["deltas"] == [0.1, 0.25, 0.4, 0.5] and delta["params"]["target_dte"] == 30 and delta["params"]["tolerance"] == 7
    for key, node in raw["pipeline"].items():
        assert node["params"].get("end_before", "2025-12-16") == "2025-12-16", key
        if node["uses"] == "records-write":
            assert node["params"]["overwrite"] is False and node["params"]["path"].startswith(VENDOR_DIR), key
    assert load_document(_path(VENDOR_PASS)).hash and plan(load_document(_path(VENDOR_PASS)))


def test_fifteen_documents_plan_pin_every_input_and_keep_the_three_baselines(fifteen_document):
    name, doc, raw = fifteen_document
    assert doc.hash and plan(doc)
    backtest = _raw("run-tech-backtest.json")
    assert raw["pipeline"]["surface"]["params"]["sha256"] == backtest["structural_rows"]["surface_rows.jsonl"]["sha256"]
    adr74 = _raw(ADR74_VALIDATION)["pipeline"]["surface_universe"]["params"]
    assert raw["pipeline"]["surface_universe"]["params"]["sha256"] == adr74["sha256"]
    for key in ("surface_curve", "positioning_profile", "positioning_near", "delta_surface"):
        node = raw["pipeline"][key]
        assert node["uses"] == "dskit.pipeline.kinds_table:RecordsFile" and node["params"]["path"].startswith(VENDOR_DIR), key
        if os.path.exists(node["params"]["path"]):
            with open(node["params"]["path"], "rb") as fh:
                assert hashlib.sha256(fh.read()).hexdigest() == node["params"]["sha256"], (name, key)
    cboe = raw["pipeline"]["cboe"]["params"]
    assert cboe["source"] == "cboe-index-wide" and cboe["stream"] == "index_daily" and cboe["key_fields"] == ["symbol", "date"]
    assert isinstance(cboe["as_of_acquisition_ms"], int)
    refits = {k: v for k, v in raw["pipeline"].items() if v["uses"] == REFIT}
    for target in TARGETS:
        for h in HORIZONS:
            lagged = refits[f"{target}_h{h}_lagged"]["params"]
            assert lagged["estimator"] == "sklearn.linear_model.Ridge" and lagged["label"] == f"y_{h}"
            assert lagged["estimator_params"] == {"alpha": {"atm": 100.0, "skew": 0.1}[target]}
            assert [f["field"] for f in lagged["features"]] == ["z", "spread_change_5"]
            assert refits[f"{target}_h{h}_mean"]["params"]["estimator"] == "sklearn.dummy.DummyRegressor"
            scaled = refits[f"{target}_h{h}_scaled"]["params"]
            assert scaled["estimator"] == STANDARDIZED and scaled["label"] == f"ys_{h}" and scaled["output_field"] == "fr_scaled"
            assert scaled["estimator_params"] == {"estimator": "sklearn.linear_model.Ridge", "alpha": 100.0}
            assert [f["field"] for f in scaled["features"]] == ["z", "spread_change_5"]


def test_fifteen_refits_share_the_clock_nest_the_frozen_features_and_scale_every_method(fifteen_document):
    name, _, raw = fifteen_document
    own = {"atm": "sd60_atm", "skew": "sd60_rr"}
    week, year = 7 * 86_400_000, 52 * 7 * 86_400_000
    for key, node in raw["pipeline"].items():
        if node["uses"] != REFIT:
            continue
        p = node["params"]
        cohort = key.startswith("u_")
        target = key.split("_")[1 if cohort else 0]
        h = int(p["label"].split("_")[1])
        assert p["known_field"] == f"known_ms_{h}" and p["time_field"] == "decision_ms" and p["block_origin_ms"] == 1_546_214_400_000
        model = key.split("_", 3 if cohort else 2)[-1]
        if cohort:
            assert "group_field" not in p and p["block_ms"] == year and p["min_train_blocks"] == 1 and p["label"] == f"ys_{h}"
            assert model.startswith("m13_pooled_scaled") and p["estimator"] == STANDARDIZED
            continue
        assert p["group_field"] == "pair" and p["block_ms"] == week and p["min_train_blocks"] == 52
        if model in ("mean", "lagged"):
            assert p["label"] == f"y_{h}"
            continue
        fields = [f["field"] for f in p["features"]]
        assert fields[:2] == ["z", "spread_change_5"] and p["label"] == f"ys_{h}" and p["output_field"] == f"fr_{model}", key
        assert p["estimator"] == STANDARDIZED, key
        if model.startswith("m15_residual"):
            inner = p["estimator_params"]
            assert inner["estimator"] == TWO_STAGE and inner["first_columns"] == 2
            assert inner["first_params"] == {"alpha": 100.0} and inner["first_estimator"] == inner["second_estimator"] == "sklearn.linear_model.Ridge"
        else:
            assert p["estimator_params"]["estimator"] == "sklearn.linear_model.Ridge"
        back = raw["pipeline"][f"back_{target}_h{h}"]["params"]["fields"]
        assert back[f"f_{model}"] == {"op": "mul", "args": [{"field": f"fr_{model}"}, {"field": own[target]}]}, key
    for target in TARGETS:
        scale = raw["pipeline"][f"compute_{target}"]["params"]["fields"]
        for h in HORIZONS:
            assert scale[f"ys_{h}"] == {"op": "div", "args": [{"field": f"y_{h}"}, {"field": own[target]}]}
        assert raw["pipeline"][f"rows_{target}"]["params"]["label_offset"] == 1
        assert raw["pipeline"][f"rows_u_{target}"]["params"]["label_offset"] == 1
        assert raw["pipeline"][f"spread_u_{target}"]["params"]["symbols"] == ["AAPL", "AMZN", "GOOGL"] + COHORT
        for h in HORIZONS:
            assert raw["pipeline"][f"three_u_{target}_h{h}"]["params"]["where"] == [{"field": "pair", "op": "in", "value": THREE}]


def test_fifteen_folds_families_and_writes_follow_the_documents_role(fifteen_document):
    name, _, raw = fifteen_document
    calendar = _raw("validation-calendar.json")
    annual = {str(b["year"]): {"start": b["start"], "end_before": b["end_before"]} for b in calendar["annual_validation"]}
    dev = calendar["initial_development"]["selection"]
    expected = ({"2020-development": {"start": dev["score_start"], "end_before": dev["score_end_before"]}}
                if name == FIFTEEN_DEVELOPMENT else annual)
    skills = {k: v for k, v in raw["pipeline"].items() if v["uses"] == "dskit.pipeline.kinds_stats:ChronologicalForecastSkill"}
    assert len(skills) == 4
    for key, node in skills.items():
        p = node["params"]
        assert p["folds"] == expected and p["benchmark"] == "mean" and p["alpha"] == 0.05
        assert set(p["forecasts"]) >= {"mean", "lagged", "scaled"}
        if name == FIFTEEN_VALIDATION:
            assert p["comparisons"] == [[m, "scaled"] for m in FIFTEEN]
            assert set(p["forecasts"]) == {"mean", "lagged", "scaled", *FIFTEEN}
        else:
            assert p["comparisons"] == []
    for key, node in raw["pipeline"].items():
        uses = node["uses"].lower()
        assert not any(word in uses for word in ("ledger", "order", "backtest", "replay", "capital")), (name, key)
        if node["uses"] in ("records-write", "table-write"):
            assert node["params"]["overwrite"] is False, key
            assert node["params"]["path"].startswith("/home/russell/data/statistical_arbitrage/prediction-fifteen-20261021/"
                                                     + ("development" if name == FIFTEEN_DEVELOPMENT else "validation") + "-r2/")


def test_fifteen_validation_freezes_the_recorded_selection_and_development_covers_the_grids():
    raw, selection = _raw(FIFTEEN_VALIDATION), _raw(FIFTEEN_SELECTION)["selected"]
    with open(_path(FIFTEEN_SELECTION), "rb") as fh:
        digest = hashlib.sha256(fh.read()).hexdigest()
    assert raw["pipeline"]["fifteen_selection"]["params"]["sha256"] == digest
    for key, node in raw["pipeline"].items():
        if node["uses"] != REFIT:
            continue
        cohort = key.startswith("u_")
        target = key.split("_")[1 if cohort else 0]
        model = key.split("_", 3 if cohort else 2)[-1]
        if model in ("mean", "lagged", "scaled"):
            continue
        knob = node["params"]["estimator_params"]["second_params"]["alpha"] if model == "m15_residual" else node["params"]["estimator_params"]["alpha"]
        assert knob == selection[target][model], key
    for target in TARGETS:
        for h in HORIZONS:
            blend = raw["pipeline"][f"blend_{target}_h{h}"]["params"]["fields"]["f_m14_blend"]
            w = selection[target]["m14_blend"]
            assert blend == {"op": "add", "args": [{"op": "mul", "args": [{"const": w}, {"field": "f_m13_pooled_scaled"}]},
                                                    {"op": "mul", "args": [{"const": 1.0 - w}, {"field": "f_scaled"}]}]}
    dev = _raw(FIFTEEN_DEVELOPMENT)
    tags = {}
    for key, node in dev["pipeline"].items():
        if node["uses"] == REFIT:
            model = key.split("_", 3 if key.startswith("u_") else 2)[-1]
            for m in FIFTEEN_RIDGE + ["m13_pooled_scaled", "m15_residual"]:
                if model.startswith(m + "_"):
                    tags.setdefault(m, set()).add(model[len(m) + 1:])
    assert all(tags[m] == {"a10", "a100", "a1000"} for m in FIFTEEN_RIDGE + ["m13_pooled_scaled", "m15_residual"]), tags
    blends = dev["pipeline"]["blend_atm_h20"]["params"]["fields"]
    assert len(blends) == 9 and all(k.startswith("f_m14_blend_") for k in blends)
