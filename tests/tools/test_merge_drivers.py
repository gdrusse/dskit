"""ADR-0109 merge drivers — union validation, keep-both, render no-op.

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
PATH_HEADER = "id,label,purpose,relevant_files,locked,current_work,criteria\n"


def _has_conflict_markers(text):
    """git-style markers, all three, in order."""
    return (
        "<<<<<<<" in text
        and "=======" in text
        and ">>>>>>>" in text
        and text.index("<<<<<<<") < text.index("=======") < text.index(">>>>>>>")
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
    assert ids == ["id", "A0001", "A0002", "A0003"]


def test_union_keeps_file_order_without_timestamp(tmp_path):
    union = _load("journal_union")
    header = PATH_HEADER
    base = tmp_path / "base.csv"
    ours = tmp_path / "ours.csv"
    theirs = tmp_path / "theirs.csv"
    _write(base, header, ["A0001,l,p,f,Y,,empirical"])
    _write(ours, header, ["A0001,l,p,f,Y,,empirical", "A0002,our,p,f,N,,empirical"])
    _write(theirs, header, ["A0001,l,p,f,Y,,empirical", "A0003,their,p,f,N,,empirical"])
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
    assert union.main(["x", str(base), str(ours), str(theirs)]) == 1
    after = _read(ours)
    assert _has_conflict_markers(after), after
    assert "EDITED" in after and "A0001,research,old" in after


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
    assert ids == ["id", "A0001", "A0002"]


def test_union_refuses_a_header_matching_neither_schema(tmp_path):
    """An identically-drifted header on BOTH sides used to merge cleanly."""
    union = _load("journal_union")
    base, ours, theirs = (tmp_path / n for n in ("b.csv", "o.csv", "t.csv"))
    drifted = "id,category,step,executed_at,inputs,outputs,db_location,notes,extra\n"
    _write(base, drifted, [])
    _write(ours, drifted, ["A0001,r,s,2026-09-07T10:00:00Z,x,y,z,n,e"])
    _write(theirs, drifted, ["A0002,r,s,2026-09-07T11:00:00Z,x,y,z,n,e"])
    assert union.main(["x", str(base), str(ours), str(theirs)]) == 1


def test_union_never_reorders_the_base_tape(tmp_path):
    """The ledger is append-only; executed_at must NOT sort it.

    The real intraday_equities ledger is not timestamp-sorted, and
    render.py builds its operator table from the TAIL of file order — so
    a sorting union would silently change what the README shows.
    """
    union = _load("journal_union")
    base, ours, theirs = (tmp_path / n for n in ("b.csv", "o.csv", "t.csv"))
    # Deliberately out of timestamp order, as the real ledger is.
    tape = [
        "A0001,r,s,2026-09-07T23:00:00Z,x,y,z,late-but-first",
        "A0002,r,s,2026-09-07T09:00:00Z,x,y,z,early-but-second",
    ]
    _write(base, ACTION_HEADER, tape)
    _write(ours, ACTION_HEADER, tape + ["A0003,r,s,2026-09-07T10:00:00Z,x,y,z,ours"])
    _write(theirs, ACTION_HEADER, tape + ["A0004,r,s,2026-09-07T08:00:00Z,x,y,z,theirs"])
    assert union.main(["x", str(base), str(ours), str(theirs)]) == 0
    ids = [line.split(",")[0] for line in _read(ours).strip().split("\n")]
    assert ids == ["id", "A0001", "A0002", "A0003", "A0004"]


REFUSAL_CASES = {
    "id_collision": (
        [],
        ["A0009,r,OURS-ROW,2026-09-07T10:00:00Z,x,y,z,n"],
        ["A0009,r,THEIRS-ROW,2026-09-07T10:00:00Z,x,y,z,n"],
    ),
    "row_edited": (
        ["A0001,r,BASE-ROW,2026-09-07T10:00:00Z,x,y,z,n"],
        ["A0001,r,OURS-ROW,2026-09-07T10:00:00Z,x,y,z,n"],
        ["A0001,r,THEIRS-ROW,2026-09-07T10:00:00Z,x,y,z,n"],
    ),
    "row_deleted": (
        ["A0001,r,s,2026-09-07T10:00:00Z,x,y,z,n",
         "A0002,r,THEIRS-ROW,2026-09-07T11:00:00Z,x,y,z,n"],
        ["A0001,r,s,2026-09-07T10:00:00Z,x,y,z,n"],
        ["A0001,r,s,2026-09-07T10:00:00Z,x,y,z,n",
         "A0002,r,THEIRS-ROW,2026-09-07T11:00:00Z,x,y,z,n"],
    ),
    "duplicate_within_a_side": (
        [],
        ["A0001,r,OURS-ROW,2026-09-07T10:00:00Z,x,y,z,n",
         "A0001,r,OURS-ROW-2,2026-09-07T10:00:00Z,x,y,z,n"],
        ["A0002,r,THEIRS-ROW,2026-09-07T11:00:00Z,x,y,z,n"],
    ),
}


def test_every_refusal_writes_a_conflict_holding_both_sides(tmp_path):
    """THE data-loss regression (review finding 1).

    git does NOT add conflict markers when a merge driver exits nonzero —
    it leaves whatever is in %A, which is OUR side alone. A resolver then
    sees a clean CSV with the other lane's rows simply absent, commits,
    and they are gone. Every refusal must therefore write the conflict
    itself, with BOTH sides visible.
    """
    union = _load("journal_union")
    for name, (base_rows, our_rows, their_rows) in REFUSAL_CASES.items():
        d = tmp_path / name
        d.mkdir()
        base, ours, theirs = (d / n for n in ("b.csv", "o.csv", "t.csv"))
        _write(base, ACTION_HEADER, base_rows)
        _write(ours, ACTION_HEADER, our_rows)
        _write(theirs, ACTION_HEADER, their_rows)
        assert union.main(["x", str(base), str(ours), str(theirs)]) == 1, name
        after = _read(ours)
        assert _has_conflict_markers(after), f"{name}: no markers\n{after}"
        assert "THEIRS-ROW" in after, f"{name}: THEIR side was LOST\n{after}"


def test_refusal_on_header_drift_still_holds_both_sides(tmp_path):
    """The unparseable case falls back to whole-file markers."""
    union = _load("journal_union")
    base, ours, theirs = (tmp_path / n for n in ("b.csv", "o.csv", "t.csv"))
    _write(base, ACTION_HEADER, [])
    _write(ours, ACTION_HEADER, ["A0001,r,OURS-ROW,2026-09-07T10:00:00Z,x,y,z,n"])
    _write(theirs, "id,category,step\n", ["A0002,r,THEIRS-ROW"])
    assert union.main(["x", str(base), str(ours), str(theirs)]) == 1
    after = _read(ours)
    assert _has_conflict_markers(after), after
    assert "OURS-ROW" in after and "THEIRS-ROW" in after, after


def test_a_conflicted_ledger_is_never_silently_parseable(tmp_path):
    """A resolver must not be able to `git add` a refusal by accident.

    The whole defect was that the refused file PARSED as a clean ledger.
    Reading it back with the driver's own reader must now fail.
    """
    union = _load("journal_union")
    base, ours, theirs = (tmp_path / n for n in ("b.csv", "o.csv", "t.csv"))
    _write(base, ACTION_HEADER, [])
    _write(ours, ACTION_HEADER, ["A0009,r,OURS-ROW,2026-09-07T10:00:00Z,x,y,z,n"])
    _write(theirs, ACTION_HEADER, ["A0009,r,THEIRS-ROW,2026-09-07T10:00:00Z,x,y,z,n"])
    assert union.main(["x", str(base), str(ours), str(theirs)]) == 1
    try:
        union.read_rows(str(ours))
    except union.LedgerConflict:
        return
    raise AssertionError("the refused file parsed as a clean ledger")


def test_a_malformed_byte_still_writes_a_conflict(tmp_path):
    """THE second data-loss hole (skeptic review, blocker).

    main() caught only LedgerConflict, and called conflict_text OUTSIDE
    the try. One invalid UTF-8 byte — a mis-pasted em-dash in a free-text
    `notes` field — raised UnicodeDecodeError, escaped main() uncaught,
    and %A was never written: a clean one-sided file under a `UU` status,
    indistinguishable from a safe refusal.
    """
    union = _load("journal_union")
    base, ours, theirs = (tmp_path / n for n in ("b.csv", "o.csv", "t.csv"))
    _write(base, ACTION_HEADER, ["A0001,r,BASE-ROW,2026-09-07T10:00:00Z,x,y,z,n"])
    # ours: a bad byte in notes, plus an appended row
    with open(ours, "wb") as fh:
        fh.write(ACTION_HEADER.encode())
        fh.write(b"A0001,r,BASE-ROW,2026-09-07T10:00:00Z,x,y,z,n\n")
        fh.write(b"A0002,r,OURS-ROW,2026-09-07T11:00:00Z,x,y,z,bad\xff\n")
    # theirs EDITS the base row, which must refuse
    _write(theirs, ACTION_HEADER, ["A0001,r,THEIRS-ROW,2026-09-07T10:00:00Z,x,y,z,n"])
    assert union.main(["x", str(base), str(ours), str(theirs)]) == 1
    with open(ours, "rb") as fh:
        after = fh.read()
    assert b"<<<<<<<" in after and b">>>>>>>" in after, after
    assert b"THEIRS-ROW" in after, f"THEIR side was LOST\n{after!r}"


def test_write_conflict_survives_an_unreadable_side(tmp_path):
    """The guard must hold even when a side cannot be read at all."""
    union = _load("journal_union")
    ours, theirs = tmp_path / "o.csv", tmp_path / "t.csv"
    _write(ours, ACTION_HEADER, ["A0001,r,OURS-ROW,2026-09-07T10:00:00Z,x,y,z,n"])
    # `theirs` does not exist; `base` is a DIRECTORY, so reads raise OSError
    bad_base = tmp_path / "base_dir"
    bad_base.mkdir()
    union.write_conflict(str(bad_base), str(ours), str(theirs))
    after = _read(ours)
    assert _has_conflict_markers(after), after
    assert "OURS-ROW" in after


def test_a_duplicate_within_a_side_is_visible_in_the_conflict(tmp_path):
    """A refusal whose blocks render empty says nothing about what broke."""
    union = _load("journal_union")
    base, ours, theirs = (tmp_path / n for n in ("b.csv", "o.csv", "t.csv"))
    row = "A0001,r,s,2026-09-07T10:00:00Z,x,y,z,n"
    _write(base, ACTION_HEADER, [row])
    _write(ours, ACTION_HEADER, [row, row])      # duplicated within our side
    _write(theirs, ACTION_HEADER, [row])
    assert union.main(["x", str(base), str(ours), str(theirs)]) == 1
    after = _read(ours)
    assert _has_conflict_markers(after), after
    ours_block = after.split("<<<<<<<")[1].split("=======")[0]
    assert "A0001" in ours_block, f"the duplicate is invisible:\n{after}"


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


def test_render_all_never_fails_the_merge(tmp_path, capsys):
    render_all = _load("render_all")
    child = _scratch_child(tmp_path, "broken", readme="stale\n")
    (child / "docs" / "decisioning" / "actions.csv").write_text(
        "not,the,pledged,header\n"
    )
    assert render_all.main(["x", str(tmp_path)]) == 0, "a broken child must not fail the merge"
    out = capsys.readouterr().out
    assert "broken" in out and "exit" in out, out


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
        # ...and the script it actually invokes, or a rename leaves the
        # suite green while every merge runs a missing file (exit 127).
        script = driver.replace("-", "_") + ".py"
        assert os.path.isfile(os.path.join(MERGE_DIR, script)), script
        assert script in install, script


def test_driver_columns_agree_with_the_journal():
    """The driver restates the ledger's column names; pin them to the owner."""
    from dskit.journal.base import ACTION_FIELDS, PATH_FIELDS

    union = _load("journal_union")
    for fields in (ACTION_FIELDS, PATH_FIELDS):
        assert union.ID_FIELD in fields
    assert union.TIMESTAMP_FIELD in ACTION_FIELDS
    assert union.TIMESTAMP_FIELD not in PATH_FIELDS
    # The driver validates whole headers, so the whole tuples are the
    # agreement — not just the two column names.
    assert union.ACTION_FIELDS == tuple(ACTION_FIELDS)
    assert union.PATH_FIELDS == tuple(PATH_FIELDS)

