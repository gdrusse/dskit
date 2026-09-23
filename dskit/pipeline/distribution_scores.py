"""Proper scores and calibration tests for SAMPLE-SET distribution forecasts.

A distribution forecast here is a finite list of draws per record (ADR-0168).
Every score reads the draws' empirical CDF, so any model that can emit draws
— a location-scale bootstrap, a simulated GARCH path set, a quantile grid —
is scored on one scale without a second representation.

The scores are the forecast-evaluation literature's, not inventions:

* CRPS, the integral of the Brier score over every threshold (Gneiting and
  Raftery 2007). For an empirical CDF it has the closed "energy" form
  ``E|X - y| - E|X - X'| / 2``, which :class:`Crps` uses.
* threshold-weighted CRPS (Gneiting and Ranjan 2011): the same integral
  restricted to declared intervals — the region a decision actually reads.
  Integrated EXACTLY over the step CDF, never by quadrature, so a test can
  pin it against :class:`Crps` on the whole line.
* the Brier score at declared thresholds, the discrete view of the same
  integrand.
* the probability integral transform (PIT) with a Kolmogorov–Smirnov test
  of uniformity (Diebold, Gunther and Tay 1998), and Berkowitz's (2001)
  likelihood-ratio test of the normal-transformed PIT for bias, scale and
  first-order dependence.

Lower is better for every scoring rule. Stdlib only — ``statistics`` owns
the normal distribution — so a document scoring distributions plans on a
machine with nothing installed.
"""

from __future__ import annotations

import bisect
import math
from abc import ABC, abstractmethod
from statistics import NormalDist
from types import SimpleNamespace

from dskit.pipeline.records import cluster_of
from dskit.pipeline.node import JsonArtifact, Node, check_int_param, reject_unknown_params
from dskit.pipeline.split_policy import SPLIT_NAMES

__all__ = [
    "BerkowitzTest",
    "CalibrationTest",
    "Crps",
    "DEFAULT_OUTCOME_FIELD",
    "DEFAULT_PIT_BINS",
    "DEFAULT_SAMPLES_FIELD",
    "PitCalibration",
    "SampleDistribution",
    "ScoreDistributions",
    "ScoringRule",
    "ThresholdBrier",
    "ThresholdWeightedCrps",
    "row_in_split",
]

#: The row field carrying a forecast's draws, unless a document says otherwise.
DEFAULT_SAMPLES_FIELD = "samples"

#: The row field carrying the realized value, unless a document says otherwise.
DEFAULT_OUTCOME_FIELD = "outcome"

#: PIT histogram resolution, unless a document says otherwise.
DEFAULT_PIT_BINS = 10

_STANDARD_NORMAL = NormalDist()


def _finite(value):
    """Whether ``value`` is a real, finite, non-boolean number."""
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(value)
    )


def row_in_split(ctx, row, split):
    """Say whether a forecast row belongs to ``split``.

    The one owner of that question for forecast-row consumers: the row's
    instant is ``asof_ms`` and its cluster is :func:`records.cluster_of`'s
    answer, so a dict row and an envelope cut identically.

    Parameters
    ----------
    ctx : NodeContext or None
        The run frame; with no splits every row belongs to every split.
    row : dict
        A forecast row.
    split : str
        A split name.

    Returns
    -------
    bool
        Whether the row is in ``split``.
    """
    if ctx is None or ctx.splits is None:
        return True
    frame = SimpleNamespace(asof_ms=row.get("asof_ms"), cluster=cluster_of(row))
    return ctx.splits.split_of(frame) == split


