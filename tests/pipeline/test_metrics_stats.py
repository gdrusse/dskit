"""Scoring rules + the cluster bootstrap / multiplicity machinery."""

import math

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
    max_informative_horizon,
    newey_west_mean,
    no_correction,
    no_information_test,
    register_correction,
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
