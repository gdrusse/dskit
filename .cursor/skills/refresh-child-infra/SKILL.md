---
name: refresh-child-infra
description: >-
  Refresh a dskit child against the current child skeleton infrastructure. Use
  when a child needs retroactive folders, journal plumbing, documentation
  READMEs, agent instructions, or validation after skeleton infrastructure
  changes. Preserve the child's domain code, evidence, journal history, and
  owner-only Path rows.
---

# Refresh child infrastructure

Bring one existing child to the current infrastructure contract without
turning it into a fresh skeleton copy. Work from the child root.

## Scope and guardrails

- Read the child's `AGENTS.md`, `CLAUDE.md`, and `README.md` first.
- Compare only infrastructure with `children/_skeleton/`: `journal.json`,
  `docs/decisioning/`, `docs/explanations/`, `docs/memos/`,
  `docs/research/`, their README reminders, and layout/instruction prose.
- Do not copy or overwrite the child's package, configs, tests, evidence,
  research, memos, explanations, `actions.csv`, or `path.csv`.
- Never add, edit, migrate, or regenerate owner-only Path content. Only the
  human owner changes Path rows and `Current Work`.
- Preserve all historical journal rows. A generated README may show only the
  latest 10 Actions; that is display-only and never permits deletion.

## Refresh workflow

1. Inspect the current skeleton and list only missing or stale infrastructure.
   State the proposed changes before making non-trivial child-specific edits.
2. If `journal.json` is missing, run `python -m dskit.journal init --root .`.
   If it exists, do not re-init it.
3. Ensure these directories exist:
   `docs/decisioning/`, `docs/explanations/`, `docs/memos/`, and
   `docs/research/`.
4. Add missing folder READMEs from the skeleton's current intent:
   - `docs/explanations/README.md`: `record-explanation`
   - `docs/memos/README.md`: `memo`
   - `docs/research/README.md`: `record-research`
   Do not overwrite a child README with substantive child-specific content.
5. Ensure the child's `AGENTS.md`, `CLAUDE.md`, and README layout state:
   - the four documentation folders;
   - generated decisioning README and CSV store;
   - human-owner-only Path and Current Work;
   - full Path display, latest-10 Actions display, and append-only history.
6. Preserve all existing decision evidence. Run
   `python -m dskit.journal render --root .` only after confirming the
   existing ledger headers are supported; never hand-edit the generated README.
7. Run the child's focused test command, normally `python -m pytest tests -q`.

## Report

Report the child, infrastructure added or corrected, files deliberately left
untouched, and the focused test result. Explicitly say that journal and Path
history were preserved.
