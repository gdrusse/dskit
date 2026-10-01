"""Pin the approved ADR-0212 bootstrap and child boundary."""

from pathlib import Path
import re
import tomllib

CHILD_ROOT = Path(__file__).resolve().parents[1]

EXPECTED_FILES = {
    "AGENTS.md",
    "CLAUDE.md",
    "README.md",
    "journal.json",
    "docs/decisioning/README.md",
    "docs/decisioning/actions.csv",
    "docs/decisioning/path.csv",
    "docs/memos/.gitkeep",
    "docs/memos/README.md",
    "docs/plans/.gitkeep",
    "docs/plans/README.md",
    "docs/research/.gitkeep",
    "docs/research/README.md",
    "pyproject.toml",
    "stock_options/__init__.py",
    "tests/conftest.py",
    "tests/test_package_boundary.py",
}


def _files() -> set[str]:
    return {
        path.relative_to(CHILD_ROOT).as_posix()
        for path in CHILD_ROOT.rglob("*")
        if path.is_file()
        and "__pycache__" not in path.parts
        and ".pytest_cache" not in path.parts
        and path.suffix != ".pyc"
    }


def test_exact_approved_bootstrap_manifest():
    assert _files() == EXPECTED_FILES


def test_package_is_empty_and_importable():
    import stock_options

    assert stock_options.__all__ == ()


def test_packaging_is_standalone_and_only_depends_on_dskit():
    with (CHILD_ROOT / "pyproject.toml").open("rb") as handle:
        project = tomllib.load(handle)
    assert project["project"]["name"] == "stock-options"
    assert project["project"]["dependencies"] == ["dskit"]
    assert project["tool"]["setuptools"]["packages"]["find"]["include"] == [
        "stock_options*"
    ]


def test_child_does_not_import_sibling_children():
    offenders = []
    for path in (CHILD_ROOT / "stock_options").rglob("*.py"):
        for line_number, line in enumerate(path.read_text().splitlines(), start=1):
            if line.lstrip().startswith(("import ", "from ")) and re.search(
                r"\bchildren\b", line
            ):
                offenders.append(f"{path.name}:{line_number}:{line.strip()}")
    assert offenders == []


def test_scope_documents_name_the_prediction_action_split():
    text = "\n".join(
        (CHILD_ROOT / name).read_text()
        for name in ("README.md", "AGENTS.md", "docs/plans/README.md")
    ).lower()
    assert "stock-specific" in text
    assert "option action set" in text
    assert "split-adjusted" in text
