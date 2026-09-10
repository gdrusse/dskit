# Decisioning

CSV is the store (`actions.csv`, `path.csv`). This README is **generated**
— do not edit it. Append a CSV row or run `python -m dskit.journal promote`.

## Process

Many things get tried. The Actions table is the full tape. Path to
Production is the owner-selected linear chain (a subset of those IDs).

```
acquire  →  research  →  execute  →  production
 pull         finding      fit          live
```

1. **Acquire** — `python -m dskit.onboarding` `register-source` /
   `acquire --mode backfill|live` / `validate` / `certify` / `publish`.
   `watch` is one row per process, not per pull. **Automatic.**
2. **Research** — only
   `python -m dskit.journal research "TITLE" --topic T --name N --body-file <draft>`.
   Writes `docs/research/<topic>/<YYYY-MM-DD>-<name>.md` and the row
   together. Default name is `synthesis`. No markdown in the research
   root. Never write that folder by hand. Skills: `record-research`
   and `deep-research` (Cursor, Claude, OpenCode).
3. **Execute** — `python -m dskit.pipeline run|walkforward`.
   **Automatic** after RECORD. Walk-forward is one row, not per fold.
4. **Production** — wrap `live.main` in
   `dskit.journal.hooks.production`. One row per process, not per tick.

The ledger is CSV, not a database. **Database Location** is a pointer
to that action's artifacts (onboarding root, run dir, research file).
MLflow / the asset store hold their own records when used.

**Path to Production** is human-owner-only: only the owner may add or edit a
row, including **Current Work**. Agents and hooks never write it. Every row
has a short label, purpose, relevant evidence files (pipeline run, research
markdown, or other material evidence), and **LOCKED** (`Y` / `N`). Pytest
does not record. A child without `journal.json` refuses acquire / run / live.

## Actions (latest 10)

Display only: `actions.csv` remains the complete, append-only journal.

