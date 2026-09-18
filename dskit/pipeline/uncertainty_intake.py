"""``uncertainty_intake`` — the gate an uncertainty artifact passes to reach a decision.

Three sibling modules in this package PRODUCE uncertainty:
:mod:`~dskit.pipeline.mean_interval` brackets an expected effect,
:mod:`~dskit.pipeline.outcome_interval` calibrates a realized outcome, and
:mod:`~dskit.pipeline.false_signal` scores how probable it is that a
signal's apparent edge is nothing. None of them says whether the number in
your hand may be USED at the decision you are about to make. That question
is this module's whole subject, and it is a different one: an artifact can
be arithmetically perfect and still be the wrong artifact — calibrated on
evidence that stopped months ago, published after the decision it is being
fed to, produced by a different model, carrying no measured coverage at
all, or answering a different question than the one being asked.

Five refusals, one per way that goes wrong
------------------------------------------

``foreign_model``, ``post_decision``, ``stale``, ``uncalibrated``,
``unknown_producer`` and ``wrong_unit`` (:data:`REFUSAL_REASONS`). Each is
screened by :meth:`AttestedUncertainty.problems`, which is a TEMPLATE a
member can never override — ``__init_subclass__`` refuses the class at
definition time, the ``production/leg.py`` idiom the sibling doorways
already use. So is **every other name the doorway defines**
(:data:`AttestedUncertainty._FINAL_METHODS`): the constructor, both
accessors and each individual screen, because overriding any one of them
defeats a refusal just as surely as overriding the template that calls
them. A member supplies the hooks in
:data:`AttestedUncertainty._HOOKS` and nothing else, and a test asserts
those two tuples between them cover every callable the class defines, so
a new method cannot be added without being classified.

The question is a TYPE, never a string
--------------------------------------

``wrong_unit`` exists because "how uncertain is the expected effect" and
"how uncertain is the realized outcome" are different questions with
different answers, and a caller who conflates them silently sizes against
the wrong dispersion. So the estimand is not a label the caller writes
down: each member DECLARES the artifact class it accepts
(:meth:`AttestedUncertainty.artifact_type`) and refuses anything else, and
a consumer states what it needs by naming a CLASS. Relabelling is
therefore not a spelling change but a type error. The same device is what
:class:`~dskit.pipeline.mean_interval.ConfidenceInterval` versus
:class:`~dskit.pipeline.mean_interval.WidenedInterval` already does one
module over, and it is why :class:`AttestedMeanConfidence` accepts only
the claim-bearing ``ConfidenceInterval`` — a ``WidenedInterval`` is a
sensitivity reading and is refused here by its own type.

Why there is a ``ProbabilityUpperBound`` family with nothing in it
-----------------------------------------------------------------

A chance constraint of the form ``sum_i(x_i * pi_i) <= q * sum_i(x_i)``
needs ``pi_i`` to be a genuine probability UPPER BOUND. ``false_signal``
ships ``pi_widened``, which is a widened POINT ESTIMATE: ADR-0152's
measurement puts its attainment of the true local fdr at 0.53–0.82
against a 0.95 nominal, and that module renamed the field rather than
repair a claim it could not make. :class:`ProbabilityUpperBound` is the
family such a bound would belong to, and it is **CLOSED**: it is listed in
:data:`CLOSED_FAMILIES`, so ``__init_subclass__`` refuses ANY subclass of
it at class-definition time, here or downstream. A consumer that demands
one therefore refuses every artifact that exists, and a project cannot
mint its own member to get past that. Adding a member is an edit to THIS
module plus an ADR carrying the measurement that earns the claim — a
reviewable act, not a four-line subclass in a caller.

(Round-1 review proved the first version of this family was not sealed:
four lines subclassing it with ``artifact_type() -> FalseSignalEstimate``
constructed, admitted, and handed ``pi_widened`` over as a bound. The
sealing below is that finding's correction.)

What this module does NOT do
----------------------------

**It is not a root of trust.** ADR-0122's Correction settles the point for
this repository: a Python resolver "cannot be a root of trust: Python has
already selected and started its interpreter, import machinery, bootstrap
modules, and possible import hooks before that resolver can run."
Everything here runs inside that interpreter and inherits exactly that
limit. What these screens DO is fail closed for an ordinary caller and for
the shipped configuration — an artifact of the wrong type, an unattested
one, a stale or foreign one, or one naming a producer this package does
not know is refused without the caller having to remember to check. What
they cannot do is stop code that already controls the interpreter.

**An admitted artifact is not evidence that a calibrated estimator
produced it.** The three artifact types are plain frozen dataclasses whose
``__post_init__`` invariants hold for every instance, so an admitted
artifact is internally consistent and nothing more; a hand-built one of
the right shape is indistinguishable from a fitted one. The producer
screen narrows that to "an object whose attestation and whose own
self-report agree on a producer this package has REGISTERED". That is a
real narrowing and it is not provenance: the registries are open by
design, the comparison is between strings, and nothing here re-runs an
estimator, imports the named module or verifies a signature. Real
provenance needs the out-of-Python launch root ADR-0122 describes.

**It measures nothing.** :class:`CoverageEvidence` RECORDS what a producer
attests about its own artifact — a target, a measured attainment, an
effective independent sample count and the identity of the experiment
that produced them — and this module compares those recorded numbers with
a consumer's declared floor. It cannot verify that the measurement
happened, that it was honest, or that it transfers to the decision being
made. Nothing here IS coverage evidence; it is the machinery that refuses
an artifact whose producer supplied none. In particular
``outcome_interval.OutcomeIntervalResult.realized_coverage`` is an
IN-SAMPLE consistency check by its own docstring, and is not the
out-of-sample attainment :class:`CoverageEvidence` records.

Every policy number is the consumer's and has no default here: how stale
is too stale, and how much measured coverage is enough, are risk choices
a toolkit is not entitled to pick. :class:`DecisionDemand` therefore
refuses to construct without them.

Import cost: stdlib plus this package's ``false_signal``, ``mean_interval``,
``outcome_interval`` and ``records``. Tier 1, no third-party dependency.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass

from .false_signal import FALSE_SIGNAL_ESTIMATORS, FalseSignalEstimate
from .mean_interval import (
    MEAN_INTERVAL_ESTIMATORS,
    ConfidenceInterval,
    WidenedInterval,
)
from .node import class_ref
from .outcome_interval import CALIBRATORS, OutcomeIntervalResult
from .records import number_ok

__all__ = [
    "CLOSED_FAMILIES",
    "REFUSAL_REASONS",
    "UNCERTAINTY_INTAKES",
    "AttestedFalseSignalRate",
    "AttestedMeanConfidence",
    "AttestedOutcomeBand",
    "AttestedUncertainty",
    "CoverageEvidence",
    "DecisionDemand",
    "ProbabilityUpperBound",
    "UncertaintyAttestation",
    "register_uncertainty_intake",
    "uncertainty_intake",
]

#: Every reason an artifact can be refused at a decision, as the code that
#: opens the refusal message. A consumer routes on these; a test enumerates
#: them. They are deliberately CLOSED: a sixth way to be unusable is a
#: sixth screen in :meth:`AttestedUncertainty.problems`, not a free-form
#: string a caller invents at the call site.
REFUSAL_REASONS = (
    "foreign_model",
    "post_decision",
    "stale",
    "uncalibrated",
    "unknown_producer",
    "wrong_unit",
)

#: Families nothing may join — ``__init_subclass__`` refuses any subclass
#: of one at class-definition time, in this package or downstream. A family
#: is closed when membership would ASSERT a guarantee no member can
#: currently earn, so leaving it open lets a caller mint the guarantee for
#: itself. Assigned below, once its members exist.
CLOSED_FAMILIES = ()

#: Registered intake members, ``name -> class``. Mirrors the sibling
#: registries (``register_false_signal_estimator``,
#: ``register_mean_interval_estimator``, ``register_calibrator``) and holds
#: CLASSES, because a member is an object with hooks. It is also what the
#: conflation screen reads: an artifact that satisfies TWO members' declared
#: types is ambiguous and is refused rather than silently assigned to one.
UNCERTAINTY_INTAKES = {}


def _check_stamp(value, name):
    """Refuse anything that is not an integer epoch-ms value >= 0."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be an integer epoch-ms value >= 0, got {value!r}")


