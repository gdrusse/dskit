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
        with open(path, encoding="utf-8", newline="") as fh:
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
        with open(path, encoding="utf-8", newline="") as fh:
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

    agreed = [row for row in base_rows if row in our_rows and row in their_rows]
    keep = [dict(row) for row in agreed]
    our_rest = [row for row in our_rows if row not in agreed]
    their_rest = [row for row in their_rows if row not in agreed]
    lines = _csv_text(our_header, keep).rstrip("\n").split("\n")
    lines.append(MARK_OURS)
    lines.extend(_row_lines(our_header, our_rest))
    lines.append(MARK_SEP)
    lines.extend(_row_lines(our_header, their_rest))
    lines.append(MARK_THEIRS)
    return "\n".join(lines) + "\n"


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
    except LedgerConflict as exc:
        print(f"journal-union: refusing to merge: {exc}", file=sys.stderr)
        conflicted = conflict_text(base_path, ours_path, theirs_path)
        with open(ours_path, "w", encoding="utf-8", newline="") as fh:
            fh.write(conflicted)
        return 1
    with open(ours_path, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
