"""sys.path bootstrap — the child's tests run UNINSTALLED (ADR-0021).

The child root (the directory holding ``yourproject/`` and ``configs/``)
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
