"""The universal execution file's engine (docs/24 §9).

LOAD → IMPORT → PLAN → RESOLVE → EXECUTE → RECORD.

One entry point, :func:`run_document`, drives any node-map document —
there is deliberately nowhere else for per-project execution logic to
live (D-145 ruling 3). Steps 1–4 fail loudly BEFORE a run directory
exists; from the moment the run dir is written, every outcome — clean
run, NO-GO halt, node error — is RECORDED as a result, never lost to a
traceback.

Execution is the single-pass DAG (spec §7: no clock = plan, run each
node once, done). A document with a ``clock`` refuses to run — clocked
execution is pending the I-222 A/B ruling; ``plan``/``validate`` still
work on it. ``trailing`` splits materialize here, from the data's edge
(:meth:`Node.data_edge`); integer ``train_days`` stamps a bounded
train window (ADR-0050).

Per node, the uniform lifecycle (D-145 ruling 4)::

    validate_params -> validate_inputs -> run -> validate_outputs -> log

Halt semantics: a ``gate``/``stat_test`` node whose outputs carry
``verdict == "NO-GO"`` halts every DAG DESCENDANT (not a linear break —
independent branches keep running); a halt is a result, exit code 3.
A node exception aborts the remaining order (exit code 1) but still
records everything that ran.

Search semantics (docs/24 §8): a ``search``-role node — and only a
search node — receives ``ctx.rerun``, the :class:`_SearchSeam` closure
that re-executes the objective's dirty subgraph per trial against a
scratch copy of the outputs. Its engine is :class:`SubgraphRunner`, the
PUBLIC seam a serving loop drives too (ADR-0091): the same per-node
lifecycle, the caller's own override rule, an optional
:class:`~dskit.pipeline.policy.ExecutionPolicy`. When the node returns a
non-empty ``best_params``, the driver re-executes that subgraph ONE final time
with the winning overrides and REPLACES those nodes' outputs, timings,
and sink metrics — everything downstream of the search consumes the
winner pass, and the search node's record carries ``trials_executed``
plus the ``winner_reran`` node list.

A run SURFACES its search (ADR-0043): the same per-node record — the
trials, the ``winner`` and its score when the kind produced them —
rides out on :attr:`DocumentRunResult.search`, node-keyed, populated
BEFORE the winner is applied so a winner-flip refusal still names the
winner that caused it. Walk-forward then tallies those winners per
fold, because per-fold re-tuning MEASURES the tuning procedure and its
per-fold disagreement must be printed rather than assumed away.

Run-over-run state (``$prev``): each run writes ``carry.json`` — every
JSON-small node output — and the next run in the series binds its
``$prev`` references against the newest prior run dir, falling back to
the declared ``default`` (first run, or the referenced output missing);
every binding is recorded in ``resolved.json`` so a silent reset cannot
hide. Spent record streams are released after their last consumer
(ADR-0048) so EXECUTE does not hold a raw tape through RECORD.

Import cost: stdlib only.
"""

from __future__ import annotations

import copy
import json
import logging
import math
import os
import re
import sys
import tempfile
import time
import traceback
from collections import ChainMap
from dataclasses import dataclass, field, replace

from dskit.pipeline.base import (
    DEFAULT_SPLIT_POLICY,
    SINK_KINDS,
    ConfigError,
    TimeSplitConfig,
    _strip_non_identity,
    _strip_notes,
    import_ref,
    is_class_ref,
    merge_event_bounds,
)
from dskit.pipeline.document import (
    ALL_PRIOR,
    DOC_NON_IDENTITY_SECTIONS,
    SEARCH_SPACE_PARAM,
    SPLITS_SOURCE,
    PipelineDocument,
    TrailingSplitSpec,
    flatten_param_paths,
    is_node_ref,
    is_prev_ref,
    load_document,
    parse_node_ref,
    parse_prev_ref,
)
from dskit.pipeline.env import load_env
from dskit.pipeline.node import Node, NodeContext
from dskit.pipeline.planner import unsearchable_space_why
from dskit.pipeline.planner import plan as plan_document
from dskit.pipeline.runs import _escape_pipe

__all__ = [
    "DocumentRunResult",
    "FOLD_FIELDS",
    "FOLD_OPTIONAL_FIELDS",
    "SubgraphRunner",
    "WalkForwardRunResult",
    "aggregate_folds",
    "apply_param_override",
    "run_document",
    "run_walk_forward",
    "write_walkforward_summary",
]

DRIVER_VERSION = "1.0.0"

_ASOF_OK = r"^\d{4}-\d{2}-\d{2}$"

#: Ceiling for one carried output value's canonical JSON, in characters —
#: carry.json holds run-over-run STATE (bankrolls, artifact paths), not
#: datasets.
_CARRY_LIMIT = 20_000

#: A list this long is storage. Unit-test streams stay under it; a
#: market tape does not (ADR-0048).
_RELEASE_MIN_LEN = 256

_SUMMARY_TYPES = frozenset({"list", "tuple", "dict"})
_KEEP_PORTS = frozenset({"flags"})

#: The roles whose ``verdict`` output decides a halt (spec §7): a NO-GO from
#: one halts its DAG descendants, and a winner pass may not flip one to
#: NO-GO after the base pass decided. One name, read by both rules.
_VERDICT_ROLES = ("gate", "stat_test")

#: The keys EVERY fold row of a walk-forward summary carries (ADR-0093).
#: ``_run_folds`` builds each row from this tuple, ``aggregate_folds``
#: refuses a row missing any of them, and ``runs.single_fold_row`` reads
#: the rows back — one owner for the shape the driver writes and the
#: readers consume.
FOLD_FIELDS = ("cutoff", "run_dir", "state", "score")

#: The keys a fold row carries ONLY WHEN PRESENT: ``search`` when the fold
#: had a search node (an always-emitted key would move every HPO-free
#: summary's bytes, ADR-0043) and ``error`` when the fold refused or
#: failed. ``aggregate_folds`` refuses any key outside the union.
FOLD_OPTIONAL_FIELDS = ("search", "error")

_log = logging.getLogger("dskit.pipeline.driver")


# ---------------------------------------------------------------------------
# Small services
# ---------------------------------------------------------------------------


def _atomic_write_text(path, text) -> None:
    """Write ``text`` to ``path`` atomically, never half-visible.

    Via a same-directory temp file + ``os.replace``, so a reader never
    sees a half-written artifact. (Inline by necessity: the purity rule
    bars importing the application's own atomic-write helper here.)
    """
    directory = os.path.dirname(path) or "."
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".tmp-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def _write_json(path, payload) -> None:
    _atomic_write_text(
        path, json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n"
    )


class _Trackers:
    """Fan-out façade over the configured sinks (Tracker seam).

    Every sink call is defended: telemetry is additional destinations,
    never the run itself — a flaky sink logs an exception and the run
    (which the driver promises to RECORD) carries on.
    """

    def __init__(self, sinks):
        self.sinks = tuple(sinks)

    def log_params(self, mapping) -> None:
        for sink in self.sinks:
            try:
                sink.log_params(mapping)
            except Exception:  # noqa: BLE001 — telemetry must not kill the run
                _log.exception("tracking sink %r failed log_params", sink)

    def log_metrics(self, node, mapping) -> None:
        if mapping:
            for sink in self.sinks:
                try:
                    sink.log_metrics(node, mapping)
                except Exception:  # noqa: BLE001 — telemetry must not kill the run
                    _log.exception(
                        "tracking sink %r failed log_metrics for %s", sink, node
                    )

    def close(self) -> None:
        for sink in self.sinks:
            try:
                sink.close()
            except Exception:  # noqa: BLE001 — closing must not mask the run
                _log.exception("tracking sink %r failed to close", sink)


def _open_sinks(tracking):
    """Construct every configured sink, refusing one that cannot track.

    Class refs are contract-checked the way the stage-list resolve did
    (``log_params``/``log_metrics``/``close``).
    """
    if tracking is None:
        return _Trackers(())
    sinks = []
    for sink_cfg in tracking.sinks:
        if is_class_ref(sink_cfg.kind):
            target = import_ref(sink_cfg.kind)
            missing = [
                m
                for m in ("log_params", "log_metrics", "close")
                if not hasattr(target, m)
            ]
            if missing:
                raise ConfigError(
                    [
                        f"tracking sink reference {sink_cfg.kind!r} is missing "
                        f"the Tracker seam method(s) {missing}"
                    ]
                )
            sinks.append(target(sink_cfg.params))
        elif sink_cfg.kind in SINK_KINDS:
            sinks.append(SINK_KINDS[sink_cfg.kind]["factory"](sink_cfg.params))
        else:
            raise ConfigError(
                [
                    f"tracking sink kind {sink_cfg.kind!r} is not registered "
                    f"(known: {sorted(SINK_KINDS)}) — the package that owns it "
                    "must register_sink_kind() on import"
                ]
            )
    return _Trackers(sinks)


def _tracked_params(pipeline, order):
    """Flatten the document's declared hyperparameters for the sinks.

    Identity alone left runs unfilterable — you could not ask a sink for
    "the runs at hidden_size=64". The payload is the DECLARED
    (post-override) document: every node's params as this execution's
    document spells them — a search winner promoted by rerunning it as
    its own document carries the override in that document's text —
    flattened to the ``"<node>.<param.path>"`` spelling ``hpo-grid``
    space keys use, with every reference logged as a reference
    (:func:`~dskit.pipeline.document.flatten_param_paths`). Declared is
    what keeps the payload honest AND stable: it is known before any
    node runs (so one call at run start carries it and a crash cannot
    lose it), its key set never drifts across a run series the way a
    resolved carry's shape can, and every key is an address the
    override/space grammar can spell. What a run RESOLVED lives in the
    run dir (``resolved.json``, ``carry.json``, the node records); what
    a search CHOSE lives in the search node's outputs.

    Parameters
    ----------
    pipeline : dict
        The document's node map (``key`` -> ``NodeSpec``).
    order : tuple of str
        Plan order, so the payload is built deterministically.

    Returns
    -------
    dict
        ``"<node>.<param.path>"`` -> declared value. Every key carries a
        dot, so none can shadow the undotted identity fields logged
        beside them.
    """
    payload = {}
    for key in order:
        payload.update(flatten_param_paths(key, pipeline[key].params))
    return payload


def _canonical_hash(payload) -> str:
    import hashlib

    canon = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )
    return hashlib.sha256(canon.encode("ascii")).hexdigest()


# ---------------------------------------------------------------------------
# Reference materialization (execute-time)
# ---------------------------------------------------------------------------


def _dig(value, path, describe):
    for seg in path:
        if not isinstance(value, dict) or seg not in value:
            available = (
                sorted(value) if isinstance(value, dict) else type(value).__name__
            )
            raise ValueError(
                f"{describe}: no {seg!r} at this path (available: {available})"
            )
        value = value[seg]
    return value


def _materialize(obj, where, outputs, splits_info, prev, bindings):
    """Deep-copy ``obj`` with every reference replaced by its value.

    ``$node.path`` resolves against upstream outputs (or the materialized
    splits for ``$splits...``); a dangling path fails loudly. ``$prev``
    resolves against the previous run's carry, falling back to its
    declared default; each binding lands in ``bindings`` for
    ``resolved.json``.
    """
    if is_prev_ref(obj):
        node, path, default = parse_prev_ref(obj)
        ref = f"{node}.{'.'.join(path)}"
        if node in prev:
            try:
                value = _dig(prev[node], path, f"$prev {ref!r}")
                bindings[ref] = "prev"
                return value
            except ValueError:
                pass
        bindings[ref] = "default"
        return default
    if is_node_ref(obj):
        source, path = parse_node_ref(obj)
        if source == SPLITS_SOURCE:
            return _dig(splits_info, path, f"{where}: $splits.{'.'.join(path)}")
        return _dig(outputs[source], path, f"{where}: ${source}.{'.'.join(path)}")
    if isinstance(obj, dict):
        return {
            k: _materialize(v, f"{where}.{k}", outputs, splits_info, prev, bindings)
            for k, v in obj.items()
        }
    if isinstance(obj, (list, tuple)):
        return type(obj)(
            _materialize(v, f"{where}[{i}]", outputs, splits_info, prev, bindings)
            for i, v in enumerate(obj)
        )
    return obj


