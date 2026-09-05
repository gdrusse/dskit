"""The production package's own gate (plan §2, §8, D2).

`dskit.production` is an APPLICATION of the toolkit, not part of it, so
its import rule is deliberately not the tier-1 rule
`tests/pipeline/test_purity.py` enforces: it may reach `dskit.pipeline`,
`dskit.onboarding` and `dskit.assets`, and `dskit.journal` at function
depth only (ADR-0056). What it may never reach is a third-party library —
a serve process that cannot import on the box it is deployed to is a
serve process that cannot halt itself — and the arrow never reverses:
`dskit/pipeline` must never import `dskit.production`, or extracting the
toolkit stops being a file move.

Four more rules live here because they are structural, not stylistic:

* **No rung branch.** D2 rules that mode is validated object composition.
  `compose.py` is the one module whose job is to read the rung; anywhere
  else, an `if rung ==` is the branch the injected `Authority` seam exists
  to make unnecessary. The AST ban is a regression backstop, not the
  argument.
* **`__all__` is the API contract**, and no `_`-name leaks through it.
* **Vocabularies are closed, registries are open.** A `*_KINDS` name bound
  to a tuple outside `vocab.py` is a closed set that has left its home;
  bound to `Registry(...)` it is the open doorway §4.3 intends.
* **No venue names** — the same rule, and the same helper, the toolkit
  already uses.

The static halves iterate over whatever modules EXIST, so this file is
useful from the first commit of the package; one behavioural test asserts
the package is actually there and imports with every heavy library
blocked.
"""

import ast
import pathlib
import sys

import pytest

import dskit
from dskit.pipeline.conformance import DEFAULT_BLOCKED_IMPORTS, import_with_blocked

# The venue rule and its AST walkers have ONE owner (CLAUDE.md: a function
# is never repeated across modules) — the toolkit's own gate.
from tests.pipeline.test_purity import VENUE_NAMES, _imports, _venue_hits

PACKAGE = "dskit.production"
LIBS_PACKAGE = "dskit.production.libs"
PACKAGE_DIR = pathlib.Path(dskit.__file__).parent / "production"
LIBS_DIR = PACKAGE_DIR / "libs"

#: What §2 lets this package import at any depth.
ALLOWED_PREFIXES = (
    "dskit.pipeline",
    "dskit.onboarding",
    "dskit.assets",
    "dskit.production",
)

#: The one sibling reachable at FUNCTION depth only (ADR-0056).
JOURNAL = "dskit.journal"

#: The only subdirectory §8 sanctions under the package.
ALLOWED_SUBDIRS = {"libs"}

#: The three names D2 forbids a comparison against outside `compose.py`.
FORBIDDEN_COMPARANDS = ("kind", "mode", "rung")

#: The one module allowed to read the rung (D2, §5.13.1).
RUNG_READER = "compose.py"


def _core_files():
    return sorted(PACKAGE_DIR.glob("*.py")) if PACKAGE_DIR.is_dir() else []


def _pack_files():
    return sorted(LIBS_DIR.glob("*.py")) if LIBS_DIR.is_dir() else []


def _all_files():
    return _core_files() + _pack_files()


def _is_journal(module):
    return module == JOURNAL or module.startswith(JOURNAL + ".")


def _is_allowed_dskit(module):
    return any(
        module == prefix or module.startswith(prefix + ".")
        for prefix in ALLOWED_PREFIXES
    )


def _is_stdlib(module):
    return module.split(".")[0] in sys.stdlib_module_names


def _tree(path):
    return ast.parse(path.read_text(encoding="utf-8"))


def _module_all(path):
    """The `__all__` value of ``path`` as a tuple of strings, or None."""
    for node in ast.walk(_tree(path)):
        if isinstance(node, ast.Assign):
            targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            targets = [node.target.id]
        else:
            continue
        if "__all__" not in targets or node.value is None:
            continue
        if isinstance(node.value, (ast.List, ast.Tuple)):
            return tuple(
                elt.value
                for elt in node.value.elts
                if isinstance(elt, ast.Constant) and isinstance(elt.value, str)
            )
    return None


# ---------------------------------------------------------------------------
# The import rule
# ---------------------------------------------------------------------------


