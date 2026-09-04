"""P11 asset-local modelability stages with a direct scramble audit.

P10's immutable cache and journal-backed runner are reused, but its pooled
estimand is not. Each derived walk filters feature records before fitting;
the full tape remains available only so non-SPY labels can retain their SPY
residual reference. Gate 1 stops at the first failed ordered horizon, then
flows directly to the whole-session refit audit in Gate 3.
"""

from __future__ import annotations

import os

from dskit.pipeline.attempts import AttemptRegistry, tier2_verdict
from dskit.pipeline.document import PipelineDocument
from dskit.pipeline.runs import score_walk
from dskit.pipeline.stages import Stage, reject_unknown_params

from . import modelability as p10

__all__ = [
    "Gate1Stage",
    "Gate3ResultStage",
    "Gate3WalksStage",
    "MemoryPreflightStage",
]

_STUDY = "p11-25-asset-modelability"
_ARCHITECTURE = "lgbm-tight-asset-local"
_ASSETS = list(p10._ASSETS)
_HORIZONS = [1, 2, 3, 5, 10, 20, 30, 60]
_MEMORY_LIMIT = 17 * 1024**3


def _check_params(params, allowed, *, assets=False, horizons=False, alpha=False):
    """Return default-deny P11 parameter problems."""
    problems = []
    reject_unknown_params(problems, params, allowed)
    if assets and params.get("assets") != _ASSETS:
        problems.append(f"assets must be exactly {_ASSETS}")
    if horizons and params.get("horizons") != _HORIZONS:
        problems.append(f"horizons must be exactly {_HORIZONS}")
    if alpha:
        value = params.get("alpha")
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not 0.0 < float(value) < 1.0
        ):
            problems.append("alpha must lie in (0, 1)")
    return problems


def _base_key(gate):
    """Return the attempt knobs shared by one P11 evidence block."""
    return {
        "study": _STUDY,
        "architecture": _ARCHITECTURE,
        "data_cut": "2026-02-28",
        "evidence": gate,
        "row_spacing_minutes": 5,
        "score_lattice_minutes": 30,
    }


def _derived_document(
    ctx, asset, horizon, *, tag="gate1", scramble_seed=None
):
    """Build one asset-only derived walk without touching the full tape."""
    if asset not in _ASSETS:
        raise ValueError(f"P11 asset is outside the frozen cohort: {asset!r}")
    if horizon not in _HORIZONS:
        raise ValueError(f"P11 horizon is outside the frozen order: {horizon!r}")
    declared, _cache_path, manifest_sha256 = p10._feature_cache_info(ctx)
    obj = ctx.document.to_obj()
    obj.pop("stages", None)
    obj["name"] = f"{_STUDY}-{tag}-{asset.lower()}-h{horizon:02d}"
    original = obj["pipeline"]
    scan = original["scan"]
    scan["inputs"]["records"] = "$asset_features.records"
    scan["inputs"]["bars"] = "$features.tape"
    params = scan["params"]
    params["lead_start"] = horizon
    params["lead_step"] = horizon
    params["lead_stop"] = horizon
    params["fit_symbols"] = [asset]
    params["score_symbols"] = [asset]
    params.pop("label_scramble_seed", None)
    if scramble_seed is not None:
        if isinstance(scramble_seed, bool) or not isinstance(scramble_seed, int):
            raise ValueError(f"P11 scramble seed must be an integer: {scramble_seed!r}")
        params["label_scramble_seed"] = scramble_seed
    obj["pipeline"] = {
        "universe": original["universe"],
        "features": {
            "uses": "intraday_equities-session-feature-cache",
            "params": {
                "path": declared,
                "manifest_sha256": manifest_sha256,
            },
        },
        "asset_features": {
            "uses": "filter",
            "inputs": {"records": "$features.records"},
            "params": {
                "where": [{"field": "symbol", "op": "==", "value": asset}]
            },
        },
        "scan": scan,
    }
    return PipelineDocument.from_obj(obj)


def _largest_asset(cache_path, assets):
    """Return the first frozen asset with the largest cached feature frame."""
    import numpy as np

    sizes = []
    for asset in assets:
        path = os.path.join(cache_path, f"{asset}.features.X.npy")
        rows = int(np.load(path, mmap_mode="r", allow_pickle=False).shape[0])
        sizes.append((rows, asset))
    largest = max(rows for rows, _asset in sizes)
    return next(asset for rows, asset in sizes if rows == largest), largest


def _score_one(summary, asset, horizon, alpha):
    """Require one exact, non-GROUP row from an asset-local walk."""
    scored = score_walk(summary, alpha=alpha, group=None)
    rows = [
        row
        for row in scored["rows"]
        if row.get("series") == asset and row.get("lead") == horizon
    ]
    if not scored["exact"] or len(rows) != 1 or len(scored["rows"]) != 1:
        raise ValueError(
            f"P11 expected one exact row for {asset}@{horizon}; "
            f"exact={scored['exact']} rows={scored['rows']!r}"
        )
    return rows[0]


