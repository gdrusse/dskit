---
name: record-research
description: >-
  Record a dskit child research action through dskit.journal so a finding
  always gets a docs/research markdown file and an actions.csv row. Use
  whenever the user asks to research, investigate, look up, survey,
  compare approaches, fire a research agent, or write to docs/research/.
---

# Record research (ADR-0056)

`dskit.journal` is the only writer of `docs/research/`. A free-hand
markdown file there is a miss — no ledger row.

## Do this

1. Find the child root (`journal.json`). Work from there. If the marker
   is missing, run `python -m dskit.journal init --root .` first (or
   the refresh-child-journal skill).
2. Draft the finding **outside** `docs/research/` (temp file). Sections:
   Question, Finding, Sources. Title is a short step (<= 80 chars).
3. Record and write in one shot:

```bash
python -m dskit.journal research "SHORT TITLE" --body-file <draft> --root .
```

4. The command prints the new path and appends a **research** row.
   Confirm `docs/decisioning/README.md` lists it.

## Never

- Write, create, or move files under `docs/research/` yourself.
- Hand-edit `docs/decisioning/README.md` or `actions.csv`.
- Skip the CLI because the look-up was "just a note".

## Already exists

If `docs/research/<slug>.md` already exists, do not re-run `research`
(it refuses). Edit that file in place, then:

```bash
python -m dskit.journal record --category research --step "<slug>" \
  --inputs "<title>" --outputs docs/research/<slug>.md \
  --db-location docs/research/<slug>.md --root .
```

Path to production is owner-only (`journal promote`). Do not add path rows.
