## Skeptic review — final-model replay Phase 1 model-method/API lens

**Reviewer task:** `/root/phase1_method_skeptic`
**Model/effort:** GPT-5.6 Terra, high
**Lens:** candidate identity/order/uniqueness; JSON normalization; ledger
atomicity and retry safety; score/diagnostic evidence; one-SE dependency
injection, semantics and deterministic ties; malformed/non-finite/deep/cyclic
inputs; public OOP/docs/tests; and Phase-1-only scope.
**Dispatch mode:** sequential; independent reviewer-owned report.

## Scope and reviewed state

Reviewed the full uncommitted diff from `c775ac5`, ADR-0113
(`docs/architecture/decision-log.md:6100-6135`), the complete final-model
replay plan (especially §§1, 5, 6, 9–11), root/pipeline/child AGENTS,
pipeline README, and skeptic-review Rules 1 and 7. Current worktree has the
seven Phase-1 files modified; no child code, market read, HPO, persistence,
calendar, replay, monitoring, or later-phase work is included. The Phase-1
placement is otherwise correctly core/generic.

## Commands and results

```text
/home/russell/dskit/.venv/bin/python -m pytest -q \
  tests/pipeline/test_kinds_search.py
# 102 passed in 0.34s

/home/russell/dskit/.venv/bin/ruff check \
  dskit/pipeline/kinds_search.py dskit/pipeline/planner.py \
  dskit/pipeline/__init__.py tests/pipeline/test_kinds_search.py
# All checks passed!

git diff --check
# clean
```

Two read-only WSL probes found the failures below. A state-changing
`collections.abc.Mapping` passed as `overrides` produced two stored
`{'depth': 2}` rows while `ledger.is_complete()` returned `True`; constructing
`TrialLedger(inventory, required_fields="se")` succeeded with `('s', 'e')`;
and mutating the backing dict of a recorded `MappingProxyType` diagnostic
appeared immediately in `ledger.rows`.

## Findings

### Major — `record()` validates/deduplicates a different overrides snapshot than it stores

`dskit/pipeline/kinds_search.py:460-474` serializes caller-owned mappings
twice for membership and `_seen`, then `:503-516` takes a third fresh
`dict(overrides)` for the immutable row. The public API deliberately accepts
any `Mapping` (`:134-144`), so a mapping whose values change per access is
valid input shape. The probe recorded it once, then recorded ordinary
`{'depth': 2}`: the ledger contained two depth-2 rows and claimed complete,
while `_seen` represented depth 1 and depth 2. This violates ADR-0113's
"exactly one immutable row per declared combination" and makes trial evidence
silently wrong. Snapshot/validate/deduplicate/store one canonical mapping
exactly once, then add a regression test.

**Disposition:** unresolved; must fix and restart fresh review.

### Major — `MappingProxyType` extras remain mutable through their backing mapping

`_freeze` only handles concrete `dict` at
`dskit/pipeline/kinds_search.py:214-230`, so a `types.MappingProxyType` falls
through unchanged. `TrialLedger.record` stores it at `:488-515`, despite the
ADR's immutable-row promise and its own append-only documentation. The probe
recorded `diagnostics=MappingProxyType(backing)`, mutated `backing`, and the
old ledger row gained `{'after': 2}`. This permits retrospective changes to
cuts/diagnostics/evidence. Freeze a copied general `Mapping` (with cycle
handling) or refuse non-serializable mapping types; test the backing-mutation
case.

**Disposition:** unresolved; must fix and restart fresh review.

### Minor — malformed `required_fields` creates an invalid ledger instead of refusing

`TrialLedger.__init__` at `dskit/pipeline/kinds_search.py:420-435` accepts a
string despite documenting `tuple[str]`; it converts `"se"` to `('s', 'e')`.
A caller supplying the documented `se` evidence is then refused for missing
unrelated fields. Validate a duplicate-free tuple/list of non-empty string
field names (and reject non-iterables with a named `ValueError`) before the
object exists; add coverage.

**Disposition:** unresolved; fix or obtain explicit owner acceptance.

### Minor — public docstring cites the wrong ADR

`CandidateInventory` points to ADR-0112 at
`dskit/pipeline/kinds_search.py:287-290`; the feature is ADR-0113. Correct the
reference.

**Disposition:** unresolved; fix with the implementation changes.

## Verdict

**FAIL.** The focused suite and Ruff pass, but the two Major findings corrupt
the generic evidence ledger under an API shape the implementation explicitly
