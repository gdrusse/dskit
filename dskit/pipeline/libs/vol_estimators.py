"""The tier-2 volatility-estimator pack: EWMA and high-low variance beside the rolling mean-square.

``RealizedVolFeatures`` (numpy pack, ADR-0168) answers one question: the root-mean-square of
the last ``w`` one-step log returns. A short-horizon model wants two more reads of the same
bars, an exponentially weighted one that reacts faster and a range-based one that uses the
high and the low, and it wants them to refuse a window that holds a missing bar. That is this
module. It is a NEW pack beside the numpy one and edits nothing in it (ADR-0244).

**Every estimator returns a per-bar VARIANCE** of the one-bar log return, in the units of one
bar. The value at bar ``i`` reads bars ``0..i`` and nothing after, so the numpy pack's
causality guard has nothing to refuse. The unit conversion (a standard deviation, per
square-root second, annualised) is the caller's, because it is the caller's bar length.

* ``rms`` — the mean of the last ``window`` squared one-bar log returns. Zero-mean: drift is
  not subtracted, so a driftless model does not fit a mean to 60 noisy points.
* ``ewma`` — squared returns weighted ``0.5 ** (age / half_life)`` over the last ``lookback``
  bars, the weights normalised. **Truncated** at ``lookback``, so the value does not depend on
  where the loaded history happens to begin: a recursive filter would, forever.
* ``high_low`` — Parkinson's range estimator, ``ln(H / L) ** 2 / (4 ln 2)`` per bar, averaged
  over ``window`` bars. It needs ``0 < L <= H``; any other bar is undefined.

**The gap rule (:func:`contiguous_mask`).** A bar whose predecessor did not open exactly one
bar earlier has no return and no range, and any window that holds such a bar is undefined
(NaN), never shortened. This is the reason the module does not use ``rolling_std``, whose
``nanstd`` skips NaNs and so quietly turns a window holding a missing minute into a shorter
window. All three estimators share the one rule, so they blank exactly the same windows.

**Wiring is by import path**, like the numpy pack's nodes: :data:`NODE_KINDS` is empty and
nothing registers. A document names ``dskit.pipeline.libs.vol_estimators:VolEstimatorFeatures``.
:class:`VolEstimatorFeatures` is an ``ArrayFeatures`` whose estimators are strategy objects
(:class:`VolEstimator`) chosen from a registry (:data:`ESTIMATORS`) by the ``kind`` each spec
names, never an ``if kind ==`` chain: a new estimator is a subclass and a registry entry.

**Restated, not imported.** The numpy pack's own underscore helpers are not used here; the
few rules this module needs (a positive number, an integer window) have one owner in this file
or come from the toolkit's public validators.

Import cost: stdlib and dskit only; numpy is imported inside the functions that compute.
"""

import math
from abc import ABC, abstractmethod

from dskit.pipeline.document import is_node_ref
from dskit.pipeline.libs.numpy import (
    DEFAULT_ORDER_FIELD,
    ArrayFeatures,
    log_return,
    narrow_params,
    rolling_sum,
)
from dskit.pipeline.node import check_int_param
from dskit.pipeline.records import number_ok

__all__ = [
    "Bars",
    "DEFAULT_CLOSE_FIELD",
    "DEFAULT_COLUMN_PREFIX",
    "DEFAULT_HIGH_FIELD",
    "DEFAULT_LOW_FIELD",
    "ESTIMATORS",
    "Ewma",
    "HighLow",
    "NODE_KINDS",
    "ROLES",
    "RollingRms",
    "VolEstimator",
    "VolEstimatorFeatures",
    "build_estimators",
    "contiguous_mask",
    "ewma_variance",
    "parkinson_variance",
]

#: The record field each price role is read from when a document does not say.
DEFAULT_CLOSE_FIELD = "close"
DEFAULT_HIGH_FIELD = "high"
DEFAULT_LOW_FIELD = "low"

#: What an output column's name starts with when a document does not say. The columns hold a
#: per-bar VARIANCE, so the default does not borrow the numpy pack's ``rv_`` (a volatility).
DEFAULT_COLUMN_PREFIX = "var_"

