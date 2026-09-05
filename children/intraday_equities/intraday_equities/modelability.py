"""P10's child-owned 25-asset modelability stages (ADR-0081).

Fold execution is not the child's: every walk's folds run through
dskit's ``BoundedFoldRunner`` (ADR-0093) under the frozen 17 GiB cap at
the width the child's documented environment knob declares. What stays
here is domain — which document, which cohort, which tag — and the
journal evidence a walk must leave.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from datetime import date, timedelta

from dskit.pipeline.attempts import AttemptRegistry, tier2_verdict
from dskit.journal import append_action
from dskit.pipeline.document import PipelineDocument
from dskit.pipeline.driver import aggregate_folds, write_walkforward_summary
from dskit.pipeline.folds import BoundedFoldRunner
from dskit.pipeline.runs import (
    resolve_run_root,
    score_bar,
    score_walk,
    single_fold_row,
    walk_cells,
)
from dskit.pipeline.stages import Stage, reject_unknown_params

__all__ = [
    "Gate1SelectStage",
    "Gate1WalksStage",
    "Gate2Stage",
    "Gate3ResultStage",
    "Gate3WalksStage",
    "MemoryPreflightStage",
]

_STUDY = "p10-25-asset-modelability"
_ASSETS = [
    "AAPL",
    "JPM",
    "XOM",
    "WMT",
    "LLY",
    "SPY",
    "QQQ",
    "XLF",
    "XLV",
    "XLE",
    "XLK",
    "XLP",
    "TSLA",
    "TQQQ",
    "NVDA",
    "AMD",
    "UPRO",
    "BAC",
    "AMZN",
    "AVGO",
    "NFLX",
    "MSFT",
    "GOOGL",
    "SMH",
    "IWM",
]
_ARCHITECTURE = "lgbm-tight-pooled"
_MEMORY_LIMIT = 17 * 1024**3
_VERIFIED_CACHES = set()


def _check_list(problems, name, value, expected=None):
    if not isinstance(value, list) or not value:
        problems.append(f"{name} must be a non-empty list")
        return
    if len(set(value)) != len(value):
        problems.append(f"{name} must contain unique values")
    if expected is not None and value != expected:
        problems.append(f"{name} must be exactly {expected}, got {value!r}")


def _params(problems, params, allowed):
    reject_unknown_params(problems, params, allowed)


def _child_root(ctx):
    return os.path.dirname(os.path.dirname(ctx.source_path))


def _file_sha256(path):
    value = hashlib.sha256()
    with open(path, "rb") as handle:
        while chunk := handle.read(1024 * 1024):
            value.update(chunk)
    return value.hexdigest()


def _feature_cache_info(ctx):
    declared = ctx.document.pipeline["features"].params.get("cache_dir")
    if not isinstance(declared, str) or not declared:
        raise ValueError("P10 features must declare cache_dir")
    path = (
        declared
        if os.path.isabs(declared)
        else os.path.join(_child_root(ctx), declared)
    )
    manifest = os.path.join(path, "manifest.json")
    if not os.path.isfile(manifest):
        raise ValueError(f"P10 feature-cache manifest is missing: {manifest}")
    with open(manifest, encoding="utf-8") as handle:
        payload = json.load(handle)
    symbols = payload.get("symbols")
    if (
        not isinstance(symbols, list)
        or len(symbols) != len(_ASSETS)
        or set(symbols) != set(_ASSETS)
    ):
        raise ValueError(
            "P10 feature cache membership must be exactly the frozen 25 assets, "
            f"got {symbols!r}"
        )
    path = os.path.abspath(path)
    digest = _file_sha256(manifest)
    key = (path, digest)
    if key not in _VERIFIED_CACHES:
        from .feature_cache import verify_feature_cache

        verify_feature_cache(path, digest)
        _VERIFIED_CACHES.add(key)
    return declared, path, digest


def _base_key():
    return {
        "study": _STUDY,
        "architecture": _ARCHITECTURE,
        "data_cut": "2026-02-28",
        "row_spacing_minutes": 5,
        "score_lattice_minutes": 30,
    }


def _last_first(spec):
    first = date.fromisoformat(spec.first)
    return (first + timedelta(days=spec.step_days * (spec.count - 1))).isoformat()


def _derived_document(
    ctx, name, horizon, *, score_symbols=None, seed=None, one_fold=False
):
    obj = ctx.document.to_obj()
    obj.pop("stages", None)
    obj["name"] = name
    scan = obj["pipeline"]["scan"]["params"]
    scan["lead_start"] = horizon
    scan["lead_step"] = horizon
    scan["lead_stop"] = horizon
    scan.pop("score_symbols", None)
    scan.pop("label_scramble_seed", None)
    if score_symbols is not None:
        scan["score_symbols"] = list(score_symbols)
    if seed is not None:
        scan["label_scramble_seed"] = seed
    if one_fold:
        walk = obj["walkforward"]
        walk["first"] = _last_first(ctx.document.walkforward)
        walk["count"] = 1
    if not one_fold:
        declared, _path, manifest_sha256 = _feature_cache_info(ctx)
        pipeline = obj["pipeline"]
        obj["pipeline"] = {
            "universe": pipeline["universe"],
            "features": {
                "uses": "intraday_equities-session-feature-cache",
                "params": {"path": declared, "manifest_sha256": manifest_sha256},
            },
            "scan": pipeline["scan"],
        }
    return PipelineDocument.from_obj(obj)


def _canonical(document):
    return (
        json.dumps(document.to_obj(), indent=2, sort_keys=True, allow_nan=False) + "\n"
    )


def _save_derived(document, path):
    text = _canonical(document)
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as handle:
            if handle.read() != text:
                raise ValueError(f"derived config changed at existing path {path}")
        return
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)


def _summary_dir(document, asof, child_root):
    declared = document.outputs.run_root if document.outputs else ""
    root = resolve_run_root(os.path.join(child_root, declared) if declared else None)
    return os.path.join(root, f"{document.name}-walkforward-{asof}-{document.hash[:8]}")


def _journal_confirms_walk(summary, document, asof, child_root):
    from dskit.journal import find_journal
    from dskit.journal.store import read_actions

    record = os.path.join(summary, "walkforward.json")
    if not os.path.isfile(record):
        return False
    try:
        with open(record, encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, ValueError):
        return False
    if payload.get("document_hash") != document.hash or payload.get("state") != "ran":
        return False
    root = find_journal(start=child_root)
    if root is None:
        return False
    needle = f"hash={document.hash[:8]}"
    asof_needle = f"asof={asof}"
    return any(
        row.category == "execute"
        and os.path.abspath(row.outputs) == os.path.abspath(summary)
        and needle in row.notes
        and asof_needle in row.notes
        and "state=ran" in row.notes
        for row in read_actions(root)
    )


#: The child's documented machine knob: how many of a walk's fold
#: processes may run at once. The seam reads it from the PROCESS
#: ENVIRONMENT, never from the document: fold count is a property of
#: the machine, not of what the run computes, and a graded knob would
#: move the identity hash — orphaning every prior run and stored
#: artifact — each time the operator tuned it. The width used is
#: journalled by :func:`_run_bounded_walk`.
_WORKERS_ENV = "INTRADAY_EQUITIES_FOLD_WORKERS"

#: The adapter every fold process imports (ADR-0021: import = registration).
_ADAPTER = "intraday_equities"


def _runner(workers=None):
    """Return the fold seam under the frozen cap, reading the child's knob.

    Parameters
    ----------
    workers : int or None
        An explicit width for callers and tests; ``None`` (what every
        stage passes) lets the seam read :data:`_WORKERS_ENV`.

    Returns
    -------
    dskit.pipeline.folds.BoundedFoldRunner
        Capped at :data:`_MEMORY_LIMIT` — the WHOLE cap per fold, never
        divided (ADR-0093), so a finished fold resumes at any width.
    """
    return BoundedFoldRunner(_MEMORY_LIMIT, workers=workers, env_var=_WORKERS_ENV)


def _walk_argv(path, asof):
    """Return the walk-forward command for one saved derived document."""
    return [
        sys.executable,
        "-m",
        "dskit.pipeline",
        "walkforward",
        path,
        "--asof",
        asof,
        "--adapter",
        _ADAPTER,
    ]


def _prepare_walk(ctx, document, tag):
    """Save a derived walk and say whether it still needs to run.

    Parameters
    ----------
    ctx : object
        The stage run frame; supplies ``asof``, ``run_dir`` and
        ``source_path``.
    document : dskit.pipeline.document.PipelineDocument
        The derived walk.
    tag : str
        Short name for the derived config under ``run_dir/derived``.

    Returns
    -------
    tuple
        ``(argv, summary_dir, reused)`` — the command to run (``None``
        when ``reused``), where its summary lands, and whether a
        journaled summary is already there.

    Raises
    ------
    ValueError
        When artifacts sit at the summary path without journal evidence:
        a partial or foreign walk is never silently adopted.
    """
    child_root = _child_root(ctx)
    configs = os.path.join(ctx.run_dir, "derived")
    os.makedirs(configs, exist_ok=True)
    path = os.path.join(configs, f"{tag}.json")
    _save_derived(document, path)
    summary = _summary_dir(document, ctx.asof, child_root)
    if os.path.isdir(summary) and os.listdir(summary):
        if _journal_confirms_walk(summary, document, ctx.asof, child_root):
            return None, summary, True
        raise ValueError(
            f"walk artifacts exist without matching journal evidence: {summary}"
        )
    return _walk_argv(path, ctx.asof), summary, False


def _measure_walk(ctx, document, tag):
    """Run one derived walk as this process's first child and read its peak.

    Parameters
    ----------
    ctx : object
        The stage run frame; supplies ``asof``, ``run_dir`` and
        ``source_path``.
    document : dskit.pipeline.document.PipelineDocument
        The one walk to measure, usually a single fold.
    tag : str
        Short name for the derived config.

    Returns
    -------
    tuple
        ``(summary_dir, peak_rss_bytes)`` — the reading is the seam's
        ``measure_one``; nothing persists it beside the walk.

    Raises
    ------
    ValueError
        When a finished walk already sits at the summary path — a
        measurement needs a fresh spawn (ADR-0093) — or from the seam's
        contamination and cap guards.
    RuntimeError
        When the walk exits nonzero or leaves no journal evidence.
    """
    argv, summary, reused = _prepare_walk(ctx, document, tag)
    if reused:
        raise ValueError(
            f"walk {tag} already finished at {summary}; a memory measurement "
            "needs a fresh spawn — remove that summary to measure again"
        )
    child_root = _child_root(ctx)
    _done, peak = _runner().measure_one(argv, cwd=child_root)
    if not _journal_confirms_walk(summary, document, ctx.asof, child_root):
        raise RuntimeError(f"walk {tag} finished without journal evidence")
    return summary, peak


def _fold_cutoffs(spec):
    """Return every declared cutoff in chronological order."""
    first = date.fromisoformat(spec.first)
    return [
        (first + timedelta(days=spec.step_days * index)).isoformat()
        for index in range(spec.count)
    ]


def _single_fold_document(document, cutoff, index):
    """Give one fold its own process-safe document identity."""
    obj = document.to_obj()
    obj["name"] = f"{document.name}-part-{index:02d}"
    obj["walkforward"]["first"] = cutoff
    obj["walkforward"]["count"] = 1
    return PipelineDocument.from_obj(obj)


def _run_bounded_walk(ctx, document, tag, *, workers=None):
    """Run each fold in a capped process, then journal one logical summary.

    Parameters
    ----------
    ctx : object
        The stage run frame; supplies ``asof``, ``run_dir`` and
        ``source_path``.
    document : dskit.pipeline.document.PipelineDocument
        The whole multi-fold walk. Each declared cutoff becomes one
        single-fold document run in its own address-space-capped
        process by the seam.
    tag : str
        Short name for the derived per-fold configs and journal rows.
    workers : int or None
        How many fold processes may run at once. ``None`` (the default,
        and what every caller uses) lets the seam read
        :data:`_WORKERS_ENV`, so the knob can never be half-applied by
        a caller that forgot it. A finished, journaled fold is never
        re-spawned, whatever the width it ran at.

    Returns
    -------
    str
        The walk summary directory.

    Raises
    ------
    ValueError
        When ``workers`` or the knob is not a positive int, when
        artifacts exist without journal evidence, or when a fold does
        not leave exactly one ran row for its cutoff.
    RuntimeError
        When a fold exits nonzero, or a summary lacks journal evidence.
    """
    runner = _runner(workers)
    child_root = _child_root(ctx)
    summary = _summary_dir(document, ctx.asof, child_root)
    if os.path.isdir(summary) and os.listdir(summary):
        if _journal_confirms_walk(summary, document, ctx.asof, child_root):
            return summary
        raise ValueError(
            f"walk artifacts exist without matching journal evidence: {summary}"
        )
    cutoffs = list(_fold_cutoffs(document.walkforward))
    parts = [
        _single_fold_document(document, cutoff, index)
        for index, cutoff in enumerate(cutoffs)
    ]
    planned = [
        _prepare_walk(ctx, part, f"{tag}-part-{index:02d}")
        for index, part in enumerate(parts)
    ]
    pending = [argv for argv, _summary, reused in planned if not reused]
    if pending:
        runner.run(pending, cwd=child_root)
    folds = []
    for part, cutoff, (_argv, part_summary, _reused) in zip(parts, cutoffs, planned):
        if not _journal_confirms_walk(part_summary, part, ctx.asof, child_root):
            raise RuntimeError(f"fold walk {part.name} finished without journal evidence")
        folds.append(single_fold_row(part_summary, cutoff))
    spec = document.walkforward
    aggregate = aggregate_folds(folds, spec.select, spec.weight_halflife_folds)
    write_walkforward_summary(
        summary, document, ctx.asof, spec, "ran", folds, aggregate
    )
    append_action(
        "execute",
        f"{document.name} bounded walk-forward",
        inputs=ctx.source_path,
        outputs=summary,
        notes=(
            f"state=ran folds={len(folds)} hash={document.hash[:8]} "
            f"asof={ctx.asof}; fold_processes=isolated; "
            f"fold_workers={runner.workers}; memory_limit_bytes={_MEMORY_LIMIT}"
        ),
        start=child_root,
    )
    if not _journal_confirms_walk(summary, document, ctx.asof, child_root):
        raise RuntimeError("bounded walk summary lacks matching journal evidence")
    return summary


class MemoryPreflightStage(Stage):
    """Measure the most recent single pooled fold under the 17 GiB cap.

    The reading is the seam's ``measure_one`` (ADR-0093): this stage is
    the study's first, so the walk it spawns is the first child this
    process reaps, and the stage keeps only its threshold and its choice
    of what to run.

    Parameters
    ----------
    params : dict
        ``memory_limit_bytes`` must be exactly 17 GiB.

    Examples
    --------
    Instantiate the frozen preflight::

        stage = MemoryPreflightStage("memory", {"memory_limit_bytes": 17 * 1024**3})
    """

    outputs = (
        "summary_dir",
        "peak_rss_bytes",
        "limit_bytes",
        "feature_cache",
        "feature_cache_manifest_sha256",
        "passed",
    )
    _PARAMS = ("memory_limit_bytes",)

    @classmethod
    def validate_params(cls, params):
        """Validate the declared byte limit."""
        problems = []
        _params(problems, params, cls._PARAMS)
        value = params.get("memory_limit_bytes")
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            problems.append("memory_limit_bytes must be a positive integer")
        elif value != _MEMORY_LIMIT:
            problems.append(
                f"memory_limit_bytes must be exactly 17 GiB ({_MEMORY_LIMIT})"
            )
        return problems

    def run(self, ctx, inputs):
        """Measure the isolated most-recent fold as this process's first child."""
        del inputs
        limit = self.params["memory_limit_bytes"]
        doc = _derived_document(ctx, f"{_STUDY}-preflight", 1, one_fold=True)
        summary, peak = _measure_walk(ctx, doc, "memory-preflight")
        if peak >= limit:
            raise MemoryError(
                f"25-asset fold used {peak} bytes, not strictly below {limit}"
            )
        _declared, cache_path, cache_digest = _feature_cache_info(ctx)
        return {
            "summary_dir": summary,
            "peak_rss_bytes": peak,
            "limit_bytes": limit,
            "feature_cache": cache_path,
            "feature_cache_manifest_sha256": cache_digest,
            "passed": True,
        }


