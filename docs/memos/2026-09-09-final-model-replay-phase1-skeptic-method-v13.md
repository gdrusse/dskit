# Skeptic review - final-model replay Phase 1 method/API lens, v13

**Reviewer task:** `/root/phase1_method_skeptic_v13`
**Model/effort:** GPT-5.6 Terra, high
**Dispatch:** fresh sequential reviewer; no implementation edits.

## Reviewed state and evidence

Read the root and pipeline rules, `skeptic-review` Rules 1 and 7, ADR-0113,
the complete final-model/replay plan, the full uncommitted diff from `c775ac5`,
and all prior method reports v1-v12 plus the architecture report. Rechecked
the retained public inventory/ledger/selector contracts: transactionality,
canonical rows/digests, scalar and integer boundaries, deterministic selection,
callback totality, planner/HpoGrid parity, and Phase-1 scope. This fresh pass
specifically attacked `max_candidates` validation, exact product arithmetic
before allocation, digest identity, `n_trials` non-bypass, and hostile grid
dimensions.

```text
PYTHONDONTWRITEBYTECODE=1 /home/russell/dskit/.venv/bin/python -m pytest -q \
  tests/pipeline/test_kinds_search.py
# 137 passed in 0.39s

/home/russell/dskit/.venv/bin/ruff check dskit/pipeline/kinds_search.py \
  dskit/pipeline/planner.py dskit/pipeline/__init__.py \
  tests/pipeline/test_kinds_search.py
# All checks passed!

git diff --check c775ac5
# clean
```

The existing count-cap regressions substantiate that the snapped tuple lengths,
not an overridden caller `__len__`, drive the product; a count above the cap is
refused before `_grid`, even when `n_trials=1`. The cap is included in the
inventory digest as ADR-0113 requires.

## Finding

### Major - a hostile allowed dimension leaks arbitrary code instead of a named grid refusal

`CandidateInventory` deliberately accepts `list` and `tuple` dimensions, but
snapshots one with an unguarded iteration at
`dskit/pipeline/kinds_search.py:484`:

```python
space_snapshot[name] = tuple(value for value in supplied_values)
```

This read-only WSL probe changed no repository files:

```text
HostileList = type(
    "HostileList", (list,),
    {"__iter__": lambda self: (_ for _ in ()).throw(
        ZeroDivisionError("dimension iteration")
    )},
)
CandidateInventory({"depth": HostileList([1])})

# ZeroDivisionError: dimension iteration
#   kinds_search.py:484 in __init__
```

Thus malformed/hostile input in an admitted public grid shape escapes as an
arbitrary caller exception rather than the constructor's named `ValueError`
refusal. This violates ADR-0113's malformed-grid refusal contract and breaks
the same hostile-input boundary already enforced for mapping snapshots and
ledger evidence. Snapshot each dimension inside a narrow boundary that turns
ordinary traversal failures into a field-naming `ValueError` before validation,
counting, or allocation; preserve `BaseException` propagation. Add list- and
tuple-subclass hostile-iteration regressions and confirm the surrounding input
remains usable after refusal.

## Verdict
