"""The evaluation package's own gate (ADR-0183).

`dskit.evaluation` is tier 1: stdlib plus `dskit.pipeline` and
`dskit.production` (and itself), at ANY depth; tier-2 packs would live only
under `dskit/evaluation/libs/`, which does not exist yet. The arrow never
reverses: neither `dskit.pipeline` nor `dskit.production` may import it.
The package registers nothing at import, declares `__all__` in every module,
exports no `_` name and reaches no other package's private name — the
AST walkers are the toolkit gate's own, imported rather than restated.
"""

import ast
import os
import pathlib
import subprocess
import sys

import dskit
from dskit.pipeline.conformance import DEFAULT_BLOCKED_IMPORTS, import_with_blocked
from tests.pipeline.test_purity import (
    VENUE_NAMES,
    _imports,
    _venue_hits,
    private_cross_package_uses,
)

PACKAGE = "dskit.evaluation"
DIST_DIR = pathlib.Path(dskit.__file__).parent
PACKAGE_DIR = DIST_DIR / "evaluation"
ALLOWED_PREFIXES = ("dskit.pipeline", "dskit.production", PACKAGE)
#: The packages that must never import this one.
UPSTREAM = ("pipeline", "production")


def _files():
    return sorted(PACKAGE_DIR.glob("*.py"))


def _is_allowed(module):
    if module.split(".")[0] in sys.stdlib_module_names:
        return True
    return any(module == p or module.startswith(p + ".") for p in ALLOWED_PREFIXES)


def _module_all(path):
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "__all__" for t in node.targets
        ):
            return tuple(elt.value for elt in node.value.elts)
    return None


def test_the_package_imports_only_stdlib_pipeline_and_production_at_any_depth():
    offenders = [f"{path.name}: {module}" for path in _files()
                 for module, _top in _imports(path, PACKAGE) if not _is_allowed(module)]
    assert not offenders, offenders


def test_pipeline_and_production_never_import_evaluation():
    offenders = []
    for name in UPSTREAM:
        package = f"dskit.{name}"
        for path in sorted((DIST_DIR / name).rglob("*.py")):
            for module, _top in _imports(path, package):
                if module == PACKAGE or module.startswith(PACKAGE + "."):
                    offenders.append(f"{path.relative_to(DIST_DIR)}: {module}")
    assert not offenders, offenders


def test_only_sanctioned_subdirectories_exist():
    extra = {p.name for p in PACKAGE_DIR.iterdir() if p.is_dir()} - {"__pycache__", "libs"}
    assert not extra, extra


def test_every_module_declares_all_and_exports_no_private_name():
    missing = [p.name for p in _files() if _module_all(p) is None]
    assert not missing, missing
    leaked = [f"{p.name}: {n}" for p in _files() for n in _module_all(p) if n.startswith("_")]
    assert not leaked, leaked


def test_no_private_name_is_reached_in_another_package():
    offenders = [hit for path in _files() for hit in private_cross_package_uses(path, PACKAGE)]
    assert not offenders, offenders


def test_no_venue_names_in_code():
    offenders = [hit for path in _files() for hit in _venue_hits(path)]
    assert not offenders, f"venue names ({VENUE_NAMES}) in code: {offenders}"


def test_every_module_imports_with_heavy_libraries_blocked():
    failures = []
    for path in _files():
        module = PACKAGE if path.name == "__init__.py" else f"{PACKAGE}.{path.stem}"
        ok, detail = import_with_blocked(module, DEFAULT_BLOCKED_IMPORTS)
        if not ok:
            failures.append(f"{module}:\n{detail}")
    assert not failures, "\n\n".join(failures)


def test_importing_the_package_registers_nothing():
    probe = (
        "import dskit.pipeline\n"
        "from dskit.pipeline.node import DEFAULT_NODE_KINDS\n"
        "before = DEFAULT_NODE_KINDS.kinds()\n"
        "import dskit.evaluation, dskit.evaluation.nodes, dskit.evaluation.__main__\n"
        "assert DEFAULT_NODE_KINDS.kinds() == before, 'import registered a kind'\n"
    )
    env = {**os.environ, "PYTHONPATH": str(DIST_DIR.parent)}
    done = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True,
                          env=env)
    assert done.returncode == 0, done.stderr
