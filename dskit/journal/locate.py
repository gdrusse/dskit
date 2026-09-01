"""Walk-up discovery of a child's journal, or a loud refusal.

Rules (ADR-0056):

* ``DSKIT_JOURNAL_ROOT`` set → that directory must contain
  ``journal.json``, else refuse.
* Walking up: ``journal.json`` found → that child.
* Walking up: ``pyproject.toml`` + ``configs/`` and no marker → this
  is an uninitialized child, refuse.
* Filesystem root with neither → ``None`` (toolkit tests, not a child).
"""

from __future__ import annotations

import json
import os

from .base import ACTION_FIELDS, PATH_FIELDS, JournalError, MARKER, atomic_write_text
from .model import JournalConfig

__all__ = ["JournalRoot", "find_journal", "init_journal", "load_root"]

_MARKER_NOTES = (
    "Walk-up marker for dskit.journal (ADR-0056). Locate finds this file; "
    "CSV + generated README live under decisioning_dir."
)


class JournalRoot:
    """Resolved child + the decisioning directory on disk.

    Parameters
    ----------
    child_root : str
        Absolute directory that holds ``journal.json``.
    config : JournalConfig
        The marker's payload.
    """

    def __init__(self, child_root, config):
        self.child_root = os.path.abspath(child_root)
        self.config = config

    @property
    def decisioning(self):
        """Absolute path of the decisioning directory."""
        return os.path.abspath(
            os.path.join(self.child_root, self.config.decisioning_dir)
        )

    @property
    def actions_csv(self):
        """Absolute path of ``actions.csv``."""
        return os.path.join(self.decisioning, "actions.csv")

    @property
    def path_csv(self):
        """Absolute path of ``path.csv``."""
        return os.path.join(self.decisioning, "path.csv")

    @property
    def readme(self):
        """Absolute path of the generated README."""
        return os.path.join(self.decisioning, "README.md")

    @property
    def research_dir(self):
        """Absolute path of ``docs/research``."""
        return os.path.join(self.child_root, "docs", "research")


def load_root(child_root):
    """Open an already-initialized child.

    Parameters
    ----------
    child_root : str
        Directory containing ``journal.json``.

    Returns
    -------
    JournalRoot

    Raises
    ------
    JournalError
        Missing or unreadable marker.
    """
    child_root = os.path.abspath(child_root)
    path = os.path.join(child_root, MARKER)
    if not os.path.isfile(path):
        raise JournalError([f"no {MARKER} at {child_root}"])
    try:
        with open(path, encoding="utf-8") as fh:
            obj = json.load(fh)
    except (OSError, ValueError) as exc:
        raise JournalError([f"cannot read {path}: {exc}"]) from exc
    return JournalRoot(child_root, JournalConfig.from_obj(obj))


def _child_shaped(directory):
    """Return True when this directory looks like a child that must journal."""
    return os.path.isfile(os.path.join(directory, "pyproject.toml")) and os.path.isdir(
        os.path.join(directory, "configs")
    )


def find_journal(start=None):
    """Locate the journal, refuse an uninitialized child, or return None.

    Parameters
    ----------
    start : str, optional
        Directory to walk from; default ``os.getcwd()``.

    Returns
    -------
    JournalRoot or None

    Raises
    ------
    JournalError
        Override missing a marker, or a child-shaped directory with none.
    """
    override = os.environ.get("DSKIT_JOURNAL_ROOT")
    if override:
        override = os.path.abspath(override)
        marker = os.path.join(override, MARKER)
        if not os.path.isfile(marker):
            raise JournalError(
                [f"DSKIT_JOURNAL_ROOT={override!r} has no {MARKER}"]
            )
        return load_root(override)

    cur = os.path.abspath(start or os.getcwd())
    seen = set()
    while cur not in seen:
        seen.add(cur)
        if os.path.isfile(os.path.join(cur, MARKER)):
            return load_root(cur)
        if _child_shaped(cur):
            raise JournalError(
                [
                    f"{cur} looks like a child (pyproject.toml + configs/) "
                    f"but has no {MARKER} — run `python -m dskit.journal init`"
                ]
            )
        parent = os.path.dirname(cur)
        if parent == cur:
            return None
        cur = parent
    return None


def init_journal(child_root):
    """Create the marker, empty CSVs, research dir, and generated README.

    Parameters
    ----------
    child_root : str
        The child directory (holds ``pyproject.toml``).

    Returns
    -------
    JournalRoot

    Raises
    ------
    JournalError
        Already initialized.
    """
    from .render import render
    from .store import write_empty_csv

    child_root = os.path.abspath(child_root)
    marker = os.path.join(child_root, MARKER)
    if os.path.isfile(marker):
        raise JournalError([f"already initialized: {marker}"])
    cfg = JournalConfig(notes=_MARKER_NOTES)
    os.makedirs(os.path.join(child_root, cfg.decisioning_dir), exist_ok=True)
    os.makedirs(os.path.join(child_root, "docs", "research"), exist_ok=True)
    payload = {
        "decisioning_dir": cfg.decisioning_dir,
        "notes": cfg.notes,
    }
    atomic_write_text(
        marker, json.dumps(payload, indent=2, sort_keys=True) + "\n"
    )
    root = load_root(child_root)
    write_empty_csv(root.actions_csv, ACTION_FIELDS)
    write_empty_csv(root.path_csv, PATH_FIELDS)
    gitkeep = os.path.join(root.research_dir, ".gitkeep")
    if not os.path.exists(gitkeep):
        atomic_write_text(gitkeep, "")
    render(root)
    return root
