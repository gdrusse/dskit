"""Declarative validation — JSON suites, content-addressed results (ADR-0015).

A suite is a JSON document::

    {"name": "prices-basic",
     "notes": "why these rules",
     "rules": [
       {"id": "close-present", "target": "prices", "rule": "not_null",
        "kwargs": {"field": "close"}, "severity": "error",
        "error_if": "!= 0", "notes": "..."},
       ...]}

Rule semantics, dbt's threshold model exactly:

- Every rule produces a FAILING COUNT over the target stream's
  normalized rows (observations + forecasts of one snapshot).
- ``severity`` is ``"error"`` or ``"warn"``; the matching threshold
  (``error_if`` / ``warn_if``, default ``"!= 0"``) is a comparator on
  the failing count. **Warn never blocks.**
- Gating: any tripped error rule -> ``block``; else any tripped warn
  rule -> ``warn``; else ``pass``. A block is a RESULT, not an
  exception — the pipeline's NO-GO philosophy applied to data.

The result is content-addressed (its identity is the canonical hash of
suite identity + snapshot identity + outcome) and registered as a
``validation_result`` whose ``statistics`` carries the full per-rule
evidence. Certification consumes THAT record — never the data.

Built-in rules (stdlib, structure-level — the semantic seam holds):

==================  =====================================================
``not_null``        kwargs ``{field}``; fails rows where ``data[field]``
                    is missing or null.
``unique``          kwargs ``{field}``; fails every row in a duplicate
                    group (nulls skipped).
``accepted_values`` kwargs ``{field, values}``; fails rows whose value
                    is not in ``values`` (nulls skipped).
``in_range``        kwargs ``{field, min?, max?}`` (at least one); fails
                    rows out of bounds or non-numeric (nulls skipped).
``row_count``       kwargs ``{min?, max?}`` (at least one); fails 1 when
                    the stream's row count is out of bounds.
``bitemporal``      no kwargs; fails rows where ``effective_date >
                    acquired_at`` (ADR-0014 re-asserted as evidence).
==================  =====================================================

Import cost: stdlib + this package.
"""

from __future__ import annotations

import json
import os
import re
from collections import Counter
from dataclasses import dataclass, field

from .base import (
    AssetError,
    _check_dict,
    _check_segment,
    _check_str,
    _check_unknown,
    _raise_if,
    canonical_hash,
    parse_utc,
)
from .layout import OnboardingRoot
from .snapshot import find_snapshot_dir

__all__ = ["Rule", "ValidationSuite", "load_suite", "run_suite", "suite_hash"]

_SEVERITIES = ("error", "warn")

#: ``"<comparator> <int>"`` — the whole threshold grammar. Small on purpose.
_THRESHOLD = re.compile(r"^(==|!=|>=|<=|>|<)\s*(\d+)$")

_COMPARE = {
    "==": lambda a, b: a == b, "!=": lambda a, b: a != b,
    ">=": lambda a, b: a >= b, "<=": lambda a, b: a <= b,
    ">": lambda a, b: a > b, "<": lambda a, b: a < b,
}


def _threshold_met(count, expr) -> bool:
    op, n = _THRESHOLD.match(expr).groups()
    return _COMPARE[op](count, int(n))


# ---------------------------------------------------------------------------
# The built-in rules: name -> (allowed kwargs, evaluator). An evaluator
# takes (rows, kwargs) and returns the failing count — nothing else.
# ---------------------------------------------------------------------------


def _value(row, field_name):
    return row["data"].get(field_name)


def _eval_not_null(rows, kw):
    return sum(1 for r in rows if _value(r, kw["field"]) is None)


def _eval_unique(rows, kw):
    counts = Counter(v for r in rows if (v := _value(r, kw["field"])) is not None)
    return sum(c for c in counts.values() if c > 1)


def _eval_accepted_values(rows, kw):
    allowed = kw["values"]
    return sum(1 for r in rows
               if (v := _value(r, kw["field"])) is not None and v not in allowed)