class SampleDistribution:
    """The empirical distribution of a finite set of draws.

    Parameters
    ----------
    samples : sequence of float
        At least one finite draw. Order is irrelevant; the draws are sorted
        once at construction.

    Examples
    --------
    Three draws, their CDF, and the PIT of an outcome::

        dist = SampleDistribution([0.0, 1.0, 2.0])
        dist.cdf(1.0)   # 0.6666666666666666
        dist.pit(1.5)   # 0.625
    """

    def __init__(self, samples):
        if isinstance(samples, (str, bytes)) or not hasattr(samples, "__iter__"):
            raise ValueError(f"samples must be a sequence of numbers, got {samples!r}")
        values = list(samples)
        if not values:
            raise ValueError("samples must hold at least one draw")
        bad = [v for v in values if not _finite(v)]
        if bad:
            raise ValueError(f"samples must be finite numbers, got {bad[:3]!r}")
        self.samples = tuple(sorted(float(v) for v in values))

    def __len__(self):
        """Count the draws."""
        return len(self.samples)

    def cdf(self, x):
        """Evaluate the right-continuous empirical CDF.

        Parameters
        ----------
        x : float
            The threshold.

        Returns
        -------
        float
            The fraction of draws ``<= x``.
        """
        return bisect.bisect_right(self.samples, x) / len(self.samples)

    def quantile(self, p):
        """Return the lower empirical quantile.

        Parameters
        ----------
        p : float
            A probability in ``[0, 1]``.

        Returns
        -------
        float
            The smallest draw whose CDF reaches ``p``.

        Raises
        ------
        ValueError
            When ``p`` is outside ``[0, 1]``.
        """
        if not _finite(p) or not 0.0 <= p <= 1.0:
            raise ValueError(f"p must be in [0, 1], got {p!r}")
        index = max(0, math.ceil(p * len(self.samples)) - 1)
        return self.samples[index]

    def pit(self, y):
        """Return the probability integral transform of an outcome.

        Uses ``(#{x <= y} + 0.5) / (n + 1)``, which stays strictly inside
        ``(0, 1)`` so the normal transform of :class:`BerkowitzTest` is
        always finite, and tends to the CDF as ``n`` grows.

        Parameters
        ----------
        y : float
            The realized value.

        Returns
        -------
        float
            The PIT value in ``(0, 1)``.
        """
        count = bisect.bisect_right(self.samples, y)
        return (count + 0.5) / (len(self.samples) + 1)


class ScoringRule(ABC):
    """A negatively oriented score of one forecast against one outcome.

    Subclasses supply :meth:`score`; lower is better.

    Examples
    --------
    Every rule is used the same way::

        rule = Crps()
        rule.score(SampleDistribution([0.0, 1.0]), 0.5)   # 0.25
    """

    #: The metric name the rule reports under.
    name = ""

    @abstractmethod
    def score(self, dist, y):
        """Score one forecast.

        Parameters
        ----------
        dist : SampleDistribution
            The forecast.
        y : float
            The realized value.

        Returns
        -------
        float
            The score; lower is better.
        """


class Crps(ScoringRule):
    """The continuous ranked probability score of an empirical CDF.

    ``E|X - y| - E|X - X'| / 2`` with ``X, X'`` drawn from the samples,
    in ``O(n)`` over the sorted draws.

    Examples
    --------
    A two-point forecast scored at its midpoint::

        Crps().score(SampleDistribution([0.0, 1.0]), 0.5)   # 0.25
    """

    name = "crps"

    def score(self, dist, y):
        """Score one forecast; see :meth:`ScoringRule.score`."""
        xs = dist.samples
        n = len(xs)
        spread = sum((2 * i - n + 1) * x for i, x in enumerate(xs))
        return sum(abs(x - y) for x in xs) / n - spread / (n * n)


class ThresholdWeightedCrps(ScoringRule):
    """CRPS restricted to declared threshold intervals (indicator weight).

    ``sum over intervals of the integral over [lo, hi] of
    (F(z) - 1{y <= z})^2 dz``, integrated exactly over the step CDF.

    Parameters
    ----------
    intervals : sequence of [lo, hi]
        Disjoint intervals with ``lo < hi``; either end may be infinite.

    Examples
    --------
    Score only the two tails a symmetric spread reads::

        rule = ThresholdWeightedCrps([[-2.5, -0.5], [0.5, 2.5]])
        rule.score(SampleDistribution([-1.0, 0.0, 1.0]), 0.2)
    """

    name = "twcrps"

    def __init__(self, intervals):
        self.intervals = self.checked_intervals(intervals)

    @staticmethod
    def interval_problems(intervals):
        """List what is wrong with an interval declaration.

        Parameters
        ----------
        intervals : object
            The candidate declaration.

        Returns
        -------
        list of str
            Empty when the declaration is usable.
        """
        if not isinstance(intervals, (list, tuple)) or not intervals:
            return [f"intervals must be a non-empty list of [lo, hi], got {intervals!r}"]
        problems = []
        for pair in intervals:
            ok = isinstance(pair, (list, tuple)) and len(pair) == 2 and all(
                not isinstance(v, bool) and isinstance(v, (int, float))
                and not math.isnan(v) for v in pair
            )
            if not ok or not pair[0] < pair[1]:
                problems.append(f"each interval must be [lo, hi] with lo < hi, got {pair!r}")
        if not problems:
            ordered = sorted((float(a), float(b)) for a, b in intervals)
            if any(prev[1] > nxt[0] for prev, nxt in zip(ordered, ordered[1:])):
                problems.append(f"intervals must not overlap, got {intervals!r}")
        return problems

    @classmethod
    def checked_intervals(cls, intervals):
        """Return the intervals as sorted float pairs, or refuse.

        Parameters
        ----------
        intervals : sequence of [lo, hi]
            The declaration.

        Returns
        -------
        tuple of (float, float)
            Sorted, validated intervals.

        Raises
        ------
        ValueError
            When :meth:`interval_problems` reports any problem.
        """
        problems = cls.interval_problems(intervals)
        if problems:
            raise ValueError("; ".join(problems))
        return tuple(sorted((float(a), float(b)) for a, b in intervals))

    def score(self, dist, y):
        """Score one forecast; see :meth:`ScoringRule.score`."""
        points = sorted(set(dist.samples) | {float(y)})
        total = 0.0
        for left, right in zip(points, points[1:]):
            gap = (dist.cdf(left) - (1.0 if y <= left else 0.0)) ** 2
            if gap:
                total += gap * self._covered(left, right)
        return total

    def _covered(self, left, right):
        """Length of ``[left, right]`` inside the declared intervals."""
        return sum(
            max(0.0, min(right, hi) - max(left, lo)) for lo, hi in self.intervals
        )


