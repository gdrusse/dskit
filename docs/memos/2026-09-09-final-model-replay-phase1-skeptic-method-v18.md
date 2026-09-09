# Skeptic review - final-model replay Phase 1 method/API lens, v18

**Reviewer task:** `/root/phase1_method_skeptic_v18`
**Model/effort:** GPT-5.6 Terra, high
**Dispatch:** fresh sequential reviewer; no implementation edits.

## Scope and evidence

Read the root and pipeline rules, skeptic-loop rules, ADR-0113, the complete
final-model/replay plan and Phase-1 sequence, full current diff from
`c775ac5`, architecture review, and retained method reviews v1-v17. Reviewed
the whole changed public surface: inventory cap/snapshots/digest, ledger
transactionality/concurrency/interrupts/canonical evidence, selector ordering
and callbacks, HpoGrid/planner scalar diagnostics, exports, docs, and tests.

```text
PYTHONDONTWRITEBYTECODE=1 /home/russell/dskit/.venv/bin/python -m pytest -q \
  tests/pipeline/test_kinds_search.py
# 147 passed in 0.41s

PYTHONDONTWRITEBYTECODE=1 /home/russell/dskit/.venv/bin/python -m pytest -q \
  tests/pipeline/test_purity.py
# 18 passed in 2.83s

/home/russell/dskit/.venv/bin/ruff check dskit/pipeline/kinds_search.py \
  dskit/pipeline/planner.py dskit/pipeline/__init__.py \
  tests/pipeline/test_kinds_search.py
# All checks passed!

git diff --check c775ac5
# clean (exit 0)
```

The v17 repair correctly removes hostile type-metadata formatting from the
three new value objects. The inventory now enforces its cap before Cartesian
allocation, and focused coverage continues to substantiate immutable canonical
rows, reservation release under ordinary failures and `BaseException`, locked
state swaps, digest ordering, and selector callback validation.

## Finding

### Major - changed shared scalar rule makes `HpoGrid` static validation crash while formatting a malformed value

Phase 1 changed `_is_json_scalar()` to refuse integers beyond 4096 decimal
digits, and `HpoGrid.validate_params()` uses that shared predicate at
[`kinds_search.py:1035`](../../dskit/pipeline/kinds_search.py#L1035). Its
otherwise accumulator-style validator then renders the rejected values with
`{bad!r}` at [`1037-1040`](../../dskit/pipeline/kinds_search.py#L1037-L1040).
For an ordinary builtin integer over Python's 4300-digit formatting ceiling,
building the diagnostic raises instead of returning the named config problem:

```text
HpoGrid.validate_params({
    "space": {"train.depth": [10**5000]},
    "objective": "$score.loss",
})
# ValueError: Exceeds the limit (4300 digits) for integer string conversion
#   at kinds_search.py:1039, f"... got {bad!r}"
```

This is not an unsupported foreign protocol: it is the exact builtin `int`
the new Phase-1 portability rule rejects. `validate_params()` is the
configuration/planner diagnostic seam and normally accumulates problems, so a
malformed scalar must fail closed as that stable problem rather than abort
validation while rendering it. The same value-rendering pattern also leaves
this public validator vulnerable to hostile `repr` on malformed scalar
objects.

**Required correction:** never interpolate untrusted rejected grid values into
`HpoGrid.validate_params()` diagnostics. Return static, target-naming
messages (and audit the adjacent `space`/objective/select formatting paths
touched by this public validator), with regressions for the 5000-digit builtin
integer and a raising `__repr__`. Re-run fresh independent skeptics after the
code/test change.

## Verdict

**FAIL - 0 Critical, 1 Major.** The new scalar bound is correct in the
inventory/ledger values, but its changed `HpoGrid` validation path cannot
reliably reject malformed public grid values.
