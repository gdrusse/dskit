"""Contract for the mean-effect interval family under temporal dependence."""

from __future__ import annotations

import math
import random

import pytest

import dskit.pipeline.mean_interval as mean_interval_module
from dskit.pipeline.mean_interval import (
    DEFAULT_LEVEL,
    MEAN_INTERVAL_ESTIMATORS,
    MIN_INDEPENDENT_UNITS,
    ClusterBootstrapInterval,
    ConfidenceInterval,
    MeanEvidence,
    MeanIntervalEstimator,
    MeanIntervalResult,
    NeweyWestInterval,
    WidenedInterval,
    mean_interval_estimator,
    register_mean_interval_estimator,
)
from dskit.pipeline.stats import cluster_bootstrap_t, dm_lags, newey_west_mean


def _unit_values(n_units=12, per_unit=5, step=0.1):
    """A synthetic sample: one label per unit, a mild per-unit level shift."""
    values, units = [], []
    for u in range(n_units):
        level = step * ((u % 4) - 1.5)
        for k in range(per_unit):
            values.append(1.0 + level + 0.01 * k)
            units.append(f"u{u:02d}")
    return values, units


def _noise(n, seed):
    """A deterministic LCG stream in [-0.5, 0.5), so no RNG is depended on."""
    out, state = [], seed
    for _ in range(n):
        state = (1103515245 * state + 12345) % 2147483648
        out.append(state / 2147483648.0 - 0.5)
    return out


def _overlapping_mean(n, window, seed=11):
    """``n`` rows of a ``window``-step forward mean — a real overlapping label.

    Row ``i`` and row ``i + k`` share ``window - k`` of the same shocks, so
    the series has POSITIVE autocovariance out to lag ``window - 1``: the
    exact structure a caller states as ``overlap_steps=window - 1``.
    """
    raw = _noise(n + window, seed)
    return [sum(raw[i:i + window]) / window for i in range(n)]


class TestMeanEvidenceRefusesAnUnstatedDependence:
    def test_neither_units_nor_overlap_refuses_by_name(self):
        with pytest.raises(ValueError) as err:
            MeanEvidence([1.0, 2.0, 3.0])
        message = str(err.value)
        assert "units" in message
        assert "overlap_steps" in message

    def test_units_alone_is_a_complete_statement(self):
        ev = MeanEvidence([1.0, 2.0], units=["a", "b"])
        assert ev.units == ("a", "b")
        assert ev.overlap_steps is None

    def test_overlap_alone_is_a_complete_statement(self):
        ev = MeanEvidence([1.0, 2.0, 3.0], overlap_steps=1)
        assert ev.overlap_steps == 1
        assert ev.units is None

    def test_both_together_are_accepted(self):
        ev = MeanEvidence([1.0, 2.0, 3.0, 4.0], units=["a", "a", "b", "b"], overlap_steps=1)
        assert ev.units == ("a", "a", "b", "b")
        assert ev.overlap_steps == 1


class TestMeanEvidenceRefusesUnusableInput:
    @pytest.mark.parametrize("bad", [float("nan"), float("inf"), -float("inf"), None, "1.0", True])
    def test_a_non_finite_or_non_numeric_value_refuses_by_index(self, bad):
        with pytest.raises(ValueError) as err:
            MeanEvidence([1.0, bad, 3.0], overlap_steps=0)
        assert "values[1]" in str(err.value)

    def test_fewer_than_two_values_refuses(self):
        with pytest.raises(ValueError):
            MeanEvidence([1.0], overlap_steps=0)

    def test_units_of_the_wrong_length_refuses(self):
        with pytest.raises(ValueError) as err:
            MeanEvidence([1.0, 2.0, 3.0], units=["a", "b"])
        assert "3" in str(err.value) and "2" in str(err.value)

    @pytest.mark.parametrize("bad", ["", 3, None, 1.5, True])
    def test_a_unit_label_the_repo_rule_refuses_is_named_by_index(self, bad):
        with pytest.raises(ValueError) as err:
            MeanEvidence([1.0, 2.0], units=["a", bad])
        assert "units[1]" in str(err.value)

    @pytest.mark.parametrize("bad", [-1, 1.0, True, "2", float("nan")])
    def test_an_implausible_overlap_refuses(self, bad):
        with pytest.raises(ValueError) as err:
            MeanEvidence([1.0, 2.0, 3.0], overlap_steps=bad)
        assert "overlap_steps" in str(err.value)

    def test_an_overlap_that_spans_the_whole_sample_refuses(self):
        with pytest.raises(ValueError) as err:
            MeanEvidence([1.0, 2.0, 3.0], overlap_steps=3)
        assert "overlap_steps" in str(err.value)


