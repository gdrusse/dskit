"""The toolkit-OWNED node kinds: the real ``stat_test`` and ``validate``.

Spec §10 step 3: these classes replace the synthetic placeholders
(:class:`~dskit.pipeline.synthetic_nodes.SynthStatTest` /
``SynthScore``) for REAL documents — same roles, same output names, so a
document swaps a synthetic registry for the toolkit one without rewiring.

* :class:`StatTest` (role ``stat_test``) — the edge test the deploy gate
  keys on: a one-sided cluster bootstrap PER INSTRUMENT
  (:func:`~dskit.pipeline.stats.cluster_bootstrap_pvalue`), then ONE
  family correction across the instruments' p-values
  (:data:`~dskit.pipeline.stats.CORRECTIONS`). Doctrine: hypotheses are
  never pooled — the correction borrows strength across the family while
  every instrument keeps its own reject/accept (WHICH instrument has the
  edge IS the deliverable).
* :class:`Validate` (role ``score``) — the model-vs-baseline scorer:
  per-record losses from :data:`~dskit.pipeline.metrics.METRICS` on the
  declared split, plus (when a baseline is wired) the per-instrument,
  per-cluster paired improvements the stat test consumes. ``baseline`` is
  optional — absent means absolute metric reporting with no beats-gate
  (the plain-ML case, docs/24 §5).
* :class:`CrossSectionIC` (role ``transform``, by import path, ADR-0259) —
  the per-instant ordering skill of one or more measures over a record
  stream: :func:`~dskit.pipeline.ordering.per_timestamp_ic` as a node,
  with a benchmark partialled out per measure, split and monthly
  summaries, and the drop census.
* :class:`QuantileSortBook` (role ``transform``, by import path, ADR-0265) —
  the per-instant quantile long-short book of one or more signals: names
  sorted into quantiles by a signal at each stamp, the declared-direction
  spread of an outcome net of a per-side cost, then the series' Newey–West
  statistics, a calendar-block bootstrap interval and one BH / Bonferroni
  family across the signals.

* :class:`ClusteredCalibrationSlope` (role ``transform``, by import path) —
  the calibration slope of an outcome on a forecast per group, with a one-
  or two-way cluster-robust band
  (:func:`~dskit.pipeline.ordering.clustered_calibration_slope`).

Registration is deliberate, never an import side effect: the orchestrator
calls :func:`register` to claim the owned kind names in a registry
(idempotent — names already present are skipped); ``owned=True`` is what
the planner's ``stat_test`` doctrine check keys on.

Import cost: stdlib only.
"""

from __future__ import annotations

import math
import sys
from collections.abc import Mapping
from datetime import date
from types import SimpleNamespace

from dskit.pipeline.document import is_node_ref
from dskit.pipeline.metrics import METRICS
from dskit.pipeline.node import (
    DEFAULT_NODE_KINDS,
    Node,
    check_int_param,
    reject_unknown_params,
)
from dskit.pipeline.ordering import (
    clustered_calibration_slope,
    cross_section_by_stamp,
    ic_from_rho,
    spearman,
)
from dskit.pipeline.records import cluster_ok, number_ok
from dskit.pipeline.split_policy import SPLIT_NAMES
from dskit.pipeline.stats import (
    CalendarBlockBootstrap,
    cluster_bootstrap_pvalue,
    cluster_bootstrap_t,
    correction,
    CORRECTIONS,
    diebold_mariano_test,
    dm_lags,
    METHODS,
    newey_west_mean,
    quantile_bin,
    quantile_edges,
    sample_quantile,
    skill_vs_mean,
)

__all__ = ["DEFAULT_CORRECTIONS", "MIN_CLUSTERS", "P_FLOOR", "ChronologicalForecastSkill", "CrossSectionIC",
           "QuantileSortBook", "StatTest", "Validate", "block_bootstrap_interval", "corrections_problems",
           "family_decisions", "iso_date", "register", "series_statistics"]

#: The bootstrap's honesty floor: below this many clusters an instrument's
#: resampling distribution is degenerate (one cluster resamples to itself
#: every draw), so a single lucky cluster would masquerade as maximal
#: significance. Such instruments degrade to ``p = 1.0`` — no evidence,
#: no crash — and real power comes from banking more events upstream.
MIN_CLUSTERS = 2


#: The ``validate_params`` helper family now has ONE definition, in
#: ``node.py`` beside the protocol it serves — and it is PUBLIC there so
#: a tier-3 child imports it instead of copying it.
#:
#: These aliases stay because nine sibling modules import the private
#: spellings from THIS module, which was never their right home (a
#: statistics module hosting a generic validator). Import
#: ``reject_unknown_params`` / ``check_int_param`` from
#: ``dskit.pipeline.node`` in new code; these are the compatibility
#: shims and can go once the importers move.
_reject_unknown = reject_unknown_params
_check_int = check_int_param


#: The smallest p-value a family accepts (a correction refuses 0).
P_FLOOR = sys.float_info.min
#: The corrections run when a family does not declare ``corrections``.
DEFAULT_CORRECTIONS = ("bh", "bonferroni")


def corrections_problems(corrections):
    """Problems with a declared ``corrections`` list (names of unweighted registered corrections)."""
    if not isinstance(corrections, list) or not corrections:
        return [f"corrections must be a non-empty list of names, got {corrections!r}"]
    problems = []
    for name in corrections:
        entry = CORRECTIONS.get(name) if isinstance(name, str) else None
        if entry is None or entry["needs_weights"]:
            problems.append(f"corrections names {name!r}: must be one of "
                            f"{sorted(n for n, e in CORRECTIONS.items() if not e['needs_weights'])}")
    return problems


def family_decisions(p_values, alpha, corrections=None):
    """The corrections over one family of one-sided p-values.

    Parameters
    ----------
    p_values : dict
        ``name -> p``; a name whose statistic is withheld carries ``None`` and stays in the family at
        ``p = 1`` (the family size is the declared count), listed in ``withheld``.
    alpha : float
        The level in (0, 1).
    corrections : list of str or None
        Registered correction names; ``None`` takes :data:`DEFAULT_CORRECTIONS`.

    Returns
    -------
    dict
        ``alpha``, ``p`` (as tested), ``withheld`` and ``decisions`` (per correction ``name -> rejected``).
    """
    p, withheld = {}, []
    for name, value in p_values.items():
        if value is None:
            p[name] = 1.0
            withheld.append(name)
        else:
            p[name] = min(max(value, P_FLOOR), 1.0)
    names = corrections or list(DEFAULT_CORRECTIONS)
    return {"alpha": alpha, "p": p, "withheld": withheld,
            "decisions": {name: correction(name)["fn"](p, alpha) for name in names}}


def series_statistics(values, lags=None, h_steps=1):
    """The one-sided Newey-West statistics of a stamp-ordered series, with the HAC guard.

    The shared reading of :class:`QuantileSortBook` and ``StampSeriesStats``: ``n``, ``mean`` (exactly
    rounded), ``se``, ``t``, ``p_value`` (one-sided, ``E <= 0`` rejected), ``lags``, ``frac_pos`` and
    ``hac_withheld`` (empty when the statistics stand). A band that spans the series (``lags >= n - 1``),
    fewer than two stamps and a series with no variance keep the mean and withhold the rest.

    Parameters
    ----------
    values : list of float
        The series in stamp order.
    lags : int or None
        The Bartlett truncation; ``None`` takes :func:`~dskit.pipeline.stats.dm_lags` over ``h_steps``.
    h_steps : int
        The horizon in stamp steps.

    Returns
    -------
    dict
        The statistics above.
    """
    n = len(values)
    out = {"n": n, "mean": math.fsum(values) / n if n else None, "se": None, "t": None,
           "p_value": None, "lags": None, "frac_pos": None, "hac_withheld": ""}
    if n == 0:
        out["hac_withheld"] = "no stamp was scored"
        return out
    out["frac_pos"] = sum(1 for v in values if v > 0.0) / n
    if n < 2:
        out["hac_withheld"] = "fewer than 2 scored stamps: no HAC statistic is reported"
        return out
    lags = dm_lags(n, h_steps) if lags is None else int(lags)
    out["lags"] = lags
    if lags >= n - 1:
        out["hac_withheld"] = (f"the Bartlett band ({lags} lag(s)) spans the {n}-stamp "
                               "series: no HAC statistic is reported")
        return out
    hac = newey_west_mean(values, lags=lags)
    out.update(mean=hac["mean"], se=hac["se"], t=hac["t"], p_value=hac["p_value"])
    if hac["t"] is None:         # no variance: the owner's p of 0.0 or 1.0 is a sign rule, not evidence
        out.update(se=None, p_value=None, hac_withheld="no_variance")
    return out


def iso_date(stamp):
    """The calendar date of an ISO-date stamp (a ``date`` passes), refusing anything else."""
    if type(stamp) is date:
        return stamp
    try:
        return date.fromisoformat(stamp)
    except (TypeError, ValueError):
        raise ValueError(f"the bootstrap needs ISO-date stamps, got {stamp!r}") from None


def block_bootstrap_interval(stamps, values, spec, statistic=None, sampler_class=CalendarBlockBootstrap):
    """The calendar-block bootstrap percentile interval of a statistic of ``values`` (one per stamp).

    Parameters
    ----------
    stamps : list
        ISO-date stamps, strictly increasing and distinct.
    values : list of float
        One value per stamp.
    spec : dict
        ``block_days``, ``replicates``, ``seed``, ``level`` (all required).
    statistic : callable or None
        Maps a resampled value list to a number or ``None``; ``None`` takes the exactly rounded mean.
    sampler_class : type
        The calendar block sampler: :class:`~dskit.pipeline.stats.CalendarBlockBootstrap` (complete blocks only, the
        default) or :class:`~dskit.pipeline.stats.EdgePaddedCalendarBlockBootstrap` (every date in equally many blocks:
        the one for week and month stamps, which complete blocks leave unsupported).

    Returns
    -------
    dict
        ``level``, ``block_days``, ``replicates``, ``seed``, ``supported`` and, when supported, ``lo``,
        ``hi`` (the two-sided ``level`` interval) and ``lower_one_sided`` (the ``1 - level`` quantile).
        A resample whose statistic is ``None`` is left out; no resample left leaves the bounds ``None``.
    """
    level, replicates, seed = spec["level"], int(spec["replicates"]), int(spec["seed"])
    out = {"level": level, "block_days": int(spec["block_days"]), "replicates": replicates,
           "seed": seed, "supported": False, "lo": None, "hi": None, "lower_one_sided": None}
    if not values:
        return out
    sampler = sampler_class([iso_date(s) for s in stamps], out["block_days"])
    out["supported"] = sampler.supported
    if not sampler.supported:
        return out
    take = statistic or (lambda sample: math.fsum(sample) / len(sample))
    stats = sorted(s for s in (take([values[i] for i in draw])
                               for draw in sampler.sample_indices(replicates, seed)) if s is not None)
    if stats:
        tail = (1.0 - level) / 2.0
        out.update(lo=sample_quantile(stats, tail), hi=sample_quantile(stats, 1.0 - tail),
                   lower_one_sided=sample_quantile(stats, 1.0 - level))
    return out


def _field(record, name):
    """Attr-or-key access on one record — the seam that lets ``Validate``
    consume :class:`~dskit.pipeline.records.MarketRecord` objects (or
    any attribute-bearing record) and plain dicts through one code path.
    Attributes win (covering ``MarketRecord.cluster``, a property); a
    mapping key is the fallback; anything else fails loudly by name."""
    value = getattr(record, name, None)
    if value is not None:
        return value
    if isinstance(record, Mapping) and name in record:
        return record[name]
    raise ValueError(f"record {record!r} carries no {name!r} (attribute or key)")


def _belief(provider, record, contract):
    """One provider's probability for one record, or ``None`` for no
    coverage. A mapping answers by ``contract`` (missing = no coverage); a
    :class:`~dskit.pipeline.protocols.SignalProvider`-style object
    answers via ``predict(record)`` (returning ``None`` declines)."""
    if isinstance(provider, Mapping):
        return provider.get(contract)
    return provider.predict(record)


def _soft_field(record, name):
    """:func:`_field` for the EVIDENCE path: ``None`` instead of a raise
    when a record does not carry ``name``.

    Coverage accounting has to attribute rows the scorer SKIPPED, and
    those are exactly the records least likely to be well-formed. A
    reporting surface that crashed on the rows it exists to count would
    be worse than the silence it replaces, so the strict accessor stays
    on the scoring path and this one serves the ledger."""
    try:
        return _field(record, name)
    except ValueError:
        return None


