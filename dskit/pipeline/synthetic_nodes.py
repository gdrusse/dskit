"""The synthetic Node set — the runner is built and proven against THESE.

Spec §10 step 2: the execution file is developed "against a synthetic
Node set only" — no adapter wrappers, no venue machinery. This module is
that set: one small, deterministic Node per role, exercising every seam
the planner and driver own (wiring, params, split reading, banking
counts, the deploy gate, capital behind the stat test, ``$prev``
carries, artifacts).

Determinism discipline (the ``testing.py`` recipe): every "random"
quantity is a pure sha256 hash of ``(seed, tags...)`` — byte-identical
across machines and runs, no RNG state. The synthetic market carries a
deliberate exploitable mispricing: the quoted ``mid`` under-reacts to
the truth by ``SHRINK``, so a trained signal genuinely beats the market
baseline and the family has both a deploy path and (with ``alpha`` low
or data thin) an honest NO-GO path.

The stat test here is a real one-sided exact sign test per instrument —
but with NO family correction and none of the doctrine machinery; the
toolkit-owned ``stat_test`` kind (spec §10 step 3) replaces it for real
documents. It registers under that owned name only inside
:func:`register_synthetic_nodes` registries, which are private/demo by
construction.

Import cost: stdlib only.
"""

from __future__ import annotations

import hashlib
import math
from types import SimpleNamespace

from dskit.pipeline.node import Node, TrainableNode, check_int_param
from dskit.pipeline.split_policy import SPLIT_NAMES

__all__ = [
    "DEMO_SPLITS",
    "SHRINK",
    "SynthBank",
    "SynthCapital",
    "SynthClip",
    "SynthEligibility",
    "SynthEvents",
    "SynthLabels",
    "SynthMarketSignal",
    "SynthReport",
    "SynthScore",
    "SynthSearch",
    "SynthStatTest",
    "SynthTrain",
    "demo_document",
    "demo_pipeline",
    "demo_registry",
    "register_synthetic_nodes",
]

#: How much the synthetic market under-reacts to the truth: quoted
#: ``mid = 0.5 + SHRINK * (p_true - 0.5)``. The exploitable edge — set
#: strong enough that a per-instrument sign test over a handful of val
#: clusters detects it decisively (the deploy path must be reachable in
#: small deterministic fixtures, not just asymptotically).
SHRINK = 0.25

#: How much of the remaining gap a trained signal recovers.
LEARN = 0.8

_DAY_MS = 24 * 60 * 60 * 1000


def _u(seed, *tags) -> float:
    """Deterministic uniform in [0, 1): sha256 of ``(seed, tags)``."""
    digest = hashlib.sha256(":".join([str(seed), *map(str, tags)]).encode()).digest()
    return int.from_bytes(digest[:8], "big") / 2**64


#: One definition, in ``node.py`` beside the ``validate_params``
#: protocol it serves.
_check_int = check_int_param


def _split_of(splits, event):
    """The split of one event dict under the materialized ``splits`` —
    ``None`` when no splits section exists (everything is in-sample)."""
    if splits is None:
        return None
    return splits.split_of(
        SimpleNamespace(asof_ms=event["asof_ms"], cluster=event["cluster"])
    )


def _in_split(splits, event, want) -> bool:
    """Whether ``event`` belongs to the ``want`` split; with no splits
    section every event belongs to every split (the degenerate case)."""
    return splits is None or _split_of(splits, event) == want


class SynthEvents(Node):
    """The synthetic market: deterministic settled events with a quoted
    mid that under-reacts to the truth (role ``data`` — no inputs)."""

    role = "data"
    outputs = ("events", "instruments", "newest_ms")

    @classmethod
    def validate_params(cls, params):
        problems = []
        _check_int(problems, "n_events", params.get("n_events", 60), ge=1)
        _check_int(problems, "n_instruments", params.get("n_instruments", 3), ge=1)
        _check_int(problems, "seed", params.get("seed", 0), ge=0)
        if params.get("n_instruments", 3) > 26:
            problems.append("n_instruments must be <= 26 (single-letter tickers)")
        return problems

    def fingerprint(self):
        return {
            "kind": "synth-events",
            "n_events": self.params.get("n_events", 60),
            "n_instruments": self.params.get("n_instruments", 3),
            "seed": self.params.get("seed", 0),
        }

    def run(self, ctx, inputs):
        n = self.params.get("n_events", 60)
        m = self.params.get("n_instruments", 3)
        seed = self.params.get("seed", 0)
        start = self.params.get("start_ms", 1_000 * _DAY_MS)
        spacing = self.params.get("spacing_ms", _DAY_MS)
        instruments = [f"SYN{chr(ord('A') + j)}" for j in range(m)]
        events = []
        for inst in instruments:
            for i in range(n):
                p_true = 0.1 + 0.8 * _u(seed, "p", inst, i)
                events.append(
                    {
                        "instrument": inst,
                        "contract": f"{inst}-{i:04d}",
                        "cluster": f"{inst}:c{i // 24}",
                        "asof_ms": start + i * spacing,
                        "p_true": p_true,
                        "mid": 0.5 + SHRINK * (p_true - 0.5),
                        "settled_yes": _u(seed, "y", inst, i) < p_true,
                    }
                )
        self.log.info("generated %d events across %d instruments", len(events), m)
        return {
            "events": events,
            "instruments": instruments,
            "newest_ms": max(e["asof_ms"] for e in events),
        }


