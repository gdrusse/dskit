"""The toolkit's stat-test machinery: cluster bootstraps + multiplicity
+ a no-information (forecast vs mean) walk.

Doctrine, venue-neutralized: **per-instrument hypotheses stay
per-instrument** — the toolkit offers NO way to pool instruments into one
aggregate test, and the corrections here borrow strength across the family
while preserving each instrument's own reject/accept (which cell has the
edge IS the deliverable). Resampling is by CLUSTER (the record's
dependence group — an event, a trading day), never by record:
records within a cluster are correlated, and treating them as independent
inflates every test in any venue.

Two statistics ship, selected by the ``stat_test`` kind's ``method`` param:
``plain`` (the original percentile-style cluster bootstrap) and
``studentized`` (a recentered cluster bootstrap-t whose pivot is
approximately ancillary, making it gate-grade for verdicts that authorize
action). The family is a CLOSED tuple, not a registry — the statistic is
the ruler, and a ruler a config can swap is optimizing the ruler.

A second estimand lives beside that owned kind, not inside it (ADR-0057):
whether a **forecast series** still beats the unconditional mean under
quadratic loss (Breitung–Knüppel no-information; Clark–West nested MSPE
adjustment; Newey–West overlap). That is one time-ordered pair series,
not a per-instrument bootstrap, so it is not a third ``METHODS`` entry.

Corrections ARE a registry (:func:`register_correction`): a correction is
multiplicity *policy*, not the statistic, and a project may legitimately
bring its own. Each entry carries ``needs_weights`` metadata so the node
layer can refuse a weighted correction with nothing wired, at plan time.

Everything is deterministic: the bootstrap RNG is seeded from
``(seed, instrument)`` via sha256, so per-instrument p-values do not
depend on iteration order, process hash randomization, or each other —
and both methods consume the identical draw stream, so switching method
never perturbs the resampling itself.

Import cost: stdlib only.
"""

from __future__ import annotations

import hashlib
import math
import random

from dskit.pipeline.records import number_ok

__all__ = [
    "CORRECTIONS",
    "METHODS",
    "across_fold_t",
    "benjamini_hochberg",
    "bonferroni",
    "clark_west_series",
    "cluster_bootstrap_pvalue",
    "cluster_bootstrap_t",
    "correction",
    "cross_sectional_fold",
    "diebold_mariano_test",
    "dm_lags",
    "dm_loss_series",
    "max_informative_horizon",
    "newey_west_mean",
    "no_correction",
    "no_information_test",
    "register_correction",
    "skill_vs_mean",
    "weighted_benjamini_hochberg",
]


#: The stat_test's selectable statistics. A TUPLE, not a registry, on
#: purpose: the test is an OWNED kind — the statistic is not
#: config-swappable or extensible (corrections are policies; the
#: statistic is the ruler).
METHODS = ("plain", "studentized")


def _check_cluster_scores(cluster_scores):
    """Validate a per-cluster score map; return its sorted cluster keys."""
    if not cluster_scores:
        raise ValueError("cluster_scores is empty — nothing to test")
    clusters = sorted(cluster_scores)
    for c in clusters:
        if not cluster_scores[c]:
            raise ValueError(f"cluster {c!r} has no scores — upstream bug")
    return clusters


def _check_n_boot(n_boot):
    """``n_boot`` must be an int >= 1 — a zero-replicate bootstrap would
    return a silent 1.0 (plain) or index an empty list (studentized)."""
    if isinstance(n_boot, bool) or not isinstance(n_boot, int) or n_boot < 1:
        raise ValueError(f"n_boot must be an int >= 1, got {n_boot!r}")


def _bootstrap_rng(seed, label):
    """The pinned RNG recipe: sha256 of ``seed:label``, first 8 bytes."""
    digest = hashlib.sha256(f"{seed}:{label}".encode()).digest()
    return random.Random(int.from_bytes(digest[:8], "big"))


def cluster_bootstrap_pvalue(cluster_scores, n_boot, seed, label="") -> float:
    """One-sided cluster-bootstrap p-value for ``mean(score) <= 0``.

    Parameters
    ----------
    cluster_scores : mapping of str -> list of float
        Per-cluster score samples (e.g. per-record ``baseline_loss -
        model_loss`` grouped by the record's cluster; positive = model
        better). Clusters are the resampling unit.
    n_boot : int
        Bootstrap replicates, >= 1.
    seed : int
        Base seed; combined with ``label`` so each instrument's p-value is
        independent of every other's and of iteration order.
    label : str
        Stable per-test tag (normally the instrument id).

    Returns
    -------
    float
        ``(1 + #{boot mean <= 0}) / (n_boot + 1)`` — the add-one bootstrap
        p-value; never exactly 0 or 1.

    Raises
    ------
    ValueError
        On an empty score map or an empty cluster (a cluster with no
        scores is an upstream bug, not a resampling detail to paper over).
    """
    clusters = _check_cluster_scores(cluster_scores)
    _check_n_boot(n_boot)
    rng = _bootstrap_rng(seed, label)
    n = len(clusters)
    at_or_below = 0
    for _ in range(n_boot):
        total, count = 0.0, 0
        for _ in range(n):
            picked = cluster_scores[clusters[rng.randrange(n)]]
            total += sum(picked)
            count += len(picked)
        if total / count <= 0.0:
            at_or_below += 1
    return (1 + at_or_below) / (n_boot + 1)


