"""Every shipped config validates, and the action documents stay twins."""

import copy
import json
import os

from dskit.assets import load_model
from dskit.onboarding import check_config, load_suite
from dskit.pipeline.document import load_document

from intraday_equities.connectors import AlpacaBars, SchwabBars
from intraday_equities.nodes import _emit_feature_names, session_feature_names

CHILD_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIGS = os.path.join(CHILD_ROOT, "configs")

ACTION_DOCS = (
    "run-action-01m.json",
    "run-action-05m.json",
    "run-action-15m.json",
    "run-action-30m.json",
    "run-action-60m.json",
)
HORIZONS = {
    "run-action-01m.json": (60_000, 1),
    "run-action-05m.json": (300_000, 5),
    "run-action-15m.json": (900_000, 15),
    "run-action-30m.json": (1_800_000, 30),
    "run-action-60m.json": (3_600_000, 60),
}
ACTION_NODES = {
    "universe", "alpaca", "session", "window", "grid", "tradable",
    "train_rows", "val_rows", "qhat", "select",
}


def _path(name):
    return os.path.join(CONFIGS, name)


def _raw(name):
    with open(_path(name), encoding="utf-8") as fh:
        return json.load(fh)


UNIVERSE = _raw("universe.json")


def test_every_run_document_loads():
    for name in sorted(os.listdir(CONFIGS)):
        if name.startswith("run-") and name.endswith(".json"):
            document = load_document(_path(name))
            assert document.hash, name


def test_action_documents_differ_only_in_cadence():
    canonical = None
    for name in ACTION_DOCS:
        raw = _raw(name)
        assert set(raw["pipeline"]) == ACTION_NODES, name
        period, lead = HORIZONS[name]
        assert raw["pipeline"]["grid"]["params"]["period_ms"] == period
        assert raw["pipeline"]["window"]["params"]["label_lead"] == lead
        clone = copy.deepcopy(raw)
        clone["name"] = "pinned"
        clone["notes"] = "pinned"
        clone["pipeline"]["window"]["params"]["label_lead"] = 0
        clone["pipeline"]["grid"]["params"]["period_ms"] = 0
        clone["pipeline"]["window"]["notes"] = "pinned"
        clone["pipeline"]["grid"]["notes"] = "pinned"
        if canonical is None:
            canonical = clone
        else:
            assert clone == canonical, name


def test_action_documents_share_cuts_and_ridge():
    first = _raw(ACTION_DOCS[0])
    for name in ACTION_DOCS[1:]:
        other = _raw(name)
        assert other["splits"] == first["splits"]
        assert other["pipeline"]["qhat"]["params"] == first["pipeline"]["qhat"]["params"]
        assert other["pipeline"]["select"]["params"] == first["pipeline"]["select"]["params"]
        assert other["pipeline"]["select"]["inputs"]["tradable"] == "$universe.tradable"


def test_horizon_scan_never_reads_the_lockbox():
    scan = _raw("run-horizon-scan.json")["pipeline"]["scan"]["params"]
    assert set(scan) == {"split", "train_end_ms", "val_start_ms", "val_end_ms"}
    assert scan["split"] == "val"
    assert "test_end_ms" not in json.dumps(scan)


def test_hl_scan_stops_before_august():
    raw = _raw("run-hl-scan.json")
    assert raw["splits"]["test_end_ms"] == UNIVERSE["holdouts"]["test_a_end_ms"]
    assert raw["splits"]["test_end_ms"] < UNIVERSE["holdouts"]["test_b_start_ms"]
    lscan = raw["pipeline"]["lscan"]["params"]
    assert lscan["lead"] == "$scan.metrics.farthest_confident_lead"
    assert lscan["split"] == "val"
    assert "test_end_ms" not in json.dumps(lscan)
    assert raw["pipeline"]["features"]["params"]["lookback"] == (
        "$universe.spec.scan.lookback_stop"
    )
    assert raw["pipeline"]["features"]["params"]["layout"] == "columns"
    assert raw["pipeline"]["scan"]["inputs"]["bars"] == "$features.tape"
    assert raw["pipeline"]["lscan"]["inputs"]["bars"] == "$features.tape"


