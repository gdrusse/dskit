"""Per-signal false-signal probability: a point estimate and a widened reading.

How probable is it that this signal's apparent edge is nothing?

A family of signals was searched, scored out of fold, and audited against
a scramble null. Every downstream consumer that sizes exposure needs one
number per signal — the posterior probability that its edge is FALSE —
and usually wants it twice: a point estimate and something more cautious
(ADR-0152, over ADR-0088).

**Read this before using the second number.** ``pi_widened`` is NOT a
confidence bound, and this module does not claim one. It is the same
two-groups ratio re-read with its two BINOMIAL inputs widened. It prices
exactly two error sources and leaves the dominant one unpriced:

===================================  ===========================
priced                               unpriced
===================================  ===========================
Monte-Carlo error of a finite        density-estimation error of
scramble (``p_upper``)               the Grenander fit (``f``)
binomial error of the null share
at ``independent_units``
(``pi0_upper``)
===================================  ===========================

``f`` sits in the DENOMINATOR of every number here, it converges at
``m ** (-1/3)`` in the interior and is not even consistent at the left
boundary (Woodroofe-Sun), and nothing below prices that. The consequence
was measured, not guessed: in a two-groups Monte Carlo with known truth
and FULLY independent signals — ``independent_units == m``, the most
favourable case — the measured probability that ``pi_widened`` reaches
the true local fdr is

====  ====  ====  ==========================
pi0   m     B     P(pi_widened >= true fdr)
====  ====  ====  ==========================
0.80  200   999   0.82
0.90  200   999   0.78
0.95  100   999   0.68
0.80   40   999   0.72
0.99  200   999   0.53
====  ====  ====  ==========================

at ``widening_level = 0.95``. Raising the level does not repair it: the
knob moves the two binomial limits and nothing else, so it cannot buy
coverage the density fit does not have.
``tests/pipeline/test_false_signal.py`` ships that Monte Carlo as a
``slow``-marked test, so the claim stays pinned by evidence, not prose.

**So a chance constraint cannot be built on this.** A capital constraint
of the form ``sum_i(x_i * pi_i) <= q * sum_i(x_i)`` fed ``pi_widened``
is a constraint on a widened POINT ESTIMATE. It does not hold with
probability ``widening_level``, and at the ``pi0 ≈ 0.99`` regime a real
signal search lives in it is violated about half the time. Use
``pi_widened`` as a sensitivity reading — "how much does the answer move
when the two countable error sources are pushed to their limits" — and
size the remaining risk some other way.

**The two evidence streams.** A signal arrives as
:class:`SignalEvidence` — one out-of-fold ``statistic`` and the same
statistic recomputed under scrambles. Orientation is this package's
existing one (``attempts.beat_all``, ``attempts.tier2_verdict``): LARGER
is stronger evidence against the null. Nothing here knows what the
statistic MEANS; a project reduces its own out-of-fold rows (a pooled
Diebold-Mariano gap, an R-squared, a hit rate — whatever it scores) to
one number and supplies its own scrambles.

**Where the empirical null enters.** :func:`permutation_pvalue` compares
the observed statistic to the scramble draws themselves, so the p-scale
null is Uniform(0, 1) BY CONSTRUCTION. That is the whole empirical-null
step: no separate null is fitted, and no normal-theory assumption is
made anywhere below. The add-one form ``(1 + k) / (1 + B)`` is
Phipson-Smyth's exact valid p-value — never zero, never a point value,
the same doctrine ``attempts.early_stop_p_bound`` states for a stopped
audit. It is computed in ONE place and :meth:`FalseSignalEstimator.estimate`
calls it rather than restating it.

**Two limits the p-scale imposes, which no estimator can lift.** The
permutation p-value cannot resolve below ``1 / (1 + B)``: a statistic of
``1e-12`` and one of ``1e12`` that both beat every draw get the SAME
p-value and therefore the same ``pi_hat``. And because the Grenander
density at the left boundary is driven by the smallest fitted p-value,
``pi`` for the strongest signal falls roughly like ``1 / B`` — more
scrambles buy a smaller number without any new evidence about that
signal's edge. Both are pinned by tests. The defence against the second
is :data:`DEFAULT_MIN_FAMILY`: a two-groups model fitted to one or two
observations reports the scramble count, not the family.

**The estimator is a FAMILY, and the family is a subclass.**
:class:`FalseSignalEstimator` owns the parts that are not up for debate —
the p-values, the two widened inputs, the screens, the ratio — and
``estimate`` is a template method a member cannot override:
``__init_subclass__`` refuses the class at definition time. A member
supplies three hooks: :meth:`~FalseSignalEstimator.fit`,
:meth:`~FalseSignalEstimator.null_proportion` and
:meth:`~FalseSignalEstimator.density`. :class:`GrenanderLocalFdr` ships;
:func:`register_false_signal_estimator` is how a project brings its own,
mirroring ``stats.register_correction``.

**How the two numbers relate.** The family's density is fitted ONCE, on
the point-estimate p-values, and both numbers read that one shape::

    pi_hat_i     = min(1, pi0_hat   / f(p_hat_i))
    pi_widened_i = min(1, pi0_upper / f(p_upper_i))

``f`` is non-increasing and ``p_upper_i >= p_hat_i`` and
``pi0_upper >= pi0_hat``, so ``pi_widened_i >= pi_hat_i`` follows —
but NOT "by construction": :data:`_MONOTONE_SLACK` lets a member's
density rise by float noise without tripping the monotonicity screen,
which is enough to invert the pair in the last bits. The ordering is
therefore enforced by TWO screens, and the second one names the
estimator that broke it. Both widened inputs are exact binomial
(Clopper-Pearson) limits, each spending half of ``1 - widening_level``:

* ``p_upper_i`` bounds the Monte-Carlo error of a FINITE scramble. It is
  exact under arbitrary temporal dependence INSIDE the exchangeable unit,
  because a scramble permutes whole blocks: the exceedance indicator is a
  Bernoulli draw whatever the within-block correlation is. That validity
  is inherited from the caller's scramble design (``attempts.py``
  documents the whole-session unit); this module states the requirement
  and does not create it.
* ``pi0_upper`` bounds the family's null share at ``independent_units``,
  the count of EFFECTIVELY INDEPENDENT signals — required, never
  defaulted, and refused above the family size. Signals fitted on
  overlapping time share sessions, so a binomial bound at the signal
  count is anti-conservative; declaring ``independent_units == m`` is an
  explicit assertion of cross-signal independence and is the caller's to
  make. It is a plug-in effective-count widening, not a claim of exact
  finite-sample coverage of the two-groups null share.

Fail-closed throughout, and every refusal names its offender.

Deterministic: there is no RNG here at all. Two calls on the same
evidence return identical floats.

**Import cost.** No third-party dependency — stdlib plus three siblings
in this package: ``records.number_ok`` (the finiteness rule),
``stats.regularized_incomplete_beta`` (the beta tail's one owner) and
``node.class_ref`` (the one owner of the ``module:QualName`` spelling
recorded in ``evidence``). ``node`` itself imports ``base`` and
``document``, so importing this module is not free; that is the price of
not carrying a second copy of the class-spelling rule.
"""