def _check_text(value, name):
    """Refuse anything that is not a non-empty string."""
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be a non-empty string, got {value!r}")


def _check_open_unit(value, name):
    """Refuse anything that is not a finite number strictly inside (0, 1)."""
    if not number_ok(value) or not 0.0 < float(value) < 1.0:
        raise ValueError(f"{name} must be a finite number in (0, 1), got {value!r}")


@dataclass(frozen=True)
class CoverageEvidence:
    """What a producer ATTESTS it measured about its own artifact.

    This is a record, not a measurement. Nothing in this package computes
    ``measured``, and nothing can check that the experiment behind
    ``evidence_id`` was run, was honest, or transfers to the decision the
    artifact is about to inform. Its PRESENCE is what separates a
    calibrated artifact from an uncalibrated one; its VALUE is compared
    against a consumer's own declared floor
    (``DecisionDemand.min_measured_coverage``) and against nothing else.

    An artifact whose producer measured nothing carries ``None`` in place
    of this value — an honest record of an uncalibrated artifact, which
    :meth:`AttestedUncertainty.problems` then refuses. Fabricating an
    instance to get past that screen is not a defect this type can
    prevent; it is a lie, and it is recorded under ``evidence_id``.

    Parameters
    ----------
    target : float
        The coverage the producer aimed at, in ``(0, 1)``.
    measured : float
        The attainment the producer MEASURED, in ``[0, 1]``. It is not
        required to reach ``target``: an artifact that fell short is a
        real artifact, and refusing it is the consumer's floor's job.
    evidence_id : str
        Non-empty identity of the measurement — the run, report or digest
        a reader follows to find out what was actually done.
    n_units : int
        How many EFFECTIVELY INDEPENDENT units the measurement had, at
        least 1. Overlapping rows are not independent units; declaring
        this number is the producer's assertion and is recorded, never
        verified.

    Raises
    ------
    ValueError
        On a ``target`` outside ``(0, 1)``, a ``measured`` outside
        ``[0, 1]``, an empty ``evidence_id``, or ``n_units`` below 1.

    Examples
    --------
    One producer's attested out-of-sample measurement::

        ev = CoverageEvidence(
            target=0.95, measured=0.94, evidence_id="cal-2026-09-17", n_units=40
        )
        ev.measured
        # -> 0.94
    """

    target: float
    measured: float
    evidence_id: str
    n_units: int

    def __post_init__(self):
        """Refuse a record a consumer could mistake for a measurement it is not."""
        _check_open_unit(self.target, "target")
        if not number_ok(self.measured) or not 0.0 <= float(self.measured) <= 1.0:
            raise ValueError(
                f"measured must be a finite number in [0, 1], got {self.measured!r}"
            )
        _check_text(self.evidence_id, "evidence_id")
        if (
            isinstance(self.n_units, bool)
            or not isinstance(self.n_units, int)
            or self.n_units < 1
        ):
            raise ValueError(f"n_units must be an int >= 1, got {self.n_units!r}")
        object.__setattr__(self, "target", float(self.target))
        object.__setattr__(self, "measured", float(self.measured))


