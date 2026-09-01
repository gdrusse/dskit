"""The toolkit purity rule, enforced (D-146).

Extracting `dskit/pipeline` to its own repo must stay a **file move**.
Three tiers, three rules:

* **Tier 1 — core** (``pipeline/*.py``): stdlib and ``dskit.pipeline``
  only. This is what keeps a document PLANNABLE on a machine with no
  heavy dependency installed, because planning imports the node classes.
  Enforced twice — statically (every module-level import, including ones
  wrapped in ``try``/``if``/``with`` or a class body, and every relative
  import resolved to its absolute name) and behaviourally (one fresh
  interpreter imports every core module with the heavy libraries blocked
  from ``sys.modules``).
* **Tier 2 — library packs** (``pipeline/libs/*.py``): may NAME their own
  library, but only inside ``run()`` — so at module level a pack obeys
  the SAME stdlib-or-toolkit rule as the core (a blocklist of known-heavy
  roots would miss e.g. joblib or yaml). Enforced twice as well:
  statically, and behaviourally per pack with its library blocked.
* No other subdirectory, and no venue name in the executable code of
  either tier.

This REPLACES the former "zero subdirectories under `pipeline/`" check.
That check was a proxy: its stated aim was to stop venue code hiding in a
subpackage, and it caught a subdirectory whether or not any venue code
was in it. What replaces it is strictly stronger — it enforces the
invariant the proxy stood for (importing a node must not import its heavy
library) on every file in both tiers, and still refuses any subdirectory
other than the one D-146 sanctions.

The helpers here were skeptic-hardened (FN-8/FN-9): the regression tests
at the bottom feed each once-invisible construct through the same helpers
the real-tree tests use, so the holes cannot silently reopen.
"""

import ast
import pathlib
import subprocess
import sys
import textwrap

import dskit.pipeline
from dskit.pipeline.conformance import DEFAULT_BLOCKED_IMPORTS, import_with_blocked

PACKAGE_DIR = pathlib.Path(dskit.pipeline.__file__).parent
LIBS_DIR = PACKAGE_DIR / "libs"

#: The toolkit's package names — what relative imports resolve against,
#: and the only non-stdlib prefix either tier may import at module level.
CORE_PACKAGE = "dskit.pipeline"
LIBS_PACKAGE = "dskit.pipeline.libs"

#: The owning distribution's top-level package. An import that reaches INTO
#: it but OUTSIDE the toolkit is the toolkit depending on its own
#: application, which makes extraction more than a file move.
ROOT_PACKAGE = CORE_PACKAGE.split(".")[0]

#: The ONLY subdirectory D-146 sanctions under the toolkit.
ALLOWED_SUBDIRS = {"libs"}

#: Domain/venue tags that must not appear in tier-1/2 executable code —
#: extend this list with your own tags once adapters exist. Prose may still
#: name one as an example ("group is an examplevenue event id"); that is
#: documentation, not coupling, and comments/docstrings are exempt.
VENUE_NAMES = ("examplevenue", "othervenue")


def _tier1_files():
    return sorted(PACKAGE_DIR.glob("*.py"))


def _tier2_files():
    return sorted(LIBS_DIR.glob("*.py")) if LIBS_DIR.is_dir() else []


def _module_level_import_ids(tree):
    """ids of Import/ImportFrom nodes that execute at import time.

    ``tree.body`` membership is NOT the test — an import wrapped in a
    module-level ``try:``/``if:``/``with:`` (or sitting in a class body)
    still executes when the module is imported. The only thing that
    defers an import is a function: an Import/ImportFrom is module-level
    iff its ancestor chain contains no FunctionDef/AsyncFunctionDef/Lambda.
    """
    parents = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[id(child)] = node
    out = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Import, ast.ImportFrom)):
            continue
        cur = parents.get(id(node))
        while cur is not None and not isinstance(
            cur, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)
        ):
            cur = parents.get(id(cur))
        if cur is None:
            out.add(id(node))
    return out


def _resolve_relative(package, level, module):
    """Absolute module name for a relative import in a module of ``package``.

    Mirrors Python's own resolution: ``level=1`` is the containing package
    itself, each extra level strips one component (``dskit/pipeline/x.py``:
    level=1 → ``dskit.pipeline``, level=2 → ``dskit``).
    """
    parts = package.split(".")
    keep = len(parts) - (level - 1)
    base = ".".join(parts[:keep]) if keep > 0 else ""
    if module:
        return f"{base}.{module}" if base else module
    return base


