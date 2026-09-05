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
import math
import os

from dskit.pipeline.attempts import (
    AttemptRegistry,
    beat_all,
    early_stop_p_bound,
    tier2_verdict,
)
from dskit.journal import find_journal
from dskit.journal.store import read_actions
from dskit.pipeline.document import PipelineDocument, load_document
from dskit.pipeline.driver import FOLD_FIELDS, FOLD_OPTIONAL_FIELDS
from dskit.pipeline.runs import score_walk
from dskit.pipeline.stages import Stage, reject_unknown_params

from . import modelability as p10

__all__ = [
    "Gate1Stage",
    "Gate3RecoveryInventoryStage",
    "Gate3ContinuationStage",
    "Gate3ResultStage",
    "Gate3WalksStage",
    "MemoryPreflightStage",
    "asset_walk_document",
    "cache_build_document",
]

_MS_PER_MINUTE = 60_000
_BARS_KIND = "intraday_equities-bars"
_GROUP_KEY_OK = "abcdefghijklmnopqrstuvwxyz0123456789_"
# Only labels the preflight's one scored row; no selection is decided here.
_PREFLIGHT_ALPHA = 0.05


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
    """Name the only tapes an asset-local walk reads."""
    # The residual is read from the document rather than restated here: a
    # second copy of "SPY" would silently drop the reference tape the moment
    # the config named a different one, and only for the assets that are not
    # themselves the residual.
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
        When a source key is unknown or not a bars node, when two
        sources name one pooled input, or when the document declares
        no ``label_residual``.

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
    # The input name is the node key less its "source_" prefix, so two
    # distinct bars nodes can collapse onto one name and drop a cohort.
    inputs = {}
    for key in sources:
        name = key.replace("source_", "", 1)
        if name in inputs:
            raise ValueError(
                f"group {group!r} sources {sources} name one pooled input "
                f"{name!r} twice; each source must reach the concat separately"
            )
        inputs[name] = f"${key}.records"
    pooled["inputs"] = inputs
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
        """Return the ordered cohort: the document's tradable ``fit_symbols``."""
        cohort = _document_cohort(ctx.document)
        path = ctx.document.pipeline["universe"].params["path"]
        tradable = _universe_spec(ctx, path).get("tradable") or []
        outside = [symbol for symbol in cohort if symbol not in tradable]
        if outside:
            raise ValueError(
                f"fit symbol(s) {outside} are not tradable in {path}; the "
                "study fits only names its own universe lists as tradable"
            )
        return cohort

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
        self.refuse_over_limit(measured["peak_rss_bytes"])
        return {
            "groups": groups,
            "measured": measured,
            "limit_bytes": self.params["memory_limit_bytes"],
            "passed": True,
        }

    def measure_document(self, ctx, document, asset, lead, tag):
        """Measure one capped child walk and score the single row it must produce."""
        summary, peak = p10._measure_walk(ctx, document, tag)
        self.score_one(summary, asset, lead)
        return {
            "kind": "asset_fold",
            "name": asset,
            "summary_dir": summary,
            "peak_rss_bytes": peak,
        }

    def score_one(self, summary, asset, lead):
        """Score the measured walk's one row; a pinned study routes it its own way."""
        return _score_one(summary, asset, lead, _PREFLIGHT_ALPHA)

    def refuse_over_limit(self, peak):
        """Refuse a measured peak that is not strictly below the declared cap."""
        limit = self.params["memory_limit_bytes"]
        if peak >= limit:
            raise MemoryError(f"preflight peak {peak!r} is not strictly below {limit}")

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
        return self.measure_document(ctx, document, asset, lead, tag)


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

# ---------------------------------------------------------- P12 recovery seam


def _strict_survivors(rows):
    """Derive selected families only from persisted rows with an exact true flag."""
    return [
        {"asset": row["asset"], "horizon": row["gate1_h"]}
        for row in rows
        if row.get("gate1_passes") is True
    ]