# --- install.sh, driven for real ---------------------------------------
#
# Round 2 of the skeptic review: every install.sh claim was guarded only by
# hand-run shell scripts, so a future edit could silently reintroduce any of
# them with the suite fully green. These run the script in scratch git repos.

import subprocess


def _git(cwd, *args):
    return subprocess.run(("git", *args), cwd=cwd, capture_output=True, text=True)


def _scratch_repo(tmp_path, name="repo"):
    """A git repo carrying a copy of tools/merge, ready for install.sh."""
    repo = tmp_path / name
    (repo / "tools" / "merge").mkdir(parents=True)
    for f in os.listdir(MERGE_DIR):
        src = os.path.join(MERGE_DIR, f)
        if os.path.isfile(src):  # skip __pycache__
            shutil.copy(src, repo / "tools" / "merge" / f)
    _git(repo, "init", "-q", ".")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "T")
    return repo


def _install(repo, frozen_clock=None):
    """Run install.sh; `frozen_clock` stubs `date` so every call returns it.

    Freezing the clock is how the repeated-backup-collision case is reached
    at all: the fallback name carries a 1-second timestamp, so two installs
    inside one second collide unless the suffix also counts up.
    """
    env = dict(os.environ)
    if frozen_clock:
        stub = repo / "_stub"
        stub.mkdir(exist_ok=True)
        (stub / "date").write_text(f'#!/bin/sh\necho "{frozen_clock}"\n')
        os.chmod(stub / "date", 0o755)
        env["PATH"] = f"{stub}{os.pathsep}{env['PATH']}"
    done = subprocess.run(
        ["sh", "tools/merge/install.sh"], cwd=repo, capture_output=True,
        text=True, env=env,
    )
    assert done.returncode == 0, done.stderr
    return done


