"""ADR-0067: the skill rule that judges a walk, and its null behaviour.

Two things have to be true of a verdict rule before it may replace
rejection-counting. It must compute what it says it computes — pinned
here against a small example worked by hand — and it must NOT pass on
noise, which is the exact failure the Clark-West count had. The last
test builds that failure on purpose: a model fitted to pure noise, where
Clark-West rejects far more often than the honest rule passes.
"""

import json
import math
import os
import random

import pytest

from dskit.pipeline.runs import (
    SKILL_FILE,
    format_skill,
    read_skill_series,
    score_walk,
    walk_fold_dirs,
)
from dskit.pipeline.stats import (
    across_fold_t,
    cross_sectional_fold,
    diebold_mariano_test,
    dm_lags,
    dm_loss_series,
    skill_vs_mean,
)

ALPHA = 0.05
#: One-sided normal and t(19) 5% points — the ADR states them as 1.645
#: and 1.729, and the code must agree with the numbers in the ADR.
Z_05 = 1.6449
T19_05 = 1.7291


class TestLossDifferential:
    def test_gap_is_the_mean_error_minus_the_model_error(self):
        # y = 1, 3; mu = 2; yhat = 1.5, 2.5
        # d1 = (1-2)^2 - (1-1.5)^2 = 1 - 0.25 = 0.75
        # d2 = (3-2)^2 - (3-2.5)^2 = 1 - 0.25 = 0.75
        got = dm_loss_series([1.0, 3.0], [1.5, 2.5], mu=2.0)
        assert got == pytest.approx([0.75, 0.75])

    def test_a_forecast_worse_than_the_mean_is_negative(self):
        # y = 1; mu = 2; yhat = 4 -> 1 - 9 = -8
        assert dm_loss_series([1.0, 1.0], [4.0, 4.0], mu=2.0)[0] == -8.0

    def test_the_benchmark_defaults_to_this_sample_mean(self):
        assert dm_loss_series([1.0, 3.0], [2.0, 2.0]) == [0.0, 0.0]

    def test_a_ragged_pair_is_refused(self):
        with pytest.raises(ValueError, match="equal length"):
            dm_loss_series([1.0, 2.0], [1.0])


class TestLagRule:
    def test_the_overlap_wins_when_it_is_longer(self):
        assert dm_lags(200, h_steps=30) == 29

    def test_the_automatic_rule_wins_on_a_long_sample(self):
        # floor(4 * (10000/100)^(2/9)) = floor(11.13) = 11
        assert dm_lags(10000, h_steps=1) == 11

    def test_the_band_can_never_span_the_sample(self):
        assert dm_lags(10, h_steps=99) == 9

    def test_a_zero_horizon_is_refused(self):
        with pytest.raises(ValueError, match="h_steps"):
            dm_lags(100, h_steps=0)


class TestDieboldMariano:
    def test_the_t_is_the_hac_mean_over_its_error_by_hand(self):
        # d = 1, 2, 3, 4 at lag 0: mean 2.5, gamma0 = 1.25,
        # se = sqrt(1.25/4) = 0.559017, t = 4.472136 before HLN.
        # HLN at h_steps 1 is sqrt((4+1-2+0)/4) = sqrt(0.75) = 0.8660254.
        out = diebold_mariano_test([1.0, 2.0, 3.0, 4.0], lags=0, h_steps=1)
        assert out["mean"] == pytest.approx(2.5)
        assert out["se"] == pytest.approx(0.5590169943749475)
        assert out["hln"] == pytest.approx(math.sqrt(0.75))
        assert out["t"] == pytest.approx(4.47213595499958 * math.sqrt(0.75))

    def test_a_longer_horizon_shrinks_the_statistic(self):
        d = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]
        near = diebold_mariano_test(d, lags=1, h_steps=1)
        far = diebold_mariano_test(d, lags=1, h_steps=3)
        assert far["t"] < near["t"]

    def test_a_constant_gap_has_no_variance_and_the_sign_decides(self):
        assert diebold_mariano_test([2.0, 2.0, 2.0])["p_value"] == 0.0
        assert diebold_mariano_test([-2.0, -2.0, -2.0])["p_value"] == 1.0


