# Skeptic review — final-model replay Gate 2 deliverable 3, architecture/governance cycle 2

**Reviewer:** GPT-5.6 Terra (fresh architecture/governance skeptic)

**Review target:** `7c0ba3d`, independently traced from the accepted
ADR-0115 base `60cf723` through the current correction chain.

**Verdict:** **PASS — 0 Critical, 0 Major, 0 Minor.**

## Scope and independent review

Read `docs/skills/skeptic-review.md`, the current `docs/RE-ENTRY.md`,
ADR-0115 (and its ADR-0114 §11.1 dependency), all retained
Gate-2-deliverable-3 skeptic reports, the full `60cf723..7c0ba3d` delta,
and the surrounding driver/node/child/config contracts. This is an
independent architecture/governance pass; prior reports were used to locate
previous failure modes, not as evidence that they are resolved.

I specifically attempted to break layered ownership, config/source identity,
per-lead evidence completeness, artifact durability, and error handling.

## Findings

None at Critical, Major, or Minor severity.

### Ownership and API boundary

The generic layer owns only the opt-in persistence mechanism:
`JsonArtifact`, driver materialization, and public read-side verification.
It neither names HPO fields nor imports child code. The child owns the
ADR-0115 scoring policy: it imports the existing child objective,
day-clustering, and one-SE/simplicity assembly rather than duplicating them.
`NoInformationScan` retains its exact historical two-output contract unless
the explicit `hpo_evidence` opt-in successfully completes; its narrow output
validator permits only that old shape or that shape plus `hpo_ledger`.

`_MODEL_FIELDS` propagates every HPO declaration, including
`hpo_evidence`, through `_pooled_document` to each independently fitted
`scan_h01` through `scan_h10`. Each receives a deep-copied identical frozen
space/seed/trial declaration. The only generated downstream inputs are the
existing records/metrics pair, so the large evidence payload is not smuggled
through the path-score API or carry as a generic bulk object.

### Source/config identity and fail-closed evidence

The final config declares one P16-pinned selector source and one `lean`,
`pooled-lightgbm` finalist recipe. `FinalistCandidate` still derives the
recipe identity from the selector output and refuses a missing recipe.
The HPO grid reader now keys on the stable model family, requires exactly
one such recipe, and the config's 33-name literal drop mask is pinned against
the existing `lean_feature_drop()` source. Validation and planning confirm
the final document remains a plan-only four-stage document; no market run was
performed.

Evidence mode requires both a nonzero trial declaration and the only accepted
objective. Before any inventory, fit, fallback, or output construction it
resolves the effective estimator after applying an optional node override to
the universe-owned scan. Thus malformed/missing estimators cannot reach the
implicit least-squares branch, and a valid universe-owned estimator remains
usable. Insufficient outer or inner rows raise before outer fitting. A failure
cannot return an `hpo_ledger`, so it cannot make final HPO look auditable or
valid without trial evidence.

### Artifact integrity, path governance, and atomicity

On a successful node result, the driver serializes the explicit wrapper as
canonical JSON, hashes those exact bytes, atomically lands them under the
content-addressed canonical relative path, and replaces the wrapper with an
internally tagged manifest before records/carry are written. The public
resolver rechecks exact manifest shape, canonical path, lowercase digest,
size, file presence, actual byte count, digest, and JSON decoding. It refuses
absolute, traversal, backslash, forged, missing, and tampered forms. An
ordinary dict that resembles a manifest is neither treated specially during
the live run nor granted resolver authority. Atomic same-directory replacement
prevents a partially written named artifact; a content-address collision with
different bytes refuses. The driver is the sole writer and no decisioning
journal/action/path file is changed by this work.

## Verification

No full suite and no market-data run were performed.

```text
PYTHONPATH=/home/russell/wt/gate2-deliverable3 \
  /home/russell/dskit/.venv/bin/python -m pytest -q tests/pipeline/test_driver.py
62 passed

cd children/intraday_equities
PYTHONPATH=/home/russell/wt/gate2-deliverable3:/home/russell/wt/gate2-deliverable3/children/intraday_equities \
  /home/russell/dskit/.venv/bin/python -m pytest -q \
  tests/test_nodes.py tests/test_configs.py tests/test_final_model.py
260 passed, 11 skipped, 5 failed
```

The five child failures are the existing unrelated configuration baseline:
`test_run_docs_do_not_restate_the_cohort`,
`test_every_run_uses_one_local_mlflow_experiment`,
`test_the_child_installs_what_its_tracking_sinks_need`,
`test_every_run_reads_the_split_adjusted_store_from_the_study_start`, and
`test_p16_feature_mask_zoo_masks_are_real_and_isolate_the_feature_set`.

Ruff passed on every changed Python file. `git diff --check` is clean for the
working tree and `git diff --check 9f54266..7c0ba3d` is clean. The broader
`60cf723..7c0ba3d` check reports only the pre-existing blank EOF in retained
method cycle 4, outside the final correction. `validate` and `plan` both pass
for `configs/run-final-hpo.json` (document hash
`2db8e95a420614a91101535bb21d1ca4cd2cb6536bdb13791307754ae2805219`).

This report is the independent architecture/governance trace for this cycle;
it does not substitute for any separate method/API review.