#: The price roles an estimator may read, in the order they are lifted from the records.
ROLES = ("close", "high", "low")

#: Parkinson's constant, the denominator of the per-bar range variance.
_PARKINSON_SCALE = 4.0 * math.log(2.0)


def _positive_number_problem(name, value):
    """Say why ``value`` is not a finite number above zero, or None when it is."""
    try:
        ok = number_ok(value) and value > 0
    except OverflowError:  # an int too large for a float is no usable number
        ok = False
    return None if ok else f"{name} must be a finite number > 0, got {value!r}"


def _ewma_problems(half_life, lookback):
    """List what is wrong with an EWMA's two knobs, empty when nothing is."""
    problems = []
    problem = _positive_number_problem("half_life", half_life)
    if problem:
        problems.append(problem)
    check_int_param(problems, "lookback", lookback, ge=1)
    return problems


# ---------------------------------------------------------------------------
# The ops: pure array rules, each with one owner
# ---------------------------------------------------------------------------


def contiguous_mask(open_times, bar_ms):
    """Mark each bar that opens exactly one bar after the previous one.

    The one home of the gap rule: a return or a range taken at a bar where this is False
    crosses a missing bar, so it is undefined, and any window holding it is undefined too.

    Parameters
    ----------
    open_times : array-like of int or float
        Each bar's opening instant, ascending, in the same unit as ``bar_ms``.
    bar_ms : int or float
        The bar length, a finite number above zero.

    Returns
    -------
    numpy.ndarray
        A bool array the length of ``open_times``; False for the first bar (it has no
        predecessor), for a repeated or reversed instant, and after a missing bar.

    Raises
    ------
    ValueError
        When ``bar_ms`` is not a finite number above zero.

    Examples
    --------
    Three one-minute bars with a missing minute before the third::

        contiguous_mask([0, 60000, 180000], 60000)
        # -> array([False, True, False])
    """
    import numpy as np

    problem = _positive_number_problem("bar_ms", bar_ms)
    if problem:
        raise ValueError(problem)
    times = np.asarray(open_times).reshape(-1)
    mask = np.zeros(times.shape, dtype=bool)
    if times.size > 1:
        mask[1:] = np.diff(times) == bar_ms
    return mask


def ewma_variance(squared_returns, half_life, lookback):
    """Weight squared returns by ``0.5 ** (age / half_life)`` over the last ``lookback`` values.

    The weights are normalised to sum to one over the window, and the window is truncated, so
    the value at ``i`` is a function of ``squared_returns[i - lookback + 1 : i + 1]`` alone and
    does not depend on where the series starts.

    Parameters
    ----------
    squared_returns : array-like of float
        One series' squared one-bar returns, oldest first; NaN where undefined.
    half_life : int or float
        The age, in bars, at which a weight has halved; finite and above zero.
    lookback : int
        The window length in bars, ``>= 1``.

    Returns
    -------
    numpy.ndarray
        A float64 array the length of the input: NaN for the first ``lookback - 1`` positions
        and wherever the window holds a NaN.

    Raises
    ------
    ValueError
        When ``half_life`` is not a finite number above zero or ``lookback`` is not an int
        ``>= 1``.

    Examples
    --------
    A one-bar half-life over three bars::

        ewma_variance([1e-4, 1e-4, 4e-4], 1, 3)
        # -> array([nan, nan, 2.7143e-4])
    """
    import numpy as np

    problems = _ewma_problems(half_life, lookback)
    if problems:
        raise ValueError("; ".join(problems))
    squared = np.asarray(squared_returns, dtype=np.float64).reshape(-1)
    if squared.size == 0:
        return squared.copy()
    width = int(lookback)
    weights = 0.5 ** (np.arange(width) / float(half_life))
    weights = weights / weights.sum()
    out = np.convolve(squared, weights, mode="full")[: squared.size]
    out[: width - 1] = np.nan
    return out


