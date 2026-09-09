# Skeptic review — final-model replay Phase 1 method/API lens, v16

**Reviewer task:** `/root/phase1_method_skeptic_v16`
**Model/effort:** GPT-5.6 Terra, high
**Dispatch:** fresh sequential reviewer; no implementation edits.

## Scope and evidence

Read the root and pipeline rules, skeptic-loop Rules 1 and 7, ADR-0113, the
complete final-model/replay plan (including Phase 1), full uncommitted diff
from `c775ac5`, architecture report, and method reports v1–v15. Re-ran hostile
public boundaries, one-shot snapshots, immutable evidence, numeric/canonical
JSON/digest bounds, caps, selector ordering, interruptions, and reservation
state against the current Phase-1 diff.

```text
PYTHONDONTWRITEBYTECODE=1 /home/russell/dskit/.venv/bin/python -m pytest -q \
  tests/pipeline/test_kinds_search.py
# 144 passed in 0.33s

PYTHONDONTWRITEBYTECODE=1 /home/russell/dskit/.venv/bin/python -m pytest -q \
  tests/pipeline/test_purity.py
# 18 passed in 2.77s

/home/russell/dskit/.venv/bin/ruff check dskit/pipeline/kinds_search.py \
  dskit/pipeline/planner.py dskit/pipeline/__init__.py \
  tests/pipeline/test_kinds_search.py
# All checks passed!

git diff --check c775ac5
# clean (exit 0)
```

The v15 regressions correctly prevent hostile *values* and malformed entries
from being rendered. Earlier one-snapshot, membership, cleanup, immutable-row,
cap, finite/exact-numeric, and all-row selector repairs also held.

## Finding

### Major — hostile exception diagnostics still bypass the named public refusal

Two constructor snapshot handlers catch an ordinary caller failure and then
render the caller-controlled exception with `!r`:
[`_snapshot_required_fields`, `kinds_search.py:172-177`](../../dskit/pipeline/kinds_search.py#L172)
and [`CandidateInventory.__init__`, `:465-470`](../../dskit/pipeline/kinds_search.py#L465).
An exception can own a hostile `__repr__`; the attempted normalization then runs
arbitrary code outside its `except` boundary and leaks that exception rather
than `ValueError`.

```text
class EvilError(Exception):
    def __repr__(self): raise ZeroDivisionError("exception repr")
class BadFields(list):
    def __iter__(self): raise EvilError()
class BadSpace(dict):
    def items(self): raise EvilError()

TrialLedger(CandidateInventory({"x": [1]}), required_fields=BadFields(["se"]))
# ZeroDivisionError: exception repr
CandidateInventory(BadSpace(x=[1]))
# ZeroDivisionError: exception repr
```

These are ordinary malformed caller collections. ADR-0113 requires malformed
grids to refuse, and v15 specifically required public input errors never render
uncontrolled `repr`; callers cannot distinguish this refusal from an internal
crash. `KeyboardInterrupt`/other `BaseException` must still propagate unchanged,
and failed construction must create no object.

**Required correction:** use static/safe context instead of both exception
renderings, inspect related public input exception paths for this pattern, and
add raising-exception-`__repr__` regressions for `required_fields` and
`space.items()` plus BaseException counterparts. Restart fresh review after the
code/test change.

## Verdict

**FAIL — 0 Critical, 1 Major.** Focused tests, style, and diff hygiene pass,
but an ordinary hostile caller failure still escapes the Phase-1 public refusal
contract. This reviewer made no implementation changes.