class Gate1WalksStage(Stage):
    """Run one full 25-asset pooled walk per frozen horizon."""

    outputs = ("walks",)
    _PARAMS = ("horizons",)

    @classmethod
    def validate_params(cls, params):
        """Require the frozen horizon sequence."""
        problems = []
        _params(problems, params, cls._PARAMS)
        _check_list(
            problems, "horizons", params.get("horizons"), [1, 2, 3, 5, 10, 20, 30, 60]
        )
        return problems

    def run(self, ctx, inputs):
        """Execute or reuse every Gate-1 walk."""
        del inputs
        walks = {}
        for horizon in self.params["horizons"]:
            name = f"{_STUDY}-gate1-h{horizon:02d}"
            doc = _derived_document(ctx, name, horizon)
            summary = _run_bounded_walk(ctx, doc, f"gate1-h{horizon:02d}")
            walks[str(horizon)] = summary
        return {"walks": walks}


class Gate1SelectStage(Stage):
    """Register all 200 cells atomically, then select consecutive horizons."""

    outputs = ("rows", "cells")
    _PARAMS = ("horizons", "assets", "attempt_registry", "alpha")

    @classmethod
    def validate_params(cls, params):
        """Validate the frozen universe, horizons, ledger, and level."""
        problems = []
        _params(problems, params, cls._PARAMS)
        _check_list(
            problems, "horizons", params.get("horizons"), [1, 2, 3, 5, 10, 20, 30, 60]
        )
        _check_list(problems, "assets", params.get("assets"))
        if len(params.get("assets", [])) != 25:
            problems.append("assets must contain exactly 25 symbols")
        if not isinstance(params.get("attempt_registry"), str):
            problems.append("attempt_registry must be a path string")
        alpha = params.get("alpha")
        if not isinstance(alpha, (int, float)) or not 0 < alpha < 1:
            problems.append("alpha must lie in (0, 1)")
        return problems

    def validate_inputs(self, inputs):
        """Require the horizon-to-walk mapping."""
        return [] if set(inputs) == {"walks"} else ["requires only walks"]

    def run(self, ctx, inputs):
        """Register all cells before applying consecutive selection."""
        horizons = self.params["horizons"]
        assets = self.params["assets"]
        all_cells = []
        for horizon in horizons:
            summary = inputs["walks"][str(horizon)]
            all_cells.extend(
                walk_cells(
                    summary,
                    key=_base_key(),
                    alpha=self.params["alpha"],
                    group=None,
                )
            )
        pairs = {(cell["key"]["series"], cell["key"]["horizon"]) for cell in all_cells}
        expected = {(asset, horizon) for asset in assets for horizon in horizons}
        if len(all_cells) != 200 or pairs != expected:
            missing = sorted(expected - pairs)
            extra = sorted(pairs - expected)
            raise ValueError(
                f"Gate 1 must produce exactly 200 cells; got {len(all_cells)}; "
                f"missing={missing[:10]} extra={extra[:10]}"
            )
        registry_path = os.path.join(_child_root(ctx), self.params["attempt_registry"])
        registry = AttemptRegistry(registry_path)
        cells = []
        for cell in all_cells:
            skill = cell["skill"] or {}
            registry.record(
                cell["key"],
                walk=inputs["walks"][str(cell["key"]["horizon"])],
                t_pool=skill.get("t_pool"),
                t_fold=skill.get("t_fold"),
                r2oos=skill.get("r2oos_pool"),
                n_folds=cell["n_folds"],
                study_gate="gate1",
            )
            cells.append(
                {
                    "cell": cell["cell"],
                    "asset": cell["key"]["series"],
                    "horizon": cell["key"]["horizon"],
                    "skill": skill,
                    "n_folds": cell["n_folds"],
                }
            )
        by_pair = {(row["asset"], row["horizon"]): row for row in cells}
        rows = []
        for asset in assets:
            selected = None
            first_failed = None
            for horizon in horizons:
                if by_pair[(asset, horizon)]["skill"].get("passes") is True:
                    selected = horizon
                else:
                    first_failed = horizon
                    break
            chosen = by_pair.get((asset, selected)) if selected is not None else None
            skill = chosen["skill"] if chosen else {}
            rows.append(
                {
                    "asset": asset,
                    "gate1_h": selected,
                    "gate1_passes": selected is not None,
                    "first_failed_h": first_failed,
                    "n_folds": chosen["n_folds"]
                    if chosen
                    else by_pair[(asset, 1)]["n_folds"],
                    "n_rows": skill.get("n_rows", 0),
                }
            )
        return {"rows": rows, "cells": cells}