from __future__ import annotations

import bisect
import math
from abc import ABC, abstractmethod
from dataclasses import dataclass
from types import MappingProxyType

from dskit.pipeline.node import class_ref
from dskit.pipeline.records import number_ok
from dskit.pipeline.stats import regularized_incomplete_beta

__all__ = [
    "DEFAULT_MIN_FAMILY",
    "DEFAULT_NULL_THRESHOLD",
    "DEFAULT_WIDENING_LEVEL",
    "FALSE_SIGNAL_ESTIMATORS",
    "FalseSignalEstimate",
    "FalseSignalEstimator",
    "GrenanderLocalFdr",
    "SignalEvidence",
    "clopper_pearson_upper",
    "false_signal_estimator",
    "permutation_pvalue",
    "register_false_signal_estimator",
]

#: The one-sided binomial confidence level the two WIDENED INPUTS are
#: read at, split evenly between them. It is emphatically NOT the
#: confidence of ``pi_widened`` — see the module docstring's measured
#: coverage table. Named ONCE: the value is a policy a project pins
#: prospectively, so it is a constructor knob everywhere and a literal
#: nowhere.
DEFAULT_WIDENING_LEVEL = 0.95

#: Storey's tail threshold: p-values above it are read as essentially
#: all null. The classic 0.5. Larger trades bias for variance.
DEFAULT_NULL_THRESHOLD = 0.5

#: Smallest family a two-groups model may be fitted to. A DECLARED
#: policy floor, the same kind of number as :data:`DEFAULT_NULL_THRESHOLD`
#: — not derived from the data. What IS derived is the direction: below
#: it the Grenander fit's first segment is a single order statistic, and
#: at ``m == 1`` it degenerates to ``f(p) = 1 / p``, which makes every
#: number a function of the scramble count rather than of the family.
DEFAULT_MIN_FAMILY = 10

#: Bisection depth for the binomial inversion. Far past float64's ~53
#: bits, so the returned limit no longer moves with the step count.
_BISECTION_STEPS = 100