#: Width of one reliability bucket — deciles of the [0, 1] belief range.
_RELIABILITY_BUCKETS = 10


def _bucket(probability):
    """Decile label for a belief, e.g. ``"0.3-0.4"``. The top edge joins
    the last bucket so ``q = 1.0`` is reported, not dropped."""
    index = min(
        _RELIABILITY_BUCKETS - 1, int(float(probability) * _RELIABILITY_BUCKETS)
    )
    index = max(0, index)
    lo = index / _RELIABILITY_BUCKETS
    hi = (index + 1) / _RELIABILITY_BUCKETS
    return f"{lo:.1f}-{hi:.1f}"


class StatTest(Node):
    """The toolkit-owned edge test (role ``stat_test``).

    Per instrument SEPARATELY — never pooled — the one-sided cluster
    bootstrap tests ``mean(improvement) <= 0`` over that instrument's
    cluster values (sha256-deterministic, add-one, seeded per
    ``(seed, instrument)`` so no p-value depends on any other). The
    family correction then decides each instrument's own reject/accept
    across the family's p-values; ``survivors`` are the rejected
    (edge-declared) instruments, sorted, and the verdict is ``"GO"`` iff
    any survive. Instruments with fewer than :data:`MIN_CLUSTERS`
    clusters degrade honestly to ``p = 1.0``; an empty ``scores`` input
    is a NO-GO with no p-values, not a crash.

    ``evidence`` is the run report's per-instrument row: each p-value,
    its cluster count, whether it survived, and what the family
    correction cost it (I-232).

    Two statistics, selected by ``method`` (`stats.METHODS` — a closed
    tuple, not a registry): ``"plain"`` (the default; byte-stable with
    every pre-``method`` run) and ``"studentized"`` (the recentered
    cluster bootstrap-t, which also emits per-instrument ``se``/``t``
    and descriptive ``ci_low``/``ci_high`` bounds). A correction whose
    registry entry declares ``needs_weights`` (e.g. ``"weighted-bh"``)
    requires a wired ``weights`` input — per-instrument weights are
    data, not config — and a ``weights`` wire with a non-weighted
    correction is refused.
    """

    role = "stat_test"
    outputs = ("survivors", "pvalues", "verdict", "evidence")

    #: The class's own knobs — anything else is refused by name.
    _PARAMS = ("alpha", "correction", "method", "n_boot", "seed")

    @classmethod
    def validate_params(cls, params):
        problems = []
        _reject_unknown(problems, params, cls._PARAMS)
        alpha = params.get("alpha", 0.05)
        if (
            isinstance(alpha, bool)
            or not isinstance(alpha, (int, float))
            or not 0 < alpha < 1
        ):
            problems.append(f"alpha must be a number in (0, 1), got {alpha!r}")
        _check_int(problems, "n_boot", params.get("n_boot", 10_000), ge=1000)
        _check_int(problems, "seed", params.get("seed", 0), ge=0)
        correction = params.get("correction", "bh")
        # isinstance first: CORRECTIONS is a dict, and membership against
        # it raises on an unhashable value — a validator must never raise.
        if not isinstance(correction, str) or correction not in CORRECTIONS:
            problems.append(
                f"correction must be one of {sorted(CORRECTIONS)}, got {correction!r}"
            )
        method = params.get("method", "plain")
        # Same isinstance-first shape as the correction check above.
        if not isinstance(method, str) or method not in METHODS:
            problems.append(
                f"method must be one of {sorted(METHODS)}, got {method!r}"
            )
        return problems

    def validate_inputs(self, inputs):
        scores = inputs.get("scores")
        if not isinstance(scores, dict):
            return [
                (
                    "scores must be a dict of instrument -> "
                    f"{{cluster: improvement}}, got {scores!r}"
                )
            ]
        problems = []
        self._check_weights(problems, inputs, scores)
        for inst, clusters in scores.items():
            if not isinstance(inst, str):
                problems.append(f"instrument keys must be strings, got {inst!r}")
                continue
            if not isinstance(clusters, dict):
                problems.append(
                    f"scores[{inst!r}] must be a dict of cluster -> improvement, "
                    f"got {clusters!r}"
                )
                continue
            for cluster, value in clusters.items():
                if not isinstance(cluster, str):
                    problems.append(
                        f"scores[{inst!r}]: cluster keys must be strings, "
                        f"got {cluster!r}"
                    )
                # Non-finite values must be refused HERE: a NaN mean makes
                # every ``boot_mean <= 0`` comparison False, so a NaN would
                # ride the bootstrap as maximal significance.
                if (
                    isinstance(value, bool)
                    or not isinstance(value, (int, float))
                    or not math.isfinite(value)
                ):
                    problems.append(
                        f"scores[{inst!r}][{cluster!r}] must be a finite number, "
                        f"got {value!r}"
                    )
        return problems

    def _check_weights(self, problems, inputs, scores):
        """The weights-port rules: demanded by a ``needs_weights``
        correction, refused otherwise, and shaped when present.

        Degraded instruments (below :data:`MIN_CLUSTERS`) still enter the
        correction family at ``p = 1.0``, so EVERY instrument in
        ``scores`` needs a weight — not just the testable ones.
        """
        correction = self.params.get("correction", "bh")
        entry = CORRECTIONS.get(correction) if isinstance(correction, str) else None
        needs = bool(entry and entry["needs_weights"])
        weights = inputs.get("weights")
        if needs and weights is None:
            problems.append(
                f"correction {correction!r} needs per-instrument weights — "
                "wire a weights input"
            )
            return
        if not needs and weights is not None:
            problems.append(
                f"a weights input is wired but correction {correction!r} does "
                "not use weights — a knob silently ignored is a config lie"
            )
            return
        if weights is None:
            return
        if not isinstance(weights, dict):
            problems.append(
                f"weights must be a dict of instrument -> weight, got {weights!r}"
            )
            return
        for name, w in weights.items():
            if not isinstance(name, str):
                problems.append(f"weights: instrument keys must be strings, got {name!r}")
            if (
                isinstance(w, bool)
                or not isinstance(w, (int, float))
                or not math.isfinite(w)
                or w <= 0
            ):
                problems.append(
                    f"weights[{name!r}] must be a finite number > 0, got {w!r}"
                )
        for inst in scores:
            if isinstance(inst, str) and inst not in weights:
                problems.append(f"no weight for instrument {inst!r}")

    @classmethod
    def serving_effect(cls, params, verified_run_evidence):
        """Classify the kind for serving: ``"pure"`` — the bootstrap reads its scores and its knobs, nothing else (ADR-0091, phase 2b).

        :meth:`run` never touches ``ctx``: the resampling is seeded from
        ``(seed, instrument)`` and the family correction reads only the
        p-values it just computed, so the verdict is a function of the
        wired ``scores`` and this node's params. A served document that
        would rather replay the training verdict than recompute one
        declares ``serving.replay.stat_test`` instead.

        Parameters
        ----------
        params : dict
            The declared params; unused — the answer holds for every document.
        verified_run_evidence : dict
            The release's evidence; unused — a pure node needs none.

        Returns
        -------
        str
            ``"pure"``.
        """
        return "pure"

    def run(self, ctx, inputs):
        alpha = self.params.get("alpha", 0.05)
        n_boot = self.params.get("n_boot", 10_000)
        seed = self.params.get("seed", 0)
        correction = self.params.get("correction", "bh")
        method = self.params.get("method", "plain")
        pvalues = {}
        tstats = {}
        for inst in sorted(inputs["scores"]):
            clusters = inputs["scores"][inst]
            if len(clusters) < MIN_CLUSTERS:
                pvalues[inst] = 1.0
                self.log.info(
                    "stat test: %s has %d cluster(s) (< %d) — degrading to p=1.0",
                    inst,
                    len(clusters),
                    MIN_CLUSTERS,
                )
                continue
            wrapped = {cluster: [float(value)] for cluster, value in clusters.items()}
            if method == "studentized":
                res = cluster_bootstrap_t(
                    wrapped, n_boot, seed, label=inst, alpha=alpha
                )
                pvalues[inst] = res["p_value"]
                tstats[inst] = res
            else:
                pvalues[inst] = cluster_bootstrap_pvalue(
                    wrapped, n_boot, seed, label=inst
                )
        if pvalues:
            entry = CORRECTIONS[correction]
            if entry["needs_weights"]:
                rejected = entry["fn"](pvalues, alpha, inputs["weights"])
            else:
                rejected = entry["fn"](pvalues, alpha)
            # An UNTESTED instrument (below MIN_CLUSTERS, sentinel p=1.0)
            # can never be a survivor. The plain corrections make this
            # unreachable arithmetically (no threshold reaches 1.0), but a
            # weighted correction ranks q = p/w, and a legal weight can
            # push q = 1/w under the bar — a GO on zero statistical
            # evidence. The sentinel stays IN the family (it spends
            # budget, honestly); it just cannot win.
            survivors = sorted(
                inst
                for inst, hit in rejected.items()
                if hit and len(inputs["scores"][inst]) >= MIN_CLUSTERS
            )
        else:
            survivors = []
        verdict = "GO" if survivors else "NO-GO"
        self.log.info(
            "stat test: %d/%d instrument(s) survive at alpha=%s (%s, %s) — %s",
            len(survivors),
            len(pvalues),
            alpha,
            method,
            correction,
            verdict,
        )
        return {
            "survivors": survivors,
            "pvalues": pvalues,
            "verdict": verdict,
            "evidence": self._evidence(
                inputs["scores"],
                pvalues,
                survivors,
                alpha,
                correction,
                method,
                n_boot,
                seed,
                tstats,
            ),
        }

    #: What each method IS, in the report's own words. The plain TEST
    #: string matches the report renderer's historical fallback exactly,
    #: so that rendered sentence is unchanged for existing documents; the
    #: independence-unit line deliberately is NOT — the stage states the
    #: generic truth ("cluster ...") where the renderer's fallback said
    #: "event (the statistical cluster)". Run output only, never identity
    #: (ADR-0033).
    _TEST_DESCRIPTIONS = {
        "plain": (
            "one-sided cluster bootstrap on the paired improvement "
            "(H0: mean improvement <= 0)"
        ),
        "studentized": (
            "one-sided studentized recentered cluster bootstrap-t on the "
            "paired improvement (H0: mean improvement <= 0)"
        ),
    }
    _STATISTIC_DESCRIPTIONS = {
        "plain": "size-weighted mean improvement over clusters",
        "studentized": (
            "studentized recentered mean improvement "
            "(t = mean / cluster-robust SE)"
        ),
    }

    @staticmethod
    def _evidence(scores, pvalues, survivors, alpha, correction, method, n_boot, seed, tstats):
        """Per-instrument p-values and what the family correction did.

        The correction's EFFECT is the point: an instrument at p = 0.03
        that survives alone and dies in a family of 40 has not changed its
        evidence, and a report that showed only the final survivor list
        would make the two runs look like different data rather than
        different family sizes. ``survives_uncorrected`` is the raw
        ``p < alpha`` comparison — the counterfactual, never a second
        verdict.

        ``totals`` self-describes the test (name, statistic, independence
        unit, replicates, seed, method) so the report renders what
        actually ran, never a fallback description. Studentized rows add
        ``se`` and — when the sample supports them — ``t``/``ci_low``/
        ``ci_high``; a degenerate bound is OMITTED, never null or NaN.
        """
        survivor_set = set(survivors)
        instruments = {}
        for name in sorted(pvalues):
            clusters = scores.get(name) or {}
            n_clusters = len(clusters)
            values = list(clusters.values())
            instruments[name] = {
                "p_value": pvalues[name],
                "n_clusters": n_clusters,
                "tested": n_clusters >= MIN_CLUSTERS,
                "mean_improvement": (sum(values) / n_clusters) if n_clusters else None,
                "survived": name in survivor_set,
                "survives_uncorrected": pvalues[name] < alpha,
                "reason": (
                    ""
                    if n_clusters >= MIN_CLUSTERS
                    else (
                        f"degraded to p=1.0: {n_clusters} cluster(s) < "
                        f"MIN_CLUSTERS={MIN_CLUSTERS}"
                    )
                ),
            }
            res = tstats.get(name)
            if res is not None:
                instruments[name]["se"] = res["se"]
                for key in ("t", "ci_low", "ci_high"):
                    if res[key] is not None:
                        instruments[name][key] = res[key]
        raw = sum(1 for row in instruments.values() if row["survives_uncorrected"])
        delta = raw - len(survivors)
        notes = []
        if delta > 0:
            notes.append(
                f"the {correction!r} family correction removed {delta} "
                f"instrument(s) that cleared alpha={alpha} on their own p-value"
            )
        elif delta < 0:
            # Only a weighted correction can ADMIT: q = p/w with a large
            # weight rejects an instrument whose own p-value did not
            # clear alpha. Say so — "removed -1" would be a lie.
            notes.append(
                f"the {correction!r} family correction admitted {-delta} "
                f"instrument(s) whose own p-value did not clear "
                f"alpha={alpha} (their weights spent the family's budget "
                "toward them)"
            )
        if method == "studentized":
            notes.append(
                f"per-instrument intervals are two-sided {1 - alpha:g} "
                "bootstrap-t bounds — descriptive evidence, uncorrected for "
                "the family, never a second verdict"
            )
        return {
            "stage": "edge test",
            "totals": {
                "family_size": len(pvalues),
                "alpha": alpha,
                "correction": correction,
                "method": method,
                "test": StatTest._TEST_DESCRIPTIONS[method],
                "statistic": StatTest._STATISTIC_DESCRIPTIONS[method],
                "independence_unit": "cluster (the record's dependence group)",
                "n_boot": n_boot,
                "seed": seed,
                "n_survivors": len(survivors),
                "n_survivors_uncorrected": raw,
                "correction_cost": raw - len(survivors),
                "n_untestable": sum(
                    1 for row in instruments.values() if not row["tested"]
                ),
                "verdict": "GO" if survivors else "NO-GO",
            },
            "instruments": instruments,
            "notes": notes,
        }


