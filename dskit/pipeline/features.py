"""Built-in STREAM transform kinds + the stream applicator.

The two-layer transform ruling (:class:`~dskit.pipeline.base.
FeatureConfig`): stream kinds live here — venue-neutral operations on the
:class:`~dskit.pipeline.records.MarketRecord` stream, registered WITH an
``apply`` so :func:`apply_stream_steps` can run them for every consumer
(the Runner's generic stages and the backend's venue stages see the SAME
transformed stream). Tensor-building kinds are registered elsewhere by the
model family/adapter that owns them, WITHOUT an apply — this module skips
them, and the backend interprets them inside train/inference.

Causality note: every built-in is pointwise (a record in, a record or
nothing out) — no built-in may look at a later record, so the stream's
ascending-``asof_ms`` guarantee survives every step by construction. A
future windowed kind must preserve that guarantee explicitly.

Built-ins:

* ``filter`` — keep records matching declared conditions. Params:
  ``require_usable`` (bool, default True) and ``where``, a list of
  ``{"field", "op", "value"}`` clauses ANDed together over the record's
  public fields. A ``None`` field value satisfies no clause (the record
  is dropped) except an explicit ``==``/``!=`` against ``null``.
* ``regroup`` — reassign the statistical cluster (``group``). Params:
  ``by`` in ``{"instrument", "contract"}``. Coarser clusters mean more
  conservative stats — the honest direction; there is deliberately no way
  to SPLIT a venue's native clusters into finer, dependence-breaking ones.

Import cost: stdlib only.
"""

from __future__ import annotations

from dataclasses import replace

from dskit.pipeline.base import (
    TRANSFORM_KINDS,
    import_ref,
    is_class_ref,
    register_transform_kind,
)

__all__ = ["apply_stream_steps"]

#: Record fields a filter clause may address.
_FILTER_FIELDS = (
    "venue",
    "instrument",
    "contract",
    "asof_ms",
    "usable",
    "reason",
    "group",
    "bid",
    "ask",
    "mid",
    "lead_frac",
    "cluster",
    "crossed",
)

_OPS = {
    "==": lambda a, b: a == b,
    "!=": lambda a, b: a != b,
    ">": lambda a, b: a > b,
    ">=": lambda a, b: a >= b,
    "<": lambda a, b: a < b,
    "<=": lambda a, b: a <= b,
    "in": lambda a, b: a in b,
    "not-in": lambda a, b: a not in b,
}


def _filter_validator(params):
    errors = []
    unknown = sorted(set(params) - {"require_usable", "where"})
    if unknown:
        errors.append(
            f"unknown param(s) {unknown} — allowed: ['require_usable', 'where']"
        )
    if not isinstance(params.get("require_usable", True), bool):
        errors.append("require_usable must be a bool")
    where = params.get("where", [])
    if not isinstance(where, list):
        errors.append("where must be a list of {field, op, value} clauses")
        where = []
    for i, clause in enumerate(where):
        if not isinstance(clause, dict) or set(clause) != {"field", "op", "value"}:
            errors.append(
                f"where[{i}] must be exactly {{field, op, value}}, got {clause!r}"
            )
            continue
        if clause["field"] not in _FILTER_FIELDS:
            errors.append(
                f"where[{i}].field {clause['field']!r} unknown — "
                f"fields: {list(_FILTER_FIELDS)}"
            )
        if clause["op"] not in _OPS:
            errors.append(
                f"where[{i}].op {clause['op']!r} unknown — ops: {sorted(_OPS)}"
            )
    return errors


def _clause_holds(record, clause) -> bool:
    value = getattr(record, clause["field"])
    if value is None:
        # a missing quote/lead satisfies nothing except an explicit null test
        return clause["op"] in ("==", "!=") and _OPS[clause["op"]](
            None, clause["value"]
        )
    try:
        return bool(_OPS[clause["op"]](value, clause["value"]))
    except TypeError:
        return False  # e.g. mid > "x": a type mismatch keeps nothing


def _apply_filter(records, params):
    require_usable = params.get("require_usable", True)
    where = params.get("where", [])
    for rec in records:
        if require_usable and not rec.usable:
            continue
        if all(_clause_holds(rec, c) for c in where):
            yield rec


def _regroup_validator(params):
    errors = []
    unknown = sorted(set(params) - {"by"})
    if unknown:
        errors.append(f"unknown param(s) {unknown} — allowed: ['by']")
    if params.get("by") not in ("instrument", "contract"):
        errors.append(
            f"by must be 'instrument' or 'contract' (coarser clusters only — "
            f"finer ones would break dependence), got {params.get('by')!r}"
        )
    return errors


def _apply_regroup(records, params):
    by = params["by"]
    for rec in records:
        target = rec.instrument if by == "instrument" else rec.contract
        yield rec if rec.group == target else replace(rec, group=target)


register_transform_kind("filter", _filter_validator, _apply_filter)
register_transform_kind("regroup", _regroup_validator, _apply_regroup)


def apply_stream_steps(records, features):
    """Thread the record stream through every STREAM step of ``features``
    in declared order. A class-reference step (``pkg.module:Attr``) is
    imported and called with the stream-transform contract
    ``target(records, params) -> iterable of records``; backend-owned
    registered kinds (no ``apply``) are passed over — the backend
    interprets those inside its own stages. ``features`` may be ``None``
    (raw stream)."""
    if features is None:
        return records
    for step in features.steps:
        if is_class_ref(step.kind):
            records = import_ref(step.kind)(records, step.params)
            continue
        entry = TRANSFORM_KINDS.get(step.kind)
        if entry is not None and entry["apply"] is not None:
            records = entry["apply"](records, step.params)
    return records