def _hook(repo):
    path = _git(repo, "rev-parse", "--git-path", "hooks").stdout.strip()
    return os.path.join(repo, path, "post-merge")


def test_install_creates_a_hook_that_invokes_render_all(tmp_path):
    repo = _scratch_repo(tmp_path)
    _install(repo)
    body = _read(_hook(repo))
    assert "render_all.py" in body
    assert os.access(_hook(repo), os.X_OK)


def test_install_chains_a_prior_hook_that_ends_in_exec(tmp_path):
    """`exec` never returns, so an APPENDED render_all would be dead code."""
    repo = _scratch_repo(tmp_path)
    _git(repo, "rev-parse", "--git-path", "hooks")
    os.makedirs(os.path.dirname(_hook(repo)), exist_ok=True)
    with open(_hook(repo), "w") as fh:
        fh.write('#!/bin/sh\ntouch "$(dirname "$0")/PRIOR_RAN"\nexec echo team\n')
    os.chmod(_hook(repo), 0o755)
    _install(repo)
    body = _read(_hook(repo))
    # ours must NOT be appended after the prior hook's exec; the prior hook
    # is invoked as a CHILD, so its exec replaces only that child.
    assert "pre-adr0109" in body, body
    assert body.index("pre-adr0109") < body.index("render_all.py"), body


