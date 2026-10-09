"""Minute-level features and 15-horizon labels for two assets, built only from bars known at t.

One row per ``(asset, t)``: ``t_ms`` is the ``close_time_ms`` of the latest bar the row may read, and
every feature is a function of bars whose ``close_time_ms <= t_ms`` (the information instant is
``t_ms + 1``, the minute boundary). The labels are what the later bars are FOR:
``y_h<h> = ln(close(t + h) / close(t)) / scale`` with ``scale`` the rolling RMS of the last
``scale_window`` one-minute returns, known at ``t`` (per-minute log-return std, the unit of the
``vol__*`` columns). The divisor is emitted as ``meta__scale``: the reserved ``meta`` prefix is no
feature family, so no ablation arm selects it and it is NOT a model input (with the defaults it equals
``vol__rms_60``, so a model that read it would keep volatility in the ``minus_vol`` arm).

- **Families.** Each column is named ``<family>__<name>`` so a list of kept families drops a family
  by prefix: ``ret``, ``vol``, ``flow``, ``clock``, ``xasset`` (the other asset's ``ret`` and ``vol``
  columns, renamed ``xasset__<name>``) and ``bvol`` (the BVOL level and its change). ``meta__scale`` and the
  labels belong to no family: every ablation arm scores the same target.
- **The row set never depends on the families.** Eligibility is decided from every window the node
  could use, whichever families are kept, so a family-ablation compares models on identical samples.
- **No fill.** A bar whose trailing window (the widest of the configured ones) holds a missing minute
  is refused by name (``gap_in_window``), as are the first bars of the data (``insufficient_history``),
  a non-finite feature, a non-positive volume or trade count, or a ``taker_buy_volume`` outside
  ``[0, volume]`` (``invalid_value``), a flat window
  (``zero_scale``) and a bar whose other asset has no eligible bar at the same instant
  (``xasset_unavailable``). The counts are in ``provenance``: every bar is either a row or a named refusal.
  A label whose ``t + h`` bar is missing is None and counted; the row stays.
- **BVOL** is optional and never refuses a row: a reading is usable when ``calc_time_ms`` is before the
  information instant (a reading AT it is not yet known) and no older than ``bvol_max_age_ms``;
  otherwise the columns are None (an ablation family with missing days). A reading whose ``index_value``
  is NaN, infinite or not a number is a missing reading (dropped), never an abort.
- **Variance** comes from :mod:`dskit.pipeline.libs.vol_estimators` (rolling RMS, EWMA, Parkinson range,
  all gap-aware); this node takes the square root. Clock features are UTC.

Import cost: stdlib + dskit; numpy only inside ``run``.
"""

import math
from abc import ABC, abstractmethod

from dskit.pipeline.libs.parquet_series import prior_index
from dskit.pipeline.libs.vol_estimators import Bars, build_estimators
from dskit.pipeline.node import check_int_param, reject_unknown_params
from dskit.pipeline.records import number_ok

from .ports import ListPortsNode

__all__ = [
    "BAR_MS", "FAMILIES", "FAMILY_TYPES", "MinuteFeatures", "REFUSALS", "FeatureFamily",
]

#: The bar this node is built for: one minute, in epoch ms.
BAR_MS = 60_000
#: Binance stamps a bar's ``close_time_ms`` one millisecond before the next bar opens.
CLOSE_OFFSET_MS = 1
_DAYS_PER_WEEK = 7
_MS_PER_DAY = 86_400_000
_MINUTES_PER_DAY = _MS_PER_DAY // BAR_MS
#: 1970-01-01 was a Thursday; with Monday as 0 that is 3.
_EPOCH_WEEKDAY = 3
_SEP = "__"
#: The reserved prefix of the columns that are not features; it is no family, so no arm selects one.
_META = "meta"
#: The names a refused bar can carry in the provenance.
REFUSALS = ("insufficient_history", "gap_in_window", "invalid_value", "zero_scale", "xasset_unavailable")

_KLINE_FIELDS = ("open_time_ms", "open", "high", "low", "close", "volume", "close_time_ms", "trades",
                 "taker_buy_volume")
_BVOL_TIME_FIELDS = ("calc_time_ms",)
_LABEL_PREFIX = "y_h"

