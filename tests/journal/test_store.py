"""CSV store + generated markdown; path JOINs and does not copy."""

import pytest


from dskit.journal.locate import init_journal
from dskit.journal.model import Action, PathRow
from dskit.journal.render import escape_cell, render, render_text
from dskit.journal.store import append_action_row, append_path_row, read_actions, read_path


def _child(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
    (tmp_path / "configs").mkdir()
    return init_journal(str(tmp_path))


def test_append_and_render_join(tmp_path):
    root = _child(tmp_path)
    action = Action(
        id="A0001",
        category="execute",
        step="hl-scan",
        executed_at="2026-08-31T00:00:00+00:00",
        inputs="configs/run-hl-scan.json",
        outputs="pipeline_runs/hl-scan",
        db_location="pipeline_runs/hl-scan",
        notes="ok",
    )
    append_action_row(root, action)
    append_path_row(root, PathRow(
        id="A0001", label="HL scan", purpose="select horizon",
        relevant_files="pipeline_runs/hl-scan", locked="Y",
        current_work="", criteria="empirical",
    ))
    render(root)
    text = root.readme.read_text() if hasattr(root.readme, "read_text") else open(
        root.readme, encoding="utf-8"
    ).read()
    assert "A0001" in text
    assert "hl-scan" in text
    assert "empirical" in text
    path_csv = open(root.path_csv, encoding="utf-8").read()
    assert "category" not in path_csv
    assert "empirical" in path_csv
    assert read_path(root)[0].id == "A0001"
    assert read_actions(root)[0].step == "hl-scan"


def test_legacy_path_refuses_extra_cells_without_rewriting(tmp_path):
    root = _child(tmp_path)
    original = "id,criteria\nA0001,empirical,do-not-drop\n"
    with open(root.path_csv, "w", encoding="utf-8") as fh:
        fh.write(original)
    from dskit.journal.base import JournalError

    with pytest.raises(JournalError, match="extra cells"):
        read_path(root)
    with open(root.path_csv, encoding="utf-8") as fh:
        assert fh.read() == original


def test_legacy_path_refuses_promotion_without_rewriting(tmp_path):
    root = _child(tmp_path)
    append_action_row(root, Action(
        id="A0001", category="execute", step="fit", executed_at="t",
    ))
    original = "id,criteria\nA0002,empirical\n"
    with open(root.path_csv, "w", encoding="utf-8") as fh:
        fh.write(original)
    from dskit.journal.base import JournalError

    with pytest.raises(JournalError, match="legacy path.csv is read-only"):
        append_path_row(root, PathRow(
            id="A0001", label="Fit", purpose="validate",
            relevant_files="pipeline_runs/fit", locked="N",
            current_work="", criteria="empirical",
        ))
    with open(root.path_csv, encoding="utf-8") as fh:
        assert fh.read() == original


def test_render_shows_only_the_latest_ten_actions():
    actions = [
        Action(
            id=f"A{i:04d}", category="execute", step=f"run-{i}",
            executed_at="t",
        )
        for i in range(1, 13)
    ]
    text = render_text(actions, [])
    assert "## Actions (latest 10)" in text
    assert "A0001" not in text
    assert "A0002" not in text
    assert "A0012" in text
    assert "complete, append-only journal" in text


def test_render_join_does_not_restated_category_in_path_source():
    text = render_text(
        [
            Action(
                id="A0001",
                category="execute",
                step="fit",
                executed_at="t",
                db_location="/runs/x",
            )
        ],
        [PathRow(
            id="A0001", label="Fit", purpose="choose model",
            relevant_files="/runs/x", locked="N", current_work="",
            criteria="judgemental",
        )],
    )
    assert "| A0001 | Fit | choose model | /runs/x | N |  | execute | fit | judgemental | /runs/x |" in text


def test_pipe_in_cell_does_not_split_columns():
    assert escape_cell("a|b") == "a\\|b"
    text = render_text(
        [
            Action(
                id="A0001",
                category="acquire",
                step="pull",
                executed_at="t",
                notes="a|b",
            )
        ],
        [],
    )
    assert "a\\|b" in text
    row = [ln for ln in text.splitlines() if ln.startswith("| A0001")][0]
    cells = [c.strip() for c in row.strip().strip("|").split(" | ")]
    assert len(cells) == 8
    assert cells[-1] == "a\\|b"


def test_the_package_readme_restates_the_process():
    """journal/README.md and the generated header are one flow."""
    import os

    from dskit.journal.render import PROCESS

    here = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
    pkg = os.path.join(here, "dskit", "journal", "README.md")
    with open(pkg, encoding="utf-8") as fh:
        readme = fh.read()
    needles = (
        "acquire  →  research  →  execute  →  production",
        "acquire --mode backfill|live",
        "journal research",
        "hooks.production",
        "CSV, not a database",
        "journal promote",
    )
    text = render_text([], [])
    for needle in needles:
        assert needle in PROCESS, needle
        assert needle in readme, needle
        assert needle in text, needle
