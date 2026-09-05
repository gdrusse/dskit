"""The asset-local modelability study over whatever cohort a document declares.

P11 asked one question of twenty-five names: is each forecastable by a
model trained on that name alone, under the ordered horizon search, and
does the selection survive the whole-session scramble audit? This module
is that study with the cohort taken from the document instead of pinned
in code (ADR-0094). The cohort is the scan's ``fit_symbols`` — graded,
and the one place the document names it. Feature caches are built per
source GROUP under the memory cap, because one build of every name at
once does not fit; each asset belongs to exactly one group, and every
derived walk reads that group's cache, filters to the asset and the
residual reference, and fits and scores the asset alone. Gate 1 is
ADR-0087's ordered stop; Gate 3 is ADR-0092's fail-fast audit.

The four stages expose hooks — the cohort, the caches, the study name,
the ledger key, the walk tags, the runner — so a pinned study such as P11
subclasses them and supplies only what is its own. The loop bodies exist
once, here.
"""

from __future__ import annotations

import json
import os

from dskit.pipeline.attempts import (
    AttemptRegistry,
    beat_all,
    early_stop_p_bound,
    tier2_verdict,
)
from dskit.pipeline.document import PipelineDocument
from dskit.pipeline.runs import score_walk
from dskit.pipeline.stages import Stage, reject_unknown_params

from . import modelability as p10

__all__ = [
    "Gate1Stage",
    "Gate3ResultStage",
    "Gate3WalksStage",
    "MemoryPreflightStage",
    "asset_walk_document",
    "cache_build_document",
]

_MS_PER_MINUTE = 60_000
_BARS_KIND = "intraday_equities-bars"
_GROUP_KEY_OK = "abcdefghijklmnopqrstuvwxyz0123456789_"


# --------------------------------------------------------------- the document


def _document_cohort(document):
    """Return the study's cohort: the scan's ``fit_symbols``, its one owner."""
    symbols = document.pipeline["scan"].params.get("fit_symbols")
    if (
        not isinstance(symbols, list)
        or not symbols
        or len(set(symbols)) != len(symbols)
    ):
        raise ValueError(
            "the study's cohort is scan.params.fit_symbols, which must be a "
            f"non-empty list of unique symbols; got {symbols!r}"
        )
    return list(symbols)


def _reference(document):
    """Return the scan's residual reference symbol; the study requires one."""
    residual = document.pipeline["scan"].params.get("label_residual")
    if not isinstance(residual, str) or not residual:
        raise ValueError(
            "the study requires scan.params.label_residual: the reference "
            "symbol every group cache carries and every asset walk keeps as tape"
        )
    return residual


def _universe_path(ctx, path):
    """Resolve a universe path against the child root when it is relative."""
    return path if os.path.isabs(path) else os.path.join(p10._child_root(ctx), path)


def _universe_spec(ctx, path):
    """Load one universe file."""
    with open(_universe_path(ctx, path), encoding="utf-8") as handle:
        return json.load(handle)


def _universe_digest(ctx, path):
    """Return the SHA-256 of one universe file's bytes."""
    return p10._file_sha256(_universe_path(ctx, path))


def _date_ok(value):
    """Report whether ``value`` is a ``YYYY-MM-DD`` string."""
    from datetime import date

    try:
        return isinstance(value, str) and date.fromisoformat(value).isoformat() == value
    except ValueError:
        return False


def _place(cohort, caches):
    """Map each asset to the ONE group whose cache holds it."""
    placement = {}
    for asset in cohort:
        holders = [group for group, entry in caches.items() if asset in entry["symbols"]]
        if not holders:
            raise ValueError(
                f"{asset} is in no group cache ({sorted(caches)}); every fit "
                "symbol must be held by exactly one group"
            )
        if len(holders) > 1:
            raise ValueError(
                f"{asset} is in two groups or more ({holders}); a fit symbol "
                "belongs to exactly one — the reference symbol sits in every "
                "group and is never a fit symbol"
            )
        placement[asset] = holders[0]
    return placement


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


