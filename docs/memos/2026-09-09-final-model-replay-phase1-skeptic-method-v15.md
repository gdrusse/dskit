# Skeptic review — final-model replay Phase 1 method/API lens, v15

**Reviewer task:** `/root/phase1_method_skeptic_v15`
**Model/effort:** GPT-5.6 Terra, high
**Dispatch:** fresh sequential reviewer; no implementation edits.

## Reviewed state and evidence

Read the root and pipeline rules, `skeptic-review` Rules 1 and 7, ADR-0113,
the complete final-model/replay plan, the full worktree diff from `c775ac5`,
the architecture report, and every prior method report v1-v14. I rechecked
the public `CandidateInventory`, `TrialLedger`, and
`OneStandardErrorSelector` constructors/properties/methods against changing
collections, hostile mappings, ordinary versus `BaseException` failures,
canonical JSON/digests, exact integer bounds, selector order, and concurrent
reserve/prepare/commit behavior.

```text
PYTHONDONTWRITEBYTECODE=1 /home/russell/dskit/.venv/bin/python -m pytest -q \
  tests/pipeline/test_kinds_search.py
# 142 passed in 0.33s

PYTHONDONTWRITEBYTECODE=1 /home/russell/dskit/.venv/bin/python -m pytest -q \
  tests/pipeline/test_purity.py
# 18 passed in 2.75s

/home/russell/dskit/.venv/bin/ruff check dskit/pipeline/kinds_search.py \
  dskit/pipeline/planner.py dskit/pipeline/__init__.py \
  tests/pipeline/test_kinds_search.py
# All checks passed!

git diff --check c775ac5
# clean (exit 0)
```

The v14 repairs are sound: `required_fields` is traversed once into an
immutable tuple before reserved-name validation, and `contains()` uses the
same override snapshot as `record()`. Ordinary traversal failures from those
two exercised shapes become `ValueError`; `KeyboardInterrupt` propagates.
Ledger reservation cleanup, row/digest snapshots, inventory scalar freezing,
the cap-before-grid rule, and all-row simplicity validation also held under
the focused probes.

## Finding

### Major — hostile diagnostics still escape the public refusal boundary

The Phase-1 API promises named refusal for malformed grids and hostile caller
objects, but `CandidateInventory.__init__` formats caller-owned values after
validation has already decided to reject them. A normal object whose
`__repr__` raises leaks that arbitrary exception at
[`kinds_search.py:539`](../../dskit/pipeline/kinds_search.py#L539), instead of
the required `ValueError`:

```text
class Evil:
    def __repr__(self):
        raise ZeroDivisionError("repr")

CandidateInventory({"x": [Evil()]})
# ZeroDivisionError: repr
```

The grid snapshot boundary is also incomplete. Although
`tuple(space.items())` is protected, the subsequent public-input destructure
at [`:498`](../../dskit/pipeline/kinds_search.py#L498) is not. A `dict`
subclass whose `items()` produces a three-item entry raises the interpreter's
bare `ValueError: too many values to unpack`, rather than the grid's named
contract refusal.

The same diagnostic-formatting hole reopens the just-fixed `contains()` API:
when a hostile `Mapping` raises `ZeroDivisionError` during `dict(overrides)`,
`_snapshot_overrides` tries `{overrides!r}` in its exception path
([`:227`](../../dskit/pipeline/kinds_search.py#L227)). A hostile `__repr__`
then replaces the normalized refusal with a second arbitrary
`ZeroDivisionError`. These are ordinary exceptions, not interrupts, so they
must not escape; a `KeyboardInterrupt`/other `BaseException` must still
propagate and leave no state change.

This is a Major because all three paths are public construction/membership
boundaries for the generic Phase-1 evidence contract. A caller cannot rely on
a malformed/hostile input being distinguishable from an implementation failure,
despite ADR-0113's explicit malformed-grid refusal and the repeated reviewer
ruling that ordinary caller-protocol failures normalize by name.

**Required correction:** snapshot/validate each grid entry inside a broad
ordinary-exception boundary, and never call uncontrolled `repr` while building
a refusal (use type/field context or a safe representation helper). Apply the
same safe diagnostic rule to `_snapshot_overrides`/`_combo_key` and any related
public input error path. Add regressions for hostile `repr`, malformed
dict-subclass entries, hostile membership traversal plus hostile `repr`, and a
`BaseException` counterpart proving propagation and unchanged inventory/ledger
state. Then restart with fresh skeptics.

## Verdict

**FAIL — 0 Critical, 1 Major.** The v14 one-shot required-field and membership
fixes work, but normal hostile-object failures still leak through public
CandidateInventory/contains boundaries. Fix with regressions, then run a fresh
independent review; this reviewer made no implementation change.
