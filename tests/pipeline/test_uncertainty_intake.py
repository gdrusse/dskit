"""``uncertainty_intake`` (ADR-0165): the gate an uncertainty artifact passes.

Every artifact here is synthetic and hand-built. **Nothing in this file
measures coverage**, and nothing in the module under test does either —
what is exercised is the REFUSAL machinery: an artifact that answers the
wrong question, is calibrated for another model, became knowable after
the decision, is older than the consumer allows, or carries no attested
measurement at all must not reach a decision.
"""

from __future__ import annotations

import pytest

from dskit.pipeline.false_signal import FalseSignalEstimate
from dskit.pipeline.mean_interval import ConfidenceInterval, WidenedInterval
from dskit.pipeline.outcome_interval import (
    BlockConformalInterval,
    BlockResiduals,
    OutcomeIntervalResult,
)
from dskit.pipeline.uncertainty_intake import (
    REFUSAL_REASONS,
    UNCERTAINTY_INTAKES,
    AttestedFalseSignalRate,
    AttestedMeanConfidence,
    AttestedOutcomeBand,
    AttestedUncertainty,
    CoverageEvidence,
    DecisionDemand,
    ProbabilityUpperBound,
    UncertaintyAttestation,
    register_uncertainty_intake,
    uncertainty_intake,
)

DECISION_TS = 1_700_000_000_000
MODEL = "release-1"


def _coverage(**overrides):
    base = {
        "target": 0.95,
        "measured": 0.94,
        "evidence_id": "probe-1",
        "n_units": 40,
    }
    base.update(overrides)
    return CoverageEvidence(**base)


def _attestation(**overrides):
    base = {
        "artifact_id": "artifact-1",
        "model_identity": MODEL,
        "calibration_end_ms": DECISION_TS - 60_000,
        "known_at_ms": DECISION_TS - 30_000,
        "coverage": _coverage(),
    }
    base.update(overrides)
    return UncertaintyAttestation(**base)


def _demand(**overrides):
    base = {
        "decision_ts_ms": DECISION_TS,
        "model_identity": MODEL,
        "max_calibration_age_ms": 600_000,
        "min_measured_coverage": 0.90,
    }
    base.update(overrides)
    return DecisionDemand(**base)


def _confidence():
    return ConfidenceInterval(
        mean=0.004,
        standard_error=0.001,
        low=0.002,
        high=0.006,
        level=0.95,
        independent_units=40,
        method="tests.synthetic",
    )


def _widened():
    return WidenedInterval(
        mean=0.004,
        standard_error=0.001,
        low=0.002,
        high=0.006,
        level=0.95,
        independent_units=40,
        method="tests.synthetic",
    )


def _band():
    residuals = BlockResiduals(
        names=("alpha", "beta"),
        rows=[
            (0.01 * (r + 1), -0.01 * (r + 1)) for r in range(8)
        ],
        blocks=["s1", "s1", "s2", "s2", "s3", "s3", "s4", "s4"],
    )
    return BlockConformalInterval().calibrate(residuals, coverage=0.6, window_blocks=2)


def _rate():
    return FalseSignalEstimate(
        pi_hat={"alpha": 0.10},
        pi_widened={"alpha": 0.20},
        evidence={"estimator": "tests.synthetic"},
    )


class TestCoverageEvidenceRecordsRatherThanMeasures:
    @pytest.mark.parametrize(
        "overrides",
        [
            {"target": 0.0},
            {"target": 1.0},
            {"target": float("nan")},
            {"measured": -0.1},
            {"measured": 1.1},
            {"measured": float("inf")},
            {"evidence_id": ""},
            {"evidence_id": None},
            {"n_units": 0},
            {"n_units": True},
            {"n_units": 1.5},
        ],
    )
    def test_an_unusable_record_refuses(self, overrides):
        with pytest.raises(ValueError):
            _coverage(**overrides)

    def test_a_measurement_below_its_own_target_is_a_legal_record(self):
        # An artifact that FELL SHORT is a real artifact. Refusing it is the
        # consumer's floor's job, not this value's.
        assert _coverage(measured=0.42).measured == 0.42


