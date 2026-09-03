"""Ordering versus size: whether the RANK is usable, the MAGNITUDE, or neither.

ADR-0067 scores a forecast's SIZE — the squared-error gap against a
constant. A model can lose that contest and still order the names
correctly, and an optimizer that only SELECTS cares about the order. So
"how far ahead can we predict" is allowed to be two numbers, and this
module measures both halves separately (ADR-0068).

**The calibration slope** regresses the outcome on the forecast
(Mincer–Zarnowitz): ``y = a + b*ŷ + e``. ``b ≈ 1`` says the predicted
magnitude is usable as it stands. ``0 < b ≪ 1`` says there is real
information at the wrong scale — the forecast over-reacts, and one
number per horizon repairs it. ``b ≈ 0`` with a positive ordering score
says only the RANK survives, and no rescaling recovers a magnitude that
is not there.

**The per-timestamp cross-sectional score** ranks the names AT ONE
INSTANT by forecast and by outcome, correlates the two orderings, and
tests the mean of that time series with an HAC standard error. It is
NOT the pooled ``(name, time)`` correlation the scan already reports:
pooling mixes "which name" with "when", so a model that only says
"everything rises now" scores well pooled and gives a selector nothing.
Both are computed here and they are named apart —
:func:`pooled_name_time_ic` versus :func:`per_timestamp_ic` — because
the whole point is that they answer different questions and one of them
has been mistaken for the other.

**The honesty guard.** With three names a per-instant Spearman takes
exactly four values (``-1, -0.5, +0.5, +1``); it can never be zero, no
single instant can be significant (the best one-sided p is 1/6), and
cross-sectional demeaning leaves two degrees of freedom. That is a
rescaled three-way hit rate wearing a correlation's clothes. So
:data:`USABLE_NAMES` is 5, every result carries the NAMES PRESENT at
each instant, and a cross-section thinner than that comes back with
``usable`` false and a reason attached — the verdict helpers refuse it
outright rather than let a three-name number pass as evidence.

Import cost: stdlib only.
"""

from __future__ import annotations

import math

from dskit.pipeline.records import number_ok
from dskit.pipeline.stats import across_fold_t, dm_lags, newey_west_mean

__all__ = [
    "MIN_CROSS_SECTION_NAMES",
    "USABLE_NAMES",
    "calibration_across_folds",
    "calibration_slope",
    "cross_section_by_stamp",
    "demean_by_series",
    "ic_from_rho",
    "ordering_verdict",
    "pearson",
    "per_timestamp_ic",
    "pooled_name_time_ic",
    "spearman",
]

#: Fewest names at one instant for a rank correlation to EXIST. Two
#: names give ±1 and nothing else; three is the first count with any
#: interior value at all, and it is still far too few to report (see
#: :data:`USABLE_NAMES`).
MIN_CROSS_SECTION_NAMES = 3

#: Fewest names at one instant for the per-timestamp ordering score to
#: be REPORTABLE. At 3 names the statistic takes 4 values and its null
#: standard deviation is 1/sqrt(2) = 0.707; at 5 it takes a 0.1-spaced
#: grid over 120 orderings, the null sd falls to 0.5, and the stamps
#: needed to detect a true IC of 0.02 fall by half. Below this the
#: measure is emitted but marked unusable.
USABLE_NAMES = 5

#: One-sided normal critical value at 5% — the level every test here
#: reports against, named once so the docs and the code cannot drift.
_Z_05 = 1.645


def _as_floats(values, label):
    """Coerce a sequence to a list of finite floats, or raise."""
    if not isinstance(values, (list, tuple)):
        raise ValueError(
            f"{label} must be a list or tuple of numbers, "
            f"got {type(values).__name__}"
        )
    out = []
    for i, v in enumerate(values):
        if not number_ok(v):
            raise ValueError(f"{label}[{i}] must be a finite number, got {v!r}")
        out.append(float(v))
    return out


def _ranks(values):
    """Rank ``values``, ties sharing their mean rank."""
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        shared = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[order[k]] = shared
        i = j + 1
    return ranks