def _read_recovery_stage(path, key, source_hash):
    """Read one exact completed source-stage envelope."""
    try:
        with open(path, encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, ValueError) as error:
        raise ValueError(f"unreadable source stage artifact {path}") from error
    expected_token = f"{source_hash}:{key}"
    if (
        not isinstance(payload, dict)
        or payload.get("stage") != key
        or payload.get("stage_token") != expected_token
        or payload.get("state") != "ran"
        or not isinstance(payload.get("outputs"), dict)
    ):
        raise ValueError(
            f"{path} is not the completed {expected_token} source-stage artifact"
        )
    return payload["outputs"]


def _matching_stage_actions(actions, artifact, token):
    """Return journal IDs proving one persisted stage artifact."""
    digest = p10._file_sha256(artifact)
    artifact = os.path.abspath(artifact)
    return [
        row.id
        for row in actions
        if row.category == "execute"
        and os.path.abspath(row.outputs) == artifact
        and f"stage_token={token}" in row.notes
        and "state=ran" in row.notes
        and f"sha256={digest}" in row.notes
    ]


def _matching_walk_actions(actions, summary, document, asof):
    """Return journal IDs proving one exact derived walk summary."""
    summary = os.path.abspath(summary)
    return [
        row.id
        for row in actions
        if row.category == "execute"
        and os.path.abspath(row.outputs) == summary
        and f"hash={document.hash[:8]}" in row.notes
        and f"asof={asof}" in row.notes
        and "state=ran" in row.notes
    ]


def _ran_fold_problems(row, cutoff):
    """Return fail-closed structural problems for one persisted ran fold."""
    if not isinstance(row, dict):
        return [f"fold {cutoff} is not an object"]
    missing = [key for key in FOLD_FIELDS if key not in row]
    extra = sorted(set(row) - (set(FOLD_FIELDS) | set(FOLD_OPTIONAL_FIELDS)))
    score = row.get("score")
    problems = []
    if missing:
        problems.append(f"fold {cutoff} missing {missing}")
    if extra:
        problems.append(f"fold {cutoff} has unsupported keys {extra}")
    if row.get("cutoff") != cutoff:
        problems.append(f"fold cutoff {row.get('cutoff')!r} != {cutoff!r}")
    if row.get("state") != "ran" or "error" in row:
        problems.append(
            f"fold {cutoff} state={row.get('state')!r} error={row.get('error')!r}"
        )
    if (
        isinstance(score, bool)
        or not isinstance(score, (int, float))
        or not math.isfinite(score)
    ):
        problems.append(f"fold {cutoff} score is not finite numeric: {score!r}")
    run_dir = row.get("run_dir")
    if not isinstance(run_dir, str) or not os.path.isdir(run_dir):
        problems.append(f"fold {cutoff} run_dir is not present: {run_dir!r}")
    return problems


def _skill_problems(label, skill):
    """Return missing or non-finite verdict inputs for one scored walk."""
    if not isinstance(skill, dict):
        return [f"{label} skill is not an object"]
    problems = []
    for key in ("r2oos", "t_pool", "t_fold"):
        value = skill.get(key)
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
        ):
            problems.append(
                f"{label} {key} is not finite numeric: {value!r}"
            )
    return problems


