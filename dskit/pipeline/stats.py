"""The toolkit's stat-test machinery: cluster bootstraps + multiplicity.

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

__all__ = [
    "CORRECTIONS",
    "METHODS",
    "benjamini_hochberg",
    "bonferroni",
    "cluster_bootstrap_pvalue",
    "cluster_bootstrap_t",
    "correction",
    "no_correction",
    "register_correction",
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