| ID | Category | Step | Execution Date | Relevant Inputs | Relevant Outputs | Database Location | Notes |
|---|---|---|---|---|---|---|---|
| A18849 | execute | staged run | 2026-09-08T11:53:39+00:00 | /home/russell/dskit/children/intraday_equities/configs/run-p16-feature-mask-zoo.json | /home/russell/dskit/children/intraday_equities/pipeline_runs/p16-feature-mask-zoo-staged-2026-02-28-98635601/stages/run.json |  | stage_token=986356010e3661192b3758d53d03a3cd060392dafc2c6ac3af4563dbd140f13d:run; state=ran; sha256=30310d89c4ea99fdc224ac497c535286c8a4e6cc9f3ee103338270cd99ca2d9a; reason=canonical path repair supersedes A18837 |
| A18850 | execute | staged compare | 2026-09-08T11:53:39+00:00 | /home/russell/dskit/children/intraday_equities/configs/run-p16-feature-mask-zoo.json | /home/russell/dskit/children/intraday_equities/pipeline_runs/p16-feature-mask-zoo-staged-2026-02-28-98635601/stages/compare.json |  | stage_token=986356010e3661192b3758d53d03a3cd060392dafc2c6ac3af4563dbd140f13d:compare; state=ran; sha256=f29bbfa51b1c84bf5e686d9c63c39637d0b1e8f70116ab2a69f6d894a729061c; reason=canonical path repair supersedes A18839 |
| A18851 | execute | intraday-equities-mio-demo | 2026-09-08T19:09:27+00:00 | configs/run-mio-demo.json | /home/user/dskit/children/intraday_equities/pipeline_runs/intraday-equities-mio-demo-2026-01-01-f089d60c | /home/user/dskit/children/intraday_equities/pipeline_runs/intraday-equities-mio-demo-2026-01-01-f089d60c | state=ran hash=f089d60c asof=2026-01-01 |
| A18852 | execute | intraday-equities-mio-demo | 2026-09-08T19:09:55+00:00 | configs/run-mio-demo.json | /home/user/dskit/children/intraday_equities/pipeline_runs/intraday-equities-mio-demo-2026-01-01-fd1b8cad | /home/user/dskit/children/intraday_equities/pipeline_runs/intraday-equities-mio-demo-2026-01-01-fd1b8cad | state=ran hash=fd1b8cad asof=2026-01-01 |
| A18853 | execute | intraday-equities-mio-demo | 2026-09-08T19:10:10+00:00 | configs/run-mio-demo.json | /home/user/dskit/children/intraday_equities/pipeline_runs/intraday-equities-mio-demo-2026-01-02-fd1b8cad | /home/user/dskit/children/intraday_equities/pipeline_runs/intraday-equities-mio-demo-2026-01-02-fd1b8cad | state=ran hash=fd1b8cad asof=2026-01-02 |
| A18854 | research | Gate 1: Phase 0 ADR-0114 proposal and owner packet | 2026-09-09T19:30:32+00:00 |  | docs/architecture/decision-log.md (ADR-0114); docs/memos/2026-09-09-final-model-replay-phase0-closeout.md |  | ADR-0114 proposed (not accepted); restates all 10 plan section-11 items unresolved; no implementation code written |
| A18855 | research | ADR-0114 accepted; §11 item 1 ruled (SE method + simplicity ordering) | 2026-09-09T20:40:20+00:00 |  | docs/architecture/decision-log.md (ADR-0114 status + §11 item 1 ruling) |  | Owner accepted ADR-0114 as proposed; ruled §11 item 1: se via dskit.pipeline.stats.cluster_bootstrap_t clustered by trading day; simplicity key = (num_leaves, learning_rate, -min_child_samples, -reg_lambda, -reg_alpha) ascending. Unblocks Phase 2 (Gate 2). Nine §11 items remain open. |
| A18856 | research | Gate 2: sklearn.py multi-head bundle writer + final_model.py domain assembly | 2026-09-09T23:15:29+00:00 |  | dskit/pipeline/libs/sklearn.py (write_bundle/load_bundle/EstimatorBundle); dskit/pipeline/node.py (atomic_write, promoted from kinds_table.py); intraday_equities/final_model.py; tests/pipeline_libs/test_sklearn.py; intraday_equities/tests/test_final_model.py |  | ADR-0114 Phase 2, deliverables 1+2 complete (generic bundle writer + domain assembly), synthetic tests only. Deliverable 3 (configs/run-final-hpo.json edit + run-final-refit.json) deferred to a follow-up pass -- not started. 237+29 focused tests pass, ruff clean, diff --check clean. |
| A18857 | research | ADR-0115: hpo_evidence in NoInformationScan; run-final-hpo.json P16 selection | 2026-09-10T00:30:38+00:00 |  | intraday_equities/nodes.py (hpo_evidence param, _hpo_evidence_selection, validate_outputs override); intraday_equities/model_zoo.py (_MODEL_FIELDS += hpo_evidence); configs/run-final-hpo.json (select.sources -> one pinned P16 compare.json A18850, finalist lgbm template -> ColumnSubsetEstimator + 33-col lean drop + squared_error_improvement + hpo_evidence=true, torch-mlp removed); tests/test_nodes.py + tests/test_configs.py (new tests) |  | Deliverables 1-3 complete and verified: hpo_evidence=False/absent is a provable no-op (regression test + full test_nodes.py/test_configs.py runs unchanged vs pre-edit baseline, 6 pre-existing environmental failures unchanged, 221 passed incl. 7 new). Deliverable 4 (run-final-refit.json) left undone: no existing stage wires pinned HPO winners into final_model.refit_heads/sklearn.write_bundle, and designing that wiring is new scope beyond ADR-0115. KNOWN GAP flagged in config notes and this record: run-final-hpo.json's sole finalist template keeps id='lgbm' (matches ADR-0115 text and preserves the already-passing final_model.hpo_space() Gate-2 test, which hardcodes that id) even though P16's real winning candidate name is 'lean'+horizon-suffixed, so FinalistCandidate's recipe lookup will only actually match once a future ADR resolves this id conflict; untestable in this gate (no run, no real P16 artifact). validate/plan only, no run, no market data, path.csv untouched. |
| A18858 | research | ADR-0115 follow-up: resolve the lgbm/lean id conflict A18857 flagged | 2026-09-10T00:36:44+00:00 |  | intraday_equities/final_model.py (hpo_space() now matches by family=='pooled-lightgbm', not a fixed id); configs/run-final-hpo.json (finalist template id -> 'lean', matching P16's real winning candidate; notes text fixed to avoid literally restating the candidate's horizon-suffixed name, which a pre-existing test forbids); tests/test_final_model.py + tests/test_configs.py updated to match |  | Resolves the KNOWN GAP A18857 flagged rather than left dangling: hpo_space() previously required a fixed id=='lgbm', which would never match FinalistCandidate's own '{id}-pooled-h{horizon}' lookup once BenchmarkSelect reports the real P16 winner 'lean-pooled-h10'. Fixed hpo_space() to match by family (a stable identity) instead of id (now correctly the finalist's candidate-name component), and renamed the config template's id to 'lean'. Caught and fixed one regression along the way: my own explanatory notes text in the config literally embedded the substring 'pooled-h', which test_the_finalist_names_no_model_and_takes_the_selectors_winner correctly refuses (this document must never restate a candidate name) -- reworded without changing meaning. Full verification: test_final_model.py (34) + test_configs.py (71 passed, same 5 pre-existing environmental failures as the pre-ADR-0115 baseline, confirmed via git stash) + test_nodes.py (186 passed with pyarrow installed) all green; ruff clean; git diff --check clean; validate/plan on the edited config succeed (new hash 2db8e95a...). No run, no market data, path.csv untouched. |

