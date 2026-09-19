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
import ast
import contextlib
import hashlib
import gc
import inspect
from unittest import mock
import importlib
import random
import types


def _boom(*_args, **_kwargs):
    """A hook with an ordinary coding bug in it."""
    raise RuntimeError("hook exploded")


def test_the_refusal_helper_undoes_a_registration_that_stops_refusing():
    """The cleanup path, EXERCISED rather than merely written.

    ``_refuses_registration``'s ``finally`` only matters on the day a guard
    regresses, so it is the one line in this file that would otherwise never
    run — and an unrun cleanup path is exactly how five negative-registration
    tests came to leave a SHIPPED name rebound for the rest of the process
    (round-6 review). Driving it needs a registration that SUCCEEDS: the
    ``pytest.raises`` then fails the assertion, and the entry must be gone
    anyway.
    """
    fresh, _ = _probe_member({"estimand": classmethod(lambda cls: "helper_probe")})
    with pytest.raises(pytest.fail.Exception):
        _refuses_registration("tests_helper_probe", fresh)
    assert "tests_helper_probe" not in UNCERTAINTY_INTAKES


def test_the_registry_snapshot_sees_a_hook_mutated_on_a_shipped_member():
    """The fixture's REACH, exercised rather than assumed.

    Round-8 review: the snapshot compared ``dict(UNCERTAINTY_INTAKES)``, which
    sees a name rebound to a different CLASS and nothing else — so mutating a
    shipped member's hook and never restoring it passed the fixture silently
    and was caught only by whichever unrelated later test happened to overlap.
    Reverting that widening breaks no test unless something actually leaks, so
    the reach is pinned here directly instead.
    """
    before = _registry_state()
    original = AttestedOutcomeBand.__dict__["artifact_type"]
    AttestedOutcomeBand.artifact_type = classmethod(lambda cls: int)
    try:
        assert _registry_state() != before, (
            "the snapshot cannot see a registered member's own hooks move"
        )
    finally:
        AttestedOutcomeBand.artifact_type = original
    assert _registry_state() == before


def _probe_member(overrides):
    """A registrable member with one hook replaced, plus an artifact for it."""

    class _ProbeArtifact:
        method = "dskit.pipeline.mean_interval:ClusterBootstrapInterval"

    body = {
        "artifact_type": classmethod(lambda cls: _ProbeArtifact),
        "estimand": classmethod(lambda cls: "probe"),
        "excluded_types": classmethod(lambda cls: ()),
        "registered_producers": classmethod(lambda cls: ()),
        "artifact_producer": classmethod(lambda cls, artifact: artifact.method),
    }
    body.update(overrides)
    member = type("_ProbeMember", (AttestedUncertainty,), body)
    return member, _ProbeArtifact()

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
    _ask,
    CLOSED_FAMILIES,
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


def _refuses_registration(name, cls, match=None):
    """Assert registering ``name`` refuses, and UNDO it if it ever stops refusing.

    A bare ``with pytest.raises(...): register_uncertainty_intake(...)`` leaves
    the entry in place on the day the guard regresses — and one of these rebinds
    a SHIPPED name, so the regression's blast radius becomes execution-order
    dependent and every later "+N extra failures" count is noise (round-6
    review). The undo is in a ``finally``, so it runs even when ``pytest.raises``
    itself fails the test.

    Parameters
    ----------
    name : str
        The registry key the registration must be refused for.
    cls : type
        The member being offered.
    match : str, optional
        A regex the refusal must match (default ``None``, any refusal).

    Returns
    -------
    ValueError
        The refusal, for a caller that wants to assert more about it.
    """
    undo = []
    try:
        with pytest.raises(ValueError, match=match) as caught:
            undo.append(register_uncertainty_intake(name, cls))
    finally:
        for forget in undo:
            forget()
    return caught.value



def _registry_state():
    """The mapping AND each member's own hook answers.

    Round-8 review: comparing `dict(UNCERTAINTY_INTAKES)` alone sees a name
    rebound to a different CLASS and nothing else — so mutating a shipped
    member's hook and never restoring it passed this fixture silently,
    corrupting that class process-wide, and was caught only by whichever
    unrelated later test happened to overlap. The registry holds LIVE
    references, which is the module's own stated threat model, so the net
    has to look at what those references now answer.
    """
    state = {}
    for key, member in UNCERTAINTY_INTAKES.items():
        hooks = []
        for hook in ("artifact_type", "estimand", "excluded_types",
                     "registered_producers"):
            value, refusal = _ask(member, hook)
            hooks.append((hook, repr(refusal[0] if refusal else value)))
        state[key] = (member, tuple(hooks))
    return state



