"""Focused rolling precision graph contract tests."""

import math
from datetime import date, timedelta

import pytest

pytest.importorskip("sklearn")
pytest.importorskip("networkx")

from dskit.pipeline.libs.graphical_lasso import RollingPrecisionGraph


def _params(**extra):
    params = {
        "date_field": "day", "symbol_field": "ticker", "return_field": "ret",
        "factor_field": "qqq_ret", "symbols": ["AAA", "BBB", "CCC"],
        "window": 40, "min_observations": 40, "cv_splits": 3,
        "max_iter": 200, "tol": 0.01, "edge_min_weight": 0.01,
        "refit_frequency": "yearly", "fit_dates": ["2020-02-10"],
        "decision_dates": ["2020-02-10"],
    }
    params.update(extra)
    return params


def _rows(n=45):
    rows = []
    for i in range(n):
        day = (date(2020, 1, 1) + timedelta(days=i)).isoformat()
        factor = 0.01 * math.sin(i * 0.91)
        common = 0.008 * math.sin(i * 1.37)
        values = {
            "AAA": 1.2 * factor + common + 0.002 * math.cos(i * 2.11),
            "BBB": 0.8 * factor + common + 0.002 * math.sin(i * 2.39),
            "CCC": 1.5 * factor + 0.5 * common + 0.004 * math.cos(i * 1.71),
        }
        for ticker, ret in values.items():
            rows.append({"day": day, "ticker": ticker, "ret": ret, "qqq_ret": factor})
    return rows


def test_lagged_fit_is_immune_to_same_date_return_change():
    node = RollingPrecisionGraph("graph", _params())
    rows = _rows()
    first = node.run(None, {"records": rows})
    assert first["fits"][0]["status"] == "ok"
    assert first["edges"]
    assert all(row["symbol_a"] < row["symbol_b"] for row in first["edges"])
    changed = [dict(row) for row in rows]
    for row in changed:
        if row["day"] >= "2020-02-10":
            row["ret"] *= -100
    second = node.run(None, {"records": changed})
    assert second == first


def test_insufficient_window_emits_status_without_edges():
    node = RollingPrecisionGraph("graph", _params(min_observations=40, decision_dates=["2020-01-20"], fit_dates=["2020-01-20"]))
    out = node.run(None, {"records": _rows()})
    assert out["edges"] == []
    assert out["fits"][0]["status"] == "insufficient_history"


def test_unknown_parameter_refused():
    assert RollingPrecisionGraph.validate_params(_params(unknown=True))


def test_yearly_graph_carries_with_fit_provenance():
    node = RollingPrecisionGraph("graph", _params(decision_dates=["2020-02-10", "2020-02-20"]))
    out = node.run(None, {"records": _rows()})
    first, second = out["fits"]
    assert first["refit"] is True
    assert second["refit"] is False
    assert first["fit_date"] == second["fit_date"] == "2020-02-10"
    assert first["trained_through"] == "2020-02-09"
    assert first["alpha"] == second["alpha"]
    assert first["train_log_likelihood"] is not None
    assert first["cv_score"] is not None
    assert first["n_iter"] is not None
    assert {r["date"] for r in out["edges"]} == {"2020-02-10", "2020-02-20"}


def test_fit_dates_anchor_calendar_and_reject_same_year_double_fit():
    assert RollingPrecisionGraph.validate_params(_params(fit_dates=["2020-01-01", "2020-07-01"]))
    node = RollingPrecisionGraph("graph", _params(
        fit_dates=["2020-02-10"], decision_dates=["2020-02-09", "2020-02-20"]))
    out = node.run(None, {"records": _rows()})
    assert out["fits"][0]["status"] == "not_fitted"
    assert out["fits"][1]["fit_date"] == "2020-02-10"
    assert out["fits"][1]["refit"] is True


def test_next_year_refit_uses_only_previous_year_rows():
    params = _params(decision_dates=["2020-02-10", "2021-01-01"],
                     fit_dates=["2020-02-10", "2021-01-01"])
    node = RollingPrecisionGraph("graph", params)
    first = node.run(None, {"records": _rows(n=370)})
    changed = [dict(row) for row in _rows(n=370)]
    for row in changed:
        if row["day"] >= "2021-01-01":
            row["ret"] *= 99
    second = node.run(None, {"records": changed})
    assert first == second
    assert first["fits"][1]["refit"] is True
    assert first["fits"][1]["fit_date"] == "2021-01-01"
    assert first["fits"][1]["trained_through"] == "2020-12-31"