def pearson(x, y):
    """Pearson correlation of two equal-length sequences.

    Parameters
    ----------
    x : list or tuple of float
        First sequence, at least two finite numbers.
    y : list or tuple of float
        Second sequence, the same length as ``x``.

    Returns
    -------
    float or None
        The correlation, or ``None`` when either side is constant (a
        correlation is undefined there, and returning 0.0 would read as
        a measured absence of relationship).

    Raises
    ------
    ValueError
        On lengths that disagree, fewer than two pairs, or a non-finite
        cell.

    Examples
    --------
    A perfect straight line::

        pearson([1.0, 2.0, 3.0], [2.0, 4.0, 6.0])  # 1.0
    """
    xs = _as_floats(x, "x")
    ys = _as_floats(y, "y")
    if len(xs) != len(ys):
        raise ValueError(f"x has {len(xs)} values, y has {len(ys)} — must agree")
    n = len(xs)
    if n < 2:
        raise ValueError(f"pearson needs at least 2 pairs, got {n}")
    xbar = sum(xs) / n
    ybar = sum(ys) / n
    sxy = sum((a - xbar) * (b - ybar) for a, b in zip(xs, ys))
    sxx = sum((a - xbar) ** 2 for a in xs)
    syy = sum((b - ybar) ** 2 for b in ys)
    if sxx <= 0.0 or syy <= 0.0:
        return None
    return sxy / math.sqrt(sxx * syy)


def spearman(x, y):
    """Spearman rank correlation of two equal-length sequences.

    Ties take their average rank, so a forecast that cannot separate two
    names does not get credit for an order it never expressed.

    Parameters
    ----------
    x : list or tuple of float
        First sequence, at least two finite numbers.
    y : list or tuple of float
        Second sequence, the same length as ``x``.

    Returns
    -------
    float or None
        The rank correlation, or ``None`` when either side is constant.

    Raises
    ------
    ValueError
        On lengths that disagree, fewer than two pairs, or a non-finite
        cell.

    Examples
    --------
    Five names, one adjacent pair swapped::

        spearman([1.0, 2, 3, 4, 5], [1.0, 2, 3, 5, 4])  # 0.9
    """
    xs = _as_floats(x, "x")
    ys = _as_floats(y, "y")
    if len(xs) != len(ys):
        raise ValueError(f"x has {len(xs)} values, y has {len(ys)} — must agree")
    if len(xs) < 2:
        raise ValueError(f"spearman needs at least 2 pairs, got {len(xs)}")
    return pearson(_ranks(xs), _ranks(ys))


def calibration_slope(y, yhat, lags=None, h_steps=1):
    """Mincer–Zarnowitz slope of the outcome on the forecast, with a band.

    Fits ``y = a + b*ŷ + e`` by least squares and gives ``b`` an HAC
    standard error, so the band survives the overlap an h-step label
    creates. The slope is read against TWO nulls and both are returned:
    ``b = 0`` (the forecast carries no linear information) and ``b = 1``
    (the forecast's SIZE is already right). A slope in ``(0, 1)`` with a
    ``t`` above 2 against zero is information at the wrong scale.

    The standard error is the usual OLS sandwich written through
    :func:`~dskit.pipeline.stats.newey_west_mean`: the score series is
    ``u_t = (ŷ_t - ŷ̄) e_t``, whose sample mean is exactly zero by the
    least-squares normal equations, and ``se(b) = n · se(u) / Sxx``.
    Nothing is re-derived here.

    Parameters
    ----------
    y : list or tuple of float
        Realized outcomes in TIME order, at least three.
    yhat : list or tuple of float
        Forecasts, same length as ``y``, not constant.
    lags : int or None
        Bartlett truncation in observation steps. ``None`` takes
        :func:`~dskit.pipeline.stats.dm_lags`, the same rule the skill
        test uses, so the two bands are built the same way.
    h_steps : int
        Forecast horizon in observation steps (the lead divided by the
        row spacing), ``h_steps >= 1``.

    Returns
    -------
    dict
        ``n``, ``slope``, ``intercept``, ``slope_se``, ``t_vs_0``,
        ``t_vs_1`` (both ``None`` when the residuals have no variance),
        ``pearson_r``, ``lags`` and ``h_steps``.

    Raises
    ------
    ValueError
        On lengths that disagree, fewer than three pairs, a non-finite
        cell, a bad ``h_steps``, or a constant forecast — a slope
        against a constant is undefined, not zero.

    Examples
    --------
    A forecast that moves twice as far as the outcome::

        out = calibration_slope([1.0, 3, 2, 4], [1.0, 2, 3, 4])
        round(out["slope"], 3)  # 0.8
    """
    ys = _as_floats(y, "y")
    fs = _as_floats(yhat, "yhat")
    if len(ys) != len(fs):
        raise ValueError(
            f"y has {len(ys)} values, yhat has {len(fs)} — must agree"
        )
    n = len(ys)
    if n < 3:
        raise ValueError(f"calibration_slope needs at least 3 pairs, got {n}")
    if isinstance(h_steps, bool) or not isinstance(h_steps, int) or h_steps < 1:
        raise ValueError(f"h_steps must be an int >= 1, got {h_steps!r}")
    fbar = sum(fs) / n
    ybar = sum(ys) / n
    sxx = sum((f - fbar) ** 2 for f in fs)
    if sxx <= 0.0:
        raise ValueError(
            "yhat is constant — a Mincer–Zarnowitz slope against a constant "
            "forecast is undefined (the scan's degenerate-forecast guard "
            "should have refused this fit first)"
        )
    sxy = sum((f - fbar) * (v - ybar) for f, v in zip(fs, ys))
    slope = sxy / sxx
    intercept = ybar - slope * fbar
    scores = [(f - fbar) * (v - intercept - slope * f) for f, v in zip(fs, ys)]
    band = dm_lags(n, h_steps) if lags is None else lags
    hac = newey_west_mean(scores, lags=band)
    se = hac["se"] * n / sxx
    return {
        "n": n,
        "slope": slope,
        "intercept": intercept,
        "slope_se": se,
        "t_vs_0": None if se <= 0.0 else slope / se,
        "t_vs_1": None if se <= 0.0 else (slope - 1.0) / se,
        "pearson_r": pearson(fs, ys),
        "lags": hac["lags"],
        "h_steps": h_steps,
    }


