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


# -- PairSpreadHorizonRows: session-indexed labels and lagged features per pair (ADR-0272) ----

from dskit.pipeline.libs.pair_spread import PairSpreadHorizonRows  # noqa: E402

DAYS = ["2020-01-02", "2020-01-03", "2020-01-06", "2020-01-07", "2020-01-08", "2020-01-09"]
SPREADS = [1.0, 2.0, 4.0, 7.0, 11.0, 16.0]


def _ms(day):
    from datetime import date, datetime, timezone

    d = date.fromisoformat(day)
    return int(datetime(d.year, d.month, d.day, tzinfo=timezone.utc).timestamp() * 1000)


def spread_rows(pair=("A", "B"), days=DAYS, spreads=SPREADS):
    return [
        {"date": day, "symbol_a": pair[0], "symbol_b": pair[1], "spread": s, "mean": s - 0.5,
         "std": 1.0, "z": 0.5, "status": "ok", "signal": "flat", "n_history": 60}
        for day, s in zip(days, spreads)
    ]


HORIZON = {"horizons": [1, 2], "change_lags": [1], "edges": {"precision": {"weight_field": "weight"}}}


def horizon(rows, edges=(), **over):
    node = PairSpreadHorizonRows("h", {**HORIZON, **over})
    return node.run(NodeContext(name="t", asof="2025-12-31", run_dir="/nonexistent"),
                    {"spreads": rows, "precision": list(edges)})


def test_labels_count_panel_sessions_and_are_known_at_the_later_session():
    out = horizon(spread_rows())
    r = out["records"]
    assert len(r) == len(DAYS)
    assert r[0]["decision_ms"] == _ms(DAYS[0])
    assert r[0]["y_1"] == pytest.approx(1.0) and r[0]["known_ms_1"] == _ms(DAYS[1])
    assert r[0]["y_2"] == pytest.approx(3.0) and r[0]["known_ms_2"] == _ms(DAYS[2])
    assert r[2]["y_2"] == pytest.approx(11.0 - 4.0) and r[2]["known_ms_2"] == _ms(DAYS[4])
    assert r[-1]["y_1"] is None and r[-1]["known_ms_1"] is None
    assert r[-2]["y_1"] == pytest.approx(5.0) and r[-2]["y_2"] is None
    assert r[0]["spread_dev"] == pytest.approx(0.5) and r[0]["z"] == pytest.approx(0.5)
    assert r[0]["spread_change_1"] is None
    assert r[1]["spread_change_1"] == pytest.approx(1.0)
    assert r[0]["pair"] == "A|B" and r[0]["symbol_a"] == "A" and r[0]["symbol_b"] == "B"
    assert out["summary"]["rows"] == len(DAYS) and out["summary"]["pairs"] == 1


def test_edge_weight_is_the_pairs_absolute_weight_on_that_date_else_zero():
    edges = [{"date": DAYS[1], "symbol_a": "A", "symbol_b": "B", "weight": 0.7,
              "partial_correlation": -0.7, "fit_date": DAYS[0], "trained_through": "2019-12-31"},
             {"date": DAYS[2], "symbol_a": "B", "symbol_b": "A", "weight": -0.2}]
    r = horizon(spread_rows(), edges)["records"]
    assert r[0]["precision_weight"] == 0.0 and r[0]["z_x_precision_weight"] == 0.0
    assert r[1]["precision_weight"] == pytest.approx(0.7)
    assert r[1]["z_x_precision_weight"] == pytest.approx(0.5 * 0.7)
    assert r[2]["precision_weight"] == pytest.approx(0.2)  # symbols in either order, absolute weight
    assert r[3]["precision_weight"] == 0.0


def test_a_non_ok_spread_carries_none_features_and_breaks_labels_through_it():
    rows = spread_rows()
    rows[2]["status"] = "incomplete_window"
    rows[2]["z"] = rows[2]["mean"] = rows[2]["std"] = None
    r = horizon(rows)["records"]
    assert r[2]["z"] is None and r[2]["spread_dev"] is None and r[2]["spread_change_1"] is None
    assert r[2]["y_1"] is None and r[2]["known_ms_1"] is None  # no spread at t
    assert r[1]["y_1"] is None  # the later session has no spread
    assert r[1]["y_2"] == pytest.approx(7.0 - 2.0)  # two sessions on is fine
    assert r[3]["spread_change_1"] is None  # the prior session has no spread


