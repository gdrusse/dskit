"""The budgeted uncertainty-set family: one doorway, three members."""

from __future__ import annotations

import itertools
import math

import pytest

from dskit.pipeline.uncertainty_set import (
    COEFFICIENT_DOMAINS,
    MAX_REALIZATIONS,
    UNCERTAINTY_SETS,
    WEIGHTS_SUM_TOLERANCE,
    WORST_CASE_SENSES,
    BudgetedMeanSet,
    BudgetedOutcomeSet,
    BudgetedProbabilitySet,
    BudgetedUncertaintySet,
    RealizationSet,
    RobustCounterpart,
    WorstCase,
    register_uncertainty_set,
    uncertainty_set,
)

# --- independent restatements ------------------------------------------------
#
# A suite must not read its expected vocabulary from the thing it validates.
# These two numbers are the CONSUMER's, restated here by hand from
# `dskit/pipeline/libs/pyomo.py` (the `payoffs()` weight screen in
# `ScenarioUtilitySolve.build_model`), so a drift in either direction is caught
# rather than agreed with.
CONSUMER_WEIGHT_TOLERANCE = 1e-8


def two_sided(names, mean=0.0, width=1.0, budget=1.0, cls=BudgetedMeanSet):
    """Build a two-sided member over ``names`` with equal deviations."""
    return cls(
        nominal={n: mean for n in names},
        deviation_below={n: width for n in names},
        deviation_above={n: width for n in names},
        budget=budget,
    )


def brute_force_extreme(uset, coefficients):
    """Independently maximize/minimize the linear form over the set's vertices.

    Enumerates every vertex of ``{z : |z_i| <= 1, sum|z_i| <= budget}`` for a
    small family and evaluates the form directly. Nothing here calls the
    module's own arithmetic.
    """
    names = sorted(uset.nominal)
    full = int(math.floor(uset.budget + 1e-12))
    frac = uset.budget - full
    best = None
    for magnitudes in itertools.product(
        *[(-1.0, 0.0, 1.0) if abs(frac) < 1e-12 else (-1.0, -frac, 0.0, frac, 1.0) for _ in names]
    ):
        if sum(abs(m) for m in magnitudes) > uset.budget + 1e-12:
            continue
        value = 0.0
        for name, magnitude in zip(names, magnitudes):
            half = uset.deviation_above[name] if magnitude > 0 else uset.deviation_below[name]
            value += coefficients[name] * (uset.nominal[name] + magnitude * half)
        if best is None:
            best = value
        elif uset.worst_case_sense() == "max":
            best = max(best, value)
        else:
            best = min(best, value)
    return best


class TestModuleSurface:
    def test_every_public_name_is_exported(self):
        import dskit.pipeline.uncertainty_set as module

        public = {n for n in vars(module) if not n.startswith("_")}
        exported = set(module.__all__)
        leaked = {n for n in exported if n.startswith("_")}
        assert not leaked, f"underscore names leaked into __all__: {sorted(leaked)}"
        # Imported helpers are not part of this module's surface; every name
        # DEFINED here is.
        defined = {
            n
            for n in public
            if getattr(vars(module)[n], "__module__", None) == module.__name__
        }
        assert defined <= exported, f"defined but unexported: {sorted(defined - exported)}"

    def test_the_vocabularies_are_closed_tuples(self):
        assert WORST_CASE_SENSES == ("max", "min")
        assert COEFFICIENT_DOMAINS == ("nonnegative", "real")

    def test_the_base_is_abstract(self):
        with pytest.raises(TypeError):
            BudgetedUncertaintySet(
                nominal={"a": 0.0},
                deviation_below={"a": 1.0},
                deviation_above={"a": 1.0},
                budget=1.0,
            )