def _imports(path, package=CORE_PACKAGE):
    """``(module, is_module_level)`` for EVERY import in ``path``.

    Relative imports are resolved against ``package`` (the package the
    file belongs to) and yielded as absolute names, so the same rules
    apply to ``from ..core.fees import venue_fee`` as to its absolute
    spelling. ``from . import x`` yields ``<package>.x`` per alias.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    toplevel = _module_level_import_ids(tree)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name, id(node) in toplevel
        elif isinstance(node, ast.ImportFrom):
            top = id(node) in toplevel
            if node.level == 0:
                if node.module:
                    yield node.module, top
            elif node.module:
                yield _resolve_relative(package, node.level, node.module), top
            else:
                for alias in node.names:
                    yield _resolve_relative(package, node.level, alias.name), top


def _is_stdlib(root):
    return root in sys.stdlib_module_names


def _in_toolkit(module):
    return module == CORE_PACKAGE or module.startswith(CORE_PACKAGE + ".")


def _module_level_offenders(path, package):
    """Module-level imports of ``path`` that are neither stdlib nor toolkit."""
    out = []
    for module, top in _imports(path, package):
        if not top:
            continue
        if _in_toolkit(module) or _is_stdlib(module.split(".")[0]):
            continue
        out.append(f"{path.name}: {module}")
    return out


def _is_journal(module):
    """The one sanctioned sibling (ADR-0056): function-level only."""
    return module == "dskit.journal" or module.startswith("dskit.journal.")


def _outside_toolkit_offenders(path, package):
    """Imports of ``path`` (ANY depth) that reach the owning distribution
    outside the toolkit."""
    out = []
    for module, top in _imports(path, package):
        inside_root = module == ROOT_PACKAGE or module.startswith(ROOT_PACKAGE + ".")
        if inside_root and not _in_toolkit(module):
            if _is_journal(module):
                if top:
                    out.append(
                        f"{path.name}: {module} (dskit.journal must be "
                        "function-level, ADR-0056)"
                    )
                continue
            out.append(f"{path.name}: {module}")
    return out


# ---------------------------------------------------------------------------
# Tier 1 — the core
# ---------------------------------------------------------------------------


def test_core_imports_only_stdlib_and_itself():
    """Module level, tier 1: stdlib or ``dskit.pipeline``. Nothing else.

    A single top-level ``import pandas`` here would stop every document
    planning on a machine without pandas — and wrapping it in
    ``try:``/``if:`` does not defer it, so those count too.
    """
    offenders = []
    for path in _tier1_files():
        offenders.extend(_module_level_offenders(path, CORE_PACKAGE))
    assert not offenders, (
        "tier-1 core must import only stdlib and dskit.pipeline at module "
        f"level: {offenders}"
    )


def test_core_never_reaches_outside_the_toolkit_at_any_depth():
    """Not even inside a function: a ``dskit.*`` import outside
    ``dskit.pipeline`` is the toolkit depending on its own application,
    which makes extraction more than a file move. Relative imports are
    resolved first, so ``from ..core.fees import x`` cannot hide.

    Exception (ADR-0056): ``dskit.journal`` may be imported at
    function depth so RECORD can label a child run. Module-level is
    still refused.
    """
    offenders = []
    for path in _tier1_files():
        offenders.extend(_outside_toolkit_offenders(path, CORE_PACKAGE))
    for path in _tier2_files():
        offenders.extend(_outside_toolkit_offenders(path, LIBS_PACKAGE))
    assert not offenders, (
        f"toolkit reached outside dskit.pipeline (adapters do that): {offenders}"
    )


def test_conformance_imports_pytest_lazily():
    """The one non-stdlib name the core may speak, and the shape it must
    speak it in. ``conformance.py`` exists to be imported BY test modules;
    it must still import cleanly where pytest is absent, so ``pytest``
    lives inside the suite factory — the same discipline nodes owe their
    heavy libraries."""
    path = PACKAGE_DIR / "conformance.py"
    levels = {top for module, top in _imports(path) if module.split(".")[0] == "pytest"}
    assert levels == {False}, (
        "conformance.py must import pytest INSIDE conformance_suite(), never "
        f"at module level (module-level found: {True in levels})"
    )
    ok, detail = import_with_blocked("dskit.pipeline.conformance", ("pytest",))
    assert ok, f"dskit.pipeline.conformance needs pytest to import:\n{detail}"


def test_core_imports_with_heavy_libraries_blocked():
    """The behavioural half for tier 1: one fresh interpreter imports
    EVERY core module with all heavy libraries blocked from
    ``sys.modules``. Static analysis cannot see an import hidden behind
    ``importlib.import_module`` or a re-export; this can. (``pytest`` is
    not in the blocked list, so ``conformance.py`` imports fine.)"""
    modules = []
    for path in _tier1_files():
        stem = path.stem
        modules.append(CORE_PACKAGE if stem == "__init__" else f"{CORE_PACKAGE}.{stem}")
    script = (
        "import sys\n"
        f"for _n in {tuple(DEFAULT_BLOCKED_IMPORTS)!r}:\n"
        "    sys.modules[_n] = None\n"
        "import importlib\n"
        "failures = []\n"
        f"for _m in {tuple(modules)!r}:\n"
        "    try:\n"
        "        importlib.import_module(_m)\n"
        "    except BaseException as exc:\n"
        "        failures.append('%s: %s: %s' % (_m, type(exc).__name__, exc))\n"
        "if failures:\n"
        "    print('\\n'.join(failures))\n"
        "    raise SystemExit(1)\n"
    )
    done = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert done.returncode == 0, (
        "tier-1 core modules must import with every heavy library blocked:\n"
        + (done.stdout or done.stderr).strip()
    )


# ---------------------------------------------------------------------------
# Tier 2 — library packs
# ---------------------------------------------------------------------------


def test_library_packs_name_their_library_only_inside_a_method():
    """A pack may ``import torch`` — inside a METHOD. At module level a
    pack obeys the same rule as the core (stdlib or ``dskit.pipeline``):
    D-146 lets it name its library only inside a method, and a blocklist
    of known-heavy roots would miss e.g. joblib or yaml. The method is
    ``run()`` for a node pack; ``libs/mlflow.py`` is a tracking SINK and
    names mlflow in ``_open``/``log_params``/``log_metrics`` instead —
    what the gate checks is the module LEVEL, not the method's name."""
    offenders = []
    for path in _tier2_files():
        offenders.extend(_module_level_offenders(path, LIBS_PACKAGE))
    assert not offenders, (
        "library packs must import their library inside a method, not at "
        f"module level: {offenders}"
    )


