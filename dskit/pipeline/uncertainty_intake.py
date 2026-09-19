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
already use, for the constructor, both accessors and the two templates
(:data:`AttestedUncertainty._FINAL_METHODS`). **That sealing is
accident-and-drift protection and is not a boundary** — see below. The
screens themselves are no longer methods at all: they are module-level
functions taking explicit values, so there is nothing on the class for a
member to override. A member supplies the hooks in
:data:`AttestedUncertainty._HOOKS` and nothing else, and a test asserts
those two tuples between them cover every callable the class defines, so
a new method cannot be added without being classified.

**The rule is a function, not a method.** :func:`admission_problems` and
:func:`admit_uncertainty` own it, and a consumer that does not trust the
envelope's class — a consumer sizing capital should not — calls them. A
method is resolved through the instance's own class, which is precisely
the thing in question; the function is resolved through this module. The
2026-09-18 convergence checkpoint required this change after two rounds of
sealing patches each found new bypasses.

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
family such a bound would belong to, and **the registry does not name
it**. That is the load-bearing statement, and it is answered from
:data:`UNCERTAINTY_INTAKES` rather than from any class's claim about
itself. :func:`admission_problems` asks TWO registry questions, in order:
is ``expected`` itself a registered intake — which this family is not, so
the demand is refused there and then, before any authority is chosen or
any artifact is read — and only then, which registered intakes have
``expected`` in their real ``__mro__``. The first question is what makes a
``__bases__`` rebinding or an ``ABCMeta.register`` irrelevant; neither
puts a class into the registry. Adding a member means editing THIS module
and registering it through :func:`register_uncertainty_intake`, plus an
ADR carrying the measurement that earns the claim.

``CLOSED_FAMILIES`` and ``__init_subclass__`` still refuse an ordinary
subclass, and that is worth having — but it is accident-and-drift
protection, NOT a boundary. Two review rounds proved it: round 1 found
four lines subclassing the family, and round 2 found ``ABCMeta.register``
flipping ``isinstance`` without creating a class at all, plus a subclass
loosening the two hooks that must stay overridable. Sealing could not
close that set, which is why the RULE moved out of the class.

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

Concretely, and recorded because review found each one: a hostile
metaclass can pop the sealed names out of a namespace and reattach them
after ``type.__new__``, so a class's ``problems()`` METHOD can be made to
return ``[]`` — :func:`admission_problems` still refuses that envelope,
and the test that proves it also asserts the method is defeated rather
than pretending otherwise. A caller can simply not call this module. A
project can register its own intake and its own producer, which is the
registry's purpose. None of that is repaired by another screen; it is the
boundary, and it is stated rather than sealed against.

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
from types import MappingProxyType

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
    "admission_problems",
    "admit_uncertainty",
    "artifact_of",
    "attestation_of",
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

#: Families no ordinary subclass may join — ``__init_subclass__`` refuses
#: one at class-definition time. A family is closed when membership would
#: ASSERT a guarantee no member has earned. **This is drift protection, not
#: a boundary**: ``ABCMeta.register`` forges ``isinstance`` without creating
#: a class, and a hostile metaclass escapes the hook entirely, so what
#: actually answers "may this artifact inform this demand" is
#: :func:`admission_problems` reading :data:`UNCERTAINTY_INTAKES`. Assigned
#: below, once its members exist.
CLOSED_FAMILIES = ()

