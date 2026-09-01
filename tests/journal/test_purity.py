"""``dskit.journal`` purity: stdlib + itself. Never pipeline/onboarding/assets."""

import ast
import pathlib
import subprocess
import sys

import dskit.journal

PACKAGE_DIR = pathlib.Path(dskit.journal.__file__).parent
PACKAGE = "dskit.journal"


def module_level_imports(path):
    """Absolute names of every module-level import in a file."""
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


def test_static_every_module_is_stdlib_or_self():
    problems = []
    for path in sorted(PACKAGE_DIR.glob("*.py")):
        for name in module_level_imports(path):
            root = name.split(".")[0]
            if name == PACKAGE or name.startswith(PACKAGE + ".") or root in sys.stdlib_module_names:
                continue
            problems.append(f"{path.name}: {name}")
    assert not problems, f"disallowed module-level imports: {problems}"


def test_never_the_siblings():
    banned = ("dskit.pipeline", "dskit.onboarding", "dskit.assets")
    problems = []
    for path in sorted(PACKAGE_DIR.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for name in names:
                if any(name == b or name.startswith(b + ".") for b in banned):
                    problems.append(f"{path.name}: {name}")
    assert not problems, f"journal must not import siblings: {problems}"


def test_no_subdirectories():
    subdirs = [
        p.name
        for p in PACKAGE_DIR.iterdir()
        if p.is_dir() and p.name not in ("__pycache__",)
    ]
    assert not subdirs, subdirs


def test_behavioural_fresh_import_is_pure():
    code = (
        "import sys\n"
        "baseline = set(sys.modules)\n"
        "import dskit.journal\n"
        "added = set(sys.modules) - baseline\n"
        "bad = [m for m in added if not m.startswith('dskit')\n"
        "       and m.split('.')[0] not in sys.stdlib_module_names]\n"
        "assert not bad, bad\n"
    )
    repo_root = str(PACKAGE_DIR.parents[1])
    proc = subprocess.run(
        [sys.executable, "-c", code],
        cwd=repo_root,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
