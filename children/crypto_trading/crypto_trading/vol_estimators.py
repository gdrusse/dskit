"""Volatility estimators over 1-minute bars: rolling RMS, EWMA and the high-low range.

INTERIM HOME (PROPOSED ADR-0241): dskit has a trailing realised-vol feature
(``RealizedVolFeatures``, the rolling root-mean-square of log returns) but no EWMA
estimator and no high-low estimator, and no way to read either at an arbitrary instant
rather than per bar. The building blocks (``log_return``, ``rolling_sum``) are imported from
the numpy pack, not copied; only the two missing estimators and the contiguity rule are here.

Each estimator returns a PER-BAR VARIANCE series: the value at bar ``i`` uses bars ``0..i``
and nothing after it, which the tests prove by cutting and corrupting the future. Gaps are
never bridged: a bar whose predecessor is not exactly one bar earlier has no return and no
range, and a window that holds such a bar is undefined (NaN), so a missing minute blanks the
feature instead of stretching a return across it. Units are the caller's: the spot node
turns a per-bar variance into a standard deviation per square root of a second.

- ``rms``: mean of squared one-bar log returns over ``window`` bars (zero-mean, so drift is
  not subtracted: a driftless model should not fit a mean to 60 noisy points).
- ``ewma``: squared returns weighted ``0.5 ** (age / half_life)`` over the last ``lookback``
  bars, weights normalised. Truncated at ``lookback`` so the value does not depend on where
  the loaded history happens to begin.
- ``high_low``: Parkinson's range estimator, ``ln(H/L)**2 / (4 ln 2)`` per bar, averaged over
  ``window`` bars. It needs a bar with ``0 < L <= H``; any other bar is undefined.

Import cost: stdlib + dskit; numpy is imported inside the methods that compute.
"""

import math
from abc import ABC, abstractmethod

from dskit.pipeline.libs.numpy import log_return, rolling_sum
from dskit.pipeline.node import check_int_param
from dskit.pipeline.records import number_ok

__all__ = ["Bars", "ESTIMATORS", "Ewma", "HighLow", "RollingRms", "VolEstimator", "build_estimators"]


class Bars:
    """Contiguous-aware arrays of 1-minute bars, oldest first.

    Parameters
    ----------
    open_time : array-like of int
        Each bar's START instant (epoch ms), ascending.
    high, low, close : array-like of float
        Each bar's high, low and close.
    bar_ms : int
        The bar length in milliseconds.

    Attributes
    ----------
    contiguous : numpy.ndarray of bool
        True where the bar opens exactly one bar after the previous one (False for the first).

    Examples
    --------
    Three one-minute bars with a missing minute before the third::

        bars = Bars([0, 60000, 180000], [2, 2, 2], [1, 1, 1], [1.5, 1.6, 1.7], 60000)
        bars.contiguous   # -> array([False, True, False])
    """

    def __init__(self, open_time, high, low, close, bar_ms):
        import numpy as np

        self.open_time = np.asarray(open_time, dtype=np.int64)
        self.high = np.asarray(high, dtype=np.float64)
        self.low = np.asarray(low, dtype=np.float64)
        self.close = np.asarray(close, dtype=np.float64)
        self.bar_ms = bar_ms
        self.contiguous = np.concatenate(([False], np.diff(self.open_time) == bar_ms))


class VolEstimator(ABC):
    """One estimator: names its column, says how much history it needs, returns per-bar variance.

    Abstract: build concrete members with :func:`build_estimators`, never directly.

    Parameters
    ----------
    spec : dict
        The validated estimator spec (see :func:`build_estimators`).

    Examples
    --------
    Build one and read its column and need::

        estimator = build_estimators([{"kind": "rms", "window": 60}])[0]
        estimator.column(), estimator.lookback_bars()   # -> ("rv_rms_60", 61)
    """

    #: The ``kind`` a document's estimator spec names.
    kind = ""
    #: The spec's own keys besides ``kind``.
    keys = ()

    def __init__(self, spec):
        self.spec = spec

    @classmethod
    @abstractmethod
    def spec_problems(cls, spec):
        """List problems with the spec's own knobs, empty when none (list of str)."""

    @abstractmethod
    def column(self):
        """Name the output column (str)."""

    @abstractmethod
    def lookback_bars(self):
        """Count the bars up to and including the current one a value needs (int)."""

    @abstractmethod
    def variance(self, bars):
        """Return the per-bar variance series.

        Parameters
        ----------
        bars : Bars
            The bars, oldest first.

        Returns
        -------
        numpy.ndarray
            One variance per bar, NaN where undefined; the value at ``i`` reads bars ``<= i``.
        """

    @staticmethod
    def squared_returns(bars):
        """Return the squared one-bar log returns, NaN where the bar is not contiguous."""
        import numpy as np

        returns = np.where(bars.contiguous, log_return(bars.close, 1), np.nan)
        return returns * returns


