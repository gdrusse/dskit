"""append_action pytest skip, record, promote."""

import pytest

from dskit.journal.base import JournalError
from dskit.journal.locate import init_journal
from dskit.journal.record import append_action, promote, under_pytest
from dskit.journal.store import read_actions, read_path


def _child(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
    (tmp_path / "configs").mkdir()
    return init_journal(str(tmp_path))


def test_pytest_skips_unless_opted_in(tmp_path, monkeypatch):
    root = _child(tmp_path)
    monkeypatch.delenv("DSKIT_JOURNAL_TESTS", raising=False)
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "tests/journal/test_record.py::test")
    assert under_pytest() is True
    assert append_action("execute", "x", start=root.child_root) is None
    assert read_actions(root) == []


def test_append_and_promote(tmp_path, monkeypatch):
    monkeypatch.setenv("DSKIT_JOURNAL_TESTS", "1")
    root = _child(tmp_path)
    action = append_action(
        "execute",
        "hl-scan",
        inputs="configs/run-hl-scan.json",
        outputs="pipeline_runs/x",
        db_location="pipeline_runs/x",
        start=root.child_root,
    )
    assert action.id == "A0001"
    assert read_actions(root)[0].step == "hl-scan"
    promote("A0001", "empirical", start=root.child_root)
    assert read_path(root)[0].criteria == "empirical"
    with pytest.raises(JournalError, match="already on the path"):
        promote("A0001", "judgemental", start=root.child_root)
    with pytest.raises(JournalError, match="no such action"):
        promote("A9999", "n/a", start=root.child_root)


def test_uninitialized_child_refuses_record(tmp_path, monkeypatch):
    monkeypatch.setenv("DSKIT_JOURNAL_TESTS", "1")
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
    (tmp_path / "configs").mkdir()
    with pytest.raises(JournalError, match="journal init"):
        append_action("execute", "x", start=str(tmp_path))
