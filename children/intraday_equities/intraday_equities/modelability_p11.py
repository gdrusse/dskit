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

P11 is the pinned special case of :mod:`.modelability_study` (ADR-0094):
its four stages subclass the study's and supply only what is P11's — the
frozen 25 assets, the frozen horizons and seeds, the one P10 cache verified
against that membership, its historical output contract and its ``p11-``
walk tags. The loop bodies live in the study module, once.
"""

from __future__ import annotations

from dskit.pipeline.stages import reject_unknown_params

from . import modelability as p10
from . import modelability_study as study
from .modelability_study import _PREFLIGHT_ALPHA, _largest_asset, _score_one

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
_SEEDS = list(range(19))


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


def _p10_cache(ctx):
    """Describe the one P10 cache every P11 walk reads, verified once per process."""
    declared, _path, digest = p10._feature_cache_info(ctx)
    return {
        "universe": ctx.document.pipeline["universe"].params["path"],
        "cache": declared,
        "manifest_sha256": digest,
        "symbols": list(_ASSETS),
    }


def _derived_document(ctx, asset, horizon, *, tag="gate1", scramble_seed=None):
    """Build one asset-only derived walk over the P10 cache."""
    if asset not in _ASSETS:
        raise ValueError(f"P11 asset is outside the frozen cohort: {asset!r}")
    if horizon not in _HORIZONS:
        raise ValueError(f"P11 horizon is outside the frozen order: {horizon!r}")
    return study.asset_walk_document(
        ctx.document, _STUDY, asset, horizon, _p10_cache(ctx),
        tag=tag, scramble_seed=scramble_seed,
    )


class MemoryPreflightStage(study.MemoryPreflightStage):
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

    def score_one(self, summary, asset, lead):
        """Score the measured walk through P11's own module-level scorer."""
        # The study's method would resolve the scorer in the study module;
        # P11's tests fake it here, where P11's other seams are faked.
        return _score_one(summary, asset, lead, _PREFLIGHT_ALPHA)

    def run(self, ctx, inputs):
        """Measure one capped Gate-1-shaped asset walk as the first child."""
        del inputs
        _declared, cache_path, digest = p10._feature_cache_info(ctx)
        asset, rows = _largest_asset(cache_path, self.params["assets"])
        # Named for the staged identity: the reading is the seam's
        # measure_one (ADR-0093), which needs a fresh spawn, so a revised
        # study measures again rather than tripping over the previous
        # study's finished preflight walk.
        document = _derived_document(
            ctx, asset, 1, tag=f"preflight-{ctx.document.hash[:8]}"
        )
        measured = self.measure_document(
            ctx, document, asset, 1, f"p11-memory-{asset.lower()}"
        )
        self.refuse_over_limit(measured["peak_rss_bytes"])
        return {
            "asset": asset,
            "feature_rows": rows,
            "summary_dir": measured["summary_dir"],
            "peak_rss_bytes": measured["peak_rss_bytes"],
            "limit_bytes": self.params["memory_limit_bytes"],
            "feature_cache_manifest_sha256": digest,
            "passed": True,
        }


class _Pinned:
    """The hooks every P11 stage shares: the frozen cohort over the P10 cache."""

    def cohort(self, ctx):
        """Return the frozen ordered 25 assets."""
        del ctx
        return list(self.params.get("assets") or _ASSETS)

    def study(self, ctx):
        """Return P11's study name."""
        del ctx
        return _STUDY

    def caches(self, ctx, inputs):
        """Return one placement group: every frozen asset over the P10 cache."""
        del ctx, inputs
        return {"p10": {"symbols": list(_ASSETS)}}

    def tag(self, ctx, kind, asset, horizon, seed=None):
        """Name one walk with P11's short ``p11-`` prefix."""
        del ctx
        prefix = f"p11-{kind}-{asset.lower()}-h{horizon:02d}"
        return prefix if seed is None else f"{prefix}-seed{seed:02d}"

    def document(self, ctx, asset, horizon, cache, *, tag, scramble_seed=None):
        """Derive one asset-only walk over the P10 cache, ignoring ``cache``."""
        del cache
        return _derived_document(ctx, asset, horizon, tag=tag, scramble_seed=scramble_seed)

    def score(self, summary, asset, horizon):
        """Score one asset-local walk at the stage's level."""
        return _score_one(summary, asset, horizon, self.params["alpha"])


class Gate1Stage(_Pinned, study.Gate1Stage):
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

    def ledger_key(self, ctx, gate):
        """Return P11's historical attempt-ledger key."""
        del ctx
        return _base_key(gate)

    def check_asof(self, ctx):
        """P11 pins its cut in the ledger key itself; the invocation is not checked."""
        del ctx


class Gate3WalksStage(_Pinned, study.Gate3WalksStage):
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
        if params.get("seeds") != _SEEDS:
            problems.append("seeds must be exactly [0, ..., 18]")
        return problems

    def validate_inputs(self, inputs):
        """Require the ordered Gate-1 outcomes and their scored cells."""
        needed = {"gate1", "gate1_cells"}
        return [] if set(inputs) == needed else [f"requires {sorted(needed)}"]


class Gate3ResultStage(_Pinned, study.Gate3ResultStage):
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
        if params.get("seeds") != _SEEDS:
            problems.append("seeds must be exactly [0, ..., 18]")
        return problems
