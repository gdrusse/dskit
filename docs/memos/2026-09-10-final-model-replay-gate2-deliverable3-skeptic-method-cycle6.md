# Skeptic review — final-model replay Gate 2 deliverable 3, method/API-contract cycle 6

**Reviewer:** GPT-5.6 Terra (fresh method/API-contract skeptic)
**Review target:** `17f4052` relative to `2acfbe1`, with the complete ADR-0115 scope reviewed from `60cf723` through `17f4052`.
**Verdict:** **FAIL — 0 Critical, 2 Major, 0 Minor.**

## Scope and correction check

Read `docs/skills/skeptic-review.md`, current `docs/RE-ENTRY.md`, ADR-0115, and every prior deliverable-3 method and architecture report. Reviewed the complete ADR surface: public validation, `run()` estimator resolution and HPO ordering, `_fit_estimator()`'s least-squares fallback, ledger/selection/artifact semantics, final config, and historical MSPE/IC behavior.

Cycle 5's false-objective Major is fixed: `squared_error_improvement` with evidence absent/false now has a clear validation refusal, before the legacy tuner can mislabel MSPE. Historical MSPE/IC behavior and its two-key output remain intact. The final config is evidence-enabled with the right objective and estimator; the prior inner-split, trial-ledger, day-clustered SE, one-SE, and artifact fixes remain sound.

The estimator correction is only partial. Its new checks use `is None` rather than validating the resolved usable estimator, leaving a least-squares bypass and rejecting the documented universe-level estimator source.

## Findings

### Major — malformed evidence estimator still silently reaches `_Lstsq`

The new runtime guard tests only whether `self.params["estimator"] is None`. An empty string is not `None`; it is copied to `base_scan`, but is falsey at the later `if hpo_trials and base_scan.get("estimator")` condition. No inventory, evidence guard, selection, ledger, or HPO metric runs. On an ordinary outer fold execution then reaches `_fit_estimator()`, whose `if path:` is also false, so it fits its implicit `_Lstsq` model and emits the normal ledger-less two-key output.

This violates the required property: evidence mode must refuse **any missing or invalid estimator before fitting**, never silently emit an unauditable least-squares result. `validate_params()` diagnoses `""`, but `run()` is directly callable (as this module's tests do), and this correction deliberately added a runtime boundary guard; that guard does not cover malformed values.

Resolve and validate the effective estimator before any inner or outer fit, rejecting non-string, empty, and non-importable paths. Add a fit-capable synthetic `run()` regression with `hpo_evidence=true` and `estimator=""`, asserting named refusal and no ledger-less output.

### Major — a valid universe-supplied estimator is rejected before resolution

The class documents node `estimator` as optional and as an override of the universe `scan` block. `run()` implements that established contract by first building `base_scan = dict(spec.get("scan") or {})`, then applying a node override only when present. Existing HPO uses that resolved `base_scan`.

The new static and early-runtime checks instead unconditionally require a node-level estimator under evidence mode, before `spec` is read. A complete evidence declaration with `hpo_trials=1`, valid seed/cuts/space/objective, and a valid `spec["scan"]["estimator"]` is rejected solely because it relies on the documented default source. I evaluated the public validation boundary with that complete parameter object (no node estimator); it returned only:

```text
hpo_evidence requires estimator to be a declared import path; implicit least-squares fits cannot produce HPO evidence
```

That is not fail-closed detection of a missing effective estimator; the effective estimator exists. It breaks the optional-override API and prevents evidence mode from working with a normal universe-owned model declaration. Move the check after `base_scan` is assembled, and add a universe-scan-only evidence regression that emits a ledger, alongside the missing/malformed resolved-estimator refusal regression.

## Focused verification

No full suite or real pipeline run was performed.

```text
PYTHONPATH=/home/russell/wt/gate2-deliverable3:/home/russell/wt/gate2-deliverable3/children/intraday_equities \
  /home/russell/dskit/.venv/bin/python -m pytest -q \
  children/intraday_equities/tests/test_nodes.py \
  children/intraday_equities/tests/test_configs.py \
  children/intraday_equities/tests/test_final_model.py
260 passed, 11 skipped, 5 failed

/home/russell/dskit/.venv/bin/python -m pytest -q tests/pipeline/test_driver.py -k json_artifact
6 passed, 56 deselected

/home/russell/dskit/.venv/bin/ruff check children/intraday_equities/intraday_equities/nodes.py children/intraday_equities/tests/test_nodes.py
All checks passed!

git diff --check 2acfbe1 17f4052
clean
```

The five child-suite failures are the registered unrelated configuration baseline: `test_run_docs_do_not_restate_the_cohort`, `test_every_run_uses_one_local_mlflow_experiment`, `test_the_child_installs_what_its_tracking_sinks_need`, `test_every_run_reads_the_split_adjusted_store_from_the_study_start`, and `test_p16_feature_mask_zoo_masks_are_real_and_isolate_the_feature_set`.

`git diff --check 60cf723 17f4052` additionally reports the pre-existing blank line at EOF in the retained cycle-4 report; the correction-range check is clean. Because the two Major API/correctness defects remain, this is not a Gate 2 deliverable-3 pass.