class Validate(Node):
    """The toolkit-owned model-vs-baseline scorer (role ``score``).

    Scores exactly the records that (1) fall in the declared ``split``
    under ``ctx.splits`` (no splits section = everything in-sample),
    (2) have a known outcome (missing or ``None`` = unsettled, skipped —
    not an error), and (3) have signal coverage (and baseline coverage
    when a baseline is wired — a paired improvement needs both beliefs).
    ``signal``/``baseline`` are each a ``contract -> probability`` mapping
    or an object exposing ``predict(record)``; records are
    :class:`~dskit.pipeline.records.MarketRecord` objects or plain
    dicts (see :func:`_field`).

    ``metrics`` reports the mean per-record loss and count — plus
    ``baseline_loss`` and ``beats_baseline`` when a baseline is wired —
    and the driver forwards its numeric leaves to the sinks
    automatically. ``cluster_scores`` is the stat test's food:
    ``{instrument: {cluster: mean(baseline_loss - model_loss)}}`` when a
    baseline is wired, else ``{}`` (absolute mode). Fewer scoreable
    records than ``min_events`` fails loudly, naming the split and floor.

    ``evidence`` is the run report's coverage ledger (I-232): rows seen
    vs rows scored with a REASON for every skip, the same losses broken
    out per instrument, and reliability by price bucket — because a
    model can beat its baseline on mean loss while being uncalibrated
    exactly where sizing trades, and a mean cannot show that.
    """

    role = "score"
    outputs = ("metrics", "cluster_scores", "evidence")

    #: The class's own knobs — anything else is refused by name.
    _PARAMS = ("metric", "min_events", "split")

    @classmethod
    def validate_params(cls, params):
        problems = []
        _reject_unknown(problems, params, cls._PARAMS)
        split = params.get("split")
        if split not in SPLIT_NAMES:
            problems.append(
                f"split must declare which split this node reads "
                f"({'/'.join(repr(s) for s in SPLIT_NAMES)}), got {split!r}"
            )
        metric = params.get("metric", "logloss")
        # isinstance first: METRICS is a dict, and membership against it
        # raises on an unhashable value — a validator must never raise.
        if not isinstance(metric, str) or metric not in METRICS:
            problems.append(f"metric must be one of {sorted(METRICS)}, got {metric!r}")
        _check_int(problems, "min_events", params.get("min_events", 1), ge=1)
        return problems

    def validate_inputs(self, inputs):
        problems = []
        records = inputs.get("records")
        if isinstance(records, (str, bytes, Mapping)) or not hasattr(
            records, "__iter__"
        ):
            problems.append(
                f"records must be an iterable of record objects/dicts, got {records!r}"
            )
        for port in ("signal", "baseline"):
            provider = inputs.get(port)
            if port == "baseline" and provider is None:
                continue  # optional — absent means absolute scoring
            if not isinstance(provider, Mapping) and not callable(
                getattr(provider, "predict", None)
            ):
                problems.append(
                    f"{port} must be a contract -> probability mapping or expose "
                    f".predict(record), got {provider!r}"
                )
        if not isinstance(inputs.get("outcomes"), dict):
            problems.append(
                f"outcomes must be a dict of contract -> bool, "
                f"got {inputs.get('outcomes')!r}"
            )
        return problems

    def run(self, ctx, inputs):
        split = self.params["split"]
        metric = METRICS[self.params.get("metric", "logloss")]
        floor = self.params.get("min_events", 1)
        signal, outcomes = inputs["signal"], inputs["outcomes"]
        baseline = inputs.get("baseline")
        losses, base_losses = [], []
        per_cluster = {}
        # -- coverage ledger (I-232) ------------------------------------
        # Every ``continue`` below is a row this node declined to score.
        # They were invisible: ``metrics["n"]`` reported the survivors and
        # nothing reported the denominator, so a signal that covered 3% of
        # the split scored exactly like one that covered all of it.
        seen = 0
        per_instrument = {}
        reliability = {}

        def _tally(record, outcome_key):
            instrument = _soft_field(record, "instrument") or "(unattributed)"
            row = per_instrument.setdefault(
                instrument,
                {
                    "n_seen": 0,
                    "n_scored": 0,
                    "skipped_unsettled": 0,
                    "skipped_other_split": 0,
                    "skipped_no_signal": 0,
                    "skipped_no_baseline": 0,
                },
            )
            row[outcome_key] += 1
            return row

        for record in inputs["records"]:
            seen += 1
            contract = _field(record, "contract")
            outcome = outcomes.get(contract)
            _tally(record, "n_seen")
            if outcome is None:
                _tally(record, "skipped_unsettled")
                continue  # unsettled: no label yet, not an error
            if ctx.splits is not None:
                frame = SimpleNamespace(
                    asof_ms=_field(record, "asof_ms"),
                    cluster=_field(record, "cluster"),
                )
                if ctx.splits.split_of(frame) != split:
                    _tally(record, "skipped_other_split")
                    continue
            q_model = _belief(signal, record, contract)
            if q_model is None:
                _tally(record, "skipped_no_signal")
                continue  # no signal coverage for this contract
            q_base = None
            if baseline is not None:
                q_base = _belief(baseline, record, contract)
                if q_base is None:
                    _tally(record, "skipped_no_baseline")
                    continue  # a paired improvement needs both beliefs
            y = 1.0 if outcome else 0.0
            loss = metric(q_model, y)
            losses.append(loss)
            row = _tally(record, "n_scored")
            row.setdefault("loss_sum", 0.0)
            row["loss_sum"] += loss
            # Reliability by PRICE BUCKET: does a belief of 0.7 settle YES
            # about 70% of the time? A model can beat its baseline on mean
            # loss while being badly calibrated in the region sizing
            # actually trades, and the mean cannot show that.
            rel = reliability.setdefault(
                _bucket(q_model), {"n": 0, "sum_predicted": 0.0, "sum_outcome": 0.0}
            )
            rel["n"] += 1
            rel["sum_predicted"] += float(q_model)
            rel["sum_outcome"] += y
            if baseline is not None:
                base_loss = metric(q_base, y)
                base_losses.append(base_loss)
                row.setdefault("baseline_loss_sum", 0.0)
                row["baseline_loss_sum"] += base_loss
                bucket = per_cluster.setdefault(_field(record, "instrument"), {})
                bucket.setdefault(_field(record, "cluster"), []).append(
                    base_loss - loss
                )
        n = len(losses)
        if n < floor:
            raise ValueError(
                f"only {n} scoreable event(s) in the {split!r} split — below "
                f"min_events={floor}"
            )
        metrics = {"loss": sum(losses) / n, "n": n}
        if baseline is not None:
            metrics["baseline_loss"] = sum(base_losses) / n
            metrics["beats_baseline"] = metrics["loss"] < metrics["baseline_loss"]
        cluster_scores = {
            inst: {c: sum(v) / len(v) for c, v in clusters.items()}
            for inst, clusters in per_cluster.items()
        }
        evidence = self._evidence(
            split, seen, n, metrics, per_instrument, per_cluster, reliability, baseline
        )
        self.log.info(
            "validate: %d record(s) scored on split %r — loss %.6f%s",
            n,
            split,
            metrics["loss"],
            f" vs baseline {metrics['baseline_loss']:.6f}" if baseline else "",
        )
        # The meaningful denominator is rows that BELONG to this split, not
        # every row handed to the node: with three splits wired, ~2/3 of the
        # stream is legitimately somebody else's and a warning keyed on the
        # gross count would fire on every healthy run.
        in_split = evidence["totals"]["rows_in_split"]
        if in_split and n / in_split < 0.5:
            self.log.warning(
                "validate: scored only %d of %d in-split record(s) on %r (%.1f%%) "
                "— read the coverage row before trusting the loss",
                n,
                in_split,
                split,
                100.0 * n / in_split,
            )
        return {
            "metrics": metrics,
            "cluster_scores": cluster_scores,
            "evidence": evidence,
        }

    @staticmethod
    def _evidence(
        split, seen, n, metrics, per_instrument, per_cluster, reliability, baseline
    ):
        """The coverage/reliability ledger for the run report.

        Every number here is one this ``run`` already produced on its way
        to ``metrics`` — the per-instrument losses are the same sums the
        mean was taken over, and the paired improvements are the very
        values ``cluster_scores`` hands the edge test. Nothing is scored
        twice.
        """
        in_split = seen - sum(
            row["skipped_other_split"] for row in per_instrument.values()
        )
        instruments = {}
        for name, row in per_instrument.items():
            scored = row["n_scored"]
            out = {k: v for k, v in row.items() if not k.endswith("_sum")}
            out["loss"] = (row.get("loss_sum", 0.0) / scored) if scored else None
            if baseline is not None:
                base_sum = row.get("baseline_loss_sum", 0.0)
                out["baseline_loss"] = (base_sum / scored) if scored else None
                out["improvement"] = (
                    None
                    if not scored
                    else (base_sum - row.get("loss_sum", 0.0)) / scored
                )
                out["beats_baseline"] = (
                    None if not scored else out["loss"] < out["baseline_loss"]
                )
                out["n_clusters"] = len(per_cluster.get(name, {}))
            instruments[name] = out
        return {
            "stage": "validation",
            "split": split,
            "totals": {
                "rows_seen": seen,
                "rows_in_split": in_split,
                "rows_scored": n,
                "rows_skipped_in_split": in_split - n,
                # Coverage of the rows this node was ASKED to score. The
                # gross count is kept beside it so a stream that is mostly
                # some other split's is still visible, but it is not the
                # number a coverage judgement should be made on.
                "coverage": (n / in_split) if in_split else None,
                "loss": metrics["loss"],
                "baseline_loss": metrics.get("baseline_loss"),
                "beats_baseline": metrics.get("beats_baseline"),
                "paired_improvements": sum(
                    len(v) for c in per_cluster.values() for v in c.values()
                ),
            },
            "instruments": instruments,
            "reliability": {
                label: {
                    "n": b["n"],
                    "mean_predicted": b["sum_predicted"] / b["n"],
                    "observed_rate": b["sum_outcome"] / b["n"],
                    "gap": (b["sum_predicted"] - b["sum_outcome"]) / b["n"],
                }
                for label, b in sorted(reliability.items())
            },
            "notes": (
                []
                if baseline is not None
                else [
                    "no baseline wired — absolute scoring only, no paired "
                    "improvements and therefore nothing for an edge test to consume"
                ]
            ),
        }


