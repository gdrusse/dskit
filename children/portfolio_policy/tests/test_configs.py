"""Every ``configs/*.json`` parses AND validates against its engine.

Configs are the child's interface, so drift between a config and the
code it drives must fail HERE — loudly, naming the file — never at the
moment someone finally runs it.
"""

import json
import os

from dskit.assets import load_model
from dskit.onboarding import check_config, load_suite
from dskit.pipeline.document import load_document

from portfolio_policy.connectors import SampleConnector

CHILD_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIGS = os.path.join(CHILD_ROOT, "configs")


def _path(name):
    return os.path.join(CONFIGS, name)


def test_run_sample_is_a_valid_pipeline_document():
    # load_document raises naming the path on any shape violation.
    document = load_document(_path("run-sample.json"))
    assert set(document.pipeline) == {"sample", "enrich"}, (
        "run-sample.json drifted from the documented sample -> enrich DAG"
    )
    assert document.hash, "a valid document always has an identity hash"


def test_asset_model_validates_and_keeps_its_shape():
    model = load_model(_path("asset-model.json"))
    assert set(model.kinds) == {"artifact", "dataset"}, (
        "asset-model.json drifted from the documented dataset/artifact pair"
    )
    governed = sorted(k for k, spec in model.kinds.items() if spec.states)
    assert governed == ["dataset"], (
        f"only 'dataset' is governed by design, got lifecycles on {governed}"
    )


def test_suite_sample_validates_and_names_its_rules():
    suite = load_suite(_path("suite-sample.json"))
    assert [r.id for r in suite.rules] == \
        ["rows-arrived", "value-present", "value-in-range"], (
        "suite-sample.json drifted from its documented rule set"
    )


def test_source_sample_validates_against_the_connectors_spec():
    with open(_path("source-sample.json"), encoding="utf-8") as fh:
        config = json.load(fh)
    connector = SampleConnector()
    check_config(connector, config)  # default-deny against spec()
    # The reserved "storage" block (ADR-0036) is platform config —
    # acquire strips it before the connector sees config; mirror that.
    connector.check({k: v for k, v in config.items() if k != "storage"})


def test_decision_day_is_declared_once_per_document():
    """The snapshot filter and the tax date name the same day: a mismatch would
    tax one day's positions as of another, silently, once lots carry dates."""
    document = load_document(_path("run-hold-vs-redeploy.json"))
    where = {c["field"]: c["value"] for c in document.pipeline["snapshot"].params["where"]}
    assert document.pipeline["tax"].params["as_of"] == where["as_of"]
    assert where["asset_type"] == "EQUITY"


def test_market_horizon_is_the_decision_horizon():
    """The market blocks are an average over the horizon the decision uses; the number
    appears in two nodes, so a mismatch must fail here, not in a quietly wrong rate."""
    document = load_document(_path("run-hold-vs-redeploy.json"))
    market = document.pipeline["market"].params
    decide = document.pipeline["decide"].params
    assert market["horizon_years"] == decide["horizon_years"]
    assert market["horizon_years"] <= market["reversion_years"]


def test_summary_restates_the_decision_terms_exactly():
    """The summary's break-even inverts decide's comparison, so every term and field it shares
    with decide must be decide's own; a mismatch would answer a different question, silently."""
    document = load_document(_path("run-hold-vs-redeploy.json"))
    summary = document.pipeline["summary"].params
    decide = document.pipeline["decide"].params
    shared = ("redeploy_cost", "risk_premium", "redeploy_tax_rate", "redeploy_tax_timing",
              "value_field", "net_field", "basis_field", "symbol_field")
    assert {name: summary[name] for name in shared} == {name: decide[name] for name in shared}
    assert summary["symbol_field"] in summary["key_fields"]


def test_outcomes_income_is_the_market_dividend_yield():
    """The hold forecast's income component is the yield the market node fed it; scoring the
    forecast must remove that same number."""
    decide = load_document(_path("run-hold-vs-redeploy.json"))
    outcomes = load_document(_path("run-decision-outcomes.json"))
    assert (outcomes.pipeline["outcomes"].params["income_rate"]
            == decide.pipeline["market"].params["dividend_yield"])