def _filter(records_ref, op, value):
    """One ``filter`` node keeping the rows whose ``symbol`` ``op`` ``value``."""
    return {
        "uses": "filter",
        "inputs": {"records": records_ref},
        "params": {"where": [{"field": "symbol", "op": op, "value": value}]},
    }


def _scan_for(obj, symbols, horizon, scramble_seed=None):
    """Rewrite the template's scan to fit ``symbols`` at one lead."""
    scan = obj["pipeline"]["scan"]
    scan["inputs"]["records"] = "$asset_features.records"
    scan["inputs"]["bars"] = "$reference_tape.records"
    params = scan["params"]
    params["lead_start"] = horizon
    params["lead_step"] = horizon
    params["lead_stop"] = horizon
    params["fit_symbols"] = list(symbols)
    params["score_symbols"] = list(symbols)
    params.pop("label_scramble_seed", None)
    if scramble_seed is not None:
        if isinstance(scramble_seed, bool) or not isinstance(scramble_seed, int):
            raise ValueError(f"scramble seed must be an integer: {scramble_seed!r}")
        params["label_scramble_seed"] = scramble_seed
    return scan


def asset_walk_document(document, study, asset, horizon, cache, *, tag, scramble_seed=None):
    """Derive one asset-local walk over one group cache.

    Parameters
    ----------
    document : dskit.pipeline.document.PipelineDocument
        The staged study document; its scan and walk-forward sections
        are the template.
    study : str
        The study name the derived document's name starts with.
    asset : str
        The one symbol the walk fits and scores.
    horizon : int
        The lead, in minutes, used as ``lead_start``/``step``/``stop``.
    cache : dict
        A group entry from the memory stage: ``universe`` (the group
        universe path), ``cache`` (the declared cache path) and
        ``manifest_sha256``.
    tag : str
        The walk's purpose, embedded in the name (``gate1``,
        ``gate3-seed03``, ``preflight-…``).
    scramble_seed : int or None
        Declared, the walk is a whole-session null draw (ADR-0074).

    Returns
    -------
    dskit.pipeline.document.PipelineDocument
        ``universe`` → the group universe; ``features`` → the verified
        cache; ``asset_features`` and ``reference_tape`` filters; the
        scan fitting and scoring ``[asset]`` alone; no stages.

    Raises
    ------
    ValueError
        When ``scramble_seed`` is not an int.

    Examples
    --------
    Seed three of MSTR's Gate-3 audit at five minutes::

        walk = asset_walk_document(
            document, "p12-40-asset-modelability", "MSTR", 5, caches["e"],
            tag="gate3-seed03", scramble_seed=3,
        )
        walk.pipeline["scan"].params["fit_symbols"]  # ['MSTR']
    """
    obj = document.to_obj()
    obj.pop("stages", None)
    obj["name"] = f"{study}-{tag}-{asset.lower()}-h{horizon:02d}"
    scan = _scan_for(obj, [asset], horizon, scramble_seed)
    residual = scan["params"].get("label_residual")
    obj["pipeline"] = {
        "universe": {
            "uses": "intraday_equities-universe",
            "params": {"path": cache["universe"]},
        },
        "features": {
            "uses": "intraday_equities-session-feature-cache",
            "params": {
                "path": cache["cache"],
                "manifest_sha256": cache["manifest_sha256"],
            },
        },
        "asset_features": _filter("$features.records", "==", asset),
        "reference_tape": _filter("$features.tape", "in", _tape_symbols(asset, residual)),
        "scan": scan,
    }
    return PipelineDocument.from_obj(obj)