def parkinson_variance(high, low):
    """Take Parkinson's range variance of each bar: ``ln(H / L) ** 2 / (4 ln 2)``.

    Parameters
    ----------
    high, low : array-like of float
        Each bar's high and low, the same shape.

    Returns
    -------
    numpy.ndarray
        A float64 array of the same shape: NaN wherever a bar does not satisfy
        ``0 < L <= H`` with both finite, and zero for a bar whose high equals its low.

    Raises
    ------
    ValueError
        When ``high`` and ``low`` do not have the same shape.

    Examples
    --------
    A bar that traded between 100 and 102::

        parkinson_variance([102.0], [100.0])
        # -> array([1.4144e-4])
    """
    import numpy as np

    high = np.asarray(high, dtype=np.float64)
    low = np.asarray(low, dtype=np.float64)
    if high.shape != low.shape:
        raise ValueError(f"high and low must have the same shape, got {high.shape} and {low.shape}")
    with np.errstate(divide="ignore", invalid="ignore"):
        valid = np.isfinite(high) & np.isfinite(low) & (low > 0.0) & (high >= low)
        per_bar = np.log(high / low) ** 2 / _PARKINSON_SCALE
    return np.where(valid, per_bar, np.nan)


# ---------------------------------------------------------------------------
# Bars: contiguity-aware arrays of one price series
# ---------------------------------------------------------------------------


class Bars:
    """Arrays of one series' bars, oldest first, with the gap rule applied.

    Parameters
    ----------
    open_time : array-like of int or float
        Each bar's opening instant, ascending, in the unit of ``bar_ms``.
    bar_ms : int or float
        The bar length, a finite number above zero.
    high, low, close : array-like of float or None
        Each bar's high, low and close; None for a role the estimators in play do not read.

    Attributes
    ----------
    contiguous : numpy.ndarray
        True where the bar opens exactly one bar after the previous one.
    high, low, close : numpy.ndarray or None
        The price arrays as float64, or None when not given.

    Raises
    ------
    ValueError
        When a price array's length differs from ``open_time``'s, or ``bar_ms`` is unusable.

    Examples
    --------
    Three one-minute bars with a missing minute before the third::

        bars = Bars([0, 60000, 180000], 60000, close=[1.5, 1.6, 1.7])
        bars.contiguous   # -> array([False, True, False])
    """

    def __init__(self, open_time, bar_ms, high=None, low=None, close=None):
        import numpy as np

        self.open_time = np.asarray(open_time).reshape(-1)
        self.bar_ms = bar_ms
        self.contiguous = contiguous_mask(self.open_time, bar_ms)
        self.high = self._role("high", high)
        self.low = self._role("low", low)
        self.close = self._role("close", close)

    def _role(self, name, values):
        """Hold one price array to ``open_time``'s length, or pass None through."""
        import numpy as np

        if values is None:
            return None
        array = np.asarray(values, dtype=np.float64).reshape(-1)
        if array.size != self.open_time.size:
            raise ValueError(f"{name} has length {array.size} but open_time has length {self.open_time.size}")
        return array

    def squared_returns(self):
        """Square each one-bar log return, NaN where the bar is not contiguous.

        Returns
        -------
        numpy.ndarray
            One value per bar; the first bar and every bar after a missing bar are NaN.

        Raises
        ------
        ValueError
            When these bars carry no close.
        """
        import numpy as np

        if self.close is None:
            raise ValueError("these bars carry no close, so no return can be taken")
        returns = np.where(self.contiguous, log_return(self.close, 1), np.nan)
        return returns * returns


# ---------------------------------------------------------------------------
# The estimators: strategy objects, chosen by a registry
# ---------------------------------------------------------------------------


