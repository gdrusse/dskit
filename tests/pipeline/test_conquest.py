"""ADR-0107 per-(unit,horizon) horizon-conquest gate tests."""

from __future__ import annotations

import pytest

from dskit.pipeline.node import NodeContext

from dskit.pipeline.conquest import HorizonConquest


def _ctx():
    return NodeContext(
        name="gate",
        asof="2026-02-28",
        run_dir="/tmp/opencode",
    )


def _rows(units, field="improvement"):
    rows = []
    for unit, horizons in units.items():
        for horizon, good in horizons.items():
            rows.append(
                {
                    "unit": unit,
                    "horizon": horizon,
                    field: 0.2 if good else -0.1,
                }
            )
    return rows


def _caps(out):
    return {row["unit"]: row for row in out["caps"]}


def test_caps_at_the_first_failing_horizon():
    rows = _rows({"AAPL": {1: True, 2: True, 3: False, 4: True}})
    node = HorizonConquest("gate", {"checks": [{"metric": "improvement"}]})
    out = node.run(_ctx(), {"records": rows})
    caps = _caps(out)
    assert caps["AAPL"]["capped_horizon"] == 2
    assert caps["AAPL"]["first_failing_horizon"] == 3


def test_caps_at_the_max_when_every_horizon_passes():
    rows = _rows({"JPM": {1: True, 2: True, 3: True}})
    out = HorizonConquest("gate", {"checks": [{"metric": "improvement"}]}).run(
        _ctx(), {"records": rows}
    )
    assert out["caps"][0]["capped_horizon"] == 3
    assert out["caps"][0]["first_failing_horizon"] is None


def test_multiple_checks_all_must_pass():
    rows = [
        {"unit": "A", "horizon": 1, "improvement": 0.2, "beats": True},
        {"unit": "A", "horizon": 2, "improvement": 0.2, "beats": False},
    ]
    out = HorizonConquest(
        "gate",
        {
            "checks": [
                {"metric": "improvement"},
                {"metric": "beats", "pass_if": "boolean"},
            ]
        },
    ).run(_ctx(), {"records": rows})
    assert out["caps"][0]["capped_horizon"] == 1


def test_p_below_rule_and_alpha():
    rows = [
        {"unit": "A", "horizon": 1, "p": 0.01},
        {"unit": "A", "horizon": 2, "p": 0.10},
    ]
    out = HorizonConquest(
        "gate", {"checks": [{"metric": "p", "pass_if": "p_below", "alpha": 0.05}]}
    ).run(_ctx(), {"records": rows})
    assert out["caps"][0]["capped_horizon"] == 1


def test_refuses_a_missing_check_metric():
    rows = [{"unit": "A", "horizon": 1, "improvement": 0.2}]
    node = HorizonConquest("gate", {"checks": [{"metric": "absent"}]})
    with pytest.raises(ValueError, match="absent"):
        node.run(_ctx(), {"records": rows})


def test_default_deny_params():
    problems = HorizonConquest.validate_params(
        {"checks": [{"metric": "improvement"}], "surprise": True}
    )
    assert any("unknown param" in p for p in problems)


def test_check_rejects_unknown_field():
    problems = HorizonConquest.validate_params(
        {"checks": [{"metric": "improvement", "surprise": True}]}
    )
    assert any("checks[0]" in p and "unknown param" in p for p in problems)


def test_requires_checks():
    problems = HorizonConquest.validate_params({})
    assert any("checks" in p for p in problems)


def test_horizons_walk_ascending_regardless_of_row_order():
    rows = _rows({"AAPL": {3: True, 1: True, 2: False}})
    out = HorizonConquest("gate", {"checks": [{"metric": "improvement"}]}).run(
        _ctx(), {"records": rows}
    )
    assert out["caps"][0]["capped_horizon"] == 1
    assert out["caps"][0]["first_failing_horizon"] == 2


def test_each_unit_is_gated_independently():
    rows = _rows(
        {
            "AAPL": {1: True, 2: False},
            "JPM": {1: True, 2: True, 3: True, 4: True, 5: True},
        }
    )
    out = HorizonConquest("gate", {"checks": [{"metric": "improvement"}]}).run(
        _ctx(), {"records": rows}
    )
    caps = _caps(out)
    assert caps["AAPL"]["capped_horizon"] == 1
    assert caps["JPM"]["capped_horizon"] == 5


