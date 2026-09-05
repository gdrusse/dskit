"""P11 asset-local modelability stages with a direct scramble audit.

P10's immutable cache and journal-backed runner are reused, but its pooled
estimand is not. Each derived walk filters feature records before fitting;
each walk keeps only its own tape and the one its label declares as the
residual reference. Gate 1 stops at the first failed ordered horizon, then
flows directly to the whole-session refit audit in Gate 3, which stops an
asset at the first null that matches or beats its real result
(ADR-0092): one such null already decides the strict beat-all limb, so
the remaining seeds are never run, while a pass always carries the full
19-draw calibration.
"""

from __future__ import annotations

import os

from dskit.pipeline.attempts import AttemptRegistry, beat_all, tier2_verdict
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


def _tape_symbols(asset, residual):
    """Name the only tapes an asset-local walk reads.

    Parameters
    ----------
    asset : str
        The one symbol the walk fits and scores.
    residual : str or None
        The scan's declared ``label_residual`` reference symbol, read
        from the document rather than restated here: a second copy of
        ``"SPY"`` would silently drop the reference tape the moment the
        config named a different one, and only for the assets that are
        not themselves the residual.

    Returns
    -------
    list of str
        The asset, plus the residual when it is a different symbol.
    """
    if residual is None or residual == asset:
        return [asset]
    return [asset, residual]