class Gate2Stage(Stage):
    """Apply one shared max-statistic family to all 200 registered cells."""

    outputs = ("rows", "cells", "bar")
    _PARAMS = ("horizons", "assets", "n_boot", "seed", "alpha")

    @classmethod
    def validate_params(cls, params):
        """Validate the study-wide correction controls."""
        problems = []
        _params(problems, params, cls._PARAMS)
        _check_list(
            problems, "horizons", params.get("horizons"), [1, 2, 3, 5, 10, 20, 30, 60]
        )
        _check_list(problems, "assets", params.get("assets"))
        if len(params.get("assets", [])) != 25:
            problems.append("assets must contain exactly 25 symbols")
        if not isinstance(params.get("n_boot"), int) or params.get("n_boot", 0) < 100:
            problems.append("n_boot must be an integer >= 100")
        if not isinstance(params.get("seed"), int):
            problems.append("seed must be an integer")
        alpha = params.get("alpha")
        if not isinstance(alpha, (int, float)) or not 0 < alpha < 1:
            problems.append("alpha must lie in (0, 1)")
        return problems

    def validate_inputs(self, inputs):
        """Require Gate-1 rows and all horizon walks."""
        return [] if set(inputs) == {"walks", "gate1"} else ["requires walks and gate1"]

    def run(self, ctx, inputs):
        """Correct all 200 cells as one shared family."""
        del ctx
        horizons = self.params["horizons"]
        summary_dirs = [inputs["walks"][str(h)] for h in horizons]
        scored = score_bar(
            summary_dirs,
            keys=[_base_key() for _h in horizons],
            registry=None,
            n_boot=self.params["n_boot"],
            seed=self.params["seed"],
            alpha=self.params["alpha"],
            group=None,
            family_of=lambda _cell: _STUDY,
        )
        if scored["n_cells"] != 200 or set(scored["families"]) != {_STUDY}:
            raise ValueError(
                f"Gate 2 requires one 200-cell family; got {scored['n_cells']} "
                f"cells and families {sorted(scored['families'])}"
            )
        family = scored["families"][_STUDY]
        cells = []
        by_pair = {}
        for verdict in family["verdicts"]:
            key = family["keys"][verdict["cell"]]
            row = {
                "cell": verdict["cell"],
                "asset": key["series"],
                "horizon": key["horizon"],
                **verdict,
            }
            cells.append(row)
            by_pair[(row["asset"], row["horizon"])] = row
        rows = []
        for gate1 in inputs["gate1"]:
            selected = gate1["gate1_h"]
            verdict = by_pair.get((gate1["asset"], selected))
            rows.append(
                {
                    **gate1,
                    "gate2_status": (
                        "not_reached"
                        if selected is None
                        else "pass"
                        if verdict["passes"]
                        else "fail"
                    ),
                    "gate2_passes": bool(verdict and verdict["passes"]),
                    "gate2_cell": verdict["cell"] if verdict else None,
                    "gate2_reasons": verdict["reasons"] if verdict else [],
                }
            )
        bar = {key: value for key, value in family["bar"].items() if key != "rows"}
        return {"rows": rows, "cells": cells, "bar": bar}


