"""The decomposed-module pin (TODO 3d).

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
"""

import ast
import pathlib

import dskit.pipeline

PACKAGE_DIR = pathlib.Path(dskit.pipeline.__file__).parent

#: The ceiling, in source lines from ``def`` to the body's last line.
#: ~100 is the number TODO 3d states; it is stated ONCE, here.
MAX_FUNCTION_LINES = 100

#: Modules taken through TODO 3d, relative to ``dskit/pipeline``. Append
#: a module here when you decompose it.
DECOMPOSED_MODULES = (
    "driver.py",
    "kinds_report.py",
    "libs/torch.py",
)


def _function_lengths(path):
    """Every function/method in ``path`` as ``(qualified name, line, n lines)``."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found = []

    def walk(node, prefix):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef):
                walk(child, f"{prefix}{child.name}.")
            elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                found.append(
                    (
                        f"{prefix}{child.name}",
                        child.lineno,
                        child.end_lineno - child.lineno + 1,
                    )
                )
                walk(child, f"{prefix}{child.name}.")

    walk(tree, "")
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