def test_slice_stability_requires_every_slice_to_pass():
    rows = [
        # Horizon 1 passes in both slices.
        {"unit": "A", "horizon": 1, "slice": "morning", "improvement": 0.2},
        {"unit": "A", "horizon": 1, "slice": "afternoon", "improvement": 0.2},
        # Horizon 2 fails in the afternoon slice.
        {"unit": "A", "horizon": 2, "slice": "morning", "improvement": 0.2},
        {"unit": "A", "horizon": 2, "slice": "afternoon", "improvement": -0.1},
    ]
    out = HorizonConquest(
        "gate",
        {
            "checks": [{"metric": "improvement"}],
            "slice_field": "slice",
        },
    ).run(_ctx(), {"records": rows})
    assert out["caps"][0]["capped_horizon"] == 1


def test_slice_stability_requires_a_slice_value():
    rows = [{"unit": "A", "horizon": 1, "improvement": 0.2}]
    node = HorizonConquest(
        "gate", {"checks": [{"metric": "improvement"}], "slice_field": "slice"}
    )
    with pytest.raises(ValueError, match="slice"):
        node.run(_ctx(), {"records": rows})


def test_duplicate_rows_are_refused():
    rows = [
        {"unit": "A", "horizon": 1, "improvement": 0.2},
        {"unit": "A", "horizon": 1, "improvement": -0.1},
    ]
    node = HorizonConquest("gate", {"checks": [{"metric": "improvement"}]})
    with pytest.raises(ValueError, match="duplicate"):
        node.run(_ctx(), {"records": rows})


def test_sparse_slise_fails_closed():
    rows = [
        {"unit": "A", "horizon": 1, "slice": "morning", "improvement": 0.2},
        {"unit": "A", "horizon": 1, "slice": "afternoon", "improvement": 0.2},
        {"unit": "A", "horizon": 2, "slice": "morning", "improvement": 0.2},
    ]
    node = HorizonConquest(
        "gate", {"checks": [{"metric": "improvement"}], "slice_field": "slice"}
    )
    with pytest.raises(ValueError, match="missing"):
        node.run(_ctx(), {"records": rows})


def test_non_finite_metrics_are_refused():
    rows = [{"unit": "A", "horizon": 1, "improvement": float("inf")}]
    node = HorizonConquest("gate", {"checks": [{"metric": "improvement"}]})
    with pytest.raises(ValueError, match="finite"):
        node.run(_ctx(), {"records": rows})


def test_boolean_check_requires_an_actual_boolean():
    rows = [{"unit": "A", "horizon": 1, "beats": -0.5}]
    node = HorizonConquest(
        "gate", {"checks": [{"metric": "beats", "pass_if": "boolean"}]}
    )
    with pytest.raises(ValueError, match="non-boolean"):
        node.run(_ctx(), {"records": rows})


def test_unhashable_unit_is_refused():
    rows = [{"unit": ["A"], "horizon": 1, "improvement": 0.2}]
    node = HorizonConquest("gate", {"checks": [{"metric": "improvement"}]})
    with pytest.raises(ValueError, match="string or int"):
        node.run(_ctx(), {"records": rows})


def test_bool_unit_is_refused():
    rows = [{"unit": True, "horizon": 1, "improvement": 0.2}]
    node = HorizonConquest("gate", {"checks": [{"metric": "improvement"}]})
    with pytest.raises(ValueError, match="string or int"):
        node.run(_ctx(), {"records": rows})


def test_bool_slice_is_refused():
    rows = [{"unit": "A", "horizon": 1, "slice": False, "improvement": 0.2}]
    node = HorizonConquest(
        "gate", {"checks": [{"metric": "improvement"}], "slice_field": "slice"}
    )
    with pytest.raises(ValueError, match="string or int"):
        node.run(_ctx(), {"records": rows})


def test_empty_slice_is_refused():
    rows = [{"unit": "A", "horizon": 1, "slice": "", "improvement": 0.2}]
    node = HorizonConquest(
        "gate", {"checks": [{"metric": "improvement"}], "slice_field": "slice"}
    )
    with pytest.raises(ValueError, match="empty"):
        node.run(_ctx(), {"records": rows})


