# Gate 2 — final refit and one bundle: implementation memo

## TL;DR

Gate 2 (Phase 2 of the final-model replay plan — the generic pieces needed
to persist ten fitted models as one verified file, and the domain code that
picks each model's winning settings) is complete for its two foundational
deliverables: a generic multi-head model bundle writer in dskit, and the
domain assembly code in the child. A third piece — editing the existing
HPO config file itself — is deliberately not started yet, explained below.
Two fresh reviewers both signed off after one round of real fixes. No real
data or real model training happened; everything is proven with small
made-up numbers standing in for the real thing.

## Execution contract

- **Branch:** `claude/phase1-recovery-seven-gates-ao4zdj`.
- **Base:** ADR-0114 (accepted) plus its "§11 item 1 — RULED" section
  (the owner's binding standard-error method and simplicity-ordering
  decision) — both from Gate 1.
- **What changed:** `dskit/pipeline/libs/sklearn.py` (the generic bundle
  writer), `dskit/pipeline/node.py` (a shared `atomic_write`, promoted out
  of `dskit/pipeline/kinds_table.py`), `children/intraday_equities/intraday_equities/final_model.py`
  (new — the domain assembly), plus tests and the three `dskit/pipeline`
  package docs.
- **Commits (`95fe75d..HEAD`, Gate 2 portion):**
  - `a9a3112` — first implementation pass (a prior attempt in this same
    session was interrupted mid-work by a rate limit; the orchestrating
    session found and fixed the resulting rough edges directly before this
    commit landed: four tests with an invalid empty `drop=[]` fixture, one
    test declaring the wrong persisted class in a bundle manifest, several
    docstring formatting violations, and one inaccurate architectural claim
    in a new docstring).
  - `da5d898` — retained report: method/API-contract reviewer, PASS.
  - `c7938d8` — retained report: architecture/governance reviewer, FAIL
    (1 Major).
  - `4f38f12` — fixed the Major finding.
  - `b654e97` — closed a Minor test-coverage gap.
  - `18d9b19`, plus this memo's own commit — retained reports: both
    reviewers re-run fresh, both PASS.

## What the three deliverables were, and what actually happened

1. **A generic multi-head model bundle** (`dskit/pipeline/libs/sklearn.py`,
   `write_bundle`/`load_bundle`/`EstimatorBundle`) — done. Generalizes the
   existing single-model save/load pattern (`SklearnFit`'s
   `sklearn-joblib-v1` format) to save TEN named models in one file plus one
   readable manifest, with the same tamper-evidence discipline: the file's
   identity hash covers the model bytes AND every field in the manifest
   that describes what's inside it, so a relabelled model, a reordered
   feature list, or an edited manifest field all fail to load — not just an
   edited model file. Loading also re-runs a small fixed prediction through
   the restored models and checks the answer matches what was recorded at
   save time, catching a corrupted restore that the byte-level check alone
   would miss. Every write happens through one shared, genuinely atomic
   file-write helper (moved to a shared location so two different files no
   longer kept separate copies of the same logic).
2. **The domain assembly** (`children/intraday_equities/intraday_equities/final_model.py`,
   new) — done. Reads the real 33-column exclusion list and the real
   24-combination search grid from their one real source files (never a
   second hand-typed copy), computes each of the ten models' accuracy score
   the way the plan requires (not the shortcut method it explicitly rules
   out), picks each model's winning settings using the owner's ruled
   statistical method, and produces the final ten fitted models ready to
   hand to the bundle writer above.
3. **Editing the existing search config file, and adding its refit sibling**
   — **not started.** This was flagged as the riskiest, most optional part
   from the start: it means safely modifying a large, already-working
   configuration file this session did not build and has not fully traced
   through. Left alone rather than risk a rushed, wrong edit to a document
   real training will eventually run from.

## Review evidence

- **Method/API-contract lens** — PASS both cycles:
  `docs/memos/2026-09-09-final-model-replay-gate2-skeptic-method.md` (0
  Critical, 0 Major, 2 Minor), `-method-2.md` (0 Critical, 0 Major, 1
  Minor — closed the same day).
- **Architecture/integration/governance lens** — cycle 1
  (`-architecture.md`) FAILED with one Major: the search grid was
  hand-copied into the new domain file instead of being read from its real
  source, the exact kind of silent-drift risk this repository's own rules
  exist to catch. Fixed by reading the real config file directly, the same
  way the 33-column exclusion list already did it correctly. Cycle 2
  (`-architecture-2.md`) PASS, 0 Critical, 0 Major, 0 Minor.

One correction cycle, within the two-cycle bound — no design adjudication
needed.

## Verification performed

```
python3 -m pytest -q tests/pipeline_libs/test_sklearn.py tests/pipeline/test_purity.py tests/pipeline/test_method_lengths.py
python3 -m ruff check dskit/pipeline/libs/sklearn.py dskit/pipeline/node.py dskit/pipeline/kinds_table.py tests/pipeline_libs/test_sklearn.py children/intraday_equities/intraday_equities/final_model.py children/intraday_equities/tests/test_final_model.py
git diff --check
cd children/intraday_equities && PYTHONPATH=/home/user/dskit:/home/user/dskit/children/intraday_equities python3 -m pytest -q tests/test_final_model.py
```

Final state: 237 dskit-side tests passed (9 skipped — unrelated,
pre-existing registry-shape skips), 34 child-side tests passed, Ruff clean,
`git diff --check` clean.

## Deliberately not run / not done

- No real market data read, no real LightGBM fit against real history, no
  real P16 artifact loaded — every test uses small made-up numbers.
- No pipeline execution of any kind (`run`/`walkforward`) — only direct
  Python calls to the new functions.
- `configs/run-final-hpo.json` was not modified; `configs/run-final-refit.json`
  was not created (deliverable 3, above).
- The full test suite was not run (task scope: focused tests only).

## Artifacts

- Code: `dskit/pipeline/libs/sklearn.py` (`write_bundle`, `load_bundle`,
  `EstimatorBundle`), `dskit/pipeline/node.py` (`atomic_write`),
  `children/intraday_equities/intraday_equities/final_model.py`.
- Tests: `tests/pipeline_libs/test_sklearn.py`,
  `children/intraday_equities/tests/test_final_model.py`.
- Four retained reviewer reports, linked above under "Review evidence".
- One journal action recorded (`A18856`); `docs/decisioning/path.csv`
  untouched throughout.

## Reproducibility and handoff

Reproduce from `origin/claude/phase1-recovery-seven-gates-ao4zdj` at the
commit this memo is committed alongside, using the exact commands above.
Not merged into `main`.

**Next:** the deferred config edit (deliverable 3) needs its own careful
pass before Gate 2 is fully complete, tracing `model_zoo.py`'s and
`benchmarks.py`'s existing stage machinery first. Gates 3 through 7 of the
same plan remain; several (3, 5's conformance half, 6) are being worked in
parallel on separate branches per the owner's request.