def _pooled_mean_and_se(totals, sizes):
    """Size-weighted pooled mean + cluster-robust SE of a cluster sample.

    The statistic is the ratio-of-totals ``theta = sum(T_j) / sum(m_j)``
    (identical to the plain method's pooled mean), and the SE is its
    Taylor linearization with clusters as the independence unit:
    residuals ``u_j = T_j - theta * m_j`` (they sum to zero), and

        se = sqrt( n/(n-1) * sum(u_j^2) ) / sum(m_j)

    For single-record clusters this reduces exactly to the classic
    ``s / sqrt(n)`` with ``s`` the ddof=1 standard deviation — the
    estimator matches the statistic (the SE of the size-weighted mean,
    not of an unweighted mean of cluster means).
    """
    n = len(totals)
    m_total = sum(sizes)
    theta = sum(totals) / m_total
    ss = sum((t - theta * m) ** 2 for t, m in zip(totals, sizes))
    se = math.sqrt(n / (n - 1) * ss) / m_total
    return theta, se


def cluster_bootstrap_t(cluster_scores, n_boot, seed, label="", alpha=0.05) -> dict:
    """One-sided studentized recentered cluster bootstrap-t for
    ``mean(score) <= 0``.

    The gate-grade sibling of :func:`cluster_bootstrap_pvalue`: each
    replicate is *recentered* at the observed statistic and *studentized*
    by its own cluster-robust SE, so the replicate distribution
    approximates the sampling law of the pivot whether or not H0 holds —
    second-order accuracy the percentile method lacks. The resampling
    stream is IDENTICAL to the plain method's (same RNG recipe, ``n``
    draws per replicate over the sorted clusters), so the two methods
    see the same resamples under one seed.

    Parameters
    ----------
    cluster_scores : mapping of str -> list of float
        Per-cluster score samples; clusters are the resampling unit.
        At least two clusters — a studentized pivot has no variance
        estimate from one.
    n_boot : int
        Bootstrap replicates, >= 1.
    seed : int
        Base seed, combined with ``label`` exactly as the plain method.
    label : str
        Stable per-test tag (normally the instrument id).
    alpha : float
        Level for the two-sided ``1 - alpha`` bootstrap-t confidence
        interval. The interval is DESCRIPTIVE per-instrument evidence,
        uncorrected for the family — never a second decision procedure.

    Returns
    -------
    dict
        ``{"p_value", "mean", "se", "t", "ci_low", "ci_high",
        "n_clusters"}``. ``t``/``ci_low``/``ci_high`` are ``None`` (never
        NaN — evidence is serialized with ``allow_nan=False``) when the
        sample or the relevant pivot tail is degenerate.

    Raises
    ------
    ValueError
        On an empty score map, an empty cluster, fewer than two
        clusters, or an alpha outside (0, 1).
    """
    clusters = _check_cluster_scores(cluster_scores)
    _check_n_boot(n_boot)
    n = len(clusters)
    if n < 2:
        raise ValueError(
            "bootstrap-t needs at least 2 clusters — the studentized "
            "pivot has no variance estimate from one"
        )
    if not isinstance(alpha, (int, float)) or isinstance(alpha, bool) or not 0 < alpha < 1:
        raise ValueError(f"alpha must lie in (0, 1), got {alpha!r}")
    totals = [float(sum(cluster_scores[c])) for c in clusters]
    sizes = [len(cluster_scores[c]) for c in clusters]
    theta_hat, se_hat = _pooled_mean_and_se(totals, sizes)

    # Signed-degenerate observed sample: every cluster's mean agrees with
    # every other's, so no variance exists to studentize. Detected
    # STRUCTURALLY (equal cluster means), not by ``se_hat == 0.0`` alone:
    # the pooled mean can round a hair away from the common value, and an
    # exact-zero test would then let ULP dust masquerade as a t of ~1e16
    # with a zero-width interval. No resampling happens; the SIGN
    # decides. A positive mean's p is floored at HALF THE ALL-ONE-CLUSTER
    # REPLICATE MASS, n^(1-n)/2 — the method's own resampling floor — so
    # an exact tie can never claim more significance than an
    # epsilon-perturbed sample could (at n=2 that floor is 0.25; by n~8
    # it is far below the add-one floor and the add-one floor rules). A
    # non-positive mean is simply not an edge. No interval is claimed
    # from a zero-variance sample.
    if se_hat == 0.0 or len({t / m for t, m in zip(totals, sizes)}) == 1:
        if theta_hat > 0:
            p = max(1 / (n_boot + 1), n ** (1 - n) / 2)
        else:
            p = 1.0
        return {
            "p_value": p,
            "mean": theta_hat,
            "se": 0.0,
            "t": None,
            "ci_low": None,
            "ci_high": None,
            "n_clusters": n,
        }

    rng = _bootstrap_rng(seed, label)
    t_stars = []
    for _ in range(n_boot):
        # The same draw pattern as the plain method: n picks with
        # replacement over the sorted cluster list, one randrange each.
        picks = [rng.randrange(n) for _ in range(n)]
        b_totals = [totals[i] for i in picks]
        b_sizes = [sizes[i] for i in picks]
        theta_b, se_b = _pooled_mean_and_se(b_totals, b_sizes)
        d = theta_b - theta_hat
        if se_b > 0.0:
            # Recentered pivot: centered at the OBSERVED statistic, so the
            # replicate law tracks the sampling law of (theta - mu)/se
            # regardless of whether H0 holds.
            t_stars.append(d / se_b)
        else:
            # Replicate-level degeneracy (e.g. one cluster drawn n
            # times): signed convention keeps the pivot ordered.
            t_stars.append(math.inf if d > 0 else (-math.inf if d < 0 else 0.0))

    # One-sided upper tail, add-one: H0 (mean <= 0) is tested at its
    # boundary, so the observed pivot is theta_hat / se_hat.
    t_obs = theta_hat / se_hat
    exceed = sum(1 for t in t_stars if t >= t_obs)
    p = (1 + exceed) / (n_boot + 1)

    # Two-sided bootstrap-t interval at 1 - alpha from the same
    # replicates. Quantile convention (pinned for determinism): the
    # q-quantile is t(k), k = ceil(q * (B + 1)) clamped to [1, B]. The
    # UPPER pivot quantile gives the LOWER bound — the bootstrap-t
    # construction. A non-finite pivot in a selected tail (guaranteed for
    # 2-cluster samples, where single-cluster draws are common) means
    # that bound is not claimable: None, never an infinity.
    t_sorted = sorted(t_stars)

    def _quantile(q):
        k = min(max(math.ceil(q * (n_boot + 1)), 1), n_boot)
        return t_sorted[k - 1]

    hi_pivot = _quantile(1 - alpha / 2)
    lo_pivot = _quantile(alpha / 2)
    ci_low = theta_hat - hi_pivot * se_hat if math.isfinite(hi_pivot) else None
    ci_high = theta_hat - lo_pivot * se_hat if math.isfinite(lo_pivot) else None
    return {
        "p_value": p,
        "mean": theta_hat,
        "se": se_hat,
        "t": t_obs,
        "ci_low": ci_low,
        "ci_high": ci_high,
        "n_clusters": n,
    }


