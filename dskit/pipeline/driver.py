"""The universal execution file's engine (docs/24 §9): LOAD → IMPORT →
PLAN → RESOLVE → EXECUTE → RECORD.

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
(:meth:`Node.data_edge`); only ``train_days != "all-prior"`` refuses.

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
scratch copy of the outputs. When the node returns a non-empty
``best_params``, the driver re-executes that subgraph ONE final time
with the winning overrides and REPLACES those nodes' outputs, timings,
and sink metrics — everything downstream of the search consumes the
winner pass, and the search node's record carries ``trials_executed``
plus the ``winner_reran`` node list.

Run-over-run state (``$prev``): each run writes ``carry.json`` — every
JSON-small node output — and the next run in the series binds its
``$prev`` references against the newest prior run dir, falling back to
the declared ``default`` (first run, or the referenced output missing);
every binding is recorded in ``resolved.json`` so a silent reset cannot
hide.

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
from dataclasses import dataclass, field, replace

from dskit.pipeline.base import (
    DEFAULT_SPLIT_POLICY,
    SINK_KINDS,
    ConfigError,
    TimeSplitConfig,
    _strip_notes,
    import_ref,
    is_class_ref,
    merge_event_bounds,
)
from dskit.pipeline.document import (
    DOC_NON_IDENTITY_SECTIONS,
    SPLITS_SOURCE,
    PipelineDocument,
    TrailingSplitSpec,
    is_node_ref,
    is_prev_ref,
    load_document,
    parse_node_ref,
    parse_prev_ref,
)
from dskit.pipeline.env import load_env
from dskit.pipeline.node import Node, NodeContext
from dskit.pipeline.planner import _UNSEARCHABLE_ROLES
from dskit.pipeline.planner import plan as plan_document

__all__ = ["DocumentRunResult", "WalkForwardRunResult", "run_document", "run_walk_forward"]

DRIVER_VERSION = "1.0.0"

_ASOF_OK = r"^\d{4}-\d{2}-\d{2}$"

#: Ceiling for one carried output value's canonical JSON, in characters —
#: carry.json holds run-over-run STATE (bankrolls, artifact paths), not
#: datasets.
_CARRY_LIMIT = 20_000

_log = logging.getLogger("dskit.pipeline.driver")


# ---------------------------------------------------------------------------
# Small services
# ---------------------------------------------------------------------------


def _atomic_write_text(path, text) -> None:
    """Write via a same-directory temp file + ``os.replace`` — a reader
    never sees a half-written artifact. (Inline by necessity: the purity
    rule bars importing the application's own atomic-write helper here.)"""
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
    """Construct every configured sink; contract-check class refs the way
    the stage-list resolve did (log_params/log_metrics/close)."""
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


def _apply_param_override(params, node_key, path, value) -> None:
    """Set ``value`` at ``path`` inside one node's (deep-copied) params.

    Overrides may only address EXISTING params: every segment must
    navigate an existing dict key, and the terminal key must already be
    there — creating a missing key is an error, never a feature (a typo
    must not become a silent new knob)."""
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


class _SearchSeam:
    """One search node's subgraph re-execution seam (docs/24 §8).

    Injected as ``ctx.rerun`` for search-role nodes ONLY. For a search
    node S whose ``objective`` references score node T:

    * ``needed`` = ancestors(T) ∪ {T} — the minimal subgraph that can
      produce the objective;
    * ``dirty(overrides)`` = the nodes the override paths address plus
      all their DAG descendants;
    * ``rerun(overrides) -> float`` applies each ``"node.param.path"``
      override into a DEEP COPY of the affected specs' params, re-executes
      ``needed ∩ dirty`` in plan order against a SCRATCH copy of the base
      outputs (clean nodes are read from the base pass, never re-run),
      full per-node lifecycle, and digs the objective path out of T's
      trial outputs. Trials run with the tracker silenced — the sinks
      reflect the final pass, not exploration. A trial that raises
      propagates a clear error naming the trial's overrides.
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
        self._specs = the_plan.document.pipeline
        self._outputs = node_outputs  # the LIVE dict; trials copy, winner writes
        self._splits_info = splits_info
        self._prev = prev
        self._trial_ctx = trial_ctx
        self.calls = 0
        # The planner guaranteed the objective is a $-ref into a declared
        # val-split score node before anything could execute.
        target, path = parse_node_ref(self._specs[key].params["objective"])
        self._target, self._obj_path = target, path
        self.needed = the_plan.ancestors(target) | {target}
        space = self._specs[key].params.get("space")
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
            self._execute(overrides, scratch, self._trial_ctx, {})
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
        """Re-execute ``needed ∩ dirty(overrides)`` ONE final time with the
        winning overrides, replacing those nodes' outputs in the live
        ``node_outputs``. Returns ``(reran_keys, seconds_by_key)``."""
        try:
            return self._execute(
                overrides, self._outputs, ctx, bindings, guard_verdicts=True
            )
        except Exception as exc:
            raise RuntimeError(
                f"search {self._key}: applying winner overrides {overrides!r} "
                f"failed: {exc}"
            ) from exc

    def _execute(self, overrides, outputs, ctx, bindings, *, guard_verdicts=False):
        if not isinstance(overrides, dict):
            raise ValueError(
                f"overrides must be a dict of 'node.param.path' -> value, "
                f"got {overrides!r}"
            )
        per_node = {}
        for target, value in overrides.items():
            parts = target.split(".") if isinstance(target, str) else []
            if len(parts) < 2 or parts[0] not in self._specs:
                raise ValueError(
                    f"override {target!r} must be '<node>.<param.path>' "
                    f"addressing a declared node (declared: {sorted(self._specs)})"
                )
            # The planner refuses unsearchable heads in the DOCUMENT's
            # space; this is the runtime twin, because a custom search
            # kind can hand ctx.rerun any overrides it likes.
            role = self._plan.role_of(parts[0])
            why = _UNSEARCHABLE_ROLES.get(role)
            if why is not None:
                raise ValueError(
                    f"override {target!r} addresses node {parts[0]!r} of "
                    f"role {role!r}, which a search may never re-tune — {why}"
                )
            per_node.setdefault(parts[0], []).append((tuple(parts[1:]), value))
        dirty = set(per_node)
        for head in per_node:
            dirty |= self._plan.descendants(head)
        subgraph = tuple(k for k in self._plan.order if k in self.needed and k in dirty)
        seconds = {}
        for k in subgraph:
            spec = self._specs[k]
            t0 = time.perf_counter()
            raw = copy.deepcopy(spec.params)
            for path, value in per_node.get(k, ()):
                _apply_param_override(raw, k, path, value)
            params = _materialize(
                raw,
                f"pipeline.{k}.params",
                outputs,
                self._splits_info,
                self._prev,
                bindings,
            )
            inputs = {
                port: _materialize(
                    ref,
                    f"pipeline.{k}.inputs.{port}",
                    outputs,
                    self._splits_info,
                    self._prev,
                    bindings,
                )
                for port, ref in spec.inputs.items()
            }
            node = self._plan.resolved[k].cls(
                k, params, mode=spec.mode, artifact=spec.artifact
            )
            problems = node.validate_inputs(inputs)
            if problems:
                raise ConfigError([f"{k}: {p}" for p in problems])
            out = node.run(ctx, inputs)
            problems = node.validate_outputs(out)
            if problems:
                raise ConfigError([f"{k}: {p}" for p in problems])
            if (
                guard_verdicts
                and self._plan.role_of(k) in ("gate", "stat_test")
                and out.get("verdict") == "NO-GO"
                and outputs.get(k, {}).get("verdict") != "NO-GO"
            ):
                raise ValueError(
                    f"{k}: the winner pass flipped this {self._plan.role_of(k)} "
                    "node's verdict to NO-GO after halt decisions were made on "
                    "the base pass — refusing to ride a stale GO"
                )
            outputs[k] = out
            seconds[k] = round(time.perf_counter() - t0, 6)
        return subgraph, seconds


# ---------------------------------------------------------------------------
# Run-series discovery ($prev)
# ---------------------------------------------------------------------------


def _materialize_splits(splits, edges, data_nodes, declines=()):
    """The document's splits as the object nodes will use.

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
    """The newest prior run dir of this series carrying a ``carry.json``
    — ordered by the asof embedded in the dir name (ISO dates sort
    lexicographically), then by mtime for same-asof reruns (the hash
    suffix is identity, not recency, and must not decide). ``None`` on a
    first run."""
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


def _summarize(value):
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


def _carryable(value):
    """The value if it is JSON-legal and small enough to carry, else None
    (with a flag) — carry.json is state, not storage."""
    try:
        text = json.dumps(value, sort_keys=True, allow_nan=False)
    except (TypeError, ValueError):
        return None, False
    if len(text) > _CARRY_LIMIT:
        return None, False
    return value, True


def _collect_flags(order, node_outputs):
    """Findings any node raised, split ``(loud, notes)``.

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


def _node_metrics(outputs) -> dict:
    """What a node's outputs contribute to the sinks: top-level numeric
    scalars, plus every numeric leaf of an output literally named
    ``metrics`` — never bulk payloads."""
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
    (Named to coexist with the stage-list runner's ``RunResult`` while
    the migration completes — one package must not export two different
    result types under one name.)

    ``state``: ``"ran"`` (exit 0) — every non-halted node completed;
    ``"halted"`` (exit 3) — a gate said NO-GO and its descendants were
    skipped, which is a RESULT; ``"error"`` (exit 1) — a node failed and
    the remaining order was aborted.
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

    @property
    def exit_code(self) -> int:
        return {"ran": 0, "halted": 3, "error": 1}[self.state]

    @property
    def verdict(self) -> str:
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


def run_document(document, asof=None, registry=None) -> DocumentRunResult:
    """Execute one node-map document end to end (docs/24 §9).

    Parameters
    ----------
    document : PipelineDocument or str
        The document, or a path to its JSON file (LOAD).
    asof : str, optional
        ``YYYY-MM-DD``. Defaults to today (UTC) — pass it explicitly
        anywhere determinism matters.
    registry : NodeKindRegistry, optional
        Where registered kinds resolve; default the toolkit registry.

    Raises
    ------
    ConfigError / ValueError / OSError
        From steps 1–4 (bad document, unresolvable uses, rule violation,
        missing env, occupied run dir) — nothing has been written except,
        in the occupied-dir case, nothing at all. Once execution starts,
        failures are recorded in the run dir instead of raised.
    """
    # -- 1 LOAD ------------------------------------------------------------
    if not isinstance(document, PipelineDocument):
        document = load_document(document)

    # -- 2+3 IMPORT + PLAN -------------------------------------------------
    the_plan = plan_document(document, registry)
    if document.clock is not None:
        raise ConfigError(
            [
                "clock present: clocked execution is pending the I-222 A/B "
                "ruling — `plan` and `validate` work on this document; `run` "
                "refuses until the ruling lands"
            ]
        )

    # -- 4 RESOLVE ---------------------------------------------------------
    if asof is None:
        from datetime import datetime, timezone

        asof = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if not isinstance(asof, str) or not re.match(_ASOF_OK, asof):
        raise ConfigError([f"asof must be 'YYYY-MM-DD', got {asof!r}"])

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
        # Data fingerprints: data- and labels-role nodes constructed now
        # (data params are fully literal by the data-is-a-source rule;
        # labels params are literal in every shipped adapter) and kept
        # for execution.
        instances = {}
        fingerprints = {}
        edges = {}
        declines = set()
        for key in the_plan.order:
            role = the_plan.role_of(key)
            # Sources AND labels: what a run consumed is its features and
            # its outcomes both. Fingerprinting only the features lets two
            # runs over the same ladder and OPPOSITE outcomes hash the
            # same, which would put the second in the first's run dir.
            # Labels params may carry references (only `data` is required
            # fully literal), and a node whose params are not yet resolved
            # cannot be built here — those simply contribute nothing.
            if role not in ("data", "labels") or key in the_plan.deferred_params:
                continue
            spec = document.pipeline[key]
            node = the_plan.resolved[key].cls(
                key, spec.params, mode=spec.mode, artifact=spec.artifact
            )
            fp = node.fingerprint()
            if fp is not None:
                fingerprints[key] = fp
            # Pin the fingerprinted instance — data AND labels — so execute
            # reuses it. A labels node rebuilt at execute would re-scan a
            # store that may have grown mid-run (live recorders append),
            # handing the run outcomes its identity never hashed; the
            # pinned instance lets the node serve run() from the same scan
            # its fingerprint() saw.
            instances[key] = node
            if role != "data":
                continue  # only a source anchors a split or declares an edge
            if type(node).data_edge is Node.data_edge:
                declines.add(key)  # the class does not implement the hook
            edge = node.data_edge()
            if edge is not None:
                edges[key] = edge

        # Trailing splits materialize HERE and nowhere else: their windows
        # are counted backward from the data's edge, which only a source
        # knows, and the sources are complete the instant they are built.
        splits = _materialize_splits(
            document.splits,
            edges,
            sorted(k for k in instances if the_plan.role_of(k) == "data"),
            declines=declines,
        )
        # WHERE the cuts are is settled; WHICH INSTANT each record is cut on
        # may still need the per-event extents. Bound here, after
        # materialization, so a trailing spec's policy rides through.
        splits = _bind_event_bounds(splits, instances, the_plan.role_of)
        splits_info = splits.to_obj() if splits is not None else {}

        identity = _strip_notes(document.to_obj())
        for section in DOC_NON_IDENTITY_SECTIONS:
            identity.pop(section, None)
        run_hash = _canonical_hash(
            {"document": identity, "data_fingerprint": fingerprints}
        )

        outputs_cfg = document.outputs
        run_root = os.path.abspath(
            os.path.expanduser(
                (outputs_cfg.run_root if outputs_cfg is not None else "")
                or "./pipeline_runs"
            )
        )
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

        os.makedirs(run_dir, exist_ok=True)
        _write_json(os.path.join(run_dir, "config.json"), document.to_obj())
        _write_json(os.path.join(run_dir, "plan.json"), the_plan.to_obj())
        prev_bindings = {}
        resolved_payload = {
            "document_hash": document.hash,
            "run_hash": run_hash,
            "asof": asof,
            "splits": splits_info,
            "data_fingerprint": fingerprints,
            "prev_run": prev_dir or "",
            "driver_version": DRIVER_VERSION,
        }
        _write_json(os.path.join(run_dir, "resolved.json"), resolved_payload)
    except BaseException:
        # A resolve-time refusal must not strand open sinks (an mlflow-
        # style tracker may hold a remote run from __init__).
        trackers.close()
        raise

    # -- 5 EXECUTE -----------------------------------------------------
    pipeline_logger = logging.getLogger("dskit.pipeline")
    handler = logging.FileHandler(os.path.join(run_dir, "run.log"), encoding="utf-8")
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(name)s %(levelname)s %(message)s")
    )
    prior_level = pipeline_logger.level
    pipeline_logger.addHandler(handler)
    # The file alone is not enough: a long run (a training node's epochs, a
    # search node's trials) showed the operator NOTHING until the summary
    # table printed at the end, because run.log was the only sink. A model
    # that diverges at epoch 2 has to be visible at epoch 2. Stream to
    # stderr — stdout carries the run's REPORT, which is piped and parsed —
    # and only when the caller has not already installed their own handler,
    # so an embedding application's logging setup is never doubled.
    stream_handler = None
    if not any(
        isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler)
        for h in (*pipeline_logger.handlers, *logging.getLogger().handlers)
    ):
        stream_handler = logging.StreamHandler(sys.stderr)
        stream_handler.setFormatter(logging.Formatter("%(message)s"))
        pipeline_logger.addHandler(stream_handler)
    pipeline_logger.setLevel(logging.INFO)

    ctx = NodeContext(
        name=document.name,
        asof=asof,
        run_dir=run_dir,
        splits=splits,
        splits_info=splits_info,
        secrets=secrets,
        tracker=trackers,
        prev=prev,
    )
    node_states = {}
    node_outputs = {}
    seconds = {}
    search_meta = {}
    halted = set()
    halted_at = ""
    error_text = ""
    state = "ran"

    try:
        trackers.log_params(
            {
                "name": document.name,
                "asof": asof,
                "document_hash": document.hash,
                "run_hash": run_hash,
                "nodes": ",".join(the_plan.order),
            }
        )
        for key in the_plan.order:
            spec = document.pipeline[key]
            if key in halted:
                node_states[key] = "halted"
                continue
            _log.info("node %s: start (%s)", key, the_plan.resolved[key].ref)
            t0 = time.perf_counter()
            seam = None
            winner_reran, winner_seconds = (), {}
            try:
                params = _materialize(
                    spec.params,
                    f"pipeline.{key}.params",
                    node_outputs,
                    splits_info,
                    prev,
                    prev_bindings,
                )
                inputs = {
                    port: _materialize(
                        ref,
                        f"pipeline.{key}.inputs.{port}",
                        node_outputs,
                        splits_info,
                        prev,
                        prev_bindings,
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
                    # docs/24 §8: search nodes ALONE receive the subgraph
                    # re-execution seam; every other node's context is
                    # untouched (ctx.rerun stays None).
                    seam = _SearchSeam(
                        key,
                        the_plan,
                        node_outputs,
                        splits_info,
                        prev,
                        trial_ctx=replace(ctx, tracker=None),
                    )
                    node_ctx = replace(ctx, rerun=seam)
                out = node.run(node_ctx, inputs)
                problems = node.validate_outputs(out)
                if problems:
                    raise ConfigError([f"{key}: {p}" for p in problems])
                if seam is not None:
                    search_meta[key] = {"trials_executed": seam.calls}
                    winner = out.get("best_params")
                    if winner:
                        winner_reran, winner_seconds = seam.apply_winner(
                            winner, ctx, prev_bindings
                        )
                        search_meta[key]["winner_reran"] = list(winner_reran)
            except Exception:  # noqa: BLE001 — recorded, then abort
                if seam is not None:
                    search_meta[key] = {"trials_executed": seam.calls}
                seconds[key] = round(time.perf_counter() - t0, 6)
                node_states[key] = "error"
                halted_at, state = key, "error"
                error_text = traceback.format_exc()
                _log.error("node %s: FAILED\n%s", key, error_text)
                break
            seconds[key] = round(time.perf_counter() - t0, 6)
            node_outputs[key] = out
            node_states[key] = "ok"
            trackers.log_metrics(key, _node_metrics(out))
            _log.info("node %s: ok in %.3fs", key, seconds[key])
            for reran in winner_reran:
                # Records and sinks must reflect the FINAL pass (spec §8):
                # the winner re-execution replaced these nodes' outputs, so
                # their timings and metrics are restated from that pass.
                seconds[reran] = winner_seconds[reran]
                trackers.log_metrics(reran, _node_metrics(node_outputs[reran]))
                _log.info(
                    "node %s: re-executed with %s's winning overrides in %.3fs",
                    reran,
                    key,
                    seconds[reran],
                )
            if (
                the_plan.role_of(key) in ("gate", "stat_test")
                and out.get("verdict") == "NO-GO"
            ):
                downstream = the_plan.descendants(key)
                halted |= downstream
                if not halted_at:
                    halted_at, state = key, "halted"
                _log.info(
                    "node %s: NO-GO — halting %d descendant(s): %s",
                    key,
                    len(downstream),
                    sorted(downstream),
                )
        for key in the_plan.order:
            node_states.setdefault(key, "not_run")

        # -- 6 RECORD ---------------------------------------------------
        nodes_dir = os.path.join(run_dir, "nodes")
        os.makedirs(nodes_dir, exist_ok=True)
        for i, key in enumerate(the_plan.order, start=1):
            record = {
                "node": key,
                "uses": the_plan.resolved[key].ref,
                "role": the_plan.role_of(key),
                "status": node_states[key],
                "seconds": seconds.get(key),
                "outputs": {
                    name: _summarize(value)
                    for name, value in node_outputs.get(key, {}).items()
                },
            }
            record.update(search_meta.get(key, {}))
            if node_states[key] == "error":
                record["error"] = error_text
            _write_json(os.path.join(nodes_dir, f"{i:02d}-{key}.json"), record)

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

        resolved_payload["prev_bindings"] = prev_bindings
        _write_json(os.path.join(run_dir, "resolved.json"), resolved_payload)

        result = DocumentRunResult(
            run_dir=run_dir,
            state=state,
            node_states=node_states,
            outputs=node_outputs,
            run_hash=run_hash,
            halted_at=halted_at,
            error=error_text,
            prev_run=prev_dir or "",
            warnings=the_plan.warnings,
            seconds=seconds,
        )
        _write_json(
            os.path.join(run_dir, "result.json"),
            {
                "name": document.name,
                "asof": asof,
                "document_hash": document.hash,
                "run_hash": run_hash,
                "state": state,
                "exit_code": result.exit_code,
                "halted_at": halted_at,
                "node_states": node_states,
                "prev_run": prev_dir or "",
            },
        )
        lines = [f"**{result.verdict}**", ""]
        # Findings come FIRST — above the identity block and above the node
        # table (I-232). "all 14 node(s) completed" is true of a healthy run
        # and of one that found an edge and deployed nothing, so a report
        # that leads with node status buries the only line that separates
        # them. A flag is a finding a human reads, never a machine verdict:
        # it does NOT touch result.state or the exit code (owner ruling,
        # 2026-08-15 — the contract stays 0 ran / 3 NO-GO / 1 error).
        loud, notes = _collect_flags(the_plan.order, node_outputs)
        if loud:
            lines += ["## ⚠ LOUD", ""]
            lines += [
                f"- **[{key}] {code}** — {message}" for key, code, message in loud
            ]
            lines.append("")
        if notes:
            lines += ["## Notes", ""]
            lines += [f"- [{key}] {code} — {message}" for key, code, message in notes]
            lines.append("")
        lines += [
            f"- run: `{document.name}-{asof}-{run_hash[:8]}`",
            f"- document hash: `{document.hash[:16]}…`",
            f"- previous run: {os.path.basename(prev_dir) if prev_dir else '— (first of the series)'}",
            "",
            "| node | role | status | seconds |",
            "|---|---|---|---|",
        ]
        lines += [
            f"| {key} | {the_plan.role_of(key)} | {node_states[key]} | "
            f"{seconds.get(key, '—')} |"
            for key in the_plan.order
        ]
        _atomic_write_text(os.path.join(run_dir, "report.md"), "\n".join(lines) + "\n")
        return result
    finally:
        trackers.close()
        pipeline_logger.removeHandler(handler)
        handler.close()
        if stream_handler is not None:
            pipeline_logger.removeHandler(stream_handler)
            stream_handler.close()
        pipeline_logger.setLevel(prior_level)


# ---------------------------------------------------------------------------
# Walk-forward (ADR-0027): one derived document per fold, one summary
# ---------------------------------------------------------------------------

_DAY_MS = 24 * 60 * 60 * 1000


@dataclass(frozen=True)
class WalkForwardRunResult:
    """What one walk-forward invocation produced.

    ``folds`` is one dict per fold, in cutoff order:
    ``{"cutoff", "run_dir", "state", "score"}`` (``score`` is ``None``
    for a halted fold, and for the erroring fold). ``state``: ``"ran"``
    — every fold completed; ``"halted"`` — at least one fold hit a NO-GO
    (a halt is a result; later folds still ran); ``"error"`` — a fold
    errored and the remaining folds were not attempted.
    """

    summary_dir: str
    state: str
    folds: tuple
    aggregate: dict
    document_hash: str

    @property
    def exit_code(self) -> int:
        return {"ran": 0, "halted": 3, "error": 1}[self.state]


def _cutoff_ms(cutoff) -> int:
    """A ``YYYY-MM-DD`` cutoff as epoch ms at UTC midnight."""
    from datetime import datetime, timezone

    moment = datetime.strptime(cutoff, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    return int(moment.timestamp() * 1000)


def _fold_splits(spec, cutoff, policy=DEFAULT_SPLIT_POLICY) -> TimeSplitConfig:
    """The pinned time cuts for one fold, half-open on BOTH boundaries:
    val is exactly ``[cutoff, cutoff + val_days)`` and train ends strictly
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
    ``record`` is dropped from the serialized form."""
    cut = _cutoff_ms(cutoff)
    val_end = cut + spec.val_days * _DAY_MS - 1
    train_end = cut - spec.embargo_days * _DAY_MS - 1
    if spec.embargo_days:
        return TimeSplitConfig(
            train_end_ms=train_end,
            val_start_ms=cut,
            val_end_ms=val_end,
            test_end_ms=val_end + 1,
            policy=policy,
        )
    return TimeSplitConfig(
        train_end_ms=train_end,
        val_end_ms=val_end,
        test_end_ms=val_end + 1,
        policy=policy,
    )


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

    A fold that HALTS (NO-GO) is recorded with no score and later folds
    still run — a halt is a result. A fold that ERRORS stops the loop;
    everything up to it is recorded. An unreadable or non-numeric
    objective on a completed fold is an error — a fold that cannot
    report cannot aggregate. A :class:`ConfigError` from a fold — e.g.
    an event policy whose fold runs find no ``event_bounds()`` source
    (ADR-0024) — propagates instead: the document, not the fold, is
    refusing.
    """
    import statistics

    if not isinstance(document, PipelineDocument):
        document = load_document(document)
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
    if asof is None:
        from datetime import datetime, timezone

        asof = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if not isinstance(asof, str) or not re.match(_ASOF_OK, asof):
        raise ConfigError([f"asof must be 'YYYY-MM-DD', got {asof!r}"])

    spec = document.walkforward
    target, obj_path = parse_node_ref(spec.objective)
    outputs_cfg = document.outputs
    run_root = os.path.abspath(
        os.path.expanduser(
            (outputs_cfg.run_root if outputs_cfg is not None else "")
            or "./pipeline_runs"
        )
    )
    summary_dir = os.path.join(
        run_root, f"{document.name}-walkforward-{asof}-{document.hash[:8]}"
    )
    if os.path.isdir(summary_dir) and os.listdir(summary_dir):
        raise ValueError(
            f"walk-forward summary dir {summary_dir} already exists and is not "
            "empty — same name+asof+identity means this exact evaluation "
            "already happened; remove it deliberately to repeat"
        )
    declared_policy = DEFAULT_SPLIT_POLICY
    if document.splits is not None:
        declared_policy = getattr(document.splits, "policy", DEFAULT_SPLIT_POLICY)
        _log.info(
            "walkforward: the document's own splits section is replaced by "
            "each fold's pinned cuts; its declared policy %r rides every "
            "fold (ADR-0031)",
            declared_policy,
        )

    base_obj = document.to_obj()
    base_obj.pop("walkforward", None)  # the fold doc IS one fold, not the plan
    folds = []
    state = "ran"
    for cutoff in spec.fold_cutoffs():
        try:
            fold_obj = copy.deepcopy(base_obj)
            fold_obj["name"] = f"{document.name}-wf-{cutoff}"
            fold_obj["splits"] = _fold_splits(spec, cutoff, declared_policy).to_obj()
            fold_doc = PipelineDocument.from_obj(fold_obj)
            _log.info("walkforward: fold %s -> %s", cutoff, fold_doc.name)
            result = run_document(fold_doc, asof=asof, registry=registry)
        except ConfigError:
            # A ConfigError is the DOCUMENT refusing, not one fold — the
            # same pipeline refuses identically at every cutoff (e.g. the
            # ADR-0024 bounds binding under a carried event policy,
            # ADR-0031) — so it propagates exactly as `run` raises it,
            # never buried in a summary as one fold's "result".
            raise
        except Exception as exc:  # noqa: BLE001 — recorded, then stop
            # run_document RAISES its pre-flight refusals (an occupied fold
            # run dir, a missing env name) rather than returning an error
            # state; letting that propagate here would discard every
            # completed fold's score and write no summary (skeptic pass).
            # The promise is "everything up to it is recorded" — so record.
            folds.append(
                {
                    "cutoff": cutoff,
                    "run_dir": "",
                    "state": "error",
                    "score": None,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
            state = "error"
            _log.error("walkforward: fold %s refused: %s", cutoff, exc)
            break
        fold = {
            "cutoff": cutoff,
            "run_dir": result.run_dir,
            "state": result.state,
            "score": None,
        }
        if result.state == "ran":
            try:
                value = _dig(
                    result.outputs[target],
                    obj_path,
                    f"walkforward objective {spec.objective!r}",
                )
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    raise ValueError(f"objective must be numeric, got {value!r}")
                if not math.isfinite(value):
                    raise ValueError(
                        f"objective is non-finite ({value!r}) — NaN/inf cannot "
                        "aggregate and must never rank folds"
                    )
                fold["score"] = float(value)
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

    scored = [f["score"] for f in folds if f["score"] is not None]
    aggregate = {"n_folds": len(folds), "n_scored": len(scored)}
    if scored:
        aggregate["mean"] = statistics.fmean(scored)
        aggregate["std"] = statistics.pstdev(scored) if len(scored) > 1 else 0.0
        aggregate["min"] = min(scored)
        aggregate["max"] = max(scored)
        pick = min if spec.select == "min" else max
        best = pick(
            (f for f in folds if f["score"] is not None), key=lambda f: f["score"]
        )
        aggregate["best_cutoff"] = best["cutoff"]
        aggregate["best_score"] = best["score"]

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
    lines = [
        f"**WALK-FORWARD {state.upper()}** — {aggregate['n_scored']}/"
        f"{aggregate['n_folds']} fold(s) scored on `{spec.objective}`",
        "",
        f"- document hash: `{document.hash[:16]}…`",
    ]
    if "mean" in aggregate:
        lines.append(
            f"- mean {aggregate['mean']:.6g} · std {aggregate['std']:.6g} · "
            f"best ({spec.select}) {aggregate['best_score']:.6g} at "
            f"{aggregate['best_cutoff']}"
        )
    lines += ["", "| fold cutoff | state | score | run |", "|---|---|---|---|"]
    for fold in folds:
        score = "—" if fold["score"] is None else f"{fold['score']:.6g}"
        run_name = os.path.basename(fold["run_dir"]) or "—"
        lines.append(
            f"| {fold['cutoff']} | {fold['state']} | {score} | `{run_name}` |"
        )
    _atomic_write_text(os.path.join(summary_dir, "report.md"), "\n".join(lines) + "\n")
    return WalkForwardRunResult(
        summary_dir=summary_dir,
        state=state,
        folds=tuple(folds),
        aggregate=aggregate,
        document_hash=document.hash,
    )