def test_pairs_are_independent_series_and_duplicates_or_future_edges_refuse():
    rows = spread_rows() + spread_rows(pair=("A", "C"), spreads=[s * 10 for s in SPREADS])
    r = horizon(rows)["records"]
    assert [x["pair"] for x in r] == ["A|B"] * 6 + ["A|C"] * 6
    assert r[6]["y_1"] == pytest.approx(10.0)
    with pytest.raises(ValueError, match="duplicate"):
        horizon(rows + spread_rows()[:1])
    with pytest.raises(ValueError, match="trained_through"):
        horizon(spread_rows(), [{"date": DAYS[1], "symbol_a": "A", "symbol_b": "B", "weight": 0.1,
                                 "trained_through": DAYS[1]}])
    with pytest.raises(ValueError, match="duplicate edge"):
        horizon(spread_rows(), [{"date": DAYS[1], "symbol_a": "A", "symbol_b": "B", "weight": 0.1},
                                {"date": DAYS[1], "symbol_a": "B", "symbol_b": "A", "weight": 0.1}])


def test_label_offset_scores_the_change_after_the_first_executable_session():
    r = horizon(spread_rows(), label_offset=1)["records"]
    # decision at DAYS[0]: the label is spread(DAYS[1+h]) - spread(DAYS[1]), known at DAYS[1+h]
    assert r[0]["y_1"] == pytest.approx(4.0 - 2.0) and r[0]["known_ms_1"] == _ms(DAYS[2])
    assert r[0]["y_2"] == pytest.approx(7.0 - 2.0) and r[0]["known_ms_2"] == _ms(DAYS[3])
    assert r[-2]["y_1"] is None and r[-1]["y_1"] is None  # the base or the end is past the panel
    assert r[0]["z"] == pytest.approx(0.5) and r[0]["spread_dev"] == pytest.approx(0.5)  # features unchanged
    rows = spread_rows()
    rows[1]["status"] = "incomplete_window"
    assert horizon(rows, label_offset=1)["records"][0]["y_1"] is None  # the base session has no spread


def test_horizon_rows_default_deny_and_knob_checks():
    good = PairSpreadHorizonRows.validate_params(HORIZON)
    assert good == []
    for bad in ({**HORIZON, "extra": 1}, {**HORIZON, "horizons": []}, {**HORIZON, "horizons": [0]},
                {**HORIZON, "label_offset": -1}, {**HORIZON, "label_offset": 1.5},
                {**HORIZON, "horizons": [1, 1]}, {**HORIZON, "change_lags": [-1]},
                {**HORIZON, "edges": {"": {"weight_field": "w"}}},
                {**HORIZON, "edges": {"precision": {}}}, {**HORIZON, "edges": []},
                {k: v for k, v in HORIZON.items() if k != "horizons"}):
        assert PairSpreadHorizonRows.validate_params(bad), bad
    assert PairSpreadHorizonRows.validate_params({"horizons": [5]}) == []
    assert PairSpreadHorizonRows.role == "transform"
    assert PairSpreadHorizonRows.outputs == ("records", "summary")
    assert "PairSpreadHorizonRows" in __import__("dskit.pipeline.libs.pair_spread", fromlist=["x"]).__all__
    with pytest.raises(ValueError, match="precision"):
        PairSpreadHorizonRows("h", HORIZON).run(None, {"spreads": spread_rows()})


# -- PairPanelFeatures: pair differences, legs, prior-only Z/std at lookbacks, changes (ADR-0273) ----

from dskit.pipeline.libs.pair_spread import PairPanelFeatures  # noqa: E402

PANEL = {"symbols": ["A", "B"], "fields": {"iv": "atm_iv"}, "lookbacks": [2], "leg_lookbacks": [2],
         "change_lags": [1]}


def panel(values, symbol_values=None):
    out = []
    for day, av, bv in values:
        out.append({"date": day, "symbol": "A", "atm_iv": av, "status": "ok", "target_dte": 30})
        out.append({"date": day, "symbol": "B", "atm_iv": bv, "status": "ok", "target_dte": 30})
    return out


def features(rows, **over):
    node = PairPanelFeatures("pf", {**PANEL, **over})
    return node.run(NodeContext(name="t", asof="2025-12-31", run_dir="/nonexistent"), {"panel": rows})


def test_pair_panel_differences_legs_and_prior_only_statistics():
    rows = panel([("2020-01-02", 0.30, 0.20), ("2020-01-03", 0.40, 0.20), ("2020-01-06", 0.60, 0.20),
                  ("2020-01-07", 0.10, 0.20)])
    out = features(rows)
    r = out["records"]
    assert [x["pair"] for x in r] == ["A|B"] * 4 and r[0]["symbol_a"] == "A"
    assert r[2]["d_iv"] == pytest.approx(0.40) and r[2]["a_iv"] == pytest.approx(0.60) and r[2]["b_iv"] == pytest.approx(0.20)
    # prior-only window of 2: mean of (0.10, 0.20) = 0.15, sample sd = 0.0707
    assert r[2]["z2_iv"] == pytest.approx((0.40 - 0.15) / 0.07071067811865477)
    assert r[2]["sd2_iv"] == pytest.approx(0.07071067811865477)
    assert r[1]["z2_iv"] is None and r[1]["sd2_iv"] is None  # only one prior session
    assert r[2]["za2_iv"] == pytest.approx((0.60 - 0.35) / 0.07071067811865477)
    assert r[2]["zb2_iv"] is None and r[2]["sdb2_iv"] == pytest.approx(0.0)  # B is constant: zero scale, no Z
    assert r[2]["sda2_iv"] == pytest.approx(0.07071067811865477)
    assert r[1]["chg1_iv"] == pytest.approx(0.10) and r[0]["chg1_iv"] is None
    assert out["summary"]["rows"] == 4 and out["summary"]["pairs"] == 1
    assert "A|B" in out["summary"]["per_pair"]