def test_install_never_overwrites_an_earlier_backup(tmp_path):
    repo = _scratch_repo(tmp_path)
    os.makedirs(os.path.dirname(_hook(repo)), exist_ok=True)
    # THREE foreign hooks under a FROZEN clock: the timestamped fallback
    # name is identical every time, so anything that does not also count up
    # silently destroys the middle one.
    for marker in ("ORIGINAL-TEAM-HOOK", "SECOND-TOOL-HOOK", "THIRD-TOOL-HOOK"):
        with open(_hook(repo), "w") as fh:
            fh.write(f"#!/bin/sh\necho {marker}\n")
        os.chmod(_hook(repo), 0o755)
        _install(repo, frozen_clock="20260907120000")
    saved = "\n".join(
        _read(os.path.join(os.path.dirname(_hook(repo)), f))
        for f in os.listdir(os.path.dirname(_hook(repo)))
        if "pre-adr0109" in f
    )
    assert "ORIGINAL-TEAM-HOOK" in saved, f"the true original was lost:\n{saved}"
    assert "SECOND-TOOL-HOOK" in saved, f"a backup was overwritten:\n{saved}"
    assert "THIRD-TOOL-HOOK" in saved, saved


def test_reinstalling_after_a_move_heals_the_stale_paths(tmp_path):
    """The marker alone is not proof the hook still works.

    Absolute paths go stale when the repo moves; the hook then fails on
    every merge and a chained prior hook silently stops running.
    """
    repo = _scratch_repo(tmp_path, "before")
    _install(repo)
    assert "before" in _read(_hook(repo))
    moved = tmp_path / "after"
    shutil.move(str(repo), str(moved))
    _install(moved)
    body = _read(_hook(moved))
    assert "after" in body, body
    assert "before" not in body, f"stale path survived the reinstall:\n{body}"