def test_the_package_imports_only_the_toolkit_and_stdlib_at_any_depth():
    """§2: stdlib + `dskit.pipeline` + `dskit.onboarding` + `dskit.assets`
    + itself. A third-party import at ANY depth — inside `run`, behind a
    `try`, in a class body — is a serve process that can fail to start on
    the host it is meant to guard."""
    offenders = []
    for path in _core_files():
        for module, top in _imports(path, PACKAGE):
            if _is_journal(module):
                if top:
                    offenders.append(f"{path.name}: {module} (function depth only)")
                continue
            if _is_allowed_dskit(module) or _is_stdlib(module):
                continue
            offenders.append(f"{path.name}: {module}")
    assert not offenders, f"dskit/production reached outside its rule: {offenders}"


def test_the_journal_is_reachable_only_from_inside_a_function():
    """ADR-0056: a module-level `dskit.journal` would make the action
    ledger a load-time dependency of the serve loop."""
    offenders = [
        f"{path.name}: {module}"
        for path in _all_files()
        for module, top in _imports(path, PACKAGE)
        if top and _is_journal(module)
    ]
    assert not offenders, offenders


def test_library_packs_name_their_library_only_inside_a_method():
    """Tier-2 discipline, unchanged: at MODULE level a pack obeys the same
    rule as the core, so importing the pack cannot import its library."""
    offenders = []
    for path in _pack_files():
        for module, top in _imports(path, LIBS_PACKAGE):
            if not top:
                continue
            if _is_allowed_dskit(module) or _is_stdlib(module):
                continue
            offenders.append(f"libs/{path.name}: {module}")
    assert not offenders, offenders


def test_the_pipeline_never_imports_the_production_package():
    """The arrow points one way: an application imports its toolkit."""
    offenders = []
    for path in sorted((pathlib.Path(dskit.__file__).parent / "pipeline").rglob("*.py")):
        for module, _top in _imports(path, "dskit.pipeline"):
            if module == PACKAGE or module.startswith(PACKAGE + "."):
                offenders.append(f"{path.relative_to(pathlib.Path(dskit.__file__).parent)}: {module}")
    assert not offenders, (
        f"dskit/pipeline imported dskit.production: {offenders} — the toolkit "
        "must stay extractable as a file move"
    )


def test_only_the_sanctioned_subdirectory_exists():
    if not PACKAGE_DIR.is_dir():
        pytest.skip("dskit/production not written yet")
    subdirs = {
        p.name for p in PACKAGE_DIR.iterdir() if p.is_dir() and p.name != "__pycache__"
    }
    assert subdirs <= ALLOWED_SUBDIRS, sorted(subdirs - ALLOWED_SUBDIRS)


# ---------------------------------------------------------------------------
# D2 — no branch on mode, kind or rung
# ---------------------------------------------------------------------------


def _comparand_names(node):
    """Every `kind`/`mode`/`rung` spelling compared in ``node``."""
    operands = [node.left, *node.comparators]
    found = []
    for operand in operands:
        if isinstance(operand, ast.Name) and operand.id in FORBIDDEN_COMPARANDS:
            found.append(operand.id)
        elif isinstance(operand, ast.Attribute) and operand.attr in FORBIDDEN_COMPARANDS:
            found.append(operand.attr)
    return found


def _branch_hits(path):
    return [
        f"{path.name}:{node.lineno}: compares {name}"
        for node in ast.walk(_tree(path))
        if isinstance(node, ast.Compare)
        for name in _comparand_names(node)
    ]


def test_no_module_but_compose_branches_on_a_kind_mode_or_rung():
    """D2: shadow / paper / live differ by which objects were injected, so
    the loop has nothing to ask. `compose.py` is the composition root and
    the one module whose job is to read the rung; a branch anywhere else
    is the code path D2 removed growing back. `in` counts: a membership
    test against a rung is the same branch with a different spelling."""
    offenders = [
        hit
        for path in _all_files()
        if path.name != RUNG_READER
        for hit in _branch_hits(path)
    ]
    assert not offenders, (
        f"branch on kind/mode/rung outside {RUNG_READER}: {offenders} — "
        "subclass a hook, add a registry entry, or pass a strategy object"
    )


def test_the_branch_detector_sees_every_spelling(tmp_path):
    """The gate is only worth what its detector catches: equality, `in`,
    an attribute read and a reversed comparison must all hit, while an
    unrelated comparison must not."""
    path = tmp_path / "probe.py"
    path.write_text(
        "def f(mode, rung, spec, other):\n"
        "    a = mode == 'live'\n"
        "    b = 'shadow' == spec.rung\n"
        "    c = rung in ('live', 'live_limited')\n"
        "    d = spec.kind != 'paper'\n"
        "    e = other == 'live'\n"
        "    return a, b, c, d, e\n",
        encoding="utf-8",
    )
    hits = _branch_hits(path)
    assert len(hits) == 4, hits
    assert all("probe.py:" in hit for hit in hits)


