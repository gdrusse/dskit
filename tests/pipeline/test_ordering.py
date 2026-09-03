"""ADR-0068: the ordering answer and the size answer, measured apart.

Three things are pinned here. The ARITHMETIC — every statistic against a
hand-worked example small enough to check on paper. The DISTINCTION —
a forecast carrying only a market-wide move scores high on the pooled
(name, time) correlation and zero on the per-timestamp one, which is the
whole reason the two are reported under different names. And the
HONESTY GUARD — a three-name cross-section comes back marked unusable
with its name counts attached, and can never pass, however good its
numbers look.
"""

import math
import os
import random

import pytest

from dskit.pipeline.ordering import (
    MIN_CROSS_SECTION_NAMES,
    USABLE_NAMES,
    calibration_across_folds,
    calibration_slope,
    cross_section_by_stamp,
    demean_by_series,
    ic_from_rho,
    ordering_verdict,
    pearson,
    per_timestamp_ic,
    pooled_name_time_ic,
    spearman,
)


def _panel(n_stamps, names, rng, shared=0.0, private=1.0, skill=0.0):
    """A synthetic panel: a shared move plus a per-name move, part predicted."""
    stamps, series, y, yhat = [], [], [], []
    for t in range(n_stamps):
        common = rng.gauss(0.0, 1.0)
        for name in names:
            own = rng.gauss(0.0, 1.0)
            stamps.append(t)
            series.append(name)
            y.append(shared * common + private * own)
            yhat.append(shared * common + skill * own + 0.01 * rng.gauss(0, 1))
    return stamps, series, y, yhat


class TestTheRankArithmetic:
    def test_spearman_matches_the_hand_worked_value(self):
        # Five names, one adjacent pair swapped: sum d^2 = 2, so
        # rho = 1 - 6*2 / (5 * 24) = 0.9.
        assert spearman(
            [1.0, 2.0, 3.0, 4.0, 5.0], [1.0, 2.0, 3.0, 5.0, 4.0]
        ) == pytest.approx(0.9)

    def test_ties_share_their_average_rank(self):
        # A forecast that cannot separate two names gets no credit for an
        # order it never expressed.
        assert spearman([1.0, 1.0, 2.0], [1.0, 2.0, 3.0]) == pytest.approx(
            0.8660254037844387
        )

    def test_a_constant_side_is_undefined_not_zero(self):
        assert spearman([1.0, 1.0, 1.0], [1.0, 2.0, 3.0]) is None
        assert pearson([1.0, 1.0], [1.0, 2.0]) is None

    def test_pearson_matches_the_hand_worked_value(self):
        assert pearson([1.0, 2.0, 3.0], [2.0, 4.0, 6.0]) == pytest.approx(1.0)

    def test_a_short_or_ragged_input_is_refused(self):
        with pytest.raises(ValueError, match="must agree"):
            spearman([1.0, 2.0], [1.0])
        with pytest.raises(ValueError, match="at least 2 pairs"):
            spearman([1.0], [1.0])