class RollingRms(VolEstimator):
    """Mean squared one-bar log return over the last ``window`` bars.

    Parameters
    ----------
    spec : dict
        ``{"kind": "rms", "window": int >= 1}``.

    Examples
    --------
    A 60-bar rolling estimator::

        estimator = RollingRms({"kind": "rms", "window": 60})
        variance = estimator.variance(bars)   # per-bar variance, NaN until 60 valid returns
    """

    kind = "rms"
    keys = ("window",)

    @classmethod
    def spec_problems(cls, spec):
        """List problems with ``window``, empty when none.

        Parameters
        ----------
        spec : dict
            The estimator spec.

        Returns
        -------
        list of str
            One problem when ``window`` is not an int >= 1.
        """
        problems = []
        check_int_param(problems, "window", spec.get("window"), ge=1)
        return problems

    def column(self):
        """Name the column ``rv_rms_<window>``."""
        return f"rv_rms_{int(self.spec['window'])}"

    def lookback_bars(self):
        """Need ``window`` returns, so ``window + 1`` bars."""
        return int(self.spec["window"]) + 1

    def variance(self, bars):
        """Return the rolling mean of squared returns."""
        window = int(self.spec["window"])
        return rolling_sum(self.squared_returns(bars), window) / window


class Ewma(VolEstimator):
    """Exponentially weighted mean squared return over a truncated window.

    Parameters
    ----------
    spec : dict
        ``{"kind": "ewma", "half_life": number > 0, "lookback": int >= 1}``, in bars.

    Examples
    --------
    A 30-bar half-life over at most 300 bars::

        estimator = Ewma({"kind": "ewma", "half_life": 30, "lookback": 300})
        variance = estimator.variance(bars)
    """

    kind = "ewma"
    keys = ("half_life", "lookback")

    @classmethod
    def spec_problems(cls, spec):
        """List problems with ``half_life`` and ``lookback``, empty when none.

        Parameters
        ----------
        spec : dict
            The estimator spec.

        Returns
        -------
        list of str
            One problem per unusable knob.
        """
        problems = []
        half_life = spec.get("half_life")
        if isinstance(half_life, bool) or not (number_ok(half_life) and half_life > 0):
            problems.append(f"half_life must be a positive number of bars, got {half_life!r}")
        check_int_param(problems, "lookback", spec.get("lookback"), ge=1)
        return problems

    def column(self):
        """Name the column ``rv_ewma_<half_life>``."""
        return f"rv_ewma_{self.spec['half_life']:g}"

    def lookback_bars(self):
        """Need ``lookback`` returns, so ``lookback + 1`` bars."""
        return int(self.spec["lookback"]) + 1

    def variance(self, bars):
        """Return the weighted mean of squared returns over the last ``lookback`` bars."""
        import numpy as np

        length = int(self.spec["lookback"])
        weights = 0.5 ** (np.arange(length) / float(self.spec["half_life"]))
        weights = weights / weights.sum()
        squared = self.squared_returns(bars)
        out = np.convolve(squared, weights, mode="full")[: squared.size]
        out[: length - 1] = np.nan
        return out


class HighLow(VolEstimator):
    """Parkinson's range variance averaged over the last ``window`` bars.

    Parameters
    ----------
    spec : dict
        ``{"kind": "high_low", "window": int >= 1}``.

    Examples
    --------
    A 60-bar range estimator::

        estimator = HighLow({"kind": "high_low", "window": 60})
        variance = estimator.variance(bars)
    """

    kind = "high_low"
    keys = ("window",)

    @classmethod
    def spec_problems(cls, spec):
        """List problems with ``window``, empty when none.

        Parameters
        ----------
        spec : dict
            The estimator spec.

        Returns
        -------
        list of str
            One problem when ``window`` is not an int >= 1.
        """
        problems = []
        check_int_param(problems, "window", spec.get("window"), ge=1)
        return problems

    def column(self):
        """Name the column ``rv_hl_<window>``."""
        return f"rv_hl_{int(self.spec['window'])}"

    def lookback_bars(self):
        """Need ``window`` contiguous bars, the first of which needs a predecessor: ``window + 1``."""
        return int(self.spec["window"]) + 1

    def variance(self, bars):
        """Return the rolling mean of per-bar Parkinson variances."""
        import numpy as np

        window = int(self.spec["window"])
        valid = bars.contiguous & (bars.low > 0.0) & (bars.high >= bars.low)
        with np.errstate(divide="ignore", invalid="ignore"):
            per_bar = np.log(bars.high / bars.low) ** 2 / (4.0 * math.log(2.0))
        return rolling_sum(np.where(valid, per_bar, np.nan), window) / window


#: kind -> estimator class: the whole vocabulary a document's ``estimators`` may name.
ESTIMATORS = {cls.kind: cls for cls in (RollingRms, Ewma, HighLow)}


def build_estimators(specs):
    """Build the estimators a document declares.

    Parameters
    ----------
    specs : list of dict
        Each ``{"kind": <key of ESTIMATORS>, ...the kind's own knobs}``.

    Returns
    -------
    list of VolEstimator
        One per spec, in order.

    Raises
    ------
    ValueError
        On a spec that is not a dict with a known kind, an unknown or unusable knob, or two
        specs that would write the same column.
    """
    built, seen = [], set()
    for spec in specs:
        if not isinstance(spec, dict) or spec.get("kind") not in ESTIMATORS:
            raise ValueError(f"estimator spec must be a dict whose kind is one of {sorted(ESTIMATORS)}, got {spec!r}")
        cls = ESTIMATORS[spec["kind"]]
        unknown = sorted(set(spec) - {"kind", *cls.keys})
        problems = ([f"unknown knob(s) {unknown} for kind {cls.kind!r}; allowed: {list(cls.keys)}"] if unknown else []
                    ) + cls.spec_problems(spec)
        if problems:
            raise ValueError(f"estimator {spec!r}: " + "; ".join(problems))
        estimator = cls(spec)
        if estimator.column() in seen:
            raise ValueError(f"estimators repeat the column {estimator.column()!r}")
        seen.add(estimator.column())
        built.append(estimator)
    return built
