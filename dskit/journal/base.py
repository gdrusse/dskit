"""Shared errors, UTC stamps, and the CSV header names (ADR-0056).

One name per header tuple — store, render, and the CLI all import these.
A second copy would be a scheduled column drift.
"""

from __future__ import annotations

import os
import tempfile
from datetime import datetime, timezone

__all__ = [
    "ACTION_FIELDS",
    "CATEGORIES",
    "CRITERIA",
    "ID_PREFIX",
    "JournalError",
    "MARKER",
    "PATH_FIELDS",
    "atomic_write_text",
    "utc_now",
]

#: Walk-up marker filename. Locate finds this, never a heuristic over
#: package names.
MARKER = "journal.json"

#: Closed action vocabulary. Validate/certify/publish fold into
#: ``acquire``; bakeoffs and walk-forward fold into ``execute``.
CATEGORIES = ("acquire", "research", "execute", "production")

#: Path-to-production decision criteria. Owner-only (``journal promote``).
CRITERIA = ("empirical", "judgemental", "n/a")

ACTION_FIELDS = (
    "id",
    "category",
    "step",
    "executed_at",
    "inputs",
    "outputs",
    "db_location",
    "notes",
)

PATH_FIELDS = ("id", "criteria")

ID_PREFIX = "A"


class JournalError(ValueError):
    """One or more journal problems; ``str`` joins them, one per line.

    Parameters
    ----------
    problems : sequence of str
        Every problem. Empty is refused so a blank raise cannot exist.

    Examples
    --------
    Build and read the joined message::

        err = JournalError(["missing journal.json", "bad category"])
        str(err)
        # -> missing journal.json
        # -> bad category
    """

    def __init__(self, problems):
        problems = list(problems)
        if not problems:
            raise ValueError("JournalError requires at least one problem")
        self.problems = problems
        super().__init__("\n".join(problems))


def utc_now():
    """Return the current UTC time as ISO-8601, second precision.

    Returns
    -------
    str
        e.g. ``"2026-09-01T22:00:00+00:00"``.
    """
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def atomic_write_text(path, text):
    """Write ``text`` via a same-directory temp + ``os.replace``.

    Parameters
    ----------
    path : str
        Destination file path; its directory must exist.
    text : str
        Full file contents, including a trailing newline when the
        caller wants one.

    Raises
    ------
    JournalError
        If the directory cannot be written.
    """
    directory = os.path.dirname(path) or "."
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".tmp-journal-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except OSError as exc:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise JournalError([f"cannot write {path}: {exc}"]) from exc
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
