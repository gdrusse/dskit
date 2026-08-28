"""The decomposed-module pins (TODO 3d).

CLAUDE.md's working agreement says it in one line: *"One job per method.
If you need comment headers to mark sections inside a body, those
sections are the methods."* TODO 3d names the bodies that had grown past
that — `driver.run_document`, `kinds_report.RunReport.run`,
`libs/torch.TorchTrain.run_train` — and decomposing them is only half the
job. Nothing stopped the next edit from growing a 400-line body back, so
the agreement was a value stated in prose with nothing pinning it.

This is the pin. Every module in :data:`DECOMPOSED_MODULES` has been
taken through 3d, and every function and method in it must stay under
:data:`MAX_FUNCTION_LINES`. The list is an ALLOWLIST, not a repo-wide
rule: other modules still carry long functions that 3d did not cover
(``planner.plan``, ``resolve.resolve``, ``runner.run``), and asserting
over the whole tree would fail for work nobody has done yet. **A module
joins this tuple when it is decomposed** — that is what makes the pin
grow instead of rot.

Deliberately NOT here: ``conformance.py``. It is a pytest-class factory
whose one long body IS the generated suite (TODO 3d exempts it by name).

The measure is source lines from ``def`` to the last line of the body,
docstring included — the thing a reader has to scroll, which is what the
rule is about.

Decomposing a body MULTIPLIES its signatures, which is why the second
pin lives here too: CLAUDE.md's docstring standard says *"No type hints
in signatures — not on parameters, not on returns. The ``Returns``
section already states the type; an annotation would be the same fact in
two places with nothing pinning them."* Extracting one 400-line body into
eighteen helpers is eighteen chances to restate a return type, and the
first pass through 3d took all of them. Nothing enforces the standard —
ruff's `D` rules say nothing about annotations, and ``driver.py`` and
``libs/torch.py`` still hold `per-file-ignores` entries — so
:func:`test_no_new_signature_annotation_in_a_decomposed_module` is what
enforces it on the modules 3d touched.
"""

import ast
import pathlib

import dskit.pipeline

PACKAGE_DIR = pathlib.Path(dskit.pipeline.__file__).parent

#: The ceiling, in source lines from ``def`` to the body's last line.
#: TODO 3d names no ceiling — it names the four bodies it wants gone
#: (399/252/230/50 lines) and exempts ``conformance.py`` at 927. The
#: ~100 comes from the 3d work order, and this is the ONE place it is
#: stated; nothing else in the tree may restate the number.
MAX_FUNCTION_LINES = 100

#: Modules taken through TODO 3d, relative to ``dskit/pipeline``. Append
#: a module here when you decompose it.
DECOMPOSED_MODULES = (
    "driver.py",
    "kinds_report.py",
    "libs/torch.py",
)

#: The signatures in :data:`DECOMPOSED_MODULES` that already carried an
#: annotation before 3d, as ``(module, qualified name)``. CLAUDE.md's
#: standard grandfathers them verbatim — *"Existing annotations stay;
#: they are simply never required"* — so the pin allows exactly these and
#: nothing else. **This tuple may shrink, never grow**: an entry leaves
#: when its annotation does, and a body that gains one is the drift the
#: pin exists to catch. Rename such a function and you rename it here.
GRANDFATHERED_ANNOTATIONS = frozenset(
    {
        ("driver.py", "_atomic_write_text"),
        ("driver.py", "_write_json"),
        ("driver.py", "_Trackers.log_params"),
        ("driver.py", "_Trackers.log_metrics"),
        ("driver.py", "_Trackers.close"),
        ("driver.py", "_canonical_hash"),
        ("driver.py", "_apply_param_override"),
        ("driver.py", "_SearchSeam.__call__"),
        ("driver.py", "_node_metrics"),
        ("driver.py", "DocumentRunResult.exit_code"),
        ("driver.py", "DocumentRunResult.verdict"),
        ("driver.py", "run_document"),
        ("driver.py", "WalkForwardRunResult.exit_code"),
        ("driver.py", "_cutoff_ms"),
        ("driver.py", "_fold_splits"),
        ("driver.py", "run_walk_forward"),
        ("kinds_report.py", "register"),
        ("libs/torch.py", "TorchBatches.__len__"),
        ("libs/torch.py", "_TorchModel._features_required"),
        ("libs/torch.py", "_DeclaredParams._features_required"),
        ("libs/torch.py", "register"),
    }
)