def _sealed_registry():
    """Build the registry: its store, its read-only view, its one writer.

    The store is a LOCAL of this function. No module-level name holds it,
    so there is no private dict to import — round-5 review showed that a
    single-underscore module attribute is one underscore wide, and that a
    rebinding through it repointed a real capital-sizing estimand while
    every test in the sealing class still passed. The validation therefore
    lives INSIDE the closure rather than beside it, so a write through the
    front door is screened by construction.

    **The honest ceiling, stated as a rule and not as a list.** Any route
    that reaches a live Python object reaches this store. Closure cells
    (``<writer>.__closure__[0].cell_contents``) and garbage-collector
    referents (``gc.get_referents(UNCERTAINTY_INTAKES)[0]``, which hands
    back the proxied dict itself and needs no closure at all) are two, the
    tests execute both, and that is not a complete list. Round-6 review
    found the second one precisely because the previous wording named the
    first as *the* remaining route, and naming one route invites the
    reading that the others are closed. They are not: this is the same
    capability as the hostile metaclass this module already declines to
    defend against, and no enumeration closes an open set.

    What IS true, and is the whole of the claim: no importable name offers
    an unvalidated write, and no ordinary attribute access on the view
    does either.

    Returns
    -------
    tuple
        ``(view, register)`` — a ``MappingProxyType`` over the store, and
        the validating writer :func:`register_uncertainty_intake` wraps.

    Examples
    --------
    Built once, at import::

        view, register = _sealed_registry()
        type(view).__name__
        # -> 'mappingproxy'
    """
    store = {}

    def register(name, cls, doc=""):
        """Validate one intake, record it, and give back its undo."""
        _check_text(name, "name")
        if not (
            isinstance(cls, type) and AttestedUncertainty in getattr(cls, "__mro__", ())
        ):
            raise ValueError(
                f"{name!r} must be an AttestedUncertainty subclass, got {cls!r}"
            )
        if getattr(cls, "__abstractmethods__", ()):
            raise ValueError(
                f"{name!r} may not register {cls.__name__}: it is abstract "
                f"({sorted(cls.__abstractmethods__)} unimplemented), and every "
                "registered intake is asked for the declarations the screens run on"
            )
        existing = store.get(name)
        if existing is not None and existing is not cls:
            raise ValueError(
                f"uncertainty intake {name!r} is already registered to "
                f"{existing.__name__}"
            )
        wanted, refusal = _ask(cls, "artifact_type")
        if refusal:
            raise ValueError(f"{name!r} may not register {cls.__name__}: {refusal[0]}")
        for other_name in sorted(store):
            other = store[other_name]
            if other_name == name or other is cls:
                continue
            other_wanted, other_refusal = _ask(other, "artifact_type")
            if other_refusal or not isinstance(other_wanted, type):
                raise ValueError(
                    f"{name!r} may not register {cls.__name__} while {other_name!r} "
                    f"is registered and its own artifact_type() cannot be read "
                    f"({other_refusal[0] if other_refusal else other_wanted!r} is "
                    "not a type) — uniqueness cannot be shown against a member "
                    "that will not say what it claims, and an unreadable peer is "
                    "a conflict, never a clearance"
                )
            if other_wanted is wanted:
                raise ValueError(
                    f"{name!r} claims artifact type "
                    f"{getattr(wanted, '__name__', wanted)}, which {other_name!r} "
                    "already claims — one artifact type answers one question, and two "
                    "members claiming it would make every such artifact ambiguous"
                )
        store[name] = cls
        if doc:
            cls.__doc__ = cls.__doc__ or doc

        def forget():
            """Undo THIS registration, if it is still the one in place."""
            if store.get(name) is cls:
                del store[name]

        return forget

    return MappingProxyType(store), register


