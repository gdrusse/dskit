"""The public Node facade must validate and persist the same domain result."""

import pytest

from dskit.pipeline.base import ConfigError
from index_options.nodes import CondorPayoffDiagnostic


def test_direct_node_has_exact_cashflows_and_labels(rows, params):
    report = CondorPayoffDiagnostic("diagnostic", params).run(None, rows)["report"].value
    assert report.get("net_pnl_usd") == "252"
    assert report["max_loss_after_fees_usd"] == "248"
    assert report["decision_eligible"] is False
    assert report["kind"] == "synthetic_ex_post_diagnostic"


def test_unknown_params_refuse(params):
    with pytest.raises(ConfigError, match="surprise"):
        CondorPayoffDiagnostic("diagnostic", {**params, "surprise": 1})

import copy


@pytest.mark.parametrize("stream", ["contracts", "quotes", "settlements"])
@pytest.mark.parametrize("mutation", ["missing", "duplicate", "wrong_corpus", "provenance", "bad_unused"])
def test_direct_boundary_rejects_stream_and_reference_failures(rows, params, stream, mutation):
    if mutation == "missing":
        rows[stream] = []
    elif mutation == "duplicate":
        rows[stream].append(copy.deepcopy(rows[stream][0]))
    elif mutation == "wrong_corpus":
        rows[stream][0]["corpus_id"] = "other"
    elif mutation == "provenance":
        rows[stream][0]["provenance"] = "real"
    else:
        rows[stream].append({**rows[stream][0], "row_version": "unselected", "known_at_basis": "guessed"})
    node = CondorPayoffDiagnostic("d", params)
    assert node.validate_inputs(rows)
    with pytest.raises(ValueError):
        node.run(None, rows)


@pytest.mark.parametrize("multiplier", [0, -1, True])
def test_direct_diagnostic_refuses_bad_multiplier(rows, params, multiplier):
    rows["contracts"][0]["multiplier"] = multiplier
    with pytest.raises(ValueError, match="multiplier"):
        CondorPayoffDiagnostic("d", params).run(None, rows)


@pytest.mark.parametrize("field", ["contract_version", "quote_version", "quote_at"])
def test_missing_exact_version_is_never_guessed(rows, params, field):
    params["legs"][0][field] = "2026-01-17T20:45:00Z" if field == "quote_at" else "absent"
    with pytest.raises(ValueError, match="exactly one"):
        CondorPayoffDiagnostic("d", params).run(None, rows)


@pytest.mark.parametrize("target", ["leg", "settlement"])
def test_nested_unknown_params_refuse(params, target):
    item = params["legs"][0] if target == "leg" else params["settlement"]
    item["surprise"] = True
    with pytest.raises(ConfigError, match="surprise"):
        CondorPayoffDiagnostic("d", params)


def test_row_order_does_not_change_selected_result(rows, params):
    expected = CondorPayoffDiagnostic("d", params).run(None, rows)["report"].value
    for stream in rows.values():
        stream.reverse()
    assert CondorPayoffDiagnostic("d", params).run(None, rows)["report"].value == expected


def test_ordinary_diagnostic_subclass_retains_validation_and_serving_refusal(rows, params):
    class ResearchDiagnostic(CondorPayoffDiagnostic):
        pass

    assert ResearchDiagnostic.serving_effect(params, {}) == "forbidden"
    assert ResearchDiagnostic("d", params).run(None, rows)["report"].value["net_pnl_usd"] == "252"
    rows["contracts"][0]["multiplier"] = 0
    with pytest.raises(ValueError):
        ResearchDiagnostic("d", params).run(None, rows)