#: Slack allowed before a member's density counts as RISING. Pure float
#: noise in a slope computed from differences; anything larger is the
#: member breaking its contract. A rise INSIDE the slack can still
#: invert ``pi_widened`` against ``pi_hat`` in the last bits, which is
#: why :class:`FalseSignalEstimate` screens the pair as well.
_MONOTONE_SLACK = 1e-12


def _check_count(value, name, minimum):
    """Refuse anything that is not a plain int at or above ``minimum``."""
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f"{name} must be an int >= {minimum}, got {value!r}")


def _check_open_unit(value, name):
    """Refuse anything that is not a finite number strictly inside (0, 1)."""
    if not number_ok(value) or not 0.0 < value < 1.0:
        raise ValueError(f"{name} must be a number in (0, 1), got {value!r}")


def _frozen(mapping):
    """Wrap a mapping read-only, freezing any mapping it holds too."""
    return MappingProxyType(
        {
            key: MappingProxyType(dict(value)) if isinstance(value, dict) else value
            for key, value in mapping.items()
        }
    )


@dataclass(frozen=True)
class SignalEvidence:
    """One signal's out-of-fold statistic beside its scramble null.

    Orientation is the package's: a LARGER statistic is stronger evidence
    against the null. A project whose statistic runs the other way
    negates both fields.

    Parameters
    ----------
    statistic : float
        The out-of-fold number, on any scale. Must be finite.
    null_draws : sequence of float
        The SAME statistic recomputed under scrambles, at least one, every
        value finite. Stored as a tuple of floats.

    Raises
    ------
    ValueError
        On a non-finite statistic, an empty draw set, or a draw that is
        not a finite number (named by index).

    Examples
    --------
    A signal that cleared its whole null::

        ev = SignalEvidence(2.4, [0.1, -0.3, 0.9])
        ev.null_draws  # (0.1, -0.3, 0.9)
    """

    statistic: float
    null_draws: tuple

    def __post_init__(self):
        """Refuse an unusable statistic or draw set at construction."""
        if not number_ok(self.statistic):
            raise ValueError(
                f"statistic must be a finite number, got {self.statistic!r}"
            )
        try:
            draws = tuple(self.null_draws)
        except TypeError:
            raise ValueError(
                f"null_draws must be a sequence of numbers, got {self.null_draws!r}"
            ) from None
        if not draws:
            raise ValueError("null_draws must hold at least one scramble statistic")
        for i, value in enumerate(draws):
            if not number_ok(value):
                raise ValueError(
                    f"null_draws[{i}] must be a finite number, got {value!r}"
                )
        object.__setattr__(self, "statistic", float(self.statistic))
        object.__setattr__(self, "null_draws", tuple(float(v) for v in draws))

    def exceedances(self):
        """Count the draws that match or beat the observed statistic.

        Returns
        -------
        int
            ``#{draw >= statistic}``. Ties count AGAINST the signal.
        """
        return sum(1 for value in self.null_draws if value >= self.statistic)


def permutation_pvalue(statistic, null_draws):
    """Compute the add-one permutation p-value against an empirical null.

    ``(1 + k) / (1 + B)`` for ``k`` draws matching or beating the
    statistic out of ``B`` (Phipson-Smyth). Never zero: a finite
    scramble cannot evidence an impossible null, which is the same
    reasoning ``attempts.early_stop_p_bound`` applies to a stopped
    audit. The ONE owner of the add-one rule in this module;
    :meth:`FalseSignalEstimator.estimate` calls it rather than
    restating it.

    Resolution floor: the smallest value it can return is
    ``1 / (1 + B)``, so two statistics that both beat every draw are
    indistinguishable however far apart they are.

    Parameters
    ----------
    statistic : float
        The observed out-of-fold statistic; larger is stronger evidence.
    null_draws : sequence of float
        The scramble draws of that same statistic, at least one.

    Returns
    -------
    float
        A p-value in ``(0, 1]``.

    Raises
    ------
    ValueError
        Through :class:`SignalEvidence`, which owns the input rules.

    Examples
    --------
    Nothing in a 99-draw null reached the signal::

        permutation_pvalue(4.0, [0.2] * 99)  # 0.01
    """
    evidence = SignalEvidence(statistic, null_draws)
    return (1 + evidence.exceedances()) / (1 + len(evidence.null_draws))


