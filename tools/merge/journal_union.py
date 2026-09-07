"""Validated-union merge driver for the append-only journal CSV ledgers.

Git invokes this as ``journal_union.py %O %A %B`` (base, ours, theirs)
for ``actions.csv`` and ``path.csv`` (ADR-0109). Two lanes that each
appended rows under the in-repo ``flock`` still conflict textually in
git; the union is accepted only when nothing was edited or deleted on
either side, ids stay unique, and the header matches the journal's own
schema.

Two invariants this driver exists to hold, both learned the hard way:

* **A refusal is never silent.** git does NOT write conflict markers for
  a driver that exits nonzero — it leaves whatever bytes are in ``%A``,
  which is OUR side alone. A resolver then sees a clean, well-formed CSV
  with the other lane's rows simply absent, commits it, and those rows
  are gone. So every refusal here WRITES the conflict itself: both sides,
  between ``<<<<<<<``/``=======``/``>>>>>>>`` markers, exactly as git
  would have without this driver. Refusing must never be worse than not
  being installed.
* **The tape is not re-ordered.** The ledger is append-only and
  ``render.py`` builds its operator table from the tail of FILE order, so
  sorting the union by ``executed_at`` would silently rewrite history and
  change what the README shows. Base rows keep base order; new rows are
  appended ours-then-theirs.

Ids are NOT auto-renumbered on collision. They are cited from prose
(``framework.md``: "action A2850 locks…"), so silently moving one would
break references no test covers. A collision is a conflict for a human.
"""

from __future__ import annotations

import csv
import io
import os
import sys

#: The ledger's row key. Pinned against ``dskit.journal.base`` by
#: ``tests/tools/test_merge_drivers.py`` — the second copy is the bug.
ID_FIELD = "id"

#: The actions ledger's ordering column; path rows lack it. Pinned the
#: same way. Used only to VALIDATE the schema — never to sort.
TIMESTAMP_FIELD = "executed_at"

#: The two ledger schemas this driver accepts, pinned against
#: ``dskit.journal.base.ACTION_FIELDS``/``PATH_FIELDS`` by test. A header
#: that matches neither is refused: two sides sharing an identically
#: drifted header used to merge cleanly and then break the next journal
#: write, with a green merge behind it.
ACTION_FIELDS = (
    "id",
    "category",
    "step",
    "executed_at",
    "inputs",
    "outputs",
    "db_location",
    "notes",
)
PATH_FIELDS = (
    "id",
    "label",
    "purpose",
    "relevant_files",
    "locked",
    "current_work",
    "criteria",
)

#: The sibling temp `replace_file` writes. Pinned against `.gitignore` by
#: test: %A is git's scratch file in the WORKTREE ROOT, so this lands there
#: too, and a rename that outran the ignore rule would make leaked merge
#: scratch committable.
TMP_SUFFIX = ".journal-union.tmp"

#: git's standard conflict markers, so a resolver sees exactly what an
#: unconfigured clone would show.
MARK_OURS = "<<<<<<< ours"
MARK_SEP = "======="
MARK_THEIRS = ">>>>>>> theirs"


class LedgerConflict(ValueError):
    """The union is not safe; the path must stay conflicted."""


def read_rows(path):
    """Load ``(header, rows)`` from a ledger CSV.

    Parameters
    ----------
    path : str
        Source file; a missing or empty file is an empty ledger.

    Returns
    -------
    tuple of (list of str, list of dict)
        Header names and rows as plain dicts.

    Raises
    ------
    LedgerConflict
        A row carries extra or missing cells.
    """
    try:
        with open(path, encoding="utf-8", errors="surrogateescape",
                  newline="") as fh:
            reader = csv.DictReader(fh)
            header = list(reader.fieldnames or ())
            rows = []
            for raw in reader:
                if raw.get(None) or any(value is None for value in raw.values()):
                    raise LedgerConflict(f"{path}: row has extra or missing cells")
                rows.append(raw)
    except FileNotFoundError:
        return [], []
    return header, rows


