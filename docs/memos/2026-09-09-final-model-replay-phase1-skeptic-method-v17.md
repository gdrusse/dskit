# Skeptic review - final-model replay Phase 1 method/API lens, v17

**Reviewer task:** `/root/phase1_method_skeptic_v17`
**Model/effort:** GPT-5.6 Terra, high
**Dispatch:** fresh sequential reviewer; no implementation edits.

## Scope and evidence

Read the root and pipeline rules, the skeptic-loop rules, ADR-0113, the full
final-model/replay plan including Phase 1, the uncommitted diff from `c775ac5`,
the architecture report, and method reports v1-v16. I attacked the public
inventory, ledger, and selector boundaries with hostile collection protocols,
exception diagnostics, immutable/canonical evidence, numeric bounds,
selection semantics, interruption cleanup, concurrency, cap-before-allocation,
and digest/JSON snapshots.

```text
PYTHONDONTWRITEBYTECODE=1 /home/russell/dskit/.venv/bin/python -m pytest -q \
  tests/pipeline/test_kinds_search.py
# 146 passed in 0.34s

PYTHONDONTWRITEBYTECODE=1 /home/russell/dskit/.venv/bin/python -m pytest -q \
  tests/pipeline/test_purity.py
# 18 passed in 2.73s

/home/russell/dskit/.venv/bin/ruff check dskit/pipeline/kinds_search.py \
  dskit/pipeline/planner.py dskit/pipeline/__init__.py \
  tests/pipeline/test_kinds_search.py
# All checks passed!

git diff --check c775ac5
# clean (exit 0)
```

The v16 repair correctly removes direct formatting of hostile exceptions from
the required-fields and `space.items()` snapshot handlers. The focused suite
also continues to cover transactional reservation release, `BaseException`
propagation on collection traversal, immutable rows, canonical JSON, exact
integers, caller cap, and inventory-order selection.

## Finding

### Major - public refusal diagnostics still execute a hostile metaclass

Several Phase-1 public refusal paths use `type(value).__name__` while building
their error. That attribute lookup is caller-controlled: a metaclass can raise
from `__getattribute__`. The API therefore leaks an arbitrary ordinary
exception instead of the stable `ValueError` promised for malformed public
inputs. This is the same public-error-safety contract that v15/v16 repaired
for hostile `repr`/`str`, by a different Python protocol.

```text
class Meta(type):
    def __getattribute__(cls, name):
        if name == "__name__":
            raise ZeroDivisionError("hostile class name")
        return super().__getattribute__(name)

class Evil(metaclass=Meta):
    pass

x = Evil()
CandidateInventory({"x": [1]}).contains(x)
# ZeroDivisionError: hostile class name

TrialLedger(x)
# ZeroDivisionError: hostile class name

OneStandardErrorSelector(select="min", simplicity_key=lambda row: 0).select(x)
# ZeroDivisionError: hostile class name

CandidateInventory({"x": x})
# ZeroDivisionError: hostile class name
```

The affected direct sites are `_combo_key` and `_snapshot_overrides`
(`kinds_search.py:168,203`), `CandidateInventory.__init__` (`:515`),
`TrialLedger.__init__` (`:643`), and `OneStandardErrorSelector.select`
(`:852`). The first three are construction/membership/record inputs; the last
is selector input. Each must refuse ordinary malformed input predictably.
`BaseException` must remain outside this normalization boundary.

**Required correction:** do not derive diagnostics from an untrusted class
attribute. Use static wording, or a helper that cannot invoke caller protocol,
at every public boundary (including `_frozen_number`'s analogous `:326`), and
add regressions for the four paths above plus a `KeyboardInterrupt`/custom
`BaseException` counterpart. Restart with fresh reviewers after the code/test
change.

## Verdict

**FAIL - 0 Critical, 1 Major.** Focused tests, style, and diff hygiene pass,
but ordinary hostile caller input can still bypass the named Phase-1 refusal
contract. This reviewer made no implementation changes.