def _as_pair_series(y, yhat):
    """Equal-length finite numeric sequences; refuse bools and empties."""
    if not isinstance(y, (list, tuple)) or not isinstance(yhat, (list, tuple)):
        raise ValueError(
            "y and yhat must be lists or tuples of numbers, "
            f"got {type(y).__name__} and {type(yhat).__name__}"
        )
    if len(y) != len(yhat):
        raise ValueError(f"y and yhat must have equal length, got {len(y)} and {len(yhat)}")
    if not y:
        raise ValueError("y is empty — nothing to test")
    ys, fs = [], []
    for i, (yi, fi) in enumerate(zip(y, yhat)):
        if not number_ok(yi):
            raise ValueError(f"y[{i}] must be a finite number, got {yi!r}")
        if not number_ok(fi):
            raise ValueError(f"yhat[{i}] must be a finite number, got {fi!r}")
        ys.append(float(yi))
        fs.append(float(fi))
    return ys, fs


def _resolve_mu(y, mu):
    """Train-supplied mean, or the mean of this ``y`` when ``mu`` is omitted."""
    if mu is None:
        return sum(y) / len(y)
    if not number_ok(mu):
        raise ValueError(f"mu must be a finite number, got {mu!r}")
    return float(mu)


def _check_lags(lags, n):
    """``lags`` is an int in ``[0, n)`` — overlap in observation steps."""
    if isinstance(lags, bool) or not isinstance(lags, int) or lags < 0:
        raise ValueError(f"lags must be an int >= 0, got {lags!r}")
    if lags >= n:
        raise ValueError(f"lags must be < n (got lags={lags}, n={n})")
    return lags


def clark_west_series(y, yhat, mu=None):
    """Clark–West MSPE-adjusted loss gap of a forecast vs the mean.

    For each pair, ``(y-μ)² - (y-ŷ)² + (ŷ-μ)²`` — equivalently
    ``2(y-μ)(ŷ-μ)``. A positive mean is extra predictive content relative
    to always guessing ``μ``. Pass the returned series to
    :func:`newey_west_mean` (time-ordered overlap) or group it and pass
    the groups to :func:`cluster_bootstrap_t` (cluster as the
    independence unit).

    Parameters
    ----------
    y : list or tuple of float
        Realized values, one per observation.
    yhat : list or tuple of float
        Forecasts, same length as ``y``.
    mu : float or None
        Unconditional-mean benchmark. ``None`` uses the mean of ``y``.

    Returns
    -------
    list of float
        One adjusted gap per observation, in input order.

    Raises
    ------
    ValueError
        On empty or unequal inputs, or a non-finite cell / ``mu``.

    Examples
    --------
    A constant forecast equal to ``mu`` has no content::

        clark_west_series([1.0, 3.0], [2.0, 2.0], mu=2.0)
        # -> [0.0, 0.0]
    """
    ys, fs = _as_pair_series(y, yhat)
    m = _resolve_mu(ys, mu)
    return [2.0 * (yi - m) * (fi - m) for yi, fi in zip(ys, fs)]


def _norm_sf(z):
    """Upper tail ``P(Z > z)`` for a standard normal, via ``erfc``."""
    return 0.5 * math.erfc(z / math.sqrt(2.0))


def newey_west_mean(values, lags=0):
    """One-sided HAC t-test that ``E[values] <= 0``.

    Newey–West (1987) Bartlett kernel, autocovariances divided by ``n``
    (not ``n-j``). ``lags`` is the MA order in **observation steps** —
    overlapping h-step errors on a series sampled every step take
    ``lags=h-1``; consecutive non-overlapping observations take ``0``.

    Parameters
    ----------
    values : list or tuple of float
        Time-ordered observations of the score whose mean is tested.
    lags : int
        Bartlett truncation, ``0 <= lags < n``.

    Returns
    -------
    dict
        ``{"n", "mean", "se", "t", "p_value", "lags"}``. ``t`` is
        ``None`` when the series has no variance (sign of the mean
        decides ``p_value``: ``0.0`` if positive, else ``1.0``).

    Raises
    ------
    ValueError
        On fewer than two observations, a non-finite cell, or ``lags``
        outside ``[0, n)``.

    Examples
    --------
    Four observations, one lag, mean 2.5 and SE 0.625::

        out = newey_west_mean([1.0, 2.0, 3.0, 4.0], lags=1)
        # -> out["t"] == 4.0
    """
    if not isinstance(values, (list, tuple)):
        raise ValueError(
            f"values must be a list or tuple of numbers, got {type(values).__name__}"
        )
    series = []
    for i, v in enumerate(values):
        if not number_ok(v):
            raise ValueError(f"values[{i}] must be a finite number, got {v!r}")
        series.append(float(v))
    n = len(series)
    if n < 2:
        raise ValueError(f"newey_west_mean needs at least 2 observations, got {n}")
    lags = _check_lags(lags, n)
    mean = sum(series) / n
    if len(set(series)) == 1:
        return {
            "n": n,
            "mean": mean,
            "se": 0.0,
            "t": None,
            "p_value": 0.0 if mean > 0.0 else 1.0,
            "lags": lags,
        }
    centered = [v - mean for v in series]
    gamma0 = sum(c * c for c in centered) / n
    lrv = gamma0
    for lag in range(1, lags + 1):
        weight = 1.0 - lag / (lags + 1)
        gamma = sum(centered[t] * centered[t - lag] for t in range(lag, n)) / n
        lrv += 2.0 * weight * gamma
    if lrv <= 0.0:
        return {
            "n": n,
            "mean": mean,
            "se": 0.0,
            "t": None,
            "p_value": 0.0 if mean > 0.0 else 1.0,
            "lags": lags,
        }
    se = math.sqrt(lrv / n)
    t = mean / se
    return {
        "n": n,
        "mean": mean,
        "se": se,
        "t": t,
        "p_value": _norm_sf(t),
        "lags": lags,
    }