DEFAULT_RET_MINUTES = (1, 2, 3, 5, 10, 15, 30, 60)
DEFAULT_RMS_WINDOWS = (15, 30, 60, 240)
DEFAULT_EWMA_HALF_LIVES = (15, 60)
#: The EWMA window is this many half-lives (the weight left beyond it is 0.5 ** 5, about 3 percent).
DEFAULT_EWMA_LOOKBACK_MULTIPLE = 5
DEFAULT_RANGE_WINDOWS = (15, 60)
DEFAULT_FLOW_WINDOW = 60
DEFAULT_TAKER_WINDOWS = (15, 60)
DEFAULT_SCALE_WINDOW = 60
DEFAULT_MAX_HORIZON = 15
DEFAULT_BVOL_MAX_AGE_MS = 2 * BAR_MS
DEFAULT_BVOL_CHANGE_MINUTES = 15

_LIST_KNOBS = ("ret_minutes", "rms_windows", "ewma_half_lives", "range_windows", "taker_windows")
_INT_KNOBS = ("ewma_lookback_multiple", "flow_window", "scale_window", "max_horizon", "bvol_max_age_ms",
              "bvol_change_minutes")


def _cell(value):
    """Return a numpy scalar as a Python float, or None when it is not finite."""
    value = float(value)
    return value if math.isfinite(value) else None


def _qualified(family, name):
    """Join a family and a column name."""
    return f"{family}{_SEP}{name}"


#: The label divisor's column: under the reserved ``meta`` prefix, so it is in no family and no arm.
_SCALE_COLUMN = _qualified(_META, "scale")


class _Frame:
    """One asset's bars as arrays, with the memoised columns of each family.

    Parameters
    ----------
    asset : str
        The asset's name.
    rows : list of dict
        Its validated kline rows (any order).
    params : dict
        The node's resolved params.

    Examples
    --------
    Build the frame of one asset and read a family's columns from it::

        frame = _Frame("AAA", kline_rows, MinuteFeatures.resolve({"assets": ["AAA", "BBB"]}))
        columns = frame.columns("ret")   # -> {"ret__r1": array, ...}
    """

    def __init__(self, asset, rows, params):
        import numpy as np

        self.asset, self.params, self._memo = asset, params, {}
        ordered = sorted(rows, key=lambda r: r["open_time_ms"])
        self.open_time = np.array([r["open_time_ms"] for r in ordered], dtype=np.int64)
        self.close_time = np.array([r["close_time_ms"] for r in ordered], dtype=np.int64)
        self.info_ms = self.close_time + CLOSE_OFFSET_MS
        self.price = {k: np.array([r[k] for r in ordered], dtype=np.float64) for k in ("open", "high", "low", "close")}
        self.flow = {k: np.array([r[k] for r in ordered], dtype=np.float64)
                     for k in ("volume", "trades", "taker_buy_volume")}
        self.bars = Bars(self.open_time, BAR_MS, high=self.price["high"], low=self.price["low"],
                         close=self.price["close"])
        self.size = self.open_time.size
        self.run = self._run_lengths()
        #: Set by the node once both assets exist: the other asset's frame, and this asset's BVOL arrays.
        self.partner, self.bvol_times, self.bvol_values = None, None, None

    def _run_lengths(self):
        """Count, per bar, the consecutive contiguous bars ending there (0 at a bar after a gap)."""
        run, count = [], 0
        for ok in self.bars.contiguous:
            count = count + 1 if ok else 0
            run.append(count)
        import numpy as np

        return np.array(run, dtype=np.int64)

    def columns(self, family):
        """Return a family's columns, computed once.

        Parameters
        ----------
        family : str
            A key of :data:`FAMILY_TYPES`.

        Returns
        -------
        dict
            ``{column name: float64 array, one value per bar}``.
        """
        if family not in self._memo:
            self._memo[family] = FAMILY_TYPES[family](self.params).compute(self)
        return self._memo[family]


