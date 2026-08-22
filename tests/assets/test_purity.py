"""The package's own purity gate (ADR-0010), mirroring the pipeline's.

``dskit.assets`` imports nothing outside stdlib + itself — extracting it
must stay a file move. Enforced twice, like the pipeline's gate:
statically (every module-level import in every file, relative imports
resolved) and behaviourally (a fresh interpreter imports the package and
the differential module set must be stdlib/dskit only).
"""

import ast
import pathlib
import subprocess
import sys

import dskit.assets

PACKAGE_DIR = pathlib.Path(dskit.assets.__file__).parent
PACKAGE = "dskit.assets"


def module_level_imports(path):
    """Absolute names of every module-level import in a file — including
    ones nested in try/if/with or class bodies, exactly like the
    pipeline's gate (a hole there was a hole here)."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found = []

    class Visitor(ast.NodeVisitor):
        # Function bodies are the sanctioned heavy-import location — do
        # not descend. Everything else (try/if/with, class bodies) is
        # module level and gets collected.
        def visit_FunctionDef(self, node):
            return

        visit_AsyncFunctionDef = visit_FunctionDef

        def visit_Import(self, node):
            found.extend(alias.name for alias in node.names)

        def visit_ImportFrom(self, node):
            found.append(PACKAGE if node.level else (node.module or ""))

    Visitor().visit(tree)
    return found


def test_static_every_module_is_stdlib_or_self():
    problems = []
    for path in sorted(PACKAGE_DIR.glob("*.py")):
        for name in module_level_imports(path):
            root = name.split(".")[0]
            if name.startswith(PACKAGE) or root in sys.stdlib_module_names:
                continue
            problems.append(f"{path.name}: {name}")
    assert not problems, f"non-stdlib module-level imports: {problems}"


def test_no_subdirectories():
    # Tier-2 store packs (libs/) are sanctioned FUTURE structure; today
    # the package is flat, and any surprise subdirectory is a smell.
    subdirs = [p.name for p in PACKAGE_DIR.iterdir()
               if p.is_dir() and p.name != "__pycache__" and p.name != "libs"]
    assert not subdirs, subdirs


def test_behavioural_fresh_import_is_pure():
    code = (
        "import sys\n"
        "baseline = set(sys.modules)\n"
        "import dskit.assets\n"
        "added = set(sys.modules) - baseline\n"
        "bad = [m for m in added if not m.startswith('dskit')\n"
        "       and m.split('.')[0] not in sys.stdlib_module_names]\n"
        "assert not bad, bad\n"
    )
    repo_root = str(PACKAGE_DIR.parents[1])
    proc = subprocess.run([sys.executable, "-c", code], cwd=repo_root,
                          capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