class MemoryPreflightStage(Stage):
    """Measure the largest asset under the frozen Gate-1 geometry.

    Parameters
    ----------
    params : dict
        ``assets`` must be the frozen 25; ``memory_limit_bytes`` must be
        exactly 17 GiB.

    Examples
    --------
    Instantiate the frozen preflight::

        stage = MemoryPreflightStage("memory", {
            "assets": _ASSETS, "memory_limit_bytes": 17 * 1024**3,
        })
    """

    outputs = (
        "asset",
        "feature_rows",
        "summary_dir",
        "peak_rss_bytes",
        "limit_bytes",
        "feature_cache_manifest_sha256",
        "passed",
    )
    _PARAMS = ("assets", "memory_limit_bytes")

    @classmethod
    def validate_params(cls, params):
        """Validate the frozen cohort and byte limit."""
        problems = _check_params(params, cls._PARAMS, assets=True)
        if params.get("memory_limit_bytes") != _MEMORY_LIMIT:
            problems.append(f"memory_limit_bytes must be exactly {_MEMORY_LIMIT}")
        return problems

    def run(self, ctx, inputs):
        """Run one capped Gate-1-shaped asset walk."""
        del inputs
        _declared, cache_path, digest = p10._feature_cache_info(ctx)
        asset, rows = _largest_asset(cache_path, self.params["assets"])
        document = _derived_document(
            ctx, asset, 1, tag="preflight"
        )
        summary, peak = p10._run_walk(
            ctx,
            document,
            f"p11-memory-{asset.lower()}",
            memory_limit=self.params["memory_limit_bytes"],
        )
        if peak is None or peak >= self.params["memory_limit_bytes"]:
            raise MemoryError(
                f"P11 preflight peak {peak!r} is not strictly below "
                f"{self.params['memory_limit_bytes']}"
            )
        _score_one(summary, asset, 1, 0.05)
        return {
            "asset": asset,
            "feature_rows": rows,
            "summary_dir": summary,
            "peak_rss_bytes": peak,
            "limit_bytes": self.params["memory_limit_bytes"],
            "feature_cache_manifest_sha256": digest,
            "passed": True,
        }


class Gate1Stage(Stage):
    """Fit each asset alone and stop its ordered horizon walk on failure.

    Parameters
    ----------
    params : dict
        Frozen ``assets``, ordered ``horizons``, attempt-ledger path and
        single-cell ``alpha``.

    Examples
    --------
    Instantiate the sequential gate::

        stage = Gate1Stage("gate1", {
            "assets": _ASSETS, "horizons": _HORIZONS,
            "attempt_registry": "docs/decisioning/attempts.jsonl",
            "alpha": 0.05,
        })
    """

    outputs = ("rows", "cells")
    _PARAMS = ("assets", "horizons", "attempt_registry", "alpha")

    @classmethod
    def validate_params(cls, params):
        """Validate the cohort, horizon order, ledger path and level."""
        problems = _check_params(
            params, cls._PARAMS, assets=True, horizons=True, alpha=True
        )
        if not isinstance(params.get("attempt_registry"), str):
            problems.append("attempt_registry must be a path string")
        return problems

    def validate_inputs(self, inputs):
        """Require a passed memory preflight."""
        return [] if inputs == {"preflight": True} else ["requires passed preflight"]

    def run(self, ctx, inputs):
        """Run, score and register only consecutive asset-local cells."""
        del inputs
        ledger_path = os.path.join(
            p10._child_root(ctx), self.params["attempt_registry"]
        )
        attempts = AttemptRegistry(ledger_path)
        cells = []
        rows = []
        for asset in self.params["assets"]:
            selected = None
            first_failed = None
            attempted = []
            for horizon in self.params["horizons"]:
                document = _derived_document(ctx, asset, horizon)
                summary = p10._run_bounded_walk(
                    ctx, document, f"p11-gate1-{asset.lower()}-h{horizon:02d}"
                )
                skill = _score_one(summary, asset, horizon, self.params["alpha"])
                key = {
                    **_base_key("gate1-selection"),
                    "series": asset,
                    "horizon": horizon,
                }
                cell = attempts.record(
                    key,
                    walk=summary,
                    t_pool=skill["t_pool"],
                    t_fold=skill["t_fold"],
                    r2oos=skill["r2oos"],
                    n_folds=skill["n_folds"],
                    study_gate="gate1",
                )
                evidence = {
                    "cell": cell,
                    "asset": asset,
                    "horizon": horizon,
                    "walk": summary,
                    "skill": skill,
                }
                cells.append(evidence)
                attempted.append(horizon)
                if skill.get("passes") is True:
                    selected = horizon
                    continue
                first_failed = horizon
                break
            unrun = [h for h in self.params["horizons"] if h not in attempted]
            rows.append(
                {
                    "asset": asset,
                    "gate1_h": selected,
                    "gate1_passes": selected is not None,
                    "first_failed_h": first_failed,
                    "attempted_horizons": attempted,
                    "unrun_horizons": unrun,
                }
            )
        if {row["asset"] for row in rows} != set(_ASSETS) or len(rows) != len(
            _ASSETS
        ):
            raise ValueError("Gate 1 did not emit the exact frozen 25 assets")
        return {"rows": rows, "cells": cells}


