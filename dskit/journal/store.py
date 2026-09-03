"""Atomic CSV read/write for the two ledgers.

The CSV is the store. Markdown is a projection (see :mod:`.render`).
One writer per child at a time: every append re-reads and rewrites the
whole file, so the duplicate check and the rewrite both run under
:func:`~dskit.journal.base.locked`. A caller that also allocates the id
holds the same lock across that too — it is re-entrant.
"""

from __future__ import annotations

import csv
import io
import os

from .base import (
    ACTION_FIELDS,
    PATH_FIELDS,
    JournalError,
    atomic_write_text,
    locked,
)
from .model import Action, PathRow

__all__ = [
    "append_action_row",
    "append_path_row",
    "read_actions",
    "read_path",
    "write_empty_csv",
]


def write_empty_csv(path, fields):
    """Create a CSV that is headers only.

    Parameters
    ----------
    path : str
        Destination.
    fields : sequence of str
        Header names.
    """
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=list(fields), lineterminator="\n")
    writer.writeheader()
    atomic_write_text(path, buf.getvalue())


def _read_csv(path, fields, builder):
    """Load every row; missing file is an empty ledger."""
    if not os.path.isfile(path):
        raise JournalError([f"missing ledger file {path}"])
    with open(path, encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        if reader.fieldnames is None:
            raise JournalError([f"{path} has no header"])
        got = tuple(reader.fieldnames)
        expected = tuple(fields)
        if got != expected:
            raise JournalError(
                [f"{path} header {got} does not match {expected}"]
            )
        rows = []
        for i, raw in enumerate(reader, start=2):
            try:
                rows.append(builder(raw))
            except JournalError as exc:
                raise JournalError(
                    [f"{path}:{i}: {p}" for p in exc.problems]
                ) from exc
        return rows


def read_actions(root):
    """Load ``actions.csv``.

    Parameters
    ----------
    root : JournalRoot

    Returns
    -------
    list of Action
    """
    return _read_csv(root.actions_csv, ACTION_FIELDS, Action.from_obj)


def read_path(root):
    """Load ``path.csv``.

    Parameters
    ----------
    root : JournalRoot

    Returns
    -------
    list of PathRow
    """
    return _read_csv(root.path_csv, PATH_FIELDS, PathRow.from_obj)


def _write_csv(path, fields, rows):
    """Rewrite the whole CSV from ``rows`` (each a ``to_obj`` dict)."""
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=list(fields), lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    atomic_write_text(path, buf.getvalue())


def append_action_row(root, action):
    """Append one action and return it.

    Parameters
    ----------
    root : JournalRoot
    action : Action

    Returns
    -------
    Action
    """
    with locked(root.decisioning):
        rows = read_actions(root)
        ids = {row.id for row in rows}
        if action.id in ids:
            raise JournalError([f"action id {action.id} already exists"])
        _write_csv(
            root.actions_csv,
            ACTION_FIELDS,
            [row.to_obj() for row in rows] + [action.to_obj()],
        )
    return action


def append_path_row(root, path_row):
    """Append one path row. The action id must already exist.

    Parameters
    ----------
    root : JournalRoot
    path_row : PathRow

    Returns
    -------
    PathRow

    Raises
    ------
    JournalError
        Duplicate path id, or id not in actions.
    """
    with locked(root.decisioning):
        actions = {row.id: row for row in read_actions(root)}
        if path_row.id not in actions:
            raise JournalError(
                [f"promote {path_row.id}: no such action — record it first"]
            )
        existing = read_path(root)
        if any(row.id == path_row.id for row in existing):
            raise JournalError([f"promote {path_row.id}: already on the path"])
        _write_csv(
            root.path_csv,
            PATH_FIELDS,
            [row.to_obj() for row in existing] + [path_row.to_obj()],
        )
    return path_row