class Gate3WalksStage(Stage):
    """Refit identical pooled models for every survivor horizon and seed."""

    outputs = ("walks", "survivors")
    _PARAMS = ("seeds",)

    @classmethod
    def validate_params(cls, params):
        """Require the nineteen frozen scramble seeds."""
        problems = []
        _params(problems, params, cls._PARAMS)
        _check_list(problems, "seeds", params.get("seeds"), list(range(19)))
        return problems

    def validate_inputs(self, inputs):
        """Require Gate-2 survivor rows."""
        return [] if set(inputs) == {"gate2"} else ["requires only gate2"]

    def run(self, ctx, inputs):
        """Run null refits while retaining all 25 training assets."""
        survivors = [row for row in inputs["gate2"] if row["gate2_passes"]]
        by_horizon = {}
        for row in survivors:
            by_horizon.setdefault(row["gate1_h"], []).append(row["asset"])
        walks = {}
        for horizon in sorted(by_horizon):
            score_symbols = sorted(by_horizon[horizon])
            for seed in self.params["seeds"]:
                tag = f"gate3-h{horizon:02d}-seed{seed:02d}"
                doc = _derived_document(
                    ctx,
                    f"{_STUDY}-{tag}",
                    horizon,
                    score_symbols=score_symbols,
                    seed=seed,
                )
                summary = _run_bounded_walk(ctx, doc, tag)
                walks[f"{horizon}:{seed}"] = summary
        return {
            "walks": walks,
            "survivors": [row["asset"] for row in survivors],
        }


