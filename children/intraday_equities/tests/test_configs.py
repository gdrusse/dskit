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

SYMBOLS = ["AAPL", "JPM", "XOM", "WMT", "LLY", "SPY"]
TRADABLE = ["AAPL", "JPM", "XOM", "WMT", "LLY"]
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
    "alpaca", "session", "window", "grid", "tradable",
    "train_rows", "val_rows", "qhat", "select",
}


def _path(name):
    return os.path.join(CONFIGS, name)


def _raw(name):
    with open(_path(name), encoding="utf-8") as fh:
        return json.load(fh)


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
        assert other["pipeline"]["select"]["params"]["tradable"] == TRADABLE


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
        assert search["params"]["objective"] == "$select.metrics.n_picks"


def test_lookback_agrees_with_ridge_features():
    lookback = _raw("run-train.json")["pipeline"]["window"]["params"]["lookback"]
    features = _raw("run-train.json")["pipeline"]["qhat"]["params"]["features"]
    assert features == [f"ret_lag_{i}" for i in range(lookback)]
    assert lookback == 30


def test_sources_pin_the_same_one_minute_cohort():
    alpaca = _raw("source-alpaca-backfill.json")
    schwab = _raw("source-schwab-live.json")
    assert alpaca["symbols"] == schwab["symbols"] == SYMBOLS
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
        assert vocab.kwargs["values"] == SYMBOLS