def test_hstar_cv_clock_mean_doc_is_superseded():
    raw = _raw("run-hstar-cv.json")
    assert raw["name"] == "intraday-equities-hstar-cv"
    assert "SUPERSEDED" in raw["notes"]
    assert raw["walkforward"]["objective"] == "$scan.metrics.go"


def test_hstar_cv_series_walkforward_pins():
    raw = _raw("run-hstar-cv-series.json")
    wf = raw["walkforward"]
    assert wf["first"] == "2019-01-07"
    assert wf["step_days"] == 63
    assert wf["count"] == 40
    assert wf["val_days"] == 63
    assert wf["embargo_days"] == 5
    assert wf["train_days"] == 730
    assert wf["objective"] == "$scan.metrics.go_frac"
    assert wf["select"] == "max"
    trees = raw["pipeline"]["scan"]["params"]["estimator_params"]
    assert trees["max_depth"] == 4
    assert trees["num_leaves"] == 15
    assert trees["min_child_samples"] == 400
    assert trees["reg_lambda"] == 5.0
    scan_params = raw["pipeline"]["scan"]["params"]
    assert scan_params["hpo_trials"] == 8
    assert scan_params["hpo_val_days"] == 63
    assert set(scan_params["hpo_space"]) == {
        "num_leaves", "max_depth", "min_child_samples",
        "learning_rate", "reg_lambda",
    }
    feat = raw["pipeline"]["features"]["params"]
    assert feat["lookback"] == 0
    extra = feat["momentum_horizons"]
    assert [row["tag"] for row in extra] == ["3m", "2h", "3h", "2s", "1w"]
    industry = tuple(sorted(set((UNIVERSE.get("industry") or {}).values())))
    base = session_feature_names(
        0, UNIVERSE["scales"], UNIVERSE["reference"], industry,
    )
    assert len(base) == 46
    assert all(not name.startswith("ret_lag_") for name in base)
    names = _emit_feature_names(
        0, UNIVERSE["scales"], UNIVERSE["reference"], industry, extra,
    )
    assert len(names) == 66
    assert raw["pipeline"]["scan"]["uses"] == (
        "intraday_equities-no-information-scan"
    )
    assert "test_end_ms" not in json.dumps(raw["pipeline"]["scan"])
    document = load_document(_path("run-hstar-cv-series.json"))
    assert document.walkforward.fold_cutoffs()[-1] == "2025-09-29"
    assert document.walkforward.fold_cutoffs()[0] == "2019-01-07"
    assert len(document.walkforward.fold_cutoffs()) == 40
    assert document.name == "intraday-equities-hstar-cv-series"


#: 1165 RTH minutes is the scan's farthest confident lead.
_HORIZON_LEAD = 1165


def test_horizon_models_labels_stop_at_the_cuts():
    raw = _raw("run-horizon-models.json")
    assert raw["pipeline"]["label_train"]["params"]["lead"] == _HORIZON_LEAD
    assert raw["pipeline"]["label_val"]["params"]["lead"] == _HORIZON_LEAD
    for key in ("label_train", "label_val"):
        params = raw["pipeline"][key]["params"]
        assert params["train_end_ms"] == "$splits.train_end_ms"
        assert params["val_end_ms"] == "$splits.val_end_ms"
        assert "test_end_ms" not in params
    session_cols = list(session_feature_names(
        UNIVERSE["lookback"], UNIVERSE["scales"], UNIVERSE["reference"],
        tuple(sorted(set((UNIVERSE.get("industry") or {}).values()))),
    ))
    for key in ("ridge", "tree"):
        assert raw["pipeline"][key]["params"]["features"] == session_cols
        assert raw["pipeline"][key]["params"]["label"] == "y_next"
    tags = [scale["tag"] for scale in UNIVERSE["scales"]]
    har = []
    for prefix in ("ret", "rv", "range", "vol"):
        har.extend(f"{prefix}_{tag}" for tag in tags)
    har.extend(["overnight_gap"] * len(tags))
    har.extend(["residual_SPY"] * len(tags))
    expect = len(tags) * 6
    for key in ("dlinear", "mlp", "patchtst", "transformer"):
        feats = raw["pipeline"][key]["params"]["features"]
        assert feats == har
        assert len(feats) == expect
        assert raw["pipeline"][key]["params"]["seq_len"] == len(tags)
        assert raw["pipeline"][key]["params"]["channels"] == 6
    assert raw["pipeline"]["dlinear"]["params"]["loss"] == (
        "torch.nn.functional:smooth_l1_loss"
    )
    assert raw["pipeline"]["mlp"]["params"]["loss"] == (
        "torch.nn.functional:smooth_l1_loss"
    )
    assert raw["pipeline"]["patchtst"]["params"]["head"] == "binary"
    assert raw["pipeline"]["transformer"]["params"]["head"] == "binary"
    assert raw["pipeline"]["patchtst"]["params"]["label"] == "y_up"
    assert raw["pipeline"]["transformer"]["params"]["label"] == "y_up"