#: The registry, read-only. Every screen reads it, so it is the one piece of
#: state a refusal depends on, and it is written ONLY through
#: :func:`register_uncertainty_intake`: the store is a closure local, not a
#: module attribute, so no importable name offers an unvalidated write.
#: Object introspection of any kind still reaches it — see
#: :func:`_sealed_registry` for the rule and the two routes the tests
#: execute — and that is disclosed rather than denied.
#:
#: It holds CLASSES, because a member is an object with hooks, and it holds
#: LIVE references rather than a snapshot: editing a registered class's
#: hooks after registration changes what the screens read immediately.
#: Closing that needs the same capability as the ceiling above, so instead
#: every hook answer is treated as untrusted at the point of use — see
#: :func:`_ask`.
UNCERTAINTY_INTAKES, _register_intake = _sealed_registry()


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
            producer="dskit.pipeline.outcome_interval:BlockConformalInterval",
            coverage=CoverageEvidence(0.95, 0.94, "cal-2026-09-17", 40),
        ))
        admit_uncertainty(env, demand, AttestedOutcomeBand) is env
        # -> True
    """

    #: Every name a member may NOT replace. **This is accident-and-drift
    #: protection, not a boundary** — see the module docstring. The five
    #: screens are no longer methods at all: they are module-level
    #: functions taking explicit values, so there is nothing on the class
    #: for a member to override in the first place. The list is read off
    #: ``AttestedUncertainty`` explicitly, never off ``cls``, so a member
    #: cannot shrink it; ``test_uncertainty_intake`` asserts it and
    #: :data:`_HOOKS` between them cover every callable this class defines,
    #: so a new method cannot be added without being classified.
    _FINAL_METHODS = (
        "__init__",
        "__init_subclass__",
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
        # These screens read the CLASS's own declarations, deliberately:
        # they are an early, convenient refusal for an ordinary caller, not
        # the rule. `admission_problems` re-checks everything from the
        # registry at use time and never consults `type(envelope)`'s
        # declarations, so nothing downstream depends on a class being
        # honest here. A sweep of this module (round 4) confirms
        # `type(envelope)` is used at use time for IDENTITY and for message
        # text only.
        if not isinstance(attestation, UncertaintyAttestation):
            raise ValueError(
                f"{type(self).__name__}: attestation must be an UncertaintyAttestation, "
                f"got {type(attestation).__name__}"
            )
        wanted, refusal = _ask(type(self), "artifact_type")
        if refusal:
            raise ValueError(f"{type(self).__name__}: {refusal[0]}")
        if not isinstance(wanted, type):
            raise ValueError(
                f"{type(self).__name__}.artifact_type() returned {wanted!r}, "
                "which is not a type"
            )
        estimand, refusal = _ask(type(self), "estimand")
        if refusal:
            raise ValueError(f"{type(self).__name__}: {refusal[0]}")
        if not isinstance(artifact, wanted):
            raise ValueError(
                f"{type(self).__name__} answers {estimand!r} and accepts only "
                f"{wanted.__name__}, got {type(artifact).__name__} — the estimand is the "
                "artifact's TYPE, not a label a caller supplies"
            )
        excluded_types, refusal = _ask(type(self), "excluded_types")
        if refusal:
            raise ValueError(f"{type(self).__name__}: {refusal[0]}")
        if not isinstance(excluded_types, tuple):
            raise ValueError(
                f"{type(self).__name__}.excluded_types() returned "
                f"{excluded_types!r}, which is not a tuple of types"
            )
        for excluded in excluded_types:
            if isinstance(artifact, excluded):
                raise ValueError(
                    f"{type(self).__name__}: {type(artifact).__name__} is ALSO a "
                    f"{excluded.__name__}, a claim this member excludes — a type that "
                    "carries two incompatible claims at once is refused, never read as "
                    "the stronger one"
                )
        # No same-artifact_type exemption. Round-1 review proved that
        # exemption skipped precisely the shape of the attack it was meant
        # to catch — and round 2 found the deletion had no regression
        # cover, so ``test_the_same_artifact_type_exemption_stays_deleted``
        # now fails if it comes back. The effect is that an UNREGISTERED
        # class sharing a registered member's artifact type cannot wrap
        # that artifact at all, which is the same rule
        # ``admission_problems`` applies at use time, applied early.
        for name, other in sorted(UNCERTAINTY_INTAKES.items()):
            if other is type(self):
                continue
            other_wanted, refusal = _ask(other, "artifact_type")
            if refusal or not isinstance(other_wanted, type):
                raise ValueError(
                    f"{type(self).__name__}: {name!r} is a registered intake whose "
                    f"own artifact_type() cannot be read "
                    f"({refusal[0] if refusal else other_wanted!r} is not a type), "
                    "so this artifact cannot be shown to answer only one question "
                    "— an unreadable peer is a conflict, never a clearance"
                )
            if isinstance(artifact, other_wanted):
                raise ValueError(
                    f"{type(self).__name__}: {type(artifact).__name__} is ALSO a "
                    f"{other_wanted.__name__}, which {name!r} claims — an "
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

        A convenience spelling of :func:`admission_problems`, which owns the
        rule. A consumer that does not trust the envelope's class — and a
        consumer sizing capital should not — calls the FUNCTION, because a
        method is resolved through the instance's own class and that class
        is the thing under suspicion. The function reads the registry, the
        consumer's own ``expected``, and the envelope's raw state.

        Parameters
        ----------
        demand : DecisionDemand
            The decision this artifact is being consumed at.
        expected : type
            The :class:`AttestedUncertainty` subclass the consumer needs.

        Returns
        -------
        list of str
            One message per refusal, each opening with its
            :data:`REFUSAL_REASONS` code.

        Raises
        ------
        ValueError
            When ``demand`` is not a :class:`DecisionDemand` or
            ``expected`` is not an :class:`AttestedUncertainty` subclass.
        """
        return admission_problems(self, demand, expected)

    def admit(self, demand, expected):
        """Return this envelope, or raise naming every reason it is refused.

        A convenience spelling of :func:`admit_uncertainty`; see
        :meth:`problems` on why a consumer calls the function instead.

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
            When :func:`admission_problems` is non-empty, reasons joined.
        """
        return admit_uncertainty(self, demand, expected)