class TestTheCalibrationSlope:
    #: y = [1, 3, 2, 4] on yhat = [1, 2, 3, 4]: Sxx = 5, Sxy = 4, so
    #: b = 0.8 and a = 2.5 - 0.8*2.5 = 0.5. Residuals are
    #: [-0.3, 0.9, -0.9, 0.3], the score series (x - xbar)*e is
    #: [0.45, -0.45, -0.45, 0.45], whose lag-0 long-run variance is
    #: 0.2025, so se(u) = 0.225 and se(b) = n*se(u)/Sxx = 0.18.
    Y = [1.0, 3.0, 2.0, 4.0]
    YHAT = [1.0, 2.0, 3.0, 4.0]

    def test_the_slope_and_intercept_are_the_hand_worked_ones(self):
        out = calibration_slope(self.Y, self.YHAT, lags=0)
        assert out["slope"] == pytest.approx(0.8)
        assert out["intercept"] == pytest.approx(0.5)
        assert out["pearson_r"] == pytest.approx(0.8)
        assert out["n"] == 4

    def test_the_error_bar_is_the_hand_worked_one(self):
        out = calibration_slope(self.Y, self.YHAT, lags=0)
        assert out["slope_se"] == pytest.approx(0.18)
        assert out["t_vs_0"] == pytest.approx(0.8 / 0.18)
        assert out["t_vs_1"] == pytest.approx(-0.2 / 0.18)

    def test_a_perfect_forecast_has_slope_one_and_no_error_bar(self):
        out = calibration_slope([1.0, 2.0, 3.0, 4.0], [1.0, 2.0, 3.0, 4.0], lags=0)
        assert out["slope"] == pytest.approx(1.0)
        assert out["slope_se"] == pytest.approx(0.0)
        assert out["t_vs_0"] is None

    def test_the_overlap_band_widens_with_the_horizon(self):
        long_y = [math.sin(i) for i in range(60)]
        long_f = [0.5 * v for v in long_y]
        assert (
            calibration_slope(long_y, long_f, h_steps=12)["lags"]
            > calibration_slope(long_y, long_f, h_steps=1, lags=0)["lags"]
        )

    def test_a_constant_forecast_is_refused_not_scored_as_zero(self):
        with pytest.raises(ValueError, match="constant"):
            calibration_slope([1.0, 2.0, 3.0], [2.0, 2.0, 2.0])

    def test_the_across_fold_summary_is_the_hand_worked_one(self):
        out = calibration_across_folds([0.2, 0.4, 0.6, 0.8])
        assert out["slope_mean"] == pytest.approx(0.5)
        assert out["slope_se"] == pytest.approx(0.12909944487358055)
        assert out["t_vs_0"] == pytest.approx(3.872983346207417)
        assert out["t_vs_1"] == pytest.approx(-3.872983346207417)
        assert out["frac_positive"] == 1.0
        assert out["df"] == 3


class TestTheTwoNumbersAreDifferentNumbers:
    def test_a_market_wide_move_scores_pooled_and_nothing_per_instant(self):
        # Every name moves together and the forecast knows only that.
        # Pooling (name, time) rows reads it as skill; ranking WITHIN an
        # instant — which is all a selector can act on — reads nothing.
        rng = random.Random(5)
        stamps, series, y, yhat = _panel(
            300, list("ABCDE"), rng, shared=1.0, private=0.05, skill=0.0
        )
        pooled = pooled_name_time_ic(y, yhat)["ic"]
        instant = per_timestamp_ic(stamps, series, y, yhat, lags=0)
        assert pooled > 0.9
        assert abs(instant["ic"]) < 0.2
        assert instant["ic_t"] < 3.0

    def test_per_name_skill_scores_on_both(self):
        rng = random.Random(6)
        stamps, series, y, yhat = _panel(
            300, list("ABCDE"), rng, shared=0.0, private=1.0, skill=1.0
        )
        assert pooled_name_time_ic(y, yhat)["ic"] > 0.9
        assert per_timestamp_ic(stamps, series, y, yhat, lags=0)["ic"] > 0.9

    def test_the_pooled_number_says_which_kind_it_is(self):
        assert pooled_name_time_ic([1.0, 2.0], [1.0, 2.0])["kind"] == (
            "pooled_name_time"
        )
        assert per_timestamp_ic([1, 1], ["A", "B"], [1.0, 2.0], [1.0, 2.0])[
            "kind"
        ] == "per_timestamp_cross_section"


class TestTheCrossSection:
    def test_each_instant_yields_one_correlation_and_its_name_count(self):
        out = cross_section_by_stamp(
            [1, 1, 1, 2, 2, 2],
            ["A", "B", "C"] * 2,
            [3.0, 2.0, 1.0, 1.0, 2.0, 3.0],
            [3.0, 2.0, 1.0, 3.0, 2.0, 1.0],
        )
        assert out["stamps"] == [1, 2]
        assert out["rho"] == pytest.approx([1.0, -1.0])
        assert out["n_names"] == [3, 3]

    def test_an_instant_too_thin_to_rank_is_skipped_but_still_counted(self):
        out = cross_section_by_stamp(
            [1, 1, 2, 2, 2],
            ["A", "B", "A", "B", "C"],
            [1.0, 2.0, 1.0, 2.0, 3.0],
            [1.0, 2.0, 1.0, 2.0, 3.0],
        )
        assert out["stamps"] == [2]
        assert out["n_skipped"] == 1
        # The thin instant is absent from the score and PRESENT in the
        # counts, which is what the usability guard reads.
        assert out["n_names_all"] == [2, 3]

    def test_demeaning_removes_each_name_s_own_level(self):
        assert demean_by_series(["A", "A", "B"], [1.0, 3.0, 5.0]) == pytest.approx(
            [-1.0, 1.0, 0.0]
        )

    def test_the_hac_test_of_the_rho_series_is_the_hand_worked_one(self):
        # rho = [0.9, 0.5, 0.7]: mean 0.7, lag-0 long-run variance
        # (0.04 + 0.04 + 0)/3 = 0.0266..., se = 0.0942809, t = 7.4246.
        out = ic_from_rho([0.9, 0.5, 0.7], [5, 5, 5], lags=0)
        assert out["ic"] == pytest.approx(0.7)
        assert out["ic_se"] == pytest.approx(0.09428090415820634)
        assert out["ic_t"] == pytest.approx(7.424621202458749)
        assert out["frac_pos"] == 1.0
        assert out["usable"] is True


