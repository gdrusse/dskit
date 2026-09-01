---
name: refresh-child-journal
description: >-
  Bring a dskit child up to the ADR-0056 action journal. Use when a child
  lacks journal.json, when docs/decisioning/README.md is still a hand table,
  when adding docs/research/, when a pipeline/onboarding/live run refuses
  with "journal init", or when the user asks to refresh a child to current
  skeleton criteria.
---

# Refresh a child journal (ADR-0056)

Do this in the child root (`children/<name>/` or a graduated copy).

## 1. Marker

If `journal.json` is missing:

```bash
python -m dskit.journal init --root .
```

If it exists, do not re-init. Ensure `docs/research/.gitkeep` exists.

## 2. Historical rows

Past actions cannot be reconstructed perfectly. Seed `actions.csv` only
for known work, with notes exactly:

`retrospective; artifacts may be incomplete`

Do not invent Path to Production rows. Only the owner runs:

```bash
python -m dskit.journal promote <ID> --criteria empirical|judgemental|n/a
```

## 3. Keep evidence files

Leave existing `docs/decisioning/*.md` rationale files. The README is
**generated** — never hand-edit it. After CSV edits:

```bash
python -m dskit.journal render --root .
```

## 4. Docs the child must say

Update the child's `CLAUDE.md` and `README.md` layout trees to include
`journal.json`, `docs/decisioning/{actions.csv,path.csv,README.md}`,
`docs/research/`. State: four categories; hooks record acquire/execute;
research writes `docs/research/<slug>.md`; wrap `live.main` in
`dskit.journal.hooks.production`; path is owner-only.

## 5. Live wrap

If `live.py` has `main` and no `production(` call, wrap the process
(one row per process, not per tick).

## 6. Done when

- `journal.json` exists
- `python -m dskit.journal render --root .` succeeds
- child `pytest` is green (pytest does not write rows)
