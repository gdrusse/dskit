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

Five more rules live here because they are structural, not stylistic:

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
* **One refusal, one cause; one `match=`, one refusal.** A message raised
  for two independent causes cannot be asserted against — a test naming
  one gate passes on the other, and keeps passing with that gate dead.
  That is ADR-0150's defect made mechanical: the pair of rules below
  refuses the conflation at the source, and refuses a suite pattern that
  cannot tell two refusals of one function apart.

The static halves iterate over whatever modules EXIST, so this file is
useful from the first commit of the package; one behavioural test asserts
the package is actually there and imports with every heavy library
blocked.
"""

import ast
import pathlib
import re
import sys

import pytest

import dskit
from dskit.pipeline.conformance import DEFAULT_BLOCKED_IMPORTS, import_with_blocked

# The venue rule and its AST walkers have ONE owner (CLAUDE.md: a function
# is never repeated across modules) — the toolkit's own gate.
from tests.pipeline.test_purity import (
    VENUE_NAMES,
    _imports,
    _venue_hits,
    private_cross_package_uses,
)

PACKAGE = "dskit.production"
LIBS_PACKAGE = "dskit.production.libs"
PACKAGE_DIR = pathlib.Path(dskit.__file__).parent / "production"
LIBS_DIR = PACKAGE_DIR / "libs"

#: This package's OWN suite — the only one whose `match=` patterns name this
#: package's refusal vocabulary. A pattern written against another package's
#: messages would be compared against the wrong set and flagged for nothing.
SUITE_DIR = pathlib.Path(__file__).parent

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


#: The one third-party name the package may reach, and only from inside the
#: conformance-suite builder (§5.7's `executor_conformance_suite`, the
#: `dskit.pipeline.conformance` precedent): pytest, at function depth, in
#: `executor.py`. A serve process never executes that builder.
CONFORMANCE_MODULE = "executor.py"
CONFORMANCE_IMPORT = "pytest"


def _is_conformance_import(path, module):
    """Say whether ``module`` is the conformance builder's lazy pytest import."""
    return path.name == CONFORMANCE_MODULE and (
        module == CONFORMANCE_IMPORT or module.startswith(CONFORMANCE_IMPORT + ".")
    )


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
            if _is_conformance_import(path, module):
                if top:
                    offenders.append(f"{path.name}: {module} (function depth only)")
                continue
            if _is_allowed_dskit(module) or _is_stdlib(module):
                continue
            offenders.append(f"{path.name}: {module}")
    assert not offenders, f"dskit/production reached outside its rule: {offenders}"


def test_bundles_module_never_imports_pipeline_trust():
    """ADR-0146 Decision point 10: `compose_replay_tape` reaches
    `CapturedAuthorizationRecord`/`LaunchSession`/`_Published` only through
    values the caller already holds and their own public methods/
    attributes, never through an import of `dskit.pipeline.trust` from
    `production/bundles.py`, at any depth. Stricter than the package-wide
    rule above, which PERMITS but does not REQUIRE this (`dskit.pipeline`
    is an allowed prefix for the whole package) -- a dedicated regression
    test for this ADR's own stricter commitment (evidence 0190
    open_findings_for_independent_skeptic: 'no automated test currently
    enforces ADR-0146 Decision point 10's...commitment specifically for
    bundles.py')."""
    path = PACKAGE_DIR / "bundles.py"
    offenders = [
        module
        for module, _top in _imports(path, PACKAGE)
        if module == "dskit.pipeline.trust" or module.startswith("dskit.pipeline.trust.")
    ]
    assert not offenders, offenders


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


def _names_its_own_library(path, module):
    """Say whether ``module`` is the library the pack's own filename names."""
    root = module.split(".")[0]
    return root.startswith(path.stem) or path.stem.startswith(root)