@dataclass(frozen=True)
class UncertaintyAttestation:
    """The provenance an uncertainty artifact must travel with.

    An artifact object carries its own arithmetic and says nothing about
    WHERE it came from or WHEN it became knowable. This value carries
    exactly that, supplied by whoever produced the artifact, so a consumer
    can screen it against the decision in front of it.

    Parameters
    ----------
    artifact_id : str
        Non-empty content identity of the artifact — what a consumer's own
        inputs pin, so a swapped artifact is detectable.
    model_identity : str
        Non-empty identity of the model or release this evidence is ABOUT.
        Uncertainty calibrated for one model is not evidence about another.
    calibration_end_ms : int
        Epoch ms of the last moment the calibration evidence covers. The
        freshness screen measures from here, not from when the file was
        written.
    known_at_ms : int
        Epoch ms at which this artifact became knowable. A decision may
        only consume what existed before it.
    producer : str
        Non-empty ``module:QualName`` of the estimator that produced the
        artifact, as :func:`~dskit.pipeline.node.class_ref` spells it. It
        is screened against the member's own registry and against the
        artifact's own self-report. It is a NAME, not a credential: see
        the module docstring on what that does and does not establish.
    coverage : CoverageEvidence or None
        The producer's attested measurement, or ``None`` when it measured
        nothing (the default). ``None`` is an honest record of an
        uncalibrated artifact, not a missing field.

    Raises
    ------
    ValueError
        On an empty ``artifact_id``, ``model_identity`` or ``producer``, a
        non-integer or negative stamp, a ``calibration_end_ms`` after
        ``known_at_ms``
        (evidence cannot cover time the artifact predates), or a
        ``coverage`` that is neither a :class:`CoverageEvidence` nor
        ``None``.

    Examples
    --------
    One attestation beside its artifact::

        att = UncertaintyAttestation(
            artifact_id="cal-abc",
            model_identity="release-1",
            calibration_end_ms=1_699_000_000_000,
            known_at_ms=1_699_500_000_000,
            producer="dskit.pipeline.outcome_interval:BlockConformalInterval",
            coverage=CoverageEvidence(0.95, 0.94, "cal-2026-09-17", 40),
        )
        att.model_identity
        # -> 'release-1'
    """

    artifact_id: str
    model_identity: str
    calibration_end_ms: int
    known_at_ms: int
    producer: str
    coverage: CoverageEvidence = None

    def __post_init__(self):
        """Refuse provenance that could not describe a real artifact."""
        _check_text(self.artifact_id, "artifact_id")
        _check_text(self.model_identity, "model_identity")
        _check_text(self.producer, "producer")
        _check_stamp(self.calibration_end_ms, "calibration_end_ms")
        _check_stamp(self.known_at_ms, "known_at_ms")
        if self.calibration_end_ms > self.known_at_ms:
            raise ValueError(
                f"calibration_end_ms {self.calibration_end_ms!r} is after known_at_ms "
                f"{self.known_at_ms!r} — evidence cannot cover time the artifact predates"
            )
        if self.coverage is not None and not isinstance(self.coverage, CoverageEvidence):
            raise ValueError(
                f"coverage must be a CoverageEvidence or None, got "
                f"{type(self.coverage).__name__} — a coverage claim is a value with an "
                "evidence identity, never a bare number"
            )