# ---------------------------------------------------------------------------
# Search subgraph re-execution (docs/24 §8)
# ---------------------------------------------------------------------------


def apply_param_override(params, node_key, path, value):
    """Set ``value`` at ``path`` inside one node's (deep-copied) params.

    The one override rule every re-execution shares — a search trial,
    the winner pass, a serving window (ADR-0091): an override may only
    address an EXISTING param. Every segment must navigate an existing
    dict key and the terminal key must already be there; creating a
    missing key is an error, never a feature. A typo must not become a
    silent new knob — and a document that will be SERVED declares its
    window (``"since_ms": null``) so the serving override finds it.

    Parameters
    ----------
    params : dict
        The node's params, already deep-copied — edited in place.
    node_key : str
        The addressed node, quoted into a refusal.
    path : tuple of str
        The param path below the node (``("opt", "lr")``).
    value : object
        The value to set.

    Returns
    -------
    None
        ``params`` is edited in place.

    Raises
    ------
    ValueError
        When a segment has nothing to descend into, or the terminal key
        is not an existing param.
    """
    where = f"override '{node_key}.{'.'.join(path)}'"
    cursor = params
    for seg in path[:-1]:
        if not isinstance(cursor, dict) or seg not in cursor:
            available = (
                sorted(cursor) if isinstance(cursor, dict) else type(cursor).__name__
            )
            raise ValueError(
                f"{where}: no {seg!r} to descend into (available: {available})"
            )
        cursor = cursor[seg]
    last = path[-1]
    if not isinstance(cursor, dict) or last not in cursor:
        available = (
            sorted(cursor) if isinstance(cursor, dict) else type(cursor).__name__
        )
        raise ValueError(
            f"{where}: {last!r} is not an existing param (available: {available}) "
            "— overrides may only address existing params, never create them"
        )
    cursor[last] = value


#: The private spelling the rule was first published under — an ALIAS of
#: the one owner above, never a second definition, kept so a caller of the
#: old name keeps working while it moves to :func:`apply_param_override`.
_apply_param_override = apply_param_override


def _override_targets(overrides, declared):
    """Parse overrides to ``(node, path, value)``; a non-dict or undeclared node refuses."""
    if not isinstance(overrides, dict):
        raise ValueError(
            f"overrides must be a dict of 'node.param.path' -> value, "
            f"got {overrides!r}"
        )
    for target, value in overrides.items():
        parts = target.split(".") if isinstance(target, str) else []
        if len(parts) < 2 or parts[0] not in declared:
            raise ValueError(
                f"override {target!r} must be '<node>.<param.path>' "
                f"addressing a declared node (declared: {sorted(declared)})"
            )
        yield parts[0], tuple(parts[1:]), value


class SubgraphRunner:
    """Re-execute part of a planned DAG under param overrides (ADR-0091).

    The engine behind the search seam, made public so a serving loop can
    drive the same lifecycle under a different override rule. Given the
    plan and a BASE pass, :meth:`rerun` applies ``"node.param.path"``
    overrides into deep copies of the addressed specs' params and runs
    ``order ∩ needed ∩ dirty`` — the addressed nodes plus their DAG
    descendants, restricted to the ancestry the caller asked for — with
    the full per-node lifecycle (``validate_inputs -> run ->
    validate_outputs``). :meth:`run_keys` runs a given key set with no
    overrides at all: the serving base pass's verb. Clean nodes are never
    re-run — a ``$node`` reference that ``outputs`` does not hold is read
    from the base pass.

    The runner keeps exactly two override clauses — a DECLARED node and
    an EXISTING param path (:func:`apply_param_override`) — and nothing
    else. Which roles a SEARCH may re-tune is the search seam's rule, not
    the runner's: serving's one override addresses a ``data`` node's
    window, which a search may never touch.

    Parameters
    ----------
    the_plan : Plan
        The resolved DAG (:func:`~dskit.pipeline.planner.plan`).
    needed : set of str
        The nodes a pass may run at all — the caller's ancestry
        (``ancestors(target) | {target}`` for a search objective,
        ``ancestors(heads) | heads`` for serving). A dirty node outside it
        is never executed. A parameter, not a derivation: the runner
        cannot know what the pass is for.
    node_outputs : dict
        The base pass — ``key -> outputs`` of every node that has run.
        Read, never written: a reference a pass's own ``outputs`` cannot
        answer resolves here.
    splits_info : dict
        The materialized splits' JSON view, behind ``$splits`` references.
    prev : dict
        The previous run's carry, behind ``$prev`` references.
    policy : ExecutionPolicy, optional
        ``None`` — the default, and today's search behaviour exactly —
        defers nothing and hands no node a reader. A policy's
        :meth:`~dskit.pipeline.policy.ExecutionPolicy.defer` skips a key
        whose output the caller SEEDED in ``outputs`` (the entry, read
        once elsewhere: no second mutable read), and its
        :meth:`~dskit.pipeline.policy.ExecutionPolicy.reader` reaches a
        node as ``ctx.release_reader``.

    Examples
    --------
    Re-run one node and the objective below it, on a scratch copy of the
    base pass, leaving the base untouched::

        runner = SubgraphRunner(the_plan, {"src", "mid", "a"}, base, {}, {})
        scratch = dict(base)
        outputs, ran, seconds = runner.rerun({"mid.bump": 5}, scratch, ctx, {})
        ran                  # ('mid', 'a')
        outputs is scratch   # True
    """

    def __init__(self, the_plan, needed, node_outputs, splits_info, prev, policy=None):
        self._plan = the_plan
        self._specs = the_plan.document.expanded
        self._needed = frozenset(needed)
        self._base = node_outputs
        self._splits_info = splits_info
        self._prev = prev
        self._policy = policy

    def rerun(self, overrides, outputs, ctx, prev_bindings, *, guard_verdicts=False):
        """Re-execute the dirty subgraph under ``overrides``, in place.

        Parameters
        ----------
        overrides : dict
            ``"node.param.path" -> value``. Each target must address a
            declared node and an existing param path; the addressed nodes
            and all their descendants are DIRTY. An empty map dirties
            nothing and executes nothing.
        outputs : dict
            What this pass reads from and writes into — a scratch copy for
            a trial, the live dict for a winner or a serving pass. Mutated
            in place and returned. A node the policy defers must already
            be seeded here; the base pass never stands in for it.
        ctx : NodeContext
            The run frame handed to every node — as is, or carrying the
            policy's reader as ``release_reader`` for the nodes it names.
        prev_bindings : dict
            OUT parameter: where this pass's ``$prev`` resolutions land
            (``"node.output" -> "prev" | "default"``).
        guard_verdicts : bool, optional
            Refuse a ``gate``/``stat_test`` node whose verdict FLIPS to
            NO-GO against the output held for it — the winner-pass rule,
            because halt decisions were already made on the base pass.

        Returns
        -------
        tuple
            ``(outputs, ran, seconds)`` — the SAME ``outputs`` object, the
            keys actually executed in plan order (deferred keys are not
            among them), and each one's wall time in seconds.

        Raises
        ------
        ValueError
            An override map that is not a dict; a target that is not
            ``'<node>.<param.path>'`` on a declared node; a param path
            that does not exist; a deferred key with no seeded output; a
            guarded verdict flip.
        ConfigError
            A node's ``validate_inputs`` or ``validate_outputs`` problems.
        """
        per_node = {}
        for node, path, value in _override_targets(overrides, self._specs):
            per_node.setdefault(node, []).append((path, value))
        dirty = set(per_node)
        for head in per_node:
            dirty |= self._plan.descendants(head)
        subgraph = [k for k in self._plan.order if k in self._needed and k in dirty]
        return self._run(subgraph, per_node, outputs, ctx, prev_bindings, guard_verdicts)

    def run_keys(self, keys, outputs, ctx, prev_bindings):
        """Run exactly ``keys``, in plan order, with no overrides, in place.

        The serving BASE PASS verb: production hands it the needed keys
        that are neither the entry nor its descendants, so the immutable
        part of the DAG runs once for the process lifetime. ``needed``
        does not filter here — the caller named the keys.

        Parameters
        ----------
        keys : iterable of str
            The nodes to run; every one must be declared.
        outputs : dict
            Read from and written into, in place; returned.
        ctx : NodeContext
            The run frame, as for :meth:`rerun`.
        prev_bindings : dict
            OUT parameter, as for :meth:`rerun`.

        Returns
        -------
        tuple
            ``(outputs, ran, seconds)``, as for :meth:`rerun`.

        Raises
        ------
        ValueError
            An undeclared key, or a deferred key with no seeded output.
        ConfigError
            A node's ``validate_inputs`` or ``validate_outputs`` problems.
        """
        wanted = set(keys)
        undeclared = sorted(str(k) for k in wanted if k not in self._specs)
        if undeclared:
            raise ValueError(
                f"run_keys: {undeclared} are not declared nodes "
                f"(declared: {sorted(self._specs)})"
            )
        subgraph = [k for k in self._plan.order if k in wanted]
        return self._run(subgraph, {}, outputs, ctx, prev_bindings, False)

    def _run(self, subgraph, per_node, outputs, ctx, prev_bindings, guard_verdicts):
        """Run ``subgraph`` in order, skipping deferred keys, timing each node."""
        view = ChainMap(outputs, self._base)
        ran, seconds = [], {}
        for key in subgraph:
            if self._deferred(key, outputs):
                continue
            t0 = time.perf_counter()
            outputs[key] = self._run_node(
                key, per_node.get(key, ()), view, ctx, prev_bindings, guard_verdicts
            )
            seconds[key] = round(time.perf_counter() - t0, 6)
            ran.append(key)
        return outputs, tuple(ran), seconds

    def _deferred(self, key, outputs):
        """Say whether the policy defers ``key``; a deferred key must be seeded."""
        if self._policy is None or not self._policy.defer(key):
            return False
        if key not in outputs:
            raise ValueError(
                f"{key}: the policy defers this node, so its output must be "
                "seeded in outputs before the pass — none is, and the base "
                "pass's value may not stand in for a deferred read"
            )
        return True

    def _run_node(self, key, overrides, view, ctx, prev_bindings, guard_verdicts):
        """Materialize, construct, validate and run ONE node; return its outputs."""
        spec = self._specs[key]
        raw = copy.deepcopy(spec.params)
        for path, value in overrides:
            apply_param_override(raw, key, path, value)
        params = _materialize(
            raw,
            f"pipeline.{key}.params",
            view,
            self._splits_info,
            self._prev,
            prev_bindings,
        )
        inputs = {
            port: _materialize(
                ref,
                f"pipeline.{key}.inputs.{port}",
                view,
                self._splits_info,
                self._prev,
                prev_bindings,
            )
            for port, ref in spec.inputs.items()
        }
        node = self._plan.resolved[key].cls(
            key, params, mode=spec.mode, artifact=spec.artifact
        )
        problems = node.validate_inputs(inputs)
        if problems:
            raise ConfigError([f"{key}: {p}" for p in problems])
        out = node.run(self._frame_for(key, ctx), inputs)
        problems = node.validate_outputs(out)
        if problems:
            raise ConfigError([f"{key}: {p}" for p in problems])
        if guard_verdicts:
            self._refuse_flip(key, out, view)
        return out

    def _frame_for(self, key, ctx):
        """``ctx`` as is, or carrying the policy's release reader for ``key``."""
        reader = None if self._policy is None else self._policy.reader(key)
        return ctx if reader is None else replace(ctx, release_reader=reader)

    def _refuse_flip(self, key, out, view):
        """Refuse a verdict node flipping to NO-GO after the base pass decided."""
        role = self._plan.role_of(key)
        if (
            role in _VERDICT_ROLES
            and out.get("verdict") == "NO-GO"
            and view.get(key, {}).get("verdict") != "NO-GO"
        ):
            raise ValueError(
                f"{key}: the winner pass flipped this {role} node's verdict to "
                "NO-GO after halt decisions were made on the base pass — "
                "refusing to ride a stale GO"
            )