class SynthLabels(Node):
    """Settled outcomes for the synthetic events (role ``labels``)."""

    role = "labels"
    outputs = ("outcomes",)

    def validate_inputs(self, inputs):
        if not isinstance(inputs.get("events"), list):
            return [
                f"events must be a list of event dicts, got {inputs.get('events')!r}"
            ]
        return []

    def run(self, ctx, inputs):
        return {"outcomes": {e["contract"]: e["settled_yes"] for e in inputs["events"]}}


class SynthClip(Node):
    """Keep events whose mid lies inside ``(lo, hi)`` (role ``transform``)
    — the pointwise stream-in/stream-out shape."""

    role = "transform"
    outputs = ("events",)

    @classmethod
    def serving_effect(cls, params, verified_run_evidence):
        """Classify the kind for serving: ``"pure"`` — a clip reads each event's own mid and its two bounds (ADR-0091).

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

    @classmethod
    def validate_params(cls, params):
        lo, hi = params.get("lo", 0.03), params.get("hi", 0.97)
        for name, v in (("lo", lo), ("hi", hi)):
            if isinstance(v, bool) or not isinstance(v, (int, float)):
                return [f"{name} must be a number, got {v!r}"]
        if not lo < hi:
            return [f"lo must be < hi, got {lo!r} >= {hi!r}"]
        return []

    def run(self, ctx, inputs):
        lo, hi = self.params.get("lo", 0.03), self.params.get("hi", 0.97)
        kept = [e for e in inputs["events"] if lo < e["mid"] < hi]
        self.log.info("clip kept %d/%d events", len(kept), len(inputs["events"]))
        return {"events": kept}


class SynthBank(Node):
    """★BANKING counter: settled events per instrument (role ``accrual``)."""

    role = "accrual"
    outputs = ("counts",)

    @classmethod
    def serving_effect(cls, params, verified_run_evidence):
        """Classify the kind for serving: ``"pure"`` — a count reads only the events wired in (ADR-0091).

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
        counts = {}
        for e in inputs["events"]:
            counts[e["instrument"]] = counts.get(e["instrument"], 0) + 1
        return {"counts": counts}


class SynthEligibility(Node):
    """The admission bar: instruments whose banked count clears
    ``min_events`` (role ``gate``). An empty family is a NO-GO — the
    verdict the driver halts descendants on."""

    role = "gate"
    outputs = ("instruments", "verdict")

    @classmethod
    def serving_effect(cls, params, verified_run_evidence):
        """Classify the kind for serving: ``"pure"`` — the bar is arithmetic on the wired counts (ADR-0091).

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

    @classmethod
    def validate_params(cls, params):
        problems = []
        _check_int(problems, "min_events", params.get("min_events", 1), ge=1)
        return problems

    def run(self, ctx, inputs):
        bar = self.params.get("min_events", 1)
        family = sorted(k for k, n in inputs["counts"].items() if n >= bar)
        verdict = "GO" if family else "NO-GO"
        self.log.info(
            "eligibility: %d instrument(s) >= %d — %s", len(family), bar, verdict
        )
        return {"instruments": family, "verdict": verdict}


class SynthMarketSignal(Node):
    """The market's own price as the belief — the null every model must
    beat (role ``signal``)."""

    role = "signal"
    outputs = ("signal",)

    @classmethod
    def serving_effect(cls, params, verified_run_evidence):
        """Classify the kind for serving: ``"pure"`` — the market's own mid is read straight off each event (ADR-0091).

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
        return {"signal": {e["contract"]: e["mid"] for e in inputs["events"]}}


