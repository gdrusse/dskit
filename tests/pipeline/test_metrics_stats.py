"""Scoring rules + the cluster bootstrap / multiplicity machinery."""

import math
from statistics import NormalDist

import pytest

from dskit.pipeline.metrics import (
    CLIP,
    METRICS,
    absolute_error,
    brier,
    logloss,
    pinball,
    register_metric,
    squared_error,
)
from dskit.pipeline.stats import (
    CORRECTIONS,
    METHODS,
    benjamini_hochberg,
    bonferroni,
    clark_west_series,
    cluster_bootstrap_pvalue,
    cluster_bootstrap_t,
    correction,
    deflated_sharpe_ratio,
    lower_tail_mean,
    max_drawdown,
    max_informative_horizon,
    newey_west_mean,
    no_correction,
    no_information_test,
    payoff_ratio,
    probabilistic_sharpe_ratio,
    profit_factor,
    register_correction,
    sharpe_ratio,
    student_t_sf,
    weighted_benjamini_hochberg,
)


class TestMetrics:
    def test_logloss_values(self):
        assert logloss(0.8, 1.0) == pytest.approx(-math.log(0.8))
        assert logloss(0.8, 0.0) == pytest.approx(-math.log(0.2))

    def test_logloss_clips_hard_beliefs(self):
        assert logloss(0.0, 1.0) == pytest.approx(-math.log(CLIP))
        assert math.isfinite(logloss(1.0, 0.0))

    def test_brier_values(self):
        assert brier(0.8, 1.0) == pytest.approx(0.04)
        assert brier(0.8, 0.0) == pytest.approx(0.64)

    def test_domain_errors(self):
        with pytest.raises(ValueError, match="q must lie"):
            logloss(1.2, 1.0)
        with pytest.raises(ValueError, match="y must be 0.0 or 1.0"):
            brier(0.5, 0.7)
        with pytest.raises(ValueError, match="finite"):
            logloss(float("nan"), 1.0)
        with pytest.raises(ValueError, match="number"):
            brier(True, 1.0)

    def test_regression_rules_take_unbounded_values(self):
        # ADR-0025: the mark-to-market pair has no [0, 1] frame.
        assert squared_error(-2.5, 1.5) == pytest.approx(16.0)
        assert absolute_error(-2.5, 1.5) == pytest.approx(4.0)
        assert squared_error(3.0, 3.0) == 0.0

    def test_regression_rules_still_refuse_non_finite_values(self):
        with pytest.raises(ValueError, match="finite"):
            squared_error(float("inf"), 1.0)
        with pytest.raises(ValueError, match="finite"):
            absolute_error(0.0, float("nan"))
        with pytest.raises(ValueError, match="number"):
            squared_error(True, 1.0)

    def test_pinball_at_median_is_half_mae(self):
        assert pinball(0.0, 2.0) == pytest.approx(1.0)
        assert pinball(2.0, 0.0) == pytest.approx(1.0)
        assert pinball(3.0, 3.0) == 0.0

    def test_pinball_refuses_bad_tau(self):
        with pytest.raises(ValueError, match="tau"):
            pinball(0.0, 1.0, tau=0.0)
        with pytest.raises(ValueError, match="tau"):
            pinball(0.0, 1.0, tau=1.0)

    def test_registry_and_registration(self):
        assert set(METRICS) >= {
            "logloss",
            "brier",
            "squared_error",
            "absolute_error",
            "pinball",
        }
        with pytest.raises(ValueError, match="already registered"):
            register_metric("logloss", lambda q, y: 0.0)
        with pytest.raises(ValueError, match="non-empty"):
            register_metric("", lambda q, y: 0.0)
        with pytest.raises(ValueError, match="callable"):
            register_metric("new", "nope")


class TestClusterBootstrap:
    def test_p_matches_the_per_draw_summing_reference_exactly(self):
        # Pins the precomputed cluster sums to the original per-draw sum.
        import random as _random

        from dskit.pipeline.stats import _bootstrap_rng

        rng = _random.Random(3)
        scores = {f"c{i}": [rng.gauss(0.05, 1.0) for _ in range(rng.randint(1, 6))]
                  for i in range(17)}
        draw = _bootstrap_rng(5, "L")
        clusters = sorted(scores)
        below = 0
        for _ in range(250):
            total, count = 0.0, 0
            for _ in range(len(clusters)):
                picked = scores[clusters[draw.randrange(len(clusters))]]
                total += sum(picked)
                count += len(picked)
            below += total / count <= 0.0
        assert cluster_bootstrap_pvalue(scores, 250, 5, label="L") == (1 + below) / 251

    def test_clear_edge_gets_small_p(self):
        scores = {f"ev{i}": [0.5 + 0.01 * (i % 3)] for i in range(40)}
        assert cluster_bootstrap_pvalue(scores, 200, 0, label="X") < 0.01

    def test_no_edge_gets_large_p(self):
        scores = {f"ev{i}": [-0.5] for i in range(40)}
        assert cluster_bootstrap_pvalue(scores, 200, 0, label="X") > 0.99

    def test_deterministic_and_label_seeded(self):
        scores = {f"ev{i}": [(-1) ** i * 0.3, 0.05] for i in range(20)}
        p1 = cluster_bootstrap_pvalue(scores, 300, 7, label="A")
        p2 = cluster_bootstrap_pvalue(scores, 300, 7, label="A")
        p3 = cluster_bootstrap_pvalue(scores, 300, 7, label="B")
        assert p1 == p2
        assert p1 != p3  # per-instrument streams are independent

    def test_never_exactly_zero_or_one(self):
        p = cluster_bootstrap_pvalue({"e": [1.0]}, 100, 0)
        assert 0.0 < p < 1.0

    def test_empty_inputs_fail_loud(self):
        with pytest.raises(ValueError, match="empty"):
            cluster_bootstrap_pvalue({}, 10, 0)
        with pytest.raises(ValueError, match="no scores"):
            cluster_bootstrap_pvalue({"e": []}, 10, 0)


class TestCorrections:
    def test_bh_known_example(self):
        pvals = {"a": 0.001, "b": 0.02, "c": 0.03, "d": 0.9}
        rejected = benjamini_hochberg(pvals, alpha=0.05)
        assert rejected == {"a": True, "b": True, "c": True, "d": False}

    def test_bh_rejects_nothing_when_nothing_clears(self):
        assert benjamini_hochberg({"a": 0.9, "b": 0.8}, 0.05) == {
            "a": False,
            "b": False,
        }

    def test_bonferroni_is_stricter(self):
        pvals = {"a": 0.03, "b": 0.04}
        assert bonferroni(pvals, 0.05) == {"a": False, "b": False}
        assert no_correction(pvals, 0.05) == {"a": True, "b": True}

    def test_per_instrument_decisions_preserved(self):
        # the deliverable IS the localization: each name keeps its own call
        rejected = benjamini_hochberg({"a": 0.001, "z": 0.99}, 0.05)
        assert rejected["a"] and not rejected["z"]

    def test_input_validation(self):
        with pytest.raises(ValueError, match="nothing to test"):
            benjamini_hochberg({}, 0.05)
        with pytest.raises(ValueError, match="p-value"):
            bonferroni({"a": 0.0}, 0.05)
        with pytest.raises(ValueError, match="alpha"):
            no_correction({"a": 0.5}, 1.5)

    def test_registry(self):
        assert set(CORRECTIONS) == {"bh", "bonferroni", "none", "weighted-bh"}
        for entry in CORRECTIONS.values():
            assert set(entry) == {"fn", "needs_weights", "doc"}
            assert callable(entry["fn"])
        assert CORRECTIONS["weighted-bh"]["needs_weights"] is True
        assert METHODS == ("plain", "studentized")