class _SearchSeam:
    """One search node's subgraph re-execution seam (docs/24 §8).

    Injected as ``ctx.rerun`` for search-role nodes ONLY. A thin caller of
    :class:`SubgraphRunner` that owns what is SEARCH's alone: the
    objective float, the unsearchable-role rule and the ``needed`` set.
    For a search node S whose ``objective`` references score node T:

    * ``needed`` = ancestors(T) ∪ {T} — the minimal subgraph that can
      produce the objective; the runner is built over it;
    * ``dirty(overrides)`` = the nodes the override paths address plus
      all their DAG descendants;
    * ``rerun(overrides) -> float`` refuses an override on a role a search
      may never re-tune, then has the runner apply each
      ``"node.param.path"`` override into a DEEP COPY of the affected
      specs' params and re-execute ``needed ∩ dirty`` in plan order
      against a SCRATCH copy of the base outputs (clean nodes are read
      from the base pass, never re-run), full per-node lifecycle, and
      digs the objective path out of T's trial outputs. Trials run with
      the tracker silenced — the sinks reflect the final pass, not
      exploration. A trial that raises propagates a clear error naming
      the trial's overrides.
    * ``seed_targets`` names the ``"node.seed"`` override paths of
      train-role nodes inside the full re-execution set that already
      declare a top-level ``seed`` param — the seeds-ensemble contract
      (:mod:`dskit.pipeline.kinds_search`).
    * after S completes, the driver applies S's non-empty ``best_params``
      via :meth:`apply_winner`, REPLACING the re-executed nodes' outputs
      in the live ``node_outputs`` — every node downstream of S consumes
      the winner pass. A re-executed ``gate``/``stat_test`` node whose
      verdict FLIPS to NO-GO under the winner raises (halt decisions were
      already made on the base pass; continuing would be a stale GO).

    ``calls`` counts every ``rerun`` invocation — surfaced as
    ``trials_executed`` in the search node's record.
    """

    def __init__(self, key, the_plan, node_outputs, splits_info, prev, trial_ctx):
        self._key = key
        self._plan = the_plan
        self._specs = the_plan.document.expanded
        self._outputs = node_outputs  # the LIVE dict; trials copy, winner writes
        self._trial_ctx = trial_ctx
        self.calls = 0
        # The planner guaranteed the objective is a $-ref into a declared
        # val-split score node before anything could execute.
        target, path = parse_node_ref(self._specs[key].params["objective"])
        self._target, self._obj_path = target, path
        self.needed = the_plan.ancestors(target) | {target}
        self._runner = SubgraphRunner(
            the_plan, self.needed, node_outputs, splits_info, prev
        )
        space = self._specs[key].params.get(SEARCH_SPACE_PARAM)
        heads = set()
        if isinstance(space, dict):
            heads = {
                t.split(".", 1)[0]
                for t in space
                if isinstance(t, str) and t.split(".", 1)[0] in self._specs
            }
        full_dirty = set(heads)
        for head in heads:
            full_dirty |= the_plan.descendants(head)
        self.seed_targets = tuple(
            f"{k}.seed"
            for k in the_plan.order
            if k in self.needed
            and k in full_dirty
            and the_plan.role_of(k) == "train"
            and "seed" in self._specs[k].params
        )

    def __call__(self, overrides) -> float:
        self.calls += 1
        scratch = dict(self._outputs)
        try:
            self._refuse_unsearchable(overrides)
            self._runner.rerun(overrides, scratch, self._trial_ctx, {})
            value = _dig(
                scratch[self._target],
                self._obj_path,
                f"objective '${self._target}.{'.'.join(self._obj_path)}'",
            )
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"the objective must be numeric, got {value!r}")
            return float(value)
        except Exception as exc:
            raise RuntimeError(
                f"search {self._key}: trial with overrides {overrides!r} failed: {exc}"
            ) from exc

    def apply_winner(self, overrides, ctx, bindings):
        """Re-execute the winner's subgraph ONE final time, for real.

        ``needed ∩ dirty(overrides)`` runs again under the winning
        overrides, replacing those nodes' outputs in the live
        ``node_outputs``.

        Parameters
        ----------
        overrides : dict
            The winning ``"node.param.path" -> value`` map.
        ctx : NodeContext
            The run frame — the LIVE one, so the winner pass reaches the
            tracking sinks that the trials were silenced against.
        bindings : dict
            Where this pass's ``$prev`` bindings are recorded.

        Returns
        -------
        tuple
            ``(reran_keys, seconds_by_key)`` — the subgraph in plan
            order, and each node's wall time.

        Raises
        ------
        RuntimeError
            Any failure of the pass, named by the winning overrides —
            including a ``gate``/``stat_test`` node whose verdict flips
            to NO-GO under the winner.
        """
        try:
            self._refuse_unsearchable(overrides)
            _, reran, seconds = self._runner.rerun(
                overrides, self._outputs, ctx, bindings, guard_verdicts=True
            )
            return reran, seconds
        except Exception as exc:
            raise RuntimeError(
                f"search {self._key}: applying winner overrides {overrides!r} "
                f"failed: {exc}"
            ) from exc

    def _refuse_unsearchable(self, overrides):
        """Refuse an override on a role a search may never re-tune."""
        # The planner refuses unsearchable heads in the DOCUMENT's space;
        # this is the runtime twin, because a custom search kind can hand
        # ctx.rerun any overrides it likes. Declaredness is checked FIRST,
        # target by target, by the rule the runner shares: Plan.role_of
        # indexes resolved[key], so an undeclared node must refuse by name
        # before its role is ever asked for.
        for node, path, _value in _override_targets(overrides, self._specs):
            role = self._plan.role_of(node)
            why = unsearchable_space_why(role, path[0])
            if why is not None:
                target = f"{node}.{'.'.join(path)}"
                raise ValueError(
                    f"override {target!r} addresses node {node!r} of "
                    f"role {role!r}, which a search may never re-tune — {why}"
                )


# ---------------------------------------------------------------------------
# Run-series discovery ($prev)
# ---------------------------------------------------------------------------


def _materialize_splits(splits, edges, data_nodes, declines=()):
    """Return the document's splits as the object nodes will use.

    Every kind but ``trailing`` IS its own runtime object and passes
    through. A trailing spec is materialized against the data's edge —
    the newest instant a ``data`` node reported through
    :meth:`~dskit.pipeline.node.Node.data_edge` — because its windows are
    counted backward from there (docs/24 splits; I-223).

    Two refusals, both loud, neither guessing: no source offered an edge,
    or more than one did. A silently-chosen anchor would silently move
    every cut, so ambiguity is an error until the grammar can name the
    anchor explicitly (the ``splits.source`` amendment parked in I-222).
    """
    if not isinstance(splits, TrailingSplitSpec):
        return splits
    if not edges:
        if not data_nodes:
            detail = "the document declares no data node"
        else:
            silent = [k for k in data_nodes if k not in declines]
            detail = (
                f"{silent} implement data_edge() but reported no data — check "
                "the source's data_dir and universe"
                if silent
                else f"{sorted(declines)} do not implement data_edge()"
            )
        raise ConfigError(
            [
                "splits.kind 'trailing' counts its windows backward from the "
                f"data's newest settled instant, and none was supplied: {detail}. "
                "Pin time cuts instead, or give a source a data_edge()"
            ]
        )
    if len(edges) > 1:
        raise ConfigError(
            [
                "splits.kind 'trailing' needs ONE anchor, but "
                f"{sorted(edges)} each supplied a data edge — refusing to pick "
                "(a guessed anchor silently moves every cut). Pin time cuts, or "
                "leave one edge-supplying source in the document"
            ]
        )
    ((key, newest_ms),) = edges.items()
    try:
        return splits.materialize(newest_ms)
    except ValueError as exc:
        raise ConfigError([f"splits.kind 'trailing' (anchored on {key!r}): {exc}"])


def _bind_event_bounds(splits, instances, roles):
    """Bind the ``cluster -> EventBounds`` map an EVENT policy needs.

    A no-op unless the materialized split declares a policy that reads
    bounds, so a ``record``-policy run never pays for the scan. Bounds come
    from the ``data`` nodes' :meth:`~dskit.pipeline.node.Node.event_bounds`
    and are UNIONED — two venues describe disjoint events, so unlike a
    trailing anchor there is nothing to refuse.

    Refuses loudly when the policy needs bounds and no source supplied any.
    Falling back to per-record assignment here would silently restore the
    straddle the document explicitly asked to close, and the run would look
    like it had worked.
    """
    if splits is None or not getattr(splits, "needs_event_bounds", False):
        return splits
    data_keys = sorted(k for k in instances if roles(k) == "data")
    maps, declines = [], []
    for key in data_keys:
        node = instances[key]
        if type(node).event_bounds is Node.event_bounds:
            declines.append(key)
            continue
        got = node.event_bounds()
        if got:
            maps.append(got)
    if not maps:
        detail = (
            f"{declines} do not implement event_bounds()"
            if declines
            else f"{data_keys} implement event_bounds() but reported none"
        )
        raise ConfigError(
            [
                f"splits.policy {splits.policy!r} assigns every record of an "
                "event to its EVENT's split, which needs each event's observed "
                f"extent, and no source supplied one: {detail}. Give a source an "
                "event_bounds(), or declare splits.policy 'record' and accept "
                "that long events straddle the cuts"
            ]
        )
    return splits.with_event_bounds(merge_event_bounds(*maps))


def _find_prev_run(run_root, name, own_dir):
    """Find the newest prior run of this series carrying a ``carry.json``.

    Ordered by the asof embedded in the dir name (ISO dates sort
    lexicographically), then by mtime for same-asof reruns — the hash
    suffix is identity, not recency, and must not decide. ``None`` on a
    first run.
    """
    if not os.path.isdir(run_root):
        return None
    pattern = re.compile(
        rf"^{re.escape(name)}-(\d{{4}}-\d{{2}}-\d{{2}})-[0-9a-f]{{8}}$"
    )
    candidates = []
    for entry in os.listdir(run_root):
        full = os.path.join(run_root, entry)
        if full == own_dir or not os.path.isdir(full):
            continue
        matched = pattern.match(entry)
        if matched is None:
            continue
        if not os.path.isfile(os.path.join(full, "carry.json")):
            continue
        candidates.append((matched.group(1), os.path.getmtime(full), full))
    if not candidates:
        return None
    return max(candidates, key=lambda c: (c[0], c[1]))[2]


def _is_summary(value):
    """Return whether ``value`` is ``_summarize``'s collapsed form."""
    return (
        isinstance(value, dict)
        and set(value) == {"type", "len"}
        and value.get("type") in _SUMMARY_TYPES
        and isinstance(value.get("len"), int)
    )


def _too_big_to_carry(value):
    """Refuse values a dumps cannot fit under ``_CARRY_LIMIT``."""
    if isinstance(value, str) and len(value) > _CARRY_LIMIT:
        return True
    if isinstance(value, (list, tuple)) and len(value) * 2 > _CARRY_LIMIT:
        return True
    return False


def _should_release(value):
    """Judge whether a spent port is a record stream, not in-process state."""
    if _is_summary(value):
        return False
    return isinstance(value, (list, tuple)) and len(value) >= _RELEASE_MIN_LEN


def _search_held(the_plan, completed):
    """Name ancestors a not-yet-run search still reads from the base pass."""
    held = set()
    for key in the_plan.order:
        if key in completed or the_plan.role_of(key) != "search":
            continue
        objective = the_plan.document.expanded[key].params.get("objective")
        if not is_node_ref(objective):
            continue
        target, _path = parse_node_ref(objective)
        held |= the_plan.ancestors(target) | {target}
    return held


