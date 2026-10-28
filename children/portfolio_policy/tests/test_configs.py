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
    """The snapshot filter, the lots' replay date and the tax date name the same day: a
    mismatch would tax one day's positions (or lots) as of another, silently."""
    document = load_document(_path("run-hold-vs-redeploy.json"))
    where = {c["field"]: c["value"] for c in document.pipeline["snapshot"].params["where"]}
    assert document.pipeline["tax"].params["as_of"] == where["as_of"]
    assert document.pipeline["lots"].params["as_of"] == where["as_of"]
    assert where["asset_type"] == "EQUITY"


def test_the_tax_reads_lots_and_the_lot_policy_is_declared_once():
    """The tax runs per lot with each lot's own date; the two lot kinds restate the holding key
    and the incomplete-history policy, so a mismatch must fail here, not in a quiet fallback."""
    document = load_document(_path("run-hold-vs-redeploy.json"))
    nodes = document.pipeline
    lots, join, tax = nodes["lots"].params, nodes["lot_positions"].params, nodes["tax"].params
    assert nodes["lots"].inputs == {"records": "$trades.records"}
    assert nodes["lot_positions"].inputs == {"lots": "$lots.records",
                                             "positions": "$snapshot.records"}
    assert nodes["tax"].inputs == {"records": "$lot_positions.records"}
    assert tax["acquired_field"] == "acquired", "each lot is taxed by its own holding period"
    assert lots["method"] == "fifo"
    assert lots["key_fields"] == join["key_fields"]
    assert lots["allow_incomplete"] == join["allow_incomplete"]
    assert (join["value_field"], join["basis_field"]) == (tax["value_field"], tax["basis_field"])
    assert set(lots["key_fields"]) < set(nodes["trades"].params["key_fields"])
    assert join["tolerance"] == 0.0, "any slack hides a missing lot: raise it only on purpose"


def test_every_lot_field_is_a_column_of_the_transactions_connector():
    """TaxLots reads its date, quantity, price, amount and order columns by the names the document
    declares; the connector writes exactly TRANSACTION_FIELDS. A misspelt name must fail HERE (a
    misspelt amount_field once read as all-null and quietly turned every buy's cash into
    quantity x price). A day is ordered by the execution time, then the unique id."""
    from dskit.onboarding.libs.schwab import TRANSACTION_FIELDS

    lots = load_document(_path("run-hold-vs-redeploy.json")).pipeline["lots"].params
    assert lots["amount_field"] in TRANSACTION_FIELDS
    named = [lots[n] for n in ("date_field", "quantity_field", "price_field", "amount_field")]
    assert set([*named, *lots["order_fields"], *lots["key_fields"]]) <= set(TRANSACTION_FIELDS)
    assert lots["order_fields"] == ["time", "transaction_id"]


def test_the_trades_key_is_the_transactions_connectors_key():
    """ObservationRows deduplicates on the document's key: anything coarser than the
    connector's own key would collapse two fills of one transaction into one."""
    from dskit.onboarding.libs.schwab import TRANSACTION_KEY_FIELDS, TRANSACTION_STREAM

    trades = load_document(_path("run-hold-vs-redeploy.json")).pipeline["trades"].params
    assert trades["key_fields"] == list(TRANSACTION_KEY_FIELDS)
    assert trades["stream"] == TRANSACTION_STREAM


def test_every_reader_of_lot_rows_keys_on_the_lot():
    """Decisions are per lot: the summary, the decisions read back for scoring and the
    outcomes all key on lot_id, and the outcomes match the later snapshot per holding."""
    decide = load_document(_path("run-hold-vs-redeploy.json"))
    outcomes = load_document(_path("run-decision-outcomes.json"))
    summary = decide.pipeline["summary"].params
    scored = outcomes.pipeline["outcomes"].params
    holding = decide.pipeline["lot_positions"].params["key_fields"]
    assert summary["key_fields"] == [*holding, "lot_id"]
    assert scored["key_fields"] == summary["key_fields"]
    assert scored["match_fields"] == holding
    assert outcomes.pipeline["decisions"].params["key_fields"] == [*summary["key_fields"], "as_of"]


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


@pytest.mark.parametrize("writer", ["decisions", "summaries"])
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