class TestClusterBootstrapT:
    def test_clear_edge_gets_small_p(self):
        scores = {f"ev{i}": [0.5 + 0.01 * (i % 3)] for i in range(40)}
        assert cluster_bootstrap_t(scores, 200, 0, label="X")["p_value"] < 0.01

    def test_no_edge_gets_large_p(self):
        scores = {f"ev{i}": [-0.5 - 0.01 * (i % 3)] for i in range(40)}
        assert cluster_bootstrap_t(scores, 200, 0, label="X")["p_value"] > 0.9

    def test_signed_degenerate_positive_hits_the_add_one_floor(self):
        # identical positives: zero variance, no resampling — the sign decides
        res = cluster_bootstrap_t({f"ev{i}": [0.5] for i in range(10)}, 1000, 0)
        assert res["p_value"] == 1 / 1001
        assert res["se"] == 0.0
        assert res["t"] is None
        assert res["ci_low"] is None and res["ci_high"] is None

    def test_signed_degenerate_nonpositive_is_p_one(self):
        for v in (-0.5, 0.0):
            res = cluster_bootstrap_t({f"ev{i}": [v] for i in range(10)}, 1000, 0)
            assert res["p_value"] == 1.0
            assert res["ci_low"] is None and res["ci_high"] is None

    def test_deterministic_and_label_seeded(self):
        scores = {f"ev{i}": [(-1) ** i * 0.3, 0.05] for i in range(20)}
        r1 = cluster_bootstrap_t(scores, 300, 7, label="A")
        r2 = cluster_bootstrap_t(scores, 300, 7, label="A")
        r3 = cluster_bootstrap_t(scores, 300, 7, label="B")
        assert r1 == r2
        assert r1["p_value"] != r3["p_value"]  # per-instrument streams

    def test_p_strictly_between_zero_and_one(self):
        scores = {f"ev{i}": [0.1 * ((-1) ** i) + 0.02 * i] for i in range(12)}
        assert 0.0 < cluster_bootstrap_t(scores, 200, 0)["p_value"] < 1.0

    def test_refusals(self):
        with pytest.raises(ValueError, match="empty"):
            cluster_bootstrap_t({}, 10, 0)
        with pytest.raises(ValueError, match="no scores"):
            cluster_bootstrap_t({"e": []}, 10, 0)
        with pytest.raises(ValueError, match="at least 2 clusters"):
            cluster_bootstrap_t({"e": [0.5]}, 10, 0)
        with pytest.raises(ValueError, match="alpha"):
            cluster_bootstrap_t({"a": [0.1], "b": [0.2]}, 10, 0, alpha=1.5)

    def test_se_reduces_to_classic_for_single_record_clusters(self):
        values = [0.3, -0.2, 0.5, 0.1, -0.4, 0.25]
        res = cluster_bootstrap_t({f"e{i}": [v] for i, v in enumerate(values)}, 50, 0)
        n = len(values)
        mean = sum(values) / n
        s = math.sqrt(sum((v - mean) ** 2 for v in values) / (n - 1))
        assert res["mean"] == pytest.approx(mean)
        assert res["se"] == pytest.approx(s / math.sqrt(n))
        assert res["t"] == pytest.approx(mean / (s / math.sqrt(n)))

    def test_multi_record_clusters_by_hand_arithmetic(self):
        # T = (3, 4, 2), m = (2, 1, 1): theta = 9/4; residuals
        # (-1.5, 1.75, -0.25); se = sqrt(3/2 * 5.375) / 4
        scores = {"a": [1.0, 2.0], "b": [4.0], "c": [2.0]}
        res = cluster_bootstrap_t(scores, 50, 0)
        assert res["mean"] == pytest.approx(2.25)
        assert res["se"] == pytest.approx(math.sqrt(1.5 * 5.375) / 4)
        assert res["n_clusters"] == 3

    def test_ci_brackets_the_mean_on_a_healthy_sample(self):
        scores = {f"ev{i}": [0.5 + 0.05 * (i % 7)] for i in range(40)}
        res = cluster_bootstrap_t(scores, 500, 0, label="X")
        assert res["ci_low"] is not None and res["ci_high"] is not None
        assert res["ci_low"] < res["mean"] < res["ci_high"]

    def test_two_clusters_yield_p_but_degenerate_ci_bounds(self):
        # half the replicates draw one cluster twice: infinite pivots sit
        # in both tails, so neither bound is claimable
        res = cluster_bootstrap_t({"a": [0.5], "b": [0.3]}, 200, 0)
        assert 0.0 < res["p_value"] < 1.0
        assert res["ci_low"] is None and res["ci_high"] is None

    def test_exact_tie_is_floored_at_the_methods_own_resampling_floor(self):
        # An exact two-cluster tie must not leap past what the method's
        # own resampling could ever report (~0.25 at n=2): the degenerate
        # p is floored at half the all-one-cluster replicate mass,
        # n^(1-n)/2. A strictly less informative sample can never claim
        # more significance than an epsilon-perturbed one.
        tie = cluster_bootstrap_t({"a": [0.5], "b": [0.5]}, 10_000, 0)
        assert tie["p_value"] == 0.25  # max(1/10001, 2**-1 / 2)
        near = cluster_bootstrap_t(
            {"a": [0.5], "b": [math.nextafter(0.5, 1.0)]}, 10_000, 0
        )
        assert tie["p_value"] <= near["p_value"] * 1.5  # no cliff between them
        # n=3: floor 3^-2/2 = 1/18; by n=10 the add-one floor rules.
        three = cluster_bootstrap_t({f"e{i}": [0.7] for i in range(3)}, 1000, 0)
        assert three["p_value"] == pytest.approx(1 / 18)
        ten = cluster_bootstrap_t({f"e{i}": [0.7] for i in range(10)}, 1000, 0)
        assert ten["p_value"] == 1 / 1001

    def test_float_dust_still_hits_the_degenerate_path(self):
        # sum([0.1]*3)/3 rounds a hair away from 0.1: an exact se==0.0
        # gate would let ULP dust masquerade as t ~ 1e16 with a
        # zero-width interval. Degeneracy is structural (equal cluster
        # means), so the dust case reports exactly like the exact case.
        res = cluster_bootstrap_t({f"e{i}": [0.1] for i in range(3)}, 1000, 0)
        assert res["se"] == 0.0
        assert res["t"] is None
        assert res["ci_low"] is None and res["ci_high"] is None
        assert res["p_value"] == pytest.approx(1 / 18)

    def test_n_boot_guards(self):
        scores = {"a": [0.1], "b": [0.2]}
        for bad in (0, -1, 2.5, True):
            with pytest.raises(ValueError, match="n_boot"):
                cluster_bootstrap_pvalue(scores, bad, 0)
            with pytest.raises(ValueError, match="n_boot"):
                cluster_bootstrap_t(scores, bad, 0)

    def test_pivot_is_scale_invariant(self):
        scores = {f"ev{i}": [0.1 * ((-1) ** i) + 0.03 * i] for i in range(15)}
        scaled = {k: [v * 7.3 for v in vals] for k, vals in scores.items()}
        p1 = cluster_bootstrap_t(scores, 300, 3, label="S")["p_value"]
        p2 = cluster_bootstrap_t(scaled, 300, 3, label="S")["p_value"]
        assert p1 == p2

    def test_golden_fraction(self):
        # pinned forever: the sha256 seed recipe + the draw pattern are
        # part of the contract, and this value moves if either does
        res = cluster_bootstrap_t(
            {"a": [0.2], "b": [0.5], "c": [-0.1], "d": [0.4], "e": [0.3]},
            999,
            0,
            label="G",
        )
        assert res["p_value"] == GOLDEN_T_P


class TestWeightedBH:
    def test_weights_flip_the_family_decision(self):
        pvals = {"a": 0.04, "b": 0.30}
        weights = {"a": 4.0, "b": 0.5}
        assert weighted_benjamini_hochberg(pvals, 0.05, weights) == {
            "a": True,
            "b": False,
        }
        # plain BH rejects nothing here: 0.04 > 0.05 * 1/2
        assert benjamini_hochberg(pvals, 0.05) == {"a": False, "b": False}

    def test_unit_weights_reproduce_plain_bh(self):
        families = [
            {"a": 0.001, "b": 0.02, "c": 0.03, "d": 0.9},
            {"a": 0.9, "b": 0.8},
            {"x": 0.04, "y": 0.049, "z": 0.5},
        ]
        for pvals in families:
            unit = {name: 1.0 for name in pvals}
            assert weighted_benjamini_hochberg(pvals, 0.05, unit) == (
                benjamini_hochberg(pvals, 0.05)
            )

    def test_missing_weight_names_the_instrument(self):
        with pytest.raises(ValueError, match="'b'"):
            weighted_benjamini_hochberg({"a": 0.01, "b": 0.02}, 0.05, {"a": 1.0})

    def test_bad_weights_refuse(self):
        pvals = {"a": 0.01}
        for w in (0.0, -1.0, True, float("nan"), float("inf"), "2"):
            with pytest.raises(ValueError, match="finite number > 0"):
                weighted_benjamini_hochberg(pvals, 0.05, {"a": w})
        with pytest.raises(ValueError, match="dict"):
            weighted_benjamini_hochberg(pvals, 0.05, [1.0])

    def test_extra_weight_keys_are_ignored(self):
        # the tested set may be a strict subset of the weighted family
        out = weighted_benjamini_hochberg(
            {"a": 0.001}, 0.05, {"a": 1.0, "not-tested": 9.0}
        )
        assert out == {"a": True}

    def test_q_over_one_is_legal(self):
        out = weighted_benjamini_hochberg({"a": 0.9}, 0.05, {"a": 0.5})
        assert out == {"a": False}