def no_information_test(y, yhat, mu=None, lags=0, horizon=None):
    """Forecast vs unconditional mean: MSPE both sides, Clark–West p.

    Left side is the model's mean squared error; right side is the mean
    squared error of always guessing ``μ``. The null of **no information**
    is left ≥ right. Inference is the nested Clark–West t-statistic of
    that comparison (not naive Diebold–Mariano), with Newey–West overlap
    correction. A panel of names must be collapsed or tested per unit
    **before** this function — it treats the pair series as one
    time-ordered sample.

    Parameters
    ----------
    y : list or tuple of float
        Realized target (the same object the forecast is scored on).
    yhat : list or tuple of float
        Forecasts, same length as ``y``.
    mu : float or None
        Benchmark mean. ``None`` uses the mean of this ``y``. Pass a
        train-set mean when the benchmark must not peek at the scored
        sample.
    lags : int
        Newey–West lag in observation steps (``h_steps - 1`` when
        consecutive rows overlap by ``h_steps - 1`` periods).
    horizon : number or None
        Optional label copied onto the result for
        :func:`max_informative_horizon`. Omitted when not supplied.

    Returns
    -------
    dict
        ``n``, ``mu``, ``mspe_model`` (left), ``mspe_mean`` (right),
        ``beats_mean`` (strict left < right, descriptive), ``mean_adj``,
        ``se``, ``t``, ``p_value`` (one-sided Clark–West), ``lags``.
        ``horizon`` only when passed.

    Raises
    ------
    ValueError
        On a bad pair series, ``mu``, ``lags``, or ``horizon``.

    Examples
    --------
    A perfect forecast of a varying series beats the mean::

        out = no_information_test([1.0, 2.0, 3.0, 4.0], [1.0, 2.0, 3.0, 4.0], mu=2.5)
        # -> out["beats_mean"] is True
    """
    ys, fs = _as_pair_series(y, yhat)
    m = _resolve_mu(ys, mu)
    n = len(ys)
    mspe_model = sum((yi - fi) ** 2 for yi, fi in zip(ys, fs)) / n
    mspe_mean = sum((yi - m) ** 2 for yi in ys) / n
    hac = newey_west_mean(clark_west_series(ys, fs, mu=m), lags=lags)
    out = {
        "n": n,
        "mu": m,
        "mspe_model": mspe_model,
        "mspe_mean": mspe_mean,
        "beats_mean": mspe_model < mspe_mean,
        "mean_adj": hac["mean"],
        "se": hac["se"],
        "t": hac["t"],
        "p_value": hac["p_value"],
        "lags": hac["lags"],
    }
    if horizon is not None:
        if not number_ok(horizon):
            raise ValueError(f"horizon must be a finite number, got {horizon!r}")
        out["horizon"] = horizon if isinstance(horizon, int) else float(horizon)
    return out


def max_informative_horizon(ordered, alpha=0.05):
    """Breitung–Knüppel sequential h*: stop at the first non-rejection.

    Walk ``ordered`` from short horizon to long. Reject no-information
    while ``p_value <= alpha``; the first fail stops the walk. ``h_star``
    is the last rejected horizon, or ``None`` if the first already fails.
    Later rows after a fail are ignored even if they would have rejected.

    This is a **test sequence** at fixed ``alpha``, not a consistent
    selector (that would need α → 0). The walk does **not** check
    Patton–Timmermann monotonicity; that assumption is the caller's.

    Parameters
    ----------
    ordered : sequence of mappings
        Each mapping needs ``horizon`` and ``p_value``. Horizons must be
        strictly increasing.
    alpha : float
        One-sided level in (0, 1).

    Returns
    -------
    dict
        ``h_star``, ``rejected`` (horizons that rejected, in order),
        ``first_fail`` (``None`` when every horizon rejected), ``alpha``,
        ``n_horizons``.

    Raises
    ------
    ValueError
        On an empty walk, a missing key, a non-increasing horizon, a
        p-value outside [0, 1], or a bad alpha.

    Examples
    --------
    Reject at 5 and 10, fail at 15 → ``h_star`` is 10::

        out = max_informative_horizon(
            [
                {"horizon": 5, "p_value": 0.01},
                {"horizon": 10, "p_value": 0.04},
                {"horizon": 15, "p_value": 0.40},
            ]
        )
        # -> out["h_star"] == 10
    """
    if not ordered:
        raise ValueError("ordered is empty — nothing to walk")
    if (
        isinstance(alpha, bool)
        or not isinstance(alpha, (int, float))
        or not 0 < alpha < 1
    ):
        raise ValueError(f"alpha must be a number in (0, 1), got {alpha!r}")
    rejected = []
    first_fail = None
    prev = None
    stopped = False
    n_horizons = 0
    for i, row in enumerate(ordered):
        if not isinstance(row, dict):
            raise ValueError(
                f"ordered[{i}] must be a dict with horizon and p_value, "
                f"got {type(row).__name__}"
            )
        if "horizon" not in row or "p_value" not in row:
            raise ValueError(
                f"ordered[{i}] needs 'horizon' and 'p_value', got {sorted(row)}"
            )
        h = row["horizon"]
        p = row["p_value"]
        if not number_ok(h):
            raise ValueError(f"ordered[{i}].horizon must be a finite number, got {h!r}")
        if (
            isinstance(p, bool)
            or not isinstance(p, (int, float))
            or not math.isfinite(p)
            or not 0 <= p <= 1
        ):
            raise ValueError(
                f"ordered[{i}].p_value must lie in [0, 1], got {p!r}"
            )
        if prev is not None and h <= prev:
            raise ValueError(
                f"horizons must be strictly increasing, got {prev} then {h}"
            )
        prev = h
        n_horizons += 1
        if stopped:
            continue
        if p <= alpha:
            rejected.append(h)
        else:
            first_fail = h
            stopped = True
    return {
        "h_star": rejected[-1] if rejected else None,
        "rejected": list(rejected),
        "first_fail": first_fail,
        "alpha": float(alpha),
        "n_horizons": n_horizons,
    }


