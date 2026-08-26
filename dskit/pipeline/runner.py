"""The Runner — resolve, bind, then drive the config's declared stages.

Design rulings (2026-08-13 session, questions 3 + follow-ups): the config
DECLARES the stage list (``PipelineConfig.stages`` — explicit, ordered,
soundness-checked at construction so capital can never precede the stat
test); the Runner executes exactly that list, owning the deploy GATES and
the two genuinely venue-neutral stages; the backend owns the three whose
machinery already exists per venue. Nothing is reimplemented on either
side:

=========  ==========  ====================================================
Stage      Owner       What happens
=========  ==========  ====================================================
train      backend     fit the venue's SignalProvider (the adapter's
                       existing model machinery — never a second trainer)
validate   Runner      score signal vs. the backend's baseline on the
                       validation split, per instrument, with the config
                       metric; testability floor ``min_events`` (clusters)
stat_test  Runner      per-instrument cluster-bootstrap p-values + the
                       config's multiplicity correction across the family
                       — per-instrument decisions ALWAYS (no pooling path
                       exists)
optimize   backend     size the survivors (the adapter's existing sizer)
backtest   backend     replay the test window (the adapter's simulator)
=========  ==========  ====================================================

Deploy gates, in order: no testable instrument after validate -> halt; no
instrument surviving the corrected stat test -> halt before any capital
stage. A predict-only run is simply a stage list without capital stages.

Cross-stage wiring is DECLARED: any ``optimization.params`` value may be
``{"$from": "<stage>.<path>"}`` — resolved against the producing stage's
outputs immediately before each capital stage runs (so ``backtest`` may
reference ``optimize`` outputs), failing loudly on a dangling wire. The
resolved params reach the backend as ``ctx.optimizer_params``; backends
never re-read the raw config params, so the wiring in the document is the
wiring that runs.

The declared feature recipe transforms the record stream ONCE, in
:meth:`RunContext.records`, so generic and venue stages see the same
stream; causality at the signal seam is enforced structurally there (a
non-ascending ``asof_ms`` fails loudly), and every signal query uses
exactly the record's own instant.

Tracking: run-dir artifacts are always written (``stages/NN-<stage>.json``,
``result.json``, ``report.md`` — verdict first, house rule); declared
sinks additionally receive run params once and each stage's numeric
metrics, and are always closed, success or not.

Import cost: stdlib only.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field

from dskit.pipeline.base import (
    SINK_KINDS,
    import_ref,
    is_class_ref,
    parse_stage_entry,
    resolve_refs,
)
from dskit.pipeline.features import apply_stream_steps
from dskit.pipeline.metrics import METRICS
from dskit.pipeline.registry import DEFAULT_REGISTRY
from dskit.pipeline.resolve import pipeline_hash, resolve, write_run_dir
from dskit.pipeline.stats import CORRECTIONS, cluster_bootstrap_pvalue

__all__ = ["RunContext", "RunResult", "Runner", "StageResult"]

_MAX_MS = 2**63 - 1


@dataclass(frozen=True, slots=True)
class StageResult:
    """One stage's outcome: a one-line verdict + a JSON-ready payload."""

    stage: str
    ok: bool
    verdict: str
    payload: dict = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class RunResult:
    """The whole run: per-stage results, survivors, and where it halted.

    ``halted_after`` is ``None`` when every DECLARED stage ran, else the
    last executed stage — a HALT IS A RESULT (the deploy gate said no),
    not an error.
    """

    stages: tuple
    survivors: tuple
    halted_after: object
    run_dir: str
    pipeline_hash: str

    @property
    def verdict(self) -> str:
        """The run's one-line headline (the last stage's verdict)."""
        return self.stages[-1].verdict if self.stages else "nothing ran"