class TestConstruction:
    def test_a_two_sided_member_constructs(self):
        u = two_sided(["a", "b"], mean=0.5, width=0.1, budget=1.0)
        assert u.names == ("a", "b")
        assert u.budget == 1.0

    def test_every_argument_is_required(self):
        with pytest.raises(TypeError):
            BudgetedMeanSet(nominal={"a": 0.0}, deviation_below={"a": 1.0}, budget=1.0)
        with pytest.raises(TypeError):
            BudgetedMeanSet(
                nominal={"a": 0.0}, deviation_below={"a": 1.0}, deviation_above={"a": 1.0}
            )
        with pytest.raises(TypeError):
            BudgetedMeanSet(deviation_below={"a": 1.0}, deviation_above={"a": 1.0}, budget=1.0)

    def test_an_empty_family_refuses(self):
        with pytest.raises(ValueError, match="at least one component"):
            BudgetedMeanSet(nominal={}, deviation_below={}, deviation_above={}, budget=1.0)

    def test_a_non_finite_nominal_refuses_by_name(self):
        with pytest.raises(ValueError, match="'b'"):
            BudgetedMeanSet(
                nominal={"a": 0.0, "b": float("nan")},
                deviation_below={"a": 1.0, "b": 1.0},
                deviation_above={"a": 1.0, "b": 1.0},
                budget=1.0,
            )

    def test_an_unbounded_deviation_refuses_by_name(self):
        with pytest.raises(ValueError, match="'a'"):
            BudgetedMeanSet(
                nominal={"a": 0.0},
                deviation_below={"a": 1.0},
                deviation_above={"a": float("inf")},
                budget=1.0,
            )

    def test_a_negative_deviation_refuses_by_name(self):
        with pytest.raises(ValueError, match=r"deviation_below\['a'\] must be >= 0"):
            BudgetedMeanSet(
                nominal={"a": 0.0},
                deviation_below={"a": -1.0},
                deviation_above={"a": 1.0},
                budget=1.0,
            )

    def test_a_zero_adverse_deviation_refuses_by_name(self):
        with pytest.raises(ValueError, match="'b'"):
            BudgetedMeanSet(
                nominal={"a": 0.0, "b": 0.0},
                deviation_below={"a": 1.0, "b": 0.0},
                deviation_above={"a": 1.0, "b": 1.0},
                budget=1.0,
            )

    def test_mismatched_component_names_refuse_naming_both_sides(self):
        with pytest.raises(ValueError, match="deviation_above"):
            BudgetedMeanSet(
                nominal={"a": 0.0, "b": 0.0},
                deviation_below={"a": 1.0, "b": 1.0},
                deviation_above={"a": 1.0},
                budget=1.0,
            )
        with pytest.raises(ValueError, match="deviation_below"):
            BudgetedMeanSet(
                nominal={"a": 0.0, "b": 0.0},
                deviation_below={"a": 1.0},
                deviation_above={"a": 1.0, "b": 1.0},
                budget=1.0,
            )

    def test_a_component_name_must_be_a_non_empty_string(self):
        with pytest.raises(ValueError, match="non-empty string"):
            BudgetedMeanSet(
                nominal={"": 0.0}, deviation_below={"": 1.0}, deviation_above={"": 1.0}, budget=1.0
            )

    @pytest.mark.parametrize("budget", [0.0, -1.0, float("nan"), float("inf"), "1", None])
    def test_a_budget_outside_its_range_refuses(self, budget):
        with pytest.raises(ValueError, match="budget"):
            two_sided(["a", "b"], budget=budget)

    def test_a_budget_above_the_component_count_refuses_naming_both(self):
        with pytest.raises(ValueError, match="2"):
            two_sided(["a", "b"], budget=2.5)

    def test_a_budget_equal_to_the_component_count_is_the_box_and_is_allowed(self):
        u = two_sided(["a", "b"], width=0.1, budget=2.0)
        assert u.protection({"a": 1.0, "b": 1.0}) == pytest.approx(0.2)

    def test_the_stored_family_is_not_the_callers_dict(self):
        nominal = {"a": 0.0, "b": 0.0}
        u = BudgetedMeanSet(
            nominal=nominal,
            deviation_below={"a": 1.0, "b": 1.0},
            deviation_above={"a": 1.0, "b": 1.0},
            budget=1.0,
        )
        nominal["a"] = 99.0
        assert u.nominal["a"] == 0.0

    def test_the_stored_family_cannot_be_mutated(self):
        u = two_sided(["a"], budget=1.0)
        for mapping in (u.nominal, u.deviation_below, u.deviation_above):
            with pytest.raises(TypeError):
                mapping["a"] = 5.0

    def test_names_is_a_sorted_tuple(self):
        u = two_sided(["c", "a", "b"], budget=1.0)
        assert u.names == ("a", "b", "c")


class TestProbabilityMemberNarrowing:
    def test_it_declares_its_own_geometry(self):
        u = BudgetedProbabilitySet(
            nominal={"a": 0.2},
            deviation_below={"a": 0.0},
            deviation_above={"a": 0.1},
            budget=1.0,
        )
        assert u.worst_case_sense() == "max"
        assert u.coefficient_domain() == "nonnegative"
        assert u.component_bounds() == (0.0, 1.0)

    def test_a_nominal_outside_the_unit_interval_refuses_by_name(self):
        with pytest.raises(ValueError, match=r"nominal\['a'\].*outside this set's component"):
            BudgetedProbabilitySet(
                nominal={"a": 1.2},
                deviation_below={"a": 0.0},
                deviation_above={"a": 0.1},
                budget=1.0,
            )

    def test_a_deviation_the_member_can_never_read_refuses_by_name(self):
        with pytest.raises(ValueError, match="deviation_below"):
            BudgetedProbabilitySet(
                nominal={"a": 0.2},
                deviation_below={"a": 0.05},
                deviation_above={"a": 0.1},
                budget=1.0,
            )

    def test_the_domain_clamp_narrows_the_effective_deviation(self):
        u = BudgetedProbabilitySet(
            nominal={"a": 0.95},
            deviation_below={"a": 0.0},
            deviation_above={"a": 0.4},
            budget=1.0,
        )
        assert u.deviation_above["a"] == pytest.approx(0.05)

    def test_a_clamp_that_erases_the_deviation_refuses_by_name(self):
        with pytest.raises(ValueError, match="'a'"):
            BudgetedProbabilitySet(
                nominal={"a": 1.0},
                deviation_below={"a": 0.0},
                deviation_above={"a": 0.3},
                budget=1.0,
            )

    def test_a_negative_coefficient_refuses_by_name(self):
        u = BudgetedProbabilitySet(
            nominal={"a": 0.2, "b": 0.2},
            deviation_below={"a": 0.0, "b": 0.0},
            deviation_above={"a": 0.1, "b": 0.1},
            budget=1.0,
        )
        with pytest.raises(ValueError, match="'b'"):
            u.worst_case({"a": 1.0, "b": -1.0})

    def test_the_adverse_extreme_is_upward(self):
        u = BudgetedProbabilitySet(
            nominal={"a": 0.2, "b": 0.3},
            deviation_below={"a": 0.0, "b": 0.0},
            deviation_above={"a": 0.1, "b": 0.05},
            budget=1.0,
        )
        out = u.worst_case({"a": 1.0, "b": 1.0})
        assert out.value == pytest.approx(0.5 + 0.1)
        assert out.realization["a"] == pytest.approx(0.3)
        assert out.realization["b"] == pytest.approx(0.3)

    def test_every_realization_stays_inside_the_unit_interval(self):
        u = BudgetedProbabilitySet(
            nominal={"a": 0.9, "b": 0.02},
            deviation_below={"a": 0.0, "b": 0.0},
            deviation_above={"a": 0.05, "b": 0.3},
            budget=1.5,
        )
        _weights, draws = u.realizations(12, seed=3).weighted_draws()
        for name, values in draws.items():
            for value in values:
                assert 0.0 <= value <= 1.0, (name, value)


