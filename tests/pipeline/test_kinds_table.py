"""``table-file``: a keyed table from a file, with its provenance pinned.

The kind exists because a table a human cannot read cannot be approved, and the
owner approves the document before anything runs. Moving the data to a file buys
that readability — but a bare path is a promise the document cannot keep, so the
tests below are mostly about what the node REFUSES: a file that is not the one
the document was approved against, a count that disagrees, a path that only
works on one machine.
"""

import hashlib
import json

import pytest

from dskit.pipeline.kinds_table import TableFile
from dskit.pipeline.node import DEFAULT_NODE_KINDS

TABLE = {"SER-AAA": 0.07, "SER-BBB": 0.035}


@pytest.fixture
def book(tmp_path, monkeypatch):
    """A table on disk, plus params that name it correctly."""
    monkeypatch.chdir(tmp_path)
    text = json.dumps(TABLE, indent=1, ensure_ascii=False) + "\n"
    (tmp_path / "fees").mkdir()
    (tmp_path / "fees" / "book.json").write_text(text, encoding="utf-8")
    return {
        "path": "fees/book.json",
        "source": "https://example.invalid/fee-schedule",
        "retrieved": "2026-08-15",
        "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "expect": 2,
    }


def test_it_is_registered_under_its_document_name():
    assert "table-file" in DEFAULT_NODE_KINDS


def test_it_loads_the_table_and_reports_where_it_came_from(book):
    out = TableFile("fees", params=book).run(None, {})
    assert out["table"] == TABLE
    assert out["provenance"] == {
        "path": "fees/book.json",
        "source": "https://example.invalid/fee-schedule",
        "retrieved": "2026-08-15",
        "sha256": book["sha256"],
        "entries": 2,
    }


def test_provenance_survives_the_file_changing_afterwards(book):
    """The run record answers "what did this run read", not "what is on disk"."""
    out = TableFile("fees", params=book).run(None, {})
    with open("fees/book.json", "w", encoding="utf-8") as fh:
        fh.write("{}")
    assert out["provenance"]["entries"] == 2
    assert out["provenance"]["sha256"] == book["sha256"]


def test_a_file_that_drifted_from_the_declared_digest_refuses(book):
    """Two runs of one document reading two different files would report the
    same identity, and nothing downstream would say which book it priced."""
    with open("fees/book.json", "w", encoding="utf-8") as fh:
        json.dump({"SER-AAA": 0.99}, fh)
    with pytest.raises(ValueError, match="NOT the one this document was approved"):
        TableFile("fees", params=book).run(None, {})


def test_a_count_that_disagrees_refuses(book):
    """The one provenance check a reader can make without hashing anything."""
    params = dict(book, expect=99)
    with pytest.raises(ValueError, match="expects 99"):
        TableFile("fees", params=params).run(None, {})


def test_a_missing_file_names_the_working_directory_it_looked_in(book):
    params = dict(book, path="fees/nope.json")
    params["sha256"] = "0" * 64
    with pytest.raises(ValueError, match="cannot read table file"):
        TableFile("fees", params=params).run(None, {})


def test_a_non_mapping_payload_refuses(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    text = "[1, 2, 3]"
    (tmp_path / "list.json").write_text(text, encoding="utf-8")
    params = {
        "path": "list.json",
        "source": "https://example.invalid/x",
        "retrieved": "2026-08-15",
        "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
    }
    with pytest.raises(ValueError, match="a table is a mapping"):
        TableFile("fees", params=params).run(None, {})


def test_a_source_node_takes_no_inputs(book):
    node = TableFile("fees", params=book)
    assert node.validate_inputs({}) == []
    assert node.validate_inputs({"records": []})


@pytest.mark.parametrize(
    "override,match",
    [
        ({"path": None}, "path is required"),
        ({"path": ""}, "non-empty string"),
        ({"source": None}, "source is required"),
        ({"source": "   "}, "source is required"),
        ({"retrieved": None}, "retrieved is required"),
        ({"retrieved": "Aug 2026"}, "retrieved is required"),
        ({"sha256": None}, "sha256 is required"),
        ({"sha256": "deadbeef"}, "sha256 is required"),
        ({"expect": -1}, "non-negative integer"),
        ({"expect": True}, "non-negative integer"),
    ],
)
def test_provenance_is_not_optional(book, override, match):
    params = dict(book)
    params.update(override)
    params = {k: v for k, v in params.items() if v is not None}
    problems = TableFile.validate_params(params)
    assert any(match in p for p in problems), problems


def test_an_unknown_param_is_refused_by_name(book):
    problems = TableFile.validate_params(dict(book, format="csv"))
    assert any("format" in p for p in problems), problems


def test_expect_is_the_only_optional_param(book):
    params = {k: v for k, v in book.items() if k != "expect"}
    assert TableFile.validate_params(params) == []
    assert TableFile("fees", params=params).run(None, {})["table"] == TABLE