def _release_spent(the_plan, run, resolved):
    """Replace spent record streams with summaries (ADR-0048)."""
    completed = {key for key, state in run.node_states.items() if state == "ok"}
    held = _search_held(the_plan, completed)
    readers = {}
    for src, dst in the_plan.edges:
        readers.setdefault(src, set()).add(dst)
    for src in the_plan.order:
        if src not in run.node_outputs or src in held:
            continue
        if readers.get(src, set()) - completed:
            continue
        outs = run.node_outputs[src]
        released = False
        for name, value in list(outs.items()):
            if name in _KEEP_PORTS or not _should_release(value):
                continue
            outs[name] = _summarize(value)
            released = True
        if released:
            resolved.instances.pop(src, None)


def _summarize(value):
    if _is_summary(value):
        return value
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, float) and not math.isfinite(value):
        return repr(value)  # inf/nan are not JSON; the record must survive
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        return value if len(value) <= 200 else value[:200] + "…"
    if isinstance(value, (list, tuple)):
        return {"type": "list", "len": len(value)}
    if isinstance(value, dict):
        return {"type": "dict", "len": len(value)}
    return {"type": type(value).__name__}


def _json_text(value):
    """Render the value as canonical JSON, or None when JSON cannot hold it.

    The one legality rule the driver's records share: ``carry.json``
    decides what a run may hand the next one by it, and a search node's
    recorded winner by it too. Two copies of this ``try`` would be one
    ``allow_nan`` apart from silently carrying a value the other dropped.
    """
    try:
        return json.dumps(value, sort_keys=True, allow_nan=False)
    except (TypeError, ValueError):
        return None


def _carryable(value):
    """Judge one output for ``carry.json``, which is state, not storage.

    Returns the value with a True flag when it is JSON-legal and small
    enough to carry, and ``(None, False)`` when it is not. Record
    streams and already-released summaries are refused without dumps
    (ADR-0048).
    """
    if _is_summary(value) or _too_big_to_carry(value):
        return None, False
    text = _json_text(value)
    if text is None or len(text) > _CARRY_LIMIT:
        return None, False
    return value, True


def _collect_flags(order, node_outputs):
    """Collect the findings any node raised, split ``(loud, notes)``.

    The channel is an output literally named ``flags``: a list of
    ``{"level", "code", "message"}`` (the shape
    :class:`~dskit.pipeline.kinds_report.RunReport` emits). Read
    defensively — a malformed entry is rendered as-is rather than
    dropped, because the one thing a findings channel must never do is
    swallow a finding it did not recognise.
    """
    loud, notes = [], []
    for key in order:
        raised = node_outputs.get(key, {}).get("flags")
        if not isinstance(raised, (list, tuple)):
            continue
        for flag in raised:
            if isinstance(flag, dict):
                level = str(flag.get("level", "note"))
                row = (key, str(flag.get("code", "flag")), str(flag.get("message", "")))
            else:
                level, row = "note", (key, "flag", str(flag))
            (loud if level.upper() == "LOUD" else notes).append(row)
    return loud, notes


#: What a search node PRODUCES, and the name the run RECORDS it under
#: (ADR-0043). Two names, because the outputs are the kind's vocabulary
#: (``best_params`` is what a search kind returns) and the record is the
#: run's (``winner`` is what a reader of a summary asks about). The
#: WINNER ITSELF is first; the rest merely describe it. This is the one
#: owner of both spellings — the record writes through it, and every
#: reader of either name (the driver's own apply step included) asks
#: :func:`_winner_names`, so no site can be missed when it is edited.
_SEARCH_WINNER_FIELDS = (("best_params", "winner"), ("best_score", "winner_score"))


def _winner_names():
    """Name the winner field, as ``(produced, recorded)``.

    The summary's readers ask here instead of re-spelling
    ``best_params``/``winner`` beside the constant that owns them: a
    reader that fell out of step with the writer would report every fold
    as winner-less and print agreement where the folds disagreed.

    Returns
    -------
    tuple
        The two names of :data:`_SEARCH_WINNER_FIELDS`' first entry: the
        output a search kind produces its winner under, and the key the
        run's record carries it as.
    """
    return _SEARCH_WINNER_FIELDS[0]


def _search_record(seam, outputs):
    """One search node's metadata: its trials, and the winner it chose.

    PRESENCE, not value, is the signal (ADR-0043): a kind that emitted
    ``best_params`` at all reported a winner — even a winner of ``None``
    — and a kind that emitted none reports nothing, because no search
    kind is obliged to choose. A produced value JSON cannot hold is
    named in ``winner_dropped`` rather than coerced: a record that
    invented a printable stand-in would be reporting a winner the search
    never picked.

    Parameters
    ----------
    seam : _SearchSeam
        The node's re-execution seam, whose ``calls`` counts its trials.
    outputs : dict
        What the search node's ``run`` returned — empty when it raised
        before returning anything.

    Returns
    -------
    dict
        ``trials_executed`` always; ``winner`` / ``winner_score`` for
        each field the kind produced JSON-legally; ``winner_dropped``,
        the produced names JSON could not hold, only when non-empty.
    """
    record = {"trials_executed": seam.calls}
    dropped = []
    for produced, recorded in _SEARCH_WINNER_FIELDS:
        if produced not in outputs:
            continue
        if _json_text(outputs[produced]) is None:
            dropped.append(produced)
        else:
            record[recorded] = outputs[produced]
    if dropped:
        record["winner_dropped"] = dropped
    return record


def _node_metrics(outputs) -> dict:
    """Extract what a node's outputs contribute to the sinks.

    Top-level numeric scalars, plus every numeric leaf of an output
    literally named ``metrics`` — never bulk payloads.
    """
    out = {}
    for key, value in outputs.items():
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            out[key] = value
        elif key == "metrics" and isinstance(value, dict):
            for mk, mv in value.items():
                if isinstance(mv, (int, float)) and not isinstance(mv, bool):
                    out[f"metrics.{mk}"] = mv
    return out


# ---------------------------------------------------------------------------
# The result
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DocumentRunResult:
    """What one invocation of the universal execution file produced.

    Named to coexist with the stage-list runner's ``RunResult`` while
    the migration completes — one package must not export two different
    result types under one name.

    Parameters
    ----------
    run_dir : str
        Absolute path to this run's directory; every artifact below is
        also on disk there.
    state : str
        ``"ran"`` (exit 0) — every non-halted node completed;
        ``"halted"`` (exit 3) — a gate said NO-GO and its descendants
        were skipped, which is a RESULT; ``"error"`` (exit 1) — a node
        failed and the remaining order was aborted.
    node_states : dict
        ``node key -> "ok" | "halted" | "error" | "not_run"``.
    outputs : dict
        Each node's outputs, in full — the winner pass's, where a search
        replaced them.
    run_hash : str
        Identity of what was computed: the document's identity plus the
        sources' fingerprints. Its first 8 characters name ``run_dir``.
    halted_at : str, optional
        The node that halted or errored; ``""`` for a clean run.
    error : str, optional
        The failing node's traceback; ``""`` when none failed.
    prev_run : str, optional
        The run dir ``$prev`` bound against; ``""`` on a first run.
    warnings : tuple of str, optional
        What the planner warned about.
    seconds : dict, optional
        Wall time per node, restated from the winner pass for any node a
        search re-executed.
    search : dict, optional
        What the run's search nodes did (ADR-0043), keyed by node key so
        K>1 searches stay distinguishable: ``trials_executed``, the
        ``winner`` and ``winner_score`` the kind produced, the nodes the
        winner re-ran. Empty — and absent from every artifact — for a
        document that declares no search node.

    Examples
    --------
    Results come from :func:`run_document`, but the class is a plain
    record and builds directly::

        result = DocumentRunResult(
            run_dir="/tmp/pipeline_runs/demo-2026-01-01-0badc0de",
            state="ran",
            node_states={"events": "ok"},
            outputs={"events": {"events": []}},
            run_hash="0badc0de" * 8,
        )
        result.exit_code
        # -> 0
    """

    run_dir: str
    state: str
    node_states: dict
    outputs: dict
    run_hash: str
    halted_at: str = ""
    error: str = ""
    prev_run: str = ""
    warnings: tuple = ()
    seconds: dict = field(default_factory=dict)
    search: dict = field(default_factory=dict)

    @property
    def exit_code(self) -> int:
        """The process exit code this state means: 0 ran, 3 NO-GO, 1 error."""
        return {"ran": 0, "halted": 3, "error": 1}[self.state]

    @property
    def verdict(self) -> str:
        """One line naming the outcome — the report's headline."""
        if self.state == "error":
            first = self.error.strip().splitlines()
            return f"ERROR at `{self.halted_at}` — {first[-1] if first else 'failed'}"
        if self.state == "halted":
            skipped = sum(1 for s in self.node_states.values() if s == "halted")
            return (
                f"NO-GO — halted at `{self.halted_at}`; "
                f"{skipped} downstream node(s) skipped"
            )
        return f"RAN — all {len(self.node_states)} node(s) completed"


# ---------------------------------------------------------------------------
# The lifecycle
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _ResolvedRun:
    """Everything RESOLVE settled, before the first node runs.

    The phase's whole job is to make a run identifiable and refuse it
    loudly while nothing has been written: the sources are built and
    fingerprinted, the cuts are materialized and bound, and the identity
    hash those two determine names the run directory. Every field here is
    read by EXECUTE or RECORD; nothing is recomputed downstream, which is
    what keeps the recorded identity and the executed run the same run.
    """

    run_dir: str
    run_hash: str
    prev_dir: str
    prev: dict
    splits: object
    splits_info: dict
    instances: dict
    payload: dict


@dataclass
class _Execution:
    """What the EXECUTE pass produced, node by node.

    Mutable and shared by the pass, because a node's outputs are the next
    node's inputs and the halt set grows as gates decide. It is also the
    complete record RECORD writes down: after the pass this object holds
    every status, timing, output, ``$prev`` binding and search tally the
    run dir is built from.
    """

    node_states: dict = field(default_factory=dict)
    node_outputs: dict = field(default_factory=dict)
    seconds: dict = field(default_factory=dict)
    search_meta: dict = field(default_factory=dict)
    prev_bindings: dict = field(default_factory=dict)
    halted: set = field(default_factory=set)
    halted_at: str = ""
    error_text: str = ""
    state: str = "ran"


@dataclass
class _NodeAttempt:
    """One node's attempt at running — its outputs and its search seam.

    The seam is carried on the attempt rather than returned because the
    ERROR path needs it too: a search node that fails after its trials
    still has to record how many it executed, and a value returned by a
    call that raised is a value nobody has.
    """

    outputs: dict = None
    seam: object = None
    winner_reran: tuple = ()
    winner_seconds: dict = field(default_factory=dict)


def _run_root(document):
    """Where this document's run directories live, absolute and expanded."""
    outputs_cfg = document.outputs
    return os.path.abspath(
        os.path.expanduser(
            (outputs_cfg.run_root if outputs_cfg is not None else "")
            or "./pipeline_runs"
        )
    )


def _validated_asof(asof):
    """Today (UTC) when none was given, and always a ``YYYY-MM-DD`` string."""
    if asof is None:
        from datetime import datetime, timezone

        asof = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if not isinstance(asof, str) or not re.match(_ASOF_OK, asof):
        raise ConfigError([f"asof must be 'YYYY-MM-DD', got {asof!r}"])
    return asof


