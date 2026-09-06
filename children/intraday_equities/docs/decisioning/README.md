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
| A18751 | execute | staged approval | 2026-09-06T16:19:12+00:00 | /home/russell/dskit/children/intraday_equities/configs/run-p14-recurrent-fusion-zoo.json | /home/russell/dskit/children/intraday_equities/pipeline_runs/p14-recurrent-fusion-zoo-staged-2026-02-28-70b5a399/stages/approval.json |  | stage_token=70b5a3998e9148852fef4e84637a2e41b631c7f776db9d2a40b4d660d9ae0036:approval; state=ran; sha256=b1cab3cf2923992b739f18c4ba928162319c81befe029373d881aa552c64423f; reason= |
| A18752 | execute | lstm-pooled-h10 walk-forward | 2026-09-06T17:17:05+00:00 | lstm-pooled-h10 | /home/russell/dskit/children/intraday_equities/pipeline_runs/lstm-pooled-h10-walkforward-2026-02-28-def2f1fe | /home/russell/dskit/children/intraday_equities/pipeline_runs/lstm-pooled-h10-walkforward-2026-02-28-def2f1fe | state=ran folds=20 hash=def2f1fe asof=2026-02-28 |
| A18753 | execute | gru-pooled-h10 walk-forward | 2026-09-06T18:16:39+00:00 | gru-pooled-h10 | /home/russell/dskit/children/intraday_equities/pipeline_runs/gru-pooled-h10-walkforward-2026-02-28-e7eb35a7 | /home/russell/dskit/children/intraday_equities/pipeline_runs/gru-pooled-h10-walkforward-2026-02-28-e7eb35a7 | state=ran folds=20 hash=e7eb35a7 asof=2026-02-28 |
| A18754 | execute | staged run | 2026-09-06T18:16:39+00:00 | /home/russell/dskit/children/intraday_equities/configs/run-p14-recurrent-fusion-zoo.json | /home/russell/dskit/children/intraday_equities/pipeline_runs/p14-recurrent-fusion-zoo-staged-2026-02-28-70b5a399/stages/run.json |  | stage_token=70b5a3998e9148852fef4e84637a2e41b631c7f776db9d2a40b4d660d9ae0036:run; state=ran; sha256=2d115964fc7cabd895e9821bc4be0aa14e391029e244885e9dd486bfc2b1e703; reason= |
| A18755 | execute | staged compare | 2026-09-06T18:16:40+00:00 | /home/russell/dskit/children/intraday_equities/configs/run-p14-recurrent-fusion-zoo.json | /home/russell/dskit/children/intraday_equities/pipeline_runs/p14-recurrent-fusion-zoo-staged-2026-02-28-70b5a399/stages/compare.json |  | stage_token=70b5a3998e9148852fef4e84637a2e41b631c7f776db9d2a40b4d660d9ae0036:compare; state=ran; sha256=12b893e4706919018ecdf15837b4c4abc4acaaeea94c6f6afa9c36d70dacfe75; reason= |
| A18756 | execute | P14 recurrent-fusion model-zoo result | 2026-09-06T18:35:19+00:00 | configs/run-p14-recurrent-fusion-zoo.json; pipeline_runs/p14-recurrent-fusion-zoo-staged-2026-02-28-70b5a399/stages/compare.json | docs/memos/p14-recurrent-fusion-model-zoo-results.md |  | 20/20 folds each; LSTM mean=0.001174414; GRU mean=-0.000416719; paired p=0.109186; no promotion or final refit; benchmark=70b5a399; compare_sha256=12b893e4706919018ecdf15837b4c4abc4acaaeea94c6f6afa9c36d70dacfe75 |
| A18757 | research | horizon-cap-gates/2026-09-06-synthesis | 2026-09-06T22:31:30+00:00 | Horizon-cap gate design: per-(stock,horizon) checks | docs/research/horizon-cap-gates/2026-09-06-synthesis.md | docs/research/horizon-cap-gates/2026-09-06-synthesis.md |  |
| A18758 | execute | transformer-pooled-h10 walk-forward | 2026-09-06T22:48:34+00:00 | transformer-pooled-h10 | /home/russell/dskit/children/intraday_equities/pipeline_runs/transformer-pooled-h10-walkforward-2026-02-28-8c0feaa6 | /home/russell/dskit/children/intraday_equities/pipeline_runs/transformer-pooled-h10-walkforward-2026-02-28-8c0feaa6 | state=ran folds=20 hash=8c0feaa6 asof=2026-02-28 |
| A18759 | execute | staged run | 2026-09-06T22:48:34+00:00 | /home/russell/dskit/children/intraday_equities/configs/run-p15-temporal-fusion-zoo.json | /home/russell/dskit/children/intraday_equities/pipeline_runs/p15-temporal-fusion-zoo-staged-2026-02-28-5e88726b/stages/run.json |  | stage_token=5e88726bb863d8368c68b8a9532e878a2e8f72d1aef32952a35201afcff4d8ff:run; state=ran; sha256=9e8760ae742e9c316c86003c4ba9ab3582cea451af641b1f267ad5dcaa092ec2; reason= |
| A18760 | execute | staged compare | 2026-09-06T22:48:35+00:00 | /home/russell/dskit/children/intraday_equities/configs/run-p15-temporal-fusion-zoo.json | /home/russell/dskit/children/intraday_equities/pipeline_runs/p15-temporal-fusion-zoo-staged-2026-02-28-5e88726b/stages/compare.json |  | stage_token=5e88726bb863d8368c68b8a9532e878a2e8f72d1aef32952a35201afcff4d8ff:compare; state=ran; sha256=47bfa13576dc3bf14d8e2a09617e5b2630f8252435c166d659b5495f37104738; reason= |

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
