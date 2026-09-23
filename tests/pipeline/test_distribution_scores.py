"""Proper scores and PIT tests for sample-set forecasts (ADR-0168)."""

import math
import random
from types import SimpleNamespace

import pytest

from dskit.pipeline.distribution_scores import (
    BerkowitzTest,
    Crps,
    PitCalibration,
    SampleDistribution,
    ScoreDistributions,
    ThresholdBrier,
    ThresholdWeightedCrps,
)
from dskit.pipeline.node import ConfigError, JsonArtifact

INF = float("inf")


def _brute_crps(samples, y, lo, hi, steps=200_000):
    dist = SampleDistribution(samples)
    width = (hi - lo) / steps
    return sum(
        (dist.cdf(lo + (k + 0.5) * width) - (1.0 if y <= lo + (k + 0.5) * width else 0.0)) ** 2
        for k in range(steps)
    ) * width


def test_sample_distribution_cdf_quantile_pit():
    dist = SampleDistribution([2.0, 0.0, 1.0])
    assert dist.cdf(-1) == 0 and dist.cdf(1.0) == pytest.approx(2 / 3) and dist.cdf(5) == 1
    assert dist.quantile(0.0) == 0.0 and dist.quantile(0.5) == 1.0 and dist.quantile(1.0) == 2.0
    assert 0 < dist.pit(-10) < dist.pit(10) < 1


@pytest.mark.parametrize("bad", [[], [float("nan")], [1.0, True], "abc", None])
def test_sample_distribution_refuses_bad_draws(bad):
    with pytest.raises(ValueError):
        SampleDistribution(bad)


def test_crps_closed_form_matches_exact_whole_line_integral():
    rng = random.Random(3)
    for _ in range(20):
        xs = [rng.gauss(0, 1) for _ in range(rng.randint(1, 30))]
        y = rng.gauss(0, 1.5)
        exact = ThresholdWeightedCrps([[-INF, INF]]).score(SampleDistribution(xs), y)
        assert Crps().score(SampleDistribution(xs), y) == pytest.approx(exact, abs=1e-12)


def test_twcrps_exact_integration_matches_brute_force_and_is_additive():
    xs, y = [-1.2, -0.3, 0.1, 0.8, 1.9], 0.4
    dist = SampleDistribution(xs)
    left = ThresholdWeightedCrps([[-2.5, -0.5]]).score(dist, y)
    right = ThresholdWeightedCrps([[0.5, 2.5]]).score(dist, y)
    both = ThresholdWeightedCrps([[0.5, 2.5], [-2.5, -0.5]]).score(dist, y)
    assert left == pytest.approx(_brute_crps(xs, y, -2.5, -0.5), abs=1e-5)
    assert right == pytest.approx(_brute_crps(xs, y, 0.5, 2.5), abs=1e-5)
    assert both == pytest.approx(left + right)
    assert both <= Crps().score(dist, y)


def test_twcrps_ignores_errors_outside_the_region():
    dist = SampleDistribution([0.0])
    assert ThresholdWeightedCrps([[1.0, 2.0]]).score(dist, 0.5) == 0.0


@pytest.mark.parametrize("bad", [[], [[1, 0]], [[0, 2], [1, 3]], [[0, float("nan")]], "x"])
def test_twcrps_refuses_bad_intervals(bad):
    with pytest.raises(ValueError):
        ThresholdWeightedCrps(bad)


def test_threshold_brier_is_the_crps_integrand_at_points():
    dist = SampleDistribution([0.0, 2.0])
    rule = ThresholdBrier([-1.0, 1.0])
    assert rule.per_threshold(dist, 0.5) == [0.0, 0.25]
    assert rule.score(dist, 0.5) == pytest.approx(0.125)
    with pytest.raises(ValueError):
        ThresholdBrier([])


def test_scores_are_proper_the_true_distribution_wins_on_average():
    rng = random.Random(7)
    truth = [rng.gauss(0, 1) for _ in range(400)]
    right = SampleDistribution([rng.gauss(0, 1) for _ in range(400)])
    wide = SampleDistribution([rng.gauss(0, 2) for _ in range(400)])
    for rule in (Crps(), ThresholdWeightedCrps([[-2.5, -0.5], [0.5, 2.5]]),
                 ThresholdBrier([-1.5, -0.5, 0.5, 1.5])):
        good = sum(rule.score(right, y) for y in truth)
        bad = sum(rule.score(wide, y) for y in truth)
        assert good < bad, rule.name