def read_text(path):
    """Return the file's raw text, or empty when it is absent."""
    try:
        with open(path, encoding="utf-8", errors="surrogateescape",
                  newline="") as fh:
            return fh.read()
    except FileNotFoundError:
        return ""


def _index_by_id(rows, path):
    """Key rows by ``id``; refuse a duplicate id within one side."""
    index = {}
    for row in rows:
        rid = row[ID_FIELD]
        if rid in index:
            raise LedgerConflict(f"{path}: duplicate id {rid}")
        index[rid] = row
    return index


def _csv_text(header, rows):
    """Render ``rows`` under ``header`` as ledger CSV text."""
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=header, lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    return buf.getvalue()


def _row_lines(header, rows):
    """Return the CSV body lines for ``rows``, without the header."""
    if not rows:
        return []
    return _csv_text(header, rows).split("\n")[1:-1]


def merge_ledgers(base_path, ours_path, theirs_path):
    """Union two appended ledger sides into merged CSV text.

    Base rows keep base order; rows new on our side follow in our file
    order, then rows new on theirs. The ``executed_at`` column is NOT
    used to sort — the ledger is an append-only tape and its order is
    data (see the module docstring).

    Parameters
    ----------
    base_path : str
        The merge base (``%O``); may be empty or absent (add/add).
    ours_path : str
        The current side (``%A``).
    theirs_path : str
        The other side (``%B``).

    Returns
    -------
    str
        The merged CSV.

    Raises
    ------
    LedgerConflict
        Header drift, a header matching neither ledger schema, a
        duplicate id within a side, or any row edited, deleted, or
        id-collided across sides. The caller must then write a conflict.
    """
    _, base_rows = read_rows(base_path)
    our_header, our_rows = read_rows(ours_path)
    their_header, their_rows = read_rows(theirs_path)

    if not our_header and not their_header:
        return ""
    if our_header != their_header:
        raise LedgerConflict(f"header drift: {our_header} vs {their_header}")
    header = our_header
    if tuple(header) not in (ACTION_FIELDS, PATH_FIELDS):
        raise LedgerConflict(
            f"header {header} matches neither the actions nor the path schema"
        )

    base = _index_by_id(base_rows, base_path)
    ours = _index_by_id(our_rows, ours_path)
    theirs = _index_by_id(their_rows, theirs_path)

    deleted = (set(base) - set(ours)) | (set(base) - set(theirs))
    if deleted:
        raise LedgerConflict(f"rows deleted, not appended: {sorted(deleted)}")

    for rid, row in base.items():
        if ours.get(rid) != row or theirs.get(rid) != row:
            raise LedgerConflict(f"row {rid} edited, not appended")

    collided = sorted(
        rid
        for rid in set(ours) & set(theirs)
        if rid not in base and ours[rid] != theirs[rid]
    )
    if collided:
        raise LedgerConflict(
            f"id(s) {collided} appended on both sides with different content — "
            "ids are cited from prose, so renumber one side by hand rather "
            "than letting the merge move them"
        )

    merged = list(base_rows)
    merged += [row for row in our_rows if row[ID_FIELD] not in base]
    merged += [
        row for row in their_rows if row[ID_FIELD] not in base and row[ID_FIELD] not in ours
    ]
    return _csv_text(header, merged)


