# Skeptic review - final-model replay Phase 1 method/API lens, v9

**Historical advisory artifact.** This report records the original reviewed
state. The implementation changed after this review; it is not a verdict on
the current worktree and must not be used as a current approval/failure gate.

**Reviewer task:** `/root/phase1_method_skeptic_v9`
**Model/effort:** GPT-5.6 Terra, high
**Lens:** reserve-prepare-commit concurrency; reservation rollback for every
failure including `BaseException`; same/different-candidate races; reentrancy;
snapshot/digest reads; canonical ordering; immutable JSON evidence; numeric
bounds; and the prior Phase-1 contracts.
**Dispatch:** fresh sequential reviewer; no implementation edits.

## Originally reviewed state

Read the root and pipeline rules, ADR-0113, complete Phase-1 plan, full
uncommitted diff from `c775ac5`, and skeptic reports v1-v8. The reviewed
implementation had the v8 reservation and lock-snapshot changes in
`dskit/pipeline/kinds_search.py`.

Focused checks in that original state:

```text
PYTHONDONTWRITEBYTECODE=1 /home/russell/dskit/.venv/bin/python -m pytest -q tests/pipeline/test_kinds_search.py
# 130 passed in 0.35s

/home/russell/dskit/.venv/bin/ruff check dskit/pipeline/kinds_search.py dskit/pipeline/planner.py dskit/pipeline/__init__.py tests/pipeline/test_kinds_search.py
# All checks passed!

git diff --check c775ac5
# clean
```

## Major - interruption during preparation leaked a reservation

In the originally reviewed source, `TrialLedger.record()` reserved the key,
then `_freeze_trial()` traversed caller-owned `Mapping.items()`. Its rollback
handled only `Exception`, so `KeyboardInterrupt`, `SystemExit`, or another
`BaseException` bypassed cleanup. This violated the retryable transactional
contract in ADR-0113: no row was committed, but the candidate remained pending.

Exact read-only reproduction used a one-candidate inventory and diagnostics
mapping whose `items()` raised `KeyboardInterrupt("interrupt during evidence
freeze")`:

```text
KeyboardInterrupt interrupt during evidence freeze
pending {'{"depth":1}'} rows 0 complete False
retry ValueError {'{"depth": 1}'} has already recorded or is recording a trial in this ledger
```

The required remedy was a BaseException-safe reservation release and a
regression that interrupts preparation, then successfully retries the same
candidate, followed by fresh independent review.

## Original verdict

**FAIL - 0 Critical, 1 Major.** This reviewer-owned Rule-7 trace is retained
only as advisory history because the implementation has since changed.
