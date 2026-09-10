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
| A18857 | research | ADR-0115: hpo_evidence in NoInformationScan; run-final-hpo.json P16 selection | 2026-09-10T00:30:38+00:00 |  | intraday_equities/nodes.py (hpo_evidence param, _hpo_evidence_selection, validate_outputs override); intraday_equities/model_zoo.py (_MODEL_FIELDS += hpo_evidence); configs/run-final-hpo.json (select.sources -> one pinned P16 compare.json A18850, finalist lgbm template -> ColumnSubsetEstimator + 33-col lean drop + squared_error_improvement + hpo_evidence=true, torch-mlp removed); tests/test_nodes.py + tests/test_configs.py (new tests) |  | Deliverables 1-3 complete and verified: hpo_evidence=False/absent is a provable no-op (regression test + full test_nodes.py/test_configs.py runs unchanged vs pre-edit baseline, 6 pre-existing environmental failures unchanged, 221 passed incl. 7 new). Deliverable 4 (run-final-refit.json) left undone: no existing stage wires pinned HPO winners into final_model.refit_heads/sklearn.write_bundle, and designing that wiring is new scope beyond ADR-0115. KNOWN GAP flagged in config notes and this record: run-final-hpo.json's sole finalist template keeps id='lgbm' (matches ADR-0115 text and preserves the already-passing final_model.hpo_space() Gate-2 test, which hardcodes that id) even though P16's real winning candidate name is 'lean'+horizon-suffixed, so FinalistCandidate's recipe lookup will only actually match once a future ADR resolves this id conflict; untestable in this gate (no run, no real P16 artifact). validate/plan only, no run, no market data, path.csv untouched. |
| A18858 | research | ADR-0115 follow-up: resolve the lgbm/lean id conflict A18857 flagged | 2026-09-10T00:36:44+00:00 |  | intraday_equities/final_model.py (hpo_space() now matches by family=='pooled-lightgbm', not a fixed id); configs/run-final-hpo.json (finalist template id -> 'lean', matching P16's real winning candidate; notes text fixed to avoid literally restating the candidate's horizon-suffixed name, which a pre-existing test forbids); tests/test_final_model.py + tests/test_configs.py updated to match |  | Resolves the KNOWN GAP A18857 flagged rather than left dangling: hpo_space() previously required a fixed id=='lgbm', which would never match FinalistCandidate's own '{id}-pooled-h{horizon}' lookup once BenchmarkSelect reports the real P16 winner 'lean-pooled-h10'. Fixed hpo_space() to match by family (a stable identity) instead of id (now correctly the finalist's candidate-name component), and renamed the config template's id to 'lean'. Caught and fixed one regression along the way: my own explanatory notes text in the config literally embedded the substring 'pooled-h', which test_the_finalist_names_no_model_and_takes_the_selectors_winner correctly refuses (this document must never restate a candidate name) -- reworded without changing meaning. Full verification: test_final_model.py (34) + test_configs.py (71 passed, same 5 pre-existing environmental failures as the pre-ADR-0115 baseline, confirmed via git stash) + test_nodes.py (186 passed with pyarrow installed) all green; ruff clean; git diff --check clean; validate/plan on the edited config succeed (new hash 2db8e95a...). No run, no market data, path.csv untouched. |
| A18859 | research | ADR-0115 correction: persist complete HPO ledgers | 2026-09-10T02:36:14+00:00 | Gate 2 deliverable 3 architecture skeptic Major at f67accd | JsonArtifact generic seam; atomic content-addressed hpo_ledger artifact and retained manifest | dskit/pipeline/node.py; dskit/pipeline/driver.py; intraday_equities/nodes.py | 24-trial >20KB ledger verified through run_document into artifact, node record, and carry manifest; default hpo_evidence=false shape unchanged; focused tests 124 generic passed and 259 child passed/11 skipped with 5 documented pre-existing config failures; no full suite, real pipeline, push, or path.csv edit |
| A18860 | research | Gate 2 deliverable 3 blocked after bounded review | 2026-09-10T02:51:19+00:00 | architecture cycle 1 Major; method cycle 3 Major; method cycle 4 Major at a926a2b | docs/RE-ENTRY.md handoff |  | Architecture cycle 1 Major fixed by d9eb132. Method cycle 3 Major fixed by 03a03ac. Method cycle 4 at a926a2b found hpo_evidence=true silently falls back without a ledger on insufficient inner data. The agreed two-correction-cycle bound blocks deliverable 3; no third correction attempted. Code is not eligible to close or merge; keep branch claude/phase1-recovery-seven-gates-ao4zdj distinct from main. |
| A18861 | research | Gate 5a: replay conformance/hook discovery | 2026-09-09T23:05:48+00:00 |  | tests/production/test_loop.py,tests/production/test_executor.py,tests/production/test_feed.py,tests/production/test_clock.py,docs/memos/2026-09-09-final-model-replay-gate5a-skeptic-conformance.md,docs/memos/2026-09-09-final-model-replay-gate5a-skeptic-conformance-cycle2.md,docs/memos/2026-09-09-final-model-replay-gate5a-skeptic-conformance-cycle3.md |  | PASS-EXISTING on items 1-5 and crash/restart positions/cash/NAV. Stopped after two correction cycles: pending-intent Recovery unknown order_event advances economic_seq (pinned) and moves pending→working (unpinned). No production hook. Reviewer 2 not run. path.csv untouched; replay.py not written. |
| A18862 | research | Gate 5a closed: replay conformance/hook discovery | 2026-09-10T00:10:37+00:00 |  | tests/production/test_loop.py,tests/production/test_executor.py,tests/production/test_feed.py,tests/production/test_clock.py,docs/memos/2026-09-09-final-model-replay-gate5a-skeptic-conformance-cycle4.md,docs/memos/2026-09-09-final-model-replay-gate5a-skeptic-architecture.md |  | PASS-EXISTING on all six Gate 5a items. No production hook. Cycle-4 conformance PASS (0C 0M 1N); architecture/governance PASS (0C 0M 1N). Pending-intent Recovery unknown order_event: economic_seq +N, n_working +N, n_pending -N pinned. path.csv untouched; replay.py not written. No §11 item inferred. |
| A18863 | execute | Gate 2 deliverable 3 method correction cycle 5 | 2026-09-10T03:29:57+00:00 | skeptic cycle 6 report; NoInformationScan estimator resolution contract | TDD regression and fail-closed resolved-estimator guard |  | GPT-5.6 Sol: universe scan estimators remain valid; evidence mode rejects empty, non-string, and non-importable effective estimators before fitting |
| A18864 | execute | Gate 2 deliverable 3 review-closed | 2026-09-10T03:44:23+00:00 | method/API PASS 7c0ba3d; architecture/governance PASS d61b86a | docs/memos/2026-09-10-final-model-replay-gate2-deliverable3-closeout.md; docs/RE-ENTRY.md |  | GPT-5.6 Sol closeout: 0C/0M/1 accepted minor method; 0C/0M/0m architecture. Atomic content-addressed HPO evidence, strict resolver, and fail-closed estimator/data checks verified. No full suite, real HPO, market-data run, run-final-refit.json, push, or path.csv edit. Next: Gate 2 deliverable 4 run-final-refit.json. |
| A18865 | execute | Gate 2 deliverable 4 pending final-refit contract | 2026-09-10T04:04:30+00:00 | ADR-0114; ADR-0115; final-HPO config identity 2db8e95a | configs/run-final-refit.json; intraday_equities/final_model.py; intraday_equities/nodes.py; focused tests |  | GPT-5.6 Sol: PENDING template refuses plan until ten real content-addressed HPO evidence manifests and frozen schema exist. Synthetic contract only; no market data, HPO, refit, pre-March read, or push. Focused child suites: 265 passed, 11 skipped, 5 documented baseline failures; Ruff/diff clean. |
| A18866 | execute | Gate 2 deliverable 4 correction cycle 2 fail-closed contract | 2026-09-10T04:31:19+00:00 | method skeptic 8038910; ADR-0114/0116; generic driver record/carry contracts | configs/run-final-refit.json; intraday_equities/final_model.py; tests/test_final_model.py; tests/test_configs.py; AGENTS.md |  | GPT-5.6 Sol: TDD correction. Mutable HPO sidecars and asserted source/cache/window hashes are not attestations; FinalRefit now remains non-executable even with filled pins until driver-owned run and content-derived ten-wire input identity contracts exist. No market data, HPO, refit, path.csv edit, or push. Focused child/direct generic tests and validate/plan only. |

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