def clopper_pearson_upper(successes, trials, confidence):
    """Invert the binomial tail for its exact upper confidence limit.

    The limit ``U`` solving ``P(X <= successes | p = U) = 1 - confidence``
    for ``X ~ Binomial(trials, U)``; equivalently the ``confidence``
    quantile of ``Beta(successes + 1, trials - successes)``. Found by
    bisection on :func:`~dskit.pipeline.stats.regularized_incomplete_beta`,
    which is monotone, so the answer is deterministic. Accuracy is the
    bisection's and the beta tail's, not float representation's: the
    limit agrees with ``scipy.stats.beta.ppf`` to about ``1e-9``.

    Parameters
    ----------
    successes : int
        Observed successes, ``0 <= successes <= trials``.
    trials : int
        Number of Bernoulli draws, at least 1.
    confidence : float
        One-sided confidence in ``(0, 1)``.

    Returns
    -------
    float
        The upper limit in ``(0, 1]``; exactly ``1.0`` when every trial
        was a success.

    Raises
    ------
    ValueError
        On a non-int count, a negative count, ``successes > trials``, an
        empty trial set, or a confidence outside ``(0, 1)``.

    Examples
    --------
    Nothing observed in ten draws still admits a 26% rate::

        clopper_pearson_upper(0, 10, 0.95)  # 0.2589...
    """
    _check_count(trials, "trials", 1)
    _check_count(successes, "successes", 0)
    if successes > trials:
        raise ValueError(
            f"successes {successes!r} cannot exceed trials {trials!r}"
        )
    _check_open_unit(confidence, "confidence")
    if successes == trials:
        return 1.0
    shape_a = successes + 1.0
    shape_b = float(trials - successes)
    low, high = 0.0, 1.0
    for _ in range(_BISECTION_STEPS):
        mid = 0.5 * (low + high)
        if regularized_incomplete_beta(shape_a, shape_b, mid) < confidence:
            low = mid
        else:
            high = mid
    return 0.5 * (low + high)


@dataclass(frozen=True)
class FalseSignalEstimate:
    """A family's per-signal false-signal numbers, point and widened.

    The consumer contract lives HERE rather than in the caller: an
    instance cannot exist holding a ``pi_widened`` below its own
    ``pi_hat``, or a probability outside ``[0, 1]``, and the three
    mappings are wrapped read-only so a later assignment cannot break
    the pair either. A refusal names the ESTIMATOR that produced the
    pair, read out of ``evidence``, not just the field.

    ``pi_widened`` is not a confidence bound; read the module docstring
    before feeding it to anything that calls itself a chance constraint.

    Parameters
    ----------
    pi_hat : mapping of str -> float
        Point estimate per signal, in ``[0, 1]``. Stored read-only.
    pi_widened : mapping of str -> float
        The widened reading per signal, in ``[pi_hat, 1]``. Same keys.
        Stored read-only.
    evidence : mapping
        What produced the numbers: ``estimator``, ``widening_level``,
        ``prices``, ``unpriced``, ``independent_units``, ``pi0_hat``,
        ``pi0_upper``, ``pvalues``, ``pvalues_upper``, ``exceedances``,
        ``draws``. Stored read-only, one level deep.

    Raises
    ------
    ValueError
        On mismatched keys, a non-probability, or a widened reading
        below its own point.

    Examples
    --------
    Built by :meth:`FalseSignalEstimator.estimate`, never by hand::

        out = GrenanderLocalFdr().estimate(evidence, independent_units=12)
        out.pi_widened["alpha"] >= out.pi_hat["alpha"]  # True
    """

    pi_hat: dict
    pi_widened: dict
    evidence: dict

    def __post_init__(self):
        """Refuse any pair the downstream consumer contract would reject."""
        if set(self.pi_hat) != set(self.pi_widened):
            raise ValueError(
                "pi_hat and pi_widened must describe the same signals; "
                f"only in pi_hat: {sorted(set(self.pi_hat) - set(self.pi_widened))}, "
                f"only in pi_widened: {sorted(set(self.pi_widened) - set(self.pi_hat))}"
            )
        blame = dict(self.evidence).get("estimator", "<unknown estimator>")
        for name in sorted(self.pi_hat):
            hat, widened = self.pi_hat[name], self.pi_widened[name]
            for label, value in (("pi_hat", hat), ("pi_widened", widened)):
                if not number_ok(value) or not 0.0 <= value <= 1.0:
                    raise ValueError(
                        f"{blame} returned {label} {value!r} for {name!r}, which "
                        f"is not a finite number in [0, 1]"
                    )
            if hat > widened:
                raise ValueError(
                    f"{blame} returned pi_widened {widened!r} for {name!r}, "
                    f"below its own pi_hat {hat!r} — its density is not "
                    f"non-increasing to float precision"
                )
        object.__setattr__(self, "pi_hat", MappingProxyType(dict(self.pi_hat)))
        object.__setattr__(self, "pi_widened", MappingProxyType(dict(self.pi_widened)))
        object.__setattr__(self, "evidence", _frozen(self.evidence))