def dm_loss_series(y, yhat, mu=None):
    """Squared-error loss gap of a forecast against a constant guess.

    For each pair, ``(y-μ)² - (y-ŷ)²`` — POSITIVE where the forecast sat
    closer than always guessing ``μ``. This is the Diebold–Mariano
    differential: unlike :func:`clark_west_series` it adds nothing back
    for the parameters the forecast spent, so the sign of its mean IS the
    sign of the realized MSPE gap. That is the difference that decides
    whether a forecast is worth having; Clark–West answers a different
    question (is there population-level content) and belongs beside this,
    never instead of it (ADR-0067).

    Parameters
    ----------
    y : list or tuple of float
        Realized values, one per observation, in time order.
    yhat : list or tuple of float
        Forecasts, same length as ``y``.
    mu : float or None
        The constant benchmark. ``None`` uses the mean of ``y``; pass the
        TRAIN mean so the benchmark cannot peek at the scored sample.

    Returns
    -------
    list of float
        One loss gap per observation, in input order.

    Raises
    ------
    ValueError
        On empty or unequal inputs, or a non-finite cell / ``mu``.

    Examples
    --------
    A forecast that nails a series the mean cannot::

        dm_loss_series([1.0, 3.0], [1.0, 3.0], mu=2.0)
        # -> [1.0, 1.0]
    """
    ys, fs = _as_pair_series(y, yhat)
    m = _resolve_mu(ys, mu)
    return [(yi - m) ** 2 - (yi - fi) ** 2 for yi, fi in zip(ys, fs)]


def dm_lags(n, h_steps=1):
    """The Bartlett truncation a DM test on ``n`` overlapping rows takes.

    ``max(h_steps - 1, floor(4 (n/100)^(2/9)))`` — the overlap the label
    itself creates, or Newey–West's automatic rule, whichever is longer.
    Clamped below ``n`` because a Bartlett band cannot span the sample.

    Parameters
    ----------
    n : int
        Observations in the series, ``n >= 2``.
    h_steps : int
        Forecast horizon in OBSERVATION steps (the lead divided by the
        row spacing), ``h_steps >= 1``.

    Returns
    -------
    int
        The lag, in observation steps, in ``[0, n)``.

    Raises
    ------
    ValueError
        On a bad ``n`` or ``h_steps``.

    Examples
    --------
    Ten thousand rows of a 12-step label take the automatic rule::

        dm_lags(10000, h_steps=12)
        # -> 11
    """
    if isinstance(n, bool) or not isinstance(n, int) or n < 2:
        raise ValueError(f"n must be an int >= 2, got {n!r}")
    if isinstance(h_steps, bool) or not isinstance(h_steps, int) or h_steps < 1:
        raise ValueError(f"h_steps must be an int >= 1, got {h_steps!r}")
    automatic = int(4.0 * (n / 100.0) ** (2.0 / 9.0))
    return min(max(h_steps - 1, automatic), n - 1)


def _hln_factor(n, h_steps):
    """Harvey–Leybourne–Newbold small-sample factor for an h-step DM t."""
    numerator = n + 1 - 2 * h_steps + h_steps * (h_steps - 1) / n
    if numerator <= 0.0:
        return 1.0
    return math.sqrt(numerator / n)


def _betacf(a, b, x):
    """Lentz continued fraction for the incomplete beta function."""
    tiny = 1e-30
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < tiny:
        d = tiny
    d = 1.0 / d
    h = d
    for m in range(1, 301):
        m2 = 2 * m
        for num in (
            m * (b - m) * x / ((qam + m2) * (a + m2)),
            -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2)),
        ):
            d = 1.0 + num * d
            if abs(d) < tiny:
                d = tiny
            d = 1.0 / d
            c = 1.0 + num / c
            if abs(c) < tiny:
                c = tiny
            step = d * c
            h *= step
        if abs(step - 1.0) < 1e-14:
            break
    return h


def _betai(a, b, x):
    """Regularized incomplete beta ``I_x(a, b)``, stdlib only."""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    front = math.exp(
        math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
        + a * math.log(x) + b * math.log1p(-x)
    )
    if x < (a + 1.0) / (a + b + 2.0):
        return front * _betacf(a, b, x) / a
    return 1.0 - front * _betacf(b, a, 1.0 - x) / b


def _student_sf(t, df):
    """Upper tail ``P(T > t)`` for Student's t on ``df`` degrees."""
    tail = 0.5 * _betai(df / 2.0, 0.5, df / (df + t * t))
    return tail if t > 0.0 else 1.0 - tail