class FeatureFamily(ABC):
    """One family of columns: names them and computes them for one asset (abstract).

    Parameters
    ----------
    params : dict
        The node's resolved params (defaults filled in).

    Examples
    --------
    A family is a subclass registered in :data:`FAMILY_TYPES`::

        class Minute(FeatureFamily):
            name = "minute"

            def compute(self, frame):
                return {_qualified("minute", "of_hour"): (frame.info_ms // 60000) % 60}
    """

    #: The family's name, the prefix of its columns.
    name = ""

    def __init__(self, params):
        self.params = params

    @abstractmethod
    def compute(self, frame):
        """Compute the family's columns for one asset.

        Parameters
        ----------
        frame : _Frame
            The asset's bars.

        Returns
        -------
        dict
            ``{column name: float64 array, one value per bar}``, NaN where unknown.
        """


class Ret(FeatureFamily):
    """Log returns over the last ``ret_minutes`` minutes: ``ret__r<n>``.

    Parameters
    ----------
    params : dict
        The node's resolved params; ``ret_minutes`` (list of int >= 1) is read.

    Examples
    --------
    Two lookbacks::

        family = Ret({"ret_minutes": [1, 5]})
        columns = family.compute(frame)   # -> {"ret__r1": array, "ret__r5": array}
    """

    name = "ret"

    def compute(self, frame):
        """Take ``ln(close_t / close_(t-n))`` for each configured ``n``.

        Parameters
        ----------
        frame : _Frame
            The asset's bars.

        Returns
        -------
        dict
            ``{"ret__r<n>": float64 array}``, NaN where the bar ``n`` back does not exist.
        """
        from dskit.pipeline.libs.numpy import log_return

        return {_qualified(self.name, f"r{n}"): log_return(frame.price["close"], n)
                for n in self.params["ret_minutes"]}


class Vol(FeatureFamily):
    """Per-minute return std from dskit's estimators: ``vol__rms_<w>``, ``vol__ewma_<h>``, ``vol__hl_<w>``.

    Parameters
    ----------
    params : dict
        The node's resolved params; ``rms_windows``, ``ewma_half_lives``, ``ewma_lookback_multiple`` and
        ``range_windows`` are read (through :func:`vol_estimators`).

    Examples
    --------
    One RMS window::

        params = MinuteFeatures.resolve({"assets": ["AAA", "BBB"], "rms_windows": [5]})
        columns = Vol(params).compute(frame)   # -> {"vol__rms_5": array, "vol__ewma_15": array, ...}
    """

    name = "vol"

    def compute(self, frame):
        """Square-root each estimator's per-bar variance.

        Parameters
        ----------
        frame : _Frame
            The asset's bars.

        Returns
        -------
        dict
            ``{"vol__<estimator label>": float64 array}``, NaN where a window is incomplete or has a gap.
        """
        import numpy as np

        out = {}
        for estimator in vol_estimators(self.params):
            with np.errstate(invalid="ignore"):
                out[_qualified(self.name, estimator.label())] = np.sqrt(estimator.variance(frame.bars))
        return out


class Flow(FeatureFamily):
    """Volume and trade count against their trailing mean, and the taker-buy share of volume.

    Parameters
    ----------
    params : dict
        The node's resolved params; ``flow_window`` (int >= 1) and ``taker_windows`` (list of int >= 1).

    Examples
    --------
    A 60-minute mean and a 15-minute taker share::

        family = Flow({"flow_window": 60, "taker_windows": [15]})
        columns = family.compute(frame)   # -> {"flow__log_volume": ..., "flow__taker_15": ...}
    """

    name = "flow"

    def compute(self, frame):
        """Take ``ln(x_t / mean(x))`` over ``flow_window`` and the taker share over each window.

        Parameters
        ----------
        frame : _Frame
            The asset's bars.

        Returns
        -------
        dict
            ``flow__log_volume``, ``flow__log_trades`` and ``flow__taker_<w>`` arrays, NaN where unknown.
        """
        import numpy as np
        from dskit.pipeline.libs.numpy import rolling_sum

        width = self.params["flow_window"]
        out = {}
        for column, key in (("log_volume", "volume"), ("log_trades", "trades")):
            series = frame.flow[key]
            mean = rolling_sum(series, width) / width
            with np.errstate(divide="ignore", invalid="ignore"):
                ratio = np.where((series > 0) & (mean > 0), series / mean, np.nan)
                out[_qualified(self.name, column)] = np.log(ratio)
        for window in self.params["taker_windows"]:
            with np.errstate(divide="ignore", invalid="ignore"):
                out[_qualified(self.name, f"taker_{window}")] = (
                    rolling_sum(frame.flow["taker_buy_volume"], window) / rolling_sum(frame.flow["volume"], window))
        return out