def _walk_evidence(document, summary, asof, actions):
    """Inventory one expected walk and its journal evidence without mutation."""
    record_path = os.path.join(summary, "walkforward.json")
    report_path = os.path.join(summary, "report.md")
    problems = []
    payload = None
    try:
        with open(record_path, encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, ValueError) as error:
        problems.append(f"unreadable walkforward.json: {type(error).__name__}")
    if not os.path.isfile(report_path):
        problems.append("missing report.md")
    if isinstance(payload, dict):
        expected = {
            "name": document.name,
            "document_hash": document.hash,
            "asof": asof,
            "state": "ran",
        }
        for key, value in expected.items():
            if payload.get(key) != value:
                problems.append(f"{key}={payload.get(key)!r}, expected {value!r}")
        folds = payload.get("folds")
        cutoffs = p10._fold_cutoffs(document.walkforward)
        if not isinstance(folds, list) or len(folds) != len(cutoffs):
            problems.append(
                f"fold count={len(folds) if isinstance(folds, list) else None}, "
                f"expected {len(cutoffs)}"
            )
        else:
            for row, cutoff in zip(folds, cutoffs):
                problems.extend(_ran_fold_problems(row, cutoff))
    elif payload is not None:
        problems.append("walkforward.json is not an object")
    action_ids = _matching_walk_actions(actions, summary, document, asof)
    if not action_ids:
        problems.append("missing matching journal evidence")
    return {
        "summary": summary,
        "walkforward": record_path,
        "report": report_path,
        "document_hash": document.hash,
        "action_ids": action_ids,
        "complete": not problems,
        "problems": problems,
    }