def test_pit_ks_accepts_uniform_and_rejects_biased():
    rng = random.Random(11)
    uniform = [rng.random() for _ in range(500)]
    result = PitCalibration(10).evaluate(uniform)
    assert result["pvalue"] > 0.01 and sum(result["histogram"]) == 500
    assert PitCalibration(10).evaluate([u * 0.5 for u in uniform])["pvalue"] < 1e-6
    assert PitCalibration(5).evaluate([])["pvalue"] is None
    with pytest.raises(ValueError):
        PitCalibration(1)


def test_berkowitz_accepts_calibrated_and_rejects_overdispersed_and_dependent():
    rng = random.Random(5)
    good = [rng.random() for _ in range(600)]
    assert BerkowitzTest().evaluate(good)["pvalue"] > 0.01
    squeezed = [0.5 + (u - 0.5) * 0.3 for u in good]
    assert BerkowitzTest().evaluate(squeezed)["pvalue"] < 1e-6
    from statistics import NormalDist
    z, dependent = 0.0, []
    for _ in range(600):
        z = 0.8 * z + 0.6 * rng.gauss(0, 1)
        dependent.append(NormalDist().cdf(z))
    result = BerkowitzTest().evaluate(dependent)
    assert result["pvalue"] < 1e-6 and result["rho"] == pytest.approx(0.8, abs=0.1)
    assert BerkowitzTest().evaluate([0.5, 0.2])["pvalue"] is None


def test_berkowitz_chi2_tail_matches_known_quantile():
    assert BerkowitzTest._chi2_sf3(7.814727903) == pytest.approx(0.05, abs=1e-6)
    assert BerkowitzTest._chi2_sf3(0.0) == 1.0


def _rows():
    rng = random.Random(2)
    rows = []
    for i in range(60):
        rows.append({"asof_ms": i, "group": f"g{i}", "outcome": rng.gauss(0, 1),
                     "samples": [rng.gauss(0, 1) for _ in range(50)]})
    rows.append({"asof_ms": 99, "group": "late", "outcome": None, "samples": [0.0]})
    return rows


class _HalfSplit:
    def split_of(self, frame):
        return "train" if frame.asof_ms < 30 else "val"


PARAMS = {"split": "val", "weight_intervals": [[-2.5, -0.5], [0.5, 2.5]],
          "thresholds": [-1.0, 1.0]}


def test_score_node_reads_only_its_split_and_counts_skips():
    node = ScoreDistributions("score", dict(PARAMS))
    out = node.run(SimpleNamespace(splits=_HalfSplit()), {"forecasts": _rows()})
    metrics = out["metrics"]
    assert metrics["n"] == 30 and metrics["n_skipped_other_split"] == 30
    assert metrics["n_skipped_no_outcome"] == 1
    assert 0 < metrics["twcrps"] < metrics["crps"]
    assert math.isfinite(metrics["berkowitz_pvalue"]) and metrics["pit_ks_pvalue"] > 0
    assert isinstance(out["report"], JsonArtifact)
    assert set(out["report"].value["brier_by_threshold"]) == {"-1.0", "1.0"}


def test_score_node_refuses_too_few_rows_and_bad_params():
    node = ScoreDistributions("score", dict(PARAMS, min_rows=31))
    with pytest.raises(ValueError, match="min_rows"):
        node.run(SimpleNamespace(splits=_HalfSplit()), {"forecasts": _rows()})
    for bad in ({"split": "nope"}, {"weight_intervals": [[1, 0]]}, {"typo": 1},
                {"thresholds": []}, {"pit_bins": 1}, {"samples_field": ""}):
        with pytest.raises(ConfigError):
            ScoreDistributions("score", dict(PARAMS, **bad))
    assert ScoreDistributions("s", dict(PARAMS)).validate_inputs({"forecasts": 3})
