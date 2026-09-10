# Skeptic review — final-model replay Gate 2 deliverable 3, method/API-contract cycle 4

**Reviewer:** GPT-5.6 Terra (fresh method/API-contract skeptic)
**Review target:** `03a03ac` relative to `d9eb132`, with ADR-0115 reviewed from `60cf723` through `03a03ac`.
**Verdict:** **FAIL — 0 Critical, 1 Major, 0 Minor.**

## Scope and method

Read `docs/skills/skeptic-review.md`, current `docs/RE-ENTRY.md`, ADR-0115, both earlier method reports, and the architecture FAIL report. Reviewed the complete ADR implementation, not only the correction diff: the evidence-mode `NoInformationScan` branch and validation, `final_model` selection ownership, model-zoo parameter copying, final-HPO configuration, and generic driver artifact persistence.

The correction genuinely closes cycle 3's artifact-manifest Major. Only the private `_JsonArtifactManifest` tag receives special live-record treatment, so an ordinary four-key dict is again summarized normally. The public `resolve_json_artifact(run_dir, manifest)` accepts a serialized manifest only when its four-key shape, lowercase 64-hex digest, and exact canonical relative path `artifacts/json/<sha256>.json` agree. It refuses malformed, absolute, traversal, and backslash paths before file access; then refuses missing files, byte-count drift, digest drift, and invalid JSON before returning a value. The writer hashes the same canonical UTF-8 bytes it atomically writes, and the manifest retained in records/carry is that tagged live object serialized as a plain dict. The added tests exercise genuine persistence, tampering, forged ordinary dicts, noncanonical paths, missing artifacts, and digest drift.

The earlier selected-score defect is still correctly fixed and pinned. The ordinary `hpo_evidence=False` path remains two-output and continues through the untouched `_hpo_combos`/`_tune_estimator` behavior. Evidence mode uses the configured deterministic `CandidateInventory`, child-owned squared-error-improvement/day clustering, and `run_lead_selection`'s ruled one-SE/simplicity policy. `run-final-hpo.json` supplies the intended 24, seed 0, evidence-enabled configuration.

## Finding

### Major — `hpo_evidence: true` can silently train an untuned base model and emit no evidence

ADR-0115 says that when `hpo_evidence` is true the scan builds the inventory, records every candidate's score/SE, performs the one-SE selection, and emits the complete ledger. That is the entire audit claim for the final-HPO document. The implementation instead makes it conditional on an implicit data-size gate and silently falls through when the gate is unmet.

`nodes.py:5824-5861` builds the evidence inventory, calls `_scan_fold_stamped` for the inner split, and invokes `_hpo_evidence_selection` only if both `in_x.shape[0] >= 2` and `ho_x.shape[0] >= 2`. If either is smaller, it neither raises nor records a refusal. `scan` remains `dict(base_scan)`; the later outer-fold `_fit_estimator(tr_x, tr_y, scan, ...)` therefore trains the unsearched base parameters. `hpo_ledger_obj` remains `None`, so `nodes.py:6014-6022` returns only `records` and `metrics`, with neither `hpo_ledger` nor an HPO metric.

This is reachable through public, validated configuration: validation requires only `hpo_trials > 0`, numeric HPO knobs, and a non-empty space. It does not establish that the declared `hpo_val_days`/embargo and available data leave two inner-train and two inner-holdout rows. `_hpo_cuts` simply computes timestamp boundaries, and sparse/short data or a long declared inner window can leave either array below two rows. No test covers this evidence-required failure case; the existing evidence test only covers the successful branch.

This is not acceptable compatibility behavior for the new opt-in contract: the final configuration explicitly declares `hpo_evidence: true`, so a completed run without the required per-lead candidate ledger and 1-SE winner would look like a valid final-HPO result while actually fitting its template parameters. Evidence mode must fail closed with a clear error whenever an inner evidence selection cannot be performed (and should gain a regression test). The legacy non-evidence path may retain its historical skip behavior.

## Focused verification

No full suite or real run was performed.

```text
/home/russell/dskit/.venv/bin/python -m pytest -q \
  tests/pipeline/test_driver.py -k json_artifact
6 passed, 56 deselected

cd children/intraday_equities && PYTHONPATH=/home/russell/wt/gate2-deliverable3:/home/russell/wt/gate2-deliverable3/children/intraday_equities \
  /home/russell/dskit/.venv/bin/python -m pytest -q \
  tests/test_nodes.py tests/test_configs.py tests/test_final_model.py
259 passed, 11 skipped, 5 failed
```

The five failures exactly match the registered environmental/config baseline: `test_run_docs_do_not_restate_the_cohort`, `test_every_run_uses_one_local_mlflow_experiment`, `test_the_child_installs_what_its_tracking_sinks_need`, `test_every_run_reads_the_split_adjusted_store_from_the_study_start`, and `test_p16_feature_mask_zoo_masks_are_real_and_isolate_the_feature_set`.

```text
ruff check dskit/pipeline/driver.py tests/pipeline/test_driver.py \
  children/intraday_equities/intraday_equities/{nodes.py,final_model.py,model_zoo.py} \
  children/intraday_equities/tests/{test_nodes.py,test_configs.py,test_final_model.py}
All checks passed!

git diff --check d9eb132 03a03ac
git diff --check 60cf723 03a03ac
clean
```

The artifact correction is sound, but the evidence-mode silent fallback is a Major correctness defect. This correction cycle is not a clean gate pass.