def test_pair_panel_status_gate_target_filter_and_gaps_are_not_bridged():
    rows = panel([("2020-01-02", 0.30, 0.20), ("2020-01-03", 0.40, 0.20), ("2020-01-06", 0.60, 0.20),
                  ("2020-01-07", 0.10, 0.20)])
    rows[3]["status"] = "no_rate"                     # B on Jan 3 unusable
    rows.append({"date": "2020-01-08", "symbol": "A", "atm_iv": 0.5, "status": "ok", "target_dte": 90})
    r = features(rows, status_field="status", ok_value="ok", target_field="target_dte", target_value=30)["records"]
    assert len(r) == 4 and r[1]["d_iv"] is None and r[1]["b_iv"] is None and r[1]["a_iv"] == pytest.approx(0.40)
    assert r[2]["z2_iv"] is None   # the prior window has a hole: refused, not shrunk
    assert r[2]["chg1_iv"] is None and r[3]["chg1_iv"] == pytest.approx((0.10 - 0.20) - 0.40)
    with pytest.raises(ValueError, match="duplicate"):
        features(rows + rows[:1])


def test_pair_panel_three_symbols_make_three_pairs_and_missing_symbol_days_are_absent():
    rows = panel([("2020-01-02", 0.3, 0.2), ("2020-01-03", 0.4, 0.2)])
    rows.append({"date": "2020-01-03", "symbol": "C", "atm_iv": 0.9, "status": "ok", "target_dte": 30})
    r = features(rows, symbols=["A", "B", "C"])["records"]
    assert [(x["date"], x["pair"]) for x in r] == [("2020-01-02", "A|B"), ("2020-01-03", "A|B"),
                                                   ("2020-01-03", "A|C"), ("2020-01-03", "B|C")]
    assert r[2]["d_iv"] == pytest.approx(0.4 - 0.9)


def test_pair_panel_log_transform_differences_in_log_space_and_refuses_non_positive():
    rows = panel([("2020-01-02", 2.0, 0.5), ("2020-01-03", 0.0, 4.0)])
    r = features(rows, fields={"lr": {"field": "atm_iv", "transform": "log"}}, lookbacks=[], leg_lookbacks=[],
                 change_lags=[])["records"]
    import math
    assert r[0]["d_lr"] == pytest.approx(math.log(2.0) - math.log(0.5))
    assert r[0]["a_lr"] == pytest.approx(math.log(2.0))
    assert r[1]["a_lr"] is None and r[1]["d_lr"] is None and r[1]["b_lr"] == pytest.approx(math.log(4.0))


def test_pair_panel_default_denies_and_checks_knobs():
    assert PairPanelFeatures.validate_params(PANEL) == []
    assert PairPanelFeatures.validate_params({"symbols": ["A", "B"], "fields": {"x": "y"}}) == []
    assert PairPanelFeatures.validate_params({"symbols": ["A", "B"], "fields": {"x": {"field": "y", "transform": "log"}}}) == []
    for bad in ({**PANEL, "extra": 1}, {**PANEL, "symbols": ["A"]}, {**PANEL, "fields": {}},
                {**PANEL, "fields": {"a b": "x"}}, {**PANEL, "lookbacks": [1]}, {**PANEL, "leg_lookbacks": [0]},
                {**PANEL, "fields": {"x": {"field": "atm_iv", "transform": "exp"}}},
                {**PANEL, "fields": {"x": {"field": "atm_iv"}}},
                {**PANEL, "change_lags": [1, 1]}, {**PANEL, "target_value": 30},
                {**PANEL, "ok_value": "ok"}, {k: v for k, v in PANEL.items() if k != "symbols"}):
        assert PairPanelFeatures.validate_params(bad), bad
    assert PairPanelFeatures.role == "transform" and PairPanelFeatures.outputs == ("records", "summary")
    assert "PairPanelFeatures" in __import__("dskit.pipeline.libs.pair_spread", fromlist=["x"]).__all__
