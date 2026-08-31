"""Every shipped config validates, and the action documents stay twins."""

import copy
import json
import os

from dskit.assets import load_model
from dskit.onboarding import check_config, load_suite
from dskit.pipeline.document import load_document

from intraday_equities.connectors import AlpacaBars, SchwabBars

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
