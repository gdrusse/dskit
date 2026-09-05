"""``children/_skeleton`` — the pinned canonical child shape (ADR-0021).

Three guarantees: the skeleton's file list cannot drift silently, the
skeleton stays a RUNNABLE child (its own suite green, by subprocess,
exactly as ``test_children.py`` runs real children), and the isolation
holds — dskit never imports ``children`` and never ships it.
"""

import os
import re
import tomllib

from .test_children import REPO_ROOT, run_child_suite

SKELETON = os.path.join(REPO_ROOT, "children", "_skeleton")

#: The skeleton's EXACT file list. Changing the skeleton's shape means
#: updating this pin in the same commit, DELIBERATELY (ADR-0021) — the
#: pin is what makes a stray file, a lost test, or an unreviewed addition
#: to the template a loud failure instead of something copies inherit.
EXPECTED_FILES = {
    "AGENTS.md",
    "CLAUDE.md",
    "README.md",
    "journal.json",
    "docs/decisioning/README.md",
    "docs/decisioning/actions.csv",
    "docs/decisioning/path.csv",
    "docs/explanations/README.md",
    "docs/memos/.gitkeep",
    "docs/memos/README.md",
    "docs/research/.gitkeep",
    "docs/research/README.md",
    "configs/asset-model.json",
    "configs/run-sample.json",
    "configs/source-sample.json",
    "configs/suite-sample.json",
    "pyproject.toml",
    "tests/conftest.py",
    "tests/test_configs.py",
    "tests/test_connectors.py",
    "tests/test_nodes.py",
    "yourproject/__init__.py",
    "yourproject/connectors.py",
    "yourproject/nodes.py",
}


def test_the_skeletons_file_list_is_pinned():
    found = set()
    for dirpath, dirnames, filenames in os.walk(SKELETON):
        # Runtime debris is not shape: bytecode caches and dot-dirs
        # (.pytest_cache and kin) are invisible to the pin.
        dirnames[:] = [
            d for d in dirnames if d != "__pycache__" and not d.startswith(".")
        ]
        for name in filenames:
            if name.endswith(".pyc"):
                continue
            rel = os.path.relpath(os.path.join(dirpath, name), SKELETON)
            found.add(rel.replace(os.sep, "/"))
    assert found == EXPECTED_FILES, (
        "the skeleton's shape drifted — "
        f"added {sorted(found - EXPECTED_FILES)}, "
        f"removed {sorted(EXPECTED_FILES - found)}. If the change is "
        "deliberate, update EXPECTED_FILES in this same commit."
    )


def test_the_skeletons_journal_readme_matches_the_renderer():
    """The committed README is the projection of the empty CSVs."""
    from dskit.journal.locate import load_root
    from dskit.journal.render import render_text
    from dskit.journal.store import read_actions, read_path

    root = load_root(SKELETON)
    expected = render_text(read_actions(root), read_path(root))
    if not expected.endswith("\n"):
        expected += "\n"
    with open(root.readme, encoding="utf-8") as fh:
        assert fh.read() == expected


def test_the_skeletons_own_suite_is_green():
    """The template is not prose: it must RUN, against today's engines,
    the same subprocess way every real child does."""
    run_child_suite(SKELETON)


def test_no_dskit_module_imports_children():
    """The isolation half of ADR-0021: the toolkit never reaches into its
    incubator. Import lines only — prose may (and does) MENTION the
    directory; an ``import``/``from`` line may not."""
    offenders = []
    pkg = os.path.join(REPO_ROOT, "dskit")
    for dirpath, dirnames, filenames in os.walk(pkg):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        for name in filenames:
            if not name.endswith(".py"):
                continue
            path = os.path.join(dirpath, name)
            with open(path, encoding="utf-8") as fh:
                for lineno, line in enumerate(fh, start=1):
                    stripped = line.lstrip()
                    if not stripped.startswith(("import ", "from ")):
                        continue
                    if re.search(r"\bchildren\b", stripped):
                        offenders.append(
                            f"{os.path.relpath(path, REPO_ROOT)}:{lineno}: "
                            f"{stripped.rstrip()}"
                        )
    assert not offenders, (
        "dskit modules import from children/ — the toolkit never imports "
        f"a child (ADR-0021): {offenders}"
    )


def test_the_wheel_still_ships_only_dskit():
    """The packaging half: ``children/`` is incubation, not distribution."""
    with open(os.path.join(REPO_ROOT, "pyproject.toml"), "rb") as fh:
        pyproject = tomllib.load(fh)
    include = pyproject["tool"]["setuptools"]["packages"]["find"]["include"]
    assert include == ["dskit*"], (
        f"packages.find.include drifted to {include!r} — wheels ship "
        "dskit* and nothing else (ADR-0021)"
    )