class TestRegisterCorrection:
    def test_duplicate_raises(self):
        with pytest.raises(ValueError, match="already registered"):
            register_correction("bh", benjamini_hochberg)

    def test_bad_arguments_raise(self):
        with pytest.raises(ValueError, match="non-empty"):
            register_correction("", benjamini_hochberg)
        with pytest.raises(ValueError, match="callable"):
            register_correction("new", "nope")
        with pytest.raises(ValueError, match="bool"):
            register_correction("new", benjamini_hochberg, needs_weights=1)

    def test_registered_correction_dispatches(self):
        def strict(pvalues, alpha):
            return {name: False for name in pvalues}

        register_correction("test-strict", strict, doc="rejects nothing")
        try:
            entry = correction("test-strict")
            assert entry["fn"]({"a": 0.001}, 0.05) == {"a": False}
            assert entry["needs_weights"] is False
        finally:
            del CORRECTIONS["test-strict"]

    def test_unknown_lookup_names_the_family(self):
        with pytest.raises(ValueError, match="known:"):
            correction("nope")


#: The pinned studentized golden value (see test_golden_fraction):
#: 65 of 999 replicates met or beat the observed pivot — (1 + 65) / (999 + 1).
GOLDEN_T_P = 66 / 1000


class TestClarkWestSeries:
    def test_identity_matches_the_two_algebra_forms(self):
        y, yhat, mu = [1.0, 0.0, -1.0], [0.5, 0.0, 0.25], 0.1
        adj = clark_west_series(y, yhat, mu=mu)
        for yi, fi, got in zip(y, yhat, adj):
            raw = (yi - mu) ** 2 - (yi - fi) ** 2 + (fi - mu) ** 2
            twice = 2.0 * (yi - mu) * (fi - mu)
            assert got == pytest.approx(raw)
            assert got == pytest.approx(twice)

    def test_constant_forecast_at_mu_is_all_zeros(self):
        assert clark_west_series([1.0, 3.0], [2.0, 2.0], mu=2.0) == [0.0, 0.0]

    def test_omitted_mu_is_the_sample_mean(self):
        y, yhat = [1.0, 3.0], [2.0, 2.0]
        assert clark_west_series(y, yhat) == clark_west_series(y, yhat, mu=2.0)

    def test_refusals(self):
        with pytest.raises(ValueError, match="equal length"):
            clark_west_series([1.0], [1.0, 2.0])
        with pytest.raises(ValueError, match="empty"):
            clark_west_series([], [])
        with pytest.raises(ValueError, match="finite"):
            clark_west_series([1.0, float("nan")], [1.0, 1.0])
        with pytest.raises(ValueError, match="mu"):
            clark_west_series([1.0, 2.0], [1.0, 2.0], mu=True)


class TestNeweyWestMean:
    def test_hand_lags_one_is_t_four(self):
        # mean 2.5, γ0=1.25, γ1=0.3125, Bartlett w1=1/2 → LRV=1.5625,
        # se = √(LRV/n) = 0.625, t = 4.
        out = newey_west_mean([1.0, 2.0, 3.0, 4.0], lags=1)
        assert out["n"] == 4
        assert out["lags"] == 1
        assert out["mean"] == pytest.approx(2.5)
        assert out["se"] == pytest.approx(0.625)
        assert out["t"] == pytest.approx(4.0)
        assert out["p_value"] == pytest.approx(0.5 * math.erfc(4.0 / math.sqrt(2.0)))

    def test_lags_zero_is_the_iid_se(self):
        values = [1.0, 2.0, 3.0, 4.0]
        out = newey_west_mean(values, lags=0)
        mean = 2.5
        gamma0 = sum((v - mean) ** 2 for v in values) / 4
        assert out["se"] == pytest.approx(math.sqrt(gamma0 / 4))

    def test_constant_positive_is_p_zero(self):
        out = newey_west_mean([0.4, 0.4, 0.4], lags=0)
        assert out["se"] == 0.0
        assert out["t"] is None
        assert out["p_value"] == 0.0

    def test_constant_nonpositive_is_p_one(self):
        for v in (0.0, -0.2):
            out = newey_west_mean([v, v, v], lags=1)
            assert out["p_value"] == 1.0
            assert out["t"] is None

    def test_refusals(self):
        with pytest.raises(ValueError, match="at least 2"):
            newey_west_mean([1.0])
        with pytest.raises(ValueError, match="lags"):
            newey_west_mean([1.0, 2.0], lags=2)
        with pytest.raises(ValueError, match="lags"):
            newey_west_mean([1.0, 2.0], lags=True)
        with pytest.raises(ValueError, match="finite"):
            newey_west_mean([1.0, float("inf")])


class TestNoInformationTest:
    def test_toy_h5_left_and_right_mspe(self):
        # The 12-pair h=5 walk-through: left ≈ 1e-6, right ≈ 5.58e-6.
        yhat = [
            0.004, 0.000, 0.001,
            -0.001, 0.003, 0.000,
            0.002, -0.001, 0.001,
            0.000, 0.001, 0.003,
        ]
        y = [
            0.005, -0.002, 0.000,
            -0.001, 0.004, 0.000,
            0.003, -0.002, 0.001,
            -0.001, 0.000, 0.004,
        ]
        out = no_information_test(y, yhat, lags=0)
        n = 12
        mu = sum(y) / n
        left = sum((yi - fi) ** 2 for yi, fi in zip(y, yhat)) / n
        right = sum((yi - mu) ** 2 for yi in y) / n
        assert out["mu"] == pytest.approx(mu)
        assert out["mspe_model"] == pytest.approx(left)
        assert out["mspe_mean"] == pytest.approx(right)
        assert out["mspe_model"] == pytest.approx(1e-6)
        assert out["mspe_mean"] == pytest.approx(5.58e-6, rel=0.02)
        assert out["beats_mean"] is True
        assert out["p_value"] < 0.05

    def test_train_mu_is_not_the_scored_sample_mean(self):
        y, yhat = [1.0, 3.0, 5.0, 7.0], [1.1, 2.9, 5.2, 6.8]
        sample = no_information_test(y, yhat)
        train = no_information_test(y, yhat, mu=0.0)
        assert sample["mu"] != train["mu"]
        assert train["mu"] == 0.0
        assert sample["mspe_mean"] != train["mspe_mean"]

    def test_constant_forecast_at_mu_does_not_beat_the_mean(self):
        y = [1.0, 2.0, 3.0, 4.0]
        out = no_information_test(y, [2.5] * 4, mu=2.5)
        assert out["beats_mean"] is False
        assert out["mspe_model"] == pytest.approx(out["mspe_mean"])
        assert out["p_value"] == 1.0
        assert out["t"] is None

    def test_clark_west_golden_pairs_with_newey_west(self):
        # μ=0, ŷ=1 → f_t = 2y = [1,2,3,4]; lags=1 → t=4.
        y = [0.5, 1.0, 1.5, 2.0]
        out = no_information_test(y, [1.0, 1.0, 1.0, 1.0], mu=0.0, lags=1, horizon=5)
        assert out["horizon"] == 5
        assert out["mean_adj"] == pytest.approx(2.5)
        assert out["t"] == pytest.approx(4.0)
        assert set(out) >= {
            "n",
            "mu",
            "mspe_model",
            "mspe_mean",
            "beats_mean",
            "mean_adj",
            "se",
            "t",
            "p_value",
            "lags",
            "horizon",
        }

    def test_horizon_omitted_from_the_result(self):
        out = no_information_test([1.0, 2.0], [1.0, 2.0])
        assert "horizon" not in out

    def test_scores_drive_the_existing_bootstrap(self):
        y = [0.1 * i for i in range(20)]
        yhat = [v + 0.05 for v in y]
        f = clark_west_series(y, yhat, mu=sum(y) / len(y))
        scores = {str(i): [fi] for i, fi in enumerate(f)}
        assert cluster_bootstrap_t(scores, 200, 0)["p_value"] < 0.05


