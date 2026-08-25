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


def module_level_imports(path, package=None):
    """Absolute names of every module-level import in a file — including
    ones nested in try/if/with or class bodies, exactly like the
    pipeline's gate (a hole there was a hole here). ``package`` is the
    dotted package holding the module — derived from the file's
    location when omitted; the scanner's own self-test feeds synthetic
    files with it explicit."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found = []
    # The package of the module holding the import: relative levels
    # resolve against it, mirroring Python's own rules — level=1 is the
    # containing package, each extra level strips one component. Naming
    # every relative import PACKAGE regardless of level let a
    # `from ...pipeline import x` in libs/ slip the scan (ADR-0020).
    if package is None:
        package_parts = list(path.relative_to(PACKAGE_DIR.parents[1]).parts[:-1])
    else:
        package_parts = package.split(".")

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
            if node.level:
                keep = max(0, len(package_parts) - (node.level - 1))
                base = package_parts[:keep]
                found.append(".".join(base + ([node.module] if node.module else [])))
            else:
                found.append(node.module or "")

    Visitor().visit(tree)
    return found


def test_static_every_module_is_stdlib_or_self():
    problems = []
    # rglob, not glob: libs/ is the sanctioned tier-2 subtree (ADR-0018)
    # and answers to the same gate — module level is stdlib + self; a
    # pack's backend library is imported inside methods, even a stdlib
    # one like sqlite3 (the pack is the template for drivers that must
    # stay lazy). Recursion means a surprise nested directory cannot
    # smuggle an import past the scan either.
    for path in sorted(PACKAGE_DIR.rglob("*.py")):
        for name in module_level_imports(path):
            root = name.split(".")[0]
            # Prefix check with the dot: "dskit.assets_evil" must not
            # ride on startswith("dskit.assets").
            if (name == PACKAGE or name.startswith(PACKAGE + ".")
                    or root in sys.stdlib_module_names):
                continue
            problems.append(f"{path.name}: {name}")
    assert not problems, f"non-stdlib module-level imports: {problems}"


def test_relative_import_levels_resolve_absolutely(tmp_path):
    # The scanner's own self-test (mirrors the pipeline gate's): each
    # relative level strips one component, so a deep escape like
    # `from ...pipeline import x` in libs/ resolves to dskit.pipeline
    # and gets flagged — not silently mapped to the package itself.
    f = tmp_path / "mod.py"
    f.write_text(
        "from ..base import x\n"
        "from ...pipeline import y\n"
        "from . import z\n"
    )
    names = module_level_imports(f, package="dskit.assets.libs")
    assert "dskit.assets.base" in names
    assert "dskit.pipeline" in names
    assert "dskit.assets.libs" in names


def test_no_subdirectories():
    # Tier-2 store packs (libs/) are sanctioned FUTURE structure; today
    # the package is flat, and any surprise subdirectory is a smell.
    subdirs = [p.name for p in PACKAGE_DIR.iterdir()
               if p.is_dir() and p.name != "__pycache__" and p.name != "libs"]
    assert not subdirs, subdirs
    # And libs/ itself stays flat: one module per backend, no nesting.
    libs_subdirs = [p.name for p in (PACKAGE_DIR / "libs").iterdir()
                    if p.is_dir() and p.name != "__pycache__"]
    assert not libs_subdirs, libs_subdirs


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