class TestTheAttestationScreensItsOwnShape:
    @pytest.mark.parametrize(
        "overrides",
        [
            {"artifact_id": ""},
            {"model_identity": ""},
            {"calibration_end_ms": -1},
            {"calibration_end_ms": 1.5},
            {"known_at_ms": True},
            {"coverage": 0.94},
            {"coverage": {"measured": 0.94}},
        ],
    )
    def test_unusable_provenance_refuses(self, overrides):
        with pytest.raises(ValueError):
            _attestation(**overrides)

    def test_evidence_cannot_cover_time_the_artifact_predates(self):
        with pytest.raises(ValueError, match="predates"):
            _attestation(
                calibration_end_ms=DECISION_TS, known_at_ms=DECISION_TS - 1
            )

    def test_no_coverage_is_an_honest_record_not_a_missing_field(self):
        assert _attestation(coverage=None).coverage is None


class TestTheDemandHasNoDefaults:
    @pytest.mark.parametrize(
        "missing",
        [
            "decision_ts_ms",
            "model_identity",
            "max_calibration_age_ms",
            "min_measured_coverage",
        ],
    )
    def test_every_policy_field_is_required(self, missing):
        kwargs = {
            "decision_ts_ms": DECISION_TS,
            "model_identity": MODEL,
            "max_calibration_age_ms": 0,
            "min_measured_coverage": 0.9,
        }
        kwargs.pop(missing)
        with pytest.raises(TypeError):
            DecisionDemand(**kwargs)

    @pytest.mark.parametrize(
        "overrides",
        [
            {"min_measured_coverage": 0.0},
            {"min_measured_coverage": 1.0},
            {"max_calibration_age_ms": -1},
            {"model_identity": ""},
            {"decision_ts_ms": -5},
        ],
    )
    def test_an_unusable_policy_refuses(self, overrides):
        with pytest.raises(ValueError):
            _demand(**overrides)


class TestTheEstimandIsTheArtifactsType:
    def test_each_member_accepts_only_its_own_artifact(self):
        assert AttestedMeanConfidence(_confidence(), _attestation())
        assert AttestedOutcomeBand(_band(), _attestation())
        assert AttestedFalseSignalRate(_rate(), _attestation())

    @pytest.mark.parametrize(
        "member,artifact",
        [
            (AttestedMeanConfidence, _band),
            (AttestedMeanConfidence, _rate),
            (AttestedOutcomeBand, _confidence),
            (AttestedOutcomeBand, _rate),
            (AttestedFalseSignalRate, _confidence),
            (AttestedFalseSignalRate, _band),
        ],
    )
    def test_the_wrong_artifact_cannot_be_wrapped(self, member, artifact):
        with pytest.raises(ValueError, match="accepts only"):
            member(artifact(), _attestation())

    def test_a_widened_interval_is_not_a_measured_coverage_interval(self):
        # The claim is a TYPE one module over, and it stays a type here:
        # WidenedInterval is a sibling of ConfidenceInterval, not a subclass.
        assert not isinstance(_widened(), ConfidenceInterval)
        with pytest.raises(ValueError, match="accepts only"):
            AttestedMeanConfidence(_widened(), _attestation())

    def test_a_non_attestation_is_refused(self):
        with pytest.raises(ValueError, match="UncertaintyAttestation"):
            AttestedOutcomeBand(_band(), {"artifact_id": "artifact-1"})

    def test_an_artifact_that_answers_two_questions_is_refused(self):
        class _OtherSubject:
            pass

        class _Ambiguous(OutcomeIntervalResult, _OtherSubject):
            pass

        class _OtherIntake(AttestedUncertainty):
            @classmethod
            def artifact_type(cls):
                return _OtherSubject

            @classmethod
            def estimand(cls):
                return "some_other_question"

        register_uncertainty_intake("tests_other_subject", _OtherIntake)
        try:
            band = _band()
            ambiguous = _Ambiguous(
                coverage_target=band.coverage_target,
                achieved_level=band.achieved_level,
                lower_offset=dict(band.lower_offset),
                upper_offset=dict(band.upper_offset),
                realized_coverage=dict(band.realized_coverage),
                conditional_coverage=dict(band.conditional_coverage),
                tail_loss=dict(band.tail_loss),
                n_blocks=band.n_blocks,
                n_rows=band.n_rows,
                calibration_hash=band.calibration_hash,
            )
            with pytest.raises(ValueError, match="two questions at once"):
                AttestedOutcomeBand(ambiguous, _attestation())
        finally:
            UNCERTAINTY_INTAKES.pop("tests_other_subject", None)


