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
| A1320 | research | distribution-modeling/2026-11-03-ridge-lasso-stability-timing | 2026-11-03T19:34:04+00:00 | Audit feature timing and compare ridge with matched Lasso | docs/research/distribution-modeling/2026-11-03-ridge-lasso-stability-timing.md | docs/research/distribution-modeling/2026-11-03-ridge-lasso-stability-timing.md |  |
| A1321 | research | distribution-modeling/2026-11-03-astra-bayesian-weak-signals | 2026-11-03T21:14:57+00:00 | Assess Bayesian methods for weak prediction signals | docs/research/distribution-modeling/2026-11-03-astra-bayesian-weak-signals.md | docs/research/distribution-modeling/2026-11-03-astra-bayesian-weak-signals.md |  |
| A1322 | research | distribution-modeling/2026-11-03-bayesian-blend-standalone-comparison | 2026-11-03T21:38:16+00:00 | Bayesian blend trails best standalone Lasso | docs/research/distribution-modeling/2026-11-03-bayesian-blend-standalone-comparison.md | docs/research/distribution-modeling/2026-11-03-bayesian-blend-standalone-comparison.md |  |
| A1323 | research | distribution-modeling/2026-11-03-m53-lasso10-substitution | 2026-11-03T22:34:37+00:00 | M53 Lasso10 substitution does not improve the original blend | docs/research/distribution-modeling/2026-11-03-m53-lasso10-substitution.md | docs/research/distribution-modeling/2026-11-03-m53-lasso10-substitution.md |  |
| A1324 | research | distribution-modeling/2026-11-03-astra-m53-complementarity | 2026-11-03T22:45:35+00:00 | Astra M53 component complementarity and weight adaptation | docs/research/distribution-modeling/2026-11-03-astra-m53-complementarity.md | docs/research/distribution-modeling/2026-11-03-astra-m53-complementarity.md |  |
| A1325 | research | distribution-modeling/2026-11-03-m53-stable-combination | 2026-11-03T23:30:05+00:00 | Stable M53 complementary forecast comparison | docs/research/distribution-modeling/2026-11-03-m53-stable-combination.md | docs/research/distribution-modeling/2026-11-03-m53-stable-combination.md |  |
| A1326 | research | distribution-modeling/2026-11-04-dte-distribution-audit | 2026-11-04T02:37:04+00:00 | CDF shape by DTE in 2016-2019 | docs/research/distribution-modeling/2026-11-04-dte-distribution-audit.md | docs/research/distribution-modeling/2026-11-04-dte-distribution-audit.md |  |
| A1327 | research | distribution-modeling/2026-11-04-pre2020-cdf-calibration | 2026-11-04T02:52:59+00:00 | Pre-2020 calibration: pooled agreement hides a time reversal | docs/research/distribution-modeling/2026-11-04-pre2020-cdf-calibration.md | docs/research/distribution-modeling/2026-11-04-pre2020-cdf-calibration.md |  |
| A1328 | research | distribution-modeling/2026-11-04-frozen-cdf-calibration-2020-2023 | 2026-11-04T03:22:28+00:00 | Frozen pre-2020 correction adds no benefit in 2020–2023 | docs/research/distribution-modeling/2026-11-04-frozen-cdf-calibration-2020-2023.md | docs/research/distribution-modeling/2026-11-04-frozen-cdf-calibration-2020-2023.md |  |
| A1329 | execute | wrap-prediction-model-audit | 2026-11-04T03:47:41+00:00 | A1312-A1328 research artifacts and independent reviews | Updated index_options RE-ENTRY and archive; M53 unchanged |  | Standalone prediction, M53 composition, PCA/segmentation, DTE shape and frozen calibration studies closed. No core, config, Path, MIO, production or sealed-window change. |

## Path to Production