def _raw(envelope, name):
    """Read an envelope's own slot past any redefined descriptor."""
    try:
        return object.__getattribute__(envelope, name)
    except AttributeError:
        return None


def _registered_classes():
    """List the registered intake classes, in registry-name order."""
    return [UNCERTAINTY_INTAKES[name] for name in sorted(UNCERTAINTY_INTAKES)]


def _answering(expected):
    """List the registered intakes that answer ``expected``, by real inheritance."""
    # ``expected in cls.__mro__``, never ``issubclass``: ``ABCMeta.register``
    # makes ``issubclass``/``isinstance`` say yes without creating a class,
    # so a virtual registration would otherwise forge family membership.
    return [cls for cls in _registered_classes() if expected in cls.__mro__]


def _identity_problems(attestation, demand):
    """Screen foreign_model: is this evidence about the model being decided."""
    attested = attestation.model_identity
    if attested == demand.model_identity:
        return []
    return [
        f"foreign_model: calibrated for model {attested!r}, but this decision is about "
        f"{demand.model_identity!r} — uncertainty calibrated for one model is not "
        "evidence about another"
    ]


def _timing_problems(attestation, demand):
    """Screen post_decision and stale, both against the decision stamp."""
    out = []
    if attestation.known_at_ms > demand.decision_ts_ms:
        out.append(
            f"post_decision: known_at_ms {attestation.known_at_ms!r} is after "
            f"decision_ts_ms {demand.decision_ts_ms!r} — a decision may only consume "
            "what existed before it"
        )
    if attestation.calibration_end_ms > demand.decision_ts_ms:
        out.append(
            f"post_decision: calibration_end_ms {attestation.calibration_end_ms!r} is "
            f"after decision_ts_ms {demand.decision_ts_ms!r} — the calibration window "
            "reaches past the decision it is informing"
        )
        return out
    age = demand.decision_ts_ms - attestation.calibration_end_ms
    if age > demand.max_calibration_age_ms:
        out.append(
            f"stale: the calibration window ended {age} ms before the decision, past the "
            f"declared max_calibration_age_ms {demand.max_calibration_age_ms!r}"
        )
    return out


def _coverage_problems(attestation, demand):
    """Screen uncalibrated: attested coverage present, and above the floor."""
    coverage = attestation.coverage
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


def _producer_problems(authority, declarations, artifact, attestation):
    """Screen unknown_producer: attestation and artifact agree on a producer we know."""
    attested = attestation.producer
    known = sorted(class_ref(cls) for cls in declarations["producers"])
    out = []
    if attested not in known:
        out.append(
            f"unknown_producer: the attestation names producer {attested!r}, which is "
            f"not a registered {declarations['estimand']!r} producer ({known}) — this "
            "compares strings against an open registry and imports nothing, so it "
            "establishes that the producer is one this package knows, never that it ran"
        )
    # `known` is read from a SIBLING module's registry (false_signal,
    # mean_interval, outcome_interval). Those are open by their own ADRs'
    # design and are not this module's to seal: anyone who can register a
    # producer there can make it "known" here. That is the same honesty the
    # message below carries — "an open registry" — and it is the reason the
    # screen claims acquaintance, never provenance.
    reported, refusal = _ask(authority, "artifact_producer", artifact)
    if refusal:
        return out + refusal
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