class TestMaxInformativeHorizon:
    def test_stop_at_first_fail(self):
        out = max_informative_horizon(
            [
                {"horizon": 5, "p_value": 0.01},
                {"horizon": 10, "p_value": 0.04},
                {"horizon": 15, "p_value": 0.40},
            ]
        )
        assert out == {
            "h_star": 10,
            "rejected": [5, 10],
            "first_fail": 15,
            "alpha": 0.05,
            "n_horizons": 3,
        }

    def test_later_reject_after_a_fail_is_ignored(self):
        out = max_informative_horizon(
            [
                {"horizon": 5, "p_value": 0.20},
                {"horizon": 10, "p_value": 0.001},
            ]
        )
        assert out["h_star"] is None
        assert out["rejected"] == []
        assert out["first_fail"] == 5
        assert out["n_horizons"] == 2

    def test_all_reject_leaves_first_fail_none(self):
        out = max_informative_horizon(
            [
                {"horizon": 5, "p_value": 0.01},
                {"horizon": 10, "p_value": 0.02},
            ]
        )
        assert out["h_star"] == 10
        assert out["first_fail"] is None

    def test_p_equal_to_alpha_rejects(self):
        out = max_informative_horizon([{"horizon": 5, "p_value": 0.05}])
        assert out["h_star"] == 5

    def test_refusals(self):
        with pytest.raises(ValueError, match="empty"):
            max_informative_horizon([])
        with pytest.raises(ValueError, match="strictly increasing"):
            max_informative_horizon(
                [
                    {"horizon": 10, "p_value": 0.01},
                    {"horizon": 5, "p_value": 0.01},
                ]
            )
        with pytest.raises(ValueError, match="p_value"):
            max_informative_horizon([{"horizon": 5, "p_value": 1.2}])
        with pytest.raises(ValueError, match="alpha"):
            max_informative_horizon([{"horizon": 5, "p_value": 0.1}], alpha=0.0)
        with pytest.raises(ValueError, match="horizon"):
            max_informative_horizon([{"p_value": 0.1}])


# ---------------------------------------------------------------------------
# The Student tail: newly PUBLIC, so its preconditions and its most basic
# symmetry both need a test aimed straight at them (ADR-0151 review, M2/M5).
# ---------------------------------------------------------------------------


def _cauchy_sf(t):
    """P(T > t) for df = 1, in closed form — an INDEPENDENT computation."""
    return 0.5 - math.atan(t) / math.pi


def _df2_sf(t):
    """P(T > t) for df = 2, in closed form — an INDEPENDENT computation."""
    return 0.5 * (1.0 - t / math.sqrt(2.0 + t * t))


def _df4_sf(t):
    """P(T > t) for df = 4, in closed form — an INDEPENDENT computation."""
    x = t / math.sqrt(4.0 + t * t)
    return 0.5 * (1.0 - 1.5 * x + 0.5 * x**3)


class TestStudentTailSymmetry:
    """The property a sign bug deletes without failing anything else.

    ``_student_t_critical`` bisects upward from ``high = 1.0``, so it never
    probes ``t < 0``, and ``across_fold_t`` reports a one-sided tail from a
    statistic whose sign it does not vary. Dropping the ``if t > 0`` branch
    therefore used to pass the whole suite. These assertions do not go
    through either caller.
    """

    @pytest.mark.parametrize("t", [0.25, 0.5, 1.0, 2.0, 3.5, 7.0, 40.0])
    @pytest.mark.parametrize("df", [1, 2, 3, 8, 30, 250, 10000])
    def test_the_tail_is_symmetric_about_zero(self, t, df):
        assert student_t_sf(-t, df) == pytest.approx(1.0 - student_t_sf(t, df), abs=1e-12)

    @pytest.mark.parametrize("df", [1, 2, 3, 8, 30, 250, 10000])
    def test_zero_splits_the_mass_evenly(self, df):
        assert student_t_sf(0.0, df) == pytest.approx(0.5, abs=1e-12)

    @pytest.mark.parametrize("t", [-6.0, -2.0, -0.5, 0.0, 0.5, 2.0, 6.0])
    @pytest.mark.parametrize("df, closed_form", [(1, _cauchy_sf), (2, _df2_sf), (4, _df4_sf)])
    def test_both_tails_match_an_independent_closed_form(self, t, df, closed_form):
        # Restated from the distribution's own algebra rather than read back
        # from the implementation — an assertion sourced from its subject
        # asserts nothing. The NEGATIVE points are the ones that matter: a
        # dropped sign correction returns the upper tail for both signs.
        assert student_t_sf(t, df) == pytest.approx(closed_form(t), abs=1e-10)

    @pytest.mark.parametrize("df", [1, 2, 8, 100])
    def test_the_tail_decreases_over_the_whole_line(self, df):
        grid = [-8.0, -3.0, -1.0, -0.25, 0.0, 0.25, 1.0, 3.0, 8.0]
        tails = [student_t_sf(t, df) for t in grid]
        assert tails == sorted(tails, reverse=True)
        assert all(0.0 <= p <= 1.0 for p in tails)


class TestStudentTailEnforcesItsPreconditions:
    @pytest.mark.parametrize("bad", [0, 0.0, -1, -5, -0.5, float("nan"), float("inf"), None, "8"])
    def test_a_df_outside_the_documented_domain_refuses(self, bad):
        # Each of these used to be silently wrong or a bare ZeroDivisionError:
        # df=0 returned 0.0, df=-5 returned 0.5, df=-1 raised from inside the
        # arithmetic. A silently wrong probability is worse than a crash.
        with pytest.raises(ValueError, match="finite df > 0"):
            student_t_sf(1.0, bad)

    @pytest.mark.parametrize("bad", [float("nan"), float("inf"), -float("inf"), None, "1.0"])
    def test_a_non_finite_t_refuses(self, bad):
        with pytest.raises(ValueError, match="finite t"):
            student_t_sf(bad, 8)

    def test_an_ordinary_call_still_works(self):
        assert student_t_sf(0.0, 8) == pytest.approx(0.5)


class TestPnlSeriesSummaries:
    """``lower_tail_mean`` / ``max_drawdown`` (moved from index_options, ADR-0182)."""

    def test_lower_tail_mean_is_the_worst_share(self):
        assert lower_tail_mean([5, 1, 2, 3, 4, -1, 0, 6, 7, 8], 0.8) == pytest.approx(-0.5)
        assert lower_tail_mean([3.0], 0.95) == 3.0  # the tail is never empty

    def test_lower_tail_mean_counts_the_tail_exactly(self):
        # (1 - 0.95) * 200 is 10.000000000000009 in floating point: 10 values, not 11
        assert lower_tail_mean(list(range(200)), 0.95) == pytest.approx(4.5)
        # 5% of 3 rounds UP to one value
        assert lower_tail_mean([10.0, -7.0, 2.0], 0.95) == -7.0

    @pytest.mark.parametrize("values, alpha", [
        ([], 0.95), ([1.0, float("nan")], 0.95), ([1.0], 0.0), ([1.0], 1.0),
        ([1.0], True), ([1.0], float("nan")),
    ])
    def test_lower_tail_mean_refusals(self, values, alpha):
        with pytest.raises(ValueError):
            lower_tail_mean(values, alpha)

    def test_max_drawdown_from_a_zero_baseline(self):
        assert max_drawdown([10.0, -25.0, 5.0]) == pytest.approx(25.0)
        assert max_drawdown([-3.0, -2.0, 4.0, -1.0]) == pytest.approx(5.0)
        assert max_drawdown([1.0, 2.0, 3.0]) == 0.0
        assert max_drawdown([]) == 0.0
        assert max_drawdown(iter([5.0, -1.0])) == pytest.approx(1.0)

    def test_max_drawdown_refuses_a_non_finite_increment(self):
        with pytest.raises(ValueError, match="finite"):
            max_drawdown([1.0, float("inf")])


