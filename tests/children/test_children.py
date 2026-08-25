"""Every incubating child runs its OWN pytest suite — by subprocess, so
a child is exercised exactly as it will live: its own rootward conftest,
its own sys.path bootstrap, nothing borrowed from this suite's process.
A red child fails the dskit suite (ADR-0021).

``_``/``.``-prefixed entries are skipped (the store's foreign-entry
idiom) — which is why ``_skeleton`` is not enumerated here: the pinned
template has its own dedicated coverage in ``test_skeleton.py``.
"""

import os
import subprocess
import sys

import pytest

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
CHILDREN = os.path.join(REPO_ROOT, "children")


def children_dirs():
    """Every enumerable child directory, sorted for a stable order."""
    if not os.path.isdir(CHILDREN):
        return []
    return sorted(
        os.path.join(CHILDREN, entry)
        for entry in os.listdir(CHILDREN)
        if os.path.isdir(os.path.join(CHILDREN, entry))
        and not entry.startswith(("_", "."))
    )


def run_child_suite(child_dir):
    """One child's own pytest from its own directory — the graduation
    posture: nothing on hand but the child and the installed dskit.
    Asserts green, printing the captured output on failure."""
    # timeout: a hung child (network wait, deadlock) must FAIL the dskit
    # suite loudly (TimeoutExpired), never hang it.
    done = subprocess.run(
        [sys.executable, "-m", "pytest", str(child_dir), "-q"],
        capture_output=True,
        text=True,
        cwd=child_dir,
        timeout=600,
    )
    assert done.returncode == 0, (
        f"{os.path.basename(child_dir)}: child suite failed "
        f"(exit {done.returncode})\n"
        f"--- stdout ---\n{done.stdout}\n--- stderr ---\n{done.stderr}"
    )


def test_every_child_ships_tests_and_runs_green():
    children = children_dirs()
    if not children:
        pytest.skip("no incubating children under children/*/")
    for child in children:
        # A child WITHOUT tests is a failure, never a quiet skip — the
        # convention's whole promise is that incubation keeps a child
        # continuously verified against engine evolution.
        assert os.path.isdir(os.path.join(child, "tests")), (
            f"{os.path.basename(child)}: no tests/ directory — a child "
            "must ship tests (ADR-0021)"
        )
        run_child_suite(child)
