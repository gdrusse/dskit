"""Validated-union merge driver for the append-only journal CSV ledgers.

Git invokes this as ``journal_union.py %O %A %B`` (base, ours, theirs)
for ``actions.csv`` and ``path.csv`` (ADR-0107). Two lanes that each
appended rows under the in-repo ``flock`` still conflict textually in
git; the union is accepted only when nothing was edited or deleted on
either side, ids stay unique, and the header is unchanged — every other
shape stays a conflict, which is exactly the dangerous case.
"""

from __future__ import annotations

import csv
import io
import sys

#: The ledger's row key. Pinned against ``dskit.journal.base`` by
#: ``tests/tools/test_merge_drivers.py`` — the second copy is the bug.
ID_FIELD = "id"

#: The actions ledger's ordering column; path rows (either schema) lack
#: it and keep file order instead. Pinned the same way.
TIMESTAMP_FIELD = "executed_at"


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
    ValueError
        A row carries extra or missing cells.
    """
    try:
        with open(path, encoding="utf-8", newline="") as fh:
            reader = csv.DictReader(fh)
            header = list(reader.fieldnames or ())
            rows = []
            for raw in reader:
                if raw.get(None) or any(value is None for value in raw.values()):
                    raise ValueError(f"{path}: row has extra or missing cells")
                rows.append(raw)
    except FileNotFoundError:
        return [], []
    return header, rows


def _index_by_id(rows, path):
    """Key rows by ``id``; refuse a duplicate id within one side."""
    index = {}
    for row in rows:
        rid = row[ID_FIELD]
        if rid in index:
            raise ValueError(f"{path}: duplicate id {rid}")
        index[rid] = row
    return index


def merge_ledgers(base_path, ours_path, theirs_path):
    """Union two appended ledger sides into merged CSV text.

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
        The merged CSV, rows ordered by ``executed_at`` when the column
        exists (stable — ties keep file order).

    Raises
    ------
    ValueError
        Header drift, a missing ``id`` column, a duplicate id within a
        side, or any row edited, deleted, or id-collided across sides.
        The merge must then stay a conflict.
    """
    base_header, base_rows = read_rows(base_path)
    our_header, our_rows = read_rows(ours_path)
    their_header, their_rows = read_rows(theirs_path)

    if not our_header and not their_header:
        return ""
    if our_header != their_header:
        raise ValueError(f"header drift: {our_header} vs {their_header}")
    header = our_header
    if ID_FIELD not in header:
        raise ValueError(f"no {ID_FIELD!r} column in {header}")

    base = _index_by_id(base_rows, base_path)
    ours = _index_by_id(our_rows, ours_path)
    theirs = _index_by_id(their_rows, theirs_path)

    deleted = (set(base) - set(ours)) | (set(base) - set(theirs))
    if deleted:
        raise ValueError(f"rows deleted, not appended: {sorted(deleted)}")

    for rid, row in base.items():
        if ours.get(rid) != row or theirs.get(rid) != row:
            raise ValueError(f"row {rid} edited, not appended")

    merged = []
    seen = set()
    for row in our_rows + their_rows:
        rid = row[ID_FIELD]
        if rid in seen:
            if ours[rid] != theirs[rid]:
                raise ValueError(f"id {rid} appended on both sides differently")
            continue
        seen.add(rid)
        merged.append(row)
    if TIMESTAMP_FIELD in header:
        merged.sort(key=lambda row: row.get(TIMESTAMP_FIELD) or "")

    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=header, lineterminator="\n")
    writer.writeheader()
    for row in merged:
        writer.writerow(row)
    return buf.getvalue()


def main(argv):
    """Drive one merge: write the union to ``%A`` or refuse it.

    Parameters
    ----------
    argv : list of str
        ``[script, %O, %A, %B]`` — the paths git supplies.

    Returns
    -------
    int
        ``0`` merged (``%A`` rewritten); ``1`` refused (``%A`` untouched,
        the path stays conflicted for hand resolution).
    """
    base_path, ours_path, theirs_path = argv[1:4]
    try:
        text = merge_ledgers(base_path, ours_path, theirs_path)
    except ValueError as exc:
        print(f"journal-union: refusing to merge: {exc}", file=sys.stderr)
        return 1
    with open(ours_path, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