class FalseSignalEstimator(ABC):
    """The doorway: out-of-fold plus scramble evidence in, two rates out.

    ``estimate`` is a TEMPLATE method and CANNOT be overridden —
    ``__init_subclass__`` refuses a member that tries, at class-definition
    time, because the p-values, the two widened inputs, the screens and
    the ratio are not a member's decision. A member supplies only the
    estimator family, through three hooks. ``density`` carries one
    CONTRACT the template enforces rather than assumes: it must be
    non-increasing in ``p``, because that is what orders the widened
    reading above the point estimate.

    Parameters
    ----------
    widening_level : float
        One-sided binomial confidence level for the two widened inputs,
        in ``(0, 1)``. Split evenly (Bonferroni) between the per-signal
        Monte-Carlo limit and the family's null-share limit. NOT the
        confidence of ``pi_widened``, which is not a confidence bound.
    min_family : int
        Smallest family ``estimate`` will fit, at least 1. Default
        :data:`DEFAULT_MIN_FAMILY`.

    Examples
    --------
    A member supplies three hooks and inherits the rest::

        class FlatFdr(FalseSignalEstimator):
            def fit(self, pvalues):
                return len(pvalues)
            def null_proportion(self, state):
                return 0.5
            def density(self, state, p):
                return 1.0

        FlatFdr(widening_level=0.99).estimate(family, independent_units=4)
    """

    def __init__(
        self, *, widening_level=DEFAULT_WIDENING_LEVEL, min_family=DEFAULT_MIN_FAMILY
    ):
        """Pin the widening and family-size policies; refuse bad values."""
        _check_open_unit(widening_level, "widening_level")
        _check_count(min_family, "min_family", 1)
        self.widening_level = float(widening_level)
        self.min_family = int(min_family)

    def __init_subclass__(cls, **kwargs):
        """Refuse a member that overrides the template instead of the hooks."""
        super().__init_subclass__(**kwargs)
        if "estimate" in vars(cls):
            raise TypeError(
                f"{cls.__name__} overrides estimate, which is the TEMPLATE and "
                f"is never a member's decision — the p-values, the two widened "
                f"inputs and the screens belong to FalseSignalEstimator; supply "
                f"fit / null_proportion / density instead"
            )

    @abstractmethod
    def fit(self, pvalues):
        """Fit the family and return this member's own opaque state.

        Parameters
        ----------
        pvalues : dict of str -> float
            The family's point-estimate p-values, every one in ``(0, 1]``.

        Returns
        -------
        object
            Whatever :meth:`null_proportion` and :meth:`density` read.
        """

    @abstractmethod
    def null_proportion(self, state):
        """Return the share of the family that is null.

        Parameters
        ----------
        state : object
            What :meth:`fit` returned.

        Returns
        -------
        float
            ``pi0`` in ``(0, 1]``. Never zero: a finite family cannot
            evidence the absence of nulls, and a zero here would declare
            every signal real.
        """

    @abstractmethod
    def density(self, state, p):
        """Return the member's non-increasing density READ at ``p``.

        MUST be non-increasing in ``p`` — the two-groups model implies it
        (a uniform null plus stochastically smaller alternatives), and it
        is what orders the widened reading above the point estimate. The
        template screens every evaluated point and refuses a member that
        rises.

        It is a READ, not a normalized density: nothing here requires it
        to integrate to 1 over ``(0, 1]``, and :class:`GrenanderLocalFdr`
        does not.

        Parameters
        ----------
        state : object
            What :meth:`fit` returned.
        p : float
            Evaluation point in ``(0, 1]``.

        Returns
        -------
        float
            A finite value > 0.
        """

    def estimate(self, evidence, *, independent_units):
        """Estimate every signal's false-signal number, point and widened.

        Parameters
        ----------
        evidence : dict of str -> SignalEvidence
            The family. At least ``min_family`` entries; every value a
            :class:`SignalEvidence`.
        independent_units : int
            The count of EFFECTIVELY INDEPENDENT signals in the family,
            in ``[1, len(evidence)]``. Required and never defaulted:
            signals fitted on overlapping time are not independent, and
            assuming they are understates the null-share limit.

        Returns
        -------
        FalseSignalEstimate
            ``pi_hat`` and ``pi_widened`` per signal, with the evidence.
            ``pi_widened`` is a widened point estimate, not a bound.

        Raises
        ------
        ValueError
            On a family below ``min_family``, a member that is not
            :class:`SignalEvidence`, an out-of-range ``independent_units``,
            a hook that returns an unusable null share or density, or a
            density that rises — each naming its offender.

        Examples
        --------
        Twelve signals audited against a 999-draw session scramble::

            out = GrenanderLocalFdr().estimate(family, independent_units=12)
            out.pi_hat["a"] <= out.pi_widened["a"]  # True
        """
        names = self._check_family(evidence, independent_units)
        tail_confidence = 1.0 - 0.5 * (1.0 - self.widening_level)
        counts = {name: evidence[name].exceedances() for name in names}
        draws = {name: len(evidence[name].null_draws) for name in names}
        p_hat = {
            n: permutation_pvalue(
                evidence[n].statistic, evidence[n].null_draws
            )
            for n in names
        }
        # Both readings of the same count are conservative: the add-one
        # p-value is Phipson-Smyth's exact valid p-value, and the
        # Clopper-Pearson limit bounds the scramble's Monte-Carlo error.
        # The max of two valid upper readings is a valid upper reading,
        # and it keeps the widened number above its own point estimate.
        p_upper = {
            n: max(
                p_hat[n],
                clopper_pearson_upper(counts[n], draws[n], tail_confidence),
            )
            for n in names
        }
        state = self.fit(dict(p_hat))
        pi0_hat = self._null_share(state)
        pi0_upper = self._null_share_upper(
            pi0_hat, independent_units, tail_confidence
        )
        f_hat = {n: self._density(state, p_hat[n]) for n in names}
        f_upper = {n: self._density(state, p_upper[n]) for n in names}
        self._check_monotone(
            [(p_hat[n], f_hat[n]) for n in names]
            + [(p_upper[n], f_upper[n]) for n in names]
        )
        return FalseSignalEstimate(
            pi_hat={n: min(1.0, pi0_hat / f_hat[n]) for n in names},
            pi_widened={n: min(1.0, pi0_upper / f_upper[n]) for n in names},
            evidence={
                "estimator": class_ref(type(self)),
                "widening_level": self.widening_level,
                "prices": ("scramble_monte_carlo", "null_share_binomial"),
                "unpriced": ("density_estimation",),
                "min_family": self.min_family,
                "independent_units": independent_units,
                "pi0_hat": pi0_hat,
                "pi0_upper": pi0_upper,
                "pvalues": dict(p_hat),
                "pvalues_upper": dict(p_upper),
                "exceedances": dict(counts),
                "draws": dict(draws),
            },
        )

    def _check_family(self, evidence, independent_units):
        """Grade the family and its declared independence; return its names."""
        if not isinstance(evidence, dict) or not evidence:
            raise ValueError(
                "evidence must be a non-empty mapping of signal -> "
                f"SignalEvidence; got an empty or non-mapping {type(evidence).__name__}"
            )
        names = sorted(evidence)
        for name in names:
            if not isinstance(evidence[name], SignalEvidence):
                raise ValueError(
                    f"evidence for {name!r} must be a SignalEvidence, got "
                    f"{type(evidence[name]).__name__}"
                )
        if len(names) < self.min_family:
            raise ValueError(
                f"a two-groups model needs at least min_family "
                f"{self.min_family} signals to be fitted to a FAMILY rather "
                f"than to the scramble count, got {len(names)}"
            )
        _check_count(independent_units, "independent_units", 1)
        if independent_units > len(names):
            raise ValueError(
                f"independent_units {independent_units!r} exceeds the "
                f"family size {len(names)} — a family cannot hold more "
                f"independent signals than signals"
            )
        return names

    def _null_share(self, state):
        """Read the member's null share, refusing one outside (0, 1]."""
        pi0 = self.null_proportion(state)
        if not number_ok(pi0) or not 0.0 < pi0 <= 1.0:
            raise ValueError(
                f"null_proportion must return a finite number in (0, 1], got "
                f"{pi0!r} — a zero null share would declare every signal real"
            )
        return float(pi0)

    @staticmethod
    def _null_share_upper(pi0_hat, independent_units, tail_confidence):
        """Widen the null share to its limit at the declared unit count."""
        # ceil, never round: rounding DOWN drops up to half a unit of
        # null share and the error never cancels, while the min() below
        # already caps the count at the units available.
        observed = math.ceil(pi0_hat * independent_units)
        limit = clopper_pearson_upper(
            min(observed, independent_units), independent_units, tail_confidence
        )
        return min(1.0, max(pi0_hat, limit))

    def _density(self, state, p):
        """Read the member's density, refusing a non-positive or wild one."""
        value = self.density(state, p)
        if not number_ok(value) or value <= 0.0:
            raise ValueError(
                f"density at p={p!r} must be a finite number > 0, got {value!r}"
            )
        return float(value)

    @staticmethod
    def _check_monotone(points):
        """Refuse a member whose density RISES across the evaluated points."""
        ordered = sorted(points)
        for (p_low, f_low), (p_high, f_high) in zip(ordered, ordered[1:]):
            if f_high > f_low + _MONOTONE_SLACK:
                raise ValueError(
                    f"density must be non-increasing in p, but rose from "
                    f"{f_low!r} at p={p_low!r} to {f_high!r} at p={p_high!r} — "
                    f"the widened reading is not ordered without it"
                )