class TestMeanEvidenceGroupingAndRuns:
    """The two accessors a unit-reading member leans on, tested directly.

    Both raise when the evidence states no units, and both used to be reached
    only THROUGH ``ClusterBootstrapInterval`` — where another guard fires first
    on the same input, so deleting either refusal changed nothing observable.
    """

    def test_grouping_keeps_each_units_values_in_order(self):
        ev = MeanEvidence([1.0, 2.0, 3.0, 4.0], units=["a", "b", "a", "b"])
        assert ev.grouped() == {"a": [1.0, 3.0], "b": [2.0, 4.0]}

    def test_grouping_refuses_evidence_that_states_no_units(self):
        with pytest.raises(ValueError) as err:
            MeanEvidence([1.0, 2.0, 3.0], overlap_steps=1).grouped()
        assert "group-based methods need one" in str(err.value)

    @pytest.mark.parametrize(
        "units, shortest",
        [
            (["a", "a", "a", "b", "b"], 2),
            (["a", "b", "b", "b", "b"], 1),
            (["a", "a", "b", "b", "b", "c", "c", "c", "c"], 2),
            (["a", "a", "b", "b"], 2),
            # A label that comes back after an interruption owns TWO runs, and
            # the shorter one is what an overlap has to fit inside.
            (["a", "a", "a", "b", "a"], 1),
        ],
    )
    def test_the_shortest_run_is_the_shortest_and_not_the_longest(self, units, shortest):
        ev = MeanEvidence([1.0] * len(units), units=units)
        assert ev.shortest_unit_run() == shortest

    def test_the_run_measure_refuses_evidence_that_states_no_units(self):
        with pytest.raises(ValueError) as err:
            MeanEvidence([1.0, 2.0, 3.0], overlap_steps=1).shortest_unit_run()
        assert "there are no runs" in str(err.value)

    def test_the_overlap_screen_reads_the_shortest_run_not_the_longest(self):
        # Runs of 4 and 2. An overlap of 2 fits inside the LONG units and not
        # the short one, so it must refuse: reading `max` instead of `min`
        # would wave it through.
        units = ["a"] * 4 + ["b"] * 2 + ["c"] * 4 + ["d"] * 4
        values = [1.0 + 0.1 * (i % 5) for i in range(len(units))]
        ev = MeanEvidence(values, units=units, overlap_steps=2)
        assert ev.shortest_unit_run() == 2
        with pytest.raises(ValueError) as err:
            ClusterBootstrapInterval().independent_units(ev)
        assert "shortest unit run (2 observations)" in str(err.value)


class TestMeanIntervalResultCannotHoldAContradiction:
    def _kwargs(self, **over):
        base = dict(
            mean=1.0,
            standard_error=0.5,
            low=0.0,
            high=2.0,
            level=0.95,
            independent_units=10,
            method="m",
        )
        base.update(over)
        return base

    def test_a_low_above_the_mean_refuses(self):
        with pytest.raises(ValueError):
            MeanIntervalResult(**self._kwargs(low=1.5))

    def test_a_high_below_the_mean_refuses(self):
        with pytest.raises(ValueError):
            MeanIntervalResult(**self._kwargs(high=0.5))

    @pytest.mark.parametrize("field", ["mean", "standard_error", "low", "high"])
    def test_a_non_finite_field_refuses(self, field):
        with pytest.raises(ValueError):
            MeanIntervalResult(**self._kwargs(**{field: float("nan")}))

    @pytest.mark.parametrize("bad", [-0.1, 0.0, -0.0])
    def test_a_non_positive_standard_error_refuses(self, bad):
        # ZERO is the boundary the `<= 0.0` guard exists for: a zero-width
        # interval reads as certainty. A test that only tried -0.1 would
        # survive the guard being loosened to `< 0.0`.
        with pytest.raises(ValueError) as err:
            MeanIntervalResult(**self._kwargs(standard_error=bad))
        assert "not evidence of certainty" in str(err.value)

    @pytest.mark.parametrize("bad", ["", None, 7, b"m"])
    def test_a_missing_or_non_string_method_refuses(self, bad):
        with pytest.raises(ValueError) as err:
            MeanIntervalResult(**self._kwargs(method=bad))
        assert "method must be a non-empty string" in str(err.value)

    @pytest.mark.parametrize("bad", [1.5, "10", True, None, 10.0])
    def test_a_non_integer_unit_count_refuses(self, bad):
        with pytest.raises(ValueError) as err:
            MeanIntervalResult(**self._kwargs(independent_units=bad))
        assert "independent_units" in str(err.value)

    @pytest.mark.parametrize("bad", [0.0, 1.0, 1.5, -0.2])
    def test_a_level_outside_the_open_unit_refuses(self, bad):
        with pytest.raises(ValueError):
            MeanIntervalResult(**self._kwargs(level=bad))

    def test_too_few_independent_units_refuses(self):
        with pytest.raises(ValueError):
            MeanIntervalResult(**self._kwargs(independent_units=1))

    def test_the_deviations_are_the_two_half_widths(self):
        out = MeanIntervalResult(**self._kwargs(mean=1.0, low=0.4, high=2.2))
        assert out.deviation_below == pytest.approx(0.6)
        assert out.deviation_above == pytest.approx(1.2)
        assert out.width == pytest.approx(1.8)