class SynthTrain(TrainableNode):
    """A trainable signal: recovers ``LEARN`` of the market's under-
    reaction, fit on the train split only (role ``train``). Honors
    ``mode``: ``train`` fits and writes the artifact; ``load`` reads a
    pinned one and never refits."""

    role = "train"
    outputs = ("signal", "artifact")

    #: The model artifact's name — written by both modes, read back under
    #: load. One name, so the writer and the reader cannot drift apart.
    _MODEL_FILE = "model.json"

    @classmethod
    def validate_params(cls, params):
        problems = []
        _check_int(problems, "min_train", params.get("min_train", 1), ge=1)
        return problems

    def run_train(self, ctx, inputs):
        train_events = [
            e for e in inputs["events"] if _in_split(ctx.splits, e, "train")
        ]
        floor = self.params.get("min_train", 1)
        if len(train_events) < floor:
            raise ValueError(
                f"min_train={floor} but only {len(train_events)} training "
                "event(s) in the train split"
            )
        return self._served(ctx, inputs, {"learn": LEARN, "n_train": len(train_events)})

    def run_load(self, ctx, inputs):
        """Restore the pinned model through the base's read service; never refit.

        Parameters
        ----------
        ctx : NodeContext
            The run frame. Under a release reader the model text is the
            reader's; otherwise the pinned ``artifact`` is read.
        inputs : dict
            ``events``, the stream to price.

        Returns
        -------
        dict
            ``signal`` and ``artifact``, exactly as a fit answers.
        """
        model = self.read_artifact(ctx, self._MODEL_FILE)
        self.log.info("loaded pinned artifact %s", self.artifact)
        return self._served(ctx, inputs, model)

    def _served(self, ctx, inputs, model):
        """The tail both modes share: persist the model, then price every
        event with it. Fitted or restored, a run answers the same way."""
        path = self.write_artifact(ctx, self._MODEL_FILE, model)
        signal = {
            e["contract"]: e["mid"] + model["learn"] * (e["p_true"] - e["mid"])
            for e in inputs["events"]
        }
        return {"signal": signal, "artifact": path}


class SynthScore(Node):
    """Brier scoring on the declared split, model vs baseline when a
    baseline is wired (role ``score``). ``cluster_scores`` carries the
    per-instrument, per-cluster improvement the stat test consumes."""

    role = "score"
    outputs = ("metrics", "cluster_scores")

    @classmethod
    def validate_params(cls, params):
        problems = []
        split = params.get("split")
        if split not in SPLIT_NAMES:
            problems.append(
                f"split must declare which split this node reads "
                f"({'/'.join(repr(s) for s in SPLIT_NAMES)}), got {split!r}"
            )
        _check_int(problems, "min_events", params.get("min_events", 1), ge=1)
        return problems

    def validate_inputs(self, inputs):
        problems = []
        for port in ("signal", "outcomes"):
            if not isinstance(inputs.get(port), dict):
                problems.append(f"{port} must be a dict, got {inputs.get(port)!r}")
        if not isinstance(inputs.get("events"), list):
            problems.append(f"events must be a list, got {inputs.get('events')!r}")
        return problems

    def run(self, ctx, inputs):
        split = self.params["split"]
        signal, outcomes = inputs["signal"], inputs["outcomes"]
        baseline = inputs.get("baseline")
        per_cluster = {}
        losses = []
        for e in inputs["events"]:
            c = e["contract"]
            if c not in signal or c not in outcomes:
                continue
            if not _in_split(ctx.splits, e, split):
                continue
            y = 1.0 if outcomes[c] else 0.0
            loss = (signal[c] - y) ** 2
            losses.append(loss)
            edge = (baseline[c] - y) ** 2 - loss if baseline is not None else -loss
            bucket = per_cluster.setdefault(e["instrument"], {})
            bucket.setdefault(e["cluster"], []).append(edge)
        n = len(losses)
        if n < self.params.get("min_events", 1):
            raise ValueError(
                f"only {n} scoreable event(s) in the {split!r} split — below "
                f"min_events={self.params.get('min_events', 1)}"
            )
        cluster_scores = {
            inst: {c: sum(v) / len(v) for c, v in clusters.items()}
            for inst, clusters in per_cluster.items()
        }
        metrics = {"loss": sum(losses) / n, "n": n}
        if ctx.tracker is not None:
            ctx.tracker.log_metrics(self.key, metrics)
        return {"metrics": metrics, "cluster_scores": cluster_scores}