def _question_problems(envelope, expected, answering):
    """Screen wrong_unit: both the demand and the envelope must be registered intakes."""
    if not any(cls is expected for cls in _registered_classes()):
        # THE DEMAND ITSELF must name a registered intake. Round-3 review
        # showed why: ``AttestedFalseSignalRate.__bases__ = (
        # ProbabilityUpperBound,)`` puts the closed family into a REAL
        # ``__mro__`` — ``__init_subclass__`` runs only inside
        # ``type.__new__`` and never sees a rebinding — and the old code
        # then fell back to ``authority = type(envelope)``, letting the
        # subverted class adjudicate itself and hand ``pi_widened`` over as
        # a bound. There is no fallback now: if the registry does not name
        # the demand, the answer is refuse.
        return [
            f"wrong_unit: {expected.__name__} is not a registered intake, so no "
            f"demand for it can be satisfied (registered: "
            f"{[cls.__name__ for cls in _registered_classes()]}) — the registry "
            "names what may be demanded as well as what may answer, and nothing "
            "registers a family whose guarantee no member has earned"
        ]
    if any(cls is type(envelope) for cls in answering):
        return []
    mine = next(
        (cls for cls in _registered_classes() if cls is type(envelope)), None
    )
    if mine is not None:
        estimand, refusal = _ask(mine, "estimand")
        if refusal:
            return refusal
        return [
            f"wrong_unit: {mine.__name__} answers {estimand!r}, but this "
            f"decision requires {expected.__name__} — uncertainty about an expected "
            "effect, about a realized outcome and about a false-signal rate are "
            "different questions with different answers"
        ]
    return [
        f"wrong_unit: {type(envelope).__name__} is not a registered intake for "
        f"{expected.__name__} (registered: {[cls.__name__ for cls in answering]}) — "
        "an unregistered class's declaration about which question it answers is "
        "not read, because that declaration is the thing in question"
    ]


def _ask(authority, hook, *args):
    """Call one hook of an intake, turning a RAISE into a coded refusal.

    Every hook is supplied by whoever registered the member, through the
    sanctioned extension point, and an ordinary coding bug in one — an
    ``evidence["estimator"]`` where the shipped members write ``.get`` —
    used to propagate out of :func:`admission_problems`, through
    ``nodes_capital.validate_inputs`` and ``driver.run_node``, killing the
    whole run instead of producing an itemized refusal. An unhandled
    exception at a risk gate is worse than a refusal, so **every** hook
    call on the use-time path goes through here.

    Parameters
    ----------
    authority : type
        The intake whose hook is being called.
    hook : str
        The hook's name.
    *args
        Arguments for it.

    Returns
    -------
    tuple
        ``(value, problems)``. On success ``problems`` is empty; on a raise
        it holds one ``wrong_unit`` message and ``value`` is ``None``.

    Examples
    --------
    Read a member's estimand without trusting it not to explode::

        estimand, problems = _ask(AttestedOutcomeBand, "estimand")
        # -> ('realized_outcome', [])
    """
    try:
        return getattr(authority, hook)(*args), []
    except Exception as exc:  # noqa: BLE001 — any raise is the member's bug
        return None, [
            f"wrong_unit: {getattr(authority, '__name__', authority)}.{hook}() raised "
            f"{type(exc).__name__}: {exc} — an intake whose own declarations cannot be "
            "read refuses everything, because an unhandled exception at a risk gate is "
            "worse than a refusal"
        ]


