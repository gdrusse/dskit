import pytest

"""sys.path bootstrap — the child's tests run UNINSTALLED (ADR-0021).

The child root (the directory holding ``intraday_equities/`` and ``configs/``)
is derived from this file's own location, never from a cwd or a repo
path, so the same tests run in-repo, after graduation, and from any
invocation directory. ``dskit`` itself comes from the environment (it is
the child's declared dependency), never from a path.
"""

import os
import sys

CHILD_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

if CHILD_ROOT not in sys.path:
    sys.path.insert(0, CHILD_ROOT)


@pytest.fixture(autouse=True)
def _no_operator_fold_width(monkeypatch):
    """Keep the suite off the operator's shell.

    ``_run_bounded_walk`` reads INTRADAY_EQUITIES_FOLD_WORKERS when no
    width is passed, and the README tells operators to export it. Without
    this the suite silently goes concurrent on whoever's machine it runs.
    """
    monkeypatch.delenv("INTRADAY_EQUITIES_FOLD_WORKERS", raising=False)