class VolEstimator(ABC):
    """One estimator: names its column, says what it needs, returns a per-bar variance.

    Abstract: a concrete estimator implements the four hooks and is added to
    :data:`ESTIMATORS`; a document then names it by ``kind`` and :func:`build_estimators`
    builds it, so no code branches on the kind.

    Parameters
    ----------
    spec : dict
        The validated spec, ``{"kind": <this class's kind>, ...its own knobs}``.

    Examples
    --------
    Build one and read its label and the history it needs::

        estimator = build_estimators([{"kind": "rms", "window": 60}])[0]
        estimator.label(), estimator.lookback_bars()   # -> ("rms_60", 61)
    """

    #: The ``kind`` a document's spec names.
    kind = ""
    #: The spec's own knobs besides ``kind``; each is required and none else is allowed.
    keys = ()
    #: The price roles (a subset of :data:`ROLES`) this estimator reads.
    needs = ()

    def __init__(self, spec):
        self.spec = spec

    @classmethod
    @abstractmethod
    def spec_problems(cls, spec):
        """List problems with the spec's own knobs, empty when none (list of str)."""

    @abstractmethod
    def label(self):
        """Name the column, without a prefix (str)."""

    @abstractmethod
    def lookback_bars(self):
        """Count the bars up to and including the current one that a value needs (int)."""

    @abstractmethod
    def variance(self, bars):
        """Return the per-bar variance series.

        Parameters
        ----------
        bars : Bars
            The bars, oldest first, carrying every role in :attr:`needs`.

        Returns
        -------
        numpy.ndarray
            One variance per bar, NaN where undefined; the value at ``i`` reads bars ``<= i``.
        """


class _WindowEstimator(VolEstimator):
    """The rule the rolling-mean estimators share: one ``window``, ``window + 1`` bars of history."""

    keys = ("window",)
    #: The word a label starts with (``rms`` makes ``rms_60``).
    stem = ""

    @classmethod
    def spec_problems(cls, spec):
        """List problems with ``window``, empty when none."""
        problems = []
        check_int_param(problems, "window", spec.get("window"), ge=1)
        return problems

    def label(self):
        """Name the column ``<stem>_<window>``."""
        return f"{self.stem}_{int(self.spec['window'])}"

    def lookback_bars(self):
        """Need ``window`` contiguous bars, the first of which needs a predecessor."""
        return int(self.spec["window"]) + 1


class RollingRms(_WindowEstimator):
    """Mean squared one-bar log return over the last ``window`` bars.

    Parameters
    ----------
    spec : dict
        ``{"kind": "rms", "window": int >= 1}``.

    Examples
    --------
    A 60-bar rolling estimator::

        estimator = RollingRms({"kind": "rms", "window": 60})
        variance = estimator.variance(bars)   # NaN until 60 valid returns
    """

    kind = "rms"
    stem = "rms"
    needs = ("close",)

    def variance(self, bars):
        """Return the rolling mean of the squared returns."""
        window = int(self.spec["window"])
        return rolling_sum(bars.squared_returns(), window) / window


class Ewma(VolEstimator):
    """Exponentially weighted mean squared return over a truncated window.

    Parameters
    ----------
    spec : dict
        ``{"kind": "ewma", "half_life": number > 0, "lookback": int >= 1}``, both in bars.

    Examples
    --------
    A 30-bar half-life over at most 300 bars::

        estimator = Ewma({"kind": "ewma", "half_life": 30, "lookback": 300})
        variance = estimator.variance(bars)
    """

    kind = "ewma"
    keys = ("half_life", "lookback")
    needs = ("close",)

    @classmethod
    def spec_problems(cls, spec):
        """List problems with ``half_life`` and ``lookback``, empty when none."""
        return _ewma_problems(spec.get("half_life"), spec.get("lookback"))

    def label(self):
        """Name the column ``ewma_<half_life>``."""
        return f"ewma_{self.spec['half_life']:g}"

    def lookback_bars(self):
        """Need ``lookback`` returns, so ``lookback + 1`` bars."""
        return int(self.spec["lookback"]) + 1

    def variance(self, bars):
        """Return the weighted mean of the squared returns over the last ``lookback`` bars."""
        return ewma_variance(bars.squared_returns(), self.spec["half_life"], self.spec["lookback"])