class TestPerformanceEstimators:
    """sharpe_ratio / PSR / DSR / profit_factor / payoff_ratio (ADR-0183), vs hand values."""

    RETURNS = [0.01, 0.02, 0.03, 0.04]

    def test_sharpe_matches_the_hand_computation(self):
        out = sharpe_ratio(self.RETURNS)
        sd = math.sqrt(sum((r - 0.025) ** 2 for r in self.RETURNS) / 3)
        sr = 0.025 / sd
        assert out["n"] == 4
        assert out["sharpe"] == pytest.approx(sr)
        assert out["skew"] == pytest.approx(0.0, abs=1e-9)
        assert out["kurtosis"] == pytest.approx(1.64)  # m4 / m2^2 of the four points
        se = math.sqrt((1 + (1.64 - 1) / 4 * sr * sr) / 3)
        assert out["se"] == pytest.approx(se)
        assert out["ci_low"] == pytest.approx(sr - 1.959963984540054 * se)
        assert out["ci_high"] == pytest.approx(sr + 1.959963984540054 * se)

    def test_a_flat_series_has_no_sharpe(self):
        out = sharpe_ratio([0.01, 0.01, 0.01])
        assert out["sharpe"] is None and out["ci_low"] is None and out["n"] == 3

    @pytest.mark.parametrize("values, alpha", [
        ([0.01], 0.05), ([0.01, float("nan")], 0.05), ([0.01, 0.02], 0.0), ("ab", 0.05),
    ])
    def test_sharpe_refusals(self, values, alpha):
        with pytest.raises(ValueError):
            sharpe_ratio(values, alpha)

    def test_psr_is_one_half_at_its_own_estimate(self):
        sr = sharpe_ratio(self.RETURNS)["sharpe"]
        assert probabilistic_sharpe_ratio(self.RETURNS, sr) == pytest.approx(0.5)
        assert probabilistic_sharpe_ratio(self.RETURNS, 0.0) > 0.9
        assert probabilistic_sharpe_ratio([0.01, 0.01], 0.0) is None

    def test_dsr_with_one_trial_is_the_psr_against_zero(self):
        out = deflated_sharpe_ratio(self.RETURNS, 1)
        assert out["expected_max_sharpe"] == 0.0
        assert out["dsr"] == pytest.approx(probabilistic_sharpe_ratio(self.RETURNS, 0.0))

    def test_dsr_raises_the_bar_with_more_trials(self):
        few = deflated_sharpe_ratio(self.RETURNS, 2, sharpe_variance=0.25)
        many = deflated_sharpe_ratio(self.RETURNS, 100, sharpe_variance=0.25)
        g = 0.5772156649015329
        inv = NormalDist().inv_cdf
        expected = 0.5 * ((1 - g) * inv(1 - 1 / 100) + g * inv(1 - 1 / (100 * math.e)))
        assert many["expected_max_sharpe"] == pytest.approx(expected)
        assert many["dsr"] < few["dsr"]

    @pytest.mark.parametrize("trials, variance", [(0, None), (True, None), (2, -1.0)])
    def test_dsr_refusals(self, trials, variance):
        with pytest.raises(ValueError):
            deflated_sharpe_ratio(self.RETURNS, trials, variance)

    def test_profit_factor_and_payoff(self):
        assert profit_factor([2.0, -1.0, 3.0, -1.0]) == pytest.approx(2.5)
        assert payoff_ratio([2.0, -1.0, 3.0, -1.0]) == pytest.approx(2.5)
        assert payoff_ratio([4.0, 2.0, -1.0, -2.0, -3.0]) == pytest.approx(1.5)
        assert profit_factor([1.0, 2.0]) is None  # no loss: unbounded
        assert payoff_ratio([1.0, 2.0]) is None
        assert profit_factor([-1.0]) == 0.0

    @pytest.mark.parametrize("fn, values", [
        (profit_factor, []), (payoff_ratio, [float("inf")]), (profit_factor, "12"),
    ])
    def test_trade_ratio_refusals(self, fn, values):
        with pytest.raises(ValueError):
            fn(values)


# --- ADR-0183 phase 2: the one owner of the equal-count bucket rule --------


def test_quantile_edges_are_equal_count_cut_points():
    from dskit.pipeline.stats import quantile_bin, quantile_edges

    values = list(range(1, 101))
    edges = quantile_edges(values, 10)
    assert len(edges) == 9
    counts = [0] * 10
    for value in values:
        counts[quantile_bin(value, edges)] += 1
    assert counts == [10] * 10
    assert quantile_edges([1.0], 10) == []
    assert quantile_edges(values, 1) == []


def test_quantile_bin_puts_a_cut_point_in_the_bucket_above():
    from dskit.pipeline.stats import quantile_bin

    assert quantile_bin(2.0, [1.0, 2.0, 3.0]) == 2
    assert quantile_bin(0.5, [1.0, 2.0, 3.0]) == 0
    assert quantile_bin(9.0, [1.0, 2.0, 3.0]) == 3
    assert quantile_bin(1.0, []) == 0


def test_monitors_bin_through_the_pipeline_owner():
    from dskit.production import monitors

    assert not hasattr(monitors, "_quantile_edges")


# --- ADR-0194: the expanding mid-rank percentile ---------------------------


def _percentile_oracle(values, min_history):
    """The rule written out by brute force: mid-rank among strictly earlier finite values."""
    out = []
    for i, value in enumerate(values):
        prior = [v for v in values[:i] if v is not None and math.isfinite(v)]
        if value is None or not math.isfinite(value) or len(prior) < min_history:
            out.append(None)
            continue
        below = sum(v < value for v in prior)
        equal = sum(v == value for v in prior)
        out.append((below + 0.5 * equal) / len(prior))
    return out


def test_expanding_percentile_hand_cases_ties_and_missing_values():
    from dskit.pipeline.stats import expanding_percentile

    values = [3.0, 1.0, 2.0, 2.0, 5.0, None, float("nan"), 2.0, float("inf"), 0.0]
    # i2: 2 among [1, 3] = 1/2;  i3: 2 among [1, 2, 3] = (1 + 1/2) / 3;  i4: 5 tops all four;
    # None / nan / inf give None and never enter the history;  i7: 2 among [1, 2, 2, 3, 5]
    # = (1 + 2/2) / 5;  i9: 0 is below all six earlier finite values (inf is not one)
    assert expanding_percentile(values, 2) == pytest.approx(
        [None, None, 0.5, 0.5, 1.0, None, None, 0.4, None, 0.0])


def test_expanding_percentile_mid_ranks_ties_at_one_half_and_orders_the_extremes():
    from dskit.pipeline.stats import expanding_percentile

    assert expanding_percentile([7.0] * 4, 1) == [None, 0.5, 0.5, 0.5]
    assert expanding_percentile([1.0, 2.0, 3.0, 4.0], 1) == [None, 1.0, 1.0, 1.0]
    assert expanding_percentile([4.0, 3.0, 2.0, 1.0], 1) == [None, 0.0, 0.0, 0.0]
    assert expanding_percentile([1, 2, 1], 1) == [None, 1.0, 0.25]  # ints are numbers: (0 + 1/2) / 2


def test_expanding_percentile_needs_min_history_earlier_finite_values_exactly():
    from dskit.pipeline.stats import expanding_percentile

    values = [1.0, None, 2.0, float("nan"), 3.0, 4.0]
    # min_history 3: the third finite value (3.0, index 4) has only two finite predecessors,
    # so the first defined position is index 5, whose three predecessors are 1, 2, 3
    assert expanding_percentile(values, 3) == [None, None, None, None, None, 1.0]
    assert expanding_percentile(values, 2)[4] == 1.0 and expanding_percentile(values, 2)[3] is None
    assert expanding_percentile(values, 4) == [None] * 6      # only three predecessors ever
    assert expanding_percentile([], 1) == []


def test_expanding_percentile_matches_the_brute_force_rule_on_random_tied_series():
    import random

    from dskit.pipeline.stats import expanding_percentile

    rng = random.Random(194)
    for _ in range(60):
        values = [rng.choice([None, float("nan"), float(rng.randint(0, 6)), rng.random()])
                  for _ in range(rng.randint(0, 40))]
        for min_history in (1, 3, 10):
            assert expanding_percentile(values, min_history) == \
                _percentile_oracle(values, min_history)


