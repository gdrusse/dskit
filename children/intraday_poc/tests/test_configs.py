"""Every ``configs/*.json`` parses AND validates against its engine.

Configs are the child's interface, so drift between a config and the
code it drives must fail HERE — loudly, naming the file — never at the
moment someone finally runs it. Documents must also PLAN without torch,
pyomo, or alpaca-py installed — the toolkit's core rule.
"""

import json
import os

from dskit.assets import load_model
from dskit.onboarding import check_config, load_suite
from dskit.pipeline.document import load_document

from intraday_poc.connectors import AlpacaBarsConnector

CHILD_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIGS = os.path.join(CHILD_ROOT, "configs")

TRAIN_NODES = {"bars", "window", "aapl_rows", "msft_rows",
               "qhat_aapl", "qhat_msft"}
BACKTEST_NODES = {"bars", "window", "aapl_train", "aapl_val", "msft_train",
                  "msft_val", "qhat_aapl", "qhat_msft", "fc_aapl", "fc_msft",
                  "forecasts", "labeled", "select"}


def _path(name):
    return os.path.join(CONFIGS, name)


def test_run_train_is_a_valid_pipeline_document():
    document = load_document(_path("run-train.json"))
    assert set(document.pipeline) == TRAIN_NODES, (
        "run-train.json drifted from the documented DAG"
    )
    assert document.hash, "a valid document always has an identity hash"


def test_run_backtest_is_a_valid_walkforward_document():
    document = load_document(_path("run-backtest.json"))
    assert set(document.pipeline) == BACKTEST_NODES, (
        "run-backtest.json drifted from the documented DAG"
    )
    assert document.walkforward is not None, (
        "the backtest IS the walk-forward — the section is load-bearing"
    )
    assert document.hash


def test_the_two_documents_share_their_modelling_core():
    """Backtest and production fit must consume identical features or
    the backtest proves nothing — pinned by comparing the shared nodes'
    params verbatim."""
    with open(_path("run-train.json"), encoding="utf-8") as fh:
        train = json.load(fh)
    with open(_path("run-backtest.json"), encoding="utf-8") as fh:
        backtest = json.load(fh)
    assert train["pipeline"]["window"]["params"] == \
        backtest["pipeline"]["window"]["params"]
    for key in ("qhat_aapl", "qhat_msft"):
        t = train["pipeline"][key]["params"]
        b = backtest["pipeline"][key]["params"]
        for knob in ("module", "module_params", "features", "label",
                     "optimizer", "epochs", "lr", "loader"):
            assert t[knob] == b[knob], (key, knob)


def test_asset_model_validates_and_keeps_its_shape():
    model = load_model(_path("asset-model.json"))
    assert set(model.kinds) == {"artifact", "dataset"}, (
        "asset-model.json drifted from the documented dataset/artifact pair"
    )
    governed = sorted(k for k, spec in model.kinds.items() if spec.states)
    assert governed == ["dataset"], (
        f"only 'dataset' is governed by design, got lifecycles on {governed}"
    )


def test_suite_bars_validates_and_names_its_rules():
    suite = load_suite(_path("suite-bars.json"))
    assert [r.id for r in suite.rules] == [
        "bars-arrived", "close-present", "close-positive",
        "symbol-vocabulary", "dates-parse-bitemporally",
    ], "suite-bars.json drifted from its documented rule set"


def test_source_configs_validate_against_the_connectors_spec():
    connector = AlpacaBarsConnector()
    for name in ("source-backfill.json", "source-live.json"):
        with open(_path(name), encoding="utf-8") as fh:
            config = json.load(fh)
        check_config(connector, config)  # default-deny against spec()
        connector._knobs(config)  # and the knob gate itself accepts them


def test_lookback_agrees_everywhere():
    """The window width, the module's lookback, and the feature list
    must be the SAME number — the live loop and the LSTM both refuse a
    mismatch, so pin it at config level too."""
    for doc_name in ("run-train.json", "run-backtest.json"):
        with open(_path(doc_name), encoding="utf-8") as fh:
            doc = json.load(fh)
        lookback = doc["pipeline"]["window"]["params"]["lookback"]
        for key in ("qhat_aapl", "qhat_msft"):
            params = doc["pipeline"][key]["params"]
            assert params["module_params"]["lookback"] == lookback, doc_name
            features = params["features"]
            assert features == [f"ret_lag_{i}" for i in range(lookback)], (
                doc_name, key,
            )
