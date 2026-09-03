"""Shared errors, UTC stamps, the CSV header names, and the writer lock.

One name per header tuple — store, render, and the CLI all import these.
A second copy would be a scheduled column drift (ADR-0056).

Every ledger write is a read-allocate-append-rewrite cycle over a whole
CSV, so two agents writing at once would each read the same rows, pick
the same next id, and the second ``os.replace`` would drop the first
row. :func:`locked` closes that window: one exclusive advisory lock per
child, held across the whole cycle.
"""

from __future__ import annotations

import os
import tempfile
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone

try:  # POSIX only; the toolkit runs on Linux/WSL.
    import fcntl
except ImportError:  # pragma: no cover - non-POSIX has no concurrent agents
    fcntl = None

__all__ = [
    "ACTION_FIELDS",
    "CATEGORIES",
    "CRITERIA",
    "ID_PREFIX",
    "JournalError",
    "LOCK_NAME",
    "LOCK_TIMEOUT",
    "MARKER",
    "PATH_FIELDS",
    "atomic_write_text",
    "locked",
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

#: Sidecar lock file in the decisioning directory. A sidecar, not the
#: CSV itself: ``atomic_write_text`` swaps the CSV's inode, so a lock
#: taken on that inode would stop guarding the name.
LOCK_NAME = ".journal.lock"

#: Seconds a writer waits for the lock before refusing. Override with
#: ``DSKIT_JOURNAL_LOCK_TIMEOUT``. An agent should wait, not fail.
LOCK_TIMEOUT = 60.0

_POLL_MIN = 0.005
_POLL_MAX = 0.2

#: Re-entrancy depth per ``(lock path, thread)``. ``append_action``
#: takes the lock and calls into the store, which takes it again; a
#: second ``flock`` on a second descriptor would deadlock against
#: itself. Only the outermost holder opens and releases.
_depth = {}


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


def _timeout_seconds(timeout):
    """Resolve the wait budget: argument, then env, then the default."""
    if timeout is not None:
        return float(timeout)
    raw = os.environ.get("DSKIT_JOURNAL_LOCK_TIMEOUT")
    if not raw:
        return LOCK_TIMEOUT
    try:
        value = float(raw)
    except ValueError as exc:
        raise JournalError(
            [f"DSKIT_JOURNAL_LOCK_TIMEOUT={raw!r} is not a number"]
        ) from exc
    if value <= 0:
        raise JournalError([f"DSKIT_JOURNAL_LOCK_TIMEOUT={raw!r} must be > 0"])
    return value


def _flock_wait(fd, path, seconds):
    """Take an exclusive ``flock``, waiting up to ``seconds``."""
    if fcntl is None:  # pragma: no cover - non-POSIX
        return
    deadline = time.monotonic() + seconds
    delay = _POLL_MIN
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return
        except OSError:
            left = deadline - time.monotonic()
            if left <= 0:
                raise JournalError(
                    [
                        f"journal lock {path} still held after {seconds:g}s — "
                        "another writer has not finished"
                    ]
                ) from None
            time.sleep(min(delay, left))
            delay = min(delay * 2, _POLL_MAX)


@contextmanager
def locked(directory, timeout=None):
    """Hold this child's exclusive writer lock for the whole block.

    Wrap the entire read-allocate-append-rewrite cycle, never just the
    write: id allocation must happen inside, or two agents pick the
    same id and one row is lost.

    Parameters
    ----------
    directory : str
        The decisioning directory; :data:`LOCK_NAME` is created there.
    timeout : float, optional
        Seconds to wait. Default ``DSKIT_JOURNAL_LOCK_TIMEOUT`` or
        :data:`LOCK_TIMEOUT`.

    Yields
    ------
    str
        Absolute path of the lock file.

    Raises
    ------
    JournalError
        The directory is unwritable, or the wait ran out.

    Examples
    --------
    Serialize a whole cycle::

        with locked(root.decisioning):
            rows = read_actions(root)
            append_action_row(root, build(next_id(r.id for r in rows)))
    """
    path = os.path.join(os.path.abspath(directory), LOCK_NAME)
    key = (path, threading.get_ident())
    held = _depth.get(key, 0)
    if held:
        _depth[key] = held + 1
        try:
            yield path
        finally:
            _depth[key] -= 1
        return
    seconds = _timeout_seconds(timeout)
    try:
        fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o644)
    except OSError as exc:
        raise JournalError([f"cannot open journal lock {path}: {exc}"]) from exc
    try:
        _flock_wait(fd, path, seconds)
        _depth[key] = 1
        try:
            yield path
        finally:
            _depth.pop(key, None)
            if fcntl is not None:
                fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


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