class TestTheHonestyGuard:
    def test_three_names_are_marked_unusable_with_the_count_attached(self):
        rng = random.Random(7)
        stamps, series, y, yhat = _panel(
            200, list("ABC"), rng, shared=0.0, private=1.0, skill=1.0
        )
        out = per_timestamp_ic(stamps, series, y, yhat, lags=0)
        # A near-perfect forecast, and the measure still refuses to
        # present its number as evidence.
        assert out["ic"] > 0.9
        assert out["usable"] is False
        assert "3" in out["unusable_reason"]
        assert str(USABLE_NAMES) in out["unusable_reason"]
        assert out["n_names"] == {"min": 3, "median": 3, "max": 3}

    def test_five_names_are_usable(self):
        rng = random.Random(8)
        stamps, series, y, yhat = _panel(
            200, list("ABCDE"), rng, shared=0.0, private=1.0, skill=1.0
        )
        out = per_timestamp_ic(stamps, series, y, yhat, lags=0)
        assert out["usable"] is True
        assert out["unusable_reason"] == ""
        assert out["n_names"]["median"] == 5

    def test_an_unusable_panel_can_never_pass(self):
        thin = {
            "kind": "per_timestamp_cross_section", "ic": 0.99, "ic_t": 99.0,
            "ic_se": 0.01, "ic_p": 0.0, "n_stamps": 500, "frac_pos": 1.0,
            "n_names": {"min": 3, "median": 3, "max": 3}, "n_skipped": 0,
            "lags": 0, "h_steps": 1, "usable": False,
            "unusable_reason": "three names is not a cross-section",
        }
        verdict = ordering_verdict(dict(thin), dict(thin), fold_positive=20,
                                   n_folds=20)
        assert verdict["passes"] is False
        assert verdict["usable"] is False
        assert "three names" in verdict["reasons"][0]

    def test_two_names_cannot_even_be_ranked(self):
        assert MIN_CROSS_SECTION_NAMES == 3
        out = cross_section_by_stamp([1, 1], ["A", "B"], [1.0, 2.0], [1.0, 2.0])
        assert out["rho"] == []
        assert out["n_skipped"] == 1


class TestTheVerdict:
    def _usable(self, ic, t):
        return {
            "kind": "per_timestamp_cross_section", "ic": ic, "ic_t": t,
            "ic_se": 0.01, "ic_p": 0.0, "n_stamps": 500, "frac_pos": 0.6,
            "n_names": {"min": 5, "median": 5, "max": 5}, "n_skipped": 0,
            "lags": 0, "h_steps": 1, "usable": True, "unusable_reason": "",
        }

    def test_a_clean_ordering_result_passes(self):
        out = ordering_verdict(
            self._usable(0.05, 3.0), self._usable(0.04, 2.5),
            fold_positive=15, n_folds=20,
        )
        assert out["passes"] is True
        assert out["retained"] == pytest.approx(0.8)

    def test_a_standing_name_tilt_is_refused(self):
        out = ordering_verdict(
            self._usable(0.05, 3.0), self._usable(0.005, 0.3),
            fold_positive=15, n_folds=20,
        )
        assert out["passes"] is False
        assert any("standing per-name tilt" in r for r in out["reasons"])

    def test_folds_that_disagree_are_refused(self):
        out = ordering_verdict(
            self._usable(0.05, 3.0), self._usable(0.04, 2.5),
            fold_positive=9, n_folds=20,
        )
        assert any("9/20 folds" in r for r in out["reasons"])

    def test_a_non_result_is_refused_not_crashed_on(self):
        with pytest.raises(ValueError, match="per_timestamp_ic result"):
            ordering_verdict({}, {})