def test_installing_is_idempotent(tmp_path):
    repo = _scratch_repo(tmp_path)
    _install(repo)
    first = _read(_hook(repo))
    _install(repo)
    _install(repo)
    assert _read(_hook(repo)) == first
    assert _read(_hook(repo)).count("render_all.py") == 1

def test_a_failed_conflict_write_leaves_the_file_intact(tmp_path):
    """A failed write must not truncate %A (skeptic review round 2).

    `open(path, "wb")` truncates the instant it succeeds, so a write that
    then fails — full disk, quota, read-only mount — left %A at ZERO
    bytes: worse than the one-sided file this driver exists to prevent.
    The write goes through a temp file and a rename, so a failure changes
    nothing. Blocking the temp path with a DIRECTORY reproduces the
    failure without depending on permissions (this container runs as
    root, where chmod proves nothing).
    """
    union = _load("journal_union")
    base, ours, theirs = (tmp_path / n for n in ("b.csv", "o.csv", "t.csv"))
    _write(base, ACTION_HEADER, [])
    _write(ours, ACTION_HEADER, ["A0009,r,OURS-ROW,2026-09-07T10:00:00Z,x,y,z,n"])
    _write(theirs, ACTION_HEADER, ["A0009,r,THEIRS-ROW,2026-09-07T10:00:00Z,x,y,z,n"])
    before = _read(ours)
    (tmp_path / "o.csv.journal-union.tmp").mkdir()   # block the temp write
    assert union.write_conflict(str(base), str(ours), str(theirs)) is False
    assert _read(ours) == before, "the file was truncated or partially written"


def test_replace_file_is_atomic_and_leaves_no_temp(tmp_path):
    """A naive direct write passes the happy half; only failure separates them."""
    union = _load("journal_union")
    target = tmp_path / "x.csv"
    target.write_text("original\n")
    union.replace_file(str(target), b"replaced\n")
    assert _read(target) == "replaced\n"
    assert not (tmp_path / "x.csv.journal-union.tmp").exists()
    # The property that actually distinguishes atomic from direct: a FAILED
    # write leaves the previous content whole, not truncated.
    (tmp_path / "x.csv.journal-union.tmp").mkdir()
    try:
        union.replace_file(str(target), b"never lands\n")
    except OSError:
        pass
    else:
        raise AssertionError("replace_file should have raised")
    assert _read(target) == "replaced\n", "a failed write damaged the file"

