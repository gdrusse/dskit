# Gate 2 deliverable 3 — architecture/governance skeptic review

**Reviewer:** GPT-5.6 Terra (independent architecture/governance lens)
**Review target:** `f67accd` against `60cf723`
**ADR:** ADR-0115
**Verdict:** **FAIL** — 0 Critical, **1 Major**, 0 Minor.

## Scope and method

Read `docs/RE-ENTRY.md`, `docs/skills/skeptic-review.md`, ADR-0115, the
complete changed `NoInformationScan` evidence path, `final_model`, the
model-zoo expansion, the final-HPO config, the decision journal and the
affected tests. This was a fresh, adversarial architecture/governance pass;
it did not rely on the prior method-lens verdict.

The required generic/child boundary is otherwise respected: the generic
`CandidateInventory`/`TrialLedger`/selector and bootstrap seam remain in
`dskit`; the child owns squared-error semantics, session-day grouping and
simplicity policy. `hpo_evidence` is default-false, validates its supported
objective/inputs explicitly, and the historical two-output return shape is
retained for callers that do not opt in. The P16 source and its digest are
pinned in config, and A18858 correctly records the follow-up identity repair.

## Findings

### Major — the required complete per-lead evidence is not durably recorded

ADR-0115 requires each lead's *complete ledger* to be emitted/persisted. The
new `hpo_ledger` is only an in-memory node output. It is not wired to any
downstream node in `_pooled_document` (only `records` and `metrics` are
wired). The driver writes node records through `_summarize`, which replaces
any dict with only `{"type": "dict", "len": 2}`; it never writes this
ledger as an artifact. The remaining generic persistence route is
`carry.json`, but it rejects a single value whose canonical JSON exceeds
20,000 characters (`_CARRY_LIMIT`).

This is not a theoretical limit: exercising the shipped evidence fixture
with its complete eight-candidate inventory produced an `hpo_ledger` of
7,889 JSON bytes (four candidates already take 4,307 bytes). The actual
config asks for 24 candidates and also carries the selection's complete
simplicity order; it exceeds the carry ceiling. Thus a real final-HPO run
will retain at most a summary for every `scan_hNN`, not the evidence needed
to audit or replay the selection. Direct unit calls pass because they inspect
the transient return object before the driver records it.

**Required correction:** write each lead's ledger/selection as an explicit,
atomic, content-addressed run artifact (and return a small manifest/path plus
digests), or add a properly governed persistence seam. Wire and test it via
the driver/run-record path for the real 24-trial shape; do not rely on
`carry.json` as evidence storage. Re-run fresh skeptic review after the fix.

## Verification performed

- `python -m pytest -q children/intraday_equities/tests/test_nodes.py children/intraday_equities/tests/test_configs.py children/intraday_equities/tests/test_final_model.py`
  - 259 passed, 11 skipped; 5 failures are the documented pre-existing
    configuration/environment failures, unchanged from baseline.
- Ruff on touched implementation and test files: passed.
- `git diff --check 60cf723 f67accd`: clean.

No full suite, real-market-data run, implementation edit, or decision/path
journal edit was performed by this reviewer.