def _eval_in_range(rows, kw):
    lo, hi = kw.get("min"), kw.get("max")
    failing = 0
    for r in rows:
        v = _value(r, kw["field"])
        if v is None:
            continue
        if not isinstance(v, (int, float)) or isinstance(v, bool):
            failing += 1  # non-numeric where a range is asserted IS a failure
        elif (lo is not None and v < lo) or (hi is not None and v > hi):
            failing += 1
    return failing


def _eval_row_count(rows, kw):
    lo, hi = kw.get("min"), kw.get("max")
    n = len(rows)
    return int((lo is not None and n < lo) or (hi is not None and n > hi))


def _eval_bitemporal(rows, _kw):
    failing = 0
    for r in rows:
        try:
            if parse_utc(r["effective_date"]) > parse_utc(r["acquired_at"]):
                failing += 1
        except AssetError:
            failing += 1  # unparseable dates fail the bitemporal assertion
    return failing


_RULES = {
    "not_null": (("field",), ("field",), _eval_not_null),
    "unique": (("field",), ("field",), _eval_unique),
    "accepted_values": (("field", "values"), ("field", "values"), _eval_accepted_values),
    "in_range": (("field", "min", "max"), ("field",), _eval_in_range),
    "row_count": (("min", "max"), (), _eval_row_count),
    "bitemporal": ((), (), _eval_bitemporal),
}


@dataclass(frozen=True, slots=True)
class Rule:
    """One declared check: a rule, its knobs, and its gate.

    Parameters
    ----------
    id : str
        Unique within the suite — how a result names its evidence.
    target : str
        The stream the rule runs over.
    rule : str
        A built-in rule name (see the module docstring).
    kwargs : dict
        The rule's knobs, default-deny per rule.
    severity : str
        ``"error"`` (can block) or ``"warn"`` (never blocks).
    threshold : str
        Comparator on the failing count (``"!= 0"``, ``"> 10"``, ...);
        the JSON key is ``error_if`` or ``warn_if`` to match severity.
    notes : str
        Documentation; excluded from the suite hash.
    """

    id: str
    target: str
    rule: str
    kwargs: dict = field(default_factory=dict)
    severity: str = "error"
    threshold: str = "!= 0"
    notes: str = ""

    def __post_init__(self):
        errors = []
        _check_str(errors, "id", self.id)
        _check_segment(errors, "target", self.target)
        _check_str(errors, "notes", self.notes, non_empty=False)
        _check_dict(errors, "kwargs", self.kwargs)
        if self.rule not in _RULES:
            errors.append(f"rule must be one of {sorted(_RULES)}, got {self.rule!r}")
        if self.severity not in _SEVERITIES:
            errors.append(
                f"severity must be one of {list(_SEVERITIES)}, got {self.severity!r}"
            )
        if not isinstance(self.threshold, str) or not _THRESHOLD.match(self.threshold):
            errors.append(
                f'threshold must be "<op> <int>" (e.g. "!= 0"), got {self.threshold!r}'
            )
        _raise_if(errors)
        allowed, required, _fn = _RULES[self.rule]
        _check_unknown(errors, self.kwargs, allowed, f"rule {self.id!r} kwargs")
        missing = sorted(k for k in required if k not in self.kwargs)
        if missing:
            errors.append(f"rule {self.id!r} missing required kwarg(s) {missing}")
        if self.rule in ("in_range", "row_count") and not (
            "min" in self.kwargs or "max" in self.kwargs
        ):
            errors.append(f"rule {self.id!r} needs at least one of min/max")
        _raise_if(errors)

    def to_obj(self) -> dict:
        out = {"id": self.id, "target": self.target, "rule": self.rule}
        if self.kwargs:
            out["kwargs"] = dict(self.kwargs)
        out["severity"] = self.severity
        out["error_if" if self.severity == "error" else "warn_if"] = self.threshold
        if self.notes:
            out["notes"] = self.notes
        return out

    @classmethod
    def from_obj(cls, obj) -> "Rule":
        errors = []
        _check_dict(errors, "rule", obj)
        _raise_if(errors)
        _check_unknown(
            errors, obj,
            ("id", "target", "rule", "kwargs", "severity", "error_if", "warn_if", "notes"),
            "rule",
        )
        severity = obj.get("severity", "error")
        # The threshold key must MATCH the severity — an error_if on a
        # warn rule is a config bug someone should hear about.
        if severity == "warn" and "error_if" in obj:
            errors.append(f"rule {obj.get('id')!r}: error_if on a warn rule — use warn_if")
        if severity == "error" and "warn_if" in obj:
            errors.append(f"rule {obj.get('id')!r}: warn_if on an error rule — use error_if")
        _raise_if(errors)
        return cls(
            id=obj.get("id", ""),
            target=obj.get("target", ""),
            rule=obj.get("rule", ""),
            kwargs=obj.get("kwargs", {}),
            severity=severity,
            threshold=obj.get("error_if", obj.get("warn_if", "!= 0")),
            notes=obj.get("notes", ""),
        )


