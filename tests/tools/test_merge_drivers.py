"""ADR-0107 merge drivers — union validation, keep-both, render no-op.

The drivers run as git low-level merge commands (``<script> %O %A %B``);
these tests drive the same entry points in-process, pin the
``.gitattributes`` ↔ ``install.sh`` agreement, and pin the driver's
ledger column names against ``dskit.journal`` (the one owner).
"""

import importlib.util
import os
import shutil

REPO_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
MERGE_DIR = os.path.join(REPO_ROOT, "tools", "merge")
SKELETON = os.path.join(REPO_ROOT, "children", "_skeleton")

ACTION_HEADER = (
    "id,category,step,executed_at,inputs,outputs,db_location,notes\n"
)


def _load(name):
    """Import one driver script by path (they are not a package)."""
    spec = importlib.util.spec_from_file_location(
        name, os.path.join(MERGE_DIR, name + ".py")
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _write(path, header, rows):
    """Write a ledger CSV; ``rows`` are full text lines."""
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(header + "".join(line + "\n" for line in rows))


def _read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


# --- journal_union -----------------------------------------------------


def test_union_merges_two_appends(tmp_path):
    union = _load("journal_union")
    base = tmp_path / "base.csv"
    ours = tmp_path / "ours.csv"
    theirs = tmp_path / "theirs.csv"
    _write(
        base,
        ACTION_HEADER,
        ["A0001,research,looked,2026-09-07T10:00:00Z,x,y,z,baseline"],
    )
    _write(
        ours,
        ACTION_HEADER,
        [
            "A0001,research,looked,2026-09-07T10:00:00Z,x,y,z,baseline",
            "A0002,execute,ran,2026-09-07T12:00:00Z,x,y,z,our-append",
        ],
    )
    _write(
        theirs,
        ACTION_HEADER,
        [
            "A0001,research,looked,2026-09-07T10:00:00Z,x,y,z,baseline",
            "A0003,execute,ran,2026-09-07T11:00:00Z,x,y,z,their-append",
        ],
    )
    assert union.main(["x", str(base), str(ours), str(theirs)]) == 0
    lines = _read(ours).strip().split("\n")
    ids = [line.split(",")[0] for line in lines]
    assert ids == ["id", "A0001", "A0003", "A0002"]


def test_union_keeps_file_order_without_timestamp(tmp_path):
    union = _load("journal_union")
    header = "id,criteria\n"
    base = tmp_path / "base.csv"
    ours = tmp_path / "ours.csv"
    theirs = tmp_path / "theirs.csv"
    _write(base, header, ["A0001,first"])
    _write(ours, header, ["A0001,first", "A0002,our-append"])
    _write(theirs, header, ["A0001,first", "A0003,their-append"])
    assert union.main(["x", str(base), str(ours), str(theirs)]) == 0
    ids = [line.split(",")[0] for line in _read(ours).strip().split("\n")]
    assert ids == ["id", "A0001", "A0002", "A0003"]


def test_union_refuses_row_edit(tmp_path):
    union = _load("journal_union")
    base = tmp_path / "base.csv"
    ours = tmp_path / "ours.csv"
    theirs = tmp_path / "theirs.csv"
    _write(base, ACTION_HEADER, ["A0001,research,old,2026-09-07T10:00:00Z,x,y,z,n"])
    _write(ours, ACTION_HEADER, ["A0001,research,EDITED,2026-09-07T10:00:00Z,x,y,z,n"])
    _write(theirs, ACTION_HEADER, ["A0001,research,old,2026-09-07T10:00:00Z,x,y,z,n"])
    before = _read(ours)
    assert union.main(["x", str(base), str(ours), str(theirs)]) == 1
    assert _read(ours) == before


def test_union_refuses_deletion(tmp_path):
    union = _load("journal_union")
    base = tmp_path / "base.csv"
    ours = tmp_path / "ours.csv"
    theirs = tmp_path / "theirs.csv"
    _write(base, ACTION_HEADER, ["A0001,r,s,t,x,y,z,n", "A0002,r,s,t,x,y,z,n"])
    _write(ours, ACTION_HEADER, ["A0001,r,s,t,x,y,z,n"])
    _write(theirs, ACTION_HEADER, ["A0001,r,s,t,x,y,z,n", "A0002,r,s,t,x,y,z,n"])
    assert union.main(["x", str(base), str(ours), str(theirs)]) == 1


def test_union_refuses_header_drift(tmp_path):
    union = _load("journal_union")
    base = tmp_path / "base.csv"
    ours = tmp_path / "ours.csv"
    theirs = tmp_path / "theirs.csv"
    _write(base, ACTION_HEADER, [])
    _write(ours, ACTION_HEADER, [])
    _write(theirs, "id,category,step\n", [])
    assert union.main(["x", str(base), str(ours), str(theirs)]) == 1


def test_union_refuses_duplicate_ids_within_a_side(tmp_path):
    union = _load("journal_union")
    base = tmp_path / "base.csv"
    ours = tmp_path / "ours.csv"
    theirs = tmp_path / "theirs.csv"
    _write(base, ACTION_HEADER, [])
    _write(ours, ACTION_HEADER, ["A0001,r,s,t,x,y,z,n", "A0001,r,s2,t,x,y,z,n"])
    _write(theirs, ACTION_HEADER, [])
    assert union.main(["x", str(base), str(ours), str(theirs)]) == 1


def test_union_refuses_id_collision_on_both_sides(tmp_path):
    union = _load("journal_union")
    base = tmp_path / "base.csv"
    ours = tmp_path / "ours.csv"
    theirs = tmp_path / "theirs.csv"
    _write(base, ACTION_HEADER, [])
    _write(ours, ACTION_HEADER, ["A0009,research,ours,2026-09-07T10:00:00Z,x,y,z,n"])
    _write(theirs, ACTION_HEADER, ["A0009,research,theirs,2026-09-07T10:00:00Z,x,y,z,n"])
    assert union.main(["x", str(base), str(ours), str(theirs)]) == 1


def test_union_merges_add_add(tmp_path):
    """No base at all: two first-writers still union."""
    union = _load("journal_union")
    base = tmp_path / "absent.csv"
    ours = tmp_path / "ours.csv"
    theirs = tmp_path / "theirs.csv"
    _write(ours, ACTION_HEADER, ["A0001,r,s,2026-09-07T10:00:00Z,x,y,z,n"])
    _write(theirs, ACTION_HEADER, ["A0002,r,s,2026-09-07T09:00:00Z,x,y,z,n"])
    assert union.main(["x", str(base), str(ours), str(theirs)]) == 0
    ids = [line.split(",")[0] for line in _read(ours).strip().split("\n")]
    assert ids == ["id", "A0002", "A0001"]


# --- keep_both ---------------------------------------------------------


def test_keep_both_concatenates_with_a_visible_marker(tmp_path):
    keep = _load("keep_both")
    ours = tmp_path / "ours.md"
    theirs = tmp_path / "theirs.md"
    ours.write_text("# Re-entry\n\nour state\n")
    theirs.write_text("# Re-entry\n\ntheir state\n")
    assert keep.main(["x", "unused", str(ours), str(theirs)]) == 0
    merged = _read(ours)
    assert "our state" in merged
    assert "their state" in merged
    assert keep.MARKER in merged
    assert merged.index("our state") < merged.index("their state")


def test_keep_both_survives_a_missing_side(tmp_path):
    keep = _load("keep_both")
    ours = tmp_path / "ours.md"
    theirs = tmp_path / "theirs.md"
    ours.write_text("only side\n")
    assert keep.main(["x", "unused", str(ours), str(theirs)]) == 0
    assert _read(ours) == "only side\n"


# --- render_journal ----------------------------------------------------


def test_render_driver_takes_ours_untouched(tmp_path):
    render = _load("render_journal")
    ours = tmp_path / "README.md"
    before = "generated content\n"
    ours.write_text(before)
    assert render.main(["x", "unused", str(ours), "unused"]) == 0
    assert _read(ours) == before


# --- render_all --------------------------------------------------------


def _scratch_child(root, name, readme="stale\n"):
    child = root / "children" / name
    decisioning = child / "docs" / "decisioning"
    decisioning.mkdir(parents=True)
    shutil.copy(
        os.path.join(SKELETON, "journal.json"), child / "journal.json"
    )
    for csv_name in ("actions.csv", "path.csv"):
        shutil.copy(
            os.path.join(SKELETON, "docs", "decisioning", csv_name),
            decisioning / csv_name,
        )
    (decisioning / "README.md").write_text(readme)
    return child


def test_render_all_heals_every_marked_child(tmp_path):
    render_all = _load("render_all")
    _scratch_child(tmp_path, "alpha", readme="stale\n")
    _scratch_child(tmp_path, "beta", readme="stale\n")
    (tmp_path / "children" / "unmarked").mkdir()
    assert render_all.main(["x", str(tmp_path)]) == 0
    from dskit.journal.locate import load_root
    from dskit.journal.render import render_text
    from dskit.journal.store import read_actions, read_path

    for name in ("alpha", "beta"):
        root = load_root(str(tmp_path / "children" / name))
        expected = render_text(read_actions(root), read_path(root))
        if not expected.endswith("\n"):
            expected += "\n"
        assert _read(root.readme) == expected


def test_render_all_never_fails_the_merge(tmp_path):
    render_all = _load("render_all")
    child = _scratch_child(tmp_path, "broken", readme="stale\n")
    (child / "docs" / "decisioning" / "actions.csv").write_text(
        "not,the,pledged,header\n"
    )
    assert render_all.main(["x", str(tmp_path)]) == 0


# --- the pins ----------------------------------------------------------


def test_gitattributes_agrees_with_install_sh():
    """The attribute names and the configured drivers are ONE agreement."""
    with open(os.path.join(REPO_ROOT, ".gitattributes"), encoding="utf-8") as fh:
        attrs = fh.read()
    with open(os.path.join(MERGE_DIR, "install.sh"), encoding="utf-8") as fh:
        install = fh.read()
    for driver in ("journal-union", "render-journal", "keep-both"):
        assert f"merge={driver}" in attrs, driver
        assert f"git config merge.{driver}.driver" in install, driver


def test_driver_columns_agree_with_the_journal():
    """The driver restates the ledger's column names; pin them to the owner."""
    from dskit.journal.base import ACTION_FIELDS, PATH_FIELDS

    union = _load("journal_union")
    for fields in (ACTION_FIELDS, PATH_FIELDS):
        assert union.ID_FIELD in fields
    assert union.TIMESTAMP_FIELD in ACTION_FIELDS
    assert union.TIMESTAMP_FIELD not in PATH_FIELDS