def diebold_mariano_test(d, lags=0, h_steps=1):
    """One-sided HAC t that a loss gap's mean is at most zero.

    The Diebold–Mariano statistic on a series :func:`dm_loss_series`
    built: Newey–West long-run variance at ``lags``, then the
    Harvey–Leybourne–Newbold small-sample factor for an ``h_steps``-step
    forecast, then normal critical values. Forecasts are taken AS GIVEN
    (Giacomini–White): a bounded rolling training window owes no
    estimation-error correction, which is why nothing is added back here.

    Parameters
    ----------
    d : list or tuple of float
        Loss gaps in TIME order, ``len(d) >= 2``.
    lags : int
        Bartlett truncation in observation steps, ``0 <= lags < len(d)``.
    h_steps : int
        Forecast horizon in observation steps, ``h_steps >= 1``.

    Returns
    -------
    dict
        ``n``, ``mean``, ``se``, ``t`` (HLN-adjusted; ``None`` when the
        series has no variance), ``p_value``, ``lags``, ``h_steps``,
        ``hln``.

    Raises
    ------
    ValueError
        On a bad series, ``lags`` or ``h_steps``.

    Examples
    --------
    A constant positive gap has no variance, so the sign decides::

        diebold_mariano_test([1.0, 1.0, 1.0])["p_value"]
        # -> 0.0
    """
    if isinstance(h_steps, bool) or not isinstance(h_steps, int) or h_steps < 1:
        raise ValueError(f"h_steps must be an int >= 1, got {h_steps!r}")
    hac = newey_west_mean(list(d), lags=lags)
    factor = _hln_factor(hac["n"], h_steps)
    t = None if hac["t"] is None else hac["t"] * factor
    return {
        "n": hac["n"],
        "mean": hac["mean"],
        "se": hac["se"],
        "t": t,
        "p_value": hac["p_value"] if t is None else _norm_sf(t),
        "lags": hac["lags"],
        "h_steps": h_steps,
        "hln": factor,
    }


def across_fold_t(values):
    """One-sided Student t that a per-fold score's mean is at most zero.

    The fold-cluster check a pooled HAC cannot make: each fold
    contributes ONE number (its out-of-sample R², say), and the folds are
    the independence units. ``df`` is ``len(values) - 1``.

    Parameters
    ----------
    values : list or tuple of float
        One score per fold, ``len(values) >= 2``.

    Returns
    -------
    dict
        ``n``, ``mean``, ``se``, ``t`` (``None`` when every fold agrees
        exactly), ``p_value``, ``df``.

    Raises
    ------
    ValueError
        On fewer than two folds or a non-finite cell.

    Examples
    --------
    Four folds averaging 2.5 with SE 0.6455::

        round(across_fold_t([1.0, 2.0, 3.0, 4.0])["t"], 3)
        # -> 3.873
    """
    if not isinstance(values, (list, tuple)):
        raise ValueError(
            "values must be a list or tuple of numbers, "
            f"got {type(values).__name__}"
        )
    series = []
    for i, v in enumerate(values):
        if not number_ok(v):
            raise ValueError(f"values[{i}] must be a finite number, got {v!r}")
        series.append(float(v))
    n = len(series)
    if n < 2:
        raise ValueError(f"across_fold_t needs at least 2 folds, got {n}")
    mean = sum(series) / n
    var = sum((v - mean) ** 2 for v in series) / (n - 1)
    if var <= 0.0:
        return {
            "n": n,
            "mean": mean,
            "se": 0.0,
            "t": None,
            "p_value": 0.0 if mean > 0.0 else 1.0,
            "df": n - 1,
        }
    se = math.sqrt(var / n)
    t = mean / se
    return {
        "n": n,
        "mean": mean,
        "se": se,
        "t": t,
        "p_value": _student_sf(t, n - 1),
        "df": n - 1,
    }


def _check_skill_folds(folds):
    """Validate the fold list :func:`skill_vs_mean` pools; return it."""
    if not isinstance(folds, (list, tuple)) or not folds:
        raise ValueError("folds must be a non-empty list of fold mappings")
    checked = []
    for i, fold in enumerate(folds):
        if not isinstance(fold, dict):
            raise ValueError(f"folds[{i}] must be a mapping, got {fold!r}")
        d = fold.get("d")
        if not isinstance(d, (list, tuple)) or len(d) < 2:
            raise ValueError(f"folds[{i}]['d'] must hold at least 2 gaps")
        for j, v in enumerate(d):
            if not number_ok(v):
                raise ValueError(
                    f"folds[{i}]['d'][{j}] must be a finite number, got {v!r}"
                )
        q = fold.get("q")
        if not number_ok(q) or q <= 0.0:
            raise ValueError(
                f"folds[{i}]['q'] must be a positive finite number, got {q!r}"
            )
        checked.append(([float(v) for v in d], float(q)))
    return checked


