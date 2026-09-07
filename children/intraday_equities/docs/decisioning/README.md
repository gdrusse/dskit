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
| A18791 | execute | staged memory | 2026-09-07T05:06:03+00:00 | /home/russell/dskit/children/intraday_equities/configs/run-p16-tft-fusion-zoo.json | /home/russell/dskit/children/intraday_equities/pipeline_runs/p16-tft-fusion-zoo-staged-2026-02-28-18568e44/stages/memory.json |  | stage_token=18568e4401f0168c3ef4340e131ff95e3b1f61e9ab0c0fc581a211301613d349:memory; state=ran; sha256=f6ef0461754082a36ea4a49668901c9c0bb19ef9c33db3dd9b115faca7faddca; reason= |
| A18792 | execute | staged materialize | 2026-09-07T05:06:04+00:00 | /home/russell/dskit/children/intraday_equities/configs/run-p16-tft-fusion-zoo.json | /home/russell/dskit/children/intraday_equities/pipeline_runs/p16-tft-fusion-zoo-staged-2026-02-28-18568e44/stages/materialize.json |  | stage_token=18568e4401f0168c3ef4340e131ff95e3b1f61e9ab0c0fc581a211301613d349:materialize; state=ran; sha256=8893265b3841abbfc0734f70a9dc2ebed143881d0ce615614c81294925c2ac43; reason= |
| A18793 | execute | staged plan | 2026-09-07T05:06:05+00:00 | /home/russell/dskit/children/intraday_equities/configs/run-p16-tft-fusion-zoo.json | /home/russell/dskit/children/intraday_equities/pipeline_runs/p16-tft-fusion-zoo-staged-2026-02-28-18568e44/stages/plan.json |  | stage_token=18568e4401f0168c3ef4340e131ff95e3b1f61e9ab0c0fc581a211301613d349:plan; state=ran; sha256=6b7ab0c538ab526df0d62a562358f87a0e2ee5a85be812a4b1f14b6579a45533; reason= |
| A18794 | execute | staged approval | 2026-09-07T05:06:05+00:00 | /home/russell/dskit/children/intraday_equities/configs/run-p16-tft-fusion-zoo.json | /home/russell/dskit/children/intraday_equities/pipeline_runs/p16-tft-fusion-zoo-staged-2026-02-28-18568e44/stages/approval.json |  | stage_token=18568e4401f0168c3ef4340e131ff95e3b1f61e9ab0c0fc581a211301613d349:approval; state=ran; sha256=f1d9133f4bea28e8b69b58749c71430bf4bc1a7c4777b10d06f549c54862f5d3; reason= |
| A18795 | execute | ridge-p16-pooled-h10 walk-forward | 2026-09-07T05:54:12+00:00 | ridge-p16-pooled-h10 | /home/russell/dskit/children/intraday_equities/pipeline_runs/ridge-p16-pooled-h10-walkforward-2026-02-28-6f928fc1 | /home/russell/dskit/children/intraday_equities/pipeline_runs/ridge-p16-pooled-h10-walkforward-2026-02-28-6f928fc1 | state=ran folds=20 hash=6f928fc1 asof=2026-02-28 |
| A18796 | execute | tft-pooled-h10 walk-forward | 2026-09-07T07:57:28+00:00 | tft-pooled-h10 | /home/russell/dskit/children/intraday_equities/pipeline_runs/tft-pooled-h10-walkforward-2026-02-28-f52a027c | /home/russell/dskit/children/intraday_equities/pipeline_runs/tft-pooled-h10-walkforward-2026-02-28-f52a027c | state=ran folds=20 hash=f52a027c asof=2026-02-28 |
| A18797 | execute | staged run | 2026-09-07T07:57:28+00:00 | /home/russell/dskit/children/intraday_equities/configs/run-p16-tft-fusion-zoo.json | /home/russell/dskit/children/intraday_equities/pipeline_runs/p16-tft-fusion-zoo-staged-2026-02-28-18568e44/stages/run.json |  | stage_token=18568e4401f0168c3ef4340e131ff95e3b1f61e9ab0c0fc581a211301613d349:run; state=ran; sha256=a8fcd21d433a1bf3991c3a6870fbf20ff7ae1efe2eb0f1aa5682856705832299; reason= |
| A18798 | execute | staged compare | 2026-09-07T07:57:29+00:00 | /home/russell/dskit/children/intraday_equities/configs/run-p16-tft-fusion-zoo.json | /home/russell/dskit/children/intraday_equities/pipeline_runs/p16-tft-fusion-zoo-staged-2026-02-28-18568e44/stages/compare.json |  | stage_token=18568e4401f0168c3ef4340e131ff95e3b1f61e9ab0c0fc581a211301613d349:compare; state=ran; sha256=eb96d00cd14a4ead361ef54dcbd050f2aaf64e002c0175800196d73f4da1f859; reason= |
| A18799 | execute | lgbm-p17-pooled-h10 walk-forward | 2026-09-07T08:23:53+00:00 | lgbm-p17-pooled-h10 | /home/russell/dskit/children/intraday_equities/pipeline_runs/lgbm-p17-pooled-h10-walkforward-2026-02-28-c9b824ba | /home/russell/dskit/children/intraday_equities/pipeline_runs/lgbm-p17-pooled-h10-walkforward-2026-02-28-c9b824ba | state=ran folds=20 hash=c9b824ba asof=2026-02-28 |
| A18800 | execute | P17 RandomForest terminated early; P16/P17 result memo | 2026-09-07T17:18:17+00:00 | configs/run-p17-randomforest-zoo.json | docs/memos/p15-temporal-fusion-model-zoo-results.md |  | Owner terminated the pooled RandomForest after two of twenty folds (CPU-bound, ~28 min/lead, ~90 h projected). Two-fold lead-by-lead r2oos was indistinguishable from LightGBM: fold1 favors LGBM ~0.001 mean, fold2 favors RF ~0.0003 mean; same horizon-decay shape and lit symbols. P16 completed: TFT mean 0.000608 vs Ridge re-run 0.001346, p=0.561241, no detected difference. Decision: record RF as indistinguishable-through-2-folds; no promotion or refit. |

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
