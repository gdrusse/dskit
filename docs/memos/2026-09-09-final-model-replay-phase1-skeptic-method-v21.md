# Skeptic review — final-model replay Phase 1 method/API lens, v21

**Reviewer task:** `/root/phase1_method_skeptic_v21`
**Model/effort:** GPT-5.6 Terra, high
**Dispatch:** fresh sequential method/API review; no implementation edits.

## Scope and evidence

Read the root and pipeline instructions, ADR-0113, the Phase-1 portions of
the final-model/replay plan, the uncommitted Phase-1 diff from `c775ac5`, the
architecture report, and method reports through v20. Rechecked the exact
inventory/ledger contracts, immutable state and evidence, membership and
identity, canonical JSON/digests, selector semantics, HpoGrid/planner
compatibility, public exports/docs, and reservation/interrupt/concurrency
handling. The v20 exact-`CandidateInventory` repair is present.

```text
PYTHONDONTWRITEBYTECODE=1 /home/russell/dskit/.venv/bin/python -m pytest -q \
  tests/pipeline/test_kinds_search.py tests/pipeline/test_purity.py
# 168 passed in 3.14s

/home/russell/dskit/.venv/bin/ruff check dskit/pipeline/kinds_search.py \
  dskit/pipeline/planner.py dskit/pipeline/__init__.py \
  tests/pipeline/test_kinds_search.py
# All checks passed!

git diff --check c775ac5
# clean
```

Direct supported-type probes also established that a bound inventory can have
its slotted attributes reassigned after ledger construction, that a
`TrialLedger` subclass is accepted by the selector, and that an inventory
preserves a float negative zero.

## Findings

### Major — an exact `CandidateInventory` is still mutable after it binds a ledger

`CandidateInventory` calls itself frozen and `TrialLedger` now correctly
requires its exact type ([`kinds_search.py:413-458`](../../dskit/pipeline/kinds_search.py#L413-L458),
[`kinds_search.py:647-662`](../../dskit/pipeline/kinds_search.py#L647-L662)).
But its slots have no one-time assignment guard. A normal caller can reassign
`_keys`, `_combinations`, `_max_candidates`, or `_digest` after binding. The
ledger repeatedly reads those mutable values for expected count, ordering, and
identity ([`kinds_search.py:664-710`](../../dskit/pipeline/kinds_search.py#L664-L710)),
while admission reads mutable `_keys` ([`kinds_search.py:772-777`](../../dskit/pipeline/kinds_search.py#L772-L777)).

For example, after `ledger = TrialLedger(CandidateInventory({"depth": [1]}))`,
assigning `inventory._keys = frozenset()`, `inventory._combinations = ()`, and
`inventory._digest = "replaced"` makes the still-empty ledger report
`expected_count == 0`, `is_complete() is True`, and serialize a zero-row
ledger under the forged digest. Conversely it can make a recorded candidate
disappear or render the ledger incomplete. This defeats ADR-0113's immutable,
bound candidate identity and the complete-ledger guarantee without using an
unsupported subclass or malformed input.

**Required correction:** make `CandidateInventory` genuinely immutable after
successful initialization (including its identity-bearing slots), then add a
regression proving post-bind mutation is refused and cannot alter membership,
count/order, completeness, serialization, or digest.

### Major — `OneStandardErrorSelector` accepts a fabricated `TrialLedger` subclass

The selector checks `isinstance(ledger, TrialLedger)`, not the exact sealed
ledger type ([`kinds_search.py:855-862`](../../dskit/pipeline/kinds_search.py#L855-L862)),
then trusts the overridable `is_complete`, `rows`, and `expected_count`
surfaces. A supported subclass can return `True` from `is_complete()` and a
one-row `rows` tuple containing `{ "overrides": {"unlisted": 99},
"score": 0.0, "se": 0.0 }`; `select()` returns that unlisted row rather than
refusing it. It therefore does not actually require a complete,
inventory-member ledger as its contract says
([`kinds_search.py:815-828`](../../dskit/pipeline/kinds_search.py#L815-L828)).

**Required correction:** require an exact `TrialLedger` at selector entry (or
seal/snapshot the authoritative state and use only that), with a regression
that a subclass cannot fabricate completeness, rows, or a winner.

### Major — negative zero has divergent public candidate identity

ADR-0113 declares that negative zero is serialized as positive `0.0`
([`decision-log.md:6122-6133`](../../docs/architecture/decision-log.md#L6122-L6133)).
`CandidateInventory` accepts finite floats unchanged
([`kinds_search.py:93-99`](../../dskit/pipeline/kinds_search.py#L93-L99)) and
hashes the raw grid combinations through `json.dumps`
([`kinds_search.py:562-574`](../../dskit/pipeline/kinds_search.py#L562-L574)).
Thus `CandidateInventory({"depth": [-0.0]})` exposes `-0.0` and hashes the
`-0.0` JSON spelling, whereas `{ "depth": [0.0] }` has a different digest.
The same raw spelling also lets one dimension contain both signed-zero values
despite the duplicate-value rule. This is an observable canonical identity
and digest divergence for equal public numeric candidates, not an uncertainty
unit decision.

**Required correction:** canonicalize float signed zero before candidate
duplicate checks, combination storage, subsampling/digest input, and any
other public evidence serialization governed by ADR-0113. Add signed-zero
identity and duplicate regressions while preserving the existing `se` test.

## Checks that passed

- The defaulted, digest-pinned full-grid cap is applied before materialization;
  `n_trials` cannot bypass it.
- Rows use recursive canonical JSON snapshots, reserve/prepare/commit outside
  the lock where appropriate, release failed/interrupted reservations, and
  retain canonical inventory order for complete serialization.
- The one-SE threshold uses the best row's caller-supplied standard error,
  validates every row's simplicity callback/key, refuses invalid comparisons,
  and preserves canonical-order ties.
- Generic values remain plain stdlib-only values; HpoGrid remains the DAG
  rerun node. Planner, exports, ADR, README, and focused tests stay within
  Phase 1.

## Verdict

**FAIL — 0 Critical, 3 Major.** The v20 inventory-subclass issue is corrected,
but direct mutability, selector subclass fabrication, and signed-zero canonical
identity still violate the declared frozen-ledger/serialization contract.
