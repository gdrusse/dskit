"""The generic per-signal false-signal probability estimator (ADR-0152)."""

import math
import random
import statistics

import pytest

from dskit.pipeline.false_signal import (
    DEFAULT_MIN_FAMILY,
    DEFAULT_NULL_THRESHOLD,
    DEFAULT_WIDENING_LEVEL,
    FALSE_SIGNAL_ESTIMATORS,
    FalseSignalEstimate,
    FalseSignalEstimator,
    GrenanderLocalFdr,
    SignalEvidence,
    clopper_pearson_upper,
    false_signal_estimator,
    permutation_pvalue,
    register_false_signal_estimator,
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


class TestTheBetaTailEnforcesItsPreconditions:
    """`regularized_incomplete_beta` is public, so it grades its inputs."""

    @pytest.mark.parametrize("a", [0.0, -1.0, float("nan"), float("inf"), "1"])
    def test_a_bad_first_shape_is_refused_by_name(self, a):
        # Used to escape as a bare `math domain error` out of lgamma.
        with pytest.raises(ValueError, match="finite a > 0"):
            regularized_incomplete_beta(a, 1.0, 0.5)

    @pytest.mark.parametrize("b", [0.0, -1.0, float("nan")])
    def test_a_bad_second_shape_is_refused_by_name(self, b):
        with pytest.raises(ValueError, match="finite b > 0"):
            regularized_incomplete_beta(1.0, b, 0.5)

    def test_a_non_finite_x_is_refused_instead_of_returning_nan(self):
        with pytest.raises(ValueError, match="finite x"):
            regularized_incomplete_beta(1.0, 1.0, float("nan"))

    def test_x_outside_the_unit_interval_still_clamps(self):
        # Documented behaviour, distinct from the refusals above.
        assert regularized_incomplete_beta(2.0, 3.0, -0.5) == 0.0
        assert regularized_incomplete_beta(2.0, 3.0, 1.5) == 1.0


class TestEstimateContract:
    """Exactly what `forecast_bundle` validates, restated independently."""

    def test_every_pi_is_a_probability_and_the_widened_covers_the_point(self):
        est = GrenanderLocalFdr()
        out = est.estimate(family(), independent_units=10)
        assert set(out.pi_hat) == set(out.pi_widened) == set(family())
        for name, hat in out.pi_hat.items():
            widened = out.pi_widened[name]
            assert isinstance(hat, float) and isinstance(widened, float)
            assert math.isfinite(hat) and math.isfinite(widened)
            assert 0.0 <= hat <= 1.0
            assert 0.0 <= widened <= 1.0
            assert hat <= widened

    def test_it_returns_the_estimate_value(self):
        out = GrenanderLocalFdr().estimate(family(), independent_units=10)
        assert isinstance(out, FalseSignalEstimate)
        for key in (
            "estimator",
            "widening_level",
            "prices",
            "unpriced",
            "min_family",
            "independent_units",
            "pi0_hat",
            "pi0_upper",
            "pvalues",
            "pvalues_upper",
            "exceedances",
            "draws",
        ):
            assert key in out.evidence

    def test_the_evidence_says_what_is_not_priced(self):
        # The one thing a consumer must not miss travels WITH the numbers.
        out = GrenanderLocalFdr().estimate(family(), independent_units=10)
        assert out.evidence["unpriced"] == ("density_estimation",)
        assert out.evidence["prices"] == (
            "scramble_monte_carlo",
            "null_share_binomial",
        )

    def test_it_is_deterministic(self):
        a = GrenanderLocalFdr().estimate(family(), independent_units=10)
        b = GrenanderLocalFdr().estimate(family(), independent_units=10)
        assert dict(a.pi_hat) == dict(b.pi_hat)
        assert dict(a.pi_widened) == dict(b.pi_widened)
        assert a.evidence["pi0_hat"] == b.evidence["pi0_hat"]

    def test_a_signal_that_beats_every_null_is_less_false_than_one_that_does_not(self):
        out = GrenanderLocalFdr().estimate(family(), independent_units=10)
        assert out.pi_hat["s0"] < out.pi_hat["w0"]
        assert out.pi_widened["s0"] < out.pi_widened["w0"]

    def test_an_all_null_family_is_declared_almost_entirely_false(self):
        nulls = [math.sin(i) for i in range(99)]
        ev = {f"n{i}": SignalEvidence(nulls[i], nulls) for i in range(12)}
        out = GrenanderLocalFdr().estimate(ev, independent_units=12)
        assert out.evidence["pi0_hat"] > 0.5
        assert min(out.pi_hat.values()) > 0.3

    def test_the_widened_reading_is_strictly_above_the_point_estimate(self):
        out = GrenanderLocalFdr().estimate(family(), independent_units=10)
        assert any(out.pi_widened[k] > out.pi_hat[k] for k in out.pi_hat)

    def test_more_null_draws_tighten_the_widened_reading(self):
        few = GrenanderLocalFdr().estimate(family(draws=19), independent_units=10)
        many = GrenanderLocalFdr().estimate(family(draws=999), independent_units=10)
        assert many.pi_widened["s0"] < few.pi_widened["s0"]

    def test_fewer_independent_units_widen_the_null_share_limit(self):
        tight = GrenanderLocalFdr().estimate(family(), independent_units=10)
        wide = GrenanderLocalFdr().estimate(family(), independent_units=2)
        assert wide.evidence["pi0_upper"] > tight.evidence["pi0_upper"]

    def test_no_rate_is_ever_defaulted_to_zero(self):
        # Every signal clears every draw: the null share must still be positive.
        nulls = [0.0] * 99
        ev = {f"s{i}": SignalEvidence(1.0, nulls) for i in range(12)}
        out = GrenanderLocalFdr().estimate(ev, independent_units=12)
        assert out.evidence["pi0_hat"] > 0.0
        assert all(v > 0.0 for v in out.pi_hat.values())
        assert all(v > 0.0 for v in out.pi_widened.values())


class TestTheWidenedInputsReachTheArithmetic:
    """Every knob and every widening step, pinned to an independent value."""

    def test_the_p_value_is_widened_by_clopper_pearson(self):
        # Deleting the CP widening of p leaves p_upper == p_hat == 0.01.
        out = GrenanderLocalFdr().estimate(family(), independent_units=10)
        assert out.evidence["pvalues"]["s0"] == pytest.approx(0.01)
        assert out.evidence["pvalues_upper"]["s0"] == pytest.approx(
            clopper_pearson_upper(0, 99, 0.975), abs=1e-12
        )
        assert out.evidence["pvalues_upper"]["s0"] > out.evidence["pvalues"]["s0"]

    def test_the_error_budget_is_split_between_the_two_inputs(self):
        # A Bonferroni split spends 0.025 per input, not 0.05 on one.
        out = GrenanderLocalFdr(widening_level=0.95).estimate(
            family(), independent_units=10
        )
        split = clopper_pearson_upper(0, 99, 0.975)
        unsplit = clopper_pearson_upper(0, 99, 0.95)
        assert unsplit < split
        assert out.evidence["pvalues_upper"]["s0"] == pytest.approx(split, abs=1e-12)

    def test_the_null_share_limit_rounds_the_observed_count_up(self):
        # pi0_hat * units is 6.4 here: round() drops it to 6 and quietly
        # narrows the limit for the WHOLE family; ceil() cannot undershoot.
        out = GrenanderLocalFdr().estimate(family(), independent_units=8)
        pi0_hat = out.evidence["pi0_hat"]
        assert pi0_hat * 8 == pytest.approx(6.4)
        up = clopper_pearson_upper(math.ceil(pi0_hat * 8), 8, 0.975)
        down = clopper_pearson_upper(round(pi0_hat * 8), 8, 0.975)
        assert up > down
        assert out.evidence["pi0_upper"] == pytest.approx(up, abs=1e-12)

    def test_the_widening_level_knob_reaches_both_numbers(self):
        loose = GrenanderLocalFdr(widening_level=0.60).estimate(
            family(), independent_units=10
        )
        tight = GrenanderLocalFdr(widening_level=0.999).estimate(
            family(), independent_units=10
        )
        assert tight.evidence["pvalues_upper"]["s0"] > loose.evidence[
            "pvalues_upper"
        ]["s0"]
        assert tight.evidence["pi0_upper"] > loose.evidence["pi0_upper"]
        assert tight.pi_widened["s0"] > loose.pi_widened["s0"]

    def test_the_null_threshold_knob_reaches_the_null_share(self):
        low = GrenanderLocalFdr(null_threshold=0.3).estimate(
            family(), independent_units=10
        )
        high = GrenanderLocalFdr(null_threshold=0.8).estimate(
            family(), independent_units=10
        )
        assert low.evidence["pi0_hat"] != high.evidence["pi0_hat"]

    def test_the_null_share_is_floored_at_one_over_m_plus_one(self):
        # No p-value clears the threshold, so Storey's share is exactly 0 and
        # only the add-one floor keeps it positive -- at its exact value.
        nulls = [0.0] * 99
        ev = {f"s{i}": SignalEvidence(1.0, nulls) for i in range(12)}
        out = GrenanderLocalFdr().estimate(ev, independent_units=12)
        assert out.evidence["pi0_hat"] == pytest.approx(1.0 / 13.0, abs=1e-15)

    def test_the_defaults_are_named_once(self):
        est = GrenanderLocalFdr()
        assert est.widening_level == DEFAULT_WIDENING_LEVEL
        assert est.null_threshold == DEFAULT_NULL_THRESHOLD
        assert est.min_family == DEFAULT_MIN_FAMILY


class TestGrenanderInternals:
    """The fit itself, against a hand-computed least concave majorant."""

    # ECDF vertices (0, 0) (0.1, 1/4) (0.2, 1/2) (0.4, 3/4) (1.0, 1).
    # (0.1, 1/4) is COLLINEAR with (0, 0) and (0.2, 1/2) at slope 5/2, so the
    # majorant is (0, 0) -> (0.2, 1/2) -> (0.4, 3/4) -> (1.0, 1).
    HAND = {"a": 0.1, "b": 0.2, "c": 0.4, "d": 1.0}

    def test_the_majorant_matches_a_hand_computed_one(self):
        fit = GrenanderLocalFdr().fit(self.HAND)
        assert fit.breaks == pytest.approx((0.2, 0.4, 1.0))
        assert fit.slopes == pytest.approx((2.5, 1.25, 0.25 / 0.6))

    def test_a_collinear_vertex_is_not_a_breakpoint(self):
        # Popping only on a STRICT decrease would keep 0.1 and report two
        # segments where the density has one value.
        fit = GrenanderLocalFdr().fit(self.HAND)
        assert 0.1 not in fit.breaks
        assert len(fit.slopes) == 3

    def test_the_density_is_left_continuous_at_a_breakpoint(self):
        # p exactly ON a breakpoint reads the segment ENDING there (2.5),
        # not the one starting there (1.25).
        est = GrenanderLocalFdr()
        fit = est.fit(self.HAND)
        assert est.density(fit, 0.2) == pytest.approx(2.5)
        assert est.density(fit, 0.2 + 1e-12) == pytest.approx(1.25)

    def test_the_density_extends_the_final_slope_past_the_largest_p(self):
        # Disclosed, not accidental: the read stays positive past p_max
        # rather than dropping to the majorant's zero.
        est = GrenanderLocalFdr()
        fit = est.fit({"a": 0.1, "b": 0.2, "c": 0.3, "d": 0.4})
        assert est.density(fit, 1.0) == pytest.approx(fit.slopes[-1])
        assert est.density(fit, 1.0) > 0.0

    @pytest.mark.parametrize("bad", [0.0, -0.1, 1.5])
    def test_fit_refuses_a_p_value_outside_the_unit_interval(self, bad):
        with pytest.raises(ValueError, match="p-value"):
            GrenanderLocalFdr().fit({"a": 0.5, "b": bad})

    def test_fit_refuses_an_empty_family(self):
        with pytest.raises(ValueError, match="p-value"):
            GrenanderLocalFdr().fit({})


class TestTheAddOneRuleHasOneOwner:
    def test_estimate_reads_the_public_permutation_pvalue(self):
        fam = family()
        out = GrenanderLocalFdr().estimate(fam, independent_units=10)
        for name, ev in fam.items():
            assert out.evidence["pvalues"][name] == permutation_pvalue(
                ev.statistic, ev.null_draws
            )


class _WithinSlackRiser(FalseSignalEstimator):
    """Its density rises by LESS than the monotonicity slack, and inverts."""

    def fit(self, pvalues):
        return None

    def null_proportion(self, state):
        return 1.0

    def density(self, state, p):
        return 2.0 + 1e-13 * p


class TestTheResultValueContract:
    def test_the_result_maps_are_read_only(self):
        out = GrenanderLocalFdr().estimate(family(), independent_units=10)
        for mapping in (out.pi_hat, out.pi_widened, out.evidence):
            with pytest.raises(TypeError):
                mapping["s0"] = 0.0
        with pytest.raises(TypeError):
            out.evidence["pvalues"]["s0"] = 0.0

    def test_a_rise_inside_the_slack_is_caught_and_names_the_estimator(self):
        # The monotonicity screen lets this through -- 1e-13 * dp is under
        # _MONOTONE_SLACK -- so the ordering is NOT by construction, and the
        # value contract is what stops it.
        with pytest.raises(ValueError, match="_WithinSlackRiser"):
            _WithinSlackRiser().estimate(family(), independent_units=10)

    def test_mismatched_keys_are_refused(self):
        with pytest.raises(ValueError, match="same signals"):
            FalseSignalEstimate({"a": 0.1}, {"b": 0.2}, {})

    @pytest.mark.parametrize("bad", [-0.1, 1.5, float("nan"), "0.5"])
    def test_a_non_probability_is_refused_and_named(self, bad):
        with pytest.raises(ValueError, match="'a'"):
            FalseSignalEstimate({"a": bad}, {"a": 1.0}, {"estimator": "x:Y"})

    def test_a_widened_reading_below_its_point_is_refused(self):
        with pytest.raises(ValueError, match="below its own pi_hat"):
            FalseSignalEstimate({"a": 0.6}, {"a": 0.5}, {"estimator": "x:Y"})

    def test_an_unnamed_producer_still_refuses(self):
        with pytest.raises(ValueError, match="unknown estimator"):
            FalseSignalEstimate({"a": 0.6}, {"a": 0.5}, {})


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

    @pytest.mark.parametrize("level", [0.0, 1.0, 1.2, "0.9"])
    def test_a_bad_widening_level_is_refused(self, level):
        with pytest.raises(ValueError, match="widening_level"):
            GrenanderLocalFdr(widening_level=level)

    @pytest.mark.parametrize("lam", [0.0, 1.0, -0.1, "0.5"])
    def test_a_bad_null_threshold_is_refused(self, lam):
        with pytest.raises(ValueError, match="null_threshold"):
            GrenanderLocalFdr(null_threshold=lam)

    @pytest.mark.parametrize("floor", [0, -1, 2.5, "3"])
    def test_a_bad_min_family_is_refused(self, floor):
        with pytest.raises(ValueError, match="min_family"):
            GrenanderLocalFdr(min_family=floor)


class TestResolutionLimits:
    """What the p-scale cannot resolve, pinned so it cannot change silently."""

    def test_a_family_below_the_floor_is_refused(self):
        # A two-groups model fitted to ONE observation reports the scramble
        # count, not the family: f(p) = 1 / p, so pi = pi0 * p ~ 1 / B.
        with pytest.raises(ValueError, match="min_family"):
            GrenanderLocalFdr().estimate(
                {"only": SignalEvidence(1e-9, [0.0] * 999)}, independent_units=1
            )

    def test_the_floor_is_a_knob_not_a_literal(self):
        small = {f"s{i}": SignalEvidence(1.0 + i, [0.0] * 99) for i in range(3)}
        with pytest.raises(ValueError, match="min_family"):
            GrenanderLocalFdr(min_family=4).estimate(small, independent_units=3)
        out = GrenanderLocalFdr(min_family=3).estimate(small, independent_units=3)
        assert set(out.pi_hat) == set(small)

    def test_two_statistics_that_beat_every_draw_are_indistinguishable(self):
        # Known limit, not a defect: the permutation p-value floors at
        # 1 / (1 + B), so strength beyond "beat everything" is invisible.
        nulls = [0.0] * 999
        rest = {f"w{i}": SignalEvidence(-1.0 - i, nulls) for i in range(11)}
        tiny = GrenanderLocalFdr().estimate(
            {"x": SignalEvidence(1e-12, nulls), **rest}, independent_units=12
        )
        huge = GrenanderLocalFdr().estimate(
            {"x": SignalEvidence(1e12, nulls), **rest}, independent_units=12
        )
        assert tiny.pi_hat["x"] == huge.pi_hat["x"]

    def test_the_strongest_signal_falls_with_the_scramble_count(self):
        # Known limit, not a defect: more scrambles shrink p_(1), which
        # inflates the Grenander boundary density and shrinks pi with no new
        # evidence about that signal's edge (Woodroofe-Sun).
        def run(draws):
            nulls = [0.0] * draws
            ev = {"x": SignalEvidence(1.0, nulls)}
            ev.update(
                {f"w{i}": SignalEvidence(-1.0 - i, nulls) for i in range(11)}
            )
            return GrenanderLocalFdr().estimate(ev, independent_units=12)

        few, many = run(99), run(9999)
        assert many.pi_hat["x"] < few.pi_hat["x"] / 50.0


class TestHookContract:
    def test_the_hooks_are_abstract(self):
        class Incomplete(FalseSignalEstimator):
            def fit(self, pvalues):
                return None

        with pytest.raises(TypeError):
            Incomplete()

    def test_a_rising_density_is_refused_by_name(self):
        class Rising(FalseSignalEstimator):
            def fit(self, pvalues):
                return None

            def null_proportion(self, state):
                return 0.5

            def density(self, state, p):
                return 1.0 + p

        with pytest.raises(ValueError, match="density"):
            Rising().estimate(family(), independent_units=10)

    def test_a_zero_null_share_is_refused(self):
        class BadPi0(FalseSignalEstimator):
            def fit(self, pvalues):
                return None

            def null_proportion(self, state):
                return 0.0

            def density(self, state, p):
                return 1.0

        with pytest.raises(ValueError, match="null_proportion"):
            BadPi0().estimate(family(), independent_units=10)

    def test_a_zero_density_is_refused(self):
        class ZeroDensity(FalseSignalEstimator):
            def fit(self, pvalues):
                return None

            def null_proportion(self, state):
                return 0.5

            def density(self, state, p):
                return 0.0

        with pytest.raises(ValueError, match="density"):
            ZeroDensity().estimate(family(), independent_units=10)

    def test_the_template_cannot_be_overridden(self):
        # Not "documented as non-overridable" -- the class cannot be DEFINED.
        with pytest.raises(TypeError, match="TEMPLATE"):

            class Sneaky(FalseSignalEstimator):
                def fit(self, pvalues):
                    return None

                def null_proportion(self, state):
                    return 0.5

                def density(self, state, p):
                    return 1.0

                def estimate(self, evidence, *, independent_units):
                    return FalseSignalEstimate({}, {}, {})

    def test_the_shipping_member_does_not_override_it(self):
        assert "estimate" not in vars(GrenanderLocalFdr)


class TestRegistry:
    def test_the_shipping_member_is_registered(self):
        entry = false_signal_estimator("grenander-local-fdr")
        assert entry["cls"] is GrenanderLocalFdr
        assert FALSE_SIGNAL_ESTIMATORS["grenander-local-fdr"]["doc"]

    def test_an_unknown_name_names_the_known_ones(self):
        with pytest.raises(ValueError, match="grenander-local-fdr"):
            false_signal_estimator("nope")

    def test_a_duplicate_registration_is_refused(self):
        with pytest.raises(ValueError, match="already registered"):
            register_false_signal_estimator(
                "grenander-local-fdr", GrenanderLocalFdr
            )

    def test_it_refuses_a_non_estimator(self):
        with pytest.raises(ValueError, match="FalseSignalEstimator"):
            register_false_signal_estimator("plain-function", lambda: None)

    def test_it_refuses_an_empty_name(self):
        with pytest.raises(ValueError, match="non-empty string"):
            register_false_signal_estimator("", GrenanderLocalFdr)


# --------------------------------------------------------------------------
# The claim the module makes about `pi_widened`, pinned by measurement.
# --------------------------------------------------------------------------

_NORMAL = statistics.NormalDist()


def _true_local_fdr(p, pi0, mu):
    """Local fdr of the two-groups model this Monte Carlo actually samples."""
    z = _NORMAL.inv_cdf(1.0 - p)
    density_ratio = math.exp(-0.5 * ((z - mu) ** 2 - z * z))
    return pi0 / (pi0 + (1.0 - pi0) * density_ratio)


def _two_groups_family(rng, pi0, m, draws, mu):
    """Sample a family from a KNOWN two-groups truth, with its true fdrs."""
    evidence, truth = {}, {}
    for i in range(m):
        alternative = rng.random() >= pi0
        z = rng.gauss(mu if alternative else 0.0, 1.0)
        evidence[f"s{i}"] = SignalEvidence(
            z, [rng.gauss(0.0, 1.0) for _ in range(draws)]
        )
        truth[f"s{i}"] = _true_local_fdr(
            min(max(_NORMAL.cdf(-z), 1e-300), 1.0), pi0, mu
        )
    return evidence, truth


def _measure(pi0, m, draws, reps, level=DEFAULT_WIDENING_LEVEL, mu=3.0, seed=1):
    """Return (coverage, share of readings already pinned at 1.0)."""
    rng = random.Random(seed)
    est = GrenanderLocalFdr(widening_level=level)
    covered = maxed = total = 0
    for _ in range(reps):
        evidence, truth = _two_groups_family(rng, pi0, m, draws, mu)
        out = est.estimate(evidence, independent_units=m)
        for name, true_fdr in truth.items():
            total += 1
            covered += out.pi_widened[name] >= true_fdr - 1e-12
            maxed += out.pi_widened[name] >= 1.0
    return covered / total, maxed / total


@pytest.mark.slow
class TestWidenedReadingIsNotAConfidenceBound:
    """Measured, not asserted: what `pi_widened` does and does not deliver.

    Fully independent signals (``independent_units == m``) and a known
    two-groups truth -- the most favourable case there is. If a future
    change ever DOES buy real coverage, these tests fail and the module's
    prose has to be rewritten with it. That is the point of them.
    """

    def test_it_under_covers_the_true_local_fdr_at_the_nominal_level(self):
        coverage, _ = _measure(pi0=0.90, m=200, draws=999, reps=12)
        assert coverage < 0.90  # nominal is 0.95
        assert coverage > 0.50  # and it is not garbage either

    def test_it_under_covers_worst_where_a_real_search_lives(self):
        # pi0 ~ 0.99 is the regime a genuine signal search sits in.
        sparse, _ = _measure(pi0=0.99, m=200, draws=999, reps=12)
        dense, _ = _measure(pi0=0.80, m=200, draws=999, reps=12)
        assert sparse < 0.70
        assert sparse < dense

    def test_raising_the_widening_level_does_not_buy_coverage(self):
        # The knob moves two binomial limits; the density fit is untouched,
        # so even a one-in-a-million nominal level stays short of 0.95.
        low, _ = _measure(pi0=0.90, m=200, draws=999, reps=8, level=0.50)
        high, _ = _measure(pi0=0.90, m=200, draws=999, reps=8, level=0.999999)
        assert low < high
        assert high < 0.95

    def test_it_is_already_pinned_at_one_for_most_signals_while_under_covering(
        self,
    ):
        # The misses are not "a slightly tight bound": most readings are
        # ALREADY the largest value the scale allows, and coverage is still
        # short. The shortfall sits on the signals that would get capital.
        coverage, maxed = _measure(pi0=0.90, m=200, draws=999, reps=12)
        assert maxed > 0.50
        assert coverage < 0.90
