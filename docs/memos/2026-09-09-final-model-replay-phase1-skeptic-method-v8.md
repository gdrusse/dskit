# Skeptic review - final-model replay Phase 1 method/API lens, v8

Reviewer: /root/phase1_method_skeptic_v8; GPT-5.6 Terra, high.
Fresh sequential reviewer; no implementation edits.

Read root/pipeline AGENTS, skeptic Rules 1 and 7, ADR-0113, the complete Phase-1
plan/diff from c775ac5, and v1-v7. Covered grid/map snapshots, immutable JSON
evidence/digests, numeric bounds, transactionality, read locking, reentrancy,
deadlock, determinism, and HpoGrid/planner compatibility.

Focused check: 127 passed in 0.40s for tests/pipeline/test_kinds_search.py;
Ruff passed on all changed Python files; git diff --check c775ac5 clean.
Read-only WSL probes changed no repository files.

## Major - reentrant Mapping evidence duplicates a candidate

record holds RLock over _record (kinds_search.py:624-627), which calls
caller-owned Mapping traversal through _frozen_json (:671-693, :245-260).
The same thread may reenter record before the outer append/_seen update
(:694-707). A one-candidate diagnostics Mapping called record(depth=1,
score=2) inside items(), then outer record wrote depth=1 score=1. It completed:

    len(_rows), len(_seen), len(rows), is_complete()
    # 2 1 1 False
    to_obj()
    # ValueError: cannot serialize an incomplete TrialLedger

rows hides one duplicate (:596-602), but raw count leaves the ledger permanently
incomplete. This violates ADR-0113 exactly-one-row and transactionality.
Reserve before caller traversal, release on refusal, commit once; test reentry,
rollback, and concurrent duplicates.

## Major - lock-held Mapping callback deadlocks across threads

The same mutation lock spans Mapping.items. A callback that starts a worker and
waits for it deadlocks when that worker calls record. A bounded probe had its
worker record another declared candidate:

    worker completed while items runs: False
    worker completed after outer record: True complete: True

The worker is blocked until outer record returns; unbounded waiting deadlocks.
Mapping evidence is accepted API shape. rows, to_obj, and digest also have no
lock-backed snapshot during admission. Freeze caller data outside the lock with
the reservation protocol and snapshot reads under lock before serialization.

## Minor - malformed dict-subclass grid key leaks TypeError

CandidateInventory snapshots space.items (:433-451), but name membership in
space_snapshot (:445) sees an unhashable key before validation. A subclass
yielding ([], [1]) raises bare TypeError. Validate entry shape/string keys first
and raise ValueError.

## Verdict

FAIL - 0 Critical, 2 Major, 1 Minor. Fix the Majors with regressions, then
restart fresh skeptic review. Reviewer-owned Rule-7 trace.