class Gate3ResultStage(Stage):
    """Score every survivor's 19 null refits and emit all 25 final rows."""

    outputs = ("rows",)
    _PARAMS = ("assets", "seeds", "alpha")

    @classmethod
    def validate_params(cls, params):
        """Validate final-row universe, seeds, and test level."""
        problems = []
        _params(problems, params, cls._PARAMS)
        _check_list(problems, "assets", params.get("assets"))
        if len(params.get("assets", [])) != 25:
            problems.append("assets must contain exactly 25 symbols")
        _check_list(problems, "seeds", params.get("seeds"), list(range(19)))
        alpha = params.get("alpha")
        if not isinstance(alpha, (int, float)) or not 0 < alpha < 1:
            problems.append("alpha must lie in (0, 1)")
        return problems

    def validate_inputs(self, inputs):
        """Require corrected cells, survivor rows, and null walks."""
        needed = {"gate2", "gate2_cells", "walks"}
        return [] if set(inputs) == needed else [f"requires {sorted(needed)}"]

    def run(self, ctx, inputs):
        """Emit one explicit result row per frozen asset."""
        del ctx
        observed = {
            (row["asset"], row["horizon"]): row for row in inputs["gate2_cells"]
        }
        scored_walks = {}
        for key, summary in inputs["walks"].items():
            scored_walks[key] = score_walk(
                summary, alpha=self.params["alpha"], group=None
            )["rows"]
        gate2 = {row["asset"]: row for row in inputs["gate2"]}
        rows = []
        for asset in self.params["assets"]:
            base = gate2[asset]
            final = {**base, "gate3_status": "not_reached", "gate3_passes": False}
            if not base["gate1_passes"]:
                final["not_reached_reason"] = "gate1_no_consecutive_horizon"
            elif not base["gate2_passes"]:
                final["not_reached_reason"] = "gate2_selected_horizon_failed"
            else:
                horizon = base["gate1_h"]
                null_r2 = []
                null_t = []
                for seed in self.params["seeds"]:
                    candidates = [
                        row
                        for row in scored_walks[f"{horizon}:{seed}"]
                        if row["series"] == asset and row["lead"] == horizon
                    ]
                    if len(candidates) != 1:
                        raise ValueError(
                            f"Gate 3 expected one row for {asset} h={horizon} "
                            f"seed={seed}; got {len(candidates)}"
                        )
                    null_r2.append(candidates[0]["r2oos"])
                    null_t.append(candidates[0]["t_pool"])
                real = observed[(asset, horizon)]
                verdict = tier2_verdict(real["r2oos"], null_r2, null_t)
                final.update(
                    {
                        "gate3_status": "pass" if verdict["passes"] else "fail",
                        "gate3_passes": verdict["passes"],
                        "gate3": verdict,
                        "not_reached_reason": None,
                    }
                )
            rows.append(final)
        return {"rows": rows}