def cache_build_document(document, group, sources, universe, cache_dir, cutoff):
    """Derive the one-fold walk that builds and verifies one group's cache.

    Parameters
    ----------
    document : dskit.pipeline.document.PipelineDocument
        The staged study document.
    group : str
        The group key; names the derived document.
    sources : list of str
        The bars node keys this group reads — its own source and the
        reference's.
    universe : str
        The group universe path, which bounds every read.
    cache_dir : str
        Where the ``features`` node persists the group's cache.
    cutoff : str
        The one fold's cutoff, ``YYYY-MM-DD``.

    Returns
    -------
    dskit.pipeline.document.PipelineDocument
        The group universe, the listed bars nodes bounded by it, a
        ``concat`` of them, the document's ``features`` node writing the
        group cache, and the scan fitting the reference symbol alone
        behind two filters, as a one-fold walk at ``cutoff``.

    Raises
    ------
    ValueError
        When a source key is unknown or not a bars node, or when the
        document declares no ``label_residual``.

    Examples
    --------
    Group D's build at the last fold::

        build = cache_build_document(
            document, "d", ["source_reference", "source_d"],
            "configs/universe-p12-d.json", "./pipeline_cache/p12/d", "2025-08-15",
        )
        build.walkforward.count  # 1
    """
    reference = _reference(document)
    obj = document.to_obj()
    obj.pop("stages", None)
    obj["name"] = f"{document.name}-cache-{group}"
    original = obj["pipeline"]
    pipeline = {"universe": {"uses": "intraday_equities-universe", "params": {"path": universe}}}
    for key in sources:
        node = original.get(key)
        if node is None:
            raise ValueError(f"group {group!r} names an unknown pipeline node {key!r}")
        if node.get("uses") != _BARS_KIND:
            raise ValueError(f"group {group!r} source {key!r} is not a {_BARS_KIND} node")
        node = dict(node)
        node["params"] = {**node["params"], "universe": universe}
        pipeline[key] = node
    pooled = dict(original["pooled"])
    pooled["inputs"] = {key.replace("source_", "", 1): f"${key}.records" for key in sources}
    pipeline["pooled"] = pooled
    features = dict(original["features"])
    features["params"] = {**features["params"], "cache_dir": cache_dir}
    pipeline["features"] = features
    pipeline["reference_features"] = _filter("$features.records", "==", reference)
    pipeline["reference_tape"] = _filter("$features.tape", "in", [reference])
    lead = original["scan"]["params"]["lead_start"]
    scan = _scan_for(obj, [reference], lead)
    scan["inputs"]["records"] = "$reference_features.records"
    pipeline["scan"] = scan
    obj["pipeline"] = pipeline
    obj["walkforward"]["first"] = cutoff
    obj["walkforward"]["count"] = 1
    return PipelineDocument.from_obj(obj)


# --------------------------------------------------------------- the readers


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
            f"expected one exact row for {asset}@{horizon}; "
            f"exact={scored['exact']} rows={scored['rows']!r}"
        )
    return rows[0]


def _largest_asset(cache_path, assets):
    """Return the first asset with the largest cached feature frame."""
    import numpy as np

    sizes = []
    for asset in assets:
        path = os.path.join(cache_path, f"{asset}.features.X.npy")
        rows = int(np.load(path, mmap_mode="r", allow_pickle=False).shape[0])
        sizes.append((rows, asset))
    largest = max(rows for rows, _asset in sizes)
    return next(asset for rows, asset in sizes if rows == largest), largest


def _verified_cache(path, group_universe, features_params):
    """Verify one group cache on disk; ``None`` when absent, else its identity."""
    manifest = os.path.join(path, "manifest.json")
    if not os.path.isfile(manifest):
        return None
    with open(manifest, encoding="utf-8") as handle:
        payload = json.load(handle)
    metadata = payload.get("metadata") or {}
    # The universe node patches derived keys (the feature-name list) into
    # the spec it emits, so the recorded spec is the raw file plus those;
    # every key the raw file declares must match, and the params exactly.
    spec = metadata.get("spec") or {}
    moved = [key for key, value in group_universe.items() if spec.get(key) != value]
    if metadata.get("params") != features_params or moved:
        raise ValueError(
            f"feature cache {path} was built under other metadata than this "
            f"document's features params and group universe (differs at "
            f"{moved or 'params'}); it cannot be reused"
        )
    digest = p10._verify_cache_once(os.path.abspath(path))
    return {"manifest_sha256": digest, "symbols": list(payload.get("symbols") or [])}


def _observed_skill(cells):
    """Index the Gate-1 cells by ``(asset, horizon)`` for the audit."""
    return {(row["asset"], row["horizon"]): row["skill"] for row in cells}


