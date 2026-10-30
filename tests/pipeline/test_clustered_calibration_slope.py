"""Clustered calibration slope: one- and two-way cluster-robust bands.

The expected standard errors are restated independently in matrix form
(``(X'X)^-1 X' u u' X (X'X)^-1`` summed within clusters, CR1 factor
``G/(G-1) * (n-1)/(n-2)``, two-way = A + B - A∩B), never read from the
module under test.
"""

import math

import numpy as np
import pytest

from dskit.pipeline.kinds_stats import ClusteredCalibrationSlope
from dskit.pipeline.ordering import calibration_slope, clustered_calibration_slope
from dskit.pipeline.stats import student_t_critical, student_t_sf


def _cr1_var(x, y, labels):
    """Independent CR1 cluster-robust variance of the slope (matrix form)."""
    x = np.asarray(x, float)
    y = np.asarray(y, float)
    n = len(x)
    design = np.column_stack([np.ones(n), x])
    bread = np.linalg.inv(design.T @ design)
    beta = bread @ design.T @ y
    resid = y - design @ beta
    meat = np.zeros((2, 2))
    groups = sorted(set(labels))
    for g in groups:
        idx = [i for i, lab in enumerate(labels) if lab == g]
        s = design[idx].T @ resid[idx]
        meat += np.outer(s, s)
    big_g = len(groups)
    factor = big_g / (big_g - 1) * (n - 1) / (n - 2)
    return float((factor * bread @ meat @ bread)[1, 1])


def _data():
    rng = np.random.default_rng(7)
    n = 60
    dates = [f"d{i % 6}" for i in range(n)]
    names = [f"s{(i * 7) % 5}" for i in range(n)]
    shock = {d: rng.normal() for d in set(dates)}
    x = rng.normal(0, 2, n)
    y = 0.4 * x + np.array([shock[d] for d in dates]) + rng.normal(0, 1, n)
    return list(x), list(y), dates, names


class TestStudentTCritical:
    def test_inverts_the_tail(self):
        for df in (1, 4, 12, 200):
            c = student_t_critical(0.95, df)
            assert math.isclose(student_t_sf(c, df), 0.025, rel_tol=1e-9)

    def test_known_values(self):
        assert math.isclose(student_t_critical(0.95, 12), 2.1788128296634, rel_tol=1e-9)

    @pytest.mark.parametrize("level", [0.0, 1.0, -0.1, 1.5, float("nan")])
    def test_refuses_a_bad_level(self, level):
        with pytest.raises(ValueError):
            student_t_critical(level, 5)

    @pytest.mark.parametrize("df", [0, -1, float("nan")])
    def test_refuses_a_bad_df(self, df):
        with pytest.raises(ValueError):
            student_t_critical(0.95, df)

    def test_mean_interval_uses_the_same_owner(self):
        from dskit.pipeline import mean_interval

        assert mean_interval._student_t_critical is student_t_critical