class ThresholdBrier(ScoringRule):
    """The mean Brier score of the CDF at declared thresholds.

    Parameters
    ----------
    thresholds : sequence of float
        At least one finite threshold.

    Examples
    --------
    The events ``y <= -1`` and ``y <= 1``::

        ThresholdBrier([-1.0, 1.0]).score(SampleDistribution([0.0, 2.0]), 0.5)
    """

    name = "brier"

    def __init__(self, thresholds):
        values = list(thresholds) if isinstance(thresholds, (list, tuple)) else []
        if not values or any(not _finite(t) for t in values):
            raise ValueError(
                f"thresholds must be a non-empty list of finite numbers, got {thresholds!r}"
            )
        self.thresholds = tuple(float(t) for t in values)

    def score(self, dist, y):
        """Score one forecast; see :meth:`ScoringRule.score`."""
        return sum(
            (dist.cdf(t) - (1.0 if y <= t else 0.0)) ** 2 for t in self.thresholds
        ) / len(self.thresholds)

    def per_threshold(self, dist, y):
        """Return the Brier score at each threshold.

        Parameters
        ----------
        dist : SampleDistribution
            The forecast.
        y : float
            The realized value.

        Returns
        -------
        list of float
            One score per threshold, in declaration order.
        """
        return [(dist.cdf(t) - (1.0 if y <= t else 0.0)) ** 2 for t in self.thresholds]


class CalibrationTest(ABC):
    """A test of a sequence of PIT values against U(0, 1).

    Examples
    --------
    Every test is used the same way::

        result = PitCalibration(10).evaluate([0.1, 0.5, 0.9])
        result["pvalue"]
    """

    #: The prefix the test's metrics report under.
    name = ""

    @abstractmethod
    def evaluate(self, pits):
        """Test the PIT values, in forecast order.

        Parameters
        ----------
        pits : sequence of float
            PIT values in ``(0, 1)``.

        Returns
        -------
        dict
            At least ``statistic`` and ``pvalue`` (``None`` when too few).
        """


class PitCalibration(CalibrationTest):
    """PIT histogram and a Kolmogorov–Smirnov test of uniformity.

    The p-value is Stephens' asymptotic approximation. It assumes
    independent PITs; overlapping-horizon forecasts are dependent, so read
    it as a diagnostic unless the rows are non-overlapping.

    Parameters
    ----------
    n_bins : int
        Histogram bins (>= 2).

    Examples
    --------
    Ten equal bins::

        PitCalibration(10).evaluate([0.05, 0.15, 0.95])["histogram"]
    """

    name = "pit_ks"

    def __init__(self, n_bins=DEFAULT_PIT_BINS):
        problems = []
        check_int_param(problems, "n_bins", n_bins, ge=2)
        if problems:
            raise ValueError("; ".join(problems))
        self.n_bins = n_bins

    def evaluate(self, pits):
        """Test the PIT values; see :meth:`CalibrationTest.evaluate`."""
        us = sorted(pits)
        m = len(us)
        histogram = [0] * self.n_bins
        for u in us:
            histogram[min(int(u * self.n_bins), self.n_bins - 1)] += 1
        if m == 0:
            return {"statistic": None, "pvalue": None, "histogram": histogram}
        stat = max(max((i + 1) / m - u, u - i / m) for i, u in enumerate(us))
        return {"statistic": stat, "pvalue": self._pvalue(stat, m), "histogram": histogram}

    @staticmethod
    def _pvalue(stat, m):
        """Stephens' asymptotic Kolmogorov survival probability."""
        lam = (math.sqrt(m) + 0.12 + 0.11 / math.sqrt(m)) * stat
        if lam < 1e-3:
            return 1.0
        total = sum(
            (-1) ** (k - 1) * math.exp(-2.0 * k * k * lam * lam) for k in range(1, 101)
        )
        return min(1.0, max(0.0, 2.0 * total))