def _source_instances(document, the_plan):
    """Build and fingerprint the ``data``- and ``labels``-role nodes.

    Sources AND labels: what a run consumed is its features and its
    outcomes both. Fingerprinting only the features lets two runs over
    the same ladder and OPPOSITE outcomes hash the same, which would put
    the second in the first's run dir. Labels params may carry references
    (only ``data`` is required fully literal), and a node whose params are
    not yet resolved cannot be built here — those simply contribute
    nothing.

    Every instance built is PINNED for execution. A labels node rebuilt at
    execute would re-scan a store that may have grown mid-run (live
    recorders append), handing the run outcomes its identity never hashed;
    the pinned instance lets the node serve ``run()`` from the same scan
    its ``fingerprint()`` saw.

    Parameters
    ----------
    document : PipelineDocument
        The document being run.
    the_plan : Plan
        The resolved plan, for roles and node classes.

    Returns
    -------
    tuple
        ``(instances, fingerprints, edges, declines)`` — the pinned nodes
        by key, their fingerprints, the data edges each source reported,
        and the source keys whose class does not implement ``data_edge``.
    """
    instances = {}
    fingerprints = {}
    edges = {}
    declines = set()
    for key in the_plan.order:
        role = the_plan.role_of(key)
        if role not in ("data", "labels") or key in the_plan.deferred_params:
            continue
        spec = document.expanded[key]
        node = the_plan.resolved[key].cls(
            key, spec.params, mode=spec.mode, artifact=spec.artifact
        )
        fp = node.fingerprint()
        if fp is not None:
            fingerprints[key] = fp
        instances[key] = node
        if role != "data":
            continue  # only a source anchors a split or declares an edge
        if type(node).data_edge is Node.data_edge:
            declines.add(key)  # the class does not implement the hook
        edge = node.data_edge()
        if edge is not None:
            edges[key] = edge
    return instances, fingerprints, edges, declines


def _run_splits(document, the_plan, instances, edges, declines):
    """Settle the cuts this run will use, as ``(splits, splits_info)``.

    Trailing splits materialize HERE and nowhere else: their windows are
    counted backward from the data's edge, which only a source knows, and
    the sources are complete the instant they are built. WHERE the cuts
    are is then settled, but WHICH INSTANT each record is cut on may still
    need the per-event extents — so bounds bind after materialization, and
    a trailing spec's policy rides through.
    """
    splits = _materialize_splits(
        document.splits,
        edges,
        sorted(k for k in instances if the_plan.role_of(k) == "data"),
        declines=declines,
    )
    splits = _bind_event_bounds(splits, instances, the_plan.role_of)
    return splits, (splits.to_obj() if splits is not None else {})


def _open_run_dir(document, asof, run_hash):
    """Claim this run's directory and find the one before it.

    Returns ``(run_dir, prev_dir, prev)``. An occupied directory is a
    refusal, not an overwrite: same name + asof + identity means this
    exact run already happened.
    """
    run_root = _run_root(document)
    run_dir = os.path.join(run_root, f"{document.name}-{asof}-{run_hash[:8]}")
    if os.path.isdir(run_dir) and os.listdir(run_dir):
        raise ValueError(
            f"run dir {run_dir} already exists and is not empty — same "
            "name+asof+identity means this exact run already happened; "
            "remove it deliberately to repeat"
        )
    prev_dir = _find_prev_run(run_root, document.name, run_dir)
    prev = {}
    if prev_dir is not None:
        with open(os.path.join(prev_dir, "carry.json"), encoding="utf-8") as fh:
            prev = json.load(fh)
    return run_dir, prev_dir, prev


def _resolve_run(document, the_plan, asof):
    """Run step 4 RESOLVE: fingerprint, cut, hash, and claim the run dir.

    Parameters
    ----------
    document : PipelineDocument
        The document being run.
    the_plan : Plan
        The resolved plan.
    asof : str
        The validated ``YYYY-MM-DD`` as-of date.

    Returns
    -------
    _ResolvedRun
        Everything EXECUTE and RECORD read. ``config.json``,
        ``plan.json`` and a first ``resolved.json`` are on disk by the
        time it returns.
    """
    instances, fingerprints, edges, declines = _source_instances(document, the_plan)
    splits, splits_info = _run_splits(document, the_plan, instances, edges, declines)
    identity = _strip_non_identity(
        _strip_notes(document.to_obj()), DOC_NON_IDENTITY_SECTIONS
    )
    run_hash = _canonical_hash({"document": identity, "data_fingerprint": fingerprints})
    run_dir, prev_dir, prev = _open_run_dir(document, asof, run_hash)

    os.makedirs(run_dir, exist_ok=True)
    _write_json(os.path.join(run_dir, "config.json"), document.to_obj())
    _write_json(os.path.join(run_dir, "plan.json"), the_plan.to_obj())
    payload = {
        "document_hash": document.hash,
        "run_hash": run_hash,
        "asof": asof,
        "splits": splits_info,
        "data_fingerprint": fingerprints,
        "prev_run": prev_dir or "",
        "driver_version": DRIVER_VERSION,
    }
    _write_json(os.path.join(run_dir, "resolved.json"), payload)
    return _ResolvedRun(
        run_dir=run_dir,
        run_hash=run_hash,
        prev_dir=prev_dir or "",
        prev=prev,
        splits=splits,
        splits_info=splits_info,
        instances=instances,
        payload=payload,
    )


def _open_run_log(run_dir):
    """Attach this run's log sinks; returns what :func:`_close_run_log` needs.

    The run.log file alone is not enough: a long run (a training node's
    epochs, a search node's trials) showed the operator NOTHING until the
    summary table printed at the end, because run.log was the only sink. A
    model that diverges at epoch 2 has to be visible at epoch 2. So the
    driver also streams to stderr — stdout carries the run's REPORT, which
    is piped and parsed — and only when the caller has not already
    installed their own handler, so an embedding application's logging
    setup is never doubled.
    """
    pipeline_logger = logging.getLogger("dskit.pipeline")
    handler = logging.FileHandler(os.path.join(run_dir, "run.log"), encoding="utf-8")
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(name)s %(levelname)s %(message)s")
    )
    prior_level = pipeline_logger.level
    pipeline_logger.addHandler(handler)
    stream_handler = None
    if not any(
        isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler)
        for h in (*pipeline_logger.handlers, *logging.getLogger().handlers)
    ):
        stream_handler = logging.StreamHandler(sys.stderr)
        stream_handler.setFormatter(logging.Formatter("%(message)s"))
        pipeline_logger.addHandler(stream_handler)
    pipeline_logger.setLevel(logging.INFO)
    return pipeline_logger, handler, stream_handler, prior_level


def _close_run_log(pipeline_logger, handler, stream_handler, prior_level):
    """Detach what :func:`_open_run_log` attached and restore the level."""
    pipeline_logger.removeHandler(handler)
    handler.close()
    if stream_handler is not None:
        pipeline_logger.removeHandler(stream_handler)
        stream_handler.close()
    pipeline_logger.setLevel(prior_level)


def _run_one_node(attempt, key, spec, the_plan, ctx, run, instances):
    """Materialize, build, validate and run ONE node, onto ``attempt``.

    What this body performs is the execute-time tail of the D-145
    lifecycle: ``validate_inputs -> run -> validate_outputs``. There is
    deliberately no ``validate_params`` call to find here — that half ran
    at plan time in the planner, and runs again inside ``Node.__init__``
    for whatever is constructed below, so a node reaching this point has
    been through it whether it was pinned at RESOLVE or built now.

    A ``search``-role node — and only a search node — is handed the
    subgraph re-execution seam (docs/24 §8); every other node's context is
    untouched and ``ctx.rerun`` stays None.

    Parameters
    ----------
    attempt : _NodeAttempt
        Filled in place, so a raise still leaves the seam readable.
    key : str
        The node's key in the document.
    spec : NodeSpec
        The node's declared spec.
    the_plan : Plan
        The resolved plan.
    ctx : NodeContext
        The run frame every node shares.
    run : _Execution
        The pass's state — read for prior outputs, written for bindings.
    instances : dict
        The nodes RESOLVE pinned, by key; a key absent here is built now.

    Returns
    -------
    None
        Everything the caller needs is on ``attempt``.
    """
    params = _materialize(
        spec.params,
        f"pipeline.{key}.params",
        run.node_outputs,
        ctx.splits_info,
        ctx.prev,
        run.prev_bindings,
    )
    inputs = {
        port: _materialize(
            ref,
            f"pipeline.{key}.inputs.{port}",
            run.node_outputs,
            ctx.splits_info,
            ctx.prev,
            run.prev_bindings,
        )
        for port, ref in spec.inputs.items()
    }
    node = instances.get(key) or the_plan.resolved[key].cls(
        key, params, mode=spec.mode, artifact=spec.artifact
    )
    problems = node.validate_inputs(inputs)
    if problems:
        raise ConfigError([f"{key}: {p}" for p in problems])
    node_ctx = ctx
    if the_plan.role_of(key) == "search":
        attempt.seam = _SearchSeam(
            key,
            the_plan,
            run.node_outputs,
            ctx.splits_info,
            ctx.prev,
            trial_ctx=replace(ctx, tracker=None),
        )
        node_ctx = replace(ctx, rerun=attempt.seam)
    out = node.run(node_ctx, inputs)
    problems = node.validate_outputs(out)
    if problems:
        raise ConfigError([f"{key}: {p}" for p in problems])
    attempt.outputs = out
    if attempt.seam is None:
        return
    # Recorded BEFORE the winner is applied (ADR-0043): a winner-flip
    # refusal must still report the winner that caused it.
    run.search_meta[key] = _search_record(attempt.seam, out)
    produced, _ = _winner_names()
    winner = out.get(produced)
    if winner:
        attempt.winner_reran, attempt.winner_seconds = attempt.seam.apply_winner(
            winner, ctx, run.prev_bindings
        )
        run.search_meta[key]["winner_reran"] = list(attempt.winner_reran)


def _record_error(run, key, t0):
    """Record the node that failed and stop the pass at it."""
    run.seconds[key] = round(time.perf_counter() - t0, 6)
    run.node_states[key] = "error"
    run.halted_at, run.state = key, "error"
    run.error_text = traceback.format_exc()
    _log.error("node %s: FAILED\n%s", key, run.error_text)


def _record_success(run, key, attempt, trackers, t0):
    """Record a completed node, plus any nodes a search winner re-ran.

    Records and sinks must reflect the FINAL pass (spec §8): the winner
    re-execution replaced those nodes' outputs, so their timings and
    metrics are restated from that pass.
    """
    run.seconds[key] = round(time.perf_counter() - t0, 6)
    run.node_outputs[key] = attempt.outputs
    run.node_states[key] = "ok"
    trackers.log_metrics(key, _node_metrics(attempt.outputs))
    _log.info("node %s: ok in %.3fs", key, run.seconds[key])
    for reran in attempt.winner_reran:
        run.seconds[reran] = attempt.winner_seconds[reran]
        trackers.log_metrics(reran, _node_metrics(run.node_outputs[reran]))
        _log.info(
            "node %s: re-executed with %s's winning overrides in %.3fs",
            reran,
            key,
            run.seconds[reran],
        )


def _apply_verdict(run, key, the_plan, outputs):
    """Halt every DAG descendant of a gate that said NO-GO.

    Not a linear break — independent branches keep running, and a halt is
    a RESULT (exit code 3), never an error.
    """
    if (
        the_plan.role_of(key) not in _VERDICT_ROLES
        or outputs.get("verdict") != "NO-GO"
    ):
        return
    downstream = the_plan.descendants(key)
    run.halted |= downstream
    if not run.halted_at:
        run.halted_at, run.state = key, "halted"
    _log.info(
        "node %s: NO-GO — halting %d descendant(s): %s",
        key,
        len(downstream),
        sorted(downstream),
    )


