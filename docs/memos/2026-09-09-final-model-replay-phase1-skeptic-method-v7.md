# Skeptic review - final-model replay Phase 1 method/API lens, v7

**Reviewer task:** `/root/phase1_method_skeptic_v7`
**Model/effort:** GPT-5.6 Terra, high
**Dispatch:** fresh sequential reviewer; no implementation edits.

## Reviewed state

Read root/pipeline `AGENTS.md`, skeptic-review Rules 1 and 7, ADR-0113, the complete Phase-1 plan, the full uncommitted diff from `c775ac5`, and skeptic reports v1-v6. I independently attacked single-snapshot grid/map handling, canonical keys/digests/JSON, immutable inventory/row state, all-row callback order and effects, numeric and 4096-digit boundaries, transactionality, deterministic order, HpoGrid/planner compatibility, and Phase-1 scope.

## Focused checks

```text
PYTHONDONTWRITEBYTECODE=1 /home/russell/dskit/.venv/bin/python -m pytest -q tests/pipeline/test_kinds_search.py
# 125 passed in 0.36s

/home/russell/dskit/.venv/bin/ruff check dskit/pipeline/kinds_search.py dskit/pipeline/planner.py dskit/pipeline/__init__.py tests/pipeline/test_kinds_search.py
# All checks passed!

git diff --check
# clean
```

## Findings

### Major - `CandidateInventory` validates one grid observation and freezes another

`CandidateInventory.__init__` validates `space.items()` at `dskit/pipeline/kinds_search.py:437-474`, then calls `_grid(space)` at `:486`, which reads `space[k]` again. The public check admits a `dict` subclass (`isinstance(space, dict)`), so a state-changing implementation can pass validation with `{"depth": [1]}` and supply a different value to `_grid`.

This probe constructs successfully even though the frozen inventory contains a value the validator explicitly refuses:

```python
class ChangingGrid(dict):
    def __init__(self):
        super().__init__(depth=[1])
    def __getitem__(self, key):
        return [float("nan")]

inventory = CandidateInventory(ChangingGrid())
# inventory.combinations == (mappingproxy({"depth": nan}),)
ledger = TrialLedger(inventory)
ledger.record({"depth": float("nan")}, 1)
ledger.digest  # bare ValueError: Out of range float values are not JSON compliant
```

The accepted inventory is neither the declared JSON-scalar grid nor a safe source for the promised deterministic ledger digest. Snapshot/validate/freeze one materialized, exact-builtin grid before any membership keys or digest are derived, and add a changing-grid regression. Merely re-running validation repeats the mutable observation.

### Major - duplicate concurrent `record()` calls consume one candidate twice

`TrialLedger.record()` checks `key in self._seen` at `kinds_search.py:643`, performs arbitrary evidence freezing at `:656-678`, and only then appends/adds at `:691-692`. There is no lock or reservation. Two callers can both pass the duplicate check, pause during their allowed `Mapping` evidence snapshot, and both append the same declared combination.

A two-thread probe with a Mapping.items() barrier produced errors == [], len(ledger._rows) == 2, len(ledger.rows) == 1, and ledger.is_complete() is False for a one-candidate inventory. The public rows projection silently collapses the duplicate while the internal count makes the ledger permanently incomplete, so the caller cannot serialize or correct/retry its only candidate. This violates ADR-0113's exactly-one-immutable-row contract and its transactional accepted/rejected-row contract. Make admission atomic across validation/freezing/commit (or provide an atomic reservation/rollback protocol) and test two concurrent identical records plus subsequent valid completion.

## Verdict

**FAIL - 0 Critical, 2 Major.** The v1-v6 fixes and focused checks are present, but one-snapshot grid validation and concurrent ledger transactionality still break core public contracts. Fix both with focused regressions, then dispatch a fresh independent skeptic; this reviewer has not edited implementation code.
