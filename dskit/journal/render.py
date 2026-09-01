"""Project the CSV ledgers into ``docs/decisioning/README.md``.

Operators read the markdown on GitHub. Writers append CSV. This file
is overwritten on every record/promote — do not edit it by hand.
"""

from __future__ import annotations

from .base import atomic_write_text
from .store import read_actions, read_path

__all__ = ["PROCESS", "escape_cell", "render", "render_text"]

#: The process every child's generated README carries. ``journal/README.md``
#: restates it; ``test_store.py`` pins the two copies.
PROCESS = """# Decisioning

CSV is the store (`actions.csv`, `path.csv`). This README is **generated**
— do not edit it. Append a CSV row or run `python -m dskit.journal promote`.

## Process

Many things get tried. The Actions table is the full tape. Path to
Production is the owner-selected linear chain (a subset of those IDs).

```
acquire  →  research  →  execute  →  production
 pull         finding      fit          live
```

1. **Acquire** — `python -m dskit.onboarding` `register-source` /
   `acquire --mode backfill|live` / `validate` / `certify` / `publish`.
   `watch` is one row per process, not per pull. **Automatic.**
2. **Research** — only
   `python -m dskit.journal research "TITLE" --body-file <draft>`.
   Writes `docs/research/<slug>.md` and the row together. Never write
   that folder by hand. Skills: `record-research` (Cursor + Claude;
   Claude `/research`).
3. **Execute** — `python -m dskit.pipeline run|walkforward`.
   **Automatic** after RECORD. Walk-forward is one row, not per fold.
4. **Production** — wrap `live.main` in
   `dskit.journal.hooks.production`. One row per process, not per tick.

The ledger is CSV, not a database. **Database Location** is a pointer
to that action's artifacts (onboarding root, run dir, research file).
MLflow / the asset store hold their own records when used.

**Path to Production** is owner-only:
`python -m dskit.journal promote <ID> --criteria empirical|judgemental|n/a`.
Hooks never write it. Pytest does not record. A child without
`journal.json` refuses acquire / run / live.
"""


def escape_cell(value):
    """Make a CSV value safe for a GitHub markdown table cell.

    Parameters
    ----------
    value : str

    Returns
    -------
    str
    """
    text = "" if value is None else str(value)
    return text.replace("|", "\\|").replace("\n", " ").replace("\r", "")


def _table(headers, rows):
    """Render a markdown table; empty body still keeps the header."""
    lines = [
        "| " + " | ".join(headers) + " |",
        "|" + "|".join("---" for _ in headers) + "|",
    ]
    for row in rows:
        lines.append("| " + " | ".join(escape_cell(c) for c in row) + " |")
    if not rows:
        lines.append("| " + " | ".join("—" for _ in headers) + " |")
    return "\n".join(lines)


def render_text(actions, path_rows, evidence=()):
    """Build the README body.

    Parameters
    ----------
    actions : sequence of Action
    path_rows : sequence of PathRow
    evidence : sequence of str, optional
        Relative names of evidence markdown files to list.

    Returns
    -------
    str
    """
    by_id = {row.id: row for row in actions}
    action_table = _table(
        [
            "ID",
            "Category",
            "Step",
            "Execution Date",
            "Relevant Inputs",
            "Relevant Outputs",
            "Database Location",
            "Notes",
        ],
        [
            [
                row.id,
                row.category,
                row.step,
                row.executed_at,
                row.inputs,
                row.outputs,
                row.db_location,
                row.notes,
            ]
            for row in actions
        ],
    )
    path_table = _table(
        ["ID", "Category", "Step", "Decision Criteria", "DB Location"],
        [
            [
                row.id,
                by_id[row.id].category if row.id in by_id else "?",
                by_id[row.id].step if row.id in by_id else "?",
                row.criteria,
                by_id[row.id].db_location if row.id in by_id else "?",
            ]
            for row in path_rows
        ],
    )
    parts = [
        PROCESS,
        "## Actions",
        "",
        action_table,
        "",
        "## Path to Production",
        "",
        path_table,
        "",
    ]
    if evidence:
        parts += [
            "## Evidence",
            "",
            "Rationale files (not generated):",
            "",
        ]
        parts += [f"- [{name}]({name})" for name in evidence]
        parts.append("")
    return "\n".join(parts)


def _evidence_names(root):
    """Markdown files in decisioning/ other than the generated README."""
    import os

    names = []
    directory = root.decisioning
    if not os.path.isdir(directory):
        return names
    for name in sorted(os.listdir(directory)):
        if name == "README.md" or not name.endswith(".md"):
            continue
        names.append(name)
    return names


def render(root):
    """Rewrite ``docs/decisioning/README.md`` from the CSVs.

    Parameters
    ----------
    root : JournalRoot
    """
    text = render_text(
        read_actions(root),
        read_path(root),
        evidence=_evidence_names(root),
    )
    atomic_write_text(root.readme, text if text.endswith("\n") else text + "\n")
