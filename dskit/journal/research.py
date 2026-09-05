"""Write a research markdown file and record it.

Research agents land findings in a topic folder under ``docs/research/``:

``docs/research/<topic>/<YYYY-MM-DD>-<name>.md``

The synthesis for a task is ``<date>-synthesis.md``. Subagent notes use
the same folder and date with a different ``name``. Re-running a topic
adds new dated files; it never overwrites. This module is the only
writer — a free-hand file with no row is a miss. Markdown never sits in
the ``docs/research/`` root.
"""

from __future__ import annotations

import os
import re

from .base import JournalError, atomic_write_text, locked, utc_now
from .hooks import record_research
from .locate import find_journal

__all__ = ["slugify", "write_research"]

_NON_SLUG = re.compile(r"[^a-z0-9]+")
_DEFAULT_NAME = "synthesis"


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


def _dated_stem(stamp, name):
    """``YYYY-MM-DD-<name>``, or a UTC-time variant on collision."""
    day = stamp[:10]
    return f"{day}-{name}"


def _collision_stem(stamp, name):
    """Same-day second write: ``YYYY-MM-DDThhmmssZ-<name>``."""
    day = stamp[:10]
    clock = stamp[11:19].replace(":", "")
    return f"{day}T{clock}Z-{name}"


def write_research(title, body=None, start=None, topic=None, name=None):
    """Create a dated file under ``docs/research/<topic>/`` and append a row.

    Parameters
    ----------
    title : str
        Short title; becomes the row ``inputs`` and the default topic slug.
    body : str, optional
        Markdown body; a stub is written when omitted.
    start : str, optional
        Locate start; default cwd.
    topic : str, optional
        Topic folder slug. Default: slug of ``title``. Re-use the same
        topic to add later notes beside an earlier synthesis.
    name : str, optional
        File stem after the date. Default ``synthesis``. Subagent notes
        pass a distinct name (``shared-heads``, ``uncertainty-sets``).

    Returns
    -------
    str
        Absolute path of the new file.

    Raises
    ------
    JournalError
        No journal, empty slugs, or the resolved path already exists
        after the same-day collision rename.
    """
    root = find_journal(start=start)
    if root is None:
        raise JournalError(
            ["no journal here — run `python -m dskit.journal init` in the child"]
        )
    topic_slug = slugify(topic if topic else title)
    name_slug = slugify(name) if name else _DEFAULT_NAME
    stamp = utc_now()
    folder = os.path.join(root.research_dir, topic_slug)
    stem = _dated_stem(stamp, name_slug)
    path = os.path.join(folder, f"{stem}.md")
    text = body if body is not None else (
        f"# {title}\n\n"
        f"Date: {stamp}\n\n"
        "## Question\n\n"
        "## Finding\n\n"
        "## Sources\n"
    )
    if not text.endswith("\n"):
        text += "\n"
    with locked(root.decisioning):
        os.makedirs(folder, exist_ok=True)
        if os.path.exists(path):
            stem = _collision_stem(stamp, name_slug)
            path = os.path.join(folder, f"{stem}.md")
        if os.path.exists(path):
            raise JournalError([f"research file already exists: {path}"])
        rel = os.path.relpath(path, root.child_root).replace(os.sep, "/")
        atomic_write_text(path, text)
        step = f"{topic_slug}/{stem}"[:80]
        record_research(
            step,
            inputs=title,
            outputs=rel,
            db_location=rel,
            start=root.child_root,
        )
    return path