def calibration_across_folds(slopes):
    """Pool per-fold calibration slopes into one number with a band.

    The folds are the independence units (ADR-0067's fold-cluster form),
    so the honest summary of twenty slopes is their mean with a Student
    ``t``, not a slope refitted on the concatenation. Two nulls again:
    the mean slope against 0 and against 1.

    Parameters
    ----------
    slopes : list or tuple of float
        One slope per fold, at least two, in fold order.

    Returns
    -------
    dict
        ``n_folds``, ``slope_mean``, ``slope_se``, ``t_vs_0``, ``p_vs_0``
        (one-sided, ``b > 0``), ``t_vs_1`` (two-sided reading is the
        caller's), ``df``, and ``frac_positive``.

    Raises
    ------
    ValueError
        On fewer than two folds or a non-finite slope.

    Examples
    --------
    Four folds averaging 0.5::

        out = calibration_across_folds([0.2, 0.4, 0.6, 0.8])
        out["slope_mean"]  # 0.5
    """
    values = _as_floats(slopes, "slopes")
    if len(values) < 2:
        raise ValueError(
            f"calibration_across_folds needs at least 2 folds, got {len(values)}"
        )
    zero = across_fold_t(values)
    one = across_fold_t([v - 1.0 for v in values])
    return {
        "n_folds": zero["n"],
        "slope_mean": zero["mean"],
        "slope_se": zero["se"],
        "t_vs_0": zero["t"],
        "p_vs_0": zero["p_value"],
        "t_vs_1": one["t"],
        "df": zero["df"],
        "frac_positive": sum(1 for v in values if v > 0.0) / len(values),
    }


def pooled_name_time_ic(y, yhat):
    """Report the POOLED ``(name, time)`` correlation — labelled, not endorsed.

    One Spearman over every row of every name concatenated together.
    This is the number the scan already reports as ``val_ic``, and it is
    kept here ONLY so the two ordering measures can be printed side by
    side under names that cannot be confused. It mixes the time-series
    question ("is now a good moment") with the cross-sectional one
    ("which name is best now"), so a model that moves all names together
    scores well here and is worth nothing to a selector. Use
    :func:`per_timestamp_ic` for the selector's question.

    Parameters
    ----------
    y : list or tuple of float
        Realized outcomes over all names and stamps, in any fixed order.
    yhat : list or tuple of float
        Forecasts, aligned with ``y``.

    Returns
    -------
    dict
        ``ic`` (``None`` when either side is constant), ``n_rows``, and
        ``kind``, always the string ``"pooled_name_time"``.

    Raises
    ------
    ValueError
        On lengths that disagree, fewer than two rows, or a non-finite
        cell.

    Examples
    --------
    Six rows pooled across two names::

        pooled_name_time_ic([1.0, 2, 3, 4, 5, 6], [1.0, 2, 3, 4, 6, 5])["ic"]
        # -> 0.9428571428571428
    """
    return {
        "ic": spearman(yhat, y),
        "n_rows": len(y),
        "kind": "pooled_name_time",
    }