def _stopped_row(draw):
    """Emit the ADR-0092 fields of an asset whose audit stopped early."""
    # Top-level on the row, not inside a gate3 block: tier2_verdict never
    # ran, so there is no block, and calibration is not claimed.
    n_draws = draw["n_draws"]
    return {
        "gate3_status": "fail",
        "gate3_passes": False,
        "not_reached_reason": None,
        "stopped": True,
        "stop_seed": draw.get("stop_seed"),
        "n_draws": n_draws,
        "p_bound": early_stop_p_bound(n_draws),
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
    elif stopped is True and n_draws != list(seeds).index(stop_seed) + 1:
        problems.append(
            f"{asset} stopped at stop_seed={stop_seed!r} but n_draws={n_draws!r} "
            f"is not the {list(seeds).index(stop_seed) + 1} seeds run in order "
            "before it"
        )
    if stopped is False and stop_seed is not None:
        problems.append(f"{asset} stop_seed={stop_seed!r} but the audit completed")
    if stopped is False and n_draws != len(seeds):
        problems.append(
            f"{asset} did not stop but n_draws={n_draws!r} is not the "
            f"{len(seeds)} declared seeds"
        )
    return problems


# ---------------------------------------------------------------- validation


def _check_alpha(problems, params):
    """Append the problem with ``alpha`` unless it lies in (0, 1)."""
    value = params.get("alpha")
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not 0.0 < float(value) < 1.0
    ):
        problems.append("alpha must lie in (0, 1)")


def _check_int_list(problems, name, values, *, ordered):
    """Append the problems with a list of unique non-negative ints."""
    ok = (
        isinstance(values, list)
        and bool(values)
        and all(isinstance(v, int) and not isinstance(v, bool) and v >= 0 for v in values)
        and len(set(values)) == len(values)
    )
    if not ok:
        problems.append(f"{name} must be a non-empty list of unique non-negative ints")
        return
    if ordered and (values != sorted(values) or values[0] < 1):
        problems.append(f"{name} must be strictly increasing positive ints")


def _check_groups(problems, groups):
    """Append the problems with the memory stage's ``groups`` mapping."""
    if not isinstance(groups, dict) or not groups:
        problems.append("groups must be a non-empty object of group -> {universe, sources}")
        return
    for key, spec in groups.items():
        if not isinstance(key, str) or not key or any(c not in _GROUP_KEY_OK for c in key):
            problems.append(f"group key {key!r} must be a short lowercase path-safe name")
        if not isinstance(spec, dict) or set(spec) != {"universe", "sources"}:
            problems.append(f"group {key!r} must declare exactly universe and sources")
            continue
        if not isinstance(spec["universe"], str) or not spec["universe"]:
            problems.append(f"group {key!r} universe must be a path string")
        sources = spec["sources"]
        if (
            not isinstance(sources, list)
            or not sources
            or any(not isinstance(s, str) or not s for s in sources)
            or len(set(sources)) != len(sources)
        ):
            problems.append(f"group {key!r} sources must be a non-empty list of node keys")


# -------------------------------------------------------------------- stages