class RunContext:
    """What every stage sees: the resolved pipeline, the run dir, prior
    stage ``outputs``, resolved ``optimizer_params``, and a transformed,
    causality-guarded record stream."""

    def __init__(self, resolved, backend, run_dir):
        self.resolved = resolved
        self.backend = backend
        self.run_dir = run_dir
        #: stage name -> outputs dict (payload plus runtime objects like
        #: the trained signal); the $from resolution source.
        self.outputs = {}
        #: the CURRENT capital stage's optimization params, refs resolved.
        self.optimizer_params = None
        self._source = None
        self._secrets = None

    @property
    def config(self):
        return self.resolved.config

    @property
    def secrets(self):
        """Credential values per the config's ``env`` reference, loaded
        lazily into the redacting :class:`~dskit.pipeline.env.Secrets`
        façade — the ONLY sanctioned path from config to credentials
        (values never touch artifacts, hashes, or sinks)."""
        if self._secrets is None:
            from dskit.pipeline.env import load_env

            self._secrets = load_env(self.config.env)
        return self._secrets

    def records(self, instrument, split=None):
        """Stream one instrument's records — the declared feature recipe
        applied, ascending ``asof_ms`` enforced — optionally filtered to a
        split. This is the ONE stream every consumer reads."""
        if self._source is None:
            self._source = self.backend.data_source(self)
        stream = apply_stream_steps(
            self._source.records_for(instrument, 0, _MAX_MS), self.config.features
        )
        last = None
        for rec in stream:
            if last is not None and rec.asof_ms < last:
                raise ValueError(
                    f"data source yielded non-ascending asof_ms for "
                    f"{instrument!r} ({rec.asof_ms} after {last}) — causality "
                    "at the signal seam cannot be guaranteed over an unordered "
                    "stream"
                )
            last = rec.asof_ms
            if split is None or self.config.splits.split_of(rec) == split:
                yield rec


