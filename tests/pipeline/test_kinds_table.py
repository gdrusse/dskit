"""``table-file``: a keyed table from a file, with its provenance pinned.

The kind exists because a table a human cannot read cannot be approved, and the
owner approves the document before anything runs. Moving the data to a file buys
that readability — but a bare path is a promise the document cannot keep, so the
tests below are mostly about what the node REFUSES: a file that is not the one
the document was approved against, a count that disagrees, a path that only
works on one machine.

The second half is ``records-write`` (ADR-0085), the record-stream sibling of
``table-write``: what it writes must be deterministic bytes a later document can
pin by digest, and what it refuses — a NaN, a frozen envelope, a value JSON has
no form for — must be refused BY NAME before anything reaches disk.
"""

import hashlib
import json
import pathlib

import pytest

from dskit.pipeline.kinds_table import (
    FileWrite,
    RecordsWrite,
    TableFile,
    TableWrite,
    register,
)
from dskit.pipeline.node import DEFAULT_NODE_KINDS, NodeContext, NodeKindRegistry
from dskit.pipeline.records import MarketRecord

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


# ---------------------------------------------------------------------------
# records-write — the record-stream sibling of table-write (ADR-0085)
# ---------------------------------------------------------------------------

ROWS = [
    {"instrument": "AAA", "contract": "AAA-1", "asof_ms": 10, "mid": 0.4},
    {"instrument": "AAA", "contract": "AAA-2", "asof_ms": 11, "mid": 0.6},
    {"instrument": "BBB", "contract": "BBB-1", "asof_ms": 12, "mid": 0.5},
]


def run_ctx(tmp_path):
    return NodeContext(name="t", asof="2026-09-04", run_dir=str(tmp_path / "run"))


def records_node(tmp_path, **params):
    base = {
        "path": str(tmp_path / "rows.jsonl"),
        "source": "the rows this run scored, for the next document to pin",
    }
    return RecordsWrite("sink", {**base, **params})


def test_records_write_is_registered_beside_the_table_pair():
    assert "records-write" in DEFAULT_NODE_KINDS
    reg = register(NodeKindRegistry())
    assert reg.get("records-write") == (RecordsWrite, False)
    register(reg)  # idempotent, never shadowing
    assert reg.get("records-write")[0] is RecordsWrite
    assert {"table-file", "table-write", "records-write"} <= set(reg.kinds())


def test_records_write_lands_one_canonical_json_object_per_line(tmp_path):
    out = records_node(tmp_path).run(run_ctx(tmp_path), {"records": ROWS})
    text = pathlib.Path(out["path"]).read_text(encoding="utf-8")
    lines = text.splitlines()
    assert text.endswith("\n") and len(lines) == 3
    assert [json.loads(line) for line in lines] == ROWS
    # the canonical spelling: sorted keys, compact separators, ASCII
    assert lines[0] == '{"asof_ms":10,"contract":"AAA-1","instrument":"AAA","mid":0.4}'


def test_the_digest_is_of_the_records_not_of_key_order(tmp_path):
    """Two runs that scored the same rows must write byte-identical files,
    or the digest is a timestamp rather than a pin."""
    shuffled = [dict(reversed(list(row.items()))) for row in ROWS]
    a = records_node(tmp_path, path=str(tmp_path / "a.jsonl")).run(
        run_ctx(tmp_path), {"records": shuffled}
    )
    b = records_node(tmp_path, path=str(tmp_path / "b.jsonl")).run(
        run_ctx(tmp_path), {"records": tuple(ROWS)}
    )
    assert a["metrics"]["sha256"] == b["metrics"]["sha256"]
    assert pathlib.Path(a["path"]).read_bytes() == pathlib.Path(b["path"]).read_bytes()


def test_metrics_and_provenance_agree_with_the_bytes_on_disk(tmp_path):
    out = records_node(tmp_path, expect=3).run(run_ctx(tmp_path), {"records": ROWS})
    raw = pathlib.Path(out["path"]).read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    assert out["metrics"] == {"rows": 3, "bytes": len(raw), "sha256": digest}
    assert out["provenance"] == {
        "path": out["path"],
        "source": "the rows this run scored, for the next document to pin",
        "retrieved": "2026-09-04",
        "sha256": digest,
        "rows": 3,
        "bytes": len(raw),
    }


def test_an_empty_stream_writes_an_empty_file_when_that_is_what_was_expected(tmp_path):
    out = records_node(tmp_path, expect=0).run(run_ctx(tmp_path), {"records": []})
    assert pathlib.Path(out["path"]).read_bytes() == b""
    assert out["metrics"]["rows"] == 0 and out["metrics"]["bytes"] == 0


@pytest.mark.parametrize(
    ("bad", "spelling"),
    [(float("nan"), "NaN"), (float("inf"), "Infinity"), (-float("inf"), "-Infinity")],
)
def test_a_value_json_cannot_carry_is_refused_by_row_and_field(tmp_path, bad, spelling):
    rows = [dict(ROWS[0]), {**ROWS[1], "mid": bad}]
    with pytest.raises(ValueError, match=f"row 1 field 'mid' is {spelling}"):
        records_node(tmp_path).run(run_ctx(tmp_path), {"records": rows})
    assert not (tmp_path / "rows.jsonl").exists()