def test_train_has_no_search_node():
    raw = _raw("run-train.json")
    assert "search" not in raw["pipeline"]
    assert raw["pipeline"]["select"]["uses"] == "intraday_equities-portfolio"


def test_hpo_documents_declare_their_trial_counts():
    counts = {
        "run-hpo-linear.json": 32,
        "run-hpo-tree.json": 40,
        "run-hpo-tcn.json": 24,
    }
    for name, n_trials in counts.items():
        search = _raw(name)["pipeline"]["search"]
        assert search["params"]["n_trials"] == n_trials
        assert search["params"]["objective"] == "$select.metrics.rank_ic"


def test_framework_pins_hl_keep_and_holdouts():
    raw = _raw("run-framework.json")
    keep = UNIVERSE["keep_features"]
    derived = set(session_feature_names(
        UNIVERSE["lookback"], UNIVERSE["scales"], UNIVERSE["reference"],
        tuple(sorted(set((UNIVERSE.get("industry") or {}).values()))),
    ))
    assert keep
    assert all(name in derived for name in keep)
    assert all(not name.startswith("ret_lag_") for name in keep)
    assert UNIVERSE["horizon"]["label_lead"] == 470
    assert UNIVERSE["scan"]["picked_lookback"] == 120
    assert UNIVERSE["lookback"] == 30
    pipe = raw["pipeline"]
    assert pipe["label_train"]["params"]["lead"] == (
        "$universe.spec.horizon.label_lead"
    )
    assert pipe["label_val"]["params"]["lead"] == (
        "$universe.spec.horizon.label_lead"
    )
    assert pipe["qhat"]["params"]["features"] == keep
    assert pipe["search"]["params"]["n_trials"] == 50
    assert pipe["search"]["params"]["objective"] == "$select.metrics.rank_ic"
    assert pipe["ensemble"]["uses"] == "top-trials"
    assert pipe["ensemble"]["params"]["frac"] == 0.1
    assert pipe["ensemble"]["params"]["size"] == 5
    assert pipe["ensemble"]["params"]["select"] == "max"
    assert raw["splits"]["test_end_ms"] == UNIVERSE["holdouts"]["test_a_end_ms"]
    assert pipe["features"]["params"]["layout"] == "columns"
    assert pipe["label_train"]["inputs"]["bars"] == "$features.tape"
    assert pipe["label_val"]["inputs"]["bars"] == "$features.tape"


def test_lookback_agrees_with_ridge_features():
    lookback = UNIVERSE["lookback"]
    features = _raw("run-train.json")["pipeline"]["qhat"]["params"]["features"]
    assert _raw("run-train.json")["pipeline"]["window"]["params"]["lookback"] == (
        "$universe.lookback"
    )
    assert features == [f"ret_lag_{i}" for i in range(lookback)]


