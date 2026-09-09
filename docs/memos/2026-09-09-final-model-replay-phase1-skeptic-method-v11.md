# Skeptic review - final-model replay Phase 1 method/API lens, v11

**Reviewer task:** `/root/phase1_method_skeptic_v11`
**Model/effort:** GPT-5.6 Terra, high
**Dispatch:** fresh sequential reviewer; no implementation edits.

## Reviewed state and method

Read the root and pipeline rules, skeptic-review Rules 1 and 7, ADR-0113,
the complete final-model/replay plan, the full current uncommitted diff from
`c775ac5`, and skeptic reports v1-v10. Independently attacked the current
immutable-state-swap ledger under interrupts, concurrent/reentrant admission,
read snapshots, pending/seen invariants, duplicates, callback/refusal order,
JSON/digest/numeric contracts, and public docs/tests. The change remains
generic Phase 1 only: it does not perform child HPO, market reads, persistence,
replay, or monitoring.

## Focused checks

```text
PYTHONDONTWRITEBYTECODE=1 /home/russell/dskit/.venv/bin/python -m pytest -q tests/pipeline/test_kinds_search.py
# 133 passed in 0.38s

/home/russell/dskit/.venv/bin/ruff check dskit/pipeline/kinds_search.py dskit/pipeline/planner.py dskit/pipeline/__init__.py tests/pipeline/test_kinds_search.py
# All checks passed!

git diff --check c775ac5
# clean
```

The following read-only WSL probes changed no repository files.

## Major - an interrupt between reservation and the protected block permanently strands the candidate

`TrialLedger.record()` reserves at `dskit/pipeline/kinds_search.py:641`,
but its `try`/`except BaseException` does not begin until line 642.
Disassembly confirms the gap: the `_reserve` call returns at bytecode offset
114, the line-642 `NOP` is at 116, and the exception table begins only at
118. A `KeyboardInterrupt` delivered at that line bypasses
`_release_if_uncommitted` entirely.

The deterministic `sys.settrace` probe raised at exactly that post-reserve,
pre-protected line and produced:

```text
KeyboardInterrupt reserve-to-try gap
() ((), frozenset(), frozenset({'{"depth":1}'})) False
ValueError {'depth': 1} has already recorded or is recording a trial in this ledger
```

There is no row, no seen membership, and no way to retry the only declared
candidate. This violates ADR-0113's transactional/retryable ledger contract.
The v10 test covers a subclass interrupting `_commit`; it does not exercise
this caller-frame admission gap.

Protect reservation itself and make cleanup ownership-aware: a plain broad
`finally` that removes a key after a duplicate refusal could release another
caller's live reservation. A per-admission token, installed with the pending
state in the atomic state swap and released only when that same token owns it,
can cover every post-swap interrupt safely. Add a deterministic regression at
this boundary, then successfully retry the candidate.

## Major - member-equivalent mutable scalar subclasses are retained by reference in an allegedly immutable row

The inventory refuses a mutable numeric subclass in its own grid, but
`TrialLedger.record()` accepts it in `overrides`: `_combo_key()` only
serializes the caller mapping for membership (`kinds_search.py:123-165`),
and `_freeze_trial()` stores that shallow snapshot directly as
`MappingProxyType(snapshot)` (`:717-722`). A builtin inventory value and
an `int` subclass both serialize as `1`, so the latter passes membership