class TestWorstCase:
    def test_a_unit_budget_moves_only_the_single_worst_component(self):
        u = BudgetedMeanSet(
            nominal={"a": 1.0, "b": 1.0, "c": 1.0},
            deviation_below={"a": 0.1, "b": 0.4, "c": 0.2},
            deviation_above={"a": 0.1, "b": 0.4, "c": 0.2},
            budget=1.0,
        )
        out = u.worst_case({"a": 1.0, "b": 1.0, "c": 1.0})
        assert out.nominal_value == pytest.approx(3.0)
        assert out.protection == pytest.approx(0.4)
        assert out.value == pytest.approx(2.6)
        assert out.deviating == ("b",)

    def test_an_integer_budget_moves_exactly_that_many(self):
        u = BudgetedMeanSet(
            nominal={"a": 1.0, "b": 1.0, "c": 1.0},
            deviation_below={"a": 0.1, "b": 0.4, "c": 0.2},
            deviation_above={"a": 0.1, "b": 0.4, "c": 0.2},
            budget=2.0,
        )
        out = u.worst_case({"a": 1.0, "b": 1.0, "c": 1.0})
        assert out.protection == pytest.approx(0.6)
        assert out.deviating == ("b", "c")

    def test_a_fractional_budget_takes_a_fraction_of_the_next(self):
        u = BudgetedMeanSet(
            nominal={"a": 1.0, "b": 1.0, "c": 1.0},
            deviation_below={"a": 0.1, "b": 0.4, "c": 0.2},
            deviation_above={"a": 0.1, "b": 0.4, "c": 0.2},
            budget=1.5,
        )
        out = u.worst_case({"a": 1.0, "b": 1.0, "c": 1.0})
        assert out.protection == pytest.approx(0.4 + 0.5 * 0.2)
        assert out.realization["c"] == pytest.approx(1.0 - 0.5 * 0.2)

    def test_the_coefficient_scales_the_deviation(self):
        u = BudgetedMeanSet(
            nominal={"a": 1.0, "b": 1.0},
            deviation_below={"a": 0.1, "b": 0.4},
            deviation_above={"a": 0.1, "b": 0.4},
            budget=1.0,
        )
        # b's deviation is larger, but a carries ten times the coefficient.
        out = u.worst_case({"a": 10.0, "b": 1.0})
        assert out.deviating == ("a",)
        assert out.protection == pytest.approx(1.0)

    def test_a_negative_coefficient_takes_the_other_half(self):
        u = BudgetedMeanSet(
            nominal={"a": 1.0},
            deviation_below={"a": 0.1},
            deviation_above={"a": 0.9},
            budget=1.0,
        )
        assert u.worst_case({"a": 1.0}).realization["a"] == pytest.approx(0.9)
        assert u.worst_case({"a": -1.0}).realization["a"] == pytest.approx(1.9)

    def test_a_zero_coefficient_never_spends_the_budget(self):
        u = BudgetedMeanSet(
            nominal={"a": 1.0, "b": 1.0},
            deviation_below={"a": 5.0, "b": 0.4},
            deviation_above={"a": 5.0, "b": 0.4},
            budget=1.0,
        )
        out = u.worst_case({"a": 0.0, "b": 1.0})
        assert out.deviating == ("b",)
        assert out.realization["a"] == pytest.approx(1.0)

    def test_the_reported_realization_reproduces_the_reported_value(self):
        u = BudgetedMeanSet(
            nominal={"a": 1.0, "b": 2.0, "c": 3.0},
            deviation_below={"a": 0.3, "b": 0.1, "c": 0.5},
            deviation_above={"a": 0.2, "b": 0.7, "c": 0.4},
            budget=1.7,
        )
        coefficients = {"a": 2.0, "b": -1.0, "c": 0.5}
        out = u.worst_case(coefficients)
        assert sum(coefficients[n] * out.realization[n] for n in u.names) == pytest.approx(out.value)

    @pytest.mark.parametrize("budget", [0.5, 1.0, 1.5, 2.0, 3.0])
    @pytest.mark.parametrize(
        "coefficients",
        [
            {"a": 1.0, "b": 1.0, "c": 1.0},
            {"a": 2.0, "b": -1.0, "c": 0.5},
            {"a": 0.0, "b": -3.0, "c": 1.0},
        ],
    )
    def test_it_agrees_with_a_brute_force_search_over_the_vertices(self, budget, coefficients):
        u = BudgetedMeanSet(
            nominal={"a": 1.0, "b": 2.0, "c": 3.0},
            deviation_below={"a": 0.3, "b": 0.1, "c": 0.5},
            deviation_above={"a": 0.2, "b": 0.7, "c": 0.4},
            budget=budget,
        )
        assert u.worst_case(coefficients).value == pytest.approx(
            brute_force_extreme(u, coefficients)
        )

    def test_a_maximizing_member_agrees_with_brute_force_too(self):
        u = BudgetedProbabilitySet(
            nominal={"a": 0.2, "b": 0.4, "c": 0.1},
            deviation_below={"a": 0.0, "b": 0.0, "c": 0.0},
            deviation_above={"a": 0.3, "b": 0.1, "c": 0.5},
            budget=1.5,
        )
        coefficients = {"a": 1.0, "b": 2.0, "c": 0.5}
        assert u.worst_case(coefficients).value == pytest.approx(
            brute_force_extreme(u, coefficients)
        )

    def test_an_unknown_coefficient_refuses_by_name(self):
        u = two_sided(["a", "b"], budget=1.0)
        with pytest.raises(ValueError, match="'z'"):
            u.worst_case({"a": 1.0, "b": 1.0, "z": 1.0})

    def test_a_missing_coefficient_refuses_by_name(self):
        u = two_sided(["a", "b"], budget=1.0)
        with pytest.raises(ValueError, match="'b'"):
            u.worst_case({"a": 1.0})

    def test_a_non_finite_coefficient_refuses_by_name(self):
        u = two_sided(["a", "b"], budget=1.0)
        with pytest.raises(ValueError, match="'a'"):
            u.worst_case({"a": float("inf"), "b": 1.0})

    def test_coefficients_must_be_a_mapping(self):
        u = two_sided(["a"], budget=1.0)
        with pytest.raises(ValueError, match="mapping"):
            u.worst_case([1.0])

    def test_an_all_zero_decision_is_unprotected_and_says_so(self):
        u = two_sided(["a", "b"], mean=1.0, width=0.5, budget=1.0)
        out = u.worst_case({"a": 0.0, "b": 0.0})
        assert out.protection == 0.0
        assert out.deviating == ()
        assert out.value == pytest.approx(0.0)