class TestClusteredCalibrationSlope:
    def test_point_estimate_matches_the_unclustered_owner(self):
        x, y, dates, _ = _data()
        out = clustered_calibration_slope(y, x, [dates])
        ref = calibration_slope(y, x)
        assert math.isclose(out["slope"], ref["slope"], rel_tol=1e-12)
        assert math.isclose(out["intercept"], ref["intercept"], rel_tol=1e-12)
        assert math.isclose(out["pearson_r"], ref["pearson_r"], rel_tol=1e-12)

    def test_one_way_matches_an_independent_sandwich(self):
        x, y, dates, _ = _data()
        out = clustered_calibration_slope(y, x, [dates])
        assert math.isclose(out["slope_se"], math.sqrt(_cr1_var(x, y, dates)), rel_tol=1e-9)
        assert out["variance_rule"] == "one_way"
        assert out["n_clusters"] == [6]
        assert out["df"] == 5

    def test_two_way_is_a_plus_b_minus_intersection(self):
        x, y, dates, names = _data()
        pairs = [f"{d}|{s}" for d, s in zip(dates, names)]
        want = _cr1_var(x, y, dates) + _cr1_var(x, y, names) - _cr1_var(x, y, pairs)
        assert want > 0
        out = clustered_calibration_slope(y, x, [dates, names])
        assert out["variance_rule"] == "two_way"
        assert math.isclose(out["slope_se"], math.sqrt(want), rel_tol=1e-9)
        assert math.isclose(out["se_by_dimension"][0], math.sqrt(_cr1_var(x, y, dates)), rel_tol=1e-9)
        assert math.isclose(out["se_by_dimension"][1], math.sqrt(_cr1_var(x, y, names)), rel_tol=1e-9)
        assert out["n_clusters"] == [6, 5]
        assert out["df"] == 4

    def test_band_is_t_with_fewest_clusters_minus_one(self):
        x, y, dates, names = _data()
        out = clustered_calibration_slope(y, x, [dates, names], level=0.9)
        crit = student_t_critical(0.9, 4)
        assert math.isclose(out["ci_low"], out["slope"] - crit * out["slope_se"], rel_tol=1e-12)
        assert math.isclose(out["ci_high"], out["slope"] + crit * out["slope_se"], rel_tol=1e-12)
        t0 = out["slope"] / out["slope_se"]
        assert math.isclose(out["t_vs_0"], t0, rel_tol=1e-12)
        assert math.isclose(out["t_vs_1"], (out["slope"] - 1) / out["slope_se"], rel_tol=1e-12)
        assert math.isclose(out["p_vs_0"], 2 * student_t_sf(abs(t0), 4), rel_tol=1e-12)
        assert out["level"] == 0.9

    def test_negative_two_way_variance_falls_back_to_the_wider_one_way(self):
        # Clusters chosen so the intersection term dominates: every pair is its
        # own (date, name) cell and the one-way sums cancel.
        x = [1.0, 2, 3, 4, 5, 6, 7, 8]
        y = [1.0, -1, -1, 1, 1, -1, -1, 1.5]
        a = ["a", "a", "b", "b", "a", "a", "b", "b"]
        b = ["p", "q", "p", "q", "q", "p", "q", "p"]
        pairs = [f"{u}|{v}" for u, v in zip(a, b)]
        two = _cr1_var(x, y, a) + _cr1_var(x, y, b) - _cr1_var(x, y, pairs)
        assert two <= 0, "fixture must produce a non-positive two-way variance"
        out = clustered_calibration_slope(y, x, [a, b])
        assert out["variance_rule"] == "max_one_way"
        wider = math.sqrt(max(_cr1_var(x, y, a), _cr1_var(x, y, b)))
        assert math.isclose(out["slope_se"], wider, rel_tol=1e-9)

    def test_perfect_fit_withholds_tests(self):
        out = clustered_calibration_slope([2.0, 4, 6, 8], [1.0, 2, 3, 4], [["a", "a", "b", "b"]])
        assert math.isclose(out["slope"], 2.0)
        assert out["t_vs_0"] is None and out["ci_low"] is None and out["p_vs_0"] is None

    @pytest.mark.parametrize("clusters", [[], [["a"] * 4] * 3, [["a", "b", "a"]], [["a", "a", "a", "a"]],
                                          [["a", "b", "a", ""]], [["a", "b", "a", None]], "ab"])
    def test_refuses_bad_clusters(self, clusters):
        with pytest.raises(ValueError):
            clustered_calibration_slope([1.0, 2, 3, 5], [1.0, 2, 4, 3], clusters)

    def test_refuses_constant_forecast_and_short_input(self):
        with pytest.raises(ValueError):
            clustered_calibration_slope([1.0, 2, 3], [1.0, 1, 1], [["a", "b", "a"]])
        with pytest.raises(ValueError):
            clustered_calibration_slope([1.0, 2], [1.0, 2], [["a", "b"]])
        with pytest.raises(ValueError):
            clustered_calibration_slope([1.0, 2, float("nan")], [1.0, 2, 3], [["a", "b", "a"]])

    @pytest.mark.parametrize("level", [0, 1, 1.2, True, "0.95"])
    def test_refuses_bad_level(self, level):
        with pytest.raises(ValueError):
            clustered_calibration_slope([1.0, 2, 3, 5], [1.0, 2, 4, 3], [["a", "b", "a", "b"]], level=level)


def _rows():
    x, y, dates, names = _data()
    return [{"g": "even" if i % 2 == 0 else "odd", "f": fx, "o": fy, "d": d, "s": s}
            for i, (fx, fy, d, s) in enumerate(zip(x, y, dates, names))]