class CrossSectionIC(Node):
    """Per-instant ordering skill across names, one row table per measure — by import path.

    Role ``transform``. ``uses: "dskit.pipeline.kinds_stats:CrossSectionIC"``;
    never registered, so no document gains it by accident (ADR-0259). The
    number is :func:`~dskit.pipeline.ordering.per_timestamp_ic` over a
    record stream: at each ``stamp_field`` value the names
    (``series_field``) are ranked by a measure's forecast and by its
    outcome, the two orderings are Spearman-correlated, and the per-instant
    series is tested with the Newey–West mean (``lags``, ``h_steps``),
    which says itself when the cross-sections are too thin to mean
    anything (``usable``). Every measure is computed on the same rows in
    one pass.

    A measure names its ``forecast_field`` and ``outcome_field``; with a
    ``control_field`` the instant's number is
    :func:`~dskit.pipeline.ordering.partial_spearman` with that benchmark
    partialled out — the "beyond the benchmark" question (a forecast
    scale against a realized move given the implied scale). There is
    deliberately no ratio form: dividing both sides by one noisy field
    correlates two independent numerators (Pearson's spurious ratio
    correlation), and a noise forecast then passes any bar.
    ``outcome_transform`` ``"abs"`` takes the outcome's size. A row whose
    forecast, outcome or control value is null or not a finite number is
    DROPPED from that measure and counted under ``dropped`` by field;
    nothing is filled. A row missing the stamp, the series or a split
    field is malformed and refuses by index (a split field may be null:
    those rows form their own group). ``splits`` names fields whose every
    distinct value gets its own summary per measure; ``by_month`` adds one
    per ISO month of the stamp (the stamps must then be ISO dates; a split
    field named ``month`` is refused beside it). A summary whose Bartlett
    band spans its whole stamp series (``lags >= n_stamps - 1``, a few
    stamps in one month, say) keeps its mean but withholds the HAC
    statistics and says so in ``hac_withheld``.

    Outputs
    -------
    ``summary``
        ``stage`` (a one-line self-description), ``rows_in``,
        ``stamp_field``, ``series_field``, ``measures`` (per measure the
        :func:`~dskit.pipeline.ordering.per_timestamp_ic` dict plus
        ``rows_scored`` and ``dropped``) and ``splits`` (the same shape per
        ``"<measure>|<field>=<value>"`` and ``"<measure>|month=<YYYY-MM>"``).
    ``records``
        One row per measure and scored instant — ``measure``, ``stamp``,
        ``rho``, ``n_names`` — in measure order, then stamp order.

    Parameters
    ----------
    params : dict
        ``stamp_field``, ``series_field`` (non-empty str, REQUIRED);
        ``measures`` (non-empty ``{name: {"forecast_field",
        "outcome_field", optional "control_field", optional
        "outcome_transform"}}``, names non-empty and without ``|``,
        REQUIRED); ``outcome_transform`` one of :data:`OUTCOME_TRANSFORMS`
        (default ``"identity"``); ``lags`` (int >= 0, or null for the
        automatic band); ``h_steps`` (int >= 1, default 1: the horizon in
        stamp steps); ``min_names`` and ``usable_names`` (int >= 2, or null
        for the ordering module's floors; an integral float, a wired
        count, is taken as its int); ``splits`` (optional non-empty list
        of distinct field names); ``by_month`` (bool, default false).

    Examples
    --------
    A forecast scale against the realized move's size, beyond the implied
    scale, split by phase and by month::

        node = CrossSectionIC("ic", {
            "stamp_field": "quote_date", "series_field": "symbol",
            "measures": {"beyond_market": {
                "forecast_field": "forecast_sd", "outcome_field": "log_move",
                "control_field": "implied", "outcome_transform": "abs"}},
            "h_steps": 5, "splits": ["phase"], "by_month": True})
        out = node.run(ctx, {"records": rows})
        out["summary"]["measures"]["beyond_market"]["ic"]
    """

    role = "transform"
    outputs = ("summary", "records")
    #: What may be done to the outcome before ranking.
    OUTCOME_TRANSFORMS = ("identity", "abs")
    _PARAMS = ("stamp_field", "series_field", "measures", "lags", "h_steps",
               "min_names", "usable_names", "splits", "by_month", "family")
    _MEASURE_KEYS = ("forecast_field", "outcome_field", "control_field", "outcome_transform", "sign")
    _FAMILY_KEYS = ("alpha", "corrections")
    #: The pseudo-split ``by_month`` writes; a declared split may not reuse it.
    _MONTH = "month"
    #: The optional floors: knob -> its inclusive lower bound.
    _FLOORS = {"lags": 0, "h_steps": 1, "min_names": 2, "usable_names": 2}

    @classmethod
    def _measure_problems(cls, name, spec):
        """Problems with one measure's declaration."""
        where = f"measures[{name!r}]"
        if not isinstance(name, str) or not name or "|" in name:
            return [f"{where}: a measure name is a non-empty string without '|'"]
        if not isinstance(spec, dict):
            return [f"{where} must be a dict, got {spec!r}"]
        problems = []
        unknown = sorted(set(spec) - set(cls._MEASURE_KEYS))
        if unknown:
            problems.append(f"{where}: unknown key(s) {unknown} — allowed: {list(cls._MEASURE_KEYS)}")
        for key in ("forecast_field", "outcome_field", "control_field"):
            value = spec.get(key)
            if (key in spec or key != "control_field") and not is_node_ref(value) and (
                    not isinstance(value, str) or not value):
                problems.append(f"{where}.{key} must be a non-empty string, got {value!r}")
        transform = spec.get("outcome_transform", "identity")
        if not is_node_ref(transform) and transform not in cls.OUTCOME_TRANSFORMS:
            problems.append(f"{where}.outcome_transform must be one of "
                            f"{list(cls.OUTCOME_TRANSFORMS)}, got {transform!r}")
        sign = spec.get("sign")
        if "sign" in spec and not is_node_ref(sign) and (isinstance(sign, bool) or sign not in (1, -1)):
            problems.append(f"{where}.sign must be 1 or -1, got {sign!r}")
        return problems

    @classmethod
    def _family_problems(cls, params):
        """Problems with the ``family`` block: its keys, its level, its corrections and a sign on every measure."""
        family = params.get("family")
        if "family" not in params or is_node_ref(family):
            return []
        if not isinstance(family, dict):
            return [f"family must be a dict, got {family!r}"]
        problems = [f"family: unknown key {k!r} — allowed: {list(cls._FAMILY_KEYS)}"
                    for k in sorted(set(family) - set(cls._FAMILY_KEYS))]
        alpha = family.get("alpha")
        if not is_node_ref(alpha) and not (number_ok(alpha) and not isinstance(alpha, bool) and 0 < alpha < 1):
            problems.append(f"family.alpha must be a number in (0, 1), got {alpha!r}")
        if "corrections" in family and not is_node_ref(family["corrections"]):
            problems += ["family." + m for m in corrections_problems(family["corrections"])]
        measures = params.get("measures")
        if isinstance(measures, dict) and not all(
                isinstance(spec, dict) and "sign" in spec for spec in measures.values()):
            problems.append("family tests a declared direction: every measure needs a sign (1 or -1)")
        return problems

    @classmethod
    def validate_params(cls, params):
        """Problems with the declared knobs, empty when none.

        Parameters
        ----------
        params : dict
            The node's ``params`` block, possibly carrying ``$`` references.

        Returns
        -------
        list of str
            One message per problem.
        """
        problems = []
        reject_unknown_params(problems, params, cls._PARAMS)
        for name in ("stamp_field", "series_field"):
            value = params.get(name)
            if not is_node_ref(value) and (not isinstance(value, str) or not value):
                problems.append(f"{name} must be a non-empty string, got {value!r}")
        measures = params.get("measures")
        if is_node_ref(measures):
            pass
        elif not isinstance(measures, dict) or not measures:
            problems.append(f"measures must be a non-empty dict of name -> measure, got {measures!r}")
        else:
            for name, spec in measures.items():
                problems.extend(cls._measure_problems(name, spec))
        for name, floor in cls._FLOORS.items():
            value = params.get(name)
            if name in params and value is not None and not is_node_ref(value):
                check_int_param(problems, name, value, ge=floor)
        splits = params.get("splits")
        if "splits" in params and not is_node_ref(splits):
            if (not isinstance(splits, list) or not splits
                    or not all(isinstance(v, str) and v for v in splits)):
                problems.append(f"splits must be a non-empty list of field names, got {splits!r}")
            elif len(set(splits)) < len(splits):
                problems.append("splits names must be distinct")
        problems.extend(cls._family_problems(params))
        by_month = params.get("by_month", False)
        if not is_node_ref(by_month) and not isinstance(by_month, bool):
            problems.append(f"by_month must be a boolean, got {by_month!r}")
        elif by_month is True and isinstance(splits, list) and cls._MONTH in splits:
            problems.append(f"splits may not name {cls._MONTH!r} beside by_month: the "
                            "month summaries would be counted twice")
        return problems

    @staticmethod
    def _describe(name, spec):
        """One measure as text: ``name: f vs abs(y) | control``."""
        outcome = spec["outcome_field"]
        if spec.get("outcome_transform", "identity") == "abs":
            outcome = f"abs({outcome})"
        control = spec.get("control_field")
        given = f" | {control}" if control else ""
        return f"{name}: {spec['forecast_field']} vs {outcome}{given}"

    @staticmethod
    def _required(where, row, field, null_ok=False):
        """Return the row's value of a field it must carry, refusing by row otherwise."""
        if field not in row or (row[field] is None and not null_ok):
            raise ValueError(f"{where} lacks the field {field!r}")
        return row[field]

    @staticmethod
    def _triple(spec, row):
        """``((forecast, outcome, control), None)`` for a usable row, else ``(None, the field that failed)``."""
        forecast, outcome = row.get(spec["forecast_field"]), row.get(spec["outcome_field"])
        if not number_ok(forecast):
            return None, spec["forecast_field"]
        if not number_ok(outcome):
            return None, spec["outcome_field"]
        forecast, outcome = float(forecast), float(outcome)
        if spec.get("outcome_transform", "identity") == "abs":
            outcome = abs(outcome)
        control = spec.get("control_field")
        if control is None:
            return (forecast, outcome, None), None
        benchmark = row.get(control)
        if not number_ok(benchmark):
            return None, control
        return (forecast, outcome, float(benchmark)), None

    def _knob(self, name):
        """The optional int knob as an int (``check_int_param`` admits an integral float), or None."""
        value = self.params.get(name)
        return None if value is None else int(value)

    def _ic_stats(self, cs, rho):
        """The ``ic_from_rho`` dict of a rho series, the HAC statistics withheld when the band spans it."""
        stats = ic_from_rho(
            rho, cs["n_names_all"], lags=self._knob("lags"),
            h_steps=self._knob("h_steps") or 1, usable_names=self._knob("usable_names"),
            n_skipped=cs["n_skipped"])
        withheld = ""
        if stats["lags"] is not None and stats["lags"] >= stats["n_stamps"] - 1:
            withheld = (f"the Bartlett band ({stats['lags']} lag(s)) spans the "
                        f"{stats['n_stamps']}-stamp series: no HAC statistic is reported")
            stats.update(ic_se=None, ic_t=None, ic_p=None)
        stats["hac_withheld"] = withheld
        return stats

    def _score(self, stamps, names, outcomes, forecasts, control, sign=None):
        """The per_timestamp_ic dict and the scored ``(stamp, rho, n_names)`` triples.

        With a declared ``sign`` the dict also carries ``sign`` and ``signed``: the same statistics of
        ``sign * rho``, whose one-sided ``ic_p`` tests the declared direction.
        """
        cs = cross_section_by_stamp(stamps, names, outcomes, forecasts,
                                    min_names=self._knob("min_names"), control=control)
        stats = self._ic_stats(cs, cs["rho"])
        if sign is not None:
            signed = self._ic_stats(cs, [sign * r for r in cs["rho"]])
            stats["sign"] = sign
            stats["signed"] = {k: signed[k] for k in ("ic", "ic_se", "ic_t", "ic_p", "frac_pos", "hac_withheld")}
            stats["signed"]["usable"] = signed["usable"]
        return stats, list(zip(cs["stamps"], cs["rho"], cs["n_names"]))

    def _collect(self, rows):
        """Read the stream once: per measure its usable columns, labels and drop census."""
        p = self.params
        stamp_field, series_field = p["stamp_field"], p["series_field"]
        splits = list(p.get("splits") or [])
        panels = {name: {"stamps": [], "names": [], "y": [], "yhat": [], "control": [],
                         "labels": [], "dropped": {}}
                  for name in p["measures"]}
        rows_in = 0
        for index, row in enumerate(rows):
            rows_in += 1
            where = f"{self.key}: row {index}"
            if not isinstance(row, Mapping):
                raise ValueError(f"{where} must be a mapping, got {type(row).__name__}")
            stamp = self._required(where, row, stamp_field)
            name = self._required(where, row, series_field)
            labels = [(field, self._required(where, row, field, null_ok=True)) for field in splits]
            if p.get("by_month", False):
                labels.append((self._MONTH, str(stamp)[:7]))
            for measure, spec in p["measures"].items():
                triple, failed = self._triple(spec, row)
                panel = panels[measure]
                if triple is None:
                    panel["dropped"][failed] = panel["dropped"].get(failed, 0) + 1
                    continue
                panel["stamps"].append(stamp)
                panel["names"].append(name)
                panel["yhat"].append(triple[0])
                panel["y"].append(triple[1])
                panel["control"].append(triple[2])
                panel["labels"].append(labels)
        return rows_in, panels

    def run(self, ctx, inputs):
        """Score every measure's ordering, overall and per split.

        Parameters
        ----------
        ctx : NodeContext
            The run frame; unused.
        inputs : dict
            ``records`` (REQUIRED): mapping rows carrying the stamp, series
            and measure fields.

        Returns
        -------
        dict
            ``summary`` and ``records`` as the class docstring states.

        Raises
        ------
        ValueError
            A row that is not a mapping or lacks the stamp, series or a
            split field.
        """
        p = self.params
        rows_in, panels = self._collect(inputs["records"])
        measures, splits, records = {}, {}, []
        for name, panel in panels.items():
            controlled = p["measures"][name].get("control_field") is not None
            columns = ("stamps", "names", "y", "yhat") + (("control",) if controlled else ())
            stats, scored = self._score(*[panel[col] for col in columns],
                                        *([] if controlled else [None]),
                                        sign=p["measures"][name].get("sign"))
            measures[name] = {**stats, "rows_scored": len(panel["y"]), "dropped": panel["dropped"]}
            records.extend({"measure": name, "stamp": stamp, "rho": rho, "n_names": n}
                           for stamp, rho, n in scored)
            groups = {}
            for position, labels in enumerate(panel["labels"]):
                for field, value in labels:
                    groups.setdefault((field, value), []).append(position)
            for (field, value), positions in sorted(groups.items(), key=lambda kv: (kv[0][0], str(kv[0][1]))):
                stats, _ = self._score(*([panel[col][i] for i in positions] for col in columns),
                                       *([] if controlled else [None]))
                splits[f"{name}|{field}={value}"] = {**stats, "rows_scored": len(positions)}
        summary = {
            "stage": f"per-{p['stamp_field']} rank IC across {p['series_field']} — "
                     + "; ".join(self._describe(n, s) for n, s in p["measures"].items()),
            "rows_in": rows_in,
            "stamp_field": p["stamp_field"],
            "series_field": p["series_field"],
            "measures": measures,
            "splits": splits,
        }
        if "family" in p:
            summary["family"] = family_decisions(
                {name: (m["signed"]["ic_p"] if m["signed"]["usable"] and m["signed"]["ic_t"] is not None else None)
                 for name, m in measures.items()},
                p["family"]["alpha"], p["family"].get("corrections"))
        self.log.info("%s: %d row(s) in, %d measure(s), %d split summar(ies)",
                      self.key, rows_in, len(measures), len(splits))
        return {"summary": summary, "records": records}