def _execute_plan(document, the_plan, ctx, resolved, trackers):
    """Run step 5 EXECUTE: every node in plan order, recording each outcome.

    ONE ``log_params`` per run goes first (the Tracker contract): the five
    identity fields plus every node's declared params, flattened to the
    override spelling — dotted, so no knob can shadow an identity field.
    Sent before any node runs, so however far the run gets, its full
    config is findable in every sink.

    Parameters
    ----------
    document : PipelineDocument
        The document being run.
    the_plan : Plan
        The resolved plan, whose ``order`` is the execution order.
    ctx : NodeContext
        The run frame every node shares.
    resolved : _ResolvedRun
        RESOLVE's products; its pinned instances are reused here.
    trackers : Tracker
        The open tracking sinks.

    Returns
    -------
    _Execution
        Every node's status, timing and outputs — including the nodes
        that never ran.
    """
    run = _Execution()
    trackers.log_params(
        {
            "name": document.name,
            "asof": ctx.asof,
            "document_hash": document.hash,
            "run_hash": resolved.run_hash,
            "nodes": ",".join(the_plan.order),
            **_tracked_params(document.expanded, the_plan.order),
        }
    )
    for key in the_plan.order:
        spec = document.expanded[key]
        if key in run.halted:
            run.node_states[key] = "halted"
            continue
        _log.info("node %s: start (%s)", key, the_plan.resolved[key].ref)
        t0 = time.perf_counter()
        attempt = _NodeAttempt()
        try:
            _run_one_node(attempt, key, spec, the_plan, ctx, run, resolved.instances)
        except Exception:  # noqa: BLE001 — recorded, then abort
            if attempt.seam is not None:
                run.search_meta[key] = _search_record(
                    attempt.seam, attempt.outputs or {}
                )
            _record_error(run, key, t0)
            break
        _record_success(run, key, attempt, trackers, t0)
        _release_spent(the_plan, run, resolved)
        _apply_verdict(run, key, the_plan, attempt.outputs)
    for key in the_plan.order:
        run.node_states.setdefault(key, "not_run")
    return run


def _write_node_records(run_dir, the_plan, run):
    """Write one JSON record per node, in execution order."""
    nodes_dir = os.path.join(run_dir, "nodes")
    os.makedirs(nodes_dir, exist_ok=True)
    for i, key in enumerate(the_plan.order, start=1):
        record = {
            "node": key,
            "uses": the_plan.resolved[key].ref,
            "role": the_plan.role_of(key),
            "status": run.node_states[key],
            "seconds": run.seconds.get(key),
            "outputs": {
                name: _summarize(value)
                for name, value in run.node_outputs.get(key, {}).items()
            },
        }
        record.update(run.search_meta.get(key, {}))
        if run.node_states[key] == "error":
            record["error"] = run.error_text
        _write_json(os.path.join(nodes_dir, f"{i:02d}-{key}.json"), record)


def _write_carry(run_dir, node_outputs):
    """Write ``carry.json`` — every JSON-small output the next run may bind."""
    carry = {}
    for key, outs in node_outputs.items():
        kept = {}
        for name, value in outs.items():
            carried, ok = _carryable(value)
            if ok:
                kept[name] = carried
        if kept:
            carry[key] = kept
    _write_json(os.path.join(run_dir, "carry.json"), carry)


def _report_lines(document, asof, the_plan, resolved, run, result):
    """Build ``report.md`` — the human read of one run.

    Findings come FIRST, above the identity block and above the node
    table (I-232). "all 14 node(s) completed" is true of a healthy run and
    of one that found an edge and deployed nothing, so a report that leads
    with node status buries the only line that separates them. A flag is a
    finding a human reads, never a machine verdict: it does NOT touch
    ``result.state`` or the exit code (owner ruling, 2026-08-15 — the
    contract stays 0 ran / 3 NO-GO / 1 error).
    """
    lines = [f"**{result.verdict}**", ""]
    loud, notes = _collect_flags(the_plan.order, run.node_outputs)
    if loud:
        lines += ["## ⚠ LOUD", ""]
        lines += [f"- **[{key}] {code}** — {message}" for key, code, message in loud]
        lines.append("")
    if notes:
        lines += ["## Notes", ""]
        lines += [f"- [{key}] {code} — {message}" for key, code, message in notes]
        lines.append("")
    previous = (
        os.path.basename(resolved.prev_dir)
        if resolved.prev_dir
        else "— (first of the series)"
    )
    lines += [
        f"- run: `{document.name}-{asof}-{resolved.run_hash[:8]}`",
        f"- document hash: `{document.hash[:16]}…`",
        f"- previous run: {previous}",
        "",
        "| node | role | status | seconds |",
        "|---|---|---|---|",
    ]
    lines += [
        f"| {key} | {the_plan.role_of(key)} | {run.node_states[key]} | "
        f"{run.seconds.get(key, '—')} |"
        for key in the_plan.order
    ]
    return lines


def _record_run(document, asof, the_plan, resolved, run):
    """Run step 6 RECORD: write everything down and return the result.

    Reached however the pass ended — clean run, NO-GO halt or node error —
    because from the moment the run dir exists every outcome is a RESULT,
    never a traceback lost to the caller.
    """
    _write_node_records(resolved.run_dir, the_plan, run)
    _write_carry(resolved.run_dir, run.node_outputs)
    resolved.payload["prev_bindings"] = run.prev_bindings
    _write_json(os.path.join(resolved.run_dir, "resolved.json"), resolved.payload)

    result = DocumentRunResult(
        run_dir=resolved.run_dir,
        state=run.state,
        node_states=run.node_states,
        outputs=run.node_outputs,
        run_hash=resolved.run_hash,
        halted_at=run.halted_at,
        error=run.error_text,
        prev_run=resolved.prev_dir,
        warnings=the_plan.warnings,
        seconds=run.seconds,
        search=run.search_meta,
    )
    _write_json(
        os.path.join(resolved.run_dir, "result.json"),
        {
            "name": document.name,
            "asof": asof,
            "document_hash": document.hash,
            "run_hash": resolved.run_hash,
            "state": run.state,
            "exit_code": result.exit_code,
            "halted_at": run.halted_at,
            "node_states": run.node_states,
            "prev_run": resolved.prev_dir,
        },
    )
    lines = _report_lines(document, asof, the_plan, resolved, run, result)
    _atomic_write_text(
        os.path.join(resolved.run_dir, "report.md"), "\n".join(lines) + "\n"
    )
    return result


def _journal_execute(step, inputs, outputs, notes):
    """Append an execute row. Function-level import (ADR-0056)."""
    from dskit.journal.hooks import record_execute

    record_execute(
        step[:80],
        inputs=inputs,
        outputs=outputs,
        db_location=outputs,
        notes=notes,
    )


def _node_context(document, asof, resolved, secrets, trackers, fold_index):
    """Build the frame every node's ``run`` receives, from the resolve."""
    return NodeContext(
        name=document.name,
        asof=asof,
        run_dir=resolved.run_dir,
        splits=resolved.splits,
        splits_info=resolved.splits_info,
        secrets=secrets,
        tracker=trackers,
        prev=resolved.prev,
        fold_index=fold_index,
    )


def run_document(
    document, asof=None, registry=None, journal=True, fold_index=None,
) -> DocumentRunResult:
    """Execute one node-map document end to end (docs/24 §9).

    LOAD → IMPORT + PLAN → RESOLVE → EXECUTE → RECORD, one call per
    phase. Steps 1–4 fail loudly BEFORE a run directory exists; from the
    moment the run dir is written, every outcome is RECORDED.

    Parameters
    ----------
    document : PipelineDocument or str
        The document, or a path to its JSON file (LOAD).
    asof : str, optional
        ``YYYY-MM-DD``. Defaults to today (UTC) — pass it explicitly
        anywhere determinism matters.
    registry : NodeKindRegistry, optional
        Where registered kinds resolve; default the toolkit registry.
    journal : bool, optional
        Record an execute row when a child journal is in scope
        (ADR-0056). Walk-forward folds pass ``False`` so one evaluation
        is one row, not one per fold. Pytest is a no-op inside the
        journal package.
    fold_index : int, optional
        This run's 0-based ordinal within a walk-forward, passed to every
        node on :class:`~dskit.pipeline.node.NodeContext`. ``None`` — the
        default — says "not a fold": a standalone run has no ordinal.
        Walk-forward supplies it so a node persisting per-row evidence
        can stamp WHICH fold produced a row (ADR-0064).

    Returns
    -------
    DocumentRunResult
        The run's state, per-node statuses and outputs, and its run dir.

    Raises
    ------
    ConfigError / ValueError / OSError
        From steps 1–4 (bad document, unresolvable uses, rule violation,
        missing env, occupied run dir) — nothing has been written except,
        in the occupied-dir case, nothing at all. Once execution starts,
        failures are recorded in the run dir instead of raised.
        A journal refusal after RECORD also raises (the run dir exists).
    """
    source = document if isinstance(document, str) else ""
    if not isinstance(document, PipelineDocument):
        document = load_document(document)
    the_plan = plan_document(document, registry)
    if document.clock is not None:
        raise ConfigError(
            [
                "clock present: clocked execution is pending the I-222 A/B "
                "ruling — `plan` and `validate` work on this document; `run` "
                "refuses until the ruling lands"
            ]
        )
    asof = _validated_asof(asof)
    if isinstance(document.splits, TrailingSplitSpec):
        # Cheap pre-check, before any source is constructed or scanned: a
        # spec that materialize() will refuse anyway must not cost a full
        # ledger read first. materialize() stays the authority.
        problem = document.splits.unmaterializable_reason()
        if problem:
            raise ConfigError([f"splits.kind 'trailing': {problem}"])

    secrets = load_env(document.env)  # raises listing every missing name
    trackers = _open_sinks(document.tracking)
    try:
        resolved = _resolve_run(document, the_plan, asof)
    except BaseException:
        # A resolve-time refusal must not strand open sinks (an mlflow-
        # style tracker may hold a remote run from __init__).
        trackers.close()
        raise

    log_state = _open_run_log(resolved.run_dir)
    # The document, verbatim, as the first thing in run.log. config.json
    # sits beside it, but a log read on its own must say what ran.
    log_state[0].info(
        "config %s asof=%s\n%s",
        document.name,
        asof,
        json.dumps(document.to_obj(), indent=2, sort_keys=True),
    )
    ctx = _node_context(document, asof, resolved, secrets, trackers, fold_index)
    try:
        run = _execute_plan(document, the_plan, ctx, resolved, trackers)
        result = _record_run(document, asof, the_plan, resolved, run)
        if journal:
            _journal_execute(
                document.name,
                source or document.name,
                result.run_dir,
                f"state={result.state} hash={result.run_hash[:8]} asof={asof}",
            )
        return result
    finally:
        trackers.close()
        _close_run_log(*log_state)


# ---------------------------------------------------------------------------
# Walk-forward (ADR-0027): one derived document per fold, one summary
# ---------------------------------------------------------------------------

_DAY_MS = 24 * 60 * 60 * 1000


@dataclass(frozen=True)
class WalkForwardRunResult:
    """What one walk-forward invocation produced.

    Parameters
    ----------
    summary_dir : str
        Where ``walkforward.json`` and ``report.md`` landed, beside the
        fold runs.
    state : str
        ``"ran"`` — every fold completed; ``"halted"`` — at least one
        fold hit a NO-GO (a halt is a result; later folds still ran);
        ``"error"`` — a fold errored and the remaining folds were not
        attempted.
    folds : tuple of dict
        One dict per fold, in cutoff order:
        ``{"cutoff", "run_dir", "state", "score"}`` (``score`` is
        ``None`` for a halted fold, and for the erroring fold), plus
        ``"search"`` — that fold's per-node search record — ONLY when
        the fold ran one.
    aggregate : dict
        ``n_folds``/``n_scored`` always, mean/std/min/max and the best
        fold once anything scored, and ``"search"`` only when some fold
        searched: per node, how many folds reported a winner and how
        many DISTINCT winners there were (ADR-0043). An HPO-free
        evaluation's summary is byte-identical to the pre-ADR-0043 one.
    document_hash : str
        Identity of the parent document — the fold plan is part of it.

    Examples
    --------
    Results come from :func:`run_walk_forward`, but the class is a plain
    record and builds directly::

        result = WalkForwardRunResult(
            summary_dir="/tmp/pipeline_runs/demo-walkforward-2026-01-01-0badc0de",
            state="ran",
            folds=({"cutoff": "2025-01-01", "run_dir": "", "state": "ran",
                    "score": 1.5},),
            aggregate={"n_folds": 1, "n_scored": 1},
            document_hash="0badc0de" * 8,
        )
        result.exit_code
        # -> 0
    """

    summary_dir: str
    state: str
    folds: tuple
    aggregate: dict
    document_hash: str

    @property
    def exit_code(self) -> int:
        """The process exit code this state means: 0 ran, 3 NO-GO, 1 error."""
        return {"ran": 0, "halted": 3, "error": 1}[self.state]