@dataclass(frozen=True)
class _GrenanderFit:
    """A fitted decreasing density: hull breakpoints, slopes, null share."""

    breaks: tuple
    slopes: tuple
    pi0: float


class GrenanderLocalFdr(FalseSignalEstimator):
    """Two-groups local FDR: Storey's null share over a Grenander density.

    The p-value density under the two-groups model is DECREASING (a
    uniform null plus stochastically smaller alternatives), so the
    nonparametric MLE of it is the Grenander estimator — the slopes of
    the least concave majorant of the empirical CDF. It is chosen over a
    kernel or spline fit because it has no tuning constant at all: a
    bandwidth would be a number governing the answer that no config
    author can choose well, and this package does not hardcode what
    could change.

    Two known limits, disclosed rather than hidden. The estimator is not
    consistent at the left boundary (Woodroofe-Sun): it over-estimates
    ``f`` near zero, which biases ``pi_hat`` LOW for the strongest signal
    in the family — the one that gets capital. And its density is a STEP
    function over order statistics, so the ratio ``pi_widened / pi_hat``
    jumps by whatever the p-value spacing happens to be, not by anything
    proportional to an error budget.

    ``pi0`` is Storey's ``#{p > null_threshold} / (m * (1 - null_threshold))``,
    capped at 1 and floored at ``1 / (m + 1)`` — ``m`` observations can
    never evidence ZERO nulls, the same add-one reasoning as the
    permutation p-value.

    Parameters
    ----------
    widening_level : float
        As :class:`FalseSignalEstimator`.
    min_family : int
        As :class:`FalseSignalEstimator`.
    null_threshold : float
        Storey's tail threshold in ``(0, 1)``; p-values above it are read
        as essentially all null. Default
        :data:`DEFAULT_NULL_THRESHOLD`.

    Examples
    --------
    Score a family whose scrambles have already been run::

        est = GrenanderLocalFdr(widening_level=0.95, null_threshold=0.5)
        out = est.estimate(family, independent_units=12)
        out.pi_hat, out.pi_widened
        # -> two read-only maps keyed by signal, every widened number at
        #    or above its point estimate
    """

    def __init__(
        self,
        *,
        widening_level=DEFAULT_WIDENING_LEVEL,
        min_family=DEFAULT_MIN_FAMILY,
        null_threshold=DEFAULT_NULL_THRESHOLD,
    ):
        """Pin the widening, family-size and threshold policies."""
        super().__init__(widening_level=widening_level, min_family=min_family)
        _check_open_unit(null_threshold, "null_threshold")
        self.null_threshold = float(null_threshold)

    def fit(self, pvalues):
        """Fit the concave majorant and Storey's null share.

        Public and callable on its own, so it grades its own input
        rather than trusting the template's.

        Parameters
        ----------
        pvalues : dict of str -> float
            The family's p-values, every one in ``(0, 1]``.

        Returns
        -------
        object
            The fitted state :meth:`null_proportion` and :meth:`density`
            read: hull breakpoints, their slopes, and the null share.

        Raises
        ------
        ValueError
            On an empty family or any p-value outside ``(0, 1]``.

        Examples
        --------
        Four p-values, two of them collinear on the majorant::

            fit = GrenanderLocalFdr().fit(
                {"a": 0.1, "b": 0.2, "c": 0.4, "d": 1.0}
            )
            fit.breaks
            # -> (0.2, 0.4, 1.0)
        """
        ordered = sorted(pvalues.values())
        size = len(ordered)
        if not ordered or not 0.0 < ordered[0] or ordered[-1] > 1.0:
            raise ValueError(
                f"every p-value must lie in (0, 1], but the family spans "
                f"{ordered[:1]} to {ordered[-1:]}"
            )
        hull = self._majorant(ordered, size)
        breaks = tuple(x for x, _ in hull[1:])
        slopes = tuple(
            (hull[i + 1][1] - hull[i][1]) / (hull[i + 1][0] - hull[i][0])
            for i in range(len(hull) - 1)
        )
        above = sum(1 for p in ordered if p > self.null_threshold)
        share = above / (size * (1.0 - self.null_threshold))
        return _GrenanderFit(
            breaks=breaks,
            slopes=slopes,
            pi0=min(1.0, max(share, 1.0 / (size + 1))),
        )

    @staticmethod
    def _majorant(ordered, size):
        """Build the least concave majorant of the ECDF, as hull vertices."""
        points = [(0.0, 0.0)]
        for rank, p in enumerate(ordered, start=1):
            height = rank / size
            if p == points[-1][0]:
                points[-1] = (p, height)
            else:
                points.append((p, height))
        hull = []
        for point in points:
            # `<=` pops a COLLINEAR vertex too: a majorant with a
            # redundant breakpoint would report two segments where the
            # density has one value.
            while len(hull) >= 2 and _slope(hull[-2], hull[-1]) <= _slope(
                hull[-1], point
            ):
                hull.pop()
            hull.append(point)
        return hull

    def null_proportion(self, state):
        """Storey's capped, floored null share, fitted with the majorant.

        Parameters
        ----------
        state : object
            What :meth:`fit` returned.

        Returns
        -------
        float
            ``pi0`` in ``(0, 1]``.
        """
        return state.pi0

    def density(self, state, p):
        """Read the majorant's slope over the segment holding ``p``.

        Non-increasing by construction: the majorant is concave, so its
        slopes descend. LEFT-continuous — a ``p`` sitting exactly on a
        breakpoint reads the segment ENDING there, which is the
        Grenander estimator's usual convention.

        Two documented extensions past the fitted range. A ``p`` below
        the smallest fitted p-value reads the first slope, which is
        ``F(p_(1)) / p_(1)`` and grows without bound as ``p_(1)``
        shrinks. A ``p`` above the largest reads the FINAL slope rather
        than the majorant's zero, so the read stays positive and the
        ratio stays defined; the price is that the implied mass over
        ``(0, 1]`` can exceed 1, which is why this is a density READ and
        not a normalized density.

        Parameters
        ----------
        state : object
            What :meth:`fit` returned.
        p : float
            Evaluation point.

        Returns
        -------
        float
            The estimated density at ``p``, always > 0.
        """
        index = bisect.bisect_left(state.breaks, p)
        if index >= len(state.slopes):
            return state.slopes[-1]
        return state.slopes[index]