class SynthStatTest(Node):
    """Per-instrument one-sided exact sign test on the cluster
    improvements (role ``stat_test``). Real arithmetic, NO family
    correction — the toolkit-owned kind replaces this for real documents
    (spec §10 step 3); it registers under the owned name only inside
    private/demo registries."""

    role = "stat_test"
    outputs = ("survivors", "pvalues", "verdict")

    @classmethod
    def validate_params(cls, params):
        alpha = params.get("alpha", 0.05)
        if (
            isinstance(alpha, bool)
            or not isinstance(alpha, (int, float))
            or not 0 < alpha < 1
        ):
            return [f"alpha must be a number in (0, 1), got {alpha!r}"]
        return []

    def run(self, ctx, inputs):
        alpha = self.params.get("alpha", 0.05)
        pvalues = {}
        for inst, clusters in inputs["scores"].items():
            diffs = [d for d in clusters.values() if d != 0.0]
            n, k = len(diffs), sum(1 for d in diffs if d > 0)
            # One-sided exact sign test: P(X >= k), X ~ Binomial(n, 1/2).
            p = sum(math.comb(n, j) for j in range(k, n + 1)) / 2**n if n else 1.0
            pvalues[inst] = p
        survivors = sorted(inst for inst, p in pvalues.items() if p <= alpha)
        verdict = "GO" if survivors else "NO-GO"
        self.log.info(
            "stat test: %d/%d instrument(s) survive at alpha=%s — %s",
            len(survivors),
            len(pvalues),
            alpha,
            verdict,
        )
        return {"survivors": survivors, "pvalues": pvalues, "verdict": verdict}


class SynthCapital(Node):
    """Deterministic sizing on gate survivors only (role ``capital``) —
    the node the ``capital ⇐ stat_test`` DAG rule protects. Emits
    ``final_bankroll`` for the run-over-run ``$prev`` carry."""

    role = "capital"
    outputs = ("positions", "final_bankroll")

    @classmethod
    def validate_params(cls, params):
        bankroll = params.get("bankroll", 1000.0)
        if (
            isinstance(bankroll, bool)
            or not isinstance(bankroll, (int, float))
            or bankroll <= 0
        ):
            return [f"bankroll must be a number > 0, got {bankroll!r}"]
        return []

    def run(self, ctx, inputs):
        bankroll = float(self.params.get("bankroll", 1000.0))
        frac = float(self.params.get("stake_frac", 0.1))
        survivors = set(inputs["survivors"])
        picks = sorted(
            c
            for c, q in inputs["signal"].items()
            if c.split("-")[0] in survivors and abs(q - 0.5) > 0.1
        )
        stake = round(bankroll * frac / len(picks), 6) if picks else 0.0
        positions = {c: stake for c in picks}
        final = round(bankroll * (1.0 + 0.01 * len(survivors)), 6)
        return {"positions": positions, "final_bankroll": final}


class SynthReport(Node):
    """Artifact-only sink (role ``report``): writes whatever was wired in
    as a JSON artifact and returns its path."""

    role = "report"
    outputs = ("path",)

    def run(self, ctx, inputs):
        summary = {
            port: value if isinstance(value, (int, float, str, bool)) else repr(value)
            for port, value in sorted(inputs.items())
        }
        path = self.write_artifact(ctx, "report.json", summary)
        return {"path": path}


class SynthSearch(Node):
    """Placeholder search driver (role ``search``): validates like the
    real ``hpo-grid`` and returns a canned selection. The subgraph
    re-execution semantics (spec §8) land with the toolkit-owned kinds
    (spec §10 step 3) — until then a search node plans (its wiring rules
    hold) and executes as a no-op selection."""

    role = "search"
    outputs = ("best_params", "best_score", "trials")

    @classmethod
    def validate_params(cls, params):
        problems = []
        if not isinstance(params.get("space"), dict) or not params.get("space"):
            problems.append(
                f"space must be a non-empty dict of 'node.param.path' -> grid, "
                f"got {params.get('space')!r}"
            )
        if "objective" not in params:
            problems.append("objective is required (a $-reference to a score output)")
        if params.get("select", "min") not in ("min", "max"):
            problems.append(
                f"select must be 'min' or 'max', got {params.get('select')!r}"
            )
        return problems

    def run(self, ctx, inputs):
        return {
            "best_params": {},
            "best_score": self.params.get("objective"),
            "trials": [],
        }


