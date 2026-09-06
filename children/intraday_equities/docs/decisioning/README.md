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
| A18671 | execute | p13-pooled-torch-mlp-memory-smoke-v1 walk-forward | 2026-09-06T02:46:14+00:00 | p13-pooled-torch-mlp-memory-smoke-v1 | /home/russell/dskit/children/intraday_equities/pipeline_runs/p13-pooled-torch-mlp-memory-smoke-v1-walkforward-2026-02-28-eb533565 | /home/russell/dskit/children/intraday_equities/pipeline_runs/p13-pooled-torch-mlp-memory-smoke-v1-walkforward-2026-02-28-eb533565 | state=error folds=1 hash=eb533565 asof=2026-02-28 |
| A18672 | execute | p13-pooled-torch-mlp-memory-smoke-v2 walk-forward | 2026-09-06T02:47:48+00:00 | p13-pooled-torch-mlp-memory-smoke-v2 | /home/russell/dskit/children/intraday_equities/pipeline_runs/p13-pooled-torch-mlp-memory-smoke-v2-walkforward-2026-02-28-e21fb7a7 | /home/russell/dskit/children/intraday_equities/pipeline_runs/p13-pooled-torch-mlp-memory-smoke-v2-walkforward-2026-02-28-e21fb7a7 | state=error folds=1 hash=e21fb7a7 asof=2026-02-28 |
| A18673 | execute | p13-pooled-torch-mlp-memory-smoke-v4 walk-forward | 2026-09-06T02:50:03+00:00 | p13-pooled-torch-mlp-memory-smoke-v4 | /home/russell/dskit/children/intraday_equities/pipeline_runs/p13-pooled-torch-mlp-memory-smoke-v4-walkforward-2026-02-28-2be3b1fc | /home/russell/dskit/children/intraday_equities/pipeline_runs/p13-pooled-torch-mlp-memory-smoke-v4-walkforward-2026-02-28-2be3b1fc | state=ran folds=1 hash=2be3b1fc asof=2026-02-28 |
| A18674 | execute | staged calendar | 2026-09-06T02:51:00+00:00 | /home/russell/dskit/children/intraday_equities/configs/run-p13-pooled-model-zoo.json | /home/russell/dskit/children/intraday_equities/pipeline_runs/p13-pooled-model-zoo-staged-2026-02-28-5676d1e6/stages/calendar.json |  | stage_token=5676d1e61755e55dde5f776eeca7c067a649d80c78e753e7d54953981d8944f1:calendar; state=ran; sha256=c39bd49d821d348fd192108aba3530b9f4f510d5d62f3dc2fef056f55e23fa2e; reason= |
| A18675 | execute | p13-pooled-model-zoo-preflight-5676d1e6-iwm-h01 walk-forward | 2026-09-06T02:51:03+00:00 | p13-pooled-model-zoo-preflight-5676d1e6-iwm-h01 | /home/russell/dskit/children/intraday_equities/pipeline_runs/p13-pooled-model-zoo-preflight-5676d1e6-iwm-h01-walkforward-2026-02-28-b5e1868f | /home/russell/dskit/children/intraday_equities/pipeline_runs/p13-pooled-model-zoo-preflight-5676d1e6-iwm-h01-walkforward-2026-02-28-b5e1868f | state=ran folds=1 hash=b5e1868f asof=2026-02-28 |
| A18676 | execute | staged memory | 2026-09-06T02:51:04+00:00 | /home/russell/dskit/children/intraday_equities/configs/run-p13-pooled-model-zoo.json | /home/russell/dskit/children/intraday_equities/pipeline_runs/p13-pooled-model-zoo-staged-2026-02-28-5676d1e6/stages/memory.json |  | stage_token=5676d1e61755e55dde5f776eeca7c067a649d80c78e753e7d54953981d8944f1:memory; state=ran; sha256=434803c57966a4795ffa36ccbab0f2bd34de528bb92737082312943169969c92; reason= |
| A18677 | execute | staged materialize | 2026-09-06T02:51:05+00:00 | /home/russell/dskit/children/intraday_equities/configs/run-p13-pooled-model-zoo.json | /home/russell/dskit/children/intraday_equities/pipeline_runs/p13-pooled-model-zoo-staged-2026-02-28-5676d1e6/stages/materialize.json |  | stage_token=5676d1e61755e55dde5f776eeca7c067a649d80c78e753e7d54953981d8944f1:materialize; state=ran; sha256=508016c784787d98882d519b294268f867e3d9055869138921cf0953a3a5f7d7; reason= |
| A18678 | execute | staged plan | 2026-09-06T02:51:05+00:00 | /home/russell/dskit/children/intraday_equities/configs/run-p13-pooled-model-zoo.json | /home/russell/dskit/children/intraday_equities/pipeline_runs/p13-pooled-model-zoo-staged-2026-02-28-5676d1e6/stages/plan.json |  | stage_token=5676d1e61755e55dde5f776eeca7c067a649d80c78e753e7d54953981d8944f1:plan; state=ran; sha256=661f84dc3f81f4d96b6750692c365b5df2c48dcf21832e9c8bcba01dca86a2e4; reason= |
| A18679 | execute | staged approval | 2026-09-06T02:51:06+00:00 | /home/russell/dskit/children/intraday_equities/configs/run-p13-pooled-model-zoo.json | /home/russell/dskit/children/intraday_equities/pipeline_runs/p13-pooled-model-zoo-staged-2026-02-28-5676d1e6/stages/approval.json |  | stage_token=5676d1e61755e55dde5f776eeca7c067a649d80c78e753e7d54953981d8944f1:approval; state=ran; sha256=80fe6780316a057fc01c07022d2ccfd0de834df01dec048ee74bb18701b77b94; reason= |
| A18680 | execute | staged run | 2026-09-06T02:51:23+00:00 | /home/russell/dskit/children/intraday_equities/configs/run-p13-pooled-model-zoo.json |  |  | stage_token=5676d1e61755e55dde5f776eeca7c067a649d80c78e753e7d54953981d8944f1:run; state=error; reason=KeyboardInterrupt: |

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
