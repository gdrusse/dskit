"""Walk-up locate, init, uninitialized-child refusal."""

import json

import pytest

from dskit.journal.base import JournalError, MARKER
from dskit.journal.locate import find_journal, init_journal


def _shape_child(path):
    (path / "pyproject.toml").write_text("[project]\nname='x'\n")
    (path / "configs").mkdir()


def test_init_then_find(tmp_path):
    _shape_child(tmp_path)
    root = init_journal(str(tmp_path))
    assert (tmp_path / MARKER).is_file()
    assert (tmp_path / "docs" / "decisioning" / "actions.csv").is_file()
    assert (tmp_path / "docs" / "research" / ".gitkeep").is_file()
    found = find_journal(start=str(tmp_path / "configs"))
    assert found.child_root == root.child_root


def test_uninitialized_child_refuses(tmp_path):
    _shape_child(tmp_path)
    with pytest.raises(JournalError, match="journal init"):
        find_journal(start=str(tmp_path))


def test_not_a_child_is_none(tmp_path):
    assert find_journal(start=str(tmp_path)) is None


def test_override_must_have_marker(tmp_path, monkeypatch):
    monkeypatch.setenv("DSKIT_JOURNAL_ROOT", str(tmp_path))
    with pytest.raises(JournalError, match="DSKIT_JOURNAL_ROOT"):
        find_journal(start=str(tmp_path))


def test_override_wins(tmp_path, monkeypatch):
    _shape_child(tmp_path)
    init_journal(str(tmp_path))
    other = tmp_path / "other"
    other.mkdir()
    monkeypatch.setenv("DSKIT_JOURNAL_ROOT", str(tmp_path))
    found = find_journal(start=str(other))
    assert found.child_root == str(tmp_path.resolve())


def test_second_init_refuses(tmp_path):
    _shape_child(tmp_path)
    init_journal(str(tmp_path))
    with pytest.raises(JournalError, match="already initialized"):
        init_journal(str(tmp_path))


def test_marker_is_default_deny(tmp_path):
    _shape_child(tmp_path)
    init_journal(str(tmp_path))
    payload = json.loads((tmp_path / MARKER).read_text())
    payload["extra"] = True
    (tmp_path / MARKER).write_text(json.dumps(payload))
    with pytest.raises(JournalError, match="unknown key"):
        find_journal(start=str(tmp_path))
