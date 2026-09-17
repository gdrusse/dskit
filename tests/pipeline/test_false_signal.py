"""The generic per-signal false-signal probability estimator (ADR-0149)."""

import math

import pytest

from dskit.pipeline.false_signal import (
    DEFAULT_CONFIDENCE,
    DEFAULT_NULL_THRESHOLD,
    ESTIMATORS,
    FalseSignalEstimate,
    FalseSignalEstimator,
    GrenanderLocalFdr,
    SignalEvidence,
    clopper_pearson_upper,
    estimator,
    permutation_pvalue,
    register_estimator,
)
from dskit.pipeline.stats import regularized_incomplete_beta


def binomial_cdf(k, n, p):
    """P(X <= k) for X ~ Binomial(n, p), restated independently of the module."""
    return sum(math.comb(n, i) * p**i * (1.0 - p) ** (n - i) for i in range(k + 1))


def family(strong=3, weak=7, draws=99, seed=0):
    """A synthetic family: `strong` signals beat every null, `weak` sit inside it."""
    out = {}
    nulls = [math.sin(seed + i) for i in range(draws)]
    for i in range(strong):
        out[f"s{i}"] = SignalEvidence(10.0 + i, nulls)
    for i in range(weak):
        out[f"w{i}"] = SignalEvidence(nulls[i % draws], nulls)
    return out


class TestSignalEvidence:
    def test_it_keeps_the_statistic_and_freezes_the_draws(self):
        ev = SignalEvidence(2.0, [0.1, 0.2])
        assert ev.statistic == 2.0
        assert ev.null_draws == (0.1, 0.2)

    def test_it_is_frozen(self):
        ev = SignalEvidence(2.0, [0.1])
        with pytest.raises(Exception):
            ev.statistic = 3.0

    def test_a_non_finite_statistic_cannot_exist(self):
        with pytest.raises(ValueError, match="statistic"):
            SignalEvidence(float("nan"), [0.1])
        with pytest.raises(ValueError, match="statistic"):
            SignalEvidence(float("inf"), [0.1])

    def test_an_empty_draw_set_cannot_exist(self):
        with pytest.raises(ValueError, match="null_draws"):
            SignalEvidence(2.0, [])

    def test_a_non_finite_draw_is_named(self):
        with pytest.raises(ValueError, match="null_draws\\[1\\]"):
            SignalEvidence(2.0, [0.1, float("nan")])


class TestPermutationPvalue:
    def test_the_add_one_rule(self):
        # No draw reaches the statistic: (1 + 0) / (1 + 3).
        assert permutation_pvalue(5.0, [1.0, 2.0, 3.0]) == pytest.approx(0.25)

    def test_ties_count_against_the_signal(self):
        # {1, 3, 5} has two draws >= 3 -> (1 + 2) / (1 + 3).
        assert permutation_pvalue(3.0, [1.0, 3.0, 5.0]) == pytest.approx(0.75)

    def test_it_is_never_zero(self):
        p = permutation_pvalue(1e9, [0.0] * 9)
        assert p == pytest.approx(0.1)
        assert p > 0.0

    def test_it_is_never_above_one(self):
        assert permutation_pvalue(-1e9, [0.0] * 9) == pytest.approx(1.0)

    def test_it_refuses_an_empty_null(self):
        with pytest.raises(ValueError, match="null_draws"):
            permutation_pvalue(1.0, [])

    def test_it_refuses_a_non_finite_statistic(self):
        with pytest.raises(ValueError, match="statistic"):
            permutation_pvalue(float("nan"), [1.0])