class HighLow(_WindowEstimator):
    """Parkinson's range variance averaged over the last ``window`` bars.

    A bar after a missing bar has no range (the same footprint as a return), so this
    estimator blanks exactly the windows the other two do.

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
    stem = "hl"
    needs = ("high", "low")

    def variance(self, bars):
        """Return the rolling mean of the per-bar Parkinson variances."""
        import numpy as np

        window = int(self.spec["window"])
        per_bar = np.where(bars.contiguous, parkinson_variance(bars.high, bars.low), np.nan)
        return rolling_sum(per_bar, window) / window


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
        When ``specs`` is not a list, or on a spec that is not a dict with a known kind, has an
        unknown or unusable knob, or would write the same column label as an earlier one.

    Examples
    --------
    Two estimators side by side::

        built = build_estimators([{"kind": "rms", "window": 60},
                                  {"kind": "ewma", "half_life": 30, "lookback": 300}])
        [e.label() for e in built]   # -> ["rms_60", "ewma_30"]
    """
    if not isinstance(specs, (list, tuple)):
        raise ValueError(f"estimators must be a list of specs, got {specs!r}")
    built, seen = [], set()
    for spec in specs:
        kind = spec.get("kind") if isinstance(spec, dict) else None
        if not isinstance(kind, str) or kind not in ESTIMATORS:
            raise ValueError(f"estimator spec must be a dict whose kind is one of {sorted(ESTIMATORS)}, got {spec!r}")
        cls = ESTIMATORS[kind]
        unknown = sorted(set(spec) - {"kind", *cls.keys}, key=str)
        problems = ([f"unknown knob(s) {unknown} for kind {kind!r}; allowed: {list(cls.keys)}"] if unknown else []
                    ) + cls.spec_problems(spec)
        if problems:
            raise ValueError(f"estimator {spec!r}: " + "; ".join(problems))
        estimator = cls(spec)
        if estimator.label() in seen:
            raise ValueError(f"estimators repeat the label {estimator.label()!r}; each may appear once")
        seen.add(estimator.label())
        built.append(estimator)
    return built


# ---------------------------------------------------------------------------
# The node
# ---------------------------------------------------------------------------