def test_negative_and_p_above_rules():
    rows = [
        {"unit": "A", "horizon": 1, "improvement": -0.1},
        {"unit": "A", "horizon": 2, "improvement": 0.1},
    ]
    node = HorizonConquest(
        "gate", {"checks": [{"metric": "improvement", "pass_if": "negative"}]}
    )
    out = node.run(_ctx(), {"records": rows})
    assert out["caps"][0]["capped_horizon"] == 1


def test_custom_unit_and_horizon_fields():
    rows = [{"ticker": "X", "h": 1, "improvement": 0.2, "slice": "m"},
            {"ticker": "X", "h": 2, "improvement": -0.1, "slice": "m"}]
    node = HorizonConquest(
        "gate",
        {
            "checks": [{"metric": "improvement"}],
            "unit_field": "ticker",
            "horizon_field": "h",
        },
    )
    out = node.run(_ctx(), {"records": rows})
    assert out["caps"][0]["unit"] == "X"
    assert out["caps"][0]["capped_horizon"] == 1


# -- ADR-0107 fix 1: gapped horizon ladders are refused, never silently
# -- treated as the whole ladder (CRITICAL review finding). ------------------


def test_gap_in_the_middle_is_refused():
    rows = _rows({"AAPL": {1: True, 3: True, 5: True}})
    node = HorizonConquest("gate", {"checks": [{"metric": "improvement"}]})
    with pytest.raises(ValueError, match=r"AAPL.*missing horizon\(s\) \[2, 4\]"):
        node.run(_ctx(), {"records": rows})


def test_ladder_not_starting_at_one_is_refused():
    rows = _rows({"AAPL": {3: True, 4: True, 5: True}})
    node = HorizonConquest("gate", {"checks": [{"metric": "improvement"}]})
    with pytest.raises(ValueError, match=r"AAPL.*missing horizon\(s\) \[1, 2\]"):
        node.run(_ctx(), {"records": rows})


def test_dense_ladder_from_one_still_passes():
    rows = _rows({"AAPL": {1: True, 2: True, 3: True, 4: True, 5: True}})
    out = HorizonConquest("gate", {"checks": [{"metric": "improvement"}]}).run(
        _ctx(), {"records": rows}
    )
    assert out["caps"][0]["capped_horizon"] == 5
    assert out["caps"][0]["first_failing_horizon"] is None


# -- ADR-0107 fix 3: the verdict names its passing checks and, under a
# -- slice_field, the per-slice evidence backing the cap. --------------------


def test_caps_row_names_the_passing_checks():
    rows = [
        {"unit": "A", "horizon": 1, "improvement": 0.2, "beats": True},
        {"unit": "A", "horizon": 2, "improvement": 0.2, "beats": False},
    ]
    out = HorizonConquest(
        "gate",
        {
            "checks": [
                {"metric": "improvement"},
                {"metric": "beats", "pass_if": "boolean"},
            ]
        },
    ).run(_ctx(), {"records": rows})
    assert out["caps"][0]["passing_checks"] == ["improvement", "beats"]


def test_capped_at_zero_names_no_passing_checks():
    rows = [{"unit": "A", "horizon": 1, "improvement": -0.1}]
    out = HorizonConquest("gate", {"checks": [{"metric": "improvement"}]}).run(
        _ctx(), {"records": rows}
    )
    assert out["caps"][0]["capped_horizon"] == 0
    assert out["caps"][0]["passing_checks"] == []


def test_caps_row_carries_slice_evidence_at_the_cap():
    rows = [
        {"unit": "A", "horizon": 1, "slice": "morning", "improvement": 0.2},
        {"unit": "A", "horizon": 1, "slice": "afternoon", "improvement": 0.2},
    ]
    out = HorizonConquest(
        "gate",
        {"checks": [{"metric": "improvement"}], "slice_field": "slice"},
    ).run(_ctx(), {"records": rows})
    assert out["caps"][0]["slice_evidence"] == {"afternoon": True, "morning": True}


def test_slice_evidence_is_absent_without_a_slice_field():
    rows = [{"unit": "A", "horizon": 1, "improvement": 0.2}]
    out = HorizonConquest("gate", {"checks": [{"metric": "improvement"}]}).run(
        _ctx(), {"records": rows}
    )
    assert "slice_evidence" not in out["caps"][0]


def test_records_output_is_retired():
    assert HorizonConquest.outputs == ("caps", "metrics")
