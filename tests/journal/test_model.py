"""Closed vocabs, default-deny, id allocation."""

import pytest

from dskit.journal.base import JournalError
from dskit.journal.model import Action, JournalConfig, PathRow, next_id


def test_action_refuses_unknown_keys_and_bad_category():
    with pytest.raises(JournalError, match="unknown key"):
        Action.from_obj(
            {
                "id": "A0001",
                "category": "execute",
                "step": "x",
                "executed_at": "t",
                "extra": "no",
            }
        )
    with pytest.raises(JournalError, match="category"):
        Action(
            id="A0001",
            category="train",
            step="x",
            executed_at="t",
        )


def test_step_must_be_short():
    with pytest.raises(JournalError, match="80"):
        Action(
            id="A0001",
            category="execute",
            step="x" * 81,
            executed_at="t",
        )


def test_path_row_closed_criteria():
    row = PathRow(id="A0001", criteria="judgemental")
    assert row.to_obj() == {"id": "A0001", "criteria": "judgemental"}
    with pytest.raises(JournalError, match="criteria"):
        PathRow(id="A0001", criteria="practical")


def test_next_id_monotonic():
    assert next_id([]) == "A0001"
    assert next_id(["A0001", "A0009"]) == "A0010"


def test_journal_config_default_deny():
    cfg = JournalConfig.from_obj({"decisioning_dir": "docs/decisioning"})
    assert cfg.decisioning_dir == "docs/decisioning"
    with pytest.raises(JournalError, match="unknown key"):
        JournalConfig.from_obj({"decisioning_dir": "docs/decisioning", "foo": 1})
    with pytest.raises(JournalError, match="relative"):
        JournalConfig.from_obj({"decisioning_dir": "/abs"})
