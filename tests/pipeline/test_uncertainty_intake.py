"""``uncertainty_intake`` (ADR-0165): the gate an uncertainty artifact passes.

Every artifact here is synthetic and hand-built. **Nothing in this file
measures coverage**, and nothing in the module under test does either —
what is exercised is the REFUSAL machinery: an artifact that answers the
wrong question, is calibrated for another model, became knowable after
the decision, is older than the consumer allows, or carries no attested
measurement at all must not reach a decision.
"""

from __future__ import annotations

import random
import types

import pytest

from dskit.pipeline.false_signal import (
    FalseSignalEstimate,
    GrenanderLocalFdr,
    SignalEvidence,
)
from dskit.pipeline.mean_interval import (
    ClusterBootstrapInterval,
    ConfidenceInterval,
    MeanEvidence,
    NeweyWestInterval,
    WidenedInterval,
)
from dskit.pipeline.node import class_ref
from dskit.pipeline.outcome_interval import (
    BlockConformalInterval,
    BlockResiduals,
    OutcomeIntervalResult,
    TwoSidedBlockConformalInterval,
)
from dskit.pipeline.uncertainty_intake import (
    CLOSED_FAMILIES,
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

BAND_PRODUCER = class_ref(BlockConformalInterval)
MEAN_PRODUCER = class_ref(ClusterBootstrapInterval)
RATE_PRODUCER = class_ref(GrenanderLocalFdr)

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
        "producer": BAND_PRODUCER,
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
    """A REAL fitted interval — the estimator records its own class_ref."""
    values = [0.004 + 0.01 * ((i % 7) - 3) for i in range(40)]
    units = [f"u{i // 2}" for i in range(40)]
    return ClusterBootstrapInterval(replicates=200, seed=3).interval(
        MeanEvidence(values=values, units=units)
    )


def _handbuilt_confidence(method=MEAN_PRODUCER):
    """The same SHAPE, built by hand. Nothing fitted it."""
    return ConfidenceInterval(
        mean=0.99,
        standard_error=1e-9,
        low=0.98999,
        high=0.99001,
        level=0.999999,
        independent_units=2,
        method=method,
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


def _rate(names=("alpha",)):
    """A REAL fitted false-signal estimate over a 40-signal synthetic family."""
    rng = random.Random(7)
    evidence = {}
    for index, name in enumerate(names):
        evidence[name] = SignalEvidence(
            statistic=3.0 + 0.5 * index,
            null_draws=[rng.gauss(0.0, 1.0) for _ in range(999)],
        )
    for index in range(20 - len(names)):
        evidence[f"s{index}"] = SignalEvidence(
            statistic=2.5 + rng.random(),
            null_draws=[rng.gauss(0.0, 1.0) for _ in range(999)],
        )
    for index in range(20):
        evidence[f"n{index}"] = SignalEvidence(
            statistic=rng.gauss(0.0, 1.0),
            null_draws=[rng.gauss(0.0, 1.0) for _ in range(999)],
        )
    return GrenanderLocalFdr().estimate(evidence, independent_units=len(evidence))


def _handbuilt_rate(estimator=RATE_PRODUCER):
    """The same SHAPE, built by hand. Nothing fitted it."""
    return FalseSignalEstimate(
        pi_hat={"alpha": 0.10},
        pi_widened={"alpha": 0.20},
        evidence={"estimator": estimator},
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
            {"producer": ""},
            {"producer": None},
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

            @classmethod
            def excluded_types(cls):
                return ()

            @classmethod
            def registered_producers(cls):
                return ()

            @classmethod
            def artifact_producer(cls, artifact):
                return None

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
        env = AttestedMeanConfidence(
            _confidence(), _attestation(producer=MEAN_PRODUCER)
        )
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
            (
                AttestedMeanConfidence(_confidence(), _attestation(producer=MEAN_PRODUCER)),
                AttestedOutcomeBand,
            ),
            (
                AttestedOutcomeBand(_band(), _attestation(producer="nobody:Nothing")),
                AttestedOutcomeBand,
            ),
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
                producer="nobody:Nothing",
                coverage=None,
            ),
        )
        with pytest.raises(ValueError) as err:
            env.admit(_demand(), AttestedOutcomeBand)
        message = str(err.value)
        for reason in (
            "wrong_unit",
            "foreign_model",
            "post_decision",
            "uncalibrated",
            "unknown_producer",
        ):
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
        env = AttestedFalseSignalRate(_rate(), _attestation(producer=RATE_PRODUCER))
        problems = env.problems(_demand(), ProbabilityUpperBound)
        assert [p.split(":")[0] for p in problems] == ["wrong_unit"]

    def test_the_widened_number_is_still_readable_as_itself(self):
        # The retreat is about the CLAIM, not about hiding the number: a
        # caller may read pi_widened as a sensitivity reading.
        env = AttestedFalseSignalRate(
            _handbuilt_rate(), _attestation(producer=RATE_PRODUCER)
        )
        assert env.artifact.pi_widened["alpha"] == 0.20
        assert env.artifact.pi_hat["alpha"] == 0.10

    def test_no_member_of_the_whole_package_satisfies_the_bound_demand(self):
        for env, producer in (
            (AttestedFalseSignalRate, RATE_PRODUCER),
            (AttestedMeanConfidence, MEAN_PRODUCER),
            (AttestedOutcomeBand, BAND_PRODUCER),
        ):
            artifact = {
                AttestedFalseSignalRate: _rate,
                AttestedMeanConfidence: _confidence,
                AttestedOutcomeBand: _band,
            }[env]()
            wrapped = env(artifact, _attestation(producer=producer))
            assert wrapped.problems(_demand(), ProbabilityUpperBound)

    def test_the_closed_family_cannot_be_joined_from_anywhere(self):
        # Round-1 review: four lines re-created the defect. A subclass, a
        # sideways multiple-inheritance route and a grandchild are all
        # refused at CLASS-DEFINITION time, before any instance exists.
        with pytest.raises(TypeError, match="CLOSED"):
            type(
                "MyBoundFromWidened",
                (ProbabilityUpperBound,),
                {
                    "artifact_type": classmethod(lambda cls: FalseSignalEstimate),
                    "estimand": classmethod(lambda cls: "sneaky_bound"),
                    "excluded_types": classmethod(lambda cls: ()),
                    "registered_producers": classmethod(lambda cls: ()),
                    "artifact_producer": classmethod(lambda cls, a: None),
                },
            )
        with pytest.raises(TypeError, match="CLOSED"):
            type("Sideways", (AttestedFalseSignalRate, ProbabilityUpperBound), {})

    def test_the_family_is_listed_as_closed(self):
        assert ProbabilityUpperBound in CLOSED_FAMILIES

    def test_no_registered_member_is_in_the_bound_family(self):
        assert [
            cls
            for cls in UNCERTAINTY_INTAKES.values()
            if issubclass(cls, ProbabilityUpperBound)
        ] == []


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

    def test_a_second_member_for_one_artifact_type_is_refused(self):
        # Round-1 review: the ambiguity screen EXEMPTED a member declaring
        # an artifact type another already claimed — exactly the attack's
        # shape. Two members can no longer share one artifact type at all.
        class _Twin(AttestedUncertainty):
            @classmethod
            def artifact_type(cls):
                return OutcomeIntervalResult

            @classmethod
            def estimand(cls):
                return "twin"

            @classmethod
            def excluded_types(cls):
                return ()

            @classmethod
            def registered_producers(cls):
                return ()

            @classmethod
            def artifact_producer(cls, artifact):
                return None

        with pytest.raises(ValueError, match="already claims"):
            register_uncertainty_intake("tests_twin", _Twin)
        assert "tests_twin" not in UNCERTAINTY_INTAKES


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


class TestTheDoorwayIsSealedNotJustItsTemplate:
    """Round-1 review: only `problems`/`admit` were sealed. Everything is now."""

    @pytest.mark.parametrize("name", AttestedUncertainty._FINAL_METHODS)
    def test_every_sealed_name_is_refused_at_class_definition(self, name):
        with pytest.raises(TypeError, match=name.lstrip("_")):
            type("Sneaky", (AttestedMeanConfidence,), {name: lambda *a, **k: []})

    def test_overriding_a_screen_can_no_longer_erase_a_refusal(self):
        # The concrete consequence: this member returned [] for a stale,
        # post-decision, uncalibrated artifact on candidate 7098571.
        with pytest.raises(TypeError, match="coverage_problems"):
            type(
                "NoScreens",
                (AttestedMeanConfidence,),
                {
                    "_coverage_problems": lambda self, demand: [],
                    "_timing_problems": lambda self, demand: [],
                },
            )

    def test_a_member_cannot_shrink_the_sealed_list(self):
        # The check reads AttestedUncertainty._FINAL_METHODS, never cls's.
        with pytest.raises(TypeError, match="problems"):
            type(
                "Shrinker",
                (AttestedMeanConfidence,),
                {"_FINAL_METHODS": (), "problems": lambda self, d, e: []},
            )

    def test_the_two_tuples_cover_every_callable_the_doorway_defines(self):
        # Anti-drift: a new method added to AttestedUncertainty must be
        # classified as sealed or as a hook, or this fails.
        classified = set(AttestedUncertainty._FINAL_METHODS) | set(
            AttestedUncertainty._HOOKS
        )
        defined = {
            name
            for name, value in vars(AttestedUncertainty).items()
            if isinstance(
                value, (types.FunctionType, classmethod, staticmethod, property)
            )
        }
        assert defined == classified, defined.symmetric_difference(classified)

    def test_every_hook_is_abstract(self):
        assert set(AttestedUncertainty._HOOKS) <= set(
            AttestedUncertainty.__abstractmethods__
        )


class TestProducerVerification:
    """What the producer screen narrows, and exactly what it does not prove."""

    def test_an_unregistered_producer_is_refused(self):
        env = AttestedMeanConfidence(
            _handbuilt_confidence(method="attacker.module:TotallyFakeEstimator"),
            _attestation(producer="attacker.module:TotallyFakeEstimator"),
        )
        problems = env.problems(_demand(), AttestedMeanConfidence)
        assert [p.split(":")[0] for p in problems] == ["unknown_producer"]
        assert "never that it ran" in problems[0]

    def test_an_attestation_that_disagrees_with_the_artifact_is_refused(self):
        env = AttestedMeanConfidence(
            _handbuilt_confidence(method="attacker.module:TotallyFakeEstimator"),
            _attestation(producer=MEAN_PRODUCER),
        )
        problems = env.problems(_demand(), AttestedMeanConfidence)
        assert any("must agree on what made it" in p for p in problems)

    def test_a_producer_registered_for_ANOTHER_estimand_is_refused(self):
        # A calibrator is a registered class, but not a registered
        # mean-interval estimator. Registry membership is per estimand.
        env = AttestedMeanConfidence(
            _handbuilt_confidence(method=BAND_PRODUCER),
            _attestation(producer=BAND_PRODUCER),
        )
        assert any(
            p.startswith("unknown_producer")
            for p in env.problems(_demand(), AttestedMeanConfidence)
        )

    @pytest.mark.parametrize(
        "artifact",
        [
            lambda: ConfidenceInterval(
                mean=0.1, standard_error=0.01, low=0.05, high=0.15,
                level=0.95, independent_units=8, method="x:Y",
            ),
        ],
    )
    def test_an_artifact_whose_self_report_is_unknown_is_refused(self, artifact):
        env = AttestedMeanConfidence(artifact(), _attestation(producer=MEAN_PRODUCER))
        assert any(
            p.startswith("unknown_producer")
            for p in env.problems(_demand(), AttestedMeanConfidence)
        )

    def test_each_member_reads_its_own_artifacts_self_report(self):
        assert AttestedMeanConfidence.artifact_producer(_confidence()) == MEAN_PRODUCER
        assert AttestedOutcomeBand.artifact_producer(_band()) == BAND_PRODUCER
        assert AttestedFalseSignalRate.artifact_producer(_rate()) == RATE_PRODUCER

    def test_every_shipped_producer_of_every_estimand_is_accepted(self):
        for member, cls in (
            (AttestedMeanConfidence, NeweyWestInterval),
            (AttestedOutcomeBand, TwoSidedBlockConformalInterval),
            (AttestedFalseSignalRate, GrenanderLocalFdr),
        ):
            assert class_ref(cls) in {
                class_ref(known) for known in member.registered_producers()
            }

    def test_a_real_fitted_artifact_passes_every_screen(self):
        for member, artifact, producer in (
            (AttestedMeanConfidence, _confidence(), MEAN_PRODUCER),
            (AttestedOutcomeBand, _band(), BAND_PRODUCER),
            (AttestedFalseSignalRate, _rate(), RATE_PRODUCER),
        ):
            env = member(artifact, _attestation(producer=producer))
            assert env.problems(_demand(), member) == []

    def test_what_this_does_NOT_establish_is_pinned(self):
        # DISCLOSED LIMIT, pinned so the claim cannot quietly grow: an
        # artifact built entirely by hand, that merely NAMES a registered
        # producer, is admitted. The screen narrows "any object of the
        # right shape" to "an object naming a producer this package
        # knows"; it is not provenance and cannot become it inside Python
        # (ADR-0122's Correction). If this ever starts refusing, the
        # module docstring's disclosure must be rewritten with it.
        env = AttestedMeanConfidence(
            _handbuilt_confidence(), _attestation(producer=MEAN_PRODUCER)
        )
        assert env.problems(_demand(), AttestedMeanConfidence) == []


class TestExcludedTypes:
    def test_a_diamond_carrying_both_claims_is_refused_at_construction(self):
        class Both(ConfidenceInterval, WidenedInterval):
            pass

        both = Both(
            mean=0.5, standard_error=0.1, low=0.3, high=0.7, level=0.95,
            independent_units=8, method=MEAN_PRODUCER,
        )
        assert isinstance(both, WidenedInterval)
        with pytest.raises(ValueError, match="excludes"):
            AttestedMeanConfidence(both, _attestation(producer=MEAN_PRODUCER))

    def test_the_mean_member_excludes_the_widened_sibling(self):
        assert AttestedMeanConfidence.excluded_types() == (WidenedInterval,)

    def test_every_member_states_its_exclusions(self):
        for cls in UNCERTAINTY_INTAKES.values():
            assert isinstance(cls.excluded_types(), tuple)
