"""Switchable feature blocks: time of day, bar-derived, cross-stock.

ADR-0071. Three named blocks — ``tod`` (P2), ``bar`` (P3a) and ``cross``
(P3b) — each selectable on its own so a run can measure one, or any
subset, against the same baseline. Nothing here is on by default.

**Causality.** Every column in this module is computable at the bar it
is stamped with, from that bar and earlier bars only. Two rules keep it
that way and both are tested:

1. No column reads an index greater than its own row's. Rolling helpers
   from :mod:`dskit.pipeline.libs.numpy` are causal (the window ends at
   ``t`` inclusive), and every explicit index arithmetic subtracts.
2. Anything that needs a fitted statistic — a time-of-day volatility
   curve, a per-bucket mean, a volume norm — is NOT computed here. It is
   fitted on the training fold alone by :func:`fit_fold_stats` and
   applied by :func:`apply_fold_stats`, once per fold.

**NaN discipline.** ``_frame_matrix`` drops a scored row on any
non-finite column, so a column that is NaN inside every session costs
that fraction of every session forever. Windows that would do that are
cross-session on purpose (a one-time warmup instead of a daily one),
exactly as the universe's own 60-minute scale already is, and the two
first-half-hour columns read zero before they are formed rather than
NaN — ``is_open30`` is the flag that says so.
"""

from __future__ import annotations

import calendar
import math

from dskit.pipeline.libs.numpy import rolling_sum

__all__ = [
    "BAR_FOLD_NAMES",
    "BAR_NAMES",
    "BLOCKS",
    "BLOCK_BAR",
    "BLOCK_CROSS",
    "BLOCK_TOD",
    "CROSS_NAMES",
    "TOD_FOLD_NAMES",
    "TOD_NAMES",
    "apply_fold_stats",
    "block_causal_names",
    "block_columns",
    "block_feature_names",
    "block_fold_names",
    "block_problems",
    "fit_fold_stats",
    "normalise_blocks",
]

#: Block A — time of day (P2).
BLOCK_TOD = "tod"
#: Block B — bar-derived inputs (P3a).
BLOCK_BAR = "bar"
#: Block C — cross-stock, market and sector inputs (P3b).
BLOCK_CROSS = "cross"
#: Declaration order; also the emission order of the columns.
BLOCKS = (BLOCK_TOD, BLOCK_BAR, BLOCK_CROSS)

_HH_BUCKETS = 13
_WEEKDAYS = ("mon", "tue", "wed", "thu", "fri")
_LUNCH_START_MINUTES = 12 * 60
_LUNCH_END_MINUTES = 13 * 60
#: Both blocks want the two session-open returns; they are emitted once.
_SHARED_OPEN_NAMES = ("ret_since_open", "ret_first30")
#: Vol-scaled past-return horizons, in minutes.
_Z_HORIZONS = (5, 15, 30, 60)
#: The long window the per-minute volatility is measured over.
_LONG_WINDOW = 390
#: The short window of the short/long volatility ratio.
_SHORT_WINDOW = 30
#: Trailing window for the market beta (ADR-0059 uses the same 3900).
_BETA_WINDOW = 3900
#: Same-time-of-day lookback, in trading days.
_SAMESLOT_DAYS = 5
#: Width of the same-time-of-day window, in minutes.
_SAMESLOT_WIDTH = 30
_EPS = 1e-12

TOD_NAMES = (
    ("tod_frac", "hh_bucket")
    + tuple(f"hh_{i:02d}" for i in range(_HH_BUCKETS))
    + ("is_open30", "is_lunch", "is_close30", "is_close5", "gap_x_open30")
    + tuple(f"dow_{tag}" for tag in _WEEKDAYS)
    + ("is_month_first2", "is_month_last3")
    + _SHARED_OPEN_NAMES
)

BAR_NAMES = (
    ("ret_sameslot_1d", "ret_sameslot_5d")
    + _SHARED_OPEN_NAMES
    + tuple(f"ret_{k}_z" for k in _Z_HORIZONS)
    + (f"rv_ratio_{_SHORT_WINDOW}_{_LONG_WINDOW}",)
)