def skill_vs_mean(folds, h_steps=1, alpha=0.05, lags=None):
    """The ADR-0067 verdict: does a forecast beat the constant mean?

    Two tests over the same evidence, and BOTH must pass. The pooled one
    concatenates every fold's scale-free gaps ``d_t / q_f`` in time order
    and runs :func:`diebold_mariano_test` on them — dividing by the
    fold's benchmark MSPE is what lets disjoint folds share one series
    without a volatile fold outvoting a quiet one. The across-fold one
    treats each fold's out-of-sample R² as one observation
    (:func:`across_fold_t`), which is the cluster check the HAC cannot
    make. One statistic alone passes on a single lucky fold, or on serial
    dependence the lag rule missed.

    ``R2oos_pool`` is reported beside the verdict because the pass is
    only the SIGN of the win; the R² is its size.

    Parameters
    ----------
    folds : list or tuple of dict
        One mapping per fold, in TIME order, each with ``d`` (the
        per-row loss gaps of :func:`dm_loss_series`, in time order, at
        least two) and ``q`` (that fold's benchmark MSPE, positive).
    h_steps : int
        Forecast horizon in observation steps, ``h_steps >= 1``.
    alpha : float
        One-sided level for both tests, in ``(0, 1)``.
    lags : int or None
        Bartlett truncation for the pooled test. ``None`` takes
        :func:`dm_lags`.

    Returns
    -------
    dict
        ``n_folds``, ``n_rows``, ``h_steps``, ``lags``, ``alpha``,
        ``t_pool``, ``p_pool``, ``t_fold``, ``p_fold``, ``r2oos_pool``,
        ``r2oos_folds`` (one per fold), ``r2oos_fold_mean``,
        ``r2oos_se`` (HAC standard error of the pooled scale-free mean),
        and ``passes``.

    Raises
    ------
    ValueError
        On a malformed fold list, a bad ``h_steps``, ``alpha`` or
        ``lags``.

    Examples
    --------
    Two folds whose gaps average half their benchmark MSPE::

        out = skill_vs_mean([
            {"d": [1.0, 0.9, 1.1, 1.0], "q": 2.0},
            {"d": [1.0, 1.1, 0.9, 1.0], "q": 2.0},
        ])
        out["r2oos_pool"]  # 0.5
    """
    checked = _check_skill_folds(folds)
    if isinstance(h_steps, bool) or not isinstance(h_steps, int) or h_steps < 1:
        raise ValueError(f"h_steps must be an int >= 1, got {h_steps!r}")
    if isinstance(alpha, bool) or not isinstance(alpha, (int, float)):
        raise ValueError(f"alpha must be a number in (0, 1), got {alpha!r}")
    if not 0.0 < alpha < 1.0:
        raise ValueError(f"alpha must lie in (0, 1), got {alpha!r}")
    pooled, r2_folds, sse_model, sse_mean = [], [], 0.0, 0.0
    for d, q in checked:
        pooled.extend(v / q for v in d)
        r2_folds.append(sum(d) / len(d) / q)
        sse_mean += q * len(d)
        sse_model += q * len(d) - sum(d)
    n_rows = len(pooled)
    band = dm_lags(n_rows, h_steps) if lags is None else lags
    dm = diebold_mariano_test(pooled, lags=band, h_steps=h_steps)
    fold = across_fold_t(r2_folds) if len(r2_folds) >= 2 else None
    p_fold = None if fold is None else fold["p_value"]
    return {
        "n_folds": len(checked),
        "n_rows": n_rows,
        "h_steps": h_steps,
        "lags": dm["lags"],
        "alpha": float(alpha),
        "t_pool": dm["t"],
        "p_pool": dm["p_value"],
        "t_fold": None if fold is None else fold["t"],
        "p_fold": p_fold,
        "r2oos_pool": 1.0 - sse_model / sse_mean if sse_mean else 0.0,
        "r2oos_folds": r2_folds,
        "r2oos_fold_mean": sum(r2_folds) / len(r2_folds),
        "r2oos_se": dm["se"],
        "passes": bool(
            dm["mean"] > 0.0
            and dm["p_value"] <= alpha
            and p_fold is not None
            and fold["mean"] > 0.0
            and p_fold <= alpha
        ),
    }


def cross_sectional_fold(units):
    """Collapse one fold's units into the panel series a group DM tests.

    At each timestamp, average the scale-free gaps ``d_t / q`` over the
    units PRESENT at that timestamp (Qu–Timmermann–Zhu): the HAC on the
    cross-sectional average absorbs the dependence between units, so a
    handful of them is not too few. The result is a fold mapping
    :func:`skill_vs_mean` pools like any other, with ``q`` of 1 because
    the scaling already happened.

    Parameters
    ----------
    units : list or tuple of dict
        One mapping per unit, each with ``stamps`` (comparable, one per
        row), ``d`` (loss gaps, same length) and ``q`` (positive).

    Returns
    -------
    dict
        ``stamps`` (sorted, deduplicated), ``d`` (the average at each),
        ``q`` (``1.0``) and ``n_units``.

    Raises
    ------
    ValueError
        On an empty list, a malformed unit, or lengths that disagree.

    Examples
    --------
    Two units sharing one timestamp average there::

        out = cross_sectional_fold([
            {"stamps": [1, 2], "d": [2.0, 2.0], "q": 1.0},
            {"stamps": [2, 3], "d": [4.0, 4.0], "q": 1.0},
        ])
        out["d"]  # [2.0, 3.0, 4.0]
    """
    if not isinstance(units, (list, tuple)) or not units:
        raise ValueError("units must be a non-empty list of unit mappings")
    totals, counts = {}, {}
    for i, unit in enumerate(units):
        if not isinstance(unit, dict):
            raise ValueError(f"units[{i}] must be a mapping, got {unit!r}")
        stamps, d = unit.get("stamps"), unit.get("d")
        if not isinstance(stamps, (list, tuple)) or not isinstance(d, (list, tuple)):
            raise ValueError(f"units[{i}] needs list 'stamps' and 'd'")
        if len(stamps) != len(d):
            raise ValueError(
                f"units[{i}] has {len(stamps)} stamps and {len(d)} gaps"
            )
        q = unit.get("q")
        if not number_ok(q) or q <= 0.0:
            raise ValueError(
                f"units[{i}]['q'] must be a positive finite number, got {q!r}"
            )
        for s, v in zip(stamps, d):
            if not number_ok(v):
                raise ValueError(f"units[{i}] carries a non-finite gap {v!r}")
            totals[s] = totals.get(s, 0.0) + float(v) / float(q)
            counts[s] = counts.get(s, 0) + 1
    order = sorted(totals)
    return {
        "stamps": order,
        "d": [totals[s] / counts[s] for s in order],
        "q": 1.0,
        "n_units": len(units),
    }


