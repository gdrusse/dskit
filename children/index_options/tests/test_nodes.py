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

_TIME_ALIASES = (
    "2026-01-16T20:45:00Z", "2026-01-16T15:45:00-05:00", "2026-01-16T20:45:00+00:00",
)


@pytest.mark.parametrize("quote_at", _TIME_ALIASES)
@pytest.mark.parametrize("effective_at", _TIME_ALIASES)
@pytest.mark.parametrize("reference_at", _TIME_ALIASES)
def test_quote_references_match_instants_not_spellings(rows, params, quote_at, effective_at, reference_at):
    rows["quotes"][0].update(quote_at=quote_at, effective_at=effective_at)
    params["legs"][0]["quote_at"] = reference_at
    node = CondorPayoffDiagnostic("d", params)
    assert node.validate_inputs(rows) == []
    assert node.run(None, rows)["report"].value["net_pnl_usd"] == "252"


@pytest.mark.parametrize("reference_at", _TIME_ALIASES)
@pytest.mark.parametrize("ask", ["2.60", "2.61"])
def test_alias_equivalent_selected_versions_are_ambiguous(rows, params, reference_at, ask):
    rows["quotes"].append({
        **rows["quotes"][0], "effective_at": _TIME_ALIASES[1], "ask": ask,
    })
    params["legs"][0]["quote_at"] = reference_at
    node = CondorPayoffDiagnostic("d", params)
    assert node.validate_inputs(rows)
    with pytest.raises(ValueError, match="exactly one"):
        node.run(None, rows)


def test_distinct_quote_versions_at_equivalent_instants_remain_selectable(rows, params):
    rows["quotes"].append({
        **rows["quotes"][0], "effective_at": _TIME_ALIASES[1], "row_version": "v2", "ask": "2.61",
    })
    params["legs"][0].update(quote_at=_TIME_ALIASES[2], quote_version="v2")
    assert CondorPayoffDiagnostic("d", params).run(None, rows)["report"].value["net_pnl_usd"] == "251"


@pytest.mark.parametrize("leg", range(4))
@pytest.mark.parametrize("field", ["bid_size", "ask_size"])
def test_quote_size_is_checked_against_count_in_direct_node(rows, params, leg, field):
    params["count"] = 2
    rows["quotes"][leg][field] = 1
    with pytest.raises(ValueError, match="sizes"):
        CondorPayoffDiagnostic("d", params).run(None, rows)
    rows["quotes"][leg][field] = 2
    assert CondorPayoffDiagnostic("d", params).run(None, rows)["report"].value["net_pnl_usd"] == "512"


@pytest.mark.parametrize("stream, effective", [
    ("quotes", "2026-01-15T20:45:00Z"), ("settlements", "2026-02-19T21:00:00Z"),
])
def test_direct_node_refuses_isolated_effective_clock_mismatch(rows, params, stream, effective):
    rows[stream][0]["effective_at"] = effective
    with pytest.raises(ValueError, match="must equal effective_at"):
        CondorPayoffDiagnostic("d", params).run(None, rows)
