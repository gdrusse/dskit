# Skeptic review — final-model replay Gate 2 deliverable 3, method/API-contract cycle 5

**Reviewer:** GPT-5.6 Terra (fresh method/API-contract skeptic)
**Review target:** `1ce347f` relative to `a926a2b`, with the complete ADR-0115 scope reviewed from `60cf723` through `1ce347f`.
**Verdict:** **FAIL — 0 Critical, 2 Major, 0 Minor.**

## Scope and correction check

Read `docs/skills/skeptic-review.md`, current `docs/RE-ENTRY.md`, ADR-0115 as introduced in `60cf723`, and every preceding delivery-3 method and architecture skeptic report. The owner corrected the review cap to 15 cycles; this is a correction review, not a closure claim.

The exact cycle-4 defect is fixed: when an evidence inventory exists, `NoInformationScan.run()` now raises before outer fitting if the outer train has fewer than two rows, and raises if either inner train or holdout has fewer than two rows. The added regression exercises the latter path while a legacy non-evidence run keeps the historical two-output shape. The selected candidate's own score remains correctly reported. The retained-manifest resolver and one-SE ledger path were also rechecked; neither causes the findings below.

## Findings

### Major — a valid `hpo_evidence: true` declaration can still bypass selection and fit the implicit base model

The new fail-closed guards only execute after `inventory` is created. But `run()` creates an inventory only under `if hpo_trials and base_scan.get("estimator")` (`nodes.py:5742-5758`). `base_scan` is assembled from the universe's optional `scan` object and the node's optional `estimator` override. Nothing in `validate_params()` requires an estimator when `hpo_evidence` is true (`nodes.py:5394-5489`). I directly evaluated a fully otherwise-valid evidence parameter object with `hpo_trials=1`, all required HPO knobs, and no estimator; `NoInformationScan.validate_params(...)` returned `[]`.

At runtime this leaves both `inventory` and `combos` empty. None of the new evidence guards execute, no ledger is emitted, and the ordinary outer-fit code reaches `_fit_estimator(tr_x, tr_y, scan, ...)`. Its documented and actual no-estimator behavior is an implicit least-squares `_Lstsq` fit (`nodes.py:3167-3214`). This is exactly an accidental base-model fit/output under an explicit evidence-required declaration, merely through a different entrance than the insufficient-inner-data case fixed in this commit.

ADR-0115's contract is not conditional on a hidden estimator-presence test: when evidence is requested it requires the frozen inventory, exhaustive candidate ledger, one-SE winner, and ledger output. Fail closed when evidence is requested but no effective estimator is available (or require it during validation where possible); do not silently use the test-only least-squares fallback. Add a regression that removes both the node estimator and universe scan estimator, then asserts a named refusal and no output.

### Major — the accepted squared-error-improvement objective is silently executed as MSPE when evidence is false

`validate_params()` accepts `hpo_objective="squared_error_improvement"` with `hpo_evidence` absent or false. Its only cross-field condition is the reverse one: evidence requires the squared-error objective (`nodes.py:5448-5458`). The class documentation, however, says the squared-error objective "requires `hpo_evidence: true`" (`nodes.py:5272-5277`).

This is semantic, not cosmetic. In the resulting legacy branch, `_tune_estimator()` recognizes only `"ic"`; every other objective—including `"squared_error_improvement"`—falls into `_mspe()` minimization (`nodes.py:3329-3388`). The caller then names the returned MSPE value `metrics["hpo_squared_error_improvement"]` and logs that same false label (`nodes.py:5883-5905`). Thus an accepted configuration can claim the ADR's training-mean-baseline improvement objective while selecting by a different, opposite-direction metric and reporting it as improvement.

Either reject squared-error-improvement unless `hpo_evidence is True`, as the public contract already says, or implement that objective faithfully in a separate non-evidence path (which would not satisfy ADR-0115's required ledger/one-SE evidence). A focused validation and behavioral regression must make the impossible state unrepresentable.

## Other checked contracts

- The actual final-HPO recipe is correctly evidence-enabled and uses the `lean`/`pooled-lightgbm` identity path; the defects are public API states outside that happy configuration, not an assertion that the config currently omits its estimator.
- `hpo_evidence=False` with historical `mspe` or `ic` values retains the old `_hpo_combos`/`_tune_estimator` behavior and exact two-key output shape. The new failure is that an accepted *new* objective is incorrectly routed through that legacy behavior.
- The inner-data refusal from `1ce347f` is correctly before the outer model fit whenever an evidence inventory was constructed; it does not cover the pre-inventory bypass above.

## Focused verification

No full suite or real run was performed.

```text
cd children/intraday_equities
PYTHONPATH=/home/russell/wt/gate2-deliverable3:/home/russell/wt/gate2-deliverable3/children/intraday_equities \
  /home/russell/dskit/.venv/bin/python -m pytest -q \
  tests/test_nodes.py tests/test_configs.py tests/test_final_model.py
260 passed, 11 skipped, 5 failed
```

The five failures are the registered configuration/environment baseline: `test_run_docs_do_not_restate_the_cohort`, `test_every_run_uses_one_local_mlflow_experiment`, `test_the_child_installs_what_its_tracking_sinks_need`, `test_every_run_reads_the_split_adjusted_store_from_the_study_start`, and `test_p16_feature_mask_zoo_masks_are_real_and_isolate_the_feature_set`.

```text
/home/russell/dskit/.venv/bin/ruff check \
  children/intraday_equities/intraday_equities/nodes.py \
  children/intraday_equities/tests/test_nodes.py
All checks passed!

git diff --check a926a2b 1ce347f
git diff --check
clean
```

The short-inner-split correction is real, but the two accepted public states above still permit evidence/objective misrepresentation. This is not a clean Gate 2 deliverable-3 pass.