class TestTheTemplateIsFinalAndTheHooksAreAbstract:
    @pytest.mark.parametrize("name", ["problems", "admit"])
    def test_a_member_that_overrides_the_template_refuses_at_definition(self, name):
        with pytest.raises(TypeError, match=name):
            type(
                "_Sneaky",
                (AttestedUncertainty,),
                {name: lambda self, demand, expected: []},
            )

    def test_an_incomplete_member_refuses_at_construction(self):
        class _Partial(AttestedUncertainty):
            @classmethod
            def artifact_type(cls):
                return ConfidenceInterval

        with pytest.raises(TypeError):
            _Partial(_confidence(), _attestation())


class TestTheFiveRefusals:
    def test_a_fully_attested_artifact_is_admitted(self):
        env = AttestedOutcomeBand(_band(), _attestation())
        assert env.problems(_demand(), AttestedOutcomeBand) == []
        assert env.admit(_demand(), AttestedOutcomeBand) is env

    def test_wrong_unit(self):
        env = AttestedMeanConfidence(_confidence(), _attestation())
        problems = env.problems(_demand(), AttestedOutcomeBand)
        assert [p.split(":")[0] for p in problems] == ["wrong_unit"]
        assert "different questions" in problems[0]

    def test_foreign_model(self):
        env = AttestedOutcomeBand(_band(), _attestation(model_identity="release-2"))
        problems = env.problems(_demand(), AttestedOutcomeBand)
        assert [p.split(":")[0] for p in problems] == ["foreign_model"]

    def test_post_decision_by_publication(self):
        env = AttestedOutcomeBand(_band(), _attestation(known_at_ms=DECISION_TS + 1))
        problems = env.problems(_demand(), AttestedOutcomeBand)
        assert any(p.startswith("post_decision") and "known_at_ms" in p for p in problems)

    def test_post_decision_by_calibration_window(self):
        env = AttestedOutcomeBand(
            _band(),
            _attestation(
                calibration_end_ms=DECISION_TS + 1, known_at_ms=DECISION_TS + 2
            ),
        )
        problems = env.problems(_demand(), AttestedOutcomeBand)
        assert any(
            p.startswith("post_decision") and "calibration_end_ms" in p
            for p in problems
        )
        assert not any(p.startswith("stale") for p in problems)

    def test_stale(self):
        env = AttestedOutcomeBand(
            _band(), _attestation(calibration_end_ms=DECISION_TS - 600_001)
        )
        problems = env.problems(_demand(), AttestedOutcomeBand)
        assert [p.split(":")[0] for p in problems] == ["stale"]

    def test_the_freshest_stale_case_is_one_millisecond_wide(self):
        env = AttestedOutcomeBand(
            _band(), _attestation(calibration_end_ms=DECISION_TS - 600_000)
        )
        assert env.problems(_demand(), AttestedOutcomeBand) == []

    def test_uncalibrated_by_absent_evidence(self):
        env = AttestedOutcomeBand(_band(), _attestation(coverage=None))
        problems = env.problems(_demand(), AttestedOutcomeBand)
        assert [p.split(":")[0] for p in problems] == ["uncalibrated"]
        assert "schema compliance" in problems[0]

    def test_uncalibrated_by_a_measurement_below_the_declared_floor(self):
        env = AttestedOutcomeBand(
            _band(), _attestation(coverage=_coverage(measured=0.5))
        )
        problems = env.problems(_demand(), AttestedOutcomeBand)
        assert [p.split(":")[0] for p in problems] == ["uncalibrated"]

    def test_every_declared_reason_is_actually_emitted_by_a_screen(self):
        seen = set()
        cases = [
            (AttestedMeanConfidence(_confidence(), _attestation()), AttestedOutcomeBand),
            (AttestedOutcomeBand(_band(), _attestation(model_identity="x")), AttestedOutcomeBand),
            (AttestedOutcomeBand(_band(), _attestation(known_at_ms=DECISION_TS + 1)), AttestedOutcomeBand),
            (AttestedOutcomeBand(_band(), _attestation(calibration_end_ms=DECISION_TS - 10**9)), AttestedOutcomeBand),
            (AttestedOutcomeBand(_band(), _attestation(coverage=None)), AttestedOutcomeBand),
        ]
        for env, expected in cases:
            for problem in env.problems(_demand(), expected):
                seen.add(problem.split(":")[0])
        assert seen == set(REFUSAL_REASONS)

    def test_admit_raises_with_every_reason_joined(self):
        env = AttestedMeanConfidence(
            _confidence(),
            _attestation(
                model_identity="release-2",
                known_at_ms=DECISION_TS + 1,
                calibration_end_ms=DECISION_TS - 10**9,
                coverage=None,
            ),
        )
        with pytest.raises(ValueError) as err:
            env.admit(_demand(), AttestedOutcomeBand)
        message = str(err.value)
        for reason in ("wrong_unit", "foreign_model", "post_decision", "uncalibrated"):
            assert reason in message

    @pytest.mark.parametrize(
        "demand,expected",
        [
            ({"decision_ts_ms": DECISION_TS}, AttestedOutcomeBand),
            (None, AttestedOutcomeBand),
        ],
    )
    def test_a_caller_error_is_refused_rather_than_answered(self, demand, expected):
        env = AttestedOutcomeBand(_band(), _attestation())
        with pytest.raises(ValueError, match="DecisionDemand"):
            env.problems(demand, expected)

    @pytest.mark.parametrize("expected", [object, "AttestedOutcomeBand", None])
    def test_expected_must_be_an_intake_class(self, expected):
        env = AttestedOutcomeBand(_band(), _attestation())
        with pytest.raises(ValueError, match="AttestedUncertainty subclass"):
            env.problems(_demand(), expected)