class TestTheDoorwayIsAbstract:
    def test_an_incomplete_subclass_refuses_at_construction(self):
        class Half(MeanIntervalEstimator):
            def independent_units(self, evidence):
                return 10

        with pytest.raises(TypeError):
            Half()

    def test_a_member_never_overrides_the_template(self):
        for entry in MEAN_INTERVAL_ESTIMATORS.values():
            assert entry["cls"].interval is MeanIntervalEstimator.interval


class TestEachMemberRefusesTheSpellingItCannotRead:
    def test_the_cluster_member_refuses_evidence_with_no_units(self):
        # The match string names THIS guard's own words. A loose `"units" in
        # message` could not tell it apart from `shortest_unit_run`'s own
        # None-check, which fires on the same input and also says "units" —
        # so deleting this guard would have passed a looser assertion.
        ev = MeanEvidence(_overlapping_mean(200, 4), overlap_steps=3)
        with pytest.raises(ValueError) as err:
            ClusterBootstrapInterval().interval(ev)
        assert "resamples units and this evidence states" in str(err.value)

    def test_the_hac_member_refuses_evidence_with_no_overlap(self):
        values, units = _unit_values()
        ev = MeanEvidence(values, units=units)
        with pytest.raises(ValueError) as err:
            NeweyWestInterval().interval(ev)
        assert "reads overlap_steps and this evidence" in str(err.value)


class TestTooFewIndependentUnitsRefuses:
    def test_the_cluster_member_refuses_below_the_floor(self):
        values, units = _unit_values(n_units=MIN_INDEPENDENT_UNITS - 1)
        ev = MeanEvidence(values, units=units)
        with pytest.raises(ValueError) as err:
            ClusterBootstrapInterval().interval(ev)
        message = str(err.value)
        assert str(MIN_INDEPENDENT_UNITS) in message
        assert str(MIN_INDEPENDENT_UNITS - 1) in message

    def test_the_hac_member_refuses_when_the_overlap_eats_the_sample(self):
        # 30 rows overlapping by 9 steps leave only 3 non-overlapping blocks.
        ev = MeanEvidence(_overlapping_mean(30, 4), overlap_steps=9)
        with pytest.raises(ValueError) as err:
            NeweyWestInterval().interval(ev)
        assert str(MIN_INDEPENDENT_UNITS) in str(err.value)


class TestOverlapAcrossUnitBoundariesRefuses:
    def test_a_label_reaching_into_the_next_unit_refuses(self):
        # Again the guard's own words: "overlap" alone appears in several
        # neighbouring refusals, so it could not prove WHICH one fired.
        values, units = _unit_values(n_units=12, per_unit=4)
        ev = MeanEvidence(values, units=units, overlap_steps=4)
        with pytest.raises(ValueError) as err:
            ClusterBootstrapInterval().interval(ev)
        message = str(err.value)
        assert "reaches past" in message
        assert "shortest unit run (4 observations)" in message

    def test_an_overlap_contained_inside_the_unit_is_fine(self):
        values, units = _unit_values(n_units=12, per_unit=4)
        ev = MeanEvidence(values, units=units, overlap_steps=3)
        out = ClusterBootstrapInterval().interval(ev)
        assert out.low < out.mean < out.high