def _derived_document(ctx, asset, horizon, *, tag="gate1", scramble_seed=None):
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
    scan["inputs"]["bars"] = "$reference_tape.records"
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
            "params": {"where": [{"field": "symbol", "op": "==", "value": asset}]},
        },
        "reference_tape": {
            "uses": "filter",
            "inputs": {"records": "$features.tape"},
            "params": {
                "where": [
                    {
                        "field": "symbol",
                        "op": "in",
                        "value": _tape_symbols(asset, params.get("label_residual")),
                    }
                ]
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
        document = _derived_document(ctx, asset, 1, tag="preflight")
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
                    ctx,
                    document,
                    f"p11-gate1-{asset.lower()}-h{horizon:02d}",
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
        if {row["asset"] for row in rows} != set(_ASSETS) or len(rows) != len(_ASSETS):
            raise ValueError("Gate 1 did not emit the exact frozen 25 assets")
        return {"rows": rows, "cells": cells}


def _observed_skill(cells):
    """Index the Gate-1 cells by ``(asset, horizon)`` for the audit."""
    return {(row["asset"], row["horizon"]): row["skill"] for row in cells}


def _stopped_row(draw):
    """Emit the ADR-0092 fields of an asset whose audit stopped early."""
    # The record's three fields were already checked by _draw_problems.
    # null_mean, null_sd and calibration say nothing was computed and there
    # is no "gate3" block, because the family was never completed and
    # tier2_verdict never ran: the Besag–Clifford bound stands in its place.
    n_draws = draw["n_draws"]
    return {
        "gate3_status": "fail",
        "gate3_passes": False,
        "not_reached_reason": None,
        "stopped": True,
        "stop_seed": draw.get("stop_seed"),
        "n_draws": n_draws,
        "p_bound": 2 / (n_draws + 1),
        "null_mean": None,
        "null_sd": None,
        "calibration": "not_computed_early_stop",
    }


def _draw_problems(asset, draw, seeds):
    """List what is malformed in one survivor's ADR-0092 stop record."""
    stopped = draw.get("stopped")
    stop_seed = draw.get("stop_seed")
    n_draws = draw.get("n_draws")
    problems = []
    if not isinstance(stopped, bool):
        problems.append(f"{asset} stopped={stopped!r} is not a bool")
    if isinstance(n_draws, bool) or not isinstance(n_draws, int) or n_draws < 1:
        problems.append(f"{asset} n_draws={n_draws!r} is not a positive int")
    if stopped is True and (isinstance(stop_seed, bool) or stop_seed not in seeds):
        problems.append(f"{asset} stop_seed={stop_seed!r} is not one of the seeds")
    if (
        stopped is True
        and not isinstance(stop_seed, bool)
        and stop_seed in seeds
        and n_draws != seeds.index(stop_seed) + 1
    ):
        problems.append(
            f"{asset} stopped at stop_seed={stop_seed!r} but n_draws={n_draws!r} "
            f"is not the {seeds.index(stop_seed) + 1} seeds run in order before it"
        )
    if stopped is False and stop_seed is not None:
        problems.append(f"{asset} stop_seed={stop_seed!r} but the audit completed")
    if stopped is False and n_draws != len(seeds):
        problems.append(
            f"{asset} did not stop but n_draws={n_draws!r} is not the "
            f"{len(seeds)} frozen seeds"
        )
    return problems


class Gate3WalksStage(Stage):
    """Refit each Gate-1 survivor against whole-session nulls, stopping early.

    The frozen seeds run in order and an asset stops at the first
    completed null whose ``r2oos`` matches or beats the real walk's,
    i.e. when :func:`~dskit.pipeline.attempts.beat_all` is false for
    that one draw (ADR-0092). A pass is never stopped: an asset that
    beats every null runs all 19 and carries a full calibration family.

    Parameters
    ----------
    params : dict
        ``seeds`` must be the frozen sequence 0 through 18; ``alpha`` is
        passed unchanged to the walk scorer.

    Examples
    --------
    Instantiate the fail-fast null-refit stage::

        stage = Gate3WalksStage(
            "gate3_walks", {"seeds": list(range(19)), "alpha": 0.05}
        )
    """

    outputs = ("walks", "survivors", "draws")
    _PARAMS = ("seeds", "alpha")

    @classmethod
    def validate_params(cls, params):
        """Require the frozen 19-session-permutation sequence and a level."""
        problems = _check_params(params, cls._PARAMS, alpha=True)
        if params.get("seeds") != list(range(19)):
            problems.append("seeds must be exactly [0, ..., 18]")
        return problems

    def validate_inputs(self, inputs):
        """Require the ordered Gate-1 outcomes and their scored cells."""
        needed = {"gate1", "gate1_cells"}
        return [] if set(inputs) == needed else [f"requires {sorted(needed)}"]

    def run(self, ctx, inputs):
        """Audit each selected asset-local horizon, one null at a time."""
        gate1 = inputs["gate1"]
        if [row.get("asset") for row in gate1] != _ASSETS:
            raise ValueError("Gate 3 input is not the frozen ordered 25 assets")
        observed = _observed_skill(inputs["gate1_cells"])
        survivors = [row for row in gate1 if row["gate1_passes"]]
        self._refuse_unscored_cells(survivors, observed)
        walks = {}
        draws = {}
        for row in survivors:
            asset = row["asset"]
            horizon = row["gate1_h"]
            draws[asset] = self._audit(
                ctx, asset, horizon, observed[(asset, horizon)]["r2oos"], walks
            )
        return {
            "walks": walks,
            "survivors": [row["asset"] for row in survivors],
            "draws": draws,
        }

    def _refuse_unscored_cells(self, survivors, observed):
        """Refuse a survivor whose selected cell Gate 1 never scored."""
        missing = [
            (row["asset"], row["gate1_h"])
            for row in survivors
            if (row["asset"], row["gate1_h"]) not in observed
        ]
        if missing:
            raise ValueError(
                f"Gate 3 has no Gate-1 cell for the selected {missing}; "
                "the audit is refused before any null walk is run"
            )

    def _audit(self, ctx, asset, horizon, observed_r2, walks):
        """Run the seeds in order; stop at the first null that is not beaten."""
        n_draws = 0
        for seed in self.params["seeds"]:
            tag = f"p11-gate3-{asset.lower()}-h{horizon:02d}-seed{seed:02d}"
            document = _derived_document(
                ctx, asset, horizon, tag=tag, scramble_seed=seed
            )
            summary = p10._run_bounded_walk(ctx, document, tag)
            walks[f"{asset}:{horizon}:{seed}"] = summary
            n_draws += 1
            null_r2 = _score_one(summary, asset, horizon, self.params["alpha"])
            if not beat_all(observed_r2, [null_r2["r2oos"]]):
                return {"stopped": True, "stop_seed": seed, "n_draws": n_draws}
        return {"stopped": False, "stop_seed": None, "n_draws": n_draws}


class Gate3ResultStage(Stage):
    """Decide every asset: a stopped audit fails, a completed one is judged.

    A stopped asset (ADR-0092) emits ``gate3_status`` ``fail`` with the
    stop record and the bound ``2 / (n_draws + 1)`` at the top level of
    its row, and no ``gate3`` block. A completed asset calls
    :func:`~dskit.pipeline.attempts.tier2_verdict` over all 19 draws
    exactly as before, so its beat-all and calibration limbs are
    unchanged.

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
        """Require observed Gate-1 rows/cells, the null refits and the stops."""
        needed = {"gate1", "gate1_cells", "walks", "draws"}
        return [] if set(inputs) == needed else [f"requires {sorted(needed)}"]

    def run(self, ctx, inputs):
        """Emit one Gate-3 decision for every frozen asset."""
        del ctx
        gate1 = inputs["gate1"]
        if [row.get("asset") for row in gate1] != self.params["assets"]:
            raise ValueError("Gate 3 result input is not the frozen ordered 25 assets")
        self._refuse_malformed_draws(gate1, inputs["draws"])
        observed = _observed_skill(inputs["gate1_cells"])
        rows = []
        for base in gate1:
            final = {**base, "gate3_status": "not_reached", "gate3_passes": False}
            if not base["gate1_passes"]:
                final["not_reached_reason"] = "gate1_failed_at_h1"
            else:
                final.update(self._decide(base, observed, inputs))
            rows.append(final)
        return {"rows": rows}

    def _refuse_malformed_draws(self, gate1, draws):
        """Refuse a missing or malformed stop record before any verdict."""
        problems = []
        for base in gate1:
            if not base["gate1_passes"]:
                continue
            asset = base["asset"]
            if not isinstance(draws.get(asset), dict):
                problems.append(f"{asset} has no draws record")
                continue
            problems.extend(_draw_problems(asset, draws[asset], self.params["seeds"]))
        if problems:
            raise ValueError(
                "Gate 3 result cannot decide a survivor: " + "; ".join(problems)
            )

    def _decide(self, base, observed, inputs):
        """One survivor's verdict: the stop record, or the full family."""
        asset = base["asset"]
        horizon = base["gate1_h"]
        draw = inputs["draws"][asset]
        if draw["stopped"]:
            return _stopped_row(draw)
        # Each of these walks was scored once already, inside the audit's own
        # stop test. Re-scoring a stored summary is cheap beside the walk that
        # produced it, and the alternative is widening the draws record past
        # the three fields ADR-0092 gives it.
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
        verdict = tier2_verdict(observed[(asset, horizon)]["r2oos"], null_r2, null_t)
        return {
            "gate3_status": "pass" if verdict["passes"] else "fail",
            "gate3_passes": verdict["passes"],
            "gate3": verdict,
            "not_reached_reason": None,
        }