CROSS_NAMES = (
    ("res_mkt_cum_30", "res_mkt_cum_60", "res_sec_cum_5", "res_sec_cum_30")
    + tuple(f"mkt_lag_{k}" for k in range(1, 6))
    + tuple(f"sec_lag_{k}" for k in range(3))
    + tuple(f"res_mkt_lag_{k}" for k in range(1, 6))
)

#: Fitted on the training fold, never on the whole sample.
TOD_FOLD_NAMES = ("tod_vol_now", "tod_mean_bucket")
BAR_FOLD_NAMES = ("vol_rel_5",)

_CAUSAL_NAMES = {
    BLOCK_TOD: TOD_NAMES,
    BLOCK_BAR: BAR_NAMES,
    BLOCK_CROSS: CROSS_NAMES,
}
_FOLD_NAMES = {
    BLOCK_TOD: TOD_FOLD_NAMES,
    BLOCK_BAR: BAR_FOLD_NAMES,
    BLOCK_CROSS: (),
}


def block_problems(blocks):
    """Problems with a declared block list, empty when none.

    Parameters
    ----------
    blocks : object
        The declared value; ``None`` and ``[]`` both mean no block.

    Returns
    -------
    list of str
        One problem per broken entry.
    """
    if blocks is None:
        return []
    if not isinstance(blocks, (list, tuple)):
        return [f"feature blocks must be a list, got {blocks!r}"]
    problems = []
    seen = set()
    for item in blocks:
        if item not in BLOCKS:
            problems.append(
                f"feature block must be one of {list(BLOCKS)}, got {item!r}"
            )
        elif item in seen:
            problems.append(f"feature block {item!r} is declared twice")
        seen.add(item)
    return problems


def normalise_blocks(blocks):
    """Return the selected blocks in declaration order.

    Parameters
    ----------
    blocks : sequence of str or None
        Any subset of :data:`BLOCKS`, in any order.

    Returns
    -------
    tuple of str
        The subset, ordered as :data:`BLOCKS` is.

    Examples
    --------
    Order is the module's, not the caller's::

        normalise_blocks(["cross", "tod"])  # ('tod', 'cross')
    """
    chosen = set(blocks or ())
    return tuple(name for name in BLOCKS if name in chosen)


def _dedup(names):
    """Keep first occurrence, drop later duplicates, preserve order."""
    out = []
    seen = set()
    for name in names:
        if name not in seen:
            out.append(name)
            seen.add(name)
    return tuple(out)


def block_causal_names(blocks):
    """Name every bar-computable column the blocks emit, in order.

    Parameters
    ----------
    blocks : sequence of str
        Any subset of :data:`BLOCKS`.

    Returns
    -------
    tuple of str
        Column names, deduplicated (``tod`` and ``bar`` share two).
    """
    chosen = normalise_blocks(blocks)
    names = []
    for block in chosen:
        names.extend(_CAUSAL_NAMES[block])
    return _dedup(names)


def block_fold_names(blocks):
    """Name every training-fold-fitted column the blocks emit, in order.

    Parameters
    ----------
    blocks : sequence of str
        Any subset of :data:`BLOCKS`.

    Returns
    -------
    tuple of str
        Column names, deduplicated.
    """
    chosen = normalise_blocks(blocks)
    names = []
    for block in chosen:
        names.extend(_FOLD_NAMES[block])
    return _dedup(names)


def block_feature_names(blocks):
    """Causal names then fold-fitted names, the emission order.

    The fold-fitted columns come last and stay contiguous so a per-fold
    node can overwrite them in place without touching anything else.

    Parameters
    ----------
    blocks : sequence of str
        Any subset of :data:`BLOCKS`.

    Returns
    -------
    tuple of str
        Every column the blocks add.
    """
    return block_causal_names(blocks) + block_fold_names(blocks)


def _session_bounds(sess_start, n):
    """Return ``(starts, ends)`` index arrays, one pair per session."""
    import numpy as np

    if not n:
        return np.zeros(0, dtype=np.int64), np.zeros(0, dtype=np.int64)
    changed = np.flatnonzero(np.diff(sess_start) != 0) + 1
    starts = np.concatenate(([0], changed)).astype(np.int64)
    ends = np.concatenate((starts[1:], [n])).astype(np.int64)
    return starts, ends


