Default answer: outcome first, max 5 lines. Expand only if I ask.

# AGENTS.md — dskit.journal

Orientation for an agent working inside this package. Read
[README.md](README.md) first.

## Conventions

- **CSV is the store; markdown is generated.** Never hand-edit
  `docs/decisioning/README.md`. Append `actions.csv` or `promote`.
- **Path is owner-only.** Hooks never write `path.csv`.
- **Default-deny** on `journal.json` and CSV rows (`from_obj`).
- **Never import pipeline, onboarding, or assets.** Callers
  function-import the hooks. Journal failure refuses the parent
  (unlike tracking sinks).
- **Pytest does not record** (`PYTEST_CURRENT_TEST`) unless
  `DSKIT_JOURNAL_TESTS=1`.
- Locate: marker found → use it; child-shaped without marker →
  refuse; neither → no-op.

## Extension points

- New category: ADR, then `CATEGORIES` + tests. Do not add a fifth
  without one — validate/certify/publish are acquire; bakeoffs are
  execute.
- Evidence markdown stays beside the generated README; render lists
  `*.md` other than README.

## Gotchas

- `path.csv` stores `id, criteria` only. Render JOINs the rest.
  Copying category/step into path.csv is the drift this avoids.
- Walk-forward records **one** execute row (the summary), not each
  fold — `run_document(..., journal=False)` from `_run_folds`.
- Production is one row per process. Do not record per tick.
- **Every write is serialized** by `base.locked(root.decisioning)` — an
  exclusive `flock` on `.journal.lock`, held across read, id
  allocation, rewrite, and render. Allocating an id outside it loses a
  row when two agents overlap. Re-entrant within one thread, so
  `record` nesting into `store` is fine. It waits, then refuses after
  `DSKIT_JOURNAL_LOCK_TIMEOUT` (default 60s).
- `step` <= 80 characters.
- **Research is CLI-only.** Never Write `docs/research/` yourself.
  `python -m dskit.journal research "title" --body-file <draft>` writes
  the markdown and the row. Skills: `.cursor/skills/record-research/`
  and `.cursor/skills/record-research/` (Codex: `/research`).
- **The process text is `render.PROCESS`.** The generated child README
  and `journal/README.md` restate it; `test_store.py` pins the copies.

## Contents

```
dskit/journal/
├── __init__.py __main__.py base.py model.py locate.py
├── store.py render.py record.py hooks.py research.py
├── README.md AGENTS.md
```