class Gate3WalksStage(Stage):
    """Refit each Gate-1 survivor against whole-session null draws.

    Parameters
    ----------
    params : dict
        ``seeds`` must be the frozen sequence 0 through 18.

    Examples
    --------
    Instantiate the null-refit stage::

        stage = Gate3WalksStage("gate3_walks", {"seeds": list(range(19))})
    """

    outputs = ("walks", "survivors")
    _PARAMS = ("seeds",)

    @classmethod
    def validate_params(cls, params):
        """Require the frozen 19-session-permutation sequence."""
        problems = _check_params(params, cls._PARAMS)
        if params.get("seeds") != list(range(19)):
            problems.append("seeds must be exactly [0, ..., 18]")
        return problems

    def validate_inputs(self, inputs):
        """Require the ordered Gate-1 outcomes."""
        return [] if set(inputs) == {"gate1"} else ["requires only gate1"]

    def run(self, ctx, inputs):
        """Run all null refits for each selected asset-local horizon."""
        gate1 = inputs["gate1"]
        if [row.get("asset") for row in gate1] != _ASSETS:
            raise ValueError("Gate 3 input is not the frozen ordered 25 assets")
        survivors = [row for row in gate1 if row["gate1_passes"]]
        walks = {}
        for row in survivors:
            asset = row["asset"]
            horizon = row["gate1_h"]
            for seed in self.params["seeds"]:
                tag = f"p11-gate3-{asset.lower()}-h{horizon:02d}-seed{seed:02d}"
                document = _derived_document(
                    ctx, asset, horizon, tag=tag, scramble_seed=seed
                )
                walks[f"{asset}:{horizon}:{seed}"] = p10._run_bounded_walk(
                    ctx, document, tag
                )
        return {"walks": walks, "survivors": [row["asset"] for row in survivors]}


class Gate3ResultStage(Stage):
    """Require every whole-session null refit to lose to the real result.

    Parameters
    ----------
    params : dict
        ``assets`` and ``seeds`` pin the final output order and null draws;
        ``alpha`` is passed unchanged to the walk scorer.

    Examples
    --------
    Instantiate the final audit::

        stage = Gate3ResultStage("gate3", {
            "assets": _ASSETS, "seeds": list(range(19)), "alpha": 0.05,
        })
    """

    outputs = ("rows",)
    _PARAMS = ("assets", "seeds", "alpha")

    @classmethod
    def validate_params(cls, params):
        """Validate the frozen cohort, null draws and scoring level."""
        problems = _check_params(params, cls._PARAMS, assets=True, alpha=True)
        if params.get("seeds") != list(range(19)):
            problems.append("seeds must be exactly [0, ..., 18]")
        return problems

    def validate_inputs(self, inputs):
        """Require observed Gate-1 rows/cells and their null refits."""
        needed = {"gate1", "gate1_cells", "walks"}
        return [] if set(inputs) == needed else [f"requires {sorted(needed)}"]

    def run(self, ctx, inputs):
        """Emit one Gate-3 decision for every frozen asset."""
        del ctx
        gate1 = inputs["gate1"]
        if [row.get("asset") for row in gate1] != self.params["assets"]:
            raise ValueError("Gate 3 result input is not the frozen ordered 25 assets")
        observed = {
            (row["asset"], row["horizon"]): row["skill"]
            for row in inputs["gate1_cells"]
        }
        rows = []
        for base in gate1:
            final = {**base, "gate3_status": "not_reached", "gate3_passes": False}
            if not base["gate1_passes"]:
                final["not_reached_reason"] = "gate1_failed_at_h1"
            else:
                asset = base["asset"]
                horizon = base["gate1_h"]
                null_r2 = []
                null_t = []
                for seed in self.params["seeds"]:
                    skill = _score_one(
                        inputs["walks"][f"{asset}:{horizon}:{seed}"],
                        asset,
                        horizon,
                        self.params["alpha"],
                    )
                    null_r2.append(skill["r2oos"])
                    null_t.append(skill["t_pool"])
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