def benjamini_hochberg(pvalues, alpha):
    """BH step-up over the family; per-instrument decisions preserved.

    Parameters
    ----------
    pvalues : mapping of str -> float
        Per-instrument p-values (one hypothesis each — never pooled).
    alpha : float
        FDR level in (0, 1).

    Returns
    -------
    dict of str -> bool
        ``instrument -> rejected`` (edge declared).
    """
    _check_pvalues(pvalues, alpha)
    return _step_up(dict(pvalues), alpha)


def weighted_benjamini_hochberg(pvalues, alpha, weights):
    """Weighted BH (Genovese–Roeder): step-up on ``q = p / w``, raw weights.

    A larger weight spends more of the family's budget on that
    instrument (its p-value is divided by more before ranking). Weights
    are used RAW — no normalization is applied. With ``sum(w) == m`` this
    is classic weighted BH with FDR <= alpha (independent p-values); with
    other totals control is at ``alpha * sum(w) / m`` — the budget
    convention is the caller's to own, by design.

    Parameters
    ----------
    pvalues : mapping of str -> float
        Per-instrument p-values. May be a strict subset of the family the
        weights describe (extra weight keys are ignored) — the tested set
        is the family.
    alpha : float
        Nominal FDR level in (0, 1).
    weights : dict of str -> float
        Per-instrument weights; every tested instrument needs one, each a
        finite number > 0.

    Returns
    -------
    dict of str -> bool
        ``instrument -> rejected``.

    Raises
    ------
    ValueError
        On a missing weight for a tested instrument, or a weight that is
        not a finite positive number.
    """
    _check_pvalues(pvalues, alpha)
    if not isinstance(weights, dict):
        raise ValueError(
            f"weights must be a dict of instrument -> weight, "
            f"got {type(weights).__name__}"
        )
    for name in pvalues:
        if name not in weights:
            raise ValueError(f"no weight for tested instrument {name!r}")
        w = weights[name]
        if (
            not isinstance(w, (int, float))
            or isinstance(w, bool)
            or not math.isfinite(w)
            or w <= 0
        ):
            raise ValueError(
                f"weight for {name!r} must be a finite number > 0, got {w!r}"
            )
    # The weighted q may exceed 1 — legal for ranking, not re-checked.
    q = {name: p / weights[name] for name, p in pvalues.items()}
    return _step_up(q, alpha)


def _step_up(values, alpha):
    """The BH step-up shared by the plain and weighted corrections."""
    m = len(values)
    ranked = sorted(values.items(), key=lambda kv: (kv[1], kv[0]))
    k_max = 0
    for i, (_, v) in enumerate(ranked, start=1):
        if v <= alpha * i / m:
            k_max = i
    return {name: i <= k_max for i, (name, _) in enumerate(ranked, start=1)}


def bonferroni(pvalues, alpha):
    """Bonferroni FWER control: reject where ``p <= alpha / m``."""
    _check_pvalues(pvalues, alpha)
    bound = alpha / len(pvalues)
    return {name: p <= bound for name, p in pvalues.items()}


def no_correction(pvalues, alpha):
    """Uncorrected per-instrument tests (explicitly opted into via
    ``correction: "none"`` — the multiplicity cost is the config's to own)."""
    _check_pvalues(pvalues, alpha)
    return {name: p <= alpha for name, p in pvalues.items()}


def _check_pvalues(pvalues, alpha):
    if not pvalues:
        raise ValueError("no p-values to correct — nothing to test")
    for name, p in pvalues.items():
        if not isinstance(p, (int, float)) or isinstance(p, bool) or not 0 < p <= 1:
            raise ValueError(f"p-value for {name!r} must lie in (0, 1], got {p!r}")
    if not 0 < alpha < 1:
        raise ValueError(f"alpha must lie in (0, 1), got {alpha!r}")


#: The correction registry ``StatTestConfig.correction`` / the stat_test
#: kind resolve against: ``name -> {"fn", "needs_weights", "doc"}``.
#: Mirrors ``SPLIT_POLICIES`` — metadata, so the node layer can refuse a
#: weighted correction with no weights wired at plan time.
CORRECTIONS: dict = {}


def register_correction(name, fn, *, needs_weights=False, doc=""):
    """Register a family correction under ``name``.

    Parameters
    ----------
    name : str
        The registry key a config's ``correction`` field names.
    fn : callable
        ``fn(pvalues, alpha)`` — or ``fn(pvalues, alpha, weights)`` when
        ``needs_weights`` — returning ``{instrument: rejected}``.
    needs_weights : bool
        Whether the correction consumes per-instrument weights. The
        stat_test kind uses this to demand (or refuse) a wired
        ``weights`` input.
    doc : str
        One-line description for error messages and docs.
    """
    if not isinstance(name, str) or not name:
        raise ValueError(f"correction name must be a non-empty string, got {name!r}")
    if not callable(fn):
        raise ValueError(f"correction {name!r} must be callable, got {fn!r}")
    if not isinstance(needs_weights, bool):
        raise ValueError(
            f"needs_weights for {name!r} must be a bool, got {needs_weights!r}"
        )
    if name in CORRECTIONS:
        raise ValueError(f"correction {name!r} is already registered")
    CORRECTIONS[name] = {"fn": fn, "needs_weights": needs_weights, "doc": doc}


def correction(name):
    """Look up a registered correction entry, loudly."""
    try:
        return CORRECTIONS[name]
    except KeyError:
        raise ValueError(
            f"unknown correction {name!r} — known: {sorted(CORRECTIONS)}"
        ) from None


register_correction(
    "bh",
    benjamini_hochberg,
    doc="BH step-up FDR control over the family.",
)
register_correction(
    "bonferroni",
    bonferroni,
    doc="Bonferroni FWER control.",
)
register_correction(
    "none",
    no_correction,
    doc="Uncorrected — the multiplicity cost is the config's to own.",
)
register_correction(
    "weighted-bh",
    weighted_benjamini_hochberg,
    needs_weights=True,
    doc="Genovese-Roeder weighted BH on p/w with raw weights.",
)