class TestTheIntervalItself:
    def _cluster_evidence(self):
        values, units = _unit_values(n_units=20, per_unit=5)
        return MeanEvidence(values, units=units)

    def test_the_cluster_member_brackets_its_own_mean(self):
        out = ClusterBootstrapInterval().interval(self._cluster_evidence())
        assert out.low < out.mean < out.high
        assert out.standard_error > 0.0
        assert out.level == DEFAULT_LEVEL
        assert out.independent_units == 20

    @pytest.mark.parametrize(
        "n, overlap, units, critical",
        [
            # Published two-sided 97.5% Student t critical values. Restated
            # here DELIBERATELY rather than read back from the implementation:
            # the df must be the INDEPENDENT-UNIT count, never the row count,
            # and an assertion sourced from the code it checks asserts nothing.
            (400, 3, 100, 1.984217),   # df = 99
            (400, 39, 10, 2.262157),   # df = 9
            (400, 19, 20, 2.093024),   # df = 19
        ],
    )
    def test_the_hac_critical_value_is_student_t_on_the_independent_units(
        self, n, overlap, units, critical
    ):
        values = [1.0 + (i % 7) * 0.13 + (i % 3) * 0.05 for i in range(n)]
        out = NeweyWestInterval().interval(MeanEvidence(values, overlap_steps=overlap))
        assert out.independent_units == units
        assert out.deviation_above / out.standard_error == pytest.approx(critical, abs=1e-6)
        assert out.deviation_below / out.standard_error == pytest.approx(critical, abs=1e-6)

    def test_the_hac_member_brackets_its_own_mean(self):
        ev = MeanEvidence(_overlapping_mean(400, 6), overlap_steps=5)
        out = NeweyWestInterval().interval(ev)
        assert out.low < out.mean < out.high
        assert out.independent_units == 400 // 6

    def test_identical_inputs_give_identical_numbers(self):
        ev = self._cluster_evidence()
        first = ClusterBootstrapInterval().interval(ev)
        second = ClusterBootstrapInterval().interval(ev)
        assert (first.mean, first.standard_error, first.low, first.high) == (
            second.mean,
            second.standard_error,
            second.low,
            second.high,
        )

    def test_the_bootstrap_honours_the_stated_level(self):
        # Strict, and over three levels: an implementation that ignored the
        # argument and always asked its delegate for one fixed alpha would
        # return three identical widths.
        ev = self._cluster_evidence()
        widths = [
            ClusterBootstrapInterval().interval(ev, level=c).width
            for c in (0.80, 0.95, 0.99)
        ]
        assert widths[0] < widths[1] < widths[2]

    def test_a_higher_level_is_never_narrower(self):
        ev = self._cluster_evidence()
        narrow = ClusterBootstrapInterval().interval(ev, level=0.80)
        wide = ClusterBootstrapInterval().interval(ev, level=0.99)
        assert wide.width >= narrow.width

        hac_ev = MeanEvidence(_overlapping_mean(400, 6), overlap_steps=5)
        assert (
            NeweyWestInterval().interval(hac_ev, level=0.99).width
            > NeweyWestInterval().interval(hac_ev, level=0.80).width
        )

    def test_stating_the_real_overlap_widens_the_interval(self):
        # The defect this module exists to prevent: rows carrying a 13-step
        # label, described as though they were independent, give an interval
        # far narrower than the same rows described truthfully.
        values = _overlapping_mean(400, 13)
        lie = NeweyWestInterval().interval(MeanEvidence(values, overlap_steps=0))
        truth = NeweyWestInterval().interval(MeanEvidence(values, overlap_steps=12))
        assert truth.width > lie.width
        assert truth.deviation_below > lie.deviation_below
        assert truth.deviation_above > lie.deviation_above

    def test_more_lags_is_not_a_universal_widening_law(self):
        # Deliberately recorded, not asserted away: a Newey-West long-run
        # variance can SHRINK when the extra autocovariances are negative.
        # The module makes the caller STATE the overlap; it does not promise
        # that a larger number always buys a wider interval.
        values = [1.0 + (1.0 if i % 2 else -1.0) * 0.1 * (1 + i % 3) for i in range(400)]
        alternating = NeweyWestInterval().interval(MeanEvidence(values, overlap_steps=1))
        independent = NeweyWestInterval().interval(MeanEvidence(values, overlap_steps=0))
        assert alternating.width < independent.width

    @pytest.mark.parametrize("bad", [0.0, 1.0, -0.1, 1.2, "0.95", None, True])
    def test_an_unusable_level_refuses(self, bad):
        ev = self._cluster_evidence()
        with pytest.raises(ValueError):
            ClusterBootstrapInterval().interval(ev, level=bad)

    def test_a_non_evidence_argument_refuses(self):
        with pytest.raises(ValueError):
            ClusterBootstrapInterval().interval({"values": [1.0, 2.0]})