class Gate3RecoveryInventoryStage(Stage):
    """Audit immutable P12 source artifacts and reconstruct only full families."""

    outputs = (
        "gate1",
        "gate1_cells",
        "caches",
        "survivors",
        "families",
        "reconstructed",
        "rerun",
        "source",
        "failure",
    )
    _PARAMS = (
        "source_document",
        "source_run",
        "source_hash",
        "data_cut",
        "failure_action",
        "seeds",
        "alpha",
    )

    @classmethod
    def validate_params(cls, params):
        """Validate every immutable source pointer and verdict knob."""
        problems = []
        reject_unknown_params(problems, params, cls._PARAMS)
        for key in ("source_document", "source_run", "source_hash", "failure_action"):
            if not isinstance(params.get(key), str) or not params.get(key):
                problems.append(f"{key} must be a non-empty string")
        source_hash = params.get("source_hash")
        if isinstance(source_hash, str) and (
            len(source_hash) != 64
            or any(char not in "0123456789abcdef" for char in source_hash)
        ):
            problems.append("source_hash must be 64 lowercase hex digits")
        if not _date_ok(params.get("data_cut")):
            problems.append("data_cut must be a YYYY-MM-DD date")
        _check_int_list(problems, "seeds", params.get("seeds"), ordered=False)
        _check_alpha(problems, params)
        return problems

    def validate_inputs(self, inputs):
        """Require no inputs; read only explicitly pinned persisted sources."""
        return [] if inputs == {} else ["inventory takes no stage inputs"]

    def _path(self, ctx, value):
        return (
            value if os.path.isabs(value) else os.path.join(p10._child_root(ctx), value)
        )

    def run(self, ctx, inputs):
        """Return the evidence inventory, reconstructed families, and rerun set."""
        del inputs
        if ctx.asof != self.params["data_cut"]:
            raise ValueError(
                f"recovery must run at source data cut {self.params['data_cut']}"
            )
        source_path = self._path(ctx, self.params["source_document"])
        source_document = load_document(source_path)
        source_hash = self.params["source_hash"]
        if source_document.hash != source_hash:
            raise ValueError(
                f"source document hash {source_document.hash} != pinned {source_hash}"
            )
        current = ctx.document.to_obj()
        source_obj = source_document.to_obj()
        moved = [
            key
            for key in ("pipeline", "walkforward", "outputs")
            if current.get(key) != source_obj.get(key)
        ]
        if moved:
            raise ValueError(f"continuation changed original computation at {moved}")
        source_run = self._path(ctx, self.params["source_run"])
        stage_dir = os.path.join(source_run, "stages")
        gate1_path = os.path.join(stage_dir, "gate1.json")
        memory_path = os.path.join(stage_dir, "memory.json")
        gate1 = _read_recovery_stage(gate1_path, "gate1", source_hash)
        memory = _read_recovery_stage(memory_path, "memory", source_hash)
        if set(gate1) != {"rows", "cells"}:
            raise ValueError("source gate1 outputs are not exactly rows and cells")
        if memory.get("passed") is not True or not isinstance(
            memory.get("groups"), dict
        ):
            raise ValueError("source memory stage did not persist passed group caches")
        rows = gate1["rows"]
        cells = gate1["cells"]
        cohort = _document_cohort(source_document)
        if (
            not isinstance(rows, list)
            or [row.get("asset") for row in rows if isinstance(row, dict)] != cohort
        ):
            raise ValueError("source gate1 rows are not the ordered source cohort")
        survivors = _strict_survivors(rows)
        if any(
            isinstance(item["horizon"], bool)
            or not isinstance(item["horizon"], int)
            or item["horizon"] < 1
            for item in survivors
        ):
            raise ValueError("a persisted passing gate1 row has no positive horizon")
        observed = {}
        for item in survivors:
            matches = [
                cell
                for cell in cells
                if cell.get("asset") == item["asset"]
                and cell.get("horizon") == item["horizon"]
            ]
            if len(matches) != 1 or not isinstance(matches[0].get("skill"), dict):
                raise ValueError(
                    f"selected Gate-1 cell missing for {item['asset']}@{item['horizon']}"
                )
            observed[(item["asset"], item["horizon"])] = matches[0]
        root = find_journal(start=p10._child_root(ctx))
        if root is None:
            raise ValueError("child journal is unavailable")
        actions = read_actions(root)
        source_actions = {
            "gate1": _matching_stage_actions(
                actions, gate1_path, f"{source_hash}:gate1"
            ),
            "memory": _matching_stage_actions(
                actions, memory_path, f"{source_hash}:memory"
            ),
        }
        if not all(source_actions.values()):
            raise ValueError(
                f"source stages lack matching journal evidence: {source_actions}"
            )
        caches = memory["groups"]
        _StudyStage.refuse_moved_universes(self, ctx, caches)
        placement = _place(cohort, caches)
        families = []
        reconstructed = []
        rerun = []
        for item in survivors:
            asset = item["asset"]
            horizon = item["horizon"]
            seed_rows = []
            for seed in self.params["seeds"]:
                tag = f"gate3-seed{seed:02d}"
                document = asset_walk_document(
                    source_document,
                    source_document.name,
                    asset,
                    horizon,
                    caches[placement[asset]],
                    tag=tag,
                    scramble_seed=seed,
                )
                summary = p10._summary_dir(
                    document, self.params["data_cut"], p10._child_root(ctx)
                )
                main = _walk_evidence(
                    document, summary, self.params["data_cut"], actions
                )
                parts = []
                for index, cutoff in enumerate(p10._fold_cutoffs(document.walkforward)):
                    part = p10._single_fold_document(document, cutoff, index)
                    part_summary = p10._summary_dir(
                        part, self.params["data_cut"], p10._child_root(ctx)
                    )
                    parts.append(
                        _walk_evidence(
                            part, part_summary, self.params["data_cut"], actions
                        )
                    )
                complete = main["complete"] and all(part["complete"] for part in parts)
                seed_rows.append(
                    {
                        "seed": seed,
                        "complete": complete,
                        "main": main,
                        "parts": parts,
                    }
                )
            family_complete = len(seed_rows) == len(self.params["seeds"]) and all(
                seed["complete"] for seed in seed_rows
            )
            family = {
                "asset": asset,
                "horizon": horizon,
                "classification": (
                    "complete_end_to_end" if family_complete else "incomplete"
                ),
                "seeds": seed_rows,
            }
            families.append(family)
            if not family_complete:
                rerun.append(item)
                continue
            draws = []
            selected = observed[(asset, horizon)]
            verdict_problems = _skill_problems("observed", selected["skill"])
            for seed_row in seed_rows:
                label = f"seed {seed_row['seed']}"
                try:
                    skill = _score_one(
                        seed_row["main"]["summary"],
                        asset,
                        horizon,
                        self.params["alpha"],
                    )
                except (KeyError, OSError, TypeError, ValueError) as error:
                    verdict_problems.append(
                        f"{label} scoring failed: {type(error).__name__}: {error}"
                    )
                    continue
                skill_problems = _skill_problems(label, skill)
                verdict_problems.extend(skill_problems)
                if skill_problems:
                    continue
                draws.append(
                    {
                        "seed": seed_row["seed"],
                        "summary": seed_row["main"]["summary"],
                        "walkforward": seed_row["main"]["walkforward"],
                        "report": seed_row["main"]["report"],
                        "summary_action_ids": seed_row["main"]["action_ids"],
                        "part_summaries": [
                            part["summary"] for part in seed_row["parts"]
                        ],
                        "part_action_ids": [
                            action_id
                            for part in seed_row["parts"]
                            for action_id in part["action_ids"]
                        ],
                        "r2oos": skill["r2oos"],
                        "t_pool": skill["t_pool"],
                        "t_fold": skill["t_fold"],
                    }
                )
            if verdict_problems:
                family["classification"] = "non_reconstructable"
                family["verdict_input_problems"] = verdict_problems
                rerun.append(item)
                continue
            verdict = tier2_verdict(
                selected["skill"]["r2oos"],
                [draw["r2oos"] for draw in draws],
                [draw["t_pool"] for draw in draws],
            )
            reconstructed.append(
                {
                    "asset": asset,
                    "horizon": horizon,
                    "observed": {
                        "cell": selected["cell"],
                        "walk": selected["walk"],
                        "skill": selected["skill"],
                    },
                    "draws": draws,
                    "all_required_draws": (
                        len(draws) == len(self.params["seeds"]) == 19
                    ),
                    "verdict": verdict,
                    "result": {
                        "gate3_status": "pass" if verdict["passes"] else "fail",
                        "gate3_passes": verdict["passes"],
                        "gate3": verdict,
                        "not_reached_reason": None,
                    },
                    "method": (
                        "persisted Gate-1 selected cell plus 19 exact journal-backed "
                        "null summaries; intraday_equities.modelability_study."
                        "_score_one and dskit.pipeline.attempts.tier2_verdict"
                    ),
                }
            )
        failure_id = self.params["failure_action"]
        failure_matches = [row for row in actions if row.id == failure_id]
        if len(failure_matches) != 1:
            raise ValueError(f"failure action {failure_id} is not unique")
        failure = failure_matches[0].to_obj()
        if (
            "state=error" not in failure["notes"]
            or "seed05-nrg-h01-part-00 finished without journal evidence"
            not in failure["notes"]
        ):
            raise ValueError(f"{failure_id} is not the pinned NRG crash")
        return {
            "gate1": rows,
            "gate1_cells": cells,
            "caches": caches,
            "survivors": survivors,
            "families": families,
            "reconstructed": reconstructed,
            "rerun": rerun,
            "source": {
                "document": source_path,
                "document_hash": source_hash,
                "run": source_run,
                "gate1": gate1_path,
                "memory": memory_path,
                "stage_action_ids": source_actions,
                "data_cut": self.params["data_cut"],
            },
            "failure": failure,
        }