class TestClusteredCalibrationSlopeNode:
    PARAMS = {"forecast_field": "f", "outcome_field": "o", "cluster_fields": ["d", "s"]}

    def test_one_row_without_groups_matches_the_function(self):
        rows = _rows()
        out = ClusteredCalibrationSlope("n", dict(self.PARAMS)).run(None, {"records": rows})
        assert len(out["records"]) == 1
        row = out["records"][0]
        ref = clustered_calibration_slope([r["o"] for r in rows], [r["f"] for r in rows],
                                          [[r["d"] for r in rows], [r["s"] for r in rows]])
        assert row["slope"] == ref["slope"] and row["slope_se"] == ref["slope_se"]
        assert row["se_d"] == ref["se_by_dimension"][0] and row["n_clusters_s"] == ref["n_clusters"][1]
        assert row["n"] == 60 and row["skipped"] is None

    def test_groups_are_sorted_and_each_scored_alone(self):
        rows = _rows()
        out = ClusteredCalibrationSlope("n", {**self.PARAMS, "group_fields": ["g"]}).run(None, {"records": rows})
        assert [r["g"] for r in out["records"]] == ["even", "odd"]
        even = [r for r in rows if r["g"] == "even"]
        ref = clustered_calibration_slope([r["o"] for r in even], [r["f"] for r in even],
                                          [[r["d"] for r in even], [r["s"] for r in even]])
        assert out["records"][0]["slope_se"] == ref["slope_se"]
        assert out["metrics"] == {"rows_in": 60, "groups": 2, "skipped": 0}

    def test_a_group_too_thin_to_cluster_is_reported_not_raised(self):
        rows = _rows()[:6] + [{"g": "lone", "f": 1.0, "o": 2.0, "d": "x", "s": "y"},
                              {"g": "lone", "f": 2.0, "o": 1.0, "d": "x", "s": "z"},
                              {"g": "lone", "f": 3.0, "o": 5.0, "d": "x", "s": "z"}]
        out = ClusteredCalibrationSlope("n", {**self.PARAMS, "group_fields": ["g"]}).run(None, {"records": rows})
        lone = [r for r in out["records"] if r["g"] == "lone"][0]
        assert lone["slope"] is None and "cluster" in lone["skipped"]
        assert lone["n"] == 3
        assert out["metrics"]["skipped"] == 1

    @pytest.mark.parametrize("bad", [{"f": None}, {"o": float("nan")}, {"o": "1"}, {"d": ""}, {"s": None}])
    def test_a_bad_cell_refuses_by_row(self, bad):
        rows = _rows()
        rows[3] = {**rows[3], **bad}
        with pytest.raises(ValueError, match="row 3"):
            ClusteredCalibrationSlope("n", dict(self.PARAMS)).run(None, {"records": rows})

    def test_a_missing_field_refuses_by_row(self):
        rows = _rows()
        del rows[5]["d"]
        with pytest.raises(ValueError, match="row 5"):
            ClusteredCalibrationSlope("n", dict(self.PARAMS)).run(None, {"records": rows})

    @pytest.mark.parametrize("params", [
        {},
        {"forecast_field": "f", "outcome_field": "o"},
        {"forecast_field": "f", "outcome_field": "o", "cluster_fields": []},
        {"forecast_field": "f", "outcome_field": "o", "cluster_fields": ["d", "s", "g"]},
        {"forecast_field": "f", "outcome_field": "o", "cluster_fields": ["d", "d"]},
        {"forecast_field": "f", "outcome_field": "o", "cluster_fields": "d"},
        {"forecast_field": "", "outcome_field": "o", "cluster_fields": ["d"]},
        {"forecast_field": "f", "outcome_field": "o", "cluster_fields": ["d"], "level": 1.0},
        {"forecast_field": "f", "outcome_field": "o", "cluster_fields": ["d"], "group_fields": "g"},
        {"forecast_field": "f", "outcome_field": "o", "cluster_fields": ["d"], "group_fields": ["d"]},
        {"forecast_field": "f", "outcome_field": "o", "cluster_fields": ["d"], "typo": 1},
    ])
    def test_validate_params_refuses(self, params):
        assert ClusteredCalibrationSlope.validate_params(params)

    def test_validate_params_accepts(self):
        assert ClusteredCalibrationSlope.validate_params(
            {**self.PARAMS, "group_fields": ["g"], "level": 0.9}) == []

    def test_the_default_level_is_the_interval_policy(self):
        from dskit.pipeline.mean_interval import DEFAULT_LEVEL

        out = ClusteredCalibrationSlope("n", dict(self.PARAMS)).run(None, {"records": _rows()})
        assert out["records"][0]["level"] == DEFAULT_LEVEL