class _StudyStage(Stage):
    """The hooks every study stage shares; a pinned study overrides them."""

    def cohort(self, ctx):
        """Return the ordered cohort: the document's ``fit_symbols``."""
        return _document_cohort(ctx.document)

    def study(self, ctx):
        """Return the study name derived documents and tags start with."""
        return ctx.document.name

    def caches(self, ctx, inputs):
        """Return the group caches keyed by group, from the memory stage."""
        del ctx
        return inputs["caches"]

    def walk(self, ctx, document, tag):
        """Run one derived walk through the bounded seam and return its summary."""
        return p10._run_bounded_walk(ctx, document, tag)

    def score(self, summary, asset, horizon):
        """Score one asset-local walk at the stage's level."""
        return _score_one(summary, asset, horizon, self.params["alpha"])

    def tag(self, ctx, kind, asset, horizon, seed=None):
        """Name one walk's derived config and journal rows."""
        prefix = f"{self.study(ctx)}-{kind}-{asset.lower()}-h{horizon:02d}"
        return prefix if seed is None else f"{prefix}-seed{seed:02d}"

    def document(self, ctx, asset, horizon, cache, *, tag, scramble_seed=None):
        """Derive one asset-local walk over ``cache``."""
        return asset_walk_document(
            ctx.document, self.study(ctx), asset, horizon, cache,
            tag=tag, scramble_seed=scramble_seed,
        )

    def check_asof(self, ctx):
        """Refuse to run as of any date but the study's declared data cut."""
        if ctx.asof != self.params["data_cut"]:
            raise ValueError(
                f"the study runs as of its data_cut {self.params['data_cut']}, "
                f"not {ctx.asof!r}: the ledger records the cut, never an invocation"
            )

    def refuse_moved_universes(self, ctx, caches):
        """Refuse a group whose universe file changed since the memory stage pinned it."""
        moved = []
        for group, entry in caches.items():
            pinned = entry.get("universe_sha256")
            if pinned is not None and _universe_digest(ctx, entry["universe"]) != pinned:
                moved.append(group)
        if moved:
            raise ValueError(
                f"group universe(s) {moved} changed since the memory stage pinned "
                "them; a changed cohort universe is a new study identity"
            )


class MemoryPreflightStage(Stage):
    """Build each group's feature cache under the cap and measure one child.

    Parameters
    ----------
    params : dict
        ``memory_limit_bytes`` (positive int) and ``groups``, a mapping
        of group key to ``{"universe": <group universe path>, "sources":
        [<bars node keys>]}``.

    Examples
    --------
    Two groups sharing the reference source::

        stage = MemoryPreflightStage("memory", {
            "memory_limit_bytes": 17 * 1024**3,
            "groups": {
                "d": {"universe": "configs/universe-p12-d.json",
                      "sources": ["source_reference", "source_d"]},
                "e": {"universe": "configs/universe-p12-e.json",
                      "sources": ["source_reference", "source_e"]},
            },
        })
    """

    outputs = ("groups", "measured", "limit_bytes", "passed")
    _PARAMS = ("memory_limit_bytes", "groups")

    @classmethod
    def validate_params(cls, params):
        """Validate the byte limit and the group mapping."""
        problems = []
        reject_unknown_params(problems, params, cls._PARAMS)
        limit = params.get("memory_limit_bytes")
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            problems.append("memory_limit_bytes must be a positive int")
        _check_groups(problems, params.get("groups"))
        return problems

    def run(self, ctx, inputs):
        """Reuse or build every group cache; measure the first build."""
        del inputs
        features = ctx.document.pipeline["features"].params
        declared = features.get("cache_dir")
        if not isinstance(declared, str) or not declared:
            raise ValueError("the study's features node must declare cache_dir")
        expected = {k: v for k, v in features.items() if k != "cache_dir"}
        groups = {}
        measured = None
        for group, spec in self.params["groups"].items():
            entry, measured = self._group(ctx, group, spec, declared, expected, measured)
            groups[group] = entry
        if measured is None:
            measured = self._measure_asset_fold(ctx, groups)
        peak = measured["peak_rss_bytes"]
        if peak >= self.params["memory_limit_bytes"]:
            raise MemoryError(
                f"preflight peak {peak!r} is not strictly below "
                f"{self.params['memory_limit_bytes']}"
            )
        return {
            "groups": groups,
            "measured": measured,
            "limit_bytes": self.params["memory_limit_bytes"],
            "passed": True,
        }

    def _group(self, ctx, group, spec, declared, expected, measured):
        """Reuse or build one group cache; return its entry and the reading."""
        universe = _universe_spec(ctx, spec["universe"])
        digest = _universe_digest(ctx, spec["universe"])
        # Versioned by the universe that bounds it: an edited universe names
        # a fresh cache instead of colliding with the write-once directory
        # the old one built.
        cache = f"{declared}/{group}-{digest[:8]}"
        path = cache if os.path.isabs(cache) else os.path.join(p10._child_root(ctx), cache)
        state = _verified_cache(path, universe, expected)
        if state is None:
            cutoff = p10._last_first(ctx.document.walkforward)
            build = cache_build_document(
                ctx.document, group, spec["sources"], spec["universe"], cache, cutoff
            )
            tag = f"{ctx.document.name}-cache-{group}"
            if measured is None:
                summary, peak = p10._measure_walk(ctx, build, tag)
                measured = {
                    "kind": "cache_build",
                    "name": group,
                    "summary_dir": summary,
                    "peak_rss_bytes": peak,
                }
            else:
                p10._run_bounded_walk(ctx, build, tag)
            state = _verified_cache(path, universe, expected)
            if state is None:
                raise RuntimeError(f"the {group} cache build left no cache at {path}")
        if set(state["symbols"]) != set(universe["symbols"]):
            raise ValueError(
                f"feature cache {path} membership {sorted(state['symbols'])} is not "
                f"the group universe {sorted(universe['symbols'])}"
            )
        entry = {
            "universe": spec["universe"],
            "universe_sha256": digest,
            "cache": cache,
            "manifest_sha256": state["manifest_sha256"],
            "symbols": list(state["symbols"]),
        }
        return entry, measured

    def _measure_asset_fold(self, ctx, groups):
        """With nothing built, measure the largest cached asset's one fold."""
        cohort = _document_cohort(ctx.document)
        placement = _place(cohort, groups)
        candidates = []
        for group, entry in groups.items():
            cache = entry["cache"]
            path = cache if os.path.isabs(cache) else os.path.join(p10._child_root(ctx), cache)
            assets = [asset for asset in cohort if placement[asset] == group]
            if assets:
                asset, rows = _largest_asset(path, assets)
                candidates.append((rows, cohort.index(asset), asset, entry))
        _rows, _order, asset, entry = min(candidates, key=lambda c: (-c[0], c[1]))
        lead = ctx.document.pipeline["scan"].params["lead_start"]
        document = asset_walk_document(
            ctx.document, ctx.document.name, asset, lead, entry,
            tag=f"preflight-{ctx.document.hash[:8]}",
        )
        obj = document.to_obj()
        obj["walkforward"]["first"] = p10._last_first(ctx.document.walkforward)
        obj["walkforward"]["count"] = 1
        document = PipelineDocument.from_obj(obj)
        tag = f"{ctx.document.name}-memory-{asset.lower()}"
        summary, peak = p10._measure_walk(ctx, document, tag)
        _score_one(summary, asset, lead, 0.05)
        return {
            "kind": "asset_fold",
            "name": asset,
            "summary_dir": summary,
            "peak_rss_bytes": peak,
        }