def _functions(path):
    """Every function/method in ``path`` as ``(qualified name, ast node)``.

    Both pins walk the same tree, so they share one walker: a body the
    length pin cannot see is a body the annotation pin cannot see either,
    and one walker keeps that true by construction.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found = []

    def walk(node, prefix):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef):
                walk(child, f"{prefix}{child.name}.")
            elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                found.append((f"{prefix}{child.name}", child))
                walk(child, f"{prefix}{child.name}.")

    walk(tree, "")
    return found


def _function_lengths(path):
    """Every function/method in ``path`` as ``(qualified name, line, n lines)``."""
    return [
        (name, node.lineno, node.end_lineno - node.lineno + 1)
        for name, node in _functions(path)
    ]


def _annotated_functions(path):
    """Every function in ``path`` carrying a type hint, as ``(name, line)``.

    Parameters, ``*args``/``**kwargs``, keyword-only parameters and the
    return are all checked — the standard forbids the lot, and a pin that
    only saw returns would claim coverage it lacks.
    """
    found = []
    for name, node in _functions(path):
        args = node.args
        every = [
            *args.posonlyargs,
            *args.args,
            *args.kwonlyargs,
            args.vararg,
            args.kwarg,
        ]
        annotated = node.returns is not None or any(
            a is not None and a.annotation is not None for a in every
        )
        if annotated:
            found.append((name, node.lineno))
    return found


def test_every_decomposed_module_exists():
    """A renamed module must not silently drop out of the pin."""
    for relative in DECOMPOSED_MODULES:
        assert (PACKAGE_DIR / relative).is_file(), (
            f"{relative} is pinned as decomposed but is not in "
            f"{PACKAGE_DIR} — a module that moved must move in this tuple too, "
            "or the pin quietly covers nothing"
        )


def test_no_function_in_a_decomposed_module_is_long():
    """No body in a 3d module exceeds the ceiling."""
    long_ones = []
    for relative in DECOMPOSED_MODULES:
        path = PACKAGE_DIR / relative
        for name, lineno, length in _function_lengths(path):
            if length > MAX_FUNCTION_LINES:
                long_ones.append(f"{relative}:{lineno} {name} ({length} lines)")
    assert not long_ones, (
        "these bodies exceed "
        f"{MAX_FUNCTION_LINES} lines in a module TODO 3d decomposed — if a "
        "body needs comment headers to mark its sections, those sections "
        "are the methods:\n  " + "\n  ".join(long_ones)
    )


def test_the_measure_sees_methods_and_nested_helpers(tmp_path):
    """The measure descends into classes and inner defs, and counts each
    body from its own ``def`` — a pin that only saw module-level functions
    would let a 400-line METHOD through, which is the exact shape 3d was
    about."""
    module = tmp_path / "sample.py"
    module.write_text(
        "class C:\n"
        "    def method(self):\n" + "        x = 1\n" * 4 + "\n"
        "def outer():\n" + "    y = 1\n" * 5 + "    def inner():\n        pass\n",
        encoding="utf-8",
    )
    assert {name: length for name, _, length in _function_lengths(module)} == {
        "C.method": 5,
        "outer": 8,
        "outer.inner": 2,
    }


def test_no_new_signature_annotation_in_a_decomposed_module():
    """Decomposition must not smuggle type hints into the new signatures."""
    offenders = []
    for relative in DECOMPOSED_MODULES:
        for name, lineno in _annotated_functions(PACKAGE_DIR / relative):
            if (relative, name) not in GRANDFATHERED_ANNOTATIONS:
                offenders.append(f"{relative}:{lineno} {name}")
    assert not offenders, (
        "these signatures carry type hints, which CLAUDE.md's docstring "
        "standard forbids on new code — the docstring's Parameters and "
        "Returns sections already state the types, and an annotation is "
        "the same fact in a second place with nothing pinning them:\n  "
        + "\n  ".join(offenders)
    )


def test_the_grandfather_list_only_shrinks():
    """Every allowed annotation still exists, so the list cannot rot.

    An entry whose function was renamed, deleted or de-annotated would
    otherwise sit here forever, silently licensing a name that no longer
    means what it meant — and a later body could reclaim that name and
    inherit the licence.
    """
    live = {
        (relative, name)
        for relative in DECOMPOSED_MODULES
        for name, _ in _annotated_functions(PACKAGE_DIR / relative)
    }
    stale = sorted(f"{relative} {name}" for relative, name in GRANDFATHERED_ANNOTATIONS - live)
    assert not stale, (
        "these grandfathered annotations are gone from the source — drop "
        "them from GRANDFATHERED_ANNOTATIONS, which may shrink but never "
        "grow:\n  " + "\n  ".join(stale)
    )


def test_the_annotation_measure_sees_parameters_too(tmp_path):
    """A parameter hint counts, not just a return one.

    The standard forbids both (*"not on parameters, not on returns"*), so
    a pin that only looked at ``node.returns`` would pass a signature that
    restates every argument's type.
    """
    module = tmp_path / "sample.py"
    module.write_text(
        "def plain(a, b):\n    pass\n"
        "def returns_hinted(a) -> int:\n    return a\n"
        "def param_hinted(a: int):\n    pass\n"
        "def kwonly_hinted(*, a: int = 1):\n    pass\n"
        "def vararg_hinted(*args: int):\n    pass\n"
        "class C:\n    def method_hinted(self) -> None:\n        pass\n",
        encoding="utf-8",
    )
    assert [name for name, _ in _annotated_functions(module)] == [
        "returns_hinted",
        "param_hinted",
        "kwonly_hinted",
        "vararg_hinted",
        "C.method_hinted",
    ]
