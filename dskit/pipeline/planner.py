"""IMPORT + PLAN — the document becomes an executable DAG (docs/24 §9.2–3).

Everything here fails loudly and ACCUMULATES: one :class:`ConfigError`
lists every unresolvable ``uses``, every role mismatch, every rule
violation at once. Nothing touches the environment — planning is a pure
function of ``(document, registry)``, so a document can be planned on
any machine its component classes import on.

The role rules (spec §5), enforced over the DAG:

* ``data`` — a source: no inputs, no ``$node`` dependence in params.
* ``capital`` — must wire a ``stat_test`` node's output into one of its
  inputs; a document with un-gated capital refuses to plan. The label
  comes from the CLASS, so no config wording can dodge this.
* ``stat_test`` — toolkit-OWNED kind only: the class must come from an
  owned registry entry, never an import path (the discipline is
  doctrine, not replaceable).
* ``score`` — must declare which split it reads; ``test`` is legal only
  for a terminal evaluation (its outputs feed nothing but ``report``
  nodes).
* ``search`` — its ``objective`` must be a ``$``-reference into a
  ``score`` node that reads the VALIDATION split (selection never sees
  test); ``space`` must be a non-empty dict whose keys address declared
  nodes' EXISTING params (head key checked here; deeper segments at
  execute); each value is either a non-empty list of JSON scalars or a
  non-empty DICT — the search KIND's own range-spec form, which that
  kind validates at execute (a search node's params always defer, its
  ``objective`` being a ``$``-reference by contract); and the
  winner-consistency rule holds — every consumer of a node the search
  would re-execute (a space-addressed node or any of its descendants)
  must itself be re-executed (an ancestor of the objective), be the
  search node, or run after it (a descendant of the search node) —
  otherwise it would consume stale pre-winner outputs (docs/24 §8).
* ``mode``/``artifact`` — trainable roles (``train``/``signal``) only.

``validate_params`` runs here for every node whose params are fully
literal once ``$prev`` defaults substitute — the classmethod exists
precisely so a bad knob surfaces at plan, not hours into a run. Params
still carrying ``$node`` OR ``$splits`` references DEFER to execute
(the values do not exist at plan; a validator must see values, never
raw ``$`` strings); the plan records which nodes deferred.

Import cost: stdlib only.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from dskit.pipeline.base import ConfigError
from dskit.pipeline.document import (
    SEARCH_SPACE_PARAM,
    SPLIT_NAMES,
    SPLITS_SOURCE,
    TRAINABLE_ROLES,
    PipelineDocument,
    is_node_ref,
    is_prev_ref,
    parse_node_ref,
    parse_prev_ref,
)
from dskit.pipeline.node import resolve_uses
from dskit.pipeline.stats import CORRECTIONS

__all__ = ["Plan", "plan"]

#: Roles that may carry ``mode``/``artifact`` (spec §5: trainable). The
#: grammar's own tuple, imported — a second copy here is how a new
#: trainable family would silently be refused its ``mode``.
_TRAINABLE_ROLES = TRAINABLE_ROLES

#: Roles a search space may NEVER address, and why. Two families: the
#: measurement instruments (score/stat_test/gate — a space that can
#: re-aim the ruler is how ``{"val.split": ["test"]}`` plans clean and
#: selection consults the split it must never see), and fingerprinted
#: identity (data/labels — trials rebuild addressed nodes with overridden
#: params, so the run would consume a source or outcomes its identity
#: never hashed, bypassing the resolve-time pinning).
_UNSEARCHABLE_ROLES = {
    "score": (
        "the measurement instrument — an override could re-aim what the "
        "objective reads (e.g. split -> 'test'), letting selection consult "
        "a split it must never see"
    ),
    "stat_test": (
        "the deploy verdict — a space that can re-tune the test is "
        "optimizing the ruler, not the model"
    ),
    "gate": (
        "the deploy verdict — a space that can re-tune the gate is "
        "optimizing the ruler, not the model"
    ),
    "data": (
        "fingerprinted identity — a trial would rebuild the source with "
        "params the run's identity never hashed"
    ),
    "labels": (
        "fingerprinted identity — a trial would re-label outcomes the "
        "run's identity never hashed"
    ),
}


def _substitute_prev_defaults(obj):
    """Copy ``obj``, replacing every ``$prev`` carry with its ``default``.

    Those are the params a FIRST run would see, which is what plan-time
    validation can honestly check.
    """
    if is_prev_ref(obj):
        return parse_prev_ref(obj)[2]
    if isinstance(obj, dict):
        return {k: _substitute_prev_defaults(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return type(obj)(_substitute_prev_defaults(v) for v in obj)
    return obj


def _has_unresolved_ref(obj) -> bool:
    """Report whether ``obj`` still holds a reference the planner cannot resolve.

    Those are ``$node`` outputs (which exist at execute) and ``$splits``
    fields (which exist at resolve). Params carrying either DEFER
    validation: a kind's validator must see values, never raw ``$``
    strings. ``$prev`` carries substitute their literal defaults first,
    so they do not defer.
    """
    if is_prev_ref(obj):
        return False
    if is_node_ref(obj):
        return True
    if isinstance(obj, dict):
        return any(_has_unresolved_ref(v) for v in obj.values())
    if isinstance(obj, (list, tuple)):
        return any(_has_unresolved_ref(v) for v in obj)
    return False


@dataclass(frozen=True)
class Plan:
    """The resolved DAG: what runs, in what order, under which classes.

    Built by :func:`plan`, never by hand — every field is a product of
    the cross-checks that function performs, so an instance constructed
    around them would claim a validation that never happened.

    Parameters
    ----------
    document : PipelineDocument
        The document this plan resolves. Its ``expanded`` map is what
        the order and the edges are over.
    order : tuple of str
        Execution order — a deterministic toposort, ties broken on
        declaration order.
    resolved : dict
        Node key -> :class:`~dskit.pipeline.node.ResolvedUse`: the
        imported class behind each ``uses``.
    edges : tuple
        ``(source, destination)`` pairs, one per ``$node`` reference.
    deferred_params : tuple of str, optional
        Nodes whose params still carry ``$node``/``$splits`` references
        and therefore validate only at execute time.
    warnings : tuple of str, optional
        Non-fatal notes the caller should surface.

    Examples
    --------
    Plan a document and read its order::

        the_plan = plan(document, registry)
        the_plan.order          # ('dataset', 'qhat', 'validate')
        the_plan.role_of('qhat')  # 'train'
    """

    document: PipelineDocument
    order: tuple
    resolved: dict
    edges: tuple
    deferred_params: tuple = ()
    warnings: tuple = ()

    def role_of(self, key) -> str:
        """Report the CLASS-declared role of one planned node.

        Parameters
        ----------
        key : str
            A node key present in this plan.

        Returns
        -------
        str
            The role the node's class declares — never the config's
            optional ``role`` label, which is only cross-checked.
        """
        return self.resolved[key].cls.role

    def descendants(self, key) -> set:
        """Collect every node downstream of ``key``, transitively.

        Parameters
        ----------
        key : str
            A node key present in this plan.

        Returns
        -------
        set of str
            The nodes a NO-GO verdict at ``key`` halts. ``key`` itself
            is not included.
        """
        return _descendants_of(key, self.edges)

    def ancestors(self, key) -> set:
        """Collect every node upstream of ``key``, transitively.

        Parameters
        ----------
        key : str
            A node key present in this plan.

        Returns
        -------
        set of str
            The nodes that feed ``key``. With ``key`` itself added this
            is the minimal subgraph that can produce its outputs — the
            search seam's ``needed`` set (docs/24 §8).
        """
        return _ancestors_of(key, self.edges)

    def to_obj(self) -> dict:
        """Render the ``plan.json`` payload — deterministic, human-diffable.

        Returns
        -------
        dict
            The document hash, the execution order, one entry per node
            (its ``uses``, resolved class, role, ownership, cadence,
            inputs and whether its params deferred), the edge list and
            the warnings. Node entries are read from the document's
            EXPANDED map, so a fanned-out instance appears exactly as a
            hand-written node would (ADR-0039).
        """
        return {
            "document_hash": self.document.hash,
            "order": list(self.order),
            "nodes": {
                key: {
                    "uses": self.resolved[key].ref,
                    "class": f"{self.resolved[key].cls.__module__}:"
                    f"{self.resolved[key].cls.__name__}",
                    "role": self.role_of(key),
                    "owned": self.resolved[key].owned,
                    "every": self.document.expanded[key].every,
                    "inputs": dict(self.document.expanded[key].inputs),
                    "params_validation": "deferred"
                    if key in self.deferred_params
                    else "checked",
                }
                for key in self.order
            },
            "edges": [list(e) for e in self.edges],
            "warnings": list(self.warnings),
        }


def _toposort(keys, deps):
    """Order the graph — deterministic Kahn, ties broken on declaration order.

    Returns ``(order, leftover)``; a non-empty leftover is a cycle.
    """
    remaining = {k: set(d) for k, d in deps.items()}
    order = []
    while True:
        ready = [k for k in keys if k not in order and not remaining[k]]
        if not ready:
            break
        nxt = ready[0]
        order.append(nxt)
        for k in remaining:
            remaining[k].discard(nxt)
    leftover = [k for k in keys if k not in order]
    return tuple(order), leftover


def _accepts_split(cls):
    """Report whether this class takes a ``split`` knob at all.

    The distinction the whole splits-required rule turns on: a capital
    node that REPLAYS a time series has a ``split`` (and defaults it, so
    the document can carry one silently), while a generic budgeted
    selection has no time dimension and must not be dragged into needing
    a splits section it has no use for.
    """
    table = getattr(cls, "_PARAMS", ()) or ()
    if isinstance(table, (tuple, list, set, frozenset)) and "split" in table:
        return True
    hook = getattr(cls, "_allowed", None)
    if callable(hook):
        try:
            return "split" in hook()
        except Exception:  # noqa: BLE001 — a broken hook is not this rule's business
            return False
    return False


def _fitted_errors(key, spec, cls, document):
    """Leakage rules for one ``fitted_transform`` node (ADR-0040).

    A fitted transform learns from ONE split and applies to the others,
    so which split it learned from must be readable from the document —
    the same discipline the ``score`` role's ``split`` carries, for the
    stronger reason: a transform fitted on validation rows leaks
    invisibly, because nothing fails and the scores merely improve.

    Parameters
    ----------
    key : str
        The node's key, prefixed onto every message.
    spec : dskit.pipeline.document.NodeSpec
        The declared node.
    cls : type
        The resolved class, read for its ``default_mode``: an OMITTED
        ``mode`` means whatever the class says it means, so the rule
        cannot key on ``spec.mode`` alone.
    document : dskit.pipeline.document.PipelineDocument
        The whole document, read for its splits section.

    Returns
    -------
    list of str
        One problem per broken rule; empty when the declaration is
        honourable.
    """
    errors = []
    fit_split = spec.params.get("fit_split")
    declared = fit_split is not None and not is_node_ref(fit_split)
    if declared and document.splits is None:
        errors.append(
            f"pipeline.{key}: fit_split {fit_split!r} names a split but the "
            "document declares none — a fitted transform with no splits would "
            "fit on EVERYTHING, which is the leak the knob exists to refuse. "
            "Declare splits, or drop the node"
        )
    mode = spec.mode if spec.mode is not None else getattr(cls, "default_mode", "train")
    if mode == "load":
        return errors  # nothing is fit; the sidecar is checked at run
    if fit_split is None:
        errors.append(
            f"pipeline.{key}: role 'fitted_transform' must declare which split "
            f"it FITS on (params.fit_split in {'/'.join(SPLIT_NAMES)}) — an "
            "undeclared fit split is a leak nobody can see"
        )
    elif declared and fit_split not in SPLIT_NAMES:
        errors.append(
            f"pipeline.{key}: fit_split must be one of "
            f"{'/'.join(SPLIT_NAMES)}, got {fit_split!r}"
        )
    return errors


def plan(document, registry=None) -> Plan:
    """Resolve, cross-check, and order one document (spec §9 steps 2–3).

    Raises
    ------
    ConfigError
        Listing every import failure, role violation, wire-contract
        break, cycle, and plan-checkable param problem at once.
    """
    errors = []
    warnings = []
    # What RUNS, not what was written: with no `foreach` section this IS
    # the declared map (the same object, ADR-0039), and with one it is
    # the shared nodes plus every fanned-out instance.
    specs = document.expanded

    # -- IMPORT: every uses -> a Node subclass; role cross-check ----------
    resolved = {}
    for key, spec in specs.items():
        try:
            resolved[key] = resolve_uses(spec.uses, registry)
        except ValueError as exc:
            errors.append(f"pipeline.{key}: {exc}")
            continue
        cls = resolved[key].cls
        if spec.role is not None and spec.role != cls.role:
            errors.append(
                f"pipeline.{key}: role {spec.role!r} contradicts the class — "
                f"{cls.__name__} declares role {cls.role!r}, and the label is "
                "the class's to give (drop the config's role key or fix the "
                "wiring)"
            )
        if cls.role == "stat_test" and not resolved[key].owned:
            errors.append(
                f"pipeline.{key}: role 'stat_test' is toolkit-owned — "
                f"{spec.uses!r} must be the registered owned kind, never a "
                "custom class (the discipline is doctrine, spec §5)"
            )
    if errors:
        raise ConfigError(errors)

    # -- edges from every $node reference (inputs AND params) -------------
    deps = {key: set() for key in specs}
    for key, spec in specs.items():
        node_refs, _prev = spec.refs()
        for source, _path in node_refs:
            if source != SPLITS_SOURCE:
                deps[key].add(source)
    edges = tuple((src, dst) for dst in specs for src in sorted(deps[dst]))

    order, leftover = _toposort(list(specs), deps)
    if leftover:
        raise ConfigError(
            [f"pipeline: dependency cycle among {leftover} — a run cannot order them"]
        )

    # -- wire contracts: ref paths vs declared producer outputs -----------
    for key, spec in specs.items():
        node_refs, _prev = spec.refs()
        for source, path in node_refs:
            if source == SPLITS_SOURCE:
                continue
            declared = resolved[source].cls.outputs
            if declared is not None and path[0] not in declared:
                errors.append(
                    f"pipeline.{key}: wire '${source}.{'.'.join(path)}' — "
                    f"{resolved[source].cls.__name__} declares outputs "
                    f"{sorted(declared)}, no {path[0]!r}"
                )

    # -- causal doctrine: the split kind a node may live under ------------
    # docs/24 has always said a causal venue refuses a `random` split. The
    # stage list enforced it (a backend's supported_split_kinds, checked
    # in resolve.py); the node map had nowhere to say it, so a randomized
    # cut over settled events planned clean — and a cluster-hashed random
    # cut puts the test set's FUTURE inside the calibrator's past. This is
    # the enforcement catching up to the spec, not a new rule.
    # A capital node DEFAULTS its `split`, so a document can carry one
    # without ever writing the word — and then the splits section is not
    # required, and a capital node with no splits keeps EVERY record. That
    # is a whole-history, in-sample "backtest" that plans, runs and exits
    # 0. The document layer cannot see this (it knows params, not roles);
    # here the classes are resolved, so here is where it is caught.
    if document.splits is None:
        ungoverned = sorted(
            key
            for key, spec in specs.items()
            if resolved[key].cls.role == "capital"
            and "split" not in spec.params
            and _accepts_split(resolved[key].cls)
        )
        if ungoverned:
            errors.append(
                f"splits section required: node(s) {ungoverned} carry role "
                "'capital' and accept a 'split' knob, but neither the node "
                "nor the document names one — with no splits they would size "
                "and replay the ENTIRE history in-sample. Declare splits, or "
                "state the split on the node"
            )

    # ADR-0034 v1: walkforward folds replace the splits section with a
    # degenerate 1 ms test band — no room for a cal band. The driver
    # refuses at run; refusing HERE means `plan`/`validate` cannot bless
    # a document whose only possible run is that refusal.
    if document.walkforward is not None and bool(
        getattr(document.splits, "cal_start_ms", None)
        or getattr(document.splits, "cal_days", 0)
    ):
        errors.append(
            "walkforward folds replace the splits section and cannot carry "
            "a cal band (ADR-0034 v1) — remove splits.cal_start_ms / "
            "splits.cal_days or the walkforward section"
        )

    declared_kind = getattr(document.splits, "kind", None)
    if declared_kind is not None:
        for key in specs:
            lawful = resolved[key].cls.supported_split_kinds
            if lawful is not None and declared_kind not in lawful:
                errors.append(
                    f"pipeline.{key}: splits.kind {declared_kind!r} is unsound "
                    f"for {resolved[key].cls.__name__}, which supports "
                    f"{sorted(lawful)} — its records are a time series, and a "
                    "randomized cut puts the test set's future inside the "
                    "training past (docs/24; the stage list has always "
                    "refused this)"
                )

    # -- role rules over the DAG ------------------------------------------
    roles = {key: resolved[key].cls.role for key in specs}
    for key, spec in specs.items():
        role = roles[key]
        if role == "data":
            if spec.inputs:
                errors.append(
                    f"pipeline.{key}: role 'data' is a source — it takes no "
                    f"inputs, got {sorted(spec.inputs)}"
                )
            else:
                node_refs, prev_refs = spec.refs()
                if node_refs or prev_refs:
                    errors.append(
                        f"pipeline.{key}: role 'data' is a source — its params "
                        "must be fully literal (no $node/$splits/$prev "
                        "references): the source's fingerprint is identity "
                        "material and must be computable before anything runs"
                    )
        if role == "capital":
            # The doctrine is the DIRECT wire: survivors flow through an
            # INPUT from a stat_test node — a decorative params reference
            # does not gate capital (spec §5 "with its survivors wired in").
            gated = False
            for ref in spec.inputs.values():
                try:
                    source, _path = parse_node_ref(ref)
                except ConfigError:  # pragma: no cover — refused upstream
                    continue
                if roles.get(source) == "stat_test":
                    gated = True
            if not gated:
                errors.append(
                    f"pipeline.{key}: role 'capital' must wire a stat_test "
                    "node's output into one of its inputs — un-gated capital "
                    "refuses to plan (spec §5)"
                )
        if role == "fitted_transform":
            errors.extend(
                _fitted_errors(key, spec, resolved[key].cls, document)
            )
        if role == "score":
            split = spec.params.get("split")
            if split not in SPLIT_NAMES:
                errors.append(
                    f"pipeline.{key}: role 'score' must declare which split "
                    f"it reads (params.split in train/val/cal/test), got {split!r}"
                )
            elif split == "cal":
                # ADR-0034: the 'cal' name only exists when the declared
                # splits carve a band — a reader of a band that cannot
                # exist would run on zero rows and exit 0. Refuse at plan.
                has_band = bool(
                    getattr(document.splits, "cal_start_ms", None)
                    or getattr(document.splits, "cal_days", 0)
                )
                if not has_band:
                    errors.append(
                        f"pipeline.{key}: a 'cal' reader needs a declared "
                        "cal band — set splits.cal_start_ms (time) or "
                        "splits.cal_days (trailing)"
                    )
            elif split == "test":
                non_report = sorted(
                    d for d in _descendants_of(key, edges) if roles.get(d) != "report"
                )
                if non_report:
                    errors.append(
                        f"pipeline.{key}: a score node may read 'test' only as "
                        f"the terminal evaluation — {non_report} consume its "
                        "outputs (only report nodes may)"
                    )
        if role == "stat_test":
            # The plan-time mirror of the weights-port rules. The
            # enforcing gate is validate_inputs (it sees the wired
            # values), but a weighted correction with no weights wire —
            # or the converse — is knowable from the spec alone, so it
            # refuses here, before anything runs.
            corr = spec.params.get("correction", "bh")
            entry = CORRECTIONS.get(corr) if isinstance(corr, str) else None
            if entry is not None:
                if entry["needs_weights"] and "weights" not in spec.inputs:
                    errors.append(
                        f"pipeline.{key}: correction {corr!r} needs "
                        "per-instrument weights — wire a weights input"
                    )
                if not entry["needs_weights"] and "weights" in spec.inputs:
                    errors.append(
                        f"pipeline.{key}: a weights input is wired but "
                        f"correction {corr!r} does not use weights"
                    )
        if role == "search":
            errors.extend(_search_errors(key, spec, specs, roles, edges))
        if spec.mode is not None and role not in _TRAINABLE_ROLES:
            errors.append(
                f"pipeline.{key}: mode/artifact apply to trainable roles "
                f"{list(_TRAINABLE_ROLES)} only — {resolved[key].cls.__name__} "
                f"declares role {role!r}"
            )

    # -- plan-time validate_params (the classmethod's whole point) --------
    deferred = []
    for key, spec in specs.items():
        prepared = _substitute_prev_defaults(spec.params)
        if _has_unresolved_ref(prepared):
            deferred.append(key)
            continue
        problems = resolved[key].cls.validate_params(prepared)
        errors.extend(f"pipeline.{key}: {p}" for p in problems)

    if document.clock is not None:
        warnings.append(
            "clock present: clocked execution is pending the I-222 A/B "
            "ruling — plan/validate work; run refuses"
        )

    if errors:
        raise ConfigError(errors)
    return Plan(
        document=document,
        order=order,
        resolved=resolved,
        edges=edges,
        deferred_params=tuple(deferred),
        warnings=tuple(warnings),
    )


def _descendants_of(key, edges) -> set:
    children = {}
    for src, dst in edges:
        children.setdefault(src, set()).add(dst)
    seen, frontier = set(), [key]
    while frontier:
        for child in children.get(frontier.pop(), ()):
            if child not in seen:
                seen.add(child)
                frontier.append(child)
    return seen


def _ancestors_of(key, edges) -> set:
    """Walk edges source-ward — the mirror of :func:`_descendants_of`.

    Returns every node upstream of ``key``, transitively.
    """
    parents = {}
    for src, dst in edges:
        parents.setdefault(dst, set()).add(src)
    seen, frontier = set(), [key]
    while frontier:
        for parent in parents.get(frontier.pop(), ()):
            if parent not in seen:
                seen.add(parent)
                frontier.append(parent)
    return seen


def _is_json_scalar(value) -> bool:
    """Report whether ``value`` is a JSON-legal scalar: null/bool/number/string."""
    if value is None or isinstance(value, (bool, str)):
        return True
    return isinstance(value, (int, float)) and math.isfinite(value)


def _search_errors(key, spec, specs, roles, edges):
    """Collect the search node's wiring-rule violations (spec §8) as plan errors.

    Beyond the objective checks: ``space`` keys must be
    ``'<node>.<param.path>'`` where the node is declared, the HEAD param
    key already exists in that node's params (overrides may only address
    existing params; deeper path segments are checked at execute), the
    node's role is SEARCHABLE (:data:`_UNSEARCHABLE_ROLES` — never the
    measurement instruments, never fingerprinted identity), and the node
    is an ancestor of the objective (an override that cannot move the
    objective is a knob the search cannot turn — the winner value would
    be noise and would never be re-applied to the live run); and a value
    is either a non-empty list of JSON scalars or a non-empty dict — the
    dict form is the search kind's own range-spec grammar, passed through
    untouched here and validated by that kind at execute (a search node's
    params always defer). The winner-consistency rule then
    guarantees no node can consume stale pre-winner outputs: every
    consumer of a dirty-candidate node (a space-addressed node or any of
    its descendants) must be re-executed with the winner (an ancestor of
    the objective node T, or T itself), be the search node, or run after
    the search (a descendant of it).
    """
    problems = []
    objective = spec.params.get("objective")
    target_node = None
    if not is_node_ref(objective):
        problems.append(
            f"pipeline.{key}: search.objective must be a $-reference to a "
            f"score node's output, got {objective!r}"
        )
    else:
        source, _path = parse_node_ref(objective)
        if source in specs:
            target_node = source
        if roles.get(source) != "score":
            problems.append(
                f"pipeline.{key}: search.objective must come from a 'score' "
                f"node — {source!r} declares role {roles.get(source)!r}"
            )
        elif specs[source].params.get("split") != "val":
            problems.append(
                f"pipeline.{key}: selection consults validation only — the "
                f"objective's score node {source!r} reads "
                f"{specs[source].params.get('split')!r}, not 'val' (spec §8)"
            )
    needed = (
        _ancestors_of(target_node, edges) | {target_node}
        if target_node is not None
        else None
    )
    space = spec.params.get(SEARCH_SPACE_PARAM)
    heads = set()
    if not isinstance(space, dict) or not space:
        problems.append(
            f"pipeline.{key}: search.space must be a non-empty dict of "
            f"'node.param.path' -> a non-empty list of JSON scalars or a "
            f"non-empty range-spec dict, got {space!r}"
        )
    else:
        for target, grid in space.items():
            parts = target.split(".") if isinstance(target, str) else []
            head = parts[0] if parts else target
            if head not in specs:
                problems.append(
                    f"pipeline.{key}: search.space addresses {target!r} but no "
                    f"node {head!r} is declared"
                )
            elif len(parts) < 2:
                problems.append(
                    f"pipeline.{key}: search.space key {target!r} must be "
                    "'<node>.<param.path>' — name the param to override"
                )
            else:
                heads.add(head)
                if parts[1] not in specs[head].params:
                    problems.append(
                        f"pipeline.{key}: search.space addresses {target!r} "
                        f"but node {head!r} declares no param {parts[1]!r} — "
                        "overrides may only address EXISTING params, never "
                        "create them"
                    )
                why = _UNSEARCHABLE_ROLES.get(roles.get(head))
                if why is not None:
                    problems.append(
                        f"pipeline.{key}: search.space may not address "
                        f"{target!r} — node {head!r} declares role "
                        f"{roles.get(head)!r}, {why}"
                    )
                elif needed is not None and head not in needed:
                    problems.append(
                        f"pipeline.{key}: search.space addresses {target!r} "
                        f"but {head!r} is not an ancestor of the objective "
                        f"node {target_node!r} — the override cannot move the "
                        "objective, and the winner would never be re-applied "
                        "to the live run (docs/24 §8)"
                    )
            # The STRUCTURAL half of the space rule is the planner's
            # (keys address declared params; winner-consistency below).
            # The VALUE half is SPLIT: the planner owns the one shape
            # every search kind shares — a list grid, when given, is
            # non-empty and holds JSON scalars; a dict must be non-empty
            # — and past that passes a DICT through untouched, because
            # its INTERNALS are the kind's range-spec form (hpo-grid
            # takes scalar lists only; optuna-search adds
            # {"low","high"}). The kind's own grammar normally bites at
            # EXECUTE, not here: a search node's `objective` is a
            # $-reference BY CONTRACT, so its params carry an unresolved
            # ref and _has_unresolved_ref DEFERS its validate_params —
            # under that contract the checks below are the only plan-time
            # guard a space value meets, and a range spec offered to
            # hpo-grid refuses when the node is constructed mid-run (both
            # pinned in tests/pipeline/test_kinds_search.py::
            # TestPlannerRules). The deferral is ref-driven: params with
            # no ref anywhere ARE validated by the kind at plan.
            if isinstance(grid, (list, tuple)):
                if not grid:
                    problems.append(
                        f"pipeline.{key}: search.space[{target!r}] must be a "
                        "non-empty list of JSON scalars — an empty grid has "
                        "nothing to search"
                    )
                else:
                    bad = [v for v in grid if not _is_json_scalar(v)]
                    if bad:
                        problems.append(
                            f"pipeline.{key}: search.space[{target!r}] values "
                            "must be JSON scalars (null/bool/number/string), "
                            f"got {bad!r}"
                        )
            elif not isinstance(grid, dict) or not grid:
                problems.append(
                    f"pipeline.{key}: search.space[{target!r}] must be a "
                    "non-empty list of JSON scalars or a non-empty range-spec "
                    f"dict (the search kind validates its shape), got {grid!r}"
                )
    # Winner-consistency (docs/24 §8): the driver re-executes
    # needed = ancestors(T) ∪ {T} restricted to the dirty set with the
    # winning overrides, replacing those outputs; a node OUTSIDE that set
    # consuming a dirty node's output would have consumed the stale
    # pre-winner pass (descendants of the search run after the winner is
    # applied, so they are safe).
    if target_node is not None and heads:
        dirty = set(heads)
        for head in heads:
            dirty |= _descendants_of(head, edges)
        allowed = needed | {key} | _descendants_of(key, edges)
        offenders = sorted(
            {dst for src, dst in edges if src in dirty and dst not in allowed}
        )
        if offenders:
            problems.append(
                f"pipeline.{key}: node(s) {offenders} consume outputs of the "
                f"search's re-execution set {sorted(dirty & needed)} but are "
                "neither re-executed with the winner (ancestors of the "
                f"objective node {target_node!r}) nor downstream of the search "
                "— they would consume stale pre-winner outputs; wire them "
                f"after '${key}.best_params' or move them out of the dirty "
                "subgraph (docs/24 §8)"
            )
    return problems
