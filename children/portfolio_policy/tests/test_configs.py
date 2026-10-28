"""Every ``configs/*.json`` parses AND validates against its engine.

Configs are the child's interface, so drift between a config and the
code it drives must fail HERE — loudly, naming the file — never at the
moment someone finally runs it.
"""

import json
import os

import pytest
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
    """The snapshot filter, the tax date, the risk day and the staleness reference name the same
    day: a mismatch would tax one day's positions as of another, price them with another day's
    risk, or measure a bars pull's staleness from the wrong day, silently."""
    document = load_document(_path("run-hold-vs-redeploy.json"))
    where = {c["field"]: c["value"] for c in document.pipeline["snapshot"].params["where"]}
    assert document.pipeline["tax"].params["as_of"] == where["as_of"]
    assert where["asset_type"] == "EQUITY"
    (risk_day,) = document.pipeline["risk_day"].params["where"]
    # <=: a pull on the decision day has no closed bar for it, so the latest earlier session
    assert risk_day == {"field": "ts", "op": "<=", "value": where["as_of"]}
    assert document.pipeline["risk_latest"].params["reference_date"] == where["as_of"]


def test_a_stale_bars_pull_is_refused_past_five_calendar_days():
    """Research A0003 / ADR-0279 amendment: the latest session of a symbol may lag the decision
    day by a long weekend and a day to spare (Friday to a Tuesday after a holiday Monday is 4),
    not more. Calendar days: the gate holds no trading calendar."""
    latest = load_document(_path("run-hold-vs-redeploy.json")).pipeline["risk_latest"].params
    assert latest["max_lag_days"] == 5


def test_every_return_is_close_to_close_on_both_sides():
    """The holding's and SPY's returns are price returns on one basis: the CLOSE. Another bar
    field (open, high, low) would be a different, noisier return with no error raised."""
    features = load_document(_path("run-hold-vs-redeploy.json")).pipeline["risk_features"].params
    assert features["close_field"] == "close"
    assert features["benchmark_close_field"] == "close"


def test_the_risk_windows_declared_twice_agree():
    """The windows live in risk_features; the field names they produce and the window columns
    the risk file carries restate them, so a changed window must change every restatement."""
    document = load_document(_path("run-hold-vs-redeploy.json"))
    features = document.pipeline["risk_features"].params
    latest = document.pipeline["risk_latest"].params["aggregates"]
    fields = document.pipeline["risk"].params["fields"]
    (vol_window,) = features["realized_vol"]["windows"]
    beta_window = features["market_relative"]["beta_window"]
    # `integer`, not `const` (which computes in floats): the risk file reads 252, not 252.0
    assert fields["vol_window"] == {"integer": vol_window}
    assert fields["beta_window"] == {"integer": beta_window}
    assert latest["daily_vol"]["field"] == features["realized_vol"]["field"].replace(
        "{window}", str(vol_window))
    assert latest["beta_raw"]["field"] == features["market_relative"]["beta_field"]
    assert (vol_window, beta_window) == (252, 252), "one year of sessions (research A0003)"


def test_the_risk_estimators_are_the_research_decision():
    """Research A0003: annual volatility = daily RMS x sqrt(252 trading days); the beta that
    prices the hold return is Blume-adjusted, raw - 0.33 x (raw - 1) = 0.67 x raw + 0.33."""
    fields = load_document(_path("run-hold-vs-redeploy.json")).pipeline["risk"].params["fields"]
    assert fields["annual_vol"] == {"op": "mul", "args": [
        {"field": "daily_vol"}, {"op": "sqrt", "args": [{"const": 252}]}]}
    assert fields["beta"] == {"op": "sub", "args": [
        {"field": "beta_raw"},
        {"op": "mul", "args": [{"const": 0.33},
                               {"op": "sub", "args": [{"field": "beta_raw"}, {"const": 1}]}]}]}


def test_the_betas_and_volatilities_are_wired_never_typed():
    """No hand beta table ships: the hold returns read the risk chain's adjusted betas, and the
    summary its annual volatilities. The hand `betas` param stays a fallback of the kind only."""
    pipeline = load_document(_path("run-hold-vs-redeploy.json")).pipeline
    assert "betas" not in pipeline["betas"].params
    assert "default_beta" not in pipeline["betas"].params
    assert pipeline["betas"].inputs["betas"] == "$risk_betas.table"
    assert pipeline["risk_betas"].params == {"key": "symbol", "value": "beta"}
    assert pipeline["summary"].inputs["volatilities"] == "$vols.table"
    assert pipeline["vols"].params == {"key": "symbol", "value": "annual_vol"}
    assert pipeline["bars"].params["source"] == "schwab_daily"
    assert pipeline["bars"].params["key_fields"] == ["symbol", "ts"]


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


@pytest.mark.parametrize("writer", ["decisions", "summaries", "risks"])
def test_the_run_writers_never_overwrite(writer):
    """Each run writes its own file (ADR-0247); a writer allowed to overwrite could replace the
    rows a later run reads or a past run's evidence, silently, so the document pins it off."""
    document = load_document(_path("run-hold-vs-redeploy.json"))
    assert document.pipeline[writer].params["overwrite"] is False


def test_outcomes_income_is_the_market_dividend_yield():
    """The hold forecast's income component is the yield the market node fed it; scoring the
    forecast must remove that same number."""
    decide = load_document(_path("run-hold-vs-redeploy.json"))
    outcomes = load_document(_path("run-decision-outcomes.json"))
    assert (outcomes.pipeline["outcomes"].params["income_rate"]
            == decide.pipeline["market"].params["dividend_yield"])


def test_tax_rates_are_declared_once_per_path_and_agree():
    """The hold path's terminal rate and the redeploy path's annual rate restate the tax
    schedule's long and short rates; a mismatch would tax the two paths on different schedules."""
    document = load_document(_path("run-hold-vs-redeploy.json"))
    tax = document.pipeline["tax"].params
    decide = document.pipeline["decide"].params
    assert tax["long_term_rate"] == decide["terminal_tax_rate"]
    assert tax["short_term_rate"] == decide["redeploy_tax_rate"]


def test_the_scoring_snapshot_keeps_the_decision_snapshots_universe():
    """A decided position is scored only if the later snapshot filter can still see it, so
    its asset and side clauses restate the decision document's; the date is its own."""
    decide = load_document(_path("run-hold-vs-redeploy.json"))
    outcomes = load_document(_path("run-decision-outcomes.json"))
    clauses = decide.pipeline["snapshot"].params["where"]
    later = outcomes.pipeline["later"].params["where"]
    assert [c["field"] for c in later] == ["as_of", "asset_type", "long_quantity"]
    assert later[1:] == [c for c in clauses if c["field"] != "as_of"]
    assert later[2] == {"field": "long_quantity", "op": ">", "value": 0}