def test_expanding_percentile_never_looks_ahead():
    import random

    from dskit.pipeline.stats import expanding_percentile

    rng = random.Random(1940)
    values = [rng.choice([None, float(rng.randint(0, 9))]) for _ in range(60)]
    full = expanding_percentile(values, 3)
    for k in range(len(values) + 1):     # a prefix is scored the same as inside the whole
        assert expanding_percentile(values[:k], 3) == full[:k]
    changed = values[:30] + [1e9 if v is not None else 1.0 for v in values[30:]]
    assert expanding_percentile(changed, 3)[:30] == full[:30]   # later values never move earlier ones
    # a later value does not even move its own predecessors' scores when appended
    assert expanding_percentile(values + [123.0], 3)[:len(values)] == full


@pytest.mark.parametrize("min_history", [0, -1, True, False, 2.0, "3", None])
def test_expanding_percentile_refuses_a_min_history_that_is_not_a_positive_int(min_history):
    from dskit.pipeline.stats import expanding_percentile

    with pytest.raises(ValueError, match="min_history"):
        expanding_percentile([1.0, 2.0], min_history)


@pytest.mark.parametrize("values", [(1.0, 2.0), None, "12", {1: 2.0}, iter([1.0, 2.0])])
def test_expanding_percentile_refuses_anything_but_a_list(values):
    from dskit.pipeline.stats import expanding_percentile

    with pytest.raises(ValueError, match="values must be a list"):
        expanding_percentile(values, 1)


@pytest.mark.parametrize("bad", ["1.0", True, [1.0], {}])
def test_expanding_percentile_refuses_a_cell_that_is_neither_a_number_nor_missing(bad):
    from dskit.pipeline.stats import expanding_percentile

    with pytest.raises(ValueError, match=r"values\[1\]"):
        expanding_percentile([1.0, bad, 2.0], 1)


def test_expanding_percentile_is_public():
    from dskit.pipeline import stats

    assert "expanding_percentile" in stats.__all__


# --- ADR-0196: expanding_quantile, the percentile's sibling ---------------------------------------
#
# The q-quantile of the STRICTLY EARLIER finite values, linear interpolation between order
# statistics (position q (n - 1) in the ascending list; the fraction between its two neighbours).


def _quantile_oracle(values, q, min_history):
    """The rule written out from the definition, as a weighted average of the two neighbours."""
    out = []
    for i, value in enumerate(values):
        prior = sorted(v for v in values[:i] if v is not None and math.isfinite(v))
        if value is None or not math.isfinite(value) or len(prior) < min_history:
            out.append(None)
            continue
        position = q * (len(prior) - 1)
        low = min(math.floor(position), len(prior) - 1)
        high = min(low + 1, len(prior) - 1)
        out.append(prior[low] * (1 - (position - low)) + prior[high] * (position - low))
    return out


#: Hand cases: 4 3 and 10 have earlier finites [1, 4], [1, 3, 4], ... (None, nan and inf are missing).
QUANTILE_SERIES = [4.0, 1.0, 3.0, 10.0, None, float("nan"), 2.0, float("inf"), 7.0, 0.0]


def test_expanding_quantile_hand_cases_at_the_median_and_the_lower_quartile():
    from dskit.pipeline.stats import expanding_quantile

    # median, min_history 2. Earlier finite values, and the interpolation at q (n - 1):
    #   i2 (3):  [1, 4]              1.0 -> 2.5                 i3 (10): [1, 3, 4]        -> 3.0
    #   i6 (2):  [1, 3, 4, 10]       1.5 -> 3 + 0.5 * 1 = 3.5   i8 (7):  [1, 2, 3, 4, 10] -> 3.0
    #   i9 (0):  [1, 2, 3, 4, 7, 10] 2.5 -> 3 + 0.5 * 1 = 3.5
    assert expanding_quantile(QUANTILE_SERIES, 0.5, 2) == pytest.approx(
        [None, None, 2.5, 3.0, None, None, 3.5, None, 3.0, 3.5])
    # lower quartile: i2 0.25 -> 1 + 0.25 * 3 = 1.75     i3 0.5 -> 1 + 0.5 * 2 = 2.0
    #   i6 0.75 -> 1 + 0.75 * 2 = 2.5     i8 1.0 -> exactly 2.0     i9 1.25 -> 2 + 0.25 * 1 = 2.25
    assert expanding_quantile(QUANTILE_SERIES, 0.25, 2) == pytest.approx(
        [None, None, 1.75, 2.0, None, None, 2.5, None, 2.0, 2.25])


def test_expanding_quantile_zero_and_one_are_the_earlier_minimum_and_maximum_exactly():
    from dskit.pipeline.stats import expanding_quantile

    assert expanding_quantile([5.0, 3.0, 9.0, 1.0], 0, 1) == [None, 5.0, 3.0, 3.0]
    assert expanding_quantile([5.0, 3.0, 9.0, 1.0], 1, 1) == [None, 5.0, 5.0, 9.0]
    assert expanding_quantile([5.0, 3.0, 9.0, 1.0], 0.5, 1) == [None, 5.0, 4.0, 5.0]
    assert expanding_quantile([5, 3, 9, 1], 1, 1) == [None, 5.0, 5.0, 9.0]    # ints are numbers
    assert all(isinstance(v, float) for v in expanding_quantile([5, 3, 9, 1], 1, 1)[1:])


@pytest.mark.parametrize("q", [0.0, 0.1, 0.3, 0.5, 0.9, 1.0])
def test_expanding_quantile_of_tied_values_is_that_value_exactly(q):
    from dskit.pipeline.stats import expanding_quantile

    assert expanding_quantile([7.0] * 5, q, 1) == [None, 7.0, 7.0, 7.0, 7.0]
    assert expanding_quantile([0.1, 0.1, 0.1, 0.1], q, 2) == [None, None, 0.1, 0.1]  # no float noise
    # ties inside a longer history: the two neighbours are equal, so the fraction is moot
    assert expanding_quantile([1.0, 2.0, 2.0, 2.0, 3.0, 9.0], 0.5, 1)[5] == 2.0


def test_expanding_quantile_needs_min_history_earlier_finite_values_exactly():
    from dskit.pipeline.stats import expanding_quantile

    values = [1.0, None, 2.0, float("nan"), 3.0, 4.0]
    assert expanding_quantile(values, 0.5, 3) == [None, None, None, None, None, 2.0]
    assert expanding_quantile(values, 0.5, 2)[4] == 1.5 and expanding_quantile(values, 0.5, 2)[3] is None
    assert expanding_quantile(values, 0.5, 4) == [None] * 6       # only three predecessors ever
    assert expanding_quantile([], 0.5, 1) == []


def test_expanding_quantile_matches_the_definition_and_the_standard_library_on_random_series():
    import random
    import statistics

    from dskit.pipeline.stats import expanding_quantile

    rng = random.Random(196)
    for _ in range(60):
        values = [rng.choice([None, float("nan"), float(rng.randint(0, 6)), rng.random() * 10])
                  for _ in range(rng.randint(0, 40))]
        for q in (0.0, 0.1, 0.25, 0.5, 0.77, 1.0):
            for min_history in (1, 3, 10):
                assert expanding_quantile(values, q, min_history) == pytest.approx(
                    _quantile_oracle(values, q, min_history), rel=1e-12, abs=1e-12)
    # the library's inclusive method is the same linear interpolation: its quartiles at 0.25/0.5/0.75
    values = [rng.random() for _ in range(30)]
    for q, cut in ((0.25, 0), (0.5, 1), (0.75, 2)):
        got = expanding_quantile(values, q, 2)
        for i in range(2, 30):
            want = statistics.quantiles(values[:i], n=4, method="inclusive")[cut]
            assert got[i] == pytest.approx(want, rel=1e-12)