def test_a_pack_never_names_its_own_library_at_module_level_even_a_stdlib_one():
    """The rule above is blind to a pack whose library ships with Python:
    `sqlite3` is stdlib, so `_is_stdlib` waves it through, and
    `libs/sqlite.py` would then import its library at module level while the
    gate reported clean. The pack's FILENAME is what names its library
    (§8: "one module per library"), so that is what is checked — importing
    the production layer must never pay for a library the serve document
    does not declare, whoever ships it."""
    offenders = [
        f"libs/{path.name}: {module}"
        for path in _pack_files()
        for module, top in _imports(path, LIBS_PACKAGE)
        if top and _names_its_own_library(path, module)
    ]
    assert not offenders, offenders


def test_the_own_library_detector_matches_a_pack_to_its_library(tmp_path):
    """The gate is worth what its detector catches: `sqlite.py` must own
    `sqlite3`, `opentelemetry.py` must own the package of its own name, and
    an unrelated stdlib import must stay legal."""
    assert _names_its_own_library(tmp_path / "sqlite.py", "sqlite3")
    assert _names_its_own_library(tmp_path / "opentelemetry.py", "opentelemetry.sdk")
    assert _names_its_own_library(tmp_path / "exchange_calendars.py", "exchange_calendars")
    assert not _names_its_own_library(tmp_path / "sqlite.py", "json")
    assert not _names_its_own_library(tmp_path / "parquet.py", "os")


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
# One refusal, one cause; one `match=`, one refusal
# ---------------------------------------------------------------------------