class TestProtection:
    def test_it_is_the_gap_between_nominal_and_worst_case(self):
        u = two_sided(["a", "b", "c"], mean=1.0, width=0.3, budget=2.0)
        coefficients = {"a": 1.0, "b": 2.0, "c": -1.0}
        out = u.worst_case(coefficients)
        assert u.protection(coefficients) == pytest.approx(abs(out.value - out.nominal_value))

    def test_it_never_goes_negative(self):
        u = two_sided(["a", "b"], mean=-5.0, width=0.2, budget=1.0)
        assert u.protection({"a": -1.0, "b": 3.0}) >= 0.0

    @pytest.mark.parametrize("low,high", [(0.5, 1.0), (1.0, 2.0), (2.0, 3.0)])
    def test_it_is_non_decreasing_in_the_budget(self, low, high):
        coefficients = {"a": 1.0, "b": 2.0, "c": 3.0}
        lo = two_sided(["a", "b", "c"], width=0.4, budget=low).protection(coefficients)
        hi = two_sided(["a", "b", "c"], width=0.4, budget=high).protection(coefficients)
        assert hi >= lo - 1e-12

    def test_at_the_full_budget_it_is_the_whole_box(self):
        u = BudgetedMeanSet(
            nominal={"a": 1.0, "b": 1.0},
            deviation_below={"a": 0.2, "b": 0.5},
            deviation_above={"a": 0.2, "b": 0.5},
            budget=2.0,
        )
        assert u.protection({"a": 1.0, "b": 1.0}) == pytest.approx(0.7)

    def test_the_dual_reformulation_attains_the_same_value(self):
        # Bertsimas-Sim: protection(x) = min_{theta >= 0} budget*theta
        # + sum_i max(0, d_i|x_i| - theta). The minimum is attained at one of
        # the d_i|x_i| breakpoints, so evaluating there proves the LP rows a
        # solver would build reproduce this module's arithmetic.
        u = BudgetedMeanSet(
            nominal={"a": 1.0, "b": 2.0, "c": 3.0},
            deviation_below={"a": 0.3, "b": 0.1, "c": 0.5},
            deviation_above={"a": 0.2, "b": 0.7, "c": 0.4},
            budget=1.6,
        )
        coefficients = {"a": 2.0, "b": -1.0, "c": 0.5}
        counterpart = u.counterpart()
        terms = [
            counterpart.adverse_deviation(n, coefficients[n]) * abs(coefficients[n])
            for n in u.names
        ]
        candidates = [0.0, *terms]
        dual = min(
            u.budget * theta + math.fsum(max(0.0, t - theta) for t in terms)
            for theta in candidates
        )
        assert dual == pytest.approx(u.protection(coefficients))


class TestCounterpart:
    def test_it_carries_everything_a_solver_needs(self):
        u = BudgetedProbabilitySet(
            nominal={"a": 0.2},
            deviation_below={"a": 0.0},
            deviation_above={"a": 0.1},
            budget=1.0,
        )
        c = u.counterpart()
        assert isinstance(c, RobustCounterpart)
        assert c.budget == 1.0
        assert c.sense == "max"
        assert c.coefficient_domain == "nonnegative"
        assert c.lower_bound == 0.0
        assert c.upper_bound == 1.0
        assert c.deviation_above["a"] == pytest.approx(0.1)
        assert "BudgetedProbabilitySet" in c.set_ref

    def test_an_unbounded_domain_reports_no_clamp(self):
        c = two_sided(["a"], budget=1.0).counterpart()
        assert c.lower_bound is None
        assert c.upper_bound is None

    def test_it_reports_the_clamped_deviation_not_the_raw_one(self):
        u = BudgetedProbabilitySet(
            nominal={"a": 0.95},
            deviation_below={"a": 0.0},
            deviation_above={"a": 0.4},
            budget=1.0,
        )
        assert u.counterpart().deviation_above["a"] == pytest.approx(0.05)

    def test_its_mappings_cannot_be_mutated(self):
        c = two_sided(["a"], budget=1.0).counterpart()
        with pytest.raises(TypeError):
            c.deviation_above["a"] = 9.0

    def test_the_direction_rule_has_one_owner(self):
        # Whatever half the counterpart reports for a coefficient is exactly
        # the half worst_case moved. Two answers here is the divergence the
        # single rule exists to prevent.
        u = BudgetedMeanSet(
            nominal={"a": 1.0, "b": 1.0},
            deviation_below={"a": 0.3, "b": 0.9},
            deviation_above={"a": 0.8, "b": 0.2},
            budget=2.0,
        )
        c = u.counterpart()
        for coefficients in ({"a": 1.0, "b": 1.0}, {"a": -1.0, "b": 2.0}):
            out = u.worst_case(coefficients)
            for name in u.names:
                moved = abs(out.realization[name] - u.nominal[name])
                assert moved == pytest.approx(
                    c.adverse_deviation(name, coefficients[name])
                ), (name, coefficients)

    def test_a_zero_coefficient_has_no_adverse_direction(self):
        c = two_sided(["a"], budget=1.0).counterpart()
        assert c.adverse_deviation("a", 0.0) == 0.0

    def test_an_unknown_component_refuses_by_name(self):
        c = two_sided(["a"], budget=1.0).counterpart()
        with pytest.raises(ValueError, match="'z'"):
            c.adverse_deviation("z", 1.0)