class Runner:
    """Drive one :class:`~dskit.pipeline.base.PipelineConfig` end to end.

    Parameters
    ----------
    config : PipelineConfig
    asof : str, optional
        Resolution date (pin it to reproduce; ``None`` = today UTC).
    backend : Backend, optional
        Injected backend (tests); ``None`` resolves via ``registry``.
    registry : BackendRegistry, optional
    """

    def __init__(self, config, asof=None, backend=None, registry=DEFAULT_REGISTRY):
        self._resolved, self._backend = resolve(
            config, asof=asof, backend=backend, registry=registry
        )

    @property
    def resolved(self):
        return self._resolved

    def run(self) -> RunResult:
        """Execute the config's declared stages in order, writing every
        artifact into the run dir. Returns the :class:`RunResult`; gate
        halts are results, stage exceptions propagate (fail loud)."""
        resolved = self._resolved
        run_dir = write_run_dir(resolved)
        os.makedirs(resolved.model_root, exist_ok=True)
        os.makedirs(os.path.join(run_dir, "stages"), exist_ok=True)
        ctx = RunContext(resolved, self._backend, run_dir)
        cfg = resolved.config
        trackers = self._open_trackers(cfg)
        stages, halted_after, survivors, signal = [], None, (), None
        try:
            for tracker in trackers:
                tracker.log_params(
                    {
                        "name": cfg.name,
                        "pipeline_hash": pipeline_hash(resolved),
                        "venue": cfg.data.venue,
                        "asof": resolved.asof,
                        "stages": ",".join(cfg.stages),
                    }
                )
            for entry in cfg.stages:
                stage, custom_ref = parse_stage_entry(entry)
                if custom_ref is not None:
                    payload = import_ref(custom_ref)(ctx)
                    if not isinstance(payload, dict):
                        raise ValueError(
                            f"custom stage {stage!r} ({custom_ref}) must return "
                            f"a JSON-ready payload dict, got "
                            f"{type(payload).__name__}"
                        )
                    result = StageResult(
                        stage=stage,
                        ok=True,
                        verdict=payload.get(
                            "verdict", f"ran custom stage {stage!r} ({custom_ref})"
                        ),
                        payload=payload,
                    )
                    ctx.outputs[stage] = dict(payload)
                elif stage == "train":
                    if is_class_ref(cfg.model.name):
                        # user-supplied model family: the imported object owns
                        # train/load; the result must still be a SignalProvider
                        family = import_ref(cfg.model.name)
                        if cfg.model.mode == "load":
                            signal = family.load(
                                ctx, cfg.model.params, cfg.model.artifact
                            )
                            verdict = (
                                f"loaded model {cfg.model.name!r} from artifact "
                                f"{cfg.model.artifact!r} (inference-only)"
                            )
                        else:
                            signal = family.train(ctx, cfg.model.params)
                            verdict = f"trained model {cfg.model.name!r}"
                        if not callable(getattr(signal, "q_hat", None)):
                            raise ValueError(
                                f"model family {cfg.model.name!r} returned "
                                f"{type(signal).__name__}, which does not "
                                "satisfy the SignalProvider seam (no q_hat)"
                            )
                    elif cfg.model.mode == "load":
                        signal = self._backend.load(ctx)
                        verdict = (
                            f"loaded model {cfg.model.name!r} from artifact "
                            f"{cfg.model.artifact!r} (inference-only)"
                        )
                    else:
                        signal = self._backend.train(ctx)
                        verdict = f"trained model {cfg.model.name!r}"
                    result = StageResult(
                        stage="train",
                        ok=True,
                        verdict=verdict,
                        payload={"model": cfg.model.name, "mode": cfg.model.mode},
                    )
                    ctx.outputs["train"] = {
                        "model": cfg.model.name,
                        "mode": cfg.model.mode,
                        "signal": signal,
                    }
                elif stage == "validate":
                    result, scores = self._validate(ctx, signal)
                    ctx.outputs["validate"] = dict(result.payload)
                elif stage == "stat_test":
                    testable = tuple(
                        i
                        for i, row in ctx.outputs["validate"]["instruments"].items()
                        if row["testable"]
                    )
                    result = self._stat_test(cfg, testable, scores)
                    survivors = tuple(
                        i
                        for i, row in result.payload["instruments"].items()
                        if row["rejected"]
                    )
                    ctx.outputs["stat_test"] = {
                        **result.payload,
                        "survivors": list(survivors),
                    }
                elif stage in ("optimize", "backtest"):
                    ctx.optimizer_params = resolve_refs(
                        cfg.optimization.params, ctx.outputs
                    )
                    if stage == "optimize":
                        if is_class_ref(cfg.optimization.kind):
                            # user-supplied sizing stage replaces backend.optimize
                            payload = import_ref(cfg.optimization.kind)(
                                ctx, signal, survivors, ctx.optimizer_params
                            )
                        else:
                            payload = self._backend.optimize(ctx, signal, survivors)
                        verdict = f"sized positions for {len(survivors)} survivor(s)"
                    else:
                        payload = self._backend.backtest(ctx, signal, survivors)
                        verdict = (
                            f"replayed test window on {len(survivors)} survivor(s)"
                        )
                    result = StageResult(
                        stage=stage, ok=True, verdict=verdict, payload=payload
                    )
                    ctx.outputs[stage] = dict(payload)
                stages.append(result)
                for tracker in trackers:
                    tracker.log_metrics(stage, _numeric_leaves(result.payload))
                # -- deploy gates --------------------------------------
                if stage == "validate" and not any(
                    row["testable"] for row in result.payload["instruments"].values()
                ):
                    halted_after = "validate"
                    break
                if stage == "stat_test" and not survivors:
                    halted_after = "stat_test"
                    break
        finally:
            for tracker in trackers:
                tracker.close()

        result = RunResult(
            stages=tuple(stages),
            survivors=survivors,
            halted_after=halted_after,
            run_dir=run_dir,
            pipeline_hash=pipeline_hash(resolved),
        )
        self._write_artifacts(result)
        return result

    # -- generic stages ----------------------------------------------------

    def _validate(self, ctx, signal):
        """Model vs. baseline on the validation split, per instrument."""
        cfg = ctx.config
        metric = METRICS[cfg.validation.metric]
        baseline = self._backend.baseline(ctx)
        accounting = self._backend.accounting(ctx)
        settlement = self._backend.settlement(ctx)
        per_instrument, scores = {}, {}
        for instrument in ctx.resolved.instruments:
            outcomes = settlement.outcomes_for(instrument)
            cluster_scores = {}
            n_records, model_sum, base_sum = 0, 0.0, 0.0
            for rec in ctx.records(instrument, split="val"):
                if not rec.usable or rec.mid is None:
                    continue
                outcome = outcomes.get(rec.contract)
                if outcome is None:
                    continue  # unsettled: no label yet, not an error
                y = accounting.payout_per_unit(outcome)
                q_m = signal.q_hat(rec.contract, rec.mid, rec.asof_ms, rec.lead_frac)
                q_b = baseline.q_hat(rec.contract, rec.mid, rec.asof_ms, rec.lead_frac)
                loss_m, loss_b = metric(q_m, y), metric(q_b, y)
                cluster_scores.setdefault(rec.cluster, []).append(loss_b - loss_m)
                n_records += 1
                model_sum += loss_m
                base_sum += loss_b
            n_events = len(cluster_scores)
            testable = n_events >= cfg.validation.min_events
            beats = n_records > 0 and model_sum < base_sum
            per_instrument[instrument] = {
                "n_records": n_records,
                "n_events": n_events,
                "model_loss": model_sum / n_records if n_records else None,
                "baseline_loss": base_sum / n_records if n_records else None,
                "beats_baseline": beats,
                "testable": testable,
            }
            if testable:
                scores[instrument] = cluster_scores
        n_testable = sum(1 for r in per_instrument.values() if r["testable"])
        verdict = (
            f"{n_testable}/{len(per_instrument)} instrument(s) testable "
            f"(min_events={cfg.validation.min_events}); "
            + (
                f"{sum(1 for r in per_instrument.values() if r['beats_baseline'] and r['testable'])} "
                f"beat the {cfg.validation.baseline!r} baseline on "
                f"{cfg.validation.metric}"
                if n_testable
                else "NO-GO: nothing to test"
            )
        )
        return (
            StageResult(
                stage="validate",
                ok=True,
                verdict=verdict,
                payload={"instruments": per_instrument},
            ),
            scores,
        )

    def _stat_test(self, cfg, testable, scores):
        """Per-instrument bootstrap p-values, corrected across the family.

        The family is the TESTABLE instruments; each keeps its own
        reject/accept (the correction borrows strength, never pools)."""
        st = cfg.stat_test
        pvalues = {
            i: cluster_bootstrap_pvalue(scores[i], st.n_boot, st.seed, label=i)
            for i in testable
        }
        rejected = CORRECTIONS[st.correction]["fn"](pvalues, st.alpha)
        payload = {
            "instruments": {
                i: {"p_value": pvalues[i], "rejected": rejected[i]} for i in testable
            },
            "alpha": st.alpha,
            "correction": st.correction,
        }
        n_rej = sum(rejected.values())
        verdict = (
            f"{n_rej}/{len(testable)} instrument(s) show a real edge at "
            f"alpha={st.alpha} ({st.correction})"
            if n_rej
            else f"NO-GO: no instrument survives the {st.correction} correction "
            f"at alpha={st.alpha}"
        )
        return StageResult(stage="stat_test", ok=True, verdict=verdict, payload=payload)

    # -- tracking ----------------------------------------------------------

    def _open_trackers(self, cfg):
        if cfg.tracking is None:
            return ()
        return tuple(
            import_ref(s.kind)(s.params)
            if is_class_ref(s.kind)
            else SINK_KINDS[s.kind]["factory"](s.params)
            for s in cfg.tracking.sinks
        )

    # -- artifacts ---------------------------------------------------------

    def _write_artifacts(self, result) -> None:
        for i, sr in enumerate(result.stages, start=1):
            path = os.path.join(result.run_dir, "stages", f"{i:02d}-{sr.stage}.json")
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(
                    {
                        "stage": sr.stage,
                        "ok": sr.ok,
                        "verdict": sr.verdict,
                        "payload": sr.payload,
                    },
                    fh,
                    indent=2,
                    sort_keys=True,
                )
                fh.write("\n")
        summary = {
            "pipeline_hash": result.pipeline_hash,
            "halted_after": result.halted_after,
            "survivors": list(result.survivors),
            "verdicts": {sr.stage: sr.verdict for sr in result.stages},
        }
        with open(
            os.path.join(result.run_dir, "result.json"), "w", encoding="utf-8"
        ) as fh:
            json.dump(summary, fh, indent=2, sort_keys=True)
            fh.write("\n")
        # report.md leads with the one-line verdict (house rule).
        lines = [f"**{result.verdict}**", ""]
        lines += [f"- pipeline_hash: `{result.pipeline_hash[:16]}…`"]
        lines += [
            f"- halted_after: {result.halted_after or '— (all declared stages ran)'}"
        ]
        lines += [f"- survivors: {', '.join(result.survivors) or 'none'}", ""]
        lines += [f"## {sr.stage}\n\n{sr.verdict}\n" for sr in result.stages]
        with open(
            os.path.join(result.run_dir, "report.md"), "w", encoding="utf-8"
        ) as fh:
            fh.write("\n".join(lines))


def _numeric_leaves(payload, prefix="") -> dict:
    """Flatten a stage payload to ``dotted.path -> number`` (bools and
    non-numerics skipped) — the metric form every sink receives."""
    out = {}
    for key, value in payload.items():
        name = f"{prefix}{key}"
        if isinstance(value, dict):
            out.update(_numeric_leaves(value, f"{name}."))
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            out[name] = value
    return out
