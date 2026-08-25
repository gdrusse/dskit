"""``table-write`` — the write half of the keyed-table pair.

The properties under test are the ones that make a writer safe to point at
an authoritative store: it round-trips through ``table-file``, it refuses to
clobber, it writes atomically, and it says what it wrote.
"""

import json
import os
import pathlib

import pytest

from dskit.pipeline.base import ConfigError
from dskit.pipeline.kinds_table import TableFile, TableWrite, register
from dskit.pipeline.node import NodeContext, NodeKindRegistry

TABLE = {"SER-AAA": 0, "SER-BBB": 1, "SER-CCC": 2}


def ctx(tmp_path, key="sink"):
    run = pathlib.Path(tmp_path) / "run"
    (run / "artifacts" / key).mkdir(parents=True, exist_ok=True)
    return NodeContext(name="t", asof="2026-08-16", run_dir=str(run))


def write_node(tmp_path, **params):
    base = {
        "path": str(pathlib.Path(tmp_path) / "vocab.json"),
        "source": "the frozen market vocab, built by the panels node",
    }
    return TableWrite("sink", {**base, **params})


# ---------------------------------------------------------------------------
# the round trip — the reason this lives beside TableFile
# ---------------------------------------------------------------------------


def test_what_is_written_reads_back_through_table_file(tmp_path):
    """The pair's contract: ``provenance`` is a superset of the params
    ``table-file`` requires, so pinning a produced table is copying values
    rather than re-deriving a digest by hand."""
    out = write_node(tmp_path).run(ctx(tmp_path), {"table": TABLE})
    prov = out["provenance"]

    reader = TableFile(
        "book",
        {
            "path": prov["path"],
            "source": prov["source"],
            "retrieved": prov["retrieved"],
            "sha256": prov["sha256"],
            "expect": prov["entries"],
        },
    )
    assert reader.run(ctx(tmp_path, "book"), {})["table"] == TABLE


def test_the_digest_is_of_the_table_not_of_dict_order(tmp_path):
    """Two runs that computed the same table must produce the same digest,
    or the pin is worthless."""
    a = write_node(tmp_path, path=str(tmp_path / "a.json")).run(
        ctx(tmp_path), {"table": dict(reversed(list(TABLE.items())))}
    )
    b = write_node(tmp_path, path=str(tmp_path / "b.json")).run(
        ctx(tmp_path), {"table": TABLE}
    )
    assert a["provenance"]["sha256"] == b["provenance"]["sha256"]


# ---------------------------------------------------------------------------
# refusing to clobber (I-233)
# ---------------------------------------------------------------------------


def test_an_existing_target_is_refused_by_default(tmp_path):
    node = write_node(tmp_path)
    node.run(ctx(tmp_path), {"table": TABLE})
    with pytest.raises(FileExistsError, match="does not declare overwrite"):
        write_node(tmp_path).run(ctx(tmp_path), {"table": {"X": 9}})
    # and the original survives untouched
    assert json.loads(pathlib.Path(node.params["path"]).read_text()) == TABLE


def test_overwrite_must_be_declared_to_replace(tmp_path):
    write_node(tmp_path).run(ctx(tmp_path), {"table": TABLE})
    write_node(tmp_path, overwrite=True).run(ctx(tmp_path), {"table": {"X": 9}})
    assert json.loads((tmp_path / "vocab.json").read_text()) == {"X": 9}


def test_a_missing_parent_directory_is_refused_not_created(tmp_path):
    """An implicitly-created tree is how a mistyped path becomes a second
    store nobody knows about."""
    target = tmp_path / "nope" / "deeper" / "vocab.json"
    with pytest.raises(NotADirectoryError, match="does not exist"):
        write_node(tmp_path, path=str(target)).run(ctx(tmp_path), {"table": TABLE})
    assert not target.parent.exists()


# ---------------------------------------------------------------------------
# atomicity + evidence
# ---------------------------------------------------------------------------


