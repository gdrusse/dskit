"""The package's purity gate (ADR-0013), mirroring assets' and the pipeline's.

``dskit.onboarding`` imports stdlib + ``dskit.assets`` + itself — and
NEVER ``dskit.pipeline``: the engine imports neither sibling and neither
sibling imports the engine (the file seams of ADR-0008/0012 are the only
crossings). Enforced statically (every module-level import, libs/
included) and behaviourally (a fresh interpreter's differential module
set).
"""

import ast
import pathlib
import subprocess
import sys

import dskit.onboarding

PACKAGE_DIR = pathlib.Path(dskit.onboarding.__file__).parent
PACKAGE = "dskit.onboarding"
#: The one sanctioned dskit dependency (ADR-0013).
ALLOWED_DSKIT = ("dskit.assets", "dskit.onboarding")


def module_level_imports(path):
    """Absolute names of every module-level import in a file — including
    try/if/with and class bodies; only function bodies are exempt."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found = []

    class Visitor(ast.NodeVisitor):
        def visit_FunctionDef(self, node):
            return

        visit_AsyncFunctionDef = visit_FunctionDef

        def visit_Import(self, node):
            found.extend(alias.name for alias in node.names)

        def visit_ImportFrom(self, node):
            found.append(PACKAGE if node.level else (node.module or ""))

    Visitor().visit(tree)
    return found


def test_static_every_module_is_stdlib_assets_or_self():
    problems = []
    for path in sorted(PACKAGE_DIR.glob("**/*.py")):
        for name in module_level_imports(path):
            root = name.split(".")[0]
            if name.startswith(ALLOWED_DSKIT) or root in sys.stdlib_module_names:
                continue
            problems.append(f"{path.relative_to(PACKAGE_DIR)}: {name}")
    assert not problems, f"disallowed module-level imports: {problems}"


def test_static_never_the_pipeline():
    # The engine/sibling firewall is worth its own loud assertion.
    problems = []
    for path in sorted(PACKAGE_DIR.glob("**/*.py")):
        for name in module_level_imports(path):
            if name.startswith("dskit.pipeline"):
                problems.append(f"{path.relative_to(PACKAGE_DIR)}: {name}")
    assert not problems, f"onboarding must never import the pipeline: {problems}"


def test_no_surprise_subdirectories():
    subdirs = [p.name for p in PACKAGE_DIR.iterdir()
               if p.is_dir() and p.name not in ("__pycache__", "libs")]
    assert not subdirs, subdirs


def test_behavioural_fresh_import_is_pure():
    code = (
        "import sys\n"
        "baseline = set(sys.modules)\n"
        "import dskit.onboarding\n"
        "added = set(sys.modules) - baseline\n"
        "bad = [m for m in added if not m.startswith('dskit')\n"
        "       and m.split('.')[0] not in sys.stdlib_module_names]\n"
        "bad += [m for m in added if m.startswith('dskit.pipeline')]\n"
        "assert not bad, bad\n"
    )
    repo_root = str(PACKAGE_DIR.parents[1])
    proc = subprocess.run([sys.executable, "-c", code], cwd=repo_root,
                          capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