def _session_ordinal(sess_start, n):
    """Give each bar the 0-based index of the session it sits in."""
    import numpy as np

    out = np.zeros(n, dtype=np.int64)
    if n:
        out[1:] = np.cumsum(np.diff(sess_start) != 0)
    return out


def _open_returns(logp, opn, sess_start, mins, session, n):
    """Give the two session-open returns the ``tod`` and ``bar`` blocks share.

    ``ret_since_open`` is ``log(price / this session's open)``.
    ``ret_first30`` is ``log(price at the first bar at or after 10:00 /
    this session's open)``, held flat for the rest of the session and
    **zero before it is formed** — a NaN there would cost the first half
    hour of every session, and ``is_open30`` already flags those bars.
    The previous-close leg of the literature's first-half-hour return is
    the separate ``overnight_gap`` column, kept apart so the two are not
    collinear.
    """
    import numpy as np

    open_log = np.full(n, np.nan)
    if n:
        opens = opn[sess_start]
        ok = opens > 0.0
        open_log[ok] = np.log(opens[ok])
    since_open = logp - open_log
    first30 = np.zeros(n, dtype=np.float64)
    formed = (mins - float(session["rth_start_minutes"])) >= _SAMESLOT_WIDTH
    starts, ends = _session_bounds(sess_start, n)
    for start, end in zip(starts, ends):
        hit = np.flatnonzero(formed[start:end])
        if not hit.size:
            continue
        at = int(start) + int(hit[0])
        first30[at:end] = since_open[at]
    return {"ret_since_open": since_open, "ret_first30": first30}


def tod_columns(*, mins, parsed, overnight, session, n, shared):
    """Block A — time of day, from the bar stamp alone.

    Parameters
    ----------
    mins : numpy.ndarray
        Minute of the New York day for each bar.
    parsed : sequence of datetime.date
        The New York calendar date of each bar.
    overnight : numpy.ndarray
        The ``overnight_gap`` column, already built by the caller.
    session : dict
        The universe ``session`` object.
    n : int
        Bar count.
    shared : dict
        Output of :func:`_open_returns`.

    Returns
    -------
    dict
        ``{name: array}`` for every name in :data:`TOD_NAMES`.
    """
    import numpy as np

    rth_start = float(session["rth_start_minutes"])
    rth_end = float(session["rth_end_minutes"])
    span = max(rth_end - rth_start, 1.0)
    from_open = mins - rth_start
    columns = {"tod_frac": from_open / span}
    bucket = np.clip(
        np.floor(from_open / 30.0), 0.0, float(_HH_BUCKETS - 1),
    )
    columns["hh_bucket"] = bucket
    for i in range(_HH_BUCKETS):
        columns[f"hh_{i:02d}"] = (bucket == float(i)).astype(np.float64)
    open30 = (from_open >= 0.0) & (from_open < 30.0)
    columns["is_open30"] = open30.astype(np.float64)
    columns["is_lunch"] = (
        (mins >= _LUNCH_START_MINUTES) & (mins < _LUNCH_END_MINUTES)
    ).astype(np.float64)
    columns["is_close30"] = (mins >= rth_end - 30.0).astype(np.float64)
    columns["is_close5"] = (mins >= rth_end - 5.0).astype(np.float64)
    # The open reversal is an interaction, not a sum: ridge cannot form
    # it and a tree needs two splits for it (P2).
    columns["gap_x_open30"] = np.where(open30, overnight, 0.0)
    weekday = np.asarray([day.weekday() for day in parsed], dtype=np.float64)
    for i, tag in enumerate(_WEEKDAYS):
        columns[f"dow_{tag}"] = (weekday == float(i)).astype(np.float64)
    day_of_month = np.asarray([day.day for day in parsed], dtype=np.float64)
    lengths = {}
    last = np.empty(n, dtype=np.float64)
    for i, day in enumerate(parsed):
        key = (day.year, day.month)
        size = lengths.get(key)
        if size is None:
            size = lengths[key] = float(calendar.monthrange(*key)[1])
        last[i] = size
    columns["is_month_first2"] = (day_of_month <= 2.0).astype(np.float64)
    columns["is_month_last3"] = (
        day_of_month > last - 3.0
    ).astype(np.float64)
    columns.update(shared)
    return columns