@dataclass(frozen=True)
class DecisionDemand:
    """What ONE decision requires of any uncertainty it consumes.

    Every field is the consumer's declared policy and none has a default.
    How stale is too stale, and how much measured coverage is enough, are
    risk choices; a default here would be this package quietly making them
    on a caller's behalf, which is the failure mode the whole module
    exists to stop.

    Parameters
    ----------
    decision_ts_ms : int
        The single epoch-ms instant the decision is taken at. Every
        admitted artifact is screened against THIS stamp, which is what
        makes "everything agrees on one decision timestamp" checkable.
    model_identity : str
        Non-empty identity of the model or release being decided about.
    max_calibration_age_ms : int
        How far ``decision_ts_ms`` may sit past an artifact's
        ``calibration_end_ms``, in ms, at least 0.
    min_measured_coverage : float
        The floor an artifact's attested ``measured`` coverage must reach,
        in ``(0, 1)``.

    Raises
    ------
    ValueError
        On a non-integer or negative stamp, an empty ``model_identity``,
        a negative ``max_calibration_age_ms``, or a
        ``min_measured_coverage`` outside ``(0, 1)``.

    Examples
    --------
    A decision that takes nothing older than a day or under 0.90 attained::

        demand = DecisionDemand(
            decision_ts_ms=1_700_000_000_000,
            model_identity="release-1",
            max_calibration_age_ms=86_400_000,
            min_measured_coverage=0.90,
        )
        demand.max_calibration_age_ms
        # -> 86400000
    """

    decision_ts_ms: int
    model_identity: str
    max_calibration_age_ms: int
    min_measured_coverage: float

    def __post_init__(self):
        """Refuse a demand that states no policy."""
        _check_stamp(self.decision_ts_ms, "decision_ts_ms")
        _check_text(self.model_identity, "model_identity")
        _check_stamp(self.max_calibration_age_ms, "max_calibration_age_ms")
        _check_open_unit(self.min_measured_coverage, "min_measured_coverage")
        object.__setattr__(
            self, "min_measured_coverage", float(self.min_measured_coverage)
        )


