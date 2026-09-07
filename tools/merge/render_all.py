"""Post-merge re-render of every child's generated decisioning README.

Installed as the ``post-merge`` hook by ``install.sh`` (ADR-0107): the
ledger union driver merged the CSVs, the render driver took one side of
the generated README, and this loop regenerates every child's README
from the merged CSVs so the projection never stays stale. Failures are
reported and never fail the merge — the next journal write re-renders.
"""

from __future__ import annotations

import os
import subprocess
import sys


def _repo_root(override=None):
    """Return the repository root (three levels above this file)."""
    if override:
        return override
    return os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    )


def _render_child(repo, root):
    """Run ``dskit.journal render`` for one child root.

    Returns
    -------
    tuple of (str, int)
        The child's name and the render exit code.
    """
    done = subprocess.run(
        [sys.executable, "-m", "dskit.journal", "render", "--root", root],
        cwd=repo,
        capture_output=True,
        text=True,
    )
    if done.returncode != 0 and done.stderr.strip():
        print(done.stderr.strip(), file=sys.stderr)
    return os.path.basename(root), done.returncode


def main(argv=None):
    """Render every child carrying a journal marker; never fail a merge.

    Parameters
    ----------
    argv : list of str or None
        Optional ``[script, <repo-root override>]`` — the override exists
        so the test suite can point the loop at a scratch tree.

    Returns
    -------
    int
        Always ``0`` — a render failure is reported, never a failed merge.
    """
    repo = _repo_root(argv[1] if len(argv or []) > 1 else None)
    children = os.path.join(repo, "children")
    if not os.path.isdir(children):
        return 0
    for name in sorted(os.listdir(children)):
        root = os.path.join(children, name)
        if not os.path.isfile(os.path.join(root, "journal.json")):
            continue
        child, code = _render_child(repo, root)
        print(f"render-journal: {child}: {'rendered' if code == 0 else f'exit {code}'}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