#: The fold name reserved for the union of every declared fold.
POOLED_FOLD = "pooled"
#: The group name reserved for every group together.
ALL_GROUPS = "all"


class ChronologicalForecastSkill(Node):
    """Per-fold forecast skill of named models against one benchmark — by import path (ADR-0272).

    Role ``transform``. ``uses: "dskit.pipeline.kinds_stats:ChronologicalForecastSkill"``;
    never registered. Rows carry an ISO-date ``stamp_field``, a ``group_field``
    (a pair, a name), an ``outcome_field`` and one forecast field per model in
    ``forecasts``; ``benchmark`` names the model every other model is scored
    against (an expanding training mean, say). ``folds`` are declared
    half-open ``[start, end_before)`` date intervals that must not overlap; a
    row outside every fold, with a missing outcome, or with any model's
    forecast missing is dropped and counted, never filled, so every model is
    scored on exactly the same rows.

    Per fold (and :data:`POOLED_FOLD`, their union), model and group (and
    :data:`ALL_GROUPS`): ``n``, ``mse``, ``mse_benchmark``, ``r2oos`` = 1 −
    mse/mse_benchmark, ``ic`` (Spearman of forecast against outcome, ``None``
    when either is constant) with a calendar block-bootstrap interval when
    ``bootstrap`` is declared (``ic_lo``, ``ic_hi``, ``ic_supported``),
    ``hit_rate`` (sign agreement over rows where both sides are non-zero,
    ``hit_n``), and the per-row loss gap ``(y − b)² − (y − f)²`` averaged per
    stamp across the group's rows and tested with the Newey–West mean at
    ``lags = h_steps − 1`` (``gap_mean``, ``gap_se``, ``gap_t``, ``gap_p``,
    ``gap_n``). The gap is signed FOR the model: positive means the model's
    squared error is smaller than the benchmark's.

    ``summary.skill`` holds :func:`~dskit.pipeline.stats.skill_vs_mean` per
    non-benchmark model over the declared folds (all groups); ``comparisons``
    holds :func:`~dskit.pipeline.stats.diebold_mariano_test` of each declared
    ``[model, reference]`` pair per fold and pooled; ``family`` is
    :func:`family_decisions` over the pooled one-sided p-values of every
    non-benchmark model (named by the model) and every comparison (named
    ``"<model>|<reference>"``). Nothing here sizes, ranks or trades.

    Parameters
    ----------
    params : dict
        ``stamp_field``, ``group_field``, ``outcome_field`` (non-empty str,
        REQUIRED); ``forecasts`` (``{model: field}``, at least two, REQUIRED);
        ``benchmark`` (a model name, REQUIRED); ``h_steps`` (int >= 1,
        REQUIRED); ``folds`` (``{name: {start, end_before}}``, non-empty,
        ISO dates, non-overlapping, no name equal to :data:`POOLED_FOLD`,
        REQUIRED). Optional ``comparisons`` (list of ``[model, reference]``),
        ``bootstrap`` (``{block_days, replicates, seed, level}`` as
        :func:`block_bootstrap_interval`), ``alpha`` (default 0.05) and
        ``corrections`` (default :data:`DEFAULT_CORRECTIONS`).

    Examples
    --------
    Two models against the mean over two annual folds::

        node = ChronologicalForecastSkill("skill", {
            "stamp_field": "date", "group_field": "pair", "outcome_field": "y_5",
            "forecasts": {"mean": "f_mean", "lagged": "f_lagged"}, "benchmark": "mean",
            "h_steps": 5, "folds": {"2021": {"start": "2021-01-01", "end_before": "2022-01-01"},
                                    "2022": {"start": "2022-01-01", "end_before": "2023-01-01"}},
            "comparisons": [["lagged", "mean"]]})
        out = node.run(ctx, {"records": rows})
    """

    role = "transform"
    outputs = ("summary", "records")

    _PARAMS = ("stamp_field", "group_field", "outcome_field", "forecasts", "benchmark", "h_steps",
               "folds", "comparisons", "bootstrap", "alpha", "corrections")
    _REQUIRED = ("stamp_field", "group_field", "outcome_field", "forecasts", "benchmark", "h_steps", "folds")
    _BOOTSTRAP_KEYS = ("block_days", "replicates", "seed", "level")

    @classmethod
    def validate_params(cls, params):
        """Default-deny: refuse unknown, missing and malformed knobs."""
        problems = []
        _reject_unknown(problems, params, cls._PARAMS)
        for name in cls._REQUIRED:
            if name not in params:
                problems.append(f"{name} is required")
        for name in ("stamp_field", "group_field", "outcome_field"):
            if name in params and (not isinstance(params[name], str) or not params[name]):
                problems.append(f"{name} must be a non-empty string")
        forecasts = params.get("forecasts")
        models = []
        if "forecasts" in params:
            if (not isinstance(forecasts, dict) or len(forecasts) < 2
                    or any(not isinstance(k, str) or not k or not isinstance(v, str) or not v
                           for k, v in forecasts.items())):
                problems.append("forecasts must map at least two model names to field names")
            else:
                models = list(forecasts)
                if len(set(forecasts.values())) != len(forecasts):
                    problems.append("forecasts must name distinct fields")
        if "benchmark" in params and params["benchmark"] not in models:
            problems.append(f"benchmark must be one of forecasts, got {params.get('benchmark')!r}")
        if "h_steps" in params:
            _check_int(problems, "h_steps", params["h_steps"], ge=1)
        if "folds" in params:
            problems += cls._folds_problems(params["folds"])
        comparisons = params.get("comparisons", [])
        if (not isinstance(comparisons, list)
                or any(not isinstance(c, list) or len(c) != 2 or c[0] == c[1]
                       or any(m not in models for m in c) for c in comparisons)):
            problems.append("comparisons must list [model, reference] pairs of distinct forecast names")
        if "bootstrap" in params:
            spec = params["bootstrap"]
            if not isinstance(spec, dict) or set(spec) != set(cls._BOOTSTRAP_KEYS):
                problems.append(f"bootstrap must declare exactly {list(cls._BOOTSTRAP_KEYS)}")
            else:
                _check_int(problems, "bootstrap.block_days", spec["block_days"], ge=1)
                _check_int(problems, "bootstrap.replicates", spec["replicates"], ge=1)
                _check_int(problems, "bootstrap.seed", spec["seed"], ge=0)
                level = spec["level"]
                if isinstance(level, bool) or not isinstance(level, (int, float)) or not 0 < level < 1:
                    problems.append(f"bootstrap.level must be in (0, 1), got {level!r}")
        alpha = params.get("alpha", 0.05)
        if isinstance(alpha, bool) or not isinstance(alpha, (int, float)) or not 0 < alpha < 1:
            problems.append(f"alpha must be a number in (0, 1), got {alpha!r}")
        if "corrections" in params:
            problems += corrections_problems(params["corrections"])
        return problems

    @classmethod
    def _folds_problems(cls, folds):
        """Problems with the declared folds: named, dated, ordered, disjoint."""
        if not isinstance(folds, dict) or not folds:
            return [f"folds must be a non-empty mapping of name -> {{start, end_before}}, got {folds!r}"]
        problems, spans = [], []
        for name, spec in folds.items():
            if not isinstance(name, str) or not name or name == POOLED_FOLD:
                problems.append(f"fold name {name!r} must be a non-empty string other than {POOLED_FOLD!r}")
            if not isinstance(spec, dict) or set(spec) != {"start", "end_before"}:
                problems.append(f"fold {name!r} must declare exactly start and end_before")
                continue
            try:
                start, end = (iso_date(spec[k]) for k in ("start", "end_before"))
            except ValueError:
                problems.append(f"fold {name!r} dates must be ISO YYYY-MM-DD")
                continue
            if start >= end:
                problems.append(f"fold {name!r} must have start < end_before")
            spans.append((start, end, name))
        spans.sort()
        for (_, end_a, a), (start_b, _, b) in zip(spans, spans[1:]):
            if start_b < end_a:
                problems.append(f"folds {a!r} and {b!r} overlap")
        return problems

    def validate_inputs(self, inputs):
        """Problems with the wired ``records`` (a materialized list)."""
        records = inputs.get("records")
        if not isinstance(records, list):
            return [f"records must be a list of rows, got {type(records).__name__}"]
        return []

    def run(self, ctx, inputs):
        """Assign folds, drop what cannot be scored, and score every model on the same rows.

        Parameters
        ----------
        ctx : NodeContext
            Unused.
        inputs : dict
            ``records``: a list of mapping rows.

        Returns
        -------
        dict
            ``summary`` and ``records`` as the class docstring describes.

        Raises
        ------
        ValueError
            On a malformed stamp or group, or when no row can be scored.
        """
        p = self.params
        models = list(p["forecasts"])
        folds = p["folds"]
        spans = [(iso_date(spec["start"]), iso_date(spec["end_before"]), name) for name, spec in folds.items()]
        dropped = {"outside_folds": 0, "missing_outcome": 0, "missing_forecast": {m: 0 for m in models}}
        cells = []
        for index, row in enumerate(inputs["records"]):
            stamp = _field(row, p["stamp_field"])
            try:
                day = iso_date(stamp)
            except ValueError:
                raise ValueError(f"{self.key}: row {index} stamp {stamp!r} is not an ISO date") from None
            group = _field(row, p["group_field"])
            if not isinstance(group, str) or not group:
                raise ValueError(f"{self.key}: row {index} group {group!r} must be a non-empty string")
            fold = next((name for start, end, name in spans if start <= day < end), None)
            if fold is None:
                dropped["outside_folds"] += 1
                continue
            outcome = _field(row, p["outcome_field"])
            if not number_ok(outcome):
                dropped["missing_outcome"] += 1
                continue
            values = {m: _field(row, f) for m, f in p["forecasts"].items()}
            missing = [m for m, v in values.items() if not number_ok(v)]
            if missing:
                for m in missing:
                    dropped["missing_forecast"][m] += 1
                continue
            cells.append((day.isoformat(), group, fold, float(outcome), {m: float(v) for m, v in values.items()}))
        if not cells:
            raise ValueError(f"{self.key}: no scored rows — every row was outside the folds or unscoreable")
        cells.sort(key=lambda c: (c[0], c[1]))
        fold_names = list(folds)
        groups = sorted({c[1] for c in cells})
        lags = int(p["h_steps"]) - 1
        records = []
        for fold in fold_names + [POOLED_FOLD]:
            in_fold = [c for c in cells if fold == POOLED_FOLD or c[2] == fold]
            if not in_fold:
                continue
            for group in groups + [ALL_GROUPS]:
                rows = [c for c in in_fold if group == ALL_GROUPS or c[1] == group]
                if not rows:
                    continue
                for model in models:
                    records.append(self._score(fold, model, group, rows, lags))
        summary = {
            "stage": f"forecast skill of {models} against {p['benchmark']!r} over folds {fold_names}",
            "rows_in": len(inputs["records"]), "rows_scored": len(cells), "dropped": dropped,
            "folds": fold_names, "groups": groups, "models": models, "benchmark": p["benchmark"],
            "h_steps": int(p["h_steps"]), "lags": lags, "bootstrap": p.get("bootstrap"),
            "skill": self._skill(cells, fold_names, models),
            "comparisons": self._comparisons(cells, fold_names, lags),
        }
        summary["family"] = self._family(records, summary["comparisons"])
        return {"summary": summary, "records": records}

    def _score(self, fold, model, group, rows, lags):
        """One record: the model's loss, ordering and gap statistics on these rows."""
        bench = self.params["benchmark"]
        ys = [c[3] for c in rows]
        fs = [c[4][model] for c in rows]
        bs = [c[4][bench] for c in rows]
        n = len(rows)
        mse = math.fsum((y - f) ** 2 for y, f in zip(ys, fs)) / n
        mse_b = math.fsum((y - b) ** 2 for y, b in zip(ys, bs)) / n
        signed = [(y, f) for y, f in zip(ys, fs) if y * f != 0.0]
        out = {
            "fold": fold, "model": model, "group": group, "n": n,
            "mse": mse, "mse_benchmark": mse_b,
            "r2oos": (1.0 - mse / mse_b) if mse_b > 0.0 else None,
            "ic": spearman(fs, ys) if n >= 2 else None,
            "ic_lo": None, "ic_hi": None, "ic_supported": None,
            "hit_rate": (sum(1 for y, f in signed if y * f > 0.0) / len(signed)) if signed else None,
            "hit_n": len(signed),
            "gap_mean": None, "gap_se": None, "gap_t": None, "gap_p": None, "gap_n": 0, "lags": lags,
        }
        gaps = self._per_stamp(rows, lambda c: (c[3] - c[4][bench]) ** 2 - (c[3] - c[4][model]) ** 2)
        out["gap_n"] = len(gaps)
        if len(gaps) >= 2:
            nw = newey_west_mean([g for _, g in gaps], lags=min(lags, len(gaps) - 1))
            out.update(gap_mean=nw["mean"], gap_se=nw["se"], gap_t=nw["t"], gap_p=nw["p_value"])
        elif gaps:
            out["gap_mean"] = gaps[0][1]
        spec = self.params.get("bootstrap")
        if spec is not None and n >= 2:
            by_stamp = self._per_stamp(rows, lambda c: (c[4][model], c[3]), collapse=False)
            interval = block_bootstrap_interval(
                [s for s, _ in by_stamp], [pairs for _, pairs in by_stamp], spec,
                statistic=self._sample_spearman)
            out.update(ic_lo=interval["lo"], ic_hi=interval["hi"], ic_supported=interval["supported"])
        return out

    @staticmethod
    def _sample_spearman(sample):
        """Spearman over the (forecast, outcome) pairs of a resampled list of stamp groups."""
        pairs = [pair for stamp_pairs in sample for pair in stamp_pairs]
        if len(pairs) < 2:
            return None
        return spearman([f for f, _ in pairs], [y for _, y in pairs])

    @staticmethod
    def _per_stamp(rows, value, collapse=True):
        """``[(stamp, value)]`` in stamp order; with ``collapse`` the per-stamp mean, else the list."""
        buckets = {}
        for cell in rows:
            buckets.setdefault(cell[0], []).append(value(cell))
        if collapse:
            return [(stamp, math.fsum(vals) / len(vals)) for stamp, vals in sorted(buckets.items())]
        return sorted(buckets.items())

    def _skill(self, cells, fold_names, models):
        """``skill_vs_mean`` per non-benchmark model over the declared folds, all groups."""
        bench, h_steps, alpha = self.params["benchmark"], int(self.params["h_steps"]), self.params.get("alpha", 0.05)
        out = {}
        for model in models:
            if model == bench:
                continue
            folds = []
            for fold in fold_names:
                rows = [c for c in cells if c[2] == fold]
                gaps = self._per_stamp(rows, lambda c: (c[3] - c[4][bench]) ** 2 - (c[3] - c[4][model]) ** 2)
                q = math.fsum((c[3] - c[4][bench]) ** 2 for c in rows) / len(rows) if rows else 0.0
                if len(gaps) >= 2 and q > 0.0:
                    folds.append({"d": [g for _, g in gaps], "q": q})
            out[model] = skill_vs_mean(folds, h_steps=h_steps, alpha=alpha) if folds else None
        return out

    def _comparisons(self, cells, fold_names, lags):
        """Diebold–Mariano of each declared [model, reference] per fold and pooled, all groups."""
        h_steps = int(self.params["h_steps"])
        out = {}
        for model, reference in self.params.get("comparisons", []):
            entry = {}
            for fold in fold_names + [POOLED_FOLD]:
                rows = [c for c in cells if fold == POOLED_FOLD or c[2] == fold]
                gaps = self._per_stamp(rows, lambda c: (c[3] - c[4][reference]) ** 2 - (c[3] - c[4][model]) ** 2)
                entry[fold] = (diebold_mariano_test([g for _, g in gaps], lags=min(lags, len(gaps) - 1),
                                                    h_steps=h_steps) if len(gaps) >= 2 else None)
            out[f"{model}|{reference}"] = entry
        return out

    def _family(self, records, comparisons):
        """BH / Bonferroni over the pooled one-sided p-values of the models and comparisons."""
        bench = self.params["benchmark"]
        p_values = {}
        for record in records:
            if record["fold"] == POOLED_FOLD and record["group"] == ALL_GROUPS and record["model"] != bench:
                p_values[record["model"]] = record["gap_p"]
        for name, entry in comparisons.items():
            pooled = entry.get(POOLED_FOLD)
            p_values[name] = None if pooled is None else pooled["p_value"]
        if not p_values:
            return None
        return family_decisions(p_values, self.params.get("alpha", 0.05), self.params.get("corrections"))