class TestADegenerateSampleRefusesRatherThanNarrowing:
    def test_a_constant_series_never_yields_a_zero_width_interval(self):
        values = [2.0] * 100
        with pytest.raises(ValueError) as err:
            NeweyWestInterval().interval(MeanEvidence(values, overlap_steps=2))
        assert "standard error" in str(err.value).lower()

    def test_constant_units_never_yield_a_zero_width_interval(self):
        values = [1.0] * 60
        units = [f"u{i // 5:02d}" for i in range(60)]
        with pytest.raises(ValueError):
            ClusterBootstrapInterval().interval(MeanEvidence(values, units=units))

    def test_an_unclaimable_bootstrap_bound_is_raised_not_passed_on(self):
        # Seven identical units and one outlier: a third of the replicates omit
        # the outlier entirely and have no variance at all, so the upper pivot
        # tail is degenerate and the delegate claims no `ci_high`. That missing
        # bound must become a refusal, never a None the consumer reads as
        # "no adjustment".
        values, units = [], []
        for u in range(8):
            for _ in range(4):
                values.append(5.0 if u == 7 else 1.0)
                units.append(f"u{u}")
        ev = MeanEvidence(values, units=units)
        with pytest.raises(ValueError) as err:
            ClusterBootstrapInterval().interval(ev)
        message = str(err.value)
        assert "degenerate" in message
        assert "8 units" in message


class TestTheArithmeticIsDelegatedNotRederived:
    def test_the_cluster_mean_and_se_are_the_stats_owner_s(self):
        values, units = _unit_values(n_units=20, per_unit=5)
        ev = MeanEvidence(values, units=units)
        grouped = {}
        for value, unit in zip(values, units):
            grouped.setdefault(unit, []).append(value)
        owner = cluster_bootstrap_t(grouped, 1, 0, label="")
        out = ClusterBootstrapInterval(seed=0, label="").interval(ev)
        assert out.mean == owner["mean"]
        assert out.standard_error == owner["se"]

    def test_the_hac_mean_and_se_are_the_stats_owner_s(self):
        values = _overlapping_mean(400, 6)
        owner = newey_west_mean(values, lags=5)
        out = NeweyWestInterval().interval(MeanEvidence(values, overlap_steps=5))
        assert out.mean == owner["mean"]
        assert out.standard_error == owner["se"]


class TestTheRegistry:
    def test_both_members_ship(self):
        assert mean_interval_estimator("cluster-bootstrap-t")["cls"] is ClusterBootstrapInterval
        assert mean_interval_estimator("newey-west")["cls"] is NeweyWestInterval

    def test_an_unknown_name_lists_what_is_known(self):
        with pytest.raises(ValueError) as err:
            mean_interval_estimator("nope")
        assert "cluster-bootstrap-t" in str(err.value)

    def test_a_duplicate_name_refuses(self):
        with pytest.raises(ValueError):
            register_mean_interval_estimator("cluster-bootstrap-t", ClusterBootstrapInterval)

    def test_a_non_member_class_refuses(self):
        with pytest.raises(ValueError):
            register_mean_interval_estimator("bad", dict)

    def test_an_empty_name_refuses(self):
        with pytest.raises(ValueError):
            register_mean_interval_estimator("", ClusterBootstrapInterval)


# ---------------------------------------------------------------------------
# What the level CLAIMS — ADR-0151 correction round, METHOD-M1
# ---------------------------------------------------------------------------


class _Stub(MeanIntervalEstimator):
    """A minimal member the template's own screens can be aimed at."""

    result_class = WidenedInterval
    units = 10
    stub_mean = 1.0
    stub_se = 1.0

    def independent_units(self, evidence):
        return self.units

    def mean_and_se(self, evidence):
        return self.stub_mean, self.stub_se

    def bounds(self, evidence, mean, se, level):
        return mean - se, mean + se


def _plain_evidence():
    """Forty rows stated as non-overlapping — enough to clear every floor."""
    return MeanEvidence([1.0 + 0.01 * (i % 7) for i in range(40)], overlap_steps=0)


def _cluster_ev(n_units=20, per_unit=5):
    values, units = _unit_values(n_units=n_units, per_unit=per_unit)
    return MeanEvidence(values, units=units)