class BerkowitzTest(CalibrationTest):
    """Berkowitz's LR test: normal-transformed PITs are iid N(0, 1).

    Fits ``z_t = c + rho * z_{t-1} + e_t``, ``e ~ N(0, sigma^2)`` by
    conditional maximum likelihood and compares it with ``c = 0, rho = 0,
    sigma = 1``; the statistic is chi-squared with 3 degrees of freedom.

    Examples
    --------
    PITs in forecast order::

        BerkowitzTest().evaluate([0.2, 0.7, 0.4, 0.9, 0.1])["pvalue"]
    """

    name = "berkowitz"

    #: Fewer PITs than this cannot identify the three AR(1) parameters.
    MIN_PITS = 4

    def evaluate(self, pits):
        """Test the PIT values; see :meth:`CalibrationTest.evaluate`."""
        z = [_STANDARD_NORMAL.inv_cdf(u) for u in pits]
        if len(z) < self.MIN_PITS:
            return {"statistic": None, "pvalue": None}
        fit = self._ar1(z[:-1], z[1:])
        if fit is None:
            return {"statistic": None, "pvalue": None}
        restricted = sum(-0.5 * (v * v + math.log(2 * math.pi)) for v in z[1:])
        stat = max(0.0, -2.0 * (restricted - fit.loglik))
        return {
            "statistic": stat, "pvalue": self._chi2_sf3(stat),
            "intercept": fit.c, "rho": fit.rho, "sigma": fit.sigma,
        }

    @staticmethod
    def _ar1(x, y):
        """OLS AR(1) fit with its Gaussian log-likelihood, or ``None`` if degenerate."""
        n = len(y)
        mx, my = sum(x) / n, sum(y) / n
        sxx = sum((a - mx) ** 2 for a in x)
        if sxx <= 0.0:
            return None
        rho = sum((a - mx) * (b - my) for a, b in zip(x, y)) / sxx
        c = my - rho * mx
        var = sum((b - c - rho * a) ** 2 for a, b in zip(x, y)) / n
        if var <= 0.0:
            return None
        loglik = -0.5 * n * (math.log(2 * math.pi * var) + 1.0)
        return SimpleNamespace(c=c, rho=rho, sigma=math.sqrt(var), loglik=loglik)

    @staticmethod
    def _chi2_sf3(x):
        """Chi-squared survival function with 3 degrees of freedom."""
        root = math.sqrt(x)
        tail = 2.0 * (1.0 - _STANDARD_NORMAL.cdf(root))
        return min(1.0, tail + math.sqrt(2.0 * x / math.pi) * math.exp(-x / 2.0))