def test_library_packs_import_with_their_library_blocked():
    """The behavioural half, and the one that actually holds: a fresh
    interpreter with every heavy library blocked from ``sys.modules``.

    Static analysis cannot see an import hidden behind
    ``importlib.import_module`` or a re-export; this can.
    """
    failures = []
    for path in _tier2_files():
        if path.name == "__init__.py":
            continue
        module = f"dskit.pipeline.libs.{path.stem}"
        ok, detail = import_with_blocked(module, DEFAULT_BLOCKED_IMPORTS)
        if not ok:
            failures.append(f"{module}:\n{detail}")
    assert not failures, (
        "library packs must import with their libraries blocked (heavy "
        "imports belong inside run()):\n" + "\n\n".join(failures)
    )


# ---------------------------------------------------------------------------
# Layout and venue neutrality
# ---------------------------------------------------------------------------


def test_only_the_sanctioned_subdirectory_exists():
    """D-146 sanctions exactly one subpackage, ``libs/``. Any other is
    where venue code would start hiding — the concern the old flatness
    rule was really about."""
    subdirs = {
        p.name for p in PACKAGE_DIR.iterdir() if p.is_dir() and p.name != "__pycache__"
    }
    assert subdirs <= ALLOWED_SUBDIRS, (
        f"unsanctioned subdirectory under the toolkit: {sorted(subdirs - ALLOWED_SUBDIRS)} "
        f"(D-146 allows only {sorted(ALLOWED_SUBDIRS)}; adapters are CHILD "
        "packages outside dskit, ADR-0032)"
    )


def _docstring_nodes(tree):
    """Every Constant node that is a docstring — exempt from the venue
    rule, because explaining a concept with an example is not coupling."""
    out = set()
    for node in ast.walk(tree):
        if isinstance(
            node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
        ):
            body = getattr(node, "body", None)
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                out.add(id(body[0].value))
    return out