class QuantileSortBook(Node):
    """Per-instant quantile long-short book of several signals, with series statistics — by import path.

    Role ``transform``. ``uses: "dskit.pipeline.kinds_stats:QuantileSortBook"``;
    never registered (ADR-0265). At each ``stamp_field`` value the names
    (``series_field``) are cut into ``quantiles`` equal-count buckets by a
    signal (:func:`~dskit.pipeline.stats.quantile_edges` /
    :func:`~dskit.pipeline.stats.quantile_bin`, a tie going to the higher
    bucket) and the book is long one extreme bucket and short the other. The
    declared ``sign`` fixes which: ``1`` longs the highest-signal bucket,
    ``-1`` the lowest, so the spread is positive when the declared direction
    pays. The gross spread is the long bucket's mean outcome less the short
    bucket's; the cost is ``sides_per_leg`` sides on each of the two legs, at
    ``cost_per_side`` (one number) or at the mean of each leg's names'
    ``cost_field`` (a per-name per-side cost, such as a half-spread over
    premium, in the outcome's units); net = gross - cost.

    The series of net spreads, in stamp order, is tested one-sided against
    ``E <= 0`` with :func:`~dskit.pipeline.stats.newey_west_mean`
    (``lags``, or :func:`~dskit.pipeline.stats.dm_lags` over ``h_steps``);
    the gross series gets the same. A series whose Bartlett band spans it
    (``lags >= n - 1``, or fewer than two stamps) keeps its mean and
    withholds the HAC statistics, saying so in ``hac_withheld``; a series with no
    variance (``t`` undefined) reports no ``p_value`` and ``hac_withheld`` of
    ``"no_variance"``. With
    ``bootstrap`` the net series also gets a
    :class:`~dskit.pipeline.stats.CalendarBlockBootstrap` percentile interval
    of its mean (stamps must be ISO dates). The signals are ONE family: the
    corrections (:data:`~dskit.pipeline.stats.CORRECTIONS`, default BH and
    Bonferroni) run over their net p-values at ``alpha``; a signal with no HAC
    statistic stays in the family at ``p = 1`` and is listed in
    ``family.withheld``, so the family size is the declared signal count.

    **Caller contract: ``outcome_field`` is measured strictly AFTER the stamp**
    that its signal was known at; the node cannot see when an outcome began. To
    have it checked, name a timestamp column of the outcome's start in
    ``outcome_after_field``: a row whose value is not strictly later than its
    stamp (or is missing, or not comparable) refuses by index.

    A row whose signal, outcome or cost value is null or not a finite number
    is DROPPED from that signal and counted under ``dropped`` by field. A
    stamp with fewer than ``min_names`` usable names is ``thin``, and one whose
    long or short bucket is empty (ties) is ``empty_bucket``: both counted and
    withheld, never filled. A row missing the stamp, the series or a split
    field, or a second row for one (stamp, name), refuses by index.

    Outputs
    -------
    ``summary``
        ``stage``, ``rows_in``, ``quantiles``, ``signals`` (per signal its
        fields, ``sign``, ``rows_scored``, ``dropped``, ``n_stamps``,
        ``thin_stamps``, ``empty_bucket_stamps``, ``quantile_means`` (mean over
        stamps of each bucket's mean outcome), ``gross`` and ``net`` (the
        series statistics: ``n``, ``mean``, ``se``, ``t``, ``p_value``,
        ``lags``, ``frac_pos``, ``hac_withheld``) and, with ``bootstrap``,
        ``ci``), ``family`` (``alpha``, ``p``, ``withheld``, ``decisions`` by
        correction) and ``splits`` (the series statistics per
        ``"<signal>|<field>=<value>"``).
    ``records``
        One row per signal and scored stamp: ``signal``, ``stamp``,
        ``n_names``, ``quantile_means``, ``gross``, ``cost``, ``net``.

    Parameters
    ----------
    params : dict
        ``stamp_field``, ``series_field`` (non-empty str), ``quantiles`` (int
        >= 2), ``min_names`` (int >= ``quantiles``), ``alpha`` (in (0, 1)) and
        ``signals`` (non-empty ``{name: {"signal_field", "outcome_field",
        optional "sign" (1 or -1, default 1), optional "cost_field"}}``, names
        without ``|``) are REQUIRED. Optional: ``cost_per_side`` (number >= 0;
        not beside a ``cost_field``), ``sides_per_leg`` (int >= 1, default 2:
        in and out), ``lags`` (int >= 0 or null), ``h_steps`` (int >= 1,
        default 1), ``corrections`` (non-empty list from
        :data:`~dskit.pipeline.stats.CORRECTIONS` without a weighted one),
        ``bootstrap`` (``{"block_days", "replicates", "seed", "level"}``, all
        required), ``splits`` (non-empty list of distinct row fields) and
        ``outcome_after_field`` (a column naming when the outcome began; see above).

    Examples
    --------
    A quintile book on one signal, a half-spread cost, a block-bootstrap interval::

        node = QuantileSortBook("book", {
            "stamp_field": "date", "series_field": "symbol", "quantiles": 5,
            "min_names": 50, "alpha": 0.05, "cost_per_side": 0.0005,
            "signals": {"rv_iv_gap": {"signal_field": "gap", "outcome_field": "ret"}},
            "bootstrap": {"block_days": 21, "replicates": 2000, "seed": 7, "level": 0.9}})
        out = node.run(ctx, {"records": rows})
        out["summary"]["signals"]["rv_iv_gap"]["net"]["t"]
    """

    role = "transform"
    outputs = ("summary", "records")
    _PARAMS = ("stamp_field", "series_field", "quantiles", "min_names", "alpha", "signals",
               "cost_per_side", "sides_per_leg", "lags", "h_steps", "corrections", "bootstrap",
               "splits", "outcome_after_field")
    _REQUIRED = ("stamp_field", "series_field", "quantiles", "min_names", "alpha", "signals")
    _SIGNAL_KEYS = ("signal_field", "outcome_field", "sign", "cost_field")
    _BOOTSTRAP_KEYS = ("block_days", "replicates", "seed", "level")
    #: The corrections run when ``corrections`` is not declared.
    DEFAULT_CORRECTIONS = DEFAULT_CORRECTIONS

    @classmethod
    def _signal_problems(cls, name, spec):
        """Problems with one signal's declaration."""
        where = f"signals[{name!r}]"
        if not isinstance(name, str) or not name or "|" in name:
            return [f"{where}: a signal name is a non-empty string without '|'"]
        if not isinstance(spec, dict):
            return [f"{where} must be a dict, got {spec!r}"]
        problems = []
        unknown = sorted(set(spec) - set(cls._SIGNAL_KEYS))
        if unknown:
            problems.append(f"{where}: unknown key(s) {unknown} — allowed: {list(cls._SIGNAL_KEYS)}")
        for key in ("signal_field", "outcome_field", "cost_field"):
            value = spec.get(key)
            if (key in spec or key != "cost_field") and not is_node_ref(value) and (
                    not isinstance(value, str) or not value):
                problems.append(f"{where}.{key} must be a non-empty string, got {value!r}")
        sign = spec.get("sign", 1)
        if not is_node_ref(sign) and (isinstance(sign, bool) or sign not in (1, -1)):
            problems.append(f"{where}.sign must be 1 or -1, got {sign!r}")
        return problems

    @classmethod
    def _bootstrap_problems(cls, spec):
        """Problems with the ``bootstrap`` block."""
        if not isinstance(spec, dict):
            return [f"bootstrap must be a dict, got {spec!r}"]
        problems = []
        unknown = sorted(set(spec) - set(cls._BOOTSTRAP_KEYS))
        if unknown:
            problems.append(f"bootstrap: unknown key(s) {unknown} — allowed: {list(cls._BOOTSTRAP_KEYS)}")
        for key in ("block_days", "replicates"):
            if key not in spec:
                problems.append(f"bootstrap.{key} is required")
            elif not is_node_ref(spec[key]):
                check_int_param(problems, f"bootstrap.{key}", spec[key], ge=1)
        if "seed" not in spec:
            problems.append("bootstrap.seed is required")
        elif not is_node_ref(spec["seed"]):
            check_int_param(problems, "bootstrap.seed", spec["seed"], ge=0)
        level = spec.get("level")
        if "level" not in spec:
            problems.append("bootstrap.level is required")
        elif not is_node_ref(level) and not (number_ok(level) and 0 < level < 1):
            problems.append(f"bootstrap.level must be a number in (0, 1), got {level!r}")
        return problems

    @classmethod
    def validate_params(cls, params):
        """Problems with the declared knobs, empty when none.

        Parameters
        ----------
        params : dict
            The node's ``params`` block, possibly carrying ``$`` references.

        Returns
        -------
        list of str
            One message per problem.
        """
        problems = []
        reject_unknown_params(problems, params, cls._PARAMS)
        for name in cls._REQUIRED:
            if name not in params:
                problems.append(f"{name} is required")
        for name in ("stamp_field", "series_field"):
            value = params.get(name)
            if name in params and not is_node_ref(value) and (not isinstance(value, str) or not value):
                problems.append(f"{name} must be a non-empty string, got {value!r}")
        quantiles = params.get("quantiles")
        if "quantiles" in params and not is_node_ref(quantiles):
            check_int_param(problems, "quantiles", quantiles, ge=2)
        min_names = params.get("min_names")
        if "min_names" in params and not is_node_ref(min_names):
            floor = quantiles if isinstance(quantiles, int) and not isinstance(quantiles, bool) else 2
            check_int_param(problems, "min_names", min_names, ge=max(2, floor))
        alpha = params.get("alpha")
        if "alpha" in params and not is_node_ref(alpha) and not (number_ok(alpha) and 0 < alpha < 1):
            problems.append(f"alpha must be a number in (0, 1), got {alpha!r}")
        problems.extend(cls._signals_problems(params))
        after = params.get("outcome_after_field")
        if "outcome_after_field" in params and not is_node_ref(after) and (
                not isinstance(after, str) or not after):
            problems.append(f"outcome_after_field must be a non-empty column name, got {after!r}")
        cost = params.get("cost_per_side")
        if "cost_per_side" in params and not is_node_ref(cost) and not (number_ok(cost) and cost >= 0):
            problems.append(f"cost_per_side must be a number >= 0, got {cost!r}")
        for name, floor in (("sides_per_leg", 1), ("h_steps", 1), ("lags", 0)):
            value = params.get(name)
            if name in params and not (name == "lags" and value is None) and not is_node_ref(value):
                check_int_param(problems, name, value, ge=floor)
        problems.extend(cls._list_problems(params))
        if "bootstrap" in params and not is_node_ref(params["bootstrap"]):
            problems.extend(cls._bootstrap_problems(params["bootstrap"]))
        return problems

    @classmethod
    def _signals_problems(cls, params):
        """Problems with ``signals`` and with a cost declared twice."""
        signals = params.get("signals")
        if "signals" not in params or is_node_ref(signals):
            return []
        if not isinstance(signals, dict) or not signals:
            return [f"signals must be a non-empty dict of name -> signal, got {signals!r}"]
        problems = []
        for name, spec in signals.items():
            problems.extend(cls._signal_problems(name, spec))
        if params.get("cost_per_side") is not None and any(
                isinstance(spec, dict) and spec.get("cost_field") for spec in signals.values()):
            problems.append("cost_per_side and a signal's cost_field are two costs for one book: declare one")
        return problems

    @classmethod
    def _list_problems(cls, params):
        """Problems with ``corrections`` and ``splits``."""
        problems = []
        corrections = params.get("corrections")
        if "corrections" in params and not is_node_ref(corrections):
            problems.extend(corrections_problems(corrections))
        splits = params.get("splits")
        if "splits" in params and not is_node_ref(splits):
            if (not isinstance(splits, list) or not splits
                    or not all(isinstance(v, str) and v for v in splits)):
                problems.append(f"splits must be a non-empty list of field names, got {splits!r}")
            elif len(set(splits)) < len(splits):
                problems.append("splits names must be distinct")
        return problems

    @staticmethod
    def _required(where, row, field, null_ok=False):
        """Return the row's value of a field it must carry, refusing by row otherwise."""
        if field not in row or (row[field] is None and not null_ok):
            raise ValueError(f"{where} lacks the field {field!r}")
        return row[field]

    @staticmethod
    def _usable(spec, row):
        """``((signal, outcome, cost), None)`` for a usable row, else ``(None, the field that failed)``."""
        values = []
        for key in ("signal_field", "outcome_field", "cost_field"):
            field = spec.get(key)
            if field is None:
                values.append(None)
                continue
            value = row.get(field)
            if not number_ok(value):
                return None, field
            values.append(float(value))
        return tuple(values), None

    def _check_outcome_after(self, where, row, stamp):
        """Refuse a row whose outcome did not start strictly after its stamp (``outcome_after_field``)."""
        field = self.params.get("outcome_after_field")
        if field is None:
            return
        start = self._required(where, row, field)
        try:
            later = start > stamp
        except TypeError:
            later = False
        if not later:
            raise ValueError(f"{where}: outcome_after_field {field!r} = {start!r} is not strictly after "
                             f"the stamp {stamp!r}: the outcome would overlap the signal")

    def _collect(self, rows):
        """Read the stream once: per signal its usable columns, labels and drop census."""
        p = self.params
        splits = list(p.get("splits") or [])
        if not isinstance(rows, (list, tuple)) or not rows:
            raise ValueError(f"{self.key}: no records to sort")
        panels = {name: {"stamps": [], "names": [], "sig": [], "out": [], "cost": [],
                         "labels": [], "dropped": {}} for name in p["signals"]}
        seen = set()
        for index, row in enumerate(rows):
            where = f"{self.key}: row {index}"
            if not isinstance(row, Mapping):
                raise ValueError(f"{where} must be a mapping, got {type(row).__name__}")
            stamp = self._required(where, row, p["stamp_field"])
            name = self._required(where, row, p["series_field"])
            self._check_outcome_after(where, row, stamp)
            try:
                repeated = (stamp, name) in seen
                seen.add((stamp, name))
            except TypeError:
                raise ValueError(f"{where}: the stamp and the name must be hashable, got "
                                 f"{stamp!r} and {name!r}") from None
            if repeated:
                raise ValueError(f"{where} repeats {p['series_field']}={name!r} at "
                                 f"{p['stamp_field']}={stamp!r}: a name counts once per stamp")
            labels = [(field, self._required(where, row, field, null_ok=True)) for field in splits]
            for signal, spec in p["signals"].items():
                values, failed = self._usable(spec, row)
                panel = panels[signal]
                if values is None:
                    panel["dropped"][failed] = panel["dropped"].get(failed, 0) + 1
                    continue
                panel["stamps"].append(stamp)
                panel["names"].append(name)
                panel["sig"].append(values[0])
                panel["out"].append(values[1])
                panel["cost"].append(values[2])
                panel["labels"].append(labels)
        return len(rows), panels

    @staticmethod
    def _mean(values):
        """The exactly rounded mean, whatever the input order."""
        return math.fsum(values) / len(values)

    def _book(self, signal, panel, positions):
        """The scored stamps of one signal over ``positions``: ``(records, thin, empty)``."""
        p, spec = self.params, self.params["signals"][signal]
        n_buckets, sides = int(p["quantiles"]), int(p.get("sides_per_leg", 2))
        sign, flat = spec.get("sign", 1), float(p.get("cost_per_side") or 0.0)
        long_at, short_at = (n_buckets - 1, 0) if sign > 0 else (0, n_buckets - 1)
        by_stamp = {}
        for position in positions:
            by_stamp.setdefault(panel["stamps"][position], []).append(position)
        try:
            stamps = sorted(by_stamp)
        except TypeError as exc:
            raise ValueError(f"{self.key}: the stamps of {signal!r} cannot be ordered: {exc}") from exc
        records, thin, empty = [], 0, 0
        for stamp in stamps:
            members = by_stamp[stamp]
            if len(members) < p["min_names"]:
                thin += 1
                continue
            edges = quantile_edges([panel["sig"][i] for i in members], n_buckets)
            buckets = [[] for _ in range(n_buckets)]
            for i in members:
                buckets[quantile_bin(panel["sig"][i], edges)].append(i)
            if not buckets[long_at] or not buckets[short_at]:
                empty += 1
                continue
            means = [self._mean([panel["out"][i] for i in b]) if b else None for b in buckets]
            leg_cost = [sides * (self._mean([panel["cost"][i] for i in buckets[at]])
                                 if spec.get("cost_field") else flat) for at in (long_at, short_at)]
            gross, cost = means[long_at] - means[short_at], leg_cost[0] + leg_cost[1]
            records.append({"signal": signal, "stamp": stamp, "n_names": len(members),
                            "quantile_means": means, "gross": gross, "cost": cost,
                            "net": gross - cost})
        return records, thin, empty

    def _series(self, values):
        """The one-sided Newey–West statistics of a stamp-ordered series, with the HAC guard."""
        return series_statistics(values, self.params.get("lags"), self.params.get("h_steps", 1))

    def _interval(self, stamps, values):
        """The block-bootstrap percentile interval of the mean of ``values`` (one per stamp)."""
        return block_bootstrap_interval(stamps, values, self.params["bootstrap"])

    def _signal_summary(self, signal, panel):
        """One signal's records and its summary block."""
        spec = self.params["signals"][signal]
        records, thin, empty = self._book(signal, panel, range(len(panel["sig"])))
        net = [r["net"] for r in records]
        columns = [r["quantile_means"] for r in records]
        block = {
            "signal_field": spec["signal_field"], "outcome_field": spec["outcome_field"],
            "sign": spec.get("sign", 1), "rows_scored": len(panel["sig"]),
            "dropped": panel["dropped"], "n_stamps": len(records), "thin_stamps": thin,
            "empty_bucket_stamps": empty,
            "quantile_means": [self._mean(v) if v else None for v in (
                [col[b] for col in columns if col[b] is not None]
                for b in range(int(self.params["quantiles"])))],
            "gross": self._series([r["gross"] for r in records]), "net": self._series(net),
        }
        if "bootstrap" in self.params:
            block["ci"] = self._interval([r["stamp"] for r in records], net)
        return records, block

    def _split_summaries(self, signal, panel):
        """The series statistics of one signal per split label."""
        groups = {}
        for position, labels in enumerate(panel["labels"]):
            for field, value in labels:
                groups.setdefault((field, value), []).append(position)
        out = {}
        for (field, value), positions in sorted(groups.items(), key=lambda kv: (kv[0][0], str(kv[0][1]))):
            records, thin, empty = self._book(signal, panel, positions)
            out[f"{signal}|{field}={value}"] = {
                **self._series([r["net"] for r in records]), "n_stamps": len(records),
                "thin_stamps": thin, "empty_bucket_stamps": empty, "rows_scored": len(positions)}
        return out

    def _family(self, signals):
        """The corrections over the signals' net p-values; a signal without one stays in at p = 1."""
        return family_decisions(
            {name: (None if block["net"]["t"] is None or block["net"]["p_value"] is None
                    else block["net"]["p_value"]) for name, block in signals.items()},
            self.params["alpha"], self.params.get("corrections"))

    def run(self, ctx, inputs):
        """Sort every signal's cross-sections, then test the spread series and the family.

        Parameters
        ----------
        ctx : NodeContext
            The run frame; unused.
        inputs : dict
            ``records`` (REQUIRED, non-empty): mapping rows carrying the stamp,
            series and signal fields.

        Returns
        -------
        dict
            ``summary`` and ``records`` as the class docstring states.

        Raises
        ------
        ValueError
            No records; a row that is not a mapping, lacks the stamp, series
            or a split field, or repeats a (stamp, name); stamps that cannot
            be ordered, or are not ISO dates under ``bootstrap``.
        """
        p = self.params
        rows_in, panels = self._collect(inputs["records"])
        signals, splits, records = {}, {}, []
        for name, panel in panels.items():
            scored, signals[name] = self._signal_summary(name, panel)
            records.extend(scored)
            if p.get("splits"):
                splits.update(self._split_summaries(name, panel))
        summary = {
            "stage": f"per-{p['stamp_field']} {p['quantiles']}-quantile long-short across "
                     f"{p['series_field']} — " + "; ".join(
                         f"{n}: {s['signal_field']} vs {s['outcome_field']}" for n, s in p["signals"].items()),
            "rows_in": rows_in, "quantiles": p["quantiles"], "signals": signals,
            "family": self._family(signals), "splits": splits,
        }
        self.log.info("%s: %d row(s) in, %d signal(s), %d scored stamp record(s)",
                      self.key, rows_in, len(signals), len(records))
        return {"summary": summary, "records": records}