def _declarations(authority):
    """Read and screen all four class-level hooks ONCE, guarded.

    The fifth hook, ``artifact_producer``, takes the artifact and is read
    the same way at its own call site.

    Parameters
    ----------
    authority : type
        The registered intake the demand named.

    Returns
    -------
    tuple
        ``(declarations, problems)``. ``declarations`` maps ``estimand`` /
        ``wanted`` / ``excluded`` / ``producers`` to the hooks' answers and
        is empty when ``problems`` is not.

    Examples
    --------
    What a shipped member declares::

        declarations, problems = _declarations(AttestedOutcomeBand)
        declarations["estimand"]
        # -> 'realized_outcome'
    """
    out = []
    values = {}
    for hook, key, ok, shape in (
        ("estimand", "estimand", lambda v: isinstance(v, str) and v, "a non-empty string"),
        ("artifact_type", "wanted", lambda v: isinstance(v, type), "a type"),
        (
            "excluded_types",
            "excluded",
            lambda v: isinstance(v, tuple) and all(isinstance(i, type) for i in v),
            "a tuple of types",
        ),
        (
            "registered_producers",
            "producers",
            lambda v: isinstance(v, tuple) and all(isinstance(i, type) for i in v),
            "a tuple of classes",
        ),
    ):
        value, refusal = _ask(authority, hook)
        if refusal:
            out.extend(refusal)
            continue
        if not ok(value):
            out.append(
                f"wrong_unit: {authority.__name__}.{hook}() returned {value!r}, which "
                f"is not {shape} — an intake that cannot say what it accepts refuses "
                "everything"
            )
            continue
        values[key] = value
    return ({} if out else values), out


def _artifact_problems(authority, declarations, artifact):
    """Screen wrong_unit on the LIVE artifact, against the declared types."""
    out = []
    wanted = declarations["wanted"]
    if not isinstance(artifact, wanted):
        out.append(
            f"wrong_unit: {authority.__name__} answers {declarations['estimand']!r} "
            f"over {wanted.__name__}, and this envelope carries a "
            f"{type(artifact).__name__} — re-read at use time, so an artifact swapped "
            "in after construction is caught here rather than trusted"
        )
    for excluded in declarations["excluded"]:
        if isinstance(artifact, excluded):
            out.append(
                f"wrong_unit: {type(artifact).__name__} is ALSO a {excluded.__name__}, "
                f"a claim {authority.__name__} excludes — a type carrying two "
                "incompatible claims is never read as the stronger one"
            )
    return out


def admission_problems(envelope, demand, expected):
    """List every reason ``envelope`` may not inform ``demand`` as ``expected``.

    **This function, not the method of the same name, is the rule.** It is
    the correction the 2026-09-18 convergence checkpoint required: three of
    the four bypasses found in review shared one root — the envelope trusted
    an identity established at ``__init__`` and never re-checked — and two
    of them could not be closed by sealing, because
    :meth:`AttestedUncertainty.artifact_type` and
    :meth:`~AttestedUncertainty.excluded_types` are hooks and must stay
    overridable. So nothing here asks the envelope's class what it is:

    * ``expected`` itself must be a REGISTERED intake. A demand the
      registry does not name is refused outright, with no fallback to the
      envelope's own class — round-3 review reassigned
      ``AttestedFalseSignalRate.__bases__`` to put the zero-member closed
      family into a real ``__mro__`` (``__init_subclass__`` runs only
      inside ``type.__new__`` and never sees a rebinding) and the old
      fallback then let the subverted class adjudicate itself;
    * which classes may answer ``expected`` comes from
      :data:`UNCERTAINTY_INTAKES`, tested by real inheritance
      (``expected in cls.__mro__``), which ``ABCMeta.register`` cannot forge;
    * the envelope must BE one of those classes, by identity;
    * every screen then runs against the DEMANDED class's declarations,
      never the envelope's;
    * the artifact and attestation are re-read from the instance at every
      call and re-checked against the types the ANSWERING class declares,
      so a value swapped in after construction is refused on the next call;
    * being a function rather than a method, it is not resolved through the
      class under suspicion.

    What it does NOT do is survive code that controls the interpreter: a
    hostile metaclass can still reattach a method, and a caller can call the
    method instead of this function. See the module docstring — these
    screens fail closed for ordinary callers and for the shipped
    configuration, and they are not a root of trust.

    Parameters
    ----------
    envelope : AttestedUncertainty
        The artifact-plus-attestation being offered.
    demand : DecisionDemand
        The decision it is being consumed at.
    expected : type
        The :class:`AttestedUncertainty` subclass the consumer requires.

    Returns
    -------
    list of str
        One message per refusal, each opening with its
        :data:`REFUSAL_REASONS` code, so a caller's ``validate_inputs`` can
        accumulate them beside its own rather than catching.

    Raises
    ------
    ValueError
        When ``demand`` is not a :class:`DecisionDemand`, ``expected`` is
        not an :class:`AttestedUncertainty` subclass, or ``envelope`` is not
        an :class:`AttestedUncertainty` — caller errors, refused rather than
        answered.

    Examples
    --------
    What a consumer that does not trust the envelope calls::

        admission_problems(envelope, demand, AttestedOutcomeBand)
        # -> []
    """
    if not isinstance(demand, DecisionDemand):
        raise ValueError(f"demand must be a DecisionDemand, got {type(demand).__name__}")
    if not (isinstance(expected, type) and AttestedUncertainty in expected.__mro__):
        raise ValueError(
            f"expected must be an AttestedUncertainty subclass, got {expected!r}"
        )
    if AttestedUncertainty not in type(envelope).__mro__:
        raise ValueError(
            f"envelope must be an AttestedUncertainty, got {type(envelope).__name__}"
        )
    answering = _answering(expected)
    problems = _question_problems(envelope, expected, answering)
    if problems:
        return problems
    # The authority is the DEMANDED class, always. It is a registered intake
    # (the screen above refused anything else) and it is the consumer's own
    # choice, so nothing here reads a declaration made by the envelope's
    # class about itself.
    authority = expected
    # Every hook answer is read ONCE, here, and treated as untrusted: a
    # registered intake whose declarations are unusable — or whose hooks
    # RAISE, which an ordinary coding bug in the sanctioned extension point
    # produces — is a coded refusal, never an exception escaping into
    # ``validate_inputs`` and ``driver.run_node``.
    declarations, problems = _declarations(authority)
    if problems:
        return problems
    artifact = _raw(envelope, "_artifact")
    attestation = _raw(envelope, "_attestation")
    if not isinstance(attestation, UncertaintyAttestation):
        return [
            "wrong_unit: the envelope carries no UncertaintyAttestation "
            f"(got {type(attestation).__name__}), so there is no provenance to screen"
        ]
    return (
        _artifact_problems(authority, declarations, artifact)
        + _identity_problems(attestation, demand)
        + _timing_problems(attestation, demand)
        + _coverage_problems(attestation, demand)
        + _producer_problems(authority, declarations, artifact, attestation)
    )


