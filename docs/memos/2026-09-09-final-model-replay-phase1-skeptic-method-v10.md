# Skeptic review — final-model replay Phase 1 method/API lens, v10

**Reviewer task:** `/root/phase1_method_skeptic_v10`
**Model/effort:** GPT-5.6 Terra, high
**Dispatch:** fresh sequential reviewer; no implementation edits.

## Reviewed state and method

Read the root and pipeline rules, skeptic-review Rules 1 and 7, ADR-0113,
the complete final-model plan, the full uncommitted diff from `c775ac5`, and
skeptic reports v1–v9. I independently exercised the public inventory,
ledger, and selector contracts, concentrating on reservation/rollback under
`BaseException`, same/different candidate races, reentrancy, lock-backed
snapshots, immutable canonical JSON/digests, selection callbacks/order, and
the numeric/refusal boundary. The Phase-1 placement remains generic and does
not perform child HPO, replay, market reads, persistence, or monitoring.

## Focused checks

```text
PYTHONDONTWRITEBYTECODE=1 /home/russell/dskit/.venv/bin/python -m pytest -q tests/pipeline/test_kinds_search.py
# 131 passed in 0.38s

/home/russell/dskit/.venv/bin/ruff check dskit/pipeline/kinds_search.py dskit/pipeline/planner.py dskit/pipeline/__init__.py tests/pipeline/test_kinds_search.py
# All checks passed!

git diff --check c775ac5
# clean
```

## Major — interruption in the commit window permanently corrupts a ledger

`TrialLedger.record()` releases a reservation for `BaseException` only while
`_freeze_trial()` runs (`dskit/pipeline/kinds_search.py:647-652`). Its commit
is three separately interruptible bytecode operations: append the row at
`:654`, add the key to `_seen` at `:655`, then remove `_pending` at `:656`.
An interrupt after append and before `_seen` leaves an uncommitted-looking row
plus a permanent reservation. `is_complete()` then reports true from the row
count, although `rows` projects the row and a retry is refused as still
recording. This violates ADR-0113's transactional, retryable exactly-one-row
contract and leaves an irreversible partial state.

Read-only WSL reproduction used `sys.settrace` to raise `KeyboardInterrupt`
immediately before source line 655 (therefore after line 654 has appended):

```text
KeyboardInterrupt commit interrupt
rows 1 seen set() pending {'{"depth":1}'} complete True
ValueError {'depth': 1} has already recorded or is recording a trial in this ledger
```

The v9 preparation-interruption regression covers an interruption during
caller-owned evidence freezing, not this post-append commit window. Make the
commit itself exception-safe/atomic with respect to the reservation (including
an interruption after each mutation), restore a consistent state on every
failure, and add deterministic regressions for an interrupt after append and
after `_seen` update before rerunning fresh reviewers.

## Minor — malformed selector direction can leak arbitrary caller exceptions

`OneStandardErrorSelector.__init__` evaluates `select not in ("min", "max")`
without a type gate or refusal boundary (`kinds_search.py:742-746`). A custom
object whose `__eq__` raises `ZeroDivisionError` escapes construction rather
than becoming the class's named `ValueError` refusal. The read-only probe
produced `ZeroDivisionError: division by zero`. This is not the shipping
blocker above, but a focused malformed-`select` regression and exact-string
validation would make the public boundary consistent.

## Verdict

**FAIL — 0 Critical, 1 Major, 1 Minor.** The v9 prepare-phase
`BaseException` cleanup is present, but the commit phase remains
interruption-unsafe. Fix the Major with regression coverage, then restart the
fresh skeptic loop; this reviewer has not edited implementation code.