@dataclass(frozen=True, slots=True)
class ValidationSuite:
    """A named, hashable set of rules.

    Parameters
    ----------
    name : str
        The suite's human name.
    rules : tuple
        :class:`Rule` instances; at least one, ids unique.
    notes : str
        Documentation; excluded from the suite hash.
    """

    name: str
    rules: tuple
    notes: str = ""

    def __post_init__(self):
        errors = []
        _check_str(errors, "name", self.name)
        _check_str(errors, "notes", self.notes, non_empty=False)
        if not isinstance(self.rules, tuple) or not self.rules:
            errors.append(f"rules must be a non-empty tuple, got {self.rules!r}")
        _raise_if(errors)
        for i, r in enumerate(self.rules):
            if not isinstance(r, Rule):
                errors.append(f"rules[{i}] must be a Rule, got {type(r).__name__}")
        _raise_if(errors)
        ids = [r.id for r in self.rules]
        dupes = sorted({i for i in ids if ids.count(i) > 1})
        if dupes:
            errors.append(f"rule ids must be unique, duplicated: {dupes}")
        _raise_if(errors)

    def to_obj(self) -> dict:
        out = {"name": self.name, "rules": [r.to_obj() for r in self.rules]}
        if self.notes:
            out["notes"] = self.notes
        return out

    @classmethod
    def from_obj(cls, obj) -> "ValidationSuite":
        errors = []
        _check_dict(errors, "suite", obj)
        _raise_if(errors)
        _check_unknown(errors, obj, ("name", "rules", "notes"), "suite")
        rules_in = obj.get("rules", [])
        if not isinstance(rules_in, list):
            errors.append(f"rules must be a list, got {rules_in!r}")
        _raise_if(errors)
        rules_out = []
        for i, o in enumerate(rules_in):
            try:
                rules_out.append(Rule.from_obj(o))
            except AssetError as exc:
                errors.extend(f"rules[{i}]: {e}" for e in exc.errors)
        _raise_if(errors)
        return cls(
            name=obj.get("name", ""),
            rules=tuple(rules_out),
            notes=obj.get("notes", ""),
        )


def load_suite(path) -> ValidationSuite:
    """Read and validate a suite from a JSON file."""
    try:
        with open(path, encoding="utf-8") as fh:
            obj = json.load(fh)
    except OSError as exc:
        raise AssetError([f"cannot read suite file {path!r}: {exc}"]) from exc
    except ValueError as exc:
        raise AssetError([f"suite file {path!r} is not valid JSON: {exc}"]) from exc
    return ValidationSuite.from_obj(obj)


def suite_hash(suite) -> str:
    """The suite's identity: canonical hash of its rules, notes stripped.

    Pinned into every result — a result is only interpretable knowing
    exactly which rules produced it.
    """
    if not isinstance(suite, ValidationSuite):
        raise AssetError(
            [f"suite must be a ValidationSuite, got {type(suite).__name__}"]
        )
    return canonical_hash(suite.to_obj())