def artifact_of(envelope):
    """Give the artifact :func:`admission_problems` screened, not a property's answer.

    A consumer that has just admitted an envelope and now reads its
    artifact should read the SAME value the screens read. ``envelope
    .artifact`` goes through a descriptor, and a descriptor is part of the
    class — the thing a consumer had reason not to trust in the first
    place. This reads the instance slot directly.

    Parameters
    ----------
    envelope : AttestedUncertainty
        An envelope, normally one just returned by
        :func:`admit_uncertainty`.

    Returns
    -------
    object
        The wrapped artifact, or ``None`` when the instance carries none.

    Examples
    --------
    Read what was admitted::

        band = artifact_of(admit_uncertainty(env, demand, AttestedOutcomeBand))
        sorted(band.lower_offset)
        # -> ['alpha', 'beta']
    """
    return _raw(envelope, "_artifact")


def attestation_of(envelope):
    """Give the attestation :func:`admission_problems` screened.

    The :func:`artifact_of` argument applies unchanged: read the slot the
    screens read, not what a class-level descriptor chooses to return.

    Parameters
    ----------
    envelope : AttestedUncertainty
        An envelope, normally one just returned by
        :func:`admit_uncertainty`.

    Returns
    -------
    UncertaintyAttestation or None
        The provenance the envelope carries, or ``None`` when it has none.

    Examples
    --------
    Record what was admitted::

        attestation_of(env).artifact_id
        # -> 'cal-abc'
    """
    return _raw(envelope, "_attestation")


