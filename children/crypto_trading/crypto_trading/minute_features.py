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
  by prefix: ``ret`` and ``trend`` (log return, and log price against its moving average, per scale),
  ``vol`` (RMS, EWMA, high-low range and bipower jump share), ``range`` (distance of the close from the
  trailing high and low, in units of the label scale), ``shock`` (minutes since a move beyond k times
  trailing vol, capped), ``flow``, ``clock`` (UTC phase, month end, US open window, minutes to funding),
  ``holiday`` (US exchange holiday and adjacent-weekday flags from dskit's exchange calendar), ``xasset``
  (the other asset's ``ret`` and ``vol`` columns, renamed ``xasset__<name>``, plus the log price ratio of the
  pair and its change per scale) and ``bvol`` (the BVOL level and its change). ``meta__scale`` and the
  labels belong to no family: every ablation arm scores the same target.
- **The scale grid is the ``scales`` param** (:data:`DEFAULT_SCALES`, minutes). ``ret``, ``trend``, ``vol``
  (RMS, high-low, jump share) and the pair change read it, so a scale ablation is a shorter ``scales`` list.
  The trailing ``range`` windows are their own param. Every window is TRAILING (the bars at or before t),
  never a calendar day's high or low, which would include the future.
- **Two views, one set of arrays.** ``columns`` is the same cells as ``records`` as one array per name (plus
  the raw ``seq__`` channels no family owns), cut from the arrays the families already computed; the ``emit``
  param names which ports are built, so ``["columns"]`` never makes the per-row dicts a 3M-row panel cannot hold.
- **The row set never depends on the families or the scales.** Eligibility is decided from every window the
  node could use plus ``history_minutes`` (default: the widest scale of the grid), whichever families and
  scales are kept, so an ablation compares models on identical samples.
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
  all gap-aware); this node takes the square root. The jump share (bipower variation against realised
  variance) is not in that pack and is computed here, over the same gap-aware returns. Clock features are
  UTC, except the US open window, which is wall time in ``us_open_tz`` so it follows daylight saving.
- **Holidays** come from dskit's ``exchange`` calendar (``dskit.production.libs.exchange_calendars``, the
  optional ``exchange_calendars`` library); a document naming the ``holiday`` family refuses by name at
  plan and run when the library is missing, and never gets a silent zero.