class AttestedUncertainty(ABC):
    """One uncertainty artifact bound to the provenance it travels with.

    The doorway. A member binds ONE artifact class to ONE estimand, and
    the binding is checked at construction: an artifact of the wrong class
    cannot be wrapped, so the estimand a consumer reads off the envelope's
    TYPE is always the question the artifact actually answers.
    :meth:`problems` and :meth:`admit` are TEMPLATE methods a member can
    never override — ``__init_subclass__`` refuses such a class at
    definition time.

    Parameters
    ----------
    artifact : object
        The uncertainty artifact, an instance of this member's
        :meth:`artifact_type` and of no OTHER registered member's.
    attestation : UncertaintyAttestation
        Where it came from and when it became knowable.

    Raises
    ------
    ValueError
        When ``attestation`` is not an :class:`UncertaintyAttestation`,
        when ``artifact`` is not an instance of :meth:`artifact_type`, or
        when it is ALSO an instance of another registered member's
        artifact type (an ambiguous artifact is refused, never assigned).

    Examples
    --------
    Wrap a calibrated outcome band, then admit it at one decision::

        env = AttestedOutcomeBand(band, UncertaintyAttestation(
            artifact_id="cal-abc", model_identity="release-1",
            calibration_end_ms=1_699_900_000_000,
            known_at_ms=1_699_900_000_000,
            coverage=CoverageEvidence(0.95, 0.94, "cal-2026-09-17", 40),
        ))
        env.admit(demand, AttestedOutcomeBand) is env
        # -> True
    """

    #: Every name a member may NOT replace. Round-1 review sealed only
    #: ``problems``/``admit``, which left each individual screen, both
    #: accessors and the constructor overridable — and overriding any one of
    #: them defeats a refusal just as surely. The list is read off
    #: ``AttestedUncertainty`` explicitly, never off ``cls``, so a member
    #: cannot shrink it; ``test_uncertainty_intake`` asserts it and
    #: :data:`_HOOKS` between them cover every callable this class defines,
    #: so a new method cannot be added without being classified.
    _FINAL_METHODS = (
        "__init__",
        "__init_subclass__",
        "_coverage_problems",
        "_identity_problems",
        "_producer_problems",
        "_question_problems",
        "_timing_problems",
        "admit",
        "artifact",
        "attestation",
        "problems",
    )

    #: The names a member MUST supply. Every one is ``@abstractmethod``, so
    #: an incomplete member refuses at construction rather than later.
    _HOOKS = (
        "artifact_producer",
        "artifact_type",
        "estimand",
        "excluded_types",
        "registered_producers",
    )

    def __init_subclass__(cls, **kwargs):
        """Refuse, at class-definition time, a member that overrides a seal or joins a closed family."""
        super().__init_subclass__(**kwargs)
        for name in AttestedUncertainty._FINAL_METHODS:
            if name in cls.__dict__:
                raise TypeError(
                    f"{cls.__name__} may not override {name} — it is part of the screen "
                    "every member is admitted through, and a member that replaces it can "
                    "skip the refusals this doorway exists to make"
                )
        for family in globals().get("CLOSED_FAMILIES", ()):
            if cls is not family and family in cls.__mro__:
                raise TypeError(
                    f"{cls.__name__} may not join {family.__name__}: that family is CLOSED "
                    "because membership ASSERTS a guarantee no member has earned. Joining it "
                    "is an edit to dskit/pipeline/uncertainty_intake.py plus an ADR carrying "
                    "the measurement, never a subclass in a caller"
                )

    def __init__(self, artifact, attestation):
        if not isinstance(attestation, UncertaintyAttestation):
            raise ValueError(
                f"{type(self).__name__}: attestation must be an UncertaintyAttestation, "
                f"got {type(attestation).__name__}"
            )
        wanted = type(self).artifact_type()
        if not isinstance(artifact, wanted):
            raise ValueError(
                f"{type(self).__name__} answers {type(self).estimand()!r} and accepts only "
                f"{wanted.__name__}, got {type(artifact).__name__} — the estimand is the "
                "artifact's TYPE, not a label a caller supplies"
            )
        for excluded in type(self).excluded_types():
            if isinstance(artifact, excluded):
                raise ValueError(
                    f"{type(self).__name__}: {type(artifact).__name__} is ALSO a "
                    f"{excluded.__name__}, a claim this member excludes — a type that "
                    "carries two incompatible claims at once is refused, never read as "
                    "the stronger one"
                )
        # No same-artifact_type exemption. Round-1 review proved that
        # exemption skipped precisely the shape of the attack it was meant
        # to catch; registration now refuses a second member for one
        # artifact type, so two registered members can never share one.
        for name, other in sorted(UNCERTAINTY_INTAKES.items()):
            if other is type(self):
                continue
            if isinstance(artifact, other.artifact_type()):
                raise ValueError(
                    f"{type(self).__name__}: {type(artifact).__name__} is ALSO a "
                    f"{other.artifact_type().__name__}, which {name!r} claims — an "
                    "artifact that answers two questions at once is refused, never "
                    "assigned to one of them"
                )
        self._artifact = artifact
        self._attestation = attestation

    @property
    def artifact(self):
        """Give the wrapped artifact, whose type is this member's estimand."""
        return self._artifact

    @property
    def attestation(self):
        """Give the provenance this artifact travels with."""
        return self._attestation

    @classmethod
    @abstractmethod
    def artifact_type(cls):
        """Name the ONE artifact class this member accepts.

        Returns
        -------
        type
            The class an artifact must be an instance of. Declaring the
            claim-bearing subclass rather than its base is how a member
            refuses an artifact that only LOOKS like the right answer.
        """

    @classmethod
    @abstractmethod
    def estimand(cls):
        """Name the question this member's artifact answers.

        Returns
        -------
        str
            A stable identifier for messages and evidence records. It is
            NOT the discriminator — :meth:`artifact_type` is — so it can
            never be spoofed into admitting the wrong artifact.
        """

    @classmethod
    @abstractmethod
    def excluded_types(cls):
        """Name the claims an accepted artifact may NOT also carry.

        Abstract with no default, for the reason ``mean_interval``'s
        ``result_class`` is: a member that inherits "nothing is excluded"
        by silence has not thought about it. Multiple inheritance can
        produce a type that satisfies a claim-bearing class AND its
        weaker sibling at once, and reading such a type as the stronger
        claim is exactly the overclaim this package refuses.

        Returns
        -------
        tuple
            Classes an artifact must not be an instance of. Empty is a
            legitimate answer, and it is a STATEMENT, not a default.
        """

    @classmethod
    @abstractmethod
    def registered_producers(cls):
        """List the producer classes this package knows for this estimand.

        Returns
        -------
        tuple
            The classes currently registered in this estimand's own
            registry. Read fresh on every call, because a registry is
            open and a project may add to it — which is also why passing
            this screen means "a producer the package knows", never "this
            estimator ran".
        """

    @classmethod
    @abstractmethod
    def artifact_producer(cls, artifact):
        """Read the producer an artifact reports for ITSELF.

        Parameters
        ----------
        artifact : object
            An instance of :meth:`artifact_type`.

        Returns
        -------
        str or None
            The ``module:QualName`` the artifact records, or ``None``
            when it records none — which is refused, because an artifact
            that will not say what made it cannot be checked against an
            attestation that does.
        """

    def problems(self, demand, expected):
        """List every reason this artifact may not inform ``demand``, empty when none.

        Parameters
        ----------
        demand : DecisionDemand
            The decision this artifact is being consumed at.
        expected : type
            The :class:`AttestedUncertainty` subclass the consumer needs.
            Naming a class rather than a string is what makes
            ``wrong_unit`` a type question.

        Returns
        -------
        list of str
            One message per refusal, each opening with its
            :data:`REFUSAL_REASONS` code, so a caller's ``validate_inputs``
            can accumulate them beside its own instead of catching.

        Raises
        ------
        ValueError
            When ``demand`` is not a :class:`DecisionDemand` or
            ``expected`` is not an :class:`AttestedUncertainty` subclass —
            a caller error, refused rather than answered.
        """
        if not isinstance(demand, DecisionDemand):
            raise ValueError(
                f"demand must be a DecisionDemand, got {type(demand).__name__}"
            )
        if not (isinstance(expected, type) and issubclass(expected, AttestedUncertainty)):
            raise ValueError(
                f"expected must be an AttestedUncertainty subclass, got {expected!r}"
            )
        return (
            self._question_problems(expected)
            + self._identity_problems(demand)
            + self._timing_problems(demand)
            + self._coverage_problems(demand)
            + self._producer_problems()
        )

    def admit(self, demand, expected):
        """Return this envelope, or raise naming every reason it is refused.

        Parameters
        ----------
        demand : DecisionDemand
            The decision this artifact is being consumed at.
        expected : type
            The :class:`AttestedUncertainty` subclass the consumer needs.

        Returns
        -------
        AttestedUncertainty
            ``self``, so an admitted artifact can be used inline.

        Raises
        ------
        ValueError
            When :meth:`problems` is non-empty, with every reason joined.
        """
        problems = self.problems(demand, expected)
        if problems:
            raise ValueError(f"{type(self).__name__}: " + "; ".join(problems))
        return self

    def _question_problems(self, expected):
        """Screen wrong_unit: does this envelope answer the question asked."""
        if isinstance(self, expected):
            return []
        return [
            f"wrong_unit: {type(self).__name__} answers {type(self).estimand()!r}, but this "
            f"decision requires {expected.__name__} — uncertainty about an expected effect, "
            "about a realized outcome and about a false-signal rate are different questions "
            "with different answers"
        ]

    def _identity_problems(self, demand):
        """Screen foreign_model: is this evidence about the model being decided."""
        attested = self._attestation.model_identity
        if attested == demand.model_identity:
            return []
        return [
            f"foreign_model: calibrated for model {attested!r}, but this decision is about "
            f"{demand.model_identity!r} — uncertainty calibrated for one model is not "
            "evidence about another"
        ]

    def _timing_problems(self, demand):
        """Screen post_decision and stale, both against the decision stamp."""
        att = self._attestation
        out = []
        if att.known_at_ms > demand.decision_ts_ms:
            out.append(
                f"post_decision: known_at_ms {att.known_at_ms!r} is after decision_ts_ms "
                f"{demand.decision_ts_ms!r} — a decision may only consume what existed "
                "before it"
            )
        if att.calibration_end_ms > demand.decision_ts_ms:
            out.append(
                f"post_decision: calibration_end_ms {att.calibration_end_ms!r} is after "
                f"decision_ts_ms {demand.decision_ts_ms!r} — the calibration window reaches "
                "past the decision it is informing"
            )
            return out
        age = demand.decision_ts_ms - att.calibration_end_ms
        if age > demand.max_calibration_age_ms:
            out.append(
                f"stale: the calibration window ended {age} ms before the decision, past the "
                f"declared max_calibration_age_ms {demand.max_calibration_age_ms!r}"
            )
        return out

    def _producer_problems(self):
        """Screen unknown_producer: attestation and artifact agree on a producer we know."""
        attested = self._attestation.producer
        known = sorted(class_ref(cls) for cls in type(self).registered_producers())
        out = []
        if attested not in known:
            out.append(
                f"unknown_producer: the attestation names producer {attested!r}, which is "
                f"not a registered {type(self).estimand()!r} producer ({known}) — this "
                "compares strings against an open registry and imports nothing, so it "
                "establishes that the producer is one this package knows, never that it ran"
            )
        reported = type(self).artifact_producer(self._artifact)
        if reported is None:
            out.append(
                "unknown_producer: the artifact records no producer of its own, so nothing "
                "can be checked against the attestation's claim"
            )
        elif reported != attested:
            out.append(
                f"unknown_producer: the artifact records producer {reported!r} but the "
                f"attestation names {attested!r} — an artifact and its provenance must "
                "agree on what made it"
            )
        return out

    def _coverage_problems(self, demand):
        """Screen uncalibrated: attested coverage present, and above the floor."""
        coverage = self._attestation.coverage
        if coverage is None:
            return [
                "uncalibrated: the attestation carries no CoverageEvidence — an artifact "
                "whose producer measured nothing proves schema compliance, not coverage"
            ]
        if coverage.measured < demand.min_measured_coverage:
            return [
                f"uncalibrated: attested measured coverage {coverage.measured!r} (evidence "
                f"{coverage.evidence_id!r}, {coverage.n_units} independent units) is below "
                f"the declared min_measured_coverage {demand.min_measured_coverage!r}"
            ]
        return []