def test_expanding_quantile_never_looks_ahead():
    import random

    from dskit.pipeline.stats import expanding_quantile

    rng = random.Random(1960)
    values = [rng.choice([None, float(rng.randint(0, 9))]) for _ in range(60)]
    for q in (0.0, 0.5, 0.8, 1.0):
        full = expanding_quantile(values, q, 3)
        for k in range(len(values) + 1):     # a prefix is scored the same as inside the whole
            assert expanding_quantile(values[:k], q, 3) == full[:k]
        changed = values[:30] + [1e9 if v is not None else 1.0 for v in values[30:]]
        assert expanding_quantile(changed, q, 3)[:30] == full[:30]
        assert expanding_quantile(values + [123.0], q, 3)[:len(values)] == full
    # a position's own value never enters its own quantile
    assert expanding_quantile([1.0, 2.0, 3.0, 1e12], 1, 1)[3] == 3.0


@pytest.mark.parametrize("function", ["expanding_percentile", "expanding_quantile"])
@pytest.mark.parametrize("min_history", [0, -1, True, False, 2.0, "3", None])
def test_both_expanding_functions_refuse_a_min_history_that_is_not_a_positive_int(
    function, min_history
):
    from dskit.pipeline import stats

    args = (0.5,) if function == "expanding_quantile" else ()
    with pytest.raises(ValueError, match="min_history must be an int >= 1"):
        getattr(stats, function)([1.0, 2.0], *args, min_history)


@pytest.mark.parametrize("function", ["expanding_percentile", "expanding_quantile"])
@pytest.mark.parametrize("values", [(1.0, 2.0), None, "12", {1: 2.0}, iter([1.0, 2.0])])
def test_both_expanding_functions_refuse_anything_but_a_list(function, values):
    from dskit.pipeline import stats

    args = (0.5,) if function == "expanding_quantile" else ()
    with pytest.raises(ValueError, match="values must be a list"):
        getattr(stats, function)(values, *args, 1)


@pytest.mark.parametrize("function", ["expanding_percentile", "expanding_quantile"])
@pytest.mark.parametrize("bad", ["1.0", True, [1.0], {}])
def test_both_expanding_functions_refuse_a_cell_that_is_neither_a_number_nor_missing(function, bad):
    from dskit.pipeline import stats

    args = (0.5,) if function == "expanding_quantile" else ()
    with pytest.raises(ValueError, match=r"values\[1\]"):
        getattr(stats, function)([1.0, bad, 2.0], *args, 1)


@pytest.mark.parametrize("q", [-0.1, 1.0000001, float("nan"), float("inf"), True, False, "0.5",
                               None, [0.5]])
def test_expanding_quantile_refuses_a_q_outside_zero_to_one(q):
    from dskit.pipeline.stats import expanding_quantile

    with pytest.raises(ValueError, match=r"q must be a number in \[0, 1\]"):
        expanding_quantile([1.0, 2.0], q, 1)


def test_expanding_quantile_is_public():
    from dskit.pipeline import stats

    assert "expanding_quantile" in stats.__all__


# --- ADR-0195 (ADR-0194 review B-M3/B-M4): one owner of the "no variance" rule ------------------
#
# A series whose spread is at most a named RELATIVE tolerance of its largest magnitude is constant
# to float noise (equal credits summed in a different order), so its t is undefined (None) in both
# t-tests, never a 1e16 rounding artifact.

#: Three equal per-share credits accumulated in different orders differ in the last bit only.
NOISY = [1.7000000000000004, 1.7000000000000004, 1.7]


def _series_with_spread(spread_over_tolerance, magnitude=1.0):
    """Two values whose spread is ``spread_over_tolerance`` x the named tolerance x ``magnitude``."""
    from dskit.pipeline import stats

    return [magnitude, magnitude - spread_over_tolerance * stats.NO_VARIANCE_RTOL * magnitude]


@pytest.mark.parametrize("t_test", ["newey_west_mean", "across_fold_t"])
def test_a_series_constant_to_float_noise_has_no_variance_in_both_t_tests(t_test):
    from dskit.pipeline import stats

    test = getattr(stats, t_test)
    assert max(NOISY) - min(NOISY) > 0.0        # the reproducer really is not exactly constant
    out = test(NOISY)
    assert out["t"] is None and out["se"] == 0.0 and out["p_value"] == 0.0   # positive mean
    assert out["mean"] == pytest.approx(1.7)


@pytest.mark.parametrize("t_test", ["newey_west_mean", "across_fold_t"])
def test_a_negative_constant_series_has_no_variance_and_takes_the_other_p_value(t_test):
    from dskit.pipeline import stats

    test = getattr(stats, t_test)
    out = test([-v for v in NOISY])              # the magnitude, not the signed maximum, scales it
    assert out["t"] is None and out["p_value"] == 1.0
    assert test([-1.7, -1.7, -1.7])["t"] is None and test([0.0, 0.0])["t"] is None


def _t_by_hand(values, ddof):
    """mean / (sd / sqrt(n)) written out: sd on divisor ``n - ddof`` (0 = Newey-West lags 0)."""
    n = len(values)
    mean = sum(values) / n
    return mean / (math.sqrt(sum((v - mean) ** 2 for v in values) / (n - ddof)) / math.sqrt(n))


@pytest.mark.parametrize("t_test, ddof", [("newey_west_mean", 0), ("across_fold_t", 1)])
def test_a_genuinely_varying_small_or_offset_series_keeps_its_t(t_test, ddof):
    import random

    from dskit.pipeline import stats

    test = getattr(stats, t_test)
    out = test([100.0, 100.01])                  # spread 1e-4 of the magnitude: real variance
    assert out["t"] == pytest.approx(_t_by_hand([100.0, 100.01], ddof), rel=1e-9)
    assert out["t"] > 1e4
    rng = random.Random(195)
    draws = [rng.gauss(1000.0, 0.5) for _ in range(20)]     # sd 0.5 around 1000: 5e-4 relative
    out = test(draws)
    assert out["t"] == pytest.approx(_t_by_hand(draws, ddof), rel=1e-9)
    assert out["se"] > 0.0 and out["t"] > 100.0
    tiny = [1e-9, 2e-9, 3e-9]                    # small in absolute terms, not in relative ones
    assert test(tiny)["t"] == pytest.approx(_t_by_hand(tiny, ddof), rel=1e-9)
    assert test([1e9 * v for v in tiny])["t"] == pytest.approx(test(tiny)["t"])


@pytest.mark.parametrize("t_test", ["newey_west_mean", "across_fold_t"])
def test_the_tolerance_is_relative_and_named_once(t_test):
    from dskit.pipeline import stats

    test = getattr(stats, t_test)
    assert "NO_VARIANCE_RTOL" in stats.__all__
    assert 1e-13 < stats.NO_VARIANCE_RTOL < 1e-9    # far above one ulp, far below any real spread
    for magnitude in (1e-6, 1.0, 1e9):               # scale-free: the same verdict at every scale
        assert test(_series_with_spread(0.5, magnitude))["t"] is None    # inside the tolerance
        assert test(_series_with_spread(2.0, magnitude))["t"] is not None  # outside it
        assert test([-v for v in _series_with_spread(0.5, magnitude)])["t"] is None
        assert test([-v for v in _series_with_spread(2.0, magnitude)])["t"] is not None


def test_the_no_variance_tolerance_is_one_named_value_pinned_against_a_literal():
    from dskit.pipeline import stats

    assert stats.NO_VARIANCE_RTOL == 1e-12       # restated here: changing it moves every t-test


#: At 1e12 one ulp is 2**-13, so a spread of exactly 1.0 sits ON the boundary
#: (``1e-12 * 1e12 == 1.0``) and ``1.0 + 2**-13`` is the very next spread above it.
BOUNDARY = [1e12, 1e12 - 1.0]
JUST_OVER = [1e12, 1e12 - 1.0 - 2.0 ** -13]


@pytest.mark.parametrize("t_test", ["newey_west_mean", "across_fold_t"])
@pytest.mark.parametrize("order", [1, -1], ids=["descending", "ascending"])
def test_a_spread_exactly_on_the_tolerance_has_no_variance_in_either_order(t_test, order):
    # B-F1 (ADR-0195 review): "at most" includes equality, and the scale is the LARGEST
    # magnitude whichever end of the series it sits at (a first-, last-, smallest- or
    # mean-magnitude norm would scale by 1e12 - 1 or 1e12 - 0.5 and let t through)
    from dskit.pipeline import stats

    out = getattr(stats, t_test)(BOUNDARY[::order])
    assert out["t"] is None and out["se"] == 0.0 and out["p_value"] == 0.0