def _slope(low, high):
    """Slope of the segment between two hull points."""
    return (high[1] - low[1]) / (high[0] - low[0])


#: The registry a config's false-signal-estimator field resolves
#: against: ``name -> {"cls", "doc"}``. Mirrors
#: :data:`~dskit.pipeline.stats.CORRECTIONS` — a project brings its own
#: family without editing this package. Named for its SUBJECT, like
#: every sibling registry here, and never the bare word ``estimator``,
#: which `libs/sklearn.py` already owns for a model dotted path.
FALSE_SIGNAL_ESTIMATORS: dict = {}


def register_false_signal_estimator(name, cls, *, doc=""):
    """Register a false-signal estimator family under ``name``.

    Parameters
    ----------
    name : str
        The registry key, a non-empty string.
    cls : type
        A :class:`FalseSignalEstimator` subclass — the registry holds
        CLASSES, because the family is an object with hooks, not a
        function.
    doc : str
        One-line description for error messages and docs.

    Raises
    ------
    ValueError
        On an empty name, a class that is not a
        :class:`FalseSignalEstimator` subclass, or a name already taken.

    Examples
    --------
    A project brings its own family::

        register_false_signal_estimator(
            "flat-fdr", FlatFdr, doc="A constant density, for testing."
        )
    """
    if not isinstance(name, str) or not name:
        raise ValueError(
            f"false-signal estimator name must be a non-empty string, got {name!r}"
        )
    if not isinstance(cls, type) or not issubclass(cls, FalseSignalEstimator):
        raise ValueError(
            f"false-signal estimator {name!r} must be a FalseSignalEstimator "
            f"subclass, got {cls!r}"
        )
    if name in FALSE_SIGNAL_ESTIMATORS:
        raise ValueError(
            f"false-signal estimator {name!r} is already registered"
        )
    FALSE_SIGNAL_ESTIMATORS[name] = {"cls": cls, "doc": doc}


def false_signal_estimator(name):
    """Look up a registered false-signal estimator entry, loudly.

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
        When nothing is registered under ``name``; the message lists what is.

    Examples
    --------
    The shipping family::

        false_signal_estimator("grenander-local-fdr")["cls"]
        # -> <class 'dskit.pipeline.false_signal.GrenanderLocalFdr'>
    """
    try:
        return FALSE_SIGNAL_ESTIMATORS[name]
    except KeyError:
        raise ValueError(
            f"unknown false-signal estimator {name!r} — known: "
            f"{sorted(FALSE_SIGNAL_ESTIMATORS)}"
        ) from None


register_false_signal_estimator(
    "grenander-local-fdr",
    GrenanderLocalFdr,
    doc="Two-groups local FDR: Storey null share over a Grenander density.",
)