class ProbabilityUpperBound(AttestedUncertainty):
    """The family whose members attest a genuine probability UPPER BOUND.

    This family is **CLOSED**. It is listed in :data:`CLOSED_FAMILIES`, so
    ``AttestedUncertainty.__init_subclass__`` refuses ANY subclass of it —
    direct, sideways through multiple inheritance, or a grandchild — at
    class-definition time, in this package and downstream. There is
    therefore no member, and no caller can mint one.

    A chance constraint needs ``P(true rate <= reported rate) >= level``;
    :class:`~dskit.pipeline.false_signal.FalseSignalEstimate` ships
    ``pi_widened``, a widened point estimate whose measured attainment is
    0.53–0.82 against a 0.95 nominal (ADR-0152, which renamed the field
    rather than claim what it could not deliver). A consumer that needs a
    bound names this class and every artifact in the package is refused
    with ``wrong_unit``.

    **Why the seal, and what it is worth.** The first version of this
    class was an ordinary abstract family, and a round-1 review showed
    four lines re-creating the defect: a subclass declaring
    ``artifact_type() -> FalseSignalEstimate`` constructed, admitted and
    handed ``pi_widened`` over as a bound. Closing the family moves the
    act of claiming a bound from a caller's subclass to an edit of THIS
    file plus an ADR carrying the measurement that earns it — a reviewable
    act rather than a silent one. It does not, and cannot, stop code that
    already controls the interpreter: see the module docstring on why
    nothing here is a root of trust.

    Parameters
    ----------
    Inherited unchanged from :class:`AttestedUncertainty`.

    Examples
    --------
    What a consumer that needs a bound gets today::

        rate = AttestedFalseSignalRate(estimate, attestation)
        rate.problems(demand, ProbabilityUpperBound)[0][:10]
        # -> 'wrong_unit'
    """


#: Closed now that its one member exists. Read by ``__init_subclass__``
#: through ``globals()`` so the class can name a family defined after it.
CLOSED_FAMILIES = (ProbabilityUpperBound,)


