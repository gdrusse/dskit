## Skeptic review — final-model replay Phase 1 method/API lens, v5

**Reviewer task:** `/root/phase1_method_skeptic_v5`
**Model/effort:** GPT-5.6 Terra, high
**Dispatch:** fresh sequential reviewer; no implementation edits.

## TL;DR

**FAIL — 0 Critical, 3 Major, 1 Minor.** The v4 Decimal lossiness fix is present, but this fresh pass found that huge accepted integers break the promised canonical digest, malformed Mapping evidence leaks arbitrary caller exceptions, and one-candidate selections accept non-orderable simplicity values (with cyclic/hostile keys also leaking raw exceptions). The focused suite, Ruff, and diff hygiene pass; they do not cover these public paths.

## Reviewed contract and state

Read root/pipeline `AGENTS.md`, `docs/skills/skeptic-review.md` Rules 1 and 7, ADR-0113 (`docs/architecture/decision-log.md:6100-6135`), Phase-1 plan items 1–5 (`children/intraday_equities/docs/plans/2026-09-08-final-model-replay-and-monitoring.md:301-315`), the complete uncommitted diff from `c775ac5`, and v1–v4. I did not inherit their verdicts.

The review covered the public `CandidateInventory`, `TrialLedger`, and `OneStandardErrorSelector` API: exact numeric identity and `-0.0`, bool/NaN/inf/huge integers, mutable/cyclic/deep evidence, map-key and mapping behavior, canonical JSON/digests, retry atomicity, inventory binding, arrival-order ties, callback exceptions, planner scalar-rule parity, and ADR/docs/tests scope. V1–v4 fixes for snapshotting, frozen inventory, strict stored numerics, canonical row order/digest, hostile comparisons, and Decimal refusal are present.

## Focused checks

```text
/home/russell/dskit/.venv/bin/python -m pytest -q tests/pipeline/test_kinds_search.py
# 118 passed in 0.36s

/home/russell/dskit/.venv/bin/ruff check dskit/pipeline/kinds_search.py \
  dskit/pipeline/planner.py dskit/pipeline/__init__.py \
  tests/pipeline/test_kinds_search.py
# All checks passed!

git diff --check
# clean
```

## Findings

### Major — accepted huge integers make inventory/ledger canonicalization fail

`_is_json_scalar` deliberately accepts every `int`, including values too large for Python's JSON integer-to-string safety limit (`kinds_search.py:68-86`), and `_frozen_number` admits every builtin `int` (`:286-294`). That promise does not survive the claimed canonical-JSON boundary. A probe using `1 << 20000` found:

```text
CandidateInventory({"x": [1 << 20000]})
# ValueError at kinds_search.py:426/json.dumps:
# Exceeds the limit (4300 digits) for integer string conversion

ledger.record({"d": 1}, 1 << 20000, se=0)
# succeeds; ledger.is_complete() is True
ledger.digest
# the same raw ValueError at kinds_search.py:564/json.dumps
```

The latter leaves a completed ledger that cannot produce the deterministic artifact/digest ADR-0113 requires. This is especially misleading because the source comment at `:71-80` explicitly says unbounded Python integers are legal JSON scalars. Either give the API a documented, pre-serialization integer bound and refuse it transactionally at inventory/record entry, or supply an encoding that actually supports the admitted range. Cover inventory values, scores, `se`, nested evidence, and an oversized `seed` used for subsampling.

### Major — arbitrary Mapping evidence can execute outside the refusal boundary

`_frozen_json` advertises general `Mapping` support, then iterates and calls `value.items()` without a broad boundary (`kinds_search.py:248-274`). `TrialLedger.record` catches only `RecursionError` around that call (`:603-621`). A valid-shape Mapping subclass whose `items()` raises `ZeroDivisionError` therefore leaks that raw exception at `:258`; it is neither a named `ValueError` refusal nor covered by a regression. The append happens later (`:622-635`), so this probe did not consume the candidate, but callers cannot reliably distinguish bad evidence from arbitrary implementation failure.

The same public API intentionally accepts non-dict mappings for overrides and diagnostics. Wrap snapshot/freeze traversal errors broadly into a trial-naming `ValueError`, while preserving the no-row/no-`_seen` transaction guarantee; add the hostile-`items`, hostile iteration, and retry-after-refusal cases.

### Major — simplicity validation is neither total nor enforced for a singleton

ADR-0113 requires non-orderable selection values to refuse. The selector only compares keys when there is a second eligible row (`kinds_search.py:807-837`). A complete one-candidate ledger with `simplicity_key=lambda row: object()` returns `{"d": 1}` rather than refusing, although `object()` is not orderable.

The preliminary checks are also unguarded: a cyclic list key recurses in `_contains_unordered_set` (`:317-334`) until raw `RecursionError`, and an object whose `__float__` raises leaks raw `ZeroDivisionError` from `_contains_nan` (`:297-314`). These occur after the callback itself is caught, so the existing callback-exception test misses them. Define and validate a finite, acyclic, totally-orderable simplicity-key grammar before choosing a provisional winner (including the one-row case), and wrap all validation/comparison failures in a candidate-naming `ValueError`. Add singleton opaque, cyclic/deep, hostile numeric-protocol, and normal tuple/string/number cases.

### Minor — inventory combinations still expose mutable numeric subclasses

The grid accepts `isinstance(value, int/float)` rather than exact builtin types (`kinds_search.py:82-86`) and stores the original object in a supposedly frozen combination (`:441-447`). An `int` subclass with a mutable `.payload` attribute was accepted; mutating it through `inventory.combinations[0]["x"]` changed the observable frozen value's state. Its numeric value and current digest remain stable, so this is not the digest corruption above, but it contradicts the documented frozen-combination boundary. Refuse numeric subclasses or normalize to builtin primitives before storing.

## Reproducibility and handoff

All probes used `PYTHONDONTWRITEBYTECODE=1` and the sibling WSL virtualenv; they changed no repository file. Only this independent reviewer-owned report was created. Resolve the three Majors with focused regressions, then restart the skeptic loop with fresh independent reviewers; this pass cannot satisfy the shipping gate.