def _raised_literals(node):
    """Every string literal an exception argument spells out."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [node.value]
    if isinstance(node, (ast.List, ast.Tuple)):
        return [text for element in node.elts for text in _raised_literals(element)]
    return []


def _scopes(node, qualname=""):
    """Yield `(qualname, node)` for every function, method and nested def."""
    for child in ast.iter_child_nodes(node):
        if not isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            yield from _scopes(child, qualname)
            continue
        nested = f"{qualname}.{child.name}" if qualname else child.name
        if not isinstance(child, ast.ClassDef):
            yield nested, child
        yield from _scopes(child, nested)


def _refusal_messages(path):
    """Map ``file:qualname`` to the distinct literal refusals that scope raises.

    Both of the package's refusal idioms count: ``raise ValueError("...")``
    and the ``problems`` list a ``ProductionError`` is raised with — the
    latter only when the appended-to name is the very name that is raised,
    so an unrelated ``.append`` cannot smuggle a string into the vocabulary.

    Parameters
    ----------
    path : pathlib.Path
        One module of the package.

    Returns
    -------
    dict
        ``{"module.py:qualname": {message, ...}}``, scopes with no literal
        refusal omitted.
    """
    found = {}
    for qualname, scope in _scopes(_tree(path)):
        raised_names = {
            node.exc.args[0].id
            for node in ast.walk(scope)
            if isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call)
            and node.exc.args and isinstance(node.exc.args[0], ast.Name)
        }
        texts = set()
        for node in ast.walk(scope):
            if isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call) and node.exc.args:
                texts.update(_raised_literals(node.exc.args[0]))
            elif (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in ("append", "extend")
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id in raised_names
                and node.args
            ):
                texts.update(_raised_literals(node.args[0]))
        if texts:
            found[f"{path.name}:{qualname}"] = texts
    return found


def _operand_names(node):
    """Every name and attribute one operand of a condition reads."""
    return {child.id for child in ast.walk(node) if isinstance(child, ast.Name)} | {
        child.attr for child in ast.walk(node) if isinstance(child, ast.Attribute)
    }


def _conflated_refusals(path):
    """Refusals raised under an ``or`` whose operands share no name."""
    offenders = []
    for node in ast.walk(_tree(path)):
        if not isinstance(node, ast.If) or not isinstance(node.test, ast.BoolOp):
            continue
        if not isinstance(node.test.op, ast.Or):
            continue
        messages = [
            text
            for statement in node.body
            if isinstance(statement, ast.Raise)
            and isinstance(statement.exc, ast.Call)
            and statement.exc.args
            for text in _raised_literals(statement.exc.args[0])
        ]
        causes = [_operand_names(value) for value in node.test.values]
        independent = all(
            not (left & right)
            for index, left in enumerate(causes)
            for right in causes[index + 1:]
        )
        if messages and independent:
            offenders.append(f"{path.name}:{node.lineno}: {messages[0]!r}")
    return offenders


def test_no_refusal_message_serves_two_independent_causes():
    """`if a is None or missing: raise ValueError(one_message)` is the
    ADR-0150 defect in its original spelling: two causes that share no
    term, answered by one string, so no caller and no test can tell which
    gate fired. Two spellings of ONE cause (`x is None or x == ""`) touch
    the same name and are left alone. Split the refusal; name each cause."""
    offenders = [hit for path in _all_files() for hit in _conflated_refusals(path)]
    assert not offenders, (
        f"one refusal message serving two independent causes: {offenders} — "
        "give each cause its own message, so a test can name the gate it exercises"
    )


def test_the_conflated_refusal_detector_separates_causes_from_spellings(tmp_path):
    """Two independent causes hit; two spellings of one cause, a plain
    single-cause guard, and an `or` with no refusal under it do not."""
    path = tmp_path / "probe_refusal.py"
    path.write_text(
        "def capture(self, ledger, missing, name):\n"
        "    if ledger is None or missing:\n"
        "        raise ValueError('CAPTURED refuses before the plan is bound')\n"
        "    if name is None or name == '':\n"
        "        raise ValueError('a name is required')\n"
        "    if missing:\n"
        "        raise ValueError('the plan is required')\n"
        "    if ledger is None or name:\n"
        "        return None\n"
        "    return ledger\n",
        encoding="utf-8",
    )
    assert _conflated_refusals(path) == [
        "probe_refusal.py:2: 'CAPTURED refuses before the plan is bound'"
    ]


def _match_patterns(path):
    """Every literal ``pytest.raises(..., match=...)`` pattern, with its line."""
    found = []
    for node in ast.walk(_tree(path)):
        if not isinstance(node, ast.Call):
            continue
        name = (
            node.func.attr if isinstance(node.func, ast.Attribute)
            else getattr(node.func, "id", None)
        )
        if name != "raises":
            continue
        for keyword in node.keywords:
            value = keyword.value
            if keyword.arg == "match" and isinstance(value, ast.Constant) and isinstance(value.value, str):
                found.append((node.lineno, value.value))
    return found


def _undiscriminating(pattern, vocabulary):
    """The scopes whose own refusals ``pattern`` cannot tell apart.

    Parameters
    ----------
    pattern : str
        A ``pytest.raises`` ``match=`` regular expression.
    vocabulary : dict
        ``_refusal_messages`` output, merged across the package.

    Returns
    -------
    list
        One ``"scope -> [messages]"`` string per scope where the pattern
        matches two or more of that ONE scope's distinct refusals; empty
        when the pattern discriminates everywhere.
    """
    expression = re.compile(pattern)
    offenders = []
    for scope, texts in sorted(vocabulary.items()):
        hits = sorted(text for text in texts if expression.search(text))
        if len(hits) > 1:
            offenders.append(f"{scope} -> {hits}")
    return offenders


def test_no_raises_pattern_confuses_two_refusals_of_one_function():
    """A `match=` that fits two refusals of the SAME function proves
    nothing about which of them fired — the exact hole ADR-0150 found,
    where one wording served two gates and four sites named a gate they
    never reached. Scoped to refusals raised in ONE scope because those
    are the ones genuinely reachable from one call; a pattern that also
    fits some unrelated module's string is loose, not wrong."""
    vocabulary = {}
    for path in _all_files():
        vocabulary.update(_refusal_messages(path))
    assert vocabulary, "no refusal vocabulary read — the detector is not seeing the package"
    patterns = [
        (path.name, lineno, pattern)
        for path in sorted(SUITE_DIR.glob("test_*.py"))
        for lineno, pattern in _match_patterns(path)
    ]
    assert patterns, "no match= patterns read — the detector is not seeing the suite"
    offenders = [
        f"{name}:{lineno}: {pattern!r} cannot separate {scope}"
        for name, lineno, pattern in patterns
        for scope in _undiscriminating(pattern, vocabulary)
    ]
    assert not offenders, (
        "pytest.raises(match=...) patterns that fit two refusals of one function:\n"
        + "\n".join(offenders)
        + "\nname the exact refusal the test exercises"
    )