class AttestedMeanConfidence(AttestedUncertainty):
    """Uncertainty about an EXPECTED EFFECT, from a measured-coverage member.

    Accepts only
    :class:`~dskit.pipeline.mean_interval.ConfidenceInterval` — the
    claim-bearing subclass whose level was measured to be delivered. A
    :class:`~dskit.pipeline.mean_interval.WidenedInterval` is a
    sensitivity reading by its own contract and is refused here by type,
    with no flag to flip.

    Parameters
    ----------
    Inherited unchanged from :class:`AttestedUncertainty`.

    Examples
    --------
    Wrap a cluster-bootstrap interval::

        env = AttestedMeanConfidence(confidence_interval, attestation)
        env.estimand()
        # -> 'mean_confidence'
    """

    @classmethod
    def artifact_type(cls):
        """Give :class:`~dskit.pipeline.mean_interval.ConfidenceInterval`.

        Returns
        -------
        type
            ``ConfidenceInterval``, never its ``MeanIntervalResult`` base:
            the base carries no claim, and admitting it would admit a
            widened reading too.
        """
        return ConfidenceInterval

    @classmethod
    def estimand(cls):
        """Give ``'mean_confidence'``.

        Returns
        -------
        str
            The estimand identifier for messages and evidence records.
        """
        return "mean_confidence"

    @classmethod
    def excluded_types(cls):
        """Give ``(WidenedInterval,)``.

        Returns
        -------
        tuple
            A diamond inheriting both claim-bearing subclasses satisfies
            ``isinstance`` for each; reading it as the measured one would
            be the overclaim. Refused at construction.
        """
        return (WidenedInterval,)

    @classmethod
    def registered_producers(cls):
        """Give the classes registered in ``MEAN_INTERVAL_ESTIMATORS``.

        Returns
        -------
        tuple
            Every currently registered mean-interval estimator class.
        """
        return tuple(entry["cls"] for entry in MEAN_INTERVAL_ESTIMATORS.values())

    @classmethod
    def artifact_producer(cls, artifact):
        """Give the interval's own ``method`` field.

        Parameters
        ----------
        artifact : dskit.pipeline.mean_interval.ConfidenceInterval
            The wrapped interval.

        Returns
        -------
        str or None
            ``artifact.method``, which the estimator template sets to its
            own ``class_ref``.
        """
        return getattr(artifact, "method", None)


class AttestedOutcomeBand(AttestedUncertainty):
    """Uncertainty about a REALIZED OUTCOME, from a block-conformal calibration.

    Accepts only
    :class:`~dskit.pipeline.outcome_interval.OutcomeIntervalResult`. That
    artifact's ``realized_coverage`` is an IN-SAMPLE consistency check by
    its own docstring and is NOT the attested out-of-sample number the
    :class:`CoverageEvidence` on the attestation carries; the two are
    different quantities and this envelope screens only the latter.

    Parameters
    ----------
    Inherited unchanged from :class:`AttestedUncertainty`.

    Examples
    --------
    Wrap a calibrated band::

        env = AttestedOutcomeBand(band, attestation)
        env.estimand()
        # -> 'realized_outcome'
    """

    @classmethod
    def artifact_type(cls):
        """Give :class:`~dskit.pipeline.outcome_interval.OutcomeIntervalResult`.

        Returns
        -------
        type
            ``OutcomeIntervalResult``.
        """
        return OutcomeIntervalResult

    @classmethod
    def estimand(cls):
        """Give ``'realized_outcome'``.

        Returns
        -------
        str
            The estimand identifier for messages and evidence records.
        """
        return "realized_outcome"

    @classmethod
    def excluded_types(cls):
        """Give ``()``.

        Returns
        -------
        tuple
            Empty, stated rather than inherited: ``OutcomeIntervalResult``
            has no claim-bearing sibling to be confused with.
        """
        return ()

    @classmethod
    def registered_producers(cls):
        """Give the classes registered in ``CALIBRATORS``.

        Returns
        -------
        tuple
            Every currently registered outcome calibrator class.
        """
        return tuple(entry["cls"] for entry in CALIBRATORS.values())

    @classmethod
    def artifact_producer(cls, artifact):
        """Give the band's own ``provenance["block_rule"]``.

        Parameters
        ----------
        artifact : dskit.pipeline.outcome_interval.OutcomeIntervalResult
            The wrapped band.

        Returns
        -------
        str or None
            The ``class_ref`` the calibrator template recorded, or
            ``None`` when the provenance carries none.
        """
        provenance = getattr(artifact, "provenance", None)
        if not isinstance(provenance, Mapping):
            return None
        value = provenance.get("block_rule")
        return value if isinstance(value, str) else None


