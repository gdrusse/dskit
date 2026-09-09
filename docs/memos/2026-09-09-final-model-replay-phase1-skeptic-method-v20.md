# Skeptic review - final-model replay Phase 1 method/API lens, v20

**Reviewer task:** `/root/phase1_method_skeptic_v20`
**Model/effort:** GPT-5.6 Terra, high
**Dispatch:** fresh sequential reviewer; no implementation edits.

## Scope and evidence

Read the root and pipeline rules, ADR-0113, the complete final-model/replay
plan's Phase-1 protocol, full current diff from `c775ac5`, architecture report,
and prior method reports through v19. Independently rechecked candidate
inventory freezing/caps/canonicalization, ledger admission/retry/serialization,
one-SE selection order and numeric policy, HpoGrid/planner boundaries, public
exports/docs, and concurrent/interruption paths.

```text
PYTHONDONTWRITEBYTECODE=1 /home/russell/dskit/.venv/bin/python -m pytest -q \
  tests/pipeline/test_kinds_search.py tests/pipeline/test_purity.py
# 167 passed in 3.24s

/home/russell/dskit/.venv/bin/ruff check dskit/pipeline/kinds_search.py \
  dskit/pipeline/planner.py dskit/pipeline/__init__.py \
  tests/pipeline/test_kinds_search.py
# All checks passed!

git diff --check c775ac5
# clean
```

The v19 correction is present: `TrialLedger._freeze_trial()` routes `se`
through `_frozen_standard_error()` ([kinds_search.py:786-810](../../dskit/pipeline/kinds_search.py#L786-L810)), which rejects negative values and canonicalizes float negative zero.

## Finding

### Major - a `CandidateInventory` subclass can change a bound ledger's public candidate set

`TrialLedger.__init__()` accepts subclasses through `isinstance(inventory,
CandidateInventory)` ([kinds_search.py:646-661](../../dskit/pipeline/kinds_search.py#L646-L661)).
After construction it repeatedly reads the overridable public
`inventory.combinations` property for `expected_count` ([kinds_search.py:667-669](../../dskit/pipeline/kinds_search.py#L667-L669)), `rows`
([kinds_search.py:671-680](../../dskit/pipeline/kinds_search.py#L671-L680)), and
completion ([kinds_search.py:682-685](../../dskit/pipeline/kinds_search.py#L682-L685)),
while membership remains against the inherited frozen private `_keys`
([kinds_search.py:771-776](../../dskit/pipeline/kinds_search.py#L771-L776)). A
normal subclass can therefore make a successfully recorded, genuine member
disappear from all public evidence:

```text
class MutableInventory(CandidateInventory):
    @property
    def combinations(self):
        return ()

inventory = MutableInventory({"depth": [1]})
ledger = TrialLedger(inventory)
ledger.record({"depth": 1}, score=1, se=0)

ledger.expected_count  # 0
ledger.rows            # ()
ledger.is_complete()   # False
ledger.to_obj()        # ValueError: cannot serialize an incomplete TrialLedger
```

This violates ADR-0113's frozen-inventory / one-complete-row-per-declared-
combination contract: the `record()` call has admitted the real inherited
candidate, but the public ledger presents a zero-candidate incomplete ledger
and cannot serialize or select it. It is the same mutable-boundary failure
that the private slots/properties fixed for direct assignment, reopened by the
accepted subclass surface.

**Required correction:** either require an exact `CandidateInventory` in
`TrialLedger`, or snapshot the immutable inventory identity, ordered
combinations, keys, expected count, and digest at bind time and use those
snapshots everywhere. Add a subclass-override regression showing that an
accepted inventory cannot alter `expected_count`, `rows`, completeness, or
serialized/digested identity; retain the retry and normal-inventory cases.

## Verdict

**FAIL - 0 Critical, 1 Major.** Focused tests, Ruff, diff hygiene, and the
v19 negative-SE repair pass, but the ledger does not reliably remain bound to
