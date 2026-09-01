"""Write a research markdown file and record it.

Research agents land findings in ``docs/research/<slug>.md``. This
module is the only writer — a free-hand file with no row is a miss.
"""

from __future__ import annotations

import os
import re

from .base import JournalError, atomic_write_text, utc_now
from .hooks import record_research
from .locate import find_journal

__all__ = ["slugify", "write_research"]

_NON_SLUG = re.compile(r"[^a-z0-9]+")


def slugify(title):
    """Turn a title into a filesystem slug.

    Parameters
    ----------
    title : str

    Returns
    -------
    str

    Raises
    ------
    JournalError
        Empty after slugging.
    """
    slug = _NON_SLUG.sub("-", title.lower()).strip("-")
    if not slug:
        raise JournalError([f"research title {title!r} slugs to empty"])
    return slug


def write_research(title, body=None, start=None):
    """Create ``docs/research/<slug>.md`` and append a research row.

    Parameters
    ----------
    title : str
        Short title; becomes the slug and the ``step``.
    body : str, optional
        Markdown body; a stub is written when omitted.
    start : str, optional
        Locate start; default cwd.

    Returns
    -------
    str
        Absolute path of the new file.

    Raises
    ------
    JournalError
        No journal, or the file already exists.
    """
    root = find_journal(start=start)
    if root is None:
        raise JournalError(
            ["no journal here — run `python -m dskit.journal init` in the child"]
        )
    os.makedirs(root.research_dir, exist_ok=True)
    slug = slugify(title)
    path = os.path.join(root.research_dir, f"{slug}.md")
    if os.path.exists(path):
        raise JournalError([f"research file already exists: {path}"])
    stamp = utc_now()
    text = body if body is not None else (
        f"# {title}\n\n"
        f"Date: {stamp}\n\n"
        "## Question\n\n"
        "## Finding\n\n"
        "## Sources\n"
    )
    if not text.endswith("\n"):
        text += "\n"
    atomic_write_text(path, text)
    rel = os.path.relpath(path, root.child_root).replace(os.sep, "/")
    record_research(slug[:80], inputs=title, outputs=rel, db_location=rel)
    return path