class AttestedFalseSignalRate(AttestedUncertainty):
    """A per-signal false-signal rate: ``pi_hat`` and the widened reading.

    Accepts only
    :class:`~dskit.pipeline.false_signal.FalseSignalEstimate`. It is NOT a
    :class:`ProbabilityUpperBound`, and no class can be: that family is
    closed, so the route this member would have to take to become one does
    not exist. ``pi_widened`` is a widened point estimate, and a consumer
    that asks this envelope for a bound is refused with ``wrong_unit``.
    Reading ``pi_widened`` off the wrapped artifact as a SENSITIVITY
    number stays available and honest; calling it a bound does not.

    Parameters
    ----------
    Inherited unchanged from :class:`AttestedUncertainty`.

    Examples
    --------
    Wrap an estimate; it is not in the bound-bearing family::

        env = AttestedFalseSignalRate(estimate, attestation)
        isinstance(env, ProbabilityUpperBound)
        # -> False
    """

    @classmethod
    def artifact_type(cls):
        """Give :class:`~dskit.pipeline.false_signal.FalseSignalEstimate`.

        Returns
        -------
        type
            ``FalseSignalEstimate``.
        """
        return FalseSignalEstimate

    @classmethod
    def estimand(cls):
        """Give ``'false_signal_rate'``.

        Returns
        -------
        str
            The estimand identifier for messages and evidence records.
        """
        return "false_signal_rate"

    @classmethod
    def excluded_types(cls):
        """Give ``()``.

        Returns
        -------
        tuple
            Empty, stated rather than inherited: ``FalseSignalEstimate``
            has no claim-bearing sibling. What this member must never be
            is a :class:`ProbabilityUpperBound`, and that is enforced by
            the family being closed, not by this tuple.
        """
        return ()

    @classmethod
    def registered_producers(cls):
        """Give the classes registered in ``FALSE_SIGNAL_ESTIMATORS``.

        Returns
        -------
        tuple
            Every currently registered false-signal estimator class.
        """
        return tuple(entry["cls"] for entry in FALSE_SIGNAL_ESTIMATORS.values())

    @classmethod
    def artifact_producer(cls, artifact):
        """Give the estimate's own ``evidence["estimator"]``.

        Parameters
        ----------
        artifact : dskit.pipeline.false_signal.FalseSignalEstimate
            The wrapped estimate.

        Returns
        -------
        str or None
            The ``class_ref`` the estimator template recorded, or
            ``None`` when the evidence carries none.
        """
        evidence = getattr(artifact, "evidence", None)
        if not isinstance(evidence, Mapping):
            return None
        value = evidence.get("estimator")
        return value if isinstance(value, str) else None


def register_uncertainty_intake(name, cls, doc=""):
    """Register one intake member under ``name``.

    Parameters
    ----------
    name : str
        Non-empty registry key, unique unless it already maps to ``cls``.
    cls : type
        A concrete :class:`AttestedUncertainty` subclass.
    doc : str, optional
        One line for a reader of the registry (default ``""``).

    Returns
    -------
    None
        Registration is a side effect on :data:`UNCERTAINTY_INTAKES`.

    Raises
    ------
    ValueError
        On an empty ``name``, a ``cls`` that is not an
        :class:`AttestedUncertainty` subclass, a name already bound to a
        DIFFERENT class, or an ``artifact_type`` another member already
        claims.

    Examples
    --------
    Register a project's own member::

        register_uncertainty_intake("my_band", MyBand, doc="a project's own band")
        UNCERTAINTY_INTAKES["my_band"] is MyBand
        # -> True
    """
    _check_text(name, "name")
    if not (isinstance(cls, type) and issubclass(cls, AttestedUncertainty)):
        raise ValueError(f"{name!r} must be an AttestedUncertainty subclass, got {cls!r}")
    existing = UNCERTAINTY_INTAKES.get(name)
    if existing is not None and existing is not cls:
        raise ValueError(
            f"uncertainty intake {name!r} is already registered to {existing.__name__}"
        )
    wanted = cls.artifact_type()
    for other_name, other in sorted(UNCERTAINTY_INTAKES.items()):
        if other_name != name and other is not cls and other.artifact_type() is wanted:
            raise ValueError(
                f"{name!r} claims artifact type {wanted.__name__}, which {other_name!r} "
                "already claims — one artifact type answers one question, and two members "
                "claiming it would make every such artifact ambiguous"
            )
    UNCERTAINTY_INTAKES[name] = cls
    if doc:
        cls.__doc__ = cls.__doc__ or doc


def uncertainty_intake(name):
    """Look one intake member up by registry name.

    Parameters
    ----------
    name : str
        The registered key.

    Returns
    -------
    type
        The :class:`AttestedUncertainty` subclass registered there.

    Raises
    ------
    KeyError
        When nothing is registered under ``name``, naming what is.

    Examples
    --------
    Reach the shipped realized-outcome member::

        uncertainty_intake("outcome_band") is AttestedOutcomeBand
        # -> True
    """
    try:
        return UNCERTAINTY_INTAKES[name]
    except KeyError:
        raise KeyError(
            f"unknown uncertainty intake {name!r}; registered: "
            f"{sorted(UNCERTAINTY_INTAKES)}"
        ) from None


register_uncertainty_intake(
    "mean_confidence",
    AttestedMeanConfidence,
    doc="uncertainty about an expected effect, from a measured-coverage interval",
)
register_uncertainty_intake(
    "outcome_band",
    AttestedOutcomeBand,
    doc="uncertainty about a realized outcome, from a block-conformal calibration",
)
register_uncertainty_intake(
    "false_signal_rate",
    AttestedFalseSignalRate,
    doc="a per-signal false-signal rate; NOT a probability upper bound",
)