class TestEveryMemberDeclaresWhatItsLevelClaims:
    def test_the_bootstrap_member_claims_a_measured_confidence_level(self):
        out = ClusterBootstrapInterval().interval(_cluster_ev())
        assert type(out) is ConfidenceInterval
        assert isinstance(out, MeanIntervalResult)

    def test_the_hac_member_claims_only_a_widening(self):
        # The whole of METHOD-M1: this member was MEASURED at 88-91% against
        # a nominal 95%, so it must not hand back a value whose type says
        # "confidence".
        ev = MeanEvidence(_overlapping_mean(400, 6), overlap_steps=5)
        out = NeweyWestInterval().interval(ev)
        assert type(out) is WidenedInterval
        assert not isinstance(out, ConfidenceInterval)

    def test_the_two_claims_are_distinct_types(self):
        assert not issubclass(WidenedInterval, ConfidenceInterval)
        assert not issubclass(ConfidenceInterval, WidenedInterval)

    def test_the_field_is_named_level_and_carries_no_coverage_promise(self):
        out = ClusterBootstrapInterval().interval(_cluster_ev())
        assert out.level == DEFAULT_LEVEL
        assert not hasattr(out, "confidence")

    def test_a_member_that_declares_no_claim_cannot_be_constructed(self):
        class Undeclared(MeanIntervalEstimator):
            def independent_units(self, evidence):
                return 10

            def mean_and_se(self, evidence):
                return 1.0, 1.0

            def bounds(self, evidence, mean, se, level):
                return mean - se, mean + se

        with pytest.raises(TypeError):
            Undeclared()

    def test_a_member_naming_the_bare_base_is_refused(self):
        class Bare(_Stub):
            result_class = MeanIntervalResult

        with pytest.raises(ValueError) as err:
            Bare().interval(_plain_evidence())
        assert "strict MeanIntervalResult" in str(err.value)

    @pytest.mark.parametrize("bad", [dict, None, "ConfidenceInterval", 7])
    def test_a_member_naming_a_non_result_class_is_refused(self, bad):
        class Wrong(_Stub):
            result_class = bad

        with pytest.raises(ValueError) as err:
            Wrong().interval(_plain_evidence())
        assert "strict MeanIntervalResult" in str(err.value)

    def test_both_registered_members_declare_a_strict_subclass(self):
        for name, entry in MEAN_INTERVAL_ESTIMATORS.items():
            claimed = entry["cls"].result_class
            assert isinstance(claimed, type), name
            assert issubclass(claimed, MeanIntervalResult), name
            assert claimed is not MeanIntervalResult, name


class TestTheTemplateIsFinalAndEnforced:
    def test_a_subclass_that_overrides_the_template_refuses_at_definition(self):
        # METHOD-M3: a docstring saying "never overridden" is not enforcement.
        with pytest.raises(TypeError) as err:

            class Sneaky(_Stub):
                def interval(self, evidence, *, level=DEFAULT_LEVEL):
                    return "whatever I like"

        message = str(err.value)
        assert "overrides interval" in message
        assert "final" in message

    def test_a_shipped_member_cannot_be_subclassed_into_overriding_it(self):
        with pytest.raises(TypeError):

            class SneakyBootstrap(ClusterBootstrapInterval):
                def interval(self, evidence, *, level=DEFAULT_LEVEL):
                    return None

    def test_every_hook_stays_overridable(self):
        class Custom(_Stub):
            def independent_units(self, evidence):
                return 12

            def mean_and_se(self, evidence):
                return 2.0, 0.5

            def bounds(self, evidence, mean, se, level):
                return mean - 2.0 * se, mean + 2.0 * se

            @property
            def minimum_units(self):
                return 3

        out = Custom().interval(_plain_evidence())
        assert (out.mean, out.independent_units, out.width) == (2.0, 12, 2.0)


class TestTheTemplateScreensItsOwnHooks:
    @pytest.mark.parametrize("bad", [float("nan"), float("inf"), -float("inf")])
    def test_a_hook_returning_a_non_finite_mean_refuses(self, bad):
        class BadMean(_Stub):
            stub_mean = bad

        with pytest.raises(ValueError) as err:
            BadMean().interval(_plain_evidence())
        assert "non-finite mean" in str(err.value)

    @pytest.mark.parametrize("bad", [float("nan"), float("inf")])
    def test_a_hook_returning_a_non_finite_standard_error_refuses(self, bad):
        class BadSe(_Stub):
            stub_se = bad

        with pytest.raises(ValueError) as err:
            BadSe().interval(_plain_evidence())
        assert "non-finite standard error" in str(err.value)

    @pytest.mark.parametrize("bad", [0.0, -1.0])
    def test_a_hook_returning_no_spread_refuses(self, bad):
        class NoSpread(_Stub):
            stub_se = bad

        with pytest.raises(ValueError) as err:
            NoSpread().interval(_plain_evidence())
        assert "no spread in this sample" in str(err.value)

    @pytest.mark.parametrize("bad", [1.5, "10", True, None])
    def test_a_hook_returning_a_non_integer_unit_count_refuses(self, bad):
        class BadUnits(_Stub):
            units = bad

        with pytest.raises(ValueError) as err:
            BadUnits().interval(_plain_evidence())
        assert "independent_units" in str(err.value)

    def test_the_floor_is_the_members_own_fact(self):
        class Lenient(_Stub):
            units = 3

            @property
            def minimum_units(self):
                return 3

        assert Lenient().interval(_plain_evidence()).independent_units == 3

        class Strict(_Stub):
            units = 3

        with pytest.raises(ValueError) as err:
            Strict().interval(_plain_evidence())
        assert str(MIN_INDEPENDENT_UNITS) in str(err.value)