def _cutoff_ms(cutoff) -> int:
    """Read a ``YYYY-MM-DD`` cutoff as epoch ms at UTC midnight."""
    from datetime import datetime, timezone

    moment = datetime.strptime(cutoff, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    return int(moment.timestamp() * 1000)


def _fold_splits(spec, cutoff, policy=DEFAULT_SPLIT_POLICY) -> TimeSplitConfig:
    """Pin one fold's time cuts, half-open on BOTH boundaries.

    Val is exactly ``[cutoff, cutoff + val_days)`` and train ends strictly
    BEFORE ``cutoff - embargo_days`` — so for midnight-stamped daily
    panels the cutoff day validates (never trains), a ``val_days`` window
    holds exactly ``val_days`` daily stamps, and an ``embargo_days`` band
    excludes exactly ``embargo_days`` of them, with or without an
    embargo (the skeptic pass caught the cutoff-instant record training
    in the no-embargo path and the windows running one stamp long). The
    1ms test band is degenerate by design — a fold's evaluation window
    IS its val split (the search doctrine's "objectives read val"), and
    :class:`TimeSplitConfig` requires strictly ascending cuts.

    ``policy`` is the parent document's declared split policy, stamped
    onto the cuts (ADR-0031): the cuts say WHERE, the policy says WHICH
    INSTANT, and each fold's :func:`run_document` then binds event bounds
    exactly as a standalone run would. The default is hash-neutral —
    ``record`` is dropped from the serialized form.
    """
    cut = _cutoff_ms(cutoff)
    val_end = cut + spec.val_days * _DAY_MS - 1
    train_end = cut - spec.embargo_days * _DAY_MS - 1
    train_start = None
    if spec.train_days != ALL_PRIOR:
        train_start = train_end - spec.train_days * _DAY_MS + 1
        if train_start < 1:
            raise ConfigError(
                f"walkforward: fold {cutoff} train_days={spec.train_days} "
                f"leaves train_start_ms={train_start}"
            )
    kwargs = dict(
        train_end_ms=train_end,
        val_end_ms=val_end,
        test_end_ms=val_end + 1,
        policy=policy,
        train_start_ms=train_start,
    )
    if spec.embargo_days:
        kwargs["val_start_ms"] = cut
        return TimeSplitConfig(**kwargs)
    return TimeSplitConfig(**kwargs)


def _walkforward_refusals(document):
    """Refuse a document walk-forward cannot honour, before any fold runs.

    Three refusals: no ``walkforward`` section (use ``run``), a ``clock``
    (pending the I-222 A/B ruling, exactly as ``run`` refuses), and a
    declared cal band — ADR-0034 v1 folds replace the splits section with
    a degenerate 1 ms test band, which leaves no room for one, and a
    parent document declaring a cal band would silently lose it.
    """
    if document.walkforward is None:
        raise ConfigError(
            [
                "walkforward: this document declares no walkforward section — "
                "add one (folds/val_days/objective) or use `run`"
            ]
        )
    if document.clock is not None:
        raise ConfigError(
            [
                "clock present: clocked execution is pending the I-222 A/B "
                "ruling — walkforward refuses exactly like `run`"
            ]
        )
    if bool(
        getattr(document.splits, "cal_start_ms", None)
        or getattr(document.splits, "cal_days", 0)
    ):
        raise ConfigError(
            [
                "walkforward folds replace the splits section and cannot "
                "carry a cal band (ADR-0034 v1) — remove "
                "splits.cal_start_ms / splits.cal_days or the walkforward "
                "section"
            ]
        )


def _walkforward_summary_dir(document, asof):
    """Claim the summary directory that sits beside this evaluation's folds."""
    summary_dir = os.path.join(
        _run_root(document),
        f"{document.name}-walkforward-{asof}-{document.hash[:8]}",
    )
    if os.path.isdir(summary_dir) and os.listdir(summary_dir):
        raise ValueError(
            f"walk-forward summary dir {summary_dir} already exists and is not "
            "empty — same name+asof+identity means this exact evaluation "
            "already happened; remove it deliberately to repeat"
        )
    return summary_dir


def _declared_policy(document):
    """Read the split policy every fold's pinned cuts carry (ADR-0031).

    The document's own splits section is replaced fold by fold, but its
    declared POLICY rides through: the cuts say WHERE, the policy says
    WHICH INSTANT, and each fold then binds event bounds exactly as a
    standalone run would.
    """
    if document.splits is None:
        return DEFAULT_SPLIT_POLICY
    policy = getattr(document.splits, "policy", DEFAULT_SPLIT_POLICY)
    _log.info(
        "walkforward: the document's own splits section is replaced by "
        "each fold's pinned cuts; its declared policy %r rides every "
        "fold (ADR-0031)",
        policy,
    )
    return policy


def _fold_score(result, target, obj_path, objective):
    """Read the declared objective off one completed fold, as a finite float.

    An unreadable or non-numeric objective is an error, not a blank: a
    fold that cannot report cannot aggregate, and a NaN must never rank
    folds.
    """
    value = _dig(
        result.outputs[target], obj_path, f"walkforward objective {objective!r}"
    )
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"objective must be numeric, got {value!r}")
    if not math.isfinite(value):
        raise ValueError(
            f"objective is non-finite ({value!r}) — NaN/inf cannot "
            "aggregate and must never rank folds"
        )
    return float(value)


def _run_folds(document, spec, asof, registry, policy):
    """Run one derived document per fold cutoff, as ``(folds, state)``.

    Each fold is the same pipeline with ``splits`` replaced by that fold's
    pinned cuts and the name suffixed ``-wf-<cutoff>`` — folds are
    separate run series, so a ``$prev`` carry binds within one fold's
    history and never across folds.

    A fold that HALTS is recorded with no score and later folds still run;
    a halt is a result. A fold that ERRORS stops the loop with everything
    up to it recorded. A :class:`ConfigError` propagates instead: that is
    the DOCUMENT refusing, and it would refuse identically at every
    cutoff, so burying it as one fold's "result" would be a lie.
    """
    base_obj = document.to_obj()
    base_obj.pop("walkforward", None)  # the fold doc IS one fold, not the plan
    target, obj_path = parse_node_ref(spec.objective)
    folds = []
    state = "ran"
    for index, cutoff in enumerate(spec.fold_cutoffs()):
        try:
            fold_obj = copy.deepcopy(base_obj)
            fold_obj["name"] = f"{document.name}-wf-{cutoff}"
            fold_obj["splits"] = _fold_splits(spec, cutoff, policy).to_obj()
            fold_doc = PipelineDocument.from_obj(fold_obj)
            _log.info("walkforward: fold %s -> %s", cutoff, fold_doc.name)
            result = run_document(
                fold_doc, asof=asof, registry=registry, journal=False,
                fold_index=index,
            )
        except ConfigError:
            raise
        except Exception as exc:  # noqa: BLE001 — recorded, then stop
            # run_document RAISES its pre-flight refusals (an occupied fold
            # run dir, a missing env name) rather than returning an error
            # state; letting that propagate here would discard every
            # completed fold's score and write no summary (skeptic pass).
            # The promise is "everything up to it is recorded" — so record.
            folds.append(
                _fold_row(cutoff, "", "error", None, error=f"{type(exc).__name__}: {exc}")
            )
            state = "error"
            _log.error("walkforward: fold %s refused: %s", cutoff, exc)
            break
        fold = _fold_row(cutoff, result.run_dir, result.state, None)
        if result.search:
            # Only when the fold HAD a search node: an always-emitted key
            # would move every HPO-free summary's bytes (ADR-0043).
            fold["search"] = result.search
        if result.state == "ran":
            try:
                fold["score"] = _fold_score(result, target, obj_path, spec.objective)
            except (KeyError, ValueError) as exc:
                fold["state"] = "error"
                fold["error"] = str(exc)
                folds.append(fold)
                state = "error"
                break
        elif result.state == "halted":
            state = "halted" if state != "error" else state
        else:  # error inside the fold run — recorded there; stop the plan
            folds.append(fold)
            state = "error"
            break
        folds.append(fold)
    return folds, state


def _winner_identity(meta):
    """How one fold's search record counts, as ``(reported, identity)``.

    Parameters
    ----------
    meta : dict
        One search node's record on one fold, as :func:`_search_record`
        wrote it.

    Returns
    -------
    tuple
        ``(reported, identity)`` — whether the kind reported a winner at
        all (bool), and that winner's canonical JSON as the string that
        identifies it, or None when it was dropped and so cannot be
        compared with any other fold's.
    """
    produced, recorded = _winner_names()
    if recorded in meta:
        return True, _json_text(meta[recorded])
    if produced in meta.get("winner_dropped", ()):
        return True, None
    return False, None


def _aggregate_search(folds):
    """Per search node, whether the folds AGREED about the winner.

    The ADR-0043 diagnostic: per-fold re-tuning measures the tuning
    procedure, so winners MAY differ fold to fold — and a distinct-winner
    count above one is exactly the instability a reader must not have to
    take on folklore. Dropped winners count as reported (the search did
    choose) but never as distinct (nothing can compare them), so the two
    numbers are reconciled by ``n_folds_dropped``, emitted only when some
    fold dropped one.

    Parameters
    ----------
    folds : list of dict
        The fold rows, each carrying a ``search`` map when its fold ran
        one.

    Returns
    -------
    dict
        ``node key -> {"n_folds_with_winner", "n_distinct_winners"[,
        "n_folds_dropped"]}``. EMPTY when no fold declared a search node,
        which is what keeps an HPO-free summary byte-identical.
    """
    tally = {}
    for fold in folds:
        for key, meta in fold.get("search", {}).items():
            reported, identity = _winner_identity(meta)
            seen = tally.setdefault(key, {"reported": 0, "dropped": 0, "distinct": set()})
            if not reported:
                continue
            seen["reported"] += 1
            if identity is None:
                seen["dropped"] += 1
            else:
                seen["distinct"].add(identity)
    out = {}
    for key, seen in tally.items():
        row = {
            "n_folds_with_winner": seen["reported"],
            "n_distinct_winners": len(seen["distinct"]),
        }
        if seen["dropped"]:
            row["n_folds_dropped"] = seen["dropped"]
        out[key] = row
    return out


def _fold_row(cutoff, run_dir, state, score, **optional):
    """One fold row built from :data:`FOLD_FIELDS`, plus present optionals."""
    row = dict(zip(FOLD_FIELDS, (cutoff, run_dir, state, score)))
    row.update(optional)
    _check_fold_rows([row])
    return row


def _check_fold_rows(folds):
    """Refuse a row missing a required key or carrying one outside the union."""
    allowed = set(FOLD_FIELDS) | set(FOLD_OPTIONAL_FIELDS)
    for index, row in enumerate(folds):
        if not isinstance(row, dict):
            raise ValueError(f"fold row {index} is not an object: {row!r}")
        missing = [key for key in FOLD_FIELDS if key not in row]
        extra = sorted(key for key in row if key not in allowed)
        if missing or extra:
            raise ValueError(
                f"fold row {index} must carry exactly {list(FOLD_FIELDS)} plus "
                f"any of {list(FOLD_OPTIONAL_FIELDS)}; missing={missing} extra={extra}"
            )