class TestAcrossFoldT:
    def test_the_t_is_the_mean_over_its_standard_error_by_hand(self):
        # 1, 2, 3, 4: mean 2.5, sd (ddof 1) = 1.290994,
        # se = 1.290994/2 = 0.645497, t = 3.872983 on 3 df.
        out = across_fold_t([1.0, 2.0, 3.0, 4.0])
        assert out["mean"] == pytest.approx(2.5)
        assert out["se"] == pytest.approx(0.6454972243679028)
        assert out["t"] == pytest.approx(3.872983346207417)
        assert out["df"] == 3

    def test_the_adrs_own_critical_value_is_reproduced(self):
        # 20 folds, 19 df: the ADR's 1.729 must be this rule's 5%
        # point. Ten folds at m+1 and ten at m-1 have sample sd
        # sqrt(20/19), so se = 1/sqrt(19) and t = m * sqrt(19) exactly.
        m = T19_05 / math.sqrt(19.0)
        out = across_fold_t([m + 1.0] * 10 + [m - 1.0] * 10)
        assert out["df"] == 19
        assert out["t"] == pytest.approx(T19_05)
        assert out["p_value"] == pytest.approx(0.05, abs=0.0002)

    def test_one_fold_is_not_a_cluster(self):
        with pytest.raises(ValueError, match="at least 2 folds"):
            across_fold_t([1.0])


class TestSkillVsMean:
    def _folds(self, per_fold, q=2.0, n=8):
        return [{"d": [g] * n, "q": q} for g in per_fold]

    def test_the_out_of_sample_r2_is_the_pooled_error_ratio(self):
        # Two folds, each 8 rows, q = 2 -> benchmark SSE 16 per fold.
        # Gaps of 1.0 leave the model SSE at 8 per fold: R2 = 0.5.
        out = skill_vs_mean(self._folds([1.0, 1.0]))
        assert out["r2oos_pool"] == pytest.approx(0.5)
        assert out["r2oos_folds"] == pytest.approx([0.5, 0.5])
        assert out["n_rows"] == 16

    def test_a_forecast_worse_than_the_mean_never_passes(self):
        out = skill_vs_mean([
            {"d": [-1.0, -0.5, -1.5, -1.0, -0.9, -1.1], "q": 2.0},
            {"d": [-1.0, -1.2, -0.8, -1.0, -1.1, -0.9], "q": 2.0},
        ])
        assert out["r2oos_pool"] < 0.0
        assert out["passes"] is False

    def test_both_halves_must_clear_the_bar(self):
        # One fold carries the whole win; the pooled t is large and the
        # across-fold t is not, so the verdict is a fail either way.
        rng = random.Random(11)
        folds = [
            {"d": [rng.gauss(0.0, 1.0) for _ in range(120)], "q": 1.0}
            for _ in range(20)
        ]
        folds[0]["d"] = [v + 25.0 for v in folds[0]["d"]]
        out = skill_vs_mean(folds)
        assert out["t_pool"] > Z_05
        assert out["t_fold"] < T19_05
        assert out["passes"] is False

    def test_a_real_edge_passes_both(self):
        rng = random.Random(3)
        folds = [
            {"d": [0.4 + rng.gauss(0.0, 1.0) for _ in range(200)], "q": 1.0}
            for _ in range(20)
        ]
        out = skill_vs_mean(folds)
        assert out["t_pool"] > Z_05
        assert out["t_fold"] > T19_05
        assert out["passes"] is True

    def test_the_scaling_stops_one_loud_fold_outvoting_the_rest(self):
        # Same R2 in every fold, wildly different variance: a rule that
        # pooled raw gaps would be decided by the loud fold alone.
        folds = [{"d": [1.0] * 10, "q": 2.0}, {"d": [1000.0] * 10, "q": 2000.0}]
        out = skill_vs_mean(folds)
        assert out["r2oos_folds"] == pytest.approx([0.5, 0.5])

    def test_a_fold_without_a_benchmark_is_refused(self):
        with pytest.raises(ValueError, match="positive finite"):
            skill_vs_mean([{"d": [1.0, 1.0], "q": 0.0}])