## Path to Production

| ID | Label | Purpose | Relevant Files | LOCKED | Current Work (owner only) | Category | Step | Decision Criteria | DB Location |
|---|---|---|---|---|---|---|---|---|---|
| A2822 | Gate 1: stock modelability | Lock the stock-modelability selection gate | children/intraday_equities/pipeline_runs/p12-63-asset-modelability-staged-2026-02-28-2d203f5c/stages/gate1.json; children/intraday_equities/configs/program-calendar.json; docs/decisioning/framework.md | Y |  | execute | staged gate1 | empirical |  |
| A2850 | Gate 2: HFDR in MIO | Replace the retired Bonferroni screen with an MIO constraint on false-signal gross capital | docs/architecture/decision-log.md#ADR-0088; docs/decisioning/framework.md | Y |  | research | HFDR constrained in MIO | judgemental | docs/architecture/decision-log.md |
| A2851 | ~~Gate 3: shuffle refit~~ | Audit Gate-1 selections against a session-scramble refit null | docs/research/gate3-lower-compute-null-design.md; docs/architecture/decision-log.md#ADR-0089 | N | Investigating a faster valid shuffle-training solution | research | gate3-lower-compute-null-design | empirical | docs/research/gate3-lower-compute-null-design.md |
| A2887 | Gate 3: fail-fast scramble refit | Audit every Gate-1 passer against whole-session scramble refits, stopping at the first null that matches or beats the real result | docs/architecture/decision-log.md#ADR-0092; docs/architecture/decision-log.md#ADR-0093; docs/architecture/decision-log.md#ADR-0094; children/intraday_equities/docs/memos/p12-gate3-recovery-results.md; children/intraday_equities/pipeline_runs/p12-g3-recovery-staged-2026-02-28-a1f293a2/stages/gate3_recovery.json; children/intraday_equities/configs/program-calendar.json | Y |  | execute | Gate 3: fail-fast scramble audit over asset-local walks | empirical | docs/architecture/decision-log.md |
| A18039 | Per-signal pi estimator | Estimate calibrated posterior false-signal probability from out-of-fold and Gate-3 null evidence | docs/research/hfdr-mio-uncertainty/2026-09-05-local-fdr-pi.md; docs/architecture/decision-log.md#ADR-0088 | N |  | research | hfdr-mio-uncertainty/2026-09-05-local-fdr-pi | empirical | docs/research/hfdr-mio-uncertainty/2026-09-05-local-fdr-pi.md |
| A18040 | Conservative pi HFDR | Bound false-signal gross capital with conservative pi inside the MIO | docs/research/hfdr-mio-uncertainty/2026-09-05-conservative-pi-hfdr.md; docs/architecture/decision-log.md#ADR-0088 | N |  | research | hfdr-mio-uncertainty/2026-09-05-conservative-pi-hfdr | judgemental | docs/research/hfdr-mio-uncertainty/2026-09-05-conservative-pi-hfdr.md |
| A18041 | Mean-alpha confidence intervals | Estimate uncertainty in net conditional mean alpha under temporal dependence | docs/research/hfdr-mio-uncertainty/2026-09-05-mean-alpha-intervals.md; docs/architecture/decision-log.md#ADR-0088 | N |  | research | hfdr-mio-uncertainty/2026-09-05-mean-alpha-intervals | empirical | docs/research/hfdr-mio-uncertainty/2026-09-05-mean-alpha-intervals.md |
| A18042 | Realized-return uncertainty | Calibrate dependent predictive intervals and joint return scenarios separately from mean alpha | docs/research/hfdr-mio-uncertainty/2026-09-05-dependent-return-calibration.md; docs/architecture/decision-log.md#ADR-0088 | N |  | research | hfdr-mio-uncertainty/2026-09-05-dependent-return-calibration | empirical | docs/research/hfdr-mio-uncertainty/2026-09-05-dependent-return-calibration.md |
| A18044 | Joint U_pi set | Represent joint uncertainty in false-signal probabilities for robust HFDR constraints | docs/research/hfdr-mio-uncertainty/2026-09-05-u-pi.md; docs/architecture/decision-log.md#ADR-0088 | N |  | research | hfdr-mio-uncertainty/2026-09-05-u-pi | judgemental | docs/research/hfdr-mio-uncertainty/2026-09-05-u-pi.md |
| A18046 | Joint U_mu set | Represent joint uncertainty in expected net alpha for conservative optimization | docs/research/hfdr-mio-uncertainty/2026-09-05-u-mu.md; docs/architecture/decision-log.md#ADR-0088 | N |  | research | hfdr-mio-uncertainty/2026-09-05-u-mu | judgemental | docs/research/hfdr-mio-uncertainty/2026-09-05-u-mu.md |
| A18047 | Joint U_r scenarios | Represent dependent joint net-return outcomes for CVaR drawdown and Kelly risk | docs/research/hfdr-mio-uncertainty/2026-09-05-u-r.md; docs/architecture/decision-log.md#ADR-0088 | N |  | research | hfdr-mio-uncertainty/2026-09-05-u-r | empirical | docs/research/hfdr-mio-uncertainty/2026-09-05-u-r.md |
| A18256 | Final model MIO bundle | Require every promoted final model to publish the complete versioned fail-closed MIO forecast bundle before capital eligibility | docs/research/hfdr-mio-uncertainty/2026-09-05-final-mio-forecast-bundle.md; configs/run-p13-model-zoo.json; docs/architecture/decision-log.md#ADR-0088 | Y |  | research | hfdr-mio-uncertainty/2026-09-05-final-mio-forecast-bundle | judgemental | docs/research/hfdr-mio-uncertainty/2026-09-05-final-mio-forecast-bundle.md |
| A18623 | Predictive program calendar | Lock one temporal source of truth for modelability, model-zoo selection, finalist HPO/refit, uncertainty calibration, simulation, and production | children/intraday_equities/configs/program-calendar.json; children/intraday_equities/docs/research/predictive-program-calendar/2026-09-05-synthesis.md; docs/architecture/decision-log.md#ADR-0098 | Y |  | research | predictive-program-calendar/2026-09-05-synthesis | empirical | docs/research/predictive-program-calendar/2026-09-05-synthesis.md |
| A12635 | ~~Official model zoo protocol~~ | Compare 13 individualized model families over all 25 Gate-3-approved asset-horizon pairs on one reviewed paired outer-fold protocol | children/intraday_equities/configs/run-p13-model-zoo.json; children/intraday_equities/configs/program-calendar.json; children/intraday_equities/docs/research/post-gate3-predictor-output/2026-09-05-synthesis.md; children/intraday_equities/docs/research/predictive-program-calendar/2026-09-05-synthesis.md; docs/architecture/decision-log.md#ADR-0097; docs/architecture/decision-log.md#ADR-0098; docs/architecture/decision-log.md#ADR-0099 | N | Superseded by the proposed pooled LightGBM/Torch-MLP zoo | research | post-gate3-predictor-output/2026-09-05-synthesis | empirical | docs/research/post-gate3-predictor-output/2026-09-05-synthesis.md |

## Evidence

Rationale files (not generated):

- [decision-framework-hpo.md](decision-framework-hpo.md)
- [decision-hl-scan.md](decision-hl-scan.md)
- [decision-horizon-criteria.md](decision-horizon-criteria.md)
- [decision-horizon-models.md](decision-horizon-models.md)
- [framework.md](framework.md)
- [hstar-go.md](hstar-go.md)
