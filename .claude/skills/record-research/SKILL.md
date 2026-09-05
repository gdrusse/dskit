---
name: record-research
description: >-
  Record a dskit child research action through dskit.journal so a finding
  always gets a docs/research markdown file and an actions.csv row. Use
  whenever the user asks to research, investigate, look up, survey,
  compare approaches, fire a research agent, or write to docs/research/.
---

# Record research (ADR-0056 / ADR-0096)

`dskit.journal` is the only writer of `docs/research/`. A free-hand
markdown file there is a miss — no ledger row.

Every task uses a **topic folder**. No markdown in `docs/research/`
root (legacy flat files stay; new writes never land there).

```
docs/research/<topic>/<YYYY-MM-DD>-<name>.md
docs/research/<topic>/<YYYY-MM-DD>-synthesis.md
```

## Do this

1. Find the child root (`journal.json`). Work from there. If the marker
   is missing, run `python -m dskit.journal init --root .` first (or
   the refresh-child-journal skill).
2. Draft **outside** `docs/research/` (temp files). Sections: Question,
   Finding, Sources. Title is a short step (<= 80 chars).
3. Pick a stable `--topic` slug for the question. Record each file:

```bash
python -m dskit.journal research "SHORT TITLE" \
  --topic <topic> --name synthesis --body-file <draft> --root .
python -m dskit.journal research "SHORT TITLE" \
  --topic <topic> --name <subagent-stem> --body-file <draft> --root .
```

4. The command prints the new path and appends a **research** row.
   Confirm `docs/decisioning/README.md` lists it.

## Deep / multi-agent work

Follow `deep-research`. Subagent notes (`--name` distinct, citations
inside) and the task synthesis (`--name synthesis`) share one topic
folder. Same-day collisions get a UTC-time stem automatically.

## Topic already exists

Do **not** overwrite. Write a new dated file in that folder
(`--name synthesis` or a new stem). Do not re-run `research` expecting
the old slug path. Do not hand-edit `actions.csv`.

Path to production is owner-only (`journal promote`). Do not add path rows.

## Never

- Write, create, or move files under `docs/research/` yourself.
- Put markdown in the `docs/research/` root.
- Hand-edit `docs/decisioning/README.md` or `actions.csv`.
- Skip the CLI because the look-up was "just a note".
