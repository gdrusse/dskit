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
| A18701 | execute | staged materialize | 2026-09-06T04:41:29+00:00 | /home/russell/dskit/children/intraday_equities/configs/run-p13-pooled-model-zoo.json | /home/russell/dskit/children/intraday_equities/pipeline_runs/p13-pooled-model-zoo-staged-2026-02-28-b017ea1b/stages/materialize.json |  | stage_token=b017ea1b2235d24e6b7f20d141350db0c3c32a2127e032c22939335f85bab7f6:materialize; state=ran; sha256=db76df7fc6da22490d2fc91a3ffa2bf7d491528149b5c8b9c479eb05697ad63a; reason= |
| A18702 | execute | staged plan | 2026-09-06T04:41:30+00:00 | /home/russell/dskit/children/intraday_equities/configs/run-p13-pooled-model-zoo.json | /home/russell/dskit/children/intraday_equities/pipeline_runs/p13-pooled-model-zoo-staged-2026-02-28-b017ea1b/stages/plan.json |  | stage_token=b017ea1b2235d24e6b7f20d141350db0c3c32a2127e032c22939335f85bab7f6:plan; state=ran; sha256=0b464d9b53f0d423521ae141f96045a90a66e41feb1690b7f7fa3ca3b8cee215; reason= |
| A18703 | execute | staged approval | 2026-09-06T04:41:31+00:00 | /home/russell/dskit/children/intraday_equities/configs/run-p13-pooled-model-zoo.json | /home/russell/dskit/children/intraday_equities/pipeline_runs/p13-pooled-model-zoo-staged-2026-02-28-b017ea1b/stages/approval.json |  | stage_token=b017ea1b2235d24e6b7f20d141350db0c3c32a2127e032c22939335f85bab7f6:approval; state=ran; sha256=e30e9e514464708ba8af7f574c138357c9eee767b180c5ab4ae9544eaf9f0a8d; reason= |
| A18704 | execute | lgbm-pooled-h10 walk-forward | 2026-09-06T07:32:47+00:00 | lgbm-pooled-h10 | /home/russell/dskit/children/intraday_equities/pipeline_runs/lgbm-pooled-h10-walkforward-2026-02-28-9a5e6806 | /home/russell/dskit/children/intraday_equities/pipeline_runs/lgbm-pooled-h10-walkforward-2026-02-28-9a5e6806 | state=ran folds=20 hash=9a5e6806 asof=2026-02-28 |
| A18705 | execute | torch-mlp-pooled-h10 walk-forward | 2026-09-06T09:22:06+00:00 | torch-mlp-pooled-h10 | /home/russell/dskit/children/intraday_equities/pipeline_runs/torch-mlp-pooled-h10-walkforward-2026-02-28-80a69c81 | /home/russell/dskit/children/intraday_equities/pipeline_runs/torch-mlp-pooled-h10-walkforward-2026-02-28-80a69c81 | state=ran folds=20 hash=80a69c81 asof=2026-02-28 |
| A18706 | execute | kronos-lgbm-pooled-h10 walk-forward | 2026-09-06T11:14:09+00:00 | kronos-lgbm-pooled-h10 | /home/russell/dskit/children/intraday_equities/pipeline_runs/kronos-lgbm-pooled-h10-walkforward-2026-02-28-bd56efb9 | /home/russell/dskit/children/intraday_equities/pipeline_runs/kronos-lgbm-pooled-h10-walkforward-2026-02-28-bd56efb9 | state=ran folds=20 hash=bd56efb9 asof=2026-02-28 |
| A18707 | execute | kronos-torch-mlp-pooled-h10 walk-forward | 2026-09-06T12:06:47+00:00 | kronos-torch-mlp-pooled-h10 | /home/russell/dskit/children/intraday_equities/pipeline_runs/kronos-torch-mlp-pooled-h10-walkforward-2026-02-28-8311fc56 | /home/russell/dskit/children/intraday_equities/pipeline_runs/kronos-torch-mlp-pooled-h10-walkforward-2026-02-28-8311fc56 | state=ran folds=20 hash=8311fc56 asof=2026-02-28 |
| A18708 | execute | staged run | 2026-09-06T12:06:48+00:00 | /home/russell/dskit/children/intraday_equities/configs/run-p13-pooled-model-zoo.json | /home/russell/dskit/children/intraday_equities/pipeline_runs/p13-pooled-model-zoo-staged-2026-02-28-b017ea1b/stages/run.json |  | stage_token=b017ea1b2235d24e6b7f20d141350db0c3c32a2127e032c22939335f85bab7f6:run; state=ran; sha256=58b38124e94f0d539868700c7b0927e386ffefb397166dfd0dc08908731e6c00; reason= |
| A18709 | execute | staged compare | 2026-09-06T12:06:49+00:00 | /home/russell/dskit/children/intraday_equities/configs/run-p13-pooled-model-zoo.json | /home/russell/dskit/children/intraday_equities/pipeline_runs/p13-pooled-model-zoo-staged-2026-02-28-b017ea1b/stages/compare.json |  | stage_token=b017ea1b2235d24e6b7f20d141350db0c3c32a2127e032c22939335f85bab7f6:compare; state=ran; sha256=924e630dbb41bae0d4baae3c6538512a9bca56aaffd6d4d37604d1d3db60dfb1; reason= |
| A18710 | execute | P13 pooled Kronos model-zoo result | 2026-09-06T12:20:15+00:00 | configs/run-p13-pooled-model-zoo.json | docs/memos/p13-pooled-kronos-model-zoo-results.md | pipeline_runs/p13-pooled-model-zoo-staged-2026-02-28-b017ea1b/stages/compare.json | four candidates ran 20 folds; native pooled LightGBM selected simplest; tabular MLP not detectably different; both frozen Kronos challengers detectably worse; exploratory four-trial HPO; no auto-promotion |

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
