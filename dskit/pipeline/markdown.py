"""The one markdown-table renderer the package emits its tables through.

Three surfaces render pipe tables — the run report kind, the walk-forward
summary, and the cross-run `runs` verb — and before this module each
carried its own copy of the same three decisions: what an absent value
looks like (``—``), how a float is rounded (``%.6g``), and the separator
line. The copies had already diverged: one rendered a boolean as ``yes``,
another as ``True``, so the same knob read differently depending on which
table you were looking at. That is the defect shape the standards file
names (a value in two places with nothing pinning them), and the fix is
not a pin but a single name.

Cells are ESCAPED, not trusted. A value carrying a ``|`` — a regex
alternation in a declared param, a column expression — would otherwise
open a column the header never declared and shift every value right of
it onto the wrong heading. A table that silently misattributes numbers is
worse than no table, so the delimiter is escaped and rows whose width
disagrees with the header are REFUSED rather than rendered crooked.

Tier 1: stdlib only, no knowledge of any domain.
"""

from __future__ import annotations

__all__ = ["MISSING", "pipe_table", "render_cell"]

#: What an absent value reads as. Never the empty string: a blank cell is
#: indistinguishable from a rendering bug.
MISSING = "—"

#: Longest cell rendered in full; beyond this a table stops being a table.
MAX_CELL = 120


def render_cell(value):
    """One table cell, as text a reader can trust.

    Parameters
    ----------
    value : object
        Any scalar or container. None and the empty string read as
        :data:`MISSING` — a cell with nothing in it is indistinguishable
        from a rendering bug, so nothing renders as one. Booleans read as
        ``yes``/``no`` (a verdict, not a number), floats to 6 significant
        figures, and containers as their size — a table row is not a
        place to dump a payload.

    Returns
    -------
    str
        The cell, truncated at :data:`MAX_CELL` characters and with every
        ``|`` escaped so it cannot be read as a column boundary.
    """
    if value is None or (isinstance(value, str) and not value):
        return MISSING
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, float):
        return f"{value:.6g}"
    if isinstance(value, (list, tuple, set, frozenset)):
        text = f"({len(value)} item(s))"
    elif isinstance(value, dict):
        text = f"({len(value)} key(s))"
    else:
        text = str(value)
    if len(text) > MAX_CELL:
        text = text[:MAX_CELL] + "…"
    return text.replace("|", r"\|")


def pipe_table(columns, rows):
    """Render rows as a markdown pipe table over an ORDERED column list.

    Column order and row order are both preserved: a ledger is
    chronological and a ranked sheet is ranked, and neither survives an
    alphabetical sort.

    Parameters
    ----------
    columns : sequence of str
        The headers, in render order.
    rows : sequence of sequence
        One sequence of raw values per row, aligned to ``columns``; each
        value is passed through :func:`render_cell`.

    Returns
    -------
    list of str
        The header line, the separator, then one line per row.

    Raises
    ------
    ValueError
        A row's width disagrees with ``columns``. Rendering it anyway
        would attribute values to the wrong headings.
    """
    columns = [render_cell(column) for column in columns]
    lines = [
        "| " + " | ".join(columns) + " |",
        "|" + "---|" * len(columns),
    ]
    for index, row in enumerate(rows):
        cells = [render_cell(value) for value in row]
        if len(cells) != len(columns):
            raise ValueError(
                f"row {index} has {len(cells)} cell(s) for {len(columns)} "
                "column(s) — a table must not misattribute values"
            )
        lines.append("| " + " | ".join(cells) + " |")
    return lines