# ---------------------------------------------------------------------------
# Closed vocabularies vs open registries
# ---------------------------------------------------------------------------


def _is_registry_call(value):
    if not isinstance(value, ast.Call):
        return False
    func = value.func
    return (isinstance(func, ast.Name) and func.id == "Registry") or (
        isinstance(func, ast.Attribute) and func.attr == "Registry"
    )


def _kinds_offenders(path):
    out = []
    for node in ast.walk(_tree(path)):
        if isinstance(node, ast.Assign):
            names = [t.id for t in node.targets if isinstance(t, ast.Name)]
            value = node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names, value = [node.target.id], node.value
        else:
            continue
        for name in names:
            if name.endswith("_KINDS") and not _is_registry_call(value):
                out.append(f"{path.name}:{node.lineno}: {name}")
    return out


def test_a_kinds_name_outside_vocab_is_a_registry_not_a_tuple():
    """§5.0: closed vocabularies live ONLY in `vocab.py`; `<FAMILY>_KINDS`
    elsewhere is the §4.3 registry, which is open by design. A tuple bound
    to that name is a closed set that escaped its module."""
    offenders = [
        hit
        for path in _all_files()
        if path.name != "vocab.py"
        for hit in _kinds_offenders(path)
    ]
    assert not offenders, offenders


def test_the_kinds_detector_tells_a_registry_from_a_tuple(tmp_path):
    path = tmp_path / "probe_kinds.py"
    path.write_text(
        "CLOCK_KINDS = Registry('clock', Clock)\n"
        "FEED_KINDS = base.Registry('feed', Feed)\n"
        "BREAK_KINDS = ('timing', 'price')\n",
        encoding="utf-8",
    )
    assert _kinds_offenders(path) == ["probe_kinds.py:3: BREAK_KINDS"]


# ---------------------------------------------------------------------------
# The API contract
# ---------------------------------------------------------------------------


def test_every_module_declares_all():
    missing = [path.name for path in _all_files() if _module_all(path) is None]
    assert not missing, f"modules without __all__: {missing}"


def test_no_underscore_name_is_exported():
    """`__all__` plus the `_` prefix IS the API contract (CLAUDE.md); a
    child that reaches a private production name is refused by this rule
    having been kept true."""
    offenders = []
    for path in _all_files():
        for name in _module_all(path) or ():
            if name.startswith("_"):
                offenders.append(f"{path.name}: {name}")
    assert not offenders, offenders


# ---------------------------------------------------------------------------
# Venue neutrality
# ---------------------------------------------------------------------------


def test_no_venue_names_in_the_packages_executable_code():
    """The venue executor subclass is child (tier-3) code; the package
    never names one outside explanatory prose."""
    offenders = [hit for path in _all_files() for hit in _venue_hits(path)]
    assert not offenders, f"venue names ({VENUE_NAMES}) in code: {offenders}"


# ---------------------------------------------------------------------------
# The behavioural half
# ---------------------------------------------------------------------------


def test_the_package_exists_and_imports_with_heavy_libraries_blocked():
    """Static analysis cannot see an import hidden behind
    `importlib.import_module` or a re-export; a fresh interpreter with
    every heavy library blocked can."""
    init = PACKAGE_DIR / "__init__.py"
    assert init.is_file(), f"{init} is missing — the package must exist"
    ok, detail = import_with_blocked(PACKAGE, DEFAULT_BLOCKED_IMPORTS)
    assert ok, f"{PACKAGE} needs a heavy library to import:\n{detail}"


def test_every_module_imports_with_heavy_libraries_blocked():
    failures = []
    for path in _core_files():
        if path.name == "__init__.py":
            continue
        module = f"{PACKAGE}.{path.stem}"
        ok, detail = import_with_blocked(module, DEFAULT_BLOCKED_IMPORTS)
        if not ok:
            failures.append(f"{module}:\n{detail}")
    assert not failures, "\n\n".join(failures)


def test_every_library_pack_imports_with_its_library_blocked():
    failures = []
    for path in _pack_files():
        if path.name == "__init__.py":
            continue
        module = f"{LIBS_PACKAGE}.{path.stem}"
        ok, detail = import_with_blocked(module, DEFAULT_BLOCKED_IMPORTS)
        if not ok:
            failures.append(f"{module}:\n{detail}")
    assert not failures, "\n\n".join(failures)