def test_a_failed_union_write_never_leaves_a_clean_file(tmp_path):
    """Blocker, review round 3 — the SUCCESS path.

    replace_file was atomic, but main() called it unguarded. An OSError
    propagated uncaught and git handed back its own %A: our side alone,
    no markers, looking perfectly mergeable. The merge succeeding is
    exactly what makes this dangerous — nothing else signals a problem.
    """
    union = _load("journal_union")
    base, ours, theirs = (tmp_path / n for n in ("b.csv", "o.csv", "t.csv"))
    row = "A0001,r,s,2026-09-07T10:00:00Z,x,y,z,base"
    _write(base, ACTION_HEADER, [row])
    _write(ours, ACTION_HEADER, [row, "A0002,r,s,2026-09-07T11:00:00Z,x,y,z,OURS-ROW"])
    _write(theirs, ACTION_HEADER, [row, "A0003,r,s,2026-09-07T12:00:00Z,x,y,z,THEIRS-ROW"])
    # This merge would SUCCEED; block every write so it cannot be stored.
    (tmp_path / "o.csv.journal-union.tmp").mkdir()
    assert union.main(["x", str(base), str(ours), str(theirs)]) == 1
    after = _read(ours)
    assert union.UNRESOLVED in after, f"the file looks clean:\n{after}"
    # and it must not parse as a ledger, so it cannot be `git add`-ed blind
    try:
        union.read_rows(str(ours))
    except union.LedgerConflict:
        return
    raise AssertionError("the unwritable merge left a parseable ledger")


def test_a_failed_conflict_write_marks_the_file_unresolved(tmp_path):
    """Same blocker, the REFUSAL path: the bool was returned but discarded."""
    union = _load("journal_union")
    base, ours, theirs = (tmp_path / n for n in ("b.csv", "o.csv", "t.csv"))
    _write(base, ACTION_HEADER, [])
    _write(ours, ACTION_HEADER, ["A0009,r,OURS-ROW,2026-09-07T10:00:00Z,x,y,z,n"])
    _write(theirs, ACTION_HEADER, ["A0009,r,THEIRS-ROW,2026-09-07T10:00:00Z,x,y,z,n"])
    (tmp_path / "o.csv.journal-union.tmp").mkdir()
    assert union.main(["x", str(base), str(ours), str(theirs)]) == 1
    assert union.UNRESOLVED in _read(ours)


def test_reinstall_after_a_move_keeps_the_chained_hook(tmp_path):
    """The saved path is ALWAYS stale after a move — which is when this runs.

    Dropping the team's hook (direnv, lint-sync) on every move+reinstall
    is silent apart from one warning line.
    """
    repo = _scratch_repo(tmp_path, "before")
    os.makedirs(os.path.dirname(_hook(repo)), exist_ok=True)
    with open(_hook(repo), "w") as fh:
        fh.write("#!/bin/sh\necho TEAM-HOOK\n")
    os.chmod(_hook(repo), 0o755)
    _install(repo)
    assert "pre-adr0109" in _read(_hook(repo))
    moved = tmp_path / "after"
    shutil.move(str(repo), str(moved))
    _install(moved)
    body = _read(_hook(moved))
    assert "pre-adr0109" in body, f"the chained hook was dropped:\n{body}"
    assert "before" not in body, f"stale path survived:\n{body}"
    kept = [
        f for f in os.listdir(os.path.dirname(_hook(moved))) if "pre-adr0109" in f
    ]
    assert kept and "TEAM-HOOK" in _read(
        os.path.join(os.path.dirname(_hook(moved)), kept[0])
    )