class VolEstimatorFeatures(ArrayFeatures):
    """Per-bar variance estimators as feature columns (role ``tensor``).

    One column per estimator, named ``<column_prefix><label>`` (``var_rms_60``,
    ``var_ewma_30``, ``var_hl_60``), each the per-bar variance of the one-bar log return at
    that bar, from bars up to and including it. A window holding a missing bar is absent
    (``None``), never shortened. The stream is lifted, grouped, ordered and framed by the numpy
    pack's :class:`~dskit.pipeline.libs.numpy.ArrayFeatures`, so the group key, the carried
    fields, ``max_gap``, the causality guard and ``latest_rows`` (the serving call) are the
    pack's own. ``fields`` is NOT a knob here: the records' price fields are named by
    ``close_field``, ``high_field`` and ``low_field``, and only the roles the declared
    estimators read are lifted.

    The order field must hold the bar's opening instant (or anything that steps by exactly
    ``bar_ms`` from one bar to the next), in the unit ``bar_ms`` is stated in.

    Parameters
    ----------
    params : dict
        ``bar_ms`` (number > 0, required), ``estimators`` (non-empty list of specs, required;
        see :func:`build_estimators`), ``close_field`` / ``high_field`` / ``low_field`` (str,
        defaults ``"close"`` / ``"high"`` / ``"low"``), ``column_prefix`` (str, default
        ``"var_"``), plus :class:`~dskit.pipeline.libs.numpy.ArrayFeatures`' knobs other than
        ``fields``.

    Examples
    --------
    Three estimators over one-minute bars ordered by their opening instant::

        node = VolEstimatorFeatures("vol", {
            "bar_ms": 60000, "order_field": "open_time", "group_field": "symbol",
            "estimators": [{"kind": "rms", "window": 60},
                           {"kind": "ewma", "half_life": 30, "lookback": 300},
                           {"kind": "high_low", "window": 60}]})
        out = node.run(ctx, {"records": bars})
        # -> {"rows": [{"var_rms_60": ..., "var_ewma_30": ..., "var_hl_60": ...}, ...], "metrics": {...}}
    """

    _PARAMS = narrow_params(ArrayFeatures._PARAMS, "fields") + (
        "bar_ms",
        "close_field",
        "column_prefix",
        "estimators",
        "high_field",
        "low_field",
    )

    def bar_ms(self):
        """Give the bar length, in the order field's own units (number)."""
        return self.params["bar_ms"]

    def close_field(self):
        """Name the record field the close is read from (str)."""
        return self.params.get("close_field", DEFAULT_CLOSE_FIELD)

    def high_field(self):
        """Name the record field the high is read from (str)."""
        return self.params.get("high_field", DEFAULT_HIGH_FIELD)

    def low_field(self):
        """Name the record field the low is read from (str)."""
        return self.params.get("low_field", DEFAULT_LOW_FIELD)

    def column_prefix(self):
        """Name what every output column's name starts with (str)."""
        return self.params.get("column_prefix", DEFAULT_COLUMN_PREFIX)

    def estimators(self):
        """Build the declared estimators, in order (list of VolEstimator)."""
        return build_estimators(self.params["estimators"])

    def lookback_bars(self):
        """Count the bars of history one row needs: the most any estimator reads (int)."""
        return max(built.lookback_bars() for built in self.estimators())

    def _role_fields(self):
        """Map each price role the estimators read to the record field that carries it."""
        wanted = {role for built in self.estimators() for role in built.needs}
        names = {"close": self.close_field(), "high": self.high_field(), "low": self.low_field()}
        return {role: names[role] for role in ROLES if role in wanted}

    def fields(self):
        """Name the numeric fields lifted: only those the declared estimators read (tuple of str)."""
        return tuple(self._role_fields().values())

    @classmethod
    def validate_params(cls, params):
        """Problems with ``params``, empty when none.

        Parameters
        ----------
        params : dict
            The node's declared params.

        Returns
        -------
        list of str
            One problem per broken knob.
        """
        problems = super().validate_params(params)
        cls._bar_ms_problems(problems, params)
        cls._name_problems(problems, params)
        cls._estimator_problems(problems, params)
        return problems

    @classmethod
    def _bar_ms_problems(cls, problems, params):
        """Require ``bar_ms``, a finite number above zero."""
        if "bar_ms" not in params:
            problems.append("bar_ms is required — the bar length, in the order field's unit, must be stated")
        elif not is_node_ref(params["bar_ms"]):
            problem = _positive_number_problem("bar_ms", params["bar_ms"])
            if problem:
                problems.append(problem)

    @classmethod
    def _name_problems(cls, problems, params):
        """Hold the price-field and prefix knobs to non-empty strings, and no price to the order field."""
        order = params.get("order_field", DEFAULT_ORDER_FIELD)
        for knob in ("close_field", "high_field", "low_field", "column_prefix"):
            value = params.get(knob)
            if knob not in params or is_node_ref(value):
                continue
            if not isinstance(value, str) or not value:
                problems.append(f"{knob} must be a non-empty string, got {value!r}")
            elif knob != "column_prefix" and value == order:
                problems.append(f"{knob} must not be the order field {order!r}: a price is not the bar's own time")

    @classmethod
    def _estimator_problems(cls, problems, params):
        """Require ``estimators``, a non-empty list of specs :func:`build_estimators` accepts."""
        if "estimators" not in params:
            problems.append("estimators is required — a list of estimator specs must be stated")
            return
        specs = params["estimators"]
        if is_node_ref(specs):
            return
        if not isinstance(specs, (list, tuple)) or not specs:
            problems.append(f"estimators must be a non-empty list of specs, got {specs!r}")
            return
        try:
            build_estimators(specs)
        except ValueError as error:
            problems.append(f"estimators: {error}")

    def apply(self, arrays, params):
        """Build one variance column per declared estimator.

        Parameters
        ----------
        arrays : dict of str -> numpy.ndarray
            One segment's arrays: the order field and each lifted price field.
        params : dict
            This node's params; read through accessors.

        Returns
        -------
        dict of str -> numpy.ndarray
            ``<column_prefix><label>`` to the per-bar variance, NaN where undefined.
        """
        prices = {role: arrays[field] for role, field in self._role_fields().items()}
        bars = Bars(arrays[self.order_field()], self.bar_ms(), **prices)
        prefix = self.column_prefix()
        return {prefix + built.label(): built.variance(bars) for built in self.estimators()}


#: Deliberately EMPTY, like the numpy pack's: the node is wired by import path
#: (``dskit.pipeline.libs.vol_estimators:VolEstimatorFeatures``) and nothing here registers.
NODE_KINDS = ()