def test_the_match_pattern_reader_sees_only_a_literal_match_keyword(tmp_path):
    """The rule above is worth nothing if it reads no patterns: a literal
    `match=` hits, while a computed pattern, a bare `raises` and an
    unrelated `.raises(...)` call contribute nothing to assert against."""
    path = tmp_path / "probe_patterns.py"
    path.write_text(
        "def test_one():\n"
        "    with pytest.raises(ValueError, match='exact refusal'):\n"
        "        gate()\n"
        "    with pytest.raises(ValueError, match=PATTERN):\n"
        "        gate()\n"
        "    with pytest.raises(ValueError):\n"
        "        gate()\n"
        "    alarm.raises(other='x')\n",
        encoding="utf-8",
    )
    assert _match_patterns(path) == [(2, "exact refusal")]


def test_the_pattern_detector_flags_only_a_pattern_spanning_two_refusals():
    """Pinned on ADR-0150's own wording: the sentinel's old alternation
    reached both of `capture`'s refusals, its four siblings reached one,
    and the exact phrase they were corrected to reaches one."""
    conflated = {
        "verifier.py:HistoricalStudyVerifier.capture": {
            "CAPTURED refuses before ScopeIntent, CES, PEA, BVP, and CAS are bound",
            "CAPTURED refuses after consumed admission is spent",
        },
        "verifier.py:HistoricalStudyVerifier.bind": {"unknown plan artifact"},
    }
    assert _undiscriminating("ScopeIntent|CES|PEA|BVP|CAS|admission", conflated) == [
        "verifier.py:HistoricalStudyVerifier.capture -> ["
        "'CAPTURED refuses after consumed admission is spent', "
        "'CAPTURED refuses before ScopeIntent, CES, PEA, BVP, and CAS are bound']"
    ]
    assert not _undiscriminating("ScopeIntent|CES|PEA|BVP|CAS", conflated)
    assert not _undiscriminating(
        "refuses before ScopeIntent, CES, PEA, BVP, and CAS are bound", conflated
    )


def test_the_refusal_vocabulary_reads_both_idioms(tmp_path):
    """A plain `raise`, a `ProductionError` problem list, and an append to
    a list that is never raised — the third must not enter the vocabulary."""
    path = tmp_path / "probe_vocab.py"
    path.write_text(
        "class Gate:\n"
        "    def check(self, value, notes):\n"
        "        problems = []\n"
        "        notes.append('not a refusal')\n"
        "        if value is None:\n"
        "            raise ValueError('value is required')\n"
        "        problems.append('value is out of range')\n"
        "        if problems:\n"
        "            raise ProductionError(problems)\n",
        encoding="utf-8",
    )
    assert _refusal_messages(path) == {
        "probe_vocab.py:Gate.check": {"value is required", "value is out of range"}
    }


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


def test_no_private_name_is_imported_from_another_package():
    """The `_` prefix is the API contract in BOTH directions.

    §9.1 states twice that production may not import a private pipeline
    name, and made `runs.render_cell` public rather than reach for
    `_render_cell` — but nothing enforced it, and `decider.py` was
    importing `driver._is_summary` and `driver._winner_names` the whole
    time. A rule the plan claims and no test keeps is how that happens.
    Reaching past another package's public surface couples this one to
    an internal that may be renamed without notice; the fix is always to
    give the rule a public name and ONE owner, never a second copy.

    This is THIS package's half of the rule. The rule itself, and the
    sweep across every package in both directions, belong to the
    toolkit's own gate — `private_cross_package_uses` is imported, not
    restated, because a second copy of a drift check drifts.
    """
    offenders = []
    for path in _all_files():
        package = LIBS_PACKAGE if path.parent == LIBS_DIR else PACKAGE
        offenders.extend(private_cross_package_uses(path, package))
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
