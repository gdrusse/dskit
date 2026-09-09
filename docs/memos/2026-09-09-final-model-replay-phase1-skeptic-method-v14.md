# Skeptic review - final-model replay Phase 1 method/API lens, v14

**Reviewer task:** `/root/phase1_method_skeptic_v14`
**Model/effort:** GPT-5.6 Terra, high
**Dispatch:** fresh sequential reviewer; no implementation edits.

## Reviewed state and evidence

Read the root and pipeline rules, `skeptic-review` Rules 1 and 7, ADR-0113,
the full final-model/replay plan, the current diff from `c775ac5`, the
architecture report, and all prior method reports v1-v13. Rechecked the
generic Phase-1 surface with particular attention to one-observation
snapshots, ordinary-exception normalization versus `BaseException`
propagation, immutable evidence, canonical JSON/digests, caps, atomic
admission, concurrency, and selection order.

```text
PYTHONDONTWRITEBYTECODE=1 /home/russell/dskit/.venv/bin/python -m pytest -q \
  tests/pipeline/test_kinds_search.py
# 139 passed in 0.38s

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

The v13 dimension correction behaves correctly: an ordinary exception from an
allowed list/tuple subclass is now a field-naming `ValueError`, while
`KeyboardInterrupt` propagates unchanged. The following read-only WSL probes
found two remaining public-boundary defects.

## Findings

### Major — `required_fields` validates a changing collection and stores a different contract

`TrialLedger.__init__` traverses its allowed list/tuple input four times:
validation at [`kinds_search.py:631-635`](../../dskit/pipeline/kinds_search.py#L631-L635),
the reserved-name check at [:639](../../dskit/pipeline/kinds_search.py#L639),
then storage at [:648](../../dskit/pipeline/kinds_search.py#L648). It never
snapshots that caller-owned collection. A list subclass can therefore pass all
validation as `("se",)` and subsequently become `("score",)`, which is a
reserved, impossible-to-satisfy requirement:

```text
ChangingFields.__iter__ -> "se" on the first three traversals,
then -> "score"

ledger = TrialLedger(CandidateInventory({"x": [1]}),
                     required_fields=ChangingFields(["se"]))
print(ledger._required_fields)
# ('score',)
ledger.record({"x": 1}, score=1, se=0)
# ValueError: trial {'x': 1} is missing required field(s) ['score']
```

This violates the constructor's duplicate-free/non-reserved evidence-contract
guarantee and repeats the one-snapshot error previously fixed for grids and
overrides. Snapshot an admitted list/tuple exactly once before every
validation, normalize ordinary traversal failures to the named `ValueError`,
and preserve `BaseException` propagation. Add changing-list/tuple and hostile
ordinary-error/interrupt regressions; construction must either retain the
validated snapshot or refuse without a ledger.

### Major — public `CandidateInventory.contains()` leaks arbitrary mapping exceptions

`CandidateInventory.contains()` directly calls `_combo_key()` at
[`kinds_search.py:578-580`](../../dskit/pipeline/kinds_search.py#L578-L580).
`_combo_key()` iterates the admitted `Mapping` before its only refusal boundary
([:174-184](../../dskit/pipeline/kinds_search.py#L174-L184)). A hostile mapping
therefore leaks arbitrary application code:

```text
BadMapping.__iter__ -> raise ZeroDivisionError("mapping iteration")
CandidateInventory({"x": [1]}).contains(BadMapping())

# ZeroDivisionError: mapping iteration
#   kinds_search.py:176 in _combo_key
```

This contradicts `_combo_key`'s stated named-refusal contract and makes the
public membership API inconsistent with `TrialLedger.record()`'s safe override
snapshot. Enclose all ordinary mapping traversal/copy/canonicalization failures
in a named `ValueError` boundary while allowing `BaseException` to propagate.
Add a hostile `Mapping` regression through `contains()` plus a
`KeyboardInterrupt` counterpart.

## Verdict


**FAIL — 0 Critical, 2 Major.** The v13 hostile-dimension fix is sound, but
the remaining mutable/hostile collection paths violate the public evidence and
membership contracts. Fix with regressions, then restart fresh skeptic review.
