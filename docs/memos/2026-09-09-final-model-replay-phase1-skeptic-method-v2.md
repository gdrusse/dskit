## Skeptic review - final-model replay Phase 1 method/API lens, v2

**Reviewer task:** `/root/phase1_method_skeptic_v2`
**Model/effort:** GPT-5.6 Terra, high
**Dispatch:** sequential, fresh reviewer after the v1 fixes.
**Lens:** immutable candidate/evidence boundaries, adversarial mappings and iterators, ledger atomicity, candidate identity/order, one-SE selection, numeric/simplicity failure paths, planner parity, OOP/docs/tests, and Phase-1 scope.

## Reviewed state

Read root and pipeline `AGENTS.md`, `docs/skills/skeptic-review.md` Rules 1 and 7, ADR-0113 (`docs/architecture/decision-log.md:6100-6135`), the final-model plan sections 1, 2, 5, 6, 9-11, the full uncommitted Phase-1 diff from `c775ac5`, and v1. The v1 mapping snapshot and `MappingProxyType` copy fixes are present: `_snapshot_overrides` snapshots once before membership/dedup/store (`kinds_search.py:481-537`) and `_freeze` copies general `Mapping` values (`:227-243`). Placement/scope remain generic Phase 1 only.

## Focused checks

```text
/home/russell/dskit/.venv/bin/python -m pytest -q \
  tests/pipeline/test_kinds_search.py
# 105 passed in 0.35s

/home/russell/dskit/.venv/bin/ruff check \
  dskit/pipeline/kinds_search.py dskit/pipeline/planner.py \
  dskit/pipeline/__init__.py tests/pipeline/test_kinds_search.py
# All checks passed!

git diff --check
# clean
```

Read-only WSL probes established the concrete failures below.

## Findings

### Major - a bound inventory can be publicly rewritten after ledger creation

`CandidateInventory` exposes writable `combinations` and `digest` slots (`kinds_search.py:337-391`). `TrialLedger.expected_count` and `is_complete()` read the same live public `inventory.combinations` (`:464-473`), while membership still reads the original private `_keys` (`:481-482`). This valid public sequence silently tears the ledger contract:

```python
inventory = CandidateInventory({"depth": [1]})
ledger = TrialLedger(inventory)
inventory.combinations = ()
ledger.record({"depth": 1}, 1.0)
assert ledger.expected_count == 0
assert not ledger.is_complete()
```

The row is a genuine original member but the ledger now claims a zero-member inventory. Reassigning `digest` also rewrites reported identity. ADR-0113 requires a frozen inventory and a ledger bound to one inventory. Make state unassignable (private slots/read-only properties or equivalent) and test post-bind mutation.

### Major - rows remain mutable through accepted iterator/opaque evidence

`_freeze` leaves anything other than mappings, list/tuple, and set/frozenset unchanged (`kinds_search.py:195-243`), but `record()` promises immutable, append-only rows while accepting arbitrary `**extra` (`:401-424`, `:509-537`). An iterator is stored by reference and can change historical evidence:

```python
iterator = iter(["before", "after"])
ledger = TrialLedger(CandidateInventory({"depth": [1]}))
ledger.record({"depth": 1}, 1.0, diagnostics=iterator)
assert next(iterator) == "before"
assert next(ledger.rows[0]["diagnostics"]) == "after"
```

The same applies to arbitrary mutable objects. This defeats ADR-0113's immutable evidence-row promise. Define an admitted evidence-value grammar and either deep-copy/freeze every admitted form or transactionally refuse unsupported mutable forms; cover iterators and mutable custom objects.

### Major - one-SE selection still leaks unchecked arbitrary exceptions

The selector duck-types numeric values, but its threshold finite check and simplicity comparison can escape raw errors. `math.isfinite(threshold)` is unguarded (`kinds_search.py:697`), despite `combine` accepting arbitrary operator outputs; the simplicity loop catches only `TypeError`/`ValueError` (`:749-774`), unlike the score comparator's broad named refusal (`:631-656`).

Two probes produced bare `ZeroDivisionError`: an accepted score whose `__add__` returns an object with a failing `__float__`, and two eligible rows whose simplicity values raise from `__lt__`. The latter is directly a non-orderable `simplicity_key` result, which ADR-0113 says public values refuse. Wrap both paths broadly, name candidate(s), and add regression cases.

### Minor - malformed `required_fields` leaks `TypeError` before validation

`TrialLedger.__init__` intersects `required_fields` before checking it is a tuple/list of strings (`kinds_search.py:433-447`). `required_fields=None` leaks `TypeError: NoneType is not iterable`; `required_fields=[[]]` leaks `TypeError: unhashable list`, not the documented named `ValueError`. Validate shape/content before calculating reserved names.

### Minor - the required-fields regression test has no assertion

`tests/pipeline/test_kinds_search.py:123-125` defines `test_required_fields_string_is_refused_at_construction` with only its docstring; the following unindented `FLAT_SPLITS` assignment ends the method. The focused suite therefore reports 105, not the claimed 130, and this fix has no regression coverage. Add actual `pytest.raises` checks, including the two malformed values above.

## Verdict

**FAIL - 0 Critical, 3 Major, 2 Minor.** V1 is fixed and focused tests/style/diff hygiene pass, but frozen-inventory integrity, immutable ledger evidence, and the selector's public refusal contract remain unsafe. Fix the Majors with focused regressions, then dispatch a fresh independent skeptic; this is reviewer-authored Rule-7 trace.