class ClusteredCalibrationSlope(Node):
    """Calibration slope of an outcome on a forecast, cluster-robust, per group — by import path.

    Role ``transform``. ``uses: "dskit.pipeline.kinds_stats:ClusteredCalibrationSlope"``;
    never registered. The number is
    :func:`~dskit.pipeline.ordering.clustered_calibration_slope` over a
    record stream: does the forecast's SIZE carry over into the outcome
    (``b ≈ 1``), at the wrong scale (``0 < b < 1``), or not at all (band
    covers 0)? Rows that are not a time series — books, trades, names —
    share shocks within one or two ``cluster_fields`` (a date, a name);
    the band is one- or two-way cluster-robust with Student-t on the
    fewest clusters minus one.

    Every row must carry a finite ``forecast_field`` and
    ``outcome_field`` and a non-empty string in each cluster field and
    group field; anything else REFUSES by row index (filter upstream —
    a slope over the rows that happened to be well formed is a different
    number from the one the document declared). A group too small to
    estimate (fewer than three rows, a constant forecast, a cluster
    dimension with one cluster) is REPORTED with null statistics and
    its reason in ``skipped``, so a thin cell is visible rather than
    silently absent or fatal to its siblings.

    Outputs
    -------
    ``records``
        One row per group, sorted by the group values: the group fields,
        ``n``, ``slope``, ``intercept``, ``pearson_r``, ``slope_se``,
        ``variance_rule``, ``se_<field>`` and ``n_clusters_<field>`` per
        cluster field, ``n_clusters_intersection``, ``df``, ``level``,
        ``ci_low``, ``ci_high``, ``t_vs_0``, ``t_vs_1``, ``p_vs_0``,
        ``forecast_sum``, ``outcome_sum`` and ``skipped`` (``None`` when
        estimated).
    ``metrics``
        ``rows_in``, ``groups``, ``skipped``.

    Parameters
    ----------
    params : dict
        ``forecast_field``, ``outcome_field`` (non-empty str, REQUIRED);
        ``cluster_fields`` (list of one or two distinct non-empty str,
        REQUIRED); ``group_fields`` (list of distinct non-empty str, none
        of them a cluster field; default ``[]``, one group);
        ``level`` (number in (0, 1); default
        :data:`~dskit.pipeline.mean_interval.DEFAULT_LEVEL`).

    Examples
    --------
    Realized book P&L on the optimizer's expected profit, clustered by
    decision date and symbol, one row per cohort::

        node = ClusteredCalibrationSlope("g1", {
            "forecast_field": "expected", "outcome_field": "pnl",
            "cluster_fields": ["decision_date", "symbol"],
            "group_fields": ["cohort"]})
        out = node.run(ctx, {"records": books})
        out["records"][0]["ci_low"]
    """

    role = "transform"
    outputs = ("records", "metrics")
    _PARAMS = ("forecast_field", "outcome_field", "cluster_fields", "group_fields", "level")
    _REQUIRED = ("forecast_field", "outcome_field", "cluster_fields")

    @staticmethod
    def _field_list_problems(name, value, minimum, maximum=None):
        """Problems with a list of distinct non-empty field names."""
        if (not isinstance(value, list) or len(value) < minimum
                or (maximum is not None and len(value) > maximum)
                or any(not isinstance(f, str) or not f for f in value)
                or len(set(value)) != len(value)):
            span = f"{minimum}..{maximum}" if maximum is not None else f"{minimum} or more"
            return [f"{name} must be a list of {span} distinct non-empty field names, got {value!r}"]
        return []

    @classmethod
    def validate_params(cls, params):
        """Default-deny: refuse unknown, missing and malformed knobs.

        Parameters
        ----------
        params : dict
            The node's ``params`` block.

        Returns
        -------
        list of str
            One message per problem.
        """
        problems = []
        _reject_unknown(problems, params, cls._PARAMS)
        for name in cls._REQUIRED:
            if name not in params:
                problems.append(f"{name} is required")
        for name in ("forecast_field", "outcome_field"):
            if name in params and (not isinstance(params[name], str) or not params[name]):
                problems.append(f"{name} must be a non-empty string")
        if "cluster_fields" in params:
            problems += cls._field_list_problems("cluster_fields", params["cluster_fields"], 1, 2)
        if "group_fields" in params:
            groups = params["group_fields"]
            problems += cls._field_list_problems("group_fields", groups, 0)
            clusters = params.get("cluster_fields")
            if isinstance(groups, list) and isinstance(clusters, list) and set(groups) & set(
                    c for c in clusters if isinstance(c, str)):
                problems.append("group_fields may not repeat a cluster field — a group "
                                "holding one cluster has no cluster band")
        if "level" in params:
            level = params["level"]
            if isinstance(level, bool) or not number_ok(level) or not 0 < level < 1:
                problems.append(f"level must be a number in (0, 1), got {level!r}")
        return problems

    def validate_inputs(self, inputs):
        """Problems with the wired ``records`` (a materialized list)."""
        records = inputs.get("records")
        if not isinstance(records, (list, tuple)):
            return [f"records must be a list of rows, got {type(records).__name__}"]
        return []

    def _cells(self, index, row):
        """``(group key, forecast, outcome, cluster labels)`` of one row, refusing by index."""
        p = self.params
        where = f"{self.key}: row {index}"
        try:
            forecast, outcome = _field(row, p["forecast_field"]), _field(row, p["outcome_field"])
            labels = [_field(row, f) for f in p["cluster_fields"]]
            group = tuple(_field(row, f) for f in p.get("group_fields", []))
        except ValueError as exc:
            raise ValueError(f"{where}: {exc}") from None
        for name, value in ((p["forecast_field"], forecast), (p["outcome_field"], outcome)):
            if isinstance(value, bool) or not number_ok(value):
                raise ValueError(f"{where}: {name} {value!r} is not a finite number")
        for name, value in zip([*p["cluster_fields"], *p.get("group_fields", [])], [*labels, *group]):
            if not cluster_ok(value):
                raise ValueError(f"{where}: {name} {value!r} must be a non-empty string")
        return group, float(forecast), float(outcome), labels

    def run(self, ctx, inputs):
        """Group the rows and fit each group's clustered calibration slope.

        Parameters
        ----------
        ctx : NodeContext
            Unused.
        inputs : dict
            ``records``: a list of mapping (or attribute-bearing) rows.

        Returns
        -------
        dict
            ``records`` and ``metrics`` as the class docstring describes.

        Raises
        ------
        ValueError
            On a row missing a field or carrying a bad cell, named by index.
        """
        p = self.params
        clusters = p["cluster_fields"]
        groups = {}
        for index, row in enumerate(inputs["records"]):
            group, forecast, outcome, labels = self._cells(index, row)
            groups.setdefault(group, []).append((forecast, outcome, labels))
        out, skipped = [], 0
        for group in sorted(groups):
            cells = groups[group]
            row = dict(zip(p.get("group_fields", []), group))
            forecasts = [c[0] for c in cells]
            outcomes = [c[1] for c in cells]
            row.update(n=len(cells), forecast_sum=math.fsum(forecasts), outcome_sum=math.fsum(outcomes))
            try:
                fit = clustered_calibration_slope(
                    outcomes, forecasts, [[c[2][k] for c in cells] for k in range(len(clusters))],
                    level=p.get("level"))
            except ValueError as exc:
                skipped += 1
                fit = None
                row["skipped"] = str(exc)
            row.update(self._fit_fields(fit, clusters))
            if fit is not None:
                row["skipped"] = None
            out.append(row)
        return {"records": out,
                "metrics": {"rows_in": len(inputs["records"]), "groups": len(out), "skipped": skipped}}

    @staticmethod
    def _fit_fields(fit, clusters):
        """The flat statistic fields of one group's fit (all None when skipped)."""
        names = ("slope", "intercept", "pearson_r", "slope_se", "variance_rule",
                 "n_clusters_intersection", "df", "level", "ci_low", "ci_high",
                 "t_vs_0", "t_vs_1", "p_vs_0")
        fields = {k: (None if fit is None else fit[k]) for k in names}
        for k, name in enumerate(clusters):
            fields[f"se_{name}"] = None if fit is None else fit["se_by_dimension"][k]
            fields[f"n_clusters_{name}"] = None if fit is None else fit["n_clusters"][k]
        return fields


def register(registry=None) -> None:
    """Claim the toolkit-owned kind names — ``stat_test`` ->
    :class:`StatTest` and ``validate`` -> :class:`Validate`, both
    ``owned=True`` — in ``registry`` (default
    :data:`~dskit.pipeline.node.DEFAULT_NODE_KINDS`). Idempotent: a
    name already present is SKIPPED, never shadowed (the registry raises
    on duplicates by design; deliberate re-registration stays deliberate).
    Called by the orchestrator, never at import time.
    """
    registry = DEFAULT_NODE_KINDS if registry is None else registry
    for name, cls in (("stat_test", StatTest), ("validate", Validate)):
        if name not in registry:
            registry.register(name, cls, owned=True)