def conflict_text(base_path, ours_path, theirs_path):
    """Build the conflicted file a refusal must leave behind.

    Row-level when both sides share one valid schema — the agreed rows
    stay clean and only the diverging appends sit between markers. Whole
    file otherwise, which is what git itself would have produced.

    Parameters
    ----------
    base_path, ours_path, theirs_path : str
        The three sides git supplied.

    Returns
    -------
    str
        Text containing BOTH sides, with git-style conflict markers.
    """
    our_text, their_text = read_text(ours_path), read_text(theirs_path)
    try:
        _, base_rows = read_rows(base_path)
        our_header, our_rows = read_rows(ours_path)
        their_header, their_rows = read_rows(theirs_path)
        usable = (
            our_header
            and our_header == their_header
            and tuple(our_header) in (ACTION_FIELDS, PATH_FIELDS)
        )
    except LedgerConflict:
        usable = False
    if not usable:
        return "\n".join([MARK_OURS, our_text.rstrip("\n"), MARK_SEP,
                          their_text.rstrip("\n"), MARK_THEIRS, ""])

    # Consume one occurrence per side as each base row is matched. Counting
    # matches instead (``row in our_rows``) double-counts when the BASE
    # itself carries a duplicate id, and the conflict then FABRICATES a row
    # that exists once on both sides. Consuming also means a side that
    # duplicated a row still shows the extra copy in its own block, rather
    # than rendering an empty block that says nothing about what broke.
    pool_ours, pool_theirs = list(our_rows), list(their_rows)
    keep = []
    for row in base_rows:
        if row in pool_ours and row in pool_theirs:
            pool_ours.remove(row)
            pool_theirs.remove(row)
            keep.append(dict(row))
    our_rest, their_rest = pool_ours, pool_theirs
    lines = _csv_text(our_header, keep).rstrip("\n").split("\n")
    lines.append(MARK_OURS)
    lines.extend(_row_lines(our_header, our_rest))
    lines.append(MARK_SEP)
    lines.extend(_row_lines(our_header, their_rest))
    lines.append(MARK_THEIRS)
    return "\n".join(lines) + "\n"


def _raw_conflict(ours_path, theirs_path):
    """Both sides between markers as BYTES — the last-resort fallback.

    Reads binary, so no encoding can make it fail. This is what stands
    between a crash and the original defect: any failure that leaves
    ``%A`` unwritten hands the resolver a clean one-sided file.
    """
    def data(path):
        try:
            with open(path, "rb") as fh:
                return fh.read().rstrip(b"\n")
        except OSError:
            return b""

    return b"\n".join([
        MARK_OURS.encode(), data(ours_path), MARK_SEP.encode(),
        data(theirs_path), MARK_THEIRS.encode(), b"",
    ])


def replace_file(path, payload):
    """Replace ``path`` with ``payload`` bytes, or leave it UNTOUCHED.

    Writes a sibling temporary file and renames it over ``path``. A plain
    ``open(path, "wb")`` truncates the instant it succeeds, so a write
    that then fails — a full disk, a quota, a read-only mount — leaves
    ``%A`` at zero bytes, which is worse than the one-sided file this
    driver exists to prevent. Renaming means a failed write changes
    nothing.

    Parameters
    ----------
    path : str
        The file to replace.
    payload : bytes
        The exact bytes to leave behind.

    Raises
    ------
    OSError
        The write or the rename failed; ``path`` still holds its prior
        content.
    """
    tmp = path + TMP_SUFFIX
    try:
        with open(tmp, "wb") as fh:
            fh.write(payload)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


#: Appended to ``%A`` when the conflict itself could not be written. It
#: makes the file unparseable as a ledger, so nobody can `git add` a
#: clean-looking one-sided file by mistake. Appending costs a few bytes,
#: so it usually lands even when the full write did not.
UNRESOLVED = (
    ">>>>>>> journal-union: CONFLICT COULD NOT BE WRITTEN. This file holds "
    "ONE SIDE ONLY. Do not commit it. Recover with `git checkout --merge` "
    "or `git show :2:<path>` and `git show :3:<path>`."
)


def mark_unresolved(path):
    """Append :data:`UNRESOLVED` to ``path``; never raise.

    The last line of defence. When even the conflict write fails there is
    no disk to fix that, but a file that cannot PARSE cannot be committed
    by accident — and that is the whole failure mode this driver exists
    to prevent.

    Parameters
    ----------
    path : str
        The file to mark.

    Returns
    -------
    bool
        Whether the marker landed.
    """
    try:
        with open(path, "ab") as fh:
            fh.write(b"\n" + UNRESOLVED.encode() + b"\n")
        return True
    except OSError:
        return False