@pytest.fixture(autouse=True)
def _the_registry_is_left_exactly_as_it_was():
    """No test may change the shipped registry, whatever it asserts on the way.

    Round-6 review found five negative-registration tests with no cleanup path
    for the day their guard regresses — one of them rebinding a SHIPPED name,
    which then corrupts every test that runs after it in the same process and
    makes any "+N extra failures" measurement order-dependent noise. The
    per-test undo is the fix; this is the net that says so out loud.
    """
    before = _registry_state()
    yield
    after = _registry_state()
    assert after == before, (
        "this test changed the registry and did not put it back: "
        f"added={sorted(set(after) - set(before))} "
        f"removed={sorted(set(before) - set(after))} "
        f"changed={sorted(k for k in set(after) & set(before) if after[k] != before[k])}"
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

        forget = register_uncertainty_intake("tests_other_subject", _OtherIntake)
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
            forget()


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

    def test_coverage_exactly_at_the_declared_floor_is_admitted(self):
        """The floor is INCLUSIVE, and that is a decision, not an accident.

        Self-review by mutation: relaxing ``measured < min`` to ``measured <=
        min`` left all 174 tests green, so nothing said whether a producer
        measuring EXACTLY the declared minimum is covered or not. It is —
        ``min_measured_coverage`` is a floor the evidence must reach, not one
        it must clear — and a run whose measurement lands on the number is the
        case an operator is most likely to meet.
        """
        demand = _demand()
        env = AttestedOutcomeBand(
            _band(),
            _attestation(coverage=CoverageEvidence(
                target=0.95, measured=demand.min_measured_coverage,
                evidence_id="ev-floor", n_units=40)),
        )
        assert env.problems(demand, AttestedOutcomeBand) == []
        below = AttestedOutcomeBand(
            _band(),
            _attestation(coverage=CoverageEvidence(
                target=0.95, measured=demand.min_measured_coverage - 0.01,
                evidence_id="ev-under", n_units=40)),
        )
        assert [p.split(":")[0] for p in below.problems(demand, AttestedOutcomeBand)] == [
            "uncalibrated"
        ]

    def test_evidence_known_exactly_at_the_decision_instant_is_admitted(self):
        """The post_decision boundary is INCLUSIVE at the instant itself.

        Self-review by mutation: tightening ``known_at_ms > decision_ts_ms`` to
        ``>=`` left all 174 tests green. Evidence known AT the decision stamp
        existed when the decision was taken; only evidence known AFTER it is a
        look-ahead. One millisecond either side is pinned so the rule cannot
        drift by one.
        """
        demand = _demand()
        at_the_instant = AttestedOutcomeBand(
            _band(), _attestation(known_at_ms=demand.decision_ts_ms)
        )
        assert at_the_instant.problems(demand, AttestedOutcomeBand) == []
        one_later = AttestedOutcomeBand(
            _band(), _attestation(known_at_ms=demand.decision_ts_ms + 1)
        )
        assert [
            p.split(":")[0] for p in one_later.problems(demand, AttestedOutcomeBand)
        ] == ["post_decision"]

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
        _refuses_registration(
            "outcome_band", AttestedMeanConfidence, match="already registered"
        )

    def test_re_registering_the_same_class_is_idempotent(self):
        register_uncertainty_intake("outcome_band", AttestedOutcomeBand)
        assert uncertainty_intake("outcome_band") is AttestedOutcomeBand

    @pytest.mark.parametrize(
        "name,cls",
        [("", AttestedOutcomeBand), ("x", object), ("x", "AttestedOutcomeBand")],
    )
    def test_an_unusable_registration_refuses(self, name, cls):
        _refuses_registration(name, cls)

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

        _refuses_registration("tests_twin", _Twin, match="already claims")
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

    def test_a_virtual_registration_between_two_REGISTERED_members_still_refuses(self):
        """The ``__mro__`` rule itself, pinned where it is observable.

        ``test_a_virtual_registration_cannot_forge_family_membership`` refuses
        via round 4's registered-demand screen, because the forged family is
        UNREGISTERED — so that test never exercises the ``__mro__`` choice at
        all. Swapping ``expected in cls.__mro__`` for ``issubclass(cls,
        expected)`` in ``_answering`` left all 172 tests green (self-review by
        mutation). The difference is only visible when BOTH classes are
        registered: ``register()`` then makes ``issubclass`` true while
        ``__mro__`` stays honest, and only the honest one refuses.
        """
        demanded, _ = _probe_member({"estimand": classmethod(lambda cls: "demanded")})
        answering, artifact = _probe_member(
            {"estimand": classmethod(lambda cls: "answering")}
        )
        forget_demanded = register_uncertainty_intake("tests_demanded", demanded)
        try:
            forget_answering = register_uncertainty_intake("tests_answering", answering)
            try:
                env = answering(artifact, _attestation(producer=MEAN_PRODUCER))
                demanded.register(answering)          # the forgery
                assert issubclass(answering, demanded)
                assert demanded not in answering.__mro__
                problems = admission_problems(env, _demand(), demanded)
                assert problems, "a virtual subclass must not answer for the real one"
                assert [p.split(":")[0] for p in problems] == ["wrong_unit"], problems
                # THE REASON, not merely a refusal. Under ``issubclass`` the
                # forged member is selected as answering, `_question_problems`
                # returns nothing, and whatever refuses next refuses for some
                # OTHER reason — which a bare "problems is non-empty" assertion
                # would have accepted. Asserting the estimand-mismatch text is
                # what makes this row bite.
                assert "but this decision requires" in problems[0], problems
            finally:
                _abc._reset_registry(demanded)
                forget_answering()
        finally:
            forget_demanded()

    def test_a_stale_undo_cannot_remove_the_member_that_replaced_it(self):
        """``forget`` removes its own registration, or nothing.

        The docstring promises it "removes the entry only while it is still
        the one in place", and that rests on one ``is`` comparison. Relaxing it
        to ``if name in store`` left all 172 tests green (self-review by
        mutation), while a REPLAYED stale undo silently deleted whichever
        member had since taken the name — an unregistration nobody asked for,
        through the sanctioned API.
        """
        first, _ = _probe_member({"estimand": classmethod(lambda cls: "first")})
        second, _ = _probe_member({"estimand": classmethod(lambda cls: "second")})
        forget_first = register_uncertainty_intake("tests_one_slot", first)
        forget_first()
        assert "tests_one_slot" not in UNCERTAINTY_INTAKES
        forget_second = register_uncertainty_intake("tests_one_slot", second)
        try:
            forget_first()                       # the stale undo, replayed
            assert UNCERTAINTY_INTAKES["tests_one_slot"] is second
        finally:
            forget_second()

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

        forget = register_uncertainty_intake("tests_fresh_rogue", FreshRogue)
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
            forget()

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
        """Refused for being ABSTRACT, not for some later gate tripping first.

        Self-review by mutation: with the abstract check deleted, this test
        still passed — the registry key was ``tests_abstract`` and the match
        was ``"abstract"``, so the assertion was satisfied by the NAME I chose
        while the refusal actually came from the candidate type-check further
        down ("None is not a type"). The key no longer carries the word and the
        assertion reads the reason.
        """
        refusal = _refuses_registration("tests_unfinished", ProbabilityUpperBound)
        assert "it is abstract" in str(refusal), refusal
        assert "unimplemented" in str(refusal), refusal
        assert "tests_unfinished" not in UNCERTAINTY_INTAKES

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

    def test_there_is_no_private_dict_to_import(self):
        """Round-5's Critical: the seal used to be one underscore wide.

        ``_INTAKES`` was a module attribute, so ``_INTAKES["outcome_band"]
        = Evil`` silently repointed a real capital-sizing estimand past
        every screen, and the whole sealing class still passed because it
        only ever mutated through the public name. The store is now a
        closure local of ``_sealed_registry``.
        """
        module = importlib.import_module("dskit.pipeline.uncertainty_intake")
        assert not hasattr(module, "_INTAKES")
        mutable = sorted(
            name
            for name, value in vars(module).items()
            if isinstance(value, dict) and not name.startswith("__")
        )
        # The only module-level dicts are the three SIBLING registries this
        # module imports for the producer screen. They belong to
        # false_signal / mean_interval / outcome_interval, are open by
        # those ADRs' design, and are not this module's to seal — which is
        # exactly why the unknown_producer message says "an open registry".
        assert mutable == [
            "CALIBRATORS",
            "FALSE_SIGNAL_ESTIMATORS",
            "MEAN_INTERVAL_ESTIMATORS",
        ], mutable

    def test_the_public_registry_is_a_read_only_view(self):
        assert isinstance(UNCERTAINTY_INTAKES, types.MappingProxyType)

    @staticmethod
    def _store_through_closure_cells(module):
        """The store, reached through the writer's closure cells."""
        for cell in module._register_intake.__closure__ or ():
            contents = cell.cell_contents
            if isinstance(contents, dict) and contents.keys() == UNCERTAINTY_INTAKES.keys():
                return contents
        return None

    @staticmethod
    def _store_through_gc_referents(module):
        """The store, reached as the proxied dict of the public view itself."""
        for referent in gc.get_referents(UNCERTAINTY_INTAKES):
            if isinstance(referent, dict) and referent.keys() == UNCERTAINTY_INTAKES.keys():
                return referent
        return None

    #: Structurally DIFFERENT routes to the store, not one route named twice:
    #: the first goes through the writer function object, the second through
    #: the public view and touches no closure at all. Round-6 review found the
    #: second while the module named only the first, which is the whole reason
    #: the ceiling is now stated as a rule.
    _CEILING_ROUTES = ("_store_through_closure_cells", "_store_through_gc_referents")

    @pytest.mark.parametrize("route", _CEILING_ROUTES)
    def test_every_disclosed_route_really_reaches_the_store(self, route):
        """Each route reaches the LIVE store, proven by writing through it.

        Reaching a dict that merely compares equal proves nothing — round-4
        review of a sibling branch found a pin asserting ``A is A``. So each
        route writes a sentinel and the PUBLIC view is read back; the write is
        undone in a ``finally``.
        """
        module = importlib.import_module("dskit.pipeline.uncertainty_intake")
        store = getattr(self, route)(module)
        assert store is not None, f"{route} no longer reaches the store"
        assert "ceiling_probe" not in UNCERTAINTY_INTAKES
        try:
            store["ceiling_probe"] = AttestedOutcomeBand
            assert UNCERTAINTY_INTAKES["ceiling_probe"] is AttestedOutcomeBand, (
                f"{route} reached a COPY, not the live store"
            )
        finally:
            store.pop("ceiling_probe", None)
        assert "ceiling_probe" not in UNCERTAINTY_INTAKES

    def test_the_two_routes_are_not_the_same_route_named_twice(self):
        """Independence, checked by BEHAVIOUR and not only by reading the source.

        A source scan alone is weak: a delegating call that never spells
        ``__closure__`` passes it. So the gc route is exercised with the
        closure route replaced by a stub that finds nothing — if it were
        delegating, it would find nothing too.
        """
        module = importlib.import_module("dskit.pipeline.uncertainty_intake")
        cls = type(self)
        assert (
            cls._store_through_closure_cells(module)
            is cls._store_through_gc_referents(module)
        ), "both routes must reach the one live store"
        with mock.patch.object(
            cls, "_store_through_closure_cells", staticmethod(lambda module: None)
        ):
            assert cls._store_through_closure_cells(module) is None
            assert cls._store_through_gc_referents(module) is not None, (
                "the gc route reaches the store only VIA the closure route"
            )
        source = inspect.getsource(cls._store_through_gc_referents)
        assert "__closure__" not in source
        assert "_store_through_closure_cells" not in source
        assert "UNCERTAINTY_INTAKES" in source

    @staticmethod
    def _module_prose():
        """Every docstring and every ``#:`` note in the module, keyed by owner.

        NOTHING CLASSIFIES and nothing is left out. Round 7 pinned two
        docstrings and one comment; round-8 review put a false ceiling claim in
        the MODULE docstring, in ``admission_problems.__doc__``, and in the
        ``#:`` block above ``CLOSED_FAMILIES`` — three places nothing read —
        and all 164 tests stayed green. A pin whose SCOPE is chosen is a pin
        with a place to write around it.
        """
        module = importlib.import_module("dskit.pipeline.uncertainty_intake")
        source = inspect.getsource(module)
        tree = ast.parse(source)
        out = {}
        doc = ast.get_docstring(tree)
        if doc:
            out["module"] = doc

        def walk(node, prefix):
            for child in ast.iter_child_nodes(node):
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    name = f"{prefix}{child.name}"
                    child_doc = ast.get_docstring(child)
                    if child_doc:
                        out[f"doc:{name}"] = child_doc
                    walk(child, f"{name}.")

        walk(tree, "")
        lines = source.splitlines()
        block, started = [], False
        for index, line in enumerate(lines):
            if line.lstrip().startswith("#:"):
                started = True
                block.append(line.strip()[2:].strip())
            elif started:
                subject = next((text.strip() for text in lines[index:] if text.strip()), "")
                # The line number is in the key ON PURPOSE. Keying by subject
                # alone COLLIDES when a name is documented twice — this module
                # assigns CLOSED_FAMILIES in two places, each with its own
                # ``#:`` block, and the second silently overwrote the first, so
                # the very note carrying "drift protection, not a boundary" was
                # not pinned at all. Found by mutation, after the reach test
                # had already passed on nothing but the key's existence.
                label = subject.split("=")[0].strip()[:48]
                out[f"note:{label}@{index}"] = " ".join(block)
                block, started = [], False
        return out

    @staticmethod
    def _digest(text):
        """First 16 hex of sha256 over the whitespace-normalised text."""
        return hashlib.sha256(" ".join(text.split()).encode()).hexdigest()[:16]

    #: Every piece of prose in ``uncertainty_intake.py``, pinned by digest.
    #:
    #: FOUR ROUNDS shipped a false claim this module's own pin could not see:
    #: a polarity-free substring (round 5), a double negation wrapped around a
    #: polarity-bearing one (round 6), and then three places the round-7 digest
    #: simply did not read (round 8). Each answer was a better-chosen scope,
    #: and each scope had an outside. There is no outside here.
    #:
    #: HONEST SCOPE, narrower than it sounds: this detects CHANGE, not
    #: falsehood. When it fails, read the new text, decide whether it is TRUE,
    #: and update the digest in the same commit. The cost is that every
    #: docstring edit in this module needs a digest update, and that is the
    #: cost of a module whose prose has been wrong four rounds running.
    _PINNED_MODULE_PROSE = {
        "doc:AttestedFalseSignalRate": "1f14651ef2417cf5",
        "doc:AttestedFalseSignalRate.artifact_producer": "d9e08ab82df75b5b",
        "doc:AttestedFalseSignalRate.artifact_type": "66df7f8b64dca19b",
        "doc:AttestedFalseSignalRate.estimand": "e7d01bd6fc21c420",
        "doc:AttestedFalseSignalRate.excluded_types": "8cdf1764f3158f8a",
        "doc:AttestedFalseSignalRate.registered_producers": "71c29cd72f3a324f",
        "doc:AttestedMeanConfidence": "2c1edfba70ac308d",
        "doc:AttestedMeanConfidence.artifact_producer": "49732ddd807c1b57",
        "doc:AttestedMeanConfidence.artifact_type": "1623fb3c3daef0b4",
        "doc:AttestedMeanConfidence.estimand": "df0a831234194da8",
        "doc:AttestedMeanConfidence.excluded_types": "48596e210b69d867",
        "doc:AttestedMeanConfidence.registered_producers": "ec3c3d7d3d3e2745",
        "doc:AttestedOutcomeBand": "2302287b8b197240",
        "doc:AttestedOutcomeBand.artifact_producer": "53b5c2972d6fa909",
        "doc:AttestedOutcomeBand.artifact_type": "38eb84e828b9d8a1",
        "doc:AttestedOutcomeBand.estimand": "ec2ec7c575b4ac79",
        "doc:AttestedOutcomeBand.excluded_types": "c942a643adbac72e",
        "doc:AttestedOutcomeBand.registered_producers": "953b066c22ace2fb",
        "doc:AttestedUncertainty": "bb38cc3300582493",
        "doc:AttestedUncertainty.__init_subclass__": "de23728b5f28e384",
        "doc:AttestedUncertainty.admit": "cb64ab4b57be3d40",
        "doc:AttestedUncertainty.artifact": "7df8c12b6a5f2399",
        "doc:AttestedUncertainty.artifact_producer": "bebd83e9f375d968",
        "doc:AttestedUncertainty.artifact_type": "71b79018d8513e63",
        "doc:AttestedUncertainty.attestation": "c5b514760c5ea9ff",
        "doc:AttestedUncertainty.estimand": "1b665d3dbb9bb2b6",
        "doc:AttestedUncertainty.excluded_types": "391a36a11f0c36fe",
        "doc:AttestedUncertainty.problems": "120ca5c7a77043d9",
        "doc:AttestedUncertainty.registered_producers": "3b7bfb859835772f",
        "doc:CoverageEvidence": "e1792884d9e8890b",
        "doc:CoverageEvidence.__post_init__": "b49cc3e776d4501f",
        "doc:DecisionDemand": "fa5fc22a2d44941f",
        "doc:DecisionDemand.__post_init__": "1265443abfc3ce2b",
        "doc:ProbabilityUpperBound": "e34ca5b5e35504e9",
        "doc:UncertaintyAttestation": "3a547b353c1311b2",
        "doc:UncertaintyAttestation.__post_init__": "21c1bb17309da959",
        "doc:_answering": "4fd21c349bfceab3",
        "doc:_artifact_problems": "28137696869ce46e",
        "doc:_ask": "f03a5e6403b6dd64",
        "doc:_check_open_unit": "30d3e7d0490d7b28",
        "doc:_check_stamp": "2eae1d723921c3ec",
        "doc:_check_text": "efc17946ee1a7f27",
        "doc:_coverage_problems": "59f3b7acad93aad2",
        "doc:_declarations": "4c16d768ac0c812b",
        "doc:_identity_problems": "b22ad2f8bde664c6",
        "doc:_producer_problems": "8c854b83b6b5fc3d",
        "doc:_question_problems": "b0b4b9bf1425d5bd",
        "doc:_raw": "1f95cb0f3fb6b5d5",
        "doc:_registered_classes": "b85c4bd73a64c1ef",
        "doc:_sealed_registry": "2ee38cb8078d4c49",
        "doc:_sealed_registry.register": "5a03bc840541e945",
        "doc:_sealed_registry.register.forget": "394343b9283b6bec",
        "doc:_timing_problems": "6d91c692db2704cf",
        "doc:admission_problems": "19afd8d295f61e1d",
        "doc:admit_uncertainty": "c61e8001098b2346",
        "doc:artifact_of": "8ff1040b8b8fdd0d",
        "doc:attestation_of": "e50d2fe2d1b81166",
        "doc:register_uncertainty_intake": "c0116dbf01e1479b",
        "doc:uncertainty_intake": "7e6951e8bccce5f2",
        "module": "dd17688469567daa",
        "note:CLOSED_FAMILIES@1425": "991d1f583e217540",
        "note:CLOSED_FAMILIES@206": "1e2ad777643dbf5d",
        "note:REFUSAL_REASONS@186": "30a119c86f1cd445",
        "note:UNCERTAINTY_INTAKES, _register_intake@334": "279b5c5c34652ddc",
        "note:_FINAL_METHODS@631": "fdca5d5ee9a15038",
        "note:_HOOKS@642": "4ac957da937c3172",
    }

    def test_every_piece_of_prose_in_the_module_is_pinned_by_digest(self):
        """No prose in this module changes without this test failing."""
        observed = {k: self._digest(v) for k, v in self._module_prose().items()}
        pinned = self._PINNED_MODULE_PROSE
        added = sorted(set(observed) - set(pinned))
        removed = sorted(set(pinned) - set(observed))
        changed = sorted(
            k for k in set(observed) & set(pinned) if observed[k] != pinned[k]
        )
        assert not (added or removed or changed), (
            "prose moved in uncertainty_intake.py. Read the new text, decide "
            "whether it is TRUE, then update _PINNED_MODULE_PROSE in the same "
            f"commit. added={added} removed={removed} changed={changed}"
        )

    def test_the_prose_pin_reaches_the_three_places_round_8_review_wrote_in(self):
        """The pin's REACH, asserted rather than assumed.

        A `_module_prose` that quietly stopped walking would make the test
        above pass over a shrinking surface. The three locations round-8 review
        used are named here, and the docstring count is checked against an
        independent AST walk.
        """
        prose = self._module_prose()
        assert "module" in prose
        assert "doc:admission_problems" in prose
        notes = [k for k in prose if k.startswith("note:")]
        closed = [k for k in notes if k.startswith("note:CLOSED_FAMILIES@")]
        assert len(closed) == 2, (
            f"both CLOSED_FAMILIES notes must be pinned separately, got {closed}"
        )
        # No two blocks may share a key, or one is silently unpinned.
        assert len(set(notes)) == len(notes)
        module = importlib.import_module("dskit.pipeline.uncertainty_intake")
        independent = sum(
            1 for node in ast.walk(ast.parse(inspect.getsource(module)))
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            and ast.get_docstring(node)
        )
        assert sum(1 for k in prose if k.startswith("doc:")) == independent

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

        forget = register_uncertainty_intake("tests_round_five", _Member)
        try:
            assert UNCERTAINTY_INTAKES["tests_round_five"] is _Member
            assert uncertainty_intake("tests_round_five") is _Member
            assert _Member in _registered()
        finally:
            forget()
        assert "tests_round_five" not in UNCERTAINTY_INTAKES

    def test_every_screen_reads_the_same_view(self):
        # A member registered through the front door is immediately visible
        # to the rule; a name absent from the view is refused by it.
        env = AttestedOutcomeBand(_band(), _attestation())
        assert admission_problems(env, _demand(), AttestedOutcomeBand) == []
        assert UNCERTAINTY_INTAKES["outcome_band"] is AttestedOutcomeBand

    #: One row per misbehaving hook: ``(id, hook, body, expect)``. The id lives
    #: IN the row, not in a second list beside it — a separate ``ids=[...]``
    #: is a value in two places with nothing pinning them, and it silently
    #: mislabelled these cases while this very round was being written: a
    #: mutation was reported as killing ``registered_producers-str`` when it
    #: actually killed the new ``artifact_type-raises`` case four rows away.
    #: An id that names the wrong test is evidence that lies.
    _MISBEHAVING_HOOKS = (
        ("artifact_type-None", "artifact_type",
         classmethod(lambda cls: None), "is not a type"),
        ("estimand-empty", "estimand",
         classmethod(lambda cls: ""), "is not a non-empty string"),
        ("excluded_types-list", "excluded_types",
         classmethod(lambda cls: [object]), "not a tuple of types"),
        ("registered_producers-str", "registered_producers",
         classmethod(lambda cls: "nope"), "not a tuple of classes"),
        ("artifact_type-raises", "artifact_type",
         classmethod(_boom), "raised RuntimeError"),
        ("estimand-raises", "estimand", classmethod(_boom), "raised RuntimeError"),
        ("registered_producers-raises", "registered_producers",
         classmethod(_boom), "raised RuntimeError"),
        ("excluded_types-raises", "excluded_types",
         classmethod(_boom), "raised RuntimeError"),
    )

    def test_every_misbehaving_hook_case_is_labelled_with_its_own_hook(self):
        """An id that names a different hook makes every mutation report a lie."""
        for ident, hook, _, _ in self._MISBEHAVING_HOOKS:
            assert ident.startswith(f"{hook}-"), (ident, hook)
        assert len({row[0] for row in self._MISBEHAVING_HOOKS}) == len(
            self._MISBEHAVING_HOOKS
        )

    @pytest.mark.parametrize(
        "hook,body,expect",
        [row[1:] for row in _MISBEHAVING_HOOKS],
        ids=[row[0] for row in _MISBEHAVING_HOOKS],
    )
    def test_a_misbehaving_hook_is_a_coded_refusal_not_a_crash(self, hook, body, expect):
        """Round-5 Major: three of five hooks were screened, and only for shape.

        Each of these registers cleanly through the REAL front door — an
        ordinary coding bug in the sanctioned extension point — and used to
        propagate out of ``admission_problems`` into ``validate_inputs``
        and ``driver.run_node``, killing the run instead of naming the
        problem.
        """
        member, artifact = _probe_member({})
        forget = register_uncertainty_intake("tests_bad_hook", member)
        try:
            env = member(artifact, _attestation(producer=MEAN_PRODUCER))
            setattr(member, hook, body)
            problems = admission_problems(env, _demand(), member)
            assert problems, "a misbehaving hook must refuse, not admit"
            assert all(p.startswith("wrong_unit") for p in problems), problems
            assert any(expect in p for p in problems), problems
        finally:
            forget()

    def test_a_raising_estimand_on_the_WRONG_member_is_a_coded_refusal(self):
        """The sixth site: the estimand read that only builds a message.

        ``_question_problems`` reaches ``_ask(mine, "estimand")`` only when the
        envelope's class IS registered but does NOT answer the demand — the
        branch whose whole job is to say which question it answers instead. A
        raise there used to propagate uncaught out of ``admission_problems``,
        which is the crash-instead-of-refusal this module exists to prevent,
        at a branch nothing reached in a test (round-6 review).
        """
        member, artifact = _probe_member({})
        forget = register_uncertainty_intake("tests_wrong_member", member)
        try:
            env = member(artifact, _attestation(producer=MEAN_PRODUCER))
            member.estimand = classmethod(_boom)
            problems = admission_problems(env, _demand(), AttestedOutcomeBand)
            assert problems, "a misbehaving hook must refuse, not admit"
            assert any("estimand() raised RuntimeError" in p for p in problems), problems
            assert all(p.startswith("wrong_unit") for p in problems), problems
        finally:
            forget()

    def test_a_raising_artifact_producer_is_a_coded_refusal(self):
        # The fifth hook, which `_declaration_problems` never reached: the
        # `evidence["estimator"]`-instead-of-`.get` bug the shipped members
        # are careful to avoid.
        member, artifact = _probe_member(
            {"artifact_producer": classmethod(lambda cls, a: a.evidence["estimator"])}
        )
        forget = register_uncertainty_intake("tests_bad_producer", member)
        try:
            env = member(artifact, _attestation(producer=MEAN_PRODUCER))
            problems = admission_problems(env, _demand(), member)
            assert any("artifact_producer() raised" in p for p in problems), problems
        finally:
            forget()

    @pytest.mark.parametrize(
        "hook,how",
        [(classmethod(_boom), "raises"),
         (classmethod(lambda cls: ()), "answers an empty tuple"),
         (classmethod(lambda cls: None), "answers None")],
        ids=["peer-raises", "peer-returns-an-empty-tuple", "peer-returns-None"],
    )
    def test_an_unreadable_peer_blocks_construction_in_every_shape(self, hook, how):
        """Round-8 review, Major: only the RAISE half of this guard was driven.

        ``if refusal or not isinstance(other_wanted, type)`` has two
        independent halves, and the construction sweep only ever tested the
        first — so narrowing it to ``if refusal:`` passed all 164 tests. The
        second half is not decoration: a peer answering ``()`` raises nothing
        AND is a legal second argument to ``isinstance``, so without it
        ``isinstance(artifact, ())`` is simply False and the artifact is
        admitted SILENTLY — the round-6 Critical, reachable again through a
        one-condition narrowing. A peer answering ``None`` instead crashes at
        the risk gate, which is round-5's Major. Both shapes are driven here,
        at the site that was one-sided.
        """
        broken, _ = _probe_member({})
        forget_broken = register_uncertainty_intake("tests_mute_at_construction", broken)
        try:
            broken.artifact_type = hook
            with pytest.raises(ValueError) as caught:
                AttestedOutcomeBand(_band(), _attestation())
            assert "tests_mute_at_construction" in str(caught.value), how
            assert "never a clearance" in str(caught.value), how
        finally:
            forget_broken()

    def test_the_two_halves_of_the_unreadable_guard_overlap_by_construction(self):
        """Why dropping ``refusal or`` from any of the three guards kills nothing.

        Round-8 review scored three mutations as surviving — the RAISE half of
        each guard. They are EQUIVALENT MUTANTS, and this is the reason, pinned
        rather than argued: :func:`_ask` answers ``(None, [message])`` when a
        hook raises, and ``None`` is not a type, so the non-type half already
        covers every raise. The ``refusal or`` half is what carries the hook's
        own coded text into the refusal MESSAGE, which the tests above assert;
        it is not what decides. Stated here so a future reader does not read
        three surviving mutants as three coverage gaps.
        """
        member, _ = _probe_member({"artifact_type": classmethod(_boom)})
        value, refusal = _ask(member, "artifact_type")
        assert refusal and "raised RuntimeError" in refusal[0]
        assert value is None and not isinstance(value, type)
        # ... so a guard written with only the non-type half sees the raise too.
        quiet, _ = _probe_member({"artifact_type": classmethod(lambda cls: None)})
        quiet_value, quiet_refusal = _ask(quiet, "artifact_type")
        assert quiet_refusal == [] and not isinstance(quiet_value, type)

    def test_an_unreadable_peer_is_a_conflict_not_a_clearance_at_construction(self):
        """Round-6 CRITICAL: the ambiguity sweep failed OPEN on an unreadable peer.

        ``__init__`` loops over the whole registry asking every other member
        for its artifact type, to refuse an artifact two members both claim.
        A peer's hook is that peer's code, so it can raise — and the sweep read
        ``_ask``'s coded refusal as "this peer said nothing" and moved on. The
        result was not a crash, which is what round 5 pinned; it was a SILENT
        ADMISSION of exactly the ambiguity the sweep exists to refuse.

        The rule now: a member that will not say what it claims cannot be shown
        not to claim this, so it is a conflict. Still a REFUSAL and not a crash
        — the hook's own coded ``wrong_unit`` text is carried inside the
        message, which is what round 5's Major actually required.
        """
        broken, _ = _probe_member({})
        forget_broken = register_uncertainty_intake("tests_broken_other", broken)
        try:
            # Registration screens the hook, so the incumbent breaks AFTER it:
            # the registry holds LIVE references, which this module documents.
            broken.artifact_type = classmethod(_boom)
            with pytest.raises(ValueError) as caught:
                AttestedOutcomeBand(_band(), _attestation())
            message = str(caught.value)
            assert "tests_broken_other" in message
            assert "artifact_type() raised RuntimeError" in message, message
            assert "never a clearance" in message
        finally:
            forget_broken()

    def test_an_unreadable_peer_is_a_conflict_not_a_clearance_at_registration(self):
        """The same rule on the REGISTRATION path's uniqueness sweep.

        With the peer readable this raises on the ambiguity itself; with the
        peer unreadable it used to SUCCEED, which is how a second member came
        to claim an artifact type another member already claimed.
        """
        broken, _ = _probe_member({})
        forget_broken = register_uncertainty_intake("tests_broken_incumbent", broken)
        try:
            broken.artifact_type = classmethod(_boom)
            fresh, _ = _probe_member({"estimand": classmethod(lambda cls: "fresh")})
            refusal = _refuses_registration("tests_fresh_member", fresh)
            assert "tests_broken_incumbent" in str(refusal)
            assert "tests_fresh_member" not in UNCERTAINTY_INTAKES
        finally:
            forget_broken()

    @pytest.mark.parametrize(
        "hook,how",
        [(classmethod(_boom), "raises"), (classmethod(lambda cls: None), "answers None")],
        ids=["peer-raises", "peer-returns-a-non-type"],
    )
    def test_a_peer_that_will_not_say_what_it_claims_blocks_a_registration(self, hook, how):
        """Unreadable has TWO shapes, and only one of them is a raise.

        A peer whose ``artifact_type()`` answers something that is not a type
        is just as unreadable as one that raises — ``other_wanted is wanted``
        can never match it, so it silently clears every claimant. Found by
        mutation: refusing on the raise alone left this half unpinned, which is
        the same shape as the Critical it was written to fix.
        """
        peer, _ = _probe_member({})
        forget_peer = register_uncertainty_intake("tests_mute_peer", peer)
        try:
            peer.artifact_type = hook
            fresh, _ = _probe_member({"estimand": classmethod(lambda cls: "fresh")})
            refusal = _refuses_registration("tests_after_mute_peer", fresh)
            assert "tests_mute_peer" in str(refusal), how
            assert "tests_after_mute_peer" not in UNCERTAINTY_INTAKES
        finally:
            forget_peer()

    @pytest.mark.parametrize(
        "hook,how",
        [(classmethod(lambda cls: None), "answers None"),
         (classmethod(lambda cls: "a string"), "answers a non-type")],
        ids=["candidate-returns-None", "candidate-returns-a-non-type"],
    )
    def test_a_candidate_that_will_not_say_what_it_claims_is_not_registrable(self, hook, how):
        """Round-8 CRITICAL-adjacent: the peer rule was missing at its twin site.

        Round 7 refused an UNREADABLE PEER but never applied the same rule to
        the CANDIDATE, so a member whose ``artifact_type()`` merely forgot its
        ``return`` registered cleanly — and then the peer sweep refused every
        OTHER member's construction and every later registration, naming it.
        One missing ``return``, through the sanctioned front door, took the
        module out for the whole process. The check belongs at both sites or
        neither.
        """
        member, _ = _probe_member({"artifact_type": hook})
        refusal = _refuses_registration("tests_mute_candidate", member)
        assert "is not a type" in str(refusal), how
        assert "tests_mute_candidate" not in UNCERTAINTY_INTAKES

    def test_one_bad_member_cannot_take_the_whole_module_out(self):
        """The consequence, end to end, as the reviewer demonstrated it.

        With the candidate check missing, this sequence left every shipped
        member unconstructible for the rest of the process.
        """
        member, _ = _probe_member({"artifact_type": classmethod(lambda cls: None)})
        _refuses_registration("tests_module_killer", member)
        # The shipped members still work, which is the whole point.
        env = AttestedOutcomeBand(_band(), _attestation())
        assert admission_problems(env, _demand(), AttestedOutcomeBand) == []
        healthy, _ = _probe_member({"estimand": classmethod(lambda cls: "healthy")})
        forget = register_uncertainty_intake("tests_still_open", healthy)
        try:
            assert UNCERTAINTY_INTAKES["tests_still_open"] is healthy
        finally:
            forget()

    def test_a_broken_peer_cannot_let_two_members_claim_one_artifact_type(self):
        """The Critical, end to end, as the reviewer demonstrated it.

        Two members declaring the SAME concrete artifact type — which
        `_probe_member` alone cannot express, because it builds a fresh
        artifact class per call, and that is why the two tests above could not
        have caught this. With the incumbent healthy the second registration is
        refused; with its hook broken it used to register, an envelope of the
        second member used to construct, and `admission_problems` used to
        return NO problems for an artifact answering two questions at once —
        at the gate `nodes_capital` sizes real capital from.
        """
        class Shared:
            method = MEAN_PRODUCER

        def claimant(estimand):
            member, _ = _probe_member({
                "artifact_type": classmethod(lambda cls: Shared),
                "estimand": classmethod(lambda cls: estimand),
            })
            return member

        first, second = claimant("first"), claimant("second")
        forget_first = register_uncertainty_intake("tests_claim_first", first)
        try:
            _refuses_registration("tests_claim_second", second, match="already claims")
            first.artifact_type = classmethod(_boom)
            refusal = _refuses_registration("tests_claim_second", second)
            assert "tests_claim_first" in str(refusal)
            assert "tests_claim_second" not in UNCERTAINTY_INTAKES
            # END TO END, which round 8 claimed and did not do (round-8 review,
            # Minor): the second member is never registered, so the path that
            # must refuse is CONSTRUCTION — reverting the construction sweep
            # alone used to leave this test green.
            with pytest.raises(ValueError, match="never a clearance"):
                second(Shared(), _attestation(producer=MEAN_PRODUCER))
        finally:
            forget_first()

    def test_a_raising_artifact_type_refuses_registration_as_ValueError(self):
        member, _artifact = _probe_member({"artifact_type": classmethod(_boom)})
        with pytest.raises(ValueError, match="artifact_type"):
            register_uncertainty_intake("tests_raising_type", member)
        assert "tests_raising_type" not in UNCERTAINTY_INTAKES

    @pytest.mark.parametrize(
        "hook", ["artifact_type", "estimand", "excluded_types"]
    )
    def test_a_broken_hook_also_refuses_at_construction(self, hook):
        # The constructor reads three hooks for its early accident screen.
        # A raise there is not at the capital gate, but it should still name
        # the member rather than escape as a bare RuntimeError.
        member, artifact = _probe_member({hook: classmethod(_boom)})
        with pytest.raises(ValueError, match=hook):
            member(artifact, _attestation(producer=MEAN_PRODUCER))

    def test_no_other_module_level_state_the_screens_trust_is_mutable(self):
        # Sweep: everything else this module exports that a screen reads is
        # an immutable tuple. A rebinding of the module global itself needs
        # the same capability as the disclosed hostile-metaclass boundary.
        import dskit.pipeline.uncertainty_intake as module

        def immutable(value, where):
            # Recurse: `([],)` is a tuple whose inner list is freely
            # mutable, so checking only the outer container would have
            # made the exhaustiveness claim structurally incomplete.
            if isinstance(value, (type, types.FunctionType)):
                return
            assert isinstance(
                value, (tuple, frozenset, types.MappingProxyType, str, bytes, int, float, bool)
            ), f"{where} is mutable public state: {type(value).__name__}"
            if isinstance(value, (tuple, frozenset)):
                for index, item in enumerate(value):
                    immutable(item, f"{where}[{index}]")
            elif isinstance(value, types.MappingProxyType):
                for key, item in value.items():
                    immutable(item, f"{where}[{key!r}]")

        for name in module.__all__:
            immutable(getattr(module, name), name)
