"""Hooks: production context manager records on success and on error."""

import pytest

from dskit.journal.hooks import production, record_execute
from dskit.journal.locate import init_journal
from dskit.journal.store import read_actions


def _child(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
    (tmp_path / "configs").mkdir()
    return init_journal(str(tmp_path))


def test_record_execute_and_production(tmp_path, monkeypatch):
    monkeypatch.setenv("DSKIT_JOURNAL_TESTS", "1")
    root = _child(tmp_path)
    monkeypatch.chdir(root.child_root)
    record_execute(
        "fit",
        inputs="run.json",
        outputs="/runs/x",
        db_location="/runs/x",
        notes="ok",
    )
    with production("paper loop", inputs="live.py", db_location="."):
        pass
    rows = read_actions(root)
    assert [r.category for r in rows] == ["execute", "production"]
    assert rows[1].step == "paper loop"


def test_production_records_failure(tmp_path, monkeypatch):
    monkeypatch.setenv("DSKIT_JOURNAL_TESTS", "1")
    root = _child(tmp_path)
    monkeypatch.chdir(root.child_root)
    with pytest.raises(RuntimeError):
        with production("paper loop"):
            raise RuntimeError("boom")
    rows = read_actions(root)
    assert rows[0].category == "production"
    assert "RuntimeError" in rows[0].notes