class Gate1Stage(_StudyStage):
    """Fit each asset alone and stop its ordered horizon walk on failure.

    Parameters
    ----------
    params : dict
        Ordered ``horizons``, the ``attempt_registry`` path, the single
        cell ``alpha``, the ``architecture`` label the ledger key carries
        and the ``data_cut`` (``YYYY-MM-DD``) the study runs as of.

    Examples
    --------
    Instantiate the sequential gate::

        stage = Gate1Stage("gate1", {
            "horizons": [1, 2, 3, 5, 10, 20, 30, 60],
            "attempt_registry": "docs/decisioning/attempts.jsonl",
            "alpha": 0.05,
            "architecture": "lgbm-tight-asset-local",
            "data_cut": "2026-02-28",
        })
    """

    outputs = ("rows", "cells")
    _PARAMS = ("horizons", "attempt_registry", "alpha", "architecture", "data_cut")

    @classmethod
    def validate_params(cls, params):
        """Validate the horizon order, ledger path, level, label and cut."""
        problems = []
        reject_unknown_params(problems, params, cls._PARAMS)
        _check_int_list(problems, "horizons", params.get("horizons"), ordered=True)
        if not isinstance(params.get("attempt_registry"), str):
            problems.append("attempt_registry must be a path string")
        _check_alpha(problems, params)
        label = params.get("architecture")
        if not isinstance(label, str) or not label:
            problems.append("architecture must be a non-empty label")
        if not _date_ok(params.get("data_cut")):
            problems.append("data_cut must be a YYYY-MM-DD date")
        return problems

    def validate_inputs(self, inputs):
        """Require a passed memory preflight and the group caches."""
        if set(inputs) != {"preflight", "caches"}:
            return ["requires preflight and caches"]
        if inputs["preflight"] is not True:
            return ["requires passed preflight"]
        return []

    def ledger_key(self, ctx, gate):
        """Return the attempt knobs shared by one evidence block."""
        universe = _universe_spec(ctx, ctx.document.pipeline["universe"].params["path"])
        return {
            "study": self.study(ctx),
            "architecture": self.params["architecture"],
            "data_cut": self.params["data_cut"],
            "evidence": gate,
            "row_spacing_minutes": universe["period_ms"] // _MS_PER_MINUTE,
            "score_lattice_minutes": (
                ctx.document.pipeline["scan"].params["score_period_ms"] // _MS_PER_MINUTE
            ),
        }

    def run(self, ctx, inputs):
        """Run, score and register only consecutive asset-local cells."""
        self.check_asof(ctx)
        cohort = self.cohort(ctx)
        caches = self.caches(ctx, inputs)
        self.refuse_moved_universes(ctx, caches)
        placement = _place(cohort, caches)
        ledger_path = os.path.join(p10._child_root(ctx), self.params["attempt_registry"])
        attempts = AttemptRegistry(ledger_path)
        base_key = self.ledger_key(ctx, "gate1-selection")
        cells = []
        rows = []
        for asset in cohort:
            rows.append(self._search(ctx, asset, caches[placement[asset]], attempts, base_key, cells))
        return {"rows": rows, "cells": cells}

    def _search(self, ctx, asset, cache, attempts, base_key, cells):
        """Walk one asset's horizons in order; stop at the first failure."""
        selected = None
        first_failed = None
        attempted = []
        for horizon in self.params["horizons"]:
            tag = self.tag(ctx, "gate1", asset, horizon)
            document = self.document(ctx, asset, horizon, cache, tag="gate1")
            summary = self.walk(ctx, document, tag)
            skill = self.score(summary, asset, horizon)
            cell = attempts.record(
                {**base_key, "series": asset, "horizon": horizon},
                walk=summary,
                t_pool=skill["t_pool"],
                t_fold=skill["t_fold"],
                r2oos=skill["r2oos"],
                n_folds=skill["n_folds"],
                study_gate="gate1",
            )
            cells.append(
                {"cell": cell, "asset": asset, "horizon": horizon, "walk": summary, "skill": skill}
            )
            attempted.append(horizon)
            if skill.get("passes") is True:
                selected = horizon
                continue
            first_failed = horizon
            break
        return {
            "asset": asset,
            "gate1_h": selected,
            "gate1_passes": selected is not None,
            "first_failed_h": first_failed,
            "attempted_horizons": attempted,
            "unrun_horizons": [h for h in self.params["horizons"] if h not in attempted],
        }