def test_a_failed_write_leaves_no_temp_file_and_no_target(tmp_path, monkeypatch):
    """Interrupted mid-write, the target must not exist and no ``.tmp-``
    fragment may be left behind to be mistaken for data."""
    target = tmp_path / "vocab.json"
    real_replace = os.replace

    def boom(src, dst):
        raise KeyboardInterrupt("interrupted between write and rename")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(KeyboardInterrupt):
        write_node(tmp_path).run(ctx(tmp_path), {"table": TABLE})
    monkeypatch.setattr(os, "replace", real_replace)

    assert not target.exists()
    assert not [p for p in tmp_path.iterdir() if ".tmp-" in p.name]


def test_an_interrupted_write_leaves_the_old_file_intact(tmp_path, monkeypatch):
    """Interrupted while the bytes are still going down (fsync, before
    the rename), the PRE-EXISTING target must survive byte-identical:
    the new content lands beside it, never through it. A non-atomic
    ``tmp = path`` regression truncates the old file at open and dies
    here — the interrupt-at-replace test above cannot see that mutant."""
    target = tmp_path / "vocab.json"
    write_node(tmp_path).run(ctx(tmp_path), {"table": TABLE})
    before = target.read_bytes()

    def boom(fd):
        raise KeyboardInterrupt("interrupted mid-write, before rename")

    monkeypatch.setattr(os, "fsync", boom)
    with pytest.raises(KeyboardInterrupt):
        write_node(tmp_path, overwrite=True).run(ctx(tmp_path), {"table": {"X": 9}})
    monkeypatch.undo()

    assert target.read_bytes() == before
    assert not [p for p in tmp_path.iterdir() if ".tmp-" in p.name]


def test_the_provenance_says_the_path_and_the_byte_count(tmp_path):
    out = write_node(tmp_path).run(ctx(tmp_path), {"table": TABLE})
    prov = out["provenance"]
    assert prov["entries"] == 3
    assert prov["bytes"] == os.path.getsize(prov["path"]) > 0
    assert prov["retrieved"] == "2026-08-16"
    assert out["path"] == prov["path"]


def test_a_tilde_path_is_expanded_and_provenance_reports_the_real_path(tmp_path, monkeypatch):
    """None of this repo's readers expand ``~``, so emitting the tilde form
    would hand ``table-file`` a path under a directory literally named
    ``~`` — which is exactly how the first real document failed."""
    monkeypatch.setenv("HOME", str(tmp_path))
    out = write_node(tmp_path, path="~/vocab.json").run(ctx(tmp_path), {"table": TABLE})
    assert out["provenance"]["path"] == str(tmp_path / "vocab.json")
    assert (tmp_path / "vocab.json").is_file()
    assert not (tmp_path / "~").exists()


def test_a_wrong_shaped_table_never_reaches_disk(tmp_path):
    with pytest.raises(ValueError, match="must not reach disk"):
        write_node(tmp_path, expect=99).run(ctx(tmp_path), {"table": TABLE})
    assert not (tmp_path / "vocab.json").exists()


# ---------------------------------------------------------------------------
# plan-time
# ---------------------------------------------------------------------------


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
def test_knobs_are_refused_by_name_at_plan(params, needle):
    problems = TableWrite.validate_params(params)
    assert any(needle in p for p in problems), problems


def test_a_non_mapping_input_is_refused_by_name(tmp_path):
    with pytest.raises(ConfigError):
        TableWrite("sink", {"path": "p"})  # source missing -> refused at construction
    node = write_node(tmp_path)
    assert node.validate_inputs({"table": [1, 2, 3]})


def test_the_kind_registers_beside_its_reader():
    registry = NodeKindRegistry()
    register(registry)
    assert "table-write" in registry and "table-file" in registry
    register(registry)  # idempotent, never shadowing
    assert registry.get("table-write")[0] is TableWrite