def _stamp_groups(stamps, series, y, yhat):
    """Group aligned rows into ``{stamp: (names, y, yhat)}``, stamp-sorted."""
    if not (len(stamps) == len(series) == len(y) == len(yhat)):
        raise ValueError(
            f"stamps/series/y/yhat lengths disagree: {len(stamps)}, "
            f"{len(series)}, {len(y)}, {len(yhat)}"
        )
    grouped = {}
    for i, stamp in enumerate(stamps):
        if not number_ok(y[i]) or not number_ok(yhat[i]):
            continue
        cell = grouped.setdefault(stamp, ([], [], []))
        cell[0].append(str(series[i]))
        cell[1].append(float(y[i]))
        cell[2].append(float(yhat[i]))
    return grouped


def demean_by_series(series, values):
    """Subtract each series' own window mean from its values.

    The guard twin the cross-sectional score needs: a forecast that
    tracks a slow per-name characteristic (a volatility level, an
    industry) orders the names correctly every instant WITHOUT timing
    anything, and that tilt vanishes under this subtraction. What
    survives is timing.

    Parameters
    ----------
    series : list or tuple of str
        The series key of each row.
    values : list or tuple of float
        The values, aligned with ``series``.

    Returns
    -------
    list of float
        The values with each series' mean removed, in input order.

    Raises
    ------
    ValueError
        On lengths that disagree or a non-finite cell.

    Examples
    --------
    Two names, each centred on its own mean::

        demean_by_series(["A", "A", "B"], [1.0, 3.0, 5.0])
        # -> [-1.0, 1.0, 0.0]
    """
    vals = _as_floats(values, "values")
    if len(series) != len(vals):
        raise ValueError(
            f"series has {len(series)} keys, values has {len(vals)} — must agree"
        )
    totals, counts = {}, {}
    for key, v in zip(series, vals):
        totals[key] = totals.get(key, 0.0) + v
        counts[key] = counts.get(key, 0) + 1
    return [v - totals[k] / counts[k] for k, v in zip(series, vals)]


def cross_section_by_stamp(stamps, series, y, yhat, min_names=None):
    """One rank correlation per instant, plus the names present at each.

    Parameters
    ----------
    stamps : list or tuple
        The instant of each row (any sortable, comparable key).
    series : list or tuple of str
        The series key of each row.
    y : list or tuple of float
        Realized outcomes, aligned with ``stamps``.
    yhat : list or tuple of float
        Forecasts, aligned with ``stamps``.
    min_names : int or None
        Fewest names for an instant to yield a correlation at all.
        ``None`` takes :data:`MIN_CROSS_SECTION_NAMES`. Instants below
        it are skipped, but they are still COUNTED in ``n_names``.

    Returns
    -------
    dict
        ``stamps`` (sorted, only the scored ones), ``rho`` (aligned with
        them), ``n_names`` (aligned with them), ``n_names_all`` (one per
        instant seen, scored or not, sorted the same way), and
        ``n_skipped``.

    Raises
    ------
    ValueError
        On lengths that disagree or a bad ``min_names``.

    Examples
    --------
    One instant with three names ordered correctly::

        out = cross_section_by_stamp(
            [1, 1, 1], ["A", "B", "C"], [3.0, 2.0, 1.0], [3.0, 2.0, 1.0]
        )
        out["rho"]  # [1.0]
    """
    floor = MIN_CROSS_SECTION_NAMES if min_names is None else min_names
    if isinstance(floor, bool) or not isinstance(floor, int) or floor < 2:
        raise ValueError(f"min_names must be an int >= 2, got {min_names!r}")
    grouped = _stamp_groups(stamps, series, y, yhat)
    kept_stamps, rhos, kept_names, all_names = [], [], [], []
    skipped = 0
    for stamp in sorted(grouped):
        names, ys, fs = grouped[stamp]
        all_names.append(len(names))
        if len(names) < floor:
            skipped += 1
            continue
        rho = spearman(fs, ys)
        if rho is None:
            skipped += 1
            continue
        kept_stamps.append(stamp)
        rhos.append(rho)
        kept_names.append(len(names))
    return {
        "stamps": kept_stamps,
        "rho": rhos,
        "n_names": kept_names,
        "n_names_all": all_names,
        "n_skipped": skipped,
    }


