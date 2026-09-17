"""Contract for the mean-effect interval family under temporal dependence."""

from __future__ import annotations

import pytest

from dskit.pipeline.mean_interval import (
    DEFAULT_CONFIDENCE,
    ESTIMATORS,
    MIN_INDEPENDENT_UNITS,
    ClusterBootstrapInterval,
    MeanEvidence,
    MeanIntervalEstimator,
    MeanIntervalResult,
    NeweyWestInterval,
    estimator,
    register_estimator,
)
from dskit.pipeline.stats import cluster_bootstrap_t, newey_west_mean


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


class TestMeanIntervalResultCannotHoldAContradiction:
    def _kwargs(self, **over):
        base = dict(
            mean=1.0,
            standard_error=0.5,
            low=0.0,
            high=2.0,
            confidence=0.95,
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

    def test_a_negative_standard_error_refuses(self):
        with pytest.raises(ValueError):
            MeanIntervalResult(**self._kwargs(standard_error=-0.1))

    @pytest.mark.parametrize("bad", [0.0, 1.0, 1.5, -0.2])
    def test_a_confidence_outside_the_open_unit_refuses(self, bad):
        with pytest.raises(ValueError):
            MeanIntervalResult(**self._kwargs(confidence=bad))

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
        for entry in ESTIMATORS.values():
            assert entry["cls"].interval is MeanIntervalEstimator.interval


class TestEachMemberRefusesTheSpellingItCannotRead:
    def test_the_cluster_member_refuses_evidence_with_no_units(self):
        ev = MeanEvidence(_overlapping_mean(200, 4), overlap_steps=3)
        with pytest.raises(ValueError) as err:
            ClusterBootstrapInterval().interval(ev)
        assert "units" in str(err.value)

    def test_the_hac_member_refuses_evidence_with_no_overlap(self):
        values, units = _unit_values()
        ev = MeanEvidence(values, units=units)
        with pytest.raises(ValueError) as err:
            NeweyWestInterval().interval(ev)
        assert "overlap_steps" in str(err.value)


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
        values, units = _unit_values(n_units=12, per_unit=4)
        ev = MeanEvidence(values, units=units, overlap_steps=4)
        with pytest.raises(ValueError) as err:
            ClusterBootstrapInterval().interval(ev)
        assert "overlap" in str(err.value).lower()

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
        assert out.confidence == DEFAULT_CONFIDENCE
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

    def test_the_bootstrap_honours_the_stated_confidence(self):
        # Strict, and over three levels: an implementation that ignored the
        # argument and always asked its delegate for one fixed alpha would
        # return three identical widths.
        ev = self._cluster_evidence()
        widths = [
            ClusterBootstrapInterval().interval(ev, confidence=c).width
            for c in (0.80, 0.95, 0.99)
        ]
        assert widths[0] < widths[1] < widths[2]

    def test_a_higher_confidence_is_never_narrower(self):
        ev = self._cluster_evidence()
        narrow = ClusterBootstrapInterval().interval(ev, confidence=0.80)
        wide = ClusterBootstrapInterval().interval(ev, confidence=0.99)
        assert wide.width >= narrow.width

        hac_ev = MeanEvidence(_overlapping_mean(400, 6), overlap_steps=5)
        assert (
            NeweyWestInterval().interval(hac_ev, confidence=0.99).width
            > NeweyWestInterval().interval(hac_ev, confidence=0.80).width
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
    def test_an_unusable_confidence_refuses(self, bad):
        ev = self._cluster_evidence()
        with pytest.raises(ValueError):
            ClusterBootstrapInterval().interval(ev, confidence=bad)

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
        assert estimator("cluster-bootstrap-t")["cls"] is ClusterBootstrapInterval
        assert estimator("newey-west")["cls"] is NeweyWestInterval

    def test_an_unknown_name_lists_what_is_known(self):
        with pytest.raises(ValueError) as err:
            estimator("nope")
        assert "cluster-bootstrap-t" in str(err.value)

    def test_a_duplicate_name_refuses(self):
        with pytest.raises(ValueError):
            register_estimator("cluster-bootstrap-t", ClusterBootstrapInterval)

    def test_a_non_member_class_refuses(self):
        with pytest.raises(ValueError):
            register_estimator("bad", dict)

    def test_an_empty_name_refuses(self):
        with pytest.raises(ValueError):
            register_estimator("", ClusterBootstrapInterval)
