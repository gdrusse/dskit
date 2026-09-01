"""Append an action: locate, skip pytest, write CSV, re-render.

Pytest is a no-op (``PYTEST_CURRENT_TEST``) so child suites do not
pollute the ledger. Set ``DSKIT_JOURNAL_TESTS=1`` to record anyway.
A missing journal in a child-shaped tree refuses (ADR-0056).
"""

from __future__ import annotations

import os

from .base import JournalError, utc_now
from .locate import find_journal
from .model import Action, PathRow, next_id
from .render import render
from .store import append_action_row, append_path_row, read_actions

__all__ = ["append_action", "promote", "under_pytest"]


def under_pytest():
    """Return True when pytest should skip recording.

    Returns
    -------
    bool
    """
    if os.environ.get("DSKIT_JOURNAL_TESTS") == "1":
        return False
    return bool(os.environ.get("PYTEST_CURRENT_TEST"))


def append_action(
    category,
    step,
    inputs="",
    outputs="",
    db_location="",
    notes="",
    executed_at=None,
    start=None,
):
    """Record one action into the child journal, if any.

    Parameters
    ----------
    category : str
        ``acquire`` / ``research`` / ``execute`` / ``production``.
    step : str
        Very short description.
    inputs : str, optional
        Argv, config path, API.
    outputs : str, optional
        Run dir, snapshot, research file.
    db_location : str, optional
        Durable location of the record.
    notes : str, optional
    executed_at : str, optional
        Stamp; default now (UTC).
    start : str, optional
        Locate start directory; default cwd.

    Returns
    -------
    Action or None
        ``None`` when pytest-skipped or no journal (not a child).

    Raises
    ------
    JournalError
        Uninitialized child, or a bad row.
    """
    if under_pytest():
        return None
    root = find_journal(start=start)
    if root is None:
        return None
    rows = read_actions(root)
    action = Action(
        id=next_id(row.id for row in rows),
        category=category,
        step=step,
        executed_at=executed_at or utc_now(),
        inputs=inputs or "",
        outputs=outputs or "",
        db_location=db_location or "",
        notes=notes or "",
    )
    append_action_row(root, action)
    render(root)
    return action


def promote(action_id, criteria, start=None):
    """Put an existing action on the path to production (owner-only).

    Parameters
    ----------
    action_id : str
        An id from ``actions.csv``.
    criteria : str
        ``empirical`` / ``judgemental`` / ``n/a``.
    start : str, optional
        Locate start; default cwd.

    Returns
    -------
    PathRow

    Raises
    ------
    JournalError
        No journal, unknown id, or already promoted.
    """
    root = find_journal(start=start)
    if root is None:
        raise JournalError(
            ["no journal here — run `python -m dskit.journal init` in the child"]
        )
    row = PathRow(id=action_id, criteria=criteria)
    append_path_row(root, row)
    render(root)
    return row
