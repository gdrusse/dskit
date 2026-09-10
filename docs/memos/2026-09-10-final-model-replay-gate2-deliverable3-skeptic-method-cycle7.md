*** Begin Patch
*** Add File: docs/memos/2026-09-10-final-model-replay-gate2-deliverable3-skeptic-method-cycle7.md
+# Skeptic review - final-model replay Gate 2 deliverable 3, method/API-contract cycle 7
+
+**Reviewer:** GPT-5.6 Terra (fresh method/API-contract skeptic)
+**Review target:** `a1b5898` relative to `9f54266`, with ADR-0115 reviewed from `60cf723` through `a1b5898`.
+**Verdict:** **PASS - 0 Critical, 0 Major, 1 Minor.**
+
+## Scope and correction check
+
+Read `docs/RE-ENTRY.md`, `docs/skills/skeptic-review.md`, ADR-0115, and every retained Gate-2-deliverable-3 skeptic report. Reviewed the full current evidence path rather than trusting the correction diff: parameter and universe validation, effective-estimator assembly/resolution, the least-squares fallback, inner-split refusal, objective/ledger/one-SE selection, artifact persistence, and the legacy branch.
+
+The two cycle-6 Majors are fixed. `run()` first builds `base_scan` from the universe optional `scan` mapping, then applies a node-level estimator override only when declared, and calls `_resolve_estimator(base_scan.get("estimator"))` when `hpo_evidence` is enabled. This happens before candidate inventory setup, inner selection, or the outer `_fit_estimator` call. Thus an empty, non-string, or non-importable effective estimator raises the named evidence refusal before the falsey-path `_Lstsq` fallback can be reached; a valid universe-owned estimator remains valid without a redundant node-level declaration.
+
+The successful universe-owned path is exercised by the focused evidence test: the node omits an estimator while `spec["scan"]` supplies LightGBM, and it emits the `JsonArtifact` ledger. The same test directly exercises node-level `""`, `17`, and a missing dotted import, each refusing before fitting. Narrow direct checks confirmed `_resolve_estimator` refuses `None`, empty, non-string, `.`, and absent-module values and resolves `lightgbm.LGBMRegressor`. A non-mapping universe `scan` is rejected by `_universe_problems` during normal input validation; direct malformed values also error before fitting. The only convertible non-mapping shape (a list of pairs) is still rejected by that schema validation and is not a supported universe document.
+
+No evidence declaration can silently use the legacy tuner: validation rejects `squared_error_improvement` unless `hpo_evidence is True`, and rejects evidence with the old objectives or zero trials. When valid evidence selection runs, it uses the frozen `CandidateInventory`, child-owned `squared_error_improvement`, trading-day cluster bootstrap SE, `OneStandardErrorSelector`, and ruled simplicity key; it reports the selected candidate ledger score, not the unconstrained best score. Insufficient outer or inner data refuses before outer fitting. The complete ledger and selection remain wrapped for the verified JSON-artifact persistence path. Conversely, the absent/false evidence path preserves the historical two-key output and unchanged MSPE/IC tuner.
+
+## Finding
+
+### Minor - malformed `"."` resolver text is less contextual than other paths
+
+`_resolve_estimator(".")` propagates `ValueError("Empty module name")` from `importlib` rather than wrapping it in the usual `scan.estimator ... could not be imported` wording. It is still a deterministic pre-fit `ValueError`, so it cannot choose `_Lstsq`, produce an unledgered result, or confuse estimator identity. This is message consistency only, not a correctness or gate issue.
+
+## Verification
+
+No full suite, real-market-data run, implementation edit, decision/action journal edit, or path edit was performed.
+
+```text
+cd children/intraday_equities
+PYTHONPATH=/home/russell/wt/gate2-deliverable3:/home/russell/wt/gate2-deliverable3/children/intraday_equities \
+  /home/russell/dskit/.venv/bin/python -m pytest -q \
+  tests/test_nodes.py tests/test_configs.py tests/test_final_model.py
+260 passed, 11 skipped, 5 failed
+```
+
+The five failures are the registered unrelated configuration/environment baseline: `test_run_docs_do_not_restate_the_cohort`, `test_every_run_uses_one_local_mlflow_experiment`, `test_the_child_installs_what_its_tracking_sinks_need`, `test_every_run_reads_the_split_adjusted_store_from_the_study_start`, and `test_p16_feature_mask_zoo_masks_are_real_and_isolate_the_feature_set`.
+
+Narrow direct resolver/static-validation probes covered effective-estimator resolution and malformed inputs. Ruff passed on all changed files, and `git diff --check 9f54266 a1b5898` was clean. The wider `git diff --check 60cf723 a1b5898` names only the pre-existing blank EOF in the retained cycle-4 report, not this correction.
+
+With no Critical or Major finding, this fresh method/API lens passes the correction. This report is its independent review trace; it does not claim to substitute for the distinct architecture/governance lens required by the skeptic-review process.
*** End Patch
