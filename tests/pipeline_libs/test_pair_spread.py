"""Behavioral tests for lag-safe pair spreads."""

import pytest

from dskit.pipeline.libs.pair_spread import RollingPairSpread
from dskit.pipeline.node import NodeContext


def run(surfaces, edges=None, **over):
    params = {
        "select_mode": "edges",
        "lookback": 2,
        "entry_z": 1.0,
        "value_field": "atm_iv",
        "target_field": "target_dte",
        "target_value": 30,
        "surface_status_field": "status",
    }
    params.update(over)
    inputs = {"surfaces": surfaces}
    if edges is not None:
        inputs["edges"] = edges
    return RollingPairSpread("spread", params).run(
        NodeContext(name="t", asof="2025-12-31", run_dir="/nonexistent"), inputs
    )


def rows(values):
    return [
        {
            "date": day,
            "symbol": symbol,
            "target_dte": 30,
            "status": "ok",
            "atm_iv": value,
        }
        for day, av, bv in values
        for symbol, value in (("A", av), ("B", bv))
    ]


def edges(*days):
    return [
        {"date": day, "symbol_a": "A", "symbol_b": "B", "weight": 0.7} for day in days
    ]


def test_prior_only_window_and_entry_direction():
    surfaces = rows(
        [
            ("2020-01-02", 0.30, 0.20),
            ("2020-01-03", 0.40, 0.20),
            ("2020-01-06", 0.60, 0.20),
            ("2020-01-07", 0.10, 0.20),
        ]
    )
    out = run(surfaces, edges("2020-01-02", "2020-01-03", "2020-01-06", "2020-01-07"))
    r = out["records"]
    assert [x["status"] for x in r[:2]] == ["warmup", "warmup"]
    assert r[2]["spread"] == pytest.approx(0.40)
    assert r[2]["mean"] == pytest.approx(0.15)
    assert r[2]["std"] == pytest.approx(0.05 * 2**0.5)
    assert r[2]["z"] > 1 and r[2]["signal"] == "short_a_long_b"
    assert r[3]["mean"] == pytest.approx(0.30)
    assert r[3]["signal"] == "long_a_short_b"


def test_inactive_graph_days_still_supply_history_and_all_pairs_baseline():
    surfaces = rows(
        [
            ("2020-01-02", 0.30, 0.20),
            ("2020-01-03", 0.40, 0.20),
            ("2020-01-06", 0.60, 0.20),
        ]
    )
    primary = run(surfaces, edges("2020-01-06"))["records"]
    baseline = run(surfaces, select_mode="all_pairs", symbols=["B", "A"])["records"]
    assert len(primary) == 1 and primary[0]["status"] == "ok"
    assert [x["status"] for x in baseline] == ["warmup", "warmup", "ok"]
    assert primary[0]["z"] == baseline[-1]["z"]
    assert all(x["symbol_a"] == "A" and x["symbol_b"] == "B" for x in baseline)


def test_missing_surface_and_zero_variance_refuse_signal():
    surfaces = rows(
        [
            ("2020-01-02", 0.30, 0.20),
            ("2020-01-03", 0.30, 0.20),
            ("2020-01-06", 0.60, 0.20),
        ]
    )
    out = run(surfaces, edges("2020-01-06"))["records"][0]
    assert out["status"] == "zero_std" and out["z"] is None and out["signal"] == "none"
    surfaces[-1]["status"] = "no_rate"
    out = run(surfaces, edges("2020-01-06"))["records"][0]
    assert (
        out["status"] == "missing_surface"
        and out["spread"] is None
        and out["signal"] == "none"
    )


def test_custom_fields_and_invalid_duplicate_refused():
    surfaces = rows(
        [
            ("2020-01-02", 0.30, 0.20),
            ("2020-01-03", 0.40, 0.20),
            ("2020-01-06", 0.60, 0.20),
        ]
    )
    for row in surfaces:
        row["skew"] = row.pop("atm_iv")
    out = run(surfaces, edges("2020-01-06"), value_field="skew")["records"][0]
    assert out["status"] == "ok"
    with pytest.raises(ValueError, match="duplicate"):
        run(surfaces + [dict(surfaces[0])], edges("2020-01-06"), value_field="skew")


def test_current_and_future_values_never_enter_the_prior_window():
    base = rows(
        [
            ("2020-01-02", 0.30, 0.20),
            ("2020-01-03", 0.40, 0.20),
            ("2020-01-06", 0.60, 0.20),
            ("2020-01-07", 0.50, 0.20),
        ]
    )
    selected = edges("2020-01-06")
    first = run(base, selected)["records"][0]
    changed = [dict(row) for row in base]
    changed[-1]["atm_iv"] = 9.0
    assert run(changed, selected)["records"][0] == first
    assert first["mean"] == pytest.approx(0.15)
    assert first["spread"] == pytest.approx(0.40)


def test_duplicate_edges_and_sealed_rows_refuse():
    surfaces = rows([("2020-01-02", 0.30, 0.20)])
    with pytest.raises(ValueError, match="duplicate edge"):
        run(surfaces, edges("2020-01-02", "2020-01-02"))
    with pytest.raises(ValueError, match="end_before"):
        run(surfaces, edges("2020-01-02"), end_before="2020-01-02")


def test_graph_fit_provenance_is_carried_without_baseline_leakage():
    surfaces = rows(
        [
            ("2020-01-02", 0.30, 0.20),
            ("2020-01-03", 0.40, 0.20),
            ("2020-01-06", 0.60, 0.20),
        ]
    )
    selected = edges("2020-01-06")
    selected[0].update(fit_date="2020-01-06", trained_through="2020-01-03")
    row = run(surfaces, selected)["records"][0]
    assert (row["fit_date"], row["trained_through"]) == ("2020-01-06", "2020-01-03")
    baseline = run(surfaces, select_mode="all_pairs", symbols=["A", "B"])["records"]
    assert all(r["fit_date"] is None and r["trained_through"] is None for r in baseline)
    selected[0]["trained_through"] = "2020-01-06"
    with pytest.raises(ValueError, match="trained_through"):
        run(surfaces, selected)


def test_missing_prior_session_does_not_stretch_window():
    surfaces = rows(
        [
            ("2020-01-02", 0.30, 0.20),
            ("2020-01-03", 0.40, 0.20),
            ("2020-01-06", 0.50, 0.20),
            ("2020-01-07", 0.60, 0.20),
        ]
    )
    surfaces[5]["status"] = "no_rate"  # B has no usable quote on Jan 6
    scored = run(surfaces, edges("2020-01-07"))["records"][0]
    assert scored["status"] == "incomplete_window"
    assert scored["n_history"] == 1
    assert scored["z"] is None and scored["signal"] == "none"