def _names_summary(counts):
    """Summarise per-instant name counts as min / median / max."""
    if not counts:
        return {"min": 0, "median": 0, "max": 0}
    ordered = sorted(counts)
    mid = len(ordered) // 2
    median = (
        ordered[mid]
        if len(ordered) % 2
        else (ordered[mid - 1] + ordered[mid]) / 2.0
    )
    return {"min": ordered[0], "median": median, "max": ordered[-1]}


def _usability(names, usable_names):
    """Decide whether a cross-section is wide enough to report."""
    if not names["max"]:
        return False, "no instant carried enough names to rank at all"
    if names["median"] < usable_names:
        return False, (
            f"the median instant carried {names['median']} name(s), below the "
            f"{usable_names} this measure needs: with {names['max']} names a "
            "per-instant Spearman takes only a handful of distinct values, can "
            "never be zero, and no single instant can reach significance — the "
            "number below is a rescaled hit rate, NOT evidence of ordering "
            "skill"
        )
    return True, ""


def per_timestamp_ic(
    stamps, series, y, yhat, lags=None, h_steps=1, usable_names=None,
    min_names=None,
):
    """Score the selector's ordering: rank WITHIN each instant, then test.

    At each instant the names are ranked by forecast and by outcome and
    the two orderings are correlated; the resulting time series is then
    tested with :func:`~dskit.pipeline.stats.newey_west_mean`, whose HAC
    band absorbs the autocorrelation an overlapping h-step label leaves
    behind. This is the number a downstream optimizer's "which name now"
    decision actually depends on, and it is NOT
    :func:`pooled_name_time_ic`.

    **The result may be unusable and says so.** ``usable`` is false
    whenever the median instant carried fewer than ``usable_names``
    names, with ``unusable_reason`` spelling out why the number below it
    is not evidence. The counts are always emitted so a reader can check
    the claim rather than take it.

    Parameters
    ----------
    stamps : list or tuple
        The instant of each row.
    series : list or tuple of str
        The series key of each row.
    y : list or tuple of float
        Realized outcomes, aligned with ``stamps``.
    yhat : list or tuple of float
        Forecasts, aligned with ``stamps``.
    lags : int or None
        Bartlett truncation over the STAMP series. ``None`` takes
        :func:`~dskit.pipeline.stats.dm_lags`.
    h_steps : int
        Forecast horizon in observation steps, ``h_steps >= 1``.
    usable_names : int or None
        Reporting floor. ``None`` takes :data:`USABLE_NAMES`.
    min_names : int or None
        Ranking floor. ``None`` takes :data:`MIN_CROSS_SECTION_NAMES`.

    Returns
    -------
    dict
        ``kind`` (always ``"per_timestamp_cross_section"``), ``ic``,
        ``ic_se``, ``ic_t``, ``ic_p``, ``n_stamps``, ``frac_pos``,
        ``n_names`` (the min/median/max summary), ``n_skipped``,
        ``lags``, ``h_steps``, ``usable`` and ``unusable_reason``.
        ``n_stamps`` under 2 leaves every statistic ``None``.

    Raises
    ------
    ValueError
        On lengths that disagree, a bad ``h_steps``, or a bad floor.

    Examples
    --------
    Two instants over five names, one of them mis-ordered::

        out = per_timestamp_ic(stamps, names, y, yhat)
        out["usable"]  # True
    """
    cs = cross_section_by_stamp(stamps, series, y, yhat, min_names=min_names)
    return ic_from_rho(
        cs["rho"],
        cs["n_names_all"],
        lags=lags,
        h_steps=h_steps,
        usable_names=usable_names,
        n_skipped=cs["n_skipped"],
    )


