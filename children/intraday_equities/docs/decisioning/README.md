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
| A18847 | execute | staged gates | 2026-09-08T11:45:46+00:00 | /tmp/dskit-mio-doc-EWNhEQ/children/intraday_equities/configs/run-p16-final-model-gates.json | /tmp/dskit-mio-doc-EWNhEQ/children/intraday_equities/pipeline_runs/p16-final-model-gates-staged-2026-02-28-77a7ab08/stages/gates.json |  | stage_token=77a7ab08cd96d132cbf9212e1c7773420e7e2d90d470a46e90a9e4398aa5c670:gates; state=ran; sha256=1fb4dec20a7ac32867f8a9cb20d1c96225b2818029034ab5ce7f1b2c872d8771; reason= |
| A18848 | execute | P16 canonical evidence repair and final developmental gates | 2026-09-08T11:49:11+00:00 | /home/russell/dskit/children/intraday_equities/configs/run-p16-feature-mask-zoo.json@986356010e3661192b3758d53d03a3cd060392dafc2c6ac3af4563dbd140f13d; run.json@30310d89c4ea99fdc224ac497c535286c8a4e6cc9f3ee103338270cd99ca2d9a; compare.json@f29bbfa51b1c84bf5e686d9c63c39637d0b1e8f70116ab2a69f6d894a729061c; inventory.json@3c0741e3c2018718d73a2f532db2d6095ae48c478b89afd827b8f1e193e604c4; gates.json@1fb4dec20a7ac32867f8a9cb20d1c96225b2818029034ab5ce7f1b2c872d8771 | /home/russell/dskit/children/intraday_equities/docs/memos/p16-feature-mask-and-final-gate-results.md |  | Canonical real-path artifacts supersede A18842 hashes and all ephemeral /tmp references in A18834-A18847; numerical results unchanged: lean selected; 90/90 beat mean; 51/90 pass all gates; contiguous 44 horizons across 11/25 stocks; developmental_post_selection; deployment_eligible=false. |
| A18849 | execute | staged run | 2026-09-08T11:53:39+00:00 | /home/russell/dskit/children/intraday_equities/configs/run-p16-feature-mask-zoo.json | /home/russell/dskit/children/intraday_equities/pipeline_runs/p16-feature-mask-zoo-staged-2026-02-28-98635601/stages/run.json |  | stage_token=986356010e3661192b3758d53d03a3cd060392dafc2c6ac3af4563dbd140f13d:run; state=ran; sha256=30310d89c4ea99fdc224ac497c535286c8a4e6cc9f3ee103338270cd99ca2d9a; reason=canonical path repair supersedes A18837 |
| A18850 | execute | staged compare | 2026-09-08T11:53:39+00:00 | /home/russell/dskit/children/intraday_equities/configs/run-p16-feature-mask-zoo.json | /home/russell/dskit/children/intraday_equities/pipeline_runs/p16-feature-mask-zoo-staged-2026-02-28-98635601/stages/compare.json |  | stage_token=986356010e3661192b3758d53d03a3cd060392dafc2c6ac3af4563dbd140f13d:compare; state=ran; sha256=f29bbfa51b1c84bf5e686d9c63c39637d0b1e8f70116ab2a69f6d894a729061c; reason=canonical path repair supersedes A18839 |
| A18851 | execute | intraday-equities-mio-demo | 2026-09-08T19:09:27+00:00 | configs/run-mio-demo.json | /home/user/dskit/children/intraday_equities/pipeline_runs/intraday-equities-mio-demo-2026-01-01-f089d60c | /home/user/dskit/children/intraday_equities/pipeline_runs/intraday-equities-mio-demo-2026-01-01-f089d60c | state=ran hash=f089d60c asof=2026-01-01 |
| A18852 | execute | intraday-equities-mio-demo | 2026-09-08T19:09:55+00:00 | configs/run-mio-demo.json | /home/user/dskit/children/intraday_equities/pipeline_runs/intraday-equities-mio-demo-2026-01-01-fd1b8cad | /home/user/dskit/children/intraday_equities/pipeline_runs/intraday-equities-mio-demo-2026-01-01-fd1b8cad | state=ran hash=fd1b8cad asof=2026-01-01 |
| A18853 | execute | intraday-equities-mio-demo | 2026-09-08T19:10:10+00:00 | configs/run-mio-demo.json | /home/user/dskit/children/intraday_equities/pipeline_runs/intraday-equities-mio-demo-2026-01-02-fd1b8cad | /home/user/dskit/children/intraday_equities/pipeline_runs/intraday-equities-mio-demo-2026-01-02-fd1b8cad | state=ran hash=fd1b8cad asof=2026-01-02 |
| A18854 | research | Gate 1: Phase 0 ADR-0114 proposal and owner packet | 2026-09-09T19:30:32+00:00 |  | docs/architecture/decision-log.md (ADR-0114); docs/memos/2026-09-09-final-model-replay-phase0-closeout.md |  | ADR-0114 proposed (not accepted); restates all 10 plan section-11 items unresolved; no implementation code written |
| A18855 | research | ADR-0114 accepted; §11 item 1 ruled (SE method + simplicity ordering) | 2026-09-09T20:40:20+00:00 |  | docs/architecture/decision-log.md (ADR-0114 status + §11 item 1 ruling) |  | Owner accepted ADR-0114 as proposed; ruled §11 item 1: se via dskit.pipeline.stats.cluster_bootstrap_t clustered by trading day; simplicity key = (num_leaves, learning_rate, -min_child_samples, -reg_lambda, -reg_alpha) ascending. Unblocks Phase 2 (Gate 2). Nine §11 items remain open. |
| A18856 | research | Gate 5a: replay conformance/hook discovery | 2026-09-09T23:05:48+00:00 |  | tests/production/test_loop.py,tests/production/test_executor.py,tests/production/test_feed.py,tests/production/test_clock.py,docs/memos/2026-09-09-final-model-replay-gate5a-skeptic-conformance.md,docs/memos/2026-09-09-final-model-replay-gate5a-skeptic-conformance-cycle2.md,docs/memos/2026-09-09-final-model-replay-gate5a-skeptic-conformance-cycle3.md |  | PASS-EXISTING on items 1-5 and crash/restart positions/cash/NAV. Stopped after two correction cycles: pending-intent Recovery unknown order_event advances economic_seq (pinned) and moves pending→working (unpinned). No production hook. Reviewer 2 not run. path.csv untouched; replay.py not written. |

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
