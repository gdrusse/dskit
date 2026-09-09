# Final-model replay Phase 1 recovery — implementation memo

## TL;DR

Phase 1 of the final-model replay plan (ADR-0113's generic search-evidence
framework — a reusable way to record every hyperparameter trial tried and
prove which one won, in `dskit/pipeline/kinds_search.py`) is now committed,
reviewed, and pushed. A skeptic review had found three real design bugs; all
three are fixed at the design level (not patched around), verified by two
independent fresh reviewers across two review lenses, with one correction
round in between. This is a generic, stdlib-only pipeline building block —
no market data, no model, no child-specific logic. Gates 1–7 of the larger
plan have not started.

## Execution contract

- **Branch:** `claude/phase1-recovery-seven-gates-ao4zdj`, tracking
  `origin/claude/phase1-recovery-seven-gates-ao4zdj`.
- **Base:** branched from `origin/main` at `519cde0` (unchanged throughout —
  confirmed by `git merge-base HEAD origin/main` still equalling `519cde0`
  at write time, so no rebase was needed to land cleanly).
- **What changed:** `dskit/pipeline/kinds_search.py` (the framework),
  `tests/pipeline/test_kinds_search.py` (unit tests), `docs/architecture/decision-log.md`
  (ADR-0113), and `dskit/pipeline/{README,AGENTS,CLAUDE}.md` (package docs).
  Nothing under `docs/decisioning/`, `dskit/journal/`, or any `children/`
  path was touched.
- **Commits (`519cde0..HEAD`):**
  - `bb362fd` — the framework's first version, recovered from
    `origin/wip/final-model-replay-phase1-20260909` (written by a different
    model, `gpt-5`, in an earlier session on a different machine; that
    session's own review process ran 22 correction rounds without
    converging — see "What was recovered" below).
  - `2631282` — fixed the three Major findings from that session's final
    skeptic report.
  - `90304e1` — retained report: fresh method/API-contract reviewer, PASS.
  - `b217e5f` — fixed four Major findings from a fresh architecture/
    governance reviewer (correction cycle 1 of the bounded 2-cycle process).
  - `31d8a29`, `83fac39` — retained reports: both reviewers re-run fresh on
    the corrected code (correction cycle 2), both PASS.

## What was recovered

This session was asked to recover "Phase 1" work described as in-progress on
a different (WSL2) machine. That machine's own files were not reachable from
this session. Investigation found the actual work already pushed to
`origin/wip/final-model-replay-phase1-20260909` (commit `bb362fd`, authored
`gpt-5 <gpt-5@openai.com>`) — a different AI model than the one performing
this recovery. That branch also carried 22 versioned skeptic-review memo
files (`docs/memos/2026-09-09-final-model-replay-phase1-skeptic-method-v2.md`
through `-v22.md`, plus a `-final.md`), evidence of a review loop that ran
well past this project's own two-correction-cycle bound without reaching a
PASS. Only the final report (`-final.md`) was used as this session's starting
point, per the task's own instruction to treat it as the retained baseline;
the intermediate `v2`–`v22` files are historical artifacts of that prior
session and were left in place rather than deleted, since they are real
review history, not something this session produced or is authorized to
prune.

## What the three original findings were, and how each was fixed

All three are in `dskit/pipeline/kinds_search.py`; full detail and file:line
references are in the four retained reviewer reports (linked below).

1. **`SelectionRecord` had no real front door.** Its constructor accepted
   any JSON-shaped dictionary — `SelectionRecord({})` used to succeed, and
   only failed later, with a bare `KeyError`, when code tried to read a
   field that was never actually there. **Fix:** the constructor now always
   refuses. The only way to get a `SelectionRecord` is through
   `OneStandardErrorSelector.select()`, which builds one through an internal
   factory that checks the exact 9 fields a record must have and computes
   both of its identity digests directly from the real ledger — never from
   a value a caller could hand it.

2. **Mixing number and text "simplicity" labels could produce an
   unsubstantiated order.** The code let a caller rank candidate models by
   any of a number, a piece of text, or a nested combination of those — but
   comparing a number to a piece of text with Python's plain `<` either
   raises an error or (for some shapes) silently claims a false tie.
   **Fix:** added one explicit, documented rule for how any two of these
   labels compare to each other, no matter their shape (numbers, then text,
   then nested combinations, in that order) — recorded in ADR-0113 as the
   canonical rule, not a private implementation detail.