def _venue_hits(path):
    """Venue names in ``path``'s EXECUTABLE code.

    Covers string constants that are not docstrings, bytes constants,
    every declared/referenced identifier (names, args, attributes,
    class/function names), keyword-argument names, import alias names
    and asnames (full dotted paths included), and ``from X import ...``
    module paths — so a venue SDK cannot ride in under a neutral binding.

    Known static limit: a venue name ASSEMBLED at runtime from clean
    fragments (``"kal" + "shi"``) is invisible to any AST rule; the
    behavioural blocked-import tests are the backstop for what such a
    string would be used for (loading a venue library). Comments and
    docstrings stay exempt by doctrine (D-146/F-221) — prose may name a
    venue as an example.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    exempt = _docstring_nodes(tree)
    hits = []
    for node in ast.walk(tree):
        texts = []
        if isinstance(node, ast.Constant):
            if isinstance(node.value, str):
                if id(node) not in exempt:
                    texts.append(node.value)
            elif isinstance(node.value, bytes):
                texts.append(node.value.decode("utf-8", errors="ignore"))
        elif isinstance(node, (ast.Name, ast.arg)):
            texts.append(getattr(node, "id", None) or getattr(node, "arg", None))
        elif isinstance(node, ast.Attribute):
            texts.append(node.attr)
        elif isinstance(node, ast.alias):
            texts.append(node.name)
            texts.append(node.asname)
        elif isinstance(node, ast.ImportFrom):
            texts.append(node.module)
        elif isinstance(node, ast.keyword):
            texts.append(node.arg)
        elif isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            texts.append(node.name)
        for text in texts:
            if not text:
                continue
            lowered = text.lower()
            for venue in VENUE_NAMES:
                if venue in lowered:
                    hits.append(f"{path.name}:{node.lineno}: {text!r}")
    return hits


def test_no_venue_names_in_toolkit_executable_code():
    """Tiers 1–2 are venue-neutral. Prose may name a venue as an example;
    CODE may not — a venue tag or ticker in the core is the toolkit
    knowing something only an adapter is allowed to know."""
    offenders = []
    for path in _tier1_files() + _tier2_files():
        offenders.extend(_venue_hits(path))
    assert not offenders, (
        f"venue names in tier-1/2 executable code: {offenders}. Adapters are "
        "CHILD packages outside dskit (ADR-0032, children/README.md); the "
        "toolkit never names one outside explanatory prose."
    )


# ---------------------------------------------------------------------------
# The helpers themselves — regression tests for the skeptic's holes
# (FN-8: module-level detection + relative imports; FN-9: venue-name
# blind spots). Each feeds a synthetic file through the SAME helpers the
# real-tree tests use, so a helper regression fails here by name.
# ---------------------------------------------------------------------------


def _synthetic(tmp_path, source):
    path = tmp_path / "synthetic_module.py"
    path.write_text(textwrap.dedent(source), encoding="utf-8")
    return path


def test_wrapped_module_level_imports_are_module_level(tmp_path):
    """FN-8.1: ``try:``/``if:``/``with:``/class-body wrappers do not defer
    an import — all of these execute at import time and must be caught."""
    path = _synthetic(
        tmp_path,
        """
        try:
            import pandas
        except ImportError:
            pandas = None
        if True:
            import numpy
        with open("x"):
            import yaml
        class Holder:
            import joblib
        """,
    )
    tops = {module: top for module, top in _imports(path, CORE_PACKAGE)}
    assert tops == {"pandas": True, "numpy": True, "yaml": True, "joblib": True}
    offenders = _module_level_offenders(path, CORE_PACKAGE)
    assert len(offenders) == 4, f"all four wrapped imports must offend: {offenders}"


def test_function_local_imports_stay_function_local(tmp_path):
    """The counterpart: a function DOES defer its imports — the tier-2
    doctrine depends on ``run()``-local imports staying legal."""
    path = _synthetic(
        tmp_path,
        """
        import json

        def run():
            import torch
            class Inner:
                import scipy
            return torch, Inner

        async def arun():
            import optuna
            return optuna
        """,
    )
    tops = {module: top for module, top in _imports(path, CORE_PACKAGE)}
    assert tops == {"json": True, "torch": False, "scipy": False, "optuna": False}
    assert _module_level_offenders(path, CORE_PACKAGE) == []


def test_relative_imports_resolve_against_the_core_package(tmp_path):
    """FN-8.2: ``from ..core.fees import x`` in a core file IS
    ``dskit.core.fees`` — it must be visible and must offend, while
    relative imports WITHIN the toolkit stay legal."""
    path = _synthetic(
        tmp_path,
        """
        from . import node
        from .node import Node
        from ..core.fees import venue_fee
        from .. import core
        """,
    )
    modules = {module for module, _top in _imports(path, CORE_PACKAGE)}
    assert modules == {"dskit.pipeline.node", "dskit.core.fees", "dskit.core"}
    offenders = _outside_toolkit_offenders(path, CORE_PACKAGE)
    assert sorted(offenders) == [
        "synthetic_module.py: dskit.core",
        "synthetic_module.py: dskit.core.fees",
    ]
    assert sorted(_module_level_offenders(path, CORE_PACKAGE)) == sorted(offenders)


def test_relative_imports_resolve_against_the_libs_package(tmp_path):
    """FN-8.2 for tier 2: one more dot of depth — level=2 is the toolkit
    (legal), level=3 escapes it (offends)."""
    path = _synthetic(
        tmp_path,
        """
        from . import sibling_pack
        from ..node import Node
        from ...core.fees import fee
        """,
    )
    modules = {module for module, _top in _imports(path, LIBS_PACKAGE)}
    assert modules == {
        "dskit.pipeline.libs.sibling_pack",
        "dskit.pipeline.node",
        "dskit.core.fees",
    }
    assert _outside_toolkit_offenders(path, LIBS_PACKAGE) == [
        "synthetic_module.py: dskit.core.fees"
    ]


def test_tier2_module_level_rule_catches_any_non_stdlib_import(tmp_path):
    """FN-8.3: the tier-2 module-level rule is stdlib-or-toolkit, not a
    blocklist — joblib and yaml were never in FORBIDDEN_ROOTS and must
    offend anyway; the same import inside ``run()`` stays legal."""
    path = _synthetic(
        tmp_path,
        """
        import joblib
        from dskit.pipeline.node import Node

        class Pack(Node):
            def run(self, ctx, inputs):
                import yaml
                return {"out": yaml, "cache": joblib}
        """,
    )
    assert _module_level_offenders(path, LIBS_PACKAGE) == [
        "synthetic_module.py: joblib"
    ]


def test_venue_hits_sees_aliases_modules_keywords_and_bytes(tmp_path):
    """FN-9: import alias names/asnames (dotted paths included),
    ``from X import ...`` module paths, keyword-argument names, and bytes
    constants are all executable venue couplings and must all hit."""
    path = _synthetic(
        tmp_path,
        """
        import a.b.examplevenue_util
        import numpy as examplevenue_np

        def open_session():
            import examplevenue_sdk as _sdk
            from othervenue_sdk import Client
            return _sdk, Client

        def tag(record):
            return dict(record, examplevenue=True)

        VENUE_TAG = b"othervenue"
        """,
    )
    hits = _venue_hits(path)
    for needle in (
        "a.b.examplevenue_util",  # dotted alias name
        "examplevenue_np",  # alias asname
        "examplevenue_sdk",  # function-local alias name
        "othervenue_sdk",  # ImportFrom.module
        "examplevenue",  # keyword-argument name
        "othervenue",  # bytes constant
    ):
        assert any(needle in hit for hit in hits), f"{needle!r} missed: {hits}"


def test_venue_hits_keeps_the_docstring_exemption(tmp_path):
    """Doctrine (D-146/F-221): prose may name a venue. Module, class and
    function docstrings and comments must stay exempt — and a plain
    string constant elsewhere must not."""
    path = _synthetic(
        tmp_path,
        '''
        """Examplevenue is a fine example of a venue this toolkit never names."""

        # an othervenue comment is prose too


        class Explainer:
            """group is an examplevenue event id."""

            def method(self):
                """examplevenue feeds the history."""
                return None
        ''',
    )
    assert _venue_hits(path) == []
    leaky = tmp_path / "leaky_module.py"
    leaky.write_text('VENUE = "examplevenue"\n', encoding="utf-8")
    assert len(_venue_hits(leaky)) == 1