class TestRealizations:
    def test_it_yields_the_consumer_pair(self):
        u = two_sided(["a", "b"], mean=1.0, width=0.1, budget=1.0, cls=BudgetedOutcomeSet)
        out = u.realizations(5, seed=0)
        assert isinstance(out, RealizationSet)
        weights, draws = out.weighted_draws()
        assert isinstance(weights, list)
        assert isinstance(draws, dict)
        assert set(draws) == {"a", "b"}
        for values in draws.values():
            assert isinstance(values, list)
            assert len(values) == len(weights)

    def test_the_weights_are_a_probability_vector(self):
        weights, _ = two_sided(["a", "b", "c"], budget=1.0).realizations(9, seed=1).weighted_draws()
        assert all(math.isfinite(w) and w >= 0.0 for w in weights)
        assert abs(math.fsum(weights) - 1.0) <= WEIGHTS_SUM_TOLERANCE

    def test_the_weighting_is_disclosed_as_a_convention(self):
        out = two_sided(["a", "b"], budget=1.0).realizations(5, seed=0)
        assert out.provenance["weighting"] == "uniform"
        assert "not" in out.provenance["weighting_note"].lower()

    def test_provenance_records_the_request_and_what_was_emitted(self):
        out = two_sided(["a", "b"], budget=1.0).realizations(7, seed=4)
        assert out.provenance["requested"] == 7
        assert out.provenance["emitted"] == len(out.weights)
        assert out.provenance["seed"] == 4
        assert out.provenance["budget"] == 1.0

    def test_every_component_varies(self):
        _weights, draws = (
            two_sided(["a", "b", "c"], budget=1.0).realizations(7, seed=2).weighted_draws()
        )
        for name, values in draws.items():
            assert len(set(values)) >= 2, f"{name} is degenerate: {values}"

    def test_the_nominal_point_is_always_present(self):
        u = two_sided(["a", "b"], mean=4.0, width=0.5, budget=1.0)
        _weights, draws = u.realizations(5, seed=0).weighted_draws()
        assert any(draws["a"][i] == 4.0 and draws["b"][i] == 4.0 for i in range(len(draws["a"])))

    def test_the_same_seed_gives_the_same_set(self):
        u = two_sided(["a", "b", "c"], budget=2.0)
        first = u.realizations(20, seed=11).weighted_draws()
        second = u.realizations(20, seed=11).weighted_draws()
        assert first == second

    def test_a_different_seed_gives_a_different_set(self):
        u = two_sided(["a", "b", "c", "d"], budget=3.0)
        first = u.realizations(24, seed=11).weighted_draws()
        second = u.realizations(24, seed=12).weighted_draws()
        assert first != second

    def test_two_members_with_one_seed_do_not_draw_alike(self):
        common = {
            "nominal": {"a": 1.0, "b": 1.0, "c": 1.0, "d": 1.0},
            "deviation_below": {"a": 0.1, "b": 0.2, "c": 0.3, "d": 0.4},
            "deviation_above": {"a": 0.1, "b": 0.2, "c": 0.3, "d": 0.4},
            "budget": 3.0,
        }
        mean_draws = BudgetedMeanSet(**common).realizations(24, seed=5).weighted_draws()
        outcome_draws = BudgetedOutcomeSet(**common).realizations(24, seed=5).weighted_draws()
        assert mean_draws != outcome_draws

    def test_every_realization_lies_inside_the_set(self):
        u = BudgetedMeanSet(
            nominal={"a": 1.0, "b": 2.0, "c": 3.0},
            deviation_below={"a": 0.3, "b": 0.1, "c": 0.5},
            deviation_above={"a": 0.2, "b": 0.7, "c": 0.4},
            budget=1.6,
        )
        _weights, draws = u.realizations(30, seed=7).weighted_draws()
        for index in range(len(draws["a"])):
            spent = 0.0
            for name in u.names:
                offset = draws[name][index] - u.nominal[name]
                half = u.deviation_above[name] if offset > 0 else u.deviation_below[name]
                share = abs(offset) / half if half else 0.0
                assert share <= 1.0 + 1e-9, (name, index)
                spent += share
            assert spent <= u.budget + 1e-9, (index, spent)

    def test_a_sub_unit_budget_scales_every_single_component_point(self):
        # With a budget below one, no component may reach its full deviation:
        # the budget, not the deviation, is the binding constraint.
        u = BudgetedMeanSet(
            nominal={"a": 1.0, "b": 1.0},
            deviation_below={"a": 0.2, "b": 0.4},
            deviation_above={"a": 0.2, "b": 0.4},
            budget=0.5,
        )
        _weights, draws = u.realizations(5, seed=0).weighted_draws()
        assert max(abs(v - 1.0) for v in draws["a"]) == pytest.approx(0.1)
        assert max(abs(v - 1.0) for v in draws["b"]) == pytest.approx(0.2)

    def test_no_point_is_emitted_twice(self):
        # The sampled tail dedups; a duplicated corner would quietly double a
        # weight and reweight the whole set.
        u = two_sided(["a", "b", "c", "d"], budget=3.0)
        out = u.realizations(30, seed=13)
        rows = list(zip(*[out.draws[n] for n in u.names]))
        assert len(set(rows)) == len(rows)

    def test_too_few_realizations_refuses_naming_the_floor(self):
        u = two_sided(["a", "b", "c"], budget=1.0)
        with pytest.raises(ValueError, match="7"):
            u.realizations(4, seed=0)

    def test_a_one_sided_member_needs_a_smaller_floor(self):
        u = BudgetedProbabilitySet(
            nominal={"a": 0.2, "b": 0.3, "c": 0.4},
            deviation_below={"a": 0.0, "b": 0.0, "c": 0.0},
            deviation_above={"a": 0.1, "b": 0.1, "c": 0.1},
            budget=1.0,
        )
        assert len(u.realizations(4, seed=0).weights) == 4

    def test_more_realizations_than_the_consumer_can_take_refuses(self):
        u = two_sided(["a", "b"], budget=1.0)
        with pytest.raises(ValueError, match=str(MAX_REALIZATIONS)):
            u.realizations(MAX_REALIZATIONS + 1, seed=0)

    @pytest.mark.parametrize("n", [2.5, "5", None, True])
    def test_a_non_integer_count_refuses(self, n):
        with pytest.raises(ValueError, match="n_realizations"):
            two_sided(["a", "b"], budget=1.0).realizations(n, seed=0)

    @pytest.mark.parametrize("seed", [1.5, "0", None, True])
    def test_a_non_integer_seed_refuses(self, seed):
        with pytest.raises(ValueError, match="seed"):
            two_sided(["a", "b"], budget=1.0).realizations(5, seed=seed)

    def test_it_never_emits_more_than_asked(self):
        u = two_sided(["a", "b", "c"], budget=3.0)
        out = u.realizations(12, seed=0)
        assert len(out.weights) <= 12

    def test_a_small_vertex_set_is_emitted_whole_rather_than_padded(self):
        # Budget 1 over two two-sided components has exactly five distinct
        # points to offer (nominal plus four single-component corners), so a
        # request for more gets five, recorded, never a duplicate.
        u = two_sided(["a", "b"], budget=1.0)
        out = u.realizations(40, seed=0)
        assert out.provenance["requested"] == 40
        assert len(out.weights) == 5
        rows = list(zip(*[out.draws[n] for n in u.names]))
        assert len(set(rows)) == len(rows)

    def test_a_realization_set_refuses_weights_that_miss_summing_to_one(self):
        with pytest.raises(ValueError, match="sum to 1"):
            RealizationSet(
                weights=(0.5, 0.4),
                draws={"a": (1.0, 2.0)},
                provenance={},
            )

    def test_a_realization_set_refuses_a_component_with_no_spread(self):
        with pytest.raises(ValueError, match="'a'"):
            RealizationSet(
                weights=(0.5, 0.5),
                draws={"a": (1.0, 1.0), "b": (1.0, 2.0)},
                provenance={},
            )

    def test_a_realization_set_refuses_a_ragged_draw(self):
        with pytest.raises(ValueError, match="one value per weight"):
            RealizationSet(weights=(0.5, 0.5), draws={"a": (1.0, 2.0), "b": (1.0,)}, provenance={})

    def test_a_realization_sets_draws_cannot_be_mutated(self):
        out = two_sided(["a", "b"], budget=1.0).realizations(5, seed=0)
        with pytest.raises(TypeError):
            out.draws["a"] = (0.0, 0.0)
        with pytest.raises(TypeError):
            out.provenance["seed"] = 99

    def test_a_realization_set_refuses_an_empty_family(self):
        with pytest.raises(ValueError, match="at least one component"):
            RealizationSet(weights=(1.0,), draws={}, provenance={})

    def test_a_realization_set_refuses_a_non_finite_draw(self):
        with pytest.raises(ValueError, match="'a'"):
            RealizationSet(
                weights=(0.5, 0.5), draws={"a": (1.0, float("nan"))}, provenance={}
            )

    def test_a_realization_set_refuses_a_negative_weight(self):
        with pytest.raises(ValueError, match="weight"):
            RealizationSet(
                weights=(-0.5, 1.5), draws={"a": (1.0, 2.0)}, provenance={}
            )