Import cost: stdlib + dskit; numpy only inside ``run``.
"""

import math
from abc import ABC, abstractmethod
from functools import lru_cache
from importlib.util import find_spec
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dskit.pipeline.libs.parquet_series import prior_index
from dskit.pipeline.libs.vol_estimators import Bars, build_estimators
from dskit.pipeline.node import check_int_param, reject_unknown_params
from dskit.pipeline.records import number_ok

from .ports import ListPortsNode

__all__ = [
    "BAR_MS", "DEFAULT_EMIT", "DEFAULT_SCALES", "EMIT_PORTS", "FAMILIES", "FAMILY_TYPES", "MinuteFeatures",
    "REFUSALS", "SEQ_COLUMNS", "FeatureFamily",
]

#: The bar this node is built for: one minute, in epoch ms.
BAR_MS = 60_000
#: The two views of the same arrays a run may build: ``records`` (one dict per row) and ``columns`` (one array per name).
EMIT_PORTS = ("records", "columns")
#: Both by default, so the rows port is exactly what it was; a panel too large for row dicts names ``["columns"]``.
DEFAULT_EMIT = EMIT_PORTS
#: Binance stamps a bar's ``close_time_ms`` one millisecond before the next bar opens.
CLOSE_OFFSET_MS = 1
_DAYS_PER_WEEK = 7
_MS_PER_DAY = 86_400_000
_MINUTES_PER_DAY = _MS_PER_DAY // BAR_MS
_MS_PER_HOUR = 3_600_000
_MINUTES_PER_HOUR = _MS_PER_HOUR // BAR_MS
_HOURS_PER_DAY = 24
_WEEKDAYS = 5
_MAX_MONTH_DAYS = 31
#: The calendar library the ``holiday`` family reads through dskit's ``exchange`` calendar.
CALENDAR_LIBRARY = "exchange_calendars"
#: How far (days) past the data the holiday calendar is read, so every adjacent weekday and the session after
#: a closed day exist inside its bounds; a bound that is not a session day is walked in by at most
#: :data:`_BOUND_WALK` days (the library refuses a bound on a weekend or a holiday).
_HOLIDAY_PAD_DAYS = 20
_BOUND_WALK = 7
#: The adjacent weekday is at most this many calendar days away (Friday to Monday).
_ADJACENT_DAYS = 3
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

#: The scale grid (minutes) of the momentum, trend and volatility features: a candidate set, not a choice.
DEFAULT_SCALES = (5, 15, 60, 240, 1440, 10080)
#: Every row has at least this many returns behind it, so dropping a scale never changes the row set.
DEFAULT_HISTORY_MINUTES = max(DEFAULT_SCALES)
DEFAULT_EWMA_HALF_LIVES = (15, 60)
#: The EWMA window is this many half-lives (the weight left beyond it is 0.5 ** 5, about 3 percent).
DEFAULT_EWMA_LOOKBACK_MULTIPLE = 5
DEFAULT_RANGE_WINDOWS = (60, 240, 1440)
#: A move is a shock when its size exceeds this many times the trailing RMS of the window before it.
DEFAULT_SHOCK_K = 4.0
DEFAULT_SHOCK_WINDOW = 60
#: ``shock__since`` never exceeds this (minutes): "no shock for a day" is one state.
DEFAULT_SHOCK_MAX_MINUTES = 1440
DEFAULT_MONTH_END_DAYS = 1
DEFAULT_US_OPEN_TZ = "America/New_York"
DEFAULT_US_OPEN_START_MINUTE = 9 * _MINUTES_PER_HOUR + 30
DEFAULT_US_OPEN_MINUTES = _MINUTES_PER_HOUR
#: Perpetual-swap funding settles at these UTC hours.
DEFAULT_FUNDING_HOURS_UTC = (0, 8, 16)
DEFAULT_HOLIDAY_EXCHANGE = "XNYS"
DEFAULT_FLOW_WINDOW = 60
DEFAULT_TAKER_WINDOWS = (15, 60)
#: Windows an impossible taker-buy volume is checked over besides ``taker_windows``: none, so by default only the
#: windows that have a column are checked. A scale ablation that drops a taker window sets this to the full list.
DEFAULT_TAKER_CHECK_WINDOWS = ()
DEFAULT_SCALE_WINDOW = 60
DEFAULT_MAX_HORIZON = 15
DEFAULT_BVOL_MAX_AGE_MS = 2 * BAR_MS
DEFAULT_BVOL_CHANGE_MINUTES = 15

#: knob -> the smallest window it may hold (a jump share needs two returns, the others one).
_LIST_KNOBS = {"scales": 2, "ewma_half_lives": 1, "range_windows": 1, "taker_windows": 1}
_INT_KNOBS = ("ewma_lookback_multiple", "flow_window", "scale_window", "history_minutes", "shock_window",
              "shock_max_minutes", "max_horizon", "bvol_max_age_ms", "bvol_change_minutes")


def _cell(value):
    """Return a numpy scalar as a Python float, or None when it is not finite."""
    value = float(value)
    return value if math.isfinite(value) else None


def _qualified(family, name):
    """Join a family and a column name."""
    return f"{family}{_SEP}{name}"


#: The label divisor's column: under the reserved ``meta`` prefix, so it is in no family and no arm.
_SCALE_COLUMN = _qualified(_META, "scale")
#: The prefix of the raw sequence channels of the ``columns`` port: no family, so no arm drops one.
_SEQ = "seq"
#: The raw per-bar channels a sequence model reads (one-minute log return, log volume, taker-buy share of volume).
SEQ_COLUMNS = tuple(_qualified(_SEQ, name) for name in ("ret_1m", "log_volume", "taker_share"))
#: The ``columns`` port's keys that are no feature: the asset's ordinal in ``assets`` and the row's ``t_ms``.
_ASSET_COLUMN, _TIME_COLUMN = "asset", "time_ms"


class _LocalClock:
    """Wall time in an IANA zone for an array of epoch-ms instants, daylight saving included.

    Parameters
    ----------
    zone : zoneinfo.ZoneInfo
        The zone.

    Examples
    --------
    New York's local minute of day at a UTC instant in winter::

        clock = _LocalClock(ZoneInfo("America/New_York"))
        clock.local_ms(np.array([1_767_623_400_000])) // 60_000 % 1440   # -> array([570])
    """

    def __init__(self, zone):
        self.zone = zone

    def local_ms(self, instants):
        """Shift each instant by the zone's UTC offset at the START of its UTC hour (every transition is hour-aligned).

        Parameters
        ----------
        instants : numpy.ndarray
            Epoch milliseconds (int64).

        Returns
        -------
        numpy.ndarray
            The same instants as local wall-clock milliseconds since the epoch.
        """
        import numpy as np
        from datetime import datetime, timezone

        hours, inverse = np.unique(instants // _MS_PER_HOUR, return_inverse=True)
        offsets = np.array([int(datetime.fromtimestamp(int(h) * 3600, tz=timezone.utc).astimezone(self.zone)
                                .utcoffset().total_seconds() * 1000) for h in hours], dtype=np.int64)
        return instants + offsets[inverse]


class _Frame:
    """One asset's bars as arrays, with the memoised columns of each family.

    Parameters
    ----------
    asset : str
        The asset's name.
    rows : list of dict or dict
        Its validated kline rows (any order), or the same bars as a ``{field: array}`` mapping (the columnar form
        :meth:`MinuteFeatures._kline_arrays` validates: no per-row dict is ever built).
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
        columns = self._columns(rows)
        self.open_time = columns["open_time_ms"].astype(np.int64)
        self.close_time = columns["close_time_ms"].astype(np.int64)
        self.info_ms = self.close_time + CLOSE_OFFSET_MS
        self.price = {k: columns[k].astype(np.float64) for k in ("open", "high", "low", "close")}
        self.flow = {k: columns[k].astype(np.float64) for k in ("volume", "trades", "taker_buy_volume")}
        self.bars = Bars(self.open_time, BAR_MS, high=self.price["high"], low=self.price["low"],
                         close=self.price["close"])
        self.size = self.open_time.size
        self.run = self._run_lengths()
        self._returns = self._scale = None
        #: Set by the node once both assets exist: the other asset's frame, and this asset's BVOL arrays.
        self.partner, self.bvol_times, self.bvol_values = None, None, None

    @staticmethod
    def _columns(rows):
        """Return the bars as ``{field: array}`` sorted by ``open_time_ms``, from rows or from a mapping."""
        import numpy as np

        if isinstance(rows, dict):
            order = np.argsort(rows["open_time_ms"], kind="stable")
            return {k: np.asarray(rows[k])[order] for k in _KLINE_FIELDS}
        ordered = sorted(rows, key=lambda r: r["open_time_ms"])
        return {k: np.array([r[k] for r in ordered]) for k in _KLINE_FIELDS}

    def _run_lengths(self):
        """Count, per bar, the consecutive contiguous bars ending there (0 at a bar after a gap)."""
        import numpy as np

        index = np.arange(self.bars.contiguous.size, dtype=np.int64)
        return index - np.maximum.accumulate(np.where(self.bars.contiguous, -1, index))  # bars since the last break

    def returns(self):
        """Return each bar's one-minute log return, NaN at a bar with no contiguous predecessor.

        Returns
        -------
        numpy.ndarray
            One value per bar.
        """
        import numpy as np
        from dskit.pipeline.libs.numpy import log_return

        if self._returns is None:
            self._returns = np.where(self.bars.contiguous, log_return(self.price["close"], 1), np.nan)
        return self._returns

    def scale(self):
        """Return the label scale known at each bar: the rolling RMS of ``scale_window`` returns (per-minute std).

        Returns
        -------
        numpy.ndarray
            One value per bar, NaN until the window is full and wherever it holds a gap.
        """
        import numpy as np

        if self._scale is None:
            with np.errstate(invalid="ignore"):
                self._scale = np.sqrt(scale_estimator(self.params).variance(self.bars))
        return self._scale

    def blanked(self, values, bars_needed):
        """Return ``values`` with NaN wherever fewer than ``bars_needed`` contiguous bars end at the bar.

        Parameters
        ----------
        values : numpy.ndarray
            One value per bar.
        bars_needed : int
            The bars a value reads, the current one included.

        Returns
        -------
        numpy.ndarray
            A copy: a value over a window that holds a gap or the start of the data is never reported.
        """
        import numpy as np

        return np.where(self.run >= bars_needed - 1, values, np.nan)

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
    """Log returns over each scale of the grid: ``ret__r<n>``.

    Parameters
    ----------
    params : dict
        The node's resolved params; ``scales`` (list of int >= 2, minutes) is read.

    Examples
    --------
    Two scales::

        family = Ret({"scales": [5, 15]})
        columns = family.compute(frame)   # -> {"ret__r5": array, "ret__r15": array}
    """

    name = "ret"

    def compute(self, frame):
        """Take ``ln(close_t / close_(t-n))`` for each scale ``n``.

        Parameters
        ----------
        frame : _Frame
            The asset's bars.

        Returns
        -------
        dict
            ``{"ret__r<n>": float64 array}``, NaN where the bar ``n`` back does not exist or a gap lies between.
        """
        from dskit.pipeline.libs.numpy import log_return

        return {_qualified(self.name, f"r{n}"): frame.blanked(log_return(frame.price["close"], n), n + 1)
                for n in self.params["scales"]}


