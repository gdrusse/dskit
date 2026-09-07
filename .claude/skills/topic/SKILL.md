---
name: topic
description: >-
  Run one child work topic end to end under a single slug — research it,
  run the pipeline document it motivates, let the journal record the
  execute, then memo the outcome. Use when the user asks to work a
  topic, take an effort from question to memo, or chain research → run
  → record → memo.
---

# Work one topic end to end (ADR-0110)

One kebab-case slug names the whole effort. Every artifact links by that
name: the research folder, the journal rows, the run directory, the
memo. This skill composes the existing skills — it adds no writer of
its own and writes no ledger by hand.

## 1. Fix the vocabulary first

- Pick the topic slug once: kebab-case, stable, chosen for the question.
  Revisiting the question later means a NEW dated file in the same
  folder — never a second slug for the same question, never an
  overwrite.
- Every phase below reuses that one slug verbatim.

## 2. Phase 1 — research

Follow `../record-research/SKILL.md` verbatim: draft outside
`docs/research/`, then record through the CLI from the child root:

```bash
python -m dskit.journal research "SHORT TITLE" \
  --topic <slug> --name <stem> --body-file <draft> --root .
```

Multi-agent work follows `../deep-research/SKILL.md` under the same
slug (`--name synthesis` for the task summary).

## 3. Phase 2 — run

Run the pipeline document the research motivates, from the child root:

```bash
python -m dskit.pipeline run configs/<doc>.json --asof <YYYY-MM-DD> --adapter <child>
# walkforward in place of run when the document carries that section
```

The journal hooks append the execute row themselves — never write
`actions.csv` or the generated decisioning README by hand. A topic that
motivated no run is a SKIP, reported as such (below), not a gap.

## 4. Phase 3 — memo

Follow `.cursor/skills/memo/SKILL.md` (repo-relative — `.claude/skills/`
carries no `memo/`, so `../memo/SKILL.md` does not resolve here) verbatim:
evidence from the run directory, the
config identity hash, and the topic's research files — never
conversation memory. Write `docs/memos/<slug>-<outcome>.md`; `## TL;DR`
first; failures and deliberately unrun work beside successes.

## 5. Report

State the slug, then one line per phase: the research file paths, the
run directory + identity hash, the actions row id, the memo path. Any
phase the work did not need is reported as **SKIPPED** with the reason —
never silently omitted.

## Never

- A second slug for the same question.
- Hand-writing anything a CLI writes (research files, `actions.csv`,
  the decisioning README). Memos are the exception — they ARE
  hand-authored, via the memo skill's flow.
- Ending without the report.