class TestValueGuards:
    """The three values refuse directly, not only through the family."""

    def test_a_worst_case_refuses_negative_protection(self):
        with pytest.raises(ValueError, match="protection"):
            WorstCase(
                value=1.0,
                nominal_value=1.0,
                protection=-0.1,
                realization={"a": 1.0},
                deviating=(),
            )

    def test_a_worst_case_refuses_an_empty_realization(self):
        with pytest.raises(ValueError, match="at least one component"):
            WorstCase(
                value=1.0, nominal_value=1.0, protection=0.0, realization={}, deviating=()
            )

    def test_a_worst_case_refuses_a_non_finite_realization(self):
        with pytest.raises(ValueError, match="realization"):
            WorstCase(
                value=1.0,
                nominal_value=1.0,
                protection=0.0,
                realization={"a": float("inf")},
                deviating=(),
            )

    @pytest.mark.parametrize("field,bad", [("value", float("nan")), ("protection", float("inf"))])
    def test_a_worst_case_refuses_a_non_finite_field(self, field, bad):
        kwargs = {
            "value": 1.0,
            "nominal_value": 1.0,
            "protection": 0.0,
            "realization": {"a": 1.0},
            "deviating": (),
        }
        kwargs[field] = bad
        with pytest.raises(ValueError, match=field):
            WorstCase(**kwargs)

    def _counterpart(self, **overrides):
        kwargs = {
            "budget": 1.0,
            "sense": "min",
            "deviation_below": {"a": 0.1},
            "deviation_above": {"a": 0.1},
            "coefficient_domain": "real",
            "lower_bound": None,
            "upper_bound": None,
            "set_ref": "x:Y",
        }
        kwargs.update(overrides)
        return RobustCounterpart(**kwargs)

    def test_a_counterpart_refuses_an_unknown_sense(self):
        with pytest.raises(ValueError, match="sense"):
            self._counterpart(sense="sideways")

    def test_a_counterpart_refuses_an_unknown_coefficient_domain(self):
        with pytest.raises(ValueError, match="coefficient_domain"):
            self._counterpart(coefficient_domain="positive")

    def test_a_counterpart_refuses_a_non_finite_budget(self):
        with pytest.raises(ValueError, match="budget"):
            self._counterpart(budget=float("nan"))

    def test_a_counterpart_refuses_an_empty_family(self):
        with pytest.raises(ValueError, match="at least one component"):
            self._counterpart(deviation_below={}, deviation_above={})

    def test_a_counterpart_refuses_mismatched_component_names(self):
        with pytest.raises(ValueError, match="same components"):
            self._counterpart(deviation_above={"b": 0.1})

    def test_a_counterpart_refuses_a_negative_half_width(self):
        with pytest.raises(ValueError, match="deviation_below"):
            self._counterpart(deviation_below={"a": -0.1})

    def test_a_counterpart_refuses_a_non_finite_coefficient(self):
        with pytest.raises(ValueError, match="coefficient"):
            self._counterpart().adverse_deviation("a", float("nan"))

    def test_a_realization_set_refuses_empty_weights(self):
        with pytest.raises(ValueError, match="non-empty"):
            RealizationSet(weights=(), draws={"a": ()}, provenance={})

    def test_a_realization_set_refuses_a_non_finite_weight(self):
        with pytest.raises(ValueError, match="weight"):
            RealizationSet(
                weights=(float("nan"), 1.0), draws={"a": (1.0, 2.0)}, provenance={}
            )