def _rms(squares, valid, width):
    """Root-mean-square of the last ``width`` returns, causal."""
    import numpy as np

    total = rolling_sum(squares, width)
    count = rolling_sum(valid, width)
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.sqrt(total / np.maximum(count, 1.0))


def _sameslot_return(logp, key, sess_ord, mins, rth_start, days, n):
    """Give the 30-minute return ending at this slot ``days`` sessions back.

    Matched by (session ordinal, minute of day), not by a fixed bar
    offset: a missing minute or an early close would silently slide a
    fixed offset onto the wrong slot. The window start is clamped to the
    target session's open, so a 09:35 bar reads that session's 09:30 to
    09:35 instead of dropping out.
    """
    import numpy as np

    if not n:
        return np.zeros(0, dtype=np.float64)
    target = sess_ord - days
    end_key = target * 1440 + mins.astype(np.int64)
    start_key = target * 1440 + np.maximum(
        mins.astype(np.int64) - _SAMESLOT_WIDTH, np.int64(rth_start),
    )
    at_end = np.searchsorted(key, end_key)
    at_start = np.searchsorted(key, start_key)
    ok = target >= 0
    ok &= at_end < n
    ok &= at_start < n
    safe_end = np.minimum(at_end, n - 1)
    safe_start = np.minimum(at_start, n - 1)
    ok &= key[safe_end] == end_key
    ok &= (key[safe_start] // 1440) == target
    out = np.full(n, np.nan, dtype=np.float64)
    out[ok] = logp[safe_end[ok]] - logp[safe_start[ok]]
    return out


def bar_columns(*, logp, ret1, sess_start, mins, session, n, shared):
    """Block B — bar-derived inputs, in the P3a ranked order.

    The multi-scale returns and the volatility windows are
    cross-session: made session-local they would be NaN for the first
    390 minutes of every session, and a NaN drops the scored row. The
    universe's own 60-minute scale is cross-session for that reason.

    Parameters
    ----------
    logp : numpy.ndarray
        Log price, NaN where the bar has no usable price.
    ret1 : numpy.ndarray
        One-minute log returns, NaN across a tape gap.
    sess_start : numpy.ndarray
        Index of the first bar of each bar's session.
    mins : numpy.ndarray
        Minute of the New York day.
    session : dict
        The universe ``session`` object.
    n : int
        Bar count.
    shared : dict
        Output of :func:`_open_returns`.

    Returns
    -------
    dict
        ``{name: array}`` for every name in :data:`BAR_NAMES`.
    """
    import numpy as np

    rth_start = int(session["rth_start_minutes"])
    columns = {}
    sess_ord = _session_ordinal(sess_start, n)
    key = sess_ord * 1440 + mins.astype(np.int64)
    # The five-day mean is NaN unless all five are there: a warmup of
    # five sessions once, not a hole inside any session.
    total = np.zeros(n, dtype=np.float64)
    for day in range(1, _SAMESLOT_DAYS + 1):
        value = _sameslot_return(
            logp, key, sess_ord, mins, rth_start, day, n,
        )
        if day == 1:
            columns["ret_sameslot_1d"] = value
        total = total + value
    columns["ret_sameslot_5d"] = total / float(_SAMESLOT_DAYS)
    del total, key, sess_ord
    finite = np.isfinite(ret1)
    squares = np.where(finite, ret1 * ret1, 0.0)
    valid = finite.astype(np.float64)
    sigma = _rms(squares, valid, _LONG_WINDOW)
    short = _rms(squares, valid, _SHORT_WINDOW)
    del squares, valid, finite
    with np.errstate(divide="ignore", invalid="ignore"):
        columns[f"rv_ratio_{_SHORT_WINDOW}_{_LONG_WINDOW}"] = short / np.maximum(
            sigma, _EPS,
        )
    del short
    index = np.arange(n)
    for width in _Z_HORIZONS:
        past = np.full(n, np.nan, dtype=np.float64)
        reach = index >= width
        past[reach] = logp[reach] - logp[index[reach] - width]
        with np.errstate(divide="ignore", invalid="ignore"):
            columns[f"ret_{width}_z"] = past / np.maximum(
                sigma * math.sqrt(width), _EPS,
            )
    columns.update(shared)
    return columns


def _lag(values, k, sess_start, n):
    """Value ``k`` bars back, NaN across a session boundary."""
    import numpy as np

    out = np.full(n, np.nan, dtype=np.float64)
    index = np.arange(n)
    source = index - k
    ok = (source >= 0) & (source >= sess_start)
    out[ok] = values[source[ok]]
    return out


def _rolling_beta(own, market, width):
    """Trailing beta of ``own`` on ``market``, causal, NaN until full."""
    import numpy as np

    both = np.isfinite(own) & np.isfinite(market)
    x = np.where(both, market, 0.0)
    y = np.where(both, own, 0.0)
    weight = both.astype(np.float64)
    count = rolling_sum(weight, width)
    with np.errstate(divide="ignore", invalid="ignore"):
        mean_x = rolling_sum(x, width) / count
        mean_y = rolling_sum(y, width) / count
        cov = rolling_sum(x * y, width) / count - mean_x * mean_y
        var = rolling_sum(x * x, width) / count - mean_x * mean_x
        beta = cov / np.where(var > _EPS, var, np.nan)
    beta[count < 2.0] = np.nan
    return beta


def cross_columns(
    *, ret1, sess_start, market_ret, sector_ret, n,
    beta_window=_BETA_WINDOW,
):
    """Block C — market, sector and hedged-residual inputs (P3b).

    ``market_ret`` and ``sector_ret`` are the reference symbols'
    one-minute returns already aligned to this symbol's bar stamps, NaN
    at a minute the reference did not trade. Both are known at the bar
    they are stamped with, so lag 0 of the sector fund is as causal as
    the existing ``ref_ret_SPY``.

    The accumulated residuals are cross-session (a warmup once, not
    daily); the lag columns are session-local, exactly as ``ret_lag_*``
    already is, so they cost no row the baseline does not already lose.

    Parameters
    ----------
    ret1 : numpy.ndarray
        This symbol's one-minute log returns.
    sess_start : numpy.ndarray
        Index of the first bar of each bar's session.
    market_ret, sector_ret : numpy.ndarray
        Aligned one-minute log returns of the market and sector funds.
    n : int
        Bar count.
    beta_window : int
        Trailing bars behind the market beta.

    Returns
    -------
    dict
        ``{name: array}`` for every name in :data:`CROSS_NAMES`.
    """
    import numpy as np

    columns = {}
    beta = _rolling_beta(ret1, market_ret, beta_window)
    residual = ret1 - beta * market_ret
    own_ok = np.isfinite(ret1)
    own_sum = np.where(own_ok, ret1, 0.0)
    mkt_ok = np.isfinite(market_ret)
    mkt_sum = np.where(mkt_ok, market_ret, 0.0)
    sec_ok = np.isfinite(sector_ret)
    sec_sum = np.where(sec_ok, sector_ret, 0.0)
    for width in (30, 60):
        columns[f"res_mkt_cum_{width}"] = (
            rolling_sum(own_sum, width) - beta * rolling_sum(mkt_sum, width)
        )
    for width in (5, 30):
        columns[f"res_sec_cum_{width}"] = (
            rolling_sum(own_sum, width) - rolling_sum(sec_sum, width)
        )
    del own_sum, mkt_sum, sec_sum, own_ok, mkt_ok, sec_ok
    for k in range(1, 6):
        columns[f"mkt_lag_{k}"] = _lag(market_ret, k, sess_start, n)
        columns[f"res_mkt_lag_{k}"] = _lag(residual, k, sess_start, n)
    columns["sec_lag_0"] = np.array(sector_ret, dtype=np.float64, copy=True)
    for k in (1, 2):
        columns[f"sec_lag_{k}"] = _lag(sector_ret, k, sess_start, n)
    return columns


def block_columns(
    blocks, *, keep, logp, ret1, opn, sess_start, mins, parsed, overnight,
    session, market_ret=None, sector_ret=None, beta_window=_BETA_WINDOW,
):
    """Build every selected block's columns and keep only the grid rows.

    Each column is reduced by ``keep`` as soon as it exists, so the peak
    is the working arrays of one block, not a full-tape copy of all of
    them.

    Parameters
    ----------
    blocks : sequence of str
        Any subset of :data:`BLOCKS`.
    keep : numpy.ndarray
        Boolean grid mask over the full tape.
    logp, ret1, opn, sess_start, mins, overnight : numpy.ndarray
        Full-tape arrays for one symbol.
    parsed : sequence of datetime.date
        The New York calendar date of each bar.
    session : dict
        The universe ``session`` object.
    market_ret, sector_ret : numpy.ndarray or None
        Aligned reference returns; required by ``cross``.
    beta_window : int
        Trailing bars behind the market beta.

    Returns
    -------
    dict
        ``{name: kept-length array}``, plus a zero placeholder for every
        training-fold-fitted name (see :func:`fit_fold_stats`).

    Raises
    ------
    ValueError
        When ``cross`` is selected without both reference series.
    """
    import numpy as np

    chosen = normalise_blocks(blocks)
    out = {}
    if not chosen:
        return out
    n = int(logp.size)
    shared = None
    if BLOCK_TOD in chosen or BLOCK_BAR in chosen:
        shared = _open_returns(logp, opn, sess_start, mins, session, n)
    if BLOCK_TOD in chosen:
        built = tod_columns(
            mins=mins, parsed=parsed, overnight=overnight,
            session=session, n=n, shared=shared,
        )
        for name in TOD_NAMES:
            out[name] = built.pop(name)[keep]
        del built
    if BLOCK_BAR in chosen:
        built = bar_columns(
            logp=logp, ret1=ret1, sess_start=sess_start, mins=mins,
            session=session, n=n, shared=shared,
        )
        for name in BAR_NAMES:
            if name in out:
                built.pop(name, None)
                continue
            out[name] = built.pop(name)[keep]
        del built
    del shared
    if BLOCK_CROSS in chosen:
        if market_ret is None or sector_ret is None:
            raise ValueError(
                "feature block 'cross' needs both a market series and a "
                "sector series; the universe must declare 'market' and a "
                "'sector_etf' entry for this symbol, and the store must "
                "carry both (ADR-0071)."
            )
        built = cross_columns(
            ret1=ret1, sess_start=sess_start, market_ret=market_ret,
            sector_ret=sector_ret, n=n, beta_window=beta_window,
        )
        for name in CROSS_NAMES:
            out[name] = built.pop(name)[keep]
        del built
    kept = int(np.count_nonzero(keep))
    for name in block_fold_names(chosen):
        # A placeholder, not a value. The per-fold node overwrites it in
        # place with a statistic fitted on that fold's TRAINING rows.
        out[name] = np.zeros(kept, dtype=np.float64)
    return out


def _slot_curve(keys, values, size, smooth):
    """Per-slot mean and variance over ``values``, smoothed over slots."""
    import numpy as np

    count = np.bincount(keys, minlength=size).astype(np.float64)
    total = np.bincount(keys, weights=values, minlength=size)
    squares = np.bincount(keys, weights=values * values, minlength=size)
    if smooth > 1:
        window = np.ones(int(smooth), dtype=np.float64)
        count = np.convolve(count, window, mode="same")
        total = np.convolve(total, window, mode="same")
        squares = np.convolve(squares, window, mode="same")
    with np.errstate(divide="ignore", invalid="ignore"):
        mean = np.where(count > 0.0, total / np.maximum(count, 1.0), np.nan)
        second = np.where(
            count > 0.0, squares / np.maximum(count, 1.0), np.nan,
        )
    return mean, np.maximum(second - mean * mean, 0.0), count


def fit_fold_stats(
    blocks, *, minutes, bucket, ret, volume, train, smooth=5,
):
    """Fit the training-fold-only statistics. Training rows only.

    ``train`` is the boolean mask of rows inside this fold's training
    window. Every row outside it is invisible here: that is the whole
    point of the function, and the leak test asserts it by scrambling
    the validation rows and demanding the fit not move.

    Parameters
    ----------
    blocks : sequence of str
        Any subset of :data:`BLOCKS`.
    minutes : numpy.ndarray
        ``minutes_from_open`` for every row.
    bucket : numpy.ndarray
        ``hh_bucket`` for every row.
    ret : numpy.ndarray
        The row's own one-minute return (``ret_lag_0``).
    volume : numpy.ndarray or None
        The row's short-window volume sum; ``None`` when ``bar`` is off.
    train : numpy.ndarray
        Boolean training mask.
    smooth : int
        Slots the volatility curve is smoothed over.

    Returns
    -------
    dict
        The fitted curves, consumed by :func:`apply_fold_stats`.
    """
    import numpy as np

    chosen = normalise_blocks(blocks)
    fitted = {"slots": 0}
    if not chosen:
        return fitted
    slot = np.clip(np.nan_to_num(minutes, nan=-1.0), 0.0, None).astype(np.int64)
    size = int(slot.max()) + 1 if slot.size else 1
    fitted["slots"] = size
    rows = train & np.isfinite(ret)
    keys = slot[rows]
    values = np.asarray(ret[rows], dtype=np.float64)
    if BLOCK_TOD in chosen:
        _, variance, count = _slot_curve(keys, values, size, smooth)
        pooled = float(values.std()) if values.size else 0.0
        curve = np.sqrt(variance)
        curve[~np.isfinite(curve) | (count <= 0.0)] = pooled
        fitted["tod_vol"] = curve
        buckets = np.clip(
            np.nan_to_num(bucket, nan=0.0), 0.0, float(_HH_BUCKETS - 1),
        ).astype(np.int64)
        b_keys = buckets[rows]
        b_count = np.bincount(b_keys, minlength=_HH_BUCKETS).astype(np.float64)
        b_total = np.bincount(b_keys, weights=values, minlength=_HH_BUCKETS)
        b_pooled = float(values.mean()) if values.size else 0.0
        with np.errstate(divide="ignore", invalid="ignore"):
            means = b_total / np.maximum(b_count, 1.0)
        means[b_count <= 0.0] = b_pooled
        fitted["tod_mean"] = means
    if BLOCK_BAR in chosen:
        if volume is None:
            raise ValueError(
                "feature block 'bar' fits a volume norm and needs the "
                "short-window volume column; declare volume_column "
                "(ADR-0071)."
            )
        v_rows = train & np.isfinite(volume)
        v_keys = slot[v_rows]
        v_values = np.asarray(volume[v_rows], dtype=np.float64)
        mean, _, count = _slot_curve(v_keys, v_values, size, smooth)
        pooled = float(v_values.mean()) if v_values.size else 0.0
        mean = np.where(
            np.isfinite(mean) & (count > 0.0) & (mean > 0.0), mean, pooled,
        )
        fitted["vol_slot"] = np.maximum(mean, _EPS)
    return fitted


def apply_fold_stats(fitted, blocks, *, minutes, bucket, volume):
    """Read the fitted curves onto every row, training and validation.

    Parameters
    ----------
    fitted : dict
        Output of :func:`fit_fold_stats`.
    blocks : sequence of str
        Any subset of :data:`BLOCKS`.
    minutes, bucket : numpy.ndarray
        Row keys, as given to :func:`fit_fold_stats`.
    volume : numpy.ndarray or None
        The row's short-window volume sum.

    Returns
    -------
    dict
        ``{name: array}`` for every name in
        :func:`block_fold_names`.
    """
    import numpy as np

    chosen = normalise_blocks(blocks)
    out = {}
    if not chosen:
        return out
    size = max(int(fitted.get("slots", 0)), 1)
    slot = np.clip(
        np.nan_to_num(minutes, nan=0.0), 0.0, float(size - 1),
    ).astype(np.int64)
    if BLOCK_TOD in chosen:
        out["tod_vol_now"] = fitted["tod_vol"][slot]
        buckets = np.clip(
            np.nan_to_num(bucket, nan=0.0), 0.0, float(_HH_BUCKETS - 1),
        ).astype(np.int64)
        out["tod_mean_bucket"] = fitted["tod_mean"][buckets]
    if BLOCK_BAR in chosen:
        norm = fitted["vol_slot"][slot]
        with np.errstate(divide="ignore", invalid="ignore"):
            out["vol_rel_5"] = np.log(
                np.maximum(np.asarray(volume, dtype=np.float64), _EPS) / norm
            )
    return out
