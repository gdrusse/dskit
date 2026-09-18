"""Uncertainty in a mean effect when the observations overlap in time.

How wide is the honest interval once the rows stop being independent?

A caller has one stream of per-observation scores — out-of-fold errors, a
loss gap, a per-row realized effect — and needs three numbers about its
MEAN: a point estimate, a standard error that survives the dependence
between the observations, and a two-sided interval at a stated LEVEL. A
robust-optimization counterpart consumes the interval as a per-component
adverse deviation; that consumer is the reason the interval must never be
too narrow, and the reason a level this module cannot deliver is named
something other than a confidence level (ADR-0151).

**The dependence is an argument, never a default.** Overlapping labels are
the ordinary case: a score computed over the next ``h`` steps shares
ground with the ``h - 1`` scores after it, so ``s / sqrt(n)`` counts each
observation as its own evidence and understates the spread by exactly the
factor that matters. A module that defaulted the dependence to "none"
would hand a caller who said nothing the narrowest possible answer, which
is the failure this module exists to prevent. :class:`MeanEvidence`
therefore refuses to exist without a dependence statement, in whichever
of the two spellings the caller actually has:

* ``units`` — a per-observation independence-unit label. This is
  :mod:`~dskit.pipeline.attempts`'s doctrine in argument form: the
  exchangeable unit is a WHOLE BLOCK, because a block moves every
  overlapping label with it. Resampling those units is a whole-unit block
  bootstrap.
* ``overlap_steps`` — how many observation steps a label reaches over,
  the idiom ``stats.newey_west_mean``'s ``lags`` already uses.

A caller with both gets both screened: a stated overlap that cannot even
FIT inside the shortest unit means the units are certainly not
independent units, and :class:`ClusterBootstrapInterval` refuses rather
than resampling them as though they were. That screen is necessary and
NOT sufficient — see :meth:`ClusterBootstrapInterval.independent_units`,
and the measured table below for what passing it is worth.

**The two spellings are NOT equally good, and this module says which by
name.** ``units`` is the PRIMARY contract: over genuine independence
units :class:`ClusterBootstrapInterval` was MEASURED to deliver its
nominal level, and it returns a :class:`ConfidenceInterval`. The
``overlap_steps`` spelling was measured NOT to deliver it, so
:class:`NeweyWestInterval` returns a :class:`WidenedInterval` — a value
whose ``level`` is a widening knob and not a coverage guarantee, the same
move ``false_signal.pi_widened`` makes for the same reason. Every member
DECLARES which of the two its answer is, through
:attr:`~MeanIntervalEstimator.result_class`, and there is no default:
the calibrated claim is the one no member should inherit by accident.

**The measurement, not an argument.** Monte Carlo against a known true
mean, 1,000–2,500 trials per cell, nominal ``level`` 0.95. The harness
ships as ``tests/pipeline/test_mean_interval.py``'s ``slow``-marked
coverage test, so what follows stays pinned by evidence rather than
prose:

===================================================  =========  ===========
sample                                               units      measured
===================================================  =========  ===========
whole independent units, each label contained        8/15/      94.6–96.3%
inside its unit — ``ClusterBootstrapInterval``       25/50
on ``units``
10-step rolling mean, ``overlap_steps=9``, units     8/15/      86.9–91.1%
carved at the overlap length — EITHER member         25/50
AR(1) φ=0.8, ``overlap_steps`` from                  53         ~81%
``stats.dm_lags`` — either member
AR(1) φ=0.8, ``overlap_steps=30`` (generous)         10         ~93%
===================================================  =========  ===========

**Why the ``overlap_steps`` path falls short, and why no constant chosen
here repairs it.** Two things break together and only one of them is this
module's. First, ``stats.newey_west_mean``'s Bartlett kernel weights the
autocovariance at lag ``k`` by ``1 - k / (lags + 1)``, so truncating at
exactly the stated overlap DOWNWEIGHTS every autocovariance that overlap
creates: for an ``h``-step rolling mean at ``lags = h - 1`` the estimated
long-run variance is 0.67 of the true one and the standard error 0.82 of
the true one — and that attenuation does not shrink with more data (0.72
measured at ``n = 80``, 0.80 at ``n = 500``), which is why the shortfall
above is FLAT from 8 units to 50. Second,
:meth:`NeweyWestInterval.independent_units` is
``n // (overlap_steps + 1)``, the count of adjacent non-overlapping
BLOCKS — but the last row of a block still shares raw ground with the
first ``overlap_steps`` rows of the next, so that count is an upper bound
on independence and never a certificate of it. A degrees-of-freedom
discount cannot repair a systematically attenuated standard error, and
the attenuation factor depends on the autocovariance SHAPE (0.82 for a
rolling mean, something else for an AR(1)), so no constant this module
could pick would deliver the nominal level for the next caller's process.
The honest repairs — a different kernel or bandwidth inside
``stats.newey_west_mean``, or fixed-``b`` critical values in place of
Student-t — are new arithmetic in a module this one deliberately does not
re-derive. So the level is not claimed. ADR-0151 records the decision.

**The arithmetic is not re-derived here.** ``stats.cluster_bootstrap_t``
owns the studentized cluster bootstrap-t and ``stats.newey_west_mean``
owns the HAC mean and standard error. This module owns the CONTRACT
around them — the required dependence statement, the screens, the
minimum-unit floor, and a result object that cannot hold a contradiction.
What it adds arithmetically is small and specific: the HAC path had no
interval at all, so the Student-t inversion is here.

**Fail-closed, and loudly.** A bound the method cannot claim raises. It is
never ``None``, never NaN, and never a quietly narrower number — a
consumer that reads an absent bound as "no adjustment needed" sizes as if
there were no uncertainty at all. That is a deliberate divergence from
``stats.cluster_bootstrap_t``, whose ``None`` bounds are right for
descriptive evidence printed beside a p-value and wrong for a number a
robust constraint eats.

**The family is a class, not a switch.** :class:`MeanIntervalEstimator`
owns ``interval`` as a template method a member never overrides —
enforced by ``__init_subclass__``, not by a docstring asking nicely. A
member supplies four hooks. Adding a geometric-block (stationary)
bootstrap, a BCa interval or a different HAC kernel is a subclass, not an
``if method ==`` inside one body.

Deterministic: the bootstrap member seeds through ``stats``'s pinned
``sha256(seed:label)`` recipe, so identical inputs give identical floats
whatever the iteration order.

Nothing here is a node kind, and nothing here is wired into a document.

**Import cost.** Stdlib plus three siblings in this package:
``records.number_ok`` and ``records.cluster_ok`` (the finiteness and
cluster-id rules) and ``node.class_ref``, the one owner of the
``module:QualName`` spelling recorded in ``method``. ``node`` drags
``base`` and ``document`` in with it, so importing this module is NOT
free; that is the price of not carrying a second copy of the
class-spelling rule, which CLAUDE.md's "a function is never repeated
across modules" forbids. No third-party dependency.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from dskit.pipeline.node import class_ref
from dskit.pipeline.records import cluster_ok, number_ok
from dskit.pipeline.stats import cluster_bootstrap_t, newey_west_mean, student_t_sf

__all__ = [
    "DEFAULT_LEVEL",
    "DEFAULT_REPLICATES",
    "DEFAULT_SEED",
    "MEAN_INTERVAL_ESTIMATORS",
    "MIN_INDEPENDENT_UNITS",
    "ClusterBootstrapInterval",
    "ConfidenceInterval",
    "MeanEvidence",
    "MeanIntervalEstimator",
    "MeanIntervalResult",
    "NeweyWestInterval",
    "WidenedInterval",
    "mean_interval_estimator",
    "register_mean_interval_estimator",
]

#: The two-sided level an interval is taken at when the caller does not
#: say. The level is a POLICY a project pins prospectively, so it has one
#: name and appears as a literal nowhere. Whether that level is DELIVERED
#: as coverage is not this constant's business — it is the member's, and
#: the member declares it by returning a :class:`ConfidenceInterval` or a
#: :class:`WidenedInterval`. The dependence structure, by contrast, has
#: no default at all — see the module docstring for why.
DEFAULT_LEVEL = 0.95

#: Bootstrap replicates when the caller does not say. High enough that
#: the 2.5%% and 97.5%% pivot quantiles are not decided by a handful of
#: draws.
DEFAULT_REPLICATES = 2000

#: Base seed for the bootstrap member. Any fixed value gives
#: reproducibility; this one is named so it is never written twice.
DEFAULT_SEED = 0

#: The hard floor on effectively independent units, shared by every
#: member because it is a fact about small-sample resampling rather than
#: about one method. Below it the bootstrap-t's tail quantiles are
#: dominated by degenerate replicates and a HAC long-run variance is
#: badly downward-biased, so the interval that comes back is narrower
#: than the evidence supports. This is a REFUSAL THRESHOLD, not a
#: sufficiency claim: coverage between this floor and a few dozen units
#: is still poor, and a caller who needs a trustworthy interval must
#: validate coverage empirically rather than read this number as
#: permission. A member with a stronger requirement overrides
#: ``minimum_units``.
MIN_INDEPENDENT_UNITS = 8

#: Bisection depth for the Student-t inversion — far past float64's ~53
#: bits, so the returned critical value is exact to representation and
#: does not depend on a tolerance anyone picked.
_BISECTION_STEPS = 100

#: The largest critical value the inversion will bracket. A confidence so
#: extreme that the t quantile exceeds this is refused rather than
#: silently clamped to a finite bound.
_QUANTILE_CEILING = 1e8


def _check_open_unit(value, name):
    """Refuse anything that is not a finite number strictly inside (0, 1)."""
    if not number_ok(value) or not 0.0 < value < 1.0:
        raise ValueError(f"{name} must be a number in (0, 1), got {value!r}")


def _check_count(value, name, minimum):
    """Refuse anything that is not a plain int at or above ``minimum``."""
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f"{name} must be an int >= {minimum}, got {value!r}")


def _student_t_critical(level, df):
    """Invert the Student tail for the two-sided ``level`` critical value."""
    target = 0.5 * (1.0 - level)
    high = 1.0
    while student_t_sf(high, df) > target:
        high *= 2.0
        if high > _QUANTILE_CEILING:
            raise ValueError(
                f"level {level!r} needs a Student-t critical value above "
                f"{_QUANTILE_CEILING:g} at df={df} — ask for a lower level or "
                "bring more independent units"
            )
    low = 0.0
    for _ in range(_BISECTION_STEPS):
        mid = 0.5 * (low + high)
        if student_t_sf(mid, df) > target:
            low = mid
        else:
            high = mid
    return 0.5 * (low + high)


@dataclass(frozen=True)
class MeanEvidence:
    """One time-ordered sample beside the statement of how it depends on itself.

    An instance cannot exist without a dependence statement: passing
    neither ``units`` nor ``overlap_steps`` refuses at construction, by
    name. That is the whole point of the class — the alternative is a
    default, and the only available default ("the observations are
    independent") is the answer that is too narrow.

    Parameters
    ----------
    values : sequence of float
        The per-observation scores whose MEAN is the estimand, in TIME
        order, at least two, every one finite.
    units : sequence of str or None
        One independence-unit label per observation — a block, an event,
        a batch. Held to
        :func:`~dskit.pipeline.records.cluster_ok` (a non-empty string),
        the repo's one cluster-id rule, so a label this package would
        refuse elsewhere cannot silently become a unit of its own here.
    overlap_steps : int or None
        How many observation steps a label overlaps the ones after it: an
        ``h``-step label on rows spaced one step apart takes ``h - 1``,
        and non-overlapping observations take ``0``. Must be less than
        the number of observations.

    Raises
    ------
    ValueError
        When neither dependence spelling is given; on fewer than two
        values; on a non-finite value, a unit label the repo's rule
        refuses, or a length mismatch (each named by index); or on an
        ``overlap_steps`` that is not an int in ``[0, n)``.

    Examples
    --------
    Twelve blocks of five scored rows, resampled by block::

        ev = MeanEvidence([0.1] * 60, units=[f"d{i // 5}" for i in range(60)])
        ev.n
        # -> 60

    The same sample described by its overlap instead::

        ev = MeanEvidence([0.1] * 60, overlap_steps=4)
    """

    values: tuple
    units: tuple = None
    overlap_steps: int = None

    def __post_init__(self):
        """Refuse an unusable sample, or one whose dependence went unstated."""
        try:
            values = tuple(self.values)
        except TypeError:
            raise ValueError(
                f"values must be a sequence of numbers, got {self.values!r}"
            ) from None
        if len(values) < 2:
            raise ValueError(
                f"values must hold at least 2 observations, got {len(values)}"
            )
        for i, value in enumerate(values):
            if not number_ok(value):
                raise ValueError(f"values[{i}] must be a finite number, got {value!r}")
        object.__setattr__(self, "values", tuple(float(v) for v in values))

        if self.units is None and self.overlap_steps is None:
            raise ValueError(
                "the dependence structure is never defaulted — state units "
                "(one independence-unit label per observation) or "
                "overlap_steps (label overlap in observation steps), or both"
            )

        if self.units is not None:
            try:
                units = tuple(self.units)
            except TypeError:
                raise ValueError(
                    f"units must be a sequence of labels, got {self.units!r}"
                ) from None
            if len(units) != len(values):
                raise ValueError(
                    f"units must carry one label per observation — {len(values)} "
                    f"values against {len(units)} units"
                )
            for i, label in enumerate(units):
                if not cluster_ok(label):
                    raise ValueError(
                        f"units[{i}] must be a non-empty string, got {label!r}"
                    )
            object.__setattr__(self, "units", units)

        if self.overlap_steps is not None:
            _check_count(self.overlap_steps, "overlap_steps", 0)
            if self.overlap_steps >= len(values):
                raise ValueError(
                    f"overlap_steps {self.overlap_steps} must be below the "
                    f"{len(values)} observations it describes"
                )

    @property
    def n(self):
        """Count the observations.

        Returns
        -------
        int
            The number of values.
        """
        return len(self.values)

    def grouped(self):
        """Group the values by their unit label.

        Returns
        -------
        dict of str -> list of float
            One entry per distinct label, values in their original order
            — the shape
            :func:`~dskit.pipeline.stats.cluster_bootstrap_t` resamples.

        Raises
        ------
        ValueError
            When this evidence carries no ``units``.
        """
        if self.units is None:
            raise ValueError(
                "this evidence states no units — group-based methods need one "
                "independence-unit label per observation"
            )
        grouped = {}
        for value, label in zip(self.values, self.units):
            grouped.setdefault(label, []).append(value)
        return grouped

    def shortest_unit_run(self):
        """Measure the shortest contiguous run any one unit occupies.

        A label that overlaps further than this reaches out of its own
        unit and into the next, which is what makes the units stop being
        independent units.

        Returns
        -------
        int
            The shortest run length, in observations.

        Raises
        ------
        ValueError
            When this evidence carries no ``units``.
        """
        if self.units is None:
            raise ValueError("this evidence states no units — there are no runs")
        runs, current, previous = [], 0, None
        for label in self.units:
            if label == previous:
                current += 1
            else:
                if previous is not None:
                    runs.append(current)
                current, previous = 1, label
        runs.append(current)
        return min(runs)


@dataclass(frozen=True)
class MeanIntervalResult:
    """A mean, its dependence-aware standard error, and the two bounds.

    **This base says nothing about what ``level`` MEANS.** It is the
    shared geometry and the shared invariants; the claim is carried by
    the two subclasses, and :meth:`MeanIntervalEstimator.interval` will
    not hand back the bare base — a member must name
    :class:`ConfidenceInterval` or :class:`WidenedInterval` (or its own
    subclass of one of them) and thereby SAY which it is.

    The consumer contract lives HERE rather than in the caller: an
    instance cannot exist whose bounds fail to bracket its own mean, or
    which carries a non-finite number, so the guarantee a robust
    counterpart rests on cannot rot silently.

    ``deviation_below`` and ``deviation_above`` are pure geometry. Which
    one is the ADVERSE deviation depends on the direction the consumer
    fears, and that is the consumer's fact — this package does not know
    whether larger is better.

    Parameters
    ----------
    mean : float
        The point estimate, finite.
    standard_error : float
        Its standard error under the stated dependence, finite and
        positive.
    low, high : float
        The two-sided bounds, finite, with ``low <= mean <= high``.
    level : float
        The two-sided level the bounds were TAKEN at, in ``(0, 1)``.
        Whether it is DELIVERED as coverage is the subclass's claim, not
        this field's.
    independent_units : int
        How many effectively independent units the estimator read, at
        least two.
    method : str
        The estimator that produced this, as
        :func:`~dskit.pipeline.node.class_ref` spells it.

    Raises
    ------
    ValueError
        On a non-finite or non-numeric field, a non-positive standard
        error, bounds that do not bracket the mean, a level outside
        ``(0, 1)``, fewer than two units, or an empty method.

    Examples
    --------
    Built by :meth:`MeanIntervalEstimator.interval`, never by hand::

        out = NeweyWestInterval().interval(MeanEvidence(rows, overlap_steps=11))
        out.deviation_below
        # -> the distance from the point estimate down to the lower bound
    """

    mean: float
    standard_error: float
    low: float
    high: float
    level: float
    independent_units: int
    method: str

    def __post_init__(self):
        """Refuse any combination a consuming robust program would reject."""
        for name in ("mean", "standard_error", "low", "high"):
            value = getattr(self, name)
            if not number_ok(value):
                raise ValueError(f"{name} must be a finite number, got {value!r}")
            object.__setattr__(self, name, float(value))
        if self.standard_error <= 0.0:
            raise ValueError(
                f"standard_error must be positive, got {self.standard_error!r} — a "
                "zero-width interval is not evidence of certainty"
            )
        if self.low > self.mean or self.mean > self.high:
            raise ValueError(
                f"bounds [{self.low!r}, {self.high!r}] do not bracket the mean "
                f"{self.mean!r}"
            )
        _check_open_unit(self.level, "level")
        object.__setattr__(self, "level", float(self.level))
        _check_count(self.independent_units, "independent_units", 2)
        if not isinstance(self.method, str) or not self.method:
            raise ValueError(f"method must be a non-empty string, got {self.method!r}")

    @property
    def deviation_below(self):
        """Measure the distance from the mean down to the lower bound.

        Returns
        -------
        float
            ``mean - low``, never negative.
        """
        return self.mean - self.low

    @property
    def deviation_above(self):
        """Measure the distance from the mean up to the upper bound.

        Returns
        -------
        float
            ``high - mean``, never negative.
        """
        return self.high - self.mean

    @property
    def width(self):
        """Measure the whole interval.

        Returns
        -------
        float
            ``high - low``, never negative.
        """
        return self.high - self.low


@dataclass(frozen=True)
class ConfidenceInterval(MeanIntervalResult):
    """The bounds whose ``level`` IS a confidence level, and was measured.

    A member returns this only when its own contract was shown to deliver
    the nominal level — not when the arithmetic merely looks right.
    :class:`ClusterBootstrapInterval` returns it, and the module
    docstring's table is the measurement: 94.6–96.3% at a nominal 0.95,
    at 8 through 50 units, over GENUINE independence units.

    That last clause is the whole condition, and this module cannot check
    it. "Genuine" means every observation's label is realized inside its
    own unit, so the units are exchangeable whole. Chopping one
    overlapping series into contiguous blocks the length of its own
    overlap does NOT produce such units — adjacent blocks still share raw
    ground across the cut — and that configuration measured 86.9–91.1%,
    not 95%. Stating the units is therefore an ASSERTION the caller makes
    and this module records; it is not one it verifies.

    Parameters
    ----------
    Inherited unchanged from :class:`MeanIntervalResult`.

    Examples
    --------
    What the block bootstrap hands back::

        out = ClusterBootstrapInterval().interval(MeanEvidence(rows, units=blocks))
        type(out) is ConfidenceInterval
        # -> True
    """


@dataclass(frozen=True)
class WidenedInterval(MeanIntervalResult):
    """The bounds whose ``level`` is a WIDENING knob, not a coverage claim.

    Raising ``level`` widens these bounds and lowering it narrows them,
    monotonically — that much is true and is tested. What is NOT true, and
    is not claimed, is that the bounds contain the true mean with
    probability ``level``. :class:`NeweyWestInterval` returns this because
    it was measured at 86.9–91.1% against a nominal 0.95 on a correctly
    stated overlap, and near 81% on an AR(1) at the overlap the repo's own
    ``stats.dm_lags`` rule suggests. The module docstring says why, and
    why no constant chosen here would repair it.

    Read it as a sensitivity reading — "how far does the mean move when
    the stated dependence is priced" — and do not build a chance
    constraint on it. The naming follows ``false_signal.pi_widened``,
    which made the same retreat for the same reason.

    Parameters
    ----------
    Inherited unchanged from :class:`MeanIntervalResult`.

    Examples
    --------
    What the HAC member hands back::

        out = NeweyWestInterval().interval(MeanEvidence(rows, overlap_steps=11))
        isinstance(out, ConfidenceInterval)
        # -> False
    """


class MeanIntervalEstimator(ABC):
    """The doorway: one dependent sample in, one bracketed mean out.

    ``interval`` is a TEMPLATE method and is never overridden — enforced
    by ``__init_subclass__``, not by a docstring asking nicely. It owns
    the level screen, the minimum-unit refusal, the degenerate-standard-
    error refusal and the result invariants. A member supplies four
    hooks, one job each: what its answer CLAIMS, how many independent
    units this evidence holds, the point estimate and its standard error,
    and the bounds.

    A member that reads only one of the two dependence spellings refuses
    the other BY NAME through :meth:`independent_units`, rather than
    quietly reading whichever field happens to be set.

    :attr:`result_class` is abstract on purpose and has NO default. A new
    member must state whether its ``level`` is a measured confidence
    level (:class:`ConfidenceInterval`) or only a widening knob
    (:class:`WidenedInterval`); inheriting the calibrated claim by
    silence is exactly the overclaim this module exists to prevent.

    Examples
    --------
    A member states its claim, its floor, and supplies the hooks::

        class FixedWidth(MeanIntervalEstimator):
            result_class = WidenedInterval
            def independent_units(self, evidence):
                return evidence.n
            def mean_and_se(self, evidence):
                total = sum(evidence.values)
                return total / evidence.n, 1.0
            def bounds(self, evidence, mean, se, level):
                return mean - se, mean + se

        FixedWidth().interval(MeanEvidence([1.0] * 40, overlap_steps=0))
    """

    def __init_subclass__(cls, **kwargs):
        """Refuse a subclass that replaces the template; every HOOK stays overridable."""
        super().__init_subclass__(**kwargs)
        if "interval" in vars(cls):
            raise TypeError(
                f"{cls.__name__} overrides interval, which is final (ADR-0151): the "
                "screens, the minimum-unit floor and the result invariants are the "
                "seam, their order is not — override a hook instead"
            )

    @property
    @abstractmethod
    def result_class(self):
        """Name the result type this member's answer has EARNED.

        Returns
        -------
        type
            A STRICT subclass of :class:`MeanIntervalResult` —
            :class:`ConfidenceInterval` when the member was measured to
            deliver its nominal level, :class:`WidenedInterval` when it
            was not. The bare base is refused: every member says which.
        """

    @property
    def minimum_units(self):
        """State the fewest independent units this method will run on.

        Returns
        -------
        int
            :data:`MIN_INDEPENDENT_UNITS` unless a member raises its own
            floor.
        """
        return MIN_INDEPENDENT_UNITS

    @abstractmethod
    def independent_units(self, evidence):
        """Count the effectively independent units this evidence holds.

        Parameters
        ----------
        evidence : MeanEvidence
            The sample and its dependence statement.

        Returns
        -------
        int
            The count, by this member's own reading of the dependence.

        Raises
        ------
        ValueError
            When the evidence does not state the dependence in the
            spelling this member reads.
        """

    @abstractmethod
    def mean_and_se(self, evidence):
        """Estimate the mean and its standard error under the dependence.

        Parameters
        ----------
        evidence : MeanEvidence
            The sample and its dependence statement.

        Returns
        -------
        tuple of (float, float)
            The point estimate and its standard error.
        """

    @abstractmethod
    def bounds(self, evidence, mean, se, level):
        """Place the two-sided bounds around an already-estimated mean.

        Parameters
        ----------
        evidence : MeanEvidence
            The sample and its dependence statement.
        mean : float
            What :meth:`mean_and_se` returned.
        se : float
            The standard error :meth:`mean_and_se` returned, positive.
        level : float
            The two-sided level, in ``(0, 1)``.

        Returns
        -------
        tuple of (float, float)
            ``(low, high)``.

        Raises
        ------
        ValueError
            When this sample cannot support a claimable bound. A member
            RAISES rather than returning a missing or narrower bound.
        """

    def interval(self, evidence, *, level=DEFAULT_LEVEL):
        """Estimate the mean and bracket it at ``level``.

        The template: it screens the arguments, refuses a sample with too
        few independent units or no usable spread, asks the member's
        hooks, and assembles the member's declared
        :class:`MeanIntervalResult` subclass (whose own construction
        refuses bounds that do not bracket the mean).

        Parameters
        ----------
        evidence : MeanEvidence
            The sample and its dependence statement. A plain list is
            refused: the dependence has to have been stated.
        level : float
            Two-sided level in ``(0, 1)``; defaults to
            :data:`DEFAULT_LEVEL`. Whether the member DELIVERS it as
            coverage is said by the type that comes back.

        Returns
        -------
        MeanIntervalResult
            The member's :attr:`result_class` — a
            :class:`ConfidenceInterval` or a :class:`WidenedInterval` —
            carrying the point estimate, standard error, bounds and
            provenance.

        Raises
        ------
        ValueError
            On a non-:class:`MeanEvidence` argument, a level outside
            ``(0, 1)``, fewer independent units than
            :attr:`minimum_units`, a non-finite or non-positive standard
            error, a hook that could not claim a bound, or a member whose
            :attr:`result_class` is not a strict
            :class:`MeanIntervalResult` subclass.

        Examples
        --------
        Twenty blocks of overlapping rows, at a 99%% level::

            out = ClusterBootstrapInterval().interval(ev, level=0.99)
            out.low <= out.mean <= out.high
            # -> True
        """
        if not isinstance(evidence, MeanEvidence):
            raise ValueError(
                "evidence must be a MeanEvidence — the dependence structure is "
                f"never defaulted, got {type(evidence).__name__}"
            )
        _check_open_unit(level, "level")
        claimed = self.result_class
        if (
            not isinstance(claimed, type)
            or not issubclass(claimed, MeanIntervalResult)
            or claimed is MeanIntervalResult
        ):
            raise ValueError(
                f"{class_ref(type(self))} must name a strict MeanIntervalResult "
                f"subclass as its result_class — ConfidenceInterval when the member "
                f"was measured to deliver its nominal level, WidenedInterval when it "
                f"was not — got {claimed!r}"
            )

        units = self.independent_units(evidence)
        _check_count(units, "independent_units", 0)
        floor = self.minimum_units
        if units < floor:
            raise ValueError(
                f"{class_ref(type(self))} needs at least {floor} independent units "
                f"and this evidence holds {units} — an interval from fewer would be "
                "narrower than the evidence supports"
            )

        mean, se = self.mean_and_se(evidence)
        if not number_ok(mean):
            raise ValueError(f"{class_ref(type(self))} produced a non-finite mean {mean!r}")
        if not number_ok(se):
            raise ValueError(
                f"{class_ref(type(self))} produced a non-finite standard error {se!r}"
            )
        if se <= 0.0:
            raise ValueError(
                f"{class_ref(type(self))} found no spread in this sample (standard "
                f"error {se!r}) — no interval is claimable from it, and a zero-width "
                "one would read as certainty"
            )

        low, high = self.bounds(evidence, mean, se, level)
        return claimed(
            mean=mean,
            standard_error=se,
            low=low,
            high=high,
            level=level,
            independent_units=units,
            method=class_ref(type(self)),
        )


class ClusterBootstrapInterval(MeanIntervalEstimator):
    """Whole-unit block bootstrap-t: the units are the resampling unit.

    Delegates every replicate to
    :func:`~dskit.pipeline.stats.cluster_bootstrap_t`, which owns the
    studentized recentered cluster bootstrap-t and its pinned
    ``sha256(seed:label)`` draw stream. Nothing is resampled here.

    This is the whole-block bootstrap in generic clothes: with ``units``
    naming genuinely exchangeable blocks it resamples exactly the unit
    :mod:`~dskit.pipeline.attempts` defines, because a unit moves every
    label that overlaps inside it.

    **This is the module's calibrated member, and the claim was
    measured.** Over genuine independence units it returns a
    :class:`ConfidenceInterval`: 94.6–96.3% at a nominal 0.95, at 8
    through 50 units, in the module docstring's Monte Carlo. The
    condition is the caller's to meet and this class cannot check it —
    units carved out of one overlapping series at the overlap's own
    length are not independence units, and that configuration measured
    86.9–91.1%. :meth:`independent_units` screens the coarsest version of
    that error (an overlap that cannot even fit inside a unit) and no
    more; it is a screen, never a certificate.

    Parameters
    ----------
    replicates : int
        Bootstrap replicates, >= 1; defaults to
        :data:`DEFAULT_REPLICATES`.
    seed : int
        Base seed; defaults to :data:`DEFAULT_SEED`.
    label : str
        A stable per-component tag combined with ``seed``, so two
        components' resamples are independent of each other and of
        iteration order — the same reason
        :func:`~dskit.pipeline.stats.cluster_bootstrap_pvalue` takes one.

    Raises
    ------
    ValueError
        On a non-int ``replicates`` below 1, a non-int ``seed``, or a
        non-string ``label``.

    Examples
    --------
    Twenty blocks, five thousand replicates, tagged per component::

        est = ClusterBootstrapInterval(replicates=5000, seed=7, label="c1")
        est.interval(MeanEvidence(rows, units=blocks))
    """

    #: Measured to deliver its nominal level over genuine independence
    #: units — see the class docstring for the numbers and the condition.
    result_class = ConfidenceInterval

    def __init__(self, *, replicates=DEFAULT_REPLICATES, seed=DEFAULT_SEED, label=""):
        """Pin the replicate count and the draw stream; refuse a bad knob."""
        _check_count(replicates, "replicates", 1)
        if isinstance(seed, bool) or not isinstance(seed, int):
            raise ValueError(f"seed must be an int, got {seed!r}")
        if not isinstance(label, str):
            raise ValueError(f"label must be a string, got {label!r}")
        self.replicates = replicates
        self.seed = seed
        self.label = label

    def independent_units(self, evidence):
        """Count the distinct unit labels, and screen the stated overlap.

        Parameters
        ----------
        evidence : MeanEvidence
            The sample; must state ``units``.

        Returns
        -------
        int
            The number of distinct unit labels.

        Raises
        ------
        ValueError
            When no ``units`` are stated, or when a stated
            ``overlap_steps`` reaches at or past the shortest unit's
            contiguous run — an overlap that cannot even fit inside a
            unit means the units are certainly not independent units, and
            resampling them as though they were is how a too-narrow
            interval is produced.

        Notes
        -----
        The overlap screen is NECESSARY and not sufficient. An overlap
        that fits inside the shortest run still reaches out of the LAST
        rows of every unit and into the next one unless the units are
        separated in time — which is what a caller asserts by naming them
        units, and is not something this method can see. Contiguous
        blocks carved from one overlapping series pass this screen and
        measured 86.9–91.1% against a nominal 0.95.
        """
        if evidence.units is None:
            raise ValueError(
                f"{class_ref(type(self))} resamples units and this evidence states "
                "none — give one independence-unit label per observation, or use a "
                "member that reads overlap_steps"
            )
        if evidence.overlap_steps is not None:
            shortest = evidence.shortest_unit_run()
            if evidence.overlap_steps >= shortest:
                raise ValueError(
                    f"a stated overlap of {evidence.overlap_steps} steps reaches past "
                    f"the shortest unit run ({shortest} observations), so a label "
                    "started in one unit lands in the next and the units are not "
                    "independent — widen the units until each contains its own overlap"
                )
        return len(set(evidence.units))

    def mean_and_se(self, evidence):
        """Read the pooled statistic and its cluster-robust standard error.

        Both are computed by
        :func:`~dskit.pipeline.stats.cluster_bootstrap_t` from the
        OBSERVED units alone, before any resampling, so they do not
        depend on the replicate count or the seed. One replicate is
        therefore asked for here and the full stream in :meth:`bounds`;
        the two calls agree by construction, not by coincidence.

        Parameters
        ----------
        evidence : MeanEvidence
            The sample; must state ``units``.

        Returns
        -------
        tuple of (float, float)
            The size-weighted pooled mean and its cluster-robust
            standard error.
        """
        out = cluster_bootstrap_t(evidence.grouped(), 1, self.seed, label=self.label)
        return out["mean"], out["se"]

    def bounds(self, evidence, mean, se, level):
        """Take the bootstrap-t pivot bounds from the full replicate stream.

        Parameters
        ----------
        evidence : MeanEvidence
            The sample; must state ``units``.
        mean : float
            The point estimate, used only to verify the delegate agrees.
        se : float
            The standard error, unused — the delegate recomputes it from
            the same observed units.
        level : float
            Two-sided level in ``(0, 1)``.

        Returns
        -------
        tuple of (float, float)
            The bootstrap-t bounds.

        Raises
        ------
        ValueError
            When the selected pivot tail is degenerate, so the delegate
            claims no bound. A missing bound is raised, never passed on
            as ``None``.
        """
        out = cluster_bootstrap_t(
            evidence.grouped(),
            self.replicates,
            self.seed,
            label=self.label,
            alpha=1.0 - level,
        )
        if out["mean"] != mean:
            raise ValueError(
                "the bootstrap delegate disagreed with its own point estimate "
                f"({out['mean']!r} against {mean!r}) — this is an upstream bug"
            )
        if out["ci_low"] is None or out["ci_high"] is None:
            raise ValueError(
                f"the bootstrap-t pivot tail is degenerate at level "
                f"{level!r} on {out['n_clusters']} units, so no bound is "
                "claimable — raise the replicate count or bring more units"
            )
        return out["ci_low"], out["ci_high"]


class NeweyWestInterval(MeanIntervalEstimator):
    """HAC-analytic: a Newey-West standard error and a Student-t bracket.

    Delegates the long-run variance to
    :func:`~dskit.pipeline.stats.newey_west_mean` with ``lags`` taken
    from the evidence's ``overlap_steps`` — the repo's existing
    overlap-in-observation-steps idiom. What is added here is the
    bracket that function never returned.

    **This member does NOT deliver its nominal level, and says so by
    returning a** :class:`WidenedInterval`. Measured against a known true
    mean: 86.9–91.1% at a nominal 0.95 on a correctly stated 10-step
    rolling mean, flat from 8 independent units to 50; about 81% on an
    AR(1) at φ=0.8 with the overlap ``stats.dm_lags`` suggests; about 93%
    only when handed an overlap three times the truth. The module
    docstring holds the diagnosis — a Bartlett kernel truncated at the
    stated overlap attenuates the standard error to ~0.82 of the truth
    and does not stop doing so with more data — and explains why no
    constant this class could apply would repair it for the next
    caller's process. Raising ``level`` widens these bounds; it does not
    buy coverage.

    The critical value is Student-t on ``independent_units - 1`` degrees
    of freedom, NOT ``n - 1``. Overlapping observations are not each a
    degree of freedom, and pairing a HAC standard error with the raw row
    count is an even narrower answer. That choice is a mitigation, not a
    fix: the measurement above is what it buys.

    Examples
    --------
    Four hundred rows carrying a twelve-step label::

        est = NeweyWestInterval()
        est.interval(MeanEvidence(rows, overlap_steps=11))
    """

    #: Measured NOT to deliver its nominal level — see the class
    #: docstring. ``level`` here widens; it does not cover.
    result_class = WidenedInterval

    def independent_units(self, evidence):
        """Count the non-overlapping blocks the stated overlap leaves.

        ``n // (overlap_steps + 1)`` — two observations one overlap-length
        apart no longer share ground, so that many of them is the
        effective sample size this member takes its degrees of freedom
        from.

        It is an UPPER BOUND on independence and not a certificate. The
        blocks it counts are adjacent, and the last row of one still
        shares raw ground with the first ``overlap_steps`` rows of the
        next; only the block REPRESENTATIVES are mutually independent.
        Together with the Bartlett attenuation described on the class,
        this is why the member returns a :class:`WidenedInterval` rather
        than a :class:`ConfidenceInterval`.

        Parameters
        ----------
        evidence : MeanEvidence
            The sample; must state ``overlap_steps``.

        Returns
        -------
        int
            The non-overlapping block count.

        Raises
        ------
        ValueError
            When no ``overlap_steps`` is stated.
        """
        if evidence.overlap_steps is None:
            raise ValueError(
                f"{class_ref(type(self))} reads overlap_steps and this evidence "
                "states none — say how many observation steps a label overlaps, or "
                "use a member that resamples units"
            )
        return evidence.n // (evidence.overlap_steps + 1)

    def mean_and_se(self, evidence):
        """Read the HAC mean and standard error at the stated overlap.

        Parameters
        ----------
        evidence : MeanEvidence
            The sample; must state ``overlap_steps``.

        Returns
        -------
        tuple of (float, float)
            The sample mean and its Newey-West standard error.
        """
        out = newey_west_mean(list(evidence.values), lags=evidence.overlap_steps)
        return out["mean"], out["se"]

    def bounds(self, evidence, mean, se, level):
        """Bracket the mean with a Student-t critical value.

        Parameters
        ----------
        evidence : MeanEvidence
            The sample; must state ``overlap_steps``.
        mean : float
            The HAC point estimate.
        se : float
            Its HAC standard error, positive.
        level : float
            Two-sided level in ``(0, 1)``.

        Returns
        -------
        tuple of (float, float)
            ``mean -/+ t * se`` on ``independent_units - 1`` degrees of
            freedom.

        Raises
        ------
        ValueError
            When the level is too extreme to bracket at this many degrees
            of freedom.
        """
        df = self.independent_units(evidence) - 1
        critical = _student_t_critical(level, df)
        return mean - critical * se, mean + critical * se


#: The registry, mirroring ``stats.CORRECTIONS``:
#: ``name -> {"cls", "doc"}``. It holds CLASSES, because the family is an
#: object with hooks rather than a function. The subject is spelled out
#: in all three names because a bare ``estimator`` already means the
#: OPPOSITE thing next door — ``libs/sklearn.py`` uses it ~135 times for
#: the dotted path to an ML model — and every other registry here names
#: its subject (``register_node_kind``, ``register_correction``,
#: ``register_metric``, ``register_split_policy``). The sibling
#: ``false_signal.py`` was renamed the same way.
MEAN_INTERVAL_ESTIMATORS: dict = {}


def register_mean_interval_estimator(name, cls, *, doc=""):
    """Register a mean-interval estimator under ``name``.

    Parameters
    ----------
    name : str
        The registry key, a non-empty string.
    cls : type
        A :class:`MeanIntervalEstimator` subclass.
    doc : str
        One-line description for error messages and docs.

    Raises
    ------
    ValueError
        On an empty name, a class that is not a
        :class:`MeanIntervalEstimator` subclass, or a name already taken.

    Examples
    --------
    A project brings its own interval family::

        register_mean_interval_estimator(
            "my-bca", MyBcaInterval, doc="BCa over units."
        )
    """
    if not isinstance(name, str) or not name:
        raise ValueError(
            f"mean-interval estimator name must be a non-empty string, got {name!r}"
        )
    if not isinstance(cls, type) or not issubclass(cls, MeanIntervalEstimator):
        raise ValueError(
            f"mean-interval estimator {name!r} must be a MeanIntervalEstimator "
            f"subclass, got {cls!r}"
        )
    if name in MEAN_INTERVAL_ESTIMATORS:
        raise ValueError(f"mean-interval estimator {name!r} is already registered")
    MEAN_INTERVAL_ESTIMATORS[name] = {"cls": cls, "doc": doc}


def mean_interval_estimator(name):
    """Look up a registered mean-interval estimator entry, loudly.

    Parameters
    ----------
    name : str
        The registry key.

    Returns
    -------
    dict
        ``{"cls", "doc"}``.

    Raises
    ------
    ValueError
        When nothing is registered under ``name``; the message lists
        what is.

    Examples
    --------
    Resolve the shipped block bootstrap::

        mean_interval_estimator("cluster-bootstrap-t")["cls"]
        # -> ClusterBootstrapInterval
    """
    try:
        return MEAN_INTERVAL_ESTIMATORS[name]
    except KeyError:
        raise ValueError(
            f"unknown mean-interval estimator {name!r} — known: "
            f"{sorted(MEAN_INTERVAL_ESTIMATORS)}"
        ) from None


register_mean_interval_estimator(
    "cluster-bootstrap-t",
    ClusterBootstrapInterval,
    doc="Whole-unit block bootstrap-t; the units are the resampling unit. "
    "Measured to deliver its nominal level over genuine independence units.",
)
register_mean_interval_estimator(
    "newey-west",
    NeweyWestInterval,
    doc="HAC standard error at the stated overlap, bracketed by Student t. "
    "Widening only — measured NOT to deliver its nominal level.",
)