class Clock(FeatureFamily):
    """UTC minute of day and day of week as sin and cos, read at the information instant.

    Parameters
    ----------
    params : dict
        The node's resolved params (none is read).

    Examples
    --------
    Four columns, no knob::

        columns = Clock({}).compute(frame)   # -> {"clock__tod_sin": ..., "clock__dow_cos": ...}
    """

    name = "clock"

    def compute(self, frame):
        """Phase the instant within the day and the week.

        Parameters
        ----------
        frame : _Frame
            The asset's bars.

        Returns
        -------
        dict
            ``clock__tod_sin``, ``clock__tod_cos``, ``clock__dow_sin`` and ``clock__dow_cos`` arrays.
        """
        import numpy as np

        minute = (frame.info_ms // BAR_MS) % _MINUTES_PER_DAY
        weekday = ((frame.info_ms // _MS_PER_DAY) + _EPOCH_WEEKDAY) % _DAYS_PER_WEEK
        tod, dow = 2 * np.pi * minute / _MINUTES_PER_DAY, 2 * np.pi * weekday / _DAYS_PER_WEEK
        return {_qualified(self.name, "tod_sin"): np.sin(tod), _qualified(self.name, "tod_cos"): np.cos(tod),
                _qualified(self.name, "dow_sin"): np.sin(dow), _qualified(self.name, "dow_cos"): np.cos(dow)}


class XAsset(FeatureFamily):
    """The OTHER asset's ``ret`` and ``vol`` columns at the same instant: ``xasset__<name>``.

    Parameters
    ----------
    params : dict
        The node's resolved params (those of ``ret`` and ``vol``, read through the partner frame).

    Examples
    --------
    Needs a frame whose ``partner`` is set by the node::

        columns = XAsset(params).compute(frame)   # -> {"xasset__r1": ..., "xasset__rms_5": ...}
    """

    name = "xasset"

    def compute(self, frame):
        """Align the other asset's columns to this asset's bars by ``close_time_ms`` (NaN where it has none).

        Parameters
        ----------
        frame : _Frame
            The asset's bars, with ``partner`` set to the other asset's frame.

        Returns
        -------
        dict
            ``{"xasset__<name>": float64 array}`` for each ``ret`` and ``vol`` column of the partner.
        """
        import numpy as np

        other = frame.partner
        position = np.searchsorted(other.close_time, frame.close_time)
        clipped = np.minimum(position, other.size - 1)
        found = (position < other.size) & (other.close_time[clipped] == frame.close_time)
        out = {}
        for family in ("ret", "vol"):
            for column, values in other.columns(family).items():
                name = column.split(_SEP, 1)[1]
                out[_qualified(self.name, name)] = np.where(found, values[clipped], np.nan)
        return out


class BVol(FeatureFamily):
    """The BVOL index level known at the information instant and its change over ``bvol_change_minutes``.

    Parameters
    ----------
    params : dict
        The node's resolved params; ``bvol_max_age_ms`` and ``bvol_change_minutes`` are read.

    Examples
    --------
    A two-minute age cap and a five-minute change::

        family = BVol({"bvol_max_age_ms": 120000, "bvol_change_minutes": 5})
        columns = family.compute(frame)   # -> {"bvol__level": array, "bvol__change": array}
    """

    name = "bvol"

    def compute(self, frame):
        """Read the newest legal reading now and ``bvol_change_minutes`` earlier (NaN when missing or stale).

        Parameters
        ----------
        frame : _Frame
            The asset's bars, with ``bvol_times`` and ``bvol_values`` set.

        Returns
        -------
        dict
            ``bvol__level`` and ``bvol__change`` float64 arrays.
        """
        level = self._level(frame, frame.info_ms)
        before = self._level(frame, frame.info_ms - self.params["bvol_change_minutes"] * BAR_MS)
        return {_qualified(self.name, "level"): level, _qualified(self.name, "change"): level - before}

    def _level(self, frame, instants):
        """Return the reading strictly before each instant and within the age cap, else NaN."""
        import numpy as np

        times, values = frame.bvol_times, frame.bvol_values
        out = np.full(instants.size, np.nan)
        if times.size == 0:
            return out
        index = prior_index(times, instants)
        safe = np.where(index >= 0, index, 0)
        usable = (index >= 0) & (instants - times[safe] <= self.params["bvol_max_age_ms"])
        return np.where(usable, values[safe], out)


#: family name -> class: the whole vocabulary of a document's ``families``.
FAMILY_TYPES = {cls.name: cls for cls in (Ret, Vol, Flow, Clock, XAsset, BVol)}
#: The family order, which is also the column order: derived from :data:`FAMILY_TYPES`, never restated.
FAMILIES = tuple(FAMILY_TYPES)


def vol_estimators(params):
    """Build the estimators the ``vol`` family names.

    Parameters
    ----------
    params : dict
        The node's resolved params.

    Returns
    -------
    list of dskit.pipeline.libs.vol_estimators.VolEstimator
        Rolling RMS, then EWMA, then range estimators.

    Raises
    ------
    ValueError
        When two estimators would share a label, or a spec is unusable.
    """
    specs = [{"kind": "rms", "window": w} for w in params["rms_windows"]]
    specs += [{"kind": "ewma", "half_life": h, "lookback": h * params["ewma_lookback_multiple"]}
              for h in params["ewma_half_lives"]]
    specs += [{"kind": "high_low", "window": w} for w in params["range_windows"]]
    return build_estimators(specs)


def scale_estimator(params):
    """Build the estimator behind the label scale: the rolling RMS of ``scale_window`` returns.

    Parameters
    ----------
    params : dict
        The node's resolved params.

    Returns
    -------
    dskit.pipeline.libs.vol_estimators.VolEstimator
        A rolling-RMS estimator.
    """
    (estimator,) = build_estimators([{"kind": "rms", "window": params["scale_window"]}])
    return estimator


class MinuteFeatures(ListPortsNode):
    """Feature rows and 15-horizon labels per asset and minute (role ``transform``).

    Inputs: ``klines`` (a list of 1-minute kline rows, each ``{asset, open_time_ms, open, high, low,
    close, volume, close_time_ms, trades, taker_buy_volume}``, for both assets) and optionally ``bvol``
    (a list of ``{asset, calc_time_ms, index_value}``). Outputs: ``records`` (one row per eligible
    ``(asset, t)``: ``asset``, ``t_ms``, the family columns, ``meta__scale`` (the label divisor, not a
    feature), ``y_h1`` ... ``y_h<max_horizon>``)
    and ``provenance`` (``families``, ``need_bars`` and per asset ``bars``, ``rows``, ``refused`` by name,
    ``label_missing`` by horizon, ``bvol_missing``).

    Parameters
    ----------
    params : dict
        ``assets`` (list of exactly two distinct non-empty strings, required; ``xasset`` is the other
        one). Every other knob is optional and its default is a named constant in this module:
        ``families`` (kept families, a non-empty subset of :data:`FAMILIES`), ``ret_minutes``,
        ``rms_windows``, ``ewma_half_lives`` (lists of distinct ints >= 1), ``ewma_lookback_multiple``
        (int >= 1, the EWMA window in half-lives), ``range_windows``, ``flow_window``,
        ``taker_windows``, ``scale_window`` (the label's RMS scale), ``max_horizon`` (labels
        ``y_h1..y_h<max_horizon>``), ``bvol_max_age_ms``, ``bvol_change_minutes``.

    Examples
    --------
    Features without the BVOL family for two assets::

        node = MinuteFeatures("minute", {"assets": ["AAA", "BBB"],
                                         "families": ["ret", "vol", "flow", "clock", "xasset"]})
        out = node.run(None, {"klines": rows})
        # -> out["records"][0]["ret__r1"], out["records"][0]["y_h15"], out["provenance"]["assets"]
    """

    role = "transform"
    outputs = ("records", "provenance")
    LIST_PORTS = ("klines",)
    FAMILIES = FAMILIES
    DEFAULTS = {
        "families": FAMILIES, "ret_minutes": DEFAULT_RET_MINUTES, "rms_windows": DEFAULT_RMS_WINDOWS,
        "ewma_half_lives": DEFAULT_EWMA_HALF_LIVES, "ewma_lookback_multiple": DEFAULT_EWMA_LOOKBACK_MULTIPLE,
        "range_windows": DEFAULT_RANGE_WINDOWS, "flow_window": DEFAULT_FLOW_WINDOW,
        "taker_windows": DEFAULT_TAKER_WINDOWS, "scale_window": DEFAULT_SCALE_WINDOW,
        "max_horizon": DEFAULT_MAX_HORIZON, "bvol_max_age_ms": DEFAULT_BVOL_MAX_AGE_MS,
        "bvol_change_minutes": DEFAULT_BVOL_CHANGE_MINUTES,
    }
    _PARAMS = ("assets",) + tuple(DEFAULTS)

    @classmethod
    def resolve(cls, params):
        """Fill each absent knob from :attr:`DEFAULTS`, the one place a default lives.

        Parameters
        ----------
        params : dict
            The declared params.

        Returns
        -------
        dict
            The declared params plus a default for every knob left out.
        """
        return {**cls.DEFAULTS, **params}

    @classmethod
    def _assets_problems(cls, assets):
        """Problems with ``assets``: exactly two distinct non-empty strings."""
        ok = (isinstance(assets, list) and len(assets) == 2 and all(isinstance(a, str) and a for a in assets)
              and assets[0] != assets[1])
        return [] if ok else [f"assets is required: exactly two distinct non-empty strings, got {assets!r}"]

    @classmethod
    def _families_problems(cls, families):
        """Problems with ``families``: a non-empty list of distinct names from :data:`FAMILIES`."""
        ok = (isinstance(families, (list, tuple)) and families and len(set(families)) == len(families)
              and all(f in FAMILIES for f in families))
        return [] if ok else [f"families must be a non-empty list of distinct names from {list(FAMILIES)}, got {families!r}"]

    @classmethod
    def _list_problems(cls, name, values):
        """Problems with a window list: a non-empty list of distinct ints >= 1."""
        if not isinstance(values, (list, tuple)) or not values or len(set(map(repr, values))) != len(values):
            return [f"{name} must be a non-empty list of distinct ints >= 1, got {values!r}"]
        problems = []
        for value in values:
            check_int_param(problems, name, value, ge=1)
        return problems

    @classmethod
    def validate_params(cls, params):
        """List problems with ``params``, empty when none.

        Parameters
        ----------
        params : dict
            The node's declared params.

        Returns
        -------
        list of str
            One problem per unknown, missing or unusable knob.
        """
        problems = []
        reject_unknown_params(problems, params, cls._PARAMS)
        full = cls.resolve(params)
        problems += cls._assets_problems(full.get("assets"))
        problems += cls._families_problems(full["families"])
        for name in _LIST_KNOBS:
            problems += cls._list_problems(name, full[name])
        for name in _INT_KNOBS:
            check_int_param(problems, name, full[name], ge=1)
        if not problems:
            try:
                vol_estimators(full)
            except ValueError as error:
                problems.append(f"estimators: {error}")
        return problems

    def validate_inputs(self, inputs):
        """Refuse a ``klines`` or a wired ``bvol`` port that is not a list.

        Parameters
        ----------
        inputs : dict
            The wired ports.

        Returns
        -------
        list of str
            One problem per bad port.
        """
        problems = super().validate_inputs(inputs)
        if inputs.get("bvol") is not None and not isinstance(inputs["bvol"], list):
            problems.append(f"bvol must be a list of rows, got {type(inputs['bvol']).__name__}")
        return problems

    # -- reading the rows ---------------------------------------------------

    def _checked(self, row, fields, label):
        """Refuse a row missing a field or carrying a non-number in a numeric one."""
        for field in fields:
            if field not in row or not number_ok(row[field]):
                raise ValueError(f"{self.key}: {label} row has no numeric {field!r}: {row!r}")

    def _kline_rows(self, rows, resolved):
        """Group the kline rows by asset, validated and duplicate-free."""
        assets = resolved["assets"]
        grouped = {asset: [] for asset in assets}
        for row in rows:
            asset = row.get("asset") if isinstance(row, dict) else None
            if asset not in grouped:
                raise ValueError(f"{self.key}: kline row of asset {asset!r}, expected one of {assets}")
            self._checked(row, _KLINE_FIELDS, "kline")
            if row["close_time_ms"] + CLOSE_OFFSET_MS != row["open_time_ms"] + BAR_MS:
                raise ValueError(f"{self.key}: kline close_time_ms must be open_time_ms + {BAR_MS - CLOSE_OFFSET_MS}, "
                                 f"got {row!r}")
            grouped[asset].append(row)
        for asset, group in grouped.items():
            if not group:
                raise ValueError(f"{self.key}: no kline rows for asset {asset!r}")
            times = [r["open_time_ms"] for r in group]
            if len(set(times)) != len(times):
                raise ValueError(f"{self.key}: duplicate open_time_ms for asset {asset!r}")
        return grouped

    def _bvol_rows(self, rows, resolved):
        """Group the BVOL rows by asset as sorted arrays."""
        import numpy as np

        grouped = {asset: [] for asset in resolved["assets"]}
        for row in rows or []:
            asset = row.get("asset") if isinstance(row, dict) else None
            if asset not in grouped:
                raise ValueError(f"{self.key}: bvol row of asset {asset!r}, expected one of {resolved['assets']}")
            self._checked(row, _BVOL_TIME_FIELDS, "bvol")
            if number_ok(row.get("index_value")):  # a NaN / non-numeric reading is a missing one
                grouped[asset].append((row["calc_time_ms"], row["index_value"]))
        out = {}
        for asset, pairs in grouped.items():
            pairs.sort()
            out[asset] = (np.array([p[0] for p in pairs], dtype=np.int64), np.array([p[1] for p in pairs], dtype=np.float64))
        return out

    # -- eligibility --------------------------------------------------------

    @staticmethod
    def need_bars(resolved):
        """Count the consecutive returns every row must have behind it, whichever families are kept.

        Parameters
        ----------
        resolved : dict
            The params with defaults filled in.

        Returns
        -------
        int
            The widest window any family or the label scale reads, in returns: each estimator's own
            ``lookback_bars() - 1`` (a bar count includes the first bar's predecessor), and the plain
            windows of ``ret_minutes``, ``taker_windows`` and ``flow_window``.
        """
        estimators = vol_estimators(resolved) + [scale_estimator(resolved)]
        return max([e.lookback_bars() - 1 for e in estimators] + list(resolved["ret_minutes"])
                   + list(resolved["taker_windows"]) + [resolved["flow_window"]])

    def _scale(self, frame, resolved):
        """Return the label scale: the rolling RMS of the last ``scale_window`` returns (per-minute std)."""
        import numpy as np

        with np.errstate(invalid="ignore"):
            return np.sqrt(scale_estimator(resolved).variance(frame.bars))

    def _bad_taker(self, frame, resolved):
        """Flag each bar whose trailing taker windows hold a taker-buy volume outside ``[0, volume]``."""
        import numpy as np
        from dskit.pipeline.libs.numpy import rolling_sum

        taker, volume = frame.flow["taker_buy_volume"], frame.flow["volume"]
        bad = ((taker < 0) | (taker > volume)).astype(np.float64)
        flagged = bad > 0
        for window in resolved["taker_windows"]:
            flagged |= np.nan_to_num(rolling_sum(bad, window)) > 0
        return flagged

    def _local_reasons(self, frame, scale, need, resolved):
        """Name why each bar is refused on its own account, or '' when it is not."""
        import numpy as np

        checked = [v for family in ("ret", "vol", "flow") for v in frame.columns(family).values()] + [scale]
        finite = np.all([np.isfinite(v) for v in checked], axis=0) & ~self._bad_taker(frame, resolved)
        index = np.arange(frame.size)
        reasons = np.full(frame.size, "", dtype=object)
        reasons[(reasons == "") & (index < need)] = "insufficient_history"
        reasons[(reasons == "") & (frame.run < need)] = "gap_in_window"
        reasons[(reasons == "") & ~finite] = "invalid_value"
        reasons[(reasons == "") & ~(scale > 0)] = "zero_scale"
        return reasons

    def _labels(self, frame, scale, horizon):
        """``ln(close(t+h) / close(t)) / scale`` per bar, NaN where the t+h bar is missing."""
        import numpy as np

        close = frame.price["close"]
        target = frame.open_time + horizon * BAR_MS
        position = np.searchsorted(frame.open_time, target)
        clipped = np.minimum(position, frame.size - 1)
        found = (position < frame.size) & (frame.open_time[clipped] == target)
        with np.errstate(divide="ignore", invalid="ignore"):
            move = np.log(close[clipped] / close)
            return np.where(found, move / scale, np.nan)

    # -- run ----------------------------------------------------------------

    def _frames(self, inputs, resolved):
        """Build both assets' frames, each holding its partner and its BVOL readings."""
        grouped = self._kline_rows(inputs["klines"], resolved)
        bvol = self._bvol_rows(inputs.get("bvol"), resolved)
        frames = {a: _Frame(a, grouped[a], resolved) for a in resolved["assets"]}
        first, second = resolved["assets"]
        frames[first].partner, frames[second].partner = frames[second], frames[first]
        for asset, frame in frames.items():
            frame.bvol_times, frame.bvol_values = bvol[asset]
        return frames

    def _asset_rows(self, frame, resolved, reasons, scale):
        """Build the rows of the bars with no refusal."""
        kept = resolved["families"]
        columns = {}
        for family in FAMILIES:
            if family in kept:
                columns.update(frame.columns(family))
        labels = {f"{_LABEL_PREFIX}{h}": self._labels(frame, scale, h) for h in range(1, resolved["max_horizon"] + 1)}
        rows = []
        for i in range(frame.size):
            if reasons[i]:
                continue
            row = {"asset": frame.asset, "t_ms": int(frame.close_time[i])}
            row.update({name: _cell(values[i]) for name, values in columns.items()})
            row[_SCALE_COLUMN] = _cell(scale[i])
            row.update({name: _cell(values[i]) for name, values in labels.items()})
            rows.append(row)
        return rows, labels

    def _asset_provenance(self, frame, resolved, reasons, labels, rows):
        """Count bars, rows, named refusals and missing labels / BVOL readings for one asset."""
        import numpy as np

        refused = {name: int((reasons == name).sum()) for name in REFUSALS if (reasons == name).any()}
        ok = reasons == ""
        missing = {h[len(_LABEL_PREFIX):]: int((ok & ~np.isfinite(v)).sum()) for h, v in labels.items()}
        bvol_level = frame.columns("bvol")[_qualified("bvol", "level")]
        return {"bars": int(frame.size), "rows": len(rows), "refused": refused, "label_missing": missing,
                "bvol_missing": int((ok & ~np.isfinite(bvol_level)).sum())}

    def run(self, ctx, inputs):
        """Build the feature and label rows.

        Parameters
        ----------
        ctx : object
            The run context (unused).
        inputs : dict
            ``klines`` (list of rows) and optionally ``bvol`` (list of rows).

        Returns
        -------
        dict
            ``records`` (rows sorted by ``t_ms`` then asset order) and ``provenance``.

        Raises
        ------
        ValueError
            On a row of an unknown asset, a missing or non-numeric field, a duplicate bar, a bar whose
            ``close_time_ms`` is not its open plus one bar minus a millisecond, or an asset with no bars.
        """
        resolved = self.resolve(self.params)
        frames = self._frames(inputs, resolved)
        need = self.need_bars(resolved)
        scales = {a: self._scale(f, resolved) for a, f in frames.items()}
        local = {a: self._local_reasons(f, scales[a], need, resolved) for a, f in frames.items()}
        reasons = {a: self._with_partner(frames[a], local[a], local[frames[a].partner.asset], frames[a].partner)
                   for a in frames}
        order = {a: n for n, a in enumerate(resolved["assets"])}
        records, provenance = [], {}
        for asset, frame in frames.items():
            rows, labels = self._asset_rows(frame, resolved, reasons[asset], scales[asset])
            provenance[asset] = self._asset_provenance(frame, resolved, reasons[asset], labels, rows)
            records += rows
        records.sort(key=lambda r: (r["t_ms"], order[r["asset"]]))
        return {"records": records, "provenance": {"families": list(resolved["families"]), "need_bars": need,
                                                   "assets": provenance}}

    @staticmethod
    def _with_partner(frame, own, other, partner):
        """Also refuse the bars whose other asset has no eligible bar at the same instant."""
        import numpy as np

        usable = np.isin(frame.close_time, partner.close_time[other == ""])
        out = own.copy()
        out[(out == "") & ~usable] = "xasset_unavailable"
        return out