class Gate3WalksStage(_StudyStage):
    """Refit each Gate-1 survivor against whole-session nulls, stopping early.

    The declared seeds run in order and an asset stops at the first
    completed null whose ``r2oos`` matches or beats the real walk's,
    i.e. when :func:`~dskit.pipeline.attempts.beat_all` is false for
    that one draw (ADR-0092). A pass is never stopped: an asset that
    beats every null runs every seed and carries a full calibration family.

    Parameters
    ----------
    params : dict
        ``seeds``, unique non-negative ints in the order they run;
        ``alpha`` is passed unchanged to the walk scorer.

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
        """Validate the seed list and the level."""
        problems = []
        reject_unknown_params(problems, params, cls._PARAMS)
        _check_int_list(problems, "seeds", params.get("seeds"), ordered=False)
        _check_alpha(problems, params)
        return problems

    def validate_inputs(self, inputs):
        """Require the ordered Gate-1 outcomes, their cells and the caches."""
        needed = {"gate1", "gate1_cells", "caches"}
        return [] if set(inputs) == needed else [f"requires {sorted(needed)}"]

    def run(self, ctx, inputs):
        """Audit each selected asset-local horizon, one null at a time."""
        gate1 = inputs["gate1"]
        cohort = self.cohort(ctx)
        if [row.get("asset") for row in gate1] != cohort:
            raise ValueError("Gate 3 input is not the ordered cohort")
        caches = self.caches(ctx, inputs)
        self.refuse_moved_universes(ctx, caches)
        placement = _place(cohort, caches)
        observed = _observed_skill(inputs["gate1_cells"])
        survivors = [row for row in gate1 if row["gate1_passes"]]
        self._refuse_unscored_cells(survivors, observed)
        walks = {}
        draws = {}
        for row in survivors:
            asset = row["asset"]
            horizon = row["gate1_h"]
            draws[asset] = self._audit(
                ctx, asset, horizon, caches[placement[asset]],
                observed[(asset, horizon)]["r2oos"], walks,
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

    def _audit(self, ctx, asset, horizon, cache, observed_r2, walks):
        """Run the seeds in order; stop at the first null that is not beaten."""
        n_draws = 0
        for seed in self.params["seeds"]:
            tag = self.tag(ctx, "gate3", asset, horizon, seed)
            document = self.document(
                ctx, asset, horizon, cache, tag=f"gate3-seed{seed:02d}", scramble_seed=seed
            )
            summary = self.walk(ctx, document, tag)
            walks[f"{asset}:{horizon}:{seed}"] = summary
            n_draws += 1
            null = self.score(summary, asset, horizon)
            if not beat_all(observed_r2, [null["r2oos"]]):
                return {"stopped": True, "stop_seed": seed, "n_draws": n_draws}
        return {"stopped": False, "stop_seed": None, "n_draws": n_draws}


class Gate3ResultStage(_StudyStage):
    """Decide every asset: a stopped audit fails, a completed one is judged.

    A stopped asset (ADR-0092) emits ``gate3_status`` ``fail`` with the
    stop record and the bound ``2 / (n_draws + 1)`` at the top level of
    its row, and no ``gate3`` block. A completed asset calls
    :func:`~dskit.pipeline.attempts.tier2_verdict` over every declared
    seed, so its beat-all and calibration limbs are P11's.

    Parameters
    ----------
    params : dict
        ``seeds`` pins the null draws; ``alpha`` is passed unchanged to
        the walk scorer.

    Examples
    --------
    Instantiate the final audit::

        stage = Gate3ResultStage("gate3", {"seeds": list(range(19)), "alpha": 0.05})
    """

    outputs = ("rows",)
    _PARAMS = ("seeds", "alpha")

    @classmethod
    def validate_params(cls, params):
        """Validate the seed list and the level."""
        problems = []
        reject_unknown_params(problems, params, cls._PARAMS)
        _check_int_list(problems, "seeds", params.get("seeds"), ordered=False)
        _check_alpha(problems, params)
        return problems

    def validate_inputs(self, inputs):
        """Require observed Gate-1 rows/cells, the null refits and the stops."""
        needed = {"gate1", "gate1_cells", "walks", "draws"}
        return [] if set(inputs) == needed else [f"requires {sorted(needed)}"]

    def run(self, ctx, inputs):
        """Emit one Gate-3 decision for every asset of the cohort."""
        gate1 = inputs["gate1"]
        if [row.get("asset") for row in gate1] != self.cohort(ctx):
            raise ValueError("Gate 3 result input is not the ordered cohort")
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
        # Each of these walks was scored once already, inside the audit's
        # own stop test. Re-scoring a stored summary is cheap beside the
        # walk that produced it, and the alternative is widening the draws
        # record past the three fields ADR-0092 gives it.
        null_r2 = []
        null_t = []
        for seed in self.params["seeds"]:
            skill = self.score(inputs["walks"][f"{asset}:{horizon}:{seed}"], asset, horizon)
            null_r2.append(skill["r2oos"])
            null_t.append(skill["t_pool"])
        verdict = tier2_verdict(observed[(asset, horizon)]["r2oos"], null_r2, null_t)
        return {
            "gate3_status": "pass" if verdict["passes"] else "fail",
            "gate3_passes": verdict["passes"],
            "gate3": verdict,
            "not_reached_reason": None,
        }