class TestClopperPearsonUpper:
    def test_zero_successes_has_the_closed_form(self):
        # U solves (1 - U) ** n = 1 - confidence.
        assert clopper_pearson_upper(0, 10, 0.95) == pytest.approx(
            1.0 - 0.05 ** (1 / 10), abs=1e-9
        )

    def test_all_successes_is_one(self):
        assert clopper_pearson_upper(7, 7, 0.95) == 1.0

    @pytest.mark.parametrize("k,n", [(1, 10), (3, 20), (17, 40), (99, 200)])
    def test_it_inverts_the_binomial_tail(self, k, n):
        u = clopper_pearson_upper(k, n, 0.95)
        assert binomial_cdf(k, n, u) == pytest.approx(0.05, abs=1e-6)

    def test_it_never_falls_below_the_point_estimate(self):
        for k, n in [(0, 5), (2, 5), (5, 5), (13, 50)]:
            assert clopper_pearson_upper(k, n, 0.9) >= k / n

    def test_more_trials_tighten_the_bound(self):
        wide = clopper_pearson_upper(5, 10, 0.95)
        tight = clopper_pearson_upper(50, 100, 0.95)
        assert tight < wide

    def test_it_refuses_impossible_counts(self):
        with pytest.raises(ValueError, match="successes"):
            clopper_pearson_upper(11, 10, 0.95)
        with pytest.raises(ValueError, match="successes"):
            clopper_pearson_upper(-1, 10, 0.95)
        with pytest.raises(ValueError, match="trials"):
            clopper_pearson_upper(0, 0, 0.95)
        with pytest.raises(ValueError, match="confidence"):
            clopper_pearson_upper(1, 10, 1.0)

    def test_the_beta_tail_has_one_public_owner(self):
        assert regularized_incomplete_beta(1.0, 1.0, 0.25) == pytest.approx(0.25)


class TestEstimateContract:
    """Exactly what `forecast_bundle` validates, restated independently."""

    def test_every_pi_is_a_probability_and_the_bound_covers_the_point(self):
        est = GrenanderLocalFdr()
        out = est.estimate(family(), independent_units=10)
        assert set(out.pi_hat) == set(out.pi_upper) == set(family())
        for name, hat in out.pi_hat.items():
            upper = out.pi_upper[name]
            assert isinstance(hat, float) and isinstance(upper, float)
            assert math.isfinite(hat) and math.isfinite(upper)
            assert 0.0 <= hat <= 1.0
            assert 0.0 <= upper <= 1.0
            assert hat <= upper

    def test_it_returns_the_estimate_value(self):
        out = GrenanderLocalFdr().estimate(family(), independent_units=10)
        assert isinstance(out, FalseSignalEstimate)
        for key in (
            "estimator",
            "confidence",
            "independent_units",
            "pi0_hat",
            "pi0_upper",
            "pvalues",
            "pvalues_upper",
            "exceedances",
            "draws",
        ):
            assert key in out.evidence

    def test_it_is_deterministic(self):
        a = GrenanderLocalFdr().estimate(family(), independent_units=10)
        b = GrenanderLocalFdr().estimate(family(), independent_units=10)
        assert a.pi_hat == b.pi_hat
        assert a.pi_upper == b.pi_upper
        assert a.evidence["pi0_hat"] == b.evidence["pi0_hat"]

    def test_a_signal_that_beats_every_null_is_less_false_than_one_that_does_not(self):
        out = GrenanderLocalFdr().estimate(family(), independent_units=10)
        assert out.pi_hat["s0"] < out.pi_hat["w0"]
        assert out.pi_upper["s0"] < out.pi_upper["w0"]

    def test_an_all_null_family_is_declared_almost_entirely_false(self):
        nulls = [math.sin(i) for i in range(99)]
        ev = {f"n{i}": SignalEvidence(nulls[i], nulls) for i in range(12)}
        out = GrenanderLocalFdr().estimate(ev, independent_units=12)
        assert out.evidence["pi0_hat"] > 0.5
        assert min(out.pi_hat.values()) > 0.3

    def test_the_bound_is_strictly_wider_than_the_point_estimate(self):
        out = GrenanderLocalFdr().estimate(family(), independent_units=10)
        assert any(out.pi_upper[k] > out.pi_hat[k] for k in out.pi_hat)

    def test_more_null_draws_tighten_the_bound(self):
        few = GrenanderLocalFdr().estimate(family(draws=19), independent_units=10)
        many = GrenanderLocalFdr().estimate(family(draws=999), independent_units=10)
        assert many.pi_upper["s0"] < few.pi_upper["s0"]

    def test_fewer_independent_units_widen_the_null_share_bound(self):
        tight = GrenanderLocalFdr().estimate(family(), independent_units=10)
        wide = GrenanderLocalFdr().estimate(family(), independent_units=2)
        assert wide.evidence["pi0_upper"] > tight.evidence["pi0_upper"]

    def test_no_rate_is_ever_defaulted_to_zero(self):
        # Every signal clears every draw: the null share must still be positive.
        nulls = [0.0] * 99
        ev = {f"s{i}": SignalEvidence(1.0, nulls) for i in range(5)}
        out = GrenanderLocalFdr().estimate(ev, independent_units=5)
        assert out.evidence["pi0_hat"] > 0.0
        assert all(v > 0.0 for v in out.pi_hat.values())
        assert all(v > 0.0 for v in out.pi_upper.values())


