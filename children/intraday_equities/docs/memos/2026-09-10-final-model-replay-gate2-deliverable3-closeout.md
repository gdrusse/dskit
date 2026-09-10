# Gate 2 deliverable 3 closeout

## TL;DR

Gate 2 deliverable 3 is closed: method/API passed at `7c0ba3d` with no Critical or Major findings and one accepted Minor, and architecture/governance passed at `d61b86a` with no findings. This proves the configuration and evidence plumbing against focused synthetic checks; no real HPO or market-data execution occurred.

## Execution contract

This deliverable updates `configs/run-final-hpo.json` to consume the P16-pinned selector, build the `lean` pooled-LightGBM finalist from its real recipe sources, and request complete per-lead HPO evidence. It does not run HPO, read market data, perform final refit, or create `configs/run-final-refit.json`.

## Correction record

The initial method review found one Major and two Minors: the reported score belonged to the unconstrained best candidate, one error named a stale model ID, and config notes restated a candidate name. The score and notes were corrected, then the regression and docstring were tightened.

Architecture cycle 1 found that the complete ledger would not survive the driver's carry limit. `JsonArtifact` now writes canonical JSON atomically to a content-addressed path and leaves a small manifest in records and carry. Method cycle 3 then found that the manifest lacked a trusted reader; `resolve_json_artifact` now verifies exact shape, canonical relative path, lowercase digest, byte count, content digest, and JSON decoding, refusing forged, missing, traversing, or tampered artifacts.

Method cycles 4-6 found successive fail-open entrances: too few inner rows, a missing estimator or evidence-only objective used without evidence, and malformed or universe-owned effective estimators. Evidence mode now resolves the effective estimator after the universe scan and node override are combined, refuses invalid estimators and insufficient outer or inner data before fitting, and cannot silently reach the legacy least-squares or tuner paths. Cycle 7 passed with one accepted Minor: the error text for resolver input `"."` is less contextual than the other refusal messages. Architecture cycle 2 independently passed with no findings.

## Durable evidence

On success, each lead's complete candidate ledger and one-standard-error selection are serialized as canonical UTF-8 JSON. The driver is the sole writer; it hashes the bytes it writes, lands them atomically under `artifacts/json/<sha256>.json`, and stores only the verified manifest in run records and carry. The public resolver re-verifies the manifest and file before returning decoded evidence. Ordinary look-alike dictionaries receive no artifact authority.

The final-HPO document validates and plans with identity hash `2db8e95a420614a91101535bb21d1ca4cd2cb6536bdb13791307754ae2805219`. No empirical HPO result, winner, refit, or market conclusion exists yet.

## Focused verification

At the final review target:

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

The five failures exactly match the unrelated registered baseline: `test_run_docs_do_not_restate_the_cohort`, `test_every_run_uses_one_local_mlflow_experiment`, `test_the_child_installs_what_its_tracking_sinks_need`, `test_every_run_reads_the_split_adjusted_store_from_the_study_start`, and `test_p16_feature_mask_zoo_masks_are_real_and_isolate_the_feature_set`.

Ruff passed on every changed Python file. `git diff --check 9f54266..7c0ba3d` and the final working tree were clean. The wider range names only the pre-existing blank EOF in the retained method-cycle-4 report. No full suite ran.

## Reproducibility and handoff

The retained reviewer reports under `docs/memos/2026-09-10-final-model-replay-gate2-deliverable3-skeptic-*` preserve every failure and correction. Deliverable 3 is review-closed at architecture commit `d61b86a`; the next Gate 2 deliverable is `configs/run-final-refit.json`, which remains uncreated and requires its own authorized implementation and verification.