def admit_uncertainty(envelope, demand, expected):
    """Return ``envelope``, or raise naming every reason it is refused.

    Parameters
    ----------
    envelope : AttestedUncertainty
        The artifact-plus-attestation being offered.
    demand : DecisionDemand
        The decision it is being consumed at.
    expected : type
        The :class:`AttestedUncertainty` subclass the consumer requires.

    Returns
    -------
    AttestedUncertainty
        ``envelope``, so an admitted artifact can be used inline.

    Raises
    ------
    ValueError
        When :func:`admission_problems` is non-empty, reasons joined.

    Examples
    --------
    Admit one band at one decision::

        admit_uncertainty(envelope, demand, AttestedOutcomeBand) is envelope
        # -> True
    """
    problems = admission_problems(envelope, demand, expected)
    if problems:
        raise ValueError(f"{type(envelope).__name__}: " + "; ".join(problems))
    return envelope


class ProbabilityUpperBound(AttestedUncertainty):
    """The family whose members attest a genuine probability UPPER BOUND.

    **This family is not registered**, and that — not the subclass hook —
    is what refuses every artifact: :func:`admission_problems` refuses any
    demand :data:`UNCERTAINTY_INTAKES` does not name, before it selects an
    authority or looks at an artifact. Neither ``ABCMeta.register`` nor a
    ``__bases__`` rebinding can put a class into the registry, which is
    why the registry and not the type graph is what is asked.

    The family is also listed in :data:`CLOSED_FAMILIES`, so
    ``AttestedUncertainty.__init_subclass__`` refuses an ordinary subclass
    at class-definition time. That is drift protection and not a boundary;
    a hostile metaclass escapes the hook, which is why the registry answer
    above is the one that matters.

    A chance constraint needs ``P(true rate <= reported rate) >= level``;
    :class:`~dskit.pipeline.false_signal.FalseSignalEstimate` ships
    ``pi_widened``, a widened point estimate whose measured attainment is
    0.53–0.82 against a 0.95 nominal (ADR-0152, which renamed the field
    rather than claim what it could not deliver). A consumer that needs a
    bound names this class; the class is not registered, so the demand is
    refused outright and no artifact — however its class's bases, MRO or
    virtual registrations are arranged — can satisfy it.

    **What two review rounds established.** Round 1: four lines
    subclassing this family constructed, admitted, and handed
    ``pi_widened`` over as a bound. Round 2, after the seal:
    ``ProbabilityUpperBound.register(AttestedFalseSignalRate)`` did the
    same without creating a class. Claiming a bound now means editing this
    file, registering the member, and writing an ADR with the measurement
    that earns it — a reviewable act rather than a silent one — and it
    remains true that none of this stops code that already controls the
    interpreter. See the module docstring on why nothing here is a root of
    trust.

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
    :class:`ProbabilityUpperBound`, and no registered intake is: a demand
    for that family is answered from the registry and finds nothing, so
    this member is refused for it however it is dressed up.
    ``pi_widened`` is a widened point estimate, and a consumer
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

    The ONE writer of :data:`UNCERTAINTY_INTAKES`. That is now mechanical
    rather than conventional — the store is a closure local of
    :func:`_sealed_registry`, so no importable name offers an unvalidated
    write, and no ordinary attribute access on the view does either.

    The ceiling is a RULE and this docstring does not restate it: read
    :func:`_sealed_registry`. Round-6 review found this paragraph still
    carrying the discredited single-route wording fourteen hundred lines
    from the one that had been corrected, which is CLAUDE.md's own named
    defect — a claim in two places with nothing pinning them. Both are
    pinned by digest now.

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
    callable
        A zero-argument undo for THIS registration, which removes the entry
        only while it is still the one in place. It is returned rather than
        offered as a module-level ``unregister`` so that no name in this
        module can remove an intake the caller did not register.

    Raises
    ------
    ValueError
        On an empty ``name``, a ``cls`` that is not an
        :class:`AttestedUncertainty` subclass, a ``cls`` that is still
        abstract, a ``cls`` whose ``artifact_type()`` raises, a name
        already bound to a DIFFERENT class, an ``artifact_type`` another
        member already claims, or an already-registered member whose own
        ``artifact_type()`` cannot be read — uniqueness cannot be shown
        against a member that will not say what it claims.

    Examples
    --------
    Register a project's own member and take its undo::

        forget = register_uncertainty_intake("my_band", MyBand, doc="a band")
        UNCERTAINTY_INTAKES["my_band"] is MyBand
        # -> True
        forget()
    """
    return _register_intake(name, cls, doc)


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