def test_installing_from_a_worktree_pins_the_main_worktree(tmp_path):
    """Driver config is SHARED across worktrees.

    Pinning it to a linked worktree means deleting that worktree breaks
    every future merge repo-wide — the original blocker reincarnated.
    """
    repo = _scratch_repo(tmp_path, "mainrepo")
    (repo / "f").write_text("x\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "base")
    wt = tmp_path / "wt"
    added = _git(repo, "worktree", "add", "-q", str(wt), "-b", "tmp")
    if added.returncode != 0:
        import pytest

        pytest.skip("git too old for worktrees")  # never a vacuous PASS
    _install(wt)
    driver = _git(wt, "config", "--get", "merge.journal-union.driver").stdout
    assert "mainrepo" in driver, driver
    assert f"{os.sep}wt{os.sep}" not in driver, f"pinned to the worktree:\n{driver}"


def test_the_temp_suffix_is_pinned_to_gitignore():
    """Rename the driver's temp suffix and leaked scratch becomes committable.

    The suite stayed green through exactly that rename, so the agreement
    needs pinning like .gitattributes and the ledger columns already are.
    """
    union = _load("journal_union")
    with open(os.path.join(REPO_ROOT, ".gitignore"), encoding="utf-8") as fh:
        ignored = fh.read()
    assert f"*{union.TMP_SUFFIX}" in ignored, union.TMP_SUFFIX


def test_the_unresolved_marker_starts_on_its_own_line(tmp_path):
    """Without the leading newline the marker merges into the last row.

    A %A with no trailing newline — a hand-edited path.csv, which the owner
    maintains by hand — would then parse as a CLEAN ledger whose last note
    swallowed the warning.
    """
    union = _load("journal_union")
    target = tmp_path / "p.csv"
    with open(target, "wb") as fh:  # deliberately no trailing newline
        fh.write((PATH_HEADER + "A0001,l,p,f,Y,,empirical").encode())
    assert union.mark_unresolved(str(target)) is True
    try:
        union.read_rows(str(target))
    except union.LedgerConflict:
        return
    raise AssertionError("the marker was absorbed into the last row")


def test_keep_both_survives_a_stray_byte(tmp_path):
    """BLOCKER, review round 4: the sibling driver had both fixed defects.

    install.sh registers keep_both for docs/RE-ENTRY.md — the file /wrap
    writes every session. One pasted smart-quote raised UnicodeDecodeError
    out of main(), leaving %A as OUR side alone with no marker: a whole
    session record silently dropped.
    """
    keep = _load("keep_both")
    base, ours, theirs = (tmp_path / n for n in ("b.md", "o.md", "t.md"))
    base.write_text("# base\n")
    with open(ours, "wb") as fh:
        fh.write(b"# ours\nmangled \x92 byte\n")
    theirs.write_text("# theirs\nTHEIR-SESSION-RECORD\n")
    assert keep.main(["x", str(base), str(ours), str(theirs)]) == 0
    with open(ours, "rb") as fh:
        after = fh.read()
    assert b"THEIR-SESSION-RECORD" in after, f"their side was LOST\n{after!r}"
    assert b"\x92" in after, "the stray byte was not preserved"


def test_keep_both_never_truncates_on_a_failed_write(tmp_path):
    """The R2 shape, still present in keep_both: a truncating open(..., "w")."""
    keep = _load("keep_both")
    union = _load("journal_union")
    base, ours, theirs = (tmp_path / n for n in ("b.md", "o.md", "t.md"))
    base.write_text("# base\n")
    ours.write_text("# ours\nOUR-SESSION-RECORD\n")
    theirs.write_text("# theirs\nTHEIR-SESSION-RECORD\n")
    (tmp_path / ("o.md" + union.TMP_SUFFIX)).mkdir()   # block the write
    assert keep.main(["x", str(base), str(ours), str(theirs)]) == 1
    after = _read(ours)
    assert "OUR-SESSION-RECORD" in after, "our side was truncated"
    # ...and their side must be APPENDED, not merely "left unchanged": an
    # unchanged %A is a clean-looking single session record, exactly the
    # loss this driver exists to prevent.
    assert "THEIR-SESSION-RECORD" in after, f"their side was LOST\n{after}"


def test_keep_both_survives_a_broken_sibling_module(tmp_path):
    """The shared import must not be a new way to lose a side.

    Sharing replace_file was right, but a module-level import made a
    broken journal_union.py fatal — and this repo edits that file
    constantly, so a merge mid-edit hit it.
    """
    shutil.copytree(MERGE_DIR, tmp_path / "merge", ignore=shutil.ignore_patterns("__pycache__"))
    (tmp_path / "merge" / "journal_union.py").write_text("this is not python(\n")
    # git runs each driver in a FRESH process; in-process, an earlier test
    # may have cached a good journal_union, which would hide the break.
    import sys as _sys

    cached = _sys.modules.pop("journal_union", None)
    spec = importlib.util.spec_from_file_location(
        "kb_broken", tmp_path / "merge" / "keep_both.py"
    )
    keep = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(keep)
    base, ours, theirs = (tmp_path / n for n in ("b.md", "o.md", "t.md"))
    base.write_text("# base\n")
    ours.write_text("# ours\nOUR-SESSION-RECORD\n")
    theirs.write_text("# theirs\nTHEIR-SESSION-RECORD\n")
    try:
        assert keep.main(["x", str(base), str(ours), str(theirs)]) == 1
    finally:
        if cached is not None:
            _sys.modules["journal_union"] = cached
    after = _read(ours)
    assert "OUR-SESSION-RECORD" in after and "THEIR-SESSION-RECORD" in after, after


def test_keep_both_preserves_crlf(tmp_path):
    """Without newline="" every CR is rewritten — a whole-file diff."""
    keep = _load("keep_both")
    base, ours, theirs = (tmp_path / n for n in ("b.md", "o.md", "t.md"))
    base.write_bytes(b"# base\r\n")
    ours.write_bytes(b"# ours\r\nOUR\r\n")
    theirs.write_bytes(b"# theirs\r\nTHEIR\r\n")
    assert keep.main(["x", str(base), str(ours), str(theirs)]) == 0
    with open(ours, "rb") as fh:
        after = fh.read()
    assert b"OUR\r\n" in after and b"THEIR\r\n" in after, after


def test_refuse_reports_when_even_the_marker_fails(tmp_path, capsys):
    """The fix for the R3 blocker shipped without a test of its own."""
    union = _load("journal_union")
    base, ours, theirs = (tmp_path / n for n in ("b.csv", "o.csv", "t.csv"))
    _write(base, ACTION_HEADER, [])
    _write(ours, ACTION_HEADER, ["A0009,r,OURS,2026-09-07T10:00:00Z,x,y,z,n"])
    _write(theirs, ACTION_HEADER, ["A0009,r,THEIRS,2026-09-07T10:00:00Z,x,y,z,n"])
    (tmp_path / ("o.csv" + union.TMP_SUFFIX)).mkdir()      # block the replace
    monkey = union.mark_unresolved
    union.mark_unresolved = lambda path: False             # and the append
    try:
        assert union.main(["x", str(base), str(ours), str(theirs)]) == 1
    finally:
        union.mark_unresolved = monkey
    err = capsys.readouterr().err
    # Must be _refuse's OWN line: write_conflict prints a similar warning,
    # so asserting the shared phrase passes even with this branch deleted.
    assert "could not even mark" in err, err


def test_install_does_not_reenable_a_disabled_chained_hook(tmp_path):
    """`chmod -x` on the chained hook is the user's off-switch."""
    repo = _scratch_repo(tmp_path)
    os.makedirs(os.path.dirname(_hook(repo)), exist_ok=True)
    with open(_hook(repo), "w") as fh:
        fh.write("#!/bin/sh\necho TEAM\n")
    os.chmod(_hook(repo), 0o755)
    _install(repo)
    hooks_dir = os.path.dirname(_hook(repo))
    prior = [f for f in os.listdir(hooks_dir) if "pre-adr0109" in f][0]
    os.chmod(os.path.join(hooks_dir, prior), 0o644)        # user disables it
    _install(repo)
    mode = os.stat(os.path.join(hooks_dir, prior)).st_mode
    assert not mode & 0o111, "the reinstall silently re-enabled it"


def test_the_temp_suffix_is_actually_ignored_by_git(tmp_path):
    """Assert git's own answer, not a substring of .gitignore."""
    union = _load("journal_union")
    name = ".merge_file_aBcDeF" + union.TMP_SUFFIX
    done = subprocess.run(
        ["git", "check-ignore", "-q", name], cwd=REPO_ROOT
    )
    assert done.returncode == 0, f"{name} is not ignored"