class Gate3ContinuationStage(Gate3WalksStage):
    """Rerun only inventory-declared incomplete families under a new identity."""

    outputs = (
        "rows",
        "rerun_rows",
        "walks",
        "draws",
        "rerun_families",
        "excluded_families",
    )
    _PARAMS = ("seeds", "alpha", "expected_rerun")

    @classmethod
    def validate_params(cls, params):
        """Validate shipped verdict knobs and the pinned rerun inventory."""
        problems = []
        reject_unknown_params(problems, params, cls._PARAMS)
        _check_int_list(problems, "seeds", params.get("seeds"), ordered=False)
        _check_alpha(problems, params)
        expected = params.get("expected_rerun")
        if (
            not isinstance(expected, list)
            or not expected
            or any(not isinstance(value, str) or ":" not in value for value in expected)
            or len(set(expected)) != len(expected)
        ):
            problems.append("expected_rerun must be unique asset:horizon strings")
        return problems

    def validate_inputs(self, inputs):
        """Require the immutable inventory outputs used by the continuation."""
        needed = {"gate1", "gate1_cells", "caches", "rerun", "reconstructed"}
        return [] if set(inputs) == needed else [f"requires {sorted(needed)}"]

    def run(self, ctx, inputs):
        """Audit every rerun family, then and only then emit the combined result."""
        gate1 = inputs["gate1"]
        cohort = self.cohort(ctx)
        if [row.get("asset") for row in gate1] != cohort:
            raise ValueError("continuation Gate-1 input is not the ordered cohort")
        survivors = _strict_survivors(gate1)
        survivor_keys = [f"{item['asset']}:{item['horizon']}" for item in survivors]
        rerun_families = inputs["rerun"]
        rerun_keys = [f"{item['asset']}:{item['horizon']}" for item in rerun_families]
        rerun_key_set = set(rerun_keys)
        rerun = [
            row for row in gate1 if f"{row['asset']}:{row['gate1_h']}" in rerun_key_set
        ]
        if rerun_keys != self.params["expected_rerun"]:
            raise ValueError(
                f"inventory rerun families {rerun_keys} != pinned "
                f"{self.params['expected_rerun']}"
            )
        reconstructed = inputs["reconstructed"]
        excluded_keys = [f"{item['asset']}:{item['horizon']}" for item in reconstructed]
        if (
            len(set(rerun_keys)) != len(rerun_keys)
            or len(set(excluded_keys)) != len(excluded_keys)
            or set(rerun_keys) & set(excluded_keys)
            or set(rerun_keys) | set(excluded_keys) != set(survivor_keys)
            or any(item.get("all_required_draws") is not True for item in reconstructed)
        ):
            raise ValueError(
                "reconstructed and rerun families do not exactly partition survivors"
            )
        caches = self.caches(ctx, inputs)
        self.refuse_moved_universes(ctx, caches)
        placement = _place(cohort, caches)
        observed = _observed_skill(inputs["gate1_cells"])
        self._refuse_unscored_cells(rerun, observed)
        walks = {}
        draws = {}
        for item in rerun:
            asset = item["asset"]
            horizon = item["gate1_h"]
            draws[asset] = self._audit(
                ctx,
                asset,
                horizon,
                caches[placement[asset]],
                observed[(asset, horizon)]["r2oos"],
                walks,
            )
        Gate3ResultStage._refuse_malformed_draws(self, rerun, draws)
        decision_inputs = {
            "draws": draws,
            "walks": walks,
        }
        rerun_rows = []
        for base in rerun:
            final = dict(base)
            final.update(
                Gate3ResultStage._decide(self, base, observed, decision_inputs)
            )
            rerun_rows.append(final)
        rerun_by_key = {f"{row['asset']}:{row['gate1_h']}": row for row in rerun_rows}
        reconstructed_by_key = {
            f"{item['asset']}:{item['horizon']}": item for item in reconstructed
        }
        rows = []
        for base in gate1:
            final = {
                **base,
                "gate3_status": "not_reached",
                "gate3_passes": False,
            }
            if not base["gate1_passes"]:
                final["not_reached_reason"] = "gate1_failed_at_h1"
            else:
                key = f"{base['asset']}:{base['gate1_h']}"
                if key in rerun_by_key:
                    final = {**base, **rerun_by_key[key]}
                else:
                    final.update(reconstructed_by_key[key]["result"])
            rows.append(final)
        return {
            "rows": rows,
            "rerun_rows": rerun_rows,
            "walks": walks,
            "draws": draws,
            "rerun_families": rerun_keys,
            "excluded_families": excluded_keys,
        }
