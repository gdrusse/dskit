# Skeptic review — final-model replay Phase 1 architecture

**Reviewer task:** `/root/phase1_arch_skeptic`
**Model/effort:** GPT-5.6 Terra, high
**Dispatch:** fresh, sequential architecture/integration skeptic; no implementation edits.

## Scope and evidence

Read the root, pipeline, and intraday-equities `AGENTS.md`; the skeptic and
wrap rules; ADR-0113; the full final-model/replay plan; the complete
uncommitted diff from `c775ac5`; method skeptic v12; and the prior Phase-1
history. Reviewed tier ownership, API/export/registry and planner compatibility,
identity and serialization, resource and concurrency bounds, docs/tree/ADR
governance, test placement, child non-overlap, and branch provenance.

Focused WSL evidence on the reviewed working tree:

```text
PYTHONDONTWRITEBYTECODE=1 /home/russell/dskit/.venv/bin/python -m pytest -q \
  tests/pipeline/test_kinds_search.py tests/pipeline/test_purity.py
# 153 passed in 3.16s

/home/russell/dskit/.venv/bin/ruff check \
  dskit/pipeline/kinds_search.py dskit/pipeline/planner.py \
  dskit/pipeline/__init__.py tests/pipeline/test_kinds_search.py
# All checks passed!

git diff --check c775ac5
# clean

# dskit.pipeline public-import / inventory -> ledger -> selector probe: PASS
```

## Finding

### Major — `CandidateInventory` has no caller-owned materialization bound

`CandidateInventory.__init__` accepts arbitrarily many dimensions and values
([`kinds_search.py:435-500`](../../dskit/pipeline/kinds_search.py#L435-L500)),
then eagerly expands their complete Cartesian product at
[`kinds_search.py:502`](../../dskit/pipeline/kinds_search.py#L502) before
`n_trials` is consulted at [503-504]. Thus an otherwise valid config such as
many two-value knobs can allocate `2**N` dictionaries, proxies, canonical keys,
and one canonical JSON payload even when the caller asks for `n_trials=1`.
There is no `max_candidates`/equivalent request cap, nor an overflow/count
refusal, and no regression covering the fail-closed boundary.

This is a public, generic config-facing value intended to freeze and serialize
an inventory, not a bounded 24-row child helper. The approved child plan
requires exactly 24 candidates ([plan:57-59](../../children/intraday_equities/docs/plans/2026-09-08-final-model-replay-and-monitoring.md#L57-L59)); it does not make the generic API's unbounded expansion safe. The nearby
generic release-calendar value establishes the repository pattern: require a
caller-owned `max_windows` and refuse count above it before materialization
([`release_rotation.py:304-381`](../../dskit/pipeline/release_rotation.py#L304-L381)).
Without the analogous inventory bound, a malformed or accidentally widened
JSON grid can exhaust process memory/CPU during validation/construction, before
the ledger's atomicity or selector safeguards matter. This violates the
requested serialization/resource-bound review and is unsafe to ship as the
public Phase-1 seam.

**Required correction:** add an explicit positive, non-boolean caller cap
(for example `max_candidates`); compute the product cardinality from the
validated snapshots with early refusal before `_grid`; apply it whether or not
`n_trials` is present (or redesign subsampling to be genuinely bounded). Pin
the cap with a regression that proves `n_trials=1` cannot bypass it. Re-run two
fresh independent skeptic lenses after the fix.

## Architecture checks that passed

- The three values are correctly tier-1, stdlib-only plain objects rather than
  registered nodes; `HpoGrid` remains the registry/driver rerun owner.
- Public exports, `__all__`, package README/AGENTS inventories, ADR number and
  Phase-1-only boundary align. No child implementation, Path edit, data read,
  replay, or later-owner decision entered this diff.
- Ledger snapshots and lock/token transitions preserve immutable evidence and
  canonical inventory order; the focused purity/integration checks substantiate
  the import and planner boundary.
- The current checkout is `main` at `c775ac5`, while `origin/main` is ahead at
  `519cde0` and the prior implementation history is on divergent
  `docs/final-model-replay-adr` (`406aab1`). This is a provenance/re-entry
  concern to reconcile before landing, but not the code finding above.

## Verdict

**FAIL — 0 Critical, 1 Major.** The resource-bound defect means this slice is
not safely committable as the generic public API. This reviewer-owned report is
the Rule-7 trace and must be superseded by fresh reviews after correction.