class TestTheStudentInversionRefusesAnUnbracketableLevel:
    class _TwoBlocks(NeweyWestInterval):
        @property
        def minimum_units(self):
            return 2

    def test_a_level_needing_a_critical_value_past_the_ceiling_refuses(self):
        # df = 1 (two blocks) is where the Cauchy tail is still above a
        # representable target past 1e8; the ceiling refuses rather than
        # silently clamping to a finite bound.
        ev = MeanEvidence([1.0, 2.0, 3.0, 5.0], overlap_steps=1)
        assert self._TwoBlocks().independent_units(ev) == 2
        with pytest.raises(ValueError) as err:
            self._TwoBlocks().interval(ev, level=1.0 - 1e-9)
        message = str(err.value)
        assert "1e+08" in message
        assert "df=1" in message

    def test_an_ordinary_level_brackets_fine_at_the_same_df(self):
        ev = MeanEvidence([1.0, 2.0, 3.0, 5.0], overlap_steps=1)
        out = self._TwoBlocks().interval(ev, level=0.95)
        assert out.low < out.mean < out.high


class TestTheBootstrapMemberScreensItsOwnKnobsAndDelegate:
    @pytest.mark.parametrize("bad", [0, -1, 1.0, "2000", None, True])
    def test_an_unusable_replicate_count_refuses(self, bad):
        with pytest.raises(ValueError) as err:
            ClusterBootstrapInterval(replicates=bad)
        assert "replicates" in str(err.value)

    @pytest.mark.parametrize("bad", [1.0, "7", None, True])
    def test_a_non_integer_seed_refuses(self, bad):
        with pytest.raises(ValueError) as err:
            ClusterBootstrapInterval(seed=bad)
        assert "seed must be an int" in str(err.value)

    @pytest.mark.parametrize("bad", [7, None, b"x", ["a"]])
    def test_a_non_string_label_refuses(self, bad):
        with pytest.raises(ValueError) as err:
            ClusterBootstrapInterval(label=bad)
        assert "label must be a string" in str(err.value)

    def test_a_delegate_disagreeing_with_the_point_estimate_refuses(self):
        # The sanity check that the one-replicate call and the full stream
        # report the SAME pooled mean. Nothing in normal operation breaks it,
        # which is exactly why it needs a test aimed straight at it.
        class Skewed(ClusterBootstrapInterval):
            def mean_and_se(self, evidence):
                mean, se = super().mean_and_se(evidence)
                return mean + 1.0, se

        with pytest.raises(ValueError) as err:
            Skewed().interval(_cluster_ev())
        assert "disagreed with its own point estimate" in str(err.value)


class TestTheRegistryNamesItsSubject:
    def test_the_colliding_bare_vocabulary_is_gone(self):
        # METHOD-M4: a bare `estimator` already means the OPPOSITE thing in
        # libs/sklearn.py - a dotted path to an ML model. Neither the bare
        # names nor either sibling lane's names may reappear here.
        for gone in (
            "ESTIMATORS",
            "estimator",
            "register_estimator",
            "FALSE_SIGNAL_ESTIMATORS",
            "CALIBRATORS",
        ):
            assert not hasattr(mean_interval_module, gone), gone
            assert gone not in mean_interval_module.__all__, gone

    def test_all_three_names_spell_the_subject_out(self):
        for name in (
            "MEAN_INTERVAL_ESTIMATORS",
            "register_mean_interval_estimator",
            "mean_interval_estimator",
        ):
            assert name in mean_interval_module.__all__
            assert hasattr(mean_interval_module, name)

    def test_every_exported_name_exists_and_nothing_underscored_leaks(self):
        for name in mean_interval_module.__all__:
            assert hasattr(mean_interval_module, name), name
            assert not name.startswith("_"), name


# ---------------------------------------------------------------------------
# The coverage claim, pinned by measurement rather than prose (METHOD-M1)
# ---------------------------------------------------------------------------
#
# A confidence level nobody measured is a promise nobody kept. These are the
# runs behind the module docstring's table: a known true mean of zero, a
# nominal level of 0.95, and a count of how often the returned bounds hold it.
# They are `slow`-marked because they are Monte Carlo, not because they are
# optional - deleting them un-pins every coverage number this module states.