class TestTheWidenedRateCanNeverBecomeABound:
    def test_dskit_ships_no_member_of_the_bound_family(self):
        members = [
            cls
            for cls in UNCERTAINTY_INTAKES.values()
            if issubclass(cls, ProbabilityUpperBound)
        ]
        assert members == []

    def test_the_false_signal_rate_is_refused_when_a_bound_is_demanded(self):
        env = AttestedFalseSignalRate(_rate(), _attestation())
        problems = env.problems(_demand(), ProbabilityUpperBound)
        assert [p.split(":")[0] for p in problems] == ["wrong_unit"]

    def test_the_widened_number_is_still_readable_as_itself(self):
        # The retreat is about the CLAIM, not about hiding the number: a
        # caller may read pi_widened as a sensitivity reading.
        env = AttestedFalseSignalRate(_rate(), _attestation())
        assert env.artifact.pi_widened["alpha"] == 0.20
        assert env.artifact.pi_hat["alpha"] == 0.10

    def test_no_member_of_the_whole_package_satisfies_the_bound_demand(self):
        for env in (
            AttestedFalseSignalRate(_rate(), _attestation()),
            AttestedMeanConfidence(_confidence(), _attestation()),
            AttestedOutcomeBand(_band(), _attestation()),
        ):
            assert env.problems(_demand(), ProbabilityUpperBound)


class TestTheRegistry:
    def test_the_three_shipped_members_are_registered(self):
        assert uncertainty_intake("mean_confidence") is AttestedMeanConfidence
        assert uncertainty_intake("outcome_band") is AttestedOutcomeBand
        assert uncertainty_intake("false_signal_rate") is AttestedFalseSignalRate

    def test_an_unknown_name_names_what_is_registered(self):
        with pytest.raises(KeyError, match="outcome_band"):
            uncertainty_intake("nope")

    def test_a_name_cannot_be_rebound_to_a_different_class(self):
        with pytest.raises(ValueError, match="already registered"):
            register_uncertainty_intake("outcome_band", AttestedMeanConfidence)

    def test_re_registering_the_same_class_is_idempotent(self):
        register_uncertainty_intake("outcome_band", AttestedOutcomeBand)
        assert uncertainty_intake("outcome_band") is AttestedOutcomeBand

    @pytest.mark.parametrize(
        "name,cls",
        [("", AttestedOutcomeBand), ("x", object), ("x", "AttestedOutcomeBand")],
    )
    def test_an_unusable_registration_refuses(self, name, cls):
        with pytest.raises(ValueError):
            register_uncertainty_intake(name, cls)


class TestTheModuleMeasuresNothing:
    def test_the_attested_number_is_the_producers_and_is_not_recomputed(self):
        # The band's own realized_coverage is IN-SAMPLE by its contract and
        # is a different quantity from the attested out-of-sample number.
        # Nothing here reads one from the other.
        band = _band()
        attestation = _attestation(coverage=_coverage(measured=0.9101))
        env = AttestedOutcomeBand(band, attestation)
        assert env.attestation.coverage.measured == 0.9101
        assert env.problems(_demand(), AttestedOutcomeBand) == []
        assert set(band.realized_coverage) == {"alpha", "beta"}