class ScoreDistributions(Node):
    """Score sample-set forecast rows on one split (role ``score``).

    Reads ``forecasts`` rows carrying draws and a realized outcome, keeps
    the rows in the declared ``split``, and reports mean CRPS, mean
    threshold-weighted CRPS over ``weight_intervals``, the Brier score at
    ``thresholds`` (when declared), and the PIT/Berkowitz tests. Rows with
    no outcome yet, or no forecast (``samples`` is ``None``), are counted
    and skipped, never scored as zero.

    Parameters
    ----------
    params : dict
        ``split`` (required), ``weight_intervals`` (required list of
        ``[lo, hi]``), ``thresholds`` (list of float, optional),
        ``samples_field`` (default ``"samples"``), ``outcome_field``
        (default ``"outcome"``), ``pit_bins`` (int >= 2, default 10),
        ``min_rows`` (int >= 1, default 1).

    Examples
    --------
    Score the val split in the tails a symmetric spread reads::

        node = ScoreDistributions("score", {
            "split": "val", "weight_intervals": [[-2.5, -0.5], [0.5, 2.5]],
            "thresholds": [-1.5, -1.0, 1.0, 1.5],
        })
        out = node.run(ctx, {"forecasts": rows})
        # -> {"metrics": {"crps": ..., "twcrps": ..., ...}, "report": JsonArtifact}
    """

    role = "score"
    outputs = ("metrics", "report")
    _PARAMS = ("min_rows", "outcome_field", "pit_bins", "samples_field", "split",
               "thresholds", "weight_intervals")

    @classmethod
    def validate_params(cls, params):
        """List the problems with ``params``.

        Parameters
        ----------
        params : dict
            The node's declared params.

        Returns
        -------
        list of str
            One problem per broken knob.
        """
        problems = []
        reject_unknown_params(problems, params, cls._PARAMS)
        if params.get("split") not in SPLIT_NAMES:
            problems.append(
                f"split must name one of {list(SPLIT_NAMES)}, got {params.get('split')!r}"
            )
        problems.extend(ThresholdWeightedCrps.interval_problems(params.get("weight_intervals")))
        if "thresholds" in params:
            try:
                ThresholdBrier(params["thresholds"])
            except ValueError as exc:
                problems.append(str(exc))
        for knob in ("samples_field", "outcome_field"):
            value = params.get(knob, "x")
            if not isinstance(value, str) or not value:
                problems.append(f"{knob} must be a non-empty string, got {value!r}")
        check_int_param(problems, "pit_bins", params.get("pit_bins", DEFAULT_PIT_BINS), ge=2)
        check_int_param(problems, "min_rows", params.get("min_rows", 1), ge=1)
        return problems

    def validate_inputs(self, inputs):
        """Require ``forecasts`` to be a list of rows.

        Parameters
        ----------
        inputs : dict
            The wired inputs.

        Returns
        -------
        list of str
            Empty when usable.
        """
        if not isinstance(inputs.get("forecasts"), list):
            return ["forecasts must be a list of forecast rows"]
        return []

    def rules(self):
        """Build the scoring rules this node reports.

        Returns
        -------
        list of ScoringRule
            CRPS, threshold-weighted CRPS, and Brier when thresholds exist.
        """
        rules = [Crps(), ThresholdWeightedCrps(self.params["weight_intervals"])]
        if "thresholds" in self.params:
            rules.append(ThresholdBrier(self.params["thresholds"]))
        return rules

    def calibration_tests(self):
        """Build the PIT tests this node reports.

        Returns
        -------
        list of CalibrationTest
            The KS histogram test and the Berkowitz test.
        """
        return [PitCalibration(self.params.get("pit_bins", DEFAULT_PIT_BINS)), BerkowitzTest()]

    def _scored_rows(self, ctx, rows):
        """Pair each in-split row with its distribution; count what was skipped."""
        samples_field = self.params.get("samples_field", DEFAULT_SAMPLES_FIELD)
        outcome_field = self.params.get("outcome_field", DEFAULT_OUTCOME_FIELD)
        pairs, skipped = [], {"other_split": 0, "no_outcome": 0, "no_forecast": 0}
        for row in rows:
            if not row_in_split(ctx, row, self.params["split"]):
                skipped["other_split"] += 1
            elif not _finite(row.get(outcome_field)):
                skipped["no_outcome"] += 1
            elif row.get(samples_field) is None:
                skipped["no_forecast"] += 1
            else:
                pairs.append((SampleDistribution(row[samples_field]), row[outcome_field]))
        return pairs, skipped

    def run(self, ctx, inputs):
        """Score the in-split rows.

        Parameters
        ----------
        ctx : NodeContext or None
            Its ``splits`` decide membership.
        inputs : dict
            ``forecasts``: rows with draws and outcomes.

        Returns
        -------
        dict
            ``metrics`` (numbers) and ``report`` (a JsonArtifact).

        Raises
        ------
        ValueError
            When fewer than ``min_rows`` rows are scorable.
        """
        pairs, skipped = self._scored_rows(ctx, inputs["forecasts"])
        floor = self.params.get("min_rows", 1)
        if len(pairs) < floor:
            raise ValueError(
                f"{self.key}: {len(pairs)} scorable row(s) in split "
                f"{self.params['split']!r}, below min_rows={floor}"
            )
        metrics = {"n": len(pairs), **{f"n_skipped_{k}": v for k, v in skipped.items()}}
        report = {"split": self.params["split"], "skipped": skipped}
        for rule in self.rules():
            metrics[rule.name] = sum(rule.score(d, y) for d, y in pairs) / len(pairs)
            if isinstance(rule, ThresholdBrier):
                per = [rule.per_threshold(d, y) for d, y in pairs]
                report["brier_by_threshold"] = dict(zip(
                    map(str, rule.thresholds), (sum(col) / len(per) for col in zip(*per)),
                ))
        pits = [d.pit(y) for d, y in pairs]
        for test in self.calibration_tests():
            result = test.evaluate(pits)
            metrics[f"{test.name}_statistic"] = result["statistic"]
            metrics[f"{test.name}_pvalue"] = result["pvalue"]
            report[test.name] = result
        return {"metrics": metrics, "report": JsonArtifact(report)}
