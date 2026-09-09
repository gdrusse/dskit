# Skeptic review - final-model replay Phase 1 method/API lens, v19

**Reviewer task:** `/root/phase1_method_skeptic_v19`
**Model/effort:** GPT-5.6 Terra, high
**Dispatch:** fresh sequential reviewer; no implementation edits.

## Scope and evidence

Read the root and pipeline rules, skeptic-loop rules, ADR-0113, the complete
final-model/replay plan, full Phase-1 diff from `c775ac5`, architecture review,
and method reports v1-v18. Rechecked the public inventory, ledger, selector,
HpoGrid/planner, exports, docs, scalar/digest, hostile traversal, interruption,
and concurrent-admission paths.

```text
PYTHONDONTWRITEBYTECODE=1 /home/russell/dskit/.venv/bin/python -m pytest -q \
  tests/pipeline/test_kinds_search.py tests/pipeline/test_purity.py
# 166 passed in 3.19s

/home/russell/dskit/.venv/bin/ruff check dskit/pipeline/kinds_search.py \
  dskit/pipeline/planner.py dskit/pipeline/__init__.py \
  tests/pipeline/test_kinds_search.py
# All checks passed!

git diff --check c775ac5
# clean
```

The v18 HpoGrid repair is present: malformed or huge grid scalars now yield
stable validator problems without rendering caller values, and the focused
tests cover the 5000-digit and hostile-`repr` paths.

## Finding

### Major - `TrialLedger` accepts and serializes a negative standard error despite ADR-0113's declared refusal

ADR-0113 declares that all public values refuse negative standard errors
([decision-log.md:6125-6128](../architecture/decision-log.md#L6125)).
`TrialLedger.record()` freezes an `se` field through `_frozen_number()`
([kinds_search.py:775-800](../../dskit/pipeline/kinds_search.py#L775)), which
checks exact type/finiteness but has no non-negative check
([kinds_search.py:313-323](../../dskit/pipeline/kinds_search.py#L313)). It
therefore commits and exposes a complete canonical ledger containing invalid
uncertainty:

```text
ledger = TrialLedger(CandidateInventory({"depth": [1]}))
ledger.record({"depth": 1}, score=1.0, se=-0.25)
ledger.to_obj()["rows"][0]["se"]
# -0.25
ledger.digest
# returns a SHA-256 digest
```

`OneStandardErrorSelector.select()` eventually rejects that value at
[kinds_search.py:904-910](../../dskit/pipeline/kinds_search.py#L904), but that
does not repair the ledger's own public admission/serialization contract: a
caller may persist, hand off, or digest a complete ledger before selection.
The existing test suite checks non-finite `se` and selector-time negativity,
but has no ledger-admission regression for finite negative `se`.

**Required correction:** have `TrialLedger.record()` reject a negative exact
builtin finite `se` before committing/reserving durable evidence (with a
regression proving the candidate remains retryable), then rerun fresh
independent skeptics.

## Verdict

**FAIL - 0 Critical, 1 Major.** The scalar, snapshot, concurrency,
interruption, HpoGrid, and digest repairs reviewed here hold, but the explicit