class TestCrossSectionalFold:
    def test_units_average_at_each_shared_timestamp(self):
        out = cross_sectional_fold([
            {"stamps": [1, 2], "d": [2.0, 2.0], "q": 1.0},
            {"stamps": [2, 3], "d": [4.0, 4.0], "q": 1.0},
        ])
        assert out["stamps"] == [1, 2, 3]
        assert out["d"] == pytest.approx([2.0, 3.0, 4.0])
        assert out["q"] == 1.0

    def test_the_average_is_scale_free_before_it_is_taken(self):
        out = cross_sectional_fold([
            {"stamps": [1], "d": [2.0], "q": 2.0},
            {"stamps": [1], "d": [100.0], "q": 100.0},
        ])
        assert out["d"] == pytest.approx([1.0])

    def test_a_ragged_unit_is_refused(self):
        with pytest.raises(ValueError, match="stamps"):
            cross_sectional_fold([{"stamps": [1, 2], "d": [1.0], "q": 1.0}])


class TestTheNull:
    """On noise the rule must pass at about its nominal rate, not above."""

    TRIALS = 400
    FOLDS = 20
    ROWS = 100

    def _noise_folds(self, rng):
        return [
            {"d": [rng.gauss(0.0, 1.0) for _ in range(self.ROWS)], "q": 1.0}
            for _ in range(self.FOLDS)
        ]

    def test_each_half_rejects_at_about_the_nominal_rate(self):
        rng = random.Random(20260903)
        pooled = folds = 0
        for _ in range(self.TRIALS):
            out = skill_vs_mean(self._noise_folds(rng), alpha=ALPHA)
            pooled += out["p_pool"] <= ALPHA
            folds += out["p_fold"] <= ALPHA
        # Binomial se at 5% over 400 trials is 1.1 points; a rule that
        # rejected noise at 15% would be the defect this ADR removes.
        assert 0.01 <= pooled / self.TRIALS <= 0.10
        assert 0.01 <= folds / self.TRIALS <= 0.10

    def test_the_joint_pass_rate_is_no_higher_than_nominal(self):
        rng = random.Random(451)
        passed = sum(
            skill_vs_mean(self._noise_folds(rng), alpha=ALPHA)["passes"]
            for _ in range(self.TRIALS)
        )
        assert passed / self.TRIALS <= ALPHA

    def test_a_model_fitted_to_noise_does_not_pass(self):
        # The concrete failure the ADR names: a one-feature fit on pure
        # noise carries no information, so its out-of-sample error is
        # WORSE than the training mean's by about one over the training
        # count, and the rule must say so.
        rng = random.Random(7)
        folds = []
        for _ in range(self.FOLDS):
            train = [(rng.gauss(0, 1), rng.gauss(0, 1)) for _ in range(10)]
            mx = sum(x for x, _ in train) / len(train)
            my = sum(y for _, y in train) / len(train)
            cov = sum((x - mx) * (y - my) for x, y in train)
            var = sum((x - mx) ** 2 for x, _ in train)
            beta = cov / var
            rows = [(rng.gauss(0, 1), rng.gauss(0, 1)) for _ in range(200)]
            y = [v for _, v in rows]
            yhat = [my + beta * (x - mx) for x, _ in rows]
            gaps = dm_loss_series(y, yhat, mu=my)
            q = sum((v - my) ** 2 for v in y) / len(y)
            folds.append({"d": gaps, "q": q})
        out = skill_vs_mean(folds, alpha=ALPHA)
        assert out["r2oos_pool"] < 0.0
        assert out["passes"] is False