class Trend(FeatureFamily):
    """Log price against its simple moving average over each scale: ``trend__ma_<n>``.

    Parameters
    ----------
    params : dict
        The node's resolved params; ``scales`` is read.

    Examples
    --------
    Two scales::

        columns = Trend({"scales": [5, 15]}).compute(frame)   # -> {"trend__ma_5": array, "trend__ma_15": array}
    """

    name = "trend"

    def compute(self, frame):
        """Take ``ln(close_t / mean(close over the last n bars))`` for each scale ``n``.

        Parameters
        ----------
        frame : _Frame
            The asset's bars.

        Returns
        -------
        dict
            ``{"trend__ma_<n>": float64 array}``, NaN until ``n`` contiguous bars exist.
        """
        import numpy as np
        from dskit.pipeline.libs.numpy import rolling_sum

        close = frame.price["close"]
        out = {}
        for n in self.params["scales"]:
            with np.errstate(divide="ignore", invalid="ignore"):
                out[_qualified(self.name, f"ma_{n}")] = frame.blanked(np.log(close / (rolling_sum(close, n) / n)), n)
        return out


class Vol(FeatureFamily):
    """Per-minute return std at each scale: ``vol__rms_<n>``, ``vol__hl_<n>``, ``vol__jump_<n>``, ``vol__ewma_<h>``.

    The RMS, EWMA and high-low columns come from dskit's estimators; ``vol__jump_<n>`` is the share of the
    window's realised variance that bipower variation does not explain (see :meth:`jump_share`).

    Parameters
    ----------
    params : dict
        The node's resolved params; ``scales``, ``ewma_half_lives`` and ``ewma_lookback_multiple`` are read
        (through :func:`vol_estimators`).

    Examples
    --------
    One scale::

        params = MinuteFeatures.resolve({"assets": ["AAA", "BBB"], "scales": [5]})
        columns = Vol(params).compute(frame)   # -> {"vol__rms_5": array, "vol__ewma_15": array, ...}
    """

    name = "vol"

    def compute(self, frame):
        """Square-root each estimator's per-bar variance, then add the jump shares.

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
        for n in self.params["scales"]:
            out[_qualified(self.name, f"jump_{n}")] = self.jump_share(frame.returns(), n)
        return out

    @staticmethod
    def jump_share(returns, n):
        """Return ``max(0, 1 - BV / RV)`` over the last ``n`` returns, with bipower variation BV.

        ``RV`` is the sum of squared returns and ``BV = (pi / 2) * n / (n - 1) * sum |r_k| |r_(k-1)|`` over the
        ``n - 1`` adjacent pairs: a lone jump inflates RV but not BV, so the share nears 1; steady returns give 0.
        A window with no variance has no jump, so its share is 0 (a defined value, not a filled one).

        Parameters
        ----------
        returns : numpy.ndarray
            One-minute log returns, NaN at a gap.
        n : int
            The window, >= 2.

        Returns
        -------
        numpy.ndarray
            A float64 array in [0, 1], NaN where the window holds a NaN.
        """
        import numpy as np
        from dskit.pipeline.libs.numpy import rolling_sum

        magnitude = np.abs(returns)
        pairs = np.concatenate(([np.nan], magnitude[1:] * magnitude[:-1]))
        realised = rolling_sum(returns * returns, n)
        bipower = rolling_sum(pairs, n - 1) * (math.pi / 2) * n / (n - 1)
        with np.errstate(divide="ignore", invalid="ignore"):
            share = np.where(realised > 0, np.clip(1.0 - bipower / realised, 0.0, 1.0), 0.0)
        return np.where(np.isnan(realised) | np.isnan(bipower), np.nan, share)


class Range(FeatureFamily):
    """Distance of the close from the TRAILING high and low, in label-scale units: ``range__hi_<w>``, ``range__lo_<w>``.

    A trailing window is the last ``w`` bars, the current one included; it is never a calendar day's high or low
    (that would include bars after t).

    Parameters
    ----------
    params : dict
        The node's resolved params; ``range_windows`` and ``scale_window`` are read.

    Examples
    --------
    One window::

        columns = Range({"range_windows": [60], "scale_window": 60}).compute(frame)
        # -> {"range__hi_60": array, "range__lo_60": array}
    """

    name = "range"

    def compute(self, frame):
        """Take ``ln(max high / close) / scale`` and ``ln(close / min low) / scale`` over each window.

        Parameters
        ----------
        frame : _Frame
            The asset's bars.

        Returns
        -------
        dict
            ``range__hi_<w>`` and ``range__lo_<w>`` arrays (both >= 0), NaN until ``w`` contiguous bars exist
            and wherever the label scale is not positive.
        """
        import numpy as np
        from dskit.pipeline.libs.numpy import rolling_max, rolling_min

        close, scale = frame.price["close"], frame.scale()
        usable = scale > 0
        out = {}
        for w in self.params["range_windows"]:
            high = rolling_max(frame.price["high"], w)
            low = rolling_min(frame.price["low"], w)
            with np.errstate(divide="ignore", invalid="ignore"):
                out[_qualified(self.name, f"hi_{w}")] = frame.blanked(np.where(usable, np.log(high / close) / scale, np.nan), w)
                out[_qualified(self.name, f"lo_{w}")] = frame.blanked(np.where(usable, np.log(close / low) / scale, np.nan), w)
        return out


class Shock(FeatureFamily):
    """Minutes since the last one-minute move beyond ``shock_k`` times its trailing vol: ``shock__since``.

    Parameters
    ----------
    params : dict
        The node's resolved params; ``shock_k`` (number > 0), ``shock_window`` (int >= 1, the returns whose RMS is
        the trailing vol, ending the minute before the move) and ``shock_max_minutes`` (int >= 1, the cap).

    Examples
    --------
    A 4-sigma move over the last hour, capped at a day::

        columns = Shock({"shock_k": 4.0, "shock_window": 60, "shock_max_minutes": 1440}).compute(frame)
        # -> {"shock__since": array}
    """

    name = "shock"

    def compute(self, frame):
        """Count the minutes back to the last shock, at most ``shock_max_minutes``.

        Parameters
        ----------
        frame : _Frame
            The asset's bars.

        Returns
        -------
        dict
            ``{"shock__since": float64 array}``: 0 when this minute is itself a shock, the cap when none lies
            within it; NaN until ``shock_max_minutes + shock_window`` contiguous returns exist (a capped value
            over a shorter history would be a fill).
        """
        import numpy as np
        from dskit.pipeline.libs.numpy import rolling_sum

        params = self.params
        returns, window, cap = frame.returns(), params["shock_window"], params["shock_max_minutes"]
        before = np.concatenate(([np.nan], rolling_sum(returns * returns, window)[:-1] / window))  # up to t - 1
        with np.errstate(invalid="ignore"):
            shocked = np.abs(returns) > params["shock_k"] * np.sqrt(before)
        index = np.arange(frame.size)
        last = np.maximum.accumulate(np.where(shocked, index, -1))
        since = np.where(last >= 0, np.minimum(index - last, cap), cap).astype(np.float64)
        return {_qualified(self.name, "since"): frame.blanked(since, cap + window + 1)}


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
    """Phase of the day and week, month end, the US open window and the funding clock, read at the information instant.

    Columns: ``clock__tod_sin``, ``clock__tod_cos``, ``clock__dow_sin``, ``clock__dow_cos`` (UTC),
    ``clock__month_end`` (1 on the last ``month_end_days`` UTC days of a month), ``clock__us_open`` (1 on a weekday
    minute inside the window that starts ``us_open_start_minute`` after local midnight in ``us_open_tz`` and lasts
    ``us_open_minutes``) and ``clock__funding_min`` (minutes from the instant to the next ``funding_hours_utc`` hour,
    0 AT one).

    Parameters
    ----------
    params : dict
        The node's resolved params; ``month_end_days``, ``us_open_tz``, ``us_open_start_minute``,
        ``us_open_minutes`` and ``funding_hours_utc`` are read.

    Examples
    --------
    The defaults::

        columns = Clock(MinuteFeatures.resolve({"assets": ["AAA", "BBB"]})).compute(frame)
        # -> {"clock__tod_sin": ..., "clock__us_open": ..., "clock__funding_min": ...}
    """

    name = "clock"

    def compute(self, frame):
        """Phase the instant within the day and the week, and flag the calendar states.

        Parameters
        ----------
        frame : _Frame
            The asset's bars.

        Returns
        -------
        dict
            The seven columns above, as float64 arrays.
        """
        import numpy as np

        minute = (frame.info_ms // BAR_MS) % _MINUTES_PER_DAY
        weekday = ((frame.info_ms // _MS_PER_DAY) + _EPOCH_WEEKDAY) % _DAYS_PER_WEEK
        tod, dow = 2 * np.pi * minute / _MINUTES_PER_DAY, 2 * np.pi * weekday / _DAYS_PER_WEEK
        out = {_qualified(self.name, "tod_sin"): np.sin(tod), _qualified(self.name, "tod_cos"): np.cos(tod),
               _qualified(self.name, "dow_sin"): np.sin(dow), _qualified(self.name, "dow_cos"): np.cos(dow)}
        out[_qualified(self.name, "month_end")] = self._month_end(frame.info_ms)
        out[_qualified(self.name, "us_open")] = self._us_open(frame.info_ms)
        out[_qualified(self.name, "funding_min")] = self._funding_minutes(minute)
        return out

    def _month_end(self, info_ms):
        """Flag the instants whose UTC date is among the last ``month_end_days`` days of its month."""
        import numpy as np

        day = (info_ms // _MS_PER_DAY).astype("datetime64[D]")
        first = day.astype("datetime64[M]")
        into_month = (day - first.astype("datetime64[D]")).astype(np.int64)  # 0 on the first day
        length = ((first + np.timedelta64(1, "M")).astype("datetime64[D]") - first.astype("datetime64[D]")).astype(np.int64)
        return ((length - into_month) <= self.params["month_end_days"]).astype(np.float64)

    def _us_open(self, info_ms):
        """Flag the weekday instants inside the open window, on the wall clock of ``us_open_tz``."""
        import numpy as np

        local = _LocalClock(ZoneInfo(self.params["us_open_tz"])).local_ms(info_ms)
        minute = (local // BAR_MS) % _MINUTES_PER_DAY
        weekday = ((local // _MS_PER_DAY) + _EPOCH_WEEKDAY) % _DAYS_PER_WEEK
        start = self.params["us_open_start_minute"]
        inside = (minute >= start) & (minute < start + self.params["us_open_minutes"])
        return (inside & (weekday < _WEEKDAYS)).astype(np.float64)

    def _funding_minutes(self, minute):
        """Return the minutes from each UTC minute of day to the next funding hour at or after it."""
        import numpy as np

        ahead = [(hour * _MINUTES_PER_HOUR - minute) % _MINUTES_PER_DAY for hour in self.params["funding_hours_utc"]]
        return np.min(ahead, axis=0).astype(np.float64)


@lru_cache(maxsize=32)
def _closed_weekdays(exchange, first_day, last_day):
    """Return ``(tz_name, closed)``: the exchange's zone and the weekday day-numbers in range with no session.

    A day number counts days since the epoch in the exchange's own zone. The answer is a fact of the published
    calendar, so it is cached by its arguments.
    """
    from datetime import date, timedelta

    from dskit.production.base import ProductionError
    from dskit.production.libs.exchange_calendars import ExchangeCalendar

    epoch = date(1970, 1, 1)
    lo, hi = epoch + timedelta(days=first_day - _HOLIDAY_PAD_DAYS), epoch + timedelta(days=last_day + _HOLIDAY_PAD_DAYS)
    failure = None
    # the library refuses a bound that is not a session day, so walk each bound in, smallest total walk first
    for total in range(2 * _BOUND_WALK - 1):
        for step in range(max(0, total - _BOUND_WALK + 1), min(total, _BOUND_WALK - 1) + 1):
            bounds = {"from": (lo + timedelta(days=step)).isoformat(),
                      "until": (hi - timedelta(days=total - step)).isoformat()}
            try:
                calendar = ExchangeCalendar({"exchange": exchange, "bounds": bounds})
            except (ProductionError, ValueError) as error:  # the library's own out-of-bounds error is a ValueError
                failure = error
                continue
            return calendar.tz_name, _scan_closed(calendar, first_day, last_day)
    raise ValueError(f"holiday: dskit's exchange calendar {exchange!r} refused every bound near "
                     f"{lo.isoformat()}..{hi.isoformat()}: {failure}")


def _scan_closed(calendar, first_day, last_day):
    """Name the weekday day-numbers in range on which ``calendar`` has no session (the next session starts later)."""
    from datetime import date, datetime, time, timedelta, timezone

    zone, epoch, closed = ZoneInfo(calendar.tz_name), date(1970, 1, 1), []
    for number in range(first_day, last_day + 1):
        if (number + _EPOCH_WEEKDAY) % _DAYS_PER_WEEK >= _WEEKDAYS:
            continue
        day = epoch + timedelta(days=number)
        midnight = datetime.combine(day, time(), tzinfo=zone)
        start_ms, _end = calendar.window("session", int(midnight.astimezone(timezone.utc).timestamp() * 1000))
        started = datetime.fromtimestamp(start_ms / 1000, tz=timezone.utc).astimezone(zone).date()
        if started != day:
            closed.append(number)
    return tuple(closed)


class Holiday(FeatureFamily):
    """US exchange holiday flags from dskit's exchange calendar: ``holiday__is``, ``holiday__prev``, ``holiday__next``.

    A holiday is a weekday on which the exchange (``holiday_exchange``) has no session. The day is the exchange's
    local date at the information instant. ``prev`` / ``next`` flag that the previous / next WEEKDAY is a holiday,
    so a Friday before a Monday holiday and the Tuesday after it are both marked, and so are the weekend days
    between. Reuses :class:`dskit.production.libs.exchange_calendars.ExchangeCalendar`; the library is imported only
    inside :meth:`compute`.

    Parameters
    ----------
    params : dict
        The node's resolved params; ``holiday_exchange`` (str, the library's calendar code) is read.

    Examples
    --------
    The New York Stock Exchange::

        columns = Holiday({"holiday_exchange": "XNYS"}).compute(frame)
        # -> {"holiday__is": array, "holiday__prev": array, "holiday__next": array}
    """

    name = "holiday"

    @staticmethod
    def library_problem():
        """Say why the calendar library is missing, or None when it is installed.

        Returns
        -------
        str or None
            A problem naming the library, or None.
        """
        if find_spec(CALENDAR_LIBRARY) is None:
            return (f"the holiday family reads dskit's exchange calendar, which needs the {CALENDAR_LIBRARY} library "
                    f"(pip install {CALENDAR_LIBRARY}); install it or drop 'holiday' from families")
        return None

    def compute(self, frame):
        """Look every bar's local date up in the exchange's calendar.

        Parameters
        ----------
        frame : _Frame
            The asset's bars.

        Returns
        -------
        dict
            The three flags as float64 arrays.

        Raises
        ------
        ValueError
            When the calendar library is missing or the calendar cannot be read for the data's dates.
        """
        import numpy as np
        from dskit.production.base import ProductionError

        problem = self.library_problem()
        if problem:
            raise ValueError(f"holiday: {problem}")
        exchange = self.params["holiday_exchange"]
        utc_days = frame.info_ms // _MS_PER_DAY
        first, last = int(utc_days.min()) - _ADJACENT_DAYS - 1, int(utc_days.max()) + _ADJACENT_DAYS + 1
        try:
            tz_name, closed_days = _closed_weekdays(exchange, first, last)
        except ProductionError as error:
            raise ValueError(f"holiday: dskit's exchange calendar {exchange!r}: {error}") from error
        closed = np.zeros(last - first + 1)
        closed[np.array(closed_days, dtype=np.int64) - first] = 1.0
        local = _LocalClock(ZoneInfo(tz_name)).local_ms(frame.info_ms) // _MS_PER_DAY
        weekday = (local + _EPOCH_WEEKDAY) % _DAYS_PER_WEEK
        back = np.where(weekday == 0, _ADJACENT_DAYS, np.where(weekday == _DAYS_PER_WEEK - 1, 2, 1))
        ahead = np.where(weekday == _WEEKDAYS - 1, _ADJACENT_DAYS, np.where(weekday == _WEEKDAYS, 2, 1))
        return {_qualified(self.name, "is"): closed[local - first],
                _qualified(self.name, "prev"): closed[local - back - first],
                _qualified(self.name, "next"): closed[local + ahead - first]}


class XAsset(FeatureFamily):
    """The OTHER asset's ``ret`` and ``vol`` columns at the same instant, and the pair's log price ratio.

    Columns: ``xasset__<name>`` for each ``ret`` and ``vol`` column of the partner, ``xasset__pair_log`` (the log of
    the price of ``assets[0]`` over ``assets[1]``, the same value on both assets' rows) and
    ``xasset__pair_chg_<n>`` (its change over each scale).

    Parameters
    ----------
    params : dict
        The node's resolved params (those of ``ret`` and ``vol``, read through the partner frame, plus ``assets``
        and ``scales``).

    Examples
    --------
    Needs a frame whose ``partner`` is set by the node::

        columns = XAsset(params).compute(frame)   # -> {"xasset__r5": ..., "xasset__rms_5": ..., "xasset__pair_log": ...}
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
            The partner's columns, the pair log ratio and its changes, as float64 arrays.
        """
        import numpy as np
        from dskit.pipeline.libs.numpy import lag

        other = frame.partner
        position = np.searchsorted(other.close_time, frame.close_time)
        clipped = np.minimum(position, other.size - 1)
        found = (position < other.size) & (other.close_time[clipped] == frame.close_time)
        out = {}
        for family in ("ret", "vol"):
            for column, values in other.columns(family).items():
                name = column.split(_SEP, 1)[1]
                out[_qualified(self.name, name)] = np.where(found, values[clipped], np.nan)
        mine, theirs = frame.price["close"], np.where(found, other.price["close"][clipped], np.nan)
        first_is_mine = frame.asset == self.params["assets"][0]
        with np.errstate(divide="ignore", invalid="ignore"):
            pair = np.log(mine / theirs if first_is_mine else theirs / mine)
        out[_qualified(self.name, "pair_log")] = pair
        for n in self.params["scales"]:
            out[_qualified(self.name, f"pair_chg_{n}")] = pair - lag(pair, n)
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
FAMILY_TYPES = {cls.name: cls for cls in (Ret, Trend, Vol, Range, Shock, Flow, Clock, Holiday, XAsset, BVol)}
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
        Rolling RMS and high-low at each scale, with the EWMAs between them.

    Raises
    ------
    ValueError
        When two estimators would share a label, or a spec is unusable.
    """
    specs = [{"kind": "rms", "window": w} for w in params["scales"]]
    specs += [{"kind": "ewma", "half_life": h, "lookback": h * params["ewma_lookback_multiple"]}
              for h in params["ewma_half_lives"]]
    specs += [{"kind": "high_low", "window": w} for w in params["scales"]]
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
    feature), ``y_h1`` ... ``y_h<max_horizon>``),
    ``columns`` (the same cells as ``name -> 1-D float64 array``, sorted by asset (its ordinal in ``assets``,
    as ``asset``) then time (``time_ms``, the row's ``t_ms``); it adds the raw channels ``seq__ret_1m``,
    ``seq__log_volume`` and ``seq__taker_share``, which belong to no family and so to no ablation arm, and a
    missing cell is NaN where the row holds None) and ``provenance`` (``families``, ``need_bars`` and per asset ``bars``, ``rows``, ``refused`` by name,
    ``label_missing`` by horizon, ``bvol_missing``).

    Parameters
    ----------
    params : dict
        ``assets`` (list of exactly two distinct non-empty strings, required; ``xasset`` is the other
        one). Every other knob is optional and its default is a named constant in this module:
        ``families`` (kept families, a non-empty subset of :data:`FAMILIES`), ``scales`` (the minute grid of
        ``ret``, ``trend``, the ``vol`` RMS / high-low / jump columns and the pair change; ints >= 2),
        ``history_minutes`` (returns every row must have behind it, so a shorter ``scales`` keeps the row
        set), ``ewma_half_lives`` (lists of distinct ints >= 1), ``ewma_lookback_multiple`` (int >= 1, the
        EWMA window in half-lives), ``range_windows`` (trailing high / low windows), ``shock_k`` (number > 0),
        ``shock_window`` and ``shock_max_minutes`` (ints >= 1), ``flow_window``, ``taker_windows``,
        ``taker_check_windows`` (distinct ints >= 1, may be empty: windows an impossible taker-buy volume is also
        refused over, so an arm that drops a taker window keeps the row set),
        ``scale_window`` (the label's RMS scale, also the divisor of ``range``), ``month_end_days``,
        ``us_open_tz`` / ``us_open_start_minute`` / ``us_open_minutes`` (the wall-clock window),
        ``funding_hours_utc`` (UTC hours 0..23), ``holiday_exchange`` (a calendar code of the
        ``exchange_calendars`` library), ``max_horizon`` (labels ``y_h1..y_h<max_horizon>``),
        ``bvol_max_age_ms``, ``bvol_change_minutes``, ``emit`` (non-empty subset of :data:`EMIT_PORTS`: the
        ports built; a port not named is None, and ``["columns"]`` builds no row dict at all).

    Examples
    --------
    Features without the BVOL family for two assets::

        node = MinuteFeatures("minute", {"assets": ["AAA", "BBB"],
                                         "families": ["ret", "vol", "flow", "clock", "xasset"]})
        out = node.run(None, {"klines": rows})
        # -> out["records"][0]["ret__r5"], out["records"][0]["y_h15"], out["provenance"]["assets"]
    """

    role = "transform"
    outputs = ("records", "provenance", "columns")
    LIST_PORTS = ()  # klines / bvol are lists of rows OR per-asset field arrays: validate_inputs says which
    FAMILIES = FAMILIES
    DEFAULTS = {
        "families": FAMILIES, "scales": DEFAULT_SCALES, "history_minutes": DEFAULT_HISTORY_MINUTES,
        "ewma_half_lives": DEFAULT_EWMA_HALF_LIVES, "ewma_lookback_multiple": DEFAULT_EWMA_LOOKBACK_MULTIPLE,
        "range_windows": DEFAULT_RANGE_WINDOWS, "shock_k": DEFAULT_SHOCK_K, "shock_window": DEFAULT_SHOCK_WINDOW,
        "shock_max_minutes": DEFAULT_SHOCK_MAX_MINUTES, "flow_window": DEFAULT_FLOW_WINDOW,
        "taker_windows": DEFAULT_TAKER_WINDOWS, "taker_check_windows": DEFAULT_TAKER_CHECK_WINDOWS,
        "scale_window": DEFAULT_SCALE_WINDOW,
        "month_end_days": DEFAULT_MONTH_END_DAYS, "us_open_tz": DEFAULT_US_OPEN_TZ,
        "us_open_start_minute": DEFAULT_US_OPEN_START_MINUTE, "us_open_minutes": DEFAULT_US_OPEN_MINUTES,
        "funding_hours_utc": DEFAULT_FUNDING_HOURS_UTC, "holiday_exchange": DEFAULT_HOLIDAY_EXCHANGE,
        "max_horizon": DEFAULT_MAX_HORIZON, "bvol_max_age_ms": DEFAULT_BVOL_MAX_AGE_MS,
        "bvol_change_minutes": DEFAULT_BVOL_CHANGE_MINUTES, "emit": DEFAULT_EMIT,
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
    def _emit_problems(cls, emit):
        """Problems with ``emit``: a non-empty list of distinct names from :data:`EMIT_PORTS`."""
        ok = (isinstance(emit, (list, tuple)) and emit and len(set(emit)) == len(emit)
              and all(e in EMIT_PORTS for e in emit))
        return [] if ok else [f"emit must be a non-empty list of distinct names from {list(EMIT_PORTS)}, got {emit!r}"]

    @classmethod
    def _list_problems(cls, name, values, ge=1):
        """Problems with a window list: a non-empty list of distinct ints >= ``ge``."""
        if not isinstance(values, (list, tuple)) or not values or len(set(map(repr, values))) != len(values):
            return [f"{name} must be a non-empty list of distinct ints >= {ge}, got {values!r}"]
        problems = []
        for value in values:
            check_int_param(problems, name, value, ge=ge)
        return problems

    @classmethod
    def _shock_k_problems(cls, value):
        """Problems with ``shock_k``: a finite number above zero."""
        ok = number_ok(value) and value > 0
        return [] if ok else [f"shock_k must be a finite number > 0, got {value!r}"]

    @classmethod
    def _calendar_problems(cls, full):
        """Problems with the clock and holiday knobs that the plain int / list checks do not cover."""
        problems = []
        check_int_param(problems, "month_end_days", full["month_end_days"], ge=1)
        if not problems and full["month_end_days"] > _MAX_MONTH_DAYS:
            problems.append(f"month_end_days must be at most {_MAX_MONTH_DAYS}, got {full['month_end_days']!r}")
        problems += cls._us_open_problems(full)
        hours = full["funding_hours_utc"]
        problems += cls._list_problems("funding_hours_utc", hours, ge=0)
        if not problems and any(h >= _HOURS_PER_DAY for h in hours):
            problems.append(f"funding_hours_utc must be UTC hours 0..{_HOURS_PER_DAY - 1}, got {hours!r}")
        if not isinstance(full["holiday_exchange"], str) or not full["holiday_exchange"]:
            problems.append(f"holiday_exchange must be a non-empty calendar code, got {full['holiday_exchange']!r}")
        return problems

    @classmethod
    def _us_open_problems(cls, full):
        """Problems with the open-window zone, start minute and length (the window stays inside one local day)."""
        problems, zone = [], full["us_open_tz"]
        try:
            ZoneInfo(zone)
        except (ZoneInfoNotFoundError, ValueError, TypeError, OSError):
            problems.append(f"us_open_tz must be an IANA zone name, got {zone!r}")
        start = full["us_open_start_minute"]
        check_int_param(problems, "us_open_start_minute", start, ge=0)
        check_int_param(problems, "us_open_minutes", full["us_open_minutes"], ge=1)
        if not problems and start + full["us_open_minutes"] > _MINUTES_PER_DAY:
            problems.append(f"us_open_start_minute + us_open_minutes must be at most {_MINUTES_PER_DAY} "
                            f"(the window stays inside one local day), got {start} + {full['us_open_minutes']}")
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
        problems += cls._emit_problems(full["emit"])
        for name, smallest in _LIST_KNOBS.items():
            problems += cls._list_problems(name, full[name], ge=smallest)
        if full["taker_check_windows"] not in ((), []):  # an empty extra list is the default, not a mistake
            problems += cls._list_problems("taker_check_windows", full["taker_check_windows"])
        for name in _INT_KNOBS:
            check_int_param(problems, name, full[name], ge=1)
        problems += cls._shock_k_problems(full["shock_k"])
        problems += cls._calendar_problems(full)
        if not problems and "holiday" in full["families"] and Holiday.library_problem():
            problems.append(Holiday.library_problem())
        if not problems:
            try:
                vol_estimators(full)
            except ValueError as error:
                problems.append(f"estimators: {error}")
        return problems

    def validate_inputs(self, inputs):
        """Refuse a ``klines`` or a wired ``bvol`` port that is neither a list of rows nor a mapping of arrays.

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
        for port, required in (("klines", True), ("bvol", False)):
            value = inputs.get(port)
            if value is None and not required:
                continue
            columnar = isinstance(value, dict) and all(isinstance(block, dict) for block in value.values())
            if not (isinstance(value, list) or columnar):
                problems.append(f"{port} must be a list of rows or a per-asset mapping of field arrays, "
                                f"got {type(value).__name__}")
        return problems

    # -- reading the rows ---------------------------------------------------

    def _checked(self, row, fields, label):
        """Refuse a row missing a field or carrying a non-number in a numeric one."""
        for field in fields:
            if field not in row or not number_ok(row[field]):
                raise ValueError(f"{self.key}: {label} row has no numeric {field!r}: {row!r}")

    def _kline_arrays(self, mapping, resolved):
        """Validate the columnar klines (``{asset: {field: array}}``) with the row form's refusals; return them."""
        import numpy as np

        assets = resolved["assets"]
        if set(mapping) != set(assets):
            raise ValueError(f"{self.key}: kline arrays for assets {sorted(mapping)}, expected {assets}")
        for asset in assets:
            block = mapping[asset]
            missing = [f for f in _KLINE_FIELDS if f not in block]
            if missing:
                raise ValueError(f"{self.key}: kline arrays of asset {asset!r} lack {missing}")
            sizes = {np.asarray(block[f]).size for f in _KLINE_FIELDS}
            if len(sizes) != 1 or not sizes.pop():
                raise ValueError(f"{self.key}: no kline rows for asset {asset!r}, or unequal field lengths")
            for field in _KLINE_FIELDS:
                values = np.asarray(block[field])
                if values.dtype.kind not in "iuf" or not np.isfinite(values.astype(np.float64)).all():
                    raise ValueError(f"{self.key}: kline arrays of asset {asset!r} have a non-numeric or non-finite "
                                     f"{field!r}")
            bad = np.asarray(block["close_time_ms"]) + CLOSE_OFFSET_MS != np.asarray(block["open_time_ms"]) + BAR_MS
            if bad.any():
                raise ValueError(f"{self.key}: kline close_time_ms must be open_time_ms + {BAR_MS - CLOSE_OFFSET_MS}, "
                                 f"first failure at open_time_ms {np.asarray(block['open_time_ms'])[bad][0]}")
            times = np.sort(np.asarray(block["open_time_ms"]), axis=None)  # equal neighbours: a duplicate
            if (times[1:] == times[:-1]).any():
                raise ValueError(f"{self.key}: duplicate open_time_ms for asset {asset!r}")
        return {asset: mapping[asset] for asset in assets}

    def _kline_rows(self, rows, resolved):
        """Group the kline rows by asset, validated and duplicate-free."""
        if isinstance(rows, dict):
            return self._kline_arrays(rows, resolved)
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

    def _bvol_arrays(self, mapping, resolved):
        """Return ``{asset: (times, values)}`` sorted by time then value, a non-finite reading dropped."""
        import numpy as np

        if set(mapping) - set(resolved["assets"]):
            raise ValueError(f"{self.key}: bvol arrays of assets {sorted(set(mapping) - set(resolved['assets']))}, "
                             f"expected {resolved['assets']}")
        out = {}
        for asset in resolved["assets"]:
            block = mapping.get(asset)
            times = np.asarray(block["calc_time_ms"]) if block else np.empty(0, dtype=np.int64)
            values = np.asarray(block["index_value"], dtype=np.float64) if block else np.empty(0)
            if times.dtype.kind not in "iuf" or not np.isfinite(times.astype(np.float64)).all():
                raise ValueError(f"{self.key}: bvol arrays of asset {asset!r} have a non-numeric calc_time_ms")
            keep = np.isfinite(values)  # a NaN reading is a missing one
            times, values = times[keep], values[keep]
            order = np.lexsort((values, times))
            out[asset] = (times[order].astype(np.int64), values[order])
        return out

    def _bvol_rows(self, rows, resolved):
        """Group the BVOL rows by asset as sorted arrays."""
        import numpy as np

        if isinstance(rows, dict):
            return self._bvol_arrays(rows, resolved)

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
            ``lookback_bars() - 1`` (a bar count includes the first bar's predecessor), the plain windows
            of ``scales``, ``range_windows``, the taker windows (with ``taker_check_windows``) and ``flow_window``,
            the shock cap plus its
            trailing window, and ``history_minutes`` (the floor that keeps the row set scale-independent).
        """
        estimators = vol_estimators(resolved) + [scale_estimator(resolved)]
        windows = [e.lookback_bars() - 1 for e in estimators] + list(resolved["scales"])  # ret, trend, jump, pair change
        windows += [w - 1 for w in resolved["range_windows"]]
        windows += [resolved["shock_max_minutes"] + resolved["shock_window"], resolved["history_minutes"]]
        return max(windows + list(MinuteFeatures.taker_checked_windows(resolved)) + [resolved["flow_window"]])

    @staticmethod
    def taker_checked_windows(resolved):
        """Return the windows an impossible taker-buy volume is refused over: ``taker_windows`` and the extras.

        Parameters
        ----------
        resolved : dict
            The params with defaults filled in.

        Returns
        -------
        tuple of int
            Distinct windows, ``taker_windows`` first.
        """
        return tuple(dict.fromkeys([*resolved["taker_windows"], *resolved["taker_check_windows"]]))

    def _bad_taker(self, frame, resolved):
        """Flag each bar whose trailing checked taker windows hold a taker-buy volume outside ``[0, volume]``."""
        import numpy as np
        from dskit.pipeline.libs.numpy import rolling_sum

        taker, volume = frame.flow["taker_buy_volume"], frame.flow["volume"]
        bad = ((taker < 0) | (taker > volume)).astype(np.float64)
        flagged = bad > 0
        for window in self.taker_checked_windows(resolved):
            flagged |= np.nan_to_num(rolling_sum(bad, window)) > 0
        return flagged

    def _local_reasons(self, frame, scale, need, resolved):
        """Name why each bar is refused on its own account, or '' when it is not."""
        import numpy as np

        families = ("ret", "trend", "vol", "range", "shock", "flow")
        checked = [v for family in families for v in frame.columns(family).values()] + [scale]
        finite = np.all([np.isfinite(v) for v in checked], axis=0) & ~self._bad_taker(frame, resolved)
        index = np.arange(frame.size)
        reasons = np.full(frame.size, "", dtype=object)
        reasons[(reasons == "") & (index < need)] = "insufficient_history"
        reasons[(reasons == "") & (frame.run < need)] = "gap_in_window"
        reasons[(reasons == "") & (scale == 0)] = "zero_scale"  # before the finite check: range divides by it
        reasons[(reasons == "") & ~finite] = "invalid_value"
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

    def _asset_arrays(self, frame, resolved, scale):
        """Return every per-bar array of the asset: kept families, the label divisor, the labels, the channels."""
        import numpy as np

        arrays = {}
        for family in FAMILIES:
            if family in resolved["families"]:
                arrays.update(frame.columns(family))
        arrays[_SCALE_COLUMN] = scale
        for name, values in zip(SEQ_COLUMNS, self._channels(frame)):
            arrays[name] = values
        arrays.update({f"{_LABEL_PREFIX}{h}": self._labels(frame, scale, h)
                       for h in range(1, resolved["max_horizon"] + 1)})
        return {name: np.asarray(values, dtype=np.float64) for name, values in arrays.items()}

    @staticmethod
    def _channels(frame):
        """Return the raw sequence channels per bar: one-minute log return, log volume and taker-buy share of volume."""
        import numpy as np

        volume = frame.flow["volume"]
        with np.errstate(divide="ignore", invalid="ignore"):
            return frame.returns(), np.log(volume), frame.flow["taker_buy_volume"] / volume

    def _asset_rows(self, frame, arrays, kept):
        """Build the rows of the bars in the ``kept`` mask: ``asset``, ``t_ms``, then the cells of ``arrays``."""
        rows = []
        for i in kept.nonzero()[0]:
            row = {"asset": frame.asset, "t_ms": int(frame.close_time[i])}
            row.update({name: _cell(values[i]) for name, values in arrays.items() if name not in SEQ_COLUMNS})
            rows.append(row)
        return rows

    def _asset_columns(self, frame, arrays, kept, code):
        """Cut the kept bars out of every array (a copy), led by the asset's ordinal and the row's ``t_ms``."""
        import numpy as np

        size = int(kept.sum())
        columns = {_ASSET_COLUMN: np.full(size, code, dtype=np.int64), _TIME_COLUMN: frame.close_time[kept]}
        columns.update({name: values[kept] for name, values in arrays.items()})
        return columns

    @staticmethod
    def _joined(pieces):
        """Concatenate the per-asset column dicts name by name, in the order given (asset, then time).

        Each piece gives up a column as it is joined, so the peak is one extra column, not a second copy of the table.
        """
        import numpy as np

        names = list(pieces[0])
        return {name: np.concatenate([piece.pop(name) for piece in pieces]) for name in names}

    def _asset_provenance(self, frame, resolved, reasons, labels, count):
        """Count bars, rows, named refusals and missing labels / BVOL readings for one asset."""
        import numpy as np

        refused = {name: int((reasons == name).sum()) for name in REFUSALS if (reasons == name).any()}
        ok = reasons == ""
        missing = {h[len(_LABEL_PREFIX):]: int((ok & ~np.isfinite(v)).sum()) for h, v in labels.items()}
        bvol_level = frame.columns("bvol")[_qualified("bvol", "level")]
        return {"bars": int(frame.size), "rows": count, "refused": refused, "label_missing": missing,
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
            ``records`` (rows sorted by ``t_ms`` then asset order; None unless ``emit`` names it), ``columns``
            (arrays sorted by asset then time; None unless ``emit`` names it) and ``provenance``.

        Raises
        ------
        ValueError
            On a row of an unknown asset, a missing or non-numeric field, a duplicate bar, a bar whose
            ``close_time_ms`` is not its open plus one bar minus a millisecond, or an asset with no bars.
        """
        resolved = self.resolve(self.params)
        frames = self._frames(inputs, resolved)
        need = self.need_bars(resolved)
        scales = {a: f.scale() for a, f in frames.items()}
        local = {a: self._local_reasons(f, scales[a], need, resolved) for a, f in frames.items()}
        reasons = {a: self._with_partner(frames[a], local[a], local[frames[a].partner.asset], frames[a].partner)
                   for a in frames}
        order = {a: n for n, a in enumerate(resolved["assets"])}
        records, pieces, provenance = [], [], {}
        for asset, frame in frames.items():
            kept = reasons[asset] == ""
            arrays = self._asset_arrays(frame, resolved, scales[asset])
            labels = {n: v for n, v in arrays.items() if n.startswith(_LABEL_PREFIX)}
            provenance[asset] = self._asset_provenance(frame, resolved, reasons[asset], labels, int(kept.sum()))
            if "records" in resolved["emit"]:
                records += self._asset_rows(frame, arrays, kept)
            if "columns" in resolved["emit"]:
                pieces.append(self._asset_columns(frame, arrays, kept, order[asset]))
            del arrays, labels  # the full-length arrays are not needed once the kept bars are cut out of them
        records.sort(key=lambda r: (r["t_ms"], order[r["asset"]]))
        return {"records": records if "records" in resolved["emit"] else None,
                "columns": self._joined(pieces) if pieces else None,
                "provenance": {"families": list(resolved["families"]), "need_bars": need, "assets": provenance}}

    @staticmethod
    def _with_partner(frame, own, other, partner):
        """Also refuse the bars whose other asset has no eligible bar at the same instant."""
        import numpy as np

        eligible = np.sort(partner.close_time[other == ""])  # membership by sorted search: np.isin hashes slowly
        position = np.minimum(np.searchsorted(eligible, frame.close_time), max(eligible.size - 1, 0))
        usable = eligible[position] == frame.close_time if eligible.size else np.zeros(frame.size, dtype=bool)
        out = own.copy()
        out[(out == "") & ~usable] = "xasset_unavailable"
        return out