def _snapshot_rows(root, source, acq_id, target) -> list:
    """Every normalized row of one stream in one acquisition —
    observations and forecasts both (rules see the whole pull)."""
    rows = []
    for forecasts in (False, True):
        path = os.path.join(root.records_dir(source, acq_id, forecasts=forecasts),
                            f"{target}.jsonl")
        if not os.path.isfile(path):
            continue
        with open(path, encoding="utf-8") as fh:
            for lineno, line in enumerate(fh, start=1):
                if not line.strip():
                    continue
                try:
                    rows.append(json.loads(line))
                except ValueError as exc:
                    raise AssetError(
                        [f"{path}:{lineno} is not valid JSON: {exc}"]
                    ) from exc
    return rows


def run_suite(root, registry, suite, snapshot_vid, origin="validate") -> dict:
    """Run one suite against one snapshot; register the result.

    Parameters
    ----------
    root : OnboardingRoot
        Where the snapshot's normalized rows live.
    registry : Registry
        The P2 registry holding the snapshot record; the result is
        registered here too.
    suite : ValidationSuite
        The rules.
    snapshot_vid : str
        The ``snapshot`` record's version_id.
    origin : str
        Provenance stamp.

    Returns
    -------
    dict
        ``{"result": vid, "gating": "pass"|"warn"|"block",
        "statistics": {...}}``. A block is a RESULT — no exception.
    """
    if not isinstance(root, OnboardingRoot):
        raise AssetError([f"root must be an OnboardingRoot, got {type(root).__name__}"])
    if not isinstance(suite, ValidationSuite):
        raise AssetError(
            [f"suite must be a ValidationSuite, got {type(suite).__name__}"]
        )
    snap = registry.get(snapshot_vid)
    if snap.kind != "snapshot":
        raise AssetError(
            [f"{snapshot_vid!r} is a {snap.kind!r}, not a snapshot"]
        )
    snap_dir = find_snapshot_dir(root, snap.payload["manifest_hash"])
    if snap_dir is None:
        raise AssetError(
            [f"no raw/ directory matches manifest_hash "
             f"{snap.payload['manifest_hash'][:12]}... — snapshot moved or tampered?"]
        )
    source = os.path.basename(os.path.dirname(snap_dir))
    acq_id = os.path.basename(snap_dir)

    results, rows_by_target = [], {}
    for rule in suite.rules:
        if rule.target not in rows_by_target:
            rows_by_target[rule.target] = _snapshot_rows(root, source, acq_id, rule.target)
        rows = rows_by_target[rule.target]
        _allowed, _required, evaluate = _RULES[rule.rule]
        failing = evaluate(rows, rule.kwargs)
        results.append({
            "id": rule.id, "target": rule.target, "rule": rule.rule,
            "severity": rule.severity, "threshold": rule.threshold,
            "failing": failing, "tripped": _threshold_met(failing, rule.threshold),
        })

    # Gating, dbt-style: errors block, warns warn, warn never blocks.
    if any(r["tripped"] and r["severity"] == "error" for r in results):
        gating = "block"
    elif any(r["tripped"] for r in results):
        gating = "warn"
    else:
        gating = "pass"

    statistics = {
        "rows": {t: len(r) for t, r in sorted(rows_by_target.items())},
        "rules": len(results),
        "tripped": sum(r["tripped"] for r in results),
        "results": results,
    }
    result_vid = registry.register(
        "validation_result",
        {
            "name": f"{suite.name}@{snapshot_vid[:8]}",
            "suite_hash": suite_hash(suite),
            "gating": gating,
            "statistics": statistics,
        },
        refs={"snapshot": snapshot_vid},
        origin=origin,
    )
    return {"result": result_vid, "gating": gating, "statistics": statistics}