class TestEstimateRefusals:
    def test_an_empty_family_is_refused(self):
        with pytest.raises(ValueError, match="empty"):
            GrenanderLocalFdr().estimate({}, independent_units=1)

    def test_a_non_evidence_member_is_named(self):
        with pytest.raises(ValueError, match="'bad'"):
            GrenanderLocalFdr().estimate(
                {"ok": SignalEvidence(1.0, [0.0]), "bad": 3.0}, independent_units=2
            )

    @pytest.mark.parametrize("units", [0, -1, 1.5, True, "4"])
    def test_a_bad_independent_unit_count_is_refused(self, units):
        with pytest.raises(ValueError, match="independent_units"):
            GrenanderLocalFdr().estimate(family(), independent_units=units)

    def test_more_independent_units_than_signals_is_refused(self):
        with pytest.raises(ValueError, match="independent_units"):
            GrenanderLocalFdr().estimate(family(), independent_units=11)

    @pytest.mark.parametrize("confidence", [0.0, 1.0, 1.2, "0.9"])
    def test_a_bad_confidence_is_refused(self, confidence):
        with pytest.raises(ValueError, match="confidence"):
            GrenanderLocalFdr(confidence=confidence)

    @pytest.mark.parametrize("lam", [0.0, 1.0, -0.1, "0.5"])
    def test_a_bad_null_threshold_is_refused(self, lam):
        with pytest.raises(ValueError, match="null_threshold"):
            GrenanderLocalFdr(null_threshold=lam)

    def test_the_defaults_are_named_once(self):
        est = GrenanderLocalFdr()
        assert est.confidence == DEFAULT_CONFIDENCE
        assert est.null_threshold == DEFAULT_NULL_THRESHOLD


class _Rising(FalseSignalEstimator):
    """A hostile member: its density RISES, which breaks the bound's argument."""

    def fit(self, pvalues):
        return None

    def null_proportion(self, state):
        return 0.5

    def density(self, state, p):
        return 1.0 + p


class _BadPi0(FalseSignalEstimator):
    def fit(self, pvalues):
        return None

    def null_proportion(self, state):
        return 0.0

    def density(self, state, p):
        return 1.0


class _ZeroDensity(FalseSignalEstimator):
    def fit(self, pvalues):
        return None

    def null_proportion(self, state):
        return 0.5

    def density(self, state, p):
        return 0.0


class TestHookContract:
    def test_the_hooks_are_abstract(self):
        class Incomplete(FalseSignalEstimator):
            def fit(self, pvalues):
                return None

        with pytest.raises(TypeError):
            Incomplete()

    def test_a_rising_density_is_refused_by_name(self):
        with pytest.raises(ValueError, match="density"):
            _Rising().estimate(family(), independent_units=10)

    def test_a_zero_null_share_is_refused(self):
        with pytest.raises(ValueError, match="null_proportion"):
            _BadPi0().estimate(family(), independent_units=10)

    def test_a_zero_density_is_refused(self):
        with pytest.raises(ValueError, match="density"):
            _ZeroDensity().estimate(family(), independent_units=10)

    def test_the_template_is_not_a_subclass_decision(self):
        assert "estimate" not in vars(GrenanderLocalFdr)


class TestRegistry:
    def test_the_shipping_member_is_registered(self):
        assert estimator("grenander-local-fdr")["cls"] is GrenanderLocalFdr
        assert ESTIMATORS["grenander-local-fdr"]["doc"]

    def test_an_unknown_name_names_the_known_ones(self):
        with pytest.raises(ValueError, match="grenander-local-fdr"):
            estimator("nope")

    def test_a_duplicate_registration_is_refused(self):
        with pytest.raises(ValueError, match="already registered"):
            register_estimator("grenander-local-fdr", GrenanderLocalFdr)

    def test_it_refuses_a_non_estimator(self):
        with pytest.raises(ValueError, match="FalseSignalEstimator"):
            register_estimator("plain-function", lambda: None)