def write_conflict(base_path, ours_path, theirs_path):
    """Write the conflict into ``%A``; report whether it landed.

    The payload is guarded — a malformed byte or an unreadable side falls
    back to :func:`_raw_conflict` rather than propagating. The WRITE
    cannot be guaranteed: no disk, no write. What is guaranteed is that a
    failed write leaves ``%A`` unchanged rather than truncated
    (:func:`replace_file`), and that the caller is told, so the failure is
    loud instead of a silent clean-looking file.

    Parameters
    ----------
    base_path, ours_path, theirs_path : str
        The three sides git supplied; ``%A`` is replaced.

    Returns
    -------
    bool
        True when the conflict was written; False when it could not be,
        in which case ``%A`` still holds whatever git left there.
    """
    try:
        payload = conflict_text(base_path, ours_path, theirs_path).encode(
            "utf-8", errors="surrogateescape"
        )
    except Exception:  # noqa: BLE001 - a conflict MUST be written regardless
        payload = _raw_conflict(ours_path, theirs_path)
    try:
        replace_file(ours_path, payload)
    except OSError as exc:
        print(
            f"journal-union: FAILED to write the conflict into {ours_path}: "
            f"{exc}\njournal-union: *** {ours_path} may hold ONE SIDE ONLY. "
            "Do NOT `git add` it. Recover with `git checkout --merge` or "
            "resolve from `git show :2:<path>` and `git show :3:<path>`. ***",
            file=sys.stderr,
        )
        return False
    return True


def _refuse(base_path, ours_path, theirs_path):
    """Write the conflict, or at minimum make %A unparseable.

    Both outcomes are reported, because they differ materially for whoever
    resolves this: a landed marker makes the file self-describing and
    unparseable, while a failed one leaves a clean-LOOKING one-sided
    ledger guarded only by stderr.
    """
    if write_conflict(base_path, ours_path, theirs_path):
        return
    if not mark_unresolved(ours_path):
        print(
            f"journal-union: *** could not even mark {ours_path} — it holds "
            "ONE SIDE ONLY and looks clean. Do NOT `git add` it. ***",
            file=sys.stderr,
        )


def main(argv):
    """Drive one merge: write the union to ``%A``, or write the conflict.

    Parameters
    ----------
    argv : list of str
        ``[script, %O, %A, %B]`` — the paths git supplies.

    Returns
    -------
    int
        ``0`` merged (``%A`` is the union); ``1`` refused (``%A`` holds
        BOTH sides between conflict markers, for hand resolution).
    """
    base_path, ours_path, theirs_path = argv[1:4]
    try:
        text = merge_ledgers(base_path, ours_path, theirs_path)
    except Exception as exc:  # noqa: BLE001 - see write_conflict's docstring
        # EVERY failure refuses, not just LedgerConflict. A UnicodeDecodeError
        # from one bad byte in a free-text `notes` field used to escape main()
        # uncaught, so %A was never written and the resolver got a clean
        # one-sided file under a `UU` status — indistinguishable from a safe
        # refusal, and the original defect exactly.
        print(f"journal-union: refusing to merge: {exc}", file=sys.stderr)
        _refuse(base_path, ours_path, theirs_path)
        return 1
    try:
        # The union goes through the same non-truncating replace: a
        # half-written ledger is data loss just as surely as a one-sided one.
        replace_file(ours_path, text.encode("utf-8", errors="surrogateescape"))
    except OSError as exc:
        # The merge SUCCEEDED but could not be stored. Leaving it here would
        # hand back git's own %A — our side alone, no markers, looking
        # perfectly clean. Downgrade to a refusal instead. The conflict
        # payload is strictly LARGER and uses the same temp path that just
        # failed, so this usually falls through to mark_unresolved — which is
        # the point: end up unparseable rather than plausibly clean.
        print(
            f"journal-union: could not write the union: {exc} — refusing instead",
            file=sys.stderr,
        )
        _refuse(base_path, ours_path, theirs_path)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