def demo_registry():
    """A fresh private registry carrying the whole synthetic set."""
    from dskit.pipeline.node import NodeKindRegistry

    registry = NodeKindRegistry()
    register_synthetic_nodes(registry)
    return registry


def demo_pipeline() -> dict:
    """The full-quant node map against the synthetic set — the shape of
    ``PROPOSAL-node-map.jsonc`` end to end (data -> labels -> bank ->
    eligibility -> transform -> signals -> score -> stat_test -> capital
    -> report), with the seed and cuts pinned where the planted edge
    decisively survives. Fresh specs each call; safe to mutate."""
    from dskit.pipeline.document import NodeSpec

    return {
        "events": NodeSpec(
            uses="synth-events",
            params={"n_events": 432, "n_instruments": 2, "seed": 4},
        ),
        "labels": NodeSpec(uses="synth-labels", inputs={"events": "$events.events"}),
        "bank": NodeSpec(uses="synth-bank", inputs={"events": "$events.events"}),
        "family": NodeSpec(
            uses="synth-eligibility",
            inputs={"counts": "$bank.counts"},
            params={"min_events": 10},
        ),
        "clip": NodeSpec(
            uses="synth-clip",
            inputs={"events": "$events.events"},
            params={"lo": 0.02, "hi": 0.98},
        ),
        "market": NodeSpec(uses="synth-market", inputs={"events": "$clip.events"}),
        "qhat": NodeSpec(
            uses="synth-train",
            mode="train",
            inputs={"events": "$clip.events"},
            params={"min_train": 5},
        ),
        "validate": NodeSpec(
            uses="synth-score",
            inputs={
                "events": "$clip.events",
                "signal": "$qhat.signal",
                "baseline": "$market.signal",
                "outcomes": "$labels.outcomes",
            },
            params={"split": "val", "min_events": 10},
        ),
        "edge_test": NodeSpec(
            uses="stat_test",
            inputs={"scores": "$validate.cluster_scores"},
            params={"alpha": 0.05},
        ),
        "size": NodeSpec(
            uses="synth-capital",
            inputs={"signal": "$qhat.signal", "survivors": "$edge_test.survivors"},
            params={
                "bankroll": {"$prev": "size.final_bankroll", "default": 1000.0},
                "stake_frac": 0.1,
            },
        ),
        "report": NodeSpec(
            uses="synth-report",
            inputs={
                "family": "$family.instruments",
                "survivors": "$edge_test.survivors",
            },
        ),
    }


#: Cuts matching the 432-event market: 8 train clusters, 8 val clusters,
#: 2 test clusters per instrument.
DEMO_SPLITS = dict(
    train_end_ms=1191 * _DAY_MS, val_end_ms=1383 * _DAY_MS, test_end_ms=1440 * _DAY_MS
)


def demo_document(**overrides):
    """The demo banking document, ready to plan or run against
    :func:`demo_registry` (``python -m dskit.pipeline nodemap``).
    Keyword overrides replace whole document sections (tests use this to
    swap the pipeline, splits, outputs, tracking, ...)."""
    from dskit.pipeline.base import TimeSplitConfig
    from dskit.pipeline.document import PipelineDocument

    base = {
        "name": "synth-banking",
        "pipeline": demo_pipeline(),
        "splits": TimeSplitConfig(**DEMO_SPLITS),
    }
    base.update(overrides)
    return PipelineDocument(**base)


def register_synthetic_nodes(registry) -> None:
    """Register the whole set into a PRIVATE/demo registry — including
    :class:`SynthStatTest` under the owned name ``stat_test`` so the
    deploy-gate rules are exercisable before the real toolkit kinds
    exist. Never call this against :data:`~dskit.pipeline.node.
    DEFAULT_NODE_KINDS`; the real owned kinds claim these names there."""
    registry.register("synth-events", SynthEvents)
    registry.register("synth-labels", SynthLabels)
    registry.register("synth-clip", SynthClip)
    registry.register("synth-bank", SynthBank)
    registry.register("synth-eligibility", SynthEligibility)
    registry.register("synth-market", SynthMarketSignal)
    registry.register("synth-train", SynthTrain)
    registry.register("synth-score", SynthScore)
    registry.register("synth-capital", SynthCapital)
    registry.register("synth-report", SynthReport)
    registry.register("synth-search", SynthSearch)
    registry.register("stat_test", SynthStatTest, owned=True)