def test_a_nested_non_finite_value_is_named_by_its_path(tmp_path):
    rows = [{"k": 1, "extra": {"vals": [1.0, float("nan")]}}]
    with pytest.raises(ValueError, match=r"row 0 field 'extra\.vals\[1\]' is NaN"):
        records_node(tmp_path).run(run_ctx(tmp_path), {"records": rows})


def test_a_frozen_envelope_is_refused_by_name(tmp_path):
    row = MarketRecord(
        venue="v", instrument="AAA", contract="AAA-1", asof_ms=1,
        usable=True, reason="ok", mid=0.5, group=None,
    )
    with pytest.raises(ValueError, match="row 0 is a MarketRecord, not a mapping"):
        records_node(tmp_path).run(run_ctx(tmp_path), {"records": [row]})
    assert not (tmp_path / "rows.jsonl").exists()


def test_a_value_json_has_no_form_for_is_refused_by_row(tmp_path):
    rows = [dict(ROWS[0]), {"k": {1, 2}}]
    with pytest.raises(ValueError, match="row 1 holds a value JSON has no form for"):
        records_node(tmp_path).run(run_ctx(tmp_path), {"records": rows})
    assert not (tmp_path / "rows.jsonl").exists()


def test_records_write_refuses_to_clobber_and_never_creates_a_tree(tmp_path):
    node = records_node(tmp_path)
    node.run(run_ctx(tmp_path), {"records": ROWS})
    with pytest.raises(FileExistsError, match="does not declare overwrite"):
        records_node(tmp_path).run(run_ctx(tmp_path), {"records": ROWS[:1]})
    target = pathlib.Path(node.params["path"])
    assert len(target.read_text(encoding="utf-8").splitlines()) == 3
    records_node(tmp_path, overwrite=True).run(run_ctx(tmp_path), {"records": ROWS[:1]})
    assert len(target.read_text(encoding="utf-8").splitlines()) == 1
    deep = tmp_path / "nope" / "rows.jsonl"
    with pytest.raises(NotADirectoryError, match="does not exist"):
        records_node(tmp_path, path=str(deep)).run(run_ctx(tmp_path), {"records": ROWS})
    assert not deep.parent.exists()


def test_a_wrong_row_count_never_reaches_disk(tmp_path):
    with pytest.raises(ValueError, match="must not reach disk"):
        records_node(tmp_path, expect=2).run(run_ctx(tmp_path), {"records": ROWS})
    assert not (tmp_path / "rows.jsonl").exists()


def test_a_tilde_path_is_expanded_for_the_stream_writer_too(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    out = records_node(tmp_path, path="~/rows.jsonl").run(
        run_ctx(tmp_path), {"records": ROWS}
    )
    assert out["provenance"]["path"] == str(tmp_path / "rows.jsonl")
    assert not (tmp_path / "~").exists()


def test_validate_inputs_wants_a_materialized_sequence_of_rows(tmp_path):
    node = records_node(tmp_path)
    assert node.validate_inputs({"records": ROWS}) == []
    assert node.validate_inputs({"records": tuple(ROWS)}) == []
    for bad in ({"a": 1}, "text", None, 5):
        assert node.validate_inputs({"records": bad}), bad
    problems = node.validate_inputs({"records": (r for r in ROWS)})
    assert problems and "one-shot" in problems[0]


@pytest.mark.parametrize(
    ("params", "needle"),
    [
        ({"source": "s"}, "path is required"),
        ({"path": "p"}, "source is required"),
        ({"path": "p", "source": "s", "overwrite": "yes"}, "overwrite must be a bool"),
        ({"path": "p", "source": "s", "expect": -1}, "expect must be"),
        ({"path": "p", "source": "s", "nope": 1}, "unknown param"),
    ],
)
def test_records_write_knobs_are_refused_by_name_at_plan(params, needle):
    problems = RecordsWrite.validate_params(params)
    assert any(needle in p for p in problems), problems


def test_both_writers_share_one_write_discipline():
    """No-clobber, parent-must-exist, atomic replace, provenance: ONE code
    path. A second copy is where the two writers would drift apart."""
    assert issubclass(TableWrite, FileWrite) and issubclass(RecordsWrite, FileWrite)
    assert TableWrite.run is FileWrite.run is RecordsWrite.run
    assert TableWrite.validate_params.__func__ is FileWrite.validate_params.__func__
    assert TableWrite._PARAMS == RecordsWrite._PARAMS == FileWrite._PARAMS


def test_table_write_now_names_a_value_json_cannot_carry(tmp_path):
    """The shared rule reaches the mapping writer too: a NaN in a table is
    named by its entry, not surfaced as the codec's message."""
    node = TableWrite(
        "sink", {"path": str(tmp_path / "t.json"), "source": "a table with a hole"}
    )
    with pytest.raises(ValueError, match="field 'AAA' is NaN"):
        node.run(run_ctx(tmp_path), {"table": {"AAA": float("nan"), "BBB": 1.0}})
    assert not (tmp_path / "t.json").exists()