| ID | Label | Purpose | Relevant Files | LOCKED | Current Work (owner only) | Category | Step | Decision Criteria | DB Location |
|---|---|---|---|---|---|---|---|---|---|
| A0001 | Distribution modeling approach | Model the physical terminal distribution in vol-standardized log-moneyness; distribution-regression challenger; drop fixed bins | children/index_options/docs/research/distribution-modeling/2026-09-23-approach.md | Y |  | research | distribution-modeling/2026-09-23-approach | judgemental | docs/research/distribution-modeling/2026-09-23-approach.md |
| A0002 | Distribution evaluation metrics | Primary score: threshold-weighted CRPS with weight 1 on \|z\| in [0.5, 2.5] tapering to 0.25 at z=0 and \|z\|>=3, with full-distribution CRPS beside it; Brier and reliability at the listed strikes; PIT/Berkowitz in the tails; only proper weighted scores; never choose the evaluation subset by outcomes. Strike regions are a pooled decomposition, never a cell axis. Predictability only: no P&L or strategy economics | children/index_options/docs/research/distribution-modeling/2026-09-23-evaluation.md | Y |  | research | distribution-modeling/2026-09-23-evaluation | judgemental | docs/research/distribution-modeling/2026-09-23-evaluation.md |
| A0003 | Procedural windows | Development 2017-01-03..2019-12-31 (fit plus last cal_n origins as early-stop band); validation 2020-01-02..2023-12-31 reported also by sub-period; holdout 2024-01-02..2025-11-28 sealed and opened once for promoted recipes; 2026 sealed forever; a row is on the earlier side of any boundary only if its label settles before it (label_reaches) | children/index_options/docs/research/distribution-modeling/2026-10-18-strike-screen-windows-lead-time-cells.md | Y |  | research | distribution-modeling/2026-09-23-model-ladder | judgemental | docs/research/distribution-modeling/2026-09-23-model-ladder.md |
| A0004 | Fold rungs | Screen: warm-up fold (train 2017-2018, validate 2019; timing and learning curves only, never ranked) plus one scored fit trained through 2019 and scored 2020-2023. Promotion: four expanding yearly refits (through 2019, 2020, 2021, 2022; validate each next year) with seeds 11/29/47. Holdout: train through 2023, validate 2024-2025, one opening | children/index_options/docs/research/distribution-modeling/2026-10-18-strike-screen-windows-lead-time-cells.md | Y |  | research | distribution-modeling/2026-09-23-ml-dl-transformers | judgemental | docs/research/distribution-modeling/2026-09-23-ml-dl-transformers.md |
| A0005 | Promotion rule and uncertainty | Eligible only if paired CRPS skill vs horizon_empirical and vs the market-implied reference is above zero on the full validation window and in at least 3 of 4 years; two-way cluster-robust intervals (expiry and symbol); never the best cell by score | children/index_options/docs/research/distribution-modeling/2026-10-18-strike-screen-windows-lead-time-cells.md | Y |  | acquire | register-source cboe-index | judgemental | /home/russell/data/index_options/ob |
| A0006 | Cell definition | Cell = (ticker, expiry rank). Weekly names: ranks 1-8, rank k = the k-th listed Friday expiry (the 7-day windows 1-7, 8-14, ...). Monthly-only names: expiry position 1-3, per ticker but flagged underpowered, with a pooled monthly line beside them. Thursday holiday expiries stay | children/index_options/docs/research/distribution-modeling/2026-10-20-strike-screen-technical-design.md | Y |  | acquire | register-source cboe-chain | judgemental | /home/russell/data/index_options/ob |
| A0007 | Eligibility gate | Simple: a cell passes if the model beats the raw market-implied distribution AND the per-name horizon baseline on the A0002 score in at least 3 of 4 yearly folds and at least 3 of 5 regimes (S0 pre-crash, S1 covid crash, S2 recovery, S3 rate shock, S4 2023). No multiplicity correction yet: report the number passing next to the number expected by luck. The recalibrated market and the pooled baseline join when built | children/index_options/docs/research/distribution-modeling/2026-10-20-strike-screen-technical-design.md | Y |  | acquire | backfill cboe-index/index_daily | judgemental | /home/russell/data/index_options/ob |
| A0008 | Training sequence and run | Retrain periodically for all events: warm-up 2017-2018 validated on 2019 (never ranked), then yearly expanding retrains (through 2019 to 2020, through 2020 to 2021, through 2021 to 2022, through 2022 to 2023). Use the best output from the model zoo. Selected recipe, components, seeds and exact output run/hash remain to be pinned. Supersedes A0004's screen rung. Owner correction 2026-10-27 replaces the fixed TCN recipe. | children/index_options/configs/strike-screen-folds-walkforward.jsonl | Y |  | acquire | backfill cboe-index/index_daily | judgemental | /home/russell/data/index_options/ob |
