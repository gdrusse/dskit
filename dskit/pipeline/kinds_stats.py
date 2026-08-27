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

Registration is deliberate, never an import side effect: the orchestrator
calls :func:`register` to claim the owned kind names in a registry
(idempotent — names already present are skipped); ``owned=True`` is what
the planner's ``stat_test`` doctrine check keys on.

Import cost: stdlib only.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from types import SimpleNamespace

from dskit.pipeline.metrics import METRICS
from dskit.pipeline.node import DEFAULT_NODE_KINDS, Node
from dskit.pipeline.stats import (
    CORRECTIONS,
    METHODS,
    cluster_bootstrap_pvalue,
    cluster_bootstrap_t,
)

__all__ = ["MIN_CLUSTERS", "StatTest", "Validate", "register"]

#: The bootstrap's honesty floor: below this many clusters an instrument's
#: resampling distribution is degenerate (one cluster resamples to itself
#: every draw), so a single lucky cluster would masquerade as maximal
#: significance. Such instruments degrade to ``p = 1.0`` — no evidence,
#: no crash — and real power comes from banking more events upstream.
MIN_CLUSTERS = 2


def _reject_unknown(problems, params, allowed):
    """Default-deny on this class's own knobs.

    The BASE ``validate_params`` accepts anything (a strict default is
    parked in I-222), but each toolkit kind knows its own knob names
    exactly — and a typo'd knob that is silently ignored is a config lie,
    so every kind closes the hole for itself. Shared by the sibling kind
    modules (``kinds_flow``, ``kinds_search``); the adapter keeps its own
    copy because the toolkit is never imported the other way around.

    Keys only: a VALUE that is a ``$``-reference string is legal wiring
    (``hpo-grid``'s ``objective`` is one by design) and is never touched
    here.
    """
    unknown = sorted(set(params) - set(allowed))
    if unknown:
        problems.append(f"unknown param(s) {unknown} — allowed: {sorted(allowed)}")


def _check_int(problems, name, value, *, ge):
    if isinstance(value, bool) or not isinstance(value, int) or value < ge:
        problems.append(f"{name} must be an int >= {ge}, got {value!r}")


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
        if split not in ("train", "val", "cal", "test"):
            problems.append(
                f"split must declare which split this node reads "
                f"('train'/'val'/'cal'/'test'), got {split!r}"
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
