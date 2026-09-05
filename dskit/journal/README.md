# dskit.journal

Per-child action ledger (ADR-0056). CSV is the store; each child's
`docs/decisioning/README.md` is generated and carries the process below.

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
   `python -m dskit.journal research "TITLE" --topic T --name N --body-file <draft>`.
   Writes `docs/research/<topic>/<YYYY-MM-DD>-<name>.md` and the row
   together. Default name is `synthesis`. No markdown in the research
   root. Never write that folder by hand. Skills: `record-research`
   and `deep-research` (Cursor, Claude, OpenCode).
3. **Execute** — `python -m dskit.pipeline run|walkforward`.
   **Automatic** after RECORD. Walk-forward is one row, not per fold.
4. **Production** — wrap `live.main` in
   `dskit.journal.hooks.production`. One row per process, not per tick.

The ledger is CSV, not a database. **Database Location** is a pointer
to that action's artifacts (onboarding root, run dir, research file).
MLflow / the asset store hold their own records when used.

**Path to Production** is human-owner-only: only the owner may add or edit a
row, including **Current Work**. Agents and hooks never write it. Every row
has a short label, purpose, relevant evidence files (pipeline run, research
markdown, or other material evidence), and **LOCKED** (`Y` / `N`). Pytest
does not record. A child without `journal.json` refuses acquire / run / live.

## 60-second path

```bash
python -m dskit.journal init --root .          # once per child (skeleton ships it)
python -m dskit.pipeline run configs/x.json --asof 2026-01-01   # auto-records
python -m dskit.journal research "why LightGBM" --topic why-lightgbm --body-file finding.md
python -m dskit.journal promote A0001 --criteria empirical --label baseline --purpose compare --relevant-files pipeline_runs/base --locked N  # owner only
```

`DSKIT_JOURNAL_ROOT` overrides locate.

## Layout in the child

```
journal.json                 # walk-up marker
docs/decisioning/
  actions.csv                # the ledger (write here)
  path.csv                   # owner Path: id, label, purpose, relevant files,
                             # LOCKED Y/N, Current Work, and criteria
  README.md                  # GENERATED — do not edit
  <evidence>.md              # rationale files, listed in README
docs/research/<topic>/       # dated notes + <date>-synthesis.md (CLI-only)
```

The generated decisioning README displays the complete Path and only the
latest 10 Actions. This is display-only: both CSV ledgers retain all history.
Legacy two-column Paths remain read-only until the human owner explicitly
migrates them; `promote` never rewrites them.

## Contents

```
dskit/journal/
├── __init__.py     public surface
├── __main__.py     init / record / research / promote / render / exec
├── base.py         errors, UTC, CSV headers
├── model.py        Action / PathRow / JournalConfig
├── locate.py       walk-up + init + uninitialized-child refusal
├── store.py        atomic CSV
├── render.py       CSV → README.md (PROCESS text)
├── record.py       append_action / promote; pytest skip
├── hooks.py        record_* + production()
├── research.py     docs/research/<topic>/<date>-<name>.md
├── README.md
└── CLAUDE.md
```