3. **Thread-safety and what happens if a trial is interrupted mid-write were
   real but undocumented.** The code already used a lock and a careful
   "reserve, then write, then commit" sequence, but nothing said so in
   writing, and untested behavior is not a guarantee. **Fix:** the exact
   contract — two callers racing for the same result never both win; an
   interrupted write can be safely retried; a keyboard interrupt or similar
   still stops the program immediately, it is never silently absorbed — is
   now written into the code's own documentation and proven by tests that
   actually run two threads at once.

## Review evidence

Two fresh reviewers, run one at a time (never in parallel), covering two
different lenses:

- **Method/API-contract lens** — is the public interface itself sound?
  - Cycle 1: `docs/memos/2026-09-09-final-model-replay-phase1-skeptic-method-recovery.md` — PASS, 0 Critical, 0 Major.
  - Cycle 2 (re-run because the code changed after cycle 1's architecture
    findings were fixed): `docs/memos/2026-09-09-final-model-replay-phase1-skeptic-method-recovery-2.md` — PASS, 0 Critical, 0 Major.
- **Architecture/integration/governance lens** — does this fit the repo's
  own rules for structure, documentation, and duplication?
  - Cycle 1: `docs/memos/2026-09-09-final-model-replay-phase1-skeptic-architecture-recovery.md` — FAIL, 0 Critical, 4 Major (a documentation file left out of sync, a missing boundary test, a missing regression a still-earlier review had already asked for, and a docstring formatting rule violated). All four were fixed in commit `b217e5f`.
  - Cycle 2: `docs/memos/2026-09-09-final-model-replay-phase1-skeptic-architecture-recovery-2.md` — PASS, 0 Critical, 0 Major, confirming each of the four fixes actually closed the gap it was meant to close, not merely looked fixed.

This used exactly one correction cycle before both lenses passed together —
within the process's 2-cycle bound, so no design adjudication was needed.

## Verification performed

Run on every commit in this sequence (never the full suite, per the task's
own instruction):

```
python3 -m pytest -q tests/pipeline/test_kinds_search.py tests/pipeline/test_planner.py \
    tests/pipeline/test_purity.py tests/pipeline/test_method_lengths.py
python3 -m ruff check dskit/pipeline/kinds_search.py tests/pipeline/test_kinds_search.py
git diff --check
```

Final state: **190 passed**, Ruff **all checks passed**, `git diff --check`
clean. (169 tests at the original recovery point, 187 after the fix commit
`2631282` added regressions for the three original findings, 190 after
`b217e5f` added the three regressions the architecture reviewer required.)
The concurrency regression class (`TestTrialLedgerConcurrencyContract`) was
independently re-run 5x standalone by one reviewer specifically to rule out
timing flakiness; stable every time.

## Deliberately not run

- The full test suite (`python -m pytest -q` with no path) — the task
  explicitly scoped verification to the four focused files above.
- No empirical search, HPO run, replay, or market-data read of any kind —
  ADR-0113 is explicit that Phase 1 is generic infrastructure only.
- Nothing under `children/` was built, run, or touched.

## Artifacts

- Code: `dskit/pipeline/kinds_search.py` — `CandidateInventory`,
  `TrialLedger`, `OneStandardErrorSelector`, `SelectionRecord`.
- Design record: ADR-0113, `docs/architecture/decision-log.md` (includes the
  three "Phase 1 recovery, 2026-09-09" amendment paragraphs naming exactly
  which finding each design change resolves).
- Package docs: `dskit/pipeline/README.md`, `AGENTS.md`, `CLAUDE.md` (all
  three now carry matching ADR-0113 orientation content and Contents-tree
  entries).
- Tests: `tests/pipeline/test_kinds_search.py`.
- Four retained reviewer reports, linked above under "Review evidence".

## Reproducibility and handoff

Anyone can reproduce this state from `origin/claude/phase1-recovery-seven-gates-ao4zdj`
at commit `83fac39` and re-run the exact verification commands above. The
branch has not been merged into `main`; it is pushed and ready for a
maintainer to open a PR or merge directly.

**Next:** Gate 1 (Phase 0 closeout) has not started. This memo covers only
Phase 1 of the plan named in the task
(`children/intraday_equities/docs/plans/2026-09-08-final-model-replay-and-monitoring.md`);
Gates 1 through 7 remain, each requiring its own inventory-before-building
pass, its own TDD cycle, and its own two-reviewer close, per the task's own
rules for every gate.