def aggregate_folds(folds, select, weight_halflife_folds=0):
    """Aggregate the scored folds, and name the best one by ``select``.

    Parameters
    ----------
    folds : list of dict
        Fold rows as ``_run_folds`` writes them: every key of
        :data:`FOLD_FIELDS`, plus any of :data:`FOLD_OPTIONAL_FIELDS`.
    select : str
        ``"max"`` or ``"min"`` — which score is best.
    weight_halflife_folds : int
        When nonzero, adds a recency-weighted mean with this half-life
        in folds.

    Returns
    -------
    dict
        ``n_folds`` and ``n_scored`` always; ``mean``, ``std``, ``min``,
        ``max``, ``best_cutoff`` and ``best_score`` when any fold scored;
        ``search`` when any fold carried one; ``weighted_mean`` when
        asked for.

    Raises
    ------
    ValueError
        When a row is missing a required key or carries a key outside
        the union of the two tuples.

    Examples
    --------
    Two scored folds, best by ``max``::

        aggregate_folds(
            [
                {"cutoff": "2025-01-01", "run_dir": "a", "state": "ran", "score": 1.0},
                {"cutoff": "2025-02-01", "run_dir": "b", "state": "ran", "score": 2.0},
            ],
            "max",
        )["best_cutoff"]  # '2025-02-01'
    """
    import statistics

    _check_fold_rows(folds)
    scored = [f["score"] for f in folds if f["score"] is not None]
    aggregate = {"n_folds": len(folds), "n_scored": len(scored)}
    search = _aggregate_search(folds)
    if search:
        aggregate["search"] = search
    if not scored:
        return aggregate
    aggregate["mean"] = statistics.fmean(scored)
    aggregate["std"] = statistics.pstdev(scored) if len(scored) > 1 else 0.0
    aggregate["min"] = min(scored)
    aggregate["max"] = max(scored)
    pick = min if select == "min" else max
    best = pick((f for f in folds if f["score"] is not None), key=lambda f: f["score"])
    aggregate["best_cutoff"] = best["cutoff"]
    aggregate["best_score"] = best["score"]
    if weight_halflife_folds:
        n = len(folds)
        indexed = [
            (i, f["score"]) for i, f in enumerate(folds) if f["score"] is not None
        ]
        raw = [
            0.5 ** ((n - 1 - i) / weight_halflife_folds) for i, _ in indexed
        ]
        total = sum(raw)
        aggregate["weighted_mean"] = sum(
            w / total * score for w, (_, score) in zip(raw, indexed)
        )
    return aggregate


def _md_cell(text):
    """Escape one free-form value for a markdown table cell.

    A pipe ENDS a cell, so a value carrying one would split its row into
    an extra column and misalign the table. Every other cell the reports
    print is a constrained token — a cutoff, a state, a node key — and
    the winner is the only one that prints a value a user chose.

    The rule itself is :func:`dskit.pipeline.runs._escape_pipe`, not a
    copy of it. This package emits markdown in two places and the
    accepted ruling was that their FORMATS may differ — a table's shape
    is taste. Its ESCAPING is not: a missed pipe corrupts the row, so
    the rule has one owner and a fix to it cannot reach one report and
    miss the other. What differs here is only WHICH values need it.

    Parameters
    ----------
    text : str
        The rendered value.

    Returns
    -------
    str
        The same text with every pipe backslash-escaped, which GFM
        renders as a literal pipe inside the cell.
    """
    return _escape_pipe(text)


def _winner_cell(meta):
    """Render one fold's winner for the report.

    Its canonical JSON, a named drop when JSON could not hold it, or a
    dash for no winner at all.
    """
    produced, recorded = _winner_names()
    if recorded in meta:
        return f"`{_md_cell(_json_text(meta[recorded]))}`"
    if produced in meta.get("winner_dropped", ()):
        return "dropped (not JSON-legal)"
    return "—"


def _search_cost_line(folds):
    """State what the folds ACTUALLY paid for their searches.

    Counted, never predicted (ADR-0043 §1): every number here is read
    off the records the folds wrote, so a fold that halted before the
    search node is not billed for one, and a fold whose search raised is
    not billed for the winner pass it never reached.

    Parameters
    ----------
    folds : list of dict
        The fold rows, each carrying a ``search`` map when its fold ran
        one.

    Returns
    -------
    str
        One sentence: folds that searched, trials executed, and winner
        passes applied.
    """
    records = [meta for fold in folds for meta in fold.get("search", {}).values()]
    searched = sum(1 for fold in folds if fold.get("search"))
    trials = sum(meta.get("trials_executed", 0) for meta in records)
    passes = sum(1 for meta in records if "winner_reran" in meta)
    return (
        f"Cost, counted: {searched} fold(s) searched, {trials} trial(s) "
        f"executed, {passes} winner pass(es) applied."
    )


def _walkforward_search_lines(folds, aggregate):
    """Build the report's Search section — nothing when no fold searched.

    An HPO-free evaluation must read exactly as it did before ADR-0043,
    so this returns an EMPTY list rather than an empty section. When
    folds did search, it prints what the ADR exists for: per node, how
    many folds chose a winner, how many DIFFERENT winners they chose and
    how many winners could not be compared at all, then the per-fold
    winners themselves — and the run's cost as counted by
    :func:`_search_cost_line`.

    Parameters
    ----------
    folds : list of dict
        The fold rows, in cutoff order.
    aggregate : dict
        The aggregate, read for its ``search`` tally.

    Returns
    -------
    list of str
        Markdown lines to append, or ``[]``.
    """
    search = aggregate.get("search")
    if not search:
        return []
    lines = [
        "",
        "## Search — per-fold re-tune",
        "",
        "Every fold re-tuned independently, which MEASURES the tuning "
        "procedure (ADR-0043): a winner below is that fold's, never a "
        f"shipped configuration. {_search_cost_line(folds)}",
        "",
        "| search node | folds with a winner | distinct winners | dropped |",
        "|---|---|---|---|",
    ]
    lines += [
        f"| {key} | {search[key]['n_folds_with_winner']} | "
        f"{search[key]['n_distinct_winners']} | "
        f"{search[key].get('n_folds_dropped', 0)} |"
        for key in sorted(search)
    ]
    lines += ["", "| fold cutoff | search node | trials | winner |", "|---|---|---|---|"]
    lines += [
        f"| {fold['cutoff']} | {key} | "
        f"{fold['search'][key].get('trials_executed', '—')} | "
        f"{_winner_cell(fold['search'][key])} |"
        for fold in folds
        for key in sorted(fold.get("search", {}))
    ]
    return lines


def _walkforward_report_lines(document, spec, state, folds, aggregate):
    """Build the walk-forward ``report.md`` — one row per fold."""
    lines = [
        f"**WALK-FORWARD {state.upper()}** — {aggregate['n_scored']}/"
        f"{aggregate['n_folds']} fold(s) scored on `{spec.objective}`",
        "",
        f"- document hash: `{document.hash[:16]}…`",
    ]
    if "mean" in aggregate:
        extra = ""
        if "weighted_mean" in aggregate:
            extra = f" · weighted_mean {aggregate['weighted_mean']:.6g}"
        lines.append(
            f"- mean {aggregate['mean']:.6g} · std {aggregate['std']:.6g} · "
            f"best ({spec.select}) {aggregate['best_score']:.6g} at "
            f"{aggregate['best_cutoff']}{extra}"
        )
    lines += ["", "| fold cutoff | state | score | run |", "|---|---|---|---|"]
    for fold in folds:
        score = "—" if fold["score"] is None else f"{fold['score']:.6g}"
        run_name = os.path.basename(fold["run_dir"]) or "—"
        lines.append(
            f"| {fold['cutoff']} | {fold['state']} | {score} | `{run_name}` |"
        )
    return lines + _walkforward_search_lines(folds, aggregate)


def write_walkforward_summary(
    summary_dir, document, asof, spec, state, folds, aggregate
):
    """Write the machine record and the human read of one evaluation.

    Parameters
    ----------
    summary_dir : str
        The walk's summary directory; created when absent.
    document : PipelineDocument
        The walk-forward document the folds were derived from.
    asof : str
        The ``YYYY-MM-DD`` the walk ran as of.
    spec : WalkForwardSpec
        The document's declared walk-forward section.
    state : str
        ``ran``, ``halted`` or ``error``.
    folds : list of dict
        Fold rows as :func:`aggregate_folds` accepts them.
    aggregate : dict
        :func:`aggregate_folds`'s result over ``folds``.

    Examples
    --------
    Record a bounded walk's folds beside its report::

        write_walkforward_summary(
            summary_dir, document, "2026-02-28", document.walkforward,
            "ran", folds, aggregate_folds(folds, "max"),
        )
    """
    os.makedirs(summary_dir, exist_ok=True)
    _write_json(
        os.path.join(summary_dir, "walkforward.json"),
        {
            "name": document.name,
            "asof": asof,
            "document_hash": document.hash,
            "objective": spec.objective,
            "select": spec.select,
            "state": state,
            "folds": folds,
            "aggregate": aggregate,
        },
    )
    lines = _walkforward_report_lines(document, spec, state, folds, aggregate)
    _atomic_write_text(os.path.join(summary_dir, "report.md"), "\n".join(lines) + "\n")


def run_walk_forward(document, asof=None, registry=None) -> WalkForwardRunResult:
    """Run one document's declared ``walkforward`` section (ADR-0027).

    Per fold cutoff, a DERIVED document is built — the same pipeline with
    ``splits`` replaced by that fold's pinned cuts carrying the document's
    declared split ``policy`` (ADR-0031) and the name suffixed
    ``-wf-<cutoff>`` (folds are separate run series: a ``$prev`` carry
    binds within one fold's history, never across folds) — and executed
    through :func:`run_document`, so every fold owns an ordinary,
    reproducible run directory. The declared ``objective`` is collected
    from each completed fold and aggregated (mean/std/min/max, best fold
    by ``select``) into a summary directory
    ``{name}-walkforward-{asof}-{hash8}`` beside the fold runs:
    ``walkforward.json`` (the machine record) + ``report.md``.

    A fold carrying a search node RE-TUNES independently (ADR-0043) —
    which is a MEASUREMENT of the tuning procedure, not an unbiased
    estimate of a tuned model, and costs folds x (one base pass + the
    trials it executed + one winner pass). The summary reports each
    fold's winner and, per search node, how many folds reported one and
    how many DISTINCT ones there were. What SHIPS is the plain ``run``:
    freezing a winner means editing the document (pin the values, drop
    the search node), which moves its hash by design.

    A fold that HALTS (NO-GO) is recorded with no score and later folds
    still run — a halt is a result. A fold that ERRORS stops the loop;
    everything up to it is recorded. An unreadable or non-numeric
    objective on a completed fold is an error — a fold that cannot
    report cannot aggregate. A :class:`ConfigError` from a fold — e.g.
    an event policy whose fold runs find no ``event_bounds()`` source
    (ADR-0024) — propagates instead: the document, not the fold, is
    refusing.

    Parameters
    ----------
    document : PipelineDocument or str
        The document, or a path to its JSON file.
    asof : str, optional
        ``YYYY-MM-DD``; today (UTC) by default.
    registry : NodeKindRegistry, optional
        Where registered kinds resolve; default the toolkit registry.

    Returns
    -------
    WalkForwardRunResult
        The evaluation's state, its per-fold records and the aggregate.

    Raises
    ------
    ConfigError / ValueError
        No walkforward section, a clock, a declared cal band, a bad
        ``asof``, or an occupied summary dir — all before any fold runs.
    """
    if not isinstance(document, PipelineDocument):
        document = load_document(document)
    _walkforward_refusals(document)
    asof = _validated_asof(asof)
    spec = document.walkforward
    summary_dir = _walkforward_summary_dir(document, asof)
    folds, state = _run_folds(
        document, spec, asof, registry, _declared_policy(document)
    )
    aggregate = aggregate_folds(
        folds, spec.select, spec.weight_halflife_folds,
    )
    write_walkforward_summary(
        summary_dir, document, asof, spec, state, folds, aggregate
    )
    result = WalkForwardRunResult(
        summary_dir=summary_dir,
        state=state,
        folds=tuple(folds),
        aggregate=aggregate,
        document_hash=document.hash,
    )
    _journal_execute(
        f"{document.name} walk-forward",
        document.name,
        summary_dir,
        f"state={state} folds={len(folds)} hash={document.hash[:8]} asof={asof}",
    )
    return result