class TestTooWideToEmit:
    def test_a_family_too_wide_for_any_request_refuses_about_itself(self):
        names = [f"c{i:03d}" for i in range(MAX_REALIZATIONS)]
        u = two_sided(names, budget=1.0)
        with pytest.raises(ValueError, match="use counterpart"):
            u.realizations(MAX_REALIZATIONS, seed=0)

    def test_the_widest_emittable_family_still_works(self):
        width = (MAX_REALIZATIONS - 1) // 2
        names = [f"c{i:03d}" for i in range(width)]
        u = two_sided(names, budget=1.0)
        assert len(u.realizations(1 + 2 * width, seed=0).weights) == 1 + 2 * width


class TestTemplateEnforcement:
    @pytest.mark.parametrize(
        "final", ["worst_case", "protection", "counterpart", "realizations"]
    )
    def test_a_subclass_that_replaces_a_template_refuses_at_definition(self, final):
        with pytest.raises(TypeError, match=final):
            type("Replacing", (BudgetedMeanSet,), {final: lambda self, *a, **k: None})

    @pytest.mark.parametrize("hook", ["worst_case_sense", "component_bounds", "coefficient_domain"])
    def test_every_hook_stays_overridable(self, hook):
        cls = type("Narrowed", (BudgetedMeanSet,), {hook: lambda self: None})
        assert issubclass(cls, BudgetedUncertaintySet)

    def test_an_incomplete_member_cannot_be_constructed(self):
        class Partial(BudgetedUncertaintySet):
            def worst_case_sense(self):
                return "min"

        with pytest.raises(TypeError):
            Partial(
                nominal={"a": 0.0},
                deviation_below={"a": 1.0},
                deviation_above={"a": 1.0},
                budget=1.0,
            )

    def test_a_hook_answering_outside_its_vocabulary_refuses(self):
        cls = type("BadSense", (BudgetedMeanSet,), {"worst_case_sense": lambda self: "sideways"})
        with pytest.raises(ValueError, match="sideways"):
            cls(
                nominal={"a": 0.0},
                deviation_below={"a": 1.0},
                deviation_above={"a": 1.0},
                budget=1.0,
            )

    def test_a_hook_answering_an_unknown_coefficient_domain_refuses(self):
        cls = type("BadDomain", (BudgetedMeanSet,), {"coefficient_domain": lambda self: "positive"})
        with pytest.raises(ValueError, match="positive"):
            cls(
                nominal={"a": 0.0},
                deviation_below={"a": 1.0},
                deviation_above={"a": 1.0},
                budget=1.0,
            )

    def test_component_bounds_must_be_a_pair(self):
        cls = type("BadShape", (BudgetedMeanSet,), {"component_bounds": lambda self: None})
        with pytest.raises(ValueError, match="component_bounds"):
            cls(
                nominal={"a": 0.5},
                deviation_below={"a": 0.1},
                deviation_above={"a": 0.1},
                budget=1.0,
            )

    def test_a_non_finite_component_bound_refuses(self):
        cls = type(
            "NanBound", (BudgetedMeanSet,), {"component_bounds": lambda self: (float("nan"), 1.0)}
        )
        with pytest.raises(ValueError, match="component_bounds"):
            cls(
                nominal={"a": 0.5},
                deviation_below={"a": 0.1},
                deviation_above={"a": 0.1},
                budget=1.0,
            )

    def test_inverted_component_bounds_refuse(self):
        cls = type("BadBounds", (BudgetedMeanSet,), {"component_bounds": lambda self: (1.0, 0.0)})
        with pytest.raises(ValueError, match="component_bounds"):
            cls(
                nominal={"a": 0.5},
                deviation_below={"a": 0.1},
                deviation_above={"a": 0.1},
                budget=1.0,
            )