def _rolling_mean(n, h, rng):
    """``n`` rows of an ``h``-step forward mean of iid shocks; true mean 0."""
    raw = [rng.gauss(0.0, 1.0) for _ in range(n + h - 1)]
    return [sum(raw[i:i + h]) / h for i in range(n)]


def _independent_blocks(n_units, per_unit, h, rng):
    """Whole independent units, every label realized INSIDE its own unit.

    This is the contract ``ClusterBootstrapInterval`` is calibrated against:
    each unit draws its own shocks, so no label reaches across a boundary and
    the units really are exchangeable whole.
    """
    values, units = [], []
    for u in range(n_units):
        raw = [rng.gauss(0.0, 1.0) for _ in range(per_unit + h - 1)]
        for i in range(per_unit):
            values.append(sum(raw[i:i + h]) / h)
            units.append(f"u{u:03d}")
    return values, units


def _ar1(n, phi, rng):
    """A stationary AR(1); true mean 0."""
    x = rng.gauss(0.0, 1.0 / math.sqrt(1.0 - phi * phi))
    out = []
    for _ in range(n):
        x = phi * x + rng.gauss(0.0, 1.0)
        out.append(x)
    return out


def _covered(build, trials, seed=4242):
    """Fraction of ``trials`` whose bounds hold the true mean of zero."""
    hits = 0
    for t in range(trials):
        result = build(random.Random(seed + t))
        hits += int(result.low <= 0.0 <= result.high)
    return hits / trials


@pytest.mark.slow
class TestMeasuredCoverage:
    TRIALS = 1000
    REPLICATES = 499

    @pytest.mark.parametrize("n_units", [8, 20, 50])
    def test_the_units_path_delivers_its_nominal_level(self, n_units):
        # THE POSITIVE CONTROL. Given what it was designed for - genuine
        # independence units - the block bootstrap holds its nominal 0.95.
        # Measured 94.6-96.3% at 8/15/25/50 units over 1000-2500 trials; the
        # bound below is that, minus room for this test's smaller run.
        def build(rng):
            values, units = _independent_blocks(n_units, 10, 5, rng)
            est = ClusterBootstrapInterval(replicates=self.REPLICATES)
            return est.interval(MeanEvidence(values, units=units))

        assert _covered(build, self.TRIALS) >= 0.92

    @pytest.mark.parametrize("n_units", [8, 20, 50])
    def test_the_overlap_path_does_not_deliver_its_nominal_level(self, n_units):
        # THE NEGATIVE CONTROL, and the reason NeweyWestInterval returns a
        # WidenedInterval. A CORRECTLY stated overlap on a 10-step rolling
        # mean measured 86.9-91.1%, flat from 8 units to 50 - so this is not
        # a small-sample effect that more data resolves. If this assertion
        # ever fails upward, the module has stopped being honest in the
        # cheap direction and the claim must be re-measured and re-stated.
        overlap = 9

        def build(rng):
            values = _rolling_mean(n_units * (overlap + 1), overlap + 1, rng)
            return NeweyWestInterval().interval(MeanEvidence(values, overlap_steps=overlap))

        assert _covered(build, self.TRIALS) < 0.94

    def test_carving_units_out_of_an_overlapping_series_is_not_calibrated(self):
        # And the same shortfall reaches the BOOTSTRAP member when its units
        # are merely contiguous blocks cut from one overlapping series: the
        # last row of each block still shares shocks with the next block.
        # This configuration passes `independent_units`'s overlap screen,
        # which is why that screen is documented as necessary, not sufficient.
        overlap, n_units = 9, 20

        def build(rng):
            n = n_units * (overlap + 1)
            values = _rolling_mean(n, overlap + 1, rng)
            units = [f"u{i // (overlap + 1):03d}" for i in range(n)]
            est = ClusterBootstrapInterval(replicates=self.REPLICATES)
            return est.interval(MeanEvidence(values, units=units))

        assert _covered(build, self.TRIALS) < 0.94

    def test_the_repos_own_lag_rule_does_not_rescue_the_overlap_path(self):
        # `stats.dm_lags` is the automatic Newey-West bandwidth this repo
        # already ships. On an AR(1) at phi=0.8 it suggests 5 lags and the
        # interval measured ~81% - which is why ADR-0151 refuses to wire it
        # in as a default and why the level is not claimed.
        n, phi = 320, 0.8
        overlap = dm_lags(n, h_steps=1)

        def build(rng):
            values = _ar1(n, phi, rng)
            return NeweyWestInterval().interval(MeanEvidence(values, overlap_steps=overlap))

        assert _covered(build, self.TRIALS) < 0.90
