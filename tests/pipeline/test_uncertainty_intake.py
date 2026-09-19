"""``uncertainty_intake`` (ADR-0165): the gate an uncertainty artifact passes.

Every artifact here is synthetic and hand-built. **Nothing in this file
measures coverage**, and nothing in the module under test does either —
what is exercised is the REFUSAL machinery: an artifact that answers the
wrong question, is calibrated for another model, became knowable after
the decision, is older than the consumer allows, or carries no attested
measurement at all must not reach a decision.
"""

from __future__ import annotations

import _abc
import contextlib
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
    MeanIntervalResult,
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
    _INTAKES,
    admission_problems,
    admit_uncertainty,
    artifact_of,
    attestation_of,
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


def _widened(method="tests.synthetic"):
    """The claim-free sibling of ConfidenceInterval.

    ``method`` defaults to a producer NO registry knows, which is what a
    hand-built artifact honestly looks like. Round-3 review found a second
    definition of this helper further down the file, shadowing this one
    with ``method=MEAN_PRODUCER``; the swap test below passed only because
    that accidental equality silenced the producer screen. The duplicate
    is gone and the choice is now explicit at each call site.
    """
    return WidenedInterval(
        mean=0.004,
        standard_error=0.001,
        low=0.002,
        high=0.006,
        level=0.95,
        independent_units=40,
        method=method,
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
            _INTAKES.pop("tests_other_subject", None)  # read-only in public


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
        # It short-circuits: an envelope that does not answer the demanded
        # question has no attestation worth screening, and reading its own
        # hooks to screen one would be trusting the class under suspicion.
        assert len(problems) == 1

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
        env = AttestedOutcomeBand(
            _band(),
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
        # `__mro__`, not issubclass: a virtual registration would make
        # issubclass say yes and this pin would then be asserting the
        # forgery rather than the fact.
        members = [
            cls
            for cls in UNCERTAINTY_INTAKES.values()
            if ProbabilityUpperBound in cls.__mro__
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
            if ProbabilityUpperBound in cls.__mro__
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

    def test_the_screens_are_not_methods_so_redefining_them_is_inert(self):
        """The round-2 `NoScreens` attack, now dead by construction.

        On candidate `7098571` a member overriding `_coverage_problems` and
        `_timing_problems` returned `[]` for a stale, post-decision,
        uncalibrated artifact. Round 3 moved the five screens OFF the class
        into module-level functions taking explicit values, so those names
        are inert attributes on a subclass rather than a bypass — the
        checkpoint's "stop trying to seal every hook" in executable form.
        """

        class NoScreens(AttestedMeanConfidence):
            def _coverage_problems(self, demand):
                return []

            def _timing_problems(self, demand):
                return []

        for name in ("_coverage_problems", "_timing_problems"):
            assert not hasattr(AttestedUncertainty, name)
        hopeless = _attestation(
            model_identity="release-2",
            known_at_ms=DECISION_TS + 1,
            calibration_end_ms=0,
            producer="nobody:Nothing",
            coverage=None,
        )
        # It cannot even be constructed — the ambiguity screen refuses an
        # unregistered class wrapping a registered member's artifact type —
        # and if it could, the function would refuse it anyway.
        with pytest.raises(ValueError, match="two questions at once"):
            NoScreens(_confidence(), hopeless)

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


class TestUseTimeVerification:
    """Round-3 checkpoint: the rule reads the REGISTRY, not the class's claims.

    Four bypasses on candidate `189125b` shared one root — the envelope
    trusted an identity established at ``__init__`` and never re-checked —
    and two of them could not be sealed away, because ``artifact_type`` and
    ``excluded_types`` are hooks and must stay overridable. Each attack
    below is the reviewer's own reproducer.
    """

    def test_a_subclass_widening_its_hooks_is_refused(self):
        # CRITICAL 1. Loosening `artifact_type` to the shared base and
        # emptying `excluded_types` admitted a WidenedInterval as measured
        # mean confidence. The envelope's own declaration is no longer read.
        class SneakyConfidence(AttestedMeanConfidence):
            @classmethod
            def artifact_type(cls):
                return MeanIntervalResult

            @classmethod
            def excluded_types(cls):
                return ()

        env = SneakyConfidence(
            _widened(method=MEAN_PRODUCER), _attestation(producer=MEAN_PRODUCER)
        )
        problems = admission_problems(env, _demand(), AttestedMeanConfidence)
        # Content, not count: a de-flaking edit to a length assertion must
        # not be able to remove this coverage.
        assert [p.split(":")[0] for p in problems] == ["wrong_unit"]
        assert "SneakyConfidence" in problems[0]
        assert "not a registered intake" in problems[0]
        assert "AttestedMeanConfidence" in problems[0]
        with pytest.raises(ValueError, match="not a registered intake"):
            admit_uncertainty(env, _demand(), AttestedMeanConfidence)

    def test_a_virtual_registration_cannot_forge_family_membership(self):
        # CRITICAL 2. `ABCMeta.register()` flips isinstance without creating
        # a class, so `__init_subclass__` never runs. The rule asks the
        # registry and tests real inheritance (`expected in cls.__mro__`),
        # neither of which `register()` can touch.
        ProbabilityUpperBound.register(AttestedFalseSignalRate)
        try:
            env = AttestedFalseSignalRate(_rate(), _attestation(producer=RATE_PRODUCER))
            assert isinstance(env, ProbabilityUpperBound)  # the forgery works
            assert ProbabilityUpperBound not in AttestedFalseSignalRate.__mro__
            problems = admission_problems(env, _demand(), ProbabilityUpperBound)
            assert [p.split(":")[0] for p in problems] == ["wrong_unit"]
            assert "not a registered intake" in problems[0]
        finally:
            _abc._reset_registry(ProbabilityUpperBound)
            _abc._reset_caches(ProbabilityUpperBound)
        assert not isinstance(
            AttestedFalseSignalRate(_rate(), _attestation(producer=RATE_PRODUCER)),
            ProbabilityUpperBound,
        )

    def test_the_closed_family_answer_comes_from_the_registry(self):
        assert ProbabilityUpperBound not in _registered()
        problems = admission_problems(
            AttestedOutcomeBand(_band(), _attestation()),
            _demand(),
            ProbabilityUpperBound,
        )
        assert problems and "not a registered intake" in problems[0]

    @pytest.mark.parametrize(
        "method,also_expected",
        [
            # The swapped artifact happens to name a registered producer:
            # only the shape screens fire.
            (MEAN_PRODUCER, set()),
            # The honest case — a hand-built artifact naming nothing the
            # registry knows. `unknown_producer` fires too, and the earlier
            # `all(startswith("wrong_unit"))` assertion held only because a
            # shadowing duplicate fixture made this case unreachable.
            ("tests.synthetic", {"unknown_producer"}),
        ],
    )
    def test_an_artifact_swapped_after_construction_is_caught(self, method, also_expected):
        # CRITICAL 4. `_artifact` is a plain attribute; the rule re-reads it
        # at every call instead of trusting `__init__`'s verdict. What is
        # INVARIANT is the shape refusal naming the swap; which OTHER
        # screens fire depends on the artifact swapped in, and both cases
        # are exercised rather than one being silenced by a fixture.
        env = AttestedMeanConfidence(_confidence(), _attestation(producer=MEAN_PRODUCER))
        assert admission_problems(env, _demand(), AttestedMeanConfidence) == []
        env._artifact = _widened(method=method)
        problems = admission_problems(env, _demand(), AttestedMeanConfidence)
        reasons = {p.split(":")[0] for p in problems}
        assert any("swapped in after construction" in p for p in problems), problems
        assert any(
            "is ALSO a WidenedInterval" in p for p in problems
        ), problems
        assert reasons == {"wrong_unit"} | also_expected

    def test_an_attestation_swapped_after_construction_is_caught(self):
        env = AttestedOutcomeBand(_band(), _attestation())
        assert admission_problems(env, _demand(), AttestedOutcomeBand) == []
        env._attestation = _attestation(model_identity="release-2")
        assert any(
            p.startswith("foreign_model")
            for p in admission_problems(env, _demand(), AttestedOutcomeBand)
        )

    def test_an_envelope_carrying_no_attestation_is_refused(self):
        env = AttestedOutcomeBand(_band(), _attestation())
        env._attestation = {"model_identity": "m"}
        problems = admission_problems(env, _demand(), AttestedOutcomeBand)
        assert problems and "no UncertaintyAttestation" in problems[0]

    def test_a_metaclass_reattaching_methods_defeats_the_METHOD_not_the_RULE(self):
        """CRITICAL 3, and the honest boundary this design does not cross.

        A hostile metaclass can pop the sealed names out of the namespace,
        let ``__init_subclass__`` see a clean class, and reattach them after
        ``type.__new__``. Its ``problems()`` method then returns ``[]``.
        The module FUNCTION is not resolved through that class, so it still
        refuses — which is why a consumer sizing capital calls the function.
        What this does NOT do is stop code that controls the interpreter,
        and the module docstring says so rather than implying otherwise.
        """

        class Reattach(type(AttestedUncertainty)):
            def __new__(mcls, name, bases, namespace, **kwargs):
                stolen = {
                    key: namespace.pop(key)
                    for key in list(namespace)
                    if key in AttestedUncertainty._FINAL_METHODS
                }
                cls = super().__new__(mcls, name, bases, namespace, **kwargs)
                for key, value in stolen.items():
                    setattr(cls, key, value)
                return cls

        class Neutralized(AttestedMeanConfidence, metaclass=Reattach):
            @classmethod
            def artifact_type(cls):
                return MeanIntervalResult

            @classmethod
            def excluded_types(cls):
                return ()

            def problems(self, demand, expected):
                return []

        hopeless = _attestation(
            model_identity="release-2",
            known_at_ms=DECISION_TS + 1,
            calibration_end_ms=0,
            producer="nobody:Nothing",
            coverage=None,
        )
        env = Neutralized(_widened(), hopeless)
        # The METHOD is defeated — recorded, not hidden.
        assert env.problems(_demand(), AttestedMeanConfidence) == []
        # The RULE is not.
        assert admission_problems(env, _demand(), AttestedMeanConfidence)

    def test_a_registered_member_still_passes_through_both_spellings(self):
        env = AttestedOutcomeBand(_band(), _attestation())
        assert env.problems(_demand(), AttestedOutcomeBand) == []
        assert admission_problems(env, _demand(), AttestedOutcomeBand) == []
        assert admit_uncertainty(env, _demand(), AttestedOutcomeBand) is env
        assert env.admit(_demand(), AttestedOutcomeBand) is env

    @pytest.mark.parametrize(
        "bad", [object(), None, "AttestedOutcomeBand", ConfidenceInterval]
    )
    def test_a_non_envelope_is_a_caller_error(self, bad):
        with pytest.raises(ValueError, match="envelope must be"):
            admission_problems(bad, _demand(), AttestedOutcomeBand)

    def test_the_same_artifact_type_exemption_stays_deleted(self):
        """MAJOR: restoring the exemption left 4674 + 236 tests green.

        The exemption skipped any registered member declaring the artifact
        type being wrapped, which is exactly the shape of an unregistered
        subclass of that member. This fails if it comes back.
        """

        class _Shadow(AttestedMeanConfidence):
            pass

        with pytest.raises(ValueError, match="two questions at once"):
            _Shadow(_confidence(), _attestation(producer=MEAN_PRODUCER))

    def test_the_declared_reason_set_is_exactly_six(self):
        # Shrink detection: a reason REMOVED upstream must fail a test, not
        # quietly produce fewer parametrized cases.
        assert REFUSAL_REASONS == (
            "foreign_model",
            "post_decision",
            "stale",
            "uncalibrated",
            "unknown_producer",
            "wrong_unit",
        )


def _registered():
    return list(UNCERTAINTY_INTAKES.values())


class TestTheAccessorsReadTheScreenedValues:
    """`artifact_of`/`attestation_of` exist so a consumer reads the SLOT.

    Round-3 review: reverting both bodies to ``return envelope.artifact`` /
    ``return envelope.attestation`` left 112 + 241 tests green, because no
    test made a property and its slot disagree. These do.
    """

    @staticmethod
    @contextlib.contextmanager
    def _lying_descriptors():
        """Redefine both accessors ON THE REGISTERED CLASS, then restore.

        A subclass cannot be used: the construction screen refuses an
        unregistered class wrapping a registered member's artifact type,
        and the use-time rule refuses it again. Rebinding the descriptor on
        the live class is how the disagreement is produced without leaving
        the shipped, registered member — which is also the realistic shape
        of the accident these accessors exist to survive.
        """
        AttestedOutcomeBand.artifact = property(lambda self: "not the artifact")
        AttestedOutcomeBand.attestation = property(lambda self: "not the attestation")
        try:
            yield
        finally:
            del AttestedOutcomeBand.artifact
            del AttestedOutcomeBand.attestation

    def test_artifact_of_returns_the_slot_not_the_property(self):
        band = _band()
        env = AttestedOutcomeBand(band, _attestation())
        with self._lying_descriptors():
            assert env.artifact == "not the artifact"
            assert artifact_of(env) is band
        assert env.artifact is band

    def test_attestation_of_returns_the_slot_not_the_property(self):
        attested = _attestation()
        env = AttestedOutcomeBand(_band(), attested)
        with self._lying_descriptors():
            assert env.attestation == "not the attestation"
            assert attestation_of(env) is attested
        assert env.attestation is attested

    def test_the_screens_survive_a_lying_descriptor_too(self):
        env = AttestedOutcomeBand(_band(), _attestation())
        with self._lying_descriptors():
            assert admission_problems(env, _demand(), AttestedOutcomeBand) == []

    def test_the_accessors_agree_with_the_screens_on_an_honest_envelope(self):
        env = AttestedOutcomeBand(_band(), _attestation())
        assert artifact_of(env) is env.artifact
        assert attestation_of(env) is env.attestation

    def test_the_accessors_are_total_on_a_stripped_instance(self):
        env = AttestedOutcomeBand(_band(), _attestation())
        del env._artifact
        assert artifact_of(env) is None


class TestTheRegistryIsAuthoritativeForTheDemandToo:
    """Round-4 Critical: `__bases__` forges a REAL `__mro__`, so ask the registry."""

    def test_a_bases_rebinding_cannot_satisfy_the_closed_family(self):
        env = AttestedFalseSignalRate(_rate(), _attestation(producer=RATE_PRODUCER))
        assert admission_problems(env, _demand(), ProbabilityUpperBound)
        original = AttestedFalseSignalRate.__bases__
        try:
            AttestedFalseSignalRate.__bases__ = (ProbabilityUpperBound,)
            # The forgery is REAL: this is ordinary C3 linearization, not a
            # virtual registration, and __init_subclass__ never sees it.
            assert ProbabilityUpperBound in AttestedFalseSignalRate.__mro__
            problems = admission_problems(env, _demand(), ProbabilityUpperBound)
            assert [p.split(":")[0] for p in problems] == ["wrong_unit"]
            assert "not a registered intake" in problems[0]
            with pytest.raises(ValueError, match="not a registered intake"):
                admit_uncertainty(env, _demand(), ProbabilityUpperBound)
            # And the envelope's own, legitimate demand still works.
            assert admission_problems(env, _demand(), AttestedFalseSignalRate) == []
        finally:
            AttestedFalseSignalRate.__bases__ = original
        assert ProbabilityUpperBound not in AttestedFalseSignalRate.__mro__

    def test_a_bases_rebinding_is_still_caught_on_a_registered_demand(self):
        env = AttestedFalseSignalRate(_rate(), _attestation(producer=RATE_PRODUCER))
        original = AttestedFalseSignalRate.__bases__
        try:
            AttestedFalseSignalRate.__bases__ = (AttestedMeanConfidence,)
            problems = admission_problems(env, _demand(), AttestedMeanConfidence)
            assert any(
                p.startswith("wrong_unit")
                and "answers 'mean_confidence' over ConfidenceInterval" in p
                for p in problems
            ), problems
        finally:
            AttestedFalseSignalRate.__bases__ = original

    def test_a_freshly_registered_rogue_cannot_reach_the_closed_family(self):
        class _RogueArtifact:
            def __init__(self):
                self.evidence = {"estimator": RATE_PRODUCER}

        class FreshRogue(AttestedMeanConfidence):
            @classmethod
            def artifact_type(cls):
                return _RogueArtifact

            @classmethod
            def estimand(cls):
                return "fresh_rogue"

            @classmethod
            def excluded_types(cls):
                return ()

            @classmethod
            def registered_producers(cls):
                return (GrenanderLocalFdr,)

            @classmethod
            def artifact_producer(cls, artifact):
                return artifact.evidence.get("estimator")

        register_uncertainty_intake("tests_fresh_rogue", FreshRogue)
        try:
            rogue = FreshRogue(_RogueArtifact(), _attestation(producer=RATE_PRODUCER))
            assert admission_problems(rogue, _demand(), ProbabilityUpperBound)
            original = FreshRogue.__bases__
            try:
                FreshRogue.__bases__ = (ProbabilityUpperBound,)
                assert admission_problems(rogue, _demand(), ProbabilityUpperBound)
            finally:
                FreshRogue.__bases__ = original
            # It also cannot answer a demand it does not really answer.
            assert admission_problems(rogue, _demand(), AttestedMeanConfidence)
        finally:
            _INTAKES.pop("tests_fresh_rogue", None)  # read-only in public

    @pytest.mark.parametrize(
        "expected", [AttestedUncertainty, ProbabilityUpperBound]
    )
    def test_a_demand_the_registry_does_not_name_is_refused(self, expected):
        # Minor: `expected` was only ever a registered leaf in tests, and a
        # non-leaf demand admitted a legitimate envelope of ANY estimand.
        env = AttestedFalseSignalRate(_rate(), _attestation(producer=RATE_PRODUCER))
        problems = admission_problems(env, _demand(), expected)
        assert [p.split(":")[0] for p in problems] == ["wrong_unit"]
        assert expected.__name__ in problems[0]
        assert "not a registered intake" in problems[0]

    def test_an_abstract_class_cannot_be_registered(self):
        # Sweep (a): a registered intake is asked for the declarations the
        # screens run on, and an abstract one answers each with None.
        with pytest.raises(ValueError, match="abstract"):
            register_uncertainty_intake("tests_abstract", ProbabilityUpperBound)
        assert "tests_abstract" not in UNCERTAINTY_INTAKES

    def test_the_authority_is_the_demanded_class_never_the_envelopes(self):
        # The shipped members declare different artifact types, so an
        # envelope answering one demand cannot satisfy another.
        pairs = [
            (AttestedMeanConfidence, _confidence(), MEAN_PRODUCER),
            (AttestedOutcomeBand, _band(), BAND_PRODUCER),
            (AttestedFalseSignalRate, _rate(), RATE_PRODUCER),
        ]
        for member, artifact, producer in pairs:
            env = member(artifact, _attestation(producer=producer))
            for other, _artifact, _producer in pairs:
                problems = admission_problems(env, _demand(), other)
                if other is member:
                    assert problems == []
                else:
                    assert problems and problems[0].startswith("wrong_unit")


class TestTheRegistryIsWriteOnlyThroughItsFrontDoor:
    """Round-4 review: every validation lived in `register_uncertainty_intake`.

    The registry is the one piece of state every screen trusts, and it was
    a bare exported dict, so ``dict.__setitem__`` skipped the abstract-member
    refusal, the name-collision refusal and the ``artifact_type`` conflict
    refusal. No metaclass or MRO knowledge needed — just item assignment.
    """

    def test_the_public_registry_is_a_read_only_view(self):
        assert isinstance(UNCERTAINTY_INTAKES, types.MappingProxyType)
        assert UNCERTAINTY_INTAKES.keys() == _INTAKES.keys()

    @staticmethod
    def _set_abstract(reg):
        reg["evil"] = ProbabilityUpperBound

    @staticmethod
    def _rebind_existing(reg):
        reg["false_signal_rate"] = AttestedMeanConfidence

    @staticmethod
    def _delete_existing(reg):
        del reg["outcome_band"]

    @pytest.mark.parametrize(
        "mutate",
        ["_set_abstract", "_rebind_existing", "_delete_existing"],
    )
    def test_writing_to_it_directly_raises(self, mutate):
        # REPRODUCER 1 and 2 both begin here, and both now stop here.
        # mappingproxy answers item assignment with TypeError and a missing
        # mutator attribute with AttributeError; either is a refusal.
        with pytest.raises((TypeError, AttributeError)):
            getattr(self, mutate)(UNCERTAINTY_INTAKES)
        assert sorted(UNCERTAINTY_INTAKES) == [
            "false_signal_rate",
            "mean_confidence",
            "outcome_band",
        ]

    @pytest.mark.parametrize("method", ["clear", "pop", "popitem", "update", "setdefault"])
    def test_it_exposes_no_mutating_method(self, method):
        assert not hasattr(UNCERTAINTY_INTAKES, method)

    def test_the_front_door_still_works_and_the_view_sees_it(self):
        class _Subject:
            pass

        class _Member(AttestedUncertainty):
            @classmethod
            def artifact_type(cls):
                return _Subject

            @classmethod
            def estimand(cls):
                return "round_five_probe"

            @classmethod
            def excluded_types(cls):
                return ()

            @classmethod
            def registered_producers(cls):
                return ()

            @classmethod
            def artifact_producer(cls, artifact):
                return None

        register_uncertainty_intake("tests_round_five", _Member)
        try:
            assert UNCERTAINTY_INTAKES["tests_round_five"] is _Member
            assert uncertainty_intake("tests_round_five") is _Member
            assert _Member in _registered()
        finally:
            _INTAKES.pop("tests_round_five", None)
        assert "tests_round_five" not in UNCERTAINTY_INTAKES

    def test_every_screen_reads_the_same_view(self):
        # A member registered through the front door is immediately visible
        # to the rule; a name absent from the view is refused by it.
        env = AttestedOutcomeBand(_band(), _attestation())
        assert admission_problems(env, _demand(), AttestedOutcomeBand) == []
        assert UNCERTAINTY_INTAKES["outcome_band"] is AttestedOutcomeBand

    def test_an_unusable_declaration_is_a_coded_refusal_not_a_crash(self):
        """The round-4 uncaught TypeError, now a named refusal.

        Reachable only past the registry's front door, which is why the
        registry is sealed AND this check exists: an unhandled exception at
        a risk gate is worse than a refusal, whatever produced it.
        """
        env = AttestedFalseSignalRate(_rate(), _attestation(producer=RATE_PRODUCER))
        _INTAKES["tests_abstract_member"] = ProbabilityUpperBound
        original = AttestedFalseSignalRate.__bases__
        try:
            AttestedFalseSignalRate.__bases__ = (ProbabilityUpperBound,)
            problems = admission_problems(env, _demand(), ProbabilityUpperBound)
            assert problems, "an abstract authority must refuse, not admit"
            assert all(p.startswith("wrong_unit") for p in problems), problems
            assert any("is not a type" in p for p in problems), problems
        finally:
            AttestedFalseSignalRate.__bases__ = original
            _INTAKES.pop("tests_abstract_member", None)

    def test_no_other_module_level_state_the_screens_trust_is_mutable(self):
        # Sweep: everything else this module exports that a screen reads is
        # an immutable tuple. A rebinding of the module global itself needs
        # the same capability as the disclosed hostile-metaclass boundary.
        import dskit.pipeline.uncertainty_intake as module

        for name in module.__all__:
            value = getattr(module, name)
            if isinstance(value, (type, types.FunctionType)):
                continue
            assert isinstance(
                value, (tuple, types.MappingProxyType)
            ), f"{name} is mutable public state: {type(value).__name__}"