def ic_from_rho(
    rho, n_names_all, lags=None, h_steps=1, usable_names=None, n_skipped=0
):
    """Test an already-built per-instant rank-correlation series.

    The half of :func:`per_timestamp_ic` that does the statistics, split
    out so a caller pooling many folds can accumulate the small ``rho``
    series fold by fold and never hold every fold's rows at once.

    Parameters
    ----------
    rho : list or tuple of float
        One rank correlation per scored instant, in TIME order.
    n_names_all : list or tuple of int
        Names present at each instant SEEN — including instants too thin
        to score, because those are exactly what the usability guard is
        looking for.
    lags : int or None
        Bartlett truncation. ``None`` takes
        :func:`~dskit.pipeline.stats.dm_lags`.
    h_steps : int
        Forecast horizon in observation steps, ``h_steps >= 1``.
    usable_names : int or None
        Reporting floor. ``None`` takes :data:`USABLE_NAMES`.
    n_skipped : int
        Instants seen but not scored.

    Returns
    -------
    dict
        The :func:`per_timestamp_ic` shape.

    Raises
    ------
    ValueError
        On a bad ``h_steps`` or ``usable_names``, or a non-finite rho.

    Examples
    --------
    Three instants over five names::

        ic_from_rho([0.9, 0.5, 0.7], [5, 5, 5])["usable"]  # True
    """
    if isinstance(h_steps, bool) or not isinstance(h_steps, int) or h_steps < 1:
        raise ValueError(f"h_steps must be an int >= 1, got {h_steps!r}")
    floor = USABLE_NAMES if usable_names is None else usable_names
    if isinstance(floor, bool) or not isinstance(floor, int) or floor < 2:
        raise ValueError(f"usable_names must be an int >= 2, got {usable_names!r}")
    rhos = _as_floats(list(rho), "rho")
    names = _names_summary(list(n_names_all))
    usable, reason = _usability(names, floor)
    out = {
        "kind": "per_timestamp_cross_section",
        "ic": None,
        "ic_se": None,
        "ic_t": None,
        "ic_p": None,
        "n_stamps": len(rhos),
        "frac_pos": None,
        "n_names": names,
        "n_skipped": n_skipped,
        "lags": None,
        "h_steps": h_steps,
        "usable": usable,
        "unusable_reason": reason,
    }
    if len(rhos) < 2:
        return out
    band = dm_lags(len(rhos), h_steps) if lags is None else lags
    hac = newey_west_mean(rhos, lags=band)
    out.update(
        ic=hac["mean"],
        ic_se=hac["se"],
        ic_t=hac["t"],
        ic_p=hac["p_value"],
        frac_pos=sum(1 for r in rhos if r > 0.0) / len(rhos),
        lags=hac["lags"],
    )
    return out


def ordering_verdict(pooled, demeaned, fold_positive=0, n_folds=0, z=_Z_05):
    """Apply P6's pre-registered ordering rule — and refuse a thin panel.

    A horizon passes on ORDER when the pooled per-timestamp score clears
    its one-sided band, most folds agree on the sign, and the score
    SURVIVES cross-sectional demeaning — the last condition is what
    separates timing from a standing per-name tilt, which is not
    tradeable and is exactly what a narrow panel produces. A result
    whose panel was too thin never passes, whatever its numbers say.

    Parameters
    ----------
    pooled : mapping
        A :func:`per_timestamp_ic` result on the raw pairs.
    demeaned : mapping
        The same on series-demeaned pairs (:func:`demean_by_series`).
    fold_positive : int
        Folds whose own mean rho was positive.
    n_folds : int
        Folds in the walk. ``0`` skips the fold-agreement condition.
    z : float
        One-sided critical value the pooled ``t`` must clear.

    Returns
    -------
    dict
        ``passes``, ``usable``, ``reasons`` (every condition that
        failed, in order), ``ic``, ``ic_t``, ``ic_demeaned`` and
        ``retained`` — the demeaned score as a share of the raw one,
        ``None`` when the raw score is not positive.

    Raises
    ------
    ValueError
        When either argument is not a :func:`per_timestamp_ic` result.

    Examples
    --------
    A thin panel refuses before any number is read::

        ordering_verdict(thin, thin_dm)["passes"]  # False
    """
    for name, payload in (("pooled", pooled), ("demeaned", demeaned)):
        if not isinstance(payload, dict) or "usable" not in payload:
            raise ValueError(f"{name} must be a per_timestamp_ic result")
    ic, t = pooled["ic"], pooled["ic_t"]
    ic_dm = demeaned["ic"]
    retained = None if not ic or ic <= 0.0 else (ic_dm or 0.0) / ic
    reasons = []
    if not pooled["usable"]:
        reasons.append(pooled["unusable_reason"])
    if t is None or t < z:
        reasons.append(f"pooled per-timestamp t {t} is below {z}")
    if n_folds and fold_positive * 2 <= n_folds:
        reasons.append(
            f"only {fold_positive}/{n_folds} folds had a positive mean rho"
        )
    if retained is None or retained < 0.5:
        reasons.append(
            "demeaning removed more than half the score — the ordering is a "
            "standing per-name tilt, not timing"
        )
    return {
        "passes": not reasons,
        "usable": pooled["usable"],
        "reasons": reasons,
        "ic": ic,
        "ic_t": t,
        "ic_demeaned": ic_dm,
        "retained": retained,
    }
