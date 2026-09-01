"""Closed-vocab rows. An invalid action or path row cannot exist.

``from_obj`` default-denies unknown keys so a typo is an error, not a
silent extra column. Optional fields on :class:`Action` emit empty
strings, never omitted keys — the CSV header is the contract.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .base import (
    ACTION_FIELDS,
    CATEGORIES,
    CRITERIA,
    ID_PREFIX,
    JournalError,
    MARKER,
    PATH_FIELDS,
)

__all__ = [
    "Action",
    "ID_RE",
    "JournalConfig",
    "PathRow",
    "next_id",
]

ID_RE = re.compile(r"^A\d{4,}$")
_STEP_MAX = 80


def _reject_unknown(errors, obj, allowed, where):
    """Append a problem for every key not in ``allowed``."""
    extra = sorted(set(obj) - set(allowed))
    if extra:
        errors.append(f"{where}: unknown key(s) {extra} — allowed {list(allowed)}")


def _check_str(errors, name, value, *, required=True, max_len=None):
    """Append problems for a string field (required non-empty, or optional)."""
    if value is None:
        if required:
            errors.append(f"{name} is required")
        return ""
    if not isinstance(value, str):
        errors.append(f"{name} must be a string, got {type(value).__name__}")
        return ""
    if required and not value.strip():
        errors.append(f"{name} must be a non-empty string")
    if max_len is not None and len(value) > max_len:
        errors.append(f"{name} must be <= {max_len} characters, got {len(value)}")
    return value


@dataclass(frozen=True)
class JournalConfig:
    """The walk-up marker: where this child's decisioning CSV lives.

    Parameters
    ----------
    decisioning_dir : str
        Directory relative to the child root. Default ``docs/decisioning``.
    notes : str, optional
        Why, never hashed — documentation only.

    Examples
    --------
    Load the skeleton's marker::

        cfg = JournalConfig.from_obj({"decisioning_dir": "docs/decisioning"})
        cfg.decisioning_dir
        # -> docs/decisioning
    """

    decisioning_dir: str = "docs/decisioning"
    notes: str = ""

    def __post_init__(self):
        """Refuse an invalid marker."""
        errors = []
        if not isinstance(self.decisioning_dir, str) or not self.decisioning_dir:
            errors.append("decisioning_dir must be a non-empty string")
        elif os_abs(self.decisioning_dir):
            errors.append("decisioning_dir must be a relative path")
        if self.notes is not None and not isinstance(self.notes, str):
            errors.append("notes must be a string")
        if errors:
            raise JournalError(errors)

    @classmethod
    def from_obj(cls, obj):
        """Build from a JSON object; unknown keys refused.

        Parameters
        ----------
        obj : dict
            Marker document.

        Returns
        -------
        JournalConfig

        Raises
        ------
        JournalError
            Unknown keys or a bad ``decisioning_dir``.
        """
        if not isinstance(obj, dict):
            raise JournalError([f"{MARKER} must be a JSON object"])
        errors = []
        _reject_unknown(errors, obj, ("decisioning_dir", "notes"), MARKER)
        if errors:
            raise JournalError(errors)
        return cls(
            decisioning_dir=obj.get("decisioning_dir", "docs/decisioning"),
            notes=obj.get("notes", "") or "",
        )


def os_abs(path):
    """Return True when ``path`` is absolute (POSIX or Windows)."""
    return path.startswith("/") or (len(path) > 1 and path[1] == ":")


@dataclass(frozen=True)
class Action:
    """One row of the actions ledger.

    Parameters
    ----------
    id : str
        ``A0001``-shape, monotonic, never reused.
    category : str
        One of :data:`CATEGORIES`.
    step : str
        Very short description (<= 80 chars).
    executed_at : str
        UTC ISO-8601.
    inputs : str
        Argv, config path, API name — whatever fed the action.
    outputs : str
        Run dir, snapshot id, research markdown path.
    db_location : str
        Where the durable record lives (run dir, onboarding root, …).
    notes : str
        Optional. Retrospective rows say artifacts may be incomplete.

    Examples
    --------
    Build a row the store would append::

        row = Action(
            id="A0001",
            category="execute",
            step="hl-scan",
            executed_at="2026-08-31T00:00:00+00:00",
            inputs="configs/run-hl-scan.json",
            outputs="pipeline_runs/hl-scan-…",
            db_location="pipeline_runs/hl-scan-…",
            notes="retrospective; artifacts may be incomplete",
        )
        row.category
        # -> execute
    """

    id: str
    category: str
    step: str
    executed_at: str
    inputs: str = ""
    outputs: str = ""
    db_location: str = ""
    notes: str = ""

    def __post_init__(self):
        """Refuse an invalid action row."""
        errors = []
        if not isinstance(self.id, str) or not ID_RE.match(self.id):
            errors.append(
                f"id must match {ID_RE.pattern}, got {self.id!r}"
            )
        if self.category not in CATEGORIES:
            errors.append(
                f"category must be one of {CATEGORIES}, got {self.category!r}"
            )
        _check_str(errors, "step", self.step, max_len=_STEP_MAX)
        _check_str(errors, "executed_at", self.executed_at)
        for name in ("inputs", "outputs", "db_location", "notes"):
            value = getattr(self, name)
            if not isinstance(value, str):
                errors.append(f"{name} must be a string, got {type(value).__name__}")
        if errors:
            raise JournalError(errors)

    def to_obj(self):
        """Ordered dict matching :data:`ACTION_FIELDS`.

        Returns
        -------
        dict
        """
        return {name: getattr(self, name) for name in ACTION_FIELDS}

    @classmethod
    def from_obj(cls, obj):
        """Build from a CSV row dict; unknown keys refused.

        Parameters
        ----------
        obj : dict

        Returns
        -------
        Action

        Raises
        ------
        JournalError
        """
        if not isinstance(obj, dict):
            raise JournalError(["action row must be an object"])
        errors = []
        _reject_unknown(errors, obj, ACTION_FIELDS, "action")
        if errors:
            raise JournalError(errors)
        return cls(
            id=obj.get("id", ""),
            category=obj.get("category", ""),
            step=obj.get("step", ""),
            executed_at=obj.get("executed_at", ""),
            inputs=obj.get("inputs", "") or "",
            outputs=obj.get("outputs", "") or "",
            db_location=obj.get("db_location", "") or "",
            notes=obj.get("notes", "") or "",
        )


@dataclass(frozen=True)
class PathRow:
    """One row of the path-to-production table: an action id + criteria.

    Category, step, and db location are JOINed at render time from
    :class:`Action` so they cannot drift.

    Parameters
    ----------
    id : str
        An existing action id.
    criteria : str
        One of :data:`CRITERIA`.

    Examples
    --------
    Promote a lock::

        row = PathRow(id="A0001", criteria="empirical")
        row.criteria
        # -> empirical
    """

    id: str
    criteria: str

    def __post_init__(self):
        """Refuse an invalid path row."""
        errors = []
        if not isinstance(self.id, str) or not ID_RE.match(self.id):
            errors.append(f"id must match {ID_RE.pattern}, got {self.id!r}")
        if self.criteria not in CRITERIA:
            errors.append(
                f"criteria must be one of {CRITERIA}, got {self.criteria!r}"
            )
        if errors:
            raise JournalError(errors)

    def to_obj(self):
        """Ordered dict matching :data:`PATH_FIELDS`.

        Returns
        -------
        dict
        """
        return {name: getattr(self, name) for name in PATH_FIELDS}

    @classmethod
    def from_obj(cls, obj):
        """Build from a CSV row dict; unknown keys refused.

        Parameters
        ----------
        obj : dict

        Returns
        -------
        PathRow

        Raises
        ------
        JournalError
        """
        if not isinstance(obj, dict):
            raise JournalError(["path row must be an object"])
        errors = []
        _reject_unknown(errors, obj, PATH_FIELDS, "path")
        if errors:
            raise JournalError(errors)
        return cls(id=obj.get("id", ""), criteria=obj.get("criteria", ""))


def next_id(existing):
    """Return the next ``A0001``-shape id after ``existing``.

    Parameters
    ----------
    existing : sequence of str
        Ids already in the ledger.

    Returns
    -------
    str
    """
    n = 0
    for item in existing:
        if isinstance(item, str) and item.startswith(ID_PREFIX):
            try:
                n = max(n, int(item[len(ID_PREFIX):]))
            except ValueError:
                continue
    return f"{ID_PREFIX}{n + 1:04d}"