@pytest.mark.parametrize("t_test", ["newey_west_mean", "across_fold_t"])
@pytest.mark.parametrize("order", [1, -1], ids=["descending", "ascending"])
def test_the_next_representable_spread_above_the_tolerance_keeps_its_t(t_test, order):
    from dskit.pipeline import stats

    out = getattr(stats, t_test)(JUST_OVER[::order])
    assert out["t"] is not None and out["t"] > 1e12 and out["se"] > 0.0


def test_the_lag_path_and_the_dm_wrapper_inherit_the_noise_rule():
    from dskit.pipeline import stats

    assert stats.newey_west_mean(NOISY, lags=1)["t"] is None
    assert stats.diebold_mariano_test(NOISY)["t"] is None


class TestCalendarBlockBootstrap:
    def sampler(self, days, width):
        from datetime import date
        from dskit.pipeline import stats

        cls = getattr(stats, "CalendarBlockBootstrap", None)
        assert cls is not None, "complete-calendar sampler is missing"
        return cls([date(2024, 1, d) for d in days], width)

    def test_complete_calendar_spans_and_deterministic_group_draws(self):
        sampler = self.sampler([1, 2, 4, 5], 2)
        assert sampler.supported
        assert sampler.evidence["eligible_starts"] == [
            "2024-01-01", "2024-01-02", "2024-01-04"]
        draws = sampler.sample_indices(200, 17)
        assert draws == sampler.sample_indices(200, 17)
        assert all(len(draw) == 4 for draw in draws)
        assert set(i for draw in draws for i in draw) == {0, 1, 2, 3}
        # Every complete block is (0,1), (1,), or (2,3), never (3,0).
        # Parse each draw using the uniquely determined first date index.
        for draw in draws:
            pos = 0
            while pos < len(draw):
                block = {0: [0, 1], 1: [1], 2: [2, 3]}[draw[pos]]
                taken = min(len(block), len(draw) - pos)
                assert draw[pos:pos+taken] == block[:taken]
                pos += taken

    @pytest.mark.parametrize("days,width,zero", [
        ([1, 3], 2, ["2024-01-03"]),
        ([1, 2], 3, ["2024-01-01", "2024-01-02"]),
        ([1], 1, []),
    ])
    def test_unsupported_history_preserves_evidence_and_refuses_draw(self, days, width, zero):
        sampler = self.sampler(days, width)
        assert not sampler.supported
        assert sampler.evidence["zero_inclusion_dates"] == zero
        with pytest.raises(ValueError, match="support"):
            sampler.sample_indices(20, 1)

    def test_endpoint_complete_block_includes_last_observed_date(self):
        sampler = self.sampler([1, 2, 3, 4], 2)
        assert sampler.evidence["inclusion_counts"] == [1, 2, 2, 1]
        assert sampler.evidence["distinct_blocks"] == 3

    @pytest.mark.parametrize("width", [0, -1, True, 1.5])
    def test_invalid_width_refuses(self, width):
        with pytest.raises(ValueError, match="block_days"):
            self.sampler([1, 2], width)

    def test_unsorted_duplicate_or_non_date_inputs_refuse(self):
        from datetime import date, datetime
        from dskit.pipeline.stats import CalendarBlockBootstrap

        for dates in ([date(2024,1,2), date(2024,1,1)],
                      [date(2024,1,1)] * 2, ["2024-01-01"],
                      [datetime(2024,1,1)], []):
            with pytest.raises(ValueError, match="dates"):
                CalendarBlockBootstrap(dates, 1)


class TestEdgePaddedCalendarBlockBootstrap:
    def padded(self, days, width, month=1):
        from datetime import date
        from dskit.pipeline.stats import EdgePaddedCalendarBlockBootstrap

        return EdgePaddedCalendarBlockBootstrap(
            [date(2024, month, d) for d in days], width)

    def legacy(self, days, width):
        from datetime import date
        from dskit.pipeline.stats import CalendarBlockBootstrap

        return CalendarBlockBootstrap([date(2024, 1, d) for d in days], width)

    def test_legacy_class_is_byte_identical_to_pre_hook_behaviour(self):
        sampler = self.legacy([1, 2, 3, 5, 6, 7], 2)
        assert sampler.evidence == {
            "block_days": 2, "date_count": 6,
            "eligible_starts": ["2024-01-01", "2024-01-02", "2024-01-03",
                                "2024-01-05", "2024-01-06"],
            "distinct_blocks": 5, "inclusion_counts": [1, 2, 2, 1, 2, 1],
            "zero_inclusion_dates": [], "supported": True}
        assert sampler.sample_indices(3, 5) == [
            [4, 5, 2, 2, 4, 5], [0, 1, 3, 4, 1, 2], [0, 1, 1, 2, 0, 1]]
        assert "truncated_blocks" not in sampler.evidence

    def test_trailing_date_uncovered_by_legacy_is_covered_when_padded(self):
        days = [1, 2, 4, 5, 8]
        legacy = self.legacy(days, 3)
        assert legacy.evidence["zero_inclusion_dates"] == ["2024-01-08"]
        assert not legacy.supported
        padded = self.padded(days, 3)
        evidence = padded.evidence
        assert evidence["zero_inclusion_dates"] == []
        assert evidence["inclusion_counts"] == [3] * 5
        assert padded.supported
        assert any(4 in draw for draw in padded.sample_indices(50, 3))

    def test_starts_are_every_calendar_day_from_first_minus_b_minus_1_to_last(self):
        evidence = self.padded([3, 4, 6], 3).evidence
        assert evidence["eligible_starts"] == [
            f"2024-01-{d:02d}" for d in range(1, 7)]
        # Starts 1,2 begin before day 3; starts 5,6 end after day 6.
        assert evidence["truncated_blocks"] == 4

    def test_blocks_are_half_open_intervals_intersected_with_observed_dates(self):
        sampler = self.padded([1, 2, 5], 2)
        # Starts Dec 31..Jan 5; empty blocks (start Jan 3 -> [3,5) has none
        # observed, start Jan 4 -> [4,6) has Jan 5) are dropped.
        assert sampler.evidence["eligible_starts"] == [
            "2023-12-31", "2024-01-01", "2024-01-02", "2024-01-04",
            "2024-01-05"]
        assert sampler._blocks == ((0,), (0, 1), (1,), (2,), (2,))

    def test_every_observed_date_lies_in_exactly_b_blocks(self):
        for days, width in (([1, 2, 5, 6, 7, 20], 4), ([1, 30], 31),
                            ([5], 1), ([1, 2, 3], 7)):
            counts = self.padded(days, width).evidence["inclusion_counts"]
            assert counts == [width] * len(days)

    def test_span_floor_requires_two_block_lengths(self):
        # span 4 days, B=3 -> floor(4/3) == 1: refuses although blocks differ.
        short = self.padded([1, 2, 3, 4], 3)
        assert len(set(short._blocks)) >= 2
        assert not short.supported
        assert not short.evidence["supported"]
        with pytest.raises(ValueError, match="support"):
            short.sample_indices(20, 1)
        # span 6, B=3 -> floor(6/3) == 2: supported.
        assert self.padded([1, 2, 5, 6], 3).supported

    def test_sample_indices_deterministic_and_full_length(self):
        sampler = self.padded([1, 2, 5, 6, 9], 2)
        draws = sampler.sample_indices(40, 11)
        assert draws == sampler.sample_indices(40, 11)
        assert all(len(draw) == 5 for draw in draws)

    def test_underflow_below_date_min_refuses(self):
        from datetime import date
        from dskit.pipeline.stats import EdgePaddedCalendarBlockBootstrap

        with pytest.raises(ValueError, match="earliest"):
            EdgePaddedCalendarBlockBootstrap([date.min, date(1, 1, 3)], 5)
        # Exactly representable padding is accepted.
        EdgePaddedCalendarBlockBootstrap([date(1, 1, 5), date(1, 1, 20)], 5)

    def test_valid_date_max_is_accepted(self):
        from datetime import date
        from dskit.pipeline.stats import EdgePaddedCalendarBlockBootstrap

        sampler = EdgePaddedCalendarBlockBootstrap(
            [date(9999, 12, 1), date.max], 3)
        assert sampler.evidence["eligible_starts"][-1] == "9999-12-31"
