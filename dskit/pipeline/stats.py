"""The toolkit's stat-test machinery: cluster bootstrap + multiplicity.

Doctrine, venue-neutralized: **per-instrument hypotheses stay
per-instrument** — the toolkit offers NO way to pool instruments into one
aggregate test, and the corrections here borrow strength across the family
while preserving each instrument's own reject/accept (which cell has the
edge IS the deliverable). Resampling is by CLUSTER (the record's
dependence group — an event, a trading day), never by record:
records within a cluster are correlated, and treating them as independent
inflates every test in any venue.

Everything is deterministic: the bootstrap RNG is seeded from
``(seed, instrument)`` via sha256, so per-instrument p-values do not
depend on iteration order, process hash randomization, or each other.

Import cost: stdlib only.
"""

from __future__ import annotations

import hashlib
import random

__all__ = [
    "CORRECTIONS",
    "benjamini_hochberg",
    "bonferroni",
    "cluster_bootstrap_pvalue",
    "no_correction",
]


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
    if not cluster_scores:
        raise ValueError("cluster_scores is empty — nothing to test")
    clusters = sorted(cluster_scores)
    for c in clusters:
        if not cluster_scores[c]:
            raise ValueError(f"cluster {c!r} has no scores — upstream bug")
    digest = hashlib.sha256(f"{seed}:{label}".encode()).digest()
    rng = random.Random(int.from_bytes(digest[:8], "big"))
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
    m = len(pvalues)
    ranked = sorted(pvalues.items(), key=lambda kv: (kv[1], kv[0]))
    k_max = 0
    for i, (_, p) in enumerate(ranked, start=1):
        if p <= alpha * i / m:
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


#: The correction registry ``StatTestConfig.correction`` resolves against.
CORRECTIONS = {
    "bh": benjamini_hochberg,
    "bonferroni": bonferroni,
    "none": no_correction,
}