class TestTheThreeMembers:
    def test_the_estimation_member_and_the_outcome_member_are_separate_classes(self):
        assert not issubclass(BudgetedMeanSet, BudgetedOutcomeSet)
        assert not issubclass(BudgetedOutcomeSet, BudgetedMeanSet)
        assert BudgetedMeanSet is not BudgetedOutcomeSet

    def test_neither_can_see_the_others_numbers(self):
        mean = two_sided(["a"], mean=1.0, width=0.1, budget=1.0, cls=BudgetedMeanSet)
        outcome = two_sided(["a"], mean=1.0, width=0.9, budget=1.0, cls=BudgetedOutcomeSet)
        assert mean.protection({"a": 1.0}) == pytest.approx(0.1)
        assert outcome.protection({"a": 1.0}) == pytest.approx(0.9)

    @pytest.mark.parametrize("cls", [BudgetedMeanSet, BudgetedOutcomeSet])
    def test_the_two_sided_members_declare_the_same_geometry(self, cls):
        u = two_sided(["a"], budget=1.0, cls=cls)
        assert u.worst_case_sense() == "min"
        assert u.coefficient_domain() == "real"
        assert u.component_bounds() == (None, None)

    def test_every_member_is_a_family_member(self):
        for cls in (BudgetedProbabilitySet, BudgetedMeanSet, BudgetedOutcomeSet):
            assert issubclass(cls, BudgetedUncertaintySet)

    def test_a_worst_case_is_a_frozen_value(self):
        out = two_sided(["a"], budget=1.0).worst_case({"a": 1.0})
        assert isinstance(out, WorstCase)
        with pytest.raises(AttributeError):
            out.value = 1.0
        with pytest.raises(TypeError):
            out.realization["a"] = 1.0


class TestRegistry:
    @pytest.mark.parametrize("name", ["probability", "mean", "outcome"])
    def test_the_three_members_ship_registered(self, name):
        assert issubclass(uncertainty_set(name)["cls"], BudgetedUncertaintySet)
        assert uncertainty_set(name)["doc"]

    def test_an_unknown_name_refuses_and_lists_what_is_registered(self):
        with pytest.raises(ValueError, match="mean"):
            uncertainty_set("nope")

    def test_a_duplicate_name_refuses(self):
        with pytest.raises(ValueError, match="already registered"):
            register_uncertainty_set("mean", BudgetedMeanSet)

    def test_a_non_member_refuses(self):
        with pytest.raises(ValueError, match="BudgetedUncertaintySet"):
            register_uncertainty_set("bogus", dict)

    def test_a_project_can_bring_its_own_member(self):
        cls = type("Extra", (BudgetedMeanSet,), {})
        register_uncertainty_set("extra-member", cls, doc="A project's own.")
        try:
            assert uncertainty_set("extra-member")["cls"] is cls
        finally:
            del UNCERTAINTY_SETS["extra-member"]


class TestConsumerAgreement:
    def test_the_realization_ceiling_matches_the_consuming_solver(self):
        from dskit.pipeline.libs.pyomo import HARD_N_SCENARIOS_CEILING

        assert MAX_REALIZATIONS == HARD_N_SCENARIOS_CEILING

    def test_the_weight_tolerance_is_tighter_than_the_consumers(self):
        assert WEIGHTS_SUM_TOLERANCE < CONSUMER_WEIGHT_TOLERANCE

    def test_a_set_this_module_emits_passes_the_consumers_own_screen(self):
        # The consumer's screen, restated by hand from
        # ScenarioUtilitySolve.build_model.
        u = two_sided(["a", "b"], mean=1.0, width=0.02, budget=1.0, cls=BudgetedOutcomeSet)
        weights, r = u.realizations(5, seed=0).weighted_draws()
        assert len(weights) > 0
        assert all(math.isfinite(w) and w >= 0.0 for w in weights)
        assert abs(math.fsum(weights) - 1.0) <= CONSUMER_WEIGHT_TOLERANCE
        assert len(weights) <= MAX_REALIZATIONS
        for name, values in r.items():
            assert len(values) == len(weights), name
            assert all(math.isfinite(v) for v in values), name


class TestEndToEndAgainstTheRealConsumer:
    def test_the_outcome_member_drives_a_real_scenario_solve(self):
        pytest.importorskip("numpy")
        pytest.importorskip("pyomo")
        pytest.importorskip("highspy")

        from dskit.pipeline.libs.pyomo import ScenarioUtilitySolve

        uset = BudgetedOutcomeSet(
            nominal={"a": 1.004, "b": 1.002},
            deviation_below={"a": 0.010, "b": 0.006},
            deviation_above={"a": 0.010, "b": 0.006},
            budget=1.0,
        )

        class Solve(ScenarioUtilitySolve):
            outputs = ("target", "trades", "cash_after", "metrics")

            def instruments(self, inputs):
                rows = {
                    "a": {"price": 10.0, "held": 0, "x_max": 400.0, "cost_buy": 0.0,
                          "cost_sell": 0.0},
                    "b": {"price": 20.0, "held": 0, "x_max": 400.0, "cost_buy": 0.0,
                          "cost_sell": 0.0},
                }
                account = {
                    "cash": 1000.0, "buying_power": 1000.0, "wealth_lo": 900.0,
                    "wealth_hi": 1100.0, "sale_credit": 1.0,
                }
                return ["a", "b"], rows, account

            def payoffs(self, inputs):
                return uset.realizations(5, seed=0).weighted_draws()

            def domain_constraints(self, model, inputs, params):
                return None

        node = Solve(
            "solve",
            {
                "risk_aversion_gamma": 2.0,
                "cvar_alpha": 0.9,
                "cvar_limit": None,
                "cardinality": 2,
                "min_ticket": 0.0,
            },
        )
        out = node.run(None, {})
        assert set(out["target"]) <= {"a", "b"}
        assert math.isfinite(out["cash_after"])