class TestTheNull:
    def test_pure_noise_rejects_at_about_the_nominal_rate(self):
        # No relationship at all between forecast and outcome: the
        # one-sided 5% test must reject about 5% of the time.
        rng = random.Random(99)
        trials, rejects = 200, 0
        for _ in range(trials):
            stamps, series, y, yhat = _panel(
                60, list("ABCDE"), rng, shared=0.0, private=1.0, skill=0.0
            )
            out = per_timestamp_ic(stamps, series, y, yhat, lags=0)
            if out["ic_t"] is not None and out["ic_t"] >= 1.645:
                rejects += 1
        assert rejects / trials <= 0.12

    def test_noise_never_passes_the_full_ordering_rule(self):
        rng = random.Random(101)
        passed = 0
        for _ in range(60):
            stamps, series, y, yhat = _panel(
                60, list("ABCDE"), rng, shared=0.0, private=1.0, skill=0.0
            )
            raw = per_timestamp_ic(stamps, series, y, yhat, lags=0)
            dm = per_timestamp_ic(
                stamps, series,
                demean_by_series(series, y), demean_by_series(series, yhat),
                lags=0,
            )
            verdict = ordering_verdict(raw, dm, fold_positive=10, n_folds=20)
            passed += bool(verdict["passes"])
        assert passed <= 3


pq = pytest.importorskip("pyarrow.parquet")

from dskit.pipeline.predictions import PredictionWriter  # noqa: E402
from dskit.pipeline.runs import format_ordering, score_ordering  # noqa: E402

LEAD = 5
PERIOD = 5


def _write_walk(root, names, n_folds=4, n=60, skill=0.6, seed=3):
    """A walk whose folds saved rows for ``names``, with a known slope."""
    rng = random.Random(seed)
    summary = os.path.join(root, "walk")
    os.makedirs(summary, exist_ok=True)
    folds = []
    for i in range(n_folds):
        run_dir = os.path.join(root, f"wf-{i}")
        node = os.path.join(run_dir, "artifacts", "scan")
        os.makedirs(node, exist_ok=True)
        stamps = [1_600_000_000_000 + t * PERIOD * 60_000 for t in range(n)]
        with PredictionWriter(
            node, names, fold=i, period_minutes=PERIOD
        ) as writer:
            for name in names:
                y = [rng.gauss(0.0, 1.0) for _ in range(n)]
                # Forecast at HALF the outcome's scale: the true
                # Mincer-Zarnowitz slope is 2, not 1.
                yhat = [0.5 * (skill * v + (1 - skill) * rng.gauss(0, 1))
                        for v in y]
                writer.append(name, LEAD, stamps, y, yhat, 0.0)
        folds.append({"cutoff": f"2024-{i + 1:02d}-01", "run_dir": run_dir,
                      "state": "ran", "score": 0.0})
    import json
    with open(os.path.join(summary, "walkforward.json"), "w",
              encoding="utf-8") as fh:
        json.dump({"name": "walk", "asof": "2025-11-30", "folds": folds}, fh)
    return summary


class TestReadingARealWalk:
    def test_a_walk_of_five_names_answers_both_halves(self, tmp_path):
        scored = score_ordering(_write_walk(str(tmp_path), list("ABCDE")))
        assert scored["exact"] is True
        assert {row["series"] for row in scored["calibration"]} == set("ABCDE")
        # The forecast was built at half the outcome's scale, so the
        # honest slope is near 2 and the size is NOT usable as it stands.
        for row in scored["calibration"]:
            assert row["slope"] > 1.2
            assert "size" in row["reading"] or "under-reacts" in row["reading"]
        order = scored["ordering"][0]
        assert order["names_median"] == 5
        assert order["usable"] is True
        assert order["xs_ic"] is not None
        assert order["pooled_name_time_ic"] is not None

    def test_a_walk_of_three_names_refuses_its_own_ordering_number(self, tmp_path):
        scored = score_ordering(_write_walk(str(tmp_path), list("ABC")))
        order = scored["ordering"][0]
        assert order["names_median"] == 3
        assert order["usable"] is False
        assert order["passes"] is False
        assert any("median instant carried 3" in n for n in scored["notes"])

    def test_the_table_names_the_two_correlations_apart(self, tmp_path):
        text = format_ordering(score_ordering(_write_walk(str(tmp_path), list("ABCDE"))))
        assert "xs_ic" in text
        assert "pooled_name_time_ic" in text
        assert "not the same number" in text
