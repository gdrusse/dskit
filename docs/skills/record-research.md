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
python -m dskit.journal research "SHORT TITLE" --body-file <draft> \
  --topic <topic-slug> --name <name-slug> --root .
```

   `--topic` is the folder under `docs/research/` (default: a slug of
   TITLE). `--name` is the file stem after the date (default `synthesis`).
   Both are optional; the file lands at
   `docs/research/<topic>/<date>-<name>.md`.

4. The command prints the new path and appends a **research** row.
   Confirm `docs/decisioning/README.md` lists it.

## Never

- Write, create, or move files under `docs/research/` yourself.
- Hand-edit `docs/decisioning/README.md` or `actions.csv`.
- Skip the CLI because the look-up was "just a note".

## Already exists

`research` does not refuse when a file at that exact date/topic/name path
already exists — a same-day second write on the same topic auto-renames
using a UTC-time-based stem instead. It only raises if that renamed path
also collides (rare). To edit an **existing** research file in place
(not to work around a collision), edit the file directly, then:

```bash
python -m dskit.journal record --category research --step "<slug>" \
  --inputs "<title>" --outputs docs/research/<topic>/<date>-<name>.md \
  --db-location docs/research/<topic>/<date>-<name>.md --root .
```

Path to production is owner-only (`journal promote`). Do not add path rows.