def test_sources_and_suites_follow_the_universe():
    symbols = UNIVERSE["symbols"]
    assert set(UNIVERSE["tradable"]) | set(UNIVERSE["reference"]) == set(symbols)
    assert not set(UNIVERSE["tradable"]) & set(UNIVERSE["reference"])
    alpaca = _raw("source-alpaca-backfill.json")
    schwab = _raw("source-schwab-live.json")
    assert alpaca["symbols"] == schwab["symbols"] == symbols
    session = UNIVERSE["session"]
    width = session["rth_end_minutes"] - session["rth_start_minutes"]
    horizon = UNIVERSE["horizon"]
    assert horizon["lead_stop"] == 3 * width
    assert horizon["anchors"] == [width, 2 * width, 3 * width]
    assert any(
        scale["width"] == horizon["lead_stop"] and scale["cross_session"]
        for scale in UNIVERSE["scales"]
    )
    assert "2022-06-20" in UNIVERSE["holidays"]
    assert "2021-06-18" not in UNIVERSE["holidays"]


def test_run_docs_do_not_restate_the_cohort():
    for name in _run_docs():
        raw = _raw(name)
        assert raw["pipeline"]["universe"]["params"]["path"] == (
            "configs/universe.json"
        ), name
        for node in raw["pipeline"].values():
            if node.get("uses") == "intraday_equities-bars":
                assert node["params"]["universe"] == "configs/universe.json", name
            if node.get("uses") == "intraday_equities-keep-symbols":
                assert node["inputs"]["symbols"] == "$universe.tradable", name
            if node.get("uses") == "intraday_equities-portfolio":
                assert node["inputs"]["tradable"] == "$universe.tradable", name
                assert "tradable" not in node["params"], name


def test_sources_pin_the_same_one_minute_cohort():
    alpaca = _raw("source-alpaca-backfill.json")
    schwab = _raw("source-schwab-live.json")
    assert alpaca["symbols"] == schwab["symbols"] == UNIVERSE["symbols"]
    assert alpaca["timeframe"] == schwab["timeframe"] == [1, "Minute"]
    assert alpaca["start"] == "2016-01-01"
    assert alpaca["feed"] == "sip"
    assert alpaca["adjustment"] == "raw"
    assert alpaca["storage"] == schwab["storage"] == {
        "payload_codec": "gzip",
        "observations_codec": "gzip",
    }
    check_config(AlpacaBars(), alpaca)
    check_config(SchwabBars(), schwab)


def test_suites_and_asset_model_validate():
    model = load_model(_path("asset-model.json"))
    assert set(model.kinds) == {"artifact", "dataset"}
    for name in ("suite-alpaca-bars.json", "suite-schwab-bars.json"):
        suite = load_suite(_path(name))
        assert [rule.id for rule in suite.rules] == [
            "bars-arrived",
            "close-present",
            "close-positive",
            "ohlc-present",
            "symbol-vocabulary",
            "dates-parse-bitemporally",
        ]
        vocab = next(rule for rule in suite.rules if rule.id == "symbol-vocabulary")
        assert vocab.kwargs["values"] == UNIVERSE["symbols"]


def _run_docs():
    return sorted(
        name for name in os.listdir(CONFIGS)
        if name.startswith("run-") and name.endswith(".json")
    )


def test_every_run_uses_one_local_mlflow_experiment():
    """Cadence and HPO compare in one local store, not per-run dirs."""
    for name in _run_docs():
        sinks = _raw(name)["tracking"]["sinks"]
        assert len(sinks) == 1, name
        sink = sinks[0]
        assert sink["kind"] == "dskit.pipeline.libs.mlflow:MlflowTracker", name
        assert sink["params"]["tracking_uri"] == "sqlite:///mlruns.db", name
        assert sink["params"]["experiment"] == "intraday_equities", name


def test_the_child_installs_what_its_tracking_sinks_need():
    import tomllib

    with open(os.path.join(CHILD_ROOT, "pyproject.toml"), "rb") as fh:
        declared = tomllib.load(fh)["project"]["dependencies"]
    for name in _run_docs():
        for sink in _raw(name)["tracking"]["sinks"]:
            module = sink["kind"].split(":")[0]
            pack = module.rsplit(".", 1)[1]
            assert any(pack in req for req in declared), (name, pack, declared)


def test_tracking_is_not_identity(tmp_path):
    name = _run_docs()[0]
    with_track = load_document(_path(name))
    raw = _raw(name)
    raw.pop("tracking")
    bare = tmp_path / name
    bare.write_text(json.dumps(raw), encoding="utf-8")
    assert with_track.hash == load_document(str(bare)).hash