def _write_fold(root, cutoff, series, gaps=True):
    """One fold run dir: a carry record per series, optionally its gaps."""
    run_dir = os.path.join(root, f"wf-{cutoff}")
    os.makedirs(os.path.join(run_dir, "artifacts", "scan"), exist_ok=True)
    records, payload = [], []
    for name, (mspe_model, mspe_mean, n, t_stat, p_value) in series.items():
        records.append({
            "symbol": name, "lead": 1, "mspe_model": mspe_model,
            "mspe_mean": mspe_mean, "n": n, "t_stat": t_stat,
            "p_value": p_value,
        })
        payload.append({
            "symbol": name, "lead": 1, "h_steps": 1, "q": mspe_mean,
            "stamps": list(range(n)),
            "d": [mspe_mean - mspe_model] * n,
        })
    with open(os.path.join(run_dir, "carry.json"), "w", encoding="utf-8") as fh:
        json.dump({"scan": {"records": records}}, fh)
    if gaps:
        path = os.path.join(run_dir, "artifacts", "scan", SKILL_FILE)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"period_minutes": 5, "series": payload}, fh)
    return run_dir


def _write_walk(root, n_folds=20, gaps=True, edge=0.02):
    """A walk-forward summary over ``n_folds`` folds of two series."""
    rng = random.Random(99)
    summary = os.path.join(root, "walk")
    os.makedirs(summary, exist_ok=True)
    folds = []
    for i in range(n_folds):
        series = {}
        for name in ("AAA", "BBB"):
            mean = 1.0 + rng.gauss(0.0, 0.05)
            series[name] = (mean * (1.0 - edge), mean, 40, 1.2, 0.11)
        run_dir = _write_fold(root, f"2024-{i + 1:02d}-01", series, gaps=gaps)
        folds.append({"cutoff": f"2024-{i + 1:02d}-01", "run_dir": run_dir,
                      "state": "ran", "score": 0.0})
    with open(os.path.join(summary, "walkforward.json"), "w", encoding="utf-8") as fh:
        json.dump({"name": "walk", "asof": "2025-11-30", "folds": folds}, fh)
    return summary


class TestScoreWalk:
    def test_a_walk_with_gaps_is_scored_exactly(self, tmp_path):
        summary = _write_walk(str(tmp_path))
        scored = score_walk(summary)
        assert scored["exact"] is True
        assert scored["n_folds"] == 20
        assert scored["notes"] == []
        names = [row["series"] for row in scored["rows"]]
        assert names == ["AAA", "BBB", "GROUP"]
        for row in scored["rows"]:
            assert row["r2oos"] == pytest.approx(0.02, abs=1e-9)
            assert row["passes"] is True
            assert row["cw_t_mean"] == pytest.approx(1.2)
            assert row["cw_reject_frac"] == 0.0

    def test_a_walk_without_gaps_answers_only_the_across_fold_half(self, tmp_path):
        summary = _write_walk(str(tmp_path), gaps=False)
        scored = score_walk(summary)
        assert scored["exact"] is False
        assert "not recoverable" in scored["notes"][0].lower()
        for row in scored["rows"]:
            assert row["t_pool"] is None
            assert row["passes"] is None
            assert row["t_fold"] is not None
            assert row["r2oos"] == pytest.approx(0.02, abs=1e-9)

    def test_a_losing_walk_fails_on_both_halves(self, tmp_path):
        summary = _write_walk(str(tmp_path), edge=-0.02)
        scored = score_walk(summary)
        for row in scored["rows"]:
            assert row["r2oos"] < 0.0
            assert row["passes"] is False

    def test_the_fold_dirs_come_back_in_walk_order(self, tmp_path):
        summary = _write_walk(str(tmp_path), n_folds=3)
        dirs = walk_fold_dirs(summary)
        assert [os.path.basename(d) for d in dirs] == [
            "wf-2024-01-01", "wf-2024-02-01", "wf-2024-03-01"
        ]
        assert len(read_skill_series(dirs[0])) == 2

    def test_a_directory_that_is_not_a_walk_is_refused(self, tmp_path):
        with pytest.raises(ValueError, match="walk-forward summary"):
            score_walk(str(tmp_path))

    def test_the_table_names_every_column_the_verdict_rests_on(self, tmp_path):
        rendered = format_skill(score_walk(_write_walk(str(tmp_path))))
        for column in ("t_pool", "t_fold", "r2oos", "cw_t_mean", "passes"):
            assert column in rendered
        assert "GROUP" in rendered
