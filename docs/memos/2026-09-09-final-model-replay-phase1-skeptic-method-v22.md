# Skeptic review — final-model replay Phase 1 method/API lens, v22

**Reviewer task:** `/root/phase1_method_skeptic_v22`
**Model/effort:** GPT-5.6 Terra, high
**Dispatch:** fresh retained method/API review; no implementation edits.

## Scope and evidence

Read root/pipeline instructions, ADR-0113, the Phase-1 plan, full current diff
from `c775ac5`, architecture report, and method reports through v21. Rechecked
sealed inventory construction/refusals, ledger contract, canonical
membership/evidence/digests (including signed zero), state/reservation handling,
selector integrity, HpoGrid/planner parity, docs, and tests. Probes used only
ordinary builtin values and direct attribute assignment.

```text
PYTHONDONTWRITEBYTECODE=1 /home/russell/dskit/.venv/bin/python -m pytest -q \
  tests/pipeline/test_kinds_search.py tests/pipeline/test_purity.py
# 170 passed in 3.37s

/home/russell/dskit/.venv/bin/ruff check dskit/pipeline/kinds_search.py \
  dskit/pipeline/planner.py dskit/pipeline/__init__.py \
  tests/pipeline/test_kinds_search.py
# All checks passed!

git diff --check c775ac5
# clean
```

Direct probes show: a score-only ledger completes and serializes; exact
`TrialLedger._state` can be reassigned to a complete forged member ledger that
the selector accepts; and `score=-0.0` stays negative and has a different
digest from `score=0.0`. Inventory, `se`, and nested JSON extras canonicalize
zero correctly.

## Findings

### Major — the ledger has no exact complete-evidence contract

Phase 1 requires candidate, score-component, seed, cut, row-count, parameter,
diagnostic, selected-winner, and inventory-digest evidence, and says a
score-only output must fail ([plan:301-314](../../children/intraday_equities/docs/plans/2026-09-08-final-model-replay-and-monitoring.md#L301-L314)).
Yet `TrialLedger` defaults `required_fields` to `()`
([`kinds_search.py:655-670`](../../dskit/pipeline/kinds_search.py#L655-L670)).
A one-candidate `record(..., score=1.0)` completes and serializes only
`inventory_digest` plus `{overrides, score}` rows
([`kinds_search.py:696-710`](../../dskit/pipeline/kinds_search.py#L696-L710)).
Nor is there ledger-level selected-winner state: the selector merely returns a
row ([`kinds_search.py:863-1002`](../../dskit/pipeline/kinds_search.py#L863-L1002)).

The public API therefore permits the explicitly prohibited artifact and cannot
produce a self-contained complete ledger with its winner. Caller discipline is
not an enforceable serialization/digest contract.

**Required correction:** require a mandatory Phase-1 evidence schema (or split
out a clearly non-Phase-1 minimal API), require all declared row fields, and
add immutable ledger-level selected-winner evidence to the canonical digest.
Test default score-only refusal, transactional missing evidence, and stable
winner serialization/digest.

### Major — an exact `TrialLedger` can be rewritten into forged complete evidence

The v21 repair seals `CandidateInventory`, and the selector requires exact
`TrialLedger`, but the ledger has no assignment guard
([`kinds_search.py:648-670`](../../dskit/pipeline/kinds_search.py#L648-L670)).
Its public methods trust `_state`: completeness is only `len(rows) ==
expected_count` ([`kinds_search.py:691-694`](../../dskit/pipeline/kinds_search.py#L691-L694)),
and `rows`/`to_obj` derive evidence from it
([`kinds_search.py:681-710`](../../dskit/pipeline/kinds_search.py#L681-L710)).

After recording only `{"depth": 1}` for a two-candidate inventory, assigning
`_state` to mapping-proxy rows for both valid members, with a fabricated
`-100.0` score for `{"depth": 2}`, matching seen keys, and no pending entry
makes `is_complete()` true. `to_obj()` serializes it and the exact-type selector
chooses the forged winner. This uses valid members and builtin evidence; the
subclass defense is bypassed by the exact class it trusts.

The ledger is advertised as append-only complete evidence and ADR-0113 says it
accepts immutable rows ([`decision-log.md:6118-6133`](../../docs/architecture/decision-log.md#L6118-L6133)).
Its integrity cannot rely on a convention against assigning its state slot.

**Required correction:** seal ledger identity/contract fields and make state
replacement private to its transition methods. Test assignment cannot alter
completion, rows, serialization, digest, or selected winner while preserving
reserve/commit/release interruption behavior.

### Major — score signed zero is not canonical, so equivalent evidence differs

ADR-0113 declares negative zero serializes as positive `0.0`
([`decision-log.md:6127-6133`](../../docs/architecture/decision-log.md#L6127-L6133)).
The recursive JSON freezer and `se` helper do canonicalize zero
([`kinds_search.py:313-315`](../../dskit/pipeline/kinds_search.py#L313-L315),
[`kinds_search.py:335-343`](../../dskit/pipeline/kinds_search.py#L335-L343)),
but score uses `_frozen_number()` unchanged
([`kinds_search.py:320-330`](../../dskit/pipeline/kinds_search.py#L320-L330),
[`kinds_search.py:795-819`](../../dskit/pipeline/kinds_search.py#L795-L819)).

`record(..., score=-0.0, se=-0.0, nested={"v": -0.0})` retains `score:-0.0`
and has a different digest from the positive-zero ledger, although `se` and
nested values serialize as `0.0`. Canonical evidence identity depends on an
irrelevant IEEE sign bit.

**Required correction:** canonicalize signed zero in `_frozen_number()` or its
score call site. Test stored score sign, `to_obj()` spelling, and equal complete
ledger digests for positive/negative score zero.

## Checks that passed

- Inventory snapshots before validation, applies its digest-pinned cap before
  materialization, rejects subclasses at the ledger boundary, and is sealed.
- Inventory/overrides/`se`/nested extras canonicalize zero; inventory order
  controls rows, serialization, and selection ties.
- Reservation cleanup, concurrent duplicate admission, interruption handling,
  strict finite numeric admission, callback/key validation, exact selector
  admission, and HpoGrid/planner compatibility remain covered.

## Verdict

**FAIL — 0 Critical, 3 Major.** Inventory and selector-subclass repairs hold,
but the ledger still emits prohibited score-only evidence, can be forged despite
exact-type selection, and hashes negative score zero differently.
