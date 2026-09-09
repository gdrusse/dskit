# Skeptic review — final-model replay Phase 1 method/API lens, v6

Reviewer task: `/root/phase1_method_skeptic_v6`
Model/effort: GPT-5.6 Terra, high
Dispatch: fresh sequential reviewer; no implementation edits.

## Reviewed state

Read root and pipeline `AGENTS.md`, skeptic-review Rules 1 and 7, ADR-0113
(`docs/architecture/decision-log.md:6100-6135`), Phase-1 plan items 1–6
(`children/intraday_equities/docs/plans/2026-09-08-final-model-replay-and-monitoring.md:301-315`), full uncommitted diff from `c775ac5`, and skeptic v1–v5. I independently attacked public inventory/ledger/selector contracts, the 4096-digit boundary, mapping snapshots and JSON hand-off, retry atomicity, canonical order, planner parity, and callback validation.

## Focused checks

```text
PYTHONDONTWRITEBYTECODE=1 /home/russell/dskit/.venv/bin/python -m pytest -q \
  tests/pipeline/test_kinds_search.py
# 122 passed in 0.36s

/home/russell/dskit/.venv/bin/ruff check dskit/pipeline/kinds_search.py \
  dskit/pipeline/planner.py dskit/pipeline/__init__.py \
  tests/pipeline/test_kinds_search.py
# All checks passed!

git diff --check
# clean
```

## Findings

### Major — a changing Mapping can bypass evidence-key validation, consume a trial, and leave no digest

`_frozen_json` first validates keys by iterating a general `Mapping`, then
independently consumes `value.items()` (`kinds_search.py:246-255`). Those two
observations are not one snapshot. A Mapping whose `__iter__` reports
`("safe",)` but whose `items()` reports `[(1, "first"), ("1", "second")]`
is accepted by `TrialLedger.record`. Its frozen diagnostic is then
`{1: "first", "1": "second"}`. On the complete ledger, `digest` fails with
bare `TypeError: '<' not supported between instances of 'str' and 'int'` at
`json.dumps(..., sort_keys=True)` (`:606-609`). The accepted row has already
been appended and marked seen (`:675-688`), so the caller cannot correct and
retry that candidate.

This violates ADR-0113’s transactional rejected-row and portable
canonical-JSON/digest contracts (`decision-log.md:6115-6126`). Snapshot every
Mapping once before key validation and freezing (or validate the exact emitted
snapshot), reject duplicate/colliding/non-string keys under that snapshot, and
test that the refusal leaves the candidate retryable.

### Major — simplicity validation is not total: excluded rows silently skip malformed callbacks

`OneStandardErrorSelector.select()` evaluates `simplicity_key` only for rows
already inside the score band (`kinds_search.py:817-845`). A complete
two-candidate min ledger with `(score, se)` `(0, 0)` and `(100, 0)` selected
depth 1 while a callback that raises for depth 2 was never called; the
read-only probe printed `{'depth': 1} [1]`. Replacing that callback result with
an opaque/non-orderable value likewise succeeds whenever its row is excluded.

ADR-0113 promises refusal of non-orderable selection values
(`decision-log.md:6123-6126`), and Phase 1 requires every candidate’s retained
evidence. Validate/call the simplicity policy exactly once for every canonical
ledger row before eligibility selection, with all callback and grammar failures
named by candidate. Add regressions for excluded malformed keys and excluded
callback exceptions, plus deterministic canonical callback order.

### Major — the new 4096-digit contract omits public seed paths and CandidateInventory.n_trials

The bound is correctly applied to inventory scalar values, ledger evidence, and
the single `HpoGrid.seed`, but not to `HpoGrid.seeds`: its validator accepts
any `isinstance(s, int)` (`kinds_search.py:963-971`). A 4097-digit value
(`10**4096`) yielded `[]` from `HpoGrid.validate_params` for an otherwise valid
document. The same value is accepted as `CandidateInventory(...,
`n_trials=10**4096)` (`:471-478`); the probe constructed it and produced digest
`b6fc52ff`.

That contradicts ADR-0113’s stated bound for accepted builtin integers and
reopens a non-portable seed/config path despite the new scalar rule. Apply one
shared bounded-integer validator to every public integer input that can enter
